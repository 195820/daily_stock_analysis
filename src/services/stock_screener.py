# -*- coding: utf-8 -*-
"""
===================================
选股流水线服务
===================================

职责：
1. 编排选股流程（数据获取 → 策略筛选 → 存库 → 推送）
2. 支持两种数据源：
   - 同花顺问财（推荐）：直接使用同花顺 DDE 计算，结果与 APP 一致
   - 东方财富：自己计算 DDE（大单净流入/流通市值）
3. 将筛选结果持久化到 ScreenerResultRecord
4. 触发通知推送

设计原则：
- 零侵入：复用现有通知/调度/数据层
- 可插拔：策略 YAML 可热加载，数据源可切换
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config import Config
from src.storage import DatabaseManager, ScreenerResultRecord

logger = logging.getLogger(__name__)


# ============================================================
# 数据模型
# ============================================================

@dataclass
class ScreenerStock:
    """选股结果中的单只股票"""
    code: str
    name: str
    score: float = 100.0           # 综合评分
    dde_big_order: float = 0.0     # DDE 大单净量
    source: str = "ths"            # 数据来源: ths / eastmoney

    # 短线技术指标
    technical_score: float = 0.0   # 技术评分 0-100
    volume_ratio: float = 1.0      # 量比
    turnover_rate: float = 0.0     # 换手率 %
    rsi6: float = 50.0             # RSI(6)
    ma_alignment: bool = False     # 均线多头排列
    breakout_5d: bool = False      # 突破5日高点
    signal_tags: List[str] = field(default_factory=list)  # 信号标签

    # 板块信息
    sector_name: str = ""          # 所属板块（最强板块，向后兼容）
    sector_score: float = 0.0      # 板块强度分（加权平均）
    all_sectors: List[Dict] = field(default_factory=list)  # 所有板块信息
    # all_sectors 格式: [
    #   {"name": "铜概念", "score": 90, "type": "concept", "is_mainline": True, "weight": 1.5},
    #   ...
    # ]

    # 舆情信息
    sentiment_score: float = 0.0   # 舆情情绪分 -1~1
    heat_score: float = 0.0        # 舆情热度 0-100

    # 风险 + AI 点评
    risk_score: float = 0.0        # 风险评分 0-100, 越高越危险
    risk_tags: List[str] = field(default_factory=list)
    ai_reasoning: str = ""         # AI 点评摘要
    recommendation: str = "watch"  # 推荐级别: strong_buy / buy / watch

    # 持仓信息
    portfolio_held: bool = False           # 是否已持仓
    portfolio_quantity: float = 0.0        # 持仓数量
    portfolio_avg_cost: float = 0.0        # 持仓均价
    portfolio_pnl_pct: Optional[float] = None  # 持仓盈亏比例 %

    # GOT 模式专用字段
    capital_score: float = 0.0             # 资金面评分 0-100
    capital_available: bool = False        # 资金面是否有真实数据（缺失时不参与加权）
    news_score: float = 0.0                # 消息面评分 0-100
    news_available: bool = False           # 消息面是否有真实数据
    tech_wave_score: float = 0.0           # 波段技术评分 0-100
    risk_score_safe: float = 100.0         # 风险评分 0-100（越高越安全，与 risk_score 相反）
    sector_type: str = ""                  # 板块类型: active_mainline / ambush
    sector_query_board: str = ""           # 问财实际查询的板块名（resolve_with_kind 产物，可能与 Node6 标签不同）
    ambush_rank_score: Optional[float] = None  # 埋伏道排序分（G-A4 产物，主线票为 None）
    backtest_snapshot: Optional[Dict[str, Any]] = None  # 回测快照

    # P5 催化剂关联度字段
    catalyst_relevance_score: float = 0.0  # 催化剂关联度 0-100
    catalyst_event_title: str = ""         # 关联的催化剂事件标题
    catalyst_event_date: str = ""          # 事件日期 YYYY-MM-DD
    catalyst_event_type: str = ""          # earnings / lockup / narrative / global / macro
    catalyst_confidence: float = 0.0       # 催化剂置信度 0-1

    # T1 Agent 终审字段
    agent_verdict: str = ""                # confirm / warn / reject / "" (未调用)
    agent_notes: str = ""                  # Agent 审查备注
    agent_decision_signal: str = ""        # reject 时的降级信号: "demoted" / ""

    # 审核修复 W10/2.1/S2：候选来源可观测性
    dde_degraded: bool = False             # 问财初筛 DDE 轮全空降级 / ambush 5日值缺失回退单日
    capital_floor_restored: bool = False   # 轮次 2 Tier A 主线保底补回
    pool_source: str = ""                  # 空=Node2 actionable 正常通道 / bypass_ambush=蓄水池升格旁路补选


@dataclass
class ScreenerRunResult:
    """单次选股运行结果"""
    run_date: str                              # 运行日期 YYYY-MM-DD
    run_time: str                              # 运行时间 HH:MM:SS
    strategy_name: str                         # 策略名称
    data_source: str = "ths"                   # 数据源: ths / eastmoney
    total_scanned: int = 0                     # 扫描股票总数
    matched_count: int = 0                     # 满足条件数量
    stocks: List[ScreenerStock] = field(default_factory=list)  # 选股结果
    error: Optional[str] = None                # 错误信息
    elapsed_seconds: float = 0.0               # 执行耗时
    query: str = ""                            # 问财查询语句
    # 4d/D-3 前置：候选池归零率基线观测（仅留痕、不进任何判定，排期批 5 §4.1）
    funnel_stats: Optional[dict] = None        # {actionable_sector_count, zero_pick_sectors, round_survival_counts}


# ============================================================
# 五维评分权重 + 缺失值归一化工具
# ============================================================

# 基础权重（按数据可靠性分配，参见 NBER Freyberger et al. 2022 缺失值处理原则）
SCORING_WEIGHTS = {
    "capital": 0.15,   # 数据不可靠（DDE 常失败），学术 alpha 弱（长江证券正交化后 IC -3.6%）
    "news": 0.15,      # 依赖外部搜索 API
    "sector": 0.25,    # mootdx TCP 稳定
    "tech": 0.25,      # K 线永远可用
    "risk": 0.20,      # 波动率永远可算
}


def weighted_score_with_redistribution(
    dimension_scores: Dict[str, float],
    dimension_available: Dict[str, bool],
    base_weights: Optional[Dict[str, float]] = None,
) -> Tuple[float, Dict[str, float]]:
    """加权评分 + 缺失维度权重归一化。

    数据缺失时跳过该维度（而非给 0 分），剩余维度权重按比例放大。
    参考 NBER Working Paper w30761 (Freyberger et al., 2022)。

    Args:
        dimension_scores: {维度名: 分数}（缺失维度的值不参与计算）
        dimension_available: {维度名: 是否可用}
        base_weights: 基础权重，默认使用 SCORING_WEIGHTS

    Returns:
        (综合分, 实际使用的归一化权重)
    """
    weights = base_weights or SCORING_WEIGHTS
    active = {k for k, v in dimension_available.items() if v and k in weights}

    if not active:
        return 50.0, {}

    total_w = sum(weights[k] for k in active)
    if total_w <= 0:
        return 50.0, {}

    adjusted = {k: weights[k] / total_w for k in active}
    score = sum(dimension_scores.get(k, 0) * adjusted[k] for k in active)
    return round(score, 1), adjusted


# ============================================================
# 选股流水线
# ============================================================

class StockScreenerPipeline:
    """
    选股流水线

    支持两种数据源：
    1. 同花顺问财（推荐）：直接使用同花顺 DDE，与 APP 一致
    2. 东方财富：自己计算 DDE（大单净流入/流通市值）
    """

    def __init__(self, config: Config):
        self.config = config
        self.db = DatabaseManager.get_instance()
        self._technical_signals: Dict = {}  # code → TechnicalSignals（供风险评估复用）

    def run(
        self,
        strategy_name: str = "dde_low_screener",
        data_source: str = "ths",
        top_n: Optional[int] = None,
    ) -> ScreenerRunResult:
        """
        执行选股流水线

        Args:
            strategy_name: 策略名称
            data_source: 数据源 ("ths" 或 "eastmoney")
            top_n: 返回 Top N 结果

        Returns:
            ScreenerRunResult
        """
        import time
        start_time = time.time()

        run_date = date.today().isoformat()
        run_time = datetime.now().strftime('%H:%M:%S')
        top_n = top_n or self.config.screener_top_n

        result = ScreenerRunResult(
            run_date=run_date,
            run_time=run_time,
            strategy_name=strategy_name,
            data_source=data_source,
        )

        try:
            if data_source == "ths":
                self._run_ths_screener(result, top_n)
            else:
                # 缺陷③A：东财 DDE 选股支路（_run_eastmoney_screener）随 push2
                # 整域封锁失效已摘除；非 ths 数据源不再受支持。
                result.error = (
                    f"数据源 '{data_source}' 已停用（东财 DDE 选股支路随数据源失效移除），"
                    "请使用 data_source='ths'（问财）"
                )
                logger.error(f"[选股] {result.error}")
                return result

            # 补充技术指标评分（短线评分核心）
            if not result.error and result.stocks:
                self._enrich_technical_scores(result)

            # 补充板块强度评分
            if not result.error and result.stocks:
                self._enrich_sector_scores(result)

            # 补充舆情热度/情绪评分
            if not result.error and result.stocks:
                self._enrich_sentiment_scores(result)

            # 补充风险评估
            if not result.error and result.stocks:
                self._enrich_risk_scores(result)

            # 挂仓感知整合（标记已持仓、调整推荐级别）
            if not result.error and result.stocks and self.config.screener_portfolio_enabled:
                self._enrich_portfolio_context(result)

            # AI 综合评分（LLM 点评）
            if not result.error and result.stocks:
                self._compute_composite_scores(result)

            # 保存结果到数据库
            self._save_screener_results(result)

            # 自动记录模拟交易
            if not result.error and result.stocks and self.config.screener_auto_trade:
                self._record_simulated_trades(result)

            elapsed = time.time() - start_time
            result.elapsed_seconds = elapsed
            logger.info(
                f"[选股] 完成: 数据源={data_source}, "
                f"满足 {result.matched_count} 只, "
                f"耗时 {elapsed:.1f}s"
            )

        except Exception as e:
            result.error = str(e)
            logger.exception(f"[选股] 执行失败: {e}")

        return result

    def _run_ths_screener(self, result: ScreenerRunResult, top_n: int) -> None:
        """使用同花顺问财选股"""
        from src.services.ths_screener import THSScreenerService

        service = THSScreenerService(self.config)
        ths_result = service.get_dde_screener()

        if ths_result.error:
            result.error = ths_result.error
            return

        result.query = ths_result.query
        result.total_scanned = ths_result.total_count
        result.matched_count = ths_result.total_count

        # 转换为统一格式
        result.stocks = [
            ScreenerStock(
                code=s.code,
                name=s.name,
                score=100.0,
                source="ths"
            )
            for s in ths_result.stocks[:top_n]
        ]

        logger.info(f"[选股-问财] 获取到 {ths_result.total_count} 只股票")

    def _enrich_technical_scores(self, result: ScreenerRunResult) -> None:
        """
        补充技术指标评分

        从 TechnicalIndicatorService 获取短线技术信号，
        更新 ScreenerStock 的技术指标字段。
        """
        from src.services.technical_indicator import TechnicalIndicatorService

        tech_service = TechnicalIndicatorService(self.config)
        codes = [s.code for s in result.stocks]

        tech_result = tech_service.analyze_stocks(codes)
        if tech_result.error:
            logger.warning(f"[选股] 技术评分获取失败: {tech_result.error}")
            return

        self._technical_signals = tech_result.signals

        for stock in result.stocks:
            signals = tech_result.signals.get(stock.code)
            if signals is None:
                continue

            stock.technical_score = signals.technical_score
            stock.volume_ratio = signals.volume_ratio
            stock.turnover_rate = signals.turnover_rate
            stock.rsi6 = signals.rsi6
            stock.ma_alignment = signals.ma_alignment
            stock.breakout_5d = signals.breakout_5d
            stock.signal_tags = signals.signal_tags
            stock.dde_big_order = signals.dde_big_order or stock.dde_big_order

        logger.info(
            f"[选股] 技术评分完成: {len(tech_result.signals)}/{len(result.stocks)} 只"
        )

    def _enrich_sector_scores(self, result: ScreenerRunResult) -> None:
        """
        补充板块强度评分

        获取强势板块列表，对每只候选股查找其所属板块，
        匹配到最强板块的 strength_score 作为该股板块分。
        """
        from src.services.sector_rotation import SectorRotationService
        from data_provider import DataFetcherManager

        try:
            sector_service = SectorRotationService(self.config)
            rotation = sector_service.get_rotation_analysis()
        except Exception as e:
            logger.warning(f"[选股] 板块评分获取失败: {e}")
            return

        # 构建板块名称 → strength_score 映射
        # 使用全部分析过的板块，确保更多个股能匹配到所属板块
        board_score_map: Dict[str, float] = {}
        mainline_names = set()  # 主线板块名称集合

        all_sectors = rotation.all_sectors or (
            rotation.strong_sectors
            + rotation.emerging_sectors
            + rotation.declining_sectors
        )
        for sector in all_sectors:
            if sector.strength_score > 0:
                board_score_map[sector.name] = sector.strength_score
                # 记录主线板块（强势 + 崛起）
                if sector in rotation.strong_sectors or sector in rotation.emerging_sectors:
                    mainline_names.add(sector.name)

        if not board_score_map:
            logger.info("[选股] 无强势/崛起板块，跳过板块评分")
            return

        # 批量获取个股所属板块
        fetcher = DataFetcherManager()
        matched_count = 0

        for stock in result.stocks:
            try:
                boards = fetcher.get_belong_boards(stock.code)
                if not boards:
                    continue

                # 收集所有匹配的板块信息
                sector_scores = []  # [(score, weight), ...]
                all_sectors_info = []  # 所有板块详细信息

                for board in boards:
                    board_name = board.get("name", "")
                    board_type = board.get("type", "concept")

                    if board_name in board_score_map:
                        score = board_score_map[board_name]
                        # 主线板块权重更高（1.5x），其他板块权重 1.0
                        is_mainline = board_name in mainline_names
                        weight = 1.5 if is_mainline else 1.0

                        sector_scores.append((score, weight))
                        all_sectors_info.append({
                            "name": board_name,
                            "score": score,
                            "type": board_type,
                            "is_mainline": is_mainline,
                            "weight": weight,
                        })

                # Top-N 过滤：只取强度最高的 3 个板块，避免稀释效应
                # 学术参考：COAR-HGNN (KBS, 2024) 使用注意力机制动态加权
                # 简化方案：Top-3 过滤，聚焦核心驱动概念
                if sector_scores:
                    # 按强度排序，只取 Top 3
                    sorted_indices = sorted(
                        range(len(sector_scores)),
                        key=lambda i: sector_scores[i][0],
                        reverse=True
                    )
                    top_indices = sorted_indices[:3]

                    top_sector_scores = [sector_scores[i] for i in top_indices]
                    top_sectors_info = [all_sectors_info[i] for i in top_indices]

                    # 计算加权平均分数（基于 Top 3）
                    total_weight = sum(w for _, w in top_sector_scores)
                    weighted_score = sum(s * w for s, w in top_sector_scores) / total_weight

                    stock.sector_score = weighted_score
                    stock.all_sectors = all_sectors_info  # 保留所有板块信息用于展示

                    # 选择最强板块作为主板块（向后兼容）
                    best_sector = max(all_sectors_info, key=lambda x: x["score"])
                    stock.sector_name = best_sector["name"]

                    matched_count += 1
            except Exception as e:
                logger.debug(f"[选股] 板块匹配失败 {stock.code}: {e}")

        logger.info(
            f"[选股] 板块评分完成: {matched_count}/{len(result.stocks)} 只匹配到强势板块"
        )

    def _enrich_sentiment_scores(self, result: ScreenerRunResult) -> None:
        """
        补充舆情热度/情绪评分

        从 ForumSentimentService 获取热门股和热门板块数据，
        匹配候选股的热度和情绪分。
        匹配逻辑：
        1. 个股维度：精确匹配股票代码
        2. 板块维度：匹配候选股所属板块与 NGA 热门板块
        """
        from src.services.forum_sentiment import ForumSentimentService

        try:
            sentiment_service = ForumSentimentService(self.config)
            if not sentiment_service.is_available:
                logger.info("[选股] 舆情服务未配置（缺少 NGA Cookie），跳过")
                return

            sent_result = sentiment_service.fetch_hot_stocks()
        except Exception as e:
            logger.warning(f"[选股] 舆情评分获取失败: {e}")
            return

        # 构建 code → ForumHotStock 映射（个股维度）
        hot_map: Dict[str, Any] = {
            hs.code: hs for hs in sent_result.hot_stocks
        }

        # 构建 sector_name → ForumHotSector 映射（板块维度）
        sector_map: Dict[str, Any] = {
            hs.name: hs for hs in sent_result.hot_sectors
        }

        matched_stock_count = 0
        matched_sector_count = 0

        for stock in result.stocks:
            # 1. 个股维度匹配
            hot = hot_map.get(stock.code)
            if hot is not None:
                stock.heat_score = hot.heat_score
                stock.sentiment_score = hot.sentiment_score
                matched_stock_count += 1
            # 2. 板块维度匹配（如果个股没匹配到，尝试匹配板块）
            elif stock.sector_name and stock.sector_name in sector_map:
                sector_hot = sector_map[stock.sector_name]
                # 板块热度作为基础分，情绪分取板块情绪
                stock.heat_score = sector_hot.heat_score
                stock.sentiment_score = sector_hot.sentiment_score
                matched_sector_count += 1
                logger.debug(f"[选股] {stock.code} 通过板块 '{stock.sector_name}' 匹配舆情")

        logger.info(
            f"[选股] 舆情评分完成: {matched_stock_count} 只个股匹配, "
            f"{matched_sector_count} 只板块匹配"
        )

    def _enrich_risk_scores(self, result: ScreenerRunResult) -> None:
        """
        补充风险评估

        复用 TechnicalSignals 数据 + akshare PE 数据，
        评估估值/波动率/超买风险。
        """
        from src.services.risk_assessment import RiskAssessmentService

        try:
            risk_service = RiskAssessmentService(self.config)
            codes = [s.code for s in result.stocks]
            assessments = risk_service.assess(
                codes, technical_signals=self._technical_signals
            )
        except Exception as e:
            logger.warning(f"[选股] 风险评估失败: {e}")
            return

        for stock in result.stocks:
            assessment = assessments.get(stock.code)
            if assessment is None:
                continue
            stock.risk_score = assessment.risk_score
            stock.risk_tags = assessment.risk_tags

        logger.info(
            f"[选股] 风险评估完成: {len(assessments)}/{len(result.stocks)} 只"
        )

    def _enrich_portfolio_context(self, result: ScreenerRunResult) -> None:
        """持仓感知整合：标记已持仓股票，调整推荐级别。"""
        try:
            from src.services.portfolio_service import PortfolioService
            from src.repositories.portfolio_repo import PortfolioRepository
            from data_provider.base import normalize_stock_code

            repo = PortfolioRepository(self.db)
            service = PortfolioService(repo)

            snapshot = service.get_portfolio_snapshot()
            held_symbols: Dict[str, Dict] = {}

            for account in snapshot.get("accounts", []):
                for pos in account.get("positions", []):
                    sym = normalize_stock_code(pos.get("symbol", ""))
                    if sym:
                        held_symbols[sym] = {
                            "quantity": pos.get("quantity", 0),
                            "avg_cost": pos.get("avg_cost", 0),
                            "unrealized_pnl_pct": pos.get("unrealized_pnl_pct"),
                        }

            if not held_symbols:
                logger.info("[选股] 持仓感知: 无持仓或无账户")
                return

            held_count = 0
            for stock in result.stocks:
                norm_code = normalize_stock_code(stock.code)
                if norm_code in held_symbols:
                    pos = held_symbols[norm_code]
                    stock.portfolio_held = True
                    stock.portfolio_quantity = pos["quantity"]
                    stock.portfolio_avg_cost = pos["avg_cost"]
                    stock.portfolio_pnl_pct = pos.get("unrealized_pnl_pct")
                    # 已持仓的降级推荐：strong_buy → buy
                    if stock.recommendation == "strong_buy":
                        stock.recommendation = "buy"
                    held_count += 1

            logger.info(f"[选股] 持仓感知: {held_count}/{len(result.stocks)} 只已持仓")

        except Exception as e:
            logger.warning(f"[选股] 持仓感知整合失败（不影响主流程）: {e}")

    def _record_simulated_trades(self, result: ScreenerRunResult) -> None:
        """为高评分未持仓股票记录模拟买入。"""
        try:
            from src.services.portfolio_service import PortfolioService
            from src.repositories.portfolio_repo import PortfolioRepository

            repo = PortfolioRepository(self.db)
            service = PortfolioService(repo)
            accounts = service.list_accounts()
            if not accounts:
                logger.info("[选股] 自动建仓: 无可用账户，跳过")
                return

            account_id = accounts[0]["id"]
            min_score = self.config.screener_auto_trade_min_score
            quantity = self.config.screener_auto_trade_quantity
            traded = 0

            for stock in result.stocks:
                if stock.portfolio_held:
                    continue
                if stock.score < min_score:
                    continue
                if stock.recommendation not in ("strong_buy", "buy"):
                    continue

                try:
                    service.record_trade(
                        account_id=account_id,
                        symbol=stock.code,
                        trade_date=date.today(),
                        side="buy",
                        quantity=quantity,
                        price=0.0,
                        note=f"选股自动建仓: 综合评分{stock.score:.1f}",
                    )
                    stock.signal_tags.append("🆕模拟建仓")
                    stock.portfolio_held = True
                    stock.portfolio_quantity = quantity
                    traded += 1
                except Exception as e:
                    logger.warning(f"[选股] 模拟建仓失败 {stock.code}: {e}")

            if traded:
                logger.info(f"[选股] 自动建仓: {traded} 只")

        except Exception as e:
            logger.warning(f"[选股] 自动建仓流程失败（不影响主流程）: {e}")

    def _compute_composite_scores(self, result: ScreenerRunResult) -> None:
        """
        综合评分

        优先尝试 LLM AI 评分，失败则降级到加权公式。
        五维权重从 Config 读取（screener_weight_*）。
        """
        if not result.stocks:
            return

        # 尝试 AI 评分
        ai_success = self._ai_scoring(result)

        if not ai_success:
            # 降级：加权公式
            w_strategy = self.config.screener_weight_strategy
            w_technical = self.config.screener_weight_technical
            w_sector = self.config.screener_weight_sector
            w_forum = self.config.screener_weight_forum
            w_risk = self.config.screener_weight_risk

            for stock in result.stocks:
                # 舆情情绪分转为正向贡献（-1~1 → 0~100）
                forum_score = (stock.sentiment_score + 1.0) / 2.0 * 100.0
                # 如果无舆情数据，给中性分 50
                if stock.heat_score == 0 and stock.sentiment_score == 0:
                    forum_score = 50.0

                raw_score = (
                    stock.score * w_strategy          # 策略分（初始100或匹配分）
                    + stock.technical_score * w_technical
                    + stock.sector_score * w_sector
                    + forum_score * w_forum
                    + (100.0 - stock.risk_score) * w_risk  # 风险分取反
                )
                stock.score = round(raw_score, 1)

            logger.info("[选股] 综合评分完成（加权公式降级）")

        # 按综合评分排序
        result.stocks.sort(key=lambda s: s.score, reverse=True)

    def _ai_scoring(self, result: ScreenerRunResult) -> bool:
        """
        LLM AI 综合评分

        把五维数据序列化后交给 LLM 打分，返回是否成功。
        失败时返回 False，调用方降级到加权公式。
        """
        try:
            from src.agent.llm_adapter import LLMToolAdapter
        except ImportError:
            logger.warning("[选股] LLM 适配器不可用，降级到加权公式")
            return False

        # 构建 prompt 数据
        stock_data = []
        for i, stock in enumerate(result.stocks, 1):
            forum_score = (stock.sentiment_score + 1.0) / 2.0 * 100.0
            if stock.heat_score == 0 and stock.sentiment_score == 0:
                forum_score = 50.0

            stock_data.append({
                "序号": i,
                "代码": stock.code,
                "名称": stock.name,
                "策略分": round(stock.score, 1),
                "技术分": round(stock.technical_score, 1),
                "板块分": round(stock.sector_score, 1),
                "板块": stock.sector_name or "无",
                "舆情分": round(forum_score, 1),
                "热度": round(stock.heat_score, 1),
                "风险分": round(stock.risk_score, 1),
                "风险标签": stock.risk_tags,
                "技术信号": stock.signal_tags,
            })

        prompt = (
            "你是A股短线选股专家。请根据以下五维评分数据，为每只股票给出综合评分和简短点评。\n\n"
            "评分规则：\n"
            "- 综合评分 0-100，综合考虑五个维度\n"
            "- 技术面（量比/DDE/RSI/均线）是最重要的短线指标\n"
            "- 板块强势加分，板块弱势减分\n"
            "- 舆情正面加分，过热（热度极高+情绪极端）要警惕\n"
            "- 高风险（高估值/超买/高波动）扣分\n"
            "- 亏损股（PE<0）额外扣分\n\n"
            f"候选股票（{len(stock_data)} 只）：\n"
            f"{json.dumps(stock_data, ensure_ascii=False, indent=2)}\n\n"
            "请严格以 JSON 数组返回，每只股票包含 code 和 score（整数 0-100）和 reason（一句话点评，不超过40字）。\n"
            "示例：[{\"code\": \"600519\", \"score\": 85, \"reason\": \"技术面强势+板块龙头+主力强攻\"}]"
        )

        try:
            adapter = LLMToolAdapter(self.config)
            response = adapter.call_text(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
                timeout=30,
            )

            if not response.content:
                logger.warning("[选股] LLM 返回空内容，降级")
                return False

            # 解析 JSON 响应
            parsed = self._parse_llm_response(response.content)
            if not parsed:
                logger.warning("[选股] LLM 响应解析失败，降级")
                return False

            # 更新评分
            score_map: Dict[str, Dict] = {
                item["code"]: item for item in parsed
            }
            updated = 0
            for stock in result.stocks:
                ai_data = score_map.get(stock.code)
                if ai_data:
                    stock.score = float(ai_data["score"])
                    stock.ai_reasoning = ai_data.get("reason", "")
                    updated += 1

            logger.info(f"[选股] AI 综合评分完成: {updated}/{len(result.stocks)} 只")
            return updated > 0

        except Exception as e:
            logger.warning(f"[选股] AI 评分调用失败: {e}，降级到加权公式")
            return False

    def _parse_llm_response(self, content: str) -> List[Dict]:
        """解析 LLM 返回的 JSON 数组"""
        import re

        # 提取 JSON 数组（可能被 markdown 代码块包裹）
        match = re.search(r'\[[\s\S]*?\]', content)
        if not match:
            return []

        try:
            items = json.loads(match.group())
            if not isinstance(items, list):
                return []

            validated = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code", "")).strip()
                score = item.get("score")
                if not code or score is None:
                    continue
                try:
                    score_val = max(0, min(100, int(float(score))))
                except (ValueError, TypeError):
                    continue
                validated.append({
                    "code": code,
                    "score": score_val,
                    "reason": str(item.get("reason", ""))[:60],
                })
            return validated
        except (json.JSONDecodeError, TypeError):
            return []

    def _save_screener_results(self, result: ScreenerRunResult) -> int:
        """保存筛选结果到数据库"""
        saved = 0
        try:
            with self.db.get_session() as session:
                for stock in result.stocks:
                    from sqlalchemy import select, and_
                    stmt = select(ScreenerResultRecord).where(
                        and_(
                            ScreenerResultRecord.run_date == result.run_date,
                            ScreenerResultRecord.stock_code == stock.code,
                        )
                    )
                    existing = session.execute(stmt).scalar_one_or_none()

                    # 推荐级别：优先用已设置的 recommendation，否则按分数计算
                    recommendation = stock.recommendation or ('buy' if stock.score >= 80 else 'watch')

                    if existing:
                        existing.composite_score = stock.score
                        existing.strategy_score = stock.score
                        existing.recommendation = recommendation
                        existing.stock_name = stock.name
                        existing.matched_strategies_json = json.dumps([result.strategy_name])
                        existing.sector = stock.sector_name or None
                        existing.sector_strength = stock.sector_score or None
                        existing.forum_heat = stock.heat_score or None
                        existing.risk_tags_json = json.dumps(stock.risk_tags)
                        existing.ai_reasoning = stock.ai_reasoning or None
                    else:
                        record = ScreenerResultRecord(
                            run_date=result.run_date,
                            stock_code=stock.code,
                            stock_name=stock.name,
                            composite_score=stock.score,
                            strategy_score=stock.score,
                            recommendation=recommendation,
                            matched_strategies_json=json.dumps([result.strategy_name]),
                            sector=stock.sector_name or None,
                            sector_strength=stock.sector_score or None,
                            forum_heat=stock.heat_score or None,
                            risk_tags_json=json.dumps(stock.risk_tags),
                            ai_reasoning=stock.ai_reasoning or None,
                        )
                        session.add(record)

                    saved += 1

                session.commit()

            logger.info(f"[选股] 已保存 {saved} 条结果")

        except Exception as e:
            logger.error(f"[选股] 保存结果失败: {e}")

        return saved

    # ============================================================
    # GOT 驱动选股模式
    # ============================================================

    def run_got_mode(
        self,
        got_date: Optional[date] = None,
        top_n: int = 3
    ) -> ScreenerRunResult:
        """GOT 驱动的选股模式

        动态读取 Phase 1 GOT 输出，从主线/埋伏板块成分股中精选 Top N

        Args:
            got_date: GOT 运行日期，默认今天
            top_n: 输出 Top N 只股票

        Returns:
            ScreenerRunResult 包含精选股票
        """
        import time
        start_time = time.time()

        run_date = date.today().isoformat()
        run_time = datetime.now().strftime('%H:%M:%S')
        got_date = got_date or date.today()

        result = ScreenerRunResult(
            run_date=run_date,
            run_time=run_time,
            strategy_name="got_mode",
            data_source="got",
        )

        try:
            # 1. 加载 GOT 上下文
            got_context = self._load_got_context(got_date)
            if not got_context:
                raise ValueError(f"无法加载 {got_date} 的 GOT 上下文")

            logger.info(f"[GOT选股] 已加载 {got_date} 的 GOT 上下文")

            # 2. 提取主线/埋伏板块成分股
            universe = self._extract_got_universe(got_context)
            result.total_scanned = len(universe)
            logger.info(f"[GOT选股] 提取到 {len(universe)} 只候选股")

            if not universe:
                result.error = "未找到主线/埋伏板块成分股"
                return result

            # 3. 执行 5 轮漏斗筛选
            finalists = self._filter_got_finalists(universe, got_context, top_n)
            result.matched_count = len(finalists)
            logger.info(f"[GOT选股] 精选出 {len(finalists)} 只股票")

            # 3b. 4d/D-3 前置：候选池归零率基线观测（纯留痕，不进任何判定）
            try:
                result.funnel_stats = self._compute_funnel_stats(universe, finalists)
            except Exception as fe:  # noqa: BLE001 - 观测失败不影响选股主流程
                logger.warning(f"[GOT选股] funnel_stats 观测计算失败（已忽略）: {fe}")

            # 4. 构建输出
            result.stocks = finalists

            # 5. 保存结果到 JSON（用于回测）
            self._save_got_result(result, got_date)

            elapsed = time.time() - start_time
            result.elapsed_seconds = elapsed
            logger.info(f"[GOT选股] 完成，耗时 {elapsed:.1f}s")

        except Exception as e:
            result.error = str(e)
            logger.exception(f"[GOT选股] 执行失败: {e}")

        return result

    def _compute_funnel_stats(self, universe: List[Dict], finalists: List[ScreenerStock]) -> dict:
        """4d/D-3 前置：从 universe(入) vs finalists(出) 算板块级归零率（仅留痕）。

        “actionable 板块 → 0 只入选”的归零事件在此首次可观测，供 D-3 基线重标。
        round_survival_counts 取自 _filter_got_finalists 逐轮出口 trace（未初始化则缺）。
        """
        entering: Dict[str, str] = {}
        for c in universe:
            sec = c.get("sector") or ""
            if sec:
                entering.setdefault(sec, c.get("sector_type", ""))
        picked: Dict[str, int] = {}
        for s in finalists:
            sec = getattr(s, "sector_name", "") or ""
            if sec:
                picked[sec] = picked.get(sec, 0) + 1
        actionable = set(entering.keys())
        zero_pick = sorted(sec for sec in actionable if picked.get(sec, 0) == 0)
        trace = dict(getattr(self, "_last_funnel_trace", {}) or {})
        trace["final_out"] = len(finalists)
        return {
            "actionable_sector_count": len(actionable),
            "picked_sector_count": len(picked),
            "zero_pick_sectors": zero_pick,
            "zero_pick_rate": round(len(zero_pick) / len(actionable), 3) if actionable else 0.0,
            "round_survival_counts": trace,
            "note": "4d/D-3 基线观测，攒 ≥10 交易日后固化归零率基线值（不进选股判定）",
        }

    def _load_got_context(self, got_date: date) -> Optional[dict]:
        """动态加载 GOT 上下文

        优先读取 data/got_daily/YYYYMMDD/ 目录下的所有 6 个节点 JSON
        降级读取 data/aggregator_contexts/previous_context_YYYYMMDD.json
        """
        date_str = got_date.strftime("%Y%m%d")

        # 优先：每日存档（完整 6 节点）
        archive_dir = Path(f"data/got_daily/{date_str}")
        if archive_dir.exists():
            context = {}

            # 加载各节点上下文
            node_files = {
                "market_context": "market.json",
                "sector_context": "sector.json",
                "capital_context": "capital.json",
                "news_context": "news.json",
                "sentiment_context": "sentiment.json",
                "unified_context": "unified.json",
            }

            for key, filename in node_files.items():
                filepath = archive_dir / filename
                if filepath.exists():
                    with open(filepath, "r", encoding="utf-8") as f:
                        context[key] = json.load(f)

            # 如果至少加载了 unified_context，就返回
            if "unified_context" in context:
                return context

        # 降级：聚合上下文（仅 unified）
        context_path = Path(f"data/aggregator_contexts/previous_context_{date_str}.json")
        if context_path.exists():
            with open(context_path, "r", encoding="utf-8") as f:
                unified_data = json.load(f)
            return {"unified_context": unified_data}

        return None

    def _extract_got_universe(self, got_context: dict) -> List[Dict]:
        """从 GOT 上下文提取选股池（先用问财快速筛选）

        Returns:
            [{"code": "600519", "name": "贵州茅台", "sector": "白酒",
              "sector_type": "active_mainline", "sector_score": 80}, ...]
        """
        sector_ctx = got_context.get("sector_context", {})

        # 检查 notes 字段，识别"兜底填充"的板块
        notes = sector_ctx.get("notes", [])
        fallback_sectors = set()
        for note in notes:
            # 解析格式："⚠️ 活跃主线: 无板块满足标准，**兜底填充** 多元金融（80分, neutral）— 非真正达标"
            if "兜底填充" in note and "非真正达标" in note:
                # 提取板块名称（在"兜底填充"和"（"之间）
                import re
                match = re.search(r"兜底填充\*\*?\s*([^（(]+)[（(]", note)
                if match:
                    sector_name = match.group(1).strip()
                    fallback_sectors.add(sector_name)
                    logger.warning(f"识别到兜底填充板块: {sector_name}")

        # 提取主线板块（active_mainline），展开 MainlineGroup → 成员板块
        active_mainline_groups = sector_ctx.get("active_mainline", [])
        # 兼容新旧格式：如果元素有 members 字段则是新格式（MainlineGroup）
        active_mainlines = []
        for item in active_mainline_groups:
            if "members" in item:
                # 新格式：MainlineGroup，展开成员
                for m in item["members"]:
                    m["_group_name"] = item.get("group_name", "")
                    m["_group_score"] = item.get("group_score", 0)
                    active_mainlines.append(m)
            else:
                # 旧格式兼容：直接是 SectorDetail
                active_mainlines.append(item)

        qualified_mainlines = [
            s for s in active_mainlines
            if s.get("name") not in fallback_sectors
        ]

        if len(qualified_mainlines) < len(active_mainlines):
            logger.info(
                f"主线板块: {len(active_mainlines)} 个中 "
                f"{len(qualified_mainlines)} 个达标，"
                f"{len(active_mainlines) - len(qualified_mainlines)} 个为兜底填充已排除"
            )

        # 提取埋伏板块（ambush），也排除兜底填充的
        ambush_sectors = sector_ctx.get("ambush", [])
        qualified_ambush = [
            s for s in ambush_sectors
            if s.get("name") not in fallback_sectors
        ]

        universe = []

        # 从达标的主线/埋伏板块获取成分股（用问财快速筛选）
        for sector_info in qualified_mainlines + qualified_ambush:
            # L3 清洗：TDX 板块名可能带 \u0000 填充/首尾空白
            from src.services.board_name_resolver import clean_board_name
            sector_name = clean_board_name(sector_info.get("name") or "")
            sector_score = sector_info.get("strength_score", 0)
            sector_type = "active_mainline" if sector_info in qualified_mainlines else "ambush"

            # 用问财快速筛选板块成分股
            constituents = self._get_sector_constituents_filtered(sector_name, sector_type)

            if not constituents:
                logger.warning(f"[选股池] {sector_name}({sector_type}) 问财返回 0 只，该板块无候选股")

            for stock_info in constituents:
                # 提取过热与生命周期信息（v2 过热检测升级，Phase 3 联动）
                overheat_sev = sector_info.get("overheat_severity", 0) or 0
                lifecycle_info = sector_info.get("lifecycle") or {}
                lifecycle_phase = lifecycle_info.get("phase", "") if isinstance(lifecycle_info, dict) else ""

                universe.append({
                    "code": stock_info["code"],
                    "name": stock_info.get("name", ""),
                    "sector": sector_name,
                    "sector_type": sector_type,
                    "sector_score": sector_score,
                    # v2: 过热与生命周期（供漏斗联动使用）
                    "sector_overheat_severity": overheat_sev,
                    "sector_lifecycle_phase": lifecycle_phase,
                    # 保留问财量价+DDE 数据（供资金面/技术面评分使用）
                    "price": stock_info.get("price", 0),
                    "dde_net_pct": stock_info.get("dde_net_pct", 0),
                    "dde_5d_sum": stock_info.get("dde_5d_sum", 0),
                    "dde_degraded": stock_info.get("dde_degraded", False),
                    "change_pct": stock_info.get("change_pct", 0),
                    "turnover_rate": stock_info.get("turnover_rate", 0),
                    "volume": stock_info.get("volume", 0),
                    # 问财实际查询的板块名（resolve_with_kind 产物，可能与 Node 6 标签不同）
                    "sector_query_board": stock_info.get("sector_query_board", ""),
                })

        # 去重（同一股票可能在多个板块）
        seen = set()
        deduped = []
        for stock in universe:
            if stock["code"] not in seen:
                seen.add(stock["code"])
                deduped.append(stock)

        msg = (
            f"选股池: {len(qualified_mainlines)} 个主线 + "
            f"{len(qualified_ambush)} 个埋伏 → {len(deduped)} 只股票（问财初筛后）"
        )
        if not deduped:
            logger.error(f"{msg} ⚠️ 全部板块问财返回 0 只，Phase 2 将无候选股！")
        else:
            logger.info(msg)

        return deduped

    def _get_sector_constituents_filtered(self, sector_name: str, sector_type: str = "active_mainline") -> List[Dict]:
        """用问财快速筛选板块成分股（DDE 策略）

        主线板块（active_mainline）：要求当天 DDE 强势（>=0.8%）
        埋伏板块（ambush）：要求 DDE 连续 3 天为正（确认主力持续吸筹，不要求当天爆发）

        Returns:
            [{"code": "600519", "name": "贵州茅台", "price": ..., "dde_net_pct": ...}, ...]
        """
        try:
            from src.services.ths_screener import THSScreenerService
            from src.services.board_name_resolver import clean_board_name, get_board_name_resolver
            ths_service = THSScreenerService(self.config)

            # L3 清洗 + L1/L2 解析（含 kind 信息）
            sector_name = clean_board_name(sector_name)
            resolver = get_board_name_resolver()

            # Q7-B（audit_20260922）：当日负缓存预检——已知失败名同日直接短路，
            # 不再白烧双语法 × 两轮问财查询
            if resolver.is_failed_today(sector_name):
                logger.info(f"[问财初筛-{sector_type}] {sector_name} 当日负缓存命中，跳过重试")
                return []

            board_name, kind = resolver.resolve_with_kind(sector_name)

            # DDE 筛选条件
            if sector_type == "active_mainline":
                # 主线：当天 DDE 强势（单条件，降低阈值避免交集归零）
                dde_condition = "1个交易日前的dde大单净量>=0.5%，"
            else:
                # 埋伏：近5日DDE累计为正（吸筹信号，不要求连续，刚启动的板块也能入选）
                dde_condition = "近5个交易日的dde大单净量合计>0，"

            # 概念→行业关键词映射（用于过滤蹭概念的票）
            # 键名必须与 Phase 1 输出的板块名称完全一致（通常带"概念"后缀）
            # 2026-08-18 补全：覆盖当前活跃主线板块，防止蹭概念票混入
            concept_industry_map = {
                # ── 跨类别映射（TDX概念→THS行业） ──
                "水产品": ["水产", "渔业", "养殖"],
                # ── 原有 ──
                "数据要素": ["计算机", "软件", "数据处理", "信息服务", "互联网", "通信"],
                "AI产业链": ["计算机", "软件", "电子", "通信", "人工智能", "芯片", "半导体"],
                "AIGC概念": ["计算机", "软件", "传媒", "互联网", "人工智能"],
                "AI智能体": ["计算机", "软件", "人工智能", "机器人"],
                "华为鸿蒙": ["计算机", "软件", "电子", "通信", "手机"],
                "CXO概念": ["医药", "医疗", "生物", "制药", "CRO", "CDMO"],
                "生物制药": ["医药", "医疗", "生物", "制药", "生物医药"],
                "生物医药": ["医药", "医疗", "生物", "制药"],
                "黄金概念": ["贵金属", "有色", "矿业", "黄金"],
                "黄金": ["贵金属", "有色", "矿业", "黄金"],  # 兼容不带"概念"后缀
                "有色金属": ["有色", "金属", "矿业", "铜", "铝", "锂"],
                "有色": ["有色", "金属", "矿业", "铜", "铝", "锂"],  # 兼容简称
                "钢铁": ["钢铁", "金属制品", "特钢"],
                "化纤": ["化学纤维", "化工", "涤纶", "氨纶"],
                "汽车整车": ["汽车", "整车", "新能源车"],
                "汽车芯片": ["汽车", "半导体", "芯片", "集成电路", "电子"],
                "虚拟电厂": ["电力", "电气", "电网", "能源"],
                "合成生物": ["生物", "医药", "化工", "发酵", "酶制剂"],
                "稀土永磁": ["有色", "稀土", "金属", "磁性材料"],
                "铜": ["有色", "铜", "金属", "矿业"],
                "复合铜箔": ["有色", "铜", "金属", "新材料", "锂电"],
                "5G概念": ["通信", "电子", "芯片", "半导体", "网络"],

                # ── 2026-08-18 补全：光伏/电池技术 ──
                "TOPCon电": ["光伏", "电池", "太阳能", "新能源", "硅片", "电气设备", "半导体设备", "电力设备"],
                "BC电池":   ["光伏", "电池", "太阳能", "新能源", "硅片", "电气设备", "电力设备"],
                "钙钛矿电": ["光伏", "电池", "太阳能", "新能源", "材料", "化工", "电气设备"],
                "钠电池":   ["电池", "新能源", "储能", "化工", "电气设备", "锂电"],
                "固态电池": ["电池", "新能源", "储能", "化工", "材料", "电气设备"],

                # ── 2026-08-18 补全：电子/半导体 ──
                "PCB概念":  ["电子", "电路", "PCB", "印刷电路", "覆铜板", "电子元器件", "半导体"],
                "CPO概念":  ["光模块", "光通信", "通信设备", "电子", "半导体", "光电子"],
                "OLED概念": ["显示", "面板", "电子", "半导体", "光电", "电子元器件"],
                "MiniLED":  ["显示", "面板", "LED", "光电", "电子", "半导体"],
                "光刻机":   ["半导体设备", "光刻", "精密制造", "光学", "电子", "机械设备"],

                # ── 2026-08-18 补全：消费电子/显示 ──
                "消费电子": ["电子", "消费电子", "手机", "智能硬件", "声学", "光学"],
                "折叠屏":   ["显示", "面板", "手机", "消费电子", "柔性", "电子"],
                "玻璃基板": ["显示", "玻璃", "面板", "电子", "新材料", "化工"],

                # ── 2026-08-18 补全：通信/算力 ──
                "光通信":   ["光模块", "光纤", "光器件", "通信", "电子", "通信设备"],
                "液冷服务": ["服务器", "数据中心", "通信设备", "电子", "散热", "机械设备"],
                "数据中心": ["计算机", "服务器", "数据中心", "通信", "电子", "互联网"],

                # ── 2026-08-18 补全：新能源/汽车 ──
                "新能源车": ["汽车", "新能源车", "电动车", "电池", "电机", "电控"],
                "高压快充": ["充电", "电力", "电气", "新能源", "电池", "充电桩"],
                "光伏":     ["光伏", "太阳能", "新能源", "电池", "电气设备"],

                # ── 2026-08-18 补全：军工/航天 ──
                "商业航天": ["航天", "军工", "航空", "卫星", "国防", "机械设备"],
                "国防军工": ["军工", "国防", "航空", "航天", "兵器", "船舶"],
                "无人机":   ["航空", "无人机", "军工", "飞行器", "机械设备"],
                # 2026-10-20 backlog §7 #18：问财对「军贸概念」应答语义不稳定
                # （两组探针 30 只互斥，混入绿电/消费票），无本行则行业过滤整段跳过
                "军贸概念": ["军工", "国防", "航空", "航天", "兵器", "船舶", "雷达", "导弹"],

                # ── 2026-08-18 补全：其他科技 ──
                "人形机器": ["机器人", "机械", "自动化", "伺服", "减速器", "电气设备"],
                "芯片":     ["半导体", "芯片", "集成电路", "电子", "电子元器件"],
                "存储芯片": ["半导体", "存储", "芯片", "内存", "闪存", "电子"],
                "先进封装": ["半导体", "封装", "芯片", "集成电路", "电子"],
                "第三代半": ["半导体", "碳化硅", "氮化镓", "化合物半导体", "电子", "芯片", "集成电路"],
                "有机硅概": ["有机硅", "硅材料", "化工", "新材料", "硅胶"],
                "石墨烯":   ["石墨烯", "碳材料", "新材料", "纳米", "化工"],
                "铜缆高速": ["通信", "电缆", "连接器", "电子", "数据中心", "铜"],
                "EDA概念":  ["电子设计自动化", "软件", "半导体", "芯片", "计算机"],
                "苹果概念": ["消费电子", "手机", "苹果", "电子", "声学", "光学"],
            }

            # 按 kind 决定语法尝试顺序
            concept_clause = f"所属同花顺概念板块是{board_name}，"
            industry_clause = f"所属同花顺板块是{board_name}，"
            if kind == "industry":
                board_clauses = [industry_clause, concept_clause]
            else:
                board_clauses = [concept_clause, industry_clause]

            common_suffix = "上市天数>365天，非ST，所属同花顺行业，"
            if sector_type != "active_mainline":
                # 审核修复 2.2：ambush 候选追加请求 5 日累计 DDE 列，
                # 与初筛口径（近5日合计>0）对齐，供 _score_capital 同口径打分
                common_suffix += "近5个交易日的dde大单净量合计，"

            def _try_query(query_template: str, max_results: int,
                           healed_name: Optional[str] = None) -> Optional[List[Dict]]:
                """按 board_clauses 顺序尝试查询，命中返回 stocks 列表

                healed_name: Q7-B 自愈重试时覆盖解析名（回写别名用原始 sector_name 不变）
                """
                _board_name = healed_name or board_name
                _clauses = board_clauses
                if healed_name:
                    _clauses = [
                        f"所属同花顺概念板块是{healed_name}，",
                        f"所属同花顺板块是{healed_name}，",
                    ]
                for clause in _clauses:
                    query = query_template.replace("{board_clause}", clause)
                    logger.info(f"[问财初筛-{sector_type}] {sector_name} (kind={kind}): {query[:80]}...")
                    ths_result = ths_service.query(query)
                    if not ths_result or not ths_result.stocks:
                        continue
                    # 命中：回写别名表（自愈路径写低置信，L1 不消费，需人工升级）
                    resolver.confirm(
                        sector_name, _board_name, kind=kind,
                        source="auto_lowconf" if healed_name else "auto",
                    )
                    # 行业过滤
                    industry_keywords = concept_industry_map.get(sector_name, [])
                    filtered_stocks = []
                    for s in ths_result.stocks:
                        if industry_keywords and s.industry:
                            if any(kw in s.industry for kw in industry_keywords):
                                filtered_stocks.append(s)
                            else:
                                logger.debug(f"[行业过滤] {s.code} {s.name} 行业'{s.industry}'与{sector_name}不匹配")
                        else:
                            filtered_stocks.append(s)
                    if len(filtered_stocks) < len(ths_result.stocks):
                        logger.info(
                            f"[行业过滤] {sector_name}: {len(ths_result.stocks)}→"
                            f"{len(filtered_stocks)} 只（过滤{len(ths_result.stocks)-len(filtered_stocks)}只）"
                        )
                    stocks = [
                        {
                            "code": s.code,
                            "name": s.name,
                            "price": s.price,
                            "dde_net_pct": s.dde_net_pct,
                            "dde_5d_sum": getattr(s, "dde_5d_sum", 0.0),
                            "change_pct": s.change_pct,
                            "turnover_rate": s.turnover_rate,
                            "volume": s.volume,
                            # 本次实际命中查询的板块名（可能已别名改写/自愈），
                            # 供 Node 7 板块校验用真实板块名检索而非 Node 6 标签
                            "sector_query_board": _board_name,
                        }
                        for s in filtered_stocks[:max_results]
                    ]
                    if stocks:
                        dde_range = ""
                        if stocks[0].get("dde_net_pct") is not None:
                            dde_range = (
                                f"（DDE: {stocks[0]['dde_net_pct']:.2f}%~"
                                f"{stocks[-1]['dde_net_pct']:.2f}%）"
                            )
                        logger.info(
                            f"[问财初筛-{sector_type}] {sector_name} 获取到 "
                            f"{len(stocks)} 只候选 {dde_range}"
                        )
                    return stocks
                return None

            # ── 第 1 轮：带 DDE 条件 ──
            result = _try_query(
                "{board_clause}" + f"{dde_condition}{common_suffix}按dde大单净量降序",
                max_results=15,
            )
            if result is not None:
                # ── 第 1.5 轮（v2.1）：返回量不足时放松 DDE 阈值 ──
                # 与第 2 轮互补：第 2 轮处理 _try_query 返回 None（查询本身空），
                # 第 1.5 轮处理 len(result) <= 2（含行业过滤清零的 []）
                if len(result) <= 2 and sector_type == "active_mainline":
                    relaxed_condition = "1个交易日前的dde大单净量>=0.3%，"
                    relaxed_result = _try_query(
                        "{board_clause}" + f"{relaxed_condition}{common_suffix}按dde大单净量降序",
                        max_results=15,
                    )
                    if relaxed_result and len(relaxed_result) > len(result):
                        before_count = len(result)
                        seen_codes = {s["code"] for s in result}
                        for s in relaxed_result:
                            if s["code"] not in seen_codes:
                                result.append(s)
                                seen_codes.add(s["code"])
                        logger.info(
                            f"[问财初筛-{sector_type}] {sector_name} "
                            f"DDE≥0.5% 仅 {before_count} 只，"
                            f"放松至 0.3% 补充至 {len(result)} 只"
                        )
                return result

            # ── 第 2 轮：不带 DDE 降级 ──
            logger.warning(f"[问财初筛-{sector_type}] {sector_name} DDE 轮全空，降级为不带 DDE")
            result = _try_query(
                "{board_clause}dde大单净量，" + f"{common_suffix}按dde大单净量降序",
                max_results=5,
            )
            if result is not None:
                # 审核修复 W10：降级候选打标，区分"真弱势出局"与"缺 DDE 数据被动出局"
                for s in result:
                    s["dde_degraded"] = True
                return result

            # 双语法 × 两轮全空 → 醒目告警
            logger.error(
                f"[问财初筛-{sector_type}] {sector_name} 双语法全空！"
                f"resolved={board_name}, kind={kind}。"
                f"尝试过的语法: {board_clauses}"
            )

            # Q7-B 自愈闭环（audit_20260922）：拉概念板块全列表做相似度匹配，
            # 命中候选名重试一次，成功则回写别名表（source: auto）
            healed = resolver.aggressive_match(board_name)
            if healed and healed != board_name:
                for _tmpl, _maxr in (
                    ("{board_clause}" + f"{dde_condition}{common_suffix}按dde大单净量降序", 15),
                    ("{board_clause}dde大单净量，" + f"{common_suffix}按dde大单净量降序", 5),
                ):
                    result = _try_query(_tmpl, _maxr, healed_name=healed)
                    if result is not None:
                        logger.info(
                            f"[问财初筛-{sector_type}] {sector_name} 自愈成功："
                            f"{board_name} → {healed}，别名表已回写"
                        )
                        return result

            # 自愈未挽回 → 当日负缓存（原始名+解析名都登记，覆盖入口预检与
            # resolve 兜底两处短路），同日不再重试
            resolver.mark_failed(sector_name)
            if board_name != sector_name:
                resolver.mark_failed(board_name)
            return []

        except Exception as e:
            logger.error(f"[问财初筛-{sector_type}] {sector_name} 失败: {e}")
            return []

    # ── 横盘 stagnation 过滤（回测实证：多窗口小涨幅区间胜率最低）──

    # TODO(校准): 以下阈值基于 2026-06-13~06-17 回测（44条实证），距今已 70 天。
    # 市场风格可能已变，建议每 3 个月用最新 60 天数据重新回测校准。
    # 参考 RegimeFolio (Zhang 2025) 的滚动百分位方法替代固定阈值。
    _STAGNATION_CALIBRATION_DATE = "2026-06-17"
    _STAGNATION_SAMPLE_COUNT = 44

    # 横盘死区定义（回测 44 条实证，2026-06-13 ~ 06-17）
    # 核心规律：多时间窗口均处于 0~10% 涨幅的票次日胜率仅 30~40%
    # 而"回调买入"或"中等涨幅确认"的票胜率 60~80%
    _STAGNATION_ZONES = [
        (3,  0.00, 0.02),   # 3日 0~2%：刚启动但未确认
        (5, -0.02, 0.02),   # 5日 -2~2%：横盘震荡
        (12, 0.00, 0.10),   # 12日 0~10%：中期无趋势
        (15, 0.00, 0.10),   # 15日 0~10%：中期无趋势
    ]

    def _filter_stagnation(
        self, candidates: List[Dict], fetcher_manager
    ) -> List[Dict]:
        """横盘过滤：排除多时间窗口均处于"死区"的票。

        回测依据（44 条历史选股，T+1 收益）：
        - 3日 0~2%: 胜率 33%
        - 5日 -2~2%: 胜率 33%
        - 12日 0~10%: 胜率 30%
        - 15日 0~10%: 胜率 40%
        反之，回调（<-5%）或中等涨幅（5~20%）胜率 60~80%。

        判定逻辑：如果一个票在 ≥2 个时间窗口都落入死区 → 排除。
        单窗口落入死区不排除（可能是时间尺度差异）。
        """
        if not candidates:
            return candidates

        kept = []
        stagnation_count = 0

        for stock in candidates:
            code = stock["code"]
            try:
                r = fetcher_manager.get_daily_data(code, days=30)
                df = r[0] if isinstance(r, tuple) else r
                if df is None or len(df) < 16:
                    kept.append(stock)  # 数据不足，降级保留
                    continue

                df = df.reset_index(drop=True)
                close = df["close"].values
                n = len(close)
                today_close = close[-1]

                # 检查各时间窗口是否落入死区
                dead_zones_hit = 0
                for days, low, high in self._STAGNATION_ZONES:
                    if n <= days:
                        continue
                    past_close = close[-(days + 1)]
                    if past_close <= 0:
                        continue
                    ret = (today_close - past_close) / past_close
                    if low <= ret < high:
                        dead_zones_hit += 1

                # ≥2 个窗口落入死区 → 横盘票，排除
                if dead_zones_hit >= 2:
                    stagnation_count += 1
                    logger.debug(
                        f"[横盘过滤] {code} 排除: {dead_zones_hit} 个窗口落入死区"
                    )
                else:
                    kept.append(stock)

            except Exception as e:
                logger.debug(f"[横盘过滤] {code} 计算失败({e})，降级保留")
                kept.append(stock)

        if stagnation_count > 0:
            logger.info(
                f"[横盘过滤] {len(candidates)} → {len(kept)} 只"
                f"（排除 {stagnation_count} 只多窗口横盘票）"
            )

        return kept

    # =================================================================
    # Step 2: 埋伏道四闸门（G-A1 ~ G-A4）
    # =================================================================

    def _run_ambush_lane(
        self,
        ambush_candidates: List[Dict],
        got_context: dict,
        fetcher_manager=None,
    ) -> Tuple[List[ScreenerStock], dict, dict]:
        """执行埋伏道四闸门筛选

        Returns:
            (lane_finalists, lane_shadow, lane_trace)
            - lane_finalists: 通过闸门的 ScreenerStock 列表
            - lane_shadow: 影子观测数据（供落盘）
            - lane_trace: 闸门计数（供 funnel_stats）
        """
        from src.got.catalyst_config import (
            AMBUSH_LANE_TOPK,
            AMBUSH_LANE_CATALYST_WINDOW_DAYS,
            AMBUSH_LANE_SAFETY_HIGH,
            AMBUSH_LANE_SAFETY_LOW,
            AMBUSH_LANE_ABSORPTION_WEIGHT,
            AMBUSH_LANE_VOLUME_RATIO_KNIFE,
            CATALYST_CONFIDENCE_GATE,
        )
        from src.got.board_aliases import normalize_sector_name
        from datetime import date as date_cls

        lane_trace: Dict[str, int] = {
            "lane_in": len(ambush_candidates),
            "g_a1_pass": 0,
            "g_a1_soft_pass": 0,
            "g_a1_flow_negative": 0,
            "g_a1_dde_degraded": 0,
            "g_a2_pool_pass": 0,
            "g_a2_no_catalyst": 0,
            "g_a2_low_confidence": 0,
            "g_a2_position_unsafe": 0,
            "g_a3_knife": 0,
            "g_a4_selected": 0,
            "cross_lane_collision": 0,
            "lane_overflow_n": 0,
            "lane_error": 0,
        }

        try:
            # ── G-A1: 资金面闸门 ──
            capital_ctx = got_context.get("capital_context", {})
            passed_a1, a1_trace = self._ga1_gate(ambush_candidates, capital_ctx)
            for k, v in a1_trace.items():
                lane_trace[k] = lane_trace.get(k, 0) + v

            # ── G-A2: 催化剂闸门 ──
            news_ctx = got_context.get("news_context", {})
            # pool fast-pass 白名单：Node 6 ambush_pool 成员（催化证明在池准入时已完成）
            unified_ctx = got_context.get("unified_context") or {}
            pool_names = {
                normalize_sector_name(c.get("sector_name", ""))
                for c in (unified_ctx.get("ambush_pool") or [])
            }
            # 构建板块 K 线查找函数（延迟加载，避免无埋伏候选时浪费）
            sector_kline_lookup = self._build_sector_kline_lookup(got_context)
            passed_a2, a2_trace = self._ga2_gate(
                passed_a1, news_ctx, sector_kline_lookup,
                AMBUSH_LANE_CATALYST_WINDOW_DAYS, CATALYST_CONFIDENCE_GATE,
                AMBUSH_LANE_SAFETY_HIGH, AMBUSH_LANE_SAFETY_LOW,
                ambush_pool_names=pool_names,
            )
            for k, v in a2_trace.items():
                lane_trace[k] = lane_trace.get(k, 0) + v

            # ── G-A3: 防接刀闸门 ──
            passed_a3, a3_trace = self._ga3_gate(
                passed_a2, fetcher_manager, AMBUSH_LANE_VOLUME_RATIO_KNIFE,
            )
            for k, v in a3_trace.items():
                lane_trace[k] = lane_trace.get(k, 0) + v

            # ── G-A4: 排序 + 同板块去重 + TopK ──
            # 计算板块 3d 流百分位（用于 absorption_strength）
            flow_percentile = self._compute_ambush_flow_percentile(passed_a3, capital_ctx)
            lane_finalists_dicts, a4_trace = self._ga4_rank(
                passed_a3, flow_percentile, AMBUSH_LANE_TOPK,
                AMBUSH_LANE_ABSORPTION_WEIGHT,
            )
            for k, v in a4_trace.items():
                lane_trace[k] = lane_trace.get(k, 0) + v

            # 转换为 ScreenerStock
            lane_finalists = []
            for c in lane_finalists_dicts:
                stock = ScreenerStock(
                    code=c["code"],
                    name=self._get_stock_name(c["code"], fetcher_manager),
                    score=0.0,  # 稍后计算
                    source="got",
                    sector_name=c.get("sector", ""),
                    sector_type="ambush",
                    sector_query_board=c.get("sector_query_board", ""),
                    sector_score=c.get("sector_score", 0),
                    ambush_rank_score=c.get("ambush_rank_score"),
                    dde_degraded=c.get("dde_degraded", False),
                )
                lane_finalists.append(stock)

            lane_shadow = {
                "mode": "shadow",  # ACTIVE 由 Node 7 侧判断
                "lane_finalists": [
                    {
                        "code": s.code,
                        "name": s.name,
                        "sector": s.sector_name,
                        "ambush_rank_score": s.ambush_rank_score,
                    }
                    for s in lane_finalists
                ],
                "note": f"G-A1→{len(passed_a1)} G-A2→{len(passed_a2)} G-A3→{len(passed_a3)} G-A4→{len(lane_finalists)}",
            }

        except Exception as e:
            # A4 fail-open: 异常时所有埋伏候选视为通过闸门，用受保护的降级排序
            logger.warning(f"埋伏道闸门异常，fail-open: {e}")
            lane_trace["lane_error"] = 1
            # 降级：所有候选视为通过，按输入顺序（或 dde 降序）排序，取 TopK
            from src.got.catalyst_config import AMBUSH_LANE_TOPK
            # 受保护的降级排序：按 dde_net_pct 降序（如果有），否则保持输入顺序
            try:
                sorted_candidates = sorted(
                    ambush_candidates,
                    key=lambda c: c.get("dde_net_pct", 0) or 0,
                    reverse=True
                )
            except Exception:
                # 如果排序也失败，直接用输入顺序
                sorted_candidates = list(ambush_candidates)
            lane_finalists_dicts = sorted_candidates[:AMBUSH_LANE_TOPK]
            lane_finalists = []
            for c in lane_finalists_dicts:
                stock = ScreenerStock(
                    code=c["code"],
                    name=self._get_stock_name(c["code"], fetcher_manager),
                    score=0.0,
                    source="got",
                    sector_name=c.get("sector", ""),
                    sector_type="ambush",
                    sector_query_board=c.get("sector_query_board", ""),
                    sector_score=c.get("sector_score", 0),
                    ambush_rank_score=c.get("ambush_rank_score"),
                    dde_degraded=c.get("dde_degraded", False),
                )
                lane_finalists.append(stock)
            lane_shadow = {
                "mode": "shadow",
                "lane_finalists": [
                    {"code": s.code, "name": s.name, "sector": s.sector_name, "ambush_rank_score": s.ambush_rank_score}
                    for s in lane_finalists
                ],
                "note": f"fail-open: {e}",
            }

        return lane_finalists, lane_shadow, lane_trace

    def _ga1_gate(
        self,
        candidates: List[Dict],
        capital_ctx: dict,
    ) -> Tuple[List[Dict], dict]:
        """G-A1: 资金面闸门

        条件 1: dde_degraded=True → fail（降级票不允许过闸）
        条件 2: 板块 3d 流 > 0 → pass; miss → soft_pass; < 0 → fail
        """
        from src.got.nodes.aggregator import AggregatorNode

        trace = {"g_a1_pass": 0, "g_a1_soft_pass": 0, "g_a1_flow_negative": 0, "g_a1_dde_degraded": 0}
        passed = []

        # 提取板块流映射
        evidence = capital_ctx.get("evidence", {})
        sector_flow = evidence.get("sector_flow", {})
        flow_map_3d = sector_flow.get("sector_flow_all_3d", {})
        flow_map_1d = sector_flow.get("sector_flow_all", {})  # fallback

        for c in candidates:
            # 条件 1: 降级票拦截
            if c.get("dde_degraded"):
                trace["g_a1_dde_degraded"] += 1
                continue

            # 条件 2: 板块 3d 流
            sector = c.get("sector", "")
            flow_3d, _ = AggregatorNode._lookup_flow_map(sector, flow_map_3d)
            if flow_3d is None:
                # fallback 到 1d 流
                flow_1d, _ = AggregatorNode._lookup_flow_map(sector, flow_map_1d)
                if flow_1d is None:
                    trace["g_a1_soft_pass"] += 1
                    passed.append(c)
                elif flow_1d > 0:
                    trace["g_a1_pass"] += 1
                    passed.append(c)
                else:
                    trace["g_a1_flow_negative"] += 1
            elif flow_3d > 0:
                trace["g_a1_pass"] += 1
                passed.append(c)
            else:
                trace["g_a1_flow_negative"] += 1

        return passed, trace

    def _ga2_gate(
        self,
        candidates: List[Dict],
        news_ctx: dict,
        sector_kline_lookup,
        window_days: int = None,
        confidence_gate: float = None,
        safety_high: float = None,
        safety_low: float = None,
        today=None,
        ambush_pool_names: Optional[set] = None,
    ) -> Tuple[List[Dict], dict]:
        """G-A2: 催化剂闸门

        失败原因三分：
        - g_a2_no_catalyst: 日历无该板块事件
        - g_a2_low_confidence: 有事件但 max confidence < gate
        - g_a2_position_unsafe: 位置安全带 fail

        催化证明双通道（2026-10-16 修复）：
        ①pool fast-pass——候选板块 ∈ Node 6 ambush_pool 即视为催化已证明。
          Node 6 入池已完成语义匹配（T5/relations）与 D4/G4 折信，本闸门
          不复算；D4 折信作用域 = pool 准入/排序，不下沉到道内闸门
          （与 P5 同口径）。生产 --got 路径 lane 候选恒 ⊆ pool 成员，
          此通道构造性生效；名字匹配在 --screener 回放路径仍是活的兜底，
          勿当死代码删除。
        ②名字匹配兜底——日历 affected_sectors 名字级匹配 + conf≥gate。
        两通道通过后共走同一位置安全带（close/high20/ma20 三件套）。
        """
        from datetime import date as date_cls
        from src.got.board_aliases import normalize_sector_name, sector_name_variants
        from src.got.catalyst_config import (
            AMBUSH_LANE_CATALYST_WINDOW_DAYS,
            CATALYST_CONFIDENCE_GATE,
            AMBUSH_LANE_SAFETY_HIGH,
            AMBUSH_LANE_SAFETY_LOW,
            CATALYST_MACRO_AS_REGIME_REF,
        )

        # 默认值从 catalyst_config 取
        if window_days is None:
            window_days = AMBUSH_LANE_CATALYST_WINDOW_DAYS
        if confidence_gate is None:
            confidence_gate = CATALYST_CONFIDENCE_GATE
        if safety_high is None:
            safety_high = AMBUSH_LANE_SAFETY_HIGH
        if safety_low is None:
            safety_low = AMBUSH_LANE_SAFETY_LOW

        trace = {"g_a2_pool_pass": 0, "g_a2_no_catalyst": 0, "g_a2_low_confidence": 0, "g_a2_position_unsafe": 0}
        passed = []
        today = today or date_cls.today()
        pool_names = ambush_pool_names or set()

        # 提取催化剂事件
        calendar_data = news_ctx.get("catalyst_calendar")
        if not calendar_data:
            # 无日历 → pool fast-pass 仍生效，仅名字通道全灭
            for c in candidates:
                if normalize_sector_name(c.get("sector", "")) in pool_names:
                    trace["g_a2_pool_pass"] += 1
                    self._ga2_position_belt(c, sector_kline_lookup, safety_high, safety_low, passed, trace)
                else:
                    trace["g_a2_no_catalyst"] += 1
            return passed, trace

        events = calendar_data.get("events", [])
        if not events:
            for c in candidates:
                if normalize_sector_name(c.get("sector", "")) in pool_names:
                    trace["g_a2_pool_pass"] += 1
                    self._ga2_position_belt(c, sector_kline_lookup, safety_high, safety_low, passed, trace)
                else:
                    trace["g_a2_no_catalyst"] += 1
            return passed, trace

        # 价值分级（2026-10-21）：例行宏观数据不入催化闸门评分（仍入日历）
        if CATALYST_MACRO_AS_REGIME_REF:
            events = [evt for evt in events if evt.get("source") != "macro_rule"]

        # 按板块分桶事件（归一化 + 去重）
        sector_events: Dict[str, List[dict]] = {}
        for evt in events:
            affected = evt.get("affected_sectors") or [evt.get("sector", "")]
            seen_keys = set()
            for s in affected:
                normalized = normalize_sector_name(s)
                if normalized in seen_keys:
                    continue
                seen_keys.add(normalized)
                sector_events.setdefault(normalized, []).append(evt)

        for c in candidates:
            sector = c.get("sector", "")
            normalized_sector = normalize_sector_name(sector)

            # ①pool fast-pass：Node 6 已裁决的催化证明，直接进位置安全带
            if normalized_sector in pool_names:
                trace["g_a2_pool_pass"] += 1
                self._ga2_position_belt(c, sector_kline_lookup, safety_high, safety_low, passed, trace)
                continue

            # 查找事件（精确 → 别名变体）
            matched_events = sector_events.get(normalized_sector, [])
            if not matched_events:
                for variant in sector_name_variants(sector):
                    if variant == normalized_sector:
                        continue
                    matched_events = sector_events.get(variant, [])
                    if matched_events:
                        break

            if not matched_events:
                trace["g_a2_no_catalyst"] += 1
                continue

            # 过滤窗口内事件 + 找 max confidence
            # 窗口含当日事件（days_to_event=0）：2026-10-16 实测当日事件
            # （军民融合/金价地缘 conf 0.8）被旧谓词 0 < d 误杀，而 Node 6
            # _compute_catalyst_score 的 day-0 分支（proximity 0.6）照常入池。
            in_window_events = []
            max_confidence = 0.0
            for evt in matched_events:
                try:
                    evt_date_str = evt.get("event_date", "")
                    if evt_date_str:
                        evt_date = date_cls.fromisoformat(str(evt_date_str)[:10])
                        days_to_event = (evt_date - today).days
                        if 0 <= days_to_event <= window_days:
                            in_window_events.append(evt)
                            conf = float(evt.get("confidence", 0))
                            if conf > max_confidence:
                                max_confidence = conf
                except (ValueError, TypeError):
                    continue

            if not in_window_events:
                trace["g_a2_no_catalyst"] += 1
                continue

            if max_confidence < confidence_gate:
                trace["g_a2_low_confidence"] += 1
                continue

            # 位置安全带检查
            self._ga2_position_belt(c, sector_kline_lookup, safety_high, safety_low, passed, trace)

        return passed, trace

    @staticmethod
    def _ga2_position_belt(c, sector_kline_lookup, safety_high, safety_low, passed, trace):
        """G-A2 位置安全带（name-match 与 pool fast-pass 共用）

        close ≤ high20×safety_high 且 close ≥ ma20×safety_low → pass；
        查无 K 线（短史/失败）→ soft pass；双高 → position_unsafe。
        """
        kline_data = sector_kline_lookup(c.get("sector", ""))
        if kline_data is None:
            passed.append(c)
            return
        close, high20, ma20 = kline_data
        if close <= high20 * safety_high and close >= ma20 * safety_low:
            passed.append(c)
        else:
            trace["g_a2_position_unsafe"] += 1

    def _ga3_gate(
        self,
        candidates: List[Dict],
        fetcher_manager,
        volume_ratio_knife: float = None,
    ) -> Tuple[List[Dict], dict]:
        """G-A3: 防接刀闸门

        条件: change_pct < 0 AND volume_ratio >= knife → fail
        """
        from src.got.catalyst_config import AMBUSH_LANE_VOLUME_RATIO_KNIFE

        # 默认值从 catalyst_config 取
        if volume_ratio_knife is None:
            volume_ratio_knife = AMBUSH_LANE_VOLUME_RATIO_KNIFE

        trace = {"g_a3_knife": 0}
        passed = []

        for c in candidates:
            change_pct = c.get("change_pct", 0) or 0
            volume_ratio = c.get("volume_ratio")

            # 如果 volume_ratio 缺失，尝试从 K 线计算
            if volume_ratio is None and fetcher_manager:
                try:
                    df, _ = fetcher_manager.get_daily_data(c["code"], days=10)
                    if df is not None and len(df) >= 6:
                        today_vol = df["volume"].iloc[-1]
                        avg_vol = df["volume"].iloc[-6:-1].mean()
                        if avg_vol > 0:
                            volume_ratio = today_vol / avg_vol
                except Exception:
                    pass

            # 判定
            if change_pct < 0 and volume_ratio is not None and volume_ratio >= volume_ratio_knife:
                trace["g_a3_knife"] += 1
            else:
                passed.append(c)

        return passed, trace

    def _ga4_rank(
        self,
        candidates: List[Dict],
        flow_percentile: Dict[str, float],
        topk: int = None,
        absorption_weight: float = None,
    ) -> Tuple[List[Dict], dict]:
        """G-A4: 排序 + 同板块去重 + TopK

        ambush_rank_score = confidence × proximity × (1 + weight × absorption_strength)
        """
        from datetime import date as date_cls
        from src.got.board_aliases import normalize_sector_name, sector_name_variants
        from src.got.catalyst_config import AMBUSH_LANE_TOPK, AMBUSH_LANE_ABSORPTION_WEIGHT

        # 默认值从 catalyst_config 取
        if topk is None:
            topk = AMBUSH_LANE_TOPK
        if absorption_weight is None:
            absorption_weight = AMBUSH_LANE_ABSORPTION_WEIGHT

        trace = {"g_a4_selected": 0}
        today = date_cls.today()

        # 为每个候选计算 ambush_rank_score
        scored_candidates = []
        for c in candidates:
            sector = c.get("sector", "")
            normalized_sector = normalize_sector_name(sector)

            # 简化版：用催化剂临近度作为 confidence × proximity
            # 实际应从 catalyst_details 取，但这里为简化直接用 0.5 作为默认
            confidence = 0.7  # 默认通过 G-A2 的至少有 0.7 置信度
            proximity = 0.5  # 默认中等临近度

            # 吸收强度（板块 3d 流百分位）
            absorption_strength = flow_percentile.get(normalized_sector, 0.5)

            # 计算排序分
            rank_score = confidence * proximity * (1 + absorption_weight * absorption_strength)
            c["ambush_rank_score"] = rank_score
            scored_candidates.append(c)

        # 同板块去重（保留最高分）
        sector_best: Dict[str, Dict] = {}
        for c in scored_candidates:
            sector = c.get("sector", "")
            normalized_sector = normalize_sector_name(sector)
            existing = sector_best.get(normalized_sector)
            if existing is None or c["ambush_rank_score"] > existing["ambush_rank_score"]:
                sector_best[normalized_sector] = c

        # 排序 + TopK
        sorted_candidates = sorted(sector_best.values(), key=lambda x: x["ambush_rank_score"], reverse=True)
        top_candidates = sorted_candidates[:topk]
        trace["g_a4_selected"] = len(top_candidates)

        return top_candidates, trace

    def _build_sector_kline_lookup(self, got_context: dict):
        """构建板块位置查找函数（供 G-A2 位置安全带使用）

        数据源：Node 2 `_analyze_single_sector` 预计算的 indicators 三标量
        （pos_close/pos_high20/pos_ma20，backlog §1 row 52 修法③）。
        旧实现读 ambush[].klines——SectorDetail 无 klines 字段，归档永不携带，
        生产恒 soft-pass（闸门退化双因子），已废弃。

        Returns:
            callable: sector_name → (close, high20, ma20) or None
        """
        sector_ctx = got_context.get("sector_context", {})
        kline_cache: Dict[str, tuple] = {}

        ambush_sectors = sector_ctx.get("ambush", [])
        for s in ambush_sectors:
            name = s.get("name", "")
            ind = s.get("indicators", {})
            close = ind.get("pos_close")
            high20 = ind.get("pos_high20")
            ma20 = ind.get("pos_ma20")
            if close is not None and high20 is not None and ma20 is not None:
                kline_cache[name] = (float(close), float(high20), float(ma20))

        def lookup(sector_name: str):
            return kline_cache.get(sector_name)

        return lookup

    def _compute_ambush_flow_percentile(
        self,
        candidates: List[Dict],
        capital_ctx: dict,
    ) -> Dict[str, float]:
        """计算埋伏板块 3d 流百分位（供 G-A4 absorption_strength 使用）

        Returns:
            {normalized_sector_name: percentile_0_to_1}
        """
        from src.got.nodes.aggregator import AggregatorNode
        from src.got.board_aliases import normalize_sector_name

        evidence = capital_ctx.get("evidence", {})
        sector_flow = evidence.get("sector_flow", {})
        flow_map_3d = sector_flow.get("sector_flow_all_3d", {})

        # 提取所有埋伏板块的 3d 流
        sector_flows: Dict[str, float] = {}
        for c in candidates:
            sector = c.get("sector", "")
            normalized = normalize_sector_name(sector)
            if normalized not in sector_flows:
                flow, _ = AggregatorNode._lookup_flow_map(sector, flow_map_3d)
                if flow is not None:
                    sector_flows[normalized] = flow

        # 计算百分位
        if not sector_flows:
            return {}

        sorted_flows = sorted(sector_flows.values())
        n = len(sorted_flows)
        percentile_map = {}
        for sector, flow in sector_flows.items():
            # 计算该 flow 在排序列表中的位置
            rank = sum(1 for f in sorted_flows if f <= flow)
            percentile = rank / n if n > 0 else 0.5
            percentile_map[sector] = percentile

        return percentile_map


    def _merge_lane_finalists(
        self,
        finalists: List[ScreenerStock],
        lane_finalists: List[ScreenerStock],
        top_n: int,
        lane_merge_mode: str,
        lane_trace: Dict,
    ) -> List[ScreenerStock]:
        """D4 合并主线 Round 5 结果与埋伏道 ``lane_finalists``（原地追加 + trace 回填）。

        - ``topn_fill``（缺省）：主线优先补位，``len(finalists) >= top_n`` 后埋伏票溢出即丢。
          ``run_got_mode``（``--screener`` 展示入口）沿用此语义，输出条数严格 ≤ top_n。
        - ``union``：埋伏道全量追加，不受 top_n 限制（道内已有 AMBUSH_LANE_TOPK=3 兜底），
          ``lane_overflow_n`` 恒 0——修掉「主线占满名额→埋伏道整道不可见」的生产缺陷。

        两模式共用：撞票（同一 code 主线已入选）归主线，``cross_lane_collision`` 计数后跳过。
        """
        mainline_codes = {s.code for s in finalists}
        _mainline_n = len(mainline_codes)
        cross_lane_collision = 0
        lane_overflow_n = 0
        _union_mode = lane_merge_mode == "union"
        for lf in lane_finalists:
            if lf.code in mainline_codes:
                cross_lane_collision += 1
                continue
            if _union_mode or len(finalists) < top_n:
                finalists.append(lf)
                mainline_codes.add(lf.code)
            else:
                lane_overflow_n += 1
        lane_trace["lane_merge_mode"] = lane_merge_mode
        lane_trace["cross_lane_collision"] = cross_lane_collision
        lane_trace["lane_overflow_n"] = lane_overflow_n
        if cross_lane_collision > 0:
            logger.info(f"埋伏道: {cross_lane_collision} 只撞票归主线")
        if lane_overflow_n > 0:
            logger.info(f"埋伏道: {lane_overflow_n} 只溢出（不进 top_n）")
        elif _union_mode:
            logger.info(
                f"埋伏道: union 合并，主线 {_mainline_n} 只 + 埋伏 "
                f"{len(lane_finalists) - cross_lane_collision} 只全量并入"
            )
        return finalists

    def _filter_got_finalists(
        self,
        universe: List[Dict],
        got_context: dict,
        top_n: int,
        lane_merge_mode: str = "topn_fill",
    ) -> List[ScreenerStock]:
        """5 轮漏斗筛选

        轮次 1: 信号过滤（排除衰竭板块）
        轮次 2: 资金面评分 Top 20（龙虎榜/主力资金/大宗交易）
        轮次 3: 波段技术确认 Top 15（MA20/布林带/周线趋势）
        轮次 4: 风险排雷 Top 8（估值/超买/波动率）
        轮次 5: 板块分散 Top N（每个板块最多 1 只）

        Args:
            lane_merge_mode: 埋伏道合并语义（2026-10-20 D4 union 修正）
                - ``"topn_fill"``（缺省）：主线优先补位，溢出即丢。``run_got_mode``
                  （``--screener`` 独立展示入口）沿用此语义，输出条数严格 ≤ top_n。
                - ``"union"``：主线照常 Round 5 内部 top_n 截断，埋伏道 ``lane_finalists``
                  全量追加、不受补位限制、无溢出丢弃（道内已有 AMBUSH_LANE_TOPK=3 兜底）。
                  撞票仍归主线（``cross_lane_collision++``）；``lane_overflow_n`` 恒 0。
                  Phase 2 生产路径用此语义，使埋伏道候选不被主线挤空。
        """
        from data_provider.base import DataFetcherManager
        fetcher_manager = DataFetcherManager()

        candidates = universe.copy()
        # 4d/D-3 前置：逐轮出口存活计数留痕（纯观测，不进判定；run_got_mode 消费后落盘）
        funnel_trace: Dict[str, int] = {"r0_in": len(candidates)}
        self._last_funnel_trace = funnel_trace

        # Step 1: 埋伏道拆分（GOT_AMBUSH_SPLIT_ENABLED=1 时启用）
        # 直接检查环境变量以支持运行时切换（测试场景）
        import os
        GOT_AMBUSH_SPLIT_ENABLED = os.environ.get("GOT_AMBUSH_SPLIT_ENABLED", "1") == "1"
        ambush_candidates = []
        if GOT_AMBUSH_SPLIT_ENABLED:
            ambush_candidates = [c for c in candidates if c.get("sector_type") == "ambush"]
            candidates = [c for c in candidates if c.get("sector_type") != "ambush"]
            funnel_trace["lane_in"] = len(ambush_candidates)
            funnel_trace["lane_mainline_in"] = len(candidates)
            logger.info(f"埋伏道拆分: 主线 {len(candidates)} 只, 埋伏 {len(ambush_candidates)} 只")


        # 轮次 0: 剔除涨停股（当日无法买入）
        def _is_limit_up(c: dict) -> bool:
            pct = c.get("change_pct", 0)
            code = c.get("code", "")
            # 创业板(300xxx)/科创板(688xxx) 涨跌幅限制 20%
            if code.startswith(("300", "688", "689")):
                return pct >= 19.5
            # 主板/中小板 涨跌幅限制 10%
            return pct >= 9.5

        limit_up_count = sum(1 for c in candidates if _is_limit_up(c))
        candidates = [c for c in candidates if not _is_limit_up(c)]
        logger.info(f"轮次 0: {len(candidates)} 只（剔除 {limit_up_count} 只涨停）")
        funnel_trace["r0"] = len(candidates)

        # 轮次 0b: 软过滤——涨 ≥8% 不参与买入排序（P0-3 新增）
        # 非涨停但涨幅过大，追高风险极高（创业板涨10%不算涨停但风险同样大）
        # 归零保底：过滤后池不足 5 只时，按涨幅从低放回过热票补足并打标，
        # 后续由 _score_risk 的 ≥8% -20 惩罚自然压后（防候选池归零）
        OVERHEAT_THRESHOLD = 8.0
        OVERHEAT_FLOOR = 5
        hot_pool = [c for c in candidates if (c.get("change_pct", 0) or 0) >= OVERHEAT_THRESHOLD]
        candidates = [c for c in candidates if (c.get("change_pct", 0) or 0) < OVERHEAT_THRESHOLD]
        if len(candidates) < OVERHEAT_FLOOR and hot_pool:
            hot_pool.sort(key=lambda c: c.get("change_pct", 0) or 0)
            need = OVERHEAT_FLOOR - len(candidates)
            for c in hot_pool[:need]:
                candidates.append(c)
            logger.info(
                f"轮次 0b: {len(candidates)} 只（过滤 {len(hot_pool)} 只过热票，"
                f"池不足放回 {min(need, len(hot_pool))} 只保底）"
            )
        else:
            logger.info(f"轮次 0b: {len(candidates)} 只（过滤 {len(hot_pool)} 只涨≥8%过热票）")

        # 轮次 1: 信号过滤（排除衰竭板块 + 过热 exhausted 板块）
        exhausted = [s.get("name") for s in got_context.get("sector_context", {}).get("exhausted_mainline", [])]
        candidates = [c for c in candidates if c["sector"] not in exhausted]

        # v2: 过热 exhausted 过滤（overheat_severity > 0.5 → 排除，带保底防归零）
        OVERHEAT_EXHAUSTED_FLOOR = 5
        overheat_exhausted = [c for c in candidates if c.get("sector_overheat_severity", 0) > 0.5]
        normal_pool = [c for c in candidates if c.get("sector_overheat_severity", 0) <= 0.5]
        if len(normal_pool) < OVERHEAT_EXHAUSTED_FLOOR and overheat_exhausted:
            # 池不足：按 severity 从低到高放回保底（放回票在 _score_risk 被 -25 重扣压制）
            overheat_exhausted.sort(key=lambda c: c.get("sector_overheat_severity", 0))
            need = OVERHEAT_EXHAUSTED_FLOOR - len(normal_pool)
            restored = overheat_exhausted[:need]
            for c in restored:
                c["_overheat_fallback"] = True
                normal_pool.append(c)
            restored_desc = ", ".join(
                f"{c.get('code')}({c.get('sector')})" for c in restored
            )
            logger.info(
                f"轮次 1: {len(normal_pool)} 只（排除衰竭+过热板块，"
                f"池不足放回 {len(restored)} 只保底: {restored_desc}）"
            )
        else:
            logger.info(
                f"轮次 1: {len(normal_pool)} 只（排除衰竭板块 + {len(overheat_exhausted)} 只过热exhausted）"
            )
        candidates = normal_pool

        # 轮次 1.5: 横盘过滤（排除多窗口小涨幅的停滞票）
        candidates = self._filter_stagnation(candidates, fetcher_manager)
        logger.info(f"轮次 1.5: {len(candidates)} 只（横盘过滤后）")
        funnel_trace["r1"] = len(normal_pool)
        funnel_trace["r1_5"] = len(candidates)

        # 轮次 2: 资金面评分 Top 20
        capital_ctx = got_context.get("capital_context", {})
        capital_scores, capital_avail = self._score_capital(candidates, capital_ctx)
        for c in candidates:
            c["capital_score"] = capital_scores.get(c["code"], 0)
            c["capital_available"] = capital_avail.get(c["code"], False)
        # 审核修复 2.3：三元组排序键——无数据票显式后置，同分用单日 DDE 打破并列
        candidates.sort(
            key=lambda x: (
                x["capital_available"],
                x["capital_score"],
                x.get("dde_net_pct") or 0,
            ),
            reverse=True,
        )
        top_pool = candidates[:20]
        dropped_pool = candidates[20:]

        # 审核修复 2.1：Tier A 主线保底（抽取为独立方法供单测）
        top_pool = self._apply_capital_floor(top_pool, dropped_pool, candidates)
        candidates = top_pool
        funnel_trace["r2"] = len(candidates)

        # 轮次 3: 波段技术确认 Top 15
        tech_scores = self._score_technical_wave(candidates, fetcher_manager)
        for c in candidates:
            c["tech_wave_score"] = tech_scores.get(c["code"], 0)
        candidates.sort(key=lambda x: x["tech_wave_score"], reverse=True)
        candidates = candidates[:15]
        logger.info(f"轮次 3: {len(candidates)} 只（波段技术 Top 15）")
        funnel_trace["r3"] = len(candidates)

        # ── 轮次 4: 风险排雷（主通道 Top 6 + 软保底至多 2）──
        risk_scores = self._score_risk(candidates, fetcher_manager)
        for c in candidates:
            c["risk_score_safe"] = risk_scores.get(c["code"], 0)
        candidates = self._apply_r4_filter(candidates, got_context)
        logger.info(f"轮次 4: {len(candidates)} 只（风险排雷后）")
        funnel_trace["r4"] = len(candidates)

        # 消息面评分（全量候选，不排序但记录分数）
        news_ctx = got_context.get("news_context", {})
        sector_ctx = got_context.get("sector_context", {})
        news_scores, news_avail = self._score_news(candidates, news_ctx, sector_ctx)
        for c in candidates:
            c["news_score"] = news_scores.get(c["code"], 0)
            c["news_available"] = news_avail.get(c["code"], False)

        # P5: 催化剂关联度重排（仅 ambush 板块，置信度 >= CATALYST_CONFIDENCE_GATE 时生效，A3 统一口径）
        from src.got.catalyst_config import CATALYST_CONFIDENCE_GATE
        catalyst_scores, catalyst_details = self._score_catalyst_relevance(candidates, got_context)
        max_catalyst_confidence = max(
            (d.get("confidence", 0) for d in catalyst_details.values()),
            default=0.0,
        )
        # 注意：此处不得复用外层 ambush_candidates（拆分后的埋伏票，供 L2205 埋伏道消费）；
        # 2026-10-13 生产事故：同名变量遮蔽导致埋伏道永不执行（拆分后主线池无 ambush 票 → 恒空）。
        p5_ambush = [c for c in candidates if c.get("sector_type") == "ambush"]
        if catalyst_scores and max_catalyst_confidence >= CATALYST_CONFIDENCE_GATE and p5_ambush:
            # 对 ambush 候选按催化剂关联度重排
            for c in candidates:
                c["catalyst_relevance_score"] = catalyst_scores.get(c["code"], 0)
            # 仅重排 ambush 候选（主线候选保持技术排序）
            p5_ambush.sort(
                key=lambda x: x.get("catalyst_relevance_score", 0), reverse=True
            )
            # 重建候选列表：主线保持原序 + ambush 按催化剂重排
            mainline_reordered = [c for c in candidates if c.get("sector_type") != "ambush"]
            candidates = mainline_reordered + p5_ambush
            logger.info(
                f"P5催化剂重排: {len(p5_ambush)} 只 ambush 候选重排 "
                f"(max_confidence={max_catalyst_confidence:.2f})"
            )
        elif catalyst_scores:
            if p5_ambush:
                logger.info(
                    f"P5催化剂重排跳过: max_confidence={max_catalyst_confidence:.2f} "
                    f"< {CATALYST_CONFIDENCE_GATE}"
                )
            else:
                logger.info(
                    "P5催化剂重排跳过: 主线池无 ambush 候选（埋伏道拆分已隔离，归埋伏道四闸门）"
                )

        # 轮次 5: 板块分散 Top N（每个板块最多 1 只）
        sector_count = {}
        finalists = []
        for c in candidates:
            sector = c["sector"]
            if sector_count.get(sector, 0) < 1:  # 每板块最多 1 只
                # 构建 ScreenerStock 对象
                stock = ScreenerStock(
                    code=c["code"],
                    name=self._get_stock_name(c["code"], fetcher_manager),
                    score=0.0,  # 稍后计算
                    source="got",
                    capital_score=c.get("capital_score", 0),
                    capital_available=c.get("capital_available", False),
                    news_score=c.get("news_score", 0),
                    news_available=c.get("news_available", False),
                    tech_wave_score=c["tech_wave_score"],
                    risk_score_safe=c["risk_score_safe"],
                    sector_name=c["sector"],
                    sector_score=c["sector_score"],
                    sector_type=c["sector_type"],
                    sector_query_board=c.get("sector_query_board", ""),
                    # 审核修复 W10/2.1：候选来源可观测性透传
                    dde_degraded=c.get("dde_degraded", False),
                    capital_floor_restored=c.get("capital_floor_restored", False),
                    # P5 催化剂关联度
                    catalyst_relevance_score=c.get("catalyst_relevance_score", 0),
                    catalyst_event_title=catalyst_details.get(c["code"], {}).get("event_title", ""),
                    catalyst_event_date=catalyst_details.get(c["code"], {}).get("event_date", ""),
                    catalyst_event_type=catalyst_details.get(c["code"], {}).get("event_type", ""),
                    catalyst_confidence=catalyst_details.get(c["code"], {}).get("confidence", 0),
                )

                # 计算综合评分（5 维）
                stock.score = self._compute_got_composite_score(stock, got_context)

                # 生成推荐理由
                stock.ai_reasoning = self._generate_got_reasoning(stock, got_context)

                # 生成回测快照
                stock.backtest_snapshot = self._build_backtest_snapshot(stock, fetcher_manager)

                finalists.append(stock)
                sector_count[sector] = sector_count.get(sector, 0) + 1
            if len(finalists) >= top_n:
                break
        logger.info(f"轮次 5: {len(finalists)} 只（板块分散）")

        # Step 3: 埋伏道汇总层（D4 主线优先 + D1' 跨道撞票）
        lane_finalists = []
        lane_shadow = {}
        lane_trace = {}
        if GOT_AMBUSH_SPLIT_ENABLED and ambush_candidates:
            try:
                lane_finalists, lane_shadow, lane_trace = self._run_ambush_lane(
                    ambush_candidates, got_context, fetcher_manager
                )
                # 闸门存活链 INFO 行（2026-10-13 可观测性修复：拆分→闸门→合并断链补齐）
                # 存活数 = 上一闸门出口 − 本闸门损失；fail-open 时 lane_error=1（见 shadow note）
                _a1_out = lane_trace.get("g_a1_pass", 0) + lane_trace.get("g_a1_soft_pass", 0)
                _a2_out = _a1_out - lane_trace.get("g_a2_no_catalyst", 0) \
                    - lane_trace.get("g_a2_low_confidence", 0) - lane_trace.get("g_a2_position_unsafe", 0)
                _a3_out = _a2_out - lane_trace.get("g_a3_knife", 0)
                logger.info(
                    "埋伏道闸门: in={lane_in} G-A1→{a1} G-A2→{a2} G-A3→{a3} G-A4→{a4} "
                    "(soft={sp} flow_neg={fn} dde_deg={dd} no_cat={nc} low_conf={lc} "
                    "pos_unsafe={pu} pool_pass={pp} knife={kn} err={err})".format(
                        lane_in=lane_trace.get("lane_in", 0),
                        a1=_a1_out,
                        a2=_a2_out,
                        a3=_a3_out,
                        a4=lane_trace.get("g_a4_selected", 0),
                        sp=lane_trace.get("g_a1_soft_pass", 0),
                        fn=lane_trace.get("g_a1_flow_negative", 0),
                        dd=lane_trace.get("g_a1_dde_degraded", 0),
                        nc=lane_trace.get("g_a2_no_catalyst", 0),
                        lc=lane_trace.get("g_a2_low_confidence", 0),
                        pu=lane_trace.get("g_a2_position_unsafe", 0),
                        pp=lane_trace.get("g_a2_pool_pass", 0),
                        kn=lane_trace.get("g_a3_knife", 0),
                        err=lane_trace.get("lane_error", 0),
                    )
                )
            except Exception as e:
                logger.warning(f"埋伏道执行失败，降级为空: {e}")
                lane_trace = {"lane_error": str(e)}

        # 合并埋伏道结果（D4：union 全量并集 / topn_fill 主线优先补位）
        if lane_finalists:
            finalists = self._merge_lane_finalists(
                finalists, lane_finalists, top_n, lane_merge_mode, lane_trace
            )
        else:
            # 零埋伏 finalist 日（全 G-A1 flow_negative 等）：合并块不执行，
            # lane_merge_mode 缺键会让 funnel_stats 对比口径断档（2026-10-20 实测）。
            lane_trace["lane_merge_mode"] = lane_merge_mode

        # funnel_trace 扁平汇入（单时点：合并块写入 cross_lane_collision/lane_overflow_n 之后，
        # 否则 int 按值拷贝看不到后写）。嵌套 `ambush_lane_trace` 键不再出现；
        # `g_a*` 等扁平键经 `_last_funnel_trace` → `_compute_funnel_stats` 整拷贝
        # → `meta.json.funnel_stats.round_survival_counts`。lane_error(str) 在异常分支：
        # funnel_stats 为 Optional[dict] 无内层类型校验，保留可观测；成功分支恒 int。
        funnel_trace.update(lane_trace)

        # 设置 shadow 属性供落盘使用（仅在埋伏道启用时设置，否则保持原值）
        if GOT_AMBUSH_SPLIT_ENABLED:
            self._ambush_lane_shadow = lane_shadow
            self._ambush_lane_trace = lane_trace


        # T1: Agent 终审（默认关闭，AGENT_STOCK_REVIEW_ENABLED=true 开启）
        from src.got.catalyst_config import AGENT_STOCK_REVIEW_ENABLED
        if AGENT_STOCK_REVIEW_ENABLED and finalists:
            finalists = self._agent_final_review(finalists, got_context)

        return finalists

    # =================================================================
    # T1: 候选股 Agent 终审
    # =================================================================

    _AGENT_REVIEW_PROMPT = """你是 A 股个股终审分析师。给定以下候选股票信息和市场环境，给出你的终审意见。

