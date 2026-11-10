#!/usr/bin/env python
"""Node 2 全量 replay v0（2026 全年，基于 board_kline_2026_cache）。

复现：
- 4 维原始指标（5d 收益 / 5d÷20d 成交额 / 20d 收益 / 上涨家数占比）
- 截面百分位 → strength_score
- RRG（截面百分位 + 3-5 日斜率 + 3 日 EMA）
- price_chg_5d / above_ma20 / high20_pos / today_change / score_drop
- is_ambush（生产 _compute_ambush_score_v2）
- tier（生产分级逻辑的复刻）
- _apply_state_machine（直接调用生产静态方法）

v0 明确近似：
- `overheat_level` 固定 "normal"（未复刻 9 维过热）
- `is_defensive` 固定 False（未复刻 Node 1 市场上下文）
- `frame_group` 用关键词表近似
这两个近似会在 fidelity 报告里显式暴露。

用法：
  python scripts/replay_node2_2026.py
  python scripts/replay_node2_2026.py --start 20260101 --end 20261103
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.got.board_filter import is_noise_board  # noqa: E402
from src.got.indicators import cross_sectional_rank  # noqa: E402
from src.got.nodes.sector_analysis import (  # noqa: E402
    _FRAME_GROUP_KEYWORDS,
    _POLICY_SUPPORTED_CHAINS,
    SectorAnalysisNode,
)
from src.got.schemas import MarketContext, RRGQuadrant  # noqa: E402

CACHE = ROOT / "data" / "got_daily" / "_cache" / "board_kline_2026_cache.json"
DAILY = ROOT / "data" / "got_daily"
DB = ROOT / "data" / "stock_analysis.db"
FIELDS = ["open", "high", "low", "close", "volume", "amount", "up_count", "down_count"]


def frame_group(name: str) -> str:
    for keywords, group in _FRAME_GROUP_KEYWORDS:
        if any(kw in name for kw in keywords):
            return group
    return "其他"


def load_frames() -> dict[str, pd.DataFrame]:
    raw = json.loads(CACHE.read_text(encoding="utf-8"))
    out: dict[str, pd.DataFrame] = {}
    for code, rec in raw.items():
        dates = rec.get("dates") or []
        if len(dates) < 25:
            continue
        if is_noise_board(str(rec.get("name") or "")):
            continue
        data = {f: rec.get(f) or [] for f in FIELDS}
        # 对齐字段长度
        n = min([len(dates)] + [len(v) for v in data.values() if v])
        idx = pd.to_datetime(dates[:n])
        df = pd.DataFrame({f: data[f][:n] for f in FIELDS}, index=idx)
        df = df.dropna(subset=["close"])
        if len(df) >= 25:
            out[code] = df
    return out


def raw_metrics(df: pd.DataFrame) -> dict[str, float] | None:
    if len(df) < 20:
        return None
    close = df["close"]
    amount = df["amount"] if "amount" in df.columns else None
    pm = close.iloc[-1] / close.iloc[-5] - 1 if len(close) >= 5 else 0.0
    if amount is not None and len(amount) >= 20:
        avg5, avg20 = amount.iloc[-5:].mean(), amount.iloc[-20:].mean()
        te = avg5 / avg20 if avg20 > 0 else 1.0
    else:
        te = 1.0
    rs = close.iloc[-1] / close.iloc[-20] - 1
    up = float(df["up_count"].iloc[-1] or 0) if "up_count" in df.columns else 0.0
    dn = float(df["down_count"].iloc[-1] or 0) if "down_count" in df.columns else 0.0
    bh = up / (up + dn) if (up + dn) > 0 else 0.5
    return {"price_momentum": pm, "turnover_expansion": te, "relative_strength": rs, "breadth_health": bh}


def indicators_from_df(df: pd.DataFrame) -> dict[str, float]:
    close = df["close"]
    high20 = float(df["high"].iloc[-20:].max()) if len(df) >= 20 else float(close.iloc[-1])
    low20 = float(df["low"].iloc[-20:].min()) if len(df) >= 20 else float(close.iloc[-1])
    ma20 = float(close.iloc[-20:].mean()) if len(df) >= 20 else float(close.iloc[-1])
    c = float(close.iloc[-1])
    high20_close = float(close.iloc[-20:].max()) if len(df) >= 20 else c
    return {
        "pos_close": c,
        "pos_high20": high20,
        "pos_ma20": ma20,
        "high20_pos": c / high20_close if high20_close > 0 else 1.0,
        "above_ma20": c > ma20,
    }


def tier_of(name: str, indicators: dict[str, float]) -> str:
    fg = frame_group(name)
    score = 3 if fg in _POLICY_SUPPORTED_CHAINS else 0
    if fg not in ("其他", ""):
        score += 1
    te = indicators.get("turnover_expansion", 0)
    score += 2 if te > 0.6 else (1 if te > 0.4 else 0)
    if indicators.get("price_momentum", 0) > 0.6 and indicators.get("relative_strength", 0) > 0.6:
        score += 1
    return "S" if score >= 5 else ("A" if score >= 3 else "B")


def build_market_ctx(frames: dict[str, pd.DataFrame], all_dates: list[pd.Timestamp]) -> dict:
    """等权板块市场代理：只供 _check_defensive 读取，零新数据。"""
    rets: dict[pd.Timestamp, list[float]] = defaultdict(list)
    for df in frames.values():
        r = df["close"].pct_change()
        for d, v in r.items():
            if pd.notna(v):
                rets[d].append(float(v))
    mkt = {d: sum(v) / len(v) for d, v in rets.items() if v}
    out = {}
    for i, d in enumerate(all_dates):
        hist = [mkt[x] for x in all_dates[: i + 1] if x in mkt][-20:]
        if not hist:
            continue
        def cum(n: int) -> float:
            p = 1.0
            for v in hist[-n:]:
                p *= 1 + v
            return p - 1
        r5, r20 = cum(5), cum(20)
        def trend(x: float, th: float) -> str:
            return "bearish" if x < -th else ("bullish" if x > th else "neutral")
        out[d] = SimpleNamespace(
            tech_growth=SimpleNamespace(
                score=max(-1.0, min(1.0, r20 / 0.10)),
                trend=trend(r20, 0.02),
                signals=SimpleNamespace(
                    short_term=SimpleNamespace(trend=trend(r5, 0.01)),
                    medium_term=SimpleNamespace(trend=trend(r20, 0.02)),
                ),
            ),
            today_change={"market": mkt.get(d, 0.0) * 100},
        )
    # 有归档 market.json 的日期用 Node 1 实际输出覆盖代理
    for d in all_dates:
        p = DAILY / d.strftime("%Y%m%d") / "market.json"
        if not p.exists():
            continue
        try:
            out[d] = MarketContext.model_validate(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260101")
    ap.add_argument("--end", default="20261103")
    args = ap.parse_args()
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)

    frames = load_frames()
    raw_cache = json.loads(CACHE.read_text(encoding="utf-8"))
    names = {code: (raw_cache.get(code) or {}).get("name", code) for code in frames}
    all_dates = sorted({d for df in frames.values() for d in df.index if start <= d <= end})
    print(f"boards={len(frames)} dates={len(all_dates)} {all_dates[0].date()} → {all_dates[-1].date()}")
    market_ctx = build_market_ctx(frames, all_dates)

    hist: dict[str, dict] = defaultdict(dict)
    results: list[dict] = []
    for di, d in enumerate(all_dates, 1):
        rows = []
        for code, df in frames.items():
            sub = df.loc[:d]
            if len(sub) < 25:
                continue
            raw = raw_metrics(sub)
            if raw is None:
                continue
            ind = indicators_from_df(sub)
            rows.append({"code": code, "name": names[code], "df": sub, "raw": raw, "ind": ind,
                         "today_change": float(sub["close"].iloc[-1] / sub["close"].iloc[-2] - 1)
                         if len(sub) >= 2 else None})
        if len(rows) < 10:
            continue
        ranked = SectorAnalysisNode._apply_cross_sectional_ranking([r["raw"] for r in rows])
        scores = [SectorAnalysisNode._calc_strength_score(x) for x in ranked]
        for r, ind_rank, strength in zip(rows, ranked, scores):
            r["indicators"] = {**r["ind"], **ind_rank}
            r["strength"] = strength
        all_scores = [r["strength"] for r in rows]
        for r in rows:
            code = r["code"]
            prev = hist[code].get("last") or {}
            prev_trend = {
                "strength_trajectory": prev.get("trajectory", [])[-7:],
                "momentum_trajectory": prev.get("momentum_traj", [])[-7:],
                "rrg_ratio_ema": prev.get("rrg_ratio_ema"),
                "rrg_momentum_ema": prev.get("rrg_momentum_ema"),
            }
            rrg = SectorAnalysisNode._compute_rrg(r["strength"], prev_trend, all_scores)
            try:
                amb = SectorAnalysisNode._compute_ambush_score_v2(
                    r["df"], r["indicators"], rrg, r["strength"], prev_trend,
                )
            except Exception:
                amb = 0.0
            is_ambush = amb >= SectorAnalysisNode.AMBUSH_SCORE_FLOOR
            bs = r["indicators"]
            price5 = float(r["df"]["close"].iloc[-1] / r["df"]["close"].iloc[-5] - 1) if len(r["df"]) >= 5 else None
            above = bs["above_ma20"]
            high20_pos = bs["high20_pos"]
            score_drop = (prev.get("strength", r["strength"]) - r["strength"])
            prev_state = prev.get("state")
            prev_dwell = int(prev.get("dwell", 0) or 0)
            prev_exit = int(prev.get("exit_pressure", 0) or 0)
            mctx = market_ctx.get(d)
            is_def = bool(
                mctx
                and SectorAnalysisNode._check_defensive(
                    {"name": r["name"]}, mctx, r["df"], r["strength"],
                )
            )
            exit_pressure = SectorAnalysisNode._compute_exit_pressure(
                prev_state, prev_exit, r["strength"], r["today_change"],
            )
            state = SectorAnalysisNode._apply_state_machine(
                prev_state=prev_state,
                prev_dwell=prev_dwell,
                q=rrg.quadrant,
                rs_momentum=rrg.rs_momentum,
                strength_score=r["strength"],
                price_chg_5d=price5,
                above_ma20=above,
                high20_pos=high20_pos,
                overheat_level="normal",
                today_change=r["today_change"],
                score_drop=score_drop,
                is_ambush=is_ambush,
                is_defensive=is_def,
                exit_pressure=exit_pressure,
                tier=tier_of(r["name"], r["indicators"]),
            )
            dwell = prev_dwell + 1 if state == prev_state else 1
            traj = (prev.get("trajectory", []) + [r["strength"]])[-7:]
            mom_traj = (prev.get("momentum_traj", []) + [rrg.rs_momentum])[-7:]
            hist[code]["last"] = {
                "state": state, "strength": r["strength"], "dwell": dwell,
                "exit_pressure": exit_pressure, "trajectory": traj, "momentum_traj": mom_traj,
                "rrg_ratio_ema": rrg.rs_ratio, "rrg_momentum_ema": rrg.rs_momentum,
            }
            results.append({
                "date": str(d.date().isoformat()), "code": str(code), "name": str(r["name"]),
                "state": str(state), "strength": float(round(r["strength"], 1)),
                "rrg": str(rrg.quadrant), "rs_ratio": float(rrg.rs_ratio), "rs_momentum": float(rrg.rs_momentum),
                "amb": float(round(amb, 2)), "tier": str(tier_of(r["name"], r["indicators"])),
                "price5": float(price5) if price5 is not None else None,
                "above_ma20": bool(above), "high20_pos": float(round(high20_pos, 3)),
                "today_change": float(r["today_change"]) if r["today_change"] is not None else None,
                "score_drop": float(round(score_drop, 2)),
                "dwell": int(dwell), "exit_pressure": int(exit_pressure),
                "is_ambush": bool(is_ambush), "is_defensive": bool(is_def),
            })
        if di % 20 == 0 or di == len(all_dates):
            print(f"  {di}/{len(all_dates)} rows={len(results)}", flush=True)

    # 保存 replay 明细
    out = ROOT / "data" / "got_daily" / "_cache" / "node2_replay_2026.json"
    out.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    print(f"saved {out} rows={len(results)}")

    # 保真度：与 DB 实际 state 对比
    con = sqlite3.connect(DB)
    actual = {
        (str(d), c): s
        for c, d, s in con.execute(
            "select sector_code, tracking_date, state from node2_sector_trends "
            "where tracking_date >= '2026-07-24'"
        )
    }
    con.close()
    both = [(r, actual[(r["date"], r["code"])]) for r in results if (r["date"], r["code"]) in actual]
    if both:
        match = sum(1 for r, s in both if r["state"] == s)
        print(f"\n保真度: {match}/{len(both)} = {match/len(both):.1%}")
        by_pair = defaultdict(lambda: [0, 0])
        for r, s in both:
            by_pair[(s, r["state"])][0] += 1
            if s == r["state"]:
                by_pair[(s, r["state"])][1] += 1
        for (actual_s, replay_s), (n, m) in sorted(by_pair.items(), key=lambda x: -x[1][0])[:10]:
            print(f"  actual={str(actual_s):<20} replay={replay_s:<20} n={n:<5} match={m/n:.0%}")
        for name in ["减速器", "短剧游戏"]:
            sample = [(r, s) for r, s in both if r["name"] == name]
            if sample:
                m = sum(1 for r, s in sample if r["state"] == s)
                print(f"  {name}: {m}/{len(sample)} = {m/len(sample):.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
