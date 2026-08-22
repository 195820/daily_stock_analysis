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
        description="波动率调整后仓位（Layer 1 输出）",
    )
    regime_scale: Optional[float] = Field(
        default=None,
        description="状态缩放因子（Layer 2 输出，0.2 ~ 1.0）",
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

    论文依据：
    - MACD 背离渐进式（Kang 2023）
    - A/D Line distribution（72% 重大下跌前出现）
    - 天量滞涨 = 分配信号（Hsu 2016）
    """
    divergence_score: float = Field(default=0.0, ge=0.0, le=1.0, description="MACD 背离强度（渐进式）")
    volume_divergence_score: float = Field(default=0.0, ge=0.0, le=1.0, description="量能背离强度")
    breadth_score: float = Field(default=0.0, ge=0.0, le=1.0, description="宽度极端强度")
    turnover_score: float = Field(default=0.0, ge=0.0, le=1.0, description="换手极端强度")
    ad_distribution: bool = Field(default=False, description="A/D Line 分配信号")
    volume_stall: bool = Field(default=False, description="天量滞涨信号")
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
    near_miss_conditions: Optional[list[str]] = Field(
        default=None,
        description="埋伏近失条件列表（差 1 条通过埋伏闸门时填充，供 Node 6 蓄水池观察）",
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


# =============================================================================
# Node 3: 资金面分析
# =============================================================================

class CapitalEvidence(BaseModel):
    """资金面证据（实时 T+0 维度）"""
    sector_flow: Optional[dict] = None        # 概念/行业板块资金流
    northbound_realtime: Optional[dict] = None  # 北向资金分钟级实时（备用，停披后全 0 自动降级）
    dde: Optional[dict] = None                # 大单净流入（DDE，全市场）
    individual_main_force: Optional[dict] = None  # 个股主力聚流（T-1 备用）


class CapitalContext(BaseModel):
    """Node 3 输出：资金面分析上下文"""
    status: str = Field(..., description="normal / degraded / failed")
    institutional_attitude: str = Field(..., description="bullish / bearish / neutral")
    institutional_score: float = Field(..., ge=-1.0, le=1.0)
    confidence_multiplier: float = Field(..., ge=0.0, le=1.0)
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
    source_type: str = Field(default="search", description="cls / eastmoney / cctv / caixin / search")
    # 生命周期追踪字段（跨日记忆）
    lifecycle_state: Optional[str] = Field(default=None, description="emerging / fermenting / peak / fading / archived")
    mention_count: Optional[int] = Field(default=None, description="跨日累计出现次数")
    first_seen: Optional[str] = Field(default=None, description="首次出现日期（YYYY-MM-DD）")


class CatalystEvent(BaseModel):
    """催化剂事件 — 板块级前瞻日历的最小单元"""
    event_id: str = Field(..., description="MD5(source + event_type + sector + date)")
    event_date: date = Field(..., description="事件日期")
    event_type: str = Field(..., description="earnings / lockup / macro / narrative / global")
    title: str = Field(..., description="事件描述")
    sector: str = Field(..., description="映射到的 A 股板块名")
    impact_level: int = Field(default=1, ge=1, le=3, description="1=★ 2=★★ 3=★★★")
    direction: str = Field(default="positive", description="positive / negative / binary")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="< 0.7 不入评分")
    consensus_expectation: str = Field(default="", description="共识预期（必填）")
    source: str = Field(..., description="akshare_earnings / akshare_lockup / macro_rule / llm_narrative / global_map")
    category_tag: str = Field(default="", description="drift / fade / binary（退出策略分型依据）")
    is_global: bool = Field(default=False, description="是否来自全球事件映射")
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
    associated_sectors: list[str] = Field(default_factory=list)


class SentimentContext(BaseModel):
    """Node 5 输出：情绪面分析上下文"""
    status: str = Field(default="normal", description="normal / degraded / failed")
    overall_sentiment: str = Field(..., description="bullish / bearish / neutral")
    sentiment_score: float = Field(..., ge=-1.0, le=1.0)
    sentiment_stage: str = Field(..., description="euphoria / optimism / neutral / pessimism / capitulation")
    trading_signal: str = Field(..., description="contrarian_sell / trend_following / neutral / contrarian_buy")
    signal_confidence: float = Field(..., ge=0.0, le=1.0)
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
    contrarian_signal: bool = Field(
        default=False,
        description="反向信号（情绪极端一致时触发）",
    )
    sentiment_momentum: float = Field(
        default=0.0,
        description="情绪动量（当前score与EMA5偏差，正=升温, 负=降温）",
    )


# =============================================================================
# Node 6: 聚合节点
# =============================================================================

class Contradiction(BaseModel):
    """矛盾（v2：新增 conflict_type 分类）"""
    pair: str
    timeframe: str
    description: str
    severity: str = Field(..., description="high / medium / low")
    conflict_type: str = Field(default="opinion", description="factual / temporal / opinion（参考 ConflictRAG）")
    resolution: Optional[str] = None
    confidence_adjustment: float = Field(default=0.0)


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
    # ── 聚合 ──
    composite_score: float = Field(default=0.0, ge=0.0, le=100.0, description="综合评分（lifecycle-aware）")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="数据完整度 × 信号一致性")
    classification: str = Field(
        default="watch",
        description="confirmed / divergent / watch / avoid / speculative",
    )
    reasoning: str = Field(default="", description="一句话推理")
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


class AmbushReservoirEntry(BaseModel):
    """埋伏蓄水池条目（P3）

    闸门通过但窗口内无合格催化剂 → 进入蓄水池。
    连续 4 周无催化 → 自动除名。
    """
    sector_name: str
    sector_code: str = ""
    reason: str = Field(description="入池原因: no_catalyst / near_miss:{条件} / reverse_query_pending / reverse_query_weak")
    entered_date: date = Field(description="入池日期")
    weeks_without_catalyst: int = Field(default=0, description="连续无催化周数")
    reverse_query_result: Optional[str] = Field(default=None, description="反向查询结果摘要")


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
    """矛盾（v2：新增 conflict_type 分类）"""
    pair: str
    timeframe: str
    description: str
    severity: str = Field(..., description="high / medium / low")
    conflict_type: str = Field(default="opinion", description="factual / temporal / opinion（参考 ConflictRAG）")
    resolution: Optional[str] = None
    confidence_adjustment: float = Field(default=0.0)


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
        description="埋伏蓄水池（闸门通过但无合格催化剂，4 周自动除名）",
    )
    ambush_veto_log: list[str] = Field(
        default_factory=list,
        description="矛盾否决记录（★★★利空 / 拥挤黑名单等否决理由）",
    )


# ── Phase 2: 统一决策节点 Schema ──


class PortfolioRiskAssessment(BaseModel):
    """Phase 2 Step 1: 风险评估（约束设定）"""
    position_limit: float = Field(..., ge=0.0, le=1.0, description="仓位上限（来自 Phase 1）")
    current_position_ratio: float = Field(..., ge=0.0, le=1.0, description="当前仓位比例")
    stop_loss_threshold: float = Field(default=-0.07, description="止损阈值（-7%）")
    sector_exposure_limits: dict[str, float] = Field(default_factory=dict, description="板块暴露上限")
    risk_budget: float = Field(default=0.05, description="风险预算（单笔最大亏损占总资产比例）")
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
    # 生命周期字段（用于 Step 3 吃鱼身+高切低策略）
    lifecycle_phase: Optional[str] = Field(default=None, description="板块生命周期阶段: launch/acceleration/mature/late_stage/mature_exhausted")
    lifecycle_day: Optional[int] = Field(default=None, description="生命周期已运行天数")
    overheat_severity: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="过热严重度")
    family_name: Optional[str] = Field(default=None, description="所属主线家族名称")
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


class PortfolioDecision(BaseModel):
    """Phase 2 统一决策节点输出"""
    status: str = Field(description="normal/manual_review_required")
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
