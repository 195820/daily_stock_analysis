"""
GOT 节点输入输出 Pydantic Schema

设计理念：
- 每个节点输出一个 Context 对象
- 包含 DataQuality 字段追踪（参考 AnalysisContextPack）
- 支持降级场景（缺失维度记录在 missing_fields）
"""

from datetime import date, datetime
from typing import Any, Optional
from pydantic import BaseModel, Field

# B2 词表同源：CONTROLLED_FAMILIES 从单一权威源导入
from src.got.family_keywords import CONTROLLED_FAMILIES  # noqa: E402
# Phase 4b：市场结构上下文模型（market_theme 不反向依赖 schemas，无循环）
from src.got.market_theme import MarketThemeContext  # noqa: E402


# =============================================================================
# 基础类型
# =============================================================================

class DataQuality(BaseModel):
    """字段级质量追踪"""
    available_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    stale_fields: list[str] = Field(default_factory=list)
    fetch_failed_fields: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.now)
    # 降级可见性（Node 2 v2 新增）
    degradation_summary: Optional[str] = Field(
        default=None,
        description="人可读的降级摘要，输出到 reasoning.md 顶部",
    )
    effective_dimensions: Optional[dict[str, bool]] = Field(
        default=None,
        description="各评分维度是否有效（如 breadth_health 依赖 up_count/down_count）",
    )
    board_fetch_stats: Optional[dict[str, int]] = Field(
        default=None,
        description="板块获取统计 {total, success, failed}",
    )
    # ── Phase 2 决策审计字段（审核修复计划 W9/3.3 新增，向后兼容）──
    sector_verify_degraded: Optional[bool] = Field(
        default=None,
        description="板块验证 LLM 仲裁不可用，pending 候选改走保守分支",
    )
    sector_verify_pending_count: Optional[int] = Field(
        default=None,
        description="板块验证降级日待仲裁候选数",
    )
    sector_available_false_count: Optional[int] = Field(
        default=None,
        description="sector_available=False 的候选数（板块标签被清空/验证降级）",
    )
    sector_vetoed_count: Optional[int] = Field(
        default=None,
        description="vetoed 中因板块失效被否决的候选数",
    )


class Signal(BaseModel):
    """通用信号"""
    trend: str = Field(..., description="bullish / bearish / neutral")
    score: float = Field(..., ge=-1.0, le=1.0)


# =============================================================================
# Node 1: 大盘分析
# =============================================================================

class TimeframeSignals(BaseModel):
    """多时间框架信号"""
    short_term: Signal = Field(..., description="5日")
    medium_term: Signal = Field(..., description="20日")
    long_term: Signal = Field(..., description="60日")


class TimeframeGOTResult(BaseModel):
    """时间框架 mini GOT 输出"""
    unified_trend: str = Field(..., description="统一趋势")
    unified_score: float = Field(..., ge=-1.0, le=1.0)
    contradiction_type: Optional[str] = None
    timeframe_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    llm_reasoning: Optional[str] = None


class RegimeMetadata(BaseModel):
    """市场状态元信息"""
    adx_value: float = Field(..., ge=0.0, le=100.0)
    regime: str = Field(..., description="strong_trending / mild_trending / ranging")
    volatility_regime: str = Field(..., description="extreme_high / normal / extreme_low")
    weights_used: dict[str, float] = Field(default_factory=dict)


class TechGrowthSignal(BaseModel):
    """科技成长方向信号（核心）"""
    trend: str
    score: float = Field(..., ge=-1.0, le=1.0)
    constituents: dict[str, float] = Field(default_factory=dict)  # chinext / star50
    consistency: str = Field(default="aligned", description="aligned / divergent")
    signals: TimeframeSignals
    timeframe_got: TimeframeGOTResult
    regime_metadata: RegimeMetadata


class StyleSignal(BaseModel):
    """风格信号（中小盘/大盘蓝筹）"""
    trend: str
    score: float = Field(..., ge=-1.0, le=1.0)
    signals: TimeframeSignals


class KeyLevels(BaseModel):
    """支撑/阻力位"""
    short_term: dict[str, float] = Field(default_factory=dict)
    mid_term: dict[str, float] = Field(default_factory=dict)
    volume_profile: Optional[dict[str, float]] = None


class MarketStateSignal(BaseModel):
    """Node 1 综合市场状态条件变量（2026-08-19 新增）。

    把 Node 1 已算的散字段聚合成一个可传导的下游条件变量，
    供 Node 2（板块选股强度）和 Node 6（聚合权重）直接消费，
    避免下游各自重新推断市场状态（参考东吴证券 2026-08-18 金工报告：
    市场状态应作为跨股票共享的条件变量，重塑下游表示与合成）。

    attack_power 是核心可操作输出，代表"当前该不该进攻、进攻多强"：
    - ≈0   : 暴跌/深度熊市，几乎不可进攻（防守优先）
    - 0.3~0.7: 震荡市，谨慎进攻（维持现状）
    - ≈1   : 强趋势/牛市，可积极进攻
    """
    attack_power: float = Field(..., ge=0.0, le=1.0, description="进攻强度 0~1（Node2/Node6 主消费量）")
    regime: str = Field(default="transition", description="市场 regime（transition/bull/bear）")
    mini_got_trend: str = Field(default="neutral", description="mini-GOT 统一趋势")
    mini_got_score: float = Field(default=0.0, ge=-1.0, le=1.0, description="mini-GOT 统一评分")
    mini_got_confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="mini-GOT 置信度")
    timeframe_consistency: str = Field(default="aligned", description="短/中/长时间框架一致性")
    contradiction_type: Optional[str] = Field(default=None, description="mini-GOT 矛盾类型")
    risk_state: str = Field(default="normal", description="风险状态 normal/high/extreme_high")
    realized_volatility: Optional[float] = Field(default=None, description="已实现波动率（年化）")
    crash_override: bool = Field(default=False, description="暴跌强制防守开关（普跌+最弱指数<-3%）")


class MarketContext(BaseModel):
    """Node 1 输出：大盘分析上下文"""
    tech_growth: TechGrowthSignal
    market_state: Optional[MarketStateSignal] = Field(
        default=None,
        description="综合市场状态条件变量（下游 Node2/Node6 的 main 消费入口）",
    )
    mid_small_cap: StyleSignal
    large_cap: StyleSignal
    position_limit: float = Field(..., ge=0.0, le=1.5, description="仓位上限（新版：0.2 ~ 1.5）")
    confidence: float = Field(..., ge=0.0, le=1.0)
    key_levels: KeyLevels
    reasoning: Optional[str] = None
    data_quality: DataQuality = Field(default_factory=DataQuality)
    today_change: dict[str, float] = Field(
        default_factory=dict,
        description="T-0 实时涨跌幅（腾讯接口）: {\"sh\": -1.08, \"sz\": -1.43, \"cy\": -1.38}",
    )
    market_breadth: Optional[dict[str, Any]] = Field(
        default=None,
        description="市场宽度（涨跌家数）: {up_count, down_count, limit_up_count, limit_down_count}",
    )
    # 三层仓位系统中间值（用于调试和透明度）
    realized_volatility: Optional[float] = Field(
        default=None,
        description="实现波动率（年化，用于波动率调整）",
    )
    vol_adjusted_position: Optional[float] = Field(
        default=None,
        description="波动率调整后仓位（Layer 1 输出）。**参考值，当前不参与 `final_position_limit`**",
    )
    regime_scale: Optional[float] = Field(
        default=None,
        description="状态缩放因子（Layer 2 输出，0.2 ~ 1.0）。**参考值，当前不参与 `final_position_limit`**",
    )


# =============================================================================
# Node 2: 板块分析
# =============================================================================

class RRGQuadrant(BaseModel):
    """RRG 四象限分类（Node 2 状态机核心）。

    RS-Ratio: 板块 strength_score 在截面中的百分位排名（趋势强度）
    RS-Momentum: RS-Ratio 的时序变化率（动量方向）

    ⚠️ rs_ratio / rs_momentum 为 3 日 EMA 平滑值（2026-08-17 状态机校准）：
    - 日频截面排名受单日噪音影响大（StockCharts: 日频 RRG 是周频趋势的"短期 hiccup"）
    - EMA 吸收单日截面挤压，防止黄金 08-13 类"单日被其他板块挤压→一夜蒸发"
    - 原始值保留在 rs_ratio_raw/rs_momentum_raw 供诊断

    四象限：
    - leading:     ratio≥0.5, momentum≥0.5 → 趋势强且加速 → active_mainline
    - improving:   ratio<0.5,  momentum≥0.5 → 趋势弱但改善 → 埋伏/启动候选
    - weakening:   ratio≥0.5, momentum<0.5  → 趋势强但减速 → 高位降温/衰竭
    - lagging:     ratio<0.5,  momentum<0.5  → 趋势弱且恶化 → neutral

    论文依据：
    - Minervini 2016: Relative Rotation Graph 四象限
    - Janssen 2020: RS-Momentum 领先 RS-Ratio（早期预警）
    - Optuma 2万次旋转统计: Leading 92% 转 Weakening → 主线只能先降温/衰竭，无 主线→埋伏 边
    """
    rs_ratio: float = Field(..., ge=0.0, le=1.0, description="RS-Ratio 3日EMA: 趋势强度百分位")
    rs_momentum: float = Field(..., ge=0.0, le=1.0, description="RS-Momentum 3日EMA: 动量百分位")
    quadrant: str = Field(..., description="leading / improving / lagging / weakening")
    rs_ratio_raw: Optional[float] = Field(default=None, description="RS-Ratio 当日原始值（未平滑，诊断用）")
    rs_momentum_raw: Optional[float] = Field(default=None, description="RS-Momentum 当日原始值（未平滑，诊断用）")


