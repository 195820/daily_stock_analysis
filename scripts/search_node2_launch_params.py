"""启动状态参数搜索（in-sample，目标：把「临启动」的参数找出来）。

用户要求：先不管过拟合，把启动状态的参数组合找出来。方法：

1. 面板：板块-日（≥61 根 K 线），标签 = T+1 开盘 → T+N 收盘，横截面超额（减当日全板块均值）。
2. 单条件扫描：位置（距 20 日高）、动量（r5/r10/r20）、趋势（MA20 斜率/站上）、
   量能（量比/涨跌量比）、波动分位、以及「临启动」结构（MA20 刚上穿、r5 刚转正）。
3. 贪心组合：从单条件里按 t 最高的开始，逐条 AND 上最能提升 t 的条件，最多 4 条。
4. 输出：参数组合、事件数、天数、各持有期超额/t/胜率；附样本前后半段对照与多重检验标尺。

多重检验标尺（必须同时看）：本脚本评估的组合数 N ≈ 数百到数千，零假设下最大 |t|
期望约 sqrt(2·ln N) ≈ 3.5~4.5。所以 in-sample 的 t 在 3~4 之间基本等于噪声；
只有 t 明显超过该标尺、且前后半段同号，才值得进入 walk-forward。

用法：
  python scripts/search_node2_launch_params.py
"""

from __future__ import annotations

import bisect
import json
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

CACHE = ROOT / "data" / "_replay" / "board_ohlcv_cache.json"
OUT = ROOT / "data" / "_replay" / "ab" / "node2_launch_param_search.md"
HORIZONS = (1, 3, 5, 10)
BH_ALPHA = 0.05


def _p_two_sided(t: float) -> float:
    """双侧 p 值（正态近似）。

    df = 每日观测数 − 1，本面板每日 ≥283 个板块-日，正态近似误差可忽略。
    """
    return math.erfc(abs(t) / math.sqrt(2.0))


