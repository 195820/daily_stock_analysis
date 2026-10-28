# -*- coding: utf-8 -*-
"""持仓主板块人工覆盖（2026-10-28）。

问题：`PortfolioDecisionNode._find_stock_sector` 只用「个股所属板块 ∩ 当日
confirmed_sectors」匹配，命中多个时取强度最高者。当个股的主营板块当天没被
confirmed（例如算力类板块未进主线/埋伏），而它又恰好是某个弱 confirmed 概念
（如光伏）的成员时，就会被次要概念顶替，板块分与后续动作随之失真。

实测：002929 润建股份在 2026-10-28 只匹配到「光伏」（composite 35），
而它的算力成员板块（东数西算 46.4 / 人工智能 68.2 / 多模态AI 77.2）都在
Node 2 的 strength_lookup 里但未进 confirmed_sectors。

治理口径：**只在人工确认主营归属时登记**，不做通用回退（避免全持仓漂移）。
强度优先取 `strength_lookup`（Node 2 当日全市场截面口径），缺失给中性 50。
覆盖只作用于持仓评分（`allow_override=True`），不影响候选股。

环境变量 `GOT_HOLDING_SECTOR_OVERRIDES` 可追加/覆盖，格式 `代码=板块`，
逗号分隔，例如 `GOT_HOLDING_SECTOR_OVERRIDES="002929=东数西算,600519=白酒"`。
"""

from __future__ import annotations

import os

# 人工确认的持仓主板块（代码 → Node 2 追踪板块名）
HOLDING_SECTOR_OVERRIDES: dict[str, str] = {
    "002929": "东数西算",  # 润建股份：通信网络/算力基础设施为主，光伏仅为新能源业务之一
}


def _parse_env(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        code, sector = item.split("=", 1)
        code, sector = code.strip(), sector.strip()
        if code and sector:
            out[code] = sector
    return out


def all_overrides() -> dict[str, str]:
    """静态表 + 环境变量（环境变量优先）。"""
    merged = dict(HOLDING_SECTOR_OVERRIDES)
    merged.update(_parse_env(os.getenv("GOT_HOLDING_SECTOR_OVERRIDES", "")))
    return merged


def get_holding_sector_override(symbol: str) -> str:
    """返回人工覆盖的板块名；无覆盖返回空串。"""
    code = str(symbol or "").replace("SH", "").replace("SZ", "").replace("BJ", "").strip()
    if not code:
        return ""
    return all_overrides().get(code, "")
