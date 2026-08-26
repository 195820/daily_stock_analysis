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
    backtest_snapshot: Optional[Dict[str, Any]] = None  # 回测快照

    # P5 催化剂关联度字段
    catalyst_relevance_score: float = 0.0  # 催化剂关联度 0-100
    catalyst_event_title: str = ""         # 关联的催化剂事件标题
    catalyst_event_date: str = ""          # 事件日期 YYYY-MM-DD
    catalyst_event_type: str = ""          # earnings / lockup / narrative / global / macro
    catalyst_confidence: float = 0.0       # 催化剂置信度 0-1


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
                self._run_eastmoney_screener(result, strategy_name, top_n)

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

    def _run_eastmoney_screener(
        self,
        result: ScreenerRunResult,
        strategy_name: str,
        top_n: int
    ) -> None:
        """使用东方财富数据选股"""
        from src.services.dde_fetcher import DdeFetcher
        from src.services.strategy_filter import StrategyFilterService
        from src.repositories.dde_repository import DdeRepository
        from datetime import date as date_type

        dde_fetcher = DdeFetcher(self.config)
        filter_service = StrategyFilterService(self.config)
        dde_repo = DdeRepository()

        # 加载策略
        yaml_path = f"strategies/custom/{strategy_name}.yaml"
        filter_service.load_strategy(yaml_path)

        # 全市场扫描
        matches, total_scanned = filter_service.scan_market(
            strategy_name=strategy_name,
            min_score=0,
        )
        result.total_scanned = total_scanned
        result.matched_count = len(matches)

        # 保存 DDE 快照
        dde_result = dde_fetcher.fetch_market_dde(use_cache=True)
        if not dde_result.error:
            trade_date = date_type.fromisoformat(result.run_date)
            records = [
                {
                    'code': s.code,
                    'dde_big_order': s.dde_big_order,
                    'dde_big_order_buy': s.dde_big_order_buy,
                    'dde_big_order_sell': s.dde_big_order_sell,
                    'dde_mid_order': s.dde_mid_order,
                    'dde_small_order': s.dde_small_order,
                }
                for s in dde_result.snapshots.values()
            ]
            dde_repo.batch_save_dde(records, trade_date)

        # 转换结果
        result.stocks = [
            ScreenerStock(
                code=m.stock_code,
                name=m.stock_name,
                score=m.score,
                dde_big_order=m.dde_big_order,
                source="eastmoney"
            )
            for m in matches[:top_n]
        ]

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
                universe.append({
                    "code": stock_info["code"],
                    "name": stock_info.get("name", ""),
                    "sector": sector_name,
                    "sector_type": sector_type,
                    "sector_score": sector_score,
                    # 保留问财量价+DDE 数据（供资金面/技术面评分使用）
                    "price": stock_info.get("price", 0),
                    "dde_net_pct": stock_info.get("dde_net_pct", 0),
                    "change_pct": stock_info.get("change_pct", 0),
                    "turnover_rate": stock_info.get("turnover_rate", 0),
                    "volume": stock_info.get("volume", 0),
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

            def _try_query(query_template: str, max_results: int) -> Optional[List[Dict]]:
                """按 board_clauses 顺序尝试查询，命中返回 stocks 列表"""
                for clause in board_clauses:
                    query = query_template.replace("{board_clause}", clause)
                    logger.info(f"[问财初筛-{sector_type}] {sector_name} (kind={kind}): {query[:80]}...")
                    ths_result = ths_service.query(query)
                    if not ths_result or not ths_result.stocks:
                        continue
                    # 命中：回写别名表
                    resolver.confirm(sector_name, board_name, kind=kind)
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
                            "change_pct": s.change_pct,
                            "turnover_rate": s.turnover_rate,
                            "volume": s.volume,
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
                return result

            # 双语法 × 两轮全空 → 醒目告警
            logger.error(
                f"[问财初筛-{sector_type}] {sector_name} 双语法全空！"
                f"resolved={board_name}, kind={kind}。"
                f"尝试过的语法: {board_clauses}"
            )
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

    def _filter_got_finalists(
        self,
        universe: List[Dict],
        got_context: dict,
        top_n: int
    ) -> List[ScreenerStock]:
        """5 轮漏斗筛选

        轮次 1: 信号过滤（排除衰竭板块）
        轮次 2: 资金面评分 Top 20（龙虎榜/主力资金/大宗交易）
        轮次 3: 波段技术确认 Top 15（MA20/布林带/周线趋势）
        轮次 4: 风险排雷 Top 8（估值/超买/波动率）
        轮次 5: 板块分散 Top N（每个板块最多 1 只）
        """
        from data_provider.base import DataFetcherManager
        fetcher_manager = DataFetcherManager()

        candidates = universe.copy()

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

        # 轮次 1: 信号过滤（排除衰竭板块）
        exhausted = [s.get("name") for s in got_context.get("sector_context", {}).get("exhausted_mainline", [])]
        candidates = [c for c in candidates if c["sector"] not in exhausted]
        logger.info(f"轮次 1: {len(candidates)} 只（排除衰竭板块）")

        # 轮次 1.5: 横盘过滤（排除多窗口小涨幅的停滞票）
        candidates = self._filter_stagnation(candidates, fetcher_manager)
        logger.info(f"轮次 1.5: {len(candidates)} 只（横盘过滤后）")

        # 轮次 2: 资金面评分 Top 20
        capital_ctx = got_context.get("capital_context", {})
        capital_scores, capital_avail = self._score_capital(candidates, capital_ctx)
        for c in candidates:
            c["capital_score"] = capital_scores.get(c["code"], 0)
            c["capital_available"] = capital_avail.get(c["code"], False)
        candidates.sort(key=lambda x: x["capital_score"], reverse=True)
        candidates = candidates[:20]
        logger.info(f"轮次 2: {len(candidates)} 只（资金面 Top 20）")

        # 轮次 3: 波段技术确认 Top 15
        tech_scores = self._score_technical_wave(candidates, fetcher_manager)
        for c in candidates:
            c["tech_wave_score"] = tech_scores.get(c["code"], 0)
        candidates.sort(key=lambda x: x["tech_wave_score"], reverse=True)
        candidates = candidates[:15]
        logger.info(f"轮次 3: {len(candidates)} 只（波段技术 Top 15）")

        # ── 轮次 4: 风险排雷（主通道 Top 6 + 软保底至多 2）──
        risk_scores = self._score_risk(candidates, fetcher_manager)
        for c in candidates:
            c["risk_score_safe"] = risk_scores.get(c["code"], 0)
        candidates = self._apply_r4_filter(candidates, got_context)
        logger.info(f"轮次 4: {len(candidates)} 只（风险排雷后）")

        # 消息面评分（全量候选，不排序但记录分数）
        news_ctx = got_context.get("news_context", {})
        sector_ctx = got_context.get("sector_context", {})
        news_scores, news_avail = self._score_news(candidates, news_ctx, sector_ctx)
        for c in candidates:
            c["news_score"] = news_scores.get(c["code"], 0)
            c["news_available"] = news_avail.get(c["code"], False)

        # P5: 催化剂关联度重排（仅 ambush 板块，置信度 >= 0.7 时生效）
        catalyst_scores, catalyst_details = self._score_catalyst_relevance(candidates, got_context)
        max_catalyst_confidence = max(
            (d.get("confidence", 0) for d in catalyst_details.values()),
            default=0.0,
        )
        ambush_candidates = [c for c in candidates if c.get("sector_type") == "ambush"]
        if catalyst_scores and max_catalyst_confidence >= 0.7 and ambush_candidates:
            # 对 ambush 候选按催化剂关联度重排
            for c in candidates:
                c["catalyst_relevance_score"] = catalyst_scores.get(c["code"], 0)
            # 仅重排 ambush 候选（主线候选保持技术排序）
            ambush_candidates.sort(
                key=lambda x: x.get("catalyst_relevance_score", 0), reverse=True
            )
            # 重建候选列表：主线保持原序 + ambush 按催化剂重排
            mainline_reordered = [c for c in candidates if c.get("sector_type") != "ambush"]
            candidates = mainline_reordered + ambush_candidates
            logger.info(
                f"P5催化剂重排: {len(ambush_candidates)} 只 ambush 候选重排 "
                f"(max_confidence={max_catalyst_confidence:.2f})"
            )
        elif catalyst_scores:
            logger.info(
                f"P5催化剂重排跳过: max_confidence={max_catalyst_confidence:.2f} < 0.7"
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

        return finalists

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
        dde_data = {}
        for c in candidates:
            dde = c.get("dde_net_pct", 0)
            if dde != 0:
                dde_data[c["code"]] = dde

        if dde_data:
            logger.info(
                f"[资金面-DDE] {len(dde_data)} 只, "
                f"范围 {min(dde_data.values()):.2f}%~{max(dde_data.values()):.2f}%"
            )
        else:
            logger.warning("[资金面-DDE] 候选股无 DDE 数据")

        dde_sorted = sorted(dde_data.values(), reverse=True) if dde_data else []

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
        sector_flow_by_stock = {}
        for c in candidates:
            sector = c.get("sector", "")
            if sector in sector_flow_map:
                sector_flow_by_stock[c["code"]] = sector_flow_map[sector]

        # 板块资金流百分位：基于全量板块排名（而非仅候选股之间）
        all_flows = sorted(sector_flow_map.values(), reverse=True) if sector_flow_map else []

        # ── 综合评分 ──
        scores = {}
        availability = {}
        for c in candidates:
            score = 0.0
            has_data = False

            # DDE 百分位 (0-50)
            if c["code"] in dde_data and dde_sorted:
                dde_val = dde_data[c["code"]]
                rank_idx = dde_sorted.index(dde_val)
                percentile = 1.0 - (rank_idx / max(len(dde_sorted) - 1, 1))
                score += 50 * percentile
                has_data = True

            # 板块资金流百分位 (0-50)：在全量板块中的排名位置
            if c["code"] in sector_flow_by_stock and all_flows:
                flow_val = sector_flow_by_stock[c["code"]]
                # 计算 flow_val 在 all_flows 中的排名（二分查找更高效）
                rank_idx = 0
                for i, v in enumerate(all_flows):
                    if v >= flow_val:
                        rank_idx = i
                    else:
                        break
                percentile = 1.0 - (rank_idx / max(len(all_flows) - 1, 1))
                score += 50 * percentile
                has_data = True

            scores[c["code"]] = round(score, 1)
            availability[c["code"]] = has_data

        return scores, availability

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
        import re

        # ── 加载催化剂日历 ──
        news_ctx = got_context.get("news_context", {})
        calendar_data = news_ctx.get("catalyst_calendar")
        if not calendar_data:
            logger.info("[P5催化剂] 无催化剂日历数据，跳过重排")
            return {}, {}

        events = calendar_data.get("events", [])
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

            # 低置信事件不参与评分（但仍记录）
            if confidence < 0.7:
                continue

            # 个股级事件：从 title 提取股票代码
            if event_type in ("earnings", "lockup"):
                codes = stock_code_pattern.findall(title)
                for code in codes:
                    individual_events.setdefault(code, []).append(evt)

            # 板块级事件：按 sector 分组（与个股级互斥，防止同一事件重复计分）
            elif sector:
                sector_events.setdefault(sector, []).append(evt)

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
            if best_score < 100 and sector in sector_events:
                for evt in sector_events[sector]:
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
                "top_stocks": [],
            }

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
                }
                data["top_stocks"].append(stock_data)

            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info(f"[GOT选股] 已保存结果到 {output_file}")
        except Exception as e:
            logger.error(f"[GOT选股] 保存结果失败: {e}")

    def _get_stock_name(self, code: str, fetcher_manager) -> str:
        """获取股票名称"""
        try:
            return fetcher_manager.get_stock_name(code) or code
        except Exception:
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