class LaunchSignal(BaseModel):
    """启动信号（Node 2 v3 降级为诊断指标，不再驱动状态）"""
    triggered: bool
    level: str = Field(..., description="strong_launch / weak_launch / sustained / none")
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    dimensions: dict[str, float] = Field(default_factory=dict)


class LifecycleInfo(BaseModel):
    """生命周期信息"""
    phase: str = Field(..., description="launch / acceleration / mature / late_stage / mature_exhausted")
    days_since_launch: Optional[int] = None
    thresholds: dict[str, int] = Field(default_factory=dict)


class OverheatSeverity(BaseModel):
    """过热严重度（渐进式评分，替代旧的 dict[str, bool]）。

    severity 计算：加权 sum of all signals（0~1）
    level 判定：
      severity > 0.5 → "exhausted"（踢出主线框）
      severity 0.3~0.5 → "warning"（过热警告，降 confidence）
      severity < 0.3 → "normal"

    9 维度（v2 升级）：
    - D1-D6: 背离型见顶（MACD背离/量能背离/宽度极端/换手极端/A-D线/天量滞涨）
    - D7-D9: 高潮型见顶（龙头极端/主线疲劳/成分股追高）

    论文依据：
    - MACD 背离渐进式（Kang 2023）
    - A/D Line distribution（72% 重大下跌前出现）
    - 天量滞涨 = 分配信号（Hsu 2016）
    - Minervini 领涨股恶化模式（龙头极端强势 = 高潮信号）
    - O'Neil Stall Day（高位放量滞涨 = 机构派发）
    """
    # 背离型维度（D1-D6）
    divergence_score: float = Field(default=0.0, ge=0.0, le=1.0, description="D1: MACD 背离强度")
    volume_divergence_score: float = Field(default=0.0, ge=0.0, le=1.0, description="D2: 量能背离强度")
    breadth_score: float = Field(default=0.0, ge=0.0, le=1.0, description="D3: 宽度极端强度")
    turnover_score: float = Field(default=0.0, ge=0.0, le=1.0, description="D4: 换手极端强度")
    ad_distribution: bool = Field(default=False, description="D5: A/D Line 分配信号")
    volume_stall: bool = Field(default=False, description="D6: 天量滞涨信号")
    # 高潮型维度（D7-D9, v2 新增）
    leader_score: float = Field(default=0.0, ge=0.0, le=1.0, description="D7: 龙头极端行为（板块涨幅代理）")
    fatigue_score: float = Field(default=0.0, ge=0.0, le=1.0, description="D8: 主线疲劳（mainline_days ≥ 5 且仍 acceleration）")
    chase_score: float = Field(default=0.0, ge=0.0, le=1.0, description="D9: 成分股追高（breadth_health 代理）")
    severity: float = Field(default=0.0, ge=0.0, le=1.0, description="加权总严重度")
    level: str = Field(default="normal", description="normal / warning / exhausted")


class SectorDetail(BaseModel):
    """板块详情"""
    code: str
    name: str
    state: str = Field(..., description="active_mainline / high_position_cooling / exhausted_mainline / ambush / defensive_rotation / neutral")
    strength_score: float = Field(..., ge=0.0, le=100.0)
    indicators: dict[str, float] = Field(default_factory=dict)
    launch_signal: Optional[LaunchSignal] = None
    lifecycle: Optional[LifecycleInfo] = None
    tier: Optional[str] = None  # S / A / B
    mainline_quality: Optional[str] = Field(
        default=None,
        description="strong / weak — B级主线标记为weak，下游选股系统可打折",
    )
    recommended_action: Optional[str] = None
    today_change: Optional[float] = Field(
        default=None,
        description="T-0 板块涨跌幅（mootdx 成分股中位数），如 -3.38",
    )
    coherence_score: Optional[float] = Field(
        default=None,
        description="板块一致性评分（与同簇成员的平均相关性）。>0.7=高一致性, <0.4=噪声/混合概念",
    )
    # 趋势追踪字段（跨日记忆）
    strength_trajectory: Optional[list[float]] = Field(
        default=None,
        description="最近 7 天强度分数轨迹",
    )
    trend_direction: Optional[str] = Field(
        default=None,
        description="increasing / stable / decreasing（基于 7 天趋势）",
    )
    mainline_days: Optional[int] = Field(
        default=None,
        description="连续主线天数（用于检测 late_stage）",
    )
    # ── 波次计数器（v3.5 S2：以回撤结构判波，支撑生命周期折扣/续航评估）──
    wave_days: Optional[int] = Field(
        default=None,
        description="当前波次持续天数（出框间隙≥2日+回撤≥10分→重置，用于生命周期折扣）",
    )
    wave_start_date: Optional[date] = Field(
        default=None,
        description="当前波次起始日期",
    )
    wave_peak_strength: Optional[float] = Field(
        default=None,
        ge=0.0, le=100.0,
        description="当前波次峰值强度（滚动更新，用于计算回撤幅度）",
    )
    frame_group: Optional[str] = Field(
        default=None,
        description="所属产业主题组名（如 AI产业链 / 黄金 / 华为系）",
    )
    overheat_severity: Optional[float] = Field(
        default=None,
        ge=0.0, le=1.0,
        description="过热严重度（0~1）。>0.5=exhausted, 0.3~0.5=warning, <0.3=normal",
    )
    rrg: Optional[RRGQuadrant] = Field(
        default=None,
        description="RRG 四象限分类（Node 2 v3 状态机核心）",
    )
    # ── MEG 动能衰竭三态（§7.3 层1信号，2026-10-08 回测证真后新增）──
    momentum_state: Optional[str] = Field(
        default=None,
        description="动能衰竭三态：snap(两日断裂)/stall(滞涨)/thrust(推进)，基于 rrg_momentum_ema 两日衰减率",
    )
    rs_mom_decay_2d: Optional[float] = Field(
        default=None,
        description="RS-Momentum EMA 两日衰减率 (ema[t]-ema[t-2])/ema[t-2]，≤-0.30 判 snap",
    )
    near_miss_conditions: Optional[list[str]] = Field(
        default=None,
        description="埋伏近失条件列表（差 1 条通过埋伏闸门时填充，供 Node 6 蓄水池观察）",
    )
    exit_pressure_days: Optional[int] = Field(
        default=None,
        description="退出压力计数器（状态机吸收原框 consecutive_low_days，日粒度去重）",
    )
    break_signals: Optional[dict] = Field(
        default=None,
        description="破位判据输入快照（price_chg_5d/above_ma20/high20_pos/overheat_level/score_drop），"
                    "落盘供状态机决策离线复现（P2-5，2026-09-02 审核新增）",
    )


class MainlineGroup(BaseModel):
    """主线组（产业主题级）"""
    group_name: str
    group_score: float = Field(..., description="成员最高分")
    tier: str = Field(..., description="成员最高 tier（S/A/B）")
    mainline_quality: str = Field(..., description="strong / weak")
    members: list[SectorDetail] = Field(default_factory=list)


class ThemeFamily(BaseModel):
    """主线家族（产业主题聚合视图）

    将多个概念板块按关键词归入同一主题家族，
    家族评分 = 成员百分位均值，共振度 = 活跃成员占比。
    """
    family_name: str = Field(..., description="家族名（AI科技/机器人/新能源/医药/金融/周期资源/军工/消费/其他）")
    family_score: float = Field(..., ge=0.0, le=100.0, description="成员百分位均值")
    family_resonance: float = Field(..., ge=0.0, le=1.0, description="活跃成员占比")
    theme_status: str = Field(
        ...,
        description="主线核心 / 主线扩散 / 独立异动 / 修复观察 / 高位降温",
    )
    member_boards: list[str] = Field(default_factory=list, description="命中的板块名称")
    member_count: int = Field(default=0, description="命中板块数")
    mixed_driver: bool = Field(
        default=False,
        description="家族内是否存在不同驱动逻辑的子群（二次聚类检测到 ≥2 个子簇且子簇间相关性 <0.3）",
    )


