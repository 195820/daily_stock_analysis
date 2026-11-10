"""启动状态（enabled launch 相位）全窗口回测 —— 不依赖归档，直接跑状态机。

对象不是「门槛规则」，是 production enabled 路径真正会路由的启动集合：
逐板块顺序执行 `transition_lifecycle`，取 `phase == launch` 且可路由的板块，
按 `launch_score` 排序取 TopN。

近似与边界（必须随结果一起引用）：

- 输入只来自板块 OHLCV：r5 / r10 / r5_pct / above_ma20 / ma20_slope /
  high20_dist / high20_pos / vol_ratio 全量可重建。
- `strength` 与 `score_drop` 用 r5_pct 的峰值回撤近似（0–100 同量纲），
  `overheat_level` 用「贴 20 日高 + 分位 ≥0.95 + 量比 ≥1.5」近似 exhausted。
  这两处与生产 Node 2 的真实强度/过热口径**有出入**，所以另跑一个
  `loose` 变体（overheat 恒 normal、score_drop=0）看结论是否依赖近似。
- 状态机从 dormant 冷启动，前 ~5 个观测日不算 launch；回测窗口从有足够
  历史的第一天开始，前 60 个交易日只用于状态预热、不计入回测。

预注册判据（跑之前定）：

1. launch Top4 的日均超额 > 0；
2. 配对 t ≥ 2；
3. 日胜率 ≥ 55%；
4. 相对「同池随机 4 只」的优势 ≥ 0.3pp/日。
四条同时成立才算「启动状态有独立 alpha」；否则判定否证。

用法：
  python scripts/backtest_node2_launch_state.py
"""

from __future__ import annotations

import bisect
import json
import math
import statistics as st
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.got.sector_lifecycle import (  # noqa: E402
    PHASE_LAUNCH,
    LifecycleEvidence,
    compute_launch_score,
    is_trend_broken,
    transition_lifecycle,
)

CACHE = ROOT / "data" / "_replay" / "board_ohlcv_cache.json"
OUT = ROOT / "data" / "_replay" / "ab" / "node2_launch_state_backtest.md"
TOPN = 4
COST = 0.002
WARMUP = 60
RNG_SEED = 20261110


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def features(bars: dict, i: int):
    if i < 25:
        return None
    closes = bars["close"]
    highs = bars["high"]
    vols = bars["volume"]
    close = float(closes[i])
    if close <= 0:
        return None
    r5 = close / float(closes[i - 5]) - 1 if float(closes[i - 5]) > 0 else None
    r10 = close / float(closes[i - 10]) - 1 if float(closes[i - 10]) > 0 else None
    high20 = max(float(x) for x in highs[i - 19:i + 1])
    close20 = max(float(x) for x in closes[i - 19:i + 1])
    ma20 = _mean([float(x) for x in closes[i - 19:i + 1]])
    prev_ma20 = _mean([float(x) for x in closes[i - 24:i - 4]])
    v5 = _mean([float(x) for x in vols[i - 4:i + 1]])
    v20 = _mean([float(x) for x in vols[i - 19:i + 1]])
    if r5 is None or r10 is None or high20 <= 0 or prev_ma20 <= 0:
        return None
    return {
        "r5": r5, "r10": r10, "high20_dist": close / high20 - 1.0,
        "high20_pos": close / close20 if close20 > 0 else None,
        "above_ma20": close > ma20,
        "ma20_slope": ma20 / prev_ma20 - 1.0,
        "vol_ratio": v5 / v20 if v20 > 0 else 1.0,
        "close": close,
    }


def fwd5(bars: dict, i: int):
    if i + 5 >= len(bars["dates"]):
        return None, None
    try:
        entry = float(bars["open"][i + 1])
        exit_ = float(bars["close"][i + 5])
        look = [float(x) for x in bars["close"][i + 1:i + 11]]
    except (KeyError, TypeError, ValueError, IndexError):
        return None, None
    if entry <= 0:
        return None, None
    top = 1.0 if look and entry >= 0.98 * max(look) else 0.0
    return (exit_ / entry - 1.0) * 100.0, top


