"""
Node 6: 聚合节点（v3 — 分层 GOT 架构）

设计理念：
- 全局环境感知（Level 0）：使用 5 个节点的全局聚合字段判断市场 regime
- 板块级情报整合（Level 1）：对每个候选板块做 4 维 mini GOT 交叉验证
- 组合决策（Level 2）：基于 regime + confirmed_sectors 计算仓位和信号

参考论文：
- ICLR 2025: Top-Down Sector Allocation（板块配置即决策）
- FINCON NeurIPS 2024: Manager-Analyst 层级（Analyst 输出 insight 而非 score）
- FinCoT ACL 2025: 推理结构匹配领域工作流
"""

import asyncio
import json
import logging
import re
from datetime import date, datetime
from typing import Any, Optional

from src.got.schemas import (
    UnifiedContext,
    MarketContext,
    SectorContext,
    CapitalContext,
    NewsContext,
    SentimentContext,
    Contradiction,
    NewsOverride,
    TemporalInconsistency,
    DataQuality,
    MarketRegime,
    SectorIntel,
    SectorDetail,
    AmbushCandidate,
    AmbushReservoirEntry,
    CatalystEvent,
    CrossNodeTemporalSignal,
)
from src.got.catalyst_config import (
    IMPACT_WEIGHT,
    ANTICIPATION_DECAY_NEAR,
    ANTICIPATION_DECAY_NORMAL,
    ANTICIPATION_THRESHOLD_DAYS,
    CATALYST_MAX_POOL_SIZE,
    CATALYST_CONFIDENCE_GATE,
    RESERVOIR_EXPIRY_DAYS,
    RESERVOIR_PROMOTION_MIN_DAYS,
    REFINE_TOP_N,
    REFINE_MAX_SECTORS,
    AMBUSH_NOMINATION_MAX,
    AMBUSH_NOMINATION_MIN_CONFIDENCE,
    AMBUSH_NOMINATION_MULTIPLIER,
    AMBUSH_NOMINATION_MIN_CONSECUTIVE,
    AMBUSH_NOMINATION_DISABLED,
)
from src.got.catalyst_calendar import _count_trade_days
from src.got.persistence import (
    load_previous_context,
    save_current_context,
    load_ema_baseline,
    save_ema_baseline,
    load_previous_regime_state,
    load_regime_state_history,
    EMA_ALPHA,
    EMA_WARMUP_SAMPLES,
)
from src.got.indicators import clip

logger = logging.getLogger(__name__)


# =============================================================================
# 常量
# =============================================================================

# 消息面覆盖：系统性政策关键词
SYSTEMIC_KEYWORDS = [
    "降息", "降准", "加息", "IPO", "注册制", "退市",
    "禁止", "禁令", "监管", "制裁", "关税", "财政刺激", "国债",
    "印花税", "暂停", "熔断", "救市", "护盘",
]

# 仓位日限幅
POSITION_LIMIT_DELTA = 0.2

# 泛化后缀：匹配时需剥离，防止 "概念" 等无意义后缀造成假阳性
GENERIC_SUFFIXES = ("概念",)


# =============================================================================
# 聚合节点
# =============================================================================

