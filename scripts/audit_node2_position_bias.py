"""Node 2 「高位接盘」量化诊断（归档 sector.json + 板块 K 线，PIT）。

回答三个问题：

1. 实际输出的 mainline / ambush 板块，离 20 日高有多近（dist 分布）？
2. strength_score 与 dist 的关系有多强（是不是「涨得越多分越高」）？
3. 篮子内部，贴高（dist > -2%）与非贴高的前向 5 日超额差多少？

口径：t 日归档收盘计算 dist；收益 t+1 开盘 → t+5 收盘；基准 = 当日全板块等权。
"""

from __future__ import annotations

import glob
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

CACHE = ROOT / "data" / "_replay" / "board_ohlcv_cache.json"


def main() -> int:
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    idx = {c: {d: i for i, d in enumerate(b.get("dates") or [])} for c, b in cache.items()}

    def dist_at(code: str, date: str):
        b = cache.get(code)
        i = idx[code].get(date) if b else None
        if i is None or i < 19:
            return None
        try:
            close = float(b["close"][i])
            high = max(float(x) for x in b["high"][i - 19:i + 1])
        except (KeyError, TypeError, ValueError):
            return None
        return close / high - 1.0 if high > 0 else None

    def fwd(code: str, date: str):
        b = cache.get(code)
        i = idx[code].get(date) if b else None
        if i is None or i + 5 >= len(b["dates"]):
            return None
        try:
            return (float(b["close"][i + 5]) / float(b["open"][i + 1]) - 1.0) * 100.0
        except (KeyError, TypeError, ValueError, IndexError):
            return None

    rows = []
    for f in sorted(glob.glob(str(ROOT / "data" / "got_daily" / "*" / "sector.json"))):
        raw = Path(f).parent.name
        date = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        uni = [v for c in cache if (v := fwd(c, date)) is not None]
        if len(uni) < 50:
            continue
        base = st.mean(uni)
        ml = []
        for g in data.get("active_mainline") or []:
            for m in g.get("members") or []:
                ml.append(m)
        ml = list({m.get("code"): m for m in ml if m.get("code")}.values())
        for role, items in (("mainline", ml), ("ambush", data.get("ambush") or [])):
            for m in items:
                code = m.get("code")
                d = dist_at(code, date)
                fv = fwd(code, date)
                if d is None or fv is None:
                    continue
                rows.append({"date": date, "role": role, "code": code,
                             "dist": d, "fwd": fv, "excess": fv - base,
                             "strength": m.get("strength_score")})

    def pct(vals, q):
        vals = sorted(vals)
        return vals[min(len(vals) - 1, int(q * len(vals)))]

    lines = ["# Node 2 输出板块的位置偏差诊断（PIT，归档 sector.json）", ""]
    for role in ("mainline", "ambush"):
        rs = [r for r in rows if r["role"] == role]
        if not rs:
            continue
        ds = [r["dist"] for r in rs]
        lines += [f"## {role}（{len(rs)} 个板块-日，{len({r['date'] for r in rs})} 天）", "",
                  f"- dist 中位数 {st.median(ds)*100:.1f}%，均值 {st.mean(ds)*100:.1f}%；"
                  f"分位 10/25/75/90 = {pct(ds,0.1)*100:.1f} / {pct(ds,0.25)*100:.1f} / "
                  f"{pct(ds,0.75)*100:.1f} / {pct(ds,0.9)*100:.1f}%。",
                  f"- 贴高占比：> -2% **{100*sum(1 for x in ds if x > -0.02)/len(ds):.0f}%**，"
                  f"> -5% {100*sum(1 for x in ds if x > -0.05)/len(ds):.0f}%，"
                  f"> -10% {100*sum(1 for x in ds if x > -0.10)/len(ds):.0f}%。"]
        pair = [(r["strength"], r["dist"]) for r in rs if r["strength"] is not None]
        if len(pair) > 10:
            xs = [p[0] for p in pair]; ys = [p[1] for p in pair]
            mx, my = st.mean(xs), st.mean(ys)
            sxy = sum((a - mx) * (b - my) for a, b in pair)
            sxx = sum((a - mx) ** 2 for a in xs); syy = sum((b - my) ** 2 for b in ys)
            corr = sxy / (sxx * syy) ** 0.5 if sxx and syy else 0.0
            lines.append(f"- strength 与 dist 相关系数 **{corr:+.2f}**")
        hi = [r["excess"] for r in rs if r["dist"] > -0.02]
        lo = [r["excess"] for r in rs if r["dist"] <= -0.02]
        lines.append(f"- 前向超额：贴高 {st.mean(hi):+.2f}pp（n={len(hi)}） vs "
                     f"非贴高 {st.mean(lo):+.2f}pp（n={len(lo)}）")
        lines.append("")

    # 全强度榜的位置分布（机制：strength 的定义本身）
    tops = []
    for f in sorted(glob.glob(str(ROOT / "data" / "got_daily" / "*" / "sector.json"))):
        raw = Path(f).parent.name
        date = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        sl = data.get("strength_lookup") or {}
        name_to_code = {}
        for role in ("ambush", "industry_top"):
            for m in data.get(role) or []:
                if m.get("name"):
                    name_to_code[m["name"]] = m.get("code")
        for g in data.get("active_mainline") or []:
            for m in g.get("members") or []:
                if m.get("name"):
                    name_to_code[m["name"]] = m.get("code")
        ranked = sorted(sl.items(), key=lambda kv: -kv[1])[:10]
        for name, score in ranked:
            code = name_to_code.get(name)
            d = dist_at(code, date) if code else None
            if d is not None:
                tops.append(d)
    if tops:
        lines += ["## 全市场 strength Top10 的位置（机制验证）", "",
                  f"- 样本 {len(tops)} 个板块-日；dist 中位数 {st.median(tops)*100:.1f}%，"
                  f"贴高（> -2%）占比 **{100*sum(1 for x in tops if x > -0.02)/len(tops):.0f}%**。"]

    OUT = ROOT / "data" / "_replay" / "ab" / "node2_position_bias.md"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
