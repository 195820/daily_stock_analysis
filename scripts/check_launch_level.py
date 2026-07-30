"""检查主线板块的启动信号等级"""
import json

d = json.load(open("data/got_daily/20260729/sector.json", encoding="utf-8"))

print("=== 主线板块启动信号 ===")
for item in d.get("active_mainline", []):
    launch = item.get("launch_signal") or {}
    print(f"{item['name']:12s} level={launch.get('level','N/A'):15s} score={launch.get('score',0):.3f}")
