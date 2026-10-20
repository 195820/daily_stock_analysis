# -*- coding: utf-8 -*-
"""Phase 2 失败必须以非零退出码收口（决策系统口径，2026-10-20 用户裁决）。

背景：orchestrator 在 Phase 2 异常时落 `status="failed"` 占位产物后继续返回，
main 读到占位产物仍打印并 `return 0` —— 调度器/终端看到"成功"，等于把
"静默清空"换成"静默无决策"。本文件钉死出口契约：

- decision 缺失 → 1
- `status == "failed"` → 1
- normal / partial → 0
"""

from types import SimpleNamespace

import main as main_module


def _decision(status: str, warnings=None):
    return SimpleNamespace(status=status, warnings=warnings or [], allocation=[])


def test_missing_decision_exits_nonzero(caplog) -> None:
    with caplog.at_level("ERROR"):
        assert main_module._phase2_exit_code(None) == 1
    assert "决策缺失" in caplog.text


def test_failed_placeholder_exits_nonzero_with_reason(caplog) -> None:
    decision = _decision("failed", ["Phase 2 失败: LLM 仲裁调用失败: timeout"])

    with caplog.at_level("ERROR"):
        assert main_module._phase2_exit_code(decision) == 1

    assert "status=failed" in caplog.text
    assert "LLM 仲裁调用失败" in caplog.text


def test_normal_and_partial_exit_zero() -> None:
    assert main_module._phase2_exit_code(_decision("normal")) == 0
    assert main_module._phase2_exit_code(_decision("partial")) == 0
