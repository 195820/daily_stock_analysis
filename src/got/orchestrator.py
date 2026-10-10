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
import os
from datetime import datetime, date
from typing import Optional

from src.got.schemas import UnifiedContext
from src.got.archive import _is_bar_complete
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

    async def _maybe_capture_fxg_baseline(
        self,
        unified, market_ctx, sector_ctx, capital_ctx, news_ctx, sentiment_ctx,
        target_date: date,
    ) -> None:
        """FXG veto 双跑：额外跑一次 FXG-off 基线 `_score_candidates`，落 fxg_baseline_scoring.json。

        纯观测影子：捕获"FXG 若不生效、系统本会怎么选"的原模式评分，供一周后对照补位反事实。
        仅当 GATE_ENABLED=1 且 EXCLUDE_ENABLED=1 且 EXCLUDE_MODE=veto 且
        EXCLUDE_BASELINE_SHADOW=1 才触发；缺省全关 → 单跑，行为与现状逐字节一致。
        关键：真实 run() 处于 orchestrator 外层 try（except 会落 status=failed 占位），
        故本 helper 自带 try/except 吞掉一切基线侧异常、绝不 raise，纯观测不得击穿实盘决策。
        """
        if os.getenv("GOT_FAMILY_TOPPING_GATE_ENABLED", "0") != "1":
            return
        if os.getenv("GOT_FAMILY_TOPPING_EXCLUDE_ENABLED", "0") != "1":
            return
        if os.getenv("GOT_FAMILY_TOPPING_EXCLUDE_MODE", "shadow") != "veto":
            return
        if os.getenv("GOT_FAMILY_TOPPING_EXCLUDE_BASELINE_SHADOW", "0") != "1":
            return
        try:
            from pathlib import Path
            from src.got.nodes.portfolio_decision import PortfolioDecisionNode

            # 独立实例：隔离 _fxg_shadow/_shadow_* 内存态；打桩 candidate_pool 写，绝不 clobber 正式 veto 版。
            baseline_node = PortfolioDecisionNode(use_mock=False)
            baseline_node._save_candidate_pool_snapshot = lambda *a, **k: None
            # 基线跑置于 FXG off（=原模式；FTS 仍 shadow 不改决策）。顺序 await，env 隔离不泄漏。
            prev = os.environ.get("GOT_FAMILY_TOPPING_EXCLUDE_ENABLED")
            os.environ["GOT_FAMILY_TOPPING_EXCLUDE_ENABLED"] = "0"
            try:
                baseline_scoring = await baseline_node._score_candidates(
                    sector_ctx, capital_ctx, news_ctx, market_ctx, unified,
                    sentiment_context=sentiment_ctx,
                )
            finally:
                if prev is None:
                    os.environ.pop("GOT_FAMILY_TOPPING_EXCLUDE_ENABLED", None)
                else:
                    os.environ["GOT_FAMILY_TOPPING_EXCLUDE_ENABLED"] = prev
            rows = [{
                "symbol": s.symbol, "name": s.name,
                "sector": s.sector_name, "family": s.family_name,
                "is_holding": s.is_holding,
                "action_recommendation": s.action_recommendation,
                "composite_score": s.composite_score,
            } for s in (baseline_scoring or [])]
            record = {"line": "fxg_baseline", "mode": "baseline_off",
                      "date": target_date.strftime("%Y%m%d"), "rows": rows}
            day_dir = Path("data/got_daily") / target_date.strftime("%Y%m%d")
            day_dir.mkdir(parents=True, exist_ok=True)
            PortfolioDecisionNode._atomic_write_json(day_dir / "fxg_baseline_scoring.json", record)
            logger.info("[FXG-baseline] 剔前原模式基线已捕获：%d 行 → fxg_baseline_scoring.json", len(rows))
        except Exception as exc:  # noqa: BLE001 — 纯观测，绝不冒泡到主链（否则真实 veto 决策被打成 failed）
            logger.warning("[FXG-baseline] 基线捕获失败（已忽略，不影响真实决策）: %s", exc)

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

        # Q4 audit: bar_complete 全流程只计算一次（与 Phase 2 同源），
        # 同步透传给 Node 4 催化剂链路，避免 P6 forward 记录恒为默认 True
        bar_complete = _is_bar_complete(target_date, datetime.now())
        logger.info(f"[GOT] bar_complete={bar_complete}")

        # Step 2: 并行执行两路
        tech_path_task = self._run_tech_path()
        data_path_task = self._run_data_path(bar_complete)

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

        # Step 2.5: Phase 4b 市场结构上下文构造（影子默认开，写入全局供 Node 6 只读消费）
        # 独立于 Node 2 再拉一次 mootdx 快筛（方案 A 的“独立信号”前提）；取数失败目回退 unknown。
        if not self.use_mock and (
            os.getenv("GOT_MARKET_THEME_SHADOW", "1") == "1"
            or os.getenv("GOT_MARKET_THEME_ENABLED", "0") == "1"
        ):
            try:
                from src.got.market_theme import build_theme_context
                await asyncio.to_thread(build_theme_context, self.sector_node.fetcher)
            except Exception as exc:  # noqa: BLE001 - 构造失败不阻断 GOT 主流程
                logger.warning("[GOT] 4b 市场结构构造失败（已忽略，下游回退 unknown）: %s", exc)

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

            # ── Phase 2: 统一决策节点 ──
            try:
                logger.info("[GOT] Phase 2: 开始统一决策")
                # FXG veto 双跑：先捕获"剔前原模式"基线（纯观测、自兜异常），随后真实 veto run 一次。
                await self._maybe_capture_fxg_baseline(
                    unified, market_ctx, sector_ctx, capital_ctx, news_ctx, sentiment_ctx, target_date,
                )
                from src.got.nodes.portfolio_decision import PortfolioDecisionNode

                decision_node = PortfolioDecisionNode(use_mock=False)
                decision = await decision_node.run(
                    unified_context=unified,
                    market_context=market_ctx,
                    sector_context=sector_ctx,
                    capital_context=capital_ctx,
                    news_context=news_ctx,
                    sentiment_context=sentiment_ctx,
                    bar_complete=bar_complete,
                    # 3-D 重放 harness：快照 entry/exit 参考日跟随目标日（生产当日二者相等，零行为变化）
                    as_of=target_date,
                )

                logger.info(
                    f"[GOT] Phase 2 完成 — 交易建议: {len(decision.allocation)} 条"
                )

                # 存档 Phase 2 决策
                try:
                    from src.got.archive import archive_phase2

                    archive_phase2(decision, run_date=target_date)
                except Exception as archive_exc:
                    logger.error(
                        "[GOT] Phase 2 决策存档失败: %s", archive_exc
                    )

            except Exception as exc:
                # Phase 2 失败不应阻断 Phase 1，但必须留下 status="failed" 占位产物，
                # 保证"跑过就有文件"，回测/影子线可区分"停牌无数据"与"节点崩了"
                logger.exception("[GOT] Phase 2 失败: %s", exc)
                try:
                    from src.got.archive import archive_phase2
                    from src.got.schemas import (
                        DataQuality,
                        PortfolioDecision,
                        PortfolioRiskAssessment,
                    )

                    fallback = PortfolioDecision(
                        status="failed",
                        risk_assessment=PortfolioRiskAssessment(
                            position_limit=0.0,
                            current_position_ratio=0.0,
                            constraint_reasoning=f"Phase 2 执行失败，未产生约束: {exc}",
                        ),
                        unified_scoring=[],
                        allocation=[],
                        summary="Phase 2 执行失败，当日无交易建议",
                        warnings=[f"Phase 2 失败: {exc}"],
                        reasoning=f"[failed] Phase 2 决策流水线异常: {exc}",
                        data_quality=DataQuality(
                            missing_fields=["phase2"],
                            degradation_summary=f"Phase 2 失败: {exc}",
                        ),
                    )
                    archive_phase2(fallback, run_date=target_date)
                    logger.info("[GOT] Phase 2 失败占位产物已落盘 (status=failed)")
                except Exception as fb_exc:
                    logger.error("[GOT] Phase 2 失败占位产物落盘也失败: %s", fb_exc)

            # P6: 催化剂兑现检测（每日运行，记录过期事件的兑现结果）
            try:
                from src.got.catalyst_realization import CatalystRealizationTracker
                tracker = CatalystRealizationTracker()
                realized = tracker.check_and_record_realization(target_date=target_date)
                if realized > 0:
                    logger.info(f"[GOT] P6 兑现检测: 新兑现 {realized} 个事件")
            except Exception as exc:
                logger.warning("[GOT] P6 兑现检测失败（已忽略）: %s", exc)

        return unified

    async def _run_tech_path(self) -> tuple:
        """技术分析路：串行依赖（大盘 → 板块 → 资金面）"""
        logger.info("[GOT] 技术分析路：开始")

        market_ctx = await self.market_node.run()
        logger.info(f"[GOT]   Node 1 完成 — 科技成长: {market_ctx.tech_growth.trend}")

        sector_ctx = await self.sector_node.run(market_context=market_ctx)
        if sector_ctx.status in ("degraded", "failed"):
            raise RuntimeError(
                f"[GOT] Node 2 板块分析失败（status={sector_ctx.status}），"
                "无法进行后续聚合与决策，中止执行"
            )
        logger.info(f"[GOT]   Node 2 完成 — 活跃主线: {len(sector_ctx.active_mainline)}")

        capital_ctx = await self.capital_node.run(
            market_context=market_ctx,
            sector_context=sector_ctx,
        )
        logger.info(f"[GOT]   Node 3 完成 — 机构态度: {capital_ctx.institutional_attitude}")

        return market_ctx, sector_ctx, capital_ctx

    async def _run_data_path(self, bar_complete: bool = True) -> tuple:
        """独立数据路：并行（消息面 ‖ 情绪面）"""
        logger.info("[GOT] 独立数据路：开始（并行）")

        news_ctx, sentiment_ctx = await asyncio.gather(
            self.news_node.run(bar_complete=bar_complete),
            self.sentiment_node.run(),
        )

        logger.info(f"[GOT]   Node 4 完成 — 消息面: {news_ctx.overall_impact}")
        logger.info(f"[GOT]   Node 5 完成 — 情绪: {sentiment_ctx.overall_sentiment}")

        return news_ctx, sentiment_ctx

    def _check_trading_day(self, check_date: date):
        """检查是否交易日（非交易日抛 NonTradingDayError）

        修复（2026-09-14）：原实现 `import TradingCalendar` 引用的类不存在，
        恒抛 ImportError 并被 `except ImportError` 吞掉 ⇒ 检查从未生效。
        改用模块级 `is_market_open()`（与 src/core/pipeline.py 同口径）。
        """
        try:
            from src.core.trading_calendar import is_market_open
        except ImportError:
            # 日历不可用时降级为「不阻断」，避免整条 GOT 链路不可用
            logger.warning("[GOT] 无法导入交易日历，跳过交易日检查")
            return

        if not is_market_open("cn", check_date):
            raise NonTradingDayError(f"{check_date} 非交易日，跳过分析")
