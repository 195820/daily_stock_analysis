#!/usr/bin/env python
"""Node 2 实际输出回测：按 state/strength 统计 T+3/T+5 的 +3% 胜率。

不重放 Node 2；直接读生产库 node2_sector_trends 的 state/strength，
用板块收盘价算前向收益。只读，不写生产。

用法：
  python scripts/audit_node2_state_winrate.py
  python scripts/audit_node2_state_winrate.py --start 20260817 --end 20261030
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

DB = ROOT / "data" / "stock_analysis.db"
CACHE = ROOT / "data" / "_replay" / "board_ohlcv_cache.json"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, center - half), min(1.0, center + half)


def pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:+.2f}%"


def group_state(state: str | None) -> str:
    if not state:
        return "unknown"
    return {
        "active_mainline": "active",
        "high_position_cooling": "cooling",
        "exhausted_mainline": "exhausted",
        "ambush": "ambush",
        "defensive_rotation": "defensive",
        "neutral": "neutral",
    }.get(state, state)


def strength_bucket(s: float) -> str:
    if s >= 80:
        return ">=80"
    if s >= 60:
        return "60-80"
    return "<60"


def main() -> int:
    ap = argparse.ArgumentParser(description="Node 2 实际输出 T+3/T+5 胜率")
    ap.add_argument("--start", default="2026-08-17")
    ap.add_argument("--end", default="2026-10-30")
    args = ap.parse_args()

    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "select sector_name, tracking_date, state, strength_score "
            "from node2_sector_trends where tracking_date between ? and ?",
            (args.start, args.end),
        ).fetchall()
        name2code = {}
        for name, code in con.execute(
            "select sector_name, sector_code from node2_sector_trends "
            "group by sector_name, sector_code"
        ):
            name2code.setdefault((name or "").strip("\x00"), code)
    finally:
        con.close()

    idx = {code: {d: i for i, d in enumerate(b["dates"])} for code, b in cache.items()}
    rows_out = []
    for name, d, state, strength in rows:
        name = (name or "").strip("\x00")
        code = name2code.get(name)
        if not code or code not in cache:
            continue
        i = idx[code].get(d)
        if i is None:
            continue
        closes = cache[code]["close"]
        r3 = closes[i + 3] / closes[i] - 1 if i + 3 < len(closes) and closes[i] else None
        r5 = closes[i + 5] / closes[i] - 1 if i + 5 < len(closes) and closes[i] else None
        rows_out.append({
            "date": d, "name": name, "state": group_state(state),
            "strength": float(strength or 0), "r3": r3, "r5": r5,
        })

    # 同日全缓存均值（超额用）
    for h in (3, 5):
        by_date = defaultdict(list)
        for r in rows_out:
            v = r[f"r{h}"]
            if v is not None:
                by_date[r["date"]].append(v)
        means = {d: sum(v) / len(v) for d, v in by_date.items() if v}
        for r in rows_out:
            v = r[f"r{h}"]
            r[f"x{h}"] = (v - means[r["date"]]) if v is not None else None

    def report(title: str, groups: dict[str, list[dict]]) -> None:
        print(f"\n== {title} ==")
        print(f"{'组':<16}{'n':>6}{'dates':>7}{'T+3均值':>9}{'T+3>0':>7}"
              f"{'T+3>=3%':>8}{'T+5均值':>9}{'T+5>0':>7}{'T+5>=3%':>8}")
        for name in sorted(groups):
            rs = groups[name]
            r3 = [r["r3"] for r in rs if r["r3"] is not None]
            r5 = [r["r5"] for r in rs if r["r5"] is not None]
            if not r5:
                continue
            m3 = sum(r3) / len(r3) if r3 else None
            m5 = sum(r5) / len(r5)
            w3 = sum(1 for v in r3 if v > 0) / len(r3) if r3 else 0
            w5 = sum(1 for v in r5 if v > 0) / len(r5)
            h3 = sum(1 for v in r3 if v >= 0.03) / len(r3) if r3 else 0
            h5 = sum(1 for v in r5 if v >= 0.03) / len(r5)
            print(
                f"{name:<16}{len(r5):>6}{len({r['date'] for r in rs}):>7}"
                f"{pct(m3):>9}{w3:>7.0%}{h3:>8.0%}"
                f"{pct(m5):>9}{w5:>7.0%}{h5:>8.0%}"
            )

    by_state = defaultdict(list)
    by_strength = defaultdict(list)
    by_state_strength = defaultdict(list)
    for r in rows_out:
        by_state[r["state"]].append(r)
        by_strength[strength_bucket(r["strength"])].append(r)
        by_state_strength[(r["state"], strength_bucket(r["strength"]))].append(r)

    print(f"区间: {args.start} → {args.end}")
    print(f"DB 行: {len(rows)}，可用样本: {len(rows_out)}，板块: {len({r['name'] for r in rows_out})}")
    report("按 state", by_state)
    report("按 strength", by_strength)
    report("state × strength", by_state_strength)

    # 关键状态 +3% 胜率的 Wilson CI
    print("\n== T+5 >=+3% 胜率（Wilson 95% CI）==")
    for name in ["active", "ambush", "neutral", "defensive", "exhausted", "cooling"]:
        rs = [r["r5"] for r in by_state.get(name, []) if r["r5"] is not None]
        if not rs:
            continue
        k = sum(1 for v in rs if v >= 0.03)
        lo, hi = wilson(k, len(rs))
        print(f"  {name:<10} {k}/{len(rs)} = {k/len(rs):.1%}  [{lo:.1%}, {hi:.1%}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
