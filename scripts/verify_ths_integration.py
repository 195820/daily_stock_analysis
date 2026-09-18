# -*- coding: utf-8 -*-
"""验证同花顺热榜集成到 Node 4"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.got.news_feeds import fetch_ths_hot_topics, fetch_all_news

print("=" * 70)
print("验证 S7 同花顺热榜集成")
print("=" * 70)

# 测试 S7 单独采集
print("\n[S7 单独测试]")
items = fetch_ths_hot_topics()
print(f"采集到 {len(items)} 条热门话题")
for i, it in enumerate(items[:5], 1):
    title = it["title"][:50]
    heat = it.get("heat", 0)
    stocks = ", ".join(s.get("name", "") for s in it.get("related_stocks", [])[:3])
    print(f"  {i}. {title}... | 热度={heat} | 关联: {stocks or '无'}")

# 测试完整采集（包含 S7）
print("\n[完整采集测试]")
merged, stats = fetch_all_news()
print(f"各源统计: {stats}")
print(f"合并后总数: {len(merged)}")

# 检查 S7 是否被包含
ths_count = sum(1 for item in merged if item.get("source_type") == "ths_hot")
print(f"同花顺热榜条目: {ths_count}")

# 检查是否包含"美国拟禁止中国光模块"类话题
print("\n[关键话题检查]")
for item in merged[:20]:
    title = item.get("title", "")
    if any(kw in title for kw in ["禁止", "制裁", "光模块", "美国"]):
        print(f"  ✓ {title[:60]}... | 来源: {item.get('source', '')} | 权重: {item.get('weight', 0):.1f}")

print("\n" + "=" * 70)
print("验证完成")
print("=" * 70)
