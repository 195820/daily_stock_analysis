"""
Node 1: 大盘分析节点

职责：基于纯技术指标规则，判断各风格方向趋势，决定仓位上限。

设计理念：
- 纯规则驱动：趋势判断由5个技术指标加权打分得出
- 分风格输出：科技成长/中小盘/大盘蓝筹分别判断
- 多时间框架 mini GOT：short/medium/long 三路并行 → 规则检测矛盾 → LLM 研判

数据源：
- 5 个指数 K 线（250日）：上证/沪深300/中证500/创业板/科创50
- 市场宽度：涨跌家数/涨停跌停统计
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from src.got.schemas import (
    MarketContext,
    MarketStateSignal,
    TechGrowthSignal,
    StyleSignal,
    TimeframeSignals,
    TimeframeGOTResult,
    RegimeMetadata,
    KeyLevels,
    DataQuality,
    Signal,
)
from src.got.indicators import calculate_all_indicators, clip

logger = logging.getLogger(__name__)


# 指数配置
INDEX_CONFIG = {
    "tech_growth": {
        "indices": ["399006.SZ", "000688.SH"],  # 创业板指 + 科创50
        "weights": {"chinext": 0.6, "star50": 0.4},
        "aggregation": "weighted_average_with_consistency",
    },
    "mid_small_cap": {
        "indices": ["000905.SH"],  # 中证500
        "aggregation": "single",
    },
    "large_cap": {
        "indices": ["000300.SH"],  # 沪深300
        "aggregation": "single",
    },
    "display": {
        "indices": ["000001.SH"],  # 上证指数（纯展示）
        "aggregation": "none",
    },
}


class MarketAnalysisNode:
    """大盘分析节点"""

    def __init__(self, use_mock: bool = True):
        self.use_mock = use_mock
        self._fetcher = None

    def _get_fetcher(self):
        """延迟初始化 DataFetcherManager"""
        if self._fetcher is None:
            from data_provider import DataFetcherManager
            self._fetcher = DataFetcherManager()
        return self._fetcher

    async def run(self) -> MarketContext:
        """执行大盘分析"""
        if self.use_mock:
            logger.info("[Node 1] 使用 mock 数据")
            return self._mock_output()

        logger.info("[Node 1] 获取真实数据")
        return await self._run_real()

    async def _run_real(self) -> MarketContext:
        """真实数据逻辑"""
        fetcher = self._get_fetcher()
        available_fields = []
        missing_fields = []
        fetch_failed_fields = []

        # Step 1: 获取指数 K 线数据
        index_data = {}
        for style, config in INDEX_CONFIG.items():
            for code in config["indices"]:
                try:
                    # 获取 250 日 K 线
                    df = self._get_index_kline(fetcher, code, days=250)
                    if df is not None and len(df) > 0:
                        index_data[code] = df
                        available_fields.append(f"kline_{code}")
                    else:
                        missing_fields.append(f"kline_{code}")
                except Exception as e:
                    logger.error(f"获取指数 {code} K线失败: {e}")
                    fetch_failed_fields.append(f"kline_{code}")

        if not index_data:
            raise RuntimeError("无法获取任何指数数据")

        # Step 2: 计算各指数技术指标
        indicator_results = {}
        for code, df in index_data.items():
            try:
                indicators = calculate_all_indicators(df)
                indicator_results[code] = indicators
            except Exception as e:
                logger.error(f"计算指数 {code} 指标失败: {e}")

        # Step 3: 风格组合并
        tech_growth = self._aggregate_style_group("tech_growth", indicator_results, index_data)
        mid_small_cap = self._aggregate_style_group("mid_small_cap", indicator_results, index_data)
        large_cap = self._aggregate_style_group("large_cap", indicator_results, index_data)

        # Step 4: 多时间框架 mini GOT（科技成长）— score 级矛盾检测
        timeframe_got = self._aggregate_timeframe_signals(
            tech_growth.get("signals", {}),
            tech_growth.get("regime_metadata", {}),
        )

        # Step 5: 支撑/阻力位
        key_levels = self._calculate_key_levels(index_data.get("399006.SZ"))

        # Step 6: T-0 实时涨跌幅（腾讯接口，同步 HTTP → 放到线程池避免阻塞事件循环）
        today_change = await asyncio.to_thread(self._fetch_realtime_index)

        # Step 7: 跨指数一致性 + confidence（三因素加权）
        cross_consistency, cross_pattern = self._compute_cross_index_consistency(indicator_results)
        regime_metadata = tech_growth.get("regime_metadata", {})
        confidence = self._calculate_confidence(cross_consistency, timeframe_got, regime_metadata)

        # Step 8: 仓位上限计算（依赖 cross_consistency）
        # 使用创业板指的收盘价作为价格历史（代表成长股波动率）
        price_history = index_data.get("399006.SZ", pd.Series()).get("close")
        position_details = self._calculate_position_limit_v2(
            timeframe_got,
            mid_small_cap.get("trend", "neutral"),
            large_trend=large_cap.get("trend", "neutral"),
            price_history=price_history,
            cross_index_consistency=cross_consistency,
        )
        position_limit = position_details["position_limit"]

        # Step 9: T-0 实时修正 confidence
        confidence = self._apply_realtime_adjustment(
            confidence, tech_growth.get("score", 0), today_change
        )

        # Step 10: 市场宽度修正（涨跌家数 vs 技术面方向）
        breadth_data, breadth_quality = self._fetch_market_breadth(fetcher)
        if breadth_data:
            confidence = self._apply_breadth_adjustment(
                confidence, tech_growth.get("score", 0), breadth_data
            )

        # Step 11: LLM reasoning 生成
        reasoning = await self._generate_llm_reasoning(
            tech_growth, timeframe_got, cross_pattern, today_change, breadth_data
        )

        # Step 12: 合成市场状态条件变量（下游 Node2/Node6 主消费入口，2026-08-19 新增）
        market_state = self._build_market_state(
            tech_growth=tech_growth,
            timeframe_got=timeframe_got,
            today_change=today_change,
            position_limit=position_limit,
            position_details=position_details,
        )

        # 构建输出
        return MarketContext(
            tech_growth=tech_growth,
            market_state=market_state,
            mid_small_cap=mid_small_cap,
            large_cap=large_cap,
            position_limit=position_limit,
            confidence=confidence,
            key_levels=key_levels,
            reasoning=reasoning,
            data_quality=DataQuality(
                available_fields=available_fields + ([
                    "market_breadth"] if breadth_data else []),
                missing_fields=missing_fields + ([
                    "market_breadth"] if not breadth_data and breadth_quality == "missing" else []),
                fetch_failed_fields=fetch_failed_fields + ([
                    "market_breadth"] if breadth_quality == "failed" else []),
            ),
            today_change=today_change,
            market_breadth=breadth_data,
            realized_volatility=position_details.get("realized_volatility"),
            vol_adjusted_position=position_details.get("vol_adjusted_position"),
            regime_scale=position_details.get("regime_scale"),
        )

    def _build_market_state(
        self,
        tech_growth: dict,
        timeframe_got: TimeframeGOTResult,
        today_change: dict,
        position_limit: float,
        position_details: dict,
    ) -> MarketStateSignal:
        """合成市场状态条件变量（2026-08-19 新增）。

        把 Node 1 已算的散字段聚合成可传导的 attack_power（进攻强度 0~1），
        供 Node 2（板块选股强度）和 Node 6（聚合权重）直接消费。

        依据（2026-08-18 东吴金工报告 + RegimeFolio）：
        - 市场状态应作为跨股票共享的条件变量，重塑下游表示与合成
        - 预测与配置必须共享同一 regime 信号，避免处方脱节（prescription misalignment）

        attack_power 合成：
        - 动量分: tech_score (-1~1) → (score+1)/2
        - mini-GOT 分: unified_score (-1~1) → (unified_score+1)/2
        - 风险分: position_limit (0.2~1.5) → 归一化
        - 暴跌 override: 最弱指数 < -3% 时强制压低到 0.15（防守优先）
        """
        # 1. 基础攻击力：技术面 + mini-GOT 加权
        tech_score = float(tech_growth.get("score", 0) or 0)
        mini_score = getattr(timeframe_got, "unified_score", 0) or 0
        momentum_power = (min(max(tech_score, -1), 1) + 1) / 2
        mini_power = (min(max(mini_score, -1), 1) + 1) / 2
        base_power = 0.5 * momentum_power + 0.5 * mini_power

        # 2. 风险状态：position_limit 反映风险预算（越低压仓越保守）
        # position_limit 范围约 [0.2, 1.5]，映射到 0~1
        pos_limit = float(position_limit or 0.5)
        risk_power = min(max((pos_limit - 0.2) / 1.3, 0.0), 1.0)

        # 3. 合成攻击力（动量 50% + 风险预算 50%）
        attack_power = 0.5 * base_power + 0.5 * risk_power

        # 4. 暴跌 override：最弱指数单日 < -3% → 强制防守
        crash_override = False
        if today_change:
            worst = min(today_change.values())
            if worst < -3.0:
                crash_override = True
                attack_power = min(attack_power, 0.15)

        # 5. 风险状态分类
        vol = position_details.get("realized_volatility")
        risk_state = "normal"
        if vol is not None:
            if vol > 0.45:
                risk_state = "extreme_high"
            elif vol > 0.30:
                risk_state = "high"

        # 6. regime 分类
        trend = tech_growth.get("trend", "neutral")
        regime = "transition"
        if trend == "bullish" and attack_power > 0.6:
            regime = "bull"
        elif trend == "bearish" or (crash_override and attack_power < 0.3):
            regime = "bear"

        return MarketStateSignal(
            attack_power=round(float(min(max(attack_power, 0.0), 1.0)), 4),
            regime=regime,
            mini_got_trend=getattr(timeframe_got, "unified_trend", "neutral"),
            mini_got_score=float(getattr(timeframe_got, "unified_score", 0) or 0),
            mini_got_confidence=float(getattr(timeframe_got, "timeframe_confidence", 1.0) or 1.0),
            timeframe_consistency=(tech_growth.get("consistency") or "aligned"),
            contradiction_type=getattr(timeframe_got, "contradiction_type", None),
            risk_state=risk_state,
            realized_volatility=vol,
            crash_override=crash_override,
        )

    def _get_index_kline(self, fetcher, code: str, days: int = 250) -> Optional[pd.DataFrame]:
        """获取指数 K 线数据"""
        # 保留原始代码格式（如 399006.SZ），让 DataFetcherManager 自动处理
        # 不要在这里转换为 sz399006 格式，否则 _is_index_code() 无法识别
        try:
            # 使用 DataFetcherManager 获取日线数据
            df, source = fetcher.get_daily_data(code, days=days)
            if df is not None and len(df) > 0:
                # 标准化列名
                df = df.rename(columns={
                    "日期": "date",
                    "开盘": "open",
                    "收盘": "close",
                    "最高": "high",
                    "最低": "low",
                    "成交量": "volume",
                })
                return df
        except Exception as e:
            logger.warning(f"获取指数 {code} 失败: {e}")

        return None

    def _aggregate_style_group(self, style: str, indicator_results: dict, index_data: dict) -> dict:
        """风格组合并：加权平均 + 一致性折扣"""
        config = INDEX_CONFIG[style]
        indices = config["indices"]
        aggregation = config.get("aggregation", "single")

        if aggregation == "none":
            # 纯展示
            return {"trend": "display", "score": 0.0}

        scores = []
        for code in indices:
            if code in indicator_results:
                scores.append(indicator_results[code]["weighted_score"])

        if not scores:
            return {
                "trend": "neutral",
                "score": 0.0,
                "signals": TimeframeSignals(
                    short_term=Signal(trend="neutral", score=0.0),
                    medium_term=Signal(trend="neutral", score=0.0),
                    long_term=Signal(trend="neutral", score=0.0),
                ),
                "timeframe_got": TimeframeGOTResult(
                    unified_trend="neutral",
                    unified_score=0.0,
                    timeframe_confidence=0.5,
                ),
                "regime_metadata": RegimeMetadata(
                    adx_value=25.0,
                    regime="mild_trending",
                    volatility_regime="normal",
                ),
            }

        if aggregation == "weighted_average_with_consistency":
            weights = config.get("weights", {})
            weight_values = list(weights.values())
            weighted_scores = [s * w for s, w in zip(scores, weight_values)]
            base_score = sum(weighted_scores) / sum(weight_values)

            # 一致性检测
            signs = [1 if s > 0.1 else (-1 if s < -0.1 else 0) for s in scores]
            same_direction = all(s == signs[0] for s in signs) and signs[0] != 0
            consistency_discount = 1.0 if same_direction else 0.7
            final_score = base_score * consistency_discount
            consistency_label = "aligned" if same_direction else "divergent"
        else:
            final_score = scores[0]
            consistency_label = "single"

        # 趋势判断（阈值 ±0.3，收窄自 ±0.5 以提高区分度）
        if final_score > 0.3:
            trend = "bullish"
        elif final_score < -0.3:
            trend = "bearish"
        else:
            trend = "neutral"

        # 获取第一个指数的指标详情
        first_code = indices[0]
        indicators = indicator_results.get(first_code, {})

        # 多时间框架信号：用不同 lookback 独立计算
        first_df = index_data.get(first_code)
        if first_df is not None:
            signals = self._calculate_timeframe_signals(first_df)
        else:
            signals = TimeframeSignals(
                short_term=Signal(trend="neutral", score=0.0),
                medium_term=Signal(trend="neutral", score=0.0),
                long_term=Signal(trend="neutral", score=0.0),
            )

        return {
            "trend": trend,
            "score": final_score,
            "constituents": {code: indicator_results.get(code, {}).get("weighted_score", 0.0) for code in indices},
            "consistency": consistency_label,
            "signals": signals,
            "timeframe_got": self._aggregate_timeframe_signals(
                signals,
                indicators.get("regime_metadata", {}),
            ),
            "regime_metadata": RegimeMetadata(
                adx_value=indicators.get("adx_value", 25.0),
                regime=indicators.get("regime", "mild_trending"),
                volatility_regime=indicators.get("volatility_regime", "normal"),
                weights_used=indicators.get("weights_used", {}),
            ),
        }

    def _calculate_timeframe_signals(self, df: pd.DataFrame) -> TimeframeSignals:
        """计算多时间框架信号：用完整数据，不同框架用不同参数敏感度"""
        from src.got.indicators import (
            calculate_ma_alignment_score,
            calculate_macd_score,
            calculate_volume_price_score,
            calculate_bollinger_score,
            clip,
        )

        def _score_timeframe(df: pd.DataFrame, ma_periods: list, recent_days: int) -> float:
            """用指定参数计算单框架评分"""
            # MA 排列：用不同周期
            ma_score = calculate_ma_alignment_score(df, periods=ma_periods)

            # MACD：用完整数据计算（参数不变，但观察近期柱状图趋势）
            macd_score = calculate_macd_score(df)

            # 量价：用不同 recent_days 观察近期行为
            volume_price_score = calculate_volume_price_score(df, lookback=recent_days)

            # 布林带：用完整数据
            bollinger_score = calculate_bollinger_score(df)

            # 等权加权（市场宽度暂缺）
            return (
                ma_score * 0.30 +
                macd_score * 0.30 +
                volume_price_score * 0.25 +
                bollinger_score * 0.15
            )

        short_score = _score_timeframe(df, ma_periods=[5, 10, 20], recent_days=5)
        medium_score = _score_timeframe(df, ma_periods=[5, 10, 20, 60], recent_days=20)
        long_score = _score_timeframe(df, ma_periods=[10, 20, 60, 120, 250], recent_days=60)

        return TimeframeSignals(
            short_term=Signal(trend=self._score_to_trend(short_score), score=clip(short_score, -1.0, 1.0)),
            medium_term=Signal(trend=self._score_to_trend(medium_score), score=clip(medium_score, -1.0, 1.0)),
            long_term=Signal(trend=self._score_to_trend(long_score), score=clip(long_score, -1.0, 1.0)),
        )

    def _aggregate_timeframe_signals(self, signals: TimeframeSignals, regime_metadata: dict) -> TimeframeGOTResult:
        """时间框架 mini GOT：score 级矛盾检测 + 模式分类

        改进点（相比旧版 categorical 比较）：
        - 检测 score 幅度差异而非 trend 字符串差异
        - 检测短-中冗余（信息量低）
        - 分类矛盾模式（pullback_in_uptrend / rally_in_downtrend 等）
        """
        short = signals.short_term
        medium = signals.medium_term
        long_ = signals.long_term

        # ── Score 级矛盾检测 ──
        contradictions = []

        # 短-长背离：幅度差 > 0.3 → 显著背离
        sl_gap = abs(short.score - long_.score)
        if sl_gap > 0.3:
            contradictions.append("short_vs_long")

        # 短-中冗余：幅度差 < 0.05 → 高度冗余（信息量低）
        sm_gap = abs(short.score - medium.score)
        if sm_gap < 0.05 and short.score != 0:
            contradictions.append("short_medium_redundant")

        # ── 矛盾模式分类 ──
        pattern = self._classify_timeframe_pattern(short, medium, long_)

        # ── 加权聚合（保留原权重）──
        unified_score = short.score * 0.20 + medium.score * 0.35 + long_.score * 0.45

        # ── 置信度折扣（基于矛盾模式）──
        if not contradictions:
            tf_confidence = 1.0
        elif pattern in ("pullback_in_downtrend", "rally_in_downtrend"):
            tf_confidence = 0.6  # 下跌中的反弹/回调 → 低置信
        elif pattern == "pullback_in_uptrend":
            tf_confidence = 0.85  # 上涨中的回调 → 较高置信（正常）
        elif pattern in ("broad_decline", "broad_advance"):
            tf_confidence = 1.0  # 全框架一致 → 高置信
        else:
            tf_confidence = 0.7  # 其他矛盾

        return TimeframeGOTResult(
            unified_trend=self._score_to_trend(unified_score),
            unified_score=unified_score,
            contradiction_type=pattern,
            timeframe_confidence=tf_confidence,
            llm_reasoning=None,  # LLM reasoning 在 Node 1 顶层统一生成
        )

    def _classify_timeframe_pattern(self, short, medium, long_) -> str:
        """分类时间框架矛盾模式"""
        s_sign = 1 if short.score > 0.1 else (-1 if short.score < -0.1 else 0)
        m_sign = 1 if medium.score > 0.1 else (-1 if medium.score < -0.1 else 0)
        l_sign = 1 if long_.score > 0.1 else (-1 if long_.score < -0.1 else 0)

        if s_sign == -1 and l_sign >= 0:
            return "pullback_in_uptrend"       # 短空长中/多 → 上涨回调
        elif s_sign == 1 and l_sign <= 0:
            return "rally_in_downtrend"        # 短多长空 → 下跌反弹
        elif s_sign == -1 and m_sign == -1 and l_sign == -1:
            return "broad_decline"             # 全框架空头 → 全面下跌
        elif s_sign == 1 and m_sign == 1 and l_sign == 1:
            return "broad_advance"             # 全框架多头 → 全面上涨
        elif abs(short.score - medium.score) < 0.05:
            return "short_medium_redundant"    # 短中期高度冗余
        else:
            return "mixed_signals"             # 混合信号

    def _score_to_trend(self, score: float) -> str:
        """score → trend 映射（阈值 ±0.3）"""
        if score > 0.3:
            return "bullish"
        elif score < -0.3:
            return "bearish"
        else:
            return "neutral"

    def _estimate_realized_volatility(self, price_series: pd.Series, lookback_days: int = 20) -> float:
        """
        估计实现波动率（年化）

        参考：Moreira & Muir (2017), "The Impact of Volatility Targeting"

        Args:
            price_series: 价格序列（收盘价）
            lookback_days: 回溯窗口（默认 20 日）

        Returns:
            realized_vol: 年化波动率（0.0 ~ 1.0+）
        """
        if price_series is None or len(price_series) < lookback_days:
            return 0.15  # 默认 15% 波动率

        # 计算对数收益率
        log_returns = np.log(price_series / price_series.shift(1)).dropna()

        if len(log_returns) < lookback_days:
            return 0.15

        # 最近 N 日的标准差 × √252 = 年化波动率
        recent_vol = log_returns.iloc[-lookback_days:].std()
        annualized_vol = recent_vol * np.sqrt(252)

        return max(0.05, annualized_vol)  # 最低 5%

    def _calculate_volatility_adjusted_position(
        self,
        realized_vol: float,
        target_vol: float = 0.15,
    ) -> float:
        """
        波动率目标制：仓位 = target_vol / realized_vol

        核心思想：波动率高时降低仓位，波动率低时提高仓位，保持组合风险恒定

        参考：Moreira & Muir (2017)

        Args:
            realized_vol: 实现波动率（年化）
            target_vol: 目标波动率（默认 15%）

        Returns:
            position_scale: 0.5 ~ 1.5
        """
        if realized_vol <= 0:
            return 1.0

        scale = target_vol / realized_vol

        # 限制范围：0.5x ~ 1.5x（提高最低限制，避免高波动时仓位过低）
        return clip(scale, 0.5, 1.5)

    def _calculate_regime_scaling(
        self,
        timeframe_got: TimeframeGOTResult,
        cross_index_consistency: float,
    ) -> float:
        """
        状态感知缩放：连续函数替代二元判定

        参考：
        - AlgoKing "Dynamic Position Sizing with Kelly + Regime"
        - MSCI "Adaptive Multi-Factor Allocation"

        核心思想：根据多个因子的置信度加权平均，输出 0.2 ~ 1.0 的连续缩放因子

        Args:
            timeframe_got: 时间框架 GOT 结果
            cross_index_consistency: 跨指数一致性（0 ~ 1）

        Returns:
            regime_scale: 0.2 ~ 1.0（永远不会归零）
        """
        # 因子 1: 时间框架一致性（0 ~ 1）
        tf_factor = timeframe_got.timeframe_confidence

        # 因子 2: 跨指数一致性（0 ~ 1）
        cross_factor = cross_index_consistency

        # 因子 3: 方向强度（unified_score 映射到 0.3 ~ 1.0）
        score = timeframe_got.unified_score
        direction_factor = clip((score + 1.0) / 2.0, 0.3, 1.0)

        # 加权平均（置信度加权）
        regime_scale = (
            tf_factor * 0.40 +
            cross_factor * 0.30 +
            direction_factor * 0.30
        )

        return clip(regime_scale, 0.2, 1.0)

    def _calculate_position_limit_v2(
        self,
        timeframe_got: TimeframeGOTResult,
        mid_trend: str,
        large_trend: str,
        price_history: Optional[pd.Series] = None,
        cross_index_consistency: float = 0.5,
    ) -> dict:
        """
        新版仓位上限：两层决策系统

        Layer 1: 波动率调整 → 保持风险恒定
        Layer 2: 状态感知缩放 → 适应市场环境

        参考：
        - Moreira & Muir (2017), "The Impact of Volatility Targeting"
        - AlgoKing "Dynamic Position Sizing with Kelly + Regime"

        Args:
            timeframe_got: 时间框架 GOT 结果
            mid_trend: 中期趋势（未使用，保留兼容性）
            large_trend: 长期趋势（未使用，保留兼容性）
            price_history: 价格历史（用于波动率估计）
            cross_index_consistency: 跨指数一致性

        Returns:
            dict: {position_limit, realized_volatility, vol_adjusted_position, regime_scale}
        """
        # Layer 1: 波动率调整
        realized_vol = self._estimate_realized_volatility(price_history)
        vol_adjusted = self._calculate_volatility_adjusted_position(realized_vol)

        # Layer 2: 状态感知缩放
        regime_scale = self._calculate_regime_scaling(
            timeframe_got,
            cross_index_consistency,
        )

        # 基础仓位 = 波动率调整 × 状态缩放
        position_limit = vol_adjusted * regime_scale

        # 限制范围：0.2 ~ 1.5
        position_limit = clip(position_limit, 0.2, 1.5)

        return {
            "position_limit": position_limit,
            "realized_volatility": realized_vol,
            "vol_adjusted_position": vol_adjusted,
            "regime_scale": regime_scale,
        }

    def _calculate_key_levels(self, df: Optional[pd.DataFrame]) -> KeyLevels:
        """计算支撑/阻力位"""
        if df is None or len(df) < 20:
            return KeyLevels()

        close = df['close']
        high = df['high']
        low = df['low']

        # 短期（20日）
        short_support = max(close.rolling(20).mean().iloc[-1], low.tail(20).min())
        short_resistance = max(high.tail(20).max(), close.rolling(20).mean().iloc[-1] * 1.05)

        # 中期（60日）
        mid_support = min(close.rolling(60).mean().iloc[-1] if len(df) >= 60 else close.min(), low.tail(60).min())
        mid_resistance = high.tail(60).max() if len(df) >= 60 else high.max()

        return KeyLevels(
            short_term={"support": short_support, "resistance": short_resistance},
            mid_term={"support": mid_support, "resistance": mid_resistance},
        )

    def _calculate_confidence(
        self,
        cross_index_consistency: float,
        timeframe_got: TimeframeGOTResult,
        regime_metadata: dict,
    ) -> float:
        """置信度 = 三因素加权（替代旧版 |score| 语义）

        1. 跨指数一致性（35%）: 5 个指数同向 → 1.0, 分化 → 低
        2. 跨时间框架一致性（35%）: timeframe_got.timeframe_confidence
        3. ADX 趋势强度（30%）: ADX ≥ 40 → 1.0, ADX=0 → 0.0
        """
        tf_conf = timeframe_got.timeframe_confidence

        adx = regime_metadata.get("adx_value", 25.0) if isinstance(regime_metadata, dict) else getattr(regime_metadata, "adx_value", 25.0)
        adx_factor = min(adx / 40.0, 1.0)

        confidence = (cross_index_consistency * 0.35 +
                      tf_conf * 0.35 +
                      adx_factor * 0.30)

        return clip(confidence, 0.05, 1.0)

    def _compute_cross_index_consistency(self, indicator_results: dict) -> tuple[float, str]:
        """5 个指数的方向一致性（替代 market_breadth 的"参与度"功能）

        Returns:
            (consistency_score, pattern)
            consistency_score: 0~1, 1=全部同向
            pattern: broad_advance / broad_decline / growth_lead / value_lead / divergent
        """
        scores = {}
        for code, r in indicator_results.items():
            ws = r.get("weighted_score", 0.0)
            if ws is not None:
                scores[code] = ws

        if not scores:
            return 0.0, "divergent"

        directions = {code: (1 if s > 0.1 else (-1 if s < -0.1 else 0))
                      for code, s in scores.items()}

        pos = sum(1 for d in directions.values() if d > 0)
        neg = sum(1 for d in directions.values() if d < 0)
        total = len(directions)

        consistency = abs(pos - neg) / total if total > 0 else 0

        # 分类模式
        tech_codes = {"399006.SZ", "000688.SH"}
        value_codes = {"000300.SH", "000001.SH"}
        tech_avg = sum(scores.get(c, 0) for c in tech_codes if c in scores) / max(len(tech_codes), 1)
        value_avg = sum(scores.get(c, 0) for c in value_codes if c in scores) / max(len(value_codes), 1)

        if pos == total and pos > 0:
            pattern = "broad_advance"
        elif neg == total and neg > 0:
            pattern = "broad_decline"
        elif tech_avg > 0.1 and value_avg < -0.1:
            pattern = "growth_lead"
        elif value_avg > 0.1 and tech_avg < -0.1:
            pattern = "value_lead"
        else:
            pattern = "divergent"

        return clip(consistency, 0, 1), pattern

    def _apply_realtime_adjustment(self, confidence: float, signal_score: float, today_change: dict) -> float:
        """T-0 实时涨跌幅修正置信度（Fix 4）

        技术信号方向 vs 今日实际方向不一致 → 降低 confidence
        同向 → 微增 confidence
        """
        if not today_change:
            return confidence

        tech_direction = 1 if signal_score > 0 else (-1 if signal_score < 0 else 0)
        today_values = list(today_change.values())
        today_avg = sum(today_values) / len(today_values) if today_values else 0
        today_direction = 1 if today_avg > 0.5 else (-1 if today_avg < -0.5 else 0)

        if tech_direction != 0 and today_direction != 0 and tech_direction != today_direction:
            # 技术面信号与今日实际反向 → 降低 confidence
            adjusted = confidence * 0.7
            logger.info(
                f"[Node 1] T-0 修正: 技术面={'多' if tech_direction > 0 else '空'} "
                f"vs 今日={today_avg:+.2f}% → confidence {confidence:.2f} → {adjusted:.2f}"
            )
            return adjusted
        elif tech_direction != 0 and today_direction == tech_direction:
            # 同向 → 微增
            return min(confidence * 1.1, 1.0)

        return confidence

    async def _generate_llm_reasoning(
        self,
        tech_growth: dict,
        timeframe_got: TimeframeGOTResult,
        cross_pattern: str,
        today_change: dict,
        breadth_data: Optional[dict] = None,
    ) -> str:
        """LLM 推理生成（Fix 5，1 次调用）

        失败时降级到规则拼接。
        """
        # 提取关键字段
        tg_trend = tech_growth.get("trend", "neutral") if isinstance(tech_growth, dict) else getattr(tech_growth, "trend", "neutral")
        tg_score = tech_growth.get("score", 0) if isinstance(tech_growth, dict) else getattr(tech_growth, "score", 0)
        constituents = tech_growth.get("constituents", {}) if isinstance(tech_growth, dict) else getattr(tech_growth, "constituents", {})
        signals = tech_growth.get("signals", None) if isinstance(tech_growth, dict) else getattr(tech_growth, "signals", None)

        # 格式化时间框架信号
        tf_detail = ""
        if signals:
            for tf_name in ["short_term", "medium_term", "long_term"]:
                tf_signal = getattr(signals, tf_name, None) if hasattr(signals, tf_name) else (signals.get(tf_name) if isinstance(signals, dict) else None)
                if tf_signal:
                    tf_detail += f" {tf_name}={tf_signal.score:+.2f}"

        today_text = ""
        if today_change:
            parts = [f"{k}={v:+.2f}%" for k, v in today_change.items()]
            today_text = ", ".join(parts)

        breadth_text = ""
        if breadth_data:
            up = breadth_data.get("up_count", 0)
            down = breadth_data.get("down_count", 0)
            limit_up = breadth_data.get("limit_up_count", 0)
            limit_down = breadth_data.get("limit_down_count", 0)
            breadth_text = f"上涨{up}家/下跌{down}家, 涨停{limit_up}/跌停{limit_down}"

        prompt = f"""你是资深投资顾问。基于以下大盘技术分析结果，生成简洁研判。