class SectorContext(BaseModel):
    """Node 2 输出：板块分析上下文"""
    # ── 概念板块视角（短线题材）──
    active_mainline: list[MainlineGroup] = Field(default_factory=list)
    exhausted_mainline: list[SectorDetail] = Field(default_factory=list)
    ambush: list[SectorDetail] = Field(default_factory=list)
    near_miss_ambush: list[SectorDetail] = Field(
        default_factory=list,
        description="近失埋伏板块（差 1 条闸门未通过，供 Node 6 蓄水池观察）",
    )
    defensive_rotation: list[SectorDetail] = Field(default_factory=list)
    cooling_down: list[SectorDetail] = Field(
        default_factory=list,
        description="高位降温（前主线回调但未破位，state=high_position_cooling）— 状态机 2026-08-17 新增",
    )
    top_by_score: list[SectorDetail] = Field(
        default_factory=list,
        description="评分最高的 N 个板块（参考用，不代表达标）",
    )

    # ── 新增：行业板块视角（中期趋势）──
    industry_top: list[SectorDetail] = Field(
        default_factory=list,
        description="行业板块 Top 10（按百分位评分排名，独立于概念板块）",
    )

    # ── 新增：主线家族视角 ──
    theme_families: list[ThemeFamily] = Field(
        default_factory=list,
        description="主线家族（~10 个，含家族强度和题材地位）",
    )

    market_direction: str = Field(default="分化", description="分化 / 普涨 / 普跌")
    notes: list[str] = Field(default_factory=list, description="附加说明（如评分不足警告）")
    reasoning: Optional[str] = None
    data_quality: DataQuality = Field(default_factory=DataQuality)
    confidence_level: str = Field(
        default="high",
        description="评分可信度：high / medium / low（>30% 板块获取失败时降级为 low）",
    )
    status: str = Field(
        default="normal",
        description="节点健康度：normal / degraded / failed（degraded 时下游走降级路径）",
    )

    # ── P0-1: 全量板块→强度映射（供 Node 6 升格消费）──
    strength_lookup: dict[str, float] = Field(
        default_factory=dict,
        description="板块名→当日强度评分（覆盖全量 analyzed 板块，Node 6 升格时查表）",
    )
    # ── 过热降权链路（Part 1: 过热评分降权）──
    overheat_lookup: dict[str, float] = Field(
        default_factory=dict,
        description="板块名→当日过热严重度(0~1)，覆盖全部参与评分板块，Node 6 透传到 SectorIntel",
    )
    crowded_families: list[str] = Field(
        default_factory=list,
        description="拥挤家族名称列表（家族内 >=3 板块 severity>0.3），用于追加降权；"
                    "口径为工程启发式（B3 裁决 2026-09-28：维持现状+口径标注，不停产不新增接线）",
    )
    frame_group_lookup: dict[str, str] = Field(
        default_factory=dict,
        description="板块名→家族名（覆盖全量 analyzed 板块，合成 detail 回退时查表），用于拥挤降权",
    )
    # Q2 audit: THS 板块热度（供 Node 6 G2/G3 闸门消费）
    ths_hot_sectors: dict[str, float] = Field(
        default_factory=dict,
        description="同花顺板块热度 {板块名: 热度值}，供 Node 6 埋伏通道 G2/G3 闸门消费（替代恒空的 associated_sectors）"
    )
    # ── WI-P1-7(b) 家族 embedding 兜底留痕（【架构计划】§6，2026-10-06）──
    family_embed_shadow: Optional[dict] = Field(
        default=None,
        description="「其他」板块的 embedding 家族兜底观测留痕: {mode, unmatched_boards, "
                    "overrides, would_flip_count, threshold, cache_hits, skipped_*}。"
                    "shadow 态只记「若兜底会改判的板块清单」，frame_group 逐字节不变；"
                    "off 态恒为 None（缺省，不触网不算向量）；enabled 才真改判。"
                    "阈值 score/margin 为未标定工程启发式，转正前由 shadow 读数收紧。"
                    "【按 #5a 先例：不进 R-B8/幂等不变组，按设计 SKIP】",
    )



# =============================================================================
# Node 3: 资金面分析
# =============================================================================

class CapitalEvidence(BaseModel):
    """资金面证据（实时 T+0 维度）"""
    sector_flow: Optional[dict] = None        # 概念/行业板块资金流
    northbound_realtime: Optional[dict] = None  # 北向资金分钟级实时（备用，停披后全 0 自动降级）
    dde: Optional[dict] = None                # 已废弃（缺陷③A：DDE 随东财封锁失效摘除，仅为向后兼容保留字段，不再填充）
    individual_main_force: Optional[dict] = None  # 个股主力聚流（T-1 备用）


class CapitalContext(BaseModel):
    """Node 3 输出：资金面分析上下文"""
    status: str = Field(..., description="normal / degraded / failed")
    institutional_attitude: str = Field(..., description="bullish / bearish / neutral")
    institutional_score: float = Field(..., ge=-1.0, le=1.0)
    # 2026-10-14 裁决（backlog §1.J 二）删除 `confidence_multiplier`：DDE 摘除 + 北向永久豁免后
    # 有效维度总数=1，该字段退化为 1.0/0.0 二值常量（0.0 只伴随 status=failed 整块降级），
    # 零决策消费者且接进公式只做无谓缩放。降级语义由 `status` + `single_dimension` 承载。
    # 历史 capital.json 多出的该键经 Pydantic v2 缺省 extra='ignore' 静默忽略，读侧不炸。
    single_dimension: bool = Field(
        default=False,
        description="机构态度仅由单一可用资金维度支撑（证据维度单薄，报告需诚实标注单维口径）",
    )
    evidence: CapitalEvidence = Field(default_factory=CapitalEvidence)
    missing_dimensions: list[str] = Field(default_factory=list)
    reasoning: Optional[str] = None
    data_quality: DataQuality = Field(default_factory=DataQuality)


# =============================================================================
# Node 4: 消息面分析
# =============================================================================

class NewsEvent(BaseModel):
    """新闻事件"""
    event_type: str = Field(..., description="policy / industry / global")
    title: str
    summary: str
    impact_level: str = Field(..., description="high / medium / low")
    sentiment: str = Field(..., description="positive / negative / neutral")
    affected_sectors: list[str] = Field(default_factory=list)
    affected_stocks: list[str] = Field(default_factory=list, description="事件中明确提到的个股名称或代码")
    timestamp: Optional[str] = None
    source_count: int = Field(default=1, ge=1)
    source_type: str = Field(default="search", description="事件级：professional/retail_forum（LLM 提取）；采集源级：sina/cls/wallstreetcn/eastmoney/newsnow/nga_forum/ths_hot（E3 死枚举对齐，原记载 cctv/caixin/search 无生产者）")
    # 生命周期追踪字段（跨日记忆）
    lifecycle_state: Optional[str] = Field(default=None, description="emerging / fermenting / peak / fading / archived")
    mention_count: Optional[int] = Field(default=None, description="跨日累计出现次数")
    first_seen: Optional[str] = Field(default=None, description="首次出现日期（YYYY-MM-DD）")
    # A1（竞品补强批次 A）证据链：来源链接与证据档（默认 None，向后兼容）。
    # ⚠ source_url 的生产端回填（news_analysis 三步确定性回连 + news_feeds 透传）为后续子项。
    source_url: Optional[str] = Field(default=None, description="新闻出处链接（采集层透传）")
    evidence_tier: Optional[str] = Field(default=None, description="证据档：confirmed/reported/rumor")


class CatalystEvent(BaseModel):
    """催化剂事件 — 板块级前瞻日历的最小单元"""
    event_id: str = Field(..., description="MD5(source + event_type + sector + date)")
    event_date: date = Field(..., description="事件日期")
    event_type: str = Field(..., description="earnings / lockup / macro / narrative / global")
    title: str = Field(..., description="事件描述")
    sector: str = Field(..., description="映射到的 A 股板块名")
    impact_level: int = Field(default=1, ge=1, le=3, description="1=★ 2=★★ 3=★★★")
    direction: str = Field(default="positive", description="价格方向：positive / negative / binary（二元事件）。与 category_tag（事件类型 drift/fade/binary）正交，不得填 drift/fade")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="< 0.7 不入评分")
    consensus_expectation: str = Field(default="", description="共识预期（必填）")
    source: str = Field(..., description="akshare_earnings / akshare_lockup / macro_rule / llm_narrative / global_map")
    category_tag: str = Field(default="", description="drift / fade / binary（退出策略分型依据）")
    is_global: bool = Field(default=False, description="是否来自全球事件映射")
    source_news: Optional[str] = Field(default=None, description="来源新闻标题（LLM 叙事事件用于回声检测）")
    # A1（竞品补强批次 A）证据链：证据档与来源链接（带默认值保归档/老库回读向后兼容）。
    evidence_tier: str = Field(default="reported", description="证据档：confirmed(交易所一手/宏观)|reported(权威传导)|rumor(无源传闻)")
    source_url: Optional[str] = Field(default=None, description="事件出处链接（一期多为空）")
    affected_sectors: list[str] = Field(
        default_factory=list,
        description="T5: LLM 语义对齐后的受影响板块列表（叙事/global 事件多板块扇出）。"
                    "【P2-1 长期口径（裁决 #8，2026-09-28）】不区分正负面：扇出与加分对正/负面事件同向处理，"
                    "已明示接受为长期口径，不升级 list[dict]",
    )
    created_at: Optional[date] = None


class CatalystCalendar(BaseModel):
    """板块级催化剂日历 — 前瞻 2 周窗口"""
    status: str = Field(default="normal", description="normal / degraded / failed")
    target_date: date = Field(..., description="日历基准日")
    window_days: int = Field(default=14, description="前瞻窗口（日历日，约 10 交易日）")
    events: list[CatalystEvent] = Field(default_factory=list, description="按 event_date 排序")
    last_full_refresh: Optional[date] = None
    refresh_mode: str = Field(default="full", description="full / incremental")
    total_trading_days_since_refresh: int = 0


class NewsContext(BaseModel):
    """Node 4 输出：消息面分析上下文"""
    status: str = Field(default="normal", description="normal / degraded / failed")
    overall_impact: str = Field(..., description="positive / negative / neutral")
    impact_score: float = Field(..., ge=-1.0, le=1.0)
    major_events: list[NewsEvent] = Field(default_factory=list)
    concept_suggestions: list[dict] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    reasoning: Optional[str] = None
    data_quality: DataQuality = Field(default_factory=DataQuality)
    catalyst_calendar: Optional[CatalystCalendar] = Field(default=None, description="板块级催化剂日历（P1 新增）")


# =============================================================================
# Node 5: 情绪面分析
# =============================================================================

class HotStock(BaseModel):
    """热门股票"""
    stock_code: str
    stock_name: str
    mention_count: int
    sentiment: str = Field(default="positive")
    associated_sectors: list[str] = Field(
        default_factory=list,
        description="【闲置口径（2026-09-28 核，OPEN-D2 重判）】生产端恒空（sentiment_analysis.py 两写入点均 []）；"
                    "原消费方 Node 6 G2 注意力闸已于 audit_20260922 Q2（09-23）改用 "
                    "SectorContext.ths_hot_sectors，字段保留仅为归档兼容",
    )
    mention_source: str = "unknown"  # D-P8: 口径声明。当前生产者仅 eastmoney_attention_index / xueqiu_attention 两个（sentiment_analysis.py）；
    # nga_mention 为预留值，当前无生产者（A6 退役后 NGAS 源不在链路）——**已拍板不接线、维持预留**
    # （裁决 #2，2026-09-28：接无源枚举=死代码，属 backlog §9 缺陷谱系）


