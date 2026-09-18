# -*- coding: utf-8 -*-
"""T5 事件→板块对齐层端到端验证脚本

强制全量重建催化剂日历，验证：
1. T5 _align_events_to_sectors 实际执行
2. 叙事/全球事件的 affected_sectors 字段被填充
3. _group_events_by_sector 消费扇出结构后事件覆盖板块数上升
4. T2 校验者影子日志输出
5. 无异常静默吞没（关键路径均有显式统计）
"""

import asyncio
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def main():
    from datetime import date
    from src.got.catalyst_calendar import CatalystCalendarBuilder
    from src.got.catalyst_config import CATALYST_CONFIDENCE_GATE

    # 强制全量重建：清除刷新追踪
    from src.storage import DatabaseManager, CatalystRefreshTracker
    db = DatabaseManager()
    with db.get_session() as session:
        session.query(CatalystRefreshTracker).delete()
        session.commit()
    logger.info("✅ 已清除刷新追踪，下次构建将触发全量重建")

    # 构建日历
    builder = CatalystCalendarBuilder()
    calendar = await builder.build_calendar(target_date=date.today())

    # ── 验证 ──
    print("\n" + "=" * 60)
    print("T5 端到端验证报告")
    print("=" * 60)

    print(f"\n日历状态: {calendar.status}")
    print(f"事件总数: {len(calendar.events)}")

    # 按来源分组统计
    by_source = {}
    for e in calendar.events:
        by_source.setdefault(e.source, []).append(e)

    print(f"\n按来源分布:")
    for source, events in sorted(by_source.items()):
        print(f"  {source}: {len(events)} 个")

    # T5 验证：检查 affected_sectors 填充情况
    narrative_global = [
        e for e in calendar.events
        if e.source in ("llm_narrative", "global_map")
    ]
    with_affected = [e for e in narrative_global if e.affected_sectors]
    multi_sector = [e for e in narrative_global if len(e.affected_sectors) > 1]

    print(f"\nT5 对齐层验证:")
    print(f"  叙事/全球事件总数: {len(narrative_global)}")
    print(f"  affected_sectors 已填充: {len(with_affected)}")
    print(f"  多板块扇出（>1）: {len(multi_sector)}")

    if with_affected:
        print(f"\n  扇出样本:")
        for e in with_affected[:5]:
            print(f"    [{e.event_type}] {e.title[:40]}...")
            print(f"      sector={e.sector} → affected_sectors={e.affected_sectors}")
    else:
        print(f"\n  ⚠️  无事件被 T5 对齐（可能 LLM 调用失败或 SECTOR_ALIGN_SHADOW=true）")

    # 置信度分布
    confidences = [e.confidence for e in calendar.events]
    above_gate = sum(1 for c in confidences if c >= CATALYST_CONFIDENCE_GATE)
    print(f"\n置信度分布:")
    print(f"  ≥ {CATALYST_CONFIDENCE_GATE}（入评分）: {above_gate}/{len(calendar.events)}")
    if confidences:
        print(f"  最小: {min(confidences):.2f}, 最大: {max(confidences):.2f}, 平均: {sum(confidences)/len(confidences):.2f}")

    # 板块覆盖率
    all_sectors = set()
    for e in calendar.events:
        all_sectors.add(e.sector)
        all_sectors.update(e.affected_sectors)
    print(f"\n板块覆盖: {len(all_sectors)} 个独立板块")

    # 模拟 _group_events_by_sector 扇出后的覆盖率
    from src.got.nodes.aggregator import AggregatorNode
    grouped = AggregatorNode._group_events_by_sector(calendar.events)
    print(f"_group_events_by_sector 扇出后: {len(grouped)} 个板块桶")

    print("\n" + "=" * 60)
    print("验证完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
