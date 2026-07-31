"""
Node 6: 聚合节点（v2 — 置信度加权聚合 + 条件迭代精炼）

设计理念（基于 15+ 篇论文）：
- 投票优于辩论（Debate-or-Vote, NeurIPS 2025）
- 质量评估优于计数（AoR, LREC 2024）
- 概率聚合在 LLM 外部完成（ThinkSum, ACL 2023）
- 反 Sycophancy（ConsensAgent, ACL 2025）

三阶段流水线：
  Phase A: 信号标准化 + 置信度加权聚合（纯规则，零 LLM）
  Phase B: 异常检测（自动矛盾 + EMA 时间一致性 + 消息面覆盖）
  Phase C: 条件 LLM 精炼（最多 2 次调用，分歧大或异常时触发）
"""

import asyncio
import itertools
import json
import logging
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
    DimensionSignal,
    ConvergenceProfile,
    DimensionContribution,
    AggregationResult,
)
from src.got.persistence import (
    load_previous_context,
    save_current_context,
    load_ema_baseline,
    save_ema_baseline,
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

# 信号投影权重（用于板块维度）
SECTOR_SCORE_SCALE = 100.0

# LLM 精炼触发阈值
AGREEMENT_THRESHOLD = 0.5
CONTRADICTION_CONFIDENCE_THRESHOLD = 0.4
CONTRADICTION_SCORE_THRESHOLD = -0.2

# Surprising Popularity: 异见置信度倍数阈值
OUTLIER_CONFIDENCE_RATIO = 1.5

# 仓位日限幅
POSITION_LIMIT_DELTA = 0.2


# =============================================================================
# 聚合节点
# =============================================================================

class AggregatorNode:
    """Node 6: 置信度加权聚合 + 条件迭代精炼"""

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
        """执行聚合分析（三阶段流水线）"""
        if self.use_mock:
            logger.info("[Node 6] 使用 mock 数据")
            return self._mock_output()

        logger.info("[Node 6] 开始 v2 聚合分析")
        today = date.today()

        # ── Phase A: 信号标准化 + 置信度加权聚合 ──
        signals = self._project_to_signal_space(market, sector, capital, news, sentiment)
        aggregation = self._confidence_weighted_aggregate(signals)

        # 计算信号置信度均值（衡量市场方向清晰度）
        active_signals = [s for s in signals if s.status != "failed"]
        sig_conf_avg = (
            sum(s.confidence for s in active_signals) / len(active_signals)
            if active_signals else 0.0
        )
        insufficient_data = sig_conf_avg < 0.5

        logger.info(
            f"[Node 6] Phase A — weighted_score={aggregation.weighted_score:.3f}, "
            f"convergence={aggregation.convergence.pattern}, "
            f"agreement={aggregation.convergence.agreement_score:.2f}, "
            f"sig_conf_avg={sig_conf_avg:.2f}, insufficient={insufficient_data}"
        )

        # ── 信息充分度门控 ──
        if insufficient_data:
            logger.warning(
                f"[Node 6] 信号置信度均值 {sig_conf_avg:.2f} < 0.5，"
                f"市场无可操作信号，建议空仓观望"
            )
            result = self._build_insufficient_data_output(
                signals, aggregation, sig_conf_avg, news, today,
            )
            # 仍需持久化（EMA 基线需要每日更新）
            self._save_context(result, market, sector, aggregation, today)
            # 回写 EMA 基线
            updated_ema = load_ema_baseline()
            if updated_ema:
                result.ema_baseline = updated_ema.get("weighted_score")
            return result

        # ── Phase B: 异常检测 ──
        contradictions = self._auto_detect_contradictions(signals)
        temporal = self._adaptive_temporal_check(aggregation)
        override = await self._simplified_news_override(news, market)
        logger.info(
            f"[Node 6] Phase B 完成 — {len(contradictions)} 对矛盾, "
            f"{len(temporal)} 个时间异常, override={override is not None}"
        )

        # ── Phase C: 条件 LLM 精炼 ──
        needs_llm = self._needs_llm_refinement(aggregation, contradictions, temporal, override)
        if needs_llm:
            logger.info("[Node 6] Phase C: 触发 LLM 精炼")
            verdict = await self._llm_generate(aggregation, contradictions, temporal, override, signals)
            verdict = await self._llm_critique(verdict, signals)
        else:
            logger.info("[Node 6] Phase C: 跳过 LLM，使用规则输出")
            verdict = self._rule_based_verdict(aggregation, market, capital)

        # ── 构建输出 ──
        # 日限幅
        final_position = clip(
            verdict.get("final_position_limit", 0.5),
            market.position_limit - POSITION_LIMIT_DELTA,
            market.position_limit + POSITION_LIMIT_DELTA,
        )

        key_risks = self._collect_key_risks(news, sentiment, verdict, temporal)
        key_opportunities = self._collect_key_opportunities(sector, news, verdict)

        unified = UnifiedContext(
            status="manual_review_required" if self._has_critical(temporal) else "normal",
            unified_signal=verdict.get("unified_signal", "neutral"),
            overall_confidence=clip(verdict.get("overall_confidence", 0.5), 0.0, 1.0),
            final_position_limit=clip(final_position, 0.0, 1.0),
            contradictions=contradictions,
            common_root_cause=verdict.get("common_root_cause"),
            news_override=override,
            temporal_inconsistencies=temporal,
            key_risks=key_risks,
            key_opportunities=key_opportunities,
            concept_suggestions=news.concept_suggestions,
            reasoning=verdict.get("reasoning"),
            data_quality=self._build_data_quality(signals),
            convergence_profile=aggregation.convergence,
            weighted_score=aggregation.weighted_score,
            dimension_contributions=aggregation.dimension_contributions,
            llm_refinement_used=needs_llm,
            ema_baseline=None,  # 从 temporal 异常中提取
            signal_confidence_avg=round(sig_conf_avg, 3),
            insufficient_data=insufficient_data,
        )

        # 持久化
        self._save_context(unified, market, sector, aggregation, today)

        # 回写 EMA 基线到输出（_save_context 已更新磁盘）
        updated_ema = load_ema_baseline()
        if updated_ema:
            unified.ema_baseline = updated_ema.get("weighted_score")

        logger.info(
            f"[Node 6] 聚合完成 — 信号: {unified.unified_signal}, "
            f"置信度: {unified.overall_confidence:.2f}, "
            f"仓位: {unified.final_position_limit:.0%}, "
            f"LLM: {'是' if needs_llm else '否'}"
        )
        return unified

    # =========================================================================
    # Phase A: 信号标准化 + 置信度加权聚合
    # =========================================================================

    @staticmethod
    def _project_to_signal_space(
        market: MarketContext,
        sector: SectorContext,
        capital: CapitalContext,
        news: NewsContext,
        sentiment: SentimentContext,
    ) -> list[DimensionSignal]:
        """将 5 个维度 ctx 投影到统一信号空间 (-1, +1)

        data_confidence: 数据完整度（available / total）→ 聚合权重衰减
        confidence:     信号置信度（节点自评）→ 信息充分度门控
        """
        signals = []

        # 1. 大盘技术面
        #    data_confidence: K线数据完整度
        dq = market.data_quality
        total_fields = len(dq.available_fields) + len(dq.missing_fields) + len(dq.fetch_failed_fields)
        market_data_conf = len(dq.available_fields) / max(total_fields, 1)
        #    signal_confidence: 节点自评（已含 ADX/波动率/时间框架一致性调整）
        market_sig_conf = market.confidence
        market_score = market.tech_growth.score
        signals.append(DimensionSignal(
            name="market",
            score=clip(market_score, -1.0, 1.0),
            confidence=clip(market_sig_conf, 0.0, 1.0),
            data_confidence=clip(market_data_conf, 0.0, 1.0),
            status="normal" if market_data_conf > 0.5 else "degraded",
        ))

        # 2. 板块
        #    data_confidence: 板块数据是否完整
        sdq = sector.data_quality
        s_total = len(sdq.available_fields) + len(sdq.missing_fields) + len(sdq.fetch_failed_fields)
        sector_data_conf = len(sdq.available_fields) / max(s_total, 1) if s_total > 0 else 1.0
        #    signal_confidence: 有主线→高，无主线但数据完整→0.5（"市场确实无方向"是有价值信息）
        if sector.active_mainline:
            sector_sig_conf = 0.7
        elif sector.market_direction == "分化":
            sector_sig_conf = 0.5  # 数据完整 + 结论是"无方向" → 中等置信
        else:
            sector_sig_conf = 0.3
        sector_score = AggregatorNode._compute_sector_score(sector)
        signals.append(DimensionSignal(
            name="sector",
            score=clip(sector_score, -1.0, 1.0),
            confidence=sector_sig_conf,
            data_confidence=clip(sector_data_conf, 0.0, 1.0),
            status="normal",
        ))

        # 3. 资金面
        #    data_confidence: 基于 confidence_multiplier（缺失维度比例）
        capital_data_conf = capital.confidence_multiplier
        capital_status = capital.status
        signals.append(DimensionSignal(
            name="capital",
            score=clip(capital.institutional_score, -1.0, 1.0),
            confidence=capital_data_conf if capital_status != "failed" else 0.0,
            data_confidence=capital_data_conf if capital_status != "failed" else 0.0,
            status=capital_status,
        ))

        # 4. 消息面
        #    data_confidence: status != failed → 完整
        news_data_conf = 1.0 if news.status != "failed" else 0.0
        signals.append(DimensionSignal(
            name="news",
            score=clip(news.impact_score, -1.0, 1.0),
            confidence=0.8 if news.status != "failed" else 0.0,
            data_confidence=news_data_conf,
            status=news.status,
        ))

        # 5. 情绪面
        #    data_confidence: 基于数据质量
        sedq = sentiment.data_quality
        se_total = len(sedq.available_fields) + len(sedq.missing_fields) + len(sedq.fetch_failed_fields)
        sentiment_data_conf = len(sedq.available_fields) / max(se_total, 1) if se_total > 0 else 1.0
        #    signal_confidence: 节点自评
        signals.append(DimensionSignal(
            name="sentiment",
            score=clip(sentiment.sentiment_score, -1.0, 1.0),
            confidence=sentiment.signal_confidence if sentiment.status != "failed" else 0.0,
            data_confidence=sentiment_data_conf if sentiment.status != "failed" else 0.0,
            status=sentiment.status,
        ))

        return signals

    @staticmethod
    def _compute_sector_score(sector: SectorContext) -> float:
        """从板块 ctx 计算统一方向分"""
        # 主线板块平均强度 → 正向
        if sector.active_mainline:
            avg_strength = sum(s.strength_score for s in sector.active_mainline) / len(sector.active_mainline)
            mainline_score = avg_strength / SECTOR_SCORE_SCALE  # 归一化到 0~1
        else:
            mainline_score = 0.0

        # market_direction 映射
        direction_map = {"普涨": 0.3, "分化": 0.0, "普跌": -0.3}
        direction_adj = direction_map.get(sector.market_direction, 0.0)

        return clip(mainline_score + direction_adj, -1.0, 1.0)

    @staticmethod
    def _confidence_weighted_aggregate(signals: list[DimensionSignal]) -> AggregationResult:
        """置信度加权聚合

        权重使用 data_confidence（数据完整度），而非 confidence（信号置信度）。
        - 数据缺失的维度 → 降权（Node 3 缺北向/ETF → 0.6 权重）
        - 数据完整但信号模糊 → 不降权（Node 1 波动大 → 仍 1.0 权重，但 confidence=0.3 被记录）
        """
        active = [s for s in signals if s.status != "failed" and s.data_confidence > 0]
        failed_count = len(signals) - len(active)

        if not active:
            return AggregationResult(
                weighted_score=0.0,
                convergence=ConvergenceProfile(pattern="low_confidence", agreement_score=0.0),
                dimension_contributions=[],
                active_count=0,
                failed_count=failed_count,
            )

        # 用 data_confidence 做权重
        total_weight = sum(s.data_confidence for s in active)
        weighted_score = sum(s.score * s.data_confidence for s in active) / total_weight

        # 各维度贡献明细（含 data_confidence 和 signal_confidence）
        contributions = [
            DimensionContribution(
                name=s.name,
                score=s.score,
                confidence=s.confidence,
                data_confidence=s.data_confidence,
                contribution=(s.score * s.data_confidence) / total_weight,
            )
            for s in active
        ]

        # 收敛剖面（用 data_confidence 加权方向判断）
        convergence = AggregatorNode._classify_convergence(active, weighted_score)

        return AggregationResult(
            weighted_score=clip(weighted_score, -1.0, 1.0),
            convergence=convergence,
            dimension_contributions=contributions,
            active_count=len(active),
            failed_count=failed_count,
        )

    @staticmethod
    def _classify_convergence(
        active: list[DimensionSignal],
        weighted_score: float,
    ) -> ConvergenceProfile:
        """分类收敛剖面"""
        if len(active) < 2:
            return ConvergenceProfile(pattern="low_confidence", agreement_score=1.0)

        # 方向量化：+1 / 0 / -1
        directions = []
        for s in active:
            if s.score > 0.1:
                directions.append(1)
            elif s.score < -0.1:
                directions.append(-1)
            else:
                directions.append(0)

        # 一致性 = |正向数 - 反向数| / 总数
        positive = sum(1 for d in directions if d > 0)
        negative = sum(1 for d in directions if d < 0)
        neutral = sum(1 for d in directions if d == 0)
        agreement = abs(positive - negative) / len(active)

        # 分类
        if positive == len(active) or negative == len(active):
            pattern = "full_convergence"
        elif neutral == len(active):
            pattern = "low_confidence"
        elif positive >= len(active) - 1 or negative >= len(active) - 1:
            # 检查 lone outlier
            outlier = AggregatorNode._find_lone_outlier(active, directions)
            if outlier:
                avg_conf = sum(s.confidence for s in active) / len(active)
                investigate = outlier.confidence > avg_conf * OUTLIER_CONFIDENCE_RATIO
                return ConvergenceProfile(
                    pattern="lone_outlier",
                    agreement_score=agreement,
                    investigate_outlier=investigate,
                )
            pattern = "mild_divergence"
        elif positive > 0 and negative > 0:
            # 两派对立
            ratio = min(positive, negative) / max(positive, negative)
            pattern = "strong_divergence" if ratio > 0.5 else "mild_divergence"
        else:
            pattern = "mild_divergence"

        return ConvergenceProfile(pattern=pattern, agreement_score=round(agreement, 3))

    @staticmethod
    def _find_lone_outlier(
        active: list[DimensionSignal],
        directions: list[int],
    ) -> Optional[DimensionSignal]:
        """找到孤立异见者（一个与其余方向相反的维度）"""
        for i, d in enumerate(directions):
            if d == 0:
                continue
            others = [directions[j] for j in range(len(directions)) if j != i and directions[j] != 0]
            if others and all(o != d for o in others) and len(set(others)) == 1:
                return active[i]
        return None

    # =========================================================================
    # Phase B: 异常检测
    # =========================================================================

    @staticmethod
    def _auto_detect_contradictions(signals: list[DimensionSignal]) -> list[Contradiction]:
        """自动矛盾检测（替代硬编码 10 对）

        仅在 data_confidence > 阈值 的维度间检测（数据缺失不参与矛盾判定）
        """
        active = [s for s in signals if s.status != "failed" and s.data_confidence > CONTRADICTION_CONFIDENCE_THRESHOLD]
        contradictions = []

        for a, b in itertools.combinations(active, 2):
            # 方向相反且各自置信度足够
            if a.score * b.score < CONTRADICTION_SCORE_THRESHOLD:
                conflict_type = AggregatorNode._classify_conflict(a, b)
                severity = "high" if min(abs(a.score), abs(b.score)) > 0.5 else "medium"

                contradictions.append(Contradiction(
                    pair=f"{a.name}_vs_{b.name}",
                    timeframe="cross",
                    description=f"{a.name}(score={a.score:.2f}) vs {b.name}(score={b.score:.2f})",
                    severity=severity,
                    conflict_type=conflict_type,
                ))

        return contradictions

    @staticmethod
    def _classify_conflict(a: DimensionSignal, b: DimensionSignal) -> str:
        """矛盾分类（参考 ConflictRAG）: factual / temporal / opinion

        - factual: 两个维度测量重叠现象（价量/资金都反映市场动态）
        - temporal: 存在时间滞后效应（消息面领先情绪面，技术面领先资金面）
        - opinion: 方法论根本不同（技术面 vs 情绪面）
        """
        names = frozenset({a.name, b.name})

        # 时间滞后型：消息面→情绪面存在传导时滞，技术面→资金面有领先/跟随关系
        if names == {"news", "sentiment"} or names == {"market", "capital"}:
            return "temporal"
        # 事实型：测量重叠现象（价量与资金、板块与大盘）
        if names <= {"market", "sector", "capital"}:
            return "factual"
        # 其余为方法论分歧
        return "opinion"

    @staticmethod
    def _adaptive_temporal_check(aggregation: AggregationResult) -> list[TemporalInconsistency]:
        """EMA 基线时间一致性校验（替代固定阈值）"""
        ema_baseline = load_ema_baseline()
        inconsistencies = []

        if ema_baseline is None:
            return inconsistencies  # 首次运行，跳过

        prev_score = ema_baseline.get("weighted_score", 0.0)
        prev_std = ema_baseline.get("score_std", 0.05)
        sample_count = ema_baseline.get("sample_count", 0)

        current_score = aggregation.weighted_score

        # z-score 异常检测
        std_floor = max(prev_std, 0.05)  # 防止除零
        z_score = abs(current_score - prev_score) / std_floor

        # 冷启动期使用宽松阈值
        if sample_count < EMA_WARMUP_SAMPLES:
            critical_threshold = 5.0
            high_threshold = 3.5
        else:
            critical_threshold = 3.0
            high_threshold = 2.0

        if z_score > critical_threshold:
            inconsistencies.append(TemporalInconsistency(
                type="sudden_trend_flip",
                description=f"EMA z-score={z_score:.2f}: {prev_score:.3f} → {current_score:.3f} (σ={prev_std:.3f})",
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
        """简化消息面覆盖（砍掉历史准确率表）"""
        # 第一层：系统性政策事件 + 多源确认
        overridable = [
            e for e in news.major_events
            if e.event_type == "policy"
            and e.impact_level == "high"
            and e.source_count >= 3
            and any(kw in e.title or kw in e.summary for kw in SYSTEMIC_KEYWORDS)
        ]

        if not overridable:
            return None

        # 第二层：LLM 判断
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

    # =========================================================================
    # Phase C: 条件 LLM 精炼
    # =========================================================================

    @staticmethod
    def _needs_llm_refinement(
        aggregation: AggregationResult,
        contradictions: list[Contradiction],
        temporal: list[TemporalInconsistency],
        override: Optional[NewsOverride],
    ) -> bool:
        """判断是否需要 LLM 精炼

        触发条件（任一满足即触发）：
        1. agreement < 0.5（维度间分歧较大）
        2. 时间异常 severity ≥ high
        3. 消息面覆盖激活
        4. convergence pattern 为 strong_divergence / lone_outlier
        5. mild_divergence 且有矛盾对（维度方向不完全一致）
        """
        if aggregation.convergence.agreement_score < AGREEMENT_THRESHOLD:
            return True
        if any(c.severity in ("high", "critical") for c in temporal):
            return True
        if override and override.override:
            return True
        if aggregation.convergence.pattern in ("strong_divergence", "lone_outlier"):
            return True
        # mild_divergence + 存在矛盾对 → 需要 LLM 研判
        if aggregation.convergence.pattern == "mild_divergence" and contradictions:
            return True
        return False

    @staticmethod
    async def _llm_generate(
        aggregation: AggregationResult,
        contradictions: list[Contradiction],
        temporal: list[TemporalInconsistency],
        override: Optional[NewsOverride],
        signals: list[DimensionSignal],
    ) -> dict:
        """Phase C1: 生成阶段"""
        # 构建信号摘要
        signals_text = "\n".join(
            f"- {s.name}: score={s.score:.2f}, confidence={s.confidence:.2f}, status={s.status}"
            for s in signals
        )
        contradictions_text = "\n".join(
            f"- {c.pair} ({c.conflict_type}, {c.severity}): {c.description}"
            for c in contradictions
        ) or "无矛盾"
        temporal_text = "\n".join(
            f"- [{t.severity}] {t.type}: {t.description}"
            for t in temporal
        ) or "无异常"

        prompt = f"""你是资深投资顾问。以下是 5 维度置信度加权聚合结果。

各维度信号：
{signals_text}

聚合结果：
- 加权方向分: {aggregation.weighted_score:.3f}
- 收敛剖面: {aggregation.convergence.pattern}
- 一致性: {aggregation.convergence.agreement_score:.2f}
- 活跃维度: {aggregation.active_count}, 降级: {aggregation.failed_count}

矛盾检测：
{contradictions_text}

时间一致性异常：
{temporal_text}

消息面覆盖: {"有" if override else "无"}

请输出 JSON（不要包含其他内容）：
{{"unified_signal": "bullish/bearish/neutral/cautious", "overall_confidence": 0.0~1.0, "final_position_limit": 0.0~1.0, "key_risks": ["风险1", "风险2"], "key_opportunities": ["机会1", "机会2"], "reasoning": "100字以内综合研判"}}"""

        raw_text = await AggregatorNode._call_llm_async(prompt, temperature=0.3, max_tokens=600)
        try:
            return AggregatorNode._parse_json(raw_text)
        except Exception as e:
            logger.warning(f"[Node 6] C1 生成阶段 JSON 解析失败: {e}")
            return {}

    @staticmethod
    async def _llm_critique(verdict: dict, signals: list[DimensionSignal]) -> dict:
        """Phase C2: 批判阶段（反 sycophancy）"""
        if not verdict:
            return verdict

        signals_text = "\n".join(
            f"- {s.name}: score={s.score:.2f} (confidence={s.confidence:.2f})"
            for s in signals if s.status != "failed"
        )

        prompt = f"""你是严格的批判者。以下是一个初始研判和各维度原始信号。

初始研判：
- 信号: {verdict.get('unified_signal')}
- 置信度: {verdict.get('overall_confidence')}
- 仓位: {verdict.get('final_position_limit')}
- 理由: {verdict.get('reasoning')}

各维度原始信号：
{signals_text}

请批判性审视这个研判：
1. 是否忽略了某个高置信度异见维度？
2. 仓位是否过高或过低？
3. 有什么风险被遗漏？

请输出 JSON（如需调整则修改对应字段，如确认原判则原样输出）：
{{"unified_signal": "...", "overall_confidence": 0.0~1.0, "final_position_limit": 0.0~1.0, "key_risks": [...], "key_opportunities": [...], "reasoning": "...", "critique_note": "你的批判意见（30字内）"}}"""

        raw_text = await AggregatorNode._call_llm_async(prompt, temperature=0.2, max_tokens=600)
        try:
            result = AggregatorNode._parse_json(raw_text)
            # 附加批判笔记到 reasoning
            critique_note = result.pop("critique_note", None)
            if critique_note:
                original_reasoning = result.get("reasoning", "")
                result["reasoning"] = f"{original_reasoning}\n[批判] {critique_note}"
            return result
        except Exception as e:
            logger.warning(f"[Node 6] C2 批判阶段 JSON 解析失败: {e}，使用 C1 结果")
            return verdict

    # =========================================================================
    # 规则输出（Phase C 不触发时）
    # =========================================================================

    @staticmethod
    def _rule_based_verdict(
        aggregation: AggregationResult,
        market: MarketContext,
        capital: CapitalContext,
    ) -> dict:
        """规则映射（替代 LLM 综合）"""
        ws = aggregation.weighted_score
        agreement = aggregation.convergence.agreement_score

        # 信号判定
        if ws > 0.3 and agreement > 0.6:
            signal = "bullish"
            position = clip(0.5 + ws * 0.5, 0.3, 0.8)
        elif ws < -0.3 and agreement > 0.6:
            signal = "bearish"
            position = clip(0.3 + ws * 0.3, 0.0, 0.3)
        elif agreement < 0.3:
            signal = "cautious"
            position = 0.2
        else:
            signal = "neutral"
            position = clip(0.3 + ws * 0.2, 0.1, 0.5)

        # 资金面置信度调节
        position *= capital.confidence_multiplier

        confidence = clip(agreement * 0.8 + 0.2, 0.0, 1.0)

        return {
            "unified_signal": signal,
            "overall_confidence": round(confidence, 3),
            "final_position_limit": round(clip(position, 0.0, 1.0), 3),
            "key_risks": [],
            "key_opportunities": [],
            "reasoning": (
                f"[规则输出] weighted_score={ws:.3f}, agreement={agreement:.2f}, "
                f"convergence={aggregation.convergence.pattern}"
            ),
        }

    @staticmethod
    def _build_insufficient_data_output(
        signals: list[DimensionSignal],
        aggregation: AggregationResult,
        sig_conf_avg: float,
        news: NewsContext,
        today: date,
    ) -> UnifiedContext:
        """信息充分度不足 → 空仓观望输出

        当 signal_confidence_avg < 0.5 时触发，说明多数维度自评"看不清方向"。
        这不是数据缺失（那是 data_confidence 管的），而是市场本身没有给出可操作信号。
        """
        # 汇总各维度的低置信原因
        low_conf_dims = [
            f"{s.name}(conf={s.confidence:.2f}, score={s.score:+.2f})"
            for s in signals
            if s.status != "failed" and s.confidence < 0.5
        ]

        reasoning = (
            f"信号置信度均值 {sig_conf_avg:.2f} < 0.5，"
            f"市场无可操作信号。"
            f"低置信维度: {', '.join(low_conf_dims)}。"
            f"建议空仓观望，等待更明确的市场方向。"
        )

        unified = UnifiedContext(
            status="insufficient_data",
            unified_signal="neutral",
            overall_confidence=round(sig_conf_avg, 3),
            final_position_limit=0.0,  # 空仓
            contradictions=[],
            common_root_cause=None,
            news_override=None,
            temporal_inconsistencies=[],
            key_risks=["市场信号模糊，不具备操作条件"],
            key_opportunities=[],
            concept_suggestions=news.concept_suggestions,
            reasoning=reasoning,
            data_quality=AggregatorNode._build_data_quality(signals),
            convergence_profile=aggregation.convergence,
            weighted_score=aggregation.weighted_score,
            dimension_contributions=aggregation.dimension_contributions,
            llm_refinement_used=False,
            ema_baseline=None,
            signal_confidence_avg=round(sig_conf_avg, 3),
            insufficient_data=True,
        )

        # 持久化（仍需保存，供次日 EMA 基线更新）
        return unified

    # =========================================================================
    # 输出构建辅助
    # =========================================================================

    @staticmethod
    def _has_critical(temporal: list[TemporalInconsistency]) -> bool:
        return any(t.severity == "critical" for t in temporal)

    @staticmethod
    def _collect_key_risks(
        news: NewsContext,
        sentiment: SentimentContext,
        verdict: dict,
        temporal: list[TemporalInconsistency],
    ) -> list[str]:
        """汇总关键风险"""
        risks = list(news.key_risks[:3])

        # 情绪极端
        if sentiment.sentiment_stage == "euphoria":
            risks.append("散户情绪极度狂热（反向信号）")
        elif sentiment.sentiment_stage == "capitulation":
            risks.append("散户极度恐慌（市场可能超跌）")

        # 时间异常
        for t in temporal:
            if t.severity in ("critical", "high"):
                risks.append(f"[时间异常] {t.description}")

        # LLM 输出
        for r in verdict.get("key_risks", []):
            if r not in risks:
                risks.append(r)

        return risks[:6]

    @staticmethod
    def _collect_key_opportunities(
        sector: SectorContext,
        news: NewsContext,
        verdict: dict,
    ) -> list[str]:
        """汇总关键机会"""
        opps = []
        for s in sector.active_mainline[:2]:
            if s.state != "neutral":
                opps.append(f"主线板块: {s.name}（{s.state}）")
        for s in sector.ambush[:2]:
            opps.append(f"埋伏板块: {s.name}")
        for o in verdict.get("key_opportunities", []):
            if o not in opps:
                opps.append(o)
        return opps[:5]

    @staticmethod
    def _build_data_quality(signals: list[DimensionSignal]) -> DataQuality:
        """汇总数据质量"""
        available = [s.name for s in signals if s.status == "normal"]
        missing = []  # 暂不细分
        failed = [s.name for s in signals if s.status == "failed"]

        return DataQuality(
            available_fields=available,
            missing_fields=missing,
            fetch_failed_fields=failed,
        )

    def _save_context(
        self,
        unified: UnifiedContext,
        market: MarketContext,
        sector: SectorContext,
        aggregation: AggregationResult,
        today: date,
    ) -> None:
        """持久化 + 更新 EMA 基线"""
        # 1. 保存当日上下文
        context_dict = {
            "date": today.isoformat(),
            "unified_signal": unified.unified_signal,
            "overall_confidence": unified.overall_confidence,
            "final_position_limit": unified.final_position_limit,
            "weighted_score": aggregation.weighted_score,
            "convergence_pattern": aggregation.convergence.pattern,
            "agreement_score": aggregation.convergence.agreement_score,
            "market_trend": market.tech_growth.trend,
            "market_score": market.tech_growth.score,
            "active_mainline_codes": [s.code for s in sector.active_mainline],
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
                    "weighted_score": aggregation.weighted_score,
                    "score_std": 0.1,  # 初始标准差
                    "last_updated": today.isoformat(),
                    "sample_count": 1,
                }
            else:
                prev_score = prev_baseline["weighted_score"]
                prev_std = prev_baseline["score_std"]
                prev_count = prev_baseline.get("sample_count", 1)

                # EMA 更新
                new_score = EMA_ALPHA * aggregation.weighted_score + (1 - EMA_ALPHA) * prev_score

                # 滚动标准差更新（Welford 在线算法）
                delta = aggregation.weighted_score - prev_score
                ss = (prev_count - 1) * prev_std**2  # 当前平方偏差和
                new_ss = ss + delta**2 * prev_count / (prev_count + 1)
                new_var = new_ss / prev_count  # 样本方差（n+1 样本，除以 n）
                new_std = max(new_var**0.5, 0.01)

                new_baseline = {
                    "weighted_score": new_score,
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
        """异步 LLM 调用（含空响应重试）"""
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
        """从 LLM 输出中提取 JSON（兼容 markdown code block）"""
        import re
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if match:
            text = match.group(1)
        return json.loads(text)

    # =========================================================================
    # Mock 输出
    # =========================================================================

    def _mock_output(self) -> UnifiedContext:
        """Mock 输出（含 v2 新字段）"""
        mock_convergence = ConvergenceProfile(
            pattern="mild_divergence",
            agreement_score=0.6,
        )
        mock_contributions = [
            DimensionContribution(name="market", score=-0.2, confidence=0.3, data_confidence=1.0, contribution=-0.08),
            DimensionContribution(name="sector", score=0.1, confidence=0.5, data_confidence=1.0, contribution=0.04),
            DimensionContribution(name="capital", score=0.3, confidence=0.6, data_confidence=0.6, contribution=0.12),
            DimensionContribution(name="news", score=0.1, confidence=0.8, data_confidence=1.0, contribution=0.04),
            DimensionContribution(name="sentiment", score=-0.3, confidence=0.3, data_confidence=1.0, contribution=-0.12),
        ]

        return UnifiedContext(
            status="normal",
            unified_signal="cautious",
            overall_confidence=0.55,
            final_position_limit=0.20,
            contradictions=[
                Contradiction(
                    pair="market_vs_capital",
                    timeframe="cross",
                    description="market(score=-0.20) vs capital(score=0.30)",
                    severity="medium",
                    conflict_type="factual",
                    resolution="技术面偏空但机构在加仓，可能是左侧布局",
                    confidence_adjustment=-0.05,
                ),
            ],
            common_root_cause=None,
            news_override=None,
            temporal_inconsistencies=[],
            key_risks=["市场分歧较大，注意仓位控制"],
            key_opportunities=["机构持续加仓，关注底部信号"],
            concept_suggestions=[],
            reasoning=(
                "[Mock v2] weighted_score=-0.01, convergence=mild_divergence, "
                "agreement=0.60, sig_conf_avg=0.50。"
            ),
            data_quality=DataQuality(
                available_fields=["market", "sector", "capital", "news", "sentiment"],
            ),
            convergence_profile=mock_convergence,
            weighted_score=-0.01,
            dimension_contributions=mock_contributions,
            llm_refinement_used=True,
            ema_baseline=None,
            signal_confidence_avg=0.50,
            insufficient_data=False,
        )
