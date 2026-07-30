"""检查今天板块数据中机器人 vs 汽车的得分对比"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.got.nodes.sector_analysis import CONCEPT_UNIVERSE

# 读取今天的结果
data = json.load(open("data/got_daily/20260729/sector.json", encoding="utf-8"))

# 汇总所有板块
all_boards = []
for section in ["active_mainline", "exhausted_mainline", "ambush", "defensive_rotation", "top_by_score"]:
    for item in data.get(section, []):
        all_boards.append((section, item))

# 找机器人和汽车相关板块
robot_kws = ["机器人", "减速器", "丝杠", "伺服", "传感器", "具身智能", "人形"]
auto_kws = ["汽车", "整车", "零部件"]

print("=== 机器人相关板块 ===")
for sec, b in all_boards:
    name = b["name"]
    if any(kw in name for kw in robot_kws):
        print(f"  [{sec:20s}] {name:12s} score={b['strength_score']:5.1f}  chg={b.get('today_change')}")

print("\n=== 汽车相关板块 ===")
for sec, b in all_boards:
    name = b["name"]
    if any(kw in name for kw in auto_kws):
        print(f"  [{sec:20s}] {name:12s} score={b['strength_score']:5.1f}  chg={b.get('today_change')}")

print("\n=== AI/科技板块 ===")
ai_kws = ["AI", "人工智能", "算力", "芯片", "AIGC", "鸿蒙", "机器人", "智能"]
for sec, b in all_boards:
    name = b["name"]
    if any(kw in name for kw in ai_kws):
        print(f"  [{sec:20s}] {name:12s} score={b['strength_score']:5.1f}  chg={b.get('today_change')}")

# 打印精选宇宙中机器人组匹配到的板块
print("\n=== CONCEPT_UNIVERSE 机器人组关键词 ===")
robot_group = CONCEPT_UNIVERSE.get("robot", {})
for cat, kws in robot_group.items():
    print(f"  {cat}: {kws}")
