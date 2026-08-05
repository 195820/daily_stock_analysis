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
from typing import Any, Dict, List, Optional

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
    sector_name: str = ""          # 所属板块
    sector_score: float = 0.0      # 板块强度分

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
    news_score: float = 0.0                # 消息面评分 0-100
    tech_wave_score: float = 0.0           # 波段技术评分 0-100
    risk_score_safe: float = 100.0         # 风险评分 0-100（越高越安全，与 risk_score 相反）
    sector_type: str = ""                  # 板块类型: active_mainline / ambush
    backtest_snapshot: Optional[Dict[str, Any]] = None  # 回测快照


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
        all_sectors = rotation.all_sectors or (
            rotation.strong_sectors
            + rotation.emerging_sectors
            + rotation.declining_sectors
        )
        for sector in all_sectors:
            if sector.strength_score > 0:
                board_score_map[sector.name] = sector.strength_score

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

                best_score = 0.0
                best_name = ""
                for board in boards:
                    board_name = board.get("name", "")
                    if board_name in board_score_map:
                        score = board_score_map[board_name]
                        if score > best_score:
                            best_score = score
                            best_name = board_name

                if best_score > 0:
                    stock.sector_name = best_name
                    stock.sector_score = best_score
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
            sector_name = sector_info.get("name")
            sector_score = sector_info.get("strength_score", 0)
            sector_type = "active_mainline" if sector_info in qualified_mainlines else "ambush"

            # 用问财快速筛选板块成分股
            constituents = self._get_sector_constituents_filtered(sector_name, sector_type)

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

        logger.info(
            f"选股池: {len(qualified_mainlines)} 个主线 + "
            f"{len(qualified_ambush)} 个埋伏 → {len(deduped)} 只股票（问财初筛后）"
        )

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
            ths_service = THSScreenerService(self.config)

            # 根据板块类型使用不同的 DDE 筛选条件
            if sector_type == "active_mainline":
                # 主线：当天 DDE 强势
                dde_condition = "1个交易日前的dde大单净量>=0.8%，近2个交易日的区间dde大单净量>=0.8%，"
            else:
                # 埋伏：近5日DDE累计为正（吸筹信号，不要求连续，刚启动的板块也能入选）
                dde_condition = "近5个交易日的dde大单净量合计>0，"

            # 概念→行业关键词映射（用于过滤蹭概念的票）
            concept_industry_map = {
                "数据要素": ["计算机", "软件", "数据处理", "信息服务", "互联网", "通信"],
                "AI产业链": ["计算机", "软件", "电子", "通信", "人工智能", "芯片", "半导体"],
                "AIGC概念": ["计算机", "软件", "传媒", "互联网", "人工智能"],
                "AI智能体": ["计算机", "软件", "人工智能", "机器人"],
                "华为鸿蒙": ["计算机", "软件", "电子", "通信", "手机"],
                "CXO": ["医药", "医疗", "生物", "制药", "CRO", "CDMO"],
                "黄金": ["贵金属", "有色", "矿业", "黄金"],
                "钢铁": ["钢铁", "金属制品", "特钢"],
                "化纤": ["化学纤维", "化工", "涤纶", "氨纶"],
                "汽车整车": ["汽车", "整车", "新能源车"],
                "虚拟电厂": ["电力", "电气", "电网", "能源"],
            }

            query = (
                f"{sector_name}概念板块成分股，"
                f"{dde_condition}"
                "上市天数>365天，"
                "非ST，"
                "所属同花顺行业，"
                "按涨跌幅降序"
            )
            logger.info(f"[问财初筛-DDE-{sector_type}] {sector_name}: {query[:60]}...")

            ths_result = ths_service.query(query)
            if ths_result and ths_result.stocks:
                # 行业过滤：检查每只票的行业板块是否与概念主题相关
                industry_keywords = concept_industry_map.get(sector_name, [])
                filtered_stocks = []
                for s in ths_result.stocks:
                    if industry_keywords and s.industry:
                        # 检查 industry 字段是否包含相关关键词
                        if any(kw in s.industry for kw in industry_keywords):
                            filtered_stocks.append(s)
                        else:
                            logger.debug(f"[行业过滤] {s.code} {s.name} 行业'{s.industry}'与{sector_name}不匹配，跳过")
                    else:
                        # 无映射或无行业信息，保留
                        filtered_stocks.append(s)

                if len(filtered_stocks) < len(ths_result.stocks):
                    logger.info(f"[行业过滤] {sector_name}: {len(ths_result.stocks)}→{len(filtered_stocks)} 只（过滤{len(ths_result.stocks)-len(filtered_stocks)}只蹭概念票）")

                # 取前 5 只
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
                    for s in filtered_stocks[:5]
                ]
                logger.info(
                    f"[问财初筛-DDE-{sector_type}] {sector_name} 获取到 {len(stocks)} 只候选"
                    f"（DDE: {stocks[0]['dde_net_pct']:.2f}%~{stocks[-1]['dde_net_pct']:.2f}%）"
                    if stocks else ""
                )
                return stocks
            else:
                logger.warning(f"[问财初筛-DDE-{sector_type}] {sector_name} 无结果，降级为不带 DDE 筛选")
                # 降级：不过滤 DDE 阈值，但仍查询 DDE 值（供资金面评分用）
                query_fallback = f"{sector_name}概念板块成分股，dde大单净量，上市天数>365天，非ST，按涨跌幅降序"
                ths_result = ths_service.query(query_fallback)
                if ths_result and ths_result.stocks:
                    stocks = [
                        {
                            "code": s.code,
                            "name": s.name,
                            "price": s.price,
                            "dde_net_pct": s.dde_net_pct,
                            "change_pct": s.change_pct,
                        }
                        for s in ths_result.stocks[:5]
                    ]
                    logger.info(f"[问财初筛-降级-{sector_type}] {sector_name} 获取到 {len(stocks)} 只候选")
                    return stocks
                return []

        except Exception as e:
            logger.error(f"[问财初筛-DDE-{sector_type}] {sector_name} 失败: {e}")
            return []

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

        # 轮次 1: 信号过滤（排除衰竭板块）
        exhausted = [s.get("name") for s in got_context.get("sector_context", {}).get("exhausted_mainline", [])]
        candidates = [c for c in candidates if c["sector"] not in exhausted]
        logger.info(f"轮次 1: {len(candidates)} 只（排除衰竭板块）")

        # 轮次 2: 资金面评分 Top 20
        capital_ctx = got_context.get("capital_context", {})
        capital_scores = self._score_capital(candidates, capital_ctx, fetcher_manager)
        for c in candidates:
            c["capital_score"] = capital_scores.get(c["code"], 0)
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

        # 轮次 4: 风险排雷 Top 8
        risk_scores = self._score_risk(candidates, fetcher_manager)
        for c in candidates:
            c["risk_score_safe"] = risk_scores.get(c["code"], 0)
        candidates = [c for c in candidates if c["risk_score_safe"] > 50]  # 风险阈值
        candidates.sort(key=lambda x: x["risk_score_safe"], reverse=True)
        candidates = candidates[:8]
        logger.info(f"轮次 4: {len(candidates)} 只（风险排雷后）")

        # 消息面评分（全量候选，不排序但记录分数）
        news_ctx = got_context.get("news_context", {})
        news_scores = self._score_news(candidates, news_ctx)
        for c in candidates:
            c["news_score"] = news_scores.get(c["code"], 0)

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
                    news_score=c.get("news_score", 0),
                    tech_wave_score=c["tech_wave_score"],
                    risk_score_safe=c["risk_score_safe"],
                    sector_name=c["sector"],
                    sector_score=c["sector_score"],
                    sector_type=c["sector_type"],
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

    def _score_capital(self, candidates: List[Dict], capital_ctx: dict, fetcher_manager) -> Dict[str, float]:
        """资金面评分（30% 权重）

        评分维度：
        - 龙虎榜机构买入: +40分（如果在 mainline_stocks_bought 中）
        - 主力资金净流入: +30分（从 AKShare 获取）
        - 北向资金净买入: +15分（如果 get_northbound_flow 显示净流入）
        - 大宗交易溢价: +15分（如果 signal_type == "抢筹"）
        """
        # 解析龙虎榜买入列表（格式: "300502.新易盛"）
        dragon_tiger_bought_raw = capital_ctx.get("evidence", {}).get("dragon_tiger", {}).get("mainline_stocks_bought", [])
        dragon_tiger_bought_codes = set()
        for item in dragon_tiger_bought_raw:
            if "." in item:
                code = item.split(".")[0]
                dragon_tiger_bought_codes.add(code)

        # 资金流数据：直接从候选股的 DDE 字段获取（问财初筛已包含）
        # 不再逐只调用 AKShare/mootdx，节省 90%+ 耗时
        capital_flow_data = {}
        for c in candidates:
            dde = c.get("dde_net_pct", 0)
            if dde != 0:
                capital_flow_data[c["code"]] = dde

        if capital_flow_data:
            logger.info(
                f"[资金面-THS] 使用问财 DDE 数据: "
                f"{len(capital_flow_data)} 只, "
                f"范围 {min(capital_flow_data.values()):.2f}%~{max(capital_flow_data.values()):.2f}%"
            )
        else:
            logger.warning("[资金面-THS] 候选股无 DDE 数据，资金面维度将为 0")

        # 一次性获取北向资金数据（避免重复请求）
        northbound_positive = False
        try:
            northbound_df = fetcher_manager.get_northbound_flow()
            if northbound_df is not None and not northbound_df.empty:
                latest = northbound_df.iloc[-1]
                if latest.get("net_amount", 0) > 0:
                    northbound_positive = True
                    logger.info("[资金面] 北向资金净流入")
        except Exception as e:
            logger.warning(f"获取北向资金失败: {e}")

        # 一次性获取大宗交易数据（避免重复请求）
        block_trade_data = {}
        try:
            block_trade_df = fetcher_manager.get_block_trade()
            if block_trade_df is not None and not block_trade_df.empty:
                # 大宗交易数据按股票代码分组
                # 注意：字段名可能是 '代码' 而不是 'code'
                code_col = '代码' if '代码' in block_trade_df.columns else 'code'
                signal_col = 'signal_type' if 'signal_type' in block_trade_df.columns else None

                if signal_col:
                    for _, row in block_trade_df.iterrows():
                        stock_code = str(row.get(code_col, ''))
                        signal = row.get(signal_col, '')
                        if stock_code:
                            # 如果有多条记录，取最新的信号
                            block_trade_data[stock_code] = signal
                    logger.info(f"[资金面] 获取到 {len(block_trade_data)} 只股票的大宗交易数据")
        except Exception as e:
            logger.warning(f"获取大宗交易失败: {e}")

        scores = {}

        # 资金流排名百分位（DDE 大单净量%，用于 0-30 分评分）
        flow_values = sorted(capital_flow_data.values(), reverse=True) if capital_flow_data else []

        for c in candidates:
            score = 0

            # 龙虎榜机构买入 (+40)
            if c["code"] in dragon_tiger_bought_codes:
                score += 40

            # 主力资金净流入 — DDE 大单净量排名百分位 (+0~30)
            if c["code"] in capital_flow_data and flow_values:
                dde_val = capital_flow_data[c["code"]]
                # 排名百分位：DDE 越高排名越靠前，percentile 越高
                rank_idx = flow_values.index(dde_val)
                percentile = 1.0 - (rank_idx / max(len(flow_values) - 1, 1))
                score += int(30 * percentile)

            # 北向资金（所有股票共享同一个结果）(+15)
            if northbound_positive:
                score += 15

            # 大宗交易抢筹 (+15)
            stock_signal = block_trade_data.get(c["code"], "")
            if stock_signal == "抢筹":
                score += 15

            scores[c["code"]] = score

        return scores

    def _score_news(self, candidates: List[Dict], news_ctx: dict) -> Dict[str, float]:
        """消息面评分（20% 权重）

        根据新闻事件中 affected_sectors 与候选股所在板块的匹配度评分。
        - high impact + positive → +40
        - high impact + negative → -20（减分）
        - medium impact + positive → +20
        - medium impact + negative → -10
        - 多源确认（source_count ≥ 3）额外 ×1.5
        """
        events = news_ctx.get("major_events", [])
        if not events:
            return {c["code"]: 50.0 for c in candidates}  # 无新闻时中性

        scores = {}
        for c in candidates:
            sector = c.get("sector", "")
            score = 50.0  # 基础分（中性）

            for event in events:
                affected = event.get("affected_sectors", [])
                # 板块名匹配（模糊匹配：板块名包含在 affected 或反之）
                if not any(self._sector_matches(sector, a) for a in affected):
                    continue

                impact = event.get("impact_level", "low")
                sentiment = event.get("sentiment", "neutral")
                source_count = event.get("source_count", 1)

                # 基础分数
                if impact == "high" and sentiment == "positive":
                    delta = 40
                elif impact == "high" and sentiment == "negative":
                    delta = -20
                elif impact == "medium" and sentiment == "positive":
                    delta = 20
                elif impact == "medium" and sentiment == "negative":
                    delta = -10
                else:
                    delta = 0

                # 多源确认加成
                if source_count >= 3:
                    delta = int(delta * 1.5)

                score += delta

            scores[c["code"]] = max(0, min(100, score))

        return scores

    @staticmethod
    def _sector_matches(stock_sector: str, news_sector: str) -> bool:
        """板块名模糊匹配（处理新闻板块名与问财板块名不一致的情况）"""
        if not stock_sector or not news_sector:
            return False
        # 精确匹配或包含关系
        return stock_sector in news_sector or news_sector in stock_sector

    def _score_technical_wave(self, candidates: List[Dict], fetcher_manager) -> Dict[str, float]:
        """波段技术指标评分（15% 权重）

        与短线不同，波段操作关注：
        - MA20 趋势: 20%（价格在 MA20 上方且 MA20 向上）
        - 布林带位置: 15%（在中轨和上轨之间）
        - 周线趋势: 15%（周线 MA10 向上）
        - RSI12: 10%（50-70 区间，未超买）
        - 20 日突破: 10%（突破 20 日最高价）
        - 量比: 10%（1.5-3.0 温和放量）
        - 换手率: 10%（3%-8% 适中）
        - MACD 金叉: 10%（DIF 上穿 DEA）
        """
        scores = {}

        for c in candidates:
            # 获取 60 天 K 线
            try:
                df, _ = fetcher_manager.get_daily_data(c["code"], days=60)
                if df is None or len(df) < 20:
                    scores[c["code"]] = 0
                    continue

                score = 0

                # MA20 趋势（20%）
                ma20 = df["close"].rolling(20).mean().iloc[-1]
                ma20_5d_ago = df["close"].rolling(20).mean().iloc[-5]
                if df["close"].iloc[-1] > ma20 and ma20 > ma20_5d_ago:
                    score += 20

                # 布林带位置（15%）
                bb_mid = df["close"].rolling(20).mean().iloc[-1]
                bb_std = df["close"].rolling(20).std().iloc[-1]
                bb_upper = bb_mid + 2 * bb_std
                if bb_mid < df["close"].iloc[-1] < bb_upper:
                    score += 15

                # RSI12（10%）
                rsi12 = self._calc_rsi(df["close"], 12)
                if 50 <= rsi12 <= 70:
                    score += 10

                # 20 日突破（10%）
                high_20d = df["high"].rolling(20).max().iloc[-2]
                if df["close"].iloc[-1] > high_20d:
                    score += 10

                # 量比（10%）
                volume_ratio = df["volume"].iloc[-1] / df["volume"].rolling(5).mean().iloc[-2]
                if 1.5 <= volume_ratio <= 3.0:
                    score += 10

                # 换手率（10%）— 直接使用问财返回的数据，避免逐只拉行情
                turnover_rate = c.get("turnover_rate", 0)
                if turnover_rate and 0.03 <= turnover_rate <= 0.08:
                    score += 10

                # MACD 金叉（10%）
                macd_line, signal_line, _ = self._calc_macd(df["close"])
                if macd_line.iloc[-1] > signal_line.iloc[-1] and macd_line.iloc[-2] <= signal_line.iloc[-2]:
                    score += 10

                # 均线多头排列（15%）
                ma5 = df["close"].rolling(5).mean().iloc[-1]
                ma10 = df["close"].rolling(10).mean().iloc[-1]
                if ma5 > ma10 > ma20:
                    score += 15

                scores[c["code"]] = score
            except Exception as e:
                logger.warning(f"计算技术指标失败 {c['code']}: {e}")
                scores[c["code"]] = 0

        return scores

    def _score_risk(self, candidates: List[Dict], fetcher_manager) -> Dict[str, float]:
        """风险评分（15% 权重，越高越安全）

        扣分项：
        - RSI > 80（超买）: -30分
        - 近 5 日涨幅 > 20%: -25分
        - 波动率 > 0.05: -15分

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

                # RSI 超买
                if len(df) >= 12:
                    rsi6 = self._calc_rsi(df["close"], 6)
                    if rsi6 > 80:
                        score -= 30

                # 近 5 日涨幅
                pct_5d = (df["close"].iloc[-1] / df["close"].iloc[-5] - 1) * 100
                if pct_5d > 20:
                    score -= 25

                # 波动率
                if len(df) >= 20 and "pct_chg" in df.columns:
                    volatility = df["pct_chg"].rolling(20).std().iloc[-1]
                    if volatility > 0.05:
                        score -= 15

                scores[c["code"]] = max(0, score)
            except Exception as e:
                logger.warning(f"计算风险评分失败 {c['code']}: {e}")
                scores[c["code"]] = 50

        return scores

    def _compute_got_composite_score(self, stock: ScreenerStock, got_context: dict) -> float:
        """综合评分（满分 100）

        权重分配（5 维度）：
        - 资金面: 30%（龙虎榜/主力资金/大宗交易）
        - 消息面: 20%（事件影响 + 多源确认）
        - 板块强度: 20%（主线/埋伏/启动信号）
        - 技术面(波段): 15%（MA20/布林带/周线）
        - 风险因子: 15%（估值/超买/波动率，反向 → risk_score_safe）
        """
        sector_score = stock.sector_score   # 0-100
        tech_score = stock.tech_wave_score  # 0-100
        risk_score = stock.risk_score_safe  # 0-100（越高越安全）
        capital = stock.capital_score       # 0-100
        news = stock.news_score             # 0-100

        composite = (
            capital * 0.30 +
            news * 0.20 +
            sector_score * 0.20 +
            tech_score * 0.15 +
            risk_score * 0.15
        )

        return round(composite, 2)

    def _generate_got_reasoning(self, stock: ScreenerStock, got_context: dict) -> str:
        """生成推荐理由（Markdown 格式）"""
        lines = [
            f"**综合评分**: {stock.score:.1f}/100",
            "",
            "### 核心逻辑",
            f"1. **资金面** ({stock.capital_score*0.3:.1f}/30): ",
            f"   - 资金面评分 {stock.capital_score:.0f}/100",
            f"2. **消息面** ({stock.news_score*0.2:.1f}/20): ",
            f"   - 消息面评分 {stock.news_score:.0f}/100",
            f"3. **板块强度** ({stock.sector_score*0.2:.1f}/20): ",
            f"   - 属于{stock.sector_type}板块「{stock.sector_name}」",
            f"   - 板块强度 {stock.sector_score:.0f} 分",
            f"4. **技术面** ({stock.tech_wave_score*0.15:.1f}/15): ",
            f"   - 波段技术指标评分 {stock.tech_wave_score:.0f}/100",
            f"5. **风险** ({stock.risk_score_safe*0.15:.1f}/15): ",
            f"   - 安全评分 {stock.risk_score_safe:.0f}/100",
            "",
            "### 交易建议",
        ]

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
