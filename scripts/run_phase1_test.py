"""Phase 1 全流程测试 + 每个 Node 的 Context 输出"""
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from datetime import datetime

project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")


def ctx_to_dict(ctx) -> dict:
    """将 Pydantic model 转为 dict"""
    return ctx.model_dump()


def ctx_to_json(ctx) -> str:
    return json.dumps(ctx_to_dict(ctx), ensure_ascii=False, indent=2, default=str)


async def run_phase1():
    from src.got import GOTOrchestrator

    print("Phase 1 全流程测试（含各 Node Context 输出）")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    orchestrator = GOTOrchestrator(use_mock=False)
    t0 = time.time()

    # 手动执行各路径以获取每个 node 的 context
    # 技术分析路（串行）
    print("\n[技术分析路] 开始...")
    market_ctx = await orchestrator.market_node.run()
    print(f"  Node 1 完成 — 科技成长: {market_ctx.tech_growth.trend}")

    sector_ctx = await orchestrator.sector_node.run(market_context=market_ctx)
    print(f"  Node 2 完成 — 活跃主线: {len(sector_ctx.active_mainline)}")

    capital_ctx = await orchestrator.capital_node.run(
        market_context=market_ctx,
        sector_context=sector_ctx,
    )
    print(f"  Node 3 完成 — 机构态度: {capital_ctx.institutional_attitude}")

    # 独立数据路（并行）
    print("\n[独立数据路] 开始（并行）...")
    news_task = orchestrator.news_node.run()
    sentiment_task = orchestrator.sentiment_node.run()
    news_ctx, sentiment_ctx = await asyncio.gather(news_task, sentiment_task)
    print(f"  Node 4 完成 — 消息影响: {news_ctx.overall_impact}")
    print(f"  Node 5 完成 — 情绪: {sentiment_ctx.overall_sentiment}")

    # 聚合
    print("\n[聚合] 开始...")
    unified = await orchestrator.aggregator.run(
        market=market_ctx,
        sector=sector_ctx,
        capital=capital_ctx,
        news=news_ctx,
        sentiment=sentiment_ctx,
    )
    print(f"  Node 6 完成 — 信号: {unified.unified_signal}")

    elapsed = time.time() - t0
    print(f"\n总耗时: {elapsed:.1f}s")
    print("=" * 70)

    # 输出各 Node Context 摘要到终端
    print("\n" + "=" * 70)
    print("各 Node Context 摘要")
    print("=" * 70)

    print(f"\n--- Node 1 大盘 (MarketContext) ---")
    print(f"  科技成长趋势: {market_ctx.tech_growth.trend}")
    print(f"  仓位限制: {market_ctx.position_limit:.0%}")
    print(f"  置信度: {market_ctx.confidence:.2f}")

    print(f"\n--- Node 2 板块 (SectorContext) ---")
    print(f"  活跃主线: {len(sector_ctx.active_mainline)}")
    print(f"  衰竭主线: {len(sector_ctx.exhausted_mainline)}")
    print(f"  埋伏板块: {len(sector_ctx.ambush)}")
    print(f"  防守板块: {len(sector_ctx.defensive_rotation)}")
    for s in sector_ctx.active_mainline[:3]:
        print(f"    [{s.tier}] {s.name}: {s.state}, 强度={s.strength_score:.1f}")

    print(f"\n--- Node 3 资金面 (CapitalContext) ---")
    print(f"  机构态度: {capital_ctx.institutional_attitude}")
    print(f"  评分: {capital_ctx.institutional_score:.2f}")
    print(f"  置信度: {capital_ctx.confidence_multiplier:.2f}")
    print(f"  缺失维度: {capital_ctx.missing_dimensions}")

    print(f"\n--- Node 4 消息面 (NewsContext) ---")
    print(f"  整体影响: {news_ctx.overall_impact}")
    print(f"  影响评分: {news_ctx.impact_score:.2f}")
    print(f"  事件数: {len(news_ctx.major_events)}")
    print(f"  风险: {news_ctx.key_risks}")

    print(f"\n--- Node 5 情绪面 (SentimentContext) ---")
    print(f"  整体情绪: {sentiment_ctx.overall_sentiment}")
    print(f"  评分: {sentiment_ctx.sentiment_score:.2f}")
    print(f"  阶段: {sentiment_ctx.sentiment_stage}")
    print(f"  交易信号: {sentiment_ctx.trading_signal}")
    print(f"  市场温度: {sentiment_ctx.market_temperature:.2f}")

    print(f"\n--- Node 6 聚合 (UnifiedContext) ---")
    print(f"  状态: {unified.status}")
    print(f"  统一信号: {unified.unified_signal}")
    print(f"  置信度: {unified.overall_confidence:.2f}")
    print(f"  仓位上限: {unified.final_position_limit:.0%}")
    print(f"  矛盾: {len(unified.contradictions)}")
    for c in unified.contradictions:
        print(f"    [{c.severity}] {c.pair}: {c.description}")
    print(f"  根因: {unified.common_root_cause}")

    # 写入完整 md 文档
    md_path = project_root / "data" / "phase1_result.md"
    md_path.parent.mkdir(exist_ok=True)

    md = "# Phase 1 GOT 全流程测试结果\n\n"
    md += f"> 运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    md += f"> 总耗时: {elapsed:.1f}s\n"
    md += "> 数据源: MootdxFetcher(TCP) + AkshareFetcher(THS降级)\n\n"

    # 各 Node Context 详情
    nodes_data = [
        ("Node 1: 大盘分析 (MarketContext)", market_ctx),
        ("Node 2: 板块分析 (SectorContext)", sector_ctx),
        ("Node 3: 资金面分析 (CapitalContext)", capital_ctx),
        ("Node 4: 消息面分析 (NewsContext)", news_ctx),
        ("Node 5: 情绪面分析 (SentimentContext)", sentiment_ctx),
        ("Node 6: 聚合输出 (UnifiedContext)", unified),
    ]

    for title, ctx in nodes_data:
        md += f"## {title}\n\n"
        md += f"```json\n{ctx_to_json(ctx)}\n```\n\n"

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"\n文档已保存: {md_path}")
    print(f"[OK] Phase 1 全流程测试通过")


if __name__ == "__main__":
    asyncio.run(run_phase1())
