#!/usr/bin/env python
"""P4 验收判定 CLI：读两份 arm payload → 机械判据报告。

用法
----
    python scripts/evaluate_theme_arms.py --self-check
    python scripts/evaluate_theme_arms.py --baseline A.json --variant B.json --out report

`--self-check` 用基线自己和自己比：只验证判定器链路（预期 `insufficient_sample`，
因为当前基线闭合往返 <100），**不构成任何收益结论**。变体缺失时退出码 2，不写假报告。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.got.theme_acceptance import evaluate_arms, render_md  # noqa: E402

DEFAULT_BASELINE = ROOT / "data" / "got_daily" / "backtest_portfolio_baseline_20260801_20261103.json"


REQUIRED_KEYS = ("metrics", "equity_curve", "closed_trades", "meta")


def _load(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"arm payload 读取失败：{path}（{type(exc).__name__}: {exc}）")
        return None
    if not isinstance(payload, dict) or any(k not in payload for k in REQUIRED_KEYS):
        print(f"arm payload 结构不完整：{path}（需 {REQUIRED_KEYS}）")
        return None
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="P4 验收判定")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    ap.add_argument("--variant", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()

    baseline_path = Path(args.baseline)
    if not baseline_path.exists():
        print(f"基线 payload 缺失：{baseline_path}")
        return 2
    variant_path = baseline_path if args.self_check else Path(args.variant) if args.variant else None
    if variant_path is None or not variant_path.exists():
        print("变体 arm 缺失：P4 未就绪（arms 3/4 需 P2-b-3/P3-b 产物）。不生成报告。")
        return 2

    baseline_payload = _load(baseline_path)
    variant_payload = _load(variant_path)
    if baseline_payload is None or variant_payload is None:
        print("拒绝评估：arm payload 缺失/损坏；不生成报告。")
        return 2
    report = evaluate_arms(baseline_payload, variant_payload)
    report["baseline_path"] = str(baseline_path)
    report["variant_path"] = str(variant_path)
    report["self_check"] = bool(args.self_check)
    if args.self_check:
        report["note"] = (
            "self-check：同一份基线自比，仅验证判定器链路；"
            "零差异不是策略结论，当前样本不足 → insufficient_sample。"
        )

    out = Path(args.out) if args.out else (
        ROOT / "data" / "got_daily" / f"theme_acceptance_{report['status']}.json"
    )
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = out.with_suffix(".md")
    md.write_text(render_md(report), encoding="utf-8")

    print(f"status={report['status']}（self_check={report['self_check']}）")
    print(f"compared_fields={report.get('compared_fields')}")
    for c in report["criteria"]:
        print(f"  {c['name']}: {c['status']} {c.get('value')}")
    print(f"JSON: {out}\nMD:   {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
