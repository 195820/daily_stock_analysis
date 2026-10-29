# -*- coding: utf-8 -*-
"""Node 4 分层粗筛回放（2026-10-28 真数据人工审核用）。

用途
----
按 `--arch flat|cluster` 回放某交易日的 Node 4 粗筛，产出两份 JSON 供人工审核：
候选池规模 / 簇数 / 分片与每片候选 / MMR 前后主题数 / 最终粗筛 12 条与真实出处 /
归档 `major_events` 的召回覆盖 / LLM 与 embedding 调用次数 / 失败分片与降级原因。

边界（不做的事）
----------------
- 不重新抓新闻、不写 DB、不写 `data/got_daily/`、不跑 Node 6/7；
- **直接调用生产** `NewsAnalysisNode._analyze_with_llm()`，只拦截外部依赖与持久化：
  * `LLMToolAdapter.call_text` 网络层：记录 prompt/响应（仍走真实调用）；
  * `_load_unabsorbed_titles` / `_load_historical_events_for_prompt` /
    `_format_historical_context_for_prompt`：按 `--date` 计算窗口（不全局 patch
    `date.today()`，否则会改动 DB/采集/归档）；
  * `_persist_raw_news` / `_save_events_to_db` / `_write_shortlist_quota_shadow`：no-op；
  * `read_news` / `_shortlist_titles` / `_parse_events`：pass-through 记录，算法不变。
- `_parse_events()` 保持生产实现，事件仍按 `source_index` 回连真实 URL。

用法
----
    python scripts/replay_node4_reading.py --date 20261028 --arch flat
    python scripts/replay_node4_reading.py --date 20261028 --arch cluster

两次运行使用相同 `GOT_NEWS_CARRY_DAYS`/`GOT_NEWS_CARRY_MAX` 才可比；cluster 运行若
已存在同日期 flat 输出，会附带「旧独有/新独有/共有」标题对照。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("replay_node4")

_PROMPT_KINDS = (
    ("flat_shortlist", "你是 A 股消息面筛选员"),
    ("shard", "你是 A 股消息面泛读员"),
    ("final_review", "你是 A 股消息面终审员"),
    ("event_extraction", "你是严谨的新闻编辑"),
    ("concept_discovery", "你是敏锐的市场分析师"),
    ("risk_summary", "你是专业的市场研究员"),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Node 4 分层粗筛回放（只读）")
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"),
                        help="回放交易日 YYYYMMDD（缺省今天）")
    parser.add_argument("--arch", required=True, choices=("flat", "cluster"),
                        help="flat=单次标题粗筛（现状）；cluster=分层粗筛")
    parser.add_argument("--out", default="",
                        help="输出 JSON 路径（缺省 reports/node4_reading_<date>_<arch>.json）")
    parser.add_argument("--match-threshold", type=float, default=0.30,
                        help="归档事件→原始标题的映射相似度阈值（缺省 0.30，仅供人工审核参考）")
    parser.add_argument("--embedding-env", default="",
                        help="可选 dotenv 文件：导入项目模块前只加载 GLUE_EMBEDDING_* "
                             "（生产 .env 未配 key 时用于回放，审计后删除该文件）")
    return parser.parse_args()


def _load_embedding_env(path: str) -> None:
    if not path:
        return
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("GLUE_EMBEDDING_"):
            os.environ[key] = value.strip().strip('"').strip("'")


def _parse_run_date(raw: str) -> date:
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError as exc:
        raise SystemExit(f"--date 必须是 YYYYMMDD: {raw}") from exc


def _prompt_kind(prompt: str) -> str:
    for kind, prefix in _PROMPT_KINDS:
        if prompt.startswith(prefix):
            return kind
    return "other"


def _build_live_pool(run_date: date, filter_carried_pool):
    """当日实时池：node4_raw_news 中 fetched_date == run_date 的原始标题。

    回放不重抓新闻，直接用当日落库行：snippet 显式置空（DB 没有摘要，不伪装成有）。
    生产 live 池是 7 源合并后的 weight 排序结果，这里按 id 升序近似采集顺序，并在
    `pool.note` 标注该差异。娱乐热榜与生产粗筛入口同口径剔除。
    """
    from src.storage import DatabaseManager, Node4RawNewsRecord

    db = DatabaseManager()
    with db.get_session() as session:
        rows = (
            session.query(Node4RawNewsRecord)
            .filter(
                Node4RawNewsRecord.fetched_date == run_date,
                Node4RawNewsRecord.lifecycle_state == "active",
            )
            .order_by(Node4RawNewsRecord.id.asc())
            .all()
        )
        items = [
            {
                "title": row.title_original or "",
                "snippet": "",
                "source": row.source or "",
                "source_type": row.source or "",
                "url": row.url or "",
                "published_date": row.published_at or "",
            }
            for row in rows
        ]

    seen: set = set()
    deduped = []
    from src.got.news_retrieval import normalize_title

    for item in items:
        title = (item.get("title") or "").strip()
        norm = normalize_title(title)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        deduped.append(item)
    filtered = filter_carried_pool(deduped)
    return filtered, len(deduped) - len(filtered)


def _build_carried(run_date: date, carry_days: int, carry_max: int, filter_carried_pool):
    """近 N 日补读池：复用生产规则（吸收排除/归一化去重/自然日龄/最新优先截断）。

    不能直接调用生产 `_load_unabsorbed_titles()`：它用 `date.today()` 计算窗口。
    """
    from src.got.news_retrieval import normalize_title
    from src.storage import DatabaseManager, Node4EventRecord, Node4RawNewsRecord

    cutoff = run_date - timedelta(days=carry_days)
    db = DatabaseManager()
    with db.get_session() as session:
        rows = (
            session.query(Node4RawNewsRecord)
            .filter(
                Node4RawNewsRecord.fetched_date >= cutoff,
                Node4RawNewsRecord.fetched_date <= run_date,
                Node4RawNewsRecord.lifecycle_state == "active",
            )
            .order_by(
                Node4RawNewsRecord.fetched_date.desc(),
                Node4RawNewsRecord.id.desc(),
            )
            .all()
        )
        absorbed_rows = (
            session.query(Node4EventRecord.title)
            .filter(
                Node4EventRecord.last_seen >= cutoff - timedelta(days=1),
                Node4EventRecord.last_seen <= run_date,
            )
            .all()
        )
        candidates = [
            (row.title_original or "", row.source or "", row.url or "",
             str(row.published_at or ""), row.fetched_date)
            for row in rows
        ]

    absorbed = {normalize_title(title or "") for (title,) in absorbed_rows}
    carried = []
    seen: set = set()
    for title, source, url, published_at, fetched_date in candidates:
        norm = normalize_title(title)
        if not norm or norm in absorbed or norm in seen:
            continue
        fetched = fetched_date
        if isinstance(fetched, datetime):
            fetched = fetched.date()
        age = (run_date - fetched).days if isinstance(fetched, date) else 1
        seen.add(norm)
        carried.append({
            "title": title,
            "snippet": "",
            "source": source,
            "source_type": source,
            "url": url,
            "published_date": published_at,
            "carried_age_days": max(0, age),
        })
    carried = filter_carried_pool(carried)
    if len(carried) > carry_max:
        carried = carried[:carry_max]
    return carried


def _load_history(run_date: date, days: int = 7):
    """生产同形历史事件上下文（窗口以 run_date 为基准，不含未来行）。"""
    from src.storage import DatabaseManager, Node4EventRecord

    cutoff = run_date - timedelta(days=days)
    db = DatabaseManager()
    with db.get_session() as session:
        rows = (
            session.query(Node4EventRecord)
            .filter(
                Node4EventRecord.last_seen >= cutoff,
                Node4EventRecord.last_seen <= run_date,
            )
            .order_by(Node4EventRecord.mention_count.desc())
            .all()
        )
        return [
            {
                "event_id": row.event_id,
                "title": row.title,
                "lifecycle_state": row.lifecycle_state,
                "mention_count": row.mention_count,
                "days_span": (row.last_seen - row.first_seen).days + 1,
                "source_count": len(row.get_sources_seen()),
                "sentiment_avg": (
                    sum(row.get_sentiment_trajectory()) / len(row.get_sentiment_trajectory())
                    if row.get_sentiment_trajectory() else 0
                ),
                "category": row.category,
            }
            for row in rows
        ]


def _archived_major_events(run_date: date) -> list:
    path = Path("data/got_daily") / run_date.strftime("%Y%m%d") / "news.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    events = payload.get("major_events") or []
    return [e for e in events if isinstance(e, dict)]


def _best_pool_match(title: str, pool: list, similarity) -> tuple:
    best_title, best_sim = "", 0.0
    for item in pool:
        candidate_title = (item.get("title") or "").strip()
        if not candidate_title:
            continue
        score = similarity(title, candidate_title)
        if score > best_sim:
            best_title, best_sim = candidate_title, score
    return best_title, best_sim


def _coverage(archived: list, pool: list, clusters: list, history: list, threshold: float,
              similarity, normalize) -> dict:
    cluster_by_title = {}
    for cluster in clusters or []:
        for member in cluster.members:
            cluster_by_title.setdefault(
                normalize(member.get("title", "")), cluster,
            )

    history_titles = [
        {"title": str(row.get("title") or ""), "event_id": row.get("event_id")}
        for row in history or []
    ]

    rows = []
    for event in archived:
        title = str(event.get("title") or "")
        raw_title, raw_score = _best_pool_match(title, pool, similarity)
        history_title, history_score = _best_pool_match(title, history_titles, similarity)
        if history_score > raw_score:
            matched_title, score, matched_via = history_title, history_score, "history_event"
        else:
            matched_title, score, matched_via = raw_title, raw_score, "raw_title"
        cluster = cluster_by_title.get(normalize(matched_title)) if matched_title else None
        rows.append({
            "archived_title": title,
            "impact_level": event.get("impact_level"),
            "raw_match_title": matched_title,
            "matched_via": matched_via,
            "similarity": round(score, 4),
            "mapped": bool(matched_title) and score >= threshold,
            "cluster_id": cluster.cluster_id if cluster else None,
            "cluster_title": cluster.representative.get("title", "") if cluster else None,
        })
    mapped = sum(1 for row in rows if row["mapped"])
    return {
        "total": len(rows),
        "mapped": mapped,
        "threshold": threshold,
        "events": rows,
    }


def _shortlist_view(items: list) -> list:
    view = []
    for item in items:
        view.append({
            "title": item.get("title", ""),
            "source": item.get("source", ""),
            "source_type": item.get("source_type", ""),
            "url": item.get("url", ""),
            "published_date": item.get("published_date", ""),
            "snippet_chars": len((item.get("snippet") or "").strip()),
            "news_cluster_id": item.get("news_cluster_id"),
            "member_count": len(item.get("member_titles") or []),
            "member_titles": list(item.get("member_titles") or [])[:6],
            "sources": item.get("sources") or [],
            "recalled_industry": bool(item.get("recalled_industry")),
        })
    return view


def main() -> int:
    args = _parse_args()
    run_date = _parse_run_date(args.date)
    out_path = Path(args.out) if args.out else (
        Path("reports") / f"node4_reading_{args.date}_{args.arch}.json"
    )
    run_root = out_path.parent

    # 回放隔离：trace/向量缓存重定向到输出目录（不写 data/got_daily/），并锁定架构
    _load_embedding_env(args.embedding_env)
    os.environ["GOT_NEWS_READING_ARCH"] = args.arch
    os.environ.setdefault("GOT_NEWS_TRACE_ROOT", str((run_root / "trace").resolve()))
    os.environ.setdefault("GOT_NEWS_EMBED_CACHE_ROOT", str((run_root / "_cache").resolve()))

    from src.agent.llm_adapter import LLMToolAdapter
    from src.got import news_retrieval as nr
    from src.got.catalyst_config import (
        GOT_NEWS_CARRY_DAYS, GOT_NEWS_CARRY_MAX, GOT_NEWS_FINALIST_LIMIT,
        GOT_NEWS_FULLTEXT_ENABLED, GOT_NEWS_READ_SHARD_SIZE,
        GOT_NEWS_SHARD_CONCURRENCY, GOT_NEWS_TITLE_SHORTLIST_ENABLED,
        GOT_NEWS_TITLE_SHORTLIST_SIZE,
    )
    from src.got.news_feeds import filter_carried_pool
    from src.got.nodes import news_analysis as na
    from src.got.nodes.news_analysis import NewsAnalysisNode

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    node = NewsAnalysisNode(use_mock=False)
    live_pool, entertainment_dropped = _build_live_pool(run_date, filter_carried_pool)
    carried = _build_carried(
        run_date, GOT_NEWS_CARRY_DAYS, GOT_NEWS_CARRY_MAX, filter_carried_pool,
    )
    history = _load_history(run_date)
    archived = _archived_major_events(run_date)
    logger.info(
        "[replay] %s arch=%s live=%d（剔除娱乐 %d）carried=%d history=%d archived=%d",
        args.date, args.arch, len(live_pool), entertainment_dropped,
        len(carried), len(history), len(archived),
    )

    captured: dict = {"shortlist": [], "ordered_items": [], "trace": None, "clusters": None}
    llm_calls: list = []
    started = time.monotonic()

    original_call_text = LLMToolAdapter.call_text

    def _spy_call_text(self, messages, **kwargs):
        prompt = ""
        if messages:
            first = messages[-1]
            prompt = first.get("content", "") if isinstance(first, dict) else str(first)
        stamp = time.monotonic()
        response = original_call_text(self, messages, **kwargs)
        content = getattr(response, "content", "") or ""
        llm_calls.append({
            "kind": _prompt_kind(prompt),
            "prompt_chars": len(prompt),
            "response_chars": len(content),
            "elapsed_seconds": round(time.monotonic() - stamp, 3),
            "prompt_head": prompt[:120],
        })
        return response

    original_shortlist = NewsAnalysisNode._shortlist_titles
    original_parse_events = NewsAnalysisNode._parse_events
    original_read_news = nr.read_news
    original_index = nr.index_news_clusters
    original_format_history = NewsAnalysisNode._format_historical_context_for_prompt

    def _spy_shortlist(self, raw_news, quota_out=None):
        captured["pool_for_shortlist"] = list(raw_news)
        picked = original_shortlist(self, raw_news, quota_out=quota_out)
        captured["shortlist"] = list(picked)
        return picked

    def _spy_parse_events(result, ordered_items=None):
        captured["ordered_items"] = list(ordered_items or [])
        return original_parse_events(result, ordered_items)

    async def _spy_read_news(node_arg, items, **kwargs):
        captured["pool_for_shortlist"] = list(items)

        def _spy_index(items_arg, **index_kwargs):
            clusters = original_index(items_arg, **index_kwargs)
            captured["clusters"] = clusters
            return clusters

        from unittest.mock import patch as _patch
        with _patch.object(nr, "index_news_clusters", side_effect=_spy_index):
            shortlist, trace = await original_read_news(
                node_arg, items, run_date=run_date, **kwargs,
            )
        captured["shortlist"] = list(shortlist)
        captured["trace"] = trace
        return shortlist, trace

    def _spy_format_history(self, historical_events):
        text = original_format_history(self, historical_events)
        captured["historical_context"] = text
        return text

    def _noop(name):
        def _record(*_args, **_kwargs):
            captured.setdefault("blocked_writes", []).append(name)
            return (0, 0) if name == "_persist_raw_news" else None
        return _record

    from unittest.mock import patch

    with patch.object(LLMToolAdapter, "call_text", new=_spy_call_text), \
            patch.object(NewsAnalysisNode, "_load_unabsorbed_titles",
                         new=lambda self, days=None: carried), \
            patch.object(NewsAnalysisNode, "_load_historical_events_for_prompt",
                         new=lambda self, days=7: history), \
            patch.object(NewsAnalysisNode, "_format_historical_context_for_prompt",
                         new=_spy_format_history), \
            patch.object(NewsAnalysisNode, "_write_shortlist_quota_shadow",
                         new=_noop("_write_shortlist_quota_shadow")), \
            patch.object(NewsAnalysisNode, "_persist_raw_news",
                         new=_noop("_persist_raw_news")), \
            patch.object(NewsAnalysisNode, "_save_events_to_db",
                         new=_noop("_save_events_to_db")), \
            patch.object(NewsAnalysisNode, "_shortlist_titles", new=_spy_shortlist), \
            patch.object(NewsAnalysisNode, "_parse_events",
                         new=staticmethod(_spy_parse_events)), \
            patch.object(na, "read_news", new=_spy_read_news):
        import asyncio
        events, concepts, risks = asyncio.run(node._analyze_with_llm(live_pool))
        events = node._apply_source_count(events, live_pool)

    elapsed = round(time.monotonic() - started, 3)
    trace = captured.get("trace") or {}
    shortlist = captured.get("shortlist") or []
    extraction_pool = captured.get("ordered_items") or []
    clusters = captured.get("clusters") or []

    def _event_dict(event):
        return event.model_dump() if hasattr(event, "model_dump") else dict(event)

    kinds: dict = {}
    for call in llm_calls:
        kinds[call["kind"]] = kinds.get(call["kind"], 0) + 1

    payload = {
        "date": args.date,
        "arch": args.arch,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "reading_arch": args.arch,
            "carry_days": GOT_NEWS_CARRY_DAYS,
            "carry_max": GOT_NEWS_CARRY_MAX,
            "title_shortlist_enabled": GOT_NEWS_TITLE_SHORTLIST_ENABLED,
            "title_shortlist_size": GOT_NEWS_TITLE_SHORTLIST_SIZE,
            "read_shard_size": GOT_NEWS_READ_SHARD_SIZE,
            "shard_concurrency": GOT_NEWS_SHARD_CONCURRENCY,
            "finalist_limit": GOT_NEWS_FINALIST_LIMIT,
            "fulltext_enabled": GOT_NEWS_FULLTEXT_ENABLED,
        },
        "pool": {
            "live": len(live_pool),
            "carried": len(carried),
            "shortlist_pool": len(captured.get("pool_for_shortlist") or live_pool),
            "entertainment_dropped": entertainment_dropped,
            "note": "live 池按 node4_raw_news 当日行 id 升序近似（生产为 7 源 weight 排序）；snippet 显式置空",
        },
        "retrieval_trace": trace or None,
        "retrieval_summary": {
            "cluster_count": trace.get("cluster_count"),
            "shard_count": trace.get("shard_count"),
            "candidate_count": trace.get("candidate_count"),
            "merged_count": trace.get("merged_count"),
            "merged_topic_count": trace.get("merged_topic_count"),
            "finalist_count": trace.get("finalist_count"),
            "selected_count": trace.get("selected_count"),
            "index_mode": trace.get("index_mode"),
            "review_status": trace.get("review_status"),
            "llm_failures": trace.get("llm_failures"),
            "degraded_reason": trace.get("degraded_reason"),
            "shards": trace.get("shards") or [],
            "finalists": trace.get("finalists") or [],
        },
        "shortlist": _shortlist_view(shortlist),
        "extraction_pool": _shortlist_view(extraction_pool),
        "events": [_event_dict(event) for event in events],
        "concepts": concepts,
        "risks": risks,
        "llm": {
            "calls_total": len(llm_calls),
            "by_kind": kinds,
            "prompt_chars_total": sum(call["prompt_chars"] for call in llm_calls),
            "calls": llm_calls,
        },
        "historical_context_chars": len(captured.get("historical_context") or ""),
        "coverage": _coverage(
            archived, live_pool + carried, clusters, history,
            args.match_threshold, nr.title_similarity, nr.normalize_title,
        ) if archived else {"total": 0, "mapped": 0, "events": []},
        "blocked_writes": captured.get("blocked_writes") or [],
        "elapsed_seconds": elapsed,
    }

    # 架构对照：cluster 运行若已有同日期 flat 输出，附「旧独有/新独有/共有」
    counterpart = run_root / f"node4_reading_{args.date}_{'flat' if args.arch == 'cluster' else 'cluster'}.json"
    if counterpart.exists():
        try:
            other = json.loads(counterpart.read_text(encoding="utf-8"))
            other_titles = [item.get("title", "") for item in other.get("shortlist") or []]
            own_titles = [item.get("title", "") for item in payload["shortlist"]]
            payload["comparison"] = {
                "counterpart": str(counterpart),
                "counterpart_arch": other.get("arch"),
                "only_counterpart": [t for t in other_titles if t not in own_titles],
                "only_self": [t for t in own_titles if t not in other_titles],
                "both": [t for t in own_titles if t in other_titles],
            }
        except (json.JSONDecodeError, OSError) as exc:
            payload["comparison"] = {"error": f"{type(exc).__name__}: {exc}"}
    else:
        payload["comparison"] = {"counterpart": None, "note": "对照文件尚不存在"}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out_path)
    logger.info(
        "[replay] 完成 %s arch=%s 簇=%s 片=%s 候选=%s 终审池=%s 入选=%d LLM=%d 耗时=%.1fs → %s",
        args.date, args.arch,
        payload["retrieval_summary"]["cluster_count"],
        payload["retrieval_summary"]["shard_count"],
        payload["retrieval_summary"]["candidate_count"],
        payload["retrieval_summary"]["finalist_count"],
        len(payload["shortlist"]), len(llm_calls), elapsed, out_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