class SentimentStageState(BaseModel):
    """情绪阶段状态机（v2.1 跨日记忆）

    反身性 5 阶段的跨日连续性追踪：
    - dwell_days：当前阶段持续天数（情绪持续性信号）
    - momentum_direction：升温/降温/稳定（转换方向比绝对水平更重要）
    - stage_transitions：近 10 日阶段序列（高频切换 → 震荡标记）
    - contrarian_alert：连续升温到极端 / 极端阶段持续过久 → 反向信号

    学术依据：Han et al. (2022) 情绪持续期携带信息；Da et al. (FEARS) 高频冲击预测短期反转
    """
    current_stage: str = Field(..., description="euphoria / optimism / neutral / pessimism / capitulation")
    dwell_days: int = Field(default=1, description="当前阶段持续交易日数")
    previous_stage: Optional[str] = Field(default=None, description="前一交易日的阶段")
    momentum_direction: str = Field(default="stable", description="warming / cooling / stable")
    stage_transitions: list[str] = Field(default_factory=list, description="近 10 日阶段序列（升序）")
    contrarian_alert: bool = Field(default=False, description="反向信号（连续升温到极端或极端阶段超时）")


class SentimentContext(BaseModel):
    """Node 5 输出：情绪面分析上下文"""
    status: str = Field(default="normal", description="normal / degraded / failed")
    overall_sentiment: str = Field(..., description="bullish / bearish / neutral")
    sentiment_score: float = Field(..., ge=-1.0, le=1.0)
    sentiment_stage: str = Field(..., description="euphoria / optimism / neutral / pessimism / capitulation")
    # 2026-10-15 拍板（backlog OPEN-2）：`trading_signal` / `signal_confidence` 已删除——
    # 53 归档日零决策消费（仅归档渲染），决策意图由 sentiment_stage + stage_state 承载。
    forum_mood: str = Field(default="中性")
    hot_stocks: list[HotStock] = Field(default_factory=list)
    hot_topics: list[str] = Field(default_factory=list)
    retail_attention_count: int = Field(default=0, description="散户关注热度（东财关注指数总量）")
    market_temperature: float = Field(default=0.5, ge=0.0, le=1.0, description="市场温度（0=冰点, 1=沸点）")
    news_drift: str = Field(default="neutral", description="消息面偏离（positive/negative/neutral）")
    reasoning: Optional[str] = None
    data_quality: DataQuality = Field(default_factory=DataQuality)
    # v2 新增：恐贪指数 + 情绪分歧度 + 反向信号
    fear_greed_index: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="恐贪指数（0=极度恐惧, 1=极度贪婪）",
    )
    sentiment_dispersion: float = Field(
        default=0.0,
        description="情绪分歧度（低=一致, 高=分歧。一致看多→反向信号）",
    )
    sentiment_dispersion_state: Optional[str] = Field(
        default=None,
        description="分歧度读数状态（2026-10-15 拍板拆态留观）："
        "no_data=无有效读数（0.0 是缺数据不是真一致）/ normal=有效读数 / "
        "high_consensus=真·高一致（dispersion<0.35 且极端分位）",
    )
    contrarian_signal: bool = Field(
        default=False,
        description="反向信号（唯一来源=stage_state.contrarian_alert；"
        "2026-10-15 拍板归并：dispersion 触发分支已退场，仅观测不进决策）",
    )
    sentiment_momentum: float = Field(
        default=0.0,
        description="情绪动量（当前score与EMA5偏差，正=升温, 负=降温）",
    )
    # ── v2.1 跨日情绪状态机 ──
    stage_state: Optional[SentimentStageState] = Field(
        default=None,
        description="情绪阶段状态机（dwell/方向/反向信号）",
    )
    persistence_regime: Optional[str] = Field(
        default=None,
        description="情绪持续性：high（AR(1)>0.7, trending）/ low（<0.3, mean-reverting）/ None",
    )


# =============================================================================
# Node 6: 聚合节点
# =============================================================================

class Contradiction(BaseModel):
    """矛盾（v2：新增 conflict_type 分类 + T3 仲裁字段）"""
    pair: str
    timeframe: str
    description: str
    severity: str = Field(..., description="high / medium / low")
    conflict_type: str = Field(default="opinion", description="factual / temporal / opinion（参考 ConflictRAG）")
    resolution: Optional[str] = None
    confidence_adjustment: float = Field(default=0.0)
    # T3 仲裁字段
    arbitration_verdict: Optional[str] = Field(default=None, description="upgrade / downgrade / keep / None")
    arbitration_reasoning: str = Field(default="", description="仲裁理由")
    arbitration_confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="仲裁置信度")


class NewsOverride(BaseModel):
    """消息面覆盖"""
    override: bool = False
    overridden_position_limit: Optional[float] = None
    original_position_limit: Optional[float] = None
    validated_events: list = Field(default_factory=list)
    reason: Optional[str] = None


class TemporalInconsistency(BaseModel):
    """时间一致性异常"""
    type: str
    description: str
    severity: str = Field(..., description="critical / high / medium")
    action: str


# =============================================================================
# Node 6 v3: 分层 GOT 架构（全局环境 + 板块 mini GOT + 组合决策）
# =============================================================================

class MarketRegime(BaseModel):
    """全局市场环境判断（Level 0 输出）

    使用 5 个节点的全局聚合字段（非板块级字段），判断当前市场"天气"。
    """
    regime: str = Field(..., description="bull / bear / transition")
    risk_budget: float = Field(..., ge=0.0, le=1.0, description="风险预算（决定最大仓位暴露）")
    # ── 5 个节点的输入汇总 ──
    adx_value: Optional[float] = Field(default=None, description="Node 1: 趋势强度")
    cross_index_consistency: float = Field(default=0.0, description="Node 1: 多指数一致性")
    market_direction: str = Field(default="分化", description="Node 2: 普涨/分化/普跌")
    mainline_count: int = Field(default=0, description="Node 2: 活跃主线数量")
    institutional_score: float = Field(default=0.0, description="Node 3: 整体机构态度")
    news_impact_score: float = Field(default=0.0, description="Node 4: 消息影响")
    fear_greed: float = Field(default=0.5, description="Node 5: 恐贪指数（0=恐惧, 1=贪婪）")
    sentiment_stage: str = Field(default="neutral", description="Node 5: 反身性阶段")
    sentiment_momentum: float = Field(default=0.0, description="Node 5: 情绪动量")
    # ── 输出 ──
    macro_overlay: str = Field(default="", description="一句话宏观判断（LLM 或规则生成）")
    # ── Regime 状态机（v2.1 跨日记忆）──
    dwell_days: int = Field(default=1, description="当前 regime 持续交易日数")
    raw_regime_score: Optional[float] = Field(
        default=None,
        description="【#14 ④ jump penalty 影子接线（2026-10-15）】阈值前的 raw regime_score "
                    "（_compute_market_regime 局部值，此前未入 schema，新增供 jump 模型与影子标定消费）。"
                    "**不参与任何决策**，仅留痕/影子输入。"
                    "【按 #5a 先例：不进 R-B8/幂等不变组，按设计 SKIP】",
    )
    previous_regime: Optional[str] = Field(default=None, description="前一交易日的 regime（用于状态转换）")
    transition_count: int = Field(default=0, description="近 20 交易日 regime 确认切换次数（震荡度）")
    transition_pending: Optional[str] = Field(default=None, description="待确认的新 regime（首次出现，待次日确认）")
    # ── §7.1 振荡修复（2026-09-26）──
    pinned_days: int = Field(default=0, description="连续「保持昨日且挂起待确认」（情形 3）天数，振荡钉住计数")
    transition_events: Optional[list[str]] = Field(
        default=None,
        description="确认切换事件日期列表（YYYYMMDD 升序），transition_count 的真相源；"
                    "None=无事件记忆（旧归档或回滚开关期，按输出 diff 迁移种子重建），"
                    "[]=已迁移且暂无事件",
    )
    regime_switch_exempt: bool = Field(
        default=False,
        description="今日切换是否为 §7.1 迟滞豁免强切（True=放弃两日确认，供 R2 复盘区分）",
    )


class CrossNodeTemporalSignal(BaseModel):
    """跨节点时间线共振信号（v2.1 Phase 6）

    四种共振模式：
    - bull_confirmation: regime 刚确认切牛 + 资金 5d 加速度 > 0 → 强买入
    - sentiment_top: 反向卖出 + 资金撤退 → 强卖出
    - catalyst_confirmation: 催化兑现 T+3 > 5% + 资金连续正流入 → 加仓
    - range_reversal: 高震荡 + 低持续性 → 短线反转策略有效

    学术依据：Liu & Wang (2017), Han (2022), PEAD 文献
    """
    signal_type: str = Field(
        ..., description="bull_confirmation / sentiment_top / catalyst_confirmation / range_reversal"
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="几何平均置信度（≥2 组件确认）")
    component_nodes: list[str] = Field(default_factory=list, description="参与节点，如 ['node6', 'node3']")
    component_evidence: dict = Field(default_factory=dict, description="各节点具体证据值")
    reasoning: str = Field(default="", description="人读友好的解释")
    suggested_action: str = Field(
        default="", description="strong_buy / sell / add / short_term_reversal"
    )


