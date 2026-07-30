# -*- coding: utf-8 -*-
"""运行选股流水线"""
import os
import sys
from pathlib import Path

# 确保项目根目录在 path 中
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.config import get_config

def main():
    config = get_config()

    print("=" * 60)
    print("选股流水线配置检查")
    print("=" * 60)
    print(f"SCREENER_ENABLED: {config.screener_enabled}")
    print(f"SCREENER_AUTO_TRADE: {getattr(config, 'screener_auto_trade', False)}")
    print(f"FORUM_SENTIMENT_ENABLED: {getattr(config, 'forum_sentiment_enabled', False)}")
    print(f"SCREENER_TOP_N: {getattr(config, 'screener_top_n', 10)}")
    print(f"SCREENER_MIN_SCORE: {getattr(config, 'screener_min_score', 60)}")
    print("=" * 60)

    # 运行选股
    from src.services.stock_screener import StockScreenerPipeline

    print("\n开始运行选股流水线...")
    screener = StockScreenerPipeline(config)
    result = screener.run()

    if result.error:
        print(f"\n❌ 选股执行失败: {result.error}")
        return 1

    print(f"\n✅ 选股完成!")
    print(f"数据源: {result.data_source}")
    print(f"策略: {result.strategy_name}")
    print(f"扫描: {result.total_scanned} 只")
    print(f"匹配: {result.matched_count} 只")
    print(f"耗时: {result.elapsed_seconds:.1f}s")

    if result.stocks:
        print(f"\n{'='*60}")
        print(f"选股结果 Top {len(result.stocks)}")
        print(f"{'='*60}")
        for i, s in enumerate(result.stocks, 1):
            held_tag = " [已持仓]" if s.portfolio_held else ""
            print(f"{i:2d}. {s.code} {s.name:8s} | "
                  f"综合:{s.score:5.1f} | "
                  f"技术:{s.technical_score:5.1f} "
                  f"板块:{s.sector_score:5.1f} "
                  f"舆情:{s.heat_score:5.1f} "
                  f"风险:{s.risk_score:5.1f} | "
                  f"推荐:{s.recommendation}{held_tag}")
            if s.signal_tags:
                print(f"    信号: {', '.join(s.signal_tags)}")
            if s.ai_reasoning:
                print(f"    AI: {s.ai_reasoning}")
    else:
        print("\n未找到符合条件的股票")

    return 0

if __name__ == "__main__":
    sys.exit(main())
