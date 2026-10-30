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
import hashlib
import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
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
    MainlineEndurance,
    SemanticGlueResult,
    GlueRelation,
    GlueStrength,
    GlueFamily,
    CONTROLLED_FAMILIES,
)
from src.got.catalyst_config import (
    IMPACT_WEIGHT,
    EVIDENCE_TIER_MULTIPLIER,
    ANTICIPATION_DECAY_NEAR,
    ANTICIPATION_DECAY_NORMAL,
    ANTICIPATION_THRESHOLD_DAYS,
    CATALYST_MAX_POOL_SIZE,
    CATALYST_CONFIDENCE_GATE,
    CATALYST_CONFIDENCE_GATE_LOW,
    GOT_NODE4_AMBUSH_EVENTS_ENABLED,
    RESERVOIR_EXPIRY_DAYS,
    RESERVOIR_PROMOTION_MIN_DAYS,
    REFINE_TOP_N,
    REFINE_MAX_SECTORS,
    AMBUSH_NOMINATION_MAX,
    AMBUSH_NOMINATION_MIN_CONFIDENCE,
    AMBUSH_NOMINATION_MULTIPLIER,
    AMBUSH_NOMINATION_MIN_CONSECUTIVE,
    AMBUSH_NOMINATION_DISABLED,
    AMBUSH_RELATIONS_WIRING,
    AMBUSH_G4_FREEZE,
    AMBUSH_ACCUM_MULTIPLIER,
    ACCUMULATION_TOLERANCE,
    AGENT_SEMANTIC_GLUE_ENABLED,
    AGENT_SEMANTIC_GLUE_SHADOW,
    AGENT_SEMANTIC_GLUE_IN_REPLAY,
    GLUE_PROMPT_VARIANT,
    GLUE_MAX_TOKENS,
    GLUE_PIPELINE_ENABLED,
    GOT_A6_ENABLED,
    GOT_AMBUSH_VALIDATION_SHADOW,
    GOT_AMBUSH_EXIT_GRACE_SHADOW,
    AMBUSH_EXIT_GRACE_WINDOW_DAYS,
    GOT_REGIME_JUMP_SHADOW,
    GOT_REGIME_JUMP_PENALTY,
    REGIME_JUMP_LAMBDA,
    REGIME_JUMP_THETA_BULL,
    REGIME_JUMP_THETA_BEAR,
    LLM_CALL_TIMEOUT_S,
    CATALYST_MACRO_AS_REGIME_REF,
)
from src.got import glue_pipeline as _glue_pipeline_mod
from src.got.catalyst_calendar import _count_trade_days, _make_event_id
from src.got.persistence import (
    load_previous_context,
    save_current_context,
    load_ema_baseline,
    save_ema_baseline,
    load_previous_regime_state,
    load_regime_state_history,
    seed_regime_transition_events,
    EMA_ALPHA,
    EMA_WARMUP_SAMPLES,
)
from src.got.indicators import clip
from src.got.family_keywords import PROMPT_EXPOSED_FAMILIES

logger = logging.getLogger(__name__)

# relations 补挂丢弃桶：升格支路/覆盖观测的计数不进主漏斗（只借道 _supplement_from_relations
# 的计数接口）；预置零值而非 defaultdict，防长期运行下键值无界增长
_DISCARD_STATS: dict = {
    "legacy_miss_total": 0,
    "relations_matched": 0,
    "legacy_miss_and_no_relations_hit": 0,
    "relations_hit_all_missed": 0,
    "supplemented_events": 0,
}

# glue 结果归档根目录（可注入：测试通过 conftest 指向 tmp_path，
# 避免夹具数据覆盖生产影子样本 data/got_daily/{date}/agent_glue.json）
GLUE_ARCHIVE_ROOT = Path("data/got_daily")


# =============================================================================
# 常量
# =============================================================================

# 消息面覆盖：系统性政策关键词
SYSTEMIC_KEYWORDS = [
    "降息", "降准", "加息", "IPO", "注册制", "退市",
    "禁止", "禁令", "监管", "制裁", "关税", "财政刺激", "国债",
    "印花税", "暂停", "熔断", "救市", "护盘",
]

# ── 波次生命周期折扣系数（v3.5 S3：老波折扣，纠正动量偏见）──
# 决策 5：不做 fresh boost，只做老波折扣（先靠折扣纠正倒挂，boost 待校准数据到位再评估）
# 决策 6：不叠加过热系数（Phase 2 _compute_overheat_penalty 已有，避免双重惩罚）
WAVE_LIFECYCLE_COEFF = {
    "fresh_cap": 5,        # wave_days ≤ 5 视为新鲜期，系数 1.0（不折扣）
    "aging_start": 6,      # wave_days 6 开始折扣
    "aging_coeff": 0.90,   # wave_days 6-7 → ×0.90
    "late_start": 8,       # wave_days 8 进入晚期
    "late_coeff": 0.85,    # wave_days ≥8 → ×0.85
}


def _wave_coeff(wave_days: Optional[int]) -> float:
    """计算波次生命周期折扣系数（v3.5 S3）。

    Args:
        wave_days: 当前波次持续天数，None 或非主线 → 返回 1.0（不折扣）

    Returns:
        折扣系数（0.85 / 0.90 / 1.0）
    """
    if wave_days is None:
        return 1.0
    if wave_days >= WAVE_LIFECYCLE_COEFF["late_start"]:
        return WAVE_LIFECYCLE_COEFF["late_coeff"]
    if wave_days >= WAVE_LIFECYCLE_COEFF["aging_start"]:
        return WAVE_LIFECYCLE_COEFF["aging_coeff"]
    return 1.0


# 仓位日限幅
POSITION_LIMIT_DELTA = 0.2

# ── 过热准入闸门（2026-09-01：压制过热、避免高位接票）──
# 过热严重度超过阈值的板块不得成为主线（不占 TOP-N 名额、不进选股池）。
# 与成员继承无关：组被拦后其成员自然继承 caution；不影响其他家族池子广度。
# 与 Phase 2 _compute_overheat_penalty 不冲突：这里是准入闸门，非评分叠加惩罚。
# 阈值与过热降权链路的 warning 口径对齐（0.3）。
OVERHEAT_ADMISSION_LIMIT = 0.3

# 泛化后缀：匹配时需剥离，防止 "概念" 等无意义后缀造成假阳性
GENERIC_SUFFIXES = ("概念",)

# （A6 阶段 2：_UNRELIABLE_MATCH_TYPES 随规则评分模式三件套一并退役）

# §7.1 振荡修复：连续钉住（情形 3）达该天数后，挂起候选次日再现即豁免确认
# （迟滞豁免）。0910 实态：raw 在 bull↔bear 日日交替，两日确认永不触发，
# 输出被永久钉死；禁用开关 DISABLE_REGIME_OSCILLATION_FIX=true 回退旧行为。
REGIME_PINNED_EXEMPT_DAYS = 3


# =============================================================================
# 聚合节点
# =============================================================================

