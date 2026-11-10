#!/usr/bin/env python
"""Node 2 连续相位 A/B 离线审计（**不是**生产 shadow，也不写生产目录）。

三种用法：

1. 缓存截面重建（08-03 硬验收口径）
   `python scripts/audit_node2_lifecycle_ab.py --date 20260803 --report-discovery`
   标 `kind=cache_reconstruction`：只按第一步过滤 `board_ohlcv_cache.json`，
   再用与生产相同的 `transition_lifecycle` + `_apply_lifecycle_state_machine`
   算 route_state，不构造假 `MarketContext`，不伪称生产重放。

2. 两臂对比（legacy=归档 universe / enabled=全量缓存发现）
   `python scripts/audit_node2_lifecycle_ab.py --compare --full-discovery \
        --start 20260902 --end 20261103 --out <tmp>/node2_lifecycle_backtest.json`
   两个独立子进程、各自临时 DB 与输出目录；逐日检查相位/年龄/路由/无截断/unresolved。

3. legacy 前后对照
   `python scripts/audit_node2_lifecycle_ab.py --diff-json <before> <after>`
   字段级比对 state/strength/buckets（改码前后必须逐字段一致）。

生产进程只接受 `GOT_NODE2_LIFECYCLE=legacy|enabled`；`ab` 只是本脚本内部的臂标签，
不可写入 `.env`。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

REPLAY_DIR = ROOT / "data" / "_replay"
CACHE = REPLAY_DIR / "board_ohlcv_cache.json"
PROD_DB = ROOT / "data" / "stock_analysis.db"
REPLAY_SCRIPT = ROOT / "scripts" / "replay_node2_fidelity.py"

ROUTED_STATES = ("active_mainline", "ambush", "high_position_cooling", "exhausted_mainline")


def parse_day(text: str) -> date:
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def load_cache() -> dict:
    return json.loads(CACHE.read_text(encoding="utf-8"))


def load_code_name() -> dict[str, str]:
    """code → name（生产库 node2_sector_trends 的最新命名）。"""
    con = sqlite3.connect(PROD_DB)
    try:
        rows = con.execute(
            "select sector_code, sector_name, count(*) c from node2_sector_trends "
            "group by sector_code, sector_name"
        ).fetchall()
    finally:
        con.close()
    best: dict[str, tuple[str, int]] = {}
    for code, name, cnt in rows:
        name = (name or "").strip("\x00")
        if not code or not name:
            continue
        if code not in best or cnt > best[code][1]:
            best[code] = (name, int(cnt))
    return {code: name for code, (name, _) in best.items()}


def _truncate(payload: dict, as_of: date) -> dict:
    """只保留 ≤ as_of 的 bar（缓存按日期升序，禁止未来数据）。"""
    cutoff = as_of.isoformat()
    keep = [i for i, d in enumerate(payload.get("dates") or []) if d <= cutoff]
    return {key: [values[i] for i in keep]
            for key, values in payload.items() if key != "dates" and isinstance(values, list)}


def _quick_metrics(closes: list[float]) -> dict | None:
    """1/3/5 日涨跌幅 + 近 5 日暴跌（与 MootdxFetcher 同口径）。"""
    if len(closes) < 6 or any(c <= 0 for c in closes):
        return None
    n = len(closes)
    daily = [(closes[i] - closes[i - 1]) / closes[i - 1] * 100 for i in range(max(1, n - 5), n)]
    return {
        "change_1d": (closes[-1] - closes[-2]) / closes[-2] * 100,
        "change_3d": (closes[-1] - closes[-4]) / closes[-4] * 100,
        "change_5d": (closes[-1] - closes[-6]) / closes[-6] * 100,
        "has_crash": any(r < -5.0 for r in daily),
    }


def cache_reconstruction(as_of: date) -> dict:
    """缓存截面重建：08-03 硬验收用（不读生产归档、不构造 MarketContext）。"""
    import bisect

    import pandas as pd

    from src.got.board_filter import is_noise_board
    from src.got.nodes.sector_analysis import SectorAnalysisNode
    from src.got.sector_lifecycle import (
        build_lifecycle_evidence,
        lifecycle_weight,
        transition_lifecycle,
    )

    cache = load_cache()
    code_name = load_code_name()
    unresolved: list[str] = []
    universe: dict[str, dict] = {}
    for code, payload in cache.items():
        bars = _truncate(payload, as_of)
        closes = [float(c) for c in bars.get("close") or []]
        if len(closes) < 6:
            continue
        name = code_name.get(code)
        if not name:
            unresolved.append(code)
            continue
        if is_noise_board(name):
            continue
        universe[code] = {
            "name": name,
            "closes": closes,
            "highs": [float(h) for h in bars.get("high") or closes],
            "volumes": [float(v) for v in bars.get("volume") or [1.0] * len(closes)],
            "quick": _quick_metrics(closes),
        }

    calendar = sorted({d for payload in cache.values() for d in payload.get("dates") or []
                       if d <= as_of.isoformat()})
    calendar = [parse_day(d.replace("-", "")) for d in calendar]

    valid = {code: row for code, row in universe.items() if row["quick"] is not None}
    quality = {
        "mode": "cache_reconstruction",
        "quick_universe_count": len(universe),
        "quick_valid_count": len(valid),
        "launch_candidates": 0,
        "launch_after_kline": 0,
        "unavailable_reasons": [],
    }
    if len(valid) < 50:
        quality["unavailable_reasons"].append("quick_universe_below_min")
        return {"kind": "cache_reconstruction", "date": as_of.isoformat(),
                "phase_data_quality": quality, "unresolved": sorted(unresolved),
                "boards": [], "launch": []}
    missing_ratio = 1.0 - len(valid) / max(len(universe), 1)
    if missing_ratio > 0.20:
        quality["unavailable_reasons"].append("quick_missing_ratio_exceeded")
        return {"kind": "cache_reconstruction", "date": as_of.isoformat(),
                "phase_data_quality": quality, "unresolved": sorted(unresolved),
                "boards": [], "launch": []}

    ordered = sorted(row["quick"]["change_5d"] for row in valid.values())
    n = len(ordered)
    pct = {code: bisect.bisect_right(ordered, row["quick"]["change_5d"]) / n
           for code, row in valid.items()}

    from src.got.nodes.sector_analysis import PHASE_FETCH_BUDGET

    launch_codes = [
        code for code, row in valid.items()
        if row["quick"]["change_3d"] > 0 and row["quick"]["change_5d"] > 0
        and not row["quick"]["has_crash"]
    ]
    launch_codes.sort(key=lambda c: valid[c]["quick"]["change_5d"], reverse=True)
    launch_codes = launch_codes[:PHASE_FETCH_BUDGET]
    quality["launch_candidates"] = len(launch_codes)

    boards: list[dict] = []
    launch_after = 0
    for code in launch_codes:
        row = valid[code]
        df = pd.DataFrame({
            "close": row["closes"],
            "high": row["highs"][-len(row["closes"]):],
            "volume": row["volumes"][-len(row["closes"]):],
        })
        tw = SectorAnalysisNode._measure_trend_window(df, r5_pct=pct[code])
        after = bool(
            tw.get("above_ma20")
            and tw.get("r10") is not None and tw["r10"] > 0
            and tw.get("ma20_slope") is not None and tw["ma20_slope"] > 0
        )
        if after:
            launch_after += 1
        evidence = build_lifecycle_evidence(
            as_of,
            bar_complete=True,
            trend_window=tw,
            r5_pct=pct[code],
            strength=None,
            rrg_quadrant=None,
            overheat_level="normal",
            break_signals={
                "price_chg_5d": tw.get("r5"),
                "above_ma20": tw.get("above_ma20"),
                "high20_pos": None if tw.get("high20_dist") is None else 1.0 + tw["high20_dist"],
                "overheat_level": "normal",
                # 无前日 strength：单日分数回落按 0 处理（缓存重建无法复算截面分）
                "score_drop": 0.0,
            },
            score_drop=0.0,
        )
        state = transition_lifecycle(None, evidence, calendar)
        route = SectorAnalysisNode._apply_lifecycle_state_machine(state, evidence)
        boards.append({
            "code": code,
            "name": row["name"],
            "phase": state.phase,
            "phase_age": state.phase_age,
            "phase_dwell": state.phase_dwell,
            "route_state": route,
            "in_output": False,  # 下方按 TopN 统一回填
            "data_status": state.data_status,
            "time_weight": lifecycle_weight(state),
            "launch_score": evidence.launch_score,
            "r5": tw.get("r5"),
            "r10": tw.get("r10"),
            "r5_pct": pct[code],
            "high20_dist": tw.get("high20_dist"),
            "kline_ok": after,
        })
    quality["launch_after_kline"] = launch_after

    from src.got.nodes.sector_analysis import get_launch_topn
    topn = get_launch_topn()
    ranked = sorted(
        [b for b in boards if b["phase"] == "launch" and b["route_state"] == "ambush"],
        key=lambda b: (-(b.get("launch_score") if b.get("launch_score") is not None else -1.0),
                       -b.get("strength") if b.get("strength") is not None else 0.0,
                       b["code"]),
    )
    for pos, board in enumerate(ranked, start=1):
        board["rank"] = pos
        board["in_output"] = bool(topn <= 0 or pos <= topn)
        if not board["in_output"]:
            board["dropped_reason"] = "launch_topn"

    return {
        "kind": "cache_reconstruction",
        "date": as_of.isoformat(),
        "launch_topn": get_launch_topn(),
        "phase_data_quality": quality,
        "unresolved": sorted(unresolved),
        "boards": boards,
        "launch": [b for b in boards if b["phase"] == "launch"],
    }


def _check_880710(report: dict) -> tuple[bool, list[str]]:
    """硬验收：计划的四项全部满足才算通过（相位/年龄/路由/是否进输出）。

    计划原文要求 `launch_watch.in_output=True` 且"并进入 enabled 的 ambush"，未满足即失败。
    若 `GOT_NODE2_LAUNCH_TOPN` 把 880710 截在输出之外，那是**计划判据未满足**，
    不能用"容量披露"把失败改写成通过——只能改配置（TOPN=0）或改计划文书后重跑。
    """
    problems: list[str] = []
    hit = next((b for b in report["launch"] if b["code"] == "880710"), None)
    if hit is None:
        names = [b["name"] for b in report["boards"] if "种业" in (b["name"] or "")]
        problems.append(
            f"880710(种业) 未进入 launch 候选：候选 {len(report['boards'])}，"
            f"含种业的候选={names}"
        )
        return False, problems
    if hit["phase"] != "launch":
        problems.append(f"880710 phase={hit['phase']}（要求 launch）")
    if hit["phase_age"] is None or not (1 <= hit["phase_age"] <= 3):
        problems.append(f"880710 phase_age={hit['phase_age']}（要求 1..3）")
    if hit["route_state"] != "ambush":
        problems.append(f"880710 route_state={hit['route_state']}（要求 ambush）")
    ranked = sorted(report["launch"],
                    key=lambda b: (b.get("launch_score") if b.get("launch_score") is not None else -1.0),
                    reverse=True)
    rank = next((i + 1 for i, b in enumerate(ranked) if b["code"] == "880710"), None)
    topn = report.get("launch_topn", 0)
    if not hit["in_output"]:
        problems.append(
            f"880710 in_output=False（要求 True；launch_score 排第 {rank}，"
            f"GOT_NODE2_LAUNCH_TOPN={topn} 截断——按计划该项不通过，"
            f"TOPN=0 可复现全量）"
        )
    return not problems, problems


def _report_discovery(as_of: date) -> int:
    report = cache_reconstruction(as_of)
    q = report["phase_data_quality"]
    print(f"== 缓存截面重建 {as_of} (kind={report['kind']}) ==")
    print(f"  快筛宇宙: {q['quick_universe_count']} / 有效 {q['quick_valid_count']} "
          f"/ 启动候选 {q['launch_candidates']} / 完整K线通过 {q['launch_after_kline']}")
    if q["unavailable_reasons"]:
        print(f"  通道不可用: {q['unavailable_reasons']}")
    print(f"  unresolved（名称缺失，不参与通过）: {len(report['unresolved'])}")
    for board in report["boards"]:
        print(f"  {board['code']} {board['name']:<10} phase={board['phase']:<9} "
              f"age={board['phase_age']} route={board['route_state']:<16} "
              f"in_output={board['in_output']} rank={board.get('rank')} score={board['launch_score']} "
              f"r5={board['r5']:.4f} r10={board['r10']:.4f} pct={board['r5_pct']:.3f} "
              f"dist={board['high20_dist']:.4f}")
    ok, problems = _check_880710(report)
    print()
    if ok:
        print("硬验收通过：880710 phase=launch / 1<=phase_age<=3 / "
              "route_state=ambush / in_output=True")
        return 0
    print("硬验收失败：")
    for problem in problems:
        print(f"  - {problem}")
    return 1


def _run_arm(mode: str, args, out_path: Path, workdir: Path) -> int:
    env = dict(os.environ)
    env["GOT_NODE2_LIFECYCLE"] = mode
    env["DATABASE_PATH"] = str(workdir / f"node2_ab_{mode}.db")
    cmd = [
        sys.executable, str(REPLAY_SCRIPT),
        "--start", args.start, "--end", args.end,
        "--json-out", str(out_path),
    ]
    if args.full_discovery:
        cmd.append("--full-discovery")
    if args.limit:
        cmd += ["--limit", str(args.limit)]
    if args.verbose:
        cmd.append("--verbose")
    print(f"[arm:{mode}] {' '.join(cmd)}")
    return subprocess.run(cmd, env=env, cwd=str(ROOT)).returncode


def _arm_criteria(day: dict) -> tuple[list[str], int]:
    """单日 enabled 臂验收；返回 (问题列表, 完整输入标记)。"""
    problems: list[str] = []
    boards = day.get("boards") or {}
    # 披露：补写行沿用前日 ambush 状态的数量（不计入通过判定）
    backfilled_ambush = sum(
        1 for row in boards.values()
        if not row.get("analyzed") and row.get("state") == "ambush"
    )
    q = day.get("phase_data_quality") or {}
    complete = (
        q.get("mode") == "enabled"
        and (q.get("quick_universe_count") or 0) >= 50
        and not q.get("unavailable_reasons")
    )

    for name, row in boards.items():
        # 「完整 board」= 当日有完整 K 线并参与截面（补写行不算）
        if row.get("analyzed") and row.get("state") in ROUTED_STATES and not row.get("phase"):
            problems.append(f"{name}: 路由 {row['state']} 但缺 LifecycleInfo")
        if row.get("hard_break") and row.get("phase") != "decline":
            problems.append(f"{name}: hard_break 但 phase={row.get('phase')}")
        if row.get("hard_break") and row.get("state") not in ("exhausted_mainline",):
            problems.append(f"{name}: hard_break 但 state={row.get('state')}")

    # 只比「当日 analyzed」的板块：补写行（K 线缺失/当日未发现）沿用前日 state，
    # 不代表 Node 2 输出（`result.ambush`），把它们算进来会产生假截断告警。
    launch_ambush = {
        row.get("code") for row in boards.values()
        if row.get("analyzed")
        and row.get("phase") == "launch" and row.get("route_state") == "ambush"
    }
    ambush_codes = set(day.get("ambush_codes") or [])
    topn = int(day.get("launch_topn") or 0)
    expected = min(len(launch_ambush), topn) if topn > 0 else len(launch_ambush)
    if complete and not ambush_codes.issubset(launch_ambush):
        problems.append(
            f"ambush 输出含非 launch∩ambush 板块 {sorted(ambush_codes - launch_ambush)}"
        )
    if complete and len(ambush_codes) != expected:
        problems.append(
            f"ambush 输出 {len(ambush_codes)} 条 != 期望 {expected}"
            f"（launch∩ambush {len(launch_ambush)} 条，TopN={topn or '不限'}）"
        )
    if not complete:
        problems.append("unresolved：快筛宇宙不完整/通道不可用（不计入通过样本）")
    day["backfilled_ambush_rows"] = backfilled_ambush
    return problems, int(complete)


def _compare(args) -> int:
    workdir = Path(tempfile.mkdtemp(prefix="node2_lifecycle_ab_"))
    legacy_out = workdir / "legacy.json"
    enabled_out = workdir / "enabled.json"
    try:
        rc_legacy = _run_arm("legacy", args, legacy_out, workdir)
        rc_enabled = _run_arm("enabled", args, enabled_out, workdir)
        if rc_legacy or rc_enabled:
            print(f"子进程失败: legacy={rc_legacy} enabled={rc_enabled}")
            return 1
        legacy = json.loads(legacy_out.read_text(encoding="utf-8"))
        enabled = json.loads(enabled_out.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    legacy_days = {d["date"]: d for d in legacy.get("days") or []}
    enabled_days = {d["date"]: d for d in enabled.get("days") or []}
    common_days = sorted(set(legacy_days) & set(enabled_days))
    complete_days = 0
    all_problems: list[str] = []
    for day_str in common_days:
        problems, complete = _arm_criteria(enabled_days[day_str])
        complete_days += complete
        if problems:
            all_problems.extend(f"{day_str}: {p}" for p in problems)

    payload = {
        "kind": "node2_lifecycle_ab",
        "start": args.start,
        "end": args.end,
        "full_discovery": bool(args.full_discovery),
        "common_days": common_days,
        "complete_enabled_days": complete_days,
        "legacy_days": legacy_days,
        "enabled_days": enabled_days,
        "problems": all_problems,
        "passed": complete_days >= 20 and not all_problems,
    }
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")
        print(f"结果写入: {out_path}")
    print(f"共同日期: {len(common_days)}，enabled 完整输入日: {complete_days}"
          f"（要求 ≥20）")
    if all_problems:
        print(f"问题 {len(all_problems)} 条：")
        for problem in all_problems[:30]:
            print(f"  - {problem}")
    else:
        print("enabled 臂验收通过：路由有相位 / ambush 输出 == min(launch∩ambush, TopN) "
              "（无静默截断，被截留痕）/ hard_break 一律 decline+exhausted / "
              "unresolved 未计入通过")
    return 0 if payload["passed"] else 1


def _diff_json(a_path: str, b_path: str) -> int:
    """legacy 前后对照：state/strength/buckets 逐字段一致。"""
    a = json.loads(Path(a_path).read_text(encoding="utf-8"))
    b = json.loads(Path(b_path).read_text(encoding="utf-8"))
    a_days = {d["date"]: d for d in a.get("days") or []}
    b_days = {d["date"]: d for d in b.get("days") or []}
    diffs: list[str] = []
    for day_str in sorted(set(a_days) & set(b_days)):
        a_boards = a_days[day_str].get("boards") or {}
        b_boards = b_days[day_str].get("boards") or {}
        for name in sorted(set(a_boards) | set(b_boards)):
            left = a_boards.get(name) or {}
            right = b_boards.get(name) or {}
            for field in ("state", "strength", "buckets"):
                if left.get(field) != right.get(field):
                    diffs.append(
                        f"{day_str} {name} {field}: {left.get(field)!r} != {right.get(field)!r}"
                    )
        if a_days[day_str].get("common") != b_days[day_str].get("common"):
            diffs.append(f"{day_str} common: {a_days[day_str].get('common')} != "
                         f"{b_days[day_str].get('common')}")
    print(f"对照日期: {len(set(a_days) & set(b_days))}，字段差异: {len(diffs)}")
    for diff in diffs[:50]:
        print(f"  - {diff}")
    return 0 if not diffs else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Node 2 生命周期离线 A/B 审计（非生产 shadow）")
    ap.add_argument("--date", default="", help="缓存截面重建日期（配合 --report-discovery）")
    ap.add_argument("--report-discovery", action="store_true")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--diff-json", nargs=2, metavar=("BEFORE", "AFTER"))
    ap.add_argument("--start", default="20260902")
    ap.add_argument("--end", default="20261103")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--full-discovery", action="store_true", default=False)
    ap.add_argument("--out", default="")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.diff_json:
        return _diff_json(*args.diff_json)
    if args.report_discovery:
        if not args.date:
            print("--report-discovery 需要 --date YYYYMMDD")
            return 2
        return _report_discovery(parse_day(args.date))
    if args.compare:
        return _compare(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
