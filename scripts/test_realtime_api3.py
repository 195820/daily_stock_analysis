# -*- coding: utf-8 -*-
"""测试更多实时资金流 API"""
import akshare as ak

print("=== 1. stock_fund_flow_industry(即时) - 行业板块 ===")
try:
    df = ak.stock_fund_flow_industry(symbol="即时")
    print(f"columns: {list(df.columns)}")
    print(f"shape: {df.shape}")
    print(df.head(5).to_string())
except Exception as e:
    print(f"ERROR: {e}")

print("\n=== 2. stock_sector_fund_flow_rank(今日, 行业) ===")
try:
    df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
    print(f"columns: {list(df.columns)}")
    print(f"shape: {df.shape}")
    print(df.head(5).to_string())
except Exception as e:
    print(f"ERROR: {e}")

print("\n=== 3. stock_sector_fund_flow_rank(今日, 概念) ===")
try:
    df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="概念资金流")
    print(f"columns: {list(df.columns)}")
    print(f"shape: {df.shape}")
    print(df.head(5).to_string())
except Exception as e:
    print(f"ERROR: {e}")

print("\n=== 4. stock_hsgt_fund_min_em(沪股通) ===")
try:
    df = ak.stock_hsgt_fund_min_em(symbol="沪股通")
    print(f"columns: {list(df.columns)}")
    non_null = df.iloc[:, -1].dropna()
    non_zero = non_null[non_null != 0]
    print(f"last col non-null: {len(non_null)}, non-zero: {len(non_zero)}")
    if len(non_zero) > 0:
        print(f"last 3 non-zero:\n{non_zero.tail(3)}")
    else:
        print("All zero or NaN — same as daily, 停披")
except Exception as e:
    print(f"ERROR: {e}")

print("\n=== 5. stock_main_fund_flow ===")
try:
    # 全市场主力资金流
    df = ak.stock_main_fund_flow(symbol="今日")
    print(f"columns: {list(df.columns)}")
    print(f"shape: {df.shape}")
    print(df.head(3).to_string())
except Exception as e:
    print(f"ERROR: {e}")
