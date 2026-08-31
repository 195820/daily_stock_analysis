# -*- coding: utf-8 -*-
"""催化剂参数校准建议脚本（T4）

读取 P6 兑现统计 + 故事寿命 + PEAD 分析，产出配置校准建议。
样本 < 30 时只提示不建建议。

用法：
    python scripts/calibrate_catalyst_params.py

产出：
    reports/param_calibration_YYYYMMDD.md
"""

import os
import sys
import logging
from datetime import date, datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 项目根目录
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

CALIBRATION_MIN_SAMPLES = 30


def main():
    from src.got.catalyst_realization import CatalystRealizationTracker
    from src.got.catalyst_config import (
        CATALYST_CONFIDENCE_GATE,
        RESERVOIR_EXPIRY_DAYS,
        RESERVOIR_PROMOTION_MIN_DAYS,
    )

    tracker = CatalystRealizationTracker()

    # 加载统计
    stats = tracker.get_realization_stats(days=90)
    lifetimes = tracker.get_story_lifetimes(days=180)
    pead = tracker.get_pead_analysis_report(days=180)

    # 注：键名与 CatalystRealizationTracker.get_realization_stats() 实际返回对齐，
    # 键名错配会导致样本量恒为 0、校准闭环永久空转（T4 依赖 A1 兑现数据）
    total_records = stats.get("total_events", 0)
    realized = stats.get("realized_events", 0)
    rate = stats.get("realization_rate", 0)

    today_str = date.today().strftime("%Y%m%d")
    lines = [
        f"# 催化剂参数校准建议 — {date.today().isoformat()}",
        "",
        f"> 自动生成，样本量 {total_records} 条（其中兑现 {realized} 条，兑现率 {rate:.1%}）",
        "",
        "## 当前配置",
        "",
        f"| 参数 | 当前值 |",
        f"|------|--------|",
        f"| CATALYST_CONFIDENCE_GATE | {CATALYST_CONFIDENCE_GATE} |",
        f"| RESERVOIR_EXPIRY_DAYS | {RESERVOIR_EXPIRY_DAYS} |",
        f"| RESERVOIR_PROMOTION_MIN_DAYS | {RESERVOIR_PROMOTION_MIN_DAYS} |",
        "",
    ]

    if total_records < CALIBRATION_MIN_SAMPLES:
        lines.extend([
            "## ⚠️ 样本量不足",
            "",
            f"当前仅有 **{total_records}** 条记录，低于最低校准样本量 {CALIBRATION_MIN_SAMPLES}。",
            f"建议继续积累数据后再重新运行此脚本。",
            "",
            "### 按类型分布",
            "",
        ])
        by_type = stats.get("by_event_type", {})
        if by_type:
            lines.append("| 类型 | 总数 | 兑现 | 兑现率 |")
            lines.append("|------|------|------|--------|")
            for etype, data in by_type.items():
                lines.append(
                    f"| {etype} | {data.get('total', 0)} | "
                    f"{data.get('realized', 0)} | {data.get('rate', 0):.1%} |"
                )
    else:
        lines.extend([
            "## 校准建议",
            "",
        ])

        # 1. 置信度门槛
        lines.extend(_calibrate_confidence_gate(stats, pead))

        # 2. 故事寿命 → 除名天数
        lines.extend(_calibrate_reservoir_params(lifetimes, pead))

        # 3. 分组兑现率
        lines.extend(_show_group_stats(stats))

    # 写出
    report_dir = os.path.join(ROOT_DIR, "reports")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"param_calibration_{today_str}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"✅ 校准报告已生成: {report_path}")


def _calibrate_confidence_gate(stats: dict, pead: dict) -> list[str]:
    """校准 CATALYST_CONFIDENCE_GATE"""
    lines = ["### 1. CATALYST_CONFIDENCE_GATE（置信度门槛）", ""]

    # 按 source 分组的方向准确率
    by_source = stats.get("by_source_type", {})
    direction_acc = stats.get("direction_accuracy", 0)

    lines.append(f"- 整体方向准确率: **{direction_acc:.1%}**")
    lines.append("")

    if by_source:
        lines.append("| 来源 | 总数 | 兑现率 | 方向准确率 |")
        lines.append("|------|------|--------|-----------|")
        for source, data in by_source.items():
            lines.append(
                f"| {source} | {data.get('total', 0)} | "
                f"{data.get('rate', 0):.1%} | "
                f"{data.get('direction_accuracy', 0):.1%} |"
            )
    lines.append("")

    # 简单建议逻辑
    if direction_acc < 0.5:
        lines.append(
            "> ⚠️ 方向准确率 < 50%，建议考虑**降低门槛至 0.60** "
            "（更多事件参与评分，用 B 公式的置信度乘子替代硬闸）"
        )
    elif direction_acc > 0.7:
        lines.append(
            "> ✅ 方向准确率 > 70%，当前门槛 0.7 可能过松，"
            "可考虑收紧至 **0.75** 或维持现状"
        )
    else:
        lines.append(
            "> 📊 方向准确率在合理区间，建议维持 0.7 不变，"
            "继续积累样本后重新评估"
        )
    lines.append("")
    return lines


def _calibrate_reservoir_params(lifetimes: dict, pead: dict) -> list[str]:
    """校准 RESERVOIR_EXPIRY_DAYS / PROMOTION_MIN_DAYS"""
    lines = ["### 2. 蓄水池参数（RESERVOIR_EXPIRY_DAYS / PROMOTION_MIN_DAYS）", ""]

    if lifetimes:
        lines.append("| 事件类型 | 平均寿命(天) | 中位数 | 最短 | 最长 |")
        lines.append("|----------|------------|--------|------|------|")
        for etype, data in lifetimes.items():
            lines.append(
                f"| {etype} | {data.get('avg_lifetime', 0):.1f} | "
                f"{data.get('median_lifetime', 0):.0f} | "
                f"{data.get('min_lifetime', 0)} | {data.get('max_lifetime', 0)} |"
            )
        lines.append("")

        # 基于中位数建议除名天数
        all_lifetimes = []
        for data in lifetimes.values():
            median = data.get("median_lifetime", 0)
            if median > 0:
                all_lifetimes.append(median)

        if all_lifetimes:
            overall_median = sum(all_lifetimes) / len(all_lifetimes)
            suggested_expiry = max(5, int(overall_median * 0.7))
            lines.append(
                f"> 📊 故事中位寿命 {overall_median:.0f} 天，"
                f"建议 RESERVOIR_EXPIRY_DAYS = **{suggested_expiry}** "
                f"（中位数 × 0.7，确保在故事衰竭前除名）"
            )
    else:
        lines.append("> ⚠️ 无故事寿命数据，无法校准除名天数")
    lines.append("")
    return lines


def _show_group_stats(stats: dict) -> list[str]:
    """按 category_tag 分组的兑现率"""
    lines = ["### 3. 按 category_tag 分组兑现率", ""]
    by_cat = stats.get("by_category_tag", {})
    if by_cat:
        lines.append("| 类型 | 总数 | 兑现 | 兑现率 | 平均收益 |")
        lines.append("|------|------|------|--------|---------|")
        for cat, data in by_cat.items():
            lines.append(
                f"| {cat} | {data.get('total', 0)} | "
                f"{data.get('realized', 0)} | "
                f"{data.get('rate', 0):.1%} | "
                f"{data.get('avg_return', 0):.2f}% |"
            )
    else:
        lines.append("> 无分组数据")
    lines.append("")
    return lines


if __name__ == "__main__":
    main()