class SectorIntel(BaseModel):
    """板块级情报（Level 1 板块 mini GOT 输出）

    对每个候选板块做 4 维交叉验证（技术面+资金面+消息面+情绪面），
    替代旧版 ConfirmedSector，增加 lifecycle 信息和更丰富的细节。
    """
    sector_name: str
    sector_code: str = ""
    sector_type: str = Field(default="concept", description="concept / industry")
    # ── 板块角色（主线/埋伏区分）──
    sector_role: str = Field(default="mainline", description="mainline / ambush / independent")
    mainline_tier: Optional[str] = Field(default=None, description="S / A / B（仅 mainline，来自 Node 2 MainlineGroup.tier）")
    mainline_quality: Optional[str] = Field(default=None, description="strong / weak（仅 mainline）")
    mainline_days: Optional[int] = Field(default=None, description="连续主线天数（仅 mainline）")
    # ── mini GOT 4 维交叉验证 ──
    tech_score: float = Field(default=0.0, ge=0.0, le=100.0, description="技术面评分（来自 Node 2）")
    tech_detail: str = Field(default="", description="技术面详情（lifecycle/tier/trajectory）")
    capital_check: str = Field(default="neutral", description="aligned / neutral / divergent")
    capital_detail: str = Field(default="", description="资金面验证细节")
    news_check: str = Field(default="neutral", description="catalyzed / neutral / negative")
    news_detail: str = Field(default="", description="消息催化细节")
    sentiment_check: str = Field(default="low", description="high / medium / low（散户热度）")
    sentiment_detail: str = Field(default="", description="散户热度细节")
    # ── 生命周期 ─
    lifecycle: Optional[LifecycleInfo] = Field(default=None, description="生命周期信息（来自 Node 2）")
    # ── MEG 动能衰竭三态（§7.3，透传自 Node 2 SectorDetail，2026-10-08 回测证真后新增）──
    momentum_state: Optional[str] = Field(default=None, description="snap/stall/thrust（Node 2 动能衰竭三态）")
    rs_mom_decay_2d: Optional[float] = Field(default=None, description="RS-Momentum EMA 两日衰减率（≤-0.30 判 snap）")
    # ── 聚合 ──
    composite_score: float = Field(default=0.0, ge=0.0, le=100.0, description="综合评分（lifecycle-aware）")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="数据完整度 × 信号一致性")
    classification: str = Field(
        default="watch",
        description="confirmed / divergent / watch / avoid / speculative",
    )
    reasoning: str = Field(default="", description="一句话推理")
    # ── 过热降权字段（Part 1: 过热评分降权）──
    overheat_severity: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="过热严重度（0~1），来自 Node 2 overheat_lookup，用于 Phase 2 评分降权",
    )
    frame_group: Optional[str] = Field(
        default=None,
        description="所属主线家族名称（来自 Node 2 ThemeFamily），用于拥挤家族追加降权",
    )
    # ── 埋伏通道字段（P3 新增，仅 sector_role=="ambush" 时有效）──
    ambush_catalyst_score: Optional[float] = Field(
        default=None, ge=0.0, le=2.0,
        description="埋伏催化剂排序分（B 公式：主催化分 + 冗余加成）",
    )
    ambush_category_tag: Optional[str] = Field(
        default=None,
        description="催化剂类型标签: drift / fade / binary（决定退出策略）",
    )
    ambush_events: list[str] = Field(
        default_factory=list,
        description="关联催化剂事件标题列表（最多 3 条）",
    )
    ambush_gate_fail: Optional[str] = Field(
        default=None,
        description="闸门失败原因（accumulation/crowded/runup/negative_catalyst/no_catalyst）",
    )


class AmbushCandidate(BaseModel):
    """埋伏通道正式池候选（P3）

    通过 3 层闸门 + B 公式排序后的板块级埋伏候选。
    """
    sector_name: str
    sector_code: str = ""
    catalyst_score: float = Field(ge=0.0, le=2.0, description="B 公式排序分")
    category_tag: str = Field(description="drift / fade / binary")
    events: list[str] = Field(default_factory=list, description="关联催化剂事件标题")
    top_event_date: Optional[date] = Field(default=None, description="最高等级事件日期")
    top_event_level: int = Field(default=1, ge=1, le=3, description="最高等级事件 ★ 数")
    accumulation_detail: str = Field(default="", description="吸筹确认细节")
    gate_notes: str = Field(default="", description="闸门通过备注")
    accum_strength: str = Field(
        default="legacy",
        description="吸筹档位: strong(连续3日正) / medium(3日累计正) / weak(仅当日正) / legacy(冷启动)",
    )
    nominated: bool = Field(
        default=False,
        description="Agent 催化剂先行提名条目（吸筹未确认但催化先行，决策 21）",
    )


class AmbushReservoirEntry(BaseModel):
    """埋伏蓄水池条目（P3）

    闸门通过但窗口内无合格催化剂 → 进入蓄水池。
    驻留 ≥20 交易日 → 自动除名（RESERVOIR_EXPIRY_DAYS, P1-2）。
    """
    sector_name: str
    sector_code: str = ""
    reason: str = Field(description="入池原因: no_catalyst / near_miss:{条件} / data_missing / reverse_query_pending / reverse_query_weak")
    entered_date: date = Field(description="入池日期")
    weeks_without_catalyst: int = Field(default=0, description="[遗留] 已被 days_in_reservoir 替代")
    reverse_query_result: Optional[str] = Field(default=None, description="反向查询结果摘要")
    # P1-2 新增字段
    days_in_reservoir: int = Field(default=0, description="蓄水池驻留天数（交易日）")
    entered_strength: float = Field(default=0.0, description="入池日强度（升格比较基准）")


class AmbushPositionSnapshot(BaseModel):
    """埋伏持仓催化剂快照（P4）

    Phase 2 建仓时写入，每日与日历/新闻对账，用于触发退出信号。
    买入快照 + 兑现结果 = 完整复盘记录（与催化归档表同一张表）。
    """
    symbol: str = Field(description="股票代码")
    sector_name: str = Field(description="所属埋伏板块名")
    category_tag: str = Field(description="催化剂类型: drift / fade / binary")
    catalyst_event_id: str = Field(default="", description="绑定的催化剂 event_id")
    catalyst_event_title: str = Field(default="", description="事件标题")
    catalyst_event_date: Optional[date] = Field(default=None, description="事件日期")
    impact_level: int = Field(default=1, ge=1, le=3, description="影响等级 ★ 数")
    consensus_expectation: str = Field(default="", description="共识预期")
    entry_date: date = Field(description="建仓日期")
    entry_price: float = Field(ge=0.0, description="建仓价格")
    # 退出追踪
    exit_signal: Optional[str] = Field(default=None, description="退出信号: fade_sell / drift_exhausted / event_cancelled / dde_graduation / stop_loss")
    exit_date: Optional[date] = Field(default=None, description="退出日期")
    exit_price: Optional[float] = Field(default=None, description="退出价格")
    is_open: bool = Field(default=True, description="是否仍在持仓")


class Contradiction(BaseModel):
    """矛盾（v2：新增 conflict_type 分类 + T3 仲裁字段）"""
    pair: str
    timeframe: str
    description: str
    severity: str = Field(..., description="high / medium / low")
    conflict_type: str = Field(default="opinion", description="factual / temporal / opinion（参考 ConflictRAG）")
    resolution: Optional[str] = None
    confidence_adjustment: float = Field(default=0.0)
    # T3 仲裁字段
    arbitration_verdict: Optional[str] = Field(default=None, description="upgrade / downgrade / keep / None")
    arbitration_reasoning: str = Field(default="", description="仲裁理由")
    arbitration_confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="仲裁置信度")


class MainlineEndurance(BaseModel):
    """主线续航评估（v3.5 S4：合成层放 aggregator，唯一同时持有 sector+capital+catalyst 的位置）。

    定位：信号展示 + 降权提示，不触发强制卖出（评分制原则，不做硬闸门）。
    """
    name: str = Field(..., description="家族/主线组名")
    wave_days: int = Field(..., description="当前波次持续天数（组内最大值）")
    lifecycle_phase: Optional[str] = Field(default=None, description="生命周期阶段（组内最老成员）")
    rrg_leading_count: int = Field(default=0, description="RRG leading 象限成员数")
    member_count: int = Field(default=0, description="家族总成员数")

    # ── 厚度指标 ──
    family_resonance: float = Field(default=0.0, ge=0.0, le=1.0, description="家族共振度（成员强度一致性）")
    tiers: dict[str, list[str]] = Field(
        default_factory=dict,
        description="梯队分层：{'veteran': wave≥6, 'mid': 3-5, 'new': ≤2}（板块名列表）",
    )

    # ── 跨节点信号 ──
    max_overheat: float = Field(default=0.0, ge=0.0, le=1.0, description="成员最高过热严重度")
    crowded: bool = Field(default=False, description="是否属于拥挤家族（∈ crowded_families）")
    capital_stance: str = Field(
        default="missing",
        description="资金面立场：passed / rejected / missing（成员 capital_check 聚合）",
    )
    has_catalyst: bool = Field(default=False, description="Node 4 日历前 2 周内有无关联事件")

    # ── 输出信号 ──
    support_signals: list[str] = Field(default_factory=list, description="支撑信号列表")
    warning_signals: list[str] = Field(default_factory=list, description="警示信号列表")
    endurance_band: str = Field(
        default="inertia",
        description="续航区间：inertia（惯性期）/ divergence（分化期）/ topping_risk（见顶风险期），启发式",
    )
    exit_watch: str = Field(default="", description="次日观察点（如'RRG 翻 weakening 即离场信号'）")


class FamilySuppression(BaseModel):
    """家族见顶持续压制台账（FTS，Family Topping Suppression）。

    与 MEG 正交：MEG 看单日板块 decay 断裂（snap），FTS 看家族级多信号累积见顶
    （endurance_band=topping_risk），并带跨日 latch 实现"持续压制"。
    由 Node 6 消费 MainlineEndurance 合成，随 UnifiedContext 落 unified.json，次日本人读回做 latch。
    缺省 GOT_FAMILY_TOPPING_GATE_ENABLED=0 时不产台账（字段为空，逐字节不变）。
    """
    name: str = Field(..., description="家族/主线组名（与 endurance.name 对齐，亦即 frame_group）")
    endurance_band: str = Field(default="topping_risk", description="触发压制的续航区间（topping_risk）")
    aggravated: bool = Field(
        default=False,
        description="是否加重（破位 + ≥MIN_CONFIRMS 个正交硬确认项：资金rejected/严重过热≥0.5/老波≥6/拥挤）："
                    "True 时 Phase2 层B 硬降 buy→watch，False 仅层A 缩仓",
    )
    warning_signals: list[str] = Field(default_factory=list, description="触发时的警示信号快照")
    members: list[str] = Field(default_factory=list, description="家族成员板块名（供 join/调试）")
    days_suppressed: int = Field(default=0, description="已连续被压制交易日数（latch 计数）")
    days_healthy: int = Field(default=0, description="转健康后连续日数（达 EXIT_HEALTHY 提前释放）")
    mode: str = Field(default="shadow", description="当日 FTS 模式快照：shadow / veto")


