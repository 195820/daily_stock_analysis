# -*- coding: utf-8 -*-
"""批次 F1（竞品经验补强计划 2026-10-06）：标准化回测骨架 CLI。

从 `.review_tmp/bt_*` 一次性脚本提炼可复用骨架，把三项过拟合检查 + 强制 run card 收拢到
一处（口径来自 `src/got/backtest_discipline.py`，本脚本只做输入装载 + 编排 + 落盘）：
1. 基准对比（沪深300 / 对应板块指数）——逐期超额
2. moving-block bootstrap 收益均值置信区间
3. walk-forward 前推验证切窗

**纪律（F2）**：缺 run card（未给 --window / --point-in-time）直接拒绝出结论（exit 2），
承 Vibe-Trading "缺卡片不回测"。缺失值不伪造填补（策略写进卡片，消费方自证）。

用法：
    python scripts/backtest_standard.py --returns-json rets.json [--bench-json bench.json] \
        --window 2026-01~2026-09 --benchmark 沪深300 --point-in-time close_after_1500 \
        [--out out_dir]

--returns-json 形态：{"returns": [1.2, -0.5, ...], "meta": {...可选...}}（单位 %）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.got.backtest_discipline import (  # noqa: E402
    benchmark_excess,
    build_run_card,
    moving_block_bootstrap_ci,
    walk_forward_splits,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="标准化回测骨架（run card + 过拟合三检）")
    ap.add_argument("--returns-json", required=True, help="收益序列 JSON：{\"returns\": [...]}")
    ap.add_argument("--bench-json", default=None, help="基准收益序列 JSON（可选，缺则跳基准对比）")
    ap.add_argument("--window", default="", help="样本窗口（run card 必填，缺则拒绝）")
    ap.add_argument("--benchmark", default="", help="基准名（run card 字段）")
    ap.add_argument("--adjust-mode", default="qfq")
    ap.add_argument("--point-in-time", default="", help="取数时点口径（run card 必填，缺则拒绝）")
    ap.add_argument("--block-len", type=int, default=5)
    ap.add_argument("--train", type=int, default=60)
    ap.add_argument("--test", type=int, default=20)
    ap.add_argument("--out", default=".", help="run_card.json 输出目录")
    args = ap.parse_args()

    # ── F2 纪律：缺 run card 关键字段不回测、不出结论 ──
    card = build_run_card(
        sample_window=args.window, benchmark=args.benchmark or "n/a",
        adjust_mode=args.adjust_mode, point_in_time=args.point_in_time or "unknown",
    )
    if not card["_valid"]:
        print("[拒绝] 缺有效 run card（--window 与 --point-in-time 必填且 point_in_time≠unknown），"
              "拒绝出结论（F2 纪律：缺卡片不回测）")
        return 2

    data = json.loads(Path(args.returns_json).read_text(encoding="utf-8"))
    returns = [r for r in data.get("returns", []) if r is not None]
    if len(returns) < 2:
        print("[拒绝] 样本不足（<2），无法出结论")
        return 2

    result = {"run_card": card, "n": len(returns)}

    # 1. 基准对比
    if args.bench_json:
        bench = [b for b in json.loads(Path(args.bench_json).read_text(encoding="utf-8")).get("returns", [])
                 if b is not None]
        excess = benchmark_excess(returns, bench)
        result["benchmark_excess_mean"] = round(sum(excess) / len(excess), 4) if excess else None
        result["excess_n"] = len(excess)

    # 2. block bootstrap 置信区间
    lo, hi = moving_block_bootstrap_ci(returns, block_len=args.block_len)
    result["mean_return"] = round(sum(returns) / len(returns), 4)
    result["bootstrap_ci95"] = [lo, hi]
    result["ci_excludes_zero"] = not (lo <= 0 <= hi)

    # 3. walk-forward 切窗（供分段重算，非结论本身）
    result["walk_forward_windows"] = len(
        list(walk_forward_splits(len(returns), train=args.train, test=args.test))
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run_card.json").write_text(
        json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "backtest_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n[OK] run card + 结果已落 {out_dir}/run_card.json, backtest_result.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
