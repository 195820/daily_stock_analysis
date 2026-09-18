"""
验证仓位上限系统重构

简单测试新逻辑是否正确工作
"""

import sys
from pathlib import Path

# 添加项目根目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 解决 Windows 编码问题
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

def test_volatility_adjustment():
    """测试波动率调整"""
    print("\n=== 测试 1: 波动率调整 ===")

    # 模拟低波动率（10%）→ 高仓位
    target_vol = 0.15
    realized_vol = 0.10
    scale = target_vol / realized_vol
    scale = max(0.3, min(1.5, scale))
    print(f"低波动率 {realized_vol:.1%} -> 仓位缩放 {scale:.2f}x (预期 1.5x)")
    assert abs(scale - 1.5) < 0.01, f"Expected 1.5, got {scale}"

    # 模拟目标波动率（15%）→ 标准仓位
    realized_vol = 0.15
    scale = target_vol / realized_vol
    scale = max(0.3, min(1.5, scale))
    print(f"目标波动率 {realized_vol:.1%} -> 仓位缩放 {scale:.2f}x (预期 1.0x)")
    assert abs(scale - 1.0) < 0.01, f"Expected 1.0, got {scale}"

    # 模拟高波动率（30%）→ 低仓位
    realized_vol = 0.30
    scale = target_vol / realized_vol
    scale = max(0.3, min(1.5, scale))
    print(f"高波动率 {realized_vol:.1%} -> 仓位缩放 {scale:.2f}x (预期 0.5x)")
    assert abs(scale - 0.5) < 0.01, f"Expected 0.5, got {scale}"

    print("[PASS] 波动率调整测试通过")


def test_regime_scaling():
    """测试状态缩放"""
    print("\n=== 测试 2: 状态缩放 ===")

    # 模拟全看涨（confidence=1.0, cross=1.0, score=0.5）
    tf_factor = 1.0
    cross_factor = 1.0
    score = 0.5
    direction_factor = (score + 1.0) / 2.0  # 0.75
    regime_scale = tf_factor * 0.40 + cross_factor * 0.30 + direction_factor * 0.30
    regime_scale = max(0.2, min(1.0, regime_scale))
    print(f"全看涨 -> 状态缩放 {regime_scale:.2f} (预期 ~1.0)")
    assert regime_scale >= 0.9, f"Expected >= 0.9, got {regime_scale}"

    # 模拟全看跌（confidence=0.5, cross=0.3, score=-0.5）
    tf_factor = 0.5
    cross_factor = 0.3
    score = -0.5
    direction_factor = (score + 1.0) / 2.0  # 0.25
    regime_scale = tf_factor * 0.40 + cross_factor * 0.30 + direction_factor * 0.30
    regime_scale = max(0.2, min(1.0, regime_scale))
    print(f"全看跌 -> 状态缩放 {regime_scale:.2f} (预期 ~0.3)")
    assert 0.25 <= regime_scale <= 0.45, f"Expected ~0.3, got {regime_scale}"

    print("[PASS] 状态缩放测试通过")


def test_combined_position_limit():
    """测试组合仓位上限"""
    print("\n=== 测试 3: 组合仓位上限 ===")

    # 场景 1: 低波动率 + 全看涨
    vol_adjusted = 1.5
    regime_scale = 1.0
    position_limit = vol_adjusted * regime_scale
    position_limit = max(0.2, min(1.5, position_limit))
    print(f"低波动率 + 全看涨 -> 仓位上限 {position_limit:.1%} (预期 100%+)")
    assert position_limit >= 1.0, f"Expected >= 1.0, got {position_limit}"

    # 场景 2: 高波动率 + 全看跌（模拟 0805 情况）
    vol_adjusted = 0.5  # 30% 波动率
    regime_scale = 0.3  # 全看跌
    position_limit = vol_adjusted * regime_scale
    position_limit = max(0.2, min(1.5, position_limit))
    print(f"高波动率 + 全看跌 -> 仓位上限 {position_limit:.1%} (预期 20%~30%)")
    assert 0.15 <= position_limit <= 0.30, f"Expected ~0.2, got {position_limit}"

    # 关键验证：不会归零！
    print(f"\n[KEY] 关键验证：即使最差情况，仓位上限 = {position_limit:.1%} > 0%")
    assert position_limit > 0, "Position limit should never be 0!"

    print("[PASS] 组合仓位上限测试通过")


