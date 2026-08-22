"""验证消息面匹配修复：板块名集合 + 降级匹配"""
import json
import sys
sys.path.insert(0, ".")

from src.services.stock_screener import StockScreenerPipeline
from src.core.config_manager import ConfigManager

config = ConfigManager()
pipeline = StockScreenerPipeline(config)

with open("data/got_daily/20260812/sector.json", "r", encoding="utf-8") as f:
    sector_ctx = json.load(f)

names = pipeline._get_all_sector_names(sector_ctx)
print(f"板块名集合大小: {len(names)}")

# 验证今天的候选板块
today_boards = [
    "黄金", "BC电池", "铝", "PCB概念", "光伏", "铜缆高速", "锂电池概",
    "有色", "人形机器", "机器人概", "固态电池", "合成生物", "创新药", "短剧游戏",
]
print("\n候选板块覆盖检查:")
for b in today_boards:
    status = "OK" if b in names else "MISS"
    print(f"  {status} {b}")

# 测试降级匹配
with open("data/got_daily/20260812/news.json", "r", encoding="utf-8") as f:
    news_ctx = json.load(f)

events = news_ctx.get("major_events", [])
print(f"\n新闻事件数: {len(events)}")

fallback = pipeline._fallback_sector_match(events, names)
print(f"降级匹配结果: {len(fallback)} 个板块")
for board, signal in sorted(fallback.items(), key=lambda x: -x[1]):
    print(f"  {board}: {signal:+.2f}")
