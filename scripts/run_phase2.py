# -*- coding: utf-8 -*-
"""
Phase 2 统一决策节点运行脚本

使用方法:
    python scripts/run_phase2.py [--date YYYY-MM-DD]

示例:
    # 使用今日 GOT 输出
    python scripts/run_phase2.py

    # 使用指定日期
    python scripts/run_phase2.py --date 2026-08-03
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.config import Config
from src.got.schemas import (
    CapitalContext,
    MarketContext,
    NewsContext,
    SectorContext,
    SentimentContext,
    UnifiedContext,
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Phase 2 统一决策节点")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="GOT 运行日期 (YYYY-MM-DD)，默认今天",
    )
    args = parser.parse_args()

    target_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    )
    date_str = target_date.strftime("%Y%m%d")

    # 加载 Phase 1 输出
    got_dir = Path("data/got_daily") / date_str
    if not got_dir.exists():
        logger.error(f"GOT 输出不存在: {got_dir}")
        return 1

    unified_path = got_dir / "unified.json"
    if not unified_path.exists():
        logger.error(f"unified.json 不存在: {unified_path}")
        return 1

    logger.info(f"[Phase 2] 加载 Phase 1 输出: {got_dir}")

    unified_data = json.loads(unified_path.read_text(encoding="utf-8"))
    unified = UnifiedContext(**unified_data)

    # 加载其他上下文
    contexts = {}
    context_classes = {
        "market": MarketContext,
        "sector": SectorContext,
        "capital": CapitalContext,
        "news": NewsContext,
        "sentiment": SentimentContext,
    }

    for name, cls in context_classes.items():
        ctx_path = got_dir / f"{name}.json"
        if ctx_path.exists():
            ctx_data = json.loads(ctx_path.read_text(encoding="utf-8"))
            contexts[f"{name}_context"] = cls(**ctx_data)
        else:
            logger.warning(f"上下文文件不存在: {ctx_path}")

    # 检查是否所有上下文都已加载
    if len(contexts) < 5:
        logger.error(f"缺少上下文，已加载 {len(contexts)}/5")
        return 1

    # 运行 Phase 2
    logger.info("[Phase 2] 开始统一决策")
    from src.got.nodes.portfolio_decision import PortfolioDecisionNode

    node = PortfolioDecisionNode(use_mock=False)
    decision = asyncio.run(node.run(unified_context=unified, **contexts))

    # 输出结果
    print("\n" + "=" * 60)
    print(f"Phase 2 决策 — {target_date}")
    print("=" * 60)
    print(f"状态: {decision.status}")
    print(f"\n风险评估:")
    print(f"  仓位上限: {decision.risk_assessment.position_limit:.0%}")
    print(f"  当前仓位: {decision.risk_assessment.current_position_ratio:.0%}")
    print(f"  止损线: {decision.risk_assessment.stop_loss_threshold:.0%}")
    print(f"  约束理由: {decision.risk_assessment.constraint_reasoning}")

    print(f"\n统一打分 Top 5:")
    for i, score in enumerate(decision.unified_scoring[:5], 1):
        held_tag = "【持仓】" if score.is_holding else ""
        lifecycle_tag = f"[{score.lifecycle_phase}]" if score.lifecycle_phase else ""
        print(
            f"  {i}. {score.name} ({score.symbol}) {held_tag}{lifecycle_tag}: "
            f"{score.composite_score:.1f} 分 — {score.reasoning}"
        )

    # 显示轮动信号（吃鱼身+高切低策略）
    if decision.rotation_signals:
        print(f"\n轮动信号 ({len(decision.rotation_signals)}):")
        for signal in decision.rotation_signals:
            print(f"  [{signal['type']}] {signal['reason']}")
            if 'aging' in signal:
                print(f"    老化持仓: {', '.join(signal['aging'])}")
            if 'targets' in signal:
                print(f"    轮动目标: {', '.join(signal['targets'])}")

    # 显示家族拥挤警告
    if decision.crowding_warnings:
        print(f"\n家族拥挤警告 ({len(decision.crowding_warnings)}):")
        for warning in decision.crowding_warnings:
            severity = "⚠️ 高" if warning['severity'] == 'high' else "⚡ 中"
            print(f"  {severity} {warning['suggestion']}")

    # 存档到文件
    from src.got.archive import archive_phase2

    archive_phase2(decision, target_date)

    logger.info(f"[Phase 2] 决策已存档: data/got_daily/{date_str}/")

    return 0


if __name__ == "__main__":
    exit(main())
