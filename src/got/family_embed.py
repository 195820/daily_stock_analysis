# -*- coding: utf-8 -*-
"""WI-P1-7(b)：「其他」板块的 embedding 最近家族兜底（生产侧独立开关）。

依据 docs/got/plans/architecture_optimization_implementation_20260929.md §6 WI-P1-7：
- b) 关键词表未命中（落 "其他"）的板块，用 embedding 找最近家族作兜底；
  **复用 glue_pipeline 的 L1 检索接地能力（embed_texts / retrieve_topk /
  _cache_key / _load_embed_cache / L1_EMBED_CACHE_ROOT），但接进生产路径**；
- 计划 §315 红线：不把 glue_pipeline 当生产家族粘合（它仍是 replay-only），
  本模块只借它的 L1 向量检索件，家族判定走本模块 + sector_analysis 独立开关。

三态开关 `GOT_FAMILY_EMBED_FALLBACK`（缺省 **off**）：
- off     : 直接返回 skipped_reason="mode_off"，不触网、不算向量、不改判 —— 旧路径逐字节不变
- shadow  : 计算 + 留痕「若兜底会改判的板块清单」，**不改任何 frame_group**（缺省非 off 态）
- enabled : 真正改写 SectorDetail.frame_group（调用方负责，见 sector_analysis Step 3.4c）

保守阈值（计划 §174-179「误兜底风险 → 保守阈值 + 留痕人工复核」）：
score ≥ min_score 且对次名 margin ≥ min_margin，否则维持 "其他"。
两值为工程缺省启发式（**未经 shadow 数据标定**，缺 off 正是为了让 shadow 先攒读数）。

降级纪律（任何一步失败都不得影响 Node 2 主流程）：
embedding 不可用 / 数量不匹配 / IO 异常 → skipped_reason 留痕 + 返回空 overrides，
调用方（Step 3.4c）包 try/except 双保险。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from src.got.catalyst_config import GLUE_EMBEDDING_API_KEY, GLUE_EMBEDDING_MODEL
from src.got.family_keywords import FAMILY_KEYWORDS
from src.got.glue_pipeline import (
    L1_EMBED_CACHE_ROOT,
    _cache_key,
    _load_embed_cache,
    embed_texts,
    retrieve_topk,
)

logger = logging.getLogger(__name__)

FAMILY_EMBED_MODES = ("off", "shadow", "enabled")
OTHER_FAMILY = "其他"

# 保守阈值（可配，缺省值为工程启发式，待 shadow 读数标定后收紧）
FAMILY_EMBED_MIN_SCORE = float(os.getenv("GOT_FAMILY_EMBED_MIN_SCORE", "0.55"))
FAMILY_EMBED_MIN_MARGIN = float(os.getenv("GOT_FAMILY_EMBED_MIN_MARGIN", "0.05"))
# 兜底候选家族（受控词表 = FAMILY_KEYWORDS 声明序，"其他" 本身不参与）
FALLBACK_FAMILIES: list[str] = [name for name, _ in FAMILY_KEYWORDS if name != OTHER_FAMILY]
# 家族原型文本注入代表关键词的上限（过多关键词会稀释原型语义）
_PROTO_KEYWORDS = 8


# =============================================================================
# 纯函数层（零 IO、零网络，便于单测）
# =============================================================================

def build_family_prototype_text(family: str, keywords: list[str]) -> str:
    """家族原型文本：族名 + 前 N 个代表关键词。

    与 glue_pipeline.build_sector_text 同构（同 embedding 空间才有可比 cosine），
    但方向相反：那边是「板块→事件」检索，这边是「板块名→家族原型」检索。
    """
    kws = [str(k) for k in keywords if k][: _PROTO_KEYWORDS]
    return f"主线家族: {family}；代表关键词: {'、'.join(kws)}"


def build_board_query_text(board_name: str) -> str:
    """板块侧查询文本：只有板块名。

    兜底对象正是「不命中任何关键词」的板块 —— 给它附加词表信息没有意义，
    板块名本身是语义主载体（同 glue_pipeline.build_sector_text 的注释口径）。
    """
    return f"板块名称: {board_name}"


def select_family(
    scores: list[tuple[str, float]],
    min_score: float = FAMILY_EMBED_MIN_SCORE,
    min_margin: float = FAMILY_EMBED_MIN_MARGIN,
) -> dict[str, Any]:
    """cosine 排名 → 是否兜底（纯函数，保守阈值的唯一裁决点）。

    拒绝口径逐条留痕，供 shadow 清单人工复核：
    - no_candidate   : 检索无结果
    - low_score      : 首选分数不足
    - ambiguous      : 首选与次名差距不够（平票不兜底）
    - ok             : 兜底成立
    """
    if not scores:
        return {"fallback": False, "reason": "no_candidate", "family": None,
                "score": None, "margin": None, "candidates": []}
    best_family, best_score = scores[0]
    runner = scores[1] if len(scores) > 1 else None
    margin = round(best_score - runner[1], 4) if runner else None
    base = {
        "family": best_family,
        "score": best_score,
        "margin": margin,
        "candidates": [{"family": f, "score": s} for f, s in scores[:3]],
    }
    if best_score < min_score:
        return {**base, "fallback": False,
                "reason": "low_score", "threshold": min_score}
    if margin is not None and margin < min_margin:
        return {**base, "fallback": False,
                "reason": "ambiguous", "min_margin": min_margin}
    return {**base, "fallback": True, "reason": "ok"}


def resolve_mode(raw: Optional[str] = None) -> str:
    """开关归一：非法值一律按 off（缺省不改生产）。"""
    value = (os.getenv("GOT_FAMILY_EMBED_FALLBACK", "off") if raw is None else raw)
    mode = (value or "").strip().lower()
    return mode if mode in FAMILY_EMBED_MODES else "off"


def collect_unmatched(
    boards: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """按现网词表复核：命中关键词的板块不参与兜底（兜底只治 "其他"）。"""
    unmatched: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for board in boards:
        name = (board.get("name") or "").strip()
        if not name:
            continue
        keyword_family = keyword_family_of(name)
        if keyword_family != OTHER_FAMILY:
            skipped.append({"name": name, "keyword_family": keyword_family})
        else:
            unmatched.append({"name": name, "code": (board.get("code") or "").strip()})
    return unmatched, skipped


def keyword_family_of(board_name: str) -> str:
    """与 sector_analysis._build_frame_group 同规则（声明序首命中）的词表判定。

    刻意不 import sector_analysis（避免循环依赖），词表同源仍是 family_keywords，
    规则只有 4 行；sector_analysis 侧的接线以本函数结果为准（单一裁决点）。
    """
    for family_name, keywords in FAMILY_KEYWORDS:
        if family_name == OTHER_FAMILY:
            continue
        if any(kw in board_name for kw in keywords):
            return family_name
    return OTHER_FAMILY


# =============================================================================
# IO 层（缓存 / embedding）
# =============================================================================

def cache_candidates(run_date: dt.date, root: Path, lookback_days: int = 7) -> list[Path]:
    """家族原型向量按日缓存的复用面：原型文本不随日变化，跨日 sha1 键必然命中。

    从 run_date 往前找最多 lookback_days 个已存在的 l1_embed_*.json（新→旧），
    entries 合并时保留**最早**的向量（同 model 同 text 同向量，避免漂移）。
    """
    files: list[Path] = []
    for delta in range(lookback_days):
        day = run_date - dt.timedelta(days=delta)
        path = Path(root) / f"l1_embed_{day.strftime('%Y%m%d')}.json"
        if path.exists():
            files.append(path)
    return files


def embed_texts_sync(texts: list[str]) -> list[list[float]]:
    """同步包装：仅在 asyncio.get_running_loop() 为空时使用（离线脚本/单测）。

    生产调用点在 Node 2 的 async run() 内，直接 await embed_texts，不走此路径。
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(embed_texts(texts))
    raise RuntimeError("family_embed: 事件循环内请直接 await embed_texts（勿用同步包装）")


