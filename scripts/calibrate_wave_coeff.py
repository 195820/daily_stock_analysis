#!/usr/bin/env python3
"""波次系数校准脚本（v3.5 S5）。

读取 wave_calibration.jsonl 埋点数据，按 wave_days 分箱统计：
- 次日强度变化均值
- 板块涨跌幅均值
- 下跌占比

输出折扣档位建议值，供人工审核后更新 WAVE_LIFECYCLE_COEFF。

触发节奏：
- 埋点积累 ≥40 个交易日后首次运行
- 此后每月一次（对齐 reports/param_calibration_*.md 惯例）

使用：
    python scripts/calibrate_wave_coeff.py
    python scripts/calibrate_wave_coeff.py --min-samples 30
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.storage import DatabaseManager, SectorHistoryRecord


def load_calibration_data(jsonl_path: Path) -> list[dict]:
    """加载 JSONL 埋点数据。"""
    if not jsonl_path.exists():
        return []

    records = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"⚠ JSON 解析失败: {e}", file=sys.stderr)
    return records


def enrich_with_next_day_data(records: list[dict]) -> list[dict]:
    """为每条记录补齐次日涨跌幅和强度变化。"""
    db = DatabaseManager()

    # 按日期分组
    by_date = defaultdict(list)
    for rec in records:
        by_date[rec["date"]].append(rec)

    enriched = []
    with db.get_session() as session:
        for record_date, recs in by_date.items():
            # 计算次日
            date_obj = datetime.strptime(record_date, "%Y-%m-%d").date()
            next_date = date_obj + timedelta(days=1)

            # 跳过周末（简化处理，实际应使用交易日历）
            while next_date.weekday() >= 5:  # 5=Saturday, 6=Sunday
                next_date += timedelta(days=1)

            next_date_str = next_date.strftime("%Y-%m-%d")

            for rec in recs:
                sector_code = rec["sector_code"]

                # 查询次日强度
                next_record = (
                    session.query(SectorHistoryRecord)
                    .filter(
                        SectorHistoryRecord.sector_name == rec["sector_name"],
                        SectorHistoryRecord.trade_date == next_date_str,
                    )
                    .first()
                )

                if next_record:
                    rec["next_day_change_pct"] = next_record.change_pct or 0
                    # 强度变化需要从 node2_sector_trends 表查询
                    # 这里简化为涨跌幅近似
                    rec["next_strength_change"] = next_record.change_pct or 0
                    enriched.append(rec)

    return enriched


def analyze_by_wave_days(records: list[dict], min_samples: int = 40) -> dict:
    """按 wave_days 分箱统计。

    Returns:
        {wave_days: {count, avg_strength_change, avg_price_change, down_ratio}}
    """
    bins = defaultdict(lambda: {
        "count": 0,
        "strength_changes": [],
        "price_changes": [],
    })

    for rec in records:
        wave_days = rec.get("wave_days", 0)
        if wave_days is None:
            continue

        bins[wave_days]["count"] += 1
        if "next_strength_change" in rec:
            bins[wave_days]["strength_changes"].append(rec["next_strength_change"])
        if "next_day_change_pct" in rec:
            bins[wave_days]["price_changes"].append(rec["next_day_change_pct"])

    # 计算统计值
    result = {}
    for wave_days, data in bins.items():
        if data["count"] < min_samples:
            continue

        strength_changes = data["strength_changes"]
        price_changes = data["price_changes"]

        avg_strength_change = (
            sum(strength_changes) / len(strength_changes) if strength_changes else 0
        )
        avg_price_change = (
            sum(price_changes) / len(price_changes) if price_changes else 0
        )
        down_ratio = (
            sum(1 for p in price_changes if p < 0) / len(price_changes)
            if price_changes else 0
        )

        result[wave_days] = {
            "count": data["count"],
            "avg_strength_change": avg_strength_change,
            "avg_price_change": avg_price_change,
            "down_ratio": down_ratio,
        }

    return result


def generate_recommendations(analysis: dict) -> list[str]:
    """基于统计结果生成折扣系数建议。"""
    recommendations = []

    # 按 wave_days 排序
    sorted_days = sorted(analysis.keys())

    # 寻找衰减拐点
    for i in range(1, len(sorted_days)):
        prev_day = sorted_days[i - 1]
        curr_day = sorted_days[i]

        prev_data = analysis[prev_day]
        curr_data = analysis[curr_day]

        # 如果下跌占比显著上升，建议折扣
        if curr_data["down_ratio"] > prev_data["down_ratio"] + 0.1:
            recommendations.append(
                f"wave_days={curr_day}: 下跌占比 {curr_data['down_ratio']:.1%} "
                f"较前日 {prev_data['down_ratio']:.1%} 上升，建议引入折扣"
            )

        # 如果平均涨幅转负，建议加大折扣
        if curr_data["avg_price_change"] < 0 and prev_data["avg_price_change"] > 0:
            recommendations.append(
                f"wave_days={curr_day}: 平均涨幅 {curr_data['avg_price_change']:.2f}% 转负，"
                f"建议加大折扣力度"
            )

    return recommendations


def main():
    parser = argparse.ArgumentParser(description="波次系数校准脚本")
    parser.add_argument(
        "--min-samples",
        type=int,
        default=40,
        help="最小样本数（默认 40，对应 40 个交易日）",
    )
    args = parser.parse_args()

    print("=" * 80)
    print("波次系数校准工具 (v3.5 S5)")
    print("=" * 80)
    print()

    # 加载埋点数据
    jsonl_path = PROJECT_ROOT / "data" / "wave_calibration.jsonl"
    print(f"加载埋点数据: {jsonl_path}")
    records = load_calibration_data(jsonl_path)
    print(f"  总记录数: {len(records)}")

    if not records:
        print("\n✗ 无埋点数据，请先运行系统积累数据")
        sys.exit(0)

    # 统计日期范围
    dates = sorted(set(r["date"] for r in records))
    print(f"  日期范围: {dates[0]} ~ {dates[-1]}")
    print(f"  交易日数: {len(dates)}")

    if len(dates) < 10:
        print(f"\n⚠ 数据不足（{len(dates)} 个交易日），建议至少积累 40 个交易日后再运行")
        print("  当前数据仅供参考，不建议用于参数校准")
        print()

    # 补齐次日数据
    print("\n补齐次日涨跌幅...")
    enriched = enrich_with_next_day_data(records)
    print(f"  成功补齐: {len(enriched)} / {len(records)}")

    if not enriched:
        print("\n✗ 无次日数据，无法校准")
        sys.exit(0)

    # 分箱统计
    print(f"\n按 wave_days 分箱统计（最小样本数: {args.min_samples}）...")
    analysis = analyze_by_wave_days(enriched, min_samples=args.min_samples)

    if not analysis:
        print(f"\n✗ 无满足最小样本数的分箱数据")
        print(f"  当前总样本: {len(enriched)}，需要 ≥{args.min_samples} 个样本/分箱")
        sys.exit(0)

    # 输出统计结果
    print("\n统计结果:")
    print("-" * 80)
    print(f"{'wave_days':>10} {'样本数':>8} {'强度变化':>10} {'涨跌幅':>10} {'下跌占比':>10}")
    print("-" * 80)
    for wave_days in sorted(analysis.keys()):
        data = analysis[wave_days]
        print(
            f"{wave_days:>10} {data['count']:>8} "
            f"{data['avg_strength_change']:>+10.2f} "
            f"{data['avg_price_change']:>+10.2f}% "
            f"{data['down_ratio']:>10.1%}"
        )

    # 生成建议
    print("\n" + "=" * 80)
    print("校准建议:")
    print("=" * 80)
    recommendations = generate_recommendations(analysis)

    if recommendations:
        for rec in recommendations:
            print(f"  • {rec}")
    else:
        print("  未发现明显的衰减拐点，当前折扣系数可能合理")

    # 保存报告
    report_path = PROJECT_ROOT / "reports" / f"wave_calibration_{datetime.now().strftime('%Y%m%d')}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 波次系数校准报告\n\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"## 数据概况\n\n")
        f.write(f"- 埋点记录数: {len(enriched)}\n")
        f.write(f"- 日期范围: {dates[0]} ~ {dates[-1]}\n")
        f.write(f"- 交易日数: {len(dates)}\n\n")
        f.write(f"## 分箱统计\n\n")
        f.write(f"| wave_days | 样本数 | 强度变化 | 涨跌幅 | 下跌占比 |\n")
        f.write(f"|-----------|--------|----------|--------|----------|\n")
        for wave_days in sorted(analysis.keys()):
            data = analysis[wave_days]
            f.write(
                f"| {wave_days} | {data['count']} | "
                f"{data['avg_strength_change']:+.2f} | "
                f"{data['avg_price_change']:+.2f}% | "
                f"{data['down_ratio']:.1%} |\n"
            )
        f.write(f"\n## 校准建议\n\n")
        if recommendations:
            for rec in recommendations:
                f.write(f"- {rec}\n")
        else:
            f.write("未发现明显的衰减拐点，当前折扣系数可能合理。\n")

    print(f"\n✓ 报告已保存: {report_path}")
    print("\n提示: 校准建议仅供参考，请结合实际业务逻辑人工审核后更新 WAVE_LIFECYCLE_COEFF")


if __name__ == "__main__":
    main()
