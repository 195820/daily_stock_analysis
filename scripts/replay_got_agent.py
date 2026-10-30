#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GOT agent-runtime replay harness (baseline vs connected).

Two variants over archived contexts:
- baseline: Node 6 replay with current rule wiring, no narrative injection.
- connected: same + A6 shared relations/narrative context (and mock identity/
  attribution when the fixture provides them).

Provenance is explicitly ``archive_approximation``: old DBs lack original
prompts/snippets/identities, so this is NOT a strict point-in-time backtest.

Examples:
    python scripts/replay_got_agent.py --dates 20261029 --variant connected \\
        --mock-llm tests/fixtures/got_agent_20261029.json --out /tmp/replay
    python scripts/replay_got_agent.py --variant connected --replay-llm --out /tmp/replay
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.got.archive import list_archive_dirs, load_daily_contexts  # noqa: E402
from src.got.agent_runtime import GOTAgentRuntime  # noqa: E402
from src.got.narrative_agents import (  # noqa: E402
    AttributionItem,
    IdentityItem,
    NarrativeContext,
)


class _TaskFixtureMap(dict):
    """Lookup by (task_id, prompt_sha256), then by task_id, then '*'."""

    def get(self, key, default=None):
        if isinstance(key, tuple) and key in self:
            return dict.__getitem__(self, key)
        task_id = key[0] if isinstance(key, tuple) else key
        if task_id in self:
            return dict.__getitem__(self, task_id)
        if "*" in self:
            return dict.__getitem__(self, "*")
        return default


def _parse_dates(raw: Optional[str]) -> List[date]:
    if raw:
        return [datetime.strptime(item.strip(), "%Y%m%d").date() for item in raw.split(",") if item.strip()]
    dates: List[date] = []
    for path in list_archive_dirs(reverse=True):
        try:
            dates.append(datetime.strptime(path.name, "%Y%m%d").date())
        except ValueError:
            continue
        if len(dates) >= 64:
            break
    return dates


def _load_steps_fixtures(date_str: str, base_dir: Path) -> Dict[tuple, str]:
    path = base_dir / date_str / "agent_steps.jsonl"
    fixtures: Dict[tuple, str] = {}
    if not path.exists():
        return fixtures
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            step = json.loads(line)
        except json.JSONDecodeError:
            continue
        if step.get("status") != "ok":
            continue
        task_id = step.get("task_id", "")
        prompt_hash = step.get("prompt_sha256", "")
        raw = step.get("raw_response", "")
        if task_id and raw:
            fixtures[(task_id, prompt_hash)] = raw
    return fixtures


def _mock_fixtures(path: Optional[str]) -> tuple[Dict[str, str], Optional[NarrativeContext]]:
    if not path:
        return {}, None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    responses = payload.get("responses") or {}
    identities = [IdentityItem.model_validate(x) for x in payload.get("identities") or []]
    attributions = [AttributionItem.model_validate(x) for x in payload.get("attributions") or []]
    ctx = None
    if identities or attributions:
        ctx = NarrativeContext(
            status="ok", as_of=str(payload.get("as_of", "")),
            identities=identities, attributions=attributions,
        )
    return responses, ctx


def _sector_rows(unified) -> List[dict]:
    return [
        {
            "sector": s.sector_name,
            "classification": s.classification,
            "role": getattr(s, "sector_role", ""),
            "composite": round(float(s.composite_score or 0), 2),
        }
        for s in (getattr(unified, "confirmed_sectors", None) or [])
    ]


def _relation_metrics(glue) -> dict:
    relations = getattr(glue, "relations", None) or []
    with_evidence = sum(1 for r in relations if getattr(r, "evidence_ids", None))
    return {
        "glue_status": getattr(glue, "status", None),
        "glue_degraded": bool(getattr(glue, "degraded", False)),
        "relations": len(relations),
        "relations_with_evidence": with_evidence,
        "closure_rate": round(with_evidence / len(relations), 3) if relations else None,
        "strengths": len(getattr(glue, "strengths", None) or []),
        "families": len(getattr(glue, "families", None) or []),
    }


def _bootstrap(values: List[float], label: str) -> Dict[str, Any]:
    clean = [float(v) for v in values if v is not None]
    if len(clean) <= 2:
        return {"metric": label, "n": len(clean), "note": "n<=2，不计算统计"}
    from src.got.backtest_discipline import moving_block_bootstrap_ci

    mean = sum(clean) / len(clean)
    low, high = moving_block_bootstrap_ci(clean)
    return {"metric": label, "n": len(clean), "mean": round(mean, 4), "ci95": [round(low, 4), round(high, 4)]}