def run_variant(cache, dates, variant: str):
    """返回 {date: [(code, score, fwd, top)]}，按变体重建状态机。"""
    idx = {c: {d: i for i, d in enumerate(b["dates"])} for c, b in cache.items()}
    calendar = [date.fromisoformat(d) for d in dates]
    # 逐日横截面分位（r5）
    day_feats: dict[str, dict[str, dict]] = defaultdict(dict)
    for d in dates:
        raw = {}
        for c, b in cache.items():
            i = idx[c].get(d)
            if i is None:
                continue
            f = features(b, i)
            if f is not None:
                raw[c] = f
        if len(raw) < 50:
            continue
        ordered = sorted(f["r5"] for f in raw.values())
        n = len(ordered)
        for c, f in raw.items():
            f["r5_pct"] = bisect.bisect_right(ordered, f["r5"]) / n
            day_feats[d][c] = f

    states: dict[str, object] = {}
    pct_peak: dict[str, float] = {}
    pool: dict[str, list] = defaultdict(list)
    for d in dates:
        td = date.fromisoformat(d)
        for c, f in day_feats.get(d, {}).items():
            pct = f["r5_pct"] * 100.0
            # score_drop 近似：r5_pct 的历史峰值回撤（0–100）
            peak_now = max(pct_peak.get(c, 0.0), pct)
            pct_peak[c] = peak_now
            score_drop = max(0.0, peak_now - pct)
            if variant == "loose":
                overheat = "normal"
                score_drop = 0.0
            else:
                overheat = ("exhausted" if (f["high20_pos"] or 0) >= 0.995
                            and pct >= 95.0 and f["vol_ratio"] >= 1.5 else "normal")
            broken = is_trend_broken(f["r5"], f["above_ma20"], f["high20_pos"],
                                     overheat, score_drop)
            ev = LifecycleEvidence(
                trade_date=td, bar_complete=True, r5=f["r5"], r10=f["r10"],
                r5_pct=f["r5_pct"], above_ma20=f["above_ma20"],
                ma20_slope=f["ma20_slope"], high20_dist=f["high20_dist"],
                high20_pos=f["high20_pos"], strength=pct, close=f["close"],
                overheat_level=overheat, trend_broken=broken, score_drop=score_drop,
            )
            state = transition_lifecycle(states.get(c), ev, calendar)
            states[c] = state
            if state.phase == PHASE_LAUNCH and broken is False and overheat != "exhausted":
                score = compute_launch_score(f["r5_pct"], f["r10"], f["high20_dist"]) or 0.0
                fv, top = fwd5(cache[c], idx[c][d])
                if fv is not None:
                    pool[d].append((c, score, fv, top, state.phase_age or 1))
    return pool


def event_study(cache, dates, pool, horizons=(1, 3, 5, 10)):
    """事件口径：每个 launch 板块-日在各持有期的横截面超额；按日聚合后跨日 t。"""
    idx = {c: {d: i for i, d in enumerate(b.get("dates") or [])} for c, b in cache.items()}

    def fwd_h(code, d, h):
        b, i = cache[code], idx[code][d]
        if i + h >= len(b["dates"]):
            return None
        try:
            entry = float(b["open"][i + 1])
            return (float(b["close"][i + h]) / entry - 1.0) * 100.0 if entry > 0 else None
        except (KeyError, TypeError, ValueError, IndexError):
            return None

    base = {}
    for d in dates:
        for h in horizons:
            vals = [v for c, b in cache.items()
                    if (i := idx[c].get(d)) is not None and (v := fwd_h(c, d, h)) is not None]
            if len(vals) >= 50:
                base[(d, h)] = st.mean(vals)
    out = {}
    for age in (1, 2, 3):
        for h in horizons:
            per_day = defaultdict(list)
            n_ev = 0
            for d, rows in pool.items():
                if (d, h) not in base:
                    continue
                for c, _s, _f, _t, a in rows:
                    if int(a) != age:
                        continue
                    v = fwd_h(c, d, h)
                    if v is not None:
                        per_day[d].append(v - base[(d, h)])
                        n_ev += 1
            series = [st.mean(v) for v in per_day.values()]
            if len(series) < 5:
                continue
            m = st.mean(series)
            sd = st.stdev(series) if len(series) > 1 else 0.0
            out[(age, h)] = {"days": len(series), "events": n_ev, "mean": m,
                             "t": m / (sd / math.sqrt(len(series))) if sd else 0.0,
                             "win": 100.0 * sum(1 for x in series if x > 0) / len(series)}
    return out


def stats(xs):
    if not xs:
        return None
    m = st.mean(xs)
    sd = st.stdev(xs) if len(xs) > 1 else 0.0
    return {"n": len(xs), "mean": m, "t": m / (sd / math.sqrt(len(xs))) if sd else 0.0,
            "win": 100.0 * sum(1 for x in xs if x > 0) / len(xs)}


