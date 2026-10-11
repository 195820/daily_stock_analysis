# -*- coding: utf-8 -*-
"""批次 G：output_review 产出前运行时自查单测（一期 warn）。

每条检查配正/负样例：带病产物→触发，健康产物→静默（遵计划 §G 验收）。
"""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from src.got.output_review import (
    REVIEW_ARTIFACT,
    review_phase2,
    review_phase1,
    run_output_review,
)


def _codes(findings):
    return {f["code"] for f in findings}


def _decision(**kw):
    base = dict(
        status="normal",
        allocation=[],
        risk_assessment=SimpleNamespace(position_limit=0.0),
        unified_scoring=[],
        funnel_stats=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_disabled_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rec = run_output_review(decision=_decision(), contexts={}, target_date=date(2026, 10, 11), enabled=False)
    assert rec == {}
    assert not (tmp_path / "data" / "got_daily").exists()


def test_healthy_decision_no_findings():
    d = _decision(
        unified_scoring=[SimpleNamespace(
            symbol="600000.SH", is_holding=True, sector_name="银行",
            action_recommendation="hold",
            dimension_scores={"capital": 60, "news": 55, "sector": 62, "tech": 58, "risk": 60},
        )],
    )
    assert review_phase2(d) == []


def test_c1_failed_with_nonzero():
    d = _decision(status="failed", allocation=[SimpleNamespace(x=1)],
                  risk_assessment=SimpleNamespace(position_limit=0.3))
    assert "G-C1_failed_nonzero" in _codes(review_phase2(d))


def test_c3_trashbin_and_empty_sector():
    d = _decision(unified_scoring=[
        SimpleNamespace(symbol="A", is_holding=True, sector_name="其他",
                        action_recommendation="hold", dimension_scores={}),
        SimpleNamespace(symbol="B", is_holding=False, sector_name="",
                        action_recommendation="buy_trend", dimension_scores={}),
    ])
    f = review_phase2(d)
    c3 = next(x for x in f if x["code"] == "G-C3_trashbin_sector")
    assert "A" in c3["detail"] and "B" in c3["detail"]


def test_w1_single_dim_dictatorship():
    d = _decision(unified_scoring=[SimpleNamespace(
        symbol="C", is_holding=True, sector_name="半导体", action_recommendation="buy_trend",
        dimension_scores={"capital": 92, "news": 20, "sector": 15, "tech": 30, "risk": 25},
    )])
    assert "G-W1_single_dim_dictatorship" in _codes(review_phase2(d))


def test_w2_pool_zero():
    d = _decision(funnel_stats={"actionable_sector_count": 5, "zero_pick_rate": 1.0})
    assert "G-W2_pool_zero" in _codes(review_phase2(d))


def test_w2_not_fired_when_partial():
    d = _decision(funnel_stats={"actionable_sector_count": 5, "zero_pick_rate": 0.4})
    assert "G-W2_pool_zero" not in _codes(review_phase2(d))


def test_phase1_node_failed_warning():
    ctxs = {"market": SimpleNamespace(status="failed"), "sector": SimpleNamespace(status="normal")}
    assert _codes(review_phase1(ctxs)) == {"G-P1_node_failed"}


def test_none_decision_is_safe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rec = run_output_review(decision=None, contexts={}, target_date=date(2026, 10, 11), enabled=True)
    assert rec["critical"] == 0 and rec["warning"] == 0


def test_run_writes_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = _decision(status="failed", allocation=[SimpleNamespace(x=1)],
                  risk_assessment=SimpleNamespace(position_limit=0.5))
    rec = run_output_review(decision=d, contexts={"unified": SimpleNamespace(status="normal")},
                            target_date=date(2026, 10, 11), enabled=True)
    assert rec["mode"] == "warn"
    assert rec["critical"] >= 1
    art = tmp_path / "data" / "got_daily" / "20261011" / REVIEW_ARTIFACT
    assert art.exists()
    loaded = json.loads(art.read_text(encoding="utf-8"))
    assert loaded["critical"] == rec["critical"]
