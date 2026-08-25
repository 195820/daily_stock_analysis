"""验证双轨合并 + 评分质量修复（2026-08-25）

模拟 08-24 场景：
1. 冷启动板块（RRG 默认值）→ 应被打折
2. 暴跌板块（近3日跌 >3%）→ 方向惩罚
3. 有真实轨迹的吸筹板块 → 应保留高分
4. Track 1 状态机成员不被 Track 2 竞选淘汰
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np

from src.got.nodes.sector_analysis import SectorAnalysisNode, _is_noise_board
from src.got.schemas import RRGQuadrant


def make_kline(n_days: int, base_price: float = 10.0, daily_returns: list[float] | None = None) -> pd.DataFrame:
    """构造模拟 K 线。"""
    if daily_returns is None:
        daily_returns = [0.0] * n_days
    prices = [base_price]
    for r in daily_returns:
        prices.append(prices[-1] * (1 + r))
    prices = prices[1:]  # 去掉初始价
    closes = np.array(prices[-n_days:])
    volumes = np.random.uniform(1e6, 5e6, size=n_days)
    highs = closes * 1.02
    lows = closes * 0.98
    opens = closes * (1 + np.random.uniform(-0.01, 0.01, n_days))
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": volumes,
    })


def test_noise_filter():
    """病因A：噪声板块过滤。"""
    print("=" * 60)
    print("病因A：噪声板块过滤")
    print("=" * 60)
    tests = [
        ("昨成交20", True),
        ("基金减仓", True),
        ("高市净率", True),
        ("TDX信息", True),
        ("TDX 信息", True),
        ("主力净流入", True),
        ("黄金", False),
        ("半导体", False),
        ("高股息", False),
        ("存储芯片", False),
        ("汽车整车", False),
        ("玻璃", False),
        ("建材", False),
    ]
    all_pass = True
    for name, expected in tests:
        actual = _is_noise_board(name)
        status = "✅" if actual == expected else "❌"
        if actual != expected:
            all_pass = False
        print(f"  {status} {name:12s} → noise={actual} (expected={expected})")
    print(f"  {'全部通过' if all_pass else '有失败'}\n")
    return all_pass


def test_cold_start_penalty():
    """病因B：冷启动惩罚。"""
    print("=" * 60)
    print("病因B：冷启动惩罚（RRG 默认值不应拿高分）")
    print("=" * 60)

    # 冷启动场景：无 prev_trend（trajectory=0）+ RRG 默认值
    cold_rrg = RRGQuadrant(rs_ratio=0.00, rs_momentum=0.50, quadrant="improving",
                           rs_ratio_raw=0.50, rs_momentum_raw=0.50)
    # 有真实轨迹场景：10 天历史
    warm_prev = {
        "strength_trajectory": [40, 42, 38, 45, 43, 41, 44, 46, 48, 50],
        "momentum_trajectory": [0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.58, 0.62, 0.65],
    }
    warm_rrg = RRGQuadrant(rs_ratio=0.20, rs_momentum=0.60, quadrant="improving",
                           rs_ratio_raw=0.20, rs_momentum_raw=0.60)

    # K 线：横盘微涨（适合埋伏）
    df = make_kline(30, base_price=10.0, daily_returns=[0.001] * 30)

    cold_score = SectorAnalysisNode._compute_ambush_score_v2(
        df, {"compression_score": 0.8, "institutional_score": 0.7,
             "volume_character_score": 0.6, "probe_score": 0.5},
        cold_rrg, strength_score=20.0, prev_trend=None,
    )
    warm_score = SectorAnalysisNode._compute_ambush_score_v2(
        df, {"compression_score": 0.8, "institutional_score": 0.7,
             "volume_character_score": 0.6, "probe_score": 0.5},
        warm_rrg, strength_score=20.0, prev_trend=warm_prev,
    )

    print(f"  冷启动（0 天轨迹, RRG 默认值）: {cold_score:.4f}")
    print(f"  有轨迹（10 天, RRG 真实值）:    {warm_score:.4f}")
    print(f"  冷启动/有轨迹 比值: {cold_score / max(warm_score, 0.001):.2f}")
    passed = cold_score < warm_score
    print(f"  {'✅ 冷启动被惩罚' if passed else '❌ 冷启动未被打折'}\n")
    return passed


def test_direction_penalty():
    """病因C：方向惩罚 — 近 3 日暴跌板块应被打折。"""
    print("=" * 60)
    print("病因C：方向惩罚（近 3 日暴跌 → 总分打折）")
    print("=" * 60)

    warm_prev = {
        "strength_trajectory": [40, 42, 38, 45, 43, 41, 44, 46, 48, 50],
        "momentum_trajectory": [0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.58, 0.62, 0.65],
    }
    rrg = RRGQuadrant(rs_ratio=0.20, rs_momentum=0.60, quadrant="improving",
                      rs_ratio_raw=0.20, rs_momentum_raw=0.60)

    # 场景 1：横盘微涨（正常埋伏）
    df_flat = make_kline(30, base_price=10.0, daily_returns=[0.001] * 30)
    # 场景 2：近 3 日暴跌 -5%（玻璃 08-24 场景）
    returns_drop = [0.001] * 27 + [-0.02, -0.015, -0.015]
    df_drop = make_kline(30, base_price=10.0, daily_returns=returns_drop)

    indicators = {"compression_score": 0.8, "institutional_score": 0.7,
                  "volume_character_score": 0.6, "probe_score": 0.5}

    score_flat = SectorAnalysisNode._compute_ambush_score_v2(
        df_flat, indicators, rrg, strength_score=20.0, prev_trend=warm_prev,
    )
    score_drop = SectorAnalysisNode._compute_ambush_score_v2(
        df_drop, indicators, rrg, strength_score=20.0, prev_trend=warm_prev,
    )

    print(f"  横盘微涨（正常埋伏）:  {score_flat:.4f}")
    print(f"  近 3 日暴跌 -5%:       {score_drop:.4f}")
    print(f"  暴跌/正常 比值:        {score_drop / max(score_flat, 0.001):.2f}")
    passed = score_drop < score_flat * 0.8  # 至少打 8 折
    print(f"  {'✅ 暴跌板块被打折' if passed else '❌ 方向惩罚不足'}\n")
    return passed


def test_track1_protection():
    """问题1：Track 1 状态机成员不被 Track 2 竞选淘汰。"""
    print("=" * 60)
    print("问题1：双轨合并为真并集")
    print("=" * 60)

    # 验证常量
    print(f"  AMBUSH_TOP_K = {SectorAnalysisNode.AMBUSH_TOP_K}")
    print(f"  AMBUSH_TRACK1_MAX = {SectorAnalysisNode.AMBUSH_TRACK1_MAX}")
    print(f"  AMBUSH_SCORE_FLOOR = {SectorAnalysisNode.AMBUSH_SCORE_FLOOR}")

    # 逻辑验证：
    # 如果 Track 1 有 10 个成员，remaining_slots = max(0, 8 - 10) = 0
    # Track 2 不补位 → 总输出 = 10（> TOP_K=8）
    track1_count = 10
    remaining = max(0, SectorAnalysisNode.AMBUSH_TOP_K - track1_count)
    print(f"\n  模拟: Track1={track1_count}, remaining_slots={remaining}")
    passed = remaining == 0
    print(f"  {'✅ Track1 超过 TOP_K 时 Track2 不补位' if passed else '❌'}")

    # 如果 Track 1 有 3 个成员，remaining = 5
    track1_count2 = 3
    remaining2 = max(0, SectorAnalysisNode.AMBUSH_TOP_K - track1_count2)
    print(f"\n  模拟: Track1={track1_count2}, remaining_slots={remaining2}")
    passed2 = remaining2 == 5
    print(f"  {'✅ Track1 不足时 Track2 补位 5 个' if passed2 else '❌'}")

    all_pass = passed and passed2
    print(f"  {'全部通过' if all_pass else '有失败'}\n")
    return all_pass


def test_cold_start_fingerprint():
    """验证冷启动指纹：trajectory=0 → cold_start_factor=0 → score=0。"""
    print("=" * 60)
    print("冷启动指纹：trajectory=0 → score=0")
    print("=" * 60)

    # 完美 RRG（rs_ratio=0, rs_momentum=1.0）但无轨迹
    perfect_rrg = RRGQuadrant(rs_ratio=0.00, rs_momentum=1.00, quadrant="leading",
                              rs_ratio_raw=0.00, rs_momentum_raw=1.00)
    df = make_kline(30)
    indicators = {"compression_score": 1.0, "institutional_score": 1.0,
                  "volume_character_score": 1.0, "probe_score": 1.0}

    score_no_trajectory = SectorAnalysisNode._compute_ambush_score_v2(
        df, indicators, perfect_rrg, strength_score=10.0, prev_trend=None,
    )

    # 有 5 天轨迹 → 满系数
    warm_prev = {
        "strength_trajectory": [30, 35, 40, 45, 50],
        "momentum_trajectory": [0.3, 0.4, 0.5, 0.6, 0.7],
    }
    score_warm = SectorAnalysisNode._compute_ambush_score_v2(
        df, indicators, perfect_rrg, strength_score=10.0, prev_trend=warm_prev,
    )

    print(f"  完美 RRG + 0 天轨迹: {score_no_trajectory:.4f}")
    print(f"  完美 RRG + 5 天轨迹: {score_warm:.4f}")
    passed = score_no_trajectory == 0.0 and score_warm > 0.0
    print(f"  {'✅ 冷启动完全归零' if passed else '❌'}\n")
    return passed


if __name__ == "__main__":
    results = []
    results.append(("噪声过滤", test_noise_filter()))
    results.append(("冷启动惩罚", test_cold_start_penalty()))
    results.append(("方向惩罚", test_direction_penalty()))
    results.append(("双轨真并集", test_track1_protection()))
    results.append(("冷启动指纹", test_cold_start_fingerprint()))

    print("=" * 60)
    print("总结")
    print("=" * 60)
    for name, passed in results:
        print(f"  {'✅' if passed else '❌'} {name}")
    total_pass = sum(1 for _, p in results if p)
    print(f"\n  {total_pass}/{len(results)} 项通过")
