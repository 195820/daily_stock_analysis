"""策略对比回测：DDE策略 vs 隔夜反转策略 vs 涨幅区间策略

基于历史推荐股票池，回测三种选股逻辑的次日(T+1)胜率与收益：
A. DDE 策略（现状）：昨日 DDE 大单净量 >= 0.8%（模拟：放量大涨 = 大单流入）
B. 隔夜反转策略（学术推荐）：连续"高开回落"（正隔夜 + 负日内）> 某频率
C. 涨幅区间策略（实操推荐）：放量且涨幅 2.5%-6.5%，且站上均线多头

用代理指标（无真实 DDE/分钟数据），做相对对比。
"""
import sys, os, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from data_provider.base import DataFetcherManager

manager = DataFetcherManager()

UNIVERSE = [
    # 历史 Phase 2 推荐过的所有股票
    "603166", "600327", "300895", "002212", "600892", "300051",
    "002421", "600216", "002792", "603629", "000620", "300603",
    "603989", "600522", "002658", "002741", "688195", "603773",
    "301266", "605399", "002365", "000716", "600459", "600267",
    # 一些大盘蓝筹对照
    "600519", "600036", "601318", "000858", "300750",
    # 一些活跃题材
    "002129", "300576", "603260", "603806", "001229",
]

LOOKBACK = 120
HOLD = 1  # T+1

print(f"股票池: {len(UNIVERSE)} 只 × 回溯 {LOOKBACK} 天\n")

# ── 采集所有股票 K 线 ──
frames = []
for code in UNIVERSE:
    try:
        r = manager.get_daily_data(code, days=LOOKBACK + 30)
        df = r[0] if isinstance(r, tuple) else r
        if df is None or len(df) < 40:
            continue
        df = df.reset_index(drop=True)
        df["code"] = code
        frames.append(df)
    except Exception:
        continue

all_df = pd.concat(frames, ignore_index=True)
print(f"采集完成: {len(all_df)} 行 × {all_df['code'].nunique()} 只股票\n")

# ── 统一列名并计算指标 ──
df = all_df.copy()
df["date"] = df["date"].astype(str).str[:10]
df["close"] = df["close"].astype(float)
df["open"] = df["open"].astype(float)
df["high"] = df["high"].astype(float)
df["low"] = df["low"].astype(float)
df["volume"] = df["volume"].astype(float)

df = df.sort_values(["code", "date"]).reset_index(drop=True)

df["ret_1d"] = df.groupby("code")["close"].pct_change(1)
df["ret_2d"] = df.groupby("code")["close"].pct_change(2)
df["ret_3d"] = df.groupby("code")["close"].pct_change(3)
df["ret_5d"] = df.groupby("code")["close"].pct_change(5)

# 隔夜收益（open vs prev close）与日内收益（close vs open）
df["prev_close"] = df.groupby("code")["close"].shift(1)
df["overnight"] = df["open"] / df["prev_close"] - 1
df["intraday"] = df["close"] / df["open"] - 1

# 成交量
df["vol_ma5"] = df.groupby("code")["volume"].transform(lambda s: s.rolling(5).mean())
df["vol_ratio"] = df["volume"] / (df["vol_ma5"] + 1e-9)

