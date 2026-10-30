# -*- coding: utf-8 -*-
"""Read-only GOT decision audit tests (2026-10-29 evidence pack)."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from src.got.audit_agent import (
    AuditFinding,
    AuditHypothesis,
    AuditPlan,
    EvidenceCard,
    GrillResult,
    GrillVerdict,
    _signature,
    _write_findings,
    collect_evidence_pack,
    detect_anomalies,
    run_audit,
    verify_finding,
)

AS_OF = date(2026, 10, 29)


@pytest.fixture(scope="module")
def pack():
    return collect_evidence_pack(AS_OF)


def test_collector_manifest_and_decision(pack):
    assert pack["as_of"] == "2026-10-29"
    assert "portfolio_decision.json" in pack["manifest"]["present"]
    assert pack["manifest"]["source"] == "archive_approximation"
    assert pack["code"]["scoring_formula_version"]


def test_detect_anomalies_has_actionable_pointer(pack):
    anomalies = detect_anomalies(pack)
    assert anomalies, "20261029 归档应至少命中一类确定性异常"
    for hyp in anomalies:
        assert hyp.pointer
        assert hyp.symptom and hyp.mechanism


def test_duplicate_catalyst_detected():
    fake = {
        "as_of": "2026-10-29",
        "archive": {"portfolio_decision.json": {"risk_assessment": {}, "allocation": []}},
        "db": {
            "catalysts": [
                {"sector": "半导体", "event_date": "2026-10-30", "source_news": "A"},
                {"sector": "半导体", "event_date": "2026-10-30", "source_news": "B"},
            ],
        },
    }
    anomalies = detect_anomalies(fake)
    assert any("重复催化剂" in h.symptom for h in anomalies)


def test_verifier_blocks_fake_pointer_and_secret(pack):
    finding = AuditFinding(
        finding_id="AUD-20261029-01", date="2026-10-29", severity="P1", status="confirmed",
        symptom="x", mechanism="y",
        evidence=[EvidenceCard(claim="x", source="archive", pointer="archive:missing.json#a", confidence=0.9)],
        confidence=0.9,
    )
    ok, problems = verify_finding(finding, pack)
    assert not ok and any("归档缺失" in p for p in problems)

    secret = finding.model_copy(deep=True)
    secret.evidence = [EvidenceCard(
        claim="api_key=sk-abcdefghijklmnop", source="archive",
        pointer="archive:portfolio_decision.json#status", confidence=0.9,
    )]
    ok, problems = verify_finding(secret, pack)
    assert not ok and any("敏感信息" in p for p in problems)


def test_unknown_verdict_not_upgraded():
    plan = AuditPlan(hypotheses=[AuditHypothesis(
        symptom="s", mechanism="m", pointer="archive:x#y", severity="P1",
    )])
    findings = _write_findings(
        AS_OF, plan,
        GrillResult(verdicts=[GrillVerdict(hypothesis_index=0, verdict="unknown")]),
        [EvidenceCard(claim="s", source="x", pointer="archive:x#y", confidence=0.9)],
        {},
    )
    assert findings == []


def test_recurrence_increments():
    plan = AuditPlan(hypotheses=[AuditHypothesis(
        symptom="同一症状", mechanism="同一机制", pointer="archive:x#y", severity="P2",
    )])
    sig = _signature("同一症状", "同一机制", "archive:x#y")
    previous = {sig: {"first_seen": "2026-10-28", "recurrence": 2}}
    findings = _write_findings(
        AS_OF, plan,
        GrillResult(verdicts=[GrillVerdict(hypothesis_index=0, verdict="likely")]),
        [EvidenceCard(claim="s", source="x", pointer="archive:x#y", confidence=0.95)],
        previous,
    )
    assert findings[0].recurrence == 3
    assert findings[0].first_seen == "2026-10-28"


@pytest.mark.asyncio
async def test_missing_archive_is_incomplete_not_pass(tmp_path):
    result = await run_audit(
        date(2020, 1, 2), base_dir=tmp_path / "missing_archive",
        out_root=tmp_path / "audit", use_llm=False,
    )
    assert result["status"] == "incomplete"
    assert result["errors"]
    out_dir = tmp_path / "audit" / "20200102"
    assert (out_dir / "report.md").exists()
    assert "incomplete" in (out_dir / "report.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_audit_writes_only_under_out_root(tmp_path, pack):
    out_root = tmp_path / "audit"
    result = await run_audit(AS_OF, out_root=out_root, use_llm=False)
    assert result["status"] in ("ok", "incomplete")
    out_dir = out_root / "20261029"
    for name in ("report.md", "findings.jsonl", "audit_runs.jsonl", "evidence_pack.json"):
        assert (out_dir / name).exists(), name
    # 不写生产决策/归档目录
    assert not (tmp_path / "data").exists()
