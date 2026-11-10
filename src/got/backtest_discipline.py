# -*- coding: utf-8 -*-
"""批次 F（竞品经验补强计划 2026-10-06）：回测与校准纪律——可复用骨架 + run card。

GOT 回测长期是 `.review_tmp` 一次性脚本、无口径卡片，曾发生「多版本逻辑混池统计导致
结论失真」事故（memory 有据）。本模块把 Vibe-Trading 的 run card / 过拟合检查纪律沉淀为
**纯函数 + 口径卡片**，供 `scripts/backtest_standard.py` 及历史 `.review_tmp/bt_*` 脚本复用：

- F2 `build_run_card`：每份回测强制附数据口径（复权方式/缺失值处理——**缺失值禁止伪造
  填补**）、样本窗口、基准、评分公式版本（F3）、生成用 git commit、**point_in_time 声明**
  （承接 a-stock-data 防未来函数：取数时点口径、盘中价是否被当收盘归因——约束批次 D/E2）。
- F1 三项过拟合检查原语：`benchmark_excess`（相对基准超额）、`moving_block_bootstrap_ci`
  （收益均值置信区间，block 保留自相关）、`walk_forward_splits`（按训练/验证窗切）。

纯函数、无 IO/网络，可离线单测。口径卡片与版本戳是「防结论漂移」的硬约束。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np


def build_run_card(
    *,
    sample_window: str,
    benchmark: str,
    adjust_mode: str = "qfq",
    missing_value_policy: str = "no_fill",
    point_in_time: str = "unknown",
    git_commit: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """生成回测口径卡片。缺失值策略默认 no_fill（Vibe-Trading 纪律：禁止伪造填补）。

    point_in_time：取数时点口径声明，如 "intraday_1330"/"close_after_1500"/"replay"；
    留空/unknown 会被自检标红（缺卡片不回测、结论不入档）。
    """
    if git_commit is None:
        try:
            from src.got.archive import _get_git_commit
            git_commit = _get_git_commit()
        except Exception:  # noqa: BLE001
            git_commit = "unknown"
    from src.got.catalyst_config import SCORING_FORMULA_VERSION
    card = {
        "sample_window": sample_window,
        "benchmark": benchmark,
        "adjust_mode": adjust_mode,          # qfq/hfq/none —— 跨源比较须同口径
        "missing_value_policy": missing_value_policy,  # no_fill / drop / ...
        "point_in_time": point_in_time,      # 防未来函数：取数时点口径
        "scoring_formula_version": SCORING_FORMULA_VERSION,
        "git_commit": git_commit,
    }
    if extra:
        card.update(extra)
    card["_valid"] = bool(point_in_time and point_in_time != "unknown"
                          and missing_value_policy != "fill")
    return card


def benchmark_excess(returns: Sequence[float], bench: Sequence[float]) -> List[float]:
    """逐期超额收益（标的收益 - 基准收益），截断到较短长度。"""
    n = min(len(returns), len(bench))
    return [float(returns[i] - bench[i]) for i in range(n)]


def moving_block_bootstrap_ci(
    values: Sequence[float],
    *,
    n_bootstrap: int = 1000,
    ci: float = 0.95,
    block_len: int = 5,
    seed: int = 42,
) -> Tuple[float, float]:
    """moving-block bootstrap 估计均值的置信区间（block 保留序列自相关，防 IID 高估精度）。

    样本不足一个 block 时退化为逐点 bootstrap。返回 (下界, 上界)。
    """
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    if arr.size == 0:
        return (0.0, 0.0)
    rng = np.random.default_rng(seed)
    block_len = max(1, min(block_len, arr.size))
    n_blocks = int(np.ceil(arr.size / block_len))
    means: List[float] = []
    for _ in range(n_bootstrap):
        starts = rng.integers(0, max(1, arr.size - block_len + 1), size=n_blocks)
        sample = np.concatenate([arr[s:s + block_len] for s in starts])[: arr.size]
        means.append(float(sample.mean()))
    lo_pct = (1 - ci) / 2 * 100
    hi_pct = (1 + ci) / 2 * 100
    lo, hi = np.percentile(means, [lo_pct, hi_pct])
    return (round(float(lo), 4), round(float(hi), 4))


def walk_forward_splits(
    n: int, *, train: int, test: int, step: Optional[int] = None,
) -> Iterable[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """滚动前推验证切窗：yield ((tr_start, tr_end), (te_start, te_end))，索引左闭右开。

    参数不合法（train/test<=0 或 n 不足一窗）→ 不产出（调用方据此跳过，不虚构切分）。
    """
    if train <= 0 or test <= 0 or n < train + test:
        return []
    step = step or test
    out: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
    start = 0
    while start + train + test <= n:
        tr = (start, start + train)
        te = (start + train, start + train + test)
        out.append((tr, te))
        start += step
    return out


def date_cluster_bootstrap_ci(
    values_by_date: "Mapping[str, Sequence[float]]",
    *,
    n_bootstrap: int = 1000,
    ci: float = 0.95,
    seed: int = 42,
) -> Tuple[float, float]:
    """日期簇 bootstrap：以「交易日」为重采样单位，估计均值置信区间。

    实施稿 §2.1 要求「日期簇 bootstrap 95% CI 下界 >0」；按行 IID 重采样会低估
    同日相关性（同一天的多笔交易共享市场 beta），因此簇单位必须是日期。
    空输入返回 (0.0, 0.0)；日期按字典序排序后固定采样顺序，配合固定 seed 可复现。
    """
    keys = sorted(values_by_date)
    if not keys:
        return (0.0, 0.0)
    pools = [list(values_by_date[k]) for k in keys]
    rng = np.random.default_rng(seed)
    n_dates = len(keys)
    means: List[float] = []
    for _ in range(n_bootstrap):
        picks = rng.integers(0, n_dates, size=n_dates)
        sample: List[float] = []
        for idx in picks:
            sample.extend(pools[int(idx)])
        if sample:
            means.append(float(np.mean(sample)))
    if not means:
        return (0.0, 0.0)
    lo_pct = (1 - ci) / 2 * 100
    hi_pct = (1 + ci) / 2 * 100
    lo, hi = np.percentile(means, [lo_pct, hi_pct])
    return (round(float(lo), 4), round(float(hi), 4))
