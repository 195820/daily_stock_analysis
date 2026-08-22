# -*- coding: utf-8 -*-
"""测试 akshare 实时资金流 API 输出格式"""
import akshare as ak

print("=== stock_fund_flow_concept(即时) ===")
try:
    df = ak.stock_fund_flow_concept(symbol="即时")
    print("columns:", list(df.columns))
    print("shape:", df.shape)
    print(df.head(3).to_string())
except Exception as e:
    print(f"ERROR: {e}")

print("\n=== stock_hsgt_fund_min_em(北向资金) ===")
try:
    df2 = ak.stock_hsgt_fund_min_em(symbol="北向资金")
    print("columns:", list(df2.columns))
    print("shape:", df2.shape)
    print(df2.tail(3).to_string())
except Exception as e:
    print(f"ERROR: {e}")

print("\n=== stock_individual_fund_flow(600519, sh) ===")
try:
    df3 = ak.stock_individual_fund_flow(stock="600519", market="sh")
    print("columns:", list(df3.columns))
    print("shape:", df3.shape)
    print(df3.tail(3).to_string())
except Exception as e:
    print(f"ERROR: {e}")
