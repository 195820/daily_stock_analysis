# -*- coding: utf-8 -*-
"""测试 akshare 实时 API - 检查数据日期和 NaN 情况"""
import akshare as ak
import pandas as pd

print("=== 1. 北向资金分钟级：检查今天数据是否有非 NaN ===")
df = ak.stock_hsgt_fund_min_em(symbol="北向资金")
print(f"columns: {list(df.columns)}")
print(f"shape: {df.shape}")
# 检查最后一列（北向资金）是否有非 NaN 值
last_col = df.columns[-1]
print(f"last col name: {last_col}")
non_null = df[last_col].dropna()
print(f"non-null count: {len(non_null)} / {len(df)}")
if len(non_null) > 0:
    print(f"last 5 non-null:\n{non_null.tail(5)}")
else:
    # 检查其他列
    for col in df.columns:
        non_null_count = df[col].dropna().shape[0]
        print(f"  {col}: {non_null_count} non-null / {len(df)}")
    print("last 5 rows:\n", df.tail(5).to_string())

print("\n=== 2. 概念板块资金流：检查净流入列 ===")
df2 = ak.stock_fund_flow_concept(symbol="即时")
print(f"columns: {list(df2.columns)}")
# 找到净额列
net_col = [c for c in df2.columns if "净额" in str(c)]
print(f"net flow col: {net_col}")
if net_col:
    top5 = df2.head(5)[[df2.columns[1]] + net_col]
    print(f"Top 5:\n{top5.to_string()}")

print("\n=== 3. 个股资金流：最新日期 ===")
df3 = ak.stock_individual_fund_flow(stock="600519", market="sh")
date_col = df3.columns[0]
print(f"latest date: {df3[date_col].iloc[-1]}")
print(f"today check: latest row:\n{df3.tail(1).to_string()}")
