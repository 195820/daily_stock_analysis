# -*- coding: utf-8 -*-
"""
P4 埋伏通道验证脚本

验证内容：
1. _ambush_exit_signal() 四种退出逻辑
2. _assess_risk() 催化剂类型系数
3. AmbushPositionSnapshotRecord 表读写
"""
import sys
import os
import io
from datetime import date, timedelta
from unittest.mock import MagicMock

# 修正 Windows GBK 输出编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# 1. _ambush_exit_signal 退出逻辑验证
# =============================================================================

def test_ambush_exit_signal():
    """测试四种退出触发"""
    from src.got.nodes.portfolio_decision import PortfolioDecisionNode

    node = PortfolioDecisionNode()
    today = date(2026, 8, 19)

    def make_snapshot(category_tag, event_date, entry_date=None, entry_price=10.0):
        snap = MagicMock()
        snap.category_tag = category_tag
        snap.catalyst_event_date = event_date
        snap.entry_date = entry_date or (today - timedelta(days=5))
        snap.entry_price = entry_price
        snap.symbol = "000001"
        snap.sector_name = "测试板块"
        return snap

    # ── fade 型：事件日次日卖 ──
    # 事件日 = 昨天 → 今天 > 事件日 → fade_sell
    snap_fade = make_snapshot("fade", event_date=today - timedelta(days=1))
    signal, reason = node._ambush_exit_signal(snap_fade, today, 10.5)
    assert signal == "fade_sell", f"fade 型应触发 fade_sell，实际: {signal}"
    print(f"✅ fade 型退出: {signal} — {reason}")

    # fade 型：事件日 = 明天 → 不触发
    snap_fade_wait = make_snapshot("fade", event_date=today + timedelta(days=1))
    signal, reason = node._ambush_exit_signal(snap_fade_wait, today, 10.5)
    assert signal is None, f"fade 型事件未到期不应触发，实际: {signal}"
    print(f"✅ fade 型等待: {signal} — {reason}")

    # ── binary 型：事件落空 ──
    # 事件日 = 5 天前 → > 2 天 grace → event_cancelled
    snap_binary_cancel = make_snapshot("binary", event_date=today - timedelta(days=7))
    signal, reason = node._ambush_exit_signal(snap_binary_cancel, today, 10.5)
    assert signal == "event_cancelled", f"binary 落空应触发 event_cancelled，实际: {signal}"
    print(f"✅ binary 型落空: {signal} — {reason}")

    # binary 型：事件日 = 明天 → 不触发
    snap_binary_wait = make_snapshot("binary", event_date=today + timedelta(days=1))
    signal, reason = node._ambush_exit_signal(snap_binary_wait, today, 10.5)
    assert signal is None, f"binary 型事件未到期不应触发，实际: {signal}"
    print(f"✅ binary 型等待: {signal} — {reason}")

    # ── drift 型：漂移衰竭 ──
    # 事件日 = 15 天前，持有期涨幅 < 2% → drift_exhausted
    snap_drift_exhausted = make_snapshot(
        "drift", event_date=today - timedelta(days=20),
        entry_date=today - timedelta(days=20), entry_price=10.0,
    )
    # 当前价 10.1 → 涨幅 1% < 2% → 衰竭
    signal, reason = node._ambush_exit_signal(snap_drift_exhausted, today, 10.1)
    assert signal == "drift_exhausted", f"drift 衰竭应触发，实际: {signal}"
    print(f"✅ drift 型衰竭: {signal} — {reason}")

    # drift 型：硬超时（35 日历日 ≈ 25 交易日 > 20）
    snap_drift_timeout = make_snapshot(
        "drift", event_date=today - timedelta(days=10),
        entry_date=today - timedelta(days=35), entry_price=10.0,
    )
    signal, reason = node._ambush_exit_signal(snap_drift_timeout, today, 10.5)
    assert signal == "drift_timeout", f"drift 硬超时，实际: {signal}"
    print(f"✅ drift 型超时: {signal} — {reason}")

    # drift 型：正常持有（事件日 = 3 天前，涨幅 5%）
    snap_drift_hold = make_snapshot(
        "drift", event_date=today - timedelta(days=5),
        entry_date=today - timedelta(days=5), entry_price=10.0,
    )
    signal, reason = node._ambush_exit_signal(snap_drift_hold, today, 10.5)
    assert signal is None, f"drift 正常持有不应触发，实际: {signal}"
    print(f"✅ drift 型持有: {signal} — {reason}")

    # ── 硬止损（任意类型）──
    snap_stop = make_snapshot("drift", event_date=today + timedelta(days=5), entry_price=10.0)
    signal, reason = node._ambush_exit_signal(snap_stop, today, 9.0)  # -10%
    assert signal == "stop_loss", f"止损应触发，实际: {signal}"
    print(f"✅ 硬止损: {signal} — {reason}")

    print("\n✅ _ambush_exit_signal 全部通过")


# =============================================================================
# 2. _assess_risk 催化剂类型系数验证
# =============================================================================

