# -*- coding: utf-8 -*-
"""Read-only GOT decision audit (independent post-hoc pipeline).

Single orchestrator, role-isolated contexts:
Collector (deterministic) -> Planner (LLM) -> Scout (symbolic, bounded) ->
Griller (LLM, independent) -> Writer -> Verifier.

Hard rules:
- Writes only under ``data/got_audit/YYYYMMDD/`` (report.md / findings.jsonl /
  audit_runs.jsonl / evidence_pack.json / agent trace). Never edits src/, docs,
  thesis, trade plans or config.
- Missing evidence is explicit; LLM prose is never evidence.
- Unknown/rejected verdicts cannot be upgraded by the writer.
- Any failure/timeout/budget overrun yields an ``incomplete`` report, never a
  silent pass.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "got-audit/v1"
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)(api[_-]?key|authorization|token|cookie|password)\s*[:=]\s*[^\s,;]+"),
)
ACCOUNT_PATTERN = re.compile(r"(?<![\d.])\d{10,}(?![\d.])")


class EvidenceCard(BaseModel):
    claim: str
    source: str
    exact_value: Any = None
    pointer: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class AuditHypothesis(BaseModel):
    symptom: str
    mechanism: str
    evidence_needed: list[str] = Field(default_factory=list)
    falsification: str = ""
    priority: int = 5
    severity: str = "P2"
    pointer: str = ""
    exact_value: Any = None


class AuditPlan(BaseModel):
    status: str = "ok"  # ok / no_audit_target
    hypotheses: list[AuditHypothesis] = Field(default_factory=list)


class GrillVerdict(BaseModel):
    hypothesis_index: int
    verdict: str = "unknown"  # confirmed / likely / rejected / unknown
    reason: str = ""


class GrillResult(BaseModel):
    verdicts: list[GrillVerdict] = Field(default_factory=list)


class AuditFinding(BaseModel):
    finding_id: str
    date: str
    severity: str
    status: str
    symptom: str
    evidence: list[EvidenceCard] = Field(default_factory=list)
    mechanism: str = ""
    counterfactual: str = ""
    impact: str = ""
    fix_options: list[str] = Field(default_factory=list)
    acceptance: str = ""
    confidence: float = 0.0
    first_seen: str = ""
    recurrence: int = 1


class AuditDraftBatch(BaseModel):
    findings: list[AuditFinding] = Field(default_factory=list)


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _dig(data: Any, dotted: str) -> Any:
    current = data
    for part in dotted.split(".") if dotted else []:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


# ----------------------------------------------------------------------
# 1. Collector (deterministic, zero LLM)
# ----------------------------------------------------------------------
def collect_evidence_pack(as_of: date, *, base_dir: Path = Path("data/got_daily")) -> dict:
    from src.got.catalyst_config import SCORING_FORMULA_VERSION

    archive_dir = base_dir / as_of.strftime("%Y%m%d")
    names = [
        "market.json", "sector.json", "capital.json", "news.json",
        "sentiment.json", "unified.json", "portfolio_decision.json",
        "agent_glue.json", "narrative_context.json", "meta.json",
        "agent_run.json", "candidate_pool.json", "run_card.json",
    ]
    archive = {}
    present, missing = [], []
    for name in names:
        path = archive_dir / name
        if path.exists():
            archive[name] = _read_json(path)
            present.append(name)
        else:
            archive[name] = None
            missing.append(name)

    steps: List[dict] = []
    steps_path = archive_dir / "agent_steps.jsonl"
    if steps_path.exists():
        try:
            for line in steps_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    steps.append(json.loads(line))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Audit] agent_steps 读取失败: %s", exc)

    db_pack: Dict[str, Any] = {}
    try:
        from src.storage import (
            CatalystEventRecord,
            CatalystRealizationRecord,
            HoldingThesisRecord,
            MoveAttributionRecord,
            TradePlanRecord,
            DatabaseManager,
        )

        db = DatabaseManager()
        with db.get_session() as session:
            plans = session.query(TradePlanRecord).order_by(TradePlanRecord.plan_date.desc()).all()
            db_pack["trade_plans"] = [
                {
                    "plan_date": str(r.plan_date), "symbol": r.symbol, "action": r.action,
                    "target_quantity": r.target_quantity, "execution_status": r.execution_status,
                    "blocked_reason": r.blocked_reason,
                }
                for r in plans[:200]
            ]
            theses = session.query(HoldingThesisRecord).order_by(HoldingThesisRecord.entry_date.desc()).all()
            db_pack["thesis"] = [
                {
                    "symbol": r.symbol, "sector": r.sector_name, "family": r.family_name,
                    "entry_date": str(r.entry_date), "is_open": bool(getattr(r, "is_open", False)),
                    "lifecycle_state": getattr(r, "lifecycle_state", None),
                }
                for r in theses[:200]
            ]
            cats = session.query(CatalystEventRecord).filter(
                CatalystEventRecord.event_date >= as_of,
            ).all()
            db_pack["catalysts"] = [
                {
                    "event_id": r.event_id, "event_date": str(r.event_date), "sector": r.sector,
                    "title": r.title, "source": r.source, "source_news": r.source_news,
                    "affected_sectors": r.affected_sectors, "confidence": r.confidence,
                }
                for r in cats[:300]
            ]
            realizations = session.query(CatalystRealizationRecord).all()
            db_pack["catalyst_realizations"] = [
                {
                    "event_id": r.event_id, "sector": r.sector, "actual_return": r.actual_return,
                    "direction_correct": r.drift_direction_correct,
                }
                for r in realizations[:200]
            ]
            moves = session.query(MoveAttributionRecord).filter(
                MoveAttributionRecord.date == as_of,
            ).all()
            db_pack["move_attributions"] = [
                {
                    "symbol": r.symbol, "bucket": r.bucket, "theme": r.theme,
                    "confidence": r.confidence, "residual": r.residual,
                }
                for r in moves
            ]
    except Exception as exc:  # noqa: BLE001
        db_pack["error"] = f"{type(exc).__name__}: {exc}"

    log_tail = ""
    try:
        log_dir = Path("logs")
        candidates = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            lines = candidates[0].read_text(encoding="utf-8", errors="ignore").splitlines()
            log_tail = "\n".join(lines[-300:])
    except Exception:  # noqa: BLE001
        log_tail = ""

    from src.got.archive import _get_git_commit

    pack = {
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "archive_dir": str(archive_dir),
        "archive": archive,
        "agent_steps": steps,
        "db": db_pack,
        "logs": log_tail,
        "manifest": {
            "present": present,
            "missing": missing,
            "trade_plan_rows": len(db_pack.get("trade_plans", [])),
            "agent_step_rows": len(steps),
            "source": "archive_approximation",
        },
        "code": {
            "git_commit": _get_git_commit(),
            "scoring_formula_version": SCORING_FORMULA_VERSION,
            "audited_at": datetime.now().isoformat(),
        },
    }
    return pack


# ----------------------------------------------------------------------
# 2. deterministic anomaly triggers
# ----------------------------------------------------------------------
def detect_anomalies(pack: dict) -> List[AuditHypothesis]:
    out: List[AuditHypothesis] = []
    archive = pack.get("archive") or {}
    decision = archive.get("portfolio_decision.json") or {}
    risk = decision.get("risk_assessment") or {}
    allocation = decision.get("allocation") or []
    steps = pack.get("agent_steps") or []
    db = pack.get("db") or {}

    # ① 仓位超限无减仓
    current = float(risk.get("current_position_ratio", 0) or 0)
    limit = float(risk.get("position_limit", 0) or 0)
    sells = [a for a in allocation if a.get("action") == "sell"]
    if current > limit + 0.005 and not sells:
        out.append(AuditHypothesis(
            symptom=f"仓位超限未减仓：current={current:.2%} > limit={limit:.2%}",
            mechanism="组合层超限路径未触发或减仓被闸门顺延",
            evidence_needed=["portfolio_decision.risk_assessment", "portfolio_decision.allocation"],
            falsification="若存在被审批的减仓顺延记录则不是缺陷",
            priority=1, severity="P1",
            pointer="archive:portfolio_decision.json#risk_assessment.current_position_ratio",
            exact_value=risk.get("current_position_ratio"),
        ))

    # ② 零池/漏斗归零
    meta = archive.get("meta.json") or {}
    funnel = meta.get("funnel_stats") or {}
    zero_rate = funnel.get("zero_pick_rate")
    if zero_rate == 1.0 or (not decision.get("unified_scoring") and decision.get("status") != "failed"):
        out.append(AuditHypothesis(
            symptom="候选池/漏斗归零：当日无任何可执行候选",
            mechanism="过滤链或数据源失败导致候选全灭",
            evidence_needed=["meta.json#funnel_stats", "portfolio_decision.unified_scoring"],
            falsification="若当日全市场无合格标的且数据源完整则不是缺陷",
            priority=2, severity="P1",
            pointer="archive:meta.json#funnel_stats.zero_pick_rate",
            exact_value=zero_rate,
        ))

    # ③ LLM 失败（决策输入优先，非决策任务降级）
    failed = [s for s in steps if s.get("status") == "failed"]
    if failed:
        critical = [s for s in failed if str(s.get("task_id", "")).startswith(("node6.semantic_glue", "node7."))]
        out.append(AuditHypothesis(
            symptom=f"语义调用失败 {len(failed)} 次（决策输入 {len(critical)} 次）",
            mechanism="模型/网络/结构化解析失败导致语义证据缺位",
            evidence_needed=["agent_steps.jsonl"],
            falsification="若失败任务均为非决策展示任务则影响有限",
            priority=2 if critical else 4,
            severity="P1" if critical else "P2",
            pointer="archive:agent_steps.jsonl",
            exact_value=len(failed),
        ))

    # ④ 计划与成交/状态不一致
    stale = [
        p for p in (db.get("trade_plans") or [])
        if p.get("execution_status") == "pending" and str(p.get("plan_date")) < pack.get("as_of", "")
    ]
    if stale:
        out.append(AuditHypothesis(
            symptom=f"过期 pending 计划 {len(stale)} 条",
            mechanism="计划对账未回填执行状态",
            evidence_needed=["trade_plans"],
            falsification="若券商侧未回传成交且计划仍在有效期内则不是缺陷",
            priority=3, severity="P2",
            pointer="db:trade_plans",
            exact_value=len(stale),
        ))

    # ⑤ 重复催化
    seen: Dict[tuple, int] = {}
    for row in db.get("catalysts") or []:
        key = (row.get("sector"), row.get("event_date"))
        seen[key] = seen.get(key, 0) + 1
    duplicates = {k: v for k, v in seen.items() if v > 1}
    if duplicates:
        out.append(AuditHypothesis(
            symptom=f"同板块同日重复催化剂 {len(duplicates)} 组",
            mechanism="逻辑事件去重未覆盖该 source/窗口",
            evidence_needed=["catalyst_events"],
            falsification="若确为不同事件（不同 source_news）则合法",
            priority=3, severity="P2",
            pointer="db:catalysts",
            exact_value=len(duplicates),
        ))

    # ⑥ 身份跳变 / 冲突
    narrative = archive.get("narrative_context.json") or {}
    changed = [
        i for i in (narrative.get("identities") or [])
        if i.get("identity_change") == "changed" or i.get("conflicts")
    ]
    if changed:
        out.append(AuditHypothesis(
            symptom=f"主题身份跳变/冲突 {len(changed)} 条",
            mechanism="身份 Agent 证据不足或主题漂移未被既有身份吸收",
            evidence_needed=["narrative_context.json"],
            falsification="若跳变有≥2来源证据且与价格行为一致则不是缺陷",
            priority=3, severity="P2",
            pointer="archive:narrative_context.json#identities",
            exact_value=len(changed),
        ))

    # ⑦ 持仓缺归因
    held = [s.get("symbol") for s in (decision.get("unified_scoring") or []) if s.get("is_holding")]
    attributed = {m.get("symbol") for m in (db.get("move_attributions") or []) if m.get("bucket") != "unexplained"}
    missing_attr = [s for s in held if s and s not in attributed]
    if missing_attr:
        out.append(AuditHypothesis(
            symptom=f"持仓缺可解释归因 {len(missing_attr)} 只",
            mechanism="个股/板块证据缺失或归因器未运行",
            evidence_needed=["move_attributions"],
            falsification="若无任何可得证据（停牌/数据源缺失）则属于显式缺失",
            priority=4, severity="P2",
            pointer="db:move_attributions",
            exact_value=missing_attr[:5],
        ))

    # ⑧ 硬止损/低分持仓未被减仓（宽限短路可观察）
    stop_ratio = float(risk.get("stop_loss_threshold", 0) or 0)
    stop_pct = abs(stop_ratio) * 100 if stop_ratio else 5.0
    risk_holdings = []
    for s in (decision.get("unified_scoring") or []):
        if not s.get("is_holding"):
            continue
        reasoning = str(s.get("reasoning") or "")
        pnl_match = re.search(r"P&L\s*([+\-]?\d+(?:\.\d+)?)%", reasoning)
        pnl = float(pnl_match.group(1)) if pnl_match else None
        if (pnl is not None and pnl <= -abs(stop_pct)) or float(s.get("composite_score", 100) or 100) < 40:
            risk_holdings.append(s)
    reduced = {
        a.get("symbol") for a in allocation
        if a.get("action") in ("sell", "reduce")
    }
    bypassed = [s.get("symbol") for s in risk_holdings if s.get("symbol") not in reduced]
    if bypassed:
        out.append(AuditHypothesis(
            symptom=f"低分持仓未减仓 {len(bypassed)} 只（<40 分）",
            mechanism="板块漂移宽限/闸门顺延导致风险处置被短路",
            evidence_needed=["portfolio_decision.unified_scoring", "portfolio_decision.allocation"],
            falsification="若同一标的已有 pending_exit 或被 GATE-1 明确顺延则不是缺陷",
            priority=2, severity="P1",
            pointer="archive:portfolio_decision.json#unified_scoring",
            exact_value=bypassed[:5],
        ))

    # ⑨ 新词/新主题缺失：高影响事件板块未出现在 confirmed_sectors
    unified = archive.get("unified.json") or {}
    confirmed = {
        s.get("sector_name") for s in (unified.get("confirmed_sectors") or []) if s.get("sector_name")
    }
    news_payload = archive.get("news.json") or {}
    missing_sectors = []
    for ev in (news_payload.get("major_events") or []):
        if ev.get("impact_level") != "high":
            continue
        for sector in (ev.get("affected_sectors") or []):
            if sector and sector not in confirmed and sector not in missing_sectors:
                missing_sectors.append(sector)
    if missing_sectors:
        out.append(AuditHypothesis(
            symptom=f"高影响事件板块未进入 confirmed_sectors: {', '.join(missing_sectors[:5])}",
            mechanism="事件→板块映射缺失或板块强度闸门淘汰",
            evidence_needed=["news.json", "unified.json#confirmed_sectors"],
            falsification="若板块强度不足被明确淘汰则不是映射缺陷",
            priority=3, severity="P2",
            pointer="archive:news.json#major_events",
            exact_value=missing_sectors[:5],
        ))
    return out


# ----------------------------------------------------------------------
# 3. Scout (symbolic, read-only, bounded)
# ----------------------------------------------------------------------
def scout_evidence(pack: dict, hypothesis: AuditHypothesis) -> List[EvidenceCard]:
    cards: List[EvidenceCard] = []
    pointer = hypothesis.pointer or ""
    if pointer.startswith("archive:"):
        name, _, dotted = pointer.partition("#")
        filename = name.split(":", 1)[1]
        value = _dig((pack.get("archive") or {}).get(filename), dotted)
        cards.append(EvidenceCard(
            claim=hypothesis.symptom, source=f"archive:{filename}",
            exact_value=value if value is not None else hypothesis.exact_value,
            pointer=pointer, confidence=0.95 if value is not None else 0.3,
        ))
    elif pointer.startswith("db:"):
        table = pointer.split(":", 1)[1]
        rows = (pack.get("db") or {}).get(table) or []
        cards.append(EvidenceCard(
            claim=hypothesis.symptom, source=f"db:{table}",
            exact_value=hypothesis.exact_value if hypothesis.exact_value is not None else len(rows),
            pointer=pointer, confidence=0.9 if rows else 0.3,
        ))
    elif pointer:
        cards.append(EvidenceCard(
            claim=hypothesis.symptom, source=pointer, exact_value=hypothesis.exact_value,
            pointer=pointer, confidence=0.2,
        ))

    for need in (hypothesis.evidence_needed or [])[:3]:
        if "agent_steps" in need:
            failed = [s for s in pack.get("agent_steps") or [] if s.get("status") == "failed"]
            cards.append(EvidenceCard(
                claim=f"失败语义步骤 {len(failed)} 条", source="agent_steps.jsonl",
                exact_value=len(failed), pointer="archive:agent_steps.jsonl",
                confidence=0.95 if failed else 0.4,
            ))
    return cards


# ----------------------------------------------------------------------
# 4. Planner / Griller / Writer (optional LLM, deterministic fallback)
# ----------------------------------------------------------------------
async def _plan(runtime, pack: dict, anomalies: List[AuditHypothesis], max_hypotheses: int) -> AuditPlan:
    if not anomalies:
        return AuditPlan(status="no_audit_target", hypotheses=[])
    base = AuditPlan(status="ok", hypotheses=anomalies[:max_hypotheses])
    if runtime is None:
        return base
    prompt = f"""你是只读 GOT 决策审计规划器。基于证据包提出最多 {max_hypotheses} 条可证伪假设。
