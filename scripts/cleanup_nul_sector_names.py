#!/usr/bin/env python3
"""NUL 幽灵行清理脚本（v3.5 S1）。

问题：数据库中部分板块名包含 \x00 字符（如 'CXO概念\x00'、'创新药\x00\x00'），
形成独立记录污染寿命统计与趋势查询。

处理流程：
1. 备份数据库 → data/stock_analysis.db.backup_YYYYMMDD_HHMMSS
2. 扫描所有含 sector_name 的表（node2_sector_trends / sector_history / ambush_position_snapshots）
3. 定位带 \x00 的行，与干净同名行比对
4. 输出删除报告（不自动执行）
5. 人工确认后执行删除

使用：
    python scripts/cleanup_nul_sector_names.py              # 仅扫描，输出报告
    python scripts/cleanup_nul_sector_names.py --execute    # 执行删除（需先备份）
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "stock_analysis.db"

# 含 sector_name 的表
TABLES_TO_SCAN = [
    "node2_sector_trends",
    "sector_history",
    "ambush_position_snapshots",
]


def backup_database(db_path: Path) -> Path:
    """备份数据库，返回备份文件路径。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_suffix(f".db.backup_{timestamp}")
    shutil.copy2(db_path, backup_path)
    print(f"✓ 数据库已备份: {backup_path}")
    return backup_path


def scan_nul_rows(db_path: Path) -> dict[str, list[dict]]:
    """扫描所有表中含 \x00 的行，返回 {表名: [行数据]}。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    results = {}

    for table in TABLES_TO_SCAN:
        # 查询含 \x00 的行（SQLite 中 NUL 字符用 CHAR(0) 表示）
        query = f"""
            SELECT *, rowid as _rowid
            FROM {table}
            WHERE INSTR(sector_name, CHAR(0)) > 0
        """
        try:
            cursor = conn.execute(query)
            rows = [dict(row) for row in cursor.fetchall()]
            if rows:
                results[table] = rows
                print(f"✗ {table}: 发现 {len(rows)} 条含 NUL 的记录")
            else:
                print(f"✓ {table}: 无 NUL 污染")
        except sqlite3.OperationalError as e:
            print(f"⚠ {table}: 查询失败 - {e}")

    conn.close()
    return results


def find_clean_duplicates(db_path: Path, nul_rows: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """对每条 NUL 行，查找是否存在干净的重复记录（同名但无 \x00）。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    duplicates = {}

    for table, rows in nul_rows.items():
        table_dupes = []
        for row in rows:
            # 构造干净版本的名字
            clean_name = row["sector_name"].strip().strip("\x00")
            if not clean_name:
                continue

            # 查找是否存在干净的同名记录（排除当前 NUL 行）
            query = f"""
                SELECT *, rowid as _rowid
                FROM {table}
                WHERE sector_name = ?
                  AND rowid != ?
            """
            cursor = conn.execute(query, (clean_name, row["_rowid"]))
            clean_matches = [dict(r) for r in cursor.fetchall()]

            if clean_matches:
                table_dupes.append({
                    "nul_row": row,
                    "clean_matches": clean_matches,
                })

        if table_dupes:
            duplicates[table] = table_dupes

    conn.close()
    return duplicates


