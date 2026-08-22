# -*- coding: utf-8 -*-
"""诊断概念板块资金流列映射"""
import akshare as ak
import pandas as pd

df = ak.stock_fund_flow_concept(symbol="即时")
print("=== 列名 (repr) ===")
for i, col in enumerate(df.columns):
    print(f"  [{i}] {repr(col)}")

print("\n=== 前 5 行（原始值）===")
for i in range(min(5, len(df))):
    row = df.iloc[i]
    print(f"\nRow {i}:")
    for col in df.columns:
        print(f"  {repr(col)}: {row[col]}")

# 验证净流入 = 流入 - 流出
print("\n=== 验证 net = in - out ===")
for i in range(3):
    row = df.iloc[i]
    cols = list(df.columns)
    in_val = float(row[cols[4]])
    out_val = float(row[cols[5]])
    col6 = float(row[cols[6]])
    print(f"  {row[cols[1]]}: in={in_val}, out={out_val}, col6={col6}, in-out={in_val-out_val:.2f}")
