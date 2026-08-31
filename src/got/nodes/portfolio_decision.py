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
import logging
from datetime import date
from typing import Any, Dict, List, Optional

from src.got.schemas import (
    CapitalContext,
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


class PortfolioDecisionNode:
    """Phase 2 统一决策节点"""

    def __init__(self, use_mock: bool = False):
        self.use_mock = use_mock

    async def run(
        self,
        unified_context: UnifiedContext,
        market_context: MarketContext,
        sector_context: SectorContext,
        capital_context: CapitalContext,
        news_context: NewsContext,
        sentiment_context: SentimentContext,
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

        # Step 3: 组合优化（规则 + LLM 混合）
        logger.info("[Phase 2] Step 3: 组合优化")
        allocation, vetoed_candidates = self._allocate(unified_scoring, risk_assessment, portfolio)

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

        # P4：埋伏仓买入快照写入（买入催化剂快照）
        ambush_buys = self._write_ambush_buy_snapshots(
            allocation, unified_scoring, unified_context,
        )
        if ambush_buys:
            logger.info(f"[Phase 2] P4 埋伏买入快照: {len(ambush_buys)} 条")

        # LLM 精炼（生成可追溯的推理总结）
        reasoning = await self._llm_refinement(
            risk_assessment, unified_scoring, allocation, unified_context, portfolio,
            vetoed_candidates=vetoed_candidates,
        )

        # 生成操作总结
        summary = self._generate_summary(allocation, risk_assessment, vetoed_candidates)

        # 生成风险提示
        warnings = self._generate_warnings(allocation, risk_assessment, unified_context)

        decision = PortfolioDecision(
            status="normal",
            risk_assessment=risk_assessment,
            unified_scoring=unified_scoring,
            allocation=allocation,
            summary=summary,
            warnings=warnings,
            reasoning=reasoning,
            rotation_signals=rotation_signals,
            crowding_warnings=crowding_warnings,
            ambush_exit_signals=ambush_exit_signals,
        )

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
        # 新版分类（TOP-N + conviction sizing）：
        # - actionable: 排名靠前，可选股（主线 100% 仓位，埋伏 50% 仓位）
        # - caution: 排名靠后但有潜力，缩小仓位（主线 30%，埋伏 15%）
        # - exclude: 不选股
        REGIME_MULTIPLIER = {"bull": 1.0, "transition": 0.65, "bear": 0.35}
        # cooling（高位降温）最低档：防新买入（2026-08-17 状态机校准新增）
        ROLE_MULTIPLIER = {"mainline": 1.0, "ambush": 0.55, "cooling": 0.3}
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
            f"风险预算 {risk_budget:.1%}"
        )

        return PortfolioRiskAssessment(
            position_limit=position_limit,
            current_position_ratio=min(1.0, current_position),  # 限制在 0-1
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

        # 构建板块情报映射（从 Node 6 v3 的 confirmed_sectors）
        sector_intel_map = self._build_sector_intel_map(unified)

        # 资金面全局评分（从 capital_context）
        capital_overall = self._extract_capital_overall(capital_context)

        # 消息面全局评分（从 news_context）
        news_overall = self._extract_news_overall(news_context)

        # 批量获取持仓股的真实技术指标评分（替代 P&L 代理）
        tech_scores_real = self._batch_fetch_tech_scores(positions)

        # ── P4：加载埋伏持仓快照（用于退出信号检测）──
        ambush_snapshot_map = self._load_ambush_snaphots(positions)

        # 批量计算资金面/消息面评分（复用 screener 的方法，避免重复实现）
        holding_candidates = []
        for position in positions:
            symbol = position.get("symbol", "")
            name = position.get("name", symbol)
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
            name = position.get("name", symbol)
            pnl_pct = position.get("unrealized_pnl_pct", 0.0) or 0.0
            pnl_pct_decimal = pnl_pct / 100.0

            # 查找该股票所属板块（从 sector_context 的 confirmed_sectors 匹配）
            stock_sector_info = self._find_stock_sector(
                symbol, unified,
                sector_strength_map=sector_strength_map,
                strength_lookup=strength_lookup,
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
            if sector_name and sector_name in sector_intel_map:
                intel = sector_intel_map[sector_name]
                lifecycle_phase = intel.get('lifecycle_phase')
                lifecycle_day = intel.get('lifecycle_day')
                # overheat_severity 从 sector_context 获取（需要额外处理）
                # family_name 从 sector_context 获取（需要额外处理）

            # 动作建议：生命周期驱动 + 板块踢出检测
            sector_strength = stock_sector_info.get("strength", 50.0)
            sector_role = stock_sector_info.get("sector_role", "")

            # 分层决策（方案 C：组合方案 + Invesco 2024 非对称门槛）
            if not sector_name:
                # 完全不在追踪范围
                action = "sell"
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
                action = "sell"
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
            if len(all_sectors) > 1:
                # 多个板块：显示加权平均和所有板块
                sectors_str = ", ".join(
                    f"{s['sector_name']}({s['strength']:.0f}分{'✓主线' if s['sector_type']=='active_mainline' else ''})"
                    for s in all_sectors[:3]  # 最多显示3个
                )
                sector_display = f"板块加权{sector_score:.0f}分[{sectors_str}]"
            elif sector_name:
                # 单个板块（正常匹配）
                if is_fallback:
                    # 降级匹配：显示板块名称但标注非主线
                    boards_str = "/".join(all_belong_boards[:3]) if all_belong_boards else sector_name
                    sector_display = f"板块[{boards_str}]（非主线，无评分）"
                else:
                    sector_display = f"板块[{sector_name}]强度{sector_score:.0f}{sector_intel_info}"
            else:
                # 无板块匹配：尝试显示所属板块列表
                if all_belong_boards:
                    boards_str = "/".join(all_belong_boards[:3])
                    sector_display = f"板块[{boards_str}]（非主线，无评分）"
                else:
                    sector_display = "板块[未知]"

            # 增强 reasoning：标注板块踢出情况
            if lifecycle_phase is None and sector_name:
                lifecycle_info = "（板块已踢出）"
            elif lifecycle_phase:
                lifecycle_info = f"[{lifecycle_phase}]"
            else:
                lifecycle_info = ""

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
                    lifecycle_phase=lifecycle_phase,
                    lifecycle_day=lifecycle_day,
                    overheat_severity=overheat_severity,
                    family_name=family_name,
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
                'overheat_severity': None,  # 从 sector_context 获取
            }

        return sector_intel_map

    def _adjust_score_by_sector_intel(
        self, base_score: float, sector_name: str, sector_intel_map: Dict[str, Dict[str, Any]]
    ) -> float:
        """根据板块情报调整股票评分

        调整逻辑（新版 conviction-weighted）：
        - actionable: 主线 +10, 埋伏 +5
        - caution: 主线 +3, 埋伏 +1（缩小但仍参与）
        - exclude: -20
        """
        if not sector_name or sector_name not in sector_intel_map:
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
                adjustment += 5.0   # 埋伏 actionable +5
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
        sentiment_check = intel.get('sentiment_check', 'medium')
        if sentiment_check == 'high':
            adjustment += 3.0
        elif sentiment_check == 'low':
            adjustment -= 5.0

        # 应用调整，限制在 [0, 100]
        adjusted_score = max(0.0, min(100.0, base_score + adjustment))
        return adjusted_score

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

    async def _verify_candidate_sectors(
        self,
        finalists: list,
        sector_intel_map: Dict[str, Dict],
    ) -> list:
        """候选股板块交叉验证（2026-08-18 新增）

        用 efinance.get_belong_board() 验证问财返回的板块标签是否真实。
        防止蹭概念票（如利君股份被问财错误归类为 TOPCon电）。

        验证逻辑：
        1. 对每只候选股，调用 efinance 获取其实际所属板块列表
        2. 检查 screener 标签的板块是否在 efinance 返回列表中
        3. 如果不在，清空板块标签（打分时板块维度给中性分 50）

        注意：只在候选股数量较少时（≤15）执行，避免过多 API 调用。
        """
        if not finalists or len(finalists) > 15:
            return finalists

        from data_provider.base import DataFetcherManager

        manager = DataFetcherManager()
        verified = []

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

                # 检查标签板块是否在实际板块中
                if tagged_sector in actual_board_names:
                    # 验证通过
                    logger.debug(
                        f"[板块验证] {stock.code} {stock.name}: "
                        f"✓ '{tagged_sector}' 在 efinance 板块列表中"
                    )
                    verified.append(stock)
                else:
                    # 验证失败：问财标签不在 efinance 返回列表中
                    logger.warning(
                        f"[板块验证] {stock.code} {stock.name}: "
                        f"✗ 问财标签'{tagged_sector}'不在 efinance 板块{list(actual_board_names)[:5]}...中，"
                        f"清空板块标签"
                    )
                    # 清空板块标签，打分时板块维度会给中性分
                    if hasattr(stock, 'sector'):
                        stock.sector = ""
                    if hasattr(stock, 'sector_name'):
                        stock.sector_name = ""
                    # 保留 sector_score 但降低权重（打分时会处理）
                    verified.append(stock)

            except Exception as e:
                # 验证过程出错，保留原标签（降级为信任问财）
                logger.warning(
                    f"[板块验证] {stock.code} {stock.name}: "
                    f"验证出错({e})，保留问财标签'{tagged_sector}'"
                )
                verified.append(stock)

        logger.info(
            f"[板块验证] 完成: {len(finalists)} 只候选股，"
            f"{len([s for s in verified if not (getattr(s, 'sector', '') or getattr(s, 'sector_name', ''))])} 只被清空标签"
        )

        return verified

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
        for board in boards:
            board_name = board.get("name", "") or board.get("board_name", "")
            canon = normalize_sector_name(board_name)
            if canon in confirmed_map:
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
            priority = 70  # 加速期（仍在鱼身，但已不是最佳入场点）
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
    ) -> List[Dict[str, Any]]:
        """P4 埋伏仓买入快照写入

        对每个 buy 类型的 allocation：
        1. 从 unified_scoring 找到该 symbol 的板块
        2. 若板块在 ambush_pool 中 → 写入催化剂快照到 SQLite
        3. 返回成功写入的快照列表（用于日志）

        降级：表不存在/写入失败 → 记录日志但不阻塞主流程。
        """
        buy_allocations = [a for a in allocation if a.action == "buy" and a.quantity_delta > 0]
        if not buy_allocations:
            return []

        # 构建 symbol → scoring 映射（获取板块名）
        symbol_to_scoring = {s.symbol: s for s in unified_scoring}

        # 构建板块名 → AmbushCandidate 映射
        ambush_pool_map = {
            c.sector_name: c for c in unified_context.ambush_pool
        }

        written = []
        try:
            from src.storage import AmbushPositionSnapshotRecord, DatabaseManager
            db = DatabaseManager()
            with db.get_session() as session:
                for alloc in buy_allocations:
                    scoring = symbol_to_scoring.get(alloc.symbol)
                    if not scoring:
                        continue

                    # 从 reasoning 中解析板块名（格式: "板块[XXX]强度XX..."）
                    sector_name = self._extract_sector_from_reasoning(alloc.reasoning)
                    if not sector_name or sector_name not in ambush_pool_map:
                        continue

                    candidate = ambush_pool_map[sector_name]
                    today = date.today()
                    entry_price = alloc.price_range[0] if alloc.price_range else 0.0

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
                    session.merge(record)
                    written.append({
                        "symbol": alloc.symbol,
                        "sector": sector_name,
                        "category_tag": candidate.category_tag,
                        "entry_price": entry_price,
                    })

                session.commit()

        except Exception as e:
            logger.warning(f"[P4] 埋伏买入快照写入失败（降级跳过）: {e}")

        return written

    @staticmethod
    def _extract_sector_from_reasoning(reasoning: str) -> str:
        """从 reasoning 字符串中提取板块名（格式: 板块[XXX]强度...）"""
        import re
        match = re.search(r'板块\[([^\]]+)\]', reasoning)
        return match.group(1) if match else ""

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
        for candidate in unified_context.ambush_pool:
            if candidate.sector_name not in existing_ambush_names:
                sector_ctx_dict["ambush"].append({
                    "name": candidate.sector_name,
                    "strength_score": strength_lookup.get(candidate.sector_name, 0.0),
                })
                existing_ambush_names.add(candidate.sector_name)
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
                "sector": True,
                "tech": True,
                "risk": True,
            }
            adjusted_composite, _actual_weights = weighted_score_with_redistribution(
                dimension_scores, dimension_available, SCORING_WEIGHTS
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

            # 动作建议：基于生命周期优先级
            if lifecycle_priority > 60 and lifecycle_phase in ["launch", "acceleration"]:
                action = "buy_trend"
            elif lifecycle_priority > 50:
                action = "buy_dip"
            else:
                action = "watch"

            results.append(
                UnifiedScoringResult(
                    symbol=stock.code,
                    name=stock.name,
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
                        (stock.ai_reasoning or f"综合评分 {adjusted_composite:.1f}")
                        + sector_intel_info
                    ),
                    lifecycle_phase=lifecycle_phase,
                    lifecycle_day=lifecycle_day,
                    family_name=family_name,
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

        # ── Step 1: 生成卖出建议并计算释放资金 ──
        sell_proceeds = 0.0  # 卖出释放的资金

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
                # 强制卖出（止损/衰竭/过热/板块踢出）
                sell_value = quantity * last_price
                sell_proceeds += sell_value

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
                    )
                )
            elif action == "reduce":
                # 减仓 50%（成熟后期/晚期阶段）
                reduce_quantity = int(quantity * 0.5 / 100) * 100  # 取整到 100 股
                if reduce_quantity > 0:
                    sell_value = reduce_quantity * last_price
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
                            price_range=(
                                last_price * 0.98,
                                last_price,
                            ),
                            expected_return=pnl_pct_decimal,
                            stop_loss=round(last_price * 0.93, 2),  # 保留部分仓位，止损放宽
                            risk_reward_ratio=0.0,
                            confidence=0.8,
                            reasoning=score.reasoning,
                        )
                    )

        # ── Step 2: 计算买入预算（包括卖出释放的资金）──
        # 可用资金 = 现有现金 + 卖出释放的资金
        available_cash = total_cash + sell_proceeds

        # 目标买入金额：使仓位达到目标水平
        # 卖出后的市值 = 当前市值 - 卖出金额
        new_market_value = total_market_value - sell_proceeds
        position_gap = target_position_value - new_market_value

        # 买入预算 = min(仓位缺口, 可用资金)
        if position_gap > 0:
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
        buy_candidates = [
            s
            for s in unified_scoring
            if not s.is_holding and s.action_recommendation.startswith("buy")
        ]
        buy_candidates.sort(key=lambda x: x.composite_score, reverse=True)

        if buy_budget > 0:
            candidates = buy_candidates

            # 差异化仓位分配（参考 Garrone 2026 Structural Optionality Budget）
            # 通过 sector_exposure_limits 推断角色：上限高=主线，上限低=埋伏
            sector_limits = risk.sector_exposure_limits
            max_limit = max(sector_limits.values()) if sector_limits else 0.40
            role_threshold = max_limit * 0.5  # 低于主线上限50%的视为埋伏

            # 每只候选股的基础预算
            actual_buy_count = min(len(candidates), 3)
            base_budget = buy_budget / max(actual_buy_count, 1)

            for score in candidates[:3]:  # 最多买 3 只
                # 获取实时价格（只有 3 只，很快）
                estimated_price = self._get_realtime_price(score.symbol)
                if estimated_price <= 0:
                    logger.warning(f"[Phase 2] 无法获取 {score.symbol} 价格，跳过")
                    continue

                # 根据板块暴露上限推断角色，调整预算（参考 Conviction-Tier: 埋伏=中确信→¼ Kelly）
                stock_limit = sector_limits.get(score.reasoning.split('[')[0].strip() if '[' in score.reasoning else "", 0.20)
                if stock_limit < role_threshold and stock_limit > 0:
                    per_stock_budget = base_budget * 0.55  # 埋伏仓缩小 45%
                    stop_loss_pct = 0.05  # 埋伏仓更紧止损（-5%）
                else:
                    per_stock_budget = base_budget
                    stop_loss_pct = 0.07  # 主线仓标准止损（-7%）

                quantity = int(per_stock_budget / estimated_price / 100) * 100

                if quantity > 0:
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
                        )
                    )

        # 预算不足时，全部 BUY 候选被约束否决 → 显式透出（消除"高分候选 vs 0 交易"静默矛盾）
        vetoed_candidates = buy_candidates if buy_budget <= 0 else []

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
