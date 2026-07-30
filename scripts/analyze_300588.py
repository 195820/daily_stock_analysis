# -*- coding: utf-8 -*-
"""熙菱信息(300588) 个股分析"""
import sys
sys.path.insert(0, r'E:\daily_stock_analysis')

from data_provider.base import DataFetcherManager
from src.services.technical_indicator import TechnicalIndicatorService, TechnicalSignals
from src.services.dde_fetcher import DdeFetcher
from src.config import Config
import pandas as pd
import numpy as np

config = Config()
mgr = DataFetcherManager()
code = '300588'

print("=" * 60)
print(f"  {code} 熙菱信息 综合分析")
print("=" * 60)

# 1. 获取K线数据
df = mgr.get_daily_data(code, days=60)
print("\n【近10日行情】")
print(df[['date','open','high','low','close','volume','pct_chg']].tail(10).to_string(index=False))

# 2. 实时行情
q = mgr.get_realtime_quote(code)
print("\n【实时行情】")
print(f"  价格: {q.price} | 涨跌: {q.pct_chg}%")
print(f"  量比: {getattr(q, 'volume_ratio', 'N/A')} | 换手率: {getattr(q, 'turnover_rate', 'N/A')}%")
print(f"  PE(动): {getattr(q, 'pe_ratio', 'N/A')} | 总市值: {getattr(q, 'total_mv', 'N/A')}亿")

# 3. 技术指标
if df is not None and len(df) > 20:
    df_calc = df.copy()
    df_calc['ma5'] = df_calc['close'].rolling(5).mean()
    df_calc['ma10'] = df_calc['close'].rolling(10).mean()
    df_calc['ma20'] = df_calc['close'].rolling(20).mean()

    # RSI
    delta = df_calc['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(6).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(6).mean()
    rs = gain / loss
    df_calc['rsi6'] = 100 - (100 / (1 + rs))

    # MACD
    exp12 = df_calc['close'].ewm(span=12).mean()
    exp26 = df_calc['close'].ewm(span=26).mean()
    df_calc['dif'] = exp12 - exp26
    df_calc['dea'] = df_calc['dif'].ewm(span=9).mean()
    df_calc['macd'] = 2 * (df_calc['dif'] - df_calc['dea'])

    # 布林带
    df_calc['boll_mid'] = df_calc['close'].rolling(20).mean()
    std = df_calc['close'].rolling(20).std()
    df_calc['boll_upper'] = df_calc['boll_mid'] + 2 * std
    df_calc['boll_lower'] = df_calc['boll_mid'] - 2 * std

    latest = df_calc.iloc[-1]
    print("\n【技术指标】")
    print(f"  MA5:  {latest['ma5']:.2f} | MA10: {latest['ma10']:.2f} | MA20: {latest['ma20']:.2f}")

    # 均线排列
    if latest['ma5'] > latest['ma10'] > latest['ma20']:
        ma_status = "多头排列 ✅"
    elif latest['ma5'] < latest['ma10'] < latest['ma20']:
        ma_status = "空头排列 ⚠️"
    else:
        ma_status = "交叉整理"
    print(f"  均线状态: {ma_status}")

    print(f"  RSI(6): {latest['rsi6']:.1f}", end="")
    if latest['rsi6'] > 80:
        print(" (超买 ⚠️)")
    elif latest['rsi6'] < 30:
        print(" (超卖 💡)")
    elif latest['rsi6'] > 60:
        print(" (强势)")
    else:
        print()

    print(f"  MACD: DIF={latest['dif']:.3f} DEA={latest['dea']:.3f} 柱={latest['macd']:.3f}", end="")
    if latest['dif'] > latest['dea'] and df_calc.iloc[-2]['dif'] <= df_calc.iloc[-2]['dea']:
        print(" (金叉 ✅)")
    elif latest['dif'] < latest['dea'] and df_calc.iloc[-2]['dif'] >= df_calc.iloc[-2]['dea']:
        print(" (死叉 ⚠️)")
    elif latest['dif'] > latest['dea']:
        print(" (多头)")
    else:
        print(" (空头)")

    # 布林带位置
    boll_width = latest['boll_upper'] - latest['boll_lower']
    boll_pos = (latest['close'] - latest['boll_lower']) / boll_width if boll_width > 0 else 0.5
    print(f"  布林带: 上轨{latest['boll_upper']:.2f} 中轨{latest['boll_mid']:.2f} 下轨{latest['boll_lower']:.2f}")
    print(f"  布林位置: {boll_pos:.1%}", end="")
    if boll_pos > 0.9:
        print(" (触及上轨 ⚠️)")
    elif boll_pos < 0.1:
        print(" (触及下轨 💡)")
    else:
        print()

    # 近5日涨跌
    pct_5d = (latest['close'] / df_calc.iloc[-6]['close'] - 1) * 100
    print(f"\n  近5日涨跌: {pct_5d:.2f}%")

    # 成交量变化
    vol_5 = df_calc['volume'].tail(5).mean()
    vol_20 = df_calc['volume'].tail(20).mean()
    vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1
    print(f"  5日均量/20日均量: {vol_ratio:.2f}x")

# 4. 关键价位
print("\n【关键价位】")
if df is not None and len(df) >= 20:
    high_20 = df['high'].tail(20).max()
    low_20 = df['low'].tail(20).min()
    print(f"  20日最高: {high_20} | 20日最低: {low_20}")
    print(f"  压力位: {high_20} | 支撑位: {low_20}")
