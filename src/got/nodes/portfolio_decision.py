# -*- coding: utf-8 -*-
"""
Phase 2: 统一决策节点（FINCON 风格的 Manager 角色）

架构评审（2026-08-03）：
- 原串行设计（持仓判断 → 选股 → 建议）存在循环依赖
- 改为统一决策节点（3 Step 封装在 1 个节点，1 次 LLM 调用）
- 学术依据：FINCON (NeurIPS 2024), RAPTOR (NeurIPS 2025)

流程：
1. 风险评估（约束设定）→ 仓位上限/止损线/板块限制
2. 统一打分（持仓 + 候选股一起评）→ 消除循环依赖
3. 组合优化（规则 + LLM 混合）→ 在约束下生成最优配置
"""

import asyncio
import json
import logging
import math
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.got.schemas import (
    CapitalContext,
    DataQuality,
    MarketContext,
    NewsContext,
    PortfolioDecision,
    PortfolioRiskAssessment,
    SectorContext,
    SentimentContext,
    TradeAllocation,
    UnifiedContext,
    UnifiedScoringResult,
)

logger = logging.getLogger(__name__)

# ── 板块角色参数（模块级：_assess_risk 与 _allocate 共用，避免两处各写一份魔法值）──
# 角色 → 仓位乘数（参与 sector_exposure_limits 公式；cooling 取最低档）
ROLE_MULTIPLIER: dict[str, float] = {"mainline": 1.0, "ambush": 0.55, "cooling": 0.3, "unknown": 0.55}
# 角色 → 单笔止损比例（埋伏 / 降温用更紧的止损）
ROLE_STOP_LOSS: dict[str, float] = {"mainline": 0.07, "ambush": 0.05, "cooling": 0.05, "unknown": 0.05}
# 角色未命中映射时的兜底（✅ 2026-09-26 决策 #1a 已拍板：保持主线取档，见
# docs/got/backlog.md §7 #1；「验证清空→unknown」与「非空未命中→mainline」口径
# 不一致为已知并被接受，再翻案需新证据，原审核报告 R2.5 关闭）
DEFAULT_SECTOR_ROLE = "mainline"