不得把 LLM 自述当事实证据；没有可证伪目标时输出 no_audit_target。

## 确定性异常候选
{json.dumps([a.model_dump() for a in anomalies[:max_hypotheses]], ensure_ascii=False)}

## 证据清单
{json.dumps(pack.get("manifest"), ensure_ascii=False)}

输出 JSON：{{"status":"ok|no_audit_target","hypotheses":[{{"symptom","mechanism","evidence_needed":[],"falsification","priority","severity","pointer","exact_value"}}]}}
"""
    step = await runtime.call_json(
        task_id="audit.plan", prompt=prompt, output_model=AuditPlan,
        temperature=0.0, max_tokens=1800, max_retries=1, timeout=60.0,
    )
    if step.status != "ok":
        return base
    plan = AuditPlan.model_validate(step.output)
    return plan if plan.hypotheses else base


async def _grill(runtime, plan: AuditPlan, cards: List[EvidenceCard], max_rounds: int) -> GrillResult:
    if runtime is None or not plan.hypotheses:
        return GrillResult(verdicts=[
            GrillVerdict(
                hypothesis_index=i,
                verdict="likely" if any(c.confidence >= 0.9 for c in cards) else "unknown",
                reason="deterministic fallback",
            )
            for i in range(len(plan.hypotheses))
        ])
    prompt = f"""你是独立反证审查员。只看假设与证据卡，不继承规划器解释。
