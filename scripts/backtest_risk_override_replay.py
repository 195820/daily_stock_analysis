#!/usr/bin/env python3
"""3b 分歧摘要（build_opinions/build_disagreement_summary）— 归档逐字节忠实重放。

**回测范围声明**：只读归档消费者，零生产改动、零 LLM。产出：
1. **Node 7 变体**（`portfolio_decision.py:5095-5110`）：归档 `sell_source_shadow` +
   `market_regime` + `confirmed_sectors` → 重算 `risk_override_shadow`，与归档逐字段比对
   （conflict_type / decision_path_hint / plan；mode、note 为运行态包装不比对）；
   veto_buy 恒 False 断言（卖出来源 mode 恒 shadow → risk_override_present=False）。
2. **Node 6 变体**（`aggregator.py:4214-4235`）：归档 unified/sector/capital/sentiment →
   SimpleNamespace 输入（regime.regime/cross_index_consistency、sector.active_mainline、
   capital.institutional_score、sentiment.fear_greed、SectorIntel.classification）→
   `memory=AgentMemory.from_config()` 重算 `conflict_summary_shadow`，逐字段比对
   （agents 三列表 / conflict_type / decision_path_hint / consensus·round3 /
   risk_control / risk_override_present / weights_equal）。
3. 11 类分类学生产分布 Counter + 11−观察 合成上下文包（标记「未见于生产样本」，
   人工审查分层材料——非生产数据，仅验证分类器可达性）。
4. 有效样本天数更正：verification.md 3b 判据节记 6 天 → 实测 **9 天**
   （20260928–20261015 双影子齐备；0704 起 archive 缺失不影响 3b 判据窗）。

数据卫生：unified.json 文本一律 `.replace('\\x00','')`；20260917 portfolio_decision.json
截断损坏 → try/except 跳过（其 unified.json 完好）。

使用：
    D:/ProgramData/miniconda3/envs/daily_stock_analysis/python.exe scripts/backtest_risk_override_replay.py
输出：`.review_tmp/backtest_risk_override_replay.md` + stdout 摘要。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.memory import AgentMemory  # noqa: E402
from src.got.disagreement import (  # noqa: E402
    build_disagreement_summary,
    build_opinions,
)

OUT_DIR = PROJECT_ROOT / ".review_tmp"
OUT_FILE = OUT_DIR / "backtest_risk_override_replay.md"
TAXONOMY = [
    "aligned_bullish", "partial_bullish_with_degraded_inputs", "bullish_with_risk_caution",
    "mixed_directional_signals", "mixed_with_degraded_inputs", "aligned_bearish",
    "partial_bearish_with_degraded_inputs", "aligned_neutral", "neutral_with_degraded_inputs",
    "degraded_only", "risk_override",
]


def _read(day: str, name: str) -> dict | None:
    p = PROJECT_ROOT / "data" / "got_daily" / day / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None  # 20260917 portfolio_decision.json 截断损坏 → 跳过


def _fmt(s: str) -> str:
    return (s or "").replace("\x00", "")


def build_inputs(day: str) -> dict:
    """归档 → 两变体重放输入。"""
    u = _read(day, "unified.json") or {}
    u = json.loads(json.dumps(u).replace("\\u0000", "")) if u else {}
    mr = u.get("market_regime") or {}
    intels = [SimpleNamespace(classification=(s or {}).get("classification") or "")
              for s in (u.get("confirmed_sectors") or [])]
    sector = SimpleNamespace(active_mainline=(_read(day, "sector.json") or {}).get("active_mainline") or [])
    cap = _read(day, "capital.json") or {}
    capital = SimpleNamespace(institutional_score=cap.get("institutional_score"))
    sent = _read(day, "sentiment.json") or {}
    # 忠实复现生产失配：build_opinions 读 `sentiment.fear_greed`，而 Node 6 传入的
    # SentimentContext 字段名是 `fear_greed_index`（schemas.py:631；`fear_greed` 在
    # MarketRegime 上，schemas.py:719）→ 生产 getattr 恒 None → sentiment 意见恒降级。
    # 重放只提供 fear_greed_index（不造 fear_greed 属性），保持与生产逐字节同参。
    sentiment = SimpleNamespace(fear_greed_index=sent.get("fear_greed_index"))
    regime = SimpleNamespace(regime=mr.get("regime"), cross_index_consistency=mr.get("cross_index_consistency"))
    pdj = _read(day, "portfolio_decision.json") or {}
    ss = pdj.get("sell_source_shadow") or {}
    return {
        "regime": regime, "sector": sector, "capital": capital, "sentiment": sentiment,
        "sector_intels": intels, "sell_source": ss or None,
        "archived_css": u.get("conflict_summary_shadow"),
        "archived_ros": pdj.get("risk_override_shadow"),
    }


def _pack(ops: list) -> list[dict]:
    return [{"agent_name": o.agent_name, "signal": o.signal, "confidence": round(o.confidence, 3)} for o in ops]


def _dir(op) -> str:
    if op.signal == "buy":
        return "bull"
    if op.signal == "sell":
        return "bear"
    return "neutral"


def _diff(replay: dict, archived: dict, fields: list[str]) -> list[str]:
    ds: list[str] = []
    for f in fields:
        rv, av = replay.get(f), archived.get(f)
        if isinstance(rv, float) or isinstance(av, float):
            rv, av = (round(float(rv), 3) if rv is not None else None,
                      round(float(av), 3) if av is not None else None)
        if rv != av:
            ds.append(f"{f}: replay={rv!r} vs archived={av!r}")
    return ds


def main() -> int:
    root = PROJECT_ROOT / "data" / "got_daily"
    days = sorted(d.name for d in root.iterdir()
                  if re.fullmatch(r"20\d{6}", d.name) and (_read(d.name, "unified.json") or {}).get("conflict_summary_shadow"))
    if not days:
        print("无 conflict_summary_shadow 归档")
        return 1

    node7_rows: list[dict] = []
    node6_rows: list[dict] = []
    taxon = Counter()
    veto_all_false = True

    for day in days:
        inp = build_inputs(day)

        # ── Node 7 变体（risk_override_shadow）───────────────────────────
        ro_present = (inp["sell_source"] or {}).get("mode") == "enabled" and bool((inp["sell_source"] or {}).get("planned_trims"))
        ops7 = build_opinions(
            regime=inp["regime"], sector=None, sector_intels=inp["sector_intels"],
            capital=None, sentiment=None, sell_source=inp["sell_source"],
        )
        sum7 = build_disagreement_summary(ops7, risk_override_present=ro_present, memory=None)
        taxon[sum7["conflict_type"]] += 1
        plan7 = sum7["risk_control"]
        if plan7.get("veto_buy"):
            veto_all_false = False
        arc7 = inp["archived_ros"] or {}
        d7 = _diff(
            {"conflict_type": sum7["conflict_type"], "decision_path_hint": sum7["decision_path_hint"], "plan": plan7},
            {"conflict_type": arc7.get("conflict_type"), "decision_path_hint": arc7.get("decision_path_hint"), "plan": arc7.get("plan")},
            ["conflict_type", "decision_path_hint", "plan"],
        )
        node7_rows.append({"day": day, "match": not d7, "diff": d7,
                           "ct": sum7["conflict_type"], "trims": len((inp["sell_source"] or {}).get("planned_trims") or [])})

        # ── Node 6 变体（conflict_summary_shadow）────────────────────────
        # E3 接线（2026-10-15）：memory 由 None 改 AgentMemory.from_config()——
        # 归档 weights_equal 佐证（0928–1014=True 等权时代，1015 起=False）。
        # 忠实重放按日期选 memory；判据读数起点 2026-10-15（此前等权读数作废）。
        mem6 = AgentMemory.from_config() if day >= "20261015" else None
        ops6 = build_opinions(
            regime=inp["regime"], sector=inp["sector"], sector_intels=inp["sector_intels"],
            capital=inp["capital"], sentiment=inp["sentiment"], sell_source=None,
        )
        sum6 = build_disagreement_summary(ops6, risk_override_present=False, memory=mem6)
        taxon[sum6["conflict_type"]] += 1
        arc6 = inp["archived_css"] or {}
        replay6 = {
            "bullish_agents": _pack([o for o in ops6 if _dir(o) == "bull"]),
            "bearish_agents": _pack([o for o in ops6 if _dir(o) == "bear"]),
            "neutral_agents": _pack([o for o in ops6 if _dir(o) == "neutral"]),
            "conflict_type": sum6["conflict_type"],
            "decision_path_hint": sum6["decision_path_hint"],
            "risk_override_present": False,
            "weights_equal": mem6 is None,  # 0928–1014 等权时代 / 1015 起先验权重
            "consensus": sum6["consensus"],
            "risk_control": sum6["risk_control"],
        }
        d6: list[str] = []
        for f in ("bullish_agents", "bearish_agents", "neutral_agents", "conflict_type",
                  "decision_path_hint", "risk_override_present", "weights_equal", "risk_control"):
            if replay6[f] != arc6.get(f):
                d6.append(f"{f}: replay={replay6[f]!r} vs archived={arc6.get(f)!r}")
        cons_r, cons_a = sum6.get("consensus") or {}, arc6.get("consensus") or {}
        for k in ("weighted_score", "confidence", "signal"):
            rv, av = cons_r.get(k), cons_a.get(k)
            if isinstance(rv, (int, float)) and isinstance(av, (int, float)):
                if round(float(rv), 3) != round(float(av), 3):
                    d6.append(f"consensus.{k}: replay={rv!r} vs archived={av!r}")
            elif rv != av:
                d6.append(f"consensus.{k}: replay={rv!r} vs archived={av!r}")
        node6_rows.append({"day": day, "match": not d6, "diff": d6, "ct": sum6["conflict_type"]})

    n7_ok = sum(1 for r in node7_rows if r["match"])
    n6_ok = sum(1 for r in node6_rows if r["match"])
    observed = sorted(taxon)
    unseen = [c for c in TAXONOMY if c not in observed]

    # ── 合成上下文包（11−观察；「未见于生产样本」，分类器可达性验证）────────
    packs: list[dict] = []
    mk = {
        "aligned_bullish": dict(regime=("bull", 0.8), mainline=3, inst=0.5, fg=0.5, actionable=3),
        "partial_bullish_with_degraded_inputs": dict(regime=("bull", 0.8), mainline=3, inst=None, fg=0.5, actionable=3),
        "bullish_with_risk_caution": dict(regime=("bull", 0.8), mainline=3, inst=0.5, fg=0.9, actionable=3),
        "mixed_directional_signals": dict(regime=("bear", 0.8), mainline=3, inst=0.5, fg=0.5, actionable=3),
        "mixed_with_degraded_inputs": dict(regime=("bear", 0.8), mainline=3, inst=None, fg=0.5, actionable=3),
        "aligned_bearish": dict(regime=("bear", 0.8), mainline=0, inst=-0.5, fg=0.5, actionable=0),
        "partial_bearish_with_degraded_inputs": dict(regime=("bear", 0.8), mainline=0, inst=-0.5, fg=None, actionable=0),
        "aligned_neutral": dict(regime=("transition", 0.5), mainline=0, inst=0.0, fg=0.5, actionable=0),
        "neutral_with_degraded_inputs": dict(regime=("transition", 0.5), mainline=0, inst=0.0, fg=None, actionable=0),
        "degraded_only": dict(regime=None, mainline=None, inst=None, fg=None, actionable=None),
    }

    def synth(cfg: dict) -> dict:
        reg = None
        if cfg["regime"]:
            reg = SimpleNamespace(regime=cfg["regime"][0], cross_index_consistency=cfg["regime"][1])
        return {
            "regime": reg,
            "sector": None if cfg["mainline"] is None else SimpleNamespace(active_mainline=[f"主线{i}" for i in range(cfg["mainline"])]),
            "capital": None if cfg["inst"] is None else SimpleNamespace(institutional_score=cfg["inst"]),
            "sentiment": None if cfg["fg"] is None else SimpleNamespace(fear_greed=cfg["fg"]),
            "sector_intels": None if cfg["actionable"] is None else [
                SimpleNamespace(classification="actionable") for _ in range(cfg["actionable"])],
            "sell_source": None,
        }

    for ct in unseen:
        if ct == "risk_override":
            ops = build_opinions(regime=SimpleNamespace(regime="bull", cross_index_consistency=0.8),
                                 sell_source={"mode": "enabled", "planned_trims": [{"symbol": "600000"}]})
            s = build_disagreement_summary(ops, risk_override_present=True, memory=None)
        else:
            cfg, inp = mk[ct], synth(mk[ct])
            ops = build_opinions(regime=inp["regime"], sector=inp["sector"], sector_intels=inp["sector_intels"],
                                 capital=inp["capital"], sentiment=inp["sentiment"], sell_source=None)
            s = build_disagreement_summary(ops, risk_override_present=False, memory=AgentMemory.from_config())
        ok = s["conflict_type"] == ct
        packs.append({"ct": ct, "reached": ok, "got": s["conflict_type"],
                      "veto": s["risk_control"].get("veto_buy")})

    OUT_DIR.mkdir(exist_ok=True)
    lines = [
        "# 3b 分歧摘要 — 归档忠实重放台账（Node 6 + Node 7 双变体）",
        "",
        f"- 生成：{date.today().isoformat()}｜纯函数直读复用（build_opinions/build_disagreement_summary），"
        f"Node 6 memory=AgentMemory.from_config()、Node 7 memory=None 与生产同参",
        f"- **有效样本天数 = {len(days)} 天**（{days[0]}–{days[-1]}，conflict_summary_shadow + risk_override_shadow 双影子齐备）"
        f"——**verification.md 3b 判据节「6 天」系读数笔误，应更正为 {len(days)} 天**",
        "",
        "## Node 7 变体（risk_override_shadow）",
        "",
        f"| 日 | 归档 conflict_type | 比对 |",
        f"|---|---|---|",
    ]
    for r in node7_rows:
        lines.append(f"| {r['day']} | {r['ct']}（sell_trims={r['trims']}） | {'✅ 一致' if r['match'] else '❌ ' + '; '.join(r['diff'])} |")
    lines += [
        "",
        f"**{n7_ok}/{len(node7_rows)} 一致**｜veto_buy 恒 False：{'✅（卖出来源 mode 恒 shadow）' if veto_all_false else '❌ 出现 True'}",
        "",
        "## Node 6 变体（conflict_summary_shadow）",
        "",
        f"| 日 | 归档 conflict_type | 比对 |",
        f"|---|---|---|",
    ]
    for r in node6_rows:
        lines.append(f"| {r['day']} | {r['ct']} | {'✅ 一致' if r['match'] else '❌ ' + '; '.join(r['diff'])} |")
    lines += [
        "",
        f"**{n6_ok}/{len(node6_rows)} 一致**（逐字段：agents×3 / conflict_type / hint / consensus·round3 / risk_control / flags）",
        "",
        "## 11 类分类学生产分布",
        "",
        f"| conflict_type | 次数（Node6+Node7 合计） |",
        f"|---|---|",
    ]
    for ct, n in taxon.most_common():
        lines.append(f"| {ct} | {n} |")
    lines += [
        f"（生产 9 日 × 2 变体 = {sum(taxon.values())} 次重放，仅覆盖 {len(observed)}/11 类）",
        "",
        "## 合成上下文包（未见于生产样本，人工审查分层材料）",
        "",
        f"| conflict_type | 构造 | 复算命中 | veto_buy |",
        f"|---|---|---|---|",
    ]
    for p in packs:
        hit = "✅" if p["reached"] else f"❌ 得到 {p['got']}"
        lines.append(f"| {p['ct']} | {'Node6 同参' if p['ct'] != 'risk_override' else 'sell_source enabled+trims'} "
                     f"| {hit} | {p['veto']} |")
    lines += [
        "",
        "## 判读",
        "",
        f"> Node 7 逐字节重放 {n7_ok}/{len(node7_rows)}、Node 6 {n6_ok}/{len(node6_rows)}；"
        f"生产分布集中在 degraded 系（partial_bullish_with_degraded 为主）：资金/情绪维度的"
        f"数据降级是常态输入形态，合成包补齐其余 {len(unseen)} 类供人工审查分类器语义。"
        f"3b 影子窗 10 日判据（不一致率 ≤2/9→现 {len(days) - n6_ok} 差异）与零决策红线照常生产执行。",
    ]
    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")

    print(f"[OK] 有效样本 {len(days)} 天（{days[0]}–{days[-1]}）")
    print(f"[OK] Node 7 risk_override_shadow: {n7_ok}/{len(node7_rows)} 一致｜veto_buy 恒 False={veto_all_false}")
    print(f"[OK] Node 6 conflict_summary_shadow: {n6_ok}/{len(node6_rows)} 一致")
    print(f"[OK] 分类学分布 {dict(taxon)}")
    print(f"[OK] 合成包 {len(packs)} 个（未观察类），命中 {sum(1 for p in packs if p['reached'])}/{len(packs)}")
    print(f"[OK] 报告 → {OUT_FILE.relative_to(PROJECT_ROOT)}")
    return 0 if (n7_ok == len(node7_rows) and n6_ok == len(node6_rows)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