def main() -> int:
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    dates = sorted({d for b in cache.values() for d in (b.get("dates") or [])})
    day_idx = {d: i for i, d in enumerate(dates)}
    D = len(dates)

    rows = []  # 每个板块-日
    for code, b in cache.items():
        closes = [float(x) for x in b["close"]]
        highs = [float(x) for x in b["high"]]
        vols = [float(x) for x in b["volume"]]
        ds = b["dates"]
        for i in range(61, len(closes)):
            close = closes[i]
            if close <= 0:
                continue
            ma20 = sum(closes[i - 19:i + 1]) / 20
            prev_ma20 = sum(closes[i - 24:i - 4]) / 20
            ma20_prev5 = sum(closes[i - 24:i - 4]) / 20
            high20 = max(highs[i - 19:i + 1])
            close20 = max(closes[i - 19:i + 1])
            v5 = sum(vols[i - 4:i + 1]) / 5
            v20 = sum(vols[i - 19:i + 1]) / 20
            rets = [closes[j] / closes[j - 1] - 1 for j in range(i - 19, i + 1)]
            up_v = down_v = 0.0
            for j in range(i - 9, i + 1):
                if closes[j] > closes[j - 1]:
                    up_v += vols[j]
                elif closes[j] < closes[j - 1]:
                    down_v += vols[j]
            fwd = {}
            ok = True
            for h in HORIZONS:
                if i + h >= len(closes):
                    ok = False
                    break
                entry = float(b["open"][i + 1])
                fwd[h] = (closes[i + h] / entry - 1.0) * 100.0 if entry > 0 else None
            if not ok:
                continue
            rows.append({
                "di": day_idx[ds[i]],
                "r5": close / closes[i - 5] - 1,
                "r10": close / closes[i - 10] - 1,
                "r20": close / closes[i - 20] - 1,
                "r60": close / closes[i - 60] - 1,
                "slope": ma20 / prev_ma20 - 1 if prev_ma20 else 0.0,
                "above": close > ma20,
                "above_5": closes[i - 5] > ma20_prev5,
                "r5_lag5": closes[i - 5] / closes[i - 10] - 1,
                "dist": close / high20 - 1,
                "pos": close / close20 if close20 else 0.0,
                "vol_ratio": v5 / v20 if v20 > 0 else 1.0,
                "udvr": up_v / down_v if down_v > 0 else 2.0,
                "vol20": float(np.std(rets)) if rets else 0.0,
                "fwd": fwd,
            })

    N = len(rows)
    di = np.array([r["di"] for r in rows], dtype=int)
    col = lambda k: np.array([r[k] for r in rows], dtype=float)
    r5, r10, r20, r60 = col("r5"), col("r10"), col("r20"), col("r60")
    slope, dist, pos = col("slope"), col("dist"), col("pos")
    vol_ratio, udvr, vol20 = col("vol_ratio"), col("udvr"), col("vol20")
    above = col("above")
    above_5 = col("above_5")
    r5_lag5 = col("r5_lag5")

    # 每组合每日全板块基准
    uni = np.zeros((D, len(HORIZONS)))
    for k, h in enumerate(HORIZONS):
        vals = np.array([r["fwd"][h] for r in rows], dtype=float)
        s = np.bincount(di, weights=vals, minlength=D)
        c = np.bincount(di, minlength=D)
        uni[:, k] = np.divide(s, c, out=np.full(D, np.nan), where=c > 0)

    excess = {}
    fwdm = {}
    for k, h in enumerate(HORIZONS):
        vals = np.array([r["fwd"][h] for r in rows], dtype=float)
        fwdm[h] = vals
        excess[h] = vals - uni[di, k]

    # 波动分位（按日）
    volpct = np.zeros(N)
    for d in range(D):
        m = di == d
        if m.sum() < 50:
            continue
        order = np.argsort(vol20[m])
        rk = np.empty(len(order))
        rk[order] = np.arange(len(order)) / max(len(order) - 1, 1)
        volpct[m] = rk

    conds: dict[str, np.ndarray] = {
        "dist<=-10%": dist <= -0.10,
        "dist-10~-5%": (dist > -0.10) & (dist <= -0.05),
        "dist-5~-2%": (dist > -0.05) & (dist <= -0.02),
        "dist-2~0%": (dist > -0.02) & (dist <= 0),
        "dist>0": dist > 0,
        "r5<=0": r5 <= 0,
        "r5 0~2%": (r5 > 0) & (r5 <= 0.02),
        "r5 2~5%": (r5 > 0.02) & (r5 <= 0.05),
        "r5>5%": r5 > 0.05,
        "r10<=0": r10 <= 0,
        "r10>0": r10 > 0,
        "r20<=0": r20 <= 0,
        "r20 0~10%": (r20 > 0) & (r20 <= 0.10),
        "r20>10%": r20 > 0.10,
        "slope>0": slope > 0,
        "slope<=0": slope <= 0,
        "above_ma20": above > 0,
        "below_ma20": above <= 0,
        "vol_ratio<0.8": vol_ratio < 0.8,
        "vol_ratio 0.8-1.2": (vol_ratio >= 0.8) & (vol_ratio <= 1.2),
        "vol_ratio>1.2": vol_ratio > 1.2,
        "udvr<1": udvr < 1,
        "udvr>=1.5": udvr >= 1.5,
        "vol20 low30%": volpct <= 0.30,
        "vol20 high30%": volpct >= 0.70,
        # 临启动结构
        "MA20刚上穿": (above > 0) & (above_5 <= 0),
        "r5刚转正": (r5 > 0) & (r5_lag5 <= 0),
        "距20日收盘高<=3%": pos >= 0.97,
    }

    def stat(mask, h, day_ok=None):
        if day_ok is not None:
            mask = mask & day_ok[di]
        if mask.sum() < 100:
            return None
        d = di[mask]
        vals = excess[h][mask]
        s = np.bincount(d, weights=vals, minlength=D)
        c = np.bincount(d, minlength=D)
        keep = c > 0
        daily = s[keep] / c[keep]
        if len(daily) < 30:
            return None
        m = float(daily.mean())
        sd = float(daily.std(ddof=1))
        raw_daily = 0.0
        if day_ok is None:
            raw_daily = float((np.bincount(d, weights=fwdm[h][mask], minlength=D)[keep]
                               / c[keep]).mean())
        return {"days": int(keep.sum()), "events": int(mask.sum()), "mean": m,
                "raw": raw_daily, "raw_event": float(fwdm[h][mask].mean()),
                "uni": float(np.nanmean(uni[keep, HORIZONS.index(h)])),
                "t": m / (sd / math.sqrt(len(daily))) if sd else 0.0,
                "win": float((daily > 0).mean() * 100)}

    def split_stat(mask, h):
        d = di[mask]
        vals = excess[h][mask]
        s = np.bincount(d, weights=vals, minlength=D)
        c = np.bincount(d, minlength=D)
        keep = c > 0
        daily = s[keep] / c[keep]
        if len(daily) < 40:
            return None
        half = len(daily) // 2
        return float(daily[:half].mean()), float(daily[half:].mean())

    # ── 单条件扫描 ──
    # `evaluated` = 检验族：本次搜索真正算过 t 的每一个组合（键=条件名有序元组）
    evaluated: dict[tuple[str, ...], dict] = {}
    uni_rows = []
    for name, mask in conds.items():
        s = stat(mask, 5)
        if s:
            evaluated[(name,)] = s
            uni_rows.append((name, s, s["t"]))
    uni_rows.sort(key=lambda x: -abs(x[2]))

    # ── 贪心 AND 组合（可选只在训练日选择）──
    names = list(conds)

    def greedy(day_ok=None):
        out = []
        rank = []
        for name in names:
            s = stat(conds[name], 5, day_ok)
            if s:
                rank.append((abs(s["t"]), name))
        rank.sort(key=lambda x: -x[0])
        for _, seed in rank[:6]:
            cur_mask = conds[seed].copy()
            cur_names = [seed]
            cur_stat = stat(cur_mask, 5, day_ok)
            if not cur_stat:
                continue
            if day_ok is None:
                evaluated.setdefault(tuple(sorted(cur_names)), cur_stat)
            while len(cur_names) < 4:
                best_add = None
                for name in names:
                    if name in cur_names:
                        continue
                    m = cur_mask & conds[name]
                    s = stat(m, 5, day_ok)
                    if day_ok is None and s:
                        evaluated.setdefault(tuple(sorted(cur_names + [name])), s)
                    if s and s["days"] >= 40 and s["events"] >= 300:
                        if best_add is None or s["t"] > best_add[0]:
                            best_add = (s["t"], name, m, s)
                if best_add is None or best_add[0] <= cur_stat["t"] + 0.05:
                    break
                _, name, cur_mask, cur_stat = best_add
                cur_names.append(name)
            out.append((cur_stat["t"], cur_names, cur_mask))
        out.sort(key=lambda x: -x[0])
        return out

    best_overall = [(x[0], x[1], x[2], stat(x[2], 5)) for x in greedy()]
    best_overall = [x for x in best_overall if x[3] and x[3]["mean"] > 0]
    best_overall.sort(key=lambda x: -x[0])

    # ── BH-FDR：检验族 = 本次实际评估过的全部组合（单条件 + 贪心逐条扩展）──
    # P1 是待上线的生产判据，显式评估并入族（不额外扩族，只补它自己）。
    p1_key = tuple(sorted(("above_ma20", "dist-10~-5%", "r5 2~5%", "vol20 high30%")))
    if p1_key not in evaluated:
        p1_mask = conds[p1_key[0]].copy()
        for _n in p1_key[1:]:
            p1_mask &= conds[_n]
        p1_stat = stat(p1_mask, 5)
        if p1_stat:
            evaluated[p1_key] = p1_stat
    p1_stat = evaluated.get(p1_key)

    items = [(names_, s) for names_, s in evaluated.items() if s]
    items.sort(key=lambda x: _p_two_sided(x[1]["t"]))
    n_tests = len(items)
    bh_k = 0
    for k, (_, s) in enumerate(items, 1):
        if _p_two_sided(s["t"]) <= k / n_tests * BH_ALPHA:
            bh_k = k
    bh_survivors = items[:bh_k]
    bh_crit_p = (bh_k / n_tests * BH_ALPHA) if bh_k else 0.0

    lines = ["# 启动状态参数搜索（in-sample，按 t 排序）", "",
             f"- 板块-日 {N} 行 / {D} 天；标签为 T+1 开盘 → T+N 收盘的横截面超额。",
             f"- 组合评估数约 {len(conds) * 6 * 4} 组；零假设最大 |t| 期望 ≈ "
             f"{math.sqrt(2 * math.log(max(len(conds) * 6 * 4, 2))):.1f}，"
             "低于该值的 in-sample 最优基本是噪声。", "",
             "## 单条件 Top 12（持有 5 日）", "",
             "| 条件 | 天数 | 事件 | 日均超额 | t | 日胜率 |", "|---|---:|---:|---:|---:|---:|"]
    for name, s, _ in uni_rows[:12]:
        lines.append(f"| {name} | {s['days']} | {s['events']} | {s['mean']:+.2f}pp | "
                     f"{s['t']:.2f} | {s['win']:.0f}% |")

    lines += ["", "## 贪心组合 Top 8（持有 5 日，AND 条件）", "",
              "| 组合 | 天数 | 事件 | 超额 | t | 胜率 | 前半段 | 后半段 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for t, names_, mask, s in best_overall[:8]:
        sp = split_stat(mask, 5)
        half = f"{sp[0]:+.2f} / {sp[1]:+.2f}" if sp else "—"
        lines.append(f"| {' ∧ '.join(names_)} | {s['days']} | {s['events']} | "
                     f"{s['mean']:+.2f}pp | {s['t']:.2f} | {s['win']:.0f}% | {half} |")

    # ── BH-FDR 输出 ──
    lines += ["", "## BH-FDR（检验族 = 本次实际评估过的组合）", "",
              f"- 检验族 N = {n_tests}；α = {BH_ALPHA}；BH 存活 k = {bh_k}"
              f"（临界 p = {bh_crit_p:.4f}）；"
              f"零假设下最大 |t| 期望 ≈ {math.sqrt(2 * math.log(max(n_tests, 2))):.1f}。",
              "- 判定口径：p = erfc(|t|/√2)（正态近似）；排序后取满足 p(k) ≤ k/N·α 的最大 k，"
              "前 k 个组合为存活集（BH 保证 FDR ≤ α，不保证每个都有效）。"]
    if bh_survivors:
        lines += ["", "| 组合 | t | p | 日均超额 | 天数 | 事件 |", "|---|---:|---:|---:|---:|---:|"]
        for names_, s in bh_survivors:
            lines.append(f"| {' ∧ '.join(names_)} | {s['t']:.2f} | {_p_two_sided(s['t']):.4f} | "
                         f"{s['mean']:+.2f}pp | {s['days']} | {s['events']} |")
    else:
        lines.append("- **无组合通过 BH**：在 N 次检验下，本次搜索的 in-sample 最优也不足以排除噪声。")

    lines.append("")
    if p1_stat is None:
        lines.append("- **P1**（"
                     + " ∧ ".join(p1_key) + "）：样本不足（事件/天数不够），无法评估 → 未通过 BH，归档标注。")
    else:
        p1_p = _p_two_sided(p1_stat["t"])
        passed = p1_key in {k for k, _ in bh_survivors}
        lines.append(
            f"- **P1**（{' ∧ '.join(p1_key)}）：t = {p1_stat['t']:.2f}，p = {p1_p:.4f}，"
            f"日均超额 {p1_stat['mean']:+.2f}pp（{p1_stat['days']} 天 / {p1_stat['events']} 事件）→ "
            + ("**通过 BH**" if passed else "**未通过 BH，归档标注**（不据此做 alpha 声明）")
        )

    if best_overall:
        top_t, top_names, top_mask, top_s = best_overall[0]
        lines += ["", f"## 最优组合的多持有期读数：{' ∧ '.join(top_names)}", "",
                  "| 持有 | 天数 | 事件 | 绝对收益(日均) | 绝对收益(事件均值) | 同期全宇宙 | 超额 | t | 日胜率 |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for h in HORIZONS:
            s = stat(top_mask, h)
            if s:
                lines.append(f"| {h}日 | {s['days']} | {s['events']} | {s['raw']:+.2f}% | "
                             f"{s['raw_event']:+.2f}% | {s['uni']:+.2f}% | {s['mean']:+.2f}pp | "
                             f"{s['t']:.2f} | {s['win']:.0f}% |")

    # ── 走查：仅在样本前 60% 日选择，后 40% 日评估 ──
    split = int(D * 0.6)
    train_ok = np.arange(D) < split
    test_ok = ~train_ok
    wf = []
    for _t, names_, mask in greedy(train_ok):
        s_tr = stat(mask, 5, train_ok)
        s_te = stat(mask, 5, test_ok)
        if s_tr and s_te and s_tr["mean"] > 0 and s_tr["t"] >= 1.5:
            wf.append((names_, s_tr, s_te))
    # 固定参数的前/后段读数（不重新选参）
    lines += ["", "## 固定参数：全样本最优组合的前 60% / 后 40% 拆分", "",
              "| 组合 | 前段超额 | 前段 t | 后段超额 | 后段 t | 后段胜率 |",
              "|---|---:|---:|---:|---:|---:|"]
    for _t, names_, mask, _s in best_overall[:3]:
        a = stat(mask, 5, train_ok)
        b = stat(mask, 5, test_ok)
        if a and b:
            lines.append(f"| {' ∧ '.join(names_)} | {a['mean']:+.2f}pp | {a['t']:.2f} | "
                         f"{b['mean']:+.2f}pp | {b['t']:.2f} | {b['win']:.0f}% |")

    lines += ["", "## 走查（前 60% 日选参 → 后 40% 日评估，持有 5 日）", "",
              "| 组合 | 训练超额 | 训练 t | 测试超额 | 测试 t | 测试胜率 |",
              "|---|---:|---:|---:|---:|---:|"]
    for names_, a, b in wf[:6]:
        lines.append(f"| {' ∧ '.join(names_)} | {a['mean']:+.2f}pp | {a['t']:.2f} | "
                     f"{b['mean']:+.2f}pp | {b['t']:.2f} | {b['win']:.0f}% |")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
