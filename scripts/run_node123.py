"""
串联运行 GOT Node 1 → 2 → 3（真实数据），输出每步结果 + 综合摘要

数据流：
  Node 1（大盘）→ MarketContext
       ↓
  Node 2（板块）+ MarketContext → SectorContext
       ↓
  Node 3（资金面）+ MarketContext + SectorContext → CapitalContext
       ↓
  综合摘要
"""

import asyncio
import json
import sys
import pathlib
import logging
from datetime import datetime

_PROJECT_ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

sys.stdout.reconfigure(encoding="utf-8")

# 反爬补丁：必须在任何东财 API 调用之前
from src.patches.eastmoney_patch import eastmoney_patch
eastmoney_patch()

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
# 只保留 GOT 节点的 INFO 日志
logging.getLogger("src.got").setLevel(logging.INFO)


def _sep(title: str):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def print_node1(result):
    """打印 Node 1 大盘分析结果"""
    _sep("Node 1: 大盘分析 (Market Analysis)")

    tg = result.tech_growth
    print(f"\n  科技成长信号:")
    print(f"    趋势: {tg.trend}  评分: {tg.score:+.2f}  一致性: {tg.consistency}")

    # 多时间框架
    tf = tg.timeframe_got
    sig = tg.signals
    print(f"\n  多时间框架 GOT:")
    print(f"    短期(5日):  {sig.short_term.trend:8s} ({sig.short_term.score:+.2f})")
    print(f"    中期(20日): {sig.medium_term.trend:8s} ({sig.medium_term.score:+.2f})")
    print(f"    长期(60日): {sig.long_term.trend:8s} ({sig.long_term.score:+.2f})")
    print(f"    → 统一趋势: {tf.unified_trend}  统一评分: {tf.unified_score:+.2f}")
    print(f"    → 矛盾类型: {tf.contradiction_type or '无'}")

    # 市场状态
    rm = tg.regime_metadata
    print(f"\n  市场状态 (ADX):")
    print(f"    ADX: {rm.adx_value:.1f}  趋势: {rm.regime}  波动: {rm.volatility_regime}")

    # 风格对比
    print(f"\n  风格对比:")
    print(f"    中小盘(中证500): {result.mid_small_cap.trend} ({result.mid_small_cap.score:+.2f})")
    print(f"    大盘蓝筹(沪深300): {result.large_cap.trend} ({result.large_cap.score:+.2f})")

    # 仓位和关键点
    print(f"\n  仓位上限: {result.position_limit:.0%}  置信度: {result.confidence:.2f}")
    kl = result.key_levels
    print(f"  短期支撑: {kl.short_term.get('support', 'N/A')}  "
          f"阻力: {kl.short_term.get('resistance', 'N/A')}")
    print(f"  中期支撑: {kl.mid_term.get('support', 'N/A')}  "
          f"阻力: {kl.mid_term.get('resistance', 'N/A')}")

    if result.reasoning:
        print(f"\n  推理: {result.reasoning[:300]}")

    dq = result.data_quality
    print(f"\n  数据: 可用={len(dq.available_fields)}字段  "
          f"缺失={dq.missing_fields}  失败={dq.fetch_failed_fields}")


