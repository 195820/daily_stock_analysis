#!/usr/bin/env python
"""用 2026 年板块指数的**真实主升段**判定门槛（标签来自行情，不来自 Node 2 输出）。

两段分析：

A) 确认延迟与剩余空间：对每段真实主升段（谷底 t0 → 20 日内 ≥20% → 峰 t_peak），
   找"首个满足门槛的确认日 t_c"，输出 延迟天数 / 确认时剩余涨幅 / 到顶前能确认的比例。
   这是门槛的**代价表**：门槛越严，确认越晚、剩余空间越小。

B) 信号质量：所有板块-日里满足门槛的信号，算每日信号数、T+1 开盘→T+5/T+10 收盘的
   中位收益与胜率、"10 日内出现 ≥10% 上涨"的比例。这是门槛的**收益表**。

注意：主升段起点按定义就是谷底（未来 20 日涨 ≥20% 的那一天），所以该日 r5/r10/dist
天然为负、above_ma20 常为假——这不是"反转证据"，是定义性偏差。因此不报 AUC。
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

CACHE = ROOT / "data" / "_replay" / "board_ohlcv_cache.json"
NOISE = ("昨日", "指数", "板块", "连板", "涨停", "跌停", "融资", "转债", "B股", "AH")


def load_boards() -> dict[str, dict]:
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    out = {}
    for code, p in cache.items():
        if len(p.get("dates") or []) < 60:
            continue
        name = (p.get("name") or "").strip("\x00")
        if not name or any(k in name for k in NOISE):
            continue
        out[code] = {
            "name": name,
            "dates": p["dates"],
            "open": np.asarray(p["open"], float),
            "high": np.asarray(p["high"], float),
            "close": np.asarray(p["close"], float),
        }
    return out


def find_episodes(close: np.ndarray, run_gain: float, horizon: int, min_gap: int):
    n = len(close)
    out = []
    t = 20
    while t < n - horizon:
        win = close[t + 1: t + 1 + horizon]
        if len(win) == 0:
            break
        peak_off = int(np.argmax(win))
        if close[t] > 0 and win[peak_off] / close[t] - 1 >= run_gain:
            out.append((t, t + 1 + peak_off))
            t = t + peak_off + 1 + min_gap
            continue
        t += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-gain", type=float, default=0.20)
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--min-gap", type=int, default=10)
    args = ap.parse_args()

    boards = load_boards()
    dates = sorted({d for b in boards.values() for d in b["dates"]})
    dpos = {d: i for i, d in enumerate(dates)}
    n_days = len(dates)

    close = np.full((len(boards), n_days), np.nan)
    openp = np.full((len(boards), n_days), np.nan)
    high = np.full((len(boards), n_days), np.nan)
    codes = list(boards)
    for r, c in enumerate(codes):
        b = boards[c]
        idx = np.array([dpos[d] for d in b["dates"]])
        close[r, idx] = b["close"]
        openp[r, idx] = b["open"]
        high[r, idx] = b["high"]

    # ---- 判据（全部只用 ≤t 的数据）----
    def roll_max(a, w):
        out = np.full_like(a, np.nan)
        for t in range(w - 1, n_days):
            seg = a[:, t - w + 1: t + 1]
            with np.errstate(all="ignore"):
                out[:, t] = np.nanmax(np.where(np.isfinite(seg), seg, -np.inf), axis=1)
        out[~np.isfinite(out)] = np.nan
        return out

    ma20 = np.full_like(close, np.nan)
    for t in range(19, n_days):
        seg = close[:, t - 19: t + 1]
        ma20[:, t] = np.where(np.all(np.isfinite(seg), axis=1), seg.mean(axis=1), np.nan)
    r5 = np.full_like(close, np.nan)
    r10 = np.full_like(close, np.nan)
    r5[:, 5:] = close[:, 5:] / close[:, :-5] - 1
    r10[:, 10:] = close[:, 10:] / close[:, :-10] - 1
    hi20 = roll_max(high, 20)
    dist_h = close / hi20 - 1
    above = close > ma20

    # 横截面分位
    r5_pct = np.full_like(close, np.nan)
    for t in range(5, n_days):
        v = r5[:, t]
        ok = np.isfinite(v)
        if ok.sum() < 50:
            continue
        order = v[ok].argsort().argsort()
        r5_pct[ok, t] = (order + 1) / ok.sum()

    # ---- 主升段 ----
    eps = []
    for r in range(len(codes)):
        first = next((i for i in range(n_days) if np.isfinite(close[r, i])), None)
        if first is None:
            continue
        for t0, tp in find_episodes(close[r, first:], args.run_gain, args.horizon, args.min_gap):
            eps.append((r, first + t0, first + tp))
    print(f"== 样本 ==\n板块 {len(codes)}，交易日 {n_days}（{dates[0]} ~ {dates[-1]}）")
    print(f"主升段（{args.horizon} 日内 ≥{args.run_gain:.0%}）: {len(eps)} 段")
    gains = [close[r, tp] / close[r, t0] - 1 for r, t0, tp in eps]
    days = [tp - t0 for _, t0, tp in eps]
    print(f"到顶涨幅中位 {st.median(gains):.1%}，到顶天数中位 {st.median(days):.0f}")
    per_day = {}
    for _, t0, _ in eps:
        per_day[t0] = per_day.get(t0, 0) + 1
    print(f"每日谷底数: 中位 {st.median(per_day.values()):.0f} / P90 {np.percentile(list(per_day.values()), 90):.0f} / max {max(per_day.values())}")

    split_idx = next((i for i, d in enumerate(dates) if d > "2026-07-31"), n_days)
    def half_of(t: int) -> str:
        return "H1" if t < split_idx else "H2"
    print(f"时间切分: H1 = {dates[0]}~{dates[split_idx-1]}，H2 = {dates[split_idx]}~{dates[-1]}（切分点仅用于稳定性检查）")

    # ---- A) 确认延迟与剩余空间 ----
    print(f"\n== A) 门槛的代价（全样本 / H1 / H2）：覆盖段数·延迟中位·剩余涨幅中位 ==")
    print(f"{'r5_pct≥':>8}{'dist_h≥':>9}{'全样本':>20}{'H1':>20}{'H2':>20}")
    for tau in (0.5, 0.8, 0.9):
        for dmin in (0.0, -0.02, -0.05, -0.10):
            acc = {"ALL": ([], [], 0, 0), "H1": ([], [], 0, 0), "H2": ([], [], 0, 0)}
            for r, t0, tp in eps:
                h = half_of(t0)
                tc = None
                for t in range(t0, tp):
                    if (np.isfinite(r5_pct[r, t]) and r5_pct[r, t] >= tau
                            and np.isfinite(dist_h[r, t]) and dist_h[r, t] >= dmin
                            and above[r, t] and r5[r, t] > 0 and r10[r, t] > 0):
                        tc = t
                        break
                for key in ("ALL", h):
                    lags, rems, cov, tot = acc[key]
                    tot += 1
                    if tc is not None:
                        cov += 1
                        lags.append(tc - t0)
                        rems.append(close[r, tp] / close[r, tc] - 1)
                    acc[key] = (lags, rems, cov, tot)
            def cell(key):
                lags, rems, cov, tot = acc[key]
                if not cov:
                    return f"{0/tot:>5.0%}  {'—':>4}  {'—':>6}"
                return f"{cov/tot:>5.0%}  {st.median(lags):>4.1f}  {st.median(rems):>6.1%}"
            print(f"{tau:>8.2f}{dmin:>9.2f}{cell('ALL'):>21}{cell('H1'):>21}{cell('H2'):>21}")

    # ---- B) 信号质量 ----
    fwd5 = np.full_like(close, np.nan)
    fwd10 = np.full_like(close, np.nan)
    for t in range(n_days - 1):
        fwd5[:, t] = close[:, min(t + 5, n_days - 1)] / openp[:, t + 1] - 1
        fwd10[:, t] = close[:, min(t + 10, n_days - 1)] / openp[:, t + 1] - 1
    fut_max = np.full_like(close, -np.inf)
    for k in range(1, 11):
        shifted = np.full_like(close, np.nan)
        shifted[:, : n_days - k] = close[:, k:]
        fut_max = np.where(np.isfinite(shifted), np.maximum(fut_max, shifted), fut_max)
    with np.errstate(invalid="ignore", divide="ignore"):
        hit10 = np.where(np.isfinite(fut_max) & (openp > 0), (fut_max / openp - 1 >= 0.10).astype(float), np.nan)

    print(f"\n== B) 信号质量（T+1 开盘进、T+5/T+10 收盘出、未计费；全样本 / H1 / H2）==")
    print(f"{'r5_pct≥':>8}{'dist_h≥':>9}{'区间':>5}{'信号/日':>8}{'n':>7}{'T+5中位':>9}{'T+5胜率':>8}{'10日≥10%':>9}")
    for tau in (0.5, 0.8, 0.9):
        for dmin in (0.0, -0.02, -0.05, -0.10):
            base = (r5_pct >= tau) & (dist_h >= dmin) & above & (r5 > 0) & (r10 > 0)
            base[:, :20] = False
            for label, sl in (("ALL", slice(0, n_days)), ("H1", slice(0, split_idx)), ("H2", slice(split_idx, n_days))):
                sig = np.zeros_like(base)
                sig[:, sl] = base[:, sl]
                n = int(sig.sum())
                days = sl.stop - sl.start
                if n < 20:
                    continue
                v5 = fwd5[sig]; v10 = fwd10[sig]; h10 = hit10[sig]
                v5 = v5[np.isfinite(v5)]; v10 = v10[np.isfinite(v10)]; h10 = h10[np.isfinite(h10)]
                print(f"{tau:>8.2f}{dmin:>9.2f}{label:>5}{n/days:>8.1f}{n:>7}"
                      f"{st.median(v5):>8.1%}{np.mean(np.array(v5) > 0):>8.0%}"
                      f"{np.mean(h10):>9.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
