# -*- coding: utf-8 -*-
"""
测试 GOT 上下文加载逻辑
不需要完整依赖，只验证 JSON 加载和解析
"""
import json
from pathlib import Path
from datetime import date

def load_got_context(got_date: date) -> dict:
    """加载 GOT 上下文（简化版，用于测试）"""
    date_str = got_date.strftime("%Y%m%d")
    archive_dir = Path(f"data/got_daily/{date_str}")

    if not archive_dir.exists():
        print(f"目录不存在: {archive_dir}")
        return None

    context = {}
    node_files = {
        "market_context": "market.json",
        "sector_context": "sector.json",
        "capital_context": "capital.json",
        "news_context": "news.json",
        "sentiment_context": "sentiment.json",
        "unified_context": "unified.json",
    }

    for key, filename in node_files.items():
        filepath = archive_dir / filename
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                context[key] = json.load(f)
            print(f"[OK] 已加载 {filename}")
        else:
            print(f"[MISS] 缺失 {filename}")

    return context if "unified_context" in context else None

def extract_got_universe(got_context: dict):
    """提取选股池（简化版）"""
    sector_ctx = got_context.get("sector_context", {})

    active_mainlines = sector_ctx.get("active_mainline", [])
    ambush_sectors = sector_ctx.get("ambush", [])

    print(f"\n主线板块: {len(active_mainlines)} 个")
    for s in active_mainlines:
        print(f"  - {s['name']} (强度 {s['strength_score']:.0f})")

    print(f"\n埋伏板块: {len(ambush_sectors)} 个")
    for s in ambush_sectors:
        print(f"  - {s['name']} (强度 {s['strength_score']:.0f})")

    print(f"\n衰竭板块: {len(sector_ctx.get('exhausted_mainline', []))} 个")
    for s in sector_ctx.get("exhausted_mainline", []):
        print(f"  - {s['name']} (建议 {s.get('recommended_action', 'N/A')})")

def extract_capital_signals(got_context: dict):
    """提取资金面信号（简化版）"""
    capital_ctx = got_context.get("capital_context", {})
    evidence = capital_ctx.get("evidence", {})

    print(f"\n资金面信号:")
    print(f"  机构态度: {capital_ctx.get('institutional_attitude')}")
    print(f"  机构评分: {capital_ctx.get('institutional_score'):.3f}")

    dragon_tiger = evidence.get("dragon_tiger", {})
    if dragon_tiger:
        bought = dragon_tiger.get("mainline_stocks_bought", [])
        print(f"  龙虎榜买入: {len(bought)} 只")
        for item in bought[:5]:
            print(f"    - {item}")

if __name__ == "__main__":
    print("=" * 80)
    print("测试 GOT 上下文加载")
    print("=" * 80)

    got_date = date(2026, 7, 29)
    context = load_got_context(got_date)

    if context:
        print("\n" + "=" * 80)
        print("提取板块信息")
        print("=" * 80)
        extract_got_universe(context)

        print("\n" + "=" * 80)
        print("提取资金面信号")
        print("=" * 80)
        extract_capital_signals(context)

        print("\n[OK] 测试通过！数据加载正常")
    else:
        print("\n[FAIL] 测试失败：无法加载 GOT 上下文")
