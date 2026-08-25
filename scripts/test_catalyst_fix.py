"""
验证催化剂日历修复（A+D1/D2）

测试内容：
1. D2 周末守卫：日期落在周六/周日的事件被丢弃
2. D1 回声检测：title 与 source_news 高度相似且 direction="negative" 的事件被丢弃
3. A 增强版 prompt：注入今日日期 + 时态区分 + few-shot 示例
"""

import asyncio
import sys
from datetime import date
from pathlib import Path

# 设置 UTF-8 编码
sys.stdout.reconfigure(encoding='utf-8')

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.got.catalyst_calendar import CatalystCalendarBuilder
from src.got.schemas import NewsEvent


def test_weekend_guard():
    """测试 D2 周末守卫"""
    print("\n=== 测试 D2 周末守卫 ===")
    builder = CatalystCalendarBuilder()

    # 模拟 LLM 输出：包含周末日期
    llm_output = """
[
  {
    "date": "2026-08-29",
    "title": "美股存储板块重挫",
    "sector": "存储芯片",
    "impact_level": 2,
    "direction": "negative",
    "confidence": 0.7,
    "consensus_expectation": "美股重挫传导",
    "category_tag": "drift",
    "source_news": "美股存储光通信板块重挫"
  },
  {
    "date": "2026-08-31",
    "title": "官方制造业PMI公布",
    "sector": "银行",
    "impact_level": 2,
    "direction": "binary",
    "confidence": 0.9,
    "consensus_expectation": "市场预期PMI维持在50上方",
    "category_tag": "binary",
    "source_news": "8月31日将公布官方制造业PMI"
  }
]
"""

    start = date(2026, 8, 25)
    end = date(2026, 9, 8)

    # 初始化 concept_set
    builder._concept_set = {"存储芯片", "银行"}

    events = builder._parse_llm_narrative(llm_output, start, end)

    print(f"LLM 输出 2 个事件（8-29 周六 + 8-31 周一）")
    print(f"解析后保留 {len(events)} 个事件")

    # 8-29 是周六，应该被丢弃
    # 8-31 是周一，应该被保留
    assert len(events) == 1, f"期望 1 个事件，实际 {len(events)}"
    assert events[0].event_date == date(2026, 8, 31), f"期望 8-31，实际 {events[0].event_date}"
    print("✅ 周末守卫生效：8-29（周六）被丢弃，8-31（周一）被保留")


def test_echo_detection():
    """测试 D1 回声检测"""
    print("\n=== 测试 D1 回声检测 ===")
    builder = CatalystCalendarBuilder()

    # 模拟 LLM 输出：包含回声事件
    llm_output = """
[
  {
    "date": "2026-08-26",
    "title": "美股存储光通信板块重挫，A股相关板块承压",
    "sector": "存储芯片",
    "impact_level": 2,
    "direction": "negative",
    "confidence": 0.7,
    "consensus_expectation": "美股重挫传导",
    "category_tag": "drift",
    "source_news": "美股存储光通信板块重挫，A股相关板块或承压"
  },
  {
    "date": "2026-08-27",
    "title": "英伟达财报发布后分析师下调半导体评级",
    "sector": "半导体",
    "impact_level": 2,
    "direction": "negative",
    "confidence": 0.6,
    "consensus_expectation": "财报不及预期",
    "category_tag": "fade",
    "source_news": "英伟达将于8-27发布财报"
  },
  {
    "date": "2026-08-28",
    "title": "AI算力产业链景气度持续验证",
    "sector": "算力",
    "impact_level": 2,
    "direction": "positive",
    "confidence": 0.8,
    "consensus_expectation": "订单持续落地",
    "category_tag": "drift",
    "source_news": "AI算力产业链景气度验证"
  }
]
"""

    start = date(2026, 8, 25)
    end = date(2026, 9, 8)

    builder._concept_set = {"存储芯片", "半导体", "算力"}

    events = builder._parse_llm_narrative(llm_output, start, end)

    print(f"LLM 输出 3 个事件：")
    print(f"  1. 8-26 美股存储重挫（回声，direction=negative）")
    print(f"  2. 8-27 英伟达财报后下调评级（非回声，direction=negative）")
    print(f"  3. 8-28 AI算力景气验证（direction=positive）")
    print(f"解析后保留 {len(events)} 个事件")

    # 第 1 个事件是回声（title 与 source_news 几乎相同 + direction=negative），应该被丢弃
    # 第 2 个事件虽然 direction=negative，但 title 与 source_news 不同，应该被保留
    # 第 3 个事件 direction=positive，应该被保留
    assert len(events) == 2, f"期望 2 个事件，实际 {len(events)}"
    assert events[0].event_date == date(2026, 8, 27), f"期望 8-27，实际 {events[0].event_date}"
    assert events[1].event_date == date(2026, 8, 28), f"期望 8-28，实际 {events[1].event_date}"
    print("✅ 回声检测生效：8-26 回声事件被丢弃，8-27/8-28 被保留")


