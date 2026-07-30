"""检查修复后的板块生命周期"""
import json

d = json.load(open("data/got_daily/20260729/sector.json", encoding="utf-8"))

print("=== 主线板块 ===")
for item in d.get("active_mainline", []):
    lc = item.get("lifecycle") or {}
    days_val = lc.get('days_since_launch', 'N/A')
    days_str = str(days_val) if days_val != 'N/A' else 'N/A'
    print(f"{item['name']:12s} score={item['strength_score']:5.1f} phase={lc.get('phase','N/A'):15s} days={days_str:>3s} action={item.get('recommended_action','N/A')}")

print("\n=== 衰竭板块 ===")
for item in d.get("exhausted_mainline", []):
    lc = item.get("lifecycle") or {}
    print(f"{item['name']:12s} score={item['strength_score']:5.1f} phase={lc.get('phase','N/A'):15s} days={lc.get('days_since_launch','N/A'):3}")

print("\n=== 埋伏板块 ===")
for item in d.get("ambush", []):
    print(f"{item['name']:12s} score={item['strength_score']:5.1f} state={item['state']}")

print("\n=== 防守板块 ===")
for item in d.get("defensive_rotation", []):
    print(f"{item['name']:12s} score={item['strength_score']:5.1f} state={item['state']}")
