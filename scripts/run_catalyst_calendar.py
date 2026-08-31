"""独立运行催化剂日历构建，验证输出"""
import sys
import os
import pathlib

PROJECT_ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

sys.stdout.reconfigure(encoding='utf-8')

import argparse
import asyncio
import logging
from datetime import date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def main():
    parser = argparse.ArgumentParser(description="催化剂日历构建验证")
    parser.add_argument(
        "--force-full", action="store_true",
        help="强制全量重建（跳过增量判断，触发残留清理）",
    )
    args = parser.parse_args()

    # 反爬补丁
    try:
        from src.patches.eastmoney_patch import eastmoney_patch
        eastmoney_patch()
    except Exception:
        pass

    from src.got.catalyst_calendar import CatalystCalendarBuilder

    builder = CatalystCalendarBuilder()
    target = date.today()

    print(f"\n{'='*60}")
    print(f"催化剂日历构建 — {target}")
    print(f"{'='*60}\n")

    calendar = await builder.build_calendar(target, force_full=args.force_full)

    print(f"状态: {calendar.status}")
    print(f"模式: {calendar.refresh_mode}")
    print(f"窗口: {target} ~ {target + __import__('datetime').timedelta(days=calendar.window_days)}")
    print(f"事件数: {len(calendar.events)}")
    print(f"距上次全量: {calendar.total_trading_days_since_refresh} 交易日")

    if not calendar.events:
        print("\n⚠️ 无事件")
        return

    # 按来源统计
    source_counts = {}
    for e in calendar.events:
        source_counts[e.source] = source_counts.get(e.source, 0) + 1

    print(f"\n--- 来源分布 ---")
    for src, cnt in sorted(source_counts.items()):
        print(f"  {src}: {cnt}")

    # 按影响等级统计
    level_counts = {1: 0, 2: 0, 3: 0}
    for e in calendar.events:
        level_counts[e.impact_level] = level_counts.get(e.impact_level, 0) + 1

    print(f"\n--- 影响等级 ---")
    for level in (3, 2, 1):
        stars = "★" * level
        print(f"  {stars}: {level_counts.get(level, 0)}")

    # 按类型标签统计
    tag_counts = {}
    for e in calendar.events:
        tag = e.category_tag or "未标注"
        tag_counts[tag] = tag_counts.get(tag, 0) + 1

    print(f"\n--- 类型标签 ---")
    for tag, cnt in sorted(tag_counts.items()):
        print(f"  {tag}: {cnt}")

    # 按事件分组展示（只展示 ★★+ 事件；同日期同标题的跨板块扇出记录合并为一行）
    print(f"\n--- 板块级催化剂（★★+ ） ---")
    high_events = [e for e in calendar.events if e.impact_level >= 2]

    # 聚合：同 (日期, 标题) = 同一真实事件向多个受影响板块的扇出记录（2026-08-28）
    from collections import OrderedDict
    grouped = OrderedDict()
    for e in sorted(high_events, key=lambda e: (e.event_date, -e.impact_level, e.title)):
        key = (e.event_date, e.title)
        if key not in grouped:
            grouped[key] = {"event": e, "sectors": []}
        grouped[key]["sectors"].append(e.sector)

    current_date = None
    for (event_date, _title), group in grouped.items():
        if event_date != current_date:
            current_date = event_date
            print(f"\n  [{current_date}]")
        e = group["event"]
        stars = "★" * e.impact_level
        conf_tag = "" if e.confidence >= 0.7 else f" ⚠️({e.confidence:.0%})"
        arrow = {"positive": "↑", "negative": "↓", "binary": "⚖", "drift": "↗", "fade": "⏳"}.get(e.direction, "•")
        sectors = group["sectors"]
        sector_tag = sectors[0] if len(sectors) == 1 else f"{sectors[0]} 等{len(sectors)}板块"
        print(f"    {stars} {arrow} {sector_tag}: {e.title} [{e.category_tag}]{conf_tag}")
        if len(sectors) > 1:
            print(f"        ↳ 影响 {len(sectors)} 个板块: {'/'.join(sectors)}")

    # 验证持久化
    print(f"\n--- 持久化验证 ---")
    active = builder._load_active_events(target)
    print(f"  DB 有效事件: {len(active)}")

    # 验证 JSON 序列化
    print(f"\n--- JSON 序列化验证 ---")
    from src.got.schemas import NewsContext, DataQuality
    ctx = NewsContext(
        status="normal",
        overall_impact="neutral",
        impact_score=0.0,
        data_quality=DataQuality(),
        catalyst_calendar=calendar,
    )
    dumped = ctx.model_dump(mode="json")
    cal_json = dumped.get("catalyst_calendar", {})
    print(f"  catalyst_calendar.events in JSON: {len(cal_json.get('events', []))}")
    print(f"  target_date in JSON: {cal_json.get('target_date')}")

    # 验证反序列化
    ctx2 = NewsContext.model_validate(dumped)
    cal2 = ctx2.catalyst_calendar
    print(f"  反序列化后事件数: {len(cal2.events) if cal2 else 0}")

    print(f"\n{'='*60}")
    print("✅ 验证完成")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
