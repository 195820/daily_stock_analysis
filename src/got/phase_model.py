"""Node 2 相位模型的样本构造与拟合（纯函数，无 I/O、无第三方依赖）。

背景：旧 `can_start` 是四条手写阈值（分位 0.80 / 距高 -8%），邻域审计显示
±20% 扰动即改变输出集合，且 dev/holdout 两段结论不稳定。本模块把「启动」改成
**可拟合的转移概率**：P(次日开盘买入、T+5 收盘的超额收益 > 0)。

纪律（与 `docs/AGENTS.md` 影子治理四问一致）：

- 特征只用 t 日收盘及以前（`build_panel` 接收的 `bars` 已按 as-of 截断亦可，
  本模块内部仍按索引截断，双保险）。
- 标签用 t+1 开盘 → t+5 收盘，与 `scripts/audit_node2_launch_forward_returns.py`
  口径一致；`entry_top` 是「买在 10 日高 2% 内」的事后标签，只用于评估，不参与拟合。
- 时间外分割：按自然月展开（train 全历史 → 次月 test），不随机打乱。
- 拟合用 numpy 牛顿法 + L2；不引入 sklearn/scipy（运行环境两者都没有）。
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

FEATURES = (
    "r5",
    "r10",
    "r5_pct",
    "high20_dist",
    "high20_pos",
    "above_ma20",
    "chg1",
    "vol_ratio",
    "vol20",
    "r60",
)

# 量价特征（排序模型专用；不进入 FEATURES，避免改变已归档的门槛模型口径）
RANK_FEATURES = FEATURES + ("udvr", "vpc")

MIN_LOOKBACK = 61  # r60 需要 61 根；不足的日期整段跳过，避免每行口径不一致


def _mean(vals: Sequence[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _stdev(vals: Sequence[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def _row_features(closes: Sequence[float], highs: Sequence[float],
                  vols: Sequence[float], i: int) -> Optional[dict]:
    """t=i 收盘时的特征；任一窗口不足返回 None（不补 0）。"""
    if i < MIN_LOOKBACK:
        return None
    close = closes[i]
    if close <= 0:
        return None
    r5 = close / closes[i - 5] - 1.0 if closes[i - 5] > 0 else None
    r10 = close / closes[i - 10] - 1.0 if closes[i - 10] > 0 else None
    r60 = close / closes[i - 60] - 1.0 if closes[i - 60] > 0 else None
    if r5 is None or r10 is None or r60 is None:
        return None
    high20 = max(highs[i - 19:i + 1])
    prev_ma20 = _mean(closes[i - 24:i - 4]) if i >= 24 else None
    ma20 = _mean(closes[i - 19:i + 1])
    close20 = max(closes[i - 19:i + 1])
    ma20 = _mean(closes[i - 19:i + 1])
    v5 = _mean(vols[i - 4:i + 1])
    v20 = _mean(vols[i - 19:i + 1])
    rets = [closes[j] / closes[j - 1] - 1.0
            for j in range(i - 19, i + 1) if closes[j - 1] > 0]
    # 量价：10 日上涨日成交量 / 下跌日成交量
    up_v = down_v = 0.0
    for j in range(i - 9, i + 1):
        if closes[j] > closes[j - 1]:
            up_v += vols[j]
        elif closes[j] < closes[j - 1]:
            down_v += vols[j]
    udvr = (up_v / down_v) if down_v > 0 else (2.0 if up_v > 0 else 1.0)
    # 量价：20 日「收益率 vs 成交量变化率」相关
    pairs = []
    for j in range(i - 19, i + 1):
        if closes[j - 1] > 0 and vols[j - 1] > 0:
            pairs.append((closes[j] / closes[j - 1] - 1.0, vols[j] / vols[j - 1] - 1.0))
    vpc = 0.0
    if len(pairs) >= 5:
        mr = _mean([x[0] for x in pairs]); mv = _mean([x[1] for x in pairs])
        sr = _stdev([x[0] for x in pairs]); sv = _stdev([x[1] for x in pairs])
        if sr > 0 and sv > 0:
            vpc = sum((x[0] - mr) * (x[1] - mv) for x in pairs) / ((len(pairs) - 1) * sr * sv)
    return {
        "r5": r5,
        "r10": r10,
        "high20_dist": (close / high20 - 1.0) if high20 > 0 else None,
        "high20_pos": (close / close20) if close20 > 0 else None,
        "above_ma20": 1.0 if close > ma20 else 0.0,
        "ma20_slope": (ma20 / prev_ma20 - 1.0) if prev_ma20 else None,
        "chg1": (close / closes[i - 1] - 1.0) if closes[i - 1] > 0 else 0.0,
        "vol_ratio": (v5 / v20) if v20 > 0 else 1.0,
        "vol20": _stdev(rets),
        "r60": r60,
        "udvr": udvr,
        "vpc": vpc,
        "_r5_raw": r5,
    }


def build_panel(cache: dict, min_forward: int = 5, horizon: int = 5) -> list[dict]:
    """从 OHLCV 缓存构造面板。`cache[code] = {dates, open, high, low, close, volume}`。

    每个元素：`{date, code, <FEATURES>, r5_pct, fwd5, fwd_excess5, y, entry_top}`。
    `fwd_*` 只在 t+1..t+`horizon` 都存在时给出；缺省行不进入拟合集。
    `entry_top` 的观察窗固定 10 根（与 horizon 无关）：买在 t+1 开盘价距其后
    10 日最高收盘 2% 内即视为追顶。
    """
    codes = sorted(cache)
    # 日期轴：取全缓存并集并排序（板块可能上市日不同）
    all_dates = sorted({d for c in codes for d in (cache[c].get("dates") or [])})
    idx_by_code = {c: {d: i for i, d in enumerate(cache[c].get("dates") or [])} for c in codes}

    rows: list[dict] = []
    for t, day in enumerate(all_dates):
        raw = []
        for c in codes:
            bars = cache[c]
            i = idx_by_code[c].get(day)
            if i is None:
                continue
            closes = [float(x) for x in bars["close"]]
            highs = [float(x) for x in (bars.get("high") or closes)]
            vols = [float(x) for x in (bars.get("volume") or [1.0] * len(closes))]
            if len(highs) < len(closes):
                highs = highs + closes[len(highs):]
            if len(vols) < len(closes):
                vols = vols + [1.0] * (len(closes) - len(vols))
            f = _row_features(closes, highs, vols, i)
            if f is None:
                continue
            raw.append((c, f))
        if len(raw) < 50:
            continue
        ordered = sorted(x[1]["_r5_raw"] for x in raw)
        n = len(ordered)
        for c, f in raw:
            pct = bisect.bisect_right(ordered, f["_r5_raw"]) / n
            bars = cache[c]
            i = idx_by_code[c][day]
            fwd = None
            entry_top = None
            if i + horizon < len(bars["dates"]):
                try:
                    entry = float(bars["open"][i + 1])
                    exit_ = float(bars["close"][i + horizon])
                    if entry > 0:
                        fwd = (exit_ / entry - 1.0) * 100.0
                        look = [float(x) for x in bars["close"][i + 1:i + 1 + 10]]
                        entry_top = 1.0 if look and entry >= 0.98 * max(look) else 0.0
                except (KeyError, TypeError, ValueError, IndexError):
                    fwd = None
            row = {"date": day, "code": c, "r5_pct": pct}
            for k in FEATURES:
                if k != "r5_pct":
                    row[k] = f.get(k)
            row["ma20_slope"] = f.get("ma20_slope")
            row["udvr"] = f.get("udvr")
            row["vpc"] = f.get("vpc")
            row["fwd5"] = fwd
            row["entry_top"] = entry_top
            rows.append(row)

    # 逐日横截面超额（只用当日已可算样本；不做全样本均值）
    by_day: dict[str, list[float]] = {}
    for r in rows:
        if r["fwd5"] is not None:
            by_day.setdefault(r["date"], []).append(r["fwd5"])
    day_mean = {d: _mean(v) for d, v in by_day.items()}
    for r in rows:
        if r["fwd5"] is None:
            r["fwd_excess5"] = None
            r["y"] = None
        else:
            r["fwd_excess5"] = r["fwd5"] - day_mean[r["date"]]
            r["y"] = 1.0 if r["fwd_excess5"] > 0 else 0.0
    return rows


# ---------------------------------------------------------------------------
# 逻辑回归（numpy 牛顿法 + L2）
# ---------------------------------------------------------------------------

def _standardize(matrix: Sequence[Sequence[float]]):
    n = len(matrix)
    m = len(matrix[0])
    means = [sum(row[j] for row in matrix) / n for j in range(m)]
    stds = []
    for j in range(m):
        var = sum((row[j] - means[j]) ** 2 for row in matrix) / max(n - 1, 1)
        stds.append(math.sqrt(var) or 1.0)
    return means, stds


def row_vector(row: dict, features: tuple = FEATURES) -> list[float]:
    """按给定特征顺序取列；None 用训练均值占位的职责留给 predict_proba。"""
    return [row.get(name) for name in features]


def fit_logistic(x: Sequence[Sequence[float]], y: Sequence[float],
                 l2: float = 1.0, iters: int = 30, features: tuple = FEATURES) -> dict:
    """返回 `{features, coef, intercept, mean, std}`；`x` 必须按 FEATURES 顺序。"""
    mean, std = _standardize(x)
    xs = [[(row[j] - mean[j]) / std[j] for j in range(len(row))] for row in x]
    n, m = len(xs), len(xs[0])
    beta = [0.0] * (m + 1)  # 最后一维是截距

    def loglik(b):
        z = [sum(b[j] * r[j] for j in range(m)) + b[m] for r in xs]
        ll = 0.0
        for zi, yi in zip(z, y):
            # log(1+exp(-z)) 的稳定写法
            ll += yi * zi - (zi + math.log1p(math.exp(-zi)) if zi >= 0
                             else math.log1p(math.exp(zi)))
        return ll - 0.5 * l2 * sum(b[j] ** 2 for j in range(m))

    cur = loglik(beta)
    for _ in range(iters):
        # 梯度
        g = [0.0] * (m + 1)
        h = [[0.0] * (m + 1) for _ in range(m + 1)]
        for r, yi in zip(xs, y):
            z = sum(beta[j] * r[j] for j in range(m)) + beta[m]
            p = 1.0 / (1.0 + math.exp(-max(min(z, 30.0), -30.0)))
            w = p * (1.0 - p)
            for j in range(m):
                g[j] += (yi - p) * r[j]
                for k in range(j, m):
                    h[j][k] += w * r[j] * r[k]
            g[m] += (yi - p)
            h[m][m] += w
        for j in range(m):
            g[j] -= l2 * beta[j]
            h[j][j] += l2 + 1e-9
        for j in range(m):
            for k in range(j + 1, m):
                h[k][j] = h[j][k]
        h[m][m] += 1e-9
        step = _solve(h, g)
        if step is None:
            break
        # 回溯
        scale = 1.0
        for _ in range(12):
            cand = [beta[j] + scale * step[j] for j in range(m + 1)]
            val = loglik(cand)
            if val > cur - 1e-12:
                beta, cur = cand, val
                break
            scale *= 0.5
        else:
            break
    return {"features": list(features), "coef": beta[:m], "intercept": beta[m],
            "mean": mean, "std": std}


def _solve(a: list[list[float]], b: list[float]) -> Optional[list[float]]:
    """高斯消元（小矩阵，m+1 ≤ 12）。"""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        for r in range(n):
            if r == col:
                continue
            f = m[r][col] / pv
            if f:
                for k in range(col, n + 1):
                    m[r][k] -= f * m[col][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def predict_proba(model: dict, row: dict) -> float:
    names = tuple(model.get("features") or ())
    if names not in (FEATURES, RANK_FEATURES):
        raise ValueError(f"unknown feature set: {model.get('features')}")
    z = model["intercept"]
    for j, name in enumerate(names):
        v = row.get(name)
        if v is None:
            v = model["mean"][j]
        z += model["coef"][j] * ((v - model["mean"][j]) / model["std"][j])
    z = max(min(z, 30.0), -30.0)
    return 1.0 / (1.0 + math.exp(-z))


def auc(pairs: Iterable[tuple[float, float]]) -> Optional[float]:
    """秩和 AUC；pairs = (score, label)。"""
    items = sorted(pairs, key=lambda x: x[0])
    n_pos = sum(1 for _, y in items if y > 0.5)
    n_neg = len(items) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    rank_sum, i = 0.0, 0
    while i < len(items):
        j = i
        while j + 1 < len(items) and items[j + 1][0] == items[i][0]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            if items[k][1] > 0.5:
                rank_sum += avg_rank
        i = j + 1
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