class PortfolioDecisionNode:
    """Phase 2 统一决策节点"""

    def __init__(self, use_mock: bool = False):
        self.use_mock = use_mock
        # 影子模式观测留痕（reasoning.md 末尾段落的数据源）：
        # 两条线的「跑没跑 / 为什么没跑 / 跑了得到什么」都记在这里，run() 末尾落盘。
        self._shadow_t1: Dict[str, Any] = {}
        self._shadow_t1h: Dict[str, Any] = {}
        # 审核修复 3.3/S1/S2：板块验证降级状态 / 名称兜底缓存 / 旁路补选板块
        self._sector_verify_degraded = False
        self._sector_verify_pending_count = 0
        self._stock_name_cache: Dict[str, str] = {}
        self._bypass_ambush_sectors: set = set()

    async def run(
        self,
        unified_context: UnifiedContext,
        market_context: MarketContext,
        sector_context: SectorContext,
        capital_context: CapitalContext,
        news_context: NewsContext,
        sentiment_context: SentimentContext,
        bar_complete: bool = True,
    ) -> PortfolioDecision:
        """
        主入口：统一决策流水线

        Args:
            unified_context: Phase 1 聚合输出
            market_context: Node 1 大盘分析
            sector_context: Node 2 板块分析
            capital_context: Node 3 资金面
            news_context: Node 4 消息面
            sentiment_context: Node 5 情绪面

        Returns:
            PortfolioDecision: 交易建议 + reasoning
        """
        logger.info("[Phase 2] 开始统一决策")

        # 获取当前持仓
        portfolio = self._get_portfolio_snapshot()

        # 提取情绪动量（用于轮动时机检测）
        sentiment_momentum = getattr(sentiment_context, 'sentiment_momentum', 0.0) or 0.0

        # Step 1: 风险评估（约束设定）
        logger.info("[Phase 2] Step 1: 风险评估")
        risk_assessment = self._assess_risk(unified_context, portfolio)

        # Step 2: 统一打分（持仓 + 候选股）
        logger.info("[Phase 2] Step 2: 统一打分")
        holdings_scores = self._score_holdings(
            portfolio, unified_context, sector_context, capital_context, news_context,
            risk_assessment,
        )
        candidates_scores = await self._score_candidates(
            sector_context, capital_context, news_context, market_context, unified_context,
            sentiment_context=sentiment_context,
        )

        # 去重：已在持仓的股票不出现在候选买入列表
        holding_symbols = {s.symbol for s in holdings_scores}
        candidates_scores = [
            s for s in candidates_scores if s.symbol not in holding_symbols
        ]

        unified_scoring = holdings_scores + candidates_scores
        unified_scoring.sort(key=lambda x: x.composite_score, reverse=True)

        logger.info(
            f"[Phase 2] 统一打分完成: "
            f"{len(holdings_scores)} 只持仓 + {len(candidates_scores)} 只候选"
        )

        # Step 2.5: T1-H 持仓审查（§15.4，Agent 降档权）
        # 审核修复 1.2：定向降级——审查失败保留原评分，不击穿整条流水线
        degraded_steps: list[str] = []
        try:
            holdings_scores = await self._agent_holding_review(
                holdings_scores, unified_context, news_context, portfolio,
            )
        except Exception as e:
            logger.warning(f"[Phase 2] Step 2.5 持仓审查失败（保留原评分）: {e}")
            degraded_steps.append(f"Step2.5 持仓审查: {e}")
        # 重新组装（持仓评分可能因降档而变化）
        unified_scoring = holdings_scores + candidates_scores
        unified_scoring.sort(key=lambda x: x.composite_score, reverse=True)

        # Step 3: 组合优化（规则 + LLM 混合）
        # 审核修复 1.2：失败时降级为空 allocation + status=partial，保证产物落盘
        try:
            allocation, vetoed_candidates = self._allocate(unified_scoring, risk_assessment, portfolio, unified_context, bar_complete=bar_complete)
        except Exception as e:
            logger.error(f"[Phase 2] Step 3 组合优化失败，降级为空 allocation: {e}")
            allocation, vetoed_candidates = [], []
            degraded_steps.append(f"Step3 组合优化: {e}")

        # Step 4: 轮动时机检测 + 家族拥挤检查（吃鱼身+高切低策略）
        logger.info("[Phase 2] Step 4: 轮动检测 + 拥挤检查")
        rotation_signals = self._detect_rotation_opportunity(
            holdings_scores, unified_context, sentiment_momentum
        )
        crowding_warnings = self._check_family_crowding(holdings_scores)

        # P4：收集埋伏退出信号（_score_holdings 中填充）
        ambush_exit_signals = getattr(self, '_ambush_exits', [])
        if ambush_exit_signals:
            logger.info(f"[Phase 2] P4 埋伏退出信号: {len(ambush_exit_signals)} 条")
            for exit_info in ambush_exit_signals:
                logger.info(
                    f"  [P4 退出] {exit_info['symbol']} "
                    f"({exit_info['sector']}/{exit_info['category_tag']}): "
                    f"{exit_info['exit_signal']} — {exit_info['reason']}"
                )
            # 关闭已退出的快照（is_open=False）
            self._close_ambush_snapshots(ambush_exit_signals, portfolio)

        # Fix 2: 持仓论点快照写入（买入 + 漂移观察）
        thesis_buys, thesis_failed = self._write_thesis_snapshots(
            allocation, unified_scoring, unified_context, sector_context,
        )
        if thesis_buys:
            logger.info(f"[Phase 2] Fix 2 持仓论点快照：{len(thesis_buys)} 条")
        if thesis_failed:
            logger.warning(f"[Phase 2] Fix 2 持仓论点快照写入失败：{thesis_failed}")

        # P4：埋伏仓买入快照写入（买入催化剂快照）
        ambush_buys, ambush_failed = self._write_ambush_buy_snapshots(
            allocation, unified_scoring, unified_context,
        )
        if ambush_buys:
            logger.info(f"[Phase 2] P4 埋伏买入快照：{len(ambush_buys)} 条")
        if ambush_failed:
            logger.warning(f"[Phase 2] P4 埋伏快照写入失败：{ambush_failed}")
        
        # Fix 2: 关闭已卖出的持仓论点快照
        self._close_thesis_snapshots(allocation, portfolio)

        # LLM 精炼（生成可追溯的推理总结）
        # 审核修复 1.2：精炼失败不阻断产物，回退规则摘要
        try:
            reasoning = await self._llm_refinement(
                risk_assessment, unified_scoring, allocation, unified_context, portfolio,
                vetoed_candidates=vetoed_candidates,
            )
        except Exception as e:
            logger.warning(f"[Phase 2] LLM 精炼失败，回退规则摘要: {e}")
            reasoning = f"[LLM 精炼不可用: {e}] 当日决策以规则链路结果为准，见 summary 与 unified_scoring。"
            degraded_steps.append(f"LLM 精炼: {e}")

        # 生成操作总结
        summary = self._generate_summary(allocation, risk_assessment, vetoed_candidates)

        # 生成风险提示
        warnings = self._generate_warnings(allocation, risk_assessment, unified_context)
        
        # Q4 audit: 盘中快照警告（bar_complete=False 时追加）
        # 审核修复 S3：append 到末尾，不再 insert(0) 顶掉既有 warnings[0] 的位置契约
        if not bar_complete:
            intraday_count = sum(1 for a in allocation if a.intraday_snapshot)
            if intraday_count > 0:
                warnings.append(f"⚠️ 盘中快照警告：{intraday_count} 条交易建议基于盘中数据（bar_complete=false），价格与止损可能失真")

        # 审核修复 1.2：降级步骤透出到 warnings，状态标记 partial
        if degraded_steps:
            warnings.extend(f"⚠️ 降级：{step}" for step in degraded_steps)

        # D-P12: 落盘板块暴露绑定信息（口径声明 + 绑定次数）
        # 绑定次数为「可观测留痕」，不参与任何决策（D-P13：分支由 sector_role 决定，
        # sector_exposure_limits 仅参考值）——从已生成的 allocation 反推，无需改动分配循环。
        sector_limits = risk_assessment.sector_exposure_limits
        sum_limits = sum(sector_limits.values()) if sector_limits else 0.0
        position_limit = risk_assessment.position_limit
        buy_allocations = [a for a in allocation if a.action == "buy"]
        # binding_count：买入分配中命中 sector_exposure_limits 显式条目（非 0.20 兜底）的次数（不归一化）
        binding_count = sum(
            1
            for a in buy_allocations
            if a.sector_limit_detail
            and (a.sector_limit_detail.get("sector_name") or "") in (sector_limits or {})
        )
        sector_exposure_binding = {
            "sum_limits": round(sum_limits, 4),
            "position_limit": round(position_limit, 4),
            "ratio": round(sum_limits / position_limit, 2) if position_limit > 0 else 0.0,
            "buy_allocation_count": len(buy_allocations),
            "binding_count": binding_count,
            "note": "确信度乘数非敞口上限，只乘不除，与板块数无关；"
                    "binding_count 仅为可观测留痕（命中显式 limit 的买入次数），不参与决策",
        }

        # 审核修复 W9/3.3：决策审计字段（回测据此识别口径断点与降级日）
        data_quality = DataQuality(
            sector_available_false_count=sum(
                1 for s in unified_scoring if not getattr(s, "sector_available", True)
            ),
            sector_vetoed_count=sum(
                1 for v in vetoed_candidates if not getattr(v, "sector_available", True)
            ),
            sector_verify_degraded=True if self._sector_verify_degraded else None,
            sector_verify_pending_count=self._sector_verify_pending_count or None,
        )

        decision = PortfolioDecision(
            status="partial" if degraded_steps else "normal",
            risk_assessment=risk_assessment,
            unified_scoring=unified_scoring,
            allocation=allocation,
            summary=summary,
            warnings=warnings,
            reasoning=reasoning,
            rotation_signals=rotation_signals,
            crowding_warnings=crowding_warnings,
            ambush_exit_signals=ambush_exit_signals,
            mainline_endurance=getattr(unified_context, "mainline_endurance", []),
            sector_exposure_binding=sector_exposure_binding,
            # P1-4/OPEN-1 W2 卖出来源观测留痕（批 4.1）：影子/生效模式由 _allocate 落 self，
            # 仅留痕不参与决策（shadow 模式 allocation 本身零改动）
            sell_source_shadow=getattr(self, "_sell_source_shadow", None),
            # Phase 3b / RiskOverridePlan 观测留痕（批 5 §3.3）：shadow 不改 buy_budget，
            # enabled+veto_buy 时与 over_limit_forced 并列归零预算；由 _allocate 落 self。
            risk_override_shadow=getattr(self, "_risk_override_shadow", None),
            # GATE-1 可成交性投影留痕（实施报告 §2）：shadow 打标/enabled 顺延均记，
            # 仅留痕不参与判定（allocation 级别的 pending_exit 才是决策投影）
            tradability_shadow=getattr(self, "_tradability_pending", None),
            # WI-P0-3/P0-4 shadow 留痕（实施报告 §3）：敞口截断/信号加权均为纯观测，
            # 不改任何 allocation 决策（由 _allocate 落 self）
            exposure_enforce_shadow=getattr(self, "_exposure_enforce_shadow", None),
            signal_size_shadow=getattr(self, "_signal_size_shadow", None),
            data_quality=data_quality,
        )

        # 影子观测落盘（A5 的 agent_glue.json 由 Node 6 自行落盘，此处补 T1 / T1-H 两条线）
        self._save_shadow_records()

        logger.info(
            f"[Phase 2] 完成 — 交易建议: {len(allocation)} 条, "
            f"状态: {decision.status}"
        )

        return decision

    # ── Step 1: 风险评估 ──

    def _assess_risk(
        self, unified: UnifiedContext, portfolio: Dict[str, Any]
    ) -> PortfolioRiskAssessment:
        """从 Phase 1 输出提取约束条件（利用 Node 6 v3 的 market_regime）"""
        # 仓位上限：Phase 1 的 final_position_limit
        position_limit = unified.final_position_limit

        # 当前仓位
        total_equity = portfolio.get("total_equity", 1.0)
        total_market_value = portfolio.get("total_market_value", 0.0)
        current_position = total_market_value / total_equity if total_equity > 0 else 0.0

        # 回撤保护：根据当前回撤动态调整仓位上限
        current_drawdown = self._estimate_current_drawdown(portfolio)
        drawdown_scaled_limit = self._apply_drawdown_scaling(
            current_drawdown, position_limit
        )
        position_limit = drawdown_scaled_limit

        # 止损阈值：基于 market_regime 动态调整
        # bear → -5%（更严格）, transition → -7%, bull → -10%（更宽松）
        regime = unified.market_regime.regime if unified.market_regime else "transition"
        if regime == "bear":
            stop_loss = -0.05
        elif regime == "bull":
            stop_loss = -0.10
        else:  # transition
            stop_loss = -0.07

        # 板块暴露限制：动态计算（参考 Conviction-Tier + 序贯投资理论）
        # 上限 = base × regime乘数 × role乘数 × confidence × 催化剂类型系数（P4）
        # 效果：bull+主线+高确信 ≈ 36%，bear+埋伏+binary ≈ 2%
        #
        # D-P12: 本字段是「确信度乘数」非「敞口上限」。
        # 公式 base × regime × role × conf × catalyst 只乘不除，与板块数无关。
        # 实测 sum(limits) / position_limit 比值区间 1.92-7.81 倍。
        #
        # 新版分类（TOP-N + conviction sizing）：
        # - actionable: 排名靠前，可选股（主线 100% 仓位，埋伏 50% 仓位）
        # - caution: 排名靠后但有潜力，缩小仓位（主线 30%，埋伏 15%）
        # - exclude: 不选股
        REGIME_MULTIPLIER = {"bull": 1.0, "transition": 0.65, "bear": 0.35}
        # ROLE_MULTIPLIER 已提为模块级常量（_allocate 同样需要，避免两处各写一份）
        # P4 催化剂类型系数（文献：Yee 2025, 二元事件限仓）
        # drift: 持有吃事件后漂移，给得大
        # fade: 事件日即退，持有期短
        # binary: 高不确定，文献"不要赌，赌则限仓"
        CATALYST_TYPE_COEFF = {"drift": 1.0, "fade": 0.7, "binary": 0.4}

        # 构建埋伏板块 → 催化剂类型映射（P4：消费 ambush_pool 的 category_tag）
        ambush_type_map: Dict[str, str] = {}
        for candidate in unified.ambush_pool:
            ambush_type_map[candidate.sector_name] = candidate.category_tag

        sector_limits = {}
        if unified.confirmed_sectors:
            regime_m = REGIME_MULTIPLIER.get(regime, 0.65)
            for sector_intel in unified.confirmed_sectors:
                sector_name = sector_intel.sector_name
                classification = sector_intel.classification
                role = getattr(sector_intel, "sector_role", "mainline")
                role_m = ROLE_MULTIPLIER.get(role, 0.55)
                conf_w = sector_intel.confidence

                # 新分类映射（conviction-weighted sizing）
                if classification == "actionable":
                    base = 0.40 if role == "mainline" else 0.20  # 主线 100%，埋伏 50%
                elif classification == "caution":
                    base = 0.12 if role == "mainline" else 0.06  # 主线 30%，埋伏 15%
                elif classification == "exclude":
                    base = 0.0
                else:  # fallback
                    base = 0.10

                # P4 催化剂类型系数：仅对埋伏板块生效
                catalyst_coeff = 1.0
                if role == "ambush" and sector_name in ambush_type_map:
                    catalyst_coeff = CATALYST_TYPE_COEFF.get(
                        ambush_type_map[sector_name], 0.7
                    )

                sector_limits[sector_name] = round(
                    base * regime_m * role_m * conf_w * catalyst_coeff, 3
                )

        # 风险预算：基于 market_regime 的 risk_budget
        # risk_budget 范围 [0.2, 0.8]，映射到实际风险预算 [0.03, 0.08]
        if unified.market_regime:
            risk_budget = 0.03 + (unified.market_regime.risk_budget - 0.2) * 0.05 / 0.6
        else:
            risk_budget = 0.05

        constraint_reasoning = (
            f"市场状态={regime}, "
            f"仓位上限 {position_limit:.0%}"
        )
        if current_drawdown > 0.05:
            constraint_reasoning += f" (回撤保护: {current_drawdown:.1%} → 缩放后 {drawdown_scaled_limit:.0%})"
        constraint_reasoning += (
            f", 当前仓位 {current_position:.0%}, "
            f"止损线 {stop_loss:.0%}, "
            f"单笔风险预算 {risk_budget:.1%}（派生展示值，当前未接线）"
        )

        return PortfolioRiskAssessment(
            position_limit=position_limit,
            current_position_ratio=min(1.0, current_position),  # 限制在 0-1
            # OPEN-D9/P2-3 观测留痕（批 3.1）：落盘 pre-clip 原值，不参与任何判定
            raw_position_ratio=round(current_position, 4),
            stop_loss_threshold=stop_loss,
            sector_exposure_limits=sector_limits,
            risk_budget=risk_budget,
            constraint_reasoning=constraint_reasoning,
        )

    def _estimate_current_drawdown(self, portfolio: Dict[str, Any]) -> float:
        """
        估计当前回撤幅度

        从持仓数据中计算：(当前净值 - 峰值净值) / 峰值净值

        Args:
            portfolio: 持仓快照

        Returns:
            drawdown: 0.0 ~ 1.0（0% ~ 100%）
        """
        try:
            # 尝试从持仓数据中获取峰值和当前净值
            total_equity = portfolio.get("total_equity", 0.0)
            if total_equity <= 0:
                return 0.0

            # 简化版：使用持仓盈亏比例估计回撤
            # 实际应该从历史净值序列中计算，这里用持仓的平均盈亏代替
            accounts = portfolio.get("accounts", [])
            if not accounts:
                return 0.0

            positions = accounts[0].get("positions", [])
            if not positions:
                return 0.0

            # 计算持仓的平均盈亏比例
            total_cost = sum(p.get("avg_cost", 0) * p.get("quantity", 0) for p in positions)
            total_market_value = sum(p.get("last_price", 0) * p.get("quantity", 0) for p in positions)

            if total_cost <= 0:
                return 0.0

            profit_ratio = (total_market_value - total_cost) / total_cost

            # 如果盈利，回撤为 0；如果亏损，回撤 = |亏损比例|
            return max(0.0, -profit_ratio)

        except Exception as e:
            logger.warning(f"估计回撤失败: {e}")
            return 0.0

    def _apply_drawdown_scaling(
        self,
        current_drawdown: float,
        base_position: float,
    ) -> float:
        """
        回撤分级缩放

        参考：Hyper Trading "Kelly Criterion Position Sizing"

        核心思想：根据当前回撤幅度动态降低仓位上限

        Args:
            current_drawdown: 当前回撤幅度（0 ~ 1）
            base_position: 基础仓位上限

        Returns:
            scaled_position: 回撤调整后的仓位上限
        """
        if current_drawdown < 0.05:
            # 0-5% 回撤：无调整
            multiplier = 1.0
        elif current_drawdown < 0.10:
            # 5-10% 回撤：线性减到 75%
            progress = (current_drawdown - 0.05) / 0.05
            multiplier = 1.0 - progress * 0.25
        elif current_drawdown < 0.15:
            # 10-15% 回撤：75% → 50%
            progress = (current_drawdown - 0.10) / 0.05
            multiplier = 0.75 - progress * 0.25
        else:
            # >15% 回撤：固定 50%
            multiplier = 0.5

        scaled = base_position * multiplier

        # 最低保留 20% 仓位（避免完全空仓）
        return max(0.2, scaled)

    # ── Step 2: 统一打分 ──

    @staticmethod
    def _resolve_holding_name(symbol: str, raw_name: Any) -> str:
        """P1-1 小尾巴（backlog §2）：持仓名缺失/等于代码时的消费侧兜底。

        上游个别持仓（如 600250/605566）的 `name` 为空串或直接等于代码，
        导致归档/日志展示成裸码。仅在 GOT 消费侧兜底（不动 `PortfolioService` 源头，
        源头被 webui 共享）：name 无计算副作用（仅展示/日志/归档），故只做显示回退。
        兜底源为离线股票索引 `get_index_stock_name`（进程内 lazy 缓存、无网络）；
        不可用时回退原行为（返回 symbol）。
        """
        name = (raw_name or "").strip() if isinstance(raw_name, str) else ""
        if name and name != symbol:
            return name
        try:
            from src.data.stock_index_loader import get_index_stock_name
            indexed = get_index_stock_name(symbol)
        except Exception:  # 兜底不得引入新异常路径
            indexed = None
        return indexed or name or symbol

    def _score_holdings(
        self,
        portfolio: Dict[str, Any],
        unified: UnifiedContext,
        sector_context: SectorContext,
        capital_context: CapitalContext,
        news_context: NewsContext,
        risk_assessment: PortfolioRiskAssessment,
    ) -> List[UnifiedScoringResult]:
        """对每只持仓评估"继续持有"的吸引力（接入 GOT 上下文 + Node 6 v3 板块情报）"""
        results = []
        # P4：重置埋伏退出记录（每次 _score_holdings 调用重新收集）
        self._ambush_exits = []

        accounts = portfolio.get("accounts", [])
        if not accounts:
            return results

        positions = accounts[0].get("positions", [])

        # 构建板块 → 强度映射（从 sector_context）
        sector_strength_map = self._build_sector_strength_map(sector_context)
        # P0-2 修复：sector_context.strength_lookup 覆盖全量板块（双保险）
        strength_lookup = getattr(sector_context, "strength_lookup", {}) or {}
        # Fix 1: frame_group_lookup 用于家族中继匹配
        frame_group_lookup = getattr(sector_context, "frame_group_lookup", {}) or {}

        # 构建板块情报映射（从 Node 6 v3 的 confirmed_sectors）
        sector_intel_map = self._build_sector_intel_map(unified)

        # 构建扩展生命周期映射（覆盖 exhausted_mainline/cooling_down 等持仓板块）
        # 确保持仓板块即使不在 confirmed_sectors 中，也能获取生命周期用于止损判定
        extended_lifecycle_map = self._build_extended_lifecycle_map(sector_context)

        # 资金面全局评分（从 capital_context）
        capital_overall = self._extract_capital_overall(capital_context)

        # 消息面全局评分（从 news_context）
        news_overall = self._extract_news_overall(news_context)

        # 批量获取持仓股的真实技术指标评分（替代 P&L 代理）
        tech_scores_real = self._batch_fetch_tech_scores(positions)

        # ── P4：加载埋伏持仓快照（用于退出信号检测）──
        ambush_snapshot_map = self._load_ambush_snaphots(positions)
        # 供 Step 2.5 T1-H 持仓审查复用完整催化剂快照（§15.4 P4 快照输入契约）
        self._ambush_snapshot_map = ambush_snapshot_map

        # Fix 2: 加载持仓论点快照
        thesis_map = self._load_holding_theses(positions)

        # Fix 3: 构建家族→锚点映射（漂移宽限需要）
        family_anchor_map = self._build_family_anchor_map(unified)

        # 批量计算资金面/消息面评分（复用 screener 的方法，避免重复实现）
        holding_candidates = []
        for position in positions:
            symbol = position.get("symbol", "")
            name = self._resolve_holding_name(symbol, position.get("name", symbol))
            stock_sector_info = self._find_stock_sector(
                symbol, unified,
                sector_strength_map=sector_strength_map,
                strength_lookup=strength_lookup,
            )
            sector_name = stock_sector_info.get("sector_name", "")
            holding_candidates.append({
                "code": symbol,
                "name": name,
                "sector": sector_name,
                "dde_net_pct": 0,  # 持仓无 DDE 数据，仅用 sector_flow
            })

        # 调用 screener 的批量评分方法
        from src.config import get_config
        from src.services.stock_screener import (
            SCORING_WEIGHTS,
            StockScreenerPipeline,
            weighted_score_with_redistribution,
        )
        _screener = StockScreenerPipeline(get_config())
        capital_scores, capital_avail = _screener._score_capital(
            holding_candidates, capital_context.model_dump(mode="python")
        )
        news_scores, news_avail = _screener._score_news(
            holding_candidates, news_context.model_dump(mode="python")
        )

        for position in positions:
            symbol = position.get("symbol", "")
            name = self._resolve_holding_name(symbol, position.get("name", symbol))
            pnl_pct = position.get("unrealized_pnl_pct", 0.0) or 0.0
            pnl_pct_decimal = pnl_pct / 100.0

            # 查找该股票所属板块（从 sector_context 的 confirmed_sectors 匹配）
            stock_sector_info = self._find_stock_sector(
                symbol, unified,
                sector_strength_map=sector_strength_map,
                strength_lookup=strength_lookup,
                frame_group_lookup=frame_group_lookup,
            )
            # Fix 2: 消费论点快照（fallback 路径）
            stock_sector_info = self._find_stock_sector_with_thesis(
                stock_sector_info, symbol, unified, family_anchor_map, thesis_map,
            )
            sector_name = stock_sector_info.get("sector_name", "")
            sector_type = stock_sector_info.get("sector_type", "")
            sector_strength = stock_sector_info.get("strength", 50.0)

            # 五维打分（v2 重构：缺失维度跳过 + 权重归一化）
            # 资金面/消息面：使用 screener 批量评分结果 + 可用性标记
            capital_score = capital_scores.get(symbol, 0.0)
            capital_has_data = capital_avail.get(symbol, False)
            news_score = news_scores.get(symbol, 0.0)
            news_has_data = news_avail.get(symbol, False)

            # 板块强度：从 sector_context 获取该股票所属板块的强度
            # 并利用 Node 6 v3 的 4 维验证进行调整
            sector_score = self._adjust_score_by_sector_intel(
                sector_strength, sector_name, sector_intel_map
            )

            # ── 过热降权（Part 1）：对板块维度施加乘性惩罚 ──
            # Fix 1-2: 中继/论点命中项用 relay_anchor_sector 查惩罚，不可逃逸过热
            overheat_lookup_name = (
                stock_sector_info.get("relay_anchor_sector") or sector_name
            )
            overheat_penalty = self._compute_overheat_penalty(
                overheat_lookup_name, sector_intel_map, unified,
            )
            if overheat_penalty < 1.0:
                logger.info(
                    f"[Phase 2] [过热降权] {symbol} 板块={sector_name} "
                    f"系数={overheat_penalty:.2f}"
                )
                sector_score *= overheat_penalty

            # 技术面：优先使用真实技术指标，降级为 P&L 代理
            tech_score = tech_scores_real.get(symbol)
            if tech_score is None:
                tech_score = self._estimate_tech_score(pnl_pct_decimal, unified)

            # 风险面：基于波动和板块类型推导
            risk_score = self._estimate_risk_score(pnl_pct_decimal, sector_type)

            # 缺失维度跳过 + 权重归一化（NBER: 缺失≠零分）
            dimension_scores = {
                "capital": capital_score,
                "news": news_score,
                "sector": sector_score,
                "tech": tech_score,
                "risk": risk_score,
            }
            dimension_available = {
                "capital": capital_has_data,
                "news": news_has_data,
                "sector": True,
                "tech": True,
                "risk": True,
            }
            composite, _actual_weights = weighted_score_with_redistribution(
                dimension_scores, dimension_available, SCORING_WEIGHTS
            )

            # 提取生命周期信息
            lifecycle_phase = None
            lifecycle_day = None
            overheat_severity = None
            family_name = None
            anchor_name = stock_sector_info.get("relay_anchor_sector")
            if sector_name and sector_name in sector_intel_map:
                intel = sector_intel_map[sector_name]
                lifecycle_phase = intel.get('lifecycle_phase')
                lifecycle_day = intel.get('lifecycle_day')
                overheat_severity = intel.get('overheat_severity')
                family_name = intel.get('frame_group')
            elif anchor_name and anchor_name in sector_intel_map:
                # Fix 1: 中继/论点命中，从锚点板块获取生命周期
                intel = sector_intel_map[anchor_name]
                lifecycle_phase = intel.get('lifecycle_phase')
                lifecycle_day = intel.get('lifecycle_day')
                overheat_severity = intel.get('overheat_severity')
                family_name = intel.get('frame_group')
            elif sector_name:
                # 板块不在 confirmed_sectors 中，尝试从 Node 2 全量列表获取生命周期
                # 使用 normalize_sector_name 做板块名归一化（同花顺→东财口径）
                from src.got.board_aliases import normalize_sector_name
                canon_name = normalize_sector_name(sector_name)
                ext_intel = extended_lifecycle_map.get(canon_name) or extended_lifecycle_map.get(sector_name)
                if ext_intel:
                    lifecycle_phase = ext_intel.get('lifecycle_phase')
                    lifecycle_day = ext_intel.get('lifecycle_day')
                    overheat_severity = ext_intel.get('overheat_severity')
                    family_name = ext_intel.get('frame_group')

            # 动作建议：生命周期驱动 + 板块踢出检测
            sector_strength = stock_sector_info.get("strength", 50.0)
            sector_role = stock_sector_info.get("sector_role", "")

            # 分层决策（方案 C：组合方案 + Invesco 2024 非对称门槛）
            thesis_drift = stock_sector_info.get("thesis_drift", False)
            if not sector_name:
                # Fix 3: 漂移 sell 分支接入宽限逻辑
                thesis = thesis_map.get(symbol)
                all_boards = stock_sector_info.get("all_belong_boards", [])
                action, grace_applied = self._drift_action_with_grace(
                    symbol, sector_name, sector_strength,
                    frame_group_lookup, family_anchor_map, thesis, date.today(),
                    drift_sector_name=all_boards[0] if all_boards else "",
                )
                if grace_applied:
                    reasoning += f", [家族宽限]"
            elif thesis_drift:
                # Fix 2: 板块漂移检测 — 论点板块与当日匹配不一致
                thesis = thesis_map.get(symbol)
                thesis_family = stock_sector_info.get("thesis_family", "")
                # 用论点家族查锚点强度，走宽限逻辑
                thesis_action, thesis_grace = self._drift_action_with_grace(
                    symbol, sector_name, sector_strength,
                    frame_group_lookup, family_anchor_map, thesis, date.today(),
                    drift_sector_name=sector_name,
                )
                if thesis_grace:
                    action = thesis_action
                    reasoning += f", [论点漂移宽限: {thesis_family}]"
                else:
                    action = "sell"
                    reasoning += f", [论点漂移卖出: {thesis_family}→{sector_name}]"
            elif lifecycle_phase is None:
                # 板块不在 confirmed_sectors 中（可能被踢出）
                # Invesco 2024 非对称门槛：持仓者降温期保护
                # 进入主线门槛高（60），踢出门槛低（40）→ 防止"卖在降温，买回在上攻"whipsaw
                if sector_strength < 40:
                    # 深度破位 → 卖出（真正弱势，不是降温）
                    action = "sell"
                elif sector_strength < 60:
                    # 轻度降温（40-60）→ 减仓但不强制卖出
                    # （给再启动快速通道留空间：FAST 信号触发后直接回主线）
                    action = "reduce"
                else:
                    # 强度尚可（>=60）→ 持有观察
                    action = "hold"
            elif sector_strength < 50:
                action = "sell"
            elif sector_role == "cooling":
                # 高位降温（前主线回调未破位）：持仓者 hold/reduce，不 force-sell
                # （2026-08-17 状态机校准：冷却板块不再被误标为弱势卖出）
                action = "hold"
            elif sector_role not in ("mainline", "ambush"):
                # Fix 3: 漂移 sell 分支接入宽限逻辑
                thesis = thesis_map.get(symbol)
                all_boards = stock_sector_info.get("all_belong_boards", [])
                action, grace_applied = self._drift_action_with_grace(
                    symbol, sector_name, sector_strength,
                    frame_group_lookup, family_anchor_map, thesis, date.today(),
                    drift_sector_name=all_boards[0] if all_boards else "",
                )
                if grace_applied:
                    reasoning += f", [家族宽限]"
            elif sector_role == "ambush" and symbol in ambush_snapshot_map:
                # P4 埋伏退出分支：催化剂驱动，替代生命周期逻辑
                snapshot = ambush_snapshot_map[symbol]
                last_price = position.get("last_price", 0.0)
                exit_signal, exit_reason = self._ambush_exit_signal(
                    snapshot, date.today(), last_price,
                )
                if exit_signal:
                    action = "sell"
                    ambush_exit_info = {
                        "symbol": symbol,
                        "sector": sector_name,
                        "category_tag": snapshot.category_tag,
                        "exit_signal": exit_signal,
                        "reason": exit_reason,
                    }
                    if not hasattr(self, '_ambush_exits'):
                        self._ambush_exits = []
                    self._ambush_exits.append(ambush_exit_info)
                else:
                    action = "hold"
            else:
                # 使用生命周期驱动逻辑（吃鱼身+高切低策略）
                action, _ = self._lifecycle_sell_signal(
                    pnl_pct_decimal,
                    lifecycle_phase,
                    lifecycle_day,
                    overheat_severity,
                    risk_assessment.stop_loss_threshold,
                )

            # 构建 reasoning，包含板块情报信息
            sector_intel_info = ""
            if sector_name and sector_name in sector_intel_map:
                intel = sector_intel_map[sector_name]
                sector_intel_info = (
                    f", 板块验证: {intel['classification']}"
                    f"(资金{intel['capital_check']}/消息{intel['news_check']}/情绪{intel['sentiment_check']})"
                )

            # 获取所有板块信息（用于 reasoning）
            all_sectors = stock_sector_info.get("all_sectors", [])
            all_belong_boards = stock_sector_info.get("all_belong_boards", [])
            is_fallback = stock_sector_info.get("is_fallback", False)

            # 构建板块情报信息
            sector_intel_info = ""
            if sector_name and sector_name in sector_intel_map:
                intel = sector_intel_map[sector_name]
                sector_intel_info = (
                    f"(资金{intel['capital_check']}/消息{intel['news_check']}/情绪{intel['sentiment_check']})"
                )

            # 构建多板块信息字符串
            in_intel_map = sector_name and sector_name in sector_intel_map
            if len(all_sectors) > 1:
                # 多个板块：显示加权平均和所有板块
                sectors_str = ", ".join(
                    f"{s['sector_name']}({s['strength']:.0f}分{'✓主线' if s['sector_type']=='active_mainline' else ''})"
                    for s in all_sectors[:3]  # 最多显示3个
                )
                sector_display = f"板块加权{sector_score:.0f}分[{sectors_str}]"
            elif sector_name and in_intel_map:
                # 正常匹配（含修复后的成员展开）
                intel = sector_intel_map[sector_name]
                cls_label = "✓主线" if intel.get('classification') == 'actionable' else "⚠"
                sector_display = f"板块[{sector_name}]强度{sector_score:.0f}{sector_intel_info} {cls_label}"
            elif sector_name:
                # 有板块名但不在 intel map 中
                if is_fallback:
                    boards_str = "/".join(all_belong_boards[:3]) if all_belong_boards else sector_name
                    sector_display = f"板块[{boards_str}]（未确认，强度{sector_score:.0f}）"
                else:
                    sector_display = f"板块[{sector_name}]强度{sector_score:.0f}"
            else:
                # 无板块匹配：尝试显示所属板块列表
                if all_belong_boards:
                    boards_str = "/".join(all_belong_boards[:3])
                    sector_display = f"板块[{boards_str}]（未在追踪范围）"
                else:
                    sector_display = "板块[未知]"

            # 增强 reasoning：标注板块踢出情况（仅在板块名确实不在 intel map 时触发）
            is_relay = stock_sector_info.get("is_relay", False)
            if lifecycle_phase is None and sector_name and not in_intel_map and not is_relay:
                lifecycle_info = "（板块已踢出）"
            elif lifecycle_phase:
                lifecycle_info = f"[{lifecycle_phase}]"
            else:
                lifecycle_info = ""

            # Fix 1: 中继展示分支
            relay_family = stock_sector_info.get("relay_family")
            anchor_strength = stock_sector_info.get("strength", 0)
            if is_relay and relay_family:
                anchor_name = stock_sector_info.get("relay_anchor_sector", "")
                sector_display = f"板块 [{sector_name}→家族{relay_family}·锚点{anchor_name}{anchor_strength:.0f}分]"

            reasoning = (
                f"持仓 P&L {pnl_pct:.1f}%, "
                f"{sector_display}{lifecycle_info}, "
                f"资金面{capital_score:.0f}"
                f"{'（无数据，已跳过）' if not capital_has_data else ''}, "
                f"动作: {action}"
            )

            # ── P4 埋伏退出字段（仅埋伏持仓有效）──
            ambush_position_type = None
            ambush_exit_signal = None
            ambush_exit_reason = None
            if symbol in ambush_snapshot_map:
                snap = ambush_snapshot_map[symbol]
                ambush_position_type = snap.category_tag
                # 查找是否在 _ambush_exits 中
                for exit_info in self._ambush_exits:
                    if exit_info["symbol"] == symbol:
                        ambush_exit_signal = exit_info["exit_signal"]
                        ambush_exit_reason = exit_info["reason"]
                        reasoning += f", [埋伏退出:{exit_info['exit_signal']}]"
                        break
                else:
                    reasoning += f", [埋伏持有:{snap.category_tag}]"

            # 板块分类（用于输出可观测性）
            sector_classification = None
            if sector_name and sector_name in sector_intel_map:
                sector_classification = sector_intel_map[sector_name].get('classification')

            # Fix 1-3: sector_match_type / drift_grace 填充
            is_relay = stock_sector_info.get("is_relay", False)
            relay_source = stock_sector_info.get("relay_source")
            if is_relay and relay_source == "thesis":
                sector_match_type = "thesis"
            elif is_relay:
                sector_match_type = "relay"
            elif stock_sector_info.get("is_fallback", False):
                sector_match_type = "fallback"
            elif sector_name and sector_name in sector_intel_map:
                sector_match_type = "direct"
            else:
                sector_match_type = "none"

            results.append(
                UnifiedScoringResult(
                    symbol=symbol,
                    name=name,
                    is_holding=True,
                    composite_score=composite,
                    dimension_scores={
                        "capital": capital_score,
                        "news": news_score,
                        "sector": sector_score,
                        "tech": tech_score,
                        "risk": risk_score,
                    },
                    action_recommendation=action,
                    confidence=0.6,
                    reasoning=reasoning,
                    sector_name=sector_name or None,
                    sector_classification=sector_classification,
                    lifecycle_phase=lifecycle_phase,
                    lifecycle_day=lifecycle_day,
                    overheat_severity=overheat_severity,
                    family_name=family_name,
                    sector_match_type=sector_match_type,
                    sector_drift_grace=grace_applied if 'grace_applied' in locals() else False,
                    ambush_position_type=ambush_position_type,
                    ambush_exit_signal=ambush_exit_signal,
                    ambush_exit_reason=ambush_exit_reason,
                )
            )

        return results

    def _build_sector_intel_map(
        self, unified: UnifiedContext
    ) -> Dict[str, Dict[str, Any]]:
        """从 Node 6 v3 的 confirmed_sectors 构建板块情报映射

        Returns:
            {sector_name: {
                'classification': 'actionable'/'caution'/'exclude',
                'composite_score': 75.0,
                'confidence': 0.9,
                'capital_check': 'aligned'/'neutral'/'divergent',
                'news_check': 'catalyzed'/'neutral'/'negative',
                'sentiment_check': 'high'/'medium'/'low',
                'tech_score': 80.0,
                'lifecycle_phase': 'launch'/'acceleration'/'mature'/'late_stage'/'mature_exhausted',
                'lifecycle_day': 15,
                'overheat_severity': 0.3,
                'family_name': 'AI硬件',
            }}
        """
        sector_intel_map = {}
        if not unified.confirmed_sectors:
            return sector_intel_map

        for sector_intel in unified.confirmed_sectors:
            # 提取生命周期信息
            lifecycle_phase = None
            lifecycle_day = None
            if sector_intel.lifecycle:
                lifecycle_phase = sector_intel.lifecycle.phase
                lifecycle_day = sector_intel.lifecycle.days_since_launch

            sector_intel_map[sector_intel.sector_name] = {
                'classification': sector_intel.classification,
                'composite_score': sector_intel.composite_score,
                'confidence': sector_intel.confidence,
                'capital_check': sector_intel.capital_check,
                'news_check': sector_intel.news_check,
                'sentiment_check': sector_intel.sentiment_check,
                'tech_score': sector_intel.tech_score,
                'sector_role': getattr(sector_intel, 'sector_role', 'mainline'),
                'lifecycle_phase': lifecycle_phase,
                'lifecycle_day': lifecycle_day,
                'overheat_severity': getattr(sector_intel, 'overheat_severity', None),
                'frame_group': getattr(sector_intel, 'frame_group', None),
            }

        return sector_intel_map

    def _build_extended_lifecycle_map(
        self, sector_context: SectorContext
    ) -> Dict[str, Dict[str, Any]]:
        """从 Node 2 全量板块列表构建扩展生命周期映射

        覆盖 confirmed_sectors 之外的板块（如 exhausted_mainline, cooling_down），
        确保持仓板块即使不在主线/埋伏列表中，也能获取生命周期信息用于止损判定。

        Returns:
            {sector_name: {
                'lifecycle_phase': 'mature'/'acceleration'/...,
                'lifecycle_day': 9,
                'overheat_severity': 0.03,
                'state': 'exhausted_mainline'/'high_position_cooling'/...,
                'strength_score': 61.4,
                'recommended_action': 'exit'/'hold'/...,
            }}
        """
        lifecycle_map = {}

        # 遍历所有板块列表，提取生命周期信息
        # 优先级：active_mainline > cooling_down > exhausted_mainline > ambush > defensive_rotation
        for group in (sector_context.active_mainline or []):
            for member in (group.members or []):
                if member.name and member.name not in lifecycle_map:
                    lifecycle_map[member.name] = self._extract_lifecycle_from_detail(member)
            # 组名也记录（最强成员代表）
            if group.group_name and group.group_name not in lifecycle_map:
                best = max(group.members, key=lambda m: m.strength_score) if group.members else None
                if best:
                    lifecycle_map[group.group_name] = self._extract_lifecycle_from_detail(best)

        for detail_list_attr in ('cooling_down', 'exhausted_mainline', 'ambush', 'defensive_rotation', 'top_by_score'):
            for detail in (getattr(sector_context, detail_list_attr, None) or []):
                if detail.name and detail.name not in lifecycle_map:
                    lifecycle_map[detail.name] = self._extract_lifecycle_from_detail(detail)

        return lifecycle_map

    @staticmethod
    def _extract_lifecycle_from_detail(detail) -> Dict[str, Any]:
        """从 SectorDetail 提取生命周期信息"""
        lifecycle_phase = None
        lifecycle_day = None
        if detail.lifecycle:
            lifecycle_phase = detail.lifecycle.phase
            lifecycle_day = detail.lifecycle.days_since_launch

        return {
            'lifecycle_phase': lifecycle_phase,
            'lifecycle_day': lifecycle_day,
            'overheat_severity': getattr(detail, 'overheat_severity', None),
            'state': getattr(detail, 'state', None),
            'strength_score': getattr(detail, 'strength_score', None),
            'recommended_action': getattr(detail, 'recommended_action', None),
            'frame_group': getattr(detail, 'frame_group', None),
        }

    def _adjust_score_by_sector_intel(
        self, base_score: float, sector_name: str, sector_intel_map: Dict[str, Dict[str, Any]]
    ) -> float:
        """根据板块情报调整股票评分

        调整逻辑（新版 conviction-weighted）：
        - actionable: 主线 +10, 埋伏 +8（2026-09-01 确定性提升效果强化：+5→+8）
        - caution: 主线 +3, 埋伏 +1（缩小但仍参与）
        - exclude: -20
        """
        if not sector_name:
            # 板块验证失败的兜底：中性 50，防止上游 sector_score（问财灌入的错误板块强度）
            # 原样透传。主路径依赖 dimension_available=False 剔除该维度，此值仅防御。
            return 50.0
        if sector_name not in sector_intel_map:
            return base_score

        intel = sector_intel_map[sector_name]
        adjustment = 0.0

        # classification 调整（区分主线/埋伏，conviction-weighted）
        classification = intel.get('classification', 'caution')
        role = intel.get('sector_role', 'mainline')

        if classification == 'actionable':
            if role == 'mainline':
                adjustment += 10.0  # 主线 actionable +10
            else:
                adjustment += 8.0   # 埋伏 actionable +8（提升与主线差距从 4→2）
        elif classification == 'caution':
            if role == 'mainline':
                adjustment += 3.0   # 主线 caution +3（缩小）
            else:
                adjustment += 1.0   # 埋伏 caution +1
        elif classification == 'exclude':
            adjustment -= 20.0

        # capital_check 调整
        capital_check = intel.get('capital_check', 'neutral')
        if capital_check == 'aligned':
            adjustment += 5.0
        elif capital_check == 'divergent':
            adjustment -= 10.0

        # news_check 调整
        news_check = intel.get('news_check', 'neutral')
        if news_check == 'catalyzed':
            adjustment += 5.0
        elif news_check == 'negative':
            adjustment -= 10.0

        # sentiment_check 调整
        # D-P4 后缺失态为 'missing'：为保持「D-P4 零分数变更」，缺失仍按 low 同档扣分
        sentiment_check = intel.get('sentiment_check', 'medium')
        if sentiment_check == 'high':
            adjustment += 3.0
        elif sentiment_check in ('low', 'missing'):
            adjustment -= 5.0

        # 应用调整，限制在 [0, 100]
        adjusted_score = max(0.0, min(100.0, base_score + adjustment))
        return adjusted_score

    @staticmethod
    def _compute_overheat_penalty(
        sector_name: str,
        sector_intel_map: Dict[str, Dict[str, Any]],
        unified: Any,
    ) -> float:
        """计算过热降权系数（乘性，作用于板块维度评分）

        三档降权：
        - severity > 0.5 → ×0.75（严重过热）
        - severity > 0.3 → ×0.90（过热警告）
        - severity <= 0.3 → ×1.0（正常）

        追加降权：
        - 板块 frame_group ∈ crowded_families → 追加 ×0.90（与上两档连乘）

        Returns:
            float: 降权系数 [0.75*0.9, 1.0] = [0.675, 1.0]
        """
        if not sector_name or sector_name not in sector_intel_map:
            return 1.0

        intel = sector_intel_map[sector_name]
        severity = intel.get('overheat_severity') or 0.0
        frame_group = intel.get('frame_group')

        # 基础降权
        if severity > 0.5:
            coefficient = 0.75
        elif severity > 0.3:
            coefficient = 0.90
        else:
            coefficient = 1.0

        # 家族拥挤追加降权
        if frame_group:
            crowded_families = getattr(unified, 'crowded_families', None) or []
            if frame_group in crowded_families:
                coefficient *= 0.90

        return coefficient

    def _build_sector_strength_map(
        self, sector_context: SectorContext
    ) -> Dict[str, float]:
        """构建板块名称 → 强度评分映射"""
        strength_map = {}
        # active_mainline 是 MainlineGroup，含 members
        for group in sector_context.active_mainline:
            for member in group.members:
                strength_map[member.name] = member.strength_score
        # ambush / exhausted / defensive / cooling 是 SectorDetail 列表
        for sd in sector_context.ambush:
            strength_map[sd.name] = sd.strength_score
        for sd in sector_context.exhausted_mainline:
            strength_map[sd.name] = sd.strength_score
        for sd in sector_context.defensive_rotation:
            strength_map[sd.name] = sd.strength_score
        for sd in sector_context.cooling_down:
            strength_map[sd.name] = sd.strength_score
        return strength_map

    def _extract_capital_overall(self, capital_context: CapitalContext) -> float:
        """从资金面上下文提取全局评分（0-100）"""
        # institutional_score 范围 [-1, 1]，映射到 [0, 100]
        inst_score = capital_context.institutional_score or 0.0
        return 50.0 + inst_score * 50.0  # -1→0, 0→50, 1→100

    def _extract_news_overall(self, news_context: NewsContext) -> float:
        """从消息面上下文提取全局评分（0-100）"""
        # impact_score 范围 [-1, 1]，映射到 [0, 100]
        score = news_context.impact_score or 0.0
        return 50.0 + score * 50.0

    @staticmethod
    def _strip_sector_reasoning(text: str) -> str:
        """剥离 ai_reasoning 中的板块强度段落，替换为验证未通过提示。

        screener `_generate_got_reasoning` 生成的段落格式：
            3. **板块强度** (xx/25):
               - 属于active_mainline板块「存储芯片」
               - 板块强度 94 分
            4. **技术面** ...

        删除从 ``3. **板块强度**`` 到下一个 ``4. `` 开头行之间的所有行，
        并在原位置插入一行提示。找不到标记时原样返回（防御性 no-op）。
        """
        if not text:
            return text
        lines = text.split("\n")
        start_idx = None
        end_idx = None
        for i, line in enumerate(lines):
            if line.startswith("3. **板块强度**"):
                start_idx = i
            elif start_idx is not None and line.startswith("4. "):
                end_idx = i
                break
        if start_idx is None:
            return text
        replacement = "3. **板块强度**（板块验证未通过，已剔除该维度）"
        new_lines = lines[:start_idx] + [replacement] + (lines[end_idx:] if end_idx is not None else [])
        return "\n".join(new_lines)

    def _resolve_missing_name(self, code: str, name: str) -> str:
        """审核修复 S1：name 退化为裸代码时回退查名（进程内缓存）。

        上游 _get_stock_name 查名失败会返回代码，裸代码混入决策产物
        （如 0908 name="600250"）；回退后仍无名称则告警并保留代码。
        """
        if not re.fullmatch(r"\d{6}", str(name or "")):
            return name or code
        if code in self._stock_name_cache:
            return self._stock_name_cache[code]
        resolved = ""
        try:
            from data_provider.base import DataFetcherManager
            resolved = DataFetcherManager().get_stock_name(code) or ""
        except Exception as e:
            logger.warning(f"[name兜底] {code} 查名异常: {e}")
        if resolved:
            self._stock_name_cache[code] = resolved
            return resolved
        logger.warning(f"[name兜底] {code} 名称缺失，产物保留裸代码")
        return name or code

    async def _verify_candidate_sectors(
        self,
        finalists: list,
        sector_intel_map: Dict[str, Dict],
    ) -> list:
        """候选股板块交叉验证（2026-08-18 新增）

        用 efinance.get_belong_board() 验证问财返回的板块标签是否真实。
        防止蹭概念票（如利君股份被问财错误归类为 TOPCon电）。

        验证逻辑（2026-09-01 升级为别名归一化 + LLM 兜底仲裁）：
        1. 对每只候选股，调用 efinance 获取其实际所属板块列表
        2. L1 别名归一化匹配：处理跨口径命名差异（如"券商" vs "券商概念"）
        3. L2 LLM 仲裁：L1 未命中时批量交由 LLM 判断是否同一概念，
           LLM 不可用时改走保守分支（审核修复 3.3：清空标签 +
           sector_available=False，进入 unknown 保守预算路径）
        4. 两者都未命中，清空板块标签（sector 维度按缺失处理，权重归一化剔除）

        注意：只在候选股数量较少时（≤15）执行，避免过多 API 调用。
        """
        if not finalists or len(finalists) > 15:
            return finalists

        from data_provider.base import DataFetcherManager
        from src.got.board_aliases import normalize_sector_name, sector_name_variants

        manager = DataFetcherManager()
        verified = []
        pending: List[Dict[str, Any]] = []   # 待 LLM 仲裁的 [{tagged, boards, stock}]

        for stock in finalists:
            tagged_sector = getattr(stock, 'sector', '') or getattr(stock, 'sector_name', '') or ''

            # 无板块标签或不在 sector_intel_map 中，跳过验证
            if not tagged_sector or tagged_sector not in sector_intel_map:
                verified.append(stock)
                continue

            try:
                # 调用 efinance 获取实际所属板块
                clean_code = stock.code.replace("SH", "").replace("SZ", "").replace("BJ", "")
                boards = manager.get_belong_boards(clean_code)

                if not boards:
                    # 获取失败，保留原标签（降级为信任问财）
                    logger.warning(
                        f"[板块验证] {stock.code} {stock.name}: "
                        f"efinance 获取失败，保留问财标签'{tagged_sector}'"
                    )
                    verified.append(stock)
                    continue

                # 提取板块名称集合
                actual_board_names = {
                    b.get("name", "") or b.get("board_name", "")
                    for b in boards
                }

                # L1 别名归一化匹配：同花顺口径（"券商概念"）↔ 东财口径（"券商"）
                tagged_canon = normalize_sector_name(tagged_sector)
                tagged_variants = sector_name_variants(tagged_sector)
                matched_board = next(
                    (b for b in actual_board_names
                     if b and (b in tagged_variants or normalize_sector_name(b) == tagged_canon)),
                    None,
                )

                if matched_board:
                    # 验证通过（精确或别名命中）
                    logger.debug(
                        f"[板块验证] {stock.code} {stock.name}: "
                        f"✓ '{tagged_sector}' 命中 efinance 板块 '{matched_board}'"
                    )
                    verified.append(stock)
                else:
                    # L1 未命中 → 收集待 LLM 仲裁，不立即清空
                    pending.append({
                        "tagged": tagged_sector,
                        "boards": sorted(b for b in actual_board_names if b),
                        "stock": stock,
                    })

            except Exception as e:
                # 验证过程出错，保留原标签（降级为信任问财）
                logger.warning(
                    f"[板块验证] {stock.code} {stock.name}: "
                    f"验证出错({e})，保留问财标签'{tagged_sector}'"
                )
                verified.append(stock)

        # L2: LLM 批量仲裁（同一(标签,板块列表)只问一次）
        if pending:
            case_index: Dict[tuple, int] = {}
            unique_cases: List[Dict[str, Any]] = []
            for item in pending:
                key = (item["tagged"], tuple(item["boards"]))
                if key not in case_index:
                    case_index[key] = len(unique_cases)
                    unique_cases.append({"tagged": item["tagged"], "boards": item["boards"]})

            arbitration = await self._llm_arbitrate_sector_match(unique_cases)

            if arbitration is None:
                # 审核修复 3.3：LLM 不可用时不再静默信任问财——蹭概念票会绕过
                # sector_available 全套守卫直接 BUY（0922 新亚制程教训）；改走保守
                # 分支：清空板块标签 → 维度权重归一剔除 + action 强制 watch +
                # unknown 保守预算；降级态显式透出到产物 data_quality
                logger.warning(
                    f"[板块验证] LLM 仲裁不可用，{len(pending)} 只待验证股改走保守处理"
                    f"（清空板块标签，禁入买入池，data_quality 标记降级）"
                )
                self._sector_verify_degraded = True
                self._sector_verify_pending_count = len(pending)
                for item in pending:
                    stock = item["stock"]
                    if hasattr(stock, 'sector'):
                        stock.sector = ""
                    if hasattr(stock, 'sector_name'):
                        stock.sector_name = ""
                    stock.sector_available = False
                    if hasattr(stock, 'ai_reasoning'):
                        stock.ai_reasoning = self._strip_sector_reasoning(
                            getattr(stock, 'ai_reasoning', '') or ''
                        )
                    verified.append(stock)
            else:
                for item in pending:
                    case_idx = case_index[(item["tagged"], tuple(item["boards"]))]
                    stock = item["stock"]
                    matched_board = arbitration.get(case_idx)
                    if matched_board:
                        logger.info(
                            f"[板块验证] {stock.code} {stock.name}: "
                            f"LLM 仲裁 ✓ '{item['tagged']}' ≈ '{matched_board}'，保留标签"
                        )
                        verified.append(stock)
                    else:
                        logger.warning(
                            f"[板块验证] {stock.code} {stock.name}: "
                            f"✗ 问财标签'{item['tagged']}'与 efinance 板块{item['boards'][:5]}...不匹配，"
                            f"清空板块标签"
                        )
                        # 清空板块标签：sector 维度按缺失处理（权重归一化剔除，非零分非中性分）
                        if hasattr(stock, 'sector'):
                            stock.sector = ""
                        if hasattr(stock, 'sector_name'):
                            stock.sector_name = ""
                        # 显式标志，供打分循环 dimension_available["sector"] 消费
                        stock.sector_available = False
                        # 剥离 ai_reasoning 中的错误板块段落，防止陈旧文本透传到 reasoning.md
                        if hasattr(stock, 'ai_reasoning'):
                            stock.ai_reasoning = self._strip_sector_reasoning(
                                getattr(stock, 'ai_reasoning', '') or ''
                            )
                        verified.append(stock)

        logger.info(
            f"[板块验证] 完成: {len(finalists)} 只候选股，"
            f"{len([s for s in verified if not (getattr(s, 'sector', '') or getattr(s, 'sector_name', ''))])} 只被清空标签"
        )

        return verified

    async def _llm_arbitrate_sector_match(
        self,
        cases: List[Dict[str, Any]],
    ) -> Optional[Dict[int, Optional[str]]]:
        """LLM 仲裁：判断问财标签板块与 efinance 板块列表中某个板块是否同一概念。

        Args:
            cases: [{"tagged": 问财标签板块名, "boards": [efinance 实际板块列表]}]

        Returns:
            {case 索引: 命中的板块名或 None}；LLM 不可用/解析失败时返回 None，
            由调用方改走保守分支（审核修复 3.3，不再信任问财）。
        """
        if not cases:
            return {}

        from src.agent.llm_adapter import LLMToolAdapter

        lines = []
        for i, case in enumerate(cases):
            boards_text = ", ".join(case["boards"][:40])
            lines.append(f'{i}. 标签板块: "{case["tagged"]}" | 实际所属板块: [{boards_text}]')
        cases_text = "\n".join(lines)

        prompt = f"""你是 A 股板块分类专家。判断每个"问财标签板块"与该股"实际所属板块列表"中某个板块是否为同一概念。

## 待判断案例
{cases_text}

## 判断规则
- 同一概念的命名差异算命中，如"券商"与"券商概念"、"半导体"与"半导体概念"
- 相关但不同的概念不算命中，如"券商"与"参股券商"、"光伏"与"光伏建筑一体化"
- matched_board 必须是该案例实际所属板块列表中的原文之一，不得自行构造名称，无命中则输出 null
- 只输出 JSON 数组，不要其他文字：
[{{"index": 案例编号, "matched_board": "命中的板块名或 null"}}]"""

        try:
            adapter = LLMToolAdapter()
            response = adapter.call_text(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=1000,
                timeout=30,
            )
            if not response or not getattr(response, "content", None):
                logger.warning("[板块验证] LLM 仲裁返回空")
                return None

            import re
            match = re.search(r'\[[\s\S]*\]', response.content)
            if not match:
                logger.warning("[板块验证] LLM 仲裁无法提取 JSON 数组")
                return None

            items = json.loads(match.group())
            if not isinstance(items, list):
                return None

            result: Dict[int, Optional[str]] = {i: None for i in range(len(cases))}
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    idx = int(item.get("index"))
                except (TypeError, ValueError):
                    continue
                if not 0 <= idx < len(cases):
                    continue
                board = str(item.get("matched_board") or "").strip()
                # 防幻觉：命中名必须存在于该案例的板块列表中，否则丢弃（matched_board 可能是字符串 "null"）
                if board and board != "null" and board in cases[idx]["boards"]:
                    result[idx] = board
            return result

        except Exception as e:
            logger.warning(f"[板块验证] LLM 仲裁失败({e})，降级为信任问财")
            return None

    def _get_sector_capital_score(
        self, sector_name: str, sector_intel_map: Dict[str, Dict], global_capital: float
    ) -> float:
        """获取板块级别资金面评分（0-100）

        优先使用 Node 6 的板块情报（capital_check），否则回退到全局评分。
        """
        if sector_name and sector_name in sector_intel_map:
            intel = sector_intel_map[sector_name]
            capital_check = intel.get('capital_check', 'neutral')
            if capital_check == 'aligned':
                return 80.0  # 资金流入
            elif capital_check == 'divergent':
                return 30.0  # 资金流出
            # neutral → 回退到全局
        return global_capital

    def _get_sector_news_score(
        self, sector_name: str, sector_intel_map: Dict[str, Dict], global_news: float
    ) -> float:
        """获取板块级别消息面评分（0-100）

        ⚠️ 已弃用：只有 3 值离散评分。改用 _score_single_stock_news() 做事件级匹配。
        保留作为 fallback（news_context 无事件时）。
        """
        if sector_name and sector_name in sector_intel_map:
            intel = sector_intel_map[sector_name]
            news_check = intel.get('news_check', 'neutral')
            if news_check == 'catalyzed':
                return 75.0
            elif news_check == 'negative':
                return 25.0
        return global_news

    def _score_single_stock_news(
        self,
        sector_name: str,
        stock_name: str,
        stock_code: str,
        news_context: NewsContext,
        sector_intel_map: Dict[str, Dict],
        global_news: float,
    ) -> float:
        """个股级消息面评分（0-100）

        与 stock_screener._score_news() 逻辑一致：
        1. 遍历 major_events，匹配个股名/代码（affected_stocks）或板块名（affected_sectors）
        2. 个股命中 × 1.5 加成，多源确认 × 1.5
        3. 无事件命中时回退到板块情报（news_check）或全局评分
        """
        events = news_context.major_events
        if not events:
            return self._get_sector_news_score(sector_name, sector_intel_map, global_news)

        score = 50.0  # 基础分（中性）

        for event in events:
            # 维度 1：个股名/代码直接命中
            stock_hit = False
            if event.affected_stocks and (stock_name or stock_code):
                stock_hit = any(
                    (stock_name and stock_name in s) or (stock_code and stock_code in s)
                    for s in event.affected_stocks
                )

            # 维度 2：板块名模糊匹配
            sector_hit = False
            if sector_name:
                sector_hit = any(
                    sector_name in a or a in sector_name
                    for a in event.affected_sectors
                )

            if not stock_hit and not sector_hit:
                continue

            # 基础 delta
            impact = event.impact_level
            sentiment = event.sentiment
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

            # 个股直接命中加成
            if stock_hit:
                delta = int(delta * 1.5)

            # 多源确认加成
            if event.source_count >= 3:
                delta = int(delta * 1.5)

            score += delta

        # 无任何事件命中 → 回退到板块情报
        if score == 50.0:
            return self._get_sector_news_score(sector_name, sector_intel_map, global_news)

        return max(0.0, min(100.0, score))

    def _find_stock_sector(
        self, symbol: str, unified_context: UnifiedContext,
        sector_strength_map: Dict[str, float] = None,
        strength_lookup: Dict[str, float] = None,
        frame_group_lookup: Dict[str, str] = None,  # Fix 1: 家族中继匹配
    ) -> Dict[str, Any]:
        """查找股票所属板块及其强度

        优先从正向映射缓存查找，未命中则用反向查询（get_belong_boards）。
        匹配 Node 6 聚合后的 confirmed_sectors（SectorIntel），非 Node 2 原始追踪列表。
        使用多成员关系加权（multiple membership scoring）计算加权平均分数。

        学术参考：
        - "A framework for stock selection via concept-oriented attention
           representation in hypergraph neural network" (KBS, 2024)
        """
        # 初始化或复用缓存
        if not hasattr(self, "_stock_sector_map"):
            self._stock_sector_map = {}

        # 标准化 symbol（去除市场前缀如 "SH600961" → "600961"）
        clean_symbol = symbol.replace("SH", "").replace("SZ", "").replace("BJ", "")

        sectors_list = self._stock_sector_map.get(clean_symbol, [])

        # 正向映射未命中时，用反向查询补充
        if not sectors_list:
            sectors_list = self._reverse_lookup_stock_sector(
                clean_symbol, unified_context,
                sector_strength_map=sector_strength_map,
                strength_lookup=strength_lookup,
                frame_group_lookup=frame_group_lookup,  # Fix 1: 家族中继匹配
            )
            if sectors_list:
                self._stock_sector_map[clean_symbol] = sectors_list

        if not sectors_list:
            return {
                "sector_name": "",
                "sector_type": "",
                "strength": 50.0,
                "sector_role": "",
                "all_sectors": [],
                "all_belong_boards": [],
                "is_fallback": False,
            }

        # Top-N 过滤：只取强度最高的 3 个板块，避免稀释效应
        # 学术参考：COAR-HGNN (KBS, 2024) 使用注意力机制动态加权
        # 简化方案：Top-3 过滤，聚焦核心驱动概念
        sorted_sectors = sorted(
            sectors_list,
            key=lambda x: x.get("strength", 0),
            reverse=True
        )
        top_sectors = sorted_sectors[:3]

        # 计算加权平均分数（基于 Top 3）
        total_weight = sum(s.get("weight", 1.0) for s in top_sectors)
        weighted_strength = sum(
            s.get("strength", 50.0) * s.get("weight", 1.0) for s in top_sectors
        ) / total_weight

        # 选择最强板块作为主板块（向后兼容）
        best_sector = top_sectors[0]

        return {
            "sector_name": best_sector.get("sector_name", ""),
            "sector_type": best_sector.get("sector_type", ""),
            "strength": weighted_strength,
            "sector_role": best_sector.get("sector_role", ""),
            "all_sectors": sectors_list,  # 保留所有板块信息用于展示
            "all_belong_boards": best_sector.get("all_belong_boards", []),  # 全量所属板块（降级显示用）
            "is_fallback": best_sector.get("is_fallback", False),  # 降级匹配标记
        }

    def _build_stock_sector_map(self, sector_context: SectorContext) -> None:
        """从 sector_context 构建 stock → sectors 映射

        遍历主线/埋伏板块，查询成分股并建立映射。
        使用多成员关系加权（multiple membership scoring）：
        一只股票可能属于多个板块，记录所有匹配的板块信息。

        学术参考：
        - "A framework for stock selection via concept-oriented attention
           representation in hypergraph neural network" (KBS, 2024)
        """
        try:
            from src.services.ths_screener import THSScreenerService
            from src.services.board_name_resolver import get_board_name_resolver
            from src.config import get_config

            config = get_config()
            ths_service = THSScreenerService(config)
            resolver = get_board_name_resolver()

            # 遍历主线板块
            for group in sector_context.active_mainline:
                for member in group.members:
                    sector_name = member.name
                    sector_strength = member.strength_score

                    # 查询成分股（简化版，不带 DDE 过滤；双语法多轮）
                    try:
                        board_name, kind = resolver.resolve_with_kind(sector_name)
                        # 按 kind 决定语法顺序
                        concept_q = f"所属同花顺概念板块是{board_name}，上市天数>365天，非ST"
                        industry_q = f"所属同花顺板块是{board_name}，上市天数>365天，非ST"
                        queries = [industry_q, concept_q] if kind == "industry" else [concept_q, industry_q]

                        ths_result = None
                        for q in queries:
                            logger.debug(f"[板块映射] 查询 {sector_name} (kind={kind}): {q[:60]}...")
                            ths_result = ths_service.query(q)
                            if ths_result and ths_result.stocks:
                                resolver.confirm(sector_name, board_name, kind=kind)
                                break

                        if ths_result and ths_result.stocks:
                            for stock in ths_result.stocks:
                                clean_code = stock.code.replace("SH", "").replace("SZ", "").replace("BJ", "")
                                # 初始化该股票的板块列表
                                if clean_code not in self._stock_sector_map:
                                    self._stock_sector_map[clean_code] = []
                                # 添加该板块信息（允许一只股票属于多个板块）
                                self._stock_sector_map[clean_code].append({
                                    "sector_name": sector_name,
                                    "sector_type": "active_mainline",
                                    "strength": sector_strength,
                                    "weight": 1.5,  # 主线板块权重更高
                                })
                            logger.debug(f"[板块映射] {sector_name}: {len(ths_result.stocks)} 只成分股")
                    except Exception as e:
                        logger.warning(f"[板块映射] 查询 {sector_name} 失败: {e}")

            # 遍历埋伏板块
            for ambush_sector in sector_context.ambush:
                sector_name = ambush_sector.name
                sector_strength = ambush_sector.strength_score

                try:
                    board_name, kind = resolver.resolve_with_kind(sector_name)
                    # 双语法多轮查询
                    concept_q = f"所属同花顺概念板块是{board_name}，上市天数>365天，非ST"
                    industry_q = f"所属同花顺板块是{board_name}，上市天数>365天，非ST"
                    queries = [industry_q, concept_q] if kind == "industry" else [concept_q, industry_q]

                    ths_result = None
                    for q in queries:
                        ths_result = ths_service.query(q)
                        if ths_result and ths_result.stocks:
                            resolver.confirm(sector_name, board_name, kind=kind)
                            break

                    if ths_result and ths_result.stocks:
                        for stock in ths_result.stocks:
                            clean_code = stock.code.replace("SH", "").replace("SZ", "").replace("BJ", "")
                            if clean_code not in self._stock_sector_map:
                                self._stock_sector_map[clean_code] = []
                            self._stock_sector_map[clean_code].append({
                                "sector_name": sector_name,
                                "sector_type": "ambush",
                                "strength": sector_strength,
                                "weight": 1.0,  # 埋伏板块权重较低
                            })
                except Exception as e:
                    logger.warning(f"[板块映射] 查询埋伏板块 {sector_name} 失败: {e}")

            logger.info(f"[板块映射] 构建完成，共映射 {len(self._stock_sector_map)} 只股票")

        except Exception as e:
            logger.error(f"[板块映射] 构建失败: {e}")

    def _reverse_lookup_stock_sector(
        self, symbol: str, unified_context: UnifiedContext,
        sector_strength_map: Dict[str, float] = None,
        strength_lookup: Dict[str, float] = None,
        frame_group_lookup: Dict[str, str] = None,  # Fix 1: 家族中继匹配
    ) -> list:
        """反向查询股票所属板块，匹配 Node 6 聚合后的板块情报

        用 get_belong_boards 查全量板块，三级匹配（2026-08-28 扩展）：
        1. unified_context.confirmed_sectors（Node 6 板块 mini GOT 输出，4 维验证后的评分）
        2. unified_context.ambush_pool（埋伏正式池）
        3. unified_context.ambush_reservoir（埋伏蓄水池：综合分低于 floor 的板块
           不在 confirmed_sectors 中，不扩展则其成分股会落入 fallback）
        匹配前统一用别名表归一化板块名（同花顺口径"半导体概念" ↔ 东财口径"半导体"）。
        二/三级命中的强度从 strength_lookup/sector_strength_map 查（查不到给中性 50）。

        降级策略：若所属板块三级均未命中，返回第一个板块用于显示，
        标记 is_fallback=True，避免持仓股板块显示为"未知"。

        Returns:
            list of dict: 匹配的板块信息列表，每项含 sector_name/sector_type/strength/weight
        """
        from data_provider.base import DataFetcherManager
        from src.got.board_aliases import normalize_sector_name

        manager = DataFetcherManager()
        boards = manager.get_belong_boards(symbol)

        if not boards:
            logger.debug(f"[反向查询] {symbol}: 未找到所属板块")
            return []

        # 收集所有板块名称（用于降级显示）
        all_board_names = []
        for board in boards:
            board_name = board.get("name", "") or board.get("board_name", "")
            if board_name:
                all_board_names.append(board_name)

        # 构建 Node 6 confirmed_sectors 的名称集合（用于匹配，归一化后建索引）
        # SectorIntel 包含 tech_score（Node 2 强度）、sector_role（主线/埋伏）等
        confirmed_map = {}  # 归一化名 -> SectorIntel
        for intel in unified_context.confirmed_sectors:
            confirmed_map[normalize_sector_name(intel.sector_name)] = intel

        # 一级：匹配 confirmed_sectors
        matched = []
        seen_canons = set()  # D-P7: 板块去重（同一板块不再占用多个名额）
        for board in boards:
            board_name = board.get("name", "") or board.get("board_name", "")
            canon = normalize_sector_name(board_name)
            # D-P7 (P1-2): 对 canon 做 set 去重
            # ⚠️ 注意：本项会改变 Top-3 的构成——去重后同一板块不再占用三个名额
            if canon in seen_canons:
                continue
            if canon in confirmed_map:
                seen_canons.add(canon)
                intel = confirmed_map[canon]
                # 板块强度统一用综合口径（composite），不再用纯技术分（2026-08-18）
                strength = intel.composite_score
                # 主线板块权重更高
                weight = 1.5 if intel.sector_role == "mainline" else 1.0
                matched.append({
                    "sector_name": intel.sector_name,
                    "sector_type": intel.sector_type,
                    "strength": strength,
                    "weight": weight,
                    "sector_role": intel.sector_role,
                    "capital_check": intel.capital_check,
                    "news_check": intel.news_check,
                    "sentiment_check": intel.sentiment_check,
                })

        if matched:
            logger.info(f"[反向查询] {symbol}: 匹配到 {len(matched)} 个板块 { [m['sector_name'] for m in matched] }")
            # 附加全量板块信息用于展示
            matched[0]["all_belong_boards"] = all_board_names
            return matched

        # ── Fix 1: 家族中继匹配（一级半层）──
        relay_matched = self._family_relay_match(
            boards, unified_context, frame_group_lookup,
        )
        if relay_matched:
            logger.info(
                f"[反向查询] {symbol}: 家族中继匹配到 {len(relay_matched)} 个板块 "
                f"{[(m['sector_name'], m.get('relay_family')) for m in relay_matched]}"
            )
            relay_matched[0]["all_belong_boards"] = all_board_names
            return relay_matched

        # ── 二/三级：埋伏正式池 + 蓄水池（2026-08-28 扩展）──
        # 综合分低于 floor 的板块落在蓄水池，不在 confirmed_sectors 中；
        # 不扩展则其成分股会落入 fallback 显示行业板名（如 002158 → 机械设备）
        ambush_map: Dict[str, str] = {}  # 归一化名 -> ambush / reservoir
        for candidate in getattr(unified_context, "ambush_pool", None) or []:
            ambush_map[normalize_sector_name(candidate.sector_name)] = "ambush"
        for entry in getattr(unified_context, "ambush_reservoir", None) or []:
            ambush_map.setdefault(normalize_sector_name(entry.sector_name), "reservoir")

        _smap = sector_strength_map or {}
        _slookup = strength_lookup or {}

        def _lookup_strength(board_name: str):
            """查板块强度：精确匹配 → 别名匹配 → 子串匹配 → None（未查到）"""
            # 1. 精确匹配（is-not-None 防止 strength=0.0 穿透）
            s = _smap.get(board_name)
            if s is not None:
                return s
            s = _slookup.get(board_name)
            if s is not None:
                return s
            # 2. 常见别名：贵金属↔黄金, 有色↔有色金属 等
            _ALIASES = {
                "贵金属": ["黄金", "黄金概念"],
                "有色": ["有色金属", "稀缺资源"],
                "养殖": ["种业", "其他养殖"],
            }
            for alias, targets in _ALIASES.items():
                if alias in board_name:
                    for t in targets:
                        s = _smap.get(t)
                        if s is not None:
                            return s
                        s = _slookup.get(t)
                        if s is not None:
                            return s
            # 3. 子串匹配：遍历全部命中取最高分（避免 dict 插入序决定结果）
            best: float | None = None
            for map_key, map_val in {**_smap, **_slookup}.items():
                if board_name in map_key or map_key in board_name:
                    if best is None or map_val > best:
                        best = map_val
            return best

        matched_ambush = []
        for board in boards:
            board_name = board.get("name", "") or board.get("board_name", "")
            canon = normalize_sector_name(board_name)
            if canon in ambush_map:
                strength = _lookup_strength(board_name)
                if strength is None and canon != board_name:
                    strength = _lookup_strength(canon)
                matched_ambush.append({
                    "sector_name": canon,
                    "sector_type": "ambush",
                    "strength": strength if strength is not None else 50.0,
                    "weight": 1.0,
                    "sector_role": "ambush",
                    "ambush_source": ambush_map[canon],
                })

        if matched_ambush:
            logger.info(
                f"[反向查询] {symbol}: 匹配到埋伏/蓄水池板块 "
                f"{[(m['sector_name'], m['ambush_source']) for m in matched_ambush]}"
            )
            matched_ambush[0]["all_belong_boards"] = all_board_names
            return matched_ambush

        # 降级：无匹配时，从 sector_strength_map / strength_lookup 查实际强度
        # P0-2 修复：不再硬编码 50.0，优先用 Node 2 实际强度分
        if all_board_names:
            # 遍历所有板块名，采纳实际强度分（含弱板块 <50，避免乐观偏差）；
            # 仅当全部查不到时才兜底中性分 50（#9 修复）
            fallback_board = all_board_names[0]
            fallback_strength = None
            for bname in all_board_names:
                s = _lookup_strength(bname)
                if s is not None and (fallback_strength is None or s > fallback_strength):
                    fallback_strength = s
                    fallback_board = bname
            if fallback_strength is None:
                fallback_strength = 50.0
            logger.info(
                f"[反向查询] {symbol}: 所属板块 {all_board_names} 均不在 confirmed_sectors 中，"
                f"降级使用 {fallback_board}（强度 {fallback_strength:.1f}）"
            )
            return [{
                "sector_name": fallback_board,
                "sector_type": "belong_board",  # 标记为非评分板块
                "strength": fallback_strength,  # P0-2: 实际强度分（非硬编码 50）
                "weight": 0.5,  # 低权重
                "sector_role": "neutral",
                "is_fallback": True,  # 标记为降级匹配
                "all_belong_boards": all_board_names,  # 全量板块用于展示
            }]

        logger.debug(f"[反向查询] {symbol}: 所属板块均不在 Node 6 confirmed_sectors 中")
        return []

    # ── Fix 1: 家族中继匹配方法 ──

    def _build_family_anchor_map(
        self, unified_context: UnifiedContext,
    ) -> Dict[str, Any]:
        """构建家族→锚点映射（Fix 1）

        遍历 confirmed_sectors，按 frame_group 分组取 composite 最高者为锚点。
        Returns:
            {family_name: {
                'sector_name': str,      # 锚点板块名
                'composite_score': float,  # 综合分
                'sector_role': str,       # mainline/ambush
                'capital_check': str,
                'news_check': str,
                'sentiment_check': str,
                'frame_group': str,
            }}
        """
        family_anchor: Dict[str, Any] = {}
        if not unified_context.confirmed_sectors:
            return family_anchor

        for intel in unified_context.confirmed_sectors:
            family = getattr(intel, 'frame_group', None)
            if not family:
                continue
            if family not in family_anchor:
                family_anchor[family] = {
                    'sector_name': intel.sector_name,
                    'composite_score': intel.composite_score,
                    'sector_role': getattr(intel, 'sector_role', 'mainline'),
                    'capital_check': intel.capital_check,
                    'news_check': intel.news_check,
                    'sentiment_check': intel.sentiment_check,
                    'frame_group': family,
                }
            else:
                # 取 composite 最高者
                if intel.composite_score > family_anchor[family]['composite_score']:
                    family_anchor[family] = {
                        'sector_name': intel.sector_name,
                        'composite_score': intel.composite_score,
                        'sector_role': getattr(intel, 'sector_role', 'mainline'),
                        'capital_check': intel.capital_check,
                        'news_check': intel.news_check,
                        'sentiment_check': intel.sentiment_check,
                        'frame_group': family,
                    }
        return family_anchor

    def _family_relay_match(
        self, boards: list, unified_context: UnifiedContext,
        frame_group_lookup: Dict[str, str],
    ) -> list:
        """家族中继匹配（Fix 1）：板块名不在 confirmed_sectors，但所属家族内有 confirmed 板块。

        板块名 → normalize → frame_group_lookup → 家族名 → 家族内 composite 最高的
        confirmed 板块作锚点（anchor）。强度取 anchor.composite_score * 0.85（打折），
        sector_role/三维 check 注入锚点值；sector_name 保留归一化后的原板块名（如"半导体"），
        避免误标"先进封装"。weight 1.2（直接主线命中 1.5 / 普通命中 1.0 之间）。
        """
        from src.got.board_aliases import normalize_sector_name

        # 构建家族→锚点映射
        family_anchor = self._build_family_anchor_map(unified_context)
        if not family_anchor:
            return []

        matched = []
        seen_families = set()

        for board in boards:
            board_name = board.get("name", "") or board.get("board_name", "")
            if not board_name:
                continue
            canon = normalize_sector_name(board_name)
            # 查家族：优先 canon，其次原 board_name
            family = frame_group_lookup.get(canon) or frame_group_lookup.get(board_name)
            if not family:
                continue
            # 同族多板块只占一个名额（D-P7 精神）
            if family in seen_families:
                continue
            anchor = family_anchor.get(family)
            if not anchor:
                continue
            seen_families.add(family)

            # 中继命中项
            matched.append({
                "sector_name": canon,                  # 归一化原板块名，如"半导体"
                "sector_type": anchor['sector_type'] if 'sector_type' in anchor else "relay",
                "strength": anchor['composite_score'] * 0.85,  # 家族锚点综合分打折（问题 1 修正）
                "weight": 1.2,
                "sector_role": anchor['sector_role'],     # mainline → 不再落入 :604 sell 分支
                "capital_check": anchor['capital_check'],
                "news_check": anchor['news_check'],
                "sentiment_check": anchor['sentiment_check'],
                "is_relay": True,
                "relay_family": family,
                "relay_anchor_sector": anchor['sector_name'],
            })

        return matched

    def _estimate_tech_score(
        self, pnl_pct_decimal: float, unified: UnifiedContext
    ) -> float:
        """基于 P&L 和市场信号估算技术面评分（0-100）

        ⚠️ 已弃用：循环论证（赚钱=技术面好）。改用 _batch_fetch_tech_scores() 获取真实指标。
        保留此方法仅作为 fallback（TechnicalIndicatorService 调用失败时）。
        """
        base = 50.0
        pnl_adjustment = max(-30.0, min(30.0, pnl_pct_decimal * 200.0))
        signal_adjustment = 0.0
        if unified.unified_signal == "bearish":
            signal_adjustment = -10.0
        elif unified.unified_signal == "bullish":
            signal_adjustment = 10.0
        return max(0.0, min(100.0, base + pnl_adjustment + signal_adjustment))

    def _batch_fetch_tech_scores(self, positions: list) -> Dict[str, float]:
        """批量获取持仓股的真实技术指标评分（0-100）

        使用 TechnicalIndicatorService 获取每只持仓股的 DDE/量比/RSI/MA/布林 等 7 维指标，
        替代旧的 P&L 代理（_estimate_tech_score），消除循环论证。
        """
        from src.config import Config
        from src.services.technical_indicator import TechnicalIndicatorService

        codes = [p.get("symbol", "") for p in positions if p.get("symbol")]
        if not codes:
            return {}

        try:
            from src.config import get_config
            config = get_config()
            tech_service = TechnicalIndicatorService(config)
            result = tech_service.analyze_stocks(codes)
            scores = {
                code: signals.technical_score
                for code, signals in result.signals.items()
            }
            if scores:
                logger.info(f"[Phase 2] 技术指标评分获取成功: {len(scores)}/{len(codes)} 只")
            return scores
        except Exception as e:
            logger.warning(f"[Phase 2] 技术指标获取失败，降级为 P&L 代理: {e}")
            return {}

    def _estimate_risk_score(
        self, pnl_pct_decimal: float, sector_type: str
    ) -> float:
        """基于 P&L 和板块类型估算风险评分（0-100，越高越安全）"""
        base = 70.0
        # 浮亏 → 风险增加（评分降低）
        if pnl_pct_decimal < -0.03:
            base -= 20.0
        elif pnl_pct_decimal < 0:
            base -= 10.0
        # 主线板块风险略高（波动大）
        if sector_type == "mainline":
            base -= 5.0
        elif sector_type == "ambush":
            base += 5.0  # 埋伏板块相对安全
        elif sector_type in ("cooling", "high_position_cooling"):
            base -= 10.0  # 高位降温：回调风险上修（状态机校准新增）
        return max(0.0, min(100.0, base))

    # ── 生命周期决策方法（吃鱼身+高切低策略） ──

    def _lifecycle_sell_signal(
        self,
        pnl_pct_decimal: float,
        lifecycle_phase: Optional[str],
        lifecycle_day: Optional[int],
        overheat_severity: Optional[float],
        stop_loss_threshold: float,
    ) -> tuple[str, str]:
        """生命周期驱动的卖出信号（论文依据：Morgan Stanley 2023, Dickinson 2011）

        核心逻辑：
        - launch/acceleration = 鱼身，持有不动
        - mature/late_stage = 减速，开始减仓
        - exhausted = 清仓

        Returns:
            (action, reason): sell/reduce/hold, 理由
        """
        # 硬止损（保留）
        if pnl_pct_decimal < stop_loss_threshold:
            return "sell", f"触发止损线 {stop_loss_threshold:.0%}, P&L {pnl_pct_decimal:.1%}"

        # 强制卖出：exhausted 阶段
        if lifecycle_phase == "mature_exhausted":
            return "sell", "板块进入衰竭阶段，建议清仓"

        # 强制卖出：过热严重度 > 0.7
        if overheat_severity and overheat_severity > 0.7:
            return "sell", f"过热严重度 {overheat_severity:.2f} > 0.7，建议清仓"

        # 减仓信号：late_stage 或 mature 且天数 > 30
        if lifecycle_phase == "late_stage":
            return "reduce", "板块进入晚期阶段，建议减仓 50%"
        if lifecycle_phase == "mature" and lifecycle_day and lifecycle_day > 30:
            return "reduce", f"板块成熟期已 {lifecycle_day} 天，建议减仓 50%"

        # 持有信号：launch/acceleration = 鱼身
        if lifecycle_phase in ["launch", "acceleration"]:
            return "hold", f"板块处于 {lifecycle_phase} 阶段（鱼身），继续持有"

        # mature 阶段：根据 P&L 决定
        if lifecycle_phase == "mature":
            if pnl_pct_decimal > 0.10:
                return "reduce", f"板块成熟且盈利 {pnl_pct_decimal:.1%} > 10%，建议减仓锁定利润"
            else:
                return "hold", "板块成熟但仍有空间，继续持有"

        # 默认持有
        return "hold", "默认持有"

    def _lifecycle_priority_score(
        self,
        lifecycle_phase: Optional[str],
        lifecycle_day: Optional[int],
        composite_score: float,
        sentiment_momentum: float = 0.0,
    ) -> float:
        """生命周期优先的买入排序（论文依据：Dickinson 2011, Launch 阶段 TSR 最高）

        优先级：launch > ambush > acceleration > mature > late_stage > exhausted

        Returns:
            final_score: 综合排序分数（0-100）
        """
        # 基础生命周期优先级（0-100）
        if lifecycle_phase == "launch" and lifecycle_day and lifecycle_day <= 10:
            priority = 95  # 最强优先：launch 早期
        elif lifecycle_phase == "launch":
            priority = 85  # launch 中后期
        elif lifecycle_phase == "acceleration":
            # 加速期按 day 衰减：day 1-3 = 75（接近 launch），day 4-7 = 65，day 8+ = 50
            # 依据：9月回测失败股集中 day 6-14（众兴菌业 day14 T+3 -10.17%，湘佳股份 day4 -7.92%），
            # 成功股集中在 day 1-10（海通发展 day1 +9.84%）
            if os.getenv("GOT_WINRATE_STEP2_ENABLED", "1") == "1" and lifecycle_day is not None:
                if lifecycle_day <= 3:
                    priority = 75
                elif lifecycle_day <= 7:
                    priority = 65
                else:
                    priority = 50
            else:
                priority = 70  # day 未知或开关关闭，保持原值
        elif lifecycle_phase == "mature" and lifecycle_day and lifecycle_day < 20:
            priority = 40  # 成熟早期（不推荐，但可观察）
        elif lifecycle_phase == "mature":
            priority = 20  # 成熟中后期（避免买入）
        elif lifecycle_phase == "late_stage":
            priority = 5   # 晚期（强烈避免）
        elif lifecycle_phase == "mature_exhausted":
            priority = 0   # 衰竭（禁止买入）
        else:
            priority = 50  # 未知阶段，中等优先级

        # 情绪动量加成（论文依据：EER 2024）
        if sentiment_momentum > 0.15:
            priority += 5  # 情绪升温，适合入场
        elif sentiment_momentum < -0.15:
            priority -= 10  # 情绪降温，谨慎入场

        # 综合排序 = 0.6 × lifecycle_priority + 0.4 × composite_score
        final_score = 0.6 * priority + 0.4 * composite_score
        return final_score

    # ── P4 埋伏退出分支（催化剂驱动） ──

    # 退出参数（集中配置，归档积累后校准）
    AMBUSH_DRIFT_STALL_DAYS = 10          # TODO: 待校准 (当前样本量: 0) — 漂移型：事件后 N 个交易日无涨 → 衰竭
    AMBUSH_DRIFT_TIMEOUT_DAYS = 20        # TODO: 待校准 (当前样本量: 0) — 漂移型：硬超时（交易日）
    AMBUSH_DRIFT_STALL_THRESHOLD = 0.02   # TODO: 待校准 (当前样本量: 0) — 漂移型：涨幅 < 2% 视为走平
    AMBUSH_BINARY_GRACE_DAYS = 2          # TODO: 待校准 (当前样本量: 0) — 二元型：事件日后 N 个交易日仍未发生 → 落空

    def _ambush_exit_signal(
        self,
        snapshot,
        today: date,
        current_price: float,
        kline_closes: Optional[List[float]] = None,
    ) -> tuple[Optional[str], str]:
        """埋伏持仓退出信号检测（P4，催化剂驱动）

        退出触发清单（plan.md §三 Node 7）：
        | fade 型      | 事件日次日开盘卖（buy the rumor, sell the news）|
        | drift 型     | 持有吃漂移；满足任一退出：衰竭/破MA20/硬超时    |
        | 事件落空     | 到期未发生/宣布推迟 → 立即退出                 |
        | DDE 毕业     | 转主线逻辑接管（由调用方在板块级检测）          |

        Args:
            snapshot: AmbushPositionSnapshotRecord（is_open=True）
            today: 当前日期
            current_price: 持仓股当前价格
            kline_closes: 最近 N 日收盘价序列（drift 型 MA20 判断用）

        Returns:
            (exit_signal, exit_reason):
                exit_signal=None → 继续持有
                exit_signal="fade_sell" / "drift_exhausted" / "drift_break_ma20" /
                             "drift_timeout" / "event_cancelled"
        """
        from src.got.catalyst_calendar import _count_trade_days

        entry_date = snapshot.entry_date
        event_date = snapshot.catalyst_event_date
        category_tag = snapshot.category_tag

        # 通用：硬止损（无论哪种类型）
        entry_price = snapshot.entry_price
        if entry_price > 0:
            pnl_pct = (current_price - entry_price) / entry_price
            if pnl_pct < -0.07:  # 埋伏仓统一 -7% 止损
                return (
                    "stop_loss",
                    f"埋伏仓止损: P&L {pnl_pct:.1%}, 入场价 {entry_price:.2f} → 现价 {current_price:.2f}",
                )

        # ── fade 型：事件日次日卖 ──
        if category_tag == "fade":
            if event_date and today > event_date:
                return (
                    "fade_sell",
                    f"fade 型催化剂已兑现（事件日 {event_date}），buy the rumor sell the news",
                )
            return None, "fade 型：等待事件日"

        # ── binary 型：事件落空即走 ──
        if category_tag == "binary":
            if event_date:
                trading_days_since_event = _count_trade_days(event_date, today)
                if trading_days_since_event > self.AMBUSH_BINARY_GRACE_DAYS:
                    return (
                        "event_cancelled",
                        f"binary 型催化剂落空：事件日 {event_date} 已过 {trading_days_since_event} 个交易日仍未发生",
                    )
            return None, "binary 型：等待事件确认"

        # ── drift 型：持有吃漂移，三种退出 ──
        if category_tag == "drift":
            if not event_date:
                # 无事件日期，退化为超时检查
                trading_days_held = _count_trade_days(entry_date, today)
                if trading_days_held > self.AMBUSH_DRIFT_TIMEOUT_DAYS:
                    return (
                        "drift_timeout",
                        f"drift 型硬超时：已持有 {trading_days_held} 个交易日（上限 {self.AMBUSH_DRIFT_TIMEOUT_DAYS}）",
                    )
                return None, "drift 型：无事件日期，等待漂移或超时"

            trading_days_since_event = _count_trade_days(event_date, today)

            # 条件 c：硬超时 20 个交易日
            trading_days_held = _count_trade_days(entry_date, today)
            if trading_days_held > self.AMBUSH_DRIFT_TIMEOUT_DAYS:
                return (
                    "drift_timeout",
                    f"drift 型硬超时：已持有 {trading_days_held} 个交易日（上限 {self.AMBUSH_DRIFT_TIMEOUT_DAYS}）",
                )

            # 条件 a：事件后 10 个交易日漂移衰竭
            if trading_days_since_event >= self.AMBUSH_DRIFT_STALL_DAYS:
                # 衰竭判定：当前价格 vs 事件后首日价格（近似用 entry_price × (1 + 预期漂移)）
                # 简化：若持有期涨幅 < AMBUSH_DRIFT_STALL_THRESHOLD → 衰竭
                if entry_price > 0:
                    held_return = (current_price - entry_price) / entry_price
                    if held_return < self.AMBUSH_DRIFT_STALL_THRESHOLD:
                        return (
                            "drift_exhausted",
                            f"drift 型漂移衰竭：事件后 {trading_days_since_event} 日，"
                            f"持有期涨幅仅 {held_return:.1%}（阈值 {self.AMBUSH_DRIFT_STALL_THRESHOLD:.0%}）",
                        )

            # 条件 b：破 MA20
            if kline_closes and len(kline_closes) >= 20:
                ma20 = sum(kline_closes[-20:]) / 20
                if current_price < ma20:
                    return (
                        "drift_break_ma20",
                        f"drift 型破 MA20: 现价 {current_price:.2f} < MA20 {ma20:.2f}",
                    )

            return None, (
                f"drift 型持有中：事件后 {trading_days_since_event} 日，"
                f"持有 {trading_days_held} 日，继续吃漂移"
            )

        # 未知类型：默认持有
        return None, f"未知催化剂类型 '{category_tag}'，默认持有"

    def _load_ambush_snaphots(
        self, positions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """加载埋伏持仓快照（P4）

        从 ambush_position_snapshots 表查询 is_open=True 的记录，
        与当前持仓列表匹配，返回 symbol → snapshot_record 映射。

        降级：表不存在或查询失败 → 返回空映射（跳过退出检测）。
        """
        symbol_set = {
            p.get("symbol", "").replace("SH", "").replace("SZ", "").replace("BJ", "")
            for p in positions
            if p.get("symbol")
        }
        if not symbol_set:
            return {}

        try:
            from src.storage import AmbushPositionSnapshotRecord, DatabaseManager
            db = DatabaseManager()
            with db.get_session() as session:
                records = (
                    session.query(AmbushPositionSnapshotRecord)
                    .filter(
                        AmbushPositionSnapshotRecord.is_open == True,  # noqa: E712
                        AmbushPositionSnapshotRecord.symbol.in_(symbol_set),
                    )
                    .all()
                )
                return {r.symbol: r for r in records}
        except Exception as e:
            logger.debug(f"[P4] 加载埋伏快照失败（降级跳过退出检测）: {e}")
            return {}

    def _write_ambush_buy_snapshots(
        self,
        allocation: List[TradeAllocation],
        unified_scoring: List[UnifiedScoringResult],
        unified_context: UnifiedContext,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        """P4 埋伏仓买入快照写入

        对每个 buy 类型的 allocation：
        1. 从 unified_scoring 找到该 symbol 的板块
        2. 若板块在 ambush_pool 中 → 写入催化剂快照到 SQLite
        3. 返回 (成功写入的快照列表, 失败的 symbol 列表)

        降级：表不存在/写入失败 → 记录日志但不阻塞主流程。
        """
        buy_allocations = [a for a in allocation if a.action == "buy" and a.quantity_delta > 0]
        if not buy_allocations:
            return [], []

        # 构建 symbol → scoring 映射（获取板块名）
        symbol_to_scoring = {s.symbol: s for s in unified_scoring}

        # 构建板块名 → AmbushCandidate 映射
        ambush_pool_map = {
            c.sector_name: c for c in unified_context.ambush_pool
        }

        written = []
        failed_symbols = []
        try:
            from src.storage import AmbushPositionSnapshotRecord, DatabaseManager
            db = DatabaseManager()
            with db.get_session() as session:
                for alloc in buy_allocations:
                    scoring = symbol_to_scoring.get(alloc.symbol)
                    if not scoring:
                        continue

                    # P2-4 修复：scoring.sector_name 替代 reasoning 反解析
                    # （候选侧 reasoning 无 "板块[XXX]" 格式，无法从中提取板块名）
                    sector_name = scoring.sector_name if scoring else ""
                    if not sector_name or sector_name not in ambush_pool_map:
                        continue

                    candidate = ambush_pool_map[sector_name]
                    today = date.today()
                    entry_price = alloc.price_range[0] if alloc.price_range else 0.0

                    # A-P0-6: 按自然键 (symbol, entry_date) 查询，命中则更新，未命中才插入
                    existing = session.query(AmbushPositionSnapshotRecord).filter(
                        AmbushPositionSnapshotRecord.symbol == alloc.symbol,
                        AmbushPositionSnapshotRecord.entry_date == today,
                    ).first()

                    if existing:
                        # 同日重跑：更新快照字段（末版为准）
                        existing.sector_name = sector_name
                        existing.category_tag = candidate.category_tag
                        existing.catalyst_event_id = candidate.events[0] if candidate.events else ""
                        existing.catalyst_event_title = candidate.events[0] if candidate.events else ""
                        existing.catalyst_event_date = candidate.top_event_date
                        existing.impact_level = candidate.top_event_level
                        existing.entry_price = entry_price
                        existing.is_open = True
                        logger.info(f"[P4] 埋伏快照已更新（同日重跑）: {alloc.symbol} @ {today}")
                    else:
                        record = AmbushPositionSnapshotRecord(
                            symbol=alloc.symbol,
                            sector_name=sector_name,
                            category_tag=candidate.category_tag,
                            catalyst_event_id=candidate.events[0] if candidate.events else "",
                            catalyst_event_title=candidate.events[0] if candidate.events else "",
                            catalyst_event_date=candidate.top_event_date,
                            impact_level=candidate.top_event_level,
                            consensus_expectation="",
                            entry_date=today,
                            entry_price=entry_price,
                            is_open=True,
                        )
                        session.add(record)

                    written.append({
                        "symbol": alloc.symbol,
                        "sector": sector_name,
                        "category_tag": candidate.category_tag,
                        "entry_price": entry_price,
                    })

                session.commit()

        except Exception as e:
            # 快照写入是非关键记录，降级运行：记录失败 symbol，不中断 Phase 2
            logger.warning(f"[P4] 埋伏买入快照写入失败（降级，不阻塞 Phase 2）: {e}")
            for alloc in buy_allocations:
                if not any(w["symbol"] == alloc.symbol for w in written):
                    failed_symbols.append(alloc.symbol)

        return written, failed_symbols

    # ── Fix 2: 持仓论点快照方法 ──

    def _write_thesis_snapshots(
        self,
        allocation: List[TradeAllocation],
        unified_scoring: List[UnifiedScoringResult],
        unified_context: UnifiedContext,
        sector_context: Any,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        """Fix 2: 持仓论点快照写入

        对每个 buy 类型的 allocation：
        1. 从 unified_scoring 找到该 symbol 的板块
        2. 若 sector_name 非空 → 写入论点快照到 SQLite
        3. family_name 从 sector_name 经 confirmed_sectors 反推（scoring.family_name 恒为 null）
        4. 返回 (成功写入的快照列表，失败的 symbol 列表)

        降级：表不存在/写入失败 → 记录日志但不阻塞主流程。
        """
        buy_allocations = [a for a in allocation if a.action == "buy" and a.quantity_delta > 0]
        if not buy_allocations:
            return [], []

        # 构建 symbol → scoring 映射（获取板块名）
        symbol_to_scoring = {s.symbol: s for s in unified_scoring}

        # 构建 sector_name → family_name 映射（从 confirmed_sectors 反推）
        # scoring.family_name 恒为 null（候选股不打家族标签），必须从板块名反查
        sector_to_family: dict[str, str] = {}
        for intel in (unified_context.confirmed_sectors or []):
            sn = intel.sector_name
            fg = intel.frame_group
            if sn and fg:
                sector_to_family[sn] = fg
        # 补充：frame_group_lookup 覆盖全量板块（含未确认的），兜底 confirmed_sectors 未命中的情况
        fgl = getattr(sector_context, "frame_group_lookup", None) or {}
        for sn, fg in fgl.items():
            sector_to_family.setdefault(sn, fg)


        written = []
        failed_symbols = []
        try:
            from src.storage import HoldingThesisRecord, DatabaseManager
            db = DatabaseManager()
            with db.get_session() as session:
                for alloc in buy_allocations:
                    scoring = symbol_to_scoring.get(alloc.symbol)
                    if not scoring:
                        continue

                    sector_name = scoring.sector_name if scoring else ""
                    family_name = scoring.family_name or sector_to_family.get(sector_name, "")
                    if not sector_name and not family_name:
                        continue  # 板块和家族都无法锚定，跳过

                    today = date.today()
                    entry_price = alloc.price_range[0] if alloc.price_range else 0.0

                    # UPSERT 逻辑
                    existing = session.query(HoldingThesisRecord).filter(
                        HoldingThesisRecord.symbol == alloc.symbol,
                        HoldingThesisRecord.entry_date == today,
                    ).first()

                    if existing:
                        # 同日重跑：更新快照字段（反推失败时保留旧值）
                        if sector_name:
                            existing.sector_name = sector_name
                        existing.family_name = family_name or existing.family_name
                        existing.composite_at_entry = scoring.composite_score
                        existing.entry_price = entry_price
                        existing.source = "got_buy"
                        existing.is_open = True
                        logger.info(f"[Fix 2] 持仓论点快照已更新（同日重跑）: {alloc.symbol} @ {today}")
                    else:
                        record = HoldingThesisRecord(
                            symbol=alloc.symbol,
                            sector_name=sector_name,
                            family_name=family_name,
                            entry_date=today,
                            entry_price=entry_price,
                            composite_at_entry=scoring.composite_score,
                            source="got_buy",
                            is_open=True,
                        )
                        session.add(record)

                    written.append({
                        "symbol": alloc.symbol,
                        "sector": sector_name,
                        "family": family_name,
                        "entry_price": entry_price,
                    })

                session.commit()

        except Exception as e:
            logger.warning(f"[Fix 2] 持仓论点快照写入失败（降级，不阻塞 Phase 2）: {e}")
            for alloc in buy_allocations:
                if not any(w["symbol"] == alloc.symbol for w in written):
                    failed_symbols.append(alloc.symbol)

        return written, failed_symbols

    def _load_holding_theses(
        self, positions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Fix 2: 加载持仓论点快照

        从 holding_thesis_snapshots 表查询 is_open=True 的记录，
        每 symbol 取 entry_date 最新一条，返回 symbol → record 映射。

        降级：查询失败 → 返回空 dict。
        """
        symbol_set = {p.get("symbol", "") for p in positions if p.get("symbol")}
        if not symbol_set:
            return {}

        try:
            from src.storage import HoldingThesisRecord, DatabaseManager
            db = DatabaseManager()
            with db.get_session() as session:
                records = (
                    session.query(HoldingThesisRecord)
                    .filter(
                        HoldingThesisRecord.is_open == True,  # noqa: E712
                        HoldingThesisRecord.symbol.in_(symbol_set),
                    )
                    .order_by(HoldingThesisRecord.entry_date.desc())
                    .all()
                )
                # 每 symbol 取第一条（entry_date 最新）
                seen = set()
                result = {}
                for r in records:
                    if r.symbol not in seen:
                        result[r.symbol] = r
                        seen.add(r.symbol)
                return result
        except Exception as e:
            logger.debug(f"[Fix 2] 加载持仓论点快照失败（降级跳过）: {e}")
            return {}

    def _close_thesis_snapshots(
        self,
        allocation: List[TradeAllocation],
        portfolio: Dict[str, Any],
    ) -> None:
        """Fix 2: 关闭已卖出的持仓论点快照（is_open=False）

        仅当卖出后持仓归零（is_open 且 quantity_delta 清仓）时关闭论点；
        部分减仓（reduce）保持论点 open。
        降级：写库失败只记 warning。
        """
        sell_allocations = [a for a in allocation if a.action in ("sell", "reduce") and a.quantity_delta < 0]
        if not sell_allocations:
            return

        try:
            from src.storage import HoldingThesisRecord, DatabaseManager
            db = DatabaseManager()
            with db.get_session() as session:
                for alloc in sell_allocations:
                    # 查当前持仓数量，判断是否清仓
                    current_qty = 0.0
                    for pos in portfolio.get("accounts", [{}])[0].get("positions", []):
                        if pos.get("symbol") == alloc.symbol:
                            current_qty = pos.get("quantity", 0.0)
                            break

                    # 仅当卖出后持仓归零时才关闭论点
                    remaining = current_qty + alloc.quantity_delta  # quantity_delta 为负
                    if remaining <= 0:
                        session.query(HoldingThesisRecord).filter(
                            HoldingThesisRecord.symbol == alloc.symbol,
                            HoldingThesisRecord.is_open == True,  # noqa: E712
                        ).update({"is_open": False})
                        logger.info(f"[Fix 2] 论点快照关闭（清仓）: {alloc.symbol}")
                    else:
                        logger.info(f"[Fix 2] 论点快照保持（部分减仓）: {alloc.symbol} 剩余{remaining:.0f}股")
                session.commit()
        except Exception as e:
            logger.warning(f"[Fix 2] 关闭持仓论点快照失败：{e}")

    def _find_stock_sector_with_thesis(
        self,
        stock_sector_info: Dict[str, Any],
        symbol: str,
        unified_context: UnifiedContext,
        family_anchor: Dict[str, Any],
        thesis_map: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Fix 2: 消费论点快照

        优先级：当日 boards 家族中继（Fix 1）> 论点快照（Fix 2）> fallback > 空。

        板块漂移检测：即使有直接匹配，若 thesis 存在且 thesis.sector_name 与当前
        sector_name 不同 → 标记 thesis_drift=True，上游 _score_holdings 将其作为
        漂移入口喂给 _drift_action_with_grace。
        """
        is_fallback = stock_sector_info.get("is_fallback", False)
        sector_name = stock_sector_info.get("sector_name", "")

        # 查论点快照
        thesis = thesis_map.get(symbol)

        # 板块漂移检测：有直接匹配但板块名与论点不一致（归一化后比对，避免别名/后缀误报）
        from src.got.board_aliases import normalize_sector_name
        thesis_sector = normalize_sector_name(thesis.sector_name) if thesis and thesis.sector_name else ""
        current_sector = normalize_sector_name(sector_name) if sector_name else ""
        if thesis_sector and current_sector and thesis_sector != current_sector:
            stock_sector_info["thesis_drift"] = True
            stock_sector_info["thesis_sector_name"] = thesis.sector_name
            stock_sector_info["thesis_family"] = thesis.family_name or ""
            logger.info(
                f"[Fix 2] 板块漂移检测: {symbol} 论点={thesis.sector_name}({thesis_sector}) → 当日={sector_name}({current_sector})"
            )
            return stock_sector_info

        if not is_fallback and sector_name:
            # 已有有效匹配且无漂移，不消费论点
            return stock_sector_info

        if not thesis:
            return stock_sector_info

        # 论点家族的家族名仍在当日 confirmed 家族中？
        thesis_family = thesis.family_name
        if not thesis_family or thesis_family not in family_anchor:
            return stock_sector_info

        anchor = family_anchor[thesis_family]
        today = date.today()

        # 构造中继型结果（strength 打折比当日 boards 中继再低一档）
        return {
            "sector_name": thesis.sector_name,
            "sector_type": "thesis_relay",
            "strength": anchor['composite_score'] * 0.75,  # 论点家族当日锚点强度打折
            "weight": 1.0,
            "sector_role": anchor['sector_role'],
            "capital_check": anchor['capital_check'],
            "news_check": anchor['news_check'],
            "sentiment_check": anchor['sentiment_check'],
            "is_relay": True,
            "relay_family": thesis_family,
            "relay_anchor_sector": anchor['sector_name'],
            "relay_source": "thesis",
        }

    # ── Fix 3: 卖出宽限方法 ──

    def _drift_action_with_grace(
        self,
        symbol: str,
        sector_name: str,
        sector_strength: float,
        frame_group_lookup: Dict[str, str],
        family_anchor: Dict[str, Any],
        thesis: Any,
        today: date,
        drift_sector_name: str = "",
    ) -> tuple[str, bool]:
        """Fix 3: 板块漂移卖出分支的宽限判定（Invesco 非对称门槛）

        逻辑：
        - 漂移板块所属家族锚点 composite >= 60 → hold + 置观察（首次）
        - 家族锚点 [40, 60)：观察已跨交易日 → sell；首次 → reduce + 置观察
        - 家族锚点 < 40 或无家族/无锚点 → sell（深度破位，不宽限）

        Returns:
            (action, grace_applied)：grace_applied=True 时上游 reasoning 加 [家族宽限：家族 X]
        """
        from src.got.board_aliases import normalize_sector_name

        # 确定板块家族名
        canon = normalize_sector_name(sector_name) if sector_name else ""
        family = frame_group_lookup.get(canon)
        if not family and sector_name:
            family = frame_group_lookup.get(sector_name)

        # 无家族 → 不宽限
        if not family or family not in family_anchor:
            return "sell", False

        anchor = family_anchor[family]
        anchor_composite = anchor['composite_score']

        # 深度破位：不宽限
        if anchor_composite < 40:
            return "sell", False

        # 家族强（>=60）→ hold + 置观察
        if anchor_composite >= 60:
            # 懒建 drift_watch 记录
            self._apply_drift_watch(symbol, family, today, anchor_composite, thesis, sector_name=drift_sector_name)
            return "hold", True

        # 家族弱 [40, 60)：查是否已观察跨日
        drift_watch_since = None
        drift_family = None
        if thesis:
            drift_watch_since = thesis.drift_watch_since
            drift_family = thesis.drift_family

        if drift_watch_since and drift_family == family:
            # 已观察跨交易日 → sell
            return "sell", False
        else:
            # 首次 → reduce + 置观察
            self._apply_drift_watch(symbol, family, today, anchor_composite, thesis, sector_name=drift_sector_name)
            return "reduce", True

    def _apply_drift_watch(
        self,
        symbol: str,
        family: str,
        today: date,
        anchor_composite: float,
        thesis: Any,
        sector_name: str = "",
    ) -> None:
        """Fix 3: 应用漂移观察（持久化到 holding_thesis_snapshots）

        sector_name 优先使用 thesis 原始板块名，其次用调用方传入的 all_belong_boards 首项。
        写库 try/except 降级（宽限判定本身不依赖写库成功）。
        """
        try:
            from src.storage import HoldingThesisRecord, DatabaseManager
            db = DatabaseManager()
            with db.get_session() as session:
                # 查是否存在该 symbol 的 open 记录
                record = (
                    session.query(HoldingThesisRecord)
                    .filter(
                        HoldingThesisRecord.symbol == symbol,
                        HoldingThesisRecord.is_open == True,  # noqa: E712
                    )
                    .first()
                )

                if record:
                    # 存在则 UPDATE
                    record.drift_watch_since = today
                    record.drift_family = family
                    logger.info(f"[Fix 3] 漂移观察更新：{symbol} 家族{family} 锚点{anchor_composite:.0f}")
                else:
                    # 不存在则懒建（source="drift_watch"）
                    # sector_name 优先用 thesis 原始板块名，其次用调用方传入值
                    fallback_sector = sector_name or (thesis.sector_name if thesis and getattr(thesis, "sector_name", "") else "")
                    last_price = 0.0  # TODO: 从 position 获取实际价格
                    record = HoldingThesisRecord(
                        symbol=symbol,
                        sector_name=fallback_sector,
                        family_name=family,
                        entry_date=today,
                        entry_price=last_price,
                        composite_at_entry=anchor_composite,
                        source="drift_watch",
                        drift_watch_since=today,
                        drift_family=family,
                        is_open=True,
                    )
                    session.add(record)
                    logger.info(f"[Fix 3] 漂移观察懒建：{symbol} 家族{family} 板块={fallback_sector} 锚点{anchor_composite:.0f}")

                session.commit()
        except Exception as e:
            logger.warning(f"[Fix 3] 漂移观察写库失败（降级）: {e}")

    def _close_ambush_snapshots(
        self,
        exit_signals: List[Dict[str, Any]],
        portfolio: Dict[str, Any],
    ) -> None:
        """P4：关闭已退出的埋伏快照（is_open=False + exit 字段）

        Args:
            exit_signals: _ambush_exits 列表
            portfolio: 持仓快照（用于获取退出价格）
        """
        if not exit_signals:
            return

        # 构建 symbol → last_price 映射
        accounts = portfolio.get("accounts", [])
        price_map: Dict[str, float] = {}
        if accounts:
            for pos in accounts[0].get("positions", []):
                price_map[pos.get("symbol", "")] = pos.get("last_price", 0.0)

        try:
            from src.storage import AmbushPositionSnapshotRecord, DatabaseManager
            db = DatabaseManager()
            today = date.today()
            with db.get_session() as session:
                for exit_info in exit_signals:
                    symbol = exit_info["symbol"]
                    exit_price = price_map.get(symbol, 0.0)
                    (
                        session.query(AmbushPositionSnapshotRecord)
                        .filter(
                            AmbushPositionSnapshotRecord.symbol == symbol,
                            AmbushPositionSnapshotRecord.is_open == True,  # noqa: E712
                        )
                        .update({
                            "exit_signal": exit_info["exit_signal"],
                            "exit_date": today,
                            "exit_price": exit_price,
                            "is_open": False,
                        })
                    )
                session.commit()
            logger.info(f"[P4] 关闭 {len(exit_signals)} 条埋伏快照")
        except Exception as e:
            logger.warning(f"[P4] 关闭埋伏快照失败: {e}")

    # =================================================================
    # §15.4 T1-H: 持仓 Agent 审查层
    # =================================================================

    async def _agent_holding_review(
        self,
        holdings: List[UnifiedScoringResult],
        unified_context: UnifiedContext,
        news_context: NewsContext,
        portfolio: Dict[str, Any],
    ) -> List[UnifiedScoringResult]:
        """T1-H: 持仓逐股 Agent 审查（§15.4）

        设计原则：
        - Agent 只有降档权：reduce/exit 标注 + 打分惩罚系数
        - 仓位计算与最终分配仍由规则层完成
        - confidence < AGENT_HOLDING_REVIEW_MIN_CONFIDENCE 忽略（与 T3 仲裁一致）
        - 调用失败/解析失败 → hold（等同现状，R4 原则）
        - 影子模式：跑审查但不应用惩罚

        审查优先级（最多 AGENT_HOLDING_REVIEW_MAX 只）：
        1. 埋伏持仓（ambush_position_type 非空）
        2. 有 P4 退出信号
        3. 浮亏超阈值（composite_score < 40）

        Args:
            holdings: _score_holdings 输出的持仓评分列表
            unified_context: 聚合上下文（含 market_regime 等）
            news_context: 消息面上下文（含催化剂信息）
            portfolio: 持仓快照 dict

        Returns:
            修改后的持仓列表（composite_score 可能因降档惩罚而降低）
        """
        from src.got.catalyst_config import (
            AGENT_HOLDING_REVIEW_ENABLED,
            AGENT_HOLDING_REVIEW_SHADOW,
            AGENT_HOLDING_REVIEW_MAX,
            AGENT_HOLDING_REVIEW_MIN_CONFIDENCE,
        )

        if not AGENT_HOLDING_REVIEW_ENABLED:
            self._shadow_t1h = {
                "line": "t1h", "executed": False,
                "reason": "AGENT_HOLDING_REVIEW_ENABLED 处于关闭状态",
            }
            return holdings

        if not holdings:
            self._shadow_t1h = {
                "line": "t1h", "executed": False,
                "reason": "当前无持仓，该影子线无审查对象",
            }
            return holdings

        # 按优先级筛选审查目标
        review_targets = self._select_holding_review_targets(
            holdings, AGENT_HOLDING_REVIEW_MAX
        )
        if not review_targets:
            logger.debug("[T1-H] 无需审查的持仓")
            self._shadow_t1h = {
                "line": "t1h", "executed": False,
                "reason": (
                    f"{len(holdings)} 只持仓均不满足三类审查条件"
                    "（埋伏持仓 / P4 退出信号 / 综合评分 < 40）"
                ),
                "skipped": [
                    {
                        "symbol": h.symbol,
                        "name": getattr(h, "name", "") or h.symbol,
                        "composite_score": round(float(h.composite_score or 0), 1),
                    }
                    for h in holdings
                ],
            }
            return holdings

        shadow_tag = "[SHADOW] " if AGENT_HOLDING_REVIEW_SHADOW else ""
        logger.info(
            f"[T1-H] 审查 {len(review_targets)} 只持仓"
            f"（预算上限 {AGENT_HOLDING_REVIEW_MAX}）"
        )
        self._shadow_t1h = {
            "line": "t1h", "executed": True, "shadow": AGENT_HOLDING_REVIEW_SHADOW,
            "reviewed": len(review_targets), "budget": AGENT_HOLDING_REVIEW_MAX,
            "verdicts": [],
        }

        # 构建 review executor（T1-H 持仓审查：verdict 词表 hold/add/reduce/exit）
        try:
            from src.agent.factory import build_review_executor
            executor = build_review_executor(review_type="t1h")
        except Exception as exc:
            logger.warning(f"[T1-H] Review Agent 构建失败，降级跳过: {exc}")
            self._shadow_t1h["executed"] = False
            self._shadow_t1h["reason"] = f"Review Agent 构建失败: {exc}"
            return holdings

        # 提取市场环境
        regime = "unknown"
        if hasattr(unified_context, 'market_regime'):
            regime = unified_context.market_regime or "unknown"

        # W10: 缓存降档前原分，影子回滚时精确还原（避免双重 rounding 漂移）
        orig_score_by_symbol: Dict[str, float] = {}

        for holding in review_targets:
            try:
                # 构建 prompt
                prompt = self._build_holding_review_prompt(
                    holding, regime, news_context, portfolio,
                )

                # 调用 Agent（空响应重试 3 次）
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
                        logger.debug(f"[T1-H] {holding.symbol} 第{attempt+1}次调用失败: {exc}")

                if not response_text:
                    logger.debug(f"[T1-H] {holding.symbol} 调用失败，降级为 hold")
                    continue

                # 解析 JSON（使用 try_parse_json 四策略）
                from src.agent.runner import try_parse_json
                verdict_data = try_parse_json(response_text)
                if not verdict_data or not isinstance(verdict_data, dict):
                    logger.warning(f"[T1-H] {holding.symbol} JSON 解析失败")
                    continue

                verdict = verdict_data.get("verdict", "hold")
                try:
                    confidence = float(verdict_data.get("confidence", 0.0))
                except (TypeError, ValueError):
                    confidence = 0.0
                # W10: clamp 到 [0,1]，防 LLM 以百分制返回（如 85）被误读为极高置信
                confidence = min(1.0, max(0.0, confidence))
                reasoning = str(verdict_data.get("reasoning", ""))[:60]

                # 记录 Agent 审查结果
                holding.agent_review_verdict = verdict
                holding.agent_review_reasoning = reasoning
                self._shadow_t1h.setdefault("verdicts", []).append({
                    "symbol": holding.symbol,
                    "name": getattr(holding, "name", "") or holding.symbol,
                    "verdict": verdict,
                    "confidence": round(confidence, 2),
                    "reasoning": reasoning,
                    "below_threshold": confidence < AGENT_HOLDING_REVIEW_MIN_CONFIDENCE,
                })

                # confidence 不足阈值 → 忽略（等同 hold）
                if confidence < AGENT_HOLDING_REVIEW_MIN_CONFIDENCE:
                    logger.debug(
                        f"[T1-H] {holding.symbol} confidence={confidence:.2f} "
                        f"< {AGENT_HOLDING_REVIEW_MIN_CONFIDENCE}，忽略"
                    )
                    continue

                # 降档惩罚（仅 reduce/exit 生效）
                if verdict in ("reduce", "exit"):
                    penalty = self._compute_holding_penalty(verdict, confidence)
                    holding.agent_review_penalty = penalty
                    old_score = holding.composite_score
                    orig_score_by_symbol[holding.symbol] = old_score
                    holding.composite_score = round(old_score * penalty, 2)
                    logger.info(
                        f"{shadow_tag}[T1-H] {holding.symbol} {holding.name}: "
                        f"{verdict.upper()} (conf={confidence:.2f}) "
                        f"→ 评分 {old_score:.1f} → {holding.composite_score:.1f} "
                        f"(惩罚×{penalty:.2f}) — {reasoning}"
                    )
                elif verdict == "add":
                    logger.info(
                        f"{shadow_tag}[T1-H] {holding.symbol}: ADD "
                        f"(conf={confidence:.2f}) — {reasoning}"
                    )

            except Exception as exc:
                logger.warning(f"[T1-H] {holding.symbol} 审查异常: {exc}")

        # 影子模式：不应用惩罚（回滚 composite_score）
        if AGENT_HOLDING_REVIEW_SHADOW:
            for holding in review_targets:
                if holding.agent_review_penalty is not None:
                    # W10: 直接还原缓存原值，不做 round(score/penalty) 逆运算（双重舍入会漂移）
                    orig = orig_score_by_symbol.get(holding.symbol)
                    if orig is not None:
                        holding.composite_score = orig
                    holding.agent_review_penalty = None
            logger.info(
                f"[SHADOW][T1-H] 影子模式: 审查完成但不应用惩罚，"
                f"降档数量={sum(1 for h in review_targets if h.agent_review_verdict in ('reduce', 'exit'))}"
            )

        return holdings

    def _save_shadow_records(self) -> None:
        """把 T1 / T1-H 的影子观测记录落到当日归档目录，供 archive.py 渲染进 reasoning.md。

        目录基准取「运行日期」，与 aggregator._save_glue_result 一致；
        Phase 2 只在生产运行（--got-replay 不经过 Phase 2），故 today() 可靠。
        """
        if not (self._shadow_t1 or self._shadow_t1h):
            return
        day_dir = Path("data/got_daily") / date.today().strftime("%Y%m%d")
        try:
            day_dir.mkdir(parents=True, exist_ok=True)
            for filename, record in (
                ("shadow_t1.json", self._shadow_t1),
                ("shadow_t1h.json", self._shadow_t1h),
            ):
                if record:
                    (day_dir / filename).write_text(
                        json.dumps(record, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
        except OSError as exc:
            logger.warning("[Phase 2] 影子观测落盘失败（已忽略）: %s", exc)

    def _collect_shadow_t1(self, finalists: List[Any]) -> None:
        """收集 T1 候选股终审的影子观测记录。

        T1 的 verdict 由 stock_screener 写在 ScreenerStock 上、随 finalists 返回；
        Phase 2 路径此前直接丢弃（只有 run_got_mode 才落盘），此处留痕供 reasoning.md 使用。
        """
        from src.got.catalyst_config import (
            AGENT_STOCK_REVIEW_ENABLED,
            AGENT_STOCK_REVIEW_SHADOW,
            AGENT_STOCK_REVIEW_MAX,
        )

        if not AGENT_STOCK_REVIEW_ENABLED:
            self._shadow_t1 = {
                "line": "t1", "executed": False,
                "reason": "AGENT_STOCK_REVIEW_ENABLED 处于关闭状态",
            }
            return

        reviewed = [s for s in finalists if getattr(s, "agent_verdict", "")]
        if not reviewed:
            self._shadow_t1 = {
                "line": "t1", "executed": False,
                "reason": "无候选进入终审（候选池为空，或漏斗未产出 finalists）",
            }
            return

        self._shadow_t1 = {
            "line": "t1", "executed": True, "shadow": AGENT_STOCK_REVIEW_SHADOW,
            "reviewed": len(reviewed), "budget": AGENT_STOCK_REVIEW_MAX,
            "rejects": [
                {
                    "symbol": getattr(s, "code", ""),
                    "name": getattr(s, "name", ""),
                    "reason": getattr(s, "agent_notes", ""),
                }
                for s in reviewed if getattr(s, "agent_verdict", "") == "reject"
            ],
        }

    def _select_holding_review_targets(
        self,
        holdings: List[UnifiedScoringResult],
        max_count: int,
    ) -> List[UnifiedScoringResult]:
        """按优先级选择持仓审查目标（最多 max_count 只）。

        优先级：
        1. 埋伏持仓（ambush_position_type 非空）
        2. 有 P4 退出信号（ambush_exit_signal 非空）
        3. 浮亏/低分持仓（composite_score < 40）
        """
        ambush = [h for h in holdings if h.ambush_position_type]
        exit_signal = [h for h in holdings if h.ambush_exit_signal]
        low_score = [h for h in holdings if h.composite_score < 40]

        # 去重合并（保持优先级顺序）
        seen = set()
        targets = []
        for h in ambush + exit_signal + low_score:
            if h.symbol not in seen:
                seen.add(h.symbol)
                targets.append(h)
            if len(targets) >= max_count:
                break

        return targets

    def _build_holding_review_prompt(
        self,
        holding: UnifiedScoringResult,
        regime: str,
        news_context: NewsContext,
        portfolio: Dict[str, Any],
    ) -> str:
        """构建 T1-H 持仓审查 prompt。"""
        # 提取持仓成本/盈亏/数量信息
        portfolio_positions = portfolio.get("positions", [])
        position_info = ""
        for pos in portfolio_positions:
            if pos.get("symbol") == holding.symbol or pos.get("code") == holding.symbol:
                cost = pos.get("cost_price", 0)
                current = pos.get("current_price", 0)
                quantity = (
                    pos.get("quantity") or pos.get("shares")
                    or pos.get("volume") or pos.get("amount") or 0
                )
                pnl_pct = ((current - cost) / cost * 100) if cost > 0 else 0
                position_info = (
                    f"持仓数量={quantity}, 成本价={cost:.2f}, "
                    f"现价={current:.2f}, 盈亏={pnl_pct:+.1f}%"
                )
                break

        # W11: §15.4 输入契约「买入以来新闻」— 按持仓标的过滤 news_context.major_events
        news_lines = []
        for ev in (getattr(news_context, "major_events", None) or []):
            affected = set(getattr(ev, "affected_stocks", None) or [])
            if holding.symbol in affected or holding.name in affected:
                ts = getattr(ev, "timestamp", None) or getattr(ev, "first_seen", None) or ""
                news_lines.append(
                    f"- [{ts}] {getattr(ev, 'title', '')}"
                    f"（{getattr(ev, 'sentiment', '')}/{getattr(ev, 'impact_level', '')}）"
                )
        news_section = "\n".join(news_lines[:10]) if news_lines else "无"

        # §15.4 P4 催化剂快照：优先注入完整快照（事件标题/共识预期/影响等级/事件日期/
        # 建仓信息），使 LLM 能判断「埋伏的是什么催化剂、共识预期、事件进度、影响量级」；
        # 快照 map 不可用时回退 holding 上的 P4 代号字段（position_type/exit_signal/exit_reason）。
        catalyst_info = "无"
        snapshot = getattr(self, "_ambush_snapshot_map", {}).get(holding.symbol)
        if snapshot is not None:
            parts = [
                f"埋伏类型={getattr(snapshot, 'category_tag', '') or holding.ambush_position_type or '未知'}"
            ]
            event_title = getattr(snapshot, "catalyst_event_title", "") or ""
            if event_title:
                parts.append(f"催化剂事件={event_title}")
            event_date = getattr(snapshot, "catalyst_event_date", None)
            if event_date:
                parts.append(f"事件日期={event_date}")
            impact_level = getattr(snapshot, "impact_level", 0) or 0
            if impact_level:
                parts.append(f"影响等级={'★' * impact_level}")
            consensus = getattr(snapshot, "consensus_expectation", "") or ""
            if consensus:
                parts.append(f"共识预期={consensus}")
            entry_date = getattr(snapshot, "entry_date", None)
            entry_price = getattr(snapshot, "entry_price", None)
            if entry_date and entry_price:
                parts.append(f"建仓={entry_date}@{entry_price:.2f}")
            if holding.ambush_exit_signal:
                parts.append(f"退出信号={holding.ambush_exit_signal}")
            if holding.ambush_exit_reason:
                parts.append(f"退出理由={holding.ambush_exit_reason}")
            catalyst_info = ", ".join(parts)
        elif holding.ambush_position_type:
            # 回退：快照 map 不可用（表缺失/查询失败），用 holding 上的 P4 代号字段
            catalyst_info = f"埋伏类型={holding.ambush_position_type}"
            if holding.ambush_exit_signal:
                catalyst_info += f", 退出信号={holding.ambush_exit_signal}"
            if holding.ambush_exit_reason:
                catalyst_info += f", 退出理由={holding.ambush_exit_reason}"

        return f"""请审查以下持仓股票，给出你的意见。

## 市场环境
- 大盘 regime: {regime}

## 持仓股票
- 代码: {holding.symbol}
- 名称: {holding.name}
- 板块: {holding.sector_name or "未知"}
- 综合评分: {holding.composite_score:.1f}/100
- 持仓信息: {position_info or "无"}
- 催化剂: {catalyst_info}
- 生命周期: {holding.lifecycle_phase or "未知"} (第{holding.lifecycle_day or "?"}天)

## 买入以来相关新闻
{news_section}

## 输出要求
以 JSON 格式输出你的审查意见：
```json
{{
    "verdict": "hold/add/reduce/exit",
    "confidence": 0.0-1.0,
    "reasoning": "一句话理由（30字内）",
    "risk_flags": ["风险点1", ...]
}}
```

verdict 含义：
- hold: 继续持有，维持现状
- add: 看好，建议加仓
- reduce: 存在风险，建议减仓
- exit: 严重风险信号，建议清仓

只输出 JSON，不要其他文字。"""

    @staticmethod
    def _compute_holding_penalty(verdict: str, confidence: float) -> float:
        """计算降档惩罚系数。

        惩罚公式：penalty = 1.0 - (severity * confidence)
        - exit: severity=0.4（最大惩罚）
        - reduce: severity=0.2
        - hold/add: severity=0（无惩罚，返回 1.0）

        Returns:
            惩罚系数（0.6-1.0 区间，越低惩罚越重）
        """
        if verdict not in ("reduce", "exit"):
            return 1.0  # hold/add 不应用惩罚

        severity = 0.4 if verdict == "exit" else 0.2
        penalty = 1.0 - (severity * confidence)
        return max(0.6, min(1.0, penalty))

    def _detect_rotation_opportunity(
        self,
        holdings: List[UnifiedScoringResult],
        unified_context: UnifiedContext,
        sentiment_momentum: float,
    ) -> List[dict]:
        """检测轮动时机（论文依据：Kang 2024, 反转效应在行业层面更稳定）

        当持仓板块进入 late_stage/exhausted → 寻找 ambush/launch 替代

        Returns:
            rotation_signals: 轮动建议列表
        """
        rotation_signals = []

        # 检查是否有持仓进入 late_stage/exhausted
        aging_holdings = [
            h for h in holdings
            if h.lifecycle_phase in ["late_stage", "mature_exhausted"]
            or (h.lifecycle_phase == "mature" and h.lifecycle_day and h.lifecycle_day > 30)
        ]

        if aging_holdings and unified_context.confirmed_sectors:
            # 寻找替代：ambush/launch 板块
            ambush_candidates = [
                s for s in unified_context.confirmed_sectors
                if getattr(s, 'sector_role', 'mainline') == "ambush" and s.composite_score > 60
            ]
            launch_candidates = [
                s for s in unified_context.confirmed_sectors
                if s.lifecycle and s.lifecycle.phase == "launch"
                and s.lifecycle.days_since_launch and s.lifecycle.days_since_launch <= 10
            ]

            if ambush_candidates or launch_candidates:
                rotation_signals.append({
                    "type": "rotation_recommended",
                    "reason": f"{len(aging_holdings)} 只持仓进入成熟后期，建议轮动到 {len(ambush_candidates + launch_candidates)} 个早期板块",
                    "aging": [h.name for h in aging_holdings],
                    "targets": [s.sector_name for s in (launch_candidates + ambush_candidates)[:3]]
                })

        # 情绪动量择时（论文依据：EER 2024）
        if sentiment_momentum > 0.15:
            rotation_signals.append({
                "type": "momentum_positive",
                "reason": "情绪升温，适合加仓 launch 板块",
                "suggestion": "add_position"
            })
        elif sentiment_momentum < -0.15:
            rotation_signals.append({
                "type": "momentum_negative",
                "reason": "情绪降温，建议减仓 mature，等待 ambush 机会",
                "suggestion": "reduce_position"
            })

        return rotation_signals

    def _check_family_crowding(
        self,
        holdings: List[UnifiedScoringResult],
    ) -> List[dict]:
        """检查家族拥挤度（论文依据：Acharya & Marks 2019, 因子拥挤导致流动性枯竭）

        每个家族最多 2 只持仓，避免过度集中

        Returns:
            crowding_warnings: 拥挤警告列表
        """
        family_counts = {}

        for holding in holdings:
            if holding.family_name:
                family_name = holding.family_name
                family_counts[family_name] = family_counts.get(family_name, 0) + 1

        crowding_warnings = []
        for family_name, count in family_counts.items():
            if count >= 3:
                crowding_warnings.append({
                    "type": "family_crowding",
                    "family": family_name,
                    "count": count,
                    "severity": "high",
                    "suggestion": f"{family_name} 家族持有 {count} 只，建议减仓至 2 只以内"
                })
            elif count == 2:
                crowding_warnings.append({
                    "type": "family_crowding",
                    "family": family_name,
                    "count": count,
                    "severity": "medium",
                    "suggestion": f"{family_name} 家族持有 {count} 只，注意分散风险"
                })

        return crowding_warnings

    async def _score_candidates(
        self,
        sector_context: SectorContext,
        capital_context: CapitalContext,
        news_context: NewsContext,
        market_context: MarketContext,
        unified_context: UnifiedContext,
        sentiment_context: Optional[SentimentContext] = None,
    ) -> List[UnifiedScoringResult]:
        """从 confirmed_sectors 提取候选股，五维打分（利用 Node 6 v3 板块情报）"""
        # 复用 stock_screener 的逻辑
        from src.config import get_config
        from src.services.stock_screener import StockScreenerPipeline

        config = get_config()
        screener = StockScreenerPipeline(config)

        # 构造 GOT 上下文（用于 _extract_got_universe 和 _filter_got_finalists）
        sentiment_momentum = getattr(sentiment_context, 'sentiment_momentum', 0.0) or 0.0
        sector_ctx_dict = sector_context.model_dump(mode="json")

        # ── 关键过滤：只有 actionable 板块参与选股 ──
        # caution 板块（候补）不参与候选池提取，大幅减少候选股数量
        actionable_names = {
            s.sector_name for s in unified_context.confirmed_sectors
            if s.classification == "actionable"
        }
        # 埋伏正式池板块（含蓄水池升格）直接获得选股资格：
        # 升格后掉出 Node 2 名单的板块可能不在 confirmed_sectors 中
        for candidate in unified_context.ambush_pool:
            actionable_names.add(candidate.sector_name)
        filtered_mainline = []
        for g in sector_ctx_dict.get("active_mainline", []):
            members = g.get("members")
            if members:
                # 裁剪 members：只保留 actionable 板块
                actionable_members = [
                    m for m in members if m.get("name") in actionable_names
                ]
                if actionable_members:
                    g = dict(g)
                    g["members"] = actionable_members
                    filtered_mainline.append(g)
            elif g.get("name") in actionable_names:
                filtered_mainline.append(g)
        sector_ctx_dict["active_mainline"] = filtered_mainline
        sector_ctx_dict["ambush"] = [
            s for s in sector_ctx_dict.get("ambush", [])
            if s.get("name") in actionable_names
        ]
        # 补入埋伏池中不在 Node 2 ambush 名单的板块（蓄水池升格路径）：
        # _extract_got_universe 只读 sector_context.ambush，不补入则升格板块无法选股；
        # strength_score 从 Node 2 的 strength_lookup 查表（升格消费专用字段）
        strength_lookup = getattr(sector_context, "strength_lookup", {}) or {}
        existing_ambush_names = {s.get("name") for s in sector_ctx_dict["ambush"]}
        self._bypass_ambush_sectors = set()
        for candidate in unified_context.ambush_pool:
            if candidate.sector_name not in existing_ambush_names:
                sector_ctx_dict["ambush"].append({
                    "name": candidate.sector_name,
                    "strength_score": strength_lookup.get(candidate.sector_name, 0.0),
                })
                existing_ambush_names.add(candidate.sector_name)
                # 审核修复 S2：旁路注入打标，供下游按来源分层回测
                self._bypass_ambush_sectors.add(candidate.sector_name)
                logger.info(
                    f"[Phase 2] 埋伏池板块补入选股池: {candidate.sector_name} "
                    f"(不在 Node 2 当日 ambush 名单，catalyst_score={candidate.catalyst_score})"
                )
        logger.info(
            f"[Phase 2] 选股池限定：{len(actionable_names)} 个 actionable 板块参与，"
            f"共 {len(unified_context.confirmed_sectors)} 个 confirmed_sectors"
        )

        got_context = {
            "sector_context": sector_ctx_dict,
            "capital_context": capital_context.model_dump(mode="json"),
            "market_context": market_context.model_dump(mode="json"),
            "news_context": news_context.model_dump(mode="json"),
            "sentiment_context": {
                "sentiment_score": getattr(sentiment_context, 'sentiment_score', 0.0) or 0.0,
                "fear_greed_index": getattr(sentiment_context, 'fear_greed_index', 0.5) or 0.5,
                "sentiment_momentum": sentiment_momentum,
            },
        }

        # 构建板块情报映射（从 Node 6 v3 的 confirmed_sectors）
        sector_intel_map = self._build_sector_intel_map(unified_context)

        # 提取候选池 + 5 阶段漏斗筛选
        # 问财 THS 使用同步 Playwright，需在独立线程中运行
        universe = await asyncio.to_thread(screener._extract_got_universe, got_context)
        if not universe:
            logger.error("[Phase 2] 候选池为空！所有 actionable 板块的问财查询均返回 0 只，请检查板块名映射或网络连通性")
            return []

        finalists = await asyncio.to_thread(
            screener._filter_got_finalists, universe, got_context, 10
        )

        # 影子观测：T1 终审结果随 finalists 返回（ScreenerStock.agent_verdict），此前无人消费
        self._collect_shadow_t1(finalists)

        # ── 2026-08-18 新增：候选股板块交叉验证 ──
        # 用 efinance.get_belong_board() 验证问财返回的板块标签是否真实
        # 防止蹭概念票（如利君股份被问财错误归类为 TOPCon电）
        finalists = await self._verify_candidate_sectors(finalists, sector_intel_map)

        # 转换为 UnifiedScoringResult，并利用板块情报调整评分
        # 缺失维度权重归一化（NBER: 缺失≠零分）
        from src.services.stock_screener import (
            SCORING_WEIGHTS,
            weighted_score_with_redistribution,
        )
        # 全局资金面/消息面评分（回退值）
        capital_overall = self._extract_capital_overall(capital_context)
        news_overall = self._extract_news_overall(news_context)

        results = []
        for stock in finalists:
            # 查找股票所属板块（从 stock 的 sector_name 字段）
            # 2026-08-18 修复：ScreenerStock 的属性是 sector_name，不是 sector
            stock_sector = getattr(stock, 'sector_name', '') or getattr(stock, 'sector', '') or ''

            # 直接复用 screener 的个股级评分（已包含 DDE + sector_flow + LLM 消息面匹配）
            capital_score = stock.capital_score
            news_score = stock.news_score

            # 利用板块情报调整板块强度评分
            adjusted_sector_score = self._adjust_score_by_sector_intel(
                stock.sector_score, stock_sector, sector_intel_map
            )

            # ── 过热降权（Part 1）：候选股同样适用 ──
            overheat_penalty = self._compute_overheat_penalty(
                stock_sector, sector_intel_map, unified_context,
            )
            if overheat_penalty < 1.0:
                logger.info(
                    f"[Phase 2] [过热降权] {stock.code} {stock.name} 板块={stock_sector} "
                    f"系数={overheat_penalty:.2f}"
                )
                adjusted_sector_score *= overheat_penalty

            # 缺失维度跳过 + 权重归一化
            dimension_scores = {
                "capital": capital_score,
                "news": news_score,
                "sector": adjusted_sector_score,
                "tech": stock.tech_wave_score,
                "risk": stock.risk_score_safe,
            }
            dimension_available = {
                "capital": stock.capital_available,
                "news": stock.news_available,
                "sector": getattr(stock, "sector_available", True),
                "tech": True,
                "risk": True,
            }
            adjusted_composite, _actual_weights = weighted_score_with_redistribution(
                dimension_scores, dimension_available, SCORING_WEIGHTS
            )

            # ── 过热降权（Part 2）：半幅传导到 composite（Step 4）──
            # 原仅乘 adjusted_sector_score（25% 权重），其余 4 维（75%）不受影响；
            # 半幅设计：过热板块仍有资金/技术面支撑时不过度降权，但确保过热信号传导到最终排序。
            # 与 Step 3 正交：Step 4 影响排序（composite），Step 3 影响 action 建议。
            if (
                os.getenv("GOT_WINRATE_STEP4_ENABLED", "1") == "1"
                and overheat_penalty < 1.0
            ):
                composite_penalty = 0.5 + 0.5 * overheat_penalty
                adjusted_composite *= composite_penalty
                logger.info(
                    f"[Phase 2] [过热降权-Composite] {stock.code} {stock.name} "
                    f"板块={stock_sector} 半幅系数={composite_penalty:.3f}"
                )

            # 提取生命周期信息
            lifecycle_phase = None
            lifecycle_day = None
            family_name = None
            if stock_sector and stock_sector in sector_intel_map:
                intel = sector_intel_map[stock_sector]
                lifecycle_phase = intel.get('lifecycle_phase')
                lifecycle_day = intel.get('lifecycle_day')

            # 使用生命周期优先级排序（吃鱼身+高切低策略）
            lifecycle_priority = self._lifecycle_priority_score(
                lifecycle_phase,
                lifecycle_day,
                adjusted_composite,
                sentiment_momentum,
            )

            # 构建 reasoning，包含板块情报信息
            sector_intel_info = ""
            if stock_sector and stock_sector in sector_intel_map:
                intel = sector_intel_map[stock_sector]
                sector_intel_info = (
                    f", 板块验证: {intel['classification']}"
                    f"(资金{intel['capital_check']}/消息{intel['news_check']}/情绪{intel['sentiment_check']})"
                )

            # Q9 连带修复（audit_20260922）：板块维度被剔除时同步重写分数行，
            # 消除 screener 旧分（69.0）与 Phase 2 重算分（67.2）双轨透传
            base_reasoning = stock.ai_reasoning or f"综合评分 {adjusted_composite:.1f}"
            if not getattr(stock, "sector_available", True):
                base_reasoning = re.sub(
                    r"\*\*综合评分\*\*:\s*[\d.]+/100",
                    f"**综合评分**: {adjusted_composite:.1f}/100（板块维度已剔除后归一）",
                    base_reasoning,
                )

            # 动作建议：基于生命周期优先级
            # 资金确认闸门（Step 3）：capital < 35 时降级为 watch（不禁止入选，只禁止激进买入建议）
            # 依据：9月失败股 capital 18-56 仍获 buy_trend（湘佳股份 capital=18.2 → T+3 -7.92%）
            # capital_score 缺失（None）时不触发闸门（缺失≠零分）
            CAPITAL_GATE = float(os.getenv("GOT_WINRATE_CAPITAL_GATE", "35.0"))
            capital_ok = (
                capital_score is None
                or not stock.capital_available
                or capital_score >= CAPITAL_GATE
            )
            if os.getenv("GOT_WINRATE_STEP3_ENABLED", "1") == "1":
                if lifecycle_priority > 60 and lifecycle_phase in ["launch", "acceleration"] and capital_ok:
                    action = "buy_trend"
                elif lifecycle_priority > 50 and capital_ok:
                    action = "buy_dip"
                else:
                    action = "watch"
            else:
                # 回退旧逻辑
                if lifecycle_priority > 60 and lifecycle_phase in ["launch", "acceleration"]:
                    action = "buy_trend"
                elif lifecycle_priority > 50:
                    action = "buy_dip"
                else:
                    action = "watch"

            # Q9-B（audit_20260922）：板块验证失败候选保留在 unified_scoring 供观察，
            # 但 action 强制标 watch（身份不可信 → 禁止任何 buy* 建议）
            if not getattr(stock, "sector_available", True) and action.startswith("buy"):
                action = "watch"

            # 板块分类（用于输出可观测性）
            sector_classification = None
            if stock_sector and stock_sector in sector_intel_map:
                sector_classification = sector_intel_map[stock_sector].get('classification')

            results.append(
                UnifiedScoringResult(
                    symbol=stock.code,
                    name=self._resolve_missing_name(stock.code, stock.name),
                    is_holding=False,
                    composite_score=adjusted_composite,
                    dimension_scores={
                        "capital": capital_score,
                        "news": news_score,
                        "sector": adjusted_sector_score,
                        "tech": stock.tech_wave_score,
                        "risk": stock.risk_score_safe,
                    },
                    action_recommendation=action,
                    confidence=0.7,
                    reasoning=(
                        (base_reasoning)
                        + sector_intel_info
                    ),
                    sector_name=stock_sector or None,
                    sector_classification=sector_classification,
                    lifecycle_phase=lifecycle_phase,
                    lifecycle_day=lifecycle_day,
                    family_name=family_name,
                    sector_available=getattr(stock, 'sector_available', True),
                    # 审核修复 W10/S2：候选来源可观测性透传
                    dde_degraded=getattr(stock, "dde_degraded", False),
                    pool_source=(
                        "bypass_ambush"
                        if stock_sector and stock_sector in self._bypass_ambush_sectors
                        else ""
                    ),
                )
            )

        # 按生命周期优先级排序（而非单纯按 composite_score）
        results.sort(key=lambda x: self._lifecycle_priority_score(
            x.lifecycle_phase, x.lifecycle_day, x.composite_score, sentiment_momentum
        ), reverse=True)

        return results

    # ── Step 3: 组合优化 ──

    def _allocate(
        self,
        unified_scoring: List[UnifiedScoringResult],
        risk: PortfolioRiskAssessment,
        portfolio: Dict[str, Any],
        unified_context: UnifiedContext = None,
        bar_complete: bool = True,
    ) -> tuple[list[TradeAllocation], list[UnifiedScoringResult]]:
        """规则驱动的仓位分配（支持高切低轮动策略）。

        Returns:
            (allocations, 被预算约束否决的 BUY 候选列表)
            当买入预算为 0 时，vetoed 含全部通过评分的 buy 候选，
            供输出层显式说明"信号被仓位/资金上限否决"，避免静默归零。
        """
        allocations = []

        # 当前持仓映射
        accounts = portfolio.get("accounts", [])
        holdings_map = {}
        if accounts:
            positions = accounts[0].get("positions", [])
            holdings_map = {p.get("symbol", ""): p for p in positions}

        # 目标仓位
        total_equity = portfolio.get("total_equity", 1.0)
        total_market_value = portfolio.get("total_market_value", 0.0)
        total_cash = portfolio.get("total_cash", 0.0)

        target_position_value = risk.position_limit * total_equity

        # ── GATE-1: 卖出侧可成交性投影层（实施报告 §2，三态开关）──
        #   缺省 off → 完全不进，旧路径逐字节不变（含不新增行情 IO）；
        #   shadow → 只给卖单打 pending_exit/blocked_reason 观测标签，不改数量/预算；
        #   enabled → 跌停封板/T+1 卖单转顺延（不进成交集合、释放资金不计入预算）。
        #   降级红线：行情取不到 → 不拦截不抛异常，仅落 blocked_reason=quote_unavailable。
        _tg_mode = os.getenv("GOT_TRADABILITY_GATE", "off").strip().lower()
        _tg_enabled = _tg_mode == "enabled"
        _tg_shadow = _tg_mode == "shadow"
        _tg_active = _tg_enabled or _tg_shadow
        pending_exits: List[Dict[str, Any]] = []
        _quote_cache: Dict[str, Any] = {}
        _entry_cache: Dict[str, Any] = {}  # symbol -> entry_date（避免同一标的多次查库）
        _tg_today = date.today()

        def _tradability_check(symbol: str, name: str, action: str):
            """行情缓存 + 可成交性判定（同标的日内一次 IO）。异常一律放行。"""
            from src.got.tradability import (
                check_tradability, fetch_tradability_inputs, get_last_entry_date,
                limit_pct_for_code,
            )
            if symbol not in _quote_cache:
                try:
                    _quote_cache[symbol] = fetch_tradability_inputs(symbol)
                except Exception as exc:  # 绝不阻塞 Phase 2 主流程
                    logger.debug("[Phase 2] GATE-1 行情获取异常 %s: %s", symbol, exc)
                    _quote_cache[symbol] = {}
            q = _quote_cache[symbol]
            entry_date = None
            if action == "sell":
                # CR#4：entry_date 来自 DB（与行情无关），同标的日内缓存一次
                if symbol not in _entry_cache:
                    try:
                        _entry_cache[symbol] = get_last_entry_date(symbol, _tg_today)
                    except Exception as exc:
                        logger.debug("[Phase 2] GATE-1 建仓日查询异常 %s: %s", symbol, exc)
                        _entry_cache[symbol] = None
                entry_date = _entry_cache[symbol]
            try:
                return check_tradability(
                    action=action,
                    quote_price=q.get("price"),
                    pre_close=q.get("pre_close"),
                    quote_low=q.get("low"),
                    quote_high=q.get("high"),
                    limit_pct=limit_pct_for_code(symbol, name),
                    entry_date=entry_date,
                    today=_tg_today,
                )
            except Exception as exc:  # 判定异常 → 放行（降级红线）
                logger.debug("[Phase 2] GATE-1 判定异常 %s: %s", symbol, exc)
                from src.got.tradability import TradabilityResult
                return TradabilityResult(executable=True, blocked_reason="quote_unavailable")

        # ── Step 1: 生成卖出建议并计算释放资金 ──
        sell_proceeds = 0.0  # 卖出释放的资金
        # 评审修复#1（2026-09-28）：Step 1 同步维护「处理后的剩余股数」真相，供 Step 1.5 按
        # 剩余敞口计算削减——杜绝重复卖单（sell 标的二次按原始股数开卖）与 sell_proceeds 虚增。
        remaining_qty = {
            s.symbol: int(holdings_map.get(s.symbol, {}).get("quantity", 0))
            for s in unified_scoring if s.is_holding
        }

        for score in unified_scoring:
            if not score.is_holding:
                continue

            position = holdings_map.get(score.symbol, {})
            pnl_pct = position.get("unrealized_pnl_pct", 0.0) or 0.0
            pnl_pct_decimal = pnl_pct / 100.0
            quantity = int(position.get("quantity", 0))
            last_price = position.get("last_price", 0.0)

            # 根据 action_recommendation 决定卖出/减仓/持有
            action = score.action_recommendation

            if action == "sell":
                # GATE-1 enabled：不可成交卖单转顺延（不落成交、释放资金不计入预算），
                # 留 pending_exits 观测；shadow 只打标不改数量；quote 缺失不拦截。
                if _tg_active:
                    _tg_res = _tradability_check(score.symbol, score.name, "sell")
                    if not _tg_res.executable:
                        if _tg_enabled:
                            pending_exits.append({
                                "symbol": score.symbol, "name": score.name,
                                "reason": _tg_res.blocked_reason, "intent": "sell",
                                "quantity": quantity,
                            })
                            logger.info(
                                f"[Phase 2] GATE-1 enabled：{score.name}({score.symbol}) 卖单顺延"
                                f"（{_tg_res.blocked_reason}），不计入当日成交与预算"
                            )
                            # CR#5：标为已处理，防 Step 1.5 将同一标的再列为削减候选（重复留痕）
                            remaining_qty[score.symbol] = 0
                            continue
                        _tg_flag, _tg_reason = True, _tg_res.blocked_reason
                    elif _tg_res.blocked_reason == "quote_unavailable":
                        # CR#3：降级放行（实际照常执行），仅留原因告警，不标 pending_exit
                        _tg_flag, _tg_reason = False, _tg_res.blocked_reason
                    else:
                        _tg_flag, _tg_reason = False, None
                else:
                    _tg_flag, _tg_reason = False, None
                # 强制卖出（止损/衰竭/过热/板块踢出）
                sell_value = quantity * last_price
                sell_proceeds += sell_value
                remaining_qty[score.symbol] = 0  # 评审修复#1：全清 → Step 1.5 不再触及

                allocations.append(
                    TradeAllocation(
                        symbol=score.symbol,
                        name=score.name,
                        action="sell",
                        target_quantity=0,
                        target_value=0.0,
                        current_quantity=quantity,
                        quantity_delta=-quantity,
                        price_range=(
                            last_price * 0.95,
                            last_price,
                        ),
                        expected_return=pnl_pct_decimal,
                        stop_loss=0.0,
                        risk_reward_ratio=0.0,
                        confidence=0.9,
                        reasoning=score.reasoning,
                        pending_exit=_tg_flag,
                        blocked_reason=_tg_reason,
                    )
                )
            elif action == "reduce":
                # 减仓 50%（成熟后期/晚期阶段）
                reduce_quantity = int(quantity * 0.5 / 100) * 100  # 取整到 100 股
                if reduce_quantity > 0:
                    # GATE-1 enabled：不可成交减仓转顺延——不落零股数卖单，
                    # 留 pending_exit=True 且 quantity_delta=0 的观测记录
                    if _tg_enabled:
                        _tg_res = _tradability_check(score.symbol, score.name, "sell")
                        if not _tg_res.executable:
                            pending_exits.append({
                                "symbol": score.symbol, "name": score.name,
                                "reason": _tg_res.blocked_reason, "intent": "reduce",
                                "quantity": reduce_quantity,
                            })
                            allocations.append(
                                TradeAllocation(
                                    symbol=score.symbol,
                                    name=score.name,
                                    action="sell",
                                    target_quantity=quantity,
                                    target_value=quantity * last_price,
                                    current_quantity=quantity,
                                    quantity_delta=0,
                                    price_range=(last_price * 0.98, last_price),
                                    expected_return=pnl_pct_decimal,
                                    stop_loss=round(last_price * 0.93, 2),
                                    risk_reward_ratio=0.0,
                                    confidence=0.8,
                                    reasoning=(
                                        f"{score.reasoning}, "
                                        f"[GATE-1 顺延: {_tg_res.blocked_reason} 不可成交，减仓待复核]"
                                    ),
                                    pending_exit=True,
                                    blocked_reason=_tg_res.blocked_reason,
                                )
                            )
                            logger.info(
                                f"[Phase 2] GATE-1 enabled：{score.name}({score.symbol}) 减仓顺延"
                                f"（{_tg_res.blocked_reason}）"
                            )
                            # CR#5：标为已处理，防 Step 1.5 重复削减同一标的
                            remaining_qty[score.symbol] = 0
                            continue
                    if _tg_shadow:
                        _tg_res = _tradability_check(score.symbol, score.name, "sell")
                        if not _tg_res.executable:
                            _tg_flag, _tg_reason = True, _tg_res.blocked_reason
                        elif _tg_res.blocked_reason == "quote_unavailable":
                            # CR#3：降级放行不标 pending_exit，仅留原因
                            _tg_flag, _tg_reason = False, _tg_res.blocked_reason
                        else:
                            _tg_flag, _tg_reason = False, None
                    else:
                        _tg_flag, _tg_reason = False, None
                    sell_value = reduce_quantity * last_price
                    sell_proceeds += sell_value
                    # 评审修复#1：扣减剩余敞口真相，Step 1.5 只可再削未处理部分
                    remaining_qty[score.symbol] = quantity - reduce_quantity

                    allocations.append(
                        TradeAllocation(
                            symbol=score.symbol,
                            name=score.name,
                            action="sell",
                            target_quantity=quantity - reduce_quantity,
                            target_value=(quantity - reduce_quantity) * last_price,
                            current_quantity=quantity,
                            quantity_delta=-reduce_quantity,
                            price_range=(
                                last_price * 0.98,
                                last_price,
                            ),
                            expected_return=pnl_pct_decimal,
                            stop_loss=round(last_price * 0.93, 2),  # 保留部分仓位，止损放宽
                            risk_reward_ratio=0.0,
                            confidence=0.8,
                            reasoning=score.reasoning,
                            pending_exit=_tg_flag,
                            blocked_reason=_tg_reason,
                        )
                    )

        # ── Step 1.5: 组合层超限减仓·卖出来源（P1-4 / OPEN-1 W2，排期批 4.1，2026-09-28）──
        # 缺口（W1/W2）：Step 1 只由个股 action_recommendation 驱动卖出；当「个股未触发卖出、
        # 但组合市值仍超目标仓位上限」时无任何减仓路径（Step 2 反而用释放资金回补 → 不减反买）。
        # 本步补齐「卖出来源」：Step 1 卖出后若仍超限，按综合分升序削最弱持仓，直到回落至上限。
        # 复用 Fix 3 宽限：sector_drift_grace=True 的持仓（板块漂移但家族仍强、已降级观察）不纳入
        #   减仓候选，防 688598 式误卖——直接消费打分阶段落盘的宽限标记，不解析 reasoning 字符串
        #   （规避 W4 脆弱耦合，W4 明确不在本批）。
        # 三态开关（仿 ENABLED+SHADOW 双开关惯例，本项风险最高，先观察再转正）：
        #   GOT_SELL_SOURCE_ENABLED=1 → 生效：真减仓（写 allocations + 累加 sell_proceeds + over_limit_forced）
        #   ENABLED=0 & GOT_SELL_SOURCE_SHADOW=1（默认）→ 影子观察：只算「会削谁/多少/是否撞宽限」落
        #       self._sell_source_shadow 留痕，**不写 allocations、不改 sell_proceeds/over_limit_forced**
        #       → Step 2/3 决策逐字节不变（仅新增归档观测字段，按 raw_position_ratio/#5a 先例移出不变组）
        #   两者皆 0 → 完全不进（逐字节还原改动前）
        over_limit_forced = False
        self._sell_source_shadow = None
        _ss_enabled = os.getenv("GOT_SELL_SOURCE_ENABLED", "0") == "1"
        _ss_shadow = os.getenv("GOT_SELL_SOURCE_SHADOW", "1") == "1"
        if _ss_enabled or _ss_shadow:
            post_sell_value = total_market_value - sell_proceeds
            excess_value = post_sell_value - target_position_value
            if excess_value > 0:
                # 评审修复#1：候选与削减量一律基于 remaining_qty（Step 1 真相）——
                # 已被 sell/reduce 处理过的标的只剩「未处理余量」可削，杜绝重复卖单。
                trimmables = [
                    s for s in unified_scoring
                    if s.is_holding and not s.sector_drift_grace
                    and remaining_qty.get(s.symbol, 0) > 0
                ]
                trimmables.sort(key=lambda x: x.composite_score)  # 先削最弱
                grace_skipped = [
                    s.symbol for s in unified_scoring
                    if s.is_holding and s.sector_drift_grace
                ]
                # 先算「减仓计划」（生效/影子共用同一算法，影子模式只算不落决策）
                plan = []  # [(score, quantity, last_price, reduce_quantity, sell_value)]
                projected_value = 0.0
                for score in trimmables:
                    remaining_gap = excess_value - projected_value
                    if remaining_gap <= 0:
                        break
                    position = holdings_map.get(score.symbol, {})
                    quantity = remaining_qty.get(score.symbol, 0)  # 评审修复#1：剩余敞口而非原始股数
                    last_price = position.get("last_price", 0.0)
                    if quantity <= 0 or last_price <= 0:
                        continue
                    # 需削减股数：覆盖 remaining_gap 的最小整手（向上取整到 100），封顶清仓。
                    # 评审修复#2：去掉 max(100,…)——零股（<100）按整笔清仓处理，
                    # 不再被抬回一手导致负 target_quantity（逆仓内「不足一手不留负数」惯例）。
                    reduce_quantity = min(math.ceil(remaining_gap / last_price / 100) * 100, quantity)
                    if reduce_quantity <= 0:
                        continue
                    sell_value = reduce_quantity * last_price
                    projected_value += sell_value
                    plan.append((score, quantity, last_price, reduce_quantity, sell_value))
                # 留痕（生效与影子都记，供归档复核「超限将削谁、是否撞宽限」）
                self._sell_source_shadow = {
                    "mode": "enabled" if _ss_enabled else "shadow",
                    "excess_value": round(excess_value, 2),
                    "target_position_value": round(target_position_value, 2),
                    "projected_trim_value": round(projected_value, 2),
                    "planned_trims": [
                        {
                            "symbol": s.symbol,
                            "name": s.name,
                            "reduce_quantity": rq,
                            "sell_value": round(sv, 2),
                        }
                        for (s, _q, _lp, rq, sv) in plan
                    ],
                    "grace_skipped_symbols": grace_skipped,
                    "note": (
                        "W2 卖出来源留痕：shadow 模式不写 allocation/不改决策，"
                        "仅观测超限将削谁、是否撞家族宽限；enabled 模式方落实减仓"
                    ),
                }
                # GATE-1：给计划内卖单补可成交性标记（shadow/enabled 均留痕，不改计划本身）
                if _tg_active:
                    for _trim in self._sell_source_shadow["planned_trims"]:
                        _tres = _tradability_check(
                            _trim["symbol"], _trim["name"], "sell"
                        )
                        if not _tres.executable:
                            _trim["tradability_blocked"] = True
                            _trim["blocked_reason"] = _tres.blocked_reason
                if _ss_enabled:
                    over_limit_forced = True
                    for (score, quantity, last_price, reduce_quantity, sell_value) in plan:
                        _tg_flag_w, _tg_reason_w = False, None
                        _tres_w = None
                        if _tg_active:
                            _tres_w = _tradability_check(score.symbol, score.name, "sell")
                            if not _tres_w.executable:
                                _tg_flag_w, _tg_reason_w = True, _tres_w.blocked_reason
                            elif _tres_w.blocked_reason == "quote_unavailable":
                                # CR#3：降级放行不标 pending_exit，仅留原因
                                _tg_flag_w, _tg_reason_w = False, _tres_w.blocked_reason
                        # GATE-1 enabled：不可成交的超限削单跳过（顺延，次日计划重入）
                        if _tg_enabled and _tres_w is not None and not _tres_w.executable:
                            pending_exits.append({
                                "symbol": score.symbol, "name": score.name,
                                "reason": _tres_w.blocked_reason,
                                "intent": "sell_source_trim",
                                "quantity": reduce_quantity,
                            })
                            logger.info(
                                f"[Phase 2] GATE-1 enabled：W2 超限削单 {score.name}"
                                f"({score.symbol}) 顺延（{_tres_w.blocked_reason}）"
                            )
                            continue
                        pnl_pct = holdings_map.get(score.symbol, {}).get(
                            "unrealized_pnl_pct", 0.0
                        ) or 0.0
                        sell_proceeds += sell_value
                        allocations.append(
                            TradeAllocation(
                                symbol=score.symbol,
                                name=score.name,
                                action="sell",
                                target_quantity=quantity - reduce_quantity,
                                target_value=(quantity - reduce_quantity) * last_price,
                                current_quantity=quantity,
                                quantity_delta=-reduce_quantity,
                                price_range=(last_price * 0.98, last_price),
                                expected_return=pnl_pct / 100.0,
                                stop_loss=round(last_price * 0.93, 2),
                                risk_reward_ratio=0.0,
                                confidence=0.7,
                                reasoning=(
                                    f"{score.reasoning}, "
                                    f"[组合超限减仓: 削 ¥{sell_value:,.0f} 使仓位回落至上限]"
                                ),
                                pending_exit=_tg_flag_w,
                                blocked_reason=_tg_reason_w,
                            )
                        )
                        logger.info(
                            f"[Phase 2] W2 超限减仓：{score.name}({score.symbol}) 削 "
                            f"{reduce_quantity} 股（¥{sell_value:,.0f}），"
                            f"累计减仓 ¥{projected_value:,.0f} / 缺口 ¥{excess_value:,.0f}"
                        )
                    if not plan:
                        logger.info(
                            "[Phase 2] W2 超限但持仓均获家族宽限/无可削 → 不强卖（仅抑制回补）"
                        )
                else:
                    # 影子：不改 sell_proceeds/over_limit_forced，Step 2/3 逐字节走旧逻辑
                    logger.info(
                        f"[Phase 2] W2 卖出来源·影子观察：超限 ¥{excess_value:,.0f}，"
                        f"本会削 {len(plan)} 只（累计 ¥{projected_value:,.0f}），"
                        f"家族宽限跳过 {len(grace_skipped)} 只——不落决策"
                    )

        # ── Step 2: 计算买入预算（包括卖出释放的资金）──
        # 可用资金 = 现有现金 + 卖出释放的资金
        available_cash = total_cash + sell_proceeds

        # 目标买入金额：使仓位达到目标水平
        # 卖出后的市值 = 当前市值 - 卖出金额
        new_market_value = total_market_value - sell_proceeds
        position_gap = target_position_value - new_market_value

        # ── Phase 3b：RiskOverridePlan 注入（批次 5 §3.3，Step 5 LLM 精炼前的预算闸门）──
        # 三态开关（与 4b/5c 同惯例）：shadow（默认）=只算 RiskOverridePlan 落
        #   self._risk_override_shadow 观测、**不改 buy_budget**（旧路径逐字节不变）；
        #   enabled 且 veto_buy → 与 over_limit_forced 并列把 buy_budget 归零（风控优先于既有降级规则）。
        # 真风控否决源 = Step 1.5 卖出来源确已 enabled 强平（sell_source mode=enabled 且有减仓）；
        #   默认两者皆影子 → _ro_veto_buy 恒 False → 决策零改动。
        self._risk_override_shadow = None
        _ro_veto_buy = False
        _dg_shadow_on = os.getenv("GOT_DISAGREEMENT_SHADOW", "1") == "1"
        _dg_enabled_on = os.getenv("GOT_DISAGREEMENT_ENABLED", "0") == "1"
        if _dg_shadow_on or _dg_enabled_on:
            try:
                from src.got.disagreement import build_disagreement_summary, build_opinions

                _ss = getattr(self, "_sell_source_shadow", None) or {}
                _ro_present = _ss.get("mode") == "enabled" and bool(_ss.get("planned_trims"))
                _dg_opinions = build_opinions(
                    regime=getattr(unified_context, "market_regime", None),
                    sector=None,
                    sector_intels=getattr(unified_context, "confirmed_sectors", None),
                    capital=None,
                    sentiment=None,
                    sell_source=_ss or None,
                )
                _dg_summary = build_disagreement_summary(
                    _dg_opinions, risk_override_present=_ro_present, memory=None
                )
                _ro_plan = _dg_summary.get("risk_control", {})
                self._risk_override_shadow = {
                    "mode": "enabled" if _dg_enabled_on else "shadow",
                    "conflict_type": _dg_summary.get("conflict_type", ""),
                    "decision_path_hint": _dg_summary.get("decision_path_hint", ""),
                    "plan": _ro_plan,
                    "note": (
                        "3b 风控覆盖留痕：shadow 模式不改 buy_budget（仅记录冲突分类/计划）；"
                        "enabled 且 veto_buy 时与 over_limit_forced 并列使预算归零"
                    ),
                }
                if _dg_enabled_on and _ro_plan.get("veto_buy"):
                    _ro_veto_buy = True
                    logger.info(
                        "[Phase 2] 3b enabled：RiskOverridePlan.veto_buy → 买入预算归零"
                    )
            except Exception as exc:  # 观测旁路：异常降级为 None，绝不阻塞 Phase 2 主流程
                logger.warning("[Phase 2] 3b 风控覆盖计算失败（已忽略，影子字段置空）: %s", exc)
                self._risk_override_shadow = None
                _ro_veto_buy = False

        # 买入预算 = min(仓位缺口, 可用资金)
        # 评审修复#3：over_limit_forced 提为前置短路——Step 1.5 整手向上取整常态会留下
        # 小正 gap（削过头），若让 position_gap>0 抢先，就会拿减仓款回补，违背 W1
        # 「本轮暂停买入」承诺。仅 enabled 且确曾超限时为 True；影子/全关恒 False，旧路径逐字节不变。
        if over_limit_forced:
            # W1「超限反而继续买入」闭环：Step 1.5 减仓是为把仓位压回上限，属风控强平、
            # 非「卖弱买强」的轮动信号 → 不得立刻用减仓款回补。预算归零。
            buy_budget = 0.0
            logger.info(
                f"[Phase 2] W1 超限处置：仓位已压至上限 {target_position_value:,.0f} 附近，"
                f"减仓款 ¥{sell_proceeds:,.0f} 不回补（本轮暂停买入）"
            )
        elif _ro_veto_buy:
            # 3b 风控否决（enabled 且 RiskOverridePlan.veto_buy）：与 over_limit_forced 并列，
            # 预算归零。_ro_veto_buy 在影子/全关恒 False → 此分支不触发 → 旧路径逐字节不变。
            buy_budget = 0.0
            logger.info(
                f"[Phase 2] 3b 风控否决：本轮暂停买入（buy_budget=0，¥{available_cash:,.0f} 可用不回补）"
            )
        elif position_gap > 0:
            buy_budget = min(position_gap, available_cash)
        else:
            # 即使仓位已达标，如果有卖出操作，也应该用释放的资金买入新股
            # 这是"高切低"策略的核心：卖出衰弱板块 → 买入新兴板块
            buy_budget = min(sell_proceeds * 0.8, available_cash)  # 保留 20% 现金作为缓冲
            if buy_budget > 0:
                logger.info(
                    f"[Phase 2] 高切低轮动：卖出释放 {sell_proceeds:.0f} 元，"
                    f"买入预算 {buy_budget:.0f} 元"
                )

        # ── Step 3: 生成买入建议 ──
        # 预收集 BUY 候选（无论预算是否充足），预算不足时显式透出被否决信号
        # Q9-B: 板块验证失败的候选不进 allocation（拆分为两组，剔除可追溯）
        buy_candidates = [
            s
            for s in unified_scoring
            if not s.is_holding and s.action_recommendation.startswith("buy") and s.sector_available
        ]
        buy_candidates.sort(key=lambda x: x.composite_score, reverse=True)
        # Q9-B 可追溯性（审核 W10 修复）：sector 失效的 buy* 候选既不进 allocation
        # 也不该静默消失 → 并入否决列表（附否决原因，与“预算不足显式透出”契约对齐）
        sector_vetoed = [
            s
            for s in unified_scoring
            if not s.is_holding and s.action_recommendation.startswith("buy") and not s.sector_available
        ]

        # ── WI-P0-3 / WI-P0-4 shadow 留痕初始化（纯计算，零 IO）──
        # P0-3：敞口截断观测——sector_exposure_limits 目前无执行消费者，shadow 只记
        #   「若强制会截断多少」；conf_w（:357 参 limit 公式）未经 ECE 校准前
        #   不得让 limit 间接驱动 sizing（隔离前置，实施报告 §3 P0-3）。
        # P0-4：等分 vs 信号加权仓位差观测；γ 为待 P0-5 标定占位值（红线 6）。
        _ee_mode = os.getenv("GOT_EXPOSURE_ENFORCE", "shadow").strip().lower()
        _ee_shadow = _ee_mode == "shadow"
        _ssz_mode = os.getenv("GOT_SIGNAL_SIZE", "shadow").strip().lower()
        _ssz_shadow = _ssz_mode == "shadow"
        exposure_checks: List[Dict[str, Any]] = []
        _score_by_symbol: Dict[str, float] = {}

        if buy_budget > 0:
            candidates = buy_candidates

            # 差异化仓位分配（参考 Garrone 2026 Structural Optionality Budget）
            # D-P13: 用 sector_role（来自 unified_context.confirmed_sectors）判定分支。
            # 未命中映射时 fallback 为 "mainline"（反推逻辑已在 D-P13 移除，
            # sector_exposure_limits 仅作参考值落盘，不再参与分支判定）
            sector_limits = risk.sector_exposure_limits

            # 构建 sector_name → sector_role 映射（D-P13 完整实现）
            sector_role_map = {}
            if unified_context and unified_context.confirmed_sectors:
                sector_role_map = {
                    s.sector_name: s.sector_role
                    for s in unified_context.confirmed_sectors
                    if s.sector_name
                }

            # 每只候选股的基础预算
            actual_buy_count = min(len(candidates), 3)
            base_budget = buy_budget / max(actual_buy_count, 1)

            for score in candidates[:3]:  # 最多买 3 只
                # 获取实时价格（只有 3 只，很快）
                estimated_price = self._get_realtime_price(score.symbol)
                if estimated_price is None or estimated_price <= 0:
                    logger.warning(f"[Phase 2] 无法获取 {score.symbol} 价格，跳过")
                    continue

                # GATE-1: 涨停一字买入拦截（enabled 不进成交集合；shadow 仅留痕）
                _tg_buy_flag = _tg_buy_reason = None
                if _tg_active:
                    _tres_b = _tradability_check(score.symbol, score.name, "buy")
                    if not _tres_b.executable:
                        if _tg_enabled:
                            pending_exits.append({
                                "symbol": score.symbol, "name": score.name,
                                "reason": _tres_b.blocked_reason, "intent": "buy_blocked",
                                "quantity": 0,
                            })
                            logger.info(
                                f"[Phase 2] GATE-1 enabled：{score.name}({score.symbol})"
                                f" 买单禁入（{_tres_b.blocked_reason}）"
                            )
                            continue
                        _tg_buy_flag, _tg_buy_reason = True, _tres_b.blocked_reason
                    elif _tres_b.blocked_reason == "quote_unavailable":
                        # CR#3：降级放行（买单照常），仅留原因告警，不标 pending_exit
                        _tg_buy_flag, _tg_buy_reason = False, _tres_b.blocked_reason

                # 板块验证清空的候选（sector_name 为空）走 unknown 保守分支（乘数 0.55，与埋伏同档）
                # 而非默认主线——验证失败是负面信号，不应获得主线标准预算
                if score.sector_name:
                    sector_role = sector_role_map.get(score.sector_name, DEFAULT_SECTOR_ROLE)
                    sector_limit = sector_limits.get(score.sector_name, 0.20)
                else:
                    sector_role = "unknown"
                    sector_limit = 0.20

                # 修正（2026-09-11）：cooling 此前落入 else 拿到主线仓位与止损，与本项目
                # 「cooling 最低档」的设计意图相反 —— ROLE_MULTIPLIER["cooling"] 从未对任何
                # 仓位生效（sector_exposure_limits 全仓无执行消费者，仅用于落盘记录）。
                role_budget_m = ROLE_MULTIPLIER.get(sector_role, ROLE_MULTIPLIER[DEFAULT_SECTOR_ROLE])
                stop_loss_pct = ROLE_STOP_LOSS.get(sector_role, ROLE_STOP_LOSS[DEFAULT_SECTOR_ROLE])
                per_stock_budget = base_budget * role_budget_m
                branch = sector_role

                quantity = int(per_stock_budget / estimated_price / 100) * 100

                _score_by_symbol[score.symbol] = score.composite_score

                if quantity == 0:
                    # A 股最小可买单位为 100 股：高价股的整手金额可能超出单只预算，
                    # 取整后归零。此前该路径无任何日志，候选被静默淘汰（审核 F-3）
                    logger.warning(
                        f"[Phase 2] {score.name} ({score.symbol}) 跳过：买入 100 股需 "
                        f"¥{estimated_price * 100:,.0f}，超出单只预算 ¥{per_stock_budget:,.0f}"
                    )
                    continue

                # WI-P0-3 shadow：若强制敞口会截断多少（仅观测，不改 quantity/预算）
                if _ee_shadow:
                    _cap_value = sector_limit * total_equity
                    _want_value = quantity * estimated_price
                    if _want_value > _cap_value:
                        exposure_checks.append({
                            "symbol": score.symbol,
                            "sector_name": score.sector_name,
                            "limit": sector_limit,
                            "would_truncate_from": round(_want_value, 2),
                            "cap_value": round(_cap_value, 2),
                            "overflow_value": round(_want_value - _cap_value, 2),
                        })

                # D-P11: 落盘分支走向，使行为变更可见。
                # 判据是 sector_role（D-P13），不再由 limit 反推；
                # stock_limit 仅作参考值（未命中 sector_limits 时为 0.20）
                sector_limit_detail = {
                    "sector_name": score.sector_name,
                    "sector_role": sector_role,
                    "stock_limit": sector_limit,
                    "branch": branch,
                }

                allocations.append(
                    TradeAllocation(
                        symbol=score.symbol,
                        name=score.name,
                        action="buy",
                        target_quantity=quantity,
                        target_value=quantity * estimated_price,
                        current_quantity=0,
                        quantity_delta=quantity,
                        price_range=(
                            estimated_price * 0.98,
                            estimated_price * 1.02,
                        ),
                        expected_return=0.10,  # 目标 +10%
                        stop_loss=round(estimated_price * (1 - stop_loss_pct), 2),
                        risk_reward_ratio=round(0.10 / stop_loss_pct, 2),
                        confidence=score.confidence,
                        reasoning=score.reasoning,
                        sector_limit_detail=sector_limit_detail,
                        intraday_snapshot=not bar_complete,  # Q4 audit: 盘中快照标记
                        pending_exit=bool(_tg_buy_flag),
                        blocked_reason=_tg_buy_reason,
                    )
                )

        # 预算不足时，全部 BUY 候选被约束否决 → 显式透出（消除"高分候选 vs 0 交易"静默矛盾）
        vetoed_candidates = buy_candidates if buy_budget <= 0 else []

        # ── WI-P0-3 shadow 留痕落盘（仅留痕不参与决策；#5a 先例不进不变组）──
        self._exposure_enforce_shadow = {
            "mode": _ee_mode if buy_budget > 0 else "n/a",
            "would_truncate_count": len(exposure_checks),
            "would_truncate_total_value": round(
                sum(c["overflow_value"] for c in exposure_checks), 2
            ),
            "checks": exposure_checks,
            "note": (
                "P0-3 敞口执行接线观测：limit 含 conf_w 未校准因子，enabled 前置条件是 "
                "ECE 达标（P0-5）；shadow 仅记若强制会截断多少，决策逐字节不变"
            ),
        } if _ee_shadow else None

        # ── WI-P0-4 shadow：等分 vs 信号加权（w_i = score^γ 归一）仓位差留痕 ──
        self._signal_size_shadow = None
        if _ssz_shadow and buy_budget > 0 and allocations:
            _gamma = float(os.getenv("GOT_SIGNAL_SIZE_GAMMA", "0.5"))
            _buy_allocs = [a for a in allocations if a.action == "buy"]
            _scores = [
                max(_score_by_symbol.get(a.symbol, 0.0), 1.0) for a in _buy_allocs
            ]
            if _buy_allocs and sum(_scores) > 0:
                _ws = [s ** _gamma for s in _scores]
                _wsum = sum(_ws)
                _sig_items = []
                for _a, _w in zip(_buy_allocs, _ws):
                    _sig_val = buy_budget * _w / _wsum
                    _sig_items.append({
                        "symbol": _a.symbol,
                        "name": _a.name,
                        "equal_value": round(_a.target_value, 2),
                        "scored_value": round(_sig_val, 2),
                        "delta": round(_sig_val - _a.target_value, 2),
                    })
                self._signal_size_shadow = {
                    "mode": "shadow",
                    "gamma": _gamma,
                    "gamma_status": "待 WI-P0-5 标定（占位 0.5，γ<1 收敛马太效应）",
                    "items": _sig_items,
                    "max_abs_delta": round(
                        max(abs(i["delta"]) for i in _sig_items), 2
                    ),
                    "note": (
                        "P0-4 信号强度连续缩放观测：未乘 role_multiplier 的裸预算口径对比；"
                        "confidence 不直接进 sizing（除非 ECE 达标）；shadow 不改任何仓位"
                    ),
                }

        # GATE-1 留痕：被拦截/打标的顺延清单（供归档复核，shadow 与 enabled 均记）
        self._tradability_pending = {
            "mode": "enabled" if _tg_enabled else ("shadow" if _tg_shadow else "off"),
            "pending_exits": pending_exits,
            "note": (
                "GATE-1 可成交性投影：enabled 下被拦截卖单不进当日成交集合（顺延待次日复核）；"
                "shadow 下仅打 pending_exit/blocked_reason 观测标签，决策逐字节不变"
            ),
        } if _tg_active else None
        # Q9-B：板块验证失败被禁入的候选同样显式透出（按综合分降序并入，避免与预算否决重复列示）
        if sector_vetoed:
            logger.info(
                "[Phase 2] Q9-B 板块验证失败禁入买入池: %s",
                "、".join(f"{s.name}({s.composite_score:.1f})" for s in sector_vetoed[:5]),
            )
            vetoed_candidates = sorted(
                list(vetoed_candidates) + list(sector_vetoed),
                key=lambda x: x.composite_score, reverse=True,
            )

        return allocations, vetoed_candidates

    # ── LLM 精炼 ──

    async def _llm_refinement(
        self,
        risk: PortfolioRiskAssessment,
        scoring: List[UnifiedScoringResult],
        allocation: List[TradeAllocation],
        unified: UnifiedContext,
        portfolio: Dict[str, Any],
        vetoed_candidates: Optional[List[UnifiedScoringResult]] = None,
    ) -> str:
        """生成可追溯的推理总结"""
        # 持仓盈亏汇总
        holdings = [s for s in scoring if s.is_holding]
        if holdings:
            pnl_pcts = []
            for h in holdings:
                # 从 scoring 中提取 pnl（在 reasoning 中）
                import re
                m = re.search(r'P&L\s+([\-\d.]+)%', h.reasoning)
                if m:
                    pnl_pcts.append(float(m.group(1)))
            avg_pnl = sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0
            holdings_text = "，".join(
                f"{h.name}({h.symbol})+{pnl_pcts[i]:.1f}%" if pnl_pcts[i] > 0 else f"{h.name}({h.symbol}){pnl_pcts[i]:.1f}%"
                for i, h in enumerate(holdings)
            )
            portfolio_summary = f"持仓盈亏：{holdings_text}，综合盈亏约{avg_pnl:+.1f}%"
        else:
            portfolio_summary = "当前无持仓"

        # 格式化 Top 5 评分
        top_scoring_text = "\n".join(
            [
                f"  {i+1}. {s.name} ({s.symbol}) "
                f"{'【持仓】' if s.is_holding else ''}: "
                f"{s.composite_score:.1f} 分 — {s.reasoning}"
                for i, s in enumerate(scoring[:5])
            ]
        )

        # 格式化交易建议
        allocation_text = "\n".join(
            [
                f"  {a.action.upper()} {a.name} ({a.symbol}): "
                f"{a.quantity_delta:+d} 股, ¥{a.target_value:,.0f} — {a.reasoning}"
                for a in allocation
            ]
        )

        # 被预算约束否决的 BUY 候选（显式披露，消除"高分候选 vs 0 交易"的静默矛盾）
        vetoed_text = ""
        if vetoed_candidates:
            vetoed_text = "\n".join(
                f"  - {s.name} ({s.symbol}): {s.composite_score:.1f} 分, 建议动作 {s.action_recommendation}"
                for s in vetoed_candidates[:5]
            )
            vetoed_text = (
                "\n以下 BUY 候选已通过评分，但受预算/仓位上限约束未获资金分配"
                f"（当前仓位 {risk.current_position_ratio:.0%} ≥ 上限 {risk.position_limit:.0%}，卖出释放资金为 0）：\n"
                f"{vetoed_text}\n"
            )

        prompt = f"""
当前风险评估:
- 仓位上限: {risk.position_limit:.0%}
- 当前仓位: {risk.current_position_ratio:.0%}
- 止损线: {risk.stop_loss_threshold:.0%}
市场背景:
- 综合研判: {unified.unified_signal}
- 研判置信度: {unified.overall_confidence:.0%}
- 关键风险: {', '.join(unified.key_risks[:3])}

{portfolio_summary}

统一打分 Top 5:
{top_scoring_text}

初步分配建议:
{allocation_text if allocation_text else "  无（当前无需操作）"}
{vetoed_text}

请生成一段简洁的推理总结（300 字以内），包含以下两部分:

**第一部分：整体决策理由**
1. 为什么这样分配仓位？
2. 主要风险点是什么？
3. 有什么需要特别注意的？

**第二部分：逐只持仓分析**
如果当前持仓且没有操作，请逐只说明保留理由（格式：股票名：理由）

重要：你的总结必须与上述"初步分配建议"一致。如果有 BUY 操作，就解释为什么买入；如果没有操作，就解释为什么维持现状。不要自行判断是否需要买入——分配建议已经由规则引擎确定。

注意：用通俗易懂的语言，避免使用技术术语。
引用评分时必须使用上方给出的精确数值（如"69.5 分"），不得四舍五入或自行重算。
"""

        try:
            from src.agent.llm_adapter import LLMToolAdapter

            adapter = LLMToolAdapter()
            response = await asyncio.to_thread(
                adapter.call_text,
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500,
            )
            return (response.content or "").strip()
        except Exception as e:
            logger.warning(f"[Phase 2] LLM 精炼失败，降级到规则总结: {e}")
            return self._rule_based_summary(risk, allocation)

    # ── 辅助方法 ──

    def _get_realtime_price(self, symbol: str) -> float:
        """获取实时价格（mootdx TCP → 腾讯降级）"""
        try:
            from data_provider.base import DataFetcherManager
            manager = DataFetcherManager()
            quote = manager.get_realtime_quote(symbol)
            if quote and quote.price > 0:
                return quote.price
        except Exception as e:
            logger.debug(f"[Phase 2] 获取 {symbol} 价格失败: {e}")
        return 0.0

    def _get_portfolio_snapshot(self) -> Dict[str, Any]:
        """获取当前持仓快照"""
        try:
            from src.services.portfolio_service import PortfolioService

            service = PortfolioService()
            snapshot = service.get_portfolio_snapshot()

            # 空账户时使用默认资金（确保 Phase 2 可以生成买入建议）
            if not snapshot.get("accounts") and snapshot.get("total_equity", 0) <= 0:
                logger.info("[Phase 2] 无持仓账户，使用默认资金 10 万元")
                return {
                    "total_equity": 100000.0,
                    "total_market_value": 0.0,
                    "total_cash": 100000.0,
                    "accounts": [],
                }

            return snapshot
        except Exception as e:
            logger.warning(f"[Phase 2] 获取持仓失败，返回空持仓: {e}")
            return {
                "total_equity": 100000.0,
                "total_market_value": 0.0,
                "total_cash": 100000.0,
                "accounts": [],
            }

    def _generate_summary(
        self,
        allocation: List[TradeAllocation],
        risk: PortfolioRiskAssessment,
        vetoed_candidates: Optional[List[UnifiedScoringResult]] = None,
    ) -> str:
        """生成操作总结"""
        if not allocation:
            if vetoed_candidates:
                top_vetoed = "、".join(s.name for s in vetoed_candidates[:3])
                return (
                    f"当前不产生新买入（{len(vetoed_candidates)} 只 BUY 候选"
                    f"如{top_vetoed}…受仓位上限 {risk.position_limit:.0%} 约束被否决），维持现有仓位"
                )
            return "当前无需操作，维持现有仓位"

        buy_count = sum(1 for a in allocation if a.action == "buy")
        sell_count = sum(1 for a in allocation if a.action == "sell")
        total_value = sum(a.target_value for a in allocation if a.action == "buy")

        parts = []
        if sell_count > 0:
            parts.append(f"卖出 {sell_count} 只（止损/止盈）")
        if buy_count > 0:
            parts.append(f"买入 {buy_count} 只（预算 ¥{total_value:,.0f}）")

        return "，".join(parts)

    def _generate_warnings(
        self,
        allocation: List[TradeAllocation],
        risk: PortfolioRiskAssessment,
        unified: UnifiedContext,
    ) -> List[str]:
        """生成风险提示"""
        warnings = []

        # 止损触发
        sell_allocations = [a for a in allocation if a.action == "sell"]
        if sell_allocations:
            warnings.append(
                f"触发止损线，需卖出 {len(sell_allocations)} 只股票"
            )

        # 仓位超限
        if risk.current_position_ratio > risk.position_limit:
            warnings.append(
                f"当前仓位 {risk.current_position_ratio:.0%} "
                f"超过上限 {risk.position_limit:.0%}，需减仓"
            )

        # 市场信号
        if unified.unified_signal == "bearish":
            warnings.append("大盘偏空，建议控制仓位，避免追高")

        # 置信度低
        if unified.overall_confidence < 0.5:
            warnings.append("研判置信度较低，建议谨慎操作")

        # ── v3.5 S4：主线续航见顶风险警告 ──
        mainline_endurance = getattr(unified, "mainline_endurance", [])
        topping_risk_families = [
            e for e in mainline_endurance if e.endurance_band == "topping_risk"
        ]
        if topping_risk_families:
            family_names = ", ".join(e.name for e in topping_risk_families[:3])
            warnings.append(
                f"主线家族进入见顶风险期（{family_names}），密切关注破位信号"
            )

        return warnings

    def _rule_based_summary(
        self,
        risk: PortfolioRiskAssessment,
        allocation: List[TradeAllocation],
    ) -> str:
        """规则兜底的总结"""
        if not allocation:
            return "当前无需操作"

        buy_count = sum(1 for a in allocation if a.action == "buy")
        sell_count = sum(1 for a in allocation if a.action == "sell")

        parts = []
        if sell_count > 0:
            parts.append(f"卖出 {sell_count} 只")
        if buy_count > 0:
            parts.append(f"买入 {buy_count} 只")

        return "，".join(parts)
