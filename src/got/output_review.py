# -*- coding: utf-8 -*-
"""批次 G（竞品经验补强计划 2026-10-06）：产出前运行时自查 gate（一期只 warn 不阻断）。

GOT 的教训谱系（候选池静默归零、垃圾桶族名"其他"流入输出、失败占位带非零仓位、决策
产物含 NaN/Inf、单维高分独裁）几乎每条都对应一次"产出物带病发布"，而回归全在 tests/
离线侧——每日真实产出前没有机械检查。本模块把这些既有缺陷**逐条编码为运行时检查**。

一期纪律（遵计划 §G）：
- **只 warn 不阻断**（`GOT_OUTPUT_REVIEW` 默认开）：检查项写日志 + 落 `review.json`，
  同步影子观测段。**绝不阻断主流程、绝不影响归档**——阻断态（拦推送/终端总结）留待
  两周零误报后人工对 critical 项启用；即便启用，**归档照常落盘**（portfolio_decision.json
  不得因 review 失败消失，守"跑过就有文件"不变式）。
- **防死分支**：只实装"在现存产物上可能触发"的检查。依赖 B 批次（counter_thesis 空率 /
  red_line_hit 不一致）的项在 B 落地前不实现（避免永不触发的空壳检查）。

检查项编号引 backlog §9 缺陷谱系，不用外部编号（遵计划）。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

REVIEW_ARTIFACT = "review.json"

# dimension_scores 量纲：composite_score 为 0~100，分项同为百分制（aggregator 侧口径）。
_HIGH_DIM = 80.0
_LOW_DIM = 40.0


def _finding(level: str, code: str, detail: str) -> Dict[str, str]:
    return {"level": level, "code": code, "detail": detail}


def _has_nonfinite(payload: Any) -> bool:
    """决策产物是否含 NaN/Infinity（json.dumps allow_nan=False 抛 ValueError 即为有）。"""
    try:
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        return False
    except ValueError:
        return True
    except (TypeError, OverflowError):
        return False


def review_phase2(decision: Any) -> List[Dict[str, str]]:
    """Phase 2 决策产出自查（一期 warn）。返回 findings 列表，不抛、不改决策。"""
    findings: List[Dict[str, str]] = []

    # G-C1 critical：status=failed 占位却带非零仓位/买单——违反"失败必须空"不变式
    # （fallback 硬编 allocation=[]/position_limit=0.0，本项是该契约的回归守卫，防未来被改坏）
    status = getattr(decision, "status", "")
    alloc = getattr(decision, "allocation", []) or []
    p_limit = getattr(getattr(decision, "risk_assessment", None), "position_limit", 0.0) or 0.0
    if status == "failed" and (alloc or p_limit > 0):
        findings.append(_finding(
            "critical", "G-C1_failed_nonzero",
            f"status=failed 却有 allocation={len(alloc)} 条 / position_limit={p_limit}（违反失败占位空约束）",
        ))

    # G-C2 critical：决策产物含 NaN/Inf（archive 会清洗为 null，但产出前带病仍是危险信号）
    try:
        payload = decision.model_dump(mode="python")
    except Exception:  # noqa: BLE001
        payload = {}
    if _has_nonfinite(payload):
        findings.append(_finding(
            "critical", "G-C2_nonfinite", "决策产物含 NaN/Infinity（下游会被清洗为 null，结论不可信）",
        ))

    # G-C3 critical：垃圾桶族名"其他"或空 sector 流入输出面（P2-4 专治的垃圾箱归属复发防护）
    bad_sector_hits = []
    for s in (getattr(decision, "unified_scoring", []) or []):
        sec = (getattr(s, "sector_name", "") or "").strip()
        if getattr(s, "is_holding", False) or getattr(s, "action_recommendation", "") in ("buy_trend", "buy_dip", "hold"):
            if sec == "其他" or sec == "":
                bad_sector_hits.append(f"{getattr(s, 'symbol', '?')}:{sec or '∅'}")
    if bad_sector_hits:
        findings.append(_finding(
            "critical", "G-C3_trashbin_sector",
            f"输出面出现垃圾桶/空板块名（{len(bad_sector_hits)} 只）：" + ", ".join(bad_sector_hits[:10]),
        ))

    # G-W1 warning：单维高分独裁（一维 ≥80 且其余维度多数 <40）——0824 独裁病谱系
    dictorators = []
    for s in (getattr(decision, "unified_scoring", []) or []):
        dims = getattr(s, "dimension_scores", {}) or {}
        vals = [v for v in dims.values() if isinstance(v, (int, float))]
        if len(vals) >= 3 and max(vals) >= _HIGH_DIM and sum(1 for v in vals if v < _LOW_DIM) >= len(vals) - 1:
            dictorators.append(getattr(s, "symbol", "?"))
    if dictorators:
        findings.append(_finding(
            "warning", "G-W1_single_dim_dictatorship",
            f"单维独裁（一维≥{_HIGH_DIM:.0f}、其余基本<{_LOW_DIM:.0f}）标的：{', '.join(dictorators[:10])}",
        ))

    # G-W2 warning：候选池归零率——复用 3a 落盘的 funnel_stats（D-3 基线），全归零打 warn
    fs = getattr(decision, "funnel_stats", None)
    if isinstance(fs, dict):
        rate = fs.get("zero_pick_rate")
        actionable = fs.get("actionable_sector_count")
        if actionable and rate is not None and rate >= 1.0:
            findings.append(_finding(
                "warning", "G-W2_pool_zero",
                f"候选池全归零（actionable={actionable}, zero_pick_rate={rate}）——0824 归零病复发信号",
            ))

    return findings


def review_phase1(contexts: Dict[str, Any]) -> List[Dict[str, str]]:
    """Phase 1 各节点 context 自查（一期 warn）。"""
    findings: List[Dict[str, str]] = []
    for key, ctx in contexts.items():
        status = getattr(ctx, "status", None)
        if status == "failed":
            findings.append(_finding("warning", "G-P1_node_failed", f"{key} 节点 status=failed"))
    return findings


def run_output_review(
    *,
    decision: Any,
    contexts: Dict[str, Any],
    target_date: date,
    enabled: bool,
) -> Dict[str, Any]:
    """执行自查并落 review.json（一期只 warn，绝不抛、绝不阻断）。未启用返回 {}。"""
    if not enabled:
        return {}
    findings = review_phase1(contexts) + review_phase2(decision)
    record = {
        "line": "output_review",
        "mode": "warn",  # 一期恒 warn；阻断态留待两周零误报后人工启用
        "date": target_date.strftime("%Y%m%d"),
        "critical": sum(1 for f in findings if f["level"] == "critical"),
        "warning": sum(1 for f in findings if f["level"] == "warning"),
        "findings": findings,
    }
    for f in findings:
        logf = logger.error if f["level"] == "critical" else logger.warning
        logf("[产出自查/%s] %s: %s", f["level"], f["code"], f["detail"])
    _write_artifact(target_date, record)
    return record


def _write_artifact(target_date: date, record: Dict[str, Any]) -> None:
    """原子落 review.json（失败静默，不阻断——归档本身不得因自查失败消失）。"""
    try:
        day_dir = Path("data/got_daily") / target_date.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / REVIEW_ARTIFACT
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:  # noqa: BLE001
        logger.warning("[产出自查] review.json 落盘失败（已忽略）: %s", exc)
