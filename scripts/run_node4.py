"""测试 Node 4 消息面分析节点（真实数据）"""
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

    from src.got.nodes.news_analysis import NewsAnalysisNode

    node = NewsAnalysisNode(use_mock=False)

    try:
        result = await node.run()

        print("\n" + "=" * 60)
        print("Node 4 执行结果:")
        print(f"  status: {result.status}")
        print(f"  overall_impact: {result.overall_impact}")
        print(f"  impact_score: {result.impact_score:.4f}")
        print(f"  major_events ({len(result.major_events)}条):")
        for i, event in enumerate(result.major_events[:5], 1):
            print(
                f"    {i}. [{event.event_type}/{event.impact_level}/{event.sentiment}] "
                f"sources={event.source_count} type={event.source_type}"
            )
            print(f"       {event.title}")
            print(f"       {event.summary[:100]}")
        print(f"  concept_suggestions ({len(result.concept_suggestions)}条):")
        for c in result.concept_suggestions[:3]:
            print(f"    - {c.get('concept_name', '')}: {c.get('evidence', '')}")
        print(f"  key_risks: {result.key_risks}")
        print(f"  reasoning: {(result.reasoning or '')[:300]}")
        print(f"  data_quality.available: {result.data_quality.available_fields}")
        print(f"  data_quality.missing: {result.data_quality.missing_fields}")
        print(f"  data_quality.failed: {result.data_quality.fetch_failed_fields}")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()


asyncio.run(main())
