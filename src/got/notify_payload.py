# -*- coding: utf-8 -*-
"""GOT 每日推送内容装配（main.py --got 通知 hook 的 payload 构造）。

正文 = 固定摘要块（操作总结 / 风险三数 / Top3 建议区间 / 轮动 / 拥挤 / warnings）
     + 当日 ``reasoning.md`` 全文（Node 1–7 推理 + 影子模式观测段）。

为什么要塞全文：摘要块实测只有 0.5 KB，`decision.reasoning`（Phase 2 LLM 精炼）
在旧实现里连终端都打了却没进 push，Node 1–6 的推理与影子读数更是只落归档。
NtfySender 对 >3800 B 的正文自动改「摘要 + 附件」（公共 ntfy.sh 单附件 2 MB / 3 h 过期），
飞书 / 企微 / Telegram / Slack 的 sender 均已实现分段，故整段塞进来是安全的；
归档缺失时退化为纯摘要，绝不阻断推送。
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional

from src.got.archive import _default_base_dir

logger = logging.getLogger(__name__)

FULL_REPORT_SEPARATOR = "\n\n---\n\n"


def _reasoning_md_path(run_date: date, base_dir: Optional[Path] = None) -> Path:
    """当日 reasoning.md 路径；归档根目录复用 archive 的唯一来源。"""
    return (base_dir or _default_base_dir()) / run_date.strftime("%Y%m%d") / "reasoning.md"


def build_got_notify_summary(decision, run_date: date) -> str:
    """结论块（推送消息正文）：操作总结 + 风险三数 + Top3 建议 + 轮动/拥挤/warnings。"""
    lines = [f"📊 GOT 每日决策 — {run_date.isoformat()}", f"操作总结: {decision.summary}"]

    risk = decision.risk_assessment
    lines.append(
        f"风险: 仓位上限 {risk.position_limit:.0%} / 当前 {risk.current_position_ratio:.0%} "
        f"/ 止损 {risk.stop_loss_threshold:.0%}"
    )

    top_allocation = decision.allocation[:3]
    if top_allocation:
        lines.append(f"\n交易建议 Top {len(top_allocation)}:")
        for alloc in top_allocation:
            action_tag = {"buy": "BUY", "sell": "SELL", "hold": "HOLD"}.get(alloc.action, "?")
            lines.append(
                f"  [{action_tag}] {alloc.name}({alloc.symbol}) "
                f"区间 {alloc.price_range[0]:.2f}~{alloc.price_range[1]:.2f}"
            )

    if decision.rotation_signals:
        lines.append(f"\n轮动信号 ({len(decision.rotation_signals)}):")
        for signal in decision.rotation_signals[:3]:
            lines.append(f"  [{signal['type']}] {signal['reason']}")

    if decision.crowding_warnings:
        lines.append(f"\n拥挤警告 ({len(decision.crowding_warnings)}):")
        for crowding in decision.crowding_warnings[:3]:
            severity = "⚠️高" if crowding["severity"] == "high" else "⚡中"
            lines.append(f"  {severity} {crowding['suggestion']}")

    if decision.warnings:
        lines.append(f"\n风险提示 ({len(decision.warnings)}):")
        for warning in decision.warnings[:5]:
            lines.append(f"  [!] {warning}")

    return "\n".join(lines)


def build_got_notify_content(
    decision,
    run_date: date,
    base_dir: Optional[Path] = None,
) -> str:
    """拼 GOT 完整推送正文：结论块 + reasoning.md 全文（供不支持附件的渠道兜底）。

    ntfy 长消息会把本函数结果整体当附件，并把 ``build_got_notify_summary`` 当消息正文，
    得到「一条消息 = 结论 + 附件」。

    Args:
        decision: PortfolioDecision（Phase 2 输出）
        run_date: 运行日期（决定标题与 reasoning.md 归档目录）
        base_dir: 归档根目录（测试注入用，缺省 data/got_daily）

    Returns:
        Markdown 正文；reasoning.md 缺失时只含结论块。
    """
    content = build_got_notify_summary(decision, run_date)
    full_report = _read_full_report(run_date, base_dir)
    if full_report:
        return content + FULL_REPORT_SEPARATOR + full_report
    return content


def _read_full_report(run_date: date, base_dir: Optional[Path]) -> str:
    """读当日 reasoning.md；缺失或不可读返回空串（推送退化为摘要）。"""
    path = _reasoning_md_path(run_date, base_dir=base_dir)
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning("[GOT] reasoning.md 不存在，推送只含摘要: %s", path)
        return ""
    except OSError as exc:
        logger.warning("[GOT] reasoning.md 读取失败，推送只含摘要: %s (%s)", path, exc)
        return ""