## 市场环境
- 大盘regime: {regime}
- 板块角色: {sector_type}
- 催化剂: {catalyst_info}

## 候选股
- 代码: {code}
- 名称: {name}
- 板块: {sector}
- 技术评分: {tech_score:.0f}/100
- 风险评分: {risk_score:.0f}/100（越高越安全）
- 资金面: {capital_score:.0f}/100
- 消息面: {news_score:.0f}/100

## 输出要求
以 JSON 格式输出你的终审意见：
```json
{{
    "verdict": "confirm/warn/reject",
    "confidence": 0.0-1.0,
    "reasoning": "一句话理由（30字内）",
    "risk_flags": ["风险点1", "风险点2"]
}}
```

verdict 含义：
- confirm: 确认推荐，无明显风险
- warn: 可推荐但有明确风险需关注
- reject: 建议否决，存在严重风险

只输出 JSON，不要其他文字。"""

    def _agent_final_review(
        self, finalists: List[ScreenerStock], got_context: dict
    ) -> List[ScreenerStock]:
        """T1: Agent 终审 — 对 Top N 候选股调用个股 Agent 做最终审查

        设计原则（§15.3 改造后）：
        - 使用轻量 review executor（3 工具 + max_steps=4），替代全量 Agent
        - 解析改用 try_parse_json（四策略），替代脆弱的正则
        - 影子模式：跑审查但不执行降级（AGENT_STOCK_REVIEW_SHADOW）
        - 只有否决权：reject → 降级展示（保留、标注、移末位），不删除
        - 默认关闭（AGENT_STOCK_REVIEW_ENABLED）
        - 最多审查 AGENT_STOCK_REVIEW_MAX 只（R7 成本预算）
        - 串行调用 + 空响应重试（R5 串行原则）
        - 失败时等同现状（R4 可降级）

        Returns:
            同输入列表，但 stock.agent_verdict/agent_notes/agent_decision_signal 已填充
        """
        from src.got.catalyst_config import (
            AGENT_STOCK_REVIEW_MAX,
            AGENT_STOCK_REVIEW_SHADOW,
        )
        from src.agent.factory import build_review_executor
        from src.agent.runner import try_parse_json

        # 截断到预算上限
        review_targets = finalists[:AGENT_STOCK_REVIEW_MAX]
        if len(finalists) > AGENT_STOCK_REVIEW_MAX:
            logger.info(
                f"[T1终审] 候选 {len(finalists)} 只，截断到 Top {AGENT_STOCK_REVIEW_MAX}"
            )

        # 提取市场环境信息
        market_ctx = got_context.get("market_context", {})
        regime = market_ctx.get("market_state", {}).get("regime", "unknown")

        try:
            executor = build_review_executor(review_type="t1")
        except Exception as exc:
            logger.warning(f"[T1终审] Review Agent 构建失败，降级跳过: {exc}")
            return finalists

        shadow_tag = "[SHADOW] " if AGENT_STOCK_REVIEW_SHADOW else ""

        for stock in review_targets:
            try:
                catalyst_info = (
                    f"{stock.catalyst_event_title} ({stock.catalyst_event_type}, "
                    f"置信度{stock.catalyst_confidence:.0%})"
                    if stock.catalyst_event_title
                    else "无催化剂事件"
                )

                prompt = self._AGENT_REVIEW_PROMPT.format(
                    regime=regime,
                    sector_type=stock.sector_type,
                    catalyst_info=catalyst_info,
                    code=stock.code,
                    name=stock.name,
                    sector=stock.sector_name,
                    tech_score=stock.tech_wave_score,
                    risk_score=stock.risk_score_safe,
                    capital_score=stock.capital_score,
                    news_score=stock.news_score,
                )

                # 串行调用，空响应重试。
                # 注：executor.run() 返回 AgentResult dataclass（非 dict），结果在 .content
                response_text = None
                for attempt in range(3):
                    try:
                        result = executor.run(prompt)
                        response_text = (
                            result.content if result and result.success else ""
                        )
                        if response_text:
                            break
                    except Exception as exc:
                        logger.debug(f"[T1终审] {stock.code} 第{attempt+1}次调用失败: {exc}")

                if not response_text:
                    stock.agent_verdict = "warn"
                    stock.agent_notes = "Agent 调用失败，降级为 warn"
                    continue

                # 解析 JSON 响应（使用 try_parse_json 四策略解析，替代旧正则）
                verdict_data = self._parse_agent_verdict_v2(response_text, try_parse_json)
                stock.agent_verdict = verdict_data.get("verdict", "warn")
                stock.agent_notes = verdict_data.get("reasoning", "")

                # reject → 降级信号（保留但标注）
                if stock.agent_verdict == "reject":
                    stock.agent_decision_signal = "demoted"
                    logger.info(
                        f"{shadow_tag}[T1终审] {stock.code} {stock.name}: REJECT — {stock.agent_notes}"
                    )

            except Exception as exc:
                logger.warning(f"[T1终审] {stock.code} 审查异常: {exc}")
                stock.agent_verdict = "warn"
                stock.agent_notes = f"审查异常: {str(exc)[:30]}"

        # 影子模式：跑审查但不执行降级（reject 不移到末位）
        if AGENT_STOCK_REVIEW_SHADOW:
            logger.info(
                f"[SHADOW][T1终审] 影子模式: 审查完成但不执行降级，"
                f"reject 数量={sum(1 for s in review_targets if s.agent_verdict == 'reject')}"
            )
            return finalists

        # reject 的候选移到末位（不删除）
        confirmed = [s for s in review_targets if s.agent_verdict != "reject"]
        rejected = [s for s in review_targets if s.agent_verdict == "reject"]
        remaining = finalists[AGENT_STOCK_REVIEW_MAX:]
        finalists = confirmed + remaining + rejected

        logger.info(
            f"[T1终审] 完成: {len(confirmed)} confirm/warn, "
            f"{len(rejected)} reject（降级到末位）"
        )
        return finalists

    @staticmethod
    def _parse_agent_verdict_v2(response_text: str, try_parse_json) -> Dict:
        """解析 Agent 终审 JSON 响应（v2: 使用 try_parse_json 四策略解析）

        §15.3 改造：替代旧版正则 \\{[^}]+\\}（不支持嵌套 JSON）
        """
        data = try_parse_json(response_text)
        if not data or not isinstance(data, dict):
            return {"verdict": "warn", "reasoning": "JSON 解析失败"}

        verdict = data.get("verdict", "warn")
        if verdict not in ("confirm", "warn", "reject"):
            verdict = "warn"

        return {
            "verdict": verdict,
            "reasoning": str(data.get("reasoning", ""))[:60],
            "confidence": data.get("confidence"),
            "risk_flags": data.get("risk_flags", []),
        }

    def _score_capital(
        self, candidates: List[Dict], capital_ctx: dict
    ) -> Tuple[Dict[str, float], Dict[str, bool]]:
        """资金面评分（15% 权重）

        评分维度（v2 重构，砍掉无法获取的维度）：
        - DDE 大单净量百分位: 0-50分（问财初筛已包含）
        - 板块资金流百分位: 0-50分（Node 3 sector_flow 数据）
        总计: 0-100分

        Returns:
            (scores, availability): 评分字典 + 可用性字典
            availability[code]=True 表示该股票有真实资金数据（DDE 或板块资金流命中）
        """
        # ── 维度1: DDE 大单净量百分位 (0-50分) ──
        # 审核修复 2.2：主线候选用单日 DDE，ambush 候选用 5 日累计 DDE（与问财初筛口径一致），
        # 两套口径分组内各自百分位，避免量纲混排；仅初筛降级票（已打 dde_degraded）
        # 允许回退单日并聚合告警，未打标缺值 fail-fast（见下）
        dde_data_main: Dict[str, float] = {}
        dde_data_ambush: Dict[str, float] = {}
        degraded_ambush: List[str] = []
        for c in candidates:
            if c.get("sector_type") == "ambush":
                val_5d = c.get("dde_5d_sum") or 0
                if val_5d != 0:
                    dde_data_ambush[c["code"]] = val_5d
                    continue
                # 唯一合法的 5 日值缺失路径：问财初筛 DDE 轮全空的 W10 降级票
                # （初筛第 2 轮查询不带 5 日条件，进 _score_capital 前已打 dde_degraded=True）。
                # 正常 ambush 初筛请求「近5日合计>0」条件 + 5日列，返回值恒非零；
                # 未打标却缺值 = 解析链路契约破坏（如 2026-10-12 问财列名漂移导致
                # 全部 5 日值静默落 0，埋伏组空转、吸筹票被单日爆发口径错杀）。
                # fail-fast 而非静默回退：错误口径的分数比崩溃更具误导性。
                if not c.get("dde_degraded"):
                    raise ValueError(
                        f"ambush 候选 {c['code']} 缺 5 日 DDE 值且未标记 dde_degraded："
                        f"问财解析与 ambush 查询口径契约破坏（应请求'近5个交易日的dde大单净量合计'"
                        f"列且值非零），拒绝以单日口径静默回退评分"
                    )
                degraded_ambush.append(c["code"])
            dde = c.get("dde_net_pct", 0)
            if dde != 0:
                dde_data_main[c["code"]] = dde

        if degraded_ambush:
            logger.warning(
                f"[资金面-DDE] {len(degraded_ambush)} 只 ambush 候选 5 日口径缺失"
                f"（初筛降级票），回退单日组: {', '.join(degraded_ambush)}"
            )

        if dde_data_main or dde_data_ambush:
            range_desc = ""
            if dde_data_main:
                range_desc = (
                    f", 单日范围 {min(dde_data_main.values()):.2f}%"
                    f"~{max(dde_data_main.values()):.2f}%"
                )
            logger.info(
                f"[资金面-DDE] {len(dde_data_main) + len(dde_data_ambush)} 只"
                f"（单日口径 {len(dde_data_main)} + ambush 5日口径 {len(dde_data_ambush)}）"
                f"{range_desc}"
            )
        else:
            logger.warning("[资金面-DDE] 候选股无 DDE 数据")

        main_sorted = sorted(dde_data_main.values(), reverse=True)
        ambush_sorted = sorted(dde_data_ambush.values(), reverse=True)

        def _percentile_desc(sorted_vals: List[float], val: float) -> Optional[float]:
            """降序百分位（审核修复 2.3：并列取最优名次=严格大于计数，两维度口径统一）；
            样本 <2 时返回 None（孤样本不计分，避免退化满分）"""
            n = len(sorted_vals)
            if n < 2:
                return None
            rank = sum(1 for v in sorted_vals if v > val)
            return 1.0 - rank / (n - 1)

        # ── 维度2: 板块资金流 (0-50分) ──
        # 从 sector_flow 解析板块名→净流入金额（亿）
        sector_flow_map = self._parse_sector_flow(capital_ctx)

        if sector_flow_map:
            n_flows = len(sector_flow_map)
            # 数据源标记：sector_flow_all（~300板块）vs top_concepts（~8板块）
            source_label = "sector_flow_all" if n_flows > 10 else "top_concepts"
            logger.info(
                f"[资金面-板块] 数据源={source_label}, {n_flows} 个板块, "
                f"范围 {min(sector_flow_map.values()):.1f}~{max(sector_flow_map.values()):.1f}亿"
            )
            if n_flows <= 10:
                logger.warning(
                    f"[资金面-板块] sector_flow_all 不可用，仅 {n_flows} 个板块参与排名，"
                    f"百分位区分度不足（参考 Briere & Szafarz 2021: 窄截面排名退化）"
                )
        else:
            logger.warning("[资金面-板块] 无 sector_flow 数据")

        # 为每个候选股匹配其所属板块的资金流（包含负值——净流出也是有效数据）
        # 审核修复 3.1：精确键 → 别名变体 → 归一化键三级匹配，消除同花顺小概念
        # （如红黄酒）因键名口径不一致而静默丢 50 分；未命中聚合告警
        from src.got.board_aliases import normalize_sector_name, sector_name_variants
        norm_flow_map: Dict[str, float] = {}
        for k, v in sector_flow_map.items():
            norm_flow_map.setdefault(normalize_sector_name(k), v)

        sector_flow_by_stock = {}
        unmatched_sectors = set()
        for c in candidates:
            sector = c.get("sector", "")
            if not sector:
                continue
            flow = sector_flow_map.get(sector)
            if flow is None:
                flow = next(
                    (sector_flow_map[v] for v in sector_name_variants(sector)
                     if v in sector_flow_map),
                    None,
                )
            if flow is None:
                flow = norm_flow_map.get(normalize_sector_name(sector))
            if flow is not None:
                sector_flow_by_stock[c["code"]] = flow
            else:
                unmatched_sectors.add(sector)
        if unmatched_sectors and sector_flow_map:
            logger.warning(
                f"[资金面-板块] {len(unmatched_sectors)} 个板块无资金流数据"
                f"（sector_flow_all 未收录且别名/归一化未命中，板块维度 0 分）: "
                f"{'、'.join(sorted(unmatched_sectors))}"
            )

        # 板块资金流百分位：基于全量板块排名（而非仅候选股之间）
        all_flows = sorted(sector_flow_map.values(), reverse=True) if sector_flow_map else []

        # ── 综合评分 ──
        scores = {}
        availability = {}
        for c in candidates:
            score = 0.0
            has_data = False
            code = c["code"]

            # DDE 百分位 (0-50)：ambush/主线各自口径分组内排名（审核修复 2.2）
            if code in dde_data_ambush:
                pct = _percentile_desc(ambush_sorted, dde_data_ambush[code])
            elif code in dde_data_main:
                pct = _percentile_desc(main_sorted, dde_data_main[code])
            else:
                pct = None
            if pct is not None:
                score += 50 * pct
                has_data = True

            # 板块资金流百分位 (0-50)：在全量板块中的排名位置（tie 口径与 DDE 统一）
            if code in sector_flow_by_stock and all_flows:
                pct = _percentile_desc(all_flows, sector_flow_by_stock[code])
                if pct is not None:
                    score += 50 * pct
                    has_data = True

            scores[code] = round(score, 1)
            availability[code] = has_data

        return scores, availability

    @staticmethod
    def _apply_capital_floor(
        top_pool: List[Dict], dropped_pool: List[Dict], candidates: List[Dict],
    ) -> List[Dict]:
        """审核修复 2.1：轮次 2 Tier A 主线保底。

        Node2 用技术面选主线，强度≥90 的主线板块不得被资金面 Top20
        整板块歼灭（红黄酒 0910-0924 全灭教训）；每板块按 capital_score
        降序补回 1 只（dropped_pool 已按轮次 2 排序键降序，首命中即最优），
        总量封顶 5 只防池膨胀；补回票打标 capital_floor_restored=True，
        不豁免后续轮次 3/4/5。
        """
        TIER_A_STRENGTH = 90.0
        CAPITAL_FLOOR_MAX = 5
        top_sectors = {c.get("sector") for c in top_pool}
        top_codes = {c["code"] for c in top_pool}
        tier_a_by_sector: Dict[str, float] = {}
        for c in candidates:
            if (
                c.get("sector_type") == "active_mainline"
                and c.get("sector")
                and (c.get("sector_score") or 0) >= TIER_A_STRENGTH
            ):
                tier_a_by_sector[c["sector"]] = max(
                    tier_a_by_sector.get(c["sector"], 0.0), c.get("sector_score") or 0.0
                )
        restored_floor = []
        for sector in sorted(tier_a_by_sector, key=tier_a_by_sector.get, reverse=True):
            if sector in top_sectors or len(restored_floor) >= CAPITAL_FLOOR_MAX:
                continue
            pick = next(
                (c for c in dropped_pool
                 if c.get("sector") == sector and c["code"] not in top_codes),
                None,
            )
            if pick:
                pick["capital_floor_restored"] = True
                restored_floor.append(pick)
        if restored_floor:
            restored_desc = ", ".join(
                f"{c.get('code')}({c.get('sector')})" for c in restored_floor
            )
            logger.info(
                f"轮次 2: {len(top_pool) + len(restored_floor)} 只（资金面 Top 20，"
                f"Tier A 主线保底补回 {len(restored_floor)} 只: {restored_desc}）"
            )
            return top_pool + restored_floor
        logger.info(f"轮次 2: {len(top_pool)} 只（资金面 Top 20）")
        return top_pool

    @staticmethod
    def _parse_sector_flow(capital_ctx: dict) -> Dict[str, float]:
        """从 Node 3 的 sector_flow 解析板块名→净流入金额（亿）

        优先使用全量映射 sector_flow_all（~300 概念 + ~41 行业），
        回退到 top_concepts/top_industries（仅 top 5+3）。
        """
        import re
        sector_flow = capital_ctx.get("evidence", {}).get("sector_flow")
        if not sector_flow:
            return {}

        # 优先全量映射（Node 3 存储的 ~300 概念 + ~41 行业实时数据）
        all_map = sector_flow.get("sector_flow_all")
        if all_map and isinstance(all_map, dict) and len(all_map) > 10:
            return all_map

        # 回退：从 top_concepts/top_industries 字符串解析
        result = {}
        pattern = re.compile(r"^(.+?)\(([\d.]+)亿\)$")

        for entry_list in [sector_flow.get("top_concepts", []), sector_flow.get("top_industries", [])]:
            if not isinstance(entry_list, list):
                continue
            for entry in entry_list:
                m = pattern.match(entry)
                if m:
                    board_name = m.group(1).strip()
                    amount = float(m.group(2))
                    # 同一板块可能在 concepts 和 industries 都出现，取最大值
                    result[board_name] = max(result.get(board_name, 0), amount)

        return result

    def _get_all_sector_names(self, sector_context: dict) -> set:
        """构建完整板块名集合（TDX_BOARD_REGISTRY + sector.json 动态板块）

        确保候选股 sector 值无论来自 registry 还是动态发现都能被匹配到。
        """
        from data_provider.tdx_board_mapping import TDX_BOARD_REGISTRY

        # 基础：TDX 注册表（~85 个硬编码板块）
        names = {info["name"] for info in TDX_BOARD_REGISTRY.values()}

        # 兜底：从当天 sector.json 提取所有板块名（覆盖动态发现的板块）
        for key in ("active_mainline", "ambush", "top_by_score"):
            items = sector_context.get(key, [])
            for item in items:
                # active_mainline 是 MainlineGroup 格式，需展开 members
                members = item.get("members", [])
                if members:
                    for m in members:
                        if m.get("name"):
                            names.add(m["name"])
                elif item.get("name"):
                    names.add(item["name"])

        return names

    def _score_news(
        self, candidates: List[Dict], news_ctx: dict, sector_context: dict = None
    ) -> Tuple[Dict[str, float], Dict[str, bool]]:
        """消息面评分（15% 权重）— LLM 语义匹配版

        流程：
        1. 将 Node 4 事件 + 板块名列表（TDX_REGISTRY + 动态板块）交给 LLM
        2. LLM 输出：每个受影响板块的 sentiment × relevance
        3. 候选股 sector 精确匹配 LLM 输出的板块名 → 评分
        4. LLM 失败时降级到子串匹配

        Returns:
            (scores, availability): 评分字典 + 可用性字典
        """
        events = news_ctx.get("major_events", [])
        if not events:
            logger.warning("[消息面] 无新闻事件，所有候选股消息面不可用")
            return {c["code"]: 40.0 for c in candidates}, {c["code"]: False for c in candidates}

        # 构建完整板块名集合
        all_sector_names = self._get_all_sector_names(sector_context or {})

        # 尝试 LLM 语义匹配
        sector_news_map = self._llm_score_sector_news(events, all_sector_names)

        if sector_news_map:
            logger.info(f"[消息面-LLM] {len(sector_news_map)} 个板块获匹配")
        else:
            logger.warning("[消息面-LLM] LLM 匹配失败，降级到子串匹配")
            sector_news_map = self._fallback_sector_match(events, all_sector_names)

        # 评分：基础分 40 + 板块情感分 × 60
        # 有事件即视为可用（无匹配给中性基础分 40，参与加权）
        has_events = bool(events)
        scores = {}
        availability = {}
        for c in candidates:
            sector = c.get("sector", "")
            news_signal = sector_news_map.get(sector, 0.0)
            score = 40.0 + news_signal * 60.0
            scores[c["code"]] = max(0.0, min(100.0, score))
            availability[c["code"]] = has_events

        return scores, availability

    def _llm_score_sector_news(self, events: list, all_sector_names: set) -> Dict[str, float]:
        """调用 LLM 将新闻事件映射到具体板块的情感分

        Args:
            events: Node 4 新闻事件列表
            all_sector_names: 合法板块名集合（TDX_REGISTRY + 动态板块）

        Returns:
            {板块名: 加权情感分}，范围 [-1, +1]
        """
        from src.agent.llm_adapter import LLMToolAdapter

        board_list = sorted(all_sector_names)

        if not board_list:
            logger.warning("[消息面-LLM] 板块名列表为空")
            return {}

        # 构建事件摘要
        event_lines = []
        for i, ev in enumerate(events):
            title = ev.get("title", "")
            summary = ev.get("summary", "")[:200]
            sentiment = ev.get("sentiment", "neutral")
            impact = ev.get("impact_level", "medium")
            event_lines.append(f"{i+1}. [{impact}/{sentiment}] {title}\n   {summary}")
        events_text = "\n".join(event_lines)
        boards_text = ", ".join(board_list)

        prompt = f"""你是一个A股市场分析专家。给定以下新闻事件和合法板块名列表，判断每个事件影响了哪些板块。