# =============================================================================
# 生产接线入口（Node 2 Step 3.4c 唯一调用点）
# =============================================================================

async def resolve_family_overrides(
    boards: list[dict[str, str]],
    run_date: dt.date,
    *,
    mode: Optional[str] = None,
    cache_root: Path = L1_EMBED_CACHE_ROOT,
    min_score: float = FAMILY_EMBED_MIN_SCORE,
    min_margin: float = FAMILY_EMBED_MIN_MARGIN,
) -> dict[str, Any]:
    """「其他」板块的家族兜底求解。返回 dict 便于直接落 SectorContext 留痕。

    overrides: {板块名: {family, score, margin, candidates}} —— 仅 fallback=True 才入表。
    任何失败都只写 skipped_reason，不抛异常到调用方之外。
    """
    mode = resolve_mode(mode)
    out: dict[str, Any] = {
        "mode": mode,
        "model": GLUE_EMBEDDING_MODEL,
        "threshold": {"min_score": min_score, "min_margin": min_margin},
        "unmatched_boards": [],
        "overrides": {},
        "skipped_count": 0,
    }
    if mode == "off":
        # 缺省态：一行 IO/网络都不碰，Node 2 输出逐字节不变
        out["skipped_reason"] = "mode_off"
        return out

    unmatched, skipped = collect_unmatched(boards or [])
    out["skipped_count"] = len(skipped)
    if not unmatched:
        out["skipped_reason"] = "no_unmatched_board"
        return out

    # 原型文本与候选家族名必须同长同序（retrieve_topk 按下标取家族名，不一致会越界）
    keyword_map = dict(FAMILY_KEYWORDS)
    proto_texts = [build_family_prototype_text(f, keyword_map.get(f, []))
                   for f in FALLBACK_FAMILIES]
    if len(proto_texts) != len(FALLBACK_FAMILIES):
        out["skipped_reason"] = "prototype_mismatch"
        return out
    query_texts = [build_board_query_text(u["name"]) for u in unmatched]
    try:
        proto_vecs, cache_stats = await load_family_vectors(
            proto_texts + query_texts, run_date, cache_root=cache_root,
        )
        board_vecs = proto_vecs[len(proto_texts):]
        proto_vecs = proto_vecs[: len(proto_texts)]
    except Exception as e:  # noqa: BLE001 — 兜底能力缺失绝不能影响主流程
        logger.warning("[family_embed] embedding 不可用，跳过兜底: %s", e)
        out.update(cache_stats_missing())
        out["skipped_reason"] = "embedding_unavailable"
        return out

    out.update(cache_stats)
    for board, vec in zip(unmatched, board_vecs):
        # 复用 glue_pipeline.retrieve_topk（同一 cosine 口径，不另写第二套相似度）；
        # k = 候选家族全量（select_family 只取前 3 留痕，k 不参与裁决）
        ranked = retrieve_topk(vec, proto_vecs, list(FALLBACK_FAMILIES), k=len(FALLBACK_FAMILIES))
        record = select_family(ranked, min_score=min_score, min_margin=min_margin)
        record["code"] = board.get("code") or ""
        out["unmatched_boards"].append({"board": board["name"], **record})
        if record["fallback"]:
            out["overrides"][board["name"]] = record

    out["would_flip_count"] = len(out["overrides"])
    logger.info(
        "[family_embed] mode=%s 其他板块 %d 个 → 可兜底 %d 个（阈值 score≥%.2f margin≥%.2f）",
        mode, len(unmatched), out["would_flip_count"], min_score, min_margin,
    )
    return out