技术指标：
- 科技成长: {tg_trend}（score={tg_score:.2f}）
  - 创业板: {constituents.get('399006.SZ', 0):.2f}
  - 科创50: {constituents.get('000688.SH', 0):.2f}
- 时间框架: {timeframe_got.unified_trend}（unified_score={timeframe_got.unified_score:.2f}）
  -{tf_detail}
  - 矛盾模式: {timeframe_got.contradiction_type}
- 跨指数模式: {cross_pattern}
- 今日实时: {today_text or '无数据'}
- 市场宽度: {breadth_text or '无数据'}

请用 100 字以内总结市场状态和短期方向。直接输出文本，不要 JSON。"""

        try:
            from src.agent.llm_adapter import LLMToolAdapter
            adapter = LLMToolAdapter()
            response = await asyncio.to_thread(
                adapter.call_text,
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200,
            )
            content = (response.content or "").strip()
            if content:
                return content
        except Exception as e:
            logger.warning(f"[Node 1] LLM reasoning 失败: {e}，降级到规则拼接")

        # 降级：规则拼接
        return self._rule_based_reasoning(tech_growth, timeframe_got, cross_pattern, today_change)

    @staticmethod
    def _rule_based_reasoning(
        tech_growth: dict,
        timeframe_got: TimeframeGOTResult,
        cross_pattern: str,
        today_change: dict,
    ) -> str:
        """规则拼接降级方案"""
        tg_trend = tech_growth.get("trend", "neutral") if isinstance(tech_growth, dict) else "neutral"
        tg_score = tech_growth.get("score", 0) if isinstance(tech_growth, dict) else 0

        parts = [f"科技成长{tg_trend}(score={tg_score:+.2f})"]
        parts.append(f"跨指数: {cross_pattern}")
        parts.append(f"时间框架: {timeframe_got.contradiction_type}")

        if today_change:
            sh = today_change.get("sh")
            if sh is not None:
                parts.append(f"今日上证{sh:+.2f}%")

        return "，".join(parts) + "。"

    def _fetch_realtime_index(self) -> dict:
        """用腾讯接口获取三大指数 T-0 实时涨跌幅。

        Returns:
            {"sh": -1.08, "sz": -1.43, "cy": -1.38} 或空 dict（获取失败时）
        """
        import urllib.request

        url = "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=10)
            data = resp.read().decode("gbk")
            result = {}
            # 腾讯接口返回格式: v_sh000001="1~上证指数~000001~3835.06~..."
            # 字段[3]=最新价, 字段[4]=昨收, 字段[32]=涨跌幅
            for line in data.strip().split(";"):
                if "=" not in line:
                    continue
                var_name = line.split("=")[0].strip()
                parts = line.split("~")
                if len(parts) < 33:
                    continue
                try:
                    pct = float(parts[32])
                except (ValueError, IndexError):
                    continue
                if "sh000001" in var_name:
                    result["sh"] = pct
                elif "sz399001" in var_name:
                    result["sz"] = pct
                elif "sz399006" in var_name:
                    result["cy"] = pct
            if result:
                logger.info(f"[Node 1] T-0 实时指数: {result}")
            return result
        except Exception as exc:
            logger.warning(f"[Node 1] T-0 实时指数获取失败: {exc}")
            return {}

    def _fetch_market_breadth(self, fetcher) -> Tuple[Optional[Dict], str]:
        """获取市场宽度（涨跌家数）。

        Returns:
            (breadth_data, quality): quality = "normal" / "missing" / "failed"
        """
        try:
            breadth = fetcher.get_market_breadth()
            if breadth:
                logger.info(f"[Node 1] 市场宽度: 上涨{breadth.get('up_count', 0)}家, "
                            f"下跌{breadth.get('down_count', 0)}家")
                return breadth, "normal"
            return None, "missing"
        except Exception as exc:
            logger.warning(f"[Node 1] 市场宽度获取失败: {exc}")
            return None, "failed"

    def _apply_breadth_adjustment(
        self, confidence: float, tech_score: float, breadth: Dict
    ) -> float:
        """市场宽度与技术面方向不一致时，降低 confidence。

        逻辑：
        - 技术面看空 (score < -0.3) 但红盘占优 (up > down) → confidence *= 0.7
        - 技术面看多 (score > 0.3) 但绿盘占优 (up < down) → confidence *= 0.7
        - 方向一致 → 不调整
        """
        up_count = breadth.get("up_count", 0)
        down_count = breadth.get("down_count", 0)

        if up_count == 0 and down_count == 0:
            return confidence

        breadth_bullish = up_count > down_count
        tech_bearish = tech_score < -0.3
        tech_bullish = tech_score > 0.3

        # 方向不一致时降低 confidence
        if (tech_bearish and breadth_bullish) or (tech_bullish and not breadth_bullish):
            adjusted = confidence * 0.7
            logger.info(f"[Node 1] 市场宽度修正: tech_score={tech_score:.2f}, "
                        f"up/down={up_count}/{down_count}, confidence {confidence:.2f} → {adjusted:.2f}")
            return adjusted

        return confidence

    def _mock_output(self) -> MarketContext:
        """Mock 输出（震荡偏空场景）"""
        # ... (保留原有 mock 实现)
        tech_growth = TechGrowthSignal(
            trend="neutral",
            score=-0.15,
            constituents={"chinext": -0.20, "star50": -0.08},
            consistency="aligned",
            signals=TimeframeSignals(
                short_term=Signal(trend="bearish", score=-0.35),
                medium_term=Signal(trend="neutral", score=-0.10),
                long_term=Signal(trend="bullish", score=0.25),
            ),
            timeframe_got=TimeframeGOTResult(
                unified_trend="neutral",
                unified_score=-0.05,
                contradiction_type="pullback_in_uptrend",
                timeframe_confidence=0.75,
                llm_reasoning="短期回调但长期多头，属趋势内修正",
            ),
            regime_metadata=RegimeMetadata(
                adx_value=22.5,
                regime="mild_trending",
                volatility_regime="normal",
                weights_used={
                    "ma_alignment": 0.29,
                    "macd": 0.29,
                    "volume_price": 0.24,
                    "bollinger": 0.18,
                },
            ),
        )

        mid_small_cap = StyleSignal(
            trend="bearish",
            score=-0.40,
            signals=TimeframeSignals(
                short_term=Signal(trend="bearish", score=-0.50),
                medium_term=Signal(trend="bearish", score=-0.35),
                long_term=Signal(trend="neutral", score=0.05),
            ),
        )

        large_cap = StyleSignal(
            trend="neutral",
            score=0.10,
            signals=TimeframeSignals(
                short_term=Signal(trend="neutral", score=0.05),
                medium_term=Signal(trend="neutral", score=0.15),
                long_term=Signal(trend="bullish", score=0.30),
            ),
        )

        position_limit = 0.5 * 0.75 * 0.5  # = 0.1875

        return MarketContext(
            tech_growth=tech_growth,
            market_state=MarketStateSignal(
                attack_power=0.45,
                regime="transition",
                mini_got_trend="neutral",
                mini_got_score=0.10,
                mini_got_confidence=0.8,
                crash_override=False,
            ),
            mid_small_cap=mid_small_cap,
            large_cap=large_cap,
            position_limit=round(position_limit, 2),
            confidence=0.65,
            key_levels=KeyLevels(
                short_term={"support": 3180.0, "resistance": 3280.0},
                mid_term={"support": 3100.0, "resistance": 3350.0},
                volume_profile={"poc": 3220.0, "value_area_low": 3180.0, "value_area_high": 3260.0},
            ),
            reasoning="[Mock] 短期偏弱但长期多头格局未破，中小盘承压明显。建议控制仓位。",
            data_quality=DataQuality(
                available_fields=["index_kline", "volume", "ma", "macd"],
                missing_fields=[],
            ),
        )
