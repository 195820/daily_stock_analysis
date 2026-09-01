# -*- coding: utf-8 -*-
"""板块名双向别名表（同花顺/efinance 命名 ↔ 东财/Node 2 命名）

efinance `get_belong_boards` 返回同花顺口径（如"半导体概念"），
Node 2 / confirmed_sectors 使用东财口径（如"半导体"）。
跨口径匹配前必须先归一化，否则精确匹配必然失败。
"""

from typing import Set

# 同花顺概念板命名 → Node 2 东财板块名
SECTOR_NAME_CANONICAL = {
    "半导体概念": "半导体", "芯片概念": "芯片",
    "券商概念": "券商",
    "PCB概念": "PCB", "CPO概念": "CPO",
    "EDA概念": "EDA", "OLED概念": "OLED",
    "算力概念": "算力", "液冷概念": "液冷",
    "数据中心概念": "数据中心",
}

# 反向：东财板块名 → 同花顺概念板命名
_SECTOR_NAME_REVERSE = {v: k for k, v in SECTOR_NAME_CANONICAL.items()}


def normalize_sector_name(name: str) -> str:
    """将板块名规范化为 Node 2 使用的东财板块名（无映射则原样返回）。"""
    return SECTOR_NAME_CANONICAL.get(name, name)


def sector_name_variants(name: str) -> Set[str]:
    """返回板块名的全部已知别名（含自身），用于双向匹配。"""
    variants = {name}
    if name in SECTOR_NAME_CANONICAL:
        variants.add(SECTOR_NAME_CANONICAL[name])
    if name in _SECTOR_NAME_REVERSE:
        variants.add(_SECTOR_NAME_REVERSE[name])
    return variants
