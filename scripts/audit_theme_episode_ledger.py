#!/usr/bin/env python
"""离线审计：状态无关的主题 episode/age（只读归档，不写生产）。

目的
----
验证一个最小假设：不看 Node 2 的 ``state``，只用板块强度序列，能否算出
"这个主题处在第几段、第几天"，从而区分
  - 减速器 10-23（E16 当天被踢成 neutral，但主题其实已在第 4 天）
  - 短剧 10-29（被 16 条 cap 截掉，但其实是 10-28 开始的新一段第 2 天）
  - 短剧 11-03（同一段第 5 天，不是"首次出现"）

方法
----
默认规则（双阈值 + 确认）：
  - 强度 >= ENTER（80）→ 进入/开始一段
  - 强度 < EXIT（75）连续 CONFIRM（3）次 → 结束当前段
  - 段内峰值滚动；结束日 = 第一次低于 EXIT 的前一个观测
  - age 按交易日历计，不按观测条数

同时跑参数网格（ENTER/EXIT/CONFIRM），用 5 条硬检查筛选：
  1. 减速器 10-23 age >= 2
  2. 减速器 10-23 与 10-20 同段
  3. 短剧 10-29 是新段（start >= 10-27）
  4. 短剧 10-29 与 10-13 不同段
  5. 短剧 11-03 与 10-29 同段且 age >= 3

用法
----
  python scripts/audit_theme_episode_ledger.py
  python scripts/audit_theme_episode_ledger.py --start 20260902 --end 20261103
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.core.trading_calendar import is_market_open  # noqa: E402

DAILY = ROOT / "data" / "got_daily"

# 桶优先级：同一板块可能同时出现在多个桶，取语义最强的一个
BUCKETS: list[tuple[str, str]] = [
    ("active_mainline", "active"),
    ("cooling_down", "cooling"),
    ("exhausted_mainline", "exhausted"),
    ("ambush", "ambush"),
    ("near_miss_ambush", "near_miss"),
    ("defensive_rotation", "defensive"),
    ("top_by_score", "top"),
    ("industry_top", "industry"),
]


@dataclass
class Obs:
    d: date
    strength: float
    state: str


@dataclass
class Episode:
    idx: int
    start: date
    peak: float
    peak_date: date
    end: date | None = None          # 段内最后一个观测（破位前）
    break_date: date | None = None   # 首次低于 EXIT 的日期
    confirm_date: date | None = None # 连续确认日

    @property
    def label(self) -> str:
        return f"ep{self.idx}"


@lru_cache(maxsize=None)
def _is_td(d: date) -> bool:
    try:
        return bool(is_market_open("cn", d))
    except Exception:
        return d.weekday() < 5


def trade_age(start: date, end: date) -> int:
    """含首尾的交易日数；缺归档日也照常计入。"""
    n = 0
    cur = start
    while cur <= end:
        if _is_td(cur):
            n += 1
        cur += timedelta(days=1)
    return n


def parse_day(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def iter_days(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def load_day(j: dict) -> dict[str, tuple[float, str]]:
    """单日全板块：strength_lookup 打底，桶状态按优先级覆盖。"""
    out: dict[str, tuple[float, str]] = {}
    for name, s in (j.get("strength_lookup") or {}).items():
        try:
            out[(name or "").strip("\x00")] = (float(s), "neutral")
        except (TypeError, ValueError):
            continue
    for key, label in BUCKETS:
        items = j.get(key) or []
        if key == "active_mainline":
            for group in items:
                for m in group.get("members") or []:
                    name = (m.get("name") or "").strip("\x00")
                    val = m.get("strength_score")
                    if name and val is not None and out.get(name, (0, "neutral"))[1] == "neutral":
                        out[name] = (float(val), label)
        else:
            for m in items:
                name = (m.get("name") or "").strip("\x00")
                val = m.get("strength_score")
                if name and val is not None and out.get(name, (0, "neutral"))[1] == "neutral":
                    out[name] = (float(val), label)
    return out


def load_series(board: str, start: date, end: date) -> list[Obs]:
    out: list[Obs] = []
    for d in iter_days(start, end):
        p = DAILY / d.strftime("%Y%m%d") / "sector.json"
        if not p.exists():
            continue
        day = load_day(json.loads(p.read_text(encoding="utf-8")))
        if board in day:
            s, st = day[board]
            out.append(Obs(d, s, st))
    return out


def load_universe(start: date, end: date) -> dict[str, list[Obs]]:
    data: dict[str, list[Obs]] = {}
    for d in iter_days(start, end):
        p = DAILY / d.strftime("%Y%m%d") / "sector.json"
        if not p.exists():
            continue
        for name, (s, st) in load_day(json.loads(p.read_text(encoding="utf-8"))).items():
            data.setdefault(name, []).append(Obs(d, s, st))
    return data


def segment(series: list[Obs], enter: float, exit_: float, confirm: int) -> list[Episode]:
    """双阈值分段；不读 state。"""
    eps: list[Episode] = []
    cur: Episode | None = None
    exit_run = 0
    prev: Obs | None = None

    for ob in series:
        if cur is None:
            if ob.strength >= enter:
                cur = Episode(
                    idx=len(eps) + 1, start=ob.d,
                    peak=ob.strength, peak_date=ob.d,
                )
                eps.append(cur)
                exit_run = 0
        else:
            if ob.strength > cur.peak:
                cur.peak = ob.strength
                cur.peak_date = ob.d
            if ob.strength < exit_:
                exit_run += 1
                if exit_run == 1:
                    cur.break_date = ob.d
                    cur.end = prev.d if prev is not None else ob.d
                if exit_run >= confirm:
                    cur.confirm_date = ob.d
                    cur = None
                    exit_run = 0
            else:
                # 未确认的破位：段继续，清掉临时 break/end
                exit_run = 0
                cur.break_date = None
                cur.end = None
        prev = ob

    if cur is not None and series:
        cur.end = series[-1].d
    return eps


def ep_at(eps: list[Episode], d: date) -> tuple[Episode | None, int | None]:
    for ep in eps:
        end = ep.end or date.max
        if ep.start <= d <= end:
            return ep, trade_age(ep.start, d)
    return None, None


def print_timeline(board: str, series: list[Obs], eps: list[Episode]) -> None:
    print(f"\n== {board} ==")
    print(f"{'日期':<12}{'强度':>7}  {'状态':<10}{'段':<5}{'age':>4}  事件")
    notes: dict[date, str] = {}
    for e in eps:
        notes[e.start] = "start"
        if e.end:
            notes.setdefault(e.end, "end")
        if e.break_date:
            notes[e.break_date] = "break"
        if e.confirm_date:
            notes[e.confirm_date] = "confirmed"
    for ob in series:
        ep, age = ep_at(eps, ob.d)
        print(
            f"{ob.d.isoformat():<12}{ob.strength:>7.1f}  {ob.state:<10}"
            f"{(ep.label if ep else '-'):<5}{(age if age is not None else '-'):>4}  "
            f"{notes.get(ob.d, '')}"
        )

    def _desc(e: Episode) -> str:
        s = f"{e.label} {e.start}→{e.end} peak={e.peak:.1f}@{e.peak_date}"
        if e.break_date:
            s += f" break={e.break_date}"
        if e.confirm_date:
            s += f" confirm={e.confirm_date}"
        return s

    print("  episodes: " + ("; ".join(_desc(e) for e in eps) or "none"))


def check_params(
    series_map: dict[str, list[Obs]], enter: float, exit_: float, confirm: int
) -> tuple[list[str], dict[str, tuple[Episode | None, int | None]]]:
    eps_map = {b: segment(s, enter, exit_, confirm) for b, s in series_map.items()}
    reducer = eps_map.get("减速器", [])
    drama = eps_map.get("短剧游戏", [])

    r23 = ep_at(reducer, date(2026, 10, 23))
    r20 = ep_at(reducer, date(2026, 10, 20))
    r15 = ep_at(reducer, date(2026, 10, 15))

    d29 = ep_at(drama, date(2026, 10, 29))
    d13 = ep_at(drama, date(2026, 10, 13))
    d03 = ep_at(drama, date(2026, 11, 3))

    failed: list[str] = []

    # 1) 10-23 不是"首次出现"
    if not (r23[0] is not None and (r23[1] or 0) >= 2):
        failed.append("减速器10-23_age<2")

    # 2) 10-23 与 10-20 同段
    if not (r23[0] is not None and r20[0] is not None and r23[0].label == r20[0].label):
        failed.append("减速器10-23≠10-20段")

    # 3) 短剧 10-29 是新段（10-27 之后开始）
    if not (
        d29[0] is not None
        and d29[0].start >= date(2026, 10, 27)
    ):
        failed.append("短剧10-29非新段")

    # 4) 10-29 与 10-13 不同段
    if not (
        d29[0] is not None
        and d13[0] is not None
        and d29[0].label != d13[0].label
    ):
        failed.append("短剧10-29=10-13段")

    # 5) 11-03 与 10-29 同段且 age>=3
    if not (
        d03[0] is not None
        and d29[0] is not None
        and d03[0].label == d29[0].label
        and (d03[1] or 0) >= 3
    ):
        failed.append("短剧11-03非同段/age<3")

    return failed, {
        "减速器10-23": r23,
        "减速器10-20": r20,
        "减速器10-15": r15,
        "短剧10-13": d13,
        "短剧10-29": d29,
        "短剧11-03": d03,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="离线审计主题 episode/age")
    ap.add_argument("--start", default="20260902")
    ap.add_argument("--end", default="20261103")
    ap.add_argument("--boards", nargs="+", default=["短剧游戏", "减速器"])
    ap.add_argument("--enter", type=float, default=80.0)
    ap.add_argument("--exit", dest="exit_", type=float, default=75.0)
    ap.add_argument("--confirm", type=int, default=3)
    ap.add_argument("--no-grid", action="store_true")
    ap.add_argument("--universe", action="store_true", help="跑全市场分段稳定性统计")
    ap.add_argument("--top", type=int, default=10, help="最碎板块展示数量")
    args = ap.parse_args()

    start, end = parse_day(args.start), parse_day(args.end)
    series_map = {b: load_series(b, start, end) for b in args.boards}

    # 数据覆盖
    present = {
        p.name for p in DAILY.iterdir()
        if p.is_dir() and len(p.name) == 8 and p.name.isdigit()
        and (p / "sector.json").exists()
    }
    missing_td = [
        d.isoformat() for d in iter_days(start, end)
        if _is_td(d) and d.strftime("%Y%m%d") not in present
    ]
    print(f"归档区间: {start} → {end}")
    print(f"有 sector.json 的日期: {sum(1 for d in iter_days(start, end) if d.strftime('%Y%m%d') in present)} 天")
    print(f"区间内缺 sector.json 的交易日: {', '.join(missing_td) or '无'}")
    for b, s in series_map.items():
        print(f"  {b}: {len(s)} 个观测，{s[0].d if s else '-'} → {s[-1].d if s else '-'}")

    # 默认参数时间线
    print(f"\n默认规则: ENTER>={args.enter:g}, EXIT<{args.exit_:g} 连续{args.confirm}次，不看 state")
    for b in args.boards:
        print_timeline(b, series_map[b], segment(series_map[b], args.enter, args.exit_, args.confirm))

    if args.no_grid:
        return 0

    if args.universe:
        print("\n== 全市场稳定性（默认参数）==")
        universe = load_universe(start, end)
        rows = []
        total_eps = 0
        in_ep_days = 0
        total_days = 0
        dist = {1: 0, 2: 0, 3: 0}
        for name, series in universe.items():
            eps = segment(series, args.enter, args.exit_, args.confirm)
            total_eps += len(eps)
            total_days += len(series)
            for ob in series:
                ep, _ = ep_at(eps, ob.d)
                if ep is not None:
                    in_ep_days += 1
            if eps:
                rows.append((len(eps), name, len(series)))
                dist[min(len(eps), 3)] += 1
        print(f"  板块数: {len(universe)}，有 episode: {len(rows)}，总 episode: {total_eps}")
        if total_days:
            print(f"  板块-日: {total_days}，落在 episode 内: {in_ep_days} ({in_ep_days/total_days:.1%})")
        print(f"  每板块 episode 数分布: 1段={dist[1]} 2段={dist[2]} 3+段={dist[3]}")
        if rows:
            counts = sorted(r[0] for r in rows)
            print(f"  每板块 episode 数中位: {counts[len(counts)//2]}，最大: {counts[-1]}")
        print(f"  最碎 top{args.top}:")
        for n, name, obs in sorted(rows, reverse=True)[: args.top]:
            print(f"    {name:<12} episodes={n:<3} obs={obs}")

    # 参数网格
    print("\n== 参数敏感性（5 条硬检查）==")
    print(f"{'ENTER':>6}{'EXIT':>6}{'CONF':>6}  结果   失败项")
    rows = []
    for enter in (78.0, 80.0, 82.0):
        for exit_ in (70.0, 72.0, 75.0, 78.0):
            for confirm in (1, 2, 3):
                failed, _ = check_params(series_map, enter, exit_, confirm)
                rows.append((len(failed), enter, exit_, confirm, failed))
    for n_failed, enter, exit_, confirm, failed in sorted(rows):
        verdict = "PASS" if n_failed == 0 else f"{5 - n_failed}/5"
        print(f"{enter:>6.0f}{exit_:>6.0f}{confirm:>6d}  {verdict:<6} {','.join(failed) or '-'}")

    # 默认参数的关键读数
    failed, reads = check_params(series_map, args.enter, args.exit_, args.confirm)
    print("\n== 关键读数（默认参数）==")
    for name, (ep, age) in reads.items():
        print(f"  {name}: {'-' if ep is None else ep.label}  age={age if age is not None else '-'}")
    print("  硬检查: " + ("全部通过" if not failed else f"失败 {failed}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
