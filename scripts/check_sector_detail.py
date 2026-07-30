"""查看 Node 2 今天实际匹配到的全部板块及得分"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 读取今天的 sector 原始上下文
ctx_path = Path("data/aggregator_contexts") / "sector_context_20260729.json"
if not ctx_path.exists():
    # 尝试从持久化中找
    ctx_path = Path("data/aggregator_contexts/previous_context_20260729.json")

if ctx_path.exists():
    print(f"找到上下文: {ctx_path}")
else:
    print("找不到 sector 上下文文件")

# 直接从归档中查看
archive_path = Path("data/got_daily/20260729/sector.json")
data = json.load(open(archive_path, encoding="utf-8"))

# 收集所有出现在结果中的板块名
seen = set()
for section in ["active_mainline", "exhausted_mainline", "ambush", "defensive_rotation", "top_by_score"]:
    for item in data.get(section, []):
        seen.add(item["name"])

print(f"\n今日进入结果的板块 ({len(seen)} 个):")
for name in sorted(seen):
    print(f"  {name}")

# 检查机器人相关词
robot_kws = ["机器人", "减速器", "丝杠", "伺服", "传感器", "具身智能", "人形", "电机"]
print("\n检查是否有机器人相关板块出现:")
for name in sorted(seen):
    for kw in robot_kws:
        if kw in name:
            print(f"  ✅ {name} (关键词: {kw})")
            break

# 检查今日 top_by_score 之外的板块 - 从 CONCEPT_UNIVERSE 角度
print("\n=== 分析: 为什么汽车整车分数高 ===")
for section in ["active_mainline"]:
    for item in data.get(section, []):
        if "汽车" in item["name"]:
            ind = item["indicators"]
            print(f"  {item['name']} (score={item['strength_score']:.1f})")
            print(f"    量价: {ind['volume_price']:.2f}")
            print(f"    动量: {ind['price_momentum']:.2f}")
            print(f"    宽度: {ind['market_breadth']:.2f}")
            print(f"    相对强度: {ind['relative_strength']:.2f}")
            print(f"    资金流: {ind['capital_flow']:.2f}")
            print(f"    生命周期: {item['lifecycle']['phase']}, 启动后{item['lifecycle']['days_since_launch']}天")
            print(f"    操作建议: {item['recommended_action']}")