def print_node2(ctx):
    """打印 Node 2 板块分析结果"""
    _sep("Node 2: 板块分析 (Sector Analysis)")

    print(f"\n  市场方向: {ctx.market_direction}")

    if ctx.active_mainline:
        print(f"\n  活跃主线 ({len(ctx.active_mainline)}):")
        for s in ctx.active_mainline:
            phase = s.lifecycle.phase if s.lifecycle else "-"
            print(f"    [{s.tier}] {s.name} ({s.code})  "
                  f"强度={s.strength_score:.1f}  阶段={phase}  "
                  f"操作={s.recommended_action or '-'}")
            if s.launch_signal:
                print(f"         启动信号: {s.launch_signal.level} "
                      f"(score={s.launch_signal.score:.2f})")
    else:
        print("\n  活跃主线: 无")

    if ctx.exhausted_mainline:
        print(f"\n  衰竭主线 ({len(ctx.exhausted_mainline)}):")
        for s in ctx.exhausted_mainline:
            phase = s.lifecycle.phase if s.lifecycle else "-"
            print(f"    [{s.tier}] {s.name}  强度={s.strength_score:.1f}  阶段={phase}")

    if ctx.ambush:
        print(f"\n  埋伏期 ({len(ctx.ambush)}):")
        for s in ctx.ambush:
            print(f"    {s.name}  强度={s.strength_score:.1f}")

    if ctx.defensive_rotation:
        print(f"\n  防守轮动 ({len(ctx.defensive_rotation)}):")
        for s in ctx.defensive_rotation:
            print(f"    {s.name}  强度={s.strength_score:.1f}")

    if ctx.reasoning:
        print(f"\n  推理: {ctx.reasoning[:300]}")

    dq = ctx.data_quality
    print(f"\n  数据: 可用={dq.available_fields}  失败={dq.fetch_failed_fields}")


def print_node3(result):
    """打印 Node 3 资金面分析结果"""
    _sep("Node 3: 资金面分析 (Capital Analysis)")

    print(f"\n  状态: {result.status}")
    print(f"  机构态度: {result.institutional_attitude}")
    print(f"  机构评分: {result.institutional_score:+.2f}")
    print(f"  置信度乘数: {result.confidence_multiplier:.2f}")
    print(f"  缺失维度: {result.missing_dimensions}")

    ev = result.evidence
    print(f"\n  证据明细:")

    if ev.northbound:
        nb = ev.northbound
        print(f"    北向资金: 净买入={nb.get('net_buy', 'N/A')}  "
              f"评分={nb.get('score', 'N/A')}")
    else:
        print(f"    北向资金: 无数据")

    if ev.dragon_tiger:
        dt = ev.dragon_tiger
        top = dt.get("top_institutions", [])
        print(f"    龙虎榜: 机构净买入={dt.get('institutional_net_buy', 'N/A')}  "
              f"评分={dt.get('score', 'N/A')}")
        if top:
            print(f"      Top机构活动: {top[:3]}")
    else:
        print(f"    龙虎榜: 无数据")

    if ev.margin_trading:
        mt = ev.margin_trading
        print(f"    融资融券: 余额变化={mt.get('balance_change', 'N/A')}  "
              f"评分={mt.get('score', 'N/A')}")
    else:
        print(f"    融资融券: 无数据")

    if ev.block_trade:
        bt = ev.block_trade
        print(f"    大宗交易: 成交额={bt.get('total_amount', 'N/A')}  "
              f"评分={bt.get('score', 'N/A')}")
    else:
        print(f"    大宗交易: 无数据")

    if result.reasoning:
        print(f"\n  推理: {result.reasoning[:300]}")

    dq = result.data_quality
    print(f"\n  数据: 可用={dq.available_fields}  失败={dq.fetch_failed_fields}")


