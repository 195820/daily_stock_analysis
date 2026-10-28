"""
Node 3: 资金面分析节点（实时版）

职责：追踪盘中实时资金流向，为其他节点提供辅助验证信号。

设计理念：
- 实时维度：板块资金流（主）/ 北向资金分钟级（备用，已停披豁免）
  （DDE 大单净流入维度随东财 push2 整域封锁长期失效，缺陷③A 已摘除）
- 降级机制：某维度缺失时权重归一化 + 置信度下调
- 盘中 T+0：概念/行业资金流为盘中实时数据

数据源：
- 板块资金流: akshare stock_fund_flow_concept/industry("即时") — 实时概念/行业净流入
- 北向资金: akshare stock_hsgt_fund_min_em — 分钟级实时（2024-08 后全 0，自动降级）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

import numpy as np
import pandas as pd

from data_provider.base import DataFetcherManager
from src.got.indicators import clip
from src.got.schemas import (
    CapitalContext,
    CapitalEvidence,
    DataQuality,
    MarketContext,
    SectorContext,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 常量配置
# =============================================================================

# 维度权重（缺陷③A：DDE 随东财 push2 整域封锁长期失效，已摘除死维度）
# northbound_realtime 自 2024-08 起官方停披，永久豁免、不计入缺失维度。
# 实际参与评分的维度当前仅 sector_flow 一维（单维资金口径）。
DIMENSION_WEIGHTS: dict[str, float] = {
    "sector_flow": 0.85,
    "northbound_realtime": 0.15,
}

# 维度中文名称
DIMENSION_LABELS: dict[str, str] = {
    "sector_flow": "板块资金流",
    "northbound_realtime": "北向资金(实时)",
}

# 态度判定阈值
_BULLISH_THRESHOLD = 0.3
_BEARISH_THRESHOLD = -0.3

# 板块资金流评分参数
_CONCEPT_TOP_N = 20          # 取净流入 Top N 概念板块
_INDUSTRY_TOP_N = 10         # 取净流入 Top N 行业板块
_CONCEPT_WEIGHT = 0.65       # 概念板块在 sector_flow 中的权重
_INDUSTRY_WEIGHT = 0.35      # 行业板块权重


# =============================================================================
# 数据新鲜度验证（Phase 6）
# =============================================================================


# C2（批次 C）：新鲜度检查收为全局唯一权威实现（src/got/data_freshness.py），本节点
# 直接复用别名——禁第二套口径。原模块级函数体已搬至该工具（并扩识别英文 date 列）。
from src.got.data_freshness import check_data_freshness as _validate_data_freshness


class CapitalAnalysisNode:
    """资金面分析节点（GOT Node 3，实时版）"""

    def __init__(self, use_mock: bool = True):
        self.use_mock = use_mock
        self._fetcher_manager: Optional[DataFetcherManager] = None

    @property
    def fetcher(self) -> DataFetcherManager:
        """懒加载 DataFetcherManager（首次使用时创建）。"""
        if self._fetcher_manager is None:
            self._fetcher_manager = DataFetcherManager()
        return self._fetcher_manager

    async def run(
        self,
        market_context: MarketContext,
        sector_context: SectorContext,
        holdings=None,
    ) -> CapitalContext:
        """执行资金面分析。

        流程：并发获取 3 维度数据 → 逐维度评分 → 加权聚合 → LLM reasoning

        holdings: H1 持仓上下文（None=无持仓诉求）。仅用于产出 `holdings_flow_index`
        （持仓板块资金流三级别名匹配留痕），**不改** institutional_score 计算。
        """
        if self.use_mock:
            logger.info("[Node 3] 使用 mock 数据")
            return self._mock_output()

        logger.info("[Node 3] 开始实时资金面分析...")

        quality = DataQuality()

        try:
            # Step 1: 并发获取 3 维度数据
            raw_data = await self._fetch_all_dimensions(quality)

            # Step 2: 逐维度评分 + 加权聚合
            result = self._calculate_institutional_score(raw_data, quality, sector_context)

            # Step 2.5（H1 持仓供数）：持仓板块资金流索引留痕。
            # 持仓股无 DDE（非问财候选），板块资金流是唯一可用资金维；此处把「哪些持仓板块
            # 拿到了资金数据、用哪个别名键命中」显式记下，Phase 2 据此把缺失维降维而非静默吞 0。
            try:
                result.evidence.holdings_flow_index = self._build_holdings_flow_index(
                    result.evidence.sector_flow, holdings,
                )
            except Exception as exc:  # noqa: BLE001 — 供数留痕失败不阻断 Node 3
                logger.warning("[Node 3] holdings_flow_index 构建失败（已忽略）: %s", exc)

            # Step 3: 判定失败
            if result.status == "failed":
                logger.warning(f"[Node 3] 数据不足，分析失败: {result.missing_dimensions}")
                return result

            # Step 4: LLM 生成 reasoning
            result.reasoning = await self._generate_reasoning(
                result, market_context, sector_context,
            )

            logger.info(
                f"[Node 3] 完成 — "
                f"态度: {result.institutional_attitude} "
                f"评分: {result.institutional_score:.3f} "
                f"缺失: {result.missing_dimensions}"
            )
            return result

        except Exception as e:
            logger.error(f"[Node 3] 资金面分析失败: {e}", exc_info=True)
            raise

    # =================================================================
    # 数据获取
    # =================================================================

    async def _fetch_all_dimensions(
        self, quality: DataQuality,
    ) -> dict[str, Any]:
        """并发获取可用维度数据（缺陷③A：DDE 已摘除）。"""
        dimension_keys = ["sector_flow", "northbound_realtime"]
        dimension_fetchers = [
            self._fetch_sector_fund_flow(quality),
            self._fetch_northbound_realtime(quality),
        ]

        raw_results = await asyncio.gather(*dimension_fetchers, return_exceptions=True)

        results = {}
        for key, result in zip(dimension_keys, raw_results):
            if isinstance(result, Exception):
                logger.warning(f"[Node 3] {DIMENSION_LABELS[key]} 获取失败: {result}")
                results[key] = None
            else:
                results[key] = result

        return results

    async def _fetch_sector_fund_flow(self, quality: DataQuality) -> Optional[dict]:
        """获取概念 + 行业板块实时资金流。通过 DataFetcherManager 代理。"""
        def _fetch():
            concept_df = self.fetcher.get_sector_fund_flow_concept()
            industry_df = self.fetcher.get_sector_fund_flow_industry()

            if concept_df is None and industry_df is None:
                return None

            return {
                "concept": concept_df,
                "industry": industry_df,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

        return await asyncio.to_thread(_fetch)

    async def _fetch_northbound_realtime(self, quality: DataQuality) -> Optional[pd.DataFrame]:
        """获取北向资金分钟级实时数据。

        注意：2024-08 后全 0（停披），自动降级。保留为备用维度。
        """
        def _fetch():
            try:
                df = self.fetcher.get_sector_fund_flow_concept()  # placeholder
                # 实际调用北向分钟级 API
                import akshare as ak
                df = ak.stock_hsgt_fund_min_em(symbol="北向资金")
                if df is None or df.empty:
                    return None

                # 检查是否有非零数据（停披后全 0）
                last_col = df.columns[-1]
                non_zero = df[last_col].dropna()
                non_zero = non_zero[non_zero != 0]
                if non_zero.empty:
                    logger.info("北向资金分钟级数据全 0（2024-08 后停披），标记缺失")
                    return None

                return df
            except Exception as e:
                logger.warning(f"北向资金分钟级获取失败: {e}")
                return None

        return await asyncio.to_thread(_fetch)

    # =================================================================
    # 评分计算
    # =================================================================

    def _calculate_institutional_score(
        self,
        raw_data: dict[str, Any],
        quality: DataQuality,
        sector_context: Optional[SectorContext] = None,
    ) -> CapitalContext:
        """综合机构态度评分（-1 ~ 1），支持降级模式。"""
        evidence = CapitalEvidence()
        available_scores: list[float] = []
        available_weights: list[float] = []
        missing_dimensions: list[str] = []

        # --- 维度 1：板块资金流（权重 0.50）---
        sf_score = self._score_sector_flow(raw_data.get("sector_flow"), sector_context)
        if sf_score is not None:
            score, ev_dict = sf_score
            evidence.sector_flow = ev_dict
            available_scores.append(score)
            available_weights.append(DIMENSION_WEIGHTS["sector_flow"])
            quality.available_fields.append("sector_flow")
        else:
            missing_dimensions.append(DIMENSION_LABELS["sector_flow"])
            quality.fetch_failed_fields.append("sector_flow")

        # --- 维度 2：北向资金实时（永久豁免 — 2024-08 起官方停披，不计入缺失）---
        # 注：原 DDE 维度（大单净流入）随东财 push2 整域封锁长期失效，缺陷③A 已摘除。
        nb_score = self._score_northbound_realtime(raw_data.get("northbound_realtime"))
        if nb_score is not None:
            score, ev_dict = nb_score
            evidence.northbound_realtime = ev_dict
            available_scores.append(score)
            available_weights.append(DIMENSION_WEIGHTS["northbound_realtime"])
            quality.available_fields.append("northbound_realtime")
        # 北向资金 2024-08 起官方停披，缺失时不标记 fetch_failed / missing
        # （避免每天恒定 degraded）

        # === 降级处理 ===
        if not available_scores:
            return CapitalContext(
                status="failed",
                institutional_attitude="neutral",
                institutional_score=0.0,
                evidence=evidence,
                missing_dimensions=missing_dimensions,
                reasoning="所有资金流维度数据均不可用",
                data_quality=quality,
            )

        # 权重归一化
        total_weight = sum(available_weights)
        institutional_score = sum(
            s * w / total_weight for s, w in zip(available_scores, available_weights)
        )
        institutional_score = clip(institutional_score, -1.0, 1.0)

        # 缺陷③A 诚实标注：机构态度仅由单一可用维度支撑时置位
        # （摘除 DDE + 北向豁免后，sector_flow 可用即单维口径）
        # 原 `confidence_multiplier = 可用维度数 / 有效维度总数` 已于 2026-10-14 删除
        # （backlog §1.J 二）：分母已被推到 1，取值恒为 1.0/0.0，不携带信息量；
        # 降级程度统一由 status + single_dimension 承载。
        single_dimension = len(available_scores) == 1

        # 态度判定
        if institutional_score > _BULLISH_THRESHOLD:
            attitude = "bullish"
        elif institutional_score < _BEARISH_THRESHOLD:
            attitude = "bearish"
        else:
            attitude = "neutral"

        status = "degraded" if missing_dimensions else "normal"

        return CapitalContext(
            status=status,
            institutional_attitude=attitude,
            institutional_score=round(institutional_score, 4),
            single_dimension=single_dimension,
            evidence=evidence,
            missing_dimensions=missing_dimensions,
            data_quality=quality,
        )

    # =================================================================
    # 单维度评分函数
    # =================================================================

    @staticmethod
    def _build_holdings_flow_index(
        sector_flow_evidence: Optional[dict],
        holdings,
    ) -> Optional[dict]:
        """持仓板块资金流索引（H1，2026-10-27）。

        对每个 `holdings.sector_wishes` 板块做「精确 → 别名变体 → 归一化」三级匹配，
        记下命中的真实键与 1d/3d/5d 净流入。用途：
        1. Phase 2 把匹配到的键喂回 `_score_capital`（消除别名口径丢分）；
        2. 未命中的板块显式标 `capital_data_missing`（缺失≠零分，仓库纪律）。

        无持仓诉求 → None（与「有持仓但全未命中」区分开，后者是有效读数）。
        """
        wishes: list[str] = []
        for raw_wish in (getattr(holdings, "sector_wishes", None) or []):
            wish = (raw_wish or "").strip()
            if wish and wish not in wishes:
                wishes.append(wish)
        if not wishes:
            return None

        evidence = sector_flow_evidence or {}
        flow_map: dict[str, float] = evidence.get("sector_flow_all") or {}
        flow_3d: dict[str, float] = evidence.get("sector_flow_all_3d") or {}
        flow_5d: dict[str, float] = evidence.get("sector_flow_all_5d") or {}

        index: dict[str, dict] = {}
        hit_count = 0
        misses: list[str] = []
        for wish in wishes:
            matched_key = _resolve_sector_flow_key(wish, flow_map)
            row = {
                "matched": bool(matched_key),
                "matched_key": matched_key or "",
                "flow": flow_map.get(matched_key) if matched_key else None,
                "flow_3d": flow_3d.get(matched_key) if matched_key else None,
                "flow_5d": flow_5d.get(matched_key) if matched_key else None,
            }
            index[wish] = row
            if matched_key:
                hit_count += 1
            else:
                misses.append(wish)

        note = ""
        if not flow_map:
            note = "sector_flow_all 不可用（Node 3 降级），全部持仓板块资金维缺失"
        elif misses:
            note = f"{len(misses)} 个持仓板块在 sector_flow_all 三级别名匹配后仍未命中"

        return {
            "wishes": wishes,
            "index": index,
            "hit_count": hit_count,
            "miss": misses,
            "note": note,
        }

    @staticmethod
    def _score_sector_flow(
        data: Optional[dict],
        sector_context: Optional[SectorContext] = None,
    ) -> Optional[tuple[float, dict]]:
        """板块资金流评分（概念 + 行业）。

        逻辑：
        1. 概念板块按净流入排名，Top N 均值为概念分数
        2. 行业板块同理
        3. 加权合并
        4. 主线板块加分（强度×持续性，上限 0.15）
        5. 从归档回读多日序列，计算板块级 3d/5d 日均映射
        """
        if data is None:
            return None

        concept_df = data.get("concept")
        industry_df = data.get("industry")

        # 数据新鲜度验证（防反爬缓存返回旧数据）
        from datetime import date as _date_cls
        _today_str = _date_cls.today().isoformat()
        for _label, _df in [("concept", concept_df), ("industry", industry_df)]:
            if _df is not None and not _df.empty:
                _fresh = _validate_data_freshness(_df, _today_str)
                if _fresh["stale_data"]:
                    logger.warning(
                        f"[Node 3] {_label} 数据陈旧: {_fresh['detail']}"
                    )

        scores = []
        weights = []
        top_concepts = []
        top_industries = []

        # 概念板块评分
        if concept_df is not None and not concept_df.empty:
            net_col = _find_column(concept_df, ["净额", "净流"])
            name_col = _find_column(concept_df, ["行业", "板块名称", "名称"])
            if net_col and name_col:
                concept_df = concept_df.copy()
                concept_df[net_col] = pd.to_numeric(concept_df[net_col], errors="coerce")
                concept_df = concept_df.dropna(subset=[net_col])

                # Top N 净流入
                top_n = concept_df.nlargest(_CONCEPT_TOP_N, net_col)
                avg_net_flow = float(top_n[net_col].mean())
                # 归一化：200 亿 → score 1.0（考虑深股通/融资融券等大资金板块）
                concept_score = clip(avg_net_flow / 200.0, -1.0, 1.0)
                scores.append(concept_score)
                weights.append(_CONCEPT_WEIGHT)

                top_concepts = [
                    f"{row[name_col]}({row[net_col]:.1f}亿)"
                    for _, row in top_n.head(5).iterrows()
                ]

        # 行业板块评分
        if industry_df is not None and not industry_df.empty:
            net_col = _find_column(industry_df, ["净额", "净流"])
            name_col = _find_column(industry_df, ["行业", "板块名称", "名称"])
            if net_col and name_col:
                industry_df = industry_df.copy()
                industry_df[net_col] = pd.to_numeric(industry_df[net_col], errors="coerce")
                industry_df = industry_df.dropna(subset=[net_col])

                top_n = industry_df.nlargest(_INDUSTRY_TOP_N, net_col)
                avg_net_flow = float(top_n[net_col].mean())
                # 归一化：100 亿 → score 1.0
                industry_score = clip(avg_net_flow / 100.0, -1.0, 1.0)
                scores.append(industry_score)
                weights.append(_INDUSTRY_WEIGHT)

                top_industries = [
                    f"{row[name_col]}({row[net_col]:.1f}亿)"
                    for _, row in top_n.head(3).iterrows()
                ]

        if not scores:
            return None

        # 加权合并
        total_w = sum(weights)
        final_score = sum(s * w / total_w for s, w in zip(scores, weights))

        # 构建全量板块名→净流入(亿)映射（~300 概念 + ~41 行业），供下游评分使用
        sector_flow_all: dict[str, float] = {}
        for _df in [concept_df, industry_df]:
            if _df is None or _df.empty:
                continue
            _net_col = _find_column(_df, ["净额", "净流"])
            _name_col = _find_column(_df, ["行业", "板块名称", "名称"])
            if not _net_col or not _name_col:
                continue
            for _, _row in _df.iterrows():
                _name = str(_row[_name_col]).strip()
                _amount = pd.to_numeric(_row[_net_col], errors="coerce")
                if pd.isna(_amount):
                    continue
                # 同一板块在概念/行业中重复时取较大值
                prev = sector_flow_all.get(_name, float("-inf"))
                sector_flow_all[_name] = round(float(_amount), 2) if _amount > prev else prev

        # === v2.1 多窗口扩展：从归档回读逐日序列 ===
        # Rollback 开关：DISABLE_NODE3_MULTIDAY=true 回退到单日模式
        import os as _os
        _multiday_disabled = _os.environ.get("DISABLE_NODE3_MULTIDAY", "").lower() == "true"

        sector_flow_all_3d: dict[str, float] = {}
        sector_flow_all_5d: dict[str, float] = {}
        mainline_consistency_days_map: dict[str, int] = {}
        acceleration: Optional[float] = None

        if _multiday_disabled:
            logger.info("[Node 3] DISABLE_NODE3_MULTIDAY=true，回退单日模式")
        else:
            try:
                from src.got.persistence import load_capital_flow_series
                archive_sector_series, _ = load_capital_flow_series(days=5)
            except Exception as e:
                logger.warning(f"[Node 3] 归档回读失败，多窗口降级: {e}")
                archive_sector_series = {}

            if archive_sector_series:
                # 板块级 3d/5d 日均映射
                for sector_name, series in archive_sector_series.items():
                    flows = [flow for _, flow in series]
                    if len(flows) >= 3:
                        sector_flow_all_3d[sector_name] = round(
                            sum(flows[-3:]) / min(len(flows), 3), 2
                        )
                    if len(flows) >= 5:
                        sector_flow_all_5d[sector_name] = round(
                            sum(flows[-5:]) / min(len(flows), 5), 2
                        )

                # 主线板块连续性（连续正流入天数，按板块 dict）
                if sector_context:
                    mainline_names = {
                        m.name for g in sector_context.active_mainline for m in g.members
                    }
                    for ml_name in mainline_names:
                        series = archive_sector_series.get(ml_name, [])
                        if not series:
                            continue
                        # 从最近一天往前数连续正流入天数
                        consecutive = 0
                        for _, flow in reversed(series):
                            if flow > 0:
                                consecutive += 1
                            else:
                                break
                        mainline_consistency_days_map[ml_name] = consecutive

            # 市场级加速度：口径对齐（仅计算同时有 1d 和 5d 数据的板块）
            common_boards = set(sector_flow_all_5d.keys()) & set(sector_flow_all.keys())
            if common_boards:
                today_common = sum(sector_flow_all[b] for b in common_boards)
                avg_5d_common = sum(sector_flow_all_5d[b] for b in common_boards)
                if today_common > 0 and avg_5d_common > 0:
                    acceleration = round(avg_5d_common / today_common - 1, 3)

        # === 主线板块加分：强度×持续性（上限 0.15）===
        mainline_bonus = 0.0
        if sector_context and sector_flow_all:
            # 强度：主线板块在当日的百分位排名（0~1）
            sorted_flows = sorted(sector_flow_all.values())
            n_sectors = len(sorted_flows)
            mainline_names = {
                m.name for g in sector_context.active_mainline for m in g.members
            }
            mainline_pct_ranks = []
            for ml_name in mainline_names:
                flow = sector_flow_all.get(ml_name)
                if flow is not None and n_sectors > 0:
                    # 计算百分位排名
                    rank = sum(1 for f in sorted_flows if f <= flow) / n_sectors
                    mainline_pct_ranks.append(rank)

            if mainline_pct_ranks and mainline_consistency_days_map:
                avg_pct = sum(mainline_pct_ranks) / len(mainline_pct_ranks)
                max_consistency = max(mainline_consistency_days_map.values())
                # 强度 × 持续性，上限 0.15
                mainline_bonus = clip(avg_pct * max_consistency * 0.02, 0, 0.15)

        final_score = clip(final_score + mainline_bonus, -1.0, 1.0)

        return final_score, {
            "score": round(final_score, 4),
            "top_concepts": top_concepts,
            "top_industries": top_industries,
            "sector_flow_all": sector_flow_all,
            "sector_flow_all_3d": sector_flow_all_3d,
            "sector_flow_all_5d": sector_flow_all_5d,
            "mainline_consistency_days": mainline_consistency_days_map,
            "acceleration": acceleration,
            "mainline_bonus": mainline_bonus > 0,
            "timestamp": data.get("timestamp"),
        }

    @staticmethod
    def _score_northbound_realtime(
        df: Optional[pd.DataFrame],
    ) -> Optional[tuple[float, dict]]:
        """北向资金分钟级实时评分（备用，通常降级）。"""
        if df is None or df.empty:
            return None

        last_col = df.columns[-1]
        valid = df[last_col].dropna()
        valid = valid[valid != 0]

        if valid.empty:
            return None

        # 累计净流入（万元）
        cumulative = float(valid.sum())
        # 最后 30 分钟趋势
        recent_30m = valid.tail(30)
        recent_trend = float(recent_30m.sum()) if len(recent_30m) > 0 else 0

        # 归一化：50 亿（500000 万元）→ score 1.0
        nb_score = clip(cumulative / 500000, -1.0, 1.0)
        trend_bonus = clip(recent_trend / 100000, -0.2, 0.2)
        final_score = clip(nb_score + trend_bonus, -1.0, 1.0)

        return final_score, {
            "score": round(final_score, 4),
            "cumulative_inflow": round(cumulative / 10000, 2),  # 万元 → 亿元
            "recent_30m_trend": "流入" if recent_trend > 0 else "流出",
        }

    # =================================================================
    # LLM Reasoning
    # =================================================================

    async def _generate_reasoning(
        self,
        ctx: CapitalContext,
        market_context: MarketContext,
        sector_context: SectorContext,
    ) -> Optional[str]:
        """调用 LLM 生成资金面分析 reasoning 文本。"""
        try:
            from src.agent.llm_adapter import LLMToolAdapter
        except ImportError:
            logger.warning("LLMToolAdapter 不可用，跳过 reasoning 生成")
            return self._fallback_reasoning(ctx)

        # 构建证据摘要
        evidence_parts = []
        if ctx.evidence.sector_flow:
            sf = ctx.evidence.sector_flow
            concepts = "、".join(sf.get("top_concepts", [])[:5]) or "无"
            industries = "、".join(sf.get("top_industries", [])[:3]) or "无"
            evidence_parts.append(
                f"- 板块资金流（实时）：净流入概念 Top5: {concepts}；"
                f"行业 Top3: {industries}"
            )
            if sf.get("mainline_bonus"):
                evidence_parts.append("  → 主线板块资金确认（额外加分）")
        if ctx.evidence.northbound_realtime:
            nb = ctx.evidence.northbound_realtime
            evidence_parts.append(
                f"- 北向资金(实时)：累计 {nb['cumulative_inflow']}亿，"
                f"近30分钟趋势：{nb['recent_30m_trend']}"
            )

        evidence_text = "\n".join(evidence_parts) if evidence_parts else "无可用数据"

        trend = getattr(market_context.tech_growth, "trend", "未知")
        mainline_count = len(sector_context.active_mainline)
        mainline_names = "、".join(
            g.group_name for g in sector_context.active_mainline[:3]
        ) or "无"

        # 时序感知：交易时段 vs 收盘后
        now_hour = datetime.now().hour
        is_trading_hours = 9 <= now_hour < 15
        if is_trading_hours:
            time_context = "盘中实时（T+0）"
            action_hint = "盘中资金整体态度"
            timing_note = ""
        else:
            time_context = "收盘后（当日最终数据）"
            action_hint = "当日资金整体态度"
            timing_note = "请基于当日全天数据分析，不要使用午后等盘中时态。"

        # 有效维度说明（盘中单维模式 / 数据降级时注入，限制 LLM 过度自信）
        # 2026-10-14：原「置信度 {confidence_multiplier:.0%}。若置信度≤50%…」随字段删除移除——
        # 走到此处时该值恒为 100%（sector_flow 不可用已在 status=failed 分支提前返回），
        # 「若置信度≤50%」结构性不可达，留着等于给 LLM 一条永不触发的假约束。
        dim_note = ""
        if ctx.missing_dimensions:
            dim_note = (
                f"有效维度缺失：{'、'.join(ctx.missing_dimensions)}，"
                "证据维度有限，请在结论中明确提示“结论基于单一资金维度、证据强度有限”，"
                "严禁使用“强烈看多”“坚决做多”等强绝对化措辞，改用“资金维度强流入”等限定表述。"
            )
        else:
            dim_note = "资金面基于板块资金流单维口径（DDE 数据源已随东财封锁失效摘除、北向官方停披豁免），证据维度有限，请注意结论强度。"

        prompt = (
            f"你是一个专业的资金流分析师。根据以下{time_context}资金数据，撰写简洁分析报告。\n\n"
            f"机构态度评分：{ctx.institutional_score}（{ctx.institutional_attitude}）\n\n"
            f"有效维度：{dim_note}\n\n"
            f"支撑证据：\n{evidence_text}\n\n"
            f"大盘背景：科技成长趋势={trend}\n"
            f"板块背景：活跃主线={mainline_names}（{mainline_count}个）\n\n"
            f"注意：以上数据为{time_context}数据。{timing_note}\n\n"
            "请输出：\n"
            f"1. 一段150字以内的分析文本，解释{action_hint}\n"
            "2. 重点说明资金是否流向主线板块\n"
            "3. 提示风险信号（如有）\n\n"
            "输出格式：纯文本（不要JSON）"
        )

        try:
            adapter = LLMToolAdapter()
            messages = [{"role": "user", "content": prompt}]
            response = await asyncio.to_thread(
                adapter.call_text, messages, temperature=0.3, max_tokens=300,
            )
            if response and response.content:
                return response.content.strip()
        except Exception as e:
            logger.warning(f"LLM reasoning 生成失败: {e}")

        return self._fallback_reasoning(ctx)

    @staticmethod
    def _fallback_reasoning(ctx: CapitalContext) -> str:
        """降级 reasoning（LLM 不可用时使用）。"""
        parts = [f"机构态度评分 {ctx.institutional_score:.3f}（{ctx.institutional_attitude}）。"]

        if ctx.evidence.sector_flow:
            sf = ctx.evidence.sector_flow
            concepts = "、".join(sf.get("top_concepts", [])[:3]) or "无"
            parts.append(f"板块资金流净流入：{concepts}。")
        if ctx.evidence.northbound_realtime:
            nb = ctx.evidence.northbound_realtime
            parts.append(f"北向资金累计 {nb['cumulative_inflow']}亿。")

        if ctx.missing_dimensions:
            parts.append(f"缺失维度: {'、'.join(ctx.missing_dimensions)}。")

        return "".join(parts)

    # =================================================================
    # Mock 输出
    # =================================================================

    def _mock_output(self) -> CapitalContext:
        """Mock 输出"""
        return CapitalContext(
            status="normal",  # 北向豁免后，仅 sector_flow 单维即为正常
            institutional_attitude="neutral",
            institutional_score=0.05,
            single_dimension=True,  # 缺陷③A：仅剩 sector_flow 单维口径
            evidence=CapitalEvidence(
                sector_flow={
                    "score": 0.15,
                    "top_concepts": ["AI视频(42亿)", "碳基指数(63亿)"],
                    "top_industries": ["种植业(1.8亿)"],
                    "mainline_bonus": False,
                    "timestamp": "mock",
                },
            ),
            missing_dimensions=["北向资金(实时)"],
            reasoning="[Mock] 板块资金小幅流入（单维口径）。整体中性。",
            data_quality=DataQuality(
                available_fields=["sector_flow"],
                missing_fields=["northbound_realtime"],
                fetch_failed_fields=["northbound_realtime"],
            ),
        )


# =============================================================================
# 工具函数
# =============================================================================

def _find_column(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    """在 DataFrame 列名中模糊匹配。"""
    for col in df.columns:
        col_text = str(col)
        for candidate in candidates:
            if candidate in col_text:
                return col
    return None


def _resolve_sector_flow_key(name: str, flow_map: dict[str, float]) -> Optional[str]:
    """板块名 → sector_flow_all 真实键：精确 → 别名变体 → 归一化（与 screener 同口径）。

    screener `_score_capital` 用同样三级顺序匹配；本函数只返回「命中的键」，
    供 Node 3 留痕 / Phase 2 回喂，避免两处各写一套顺序。
    """
    if not name or not flow_map:
        return None
    from src.got.board_aliases import normalize_sector_name, sector_name_variants

    if name in flow_map:
        return name
    for variant in sector_name_variants(name):
        if variant in flow_map:
            return variant
    canon = normalize_sector_name(name)
    for key in flow_map:
        if normalize_sector_name(key) == canon:
            return key
    return None
