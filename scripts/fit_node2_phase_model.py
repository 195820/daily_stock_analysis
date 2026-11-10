"""Node 2 相位模型：按自然月 walk-forward 拟合与评估（离线，只读缓存）。

协议（跑之前定死，结果出来不改）：

1. 样本：`src/got/phase_model.build_panel`，t 日收盘特征，t+1 开盘→t+5 收盘标签。
2. 分割：test 月 M 的样本，只用 date < M 首日的样本训练；不随机打乱、不重复采样。
3. 参赛者：
   - `legacy`：现行闸门（r5_pct≥0.80 且 high20_dist≥-0.08），按 r5_pct 取前 K；
   - `model`：逻辑回归 P(超额>0)，按概率取前 K；
   - `universe`：当日全样本均值（参照，不参与胜负）。
4. 指标：逐日 paired 超额（入选均值 − 当日全样本均值）、命中率、追顶率
   （`entry_top`=买在 10 日高 2% 内的比例）、T+5 绝对收益。
5. 通过判据（预注册）：合并样本外逐日超额 t ≥ 1.5，追顶率 < legacy，且没有任何
   一个测试月逐日超额 < -1.0pp。三者全满足才写生产工件。

用法：
  python scripts/fit_node2_phase_model.py
  python scripts/fit_node2_phase_model.py --topn 4 --first-test 202606
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

from src.got.phase_model import (  # noqa: E402
    FEATURES,
    RANK_FEATURES,
    build_panel,
    fit_logistic,
    predict_proba,
    row_vector,
)

CACHE = ROOT / "data" / "_replay" / "board_ohlcv_cache.json"
OUT_DIR = ROOT / "data" / "_replay" / "ab"
MODEL_DIR = ROOT / "data" / "got_models"

LEGACY_PCT = 0.80
LEGACY_DIST = -0.08


def _tstat(vals: list[float]) -> float | None:
    if len(vals) < 3:
        return None
    sd = st.stdev(vals)
    return None if sd <= 0 else st.mean(vals) / (sd / math.sqrt(len(vals)))


def _legacy_score(row: dict) -> float:
    return row["r5_pct"] if row["r5_pct"] is not None else -1.0


def _pick(rows: list[dict], score, k: int) -> list[dict]:
    return sorted(rows, key=lambda r: (-score(r), r["code"]))[:k]


def _evaluate(rows: list[dict], score, k: int) -> dict:
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        if r["fwd5"] is not None:
            by_day.setdefault(r["date"], []).append(r)
    excess, picked_ret, hits, tops, n = [], [], [], [], 0
    for day, day_rows in by_day.items():
        sel = _pick(day_rows, score, k)
        if not sel:
            continue
        uni = [r["fwd5"] for r in day_rows]
        m = st.mean([r["fwd5"] for r in sel])
        excess.append(m - st.mean(uni))
        for r in sel:
            picked_ret.append(r["fwd5"])
            hits.append(1 if r["fwd5"] > 0 else 0)
            tops.append(r["entry_top"] or 0)
            n += 1
    return {
        "n": n,
        "days": len(excess),
        "mean_t5": round(st.mean(picked_ret), 3) if picked_ret else None,
        "hit": round(100.0 * st.mean(hits), 1) if hits else None,
        "top_rate": round(100.0 * st.mean(tops), 1) if tops else None,
        "day_excess": round(st.mean(excess), 3) if excess else None,
        "day_excess_t": round(_tstat(excess), 2) if _tstat(excess) is not None else None,
        "_excess": excess,
    }


def _auc(rows: list[dict], score) -> float | None:
    pairs = [(score(r), r["y"]) for r in rows if r["y"] is not None]
    pos = sum(1 for _, y in pairs if y > 0.5)
    neg = len(pairs) - pos
    if not pos or not neg:
        return None
    pairs.sort(key=lambda x: x[0])
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        rank_sum += avg * sum(1 for k in range(i, j + 1) if pairs[k][1] > 0.5)
        i = j + 1
    return (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)


def _gate_new(r: dict) -> bool:
    return (r["above_ma20"] == 1.0 and (r["r5"] or 0) > 0
            and (r["r10"] or 0) > 0 and (r["ma20_slope"] or 0) > 0)


def _gate_old(r: dict) -> bool:
    return (r["r5_pct"] or 0) >= LEGACY_PCT and r["high20_dist"] is not None \
        and r["high20_dist"] >= LEGACY_DIST


def _stats(rows: list[dict], gate, topn: int, score_fn) -> dict:
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)
    rets, excess, tops = [], [], []
    for day_rows in by_day.values():
        uni = [r["fwd5"] for r in day_rows]
        sel = [r for r in day_rows if gate(r)]
        if topn:
            sel = sorted(sel, key=lambda r: (-score_fn(r), r["code"]))[:topn]
        if not sel:
            continue
        rets += [r["fwd5"] for r in sel]
        tops += [r["entry_top"] or 0 for r in sel]
        excess.append(st.mean([r["fwd5"] for r in sel]) - st.mean(uni))
    return {
        "n": len(rets),
        "hit": round(100.0 * st.mean([1 if x > 0 else 0 for x in rets]), 1) if rets else None,
        "mean": round(st.mean(rets), 2) if rets else None,
        "excess": round(st.mean(excess), 2) if excess else None,
        "top_rate": round(100.0 * st.mean(tops), 1) if tops else None,
    }


GRID = {
    "pct": (0.50, 0.60, 0.70, 0.80),
    "dist": (-0.02, -0.05, -0.08, -0.12),
    "chg1": (None, 0.05, 0.08),
    "slope": (True, False),
    "vol": (None, 1.0, 1.2),
}


def _combo_pass(r: dict, c: dict) -> bool:
    if r["above_ma20"] != 1.0 or (r["r5"] or 0) <= 0 or (r["r10"] or 0) <= 0:
        return False
    if c["slope"] and (r["ma20_slope"] or 0) <= 0:
        return False
    if c["pct"] is not None and (r["r5_pct"] or 0) < c["pct"]:
        return False
    if c["dist"] is not None and (r["high20_dist"] is None or r["high20_dist"] < c["dist"]):
        return False
    if c["chg1"] is not None and r["chg1"] is not None and r["chg1"] > c["chg1"]:
        return False
    if c["vol"] is not None and (r["vol_ratio"] or 0) < c["vol"]:
        return False
    return True


def _combo_eval(rows: list[dict], c: dict, topn: int) -> dict:
    from src.got.sector_lifecycle import compute_launch_score as score

    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)
    rets, tops, excess = [], [], []
    for day_rows in by_day.values():
        uni = [r["fwd5"] for r in day_rows]
        sel = [r for r in day_rows if _combo_pass(r, c)]
        sel = sorted(sel, key=lambda r: (-score(r["r5_pct"], r["r10"], r["high20_dist"]), r["code"]))[:topn]
        if not sel:
            continue
        rets += [r["fwd5"] for r in sel]
        tops += [r["entry_top"] or 0 for r in sel]
        excess.append(st.mean([r["fwd5"] for r in sel]) - st.mean(uni))
    return {
        "n": len(rets),
        "win": round(100.0 * st.mean([1 if x > 0 else 0 for x in rets]), 1) if rets else None,
        "mean": round(st.mean(rets), 2) if rets else None,
        "excess": round(st.mean(excess), 2) if excess else None,
        "top": round(100.0 * st.mean(tops), 1) if tops else None,
    }


def _combo_str(c: dict) -> str:
    return (f"pct={c['pct'] if c['pct'] is not None else '—'}, "
            f"dist={c['dist'] if c['dist'] is not None else '—'}, "
            f"chg1={c['chg1'] if c['chg1'] is not None else '—'}, "
            f"slope={'on' if c['slope'] else 'off'}, "
            f"vol={c['vol'] if c['vol'] is not None else '—'}")


def _combos():
    out = []
    for pct in GRID["pct"]:
        for dist in GRID["dist"]:
            for chg1 in GRID["chg1"]:
                for slope in GRID["slope"]:
                    for vol in GRID["vol"]:
                        out.append({"pct": pct, "dist": dist, "chg1": chg1,
                                    "slope": slope, "vol": vol})
    return out


def _grid(rows: list[dict], test_months: list[str], args) -> int:
    """阈值组合网格：先看全样本最优（会过拟合），再看逐月 walk-forward 的真实效果。"""
    combos = _combos()
    scored = []
    for c in combos:
        m = _combo_eval(rows, c, args.topn)
        if m["n"] and m["n"] >= 200:
            scored.append((m["win"], c, m))
    scored.sort(key=lambda x: -x[0])
    best_win, best_c, best_m = scored[0]

    # walk-forward：每月用训练段选胜率最高的组合
    wf_rows = [r for r in rows if r["date"][:7] >= test_months[0]]
    wf = []
    for m in test_months:
        train = [r for r in rows if r["date"][:7] < m]
        test = [r for r in wf_rows if r["date"][:7] == m]
        if len(train) < 1500 or not test:
            continue
        cand = []
        for c in combos:
            s = _combo_eval(train, c, args.topn)
            if s["n"] and s["n"] >= 100:
                cand.append((s["win"], c))
        cand.sort(key=lambda x: -x[0])
        pick = cand[0][1]
        ev = _combo_eval(test, pick, args.topn)
        wf.append({"month": m, "pick": pick, "ev": ev})
    base = {"pct": None, "dist": None, "chg1": None, "slope": True, "vol": None}
    base_ev = _combo_eval(wf_rows, base, args.topn)

    lines = ["# Node 2 阈值组合网格（胜率为目标）", "",
             f"- 网格 {len(combos)} 组；每日取 Top{args.topn}；"
             f"样本 {len(rows)} 行 / {len({r['date'] for r in rows})} 日。",
             "- 全部样本最优（**样本内，必然好看**）：",
             f"  - {_combo_str(best_c)} → 胜率 {best_m['win']}% / T+5 {best_m['mean']:+.2f}% / "
             f"超额 {best_m['excess']:+.2f}pp / 追顶 {best_m['top']}% / n={best_m['n']}",
             "", "## 逐月 walk-forward（每月用此前数据选组合，再测当月）", "",
             "| 月 | 选中的组合 | 胜率 | T+5 | 超额 | 追顶 |", "|---|---|---:|---:|---:|---:|"]
    for w in wf:
        e = w["ev"]
        lines.append(f"| {w['month']} | {_combo_str(w['pick'])} | {e['win']}% | "
                     f"{e['mean']:+.2f}% | {e['excess']:+.2f}pp | {e['top']}% |")
    win_wf = [w["ev"]["win"] for w in wf if w["ev"]["win"] is not None]
    lines += ["", f"- walk-forward 平均胜率 **{st.mean(win_wf):.1f}%**（{len(win_wf)} 个月）；"
              f"现行结构门槛同窗口胜率 **{base_ev['win']}%**。", ""]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "node2_threshold_grid.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def _rank(rows: list[dict], test_months: list[str], args) -> int:
    """排序模型（预注册判据）：门槛内样本，Top4 的逐日超额必须显著优于 launch_score。

    判据（跑之前定）：model − baseline 的逐日超额差均值 > 0 且 t ≥ 1.5，
    且追顶率不高于 baseline。两条不满足即不写工件。
    """
    from src.got.sector_lifecycle import compute_launch_score as score

    pool = [r for r in rows if _gate_new(r)]
    lines = ["# Node 2 排序模型（门槛内 Top4）", "",
             f"- 样本：门槛内 {len(pool)} 行 / {len({r['date'] for r in pool})} 日；"
             f"特征 {len(RANK_FEATURES)} 个（含量价 udvr/vpc）；每月 walk-forward。", "",
             "| 月 | 训练行 | AUC | model 超额 | launch_score 超额 | 差 | model 追顶 | 基线追顶 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    diffs, m_tops, b_tops = [], [], []
    m_all, b_all, m_days, b_days = [], [], [], []
    aucs = []
    for m in test_months:
        train = [r for r in pool if r["date"][:7] < m]
        test = [r for r in pool if r["date"][:7] == m]
        if len(train) < 800 or not test:
            continue
        model = fit_logistic(
            [[0.0 if v is None else v for v in row_vector(r, RANK_FEATURES)] for r in train],
            [r["y"] for r in train], l2=args.l2, features=RANK_FEATURES)
        for r in test:
            r["_rp"] = predict_proba(model, r)
        aucs.append(_auc(test, lambda r: r["_rp"]))

        def ev(scorer):
            by_day: dict[str, list[dict]] = {}
            for r in test:
                by_day.setdefault(r["date"], []).append(r)
            ex, rets, tops = [], [], []
            for day_rows in by_day.values():
                uni = [r["fwd5"] for r in day_rows]
                sel = sorted(day_rows, key=lambda r: (-scorer(r), r["code"]))[:args.topn]
                if not sel:
                    continue
                rets += [r["fwd5"] for r in sel]
                tops += [r["entry_top"] or 0 for r in sel]
                ex.append(st.mean([r["fwd5"] for r in sel]) - st.mean(uni))
            return ex, rets, tops

        me, mr, mt = ev(lambda r: r["_rp"])
        be, br, bt = ev(lambda r: score(r.get("r5_pct"), r.get("r10"), r.get("high20_dist")))
        n = min(len(me), len(be))
        d = [me[i] - be[i] for i in range(n)]
        diffs += d
        m_tops += mt; b_tops += bt
        m_all += mr; b_all += br
        lines.append(
            f"| {m} | {len(train)} | {aucs[-1]:.3f} | {st.mean(me):+.2f}pp | {st.mean(be):+.2f}pp | "
            f"{st.mean(d):+.2f}pp | {100*st.mean(mt):.0f}% | {100*st.mean(bt):.0f}% |")

    if not diffs:
        print("no fold", flush=True)
        return 2
    t_diff = _tstat(diffs)
    verdict = (t_diff is not None and st.mean(diffs) > 0 and t_diff >= 1.5
               and st.mean(m_tops) <= st.mean(b_tops))
    lines += ["",
              f"- 合并：model T+5 {st.mean(m_all):+.2f}% / hit {100*st.mean([1 if x>0 else 0 for x in m_all]):.1f}% / "
              f"追顶 {100*st.mean(m_tops):.0f}%；基线 T+5 {st.mean(b_all):+.2f}% / "
              f"hit {100*st.mean([1 if x>0 else 0 for x in b_all]):.1f}% / 追顶 {100*st.mean(b_tops):.0f}%。",
              f"- 逐日超额差：{st.mean(diffs):+.2f}pp（t={t_diff:.2f}）；"
              f"AUC 中位 {st.median(aucs):.3f}。",
              f"- **判定：{'通过' if verdict else '不通过'}**。"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "node2_rank_model.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def _report(rows: list[dict], cache: dict, args) -> int:
    from src.got.sector_lifecycle import compute_launch_score as score

    def compute_score(r):
        return score(r.get("r5_pct"), r.get("r10"), r.get("high20_dist"))

    lines = ["# Node 2 相位门槛：胜率对照与密度（描述性）", "",
             f"- 面板 {len(rows)} 行 / {len({r['date'] for r in rows})} 日 "
             f"（{min(r['date'] for r in rows)} ~ {max(r['date'] for r in rows)}）；"
             "口径 T+1 开盘 → T+5 收盘，超额=当日入选均值−全样本均值。", "",
             "| 口径 | 笔数 | 胜率 | T+5 均值 | 逐日超额 | 追顶率 |",
             "|---|---:|---:|---:|---:|---:|"]
    for name, gate, k in (("新结构 全量", _gate_new, 0), ("新结构 Top4", _gate_new, 4),
                          ("旧阈值 全量", _gate_old, 0), ("旧阈值 Top4", _gate_old, 4),
                          ("全宇宙", lambda r: True, 0)):
        s = _stats(rows, gate, k, compute_score)
        lines.append(f"| {name} | {s['n']} | {s['hit']}% | {s['mean']:+.2f}% | "
                     f"{s['excess']:+.2f}pp | {s['top_rate']}% |")

    # 密度：逐 code 的相位进入次数
    by_code: dict[str, list[dict]] = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)
    starts, passing, leads = {}, {}, []
    for code, day_rows in by_code.items():
        day_rows.sort(key=lambda r: r["date"])
        prev = False
        bars = cache.get(code) or {}
        dates = bars.get("dates") or []
        idx = {d: i for i, d in enumerate(dates)}
        for r in day_rows:
            g = _gate_new(r)
            if g:
                passing[r["date"]] = passing.get(r["date"], 0) + 1
                if not prev:
                    starts[r["date"]] = starts.get(r["date"], 0) + 1
                    i = idx.get(r["date"])
                    if i is not None and i + 20 < len(dates):
                        win = [float(x) for x in bars["close"][i + 1:i + 21]]
                        if win:
                            leads.append(win.index(max(win)) + 1)
            prev = g
    sp = [starts[d] for d in sorted(starts)]
    pp = [passing[d] for d in sorted(passing)]
    lines += ["", "**结构门槛密度**", "",
              f"- 每日新启动：中位 {st.median(sp):.1f} / 均值 {st.mean(sp):.1f} / 最大 {max(sp)}"
              f"（{len(sp)} 个交易日）",
              f"- 每日通过（含延续）：中位 {st.median(pp):.1f} / 均值 {st.mean(pp):.1f} / 最大 {max(pp)}",
              f"- 启动→20 日最高收盘：中位 {st.median(leads):.1f} 日，"
              f"当天即高点 {100.0 * sum(1 for x in leads if x <= 1) / len(leads):.0f}%（n={len(leads)}）"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "node2_phase_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def _screen(rows: list[dict], test_months: list[str], args) -> int:
    """单特征：方向只在 train 上定（正负类均值差），逐月看样本外 AUC。"""
    lines = ["# Node 2 单特征样本外筛查", "",
             "方向由 train 正负类均值差决定；AUC 按月 walk-forward，不做任何阈值搜索。", ""]
    day_count = {m: len({r["date"] for r in rows if r["date"][:7] == m}) for m in test_months}
    test_months = [m for m in test_months if day_count[m] >= 5]  # 不足 5 日的月份不参与结论
    header = "| 特征 | " + " | ".join(test_months) + " | 最差 | 稳定? |"
    lines += [header, "|---|" + "---:|" * len(test_months) + "---:|---|"]
    for f in FEATURES:
        vals, ok = [], True
        for m in test_months:
            train = [r for r in rows if r["date"][:7] < m and r[f] is not None]
            test = [r for r in rows if r["date"][:7] == m and r[f] is not None]
            if not train or not test:
                vals.append(None)
                continue
            mu1 = st.mean([r[f] for r in train if r["y"] > 0.5])
            mu0 = st.mean([r[f] for r in train if r["y"] <= 0.5])
            s = 1.0 if mu1 >= mu0 else -1.0
            vals.append(_auc(test, lambda r, s=s, f=f: s * r[f]))
        real = [v for v in vals if v is not None]
        stable = bool(real) and all(v >= 0.55 for v in real)
        ok = stable
        cells = " | ".join("—" if v is None else f"{v:.3f}" for v in vals)
        lines.append(f"| {f} | {cells} | {min(real):.3f} | {'是' if ok else '否'} |")
    lines += ["", f"- 样本 {len(rows)} 行 / {len({r['date'] for r in rows})} 日；"
              f"测试月 {', '.join(test_months)}；稳定判据：每月 AUC ≥ 0.55。"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "node2_feature_screen.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topn", type=int, default=4)
    ap.add_argument("--first-test", default="202608")
    ap.add_argument("--l2", type=float, default=1.0)
    ap.add_argument("--screen", action="store_true",
                    help="单特征样本外筛查（方向只由 train 定），不拟合多变量模型")
    ap.add_argument("--grid", action="store_true",
                    help="门槛阈值组合网格：全样本最优 + 逐月 walk-forward 选择")
    ap.add_argument("--rank", action="store_true",
                    help="排序模型：只在门槛内样本上拟合，Top4 对照 launch_score")
    ap.add_argument("--report", action="store_true",
                    help="新旧闸门胜率对照 + 结构门槛密度（描述性，不参与选参）")
    args = ap.parse_args()

    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    rows = build_panel(cache)
    usable = [r for r in rows if r["y"] is not None]
    dates = sorted({r["date"] for r in usable})
    print(f"panel: {len(rows)} rows / {len(usable)} labeled / {len(dates)} days "
          f"({dates[0]} ~ {dates[-1]})", flush=True)

    months = sorted({d[:7] for d in dates})
    test_months = [m for m in months if m.replace("-", "") >= args.first_test]
    _dc = {m: len({r["date"] for r in usable if r["date"][:7] == m}) for m in test_months}
    test_months = [m for m in test_months if _dc[m] >= 5]  # 不足 5 日的月份不参与结论

    if args.screen:
        return _screen(usable, test_months, args)
    if args.report:
        return _report(usable, cache, args)
    if args.rank:
        return _rank(usable, test_months, args)
    if args.grid:
        return _grid(usable, test_months, args)
    fold_rows, legacy_rows, model_rows = [], [], []
    report = []
    for m in test_months:
        train = [r for r in usable if r["date"][:7] < m]
        test = [r for r in usable if r["date"][:7] == m]
        if len(train) < 1500 or not test:
            continue
        model = fit_logistic(
            [[0.0 if v is None else v for v in row_vector(r)] for r in train],
            [r["y"] for r in train], l2=args.l2)
        for r in test:
            r["_p"] = predict_proba(model, r)
        ev_m = _evaluate(test, lambda r: r["_p"], args.topn)
        ev_l = _evaluate(test, _legacy_score, args.topn)  # 同容量：纯 5 日动量前 K
        ev_g = _evaluate([r for r in test if r["r5_pct"] >= LEGACY_PCT
                          and r["high20_dist"] is not None
                          and r["high20_dist"] >= LEGACY_DIST],
                         _legacy_score, args.topn)
        auc = _auc(test, lambda r: r["_p"])
        report.append({
            "month": m,
            "train_rows": len(train),
            "test_rows": len(test),
            "auc": round(auc, 3) if auc is not None else None,
            "model": {k: v for k, v in ev_m.items() if not k.startswith("_")},
            "legacy": {k: v for k, v in ev_l.items() if not k.startswith("_")},
            "legacy_gate": {k: v for k, v in ev_g.items() if not k.startswith("_")},
            "_model_excess": ev_m["_excess"],
            "_legacy_excess": ev_l["_excess"],
        })
        fold_rows.append(model)
        model_rows.extend(test)
        legacy_rows.extend(test)
        print(f"  {m}: auc={report[-1]['auc']} model={ev_m['day_excess']:+.2f}pp "
              f"legacy={ev_l['day_excess']:+.2f}pp n={ev_m['n']}", flush=True)

    if not report:
        print("no fold", flush=True)
        return 2

    pooled = []
    for r in report:
        pooled.extend(r["_model_excess"])
    pooled_legacy = []
    for r in report:
        pooled_legacy.extend(r["_legacy_excess"])
    t_model = _tstat(pooled)
    t_legacy = _tstat(pooled_legacy)
    model_metrics = _evaluate(model_rows, lambda r: r["_p"], args.topn)
    legacy_metrics = _evaluate(legacy_rows, _legacy_score, args.topn)

    verdict = (
        t_model is not None and t_model >= 1.5
        and model_metrics["top_rate"] is not None
        and legacy_metrics["top_rate"] is not None
        and model_metrics["top_rate"] < legacy_metrics["top_rate"]
        and all(r["model"]["day_excess"] > -1.0 for r in report)
    )

    lines = [
        "# Node 2 相位模型 walk-forward 评估",
        "",
        f"- 样本：{len(usable)} 行 / {len(dates)} 日；测试月 {', '.join(r['month'] for r in report)}；"
        f"每日取前 {args.topn}。",
        "- 判据（预注册）：合并样本外逐日超额 t ≥ 1.5、",
        "  追顶率低于 legacy、无单月 < -1.0pp。",
        "",
        "| 月 | AUC | model 超额 | legacy 超额 | model 追顶 | legacy 追顶 | n |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in report:
        lines.append(
            f"| {r['month']} | {r['auc']} | {r['model']['day_excess']:+.2f}pp | "
            f"{r['legacy']['day_excess']:+.2f}pp | {r['model']['top_rate']}% | "
            f"{r['legacy']['top_rate']}% | {r['model']['n']} |")
    lines += [
        "",
        f"- 合并：model 超额 {st.mean(pooled):+.2f}pp（t={t_model:.2f}），"
        f"legacy {st.mean(pooled_legacy):+.2f}pp（t={t_legacy:.2f}）。",
        f"- 合并 T+5：model {model_metrics['mean_t5']:+.2f}% / hit {model_metrics['hit']}% / "
        f"追顶 {model_metrics['top_rate']}%；legacy {legacy_metrics['mean_t5']:+.2f}% / "
        f"hit {legacy_metrics['hit']}% / 追顶 {legacy_metrics['top_rate']}%。",
        f"- **判定：{'通过' if verdict else '不通过'}**（阈值仅此一次评估，未用于调参）。",
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "node2_phase_model.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    payload = {
        "protocol": {"topn": args.topn, "first_test": args.first_test, "l2": args.l2,
                     "features": list(FEATURES), "horizon": 5},
        "folds": [{k: v for k, v in r.items() if not k.startswith("_")} for r in report],
        "pooled": {
            "model_day_excess": round(st.mean(pooled), 3),
            "model_t": round(t_model, 2) if t_model is not None else None,
            "legacy_day_excess": round(st.mean(pooled_legacy), 3),
            "legacy_t": round(t_legacy, 2) if t_legacy is not None else None,
            "model": {k: v for k, v in model_metrics.items() if not k.startswith("_")},
            "legacy": {k: v for k, v in legacy_metrics.items() if not k.startswith("_")},
        },
        "verdict": "pass" if verdict else "fail",
    }
    (OUT_DIR / "node2_phase_model.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if verdict:
        # 用全部样本重拟合，写工件（生产只读这个文件）
        final = fit_logistic(
            [[0.0 if v is None else v for v in row_vector(r)] for r in usable],
            [r["y"] for r in usable], l2=args.l2)
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        artifact = {
            "version": 1,
            "trained_until": dates[-1],
            "rows": len(usable),
            "features": list(FEATURES),
            "mean": final["mean"],
            "std": final["std"],
            "coef": final["coef"],
            "intercept": final["intercept"],
            "oos": payload["pooled"],
            "verdict": "pass",
        }
        (MODEL_DIR / "node2_phase_model.json").write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        print("artifact written", flush=True)
    print("\n".join(lines[-8:]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
