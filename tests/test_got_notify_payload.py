# -*- coding: utf-8 -*-
"""GOT 推送正文装配：摘要块 + reasoning.md 全文（2026-10-27 用户裁决）。

旧实现只推 9 行摘要（实测 507 B），reasoning.md（30 KB，Node 1–7 + 影子段）
从不进 push。本测试钉两件事：摘要块逐字保留、全文接通后用真实规模触发 ntfy 附件路径。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from src.got.notify_payload import (
    FULL_REPORT_SEPARATOR,
    build_got_notify_content,
    build_got_notify_summary,
)
from src.notification_sender.ntfy_sender import MAX_MESSAGE_BYTES

DAY = date(2026, 10, 27)


def _decision() -> SimpleNamespace:
    return SimpleNamespace(
        summary="仓位超限，减仓观望",
        risk_assessment=SimpleNamespace(
            position_limit=0.44,
            current_position_ratio=0.96,
            stop_loss_threshold=-0.05,
        ),
        allocation=[
            SimpleNamespace(
                action="sell", name="润建股份", symbol="002929", price_range=[55.8208, 56.96]
            ),
            SimpleNamespace(
                action="buy", name="国脉科技", symbol="002093", price_range=[7.7714, 8.1]
            ),
        ],
        rotation_signals=[],
        crowding_warnings=[],
        warnings=["触发止损线，需卖出 1 只股票"],
    )


def test_summary_block_kept_and_full_report_appended(tmp_path) -> None:
    report = "# GOT 运行推理汇总 - 2026-10-27\n\n" + "\n".join(
        f"## Node 段落 {i} — 板块强度/资金/情绪读数" for i in range(200)
    )
    report_path = tmp_path / "20261027" / "reasoning.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(report, encoding="utf-8")

    content = build_got_notify_content(_decision(), DAY, base_dir=tmp_path)

    summary_part, full_part = content.split(FULL_REPORT_SEPARATOR)
    assert summary_part == build_got_notify_summary(_decision(), DAY)
    assert summary_part.splitlines()[:2] == [
        "📊 GOT 每日决策 — 2026-10-27",
        "操作总结: 仓位超限，减仓观望",
    ]
    assert "风险: 仓位上限 44% / 当前 96% / 止损 -5%" in summary_part
    assert "  [SELL] 润建股份(002929) 区间 55.82~56.96" in summary_part
    assert "  [BUY] 国脉科技(002093) 区间 7.77~8.10" in summary_part
    assert "  [!] 触发止损线，需卖出 1 只股票" in summary_part

    assert full_part.startswith("# GOT 运行推理汇总 - 2026-10-27")
    assert full_part.rstrip().endswith("## Node 段落 199 — 板块强度/资金/情绪读数")

    # 真机规模（30 KB）必须越过 ntfy 单条上限 → 走「摘要 + 附件」分支
    assert len(content.encode("utf-8")) > MAX_MESSAGE_BYTES


def test_missing_report_falls_back_to_summary(tmp_path) -> None:
    content = build_got_notify_content(_decision(), DAY, base_dir=tmp_path)

    assert FULL_REPORT_SEPARATOR not in content
    assert "GOT 运行推理汇总" not in content
    assert content.startswith("📊 GOT 每日决策 — 2026-10-27")


def test_empty_report_not_appended(tmp_path) -> None:
    report_path = tmp_path / "20261027" / "reasoning.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("   \n\n", encoding="utf-8")

    content = build_got_notify_content(_decision(), DAY, base_dir=tmp_path)

    assert FULL_REPORT_SEPARATOR not in content
