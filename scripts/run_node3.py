"""测试 Node 3 资金面分析节点（真实数据）"""
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
    from src.got.nodes.market_analysis import MarketAnalysisNode
    from src.got.nodes.sector_analysis import SectorAnalysisNode
    from src.got.nodes.capital_analysis import CapitalAnalysisNode

    # 使用 Node 1 和 Node 2 的 mock 输出作为上下文
    market_node = MarketAnalysisNode(use_mock=True)
    sector_node = SectorAnalysisNode(use_mock=True)

    market_ctx = market_node._mock_output()
    sector_ctx = sector_node._mock_output()

    print("Node 1 mock trend:", market_ctx.tech_growth.trend)
    print("Node 2 mock mainline:", len(sector_ctx.active_mainline))
    print()

    # 测试 Node 3 真实数据
    capital_node = CapitalAnalysisNode(use_mock=False)

    try:
        result = await capital_node.run(
            market_context=market_ctx,
            sector_context=sector_ctx,
        )
        print("\n" + "=" * 60)
        print("Node 3 执行结果:")
        print(f"  status: {result.status}")
        print(f"  attitude: {result.institutional_attitude}")
        print(f"  score: {result.institutional_score}")
        print(f"  confidence: {result.confidence_multiplier}")
        print(f"  missing: {result.missing_dimensions}")
        print(f"  evidence:")
        if result.evidence.sector_flow:
            print(f"    sector_flow: {result.evidence.sector_flow}")
        if result.evidence.dde:
            print(f"    dde: {result.evidence.dde}")
        if result.evidence.northbound_realtime:
            print(f"    northbound_realtime: {result.evidence.northbound_realtime}")
        print(f"  reasoning: {(result.reasoning or '')[:200]}")
        print(f"  data_quality.available: {result.data_quality.available_fields}")
        print(f"  data_quality.failed: {result.data_quality.fetch_failed_fields}")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())
