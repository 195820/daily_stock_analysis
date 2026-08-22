"""快速验证子串匹配"""
import json, sys
sys.path.insert(0, ".")

from src.services.stock_screener import StockScreenerPipeline
from src.core.config_manager import ConfigManager

config = ConfigManager()
pipeline = StockScreenerPipeline(config)

with open("data/got_daily/20260812/sector.json", "r", encoding="utf-8") as f:
    sector_ctx = json.load(f)
with open("data/got_daily/20260812/news.json", "r", encoding="utf-8") as f:
    news_ctx = json.load(f)

names = pipeline._get_all_sector_names(sector_ctx)
events = news_ctx["major_events"]

# 检查关键子串匹配
test_pairs = [
    ("机器人", "机器人概"),
    ("机器人", "人形机器"),
    ("半导体", "半导体"),
    ("新能源", "新能源车"),
    ("AI", "AIGC概念"),
    ("PCB概念", "PCB概念"),
]
print("=== 子串匹配检查 ===")
for a, board in test_pairs:
    r1 = a in board
    r2 = board in a
    print(f"  '{a}' in '{board}' = {r1}, '{board}' in '{a}' = {r2} => {'MATCH' if r1 or r2 else 'NO MATCH'}")

# 完整降级匹配
fallback = pipeline._fallback_sector_match(events, names)
print(f"\n=== 降级匹配结果 ({len(fallback)} boards) ===")
for board, signal in sorted(fallback.items(), key=lambda x: -x[1]):
    print(f"  {board}: {signal:+.2f}")

# 检查未匹配的affected_sectors
all_affected = set()
for ev in events:
    for s in ev.get("affected_sectors", []):
        all_affected.add(s)
print(f"\n=== 新闻 affected_sectors ({len(all_affected)}) ===")
for s in sorted(all_affected):
    matched = [b for b in names if s in b or b in s]
    print(f"  '{s}' => {matched if matched else 'NO MATCH'}")