class UnifiedContext(BaseModel):
    """Node 6 输出：GOT 最终输出（v3 分层架构）"""
    status: str = Field(..., description="normal / manual_review_required")
    unified_signal: str = Field(..., description="bullish / bearish / neutral / cautious")
    overall_confidence: float = Field(..., ge=0.0, le=1.0)
    final_position_limit: float = Field(..., ge=0.0, le=1.0)
    contradictions: list[Contradiction] = Field(default_factory=list)
    common_root_cause: Optional[str] = None
    news_override: Optional[NewsOverride] = None
    temporal_inconsistencies: list[TemporalInconsistency] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    key_opportunities: list[str] = Field(default_factory=list)
    concept_suggestions: list[dict] = Field(default_factory=list)
    reasoning: Optional[str] = None
    data_quality: DataQuality = Field(default_factory=DataQuality)

    # ── v3 新增：分层 GOT 架构 ──
    market_regime: Optional[MarketRegime] = Field(
        default=None,
        description="全局市场环境判断（Level 0: 5 个节点聚合字段 → bull/bear/transition）",
    )
    llm_refinement_used: bool = Field(
        default=False,
        description="是否使用了 LLM 精炼（板块 narrative）",
    )
    ema_baseline: Optional[float] = Field(
        default=None,
        description="当前 EMA 基线值（用于时间一致性校验）",
    )

    # ── A6 接线可观测性（2026-09-17）──
    glue_consumed: bool = Field(
        default=False,
        description="当日 SectorIntel 是否已接入 glue 输出（A6 阶段 1）；false = 影子期行为或上游降级回退",
    )
    glue_status: Optional[str] = Field(
        default=None,
        description="当日 glue 产物状态（ok / S1_* / S2_* / S6_disabled / replay_mode），供归档回溯",
    )

    # ── 板块情报（Level 1 板块 mini GOT 输出，替代旧 confirmed_sectors） ──
    confirmed_sectors: list[SectorIntel] = Field(
        default_factory=list,
        description="板块 mini GOT 情报（4 维交叉验证 + lifecycle + 分类），Phase 2 直接消费",
    )

    # ── 埋伏通道（P3 新增：Level 1 平行分支输出）──
    ambush_pool: list[AmbushCandidate] = Field(
        default_factory=list,
        description="埋伏正式池（≤10，催化剂排序，宁缺毋滥）",
    )
    ambush_reservoir: list[AmbushReservoirEntry] = Field(
        default_factory=list,
        description="埋伏蓄水池（闸门通过但无合格催化剂，≥20 交易日自动除名）",
    )
    ambush_veto_log: list[str] = Field(
        default_factory=list,
        description="矛盾否决记录（★★★利空 / 拥挤黑名单等否决理由）",
    )
    # ── F1 埋伏道一致性校验（2026-10-15，影子观测）──
    ambush_lane_validation: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="埋伏道交叉校验提案（三规则：stagnation_ambush/catalyst_expiry_watch/family_divergence），"
                    "只产提案不改 ambush_pool/ambush_reservoir 本体（GOT_AMBUSH_VALIDATION_SHADOW 开关控制）",
    )
    # ── 过热降权（Part 1: 家族拥挤信号透传）──
    crowded_families: list[str] = Field(
        default_factory=list,
        description="拥挤家族名称列表（来自 Node 2，用于 Phase 2 追加降权）；"
                    "B3 裁决 2026-09-28：维持现状+口径标注（启发式阈值，不停产不新增接线）",
    )
    # ── 主线续航评估（v3.5 S4：信号展示 + 降权提示，不触发强制卖出）──
    mainline_endurance: list[MainlineEndurance] = Field(
        default_factory=list,
        description="主线家族续航评估（对 active_mainline 家族逐一生成，警示分档+观察点）",
    )
    # ── 家族见顶持续压制台账（FTS：Node6 产出带跨日 latch，Phase2 分级消费）──
    family_suppression: list[FamilySuppression] = Field(
        default_factory=list,
        description="被压制家族台账（topping_risk + 跨日 latch）；缺省 GOT_FAMILY_TOPPING_GATE_ENABLED=0 时为空",
    )

    # ── 跨节点时间线共振（v2.1 Phase 6）──
    cross_node_signals: list[CrossNodeTemporalSignal] = Field(
        default_factory=list,
        description="跨节点时间线共振信号（bull_confirmation / sentiment_top / catalyst_confirmation / range_reversal）",
    )

    # ── D-P1: 资金背离阈值可观测性（2026-09-11）──
    capital_divergence: Optional[dict] = Field(
        default=None,
        description="资金背离阈值诊断信息（样本数/5%分位/最终阈值/确认板块匹配详情），仅落盘不进评分",
    )

    # ── D-P2: 埋伏闸门漏斗 + 蓄水池溢出（2026-09-11，2026-09-26 补 skipped 留痕）──
    ambush_gate_funnel: Optional[dict] = Field(
        default=None,
        description="埋伏闸门逐层漏斗（G1利空→G2防买顶→G3拥挤→G4吸筹→入池的逐层计数；"
                    "各闸 evaluated/rejected/skipped 分列，skipped_reason 标注未评估成因"
                    "（死闸无数据源/资金流缺失），死闸不再被计 evaluated）",
    )
    ambush_reservoir_overflow: Optional[list[dict]] = Field(
        default=None,
        description="蓄水池容量驱逐记录（被驱逐板块/驻留天数/驱逐原因）",
    )

    # ── Phase 4b / MarketThemeContext Step 2 观测留痕（批次 5 §2，2026-09-28）──
    market_theme_shadow: Optional[dict] = Field(
        default=None,
        description="市场结构（题材阶段）影子观测留痕: {status, source, "
                    "top5_concepts:[{name,rank,change_pct}], breadth, note}。"
                    "影子模式（GOT_MARKET_THEME_SHADOW=1 默认）只落此字段、不改任何决策；"
                    "仅留痕，不参与判定。【按 #5a 先例：不进 R-B8/幂等不变组，按设计 SKIP】",
    )
    market_theme: Optional[MarketThemeContext] = Field(
        default=None,
        description="市场结构上下文真值（仅 GOT_MARKET_THEME_ENABLED=1 生效时填）；"
                    "影子期保持 None，避免把未生效数据伪装成已接入。不参与既有评分链，"
                    "仅在 enabled 时对 overall_confidence 施加一致性因子。",
    )

    # ── Phase 3b / 分歧摘要观测留痕（批次 5 §3.2，2026-09-28）──
    conflict_summary_shadow: Optional[dict] = Field(
        default=None,
        description="分歧摘要影子观测留痕（remote §8.2 build_agent_disagreement_summary 形状）: "
                    "{conflict_type, decision_path_hint, bullish/bearish/neutral_agents, "
                    "consensus, risk_control, risk_override_present, weights_equal}。"
                    "影子模式（GOT_DISAGREEMENT_SHADOW=1 默认）只落此字段、不改既有矛盾检测/LLM 仲裁；"
                    "仅留痕，不参与判定。【按 #5a 先例：不进 R-B8/幂等不变组，按设计 SKIP】",
    )


# ── Phase 2: 统一决策节点 Schema ──


class PortfolioRiskAssessment(BaseModel):
    """Phase 2 Step 1: 风险评估（约束设定）"""
    position_limit: float = Field(..., ge=0.0, le=1.0, description="仓位上限（来自 Phase 1）")
    current_position_ratio: float = Field(..., ge=0.0, le=1.0, description="当前仓位比例（经 min(1.0, raw) 截断后）")
    raw_position_ratio: Optional[float] = Field(
        default=None,
        description="【OPEN-D9 / P2-3 观测留痕（排期批 3.1，2026-09-28）】未截断的原始仓位比（pre-clip）。"
                    "current_position_ratio 被 min(1.0, raw) 截断后，13 天 buy_budget=0 不可证伪——"
                    "本字段落盘截断前真值以供离线诊断。**不参与任何决策/仓位计算**，仅留痕",
    )
    stop_loss_threshold: float = Field(default=-0.07, description="止损阈值（-7%）")
    sector_exposure_limits: dict[str, float] = Field(default_factory=dict, description="板块暴露上限")
    risk_budget: float = Field(default=0.05, description="风险预算（单笔最大亏损占总资产比例）。**派生自 `MarketRegime.risk_budget`，当前无消费者**")
    constraint_reasoning: str = Field(description="约束设定理由")


