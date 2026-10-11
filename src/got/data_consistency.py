# -*- coding: utf-8 -*-
"""批次 C1（竞品经验补强计划 2026-10-06）：数值双源交叉验证（抽样、一期纯观测）。

对当日抽样 N 只持仓/候选股做**两个独立数据源**的当日涨跌幅比对（ai-berkshire 经验：
关键数值 ≥2 独立来源交叉验证，误差过大告警）：
- 源 A：`DataFetcherManager.get_realtime_quote`（东财/新浪等综合实时行情，含 change_pct / is_stale）
- 源 B：`DataFetcherManager.get_stock_quick_change`（腾讯批量行情 change_pct，D0 新原语）

口径修正（遵计划 §C1，避免"两源都前复权、基不同即 >1%"的伪告警）：
- 比的是**当日涨跌幅（change_pct）**——同为当日比值、不受前复权基漂移影响，可比；
  不复权原始价不可得时即走此符号/量级一致性降级路径。
- 资金流：本仓个股主力净流入**无第二源**（Node 3 DDE 已因东财封锁摘除）→ 不做数值阈值比对，
  record 明标 `fund_dimension: no_second_source`（诚实缺源，不伪造）。

一期红线：偏差/不一致 → 仅落 `consistency.json` + 日志，**不改任何决策输入**。
`GOT_CONSISTENCY_SAMPLE=0` 关闭；缺省 10。产物头带口径声明（与批次 F run card 呼应）。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONSISTENCY_ARTIFACT = "consistency.json"

# 视为"零涨跌噪声"的下限：两源都接近 0 时不做符号判定（避免 -0.01 vs +0.01 伪告警）
_ZERO_EPS = 0.05


def compare_change_consistency(
    a_pct: Optional[float], b_pct: Optional[float], *, tol: float = 1.0,
) -> Dict[str, Any]:
    """纯比对：返回 {status, detail}。status ∈ ok / sign_mismatch / magnitude_diff / missing。"""
    if a_pct is None or b_pct is None:
        return {"status": "missing", "detail": "单源缺值，无法比对"}
    # 符号相反（且非零噪声）
    if a_pct > _ZERO_EPS and b_pct < -_ZERO_EPS or a_pct < -_ZERO_EPS and b_pct > _ZERO_EPS:
        return {"status": "sign_mismatch", "detail": f"符号相反 A={a_pct:+.2f}% B={b_pct:+.2f}%"}
    diff = abs(a_pct - b_pct)
    if diff > tol:
        return {"status": "magnitude_diff", "detail": f"量级差 {diff:.2f}pp A={a_pct:+.2f}% B={b_pct:+.2f}%"}
    return {"status": "ok", "detail": f"一致 A={a_pct:+.2f}% B={b_pct:+.2f}%（Δ={diff:.2f}pp）"}


def _sample_symbols(decision: Any, n: int) -> List[str]:
    """抽样：持仓优先，其次候选，去重保序，取前 n。"""
    scoring = getattr(decision, "unified_scoring", []) or []
    holdings = [s.symbol for s in scoring if getattr(s, "is_holding", False) and getattr(s, "symbol", "")]
    others = [s.symbol for s in scoring if not getattr(s, "is_holding", False) and getattr(s, "symbol", "")]
    seen, out = set(), []
    for sym in holdings + others:
        if sym not in seen:
            seen.add(sym)
            out.append(sym)
        if len(out) >= n:
            break
    return out


def run_consistency_check(
    *,
    decision: Any,
    target_date: date,
    sample_n: int,
    tol: float,
    manager: Any,
) -> Optional[Dict[str, Any]]:
    """执行一次抽样双源交叉验证，落 consistency.json（纯观测）。

    sample_n<=0 或无标的 → None（不落盘）。调用方 try/except 包裹、不阻断。单只取数失败
    静默计入 missing，不抛（遵计划"失败静默跳过并计数"）。
    """
    if sample_n <= 0:
        return None

    symbols = _sample_symbols(decision, sample_n)
    if not symbols:
        return None

    # 源 B：腾讯批量一次取全部（D0 原语，单请求）
    b_changes: Dict[str, dict] = {}
    try:
        b_changes = manager.get_stock_quick_change(symbols) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("[consistency] 源B 批量涨跌幅取数失败（计缺源）: %s", exc)

    items: List[Dict[str, Any]] = []
    alerts = 0
    missing = 0
    for sym in symbols:
        a_pct = None
        a_stale = None
        try:
            quote = manager.get_realtime_quote(sym)
            if quote is not None:
                a_pct = getattr(quote, "change_pct", None)
                a_stale = getattr(quote, "is_stale", None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[consistency] 源A 实时行情取数失败 %s: %s", sym, exc)
        b_pct = b_changes.get(sym, {}).get("change_pct")
        cmp = compare_change_consistency(a_pct, b_pct, tol=tol)
        if cmp["status"] == "missing":
            missing += 1
        elif cmp["status"] != "ok":
            alerts += 1
            logger.warning("[consistency/alert] %s: %s（%s）", sym, cmp["status"], cmp["detail"])
        items.append({
            "symbol": sym, "a_change_pct": a_pct, "b_change_pct": b_pct,
            "status": cmp["status"], "detail": cmp["detail"], "a_is_stale": a_stale,
        })

    record = {
        "line": "consistency",
        "date": target_date.strftime("%Y%m%d"),
        "caliber": "当日涨跌幅 change_pct 比对（同为当日比值，不受前复权基漂移影响）",
        "sources": {"A": "manager.get_realtime_quote", "B": "manager.get_stock_quick_change(腾讯批量)"},
        "fund_dimension": "no_second_source（个股主力净流入无第二源，不做数值比对）",
        "sample_size": len(items),
        "alerts": alerts,
        "missing_source": missing,
        "items": items,
    }
    _write_artifact(target_date, record)
    return record


def _write_artifact(target_date: date, record: Dict[str, Any]) -> None:
    """原子落 consistency.json（失败静默，不阻断——与其余影子产物同模式）。"""
    try:
        day_dir = Path("data/got_daily") / target_date.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / CONSISTENCY_ARTIFACT
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        logger.info("[consistency] 观测产物已落盘: %s（抽样 %d，告警 %d，缺源 %d）",
                    path, record["sample_size"], record["alerts"], record["missing_source"])
    except OSError as exc:  # noqa: BLE001
        logger.warning("[consistency] 产物落盘失败（已忽略）: %s", exc)
