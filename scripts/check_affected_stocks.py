# -*- coding: utf-8 -*-
"""检查 Node 4 输出中 affected_stocks 字段"""
import json
import sys
sys.stdout.reconfigure(encoding='utf-8')

d = json.load(open('data/got_daily/20260810/news.json', 'r', encoding='utf-8'))
events = d.get('major_events', [])
print(f"=== Node 4 事件: {len(events)} 个 ===")
for i, e in enumerate(events, 1):
    stocks = e.get('affected_stocks', [])
    sectors = e.get('affected_sectors', [])
    title = e.get('title', '')[:40]
    print(f"{i:2d}. {title}")
    print(f"    sectors: {sectors}")
    print(f"    stocks:  {stocks}")
    print()
