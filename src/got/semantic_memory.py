# -*- coding: utf-8 -*-
"""Incremental, evaluable semantic memory for GOT.

Design (2026-10-30 consolidation):
- Reads are always bounded by ``as_of`` (no future information).
- Replay never writes production tables.
- Identity/thesis/event lifecycles come from existing tables; this module only
  adds the narrow identity / attribution / realization tables.
- Realizations are written only after the window has actually closed and a
  verifiable theme proxy exists; otherwise the row is explicitly ``unresolved``
  (never substitute stock returns or identity confidence for theme returns).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

BENCHMARK_SYMBOL = "000852"  # 中证1000


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


class SemanticMemory:
    """as_of-bounded semantic memory reader/writer."""

    def __init__(self, as_of: date, *, replay: bool = False, agent_run_id: Optional[str] = None):
        self.as_of = as_of
        self.replay = replay
        self.agent_run_id = agent_run_id

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def load_memory(
        self,
        symbols: Iterable[str] = (),
        themes: Iterable[str] = (),
        *,
        event_days: int = 7,
    ) -> Dict[str, Any]:
        """Return grouped memory entries (identity / thesis / events / realizations)."""
        symbols = [s for s in dict.fromkeys(symbols) if s]
        themes = [t for t in dict.fromkeys(themes) if t]
        out: Dict[str, Any] = {
            "as_of": self.as_of.isoformat(),
            "identities": [],
            "thesis_changes": [],
            "events": [],
            "realizations": [],
        }
        try:
            from src.storage import (
                CatalystRealizationRecord,
                HoldingThesisRecord,
                Node4EventRecord,
                NarrativeRealizationRecord,
                StockThemeIdentityRecord,
                DatabaseManager,
            )

            db = DatabaseManager()
            with db.get_session() as session:
                if symbols:
                    rows = (
                        session.query(StockThemeIdentityRecord)
                        .filter(
                            StockThemeIdentityRecord.symbol.in_(symbols),
                            StockThemeIdentityRecord.as_of <= self.as_of,
                        )
                        .order_by(StockThemeIdentityRecord.as_of.desc())
                        .all()
                    )
                    latest: Dict[tuple[str, str], Any] = {}
                    for row in rows:
                        latest.setdefault((row.symbol, row.theme), row)
                    out["identities"] = [
                        {
                            "symbol": r.symbol,
                            "theme": r.theme,
                            "layer": r.layer,
                            "confidence": r.confidence,
                            "price_beta": r.price_beta,
                            "novelty": bool(r.novelty),
                            "evidence_ids": _loads(r.evidence_json),
                            "identity_change": r.identity_change,
                            "first_seen": _parse_date(r.first_seen).isoformat() if _parse_date(r.first_seen) else None,
                            "last_seen": _parse_date(r.last_seen).isoformat() if _parse_date(r.last_seen) else None,
                        }
                        for r in latest.values()
                    ]
                    thesis_rows = (
                        session.query(HoldingThesisRecord)
                        .filter(HoldingThesisRecord.symbol.in_(symbols))
                        .order_by(HoldingThesisRecord.entry_date.desc())
                        .all()
                    )
                    out["thesis_changes"] = [
                        {
                            "symbol": r.symbol,
                            "sector": r.sector_name,
                            "family": r.family_name,
                            "entry_date": _parse_date(r.entry_date).isoformat() if _parse_date(r.entry_date) else None,
                            "is_open": bool(getattr(r, "is_open", False)),
                            "lifecycle_state": getattr(r, "lifecycle_state", None),
                        }
                        for r in thesis_rows[:50]
                    ]

                cutoff = self.as_of - timedelta(days=event_days)
                event_rows = (
                    session.query(Node4EventRecord)
                    .filter(
                        Node4EventRecord.last_seen >= cutoff,
                        Node4EventRecord.last_seen <= self.as_of,
                    )
                    .order_by(Node4EventRecord.last_seen.desc())
                    .all()
                )
                out["events"] = [
                    {
                        "event_id": r.event_id,
                        "title": r.title,
                        "lifecycle_state": r.lifecycle_state,
                        "mention_count": r.mention_count,
                        "first_seen": _parse_date(r.first_seen).isoformat() if _parse_date(r.first_seen) else None,
                        "last_seen": _parse_date(r.last_seen).isoformat() if _parse_date(r.last_seen) else None,
                    }
                    for r in event_rows[:80]
                ]

                realization_query = session.query(NarrativeRealizationRecord).filter(
                    NarrativeRealizationRecord.as_of <= self.as_of,
                )
                if themes:
                    realization_query = realization_query.filter(
                        NarrativeRealizationRecord.theme.in_(themes),
                    )
                out["realizations"] = [
                    {
                        "source_kind": r.source_kind,
                        "source_id": r.source_id,
                        "as_of": _parse_date(r.as_of).isoformat() if _parse_date(r.as_of) else None,
                        "horizon_days": r.horizon_days,
                        "theme": r.theme,
                        "theme_return": r.theme_return,
                        "benchmark_return": r.benchmark_return,
                        "excess_return": r.excess_return,
                        "direction_correct": r.direction_correct,
                    }
                    for r in realization_query.order_by(NarrativeRealizationRecord.as_of.desc()).all()[:100]
                ]
                catalyst_rows = (
                    session.query(CatalystRealizationRecord)
                    .filter(CatalystRealizationRecord.realization_date <= self.as_of)
                    .order_by(CatalystRealizationRecord.realization_date.desc())
                    .all()[:50]
                )
                out["completed_catalysts"] = [
                    {
                        "event_id": r.event_id,
                        "sector": r.sector,
                        "event_date": _parse_date(r.event_date).isoformat() if _parse_date(r.event_date) else None,
                        "actual_return": r.actual_return,
                        "drift_direction_correct": r.drift_direction_correct,
                    }
                    for r in catalyst_rows
                ]
        except Exception as exc:  # noqa: BLE001 — memory read failure degrades to empty
            logger.warning("[SemanticMemory] load_memory 失败（返回空记忆）: %s", exc)
        return out

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------
    def record_identities(self, identities: Iterable[Any]) -> int:
        """Persist per-symbol identities with deterministic identity_change diff."""
        if self.replay:
            return 0
        from src.storage import DatabaseManager, StockThemeIdentityRecord

        rows = [
            r for r in (identities or [])
            if getattr(r, "theme", "") and not getattr(r, "abstain", False)
        ]
        if not rows:
            return 0
        written = 0
        db = DatabaseManager()
        with db.get_session() as session:
            prior_rows = (
                session.query(StockThemeIdentityRecord)
                .filter(
                    StockThemeIdentityRecord.symbol.in_([r.symbol for r in rows]),
                    StockThemeIdentityRecord.as_of < self.as_of,
                )
                .order_by(StockThemeIdentityRecord.as_of.desc())
                .all()
            )
            prior: Dict[tuple[str, str], Any] = {}
            for row in prior_rows:
                prior.setdefault((row.symbol, row.theme), row)

            for item in rows:
                key = (item.symbol, item.theme)
                previous = prior.get(key)
                if previous is None:
                    change = "new"
                    first_seen = self.as_of
                else:
                    same = (
                        (previous.layer or "other") == (item.layer or "other")
                        and abs(float(previous.confidence or 0.0) - float(item.confidence or 0.0)) < 0.05
                    )
                    change = "unchanged" if same else "changed"
                    first_seen = _parse_date(previous.first_seen) or self.as_of

                existing = (
                    session.query(StockThemeIdentityRecord)
                    .filter_by(symbol=item.symbol, theme=item.theme, as_of=self.as_of)
                    .first()
                )
                if existing is None:
                    existing = StockThemeIdentityRecord(
                        symbol=item.symbol, theme=item.theme, as_of=self.as_of,
                    )
                    session.add(existing)
                existing.layer = item.layer
                existing.confidence = item.confidence
                existing.price_beta = item.price_beta
                existing.novelty = bool(item.novelty)
                existing.evidence_json = json.dumps(list(item.evidence_ids or []), ensure_ascii=False)
                existing.first_seen = first_seen
                existing.last_seen = self.as_of
                existing.identity_change = change
                existing.agent_run_id = self.agent_run_id
                written += 1
            session.commit()
        return written

    def record_attributions(self, attributions: Iterable[Any]) -> int:
        if self.replay:
            return 0
        from src.storage import DatabaseManager, MoveAttributionRecord

        rows = list(attributions or [])
        if not rows:
            return 0
        written = 0
        db = DatabaseManager()
        with db.get_session() as session:
            for item in rows:
                existing = (
                    session.query(MoveAttributionRecord)
                    .filter_by(symbol=item.symbol, date=self.as_of)
                    .first()
                )
                if existing is None:
                    existing = MoveAttributionRecord(symbol=item.symbol, date=self.as_of)
                    session.add(existing)
                existing.return_pct = item.return_pct
                existing.bucket = item.bucket
                existing.theme = item.theme
                existing.confidence = item.confidence
                existing.evidence_json = json.dumps(list(item.evidence_ids or []), ensure_ascii=False)
                existing.residual = item.residual
                existing.agent_run_id = self.agent_run_id
                written += 1
            session.commit()
        return written

    def record_realizations(self, identities: Optional[list[dict]] = None) -> Dict[str, int]:
        """Evaluate closed windows; unresolved instead of inventing theme returns."""
        stats = {"written": 0, "unresolved": 0, "skipped": 0}
        if self.replay:
            return stats
        from src.got.catalyst_realization import CatalystRealizationTracker
        from src.storage import DatabaseManager, NarrativeRealizationRecord, StockThemeIdentityRecord

        tracker = CatalystRealizationTracker()
        db = DatabaseManager()
        with db.get_session() as session:
            rows = (
                session.query(StockThemeIdentityRecord)
                .filter(
                    StockThemeIdentityRecord.as_of <= self.as_of,
                    StockThemeIdentityRecord.as_of >= self.as_of - timedelta(days=30),
                )
                .order_by(StockThemeIdentityRecord.as_of.desc())
                .all()
            )
            evaluated: set[tuple[str, str, date, int]] = set()
            for row in rows:
                source_id = f"identity:{row.symbol}:{row.theme}"
                base_date = _parse_date(row.as_of)
                if base_date is None:
                    stats["skipped"] += 1
                    continue
                window = _theme_window(tracker, row.theme, base_date)
                for horizon in (1, 3, 5):
                    key = (source_id, row.theme, base_date, horizon)
                    if key in evaluated:
                        continue
                    evaluated.add(key)
                    existing = (
                        session.query(NarrativeRealizationRecord)
                        .filter_by(
                            source_kind="identity", source_id=source_id,
                            as_of=base_date, horizon_days=horizon,
                        )
                        .first()
                    )
                    if existing is not None:
                        stats["skipped"] += 1
                        continue
                    target_date = _forward_trading_day(base_date, horizon)
                    if target_date is None or target_date > self.as_of:
                        stats["skipped"] += 1
                        continue
                    theme_return = _theme_value(window, horizon)
                    bench_return = _benchmark_return(base_date, horizon, target_date)
                    record = NarrativeRealizationRecord(
                        source_kind="identity",
                        source_id=source_id,
                        as_of=base_date,
                        horizon_days=horizon,
                        symbol=row.symbol,
                        theme=row.theme,
                        entry_date=base_date,
                        evaluation_date=target_date,
                        theme_return=theme_return,
                        benchmark_return=bench_return,
                        excess_return=(
                            round(theme_return - bench_return, 4)
                            if theme_return is not None and bench_return is not None else None
                        ),
                        direction_correct=(
                            theme_return - bench_return > 0
                            if theme_return is not None and bench_return is not None else None
                        ),
                        agent_run_id=self.agent_run_id,
                    )
                    session.add(record)
                    if theme_return is None:
                        stats["unresolved"] += 1
                    else:
                        stats["written"] += 1
            session.commit()
        return stats

    # ------------------------------------------------------------------
    # run card
    # ------------------------------------------------------------------
    def build_run_card(self) -> Dict[str, Any]:
        from src.got.backtest_discipline import build_run_card

        return build_run_card(
            sample_window=f"as_of:{self.as_of.isoformat()}",
            benchmark=f"中证1000({BENCHMARK_SYMBOL})",
            adjust_mode="qfq",
            missing_value_policy="no_fill",
            point_in_time="close_after_1500",
            extra={"as_of": self.as_of.isoformat(), "agent_run_id": self.agent_run_id},
        )


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _loads(text: Any) -> List[Any]:
    if not text:
        return []
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _forward_trading_day(start: date, horizon: int) -> Optional[date]:
    try:
        from scripts.backtest_lane_budget import add_trading_days

        target = add_trading_days(datetime.combine(start, datetime.min.time()), horizon)
        return target.date() if target is not None else None
    except Exception:  # noqa: BLE001
        return None


def _theme_window(tracker: Any, theme: str, base_date: date) -> Dict[str, Any]:
    """Fetch the theme proxy window once per (theme, base_date)."""
    try:
        return tracker._compute_multi_window_returns(theme, base_date) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SemanticMemory] 主题行情不可用 %s: %s", theme, exc)
        return {}


def _theme_value(window: Dict[str, Any], horizon: int) -> Optional[float]:
    value = (window or {}).get(f"day_{horizon}")
    return float(value) if value is not None else None


def _benchmark_return(base_date: date, horizon: int, target_date: date) -> Optional[float]:
    try:
        from data_provider.base import DataFetcherManager

        df = DataFetcherManager().get_daily_data(BENCHMARK_SYMBOL, days=80)
        if df is None or df.empty or "close" not in df.columns:
            return None
        frame = df.copy()
        if "date" in frame.columns:
            frame["date"] = frame["date"].astype(str).str[:10]
            frame = frame.sort_values("date")
        base_rows = frame[frame["date"] >= base_date.isoformat()] if "date" in frame.columns else frame
        target_rows = frame[frame["date"] >= target_date.isoformat()] if "date" in frame.columns else frame
        if base_rows.empty or target_rows.empty:
            return None
        base_close = float(base_rows.iloc[0]["close"])
        target_close = float(target_rows.iloc[0]["close"])
        if base_close <= 0:
            return None
        return round((target_close / base_close - 1) * 100, 4)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[SemanticMemory] 基准收益不可用: %s", exc)
        return None
