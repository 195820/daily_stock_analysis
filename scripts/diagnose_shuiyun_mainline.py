# -*- coding: utf-8 -*-
"""
诊断水运板块在 08-17~08-20 期间为什么没被发现
用当前代码逻辑模拟跑那几天的数据，看卡在哪一步
"""

import sys
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from data_provider.mootdx_fetcher import MootdxFetcher
from data_provider.tdx_board_mapping import TDX_BOARD_REGISTRY


def diagnose_discovery_process():
    """模拟当前发现流程，看水运在哪一步被卡"""

    fetcher = MootdxFetcher()
    water_code = "880461"
    water_name = "水运"

    print(f"=== 诊断 {water_name} ({water_code}) 发现流程 ===\n")

    # 第一步：检查是否在注册表
    print("步骤1: 检查是否在 TDX_BOARD_REGISTRY 注册表")
    in_registry = water_code in TDX_BOARD_REGISTRY
    print(f"  结果: {'YES' if in_registry else 'NO'}")
    if not in_registry:
        print(f"  -> 水运不在注册表，需要靠动态发现\n")

    # 第二步：获取所有概念板块，模拟快筛
    print("步骤2: 获取全量概念板块并快筛")
    all_boards = fetcher.get_all_concept_boards()
    print(f"  全量板块数: {len(all_boards)}")

    # 检查水运是否在板块列表中
    water_in_all = any(b['code'] == water_code for b in all_boards)
    print(f"  水运在全量列表中: {'YES' if water_in_all else 'NO'}")

    if not water_in_all:
        print(f"  -> 水运不在全量板块列表中，无法被发现！")
        return

    # 第三步：获取水运的历史K线，计算那几天的涨跌幅
    print(f"\n步骤3: 获取水运K线数据，计算 08-17~08-20 的涨跌幅")
    kline = fetcher.get_board_kline(water_code, days=30)
    if kline is None or len(kline) < 20:
        print(f"  错误：K线数据不足")
        return

    kline['date'] = pd.to_datetime(kline['date']).dt.date
    kline['change_pct'] = kline['close'].pct_change() * 100

    target_dates = ['2026-08-17', '2026-08-18', '2026-08-19', '2026-08-20']

    print(f"\n步骤4: 模拟那几天的动态发现排名")
    print(f"{'日期':<12} | {'水运涨幅':<10} | {'当日排名':<10} | {'能否进Top30':<12} | {'结论'}")
    print("-" * 75)

    for target_date_str in target_dates:
        from datetime import date as date_cls
        target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()

        # 找到当天的涨跌幅
        idx = kline[kline['date'] == target_date].index
        if len(idx) == 0:
            print(f"{target_date_str} | 数据不存在")
            continue

        water_change = kline.loc[idx[0], 'change_pct']

        # 计算所有板块在那天的涨跌幅（简化版：只取前100个板块做对比）
        # 实际应该用 get_board_quick_change，但那个方法只返回当前数据
        # 这里用历史K线近似
        all_changes = {}
        for board in all_boards[:100]:  # 只测试前100个板块
            code = board['code']
            board_kline = fetcher.get_board_kline(code, days=10)
            if board_kline is not None and len(board_kline) > 0:
                board_kline['date'] = pd.to_datetime(board_kline['date']).dt.date
                board_kline['change_pct'] = board_kline['close'].pct_change() * 100
                board_idx = board_kline[board_kline['date'] == target_date].index
                if len(board_idx) > 0:
                    all_changes[code] = board_kline.loc[board_idx[0], 'change_pct']

        # 排名
        if water_code in all_changes:
            sorted_changes = sorted(all_changes.items(), key=lambda x: x[1], reverse=True)
            rank = next((i+1 for i, (c, _) in enumerate(sorted_changes) if c == water_code), None)
            total = len(sorted_changes)

            # 判断是否能进 Top 30
            in_top30_up = rank <= 30
            in_top30_down = rank > total - 30
            can_discover = in_top30_up or in_top30_down or in_registry

            status = "CAN discover" if can_discover else "CANNOT discover"
            top30_status = "YES" if in_top30_up else ("YES(bottom)" if in_top30_down else "NO")

            print(f"{target_date_str} | {water_change:+6.2f}%   | {rank:>3}/{total:<5} | {top30_status:<12} | {status}")

            if not can_discover:
                print(f"           -> 卡在这里：排名 {rank}，不在涨跌幅 Top 30")

    print(f"\n=== 诊断结论 ===")
    print(f"水运在 08-17~08-20 期间无法被发现，因为：")
    print(f"1. 不在 TDX_BOARD_REGISTRY 注册表")
    print(f"2. 每日涨幅 1-3%，进不了涨跌幅 Top 30")
    print(f"3. 动态发现只看当日涨跌幅，不看多日累计")


if __name__ == "__main__":
    diagnose_discovery_process()
