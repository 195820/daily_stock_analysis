"""
Phase 1.1: 归档完整性验证脚本

验证 data/got_daily/ 下所有归档日的 capital.json 覆盖率：
- sector_flow_all 板块级资金流（464 板块）
- dde.total_net_inflow 全市场 DDE 净流入

用途：
1. 确认多窗口计算可用的历史深度
2. 标记冷启动日期范围
3. 抽查 akshare "3日排行/5日排行" 列名（可选）

运行方式：
    python scripts/verify_flow_archive.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 项目根目录（脚本所在位置的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_ROOT = PROJECT_ROOT / "data" / "got_daily"

# 排除备份/修复前缀目录
_SKIP_SUFFIXES = ("_backup", "_pre_fix")


def _load_capital(date_dir: Path) -> dict | None:
    """加载单日 capital.json，失败返回 None。"""
    path = date_dir / "capital.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [WARN] {date_dir.name} 读取失败: {e}", file=sys.stderr)
        return None


def verify() -> dict:
    """遍历归档目录，返回覆盖率统计。"""
    if not ARCHIVE_ROOT.exists():
        print(f"[ERROR] 归档目录不存在: {ARCHIVE_ROOT}", file=sys.stderr)
        return {"error": "archive_not_found"}

    dirs = sorted(
        d for d in ARCHIVE_ROOT.iterdir()
        if d.is_dir() and not d.name.endswith(_SKIP_SUFFIXES)
    )

    report = {
        "total_dirs": len(dirs),
        "days_with_sector_flow_all": [],
        "days_with_dde": [],
        "days_failed": [],
        "sector_counts": {},
    }

    for d in dirs:
        data = _load_capital(d)
        if data is None:
            report["days_failed"].append(d.name)
            continue

        status = data.get("status")
        evidence = data.get("evidence") or {}
        sector_flow = evidence.get("sector_flow") or {}
        sector_flow_all = sector_flow.get("sector_flow_all") or {}
        dde = evidence.get("dde") or {}
        dde_net = dde.get("total_net_inflow")

        if status == "failed":
            report["days_failed"].append(d.name)
            continue

        if sector_flow_all:
            report["days_with_sector_flow_all"].append(d.name)
            report["sector_counts"][d.name] = len(sector_flow_all)

        if dde_net is not None:
            report["days_with_dde"].append(d.name)

    return report


def print_report(report: dict) -> None:
    """打印可读报告。"""
    if "error" in report:
        print(f"[FAIL] {report['error']}")
        return

    total = report["total_dirs"]
    sfa_days = report["days_with_sector_flow_all"]
    dde_days = report["days_with_dde"]
    failed = report["days_failed"]

    print("=" * 70)
    print("GOT Archive Completeness Verification Report")
    print("=" * 70)
    print(f"Archive root: {ARCHIVE_ROOT}")
    print(f"Total archive days: {total}")
    print()

    print(f"sector_flow_all 覆盖: {len(sfa_days)}/{total}")
    if sfa_days:
        print(f"  首日: {sfa_days[0]}  末日: {sfa_days[-1]}")
        # 计算最长连续段
        consecutive = _max_consecutive(sfa_days)
        print(f"  最长连续: {consecutive} 天")
        print(f"  板块数量范围: {min(report['sector_counts'].values())} ~ "
              f"{max(report['sector_counts'].values())}")
    print()

    print(f"dde.total_net_inflow 覆盖: {len(dde_days)}/{total}")
    if dde_days:
        print(f"  有数据日: {', '.join(dde_days)}")
        consecutive = _max_consecutive(dde_days)
        print(f"  最长连续: {consecutive} 天")
    print()

    if failed:
        print(f"失败/缺失日 ({len(failed)}): {', '.join(failed)}")
    print()

    # Phase 1 就绪判定
    print("-" * 70)
    print("Phase 1 就绪判定:")
    min_sfa_for_5d = 5
    if len(sfa_days) >= min_sfa_for_5d:
        print(f"  [OK] sector_flow_all 覆盖 {len(sfa_days)} 天 >= {min_sfa_for_5d}，"
              f"可支持 5 日窗口计算")
    else:
        print(f"  [FAIL] sector_flow_all 仅 {len(sfa_days)} 天 < {min_sfa_for_5d}，"
              f"不足以支持 5 日窗口")

    min_dde_for_3d = 3
    if len(dde_days) >= min_dde_for_3d:
        print(f"  [OK] DDE 覆盖 {len(dde_days)} 天 >= {min_dde_for_3d}，"
              f"可支持 3 日累计")
    else:
        print(f"  [WARN] DDE 仅 {len(dde_days)} 天 < {min_dde_for_3d}，"
              f"需逐日积累（冷启动期约 {min_dde_for_3d - len(dde_days)} 天）")
    print("=" * 70)


def _max_consecutive(date_strs: list[str]) -> int:
    """计算日期字符串列表中的最长连续**交易日**段（跳过周末）。"""
    if not date_strs:
        return 0
    from datetime import datetime, timedelta
    dates = sorted(datetime.strptime(d, "%Y%m%d") for d in date_strs)
    max_run = 1
    current_run = 1
    for i in range(1, len(dates)):
        prev = dates[i - 1]
        curr = dates[i]
        delta = (curr - prev).days
        # 相邻交易日判定：delta=1 且同周，或周五→周一（delta=3）
        is_adjacent = (
            delta == 1
            or (delta == 3 and prev.weekday() == 4 and curr.weekday() == 0)  # Fri→Mon
            or (delta == 2 and prev.weekday() == 4)  # Fri→Mon (无假期)
        )
        if is_adjacent:
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 1
    return max_run


if __name__ == "__main__":
    report = verify()
    print_report(report)