## 新闻事件
{events_text}

## 合法板块名列表（只能从以下名称中选择）
{boards_text}

## 输出要求
输出 JSON 数组，每个元素表示一个受影响的板块：
[{{"sector": "板块名", "relevance": 0.8, "sentiment": "positive"}}, ...]

规则：
- sector 必须是上方合法板块名列表中的精确名称
- relevance: 0-1，事件对该板块的影响程度
- sentiment: positive / negative / neutral
- 一个事件可影响多个板块，不同事件可影响同一板块（取最强影响）
- 只输出 relevance >= 0.3 的板块
- 只输出 JSON 数组，不要其他文字"""

        try:
            adapter = LLMToolAdapter(self.config)
            response = adapter.call_text(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=3000,
                timeout=60,
            )

            if not response or not response.content:
                logger.warning("[消息面-LLM] LLM 返回空")
                return {}

            # 解析 JSON
            import re
            match = re.search(r'\[[\s\S]*\]', response.content)
            if not match:
                logger.warning("[消息面-LLM] 无法提取 JSON 数组")
                return {}

            items = json.loads(match.group())
            if not isinstance(items, list):
                return {}

            # 聚合：同一板块取最强影响
            sector_scores: Dict[str, float] = {}
            sentiment_multiplier = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}

            for item in items:
                if not isinstance(item, dict):
                    continue
                sector = str(item.get("sector", "")).strip()
                relevance = float(item.get("relevance", 0))
                sentiment = str(item.get("sentiment", "neutral")).strip()

                if sector not in all_sector_names or relevance < 0.3:
                    continue

                signal = relevance * sentiment_multiplier.get(sentiment, 0)
                # 同一板块多事件取绝对值最大的
                if abs(signal) > abs(sector_scores.get(sector, 0)):
                    sector_scores[sector] = signal

            logger.info(f"[消息面-LLM] 解析成功: {len(sector_scores)} 个板块")
            return sector_scores

        except Exception as e:
            logger.warning(f"[消息面-LLM] LLM 调用失败: {e}")
            return {}

    def _fallback_sector_match(self, events: list, all_sector_names: set) -> Dict[str, float]:
        """降级方案：子串匹配 affected_sectors → 板块名

        Args:
            events: Node 4 新闻事件列表
            all_sector_names: 合法板块名集合（TDX_REGISTRY + 动态板块）
        """
        sector_scores: Dict[str, float] = {}
        sentiment_multiplier = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}

        for ev in events:
            affected = ev.get("affected_sectors", [])
            sentiment = ev.get("sentiment", "neutral")
            impact = ev.get("impact_level", "medium")

            base = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(impact, 0.3)
            signal = base * sentiment_multiplier.get(sentiment, 0)

            # 子串匹配：affected_sector 与板块名双向包含即命中
            matched_boards = set()
            for a in affected:
                for board in all_sector_names:
                    if a in board or board in a:
                        matched_boards.add(board)

            for board in matched_boards:
                if abs(signal) > abs(sector_scores.get(board, 0)):
                    sector_scores[board] = signal

        return sector_scores

    def _score_technical_wave(self, candidates: List[Dict], fetcher_manager) -> Dict[str, float]:
        """波段技术指标评分（15% 权重）— 连续评分版

        所有维度改为连续评分（消除二元判定的分辨率损失）：
        - MA20 趋势: 20分（偏离度 12 + 斜率 8）
        - 布林带位置: 15分（带状位置连续映射）
        - RSI12: 10分（50-70 最优，分段连续）
        - 20日突破: 10分（突破程度连续）
        - 量比: 10分（1.5-3.0 最优，连续）
        - 换手率: 10分（3%-8% 最优，连续）
        - MACD 状态: 10分（金叉/死叉/方向 + DIF 力度）
        - 均线排列: 15分（MA 间距连续）
        """
        scores = {}

        for c in candidates:
            try:
                df, _ = fetcher_manager.get_daily_data(c["code"], days=60)
                if df is None or len(df) < 20:
                    scores[c["code"]] = 0
                    continue

                close = df["close"]
                score = 0.0

                # ── 1. MA20 趋势（20分：偏离度 12 + 斜率 8）──
                ma20 = close.rolling(20).mean()
                ma20_now = ma20.iloc[-1]
                ma20_5d = ma20.iloc[-5]
                if ma20_now > 0:
                    gap = (close.iloc[-1] - ma20_now) / ma20_now
                    slope = (ma20_now - ma20_5d) / ma20_now
                    if gap > 0:
                        score += min(12.0, gap * 300)
                    if slope > 0:
                        score += min(8.0, slope * 200)

                # ── 2. 布林带位置（15分）──
                bb_mid = ma20_now
                bb_std = close.rolling(20).std().iloc[-1]
                bb_upper = bb_mid + 2 * bb_std
                bb_lower = bb_mid - 2 * bb_std
                if bb_upper > bb_lower:
                    boll_pos = (close.iloc[-1] - bb_lower) / (bb_upper - bb_lower)
                    # 最优区间 0.5~0.85，两端衰减
                    if boll_pos < 0:
                        score += 3  # 超卖反弹潜力（Ni et al. 2020: 触下轨逆向策略正超额收益）
                    elif boll_pos < 0.3:
                        score += boll_pos / 0.3 * 7
                    elif boll_pos <= 0.85:
                        score += 7 + (boll_pos - 0.3) / 0.55 * 8
                    elif boll_pos <= 1.0:
                        score += 15 - (boll_pos - 0.85) / 0.15 * 5
                    else:
                        score += 10  # 上轨以上，略扣

                # ── 3. RSI12（10分：50-70 最优）──
                rsi12 = self._calc_rsi(close, 12)
                if rsi12 < 30:
                    score += rsi12 / 30 * 3
                elif rsi12 < 50:
                    score += 3 + (rsi12 - 30) / 20 * 5
                elif rsi12 <= 70:
                    score += 8 + (rsi12 - 50) / 20 * 2
                elif rsi12 <= 85:
                    score += max(0, 10 - (rsi12 - 70) / 15 * 6)
                else:
                    score += 2  # 严重超买

                # ── 4. 20日突破（10分：突破程度连续）──
                high_20d_prev = df["high"].rolling(20).max().iloc[-2]
                if high_20d_prev > 0:
                    breakout_ratio = (close.iloc[-1] - high_20d_prev) / high_20d_prev
                    if breakout_ratio > 0:
                        score += min(10.0, 5.0 + breakout_ratio * 200)
                    else:
                        # 未突破但接近高点也给分
                        score += max(0, 5.0 + breakout_ratio * 100)

                # ── 5. 量比（10分：1.5-3.0 最优）──
                vol_ma5 = df["volume"].rolling(5).mean().iloc[-2]
                if vol_ma5 > 0:
                    volume_ratio = df["volume"].iloc[-1] / vol_ma5
                    if volume_ratio < 0.5:
                        score += 0
                    elif volume_ratio < 1.0:
                        score += (volume_ratio - 0.5) / 0.5 * 3
                    elif volume_ratio <= 3.0:
                        score += 3 + (volume_ratio - 1.0) / 2.0 * 7
                    elif volume_ratio <= 5.0:
                        score += max(0, 10 - (volume_ratio - 3.0) / 2.0 * 5)
                    else:
                        score += 2  # 过度放量

                # ── 6. 换手率（10分：3%-8% 最优）──
                turnover_rate = c.get("turnover_rate", 0) or 0
                if turnover_rate <= 0:
                    score += 0
                elif turnover_rate < 0.03:
                    score += turnover_rate / 0.03 * 4
                elif turnover_rate <= 0.08:
                    score += 4 + (turnover_rate - 0.03) / 0.05 * 6
                elif turnover_rate <= 0.15:
                    score += max(0, 10 - (turnover_rate - 0.08) / 0.07 * 5)
                else:
                    score += 2  # 过度换手

                # ── 7. MACD 状态（10分）──
                macd_line, signal_line, hist = self._calc_macd(close)
                macd_now = macd_line.iloc[-1]
                signal_now = signal_line.iloc[-1]
                macd_prev = macd_line.iloc[-2]
                signal_prev = signal_line.iloc[-2]
                # 金叉/死叉基础分
                if macd_now > signal_now:
                    score += 6  # 多头状态
                    # 刚金叉加分
                    if macd_prev <= signal_prev:
                        score += 4
                    else:
                        # DIF 力度（持续放大）
                        if hist is not None and len(hist) >= 2:
                            hist_now = hist.iloc[-1]
                            hist_prev = hist.iloc[-2]
                            if hist_now > hist_prev > 0:
                                score += 3
                            elif hist_now > 0:
                                score += 1
                else:
                    # 空头状态
                    if macd_prev >= signal_prev:
                        score += 0  # 刚死叉，最差
                    else:
                        score += 2  # 持续空头但未恶化

                # ── 8. 均线多头排列（15分：间距连续）──
                ma5 = close.rolling(5).mean().iloc[-1]
                ma10 = close.rolling(10).mean().iloc[-1]
                ma20_val = ma20_now
                if ma5 > ma10 > ma20_val and ma20_val > 0:
                    # 多头排列，按间距给分
                    gap_5_10 = (ma5 - ma10) / ma20_val
                    gap_10_20 = (ma10 - ma20_val) / ma20_val
                    score += min(15.0, 8 + (gap_5_10 + gap_10_20) * 200)
                elif ma5 > ma10:
                    score += 4  # 部分多头
                elif ma5 > ma20_val:
                    score += 2

                scores[c["code"]] = min(100.0, round(score, 1))
            except Exception as e:
                logger.warning(f"计算技术指标失败 {c['code']}: {e}")
                scores[c["code"]] = 0

        return scores

    @staticmethod
    def _apply_r4_filter(
        candidates: List[Dict], got_context: dict
    ) -> List[Dict]:
        """R4 风险排雷：主通道 Top 6 + 软保底至多 2（v2.1）

        主通道门槛 >50，保底门槛 >40，仅对 actionable 板块提供保底。
        actionable_sectors 从 got_context 内部派生（双格式兼容 + 板块名清洗）。
        """
        from src.services.board_name_resolver import clean_board_name

        R4_MAIN_TOP_K = 6
        R4_MAIN_FLOOR = 50
        R4_GUARANTEE_MAX = 2
        R4_GUARANTEE_FLOOR = 35  # v2.1 修订：40→35，为层3新维度叠加留余量

        # 主通道：>50 + Top 6
        main_candidates = [c for c in candidates if c["risk_score_safe"] > R4_MAIN_FLOOR]
        main_candidates.sort(key=lambda x: x["risk_score_safe"], reverse=True)
        selected = main_candidates[:R4_MAIN_TOP_K]
        selected_sectors = {c["sector"] for c in selected}

        # 保底通道：派生 actionable_sectors（双格式兼容 + 清洗）
        sector_ctx = got_context.get("sector_context", {})
        actionable_sectors = set()
        for g in sector_ctx.get("active_mainline", []):
            if "members" in g:
                for m in g.get("members", []):
                    actionable_sectors.add(clean_board_name(m.get("name", "")))
            else:
                actionable_sectors.add(clean_board_name(g.get("name", "")))
        for a in sector_ctx.get("ambush", []):
            actionable_sectors.add(clean_board_name(a.get("name", "")))

        # 保底池：主通道落选(>50) + 40~50 区间，板块未代表
        guarantee_pool = [
            c for c in main_candidates[R4_MAIN_TOP_K:]
            if c["sector"] not in selected_sectors
        ] + [
            c for c in candidates
            if R4_GUARANTEE_FLOOR < c["risk_score_safe"] <= R4_MAIN_FLOOR
            and c["sector"] not in selected_sectors
        ]
        guarantee_pool.sort(key=lambda x: x["risk_score_safe"], reverse=True)

        guarantee_filled = 0
        for c in guarantee_pool:
            if guarantee_filled >= R4_GUARANTEE_MAX:
                break
            if c["sector"] in actionable_sectors:
                selected.append(c)
                selected_sectors.add(c["sector"])
                guarantee_filled += 1
                logger.info(
                    f"R4 软保底: {c['code']} {c.get('name', '')} ({c['sector']}) "
                    f"risk={c['risk_score_safe']:.1f}"
                )

        logger.info(
            f"轮次 4 明细: 主通道 {min(len(main_candidates), R4_MAIN_TOP_K)}"
            f" + 保底 {guarantee_filled} = {len(selected)} 只"
        )
        return selected

    def _score_risk(self, candidates: List[Dict], fetcher_manager) -> Dict[str, float]:
        """风险评分（15% 权重，越高越安全）

        扣分项（v2.1 梯度化）：
        - RSI6: >85 -30 / >80 -20 / >75 -10
        - 近 5 日涨幅: >25% -25 / >20% -18 / >15% -10 / >10% -5
        - 波动率(百分点): >8 -15 / >5 -8（已合入）
        - 换手率衰减(新增): 近3日/前7日 <0.5 → -10（缩量上涨动能衰竭）
        - MA20 偏离度(新增): |偏离| >15% -15 / >10% -8 / >7% -3
          （abs 设计：超涨回归和接飞刀都是买方风险）

        注：PE 估值检查已移除（需逐只拉行情，push2 被封时耗时 5s/只）。
        """
        scores = {}

        for c in candidates:
            score = 100  # 基础分

            try:
                df, _ = fetcher_manager.get_daily_data(c["code"], days=30)
                if df is None or len(df) < 5:
                    scores[c["code"]] = 50  # 数据不足给中间分
                    continue

                # 1. RSI 超买（梯度化）
                if len(df) >= 12:
                    rsi6 = self._calc_rsi(df["close"], 6)
                    if rsi6 > 85:
                        score -= 30   # 极端超买
                    elif rsi6 > 80:
                        score -= 20   # 超买
                    elif rsi6 > 75:
                        score -= 10   # 轻度超买

                # 2. 近 5 日涨幅（梯度化）
                pct_5d = (df["close"].iloc[-1] / df["close"].iloc[-5] - 1) * 100
                if pct_5d > 25:
                    score -= 25       # 暴涨
                elif pct_5d > 20:
                    score -= 18
                elif pct_5d > 15:
                    score -= 10
                elif pct_5d > 10:
                    score -= 5

                # 3. 波动率（已合入，不动）
                if len(df) >= 20 and "pct_chg" in df.columns:
                    volatility = df["pct_chg"].rolling(20).std().iloc[-1]
                    if volatility > 8:
                        score -= 15   # 极端波动
                    elif volatility > 5:
                        score -= 8    # 高波动

                # 4. 换手率衰减（新增）
                if len(df) >= 10 and "volume" in df.columns:
                    vol_recent = df["volume"].iloc[-3:].mean()
                    vol_past = df["volume"].iloc[-10:-3].mean()
                    if vol_past > 0:
                        vol_ratio = vol_recent / vol_past
                        if vol_ratio < 0.5:
                            score -= 10  # 缩量上涨，动能衰竭

                # 5. MA20 偏离度（新增）
                # abs() 设计：超涨回归（正偏离）和接飞刀（负偏离）都是买方风险
                if len(df) >= 20:
                    ma20 = df["close"].rolling(20).mean().iloc[-1]
                    if ma20 > 0:
                        deviation = (df["close"].iloc[-1] / ma20 - 1) * 100
                        if abs(deviation) > 15:
                            score -= 15
                        elif abs(deviation) > 10:
                            score -= 8
                        elif abs(deviation) > 7:
                            score -= 3

                # 6. 当日涨幅惩罚（P0-3 新增）
                # 大涨后追高风险：创业板涨8%+、主板涨8%+ 风险极高
                change_pct = c.get("change_pct", 0) or 0
                if change_pct >= 8.0:
                    score -= 20      # 大涨追高风险
                elif change_pct >= 5.0:
                    score -= 10      # 中度上涨追高风险

                # 7. 板块过热惩罚（v2 过热检测升级，Phase 3 联动）
                overheat_sev = c.get("sector_overheat_severity", 0) or 0
                if overheat_sev > 0.5:
                    # exhausted 票在轮次 1 已排除，能走到这里的都是保底放回票（_overheat_fallback）
                    # 重扣压制，保证"放回但不冒头"（计划 §5.4）
                    score -= 25
                    if c.get("_overheat_fallback"):
                        logger.info(
                            f"[过热保底] {c['code']} {c.get('name','')} ({c['sector']}) "
                            f"severity={overheat_sev:.2f} 保底放回 -25 压制"
                        )
                elif 0.3 < overheat_sev <= 0.5:
                    score -= 15      # 过热警告：板块高潮风险
                    logger.debug(f"[过热警告] {c['code']} {c.get('name','')} ({c['sector']}) severity={overheat_sev:.2f}")

                # 8. 板块生命周期 mature 惩罚（软过滤，防追高成熟期板块）
                lifecycle_phase = c.get("sector_lifecycle_phase", "")
                if lifecycle_phase == "mature":
                    score -= 10      # 成熟期板块：上涨空间有限
                elif lifecycle_phase == "late_stage":
                    score -= 20      # 晚期板块：即将衰竭

                scores[c["code"]] = max(0, score)
            except Exception as e:
                logger.warning(f"计算风险评分失败 {c['code']}: {e}")
                scores[c["code"]] = 50

        return scores

    # ============================================================
    # P5 催化剂关联度重排
    # ============================================================

    def _score_catalyst_relevance(
        self, candidates: List[Dict], got_context: dict
    ) -> Tuple[Dict[str, float], Dict[str, dict]]:
        """催化剂关联度评分（P5）

        消费 catalyst_calendar，为每只候选股计算催化剂关联度分数。

        评分逻辑：
        1. 个股级事件（earnings/lockup）→ title 含股票代码 → 当事股直接置顶 (score=100)
        2. 板块级事件（narrative/global/macro）→ sector 匹配 → score = confidence × impact × proximity × 50
        3. 无事件匹配 → score=0

        重排规则：
        - 仅对 ambush 板块候选生效
        - 最大 confidence < 0.7 → 不重排（保持技术排序）

        Returns:
            (scores, details): 评分字典 + 催化剂详情字典
        """
        from src.got.catalyst_config import CATALYST_MACRO_AS_REGIME_REF
        import re

        # ── 加载催化剂日历 ──
        news_ctx = got_context.get("news_context", {})
        calendar_data = news_ctx.get("catalyst_calendar")
        if not calendar_data:
            logger.info("[P5催化剂] 无催化剂日历数据，跳过重排")
            return {}, {}

        events = calendar_data.get("events", [])

        # 价值分级（2026-10-21）：例行宏观数据不参与 P5 催化剂重排
        if CATALYST_MACRO_AS_REGIME_REF:
            events = [evt for evt in events if evt.get("source") != "macro_rule"]
        if not events:
            logger.info("[P5催化剂] 催化剂日历无事件，跳过重排")
            return {}, {}

        logger.info(f"[P5催化剂] 加载 {len(events)} 个催化剂事件")

        # ── 提取个股级事件中的股票代码 → event 映射 ──
        stock_code_pattern = re.compile(r'(\d{6})')
        individual_events: Dict[str, List[dict]] = {}  # stock_code → [events]
        sector_events: Dict[str, List[dict]] = {}      # sector_name → [events]

        from datetime import date as date_cls
        today = date_cls.today()

        for evt in events:
            event_type = evt.get("event_type", "")
            confidence = float(evt.get("confidence", 0))
            sector = evt.get("sector", "")
            title = evt.get("title", "")

            # 低置信事件不参与评分（A3: 统一引用 CATALYST_CONFIDENCE_GATE）
            from src.got.catalyst_config import CATALYST_CONFIDENCE_GATE
            if confidence < CATALYST_CONFIDENCE_GATE:
                continue

            # 个股级事件：从 title 提取股票代码
            if event_type in ("earnings", "lockup"):
                codes = stock_code_pattern.findall(title)
                for code in codes:
                    individual_events.setdefault(code, []).append(evt)

            # 板块级事件：按 sector 分组（A2 归一化 + T5 扇出；归一化后去重防重复计分）
            elif sector:
                from src.got.board_aliases import normalize_sector_name
                # T5: 若事件有多板块扇出，进入多个桶；
                # 归一化后去重，避免别名变体使同一事件重复进同一桶（取 max 时无放大但冗余）
                affected = evt.get("affected_sectors") or [sector]
                seen_keys = set()
                for s in affected:
                    normalized_sector = normalize_sector_name(s)
                    if normalized_sector in seen_keys:
                        continue
                    seen_keys.add(normalized_sector)
                    sector_events.setdefault(normalized_sector, []).append(evt)

        # ── 为每只候选股评分 ──
        scores: Dict[str, float] = {}
        details: Dict[str, dict] = {}

        for c in candidates:
            code = c["code"]
            sector = c.get("sector", "")
            sector_type = c.get("sector_type", "")

            best_score = 0.0
            best_event: dict = {}

            # 1. 个股级事件 → 直接置顶（跳过利空事件）
            if code in individual_events:
                for evt in individual_events[code]:
                    direction = evt.get("direction", "positive")
                    if direction == "negative":
                        continue  # 利空个股事件（如限售解禁）不置顶
                    # 个股级事件 score = 100（最高优先级）
                    if 100 > best_score:
                        best_score = 100.0
                        best_event = evt
                # 如果有个股级事件，跳过板块级事件（个股级优先级最高）
                # 注：多个板块共享同一事件（如 SK 海力士→存储芯片/先进封装/PCB）时，
                # 每只候选股只匹配自己所属 sector 的事件，取 max 去重，不会重复计分。

            # 2. 板块级事件 → confidence × impact × proximity × 50
            # A2: 归一化查找（精确 → 别名变体）
            matched_events = []
            if best_score < 100:
                matched_events = sector_events.get(sector, [])
                if not matched_events:
                    from src.got.board_aliases import sector_name_variants
                    for variant in sector_name_variants(sector):
                        if variant == sector:
                            continue
                        matched_events = sector_events.get(variant, [])
                        if matched_events:
                            break
            if best_score < 100 and matched_events:
                for evt in matched_events:
                    confidence = float(evt.get("confidence", 0))
                    impact = int(evt.get("impact_level", 1))

                    # 临近度系数：事件日距今越近越重要
                    try:
                        evt_date_str = evt.get("event_date", "")
                        if evt_date_str:
                            evt_date = date_cls.fromisoformat(str(evt_date_str)[:10])
                            days_diff = abs((evt_date - today).days)
                            proximity = max(0.3, 1.0 - days_diff / 30.0)
                        else:
                            proximity = 0.5
                    except (ValueError, TypeError):
                        proximity = 0.5

                    # 方向修正：negative 事件降权（利空不置顶）
                    direction = evt.get("direction", "positive")
                    direction_coeff = 1.0 if direction == "positive" else 0.5

                    evt_score = confidence * impact * proximity * direction_coeff * 50
                    # 板块级封顶 99：严格低于个股级 100，配合 L2287 `if best_score < 100`
                    # 保证优先级不变量：个股级事件（earnings）永远优先于板块级事件（narrative）
                    evt_score = min(99.0, evt_score)

                    if evt_score > best_score:
                        best_score = evt_score
                        best_event = evt

            scores[code] = round(best_score, 1)
            if best_event:
                details[code] = {
                    "event_title": best_event.get("title", ""),
                    "event_date": str(best_event.get("event_date", "")),
                    "event_type": best_event.get("event_type", ""),
                    "confidence": float(best_event.get("confidence", 0)),
                }

        # ── 统计 ──
        matched = sum(1 for v in scores.values() if v > 0)
        if matched > 0:
            logger.info(f"[P5催化剂] {matched}/{len(candidates)} 只匹配到催化剂事件")

        # A3 影子评估：若门槛降至 0.60 会多放行哪些事件
        shadow_events = [
            evt for evt in events
            if 0.6 <= float(evt.get("confidence", 0)) < CATALYST_CONFIDENCE_GATE
        ]
        if shadow_events:
            shadow_sectors = {evt.get("sector", "?") for evt in shadow_events}
            logger.info(
                f"[P5影子] 门槛 0.60 会多放行 {len(shadow_events)} 个事件 "
                f"({len(shadow_sectors)} 板块): "
                f"{', '.join(list(shadow_sectors)[:5])}"
            )

        return scores, details

    def _compute_got_composite_score(self, stock: ScreenerStock, got_context: dict) -> float:
        """综合评分（满分 100）

        权重分配（5 维度，按数据可靠性分配）：
        - 资金面: 15%（数据常缺失，alpha 弱）
        - 消息面: 15%（依赖外部 API）
        - 板块强度: 25%（mootdx TCP 稳定）
        - 技术面(波段): 25%（K 线永远可用）
        - 风险因子: 20%（波动率永远可算）

        缺失维度跳过 + 权重归一化（NBER Freyberger et al. 2022）。
        """
        scores = {
            "capital": stock.capital_score,
            "news": stock.news_score,
            "sector": stock.sector_score,
            "tech": stock.tech_wave_score,
            "risk": stock.risk_score_safe,
        }
        available = {
            "capital": stock.capital_available,
            "news": stock.news_available,
            "sector": True,   # 板块数据始终可用
            "tech": True,     # K 线始终可用
            "risk": True,     # 波动率始终可算
        }

        composite, _ = weighted_score_with_redistribution(scores, available)
        return composite

    def _generate_got_reasoning(self, stock: ScreenerStock, got_context: dict) -> str:
        """生成推荐理由（Markdown 格式）"""
        # 计算实际权重（考虑缺失维度归一化）
        scores = {
            "capital": stock.capital_score,
            "news": stock.news_score,
            "sector": stock.sector_score,
            "tech": stock.tech_wave_score,
            "risk": stock.risk_score_safe,
        }
        available = {
            "capital": stock.capital_available,
            "news": stock.news_available,
            "sector": True,
            "tech": True,
            "risk": True,
        }
        _, actual_weights = weighted_score_with_redistribution(scores, available)

        def _w(dim: str) -> float:
            return actual_weights.get(dim, 0) * 100

        lines = [
            f"**综合评分**: {stock.score:.1f}/100",
        ]

        # 标注缺失维度
        missing_dims = [k for k, v in available.items() if not v]
        if missing_dims:
            lines.append(f"⚠️ 缺失维度（已跳过归一化）: {', '.join(missing_dims)}")

        lines.extend([
            "",
            "### 核心逻辑",
            f"1. **资金面** ({stock.capital_score * _w('capital') / 100:.1f}/{_w('capital'):.0f}): ",
            f"   - 资金面评分 {stock.capital_score:.0f}/100"
            + ("（无真实数据，已跳过）" if not stock.capital_available else ""),
            f"2. **消息面** ({stock.news_score * _w('news') / 100:.1f}/{_w('news'):.0f}): ",
            f"   - 消息面评分 {stock.news_score:.0f}/100"
            + ("（无事件匹配，已跳过）" if not stock.news_available else ""),
            f"3. **板块强度** ({stock.sector_score * _w('sector') / 100:.1f}/{_w('sector'):.0f}): ",
            f"   - 属于{stock.sector_type}板块「{stock.sector_name}」",
            f"   - 板块强度 {stock.sector_score:.0f} 分",
            f"4. **技术面** ({stock.tech_wave_score * _w('tech') / 100:.1f}/{_w('tech'):.0f}): ",
            f"   - 波段技术指标评分 {stock.tech_wave_score:.0f}/100",
            f"5. **风险** ({stock.risk_score_safe * _w('risk') / 100:.1f}/{_w('risk'):.0f}): ",
            f"   - 安全评分 {stock.risk_score_safe:.0f}/100",
        ])

        # P5 催化剂关联度（仅在有匹配事件时展示）
        if stock.catalyst_relevance_score > 0:
            event_type_label = {
                "earnings": "财报", "lockup": "解禁",
                "narrative": "叙事催化", "global": "全球映射", "macro": "宏观数据",
            }.get(stock.catalyst_event_type, stock.catalyst_event_type)
            lines.extend([
                "",
                f"6. **催化剂关联度** ({stock.catalyst_relevance_score:.0f}/100):",
                f"   - 关联事件: {stock.catalyst_event_title}",
                f"   - 事件类型: {event_type_label}",
                f"   - 事件日期: {stock.catalyst_event_date}",
                f"   - 置信度: {stock.catalyst_confidence:.2f}",
            ])
        else:
            lines.append("")

        lines.extend([
            "",
            "### 交易建议",
        ])

        if stock.backtest_snapshot:
            snapshot = stock.backtest_snapshot
            lines.extend([
                f"- **买入价**: {snapshot['entry_price']:.2f} 元（当前价）",
                f"- **目标价**: {snapshot['target_price']:.2f} 元（+{snapshot['target_pct']:.0f}%）",
                f"- **止损价**: {snapshot['stop_loss_price']:.2f} 元（{snapshot['stop_loss_pct']:.0f}%）",
                f"- **持仓周期**: 10-15 个交易日",
            ])

        return "\n".join(lines)

    def _build_backtest_snapshot(self, stock: ScreenerStock, fetcher_manager) -> Dict[str, Any]:
        """构建回测快照"""
        try:
            quote = fetcher_manager.get_realtime_quote(stock.code)
            if not quote:
                return {}

            entry_price = quote.price
            target_price = entry_price * 1.10  # +10%
            stop_loss_price = entry_price * 0.93  # -7%

            return {
                "entry_date": date.today().isoformat(),
                "entry_price": entry_price,
                "target_price": target_price,
                "target_pct": 10,
                "stop_loss_price": stop_loss_price,
                "stop_loss_pct": -7,
                "exit_date": None,
                "exit_price": None,
                "exit_reason": None,
                "pnl_pct": None,
            }
        except Exception as e:
            logger.warning(f"构建回测快照失败 {stock.code}: {e}")
            return {}

    def _save_got_result(self, result: ScreenerRunResult, got_date: date) -> None:
        """保存 GOT 选股结果到 JSON（用于回测）"""
        try:
            output_dir = Path("data/got_screener")
            output_dir.mkdir(parents=True, exist_ok=True)

            date_str = got_date.strftime("%Y%m%d")
            output_file = output_dir / f"{date_str}.json"

            data = {
                "run_date": result.run_date,
                "run_time": result.run_time,
                "got_date": got_date.isoformat(),
                "strategy_name": result.strategy_name,
                "total_scanned": result.total_scanned,
                "matched_count": result.matched_count,
                "elapsed_seconds": result.elapsed_seconds,
                "funnel_stats": result.funnel_stats,  # 4d/D-3 归零率基线观测（仅留痕）
                "top_stocks": [],
            }

            # 拦点 #14: 埋伏道影子输出（仅当启用时）
            if hasattr(self, '_ambush_lane_shadow') and self._ambush_lane_shadow:
                data["ambush_lane_shadow"] = self._ambush_lane_shadow
            for stock in result.stocks:
                stock_data = {
                    "code": stock.code,
                    "name": stock.name,
                    "score": stock.score,
                    "sector": stock.sector_name,
                    "sector_type": stock.sector_type,
                    "sector_score": stock.sector_score,
                    "capital_score": stock.capital_score,
                    "news_score": stock.news_score,
                    "tech_wave_score": stock.tech_wave_score,
                    "risk_score_safe": stock.risk_score_safe,
                    "reasoning": stock.ai_reasoning,
                    "backtest_snapshot": stock.backtest_snapshot,
                    # §15.3 影子期 verdict 落库：供 2 周后兑现数据对比 reject 命中率（A2 记忆回填数据源）
                    "agent_verdict": getattr(stock, "agent_verdict", ""),
                    "agent_notes": getattr(stock, "agent_notes", ""),
                    "agent_decision_signal": getattr(stock, "agent_decision_signal", ""),
                }
                data["top_stocks"].append(stock_data)

            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info(f"[GOT选股] 已保存结果到 {output_file}")
        except Exception as e:
            logger.error(f"[GOT选股] 保存结果失败: {e}")

    def _get_stock_name(self, code: str, fetcher_manager) -> str:
        """获取股票名称（审核修复 S1：失败路径加 warning，避免裸代码静默混入产物）"""
        try:
            name = fetcher_manager.get_stock_name(code) or code
            if name == code:
                logger.warning(f"[名称缺失] {code} 查名返回空，保留裸代码")
            return name
        except Exception as e:
            logger.warning(f"[名称缺失] {code} 查名异常，保留裸代码: {e}")
            return code

    def _calc_rsi(self, prices, period: int = 14) -> float:
        """计算 RSI 指标"""
        import pandas as pd
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1] if not rsi.empty else 50.0

    def _calc_macd(self, prices, fast: int = 12, slow: int = 26, signal: int = 9):
        """计算 MACD 指标"""
        import pandas as pd
        ema_fast = prices.ewm(span=fast, adjust=False).mean()
        ema_slow = prices.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def format_notification(self, result: ScreenerRunResult) -> str:
        """格式化通知消息"""
        if result.error:
            return f"❌ 选股任务失败: {result.error}"

        if not result.stocks:
            return f"📊 选股完成 ({result.run_date} {result.run_time})\n\n未找到满足条件的股票"

        source_name = "同花顺问财" if result.data_source == "ths" else "东方财富"

        lines = [
            f"📊 选股结果 ({result.run_date} {result.run_time})",
            f"数据源: {source_name}",
            f"策略: {result.strategy_name}",
            f"满足: {result.matched_count} 只",
            "",
            "━━━ 精选推荐 ━━━",
        ]

        for i, stock in enumerate(result.stocks, 1):
            score_emoji = "🔥" if stock.score >= 75 else "⭐" if stock.score >= 60 else "👀"
            lines.append(f"\n{score_emoji} {i}. {stock.code} {stock.name}")
            lines.append(f"   综合评分: {stock.score:.1f}")

            # 技术维度
            if stock.technical_score > 0:
                tech_parts = [f"技术{stock.technical_score:.0f}"]
                if stock.volume_ratio > 1.5:
                    tech_parts.append(f"量比{stock.volume_ratio:.1f}")
                if stock.dde_big_order > 0:
                    tech_parts.append(f"DDE+{stock.dde_big_order:.1f}%")
                if stock.signal_tags:
                    tech_parts.extend(stock.signal_tags[:2])
                lines.append(f"   技术: {' | '.join(tech_parts)}")

            # 板块维度
            if stock.sector_name:
                lines.append(f"   板块: {stock.sector_name} (强度{stock.sector_score:.0f})")

            # 舆情维度
            if stock.heat_score > 0:
                direction = "📈" if stock.sentiment_score > 0.1 else "📉" if stock.sentiment_score < -0.1 else "➡️"
                lines.append(
                    f"   舆情: 热度{stock.heat_score:.0f} {direction}"
                    f" 情绪{stock.sentiment_score:+.2f}"
                )

            # 风险维度
            if stock.risk_score > 30 or stock.risk_tags:
                risk_parts = [f"风险{stock.risk_score:.0f}"]
                risk_parts.extend(stock.risk_tags[:2])
                lines.append(f"   风险: {' | '.join(risk_parts)}")

            # AI 点评
            if stock.ai_reasoning:
                reasoning = stock.ai_reasoning[:40]
                lines.append(f"   💡 {reasoning}")

            # 持仓状态
            if stock.portfolio_held:
                pnl_str = ""
                if stock.portfolio_pnl_pct is not None:
                    pnl_emoji = "📈" if stock.portfolio_pnl_pct > 0 else "📉"
                    pnl_str = f" {pnl_emoji}{stock.portfolio_pnl_pct:+.1f}%"
                lines.append(
                    f"   💰 已持仓 {stock.portfolio_quantity:.0f}股"
                    f" 成本{stock.portfolio_avg_cost:.2f}{pnl_str}"
                )

        lines.append("")
        lines.append(f"⏱️ 耗时: {result.elapsed_seconds:.1f}s")

        if result.query:
            lines.append(f"📝 查询: {result.query[:60]}...")

        return "\n".join(lines)
