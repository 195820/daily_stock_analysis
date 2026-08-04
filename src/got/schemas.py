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
    mainline_quality: Optional[str] = Field(
        default=None,
        description="strong / weak — B级主线标记为weak，下游选股系统可打折",
    )
    recommended_action: Optional[str] = None
    today_change: Optional[float] = Field(
        default=None,
        description="T-0 板块涨跌幅（mootdx 成分股中位数），如 -3.38",
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


class SectorContext(BaseModel):
    """Node 2 输出：板块分析上下文"""
    # ── 概念板块视角（短线题材）──
    active_mainline: list[MainlineGroup] = Field(default_factory=list)
    exhausted_mainline: list[SectorDetail] = Field(default_factory=list)
    ambush: list[SectorDetail] = Field(default_factory=list)
    defensive_rotation: list[SectorDetail] = Field(default_factory=list)
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
    timestamp: Optional[str] = None
    source_count: int = Field(default=1, ge=1)
    source_type: str = Field(default="search", description="cls / eastmoney / cctv / caixin / search")
    # 生命周期追踪字段（跨日记忆）
    lifecycle_state: Optional[str] = Field(default=None, description="emerging / fermenting / peak / fading / archived")
    mention_count: Optional[int] = Field(default=None, description="跨日累计出现次数")
    first_seen: Optional[str] = Field(default=None, description="首次出现日期（YYYY-MM-DD）")


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


class ConfirmedSector(BaseModel):
    """Node 6 四维交叉验证后的板块推荐（Phase 2 直接消费）"""
    sector_name: str = Field(..., description="板块名称")
    sector_type: str = Field(..., description="confirmed / divergent / watch / avoid / speculative")
    action: str = Field(..., description="buy_trend / buy_dip / watch / avoid")
    position_hint: float = Field(default=0.0, ge=0.0, le=1.0, description="仓位权重 0~1，归一化用")

    # 四维验证结果
    capital_check: str = Field(default="neutral", description="aligned / neutral / divergent")
    capital_detail: str = Field(default="", description="资金面验证细节")
    trend_check: str = Field(default="stable", description="rising / stable / declining / cliff_drop")
    trend_detail: str = Field(default="", description="趋势验证细节（含 trajectory）")
    environment_check: str = Field(default="neutral", description="supportive / neutral / hostile")
    environment_detail: str = Field(default="", description="环境验证细节")
    news_check: str = Field(default="neutral", description="catalyzed / neutral / negative")
    news_detail: Optional[str] = Field(default=None, description="消息催化细节")

    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="综合置信度")
    reasoning: str = Field(default="", description="一句话逻辑")


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

    # ── Phase D: 四维板块交叉验证 ──
    confirmed_sectors: list[ConfirmedSector] = Field(
        default_factory=list,
        description="四维交叉验证后的板块推荐（资金+趋势+环境+消息），Phase 2 直接消费",
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
