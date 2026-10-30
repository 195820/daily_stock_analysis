# -*- coding: utf-8 -*-
"""Two bounded semantic agents (identity + move attribution).

They are deliberately narrow: each does one fixed structured call over a small
batch of symbols, cannot change sector_role/classification/ambush gates/position
sizing, and records every claim against run evidence IDs. No autonomous planner,
no free-text hand-off between roles.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field, RootModel

from src.got.agent_runtime import EvidenceRef, GOTAgentRuntime

logger = logging.getLogger(__name__)

IDENTITY_BATCH_SIZE = 10
ATTRIBUTION_BATCH_SIZE = 10
IDENTITY_MAX_PER_SYMBOL = 2
VALID_LAYERS = ("software_model", "hardware", "supply_chain", "other")
VALID_BUCKETS = ("sector_beta", "theme_alpha", "idiosyncratic", "unexplained")


class IdentityItem(BaseModel):
    symbol: str
    theme: str = ""
    layer: str = Field(default="other")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    price_beta: Optional[float] = None
    novelty: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    abstain: bool = False
    identity_change: str = Field(default="unchanged")


class IdentityBatch(RootModel[list[IdentityItem]]):
    """Root model for a batch of identity claims."""


class AttributionItem(BaseModel):
    symbol: str
    date: str = ""
    return_pct: Optional[float] = None
    bucket: str = Field(default="unexplained")
    theme: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    residual: Optional[float] = None


class AttributionBatch(RootModel[list[AttributionItem]]):
    """Root model for a batch of move attributions."""


class NarrativeContext(BaseModel):
    status: str = Field(default="ok")
    as_of: str = ""
    identities: list[IdentityItem] = Field(default_factory=list)
    attributions: list[AttributionItem] = Field(default_factory=list)


def persist_narrative_context(ctx: NarrativeContext, as_of: date, *, base_dir: Optional[str] = None) -> None:
    """Atomically write ``narrative_context.json`` for the business day."""
    import os
    from pathlib import Path

    try:
        day_dir = Path(base_dir or "data/got_daily") / as_of.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / "narrative_context.json"
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(ctx.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001 — artifact write never blocks decisions
        logger.warning("[Narrative] narrative_context.json 落盘失败（已忽略）: %s", exc)


def _symbols_from_holdings(holdings: Any) -> List[Dict[str, str]]:
    """Accept HoldingsContext / positions list / portfolio snapshot."""
    out: List[Dict[str, str]] = []
    if holdings is None:
        return out
    if hasattr(holdings, "positions"):
        for pos in getattr(holdings, "positions") or []:
            symbol = str(getattr(pos, "symbol", "") or "")
            if symbol:
                out.append({
                    "symbol": symbol,
                    "name": str(getattr(pos, "name", "") or ""),
                    "sector": str(getattr(pos, "sector_name", "") or ""),
                })
        return out
    if isinstance(holdings, dict):
        positions = holdings.get("positions")
        if positions is None and "accounts" in holdings:
            from src.got.nodes.portfolio_decision import PortfolioDecisionNode

            positions = PortfolioDecisionNode._all_positions(holdings)
        for pos in positions or []:
            symbol = str(pos.get("symbol") or pos.get("code") or "")
            if symbol:
                out.append({
                    "symbol": symbol,
                    "name": str(pos.get("name") or ""),
                    "sector": str(pos.get("sector_name") or pos.get("sector") or ""),
                })
        return out
    for pos in holdings or []:
        if isinstance(pos, dict):
            symbol = str(pos.get("symbol") or pos.get("code") or "")
            if symbol:
                out.append({
                    "symbol": symbol,
                    "name": str(pos.get("name") or ""),
                    "sector": str(pos.get("sector_name") or pos.get("sector") or ""),
                })
    return out


def _set_future(future: asyncio.Future, value: Any) -> None:
    if not future.done():
        future.set_result(value)


async def _collect_identity_symbols(
    holdings: Any, sector_ctx: Any, max_symbols: int,
) -> tuple[List[Dict[str, Any]], str]:
    """Holdings + mainline/ambush constituents, deduped by symbol.

    Constituent lookup is best-effort and time-boxed: a slow THS query must not
    block the GOT decision chain.
    """
    from src.got.catalyst_config import (
        GOT_NARRATIVE_CONSTITUENTS_ENABLED,
        GOT_NARRATIVE_CONSTITUENTS_TIMEOUT_S,
    )

    rows = _symbols_from_holdings(holdings)
    note = ""
    if sector_ctx is not None and GOT_NARRATIVE_CONSTITUENTS_ENABLED:
        try:
            from src.got.nodes.portfolio_decision import PortfolioDecisionNode

            helper = PortfolioDecisionNode(use_mock=True)
            helper._stock_sector_map = {}
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()

            def _lookup() -> None:
                try:
                    helper._build_stock_sector_map(sector_ctx)
                    loop.call_soon_threadsafe(_set_future, future, True)
                except Exception as exc:  # noqa: BLE001
                    loop.call_soon_threadsafe(_set_future, future, exc)

            threading.Thread(target=_lookup, daemon=True).start()
            outcome = await asyncio.wait_for(future, timeout=GOT_NARRATIVE_CONSTITUENTS_TIMEOUT_S)
            if isinstance(outcome, Exception):
                raise outcome
            for symbol, memberships in (helper._stock_sector_map or {}).items():
                if not symbol:
                    continue
                sectors = [m.get("sector_name", "") for m in memberships if m.get("sector_name")]
                rows.append({
                    "symbol": symbol,
                    "name": "",
                    "sector": sectors[0] if sectors else "",
                    "memberships": sectors,
                })
        except asyncio.TimeoutError:
            note = f"constituents_timeout>{GOT_NARRATIVE_CONSTITUENTS_TIMEOUT_S}s"
            logger.warning("[Narrative] 成分股查询超时，仅用持仓: %s", note)
        except Exception as exc:  # noqa: BLE001 — constituent lookup is best-effort
            note = f"constituents_unavailable: {exc}"
            logger.warning("[Narrative] 成分股映射不可用（仅用持仓）: %s", exc)

    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for row in rows:
        symbol = row.get("symbol", "")
        if symbol and symbol not in seen:
            seen.add(symbol)
            deduped.append(row)
    return deduped[:max_symbols], note


def _previous_identity_map(previous_identities: Any) -> Dict[str, List[dict]]:
    items = []
    if isinstance(previous_identities, NarrativeContext):
        items = [i.model_dump() for i in previous_identities.identities]
    elif isinstance(previous_identities, dict):
        items = list(previous_identities.get("identities") or [])
    elif isinstance(previous_identities, list):
        items = list(previous_identities)
    out: Dict[str, List[dict]] = {}
    for item in items:
        if not isinstance(item, dict):
            item = item.model_dump() if hasattr(item, "model_dump") else {}
        symbol = str(item.get("symbol", "") or "")
        if symbol:
            out.setdefault(symbol, []).append(item)
    return out


def _deterministic_change(previous: List[dict], theme: str, layer: str, confidence: float) -> str:
    if not previous:
        return "new"
    for old in previous:
        if old.get("theme") == theme and (old.get("layer") or "other") == layer:
            return "unchanged" if abs(float(old.get("confidence", 0.0) or 0.0) - confidence) < 0.1 else "changed"
    return "changed"


async def run_identity_agent(
    holdings: Any,
    sector_ctx: Any,
    news_ctx: Any,
    previous_identities: Any,
    runtime: Optional[GOTAgentRuntime],
    as_of: date,
) -> NarrativeContext:
    """One bounded identity pass; A6 may read it as evidence only."""
    from src.got.catalyst_config import (
        GOT_NARRATIVE_AGENTS_ENABLED,
        GOT_NARRATIVE_MAX_SYMBOLS,
        GOT_NARRATIVE_TIMEOUT_S,
    )

    if not GOT_NARRATIVE_AGENTS_ENABLED:
        return NarrativeContext(status="disabled", as_of=as_of.isoformat())

    if runtime is None:
        return NarrativeContext(status="degraded", as_of=as_of.isoformat())

    symbols, note = await _collect_identity_symbols(holdings, sector_ctx, GOT_NARRATIVE_MAX_SYMBOLS)
    if not symbols:
        return NarrativeContext(status="empty", as_of=as_of.isoformat())

    prev_map = _previous_identity_map(previous_identities)

    # ── evidence ──
    refs: List[EvidenceRef] = []
    event_refs: List[EvidenceRef] = []
    for ev in (getattr(news_ctx, "major_events", None) or [])[:40]:
        event_refs.append(runtime.register_evidence(
            kind="news",
            source="node4",
            locator=str(getattr(ev, "event_id", None) or getattr(ev, "title", "")),
            content={
                "title": getattr(ev, "title", ""),
                "summary": getattr(ev, "summary", ""),
                "sectors": list(getattr(ev, "affected_sectors", None) or []),
                "sentiment": getattr(ev, "sentiment", ""),
            },
            quote=getattr(ev, "title", ""),
        ))
    refs.extend(event_refs)

    sector_rows: Dict[str, EvidenceRef] = {}
    if sector_ctx is not None:
        for name, detail in _iter_sector_details(sector_ctx):
            code = getattr(detail, "code", "") or name
            sector_rows[name] = runtime.register_evidence(
                kind="market",
                source="node2",
                locator=f"board:{code}",
                content={"name": name, "strength_score": getattr(detail, "strength_score", None)},
                quote=name,
            )
            refs.append(sector_rows[name])

    for row in symbols:
        sector = row.get("sector", "")
        if sector and sector in sector_rows:
            refs.append(sector_rows[sector])

    # 上一版身份也作为本轮可引用证据（旧 evidence_id 不带入，避免闭包误伤）
    for symbol, items in prev_map.items():
        for item in items[:2]:
            refs.append(runtime.register_evidence(
                kind="memory",
                source="semantic_memory",
                locator=f"identity:{symbol}:{item.get('theme', '')}",
                content={
                    "symbol": symbol,
                    "theme": item.get("theme"),
                    "layer": item.get("layer"),
                    "confidence": item.get("confidence"),
                    "identity_change": item.get("identity_change"),
                },
                quote=str(item.get("theme", "")),
            ))

    evidence_lines = "\n".join(
        f"- {ref.evidence_id} | {ref.kind} | {ref.locator} | {(ref.quote or '')[:100]}"
        for ref in refs
    )
    def _prev_json(items):
        return json.dumps(
            [{k: v for k, v in item.items() if k != "evidence_ids"} for item in items],
            ensure_ascii=False,
        )[:300]

    previous_lines = "\n".join(
        f"- {row['symbol']} | 当前板块:{row.get('sector') or '未知'} | 上一版身份:"
        f"{_prev_json(prev_map.get(row['symbol'], []))}"
        for row in symbols
    )

    identities: List[IdentityItem] = []
    status = "ok"
    for start in range(0, len(symbols), IDENTITY_BATCH_SIZE):
        batch = symbols[start:start + IDENTITY_BATCH_SIZE]
        batch_lines = "\n".join(
            f"- {row['symbol']} | 板块:{'/'.join(row.get('memberships') or [row.get('sector','')]) or '未知'}"
            for row in batch
        )
        prompt = f"""你是 A 股主题身份标注器。为每只股票给出最多 2 个可验证的主题身份。
