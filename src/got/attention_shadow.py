# -*- coding: utf-8 -*-
"""Node 4 attention 影子聚合（P0-1，纯观测，零决策面）。

聚合当日 `node4_raw_news` 中带 `platform` 的热榜行，按标题归一化汇总
「几个平台同时在榜 / 最好名次 / 当日快照次数 / 最大热度」，落
`data/got_daily/YYYYMMDD/shadow_attention.json`。

**边界（契约）**：attention 与 `source_count` 严格分离——平台扩散（微博/百度/知乎
同题）≠ 独立媒体确认；本模块不写 `source_count`、不进 prompt、不进
`_detect_fermenting_events`，只做观测聚合。转正需单独裁决。
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 影子产物条目上限（按平台扩散降序 + 名次升序取前 N）
ATTENTION_TOP_N = 50


def _split_platforms(row: Any) -> List[str]:
    """从行内取平台集合：`platforms`（合并后的逗号串）优先，回退单值 `platform`。"""
    merged = getattr(row, "platforms", None)
    if merged:
        return [p for p in str(merged).split(",") if p]
    single = getattr(row, "platform", None)
    return [single] if single else []


def _load_hot_rows(today: date) -> List[Any]:
    """读取当日带平台标记的原始新闻行（无热榜行 → 空列表，异常同样降级）。"""
    try:
        from src.storage import DatabaseManager, Node4RawNewsRecord

        db = DatabaseManager()
        with db.get_session() as session:
            return session.query(Node4RawNewsRecord).filter(
                Node4RawNewsRecord.fetched_date == today,
                Node4RawNewsRecord.platform.isnot(None),
            ).all()
    except Exception as exc:  # noqa: BLE001 — 影子聚合失败不阻断 Node 4
        logger.warning("[Node 4] attention 影子读取失败（已忽略）: %s", exc)
        return []


def _aggregate(rows: List[Any]) -> List[Dict[str, Any]]:
    """按 title_normalized 聚合平台扩散/名次/快照次数/热度。"""
    buckets: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = getattr(row, "title_normalized", "") or ""
        if not key:
            continue
        entry = buckets.setdefault(key, {
            "title": getattr(row, "title_original", "") or "",
            "platforms": set(),
            "best_rank": None,
            "snapshot_count": 0,
            "heat": None,
        })
        entry["platforms"].update(_split_platforms(row))

        rank = getattr(row, "rank", None)
        if isinstance(rank, int) and rank > 0:
            entry["best_rank"] = rank if entry["best_rank"] is None else min(entry["best_rank"], rank)

        snap = getattr(row, "snapshot_count", None) or 0
        entry["snapshot_count"] = max(entry["snapshot_count"], snap)

        heat = getattr(row, "heat", None)
        if isinstance(heat, (int, float)) and heat:
            entry["heat"] = heat if entry["heat"] is None else max(entry["heat"], heat)

    items: List[Dict[str, Any]] = []
    for entry in buckets.values():
        platforms = sorted(entry["platforms"])
        items.append({
            "title": entry["title"],
            "platforms": platforms,
            "platform_count": len(platforms),
            "best_rank": entry["best_rank"],
            "snapshot_count": entry["snapshot_count"],
            "heat": entry["heat"],
        })
    return items


def build_attention_shadow(day_dir: Path, today: Optional[date] = None) -> Dict[str, Any]:
    """聚合当日热榜 attention 信号并落 `shadow_attention.json`（tmp+replace 原子写）。

    Returns:
        {"date": "YYYYMMDD", "source_rows": N, "items": [...], "multi_platform_count": X}
        无热榜行时 items=[] 且不抛错（快照未采热榜或全源失败均正常）。
    """
    today = today or date.today()
    rows = _load_hot_rows(today)
    items = _aggregate(rows)
    items.sort(
        key=lambda it: (
            -it["platform_count"],
            it["best_rank"] if isinstance(it["best_rank"], int) else 10 ** 6,
        ),
    )
    record = {
        "date": today.strftime("%Y%m%d"),
        "source_rows": len(rows),
        "items": items[:ATTENTION_TOP_N],
        "multi_platform_count": sum(1 for it in items if it["platform_count"] >= 2),
        "note": (
            "attention 仅观测：平台扩散≠独立媒体确认，source_count/事件提取/prompt 均未接入；"
            "THS 热榜被小时快照跳过（反爬），其盘中轨迹仅每日 GOT 一次"
        ),
    }

    try:
        day_dir.mkdir(parents=True, exist_ok=True)
        out = day_dir / "shadow_attention.json"
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(out)
        logger.info(
            "[Node 4] attention 影子落盘: rows=%d items=%d multi_platform=%d",
            record["source_rows"], len(record["items"]), record["multi_platform_count"],
        )
    except OSError as exc:
        logger.warning("[Node 4] shadow_attention.json 落盘失败（已忽略）: %s", exc)
    return record
