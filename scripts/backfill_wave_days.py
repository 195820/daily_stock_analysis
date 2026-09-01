#!/usr/bin/env python3
"""波次计数器回填脚本（v3.5 S2）。

按时间序全量重算所有板块的 wave_days/wave_start_date/wave_peak_strength。
幂等可重跑（覆盖写入，不累积）。

注意：08-17 前数据为旧逻辑产物，回填值仅作连续性需要，不作为校准样本。

使用：
    python scripts/backfill_wave_days.py              # 回填全部
    python scripts/backfill_wave_days.py --since 2026-08-17  # 仅回填指定日期之后
    python scripts/backfill_wave_days.py --dry-run    # 仅预览，不写入
"""

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text
from src.storage import DatabaseManager, Node2SectorTrendRecord


# 主线族状态（与 sector_analysis.py 保持一致）
MAINLINE_FAMILY = {"active_mainline", "high_position_cooling", "exhausted_mainline"}

# 波次重置阈值（弱先验，11 起样本）
WAVE_GAP_MIN_DAYS = 2  # 间隙≥2 交易日
WAVE_STRENGTH_DRAWDOWN = 10  # 强度回撤≥10 分


def calc_wave_days_for_sector(records: list[Node2SectorTrendRecord]) -> list[tuple[int, date, float]]:
    """为单个板块的历史记录序列计算波次计数器。

    Args:
        records: 按 tracking_date 升序排列的记录列表

    Returns:
        [(wave_days, wave_start_date, wave_peak_strength), ...] 与 records 一一对应
    """
    results = []
    prev_record = None
    prev_wave_days = 0
    prev_wave_start = None
    prev_wave_peak = 0.0

    for record in records:
        current_state = record.state
        current_strength = float(record.strength_score or 0)
        current_date = record.tracking_date

        # 无前记录或非主线族状态 → 重置
        if prev_record is None or current_state not in MAINLINE_FAMILY:
            wave_days = 1
            wave_start = current_date
            wave_peak = current_strength
        else:
            prev_state = prev_record.state

            # 前日也是主线族 → 延续当前波
            if prev_state in MAINLINE_FAMILY:
                wave_days = prev_wave_days + 1
                wave_start = prev_wave_start
                wave_peak = max(prev_wave_peak, current_strength)
            # 前日非主线族（neutral/ambush/defensive）→ 无条件重置
            elif prev_state in {"neutral", "ambush", "defensive_rotation"}:
                wave_days = 1
                wave_start = current_date
                wave_peak = current_strength
            else:
                # 前日 cooling/exhausted → 按回撤结构判波
                # 计算间隙天数
                gap_days = (current_date - prev_record.tracking_date).days

                # 间隙不足 2 交易日 → 冻结继承
                if gap_days < WAVE_GAP_MIN_DAYS:
                    wave_days = prev_wave_days
                    wave_start = prev_wave_start
                    wave_peak = prev_wave_peak
                else:
                    # 检查隙期强度回撤
                    # 查找间隙期间的最高强度（这里简化为前日强度，实际应查询中间记录）
                    gap_max_strength = float(prev_record.strength_score or 0)
                    strength_dropout = prev_wave_peak - gap_max_strength

                    # 回撤不足 10 分 → 冻结继承
                    if strength_dropout < WAVE_STRENGTH_DRAWDOWN:
                        wave_days = prev_wave_days
                        wave_start = prev_wave_start
                        wave_peak = prev_wave_peak
                    else:
                        # 真洗盘再启动，重置波次
                        wave_days = 1
                        wave_start = current_date
                        wave_peak = current_strength

        results.append((wave_days, wave_start, wave_peak))

        # 更新 prev
        prev_record = record
        prev_wave_days = wave_days
        prev_wave_start = wave_start
        prev_wave_peak = wave_peak

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
        # 查询所有记录，按板块分组
        query = session.query(Node2SectorTrendRecord).order_by(
            Node2SectorTrendRecord.sector_code,
            Node2SectorTrendRecord.tracking_date,
        )

        if since_date:
            query = query.filter(Node2SectorTrendRecord.tracking_date >= since_date)

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

        print(f"开始回填: {len(sectors)} 个板块, {len(all_records)} 条记录")
        if stats["date_range"]:
            print(f"日期范围: {stats['date_range'][0]} ~ {stats['date_range'][1]}")
        print(f"模式: {'预览（不写入）' if dry_run else '执行写入'}")
        print()

        for sector_code, records in sectors.items():
            # 计算波次
            wave_data = calc_wave_days_for_sector(records)

            # 更新记录
            for record, (wave_days, wave_start, wave_peak) in zip(records, wave_data):
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
