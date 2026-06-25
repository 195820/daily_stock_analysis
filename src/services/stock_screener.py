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