逐假设给出 confirmed / likely / rejected / unknown；证据不足必须 unknown。

## 假设
{json.dumps([h.model_dump() for h in plan.hypotheses], ensure_ascii=False)}

## 证据卡
{json.dumps([c.model_dump() for c in cards], ensure_ascii=False)}

输出 JSON：{{"verdicts":[{{"hypothesis_index":0,"verdict":"confirmed|likely|rejected|unknown","reason":""}}]}}
"""
    for _ in range(max(1, max_rounds)):
        step = await runtime.call_json(
            task_id="audit.grill", prompt=prompt, output_model=GrillResult,
            temperature=0.0, max_tokens=1200, max_retries=1, timeout=60.0,
        )
        if step.status == "ok":
            return GrillResult.model_validate(step.output)
    return GrillResult(verdicts=[])


def _write_findings(
    as_of: date, plan: AuditPlan, grill: GrillResult, cards: List[EvidenceCard],
    previous: Dict[str, dict],
) -> List[AuditFinding]:
    findings: List[AuditFinding] = []
    verdict_map = {v.hypothesis_index: v.verdict for v in grill.verdicts}
    for idx, hyp in enumerate(plan.hypotheses):
        verdict = verdict_map.get(idx, "unknown")
        if verdict not in ("confirmed", "likely"):
            continue
        pointer = hyp.pointer or (cards[idx].pointer if idx < len(cards) else "")
        evidence = [c for c in cards if not pointer or c.pointer == pointer] or cards[:2]
        signature = _signature(hyp.symptom, hyp.mechanism, pointer)
        prior = previous.get(signature)
        first_seen = prior.get("first_seen") if prior else as_of.isoformat()
        recurrence = int(prior.get("recurrence", 0)) + 1 if prior else 1
        findings.append(AuditFinding(
            finding_id=f"AUD-{as_of.strftime('%Y%m%d')}-{len(findings) + 1:02d}",
            date=as_of.isoformat(),
            severity=hyp.severity if hyp.severity in ("P0", "P1", "P2") else "P2",
            status=verdict,
            symptom=hyp.symptom,
            evidence=evidence,
            mechanism=hyp.mechanism,
            counterfactual=hyp.falsification,
            impact="需人工复核后决定是否修复",
            fix_options=["按证据 pointer 定位并修复上游逻辑", "补充确定性回归用例"],
            acceptance="同一 symptom+mechanism+pointer 不再复发",
            confidence=0.9 if verdict == "confirmed" else 0.6,
            first_seen=first_seen,
            recurrence=recurrence,
        ))
    return findings


# ----------------------------------------------------------------------
# 5. Verifier
# ----------------------------------------------------------------------
def _signature(symptom: str, mechanism: str, pointer: str) -> str:
    text = f"{symptom}|{mechanism}|{pointer}".lower()
    text = re.sub(r"\d+", "#", text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _redact(text: str) -> str:
    out = text
    for pattern in SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    out = ACCOUNT_PATTERN.sub("[REDACTED_ACCOUNT]", out)
    return out


def _redact_obj(value: Any) -> Any:
    """Recursive redaction that preserves JSON structure."""
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, dict):
        return {key: _redact_obj(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_obj(item) for item in value]
    return value


def verify_finding(finding: AuditFinding, pack: dict) -> tuple[bool, List[str]]:
    problems: List[str] = []
    if not finding.evidence:
        problems.append("无证据卡")
    for card in finding.evidence:
        pointer = card.pointer or ""
        if pointer.startswith("archive:"):
            name, _, dotted = pointer.partition("#")
            filename = name.split(":", 1)[1]
            if filename == "agent_steps.jsonl":
                if not (pack.get("agent_steps")):
                    problems.append("agent_steps 缺失")
                continue
            data = (pack.get("archive") or {}).get(filename)
            if data is None:
                problems.append(f"归档缺失 {filename}")
                continue
            if dotted:
                value = _dig(data, dotted)
                if value is None:
                    problems.append(f"pointer 不存在 {pointer}")
                elif (
                    card.exact_value is not None
                    and not isinstance(value, (list, dict))
                    and str(card.exact_value) != str(value)
                ):
                    problems.append(f"数值不符 {pointer}: {card.exact_value} != {value}")
        elif pointer.startswith("db:"):
            table = pointer.split(":", 1)[1]
            if table not in (pack.get("db") or {}):
                problems.append(f"DB 表未采集 {table}")
        elif pointer.startswith("file:"):
            raw = pointer[5:]
            path_text, _, line_part = raw.rpartition(":")
            if not path_text:
                path_text, line_part = raw, ""
            path = Path(path_text)
            if not path.exists():
                problems.append(f"文件不存在 {path}")
            elif line_part.isdigit() and int(line_part) > len(path.read_text(encoding="utf-8", errors="ignore").splitlines()):
                problems.append(f"行号越界 {pointer}")

    text = json.dumps(finding.model_dump(), ensure_ascii=False)
    if any(p.search(text) for p in SECRET_PATTERNS) or ACCOUNT_PATTERN.search(text):
        problems.append("敏感信息未脱敏")
    return (not problems), problems


# ----------------------------------------------------------------------
# 6. persistence + orchestrator entry
# ----------------------------------------------------------------------
def _load_previous_findings(out_root: Path) -> Dict[str, dict]:
    previous: Dict[str, dict] = {}
    if not out_root.exists():
        return previous
    for path in sorted(out_root.glob("*/findings.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                sig = _signature(
                    item.get("symptom", ""), item.get("mechanism", ""),
                    (item.get("evidence") or [{}])[0].get("pointer", ""),
                )
                previous[sig] = item
        except Exception:  # noqa: BLE001
            continue
    return previous


def _write_report(out_dir: Path, as_of: date, pack: dict, plan: AuditPlan, findings: List[AuditFinding], run_meta: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# GOT 决策复盘 - {as_of.isoformat()}",
        "",
        f"- 状态: **{run_meta.get('status')}**",
        f"- schema: `{SCHEMA_VERSION}`",
        f"- commit: `{pack.get('code', {}).get('git_commit')}`",
        f"- scoring_formula_version: `{pack.get('code', {}).get('scoring_formula_version')}`",
        f"- 证据口径: `{pack.get('manifest', {}).get('source')}`（旧库缺原始 prompt，非严格 point-in-time）",
        f"- LLM 调用: {run_meta.get('llm_calls', 0)} / 预算 {run_meta.get('max_llm_calls')}",
        "",
        "## 证据清单",
        f"- 已有: {', '.join(pack.get('manifest', {}).get('present') or []) or '无'}",
        f"- 缺失: {', '.join(pack.get('manifest', {}).get('missing') or []) or '无'}",
        "",
        "## 假设与裁决",
    ]
    for i, hyp in enumerate(plan.hypotheses):
        lines.append(f"{i + 1}. [{hyp.severity}] {hyp.symptom}")
        lines.append(f"   - 机制: {hyp.mechanism}")
        lines.append(f"   - 反证: {hyp.falsification}")
        lines.append(f"   - pointer: `{hyp.pointer}`")
    if not plan.hypotheses:
        lines.append("- 无可证伪假设（no_audit_target）")

    lines.extend(["", "## Findings"])
    if not findings:
        lines.append("- 无 confirmed/likely finding")
    for finding in findings:
        lines.append(f"### {finding.finding_id} [{finding.severity}/{finding.status}]")
        lines.append(f"- 症状: {finding.symptom}")
        lines.append(f"- 机制: {finding.mechanism}")
        lines.append(f"- 反事实: {finding.counterfactual}")
        lines.append(f"- 证据: {', '.join(c.pointer for c in finding.evidence)}")
        lines.append(f"- 复发: {finding.recurrence}（first_seen={finding.first_seen}）")
    if run_meta.get("errors"):
        lines.extend(["", "## 未完成原因"])
        lines.extend(f"- {err}" for err in run_meta["errors"])
    (out_dir / "report.md").write_text(_redact("\n".join(lines)) + "\n", encoding="utf-8")


async def _audit_impl(
    as_of: date,
    *,
    base_dir: Path,
    out_root: Path,
    runtime,
    max_hypotheses: int,
    max_grill_rounds: int,
    max_llm_calls: int,
) -> dict:
    from src.got.catalyst_config import (
        GOT_AUDIT_MAX_LLM_CALLS,
        GOT_AUDIT_MAX_GRILL_ROUNDS,
        GOT_AUDIT_MAX_HYPOTHESES,
    )

    pack = collect_evidence_pack(as_of, base_dir=base_dir)
    out_dir = out_root / as_of.strftime("%Y%m%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "evidence_pack.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
    )

    errors: List[str] = []
    if pack["manifest"]["missing"]:
        errors.append(f"归档缺失: {', '.join(pack['manifest']['missing'])}")

    anomalies = detect_anomalies(pack)
    used_calls = len([s for s in runtime.steps if s.task_id.startswith("audit.")]) if runtime else 0
    if used_calls >= max_llm_calls:
        errors.append(f"LLM 预算耗尽（{used_calls}/{max_llm_calls}）")
        plan = AuditPlan(status="no_audit_target", hypotheses=[])
        grill = GrillResult(verdicts=[])
        cards: List[EvidenceCard] = []
    else:
        plan = await _plan(runtime, pack, anomalies, min(max_hypotheses, max_llm_calls - used_calls))
        cards = []
        for hyp in plan.hypotheses[:max_hypotheses]:
            cards.extend(scout_evidence(pack, hyp))
        used_calls = len([s for s in runtime.steps if s.task_id.startswith("audit.")]) if runtime else 0
        if used_calls >= max_llm_calls:
            errors.append(f"LLM 预算耗尽（{used_calls}/{max_llm_calls}）")
            grill = GrillResult(verdicts=[])
        else:
            grill = await _grill(runtime, plan, cards, max_grill_rounds)

    previous = _load_previous_findings(out_root)
    findings = _write_findings(as_of, plan, grill, cards, previous)

    verified: List[AuditFinding] = []
    for finding in findings:
        ok, problems = verify_finding(finding, pack)
        if not ok:
            errors.append(f"{finding.finding_id} 校验失败: {'; '.join(problems)}")
            finding.status = "unknown"
            finding.severity = "observation"
        verified.append(finding)

    run_meta = {
        "status": "incomplete" if errors else "ok",
        "llm_calls": len([s for s in runtime.steps if s.task_id.startswith("audit.")]) if runtime else 0,
        "max_llm_calls": max_llm_calls,
        "hypotheses": len(plan.hypotheses),
        "findings": len(verified),
        "errors": errors,
        "as_of": as_of.isoformat(),
        "generated_at": datetime.now().isoformat(),
        "schema_version": SCHEMA_VERSION,
        "source": pack["manifest"]["source"],
    }
    with (out_dir / "findings.jsonl").open("w", encoding="utf-8") as fh:
        for finding in verified:
            safe = _redact_obj(finding.model_dump())
            fh.write(json.dumps(safe, ensure_ascii=False) + "\n")
    with (out_dir / "audit_runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(run_meta, ensure_ascii=False) + "\n")
    _write_report(out_dir, as_of, pack, plan, verified, run_meta)
    return run_meta


async def run_audit(
    as_of: date,
    *,
    base_dir: Path = Path("data/got_daily"),
    out_root: Path = Path("data/got_audit"),
    runtime=None,
    use_llm: bool = True,
) -> dict:
    """Run the read-only audit pipeline; never raises into the caller."""
    from src.got.catalyst_config import (
        GOT_AUDIT_ENABLED,
        GOT_AUDIT_MAX_HYPOTHESES,
        GOT_AUDIT_MAX_LLM_CALLS,
        GOT_AUDIT_MAX_GRILL_ROUNDS,
        GOT_AUDIT_TIMEOUT_S,
    )

    if str(GOT_AUDIT_ENABLED).lower() in ("0", "false", "off", "no"):
        return {"status": "disabled", "as_of": as_of.isoformat()}

    if runtime is None and use_llm:
        try:
            from src.got.agent_runtime import GOTAgentRuntime

            runtime = GOTAgentRuntime(as_of, archive_root=out_root)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Audit] runtime 构建失败，降级为确定性审计: %s", exc)
            runtime = None

    started = time.time()
    try:
        result = await asyncio.wait_for(
            _audit_impl(
                as_of,
                base_dir=base_dir,
                out_root=out_root,
                runtime=runtime if use_llm else None,
                max_hypotheses=GOT_AUDIT_MAX_HYPOTHESES,
                max_grill_rounds=GOT_AUDIT_MAX_GRILL_ROUNDS,
                max_llm_calls=GOT_AUDIT_MAX_LLM_CALLS,
            ),
            timeout=float(GOT_AUDIT_TIMEOUT_S),
        )
    except asyncio.TimeoutError:
        result = {
            "status": "incomplete", "as_of": as_of.isoformat(),
            "errors": [f"审计超时 > {GOT_AUDIT_TIMEOUT_S}s"],
        }
        out_dir = out_root / as_of.strftime("%Y%m%d")
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / "audit_runs.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(result, ensure_ascii=False) + "\n")
        (out_dir / "report.md").write_text(
            f"# GOT 决策复盘 - {as_of.isoformat()}\n\n- 状态: **incomplete**\n"
            f"- 原因: 审计超时 > {GOT_AUDIT_TIMEOUT_S}s\n",
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001 — audit must never break --got
        logger.warning("[Audit] 审计失败（不阻断主流程）: %s", exc)
        result = {
            "status": "incomplete", "as_of": as_of.isoformat(),
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    result["elapsed_s"] = round(time.time() - started, 2)
    if runtime is not None:
        try:
            runtime.flush()
        except Exception:  # noqa: BLE001
            pass
    return result


def run_audit_sync(as_of: date, **kwargs) -> dict:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_audit(as_of, **kwargs))
    raise RuntimeError("run_audit is async; await it instead of run_audit_sync")
