#!/usr/bin/env python
"""Node 2 replay 单步保真度：用 DB 实际 prev_state，只检验当日输入与转移。

与全量 replay 的区别：不继承 replay 的历史状态，避免路径漂移放大。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.got.nodes.sector_analysis import SectorAnalysisNode  # noqa: E402
from src.got.schemas import MarketContext  # noqa: E402

DB = ROOT / "data" / "stock_analysis.db"
REPLAY = ROOT / "data" / "got_daily" / "_cache" / "node2_replay_2026.json"
CACHE = ROOT / "data" / "got_daily" / "_cache" / "board_kline_2026_cache.json"
DAILY = ROOT / "data" / "got_daily"


def main() -> int:
    rows = json.loads(REPLAY.read_text(encoding="utf-8"))
    by_key = {(r["code"], r["date"]): r for r in rows}

    con = sqlite3.connect(DB)
    db = con.execute(
        "select sector_code, tracking_date, state, dwell_days, exit_pressure_days "
        "from node2_sector_trends where tracking_date >= '2026-07-24' "
        "order by sector_code, tracking_date"
    ).fetchall()
    con.close()

    seq = defaultdict(list)
    for code, d, state, dwell, ep in db:
        if state:
            seq[code].append((d, state, int(dwell or 0), int(ep or 0)))

    raw = json.loads(CACHE.read_text(encoding="utf-8"))
    frames = {
        code: pd.DataFrame(
            {"close": rec.get("close") or []},
            index=pd.to_datetime(rec.get("dates") or []),
        )
        for code, rec in raw.items()
    }
    market: dict[str, MarketContext] = {}
    for d in sorted({r["date"] for r in rows}):
        p = DAILY / d.replace("-", "") / "market.json"
        if p.exists():
            try:
                market[d] = MarketContext.model_validate(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass

    out = []
    for code, items in seq.items():
        for i in range(1, len(items)):
            d, actual, _, _ = items[i]
            prev_d, prev_state, prev_dwell, prev_ep = items[i - 1]
            r = by_key.get((code, d))
            if r is None:
                continue
            is_def = bool(r.get("is_defensive"))
            df = frames.get(code)
            mctx = market.get(d)
            if df is not None and mctx is not None:
                sub = df.loc[: pd.Timestamp(d)]
                if len(sub) >= 20:
                    is_def = bool(
                        SectorAnalysisNode._check_defensive(
                            {"name": r["name"]}, mctx, sub, float(r["strength"]),
                        )
                    )
            state = SectorAnalysisNode._apply_state_machine(
                prev_state=prev_state,
                prev_dwell=prev_dwell,
                q=r["rrg"],
                rs_momentum=float(r["rs_momentum"]),
                strength_score=float(r["strength"]),
                price_chg_5d=r.get("price5"),
                above_ma20=bool(r.get("above_ma20")),
                high20_pos=float(r.get("high20_pos") or 1.0),
                overheat_level="normal",
                today_change=r.get("today_change"),
                score_drop=float(r.get("score_drop") or 0),
                is_ambush=bool(r.get("is_ambush")),
                is_defensive=is_def,
                exit_pressure=prev_ep,
                tier=r.get("tier"),
            )
            out.append((code, d, actual, state, prev_state))

    n = len(out)
    match = sum(1 for _, _, a, s, _ in out if a == s)
    print(f"one-step n={n} match={match}/{n} = {match/n:.1%}" if n else "no rows")
    by_actual = defaultdict(lambda: [0, 0])
    by_pair = defaultdict(int)
    for _, _, a, s, _ in out:
        by_actual[a][0] += 1
        by_pair[(a, s)] += 1
        if a == s:
            by_actual[a][1] += 1
    print(f"{'actual':<22}{'n':>7}{'match':>8}")
    for a, (cnt, m) in sorted(by_actual.items(), key=lambda x: -x[1][0]):
        print(f"{a:<22}{cnt:>7}{m/cnt:>8.0%}")
    print("\ntop confusion:")
    for (a, s), cnt in sorted(by_pair.items(), key=lambda x: -x[1])[:12]:
        if a != s:
            print(f"  actual={a:<20} replay={s:<20} n={cnt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