async def _run_date(
    day: date, *, variant: str, out_dir: Path, steps_fixtures: Dict[tuple, str],
    mock_responses: Dict[str, str], mock_ctx: Optional[NarrativeContext],
    base_dir: Path,
) -> dict:
    import src.got.nodes.aggregator as agg_mod

    contexts = load_daily_contexts(day, base_dir=base_dir)
    required = ("market", "sector", "capital", "news", "sentiment")
    missing = [k for k in required if contexts.get(k) is None]
    if missing:
        return {"date": day.isoformat(), "status": "missing_contexts", "missing": missing}

    old_root = agg_mod.GLUE_ARCHIVE_ROOT
    old_replay_flag = agg_mod.AGENT_SEMANTIC_GLUE_IN_REPLAY
    agg_mod.GLUE_ARCHIVE_ROOT = out_dir
    agg_mod.AGENT_SEMANTIC_GLUE_IN_REPLAY = True
    try:
        fixtures = _TaskFixtureMap()
        fixtures.update(steps_fixtures)
        fixtures.update(mock_responses)
        runtime = GOTAgentRuntime(
            day, replay=True, archive_root=out_dir, response_fixtures=fixtures,
        )
        node = agg_mod.AggregatorNode(use_mock=False, got_runtime=runtime)
        unified = await node.run(
            market=contexts["market"],
            sector=contexts["sector"],
            capital=contexts["capital"],
            news=contexts["news"],
            sentiment=contexts["sentiment"],
            replay_mode=True,
            run_date=day,
            narrative_context=mock_ctx if variant == "connected" else None,
        )
        glue = getattr(node, "_last_glue", None)
        runtime.flush()

        # 差异表：本次 replay vs 当日生产归档（SectorIntel 口径）
        diff = {"added": [], "removed": [], "changed": []}
        archived = None
        archived_path = Path("data/got_daily") / day.strftime("%Y%m%d") / "unified.json"
        if archived_path.exists():
            try:
                archived = json.loads(archived_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                archived = None
        if archived is not None:
            old = {
                s.get("sector_name"): {
                    "classification": s.get("classification"),
                    "composite": round(float(s.get("composite_score") or 0), 2),
                }
                for s in archived.get("confirmed_sectors") or []
            }
            new = {
                s["sector"]: {"classification": s["classification"], "composite": s["composite"]}
                for s in _sector_rows(unified)
            }
            diff["added"] = sorted(set(new) - set(old))
            diff["removed"] = sorted(set(old) - set(new))
            for name in sorted(set(old) & set(new)):
                if old[name] != new[name]:
                    diff["changed"].append({"sector": name, "before": old[name], "after": new[name]})
        else:
            diff["missing_reason"] = "unified.json 不存在"

        return {
            "date": day.isoformat(),
            "status": "ok",
            "variant": variant,
            "relations": _relation_metrics(glue) if glue is not None else {},
            "sectors": _sector_rows(unified),
            "diff_vs_archive": diff,
            "identities": (
                [i.model_dump() for i in (mock_ctx.identities if mock_ctx else [])]
                if variant == "connected" else []
            ),
            "attributions": (
                [a.model_dump() for a in (mock_ctx.attributions if mock_ctx else [])]
                if variant == "connected" else []
            ),
            "steps": {
                "count": len(runtime.steps),
                "failed": sum(1 for s in runtime.steps if s.status == "failed"),
                "missing_fixture": sum(1 for s in runtime.steps if s.error == "missing_fixture"),
                "tokens": sum(s.tokens.get("total_tokens", 0) for s in runtime.steps),
                "latency_ms": sum(s.latency_ms for s in runtime.steps),
            },
        }
    finally:
        agg_mod.GLUE_ARCHIVE_ROOT = old_root
        agg_mod.AGENT_SEMANTIC_GLUE_IN_REPLAY = old_replay_flag


async def main_async(args) -> int:
    out_dir = Path(args.out or "data/got_daily/_replay/agent_runtime_eval")
    out_dir.mkdir(parents=True, exist_ok=True)
    dates = _parse_dates(args.dates)
    if not dates:
        print("no archive dates found")
        return 1

    mock_responses, mock_ctx = _mock_fixtures(args.mock_llm)
    rows: List[dict] = []
    for day in dates:
        steps_fixtures = _load_steps_fixtures(day.strftime("%Y%m%d"), Path("data/got_daily")) if args.replay_llm else {}
        try:
            row = await _run_date(
                day, variant=args.variant, out_dir=out_dir,
                steps_fixtures=steps_fixtures, mock_responses=mock_responses,
                mock_ctx=mock_ctx, base_dir=Path("data/got_daily"),
            )
        except Exception as exc:  # noqa: BLE001 — report per-date failure, keep going
            row = {"date": day.isoformat(), "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        rows.append(row)

    from src.got.catalyst_config import SCORING_FORMULA_VERSION

    summary = {
        "provenance": "archive_approximation",
        "note": "旧库缺原始 prompt/完整 snippet/当时 identity，非严格 point-in-time；不作收益提升结论",
        "variant": args.variant,
        "scoring_formula_version": SCORING_FORMULA_VERSION,
        "replay_llm": bool(args.replay_llm),
        "mock_llm": args.mock_llm,
        "dates": [r["date"] for r in rows],
        "n_dates": len(rows),
        "n_ok": sum(1 for r in rows if r.get("status") == "ok"),
        "missing_reasons": sorted({
            r.get("status", "unknown") for r in rows if r.get("status") != "ok"
        }),
        "rows": rows,
    }
    (out_dir / "replay_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
    )
    print(json.dumps({
        "provenance": summary["provenance"],
        "variant": summary["variant"],
        "n_dates": summary["n_dates"],
        "n_ok": summary["n_ok"],
        "missing_reasons": summary["missing_reasons"],
        "out": str(out_dir / "replay_summary.json"),
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay GOT Node 6 with agent runtime")
    parser.add_argument("--dates", default=None, help="YYYYMMDD[,..]；缺省枚举最多 64 个归档日")
    parser.add_argument("--variant", choices=["baseline", "connected"], default="connected")
    parser.add_argument("--replay-llm", action="store_true", help="只从 agent_steps.jsonl 重放响应，不联网")
    parser.add_argument("--mock-llm", default=None, help="确定性 fixture JSON")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
