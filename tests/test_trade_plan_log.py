# -*- coding: utf-8 -*-
"""决策编排层输出留痕（trade_plan_log，2026-10-20）。

覆盖三段契约：
1. `decision_plan.reconcile_log_lines` / `assemble_log_lines` 三态输出（失败/空/有记录）；
2. `archive._append_phase2_reasoning` 把 `decision.trade_plan_log` 渲染进 Node 7 段，
   同日重跑段落数恒为 1（幂等，随 "## Node 7 交易建议" 一并丢弃重建）；
3. `trade_plan_log` 为空（缺省/未接线）时 reasoning.md 不出现该段（off 态零扰动）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.got.decision_plan import assemble_log_lines, reconcile_log_lines
from src.got.archive import SHADOW_SECTION_TITLE, _append_phase2_reasoning

DAY = "2026-10-20"
HEADER = "### 决策编排层（trade_plans 装配/对账）"


def _plan(**kw) -> SimpleNamespace:
    defaults = dict(
        lane="mainline", symbol="600001", action="buy", target_quantity=1100.0,
        trigger_price=9.8, execution_status="pending", valid_until="2026-10-23",
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# ─────────────────────────── 1. 纯函数三态 ───────────────────────────

def test_reconcile_log_error_line() -> None:
    lines = reconcile_log_lines("2026-10-19", [], error="db locked")
    assert lines == ["昨日对账(2026-10-19)失败（不阻断）: db locked"]


def test_reconcile_log_empty_pending() -> None:
    assert reconcile_log_lines("2026-10-19", []) == [
        "昨日对账(2026-10-19): 无 pending 计划待回填"
    ]


def test_reconcile_log_records() -> None:
    updated = [
        _plan(lane="ambush", symbol="600003", action="buy", execution_status="executed"),
        _plan(lane="holding_reduce", symbol="600004", action="reduce",
              execution_status="expired"),
    ]
    lines = reconcile_log_lines("2026-10-19", updated)
    assert lines == [
        "昨日对账(2026-10-19): 2 条状态回填",
        "- [ambush] 600003 buy → executed",
        "- [holding_reduce] 600004 reduce → expired",
    ]


def test_assemble_log_error_line() -> None:
    assert assemble_log_lines([], error="no table") == [
        "当日计划装配失败（不阻断）: no table"
    ]


def test_assemble_log_empty_allocation() -> None:
    assert assemble_log_lines([]) == ["当日计划装配: allocation 为空，无计划写入"]


def test_assemble_log_records() -> None:
    written = [
        _plan(),
        _plan(lane="ambush", symbol="600003", action="buy", target_quantity=600.0,
              trigger_price=None, execution_status="pending", valid_until="2026-10-23"),
    ]
    lines = assemble_log_lines(written)
    assert lines == [
        "当日计划装配: 2 条写入 trade_plans",
        "- [mainline] 600001 buy 1100股 @触发 ¥9.80 → pending（有效至 2026-10-23）",
        "- [ambush] 600003 buy 600股 @触发 — → pending（有效至 2026-10-23）",
    ]


# ─────────────────────────── 2. reasoning.md 渲染 ───────────────────────────

def _decision(trade_plan_log) -> SimpleNamespace:
    """最小决策壳（_append_phase2_reasoning 只读这些属性）。"""
    return SimpleNamespace(
        reasoning="本次不做调整。", allocation=[], warnings=[], summary="",
        ambush_exit_signals=[], trade_plan_log=trade_plan_log,
    )


def _prepare(tmp_path):
    day_dir = tmp_path / DAY.replace("-", "")
    day_dir.mkdir(parents=True, exist_ok=True)
    reasoning_path = day_dir / "reasoning.md"
    reasoning_path.write_text(
        "# GOT 运行推理汇总\n\n## Node 6 聚合研判\n\n正文\n", encoding="utf-8"
    )
    return reasoning_path


def test_trade_plan_log_rendered_after_allocation(tmp_path) -> None:
    reasoning_path = _prepare(tmp_path)
    log = reconcile_log_lines("2026-10-19", [_plan(execution_status="executed")])
    log += assemble_log_lines([_plan()])

    _append_phase2_reasoning(reasoning_path, _decision(log))
    text = reasoning_path.read_text(encoding="utf-8")

    assert HEADER in text
    assert "昨日对账(2026-10-19): 1 条状态回填" in text
    assert "当日计划装配: 1 条写入 trade_plans" in text
    # 位置：Node 7 段内、影子段之前
    assert text.index("## Node 7 交易建议") < text.index(HEADER)
    assert text.index(HEADER) < text.index(SHADOW_SECTION_TITLE)


def test_trade_plan_log_not_duplicated_on_rerun(tmp_path) -> None:
    reasoning_path = _prepare(tmp_path)
    log = assemble_log_lines([_plan()])

    _append_phase2_reasoning(reasoning_path, _decision(log))
    _append_phase2_reasoning(reasoning_path, _decision(assemble_log_lines([_plan()])))
    text = reasoning_path.read_text(encoding="utf-8")

    assert text.count(HEADER) == 1
    assert text.count("当日计划装配: 1 条写入 trade_plans") == 1


def test_trade_plan_log_absent_when_empty(tmp_path) -> None:
    reasoning_path = _prepare(tmp_path)

    _append_phase2_reasoning(reasoning_path, _decision([]))
    text = reasoning_path.read_text(encoding="utf-8")

    assert HEADER not in text
    assert "trade_plans" not in text


def test_missing_field_does_not_render(tmp_path) -> None:
    """未接线形态（decision 无 trade_plan_log 属性）→ 不渲染、不抛错"""
    reasoning_path = _prepare(tmp_path)
    decision = _decision(None)
    del decision.trade_plan_log

    _append_phase2_reasoning(reasoning_path, decision)
    assert HEADER not in reasoning_path.read_text(encoding="utf-8")