def test_assess_risk_catalyst_coeff():
    """测试类型化仓位系数"""
    from src.got.nodes.portfolio_decision import PortfolioDecisionNode
    from src.got.schemas import (
        UnifiedContext, MarketRegime, SectorIntel, AmbushCandidate,
    )

    node = PortfolioDecisionNode()

    # 构建 unified context
    # 主线板块
    mainline_intel = SectorIntel(
        sector_name="AI硬件",
        sector_role="mainline",
        classification="actionable",
        composite_score=80.0,
        confidence=0.9,
    )
    # 埋伏板块 drift
    ambush_drift_intel = SectorIntel(
        sector_name="存储芯片",
        sector_role="ambush",
        classification="actionable",
        composite_score=65.0,
        confidence=0.8,
    )
    # 埋伏板块 binary
    ambush_binary_intel = SectorIntel(
        sector_name="商业航天",
        sector_role="ambush",
        classification="actionable",
        composite_score=60.0,
        confidence=0.7,
    )

    # 埋伏池
    drift_candidate = AmbushCandidate(
        sector_name="存储芯片",
        catalyst_score=0.8,
        category_tag="drift",
        events=["SK 海力士 HBM 指引"],
        top_event_date=date(2026, 8, 25),
        top_event_level=3,
    )
    binary_candidate = AmbushCandidate(
        sector_name="商业航天",
        catalyst_score=0.5,
        category_tag="binary",
        events=["SpaceX 星舰试飞"],
        top_event_date=date(2026, 8, 28),
        top_event_level=2,
    )

    unified = UnifiedContext(
        status="normal",
        unified_signal="neutral",
        overall_confidence=0.7,
        final_position_limit=0.7,
        confirmed_sectors=[mainline_intel, ambush_drift_intel, ambush_binary_intel],
        ambush_pool=[drift_candidate, binary_candidate],
        market_regime=MarketRegime(
            regime="transition",
            risk_budget=0.5,
        ),
    )

    portfolio = {
        "total_equity": 100000.0,
        "total_market_value": 50000.0,
        "total_cash": 50000.0,
        "accounts": [],
    }

    result = node._assess_risk(unified, portfolio)
    limits = result.sector_exposure_limits

    # 验证主线板块：无催化剂系数（×1.0）
    mainline_limit = limits.get("AI硬件", 0)
    # 验证埋伏 drift：×1.0
    drift_limit = limits.get("存储芯片", 0)
    # 验证埋伏 binary：×0.4
    binary_limit = limits.get("商业航天", 0)

    print(f"主线 AI硬件 仓位上限: {mainline_limit:.3f}")
    print(f"埋伏 drift 存储芯片: {drift_limit:.3f}")
    print(f"埋伏 binary 商业航天: {binary_limit:.3f}")

    # binary 应显著小于 drift（×0.4 vs ×1.0）
    assert binary_limit < drift_limit, (
        f"binary 仓位上限应小于 drift: {binary_limit} < {drift_limit}"
    )
    # drift / binary 比例应接近 1.0 / 0.4 = 2.5（其他因素相同）
    if binary_limit > 0:
        ratio = drift_limit / binary_limit
        assert 2.0 < ratio < 3.0, f"drift/binary 比例应接近 2.5，实际 {ratio:.2f}"
        print(f"✅ drift/binary 比例: {ratio:.2f}（预期 ~2.5）")

    print("\n✅ _assess_risk 催化剂类型系数验证通过")


# =============================================================================
# 3. AmbushPositionSnapshotRecord 表读写验证
# =============================================================================

def test_snapshot_table():
    """测试快照表创建和读写"""
    from src.storage import AmbushPositionSnapshotRecord, DatabaseManager, Base

    db = DatabaseManager()
    # 确保表已创建（通过 DatabaseManager 获取引擎）
    Base.metadata.create_all(db._engine)

    today = date.today()

    # 写入一条快照
    with db.get_session() as session:
        record = AmbushPositionSnapshotRecord(
            symbol="000001",
            sector_name="测试板块",
            category_tag="drift",
            catalyst_event_id="test_event_001",
            catalyst_event_title="测试催化剂事件",
            catalyst_event_date=today + timedelta(days=5),
            impact_level=2,
            consensus_expectation="预期超预期",
            entry_date=today,
            entry_price=10.5,
            is_open=True,
        )
        session.merge(record)
        session.commit()

    # 读取并验证
    with db.get_session() as session:
        loaded = (
            session.query(AmbushPositionSnapshotRecord)
            .filter_by(symbol="000001", is_open=True)
            .first()
        )
        assert loaded is not None, "快照写入失败"
        assert loaded.category_tag == "drift"
        assert loaded.sector_name == "测试板块"
        assert loaded.entry_price == 10.5
        print(f"✅ 快照写入读取成功: {loaded.symbol} {loaded.sector_name} {loaded.category_tag}")

    # 关闭快照
    with db.get_session() as session:
        (
            session.query(AmbushPositionSnapshotRecord)
            .filter_by(symbol="000001", is_open=True)
            .update({
                "exit_signal": "drift_exhausted",
                "exit_date": today,
                "exit_price": 10.8,
                "is_open": False,
            })
        )
        session.commit()

    # 验证关闭后 is_open=False
    with db.get_session() as session:
        closed = (
            session.query(AmbushPositionSnapshotRecord)
            .filter_by(symbol="000001")
            .first()
        )
        assert closed.is_open == False
        assert closed.exit_signal == "drift_exhausted"
        assert closed.exit_price == 10.8
        print(f"✅ 快照关闭成功: exit_signal={closed.exit_signal}, exit_price={closed.exit_price}")

    # 清理测试数据
    with db.get_session() as session:
        session.query(AmbushPositionSnapshotRecord).filter_by(symbol="000001").delete()
        session.commit()

    print("\n✅ AmbushPositionSnapshotRecord 表读写验证通过")


# =============================================================================
# 主函数
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("P4 埋伏通道验证")
    print("=" * 60)

    print("\n--- 1. _ambush_exit_signal 退出逻辑 ---")
    test_ambush_exit_signal()

    print("\n--- 2. _assess_risk 催化剂类型系数 ---")
    test_assess_risk_catalyst_coeff()

    print("\n--- 3. AmbushPositionSnapshotRecord 表读写 ---")
    test_snapshot_table()

    print("\n" + "=" * 60)
    print("✅ P4 验证全部通过")
    print("=" * 60)
