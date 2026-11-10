"""Node 2 整体重建：横截面排序模型 + 周频组合回测（预注册协议）。

推翻对象：`can_start` 的 7 条合取门槛 + 手写 `launch_score` 排序。
新方案：单一模型给每日全部板块打分，取 Top4；不再有启动/延续/退出阈值。

协议（跑之前定死，不得看结果后改）：

- 标签：`y = fwd_excess5 > 0`（t+1 开盘买入 → t+5 收盘，横截面超额）。
- 特征（t 日收盘可得，14 维）：r5 / r10 / r60 / r5_pct / high20_dist / high20_pos /
  above_ma20 / ma20_slope / chg1 / vol_ratio / vol20 / udvr / vpc。
  每日横截面 rank 归一化到 (0,1)（缺失 = 0.5），消除市场整体漂移。
- 模型：L2 逻辑回归（λ=1.0，牛顿法，30 次迭代），唯一超参，不调。
- 时间外：逐月 walk-forward，训练窗 = 测试月首日前 120 个交易日，且**剔除最后 5 个
  交易日**（标签重叠 embargo）；训练不足 3000 行则跳过该月。
- 组合：每 5 个交易日调仓一次，取分数 Top4 等权，持有 5 日；单边成本 10bp
  （调仓往返 20bp）。基准同口径：旧门槛 Top4、结构门槛 Top4、随机 4 只（200 次）、全样本。
- 预注册判据：模型**净**年化 Sharpe 比结构门槛高 ≥0.2，且净胜率不低于结构门槛。
  两条同时成立才算通过；否则判定「无增量」，生产不改。

用法：
  python scripts/backtest_node2_rank_model.py
"""

from __future__ import annotations

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

from src.got.phase_model import build_panel  # noqa: E402

CACHE = ROOT / "data" / "_replay" / "board_ohlcv_cache.json"
OUT_DIR = ROOT / "data" / "_replay" / "ab"
L2 = 1.0
ITERS = 30
WINDOW = 120
PURGE = 5
TOPN = 4
COST = 0.002  # 一次调仓的往返成本

FEATURES = ("r5", "r10", "r60", "r5_pct", "high20_dist", "high20_pos",
            "above_ma20", "ma20_slope", "chg1", "vol_ratio", "vol20", "udvr", "vpc")
RN = tuple("rn_" + f for f in FEATURES)


def rank_normalize(rows: list[dict]) -> None:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
    for rs in by_day.values():
        for f, rn in zip(FEATURES, RN):
            vals = sorted((r[f], i) for i, r in enumerate(rs) if r[f] is not None)
            n = len(vals)
            if n:
                for k, (_, i) in enumerate(vals):
                    rs[i][rn] = (k + 0.5) / n
            for r in rs:
                if r[f] is None:
                    r[rn] = 0.5


def fit(x: np.ndarray, y: np.ndarray, l2: float = L2, iters: int = ITERS) -> dict:
    mu = x.mean(axis=0)
    sd = x.std(axis=0)
    sd[sd == 0] = 1.0
    xs = (x - mu) / sd
    xb = np.hstack([xs, np.ones((len(xs), 1))])
    beta = np.zeros(xb.shape[1])

    def loglik(b: np.ndarray) -> float:
        z = xb @ b
        return float(np.sum(y * z - np.logaddexp(0.0, z)) - 0.5 * l2 * np.sum(b[:-1] ** 2))

    cur = loglik(beta)
    for _ in range(iters):
        z = xb @ beta
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))
        g = xb.T @ (y - p)
        g[:-1] -= l2 * beta[:-1]
        w = p * (1.0 - p)
        h = (xb * w[:, None]).T @ xb
        h[np.arange(h.shape[0] - 1), np.arange(h.shape[0] - 1)] += l2
        h[-1, -1] += 1e-9
        try:
            step = np.linalg.solve(h + 1e-9 * np.eye(h.shape[0]), g)
        except np.linalg.LinAlgError:
            break
        scale = 1.0
        for _ in range(12):
            cand = beta + scale * step
            val = loglik(cand)
            if val > cur - 1e-12:
                beta, cur = cand, val
                break
            scale *= 0.5
        else:
            break
    return {"coef": beta[:-1], "intercept": float(beta[-1]), "mean": mu, "std": sd}


def score(x: np.ndarray, model: dict) -> np.ndarray:
    z = ((x - model["mean"]) / model["std"]) @ model["coef"] + model["intercept"]
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))


