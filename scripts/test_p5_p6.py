# -*- coding: utf-8 -*-
"""
P5/P6 验证脚本

验证内容：
1. P5: 催化剂关联度重排 — _score_catalyst_relevance()
2. P6: CatalystRealizationRecord 表读写
3. P6: CatalystRealizationTracker 兑现检测 + 统计
"""
import sys
import os
import io
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

# 修正 Windows GBK 输出编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =============================================================================
# 1. P5 催化剂关联度重排验证
# =============================================================================

def test_p5_catalyst_relevance():
    """测试 _score_catalyst_relevance 评分逻辑"""
    from src.services.stock_screener import StockScreenerPipeline
    from src.config import Config

    config = Config()
    service = StockScreenerPipeline(config)

    # 构造测试数据
    candidates = [
        {"code": "600519", "name": "贵州茅台", "sector": "白酒", "sector_type": "ambush"},
        {"code": "000858", "name": "五粮液", "sector": "白酒", "sector_type": "ambush"},
        {"code": "601318", "name": "中国平安", "sector": "保险", "sector_type": "active_mainline"},
    ]

    # 构造催化剂日历（嵌入 news_context）
    today = date.today()
    got_context = {
        "news_context": {
            "catalyst_calendar": {
                "status": "normal",
                "target_date": str(today),
                "window_days": 14,
                "events": [
                    {
                        "event_id": "test_earnings_001",
                        "event_date": str(today + timedelta(days=3)),
                        "event_type": "earnings",
                        "title": "贵州茅台(600519) 财报披露",
                        "sector": "白酒",
                        "impact_level": 2,
                        "direction": "positive",
                        "confidence": 0.95,
                        "source": "akshare_earnings",
                        "category_tag": "drift",
                    },
                    {
                        "event_id": "test_narrative_001",
                        "event_date": str(today + timedelta(days=7)),
                        "event_type": "narrative",
                        "title": "白酒消费升级政策催化",
                        "sector": "白酒",
                        "impact_level": 2,
                        "direction": "positive",
                        "confidence": 0.75,
                        "source": "llm_narrative",
                        "category_tag": "drift",
                    },
                    {
                        "event_id": "test_low_conf_001",
                        "event_date": str(today + timedelta(days=5)),
                        "event_type": "narrative",
                        "title": "保险行业低置信事件",
                        "sector": "保险",
                        "impact_level": 1,
                        "direction": "positive",
                        "confidence": 0.5,  # < 0.7，不参与评分
                        "source": "llm_narrative",
                        "category_tag": "",
                    },
                ],
            }
        }
    }

    # 执行评分
    scores, details = service._score_catalyst_relevance(candidates, got_context)

    # 验证：600519 有个股级事件（earnings），score=100
    assert scores.get("600519") == 100.0, (
        f"600519 有个股级事件应 score=100，实际: {scores.get('600519')}"
    )
    assert details.get("600519", {}).get("event_type") == "earnings"
    print(f"✅ 600519 个股级事件置顶: score={scores['600519']}, title={details['600519']['event_title']}")

    # 验证：000858 有板块级事件（narrative），score < 100
    score_000858 = scores.get("000858", 0)
    assert 0 < score_000858 < 100, (
        f"000858 有板块级事件应 0<score<100，实际: {score_000858}"
    )
    print(f"✅ 000858 板块级事件: score={score_000858}")

    # 验证：601318 保险的低置信事件不参与评分
    score_601318 = scores.get("601318", 0)
    assert score_601318 == 0, (
        f"601318 低置信事件不应评分，实际: {score_601318}"
    )
    print(f"✅ 601318 低置信事件不评分: score={score_601318}")

    # 验证：无日历数据时返回空
    empty_scores, empty_details = service._score_catalyst_relevance(candidates, {})
    assert empty_scores == {}, f"无日历应返回空，实际: {empty_scores}"
    print(f"✅ 无日历数据时返回空")

    print("\n✅ P5 _score_catalyst_relevance 全部通过")


# =============================================================================
# 2. P6 CatalystRealizationRecord 表读写验证
# =============================================================================

def test_p6_realization_table():
    """测试 CatalystRealizationRecord 表创建和读写"""
    from src.storage import CatalystRealizationRecord, DatabaseManager, Base

    db = DatabaseManager()
    Base.metadata.create_all(db._engine)

    today = date.today()
    event_id = "test_event_p6_001"

    # 写入
    with db.get_session() as session:
        record = CatalystRealizationRecord(
            event_id=event_id,
            event_type="narrative",
            source="llm_narrative",
            source_type="forward",
            sector="AI硬件",
            title="测试催化剂事件",
            event_date=today - timedelta(days=5),
            category_tag="drift",
            impact_level=2,
            consensus_expectation="预期超预期",
            direction="positive",
            confidence=0.85,
            is_realized=False,
        )
        session.merge(record)
        session.commit()
        print(f"✅ 写入归档记录: {event_id}")

    # 读取
    with db.get_session() as session:
        loaded = (
            session.query(CatalystRealizationRecord)
            .filter_by(event_id=event_id)
            .first()
        )
        assert loaded is not None, "归档记录写入失败"
        assert loaded.sector == "AI硬件"
        assert loaded.is_realized == False
        print(f"✅ 读取归档记录: {loaded.event_id} {loaded.sector}")

    # 更新为已兑现
    with db.get_session() as session:
        (
            session.query(CatalystRealizationRecord)
            .filter_by(event_id=event_id)
            .update({
                "is_realized": True,
                "realization_date": today,
                "actual_return": 3.5,
                "drift_direction_correct": True,
                "story_lifetime_days": 5,
            })
        )
        session.commit()

    # 验证兑现更新
    with db.get_session() as session:
        realized = (
            session.query(CatalystRealizationRecord)
            .filter_by(event_id=event_id)
            .first()
        )
        assert realized.is_realized == True
        assert realized.actual_return == 3.5
        assert realized.drift_direction_correct == True
        print(f"✅ 兑现更新: return={realized.actual_return}%, correct={realized.drift_direction_correct}")

    # 清理测试数据
    with db.get_session() as session:
        session.query(CatalystRealizationRecord).filter_by(event_id=event_id).delete()
        session.commit()

    print("\n✅ P6 CatalystRealizationRecord 表读写验证通过")


