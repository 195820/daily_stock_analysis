#!/usr/bin/env python3
"""波次计数器回填脚本（v3.5 S2）。

按时间序全量重算所有板块的 wave_days/wave_start_date/wave_peak_strength。
幂等可重跑（覆盖写入，不累积）。

判波语义复用 sector_analysis.decide_wave_days（审核 P1 修复：消除双实现分叉），
--since 时仍全量加载历史计算（保证窗口首条记录有完整前文语境），仅写回窗口内行（审核 P2 修复）。

注意：08-17 前数据为旧逻辑产物，回填值仅作连续性需要，不作为校准样本。

使用：
    python scripts/backfill_wave_days.py              # 回填全部
    python scripts/backfill_wave_days.py --since 2026-08-17  # 仅写回指定日期之后（计算仍全量）
    python scripts/backfill_wave_days.py --dry-run    # 仅预览，不写入（内存快照，不碰 ORM 对象）
"""

import argparse
import sys
from collections import defaultdict
from collections import namedtuple
from datetime import date, datetime
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.got.nodes.sector_analysis import decide_wave_days
from src.storage import DatabaseManager, Node2SectorTrendRecord


# 内存快照（dry-run 下不触碰 ORM 对象，避免脏会话意外提交）
_Snap = namedtuple(
    "_Snap",
    ["state", "strength_score", "tracking_date", "wave_days", "wave_start_date", "wave_peak_strength"],
)


def calc_wave_days_for_sector(records: list) -> list[tuple[int, date, float]]:
    """为单个板块的历史记录序列计算波次计数器（与在线逻辑共用 decide_wave_days）。

    Args:
        records: 按 tracking_date 升序排列的 ORM 记录列表（只读，不修改）

    Returns:
        [(wave_days, wave_start_date, wave_peak_strength), ...] 与 records 一一对应
    """
    results: list[tuple[int, date, float]] = []
    # 内存快照序列：decide_wave_days 从快照读 wave_* 字段（等价于在线从已落库记录读取）
    snaps = [
        _Snap(r.state, float(r.strength_score or 0), r.tracking_date, None, None, None)
        for r in records
    ]

    for i, snap in enumerate(snaps):
        prev_snap = snaps[i - 1] if i > 0 else None

        # 两段式取数（与在线 _calc_wave_days 同构）：锚点 = 最近一次 active 日，间隙 = 锚点至今的记录
        anchor_snap = None
        gap_snaps: list = []
        if (
            prev_snap is not None
            and snap.state == "active_mainline"
            and prev_snap.state in ("high_position_cooling", "exhausted_mainline")
        ):
            for j in range(i - 1, -1, -1):
                if snaps[j].state == "active_mainline":
                    anchor_snap = snaps[j]
                    gap_snaps = snaps[j + 1:i]
                    break

        wave_days, wave_start, wave_peak = decide_wave_days(
            snap.state, snap.strength_score, snap.tracking_date,
            prev_snap, anchor_snap, gap_snaps,
        )
        results.append((wave_days, wave_start, wave_peak))

        # 回写快照，供后续日期消费（等价于在线流水线的落库→次日读取）
        snaps[i] = snap._replace(
            wave_days=wave_days, wave_start_date=wave_start, wave_peak_strength=wave_peak,
        )

    return results


def backfill_wave_days(since_date: date | None = None, dry_run: bool = False) -> dict:
    """回填波次计数器。

    Args:
        since_date: 仅回填此日期之后的记录（含），None 表示全量
        dry_run: True 则仅预览不写入

    Returns:
        {sectors_count, records_updated, date_range}
    """
    db = DatabaseManager()

    with db.get_session() as session:
        # 全量加载（审核 P2 修复）：--since 时不按日期过滤查询，
        # 保证窗口首条记录的前文语境完整，避免首条必判"重置 1"污染后续波次起点。
        query = session.query(Node2SectorTrendRecord).order_by(
            Node2SectorTrendRecord.sector_code,
            Node2SectorTrendRecord.tracking_date,
        )

        all_records = query.all()

        # 按板块分组
        sectors = defaultdict(list)
        for record in all_records:
            sectors[record.sector_code].append(record)

        stats = {
            "sectors_count": len(sectors),
            "records_updated": 0,
            "date_range": None,
        }

        if all_records:
            min_date = min(r.tracking_date for r in all_records)
            max_date = max(r.tracking_date for r in all_records)
            stats["date_range"] = (min_date, max_date)

        print(f"开始回填: {len(sectors)} 个板块, {len(all_records)} 条记录（全量计算）")
        if stats["date_range"]:
            print(f"日期范围: {stats['date_range'][0]} ~ {stats['date_range'][1]}")
        if since_date:
            print(f"写回窗口: ≥ {since_date}（窗口外记录仅参与计算不写入）")
        print(f"模式: {'预览（不写入）' if dry_run else '执行写入'}")
        print()

        for sector_code, records in sectors.items():
            # 计算波次（全量历史）
            wave_data = calc_wave_days_for_sector(records)

            # 更新记录（仅写回窗口内行）
            for record, (wave_days, wave_start, wave_peak) in zip(records, wave_data):
                if since_date and record.tracking_date < since_date:
                    continue

                if dry_run:
                    # 仅打印前 5 条样例
                    if stats["records_updated"] < 5:
                        print(
                            f"  {record.sector_name} {record.tracking_date}: "
                            f"wave_days={wave_days}, start={wave_start}, peak={wave_peak:.1f}"
                        )
                else:
                    record.wave_days = wave_days
                    record.wave_start_date = wave_start
                    record.wave_peak_strength = wave_peak

                stats["records_updated"] += 1

        if not dry_run:
            session.commit()
            print(f"\n✓ 已更新 {stats['records_updated']} 条记录")
        else:
            print(f"\n（预览模式，未写入）")

        return stats


def main():
    parser = argparse.ArgumentParser(description="波次计数器回填脚本")
    parser.add_argument(
        "--since",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        help="仅回填此日期之后的记录（YYYY-MM-DD）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览，不写入数据库",
    )
    args = parser.parse_args()

    print("=" * 80)
    print("波次计数器回填工具 (v3.5 S2)")
    print("=" * 80)
    print()

    try:
        stats = backfill_wave_days(since_date=args.since, dry_run=args.dry_run)
        print()
        print("=" * 80)
        print("回填完成")
        print(f"  板块数: {stats['sectors_count']}")
        print(f"  记录数: {stats['records_updated']}")
        if stats["date_range"]:
            print(f"  日期范围: {stats['date_range'][0]} ~ {stats['date_range'][1]}")
        print("=" * 80)
    except Exception as e:
        print(f"\n✗ 回填失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
