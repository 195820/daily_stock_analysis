"""单独验证消息面评分修复效果（跳过问财筛选）"""
import json, sys, logging
sys.path.insert(0, ".")

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

from src.services.stock_screener import StockScreenerPipeline
from src.core.config_manager import ConfigManager

config = ConfigManager()
pipeline = StockScreenerPipeline(config)

# 加载今天的数据
with open("data/got_daily/20260812/sector.json", "r", encoding="utf-8") as f:
    sector_ctx = json.load(f)
with open("data/got_daily/20260812/news.json", "r", encoding="utf-8") as f:
    news_ctx = json.load(f)

# 构造模拟候选股（使用今天实际板块）
mock_candidates = [
    {"code": "000001", "name": "模拟股A", "sector": "PCB概念"},
    {"code": "000002", "name": "模拟股B", "sector": "光伏"},
    {"code": "000003", "name": "模拟股C", "sector": "机器人概"},
    {"code": "000004", "name": "模拟股D", "sector": "半导体"},
    {"code": "000005", "name": "模拟股E", "sector": "新能源车"},
    {"code": "000006", "name": "模拟股F", "sector": "黄金"},
    {"code": "000007", "name": "模拟股G", "sector": "有色金属"},  # TDX 名
    {"code": "000008", "name": "模拟股H", "sector": "铜缆高速"},
    {"code": "000009", "name": "模拟股I", "sector": "固态电池"},
    {"code": "000010", "name": "模拟股J", "sector": "AIGC概念"},
]

# 调用消息面评分
scores, avail = pipeline._score_news(mock_candidates, news_ctx, sector_ctx)

print("\n=== 消息面评分结果 ===")
print(f"{'股票':>12} {'板块':>10} {'news_score':>10} {'available':>10}")
print("-" * 50)
for c in mock_candidates:
    code = c["code"]
    sector = c["sector"]
    score = scores.get(code, 0)
    available = avail.get(code, False)
    marker = " <-- 有区分度" if score != 40.0 else " <-- 基础分"
    print(f"{c['name']:>12} {sector:>10} {score:>10.1f} {str(available):>10}{marker}")
