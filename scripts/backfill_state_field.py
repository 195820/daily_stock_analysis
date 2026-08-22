"""一次性回填脚本：从历史 sector.json 反推 state 字段，回填 DB。

解决：旧实现没写 state 字段，新实现第一次跑时 prev_state=None 导致冷启动判定。
回填后，医药板块能从正确的 prev_state 开始判定。

用法：
    python scripts/backfill_state_field.py [--dry-run]
"""
import argparse
import json
import os
import sys
from datetime import date, datetime

os.chdir(r"E:\daily_stock_analysis")
sys.path.insert(0, ".")

from src.storage import DatabaseManager, Node2SectorTrendRecord


def extract_state_from_sector_json(sector_json_path: str) -> dict[str, str]:
    """从 sector.json 提取每个板块的 state。

    Returns: {code: state}
    """
    result = {}
    with open(sector_json_path, encoding="utf-8") as f:
        ctx = json.load(f)

    # active_mainline（组结构）
    for group in ctx.get("active_mainline", []):
        for m in group.get("members", []):
            code = m.get("code")
            if code:
                result[code] = m.get("state", "active_mainline")

    # exhausted_mainline / ambush / defensive_rotation（直接列表）
    for key, default_state in [
        ("exhausted_mainline", "exhausted_mainline"),
        ("ambush", "ambush"),
        ("defensive_rotation", "defensive_rotation"),
        ("cooling_down", "high_position_cooling"),
    ]:
        for d in ctx.get(key, []):
            code = d.get("code")
            if code:
                result[code] = d.get("state", default_state)

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写 DB")
    args = parser.parse_args()

    got_daily_root = os.path.join("data", "got_daily")
    archive_dirs = sorted(
        d for d in os.listdir(got_daily_root)
        if d.startswith("20") and os.path.isdir(os.path.join(got_daily_root, d))
    )

    db = DatabaseManager()
    updated_count = 0

    with db.get_session() as session:
        for date_str in archive_dirs:
            sector_json = os.path.join(got_daily_root, date_str, "sector.json")
            if not os.path.isfile(sector_json):
                continue

            try:
                tracking_date = datetime.strptime(date_str, "%Y%m%d").date()
            except ValueError:
                continue

            state_map = extract_state_from_sector_json(sector_json)
            if not state_map:
                continue

            for code, state in state_map.items():
                record = session.query(Node2SectorTrendRecord).filter_by(
                    sector_code=code,
                    tracking_date=tracking_date,
                ).first()

                if record is None:
                    continue

                if record.state is None:
                    if args.dry_run:
                        print(f"[DRY] {date_str} {code}: None → {state}")
                    else:
                        record.state = state
                        updated_count += 1

        if not args.dry_run:
            session.commit()
            print(f"\n[OK] 回填完成，更新 {updated_count} 条记录")
        else:
            print(f"\n[DRY-RUN] 以上记录将被更新（实际未写入）")


if __name__ == "__main__":
    main()
