#!/usr/bin/env python
"""Node 2 推荐集合的**候选板指数**前向收益诊断（dev 窗口，非 P4 判定）。

回答的问题不是「全宇宙涨跌」，而是：新版本当天真正推荐出去的板块，接下来
3/5/10 个交易日相对当日全宇宙是否更好；以及**只取前 K 条**时读数如何。

口径（全部显式，禁止含糊）：
- 对象：板块指数（`board_ohlcv_cache.json`），不是个股、不是组合净值。
- 推荐集合：A/B 产物里各臂当日的 `ambush_codes`（enabled=launch 相位全量；
  legacy=旧双轨 ≤16）。
- Top-K 排序：enabled 按 `launch_score` 降序；legacy 按 Node 2 实际输出顺序
  （track1 按 `ambush_score_v2` 降序后接 track2）。
- 基准：当日缓存内同口径可算前向收益的全部板块等权均值（消掉窗口 beta）。
- 入场：T+1 开盘（GOT 收盘后运行，次日开盘才可执行）；出场：T+N 收盘。
- 成本：未计（板块指数无费用），故只做相对比较、不下收益结论。
- 样本：`data/_replay/ab/node2_lifecycle_backtest.json` 的共同日期；T+N 越界的板块剔除。

用法：
  python scripts/audit_node2_launch_forward_returns.py
  python scripts/audit_node2_launch_forward_returns.py --md-out data/_replay/ab/launch_forward.md
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

CACHE = ROOT / "data" / "_replay" / "board_ohlcv_cache.json"
AB = ROOT / "data" / "_replay" / "ab" / "node2_lifecycle_backtest.json"
HORIZONS = (3, 5, 10)
TOPK = (1, 2, 3, 4, 5, 8, 16)
CAP = 16  # legacy 的 Node 2 容量（AMBUSH_TRACK1_MAX）


def _index(bars: dict) -> dict[str, int]:
    return {d: i for i, d in enumerate(bars.get("dates") or [])}


def forward_return(bars: dict, idx: dict[str, int], day: str, n: int) -> float | None:
    """T+1 开盘 → T+N 收盘（%）。数据不足返回 None（不插值、不外推）。"""
    i0 = idx.get(day)
    if i0 is None:
        return None
    i_in, i_out = i0 + 1, i0 + n
    dates = bars["dates"]
    if i_in >= len(dates) or i_out >= len(dates):
        return None
    try:
        entry = float(bars["open"][i_in])
        exit_ = float(bars["close"][i_out])
    except (TypeError, ValueError):
        return None
    if entry <= 0:
        return None
    return (exit_ / entry - 1.0) * 100.0


def ordered_codes(ab: dict, arm: str, day: str) -> list[str]:
    """当日的推荐顺序。enabled 按 launch_score 降序；legacy 用 Node 2 输出顺序。"""
    day_data = (ab.get(f"{arm}_days") or {}).get(day) or {}
    if arm == "enabled":
        watch = [w for w in (day_data.get("launch_watch") or []) if w.get("in_output")]
        watch.sort(
            key=lambda w: (w.get("launch_score") if w.get("launch_score") is not None else -1.0),
            reverse=True,
        )
        return [w["code"] for w in watch if w.get("code")]
    return list(day_data.get("ambush_codes") or [])


def _agg(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "hit": None}
    return {
        "n": len(values),
        "mean": round(st.mean(values), 2),
        "median": round(st.median(values), 2),
        "hit": round(100.0 * sum(1 for v in values if v > 0) / len(values), 1),
    }


def _tstat(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    sd = st.stdev(values)
    if sd <= 0:
        return None
    return round(st.mean(values) / (sd / math.sqrt(len(values))), 2)


def _day_universe(cache: dict, idx_map: dict, day: str, n: int) -> list[float]:
    out: list[float] = []
    for code, bars in cache.items():
        r = forward_return(bars, idx_map[code], day, n)
        if r is not None:
            out.append(r)
    return out


def evaluate(cache: dict, ab: dict, n: int) -> dict:
    idx_map = {code: _index(bars) for code, bars in cache.items()}
    days = list(ab.get("common_days") or [])
    out: dict = {"horizon": n, "arms": {}, "capacity": None, "days_total": len(days)}

    for arm in ("enabled", "legacy"):
        pooled: list[float] = []
        base_pooled: list[float] = []
        excesses: list[float] = []
        used_days = 0
        for day in days:
            rec_codes = set((ab.get(f"{arm}_days") or {}).get(day, {}).get("ambush_codes") or [])
            if not rec_codes:
                continue
            base = _day_universe(cache, idx_map, day, n)
            rec = [r for code in rec_codes
                   if (r := forward_return(cache.get(code, {}), idx_map.get(code, {}), day, n)) is not None]
            if not rec or not base:
                continue
            used_days += 1
            pooled.extend(rec)
            base_pooled.extend(base)
            excesses.append(st.mean(rec) - st.mean(base))
        stats = _agg(pooled)
        stats["days"] = used_days
        stats.update({
            "day_excess_mean": round(st.mean(excesses), 2) if excesses else None,
            "day_excess_median": round(st.median(excesses), 2) if excesses else None,
            "day_excess_positive_share": (
                round(100.0 * sum(1 for e in excesses if e > 0) / len(excesses), 1)
                if excesses else None
            ),
            "day_excess_t": _tstat(excesses),
            "baseline_mean": round(st.mean(base_pooled), 2) if base_pooled else None,
            "baseline_hit": (
                round(100.0 * sum(1 for v in base_pooled if v > 0) / len(base_pooled), 1)
                if base_pooled else None
            ),
        })
        out["arms"][arm] = stats

    # 相位分组（回答「非主线跌是否正常」）
    phase_rows: dict[str, list[float]] = {}
    for day in days:
        for row in ((ab.get("enabled_days") or {}).get(day, {}).get("boards") or {}).values():
            if not row.get("analyzed") or not row.get("phase") or not row.get("code"):
                continue
            r = forward_return(cache.get(row["code"], {}), idx_map.get(row["code"], {}), day, n)
            if r is not None:
                phase_rows.setdefault(row["phase"], []).append(r)
    out["by_phase"] = {k: _agg(v) for k, v in sorted(phase_rows.items())}

    # 按日配对 launch − decline（消除池化样本量悬殊）
    paired: list[float] = []
    for day in days:
        launch_r: list[float] = []
        decline_r: list[float] = []
        for row in ((ab.get("enabled_days") or {}).get(day, {}).get("boards") or {}).values():
            if not row.get("analyzed") or not row.get("code"):
                continue
            r = forward_return(cache.get(row["code"], {}), idx_map.get(row["code"], {}), day, n)
            if r is None:
                continue
            if row.get("phase") == "launch":
                launch_r.append(r)
            elif row.get("phase") == "decline":
                decline_r.append(r)
        if len(launch_r) >= 3 and len(decline_r) >= 10:
            paired.append(st.mean(launch_r) - st.mean(decline_r))
    out["launch_minus_decline"] = {
        "days": len(paired),
        "mean": round(st.mean(paired), 2) if paired else None,
        "positive_share": (
            round(100.0 * sum(1 for x in paired if x > 0) / len(paired), 1) if paired else None
        ),
        "t": _tstat(paired),
    }

    # 容量：同日按 launch_score 前 16 vs 17+
    top: list[float] = []
    tail: list[float] = []
    for day in days:
        ordered = ordered_codes(ab, "enabled", day)
        for pos, code in enumerate(ordered):
            r = forward_return(cache.get(code, {}), idx_map.get(code, {}), day, n)
            if r is None:
                continue
            (top if pos < CAP else tail).append(r)
    out["capacity"] = {"top_cap": _agg(top), "beyond_cap": _agg(tail)}

    # Top-K：两臂各按自己的排序取前 K，逐 K 汇总（同日两臂并入同一桶，
    # 因此 K 行的 n 是天数×臂数×K 的实际上限）
    topk: dict[str, dict[int, dict]] = {"enabled": {}, "legacy": {}}
    for arm in ("enabled", "legacy"):
        for k in TOPK:
            pooled_k: list[float] = []
            excesses_k: list[float] = []
            used = 0
            for day in days:
                base = _day_universe(cache, idx_map, day, n)
                vals = [r for code in ordered_codes(ab, arm, day)[:k]
                        if (r := forward_return(cache.get(code, {}), idx_map.get(code, {}), day, n)) is not None]
                if not vals or not base:
                    continue
                used += 1
                pooled_k.extend(vals)
                excesses_k.append(st.mean(vals) - st.mean(base))
            stats = _agg(pooled_k)
            stats.update({
                "days": used,
                "day_excess_mean": round(st.mean(excesses_k), 2) if excesses_k else None,
                "day_excess_positive_share": (
                    round(100.0 * sum(1 for e in excesses_k if e > 0) / len(excesses_k), 1)
                    if excesses_k else None
                ),
                "day_excess_t": _tstat(excesses_k),
            })
            topk[arm][k] = stats
    out["topk"] = topk
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Node 2 推荐集合前向收益诊断（dev，非 P4）")
    ap.add_argument("--md-out", default="")
    args = ap.parse_args()

    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    ab = json.loads(AB.read_text(encoding="utf-8"))
    results = [evaluate(cache, ab, n) for n in HORIZONS]

    lines: list[str] = []
    lines.append("## Node 2 推荐集合前向收益（板块指数，T+1 开盘 → T+N 收盘，未计成本）\n")
    lines.append(f"样本：{ab['common_days'][0]} ~ {ab['common_days'][-1]}，"
                 f"共同交易日 {len(ab['common_days'])}\n")

    lines.append("### 全集合\n")
    lines.append("| 臂 | N | 板块-日 | 均值% | 中位% | 胜率% | 基准均值% | 基准胜率% | "
                 "日均超额% | 超额为正天数占比% | 超额 t |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        for arm, label in (("enabled", "enabled(launch全量)"), ("legacy", "legacy(≤16)")):
            s = r["arms"][arm]
            lines.append(
                f"| {label} | {r['horizon']} | {s['n']} | {s['mean']} | {s['median']} | {s['hit']} | "
                f"{s['baseline_mean']} | {s['baseline_hit']} | {s['day_excess_mean']} | "
                f"{s['day_excess_positive_share']} | {s['day_excess_t']} |"
            )

    lines.append("")
    lines.append("### 只取前 K 条（enabled 按 launch_score / legacy 按原输出顺序）\n")
    lines.append("| 臂 | N | K | 板块-日 | 均值% | 中位% | 胜率% | 日均超额% | 超额为正占比% | 超额 t |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        for arm in ("enabled", "legacy"):
            for k in TOPK:
                s = r["topk"][arm][k]
                lines.append(
                    f"| {arm} | {r['horizon']} | {k} | {s['n']} | {s['mean']} | {s['median']} | {s['hit']} | "
                    f"{s['day_excess_mean']} | {s['day_excess_positive_share']} | {s['day_excess_t']} |"
                )

    lines.append("")
    lines.append("### 相位单调性（analyzed 板块按连续相位分组）\n")
    lines.append("| N | 相位 | 板块-日 | 均值% | 中位% | 胜率% |")
    lines.append("|---|---|---|---|---|---|")
    for r in results:
        for phase in ("launch", "expansion", "mature", "decline", "dormant"):
            s = (r.get("by_phase") or {}).get(phase)
            if not s or not s.get("n"):
                continue
            lines.append(f"| {r['horizon']} | {phase} | {s['n']} | {s['mean']} | {s['median']} | {s['hit']} |")

    lines.append("")
    lines.append("### 按日配对：launch − decline\n")
    lines.append("| N | 配对日数 | 日均差% | 正天数占比% | t |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        p = r.get("launch_minus_decline") or {}
        lines.append(f"| {r['horizon']} | {p.get('days')} | {p.get('mean')} | "
                     f"{p.get('positive_share')} | {p.get('t')} |")

    lines.append("")
    lines.append("### 容量：前 16（按 launch_score）vs 17+\n")
    lines.append("| N | 组 | 板块-日 | 均值% | 中位% | 胜率% |")
    lines.append("|---|---|---|---|---|---|")
    for r in results:
        c = r["capacity"]
        for key, label in (("top_cap", "前16"), ("beyond_cap", "17+")):
            s = c[key]
            lines.append(f"| {r['horizon']} | {label} | {s['n']} | {s['mean']} | {s['median']} | {s['hit']} |")

    lines.append("")
    lines.append("> 口径：板块指数、入场 T+1 开盘、未计费用；基准=当日可算前向收益的全部缓存板块等权。")
    lines.append("> 这是**窗口内开发集诊断**，不是 P4 样本外结论，也不构成收益改善证明。")

    text = "\n".join(lines)
    print(text)
    if args.md_out:
        p = Path(args.md_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
        print(f"\n写入 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
