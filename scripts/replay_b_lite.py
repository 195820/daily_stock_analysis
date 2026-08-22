# -*- coding: utf-8 -*-
"""
B-lite 方案回放验证

验证目标：
1. 水运 08-17~08-20 能通过 B-lite 条件
2. "假慢涨"板块被护栏过滤掉
"""

import sys
from pathlib import Path
import pandas as pd

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from data_provider.mootdx_fetcher import MootdxFetcher


def check_b_lite_conditions(kline_df: pd.DataFrame, sector_name: str, target_dates: list) -> dict:
    """
    检查 B-lite 条件：
    - 3日累计涨幅 > 0
    - 5日累计涨幅 > 0
    - 近5日无单日跌幅 > 5%
    - 5日累计涨幅（排序键）
    """
    if kline_df is None or len(kline_df) < 10:
        return {"error": "K线数据不足"}

    kline_df = kline_df.copy()
    kline_df['date'] = pd.to_datetime(kline_df['date']).dt.date
    kline_df['daily_return'] = kline_df['close'].pct_change() * 100

    results = []
    for target_date_str in target_dates:
        from datetime import datetime
        target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()

        # 找到目标日期的位置（整数位置，不是标签）
        mask = kline_df['date'] == target_date
        if not mask.any():
            continue

        idx = mask.values.argmax()  # 第一个 True 的位置

        # 计算 3日、5日累计涨幅
        if idx < 5:
            continue

        close_today = kline_df.iloc[idx]['close']
        close_3d_ago = kline_df.iloc[idx - 3]['close']
        close_5d_ago = kline_df.iloc[idx - 5]['close']

        momentum_3d = (close_today / close_3d_ago - 1) * 100
        momentum_5d = (close_today / close_5d_ago - 1) * 100

        # 检查近5日是否有单日跌幅 > 5%
        last_5_days = kline_df.iloc[idx-4:idx+1]['daily_return']
        has_crash = (last_5_days < -5).any()

        # B-lite 条件
        pass_3d = momentum_3d > 0
        pass_5d = momentum_5d > 0
        pass_no_crash = not has_crash

        pass_all = pass_3d and pass_5d and pass_no_crash

        results.append({
            'date': target_date_str,
            'momentum_3d': momentum_3d,
            'momentum_5d': momentum_5d,
            'has_crash': has_crash,
            'pass_3d': pass_3d,
            'pass_5d': pass_5d,
            'pass_no_crash': pass_no_crash,
            'pass_all': pass_all,
        })

    return {
        'sector': sector_name,
        'results': results,
    }


def main():
    fetcher = MootdxFetcher()

    # 测试板块列表
    test_sectors = [
        # 真慢涨：水运
        ('880461', '水运', ['2026-08-17', '2026-08-18', '2026-08-19', '2026-08-20']),

        # 假慢涨示例（需要找几个实际暴跌后反弹的板块）
        # 这里先用几个常见板块做示例
        ('880491', '半导体', ['2026-08-17', '2026-08-18', '2026-08-19', '2026-08-20']),
        ('880952', '芯片', ['2026-08-17', '2026-08-18', '2026-08-19', '2026-08-20']),
    ]

    print("=" * 80)
    print("B-lite 方案回放验证")
    print("=" * 80)
    print()

    for code, name, dates in test_sectors:
        print(f"板块: {name} ({code})")
        print("-" * 80)

        # 获取30天K线（足够计算5日累计 + 回放4天）
        kline = fetcher.get_board_kline(code, days=30)
        result = check_b_lite_conditions(kline, name, dates)

        if 'error' in result:
            print(f"  错误: {result['error']}")
            print()
            continue

        print(f"  {'日期':<12} | {'3日涨幅':>8} | {'5日涨幅':>8} | {'有暴跌':>6} | {'3d>0':>5} | {'5d>0':>5} | {'无暴跌':>6} | {'通过':>4}")
        print("  " + "-" * 76)

        for r in result['results']:
            print(f"  {r['date']:<12} | {r['momentum_3d']:>+7.2f}% | {r['momentum_5d']:>+7.2f}% | "
                  f"{'是' if r['has_crash'] else '否':>6} | "
                  f"{'Y' if r['pass_3d'] else 'N':>5} | "
                  f"{'Y' if r['pass_5d'] else 'N':>5} | "
                  f"{'Y' if r['pass_no_crash'] else 'N':>6} | "
                  f"{'Y' if r['pass_all'] else 'N':>4}")

        # 统计
        pass_count = sum(1 for r in result['results'] if r['pass_all'])
        total_count = len(result['results'])
        print(f"\n  通过天数: {pass_count}/{total_count}")
        print()

    print("=" * 80)
    print("验证结论")
    print("=" * 80)
    print("1. 水运在 08-17~08-20 应该能通过 B-lite 条件（待确认）")
    print("2. 假慢涨板块应该被'无单日暴跌'护栏过滤（待确认）")


if __name__ == "__main__":
    main()