class UnifiedScoringResult(BaseModel):
    """Phase 2 Step 2: 统一打分（持仓 + 候选股）"""
    symbol: str
    name: str
    is_holding: bool = Field(description="是否为当前持仓")
    composite_score: float = Field(ge=0.0, le=100.0, description="综合评分")
    dimension_scores: dict[str, float] = Field(description="五维分项: capital/news/sector/tech/risk")
    action_recommendation: str = Field(description="buy_trend/buy_dip/hold/sell/avoid")
    confidence: float = Field(ge=0.0, le=1.0, description="评分置信度")
    reasoning: str = Field(description="一句话理由")
    # ── 板块归属（修复：sector_name 缺失导致"板块已踢出"误报）──
    sector_name: Optional[str] = Field(default=None, description="匹配到的板块名（来自 confirmed_sectors）")
    sector_classification: Optional[str] = Field(default=None, description="板块分类: actionable/caution/exclude")
    # 生命周期字段（用于 Step 3 吃鱼身+高切低策略）
    lifecycle_phase: Optional[str] = Field(default=None, description="板块生命周期阶段: launch/acceleration/mature/late_stage/mature_exhausted")
    lifecycle_day: Optional[int] = Field(default=None, description="生命周期已运行天数")
    overheat_severity: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="过热严重度")
    family_name: Optional[str] = Field(default=None, description="所属主线家族名称")
    # ── 板块漂移可观测性（Fix 1-3）──
    sector_match_type: Optional[str] = Field(
        default=None,
        description="板块匹配方式：direct / relay / thesis / fallback / none",
    )
    sector_drift_grace: bool = Field(
        default=False,
        description="家族宽限标记（板块漂移但家族仍强，降级持有观察）",
    )
    # ── P4 埋伏退出字段（仅埋伏持仓有效）──
    ambush_position_type: Optional[str] = Field(
        default=None,
        description="埋伏催化剂类型: drift / fade / binary（来自 AmbushPositionSnapshot）",
    )
    ambush_exit_signal: Optional[str] = Field(
        default=None,
        description="埋伏退出信号: fade_sell / drift_exhausted / drift_break_ma20 / drift_timeout / event_cancelled / dde_graduation / null",
    )
    ambush_exit_reason: Optional[str] = Field(
        default=None,
        description="退出理由（人可读）",
    )
    # ── §15 T1-H 持仓审查字段（仅持仓股有效）──
    agent_review_verdict: Optional[str] = Field(
        default=None,
        description="T1-H Agent 审查结果: hold / add / reduce / exit",
    )
    agent_review_reasoning: Optional[str] = Field(
        default=None,
        description="T1-H Agent 审查理由",
    )
    agent_review_penalty: Optional[float] = Field(
        default=None,
        description="T1-H 降档惩罚系数（0.0-1.0，已乘入 composite_score）",
    )
    # ── Q9-B: 板块验证失败标记（审计 2026-09-22）──
    sector_available: bool = Field(
        default=True,
        description="板块身份是否可信（False=验证失败，禁入买入池）",
    )
    # ── 审核修复计划 W10/S2：候选来源可观测性 ──
    dde_degraded: bool = Field(
        default=False,
        description="问财初筛 DDE 轮全空降级候选（区分真弱势出局与缺数据被动出局）",
    )
    pool_source: str = Field(
        default="",
        description="选股池来源标记：空=Node2 actionable 正常通道 / bypass_ambush=蓄水池升格旁路补选",
    )
    # ── 工作流 C：持仓延续性观测字段（C-B3，纯观测不参与评分；仅持仓股有效）──
    days_held: Optional[int] = Field(
        default=None,
        description="持有交易日数（入场日→今日，入场日取自持仓论点/埋伏快照；无入场日=None）",
    )
    drawdown_from_hwm: Optional[float] = Field(
        default=None,
        description="自入场以来最高收盘的回撤幅度（正数=回撤比 0~1）；K 线缺失/不足=None（追踪 no-op）",
    )
    # ── WI-P-2（持仓模块 T-3）：hwm 口径结构化观测，替代解析 reasoning 文本 ──
    hwm_gain: Optional[float] = Field(
        default=None,
        description="自入场以来最高收盘相对成本收益（0~1 小数）；近似口径下为反推值仅作观测；K 线缺失=None",
    )
    hwm_approx: Optional[bool] = Field(
        default=None,
        description="HWM 是否近似口径（True=无入场快照：窗口最高点+成本反推；该口径下追踪止盈 no-op，WI-P-1）",
    )
    entry_source: Optional[str] = Field(
        default=None,
        description="入场锚来源：thesis=持仓论点快照 / ambush=埋伏仓位快照 / none=无快照（近似口径）",
    )
    lifecycle_phase_source: Optional[str] = Field(
        default=None,
        description="生命周期阶段来源：sector_intel=confirmed 直接命中 / anchor=中继锚点板块 / extended=Node2 全量降级 / none=三路全落空（None 阶段）",
    )
    # ── 埋伏道拆分 v2 拦点#6：入场分道标识 ──
    # 词表说明：生产 passthrough 携带 screener 内部词表 "active_mainline"/"ambush"；
    # default "mainline" 仅为 Schema 兜底缺省（无 stock 来源时），所有闸门均判 == "ambush"，
    # 兜底值永不等价 ambush → 语义安全。禁止在 passthrough 处 remap（保持 house vocabulary 原样）。
    sector_type: str = Field(
        default="mainline",
        description="入场分道: active_mainline/ambush（screener 内部词表，生产 passthrough 原样）；mainline 为 Schema 兜底缺省（埋伏道拆分 v2 拦点#6）",
    )



class TradeAllocation(BaseModel):
    """Phase 2 Step 3: 仓位分配"""
    symbol: str
    name: str
    action: str = Field(description="buy/sell/hold")
    target_quantity: int = Field(description="目标股数（100 的整数倍）")
    target_value: float = Field(description="目标金额")
    current_quantity: int = Field(default=0, description="当前持仓股数")
    quantity_delta: int = Field(description="变动股数（正=买入，负=卖出）")
    price_range: tuple[float, float] = Field(description="建议价格区间")
    expected_return: float = Field(description="预期收益率")
    stop_loss: float = Field(description="止损价")
    risk_reward_ratio: float = Field(description="风险收益比")
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    # ── P4 埋伏仓催化剂快照（仅 ambush 买入有效）──
    is_ambush_entry: bool = Field(default=False, description="是否为埋伏仓买入")
    ambush_category_tag: Optional[str] = Field(
        default=None, description="催化剂类型: drift / fade / binary",
    )
    ambush_catalyst_event_id: Optional[str] = Field(
        default=None, description="绑定的催化剂 event_id（用于写入 AmbushPositionSnapshot）",
    )
    ambush_catalyst_event_date: Optional[date] = Field(
        default=None, description="催化剂事件日期",
    )
    # ── D-P11: 板块限制明细（落盘分支走向，使行为变更可见）──
    sector_limit_detail: Optional[dict] = Field(
        default=None,
        description="板块限制明细: {sector_name, sector_role, stock_limit, branch}（判据为 sector_role，limit 仅参考值）",
    )
    # ── Q4 audit: 盘中快照标记（bar_complete=false 时为 True）──
    intraday_snapshot: bool = Field(
        default=False,
        description="Q4 audit: True if based on intraday data (bar_complete=false)",
    )
    # ── GATE-1: 卖出侧可成交性投影层（shadow 打标不改决策 / enabled 转顺延）──
    pending_exit: bool = Field(
        default=False,
        description="GATE-1: True=卖单被可成交性拦截（跌停封板/T+1），待顺延复核；"
                    "shadow 模式仅打标不改数量，enabled 模式不进当日成交集合",
    )
    blocked_reason: Optional[str] = Field(
        default=None,
        description="GATE-1 拦截/降级原因: limit_down/t_plus_1/limit_up/quote_unavailable",
    )


