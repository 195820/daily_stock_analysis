"""
GOT 编排器

执行流程：
1. 检查是否交易日（非交易日直接跳过）
2. 并行启动两路：
   - 独立数据路：Node 4（消息面）‖ Node 5（情绪面）
   - 技术分析路：Node 1（大盘）→ Node 2（板块）→ Node 3（资金面）
3. 两路都完成后 → Node 6（聚合）
4. 输出 unified_context
"""

import asyncio
import logging
from datetime import datetime, date
from typing import Optional

from src.got.schemas import UnifiedContext
from src.got.nodes.market_analysis import MarketAnalysisNode
from src.got.nodes.sector_analysis import SectorAnalysisNode
from src.got.nodes.capital_analysis import CapitalAnalysisNode
from src.got.nodes.news_analysis import NewsAnalysisNode
from src.got.nodes.sentiment_analysis import SentimentAnalysisNode
from src.got.nodes.aggregator import AggregatorNode

logger = logging.getLogger(__name__)


class NonTradingDayError(Exception):
    """非交易日异常"""
    pass


class GOTOrchestrator:
    """GOT Phase 1 编排器"""

    def __init__(self, use_mock: bool = True):
        """
        Args:
            use_mock: 是否使用 mock 数据（MVP 阶段为 True）
        """
        self.use_mock = use_mock
        self.market_node = MarketAnalysisNode(use_mock=use_mock)
        self.sector_node = SectorAnalysisNode(use_mock=use_mock)
        self.capital_node = CapitalAnalysisNode(use_mock=use_mock)
        self.news_node = NewsAnalysisNode(use_mock=use_mock)
        self.sentiment_node = SentimentAnalysisNode(use_mock=use_mock)
        self.aggregator = AggregatorNode(use_mock=use_mock)

    async def run(self, check_date: Optional[date] = None) -> UnifiedContext:
        """
        执行 GOT 完整流程

        Args:
            check_date: 检查日期，默认为今天

        Returns:
            UnifiedContext: 统一市场上下文

        Raises:
            NonTradingDayError: 非交易日
        """
        target_date = check_date or date.today()

        # Step 1: 交易日检查（MVP 阶段跳过）
        if not self.use_mock:
            self._check_trading_day(target_date)

        logger.info(f"[GOT] 开始执行 — 日期: {target_date}, mock: {self.use_mock}")
        start_time = datetime.now()

        # Step 2: 并行执行两路
        tech_path_task = self._run_tech_path()
        data_path_task = self._run_data_path()

        tech_result, data_result = await asyncio.gather(
            tech_path_task,
            data_path_task,
            return_exceptions=True,
        )

        # 错误处理
        if isinstance(tech_result, Exception):
            logger.error(f"[GOT] 技术分析路失败: {tech_result}")
            raise tech_result
        if isinstance(data_result, Exception):
            logger.error(f"[GOT] 独立数据路失败: {data_result}")
            raise data_result

        market_ctx, sector_ctx, capital_ctx = tech_result
        news_ctx, sentiment_ctx = data_result

        # Step 3: 聚合
        logger.info("[GOT] 两路完成，开始聚合")
        unified = await self.aggregator.run(
            market=market_ctx,
            sector=sector_ctx,
            capital=capital_ctx,
            news=news_ctx,
            sentiment=sentiment_ctx,
        )

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"[GOT] 完成 — 耗时: {elapsed:.1f}s, 信号: {unified.unified_signal}")

        # 真实数据运行自动存档到 data/got_daily/YYYYMMDD/
        if not self.use_mock:
            try:
                from src.got.archive import archive_daily_run

                archive_daily_run(
                    market_ctx=market_ctx,
                    sector_ctx=sector_ctx,
                    capital_ctx=capital_ctx,
                    news_ctx=news_ctx,
                    sentiment_ctx=sentiment_ctx,
                    unified_ctx=unified,
                    run_date=target_date,
                    use_mock=False,
                    start_time=start_time,
                )
            except Exception as exc:
                # 存档失败不应阻断 GOT 主流程
                logger.warning("[GOT] 历史档存档失败（已忽略）: %s", exc)

        return unified

    async def _run_tech_path(self) -> tuple:
        """技术分析路：串行依赖（大盘 → 板块 → 资金面）"""
        logger.info("[GOT] 技术分析路：开始")

        market_ctx = await self.market_node.run()
        logger.info(f"[GOT]   Node 1 完成 — 科技成长: {market_ctx.tech_growth.trend}")

        sector_ctx = await self.sector_node.run(market_context=market_ctx)
        logger.info(f"[GOT]   Node 2 完成 — 活跃主线: {len(sector_ctx.active_mainline)}")

        capital_ctx = await self.capital_node.run(
            market_context=market_ctx,
            sector_context=sector_ctx,
        )
        logger.info(f"[GOT]   Node 3 完成 — 机构态度: {capital_ctx.institutional_attitude}")

        return market_ctx, sector_ctx, capital_ctx

    async def _run_data_path(self) -> tuple:
        """独立数据路：并行（消息面 ‖ 情绪面）"""
        logger.info("[GOT] 独立数据路：开始（并行）")

        news_ctx, sentiment_ctx = await asyncio.gather(
            self.news_node.run(),
            self.sentiment_node.run(),
        )

        logger.info(f"[GOT]   Node 4 完成 — 消息面: {news_ctx.overall_impact}")
        logger.info(f"[GOT]   Node 5 完成 — 情绪: {sentiment_ctx.overall_sentiment}")

        return news_ctx, sentiment_ctx

    def _check_trading_day(self, check_date: date):
        """检查是否交易日"""
        try:
            from src.core.trading_calendar import TradingCalendar
            calendar = TradingCalendar()
            if not calendar.is_market_open("cn", check_date):
                raise NonTradingDayError(f"{check_date} 非交易日，跳过分析")
        except ImportError:
            logger.warning("[GOT] 无法导入交易日历，跳过交易日检查")
