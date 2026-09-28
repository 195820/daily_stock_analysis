# -*- coding: utf-8 -*-
"""Phase 3b 分歧摘要单测（批次 5 §3.4）。

覆盖：11 类冲突分类全分支表驱动 / RiskOverridePlan 转换 / 加权共识权重解析
（memory=None 退化等权）/ build_opinions 信号映射 + degraded 标记 /
build_disagreement_summary 形状 / 三态契约（helper 级：影子=纯观测零决策改动）。
"""
from __future__ import annotations

from src.agent.protocols import AgentOpinion
from src.got.disagreement import (
    _ALL_CONFLICT_TYPES,
    RiskOverridePlan,
    build_disagreement_summary,
    build_opinions,
    classify_conflict_type,
    compute_signal_weights,
    derive_risk_override_plan,
    weighted_consensus,
)


def _op(signal: str, *, degraded: bool = False, risk_caution: bool = False, name: str = "a", conf: float = 0.5) -> AgentOpinion:
    return AgentOpinion(
        agent_name=name,
        signal=signal,
        confidence=conf,
        raw_data={"degraded": degraded, "risk_caution": risk_caution},
    )


# ─────────────────────────────────────────────────────────────
# 11 类冲突分类全分支表驱动
# ─────────────────────────────────────────────────────────────

def test_classify_all_11_types():
    cases = {
        "risk_override": ([_op("buy")], True),
        "degraded_only": ([_op("hold", degraded=True), _op("buy", degraded=True)], False),
        "aligned_bullish": ([_op("buy"), _op("buy", name="b")], False),
        "aligned_bearish": ([_op("sell")], False),
        "aligned_neutral": ([_op("hold")], False),
        "mixed_directional_signals": ([_op("buy"), _op("sell", name="b")], False),
        "partial_bullish_with_degraded_inputs": ([_op("buy"), _op("hold", degraded=True, name="b")], False),
        "partial_bearish_with_degraded_inputs": ([_op("sell"), _op("hold", degraded=True, name="b")], False),
        "neutral_with_degraded_inputs": ([_op("hold"), _op("buy", degraded=True, name="b")], False),
        "mixed_with_degraded_inputs": ([_op("buy"), _op("sell", name="b"), _op("hold", degraded=True, name="c")], False),
        "bullish_with_risk_caution": ([_op("buy", risk_caution=True), _op("buy", name="b")], False),
    }
    assert len(cases) == 11, "表驱动必须覆盖全部 11 类"
    seen = set()
    for want, (ops, rop) in cases.items():
        got = classify_conflict_type(ops, risk_override_present=rop)
        assert got["conflict_type"] == want, f"{want} 误判为 {got['conflict_type']}"
        assert got["decision_path_hint"], f"{want} 缺 decision_path_hint"
        seen.add(got["conflict_type"])
    assert seen == set(_ALL_CONFLICT_TYPES), f"分类未穷举 11 类，缺：{set(_ALL_CONFLICT_TYPES) - seen}"


def test_risk_override_takes_top_priority():
    # 即便方向一致看多，risk_override_present=True 也必须压到 risk_override
    res = classify_conflict_type([_op("buy"), _op("buy", name="b")], risk_override_present=True)
    assert res["conflict_type"] == "risk_override"


# ─────────────────────────────────────────────────────────────
# RiskOverridePlan 转换
# ─────────────────────────────────────────────────────────────

def test_derive_plan_veto():
    plan = derive_risk_override_plan("risk_override")
    assert isinstance(plan, RiskOverridePlan)
    assert plan.veto_buy is True and plan.adjustment == "veto" and plan.reason == "risk_veto"


def test_derive_plan_downgrades():
    for ct in ("aligned_bearish", "partial_bearish_with_degraded_inputs",
               "mixed_directional_signals", "bullish_with_risk_caution"):
        plan = derive_risk_override_plan(ct)
        assert plan.veto_buy is False and plan.adjustment == "downgrade_one", ct


