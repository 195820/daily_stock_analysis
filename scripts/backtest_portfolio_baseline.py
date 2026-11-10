#!/usr/bin/env python
"""P0 冻结基线：把归档 allocation 当作订单流，用模拟成交与组合记账回放。

实施稿 §7.1/§7.3/§8-P0
------------------------
- 决策日收盘后出单 → **下一交易日开盘价**模拟成交（滑点/佣金/印花税/过户费全计）；
- 100 股买入单位；T+1（按 lot 建仓日）；涨跌停/停牌/报价缺失 → 不可成交并留痕；
- 初始资金固定、输入=归档 allocation + 冻结行情快照 → 同一输入两次运行逐字节相同；
- `allocation` 只是建议：本脚本不把它当成真实成交，而是自建模拟组合并独立记账；
- 缺失/损坏产物单独计数（`artifact_missing` / `artifact_corrupt`），不计作「零订单」。

不做什么
--------
不做策略变体、不做参数搜索、不宣称收益改善。四组对照（基线 / +账本 / +路由 setup /
+个股转换）由后续阶段在同一 runner 上追加。

用法
----
    python scripts/backtest_portfolio_baseline.py --start 20260801 --end 20261103
    python scripts/backtest_portfolio_baseline.py --start 20260801 --end 20261103 --check-repro
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.got.backtest_discipline import (  # noqa: E402
    build_run_card,
    date_cluster_bootstrap_ci,
)
from src.got.backtest_data import PriceSnapshot  # noqa: E402
from src.got.portfolio_sim import (  # noqa: E402
    Bar,
    CostModel,
    Order,
    PortfolioSimulator,
    closed_trade_metrics,
    max_drawdown,
    turnover_ratio,
)

DAILY = ROOT / "data" / "got_daily"
DEFAULT_CACHE = ROOT / "data" / "_replay" / "stock_ohlcv_cache.json"
_ARCHIVE_SUFFIXES = ("_backup", "_pre_fix")

MIN_DAYS = 60
MIN_CLOSED_TRADES = 100


# ── 输入 ────────────────────────────────────────────────────────────────
def archive_days(start: date, end: date) -> List[Path]:
    out = []
    for path in sorted(DAILY.iterdir()):
        if not path.is_dir() or len(path.name) != 8 or not path.name.isdigit():
            continue
        if any(path.name.endswith(sfx) for sfx in _ARCHIVE_SUFFIXES):
            continue
        day = datetime.strptime(path.name, "%Y%m%d").date()
        if start <= day <= end:
            out.append(path)
    return out


def load_orders(day_dir: Path, counters: Dict[str, int]) -> List[Order]:
    path = day_dir / "portfolio_decision.json"
    if not path.exists():
        counters["artifact_missing"] += 1
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        counters["artifact_corrupt"] += 1
        counters.setdefault("artifact_corrupt_detail", []).append(
            f"{day_dir.name}:{type(exc).__name__}"
        )
        return []
    decision_date = datetime.strptime(day_dir.name, "%Y%m%d").date()
    orders: List[Order] = []
    for item in payload.get("allocation") or []:
        if item.get("blocked_reason"):
            # 生产 GATE-1 已判不可成交 → 从未成为真实订单，不混入回测拒单统计
            counters["order_blocked_in_production"] += 1
            continue
        symbol = str(item.get("symbol") or "").strip()
        action = str(item.get("action") or "").strip().lower()
        try:
            delta = int(float(item.get("quantity_delta") or 0))
        except (TypeError, ValueError):
            counters["order_quantity_invalid"] += 1
            continue
        if not symbol or delta == 0:
            continue
        if action == "buy":
            side, qty = "buy", abs(delta)
        elif action in ("sell", "reduce"):
            side, qty = "sell", abs(delta)
        else:
            counters["order_action_unknown"] += 1
            continue
        orders.append(Order(
            decision_date=decision_date,
            symbol=symbol,
            name=str(item.get("name") or ""),
            side=side,
            quantity=qty,
            lane="ambush" if item.get("is_ambush_entry") else "mainline",
            reason=action,
        ))
    if not orders:
        counters["no_orders"] += 1
    return orders


# ── 回放 ────────────────────────────────────────────────────────────────
def simulate(
    *,
    days: Sequence[Path],
    snapshot: PriceSnapshot,
    initial_cash: float,
    cost: CostModel,
    start_day: str,
    end_day: str,
    counters: Dict[str, int],
) -> dict:
    sim = PortfolioSimulator(initial_cash=initial_cash, cost_model=cost)

    orders_by_exec: Dict[str, List[Order]] = {}
    order_rows: List[dict] = []
    for day_dir in days:
        day_str = f"{day_dir.name[:4]}-{day_dir.name[4:6]}-{day_dir.name[6:]}"
        orders = load_orders(day_dir, counters)
        if not orders:
            continue
        exec_day = snapshot.next_day(day_str)
        if exec_day is None or exec_day > snapshot.dates[-1]:
            counters["no_next_session"] += len(orders)
            continue
        orders_by_exec.setdefault(exec_day, []).extend(orders)
        for o in orders:
            order_rows.append({
                "decision_date": o.decision_date.isoformat(),
                "exec_date": exec_day,
                "symbol": o.symbol,
                "name": o.name,
                "side": o.side,
                "quantity": o.quantity,
                "lane": o.lane,
                "action": o.reason,
            })

    equity_end = snapshot.dates[-1]
    window = [d for d in snapshot.dates if start_day <= d <= equity_end]
    market_values: List[Tuple[date, float]] = []
    for day_str in window:
        if day_str in orders_by_exec:
            bars = {}
            for order in orders_by_exec[day_str]:
                bar = snapshot.bar(order.symbol, day_str)
                if bar is None:
                    counters["symbol_missing_bar"] += 1
                    continue
                bars[order.symbol] = bar
            sim.apply_orders(
                decision_date=min(o.decision_date for o in orders_by_exec[day_str]),
                trade_date=date.fromisoformat(day_str),
                orders=orders_by_exec[day_str],
                bars=bars,
                qfq_closes=snapshot.qfq_closes(day_str),
            )
        sim.mark_to_market(
            date.fromisoformat(day_str),
            snapshot.closes(day_str),
            snapshot.qfq_closes(day_str),
        )
        market_values.append((date.fromisoformat(day_str), sim.market_value()))

    equity_values = [v for _, v in sim.equity_curve]
    total_notional = sum(f.notional for f in sim.fills)
    closed = sim.closed_trades
    metrics = closed_trade_metrics(closed)

    # 日期簇 bootstrap：按平仓日聚合净收益
    by_date: Dict[str, List[float]] = {}
    by_date_win: Dict[str, List[float]] = {}
    for trade in closed:
        key = trade.exit_date.isoformat()
        by_date.setdefault(key, []).append(trade.net_return)
        by_date_win.setdefault(key, []).append(1.0 if trade.net_return > 0 else 0.0)
    ret_ci = date_cluster_bootstrap_ci(by_date)
    win_ci = date_cluster_bootstrap_ci(by_date_win)

    # qfq 交叉校验（除权除息日 raw 收益低估）
    qfq_delta = []
    closed_rows = []
    for trade in closed:
        row = trade.as_dict()
        qfq_entry = snapshot.qfq_close(trade.symbol, trade.entry_date.isoformat())
        qfq_exit = snapshot.qfq_close(trade.symbol, trade.exit_date.isoformat())
        if qfq_entry and qfq_exit:
            row["qfq_gross_return"] = round(qfq_exit / qfq_entry - 1, 6)
            row["return_basis"] = "raw_cash（净额含费）+ qfq 交叉校验"
            qfq_delta.append(row["qfq_gross_return"] - trade.net_return)
        else:
            row["qfq_gross_return"] = None
            row["return_basis"] = "raw_cash（qfq 缺失）"
        closed_rows.append(row)

    cost_total = sum(f.fees for f in sim.fills)
    util = (
        sum(mv / eq for (_, mv), (_, eq) in zip(market_values, sim.equity_curve) if eq > 0)
        / len(sim.equity_curve)
        if sim.equity_curve else 0.0
    )
    lane_stats = {}
    for lane in ("mainline", "ambush", ""):
        subset = [t for t in closed if t.lane == lane]
        if subset:
            lane_stats[lane or "unknown"] = closed_trade_metrics(subset)

    payload = {
        "meta": {
            "initial_cash": initial_cash,
            "cost_model": cost.as_dict(),
            "window": [start_day, equity_end],
            "decision_end": end_day,
            "args": {"start": start_day, "end": end_day, "initial_cash": initial_cash},
            "price_snapshot": {
                "symbols": len(snapshot.symbols),
                "dates": len(snapshot.dates),
                # 内容指纹替代抓取时间：同一数据重建不应改变基线 digest
                "data_sha256": hashlib.sha256(
                    json.dumps(snapshot.symbols, sort_keys=True,
                               ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ).hexdigest()[:16],
                "sources": [snapshot.meta.get("raw_source"), snapshot.meta.get("qfq_source")],
            },
            "replay_uses_archived_allocation": True,
            "allocation_is_advisory": True,
        },
        "counters": {k: v for k, v in sorted(counters.items())},
        "orders": order_rows,
        "fills": [f.as_dict() for f in sim.fills],
        "closed_trades": closed_rows,
        "equity_curve": [
            [d.isoformat(), round(eq, 2), round(mv, 2)]
            for (d, eq), (_, mv) in zip(sim.equity_curve, market_values)
        ],
        "metrics": {
            "initial_cash": round(initial_cash, 2),
            "final_equity": round(sim.equity(), 2),
            "total_return": round(sim.equity() / initial_cash - 1, 6),
            "max_drawdown": round(max_drawdown(equity_values), 6),
            "closed_trades": metrics,
            "win_rate_ci95": list(win_ci),
            "avg_net_return_ci95": list(ret_ci),
            "cost_total": round(cost_total, 2),
            "turnover_ratio": round(turnover_ratio(total_notional, sim.equity_curve), 4),
            "avg_position_utilization": round(util, 4),
            "cash_final": round(sim.cash, 2),
            "positions_final": len([p for p in sim.positions.values() if p.quantity > 0]),
            "stale_marks": sim.stale_marks,
            "stale_marks_detail": sim.stale_marks_detail,
            "qfq_mark_fallbacks": sim.qfq_mark_fallbacks,
            "qfq_fallback_detail": sim.qfq_fallback_detail,
            "partial_counts": dict(sorted(sim.partial_counts.items())),
            "corporate_action_days": len(snapshot.corporate_action_days()),
            "qfq_vs_net_return_max_abs_delta": (
                round(max(abs(x) for x in qfq_delta), 6) if qfq_delta else None
            ),
            "lane_breakdown": lane_stats,
            "sample_days": len(sim.equity_curve),
            "insufficient_sample": bool(
                len(sim.equity_curve) < MIN_DAYS or metrics["count"] < MIN_CLOSED_TRADES
            ),
            "sample_requirement": {"min_days": MIN_DAYS, "min_closed_trades": MIN_CLOSED_TRADES},
        },
        "rejections": dict(sorted(sim.rejections.items())),
    }
    return payload


def payload_digest(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── 报告 ────────────────────────────────────────────────────────────────
def _binding_constraint(metrics: dict) -> str:
    """指出 insufficient_sample 的绑定条件，避免只看到「天数达标」就误读。"""
    missing = []
    if metrics["sample_days"] < MIN_DAYS:
        missing.append(f"days {metrics['sample_days']}<{MIN_DAYS}")
    if metrics["closed_trades"]["count"] < MIN_CLOSED_TRADES:
        missing.append(
            f"closed_trades {metrics['closed_trades']['count']}<{MIN_CLOSED_TRADES}"
        )
    return "; ".join(missing) or "—"


def render_md(payload: dict, digest: str, run_card: dict) -> str:
    m = payload["metrics"]
    c = payload["counters"]
    lines = [
        f"# P0 冻结基线组合回测（{payload['meta']['window'][0]} → {payload['meta']['window'][1]}）",
        "",
        "口径：归档 `allocation` 作为订单流 → 下一交易日开盘价模拟成交 → 自建模拟组合记账。"
        "`allocation` 是建议，不是真实成交；本报告不构成收益改善结论。",
        "",
        "**结构性前提**：`allocation` 的卖出数量以**真实账户持仓**为前提，模拟组合从固定初始现金起步，"
        "因此卖单会被模拟持仓裁剪（见「未成交/拒绝原因」中的 `no_position` / `insufficient_position`）。"
        "未平仓头寸是「决策忠实重放」的结果，不是可交易策略收益；四组对照与自洽决策回路属 P4。",
        "",
        "## Run card",
        "",
        "| 项 | 值 |",
        "|---|---|",
    ]
    for key, value in run_card.items():
        lines.append(f"| {key} | {value} |")
    ct = m["closed_trades"]
    win_rate = f"{ct['win_rate']:.1%}" if ct["count"] else "insufficient_sample"
    avg_ret = f"{ct['avg_net_return']:+.2%}" if ct["count"] else "insufficient_sample"
    lines += [
        "",
        "## 主指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 初始资金 | ¥{m['initial_cash']:,.2f} |",
        f"| 期末净值 | ¥{m['final_equity']:,.2f} |",
        f"| 累计净收益率 | {m['total_return']:+.2%} |",
        f"| 最大回撤 | {m['max_drawdown']:.2%} |",
        f"| 闭合往返笔数 | {ct['count']} |",
        f"| 净收益胜率 | {win_rate} |",
        f"| 平均每笔净收益 | {avg_ret} |",
        f"| 盈亏比（profit factor） | "
        f"{('%.2f' % ct['profit_factor']) if ct['profit_factor'] is not None else 'n/a'} |",
        f"| 胜率 95% CI（日期簇） | {m['win_rate_ci95']}"
        f"{'（insufficient_sample）' if m['insufficient_sample'] else ''} |",
        f"| 平均每笔净收益 95% CI | {m['avg_net_return_ci95']} |",
        f"| 成本合计 | ¥{m['cost_total']:,.2f} |",
        f"| 换手率 | {m['turnover_ratio']:.2f}× |",
        f"| 平均仓位利用率 | {m['avg_position_utilization']:.1%} |",
        f"| 期末持仓数 | {m['positions_final']} |",
        f"| 样本天数 | {m['sample_days']}（要求 ≥{MIN_DAYS}） |",
        f"| 样本是否充足 | {'否 → insufficient_sample（约束：' + _binding_constraint(m) + '）' if m['insufficient_sample'] else '是'} |",
        f"| 部分成交原因 | {m.get('partial_counts') or '—'} |",
        "",
        "## 数据完整性计数",
        "",
        "| 计数 | 值 |",
        "|---|---|",
    ]
    for key, value in c.items():
        if isinstance(value, list):
            value = f"{len(value)} 条"
        lines.append(f"| {key} | {value} |")
    lines += [
        "",
        "## 计价审计明细",
        "",
        f"- stale marks（缺当日收盘、沿用上一价）：{m['stale_marks']} 条"
        + (f"：{', '.join((m.get('stale_marks_detail') or [])[:10])}" if m['stale_marks'] else ""),
        f"- qfq 换算缺口（回退 raw）：{m['qfq_mark_fallbacks']} 条"
        + (f"：{', '.join((m.get('qfq_fallback_detail') or [])[:10])}" if m['qfq_mark_fallbacks'] else ""),
        "",
        "## 未成交/拒绝原因",
        "",
        "| 原因 | 次数 |",
        "|---|---|",
    ]
    for reason, count in (payload.get("rejections") or {}).items():
        lines.append(f"| {reason} | {count} |")
    if not payload.get("rejections"):
        lines.append("| — | 0 |")
    lines += [
        "",
        "## 收盘净值（前 5 / 后 5）",
        "",
        "| 日期 | 净值 | 市值 |",
        "|---|---|---|",
    ]
    curve = payload["equity_curve"]
    for row in curve[:5] + ([["…", "", ""]] if len(curve) > 10 else []) + curve[-5:]:
        if row == ["…", "", ""]:
            lines.append("| … | … | … |")
        else:
            lines.append(f"| {row[0]} | {row[1]:,.2f} | {row[2]:,.2f} |")
    lines += [
        "",
        "## 可复现性",
        "",
        f"- payload sha256：`{digest}`",
        "- 复现：`python scripts/backtest_portfolio_baseline.py "
        f"--start {payload['meta']['window'][0].replace('-', '')} "
        f"--end {payload['meta']['window'][1].replace('-', '')} --check-repro`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="P0 冻结基线组合回测")
    ap.add_argument("--start", default="20260801")
    ap.add_argument("--end", default="20261103")
    ap.add_argument("--initial-cash", type=float, default=1_000_000.0)
    ap.add_argument("--price-cache", default=str(DEFAULT_CACHE))
    ap.add_argument("--json-out", default="")
    ap.add_argument("--md-out", default="")
    ap.add_argument("--check-repro", action="store_true")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y%m%d").date()
    end = datetime.strptime(args.end, "%Y%m%d").date()
    cache_path = Path(args.price_cache)
    if not cache_path.exists():
        print(f"缺行情快照：{cache_path}（先跑 scripts/build_stock_ohlcv_cache.py）")
        return 2
    snapshot = PriceSnapshot(json.loads(cache_path.read_text(encoding="utf-8")))

    days = archive_days(start, end)
    if not days:
        print("无归档日")
        return 1

    start_day = start.isoformat()
    end_day = end.isoformat()
    cost = CostModel()

    def run() -> Tuple[dict, str]:
        counters = {
            "archive_days": len(days),
            "artifact_missing": 0,
            "artifact_corrupt": 0,
            "no_orders": 0,
            "order_quantity_invalid": 0,
            "order_action_unknown": 0,
            "order_blocked_in_production": 0,
            "no_next_session": 0,
            "symbol_missing_bar": 0,
        }
        payload = simulate(
            days=days, snapshot=snapshot, initial_cash=args.initial_cash,
            cost=cost, start_day=start_day, end_day=end_day, counters=counters,
        )
        return payload, payload_digest(payload)

    t0 = time.time()
    payload, digest = run()
    duration = time.time() - t0

    run_card = build_run_card(
        sample_window=f"{start_day} → {end_day}（决策窗口；净值窗口至 {payload['meta']['window'][1]}）",
        benchmark="none（绝对净收益，双主指标之组合净值口径）",
        adjust_mode="raw(成交/涨跌停) + qfq(收益核算交叉校验)",
        missing_value_policy="no_fill（缺失按不可成交，单独计数）",
        point_in_time="close_decision_next_open（决策日收盘 → 次日开盘成交）",
        extra={
            "initial_cash": args.initial_cash,
            "cost_model": cost.as_dict(),
            "order_source": "data/got_daily/YYYYMMDD/portfolio_decision.json::allocation",
            "price_snapshot": str(cache_path.relative_to(ROOT)) if cache_path.is_relative_to(ROOT) else str(cache_path),
            "price_snapshot_scope": "allocation + candidate_pool universe（成交只读取所需 symbol）",
            "lot_size": 100,
            "t_plus_1": True,
            "insufficient_sample_rule": f"days<{MIN_DAYS} or closed_trades<{MIN_CLOSED_TRADES}",
            "decision_window_end": end_day,
            "equity_window_end": payload["meta"]["window"][1],
            "account_premise": "固定初始现金的合成组合；卖单按模拟持仓裁剪（非真实账户）",
        },
    )

    payload["meta"]["run_card"] = run_card
    payload["meta"]["generated_at"] = datetime.now().isoformat(timespec="seconds")
    payload["meta"]["duration_sec"] = round(duration, 3)
    payload["payload_sha256"] = digest

    if args.check_repro:
        payload2, digest2 = run()
        same = digest == digest2
        print(f"repro: {'PASS' if same else 'FAIL'} ({digest[:16]} vs {digest2[:16]})")
        if not same:
            return 3

    json_out = Path(args.json_out) if args.json_out else (
        DAILY / f"backtest_portfolio_baseline_{args.start}_{args.end}.json"
    )
    md_out = Path(args.md_out) if args.md_out else json_out.with_suffix(".md")
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_out.write_text(render_md(payload, digest, run_card), encoding="utf-8")

    m = payload["metrics"]
    print(
        f"净值 {m['initial_cash']:,.0f} → {m['final_equity']:,.0f}"
        f"（{m['total_return']:+.2%}）；闭合 {m['closed_trades']['count']} 笔；"
        f"胜率 {m['closed_trades']['win_rate']}；样本充足={not m['insufficient_sample']}"
    )
    print(f"JSON: {json_out}")
    print(f"MD:   {md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
