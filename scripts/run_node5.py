"""测试 Node 5 情绪面分析节点（真实数据）"""
import sys
import os
import pathlib

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

sys.stdout.reconfigure(encoding='utf-8')

import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def main():
    # 反爬补丁预加载
    try:
        from src.patches.eastmoney_patch import eastmoney_patch
        eastmoney_patch()
    except Exception:
        pass

    from src.got.nodes.sentiment_analysis import SentimentAnalysisNode

    node = SentimentAnalysisNode(use_mock=False)

    try:
        result = await node.run()

        print("\n" + "=" * 60)
        print("Node 5 执行结果:")
        print(f"  status: {result.status}")
        print(f"  overall_sentiment: {result.overall_sentiment}")
        print(f"  sentiment_score: {result.sentiment_score:.4f}")
        print(f"  sentiment_stage: {result.sentiment_stage}")
        print(f"  trading_signal: {result.trading_signal}")
        print(f"  signal_confidence: {result.signal_confidence}")
        print(f"  forum_mood: {result.forum_mood}")
        print(f"  market_temperature: {result.market_temperature:.2f}")
        print(f"  retail_attention: {result.retail_attention_count}")
        print(f"  news_drift: {result.news_drift}")
        print(f"\n  hot_stocks ({len(result.hot_stocks)}只):")
        for i, s in enumerate(result.hot_stocks[:10], 1):
            print(f"    {i}. {s.stock_code} {s.stock_name} (attention={s.mention_count})")
        print(f"\n  hot_topics: {result.hot_topics}")
        print(f"\n  reasoning: {(result.reasoning or '')[:300]}")
        print(f"\n  data_quality.available: {result.data_quality.available_fields}")
        print(f"  data_quality.missing: {result.data_quality.missing_fields}")
        print(f"  data_quality.failed: {result.data_quality.fetch_failed_fields}")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()


asyncio.run(main())
