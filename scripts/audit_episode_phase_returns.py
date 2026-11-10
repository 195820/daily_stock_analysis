#!/usr/bin/env python
"""按“周期相位”而非“决策日固定 T+h”统计板块收益（只读归档）。

背景：短线题材周期通常 7–20 个交易日（启动 1–5 天、发酵/主升 3–15 天、高潮/分歧 3–10 天），
实战主张“吃中段 5 天”。本脚本把 episode 的 age 作为相位：
- early: 段内 age=1 入场，持有 5 个交易日；
- mid  : age=3 入场，持有 5 个交易日（“吃鱼身”）；
- late : age=6 入场，持有 5 个交易日。

超额 = 板块 5 日收益 − 同日全缓存板块均值；CI 为日期簇 bootstrap。
只读归档 + 板块 K 线缓存，不产生任何交易信号。

用法
----
    python scripts/audit_episode_phase_returns.py --start 20260724 --end 20261103
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from audit_theme_episode_ledger import (  # noqa: E402
    load_universe,
    parse_day,
    segment,
    trade_age,
)
from audit_theme_episode_forward_returns import (  # noqa: E402
    fmt_pct,
    load_cache,
    load_name_code,
)
from src.got.backtest_discipline import date_cluster_bootstrap_ci  # noqa: E402

HOLD = 5
PHASES = ((1, "early_age1"), (3, "mid_age3"), (6, "late_age6"))
LAUNCH_PHASES = ((0, "early_d1-5"), (2, "mid_d3-7"), (7, "late_d8-12"))


def _stats(rows: list[dict], key: str = "x") -> dict:
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return {"n": 0, "mean": None, "win": None, "ci": None, "dates": 0}
    by_date: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get(key) is not None:
            by_date[r["entry_date"]].append(r[key])
    return {
        "n": len(vals),
        "mean": sum(vals) / len(vals),
        "win": sum(1 for v in vals if v > 0) / len(vals),
        "ci": date_cluster_bootstrap_ci(by_date),
        "dates": len(by_date),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="episode 相位收益")
    ap.add_argument("--start", default="20260724")
    ap.add_argument("--end", default="20261103")
    ap.add_argument("--enter", type=float, default=80.0)
    ap.add_argument("--exit", type=float, default=75.0)
    ap.add_argument("--confirm", type=int, default=3)
    ap.add_argument("--mode", choices=("episode", "launch"), default="episode",
                    help="episode=按段内 age；launch=按 strength>=80 首次启动日对齐")
    args = ap.parse_args()

    start, end = parse_day(args.start), parse_day(args.end)
    universe = load_universe(start, end)
    cache = load_cache()
    name2code = load_name_code(set(cache))

    rows: list[dict] = []
    ep_lengths: list[int] = []
    peak_ages: list[int] = []
    boards = 0
    skipped_no_cache = 0
    launches = 0

    for name, series in universe.items():
        code = name2code.get(name)
        bars = cache.get(code) if code else None
        if not bars:
            skipped_no_cache += 1
            continue
        idx = {d: i for i, d in enumerate(bars["dates"])}
        closes = bars["closes"]
        if args.mode == "launch":
            for k, ob in enumerate(series):
                if ob.strength < args.enter:
                    continue
                # 首次启动：前 5 个观测均未达阈值
                if any(x.strength >= args.enter for x in series[max(0, k - 5):k]):
                    continue
                launches += 1
                i = idx.get(ob.d.isoformat())
                if i is None:
                    continue
                for off, label in LAUNCH_PHASES:
                    j = i + off
                    if j + HOLD >= len(closes) or not closes[j]:
                        continue
                    rows.append({
                        "board": name, "phase": label, "entry_age": off + 1,
                        "entry_date": bars["dates"][j],
                        "r": closes[j + HOLD] / closes[j] - 1,
                    })
            continue
        eps = segment(series, args.enter, args.exit, args.confirm)
        if eps:
            boards += 1
        for ep in eps:
            ep_end = ep.end or series[-1].d
            ep_lengths.append(trade_age(ep.start, ep_end))
            peak_ages.append(trade_age(ep.start, ep.peak_date))
            obs = [o for o in series if ep.start <= o.d <= ep_end]
            for age_target, label in PHASES:
                hit = next((o for o in obs if trade_age(ep.start, o.d) == age_target), None)
                if hit is None:
                    continue
                i = idx.get(hit.d.isoformat())
                if i is None or i + HOLD >= len(closes) or not closes[i]:
                    continue
                rows.append({
                    "board": name, "phase": label, "entry_age": age_target,
                    "entry_date": hit.d.isoformat(),
                    "r": closes[i + HOLD] / closes[i] - 1,
                })

    # 同日全缓存板块的 5 日收益均值 → 超额
    date_means: dict[str, float] = {}
    by_date_vals: dict[str, list[float]] = defaultdict(list)
    for code, bars in cache.items():
        idx = {d: i for i, d in enumerate(bars["dates"])}
        closes = bars["closes"]
        for d, i in idx.items():
            if i + HOLD < len(closes) and closes[i]:
                by_date_vals[d].append(closes[i + HOLD] / closes[i] - 1)
    for d, vals in by_date_vals.items():
        if vals:
            date_means[d] = sum(vals) / len(vals)
    for r in rows:
        m = date_means.get(r["entry_date"])
        r["x"] = (r["r"] - m) if m is not None else None

    print(f"区间 {start} → {end}；mode={args.mode}；板块 {len(universe)}"
          f"（无缓存 {skipped_no_cache}）；有段板块 {boards}；段数 {len(ep_lengths)}；"
          f"启动数 {launches}")
    if ep_lengths:
        s = sorted(ep_lengths)
        print(f"段长（交易日）：中位 {s[len(s)//2]}，均值 {sum(s)/len(s):.1f}，"
              f"≤5={sum(1 for v in s if v<=5)} / 6-10={sum(1 for v in s if 6<=v<=10)} / "
              f"11-15={sum(1 for v in s if 11<=v<=15)} / >15={sum(1 for v in s if v>15)}")
        p = sorted(peak_ages)
        print(f"峰值 age：中位 {p[len(p)//2]}，均值 {sum(p)/len(p):.1f}")
        print("段长分布：", dict(sorted(Counter(ep_lengths).items())))

    print("\n== 相位收益（持有 5 个交易日）==")
    print(f"{'相位':<12}{'n':>5}{'dates':>7}{'5日收益':>10}{'超额':>10}{'胜率':>7}{'95%CI':>22}")
    phase_defs = LAUNCH_PHASES if args.mode == "launch" else PHASES
    for age_target, label in phase_defs:
        sub = [r for r in rows if r["phase"] == label]
        raw = _stats(sub, "r")
        ex = _stats(sub, "x")
        print(f"{label:<12}{ex['n']:>5}{ex['dates']:>7}{fmt_pct(raw['mean']):>10}"
              f"{fmt_pct(ex['mean']):>10}{ex['win']:>7.0%}  [{fmt_pct(ex['ci'][0])}, {fmt_pct(ex['ci'][1])}]")

    print("\n== 对照：日龄分组（同段内，持有 5 日）==")
    for age in (1, 2, 3, 4, 5, 6, 7, 8):
        sub = [r for r in rows if r["entry_age"] == age]
        if not sub:
            continue
        ex = _stats(sub, "x")
        raw = _stats(sub, "r")
        print(f"  age={age:<2} n={ex['n']:<4} 5日收益={fmt_pct(raw['mean'])} "
              f"超额={fmt_pct(ex['mean'])} 胜率={ex['win']:.0%} "
              f"CI=[{fmt_pct(ex['ci'][0])}, {fmt_pct(ex['ci'][1])}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
