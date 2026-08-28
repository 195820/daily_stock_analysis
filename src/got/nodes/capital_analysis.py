"""
Node 3: 资金面分析节点（实时版）

职责：追踪盘中实时资金流向，为其他节点提供辅助验证信号。

设计理念：
- 3 实时维度：板块资金流 / DDE 大单净流入 / 北向资金分钟级（备用）
- 降级机制：某维度缺失时权重归一化 + 置信度下调
- 盘中 T+0：概念/行业资金流和 DDE 均为盘中实时数据

数据源：
- 板块资金流: akshare stock_fund_flow_concept/industry("即时") — 实时概念/行业净流入
- DDE 大单: DdeFetcher (Playwright → push2.eastmoney.com) — 全市场大单净流入
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

# 3 维度权重（总分母 = 1.0）
# 注意：northbound_realtime 自 2024-08 起官方停披，已永久豁免
# 缺失时不拖低 confidence_multiplier，权重自动归一化到 sector_flow + dde
DIMENSION_WEIGHTS: dict[str, float] = {
    "sector_flow": 0.50,
    "dde": 0.35,
    "northbound_realtime": 0.15,
}

# 永久豁免维度（官方停披/结构性不可用，不参与置信度分母计算）
EXEMPT_DIMENSIONS: set[str] = {"northbound_realtime"}

# 维度中文名称
DIMENSION_LABELS: dict[str, str] = {
    "sector_flow": "板块资金流",
    "dde": "大单净流入(DDE)",
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

# DDE 评分参数
_DDE_NET_INFLOW_SCALE = 1e8  # DDE 净流入额归一化基数（元）


# =============================================================================
# 数据新鲜度验证（Phase 6）
# =============================================================================


def _validate_data_freshness(df: pd.DataFrame, expected_date: str) -> dict:
    """验证 akshare 返回的数据是否为当日（防反爬缓存）。

    检查逻辑：
    1. 如果 df 有 "日期" 列 → 精确匹配
    2. 如果 df 有 "时间" 列 → 取最新时间戳的日期部分
    3. 都没有 → 假设有效（无法验证时不阻塞）

    Returns:
        dict: {"is_fresh": bool, "stale_data": bool, "detail": str}
    """
    result = {"is_fresh": True, "stale_data": False, "detail": "无法验证（无日期列）"}

    if df is None or df.empty:
        result["detail"] = "空数据"
        return result

    # 精确日期匹配
    if "日期" in df.columns:
        dates = df["日期"].astype(str).str.strip()
        latest = dates.iloc[-1][:10]  # 取最后一行的日期
        result["is_fresh"] = latest == expected_date
        result["stale_data"] = not result["is_fresh"]
        result["detail"] = f"日期列最新={latest}, 期望={expected_date}"
        return result

    # 时间戳列
    if "时间" in df.columns:
        times = df["时间"].astype(str).str.strip()
        latest_time = times.iloc[-1]
        latest_date = latest_time[:10]
        result["is_fresh"] = latest_date == expected_date
        result["stale_data"] = not result["is_fresh"]
        result["detail"] = f"时间列最新={latest_time}, 期望日期={expected_date}"
        return result

    return result


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
    ) -> CapitalContext:
        """执行资金面分析。

        流程：并发获取 3 维度数据 → 逐维度评分 → 加权聚合 → LLM reasoning
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
                f"置信度: {result.confidence_multiplier:.1f} "
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
        """并发获取 3 维度数据。"""
        dimension_keys = ["sector_flow", "dde", "northbound_realtime"]
        dimension_fetchers = [
            self._fetch_sector_fund_flow(quality),
            self._fetch_dde(quality),
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

    async def _fetch_dde(self, quality: DataQuality) -> Optional[dict]:
        """获取 DDE 大单净流入数据。复用已有的 DdeFetcher。"""
        def _fetch():
            try:
                from src.services.dde_fetcher import DdeFetcher
                from src.config import get_config
                config = get_config()
                dde = DdeFetcher(config)
                result = dde.fetch_market_dde()
                if not result or result.error or not result.snapshots:
                    return None

                # 将 snapshots dict 转为 list of dicts 以便评分
                snapshot_list = []
                total_net = 0.0
                for code, snap in result.snapshots.items():
                    snapshot_list.append({
                        "code": snap.code,
                        "dde_big_order": snap.dde_big_order,
                        "big_net_amount": snap.big_net_amount,
                    })
                    total_net += snap.big_net_amount

                return {
                    "snapshots": snapshot_list,
                    "total_net_amount": total_net,
                    "stock_count": len(snapshot_list),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            except Exception as e:
                logger.warning(f"DDE 获取失败: {e}")
            return None

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

        # --- 维度 2：DDE 大单净流入（权重 0.35）---
        dde_score = self._score_dde(raw_data.get("dde"))
        if dde_score is not None:
            score, ev_dict = dde_score
            evidence.dde = ev_dict
            available_scores.append(score)
            available_weights.append(DIMENSION_WEIGHTS["dde"])
            quality.available_fields.append("dde")
        else:
            missing_dimensions.append(DIMENSION_LABELS["dde"])
            quality.fetch_failed_fields.append("dde")

        # --- 维度 3：北向资金实时（永久豁免 — 2024-08 起官方停披，不计入缺失）---
        nb_score = self._score_northbound_realtime(raw_data.get("northbound_realtime"))
        if nb_score is not None:
            score, ev_dict = nb_score
            evidence.northbound_realtime = ev_dict
            available_scores.append(score)
            available_weights.append(DIMENSION_WEIGHTS["northbound_realtime"])
            quality.available_fields.append("northbound_realtime")
        # 北向资金 2024-08 起官方停披，缺失时不标记 fetch_failed / missing
        # 避免每天恒定 degraded 拖累置信度

        # === 降级处理 ===
        if not available_scores:
            return CapitalContext(
                status="failed",
                institutional_attitude="neutral",
                institutional_score=0.0,
                confidence_multiplier=0.0,
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

        # 置信度 = 可用维度数 / 有效维度总数（排除永久豁免维度）
        effective_total = len(DIMENSION_WEIGHTS) - len(EXEMPT_DIMENSIONS & set(DIMENSION_WEIGHTS.keys()))
        confidence_multiplier = len(available_scores) / max(1, effective_total)

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
            confidence_multiplier=round(confidence_multiplier, 2),
            evidence=evidence,
            missing_dimensions=missing_dimensions,
            data_quality=quality,
        )

    # =================================================================
    # 单维度评分函数
    # =================================================================

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
    def _score_dde(data: Optional[dict]) -> Optional[tuple[float, dict]]:
        """DDE 大单净流入评分。

        逻辑：全市场大单净流入金额合计 / 归一化基数 → clip(-1, 1)
        数据源：DdeFetcher (Playwright → push2.eastmoney.com)
        v2.1 扩展：从归档回读 DDE 序列，计算累计值和连续正流入天数
        """
        if data is None:
            return None

        total_net = data.get("total_net_amount")
        if total_net is None:
            return None

        total_net = float(total_net)
        stock_count = data.get("stock_count", 0)

        # 归一化：1000 亿（1e11 元）→ score 1.0
        dde_score = clip(total_net / 1e11, -1.0, 1.0)

        # 统计流入/流出股票数
        snapshots = data.get("snapshots", [])
        inflow_count = sum(1 for s in snapshots if s.get("big_net_amount", 0) > 0)
        outflow_count = sum(1 for s in snapshots if s.get("big_net_amount", 0) < 0)

        # === v2.1 DDE 序列归档回查 ===
        cumulative_3d: Optional[float] = None
        cumulative_5d: Optional[float] = None
        consecutive_positive_days = 0

        try:
            from src.got.persistence import load_capital_flow_series
            _, dde_series = load_capital_flow_series(days=5)
        except Exception as e:
            logger.warning(f"[Node 3] DDE 归档回读失败: {e}")
            dde_series = []

        if dde_series:
            # 累计值（最近 3d/5d 之和）
            flows = [flow for _, flow in dde_series]
            if len(flows) >= 3:
                cumulative_3d = round(sum(flows[-3:]), 2)
            if len(flows) >= 5:
                cumulative_5d = round(sum(flows[-5:]), 2)

            # 连续正流入天数（从最近一天往前数）
            for _, flow in reversed(dde_series):
                if flow > 0:
                    consecutive_positive_days += 1
                else:
                    break

        return dde_score, {
            "score": round(dde_score, 4),
            "total_net_inflow": round(total_net / 1e8, 2),  # 亿元
            "inflow_stock_count": inflow_count,
            "outflow_stock_count": outflow_count,
            "cumulative_3d": cumulative_3d,
            "cumulative_5d": cumulative_5d,
            "consecutive_positive_days": consecutive_positive_days,
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
        if ctx.evidence.dde:
            dde = ctx.evidence.dde
            evidence_parts.append(
                f"- 大单净流入(DDE)：全市场净{'流入' if dde['total_net_inflow'] > 0 else '流出'} "
                f"{abs(dde['total_net_inflow'])}亿，"
                f"流入股 {dde['inflow_stock_count']} / 流出股 {dde['outflow_stock_count']}"
            )
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
        dim_note = ""
        if ctx.missing_dimensions:
            dim_note = (
                f"有效维度缺失：{'、'.join(ctx.missing_dimensions)}，"
                f"置信度 {ctx.confidence_multiplier:.0%}。"
                "若置信度≤50%，请在结论中明确提示“结论基于单一资金维度、证据强度有限”，"
                "严禁使用“强烈看多”“坚决做多”等强绝对化措辞，改用“资金维度强流入”等限定表述。"
            )
        else:
            dim_note = "三维资金数据完整（板块资金流/大单/北向），证据充分。"

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
        if ctx.evidence.dde:
            dde = ctx.evidence.dde
            direction = "流入" if dde["total_net_inflow"] > 0 else "流出"
            parts.append(f"大单净{direction} {abs(dde['total_net_inflow'])}亿。")
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
            status="normal",  # 北向豁免后，仅 sector_flow + dde 两维即为正常
            institutional_attitude="neutral",
            institutional_score=0.05,
            confidence_multiplier=1.0,  # 2/2 有效维度（北向已豁免）
            evidence=CapitalEvidence(
                sector_flow={
                    "score": 0.15,
                    "top_concepts": ["AI视频(42亿)", "碳基指数(63亿)"],
                    "top_industries": ["种植业(1.8亿)"],
                    "mainline_bonus": False,
                    "timestamp": "mock",
                },
                dde={
                    "score": -0.10,
                    "total_net_inflow": -15.0,
                    "inflow_stock_count": 2200,
                    "outflow_stock_count": 2800,
                    "timestamp": "mock",
                },
            ),
            missing_dimensions=["北向资金(实时)"],
            reasoning="[Mock] 板块资金小幅流入，大单净流出15亿。整体中性。",
            data_quality=DataQuality(
                available_fields=["sector_flow", "dde"],
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
