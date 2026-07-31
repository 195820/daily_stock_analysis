# -*- coding: utf-8 -*-
"""
Node 2 板块趋势迁移脚本

从 data/got_daily/YYYYMMDD/sector.json 历史档中提取板块数据，
反向填充 node2_sector_trends 表，建立跨日记忆的初始数据。

使用方法：
    python scripts/migrate_node2_trends.py [--days 30]

参数：
    --days: 从历史档中迁移最近 N 天的数据（默认 30 天）
"""

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.storage import DatabaseManager, Node2SectorTrendRecord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def load_sector_archives(days: int = 30) -> Dict[date, List[Dict[str, Any]]]:
    """加载最近 N 天的 sector.json 历史档。

    Returns:
        {date: [sector_dict, ...]}
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

        sector_file = day_dir / "sector.json"
        if not sector_file.exists():
            logger.debug("跳过 %s（无 sector.json）", day_dir.name)
            continue

        try:
            with open(sector_file, "r", encoding="utf-8") as f:
                sector_data = json.load(f)

            # 提取所有板块（主线 + 衰竭 + 埋伏 + 防守）
            sectors = []
            for category in ["active_mainline", "exhausted_mainline", "ambush", "defensive_rotation"]:
                for sector in sector_data.get(category, []):
                    sectors.append(sector)

            if sectors:
                archives[day_date] = sectors
                logger.info("加载 %s: %d 个板块", day_dir.name, len(sectors))

        except Exception as e:
            logger.error("读取 %s 失败: %s", sector_file, e)

    return archives


def state_to_signal(state: str) -> str:
    """将板块状态转换为信号方向。"""
    mapping = {
        "active_mainline": "bullish",
        "exhausted_mainline": "bearish",
        "ambush": "neutral",
        "defensive_rotation": "neutral",
    }
    return mapping.get(state, "neutral")


def migrate_sectors_to_db(archives: Dict[date, List[Dict[str, Any]]]) -> int:
    """将历史板块数据迁移到 node2_sector_trends 表。

    Returns:
        迁移的板块记录总数
    """
    if not archives:
        logger.warning("无历史板块数据可迁移")
        return 0

    db = DatabaseManager()
    migrated_count = 0

    with db.get_session() as session:
        # 按日期排序，确保先处理旧数据
        for archive_date in sorted(archives.keys()):
            sectors = archives[archive_date]

            for sector in sectors:
                code = sector.get("code", "")
                name = sector.get("name", "")
                if not code or not name:
                    continue

                # 检查是否已存在
                existing = session.query(Node2SectorTrendRecord).filter_by(
                    sector_code=code,
                    tracking_date=archive_date,
                ).first()

                if existing:
                    logger.debug("板块记录已存在，跳过: %s (%s)", name, code)
                    continue

                # 提取板块数据
                state = sector.get("state", "ambush")
                strength_score = sector.get("strength_score", 0.0)
                lifecycle = sector.get("lifecycle", {})
                lifecycle_phase = lifecycle.get("phase") if lifecycle else None

                # 创建新记录
                db_record = Node2SectorTrendRecord(
                    sector_code=code,
                    sector_name=name,
                    tracking_date=archive_date,
                    unified_signal=state_to_signal(state),
                    lifecycle_phase=lifecycle_phase,
                    strength_score=strength_score,
                    is_mainline=(state in ("active_mainline", "exhausted_mainline")),
                    mainline_days=0,  # 历史数据无法准确计算，设为 0
                    trend_direction="stable",  # 历史数据无法准确计算，设为 stable
                )
                db_record.set_strength_trajectory([strength_score])

                session.add(db_record)
                migrated_count += 1

        try:
            session.commit()
            logger.info("迁移完成: %d 条板块记录", migrated_count)
        except Exception as e:
            logger.error("提交失败: %s", e)
            session.rollback()
            return 0

    return migrated_count


def main():
    parser = argparse.ArgumentParser(description="Node 2 板块趋势迁移脚本")
    parser.add_argument("--days", type=int, default=30, help="迁移最近 N 天的数据（默认 30）")
    args = parser.parse_args()

    logger.info("开始迁移 Node 2 板块趋势（最近 %d 天）", args.days)

    # 加载历史档
    archives = load_sector_archives(args.days)
    logger.info("加载了 %d 天的历史板块数据", len(archives))

    # 迁移到数据库
    migrated_count = migrate_sectors_to_db(archives)
    logger.info("迁移完成: %d 条板块记录", migrated_count)


if __name__ == "__main__":
    main()