def fm_betas(rows: list[dict]) -> list[np.ndarray]:
    """Fama-MacBeth 逐月横截面 OLS（超额 % 对 rank 特征）的系数序列。"""
    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_month[r["date"][:7]].append(r)
    coefs = []
    for rs in by_month.values():
        if len(rs) < 200:
            continue
        x = np.array([[r[k] for k in RN] + [1.0] for r in rs], dtype=float)
        y = np.array([r["fwd_excess5"] for r in rs], dtype=float)
        beta, *_ = np.linalg.lstsq(x, y, rcond=None)
        coefs.append(beta)
    return coefs


def fit_fm(rows: list[dict]) -> np.ndarray:
    betas = fm_betas(rows)
    return np.mean(np.array(betas), axis=0) if betas else np.zeros(len(RN) + 1)


def core(r: dict) -> bool:
    return (r["above_ma20"] == 1.0 and (r["r5"] or 0) > 0
            and (r["r10"] or 0) > 0 and (r["ma20_slope"] or 0) > 0)


def old_gate(r: dict) -> bool:
    return (core(r) and (r["r5_pct"] or 0) >= 0.80
            and r["high20_dist"] is not None and r["high20_dist"] >= -0.02)


def launch_score(r: dict) -> float:
    dist = r["high20_dist"]
    return (0.5 * (r["r5_pct"] or 0.0)
            + 0.25 * min(1.0, max(0.0, ((r["r10"] or 0.0) + 0.10) / 0.20))
            + 0.25 * min(1.0, max(0.0, ((dist or -1.0) + 0.02) / 0.02)))


def pick(rows: list[dict], n: int, key) -> list[dict]:
    return sorted(rows, key=key, reverse=True)[:n]


def weekly_return(picks: list[dict]) -> tuple[float, int]:
    if not picks:
        return 0.0, 0
    return st.mean([r["fwd5"] for r in picks]), len(picks)


def stats(series: list[float]) -> dict:
    if not series:
        return {"n": 0}
    m = st.mean(series)
    sd = st.stdev(series) if len(series) > 1 else 0.0
    cum = 1.0
    peak = 1.0
    mdd = 0.0
    for x in series:
        cum *= 1.0 + x / 100.0
        peak = max(peak, cum)
        mdd = min(mdd, cum / peak - 1.0)
    ann_factor = 252.0 / 5.0
    return {"n": len(series), "mean": m, "cum": (cum - 1.0) * 100.0,
            "ann": m * ann_factor, "sharpe": (m / sd * math.sqrt(ann_factor)) if sd else 0.0,
            "mdd": mdd * 100.0, "win": 100.0 * sum(1 for x in series if x > 0) / len(series)}


