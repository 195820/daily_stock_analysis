#!/usr/bin/env python
"""E16 定向反事实：defensive_rotation → neutral 的 board-day，落到 neutral 分支会怎样。

只读归档，不重跑 Node 2。覆盖范围：当日仍出现在某个输出桶（多为 top_by_score）的板块；
未出现在任何桶的 neutral 板块没有 detail，不在样本内。

四个变体
--------
A baseline           : 实际 neutral（E16 直接返回）
B E16 fall-through   : 走 neutral 分支，保留 is_ambush 检查
C B + 强 leading 豁免: strength>=80 且 q=leading 时跳过 is_ambush
D B + 完全解耦       : 移除 is_ambush 检查（E13 式解耦）

用法
----
  python scripts/audit_node2_state_counterfactual.py
  python scripts/audit_node2_state_counterfactual.py --start 20260817 --end 20261103
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from audit_theme_episode_ledger import BUCKETS, iter_days, parse_day  # noqa: E402
from audit_theme_episode_forward_returns import (  # noqa: E402
    agg,
    boot_diff,
    boot_mean,
    fmt_pct,
    load_cache,
    load_name_code,
)

DAILY = ROOT / "data" / "got_daily"
AMBUSH_FLOOR = 0.30
LEADING_EXEMPT_STRENGTH = 80.0
HORIZONS = (3, 5, 10, 15)


def load_details_day(j: dict) -> dict[str, dict]:
    """单日板块 detail；桶优先级同 Node 2 输出。"""
    out: dict[str, dict] = {}
    for key, _label in BUCKETS:
        items = j.get(key) or []
        if key == "active_mainline":
            for group in items:
                for m in group.get("members") or []:
                    name = (m.get("name") or "").strip("\x00")
                    if name and name not in out:
                        out[name] = m
        else:
            for m in items:
                name = (m.get("name") or "").strip("\x00")
                if name and name not in out:
                    out[name] = m
    return out


def load_sequences(start: date, end: date) -> dict[str, list[tuple[str, dict]]]:
    seq: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for d in iter_days(start, end):
        p = DAILY / d.strftime("%Y%m%d") / "sector.json"
        if not p.exists():
            continue
        day = load_details_day(json.loads(p.read_text(encoding="utf-8")))
        for name, det in day.items():
            seq[name].append((d.isoformat(), det))
    return seq


def load_db_states() -> dict[str, list[tuple[str, str]]]:
    """全量 board-day 的 state；归档桶会截断，prev_state 必须回 DB 取。"""
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    db = ROOT / "data" / "stock_analysis.db"
    if not db.exists():
        return out
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "select sector_name, tracking_date, state from node2_sector_trends"
        ).fetchall()
    finally:
        con.close()
    for name, d, st in rows:
        name = (name or "").strip("\x00")
        if name and st:
            out[name].append((str(d), st))
    for v in out.values():
        v.sort()
    return out


def add_forward(rows: list[dict], cache: dict, name2code: dict[str, str]) -> None:
    """给 E16 行补 T+3/T+5 原始收益与同日全缓存超额。"""
    idx = {code: {d: i for i, d in enumerate(bars["dates"])} for code, bars in cache.items()}
    for r in rows:
        code = name2code.get(r["board"])
        r["code"] = code
        for h in HORIZONS:
            r[f"r{h}"] = None
        if not code or code not in cache:
            continue
        bars = cache[code]
        i = idx[code].get(r["date"])
        if i is None:
            continue
        closes = bars["closes"]
        for h in HORIZONS:
            if i + h < len(closes) and closes[i]:
                r[f"r{h}"] = closes[i + h] / closes[i] - 1

    dates = sorted({r["date"] for r in rows})
    for h in HORIZONS:
        means: dict[str, float] = {}
        for d in dates:
            vals = []
            for code, bars in cache.items():
                i = idx[code].get(d)
                if i is None:
                    continue
                closes = bars["closes"]
                if i + h < len(closes) and closes[i]:
                    vals.append(closes[i + h] / closes[i] - 1)
            if vals:
                means[d] = sum(vals) / len(vals)
        for r in rows:
            v = r.get(f"r{h}")
            r[f"x{h}"] = (v - means[r["date"]]) if (v is not None and r["date"] in means) else None


def print_forward(rows: list[dict], iters: int) -> None:
    groups = {
        "C_recovered": [r for r in rows if r["cf_c"] == "active_mainline"],
        "C_neutral": [r for r in rows if r["cf_c"] != "active_mainline"],
        "B_active": [r for r in rows if r["cf_b"] == "active_mainline"],
        "B_ambush": [r for r in rows if r["cf_b"] == "ambush"],
    }
    print("\n== C 恢复组 vs E16-neutral：前向收益（同日全缓存超额）==")
    for h in HORIZONS:
        print(f"-- T+{h} --")
        print(f"{'组':<14}{'n':>6}{'dates':>7}{'均值':>9}{'胜率':>7}{'95%CI':>22}")
        for name, rs in groups.items():
            vals = [r[f"x{h}"] for r in rs if r.get(f"x{h}") is not None]
            a = agg(rs, h)
            _med, lo, hi = boot_mean(a, iters)
            win = sum(1 for v in vals if v > 0) / len(vals) if vals else 0.0
            mean = fmt_pct(sum(vals) / len(vals) if vals else None)
            print(f"{name:<14}{len(rs):>6}{len(a):>7}{mean:>9}{win:>7.0%}"
                  f"  [{fmt_pct(lo)}, {fmt_pct(hi)}]")
        c = groups["C_recovered"]
        n = groups["C_neutral"]
        d, lo, hi = boot_diff(agg(c, h), agg(n, h), iters)
        print(f"  C_recovered − C_neutral T+{h}: {fmt_pct(d)}  [{fmt_pct(lo)}, {fmt_pct(hi)}]\n")


def broken(det: dict) -> bool:
    bs = det.get("break_signals") or {}
    price5 = bs.get("price_chg_5d")
    above = bs.get("above_ma20")
    high20 = bs.get("high20_pos")
    overheat = bs.get("overheat_level")
    score_drop = bs.get("score_drop")
    return bool(
        (price5 is not None and price5 <= 0)
        or (not above)
        or (overheat == "exhausted" and (high20 or 0) < 0.60)
        or ((score_drop or 0) >= 20)
    )


def entry_ok(det: dict) -> bool:
    strength = float(det.get("strength_score") or 0)
    tier = det.get("tier")
    if tier in ("S", "A"):
        return strength > 60
    return strength >= 80


def quadrant(det: dict) -> str | None:
    return (det.get("rrg") or {}).get("quadrant")


def ambush_score(det: dict) -> float:
    return float((det.get("indicators") or {}).get("ambush_score_v2") or 0)


def cf_active(det: dict) -> str:
    return "active_mainline" if (not broken(det) and quadrant(det) == "leading" and entry_ok(det)) else "neutral"


def cf_b(det: dict) -> str:
    """E16 fall-through，保留 neutral 分支的 is_ambush 检查。"""
    if ambush_score(det) >= AMBUSH_FLOOR:
        return "ambush"
    return cf_active(det)


def cf_c(det: dict) -> str:
    """E16 fall-through；强 leading 不被 ambush 拦截。"""
    if ambush_score(det) >= AMBUSH_FLOOR and not (
        (det.get("strength_score") or 0) >= LEADING_EXEMPT_STRENGTH
        and quadrant(det) == "leading"
    ):
        return "ambush"
    return cf_active(det)


def cf_d(det: dict) -> str:
    """E16 fall-through；完全移除 is_ambush 检查。"""
    return cf_active(det)


def load_attack_power(start: date, end: date) -> dict[str, float]:
    """当日 market.json 的 attack_power（缺失按 0.5 中性）。"""
    out: dict[str, float] = {}
    for d in iter_days(start, end):
        p = DAILY / d.strftime("%Y%m%d") / "market.json"
        if not p.exists():
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            ap = (j.get("market_state") or {}).get("attack_power")
            if ap is not None:
                out[d.isoformat()] = float(ap)
        except Exception:
            continue
    return out


def _max_mainline(ap: float) -> int:
    if ap < 0.15:
        return 1
    if ap < 0.30:
        return 2
    if ap < 0.50:
        return 3
    if ap < 0.70:
        return 4
    return 5


def attach_caps(rows: list[dict], seq: dict, ap_by_day: dict[str, float]) -> None:
    """反事实路由 → 当日输出容量（active 分类≤3 + 主线 family≤attack_power 档；ambush Track1≤16）。

    只读归档事实；用于回答「修完之后是否真的能走到下游」，不产生交易信号。
    """
    day: dict[str, dict[str, dict]] = defaultdict(dict)
    for name, items in seq.items():
        for d, det in items:
            key = d if isinstance(d, str) else d.isoformat()
            day[key][name] = det

    for r in rows:
        d = r["date"]
        boards = day.get(d, {})
        # active
        act = {n: det for n, det in boards.items() if det.get("state") == "active_mainline"}
        if r["cf_c"] == "active_mainline":
            act[r["board"]] = r["det"]
        ranked = sorted(act.items(), key=lambda x: float(x[1].get("strength_score") or 0), reverse=True)
        names = [n for n, _ in ranked]
        r["active_rank"] = names.index(r["board"]) + 1 if r["board"] in names else None
        groups: dict[str, float] = {}
        for n, det in ranked:
            g = det.get("frame_group") or n
            groups[g] = max(groups.get(g, 0.0), float(det.get("strength_score") or 0))
        g_ranked = sorted(groups.items(), key=lambda x: -x[1])
        r["group_rank"] = next(
            (i + 1 for i, (g, _) in enumerate(g_ranked)
             if g == (r["det"].get("frame_group") or r["board"])), None
        )
        cap = _max_mainline(ap_by_day.get(d, 0.5))
        r["active_cap"] = cap
        r["active_survive"] = bool(
            r["active_rank"] is not None and r["active_rank"] <= 3
            and r["group_rank"] is not None and r["group_rank"] <= cap
        )
        # ambush
        amb = {n: det for n, det in boards.items() if det.get("state") == "ambush"}
        if r["cf_c"] == "ambush":
            amb[r["board"]] = r["det"]
        if r["cf_b"] == "ambush":
            amb.setdefault(r["board"], r["det"])
        ar = sorted(amb.items(), key=lambda x: ambush_score(x[1]), reverse=True)
        anames = [n for n, _ in ar]
        r["ambush_rank"] = anames.index(r["board"]) + 1 if r["board"] in anames else None
        r["ambush_survive"] = bool(r["ambush_rank"] is not None and r["ambush_rank"] <= 16)


def print_caps(rows: list[dict]) -> None:
    print("\n== 容量存活（反事实路由 → 当日输出 cap）==")
    for label, key in (("B(保留 is_ambush)", "cf_b"), ("C(强 leading 豁免)", "cf_c")):
        amb = [r for r in rows if r[key] == "ambush"]
        act = [r for r in rows if r[key] == "active_mainline"]
        print(f"  {label}: ambush={len(amb)} 存活(Track1≤16)={sum(1 for r in amb if r['ambush_survive'])}；"
              f"active={len(act)} 存活(分类≤3 & family≤attack_power)={sum(1 for r in act if r['active_survive'])}")
    # 前向超额按存活分组
    for label, key, eff in (
        ("C_active", "cf_c", "active_survive"), ("C_ambush", "cf_c", "ambush_survive"),
        ("B_ambush", "cf_b", "ambush_survive"),
    ):
        target = "active_mainline" if "active" in label.lower() else "ambush"
        sub = [r for r in rows if r[key] == target]
        for flag, name in ((True, "survive"), (False, "blocked")):
            parts = []
            for h in HORIZONS:
                vals = [r[f"x{h}"] for r in sub if r.get(eff) is flag and r.get(f"x{h}") is not None]
                if vals:
                    parts.append(f"T+{h} n={len(vals)} {fmt_pct(sum(vals)/len(vals))}")
            if parts:
                print(f"  {label}_{name}: " + " | ".join(parts))


def main() -> int:
    ap = argparse.ArgumentParser(description="E16 定向反事实")
    ap.add_argument("--start", default="20260902")
    ap.add_argument("--end", default="20261103")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--iters", type=int, default=1000)
    ap.add_argument("--no-forward", action="store_true", help="跳过前向收益")
    ap.add_argument("--no-caps", action="store_true", help="跳过容量存活")
    args = ap.parse_args()

    start, end = parse_day(args.start), parse_day(args.end)
    seq = load_sequences(start, end)

    rows: list[dict] = []
    db_states = load_db_states()
    for name, items in seq.items():
        prev_state = None
        db_hist = db_states.get(name, [])
        for d, det in items:
            actual = det.get("state")
            db_prev = None
            for dd, st in db_hist:
                if dd < d:
                    db_prev = st
                else:
                    break
            effective_prev = db_prev if db_prev is not None else prev_state
            if effective_prev == "defensive_rotation" and actual == "neutral":
                rows.append({
                    "date": d, "d": d, "board": name, "det": det,
                    "strength": float(det.get("strength_score") or 0),
                    "q": quadrant(det), "tier": det.get("tier"),
                    "broken": broken(det), "entry_ok": entry_ok(det),
                    "amb": ambush_score(det), "is_ambush": ambush_score(det) >= AMBUSH_FLOOR,
                    "cf_b": cf_b(det), "cf_c": cf_c(det), "cf_d": cf_d(det),
                    "prev_src": "db" if db_prev is not None else "archive",
                })
            prev_state = actual

    print(f"区间: {start} → {end}")
    print(f"板块数: {len(seq)}，E16 board-day（prev=defensive_rotation, actual=neutral）: {len(rows)}")
    if not rows:
        return 0
    src = Counter(r["prev_src"] for r in rows)
    print(f"prev_state 来源: DB={src.get('db', 0)} archive={src.get('archive', 0)}")

    if not args.no_forward:
        cache = load_cache()
        add_forward(rows, cache, load_name_code(set(cache)))
    if not args.no_caps:
        attach_caps(rows, seq, load_attack_power(start, end))

    ready = [r for r in rows if r["q"] == "leading" and not r["broken"] and r["entry_ok"]]
    print(f"入口条件满足（leading + not broken + entry_ok）: {len(ready)}")
    print(f"is_ambush=True（ambush_score>=0.30）: {sum(1 for r in rows if r['is_ambush'])}")

    print("\n== 变体计数 ==")
    for key, label in [("cf_b", "B 保留 is_ambush"), ("cf_c", "C 强 leading 豁免"), ("cf_d", "D 完全解耦")]:
        c = Counter(r[key] for r in rows)
        print(f"  {label:<18} active={c.get('active_mainline',0):<4} "
              f"ambush={c.get('ambush',0):<4} neutral={c.get('neutral',0)}")

    diff = [r for r in rows if r["cf_b"] == "ambush" and r["cf_c"] == "active_mainline"]
    print(f"\n== B→C 差异（is_ambush 拦住了强 leading）: {len(diff)} ==")
    for r in sorted(diff, key=lambda x: (x["date"], -x["strength"]))[: args.top * 2]:
        print(f"  {r['date']} {r['board']:<10} strength={r['strength']:.1f} amb={r['amb']:.2f}")

    print(f"\n  E16 中 broken=True: {sum(1 for r in rows if r['broken'])}，"
          f"entry_ok=False: {sum(1 for r in rows if not r['entry_ok'])}")

    if not args.no_caps:
        print_caps(rows)
    if not args.no_forward:
        print_forward(rows, args.iters)

    print("\n== 强度分层 ==")
    for lo, hi, label in [(80, 1e9, ">=80"), (60, 80, "60-80"), (0, 60, "<60")]:
        sub = [r for r in rows if lo <= r["strength"] < hi]
        if not sub:
            continue
        c = Counter(r["cf_c"] for r in sub)
        print(f"  {label:<6} n={len(sub):<4} C: active={c.get('active_mainline',0):<4} "
              f"ambush={c.get('ambush',0):<4} neutral={c.get('neutral',0)}")

    print(f"\n== 日期分布 top{args.top} ==")
    for d, n in Counter(r["date"] for r in rows).most_common(args.top):
        sub = [r for r in rows if r["date"] == d]
        c = Counter(r["cf_c"] for r in sub)
        print(f"  {d} n={n:<3} C: active={c.get('active_mainline',0):<3} "
              f"ambush={c.get('ambush',0):<3} neutral={c.get('neutral',0)}")

    print("\n== 三个案例 ==")
    targets = [("减速器", "2026-10-23"), ("短剧游戏", "2026-10-29"), ("短剧游戏", "2026-11-03")]
    for board, d in targets:
        hit = [r for r in rows if r["board"] == board and r["date"] == d]
        if hit:
            r = hit[0]
            print(f"  {board} {d}: E16 -> B={r['cf_b']} C={r['cf_c']} D={r['cf_d']} "
                  f"(strength={r['strength']:.1f}, q={r['q']}, broken={r['broken']}, "
                  f"amb={r['amb']:.2f})")
        else:
            # 非 E16：打印当日实际状态与入口条件
            items = seq.get(board) or []
            det = next((x for dd, x in items if dd == d), None)
            if det is None:
                print(f"  {board} {d}: 无当日 detail")
                continue
            print(f"  {board} {d}: 非 E16（actual={det.get('state')}, "
                  f"strength={float(det.get('strength_score') or 0):.1f}, "
                  f"q={quadrant(det)}, broken={broken(det)}, entry_ok={entry_ok(det)}, "
                  f"amb={ambush_score(det):.2f}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
