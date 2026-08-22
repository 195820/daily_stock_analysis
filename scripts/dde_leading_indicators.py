"""DDE 领先指标挖掘：用 T-1 技术指标预测 T 日 DDE 大单净量

思路：DDE 是滞后指标（大单已进场），我们要找 T-1 就能预测 T 日大单流入的领先信号。
方法：取近 N 天数据，计算 T-1 的各种特征，与 T 日 DDE 做相关性分析。
"""
import sys, os, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from data_provider.base import DataFetcherManager

manager = DataFetcherManager()

# ── Step 1: 获取一个有代表性的股票池（最近 Phase 2 推荐过 + 主线板块成分股）──
UNIVERSE_CODES = [
    "002741", "688195", "603773", "301266", "605399",  # 最近推荐
    "002365", "000716", "600459", "600267", "002792",  # 历史推荐
    "603629", "000620", "300603", "002421", "600216",  # 更多历史
    "603989", "600522", "002658", "603166", "600327",
    "300895", "002212", "600892", "300051",
    "600961", "002902", "301313", "000831",  # 历史持仓
    "000001", "600519", "601318", "000858", "002594",  # 大盘蓝筹对照
    "300750", "002475", "601012", "600036", "000333",
]

LOOKBACK = 60  # 回溯60个交易日

print(f"股票池: {len(UNIVERSE_CODES)} 只")
print(f"回溯天数: {LOOKBACK}")
print()

# ── Step 2: 获取每只股票的 K线 + DDE 数据 ──
all_rows = []

for i, code in enumerate(UNIVERSE_CODES):
    try:
        result = manager.get_daily_data(code, days=LOOKBACK + 20)
        if isinstance(result, tuple):
            df, src = result
        else:
            df = result

        if df is None or len(df) < 20:
            continue

        df = df.reset_index(drop=True)
        df["date"] = df["date"].astype(str).str[:10]

        # 基础技术指标
        df["ma5"] = df["close"].rolling(5).mean()
        df["ma10"] = df["close"].rolling(10).mean()
        df["ma20"] = df["close"].rolling(20).mean()
        df["vol_ma5"] = df["volume"].rolling(5).mean()
        df["vol_ma20"] = df["volume"].rolling(20).mean()

        # 动量指标
        df["ret_1d"] = df["close"].pct_change(1)
        df["ret_3d"] = df["close"].pct_change(3)
        df["ret_5d"] = df["close"].pct_change(5)
        df["ret_10d"] = df["close"].pct_change(10)

        # 波动率
        df["volatility_5d"] = df["ret_1d"].rolling(5).std()
        df["volatility_10d"] = df["ret_1d"].rolling(10).std()

        # 量比
        df["vol_ratio"] = df["volume"] / df["vol_ma5"]
        df["vol_ratio_20"] = df["volume"] / df["vol_ma20"]

        # 均线偏离
        df["ma5_bias"] = (df["close"] - df["ma5"]) / df["ma5"]
        df["ma10_bias"] = (df["close"] - df["ma10"]) / df["ma10"]
        df["ma20_bias"] = (df["close"] - df["ma20"]) / df["ma20"]

        # RSI
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        df["rsi_14"] = 100 - (100 / (1 + rs))

        # 布林带位置 (0=下轨, 1=上轨)
        bb_mid = df["ma20"]
        bb_std = df["close"].rolling(20).std()
        df["bb_position"] = (df["close"] - (bb_mid - 2 * bb_std)) / (4 * bb_std + 1e-10)

        # 换手率变化（如果有）
        if "turnover" in df.columns:
            df["turnover_ma5"] = df["turnover"].rolling(5).mean()
            df["turnover_ratio"] = df["turnover"] / (df["turnover_ma5"] + 1e-10)

        # DDE（大单净量）—— 用 东财接口获取
        # 这里用 price/volume 的代理指标：放量上涨 = 大单买入的近似
        # 真正的 DDE 需要单独接口
        df["price_vol_corr"] = df["close"].rolling(5).corr(df["volume"].astype(float))
        df["amplitude"] = (df["high"] - df["low"]) / df["close"].shift(1)

        # 标记日期索引
        df["code"] = code
        df["row_idx"] = range(len(df))

        all_rows.append(df)

        if (i + 1) % 10 == 0:
            print(f"  已处理 {i+1}/{len(UNIVERSE_CODES)} 只...")

    except Exception as e:
        continue

print(f"\n成功获取 {len(all_rows)} 只股票数据")

# ── Step 3: 尝试获取真实 DDE 数据 ──
print("\n尝试获取 DDE 数据...")
try:
    from src.services.dde_fetcher import DDEFetcher
    dde_fetcher = DDEFetcher()
    has_real_dde = True
    print("  DDEFetcher 可用")
