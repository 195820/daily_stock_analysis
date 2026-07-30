"""
GOT 节点输入输出 Pydantic Schema

设计理念：
- 每个节点输出一个 Context 对象
- 包含 DataQuality 字段追踪（参考 AnalysisContextPack）
- 支持降级场景（缺失维度记录在 missing_fields）
"""

from datetime import datetime
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


class MarketContext(BaseModel):
    """Node 1 输出：大盘分析上下文"""
    tech_growth: TechGrowthSignal
    mid_small_cap: StyleSignal
    large_cap: StyleSignal
    position_limit: float = Field(..., ge=0.0, le=1.0)
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


# =============================================================================
# Node 2: 板块分析
# =============================================================================

class LaunchSignal(BaseModel):
    """启动信号"""
    triggered: bool
    level: str = Field(..., description="strong_launch / weak_launch / sustained / none")
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    dimensions: dict[str, float] = Field(default_factory=dict)


class LifecycleInfo(BaseModel):
    """生命周期信息"""
    phase: str = Field(..., description="launch / acceleration / mature / late_stage / mature_exhausted")
    days_since_launch: Optional[int] = None
    thresholds: dict[str, int] = Field(default_factory=dict)


class SectorDetail(BaseModel):
    """板块详情"""
    code: str
    name: str
    state: str = Field(..., description="active_mainline / exhausted_mainline / ambush / defensive_rotation")
    strength_score: float = Field(..., ge=0.0, le=100.0)
    indicators: dict[str, float] = Field(default_factory=dict)
    launch_signal: Optional[LaunchSignal] = None
    lifecycle: Optional[LifecycleInfo] = None
    tier: Optional[str] = None  # S / A / B
    recommended_action: Optional[str] = None
    today_change: Optional[float] = Field(
        default=None,
        description="T-0 板块涨跌幅（mootdx 成分股中位数），如 -3.38",
    )


class SectorContext(BaseModel):
    """Node 2 输出：板块分析上下文"""
    active_mainline: list[SectorDetail] = Field(default_factory=list)
    exhausted_mainline: list[SectorDetail] = Field(default_factory=list)
    ambush: list[SectorDetail] = Field(default_factory=list)
    defensive_rotation: list[SectorDetail] = Field(default_factory=list)
    top_by_score: list[SectorDetail] = Field(
        default_factory=list,
        description="评分最高的 N 个板块（参考用，不代表达标）",
    )
    market_direction: str = Field(default="分化", description="分化 / 普涨 / 普跌")
    notes: list[str] = Field(default_factory=list, description="附加说明（如评分不足警告）")
    reasoning: Optional[str] = None
    data_quality: DataQuality = Field(default_factory=DataQuality)


# =============================================================================
# Node 3: 资金面分析
# =============================================================================

class CapitalEvidence(BaseModel):
    """资金面证据"""
    northbound: Optional[dict] = None
    dragon_tiger: Optional[dict] = None
    etf_flow: Optional[dict] = None
    margin_trading: Optional[dict] = None
    block_trade: Optional[dict] = None


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
    timestamp: Optional[str] = None
    source_count: int = Field(default=1, ge=1)
    source_type: str = Field(default="search", description="cls / eastmoney / cctv / caixin / search")


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
    retail_attention_count: int = Field(default=0, description="散户关注热度（雪球讨论量归一化）")
    market_temperature: float = Field(default=0.5, ge=0.0, le=1.0, description="市场温度（0=冰点, 1=沸点）")
    news_drift: str = Field(default="neutral", description="消息面偏离（positive/negative/neutral）")
    reasoning: Optional[str] = None
    data_quality: DataQuality = Field(default_factory=DataQuality)


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
# Node 6 v2: 聚合中间类型（Phase A 产物）
# =============================================================================

class DimensionSignal(BaseModel):
    """统一信号空间投影结果（Phase A 内部用）"""
    name: str = Field(..., description="market / sector / capital / news / sentiment")
    score: float = Field(..., ge=-1.0, le=1.0, description="统一方向分")
    confidence: float = Field(..., ge=0.0, le=1.0, description="信号置信度（节点自评，衡量市场方向清晰度）")
    data_confidence: float = Field(..., ge=0.0, le=1.0, description="数据置信度（数据完整度，用于聚合权重衰减）")
    status: str = Field(default="normal", description="normal / degraded / failed")


class ConvergenceProfile(BaseModel):
    """收敛剖面（多维度一致性分析）"""
    pattern: str = Field(
        ...,
        description=(
            "full_convergence / mild_divergence / strong_divergence "
            "/ lone_outlier / low_confidence"
        ),
    )
    agreement_score: float = Field(..., ge=0.0, le=1.0, description="0=完全分裂, 1=完全一致")
    investigate_outlier: bool = Field(
        default=False,
        description="Surprising Popularity: 孤立异见置信度异常高时需调查",
    )


class DimensionContribution(BaseModel):
    """各维度对最终聚合结果的贡献明细"""
    name: str
    score: float = Field(..., description="该维度投影分")
    confidence: float = Field(..., description="信号置信度（节点自评）")
    data_confidence: float = Field(..., description="数据置信度（数据完整度）")
    contribution: float = Field(..., description="对 weighted_score 的贡献值")


class AggregationResult(BaseModel):
    """Phase A 聚合结果"""
    weighted_score: float = Field(..., ge=-1.0, le=1.0, description="置信度加权方向分")
    convergence: ConvergenceProfile
    dimension_contributions: list[DimensionContribution] = Field(default_factory=list)
    active_count: int = Field(..., description="参与聚合的维度数")
    failed_count: int = Field(default=0, description="降级的维度数")


class UnifiedContext(BaseModel):
    """Node 6 输出：GOT 最终输出"""
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

    # ── v2 新增字段（置信度加权聚合） ──
    convergence_profile: Optional[ConvergenceProfile] = Field(
        default=None,
        description="收敛剖面：多维度一致性分析结果",
    )
    weighted_score: Optional[float] = Field(
        default=None,
        description="置信度加权方向分（-1~+1）",
    )
    dimension_contributions: list[DimensionContribution] = Field(
        default_factory=list,
        description="各维度对最终聚合的贡献明细",
    )
    llm_refinement_used: bool = Field(
        default=False,
        description="是否使用了 LLM 精炼（Phase C）",
    )
    ema_baseline: Optional[float] = Field(
        default=None,
        description="当前 EMA 基线值（用于时间一致性校验）",
    )
    signal_confidence_avg: Optional[float] = Field(
        default=None,
        description="信号置信度均值（各维度节点自评平均，衡量市场方向清晰度）",
    )
    insufficient_data: bool = Field(
        default=False,
        description="信息充分度不足：信号置信度均值 < 0.5 时置 true，建议空仓观望",
    )
