"""Node 2 实际产出板块篮子的前向检验（PIT，无重跑）。

对象：`data/got_daily/YYYYMMDD/sector.json` 里真实写出的 `active_mainline` /
`ambush` / `industry_top`，而不是离线重建的候选集。

口径：t 日归档 → t+1 开盘买入 → t+5 收盘卖出（与 phase_model 标签一致），
篮内等权；基准 = 当日全缓存板块等权（358 个）。超额 = 篮子 − 基准。

用途：回答「Node 2 输出的板块本身有没有用」，并把
`launch`（已判负）与实际交易通道（mainline / ambush）分开。
"""

from __future__ import annotations

import glob
import json
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

CACHE = ROOT / "data" / "_replay" / "board_ohlcv_cache.json"
OUT = ROOT / "data" / "_replay" / "ab" / "node2_boards_forward.md"


def main() -> int:
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    idx = {c: {d: i for i, d in enumerate(b.get("dates") or [])} for c, b in cache.items()}

    def fwd(code: str, date: str):
        b = cache.get(code)
        i = idx[code].get(date) if b else None
        if i is None or i + 5 >= len(b["dates"]):
            return None
        try:
            entry = float(b["open"][i + 1])
            exit_ = float(b["close"][i + 5])
            look = [float(x) for x in b["close"][i + 1:i + 11]]
        except (KeyError, TypeError, ValueError, IndexError):
            return None
        if entry <= 0:
            return None
        top = 1.0 if look and entry >= 0.98 * max(look) else 0.0
        return (exit_ / entry - 1.0) * 100.0, top

    files = sorted(glob.glob(str(ROOT / "data" / "got_daily" / "*" / "sector.json")))
    days = []
    for f in files:
        raw = Path(f).parent.name
        date = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        uni = [v[0] for c in cache if (v := fwd(c, date))]
        if len(uni) < 50:
            continue
        base = st.mean(uni)
        ml = []
        for g in data.get("active_mainline") or []:
            for m in g.get("members") or []:
                ml.append(m.get("code"))
        ml = [c for c in dict.fromkeys(ml) if c in cache]
        amb = [r.get("code") for r in (data.get("ambush") or []) if r.get("code") in cache]
        ind = [r.get("code") for r in (data.get("industry_top") or []) if r.get("code") in cache]
        row = {"date": date, "base": base, "ml": None, "amb": None, "ind": None,
               "ml_n": len(ml), "amb_n": len(amb), "ind_n": len(ind)}
        for key, codes in (("ml", ml), ("amb", amb), ("ind", ind)):
            vals = [v for c in codes if (v := fwd(c, date))]
            if vals:
                row[key] = st.mean([x[0] for x in vals]) - base
        days.append(row)

    def summ(key: str):
        xs = [d[key] for d in days if d[key] is not None]
        if len(xs) < 3:
            return None
        m = st.mean(xs)
        sd = st.stdev(xs)
        return {"n": len(xs), "mean": m, "t": m / (sd / math.sqrt(len(xs))) if sd else 0.0,
                "win": 100.0 * sum(1 for x in xs if x > 0) / len(xs),
                "sd": sd}

    res = {k: summ(k) for k in ("ml", "amb", "ind")}
    lines = ["# Node 2 实际产出板块的前向超额（归档 sector.json，PIT）", "",
             f"- 归档日 {len(days)} 个（{days[0]['date']} ~ {days[-1]['date']}）；"
             f"T+1 开盘 → T+5 收盘；基准 = 当日全板块等权。", "",
             "| 通道 | 天数 | 日均超额 | t | 正超额占比 | 日内标准差 | 平均板块数 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for k, lab in (("ml", "active_mainline 成员"), ("amb", "ambush"), ("ind", "industry_top")):
        r = res[k]
        if not r:
            lines.append(f"| {lab} | 0 | — | — | — | — | — |")
            continue
        n = st.mean([d[f"{k}_n"] for d in days])
        lines.append(f"| {lab} | {r['n']} | {r['mean']:+.2f}pp | {r['t']:.2f} | "
                     f"{r['win']:.0f}% | {r['sd']:.2f}pp | {n:.1f} |")

    lines += ["", "## 逐日", "", "| 日 | 基准 T+5 | 主线超额 | 埋伏超额 | 行业超额 |",
              "|---|---:|---:|---:|---:|"]
    for d in days:
        fmt = lambda x: "—" if x is None else f"{x:+.2f}pp"
        lines.append(f"| {d['date']} | {d['base']:+.2f}% | {fmt(d['ml'])} | "
                     f"{fmt(d['amb'])} | {fmt(d['ind'])} |")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:14]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
