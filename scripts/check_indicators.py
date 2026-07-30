"""检查主线板块的详细指标"""
import json

d = json.load(open("data/got_daily/20260729/sector.json", encoding="utf-8"))

print("=== 主线板块详细指标 ===")
for item in d.get("active_mainline", []):
    ind = item.get("indicators", {})
    launch = item.get("launch_signal") or {}
    print(f"\n{item['name']}:")
    print(f"  strength_score: {item['strength_score']:.1f}")
    print(f"  launch_level: {launch.get('level')}, score: {launch.get('score'):.3f}")
    print(f"  volume_price: {ind.get('volume_price', 0):.3f}")
    print(f"  market_breadth: {ind.get('market_breadth', 0):.3f}")
    print(f"  P3条件: score>70={item['strength_score']>70}, vp>0.5={ind.get('volume_price',0)>0.5}, breadth>0.7={ind.get('market_breadth',0)>0.7}")
    print(f"  P3结果: {item['strength_score']>70 and ind.get('volume_price',0)>0.5 and ind.get('market_breadth',0)>0.7}")