def generate_report(nul_rows: dict[str, list[dict]], duplicates: dict[str, list[dict]]) -> str:
    """生成清理报告。"""
    lines = []
    lines.append("=" * 80)
    lines.append("NUL 幽灵行清理报告")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 80)
    lines.append("")

    total_nul = sum(len(rows) for rows in nul_rows.values())
    total_dupes = sum(len(d) for d in duplicates.values())

    lines.append(f"总计: {total_nul} 条 NUL 污染记录，其中 {total_dupes} 条有干净重复可删除")
    lines.append("")

    for table in TABLES_TO_SCAN:
        lines.append("-" * 80)
        lines.append(f"表: {table}")
        lines.append("-" * 80)

        if table not in nul_rows:
            lines.append("  （无数据）")
            continue

        nul_count = len(nul_rows[table])
        dupe_count = len(duplicates.get(table, []))
        lines.append(f"  NUL 污染: {nul_count} 条")
        lines.append(f"  可删除重复: {dupe_count} 条")
        lines.append("")

        # 展示前 10 条样例
        samples = nul_rows[table][:10]
        for i, row in enumerate(samples, 1):
            sector_name = row["sector_name"]
            clean_name = sector_name.strip().strip("\x00")
            nul_chars = sector_name.count("\x00")

            lines.append(f"  [{i}] sector_name: {repr(sector_name)}")
            lines.append(f"      干净版本: {repr(clean_name)}")
            lines.append(f"      NUL 字符数: {nul_chars}")

            # 检查是否有干净重复
            if table in duplicates:
                dupe_info = next(
                    (d for d in duplicates[table] if d["nul_row"]["_rowid"] == row["_rowid"]),
                    None,
                )
                if dupe_info:
                    lines.append(f"      ✓ 存在 {len(dupe_info['clean_matches'])} 条干净重复，可安全删除")
                else:
                    lines.append(f"      ✗ 无干净重复，删除将丢失数据（需人工审核）")

            # 显示关键字段
            if "tracking_date" in row:
                lines.append(f"      tracking_date: {row['tracking_date']}")
            if "state" in row:
                lines.append(f"      state: {row['state']}")
            if "strength_score" in row:
                lines.append(f"      strength_score: {row['strength_score']}")
            lines.append("")

        if nul_count > 10:
            lines.append(f"  ... 还有 {nul_count - 10} 条未展示")
        lines.append("")

    lines.append("=" * 80)
    lines.append("操作建议:")
    lines.append("  1. 检查报告中'无干净重复'的条目，确认是否可删除")
    lines.append("  2. 确认无误后执行: python scripts/cleanup_nul_sector_names.py --execute")
    lines.append("  3. 执行前已自动备份数据库")
    lines.append("=" * 80)

    return "\n".join(lines)


def execute_cleanup(db_path: Path, duplicates: dict[str, list[dict]]) -> int:
    """执行删除操作，返回删除行数。"""
    conn = sqlite3.connect(db_path)
    deleted_count = 0

    for table, dupe_list in duplicates.items():
        for dupe_info in dupe_list:
            nul_rowid = dupe_info["nul_row"]["_rowid"]
            query = f"DELETE FROM {table} WHERE rowid = ?"
            conn.execute(query, (nul_rowid,))
            deleted_count += 1

    conn.commit()
    conn.close()
    return deleted_count


def main():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="NUL 幽灵行清理脚本")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="执行删除操作（默认仅扫描）",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DB_PATH,
        help=f"数据库路径（默认: {DB_PATH}）",
    )
    args = parser.parse_args()

    db_path = args.db_path

    if not db_path.exists():
        print(f"✗ 数据库不存在: {db_path}")
        sys.exit(1)

    print("=" * 80)
    print("NUL 幽灵行清理工具 (v3.5 S1)")
    print("=" * 80)
    print(f"数据库: {db_path}")
    print(f"模式: {'执行删除' if args.execute else '仅扫描'}")
    print("")

    # 备份（仅执行模式）
    if args.execute:
        print("步骤 1/4: 备份数据库...")
        backup_database(db_path)
        print("")
    else:
        print("步骤 1/4: 跳过备份（扫描模式）")
        print("")

    # 扫描 NUL 行
    print("步骤 2/4: 扫描含 NUL 的记录...")
    nul_rows = scan_nul_rows(db_path)
    print("")

    if not nul_rows:
        print("✓ 未发现 NUL 污染，无需清理")
        sys.exit(0)

    # 查找干净重复
    print("步骤 3/4: 查找干净重复记录...")
    duplicates = find_clean_duplicates(db_path, nul_rows)
    print("")

    # 生成报告
    print("步骤 4/4: 生成清理报告...")
    report = generate_report(nul_rows, duplicates)
    print(report)

    # 保存报告到文件
    report_path = PROJECT_ROOT / "reports" / f"nul_cleanup_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"\n✓ 报告已保存: {report_path}")

    # 执行删除
    if args.execute:
        total_dupes = sum(len(d) for d in duplicates.values())
        if total_dupes == 0:
            print("\n✗ 无可删除的干净重复记录")
            sys.exit(0)

        print(f"\n即将删除 {total_dupes} 条 NUL 幽灵行...")
        confirm = input("确认执行? (yes/no): ").strip().lower()
        if confirm != "yes":
            print("已取消")
            sys.exit(0)

        deleted = execute_cleanup(db_path, duplicates)
        print(f"\n✓ 已删除 {deleted} 条 NUL 幽灵行")
        print("✓ 请重新运行扫描确认清理结果")
    else:
        print("\n提示: 当前为扫描模式，如需执行删除请添加 --execute 参数")


if __name__ == "__main__":
    main()