class AggregatorNode:
    """Node 6: 分层 GOT 聚合（全局环境 → 板块 mini GOT → 组合决策）"""

    # 资金流中的通道/杠杆聚合项（非真实板块），板块级错位比对一律排除，
    # 防止"融资融券/深股通"这类通道资金被误当成板块资金参与家族错位归并。
    # A6 阶段 2：_parse_sector_level_flows 仍供 _detect_family_capital_mismatch 使用，
    # 本常量随之保留（§6.1 将 #8/#9 列为「删」但审计后发现新消费者，修正为保留）。
    _CAPITAL_CHANNEL_TERMS = frozenset({
        "融资融券", "深股通", "沪股通", "北向资金",
        "陆股通", "融资余额", "融券余额", "两融",
    })

    def __init__(self, use_mock: bool = True, got_runtime=None):
        self.use_mock = use_mock
        self.got_runtime = got_runtime

    async def run(
        self,
        market: MarketContext,
        sector: SectorContext,
        capital: CapitalContext,
        news: NewsContext,
        sentiment: SentimentContext,
        *,
        replay_mode: bool = False,
        run_date: Optional[date] = None,
        holdings=None,
        narrative_context=None,
    ) -> UnifiedContext:
        """执行分层 GOT 聚合

        Args:
            replay_mode: 回放模式（docs/got/archive/agent_glue_plan_0903.md §8.3 A1）。
                         跳过 _save_context / _update_monitoring / EMA 回写 / 状态机，
                         不产生任何持久化副作用。
            run_date: 运行日期（用于 glue 落盘目录）。默认为 date.today()。
                     回放模式时应传入回放日期。
            holdings: H1 持仓上下文（None=无持仓诉求）。仅用于产出 `holdings_intel`
                     （持仓专属 SectorIntel，与 confirmed_sectors 隔离）。
        """
        if self.use_mock:
            logger.info("[Node 6] 使用 mock 数据")
            return self._mock_output()

        self._replay_mode = replay_mode
        self._narrative_context = narrative_context
        # C-P2: 运行日期（用于 _save_glue_result 落盘目录）
        self._run_date = run_date or date.today()
        logger.info("[Node 6] 开始 v3 分层 GOT 聚合" + ("（replay_mode）" if replay_mode else ""))
        today = self._run_date
        self._run_cache = {}  # 单次 run() 内归档回读缓存

        # D-P2: 可观测性数据追踪（每次 run() 重置）
        self._ambush_gate_funnel = None  # D-P2: 埋伏闸门逐层漏斗
        self._ambush_reservoir_overflow = []  # D-P2: 蓄水池容量驱逐记录

        # ── Level 0: 全局环境感知 ──
        regime = self._compute_market_regime(market, sector, capital, news, sentiment)
        # 状态机：跨日记忆 + 转换确认 + dwell 追踪（DISABLE_REGIME_STATE_MACHINE=true 回滚）
        import os as _os
        if replay_mode:
            logger.info("[Node 6] replay_mode=true，跳过状态机（避免污染跨日状态）")
        elif _os.environ.get("DISABLE_REGIME_STATE_MACHINE", "").lower() == "true":
            logger.info("[Node 6] DISABLE_REGIME_STATE_MACHINE=true，跳过状态机")
        elif GOT_REGIME_JUMP_PENALTY:
            # #14 ④ 生产切换：jump 模型替换确认层（影子判据达标 + 负责人拍板后才有此分支）
            regime = self._apply_regime_jump_model(regime)
        else:
            regime = self._apply_regime_state_machine(regime)

        # #14 ④ 影子层（缺省开）：jump 模型只算只落盘，零决策改动。
        # replay_mode 跳过（不产生持久化副作用，0903 A1 契约）。
        if GOT_REGIME_JUMP_SHADOW and not replay_mode and not GOT_REGIME_JUMP_PENALTY:
            try:
                self._compute_regime_jump_shadow(regime)
            except Exception as exc:  # noqa: BLE001 — 影子旁路：降级不阻塞主链路
                logger.warning("[Node 6] jump 影子计算失败（已忽略）: %s", exc)
        logger.info(
            f"[Node 6] Level 0 — regime={regime.regime}, "
            f"risk_budget={regime.risk_budget:.2f}, "
            f"fear_greed={regime.fear_greed:.2f}, "
            f"dwell_days={regime.dwell_days}"
        )

        # ── Agent 语义粘合层（docs/got/archive/agent_glue_plan_0903.md §5.4 时序修订）──
        # 时序前置到 _build_ambush_channel 之前（2026-09-30 全流程实测修正）：
        # 埋伏名单日间轮换几乎不相交，前日归档 relations 对当日名单覆盖实测为 0，
        # 补挂须消费当日 in-memory glue。环险排除：_collect_sector_candidates 的
        # 埋伏支路直接消费 sector.ambush（Node 2 输入，不依赖池产出），环仅来自
        # 池外补入支路——此处以 ambush_pool=None 收集，通道后用完整池重收集补齐。
        candidates_for_glue, member_to_group_glue = self._collect_sector_candidates(sector, None)
        glue = await self._agent_semantic_glue(
            candidates_for_glue, news, capital, sentiment,
        )
        # A6 接线：glue 是生产路径，失败必须报错
        # 影子期已退出（2026-09-17），规则路径即将清理，不允许静默回退
        # replay_mode 豁免：replay 允许 degraded glue（不产生持久化副作用）
        is_replay = getattr(self, '_replay_mode', False)
        if not GOT_A6_ENABLED:
            raise RuntimeError("[Node 6] GOT_A6_ENABLED=false，glue 已转正，不允许关闭")
        if glue is None:
            raise RuntimeError("[Node 6] glue 返回 None，无法继续")
        if not is_replay:
            if getattr(glue, "status", None) != "ok":
                raise RuntimeError(f"[Node 6] glue 状态异常: {getattr(glue, 'status', 'unknown')}，原因: {getattr(glue, 'warnings', [])}")
            if getattr(glue, "degraded", False):
                raise RuntimeError(f"[Node 6] glue 已降级，不允许继续: {getattr(glue, 'warnings', [])}")
        self._a6_glue_active = bool(not is_replay and getattr(glue, "status", None) == "ok" and not getattr(glue, "degraded", False))
        self._last_glue = glue
        # relations 补挂索引（当日优先）：写入缓存供 _build_ambush_channel 及其内部
        # 升格分支消费；degraded/no_candidates 时索引为空→补挂恒 no-op
        self._rel_idx_cache = self._build_relations_index(glue)
        # Node 4 事件×glue（2026-10-23）：major_events 带合成 id 进 glue prompt，
        # relations 命中即按 board_code 归属，名称扇出降级为兜底。
        self._node4_glue_index = self._build_node4_glue_index(glue, news)

        # A6 relations 共享回填（2026-10-30）：
        # 1) llm_narrative/global_map 的 affected_sectors 用同一 relation 改写并 UPSERT；
        # 2) concepts/key_risks/hot_topics/narrative 由 A6 一次合成回填 Node 4/5。
        self._apply_glue_relations_to_calendar(news, glue, candidates_for_glue, replay=is_replay)
        self._backfill_semantic_aggregates(news, sentiment, glue)
        # 内存 relations（板块名→信号）供 Phase 2 选股消息面直接消费，避免二次 LLM 解释
        try:
            from src.got.glue_relations import relation_signal_map

            self._semantic_relations = relation_signal_map(glue.relations, candidates_for_glue)
        except Exception as exc:  # noqa: BLE001 — 供数旁挂，失败时下游走 relation_missing
            logger.warning("[Node 6] semantic_relations 构建失败（已忽略）: %s", exc)
            self._semantic_relations = {}

        # ── Level 1 平行分支: 埋伏通道（P3，先于 mini GOT 分类，供拆分消费）──
        ambush_pool, ambush_reservoir, ambush_veto_log = await self._build_ambush_channel(
            sector, capital, news, sentiment, regime=regime,
            node4_glue_index=self._node4_glue_index,
        )

        # ── Level 1: 板块 mini GOT（主线/降温走 TOP-N，埋伏按通道结果拆分消费）──
        # 重收集（含池外补入）：蓄水池升格/近失入池的板块可能不在 Node 2 名单，
        # 须补入候选才能拿 SectorIntel（与前置收集同一规则函数，幂等）
        candidates_for_glue, member_to_group_glue = self._collect_sector_candidates(sector, ambush_pool)
        sector_intels = self._build_sector_intel(
            sector, capital, news, sentiment,
            ambush_pool=ambush_pool, ambush_reservoir=ambush_reservoir,
            glue=glue,
            candidates=candidates_for_glue, member_to_group=member_to_group_glue,
        )
        # 将埋伏通道结果回写到 SectorIntel（供 reasoning.md 和 Phase 2 消费）
        self._enrich_ambush_sector_intel(sector_intels, ambush_pool, ambush_reservoir)

        # ── H1 持仓专属板块情报（2026-10-27，原则②供数）──
        # 只覆盖 confirmed_sectors 未收录的持仓诉求板块；独立字段、独立角色 holding，
        # 不参与 TOP-N 名额竞争、不进选股池、不改 position_limit/埋伏池。
        holdings_intel: list[SectorIntel] = []
        try:
            holdings_intel = self._build_holdings_intel(sector, sector_intels, holdings)
        except Exception as exc:  # noqa: BLE001 — 供数旁挂，失败不阻断聚合
            logger.warning("[Node 6] holdings_intel 构建失败（已忽略）: %s", exc)
            holdings_intel = []
        if holdings_intel:
            logger.info(
                "[Node 6] 持仓专属情报 %d 个（未覆盖诉求板块）: %s",
                len(holdings_intel), [i.sector_name for i in holdings_intel],
            )

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

        # A6 语义粘合已是唯一决策语义入口（2026-10-30 运行时收敛）：
        # 原 _llm_refine_sectors（narrative/cross_sector_risks）删除，narrative 由 A6 产出，
        # cross_sector_risks 并入 UnifiedContext.key_risks（_collect_key_risks）。
        # 确定性提升兜底保留（硬规则保障）。
        refine_calendar = getattr(news, "catalyst_calendar", None)
        # A6 接线：构建 board_code→sector_name 反查表，供 _glue_aware_events_map 使用。
        # candidates_for_glue 已在上方收集，此处重新取一次以取到 detail.code。
        code_to_name: dict = {}
        for _name, _detail, _role in candidates_for_glue:
            _code = getattr(_detail, "code", "") if _detail else ""
            if _code:
                code_to_name.setdefault(_code, _name)
        refine_events_map = self._glue_aware_events_map(
            lambda evs: self._group_events_by_sector(self._exclude_macro_regime_ref(evs)),
            refine_calendar.events if refine_calendar else [],
            glue=glue,
            code_to_name=code_to_name,
        )

        # ── 确定性提升兜底（LLM 概率上调之外的硬规则保障）──
        promoted_n = self._deterministic_catalyst_promotion(
            sector_intels, ambush_pool, refine_events_map,
        )
        if promoted_n:
            logger.info(f"[Node 6] 确定性提升: {promoted_n} 个埋伏板块上调为 actionable")

        # ── Level 2: 组合决策 ──
        position_limit = self._compute_position_from_regime(regime, sector_intels)
        unified_signal = self._determine_unified_signal(regime, sector_intels)

        # B3 注释修正：实际只有两层（状态缩放 + 回撤保护）
        # 波动率调整（Node 1 position_limit）当前未接入 final_position_limit
        # 旧版日限幅 bug：当 market.position_limit=0.0 时，clip 到 [−0.2, 0.2] 导致锁死 20%
        # 参考：Moreira & Muir (2017), AlgoKing "Dynamic Position Sizing with Kelly + Regime"
        final_position = position_limit

        # ── 辅助信息 ──
        contradictions = self._detect_contradictions(regime, sector_intels, sector, capital)
        temporal = self._adaptive_temporal_check(regime)
        override = await self._simplified_news_override(news, market) if not replay_mode else None

        # ── Phase 3b：分歧摘要（三态开关，批次 5 §3.2，矛盾检测段旁挂）──
        # shadow（默认开）=只算 11 类冲突分类、落 conflict_summary_shadow 观测，
        #   现有硬编码矛盾对检测 + LLM 仲裁一字不动；
        # enabled=额外把 decision_path_hint 注入 T3 仲裁 prompt（改 LLM 输入=行为变更，
        #   转正需影子期读数 + 负责人判定，见计划 §9.3）；两者皆关=不进。
        _dg_shadow_on = os.getenv("GOT_DISAGREEMENT_SHADOW", "1") == "1"
        _dg_enabled_on = os.getenv("GOT_DISAGREEMENT_ENABLED", "0") == "1"
        conflict_summary_shadow_dict = None
        _decision_path_hint = ""
        if _dg_shadow_on or _dg_enabled_on:
            try:
                conflict_summary_shadow_dict = self._build_conflict_summary_shadow(
                    regime, sector_intels, sector, capital, sentiment,
                )
                if _dg_enabled_on and conflict_summary_shadow_dict:
                    _decision_path_hint = conflict_summary_shadow_dict.get("decision_path_hint", "")
            except Exception as exc:  # 观测旁路：任何异常降级为 None，绝不阻塞主流程
                logger.warning("[Node 6] 3b 分歧摘要计算失败（已忽略，影子字段置空）: %s", exc)
                conflict_summary_shadow_dict = None
                _decision_path_hint = ""

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
        if replay_mode:
            logger.info("[Node 6] replay_mode: 跳过 _arbitrate_contradictions")
        else:
            contradictions = await self._arbitrate_contradictions(
                contradictions, regime, sector, capital, news, sentiment, cross_node_signals,
                decision_path_hint=_decision_path_hint,
            )

        key_risks = self._collect_key_risks(
            news, sentiment, temporal,
            extra_risks=getattr(glue, "cross_sector_risks", None),
        )
        key_opportunities = self._collect_key_opportunities(sector_intels)

        # ── 主线续航评估（v3.5 S4：对 active_mainline 家族逐一生成）──
        mainline_endurance = self._synthesize_mainline_endurance(
            sector_intels, sector, news, regime, glue=glue,
        )
        if mainline_endurance:
            logger.info(
                f"[Node 6] 主线续航评估: {len(mainline_endurance)} 个家族 — "
                f"{[e.name for e in mainline_endurance]}"
            )

        # ── 家族见顶持续压制（FTS）：消费 endurance_band=topping_risk + 跨日 latch ──
        # 缺省 GOT_FAMILY_TOPPING_GATE_ENABLED=0 时不产台账（family_suppression=[]，逐字节不变）。
        family_suppression = []
        if os.getenv("GOT_FAMILY_TOPPING_GATE_ENABLED", "0") == "1":
            # C-2：FTS 为旁路闸门特性，任何合成异常一律降级为空台账，绝不击穿 Node 6 主链路。
            try:
                _prev_suppression = self._load_previous_family_suppression()
                family_suppression = self._synthesize_family_suppression(
                    mainline_endurance, _prev_suppression,
                )
                if family_suppression:
                    logger.info(
                        f"[Node 6] 家族见顶压制(FTS): {len(family_suppression)} 个家族 — "
                        f"{[(e.name, 'aggravated' if e.aggravated else 'soft', e.days_suppressed) for e in family_suppression]}"
                    )
            except Exception as exc:  # noqa: BLE001 — 观测/闸门旁路：降级不阻塞
                logger.warning("[Node 6] FTS 合成失败（已忽略，family_suppression 置空）: %s", exc)
                family_suppression = []

        # ── 构建输出 ──
        data_quality = self._build_data_quality(market, sector, capital, news, sentiment, glue=glue)
        # P0-B B2: 护栏空转日志（lifecycle/crowded_families 等关键字段缺失时显式告警）
        if data_quality.missing_fields:
            logger.warning(
                f"[Node 6] 数据质量告警: missing_fields={data_quality.missing_fields} "
                f"（护栏可能空转，详见 docs/got/archive/agent_glue_plan_0903.md §9）"
            )

        # ── Phase 4b：市场结构上下文消费（三态开关，批次 5 §2.2）──
        # shadow（默认开）=每日真跑读 get_market_theme_context() 落观测、零决策改动；
        # enabled=额外对 overall_confidence 施一致性因子并填 market_theme 真值；两者皆关=不进。
        _mt_shadow_on = os.getenv("GOT_MARKET_THEME_SHADOW", "1") == "1"
        _mt_enabled_on = os.getenv("GOT_MARKET_THEME_ENABLED", "0") == "1"
        market_theme_shadow_dict = None
        market_theme_ctx = None
        _overall_confidence = self._compute_overall_confidence(regime, sector_intels)
        if _mt_shadow_on or _mt_enabled_on:
            from src.got.market_theme import get_market_theme_context
            _theme_ctx = get_market_theme_context()
            if _mt_shadow_on:
                market_theme_shadow_dict = self._build_theme_shadow_dict(_theme_ctx)
            if _mt_enabled_on:
                market_theme_ctx = _theme_ctx
                _factor = self._theme_confidence_factor(_theme_ctx, sector)
                if _factor < 1.0:
                    _overall_confidence = clip(_overall_confidence * _factor, 0.0, 1.0)
                    logger.info(
                        "[Node 6] 4b enabled：题材阶段与主线状态不一致 → confidence ×%.1f", _factor
                    )
        # F1 埋伏道一致性校验（2026-10-15，影子观测）
        ambush_lane_validation = self._build_ambush_lane_validation(
            ambush_pool, ambush_reservoir, sector
        )

        # 埋伏出场催化宽限影子（2026-10-16 RCA，GOT_AMBUSH_EXIT_GRACE_SHADOW=1 缺省）：
        # E14-only、只算只落盘、零决策改动。replay_mode 跳过（0903 A1 契约：回放不产生持久化副作用）。
        if GOT_AMBUSH_EXIT_GRACE_SHADOW and not replay_mode:
            try:
                self._compute_ambush_exit_grace_shadow(sector, news)
            except Exception as exc:  # noqa: BLE001 — 影子旁路：降级不阻塞主链路
                logger.warning("[Node 6] 出场宽限影子计算失败（已忽略）: %s", exc)

        unified = UnifiedContext(
            status="manual_review_required" if self._has_critical(temporal) else "normal",
            unified_signal=unified_signal,
            overall_confidence=_overall_confidence,
            final_position_limit=clip(final_position, 0.0, 1.0),
            contradictions=contradictions,
            common_root_cause=None,
            news_override=override,
            temporal_inconsistencies=temporal,
            key_risks=key_risks,
            key_opportunities=key_opportunities,
            concept_suggestions=news.concept_suggestions,
            reasoning=self._build_reasoning(
                regime, sector_intels, contradictions,
                narrative=getattr(glue, "narrative", ""),
            ),
            data_quality=data_quality,
            market_regime=regime,
            llm_refinement_used=False,
            ema_baseline=None,
            confirmed_sectors=sector_intels,
            holdings_intel=holdings_intel,
            narrative_context=getattr(self, "_narrative_context", None),
            ambush_pool=ambush_pool,
            ambush_reservoir=ambush_reservoir,
            ambush_veto_log=ambush_veto_log,
            ambush_lane_validation=ambush_lane_validation,
            crowded_families=getattr(sector, "crowded_families", []) or [],
            mainline_endurance=mainline_endurance,
            family_suppression=family_suppression,
            cross_node_signals=cross_node_signals,
            # A6 阶段 2：资金背离诊断随规则模式退役，恒为 None（schema 字段保留）
            capital_divergence=None,
            # D-P2: 埋伏闸门漏斗 + 蓄水池溢出
            ambush_gate_funnel=self._ambush_gate_funnel,
            ambush_reservoir_overflow=self._ambush_reservoir_overflow if self._ambush_reservoir_overflow else None,
            # A6 接线可观测性：glue 实际是否进入 SectorIntel 下游，供归档回溯
            glue_consumed=any(getattr(s, "reasoning", "").find("[A6-glue") >= 0 for s in sector_intels),
            glue_status=glue.status,
            market_theme_shadow=market_theme_shadow_dict,
            market_theme=market_theme_ctx,
            conflict_summary_shadow=conflict_summary_shadow_dict,
        )

        # 持久化（回放模式跳过）
        if replay_mode:
            logger.info("[Node 6] replay_mode: 跳过 _save_context / EMA 回写 / _update_monitoring")
        else:
            self._save_context(unified, regime, sector_intels, today)

            # 回写 EMA 基线
            updated_ema = load_ema_baseline()
            if updated_ema:
                unified.ema_baseline = updated_ema.get("regime_score")

        # ── 生产监控更新（Phase 6，回放模式跳过）──
        if not replay_mode:
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
        # attack_power（Node 1 综合进攻强度 0-1，Optional 兼容，缺失时 None）
        _market_state = getattr(market, 'market_state', None)
        attack_power = getattr(_market_state, 'attack_power', None) if _market_state else None

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

        # attack_power 作为 Node 1 第二信号（独立子项，与 ADX 分支叠加）
        # 0.5 中性，±0.1 幅度：attack_power=0.15 → -0.07；attack_power=0.85 → +0.07
        if attack_power is not None:
            regime_score += (attack_power - 0.5) * 0.2

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

        # Node 1 attack_power 双档熔断：技术面极弱时禁止判 bull
        # 依据：Node 2 已有 5 档主线裁剪（<0.15→1 / <0.30→2），Step 1 与之对齐
        # 9月回测：attack_power 0.16-0.17 时 Node 6 判 bull 导致 bear 市场采 bull 仓位
        # 单档熔断只能压到 transition（bear 需 score<-0.3），无法触发 bear -5% stop_loss
        import os
        if os.getenv("GOT_WINRATE_STEP1_ENABLED", "1") == "1" and attack_power is not None:
            if attack_power < 0.15 and regime_score > -0.35:
                # 档 1：crash_override 区间 → 强制 bear
                logger.info(
                    f"[Node 6] attack_power={attack_power:.2f} < 0.15 熔断 → bear "
                    f"(原 regime_score={regime_score:.2f})"
                )
                regime_score = min(regime_score, -0.35)
            elif attack_power < 0.30 and regime_score > 0.25:
                # 档 2：Node 2 主线裁剪第 2 档 → 压回 transition
                logger.info(
                    f"[Node 6] attack_power={attack_power:.2f} < 0.30 熔断 bull → transition "
                    f"(原 regime_score={regime_score:.2f})"
                )
                regime_score = min(regime_score, 0.25)

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
        # 机构态度维度口径标注（缺陷③A：DDE 摘除后不再靠 confidence≤0.5 触发，
        # 改用 Node 3 显式置位的 single_dimension——仅剩 sector_flow 一维时诚实标注）
        cal_note = ""
        if getattr(capital, "single_dimension", False):
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
            # #14 ④：阈值前 raw 分数留痕（jump 模型影子输入 + 归档标定源）
            raw_regime_score=round(regime_score, 4),
        )

    @staticmethod
    def _apply_regime_state_machine(regime: MarketRegime) -> MarketRegime:
        """Regime 状态机：跨日记忆 + 转换确认 + dwell 追踪。

        状态转换规则（v2.1 + §7.1 振荡修复）：
        - raw == previous → dwell_days += 1
        - raw != previous 且 raw == transition_pending → 确认切换，dwell_days = 1
        - raw != previous 且 raw != transition_pending → 保持 previous，
          transition_pending = raw（首次出现，待次日确认），pinned_days += 1
        - 迟滞豁免（§7.1 主因修复）：pinned_days ≥ REGIME_PINNED_EXEMPT_DAYS 时，
          当日 raw（≠previous）直接确认为新 regime（放弃两日确认），
          打破 bull↔bear 日日交替导致的确认死锁（交替态下「候选再现」永不
          成立，只有即时确认才能真正脱钉）。脱钉后纯交替信号被迟滞抑制
          （输出锁在首个脱钉值），持续两日的真实信号仍按情形 2 正常确认
        - 冷启动（无归档/缺字段）→ current = raw，dwell_days = 1，不阻塞

        transition_count：近 20 交易日**确认切换事件**数（§7.1 缺陷②修复：
        由状态机自记 transition_events 事件列表作真相源，不再回看输出值
        相邻 diff；旧归档无事件记忆时把 seed_regime_transition_events 迁移种子
        **播种进列表**后继续追加，窗口按日期区间判定（含当日）。
        豁免强切日归档标 regime_switch_exempt=True（与两日确认区分，R2 复盘）。
        DISABLE_REGIME_OSCILLATION_FIX=true 一键回退旧行为（无豁免、
        输出 diff 计数、不记事件；关闭后事件链从迁移种子重建）。
        """
        import os as _os
        legacy_no_osc_fix = _os.environ.get(
            "DISABLE_REGIME_OSCILLATION_FIX", ""
        ).strip().lower() == "true"

        # 加载前日 regime 状态
        prev_state = load_previous_regime_state()

        # 加载 20 日 regime 历史（用于 transition_count 窗口/迁移种子）
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
                "pinned_days": 0,
                "transition_events": None,
            })

        prev_regime = prev_state["regime"]
        prev_dwell = prev_state["dwell_days"]
        prev_pending = prev_state["transition_pending"]
        prev_pinned = int(prev_state.get("pinned_days") or 0)
        prev_events: list[str] = list(
            prev_state.get("transition_events") or []
        )

        # 状态转换判定
        switch_confirmed = False
        exempt_applied = False
        if raw_regime == prev_regime:
            # 情形 1：与昨日相同 → dwell+1，清除 pending，解除钉住
            new_regime = prev_regime
            new_dwell = prev_dwell + 1
            new_pending = None
            new_pinned = 0
        elif raw_regime == prev_pending and (
            legacy_no_osc_fix or prev_pinned < REGIME_PINNED_EXEMPT_DAYS
        ):
            # 情形 2：与 pending 相同 → 确认切换（旧行为在开关开启时原样保留）
            new_regime = prev_pending
            new_dwell = 1
            new_pending = None
            new_pinned = 0
            switch_confirmed = True
            logger.info(
                f"[Node 6] Regime 切换确认: {prev_regime} → {new_regime}"
            )
        elif not legacy_no_osc_fix and prev_pinned >= REGIME_PINNED_EXEMPT_DAYS:
            # 豁免分支（§7.1 核心出口，审核⑧合并原「2-豁免/3-豁免」两分支：
            # 行为完全相同）。日日交替下 pending 每日被换挂、「候选再现」
            # 很少命中；钉住满阈值后直接信任当日 raw（含再现与换挂两种情形）
            new_regime = raw_regime
            new_dwell = 1
            new_pending = None
            new_pinned = 0
            switch_confirmed = True
            exempt_applied = True
            logger.info(
                f"[Node 6] Regime 迟滞豁免切换: {prev_regime} → {new_regime}"
                f"（已钉住 {prev_pinned} 日，直接确认当日信号，放弃两日确认"
                + ("，候选再现）" if raw_regime == prev_pending else "）")
            )
        else:
            # 情形 3：新信号 ≠ pending → 保持昨日，设新 pending，钉住 +1
            new_regime = prev_regime
            new_dwell = prev_dwell + 1
            new_pending = raw_regime
            new_pinned = prev_pinned + 1
            logger.info(
                f"[Node 6] Regime 首次出现: {raw_regime}，待次日确认"
                + (f"（已钉住 {new_pinned} 日）" if new_pinned else "")
            )

        # transition_count（§7.1 缺陷②）：确认切换事件列表在 20 日窗口内的条数。
        # 窗口按**日期区间**判定（历史最早日 ≤ e ≤ 今日）：当日归档在运行时
        # 尚未落盘，按归档成员包含判定会系统性漏计当日事件（审核修正①）
        today_str = date.today().strftime("%Y%m%d")
        window_lo = regime_history[0][0] if regime_history else None
        if legacy_no_osc_fix:
            # 回退旧行为：输出值相邻 diff 计数，不记事件（None 保留「未迁移」
            # 语义，关闭开关后从种子重建）
            transition_count = sum(
                1 for i in range(1, len(regime_history))
                if regime_history[i][1] != regime_history[i - 1][1]
            )
            events = None
        else:
            events = prev_events
            if prev_state.get("transition_events") is None and not events:
                # 未迁移归档：迁移种子（旧输出相邻 diff）**播种进列表**作
                # 续记真相源——避免切换日整盘丢弃种子致计数一夜归零（审核①）
                events = seed_regime_transition_events(regime_history)
            if switch_confirmed and today_str not in events:
                # 确认切换日记今日事件（幂等：同日重跑不重复 append）
                events = events + [today_str]
            transition_count = sum(
                1 for e in events
                if (window_lo is None or e >= window_lo) and e <= today_str
            )
            # 列表只保留窗口长度，防无限增长（多跑冗余日修剪最早事件）
            max_keep = max(len(regime_history), 1)
            if len(events) > max_keep:
                events = events[-max_keep:]

        return regime.model_copy(update={
            "regime": new_regime,
            "dwell_days": new_dwell,
            "previous_regime": prev_regime,
            "transition_count": transition_count,
            "transition_pending": new_pending,
            "pinned_days": new_pinned,
            # 豁免强切归档标记（R2 复盘区分两日确认 vs 单日强切，审核②）
            "regime_switch_exempt": exempt_applied,
            # 旧行为模式不记事件→None；新行为空列表照落（保留「已迁移」标记）
            "transition_events": events,
        })

    # ── #14 ④ jump penalty 模型（backlog §7 #14 拍板 2026-10-15）─────────────
    # s_t = argmin_s [ |raw − θ_s| + λ·1{s ≠ s_{t-1}} ]
    # 结构性删除两日确认层：切换成本一次性收取，代替「pending 次日再现才确认」。
    # transition 作为一个真实状态参与 argmin（0.0 三义拆态归 OPEN-2，本模型暂以
    # |raw−0|≤θ 处理 transition 邻域）。

    @staticmethod
    def _jump_argmin(raw: float, prev: str, lam: float) -> tuple[str, float]:
        """单步 argmin。返回 (s_t, cost_stay)（cost_stay 供影子观测单调性诊断）。

        θ_transition = 0（三状态对称：|raw−0| 即 transition 邻域距离）。
        tie-break：切换与保持代价相等时保持 s_{t-1}（argmin 取先到者，
        与「不动点优先」的迟滞语义一致，避免等价抖动）。
        """
        costs = {
            "bull": abs(raw - REGIME_JUMP_THETA_BULL),
            "transition": abs(raw - 0.0),
            "bear": abs(raw - REGIME_JUMP_THETA_BEAR),
        }
        costs_prev = costs[prev] if prev in costs else costs["transition"]
        # 保持：无切换成本；切换：+λ
        best_state, best_cost = prev, costs_prev
        for state, cost in costs.items():
            if state == prev:
                continue
            total = cost + lam
            if total < best_cost:  # 严格小于 → tie 保持
                best_state, best_cost = state, total
        return best_state, costs_prev

    @staticmethod
    def _apply_regime_jump_model(regime: MarketRegime) -> MarketRegime:
        """jump 模型（生产态，GOT_REGIME_JUMP_PENALTY=1 时替换状态机）。

        - raw 来自 regime.raw_regime_score（缺失时回退 macro_overlay 解析，再缺失
          则按 regime.regime 的阈值中心近似——构造态兜底，不阻塞）。
        - s_t 序列由 raw 序列整体 argmin 重放（锚点日 = 窗口内首个 raw 有效日，
          s_0 = 其阈值态；影子期归档的 regime 是旧状态机输出，不作 jump 续接点）。
        - transition_count 重定义为 20 日窗 jump 切换计数（s_t ≠ s_{t-1} 的事件日列表）。
        - dwell_days 纯观测（连续保持 s 的天数）；pinned/pending/exempt 整层退役恒空。
        """
        from src.got.persistence import load_regime_jump_history

        raw = regime.raw_regime_score
        if raw is None:
            m = re.search(r"regime_score=(-?\d+\.?\d*)", regime.macro_overlay or "")
            raw = float(m.group(1)) if m else {
                "bull": REGIME_JUMP_THETA_BULL, "transition": 0.0,
                "bear": REGIME_JUMP_THETA_BEAR,
            }.get(regime.regime, 0.0)
        raw = float(raw)

        history = load_regime_jump_history(days=20)

        # 整窗重放：锚点日 = 首个 raw 有效日（s_0 = 阈值态，不收切换成本）；
        # 旧状态机日（raw=None）只以输出 diff 参与事件计数，不续接 s 序列。
        seq_prev = None
        events: list[str] = []
        for row in history:
            if row["raw"] is None:
                if seq_prev is not None and row["regime"] != seq_prev:
                    events.append(row["date"])
                seq_prev = row["regime"]
                continue
            if seq_prev is None:
                seq_prev = AggregatorNode._raw_threshold_state(row["raw"])
                continue
            s_i, _ = AggregatorNode._jump_argmin(
                row["raw"], seq_prev, REGIME_JUMP_LAMBDA,
            )
            if s_i != seq_prev:
                events.append(row["date"])
            seq_prev = s_i

        # 当日：从重放终态续算；无有效历史时冷启动 s_t = raw 阈值态（无切换成本）
        if seq_prev is None:
            s_t = AggregatorNode._raw_threshold_state(raw)
            switch_today = False
        else:
            s_t, cost_stay = AggregatorNode._jump_argmin(
                raw, seq_prev, REGIME_JUMP_LAMBDA,
            )
            switch_today = s_t != seq_prev
        prev = seq_prev if seq_prev is not None else s_t
        today_str = date.today().strftime("%Y%m%d")
        if switch_today and today_str not in events:
            events.append(today_str)

        # dwell：jump 生效段的连续保持天数（从窗口尾部往前数）
        dwell = AggregatorNode._jump_dwell(history, s_t)

        if switch_today:
            logger.info(
                f"[Node 6] Regime jump 切换: {prev} → {s_t}"
                f"（raw={raw:.2f}, λ={REGIME_JUMP_LAMBDA}, 保持代价={cost_stay:.2f}）"
            )

        return regime.model_copy(update={
            "regime": s_t,
            "dwell_days": dwell,
            "previous_regime": prev,
            "transition_count": len(events),
            "transition_pending": None,
            "pinned_days": 0,
            "transition_events": events,
            "regime_switch_exempt": False,
        })

    @staticmethod
    def _raw_threshold_state(raw: float) -> str:
        """raw 阈值态（无记忆基线，与 _compute_market_regime 三段阈值一致）。"""
        if raw > REGIME_JUMP_THETA_BULL:
            return "bull"
        if raw < REGIME_JUMP_THETA_BEAR:
            return "bear"
        return "transition"

    @staticmethod
    def _jump_dwell(history: list[dict], s_t: str) -> int:
        """jump 生效段的连续保持天数：从窗口尾部往前数 raw 有效日，argmin 重放。

        简化口径：对 raw 有效日直接比较阈值态与 s_t（影子期过渡段足够；jump
        生产生效后归档 regime 即 s_t，历史 regime 直接可比，此处保持重放一致性）。
        """
        dwell = 1
        for row in reversed(history):
            if row["raw"] is None:
                break
            if AggregatorNode._raw_threshold_state(row["raw"]) == s_t:
                dwell += 1
            else:
                break
        return dwell

    def _compute_regime_jump_shadow(self, regime: MarketRegime) -> None:
        """jump 模型影子（GOT_REGIME_JUMP_SHADOW=1 缺省）：只算只落盘，零决策改动。

        与生产状态机并行跑同一 raw 输入，落 shadow_regime_jump.json：
        {jump_state, confirmed_state, raw, lambda, agree, switches_20d_jump,
         switches_20d_confirmed, note}。
        转正判据见 docs/got/verification.md 2026-10-15 节（影子 5 交易日）。
        """
        from src.got.persistence import load_regime_jump_history, load_regime_state_history

        raw = regime.raw_regime_score
        if raw is None:
            m = re.search(r"regime_score=(-?\d+\.?\d*)", regime.macro_overlay or "")
            raw = float(m.group(1)) if m else None
        if raw is None:
            logger.info("[Node 6] jump 影子：无 raw 分数（旧归档口径），跳过当日观测")
            return
        raw = float(raw)

        jump_hist = load_regime_jump_history(days=20)

        # 整窗重放（与 _apply_regime_jump_model 同构）
        seq_prev = None
        jump_switches = 0
        for row in jump_hist:
            if row["raw"] is None:
                if seq_prev is not None and row["regime"] != seq_prev:
                    jump_switches += 1
                seq_prev = row["regime"]
                continue
            if seq_prev is None:
                seq_prev = self._raw_threshold_state(row["raw"])
                continue
            s_i, _ = self._jump_argmin(row["raw"], seq_prev, REGIME_JUMP_LAMBDA)
            if s_i != seq_prev:
                jump_switches += 1
            seq_prev = s_i

        # 当日 jump 决策
        if seq_prev is None:
            s_t = self._raw_threshold_state(raw)
            cost_stay = abs(raw - {"bull": REGIME_JUMP_THETA_BULL, "transition": 0.0,
                                   "bear": REGIME_JUMP_THETA_BEAR}[s_t])
            switched_today = False
        else:
            s_t, cost_stay = self._jump_argmin(raw, seq_prev, REGIME_JUMP_LAMBDA)
            switched_today = s_t != seq_prev
        if switched_today:
            jump_switches += 1

        # 对照面：现行状态机 20 日窗确认切换数（transition_events 真相源）
        confirmed_hist = load_regime_state_history(days=20)
        confirmed_switches = sum(
            1 for i in range(1, len(confirmed_hist))
            if confirmed_hist[i][1] != confirmed_hist[i - 1][1]
        )

        record = {
            "date": self._run_date.strftime("%Y%m%d"),
            "raw": round(raw, 4),
            "lambda": REGIME_JUMP_LAMBDA,
            "theta": {"bull": REGIME_JUMP_THETA_BULL, "transition": 0.0,
                      "bear": REGIME_JUMP_THETA_BEAR},
            "jump_state": s_t,
            "jump_prev": seq_prev if seq_prev is not None else s_t,
            "jump_switched": switched_today,
            "confirmed_state": regime.regime,
            "agree": s_t == regime.regime,
            "switches_20d_jump": jump_switches,
            "switches_20d_confirmed": confirmed_switches,
            "cost_stay": round(cost_stay, 4),
            "note": "影子观测：jump 模型并行计算，零决策改动（#14 ④ 影子 5 交易日判据）",
        }
        day_dir = Path("data/got_daily") / self._run_date.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        out = day_dir / "shadow_regime_jump.json"
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(out)
        logger.info(
            "[Node 6] jump 影子: jump=%s confirmed=%s（%s）",
            s_t, regime.regime, "agree" if record["agree"] else "DIVERGE",
        )

    # ── H1 持仓专属情报（2026-10-27，原则②供数）───────────────

    def _build_holdings_intel(
        self,
        sector: SectorContext,
        confirmed: list[SectorIntel],
        holdings,
    ) -> list[SectorIntel]:
        """持仓诉求板块 → 专属 SectorIntel（`sector_role="holding"`）。

        刻意与 `_collect_sector_candidates` 分开（计划原文的「第 5 路」在此落成独立路径）：
        那条链的产物是 `confirmed_sectors`，会参与 TOP-N 名额、选股池、position_limit 与
        埋伏池——把持仓板块塞进去等于用「持仓」反向污染选股通道。持仓诉求只需要
        lifecycle / 强度 / 过热三样事实，故独立成表并恒置 `classification="watch"`。

        数据来源优先级：Node 2 `holdings_sectors`（补拉并评分）> `strength_lookup`
        （已被候选集覆盖但未进 confirmed 的板块，只补强度）。两者皆无 → 跳过（不造数据）。
        """
        wishes: list[str] = []
        for raw_wish in (getattr(holdings, "sector_wishes", None) or []):
            wish = (raw_wish or "").strip()
            if wish and wish not in wishes:
                wishes.append(wish)
        if not wishes:
            return []

        from src.got.board_aliases import normalize_sector_name

        confirmed_names = {s.sector_name for s in (confirmed or []) if s.sector_name}
        confirmed_norm = {normalize_sector_name(n) for n in confirmed_names}

        detail_by_name: dict[str, SectorDetail] = {}
        for detail in (getattr(sector, "holdings_sectors", None) or []):
            if detail and detail.name:
                detail_by_name[detail.name] = detail

        strength_lookup = getattr(sector, "strength_lookup", None) or {}
        results: list[SectorIntel] = []
        for wish in wishes:
            if wish in confirmed_names or normalize_sector_name(wish) in confirmed_norm:
                continue  # 已由 confirmed_sectors 覆盖：不重复、不降级其分类
            detail = detail_by_name.get(wish)
            if detail is None:
                # 候选集里也许有同板块但未进 confirmed（caution/exclude/非 TOP-N）→ 只保留强度
                for name, d in detail_by_name.items():
                    if normalize_sector_name(name) == normalize_sector_name(wish):
                        detail = d
                        break
            if detail is None:
                strength = strength_lookup.get(wish)
                if strength is None:
                    continue
                results.append(SectorIntel(
                    sector_name=wish,
                    sector_type="concept",
                    board_state=None,   # 无 detail：Node 2 未判定该板块状态
                    sector_role="holding",
                    tech_score=round(float(strength), 1),
                    tech_detail=f"strength_lookup={float(strength):.1f}（无 detail，仅强度）",
                    composite_score=round(float(strength), 1),
                    confidence=0.3,
                    classification="watch",
                    reasoning=f"{wish}: 持仓补入（仅强度，无生命周期）",
                ))
                continue
            results.append(SectorIntel(
                sector_name=detail.name,
                sector_code=getattr(detail, "code", "") or "",
                sector_type="concept",
                board_state=getattr(detail, "state", None),
                sector_role="holding",
                tech_score=round(float(detail.strength_score), 1),
                tech_detail=(
                    f"state={getattr(detail, 'state', '')}, "
                    f"lifecycle={detail.lifecycle.phase if detail.lifecycle else 'none'}"
                ),
                lifecycle=detail.lifecycle if detail.lifecycle else None,
                composite_score=round(float(detail.strength_score), 1),
                confidence=0.3,
                classification="watch",
                reasoning=(
                    f"{detail.name}: 持仓补入（不参与主线/埋伏分类），"
                    f"strength={detail.strength_score:.1f}"
                ),
                overheat_severity=getattr(detail, "overheat_severity", None),
                frame_group=getattr(detail, "frame_group", None),
                momentum_state=getattr(detail, "momentum_state", None),
                rs_mom_decay_2d=getattr(detail, "rs_mom_decay_2d", None),
            ))
        return results

    # =========================================================================
    # Level 1: 板块 mini GOT
    # =========================================================================

    def _collect_sector_candidates(
        self,
        sector: SectorContext,
        ambush_pool: Optional[list[AmbushCandidate]] = None,
    ) -> tuple[list[tuple[str, Optional[SectorDetail], dict]], dict[str, str]]:
        """Segment A: 候选板块收集（纯规则，供 _build_sector_intel 与 glue 复用）

        4 路来源，按优先级去重：
        1. 主线板块 — 组名 + 成员展开（组粒度分类用 member_to_group）
        2. 埋伏板块 — Node 2 状态机 ambush 列表
        3. 高位降温 — 前主线回调未破位
        4. 池外补入 — 埋伏正式池中不在 Node 2 名单的板块（蓄水池升格/近失来源）

        Returns:
            (candidates, member_to_group)
            candidates: [(name, detail, role_info), ...]
            member_to_group: {成员名: 组名}
        """
        candidates: list[tuple[str, Optional[SectorDetail], dict]] = []
        seen_names: set[str] = set()
        member_to_group: dict[str, str] = {}

        # 主线板块（组名 + 成员展开，确保反向查询能匹配到任意成员）
        for group in sector.active_mainline:
            best_member = max(group.members, key=lambda m: m.strength_score) if group.members else None
            role_info = {
                "role": "mainline",
                "tier": group.tier,
                "quality": group.mainline_quality,
                "mainline_days": best_member.mainline_days if best_member else None,
            }
            # 组名（最强成员代表）
            candidates.append((group.group_name, best_member, role_info))
            seen_names.add(group.group_name)
            # 展开其他成员（继承组级属性，Phase 2 反向查询可命中）
            for member in group.members:
                if member.name not in seen_names:
                    candidates.append((member.name, member, role_info))
                    seen_names.add(member.name)
                    member_to_group[member.name] = group.group_name

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

        return candidates, member_to_group

    # =========================================================================
    # Agent 语义粘合层（docs/got/archive/agent_glue_plan_0903.md §5.2-5.3 / §7.2）
    # =========================================================================

    @staticmethod
    def _glue_f1_degraded(
        candidates: list[tuple[str, Optional[SectorDetail], dict]],
        reason: str = "",
    ) -> SemanticGlueResult:
        """F1 降级态：三件套全 unknown，composite 退回 tech_score（§7.2 F1-1~F1-6）"""
        strengths = []
        degraded_sectors = []
        for name, detail, role_info in candidates:
            code = getattr(detail, "code", "") or ""
            tech = (detail.strength_score / 100.0) if detail else 0.0
            strengths.append(GlueStrength(
                board_code=code, strength=clip(tech, 0.0, 1.0),
                confidence=0.3, evidence=f"F1 降级{':' + reason if reason else ''}",
            ))
            degraded_sectors.append(name)
        return SemanticGlueResult(
            degraded=True, degraded_sectors=degraded_sectors,
            strengths=strengths,
            warnings=[f"F1 降级: {reason}"] if reason else [],
        )

    @staticmethod
    def _glue_strip_event(ev: CatalystEvent) -> dict:
        """事件去冗余（§5.2）：剥除 4 个可由其他字段派生的字段；附逻辑事件键。"""
        from src.got.board_aliases import normalize_sector_name

        return {
            "event_id": ev.event_id,
            "logical_event_key": f"{ev.source}:{normalize_sector_name(ev.source_news or ev.title)}",
            "event_date": str(ev.event_date),
            "event_type": ev.event_type,
            "title": ev.title,
            "sector": ev.sector,
            "impact_level": ev.impact_level,
            "direction": ev.direction,
            "confidence": ev.confidence,
            "source": ev.source,
            "affected_sectors": ev.affected_sectors,
            "source_news": ev.source_news,
        }

    @staticmethod
    def _build_glue_prompt(
        candidates: list[tuple[str, Optional[SectorDetail], dict]],
        news: NewsContext,
        capital: CapitalContext,
        sentiment: SentimentContext,
        variant: str = "",
        evidence: Optional[list] = None,
    ) -> str:
        """组装 Agent 输入 prompt（§5.2：只喂候选 + 去冗余事件 + 3d 资金流 + 瘦身情绪）

        C-P12 prompt A/B（D-C6）variant 参数：""=现行默认 / "a"=保守版（现行口径+澄清句）/
        "b"=全量归属版（任务1 不得留 unknown + 任务3 全量归属低置信保留，带防幻觉护栏）。
        两版仅改「无把握时的输出策略」一个自由度，输入区块/输出 schema/任务2 完全共用；
        未知取值回退现行默认。仅 replay 调用点会传非空值（生产链路强制 ""）。
        """
        # ── 候选板块（受控词表）──
        sector_lines = []
        for name, detail, role_info in candidates:
            code = getattr(detail, "code", "") or ""
            tier = role_info.get("tier") or "-"
            score = f"{detail.strength_score:.1f}" if detail else "-"
            # B1 口径标注：strength_raw = Node 2 原始技术强度（非综合分）
            sector_lines.append(f"- {code} | {name} | {role_info.get('role', '?')} | tier={tier} | strength_raw={score}")
        sectors_text = "\n".join(sector_lines)

        # ── 事件（去 4 个冗余字段）──
        events = []
        cal = getattr(news, "catalyst_calendar", None)
        if cal and cal.events:
            events = [AggregatorNode._glue_strip_event(ev) for ev in cal.events]

        # ── 重大事件（§5.2 必喂：news.major_events）──
        major_events = []
        for me in (getattr(news, "major_events", None) or []):
            major_events.append({
                # 2026-10-23：给 Node 4 事件稳定 id，让 relations 能按 id 回连（原先无 id，
                # 事件→板块只能走 affected_sectors 名称扇出）
                "event_id": AggregatorNode._major_event_id(me),
                "event_type": getattr(me, "event_type", ""),
                "title": getattr(me, "title", ""),
                "impact_level": getattr(me, "impact_level", ""),
                "sentiment": getattr(me, "sentiment", ""),
                "affected_sectors": list(getattr(me, "affected_sectors", None) or []),
                "lifecycle_state": getattr(me, "lifecycle_state", None),
            })

        # ── 资金流 3d（§5.2：全量嗂，不按位置截断）──
        flow_3d = {}
        sf = capital.evidence.sector_flow if capital and capital.evidence else None
        if sf:
            flow_3d = sf.get("sector_flow_all_3d") or {}

        # ── 情绪热榜（瘦身：去 stock_code 和 associated_sectors）──
        hot_list = []
        for hs in (sentiment.hot_stocks if sentiment else []):
            hot_list.append({
                "stock_name": hs.stock_name,
                "mention_count": hs.mention_count,
                "sentiment": hs.sentiment,
            })

        # ── 情绪全局基调（§5.2 全喂，极小；供 Agent 判 sentiment_read）──
        sentiment_global = {
            "overall_sentiment": getattr(sentiment, "overall_sentiment", ""),
            "sentiment_score": getattr(sentiment, "sentiment_score", 0.0),
            "sentiment_stage": getattr(sentiment, "sentiment_stage", ""),
            "forum_mood": getattr(sentiment, "forum_mood", ""),
            "market_temperature": getattr(sentiment, "market_temperature", 0.5),
            "fear_greed_index": getattr(sentiment, "fear_greed_index", 0.5),
            "news_drift": getattr(sentiment, "news_drift", "neutral"),
            "hot_topics": list(getattr(sentiment, "hot_topics", None) or [])[:20],
        } if sentiment else {}

        # ── 证据索引（运行时注册的 EvidenceRef；输出必须引用这些 ID）──
        evidence_lines = []
        for ref in (evidence or []):
            label = (ref.quote or ref.locator or "")[:140]
            evidence_lines.append(f"- {ref.evidence_id} | {ref.kind} | {ref.locator} | {label}")
        _evidence_text = "\n".join(evidence_lines)

        # ── C-P12 变体文案区块（只替换任务1 的 unknown 行与任务3 整块，其余两版共用）──
        if variant == "b":
            t1_unknown_line = (
                '- 每个维度都必须给出 high/medium/low 判据，不得留 "unknown"：\n'
                '  输入中完全没有该维度证据时写 "low"，并在 evidence 中注明「无直接证据，保守取值」'
            )
            t3_block = """### 任务3: relations — 事件→板块归属
**每条有传导链的事件都要给出归属**，不允许整条省略。要求：
- board_code 必须从上方候选列表中选择
- propagation_path 是传导链，每跳是一个实体（公司/产品/技术），终点必须是候选板块
- strength: 事件对该板块的影响强度 (0-1)
- confidence: 你对这个归属判断的置信度 (0-1)
- 把握不足的**用低 confidence（<0.5）表达，不要省略**——省略会让该事件在验收统计中不可见
- **禁止编造**：低 confidence 是指「传导链真实但你不确定其强度/范围」，不是指「可以虚构
  propagation_path」；构造不出「事件→实体→候选板块」真实链条的事件，允许不输出（这是
  本版唯一的合法省略通道，放弃量将作为本版纪律指标统计）
- **去重**：每个 (event_id, board_code) 组合只输出 1 条 relation，选最强的传播路径。禁止同一事件对同一板块输出多条不同 propagation_path"""
        else:
            # ""（现行默认）/ "a"（保守版）共用；a 仅多一条澄清句，消除指令模糊混杂变量
            t1_unknown_line = '- 没有判据的维度必须写 "unknown"（不要写 "neutral"）'
            t3_lead = "### 任务3: relations — 事件→板块归属" + (
                "\n只对**有把握**的事件输出归属判断。要求："
                if variant == "a" else
                "\n对每条有把握的事件，判断它影响哪个**候选板块**。要求："
            )
            t3_block = t3_lead + """
- board_code 必须从上方候选列表中选择
- propagation_path 是传导链，每跳是一个实体（公司/产品/技术），终点必须是候选板块
- strength: 事件对该板块的影响强度 (0-1)
- confidence: 你对这个归属判断的置信度 (0-1)
- evidence_ids: 支撑该归属的输入证据 ID 列表（只能引用证据索引中的 ID；无证据时 confidence 必须 ≤0.5）
- 没把握的事件不要输出（不要编造）""" + (
                "——宁缺毋滥，省略不是错误" if variant == "a" else ""
            ) + """
- **去重**：每个 (event_id, board_code) 组合只输出 1 条 relation，选最强的传播路径。禁止同一事件对同一板块输出多条不同 propagation_path"""

        prompt = f"""你是一个 A 股板块语义分析引擎。请根据以下输入数据完成三个任务。

## 候选板块（受控词表 — 你只能使用这些板块代码）
{sectors_text}

## 催化事件（{len(events)} 条）
{json.dumps(events, ensure_ascii=False, default=str)}

## 重大事件（{len(major_events)} 条）
{json.dumps(major_events, ensure_ascii=False, default=str)}

> 归属口径（任务3）：**催化事件与重大事件都可以归属**，relations.event_id 必须原样引用
> 上方给出的 event_id；重大事件的 id 以 `major_` 开头，未给出 id 的条目不得编造归属。

## 资金流 3d 日均（单位：亿元，全量 {len(flow_3d)} 板块）
{json.dumps(flow_3d, ensure_ascii=False, default=str)}

## 情绪全局基调
{json.dumps(sentiment_global, ensure_ascii=False, default=str)}

## 情绪热榜（{len(hot_list)} 只）
{json.dumps(hot_list[:50], ensure_ascii=False)}

## 证据索引（输出 evidence_ids 只能引用这些 ID）
{_evidence_text or "（无）"}
> 规则：relations/strengths 的 evidence_ids 必须来自上方证据索引；没有证据支撑的判断
> 不得给高 confidence。引用不存在的 ID 会让整次结构化输出判为失败。

## 任务

### 任务1: strengths — 板块强度评估
对每个候选板块给出综合强度评估。要求：
- strength: 综合强度 (0-1)。**此字段替代的是综合评分（技术面+资金面+消息面+情绪面）**，不是只替代技术面。0=极弱，1=极强
- capital_read: 资金面判据 ("high"/"medium"/"low"/"unknown")。引用资金流数据时标注窗口（1d/3d/5d），例如"3d 净流入"
- news_read: 消息面判据 ("high"/"medium"/"low"/"unknown")
- sentiment_read: 情绪面判据 ("high"/"medium"/"low"/"unknown")
- confidence: 你对这个评估的置信度 (0-1)
- evidence: 一句话说明判断依据
- evidence_ids: 支撑该判断的输入证据 ID 列表（只能引用证据索引中的 ID）
{t1_unknown_line}

### 任务2: families — 家族归属（仅对落「其他」的板块）
以下家族名称是受控词表，你只能从中选择：
{' / '.join(PROMPT_EXPOSED_FAMILIES)}
如果某板块不属于任何家族，family 写 "无归属"。

{t3_block}

### 任务4: 共享语义产物（供 Node 4/5 回填，不改变任何评分）
- narrative: 一段话总结板块格局（50 字内）
- concept_suggestions: 从事件/新闻中发现的新概念（最多 5 条），每条 {{"concept_name","evidence","mention_frequency"}}
- key_risks: 消息面关键风险（最多 5 条，纯文本）
- hot_topics: 2-3 个热门话题关键词
- cross_sector_risks: 跨板块风险（最多 5 条，纯文本）

## 输出格式（严格 JSON）
```json
{{
  "evidence_ids": ["ev_..."],
  "strengths": [
    {{"board_code": "...", "strength": 0.7, "confidence": 0.8, "evidence": "...", "capital_read": "medium", "news_read": "high", "sentiment_read": "low", "evidence_ids": ["ev_..."]}}
  ],
  "families": [
    {{"board_code": "...", "family": "AI软件"}}
  ],
  "relations": [
    {{"event_id": "...", "board_code": "...", "strength": 0.8, "direction": "positive", "propagation_path": ["实体A", "实体B"], "confidence": 0.9, "evidence_ids": ["ev_..."]}}
  ],
  "concept_suggestions": [{{"concept_name": "...", "evidence": "...", "mention_frequency": 3}}],
  "key_risks": ["..."],
  "hot_topics": ["..."],
  "cross_sector_risks": ["..."],
  "narrative": "..."
}}
```
只输出 JSON，不要其他内容。"""
        return prompt

    @staticmethod
    def _parse_glue_response(raw: str) -> dict:
        """解析 Agent 输出的 JSON（处理 ```json fence + 截断抢救）。"""
        import re
        # 1. Strip ```json fence
        cleaned = raw.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        
        # 2. Try normal parse
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        
        # 3. Truncation rescue: find last complete object, truncate, close brackets
        # Find the last '}' that closes a complete object
        last_brace = cleaned.rfind("}")
        if last_brace > 0:
            truncated = cleaned[:last_brace + 1]
            # Try to close any open arrays/objects
            open_brackets = truncated.count("[") - truncated.count("]")
            open_braces = truncated.count("{") - truncated.count("}")
            truncated += "]" * open_brackets + "}" * open_braces
            try:
                return json.loads(truncated)
            except json.JSONDecodeError:
                pass
        
        # 4. Fallback: extract any JSON object
        m = re.search(r'\{[\s\S]*\}', raw)
        if not m:
            raise ValueError("无 JSON 对象")
        return json.loads(m.group())

    

    def _validate_glue(
        self,
        parsed: dict,
        candidates: list[tuple[str, Optional[SectorDetail], dict]],
        allowed_evidence_ids: Optional[set[str]] = None,
    ) -> SemanticGlueResult:
        """校验 Agent 输出并构造 SemanticGlueResult（§7.2 S3-S5 + 证据闭包）。"""
        code_to_name = {}
        for name, detail, role_info in candidates:
            code = getattr(detail, "code", "") or ""
            if code:
                code_to_name[code] = name

        warnings: list[str] = []
        controlled_set = set(CONTROLLED_FAMILIES) | {"无归属"}

        # 容错转换：LLM 可能返回 null / 字符串 / 百分制，裸 float() 会抛异常击穿 F1 降级
        def _f(v, d=0.0):
            try:
                return float(v)
            except (TypeError, ValueError):
                return d

        def _path(v):
            if isinstance(v, list):
                return [str(x) for x in v]
            if v is None:
                return []
            return [str(v)]

        def _ids(v) -> list[str]:
            if isinstance(v, list):
                return [str(x) for x in v if str(x).strip()]
            if isinstance(v, str) and v.strip():
                return [v.strip()]
            return []

        bad_ids: set[str] = set()

        def _checked_ids(v) -> list[str]:
            ids = _ids(v)
            if allowed_evidence_ids is not None:
                bad = {eid for eid in ids if eid not in allowed_evidence_ids}
                bad_ids.update(bad)
            return ids

        def _str_list(v, limit: int) -> list[str]:
            if not isinstance(v, list):
                return []
            out: list[str] = []
            for item in v:
                text = str(item).strip()
                if text and text not in out:
                    out.append(text)
                if len(out) >= limit:
                    break
            return out

        # ── relations ──
        relations = []
        seen_relation_keys: set[tuple[str, str]] = set()
        for r in parsed.get("relations", []) or []:
            if not isinstance(r, dict):
                continue
            event_id = str(r.get("event_id", "")).strip()
            board_code = str(r.get("board_code", "")).strip()
            if not event_id or not board_code:
                warnings.append("S2 relation 缺 event_id/board_code，已丢弃")
                continue
            if code_to_name and board_code not in code_to_name:
                warnings.append(f"S2 relation board_code 不在候选: {board_code}，已丢弃")
                continue
            key = (event_id, board_code)
            if key in seen_relation_keys:
                warnings.append(f"S2 relation 重复: {event_id}→{board_code}，保留首条")
                continue
            seen_relation_keys.add(key)
            evidence_ids = _checked_ids(r.get("evidence_ids"))
            path = _path(r.get("propagation_path"))
            confidence = clip(_f(r.get("confidence"), 0.5), 0, 1)
            # 空传播路径/无证据不能作为高置信事实
            if not path or not evidence_ids:
                confidence = min(confidence, 0.5)
            relations.append(GlueRelation(
                event_id=event_id,
                board_code=board_code,
                strength=clip(_f(r.get("strength"), 0.0), 0, 1),
                direction=str(r.get("direction", "positive")),
                propagation_path=path,
                confidence=confidence,
                evidence_ids=evidence_ids,
            ))

        # ── strengths ──
        strengths = []
        seen_codes = set()
        for s in parsed.get("strengths", []) or []:
            if not isinstance(s, dict):
                continue
            code = str(s.get("board_code", ""))
            raw_strength = _f(s.get("strength"), 0.0)
            clipped = clip(raw_strength, 0, 1)
            if abs(raw_strength - clipped) > 1e-6:
                warnings.append(f"S5 strength 越界: {code}={raw_strength:.2f} → clip → {clipped:.2f}")
            seen_codes.add(code)
            strengths.append(GlueStrength(
                board_code=code,
                strength=clipped,
                confidence=clip(_f(s.get("confidence"), 0.5), 0, 1),
                evidence=str(s.get("evidence", "")),
                capital_read=str(s.get("capital_read", "unknown")),
                news_read=str(s.get("news_read", "unknown")),
                sentiment_read=str(s.get("sentiment_read", "unknown")),
                evidence_ids=_checked_ids(s.get("evidence_ids")),
            ))

        # S3: 覆盖不全检测
        all_codes = {getattr(d, "code", "") for _, d, _ in candidates if d}
        missing_codes = all_codes - seen_codes - {""}
        degraded_sectors = [code_to_name.get(c, c) for c in missing_codes]
        if missing_codes:
            warnings.append(f"S3 覆盖不全: {len(missing_codes)} 板块缺失 → 逐板块 F1 ({', '.join(degraded_sectors)})")
            # 补充缺失板块的 F1 降级条目
            for name, detail, _ in candidates:
                code = getattr(detail, "code", "") or ""
                if code in missing_codes:
                    tech = (detail.strength_score / 100.0) if detail else 0.0
                    strengths.append(GlueStrength(
                        board_code=code, strength=clip(tech, 0, 1),
                        confidence=0.3, evidence="F1 降级: Agent 未覆盖",
                        capital_read="unknown", news_read="unknown", sentiment_read="unknown",
                    ))

        # ── families ──
        families = []
        for f in parsed.get("families", []) or []:
            if not isinstance(f, dict):
                continue
            family_name = str(f.get("family", ""))
            code = str(f.get("board_code", ""))
            # S4: 家族名越界检测
            if family_name not in controlled_set:
                warnings.append(f"S4 家族名越界: {code_to_name.get(code, code)} → '{family_name}' 走兜底链")
                continue
            families.append(GlueFamily(board_code=code, family=family_name))

        # 证据闭包：伪造/未知 ID 必须让结构化步骤失败，不能静默降级
        top_evidence_ids = _checked_ids(parsed.get("evidence_ids"))
        if bad_ids:
            raise ValueError(
                f"证据闭包校验失败: 未知 evidence_id {sorted(bad_ids)[:5]}"
            )

        # ── A6 共享语义产物（Node 4/5 回填源）──
        concept_suggestions = []
        raw_concepts = parsed.get("concept_suggestions")
        if isinstance(raw_concepts, list):
            for c in raw_concepts:
                if not isinstance(c, dict):
                    continue
                name = str(c.get("concept_name", "")).strip()
                if not name:
                    continue
                concept_suggestions.append({
                    "concept_name": name,
                    "evidence": str(c.get("evidence", "")),
                    "mention_frequency": int(_f(c.get("mention_frequency"), 1)),
                })
                if len(concept_suggestions) >= 8:
                    break

        key_risks = _str_list(parsed.get("key_risks"), 8)
        cross_sector_risks = _str_list(parsed.get("cross_sector_risks"), 8)
        hot_topics = _str_list(parsed.get("hot_topics"), 10)
        narrative = str(parsed.get("narrative", "") or "").strip()

        return SemanticGlueResult(
            relations=relations, strengths=strengths, families=families,
            evidence_ids=top_evidence_ids,
            concept_suggestions=concept_suggestions,
            key_risks=key_risks[:8],
            hot_topics=hot_topics,
            narrative=narrative,
            cross_sector_risks=cross_sector_risks,
            degraded=bool(missing_codes),
            degraded_sectors=degraded_sectors,
            warnings=warnings,
        )

    def _collect_pipeline_events(self, news: NewsContext) -> list[dict]:
        """C-P13 管线事件集：catalyst 事件（_glue_strip_event 去冗余）+ major_events 合成条目。

        与 base prompt 的事件输入同源（任务3 只消费 catalyst 事件，但 major_events 同样
        可驱动板块归属，管线一并纳入）；合成 event_id 前缀 "major_" 防与真实事件冲突。
        """
        events: list[dict] = []
        cal = getattr(news, "catalyst_calendar", None)
        if cal and cal.events:
            events.extend(self._glue_strip_event(ev) for ev in cal.events)
        for me in (getattr(news, "major_events", None) or []):
            events.append({
                # 与 _build_glue_prompt / _build_node4_glue_index 共用同一稳定 id
                "event_id": self._major_event_id(me),
                "event_type": getattr(me, "event_type", ""),
                "title": getattr(me, "title", ""),
                "impact_level": getattr(me, "impact_level", ""),
                "sentiment": getattr(me, "sentiment", ""),
                "affected_sectors": list(getattr(me, "affected_sectors", None) or []),
                "source_news": "",
            })
        return events

    def _ensure_runtime(self):
        """Lazily create the run runtime when a node is used outside the orchestrator."""
        from src.got.agent_runtime import ensure_runtime

        return ensure_runtime(self, getattr(self, "_run_date", None))

    def _register_glue_evidence(
        self,
        candidates: list[tuple[str, Optional[SectorDetail], dict]],
        news: NewsContext,
        capital: CapitalContext,
        sentiment: SentimentContext,
    ) -> list:
        """Register A6 input evidence and return refs for prompt + closure checks."""
        runtime = self._ensure_runtime()
        refs = []

        cal = getattr(news, "catalyst_calendar", None)
        for ev in (cal.events if cal else [])[:60]:
            refs.append(runtime.register_evidence(
                kind="event",
                source=f"catalyst:{ev.source}",
                locator=ev.event_id,
                content=self._glue_strip_event(ev),
                quote=ev.title,
            ))
        for me in (getattr(news, "major_events", None) or [])[:30]:
            refs.append(runtime.register_evidence(
                kind="news",
                source="node4",
                locator=self._major_event_id(me),
                content={
                    "title": getattr(me, "title", ""),
                    "summary": getattr(me, "summary", ""),
                    "impact_level": getattr(me, "impact_level", ""),
                    "sentiment": getattr(me, "sentiment", ""),
                    "affected_sectors": list(getattr(me, "affected_sectors", None) or []),
                },
                quote=getattr(me, "title", ""),
            ))

        sf = capital.evidence.sector_flow if capital and capital.evidence else None
        flow_3d = (sf or {}).get("sector_flow_all_3d") or {}
        refs.append(runtime.register_evidence(
            kind="capital",
            source="node3",
            locator="capital.sector_flow_all_3d",
            content=flow_3d,
            quote=f"institutional_score={getattr(capital, 'institutional_score', 0.0)}",
        ))

        refs.append(runtime.register_evidence(
            kind="sentiment",
            source="node5",
            locator="sentiment.global",
            content={
                "overall_sentiment": getattr(sentiment, "overall_sentiment", ""),
                "sentiment_score": getattr(sentiment, "sentiment_score", 0.0),
                "sentiment_stage": getattr(sentiment, "sentiment_stage", ""),
                "fear_greed_index": getattr(sentiment, "fear_greed_index", 0.5),
                "hot_topics": list(getattr(sentiment, "hot_topics", None) or [])[:20],
            },
            quote=(getattr(sentiment, "reasoning", "") or "")[:120],
        ))

        for name, detail, role_info in candidates:
            code = getattr(detail, "code", "") or ""
            if not code:
                continue
            refs.append(runtime.register_evidence(
                kind="market",
                source="node2",
                locator=f"board:{code}",
                content={
                    "name": name,
                    "role": role_info.get("role"),
                    "tier": role_info.get("tier"),
                    "strength_raw": getattr(detail, "strength_score", None),
                },
                quote=name,
            ))

        # 窄身份 Agent 产物只作为证据进入 A6（不得改 sector_role/classification/仓位）
        narrative_ctx = getattr(self, "_narrative_context", None)
        for item in (getattr(narrative_ctx, "identities", None) or [])[:40]:
            symbol = getattr(item, "symbol", "")
            theme = getattr(item, "theme", "")
            if not symbol or not theme:
                continue
            refs.append(runtime.register_evidence(
                kind="memory",
                source="narrative_identity",
                locator=f"identity:{symbol}:{theme}",
                content={
                    "symbol": symbol,
                    "theme": theme,
                    "layer": getattr(item, "layer", "other"),
                    "confidence": getattr(item, "confidence", 0.0),
                    "abstain": getattr(item, "abstain", False),
                },
                quote=theme,
            ))
        return refs

    def _apply_glue_relations_to_calendar(
        self, news: NewsContext, glue: SemanticGlueResult,
        candidates: list[tuple[str, Optional[SectorDetail], dict]], *, replay: bool,
    ) -> None:
        """A6 relations → 催化剂日历扇出（唯一一次，不再调用 LLM）。"""
        from src.got.glue_relations import apply_glue_relations

        stats = apply_glue_relations(news, glue, candidates, replay=replay)
        if stats.get("updated") or stats.get("unmapped_relations") or stats.get("db_missing_row"):
            logger.info(
                "[Node 6] A6 relations 扇出回填: updated=%d, unmapped=%d, kept=%d, db=%d, missing_row=%d",
                stats.get("updated", 0), stats.get("unmapped_relations", 0),
                stats.get("kept_existing", 0), stats.get("db_persisted", 0),
                stats.get("db_missing_row", 0),
            )

    def _backfill_semantic_aggregates(
        self, news: NewsContext, sentiment: SentimentContext, glue: SemanticGlueResult,
    ) -> None:
        """把 A6 共享产物回填 Node 4/5（下游只读，不再各自调用 LLM）。"""
        if getattr(glue, "concept_suggestions", None):
            news.concept_suggestions = list(glue.concept_suggestions)
        if getattr(glue, "key_risks", None):
            news.key_risks = list(glue.key_risks)
        if getattr(glue, "hot_topics", None):
            sentiment.hot_topics = list(glue.hot_topics)

        # Node 4 reasoning 是规则文本，回填后重算以保持与 concepts/risks 一致
        try:
            from src.got.nodes.news_analysis import NewsAnalysisNode

            news.reasoning = NewsAnalysisNode._fallback_reasoning(
                news.overall_impact, news.impact_score, news.major_events,
                news.concept_suggestions, news.key_risks,
            )
        except Exception as exc:  # noqa: BLE001 — 展示文本回填失败不影响决策
            logger.warning("[Node 6] Node 4 reasoning 回填失败（已忽略）: %s", exc)

    async def _agent_semantic_glue(
        self,
        candidates: list[tuple[str, Optional[SectorDetail], dict]],
        news: NewsContext,
        capital: CapitalContext,
        sentiment: SentimentContext,
    ) -> SemanticGlueResult:
        """Agent 语义粘合层（docs/got/archive/agent_glue_plan_0903.md §5.2-5.3 / §7.2）

        单次全量 LLM 调用，输出三类结构化结果：
        - relations: 事件→板块关系图（替代 T5 映射）
        - strengths: 板块强度/confidence/evidence（替代 Segment C/D/E）
        - families: 「其他」板块的家族归属（受控词表）

        F1 降级触发场景：S1-S7（§7.2）。降级态不影响现有行为（A4 阶段）。

        D-P14: 删除死参数 regime（从未进 prompt；如需接入须先重启影子基线）。
        """
        # S6: 开关关闭 → 报错（glue 已转正，不允许关闭）
        if not AGENT_SEMANTIC_GLUE_ENABLED:
            raise RuntimeError("[Node 6] AGENT_SEMANTIC_GLUE_ENABLED=false，glue 已转正，不允许关闭")

        # C-P3: replay_mode 默认仍跳过 glue（保持 0903 A1「不产生任何持久化副作用」契约）
        # 开启 AGENT_SEMANTIC_GLUE_IN_REPLAY=true 后允许回填影子样本（D-C4）
        if getattr(self, '_replay_mode', False) and not AGENT_SEMANTIC_GLUE_IN_REPLAY:
            logger.info("[Node 6] replay_mode: 跳过 _agent_semantic_glue（设 AGENT_SEMANTIC_GLUE_IN_REPLAY=true 可启用）")
            result = self._glue_f1_degraded(candidates, reason="replay_mode")
            result.status = "replay_mode"
            self._save_glue_result(result)
            return result

        if not candidates:
            result = SemanticGlueResult(degraded=True, status="no_candidates")
            self._save_glue_result(result)
            return result

        # ── 组装 prompt ──
        # C-P12: 变体文案仅在 replay 生效（GLUE_PROMPT_VARIANT=a|b，catalyst_config 读取），
        # 生产链路强制现行默认 ""——守 D-C6「拿到曲线前不改生产 prompt」；未知取值回退默认。
        # C-P13 阶段1: GLUE_PIPELINE_ENABLED 同样仅 replay 生效（双保险门控同 C-P12）；
        # 开启时 base 调用仍用现行默认 prompt 产 strengths/families，relations 由接地管线替换，
        # 留痕 prompt_variant="p"（与 a/b 互斥，管线优先）。
        pipeline_on = GLUE_PIPELINE_ENABLED and getattr(self, '_replay_mode', False)
        prompt_variant = (
            "p" if pipeline_on else
            (GLUE_PROMPT_VARIANT if (GLUE_PROMPT_VARIANT in ("a", "b") and getattr(self, '_replay_mode', False)) else "")
        )
        # ── 证据注册（A6 是唯一决策语义入口，输出必须可回溯）──
        evidence_refs = self._register_glue_evidence(candidates, news, capital, sentiment)

        prompt = self._build_glue_prompt(
            candidates, news, capital, sentiment,
            variant=("" if pipeline_on else prompt_variant),
            evidence=evidence_refs,
        )

        # ── LLM 调用（C1: temperature=0，D9 甲: 单次全量）──
        # max_tokens=GLUE_MAX_TOKENS（默认 16000）: 周末事件堆积时 8000 不够（0921 截断导致 S2_parse_failed）；
        # C-P12 配套：B 版全量归属输出更大（0902 撞顶），回放日可用 env 临时调高，默认值不变=生产零影响
        # max_retries=3: 仅对网络错误/空响应重试；finish_reason="length" 立即失败（Q1 audit）
        try:
            raw = await self._call_llm_async(
                prompt,
                temperature=0.0,
                max_tokens=GLUE_MAX_TOKENS,
                max_retries=3,
                call_type="node6.semantic_glue",
                timeout=float(LLM_CALL_TIMEOUT_S),
                evidence_refs=evidence_refs,
            )
        except Exception as e:
            # S1: 调用失败
            logger.warning(f"[Node 6] glue LLM 调用失败，F1 降级: {e}")
            result = self._glue_f1_degraded(candidates, reason=f"S1 调用失败: {e}")
            result.status = "S1_call_failed"
            result.prompt_variant = prompt_variant
            self._save_glue_result(result)
            return result

        if not raw or not raw.strip():
            # S1: 空响应
            logger.warning("[Node 6] glue LLM 返回空，F1 降级")
            result = self._glue_f1_degraded(candidates, reason="S1 空响应")
            result.status = "S1_empty"
            result.prompt_variant = prompt_variant
            self._save_glue_result(result)
            return result

        # ── 解析 JSON ──
        try:
            parsed = self._parse_glue_response(raw)
        except (json.JSONDecodeError, ValueError) as e:
            # S2: 解析失败
            logger.warning(f"[Node 6] glue JSON 解析失败，F1 降级: {e}")
            result = self._glue_f1_degraded(candidates, reason=f"S2 解析失败")
            result.raw_response = raw
            result.status = "S2_parse_failed"
            result.prompt_variant = prompt_variant
            self._save_glue_result(result)
            return result

        # ── 校验 + 构造结果 ──
        # Critical#2: _validate_glue 内部若因畸形结构抛异常，须走 F1 降级而非击穿整个 Node 6
        try:
            result = self._validate_glue(
                parsed, candidates,
                allowed_evidence_ids={ref.evidence_id for ref in evidence_refs},
            )
        except Exception as e:
            logger.warning(f"[Node 6] glue 校验异常，F1 降级: {e}")
            if "证据闭包" in str(e):
                try:
                    _step = self._ensure_runtime().get("node6.semantic_glue")
                    if _step is not None:
                        _step.status = "failed"
                        _step.error = f"evidence closure: {e}"
                except Exception:  # noqa: BLE001
                    pass
            result = self._glue_f1_degraded(candidates, reason=f"S2 校验失败: {e}")
            result.raw_response = raw
            result.status = "S2_validate_failed"
            result.prompt_variant = prompt_variant
            self._save_glue_result(result, prompt_bytes=len(prompt.encode("utf-8")))
            return result
        result.raw_response = raw
        result.status = "ok"
        # C-P12 留痕：成功/失败态均记录本次调用实际使用的变体文案（生产恒为空）
        result.prompt_variant = prompt_variant

        # ── C-P13 阶段1：接地管线替换 relations（仅 replay + 开关；任何失败保留 base relations）──
        pipeline_trace: Optional[dict] = None
        if pipeline_on:
            pipeline_events = self._collect_pipeline_events(news)
            pipe = await _glue_pipeline_mod.run_relations_pipeline(
                self, candidates, pipeline_events,
                getattr(self, '_run_date', None) or date.today(),
            )
            pipeline_trace = pipe.trace
            result.pipeline_status = pipe.status
            if pipe.status == "ok":
                result.relations = [GlueRelation(**r) for r in pipe.relations]
                logger.info(
                    f"[Node 6] C-P13 管线替换 relations: {len(result.relations)} 条"
                    f"（事件 {len(pipeline_events)} 个）"
                )
            else:
                result.warnings.append(
                    f"C-P13 管线未替换 relations（status={pipe.status}），保留 base 产出"
                )
                logger.warning(f"[Node 6] C-P13 管线降级: status={pipe.status}")
            for w in pipe.warnings:
                result.warnings.append(w)
                logger.warning(f"[Node 6] C-P13 管线: {w}")

        for w in result.warnings:
            logger.warning(f"[Node 6] glue 校验: {w}")

        # ── C2: 结果落盘 ──
        self._save_glue_result(result, prompt_bytes=len(prompt.encode("utf-8")),
                               pipeline_trace=pipeline_trace)

        # ── 影子模式日志（A5：glue 照跑、结果只归档不生效）──
        if AGENT_SEMANTIC_GLUE_SHADOW:
            logger.info(
                "[Node 6] 影子模式: glue 结果已归档但不影响 _build_sector_intel 行为"
            )
            # C-P4: _log_shadow_comparison 已删除（对照数据磁盘上已有，D-C7）

        logger.info(
            f"[Node 6] glue 完成: {len(result.relations)} 关系, "
            f"{len(result.strengths)} 强度, {len(result.families)} 家族"
            + (
                f", degraded={len(result.degraded_sectors)} ({', '.join(result.degraded_sectors[:10])})"
                if result.degraded else ""
            )
            + f"{' [SHADOW]' if AGENT_SEMANTIC_GLUE_SHADOW else ''}"
        )

        return result

    def _save_glue_result(
        self,
        result: SemanticGlueResult,
        prompt_bytes: Optional[int] = None,
        pipeline_trace: Optional[dict] = None,
    ) -> None:
        """C2: glue 结果落盘 → data/got_daily/{date}/agent_glue.json

        replay_mode 下写入 _replay/ 子目录，避免污染真实归档（A1「不写归档」契约）。
        C-P2: 使用 _run_date 替代 date.today()，回放时日期正确。
        C-P12: 环境变量 GLUE_REPLAY_VARIANT（仅 replay_mode 生效）→ 落 _replay/<variant>/，
               使 A/B 两版 prompt 的回放产物各自隔离，不再同写 {date}/_replay/agent_glue.json 互相覆盖；
               默认空 = 现路径（不改变 C-P11 单跑回填行为）。
        C-P13: pipeline_trace 非空且 replay 时，额外落同目录 glue_pipeline_trace.json
               （L1 top-K / L2 逐事件 / L3 剪枝 / 护栏全链路留痕）。

        Q1 audit: status != "ok" 时写入 agent_glue_degraded_HHMMSS_毫秒.json，
        不覆盖主文件（保留现场供事后分析）。
        """
        import os
        from datetime import datetime
        replay = getattr(self, '_replay_mode', False)
        # C-P2: 优先使用 _run_date（回放模式），否则 date.today()
        run_date = getattr(self, '_run_date', None) or date.today()
        today_str = run_date.strftime("%Y%m%d")
        base = GLUE_ARCHIVE_ROOT / today_str / ("_replay" if replay else "")
        # C-P12: 变体目录隔离——A/B prompt 变体回放各落 _replay/<variant>/，避免互相覆盖。
        # 仅 replay_mode 生效、默认空；生产链路（非 replay）永远不受该变量影响，守住 A1 契约。
        variant = os.environ.get("GLUE_REPLAY_VARIANT", "").strip().strip("/\\") if replay else ""
        if variant:
            base = base / variant
        os.makedirs(base, exist_ok=True)
        
        # Q1 audit: LLM 失败态（含截断）写独立取证文件 agent_glue_degraded_HHMMSS.json，
        # 不覆盖主文件；良性跳过态（replay_mode/no_candidates）仍写主文件，
        # 避免下游消费方（verify_glue_metrics、影子报告 _render_shadow_a5）对非失败态误报缺失
        _FORENSICS_STATUSES = {
            "S1_call_failed", "S1_empty", "S2_parse_failed", "S2_validate_failed", "S3_truncated",
        }
        if result.status in _FORENSICS_STATUSES:
            # Q1 audit: 毫秒精度后缀，避免同秒两次失败互相覆盖取证样本
            timestamp = datetime.now().strftime("%H%M%S_%f")[:-3]
            path = base / f"agent_glue_degraded_{timestamp}.json"
        else:
            path = base / "agent_glue.json"
        
        data = result.model_dump()
        # C-P1: 仅在降级态（status != "ok"）时保留 raw_response 供复盘
        if result.status != "ok":
            data["raw_response"] = result.raw_response
        else:
            data.pop("raw_response", None)
        if prompt_bytes is not None:
            data["prompt_bytes"] = prompt_bytes
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        size = path.stat().st_size
        if size > 90 * 1024:
            logger.warning(f"[Node 6] glue 结果偏大: {path} ({size:,} B > 90KB)")
        else:
            logger.info(f"[Node 6] glue 结果已写入: {path} ({size:,} B)")

        # C-P13: 管线 trace 落盘（仅 replay；与 agent_glue.json 同目录，随变体隔离）
        if pipeline_trace is not None and replay:
            trace_path = base / "glue_pipeline_trace.json"
            trace_payload = dict(pipeline_trace)
            trace_payload["pipeline_status"] = result.pipeline_status
            with open(trace_path, "w", encoding="utf-8") as f:
                json.dump(trace_payload, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"[Node 6] C-P13 管线 trace 已写入: {trace_path}")

    def _build_sector_intel(
        self,
        sector: SectorContext,
        capital: CapitalContext,
        news: NewsContext,
        sentiment: SentimentContext,
        ambush_pool: Optional[list[AmbushCandidate]] = None,
        ambush_reservoir: Optional[list[AmbushReservoirEntry]] = None,
        glue: Optional[SemanticGlueResult] = None,
        candidates: Optional[list[tuple[str, Optional[SectorDetail], dict]]] = None,
        member_to_group: Optional[dict] = None,
    ) -> list[SectorIntel]:
        """对每个候选板块做 4 维 mini GOT，标记主线/埋伏来源

        分类拆分消费：主线/降温板块走 composite TOP-N（Node 2 尺子），
        埋伏板块的 classification 由埋伏通道准入结果直接映射
        （入正式池 → actionable，否则 caution），不再与主线争抢 TOP-N 名额。

        glue: Agent 语义粘合层输出（A4 阶段接受但暂不使用，A6 正式消费）。
        """

        # Segment A: 候选收集（纯规则，抽出为独立方法供 glue 复用）
        # Suggestion#18: run() 已为 glue 收集过候选，此处直接复用避免二次扫描
        if candidates is None:
            candidates, member_to_group = self._collect_sector_candidates(sector, ambush_pool)
        if not candidates:
            return []

        # ── Pass 1: 计算所有板块的技术面 composite（规则三件套已随 A6 阶段 2 退役）──
        # 不创建 SectorIntel，先收集原始数据用于批量分类
        raw_data = []  # [(name, composite, capital_check, news_check, detail, sector_role, ...)]
        for name, detail, role_info in candidates:
            sector_role = role_info.get("role", "mainline")
            composite, capital_check, news_check, other = self._compute_sector_scores(
                name, detail, role_info,
            )
            raw_data.append((name, composite, capital_check, news_check, detail, sector_role, role_info, other))

        # ── Pass 1.5 (A6 接线): glue 直接替换三件套输出 ──
        # glue 已由 run() 入口守卫保证 status=='ok' 且非 degraded
        glue_hit_names: set[str] = set()
        raw_data = self._apply_glue_override_to_raw_data(raw_data, glue)
        glue_hit_names = {row[0] for row in raw_data if row[-1].get("_glue_applied")}

        # ── Pass 2: 批量分类（主线/降温走 TOP-N，埋伏按通道结果拆分）──
        # 埋伏板块不走 composite TOP-N：其准入由埋伏通道的闸门+B公式排序决定，
        # composite（Node 2 尺子）对低位埋伏板块天然偏见，不能当埋伏的尺子
        classify_input = [
            (name, composite, capital_check, news_check, detail, sector_role)
            for name, composite, capital_check, news_check, detail, sector_role, _, _ in raw_data
            if sector_role != "ambush"
        ]
        classification_map = self._classify_all_sectors(
            classify_input, top_n=12, member_to_group=member_to_group,
            overheat_lookup=sector.overheat_lookup,
        )

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
                overheat_lookup=sector.overheat_lookup,
                frame_group_lookup=sector.frame_group_lookup,
            )
            if other.get("_glue_applied"):
                # A6 接线可观测性：标记 SectorIntel 字段来源为 glue，保留证据链
                evidence = other.get("_glue_evidence", "")
                intel.reasoning = (
                    f"{intel.reasoning} [A6-glue {other.get('_glue_strength_source', '')}"
                    + (f" · {evidence[:60]}" if evidence else "")
                    + "]"
                )
            results.append(intel)

        # 按 composite_score 降序排列
        results.sort(key=lambda x: x.composite_score, reverse=True)

        return results

    # ── A6 接线：glue 词汇映射 + 直接替换助手 ──
    # 将 glue 侧 {high, medium, low, unknown} 映射到 SectorIntel 侧
    # {aligned/divergent/neutral}、{catalyzed/negative/neutral}、{high/low/missing}。
    # 与退役设计一致：直接替换，不做加权融合。
    _GLUE_CAPITAL_READ_MAP = {
        "high": "aligned",
        "medium": "neutral",
        "low": "divergent",
        "unknown": "neutral",
    }
    _GLUE_NEWS_READ_MAP = {
        "high": "catalyzed",
        "medium": "neutral",
        "low": "neutral",   # glue 的 low 不直接等价负面，中性处理
        "unknown": "neutral",
    }
    _GLUE_SENTIMENT_READ_MAP = {
        "high": "high",
        "medium": "low",
        "low": "low",
        "unknown": "missing",
    }

    @classmethod
    def _glue_map_capital_read(cls, read: str) -> str:
        return cls._GLUE_CAPITAL_READ_MAP.get((read or "unknown").lower(), "neutral")

    @classmethod
    def _glue_map_news_read(cls, read: str) -> str:
        return cls._GLUE_NEWS_READ_MAP.get((read or "unknown").lower(), "neutral")

    @classmethod
    def _glue_map_sentiment_read(cls, read: str) -> str:
        return cls._GLUE_SENTIMENT_READ_MAP.get((read or "unknown").lower(), "missing")

    def _apply_glue_override_to_raw_data(
        self,
        raw_data: list[tuple],
        glue: SemanticGlueResult,
    ) -> list[tuple]:
        """Pass 1.5：用 glue.strengths 直接替换规则侧输出。

        对每个候选板块：
          1. 按 name 或 detail.code 查找 GlueStrength（先 name 后 code）
          2. 命中且不在 glue.degraded_sectors 中 ⇒ 重写
             composite/confidence/capital_check/news_check/sentiment_check，
             在 other 中留痕 _glue_applied=True，供 Pass 3 标注 reasoning
          3. 未命中 / 降级 ⇒ 保留技术面中性垫底（规则三件套已随 A6 阶段 2 退役，
             capital_check/news_check=neutral、sentiment_check=missing）
        glue.status != 'ok' 时（replay degraded）直接原样返回，不做替换。
        """
        if not GOT_A6_ENABLED or glue is None or glue.status != "ok":
            return raw_data
        # 建 name → GlueStrength 与 code → GlueStrength 双向索引
        by_name: dict[str, GlueStrength] = {}
        by_code: dict[str, GlueStrength] = {}
        for st in glue.strengths or []:
            if st.board_code:
                by_code[st.board_code] = st
        # strengths 只带 board_code；用 candidates 的 detail.name 反推。但 candidates 不传入
        # 本函数，因此用 SectorDetail.name→code 的约定从 raw_data 里取。
        degraded = set(glue.degraded_sectors or [])
        patched: list[tuple] = []
        for row in raw_data:
            (name, composite, capital_check, news_check, detail,
             sector_role, role_info, other) = row
            code = getattr(detail, "code", "") if detail else ""
            st = by_code.get(code) if code else None
            if st is None or name in degraded:
                patched.append(row)
                continue
            # 直接替换：glue.strength 0-1 → composite 0-100（C-P9 basis=composite 口径）
            new_composite = float(st.strength) * 100.0
            new_capital = self._glue_map_capital_read(st.capital_read)
            new_news = self._glue_map_news_read(st.news_read)
            new_sentiment = self._glue_map_sentiment_read(st.sentiment_read)
            new_confidence = float(st.confidence)
            other = dict(other)
            # A6 阶段 2：规则评分模式已退役（实施顺序第 5 步）。
            # capital_detail 原为规则侧原文，现直接以 glue 证据覆写；
            # Q8-A1 的 rule_capital_check/rule_capital_detail 留存随之移除。
            other["capital_detail"] = f"[glue] {st.evidence or st.capital_read}"
            other["news_detail"] = f"[glue] {st.news_read}"
            other["sentiment_check"] = new_sentiment
            other["sentiment_detail"] = f"[glue] {st.sentiment_read}"
            other["confidence"] = new_confidence
            other["_glue_applied"] = True
            other["_glue_evidence"] = st.evidence or ""
            other["_glue_strength_source"] = (
                f"capital={st.capital_read}/news={st.news_read}/sent={st.sentiment_read}"
            )
            patched.append((
                name, new_composite, new_capital, new_news, detail,
                sector_role, role_info, other,
            ))
            by_name[name] = st  # 预留：目前未用，供后续需求反查
        # 一次性 INFO，便于日志内快速定位 A6 生效日
        applied = sum(1 for row in patched if row[-1].get("_glue_applied"))
        if applied:
            logger.info(
                f"[Node 6][A6] glue 直接替换: {applied}/{len(patched)} 个板块的三件套输出"
            )
        return patched

    @staticmethod
    def _glue_aware_events_map(
        self_events_group: callable,
        events: list,
        glue: SemanticGlueResult,
        code_to_name: dict,
    ) -> dict:
        """A6 接线：将 glue.relations 翻译为 {sector_name: list[CatalystEvent]} 形态。

        命中 glue.relations 的事件用 board_code→sector_name 反查（需 code_to_name）；
        glue 未覆盖的事件回退 legacy T5 扇出，保证事件不丢。
        glue.relations 为空时，完全回退 legacy。
        """
        if not (GOT_A6_ENABLED and glue.relations and code_to_name):
            return self_events_group(events)
        event_by_id = {
            getattr(e, "event_id", None): e
            for e in events
            if getattr(e, "event_id", None)
        }
        grouped: dict[str, list] = {}
        used_ids: set[str] = set()
        for rel in glue.relations:
            ev = event_by_id.get(rel.event_id)
            if ev is None:
                continue
            name = code_to_name.get(rel.board_code)
            if name is None:
                continue
            grouped.setdefault(name, []).append(ev)
            used_ids.add(rel.event_id)
        uncovered = [
            e for e in events
            if getattr(e, "event_id", None) not in used_ids
        ]
        if uncovered:
            legacy = self_events_group(uncovered)
            for k, v in legacy.items():
                grouped.setdefault(k, []).extend(v)
        return grouped

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
        role_info: Optional[dict] = None,
    ) -> tuple[float, str, str, dict]:
        """计算板块的技术面综合分（A6 阶段 2：规则评分模式已退役）

        跨维校验三件套（资金/消息/情绪）已随 glue 转正删除：命中板块的三态判定与
        composite 由 Pass 1.5 用 glue 输出直接覆写；本函数只为「glue 漏命中/降级」的
        板块垫一个技术面中性分（放弃逐板块规则兜底，负责人 2026-09-26 裁决）。

        Returns:
            (composite_score, capital_check, news_check, other_data_dict)
            capital_check/news_check 恒为 "neutral"，情绪态在 other 中置 "missing"。
        """
        role_info = role_info or {"role": "mainline", "tier": None, "quality": None, "mainline_days": None}
        sector_role = role_info.get("role", "mainline")

        # ── 技术面（来自 Node 2）──
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

        # ── 综合评分：技术面 + Lifecycle 修正（跨维修正随规则模式退役移除）──
        # 注：命中板块的 composite 会在 Pass 1.5 被 glue 输出（strength×100）整体覆写，
        #     本函数值仅对 glue 漏命中/降级板块生效。
        composite = tech_score
        if detail and detail.lifecycle:
            phase = detail.lifecycle.phase
            if phase in ("launch", "acceleration"):
                composite += 8  # 早期阶段加分
            elif phase in ("late_stage", "mature_exhausted"):
                composite -= 15  # 晚期阶段减分
        composite = clip(composite, 0, 100)

        # 置信度：仅基于 Node 2 数据完整度（跨维信号加成随规则模式退役移除，
        # glue 命中板块的 confidence 由 Pass 1.5 覆写）
        confidence = clip(0.5 + (0.2 if detail else 0.0), 0.0, 1.0)

        other = {
            "tech_score": tech_score,
            "tech_detail": tech_detail,
            "capital_detail": "规则模式已退役",
            "news_detail": "规则模式已退役",
            "sentiment_check": "missing",
            "sentiment_detail": "规则模式已退役",
            "confidence": confidence,
            "sector_role": sector_role,
        }

        return composite, "neutral", "neutral", other

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
        overheat_lookup: Optional[dict[str, float]] = None,
        frame_group_lookup: Optional[dict[str, str]] = None,
    ) -> SectorIntel:
        """从预计算的评分创建 SectorIntel（分类已由批量分类器决定）"""
        reasoning = f"{name}: {classification}, score={composite:.0f}, {other['tech_detail']}"

        # 过热降权链路透传（Part 1）：从 SectorDetail 取 overheat_severity + frame_group；
        # detail 缺失或为合成最小 detail（回退链）时，用 Node 2 全量 lookup 兜底，
        # 保证升格/池外埋伏板块的降权链路不静默失效（overheat_lookup 覆盖全量 analyzed）
        overheat_severity = None
        frame_group = None
        if detail:
            overheat_severity = getattr(detail, "overheat_severity", None)
            frame_group = getattr(detail, "frame_group", None)
        if overheat_severity is None and overheat_lookup:
            overheat_severity = overheat_lookup.get(name)
        if frame_group is None and frame_group_lookup:
            frame_group = frame_group_lookup.get(name)

        return SectorIntel(
            sector_name=name,
            sector_code=detail.code if detail else "",
            sector_type="concept",
            board_state=getattr(detail, "state", None) if detail else None,
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
            overheat_severity=overheat_severity,
            frame_group=frame_group,
            momentum_state=getattr(detail, "momentum_state", None) if detail else None,
            rs_mom_decay_2d=getattr(detail, "rs_mom_decay_2d", None) if detail else None,
        )

    def _classify_all_sectors(
        self,
        candidates: list[tuple[str, float, str, str, Optional[SectorDetail], str]],
        top_n: int = 12,
        member_to_group: Optional[dict[str, str]] = None,
        overheat_lookup: Optional[dict[str, float]] = None,
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

        member_to_group = member_to_group or {}
        result = {}

        # ── Pass 1: 标记硬拒绝（不可被 TOP-N 覆盖）──
        for name, composite, capital_check, news_check, detail, sector_role in candidates:
            if self._is_hard_exclude(composite, capital_check, news_check, detail):
                result[name] = "exclude"

        # ── Pass 2: 按综合分排名，TOP-N → actionable（组粒度竞争名额）──
        # 主线组成员展开仅为反向查询服务，不参与名额竞争：
        # 同族多成员若各自占名额会挤压其他家族的 actionable 配额（2026-09-01）
        # ── 波次生命周期折扣（v3.5 S3：纠正动量偏见）──
        # 排序键改为 adjusted = composite × wave_coeff(wave_days)
        eligible = sorted(
            [(name, score, detail) for name, score, *_rest, detail, _role in candidates
             if name not in result and name not in member_to_group],
            key=lambda x: x[1] * _wave_coeff(getattr(x[2], "wave_days", None) if x[2] else None),
            reverse=True,
        )

        # ── 主线组拥挤判定（2026-09-01）──
        # 组的热度本质是成员热度的扩散，扩散成员死得比过热还快：
        # 同组过热成员（含组代表自身）≥3 判拥挤（口径参考 Node 2 crowded_families 的 ≥3/>0.3）。
        # 注意不用 Node 2 crowded_families：其家族含"[其他]"这类百员杂物家族，
        # 家族级拦截会伤及无关干净板块；组级判定只碰主线组自身。
        # 拥挤组整组拦截、名额让渡，其成员全部继承 caution 不进选股池。
        hot_count: dict[str, int] = {}
        for name, _composite, _capital, _news, detail, _role in candidates:
            grp = member_to_group.get(name) or name  # 无组成员的候选（组代表自身）计入自己的组
            if self._resolve_overheat(name, detail, overheat_lookup) > OVERHEAT_ADMISSION_LIMIT:
                hot_count[grp] = hot_count.get(grp, 0) + 1
        crowded_groups = {g for g, n in hot_count.items() if n >= 3}
        for g in sorted(crowded_groups):
            logger.info(f"[Node 6] 主线组拥挤拦截: {g}（过热成员 {hot_count[g]} 个，整组不得入池）")

        actionable_groups = set()
        actionable_count = 0
        for name, score, _detail in eligible:
            if actionable_count < top_n and score >= 50:
                # 过热准入闸门：自身过热或组内拥挤的板块不当主线（名额让渡给下一位候选），
                # 其组成员也因此继承 caution，不进 Phase 2 选股池（2026-09-01）
                if (
                    self._resolve_overheat(name, _detail, overheat_lookup) > OVERHEAT_ADMISSION_LIMIT
                    or name in crowded_groups
                ):
                    result[name] = "caution"
                    continue
                result[name] = "actionable"
                actionable_groups.add(name)
                actionable_count += 1
            else:
                result[name] = "caution"

        # 组成员继承组分类（不占名额）；硬拒绝成员已在 Pass 1 独立判定，不会走到这里。
        # 过热过滤：过热成员不得继承（不进 Phase 2 池，避免高位接票），
        # 其余成员照常继承——非上限截断，不影响池子广度（2026-09-01）
        for name, _composite, _capital, _news, detail, _role in candidates:
            if name in result:
                continue
            grp = member_to_group.get(name)
            if (
                grp in actionable_groups
                and self._resolve_overheat(name, detail, overheat_lookup) <= OVERHEAT_ADMISSION_LIMIT
            ):
                result[name] = "actionable"
            else:
                result[name] = "caution"

        return result

    @staticmethod
    def _resolve_overheat(
        name: str,
        detail: Optional[SectorDetail],
        overheat_lookup: Optional[dict[str, float]] = None,
    ) -> float:
        """解析板块过热严重度（0~1）：detail 优先，缺失时查 Node 2 全量 lookup"""
        sev = getattr(detail, "overheat_severity", None) if detail else None
        if sev is None and overheat_lookup:
            sev = overheat_lookup.get(name)
        return sev or 0.0

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

    

    def _deterministic_catalyst_promotion(
        self,
        sectors: list[SectorIntel],
        ambush_pool: list,
        events_by_sector: dict,
    ) -> int:
        """确定性提升兜底规则（LLM 概率上调之外的硬规则保障）

        对象：sector_role == "ambush" 且 classification == "caution" 的 SectorIntel
        条件 A（合格催化）：命中事件 confidence >= CATALYST_CONFIDENCE_GATE_LOW(0.6)
                           且 direction != "negative" 且 impact_level >= 2
                           （2026-10-23 与升格门/准入闸对齐：10-22 回测 0.6/0.65/0.7 三档
                           已兑现方向正确率 0.40/0.42/0.40、平均收益 +0.10/+0.15/+0.24%，
                           Wilson95 全重叠 → 保留 0.7 无统计依据）
        条件 B（证据，二选一）：
          - 该板块在 ambush_pool 中且 accum_strength ∈ {strong, medium, weak} 或 nominated=True
          - 不在池中时 capital_check == "aligned"
        两条件同时满足 → classification = "actionable"

        Returns:
            int: 被提升的板块数量
        """
        ambush_pool_names = {c.sector_name for c in (ambush_pool or [])}
        ambush_pool_map = {c.sector_name: c for c in (ambush_pool or [])}
        promoted_count = 0

        for s in sectors:
            # 只处理埋伏 + caution
            if s.sector_role != "ambush" or s.classification != "caution":
                continue

            # 条件 A：合格催化事件
            events = self._lookup_sector_events(s.sector_name, events_by_sector)
            qualified_events = [
                e for e in events
                if e.confidence >= CATALYST_CONFIDENCE_GATE_LOW
                and e.direction != "negative"
                and e.impact_level >= 2
            ]
            if not qualified_events:
                continue  # 条件 A 不满足

            # 条件 B：吸筹证据
            in_pool = s.sector_name in ambush_pool_names
            has_evidence = False
            promoted_via_freeze = False
            if in_pool:
                pool_entry = ambush_pool_map[s.sector_name]
                accum_strength = getattr(pool_entry, "accum_strength", "")
                nominated = getattr(pool_entry, "nominated", False)
                if accum_strength in ("strong", "medium", "weak") or nominated:
                    has_evidence = True
                elif AMBUSH_G4_FREEZE and accum_strength == "missing":
                    # 工作流 A：冻结期无资金准据的 missing 也可满足条件 B（条件 A 合格催化不动）
                    has_evidence = True
                    promoted_via_freeze = True
            else:
                if s.capital_check == "aligned":
                    has_evidence = True

            if not has_evidence:
                continue  # 条件 B 不满足

            # 双条件满足 → 提升
            s.classification = "actionable"
            event_titles = "; ".join(e.title[:30] for e in qualified_events[:2])
            s.reasoning += f" [确定性提升: 日历催化×吸筹证据] {event_titles}"
            if promoted_via_freeze:
                s.reasoning += " [冻结期提升-无资金准据]"  # 可审计：标记经由 G4 冻结放行的提升
            promoted_count += 1
            logger.info(
                f"[Node 6] 确定性提升: {s.sector_name} caution→actionable "
                f"(催化{len(qualified_events)}条, 池内={in_pool})"
            )

        return promoted_count

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
        freeze_missing: bool = False,
    ) -> tuple[bool, str, str, str, dict]:
        """4 层闸门流水线（★★★利空 + 防买顶 + 拥挤度 + 吸筹确认）。

        D-P5 闸门诚实化：其中 G2 注意力闸 / G3 拥挤度黑名单因 `associated_sectors`
        无数据源而未启用（恒返回通过）。实际活着 2 个半（G1 利空 + G4 吸筹 + G2 已发酵子闸）。

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
                # 工作流 A：G4 资金闸门冻结——missing（无资金流证据，非市场否决）视同过闸。
                # 只对当日候选 opt-in（升格支路传 False）；坏信号（吸筹观察/净流出）不走此分支。
                if freeze_missing:
                    return True, "", "G4冻结放行(无资金流证据)", accum_strength, evidence
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

        # 找最近一个有 unified.json 的日期目录（A-P0-1 统一入口）
        from src.got.archive import list_archive_dirs
        date_dirs = list_archive_dirs(reverse=True, before=date.today())
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
    ) -> tuple[list[AmbushReservoirEntry], list[dict]]:
        """蓄水池跨日合并：今日新入 + 前日留存 → 合并列表。

        - 今日在 + 前日在：天数 = 前日天数 + 1，保留入池日
        - 今日在 + 前日不在：天数 = 0（新入池）
        - 今日不在 + 前日在：
          - 若板块已不在今日近失名单且驻留 ≥3 天 → 除名（"不再是近失"=最强退出信号）
          - 否则天数 +1，若 ≥ RESERVOIR_EXPIRY_DAYS → 除名
        - 末尾容量上限：超出 RESERVOIR_MAX_SIZE 时按 days_in_reservoir 降序驱逐

        D-P2（2026-09-11）：返回 (merged_list, overflow_info)，overflow_info 记录
        被容量驱逐的条目（板块名/驻留天数/驱逐原因），供 unified.json 落盘。

        Q3（audit_20260922）：新增当日新入上限（超出按入池强度降序截断，不落盘驱逐记录），
        驱逐保护改为「新入保护 ∪ 升格临近(days==PROMOTION_MIN)」单 or 条件（防未来校准后死分支）。
        """
        from src.got.catalyst_config import (
            RESERVOIR_MAX_SIZE,
            RESERVOIR_DAILY_NEW_ENTRY_MAX,
            RESERVOIR_NEW_ENTRY_PROTECTION_DAYS,
            RESERVOIR_PROMOTION_MIN_DAYS,
        )

        merged: dict[str, AmbushReservoirEntry] = {}
        overflow_info: list[dict] = []  # D-P2: 容量驱逐记录

        # Q3: 当日新入上限——日均新入 9-16 ≫ 容量 15，任何驱逐方向都会被新入冲垮；
        # 超限按入池强度降序保留，被截断的新入不写驱逐记录（未入池非驱逐），仅日志留痕
        prev_names = set(prev_reservoir_map)
        today_new = [e for e in today_reservoir if e.sector_name not in prev_names]
        if len(today_new) > RESERVOIR_DAILY_NEW_ENTRY_MAX:
            today_new.sort(key=lambda e: e.entered_strength, reverse=True)
            dropped_names = {
                e.sector_name for e in today_new[RESERVOIR_DAILY_NEW_ENTRY_MAX:]
            }
            today_reservoir = [
                e for e in today_reservoir if e.sector_name not in dropped_names
            ]
            logger.info(
                f"[Node 6] 蓄水池当日新入上限: {len(today_new)} → "
                f"{RESERVOIR_DAILY_NEW_ENTRY_MAX}，截断强度靠后的新入 "
                f"{len(dropped_names)} 条 ({', '.join(sorted(dropped_names))})"
            )

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

        # Q3-A audit: 容量上限驱逐逻辑（新入保护 + 升格临近豁免）
        if len(merged) > RESERVOIR_MAX_SIZE:
            # 标记受保护条目。单 or 条件而非 if/elif：当前取值下 days==PROMOTION_MIN(2)
            # 已被 days<PROTECTION(3) 覆盖，但两常量未来一同校准，若出现
            # PROTECTION ≤ PROMOTION_MIN 则 elif 式写法会变死分支（audit 审核 S13）
            protected = set()
            for name, entry in merged.items():
                days = entry.days_in_reservoir
                if (
                    days < RESERVOIR_NEW_ENTRY_PROTECTION_DAYS
                    or days == RESERVOIR_PROMOTION_MIN_DAYS
                ):
                    protected.add(name)
            
            # 只驱逐未受保护的条目
            unprotected = [e for e in merged.values() if e.sector_name not in protected]
            unprotected.sort(key=lambda e: e.days_in_reservoir, reverse=True)
            
            overflow_count = len(merged) - RESERVOIR_MAX_SIZE
            if len(unprotected) >= overflow_count:
                # 有足够的未保护条目可驱逐
                evicted = unprotected[:overflow_count]
            else:
                # 兜底：全部受保护时，强制驱逐最老的（防死锁）
                all_sorted = sorted(merged.values(), key=lambda e: e.days_in_reservoir, reverse=True)
                evicted = all_sorted[:overflow_count]
                logger.warning(
                    f"[Node 6] 蓄水池全池受保护，强制驱逐最老 {len(evicted)} 条"
                )
            
            evicted_names = {e.sector_name for e in evicted}
            merged = {k: v for k, v in merged.items() if k not in evicted_names}

            # D-P2: 记录被驱逐的条目
            for e in evicted:
                overflow_info.append({
                    "sector": e.sector_name,
                    "days_in_reservoir": e.days_in_reservoir,
                    "reason": "容量驱逐",
                    "was_protected": e.sector_name in protected,
                })

            logger.info(
                f"[Node 6] 蓄水池容量驱逐: {len(evicted)} 条 "
                f"(上限 {RESERVOIR_MAX_SIZE}, 受保护 {len(protected)} 条, "
                f"被驱逐: {', '.join(evicted_names)})"
            )

        return list(merged.values()), overflow_info

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
        all_events = self._exclude_macro_regime_ref(all_events)
        events_by_sector = self._group_events_by_sector(all_events)
        # relations 补挂（与今日候选支路同源）：优先复用本日 run 缓存的索引（当日 glue），
        # 独立调用时（单测/回放）自行载入前日归档回退；开关关→空索引→恒 no-op
        # W1 修正：回退载入须锚定 _run_date（回放不得读"相对真实今天"的归档，防前视）
        rel_index = getattr(self, "_rel_idx_cache", None)
        if rel_index is None:
            rel_index = (
                self._load_prev_glue_relations_index(
                    before=getattr(self, "_run_date", None) or date.today())
                if AMBUSH_RELATIONS_WIRING else {}
            )
        # Q2 audit: 补传 sector_ctx，蓄水池升格的情绪面验证同样走 THS 热度口径
        hot_sectors = self._extract_hot_sectors(sentiment_ctx, sector_ctx)

        # A2 可观测性：蓄水池板块的催化事件覆盖率（含 relations 补挂，与实际查表同口径）
        # W2 修正：覆盖观测用一次性 stats，避免与下方条件 2 的补挂调用双重计数
        _covered_probe_stats = {k: 0 for k in _DISCARD_STATS}
        covered = sum(
            1 for e in reservoir_entries
            if self._supplement_from_relations(
                self._lookup_sector_events(e.sector_name, events_by_sector),
                e.sector_code, rel_index, all_events, _covered_probe_stats,
            )
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

            # 条件 2: 有合格催化剂（A2: 归一化查找；落空时 relations 补挂，
            # 升格支路计数不进主漏斗，命中经日志留痕）
            sector_events = self._lookup_sector_events(name, events_by_sector)
            sector_events = self._supplement_from_relations(
                sector_events, entry.sector_code, rel_index, all_events, _DISCARD_STATS,
            )
            # 2026-10-23：升格门与准入闸对齐到 GATE_LOW(0.6)——10-22 回测三档
            # （0.6/0.65/0.7）已兑现收益无统计差异，0.7 硬门只砍量不筛质。
            # 原「晋升影子（0.60 会放行吗）」区块在两档相等后恒空，随之删除。
            qualified_events = [
                e for e in sector_events
                if e.confidence >= CATALYST_CONFIDENCE_GATE_LOW
                and e.direction != "negative"
            ]
            if not qualified_events:
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
            # Warning #7：升格支路显式 freeze_missing=False——冻结只对当日候选生效，
            # 否则蓄水池存量 data_missing 条目会在冻结首日一次性升格涌入、挤占正式池名额
            gate_passed, fail_reason, accum_detail, accum_strength, _ = self._run_ambush_gates(
                name, sector_events, capital_ctx, hot_sectors, today, freeze_missing=False,
            )

            if gate_passed:
                score, top_event, category_tag, event_titles = self._compute_catalyst_score(
                    qualified_events, today,
                )
                # v2.1: 吸筹档位乘数（共用模块级常量 AMBUSH_ACCUM_MULTIPLIER，防与另一处各写各的）
                accum_multiplier = AMBUSH_ACCUM_MULTIPLIER.get(accum_strength, 1.0)
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

        # S3 可观测出口：升格支路的补挂计数不进主漏斗，至少在 run 末尾留一次快照
        # （进程内累加，重启归零；与候选支路的 relations_supplement 分开读数）
        if AMBUSH_RELATIONS_WIRING and _DISCARD_STATS["legacy_miss_total"]:
            logger.info(f"[Node 6] 升格支路 relations 补挂快照: {_DISCARD_STATS}")

        return promoted_pool, remaining
    def _compute_ambush_exit_grace_shadow(
        self, sector: SectorContext, news: NewsContext
    ) -> None:
        """埋伏出场催化宽限影子（2026-10-16 RCA，零决策改动观测线）

        E14 动量转负/低分出场是纯技术判据，催化剂日历从不参与。本影子只观测：
        当日发生「前日 ambush → 今日 neutral」的 E14 转出时，日历若存在该板块
        N 交易日内合格催化事件，记 grace_would_hold（假使宽限转正，该板块当日本会
        保留在 ambush）。零决策改动——状态机/埋伏池/蓄水池全部不变。

        谓词源（计划 §2.2b）：前日 state 用 DB node2_sector_trends（状态机写入真相源）；
        当日 E14 判据用当日 DB 行（rrg_momentum_ema / strength_score，0-100 口径，
        与 _apply_state_machine 同源落库）。落 shadow_ambush_exit_grace.json。
        """
        from src.storage import DatabaseManager, Node2SectorTrendRecord

        today = self._run_date
        prev_rows: list = []
        today_rows: list = []
        try:
            db = DatabaseManager()
            with db.get_session() as session:
                # 前日行：limit(200) 覆盖近 2-3 日（单日约 80-90 板块），按 code 去重取最新
                prev_rows = (
                    session.query(Node2SectorTrendRecord)
                    .filter(Node2SectorTrendRecord.tracking_date < today)
                    .order_by(Node2SectorTrendRecord.tracking_date.desc())
                    .limit(200)
                    .all()
                )
                today_rows = (
                    session.query(Node2SectorTrendRecord)
                    .filter(Node2SectorTrendRecord.tracking_date == today)
                    .all()
                )
        except Exception as exc:  # noqa: BLE001 — DB 缺表/空库：warning + 空样本落盘（漏记不误记）
            logger.warning("[Node 6] 出场宽限影子 DB 读取失败（按空样本落盘）: %s", exc)

        prev_states: dict[str, tuple] = {}
        for row in prev_rows:
            if row.sector_code not in prev_states:
                prev_states[row.sector_code] = (
                    row.tracking_date, row.state, int(row.dwell_days or 0),
                )
        today_by_code = {row.sector_code: row for row in today_rows}

        calendar = getattr(news, "catalyst_calendar", None)
        # 影子观测路径：刻意不排除 macro_rule（保持影子读数与历史可比，2026-10-21）
        events_by_sector = self._group_events_by_sector(calendar.events if calendar else [])

        candidates: list[dict] = []
        # E14 当日转出识别：前日 ambush + 今日 neutral + E14 判据复验 + 排 E12b 飞刀。
        # 前日 dwell>=2 不复验——「前日 ambush→今日 neutral」且非飞刀已唯一指向 E14
        # （E16 起点 defensive_rotation 非 ambush；E13 结果是 active_mainline 非 neutral）。
        for code, row in today_by_code.items():
            prev = prev_states.get(code)
            if not prev or prev[1] != "ambush":
                continue
            if row.state != "neutral":
                continue
            mom = float(row.rrg_momentum_ema or 0.0)
            strength = float(row.strength_score or 0.0)
            if mom < 0.2 and strength < 25:
                continue  # E12b 飞刀硬退出，不属宽限对象（D7）
            if not (mom < 0.5 or strength < 50):
                continue  # E14 判据不复匹配（防御：当日行被其他路径改写时）

            sector_name = (row.sector_name or "").strip()
            sector_events = (
                self._lookup_sector_events(sector_name, events_by_sector)
                if sector_name else []
            )
            # 与埋伏池路径同款 0.7 预滤 + 负面排除
            qualified = [
                e for e in sector_events
                if e.confidence >= CATALYST_CONFIDENCE_GATE and e.direction != "negative"
            ]
            nearest = None
            grace_would_hold = False
            for e in qualified:
                td = _count_trade_days(today, e.event_date)
                if not 1 <= td <= AMBUSH_EXIT_GRACE_WINDOW_DAYS:
                    continue  # past 事件（≤0）与超窗（>10）自然排除
                grace_would_hold = True
                if nearest is None or td < nearest["trade_days_away"]:
                    nearest = {
                        "event_id": e.event_id,
                        "title": e.title,
                        "event_date": e.event_date.isoformat(),
                        "confidence": float(e.confidence),
                        "direction": e.direction,
                        "trade_days_away": td,
                    }
            candidates.append({
                "sector_code": code,
                "sector_name": sector_name,
                "prev_state": prev[1],
                "prev_dwell": prev[2],
                "today_state": row.state,
                "rrg_momentum_ema": mom,
                "strength_score": strength,
                "e14_only": True,
                "qualified_event_count": len(qualified),
                "nearest_event": nearest,
                "grace_would_hold": grace_would_hold,
            })

        record = {
            "date": today.strftime("%Y%m%d"),
            "switch": True,
            "window_days": AMBUSH_EXIT_GRACE_WINDOW_DAYS,
            "grace_candidates_count": len(candidates),
            "records": candidates,
            "note": "影子观测：E14 出场催化宽限，零决策改动（状态机/池/蓄水池不变，"
                    "≥10 交易日转正判据见 docs/got/verification.md 补登 #6）",
        }
        day_dir = Path("data/got_daily") / today.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        out = day_dir / "shadow_ambush_exit_grace.json"
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(out)
        logger.info(
            "[Node 6] 出场宽限影子: %d 候选, %d grace_would_hold",
            len(candidates),
            sum(1 for c in candidates if c["grace_would_hold"]),
        )

    @staticmethod
    def _build_ambush_lane_validation(
        ambush_pool: list[AmbushCandidate],
        ambush_reservoir: list[AmbushReservoirEntry],
        sector: SectorContext,
    ) -> Optional[list[dict]]:
        """F1 埋伏道一致性校验（2026-10-15，影子观测）

        三规则交叉验证，只产提案不改 ambush_pool/ambush_reservoir 本体：
        1. stagnation_ambush: ambush_pool 候选板块同时出现在 cooling_down 或 industry_top
        2. catalyst_expiry_watch: ambush_reservoir 条目 days_in_reservoir >= 7 (0.7×10)
        3. family_divergence: reservoir 条目家族与主线 strength 榜前 3 家族分歧

        Returns:
            list[dict] 或 None（开关关闭时）
        """
        if not GOT_AMBUSH_VALIDATION_SHADOW:
            return None

        proposals = []

        # 规则 1: stagnation_ambush — ambush_pool 候选板块同时出现在 cooling_down 或 industry_top
        # 注意：cooling_down/industry_top 元素是 SectorDetail，字段名是 name（不是 sector_name）；
        # ambush_pool（AmbushCandidate）/ambush_reservoir（AmbushReservoirEntry）才有 sector_name
        cooling_names = {s.name for s in getattr(sector, "cooling_down", [])}
        industry_names = {s.name for s in getattr(sector, "industry_top", [])}
        stagnant_names = cooling_names | industry_names

        for candidate in ambush_pool:
            if candidate.sector_name in stagnant_names:
                proposals.append({
                    "rule": "stagnation_ambush",
                    "sector": candidate.sector_name,
                    "detail": {
                        "in_cooling": candidate.sector_name in cooling_names,
                        "in_industry_top": candidate.sector_name in industry_names,
                    },
                    "proposal": "建议移出埋伏池进观察道",
                    "severity": "medium",
                })

        # 规则 2: catalyst_expiry_watch — ambush_reservoir 条目驻留天数接近过期
        for entry in ambush_reservoir:
            days = getattr(entry, "days_in_reservoir", 0)
            if days >= 7:  # 0.7 × RESERVOIR_EXPIRY_DAYS (10)
                proposals.append({
                    "rule": "catalyst_expiry_watch",
                    "sector": entry.sector_name,
                    "detail": {
                        "days_in_reservoir": days,
                        "expiry_days": RESERVOIR_EXPIRY_DAYS,
                    },
                    "proposal": "蓄水池驻留接近过期，建议升格或除名",
                    "severity": "high",
                })
            elif days >= 5:  # 早期预警
                proposals.append({
                    "rule": "catalyst_expiry_watch",
                    "sector": entry.sector_name,
                    "detail": {
                        "days_in_reservoir": days,
                        "expiry_days": RESERVOIR_EXPIRY_DAYS,
                    },
                    "proposal": "蓄水池驻留进入预警区间",
                    "severity": "low",
                })

        # 规则 3: family_divergence — reservoir 条目家族与主线 strength 榜前 3 家族分歧
        # 从 sector.theme_families 取主线家族榜（按 family_score 排序）
        theme_families = getattr(sector, "theme_families", [])
        if theme_families and ambush_reservoir:
            # 主线 strength 榜前 3 家族名称
            sorted_families = sorted(theme_families, key=lambda f: getattr(f, "family_score", 0), reverse=True)
            top3_family_names = {getattr(f, "family_name", "") for f in sorted_families[:3]}

            # reservoir 条目强度中位数（用 entered_strength 或 strength_lookup）
            strength_lookup = getattr(sector, "strength_lookup", {})
            reservoir_strengths = [
                strength_lookup.get(e.sector_name, getattr(e, "entered_strength", 0))
                for e in ambush_reservoir
            ]
            median_strength = sorted(reservoir_strengths)[len(reservoir_strengths) // 2] if reservoir_strengths else 0

            # 检查每个 reservoir 条目的家族归属
            for entry in ambush_reservoir:
                entry_strength = strength_lookup.get(entry.sector_name, getattr(entry, "entered_strength", 0))
                # 简单启发式：板块名包含家族关键词（实际应查 member stocks 的家族归属）
                # 此处简化为检查板块名是否与 top3 家族名有子串关系
                for family_name in top3_family_names:
                    if family_name in entry.sector_name or entry.sector_name in family_name:
                        if entry_strength < median_strength:
                            proposals.append({
                                "rule": "family_divergence",
                                "sector": entry.sector_name,
                                "detail": {
                                    "family": family_name,
                                    "entry_strength": entry_strength,
                                    "median_strength": median_strength,
                                },
                                "proposal": "家族在主线强势但该板块强度低于中位数",
                                "severity": "medium",
                            })
                        break  # 匹配到一个家族即可

        return proposals

    async def _build_ambush_channel(
        self,
        sector: SectorContext,
        capital: CapitalContext,
        news: NewsContext,
        sentiment: SentimentContext,
        regime: Optional[MarketRegime] = None,
        node4_glue_index: Optional[dict] = None,
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

        F1 影子校验（2026-10-15）：
            产出 ambush_lane_validation 提案列表（三规则：stagnation_ambush / catalyst_expiry_watch / family_divergence），
            零本体改动——ambush_pool / ambush_reservoir 内容不变。

        Returns:
            (ambush_pool, ambush_reservoir, veto_log)
        """
        # 预提取数据（即使 ambush 为空也处理 near_miss）
        # Q2 audit: G2/G3 闸门修复 — 从 SectorContext.ths_hot_sectors 获取热度数据
        # 替代原 sentiment.hot_stocks[].associated_sectors（恒空导致闸门失效）
        ths_hot_map = getattr(sector, 'ths_hot_sectors', {}) or {}
        hot_sectors = list(ths_hot_map.keys())  # 板块名列表，供 G2/G3 闸门使用
        calendar = getattr(news, "catalyst_calendar", None)
        all_events = calendar.events if calendar else []
        all_events = self._exclude_macro_regime_ref(all_events)
        events_by_sector = self._group_events_by_sector(all_events)
        # 第二支路（2026-10-22）：Node 4 当日事件也进埋伏通道（此前只读日历，
        # 10-21 实测 11 个产业级事件对埋伏零贡献）。开关关闭 → 空索引 → 逐字节回现状。
        node4_index = self._build_node4_event_index(news)
        # 与今日候选支路同形（L217）：锚定 _run_date，回放时 today=回放日
        today = getattr(self, "_run_date", None) or date.today()

        # relations 补挂（A/B 根因修复）：名称查表落空时用 board_code 反查补挂当日日历事件。
        # 索引优先消费 run() 前置写入的当日 in-memory glue（实测：埋伏名单日间轮换，
        # 前日归档覆盖为 0）；独立调用（单测/回放）回退前日归档，before=today 守回放前视
        rel_index = getattr(self, "_rel_idx_cache", None)
        if rel_index is None:
            rel_index = (
                self._load_prev_glue_relations_index(before=today)
                if AMBUSH_RELATIONS_WIRING else {}
            )
            self._rel_idx_cache = rel_index
        rel_supplement_stats = {
            "wiring": "on" if AMBUSH_RELATIONS_WIRING else "off",
            "legacy_miss_total": 0,
            "relations_matched": 0,
            "legacy_miss_and_no_relations_hit": 0,
            "relations_hit_all_missed": 0,
            "supplemented_events": 0,
        }
        
        pool: list[AmbushCandidate] = []
        reservoir: list[AmbushReservoirEntry] = []
        veto_log: list[str] = []
        nomination_queue: list[dict] = []  # 决策 21: 仅被吸筹拦下的候选，待 Agent 批量评审
        missing_count = 0  # 资金流数据缺失候选数（轨道三熔断统计）
        
        # D-P2: 闸门漏斗追踪（初始化）
        # Q2 audit: G2/G3 闸门已修复，使用 THS 板块热度数据
        # D-P2 补全: skipped/skipped_reason 区分「未评估」与「评估后拒绝」——
        # 死闸（无数据源）不再被计 evaluated，与 architecture.md §7.3 的诚实化标注对齐
        gate_funnel = {
            "initial_candidates": 0,
            "G1_利空": {"evaluated": 0, "rejected": 0},
            "G2_防买顶": {"evaluated": 0, "rejected": 0, "skipped": 0, "skipped_reason": None, "data_source": "THS板块热度" if hot_sectors else "无数据"},
            "G3_拥挤度": {"evaluated": 0, "rejected": 0, "skipped": 0, "skipped_reason": None, "data_source": "THS板块热度" if hot_sectors else "无数据"},
            "G4_吸筹": {"evaluated": 0, "rejected": 0, "accepted": 0, "skipped": 0, "skipped_reason": None, "frozen_released": 0},
            "final_pool": 0,
        }

        def _gate_stat(gate_key: str, kind: str, skip_reason: Optional[str] = None) -> None:
            """D-P2: 漏斗计数唯一入口（计数确定、与输入顺序无关，守同日重跑幂等）。"""
            entry = gate_funnel[gate_key]
            entry[kind] += 1
            if kind == "skipped" and entry.get("skipped_reason") != skip_reason:
                entry["skipped_reason"] = skip_reason
        
        if hot_sectors:
            logger.info(f"[Node 6] G2/G3 闸门启用: {len(hot_sectors)} 个 THS 热度板块")

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
            gate_funnel["initial_candidates"] = len(sector.ambush)

            for detail in sector.ambush:
                name = detail.name
                sector_events = self._lookup_sector_events(name, events_by_sector)
                # relations 补挂：仅在名称查表落空时触发（纯增量，不动已命中关系）
                sector_events = self._supplement_from_relations(
                    sector_events, detail.code, rel_index, all_events, rel_supplement_stats,
                )
                # Node 4 事件（第二支路）：优先 glue board_code 归属（2026-10-23），
                # 未覆盖时回退 affected_sectors 名称扇出（保持旧口径兜底）。
                node4_events = list((node4_glue_index or {}).get(detail.code) or [])
                if not node4_events and node4_index:
                    node4_events = self._lookup_sector_events(name, node4_index)

                # D-P2: 闸门漏斗追踪（每个候选都评估 G1）
                gate_funnel["G1_利空"]["evaluated"] += 1

                # 闸门流水线（抽取为独立方法，供升格复用）；工作流 A：当日候选 opt-in 冻结
                gate_passed, fail_reason, accum_detail, accum_strength, evidence = self._run_ambush_gates(
                    name, sector_events, capital, hot_sectors, today,
                    freeze_missing=AMBUSH_G4_FREEZE,
                )

                # D-P2: 根据 fail_reason 追踪闸门漏斗（计数口径见 _gate_stat，
                # 仅「评估过」才计 evaluated；数据缺失的 G4 记未评估留痕）
                if not gate_passed:
                    if "★★★利空否决" in fail_reason:
                        _gate_stat("G1_利空", "rejected")
                    elif "防买顶否决" in fail_reason:
                        _gate_stat("G2_防买顶", "evaluated")
                        _gate_stat("G2_防买顶", "rejected")
                    elif "拥挤度黑名单" in fail_reason:
                        _gate_stat("G3_拥挤度", "evaluated")
                        _gate_stat("G3_拥挤度", "rejected")
                    elif "吸筹观察" in fail_reason:
                        _gate_stat("G4_吸筹", "evaluated")
                        _gate_stat("G4_吸筹", "rejected")
                    elif accum_strength == "missing":
                        # 资金流数据缺失 ≠ 市场否决：G4 未真正评估，记未评估留痕（死闸口径）
                        _gate_stat("G4_吸筹", "skipped", "flow_data_missing")
                    # 继续处理后续逻辑（蓄水池/否决日志等）
                else:
                    # D-P2: 闸门全通过。G2 注意力子闸/G3 依赖 hot_sectors，无数据时结构性
                    # 不可达 ⇒ 只记 skipped 留痕，不再虚计 evaluated（死闸仍被计 evaluated 的残留项）
                    if hot_sectors:
                        _gate_stat("G2_防买顶", "evaluated")
                        _gate_stat("G3_拥挤度", "evaluated")
                    else:
                        _gate_stat("G2_防买顶", "skipped", "data_unavailable(THS热度为空)")
                        _gate_stat("G3_拥挤度", "skipped", "data_unavailable(THS热度为空)")
                    if accum_strength == "missing":
                        # 工作流 A：G4 冻结放行（missing 视同过闸）——不计 evaluated/accepted，
                        # 避免"无数据"虚增为"评估并接受"，单独计 frozen_released
                        _gate_stat("G4_吸筹", "frozen_released")
                    else:
                        _gate_stat("G4_吸筹", "evaluated")
                        _gate_stat("G4_吸筹", "accepted")
                    # 继续处理后续逻辑（催化剂排序等）

                # 以下保持原有逻辑不变
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
                # 2026-10-22：准入降到低档闸（CATALYST_CONFIDENCE_GATE_LOW，缺省 0.6）——
                # 三档阈值（0.6/0.65/0.7）的已兑现方向正确率 0.40/0.42/0.40 Wilson 区间全重叠，
                # 硬闸无区分度只拦截量；质量由 B 公式里的 confidence 乘数保证（0.6 事件自然
                # 排 0.8 之后）。提升/升格 2026-10-23 起同样对齐到 GATE_LOW=0.6。
                qualified_events = [
                    e for e in sector_events
                    if e.confidence >= CATALYST_CONFIDENCE_GATE_LOW
                    and e.direction != "negative"
                ]

                if not qualified_events and not node4_events:
                    # A3 影子评估（低档闸与正式闸之间的残余带；两档相等时恒空）
                    shadow = [
                        e for e in sector_events
                        if CATALYST_CONFIDENCE_GATE_LOW <= e.confidence < CATALYST_CONFIDENCE_GATE
                        and e.direction != "negative"
                    ]
                    if shadow:
                        logger.info(
                            f"[Node 6 闸门影子] {name}: 门槛 "
                            f"{CATALYST_CONFIDENCE_GATE_LOW:.2f} 会放行 "
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

                # v2.1: 吸筹档位乘数（共用模块级常量 AMBUSH_ACCUM_MULTIPLIER，防与另一处各写各的）
                accum_multiplier = AMBUSH_ACCUM_MULTIPLIER.get(accum_strength, 1.0)

                if qualified_events:
                    score, top_event, category_tag, event_titles = self._compute_catalyst_score(
                        qualified_events, today,
                    )
                    top_event_date = top_event.event_date if top_event else None
                    top_event_level = top_event.impact_level if top_event else 1
                    gate_note = (
                        f"G4冻结放行({accum_strength}×{accum_multiplier})"
                        if accum_strength == "missing"
                        else f"闸门全通过({accum_strength}×{accum_multiplier})"
                    )
                    if node4_events:
                        # 日历胜出（有日期前瞻，信息更全）：Node 4 事件只补留痕，
                        # 不改分、不重复登记 P6（同一现实事件由日历侧 llm_narrative 负责）
                        gate_note += f"；{self._format_node4_note(node4_events)}"
                    node4_primary = False
                else:
                    # 日历零合格事件 → Node 4 当日事件作主催化（无 event_date，固定 proximity）
                    score, category_tag, event_titles, top_event_level = (
                        self._compute_node4_catalyst_score(node4_events)
                    )
                    # top_event_date 取 today（不是 None）：P4 退出分支按
                    # `category_tag=="fade" and today > event_date` 判「事件日次日卖」，
                    # 留 None 会让 fade 标签的持仓永远走不到该分支（只剩 -7% 止损），
                    # 且与 P6 登记（event_date=today）不一致。代价：次日即触发 fade_sell，
                    # 与「当日事件=已兑现、不追持有」的 fade 语义自洽。
                    top_event_date = today
                    gate_note = (
                        f"{self._format_node4_note(node4_events)}；闸门全通过"
                        f"({accum_strength}×{accum_multiplier})"
                    )
                    node4_primary = True

                adjusted_score = round(score * accum_multiplier, 3)

                pool.append(AmbushCandidate(
                    sector_name=name,
                    sector_code=detail.code,
                    catalyst_score=adjusted_score,
                    category_tag=category_tag,
                    events=event_titles[:3],
                    top_event_date=top_event_date,
                    top_event_level=top_event_level,
                    accumulation_detail=accum_detail,
                    gate_notes=gate_note,
                    accum_strength=accum_strength,
                ))

                # P6 兑现登记（第 4 步）：仅 Node 4 事件单独作主催化时登记——
                # 日历胜出场景由日历侧负责，避免同一现实事件双登记污染兑现统计。
                if node4_primary:
                    self._register_node4_realization(name, node4_events, today)

        # ── 决策 21: Agent 催化剂先行提名（单次批量调用，仅候选非空时触发）──
        if nomination_queue:
            if getattr(self, '_replay_mode', False):
                logger.info("[Node 6] replay_mode: 跳过 _llm_nominate_ambush（%d 候选）", len(nomination_queue))
                adopted: dict[str, str] = {}
            else:
                adopted = await self._llm_nominate_ambush(nomination_queue, regime, today)

            # 消费提名结果对两种模式共用：此前该循环被误置于 replay 分支内，
            # 且该分支的 adopted 恒为空 list，导致 LLM 提名结果被静默丢弃
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
            reservoir, overflow_info = self._merge_reservoir(reservoir, prev_reservoir_map, today, today_near_miss_names)
            # D-P2: 保存蓄水池溢出信息
            self._ambush_reservoir_overflow = overflow_info
            logger.info(
                f"[Node 6] 蓄水池跨日合并: 今日新入 + 前日留存 → {len(reservoir)} 条"
                + (f", 容量驱逐 {len(overflow_info)} 条" if overflow_info else "")
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
        # 而非个别板块盲区，显式告警避免静默空池。
        # 工作流 A：冻结态下 missing 被①接管、不再递增 missing_count，熔断条件恒 False自然退役；
        # 改为观测 frozen_released/initial 占比（过半打 INFO）；WARN 告警保留给开关关闭态。
        if AMBUSH_G4_FREEZE and sector.ambush:
            _frozen_n = gate_funnel["G4_吸筹"]["frozen_released"]
            if _frozen_n > len(sector.ambush) / 2:
                logger.info(
                    f"[Node 6] 冻结放行占比观测: {_frozen_n}/{len(sector.ambush)} "
                    f"埋伏候选因无资金流数据被 G4 冻结放行（>50%，数据源修复后应移除开关）"
                )
        elif sector.ambush and missing_count > len(sector.ambush) / 2:
            logger.warning(
                f"[Node 6] ⚠️ 资金流数据源覆盖异常: {missing_count}/{len(sector.ambush)} "
                f"埋伏候选无资金流数据（>50%），疑似数据源故障，请检查 Node 3"
            )

        veto_n = sum(1 for v in veto_log if not v.split(": ", 1)[-1].startswith("吸筹观察"))
        observe_n = len(veto_log) - veto_n

        # D-P2: 完成闸门漏斗追踪
        gate_funnel["final_pool"] = len(pool)
        # relations 补挂永久观测（非影子期专属：误挂归因与供给面读数长期需要）
        gate_funnel["relations_supplement"] = rel_supplement_stats
        self._ambush_gate_funnel = gate_funnel

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
            raw_text = await self._call_llm_async(prompt, temperature=0.2, max_tokens=400, call_type="node6.nominate")
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
    def _exclude_macro_regime_ref(events: list) -> list:
        """价值分级（2026-10-21）：例行宏观数据退出催化剂评分入口。

        依据：macro_rule 方向正确率 1/10（catalyst_realizations 本地回测）+ 宏观公告溢价
        文献（事件日风险补偿，非板块方向）。事件仍入日历/归档，只不参与评分排序。
        回滚：GOT_CATALYST_MACRO_AS_REGIME_REF=false 恢复旧行为。
        """
        if not CATALYST_MACRO_AS_REGIME_REF:
            return events
        return [e for e in events if getattr(e, "source", "") != "macro_rule"]

    @staticmethod
    def _group_events_by_sector(
        events: list[CatalystEvent],
    ) -> dict[str, list[CatalystEvent]]:
        """将催化剂事件按板块名分组（A2 归一化 + T5 扇出）

        若事件有 affected_sectors（T5 多板块扇出），一条事件进入多个桶；
        否则按 event.sector 单键分组。

        【P2-1 长期口径（裁决 #8，2026-09-28）】扇出不区分正负面——
        负面事件与正面事件同样进桶并参与下游加分，已明示接受为长期口径，
        affected_sectors 不升级 list[dict]（若翻案需同步改对齐 prompt/本扇出/portfolio 加分三处）。
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
    def _major_event_id(me) -> str:
        """Node 4 事件的稳定 id：优先已持久化 event_id，旧归档回退标题哈希。"""
        persisted = str(getattr(me, "event_id", "") or "").strip()
        if persisted:
            return persisted
        title = str(getattr(me, "title", "") or "").strip()
        return f"major_{hashlib.md5(title.encode('utf-8')).hexdigest()[:12]}"

    def _build_node4_glue_index(self, glue, news: "NewsContext") -> dict[str, list]:
        """glue.relations → {board_code: [major_event]}（2026-10-23）。

        取代 Node 4 支路的名称扇出：relations 直接给 board_code，无需别名/归一化。
        过滤口径与 `_build_node4_event_index` 一致（impact ∈ high/medium 且非负面）。
        开关 `GOT_NODE4_AMBUSH_EVENTS_ENABLED=false` → 空索引（回退名称扇出）。
        """
        if not GOT_NODE4_AMBUSH_EVENTS_ENABLED or not glue:
            return {}
        relations = getattr(glue, "relations", None) or []
        if not relations:
            return {}
        by_id = {
            self._major_event_id(me): me
            for me in (getattr(news, "major_events", None) or [])
        }
        if not by_id:
            return {}
        index: dict[str, list] = {}
        seen: set[tuple[str, str]] = set()
        for rel in relations:
            eid = str(getattr(rel, "event_id", "") or "")
            me = by_id.get(eid)
            if me is None:
                continue
            if str(getattr(rel, "direction", "")).lower() == "negative":
                continue
            if str(getattr(me, "impact_level", "")).lower() not in ("high", "medium"):
                continue
            if str(getattr(me, "sentiment", "")).lower() == "negative":
                continue
            code = str(getattr(rel, "board_code", "") or "")
            if not code or (code, eid) in seen:
                continue
            seen.add((code, eid))
            index.setdefault(code, []).append(me)
        if index:
            logger.info(
                "[Node 6] Node 4×glue 归属: %d 条 relations → %d 个板块",
                len(relations), len(index),
            )
        return index

    @staticmethod
    def _build_node4_event_index(news: "NewsContext") -> dict[str, list]:
        """Node 4 当日事件按 affected_sectors 归一化分桶（2026-10-22，埋伏通道第二支路）。

        背景（10-21 实测）：当日 11 个产业级事件（稀土出口管制/厄尔尼诺/MLCC 交期 52 周/
        液氩涨价）对埋伏通道零贡献——`_build_ambush_channel` 只读 `catalyst_calendar.events`。
        59 个归档日中正式池 >0 仅 16 天（27%），均值 0.47 个/日。

        与 `_group_events_by_sector` 同形（normalize_sector_name + affected_sectors 扇出）：
        - 不做日期窗口过滤：当日事件即"正在发生"，没有 event_date；
        - 准入条件改为 impact_level ∈ {high, medium} 且 sentiment != negative
          （日历侧用 confidence 硬闸，Node 4 侧用确定性映射权重，见
          `NewsAnalysisNode._derive_catalyst_weight`）；
        - 宏观例行数据不排除（Node 4 事件非 macro_rule 来源，`_exclude_macro_regime_ref`
          只作用于日历）。

        开关 GOT_NODE4_AMBUSH_EVENTS_ENABLED=false → 空索引（逐字节回现状）。
        """
        if not GOT_NODE4_AMBUSH_EVENTS_ENABLED:
            return {}
        from src.got.board_aliases import normalize_sector_name

        index: dict[str, list] = {}
        for event in (getattr(news, "major_events", None) or []):
            if str(getattr(event, "impact_level", "")).lower() not in ("high", "medium"):
                continue
            if str(getattr(event, "sentiment", "")).lower() == "negative":
                continue
            seen_keys: set[str] = set()
            for raw_sector in (getattr(event, "affected_sectors", None) or []):
                key = normalize_sector_name(raw_sector)
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)
                index.setdefault(key, []).append(event)
        if index:
            logger.info(
                "[Node 6] Node 4 事件索引: %d 条事件 → %d 个板块桶",
                len(getattr(news, "major_events", []) or []), len(index),
            )
        return index

    def _register_node4_realization(
        self, sector_name: str, events: list, today: date
    ) -> None:
        """Node 4 事件入埋伏池后补登 P6 兑现追踪（2026-10-22，第 4 步）。

        目的：不断样本链——`catalyst_realizations` 是唯一可校准的「预期 vs 实际」数据源，
        新准入路径若不登记，后续无法回答「Node 4 事件入池的 3 日超额是多少」。

        纪律：
        - 只在 Node 4 事件**单独**作主催化时调用（日历胜出的场景由日历侧 llm_narrative 登记，
          同一现实事件不双登记）；
        - event_id 用 `_make_event_id(source, type, sector, date)`（与日历同一确定性 MD5）
          → 同日重跑 UPSERT 幂等；
        - `register_event` 收的是 `CatalystEvent`（不是散字段）；
        - replay 模式零持久化副作用 → 跳过；异常只 warn 不阻断主流程。
        """
        if not events or getattr(self, "_replay_mode", False):
            return
        try:
            top = max(
                events,
                key=lambda e: (
                    self._NODE4_IMPACT_MULT.get(str(getattr(e, "impact_level", "")).lower(), 1.0),
                    float(getattr(e, "catalyst_weight", 0.0) or 0.0),
                ),
            )
            tracker = getattr(self, "_p6_tracker", None)
            if tracker is None:
                from src.got.catalyst_realization import CatalystRealizationTracker
                tracker = CatalystRealizationTracker()
                self._p6_tracker = tracker
            title = (getattr(top, "title", "") or "")[:80]
            tracker.register_event(CatalystEvent(
                event_id=_make_event_id("node4_event", "narrative", sector_name, today),
                event_date=today,
                event_type="narrative",
                title=f"[Node4] {title}",
                sector=sector_name,
                impact_level=3 if str(getattr(top, "impact_level", "")).lower() == "high" else 2,
                direction="positive",
                confidence=float(getattr(top, "catalyst_weight", 0.0) or 0.0),
                consensus_expectation="Node 4 当日事件，埋伏通道准入",
                source="node4_event",
                category_tag="fade",
                is_global=False,
                source_news=title,
            ))
        except Exception as exc:  # noqa: BLE001 — P6 旁挂，失败不阻断埋伏通道
            logger.warning("[Node 6] Node 4 事件 P6 登记失败（不阻断）: %s", exc)

    @staticmethod
    def _lookup_sector_events(
        name: str, events_by_sector: dict[str, list]
    ) -> list:
        """归一化板块名查找事件（A2：精确 → 别名变体 → 空）

        解决晋升路径/P5 中板块名与事件 sector 命名不一致导致的事件饥荒。

        2026-10-22：入参泛化为 `list`（元素可 CatalystEvent 或 NewsEvent），供埋伏通道
        的 Node 4 事件支路复用同一套 L1/L2 查找——纯字典取值，不新写第二套查找。
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
    def _build_relations_index(glue) -> dict[str, list[dict]]:
        """当日 in-memory glue → {board_code: [{event_id, direction}]} 索引。

        与 _load_prev_glue_relations_index 消费端同构；glue 缺失/degraded/无 relations
        时返回空索引（补挂恒 no-op）。护栏不变：direction 预筛、日历原值过闸。
        """
        index: dict[str, list[dict]] = {}
        for r in (getattr(glue, "relations", None) or []):
            code = str(getattr(r, "board_code", "") or "")
            eid = str(getattr(r, "event_id", "") or "")
            if code and eid:
                index.setdefault(code, []).append(
                    {"event_id": eid, "direction": str(getattr(r, "direction", "positive"))}
                )
        if index:
            logger.info(
                f"[relations补挂] 载入当日 glue relations: "
                f"({sum(len(v) for v in index.values())} 关系 / {len(index)} 板块)"
            )
        return index

    @staticmethod
    def _load_prev_glue_relations_index(root=None, before: Optional[date] = None) -> dict[str, list[dict]]:
        """读最近一份归档 agent_glue.json 的 relations，建 {board_code: [{event_id, direction}]} 索引。

        归档回退路径（仅独立调用：单测/直接实例化）：生产 run() 已改为消费当日
        in-memory glue（见 _build_relations_index）——实测埋伏名单日间轮换几乎不相交，
        前日 relations 对当日名单覆盖为 0，只能作兜底。
        取当日之前的归档：A-P0-1 排除当天守同日重跑幂等。取当日归档会读到
        本次 run 正在写入的文件（glue 落盘先于通道评估），内存路径不存在此问题。
        降级：无归档/解析失败 → 空索引（与开关关闭同效，不阻断主流程）。
        """
        from src.got.archive import list_archive_dirs
        archive_root = Path(root) if root is not None else GLUE_ARCHIVE_ROOT
        dirs = list_archive_dirs(reverse=True, before=before or date.today(), base_dir=archive_root)
        for d in dirs:
            p = d / "agent_glue.json"
            if not p.exists():
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as exc:
                logger.debug(f"[relations补挂] 解析 {p} 失败: {exc}")
                continue
            index: dict[str, list[dict]] = {}
            for r in (data.get("relations") or []):
                if not isinstance(r, dict):
                    continue
                code = str(r.get("board_code", ""))
                eid = str(r.get("event_id", ""))
                if code and eid:
                    index.setdefault(code, []).append(
                        {"event_id": eid, "direction": str(r.get("direction", "positive"))}
                    )
            if index:
                logger.info(
                    f"[relations补挂] 载入前日 relations: {p.parent.name}/agent_glue.json "
                    f"({sum(len(v) for v in index.values())} 关系 / {len(index)} 板块)"
                )
            return index
        return {}

    @staticmethod
    def _supplement_from_relations(
        sector_events: list[CatalystEvent],
        code: str,
        rel_index: dict[str, list[dict]],
        all_events: list[CatalystEvent],
        stats: dict,
    ) -> list[CatalystEvent]:
        """名称查表落空时的 relations 补挂（仅空时触发，纯增量不动已命中关系）。

        护栏（设计讨论 2026-09-30）：
        - 只信当日日历事件原值：relation 回连 events 取不到即丢（前日事件已过期）；
        - relation 自报 direction==negative 不补挂（利空线不松弛）；回连后下游
          合格过滤 / ★★★利空闸 / B 公式均照常执行；
        - relation 自报 confidence 不作数，下游 CATALYST_CONFIDENCE_GATE 照旧。
        永久观测：legacy_miss_and_no_relations_hit / relations_matched /
        relations_hit_all_missed 计数落 ambush_gate_funnel。relations_matched 仅表示
        索引命中 code（不论事件是否最终放行）；真实补挂须看 supplemented_events>0。
        """
        if sector_events or not rel_index:
            return sector_events
        stats["legacy_miss_total"] += 1
        rels = rel_index.get(code or "")
        if not rels:
            stats["legacy_miss_and_no_relations_hit"] += 1
            return sector_events
        event_by_id = {e.event_id: e for e in all_events if e.event_id}
        hits: list[CatalystEvent] = []
        seen_ids: set[str] = set()
        for rel in rels:
            if rel.get("direction") == "negative":
                continue
            ev = event_by_id.get(rel.get("event_id", ""))
            if ev is None:
                continue
            if ev.event_id not in seen_ids:
                seen_ids.add(ev.event_id)
                hits.append(ev)
        stats["relations_matched"] += 1
        if hits:
            stats["supplemented_events"] += len(hits)
            logger.info(
                f"[relations补挂] {code}: 名称查表落空，relations 补挂 {len(hits)} 个事件 "
                f"({', '.join(e.title[:24] for e in hits[:2])})"
            )
        else:
            stats["relations_hit_all_missed"] += 1  # 索引命中但全被护栏拦下
        return hits

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
        1. 原名用 _match_sector（归一化精确 → 别名 → 受控子串 ≥3 字，
           排除 generic/narrow 不可靠匹配）——避免 _fuzzy_match 的 2 字拆词
           子串命中导致"医药"→"医药电商"/"装修装饰"→"建筑装饰"错配。
        2. 未命中 → 别名表代理板【严格匹配】（去泛化后缀后相等）
        """
        # 阶段 1: _match_sector（精确 → 别名 → 受控子串，排除 generic/narrow）
        for name, amount in flow_map.items():
            if not isinstance(name, str) or not name:
                continue
            matched, match_type = cls._match_sector(sector_name, name, min_substr_len=3)
            if matched and match_type not in ("generic", "narrow"):
                try:
                    return float(amount), name
                except (TypeError, ValueError):
                    continue
        # 阶段 2: 资金别名表严格匹配（去泛化后缀后相等）
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
        # ACCUMULATION_TOLERANCE: 净流出 < 0.5 亿视为噪声持平，不算否决
        tol = ACCUMULATION_TOLERANCE
        if consecutive_positive >= 3 and today_flow >= -tol:
            return True, f"{proxy_tag}连续{consecutive_positive}日正流入+当日{today_flow:.1f}亿", "strong", evidence
        elif consecutive_positive >= 3:
            return False, f"{proxy_tag}连续{consecutive_positive}日正流入但当日净流出{abs(today_flow):.1f}亿", "medium", evidence
        elif flow_3d_avg is not None and flow_3d_avg > 0 and today_flow >= -tol:
            return True, f"{proxy_tag}3日均净流入{flow_3d_avg:.1f}亿+当日{today_flow:.1f}亿", "medium", evidence
        elif today_flow >= -tol:
            detail = (
                f"{proxy_tag}当日净流入{today_flow:.1f}亿"
                if today_flow > 0
                else f"{proxy_tag}当日资金持平({today_flow:.1f}亿，噪声容忍)"
            )
            return True, detail, "weak", evidence
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

        Q2 audit (2026-09-23): 注意力闸已修复 — 使用 SectorContext.ths_hot_sectors 替代恒空的 associated_sectors
        已发酵检查：同类催化剂已在过去 7 天内出现过 → 行情可能已提前走完

        文献：Kargarzaseh et al. 2026 — "等确认再进 = 买在顶上"
        """
        # 注意力闸：3+ 只关联个股上热榜 → 太热了
        # Q2 audit: 现在使用 THS 板块热度数据，闸门已激活
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

        Q2 audit (2026-09-23): 已修复 — 使用 SectorContext.ths_hot_sectors 替代恒空的 associated_sectors
        闸门已激活，可正常拦截高拥挤度板块。

        与防买顶的注意力闸互补：
        - 注意力闸：模糊匹配 3+ 只 → 否决（渐进式）【已启用】
        - 拥挤黑名单：精确匹配板块名 → 否决（硬闸）【已启用】

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
        # 口径（2026-10-21）：**实跑**日历不再含已过事件日的记录（_load_active_events 按
        # event_date >= today 过滤），故 else 0 分支只在两种场景命中——① 事件日=今天
        # （_count_trade_days 含首尾 → 1）；② 回放/单测直接传入含过去事件的上下文。
        # 事件后的漂移收益不靠这里补：P6 兑现通道（catalyst_realized，day_3 超额）另立，
        # CATALYST_DRIFT_TAIL_TRADE_DAYS 现只约束 catalyst_events.expires_at 的库内保留期。
        trade_days = _count_trade_days(today, top_event.event_date) if top_event.event_date >= today else 0
        if trade_days <= ANTICIPATION_THRESHOLD_DAYS:
            proximity = ANTICIPATION_DECAY_NEAR  # 0.6（anticipation 大半走完）
        else:
            proximity = ANTICIPATION_DECAY_NORMAL  # 1.0

        main_score = impact_weight * proximity * top_event.confidence

        # A3（批次 A 证据链）：证据档→催化分乘数。一期 confirmed/reported=1.0（零变化），
        # rumor<1.0 为二期无源事件预留；rumor 且无 url 仅打低证据日志（不改决策）。
        tier_mult = EVIDENCE_TIER_MULTIPLIER.get(top_event.evidence_tier, 1.0)
        if tier_mult < 1.0 or (top_event.evidence_tier == "rumor" and not top_event.source_url):
            logger.info(
                "[Node 6][低证据] %s/%s evidence_tier=%s url=%s 乘数=%.2f",
                getattr(top_event, "sector", "?"), getattr(top_event, "title", "?")[:24],
                top_event.evidence_tier, "有" if top_event.source_url else "无", tier_mult,
            )
        main_score *= tier_mult

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

    # Node 4 事件支路的固定临近系数（2026-10-22）：NewsEvent 无 event_date，
    # B 公式的 proximity 无输入。0.5 等价"中等临近"（介于 ANTICIPATION_DECAY_NEAR=0.6 与
    # 已过事件 0 之间）。若后续回测显示固定值扭曲排序，再接入
    # NewsEvent.catalyst_onset_days（字段已预留，本次不接）。
    NODE4_PROXIMITY_FIXED = 0.5
    _NODE4_IMPACT_MULT = {"high": 2.0, "medium": 1.0}

    @classmethod
    def _compute_node4_catalyst_score(
        cls, events: list
    ) -> tuple[float, str, list[str], int]:
        """Node 4 当日事件的主催化分（日历落空时的替代路径）。

        主催化分 = 影响权重(high=2.0 / medium=1.0) × 0.5(固定临近) × mean(catalyst_weight)。
        与 `_compute_catalyst_score` 同量纲（日历档 0.18~1.0），使两路候选可在同一
        `catalyst_score` 排序中混排。

        Returns:
            (score, category_tag, event_titles, top_impact_level)
            category_tag 固定 "fade"（当日事件=软叙事，事件日即退）；
            top_impact_level 折算 1/2（同日历档位口径），供 AmbushCandidate 落盘。
        """
        if not events:
            return 0.0, "", [], 1

        ranked = sorted(
            events,
            key=lambda e: (
                -cls._NODE4_IMPACT_MULT.get(str(getattr(e, "impact_level", "")).lower(), 1.0),
                -float(getattr(e, "catalyst_weight", 0.0) or 0.0),
            ),
        )
        top = ranked[0]
        impact_weight = cls._NODE4_IMPACT_MULT.get(
            str(getattr(top, "impact_level", "")).lower(), 1.0
        )
        weights = [float(getattr(e, "catalyst_weight", 0.0) or 0.0) for e in ranked]
        mean_weight = sum(weights) / len(weights)
        score = round(impact_weight * cls.NODE4_PROXIMITY_FIXED * mean_weight, 3)
        top_level = 2 if str(getattr(top, "impact_level", "")).lower() == "high" else 1
        return score, "fade", [getattr(e, "title", "") for e in ranked[:3]], top_level

    @staticmethod
    def _format_node4_note(events: list) -> str:
        """Node 4 事件留痕（gate_notes 用）：`Node4事件(high×2, w=0.80): 标题 / 标题`"""
        if not events:
            return ""
        top = max(
            events,
            key=lambda e: (
                AggregatorNode._NODE4_IMPACT_MULT.get(str(getattr(e, "impact_level", "")).lower(), 1.0),
                float(getattr(e, "catalyst_weight", 0.0) or 0.0),
            ),
        )
        titles = " / ".join((getattr(e, "title", "") or "")[:24] for e in events[:2])
        return (
            f"Node4事件({getattr(top, 'impact_level', '?')}×{len(events)}, "
            f"w={float(getattr(top, 'catalyst_weight', 0.0) or 0.0):.2f}): {titles}"
        )

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
                # 2026-10-23 修：原先一律写 "no_catalyst"，把 near_miss:score_below_floor
                # 也误标成「无事件支撑」（10-23 15 条蓄水池里 9 条其实是分数 0.28~0.29<0.30）。
                # 蓄水池条目的 reason 才是真实失败原因，原样回写。
                intel.ambush_gate_fail = reservoir_map[name].reason or "no_catalyst"

    # =========================================================================
    # Level 2: 组合决策
    # =========================================================================

    @staticmethod
    def _compute_position_from_regime(
        regime: MarketRegime,
        confirmed: list[SectorIntel],
    ) -> float:
        """基于 regime + actionable_sectors 计算仓位

        口径隔离：只统计主线/降温侧的 actionable，埋伏板块的仓位由
        Node 7 的 ROLE_MULTIPLIER 单独控制，不顶高全局仓位上限。

        v2.1: dwell 感知缩放（替换硬编码 regime 系数）

        B3 注释修正：实际只有两层（状态缩放 + 回撤保护），
        波动率调整（Node 1 position_limit）当前未接入 final_position_limit。
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

    @staticmethod
    def _build_theme_shadow_dict(theme_ctx) -> dict:
        """Phase 4b 影子留痕：把 MarketThemeContext 压成观测 dict（不进判定）。"""
        top5 = [
            {"name": c.name, "rank": c.rank, "change_pct": round(c.change_pct, 2)}
            for c in (theme_ctx.leading_concepts or [])[:5]
        ]
        status = theme_ctx.status
        note = (
            "unknown/未构造：上游未调 build_theme_context 或取数失败（零行为）"
            if status == "unknown" else
            f"active={len(theme_ctx.active_themes)} leading={len(theme_ctx.leading_concepts)}"
        )
        return {
            "status": status,
            "source": theme_ctx.data_quality.source,
            "top5_concepts": top5,
            "breadth": theme_ctx.theme_breadth.model_dump(),
            "note": note,
        }

    @staticmethod
    def _theme_confidence_factor(theme_ctx, sector) -> float:
        """enabled-only：题材阶段 vs 板块主线状态一致性因子（§5.4 近似）。

        用 active_mainline 是否非空近似 RRG 'leading' 象限（aggregator 组装点不直接持有
        逐板块 rrg.quadrant，此为可行近似，enabled 转正前需影子读数校准）：
        题材宽度偏多（accelerating+warming > cooling）与是否存在 leading 主线不一致
        → 返回 0.9；一致或数据不可用→ 1.0。
        """
        if getattr(theme_ctx, "status", "unknown") != "ok":
            return 1.0
        b = theme_ctx.theme_breadth
        theme_bullish = (b.accelerating_count + b.warming_count) > b.cooling_count
        has_leading_mainline = bool(getattr(sector, "active_mainline", None))
        return 1.0 if theme_bullish == has_leading_mainline else 0.9

    @staticmethod
    def _build_conflict_summary_shadow(regime, sector_intels, sector, capital, sentiment) -> dict:
        """Phase 3b 影子留痕：把节点信号→AgentOpinion[]→11 类冲突分类，产观测 dict（不进判定）。

        aggregator 侧无组合层 sell_source（那是 Phase 2 产物），故 risk_override_present
        在此恒 False（真风控否决在 PD 侧派生）。
        E3 接线（2026-10-15）：memory 由 None 改为 AgentMemory.from_config()——
        compute_signal_weights 走先验常量表 _SOURCE_PRIOR_WEIGHTS 路径（from_config 内部
        try/except 回退 enabled=False → compute_skill_weights 返回全 1.0 校准因子，
        先验权重直接生效）。仅改 shadow JSON 内容，零决策影响（3b 缺省 0 不触发 veto_buy）。
        2026-10-15 前的等权读数作废重计（verification.md 3b 判据节注记读数起点）。
        """
        from src.agent.memory import AgentMemory
        from src.got.disagreement import build_disagreement_summary, build_opinions

        opinions = build_opinions(
            regime=regime, sector=sector, sector_intels=sector_intels,
            capital=capital, sentiment=sentiment, sell_source=None,
        )
        return build_disagreement_summary(
            opinions, risk_override_present=False, memory=AgentMemory.from_config()
        )

    # =========================================================================
    # 辅助方法
    # =========================================================================

    @staticmethod
    def _parse_sector_level_flows(capital: CapitalContext) -> dict[str, float]:
        """解析板块级资金流（capital.sector_flow_all），排除通道/杠杆聚合项。

        Returns:
            {板块名: 净流入(亿)} — 仅用于板块级错位比对，不做家族级归并。

        A6 阶段 2：规则三件套已退役，本函数仍供 `_detect_family_capital_mismatch`
        消费（主线家族 vs 板块资金错位检测），保留。
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
    def _extract_hot_sectors(sentiment: SentimentContext, sector_ctx: Optional[SectorContext] = None) -> list[str]:
        """提取热榜关联板块
        
        Q2 audit (2026-09-23): 优先使用 SectorContext.ths_hot_sectors（THS 板块热度），
        降级到 sentiment.hot_stocks[].associated_sectors（恒空，历史遗留）。
        """
        # Q2 audit: 优先使用 THS 板块热度数据
        if sector_ctx and getattr(sector_ctx, 'ths_hot_sectors', None):
            return list(sector_ctx.ths_hot_sectors.keys())
        # 降级：旧逻辑（associated_sectors 恒空）
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
    # A6 阶段 2：_match_sector 仍供 _lookup_flow_map（吸筹确认闸门 _check_accumulation_gate
    # 的资金流比对）消费，本常量随之保留。
    _GENERIC_ROOTS = frozenset({
        "AI", "芯片", "电力", "新能源", "医药", "军工", "消费", "金融",
        "周期", "地产", "汽车", "数据", "软件", "硬件", "网络", "通信",
        "光伏", "储能", "机器人", "智能", "科技", "信息", "生物",
    })

    @staticmethod
    def _match_sector(
        name: str,
        target: str,
        min_substr_len: int = 3,
    ) -> tuple[bool, str]:
        """统一板块名匹配入口（A5）

        匹配链路：归一化精确 → 别名变体 → 受控子串（≥min_substr_len 字）

        A6 阶段 2：规则三件套已退役，本函数仍供 `_lookup_flow_map` 消费（板块名→
        资金流表的归一化匹配，由吸筹确认闸门 `_check_accumulation_gate` 调用），保留。

        Args:
            min_substr_len: L3 子串匹配的最小字数门槛，默认 3。

        Returns:
            (matched, match_type): match_type = "exact" / "alias" / "substring"
                / "narrow" / "generic" / "none"
            "generic" 表示命中泛化词根（护栏），调用方自行判断是否降权
            "narrow" 表示目标比查询更窄（查询是目标的子串）
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

        # L3: 受控子串（≥min_substr_len 字，剥离后缀后）
        s = AggregatorNode._strip_generic_suffix(norm_name)
        t = AggregatorNode._strip_generic_suffix(norm_target)
        if len(s) >= min_substr_len and len(t) >= min_substr_len:
            if s in t or t in s:
                core = s if s in t else t
                if core in AggregatorNode._GENERIC_ROOTS:
                    return True, "generic"
                if s in t and len(t) > len(s):
                    return True, "narrow"
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

    def _synthesize_mainline_endurance(
        self,
        sector_intels: list[SectorIntel],
        sector: SectorContext,
        news: NewsContext,
        regime: MarketRegime,
        glue: Optional[SemanticGlueResult] = None,
    ) -> list[MainlineEndurance]:
        """合成主线续航评估（v3.5 S4）。

        对 active_mainline 家族逐一生成续航评估：
        - 警示信号加权计分（评分制，不做硬闸门）
        - 支撑信号：全成员 leading、family_resonance ≥0.7、有催化剂、资金 passed、梯队仍有新补涨成员
        - band：警示分 ≤1 惯性期；2-3 分化期；≥4 见顶风险期（阈值可配）

        A6 接线：
        - family_name 三级优先：glue.families[board_code].family → intel.frame_group → intel.sector_name
        - E2 公式：family_resonance 分母从固定 50.0 改为当日家族 std_dev P90 自适应归一化（P90=0 时 fallback 50.0）
        """
        from src.got.schemas import MainlineEndurance
        from collections import defaultdict
        import statistics

        # 仅处理 actionable 板块（主线角色）
        actionable_intels = [
            intel for intel in sector_intels
            if intel.classification == "actionable" and intel.sector_role == "mainline"
        ]
        if not actionable_intels:
            return []

        # ── sector_name → SectorDetail 查表（审核 P0 修复）──
        # SectorIntel 不携带 detail/wave_days/RRG 等字段，续航合成所需的波次/趋势/RRG
        # 原始数据在 Node 2 的 SectorDetail 上，从 SectorContext 各桶建立查表。
        detail_lookup: dict[str, SectorDetail] = {}
        for _group in getattr(sector, "active_mainline", None) or []:
            for _member in getattr(_group, "members", None) or []:
                detail_lookup.setdefault(_member.name, _member)
        for _bucket in ("cooling_down", "exhausted_mainline", "top_by_score"):
            for _d in getattr(sector, _bucket, None) or []:
                detail_lookup.setdefault(_d.name, _d)

        # ── A6: glue.families 以 board_code 为键，构建 code → family 索引 ──
        glue_family_by_code: dict[str, str] = {}
        if GOT_A6_ENABLED and glue is not None and glue.status == "ok":
            for gf in (glue.families or []):
                if getattr(gf, "board_code", "") and getattr(gf, "family", ""):
                    glue_family_by_code[gf.board_code] = gf.family

        # 按家族分组（A6 三级优先：glue.families → intel.frame_group → intel.sector_name）
        families: dict[str, list[SectorIntel]] = defaultdict(list)
        for intel in actionable_intels:
            _d = detail_lookup.get(intel.sector_name)
            _code = getattr(_d, "code", "") if _d is not None else ""
            family_name = (
                (glue_family_by_code.get(_code) if _code else None)
                or intel.frame_group
                or intel.sector_name
            )
            families[family_name].append(intel)

        # ── E2: 预计算各家族 strength std_dev，取当日 P90 作为归一化分母 ──
        # P90=0 或不足两个家族时 fallback 50.0（保持与旧公式同量级）
        per_family_std: dict[str, float] = {}
        for fname, members in families.items():
            strengths = [m.composite_score for m in members]
            if len(strengths) >= 2:
                per_family_std[fname] = float(statistics.stdev(strengths))
            # 单成员家族无 std_dev，后续 resonance 直接返 1.0，不参与 P90
        std_values = sorted(per_family_std.values())
        if std_values:
            # 简易 P90：线性插值（numpy 无依赖）
            _n = len(std_values)
            if _n == 1:
                _p90 = std_values[0]
            else:
                _idx = 0.9 * (_n - 1)
                _lo = int(_idx // 1)
                _hi = min(_lo + 1, _n - 1)
                _frac = _idx - _lo
                _p90 = std_values[_lo] * (1 - _frac) + std_values[_hi] * _frac
            resonance_denom = _p90 if _p90 > 1e-6 else 50.0
        else:
            resonance_denom = 50.0

        endurance_list = []
        crowded_families = set(getattr(sector, "crowded_families", []) or [])

        for family_name, members in families.items():
            # ── 基础指标 ──
            member_details = [detail_lookup.get(m.sector_name) for m in members]
            wave_days_list = [
                getattr(d, "wave_days", 0) or 0 for d in member_details if d is not None
            ]
            max_wave_days = max(wave_days_list) if wave_days_list else 0

            # lifecycle_phase：取最老成员（wave_days 最大）的 phase
            oldest_detail = None
            oldest_wave = -1
            for d in member_details:
                if d is not None:
                    w = getattr(d, "wave_days", 0) or 0
                    if w > oldest_wave:
                        oldest_wave = w
                        oldest_detail = d
            lifecycle_phase = None
            if oldest_detail is not None and getattr(oldest_detail, "lifecycle", None):
                lifecycle_phase = oldest_detail.lifecycle.phase

            # RRG leading count
            rrg_leading_count = sum(
                1 for d in member_details
                if d is not None and getattr(d, "rrg", None)
                and getattr(d.rrg, "quadrant", None) == "leading"
            )
            member_count = len(members)

            # ── 厚度指标 ──
            # family_resonance：成员强度标准差的倒数（一致性越高，共振度越高）
            # A6 E2: 分母从固定 50.0 改为当日家族 std_dev P90 自适应归一化
            strengths = [m.composite_score for m in members]
            if len(strengths) >= 2:
                std_dev = float(statistics.stdev(strengths))
                family_resonance = max(0.0, min(1.0, 1.0 - std_dev / resonance_denom))
            else:
                family_resonance = 1.0

            # 梯队分层（波次口径：wave_days ≥6 veteran / 3-5 mid / ≤2 new）
            tiers = {"veteran": [], "mid": [], "new": []}
            # P1-1 尾巴 / OPEN-4 X1 同型防御（排期批 4.5，2026-09-28）：
            # member_details 现由上方列表推导按 members 1:1 生成，长度恒等 → zip 不会截断。
            # 此处仅设「金丝雀」：一旦未来改动破坏该不变式（两列表错长），zip 的静默截断
            # 会丢成员而不报错，故显式校验并留痕（不抛异常、不改变现有行为）。
            if len(members) != len(member_details):
                logger.error(
                    "[OPEN-4/X1 防御] 家族 %s members(%d) 与 member_details(%d) 长度错配，"
                    "下方 zip 将静默截断——请核查 member_details 构造处是否仍与 members 1:1",
                    family_name, len(members), len(member_details),
                )
            for m, d in zip(members, member_details):
                w = getattr(d, "wave_days", 0) or 0 if d is not None else 0
                if w >= 6:
                    tiers["veteran"].append(m.sector_name)
                elif w >= 3:
                    tiers["mid"].append(m.sector_name)
                else:
                    tiers["new"].append(m.sector_name)

            # ── 跨节点信号 ──
            max_overheat = max(
                (getattr(m, "overheat_severity", 0) or 0) for m in members
            )
            crowded = family_name in crowded_families

            # capital_stance：成员 capital_check 聚合（审核 P0 修复：SectorIntel 值域为
            # aligned/neutral/divergent，映射到续航三态 passed/rejected/missing）
            capital_checks = [getattr(m, "capital_check", "neutral") for m in members]
            if capital_checks and all(c == "aligned" for c in capital_checks):
                capital_stance = "passed"
            elif any(c == "divergent" for c in capital_checks):
                capital_stance = "rejected"
            else:
                capital_stance = "missing"

            # has_catalyst：成员消息面已被催化（Node 4 日历交叉验证结果，审核 P0 修复：
            # SectorIntel 无 catalyst_events 字段，改用 news_check 三态判定）
            has_catalyst = any(
                getattr(m, "news_check", "neutral") == "catalyzed" for m in members
            )

            # ── 警示信号计分（评分制，不做硬闸门）──
            warning_signals = []
            warning_score = 0

            # 过热 ≥0.3（+1）/≥0.5（+2）
            if max_overheat >= 0.5:
                warning_signals.append(f"严重过热（{max_overheat:.2f}）")
                warning_score += 2
            elif max_overheat >= 0.3:
                warning_signals.append(f"过热警告（{max_overheat:.2f}）")
                warning_score += 1

            # 拥挤家族（+1）
            if crowded:
                warning_signals.append("家族拥挤（≥3板块同族）")
                warning_score += 1

            # 资金 missing/rejected（+1）
            if capital_stance in ("missing", "rejected"):
                warning_signals.append(f"资金面未确认（{capital_stance}）")
                warning_score += 1

            # 无催化剂（+1）
            if not has_catalyst:
                warning_signals.append("无近期催化剂")
                warning_score += 1

            # 破位信号（+2，独立于过热，决策 9）：T-0 板块跌幅 <-3 或强度轨迹 decreasing；
            # 审核 P0 修复：字段取自 SectorDetail 查表（原读 SectorIntel.detail 恒 None 永不触发）
            break_signal = False
            for d in member_details:
                if d is None:
                    continue
                today_change = getattr(d, "today_change", None)
                if today_change is not None and today_change < -3:
                    break_signal = True
                    break
                if getattr(d, "trend_direction", None) == "decreasing":
                    break_signal = True
                    break
            if break_signal:
                warning_signals.append("破位信号（跌幅>3%或趋势下行）")
                warning_score += 2

            # wave_days ≥6（+1）
            if max_wave_days >= 6:
                warning_signals.append(f"老波（wave_days={max_wave_days}）")
                warning_score += 1

            # ── 支撑信号 ──
            support_signals = []
            if rrg_leading_count == member_count and member_count > 0:
                support_signals.append("全成员 RRG leading")
            if family_resonance >= 0.7:
                support_signals.append(f"高共振（{family_resonance:.2f}）")
            if has_catalyst:
                support_signals.append("有催化剂")
            if capital_stance == "passed":
                support_signals.append("资金面确认")
            if tiers["new"]:
                support_signals.append(f"梯队有新补涨（{len(tiers['new'])}个）")

            # ── 续航区间（启发式）──
            if warning_score <= 1:
                endurance_band = "inertia"
            elif warning_score <= 3:
                endurance_band = "divergence"
            else:
                endurance_band = "topping_risk"

            # ── 次日观察点 ──
            exit_watch = ""
            if endurance_band == "topping_risk":
                exit_watch = "见顶风险期，密切关注破位信号"
            elif endurance_band == "divergence":
                exit_watch = "分化期，观察梯队是否跟涨"
            else:
                exit_watch = "惯性期，维持观察"

            # 构建 MainlineEndurance
            endurance = MainlineEndurance(
                name=family_name,
                wave_days=max_wave_days,
                lifecycle_phase=lifecycle_phase,
                rrg_leading_count=rrg_leading_count,
                member_count=member_count,
                family_resonance=round(family_resonance, 2),
                tiers=tiers,
                max_overheat=round(max_overheat, 2),
                crowded=crowded,
                capital_stance=capital_stance,
                has_catalyst=has_catalyst,
                support_signals=support_signals,
                warning_signals=warning_signals,
                endurance_band=endurance_band,
                exit_watch=exit_watch,
            )
            endurance_list.append(endurance)

        # 按 warning_score 降序排列（风险高的排前面）
        endurance_list.sort(
            key=lambda e: len(e.warning_signals),
            reverse=True,
        )

        return endurance_list

    @staticmethod
    def _load_previous_family_suppression() -> dict[str, dict]:
        """加载前日家族压制台账（FTS，从 data/got_daily/ 最近归档的 unified.json）。

        照搬 `_load_previous_reservoir` 读法：`before=date.today()` 保证同日重跑读的是前一交易日，
        days_suppressed 计数在同日多次运行下稳定不重复递增。

        Returns:
            {family_name: entry_dict}，失败/无归档返回空 dict。
        """
        import json
        from src.got.archive import list_archive_dirs

        date_dirs = list_archive_dirs(reverse=True, before=date.today())
        for d in date_dirs:
            unified_path = d / "unified.json"
            if unified_path.exists():
                try:
                    with open(unified_path, encoding="utf-8") as f:
                        data = json.load(f)
                    prev = data.get("family_suppression", []) or []
                    return {e["name"]: e for e in prev if e.get("name")}
                except Exception as e:
                    logger.warning(f"[Node 6] 加载前日家族压制台账失败: {e}")
                    return {}
        return {}

    @staticmethod
    def _synthesize_family_suppression(
        mainline_endurance: list,
        prev_map: dict[str, dict],
    ) -> list:
        """合成家族见顶持续压制台账（FTS）。

        与 MEG 正交、与大盘 regime 解耦：只消费 Node6 已算出的 endurance_band=topping_risk。

        跨日 latch（days_suppressed = 连续处于压制台账的交易日数）：
        - 今日 topping + 前日未压 → 新建 days_suppressed=0, days_healthy=0
        - 今日 topping + 前日已压 → days_suppressed+=1, days_healthy=0（持续；actively topping 不因 COOLDOWN 释放）
        - 今日非 topping + 前日已压 → days_healthy+=1, days_suppressed+=1；
          达 EXIT_HEALTHY（连续健康迟滞退出）或 COOLDOWN（最大锁定上限，防振荡永久锁）→ 释放

        aggravated = 破位（顶部确认的必要条件）+ ≥GOT_FAMILY_TOPPING_AGGRAVATED_MIN_CONFIRMS 个
        正交硬确认项（资金 rejected / 严重过热≥0.5 / 老波≥6 / 拥挤）：Phase2 层B 对 aggravated
        家族硬降 buy→watch，非 aggravated 仅层A 缩仓。（修正旧"含破位即加重"的分级塌缩）
        """
        from src.got.schemas import FamilySuppression

        def _int_env(_name: str, _default: int) -> int:
            # env 数值 clamp（防 COOLDOWN/EXIT_HEALTHY=0 使 latch 退化、MIN_CONFIRMS 负数塌缩）：
            # 解析失败回落缺省并告警；成功则强制 ≥1。
            try:
                return max(1, int(os.getenv(_name, str(_default))))
            except (TypeError, ValueError):
                logger.warning("[FTS] %s 解析失败，回落缺省 %d", _name, _default)
                return _default

        cooldown = _int_env("GOT_FAMILY_TOPPING_COOLDOWN", 5)
        exit_healthy = _int_env("GOT_FAMILY_TOPPING_EXIT_HEALTHY", 2)
        mode = os.getenv("GOT_FAMILY_TOPPING_MODE", "shadow")
        # aggravated 硬确认门槛：破位之外还需 ≥N 个正交硬确认项（缺省 1，可收紧到 2）。
        agg_min_confirms = _int_env("GOT_FAMILY_TOPPING_AGGRAVATED_MIN_CONFIRMS", 1)

        today_endurance = {e.name: e for e in mainline_endurance}
        result: list = []

        # ── 今日 topping_risk 的家族：进入/持续压制 ──
        for name, e in today_endurance.items():
            if e.endurance_band != "topping_risk":
                continue
            # aggravated（Phase2 层B 硬降级 buy→watch 触发条件）= 破位（顶部确认的必要条件）
            # + ≥agg_min_confirms 个正交硬确认项。修正原"含破位即加重"导致回测坐实
            # aggravated 子集 == topping_risk 全集、层A(软)/层B(硬) 分级塌缩的问题。
            # 硬确认项复用 MainlineEndurance 结构化字段（均随 unified.json 落盘，次日本地可读）：
            #   资金 rejected（比 missing 更硬）/ 严重过热≥0.5 / 老波 wave_days≥6 / 家族拥挤。
            _break = any("破位信号" in s for s in (e.warning_signals or []))
            _confirms = 0
            if getattr(e, "capital_stance", None) == "rejected":
                _confirms += 1
            if (getattr(e, "max_overheat", 0) or 0) >= 0.5:
                _confirms += 1
            if (getattr(e, "wave_days", 0) or 0) >= 6:
                _confirms += 1
            if getattr(e, "crowded", False):
                _confirms += 1
            aggravated = _break and _confirms >= agg_min_confirms
            members: list[str] = []
            for _tier_members in (e.tiers or {}).values():
                members.extend(_tier_members)
            prev = prev_map.get(name)
            days_suppressed = (int(prev.get("days_suppressed") or 0) + 1) if prev else 0
            result.append(FamilySuppression(
                name=name,
                endurance_band="topping_risk",
                aggravated=aggravated,
                warning_signals=list(e.warning_signals or []),
                members=members,
                days_suppressed=days_suppressed,
                days_healthy=0,
                mode=mode,
            ))

        # ── 前日已压制、今日不再 topping 的家族：latch 释放判定 ──
        for name, prev in prev_map.items():
            today_e = today_endurance.get(name)
            if today_e is not None and today_e.endurance_band == "topping_risk":
                continue  # 已在上一步处理
            new_suppressed = int(prev.get("days_suppressed", 0)) + 1
            new_healthy = int(prev.get("days_healthy", 0)) + 1
            if new_healthy >= exit_healthy or new_suppressed >= cooldown:
                continue  # 释放：连续健康达标 或 触最大锁定上限
            result.append(FamilySuppression(
                name=name,
                endurance_band=prev.get("endurance_band", "topping_risk"),
                aggravated=bool(prev.get("aggravated", False)),
                warning_signals=list(prev.get("warning_signals", []) or []),
                members=list(prev.get("members", []) or []),
                days_suppressed=new_suppressed,
                days_healthy=new_healthy,
                mode=mode,
            ))

        return result

    @staticmethod
    def _has_critical(temporal: list[TemporalInconsistency]) -> bool:
        return any(t.severity == "critical" for t in temporal)

    @staticmethod
    def _collect_key_risks(
        news: NewsContext,
        sentiment: SentimentContext,
        temporal: list[TemporalInconsistency],
        extra_risks: Optional[list[str]] = None,
    ) -> list[str]:
        """汇总关键风险（A6 跨板块风险并入，去重后最多 6 条）。"""
        risks = list(news.key_risks[:3])
        for risk in (extra_risks or []):
            text = str(risk or "").strip()
            if text and text not in risks:
                risks.append(text)

        if sentiment.sentiment_stage == "euphoria":
            risks.append("散户情绪极度狂热（反向信号）")
        elif sentiment.sentiment_stage == "capitulation":
            risks.append("散户极度恐慌（市场可能超跌）")

        for t in temporal:
            if t.severity in ("critical", "high"):
                risks.append(f"[时间异常] {t.description}")

        deduped: list[str] = []
        for risk in risks:
            if risk not in deduped:
                deduped.append(risk)
        return deduped[:6]

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
        narrative: str = "",
    ) -> str:
        """构建推理文本（包含 mini GOT 板块情报详情 + 矛盾警示 + A6 narrative）"""
        actionable = [s for s in sectors if s.classification == "actionable"]
        caution = [s for s in sectors if s.classification == "caution"]
        lines = [
            f"[分层GOT] regime={regime.regime}, risk_budget={regime.risk_budget:.2f}, "
            f"{regime.macro_overlay}. 确认板块: {len(actionable)}个, 观察: {len(caution)}个",
            "",
        ]
        if narrative:
            lines.append(f"【A6 板块格局】{narrative}")
            lines.append("")

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
        glue: Optional[SemanticGlueResult] = None,
    ) -> DataQuality:
        """汇总数据质量"""
        available = ["market", "sector", "capital", "news", "sentiment"]
        failed = []
        missing = []

        if capital.status == "failed":
            failed.append("capital")
            available.remove("capital")
        if news.status == "failed":
            failed.append("news")
            available.remove("news")
        if sentiment.status == "failed":
            failed.append("sentiment")
            available.remove("sentiment")

        # ── 字段级完整性自检（docs/got/archive/agent_glue_plan_0903.md §9 OPEN-5）──
        # lifecycle 全部为 null → _is_hard_exclude 的 late_stage 分支空转
        all_members = []
        for group in (sector.active_mainline or []):
            all_members.extend(group.members or [])
        for detail in (sector.cooling_down or []):
            all_members.append(detail)
        if all_members:
            lifecycle_null_count = sum(
                1 for m in all_members
                if getattr(m, "lifecycle", None) is None
            )
            if lifecycle_null_count == len(all_members):
                missing.append("lifecycle_unavailable")

        # catalyst_calendar 事件为空 → 消息面维度实质缺失
        cal = getattr(news, "catalyst_calendar", None)
        events = getattr(cal, "events", None) if cal else None
        if events is not None and len(events) == 0:
            missing.append("catalyst_events_empty")

        # crowded_families 为空 → 组拥挤拦截无输入（P0-B B1/B3）
        crowded = getattr(sector, "crowded_families", None)
        if crowded is not None and len(crowded) == 0:
            missing.append("crowded_families_empty")

        # 资金流覆盖率（P0-B B1）：sector_flow_all 为空 → 规则侧资金维度全盲
        sf = capital.evidence.sector_flow if capital and capital.evidence else None
        sector_flow_all = sf.get("sector_flow_all") if sf else None
        if not sector_flow_all:
            missing.append("capital_sector_flow_empty")

        # 情绪热榜关联（P0-B B1）：associated_sectors 全空 → 情绪→板块归属空转
        hot_stocks = getattr(sentiment, "hot_stocks", None) or []
        if hot_stocks:
            all_empty = all(
                not getattr(hs, "associated_sectors", None)
                for hs in hot_stocks
            )
            if all_empty:
                missing.append("hot_stocks_sectors_empty")


        return DataQuality(
            available_fields=available,
            missing_fields=missing,
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
        decision_path_hint: str = "",
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

        # 串行调用每个矛盾（统一 runtime 收口 usage/trace）
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
                # 3b enabled：注入跨节点分歧摘要的决策路径提示（decision_path_hint="" 时
                # prompt 逐字节不变——shadow/off 走此空串分支，确保影子期零 LLM 输入变更）
                if decision_path_hint:
                    prompt += (
                        "\n\n## 跨节点分歧摘要（仅供参考，不改变仲裁规则）\n"
                        f"当前分歧类型决策路径提示：{decision_path_hint}\n"
                    )

                response_text = await self._call_llm_async(
                    "你是矛盾仲裁者，只输出 JSON。\n\n" + prompt,
                    temperature=0.3,
                    max_tokens=300,
                    max_retries=2,
                    call_type="node6.t3_arbitrate",
                    timeout=60.0,
                )

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
        5. Regime 刚切换低置信（dwell < 2）
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

    async def _simplified_news_override(
        self,
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

        try:
            content = await self._call_llm_async(
                prompt,
                temperature=0.2,
                max_tokens=300,
                max_retries=1,
                call_type="node6.news_override",
                timeout=60.0,
            )
            result = AggregatorNode._parse_json(content or "")
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
            # WI-P2-9 跨日一致性断言（GOT_CONTEXT_CONSISTENCY，缺省 off）：
            # shadow/enabled 仅在此处追加观测，off 态不进入该分支 → 旧路径逐字节不变。
            _cc_mode = os.environ.get("GOT_CONTEXT_CONSISTENCY", "off").strip().lower()
            if _cc_mode in ("shadow", "enabled"):
                self._shadow_context_consistency(context_dict, today)
        except Exception as e:
            logger.warning(f"[Node 6] 上下文持久化失败: {e}")

        # 2. 更新 EMA 基线（按日幂等：daily_scores 字典覆盖式更新）
        try:
            from src.got.persistence import EMA_MAX_DAYS, compute_ema_stats_from_daily
            prev_baseline = load_ema_baseline()

            # 初始化或迁移 daily_scores
            if prev_baseline is None:
                daily_scores = {}
            else:
                daily_scores = prev_baseline.get("daily_scores", {})
                # 冷启动兼容：旧文件无 daily_scores 时，用 regime_score/score_std 作种子
                if not daily_scores and prev_baseline.get("regime_score") is not None:
                    legacy_date = prev_baseline.get("last_updated", today.isoformat())
                    daily_scores[legacy_date] = prev_baseline["regime_score"]
                    logger.info(f"[Node 6] EMA 基线迁移：旧 regime_score 迁入 {legacy_date}")

            # 覆盖当天（末版为准）
            daily_scores[today.isoformat()] = regime_score

            # 落盘前按 N=EMA_MAX_DAYS 截断（保留最近 60 个交易日），防止无界增长。
            # compute_ema_stats_from_daily 的计算窗口同为最近 max_days，
            # 故先截断不改变 regime_score / score_std / sample_count 结果。
            if len(daily_scores) > EMA_MAX_DAYS:
                for stale_date in sorted(daily_scores)[:-EMA_MAX_DAYS]:
                    del daily_scores[stale_date]

            # 重算统计量（纯函数）
            new_score, new_std, sample_count = compute_ema_stats_from_daily(daily_scores)

            new_baseline = {
                "daily_scores": daily_scores,
                "regime_score": new_score,
                "score_std": new_std,
                "last_updated": today.isoformat(),
                "sample_count": sample_count,
            }

            save_ema_baseline(new_baseline)
        except Exception as e:
            logger.warning(f"[Node 6] EMA 基线更新失败: {e}")

    @staticmethod
    def _shadow_context_consistency(context_dict: dict, today: date) -> None:
        """WI-P2-9 shadow：JSON 上下文 actionable 板块 vs Node2 DB 主线族当日记录。

        只报警不改写（enabled 同 shadow；「统一真相源 + 冲突拦截」属后续批次，
        需维护者裁决口径）。任何异常内部兑底，绝不向外抛出——不得把 shadow
        观测变成 save_current_context 之后新的失败面。
        """
        try:
            from src.got.persistence import (
                cross_day_consistency_diff,
                query_db_mainline_family_sector_names,
            )

            db_names = query_db_mainline_family_sector_names(today)
            diff = cross_day_consistency_diff(
                list(context_dict.get("confirmed_sectors") or []), db_names,
            )
            if diff["j_only"] or diff["db_only"]:
                logger.warning(
                    "[Node 6][P2-9 shadow] 跨日一致性差异: "
                    f"JSON有/DB主线族无={diff['j_only']}, "
                    f"DB主线族有/JSON无={diff['db_only']}"
                )
        except Exception as e:
            logger.warning(f"[Node 6][P2-9 shadow] 一致性断言异常（已吞）: {e}")

    # =========================================================================
    # LLM 调用工具
    # =========================================================================

    async def _call_llm_async(
        self,
        prompt: str,
        *,
        temperature: float,
        max_tokens: int,
        max_retries: int = 3,
        call_type: str = "got_node6_unknown",
        timeout: float = 60.0,
        evidence_refs: Optional[list] = None,
    ) -> str:
        """异步 LLM 调用（统一 runtime：timeout/重试/usage/trace 一次收口）。

        返回空串表示本次任务失败（调用失败/截断/重试耗尽）；A6 等调用方按既有
        fail-fast/degrade 语义处理。C-P13 管线继续通过本薄适配复用同一 runtime。
        """
        from src.got.catalyst_config import LLM_CALL_TIMEOUT_S

        runtime = self._ensure_runtime()
        effective_timeout = timeout if timeout > 0 else LLM_CALL_TIMEOUT_S
        step = await runtime.call_text(
            task_id=call_type,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
            timeout=effective_timeout,
            evidence_refs=evidence_refs,
        )
        if step.status == "ok" and isinstance(step.output, str) and step.output.strip():
            return step.output.strip()
        if step.status == "failed":
            logger.warning("[Node 6] LLM 调用未成功（%s）: %s", call_type, step.error)
        return ""

    @staticmethod
    def _parse_json(text: str) -> dict:
        """从 LLM 输出中提取 JSON。

        空内容单独报错：否则 json.loads 抛的 "Expecting value: line 1 column 1"
        会把"LLM 没返回内容"误报成"JSON 格式不对"，掩盖真实故障点
        （_call_llm_async 重试耗尽后返回空串）。
        """
        content = (text or "").strip()
        if not content:
            raise ValueError("LLM 返回空内容（重试已耗尽），无可解析的 JSON")
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if match:
            content = match.group(1)
        return json.loads(content)

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

        # Node 3: 资金流非空率（Q5-1: 分母改为交集，删除 v!=0 误判）
        evidence = getattr(capital, "evidence", None)
        sector_flow = getattr(evidence, "sector_flow", None) or {}
        flow_5d_map = sector_flow.get("sector_flow_all_5d", {})
        flow_1d_map = sector_flow.get("sector_flow_all", {})
        # Q5-1a: 分母用交集（排除 5d 独有的盲区板块虚增合格数）
        common_keys = set(flow_5d_map.keys()) & set(flow_1d_map.keys())
        total_boards = max(len(common_keys), 1)
        # Q5-1b: 只检查 None，不检查 v!=0（真实零流板块不应被当缺失）
        non_null_5d = sum(1 for k in common_keys if flow_5d_map.get(k) is not None)
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
