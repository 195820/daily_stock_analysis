#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试新 Node 6（v3 分层 GOT 架构）

加载 data/got_daily/20260804/ 的历史数据，运行新 Node 6，
对比新旧架构输出差异。
"""

import asyncio
import json
import sys
from pathlib import Path

# 设置 UTF-8 输出
sys.stdout.reconfigure(encoding='utf-8')

# 添加项目根目录到 path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.got.schemas import (
    MarketContext,
    SectorContext,
    CapitalContext,
    NewsContext,
    SentimentContext,
    UnifiedContext,
)
from src.got.nodes.aggregator import AggregatorNode


async def test_node6_v3(test_date: str = "20260804"):
    """测试新 Node 6"""

    # 加载历史数据
    data_dir = project_root / "data" / "got_daily" / test_date
    if not data_dir.exists():
        print(f"❌ 历史数据不存在: {data_dir}")
        return

    print(f"📂 加载历史数据: {data_dir}")

    try:
        market_data = json.loads((data_dir / "market.json").read_text(encoding="utf-8"))
        sector_data = json.loads((data_dir / "sector.json").read_text(encoding="utf-8"))
        capital_data = json.loads((data_dir / "capital.json").read_text(encoding="utf-8"))
        news_data = json.loads((data_dir / "news.json").read_text(encoding="utf-8"))
        sentiment_data = json.loads((data_dir / "sentiment.json").read_text(encoding="utf-8"))
        old_unified_data = json.loads((data_dir / "unified.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ 加载数据失败: {e}")
        return

    # 构建 Context 对象
    market_ctx = MarketContext(**market_data)
    sector_ctx = SectorContext(**sector_data)
    capital_ctx = CapitalContext(**capital_data)
    news_ctx = NewsContext(**news_data)
    sentiment_ctx = SentimentContext(**sentiment_data)

    print("✅ 历史数据加载完成")
    print(f"  - Node 1: tech_growth.score={market_ctx.tech_growth.score:.3f}")
    print(f"  - Node 2: active_mainline={len(sector_ctx.active_mainline)}个, market_direction={sector_ctx.market_direction}")
    print(f"  - Node 3: institutional_score={capital_ctx.institutional_score:.3f}, status={capital_ctx.status}")
    print(f"  - Node 4: impact_score={news_ctx.impact_score:.3f}, events={len(news_ctx.major_events)}个")
    print(f"  - Node 5: sentiment_score={sentiment_ctx.sentiment_score:.3f}, stage={sentiment_ctx.sentiment_stage}")
    print()

    # 显示旧 Node 6 输出
    print("=" * 60)
    print("旧 Node 6 输出（v2 架构）:")
    print("=" * 60)
    old_unified = UnifiedContext(**old_unified_data)
    print(f"  unified_signal: {old_unified.unified_signal}")
    print(f"  overall_confidence: {old_unified.overall_confidence:.3f}")
    print(f"  final_position_limit: {old_unified.final_position_limit:.0%}")
    if hasattr(old_unified, 'weighted_score') and old_unified.weighted_score is not None:
        print(f"  weighted_score: {old_unified.weighted_score:.3f}")
    if hasattr(old_unified, 'convergence_profile') and old_unified.convergence_profile:
        print(f"  convergence: {old_unified.convergence_profile.pattern}")
    old_confirmed = old_unified.confirmed_sectors if hasattr(old_unified, 'confirmed_sectors') else []
    print(f"  confirmed_sectors: {len(old_confirmed)}个")
    for s in old_confirmed[:5]:
        print(f"    - {s.sector_name}: {s.sector_type}, confidence={s.confidence:.2f}")
    print()

    # 运行新 Node 6
    print("=" * 60)
    print("新 Node 6 输出（v3 分层 GOT 架构）:")
    print("=" * 60)

    aggregator = AggregatorNode(use_mock=False)
    new_unified = await aggregator.run(
        market=market_ctx,
        sector=sector_ctx,
        capital=capital_ctx,
        news=news_ctx,
        sentiment=sentiment_ctx,
    )

    # 显示新输出
    regime = new_unified.market_regime
    print(f"  regime: {regime.regime}")
    print(f"  risk_budget: {regime.risk_budget:.0%}")
    print(f"  macro_overlay: {regime.macro_overlay}")
    print()
    print(f"  unified_signal: {new_unified.unified_signal}")
    print(f"  overall_confidence: {new_unified.overall_confidence:.3f}")
    print(f"  final_position_limit: {new_unified.final_position_limit:.0%}")
    print()

    confirmed = [s for s in new_unified.confirmed_sectors if s.classification == "confirmed"]
    print(f"  confirmed_sectors: {len(confirmed)}个")
    for s in confirmed[:10]:
        print(f"    - {s.sector_name}: score={s.composite_score:.0f}, "
              f"capital={s.capital_check}, news={s.news_check}, "
              f"sentiment={s.sentiment_check}, confidence={s.confidence:.2f}")
    print()

    # 对比
    print("=" * 60)
    print("对比分析:")
    print("=" * 60)
    print(f"  仓位变化: {old_unified.final_position_limit:.0%} → {new_unified.final_position_limit:.0%} "
          f"({'+' if new_unified.final_position_limit > old_unified.final_position_limit else ''}"
          f"{(new_unified.final_position_limit - old_unified.final_position_limit):+.0%})")
    print(f"  确认板块数: {len(old_confirmed)} → {len(confirmed)}")

    # 找出新增的确认板块
    old_names = {s.sector_name for s in old_confirmed if s.sector_type == "confirmed"}
    new_names = {s.sector_name for s in confirmed}
    added = new_names - old_names
    removed = old_names - new_names
    if added:
        print(f"  新增确认板块: {', '.join(added)}")
    if removed:
        print(f"  移除确认板块: {', '.join(removed)}")

    print()
    print("✅ 测试完成")


if __name__ == "__main__":
    test_date = sys.argv[1] if len(sys.argv) > 1 else "20260804"
    asyncio.run(test_node6_v3(test_date))
