# -*- coding: utf-8 -*-
"""P2 路由影子层单测：E16 规则一致性、阻断层优先级、提醒集合、无 buy 资格。

E16 变体的真源是 `scripts/audit_node2_state_counterfactual.py`（164 board-day 反事实
的既有实现）；本模块是纯函数复刻，这里对同一输入做逐值比对，规则漂移即失败。
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from src.got.theme_ledger import Observation, derive_episodes
from src.got.theme_routing_shadow import (
    BLOCKING_LAYERS,
    GATE_ORDER,
    GateFacts,
    build_rows,
    classify_blocking_layer,
    collect_gate_facts,
    first_failing_gate,
    gate_verdicts,
    detail_index,
    observation_reminders,
    summarize,
)

_SCRIPT = Path("scripts/audit_node2_state_counterfactual.py")


def _load_counterfactual():
    spec = importlib.util.spec_from_file_location("_cf", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _det(**over) -> dict:
    base = {
        "state": "neutral",
        "strength_score": 85.0,
        "tier": "A",
        "frame_group": "AI软件",
        "rrg": {"quadrant": "leading"},
        "indicators": {"ambush_score_v2": 0.31},
        "break_signals": {
            "price_chg_5d": 0.02, "above_ma20": True, "high20_pos": 0.9,
            "overheat_level": "normal", "score_drop": 3.0,
        },
    }
    base.update(over)
    return base


def _payload(**over) -> dict:
    payload = {
        "active_mainline": [],
        "ambush": [],
        "near_miss_ambush": [],
        "top_by_score": [],
        "defensive_rotation": [],
        "cooling_down": [],
        "exhausted_mainline": [],
        "industry_top": [],
        "overheat_lookup": {},
        "crowded_families": [],
    }
    payload.update(over)
    return payload


class TestE16Parity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cf = _load_counterfactual()

    def test_rules_match_counterfactual_script(self):
        from src.got import theme_routing_shadow as trs

        cases = [
            _det(),
            _det(indicators={"ambush_score_v2": 0.0}),
            _det(strength_score=85.0, rrg={"quadrant": "weakening"}),
            _det(strength_score=70.0, tier="A"),
            _det(strength_score=70.0, tier="B"),
            _det(break_signals={"price_chg_5d": -0.01, "above_ma20": True,
                                "high20_pos": 0.9, "overheat_level": "normal", "score_drop": 0}),
            _det(break_signals={"price_chg_5d": 0.02, "above_ma20": False,
                                "high20_pos": 0.9, "overheat_level": "normal", "score_drop": 0}),
            _det(break_signals={"price_chg_5d": 0.02, "above_ma20": True,
                                "high20_pos": 0.9, "overheat_level": "normal", "score_drop": 25}),
            _det(break_signals={"price_chg_5d": 0.02, "above_ma20": True,
                                "high20_pos": 0.1, "overheat_level": "exhausted", "score_drop": 0}),
        ]
        for det in cases:
            self.assertEqual(trs.broken(det), self.cf.broken(det))
            self.assertEqual(trs.entry_ok(det), self.cf.entry_ok(det))
            self.assertEqual(trs.quadrant(det), self.cf.quadrant(det))
            self.assertAlmostEqual(trs.ambush_score(det), self.cf.ambush_score(det))
            self.assertEqual(trs.cf_b(det), self.cf.cf_b(det))
            self.assertEqual(trs.cf_c(det), self.cf.cf_c(det))
            self.assertEqual(trs.cf_d(det), self.cf.cf_d(det))


class TestClassification(unittest.TestCase):
    def _facts(self, **over) -> GateFacts:
        base = dict(
            code="880692", name="短剧游戏", buckets=(), det_present=True, state="ambush",
            strength=84.4, broken=False, entry_ok=True, quadrant="leading",
            ambush_score=0.31, overheat_level="normal", overheat_severity=0.0,
            regime="bull", frame_group="AI软件", crowded=False,
            position_ratio=0.5, position_limit=0.8,
        )
        base.update(over)
        return GateFacts(**base)

    def test_tradable_bucket_is_visible_but_top_only_is_not(self):
        self.assertEqual(
            classify_blocking_layer(self._facts(buckets=("ambush",)))[0], "visible"
        )
        self.assertEqual(
            classify_blocking_layer(self._facts(buckets=("top_by_score",)))[0], "top_only"
        )

    def test_no_detail_is_unresolved_not_guessed(self):
        layer, _ = classify_blocking_layer(self._facts(det_present=False, buckets=()))
        self.assertEqual(layer, "unresolved_no_detail")

    def test_precedence_order(self):
        self.assertEqual(
            classify_blocking_layer(self._facts(broken=True, regime="bear"))[0], "trend_broken"
        )
        self.assertEqual(
            classify_blocking_layer(self._facts(overheat_level="warning"))[0], "overheat"
        )
        self.assertEqual(classify_blocking_layer(self._facts(regime="bear"))[0], "regime_blocked")
        self.assertEqual(classify_blocking_layer(self._facts(crowded=True))[0], "crowding")
        # 组合预算只进 verdicts，不作板块级首要阻断层
        budget_facts = self._facts(position_ratio=0.9, position_limit=0.8)
        self.assertEqual(classify_blocking_layer(budget_facts)[0], "capacity")
        self.assertEqual(gate_verdicts(budget_facts)["budget"]["state"], "fail")
        self.assertEqual(classify_blocking_layer(self._facts())[0], "capacity")
        self.assertEqual(
            classify_blocking_layer(self._facts(state="neutral", strength=60.0))[0], "score_rank"
        )

    def test_all_layers_are_declared(self):
        produced = {
            classify_blocking_layer(self._facts(buckets=("ambush",)))[0],
            classify_blocking_layer(self._facts(buckets=("top_by_score",)))[0],
            classify_blocking_layer(self._facts(det_present=False))[0],
            classify_blocking_layer(self._facts(broken=True))[0],
            classify_blocking_layer(self._facts(overheat_level="warning"))[0],
            classify_blocking_layer(self._facts(regime="bear"))[0],
            classify_blocking_layer(self._facts(crowded=True))[0],
            classify_blocking_layer(self._facts(position_ratio=0.9, position_limit=0.8))[0],
            classify_blocking_layer(self._facts())[0],
            classify_blocking_layer(self._facts(state="neutral", strength=60.0))[0],
        }
        self.assertTrue(produced <= set(BLOCKING_LAYERS), produced - set(BLOCKING_LAYERS))

    def test_gate_verdicts_cover_all_gates_and_agree_with_layer(self):
        mapping = {
            "trend_broken": "trend",
            "overheat": "overheat",
            "regime_blocked": "regime",
            "crowding": "crowding",
            "capacity": "capacity",
        }
        cases = [
            self._facts(broken=True),
            self._facts(overheat_level="warning"),
            self._facts(regime="bear"),
            self._facts(crowded=True),
            self._facts(),
            self._facts(state="neutral", strength=60.0),
            self._facts(buckets=("ambush",)),
        ]
        for facts in cases:
            verdicts = gate_verdicts(facts)
            self.assertEqual(set(verdicts), set(GATE_ORDER))
            layer, _ = classify_blocking_layer(facts)
            if layer in mapping:
                self.assertEqual(first_failing_gate(verdicts), mapping[layer])
        # 缺 detail：全 unknown，不得把未知当 pass
        unknown = gate_verdicts(self._facts(det_present=False))
        self.assertTrue(all(v["state"] == "unknown" for v in unknown.values()))
        self.assertIsNone(first_failing_gate(unknown))

    def test_collect_gate_facts_reads_archived_fields(self):
        payload = _payload(
            top_by_score=[_det(name="短剧游戏", code="880692", state="ambush")],
            overheat_lookup={"短剧游戏": 0.13},
            crowded_families=["AI软件"],
        )
        facts = collect_gate_facts(
            code="880692", name="短剧游戏", payload=payload,
            unified={"market_regime": {"regime": "bear"}},
            risk={"current_position_ratio": 0.99, "position_limit": 0.43},
        )
        self.assertEqual(facts.buckets, ("top_by_score",))
        self.assertEqual(facts.overheat_level, "normal")
        self.assertAlmostEqual(facts.overheat_severity or 0, 0.13)
        self.assertTrue(facts.crowded)
        self.assertEqual(facts.regime, "bear")
        self.assertEqual(facts.position_limit, 0.43)


class TestRows(unittest.TestCase):
    def _obs(self, day="2026-10-29", code="880692", strength=84.4):
        return Observation(
            trade_date=day, board_code=code, board_name="短剧游戏", theme_id=code,
            strength=strength, state="ambush", in_top_output=True,
        )

    def test_rows_carry_alt_routes_and_no_buy_field(self):
        payload = _payload(top_by_score=[_det(name="短剧游戏", code="880692", state="ambush")])
        obs = self._obs()
        _, index = derive_episodes([obs], ["2026-10-29"])
        from src.got.theme_ledger import derive_setups

        setups = derive_setups([obs], index, ["2026-10-29"])
        rows = build_rows(
            trade_date="2026-10-29", setups=setups, observations=[obs],
            payload=payload, unified={"market_regime": {"regime": "bull"}}, risk={},
        )
        self.assertTrue(rows)
        for row in rows:
            self.assertNotIn("buy", row)
            self.assertNotIn("action", row)
            self.assertEqual(row["blocking_layer"], "top_only")
            self.assertEqual(row["e16_alt_routes"], {"B": "ambush", "C": "active_mainline", "D": "active_mainline"})
        summary = summarize(rows, [])
        self.assertEqual(summary["by_layer"], {"top_only": len(rows)})

    def test_unarmed_setup_keeps_fact_layer_but_is_not_applied(self):
        payload = _payload(top_by_score=[_det(name="短剧游戏", code="880692")])
        obs = Observation(
            trade_date="2026-10-29", board_code="880692", board_name="短剧游戏",
            theme_id="880692", strength=84.4, state="ambush",
        )
        _, index = derive_episodes([obs], ["2026-10-29"])
        from src.got.theme_ledger import derive_setups

        setups = derive_setups([obs], index, ["2026-10-29"])
        rows = build_rows(
            trade_date="2026-10-29", setups=setups, observations=[obs],
            payload=payload, unified={}, risk={},
        )
        self.assertTrue(any(r["blocking_layer"] == "top_only" for r in rows))
        by_variant = {r["variant"]: r for r in rows}
        # 资金三变体缺证据 → 未申请复评；纯观察变体 armed
        self.assertTrue(by_variant["none"]["setup_applied"])
        self.assertTrue(all(
            not r["setup_applied"] for r in rows if r["variant"] != "none"
        ))
        summary = summarize(rows, [])
        self.assertEqual(summary["by_layer_armed"].get("top_only"), 1)

    def test_observation_reminders_exclude_visible_boards(self):
        payload = _payload()
        vis = Observation(
            trade_date="2026-10-29", board_code="880001", board_name="可见板块",
            theme_id="880001", strength=85.0, state="neutral",
        )
        payload["top_by_score"] = [{"code": "880001", "name": "可见板块", "strength_score": 85.0}]
        hidden = Observation(
            trade_date="2026-10-29", board_code="880002", board_name="隐藏板块",
            theme_id="880002", strength=83.0, state="neutral",
        )
        episodes, _ = derive_episodes([vis, hidden], ["2026-10-29"])
        reminders = observation_reminders(
            trade_date="2026-10-29", episodes=episodes,
            observations=[vis, hidden], payload=payload,
        )
        self.assertEqual([r["board_name"] for r in reminders], ["隐藏板块"])


if __name__ == "__main__":
    unittest.main()
