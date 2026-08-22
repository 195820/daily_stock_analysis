"""
GOT 纯规则指标函数

包含：
- ADX 计算（趋势强度）
- MACD 计算 + 阶段分类
- ADX 动态调权
- 波动率状态分类
- 市场宽度计算
- MA 排列评分
- 量价关系评分
- 布林带评分
- 截面排名百分位（cross_sectional_rank）

设计理念：
- 纯函数，无副作用
- 输入 pandas DataFrame（OHLCV），输出数值
- 可独立测试
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# =============================================================================
# 工具函数
# =============================================================================

def clip(value: float, min_val: float, max_val: float) -> float:
    """裁剪到指定范围"""
    return max(min_val, min(max_val, value))


def percentile_rank(value: float, series: pd.Series) -> float:
    """计算值在序列中的百分位排名（0-100）"""
    if len(series) == 0:
        return 50.0
    rank = (series < value).sum() / len(series) * 100
    return rank


def cross_sectional_rank(values: dict[str, float]) -> dict[str, float]:
    """截面排名百分位（0-1），用于多板块横向比较。

    将 dict[name → raw_value] 转为 dict[name → percentile_rank]。
    排名越高 = 值越大。

    优势：
    - 牛市所有板块都涨 → 排名仍能区分强中弱
    - 熊市所有板块都跌 → 排名仍能找到相对强势
    - 不受量纲影响（%、亿元、个数都能统一到 0-1）
    """
    if not values:
        return {}
    sorted_vals = sorted(set(values.values()))
    n = len(sorted_vals)
    if n == 1:
        return {name: 0.5 for name in values}
    rank_map = {val: (i + 1) / n for i, val in enumerate(sorted_vals)}
    return {name: rank_map[val] for name, val in values.items()}


# =============================================================================
# ADX（Average Directional Index）
# =============================================================================

def calculate_adx(df: pd.DataFrame, period: int = 14) -> float:
    """
    计算 ADX（趋势强度指标，0-100）

    分类：
    - ADX > 30: 强趋势
    - 20 < ADX <= 30: 温和趋势
    - ADX <= 20: 震荡
    """
    if len(df) < period * 2:
        logger.warning(f"数据不足，无法计算 ADX（需要 {period * 2} 行，实际 {len(df)} 行）")
        return 25.0

    high = df['high'].values
    low = df['low'].values
    close = df['close'].values
    n = len(high)

    # True Range
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))

    # +DM / -DM
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up_move = high[i] - high[i-1]
        down_move = low[i-1] - low[i]
        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move
        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move

    # Wilder's Smoothing
    atr = np.zeros(n)
    plus_di_smooth = np.zeros(n)
    minus_di_smooth = np.zeros(n)

    # 初始化：前 period 个值用简单求和
    atr[period-1] = np.sum(tr[:period])
    plus_di_smooth[period-1] = np.sum(plus_dm[:period])
    minus_di_smooth[period-1] = np.sum(minus_dm[:period])

    # 后续用 Wilder's 平滑
    for i in range(period, n):
        atr[i] = atr[i-1] - atr[i-1] / period + tr[i]
        plus_di_smooth[i] = plus_di_smooth[i-1] - plus_di_smooth[i-1] / period + plus_dm[i]
        minus_di_smooth[i] = minus_di_smooth[i-1] - minus_di_smooth[i-1] / period + minus_dm[i]

    # 计算 +DI / -DI
    plus_di = np.zeros(n)
    minus_di = np.zeros(n)
    for i in range(period-1, n):
        if atr[i] > 0:
            plus_di[i] = plus_di_smooth[i] / atr[i] * 100
            minus_di[i] = minus_di_smooth[i] / atr[i] * 100

    # 计算 DX
    dx = np.zeros(n)
    for i in range(period-1, n):
        di_sum = plus_di[i] + minus_di[i]
        if di_sum > 0:
            dx[i] = abs(plus_di[i] - minus_di[i]) / di_sum * 100

    # 计算 ADX（DX 的 Wilder 平滑）
    adx = np.zeros(n)
    adx_start = period * 2 - 1
    if adx_start < n:
        adx[adx_start] = np.sum(dx[period-1:adx_start+1]) / period
        for i in range(adx_start + 1, n):
            adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period

    adx_value = adx[-1] if adx[-1] > 0 else 25.0
    return float(clip(adx_value, 0, 100))


# =============================================================================
# MACD 计算 + 阶段分类
# =============================================================================

def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """
    计算 MACD 指标

    返回 DataFrame 增加列：
    - MACD_DIF: 快线
    - MACD_DEA: 慢线（信号线）
    - MACD_BAR: 柱状图 (DIF - DEA) * 2
    """
    df = df.copy()

    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()

    df['MACD_DIF'] = ema_fast - ema_slow
    df['MACD_DEA'] = df['MACD_DIF'].ewm(span=signal, adjust=False).mean()
    df['MACD_BAR'] = (df['MACD_DIF'] - df['MACD_DEA']) * 2

    return df


def classify_macd_phase(df: pd.DataFrame) -> dict:
    """
    MACD 四阶段分类

    区分"刚金叉"与"金叉已运行多日"，为聚合节点提供更精细的信号语义。

    输出：
    - phase: emerging_golden_cross / running_bullish / divergence_warning / death_cross_confirmed / running_bearish
    - since_days: 当前阶段持续天数
    """
    if len(df) < 2:
        return {"phase": "unknown", "since_days": 0}

    df = calculate_macd(df)

    macd_line = df['MACD_DIF']
    signal_line = df['MACD_DEA']
    histogram = df['MACD_BAR']
    close = df['close']

    # 金叉/死叉判定（当前 vs 前一日）
    golden_cross = (macd_line.iloc[-1] > signal_line.iloc[-1] and
                    macd_line.iloc[-2] <= signal_line.iloc[-2])
    death_cross = (macd_line.iloc[-1] < signal_line.iloc[-1] and
                   macd_line.iloc[-2] >= signal_line.iloc[-2])

    if golden_cross:
        return {"phase": "emerging_golden_cross", "since_days": 1}
    elif death_cross:
        return {"phase": "death_cross_confirmed", "since_days": 1}
    elif macd_line.iloc[-1] > signal_line.iloc[-1]:
        # 检查顶背离：价格近 20 日新高，但 MACD 柱未创新高
        if len(close) >= 20:
            price_new_high = close.iloc[-1] >= close.tail(20).max()
            macd_not_new_high = histogram.iloc[-1] < histogram.tail(20).max()
            if price_new_high and macd_not_new_high:
                return {"phase": "divergence_warning", "since_days": 1}

        # 计算当前多头阶段持续天数
        since = _count_days_since_cross(macd_line, signal_line, direction="up")
        return {"phase": "running_bullish", "since_days": since}
    else:
        since = _count_days_since_cross(macd_line, signal_line, direction="down")
        return {"phase": "running_bearish", "since_days": since}


def _count_days_since_cross(macd_line: pd.Series, signal_line: pd.Series, direction: str) -> int:
    """计算从最近一次交叉到当前的天数"""
    if direction == "up":
        # 找最近一次 DIF 从下穿上 DEA
        for i in range(len(macd_line) - 1, 0, -1):
            if macd_line.iloc[i - 1] <= signal_line.iloc[i - 1]:
                return len(macd_line) - i
    else:
        # 找最近一次 DIF 从上穿下 DEA
        for i in range(len(macd_line) - 1, 0, -1):
            if macd_line.iloc[i - 1] >= signal_line.iloc[i - 1]:
                return len(macd_line) - i
    return len(macd_line)


# =============================================================================
# ADX 动态调权
# =============================================================================

def calculate_adaptive_weights(df: pd.DataFrame, adx_value: Optional[float] = None) -> dict:
    """
    根据 ADX 判断趋势/震荡状态，动态调整指标权重

    返回：
    - weights: dict（指标名 -> 权重）
    - regime: str（strong_trending / mild_trending / ranging）
    - adx_value: float
    """
    if adx_value is None:
        adx_value = calculate_adx(df)

    if adx_value > 30:
        regime = "strong_trending"
        weights = {
            "ma_alignment": 0.24,    # 趋势中 MA 已充分反映，降权
            "macd": 0.35,            # 趋势持续性判断最重要
            "volume_price": 0.29,    # 量能验证趋势健康度
            "bollinger": 0.12,
        }
    elif adx_value > 20:
        regime = "mild_trending"
        weights = {
            "ma_alignment": 0.29,
            "macd": 0.29,
            "volume_price": 0.24,
            "bollinger": 0.18,
        }
    else:
        regime = "ranging"
        weights = {
            "ma_alignment": 0.12,    # 震荡市 MA 频繁翻转，大幅降权
            "macd": 0.24,
            "volume_price": 0.24,
            "bollinger": 0.40,       # 布林收口→张口是震荡市最可靠信号
        }

    return {"weights": weights, "regime": regime, "adx_value": adx_value}


# =============================================================================
# 波动率状态分类
# =============================================================================

def classify_volatility_regime(df: pd.DataFrame, lookback: int = 60) -> str:
    """
    波动率状态分类（当前 HV vs 历史百分位）

    返回：
    - extreme_high: 历史波动率 > 80% 百分位
    - extreme_low: 历史波动率 < 20% 百分位
    - normal: 其他
    """
    if len(df) < lookback:
        return "normal"

    returns = np.log(df['close'] / df['close'].shift(1)).dropna()
    if len(returns) < 20:
        return "normal"

    current_hv = returns.tail(20).std() * np.sqrt(252)
    hv_series = returns.rolling(20).std() * np.sqrt(252)
    percentile = percentile_rank(current_hv, hv_series.dropna())

    if percentile > 80:
        return "extreme_high"
    elif percentile < 20:
        return "extreme_low"
    else:
        return "normal"


# =============================================================================
# 技术指标评分
# =============================================================================

def calculate_ma_alignment_score(df: pd.DataFrame, periods: list[int] = None) -> float:
    """
    MA 排列评分

    - 多头排列（MA5>10>20>60>120）→ +1
    - 空头排列（MA5<10<20<60<120）→ -1
    - 混乱纠缠 → 0
    """
    if periods is None:
        periods = [5, 10, 20, 60, 120]

    if len(df) < max(periods):
        return 0.0

    ma_values = []
    for p in periods:
        ma = df['close'].rolling(p).mean().iloc[-1]
        ma_values.append(ma)

    # 判断排列
    bullish_count = sum(1 for i in range(len(ma_values) - 1) if ma_values[i] > ma_values[i + 1])
    bearish_count = sum(1 for i in range(len(ma_values) - 1) if ma_values[i] < ma_values[i + 1])

    total_pairs = len(ma_values) - 1
    if bullish_count == total_pairs:
        return 1.0
    elif bearish_count == total_pairs:
        return -1.0
    else:
        # 部分排列，按比例评分
        return (bullish_count - bearish_count) / total_pairs


def calculate_macd_score(df: pd.DataFrame) -> float:
    """
    MACD 分层评分

    第一层：0轴位置
    第二层：红绿柱动能
    第三层：金叉死叉确认
    """
    if len(df) < 30:
        return 0.0

    df = calculate_macd(df)
    dif = df['MACD_DIF'].iloc[-1]
    dea = df['MACD_DEA'].iloc[-1]
    bar = df['MACD_BAR'].iloc[-1]
    bar_prev = df['MACD_BAR'].iloc[-2]

    score = 0.0

    # 第一层：0轴位置
    if dif > 0 and dea > 0:
        score += 0.3
    elif dif < 0 and dea < 0:
        score -= 0.3

    # 第二层：红绿柱动能
    if bar > 0:
        if bar > bar_prev:
            score += 0.4  # 红柱扩张
        else:
            score += 0.1  # 红柱缩短
    else:
        if bar > bar_prev:
            score -= 0.1  # 绿柱缩短
        else:
            score -= 0.4  # 绿柱扩张

    # 第三层：金叉死叉确认
    dif_prev = df['MACD_DIF'].iloc[-2]
    dea_prev = df['MACD_DEA'].iloc[-2]
    if dif_prev <= dea_prev and dif > dea:
        # 金叉
        if dif > 0:
            score += 0.3  # 0轴上方金叉
        else:
            score += 0.1
    elif dif_prev >= dea_prev and dif < dea:
        # 死叉
        if dif < 0:
            score -= 0.3  # 0轴下方死叉
        else:
            score -= 0.1

    return clip(score, -1.0, 1.0)


def calculate_volume_price_score(df: pd.DataFrame, lookback: int = 5) -> float:
    """
    量价关系评分

    - 放量上涨 / 缩量回调 → +1
    - 放量下跌 / 缩量反弹 → -1
    - 缩量横盘 → 0
    """
    if len(df) < lookback + 1:
        return 0.0

    price_change = df['close'].iloc[-1] - df['close'].iloc[-lookback]
    volume_avg = df['volume'].rolling(lookback).mean().iloc[-1]
    volume_current = df['volume'].iloc[-1]
    volume_ratio = volume_current / volume_avg if volume_avg > 0 else 1.0

    # 价格趋势
    if price_change > 0:
        # 上涨
        if volume_ratio > 1.2:
            return 1.0  # 放量上涨
        else:
            return 0.3  # 缩量上涨
    elif price_change < 0:
        # 下跌
        if volume_ratio > 1.2:
            return -1.0  # 放量下跌
        else:
            return -0.3  # 缩量下跌
    else:
        return 0.0


def calculate_bollinger_score(df: pd.DataFrame, period: int = 20, num_std: float = 2.0) -> float:
    """
    布林带评分

    - 站上中轨 + 张口向上 → +1
    - 跌破中轨 + 张口向下 → -1
    - 收口 → 0
    """
    if len(df) < period:
        return 0.0

    ma = df['close'].rolling(period).mean()
    std = df['close'].rolling(period).std()
    upper = ma + num_std * std
    lower = ma - num_std * std

    current_close = df['close'].iloc[-1]
    current_ma = ma.iloc[-1]
    current_upper = upper.iloc[-1]
    current_lower = lower.iloc[-1]

    # 带宽变化
    bandwidth = (current_upper - current_lower) / current_ma
    prev_bandwidth = (upper.iloc[-5] - lower.iloc[-5]) / ma.iloc[-5] if len(df) >= period + 5 else bandwidth

    # 价格位置
    if current_close > current_ma:
        if bandwidth > prev_bandwidth:
            return 1.0  # 站上中轨 + 张口
        else:
            return 0.3  # 站上中轨但收口
    elif current_close < current_ma:
        if bandwidth > prev_bandwidth:
            return -1.0  # 跌破中轨 + 张口
        else:
            return -0.3  # 跌破中轨但收口
    else:
        return 0.0


# =============================================================================
# Chaikin A/D Line（积累/分配线）
# =============================================================================

def calculate_ad_line(df: pd.DataFrame) -> pd.Series:
    """Chaikin Accumulation/Distribution Line。

    CLV = ((close - low) - (high - close)) / (high - low)
    A/D Line = cumsum(CLV × volume)

    A/D Line 上升 = 资金流入（accumulation）
    A/D Line 下降 = 资金流出（distribution）
    与价格背离时有强预测力（65-72% 准确率，TradeAlgo 2026）。
    """
    high_low_range = (df["high"] - df["low"]).replace(0, 1)
    clv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / high_low_range
    money_flow = clv * df["volume"]
    return money_flow.cumsum()


def ad_line_confirmation(df: pd.DataFrame, lookback: int = 20) -> float:
    """A/D Line 方向确认评分（0~1）。

    量价同向上升 → 高分（突破有资金支撑）
    A/D Line 上升 + 价格横盘 → 中高分（暗中吸筹）
    A/D Line 下降 + 价格上涨 → 低分（分配信号）

    论文依据：A/D Line divergence 预测反转准确率 65-72%
    """
    if len(df) < lookback:
        return 0.5
    ad = calculate_ad_line(df)
    close = df["close"]

    ad_change = ad.iloc[-1] - ad.iloc[-lookback]
    price_change = close.iloc[-1] - close.iloc[-lookback]

    if ad_change > 0 and price_change > 0:
        return 0.9   # 量价齐升
    elif ad_change > 0 and price_change <= 0:
        return 0.7   # 暗中吸筹（bullish divergence）
    elif ad_change <= 0 and price_change > 0:
        return 0.2   # 分配信号（bearish divergence）
    else:
        return 0.3   # 量价齐跌


def ad_distribution_signal(df: pd.DataFrame) -> bool:
    """A/D Line 分配信号检测。

    条件：价格 20d 上涨 > 3% 但 A/D Line 近 10d 下降
    → 机构在价格上升中暗中出货

    论文依据：出现在 72% 的重大下跌（>10%）前，平均提前 18 个交易日
    """
    if len(df) < 20:
        return False
    ad = calculate_ad_line(df)
    close = df["close"]
    price_up_20d = close.iloc[-1] / close.iloc[-20] - 1
    ad_down_10d = ad.iloc[-1] - ad.iloc[-10]
    return bool(price_up_20d > 0.03 and ad_down_10d < 0)


def accumulation_score(df: pd.DataFrame) -> float:
    """机构积累检测 — A/D Line 上升 + 价格横盘。

    返回 0~1 的积累信号强度。
    论文依据：A/D Line 积累信号出现在 68% 的拉升前（TradeAlgo 2026）
    """
    if len(df) < 20:
        return 0.0
    ad = calculate_ad_line(df)
    close = df["close"]

    ad_slope = (
        (ad.iloc[-1] - ad.iloc[-20]) / abs(ad.iloc[-20])
        if abs(ad.iloc[-20]) > 1e-10 else 0.0
    )
    price_vol = close.pct_change().tail(20).std() * np.sqrt(252)
    price_change = abs(close.iloc[-1] / close.iloc[-20] - 1)

    # 积累 = A/D Line 持续上升 + 价格横盘（低波动 + 低变化）
    if ad_slope > 0.05 and price_change < 0.05 and price_vol < 0.30:
        return clip(ad_slope * 5, 0, 1)
    return 0.0


# =============================================================================
# 成交量变异系数（Volume Coefficient of Variation）
# =============================================================================

def volume_vcv(df: pd.DataFrame, lookback: int = 20) -> float:
    """成交量变异系数 — 知情交易代理指标。

    VCV = std(volume) / mean(volume)
    VCV 上升 = 成交量分布不均匀 = 知情资金选择性介入

    论文依据：Boone & White 2015, Kyle 1985 模型扩展
    """
    if len(df) < lookback:
        return 0.5
    vol = df["volume"].tail(lookback)
    mean_vol = vol.mean()
    if mean_vol <= 0:
        return 0.5
    vcv = vol.std() / mean_vol
    # VCV 0.3~1.0 为正常范围，>0.6 暗示知情交易
    return clip((vcv - 0.3) / 0.7, 0, 1)


# =============================================================================
# 布林带宽百分位排名
# =============================================================================

def bb_width_percentile(df: pd.DataFrame, period: int = 20, lookback: int = 252) -> float:
    """布林带宽在当前历史窗口中的百分位排名（0~1）。

    越低 = 越压缩（squeeze）。bottom 10-20th percentile = 真正 squeeze。
    论文依据：Bollinger 1992, squeeze 后突破爆发力最强
    """
    if len(df) < period + 10:
        return 0.5
    close = df["close"]
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    bw = (2 * std / ma).dropna()
    if len(bw) < 10:
        return 0.5
    historical = bw.tail(min(lookback, len(bw)))
    current = bw.iloc[-1]
    pct = (historical < current).mean()
    return float(clip(pct, 0, 1))


# =============================================================================
# 综合指标计算
# =============================================================================

def calculate_all_indicators(df: pd.DataFrame) -> dict:
    """
    计算所有技术指标 + ADX 动态调权

    4 维评分：MA排列 + MACD + 量价 + 布林（已移除 market_breadth，
    因全市场涨跌家数数据源被封。替代方案：Node 1 跨指数一致性检测）

    返回：
    - 各指标评分
    - 加权总分
    - ADX 元信息
    """
    # ADX 动态调权
    adaptive = calculate_adaptive_weights(df)
    weights = adaptive["weights"]
    regime = adaptive["regime"]
    adx_value = adaptive["adx_value"]
    vol_regime = classify_volatility_regime(df)

    # 极端波动率修正：增加布林权重（原 market_breadth 已移除）
    if vol_regime == "extreme_high":
        weights["bollinger"] = min(weights["bollinger"] + 0.10, 0.50)
    elif vol_regime == "extreme_low":
        weights["bollinger"] = min(weights["bollinger"] + 0.10, 0.50)

    # 归一化
    total = sum(weights.values())
    weights = {k: v / total for k, v in weights.items()}

    # 各指标评分
    ma_score = calculate_ma_alignment_score(df)
    macd_score = calculate_macd_score(df)
    macd_phase = classify_macd_phase(df)
    volume_price_score = calculate_volume_price_score(df)
    bollinger_score = calculate_bollinger_score(df)

    # 加权求和（4 维）
    weighted_score = (
        ma_score * weights["ma_alignment"] +
        macd_score * weights["macd"] +
        volume_price_score * weights["volume_price"] +
        bollinger_score * weights["bollinger"]
    )

    return {
        "ma_alignment": ma_score,
        "macd": {
            "score": macd_score,
            "phase": macd_phase["phase"],
            "phase_since_days": macd_phase["since_days"],
        },
        "volume_price": volume_price_score,
        "bollinger": bollinger_score,
        "weighted_score": weighted_score,
        "adx_value": adx_value,
        "regime": regime,
        "volatility_regime": vol_regime,
        "weights_used": weights,
    }
