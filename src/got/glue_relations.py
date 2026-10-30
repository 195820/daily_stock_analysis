# -*- coding: utf-8 -*-
"""Shared A6 relation consumers.

A6 (`node6.semantic_glue`) is the single semantic owner of event → sector
relations. This module applies those relations to the catalyst calendar exactly
once (affecting ``llm_narrative`` / ``global_map`` events) and exposes the same
in-memory relations to downstream consumers (screener, Phase 2) so they never
re-interpret the same news with another LLM call.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from src.got.board_aliases import normalize_sector_name

logger = logging.getLogger(__name__)

_NARRATIVE_SOURCES = ("llm_narrative", "global_map")


def relation_board_name_map(candidates: list[tuple[str, Any, dict]]) -> dict[str, str]:
    """board_code → canonical sector name (only codes present in candidates)."""
    mapping: dict[str, str] = {}
    for name, detail, _role in candidates or []:
        code = getattr(detail, "code", "") if detail is not None else ""
        if code:
            mapping[str(code)] = normalize_sector_name(name)
    return mapping


def relation_signal_map(
    relations: list[Any],
    candidates: Optional[list[tuple[str, Any, dict]]] = None,
) -> dict[str, dict]:
    """Strongest relation per sector name.

    Returns ``{sector_name: {"signal": float, "direction": str, "strength": float,
    "confidence": float, "event_id": str}}``. ``signal = direction_sign × strength``
    (binary/unknown direction contributes 0 but is still reported).
    """
    code_to_name = relation_board_name_map(candidates or [])
    best: dict[str, dict] = {}
    for rel in relations or []:
        code = str(getattr(rel, "board_code", "") or "")
        name = code_to_name.get(code)
        if not name:
            continue
        direction = str(getattr(rel, "direction", "positive") or "positive")
        sign = 1.0 if direction == "positive" else (-1.0 if direction == "negative" else 0.0)
        strength = float(getattr(rel, "strength", 0.0) or 0.0)
        signal = sign * strength
        current = best.get(name)
        if current is None or abs(signal) > abs(current["signal"]):
            best[name] = {
                "signal": signal,
                "direction": direction,
                "strength": strength,
                "confidence": float(getattr(rel, "confidence", 0.0) or 0.0),
                "event_id": str(getattr(rel, "event_id", "") or ""),
            }
    return best


def apply_glue_relations(
    news: Any,
    glue: Any,
    candidates: list[tuple[str, Any, dict]],
    *,
    replay: bool = False,
) -> dict:
    """Fan A6 relations back into ``llm_narrative`` / ``global_map`` events.

    - Only those two sources are rewritten; ``macro_rule`` keeps deterministic fanout.
    - No relation → keep existing ``affected_sectors`` (or ``[sector]``); never turn a
      missed relation into "no impact".
    - ``direction`` is untouched (a negative event must stay negative).
    - Production UPSERTs ``catalyst_events.affected_sectors`` for the same ``event_id``;
      replay writes nothing. DB failures propagate in production (fail-fast).
    """
    stats = {
        "updated": 0,
        "unmapped_relations": 0,
        "kept_existing": 0,
        "db_persisted": 0,
        "db_missing_row": 0,
    }
    calendar = getattr(news, "catalyst_calendar", None)
    events = list(getattr(calendar, "events", None) or [])
    if not events or glue is None:
        return stats

    code_to_name = relation_board_name_map(candidates)
    by_event: dict[str, list] = {}
    for rel in getattr(glue, "relations", None) or []:
        code = str(getattr(rel, "board_code", "") or "")
        if code not in code_to_name:
            stats["unmapped_relations"] += 1
            continue
        by_event.setdefault(str(getattr(rel, "event_id", "") or ""), []).append(rel)

    changed: list[Any] = []
    for event in events:
        if str(getattr(event, "source", "")) not in _NARRATIVE_SOURCES:
            continue
        rels = by_event.get(str(getattr(event, "event_id", "") or ""), [])
        sectors: list[str] = []
        for rel in rels:
            name = code_to_name.get(str(getattr(rel, "board_code", "") or ""))
            if name and name not in sectors:
                sectors.append(name)
        if not sectors:
            # 漏判不是"无影响"：保留原扇出（或名义板块）
            if not getattr(event, "affected_sectors", None):
                event.affected_sectors = [event.sector]
                changed.append(event)
            stats["kept_existing"] += 1
            continue
        event.affected_sectors = sectors
        changed.append(event)
        stats["updated"] += 1

    if replay or not changed:
        return stats

    from src.storage import CatalystEventRecord, DatabaseManager

    db = DatabaseManager()
    with db.get_session() as session:
        for event in changed:
            row = (
                session.query(CatalystEventRecord)
                .filter(CatalystEventRecord.event_id == event.event_id)
                .first()
            )
            if row is None:
                stats["db_missing_row"] += 1
                continue
            row.affected_sectors = (
                json.dumps(event.affected_sectors, ensure_ascii=False)
                if event.affected_sectors else None
            )
            stats["db_persisted"] += 1
        session.commit()
    logger.info(
        "[A6 relations] 扇出回填: updated=%d, unmapped=%d, kept_existing=%d, db=%d",
        stats["updated"], stats["unmapped_relations"], stats["kept_existing"],
        stats["db_persisted"],
    )
    return stats
