"""逐事件追踪降级匹配"""
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

# 检查 "机器人概" 是否在 names 中
print(f"'机器人概' in names: {'机器人概' in names}")
print(f"'人形机器' in names: {'人形机器' in names}")

# 逐事件匹配追踪
sentiment_multiplier = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
for i, ev in enumerate(events):
    affected = ev.get("affected_sectors", [])
    sentiment = ev.get("sentiment", "neutral")
    impact = ev.get("impact_level", "medium")
    base = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(impact, 0.3)
    signal = base * sentiment_multiplier.get(sentiment, 0)

    matched = set()
    for a in affected:
        for board in names:
            if a in board or board in a:
                matched.add(board)

    if matched:
        print(f"Event {i+1} [{impact}/{sentiment}] affected={affected} => {matched} (signal={signal:+.2f})")