# 均线
df["ma5"] = df.groupby("code")["close"].transform(lambda s: s.rolling(5).mean())
df["ma10"] = df.groupby("code")["close"].transform(lambda s: s.rolling(10).mean())
df["ma20"] = df.groupby("code")["close"].transform(lambda s: s.rolling(20).mean())
df["ma_bull"] = (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"])

# T+1 收益（target）
df["fwd_1d"] = df.groupby("code")["close"].pct_change(-1)

# 隔夜反转历史频率（过去10天中，高开回落的天数）
df["gap_up"] = (df["overnight"] > 0.005).astype(int)
df["gap_down_day"] = ((df["overnight"] > 0.005) & (df["intraday"] < -0.005)).astype(int)
df["gap_up_count_10"] = df.groupby("code")["gap_up"].transform(lambda s: s.rolling(10).sum())
df["gapdown_count_10"] = df.groupby("code")["gap_down_day"].transform(lambda s: s.rolling(10).sum())

# DDE 代理：放量大涨（大单流入近似）
df["ddeproxy"] = (df["ret_1d"] > 0.008) & (df["vol_ratio"] > 1.2)

# 有效样本（跳过前 30 行预热）
valid = df[df["ret_5d"].notna() & df["fwd_1d"].notna() & df["vol_ma5"].notna()].copy()
valid = valid[valid["date"] >= "2026-01-01"]  # 只用近期数据

# 去掉未来数据：fwd_1d 是 T+1 收益，用于标签；信号用 T 及之前数据
print(f"有效样本: {len(valid)} 行（2026年至今）")

# ── 定义五种策略的信号 ──
def sig_dde(row):
    """现状：昨日放量大涨(近似DDE)"""
    return row["ddeproxy"]

def sig_ranged(row):
    """实操：放量 2.5%-6.5% 涨幅 + 均线多头"""
    in_range = (row["ret_1d"] > 0.025) and (row["ret_1d"] < 0.065)
    return in_range and (row["vol_ratio"] > 1.5) and row["ma_bull"]

def sig_overnight(row):
    """学术：近期高开回落频率高（过去10天>3次）"""
    return row["gapdown_count_10"] >= 3 and row["overnight"] > 0

def sig_ov_mild(row):
    """学术温和：近期有高开回落且当前未追高(涨幅<6.5%)"""
    return (row["gapdown_count_10"] >= 2) and (row["ret_1d"] < 0.065)

def sig_dde_ranged(row):
    """改良DDE：放量大涨但涨幅受限(2.5-6.5%)，避免追高"""
    return row["ddeproxy"] and (row["ret_1d"] < 0.065) and (row["ret_1d"] > 0.025)

strategies = {
    "A. DDE(现状)放量大涨": sig_dde,
    "B. 涨幅区间+多头": sig_ranged,
    "C. 隔夜反转频率": sig_overnight,
    "D. 隔夜反转温和": sig_ov_mild,
    "E. DDE+涨幅限制": sig_dde_ranged,
}

print(f"\n{'='*90}")
print("策略对比回测（信号次日收益，T+1）")
print(f"{'='*90}")
print(f"{'策略':<28} {'信号数':>7} {'胜率':>7} {'均值':>8} {'中位':>8} {'最佳':>8} {'最差':>8}")
print("-" * 90)

results_summary = {}
for name, sig_fn in strategies.items():
    signal = valid.apply(sig_fn, axis=1)
    s = valid[signal]
    if len(s) < 5:
        print(f"{name:<28} {len(s):>7} 样本过少")
        results_summary[name] = {"count": len(s), "win": None, "mean": None, "median": None, "best": None, "worst": None}
        continue
    fwd = s["fwd_1d"] * 100
    win = (fwd > 0).mean()
    mean = fwd.mean()
    med = fwd.median()
    best = fwd.max()
    worst = fwd.min()
    results_summary[name] = {"count": len(s), "win": win, "mean": mean, "median": med, "best": best, "worst": worst}
    print(f"{name:<28} {len(s):>7} {win*100:>6.1f}% {mean:>+7.2f}% {med:>+7.2f}% {best:>+7.2f}% {worst:>+7.2f}%")

# ── 混合信号组合 ──
print(f"\n{'='*90}")
print("组合信号测试（两个信号叠加）")
print(f"{'='*90}")

combos = {
    "B+D: 涨幅区间+隔夜反转": lambda r: sig_ranged(r) and sig_ov_mild(r),
    "B+C: 涨幅区间+高频反转": lambda r: sig_ranged(r) and sig_overnight(r),
    "E+D: DDE受限+隔夜反转": lambda r: sig_dde_ranged(r) and sig_ov_mild(r),
}
for name, combo_fn in combos.items():
    signal = valid.apply(combo_fn, axis=1)
    s = valid[signal]
    if len(s) < 5:
        print(f"{name:<28} {len(s):>7} 样本过少")
        continue
    fwd = s["fwd_1d"] * 100
    win = (fwd > 0).mean()
    mean = fwd.mean()
    print(f"{name:<28} {len(s):>7} {win*100:>6.1f}% {mean:>+7.2f}%")

# ── 对照：随机买入基准 ──
print(f"\n{'='*90}")
print("对照基准：全市场随机买入 T+1")
print(f"{'='*90}")
fwd_all = valid["fwd_1d"].dropna() * 100
win_all = (fwd_all > 0).mean()
print(f"  全样本 {len(fwd_all)} 次 | 胜率 {win_all*100:.1f}% | 均值 {fwd_all.mean():+.2f}%")

# ── 结论 ──
print(f"\n{'='*90}")
print("结论")
print(f"{'='*90}")
best_strat = max(results_summary.items(), key=lambda x: x[1]["mean"] if x[1]["mean"] is not None else -999)
print(f" 最优策略: {best_strat[0]} (均值 {best_strat[1]['mean']:+.2f}%, 胜率 {best_strat[1]['win']*100:.0f}%)")
print(f" 基准:     全样本 (均值 {fwd_all.mean():+.2f}%, 胜率 {win_all*100:.0f}%)")