# =============================================================================
# 3. P6 CatalystRealizationTracker 统计验证
# =============================================================================

def test_p6_tracker_stats():
    """测试 CatalystRealizationTracker 统计方法"""
    from src.got.catalyst_realization import CatalystRealizationTracker
    from src.storage import CatalystRealizationRecord, DatabaseManager, Base

    db = DatabaseManager()
    Base.metadata.create_all(db._engine)

    tracker = CatalystRealizationTracker()
    today = date.today()

    # 写入 3 条测试数据
    test_events = [
        {
            "event_id": "test_stats_001",
            "event_type": "earnings",
            "source": "akshare_earnings",
            "source_type": "forward",
            "sector": "白酒",
            "event_date": today - timedelta(days=10),
            "category_tag": "drift",
            "direction": "positive",
            "is_realized": True,
            "actual_return": 5.0,
            "drift_direction_correct": True,
            "story_lifetime_days": 10,
        },
        {
            "event_id": "test_stats_002",
            "event_type": "narrative",
            "source": "llm_narrative",
            "source_type": "forward",
            "sector": "AI硬件",
            "event_date": today - timedelta(days=7),
            "category_tag": "fade",
            "direction": "positive",
            "is_realized": True,
            "actual_return": -2.0,
            "drift_direction_correct": False,
            "story_lifetime_days": 3,
        },
        {
            "event_id": "test_stats_003",
            "event_type": "earnings",
            "source": "akshare_earnings",
            "source_type": "forward",
            "sector": "半导体",
            "event_date": today - timedelta(days=3),
            "category_tag": "drift",
            "direction": "positive",
            "is_realized": False,
        },
    ]

    with db.get_session() as session:
        for evt in test_events:
            record = CatalystRealizationRecord(**evt)
            session.merge(record)
        session.commit()

    # 测试统计
    stats = tracker.get_realization_stats(days=30)
    assert stats["total_events"] == 3, f"总事件数应为 3，实际: {stats['total_events']}"
    assert stats["realized_events"] == 2, f"已兑现应为 2，实际: {stats['realized_events']}"
    assert 0.6 < stats["realization_rate"] < 0.7, f"兑现率应 ~0.67，实际: {stats['realization_rate']}"
    print(f"✅ 兑现统计: total={stats['total_events']}, realized={stats['realized_events']}, rate={stats['realization_rate']:.2f}")

    # 测试方向准确率
    assert stats["direction_accuracy"] == 0.5, f"方向准确率应为 0.5，实际: {stats['direction_accuracy']}"
    print(f"✅ 方向准确率: {stats['direction_accuracy']:.2f}")

    # 测试按 event_type 分组
    by_type = stats["by_event_type"]
    assert "earnings" in by_type, "earnings 类型应存在"
    assert by_type["earnings"]["total"] == 2, f"earnings 应为 2，实际: {by_type['earnings']['total']}"
    print(f"✅ 按 event_type 分组: earnings={by_type['earnings']['total']}, narrative={by_type.get('narrative', {}).get('total', 0)}")

    # 测试故事寿命
    lifetimes = tracker.get_story_lifetimes(days=30)
    assert "earnings" in lifetimes, "earnings 寿命应存在"
    assert lifetimes["earnings"]["count"] == 1, f"earnings 已兑现 1 条，实际: {lifetimes['earnings']['count']}"
    print(f"✅ 故事寿命: earnings avg={lifetimes['earnings']['avg_lifetime']:.1f}天")

    # 清理测试数据
    with db.get_session() as session:
        for evt in test_events:
            session.query(CatalystRealizationRecord).filter_by(event_id=evt["event_id"]).delete()
        session.commit()

    print("\n✅ P6 CatalystRealizationTracker 统计验证通过")


# =============================================================================
# 主函数
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("P5/P6 验证")
    print("=" * 60)

    print("\n--- 1. P5 催化剂关联度重排 ---")
    test_p5_catalyst_relevance()

    print("\n--- 2. P6 CatalystRealizationRecord 表读写 ---")
    test_p6_realization_table()

    print("\n--- 3. P6 CatalystRealizationTracker 统计 ---")
    test_p6_tracker_stats()

    print("\n" + "=" * 60)
    print("✅ P5/P6 验证全部通过")
    print("=" * 60)
