# -*- coding: utf-8 -*-
"""P6 兑现闭环修复（A1）— 一次性清理脚本

用途：
1. ALTER TABLE 添加 resolution 列（如不存在）
2. 对 366 条滞留 is_realized=False 记录重跑三级板块名解析
3. 仍不可解析的批量标记 resolution="unresolvable"

幂等：可重复运行。
"""

import sys
import os
import sqlite3
import difflib
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "stock_analysis.db")


def ensure_resolution_column(conn: sqlite3.Connection):
    """ALTER TABLE 添加 resolution 列（如不存在）"""
    cursor = conn.execute("PRAGMA table_info(catalyst_realizations)")
    columns = {row[1] for row in cursor.fetchall()}
    if "resolution" in columns:
        logger.info("resolution 列已存在，跳过 ALTER TABLE")
        return
    conn.execute(
        "ALTER TABLE catalyst_realizations ADD COLUMN resolution VARCHAR(16)"
    )
    conn.commit()
    logger.info("✅ 已添加 resolution 列")


def rebuild_board_registry() -> dict[str, str]:
    """重建 TDX_BOARD_REGISTRY 查找表"""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from data_provider.tdx_board_mapping import TDX_BOARD_REGISTRY
    return TDX_BOARD_REGISTRY


def build_name_to_code_map(registry: dict) -> dict[str, str]:
    """从 TDX_BOARD_REGISTRY 构建 name → code 反向映射"""
    mapping = {}
    for code, info in registry.items():
        if isinstance(info, dict):
            name = info.get("name", "")
        else:
            name = str(info)
        if name:
            mapping[name] = code
    return mapping


def resolve_board_code(
    sector_name: str, name_map: dict[str, str]
) -> tuple[str | None, str]:
    """三级解析（与 catalyst_realization._get_board_code 同逻辑）"""
    if not sector_name:
        return None, "failed"

    # L1: 精确反向匹配
    code = name_map.get(sector_name)
    if code:
        return code, "exact"

    # L2: 别名表
    try:
        from src.got.board_aliases import normalize_sector_name, sector_name_variants
        normalized = normalize_sector_name(sector_name)
        if normalized != sector_name:
            code = name_map.get(normalized)
            if code:
                return code, "alias"
        for variant in sector_name_variants(sector_name):
            if variant in (sector_name, normalized):
                continue
            code = name_map.get(variant)
            if code:
                return code, "alias"
    except ImportError:
        pass

    # L3: difflib 模糊匹配
    matches = difflib.get_close_matches(
        sector_name, name_map.keys(), n=1, cutoff=0.68
    )
    if matches:
        return name_map[matches[0]], f"fuzzy:{matches[0]}"

    return None, "failed"


def main():
    db_path = os.path.abspath(DB_PATH)
    if not os.path.exists(db_path):
        logger.error(f"数据库不存在: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)

    # Step 1: 确保列存在
    ensure_resolution_column(conn)

    # Step 2: 加载注册表 + 构建反向映射
    registry = rebuild_board_registry()
    name_map = build_name_to_code_map(registry)
    logger.info(f"TDX_BOARD_REGISTRY 共 {len(registry)} 条, 反向映射 {len(name_map)} 条")

    # Step 3: 加载所有滞留记录（排除已标记 unresolvable，含 NULL 处理）
    cursor = conn.execute(
        "SELECT id, sector, event_id FROM catalyst_realizations "
        "WHERE is_realized = 0 AND (resolution IS NULL OR resolution != 'unresolvable')"
    )
    stuck_records = cursor.fetchall()
    logger.info(f"待处理滞留记录: {len(stuck_records)} 条")

    # Step 4: 逐条重跑解析
    resolved_count = 0
    still_failed = 0
    match_sources = {"exact": 0, "alias": 0, "fuzzy": 0, "failed": 0}

    for row_id, sector, event_id in stuck_records:
        code, source = resolve_board_code(sector, name_map)
        category = source.split(":")[0] if source.startswith("fuzzy") else source
        match_sources[category] = match_sources.get(category, 0) + 1

        if code:
            resolved_count += 1
            if source != "exact":
                logger.info(f"  ✅ {event_id}: '{sector}' → {source}")
        else:
            conn.execute(
                "UPDATE catalyst_realizations SET resolution = 'unresolvable' "
                "WHERE id = ?",
                (row_id,),
            )
            still_failed += 1

    conn.commit()
    conn.close()

    # Step 5: 汇总报告
    logger.info("=" * 60)
    logger.info(f"总滞留记录: {len(stuck_records)}")
    logger.info(f"解析成功:   {resolved_count}")
    logger.info(f"仍不可解析: {still_failed} → 已标记 unresolvable")
    logger.info(f"匹配来源分布: {match_sources}")
    logger.info("=" * 60)

    if still_failed > 0:
        logger.warning(
            f"仍有 {still_failed} 条不可解析，建议扩充 board_aliases.py 别名表"
        )


if __name__ == "__main__":
    main()