def main() -> int:
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    dates = sorted({d for b in cache.values() for d in (b.get("dates") or [])})
    print(f"dates {len(dates)}", flush=True)

    out = {}
    for variant in ("proxy", "loose"):
        pool = run_variant(cache, dates, variant)
        # 逐日全板块基准
        idx = {c: {d: i for i, d in enumerate(b.get("dates") or [])} for c, b in cache.items()}
        base = {}
        for d in dates:
            vals = [v for c, b in cache.items()
                    if (i := idx[c].get(d)) is not None and (v := fwd5(b, i)[0]) is not None]
            if len(vals) >= 50:
                base[d] = st.mean(vals)
        bt = [d for d in dates if d in base and d in pool][WARMUP::5]
        rng = __import__("random").Random(RNG_SEED)
        series = {k: [] for k in ("top", "all", "random_pool", "random_universe")}
        sizes = []
        for d in bt:
            rows = pool[d]
            if not rows:
                continue
            sizes.append(len(rows))
            b = base[d]
            ranked = sorted(rows, key=lambda r: -r[1])
            series["top"].append(st.mean([r[2] for r in ranked[:TOPN]]) - b - COST * 100)
            series["all"].append(st.mean([r[2] for r in rows]) - b - COST * 100)
            n = min(TOPN, len(rows))
            draws = [st.mean([rows[rng.randrange(len(rows))][2] for _ in range(n)]) - b - COST * 100
                     for _ in range(200)]
            series["random_pool"].append(st.mean(draws))
            uni = [v for c, bb in cache.items()
                   if (i := idx[c].get(d)) is not None and (v := fwd5(bb, i)[0]) is not None]
            draws2 = [st.mean([uni[rng.randrange(len(uni))] for _ in range(TOPN)]) - b - COST * 100
                      for _ in range(200)]
            series["random_universe"].append(st.mean(draws2))
        res = {k: stats(v) for k, v in series.items()}
        out[variant] = {"res": res, "sizes": sizes, "days": len(series["top"])}

    lines = ["# 启动状态回测（enabled launch 相位，缓存重建状态机）", "",
             f"- 交易日 {len(dates)}（{dates[0]} ~ {dates[-1]}）；回测从第 {WARMUP} 个交易日起、"
             "每 5 日非重叠调仓；Top4 等权；往返成本 0.2%。",
             "- 输入近似：strength≈r5_pct×100、score_drop≈r5_pct 20 日峰值回撤、"
             "overheat=贴高+分位≥0.95+量比≥1.5 近似 exhausted。"
             "变体 B 的启动池显著更大（近似放宽 → 更多板块进入 launch），"
             "所以 B 只用于方向性稳健性，不是同分布复现。", ""]
    for variant, label in (("proxy", "变体 A：含过度热/回撤近似"), ("loose", "变体 B：不过热、无回撤（宽松）")):
        r = out[variant]
        lines += [f"## {label}", "",
                  "| 臂 | 天数 | 日均超额 | t | 日胜率 |", "|---|---:|---:|---:|---:|"]
        for k, lab in (("top", "launch Top4"), ("all", "launch 全池等权"),
                       ("random_pool", "同池随机 4 只"), ("random_universe", "全宇宙随机 4 只")):
            s = r["res"].get(k)
            if s:
                # 全宇宙臂是 200 次抽样的均值，方差被压 200 倍，t 无推断意义
                tt = "—" if k == "random_universe" else f"{s['t']:.2f}"
                lines.append(f"| {lab} | {s['n']} | {s['mean']:+.2f}pp | {tt} | {s['win']:.0f}% |")
        lines += ["", f"- 平均启动池 {st.mean(r['sizes']):.1f} 个/日，"
                      f"空池 {sum(1 for s in r['sizes'] if s == 0)} 天。"
                      f"Top4 毛超额 = 净 + {COST*100:.1f}pp = "
                      f"{r['res']['top']['mean'] + COST*100:+.2f}pp/日。", ""]

    ev = event_study(cache, dates, run_variant(cache, dates, "proxy"))
    lines += ["", "## 事件口径：每个 launch 板块-日（按相位年龄 × 持有期）", "",
              "| 年龄 | 持有 | 事件数 | 天数 | 日均超额 | t(跨日) | 正超额日占比 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for (age, h), s in sorted(ev.items()):
        lines.append(f"| day{age} | {h}日 | {s['events']} | {s['days']} | {s['mean']:+.2f}pp | "
                     f"{s['t']:.2f} | {s['win']:.0f}% |")

    top = out["proxy"]["res"]["top"]; rp = out["proxy"]["res"]["random_pool"]
    passed = bool(top and top["mean"] > 0 and top["t"] >= 2 and top["win"] >= 55
                  and top["mean"] - rp["mean"] >= 0.3)
    lines += [f"**预注册判定**：Top4 日均超额 {top['mean']:+.2f}pp（t={top['t']:.2f}，"
              f"胜率 {top['win']:.0f}%），对同池随机 {top['mean']-rp['mean']:+.2f}pp → "
              f"{'通过' if passed else '**不通过**'}。",
              "结论：" + ("启动状态存在独立 alpha。" if passed
                       else "启动状态不产生独立 alpha；不得作为买入信号，只能作为相位记账。")]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
