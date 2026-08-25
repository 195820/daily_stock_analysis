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
)
from src.got.catalyst_calendar import _count_trade_days
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

        # ── Level 0: 全局环境感知 ──
        regime = self._compute_market_regime(market, sector, capital, news, sentiment)
        logger.info(
            f"[Node 6] Level 0 — regime={regime.regime}, "
            f"risk_budget={regime.risk_budget:.2f}, "
            f"fear_greed={regime.fear_greed:.2f}"
        )

        # ── Level 1 平行分支: 埋伏通道（P3，先于 mini GOT 分类，供拆分消费）──
        ambush_pool, ambush_reservoir, ambush_veto_log = self._build_ambush_channel(
            sector, capital, news, sentiment,
        )

        # ── Level 1: 板块 mini GOT（主线/降温走 TOP-N，埋伏按通道结果拆分消费）──
        sector_intels = self._build_sector_intel(
            sector, capital, news, sentiment,
            ambush_pool=ambush_pool, ambush_reservoir=ambush_reservoir,
        )
        # 将埋伏通道结果回写到 SectorIntel（供 reasoning.md 和 Phase 2 消费）
        self._enrich_ambush_sector_intel(sector_intels, ambush_pool, ambush_reservoir)

        logger.info(
            f"[Node 6] Level 1 — {len(sector_intels)} 个板块情报, "
            f"actionable={sum(1 for s in sector_intels if s.classification == 'actionable')}, "
            f"caution={sum(1 for s in sector_intels if s.classification == 'caution')}, "
            f"exclude={sum(1 for s in sector_intels if s.classification == 'exclude')}"
            f" | 埋伏通道: 正式池={len(ambush_pool)}, 蓄水池={len(ambush_reservoir)}, "
            f"否决={len(ambush_veto_log)}"
        )

        # LLM 精炼板块情报
        sector_intels = await self._llm_refine_sectors(sector_intels, regime)

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
        )

        # 持久化
        self._save_context(unified, regime, sector_intels, today)

        # 回写 EMA 基线
        updated_ema = load_ema_baseline()
        if updated_ema:
            unified.ema_baseline = updated_ema.get("regime_score")

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

        if not candidates:
            return []

        # 预提取各维度数据
        capital_flows = self._parse_capital_flows(capital)
        news_keywords = self._extract_news_keywords(news)
        hot_sectors = self._extract_hot_sectors(sentiment)

        # ── Pass 1: 计算所有板块的 composite score 和验证数据 ──
        # 不创建 SectorIntel，先收集原始数据用于批量分类
        raw_data = []  # [(name, composite, capital_check, news_check, detail, sector_role, ...)]
        for name, detail, role_info in candidates:
            sector_role = role_info.get("role", "mainline")
            composite, capital_check, news_check, other = self._compute_sector_scores(
                name, detail, capital_flows, news_keywords, hot_sectors, role_info,
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

    def _compute_sector_scores(
        self,
        name: str,
        detail: Optional[SectorDetail],
        capital_flows: list[tuple[str, float]],
        news_keywords: list[tuple[str, dict]],
        hot_sectors: list[str],
        role_info: Optional[dict] = None,
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

        # ── 维度 2: 资金面 ──
        capital_check, capital_detail = self._cross_check_capital(name, capital_flows)

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
    ) -> tuple[str, str]:
        """资金面交叉验证: (check, detail)"""
        if not flows:
            return "neutral", "资金面数据缺失"

        # 模糊匹配：板块名是否出现在资金流 top 中
        for flow_name, amount in flows:
            if self._fuzzy_match(name, flow_name):
                if amount > 10:
                    return "aligned", f"{flow_name}净流入{amount:.1f}亿"
                else:
                    return "aligned", f"{flow_name}净流入{amount:.1f}亿（小额）"

        # 子词匹配（剥离泛化后缀后）
        clean_name = self._strip_generic_suffix(name)
        name_parts = self._split_sector_name(clean_name)
        for flow_name, amount in flows:
            clean_flow = self._strip_generic_suffix(flow_name)
            for part in name_parts:
                if len(part) >= 2 and (part in clean_flow or clean_flow in part):
                    if amount > 10:
                        return "aligned", f"关联[{flow_name}]净流入{amount:.1f}亿"
                    else:
                        return "neutral", f"关联[{flow_name}]流入{amount:.1f}亿"

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

        name_parts = self._split_sector_name(name)
        matched: list[dict] = []

        for kw, meta in keywords:
            for part in name_parts:
                if len(part) >= 2 and (part in kw or kw in part or part in meta["title"]):
                    matched.append(meta)
                    break

        if not matched:
            return "neutral", f"{name}无直接消息催化", 0.0

        # 去重（按标题）
        seen = set()
        unique = []
        for meta in matched:
            if meta["title"] not in seen:
                seen.add(meta["title"])
                unique.append(meta)

        pos_adjust = 0.0
        neg_adjust = 0.0
        pos_count = 0
        neg_count = 0
        global_hit = False

        for meta in unique:
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
            if sentiment == "positive":
                pos_adjust += magnitude
                pos_count += 1
            else:
                neg_adjust -= magnitude
                neg_count += 1

        pos_adjust = min(pos_adjust, self._NEWS_POS_CAP)
        neg_adjust = max(neg_adjust, self._NEWS_NEG_CAP)
        adjustment = pos_adjust + neg_adjust

        top_title = unique[0]["title"][:30]
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
        """情绪面交叉验证: (check, detail)"""
        if not hot_sectors:
            return "low", "无热榜数据"

        # 检查板块名是否出现在热榜关联板块中
        name_parts = self._split_sector_name(name)
        match_count = 0

        for hot_sector in hot_sectors:
            for part in name_parts:
                if len(part) >= 2 and (part in hot_sector or hot_sector in part):
                    match_count += 1
                    break

        if match_count >= 3:
            return "high", f"{match_count}只关联股票上热榜"
        elif match_count >= 1:
            return "medium", f"{match_count}只关联股票上热榜"
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
    ) -> list[SectorIntel]:
        """LLM 精炼板块情报（可选，生成 narrative + 跨板块风险）"""
        if not sectors:
            return sectors

        # 构建板块情报文本
        sector_lines = []
        for i, s in enumerate(sectors[:10], 1):  # 只取前 10 个
            sector_lines.append(
                f"{i}. {s.sector_name} | {s.classification} | score={s.composite_score:.0f}\n"
                f"   {s.tech_detail}\n"
                f"   资金: {s.capital_check}（{s.capital_detail}）\n"
                f"   催化: {s.news_check}（{s.news_detail}）\n"
                f"   热度: {s.sentiment_check}（{s.sentiment_detail}）"
            )

        sectors_text = "\n".join(sector_lines)

        prompt = f"""全局环境：regime={regime.regime}, risk_budget={regime.risk_budget:.2f}
恐贪指数：{regime.fear_greed:.2f}（{regime.sentiment_stage}）

板块情报：
{sectors_text}

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
    ) -> tuple[bool, str, str]:
        """4 层闸门流水线（★★★利空 + 吸筹确认 + 防买顶 + 拥挤度）。

        Returns:
            (passed, fail_reason, accum_detail)
            - passed=True: 闸门全通过，accum_detail 为吸筹确认细节
            - passed=False: fail_reason 为否决原因（空串=静默否决，不入 veto_log）
        """
        # 闸门 1: ★★★ 利空否决
        negative_block = self._check_negative_catalyst(sector_events)
        if negative_block:
            return False, f"★★★利空否决 — {negative_block}", ""

        # 闸门 2: 吸筹确认
        accumulation_ok, accum_detail = self._check_accumulation_gate(name, capital)
        if not accumulation_ok:
            return False, "", ""  # 静默否决（观望，不入 veto_log）

        # 闸门 3: 防买顶双闸
        anti_top_ok, anti_top_reason = self._check_anti_top_buy(
            name, hot_sectors, sector_events, today,
        )
        if not anti_top_ok:
            return False, f"防买顶否决 — {anti_top_reason}", ""

        # 闸门 4: 拥挤度黑名单
        if self._check_crowded_blacklist(name, hot_sectors):
            return False, "拥挤度黑名单（多只个股上热榜，来晚了）", ""

        return True, "", accum_detail

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
            # "不再是近失"本身是最强的退出信号，不需要等到过期
            if today_near_miss_names is not None and name not in today_near_miss_names and days >= 3:
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

        promoted_pool: list[AmbushCandidate] = []
        remaining: list[AmbushReservoirEntry] = []

        for entry in reservoir_entries:
            name = entry.sector_name

            # 条件 1: 驻留天数
            if entry.days_in_reservoir < RESERVOIR_PROMOTION_MIN_DAYS:
                remaining.append(entry)
                continue

            # 条件 2: 有合格催化剂
            sector_events = events_by_sector.get(name, [])
            qualified_events = [
                e for e in sector_events
                if e.confidence >= CATALYST_CONFIDENCE_GATE
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

            # 升格 → 重新走闸门
            gate_passed, fail_reason, accum_detail = self._run_ambush_gates(
                name, sector_events, capital_ctx, hot_sectors, today,
            )

            if gate_passed:
                score, top_event, category_tag, event_titles = self._compute_catalyst_score(
                    qualified_events, today,
                )
                promoted_pool.append(AmbushCandidate(
                    sector_name=name,
                    sector_code=entry.sector_code,
                    catalyst_score=round(score, 3),
                    category_tag=category_tag,
                    events=event_titles[:3],
                    top_event_date=top_event.event_date if top_event else None,
                    top_event_level=top_event.impact_level if top_event else 1,
                    accumulation_detail=accum_detail,
                    gate_notes="蓄水池升格",
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

    def _build_ambush_channel(
        self,
        sector: SectorContext,
        capital: CapitalContext,
        news: NewsContext,
        sentiment: SentimentContext,
    ) -> tuple[list[AmbushCandidate], list[AmbushReservoirEntry], list[str]]:
        """埋伏通道（P3+P1-2）：独立于主线 composite 的平行分支

        流水线：
        1. 今日候选 → 闸门（★★★利空 + 吸筹确认 + 防买顶 + 拥挤度）→ 正式池/蓄水池
        2. 跨日合并：今日蓄水池 + 前日留存 → 合并列表（天数递增，过期除名）
        3. 升格检测：蓄水池中满足条件的板块 → 重新走闸门 → 正式池

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
                sector_events = events_by_sector.get(name, [])

                # 闸门流水线（抽取为独立方法，供升格复用）
                gate_passed, fail_reason, accum_detail = self._run_ambush_gates(
                    name, sector_events, capital, hot_sectors, today,
                )

                if not gate_passed:
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

                pool.append(AmbushCandidate(
                    sector_name=name,
                    sector_code=detail.code,
                    catalyst_score=round(score, 3),
                    category_tag=category_tag,
                    events=event_titles[:3],
                    top_event_date=top_event.event_date if top_event else None,
                    top_event_level=top_event.impact_level if top_event else 1,
                    accumulation_detail=accum_detail,
                    gate_notes="闸门全通过",
                ))

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

        logger.info(
            f"[Node 6] 埋伏通道: {len(sector.ambush)} 候选 → "
            f"正式池={len(pool)}, 蓄水池={len(reservoir)}, 否决={len(veto_log)}"
        )
        return pool, reservoir, veto_log

    @staticmethod
    def _group_events_by_sector(
        events: list[CatalystEvent],
    ) -> dict[str, list[CatalystEvent]]:
        """将催化剂事件按板块名分组"""
        grouped: dict[str, list[CatalystEvent]] = {}
        for event in events:
            grouped.setdefault(event.sector, []).append(event)
        return grouped

    @staticmethod
    def _check_negative_catalyst(events: list[CatalystEvent]) -> Optional[str]:
        """检查是否有 ★★★ 利空事件（直接否决）"""
        for event in events:
            if event.impact_level >= 3 and event.direction == "negative":
                return f"{event.title}({event.event_date})"
        return None

    @staticmethod
    def _check_accumulation_gate(
        sector_name: str,
        capital: CapitalContext,
    ) -> tuple[bool, str]:
        """吸筹确认闸门：连续 3 日小幅净流入

        判定依据：capital.evidence.sector_flow 中的板块资金流方向。
        注意：当前 CapitalContext 仅提供当日截面资金流（top_concepts/top_industries），
        无逐日序列。降级为"当日正向流入即通过"，待 P6 接入逐日数据后升级。
        """
        # 从 sector_flow_all 中查找板块净流入
        sector_flow = capital.evidence.sector_flow if capital and capital.evidence else None
        if not sector_flow:
            return False, "资金面数据缺失"

        sector_flow_all = sector_flow.get("sector_flow_all") or {}
        for name, amount in sector_flow_all.items():
            if not isinstance(name, str) or not name:
                continue
            clean = AggregatorNode._strip_generic_suffix(name)
            if AggregatorNode._fuzzy_match(sector_name, clean):
                try:
                    flow_val = float(amount)
                except (TypeError, ValueError):
                    continue
                if flow_val > 0:
                    return True, f"当日净流入{flow_val:.1f}亿"
                else:
                    return False, f"当日净流出{abs(flow_val):.1f}亿"

        # top_concepts / top_industries 模糊匹配
        for key in ("top_concepts", "top_industries"):
            items = sector_flow.get(key, [])
            for item in items:
                match = re.match(r'(.+?)\(([+-]?[\d.]+)亿\)', item)
                if match and AggregatorNode._fuzzy_match(sector_name, match.group(1)):
                    flow_val = float(match.group(2))
                    if flow_val > 0:
                        return True, f"当日净流入{flow_val:.1f}亿（{key}）"
                    return False, f"当日净流出{abs(flow_val):.1f}亿（{key}）"

        return False, "未找到板块资金流数据"

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

        if regime.regime == "bear":
            # 熊市：低仓位
            position = 0.2 + 0.1 * actionable_count
        elif regime.regime == "transition":
            # 震荡市：中等仓位
            position = 0.3 + 0.1 * actionable_count + 0.1 * avg_confidence
        else:  # bull
            # 牛市：较高仓位
            position = 0.4 + 0.08 * actionable_count + 0.1 * avg_confidence

        # 限制在 risk_budget 内
        position = min(position, regime.risk_budget)

        return clip(position, 0.0, 0.8)

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