def test_derive_plan_no_override():
    for ct in ("aligned_bullish", "aligned_neutral", "degraded_only",
               "partial_bullish_with_degraded_inputs"):
        plan = derive_risk_override_plan(ct)
        assert plan.veto_buy is False and plan.adjustment == "", ct


# ─────────────────────────────────────────────────────────────
# 权重解析（memory=None 退化等权）
# ─────────────────────────────────────────────────────────────

def test_weights_memory_none_equal():
    ops = [_op("buy", name="regime"), _op("sell", name="capital")]
    w = compute_signal_weights(ops, memory=None)
    assert set(w.keys()) == {"regime", "capital"}
    assert all(v == 1.0 for v in w.values()), "memory=None 必须退化等权"


def test_weights_memory_calibrated():
    class _Mem:
        def compute_skill_weights(self, names):
            return {"regime": 2.0}

    ops = [_op("buy", name="regime"), _op("sell", name="capital")]
    w = compute_signal_weights(ops, memory=_Mem())
    assert w["regime"] > w["capital"], "校准因子应放大 regime（先验权重也更高）"


# ─────────────────────────────────────────────────────────────
# 加权共识
# ─────────────────────────────────────────────────────────────

def test_weighted_consensus_empty_defaults_hold():
    res = weighted_consensus([], {})
    assert res["signal"] == "hold" and res["weighted_score"] == 3.0


def test_weighted_consensus_direction():
    ops = [_op("buy", name="a", conf=0.8), _op("buy", name="b", conf=0.6)]
    w = {"a": 1.0, "b": 1.0}
    res = weighted_consensus(ops, w)
    assert res["weighted_score"] == 4.0 and res["signal"] == "buy"


# ─────────────────────────────────────────────────────────────
# build_opinions 信号映射 + degraded 标记
# ─────────────────────────────────────────────────────────────

def test_build_opinions_degraded_when_missing():
    ops = build_opinions()  # 全部缺失
    names = {op.agent_name for op in ops}
    assert {"regime", "family_rank", "capital", "catalyst_gate", "sentiment"} <= names
    assert all(op.raw_data["degraded"] for op in ops), "缺失输入应逐源标 degraded"


def test_build_opinions_regime_and_capital_mapping():
    class _Regime:
        regime = "bull"
        cross_index_consistency = 0.7

    class _Capital:
        institutional_score = 0.5

    class _Sector:
        active_mainline = [1, 2, 3]

    ops = build_opinions(regime=_Regime(), capital=_Capital(), sector=_Sector(), sector_intels=[])
    by = {op.agent_name: op for op in ops}
    assert by["regime"].signal == "buy" and not by["regime"].raw_data["degraded"]
    assert by["capital"].signal == "buy"
    assert by["family_rank"].signal == "buy"


def test_build_opinions_sell_source_risk():
    ss = {"mode": "enabled", "planned_trims": [{"symbol": "600000"}]}
    ops = build_opinions(sell_source=ss)
    ss_op = next(op for op in ops if op.agent_name == "sell_source")
    assert ss_op.signal == "sell" and ss_op.raw_data["risk_caution"] is True


# ─────────────────────────────────────────────────────────────
# build_disagreement_summary 形状
# ─────────────────────────────────────────────────────────────

def test_summary_shape():
    ops = [_op("buy", name="regime"), _op("buy", name="capital")]
    s = build_disagreement_summary(ops, memory=None)
    for k in ("bullish_agents", "bearish_agents", "neutral_agents", "conflict_type",
              "decision_path_hint", "risk_override_present", "consensus", "risk_control",
              "weights_equal"):
        assert k in s, f"摘要缺字段 {k}"
    assert s["weights_equal"] is True
    assert s["conflict_type"] == "aligned_bullish"
    assert s["risk_control"]["veto_buy"] is False


def test_summary_risk_override_present_flips_veto():
    ops = [_op("buy", name="regime"), _op("buy", name="capital")]
    s = build_disagreement_summary(ops, risk_override_present=True, memory=None)
    assert s["conflict_type"] == "risk_override"
    assert s["risk_control"]["veto_buy"] is True
