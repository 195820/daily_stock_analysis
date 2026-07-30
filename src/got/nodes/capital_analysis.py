"""
Node 3: 资金面分析节点

职责：追踪机构资金行为，为其他节点提供辅助验证信号。

设计理念：
- 5 维度：北向资金 / 龙虎榜 / ETF申赎 / 融资融券 / 大宗交易
- 降级机制：某维度缺失时权重归一化 + 置信度下调（P2-4）
- 机构视角：所有维度都指向机构行为

数据源：akshare
- 北向资金: stock_hsgt_hist_em（2024-08 后数据不可用，自动降级）
- 龙虎榜: stock_lhb_jgmmtj_em（机构席位买卖统计）
- 融资融券: macro_china_market_margin_sh（市场级汇总）
- 大宗交易: stock_dzjy_mrtj（逐笔交易明细）
- ETF 申赎: fund_etf_scale_sse/szse（仅快照，无历史，自动降级）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

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

# 5 维度权重（总分母 = 1.0）
DIMENSION_WEIGHTS: dict[str, float] = {
    "northbound": 0.25,
    "dragon_tiger": 0.25,
    "etf_flow": 0.25,
    "margin_trading": 0.15,
    "block_trade": 0.10,
}

# 维度中文名称（用于日志和 missing_dimensions 输出）
DIMENSION_LABELS: dict[str, str] = {
    "northbound": "北向资金",
    "dragon_tiger": "龙虎榜",
    "etf_flow": "ETF 申赎",
    "margin_trading": "融资融券",
    "block_trade": "大宗交易",
}

# 数据回溯天数
_LOOKBACK_DAYS = 5

# 龙虎榜默认查询区间（覆盖回溯天数 + 周末）
_LHB_DATE_RANGE_DAYS = 10

# 并发请求限制
_CONCURRENT_FETCHES = 5

# 态度判定阈值
_BULLISH_THRESHOLD = 0.3
_BEARISH_THRESHOLD = -0.3

# 大宗交易信号阈值
_BLOCK_PREMIUM_SIGNAL = 0.02       # 溢价 > 2% → 抢筹
_BLOCK_DISCOUNT_SIGNAL = -0.05     # 折价 > 5% → 甩货


class CapitalAnalysisNode:
    """资金面分析节点（GOT Node 3）"""

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

        流程：并发获取 5 维度数据 → 逐维度评分 → 加权聚合 → LLM reasoning
        """
        if self.use_mock:
            logger.info("[Node 3] 使用 mock 数据")
            return self._mock_output()

        logger.info("[Node 3] 开始真实资金面分析...")

        quality = DataQuality()

        try:
            # Step 1: 并发获取 5 维度数据
            raw_data = await self._fetch_all_dimensions(quality)

            # Step 2: 逐维度评分 + 加权聚合
            result = self._calculate_institutional_score(raw_data, quality)

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
        """并发获取 5 维度数据。

        北向资金和 ETF 申赎已知数据源受限，仍然尝试获取以支持未来恢复。
        """
        tasks = {
            "northbound": self._fetch_northbound(quality),
            "dragon_tiger": self._fetch_dragon_tiger(quality),
            "margin_trading": self._fetch_margin_trading(quality),
            "block_trade": self._fetch_block_trade(quality),
            # ETF 申赎暂无可用 API（仅快照无历史），跳过获取
        }

        results = {}
        for key, task in tasks.items():
            try:
                results[key] = await task
            except Exception as e:
                logger.warning(f"[Node 3] {DIMENSION_LABELS[key]} 获取失败: {e}")
                results[key] = None

        return results

    async def _fetch_northbound(self, quality: DataQuality) -> Optional[pd.DataFrame]:
        """获取北向资金历史数据（沪股通 + 深股通）。

        通过 DataFetcherManager 代理（支持多源降级）。
        注意：2024-08 后 `当日成交净买额` 列为 NaN，自动标记为缺失。
        """
        def _fetch():
            frames = []
            for symbol in ("沪股通", "深股通"):
                try:
                    df = self.fetcher.get_northbound_flow(symbol)
                    if df is not None and not df.empty:
                        frames.append(df)
                except Exception as e:
                    logger.warning(f"北向资金 {symbol} 获取失败: {e}")

            if not frames:
                return None

            combined = pd.concat(frames, ignore_index=True)
            combined["日期"] = pd.to_datetime(combined["日期"])
            combined = combined.sort_values("日期")

            # 检查数据有效性（2024-08 后为 NaN）
            recent = combined.tail(_LOOKBACK_DAYS)
            if "当日成交净买额" in recent.columns and recent["当日成交净买额"].isna().all():
                logger.info("北向资金数据近 5 日全为 NaN（2024-08 后已停披），标记缺失")
                return None

            return recent

        return await asyncio.to_thread(_fetch)

    async def _fetch_dragon_tiger(self, quality: DataQuality) -> Optional[pd.DataFrame]:
        """获取龙虎榜机构席位买卖数据。通过 DataFetcherManager 代理。"""
        def _fetch():
            today = datetime.now()
            end_date = today.strftime("%Y%m%d")
            start_date = (today - timedelta(days=_LHB_DATE_RANGE_DAYS)).strftime("%Y%m%d")

            df = self.fetcher.get_dragon_tiger(start_date=start_date, end_date=end_date)
            if df is None or df.empty:
                return None

            required_cols = {"机构买入净额", "代码", "上榜日期"}
            if not required_cols.issubset(set(df.columns)):
                logger.warning(f"龙虎榜数据缺少关键列: {required_cols - set(df.columns)}")
                return None

            return df

        return await asyncio.to_thread(_fetch)

    async def _fetch_margin_trading(self, quality: DataQuality) -> Optional[pd.DataFrame]:
        """获取融资融券市场级汇总数据（沪市，近 5 日）。通过 DataFetcherManager 代理。"""
        def _fetch():
            df = self.fetcher.get_margin_trading()
            if df is None or df.empty:
                return None

            df["日期"] = pd.to_datetime(df["日期"])
            df = df.sort_values("日期").tail(_LOOKBACK_DAYS + 1)
            return df

        return await asyncio.to_thread(_fetch)

    async def _fetch_block_trade(self, quality: DataQuality) -> Optional[pd.DataFrame]:
        """获取大宗交易数据（近 5 日）。通过 DataFetcherManager 代理。"""
        def _fetch():
            today = datetime.now()
            end_date = today.strftime("%Y%m%d")
            start_date = (today - timedelta(days=_LHB_DATE_RANGE_DAYS)).strftime("%Y%m%d")

            df = self.fetcher.get_block_trade(start_date=start_date, end_date=end_date)
            if df is None or df.empty:
                return None

            return df

        return await asyncio.to_thread(_fetch)

    # =================================================================
    # 评分计算
    # =================================================================

    def _calculate_institutional_score(
        self,
        raw_data: dict[str, Any],
        quality: DataQuality,
    ) -> CapitalContext:
        """综合机构态度评分（-1 ~ 1），支持降级模式。

        每个维度独立评分，按权重归一化加权。
        缺失维度自动排除，置信度按可用比例下调。
        """
        evidence = CapitalEvidence()
        available_scores: list[float] = []
        available_weights: list[float] = []
        missing_dimensions: list[str] = []

        # --- 维度 1：北向资金（权重 0.25）---
        north_score = self._score_northbound(raw_data.get("northbound"))
        if north_score is not None:
            score, ev_dict = north_score
            evidence.northbound = ev_dict
            available_scores.append(score)
            available_weights.append(DIMENSION_WEIGHTS["northbound"])
            quality.available_fields.append("northbound")
        else:
            missing_dimensions.append(DIMENSION_LABELS["northbound"])
            quality.fetch_failed_fields.append("northbound")

        # --- 维度 2：龙虎榜（权重 0.25）---
        dt_score = self._score_dragon_tiger(raw_data.get("dragon_tiger"))
        if dt_score is not None:
            score, ev_dict = dt_score
            evidence.dragon_tiger = ev_dict
            available_scores.append(score)
            available_weights.append(DIMENSION_WEIGHTS["dragon_tiger"])
            quality.available_fields.append("dragon_tiger")
        else:
            missing_dimensions.append(DIMENSION_LABELS["dragon_tiger"])
            quality.fetch_failed_fields.append("dragon_tiger")

        # --- 维度 3：ETF 申赎（权重 0.25）---
        # 当前无可用 API，始终缺失
        missing_dimensions.append(DIMENSION_LABELS["etf_flow"])
        quality.missing_fields.append("etf_flow")

        # --- 维度 4：融资融券（权重 0.15）---
        margin_score = self._score_margin_trading(raw_data.get("margin_trading"))
        if margin_score is not None:
            score, ev_dict = margin_score
            evidence.margin_trading = ev_dict
            available_scores.append(score)
            available_weights.append(DIMENSION_WEIGHTS["margin_trading"])
            quality.available_fields.append("margin_trading")
        else:
            missing_dimensions.append(DIMENSION_LABELS["margin_trading"])
            quality.fetch_failed_fields.append("margin_trading")

        # --- 维度 5：大宗交易（权重 0.10）---
        block_score = self._score_block_trade(raw_data.get("block_trade"))
        if block_score is not None:
            score, ev_dict = block_score
            evidence.block_trade = ev_dict
            available_scores.append(score)
            available_weights.append(DIMENSION_WEIGHTS["block_trade"])
            quality.available_fields.append("block_trade")
        else:
            missing_dimensions.append(DIMENSION_LABELS["block_trade"])
            quality.fetch_failed_fields.append("block_trade")

        # === 降级处理 ===
        if len(available_scores) < 2:
            return CapitalContext(
                status="failed",
                institutional_attitude="neutral",
                institutional_score=0.0,
                confidence_multiplier=0.0,
                evidence=evidence,
                missing_dimensions=missing_dimensions,
                reasoning=f"可用数据不足（仅 {len(available_scores)} 个维度）",
                data_quality=quality,
            )

        # 权重归一化
        total_weight = sum(available_weights)
        institutional_score = sum(
            s * w / total_weight for s, w in zip(available_scores, available_weights)
        )
        institutional_score = clip(institutional_score, -1.0, 1.0)

        # 置信度 = 可用维度数 / 总维度数（5）
        confidence_multiplier = len(available_scores) / len(DIMENSION_WEIGHTS)

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
    def _score_northbound(
        df: Optional[pd.DataFrame],
    ) -> Optional[tuple[float, dict]]:
        """北向资金评分。

        逻辑：5 日累计净买入 → base_score + 加速度 + 集中度加分
        单位：当日成交净买额 为亿元。
        """
        if df is None or df.empty:
            return None

        if "当日成交净买额" not in df.columns:
            return None

        net_buy = df["当日成交净买额"].dropna()
        if net_buy.empty or len(net_buy) < 2:
            return None

        # 单位转换：亿 → 元（公式中除以 5e9、2e9 等）
        net_buy_yuan = net_buy * 1e8

        cumulative_5d = float(net_buy_yuan.sum())
        base_score = clip(cumulative_5d / 5e9, -1.0, 1.0)

        today = float(net_buy_yuan.iloc[-1])
        avg_5d = cumulative_5d / len(net_buy_yuan)
        acceleration = clip((today - avg_5d) / 2e9, -0.3, 0.3)

        concentration_bonus = 0.1 if today > 1e10 else 0.0

        north_score = clip(base_score + acceleration + concentration_bonus, -1.0, 1.0)

        cumulative_5d_yi = cumulative_5d / 1e8
        today_yi = today / 1e8

        if acceleration > 0.1:
            accel_label = "加速流入"
        elif acceleration < -0.1:
            accel_label = "减速"
        else:
            accel_label = "稳定"

        return north_score, {
            "score": round(north_score, 4),
            "cumulative_5d": round(cumulative_5d_yi, 2),
            "today": round(today_yi, 2),
            "acceleration": accel_label,
        }

    @staticmethod
    def _score_dragon_tiger(
        df: Optional[pd.DataFrame],
    ) -> Optional[tuple[float, dict]]:
        """龙虎榜机构席位评分。

        逻辑：区间内机构净买入总额 / 5e8 为基准分。
        同一股票多日重复上榜时，净买入累计、名称取最新。
        单位：机构买入净额 为元。
        """
        if df is None or df.empty:
            return None

        if "机构买入净额" not in df.columns:
            return None

        total_net_buy = float(df["机构买入净额"].sum())

        # 按股票聚合：累计净买入 + 取最新名称
        bought_stocks: list[str] = []
        if "代码" in df.columns and "名称" in df.columns:
            stock_agg = (
                df.groupby("代码")
                .agg(净买入=("机构买入净额", "sum"), 名称=("名称", "last"))
                .sort_values("净买入", ascending=False)
            )
            top_bought = stock_agg[stock_agg["净买入"] > 0].head(5)
            bought_stocks = [
                f"{code}.{row['名称']}" for code, row in top_bought.iterrows()
            ]

        dt_score = clip(total_net_buy / 5e8, -1.0, 1.0)

        return dt_score, {
            "score": round(dt_score, 4),
            "institutional_net_buy": round(total_net_buy / 1e8, 2),
            "mainline_stocks_bought": bought_stocks,
        }

    @staticmethod
    def _score_margin_trading(
        df: Optional[pd.DataFrame],
    ) -> Optional[tuple[float, dict]]:
        """融资融券评分。

        逻辑：最新一日融资余额相对前一日变化 / 2e9 → clip(-0.5, 0.5)
        单位：融资融券余额 为元。
        """
        if df is None or df.empty:
            return None

        if "融资余额" not in df.columns:
            return None

        balance = df["融资余额"].dropna()
        if len(balance) < 2:
            return None

        # 最新余额与前一日余额
        latest = float(balance.iloc[-1])
        prev = float(balance.iloc[-2])
        daily_change = latest - prev

        margin_score = clip(daily_change / 2e9, -0.5, 0.5)

        return margin_score, {
            "score": round(margin_score, 4),
            "balance_change": round(daily_change / 1e8, 2),
            "latest_balance": round(latest / 1e8, 2),
        }

    @staticmethod
    def _score_block_trade(
        df: Optional[pd.DataFrame],
    ) -> Optional[tuple[float, dict]]:
        """大宗交易评分。

        逻辑：按成交额加权平均折溢率 → 分段评分
        - 折价 > 5% → 甩货信号（看空）
        - 溢价 > 2% → 抢筹信号（看多）
        - 其他 → 平价成交
        单位：成交总额 为万元，折溢率 为比率。
        """
        if df is None or df.empty:
            return None

        required_cols = {"折溢率", "成交总额"}
        if not required_cols.issubset(set(df.columns)):
            return None

        # 清理数据
        valid = df.dropna(subset=["折溢率", "成交总额"])
        if valid.empty:
            return None

        # 按成交额加权平均折溢率
        total_amount = float(valid["成交总额"].sum())
        if total_amount <= 0:
            return None

        weighted_premium = float(
            (valid["折溢率"] * valid["成交总额"]).sum() / total_amount
        )

        # 分段评分
        if weighted_premium < _BLOCK_DISCOUNT_SIGNAL:
            block_score = clip(weighted_premium * 10, -1.0, -0.3)
            signal_type = "甩货（折价 > 5%）"
        elif weighted_premium > _BLOCK_PREMIUM_SIGNAL:
            block_score = clip(weighted_premium * 8, 0.3, 1.0)
            signal_type = "抢筹（溢价）"
        else:
            block_score = clip(weighted_premium * 5, -0.3, 0.3)
            signal_type = "平价成交"

        # 成交总额：万元 → 亿元
        amount_yi = total_amount / 1e4

        return block_score, {
            "score": round(block_score, 4),
            "premium_ratio": round(weighted_premium, 6),
            "signal_type": signal_type,
            "amount": round(amount_yi, 2),
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
        if ctx.evidence.northbound:
            nb = ctx.evidence.northbound
            evidence_parts.append(
                f"- 北向资金：5日累计 {nb['cumulative_5d']}亿，"
                f"今日 {nb['today']}亿（{nb['acceleration']}）"
            )
        if ctx.evidence.dragon_tiger:
            dt = ctx.evidence.dragon_tiger
            stocks = "、".join(dt.get("mainline_stocks_bought", [])[:3]) or "无"
            evidence_parts.append(
                f"- 龙虎榜：机构席位净买入 {dt['institutional_net_buy']}亿，"
                f"买入个股：{stocks}"
            )
        if ctx.evidence.etf_flow:
            etf = ctx.evidence.etf_flow
            evidence_parts.append(f"- ETF申赎：净申购 {etf['net_subscription']}亿份")
        if ctx.evidence.margin_trading:
            mt = ctx.evidence.margin_trading
            evidence_parts.append(
                f"- 融资融券：融资余额变化 {mt['balance_change']}亿"
            )
        if ctx.evidence.block_trade:
            bt = ctx.evidence.block_trade
            evidence_parts.append(
                f"- 大宗交易：{bt['signal_type']}（溢价率 {bt['premium_ratio']:.4f}，"
                f"成交额 {bt['amount']}亿）"
            )

        evidence_text = "\n".join(evidence_parts) if evidence_parts else "无可用数据"

        trend = getattr(market_context.tech_growth, "trend", "未知")
        mainline_count = len(sector_context.active_mainline)

        prompt = (
            "你是一个专业的资金流分析师。根据以下机构资金数据，撰写一段简洁的分析报告。\n\n"
            f"机构态度评分：{ctx.institutional_score}（{ctx.institutional_attitude}）\n\n"
            f"支撑证据：\n{evidence_text}\n\n"
            f"大盘背景：科技成长趋势={trend}\n"
            f"板块背景：活跃主线数量={mainline_count}\n\n"
            "请输出：\n"
            "1. 一段150字以内的分析文本，解释机构资金的整体态度\n"
            "2. 重点说明机构对主线板块的态度（是否在加仓）\n"
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

        if ctx.evidence.northbound:
            nb = ctx.evidence.northbound
            parts.append(f"北向资金5日累计 {nb['cumulative_5d']}亿（{nb['acceleration']}）。")
        if ctx.evidence.dragon_tiger:
            dt = ctx.evidence.dragon_tiger
            parts.append(f"龙虎榜机构净买入 {dt['institutional_net_buy']}亿。")
        if ctx.evidence.margin_trading:
            mt = ctx.evidence.margin_trading
            parts.append(f"融资余额变化 {mt['balance_change']}亿。")
        if ctx.evidence.block_trade:
            bt = ctx.evidence.block_trade
            parts.append(f"大宗交易{bt['signal_type']}，成交额 {bt['amount']}亿。")

        if ctx.missing_dimensions:
            parts.append(f"缺失维度: {'、'.join(ctx.missing_dimensions)}。")

        return "".join(parts)

    # =================================================================
    # Mock 输出
    # =================================================================

    def _mock_output(self) -> CapitalContext:
        """Mock 输出（机构偏中性）"""
        return CapitalContext(
            status="degraded",
            institutional_attitude="neutral",
            institutional_score=0.05,
            confidence_multiplier=0.8,
            evidence=CapitalEvidence(
                northbound={
                    "score": 0.15,
                    "cumulative_5d": 12.5,
                    "today": 3.2,
                    "acceleration": "稳定",
                },
                dragon_tiger={
                    "score": 0.10,
                    "institutional_net_buy": 2.8,
                    "mainline_stocks_bought": ["002935", "300377"],
                },
                etf_flow={
                    "score": -0.05,
                    "net_subscription": -5.2,
                },
                margin_trading=None,
                block_trade={
                    "score": 0.05,
                    "premium_ratio": 0.01,
                    "signal_type": "平价成交",
                    "amount": 8.5,
                },
            ),
            missing_dimensions=["融资融券"],
            reasoning="[Mock] 北向资金小幅流入，龙虎榜机构净买入，但ETF净赎回。整体中性偏多。",
            data_quality=DataQuality(
                available_fields=["northbound", "dragon_tiger", "etf_flow", "block_trade"],
                missing_fields=["margin_trading"],
                fetch_failed_fields=["margin_trading"],
            ),
        )