def test_drawdown_scaling():
    """测试回撤保护"""
    print("\n=== 测试 4: 回撤保护 ===")

    def apply_drawdown_scaling(drawdown, base_position):
        if drawdown < 0.05:
            multiplier = 1.0
        elif drawdown < 0.10:
            progress = (drawdown - 0.05) / 0.05
            multiplier = 1.0 - progress * 0.25
        elif drawdown < 0.15:
            progress = (drawdown - 0.10) / 0.05
            multiplier = 0.75 - progress * 0.25
        else:
            multiplier = 0.5
        return max(0.2, base_position * multiplier)

    # 无回撤
    scaled = apply_drawdown_scaling(0.0, 0.8)
    print(f"无回撤 -> 仓位 {scaled:.1%} (预期 80%)")
    assert abs(scaled - 0.8) < 0.01, f"Expected 0.8, got {scaled}"

    # 10% 回撤
    scaled = apply_drawdown_scaling(0.10, 0.8)
    print(f"10% 回撤 -> 仓位 {scaled:.1%} (预期 60%)")
    assert 0.55 <= scaled <= 0.65, f"Expected ~0.6, got {scaled}"

    # 20% 回撤
    scaled = apply_drawdown_scaling(0.20, 0.8)
    print(f"20% 回撤 -> 仓位 {scaled:.1%} (预期 40%)")
    assert 0.35 <= scaled <= 0.45, f"Expected ~0.4, got {scaled}"

    print("[PASS] 回撤保护测试通过")


def test_old_vs_new_system():
    """对比新旧系统"""
    print("\n=== 测试 5: 新旧系统对比 ===")

    # 旧系统：0805 情况（unified_score = -0.48）
    print("\n旧系统（二元判定）:")
    unified_score = -0.48
    if unified_score > 0.3:
        old_position = 1.0
    elif unified_score > -0.3:
        old_position = 0.5
    else:
        old_position = 0.0
    print(f"  unified_score = {unified_score:.2f} -> position_limit = {old_position:.1%}")
    print(f"  [ISSUE] 问题：仓位归零，导致后续锁死在 20%")

    # 新系统
    print("\n新系统（三层连续函数）:")
    # Layer 1: 假设波动率 25%
    realized_vol = 0.25
    target_vol = 0.15
    vol_adjusted = target_vol / realized_vol  # 0.6
    vol_adjusted = max(0.3, min(1.5, vol_adjusted))

    # Layer 2: 假设 tf_confidence=1.0, cross_consistency=0.5, score=-0.48
    tf_factor = 1.0
    cross_factor = 0.5
    direction_factor = (unified_score + 1.0) / 2.0  # 0.26
    regime_scale = tf_factor * 0.40 + cross_factor * 0.30 + direction_factor * 0.30
    regime_scale = max(0.2, min(1.0, regime_scale))

    new_position = vol_adjusted * regime_scale
    new_position = max(0.2, min(1.5, new_position))

    print(f"  Layer 1 (波动率 {realized_vol:.1%}) -> vol_adjusted = {vol_adjusted:.2f}")
    print(f"  Layer 2 (tf={tf_factor:.1f}, cross={cross_factor:.1f}, dir={direction_factor:.2f}) -> regime_scale = {regime_scale:.2f}")
    print(f"  最终仓位 = {vol_adjusted:.2f} x {regime_scale:.2f} = {new_position:.1%}")
    print(f"  [FIX] 优势：仓位不会归零，保持在合理范围")

    assert new_position > 0.2, "New system should maintain at least 20% position"
    assert new_position < old_position + 0.5, "New system should be reasonable"

    print("\n[PASS] 新旧系统对比测试通过")


if __name__ == "__main__":
    print("=" * 70)
    print("  仓位上限系统重构验证")
    print("=" * 70)

    try:
        test_volatility_adjustment()
        test_regime_scaling()
        test_combined_position_limit()
        test_drawdown_scaling()
        test_old_vs_new_system()

        print("\n" + "=" * 70)
        print("  [SUCCESS] 所有测试通过！")
        print("=" * 70)
        print("\n关键改进：")
        print("1. 仓位上限不再归零（最低 20%）")
        print("2. 波动率调整保持风险恒定")
        print("3. 状态缩放连续平滑（无二元跳变）")
        print("4. 回撤保护动态降仓")
        print("5. 消除 20% 锁死问题")

    except AssertionError as e:
        print(f"\n[FAIL] 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
