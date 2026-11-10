#!/usr/bin/env python
"""P3-a 个股—主题证据回链审计（只读归档，不扩候选资格）。

输出
----
- 每只候选票的 board_code / episode_id / age / 四变体 setup / 输出桶可见性回链；
- 三类失真的**可观测部分**：弱埋伏接跌、晚段入场、强板块非 finalist（代理）；
- 映射率、未匹配板块名、空占位行（不计入分母）；
- 同输入两次运行 digest 一致。

不做：不生成候选、不改漏斗、不做验证补选（需漏斗重放，见 `not_measured`）。

用法
----
    python scripts/audit_theme_stock_link.py --start 20261008 --end 20261103
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from build_theme_ledger_history import (  # noqa: E402
    archive_day_payload,
    load_name_code,
    membership_of,
    states_from_archive,
    trading_days,
)
from src.got.theme_ledger import build_observations, derive_episodes, derive_setups  # noqa: E402
from src.got.theme_stock_link import (  # noqa: E402
    attach_forward,
    attach_forward_net,
    build_links,
    summarize,
    summarize_forward,
    summarize_net,
)

DAILY = ROOT / "data" / "got_daily"
STOCK_CACHE = ROOT / "data" / "_replay" / "stock_ohlcv_cache.json"
BOARD_CACHE = ROOT / "data" / "_replay" / "board_ohlcv_cache.json"


def build(start: str, end: str) -> Dict[str, Any]:
    calendar = trading_days(
        datetime.strptime(start, "%Y%m%d").date(),
        datetime.strptime(end, "%Y%m%d").date(),
    )
    name_code = load_name_code()
    days = [
        d for d in calendar
        if (DAILY / d.replace("-", "") / "sector.json").exists()
        and (DAILY / d.replace("-", "") / "candidate_pool.json").exists()
    ]

    all_obs = []
    payloads: Dict[str, dict] = {}
    pools: Dict[str, dict] = {}
    for day in days:
        payload = archive_day_payload(DAILY / day.replace("-", "") / "sector.json")
        payloads[day] = payload
        state_rows, _ = states_from_archive(payload, name_code)
        observations, _ = build_observations(
            day, state_rows=state_rows, membership=membership_of(payload),
            news_refs={}, catalyst_refs={}, run_id="p3-link",
        )
        all_obs.extend(observations)
        pools[day] = json.loads(
            (DAILY / day.replace("-", "") / "candidate_pool.json").read_text(encoding="utf-8")
        )

    links: List[Any] = []
    per_day: Dict[str, Any] = {}
    for day in days:
        as_of = [o for o in all_obs if o.trade_date <= day]
        episodes, index = derive_episodes(as_of, calendar)
        setups = derive_setups(as_of, index, calendar)
        day_links = build_links(
            trade_date=day, candidates=pools[day].get("universe") or [],
            observations=as_of, episodes=episodes, setups=setups, calendar=calendar,
        )
        links.extend(day_links)
        per_day[day] = summarize(day_links)

    links.sort(key=lambda x: (x.trade_date, x.symbol))
    summary = summarize(links)

    # 事后评估：个股 T+h 收益 − 所属板块 T+h（收盘到收盘；不参与任何决策）
    rows = [x.as_dict() for x in links]
    forward_note = "skipped_no_cache"
    if STOCK_CACHE.exists() and BOARD_CACHE.exists():
        stock_payload = json.loads(STOCK_CACHE.read_text(encoding="utf-8"))
        board_payload = json.loads(BOARD_CACHE.read_text(encoding="utf-8"))
        stock_closes = {
            sym: {r[0]: float(r[4]) for r in entry.get("raw") or [] if len(r) >= 6}
            for sym, entry in (stock_payload.get("symbols") or {}).items()
        }
        board_closes = {
            code: {d: float(c) for d, c in zip(entry.get("dates") or [], entry.get("close") or [])}
            for code, entry in board_payload.items()
        }
        all_dates = [d for series in stock_closes.values() for d in series]
        all_dates += [d for series in board_closes.values() for d in series]
        cal_end = max(all_dates).replace("-", "") if all_dates else end
        forward_calendar = trading_days(
            datetime.strptime(start, "%Y%m%d").date(),
            datetime.strptime(cal_end, "%Y%m%d").date(),
        )
        rows = attach_forward(
            rows, calendar=forward_calendar,
            stock_closes=stock_closes, board_closes=board_closes, horizons=(3, 5, 10, 15),
        )
        stock_bars = {
            sym: {
                r[0]: {
                    "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
                    "close": float(r[4]), "volume": float(r[5]),
                    "prev_close": float(entry["raw"][i - 1][4]) if i > 0 else None,
                }
                for i, r in enumerate(entry.get("raw") or []) if len(r) >= 6
            }
            for sym, entry in (stock_payload.get("symbols") or {}).items()
        }
        rows = attach_forward_net(
            rows, calendar=forward_calendar, stock_bars=stock_bars, horizons=(3, 5, 10, 15),
        )
        summary["forward"] = summarize_forward(rows, horizons=(3, 5, 10, 15))
        summary["forward_net"] = summarize_net(rows, horizons=(3, 5, 10, 15))
        recon = {}
        for h in ("3", "5", "10", "15"):
            subset = [r for r in rows
                      if ((r.get("forward") or {}).get(h) or {}).get("status") == "ok"
                      and ((r.get("forward_net") or {}).get(h) or {}).get("status") == "ok"]
            if not subset:
                continue
            recon[f"T+{h}"] = {
                "n": len(subset),
                "stock_mean": round(sum(r["forward"][h]["stock_return"] for r in subset) / len(subset), 6),
                "board_mean": round(sum(r["forward"][h]["board_return"] for r in subset) / len(subset), 6),
                "excess_mean": round(sum(r["forward"][h]["excess"] for r in subset) / len(subset), 6),
                "net_mean": round(sum(r["forward_net"][h]["net_return"] for r in subset) / len(subset), 6),
            }
        summary["forward_reconciliation"] = recon
        summary["forward_calendar_end"] = forward_calendar[-1] if forward_calendar else None
        coverage = {}
        net_coverage = {}
        real_rows = [r for r in rows if r.get("link_status") != "placeholder_row"]
        for h in ("3", "5", "10", "15"):
            ok = sum(1 for r in real_rows
                     if ((r.get("forward") or {}).get(h) or {}).get("status") == "ok")
            coverage[h] = {"ok": ok, "unresolved": len(real_rows) - ok}
            ok_net = sum(1 for r in real_rows
                         if ((r.get("forward_net") or {}).get(h) or {}).get("status") == "ok")
            net_coverage[h] = {"ok": ok_net, "unresolved": len(real_rows) - ok_net}
        summary["forward_coverage"] = coverage
        summary["forward_net_coverage"] = net_coverage
        forward_note = "ok"
    summary["forward_note"] = forward_note
    summary.update({
        "start": start,
        "end": end,
        "days": days,
        "ledger_offline_mode": "archive（strength_lookup + 桶状态覆盖）",
        "no_candidate_expansion": True,
    })
    return {"summary": summary, "per_day": per_day, "links": rows}


def digest(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_md(payload: Dict[str, Any], sha: str) -> str:
    s = payload["summary"]
    lines = [
        f"# P3-a 个股—主题回链审计（{s['start']} → {s['end']}）",
        "",
        "只读回链：`candidate_pool` → board_code → episode/age → setup → 输出桶。"
        "**不生成候选、不改漏斗、无 buy 字段**；空占位行不计入映射率分母。",
        "",
        "## 汇总",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 归档日 | {len(s['days'])} |",
        f"| 候选行（含占位） | {s['candidates_total']} |",
        f"| 空占位行（剔除） | {s['placeholder_rows']} |",
        f"| 真实候选 | {s['candidates_real']} |",
        f"| 成功回链 | {s['linked']}（{s['linked_rate']}） |",
        f"| finalist | {s['finalists']} |",
        f"| 未匹配板块名 | {s['unmatched_count']} |",
        "",
        "## 可观测失真",
        "",
        "| 标记 | 行数 |",
        "|---|---|",
    ]
    for flag, count in s["flags"].items():
        lines.append(f"| {flag} | {count} |")
    lines += [
        "",
        "> `weak_ambush_board` = ambush 道且（板块破位 或 强度<40）；"
        "`strong_board_follower` 是代理指标（板块强度≥80 且非 finalist），"
        "不是个股板块内相对强度的直接度量。",
        "",
        "## 分道",
        "",
        "| 道 | 候选 | 回链 | finalist | 弱埋伏 | 平均板块强度 | 道别来源 |",
        "|---|---|---|---|---|---|---|",
    ]
    for lane, row in s["lanes"].items():
        lines.append(
            f"| {lane} | {row['count']} | {row['linked']} | {row['finalists']} | "
            f"{row['weak_ambush_board']} | {row['avg_strength']} | "
            f"{row.get('lane_sources') or {}} |"
        )
    lines += [
        "",
        "## 段 age 分布",
        "",
        "| 桶 | 行数 |",
        "|---|---|",
    ]
    for bucket, count in s["age_buckets"].items():
        lines.append(f"| {bucket} | {count} |")
    invisible_detail = s.get("invisible_detail") or []
    lines += [
        "",
        "## 回链到「不在任何输出桶」的板块（逐日）",
        "",
        "| 日期 | 板块 | 行数 |",
        "|---|---|---|",
    ]
    for item in invisible_detail:
        lines.append(f"| {item['trade_date']} | {item['sector_name']} | {item['count']} |")
    if not invisible_detail:
        lines.append("| — | 无 | 0 |")
    lines += [
        "",
        "## 未匹配板块名（前 30）",
        "",
        "```",
        "、".join(s["unmatched_names"]) or "无",
        "```",
        "",
        "## 未测项（本审计不覆盖）",
        "",
    ]
    for item in s["not_measured"]:
        lines.append(f"- {item}")
    fwd = s.get("forward") or {}
    if fwd:
        lines += [
            "",
            "## 事后评估：个股相对板块超额（收盘到收盘，仅评估不参与决策）",
            "",
            "> 超额 = 个股 T+h 收益 − 所属板块指数 T+h 收益；`positive_rate` = 超额>0 占比；"
            "CI 为日期簇 bootstrap。未到期/缺价记 unresolved，不计入分母。",
            "",
            f"覆盖率：{s.get('forward_coverage')}；日历止于 `{s.get('forward_calendar_end')}`。",
            "",
            "| horizon | 分组 | n | 均值 | 胜率(>0) | 95% CI |",
            "|---|---|---|---|---|---|",
        ]
        for h in ("3", "5", "10", "15"):
            for group, row in (fwd.get(h) or {}).items():
                lines.append(
                    f"| T+{h} | {group} | {row['count']} | {row['mean']} | "
                    f"{row['positive_rate']} | {row['ci95']} |"
                )
    fwd_net = s.get("forward_net") or {}
    if fwd_net:
        lines += [
            "",
            "## 候选级含成本净收益（次日开盘买 → T+h 收盘卖，仅评估）",
            "",
            "> **净收益是绝对收益，不是超额**；对照口径见下方 reconciliation。"
            "纳入双边费率（佣金/过户费/印花税）与滑点；涨停/停牌/缺前收 → unresolved_entry。"
            "费率不含最低佣金，避免用任意头寸规模制造假费用。",
            "",
            f"覆盖率：{s.get('forward_net_coverage')}。",
            "",
            "| 口径 | n | 股票毛收益 | 板块收益 | 超额 | 候选净收益 |",
            "|---|---|---|---|---|---|",
            *[
                f"| {k} | {v['n']} | {v['stock_mean']} | {v['board_mean']} | "
                f"{v['excess_mean']} | {v['net_mean']} |"
                for k, v in (s.get("forward_reconciliation") or {}).items()
            ],
            "",
            "| horizon | 分组 | n | 胜率(净>0) | 均值净收益 | 95% CI |",
            "|---|---|---|---|---|---|",
        ]
        for h in ("3", "5", "10", "15"):
            for group, row in (fwd_net.get(h) or {}).items():
                lines.append(
                    f"| T+{h} | {group} | {row['count']} | {row.get('win_rate')} | "
                    f"{row['mean']} | {row['ci95']} |"
                )
    lines += [
        "",
        "## 复现",
        "",
        f"- payload sha256：`{sha}`",
        f"- `python scripts/audit_theme_stock_link.py --start {s['start']} --end {s['end']}`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="P3-a 个股—主题回链审计")
    ap.add_argument("--start", default="20261008")
    ap.add_argument("--end", default="20261103")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    payload = build(args.start, args.end)
    sha = digest(payload)
    repro = sha == digest(build(args.start, args.end))

    out = Path(args.out) if args.out else (
        DAILY / f"theme_stock_link_{args.start}_{args.end}.json"
    )
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md = out.with_suffix(".md")
    md.write_text(render_md(payload, sha), encoding="utf-8")

    s = payload["summary"]
    print(f"归档日 {len(s['days'])}；候选 {s['candidates_real']}（占位 {s['placeholder_rows']}）；"
          f"回链 {s['linked']}（{s['linked_rate']}）；finalist {s['finalists']}")
    print(f"标记：{s['flags']}")
    print(f"分道：{s['lanes']}")
    if s.get("forward"):
        f5 = (s["forward"].get("5") or {}).get("all") or {}
        n5 = (s.get("forward_net", {}).get("5") or {}).get("all") or {}
        recon5 = (s.get("forward_reconciliation") or {}).get("T+5") or {}
        print(f"forward：{s.get('forward_coverage')}；T+5 股票毛 {recon5.get('stock_mean')} / "
              f"板块 {recon5.get('board_mean')} / 超额 {f5.get('mean')}（CI {f5.get('ci95')}）/ "
              f"候选净 {n5.get('mean')}（绝对收益，胜率 {n5.get('win_rate')}）")
    else:
        print(f"forward：{s.get('forward_note')}")
    print(f"复现：{'PASS' if repro else 'FAIL'}（{sha[:16]}）")
    print(f"JSON: {out}\nMD:   {md}")
    return 0 if repro else 3


if __name__ == "__main__":
    raise SystemExit(main())
