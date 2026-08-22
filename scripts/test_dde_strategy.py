# -*- coding: utf-8 -*-
"""
测试主线/埋伏板块 DDE 筛选策略
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.stock_screener import StockScreenerPipeline
from src.config import Config


def main():
    config = Config()
    screener = StockScreenerPipeline(config)

    print("=" * 60)
    print("测试主线板块 DDE 筛选（要求当天 DDE >= 0.8%）")
    print("=" * 60)

    # 测试主线板块
    mainline_sector = "人工智能"
    print(f"\n板块: {mainline_sector}")
    mainline_stocks = screener._get_sector_constituents_filtered(mainline_sector, "active_mainline")
    print(f"获取到 {len(mainline_stocks)} 只候选:")
    for s in mainline_stocks[:3]:
        print(f"  {s['code']} {s['name']}: price={s['price']:.2f}, DDE={s['dde_net_pct']:.2f}%, chg={s['change_pct']:.2f}%")

    print("\n" + "=" * 60)
    print("测试埋伏板块 DDE 筛选（要求 DDE 连续 3 天为正）")
    print("=" * 60)

    # 测试埋伏板块
    ambush_sector = "证券"
    print(f"\n板块: {ambush_sector}")
    ambush_stocks = screener._get_sector_constituents_filtered(ambush_sector, "ambush")
    print(f"获取到 {len(ambush_stocks)} 只候选:")
    for s in ambush_stocks[:3]:
        print(f"  {s['code']} {s['name']}: price={s['price']:.2f}, DDE={s['dde_net_pct']:.2f}%, chg={s['change_pct']:.2f}%")

    print("\n" + "=" * 60)
    print("对比总结")
    print("=" * 60)
    print(f"主线板块 ({mainline_sector}): {len(mainline_stocks)} 只")
    print(f"埋伏板块 ({ambush_sector}): {len(ambush_stocks)} 只")

    if mainline_stocks and ambush_stocks:
        print("\n主线板块平均 DDE:", sum(s['dde_net_pct'] for s in mainline_stocks) / len(mainline_stocks))
        print("埋伏板块平均 DDE:", sum(s['dde_net_pct'] for s in ambush_stocks) / len(ambush_stocks))


if __name__ == "__main__":
    main()