async def load_family_vectors(
    texts: list[str], run_date: dt.date, *, cache_root: Path,
) -> tuple[list[list[float]], dict[str, int]]:
    """向量装载：sha1(model+text) 命中按日缓存则零网络，未命中才 await 生产 embed_texts。

    唯一一套装载实现（sync 调用方用 embed_texts_sync 自行包事件循环）。
    """
    entries: dict[str, list[float]] = {}
    for path in cache_candidates(run_date, cache_root):
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[family_embed] 缓存读取失败，忽略该文件: %s (%s)", path, e)
            continue
        if blob.get("model") != GLUE_EMBEDDING_MODEL:
            continue
        for key, vec in (blob.get("entries") or {}).items():
            entries.setdefault(key, vec)

    keys = [_cache_key(GLUE_EMBEDDING_MODEL, t) for t in texts]
    missing = [i for i, key in enumerate(keys) if key not in entries]
    stats = {"cache_hits": len(keys) - len(missing), "cache_misses": len(missing)}
    if missing:
        new_vecs = await embed_texts([texts[i] for i in missing])
        if len(new_vecs) != len(missing):
            raise RuntimeError(
                f"family_embed: embedding 数量不匹配（{len(new_vecs)} != {len(missing)}）"
            )
        for i, vec in zip(missing, new_vecs):
            entries[keys[i]] = vec
        target = Path(cache_root) / f"l1_embed_{run_date.strftime('%Y%m%d')}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"model": GLUE_EMBEDDING_MODEL, "entries": entries}, ensure_ascii=False),
            encoding="utf-8",
        )
    return [entries[k] for k in keys], stats


def cache_stats_missing() -> dict[str, int]:
    return {"cache_hits": 0, "cache_misses": 0}