def main() -> int:
    rows = [r for r in build_panel(json.loads(CACHE.read_text(encoding="utf-8")))
            if r["y"] is not None]
    rank_normalize(rows)
    dates = sorted({r["date"] for r in rows})
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
    print(f"panel {len(rows)} / {len(dates)} days", flush=True)

    months = sorted({d[:7] for d in dates})
    test_month = months[4]  # 前 4 个月只做训练（≥ 约 60 个交易日）
    print(f"test months: {months[4:]}", flush=True)

    rng = np.random.default_rng(20261109)
    oos_scores: dict[str, float] = {}
    month_auc: dict[str, list[float]] = defaultdict(list)
    coefs: list[tuple[str, dict]] = []
    for mon in months[4:]:
        train = [r for r in rows if r["date"] < mon + "-01"]
        if train:
            last = train[-1]["date"]
            cut = dates[max(0, dates.index(last) - PURGE + 1)]
            train = [r for r in train if r["date"] < cut]
        window_start = dates[max(0, dates.index(cut) - WINDOW)] if train else None
        train = [r for r in train if r["date"] >= window_start] if window_start else []
        test = [r for r in rows if r["date"][:7] == mon]
        if len(train) < 3000 or len({r["date"] for r in test}) < 5:
            print(f"skip {mon}: train {len(train)}", flush=True)
            continue
        x = np.array([[r[k] for k in RN] for r in train], dtype=float)
        y = np.array([r["y"] for r in train], dtype=float)
        model = fit(x, y)
        fm = fit_fm(train)
        coefs.append((mon, {f: float(c) for f, c in zip(FEATURES, model["coef"])}))
        for d in sorted({r["date"] for r in test}):
            day_rows = [r for r in test if r["date"] == d]
            xd = np.array([[r[k] for k in RN] + [1.0] for r in day_rows], dtype=float)
            s = score(xd[:, :-1], model)
            s_fm = xd @ fm
            for r, v, vf in zip(day_rows, s, s_fm):
                oos_scores[(d, r["code"])] = float(v)
                oos_scores[(d, r["code"], "fm")] = float(vf)
            pos = sum(1 for r in day_rows if r["y"] > 0.5)
            neg = len(day_rows) - pos
            if pos and neg:
                order = sorted(range(len(day_rows)), key=lambda i: s[i])
                ranks = [0.0] * len(order)
                for k, i in enumerate(order):
                    ranks[i] = k + 1
                auc = (sum(ranks[i] for i in range(len(day_rows)) if day_rows[i]["y"] > 0.5)
                       - pos * (pos + 1) / 2) / (pos * neg)
                month_auc[mon].append(auc)

    def score_of(r: dict) -> float:
        return oos_scores.get((r["date"], r["code"]), -1.0)

    def score_fm_of(r: dict) -> float:
        return oos_scores.get((r["date"], r["code"], "fm"), -1e9)

    start = next(d for d in dates if d >= test_month + "-01")
    bt_dates = [d for d in dates[dates.index(start):]
                if any(r["date"] == d and r["fwd5"] is not None for r in by_day[d])]
    bt_dates = bt_dates[::5]

    series = {"model": [], "fm": [], "old": [], "struct": [], "random": [], "universe": [],
              "old_all": [], "struct_all": []}
    marks = {"model": [], "fm": [], "old": [], "struct": [], "random": [], "universe": [],
             "old_all": [], "struct_all": []}
    counts = {"model": [], "fm": [], "old": [], "struct": []}
    regime_days: dict[str, list[str]] = {"bull": [], "bear": []}
    for d in bt_dates:
        day_rows = [r for r in by_day[d] if r["fwd5"] is not None and (d, r["code"]) in oos_scores]
        if len(day_rows) < 20:
            continue
        m, cm = weekly_return(pick(day_rows, TOPN, score_of))
        f, cf = weekly_return(pick(day_rows, TOPN, score_fm_of))
        o, co = weekly_return(pick([r for r in day_rows if old_gate(r)], TOPN, launch_score))
        s, cs = weekly_return(pick([r for r in day_rows if core(r)], TOPN, launch_score))
        u, _ = weekly_return(day_rows)
        draws = [st.mean([day_rows[int(i)]["fwd5"]
                          for i in rng.integers(0, len(day_rows), TOPN)]) for _ in range(200)]
        for key, val in (("model", m), ("fm", f), ("old", o), ("struct", s),
                         ("random", st.mean(draws)), ("universe", u)):
            series[key].append(val - (COST * 100.0 if key != "universe" else 0.0))
            marks[key].append(d)
        # 诊断：门槛本身（全量等权，不 TopN、不计成本），把「选池」与「选4只」拆开
        for key, gate in (("old_all", old_gate), ("struct_all", core)):
            sel = [r["fwd5"] for r in day_rows if gate(r)]
            series[key].append(st.mean(sel) if sel else 0.0)
            marks[key].append(d)
        counts["model"].append(cm); counts["fm"].append(cf)
        counts["old"].append(co); counts["struct"].append(cs)
        market = st.mean([r["r10"] for r in by_day[d] if r["r10"] is not None])
        regime_days["bull" if market > 0 else "bear"].append(d)

    res = {k: stats(v) for k, v in series.items()}
    model_series = series["model"]
    diff = [a - b for a, b in zip(model_series, series["struct"])]
    t = (st.mean(diff) / (st.stdev(diff) / math.sqrt(len(diff)))) if len(diff) > 2 and st.stdev(diff) else 0.0
    dr = [a - b for a, b in zip(model_series, series["random"])]
    t_rand = (st.mean(dr) / (st.stdev(dr) / math.sqrt(len(dr)))) if len(dr) > 2 and st.stdev(dr) else 0.0
    du = [a - b for a, b in zip(model_series, series["universe"])]
    t_uni = (st.mean(du) / (st.stdev(du) / math.sqrt(len(du)))) if len(du) > 2 and st.stdev(du) else 0.0

    lines = ["# Node 2 整体重建：横截面排序模型 vs 手写门槛（预注册）", "",
             f"- 面板 {len(rows)} 行 / {len(dates)} 日；样本外 {bt_dates[0]} ~ {bt_dates[-1]}，"
             f"{len(model_series)} 个周频调仓点；Top{TOPN} 等权，往返成本 {COST*100:.1f}%。",
             f"- 特征 {len(FEATURES)} 维逐日 rank 归一化；L2={L2}、{ITERS} 次牛顿迭代；"
             f"训练窗 {WINDOW} 日 + {PURGE} 日 embargo。", "",
             "## 样本外逐月 AUC（模型分数 vs 超额胜）", "",
             "| 月 | AUC | 交易日 |", "|---|---:|---:|"]
    for mon in sorted(month_auc):
        vals = month_auc[mon]
        lines.append(f"| {mon} | {st.mean(vals):.3f} | {len(vals)} |")
    all_auc = [v for vs in month_auc.values() for v in vs]
    if all_auc:
        lines.append(f"| **均值** | **{st.mean(all_auc):.3f}** | {len(all_auc)} |")

    lines += ["", "## 系数符号（正 = 该特征越高、次日超额胜率越高）", "",
              "| 月 | " + " | ".join(FEATURES) + " |",
              "|---|" + "---:|" * len(FEATURES)]
    for mon, cf in coefs:
        lines.append(f"| {mon} | " + " | ".join(
            f"{'+' if cf[f] > 0 else '−'}{abs(cf[f]):.2f}" for f in FEATURES) + " |")

    lines += ["", "## 组合回测（周频，净成本）", "",
              "| 策略 | 周数 | 周均 | 超额 vs 全样本 | 累计 | 年化 | Sharpe | 最大回撤 | 周胜率 | 平均选入 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    label = {"model": "模型 Top4", "fm": "FM 模型 Top4（规格对照，不参与采纳）", "old": "旧门槛 Top4", "struct": "结构门槛 Top4",
             "random": "随机 4 只", "universe": "全样本（不交易）",
             "old_all": "旧门槛全量等权（诊断）", "struct_all": "结构门槛全量等权（诊断）"}
    for k in ("model", "fm", "old", "struct", "old_all", "struct_all", "random", "universe"):
        r = res[k]
        if not r.get("n"):
            continue
        c = f"{st.mean(counts[k]):.1f}" if k in counts else "—"
        ex = r["mean"] - res["universe"]["mean"] if k != "universe" else 0.0
        lines.append(f"| {label[k]} | {r['n']} | {r['mean']:+.2f}% | {ex:+.2f}pp | {r['cum']:+.1f}% | "
                     f"{r['ann']:+.1f}% | {r['sharpe']:.2f} | {r['mdd']:.1f}% | {r['win']:.0f}% | {c} |")

    all_betas = fm_betas(rows)
    if all_betas:
        pos = [sum(1 for b in all_betas if b[j] > 0) / len(all_betas) for j in range(len(FEATURES))]
        lines += ["", f"## FM 逐月系数符号一致性（{len(all_betas)} 个月）", "",
                  "| 特征 | " + " | ".join(FEATURES) + " |",
                  "|---|" + "---:|" * len(FEATURES),
                  "| 正号月份占比 | " + " | ".join(f"{100*x:.0f}%" for x in pos) + " |"]

    lines += ["", "## 分市场状态（当日全板块 r10 均值 > 0 记为 bull）", "",
              "| 状态 | 周数 | 模型周均 | FM 周均 | 结构周均 |", "|---|---:|---:|---:|---:|"]
    for reg, ds in regime_days.items():
        keep = set(ds)
        mm = [v for d, v in zip(marks["model"], series["model"]) if d in keep]
        ff = [v for d, v in zip(marks["fm"], series["fm"]) if d in keep]
        ss = [v for d, v in zip(marks["struct"], series["struct"]) if d in keep]
        if mm:
            lines.append(f"| {reg} | {len(mm)} | {st.mean(mm):+.2f}% | {st.mean(ff):+.2f}% | "
                         f"{st.mean(ss):+.2f}% |")

    passed_primary = (res["model"]["sharpe"] > res["struct"]["sharpe"] + 0.2
                      and res["model"]["win"] >= res["struct"]["win"])
    passed_econ = (res["model"]["sharpe"] > res["random"]["sharpe"]
                   and res["model"]["win"] >= res["random"]["win"]
                   and res["model"]["mean"] > res["universe"]["mean"])
    lines += ["",
              f"- 模型 − 结构门槛：周均差 {st.mean(diff):+.2f}pp，t = {t:.2f}。",
              f"- 模型 − 随机 4 只：周均差 {st.mean(dr):+.2f}pp，t = {t_rand:.2f}。",
              f"- 模型 − 全样本：周均差 {st.mean(du):+.2f}pp，t = {t_uni:.2f}。", "",
              f"- **预注册判据（对结构门槛）**：Sharpe ≥ 结构 +0.2 且胜率 ≥ 结构 → "
              f"{'通过' if passed_primary else '**不通过**'}。",
              f"- **经济判据（对随机 4 只 / 全样本）**：Sharpe > 随机、胜率 ≥ 随机、周均 > 全样本 → "
              f"{'通过' if passed_econ else '**不通过**'}。", "",
              "- 结论：**模型只能作为旧门槛的止损替代（少亏），不能以 alpha 名义采用。**"
              "任何选板动作都不如不选：全样本周均 "
              f"{res['universe']['mean']:+.2f}%、Sharpe {res['universe']['sharpe']:.2f}，"
              f"高于模型（{res['model']['mean']:+.2f}% / {res['model']['sharpe']:.2f}）"
              f"与随机（{res['random']['mean']:+.2f}% / {res['random']['sharpe']:.2f}）。",]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "node2_rank_model_backtest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
