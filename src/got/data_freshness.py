# -*- coding: utf-8 -*-
"""批次 C2（竞品经验补强计划 2026-10-06）：全局数据新鲜度检查通用工具。

计划 §C2：`_validate_data_freshness`（原 capital_analysis 模块级函数）本就是通用逻辑，
「提为通用工具」是搬家而非重构。此处收为**唯一权威实现**，capital_analysis 改引用本模块
（禁第二套口径）；并扩识别英文 `date` 列（Node 1/2/5 行情类取数点产出的 STANDARD_COLUMNS
用 `date`，原函数只认中文 `日期/时间`）。Node 1/2/5 各行情消费点在取数后可调本工具打
`is_stale`/stale_fields（缺日期列 → 返回"无法验证"，不阻塞）。
"""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd

# 识别的日期列（按优先级）：中文 akshare 口径 + 英文 fetcher 标准列
_DATE_COLUMNS = ("日期", "date", "trade_date")
_TIME_COLUMNS = ("时间", "datetime", "timestamp")


def check_data_freshness(df: Any, expected_date: str) -> Dict[str, Any]:
    """验证 DataFrame 是否为当日数据（防反爬缓存 / 陈旧行情）。

    检查逻辑：
    1. 有日期列（"日期"/"date"/"trade_date"）→ 取末行日期精确匹配 expected_date
    2. 有时间列（"时间"/"datetime"/"timestamp"）→ 取末行时间戳前 10 位日期比对
    3. 都没有 → {"is_fresh": True, "stale_data": False, "unverifiable": True}（不阻塞）

    Returns:
        {"is_fresh": bool, "stale_data": bool, "unverifiable": bool, "detail": str}
    """
    result = {"is_fresh": True, "stale_data": False, "unverifiable": True,
              "detail": "无法验证（无日期列）"}

    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        result["detail"] = "空数据"
        return result
    if not isinstance(df, pd.DataFrame):
        return result

    for col in _DATE_COLUMNS:
        if col in df.columns and len(df) > 0:
            latest = str(df[col].iloc[-1]).strip()[:10]
            result["is_fresh"] = latest == expected_date
            result["stale_data"] = not result["is_fresh"]
            result["unverifiable"] = False
            result["detail"] = f"日期列[{col}]最新={latest}, 期望={expected_date}"
            return result

    for col in _TIME_COLUMNS:
        if col in df.columns and len(df) > 0:
            latest_time = str(df[col].iloc[-1]).strip()
            result["is_fresh"] = latest_time[:10] == expected_date
            result["stale_data"] = not result["is_fresh"]
            result["unverifiable"] = False
            result["detail"] = f"时间列[{col}]最新={latest_time}, 期望日期={expected_date}"
            return result

    return result