class AggregatorNode:
    """Node 6: 分层 GOT 聚合（全局环境 → 板块 mini GOT → 组合决策）"""

    # 资金流中的通道/杠杆聚合项（非真实板块），板块级错位比对一律排除，
    # 防止"融资融券/深股通"这类通道资金被误当成板块资金参与家族错位归并。
    _CAPITAL_CHANNEL_TERMS = frozenset({
        "融资融券", "深股通", "沪股通", "北向资金",
        "陆股通", "融资余额", "融券余额", "两融",
    })

    def __init__(self, use_mock: bool = True):
        self.use_mock = use_mock

    async def run(
        self,
        market: MarketContext,
        sector: SectorContext,
        capital: CapitalContext,
        news: NewsContext,
        sentiment: SentimentContext,
    ) -> UnifiedContext:
        """执行分层 GOT 聚合"""
        if self.use_mock:
            logger.info("[Node 6] 使用 mock 数据")
            return self._mock_output()

        logger.info("[Node 6] 开始 v3 分层 GOT 聚合")
        today = date.today()
        self._run_cache = {}  # 单次 run() 内归档回读缓存

        # ── Level 0: 全局环境感知 ──
        regime = self._compute_market_regime(market, sector, capital, news, sentiment)
        # 状态机：跨日记忆 + 转换确认 + dwell 追踪（DISABLE_REGIME_STATE_MACHINE=true 回滚）
        import os as _os
        if _os.environ.get("DISABLE_REGIME_STATE_MACHINE", "").lower() == "true":
            logger.info("[Node 6] DISABLE_REGIME_STATE_MACHINE=true，跳过状态机")
        else:
            regime = self._apply_regime_state_machine(regime)
        logger.info(
            f"[Node 6] Level 0 — regime={regime.regime}, "
            f"risk_budget={regime.risk_budget:.2f}, "
            f"fear_greed={regime.fear_greed:.2f}, "
            f"dwell_days={regime.dwell_days}"
        )

        # ── Level 1 平行分支: 埋伏通道（P3，先于 mini GOT 分类，供拆分消费）──
        ambush_pool, ambush_reservoir, ambush_veto_log = await self._build_ambush_channel(
            sector, capital, news, sentiment, regime=regime,
        )

        # ── Level 1: 板块 mini GOT（主线/降温走 TOP-N，埋伏按通道结果拆分消费）──
        sector_intels = self._build_sector_intel(
            sector, capital, news, sentiment,
            ambush_pool=ambush_pool, ambush_reservoir=ambush_reservoir,
        )
        # 将埋伏通道结果回写到 SectorIntel（供 reasoning.md 和 Phase 2 消费）
        self._enrich_ambush_sector_intel(sector_intels, ambush_pool, ambush_reservoir)

        veto_n = sum(
            1 for v in ambush_veto_log if not v.split(": ", 1)[-1].startswith("吸筹观察")
        )
        observe_n = len(ambush_veto_log) - veto_n
        nominated_n = sum(1 for c in ambush_pool if getattr(c, "nominated", False))
        logger.info(
            f"[Node 6] Level 1 — {len(sector_intels)} 个板块情报, "
            f"actionable={sum(1 for s in sector_intels if s.classification == 'actionable')}, "
            f"caution={sum(1 for s in sector_intels if s.classification == 'caution')}, "
            f"exclude={sum(1 for s in sector_intels if s.classification == 'exclude')}"
            f" | 埋伏通道: 正式池={len(ambush_pool)}(含提名{nominated_n}), "
            f"蓄水池={len(ambush_reservoir)}, 否决={veto_n} 观察={observe_n}"
        )

        # LLM 精炼板块情报（决策 20：视野扩围 + 注入催化事件，让 Agent 看见带催化的埋伏候选）
        refine_calendar = getattr(news, "catalyst_calendar", None)
        refine_events_map = self._group_events_by_sector(
            refine_calendar.events if refine_calendar else []
        )
        sector_intels = await self._llm_refine_sectors(
            sector_intels, regime, catalyst_events_by_sector=refine_events_map,
        )

        # ── Level 2: 组合决策 ──
        position_limit = self._compute_position_from_regime(regime, sector_intels, market.position_limit)
        unified_signal = self._determine_unified_signal(regime, sector_intels)

        # 移除日限幅：三层仓位系统（波动率调整 + 状态缩放 + 回撤保护）已提供平滑过渡
        # 旧版日限幅 bug：当 market.position_limit=0.0 时，clip 到 [−0.2, 0.2] 导致锁死 20%
        # 参考：Moreira & Muir (2017), AlgoKing "Dynamic Position Sizing with Kelly + Regime"
        final_position = position_limit

        # ── 辅助信息 ──
        contradictions = self._detect_contradictions(regime, sector_intels, sector, capital)
        temporal = self._adaptive_temporal_check(regime)
        override = await self._simplified_news_override(news, market)

        # ── 跨节点时间线共振（v2.1 Phase 6）──
        # Rollback 开关：DISABLE_CROSS_NODE_SIGNALS=true 跳过共振检测；
        # 检测异常降级为空信号，不阻塞 Node 6 主流程
        import os as _os
        if _os.environ.get("DISABLE_CROSS_NODE_SIGNALS", "").lower() == "true":
            logger.info("[Node 6] DISABLE_CROSS_NODE_SIGNALS=true，跳过跨节点共振")
            cross_node_signals = []
        else:
            try:
                cross_node_signals = self._detect_cross_node_temporal_signals(
                    regime, capital, sentiment, news,
                )
            except Exception as e:
                logger.warning(f"[Node 6] 跨节点共振检测失败，降级为无信号: {e}")
                cross_node_signals = []
        if cross_node_signals:
            logger.info(
                f"[Node 6] 跨节点共振: {len(cross_node_signals)} 个信号 — "
                f"{[s.signal_type for s in cross_node_signals]}"
            )

        # T3: 矛盾仲裁者（仅 medium/high 严重度，串行 LLM，影子模式）
        contradictions = await self._arbitrate_contradictions(
            contradictions, regime, sector, capital, news, sentiment, cross_node_signals,
        )

        key_risks = self._collect_key_risks(news, sentiment, temporal)
        key_opportunities = self._collect_key_opportunities(sector_intels)

        # ── 构建输出 ──
        unified = UnifiedContext(
            status="manual_review_required" if self._has_critical(temporal) else "normal",
            unified_signal=unified_signal,
            overall_confidence=self._compute_overall_confidence(regime, sector_intels),
            final_position_limit=clip(final_position, 0.0, 1.0),
            contradictions=contradictions,
            common_root_cause=None,
            news_override=override,
            temporal_inconsistencies=temporal,
            key_risks=key_risks,
            key_opportunities=key_opportunities,
            concept_suggestions=news.concept_suggestions,
            reasoning=self._build_reasoning(regime, sector_intels, contradictions),
            data_quality=self._build_data_quality(market, sector, capital, news, sentiment),
            market_regime=regime,
            llm_refinement_used=True,
            ema_baseline=None,
            confirmed_sectors=sector_intels,
            ambush_pool=ambush_pool,
            ambush_reservoir=ambush_reservoir,
            ambush_veto_log=ambush_veto_log,
            cross_node_signals=cross_node_signals,
        )

        # 持久化
        self._save_context(unified, regime, sector_intels, today)

        # 回写 EMA 基线
        updated_ema = load_ema_baseline()
        if updated_ema:
            unified.ema_baseline = updated_ema.get("regime_score")

        # ── 生产监控更新（Phase 6）──
        self._update_monitoring(regime, capital, sentiment, cross_node_signals)

        logger.info(
            f"[Node 6] 聚合完成 — regime: {regime.regime}, "
            f"信号: {unified.unified_signal}, "
            f"仓位: {unified.final_position_limit:.0%}, "
            f"actionable sectors: {len([s for s in sector_intels if s.classification == 'actionable'])}"
        )
        return unified

    # =========================================================================
    # Level 0: 全局环境感知
    # =========================================================================

    @staticmethod
    def _compute_market_regime(
        market: MarketContext,
        sector: SectorContext,
        capital: CapitalContext,
        news: NewsContext,
        sentiment: SentimentContext,
    ) -> MarketRegime:
        """全局市场环境判断（使用 5 个节点的全局聚合字段）"""

        # Node 1: 大盘
        adx_value = market.tech_growth.regime_metadata.adx_value
        cross_index_consistency = getattr(market.tech_growth, 'cross_index_consistency', 0.5)

        # Node 2: 板块
        market_direction = sector.market_direction
        mainline_count = len(sector.active_mainline)

        # Node 3: 资金面
        institutional_score = capital.institutional_score

        # Node 4: 消息面
        news_impact_score = news.impact_score

        # Node 5: 情绪面
        fear_greed = sentiment.fear_greed_index
        sentiment_stage = sentiment.sentiment_stage
        sentiment_momentum = sentiment.sentiment_momentum

        # Regime 判定（规则决策树）
        regime_score = 0.0

        # Node 1: ADX + 趋势
        if adx_value > 25:  # 有趋势
            if market.tech_growth.score > 0.3:
                regime_score += 0.3
            elif market.tech_growth.score < -0.3:
                regime_score -= 0.3
        else:  # 无趋势
            regime_score += market.tech_growth.score * 0.2

        # Node 2: 板块方向
        if market_direction == "普涨":
            regime_score += 0.3
        elif market_direction == "普跌":
            regime_score -= 0.3
        else:  # 分化
            regime_score += 0.0

        # Node 3: 资金面
        regime_score += institutional_score * 0.2

        # Node 4: 消息面
        # 权重 0.1 → 0.2：国内科技股与海外科技消息联动性极高，
        # 消息面不应是 regime 中最弱的一维（与 Node 3 资金面持平）
        regime_score += news_impact_score * 0.2

        # Node 5: 情绪面
        # fear_greed: 0=恐惧, 1=贪婪
        if fear_greed > 0.7:
            regime_score += 0.1  # 贪婪 → 偏多
        elif fear_greed < 0.3:
            regime_score -= 0.1  # 恐惧 → 偏空

        # sentiment_stage 修正
        if sentiment_stage == "euphoria":
            regime_score -= 0.15  # 过度乐观 → 反向修正
        elif sentiment_stage == "capitulation":
            regime_score += 0.15  # 过度悲观 → 反向修正
        elif sentiment_stage == "optimism":
            regime_score += 0.05
        elif sentiment_stage == "pessimism":
            regime_score -= 0.05

        # 情绪动量
        regime_score += sentiment_momentum * 0.1

        # 分类 regime
        regime_score = clip(regime_score, -1.0, 1.0)

        if regime_score > 0.3:
            regime = "bull"
        elif regime_score < -0.3:
            regime = "bear"
        else:
            regime = "transition"

        # 风险预算
        if regime == "bull":
            risk_budget = 0.6 + 0.2 * (regime_score - 0.3) / 0.7
        elif regime == "bear":
            risk_budget = 0.3 + 0.2 * (regime_score + 1.0) / 0.7
        else:  # transition
            risk_budget = 0.4 + 0.2 * (regime_score + 0.3) / 0.6

        risk_budget = clip(risk_budget, 0.2, 0.8)

        # 宏观判断文本
        # 机构态度维度口径标注（盘中单维模式：数值口径不变，仅文本显式标注证据维度）
        cal_note = ""
        if capital.confidence_multiplier <= 0.5:
            cal_note = "（单维资金口径）"
        macro_overlay = (
            f"regime_score={regime_score:.2f}, ADX={adx_value:.0f}, "
            f"板块{market_direction}, 机构态度{institutional_score:+.2f}{cal_note}, "
            f"恐贪={fear_greed:.2f}({sentiment_stage})"
        )

        return MarketRegime(
            regime=regime,
            risk_budget=round(risk_budget, 3),
            adx_value=adx_value,
            cross_index_consistency=cross_index_consistency,
            market_direction=market_direction,
            mainline_count=mainline_count,
            institutional_score=institutional_score,
            news_impact_score=news_impact_score,
            fear_greed=fear_greed,
            sentiment_stage=sentiment_stage,
            sentiment_momentum=sentiment_momentum,
            macro_overlay=macro_overlay,
        )

    @staticmethod
    def _apply_regime_state_machine(regime: MarketRegime) -> MarketRegime:
        """Regime 状态机：跨日记忆 + 转换确认 + dwell 追踪。

        状态转换规则（v2.1）：
        - raw == previous → dwell_days += 1
        - raw != previous 且 raw == transition_pending → 确认切换，dwell_days = 1
        - raw != previous 且 raw != transition_pending → 保持 previous，
          transition_pending = raw（首次出现，待次日确认）
        - 冷启动（无归档/缺字段）→ current = raw，dwell_days = 1，不阻塞

        transition_count：近 20 交易日确认切换次数（从归档序列统计）
        """
        # 加载前日 regime 状态
        prev_state = load_previous_regime_state()

        # 加载 20 日 regime 历史（用于 transition_count）
        regime_history = load_regime_state_history(days=20)

        raw_regime = regime.regime

        if prev_state is None:
            # 冷启动：无归档或归档中无 regime 字段
            logger.info("[Node 6] Regime 状态机冷启动，无历史归档")
            return regime.model_copy(update={
                "dwell_days": 1,
                "previous_regime": None,
                "transition_count": 0,
                "transition_pending": None,
            })

        prev_regime = prev_state["regime"]
        prev_dwell = prev_state["dwell_days"]
        prev_pending = prev_state["transition_pending"]

        # 状态转换判定
        if raw_regime == prev_regime:
            # 情形 1：与昨日相同 → dwell+1，清除 pending
            new_regime = prev_regime
            new_dwell = prev_dwell + 1
            new_pending = None
        elif raw_regime == prev_pending:
            # 情形 2：与 pending 相同 → 确认切换
            new_regime = prev_pending
            new_dwell = 1
            new_pending = None
            logger.info(
                f"[Node 6] Regime 切换确认: {prev_regime} → {new_regime}"
            )
        else:
            # 情形 3：新信号 ≠ pending → 保持昨日，设新 pending
            new_regime = prev_regime
            new_dwell = prev_dwell + 1
            new_pending = raw_regime
            logger.info(
                f"[Node 6] Regime 首次出现: {raw_regime}，待次日确认"
            )

        # transition_count：统计历史序列中 regime 切换次数
        transition_count = 0
        if len(regime_history) >= 2:
            for i in range(1, len(regime_history)):
                if regime_history[i][1] != regime_history[i-1][1]:
                    transition_count += 1

        return regime.model_copy(update={
            "regime": new_regime,
            "dwell_days": new_dwell,
            "previous_regime": prev_regime,
            "transition_count": transition_count,
            "transition_pending": new_pending,
        })

    # =========================================================================
    # Level 1: 板块 mini GOT
    # =========================================================================

    def _build_sector_intel(
        self,
        sector: SectorContext,
        capital: CapitalContext,
        news: NewsContext,
        sentiment: SentimentContext,
        ambush_pool: Optional[list[AmbushCandidate]] = None,
        ambush_reservoir: Optional[list[AmbushReservoirEntry]] = None,
    ) -> list[SectorIntel]:
        """对每个候选板块做 4 维 mini GOT，标记主线/埋伏来源

        分类拆分消费：主线/降温板块走 composite TOP-N（Node 2 尺子），
        埋伏板块的 classification 由埋伏通道准入结果直接映射
        （入正式池 → actionable，否则 caution），不再与主线争抢 TOP-N 名额。
        """

        # 收集候选板块：(name, detail, role_info)
        candidates = []
        seen_names = set()

        # 主线板块（用组内最强成员代表）
        for group in sector.active_mainline:
            best_member = max(group.members, key=lambda m: m.strength_score) if group.members else None
            role_info = {
                "role": "mainline",
                "tier": group.tier,
                "quality": group.mainline_quality,
                "mainline_days": best_member.mainline_days if best_member else None,
            }
            candidates.append((group.group_name, best_member, role_info))
            seen_names.add(group.group_name)

        # 埋伏板块
        for detail in sector.ambush:
            if detail.name not in seen_names:
                role_info = {
                    "role": "ambush",
                    "tier": None,
                    "quality": None,
                    "mainline_days": None,
                }
                candidates.append((detail.name, detail, role_info))
                seen_names.add(detail.name)

        # 高位降温板块（前主线回调未破位，2026-08-17 状态机新增）：
        # 纳入候选做 mini GOT，但独立角色，走 hold/reduce（防被误当埋伏选股）
        for detail in sector.cooling_down:
            if detail.name not in seen_names:
                role_info = {
                    "role": "cooling",
                    "tier": detail.tier,
                    "quality": "weak",
                    "mainline_days": detail.mainline_days,
                }
                candidates.append((detail.name, detail, role_info))
                seen_names.add(detail.name)

        # 埋伏正式池中不在 Node 2 名单的板块（蓄水池升格 / 近失来源）：
        # 升格成功时板块可能已移出 sector.ambush，但已获得入池资格，
        # 必须补入候选才能拿到 SectorIntel 和 actionable 分类（否则 Phase 2 选股不可见）
        for candidate in (ambush_pool or []):
            if candidate.sector_name not in seen_names:
                detail = self._resolve_pool_sector_detail(sector, candidate)
                role_info = {
                    "role": "ambush",
                    "tier": None,
                    "quality": None,
                    "mainline_days": None,
                }
                candidates.append((candidate.sector_name, detail, role_info))
                seen_names.add(candidate.sector_name)
                logger.info(
                    f"[Node 6] 池外板块补入 mini GOT 候选: {candidate.sector_name} "
                    f"({'命中 Node 2 detail' if detail else '无 Node 2 detail，技术分取 strength_lookup'})"
                )

        if not candidates:
            return []

        # 预提取各维度数据
        capital_flows = self._parse_capital_flows(capital)
        capital_flow_map = self._parse_sector_level_flows(capital)  # A4: 全量 464 板块
        news_keywords = self._extract_news_keywords(news)
        hot_sectors = self._extract_hot_sectors(sentiment)

        # ── Pass 1: 计算所有板块的 composite score 和验证数据 ──
        # 不创建 SectorIntel，先收集原始数据用于批量分类
        raw_data = []  # [(name, composite, capital_check, news_check, detail, sector_role, ...)]
        for name, detail, role_info in candidates:
            sector_role = role_info.get("role", "mainline")
            composite, capital_check, news_check, other = self._compute_sector_scores(
                name, detail, capital_flows, news_keywords, hot_sectors, role_info,
                capital_flow_map=capital_flow_map,
            )
            raw_data.append((name, composite, capital_check, news_check, detail, sector_role, role_info, other))

        # ── Pass 2: 批量分类（主线/降温走 TOP-N，埋伏按通道结果拆分）──
        # 埋伏板块不走 composite TOP-N：其准入由埋伏通道的闸门+B公式排序决定，
        # composite（Node 2 尺子）对低位埋伏板块天然偏见，不能当埋伏的尺子
        classify_input = [
            (name, composite, capital_check, news_check, detail, sector_role)
            for name, composite, capital_check, news_check, detail, sector_role, _, _ in raw_data
            if sector_role != "ambush"
        ]
        classification_map = self._classify_all_sectors(classify_input, top_n=12)

        ambush_pool_names = {c.sector_name for c in (ambush_pool or [])}
        for name, composite, capital_check, news_check, detail, sector_role, _, _ in raw_data:
            if sector_role == "ambush":
                classification_map[name] = (
                    "actionable" if name in ambush_pool_names else "caution"
                )

        # ── Pass 3: 创建 SectorIntel（使用批量分类结果）──
        results = []
        for name, composite, capital_check, news_check, detail, sector_role, role_info, other in raw_data:
            classification = classification_map.get(name, "caution")
            intel = self._build_sector_intel_from_scores(
                name, detail, sector_role, role_info,
                composite, capital_check, news_check, classification, other,
            )
            results.append(intel)

        # 按 composite_score 降序排列
        results.sort(key=lambda x: x.composite_score, reverse=True)

        return results

    @staticmethod
    def _resolve_pool_sector_detail(
        sector: SectorContext,
        candidate: AmbushCandidate,
    ) -> Optional[SectorDetail]:
        """为埋伏池板块查找 Node 2 detail（升格后可能已不在 ambush 列表）。

        查找顺序：near_miss → ambush → cooling_down → top_by_score → exhausted；
        均未命中时用 strength_lookup 合成最小 detail（缺数据时返回 None，
        由 _compute_sector_scores 走默认技术分降级）。
        """
        for detail_list in (
            sector.near_miss_ambush,
            sector.ambush,
            sector.cooling_down,
            sector.top_by_score,
            sector.exhausted_mainline,
        ):
            for d in detail_list or []:
                if d.name == candidate.sector_name:
                    return d

        strength = sector.strength_lookup.get(candidate.sector_name)
        if strength is None:
            return None
        return SectorDetail(
            code=candidate.sector_code,
            name=candidate.sector_name,
            state="ambush",
            strength_score=strength,
        )

    def _compute_sector_scores(
        self,
        name: str,
        detail: Optional[SectorDetail],
        capital_flows: list[tuple[str, float]],
        news_keywords: list[tuple[str, dict]],
        hot_sectors: list[str],
        role_info: Optional[dict] = None,
        capital_flow_map: Optional[dict[str, float]] = None,
    ) -> tuple[float, str, str, dict]:
        """计算板块的 4 维评分和综合分（不含分类）

        Returns:
            (composite_score, capital_check, news_check, other_data_dict)
            other_data_dict 包含创建 SectorIntel 所需的所有中间数据
        """
        role_info = role_info or {"role": "mainline", "tier": None, "quality": None, "mainline_days": None}
        sector_role = role_info.get("role", "mainline")

        # ── 维度 1: 技术面（来自 Node 2）──
        if detail:
            tech_score = detail.strength_score
            lifecycle = detail.lifecycle
            trajectory = detail.strength_trajectory
            tier = detail.tier

            # 构建技术面详情
            tech_parts = []
            if lifecycle:
                tech_parts.append(f"lifecycle={lifecycle.phase}")
                if lifecycle.days_since_launch:
                    tech_parts.append(f"day{lifecycle.days_since_launch}")
            if tier:
                tech_parts.append(f"tier={tier}")
            if detail.mainline_quality:
                tech_parts.append(f"quality={detail.mainline_quality}")

            # trajectory 趋势
            if trajectory and len(trajectory) >= 2:
                recent = trajectory[-min(3, len(trajectory)):]
                slope = (recent[-1] - recent[0]) / max(len(recent) - 1, 1)
                if slope > 5:
                    tech_parts.append("trajectory↑")
                elif slope < -5:
                    tech_parts.append("trajectory↓")

            tech_detail = ", ".join(tech_parts) if tech_parts else "无详细信息"
        else:
            tech_score = 50.0  # 默认中等
            tech_detail = "无 Node 2 数据"

        # ── 维度 2: 资金面（A4: 优先全量查找，Top-8 兜底）──
        capital_check, capital_detail = self._cross_check_capital(
            name, capital_flows, capital_flow_map=capital_flow_map,
        )

        # ── 维度 3: 消息面（分级修正：影响等级 × 多源确认 × 海外联动）──
        news_check, news_detail, news_adjust = self._cross_check_news(name, news_keywords)

        # ── 维度 4: 情绪面 ──
        sentiment_check, sentiment_detail = self._cross_check_sentiment(name, hot_sectors)

        # ── 综合评分 ──
        # 基础分 = 技术面（Node 2 的 strength_score）
        composite = tech_score

        # 资金面修正（从门槛变为放大器）
        if capital_check == "aligned":
            composite += 10
        elif capital_check == "divergent":
            composite -= 15

        # 消息面修正（分级：高影响事件修正幅度最高可达 ±20/−24，不再是扁平 ±8/−12）
        composite += news_adjust

        # 情绪面修正（P3: 埋伏板块不惩罚低热度——低关注度是埋伏的利润来源）
        if sentiment_check == "high":
            composite += 5
        elif sentiment_check == "low" and sector_role != "ambush":
            composite -= 5

        # Lifecycle 修正
        if detail and detail.lifecycle:
            phase = detail.lifecycle.phase
            if phase in ("launch", "acceleration"):
                composite += 8  # 早期阶段加分
            elif phase in ("late_stage", "mature_exhausted"):
                composite -= 15  # 晚期阶段减分

        composite = clip(composite, 0, 100)

        # 置信度（基于数据完整度）
        confidence = 0.5
        if detail:
            confidence += 0.2  # 有 Node 2 数据
        if capital_check != "neutral":
            confidence += 0.1  # 资金面有信号
        if news_check != "neutral":
            confidence += 0.1  # 消息面有信号
        if sentiment_check != "low":
            confidence += 0.1  # 情绪面有信号
        confidence = clip(confidence, 0.0, 1.0)

        other = {
            "tech_score": tech_score,
            "tech_detail": tech_detail,
            "capital_detail": capital_detail,
            "news_detail": news_detail,
            "sentiment_check": sentiment_check,
            "sentiment_detail": sentiment_detail,
            "confidence": confidence,
            "sector_role": sector_role,
        }

        return composite, capital_check, news_check, other

    def _build_sector_intel_from_scores(
        self,
        name: str,
        detail: Optional[SectorDetail],
        sector_role: str,
        role_info: dict,
        composite: float,
        capital_check: str,
        news_check: str,
        classification: str,
        other: dict,
    ) -> SectorIntel:
        """从预计算的评分创建 SectorIntel（分类已由批量分类器决定）"""
        reasoning = f"{name}: {classification}, score={composite:.0f}, {other['tech_detail']}"

        return SectorIntel(
            sector_name=name,
            sector_code=detail.code if detail else "",
            sector_type="concept",
            sector_role=sector_role,
            mainline_tier=role_info.get("tier"),
            mainline_quality=role_info.get("quality"),
            mainline_days=role_info.get("mainline_days"),
            tech_score=other["tech_score"],
            tech_detail=other["tech_detail"],
            capital_check=capital_check,
            capital_detail=other["capital_detail"],
            news_check=news_check,
            news_detail=other["news_detail"],
            sentiment_check=other["sentiment_check"],
            sentiment_detail=other["sentiment_detail"],
            lifecycle=detail.lifecycle if detail and detail.lifecycle else None,
            composite_score=round(composite, 1),
            confidence=round(other["confidence"], 3),
            classification=classification,
            reasoning=reasoning,
        )

    def _cross_check_capital(
        self,
        name: str,
        flows: list[tuple[str, float]],
        capital_flow_map: Optional[dict[str, float]] = None,
    ) -> tuple[str, str]:
        """资金面交叉验证: (check, detail)

        A4: 优先从全量 sector_flow_all (464 板块) 精确查找，
        Top-8 榜单作模糊兜底。
        """
        # A4: 全量查找（优先）
        if capital_flow_map:
            amount = capital_flow_map.get(name)
            if amount is None:
                # 归一化 + 别名
                from src.got.board_aliases import sector_name_variants
                for variant in sector_name_variants(name):
                    amount = capital_flow_map.get(variant)
                    if amount is not None:
                        break
            if amount is not None:
                if amount > 10:
                    return "aligned", f"{name}净流入{amount:.1f}亿(全量)"
                elif amount > 0:
                    return "aligned", f"{name}净流入{amount:.1f}亿(小额/全量)"
                else:
                    return "neutral", f"{name}净流出{abs(amount):.1f}亿(全量)"

        # Top-8 兜底（A5: 统一匹配入口）
        if not flows:
            return "neutral", "资金面数据缺失"

        for flow_name, amount in flows:
            hit, match_type = self._match_sector(name, flow_name)
            if hit:
                # A6: 泛化词匹配只给 neutral（不确认方向）
                if match_type == "generic":
                    return "neutral", f"泛化关联[{flow_name}]流入{amount:.1f}亿"
                if amount > 10:
                    return "aligned", f"{flow_name}净流入{amount:.1f}亿"
                else:
                    return "aligned", f"{flow_name}净流入{amount:.1f}亿（小额）"

        return "neutral", f"{name}不在资金流入 top"

    # 消息面分级修正参数（v2：消息面权重加大）
    # 基础修正按影响等级分档，多源确认与海外联动再放大，正负各自封顶
    _NEWS_POS_ADJUST = {"high": 12.0, "medium": 8.0, "low": 4.0}
    _NEWS_NEG_ADJUST = {"high": -18.0, "medium": -12.0, "low": -6.0}
    _NEWS_SOURCE_MULTIPLIER = 1.3   # source_count >= 3：多源确认放大
    _NEWS_GLOBAL_MULTIPLIER = 1.3   # event_type == "global"：海外科技消息→A股联动放大
    _NEWS_FADING_MULTIPLIER = 0.5   # fading/archived：行情已 price-in，减半
    _NEWS_POS_CAP = 20.0            # 正面修正封顶
    _NEWS_NEG_CAP = -24.0           # 负面修正封顶

    def _cross_check_news(
        self,
        name: str,
        keywords: list[tuple[str, dict]],
    ) -> tuple[str, str, float]:
        """消息面交叉验证（分级修正版）: (check, detail, adjustment)

        修正幅度 = Σ(影响等级基础分 × 多源系数 × 海外联动系数 × 生命周期衰减)，
        正面封顶 +20、负面封顶 −24。
        设计依据：国内科技股高度跟随海外科技消息，global 事件额外放大 1.3×。
        """
        if not keywords:
            return "neutral", "无消息催化", 0.0

        # A5: 统一匹配入口替代 _split_sector_name 子词匹配。
        # 注：不得以"关键词出现在标题中"作兜底——_extract_news_keywords 会把事件标题本身
        # 作为关键词加入（kw == meta["title"]），该条件恒真，会导致所有板块匹配全部新闻。
        # "标题含板块名"的合法场景已由 _match_sector 的 L3 受控子串覆盖。
        matched: list[tuple[dict, str]] = []  # (meta, match_type)

        for kw, meta in keywords:
            hit, match_type = self._match_sector(name, kw)
            if hit:
                matched.append((meta, match_type))

        if not matched:
            return "neutral", f"{name}无直接消息催化", 0.0

        # 去重（按标题）
        seen = set()
        unique: list[tuple[dict, str]] = []
        for meta, mt in matched:
            if meta["title"] not in seen:
                seen.add(meta["title"])
                unique.append((meta, mt))

        pos_adjust = 0.0
        neg_adjust = 0.0
        pos_count = 0
        neg_count = 0
        global_hit = False

        for meta, match_type in unique:
            sentiment = meta.get("sentiment", "neutral")
            if sentiment not in ("positive", "negative"):
                continue
            base = (
                self._NEWS_POS_ADJUST.get(meta.get("impact_level", "low"), 4.0)
                if sentiment == "positive"
                else self._NEWS_NEG_ADJUST.get(meta.get("impact_level", "low"), -6.0)
            )
            magnitude = abs(base)
            if meta.get("source_count", 1) >= 3:
                magnitude *= self._NEWS_SOURCE_MULTIPLIER
            if meta.get("event_type") == "global":
                magnitude *= self._NEWS_GLOBAL_MULTIPLIER
                global_hit = True
            if meta.get("lifecycle_state") in ("fading", "archived"):
                magnitude *= self._NEWS_FADING_MULTIPLIER
            # A6: 泛化词根匹配降权（防止 "AI" 等短词假阳性加分）
            if match_type == "generic":
                magnitude *= 0.5
            if sentiment == "positive":
                pos_adjust += magnitude
                pos_count += 1
            else:
                neg_adjust -= magnitude
                neg_count += 1

        pos_adjust = min(pos_adjust, self._NEWS_POS_CAP)
        neg_adjust = max(neg_adjust, self._NEWS_NEG_CAP)
        adjustment = pos_adjust + neg_adjust

        top_title = unique[0][0]["title"][:30]
        if pos_count > neg_count and adjustment >= 6:
            global_note = "（含海外联动）" if global_hit else ""
            check = "catalyzed"
            detail = f"{pos_count}条正面催化{global_note} +{adjustment:.0f}: {top_title}"
        elif neg_count > pos_count and adjustment <= -6:
            global_note = "（含海外联动）" if global_hit else ""
            check = "negative"
            detail = f"{neg_count}条负面消息{global_note} {adjustment:.0f}: {top_title}"
        else:
            check = "neutral"
            detail = f"消息中性: {top_title}"

        return check, detail, round(adjustment, 1)

    def _cross_check_sentiment(
        self,
        name: str,
        hot_sectors: list[str],
    ) -> tuple[str, str]:
        """情绪面交叉验证: (check, detail) — A5 统一匹配"""
        if not hot_sectors:
            return "low", "无热榜数据"

        # A5: 统一匹配入口替代 _split_sector_name 子词匹配
        match_count = 0
        generic_count = 0

        for hot_sector in hot_sectors:
            hit, match_type = self._match_sector(name, hot_sector)
            if hit:
                match_count += 1
                if match_type == "generic":
                    generic_count += 1

        # A6: 泛化词根匹配不计入有效匹配数
        effective_count = match_count - generic_count

        if effective_count >= 3:
            return "high", f"{effective_count}只关联股票上热榜"
        elif effective_count >= 1:
            return "medium", f"{effective_count}只关联股票上热榜"
        else:
            return "low", f"{name}未上热榜"

    def _classify_all_sectors(
        self,
        candidates: list[tuple[str, float, str, str, Optional[SectorDetail], str]],
        top_n: int = 12,
    ) -> dict[str, str]:
        """批量板块分类（TOP-N + conviction 分级）

        参考论文：
        - Bergemann, Hege & Peng (2008): 不确定性下序贯投资，分阶段递进
        - Black-Litterman: 不同置信度 → 不同仓位权重（非二元准入）
        - Ben-David et al. (NBER): 资金流是领先指标，非确认指标

        设计：
        - 排名决定能不能进（TOP-N 截断，控制候选池规模）
        - 角色决定仓位大小（主线/埋伏 → conviction multiplier）
        - 资金面从门槛变成放大器（有资金→加分，无资金→不减分）

        Args:
            candidates: (name, composite, capital_check, news_check, detail, sector_role)
            top_n: actionable 板块上限

        Returns:
            {sector_name: classification}，值为 actionable / caution / exclude
        """
        if not candidates:
            return {}

        result = {}

        # ── Pass 1: 标记硬拒绝（不可被 TOP-N 覆盖）──
        for name, composite, capital_check, news_check, detail, sector_role in candidates:
            if self._is_hard_exclude(composite, capital_check, news_check, detail):
                result[name] = "exclude"

        # ── Pass 2: 按综合分排名，TOP-N → actionable，其余 → caution ──
        eligible = sorted(
            [(name, score) for name, score, *_ in candidates if name not in result],
            key=lambda x: x[1],
            reverse=True,
        )

        actionable_count = 0
        for name, score in eligible:
            if actionable_count < top_n and score >= 50:
                result[name] = "actionable"
                actionable_count += 1
            else:
                result[name] = "caution"

        return result

    @staticmethod
    def _is_hard_exclude(
        composite: float,
        capital_check: str,
        news_check: str,
        detail: Optional[SectorDetail],
    ) -> bool:
        """硬拒绝判定（不可被 TOP-N 覆盖）"""
        if composite < 40:
            return True
        if detail and detail.lifecycle:
            phase = detail.lifecycle.phase
            if phase in ("late_stage", "mature_exhausted"):
                if capital_check == "divergent" or news_check == "negative":
                    return True
        return False

    async def _llm_refine_sectors(
        self,
        sectors: list[SectorIntel],
        regime: MarketRegime,
        catalyst_events_by_sector: Optional[dict] = None,
    ) -> list[SectorIntel]:
        """LLM 精炼板块情报（可选，生成 narrative + 跨板块风险）

        决策 20：视野扩围 — 基础片为 composite 降序前 REFINE_TOP_N，
        追加全部埋伏角色候选（总量 ≤ REFINE_MAX_SECTORS），并注入合格催化事件，
        避免低位带催化的埋伏候选（如 0831 短剧游戏）完全不在 Agent 视野内。
        """
        if not sectors:
            return sectors

        # ── 切片：基础片（前 REFINE_TOP_N）+ 埋伏候选追加片 ──
        base = sectors[:REFINE_TOP_N]
        extra = [
            s for s in sectors[REFINE_TOP_N:]
            if getattr(s, "sector_role", "mainline") == "ambush"
        ][: max(0, REFINE_MAX_SECTORS - REFINE_TOP_N)]
        view = base + extra

        # 构建板块情报文本
        sector_lines = []
        for i, s in enumerate(view, 1):
            lines = [
                f"{i}. {s.sector_name} | {s.classification} | score={s.composite_score:.0f}\n"
                f"   {s.tech_detail}\n"
                f"   资金: {s.capital_check}（{s.capital_detail}）\n"
                f"   催化: {s.news_check}（{s.news_detail}）\n"
                f"   热度: {s.sentiment_check}（{s.sentiment_detail}）"
            ]
            # 埋伏候选注入合格催化事件（决策 20）：让 Agent 看见消息面证据
            if getattr(s, "sector_role", "mainline") == "ambush" and catalyst_events_by_sector:
                evs = [
                    e for e in self._lookup_sector_events(s.sector_name, catalyst_events_by_sector)
                    if e.confidence >= CATALYST_CONFIDENCE_GATE and e.direction != "negative"
                ]
                if evs:
                    ev_desc = "; ".join(
                        f"[{e.event_date} {'★' * e.impact_level}] {e.title[:40]}"
                        for e in evs[:2]
                    )
                    lines.append(f"   日历催化剂: {ev_desc}")
            sector_lines.append("\n".join(lines))

        sectors_text = "\n".join(sector_lines)

        prompt = f"""全局环境：regime={regime.regime}, risk_budget={regime.risk_budget:.2f}
恐贪指数：{regime.fear_greed:.2f}（{regime.sentiment_stage}）

板块情报：
{sectors_text}

判断规则补充：埋伏角色（ambush）板块若带有日历催化剂但当前为 caution，
可结合催化强度与资金面证据建议上调为 actionable；无催化剂支撑不要上调。

请输出 JSON：
{{
  "narrative": "一段话总结板块格局（50字内）",
  "cross_sector_risks": ["跨板块风险1", "跨板块风险2"],
  "adjustments": [
    {{"sector": "板块名", "new_classification": "actionable/caution/exclude", "reason": "调整理由"}}
  ]
}}"""

        raw_text = await self._call_llm_async(prompt, temperature=0.3, max_tokens=500)

        # 分类层级（数值越大越"好"），用于判断上调/下调
        CLASS_RANK = {
            "exclude": 0, "caution": 1, "actionable": 2,
        }

        try:
            result = self._parse_json(raw_text)

            # 应用调整（仅允许上调，禁止下调）
            adjustments = result.get("adjustments", [])
            for adj in adjustments:
                sector_name = adj.get("sector", "")
                new_class = adj.get("new_classification", "")
                reason = adj.get("reason", "")

                if new_class not in CLASS_RANK:
                    continue

                for s in sectors:
                    if s.sector_name != sector_name:
                        continue
                    old_rank = CLASS_RANK.get(s.classification, 2)
                    new_rank = CLASS_RANK.get(new_class, 2)
                    if new_rank <= old_rank:
                        # 禁止下调：confirmed → watch 等
                        logger.warning(
                            f"[Node 6] 跳过降级: {sector_name} {s.classification}→{new_class}（{reason}）"
                        )
                    else:
                        s.classification = new_class
                        s.reasoning += f" [LLM调整: {reason}]"
                    break

            # 添加 narrative 到第一个板块的 reasoning（作为全局摘要）
            narrative = result.get("narrative", "")
            if narrative and sectors:
                sectors[0].reasoning = f"[全局] {narrative} | {sectors[0].reasoning}"

            return sectors
        except Exception as e:
            logger.warning(f"[Node 6] LLM 精炼板块情报失败: {e}")
            return sectors

    # =========================================================================
    # Level 1 平行分支: 埋伏通道（P3）
    # =========================================================================

    # ── P1-2: 闸门抽取（供正式池 + 升格分支复用）──

    def _run_ambush_gates(
        self,
        name: str,
        sector_events: list,
        capital: "CapitalContext",
        hot_sectors: list[str],
        today: date,
    ) -> tuple[bool, str, str, str, dict]:
        """4 层闸门流水线（★★★利空 + 防买顶 + 拥挤度 + 吸筹确认）。

        闸门序（0831 短剧案例修正）：利空 → 防买顶 → 拥挤 → 吸筹。
        吸筹放最后：它不是市场否决而是"时机未到"，且需要把证据透出给
        Agent 提名通道（决策 21）；吸筹失败不再静默，以「吸筹观察」留痕。
        正常入池路径的语义与结果不变（四闸全过才入池）。

        Returns:
            (passed, fail_reason, accum_detail, accum_strength, evidence)
            - passed=True: 闸门全通过，accum_detail 为吸筹确认细节
            - passed=False: fail_reason 为否决/观察原因（空串=静默否决）
            - accum_strength: strong / medium / weak / legacy / missing
              （missing = 资金流数据缺失，非市场信号，调用方应转观察而非否决）
            - evidence: 吸筹证据 {today_flow, flow_3d_avg, consecutive_positive}，
              供提名前置条件 G3 使用；吸筹未评估时为空 dict 兜底
        """
        empty_evidence = {"today_flow": None, "flow_3d_avg": None, "consecutive_positive": 0}

        # 闸门 1: ★★★ 利空否决（否决权不可被 Agent 覆盖）
        negative_block = self._check_negative_catalyst(sector_events)
        if negative_block:
            return False, f"★★★利空否决 — {negative_block}", "", "legacy", empty_evidence

        # 闸门 2: 防买顶双闸（真否决，提名不可覆盖）
        anti_top_ok, anti_top_reason = self._check_anti_top_buy(
            name, hot_sectors, sector_events, today,
        )
        if not anti_top_ok:
            return False, f"防买顶否决 — {anti_top_reason}", "", "legacy", empty_evidence

        # 闸门 3: 拥挤度黑名单（真否决，提名不可覆盖）
        if self._check_crowded_blacklist(name, hot_sectors):
            return False, "拥挤度黑名单（多只个股上热榜，来晚了）", "", "legacy", empty_evidence

        # 闸门 4: 吸筹确认（唯一可被 Agent 提名救回的闸门）
        accumulation_ok, accum_detail, accum_strength, evidence = self._check_accumulation_gate(
            name, capital,
        )
        if not accumulation_ok:
            if accum_strength == "missing":
                # 数据缺失保持静默（由调用方转蓄水池观察，语义不变）
                return False, "", "", accum_strength, evidence
            # S1 留痕：吸筹失败 = "时机未到的观察"而非市场否决，不再静默丢弃；
            # 提名成功时调用方会移除此观察条目，未获提名则落盘为否决日志可见
            return False, f"吸筹观察 — {accum_detail}", accum_detail, accum_strength, evidence

        return True, "", accum_detail, accum_strength, evidence

    # ── P1-2: 蓄水池跨日合并 + 升格 + 除名 ──

    @staticmethod
    def _load_previous_reservoir() -> dict[str, dict]:
        """加载前日蓄水池（从 data/got_daily/ 最近归档的 unified.json）。

        Returns:
            {sector_name: entry_dict}，失败返回空 dict。
        """
        import json
        from pathlib import Path

        archive_dir = Path("data/got_daily")
        if not archive_dir.exists():
            return {}

        # 找最近一个有 unified.json 的日期目录
        date_dirs = sorted(
            [d for d in archive_dir.iterdir() if d.is_dir()],
            reverse=True,
        )
        for d in date_dirs:
            unified_path = d / "unified.json"
            if unified_path.exists():
                try:
                    with open(unified_path, encoding="utf-8") as f:
                        data = json.load(f)
                    prev_reservoir = data.get("ambush_reservoir", [])
                    return {
                        e["sector_name"]: e
                        for e in prev_reservoir
                        if e.get("sector_name")
                    }
                except Exception as e:
                    logger.warning(f"[Node 6] 加载前日蓄水池失败: {e}")
                    return {}
        return {}

    @staticmethod
    def _merge_reservoir(
        today_reservoir: list[AmbushReservoirEntry],
        prev_reservoir_map: dict[str, dict],
        today: date,
        today_near_miss_names: Optional[set[str]] = None,
    ) -> list[AmbushReservoirEntry]:
        """蓄水池跨日合并：今日新入 + 前日留存 → 合并列表。

        - 今日在 + 前日在：天数 = 前日天数 + 1，保留入池日
        - 今日在 + 前日不在：天数 = 0（新入池）
        - 今日不在 + 前日在：
          - 若板块已不在今日近失名单且驻留 ≥3 天 → 除名（"不再是近失"=最强退出信号）
          - 否则天数 +1，若 ≥ RESERVOIR_EXPIRY_DAYS → 除名
        - 末尾容量上限：超出 RESERVOIR_MAX_SIZE 时按 days_in_reservoir 降序驱逐
        """
        from src.got.catalyst_config import RESERVOIR_MAX_SIZE

        merged: dict[str, AmbushReservoirEntry] = {}

        # 今日新入 + 前日续存
        for entry in today_reservoir:
            name = entry.sector_name
            if name in prev_reservoir_map:
                prev = prev_reservoir_map[name]
                entry.days_in_reservoir = prev.get("days_in_reservoir", 0) + 1
                # 保留首次入池日期和入池强度
                if prev.get("entered_date"):
                    entry.entered_date = date.fromisoformat(prev["entered_date"]) if isinstance(prev["entered_date"], str) else prev["entered_date"]
                if prev.get("entered_strength"):
                    entry.entered_strength = prev["entered_strength"]
            merged[name] = entry

        # 前日有但今日无：再评估
        for name, prev in prev_reservoir_map.items():
            if name in merged:
                continue
            days = prev.get("days_in_reservoir", 0) + 1

            # 再评估：今日不在近失名单 + 驻留≥3天 → 除名
            # "不再是近失"本身是最强的退出信号，不需要等到过期；
            # data_missing 条目豁免：它们是数据盲区而非"不再是近失"，退出由过期上限控制，
            # 待别名表补上或数据源覆盖后自会经升格重走闸门救活
            if (
                prev.get("reason") != "data_missing"
                and today_near_miss_names is not None
                and name not in today_near_miss_names
                and days >= 3
            ):
                logger.info(f"[Node 6] 蓄水池再评估除名: {name} (驻留{days}天, 已不在近失名单)")
                continue

            if days >= RESERVOIR_EXPIRY_DAYS:
                logger.info(f"[Node 6] 蓄水池除名: {name} (驻留{days}天 ≥ {RESERVOIR_EXPIRY_DAYS})")
                continue
            merged[name] = AmbushReservoirEntry(
                sector_name=name,
                sector_code=prev.get("sector_code", ""),
                reason=prev.get("reason", "carried_over"),
                entered_date=date.fromisoformat(prev["entered_date"]) if isinstance(prev.get("entered_date"), str) else prev.get("entered_date", today),
                days_in_reservoir=days,
                entered_strength=prev.get("entered_strength", 0.0),
            )

        # 容量上限：超出时按 days_in_reservoir 降序驱逐最老条目
        if len(merged) > RESERVOIR_MAX_SIZE:
            sorted_entries = sorted(
                merged.values(),
                key=lambda e: e.days_in_reservoir,
                reverse=True,
            )
            evicted = sorted_entries[:len(merged) - RESERVOIR_MAX_SIZE]
            evicted_names = {e.sector_name for e in evicted}
            merged = {k: v for k, v in merged.items() if k not in evicted_names}
            logger.info(
                f"[Node 6] 蓄水池容量驱逐: {len(evicted)} 条 "
                f"(上限 {RESERVOIR_MAX_SIZE}, 被驱逐: {', '.join(evicted_names)})"
            )

        return list(merged.values())

    def _check_reservoir_promotion(
        self,
        reservoir_entries: list[AmbushReservoirEntry],
        sector_ctx: "SectorContext",
        news_ctx: "NewsContext",
        capital_ctx: "CapitalContext",
        sentiment_ctx: "SentimentContext",
        today: date,
    ) -> tuple[list[AmbushCandidate], list[AmbushReservoirEntry]]:
        """蓄水池升格检测（P1-2）。

        升格 = 获得重新走闸门流水线的资格，非直接入池。
        条件：驻留 ≥ RESERVOIR_PROMOTION_MIN_DAYS + 有合格催化剂 + 强度相对回升。

        Returns:
            (promoted_to_pool, remaining_in_reservoir)
        """
        strength_lookup = getattr(sector_ctx, "strength_lookup", {})
        calendar = getattr(news_ctx, "catalyst_calendar", None)
        all_events = calendar.events if calendar else []
        events_by_sector = self._group_events_by_sector(all_events)
        hot_sectors = self._extract_hot_sectors(sentiment_ctx)

        # A2 可观测性：蓄水池板块的催化事件覆盖率
        covered = sum(
            1 for e in reservoir_entries
            if self._lookup_sector_events(e.sector_name, events_by_sector)
        )
        logger.info(
            f"[Node 6] 蓄水池升格: {len(reservoir_entries)} 板块, "
            f"催化事件覆盖 {covered} 个 (事件总数 {len(all_events)})"
        )

        promoted_pool: list[AmbushCandidate] = []
        remaining: list[AmbushReservoirEntry] = []

        for entry in reservoir_entries:
            name = entry.sector_name

            # 条件 1: 驻留天数
            if entry.days_in_reservoir < RESERVOIR_PROMOTION_MIN_DAYS:
                remaining.append(entry)
                continue

            # 条件 2: 有合格催化剂（A2: 归一化查找）
            sector_events = self._lookup_sector_events(name, events_by_sector)
            qualified_events = [
                e for e in sector_events
                if e.confidence >= CATALYST_CONFIDENCE_GATE
                and e.direction != "negative"
            ]
            if not qualified_events:
                # A3 影子评估：若门槛降至 0.60 会放行吗？
                shadow = [
                    e for e in sector_events
                    if 0.6 <= e.confidence < CATALYST_CONFIDENCE_GATE
                    and e.direction != "negative"
                ]
                if shadow:
                    logger.info(
                        f"[Node 6 晋升影子] {name}: 门槛 0.60 会放行 "
                        f"{len(shadow)} 个事件 (置信度 "
                        f"{shadow[0].confidence:.2f})"
                    )
                remaining.append(entry)
                continue

            # 条件 3: 强度相对回升
            current_strength = strength_lookup.get(name)
            if current_strength is None:
                remaining.append(entry)
                continue
            if current_strength <= entry.entered_strength:
                remaining.append(entry)
                continue

            # 升格 → 重新走闸门（升格路径不交叉提名：提名仅当日候选直入）
            gate_passed, fail_reason, accum_detail, accum_strength, _ = self._run_ambush_gates(
                name, sector_events, capital_ctx, hot_sectors, today,
            )

            if gate_passed:
                score, top_event, category_tag, event_titles = self._compute_catalyst_score(
                    qualified_events, today,
                )
                # v2.1: 吸筹档位乘数（strong=1.0 / medium=0.8 / weak=0.6 / legacy=1.0）
                accum_multiplier = {"strong": 1.0, "medium": 0.8, "weak": 0.6, "legacy": 1.0}.get(
                    accum_strength, 1.0
                )
                adjusted_score = round(score * accum_multiplier, 3)
                promoted_pool.append(AmbushCandidate(
                    sector_name=name,
                    sector_code=entry.sector_code,
                    catalyst_score=adjusted_score,
                    category_tag=category_tag,
                    events=event_titles[:3],
                    top_event_date=top_event.event_date if top_event else None,
                    top_event_level=top_event.impact_level if top_event else 1,
                    accumulation_detail=accum_detail,
                    gate_notes=f"蓄水池升格({accum_strength}×{accum_multiplier})",
                    accum_strength=accum_strength,
                ))
                logger.info(
                    f"[Node 6] 蓄水池升格: {name} "
                    f"(驻留{entry.days_in_reservoir}天, "
                    f"强度{entry.entered_strength:.0f}→{current_strength:.0f})"
                )
            else:
                # 闸门未通过 → 回蓄水池（天数已在 _merge_reservoir 中 +1，不再重复计数）
                remaining.append(entry)
                if fail_reason:
                    logger.info(f"[Node 6] 升格未通过闸门: {name}, {fail_reason}")

        return promoted_pool, remaining

    async def _build_ambush_channel(
        self,
        sector: SectorContext,
        capital: CapitalContext,
        news: NewsContext,
        sentiment: SentimentContext,
        regime: Optional[MarketRegime] = None,
    ) -> tuple[list[AmbushCandidate], list[AmbushReservoirEntry], list[str]]:
        """埋伏通道（P3+P1-2）：独立于主线 composite 的平行分支

        流水线：
        1. 今日候选 → 闸门（★★★利空 + 吸筹确认 + 防买顶 + 拥挤度）→ 正式池/蓄水池；
           资金流数据缺失（missing）转蓄水池观察（reason=data_missing），不当作市场否决；
           缺失占比 >50% 时告警数据源故障（比例熔断）
        2. 决策 21：仅被吸筹闸门拦下的候选（利空/防买顶/拥挤均已过）进入
           Agent 催化剂先行提名（单次批量调用，满足 G2/G3 才进视野）
        3. 跨日合并：今日蓄水池 + 前日留存 → 合并列表（天数递增，过期除名）
        4. 升格检测：蓄水池中满足条件的板块 → 重新走闸门 → 正式池

        Returns:
            (ambush_pool, ambush_reservoir, veto_log)
        """
        # 预提取数据（即使 ambush 为空也处理 near_miss）
        hot_sectors = self._extract_hot_sectors(sentiment)
        calendar = getattr(news, "catalyst_calendar", None)
        all_events = calendar.events if calendar else []
        events_by_sector = self._group_events_by_sector(all_events)
        today = date.today()

        pool: list[AmbushCandidate] = []
        reservoir: list[AmbushReservoirEntry] = []
        veto_log: list[str] = []
        nomination_queue: list[dict] = []  # 决策 21: 仅被吸筹拦下的候选，待 Agent 批量评审
        missing_count = 0  # 资金流数据缺失候选数（轨道三熔断统计）

        # ── 近失板块直接入蓄水池（差 1 条闸门，观察）──
        near_miss_list = getattr(sector, "near_miss_ambush", []) or []
        strength_lookup = getattr(sector, "strength_lookup", {})
        for detail in near_miss_list:
            fail_list = detail.near_miss_conditions or ["unknown"]
            reservoir.append(AmbushReservoirEntry(
                sector_name=detail.name,
                sector_code=detail.code,
                reason=f"near_miss:{','.join(fail_list)}",
                entered_date=today,
                weeks_without_catalyst=0,
                days_in_reservoir=0,
                entered_strength=strength_lookup.get(detail.name, detail.strength_score),
            ))
        if near_miss_list:
            logger.info(
                f"[Node 6] 近失入蓄水池: {len(near_miss_list)} 个 "
                f"({', '.join(d.name for d in near_miss_list[:5])})"
            )

        # ── 今日 ambush 候选 → 闸门 → 正式池/蓄水池 ──
        if sector.ambush:
            for detail in sector.ambush:
                name = detail.name
                sector_events = self._lookup_sector_events(name, events_by_sector)

                # 闸门流水线（抽取为独立方法，供升格复用）
                gate_passed, fail_reason, accum_detail, accum_strength, evidence = self._run_ambush_gates(
                    name, sector_events, capital, hot_sectors, today,
                )

                if not gate_passed:
                    if fail_reason.startswith("吸筹观察"):
                        # S1 留痕：非归档场景（终端/当日日志）也可追溯"资金未到的观察"
                        logger.info(f"[Node 6] 吸筹闸门观察: {name}（{accum_detail}）")
                        # ── 决策 21: 提名收集（G5 名额）──
                        # G1（利空）/G4（防买顶+拥挤）已过：闸门序保证能走到吸筹即已过前三闸；
                        # 这里只补检 G2（合格催化）+ G3（历史吸筹证据），拒绝纯消息裸奔
                        if (
                            not AMBUSH_NOMINATION_DISABLED
                            and len(nomination_queue) < AMBUSH_NOMINATION_MAX
                        ):
                            qualified_nominate = [
                                e for e in sector_events
                                if e.confidence >= CATALYST_CONFIDENCE_GATE
                                and e.direction != "negative"
                            ]
                            consec = evidence.get("consecutive_positive", 0)
                            flow_3d = evidence.get("flow_3d_avg")
                            if qualified_nominate and (
                                consec >= AMBUSH_NOMINATION_MIN_CONSECUTIVE
                                or (flow_3d is not None and flow_3d > 0)
                            ):
                                nomination_queue.append({
                                    "name": name,
                                    "code": detail.code,
                                    "strength": strength_lookup.get(name, detail.strength_score),
                                    "qualified_events": qualified_nominate,
                                    "evidence": evidence,
                                    "accum_detail": accum_detail,
                                })
                    if accum_strength == "missing":
                        # 数据缺失 ≠ 市场否决：转蓄水池观察，不入否决日志（区分"无信号"与"坏信号"）
                        # ★★★利空仍否决：缺数据不代表可以无视明确利空
                        negative_block = self._check_negative_catalyst(sector_events)
                        if negative_block:
                            veto_log.append(f"{name}: ★★★利空否决 — {negative_block}")
                        else:
                            reservoir.append(AmbushReservoirEntry(
                                sector_name=name,
                                sector_code=detail.code,
                                reason="data_missing",
                                entered_date=today,
                                weeks_without_catalyst=0,
                                days_in_reservoir=0,
                                entered_strength=strength_lookup.get(name, detail.strength_score),
                            ))
                            missing_count += 1
                        continue
                    if fail_reason:
                        veto_log.append(f"{name}: {fail_reason}")
                    continue

                # 闸门通过 → 催化剂排序
                qualified_events = [
                    e for e in sector_events
                    if e.confidence >= CATALYST_CONFIDENCE_GATE
                    and e.direction != "negative"
                ]

                if not qualified_events:
                    # A3 影子评估
                    shadow = [
                        e for e in sector_events
                        if 0.6 <= e.confidence < CATALYST_CONFIDENCE_GATE
                        and e.direction != "negative"
                    ]
                    if shadow:
                        logger.info(
                            f"[Node 6 闸门影子] {name}: 门槛 0.60 会放行 "
                            f"{len(shadow)} 个事件"
                        )
                    # 无催化分支 → 蓄水池
                    reservoir.append(AmbushReservoirEntry(
                        sector_name=name,
                        sector_code=detail.code,
                        reason="no_catalyst",
                        entered_date=today,
                        weeks_without_catalyst=0,
                        days_in_reservoir=0,
                        entered_strength=strength_lookup.get(name, detail.strength_score),
                    ))
                    continue

                score, top_event, category_tag, event_titles = self._compute_catalyst_score(
                    qualified_events, today,
                )

                # v2.1: 吸筹档位乘数（strong=1.0 / medium=0.8 / weak=0.6 / legacy=1.0）
                accum_multiplier = {"strong": 1.0, "medium": 0.8, "weak": 0.6, "legacy": 1.0}.get(
                    accum_strength, 1.0
                )
                adjusted_score = round(score * accum_multiplier, 3)

                pool.append(AmbushCandidate(
                    sector_name=name,
                    sector_code=detail.code,
                    catalyst_score=adjusted_score,
                    category_tag=category_tag,
                    events=event_titles[:3],
                    top_event_date=top_event.event_date if top_event else None,
                    top_event_level=top_event.impact_level if top_event else 1,
                    accumulation_detail=accum_detail,
                    gate_notes=f"闸门全通过({accum_strength}×{accum_multiplier})",
                    accum_strength=accum_strength,
                ))

        # ── 决策 21: Agent 催化剂先行提名（单次批量调用，仅候选非空时触发）──
        if nomination_queue:
            adopted = await self._llm_nominate_ambush(nomination_queue, regime, today)
            for item in nomination_queue:
                name = item["name"]
                if name not in adopted:
                    continue  # 未获提名 → 「吸筹观察」留痕保留在 veto_log
                score, top_event, category_tag, event_titles = self._compute_catalyst_score(
                    item["qualified_events"], today,
                )
                adjusted_score = round(score * AMBUSH_NOMINATION_MULTIPLIER, 3)
                # accum_strength 记为 evidence 推导档位（不足按 weak）
                ev = item["evidence"]
                derived_strength = (
                    "strong" if ev.get("consecutive_positive", 0) >= 3
                    else "medium" if (ev.get("flow_3d_avg") or 0) > 0
                    else "weak"
                )
                pool.append(AmbushCandidate(
                    sector_name=name,
                    sector_code=item["code"],
                    catalyst_score=adjusted_score,
                    category_tag=category_tag,
                    events=event_titles[:3],
                    top_event_date=top_event.event_date if top_event else None,
                    top_event_level=top_event.impact_level if top_event else 1,
                    accumulation_detail=item["accum_detail"],
                    gate_notes=f"Agent提名({adopted[name]})",
                    accum_strength=derived_strength,
                    nominated=True,
                ))
                # 提名成功 → 移除 S1 观察条目（它不是市场否决）
                veto_log[:] = [
                    v for v in veto_log if not v.startswith(f"{name}: 吸筹观察")
                ]
                logger.info(
                    f"[Node 6] Agent提名入池: {name} "
                    f"(score={score:.3f}×{AMBUSH_NOMINATION_MULTIPLIER}={adjusted_score}, "
                    f"理由: {adopted[name]})"
                )

        # ── P1-2: 蓄水池跨日合并 ──
        prev_reservoir_map = self._load_previous_reservoir()
        today_near_miss_names = {d.name for d in near_miss_list} if near_miss_list else None
        if prev_reservoir_map:
            reservoir = self._merge_reservoir(reservoir, prev_reservoir_map, today, today_near_miss_names)
            logger.info(
                f"[Node 6] 蓄水池跨日合并: 今日新入 + 前日留存 → {len(reservoir)} 条"
            )

        # ── P1-2: 蓄水池升格检测 ──
        if reservoir:
            promoted_pool, remaining_reservoir = self._check_reservoir_promotion(
                reservoir, sector, news, capital, sentiment, today,
            )
            pool.extend(promoted_pool)
            reservoir = remaining_reservoir

        # 去重：同一板块可能同时走"闸门全通过"和"蓄水池升格"两条路径入池
        # 保留"闸门全通过"（权威性更高），去除升格副本
        seen_sectors: set[str] = set()
        deduped_pool: list[AmbushCandidate] = []
        for candidate in pool:
            if candidate.sector_name not in seen_sectors:
                seen_sectors.add(candidate.sector_name)
                deduped_pool.append(candidate)
            else:
                logger.debug(
                    f"[Node 6] 埋伏池去重: {candidate.sector_name} "
                    f"(gate_notes={candidate.gate_notes})"
                )
        pool = deduped_pool

        # 按催化剂排序分降序
        pool.sort(key=lambda x: x.catalyst_score, reverse=True)
        pool = pool[:CATALYST_MAX_POOL_SIZE]

        # 轨道三：比例熔断——无数据占比过半时大概率是数据源故障（Node 3 降级/接口变更），
        # 而非个别板块盲区，显式告警避免静默空池
        if sector.ambush and missing_count > len(sector.ambush) / 2:
            logger.warning(
                f"[Node 6] ⚠️ 资金流数据源覆盖异常: {missing_count}/{len(sector.ambush)} "
                f"埋伏候选无资金流数据（>50%），疑似数据源故障，请检查 Node 3"
            )

        veto_n = sum(1 for v in veto_log if not v.split(": ", 1)[-1].startswith("吸筹观察"))
        observe_n = len(veto_log) - veto_n
        logger.info(
            f"[Node 6] 埋伏通道: {len(sector.ambush)} 候选 → "
            f"正式池={len(pool)}, 蓄水池={len(reservoir)}(含数据缺失{missing_count}), "
            f"否决={veto_n} 观察={observe_n}"
        )
        return pool, reservoir, veto_log

    async def _llm_nominate_ambush(
        self,
        queue: list[dict],
        regime: Optional[MarketRegime],
        today: date,
    ) -> dict[str, str]:
        """Agent 催化剂先行提名评审（决策 21，单次批量调用）。

        仅对"技术状态在埋伏 + 合格催化已在日历 + 唯一卡点是当日资金未确认"的
        候选（调用方已过 G1利空/G4防买顶拥挤闸门，并补检 G2催化/G3吸筹证据）
        请求 Agent 裁定是否提前入池。资金确认从硬闸门降为折扣+复核项。

        Returns:
            {sector_name: reason} 采纳的提名；失败/超时/解析异常 → 空 dict（全部不提名，R4 降级）
        """
        if AMBUSH_NOMINATION_DISABLED or not queue:
            return {}

        def _fmt_flow(v) -> str:
            return f"{v:.1f}亿" if isinstance(v, (int, float)) else "无数据"

        blocks: list[str] = []
        for item in queue:
            ev = item["evidence"]
            event_lines = "\n".join(
                f"    [★{e.impact_level}] {e.title}（{e.event_date}，置信度{e.confidence:.2f}）"
                for e in item["qualified_events"][:2]
            )
            blocks.append(
                f"- {item['name']}（强度分{item['strength']:.0f}）\n"
                f"  吸筹证据: 连续正流入{ev.get('consecutive_positive', 0)}日 | "
                f"3日均 {_fmt_flow(ev.get('flow_3d_avg'))} | 当日 {_fmt_flow(ev.get('today_flow'))}\n"
                f"  合格催化剂:\n{event_lines}"
            )

        regime_desc = (
            f"{regime.regime}（risk_budget={regime.risk_budget:.2f}）" if regime else "未知"
        )
        prompt = (
            "你是「埋伏提名评审」。这些板块的资金确认数据尚不充分（当日资金未确认正流入），"
            "请判断催化剂是否足以支撑提前埋伏。\n\n"
            f"当前市场状态: {regime_desc}\n\n"
            "候选板块:\n" + "\n".join(blocks) + "\n\n"
            "评审纪律：\n"
            "1. 说不出催化剂实质内容、或它与板块的关联牵强 → 拒绝\n"
            "2. 事件日期已在 2 个交易日内（anticipation 大半走完）→ 倾向拒绝\n"
            "3. 当日资金大幅净流出 → 拒绝\n"
            "4. 历史吸筹证据是支持项，但不能替代当日确认；不确定时 nominate=false\n\n"
            "只输出 JSON 数组（无其他文字）：\n"
            '[{"sector": "板块名", "nominate": true/false, "confidence": 0~1, "reason": "≤40字"}]\n'
            "对每个候选板块都给出一条判定。"
        )

        try:
            raw_text = await self._call_llm_async(prompt, temperature=0.2, max_tokens=400)
            parsed = self._parse_json(raw_text)
            items = parsed if isinstance(parsed, list) else parsed.get("nominations", [])
            queue_names = {q["name"] for q in queue}
            adopted: dict[str, str] = {}
            for it in items:
                if not isinstance(it, dict):
                    continue
                name = it.get("sector", "")
                if name not in queue_names or not it.get("nominate"):
                    continue
                try:
                    conf = float(it.get("confidence", 0))
                except (TypeError, ValueError):
                    continue
                if conf < AMBUSH_NOMINATION_MIN_CONFIDENCE:
                    logger.info(
                        f"[Node 6] Agent提名未采纳: {name} 置信度{conf:.2f} < {AMBUSH_NOMINATION_MIN_CONFIDENCE}"
                    )
                    continue
                adopted[name] = str(it.get("reason", ""))[:40]
            if adopted:
                logger.info(f"[Node 6] Agent提名采纳: {list(adopted.keys())}")
            return adopted
        except Exception as e:
            logger.warning(f"[Node 6] Agent提名评审失败（降级：全部不提名）: {e}")
            return {}

    @staticmethod
    def _group_events_by_sector(
        events: list[CatalystEvent],
    ) -> dict[str, list[CatalystEvent]]:
        """将催化剂事件按板块名分组（A2 归一化 + T5 扇出）

        若事件有 affected_sectors（T5 多板块扇出），一条事件进入多个桶；
        否则按 event.sector 单键分组。
        """
        from src.got.board_aliases import normalize_sector_name
        grouped: dict[str, list[CatalystEvent]] = {}
        for event in events:
            # T5: 多板块扇出；归一化后去重，防止别名变体（"芯片"+"芯片概念"）
            # 使同一事件重复进同一桶导致冗余加成被放大（晋升分最多 +40%）
            sectors = event.affected_sectors if event.affected_sectors else [event.sector]
            seen_keys: set[str] = set()
            for raw_sector in sectors:
                key = normalize_sector_name(raw_sector)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                grouped.setdefault(key, []).append(event)
        return grouped

    @staticmethod
    def _lookup_sector_events(
        name: str, events_by_sector: dict[str, list[CatalystEvent]]
    ) -> list[CatalystEvent]:
        """归一化板块名查找事件（A2：精确 → 别名变体 → 空）

        解决晋升路径/P5 中板块名与事件 sector 命名不一致导致的事件饥荒。
        """
        # L1: 精确匹配
        result = events_by_sector.get(name)
        if result:
            return result

        # L2: 别名变体
        from src.got.board_aliases import sector_name_variants
        for variant in sector_name_variants(name):
            if variant == name:
                continue
            result = events_by_sector.get(variant)
            if result:
                return result

        return []

    @staticmethod
    def _check_negative_catalyst(events: list[CatalystEvent]) -> Optional[str]:
        """检查是否有 ★★★ 利空事件（直接否决）"""
        for event in events:
            if event.impact_level >= 3 and event.direction == "negative":
                return f"{event.title}({event.event_date})"
        return None

    # 资金流别名表缓存（进程内只加载一次；文件缺失 → 空表，不影响原逻辑）
    _capital_flow_aliases_cache: Optional[dict[str, list[str]]] = None

    @classmethod
    def _load_capital_flow_aliases(cls) -> dict[str, list[str]]:
        """加载资金流查找别名表（data/capital_flow_aliases.json）。

        用途：Node 2 候选用 THS/问财命名（如"船舶"），东财 sector_flow_all 无同名板，
        通过别名映射到代理板（如"中船系"）补齐资金面覆盖。
        """
        if cls._capital_flow_aliases_cache is not None:
            return cls._capital_flow_aliases_cache
        aliases: dict[str, list[str]] = {}
        try:
            from pathlib import Path
            p = Path("data/capital_flow_aliases.json")
            if p.exists():
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in (data.get("aliases") or {}).items():
                    if isinstance(v, dict):
                        targets = v.get("targets") or []
                    elif isinstance(v, list):
                        targets = v
                    else:
                        targets = [v] if v else []
                    aliases[k] = [str(t) for t in targets if t]
        except Exception as e:
            logger.warning(f"[Node 6] 加载资金流别名表失败: {e}")
        cls._capital_flow_aliases_cache = aliases
        return aliases

    @classmethod
    def _lookup_flow_map(
        cls,
        sector_name: str,
        flow_map: dict,
    ) -> tuple[Optional[float], Optional[str]]:
        """在资金流映射表中查找板块值。

        两阶段：
        1. 原名模糊匹配（保持原有行为，含子串/子词兜底）
        2. 未命中 → 别名表代理板【严格匹配】（去泛化后缀后相等，避免"军民融合"经子词误中"电子竞技"）
        """
        for name, amount in flow_map.items():
            if not isinstance(name, str) or not name:
                continue
            if cls._fuzzy_match(sector_name, cls._strip_generic_suffix(name)):
                try:
                    return float(amount), name
                except (TypeError, ValueError):
                    continue
        for alias in cls._load_capital_flow_aliases().get(sector_name, []):
            for name, amount in flow_map.items():
                if not isinstance(name, str) or not name:
                    continue
                if cls._strip_generic_suffix(name) == cls._strip_generic_suffix(alias):
                    try:
                        return float(amount), name
                    except (TypeError, ValueError):
                        continue
        return None, None

    def _check_accumulation_gate(
        self,
        sector_name: str,
        capital: CapitalContext,
    ) -> tuple[bool, str, str, dict]:
        """吸筹确认闸门（v2.1 三档升级）。

        判定逻辑（按优先级）：
        1. 从归档回读逐日序列，检查板块连续正流入天数和累计值
        2. 归档不可用时降级为当日截面判定

        Returns:
            (passed, detail, accum_strength, evidence)
            - passed: 是否通过闸门
            - detail: 人读友好的判定依据
            - accum_strength: strong / medium / weak / legacy / missing
            - evidence: 吸筹证据 {today_flow, flow_3d_avg, consecutive_positive}，
              供 Agent 提名前置条件 G3 使用（决策 21）
        """
        evidence = {"today_flow": None, "flow_3d_avg": None, "consecutive_positive": 0}
        sector_flow = capital.evidence.sector_flow if capital and capital.evidence else None
        if not sector_flow:
            return False, "资金面数据缺失", "missing", evidence

        # 从 sector_flow_all 中查找板块当日净流入（原名模糊匹配；未命中再用别名表代理板严格匹配）
        sector_flow_all = sector_flow.get("sector_flow_all") or {}
        today_flow, matched_name = self._lookup_flow_map(sector_name, sector_flow_all)

        # top_concepts / top_industries 模糊匹配兜底
        if today_flow is None:
            for key in ("top_concepts", "top_industries"):
                items = sector_flow.get(key, [])
                for item in items:
                    match = re.match(r'(.+?)\(([+-]?[\d.]+)亿\)', item)
                    if match and AggregatorNode._fuzzy_match(sector_name, match.group(1)):
                        today_flow = float(match.group(2))
                        break
                if today_flow is not None:
                    break

        if today_flow is None:
            return False, "未找到板块资金流数据", "missing", evidence
        evidence["today_flow"] = today_flow

        # 代理板标记：命中名与原名不同源（经别名表映射）→ 细节中显式标注，避免误读为板块本体数据
        is_proxy = matched_name is not None and not AggregatorNode._fuzzy_match(
            sector_name, AggregatorNode._strip_generic_suffix(matched_name)
        )
        proxy_tag = f"[代理板{matched_name}]" if is_proxy else ""

        # === v2.1: 从归档回读逐日序列，判定吸筹档位 ===
        sector_flow_all_3d = sector_flow.get("sector_flow_all_3d") or {}
        # 查找板块 3d 日均（与当日查找同策略：原名模糊优先，别名严格兜底）
        flow_3d_avg, _ = self._lookup_flow_map(sector_name, sector_flow_all_3d)
        evidence["flow_3d_avg"] = flow_3d_avg

        # 从归档回查板块逐日序列（计算连续正流入天数）
        # 缓存：单次 run() 内只加载一次（_run_cache 在 run() 入口初始化）
        # 匹配策略：精确键优先 → 别名表严格兜底；不用模糊匹配，
        # 避免"电力"经子串误中"电力物联网/绿色电力"导致档位不确定性抖动（历史 bug）
        consecutive_positive = 0
        try:
            from src.got.persistence import load_capital_flow_series
            cache_key = "_capital_flow_series_5d"
            if not hasattr(self, "_run_cache"):
                self._run_cache = {}
            if cache_key not in self._run_cache:
                archive_series, _ = load_capital_flow_series(days=5)
                self._run_cache[cache_key] = archive_series
            else:
                archive_series = self._run_cache[cache_key]
            series = archive_series.get(sector_name)
            if series is None:
                for alias in self._load_capital_flow_aliases().get(sector_name, []):
                    for key in archive_series:
                        if AggregatorNode._strip_generic_suffix(key) == AggregatorNode._strip_generic_suffix(alias):
                            series = archive_series[key]
                            break
                    if series is not None:
                        break
            if series is not None:
                for _, flow in reversed(series):
                    if flow > 0:
                        consecutive_positive += 1
                    else:
                        break
        except Exception:
            pass  # 归档不可用，降级
        evidence["consecutive_positive"] = consecutive_positive

        # 档位判定（strong/medium 追加当日正向确认，防止历史吸筹但当日出货仍判通过）
        if consecutive_positive >= 3 and today_flow > 0:
            return True, f"{proxy_tag}连续{consecutive_positive}日正流入+当日{today_flow:.1f}亿", "strong", evidence
        elif consecutive_positive >= 3:
            return False, f"{proxy_tag}连续{consecutive_positive}日正流入但当日净流出{abs(today_flow):.1f}亿", "medium", evidence
        elif flow_3d_avg is not None and flow_3d_avg > 0 and today_flow > 0:
            return True, f"{proxy_tag}3日均净流入{flow_3d_avg:.1f}亿+当日{today_flow:.1f}亿", "medium", evidence
        elif today_flow > 0:
            return True, f"{proxy_tag}当日净流入{today_flow:.1f}亿", "weak", evidence
        else:
            return False, f"{proxy_tag}当日净流出{abs(today_flow):.1f}亿", "weak", evidence

    def _check_anti_top_buy(
        self,
        sector_name: str,
        hot_sectors: list[str],
        events: list[CatalystEvent],
        today: date,
    ) -> tuple[bool, str]:
        """防买顶双闸：注意力闸 + 已发酵检查

        注意力闸：板块多只个股上热榜 → 已在传闻期发酵，买在顶上的风险极高
        已发酵检查：同类催化剂已在过去 7 天内出现过 → 行情可能已提前走完

        文献：Kargarzadeh et al. 2026 — "等确认再进 = 买在顶上"
        """
        # 注意力闸：3+ 只关联个股上热榜 → 太热了
        match_count = sum(
            1 for hot in hot_sectors
            if self._fuzzy_match(sector_name, hot)
        )
        if match_count >= 3:
            return False, f"{match_count}只个股上热榜（注意力已过高）"

        # 已发酵检查：如果窗口内已有 ≥2 个同类合格事件且最早的在 5 天前
        # → 行情可能已提前消化（传闻期发酵）
        qualified = [
            e for e in events
            if e.confidence >= CATALYST_CONFIDENCE_GATE
            and e.direction != "negative"
        ]
        if len(qualified) >= 2:
            earliest = min(e.event_date for e in qualified)
            days_since = (today - earliest).days
            if days_since >= 5:
                return False, f"催化剂已在 {days_since} 天前出现（行情可能已提前走完）"

        return True, ""

    @staticmethod
    def _check_crowded_blacklist(
        sector_name: str,
        hot_sectors: list[str],
    ) -> bool:
        """拥挤度黑名单：板块名或子词直接出现在热榜关联板块中

        与防买顶的注意力闸互补：
        - 注意力闸：模糊匹配 3+ 只 → 否决（渐进式）
        - 拥挤黑名单：精确匹配板块名 → 否决（硬闸）

        逻辑：低关注度才是埋伏的利润来源，已被全网关注 = 来晚了
        """
        clean_name = AggregatorNode._strip_generic_suffix(sector_name)
        for hot in hot_sectors:
            clean_hot = AggregatorNode._strip_generic_suffix(hot)
            if clean_name == clean_hot:
                return True
        return False

    @staticmethod
    def _compute_catalyst_score(
        events: list[CatalystEvent],
        today: date,
    ) -> tuple[float, Optional[CatalystEvent], str, list[str]]:
        """B 公式催化剂排序分

        主催化分 = 影响权重 × 临近系数 × 置信度
        冗余加成 = 第 2 个及以上合格催化剂，每个 +20% 主催化分，封顶 +40%

        Returns:
            (score, top_event, category_tag, event_titles)
        """
        if not events:
            return 0.0, None, "", []

        # 按影响等级降序排列，找最高等级事件
        sorted_events = sorted(events, key=lambda e: (-e.impact_level, e.event_date))
        top_event = sorted_events[0]

        # 计算主催化分（取最高等级事件）
        impact_weight = IMPACT_WEIGHT.get(top_event.impact_level, 0.3)

        # 临近系数：事件距今天的交易日数
        trade_days = _count_trade_days(today, top_event.event_date) if top_event.event_date >= today else 0
        if trade_days <= ANTICIPATION_THRESHOLD_DAYS:
            proximity = ANTICIPATION_DECAY_NEAR  # 0.6（anticipation 大半走完）
        else:
            proximity = ANTICIPATION_DECAY_NORMAL  # 1.0

        main_score = impact_weight * proximity * top_event.confidence

        # 冗余加成：第 2 个及以上合格催化剂
        redundancy_count = len(sorted_events) - 1
        if redundancy_count > 0:
            redundancy_bonus = min(redundancy_count * 0.2, 0.4) * main_score
        else:
            redundancy_bonus = 0.0

        total_score = main_score + redundancy_bonus

        # 类型标签：取最高等级事件的 category_tag
        category_tag = top_event.category_tag or "drift"

        # 事件标题列表
        event_titles = [e.title for e in sorted_events[:3]]

        return total_score, top_event, category_tag, event_titles

    @staticmethod
    def _enrich_ambush_sector_intel(
        sector_intels: list[SectorIntel],
        ambush_pool: list[AmbushCandidate],
        ambush_reservoir: list[AmbushReservoirEntry],
    ) -> None:
        """将埋伏通道结果回写到 SectorIntel 的埋伏字段"""
        pool_map = {c.sector_name: c for c in ambush_pool}
        reservoir_map = {r.sector_name: r for r in ambush_reservoir}

        for intel in sector_intels:
            if intel.sector_role != "ambush":
                continue
            name = intel.sector_name
            if name in pool_map:
                candidate = pool_map[name]
                intel.ambush_catalyst_score = candidate.catalyst_score
                intel.ambush_category_tag = candidate.category_tag
                intel.ambush_events = candidate.events
            elif name in reservoir_map:
                intel.ambush_gate_fail = "no_catalyst"

    # =========================================================================
    # Level 2: 组合决策
    # =========================================================================

    @staticmethod
    def _compute_position_from_regime(
        regime: MarketRegime,
        confirmed: list[SectorIntel],
        base_position: float,
    ) -> float:
        """基于 regime + actionable_sectors 计算仓位

        口径隔离：只统计主线/降温侧的 actionable，埋伏板块的仓位由
        Node 7 的 ROLE_MULTIPLIER 单独控制，不顶高全局仓位上限。

        v2.1: dwell 感知缩放（替换硬编码 regime 系数）
        """

        actionable = [
            s for s in confirmed
            if s.classification == "actionable" and s.sector_role != "ambush"
        ]
        actionable_count = len(actionable)
        avg_confidence = (
            sum(s.confidence for s in actionable) / actionable_count
            if actionable_count > 0 else 0.0
        )

        # v2.1: 统一基础仓位 + dwell 感知缩放（REPLACE regime 差异，非相乘叠加）
        # 原方案 bug：regime 基础公式已含 bear/transition/bull 差异，再乘 dwell 系数 → 三重叠加
        # 修复：统一基础公式（bull 口径），regime+dwell 合并为单一缩放表
        position = 0.4 + 0.08 * actionable_count + 0.1 * avg_confidence

        dwell_stage = "established" if regime.dwell_days >= 5 else "fresh"
        REGIME_DWELL_SCALE = {
            ("bull", "established"): 1.0,
            ("bull", "fresh"): 0.9,
            ("bear", "established"): 0.5,
            ("bear", "fresh"): 0.7,
            ("transition", "established"): 0.6,
            ("transition", "fresh"): 0.6,
        }
        dwell_coeff = REGIME_DWELL_SCALE.get(
            (regime.regime, dwell_stage), 0.6
        )
        position *= dwell_coeff

        # 震荡惩罚：transition_count ≥ 5 → × 0.8
        if regime.transition_count >= 5:
            position *= 0.8

        # 限制在 risk_budget 内
        position = min(position, regime.risk_budget)

        return clip(position, 0.0, 0.8)

    @staticmethod
    def _detect_cross_node_temporal_signals(
        regime: MarketRegime,
        capital: CapitalContext,
        sentiment: SentimentContext,
        news: NewsContext,
    ) -> list[CrossNodeTemporalSignal]:
        """跨节点时间线共振检测（v2.1 Phase 6）。

        消费各节点的时间线状态，输出共振信号。
        几何平均置信度（非算术平均）惩罚单组件极端值。
        """
        import math
        signals: list[CrossNodeTemporalSignal] = []

        # ── 提取时间线证据 ──
        dwell = getattr(regime, "dwell_days", 1)
        transition_count = getattr(regime, "transition_count", 0)
        current_regime = regime.regime

        # Node 3: 资金流加速度（从 evidence.sector_flow 提取）
        evidence = getattr(capital, "evidence", None)
        sector_flow = getattr(evidence, "sector_flow", None) or {}
        flow_5d_avg_map = sector_flow.get("sector_flow_all_5d", {})
        flow_1d_map = sector_flow.get("sector_flow_all", {})
        # 市场级 5d 加速度：全板块平均的 5d 流 / 1d 流 - 1
        if flow_5d_avg_map and flow_1d_map:
            market_5d = sum(flow_5d_avg_map.values()) / max(len(flow_5d_avg_map), 1)
            market_1d = sum(flow_1d_map.values()) / max(len(flow_1d_map), 1)
            acceleration = (market_5d / market_1d - 1) if market_1d > 0 else None
        else:
            acceleration = None

        # Node 5: 情绪阶段状态
        stage_state = getattr(sentiment, "stage_state", None)
        persistence_regime = getattr(sentiment, "persistence_regime", None)
        contrarian_alert = getattr(stage_state, "contrarian_alert", False) if stage_state else False
        momentum_dir = getattr(stage_state, "momentum_direction", "stable") if stage_state else "stable"
        sentiment_stage = getattr(sentiment, "sentiment_stage", "neutral")

        # ── 模式 1: bull_confirmation ──
        # regime 刚确认切牛（dwell=1~2）+ 资金 5d 加速度 > 0.15
        if current_regime == "bull" and dwell <= 2 and acceleration is not None and acceleration > 0.15:
            # 置信度：regime 确认(0.8) × 资金加速度(clip(acceleration, 0, 1))
            capital_conf = clip(acceleration, 0.0, 1.0)
            components = {"regime_confirmation": 0.8, "capital_acceleration": capital_conf}
            conf = math.prod(components.values()) ** (1 / len(components))
            signals.append(CrossNodeTemporalSignal(
                signal_type="bull_confirmation",
                confidence=round(conf, 3),
                component_nodes=["node6_regime", "node3_capital"],
                component_evidence={
                    "regime": current_regime,
                    "dwell_days": dwell,
                    "flow_5d_acceleration": round(acceleration, 3),
                },
                reasoning=(
                    f"牛市初期确认（dwell={dwell}）+ 资金 5d 加速度 {acceleration:.2f} > 0.15，"
                    f"机构订单拆分周期启动信号"
                ),
                suggested_action="strong_buy",
            ))

        # ── 模式 2: sentiment_top ──
        # 反向卖出信号 + 资金 5d 流由正转负
        if contrarian_alert and momentum_dir == "cooling" and acceleration is not None and acceleration < -0.1:
            components = {"contrarian_signal": 0.75, "capital_withdrawal": clip(-acceleration, 0.0, 1.0)}
            conf = math.prod(components.values()) ** (1 / len(components))
            signals.append(CrossNodeTemporalSignal(
                signal_type="sentiment_top",
                confidence=round(conf, 3),
                component_nodes=["node5_sentiment", "node3_capital"],
                component_evidence={
                    "sentiment_stage": sentiment_stage,
                    "momentum_direction": momentum_dir,
                    "contrarian_alert": True,
                    "flow_5d_acceleration": round(acceleration, 3),
                },
                reasoning=(
                    f"情绪反向信号（{sentiment_stage}+{momentum_dir}）+ "
                    f"资金 5d 撤退（加速度 {acceleration:.2f}），泡沫破裂风险"
                ),
                suggested_action="sell",
            ))

        # ── 模式 3: catalyst_confirmation ──
        # Node 4 催化兑现 day_3 > 5% + Node 3 对应板块 consistency_days >= 3
        catalyst_cal = getattr(news, "catalyst_calendar", None)
        if catalyst_cal:
            # 查找近期兑现的高影响事件
            from src.storage import CatalystRealizationRecord, DatabaseManager
            try:
                db = DatabaseManager()
                with db.get_session() as session:
                    recent_realized = (
                        session.query(CatalystRealizationRecord)
                        .filter(CatalystRealizationRecord.is_realized == True)
                        .filter(CatalystRealizationRecord.day_3_return.isnot(None))
                        .filter(CatalystRealizationRecord.day_3_return > 5.0)
                        .order_by(CatalystRealizationRecord.event_date.desc())
                        .limit(5)
                        .all()
                    )
                for rec in recent_realized:
                    # 检查该板块是否有 consistency_days >= 3
                    sector_name = rec.sector
                    consistency = sector_flow.get("mainline_consistency_days", {})
                    if isinstance(consistency, dict):
                        cons_days = consistency.get(sector_name, 0)
                    else:
                        cons_days = 0
                    if cons_days >= 3:
                        components = {"catalyst_realized": 0.8, "capital_persistent": clip(cons_days / 10.0, 0.0, 1.0)}
                        conf = math.prod(components.values()) ** (1 / len(components))
                        signals.append(CrossNodeTemporalSignal(
                            signal_type="catalyst_confirmation",
                            confidence=round(conf, 3),
                            component_nodes=["node4_catalyst", "node3_capital"],
                            component_evidence={
                                "sector": sector_name,
                                "day_3_return": rec.day_3_return,
                                "consistency_days": cons_days,
                                "pead_direction": rec.pead_direction,
                            },
                            reasoning=(
                                f"{sector_name} 催化兑现 T+3={rec.day_3_return:.1f}% > 5% + "
                                f"资金连续正流入 {cons_days} 天，PEAD 漂移延长"
                            ),
                            suggested_action="add",
                        ))
            except Exception:
                pass  # DB 查询失败不阻塞

        # ── 模式 4: range_reversal ──
        # 高震荡（transition_count >= 5）+ 低持续性（persistence_regime == "low"）
        if transition_count >= 5 and persistence_regime == "low":
            components = {"regime_choppiness": clip(transition_count / 10.0, 0.0, 1.0), "low_persistence": 0.7}
            conf = math.prod(components.values()) ** (1 / len(components))
            signals.append(CrossNodeTemporalSignal(
                signal_type="range_reversal",
                confidence=round(conf, 3),
                component_nodes=["node6_regime", "node5_sentiment"],
                component_evidence={
                    "transition_count": transition_count,
                    "persistence_regime": persistence_regime,
                },
                reasoning=(
                    f"高震荡（20 日切换 {transition_count} 次）+ "
                    f"情绪低持续性（mean-reverting），短线反转策略有效"
                ),
                suggested_action="short_term_reversal",
            ))

        return signals

    @staticmethod
    def _determine_unified_signal(
        regime: MarketRegime,
        sectors: list[SectorIntel],
    ) -> str:
        """基于 regime + sectors 判定统一信号（主线侧口径，埋伏不参与）"""

        actionable_count = len([
            s for s in sectors
            if s.classification == "actionable" and s.sector_role != "ambush"
        ])
        exclude_count = len([
            s for s in sectors
            if s.classification == "exclude" and s.sector_role != "ambush"
        ])

        if regime.regime == "bull" and actionable_count >= 3:
            return "bullish"
        elif regime.regime == "bear" and exclude_count >= 3:
            return "bearish"
        elif actionable_count > exclude_count:
            return "cautious"  # 偏多但谨慎
        else:
            return "neutral"

    @staticmethod
    def _compute_overall_confidence(
        regime: MarketRegime,
        sectors: list[SectorIntel],
    ) -> float:
        """计算整体置信度（主线侧口径，埋伏不参与）"""

        actionable = [
            s for s in sectors
            if s.classification == "actionable" and s.sector_role != "ambush"
        ]
        if not actionable:
            return 0.3

        avg_conf = sum(s.confidence for s in actionable) / len(actionable)

        # Regime 一致性加成
        if regime.regime == "bull":
            regime_bonus = 0.1
        elif regime.regime == "bear":
            regime_bonus = 0.1
        else:
            regime_bonus = 0.0

        return clip(avg_conf * 0.8 + regime_bonus + 0.2, 0.0, 1.0)

    # =========================================================================
    # 辅助方法
    # =========================================================================

    @staticmethod
    def _parse_capital_flows(capital: CapitalContext) -> list[tuple[str, float]]:
        """解析资金流数据: [("核电", 31.6), ("绿色电力", 23.9), ...]"""
        flows = []
        evidence = capital.evidence
        if not evidence.sector_flow:
            return flows

        for key in ("top_concepts", "top_industries"):
            items = evidence.sector_flow.get(key, [])
            for item in items:
                match = re.match(r'(.+?)\(([+-]?[\d.]+)亿\)', item)
                if match:
                    flows.append((match.group(1), float(match.group(2))))
        return flows

    @staticmethod
    def _parse_sector_level_flows(capital: CapitalContext) -> dict[str, float]:
        """解析板块级资金流（capital.sector_flow_all），排除通道/杠杆聚合项。

        Returns:
            {板块名: 净流入(亿)} — 仅用于板块级错位比对，不做家族级归并。
        """
        flows: dict[str, float] = {}
        sector_flow = capital.evidence.sector_flow if capital and capital.evidence else None
        if not sector_flow:
            return flows
        sector_flow_all = sector_flow.get("sector_flow_all") or {}
        for name, amount in sector_flow_all.items():
            if not isinstance(name, str) or not name:
                continue
            clean = AggregatorNode._strip_generic_suffix(name)
            if clean in AggregatorNode._CAPITAL_CHANNEL_TERMS:
                continue
            try:
                flows[name] = float(amount)
            except (TypeError, ValueError):
                continue
        return flows

    @staticmethod
    def _extract_news_keywords(news: NewsContext) -> list[tuple[str, dict]]:
        """提取消息面关键词: [(关键词, 事件元数据), ...]

        元数据保留影响等级/多源数/事件类型/生命周期，供分级修正使用
        （旧版只保留 sentiment，导致高影响事件与低影响事件修正幅度相同）。
        """
        keywords = []
        for event in news.major_events:
            meta = {
                "title": event.title,
                "sentiment": event.sentiment,
                "impact_level": event.impact_level,
                "source_count": event.source_count,
                "event_type": event.event_type,
                "lifecycle_state": event.lifecycle_state,
            }
            for sector_name in event.affected_sectors:
                keywords.append((sector_name, meta))
            keywords.append((event.title, meta))

        for concept in news.concept_suggestions:
            name = concept.get("concept_name", "")
            if name:
                keywords.append((name, {
                    "title": name,
                    "sentiment": "positive",
                    "impact_level": "low",
                    "source_count": 1,
                    "event_type": "industry",
                    "lifecycle_state": None,
                }))

        return keywords

    @staticmethod
    def _extract_hot_sectors(sentiment: SentimentContext) -> list[str]:
        """提取热榜关联板块"""
        sectors = []
        for stock in sentiment.hot_stocks:
            sectors.extend(stock.associated_sectors)
        return list(set(sectors))

    @staticmethod
    def _strip_generic_suffix(name: str) -> str:
        """剥离泛化后缀（'AIGC概念' → 'AIGC'），防止后缀成为假阳性匹配词素"""
        for suffix in GENERIC_SUFFIXES:
            if name.endswith(suffix) and len(name) > len(suffix):
                return name[:-len(suffix)]
        return name

    @staticmethod
    def _fuzzy_match(sector_name: str, target: str) -> bool:
        """模糊匹配（先剥离泛化后缀再比较）"""
        s = AggregatorNode._strip_generic_suffix(sector_name)
        t = AggregatorNode._strip_generic_suffix(target)
        if s == t:
            return True
        if t in s or s in t:
            return True
        for part in AggregatorNode._split_sector_name(s):
            if len(part) >= 2 and (part in t or t in part):
                return True
        return False

    # A6: 高频泛化词根（≤2 字），子串匹配命中时降权
    _GENERIC_ROOTS = frozenset({
        "AI", "芯片", "电力", "新能源", "医药", "军工", "消费", "金融",
        "周期", "地产", "汽车", "数据", "软件", "硬件", "网络", "通信",
        "光伏", "储能", "机器人", "智能", "科技", "信息", "生物",
    })

    @staticmethod
    def _match_sector(name: str, target: str) -> tuple[bool, str]:
        """统一板块名匹配入口（A5）

        匹配链路：归一化精确 → 别名变体 → 受控子串（≥3 字）

        Returns:
            (matched, match_type): match_type = "exact" / "alias" / "substring" / "generic" / "none"
            "generic" 表示命中泛化词根（A6 护栏），调用方应降权处理
        """
        if not name or not target:
            return False, "none"

        # L1: 归一化精确
        from src.got.board_aliases import normalize_sector_name, sector_name_variants
        norm_name = normalize_sector_name(name)
        norm_target = normalize_sector_name(target)
        if norm_name == norm_target:
            return True, "exact"

        # L2: 别名变体交叉
        name_variants = sector_name_variants(name)
        target_variants = sector_name_variants(target)
        for nv in name_variants:
            for tv in target_variants:
                if nv == tv:
                    return True, "alias"
                if normalize_sector_name(nv) == normalize_sector_name(tv):
                    return True, "alias"

        # L3: 受控子串（≥3 字，剥离后缀后）
        s = AggregatorNode._strip_generic_suffix(norm_name)
        t = AggregatorNode._strip_generic_suffix(norm_target)
        if len(s) >= 3 and len(t) >= 3:
            if s in t or t in s:
                # A6: 命中泛化词根 → 降级为 "generic"
                core = s if s in t else t
                if core in AggregatorNode._GENERIC_ROOTS:
                    return True, "generic"
                return True, "substring"

        return False, "none"

    @staticmethod
    def _split_sector_name(name: str) -> list[str]:
        """拆分板块名为子词列表。

        先剥离泛化后缀（"概念"等），防止拆出"概念"这种万能匹配词素。
        """
        # 先剥离泛化后缀，再拆分
        stripped = AggregatorNode._strip_generic_suffix(name)
        parts = [stripped]
        clean = re.sub(r'[（(].*?[）)]', '', stripped).strip()
        if clean != stripped:
            parts.append(clean)
        if len(clean) >= 4:
            for i in range(2, len(clean)):
                parts.append(clean[:i])
                parts.append(clean[i:])
        for sep in ['/', '、', '与', '及']:
            if sep in stripped:
                parts.extend(stripped.split(sep))
        # 过滤掉泛化后缀本身和过短词
        return [p for p in parts if len(p) >= 2 and p not in GENERIC_SUFFIXES]

    @staticmethod
    def _has_critical(temporal: list[TemporalInconsistency]) -> bool:
        return any(t.severity == "critical" for t in temporal)

    @staticmethod
    def _collect_key_risks(
        news: NewsContext,
        sentiment: SentimentContext,
        temporal: list[TemporalInconsistency],
    ) -> list[str]:
        """汇总关键风险"""
        risks = list(news.key_risks[:3])

        if sentiment.sentiment_stage == "euphoria":
            risks.append("散户情绪极度狂热（反向信号）")
        elif sentiment.sentiment_stage == "capitulation":
            risks.append("散户极度恐慌（市场可能超跌）")

        for t in temporal:
            if t.severity in ("critical", "high"):
                risks.append(f"[时间异常] {t.description}")

        return risks[:6]

    @staticmethod
    def _collect_key_opportunities(sectors: list[SectorIntel]) -> list[str]:
        """汇总关键机会"""
        opps = []
        for s in sectors:
            if s.classification == "actionable":
                opps.append(f"确认板块: {s.sector_name}（score={s.composite_score:.0f}）")
        return opps[:5]

    @staticmethod
    def _build_reasoning(
        regime: MarketRegime,
        sectors: list[SectorIntel],
        contradictions: Optional[list[Contradiction]] = None,
    ) -> str:
        """构建推理文本（包含 mini GOT 板块情报详情 + 矛盾警示）"""
        actionable = [s for s in sectors if s.classification == "actionable"]
        caution = [s for s in sectors if s.classification == "caution"]
        lines = [
            f"[分层GOT] regime={regime.regime}, risk_budget={regime.risk_budget:.2f}, "
            f"{regime.macro_overlay}. 确认板块: {len(actionable)}个, 观察: {len(caution)}个",
            "",
        ]

        # ── 矛盾警示（放在最前面，确保可见）──
        if contradictions:
            mismatch = [c for c in contradictions if c.pair in (
                "sector_capital_mainline_mismatch", "sector_capital_rank_divergence"
            )]
            if mismatch:
                lines.append("⚠️ **主线-资金错位警示**")
                for c in mismatch:
                    lines.append(f"  - {c.description}")
                if mismatch[0].resolution:
                    lines.append(f"  - 建议: {mismatch[0].resolution}")
                lines.append("")
        # 输出每个 actionable 板块的 mini GOT 4 维交叉验证
        for s in sorted(actionable, key=lambda x: x.composite_score, reverse=True):
            # 角色标注：🎯主线-Tier / 🔍埋伏
            role = getattr(s, "sector_role", "mainline")
            if role == "mainline":
                tier = getattr(s, "mainline_tier", None) or "?"
                role_tag = f"🎯主线-{tier}"
            elif role == "ambush":
                role_tag = "🔍埋伏"
            elif role == "cooling":
                role_tag = "🔻高位降温"
            else:
                role_tag = "📌独立"
            lines.append(f"### {role_tag} {s.sector_name}（{s.sector_code}）— 综合 {s.composite_score:.0f} 分，置信度 {s.confidence:.0%}")
            lines.append(f"- **技术面**: {s.tech_score:.0f} 分 | {s.tech_detail or 'N/A'}")
            lines.append(f"- **资金面**: {s.capital_check} | {s.capital_detail or '无异常'}")
            lines.append(f"- **消息面**: {s.news_check} | {s.news_detail or '无显著催化'}")
            lines.append(f"- **情绪面**: {s.sentiment_check} | {s.sentiment_detail or '热度一般'}")
            if s.lifecycle:
                phase_emoji = {"launch": "🚀", "acceleration": "📈", "mature": "✅", "late_stage": "⚠️", "mature_exhausted": ""}.get(s.lifecycle.phase, "❓")
                lines.append(f"- **生命周期**: {phase_emoji} {s.lifecycle.phase}（第 {s.lifecycle.days_since_launch or '?'} 天）")
            if s.reasoning:
                lines.append(f"- **研判**: {s.reasoning}")
            lines.append("")

        # ── 候补板块（top 5 caution，缩小仓位参与选股）──
        watch_display = sorted(caution, key=lambda x: x.composite_score, reverse=True)[:5]
        if watch_display:
            lines.append("---")
            lines.append(f"### 📋 候补板块（{len(caution)} 个，展示 top 5，仓位缩小至 30%/15%）")
            lines.append("")
            capital_emoji = {"aligned": "✅", "neutral": "⚪", "divergent": "❌"}
            for s in watch_display:
                cap_icon = capital_emoji.get(s.capital_check, "⚪")
                role = getattr(s, "sector_role", "mainline")
                role_tag = (
                    "主线" if role == "mainline"
                    else "埋伏" if role == "ambush"
                    else "降温" if role == "cooling"
                    else "独立"
                )
                lines.append(
                    f"- 📋 **{s.sector_name}**（{role_tag}）"
                    f" | {s.composite_score:.0f}分"
                    f" | 资金{cap_icon}{s.capital_check}"
                    f" | caution"
                )
                if s.tech_detail and s.tech_detail != "无详细信息":
                    lines.append(f"  - {s.tech_detail}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _build_data_quality(
        market: MarketContext,
        sector: SectorContext,
        capital: CapitalContext,
        news: NewsContext,
        sentiment: SentimentContext,
    ) -> DataQuality:
        """汇总数据质量"""
        available = ["market", "sector", "capital", "news", "sentiment"]
        failed = []

        if capital.status == "failed":
            failed.append("capital")
            available.remove("capital")
        if news.status == "failed":
            failed.append("news")
            available.remove("news")
        if sentiment.status == "failed":
            failed.append("sentiment")
            available.remove("sentiment")

        return DataQuality(
            available_fields=available,
            missing_fields=[],
            fetch_failed_fields=failed,
        )

    # =================================================================
    # T3: 矛盾仲裁者（语义层裁决）
    # =================================================================

    _ARBITRATION_PROMPT = """你是 A 股市场矛盾的仲裁者。给定以下矛盾信息和多维度证据，判断规则层的矛盾判定是否需要修正。

## 矛盾信息
- 矛盾对: {pair}
- 描述: {description}
- 严重度: {severity}
- 类型: {conflict_type}

## 多维度证据
{evidence_text}

## 跨节点共振信号
{cross_node_signals_text}

## 仲裁规则
1. 只有 confidence >= 0.6 的判断才有效
2. upgrade: 如果规则层过度悲观，可以建议升级（如矛盾实际不严重）
3. downgrade: 如果规则层遗漏风险，可以降级（但只允许降一级，不可直接排除）
4. keep: 维持规则层判定

## 输出格式
JSON: {{"verdict": "upgrade/downgrade/keep", "confidence": 0.0-1.0, "reasoning": "一句话理由（30字内）"}}

只输出 JSON。"""

    async def _arbitrate_contradictions(
        self,
        contradictions: list[Contradiction],
        regime: MarketRegime,
        sector: Optional[SectorContext],
        capital: Optional[CapitalContext],
        news: Optional[NewsContext],
        sentiment: Optional[SentimentContext],
        cross_node_signals: list,
    ) -> list[Contradiction]:
        """T3: 矛盾仲裁者 — 对 medium/high 矛盾调用 LLM 做语义裁决

        设计原则：
        - 仅处理 severity ∈ (medium, high)，low 不仲裁
        - 单次最多 3 个矛盾（R7 成本预算）
        - 权限受限：可 upgrade/downgrade 一级，不可 exclude
        - confidence < 0.6 的仲裁结果忽略
        - 降级时必须写 arbitration_reasoning
        - 失败时等同现状（R4 可降级）
        - 串行调用 + 空响应重试（R5）

        Returns:
            同输入列表，但符合条件的矛盾已填充 arbitration_* 字段
        """
        # 计划约束：regime 新切换（dwell_days<2）状态机尚未稳定，跳过仲裁，
        # 避免对 _detect_contradictions 产出的 regime_fresh_switch 矛盾做语义裁决
        if getattr(regime, "dwell_days", 1) < 2:
            logger.info(
                f"[T3仲裁] regime dwell_days={getattr(regime, 'dwell_days', 'N/A')}<2（新切换），跳过仲裁"
            )
            return contradictions

        # 筛选需要仲裁的矛盾（medium/high，最多 3 个）
        ARBITRATION_MIN_CONFIDENCE = 0.6
        candidates = [
            c for c in contradictions
            if c.severity in ("medium", "high")
        ][:3]

        if not candidates:
            return contradictions

        logger.info(
            f"[T3仲裁] {len(candidates)} 个矛盾候选仲裁 "
            f"(总矛盾 {len(contradictions)})"
        )

        # 构建证据摘要
        evidence_parts = []
        if sector:
            mainline = [s.group_name for s in (sector.active_mainline or [])[:3]]
            evidence_parts.append(f"主线板块: {', '.join(mainline) if mainline else '无'}")
        if capital:
            evidence_parts.append(f"资金面评分: {getattr(capital, 'institutional_score', 'N/A')}")
        if news:
            major_count = len(getattr(news, 'major_events', []))
            evidence_parts.append(f"重大事件: {major_count} 条")
        if sentiment:
            stage = getattr(sentiment, 'sentiment_stage', 'N/A')
            evidence_parts.append(f"情绪阶段: {stage}")
        # 状态机证据：dwell_days/transition_count 作为仲裁输入（计划约束）
        evidence_parts.append(
            f"regime={getattr(regime, 'regime', 'N/A')}, "
            f"dwell_days={getattr(regime, 'dwell_days', 'N/A')}, "
            f"transition_count={getattr(regime, 'transition_count', 'N/A')}"
        )
        evidence_text = "\n".join(evidence_parts) if evidence_parts else "无额外证据"

        # 跨节点信号摘要
        signals_text = "\n".join(
            f"- {s.signal_type}: {getattr(s, 'description', '')[:40]}"
            for s in cross_node_signals[:5]
        ) if cross_node_signals else "无共振信号"

        # 串行调用每个矛盾
        from src.agent.llm_adapter import LLMToolAdapter
        try:
            from src.config import get_config
            llm = LLMToolAdapter(get_config())
        except Exception as exc:
            logger.warning(f"[T3仲裁] LLM 初始化失败，降级跳过: {exc}")
            return contradictions

        for contradiction in candidates:
            try:
                prompt = self._ARBITRATION_PROMPT.format(
                    pair=contradiction.pair,
                    description=contradiction.description[:100],
                    severity=contradiction.severity,
                    conflict_type=contradiction.conflict_type,
                    evidence_text=evidence_text,
                    cross_node_signals_text=signals_text,
                )

                response_text = None
                for attempt in range(3):
                    try:
                        response = await asyncio.to_thread(
                            llm.call_text,
                            messages=[
                                {"role": "system", "content": "你是矛盾仲裁者，只输出 JSON。"},
                                {"role": "user", "content": prompt},
                            ],
                            temperature=0.3,
                            max_tokens=300,
                        )
                        response_text = response.content if response else ""
                        if response_text:
                            break
                    except Exception:
                        pass

                if not response_text:
                    continue

                # 解析响应
                import json, re
                match = re.search(r'\{[^}]+\}', response_text, re.DOTALL)
                if not match:
                    continue
                data = json.loads(match.group())

                verdict = data.get("verdict", "keep")
                confidence = float(data.get("confidence", 0))
                reasoning = data.get("reasoning", "")[:60]

                # 置信度低于阈值 → 忽略仲裁结果
                if confidence < ARBITRATION_MIN_CONFIDENCE:
                    logger.debug(
                        f"[T3仲裁] {contradiction.pair}: 置信度 {confidence:.2f} < 0.6，忽略"
                    )
                    continue

                if verdict not in ("upgrade", "downgrade", "keep"):
                    verdict = "keep"

                # 写入仲裁结果
                contradiction.arbitration_verdict = verdict
                contradiction.arbitration_reasoning = reasoning
                contradiction.arbitration_confidence = confidence

                logger.info(
                    f"[T3仲裁] {contradiction.pair}: {verdict} "
                    f"(置信度 {confidence:.2f}) — {reasoning}"
                )

            except Exception as exc:
                logger.debug(f"[T3仲裁] {contradiction.pair} 仲裁失败: {exc}")

        return contradictions

    @staticmethod
    def _detect_contradictions(
        regime: MarketRegime,
        sectors: list[SectorIntel],
        sector: Optional[SectorContext] = None,
        capital: Optional[CapitalContext] = None,
    ) -> list[Contradiction]:
        """检测矛盾

        1. regime vs 板块分歧（bear + 多 confirmed）
        2. 主线家族 vs 资金流向错位（板块级比对，2026-08-18）
        3. 技术分排名 vs 综合分排名落差（口径分裂的可读化信号，2026-08-18）
        4. 资金强流入 vs 情绪中性/恐惧（跨维背离，2026-08-18）
        """
        contradictions = []

        # ── 矛盾 1: Regime vs 板块分歧 ──
        if regime.regime == "bear":
            actionable_count = len([s for s in sectors if s.classification == "actionable"])
            if actionable_count >= 3:
                contradictions.append(Contradiction(
                    pair="regime_vs_sectors",
                    timeframe="cross",
                    description=f"regime=bear 但有 {actionable_count} 个确认板块",
                    severity="medium",
                    conflict_type="opinion",
                ))

        # ── 矛盾 2: 主线家族 vs 资金流向错位 ──
        if sector and capital and sector.theme_families:
            contradictions.extend(
                AggregatorNode._detect_family_capital_mismatch(sector, capital)
            )

        # ── 矛盾 3: 技术分排名 vs 综合分排名落差（口径分裂的可读化信号）──
        if len(sectors) >= 3:
            comp_rank = {
                s.sector_name: i
                for i, s in enumerate(sorted(sectors, key=lambda x: x.composite_score, reverse=True))
            }
            tech_rank = {
                s.sector_name: i
                for i, s in enumerate(sorted(sectors, key=lambda x: x.tech_score, reverse=True))
            }
            for s in sorted(sectors, key=lambda x: x.composite_score, reverse=True):
                tr = tech_rank.get(s.sector_name, 99)
                cr = comp_rank.get(s.sector_name, 99)
                if abs(tr - cr) < 3:
                    continue
                contradictions.append(Contradiction(
                    pair="tech_composite_rank_divergence",
                    timeframe="cross_dimension",
                    description=(
                        f"「{s.sector_name}」技术分第 {tr + 1} / 综合分第 {cr + 1}，"
                        f"技术分与综合分排名落差 {abs(tr - cr)} 位"
                    ),
                    severity="medium",
                    conflict_type="opinion",
                    resolution="综合分（技术+资金+消息+情绪）与纯技术分排名背离，决策以综合口径为准",
                ))
                break

        # ── 矛盾 4: 资金强流入 vs 情绪中性/恐惧（跨维背离）──
        if (
            capital
            and regime.institutional_score >= 0.6
            and 0.3 <= regime.fear_greed <= 0.6
        ):
            contradictions.append(Contradiction(
                pair="capital_vs_sentiment",
                timeframe="cross_dimension",
                description=(
                    f"机构态度 {regime.institutional_score:+.2f}（强流入）"
                    f"vs 恐贪指数 {regime.fear_greed:.2f}（中性区间），资金与散户情绪未共振"
                ),
                severity="medium",
                conflict_type="opinion",
                resolution="资金强流入但情绪平淡，警惕资金单兵抢跑后回落，谨慎追涨",
            ))

        # ── 矛盾 5: Regime 刚切换低置信（dwell < 2）──
        dwell_days = getattr(regime, "dwell_days", 1)
        if dwell_days < 2:
            contradictions.append(Contradiction(
                pair="regime_fresh_switch",
                timeframe="cross",
                description=f"regime 刚切换（dwell={dwell_days}），所有矛盾检测低置信度",
                severity="low",
                conflict_type="temporal",
                resolution="regime 处于过渡期，信号不稳定，建议降低仓位或观望",
            ))

        return contradictions

    @staticmethod
    def _detect_family_capital_mismatch(
        sector: SectorContext,
        capital: CapitalContext,
    ) -> list[Contradiction]:
        """检测 Node 2 主线家族 vs Node 3 资金流向的错位矛盾（板块级，2026-08-18 重构）

        旧实现把资金 top-5 通过 fuzzy 匹配逐条归并到家族再与家族排名比对，
        导致"芯片概念+存储芯片"等板块级资金被误并进代表板块命名的大家族
        （如"PCB概念等19板块"），制造出"908亿"家族级幻数，且掩盖了
        真正的主线缺资金信号。

        新实现板块级精确比对（strip 泛化后缀后按名精配，不做子串模糊）：
          方向 1（单兵抢跑）：
            资金板块级 top 板块所属家族在 Node 2 综合排名 ≥ 2（未进 top-2）→ high
          方向 2（主线缺资金）：
            Node 2 top-1 家族全部成员板块在板块级资金中无正流入 → medium
        """
        families = sorted(sector.theme_families, key=lambda f: f.family_score, reverse=True)
        if len(families) < 2:
            return []

        # 板块级资金流（精确、排除通道项）
        sector_level = AggregatorNode._parse_sector_level_flows(capital)
        top_flows = sorted(sector_level.items(), key=lambda x: x[1], reverse=True)
        if not top_flows:
            return []
        # strip 后缀后的板块资金表，便于与家族成员精确映射
        sector_level_stripped = {
            AggregatorNode._strip_generic_suffix(name): amount
            for name, amount in sector_level.items()
        }

        # 板块(strip 后缀) → 家族 精确映射
        board_to_family: dict[str, str] = {}
        for fam in families:
            for board in fam.member_boards:
                key = AggregatorNode._strip_generic_suffix(board)
                board_to_family.setdefault(key, fam.family_name)

        family_rank = {f.family_name: i for i, f in enumerate(families)}

        contradictions: list[Contradiction] = []

        # ── 方向 1：资金板块级 top 板块所属家族排名错位（单兵抢跑）──
        for flow_name, amount in top_flows[:5]:
            fam = board_to_family.get(AggregatorNode._strip_generic_suffix(flow_name))
            if not fam:
                continue  # 非主线家族成员，不参与错位判定
            rank = family_rank.get(fam, 99)
            if rank < 2:
                continue  # 资金与综合口径一致（家族在 top-2），无错位
            contradictions.append(Contradiction(
                pair="sector_capital_mainline_mismatch",
                timeframe="cross_dimension",
                description=(
                    f"资金流领先板块「{flow_name}」（净流入 {amount:.0f}亿，板块级）"
                    f"所属家族「{fam}」在 Node 2 综合排名仅第 {rank + 1}，"
                    f"资金与家族综合评分未共振"
                ),
                severity="high",
                conflict_type="opinion",
                resolution=(
                    f"「{flow_name}」单板块资金领先但与整个家族的综合口径"
                    f"（技术+资金+消息+情绪）排名错位，"
                    f"可能为个别板块单兵抢跑或口径未确认，"
                    f"建议以综合分复核其主线地位，勿仅因资金流入追涨"
                ),
            ))
            break

        # ── 方向 2：Node 2 top-1 家族无板块级资金支撑（主线缺资金）──
        if not contradictions:
            top_family = families[0]
            funded = any(
                sector_level_stripped.get(board_key, 0.0) > 0
                for board in top_family.member_boards
                for board_key in [AggregatorNode._strip_generic_suffix(board)]
            )
            if not funded:
                contradictions.append(Contradiction(
                    pair="sector_capital_no_support",
                    timeframe="cross_dimension",
                    description=(
                        f"Node 2 核心主线家族「{top_family.family_name}」"
                        f"（综合第 1，共振{top_family.family_resonance:.0%}）"
                        f"在板块级资金流中无任何正向流入，技术强势缺乏资金确认"
                    ),
                    severity="medium",
                    conflict_type="opinion",
                    resolution=(
                        "技术领先但资金未确认的主线应降低置信度，"
                        "等待资金面后续跟进，避免追高站岗"
                    ),
                ))

        return contradictions

    @staticmethod
    def _adaptive_temporal_check(regime: MarketRegime) -> list[TemporalInconsistency]:
        """EMA 基线时间一致性校验"""
        ema_baseline = load_ema_baseline()
        inconsistencies = []

        if ema_baseline is None:
            return inconsistencies

        prev_score = ema_baseline.get("regime_score", 0.0)
        prev_std = ema_baseline.get("score_std", 0.05)
        sample_count = ema_baseline.get("sample_count", 0)

        # 计算当前 regime_score
        regime_score_map = {"bull": 0.5, "transition": 0.0, "bear": -0.5}
        current_score = regime_score_map.get(regime.regime, 0.0)

        std_floor = max(prev_std, 0.05)
        z_score = abs(current_score - prev_score) / std_floor

        if sample_count < EMA_WARMUP_SAMPLES:
            critical_threshold = 5.0
            high_threshold = 3.5
        else:
            critical_threshold = 3.0
            high_threshold = 2.0

        if z_score > critical_threshold:
            inconsistencies.append(TemporalInconsistency(
                type="sudden_trend_flip",
                description=f"EMA z-score={z_score:.2f}: {prev_score:.3f} → {current_score:.3f}",
                severity="critical",
                action="manual_review_required",
            ))
        elif z_score > high_threshold:
            inconsistencies.append(TemporalInconsistency(
                type="trend_flip",
                description=f"EMA z-score={z_score:.2f}: {prev_score:.3f} → {current_score:.3f}",
                severity="high",
                action="investigate_root_cause",
            ))

        return inconsistencies

    @staticmethod
    async def _simplified_news_override(
        news: NewsContext,
        market: MarketContext,
    ) -> Optional[NewsOverride]:
        """消息面覆盖（系统性政策事件）"""
        overridable = [
            e for e in news.major_events
            if e.event_type == "policy"
            and e.impact_level == "high"
            and e.source_count >= 3
            and any(kw in e.title or kw in e.summary for kw in SYSTEMIC_KEYWORDS)
        ]

        if not overridable:
            return None

        events_text = "\n".join(f"- {e.title} ({e.summary})" for e in overridable)
        prompt = f"""技术面判断：科技成长={market.tech_growth.trend}，score={market.tech_growth.score:.2f}
当前仓位上限：{market.position_limit:.0%}

经过筛选的重大政策事件：
{events_text}

问题：这些事件是否足以覆盖技术面判断？

约束：
- 仓位调整幅度不得超过 ±{POSITION_LIMIT_DELTA:.0%}
- 必须说明理由
- 如果不确定，维持技术面判断

请输出 JSON：
{{"override": true/false, "overridden_position_limit": 0.0~1.0, "reason": "理由（50字内）"}}"""

        from src.agent.llm_adapter import LLMToolAdapter
        adapter = LLMToolAdapter()

        try:
            response = await asyncio.to_thread(
                adapter.call_text,
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=300,
            )
            result = AggregatorNode._parse_json(response.content or "")
            proposed = result.get("overridden_position_limit", market.position_limit)
            clamped = clip(proposed, market.position_limit - POSITION_LIMIT_DELTA, market.position_limit + POSITION_LIMIT_DELTA)

            return NewsOverride(
                override=result.get("override", False),
                overridden_position_limit=clamped if result.get("override") else None,
                original_position_limit=market.position_limit,
                validated_events=[e.title for e in overridable],
                reason=result.get("reason"),
            )
        except Exception as e:
            logger.warning(f"[Node 6] 消息面覆盖 LLM 失败: {e}")
            return None

    def _save_context(
        self,
        unified: UnifiedContext,
        regime: MarketRegime,
        sectors: list[SectorIntel],
        today: date,
    ) -> None:
        """持久化 + 更新 EMA 基线"""
        # 1. 保存当日上下文
        regime_score_map = {"bull": 0.5, "transition": 0.0, "bear": -0.5}
        regime_score = regime_score_map.get(regime.regime, 0.0)

        context_dict = {
            "date": today.isoformat(),
            "unified_signal": unified.unified_signal,
            "overall_confidence": unified.overall_confidence,
            "final_position_limit": unified.final_position_limit,
            "regime": regime.regime,
            "regime_score": regime_score,
            "risk_budget": regime.risk_budget,
            "confirmed_sectors": [s.sector_name for s in sectors if s.classification == "actionable"],
        }
        try:
            save_current_context(context_dict, today)
        except Exception as e:
            logger.warning(f"[Node 6] 上下文持久化失败: {e}")

        # 2. 更新 EMA 基线
        try:
            prev_baseline = load_ema_baseline()
            if prev_baseline is None:
                new_baseline = {
                    "regime_score": regime_score,
                    "score_std": 0.1,
                    "last_updated": today.isoformat(),
                    "sample_count": 1,
                }
            else:
                prev_score = prev_baseline.get("regime_score", prev_baseline.get("weighted_score", 0.0))
                prev_std = prev_baseline["score_std"]
                prev_count = prev_baseline.get("sample_count", 1)

                new_score = EMA_ALPHA * regime_score + (1 - EMA_ALPHA) * prev_score
                delta = regime_score - prev_score
                ss = (prev_count - 1) * prev_std**2
                new_ss = ss + delta**2 * prev_count / (prev_count + 1)
                new_var = new_ss / prev_count
                new_std = max(new_var**0.5, 0.01)

                new_baseline = {
                    "regime_score": new_score,
                    "score_std": new_std,
                    "last_updated": today.isoformat(),
                    "sample_count": prev_count + 1,
                }

            save_ema_baseline(new_baseline)
        except Exception as e:
            logger.warning(f"[Node 6] EMA 基线更新失败: {e}")

    # =========================================================================
    # LLM 调用工具
    # =========================================================================

    @staticmethod
    async def _call_llm_async(
        prompt: str,
        *,
        temperature: float,
        max_tokens: int,
        max_retries: int = 3,
    ) -> str:
        """异步 LLM 调用"""
        from src.agent.llm_adapter import LLMToolAdapter

        adapter = LLMToolAdapter()
        messages = [{"role": "user", "content": prompt}]

        for attempt in range(max_retries + 1):
            try:
                response = await asyncio.to_thread(
                    adapter.call_text, messages,
                    temperature=temperature, max_tokens=max_tokens,
                )
                content = response.content or ""
                if content.strip():
                    return content.strip()
                logger.warning(f"[Node 6] LLM 空响应 (attempt {attempt + 1})")
            except Exception as e:
                logger.warning(f"[Node 6] LLM 调用失败 (attempt {attempt + 1}): {e}")

            if attempt < max_retries:
                await asyncio.sleep(1.0)

        return ""

    @staticmethod
    def _parse_json(text: str) -> dict:
        """从 LLM 输出中提取 JSON"""
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if match:
            text = match.group(1)
        return json.loads(text)

    def _update_monitoring(
        self,
        regime: MarketRegime,
        capital: CapitalContext,
        sentiment: SentimentContext,
        cross_node_signals: list[CrossNodeTemporalSignal],
    ) -> None:
        """更新生产监控 JSON（Phase 6）。

        每日 run() 末尾调用，汇总各节点时间线健康指标 + 异常告警。
        """
        from src.got.persistence import save_monitoring

        # Node 1: regime 切换次数（从归档回读近 20 日统计）
        from src.got.persistence import load_regime_history
        try:
            regime_hist = load_regime_history(days=20)
            switch_count = sum(
                1 for i in range(1, len(regime_hist))
                if regime_hist[i][1] != regime_hist[i - 1][1]
            )
        except Exception:
            switch_count = getattr(regime, "transition_count", 0)

        # Node 3: 资金流非空率
        evidence = getattr(capital, "evidence", None)
        sector_flow = getattr(evidence, "sector_flow", None) or {}
        flow_5d_map = sector_flow.get("sector_flow_all_5d", {})
        flow_1d_map = sector_flow.get("sector_flow_all", {})
        total_boards = max(len(flow_1d_map), 1)
        non_null_5d = sum(1 for v in flow_5d_map.values() if v is not None and v != 0)
        flow_5d_rate = non_null_5d / total_boards

        # Node 3: 主线板块连续正流入天数平均值
        consistency_days_map = sector_flow.get("mainline_consistency_days", {})
        if isinstance(consistency_days_map, dict) and consistency_days_map:
            consistency_avg = sum(consistency_days_map.values()) / len(consistency_days_map)
        else:
            consistency_avg = 0.0

        # Node 5: 反向信号（今日是否触发，由 save_monitoring 维护 30 日窗口）
        stage_state = getattr(sentiment, "stage_state", None)
        contrarian_alert_today = bool(
            stage_state and getattr(stage_state, "contrarian_alert", False)
        )

        # 情绪持续性
        persistence_regime = getattr(sentiment, "persistence_regime", None)

        # 跨节点信号计数
        signal_counts: dict[str, int] = {}
        for sig in cross_node_signals:
            signal_counts[sig.signal_type] = signal_counts.get(sig.signal_type, 0) + 1

        try:
            save_monitoring(
                regime_dwell=getattr(regime, "dwell_days", 1),
                regime_switch_count_20d=switch_count,
                flow_5d_non_null_rate=flow_5d_rate,
                flow_5d_total_boards=total_boards,
                mainline_consistency_avg=consistency_avg,
                contrarian_alert_today=contrarian_alert_today,
                persistence_regime=persistence_regime,
                cross_node_signal_counts=signal_counts,
            )
        except Exception as e:
            logger.warning(f"[Node 6] 监控写入失败: {e}")

    # =========================================================================
    # Mock 输出
    # =========================================================================

    def _mock_output(self) -> UnifiedContext:
        """Mock 输出"""
        regime = MarketRegime(
            regime="transition",
            risk_budget=0.5,
            adx_value=22.0,
            cross_index_consistency=0.6,
            market_direction="分化",
            mainline_count=3,
            institutional_score=0.2,
            news_impact_score=0.1,
            fear_greed=0.55,
            sentiment_stage="neutral",
            sentiment_momentum=0.05,
            macro_overlay="Mock regime: transition",
        )

        mock_sectors = [
            SectorIntel(
                sector_name="半导体",
                sector_code="880422",
                sector_type="concept",
                tech_score=85.0,
                tech_detail="lifecycle=launch, day4, tier=S",
                capital_check="aligned",
                capital_detail="净流入425亿",
                news_check="catalyzed",
                news_detail="韩日存储芯片暴跌",
                sentiment_check="high",
                sentiment_detail="3只关联股票上热榜",
                composite_score=88.0,
                confidence=0.85,
                classification="actionable",
                reasoning="半导体: actionable, 4维共振",
            ),
            SectorIntel(
                sector_name="AIGC",
                sector_code="880555",
                sector_type="concept",
                tech_score=72.0,
                tech_detail="lifecycle=acceleration, day4",
                capital_check="neutral",
                capital_detail="不在 top",
                news_check="catalyzed",
                news_detail="多模态AI突破",
                sentiment_check="medium",
                sentiment_detail="1只关联股票上热榜",
                composite_score=75.0,
                confidence=0.65,
                classification="caution",
                reasoning="AIGC: caution, 技术面强但资金未确认",
            ),
        ]

        return UnifiedContext(
            status="normal",
            unified_signal="cautious",
            overall_confidence=0.65,
            final_position_limit=0.45,
            contradictions=[],
            common_root_cause=None,
            news_override=None,
            temporal_inconsistencies=[],
            key_risks=["市场分化，注意仓位控制"],
            key_opportunities=["确认板块: 半导体（score=88）"],
            concept_suggestions=[],
            reasoning="[Mock v3] regime=transition, 2个板块情报",
            data_quality=DataQuality(
                available_fields=["market", "sector", "capital", "news", "sentiment"],
            ),
            market_regime=regime,
            llm_refinement_used=False,
            ema_baseline=None,
            confirmed_sectors=mock_sectors,
        )
