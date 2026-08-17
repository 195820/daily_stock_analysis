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

# 仓位日限幅
POSITION_LIMIT_DELTA = 0.2

# 泛化后缀：匹配时需剥离，防止 "概念" 等无意义后缀造成假阳性
GENERIC_SUFFIXES = ("概念",)


# =============================================================================
# 聚合节点
# =============================================================================

class AggregatorNode:
    """Node 6: 分层 GOT 聚合（全局环境 → 板块 mini GOT → 组合决策）"""

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

        # ── Level 1: 板块 mini GOT ──
        sector_intels = self._build_sector_intel(sector, capital, news, sentiment)
        logger.info(
            f"[Node 6] Level 1 — {len(sector_intels)} 个板块情报, "
            f"actionable={sum(1 for s in sector_intels if s.classification == 'actionable')}, "
            f"caution={sum(1 for s in sector_intels if s.classification == 'caution')}, "
            f"exclude={sum(1 for s in sector_intels if s.classification == 'exclude')}"
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
        regime_score += news_impact_score * 0.1

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
        macro_overlay = (
            f"regime_score={regime_score:.2f}, ADX={adx_value:.0f}, "
            f"板块{market_direction}, 机构态度{institutional_score:+.2f}, "
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
    ) -> list[SectorIntel]:
        """对每个候选板块做 4 维 mini GOT，标记主线/埋伏来源"""

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

        # ── Pass 2: 批量分类（TOP-N → actionable）──
        classify_input = [
            (name, composite, capital_check, news_check, detail, sector_role)
            for name, composite, capital_check, news_check, detail, sector_role, _, _ in raw_data
        ]
        classification_map = self._classify_all_sectors(classify_input, top_n=12)

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
        news_keywords: list[tuple[str, str, str]],
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

        # ── 维度 3: 消息面 ──
        news_check, news_detail = self._cross_check_news(name, news_keywords)

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

        # 消息面修正
        if news_check == "catalyzed":
            composite += 8
        elif news_check == "negative":
            composite -= 12

        # 情绪面修正
        if sentiment_check == "high":
            composite += 5
        elif sentiment_check == "low":
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

    def _cross_check_news(
        self,
        name: str,
        keywords: list[tuple[str, str, str]],
    ) -> tuple[str, str]:
        """消息面交叉验证: (check, detail)"""
        if not keywords:
            return "neutral", "无消息催化"

        name_parts = self._split_sector_name(name)
        matched_events = []

        for kw, title, sentiment in keywords:
            for part in name_parts:
                if len(part) >= 2 and (part in kw or kw in part or part in title):
                    matched_events.append((title, sentiment))
                    break

        if not matched_events:
            return "neutral", f"{name}无直接消息催化"

        # 去重
        seen = set()
        unique = []
        for title, sentiment in matched_events:
            if title not in seen:
                seen.add(title)
                unique.append((title, sentiment))

        pos = sum(1 for _, s in unique if s == "positive")
        neg = sum(1 for _, s in unique if s == "negative")

        if pos > neg and pos >= 2:
            return "catalyzed", f"{pos}条正面催化: {unique[0][0][:30]}"
        elif pos > neg:
            return "catalyzed", f"1条正面催化: {unique[0][0][:30]}"
        elif neg > pos:
            return "negative", f"{neg}条负面消息: {unique[0][0][:30]}"
        else:
            return "neutral", f"消息中性: {unique[0][0][:30]}"

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
    # Level 2: 组合决策
    # =========================================================================

    @staticmethod
    def _compute_position_from_regime(
        regime: MarketRegime,
        confirmed: list[SectorIntel],
        base_position: float,
    ) -> float:
        """基于 regime + actionable_sectors 计算仓位"""

        actionable_count = len([s for s in confirmed if s.classification == "actionable"])
        avg_confidence = (
            sum(s.confidence for s in confirmed if s.classification == "actionable") / actionable_count
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
        """基于 regime + sectors 判定统一信号"""

        actionable_count = len([s for s in sectors if s.classification == "actionable"])
        exclude_count = len([s for s in sectors if s.classification == "exclude"])

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
        """计算整体置信度"""

        actionable = [s for s in sectors if s.classification == "actionable"]
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
    def _extract_news_keywords(news: NewsContext) -> list[tuple[str, str, str]]:
        """提取消息面关键词: [(关键词, 事件标题, sentiment), ...]"""
        keywords = []
        for event in news.major_events:
            for sector_name in event.affected_sectors:
                keywords.append((sector_name, event.title, event.sentiment))
            keywords.append((event.title, event.title, event.sentiment))

        for concept in news.concept_suggestions:
            name = concept.get("concept_name", "")
            if name:
                keywords.append((name, name, "positive"))

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
        2. 主线家族 vs 资金流向错位（Node 2 top-1 家族无资金支撑 vs 低排名家族吸金）
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

        return contradictions

    @staticmethod
    def _detect_family_capital_mismatch(
        sector: SectorContext,
        capital: CapitalContext,
    ) -> list[Contradiction]:
        """检测 Node 2 主线家族 vs Node 3 资金流向的错位矛盾

        高阈值模式：仅在以下条件同时满足时触发——
        ① Node 2 top-1 家族的 member_boards 在资金 top-5 中完全无匹配
        ② 资金 top-1 对应的家族在 Node 2 家族排名中不在 top-2

        错位 → high severity；排名差 ≥3 → medium severity。
        """
        families = sorted(sector.theme_families, key=lambda f: f.family_score, reverse=True)
        if len(families) < 2:
            return []

        # 解析资金流 top-5
        flows = AggregatorNode._parse_capital_flows(capital)
        top5_flows = flows[:5]
        if not top5_flows:
            return []

        # 为每个资金流条目找到其对应的家族
        def _find_family_for_flow(flow_name: str) -> Optional[str]:
            """找到资金流条目所属的家族"""
            for family in families:
                for board in family.member_boards:
                    if AggregatorNode._fuzzy_match(board, flow_name):
                        return family.family_name
            return None

        # 计算每个家族的资金流入总额
        family_capital: dict[str, float] = {}
        family_capital_top_flow: dict[str, str] = {}
        for flow_name, amount in top5_flows:
            matched_family = _find_family_for_flow(flow_name)
            if matched_family:
                family_capital[matched_family] = family_capital.get(matched_family, 0) + amount
                if matched_family not in family_capital_top_flow:
                    family_capital_top_flow[matched_family] = f"{flow_name}({amount:.0f}亿)"

        # 家族排名（按 score 降序）
        family_rank = {f.family_name: i for i, f in enumerate(families)}
        # 资金排名（按流入额降序）
        capital_ranked = sorted(family_capital.items(), key=lambda x: x[1], reverse=True)
        capital_rank = {name: i for i, (name, _) in enumerate(capital_ranked)}

        contradictions = []
        top1_family = families[0].family_name

        # 条件 ①: top-1 家族在资金 top-5 中完全无匹配
        top1_has_capital = top1_family in family_capital
        # 条件 ②: 资金 top-1 家族在 Node 2 排名中不在 top-2
        if capital_ranked:
            capital_top1_family = capital_ranked[0][0]
            capital_top1_not_in_sector_top2 = family_rank.get(capital_top1_family, 99) >= 2
        else:
            capital_top1_family = None
            capital_top1_not_in_sector_top2 = False

        # 错位矛盾（high severity）
        if not top1_has_capital and capital_top1_not_in_sector_top2 and capital_top1_family:
            top1_members = ", ".join(families[0].member_boards[:3])
            capital_top1_amount = capital_ranked[0][1]
            contradictions.append(Contradiction(
                pair="sector_capital_mainline_mismatch",
                timeframe="cross_dimension",
                description=(
                    f"主线错位：Node 2 核心主线「{top1_family}」（{top1_members}）"
                    f"在资金 top-5 中无匹配，而「{capital_top1_family}」"
                    f"独占资金 top-1（{capital_top1_amount:.0f}亿）但 Node 2 排名仅第 {family_rank.get(capital_top1_family, '?') + 1}"
                ),
                severity="high",
                conflict_type="factual",
                resolution=f"建议降低「{top1_family}」置信度，关注「{capital_top1_family}」是否被技术面低估",
            ))

        # 方向矛盾（medium severity）：家族 score 排名与资金排名差 ≥3
        elif capital_top1_family and capital_top1_not_in_sector_top2:
            score_rank = family_rank.get(capital_top1_family, 99)
            cap_rank = capital_rank.get(capital_top1_family, 99)
            rank_gap = abs(score_rank - cap_rank)
            if rank_gap >= 3:
                contradictions.append(Contradiction(
                    pair="sector_capital_rank_divergence",
                    timeframe="cross_dimension",
                    description=(
                        f"「{capital_top1_family}」技术面排名第 {score_rank + 1} vs 资金面排名第 {cap_rank + 1}，"
                        f"差距 {rank_gap} 位"
                    ),
                    severity="medium",
                    conflict_type="opinion",
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
