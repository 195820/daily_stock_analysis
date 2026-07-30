# -*- coding: utf-8 -*-
import os
os.environ['ENABLE_EASTMONEY_PATCH']='true'
import sys
sys.path.insert(0, r'E:\daily_stock_analysis')
from src.patches.eastmoney_patch import eastmoney_patch
eastmoney_patch()
from data_provider.base import DataFetcherManager
import pandas as pd
import numpy as np

mgr = DataFetcherManager()
result = mgr.get_daily_data('300588', days=60)
df = result[0] if isinstance(result, tuple) else result

# 计算技术指标
df['ma5'] = df['close'].rolling(5).mean()
df['ma10'] = df['close'].rolling(10).mean()
df['ma20'] = df['close'].rolling(20).mean()

# RSI
delta = df['close'].diff()
gain = delta.where(delta > 0, 0).rolling(6).mean()
loss = (-delta.where(delta < 0, 0)).rolling(6).mean()
rs = gain / loss
df['rsi6'] = 100 - (100 / (1 + rs))

# MACD
exp12 = df['close'].ewm(span=12).mean()
exp26 = df['close'].ewm(span=26).mean()
df['dif'] = exp12 - exp26
df['dea'] = df['dif'].ewm(span=9).mean()
df['macd'] = 2 * (df['dif'] - df['dea'])

# 布林带
df['boll_mid'] = df['close'].rolling(20).mean()
std = df['close'].rolling(20).std()
df['boll_upper'] = df['boll_mid'] + 2 * std
df['boll_lower'] = df['boll_mid'] - 2 * std

latest = df.iloc[-1]
prev = df.iloc[-2]

print('=' * 60)
print('  300588 熙菱信息 技术分析')
print('=' * 60)

print(f'\n【最新行情】(6月16日)')
print(f'  收盘: {latest["close"]} | 涨跌: {latest["pct_chg"]:.2f}%')
print(f'  最高: {latest["high"]} | 最低: {latest["low"]}')
print(f'  成交量: {latest["volume"]/10000:.0f}万 | 量比: {latest["volume_ratio"]:.2f}')

print(f'\n【均线系统】')
print(f'  MA5:  {latest["ma5"]:.2f} | MA10: {latest["ma10"]:.2f} | MA20: {latest["ma20"]:.2f}')
if latest['ma5'] > latest['ma10'] > latest['ma20']:
    ma_status = '多头排列 ✅'
elif latest['ma5'] < latest['ma10'] < latest['ma20']:
    ma_status = '空头排列 ⚠️'
else:
    ma_status = '交叉整理'
print(f'  状态: {ma_status}')
print(f'  价格偏离MA5: {(latest["close"]/latest["ma5"]-1)*100:.1f}%')

print(f'\n【RSI指标】')
rsi_val = latest["rsi6"]
print(f'  RSI(6):  {rsi_val:.1f}', end='')
if rsi_val > 80:
    print(' (超买区 ⚠️)')
elif rsi_val > 70:
    print(' (强势区)')
elif rsi_val < 30:
    print(' (超卖区 💡)')
else:
    print(' (中性)')

print(f'\n【MACD指标】')
print(f'  DIF: {latest["dif"]:.3f} | DEA: {latest["dea"]:.3f} | MACD柱: {latest["macd"]:.3f}')
if latest['dif'] > latest['dea'] and prev['dif'] <= prev['dea']:
    print('  状态: 金叉 ✅')
elif latest['dif'] < latest['dea'] and prev['dif'] >= prev['dea']:
    print('  状态: 死叉 ⚠️')
elif latest['dif'] > latest['dea']:
    print('  状态: 多头运行')
else:
    print('  状态: 空头运行')

print(f'\n【布林带】')
print(f'  上轨: {latest["boll_upper"]:.2f} | 中轨: {latest["boll_mid"]:.2f} | 下轨: {latest["boll_lower"]:.2f}')
boll_width = latest['boll_upper'] - latest['boll_lower']
boll_pos = (latest['close'] - latest['boll_lower']) / boll_width if boll_width > 0 else 0.5
print(f'  位置: {boll_pos:.1%}', end='')
if boll_pos > 1.0:
    print(' (突破上轨 ⚠️)')
elif boll_pos > 0.9:
    print(' (触及上轨 ⚠️)')
elif boll_pos < 0.1:
    print(' (触及下轨 💡)')
else:
    print('')

# 近5日走势
print(f'\n【近5日走势】')
for i in range(-5, 0):
    row = df.iloc[i]
    chg = '🔴' if row['pct_chg'] < 0 else '🟢'
    date_str = str(row['date'])[:10]
    print(f'  {date_str} | {row["close"]:7.2f} | {chg}{row["pct_chg"]:+6.2f}% | 量:{row["volume"]/10000:6.0f}万')

# 关键价位
print(f'\n【关键价位】')
high_20 = df['high'].tail(20).max()
low_20 = df['low'].tail(20).min()
print(f'  20日最高: {high_20} | 20日最低: {low_20}')
print(f'  短期压力位: {latest["high"]} (今日高点)')
print(f'  短期支撑位: {latest["ma5"]:.2f} (MA5)')
print(f'  强支撑位: {low_20}')

# 综合判断
print(f'\n【综合判断】')
signals = []
if latest['ma5'] > latest['ma10']:
    signals.append('短期均线金叉')
if latest['dif'] > latest['dea']:
    signals.append('MACD多头')
if rsi_val > 70:
    signals.append('RSI强势')
elif rsi_val > 50:
    signals.append('RSI偏强')

print(f'  多头信号: {", ".join(signals) if signals else "无"}')

# 风险提示
print(f'\n【风险提示】')
risks = []
if (latest['close'] / latest['ma5'] - 1) * 100 > 10:
    risks.append('价格偏离MA5过大，短线有回调压力')
if rsi_val > 75:
    risks.append('RSI进入超买区')
if boll_pos > 0.95:
    risks.append('触及布林带上轨')
if latest['volume_ratio'] > 3:
    risks.append('成交量异常放大，注意冲高回落')

print(f'  {chr(10).join(risks) if risks else "暂无明显风险"}')
