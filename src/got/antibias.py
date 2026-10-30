# -*- coding: utf-8 -*-
"""批次 B（竞品经验补强计划 2026-10-06）：轻量反偏见层 antibias（一期纯影子观测）。

对 Phase 2 产出的 actionable 板块做**一次**小 LLM 调用，逐板块输出：
- counter_thesis：「如果该板块 5 日内回撤 8%，最可能的 1 条原因」（镜子测试式最强反方论据）
- red_line_hit ：对照红线清单自报命中项（无出处利好 / 单维高分独裁 / 追高位置 / 数据缺失档位）

红线纪律（遵计划 §B）：
- **一期纯影子**：结果落 `data/got_daily/{date}/shadow_antibias.json`，**不参与排序、不进
  `decision.warnings`**（glue 降级日/回放日 counter_thesis 必空会结构性误报）。B2 转正后才
  升级 warnings（仍不否决）。`GOT_ANTIBIAS_SHADOW` 默认开（只观测，有 LLM 成本）。
- **职责主次**：`red_line_hit` 为 LLM 自报，与批次 G 机械判定重叠——定调 **G 为准**，本自报
  仅观测；二者不一致本身作为 G 的一条 LLM 校准 warning（见 output_review）。
- **落点选择**：计划原文写「SectorIntel 新字段」，但 SectorIntel 是 Node 6 产物、其 unified.json
  在 Phase 1 归档（早于 Phase 2），Phase 2 回填不会持久化 → 改用与 shadow_t1/meg/fts 同构的
  **独立影子产物**（零 schema 改动，规避死字段），这是既有影子线的成熟惯例。

转正前置（计划 §B1）：glue status=ok 且非降级/回放日，影子期 ≥10 交易日读数且
counter_thesis 非空率 >80%（由人工判定，本模块不代判）。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ANTIBIAS_ARTIFACT = "shadow_antibias.json"

# 红线清单（对照 output_review 机械判定；LLM 只能从中选，越界按 unknown 记）
RED_LINES = ("无出处利好", "单维高分独裁", "追高位置", "数据缺失档位")

_ACTIONABLE_ACTIONS = ("buy_trend", "buy_dip", "hold")

_PROMPT = """你是 A 股板块的反偏见审查员（唱反调角色）。下面是当日 actionable 板块，\
对每个板块给出**最强的一条反方论据**与红线自报，不要附和买入共识。

板块列表（含简要当日情报）：
{sector_lines}

对每个板块输出：
- counter_thesis：如果该板块 5 个交易日内回撤 8%，最可能的**一条**具体原因（须可证伪，不要"回调/波动"这类空话）
- red_line_hit：从 {red_lines} 中选该板块当前**最命中**的一条；都不命中则填 "无"

严格输出 JSON 数组（无其它文字）：
[{{"sector": "板块名", "counter_thesis": "...", "red_line_hit": "..."}}]"""


def _collect_actionable_sectors(decision: Any) -> List[str]:
    """从 Phase 2 决策里取 actionable 板块（持仓 + 买入/持有建议标的的板块名，去重保序）。"""
    seen: List[str] = []
    for s in (getattr(decision, "unified_scoring", []) or []):
        sec = (getattr(s, "sector_name", "") or "").strip()
        if not sec or sec == "其他":
            continue
        is_target = getattr(s, "is_holding", False) or \
            getattr(s, "action_recommendation", "") in _ACTIONABLE_ACTIONS
        if is_target and sec not in seen:
            seen.append(sec)
    return seen


def _parse_llm_json(text: str) -> Optional[List[Dict[str, Any]]]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, list) else None
    except (json.JSONDecodeError, ValueError):
        return None


def run_antibias(
    *,
    decision: Any,
    target_date: date,
    got_runtime: Any,
    shadow: bool,
    max_sectors: int,
) -> Optional[Dict[str, Any]]:
    """对 actionable 板块做一次反偏见 LLM 调用，落 shadow_antibias.json（纯观测）。

    无 actionable 板块 → 落一行 `{skipped_reason: no_actionable_target}` 观测产物
    （2026-10-15 拍板 §9 #7：空仓/全 watch 日计时器不累加必须留声息）。
    调用方须 try/except 包裹、不阻断主流程。
    """
    if not shadow:
        return None

    sectors = _collect_actionable_sectors(decision)
    if not sectors:
        record = {
            "line": "antibias",
            "mode": "shadow",
            "date": target_date.strftime("%Y%m%d"),
            "sectors_evaluated": 0,
            "counter_thesis_nonempty": 0,
            "skipped_reason": "no_actionable_target",
            "note": "空仓/全 watch 日：无持仓且无买入/持有标的，影子计时器本日不累加",
            "items": [],
        }
        _write_artifact(target_date, record)
        logger.info("[antibias] 无 actionable 板块，落 skipped 观测行")
        return record
    sectors = sectors[:max_sectors]

    sector_lines = "\n".join(f"- {i + 1}. {name}" for i, name in enumerate(sectors))
    prompt = _PROMPT.format(sector_lines=sector_lines, red_lines=" / ".join(RED_LINES))

    items: List[Dict[str, Any]] = []
    error = None
    try:
        step = got_runtime.call_text_sync(
            task_id="node7.antibias",
            prompt="你是反偏见审查员，只输出 JSON 数组。\n\n" + prompt,
            temperature=0.3,
            max_tokens=1500,
            timeout=45.0,
        )
        if step.status != "ok":
            error = f"llm_{step.status}: {step.error}"
            parsed = None
        else:
            parsed = _parse_llm_json(getattr(step, "output", "") or "")
        if parsed is None:
            if error is None:
                error = "llm_output_unparsed"
        else:
            by_sector = {}
            for m in parsed:
                if isinstance(m, dict) and m.get("sector"):
                    rl = m.get("red_line_hit", "")
                    by_sector[m["sector"]] = {
                        "counter_thesis": (m.get("counter_thesis") or "").strip(),
                        "red_line_hit": rl if rl in RED_LINES else ("无" if not rl else f"越界:{rl}"),
                    }
            for name in sectors:
                got = by_sector.get(name, {})
                items.append({
                    "sector": name,
                    "counter_thesis": got.get("counter_thesis", ""),
                    "red_line_hit": got.get("red_line_hit", ""),
                })
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"

    nonempty = sum(1 for it in items if it["counter_thesis"])
    record = {
        "line": "antibias",
        "mode": "shadow",  # 一期恒 shadow（不进排序/warnings）
        "date": target_date.strftime("%Y%m%d"),
        "sectors_evaluated": len(items),
        "counter_thesis_nonempty": nonempty,
        "red_lines_enum": list(RED_LINES),
        "llm_error": error,
        "promotion_precondition": "glue_ok 非降级/回放日 + 影子≥10交易日（仅计有买入/持有标的的交易日）+ counter_thesis 非空率>80%（人工判定）",
        "items": items,
    }
    _write_artifact(target_date, record)
    return record


def _write_artifact(target_date: date, record: Dict[str, Any]) -> None:
    """原子落 shadow_antibias.json（失败静默，不阻断——与 shadow_t1/meg/fts 同模式）。"""
    try:
        day_dir = Path("data/got_daily") / target_date.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / ANTIBIAS_ARTIFACT
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        logger.info("[antibias] 影子产物已落盘: %s（评估 %d 板块）", path, record["sectors_evaluated"])
    except OSError as exc:  # noqa: BLE001
        logger.warning("[antibias] 产物落盘失败（已忽略）: %s", exc)
