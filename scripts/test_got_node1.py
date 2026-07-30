"""
GOT Node 1 真实数据测试

验证：大盘分析节点能获取真实指数数据并计算技术指标
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


async def test_node1_real():
    """测试 Node 1 真实数据"""
    from src.got.nodes.market_analysis import MarketAnalysisNode

    print("=" * 60)
    print("GOT Node 1 真实数据测试")
    print("=" * 60)

    # 创建节点（真实数据模式）
    node = MarketAnalysisNode(use_mock=False)

    try:
        # 执行分析
        result = await node.run()

        print("\n" + "=" * 60)
        print("分析结果")
        print("=" * 60)

        print(f"\n科技成长方向:")
        print(f"  趋势: {result.tech_growth.trend}")
        print(f"  评分: {result.tech_growth.score:.2f}")
        print(f"  一致性: {result.tech_growth.consistency}")
        print(f"  创业板指得分: {result.tech_growth.constituents.get('399006.SZ', 'N/A')}")
        print(f"  科创50得分: {result.tech_growth.constituents.get('000688.SH', 'N/A')}")

        print(f"\n市场状态:")
        print(f"  ADX: {result.tech_growth.regime_metadata.adx_value:.1f}")
        print(f"  趋势状态: {result.tech_growth.regime_metadata.regime}")
        print(f"  波动率: {result.tech_growth.regime_metadata.volatility_regime}")

        print(f"\n多时间框架:")
        print(f"  短期(5日): {result.tech_growth.signals.short_term.trend} ({result.tech_growth.signals.short_term.score:.2f})")
        print(f"  中期(20日): {result.tech_growth.signals.medium_term.trend} ({result.tech_growth.signals.medium_term.score:.2f})")
        print(f"  长期(60日): {result.tech_growth.signals.long_term.trend} ({result.tech_growth.signals.long_term.score:.2f})")
        print(f"  统一趋势: {result.tech_growth.timeframe_got.unified_trend}")
        print(f"  矛盾类型: {result.tech_growth.timeframe_got.contradiction_type}")

        print(f"\n风格对比:")
        print(f"  中小盘: {result.mid_small_cap.trend} ({result.mid_small_cap.score:.2f})")
        print(f"  大盘蓝筹: {result.large_cap.trend} ({result.large_cap.score:.2f})")

        print(f"\n仓位上限: {result.position_limit:.0%}")
        print(f"置信度: {result.confidence:.2f}")

        print(f"\n关键点位:")
        print(f"  短期支撑: {result.key_levels.short_term.get('support', 'N/A')}")
        print(f"  短期阻力: {result.key_levels.short_term.get('resistance', 'N/A')}")

        print(f"\n推理: {result.reasoning}")

        print(f"\n数据质量:")
        print(f"  可用字段: {len(result.data_quality.available_fields)}")
        print(f"  缺失字段: {result.data_quality.missing_fields}")
        print(f"  获取失败: {result.data_quality.fetch_failed_fields}")

        print("\n" + "=" * 60)
        print("[OK] Node 1 真实数据测试通过")
        print("=" * 60)

    except Exception as e:
        print(f"\n[X] 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_node1_real())
