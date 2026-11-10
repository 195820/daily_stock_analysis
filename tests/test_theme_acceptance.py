# -*- coding: utf-8 -*-
"""P4 验收判定器单测：样本门槛优先、双主指标方向、回撤约束、日期对齐。

判据来源：实施稿 §2.1（双主指标 + 最小样本）与 §9（停止条件）。
"""

from __future__ import annotations

import unittest

from src.got.theme_acceptance import (
    FAIL,
    INSUFFICIENT,
    UNRESOLVED,
    evaluate_arms,
)


def _dates(n: int) -> list[str]:
    # 2026-01 起连续自然日（判定器只按日期键对齐，不校验交易日）
    return [f"2026-{1 + (i // 28):02d}-{1 + (i % 28):02d}" for i in range(n)]


def arm(
    *,
    days: int = 70,
    closed: int = 100,
    win_rate: float = 0.5,
    daily_ret: float = 0.001,
    mdd: float = 0.10,
) -> dict:
    dates = _dates(days)
    equity = 1_000_000.0
    curve = []
    for d in dates:
        equity *= 1 + daily_ret
        curve.append([d, round(equity, 2), 0])
    trades = []
    for i in range(closed):
        trades.append({
            "exit_date": dates[i % days],
            "net_return": 0.01 if i < round(closed * win_rate) else -0.01,
        })
    return {
        "meta": {"window": [dates[0], dates[-1]]},
        "metrics": {
            "sample_days": days,
            "max_drawdown": mdd,
            "closed_trades": {"count": closed, "win_rate": win_rate},
        },
        "equity_curve": curve,
        "closed_trades": trades,
    }


class TestAcceptance(unittest.TestCase):
    def test_insufficient_sample_dominates(self):
        report = evaluate_arms(
            arm(days=20, closed=5), arm(days=20, closed=5, daily_ret=0.002)
        )
        self.assertEqual(report["status"], INSUFFICIENT)

    def test_pass_pending_attestation_when_all_mechanical_pass(self):
        report = evaluate_arms(arm(), arm(daily_ret=0.0015, win_rate=0.6))
        self.assertEqual(report["status"], "pass_pending_attestation")
        names = {c["name"]: c for c in report["criteria"]}
        self.assertEqual(names["net_return_diff"]["status"], "pass")
        self.assertEqual(names["win_rate_diff"]["status"], "pass")
        self.assertEqual(names["max_drawdown"]["status"], "pass")
        self.assertEqual(names["risk_gate_no_bypass"]["status"], "not_verifiable")

    def test_fail_when_variant_worse(self):
        report = evaluate_arms(arm(), arm(daily_ret=0.0002, win_rate=0.4))
        self.assertEqual(report["status"], FAIL)

    def test_fail_when_drawdown_worsens_over_one_point(self):
        report = evaluate_arms(arm(mdd=0.10), arm(mdd=0.125, daily_ret=0.002))
        names = {c["name"]: c for c in report["criteria"]}
        self.assertEqual(names["max_drawdown"]["status"], "fail")
        self.assertEqual(report["status"], FAIL)

    def test_unresolved_on_date_misalignment(self):
        base = arm()
        variant = arm(daily_ret=0.002)
        variant["equity_curve"] = variant["equity_curve"][:-1]
        report = evaluate_arms(base, variant)
        self.assertEqual(report["status"], UNRESOLVED)
        names = {c["name"]: c for c in report["criteria"]}
        self.assertEqual(names["equity_alignment"]["status"], UNRESOLVED)

    def test_concentration_is_disclosure_not_pass(self):
        report = evaluate_arms(arm(), arm(daily_ret=0.0015, win_rate=0.6))
        names = {c["name"]: c for c in report["criteria"]}
        self.assertIn("single_day_concentration", names)
        self.assertEqual(names["single_day_concentration"]["status"], "disclosure")
        self.assertIsNotNone(names["single_day_concentration"]["value"])
        # 披露项不参与聚合：机械判据全过时仍是 pass_pending_attestation
        self.assertEqual(report["status"], "pass_pending_attestation")

    def test_win_rate_diff_discloses_dropped_trades(self):
        base = arm()
        base["closed_trades"].append({"exit_date": "", "net_return": 0.01})
        report = evaluate_arms(base, arm(daily_ret=0.0015, win_rate=0.6))
        names = {c["name"]: c for c in report["criteria"]}
        self.assertIn("baseline=1", names["win_rate_diff"]["detail"])

    def test_schema_mismatch_is_unresolved(self):
        variant = arm(daily_ret=0.0015)
        variant["meta"]["cost_model"] = {"commission_rate": 0.001}
        report = evaluate_arms(arm(), variant)
        self.assertEqual(report["status"], UNRESOLVED)
        names = {c["name"]: c for c in report["criteria"]}
        self.assertEqual(names["payload_schema"]["status"], "unresolved")
        self.assertIn("cost_model", names["payload_schema"]["value"])


if __name__ == "__main__":
    unittest.main()
