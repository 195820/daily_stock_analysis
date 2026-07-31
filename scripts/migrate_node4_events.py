# -*- coding: utf-8 -*-
"""
Node 4 事件迁移脚本

从 data/got_daily/YYYYMMDD/news.json 历史档中提取事件，
反向填充 node4_events 表，建立跨日记忆的初始数据。

使用方法：
    python scripts/migrate_node4_events.py [--days 30]

参数：
    --days: 从历史档中迁移最近 N 天的数据（默认 30 天）
"""

import argparse
import hashlib
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.storage import DatabaseManager, Node4EventRecord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def load_news_archives(days: int = 30) -> Dict[date, List[Dict[str, Any]]]:
    """加载最近 N 天的 news.json 历史档。

    Returns:
        {date: [event_dict, ...]}
    """
    archive_dir = project_root / "data" / "got_daily"
    if not archive_dir.exists():
        logger.warning("历史档目录不存在: %s", archive_dir)
        return {}

    today = date.today()
    cutoff_date = today - timedelta(days=days)

    archives = {}
    for day_dir in sorted(archive_dir.iterdir()):
        if not day_dir.is_dir():
            continue

        # 解析日期目录名（YYYYMMDD）
        try:
            day_date = datetime.strptime(day_dir.name, "%Y%m%d").date()
        except ValueError:
            continue

        if day_date < cutoff_date:
            continue

        news_file = day_dir / "news.json"
        if not news_file.exists():
            logger.debug("跳过 %s（无 news.json）", day_dir.name)
            continue

        try:
            with open(news_file, "r", encoding="utf-8") as f:
                news_data = json.load(f)

            events = news_data.get("major_events", [])
            if events:
                archives[day_date] = events
                logger.info("加载 %s: %d 个事件", day_dir.name, len(events))

        except Exception as e:
            logger.error("读取 %s 失败: %s", news_file, e)

    return archives


def generate_event_id(title: str, event_date: date) -> str:
    """生成事件 ID（基于标题和日期的哈希）。"""
    content = f"{title}|{event_date.isoformat()}"
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def migrate_events_to_db(archives: Dict[date, List[Dict[str, Any]]]) -> int:
    """将历史事件迁移到 node4_events 表。

    Returns:
        迁移的事件总数
    """
    if not archives:
        logger.warning("无历史事件可迁移")
        return 0

    db = DatabaseManager()
    migrated_count = 0

    with db.get_session() as session:
        # 按日期排序，确保先处理旧事件
        for event_date in sorted(archives.keys()):
            events = archives[event_date]

            for event in events:
                title = event.get("title", "")
                if not title:
                    continue

                # 生成 event_id
                event_id = generate_event_id(title, event_date)

                # 检查是否已存在
                existing = session.query(Node4EventRecord).filter_by(event_id=event_id).first()
                if existing:
                    logger.debug("事件已存在，跳过: %s", title)
                    continue

                # 创建新事件记录
                db_event = Node4EventRecord(
                    event_id=event_id,
                    title=title,
                    source=event.get("source_type", "unknown"),
                    first_seen=event_date,
                    last_seen=event_date,
                    mention_count=1,
                    lifecycle_state="archived",  # 历史事件默认为 archived
                    category=event.get("event_type"),
                    significance_score=1.0 if event.get("impact_level") == "high" else 0.5,
                )
                db_event.set_sources_seen([event.get("source_type", "unknown")])

                # 设置情感轨迹
                sentiment = event.get("sentiment", "neutral")
                sentiment_value = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}.get(sentiment, 0.0)
                db_event.set_sentiment_trajectory([sentiment_value])

                session.add(db_event)
                migrated_count += 1

        try:
            session.commit()
            logger.info("迁移完成: %d 个事件", migrated_count)
        except Exception as e:
            logger.error("提交失败: %s", e)
            session.rollback()
            return 0

    return migrated_count


def main():
    parser = argparse.ArgumentParser(description="Node 4 事件迁移脚本")
    parser.add_argument("--days", type=int, default=30, help="迁移最近 N 天的数据（默认 30）")
    args = parser.parse_args()

    logger.info("开始迁移 Node 4 事件（最近 %d 天）", args.days)

    # 加载历史档
    archives = load_news_archives(args.days)
    logger.info("加载了 %d 天的历史事件", len(archives))

    # 迁移到数据库
    migrated_count = migrate_events_to_db(archives)
    logger.info("迁移完成: %d 个事件", migrated_count)


if __name__ == "__main__":
    main()