class PortfolioDecision(BaseModel):
    """Phase 2 统一决策节点输出"""
    status: str = Field(description="normal/manual_review_required/partial(部分步骤降级)/failed(流水线失败占位)")
    risk_assessment: PortfolioRiskAssessment
    unified_scoring: list[UnifiedScoringResult] = Field(description="所有标的统一排序")
    allocation: list[TradeAllocation] = Field(description="最终交易建议")
    summary: str = Field(description="操作总结")
    warnings: list[str] = Field(default_factory=list, description="风险提示")
    reasoning: str = Field(description="LLM 推理过程（可追溯）")
    data_quality: DataQuality = Field(default_factory=DataQuality)
    # 轮动信号（吃鱼身+高切低策略）
    rotation_signals: list[dict] = Field(default_factory=list, description="轮动建议信号")
    crowding_warnings: list[dict] = Field(default_factory=list, description="家族拥挤警告")
    # ── P4 埋伏退出汇总 ──
    ambush_exit_signals: list[dict] = Field(
        default_factory=list,
        description="埋伏持仓退出信号汇总: {symbol, sector, category_tag, exit_signal, reason}",
    )
    # ── v3.5 S4 主线续航评估（从 UnifiedContext 透传）──
    mainline_endurance: list[MainlineEndurance] = Field(
        default_factory=list,
        description="主线家族续航评估（信号展示+降权提示，不触发强制卖出）",
    )
    # ── D-P12: 板块暴露绑定信息（口径声明 + 落盘绑定次数，2026-09-26 补 binding_count）──
    sector_exposure_binding: Optional[dict] = Field(
        default=None,
        description="板块暴露绑定信息: {sum_limits, position_limit, ratio, "
                    "buy_allocation_count, binding_count, note}（binding_count 仅可观测留痕，不参与决策）",
    )
    # ── 4d/D-3 前置：候选池归零率基线观测（项三 3a，外网对标 2026-10-11）──
    # 由 Phase 2 直调 screener._compute_funnel_stats 计算（universe 入 vs finalists 出），
    # 纯留痕不进任何判定；archive_phase2 消费后合并进 meta.json，使生产链路 D-3 基线可累积。
    funnel_stats: Optional[dict] = Field(
        default=None,
        description="候选池归零率基线观测: {actionable_sector_count, picked_sector_count, "
                    "zero_pick_sectors, zero_pick_rate, round_survival_counts}（仅留痕，不进判定）",
    )
    # ── P1-4 / OPEN-1 W2 卖出来源观测留痕（排期批 4.1，2026-09-28）──
    sell_source_shadow: Optional[dict] = Field(
        default=None,
        description="组合层超限减仓观测留痕: {mode, excess_value, target_position_value, "
                    "projected_trim_value, planned_trims, grace_skipped_symbols, note}。"
                    "mode=shadow 时不落任何决策（仅记录超限将削谁/是否撞家族宽限）；"
                    "mode=enabled 时同步已写入 allocation。仅留痕，不参与判定。"
                    "【按 #5a 先例：新增观测字段应移出 R-B8/幂等不变组，按设计 SKIP】",
    )
    # ── Phase 3b / RiskOverridePlan 观测留痕（批次 5 §3.3，2026-09-28）──
    risk_override_shadow: Optional[dict] = Field(
        default=None,
        description="风控覆盖计划留痕: {mode, conflict_type, plan, note}（plan=RiskOverridePlan "
                    "{veto_buy, adjustment, target_signal, reason}）。"
                    "mode=shadow 时只算不落决策（buy_budget 不变）；mode=enabled 且 veto_buy 时"
                    "与 over_limit_forced 并列把 buy_budget 归零（风控优先于既有降级规则）。"
                    "仅留痕，不参与判定。【按 #5a 先例：不进 R-B8/幂等不变组，按设计 SKIP】",
    )
    # ── GATE-1 卖出侧可成交性投影留痕（实施报告 2026-09-29）──
    tradability_shadow: Optional[dict] = Field(
        default=None,
        description="可成交性投影留痕: {mode, pending_exits, note}。mode=shadow 时仅打标"
                    "（pending_exit/blocked_reason 落 allocation，不改数量）；mode=enabled 时"
                    "被拦截卖单不进当日成交集合（顺延复核）。"
                    "【按 #5a 先例：不进 R-B8/幂等不变组，按设计 SKIP】",
    )
    # ── WI-P0-3 敞口执行接线 shadow 留痕（实施报告 2026-09-29）──
    exposure_enforce_shadow: Optional[dict] = Field(
        default=None,
        description="敞口截断观测留痕: {mode, would_truncate_count, would_truncate_total_value, "
                    "checks, note}。仅记录若强制会截断多少，shadow 下决策逐字节不变；"
                    "enabled 前置条件=conf_w 经 ECE 校准达标（P0-5）。"
                    "【按 #5a 先例：不进 R-B8/幂等不变组，按设计 SKIP】",
    )
    # ── WI-P0-4 信号强度连续缩放 shadow 留痕（实施报告 2026-09-29）──
    signal_size_shadow: Optional[dict] = Field(
        default=None,
        description="信号加权仓位差观测留痕: {mode, gamma, items, max_abs_delta, note}。"
                    "对比等分 vs score^γ 加权的仓位差，shadow 不改任何仓位。"
                    "【按 #5a 先例：不进 R-B8/幂等不变组，按设计 SKIP】",
    )
    # ── C-B2 启用前盘点 shadow 留痕（【总计划】§4.2，2026-10-06；2026-10-12 A1 #3 改三态 dict）──
    mature_reduce_shadow: Optional[dict] = Field(
        default=None,
        description="若 GOT_MATURE_REDUCE_DAY 切 14 本应触发 reduce 的盘点观测："
                    "{hits: [{symbol, sector, lifecycle_day, action_kept}], holdings_seen: int, "
                    "skipped_reason: Optional[str]}，窗口 (14, 现阈值]。"
                    "三态语义（定稿于 docs/got/verification.md 影子五线表）："
                    "null=本次未调 _score_holdings；skipped_reason=no_accounts/no_holdings ⇒ 跑了但无可观测对象"
                    "（hits 必空，不得读作「没有到期持仓」）；holdings_seen>0 且 hits 空 ⇒ 真读数。"
                    "纯观测不参与任何判定；持仓模块大改时须返工（持仓文档 WI-P-6）。"
                    "【按 #5a 先例：不进 R-B8/幂等不变组，按设计 SKIP】"
                    "【形状变更：2026-10-12 前为裸 list，旧归档（1008/1009 的 []）不得与新 dict 直接对拍】",
    )
    # ── 埋伏道拆分 v2 拦点#5：screener 侧埋伏道产物透传 ──
    ambush_lane_shadow: Optional[dict] = Field(
        default=None,
        description="埋伏道拆分 v2 产物: {mode, lane_finalists, note}（screener 侧埋伏道过闸名单）。"
                    "纯观测，不参与判定。"
                    "【按 #5a 先例：不进 R-B8/幂等不变组，按设计 SKIP】",
    )
    ambush_lane_trace: Optional[dict] = Field(
        default=None,
        description="埋伏道拆分 v2 拦点追踪: {g_a1_pass, g_a1_soft_pass, g_a1_flow_negative, "
                    "g_a1_dde_degraded, g_a2_no_catalyst, g_a2_low_confidence, g_a2_position_unsafe, "
                    "g_a3_knife, g_a4_selected, cross_lane_collision, lane_overflow_n, lane_error, "
                    "action_gate_lifted, action_gate_watch, bear_gate_watch, capital_gate_watch, "
                    "sector_verify_miss, meg_fts_overheat_triggers}（Node 7 侧拦点命中统计）。"
                    "纯观测，不参与判定。"
                    "【按 #5a 先例：不进 R-B8/幂等不变组，按设计 SKIP】",
    )
    # ── C4 分道预算影子 trace（三问裁决 WI-P-7，2026-10-15）──
    lane_budget_split_shadow: Optional[list[dict]] = Field(
        default=None,
        description="分道预算观测留痕: [{lane, allocated_value, allocated_weight, role_budget_m, "
                    "n_positions, formula_version}]。只读现状，零决策改动；GOT_LANE_BUDGET_SHADOW=0 时 None。"
                    "【按 #5a 先例：不进 R-B8/幂等不变组，按设计 SKIP】",
    )
    # ── E1 T3 verdict 路由影子（三问裁决 WI-P-7，2026-10-15）──
    t3_routing_shadow: Optional[list[dict]] = Field(
        default=None,
        description="T3 仲裁路由留痕: [{contradiction_id, rule_verdict, t3_verdict, routed_signal, action_taken}]。"
                    "action_taken 恒 'shadow_none'（零数值调制，O3 纪律）；GOT_T3_ROUTING_SHADOW=0 时 None。"
                    "【按 #5a 先例：不进 R-B8/幂等不变组，按设计 SKIP】",
    )
    # ── E2 cross_node_signals 路由影子（三问裁决 WI-P-7，2026-10-15）──
    cross_node_signals_shadow: Optional[list[dict]] = Field(
        default=None,
        description="跨节点信号路由留痕: [{signal_type, source_signal, strength, routing_mode, blocked_by, action_taken}]。"
                    "routing_mode ∈ {strong_alignment_pass, divergence_escalate, weak_signal_hold, bull_confirmation}；"
                    "bull_confirmation 行带 blocked_by='regime_pin_#14_pending'（#14 未拍板前零样本休眠）；"
                    "GOT_XNODE_ROUTING_SHADOW=0 时 None。"
                    "【按 #5a 先例：不进 R-B8/幂等不变组，按设计 SKIP】",
    )




# =============================================================================
# Agent 语义粘合层输出（docs/got/archive/agent_glue_plan_0903.md §5.3）
# =============================================================================

# 受控家族词表（§5.3.3）— 已迁移至 src.got.family_keywords（B2 词表同源修复）
# CONTROLLED_FAMILIES 从该模块 import（见文件顶部）


class GlueRelation(BaseModel):
    """Agent 语义粘合层 — 事件→板块关系（替代 T5 映射）"""
    event_id: str
    board_code: str
    strength: float = Field(ge=0.0, le=1.0)
    direction: str = Field(default="positive")
    propagation_path: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class GlueStrength(BaseModel):
    """Agent 语义粘合层 — 板块强度（替代 Segment C/D/E）"""
    board_code: str
    strength: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: str = Field(default="")
    capital_read: str = Field(default="unknown")
    news_read: str = Field(default="unknown")
    sentiment_read: str = Field(default="unknown")
    # C-P9: 口径声明（strength 替代的是综合评分 composite_score，0-100 百分制）
    basis: str = Field(
        default="composite",
        description="strength 所替代的口径（当前恒为 composite_score，0-100 百分制）",
    )


class GlueFamily(BaseModel):
    """Agent 语义粘合层 — 家族归属（只判落「其他」的板块）"""
    board_code: str
    family: str


class SemanticGlueResult(BaseModel):
    """Agent 语义粘合层输出（docs/got/archive/agent_glue_plan_0903.md §5.3 / §7.2）

    包含三类结构化输出：关系图 + 板块强度 + 家族归属。
    `degraded=True` 时表示 F1 降级（全量或逐板块）。
    """
    relations: list[GlueRelation] = Field(default_factory=list)
    strengths: list[GlueStrength] = Field(default_factory=list)
    families: list[GlueFamily] = Field(default_factory=list)
    degraded: bool = Field(default=False)
    degraded_sectors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: str = Field(default="ok", description="状态标记：ok / no_candidates / S1_call_failed / S1_empty / S2_parse_failed / S2_validate_failed / S6_disabled / replay_mode")
    # C-P12 prompt A/B 留痕：本次 glue 调用所用的变体文案（""=现行默认 / a=保守版 / b=全量归属版），
    # 仅 replay_mode 下会非空（生产链路忽略 GLUE_PROMPT_VARIANT），供 R-C6 对照矩阵溯源防错跑。
    prompt_variant: str = Field(default="", description="C-P12 变体留痕：空=现行默认，a=保守版，b=全量归属版，p=C-P13 接地管线（仅回放生效）")
    # C-P13 阶段1 留痕：接地管线状态（空=未走管线 / ok / embedding_unavailable / day_void / l2_failed / l2_partial / pipeline_error），
    # 仅 replay_mode + GLUE_PIPELINE_ENABLED 下非空；trace 详情落 glue_pipeline_trace.json。
    pipeline_status: str = Field(default="", description="C-P13 管线留痕：空=未走管线，ok/embedding_unavailable/day_void/l2_failed/l2_partial/pipeline_error（仅回放生效）")
    raw_response: str = Field(default="", exclude=True)
