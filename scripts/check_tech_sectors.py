"""检查科技板块的分类状态"""
import json

d = json.load(open("data/got_daily/20260729/sector.json", encoding="utf-8"))

tech_keywords = ["光模块", "存储", "半导体", "芯片", "算力", "AI", "人工智能", "CPO"]

print("=== 科技板块分类 ===")
for section_name in ["active_mainline", "exhausted_mainline", "ambush", "defensive_rotation"]:
    section = d.get(section_name, [])
    for item in section:
        name = item.get("name", "")
        if any(kw in name for kw in tech_keywords):
            ind = item.get("indicators", {})
            launch = item.get("launch_signal") or {}
            overheat = item.get("overheat_signals") or {}
            print(f"\n{name} [{section_name}]:")
            print(f"  强度: {item['strength_score']:.1f}")
            print(f"  启动信号: {launch.get('level', 'N/A')} ({launch.get('score', 0):.3f})")
            print(f"  过热信号: {overheat}")
            print(f"  量价: {ind.get('volume_price', 0):.3f}")
            print(f"  动量: {ind.get('price_momentum', 0):.3f}")
            print(f"  宽度: {ind.get('market_breadth', 0):.3f}")

# 也检查 top_by_score
print("\n=== Top by Score (未分类) ===")
for item in d.get("top_by_score", []):
    name = item.get("name", "")
    if any(kw in name for kw in tech_keywords):
        print(f"{name}: {item['strength_score']:.1f}分, state={item.get('state', 'N/A')}")