只使用下方证据索引，不得引入外部事实；无证据时输出 abstain=true。

## 证据索引
{evidence_lines or '（无）'}

## 上一版身份（确定性记忆，不是结论）
{previous_lines}

## 待标注股票
{batch_lines}

## 输出（JSON 数组）
[{{"symbol":"600000","theme":"主题名","layer":"software_model|hardware|supply_chain|other",
  "confidence":0.0,"price_beta":null,"novelty":false,
  "evidence_ids":["ev_..."],"conflicts":["与既有身份冲突说明"],"abstain":false}}]

规则：
- theme 必须来自板块名/事件中的真实主题，不能编造
- novelty=true 必须至少有 2 条不同来源的证据；否则填 false
- 证据不足或主题与既有身份冲突无法裁决时 abstain=true（冲突可保留在 conflicts）
- 每只股票最多 2 条主题；不得修改 sector_role/classification/仓位
"""
        step = await runtime.call_json(
            task_id=f"narrative.identity.{start // IDENTITY_BATCH_SIZE + 1}",
            prompt=prompt,
            output_model=IdentityBatch,
            evidence_refs=refs,
            temperature=0.0,
            max_tokens=2500,
            max_retries=1,
            timeout=GOT_NARRATIVE_TIMEOUT_S,
        )
        if step.status != "ok":
            status = "missing_fixture" if step.status == "skipped" else "degraded"
            logger.warning("[Narrative] identity 批次失败: %s (%s)", step.step_id, step.error)
            continue
        batch_items = [IdentityItem.model_validate(item) for item in (step.output or [])]
        by_symbol: Dict[str, List[IdentityItem]] = {}
        for item in batch_items:
            by_symbol.setdefault(item.symbol, []).append(item)
        for row in batch:
            items = by_symbol.get(row["symbol"], [])
            if not items:
                identities.append(IdentityItem(symbol=row["symbol"], abstain=True))
                continue
            for item in items[:IDENTITY_MAX_PER_SYMBOL]:
                if item.layer not in VALID_LAYERS:
                    item.layer = "other"
                if item.novelty:
                    sources = {
                        ref.source for ref in refs
                        if ref.evidence_id in item.evidence_ids
                    }
                    if len(sources) < 2:
                        item.novelty = False
                if not item.evidence_ids:
                    item.abstain = True
                item.identity_change = _deterministic_change(
                    prev_map.get(item.symbol, []), item.theme, item.layer, item.confidence,
                )
                identities.append(item)

    if note:
        logger.info("[Narrative] identity note: %s", note)
    return NarrativeContext(status=status, as_of=as_of.isoformat(), identities=identities)


async def run_move_attribution(
    holdings: Any,
    unified: Any,
    news: Any,
    market: Any,
    sector: Any,
    thesis_map: Dict[str, Any],
    runtime: Optional[GOTAgentRuntime],
    as_of: date,
    capital: Any = None,
) -> NarrativeContext:
    """One bounded attribution pass for held symbols (max 10 per call)."""
    from src.got.catalyst_config import (
        GOT_NARRATIVE_AGENTS_ENABLED,
        GOT_NARRATIVE_TIMEOUT_S,
    )

    if not GOT_NARRATIVE_AGENTS_ENABLED:
        return NarrativeContext(status="disabled", as_of=as_of.isoformat())
    if runtime is None:
        return NarrativeContext(status="degraded", as_of=as_of.isoformat())

    rows = _symbols_from_holdings(holdings)
    seen: set[str] = set()
    rows = [r for r in rows if r["symbol"] and not (r["symbol"] in seen or seen.add(r["symbol"]))]
    rows = rows[:ATTRIBUTION_BATCH_SIZE]
    if not rows:
        return NarrativeContext(status="empty", as_of=as_of.isoformat())

    refs: List[EvidenceRef] = []
    event_refs: List[EvidenceRef] = []
    for ev in (getattr(news, "major_events", None) or [])[:40]:
        event_refs.append(runtime.register_evidence(
            kind="news",
            source="node4",
            locator=str(getattr(ev, "event_id", None) or getattr(ev, "title", "")),
            content={
                "title": getattr(ev, "title", ""),
                "sectors": list(getattr(ev, "affected_sectors", None) or []),
                "stocks": list(getattr(ev, "affected_stocks", None) or []),
                "sentiment": getattr(ev, "sentiment", ""),
            },
            quote=getattr(ev, "title", ""),
        ))
    refs.extend(event_refs)

    flow_data = {}
    try:
        sector_flow = getattr(getattr(capital, "evidence", None), "sector_flow", None) or {}
        flow_data = sector_flow.get("sector_flow_all_3d") or {}
    except Exception:  # noqa: BLE001
        flow_data = {}
    flow_ref = runtime.register_evidence(
        kind="capital",
        source="node3",
        locator="capital.sector_flow_all_3d",
        content=flow_data,
        quote="sector flow 3d",
    )
    refs.append(flow_ref)

    cases = []
    for row in rows:
        symbol = row["symbol"]
        sector_name = row.get("sector", "")
        return_pct = await asyncio.to_thread(_daily_return_pct, symbol)
        thesis = thesis_map.get(symbol)
        identity = _latest_identity_for_symbol(unified, symbol)
        cases.append({
            "symbol": symbol,
            "sector": sector_name,
            "return_pct": return_pct,
            "thesis": _as_dict(thesis),
            "identity": _as_dict(identity),
        })

    case_lines = "\n".join(
        f"- {c['symbol']} | 板块:{c['sector'] or '未知'} | 当日涨跌:{c['return_pct']} | "
        f"thesis:{json.dumps(c['thesis'], ensure_ascii=False)[:260]} | "
        f"identity:{json.dumps(c['identity'], ensure_ascii=False)[:260]}"
        for c in cases
    )
    prompt = f"""你是 A 股涨跌归因审核员。对每只持仓给出当日涨跌的最可能归因桶。
