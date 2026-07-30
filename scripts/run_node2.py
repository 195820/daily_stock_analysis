"""本地运行 Node 2 板块分析（真实数据），输出 JSON 到 data/node2_real_output.json"""

import asyncio
import json
import sys

sys.path.insert(0, ".")

# 反爬补丁必须在任何东财 API 调用之前应用
from src.patches.eastmoney_patch import eastmoney_patch
eastmoney_patch()

from src.got.nodes.sector_analysis import SectorAnalysisNode
from src.got.schemas import (
    KeyLevels,
    MarketContext,
    RegimeMetadata,
    Signal,
    StyleSignal,
    TechGrowthSignal,
    TimeframeGOTResult,
    TimeframeSignals,
)


def build_market_context() -> MarketContext:
    """从今日 market.json 加载真实 MarketContext。
    若无归档文件则回退到中性默认值。
    """
    import json
    import os
    from datetime import date

    market_file = os.path.join("data", "got_daily", str(date.today()), "market.json")
    if os.path.exists(market_file):
        with open(market_file, encoding="utf-8") as f:
            d = json.load(f)
        tg = d["tech_growth"]
        sigs = tg["signals"]
        tf_got = tg.get("timeframe_got", {})
        regime = tg.get("regime_metadata", {})

        def _signal(s: dict) -> Signal:
            return Signal(trend=s.get("trend", "neutral"), score=s.get("score", 0.0))

        return MarketContext(
            tech_growth=TechGrowthSignal(
                trend=tg.get("trend", "neutral"),
                score=tg.get("score", 0.0),
                constituents=tg.get("constituents", {}),
                consistency=tg.get("consistency", "aligned"),
                signals=TimeframeSignals(
                    short_term=_signal(sigs.get("short_term", {})),
                    medium_term=_signal(sigs.get("medium_term", {})),
                    long_term=_signal(sigs.get("long_term", {})),
                ),
                timeframe_got=TimeframeGOTResult(
                    unified_trend=tf_got.get("unified_trend", "neutral"),
                    unified_score=tf_got.get("unified_score", 0.0),
                    contradiction_type=tf_got.get("contradiction_type"),
                    timeframe_confidence=tf_got.get("timeframe_confidence", 1.0),
                ),
                regime_metadata=RegimeMetadata(
                    adx_value=regime.get("adx_value", 25.0),
                    regime=regime.get("regime", "ranging"),
                    volatility_regime=regime.get("volatility_regime", "normal"),
                    weights_used=regime.get("weights_used", {}),
                ),
            ),
            mid_small_cap=StyleSignal(
                trend="neutral", score=0.0,
                signals=TimeframeSignals(
                    short_term=_signal({}), medium_term=_signal({}), long_term=_signal({}),
                ),
            ),
            large_cap=StyleSignal(
                trend="neutral", score=0.0,
                signals=TimeframeSignals(
                    short_term=_signal({}), medium_term=_signal({}), long_term=_signal({}),
                ),
            ),
            position_limit=d.get("position_limit", 0.5),
            confidence=d.get("confidence", 0.5),
            key_levels=KeyLevels(**d.get("key_levels", {})),
            today_change=d.get("today_change", {}),
        )

    # 回退：中性默认值
    neutral_signal = Signal(trend="neutral", score=0.0)
    neutral_timeframe = TimeframeSignals(
        short_term=neutral_signal,
        medium_term=neutral_signal,
        long_term=neutral_signal,
    )
    return MarketContext(
        tech_growth=TechGrowthSignal(
            trend="neutral", score=0.0,
            signals=neutral_timeframe,
            timeframe_got=TimeframeGOTResult(
                unified_trend="neutral", unified_score=0.0,
            ),
            regime_metadata=RegimeMetadata(
                adx_value=25.0, regime="ranging",
                volatility_regime="normal",
            ),
        ),
        mid_small_cap=StyleSignal(
            trend="neutral", score=0.0, signals=neutral_timeframe,
        ),
        large_cap=StyleSignal(
            trend="neutral", score=0.0, signals=neutral_timeframe,
        ),
        position_limit=0.5,
        confidence=0.5,
        key_levels=KeyLevels(),
    )


async def main():
    print("Node 2 板块分析（真实数据）")
    print("=" * 50)

    market_context = build_market_context()
    node = SectorAnalysisNode(use_mock=False)

    try:
        ctx = await node.run(market_context)
    except Exception as e:
        print(f"运行失败: {e}")
        return

    # 输出摘要
    print(f"\n市场方向: {ctx.market_direction}")
    print(f"\n活跃主线 ({len(ctx.active_mainline)}):")
    for s in ctx.active_mainline:
        print(f"  {s.name} ({s.code})  强度={s.strength_score:.1f}  "
              f"分级={s.tier}  阶段={s.lifecycle.phase if s.lifecycle else '-'}  "
              f"操作={s.recommended_action or '-'}")
        if s.launch_signal:
            print(f"    启动信号: {s.launch_signal.level} (score={s.launch_signal.score})")

    print(f"\n衰竭主线 ({len(ctx.exhausted_mainline)}):")
    for s in ctx.exhausted_mainline:
        print(f"  {s.name} ({s.code})  强度={s.strength_score:.1f}  "
              f"分级={s.tier}  阶段={s.lifecycle.phase if s.lifecycle else '-'}")

    print(f"\n埋伏期 ({len(ctx.ambush)}):")
    for s in ctx.ambush:
        print(f"  {s.name} ({s.code})  强度={s.strength_score:.1f}")

    print(f"\n防守轮动 ({len(ctx.defensive_rotation)}):")
    for s in ctx.defensive_rotation:
        print(f"  {s.name} ({s.code})  强度={s.strength_score:.1f}")

    if ctx.notes:
        print(f"\n备注:")
        for note in ctx.notes:
            print(f"  ⚠ {note}")

    if ctx.reasoning:
        print(f"\nReasoning:\n{ctx.reasoning}")

    print(f"\n数据质量: available={ctx.data_quality.available_fields}  "
          f"failed={ctx.data_quality.fetch_failed_fields}")

    # 输出完整 JSON
    output_path = "data/node2_real_output.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(ctx.model_dump(exclude_none=True), f, ensure_ascii=False, indent=2, default=str)
    print(f"\n完整输出已写入: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
