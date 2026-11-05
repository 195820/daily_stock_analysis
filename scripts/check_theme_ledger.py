#!/usr/bin/env python
"""P5 审计产物：主题账本 shadow 健康检查（只读，不写任何文件/DB）。

用途：生产开启 `GOT_THEME_LEDGER=shadow` 后，一条命令核对当日产物是否自洽：

1. 快照文件存在、可解析、`trade_date` 与请求日一致；
2. 快照观测数 >0，且与 DB `theme_observation` 当日行数一致；
3. 快照与 DB 的 board_code 集合一致；
4. `theme_episode` / `theme_setup` 表可读（计数仅报告）；
5. `unified.json` 的 `theme_watch_shadow` 是否存在（缺失仅 warn，可能为开关关闭）；
6. 主键无重复。

退出码：0 = pass/warn，2 = fail（不自洽），3 = 缺快照（尚未产出）。
只读：不建表、不写快照、不改 DB。

用法
----
    python scripts/check_theme_ledger.py --date 20261105
    python scripts/check_theme_ledger.py            # 默认取 data/got_daily 下最新日期
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_ROOT = ROOT / "data" / "got_daily"
DEFAULT_DB = ROOT / "data" / "stock_analysis.db"


def latest_archive_date(root: Path) -> Optional[str]:
    if not root.exists():
        return None
    days = [
        p.name for p in root.iterdir()
        if p.is_dir() and len(p.name) == 8 and p.name.isdigit()
    ]
    return max(days) if days else None


def evaluate_checks(
    *,
    snapshot: Optional[Mapping[str, Any]],
    expected_date: str,
    db_observation_codes: Optional[set[str]],
    db_duplicate_pks: int,
    unified: Optional[Mapping[str, Any]],
    mode: str,
) -> Dict[str, Any]:
    """纯函数：快照 + DB 事实 → 检查表。不读 IO，便于单测。"""
    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, warn: bool = False) -> None:
        checks.append({
            "name": name,
            "status": "pass" if ok else ("warn" if warn else "fail"),
            "detail": detail,
        })

    add("mode", mode in ("shadow", "enabled"), f"GOT_THEME_LEDGER={mode}", warn=True)
    if snapshot is None:
        add("snapshot", False, "缺 theme_ledger.json（尚未产出或开关未开）")
        return {"status": "missing", "checks": checks}

    add("snapshot_date", str(snapshot.get("trade_date")) == expected_date,
        f"trade_date={snapshot.get('trade_date')} vs {expected_date}")
    obs = snapshot.get("observations") or []
    add("observations_nonempty", len(obs) > 0, f"observations={len(obs)}")
    snap_codes = {str(o.get("board_code")) for o in obs if o.get("board_code")}
    if db_observation_codes is None:
        add("db_observation", False, "DB 读取失败", warn=True)
    else:
        add("db_observation", len(db_observation_codes) == len(snap_codes),
            f"snapshot={len(snap_codes)} / db={len(db_observation_codes)}")
        add("board_codes_match", snap_codes == db_observation_codes,
            f"仅快照有={sorted(snap_codes - db_observation_codes)[:5]}；"
            f"仅DB有={sorted(db_observation_codes - snap_codes)[:5]}")
    add("no_duplicate_pk", db_duplicate_pks == 0, f"duplicate_pk={db_duplicate_pks}")
    add("snapshot_version", bool(snapshot.get("snapshot_version")),
        f"snapshot_version={snapshot.get('snapshot_version')}")
    shadow = (unified or {}).get("theme_watch_shadow")
    add("unified_watch", shadow is not None,
        "unified.theme_watch_shadow 存在" if shadow is not None
        else "unified.theme_watch_shadow 缺失（可能为开关关闭/快照缺失）", warn=True)

    if any(c["status"] == "fail" for c in checks):
        status = "fail"
    elif any(c["status"] == "warn" for c in checks):
        status = "warn"
    else:
        status = "pass"
    return {"status": status, "checks": checks}


def collect(
    *,
    date_str: str,
    root: Path,
    db_path: Path,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    day_dir = root / date_str
    snap_path = day_dir / "theme_ledger.json"
    snapshot = None
    if snap_path.exists():
        try:
            snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            snapshot = None
            print(f"快照解析失败：{snap_path}（{type(exc).__name__}: {exc}）")

    codes: Optional[set[str]] = None
    duplicates = 0
    counts = {"episode": None, "setup": None}
    if db_path.exists():
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
                rows = con.execute(
                    "SELECT board_code FROM theme_observation WHERE trade_date=?", (iso,)
                ).fetchall()
                codes = {r[0] for r in rows}
                duplicates = con.execute(
                    "SELECT COUNT(*) FROM (SELECT board_code, trade_date FROM theme_observation "
                    "WHERE trade_date=? GROUP BY 1,2 HAVING COUNT(*)>1)", (iso,)
                ).fetchone()[0]
                counts["episode"] = con.execute("SELECT COUNT(*) FROM theme_episode").fetchone()[0]
                counts["setup"] = con.execute("SELECT COUNT(*) FROM theme_setup").fetchone()[0]
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001 - 缺表/锁 → 交回警告
            print(f"DB 读取失败：{db_path}（{type(exc).__name__}: {exc}）")

    unified = None
    unified_path = day_dir / "unified.json"
    if unified_path.exists():
        try:
            unified = json.loads(unified_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            unified = None

    report = evaluate_checks(
        snapshot=snapshot, expected_date=f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
        db_observation_codes=codes, db_duplicate_pks=duplicates,
        unified=unified,
        mode=(mode if mode is not None else os.getenv("GOT_THEME_LEDGER", "off")).strip().lower(),
    )
    report["counts"] = counts
    report["path"] = str(snap_path)
    return report


def render_md(report: Mapping[str, Any], date_str: str) -> str:
    lines = [
        f"# 主题账本健康检查：`{report['status']}`（{date_str}）",
        "",
        "| 检查 | 状态 | 明细 |",
        "|---|---|---|",
    ]
    for c in report["checks"]:
        lines.append(f"| {c['name']} | {c['status']} | {c['detail']} |")
    lines += ["", f"- 快照：`{report.get('path')}`", f"- 表计数：{report.get('counts')}", ""]
    return "\n".join(lines)


def _bootstrap_env() -> None:
    """与 main.py 同源加载 .env（否则 mode 检查永远读到 off）。"""
    try:
        from src.config import setup_env

        setup_env()
    except Exception:  # noqa: BLE001 - 加载失败保持当前环境
        pass


def main() -> int:
    _bootstrap_env()
    ap = argparse.ArgumentParser(description="主题账本 shadow 健康检查")
    ap.add_argument("--date", default="")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    date_str = args.date or latest_archive_date(Path(args.root))
    if not date_str:
        print("无可检查日期：归档根下没有 YYYYMMDD 目录")
        return 3
    report = collect(date_str=date_str, root=Path(args.root), db_path=Path(args.db))
    print(f"status={report['status']}")
    for c in report["checks"]:
        print(f"  {c['name']}: {c['status']} — {c['detail']}")
    print(f"counts={report.get('counts')}")

    out = Path(args.out) if args.out else (
        Path(args.root) / "theme_ledger_health" / f"{date_str}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render_md(report, date_str), encoding="utf-8")
    print(f"JSON: {out}")
    return 0 if report["status"] in ("pass", "warn") else (3 if report["status"] == "missing" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
