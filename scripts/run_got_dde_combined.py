# -*- coding: utf-8 -*-
"""
GOT + 问财默认DDE选股联合推荐
使用项目默认的 get_dde_screener() 获取候选，再结合 GOT 输出筛选
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config
from src.services.ths_screener import THSScreenerService
import json
import time


def run_default_dde_with_got():
    config = Config()
    ths = THSScreenerService(config)

    # ── 使用默认 DDE 问财查询 ──
    # 条件：dde大单净量>=0.8% + 低位企稳 + 近2日区间dde>=0.8% + 上市>365天
    print("=" * 60)
    print("问财默认 DDE 选股")
    print("=" * 60)

    t0 = time.time()
    result = ths.get_dde_screener(use_cache=False)
    elapsed = time.time() - t0

    if result.error:
        print(f"错误: {result.error}")
        return

    stocks = result.stocks
    print(f"共 {result.total_count} 只候选, 耗时 {elapsed:.1f}s\n")

    # ── 加载 GOT 输出 ──
    got_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "got_daily", "20260729", "sector.json"
    )

    if not os.path.exists(got_path):
        print(f"GOT 输出不存在: {got_path}")
        print("仅显示问财候选")
        for i, s in enumerate(stocks[:30], 1):
            print(f"  {i:3d}. {s.code} {s.name}")
        return

    with open(got_path, "r", encoding="utf-8") as f:
        sector_data = json.load(f)

    # 提取 GOT 板块方向
    got_directions = {
        "active_mainline": [],
        "exhausted_mainline": [],
        "ambush": [],
        "defensive_rotation": [],
    }

    for key in got_directions:
        for item in sector_data.get(key, []):
            got_directions[key].append({
                "code": item["code"],
                "name": item["name"],
                "strength": item.get("strength_score", 0),
                "action": item.get("recommended_action"),
                "lifecycle": item.get("lifecycle", {}).get("phase") if item.get("lifecycle") else None,
            })

    # ── GOT 板块名称映射（用于匹配问财候选） ──
    # 从 GOT 输出提取所有板块名称
    sector_names = {}
    for key, items in got_directions.items():
        for item in items:
            sector_names[item["code"]] = {
                "name": item["name"],
                "direction": key,
                "strength": item["strength"],
                "action": item["action"],
                "lifecycle": item["lifecycle"],
            }

    print("=" * 60)
    print("GOT 板块方向（今日 2026-07-29）")
    print("=" * 60)
    print(f"  信号: cautious | 仓位: 30%")
    print(f"\n  活跃主线:")
    for item in got_directions["active_mainline"]:
        print(f"    {item['name']} ({item['strength']:.0f}分) - {item['lifecycle'] or 'N/A'} - {item['action'] or 'N/A'}")
    print(f"\n  防守轮动:")
    for item in got_directions["defensive_rotation"]:
        print(f"    {item['name']} ({item['strength']:.0f}分)")
    print(f"\n  埋伏板块:")
    for item in got_directions["ambush"]:
        print(f"    {item['name']} ({item['strength']:.0f}分)")
    print(f"\n  衰竭主线（回避）:")
    for item in got_directions["exhausted_mainline"]:
        print(f"    {item['name']} ({item['strength']:.0f}分)")

    # ── 直接显示问财候选（前30只） ──
    print("\n" + "=" * 60)
    print(f"问财 DDE 候选（共 {len(stocks)} 只，显示前 30）")
    print("=" * 60)
    for i, s in enumerate(stocks[:30], 1):
        print(f"  {i:3d}. {s.code} {s.name}")

    # ── 保存结果供后续分析 ──
    output = {
        "date": "2026-07-29",
        "got_signal": "cautious",
        "got_position": 0.3,
        "ths_query": result.query,
        "ths_total": result.total_count,
        "ths_stocks": [{"code": s.code, "name": s.name, "rank": s.rank} for s in stocks],
        "got_sectors": got_directions,
    }

    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "got_dde_combined_20260729.json"
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {output_path}")


if __name__ == "__main__":
    run_default_dde_with_got()
