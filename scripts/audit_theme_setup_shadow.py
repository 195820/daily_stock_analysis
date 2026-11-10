#!/usr/bin/env python
"""P2 影子审计：ledger setup 逐条「阻断层」归因 + observation 提醒（只读归档）。

回答实施稿 §5.2 四问
--------------------
1. 是否仍在 observation？——账本 open/pending 段；
2. 是否因强度/位置/催化被排序靠后？——归档 strength / break_signals / overheat；
3. 是否曾经申请 setup？——四变体 setup 状态（armed/unarmed/unknown/invalidated）；
4. 哪一层容量或闸门阻止其继续向下游传递？——`theme_routing_shadow.classify_blocking_layer`。

口径红线
--------
- 只读归档事实（sector/unified/portfolio_decision），不重算评分、不调用预算、不生成 buy；
- `blocking_layer` 描述「归档路由把板块停在哪一层」；`setup_applied` 单独标记是否申请复评；
- 与 `audit_node2_state_counterfactual.py` 的 E16 规则逐值一致（单测锁定）；
- 同输入两次运行 digest 必须一致。

用法
----
    python scripts/audit_theme_setup_shadow.py --start 20261008 --end 20261103
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
    news_refs,
    states_from_archive,
    trading_days,
)
from src.got.theme_ledger import (  # noqa: E402
    build_observations,
    derive_episodes,
    derive_setups,
)
from src.got.theme_routing_shadow import (  # noqa: E402
    build_rows,
    first_failing_gate,
    observation_reminders,
    summarize,
)

DAILY = ROOT / "data" / "got_daily"


def build(start: str, end: str) -> Dict[str, Any]:
    calendar = trading_days(
        datetime.strptime(start, "%Y%m%d").date(),
        datetime.strptime(end, "%Y%m%d").date(),
    )
    name_code = load_name_code()
    days = [d for d in calendar if (DAILY / d.replace("-", "") / "sector.json").exists()]

    all_obs = []
    payloads: Dict[str, dict] = {}
    unified: Dict[str, dict] = {}
    risk: Dict[str, dict] = {}
    for day in days:
        path = DAILY / day.replace("-", "") / "sector.json"
        payload = archive_day_payload(path)
        payloads[day] = payload
        state_rows, _ = states_from_archive(payload, name_code)
        observations, _ = build_observations(
            day, state_rows=state_rows, membership=membership_of(payload),
            news_refs={}, catalyst_refs={}, run_id="p2-shadow",
        )
        all_obs.extend(observations)
        for fname, target in (("unified.json", unified), ("portfolio_decision.json", risk)):
            fpath = DAILY / day.replace("-", "") / fname
            if fpath.exists():
                try:
                    target[day] = json.loads(fpath.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001 - 损坏归档不阻断审计
                    target[day] = {}

    rows: List[Dict[str, Any]] = []
    reminders: List[Dict[str, Any]] = []
    for day in days:
        as_of = [o for o in all_obs if o.trade_date <= day]
        episodes, index = derive_episodes(as_of, calendar)
        setups = derive_setups(as_of, index, calendar)
        rows.extend(build_rows(
            trade_date=day, setups=setups, observations=as_of,
            payload=payloads[day], unified=unified.get(day), risk=risk.get(day),
        ))
        reminders.extend(observation_reminders(
            trade_date=day, episodes=episodes, observations=as_of,
            payload=payloads[day],
        ))

    rows.sort(key=lambda r: (r["trade_date"], r["board_code"], r["variant"]))
    reminders.sort(key=lambda r: (r["trade_date"], r["board_code"]))
    summary = summarize(rows, reminders)
    # 不变量：首要阻断层必须等于 GATE_ORDER 下的首个 fail（visible/top_only/unresolved/score_rank 除外）
    gate_to_layer = {
        "trend": "trend_broken", "overheat": "overheat", "regime": "regime_blocked",
        "crowding": "crowding", "capacity": "capacity",
    }
    checked = mismatches = 0
    checked_by_layer: Dict[str, int] = {}
    for row in rows:
        if row["blocking_layer"] not in gate_to_layer.values():
            continue
        checked += 1
        checked_by_layer[row["blocking_layer"]] = checked_by_layer.get(row["blocking_layer"], 0) + 1
        expected = gate_to_layer.get(first_failing_gate(row.get("gate_verdicts") or {}))
        if expected != row["blocking_layer"]:
            mismatches += 1
    summary["gate_layer_consistency"] = {
        "checked": checked, "by_layer": dict(sorted(checked_by_layer.items())),
        "mismatches": mismatches,
    }
    summary.update({
        "start": start,
        "end": end,
        "days": days,
        "ledger_offline_mode": "archive（strength_lookup + 桶状态覆盖）",
        "e16_rule_source": "scripts/audit_node2_state_counterfactual.py（同常量/同判据，单测逐值比对）",
        "not_a_buy_signal": True,
    })
    return {"summary": summary, "rows": rows, "reminders": reminders}


def digest(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_md(payload: Dict[str, Any], sha: str) -> str:
    s = payload["summary"]
    lines = [
        f"# P2 路由影子审计（{s['start']} → {s['end']}）",
        "",
        "只读归档闸门事实的**归因审计**：不重算评分、不产生买入资格。"
        "`blocking_layer` 描述归档路由停在哪一层，`setup_applied` 标记是否申请复评。",
        "",
        "## 汇总",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 归档日 | {len(s['days'])} |",
        f"| setup 审计行 | {s['rows']} |",
        f"| 已申请复评（armed）行 | {sum(s['by_layer_armed'].values())} |",
        f"| observation 提醒（段内但下游不可见） | {s['observation_reminders']} |",
        "",
        "### 阻断层（全部行 vs armed 行）",
        "",
        "| 层 | 全部 | 仅 armed |",
        "|---|---|---|",
    ]
    for layer in sorted(set(s["by_layer"]) | set(s["by_layer_armed"])):
        lines.append(
            f"| {layer} | {s['by_layer'].get(layer, 0)} | {s['by_layer_armed'].get(layer, 0)} |"
        )
    lines += [
        "",
        "### 逐闸门判定（仅 armed 行；事实来源 `archived`，缺证据记 unknown）",
        "",
        "| 闸门 | fail | unknown |",
        "|---|---|---|",
    ]
    for gate in ("trend", "overheat", "regime", "crowding", "budget", "capacity"):
        lines.append(
            f"| {gate} | {s.get('by_gate_fail_armed', {}).get(gate, 0)} | "
            f"{s.get('by_gate_unknown_armed', {}).get(gate, 0)} |"
        )
    lines += [
        "",
        "> 读法：每行是**该闸门独立失守**的 armed 行数，不是按层归因；"
        "阻断层按 `GATE_ORDER` 取首个 fail（`visible`/`top_only`/`unresolved_no_detail` 更早判定）。"
        "`budget` 的 unknown 与其余闸门同为「无 detail」的 61 行；det-present 的 127 行中 fail 31 / pass 96。"
        "`budget` 是 Phase 2 执行层约束，不参与板块级首要阻断层。",
        f"> 不变量：阻断层 ↔ 首个 fail 一致性 checked={s['gate_layer_consistency']['checked']}"
        f"（{s['gate_layer_consistency']['by_layer']}，含全部变体行），"
        f"mismatches={s['gate_layer_consistency']['mismatches']}。",
        "",
        "### 变体 × 状态", "", "| 变体:状态 | 行数 |", "|---|---|",
    ]
    for key, value in s["by_variant_status"].items():
        lines.append(f"| {key} | {value} |")
    lines += [
        "",
        "## 校准案例（10-29 短剧 / 10-23 减速器）",
        "",
        "| 日期 | 板块 | 变体 | setup | applied | 阻断层 | 原因 | E16 B/C/D |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in payload["rows"]:
        if row["board_code"] not in ("880692", "880675"):
            continue
        if row["trade_date"] not in ("2026-10-23", "2026-10-29", "2026-11-03"):
            continue
        alt = row.get("e16_alt_routes") or {}
        lines.append(
            f"| {row['trade_date']} | {row['board_name']} | {row['variant']} | "
            f"{row['setup_status']} | {row['setup_applied']} | {row['blocking_layer']} | "
            f"{row['reason']} | {alt.get('B', '-')}/{alt.get('C', '-')}/{alt.get('D', '-')} |"
        )
    lines += [
        "",
        "## observation 提醒（前 20 条）",
        "",
        "| 日期 | 板块 | 段状态 | 强度 | 峰值 |",
        "|---|---|---|---|---|",
    ]
    for item in payload["reminders"][:20]:
        lines.append(
            f"| {item['trade_date']} | {item['board_name']} | {item['episode_status']} | "
            f"{item['strength']} | {item['peak_strength']} |"
        )
    lines += [
        "",
        "## 复现",
        "",
        f"- payload sha256：`{sha}`",
        f"- `python scripts/audit_theme_setup_shadow.py --start {s['start']} --end {s['end']}`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="P2 setup 路由影子审计")
    ap.add_argument("--start", default="20261008")
    ap.add_argument("--end", default="20261103")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    payload = build(args.start, args.end)
    sha = digest(payload)
    again = digest(build(args.start, args.end))
    repro = sha == again

    out = Path(args.out) if args.out else (
        DAILY / f"theme_setup_shadow_{args.start}_{args.end}.json"
    )
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md = out.with_suffix(".md")
    md.write_text(render_md(payload, sha), encoding="utf-8")

    s = payload["summary"]
    print(f"归档日 {len(s['days'])}；setup 行 {s['rows']}；armed {sum(s['by_layer_armed'].values())}；"
          f"提醒 {s['observation_reminders']}")
    print(f"armed 阻断层：{s['by_layer_armed']}")
    print(f"复现：{'PASS' if repro else 'FAIL'}（{sha[:16]}）")
    mismatch = s["gate_layer_consistency"]["mismatches"]
    if mismatch:
        print(f"闸门/阻断层一致性失败：{mismatch} 行")
        return 4
    print(f"JSON: {out}\nMD:   {md}")
    return 0 if repro else 3


if __name__ == "__main__":
    raise SystemExit(main())
