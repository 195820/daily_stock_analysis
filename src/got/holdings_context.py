# -*- coding: utf-8 -*-
"""持仓上下文构建器（Phase 1 供数，持仓模块三原则接线 H1，2026-10-27）。

原则②「持仓内容必须由前序 Node 节点提供分析数据」的第一跳：
GOT 编排器在 Phase 1 头部构建一次 `HoldingsContext`，供 Node 2/3/6 消费。

范围纪律（刻意收窄，避免把 Phase 2 的职责搬进来）：
- 只读两处真相源：`PortfolioService.get_portfolio_snapshot()`（账户/持仓）+ SQLite
  `holding_thesis_snapshots` / `ambush_position_snapshots`（入口锚）。
- 零 LLM、零新增爬虫、零决策：不判断买卖，只把「持仓是什么、入口锚在哪」供给下游。
- 失败一律 fail-open：返回 `status="degraded"` 的空壳（下游按无持仓处理），
  绝不阻断 GOT 主流程（与影子/旁挂产物同一纪律）。

入口锚口径（W3 未拍板前维持现状）：thesis 仍是唯一入场锚源（B2-c），
`ambush_position_snapshots` 只用于 `entry_source=ambush` 的标注；
若后续拍板换源，替换本模块的读侧即可，节点侧无需改动。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from src.got.schemas import HoldingBrief, HoldingsContext

logger = logging.getLogger(__name__)


def _merge_positions(portfolio: dict[str, Any]) -> list[dict[str, Any]]:
    """全账户去重合并（与 `PortfolioDecisionNode._all_positions` 同口径 B1-b）。

    symbol 重复时取先出现者（账户顺序即 service 返回顺序）。单账户现状下与
    `accounts[0].positions` 等价。
    """
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for account in (portfolio.get("accounts") or []):
        for pos in (account.get("positions") or []):
            symbol = str(pos.get("symbol") or "")
            if not symbol:
                continue
            if symbol in seen:
                continue
            seen.add(symbol)
            merged.append(pos)
    return merged


def _load_theses(symbols: list[str], session=None) -> dict[str, Any]:
    """每 symbol 取 entry_date 最新一条 open thesis（与 `_load_holding_theses` 同口径）。

    读失败 → 空 dict（调用方据此标 entry_source=none，不阻断）。
    """
    if not symbols:
        return {}
    own_session = session is None
    try:
        from src.storage import DatabaseManager, HoldingThesisRecord

        if own_session:
            session = DatabaseManager().get_session()
        records = (
            session.query(HoldingThesisRecord)
            .filter(
                HoldingThesisRecord.is_open == True,  # noqa: E712
                HoldingThesisRecord.symbol.in_(symbols),
            )
            .order_by(HoldingThesisRecord.entry_date.desc())
            .all()
        )
        result: dict[str, Any] = {}
        for r in records:
            if r.symbol not in result:
                result[r.symbol] = r
        return result
    except Exception as exc:  # noqa: BLE001 — 供数失败降级，绝不阻断 Phase 1
        logger.debug("[Holdings] thesis 读取失败（降级 entry_source=none）: %s", exc)
        return {}
    finally:
        if own_session and session is not None:
            try:
                session.close()
            except Exception:
                pass


def _load_ambush_symbols(symbols: list[str], session=None) -> set[str]:
    """当前仍 open 的埋伏仓位快照 symbol 集合（仅用于 entry_source 标注）。"""
    if not symbols:
        return set()
    own_session = session is None
    try:
        from src.storage import AmbushPositionSnapshotRecord, DatabaseManager

        if own_session:
            session = DatabaseManager().get_session()
        rows = (
            session.query(AmbushPositionSnapshotRecord.symbol)
            .filter(
                AmbushPositionSnapshotRecord.is_open == True,  # noqa: E712
                AmbushPositionSnapshotRecord.symbol.in_(symbols),
            )
            .all()
        )
        return {str(r[0]) for r in rows if r and r[0]}
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Holdings] 埋伏快照读取失败（降级为空集）: %s", exc)
        return set()
    finally:
        if own_session and session is not None:
            try:
                session.close()
            except Exception:
                pass


def _fetch_snapshot() -> dict[str, Any]:
    """读取持仓快照（唯一 IO 入口，便于测试注入 portfolio=...）。"""
    try:
        from src.services.portfolio_service import PortfolioService

        return PortfolioService().get_portfolio_snapshot() or {}
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"持仓快照读取失败: {exc}") from exc


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def build_holdings_context(
    as_of: Optional[date] = None,
    portfolio: Optional[dict[str, Any]] = None,
    session=None,
) -> HoldingsContext:
    """构建 Phase 1 持仓上下文。

    Args:
        as_of: 业务日（仅记入 generated_at，不影响读取口径；持仓快照恒为「当前」）
        portfolio: 持仓快照（None → 直读 PortfolioService；测试可注入）
        session: SQLAlchemy Session（None → 自建并关闭；测试可注入内存库）

    Returns:
        HoldingsContext：
        - status=normal：有持仓
        - status=empty：无持仓账户（下游全部按「无持仓诉求」处理）
        - status=degraded：读取失败（error 记原因，下游同样按无持仓处理）
    """
    try:
        snapshot = portfolio if portfolio is not None else _fetch_snapshot()
    except Exception as exc:  # noqa: BLE001 — 供数失败不阻断 GOT
        logger.warning("[Holdings] 持仓上下文构建降级: %s", exc)
        return HoldingsContext(
            status="degraded",
            error=str(exc),
            generated_at=datetime.now(),
        )

    raw_positions = _merge_positions(snapshot)
    if not raw_positions:
        return HoldingsContext(status="empty", generated_at=datetime.now())

    symbols = [str(p.get("symbol") or "") for p in raw_positions if p.get("symbol")]
    theses = _load_theses(symbols, session=session)
    ambush_symbols = _load_ambush_symbols(symbols, session=session)

    positions: list[HoldingBrief] = []
    sector_wishes: list[str] = []
    catalyst_ids: list[str] = []
    source_counts = {"thesis": 0, "ambush": 0, "none": 0}

    for pos in raw_positions:
        symbol = str(pos.get("symbol") or "")
        if not symbol:
            continue
        thesis = theses.get(symbol)
        if thesis is not None:
            entry_source = "thesis"
        elif symbol in ambush_symbols:
            entry_source = "ambush"
        else:
            entry_source = "none"
        source_counts[entry_source] = source_counts.get(entry_source, 0) + 1

        sector_name = str(getattr(thesis, "sector_name", "") or "") if thesis else ""
        catalyst_id = str(getattr(thesis, "catalyst_event_id", "") or "") if thesis else ""
        if sector_name and sector_name not in sector_wishes:
            sector_wishes.append(sector_name)
        if catalyst_id and catalyst_id not in catalyst_ids:
            catalyst_ids.append(catalyst_id)

        positions.append(HoldingBrief(
            symbol=symbol,
            name=str(pos.get("name") or ""),
            quantity=_to_float(pos.get("quantity")),
            avg_cost=_to_float(pos.get("avg_cost", pos.get("cost_price"))),
            last_price=_to_float(pos.get("last_price", pos.get("current_price"))),
            market_value=_to_float(pos.get("market_value")),
            unrealized_pnl_pct=_to_float(pos.get("unrealized_pnl_pct")),
            sector_name=sector_name,
            catalyst_event_id=catalyst_id,
            entry_source=entry_source,
        ))

    logger.info(
        "[Holdings] 持仓上下文: %d 只持仓, %d 个板块诉求, %d 个催化剂锚（入口锚 %s）",
        len(positions), len(sector_wishes), len(catalyst_ids), source_counts,
    )
    return HoldingsContext(
        status="normal",
        symbols=symbols,
        positions=positions,
        sector_wishes=sector_wishes,
        catalyst_ids=catalyst_ids,
        entry_source_counts=source_counts,
        generated_at=datetime.now(),
    )
