#!/usr/bin/env python3
"""#14 ④ regime jump penalty λ 标定回放（backlog §7 #14 拍板 2026-10-15）。

从归档 unified.json 的 macro_overlay（regime_score=2 位小数）+ raw_regime_score
（2026-10-15 起新字段）提取每日 raw 分数序列，对 λ 网格离线重放 jump 模型：

    s_t = argmin_s [ |raw_t − θ_s| + λ·1{s_t ≠ s_{t-1}} ]

输出两窗口对照表：
- 振荡窗 0910–1009（拍板指定的标定窗）：现行状态机 19 日才确认 bear（1015），
  switch 数与方向一致天数为 λ 选值依据（振荡窗 switch>20 则加密网格）。
- 全窗（归档可得 raw 序列全部日）。

σ 基准：归档 raw 序列 daily-diff std（实测 ≈0.49）——λ 网格 {0.3,0.5,1.0,2.0}×σ
拍板原案 + {0.15,0.25} 细化档（0.3σ≈0.15 / 0.5σ≈0.25）。

使用：
    python scripts/calibrate_regime_jump_penalty.py
    python scripts/calibrate_regime_jump_penalty.py --window 20260910:20261009
"""

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

THETA = {"bull": 0.3, "transition": 0.0, "bear": -0.3}
STATES = ("bull", "transition", "bear")


def load_raw_series() -> list[tuple[str, float]]:
    """从归档提取 (YYYYMMDD, raw regime_score) 升序序列。

    优先 raw_regime_score 字段（新归档），回退 macro_overlay 文本解析
    （2 位小数精度，标定够用）。损坏归档（20260917 等）try/except 跳过。
    """
    rows: list[tuple[str, float]] = []
    root = PROJECT_ROOT / "data" / "got_daily"
    for d in sorted(root.iterdir()) if root.exists() else []:
        day = d.name
        if not re.fullmatch(r"20\d{6}", day):
            # 排除 _backup/_pre_fix/_cache 等非日期目录
            continue
        p = d / "unified.json"
        if not p.exists():
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        mr = data.get("market_regime") or {}
        raw = mr.get("raw_regime_score")
        if raw is None:
            m = re.search(r"regime_score=(-?\d+\.?\d*)", mr.get("macro_overlay") or "")
            if not m:
                continue
            raw = float(m.group(1))
        rows.append((day, float(raw)))
    return rows


def threshold_state(raw: float) -> str:
    """现行 raw 阈值态（与 _compute_market_regime 三段阈值一致）。"""
    if raw > 0.3:
        return "bull"
    if raw < -0.3:
        return "bear"
    return "transition"


def replay_jump(series: list[tuple[str, float]], lam: float) -> list[str]:
    """jump 模型离线重放，返回逐日 s_t 序列。"""
    states: list[str] = []
    prev: str | None = None
    for _, raw in series:
        if prev is None:
            prev = threshold_state(raw)  # 冷启动 s_0 = raw 阈值态
            states.append(prev)
            continue
        costs = {s: abs(raw - THETA[s]) for s in STATES}
        best, best_cost = prev, costs.get(prev, abs(raw))
        for s in STATES:
            if s == prev:
                continue
            total = costs[s] + lam
            if total < best_cost:
                best, best_cost = s, total
        states.append(best)
        prev = best
    return states


def count_switches(states: list[str]) -> int:
    return sum(1 for i in range(1, len(states)) if states[i] != states[i - 1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="20260910:20261009",
                    help="标定窗 YYYYMMDD:YYYYMMDD（默认拍板窗 0910–1009）")
    ap.add_argument("--lambdas", default="",
                    help="逗号分隔 λ 列表；缺省 {0.15,0.25,0.3,0.5,1.0,2.0}")
    args = ap.parse_args()

    series_all = load_raw_series()
    if len(series_all) < 5:
        print(f"归档 raw 序列不足（{len(series_all)} 日），无法标定")
        return 1

    lo, hi = args.window.split(":")
    win = [(d, r) for d, r in series_all if lo <= d <= hi]
    vals = [r for _, r in series_all]
    diffs = [vals[i] - vals[i - 1] for i in range(1, len(vals))]
    sigma_diff = statistics.stdev(diffs) if len(diffs) > 1 else 0.0

    lambdas = (
        [float(x) for x in args.lambdas.split(",") if x.strip()]
        if args.lambdas
        else [0.15, 0.25, 0.3, 0.5, 1.0, 2.0]
    )

    print(f"raw 序列: 全窗 {len(series_all)} 日（{series_all[0][0]}–{series_all[-1][0]}）"
          f"，标定窗 {len(win)} 日（{lo}–{hi}）")
    print(f"σ（daily-diff std）= {sigma_diff:.4f}  |  level std = "
          f"{statistics.stdev(vals):.4f}")
    print()

    # 现行阈值基线（每日 raw 直接过阈值 = 无记忆基线，agree 判据的对照态）
    base_win = [threshold_state(r) for _, r in win]
    base_all = [threshold_state(r) for _, r in series_all]
    print(f"{'λ':>6} | {'窗内switch':>9} | {'窗内方向一致':>11} | {'全窗switch':>9} | 备注")
    print("-" * 66)
    # 方向一致 = jump 态与当日 raw 阈值态一致（拍板判据：无记忆方向一致天数）
    print(f"{'无记忆':>6} | {count_switches(base_win):>9} | {len(win):>9}/{len(win)} | "
          f"{count_switches(base_all):>9} | 逐日阈值基线（λ=0 极限，agree 判据对照态）")
    for lam in lambdas:
        st_win = replay_jump(win, lam)
        st_all = replay_jump(series_all, lam)
        agree = sum(1 for a, b in zip(st_win, base_win) if a == b)
        sw_win, sw_all = count_switches(st_win), count_switches(st_all)
        note = ""
        if sw_win == 0:
            note = "⚠ λ 过大钉死初态"
        elif sw_win > 20:
            note = "⚠ 振荡未抑制，需加密网格"
        print(f"{lam:>6} | {sw_win:>9} | {agree:>9}/{len(win)} | {sw_all:>9} | {note}")

    print()
    print("现行确认状态机对照（归档 regime 输出）: 见 verification.md #14 ④ 判据节；")
    print("锚点（非真标定）：现行 1015 切/1 次；λ=0.5→0928 切/2 次；λ=2.0→0 次永不切换。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
