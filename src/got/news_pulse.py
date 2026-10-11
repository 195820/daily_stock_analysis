# -*- coding: utf-8 -*-
"""批次 D（竞品经验补强计划 2026-10-06）：持仓异动归因通道 news_pulse（一期纯观测）。

触发 = 持仓股当日 |涨跌幅| > 阈值（默认 7%）；对每只触发标的做**三维侦察**并一次
小 LLM 归因，输出照抄 ai-berkshire /news-pulse 实战形态：
  1. 一句话归因；2. 归因表（候选解释 × 估算贡献 × 置信度，含排除项）；
  3. 性质判断四选一（价值事件/情绪波动/真因不明/混合，带百分比分解）；4. 关键反证清单。

红线（遵计划 §D）：
- **一期只观测、不进任何决策**：产物落 `data/got_daily/{date}/news_pulse.json`，参照
  GLUE/SHADOW_T1 命名与影子段渲染；`GOT_NEWS_PULSE_ENABLED` 默认关。
- **单请求批量取当日涨幅**（D0 `manager.get_stock_quick_change`），非逐股循环。
- **触发口径绑定 `archive._is_bar_complete`**：盘中跑标「盘中预警口径（半截行情）」、
  收盘/回放标「收盘归因口径」（防把盘中价当收盘归因）。
- **硬上限 N 只**（默认 3，沿用 AMBUSH_NOMINATION_MAX 式惯例），LLM 成本入产物计数。
- **资金维本仓无源**（个股主力净流入无生产者）→ 诚实降为「三维 + 资金缺口标注」，
  板块 sector_flow 方向仅作旁证。
- **look-ahead 纪律**：归因搜索窗只用当日 news_ctx 池（当日及前一交易日采集），
  禁止用事后解读报道反锚原因。

与既有机制分工：`thesis_drift` 答"买入论点是否失效"，news_pulse 答"今天这根大 K 线
是谁干的"；二者互补，pulse 结论可回写 thesis snapshot 的 evidence 附注（本模块不落库，
避免另建存储）。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

NEWS_PULSE_ARTIFACT = "news_pulse.json"

# 归因 prompt 模板（{占位} 用 .format 注入，字面 JSON 花括号须双写）
_ATTRIBUTION_PROMPT = """你是 A 股持仓异动归因分析师。持仓股「{name}」（{symbol}）当日涨跌幅 {change_pct}%（口径：{caliber}）。
请只依据下面给出的当日证据判断这次异动最可能的驱动因素，禁止编造未提供的新闻或数据。

## 当日证据（三维侦察）
- 公司新闻（Node 4 当日池，affected_stocks 稀疏，可能为空）：
{news_evidence}
- 所属板块（{sector}）当日上下文：
{sector_evidence}
- 同板块其它成员当日涨跌（反证素材）：
{peer_evidence}
- 市场大盘 regime（beta 参考）：{regime}
- 资金面：本仓无个股主力净流入数据源（资金维缺口，仅可参考板块 sector_flow 方向作旁证）。

