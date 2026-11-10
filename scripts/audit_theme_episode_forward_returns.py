#!/usr/bin/env python
"""前向收益验证：状态无关 episode/age 是否带来增量信息（只读）。

口径
----
- episode/age：复用 ``audit_theme_episode_ledger`` 的双阈值分段（默认 80/75/3），不看 state
- 收益：``data/got_daily/_cache/board_kline_cache.json`` 板块指数 close
- 超额：同日全部缓存板块均值（与文档事件研究同口径，但宇宙是 250 个缓存板块）
- 统计：按日期簇 bootstrap（重采样日期，不重采样行）

输出
----
1. 按 episode 分组（age 1-2 / 3-5 / 6+ / 无段）的 T+3/T+5 超额
2. 按 state 分组（active/ambush/top/neutral...）
3. 在 ``top``（10-23/10-29/11-03 所在输出桶）内，按 episode 分组的增量
4. 关键对比：early - no_ep、early - late、top_early - top_no_ep、active - no_ep

用法
----
  python scripts/audit_theme_episode_forward_returns.py
  python scripts/audit_theme_episode_forward_returns.py --enter 78 --exit 75 --confirm 3
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from audit_theme_episode_ledger import (  # noqa: E402
    ep_at,
    iter_days,
    load_universe,
    parse_day,
    segment,
)

CACHE = ROOT / "data" / "got_daily" / "_cache" / "board_kline_cache.json"
DB = ROOT / "data" / "stock_analysis.db"


def load_cache() -> dict[str, dict]:
    return json.loads(CACHE.read_text(encoding="utf-8"))


def load_name_code(cache_codes: set[str]) -> dict[str, str]:
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "select sector_name, sector_code, count(*) c "
            "from node2_sector_trends group by sector_name, sector_code"
        ).fetchall()
    finally:
        con.close()
    best: dict[str, tuple[str, int]] = {}
    for name, code, cnt in rows:
        name = (name or "").strip("\x00")
        if not name or code not in cache_codes:
            continue
        if name not in best or cnt > best[name][1]:
            best[name] = (code, int(cnt))
    return {n: c for n, (c, _) in best.items()}


def build_rows(
    universe: dict,
    name2code: dict[str, str],
    cache: dict[str, dict],
    enter: float,
    exit_: float,
    confirm: int,
    min_obs: int,
) -> list[dict]:
    rows: list[dict] = []
    for name, series in universe.items():
        if len(series) < min_obs:
            continue
        code = name2code.get(name)
        if not code or code not in cache:
            continue
        bars = cache[code]
        idx = {d: i for i, d in enumerate(bars["dates"])}
        closes = bars["closes"]
        eps = segment(series, enter, exit_, confirm)
        for ob in series:
            i = idx.get(ob.d.isoformat())
            if i is None:
                continue
            ep, age = ep_at(eps, ob.d)
            r3 = closes[i + 3] / closes[i] - 1 if i + 3 < len(closes) and closes[i] else None
            r5 = closes[i + 5] / closes[i] - 1 if i + 5 < len(closes) and closes[i] else None
            rows.append({
                "board": name, "code": code, "date": ob.d, "d": ob.d.isoformat(),
                "state": ob.state, "in_ep": ep is not None, "age": age,
                "ep": ep.label if ep else None, "r3": r3, "r5": r5,
            })
    return rows


def add_excess(rows: list[dict], h: int) -> None:
    by_date: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        v = r.get(f"r{h}")
        if v is not None:
            by_date[r["d"]].append(v)
    means = {d: sum(v) / len(v) for d, v in by_date.items() if v}
    for r in rows:
        v = r.get(f"r{h}")
        if v is not None:
            r[f"x{h}"] = v - means[r["d"]]


def ep_group(r: dict) -> str:
    if not r["in_ep"]:
        return "NO_EP"
    age = r["age"] or 0
    if age <= 2:
        return "EP_age_1-2"
    if age <= 5:
        return "EP_age_3-5"
    return "EP_age_6+"


def load_flows(start: date, end: date) -> dict[str, tuple[dict, dict]]:
    """{date: (1d, 3d) board->net flow}，读 capital.json。"""
    out: dict[str, tuple[dict, dict]] = {}
    for d in iter_days(start, end):
        p = ROOT / "data" / "got_daily" / d.strftime("%Y%m%d") / "capital.json"
        if not p.exists():
            continue
        j = json.loads(p.read_text(encoding="utf-8"))
        sf = (j.get("evidence") or {}).get("sector_flow") or {}
        out[d.isoformat()] = (
            sf.get("sector_flow_all") or {},
            sf.get("sector_flow_all_3d") or {},
        )
    return out


def add_setup(rows: list[dict], flows: dict[str, tuple[dict, dict]]) -> None:
    """早段 + 资金确认。1d/3d 分别置标，便于比较。"""
    for r in rows:
        f1, f3 = flows.get(r["d"], ({}, {}))
        r["f1"] = f1.get(r["board"])
        r["f3"] = f3.get(r["board"])
        if not r["in_ep"] or (r["age"] or 0) > 2:
            r["setup"] = "-"
            continue
        if r["f1"] is None or r["f3"] is None:
            r["setup"] = "UNKNOWN"
            continue
        a = r["f1"] > 0
        b = r["f3"] > 0
        if a and b:
            r["setup"] = "ARMED_both"
        elif a:
            r["setup"] = "ARMED_1d"
        elif b:
            r["setup"] = "ARMED_3d"
        else:
            r["setup"] = "UNARMED"


def setup_group(r: dict) -> str:
    if r.get("setup") in {"ARMED_both", "ARMED_1d", "ARMED_3d", "UNARMED", "UNKNOWN"}:
        return r["setup"]
    if r["in_ep"]:
        return "EP_late"
    return "NO_EP"


def load_visible(start: date, end: date) -> dict[str, tuple[set[str], set[str]]]:
    """{date: (候选池板块名集合, 有 finalist 的板块名集合)}。"""
    out: dict[str, tuple[set[str], set[str]]] = {}
    for d in iter_days(start, end):
        p = ROOT / "data" / "got_daily" / d.strftime("%Y%m%d") / "candidate_pool.json"
        if not p.exists():
            continue
        j = json.loads(p.read_text(encoding="utf-8"))
        names: set[str] = set()
        finals: set[str] = set()
        for it in j.get("universe") or []:
            sn = (it.get("sector_name") or "").strip("\x00")
            if not sn:
                continue
            names.add(sn)
            if it.get("is_finalist"):
                finals.add(sn)
        out[d.isoformat()] = (names, finals)
    return out


def add_visible(rows: list[dict], vis: dict[str, tuple[set[str], set[str]]]) -> None:
    for r in rows:
        names, finals = vis.get(r["d"], (set(), set()))
        r["visible"] = r["board"] in names
        r["finalist"] = r["board"] in finals


def state_group(r: dict) -> str:
    return r["state"] if r["state"] in {
        "active", "ambush", "top", "neutral", "exhausted", "cooling",
        "near_miss", "defensive", "industry",
    } else "other"


def agg(rows: list[dict], h: int) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    for r in rows:
        v = r.get(f"x{h}")
        if v is None:
            continue
        a = out[r["d"]]
        a[0] += v
        a[1] += 1
    return out


def boot_mean(a: dict[str, list[float]], iters: int) -> tuple[float, float, float]:
    dates = list(a)
    if not dates:
        return 0.0, 0.0, 0.0
    means = []
    for _ in range(iters):
        s = n = 0.0
        for d in random.choices(dates, k=len(dates)):
            s += a[d][0]
            n += a[d][1]
        if n:
            means.append(s / n)
    means.sort()
    if not means:
        return 0.0, 0.0, 0.0
    return (
        means[len(means) // 2],
        means[int(len(means) * 0.025)],
        means[min(len(means) - 1, int(len(means) * 0.975))],
    )


def boot_diff(
    a: dict[str, list[float]], b: dict[str, list[float]], iters: int
) -> tuple[float, float, float]:
    dates = sorted(set(a) | set(b))
    if not dates:
        return 0.0, 0.0, 0.0
    diffs = []
    for _ in range(iters):
        sa = na = sb = nb = 0.0
        for d in random.choices(dates, k=len(dates)):
            if d in a:
                sa += a[d][0]
                na += a[d][1]
            if d in b:
                sb += b[d][0]
                nb += b[d][1]
        if na and nb:
            diffs.append(sa / na - sb / nb)
    diffs.sort()
    if not diffs:
        return 0.0, 0.0, 0.0
    return (
        diffs[len(diffs) // 2],
        diffs[int(len(diffs) * 0.025)],
        diffs[min(len(diffs) - 1, int(len(diffs) * 0.975))],
    )


def fmt_pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:+.2f}%"


def print_table(
    title: str, rows: list[dict], h: int, key_fn, iters: int
) -> None:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get(f"x{h}") is not None:
            groups[key_fn(r)].append(r)
    print(f"\n== {title} | T+{h} ==")
    print(f"{'组':<14}{'n':>6}{'dates':>7}{'超额均值':>11}{'胜率':>8}  {'中位/95%CI':<28}")
    for name in sorted(groups):
        rs = groups[name]
        vals = [r[f"x{h}"] for r in rs if r.get(f"x{h}") is not None]
        a = agg(rs, h)
        med, lo, hi = boot_mean(a, iters)
        win = sum(1 for v in vals if v > 0) / len(vals) if vals else 0.0
        print(
            f"{name:<14}{len(vals):>6}{len(a):>7}{fmt_pct(sum(vals)/len(vals)):>11}"
            f"{win:>8.0%}  [{fmt_pct(lo)}, {fmt_pct(hi)}]"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="episode/age 前向收益验证")
    ap.add_argument("--start", default="20260902")
    ap.add_argument("--end", default="20261103")
    ap.add_argument("--enter", type=float, default=80.0)
    ap.add_argument("--exit", dest="exit_", type=float, default=75.0)
    ap.add_argument("--confirm", type=int, default=3)
    ap.add_argument("--min-obs", type=int, default=10)
    ap.add_argument("--iters", type=int, default=2000)
    ap.add_argument("--setup", action="store_true", help="加入资金确认 setup 分组")
    ap.add_argument("--visibility", action="store_true", help="加入候选池可见性统计")
    args = ap.parse_args()

    random.seed(20261105)
    start, end = parse_day(args.start), parse_day(args.end)
    cache = load_cache()
    name2code = load_name_code(set(cache))
    universe = load_universe(start, end)
    rows = build_rows(
        universe, name2code, cache, args.enter, args.exit_, args.confirm, args.min_obs
    )
    for h in (3, 5):
        add_excess(rows, h)
    if args.setup:
        add_setup(rows, load_flows(start, end))
        early = [r for r in rows if r["in_ep"] and (r["age"] or 0) <= 2]
        hit = sum(1 for r in early if r.get("f1") is not None or r.get("f3") is not None)
        print(f"资金流命中（早段）: {hit}/{len(early)}")
    if args.visibility:
        add_visible(rows, load_visible(start, end))

    print(f"区间: {start} → {end}")
    print(f"缓存板块: {len(cache)}，名称匹配: {len(name2code)}，样本行: {len(rows)}")
    print(f"episode 参数: ENTER>={args.enter:g}, EXIT<{args.exit_:g}, CONFIRM={args.confirm}")
    n_dates = len({r["d"] for r in rows})
    print(f"日期数: {n_dates}，板块数: {len({r['board'] for r in rows})}")

    for h in (3, 5):
        print_table("按 episode 分组", rows, h, ep_group, args.iters)
        print_table("按 state 分组", rows, h, state_group, args.iters)

    # top 桶内部：episode 是否带来增量
    top_rows = [r for r in rows if r["state"] == "top"]
    for h in (3, 5):
        print_table("top 桶内按 episode 分组", top_rows, h, ep_group, args.iters)

    if args.setup:
        for h in (3, 5):
            print_table("按 setup 分组（早段+资金）", rows, h, setup_group, args.iters)
            print_table("top 桶内按 setup 分组", top_rows, h, setup_group, args.iters)

    if args.visibility:
        def vis_table(title: str, pool: list[dict], key_fn) -> None:
            groups: dict[str, list[dict]] = defaultdict(list)
            for r in pool:
                groups[key_fn(r)].append(r)
            print(f"\n== {title} ==")
            print(f"{'组':<14}{'n':>6}{'可见':>7}{'覆盖率':>8}{'finalist':>9}{'率':>8}")
            for name in sorted(groups):
                rs = groups[name]
                n = len(rs)
                v = sum(1 for r in rs if r["visible"])
                f = sum(1 for r in rs if r["finalist"])
                print(f"{name:<14}{n:>6}{v:>7}{v/n:>8.0%}{f:>9}{f/n:>8.0%}")

        vis_table("可见性：按 episode 分组", rows, ep_group)
        vis_table("可见性：top 桶内按 episode 分组", top_rows, ep_group)

        print("\n== 三个案例的可见性 ==")
        print(f"{'案例':<18}{'候选池':>8}{'finalist':>10}")
        for board, d in [("减速器", "2026-10-23"), ("短剧游戏", "2026-10-29"),
                         ("短剧游戏", "2026-11-03")]:
            hit = [r for r in rows if r["board"] == board and r["d"] == d]
            if hit:
                r = hit[0]
                print(f"{board+' '+d:<18}{'是' if r['visible'] else '否':>8}"
                      f"{'是' if r['finalist'] else '否':>10}")
            else:
                print(f"{board+' '+d:<18}{'-':>8}{'-':>10}")

    # 关键对比
    print("\n== 关键对比（同日簇 bootstrap）==")
    print(f"{'对比':<36}{'T+3':>10}{'CI':>24}  {'T+5':>10}{'CI':>24}")
    comparisons = [
        ("EP_age_1-2 − NO_EP", "EP_age_1-2", "NO_EP", rows),
        ("EP_age_1-2 − EP_age_6+", "EP_age_1-2", "EP_age_6+", rows),
        ("top_EP_1-2 − top_NO_EP", "EP_age_1-2", "NO_EP", top_rows),
        ("EP_all − NO_EP", "EP", "NO_EP", rows),
        ("active − NO_EP", "active", "NO_EP", rows),
    ]
    for label, ga, gb, pool in comparisons:
        if ga == "EP":
            ra = [r for r in pool if r["in_ep"] and r.get("x3") is not None]
        elif ga == "active":
            ra = [r for r in pool if r["state"] == "active" and r.get("x3") is not None]
        else:
            ra = [r for r in pool if ep_group(r) == ga]
        if gb == "NO_EP":
            rb = [r for r in pool if not r["in_ep"]]
        elif gb == "active":
            rb = [r for r in pool if r["state"] == "active"]
        else:
            rb = [r for r in pool if ep_group(r) == gb]
        d3, l3, h3 = boot_diff(agg(ra, 3), agg(rb, 3), args.iters)
        d5, l5, h5 = boot_diff(agg(ra, 5), agg(rb, 5), args.iters)
        print(
            f"{label:<36}{fmt_pct(d3):>10}  [{fmt_pct(l3)}, {fmt_pct(h3)}]"
            f"  {fmt_pct(d5):>10}  [{fmt_pct(l5)}, {fmt_pct(h5)}]"
        )

    if args.setup:
        print("\n== setup 对比（同日簇 bootstrap）==")
        print(f"{'对比':<36}{'T+3':>10}{'CI':>24}  {'T+5':>10}{'CI':>24}")

        def sel(label: str, pool: list[dict]) -> list[dict]:
            if label == "NO_EP":
                return [r for r in pool if not r["in_ep"]]
            if label == "EP_late":
                return [r for r in pool if r["in_ep"] and (r["age"] or 0) > 2]
            return [r for r in pool if r.get("setup") == label]

        for label, ga, gb, pool in [
            ("ARMED_both − UNARMED", "ARMED_both", "UNARMED", rows),
            ("ARMED_1d − UNARMED", "ARMED_1d", "UNARMED", rows),
            ("ARMED_both − EP_late", "ARMED_both", "EP_late", rows),
            ("ARMED_both − NO_EP", "ARMED_both", "NO_EP", rows),
            ("top_ARMED_both − top_NO_EP", "ARMED_both", "NO_EP", top_rows),
        ]:
            ra, rb = sel(ga, pool), sel(gb, pool)
            d3, l3, h3 = boot_diff(agg(ra, 3), agg(rb, 3), args.iters)
            d5, l5, h5 = boot_diff(agg(ra, 5), agg(rb, 5), args.iters)
            print(
                f"{label:<36}{fmt_pct(d3):>10}  [{fmt_pct(l3)}, {fmt_pct(h3)}]"
                f"  {fmt_pct(d5):>10}  [{fmt_pct(l5)}, {fmt_pct(h5)}]"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