def print_summary(market_ctx, sector_ctx, capital_ctx, elapsed_sec: float):
    """综合三个节点输出摘要"""
    _sep("综合摘要 (Node 1 + 2 + 3)")

    # 大盘判断
    tg = market_ctx.tech_growth
    print(f"\n  [大盘] 趋势={tg.trend}  评分={tg.score:+.2f}  "
          f"ADX={tg.regime_metadata.adx_value:.0f}({tg.regime_metadata.regime})  "
          f"仓位={market_ctx.position_limit:.0%}")

    # 板块判断
    n_active = len(sector_ctx.active_mainline)
    n_exhausted = len(sector_ctx.exhausted_mainline)
    top_mainline = sector_ctx.active_mainline[0].name if sector_ctx.active_mainline else "无"
    print(f"\n  [板块] 方向={sector_ctx.market_direction}  "
          f"活跃主线={n_active}条  衰竭={n_exhausted}条  "
          f"龙头={top_mainline}")

    # 资金面判断
    print(f"\n  [资金] 态度={capital_ctx.institutional_attitude}  "
          f"评分={capital_ctx.institutional_score:+.2f}  "
          f"置信度={capital_ctx.confidence_multiplier:.2f}  "
          f"状态={capital_ctx.status}")

    # 综合判断
    print(f"\n  {'─' * 50}")
    overall_bullish = 0
    overall_bearish = 0

    # 大盘维度
    if tg.score > 0.3:
        overall_bullish += 1
    elif tg.score < -0.3:
        overall_bearish += 1

    # 板块维度
    if n_active >= 3:
        overall_bullish += 1
    elif n_active == 0:
        overall_bearish += 1

    # 资金维度
    if capital_ctx.institutional_score > 0.3:
        overall_bullish += 1
    elif capital_ctx.institutional_score < -0.3:
        overall_bearish += 1

    if overall_bullish >= 2:
        verdict = "偏多 — 可适当进攻"
    elif overall_bearish >= 2:
        verdict = "偏空 — 建议防守"
    else:
        verdict = "中性 — 控制仓位、等待方向"

    print(f"  综合判断: {verdict}")
    print(f"  (多头信号={overall_bullish}/3  空头信号={overall_bearish}/3)")
    print(f"  执行耗时: {elapsed_sec:.1f}s  时间: {datetime.now().strftime('%H:%M:%S')}")


async def main():
    from src.got.nodes.market_analysis import MarketAnalysisNode
    from src.got.nodes.sector_analysis import SectorAnalysisNode
    from src.got.nodes.capital_analysis import CapitalAnalysisNode

    print(f"GOT Node 1→2→3 串联执行  日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    start = datetime.now()

    # ── Node 1: 大盘分析 ──
    print("\n▶ 执行 Node 1: 大盘分析...")
    market_node = MarketAnalysisNode(use_mock=False)
    try:
        market_ctx = await market_node.run()
        print_node1(market_ctx)
    except Exception as e:
        print(f"[X] Node 1 失败: {e}")
        import traceback; traceback.print_exc()
        return

    # ── Node 2: 板块分析（使用 Node 1 真实输出） ──
    print("\n▶ 执行 Node 2: 板块分析...")
    sector_node = SectorAnalysisNode(use_mock=False)
    try:
        sector_ctx = await sector_node.run(market_context=market_ctx)
        print_node2(sector_ctx)
    except Exception as e:
        print(f"[X] Node 2 失败: {e}")
        import traceback; traceback.print_exc()
        return

    # ── Node 3: 资金面分析（使用 Node 1+2 真实输出） ──
    print("\n▶ 执行 Node 3: 资金面分析...")
    capital_node = CapitalAnalysisNode(use_mock=False)
    try:
        capital_ctx = await capital_node.run(
            market_context=market_ctx,
            sector_context=sector_ctx,
        )
        print_node3(capital_ctx)
    except Exception as e:
        print(f"[X] Node 3 失败: {e}")
        import traceback; traceback.print_exc()
        return

    # ── 综合摘要 ──
    elapsed = (datetime.now() - start).total_seconds()
    print_summary(market_ctx, sector_ctx, capital_ctx, elapsed)

    # 保存完整 JSON
    output_dir = pathlib.Path(_PROJECT_ROOT) / "data"
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / "node123_output.json"
    full_output = {
        "timestamp": datetime.now().isoformat(),
        "elapsed_seconds": elapsed,
        "node1_market": market_ctx.model_dump(exclude_none=True),
        "node2_sector": sector_ctx.model_dump(exclude_none=True),
        "node3_capital": capital_ctx.model_dump(exclude_none=True),
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(full_output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n完整结果已写入: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