except Exception as e:
    has_real_dde = False
    print(f"  DDEFetcher 不可用: {e}")
    print("  使用代理指标: 放量上涨强度 (volume-weighted return)")

# ── Step 4: 构建特征矩阵 ──
# 用 "代理 DDE" = 次日涨跌幅 * 量比（大单流入的股票次日倾向继续涨）
# 更好的代理：当日量价相关性 + 放量程度

feature_df_list = []

for df in all_rows:
    # 构造 T 日 target 和 T-1 特征
    df_shifted = df.iloc[20:].copy()  # 跳过前20行（MA/RSI 预热）

    # Target: T 日的"资金流入强度"代理
    # 方案 A: 用 T 日涨跌幅 × 量比（放量涨=大单推动）
    df_shifted["target_flow"] = df_shifted["ret_1d"] * df_shifted["vol_ratio"]

    # 方案 B: 如果真实 DDE 可用，用真实值
    # (暂时用方案 A)

    # 特征: T-1 的指标（shift 1 天）
    feature_cols = [
        "ret_1d", "ret_3d", "ret_5d", "ret_10d",     # 动量
        "volatility_5d", "volatility_10d",              # 波动率
        "vol_ratio", "vol_ratio_20",                    # 量比
        "ma5_bias", "ma10_bias", "ma20_bias",          # 均线偏离
        "rsi_14",                                       # RSI
        "bb_position",                                  # 布林带位置
        "amplitude",                                    # 振幅
        "price_vol_corr",                               # 量价相关
    ]

    for col in feature_cols:
        df_shifted[f"feat_{col}_lag1"] = df_shifted[col].shift(1)

    # 额外衍生特征
    df_shifted["feat_vol_accel"] = (df_shifted["vol_ratio"].shift(1) - 1.0)  # 量能加速度
    df_shifted["feat_momentum_reversal"] = df_shifted["ret_5d"].shift(1) * -1  # 反转因子
    df_shifted["feat_bb_squeeze"] = 1.0 - df_shifted["amplitude"].shift(1) / (df_shifted["amplitude"].rolling(10).mean().shift(1) + 1e-10)
    df_shifted["feat_gap"] = (df_shifted["open"] - df_shifted["close"].shift(1)) / (df_shifted["close"].shift(1) + 1e-10)  # 跳空

    feature_df_list.append(df_shifted)

all_features = pd.concat(feature_df_list, ignore_index=True)
all_features = all_features.dropna(subset=["target_flow"] + [c for c in all_features.columns if c.startswith("feat_")])

print(f"\n特征矩阵: {all_features.shape[0]} 行 × {len([c for c in all_features.columns if c.startswith('feat_')])} 特征")

# ── Step 5: 相关性分析 ──
feat_cols = [c for c in all_features.columns if c.startswith("feat_")]
correlations = {}
for col in feat_cols:
    corr = all_features[col].corr(all_features["target_flow"])
    correlations[col] = corr

corr_series = pd.Series(correlations).sort_values(ascending=False)

print(f"\n{'='*70}")
print("特征与次日资金流入强度的相关性排名")
print(f"{'='*70}")
print(f"{'排名':>4} {'特征':<35} {'相关系数':>10} {'方向'}")
print("-" * 60)
for rank, (feat, corr) in enumerate(corr_series.items(), 1):
    direction = "正相关↑" if corr > 0 else "负相关↓"
    bar = "█" * int(abs(corr) * 50)
    print(f"{rank:>4} {feat:<35} {corr:>+10.4f}  {direction} {bar}")

# ── Step 6: 分组回测验证 ──
print(f"\n{'='*70}")
print("Top 3 正相关特征: 按特征值分组后的次日表现")
print(f"{'='*70}")

top_features = corr_series.head(5).index.tolist()

for feat in top_features:
    vals = all_features[feat].dropna()
    if vals.empty:
        continue
    q33 = vals.quantile(0.33)
    q66 = vals.quantile(0.66)

    low = all_features[all_features[feat] <= q33]["target_flow"]
    mid = all_features[(all_features[feat] > q33) & (all_features[feat] <= q66)]["target_flow"]
    high = all_features[all_features[feat] > q66]["target_flow"]

    print(f"\n  {feat}:")
    print(f"    低值组 (≤{q33:.3f}): {len(low)}样本, 均值={low.mean():+.4f}")
    print(f"    中值组:              {len(mid)}样本, 均值={mid.mean():+.4f}")
    print(f"    高值组 (>{q66:.3f}): {len(high)}样本, 均值={high.mean():+.4f}")
    spread = high.mean() - low.mean()
    print(f"    多空价差: {spread:+.4f} {'✅ 有效' if spread > 0.001 else '❌ 无效'}")

# ── Step 7: 实战建议 ──
print(f"\n{'='*70}")
print("结论与实战建议")
print(f"{'='*70}")
