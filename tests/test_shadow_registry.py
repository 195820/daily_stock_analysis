# -*- coding: utf-8 -*-
"""影子模式登记表的结构与覆盖校验。

治理口径见 ``docs/AGENTS.md``「影子模式治理」：
- 能回测/可回放的 gate 不得新开 shadow；存量 legacy 必须写 waiver + 到期日。
- 纯观测、冻结、死开关不按影子天数计时。
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date

from src.got.shadow_registry import (
    SHADOW_REGISTRY,
    ShadowRecord,
    discover_shadow_keys,
    expired_gates,
    validate_registry,
)


class TestShadowRegistry(unittest.TestCase):
    def test_registry_schema_valid(self):
        errors = validate_registry()
        self.assertEqual(errors, [], "\n".join(errors))

    def test_no_unregistered_shadow_keys(self):
        unknown = discover_shadow_keys() - set(SHADOW_REGISTRY)
        self.assertEqual(
            sorted(unknown),
            [],
            "发现未登记的影子开关；先在 src/got/shadow_registry.py 登记并回答四问："
            + ", ".join(sorted(unknown)),
        )

    def test_no_stale_registry_entries(self):
        missing = set(SHADOW_REGISTRY) - discover_shadow_keys()
        self.assertEqual(
            sorted(missing),
            [],
            "登记表存在代码/.env.example 已不存在的 key；删除登记或恢复开关："
            + ", ".join(sorted(missing)),
        )

    def test_backtestable_gate_cannot_shadow_without_waiver(self):
        for basis in ("backtestable", "replayable"):
            with self.subTest(data_basis=basis):
                bad = ShadowRecord(
                    env="TEST_SHADOW",
                    default="1",
                    kind="gate",
                    data_basis=basis,
                    status="shadow",
                    artifact="test.json",
                    metric="test_metric",
                    min_samples=10,
                    max_days=10,
                    expiry="2026-12-31",
                )
                errors = validate_registry({"TEST_SHADOW": bad})
                self.assertTrue(
                    any("waiver" in error for error in errors),
                    f"{basis} gate 无 waiver 应被拒绝，实际 errors={errors}",
                )

    def test_forward_only_gate_can_shadow(self):
        forward = ShadowRecord(
            env="TEST_FORWARD_SHADOW",
            default="1",
            kind="gate",
            data_basis="forward_only",
            status="shadow",
            artifact="test.json",
            metric="test_metric",
            min_samples=10,
            max_days=10,
            expiry="2026-12-31",
        )
        self.assertEqual(validate_registry({"TEST_FORWARD_SHADOW": forward}), [])

    def test_shadow_gate_requires_samples_and_expiry(self):
        base = ShadowRecord(
            env="TEST_NO_SAMPLES_SHADOW",
            default="1",
            kind="gate",
            data_basis="forward_only",
            status="shadow",
            artifact="test.json",
            metric="test_metric",
        )
        bad = replace(
            base,
            min_samples=None,
            max_days=None,
            expiry="",
        )
        errors = validate_registry({"TEST_NO_SAMPLES_SHADOW": bad})
        joined = "\n".join(errors)
        self.assertIn("min_samples", joined)
        self.assertIn("max_days", joined)
        self.assertIn("expiry", joined)

    def test_kind_status_must_match(self):
        bad = ShadowRecord(
            env="TEST_TRACE_SHADOW",
            default="1",
            kind="trace",
            data_basis="n/a",
            status="shadow",
        )
        errors = validate_registry({"TEST_TRACE_SHADOW": bad})
        self.assertTrue(
            any("kind=trace" in error and "status" in error for error in errors),
            f"trace+shadow 应被拒绝，实际 errors={errors}",
        )

    def test_expired_gates_uses_fixed_date(self):
        base = ShadowRecord(
            env="TEST_SHADOW",
            default="1",
            kind="gate",
            data_basis="forward_only",
            status="shadow",
            artifact="test.json",
            metric="test_metric",
            min_samples=1,
            max_days=1,
            expiry="2026-11-15",
        )
        registry = {
            base.env: base,
            "TEST_ENABLED": replace(base, env="TEST_ENABLED", status="enabled"),
            "TEST_TRACE": replace(
                base,
                env="TEST_TRACE",
                kind="trace",
                data_basis="n/a",
                status="trace",
                expiry="",
            ),
        }
        self.assertEqual(expired_gates(registry, date(2026, 11, 14)), [])
        self.assertEqual(
            expired_gates(registry, date(2026, 11, 15)),
            [("TEST_SHADOW", "2026-11-15")],
        )


if __name__ == "__main__":
    unittest.main()