## 输出要求（严格 JSON，无其它文字）
{{
  "one_line": "一句话归因，像『这次 -8% 约 70% 由情绪面驱动，基本面无利空』",
  "attribution_table": [
    {{"explanation": "候选解释", "estimated_contrib": "-3%~-4%", "confidence": "高|中|低", "kept": true}},
    {{"explanation": "被排除项（如基本面恶化）", "estimated_contrib": "0%", "confidence": "高", "kept": false}}
  ],
  "nature": {{"type": "价值事件|情绪波动|真因不明|混合", "breakdown": "情绪60%/资金20%/基本面0%/未知20%"}},
  "counter_evidence": ["支持或反驳某解释的具体证据行"]
}}
注意：证据不足时 nature 选「真因不明」并如实反映（一期真因不明仅观测，两周后据占比定告警优先级）。"""


def _fmt_pct(x: Optional[float]) -> str:
    return "N/A" if x is None else f"{x:+.2f}%"


def _collect_news_evidence(events: List[Any], name: str, symbol: str, sector: Optional[str]) -> List[str]:
    """从当日 news 池挑与该标的相关的行（affected_stocks 含名/码，或标题含名，或板块命中）。"""
    out: List[str] = []
    hay = {name, symbol, str(symbol).split(".")[0]}
    for ev in events or []:
        affected = set(ev.affected_stocks or []) | set(str(a) for a in (ev.affected_stocks or []))
        title = ev.title or ""
        hit_by_stock = bool(hay & affected) or any(h and h in title for h in hay if h)
        hit_by_sector = bool(sector) and any(sector in s for s in (ev.affected_sectors or []))
        if hit_by_stock or hit_by_sector:
            tag = getattr(ev, "sentiment", "?")
            out.append(f"- [{tag}] {title[:70]}")
    return out[:8]  # 控量


def _sector_evidence(sector_ctx: Any, sector: Optional[str]) -> str:
    """标的所属板块在 Node 2 上下文里的分类（actionable/主线/生命周期），作定性旁证。"""
    if not sector:
        return "（无板块归属信息）"
    parts: List[str] = []
    confirmed = getattr(sector_ctx, "confirmed_sectors", []) or []
    for s in confirmed:
        if (getattr(s, "sector_name", "") or getattr(s, "name", "")) == sector:
            cls = getattr(s, "classification", "") or getattr(s, "action", "")
            parts.append(f"分类={cls or '?'}")
            lc = getattr(s, "lifecycle_phase", None)
            if lc:
                parts.append(f"生命周期={lc}")
            break
    mainline = {g.name for g in (getattr(sector_ctx, "active_mainline", []) or [])
                if getattr(g, "name", None)} if hasattr(sector_ctx, "active_mainline") else set()
    if sector in mainline:
        parts.append("当日活跃主线")
    return "；".join(parts) if parts else f"（{sector} 不在当日 confirmed_sectors/主线中）"


def _regime_of(market_ctx: Any) -> str:
    tf = getattr(market_ctx, "timeframe_got", None)
    reg = getattr(tf, "regime", None) if tf else None
    if not reg:
        ms = getattr(market_ctx, "market_signal", None)
        reg = getattr(ms, "regime", None) if ms else None
    return str(reg or "unknown")


def _parse_llm_json(text: str) -> Optional[Dict[str, Any]]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def run_news_pulse(
    *,
    decision: Any,
    news_ctx: Any,
    sector_ctx: Any,
    market_ctx: Any,
    target_date: date,
    manager: Any,
    llm: Any,
    enabled: bool,
    threshold: float,
    max_stocks: int,
    persist_usage=None,
) -> Optional[Dict[str, Any]]:
    """执行一次持仓异动归因观测；未启用/无触发标的返回 None（不落盘）。

    调用方（orchestrator）须以 try/except 包裹、不阻断主流程（与 P6 同模式）。
    """
    if not enabled:
        return None

    scoring = getattr(decision, "unified_scoring", []) or []
    holdings = [s for s in scoring if getattr(s, "is_holding", False)]
    if not holdings:
        return None

    # D0：单请求批量取当日涨幅（对全部评分标的取数，供同板块反证；触发只看持仓）
    codes = [s.symbol for s in scoring if getattr(s, "symbol", "")]
    changes: Dict[str, dict] = {}
    try:
        changes = manager.get_stock_quick_change(codes) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[news_pulse] 批量行情取数失败（缺源，整批观测跳过触发）: %s", exc)

    from src.got import archive as _archive
    now = datetime.now()
    caliber = "收盘归因口径" if _archive._is_bar_complete(target_date, now) else "盘中预警口径（半截行情，尾盘跳水日可能漏触发）"

    events = getattr(news_ctx, "major_events", []) or []
    # 按 |涨跌| 降序取触发集合（持仓），硬上限 max_stocks
    trig: List[tuple] = []
    for s in holdings:
        c = changes.get(s.symbol, {}).get("change_pct")
        if c is not None and abs(c) >= threshold:
            trig.append((s, c))
    trig.sort(key=lambda t: abs(t[1]), reverse=True)
    trig = trig[:max_stocks]
    if not trig:
        return None

    sectors_by_name: Dict[str, List[str]] = {}
    for s in scoring:
        if s.sector_name:
            sectors_by_name.setdefault(s.sector_name, []).append(s.symbol)

    llm_calls = 0
    items: List[Dict[str, Any]] = []
    for s, c in trig:
        peers = [
            f"{p} {_fmt_pct(changes.get(p, {}).get('change_pct'))}"
            for p in sectors_by_name.get(s.sector_name, []) if p != s.symbol
        ][:8]
        news_ev = _collect_news_evidence(events, s.name, s.symbol, s.sector_name)
        prompt = _ATTRIBUTION_PROMPT.format(
            name=s.name or "?", symbol=s.symbol, change_pct=_fmt_pct(c), caliber=caliber,
            sector=s.sector_name or "未知", regime=_regime_of(market_ctx),
            news_evidence="\n".join(news_ev) or "（当日无直接相关新闻，缺口如实标注）",
            sector_evidence=_sector_evidence(sector_ctx, s.sector_name),
            peer_evidence="；".join(peers) or "（无同板块其它评分标的）",
        )
        entry: Dict[str, Any] = {
            "symbol": s.symbol, "name": s.name, "change_pct": c,
            "sector_name": s.sector_name, "evidence_news_count": len(news_ev),
            "peers": peers, "attribution": None, "error": None,
        }
        try:
            resp = llm.call_text(
                [{"role": "system", "content": "你是持仓异动归因分析师，只输出 JSON。"},
                 {"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=1200, timeout=45.0,
            )
            llm_calls += 1
            if persist_usage and resp is not None:
                try:
                    persist_usage(usage=getattr(resp, "usage", {}) or {},
                                  model=getattr(resp, "model", "unknown") or "unknown",
                                  call_type="got_news_pulse")
                except Exception:  # noqa: BLE001
                    pass
            entry["attribution"] = _parse_llm_json(getattr(resp, "content", "") or "")
            if entry["attribution"] is None:
                entry["error"] = "llm_output_unparsed"
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {exc}"
        items.append(entry)

    record = {
        "line": "news_pulse",
        "date": target_date.strftime("%Y%m%d"),
        "caliber": caliber,
        "threshold_pct": threshold,
        "max_stocks": max_stocks,
        "holdings_total": len(holdings),
        "triggered": len(items),
        "llm_calls": llm_calls,
        "fund_dimension": "no_source（个股主力净流入无生产者，资金维缺口）",
        "lookahead_guard": "仅用当日 news_ctx 池，不用事后解读报道反锚",
        "items": items,
    }
    _write_artifact(target_date, record)
    return record


def _write_artifact(target_date: date, record: Dict[str, Any]) -> None:
    """原子落 news_pulse.json 到当日归档目录（失败静默，不阻断主流程）。"""
    try:
        day_dir = Path("data/got_daily") / target_date.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / NEWS_PULSE_ARTIFACT
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        logger.info("[news_pulse] 观测产物已落盘: %s（触发 %d 只）", path, record["triggered"])
    except OSError as exc:  # noqa: BLE001
        logger.warning("[news_pulse] 产物落盘失败（已忽略）: %s", exc)