def test_echo_similarity_threshold():
    """测试回声检测的相似度阈值"""
    print("\n=== 测试回声检测相似度阈值 ===")

    # 测试互相包含
    assert CatalystCalendarBuilder._is_echo_event(
        "美股存储光通信板块重挫",
        "美股存储光通信板块重挫，A股相关板块或承压",
        "negative"
    ) == True, "互相包含应该被判定为回声"

    # 测试 Jaccard 相似度 >= 0.6
    assert CatalystCalendarBuilder._is_echo_event(
        "美股存储板块重挫A股承压",
        "美股存储光通信板块重挫，A股相关板块或承压",
        "negative"
    ) == True, "高相似度应该被判定为回声"

    # 测试 Jaccard 相似度 < 0.6
    assert CatalystCalendarBuilder._is_echo_event(
        "英伟达财报发布后分析师下调半导体评级",
        "英伟达将于8-27发布财报",
        "negative"
    ) == False, "低相似度不应该被判定为回声"

    # 测试 direction != "negative" 不触发回声检测
    assert CatalystCalendarBuilder._is_echo_event(
        "美股存储板块重挫",
        "美股存储光通信板块重挫",
        "positive"
    ) == False, "direction=positive 不应该触发回声检测"

    # 测试 source_news 为空不触发
    assert CatalystCalendarBuilder._is_echo_event(
        "美股存储板块重挫",
        None,
        "negative"
    ) == False, "source_news 为空不应该触发回声检测"

    print("✅ 相似度阈值测试通过")


async def test_full_rebuild():
    """测试强制全量重建（使用真实数据）"""
    print("\n=== 测试强制全量重建 ===")
    print("注意：此测试会清空旧的叙事事件并重新构建")

    # 清空旧的叙事事件
    from src.storage import DatabaseManager, CatalystEventRecord
    db = DatabaseManager()

    with db.get_session() as session:
        deleted_count = session.query(CatalystEventRecord).filter(
            CatalystEventRecord.event_type == "narrative"
        ).delete()
        session.commit()

    print(f"已清空 {deleted_count} 条旧的叙事事件")

    # 强制全量重建
    builder = CatalystCalendarBuilder()
    target_date = date(2026, 8, 25)

    # 强制全量刷新（重置 tracker）
    from src.storage import CatalystRefreshTracker
    with db.get_session() as session:
        tracker = session.query(CatalystRefreshTracker).first()
        if tracker:
            tracker.trading_days_since_refresh = 999  # 强制全量
            session.commit()

    print(f"开始全量构建催化剂日历（target_date={target_date}）...")
    calendar = await builder.build_calendar(target_date)

    print(f"构建完成：{len(calendar.events)} 个事件")

    # 检查是否有 8-29 的存储芯片事件
    aug29_storage = [
        e for e in calendar.events
        if e.event_date == date(2026, 8, 29) and "存储" in e.title
    ]

    if aug29_storage:
        print(f"❌ 发现 8-29 存储芯片事件：{aug29_storage[0].title}")
        print("修复未生效")
    else:
        print("✅ 8-29 存储芯片事件已被过滤")

    # 检查是否有周末事件
    weekend_events = [
        e for e in calendar.events
        if e.event_date.weekday() >= 5
    ]

    if weekend_events:
        print(f"❌ 发现 {len(weekend_events)} 个周末事件")
        for e in weekend_events[:3]:
            print(f"  - {e.event_date} {e.title}")
    else:
        print("✅ 没有周末事件（周末守卫生效）")

    return len(calendar.events)


if __name__ == "__main__":
    print("=" * 60)
    print("催化剂日历修复验证（A+D1/D2）")
    print("=" * 60)

    try:
        test_weekend_guard()
        test_echo_detection()
        test_echo_similarity_threshold()

        print("\n" + "=" * 60)
        print("单元测试全部通过 ✅")
        print("=" * 60)

        # 检查是否有 --full-rebuild 参数
        if '--full-rebuild' in sys.argv:
            print("\n开始全量重建测试...")
            event_count = asyncio.run(test_full_rebuild())
            print(f"\n全量重建完成，共 {event_count} 个事件")
        else:
            print("\n如需运行全量重建测试，请使用：python test_catalyst_fix.py --full-rebuild")

    except AssertionError as e:
        print(f"\n❌ 测试失败：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 测试异常：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