只使用下方证据索引与给定数值；没有个股新闻就是缺失，不得虚构“个股主力资金”。

## 证据索引
{chr(10).join(f"- {r.evidence_id} | {r.kind} | {r.locator} | {(r.quote or '')[:100]}" for r in refs) or '（无）'}

## 持仓
{case_lines}

## 输出（JSON 数组）
[{{"symbol":"600000","return_pct":0.0,"bucket":"sector_beta|theme_alpha|idiosyncratic|unexplained",
  "theme":null,"confidence":0.0,"evidence_ids":["ev_..."],"residual":null}}]

规则：
- bucket=sector_beta/theme_alpha 必须引用板块/主题证据；idiosyncratic 必须有个股证据
- 无任何可引用证据或涨跌数值缺失 → bucket="unexplained"，evidence_ids=[]
- residual 是未被解释的收益占比估计（0-1），无法估计填 null
"""
    step = await runtime.call_json(
        task_id="narrative.move_attribution",
        prompt=prompt,
        output_model=AttributionBatch,
        evidence_refs=refs,
        temperature=0.0,
        max_tokens=2000,
        max_retries=2,
        timeout=GOT_NARRATIVE_TIMEOUT_S,
    )
    if step.status != "ok":
        status = "missing_fixture" if step.status == "skipped" else "degraded"
        logger.warning("[Narrative] attribution 调用失败: %s (%s)", step.step_id, step.error)
        return NarrativeContext(status=status, as_of=as_of.isoformat())

    by_symbol = {
        item.symbol: item
        for item in (AttributionItem.model_validate(x) for x in (step.output or []))
    }
    attributions: List[AttributionItem] = []
    for case in cases:
        item = by_symbol.get(case["symbol"])
        if item is None:
            item = AttributionItem(
                symbol=case["symbol"], date=as_of.isoformat(),
                return_pct=case["return_pct"], bucket="unexplained",
            )
        item.date = as_of.isoformat()
        item.return_pct = case["return_pct"] if case["return_pct"] is not None else item.return_pct
        if item.bucket not in VALID_BUCKETS:
            item.bucket = "unexplained"
        if not item.evidence_ids or item.return_pct is None:
            item.bucket = "unexplained"
        elif item.bucket == "idiosyncratic":
            allowed = {r.evidence_id for r in refs if r.kind == "news"}
            if not (set(item.evidence_ids) & allowed):
                item.bucket = "unexplained"
        attributions.append(item)
    return NarrativeContext(status="ok", as_of=as_of.isoformat(), attributions=attributions)


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _iter_sector_details(sector_ctx: Any):
    seen = set()
    for group in getattr(sector_ctx, "active_mainline", None) or []:
        for member in getattr(group, "members", None) or []:
            if getattr(member, "name", None) and member.name not in seen:
                seen.add(member.name)
                yield member.name, member
    for attr in ("ambush", "exhausted_mainline", "defensive_rotation", "industry_top"):
        for detail in getattr(sector_ctx, attr, None) or []:
            name = getattr(detail, "name", None)
            if name and name not in seen:
                seen.add(name)
                yield name, detail


def _daily_return_pct(symbol: str) -> Optional[float]:
    try:
        from data_provider.base import DataFetcherManager

        quote = DataFetcherManager().get_realtime_quote(symbol)
        if isinstance(quote, dict):
            value = quote.get("change_pct")
            if value is not None:
                return float(value)
            price = float(quote.get("price", 0) or 0)
            prev = float(quote.get("prev_close", 0) or 0)
            if price > 0 and prev > 0:
                return round((price / prev - 1) * 100, 4)
            return None
        value = getattr(quote, "change_pct", None)
        if value is not None:
            return float(value)
        price = float(getattr(quote, "price", 0) or 0)
        prev = float(getattr(quote, "prev_close", 0) or 0)
        if price > 0 and prev > 0:
            return round((price / prev - 1) * 100, 4)
    except Exception as exc:  # noqa: BLE001 — missing quote is explicit missing data
        logger.debug("[Narrative] %s 当日涨跌不可用: %s", symbol, exc)
    return None


def _as_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {"value": str(value)}


def _latest_identity_for_symbol(unified: Any, symbol: str) -> Optional[dict]:
    ctx = getattr(unified, "narrative_context", None)
    if ctx is None:
        return None
    for item in reversed(getattr(ctx, "identities", None) or []):
        if getattr(item, "symbol", None) == symbol:
            return _as_dict(item)
    return None
