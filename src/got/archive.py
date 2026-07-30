# -*- coding: utf-8 -*-
"""
GOT 每日历史档存档

将每次 GOT Phase 1 运行的 6 个 Context + LLM reasoning 文本 + 运行元数据
存档到 data/got_daily/YYYYMMDD/ 目录。

设计目标：
- 支撑阈值回测、prompt 调优、信号复盘
- 永久保留（磁盘占用小，单档 ~50KB）
- JSON 人读友好（indent=2, ensure_ascii=False）
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from src.got.schemas import (
    CapitalContext,
    MarketContext,
    NewsContext,
    SectorContext,
    SentimentContext,
    UnifiedContext,
)

logger = logging.getLogger(__name__)

# 节点键 → 存档文件名
CONTEXT_FILES = {
    "market": "market.json",
    "sector": "sector.json",
    "capital": "capital.json",
    "news": "news.json",
    "sentiment": "sentiment.json",
    "unified": "unified.json",
}

# reasoning.md 的节点标题
REASONING_NODE_TITLES = {
    "market": "Node 1 大盘分析",
    "sector": "Node 2 板块分析",
    "capital": "Node 3 资金面",
    "news": "Node 4 消息面",
    "sentiment": "Node 5 情绪面",
    "unified": "Node 6 聚合研判",
}

# 节点遍历顺序
NODE_ORDER = ("market", "sector", "capital", "news", "sentiment", "unified")


def archive_daily_run(
    *,
    market_ctx: MarketContext,
    sector_ctx: SectorContext,
    capital_ctx: CapitalContext,
    news_ctx: NewsContext,
    sentiment_ctx: SentimentContext,
    unified_ctx: UnifiedContext,
    run_date: Optional[date] = None,
    base_dir: Optional[Path] = None,
    use_mock: bool = False,
    start_time: Optional[datetime] = None,
) -> Path:
    """将一次 GOT 运行结果存档为当日历史档。

    Args:
        market_ctx: Node 1 输出
        sector_ctx: Node 2 输出
        capital_ctx: Node 3 输出
        news_ctx: Node 4 输出
        sentiment_ctx: Node 5 输出
        unified_ctx: Node 6 输出
        run_date: 运行日期，默认今天
        base_dir: 存档根目录，默认 data/got_daily
        use_mock: 是否 mock 运行（记录到 meta.json）
        start_time: 运行开始时间（用于计算耗时）

    Returns:
        存档目录路径
    """
    run_date = run_date or date.today()
    base_dir = base_dir or _default_base_dir()
    archive_dir = base_dir / run_date.strftime("%Y%m%d")
    archive_dir.mkdir(parents=True, exist_ok=True)

    contexts = {
        "market": market_ctx,
        "sector": sector_ctx,
        "capital": capital_ctx,
        "news": news_ctx,
        "sentiment": sentiment_ctx,
        "unified": unified_ctx,
    }

    # 1. 写入 6 个 Context JSON（全量 model_dump）
    for key, ctx in contexts.items():
        _write_json(
            archive_dir / CONTEXT_FILES[key],
            ctx.model_dump(mode="json"),
        )

    # 2. 写入 reasoning.md（汇总 LLM 推理文本）
    _write_reasoning_md(archive_dir / "reasoning.md", contexts, run_date)

    # 3. 写入 meta.json（运行元数据）
    meta = _build_meta(
        run_date=run_date,
        use_mock=use_mock,
        start_time=start_time,
        contexts=contexts,
    )
    _write_json(archive_dir / "meta.json", meta)

    logger.info("[GOT Archive] 历史档已保存: %s", archive_dir)
    return archive_dir


def _default_base_dir() -> Path:
    """默认存档根目录：项目根目录下的 data/got_daily。"""
    # 从本文件位置推算：<project>/src/got/archive.py → <project>/data/got_daily
    project_root = Path(__file__).resolve().parent.parent.parent
    return project_root / "data" / "got_daily"


def _write_json(path: Path, data: dict) -> None:
    """写 JSON 文件，人读友好格式。"""
    content = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    path.write_text(content, encoding="utf-8")


def _write_reasoning_md(
    path: Path,
    contexts: dict,
    run_date: date,
) -> None:
    """写 reasoning.md，汇总所有节点的 LLM 推理文本。"""
    lines = [f"# GOT 运行推理汇总 - {run_date}", ""]
    for key in NODE_ORDER:
        ctx = contexts[key]
        title = REASONING_NODE_TITLES[key]
        lines.append(f"## {title}")
        lines.append("")
        reasoning = getattr(ctx, "reasoning", None)
        if reasoning:
            lines.append(reasoning.strip())
        else:
            lines.append("*（本次运行未生成 reasoning 文本）*")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _build_meta(
    *,
    run_date: date,
    use_mock: bool,
    start_time: Optional[datetime],
    contexts: dict,
) -> dict:
    """构建 meta.json 内容。"""
    meta: dict = {
        "run_date": run_date.isoformat(),
        "run_timestamp": datetime.now().isoformat(timespec="seconds"),
        "use_mock": use_mock,
        "git_commit": _get_git_commit(),
    }

    if start_time:
        meta["duration_seconds"] = round(
            (datetime.now() - start_time).total_seconds(), 1
        )

    # 数据质量摘要
    data_quality_summary = {}
    for key, ctx in contexts.items():
        dq = getattr(ctx, "data_quality", None)
        if dq is None:
            continue
        if getattr(dq, "fetch_failed_fields", None):
            data_quality_summary[key] = "degraded"
        elif getattr(dq, "missing_fields", None):
            data_quality_summary[key] = "partial"
        else:
            data_quality_summary[key] = "normal"
    if data_quality_summary:
        meta["data_quality_summary"] = data_quality_summary

    # CapitalContext 特有：缺失维度
    capital_ctx = contexts.get("capital")
    missing_dims = getattr(capital_ctx, "missing_dimensions", None)
    if missing_dims:
        meta["missing_dimensions"] = {"capital": list(missing_dims)}

    # LLM 模型（从常见环境变量推断）
    llm_model = (
        os.getenv("LITELLM_MODEL")
        or os.getenv("DEEPSEEK_MODEL")
        or os.getenv("OPENAI_MODEL")
    )
    if llm_model:
        meta["llm_model"] = llm_model

    return meta


def _get_git_commit() -> str:
    """获取当前 git commit short hash（失败时返回 'unknown'）。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"
