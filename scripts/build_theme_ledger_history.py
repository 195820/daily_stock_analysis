#!/usr/bin/env python
"""P1 离线重建：按日 as-of 生成主题账本快照（只读，不写生产决策）。

目的（实施稿 §8-P1 验收）
--------------------------
1. 用同一套纯函数把归档区间重建成逐日快照，可逐字节复现；
2. 快照只含 `<= trade_date` 的事实（as-of 无前视）；
3. 三个校准案例可核对：减速器 10-23 / 短剧 10-29 / 短剧 11-03；
4. 状态与输出桶成员分列：10-29 短剧「内部 ambush、输出被 cap」必须可还原。

两种强度口径
------------
- `archive`：`sector.json.strength_lookup`（全板块）+ 桶状态覆盖 —— 与验证报告口径一致；
- `db`：`node2_sector_trends` 内部 state/strength —— 生产运行时口径，缺日不补。

用法
----
    python scripts/build_theme_ledger_history.py --start 20261008 --end 20261103 --mode archive
    python scripts/build_theme_ledger_history.py --start 20261008 --end 20261103 --mode db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.core.trading_calendar import is_market_open  # noqa: E402
from src.got.theme_ledger import (  # noqa: E402
    Observation,
    build_observations,
    build_snapshot,
    build_theme_watch,
    derive_episodes,
    derive_setups,
    snapshot_digest,
    trend_map_from_cache,
)

DAILY = ROOT / "data" / "got_daily"
DB = ROOT / "data" / "stock_analysis.db"
OUT_ROOT = ROOT / "data" / "got_daily" / "theme_ledger_history"

BUCKET_FLAGS = {
    "active_mainline": "active",
    "ambush": "ambush",
    "near_miss_ambush": "near_miss",
    "top_by_score": "top",
}
BUCKET_STATE_PRIORITY = [
    ("active_mainline", "active_mainline"),
    ("ambush", "ambush"),
    ("near_miss_ambush", "neutral"),
    ("defensive_rotation", "defensive_rotation"),
    ("cooling_down", "high_position_cooling"),
    ("exhausted_mainline", "exhausted_mainline"),
    ("top_by_score", "neutral"),
]


def trading_days(start: date, end: date) -> List[str]:
    out, cur = [], start
    while cur <= end:
        if is_market_open("cn", cur):
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def load_name_code() -> Dict[str, str]:
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "SELECT sector_name, sector_code FROM node2_sector_trends "
            "GROUP BY sector_name ORDER BY COUNT(*) DESC"
        ).fetchall()
    finally:
        con.close()
    out: Dict[str, str] = {}
    for name, code in rows:
        if name and code and name not in out:
            out[str(name)] = str(code)
    return out


def archive_day_payload(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def bucket_items(payload: Dict[str, Any], bucket: str) -> List[Dict[str, Any]]:
    items = payload.get(bucket) or []
    out: List[Dict[str, Any]] = []
    for item in items:
        if bucket == "active_mainline" and item.get("members"):
            for member in item["members"]:
                out.append(member)
        else:
            out.append(item)
    return out


def membership_of(payload: Dict[str, Any]) -> Dict[str, Dict[str, bool]]:
    out: Dict[str, Dict[str, bool]] = {}
    for bucket, flag in BUCKET_FLAGS.items():
        for item in bucket_items(payload, bucket):
            for key in (item.get("code"), item.get("name")):
                if key:
                    out.setdefault(str(key), {})[flag] = True
    return out


def states_from_archive(
    payload: Dict[str, Any], name_code: Dict[str, str]
) -> Tuple[List[Dict[str, Any]], int]:
    strengths = payload.get("strength_lookup") or {}
    states: Dict[str, str] = {}
    for bucket, state in BUCKET_STATE_PRIORITY:
        for item in bucket_items(payload, bucket):
            name = str(item.get("name") or "").strip()
            if name and name not in states:
                # 桶条目自带的 state 是当时的事实（如 10-29 短剧 top_by_score 但 state=ambush），
                # 只有缺字段时才用桶→状态的先验映射，禁止用映射覆盖事实。
                states[name] = str(item.get("state") or state)
    rows, unmapped = [], 0
    for name, strength in strengths.items():
        code = name_code.get(str(name))
        if not code:
            unmapped += 1
            continue
        if strength is None:
            continue
        rows.append({
            "sector_code": code,
            "sector_name": str(name),
            "strength_score": float(strength),
            "state": states.get(str(name), ""),
        })
    return rows, unmapped


def states_from_db(day: str) -> List[Dict[str, Any]]:
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "SELECT sector_code, sector_name, strength_score, state "
            "FROM node2_sector_trends WHERE tracking_date = ?",
            (day,),
        ).fetchall()
    finally:
        con.close()
    return [
        {"sector_code": str(c), "sector_name": str(n), "strength_score": s, "state": str(st) or ""}
        for c, n, s, st in rows
    ]


def news_refs(payload: Dict[str, Any], day: str) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    news: Dict[str, List[str]] = {}
    catalyst: Dict[str, List[str]] = {}
    for idx, event in enumerate(payload.get("major_events") or []):
        event_id = str(event.get("event_id") or f"news:{day}:{idx}")
        for sector in event.get("affected_sectors") or []:
            news.setdefault(str(sector).strip(), []).append(event_id)
    cal = payload.get("catalyst_calendar") or {}
    for event in cal.get("events") or []:
        event_id = str(event.get("event_id") or "")
        sectors = list(event.get("affected_sectors") or [])
        if event.get("sector"):
            sectors.append(event["sector"])
        for sector in sectors:
            if event_id and sector:
                catalyst.setdefault(str(sector).strip(), []).append(event_id)
    return news, catalyst


def build_mode(mode: str, days: List[str], calendar: List[str], out_dir: Path) -> Dict[str, Any]:
    name_code = load_name_code()
    all_obs: List[Observation] = []
    per_day_diag: Dict[str, Dict[str, Any]] = {}
    db_missing = []
    for day in days:
        archive_path = DAILY / day.replace("-", "") / "sector.json"
        db_rows = states_from_db(day)
        if mode == "db":
            state_rows = db_rows
            if not state_rows:
                db_missing.append(day)
                continue
            payload = archive_day_payload(archive_path) if archive_path.exists() else {}
            membership = membership_of(payload) if payload else {}
            unmapped = 0
        else:
            if not archive_path.exists():
                continue
            payload = archive_day_payload(archive_path)
            state_rows, unmapped = states_from_archive(payload, name_code)
            membership = membership_of(payload)
        news, catalyst = news_refs(payload, day) if payload else ({}, {})
        trend_map = trend_map_from_cache([r["sector_code"] for r in state_rows], as_of=day)
        observations, diag = build_observations(
            day,
            state_rows=state_rows,
            membership=membership,
            news_refs=news,
            catalyst_refs=catalyst,
            trend_map=trend_map,
            run_id=f"offline-{mode}",
        )
        diag["unmapped_by_name"] = unmapped
        per_day_diag[day] = diag
        all_obs.extend(observations)

    out_dir.mkdir(parents=True, exist_ok=True)
    digests: Dict[str, str] = {}
    case_rows: Dict[str, Any] = {}
    for day in calendar:
        as_of_obs = [o for o in all_obs if o.trade_date <= day]
        if not as_of_obs:
            continue
        episodes, index = derive_episodes(as_of_obs, calendar)
        setups = derive_setups(as_of_obs, index, calendar)
        watch = build_theme_watch(episodes, setups, as_of_obs, calendar, day)
        snap = build_snapshot(
            day,
            observations=as_of_obs,
            episodes=episodes,
            setups=setups,
            watch=watch,
            calendar=calendar,
            mode=f"offline-{mode}",
            run_id=f"offline-{mode}",
            diagnostics=per_day_diag.get(day, {}),
        )
        digests[day] = snapshot_digest(snap)
        path = out_dir / f"theme_ledger_{day.replace('-', '')}.json"
        path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
        if day in ("2026-10-23", "2026-10-29", "2026-11-03"):
            case_rows[day] = _case_view(snap, day)

    summary = {
        "mode": mode,
        "days": len(digests),
        "calendar_len": len(calendar),
        "observations": len(all_obs),
        "db_missing_days": db_missing,
        "cases": case_rows,
        "digests": digests,
        "diagnostics": per_day_diag,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _case_view(snap: Dict[str, Any], day: str) -> Dict[str, Any]:
    def pick(code: str) -> Dict[str, Any]:
        obs = next((o for o in snap["observations"] if o["board_code"] == code), None)
        # 优先取「覆盖当日」的未关闭段，避免多段并存时取到错误段
        ep = next(
            (e for e in snap["episodes"]
             if e["board_code"] == code
             and e["start_date"] <= day
             and (e["end_date"] is None or e["end_date"] >= day)),
            None,
        )
        setups = [s for s in snap["setups"] if s["board_code"] == code]
        return {
            "observation": obs,
            "episode": ep,
            "setups": {s["setup_type"]: s["status"] for s in setups},
        }

    return {"880675_减速器": pick("880675"), "880692_短剧游戏": pick("880692")}


def main() -> int:
    ap = argparse.ArgumentParser(description="P1 主题账本离线重建")
    ap.add_argument("--start", default="20261008")
    ap.add_argument("--end", default="20261103")
    ap.add_argument("--mode", choices=("archive", "db"), default="archive")
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y%m%d").date()
    end = datetime.strptime(args.end, "%Y%m%d").date()
    calendar = trading_days(start, end)
    days = [d for d in calendar if (DAILY / d.replace("-", "") / "portfolio_decision.json").exists()
            or (DAILY / d.replace("-", "") / "sector.json").exists()]
    out_dir = Path(args.out_dir) if args.out_dir else OUT_ROOT / args.mode

    summary = build_mode(args.mode, days, calendar, out_dir)
    # 复现校验：同输入再跑一次，逐日 digest 必须一致
    again = build_mode(args.mode, days, calendar, out_dir / "_repro")
    repro = summary["digests"] == again["digests"]

    print(f"模式 {args.mode}：{summary['days']} 天 / {summary['observations']} 条观测 / "
          f"缺 DB 日 {len(summary['db_missing_days'])}")
    for day, view in summary["cases"].items():
        for key, value in view.items():
            ep = value["episode"] or {}
            obs = value["observation"] or {}
            print(f"  {day} {key}: state={obs.get('state')} strength={obs.get('strength')} "
                  f"in_ambush={obs.get('in_ambush_output')} in_top={obs.get('in_top_output')} "
                  f"episode={ep.get('episode_id')} age={ep.get('age_days')} status={ep.get('status')} "
                  f"setups={value['setups']}")
    print(f"复现：{'PASS' if repro else 'FAIL'}；摘要 {out_dir / 'summary.json'}")
    return 0 if repro else 3


if __name__ == "__main__":
    raise SystemExit(main())
