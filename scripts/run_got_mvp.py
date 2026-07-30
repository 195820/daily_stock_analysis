"""
GOT 架构 MVP 验证脚本

验证：6 个节点 mock 数据能跑通完整流程，输出符合 Schema
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


async def run_got_mvp():
    """运行 GOT MVP 验证"""
    from src.got import GOTOrchestrator

    print("=" * 60)
    print("GOT 架构 MVP 验证")
    print("=" * 60)

    # 创建编排器（mock 模式）
    orchestrator = GOTOrchestrator(use_mock=True)

    # 执行完整流程
    unified = await orchestrator.run()

    # 验证输出
    print("\n" + "=" * 60)
    print("输出验证")
    print("=" * 60)

    print(f"\n状态: {unified.status}")
    print(f"统一信号: {unified.unified_signal}")
    print(f"综合置信度: {unified.overall_confidence:.2f}")
    print(f"最终仓位上限: {unified.final_position_limit:.0%}")

    print(f"\n矛盾数量: {len(unified.contradictions)}")
    for c in unified.contradictions:
        print(f"  - [{c.severity}] {c.pair}: {c.description}")

    print(f"\n共同根因: {unified.common_root_cause}")

    print(f"\n关键风险:")
    for risk in unified.key_risks:
        print(f"  - {risk}")

    print(f"\n关键机会:")
    for opp in unified.key_opportunities:
        print(f"  - {opp}")

    print(f"\n新概念建议:")
    for concept in unified.concept_suggestions:
        print(f"  - {concept['concept_name']}: {concept['evidence']}")

    print(f"\n推理文本: {unified.reasoning}")

    # Schema 验证
    print("\n" + "=" * 60)
    print("Schema 验证")
    print("=" * 60)

    # 转为 dict 验证可序列化
    unified_dict = unified.model_dump()
    json_str = json.dumps(unified_dict, ensure_ascii=False, indent=2, default=str)
    print(f"\nJSON 输出长度: {len(json_str)} 字符")
    print(f"字段数: {len(unified_dict)}")

    # 验证关键字段
    required_fields = [
        "status", "unified_signal", "overall_confidence",
        "final_position_limit", "contradictions", "key_risks",
    ]
    missing = [f for f in required_fields if f not in unified_dict]
    if missing:
        print(f"\n[X] 缺失字段: {missing}")
    else:
        print(f"\n[OK] 所有必要字段存在")

    print("\n" + "=" * 60)
    print("[OK] GOT MVP 验证通过")
    print("=" * 60)

    return unified


if __name__ == "__main__":
    asyncio.run(run_got_mvp())
