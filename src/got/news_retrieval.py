# -*- coding: utf-8 -*-
"""Node 4 事件簇索引 + 分层粗筛（2026-10-29）。

职责边界
--------
只服务 Node 4 的候选发现（`NewsAnalysisNode._analyze_with_llm` 的 cluster 路径）与
回放脚本：不改采集频率、不碰其他节点、不改催化剂日历、不引入本地大模型/向量库。
embedding **只用于把同题报道聚成事件簇**，不做价值判断；价值判断仍只交 LLM。

文献接地
--------
- Lost in the Middle（arXiv:2307.03172）：长 prompt 的位置偏差 → 分片泛读 + 全局归并，
  而不是把数千条标题一次性塞进一次调用。
- RAPTOR（arXiv:2401.18059）/ LLM×MapReduce（ACL 2025）：分而治之替代单次泛读；
  这里只取「分片阅读」，不搬递归摘要树、UMAP/GMM 或向量数据库。
- MMR（Carbonell & Goldstein, 1998）：终审候选按「价值 − 冗余」保留不同主题，
  避免同一新闻的跨源转载挤掉独立产业事件。
- LangExtract 出处接地：聚类只压缩同题报道，不生成无出处的新事实；最终事件仍引用
  真实原始标题/snippet/url。

流程
----
1. `_collapse_exact()`：`normalize_title()` 完全同题归并（主行选择：有 URL > 有 snippet >
   时间较新 > 原顺序），附 `member_titles/sources/member_count`。
2. `index_news_clusters()`：主标题 embedding cosine ≥0.78 连通分量；embedding 不可用时
   调用方传 `vectors=None` 走 `title_similarity() ≥0.45` 兜底。边构建用「实体 token ×
   日龄」倒排桶，不对全池做 O(n²) 两两比较。
3. `make_reading_shards()`：日龄（今日早前/昨日/更早）→ 产业实体形态桶 → 连续切片，
   不跨日龄拼接。
4. `score_shard()`：单片一次 LLM 泛读，输出簇候选（importance/freshness/evidence_index）。
5. `merge_shard_candidates()` → `diversify_candidates()`（MMR）→ `review_candidates()`。
6. `read_news()`：唯一 async 编排入口，返回 `(shortlist, trace)`；所有降级都写 trace。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.got.catalyst_config import (
    GLUE_EMBEDDING_MODEL,
    GOT_NEWS_EMBED_CACHE_ROOT,
    GOT_NEWS_FINALIST_LIMIT,
    GOT_NEWS_READ_SHARD_SIZE,
    GOT_NEWS_SHARD_CONCURRENCY,
    GOT_NEWS_TITLE_SHORTLIST_SIZE,
    GOT_NEWS_TRACE_ROOT,
    INDUSTRY_RECALL_KEYWORDS,
)
from src.got.glue_pipeline import _cache_key, _load_embed_cache, embed_texts

logger = logging.getLogger(__name__)

# 聚类阈值：cosine 0.78 ≈ 同题改写/跨源转载（标题级 embedding 经验区间）
COSINE_CLUSTER_THRESHOLD = 0.78
# embedding 不可用时的 Jaccard bigram 兜底阈值（与 news_feeds 跨源确认同口径）
JACCARD_FALLBACK_THRESHOLD = 0.45
# MMR 冗余惩罚系数（终审候选：价值分 − 0.60 × 最大主题相似度）
MMR_LAMBDA = 0.60
# 同一主题最多进入终审的候选数（防单一主题挤占）
TOPIC_CAP = 3
# 单片最多候选（60 簇 → 20 候选，不把召回压回 12 条）
SHARD_CANDIDATE_CAP = 20
SHARD_TEMPERATURE = 0.2
SHARD_MAX_TOKENS = 1600
FINAL_REVIEW_TEMPERATURE = 0.2
FINAL_REVIEW_MAX_TOKENS = 1200
# 空响应/JSON 失败最多重试 1 次（_call_llm 内部另有空响应重试）
SHARD_MAX_ATTEMPTS = 2
# 连续 N 片 LLM 失败 → 停止后续分片调用（防错误放大与无意义重试）
MAX_CONSECUTIVE_SHARD_FAILURES = 3
# 来源质量分（只区分来源，不含热度/rank/source_count）
_SOURCE_QUALITY_PROFESSIONAL = 1.0
_SOURCE_QUALITY_EASTMONEY = 0.9
_SOURCE_QUALITY_DEFAULT = 0.6

_AGE_LABELS = ("今日", "昨日", "更早")
_NORM_PUNCT_RE = re.compile(r"[^\w一-鿿]")
_SPACE_RE = re.compile(r"\s+")


class NewsRetrievalUnavailable(RuntimeError):
    """embedding 缺 key/报错/向量不可用 → 调用方降级为 Jaccard 聚类。"""


@dataclass(frozen=True)
class NewsCluster:
    """事件簇：代表条目 + 全部同题成员。

    `cluster_id` 是代表标题归一化后的 SHA1 前 12 位，不依赖当日排序，回放与审计可对齐。
    `similarity` 只记录簇形成阈值，不进入 prompt 评分。
    """

    cluster_id: str
    representative: dict
    members: List[dict]
    similarity: Optional[float]


# =============================================================================
# Prompt 模板
# =============================================================================

NEWS_SHARD_PROMPT = """你是 A 股消息面泛读员。下面是第 {shard_no}/{shard_total} 批候选事件簇，共 {count} 个。
每个编号 = 一个事件簇：`[簇ID] [日龄] 代表标题`；下一行 `成员:` 列出同题报道的原始标题序号（1 = 代表标题）。

{lines}

任务：挑出对**中国经济与 A 股板块**可能产生实质影响的事件簇，最多 {limit} 个。
判断口径：
- 挑：能改变供需格局/政策方向/技术路线/资金成本的事件——产业政策与监管、重大技术发布（开源/量产/突破）、产业链供需与价格异动、地缘与大宗商品、宏观数据与央行操作、大额资本动作、影响行业的公司级事件。
- 不挑：单纯行情播报（"XX涨超X%""多股涨停"）、论坛口水、无主体的评论转载、软文、与 A 股无关的境外社会新闻、标题无法判断主体的条目。
- 单源产业发布（成员只有 1 条）有实质影响同样要挑，不得因"未多平台扩散"排除。
- 同题转载已归入同一簇，不要因为簇内成员多就提高重要性。
字段：
- cluster_id：上面出现过的簇ID
- topic：事件主题（≤12 字，用于全局去重；写主题不写新闻标题）
- importance：3=可能改变全行业供需/政策/技术路线；2=影响单个板块或细分产业链；1=局部/仅个股/信息不足
- freshness：2=今日；1=昨日；0=更早（按 [日龄] 前缀）
- evidence_index：该簇成员列表里支撑判断的原始标题序号（1 起）
- reason：一句话理由（引用标题里的实体与影响链条）
输出 JSON：{{"candidates":[{{"cluster_id":"...","topic":"...","importance":2,"freshness":2,"reason":"...","evidence_index":1}}]}}
没有值得关注的事件时输出 {{"candidates":[]}}。簇ID 必须来自上面出现过的值。只输出 JSON。"""

NEWS_FINAL_REVIEW_PROMPT = """你是 A 股消息面终审员。下面是全局去重后的候选事件（每簇一条，共 {count} 个）。
它们按「价值 × 时效」预排过，但**不要照抄顺序**，也不要按标题炸裂程度判断。

{candidates}

任务：挑出最多 {limit} 个事件进入细读（候选主题足够多时应选满 {limit} 个），输出 JSON：
{{"selected":[{{"cluster_id":"...","merge_with":"...","reason":"..."}}]}}
要求：
1. 先覆盖不同产业/政策/宏观主题，再在主题内选最强；**同一 topic 最多选 1 条**，
   只有候选主题数不足时才允许同 topic 第 2 条；同一主题的不同事件不得占满名额。
2. 允许单源产业发布（成员只有 1 条）进入；不得仅按多平台热榜/热度判断价值。
3. 应排除的只有：没有实质 A 股影响的重复报道、纯行情播报、娱乐或社会新闻。
4. merge_with 仅用于「另一簇其实是同一事件的不同说法」，且必须同属一个产业类目；
   其他情况省略 merge_with。
5. cluster_id 必须来自上面出现过的值；没有重要事件时输出 {{"selected":[]}}。
只输出 JSON。"""


# =============================================================================
# 标题归一化 / 相似度（全仓唯一实现，NewsAnalysisNode 委托到这里）
# =============================================================================

def normalize_title(title: str) -> str:
    """标题归一化：去标点/统一小写/压缩空白，用于精确匹配去重。"""
    t = (title or "").strip().lower()
    t = _NORM_PUNCT_RE.sub("", t)
    t = _SPACE_RE.sub("", t)
    return t[:200]


def title_similarity(a: str, b: str) -> float:
    """标题相似度（Jaccard on character bigrams）。0~1。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    bigrams_a = {a[i:i + 2] for i in range(len(a) - 1)}
    bigrams_b = {b[i:i + 2] for i in range(len(b) - 1)}
    if not bigrams_a or not bigrams_b:
        return 0.0
    return len(bigrams_a & bigrams_b) / len(bigrams_a | bigrams_b)


def news_category(title: str) -> str:
    """粗类目 = 标题命中的首个产业实体词，未命中记「其他」。

    与产业召回共用 `INDUSTRY_RECALL_KEYWORDS["industry_entity"]`（只做形态归类，
    不做价值判断），与 Node 4 短名单类目配额同一口径。
    """
    for kw in INDUSTRY_RECALL_KEYWORDS["industry_entity"]:
        if kw in (title or ""):
            return kw
    return "其他"


# =============================================================================
# Step 1-2: 完全同题归并 + 事件簇索引
# =============================================================================

def _dedupe(values: Sequence[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        v = (value or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def _age_days(item: dict) -> int:
    try:
        return max(0, int(item.get("carried_age_days") or 0))
    except (TypeError, ValueError):
        return 0


def _age_bucket(age: int) -> int:
    if age <= 0:
        return 0
    return 1 if age == 1 else 2


def _time_key(item: dict) -> tuple:
    """条目时间戳（fetched_date/created_at > published_date），不可解析取 (0, 0.0)。"""
    for key in ("fetched_date", "created_at", "published_date"):
        raw = item.get(key)
        if isinstance(raw, datetime):
            return (1, raw.timestamp())
        if isinstance(raw, date):
            return (1, datetime(raw.year, raw.month, raw.day).timestamp())
        if isinstance(raw, str) and raw.strip():
            txt = raw.strip().replace("T", " ")[:19]
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    return (1, datetime.strptime(txt, fmt).timestamp())
                except ValueError:
                    continue
    return (0, 0.0)


def _sources_of(item: dict) -> List[str]:
    source = (item.get("source") or "").strip()
    return [source] if source else []


def _main_preference(item: dict, index: int) -> tuple:
    """同题主行优先级：有 URL > 有 snippet > 时间较新 > 原顺序靠前。"""
    has_url = 1 if (item.get("url") or "").strip() else 0
    has_snippet = 1 if (item.get("snippet") or "").strip() else 0
    return (has_url, has_snippet, _time_key(item), -index)


def _collapse_exact(items: Sequence[dict]) -> List[dict]:
    """`normalize_title()` 完全同题的条目归并为主行（不改调用方对象）。"""
    groups: Dict[str, dict] = {}
    order: List[str] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        norm = normalize_title(title)
        if not norm:
            continue
        group = groups.get(norm)
        if group is None:
            groups[norm] = {
                "main": dict(item),
                "main_idx": idx,
                "titles": [title],
                "sources": _sources_of(item),
            }
            order.append(norm)
            continue
        group["titles"].append(title)
        group["sources"] = _dedupe(group["sources"] + _sources_of(item))
        if _main_preference(item, idx) > _main_preference(group["main"], group["main_idx"]):
            group["main"] = dict(item)
            group["main_idx"] = idx

    bases: List[dict] = []
    for norm in order:
        group = groups[norm]
        base = dict(group["main"])
        title = (base.get("title") or group["titles"][0]).strip()
        base["title"] = title
        base["snippet"] = base.get("snippet") or ""
        base["carried_age_days"] = _age_days(group["main"])
        member_titles = _dedupe([title] + group["titles"])
        base["member_titles"] = member_titles
        base["sources"] = group["sources"] or _sources_of(base)
        base["member_count"] = len(member_titles)
        bases.append(base)
    return bases


def _bigram_buckets(bases: Sequence[dict]) -> Dict[str, List[int]]:
    """候选桶：normalized title 的 character bigram 倒排（跨日龄共用）。

    只按「实体 token」建桶会漏掉 77% 的实际高相似对（10-28 真数据实测：165 对 cosine
    ≥0.78，旧桶只命中 38 对）——中文标题换词后往往不共享整词，但共享 bigram。
    bigram 桶是 exact blocking（相似度 >0 必共享 bigram），且不会像「标题前 2 字」
    那样把所有「财联社10月…」挤进同一桶。
    """
    index: Dict[str, List[int]] = {}
    for i, base in enumerate(bases):
        title = normalize_title(base.get("title", ""))
        for bigram in {title[j:j + 2] for j in range(len(title) - 1)}:
            index.setdefault(bigram, []).append(i)
    return index


def _union_find_components(count: int, pairs: Sequence[tuple]) -> Dict[int, List[int]]:
    parent = list(range(count))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri
    groups: Dict[int, List[int]] = {}
    for i in range(count):
        groups.setdefault(find(i), []).append(i)
    return groups


def _cosine_pairs(bases: Sequence[dict], vectors: Sequence[Sequence[float]]) -> set:
    """桶内 cosine ≥0.78 的标题对（NumPy 归一化，bigram 倒排取候选桶）。"""
    import numpy as np  # noqa: PLC0415

    mat = np.asarray(vectors, dtype=float)
    if mat.ndim != 2 or mat.shape[0] != len(bases):
        raise NewsRetrievalUnavailable(
            f"标题向量形状不匹配: {mat.shape} vs {len(bases)} 条"
        )
    norms = np.linalg.norm(mat, axis=1)
    norms[norms == 0] = 1e-12
    mat = mat / norms[:, None]

    pairs: set = set()
    for idxs in _bigram_buckets(bases).values():
        if len(idxs) < 2:
            continue
        sub = mat[idxs]
        sims = sub @ sub.T
        rows, cols = np.where(np.triu(sims >= COSINE_CLUSTER_THRESHOLD, k=1))
        for r, c in zip(rows.tolist(), cols.tolist()):
            pairs.add((idxs[r], idxs[c]))
    return pairs


def _jaccard_pairs(bases: Sequence[dict]) -> set:
    """Jaccard 兜底连通分量：bigram 倒排取候选对，避免 O(n²) 全量两两比较。

    候选对=共享 ≥1 个 character bigram，因此不会漏掉任何 `title_similarity > 0`
    的真实重题；不按日龄截断（同一事件跨日转载应并入一簇）。
    """
    pairs: set = set()
    for bucket in _bigram_buckets(bases).values():
        if len(bucket) < 2:
            continue
        for a in range(len(bucket)):
            for b in range(a + 1, len(bucket)):
                i, j = bucket[a], bucket[b]
                if title_similarity(bases[i]["title"], bases[j]["title"]) >= JACCARD_FALLBACK_THRESHOLD:
                    pairs.add((i, j))
    return pairs


def _source_priority(entry: dict) -> int:
    source = (entry.get("source") or "").strip()
    source_type = (entry.get("source_type") or "").strip()
    if source_type == "intelligence" and source.startswith("intelligence_rss-"):
        return 5
    if source_type == "cls":
        return 4
    if source_type == "wallstreetcn":
        return 3
    if source_type == "sina":
        return 2
    return 1


def _pick_representative(members: Sequence[dict]) -> dict:
    """簇代表：RSS > cls > WSC > sina > 其他 → 时间最新 → snippet 最长。"""
    best = members[0]
    best_key = (
        _source_priority(best),
        _time_key(best),
        len((best.get("snippet") or "").strip()),
    )
    for member in members[1:]:
        key = (
            _source_priority(member),
            _time_key(member),
            len((member.get("snippet") or "").strip()),
        )
        if key > best_key:
            best, best_key = member, key
    return best


def _build_clusters(bases: Sequence[dict], pairs: set, threshold: float) -> List[NewsCluster]:
    groups = sorted(_union_find_components(len(bases), pairs).values(), key=min)
    clusters: List[NewsCluster] = []
    for indexes in groups:
        members = [bases[i] for i in indexes]
        representative = _pick_representative(members)
        cluster_id = hashlib.sha1(
            normalize_title(representative.get("title", "")).encode("utf-8")
        ).hexdigest()[:12]
        clusters.append(NewsCluster(
            cluster_id=cluster_id,
            representative=representative,
            members=members,
            similarity=threshold,
        ))
    return clusters


def index_news_clusters(
    items: Sequence[dict], *, vectors: Optional[Sequence[Sequence[float]]] = None,
) -> List[NewsCluster]:
    """同题归并 + 连通分量聚类。

    `vectors` 与「完全同题归并后的主条目标题」同序（由 `read_news` 计算 embedding）；
    缺省/None → `title_similarity() ≥0.45` 的 Jaccard 兜底（embedding 不可用的降级路径）。
    """
    bases = _collapse_exact(items)
    if not bases:
        return []
    if vectors is not None and len(vectors) == len(bases):
        return _build_clusters(bases, _cosine_pairs(bases, vectors), COSINE_CLUSTER_THRESHOLD)
    return _build_clusters(bases, _jaccard_pairs(bases), JACCARD_FALLBACK_THRESHOLD)


# =============================================================================
# Step 3: 分片泛读
# =============================================================================

def make_reading_shards(
    clusters: Sequence[NewsCluster], *, shard_size: int = GOT_NEWS_READ_SHARD_SIZE,
) -> List[List[NewsCluster]]:
    """日龄 → 产业实体形态桶 → 连续切片；不跨日龄拼接（旧闻不淹盖新事件）。"""
    if not clusters:
        return []
    size = max(1, int(shard_size))
    by_age: Dict[int, List[NewsCluster]] = {}
    for cluster in clusters:
        by_age.setdefault(_age_bucket(_age_days(cluster.representative)), []).append(cluster)

    shards: List[List[NewsCluster]] = []
    for age in sorted(by_age):
        buckets: Dict[str, List[NewsCluster]] = {}
        bucket_order: List[str] = []
        for cluster in by_age[age]:
            category = news_category(cluster.representative.get("title", ""))
            if category not in buckets:
                buckets[category] = []
                bucket_order.append(category)
            buckets[category].append(cluster)
        for category in bucket_order:
            items = buckets[category]
            for start in range(0, len(items), size):
                shards.append(items[start:start + size])
    return shards


def _member_display(cluster: NewsCluster) -> List[str]:
    """该簇在 prompt 中可见的成员标题：代表 + 最多 2 条不同标题。"""
    titles = [(cluster.representative.get("title") or "").strip()]
    for member in cluster.members:
        title = (member.get("title") or "").strip()
        if title and title not in titles:
            titles.append(title)
        if len(titles) >= 3:
            break
    return [t for t in titles if t]


def _shard_prompt(shard: Sequence[NewsCluster], shard_no: int, shard_total: int) -> tuple:
    lines: List[str] = []
    display: Dict[str, List[str]] = {}
    for i, cluster in enumerate(shard, 1):
        titles = _member_display(cluster)
        display[cluster.cluster_id] = titles
        age = _age_bucket(_age_days(cluster.representative))
        lines.append(f"{i}. [{cluster.cluster_id}] [{_AGE_LABELS[age]}] {titles[0] if titles else ''}")
        if len(titles) > 1:
            lines.append("   成员: " + " | ".join(
                f"{j}) {t}" for j, t in enumerate(titles[1:], 2)
            ))
    prompt = NEWS_SHARD_PROMPT.format(
        shard_no=shard_no,
        shard_total=shard_total,
        count=len(shard),
        limit=SHARD_CANDIDATE_CAP,
        lines="\n".join(lines),
    )
    return prompt, display


def _int_between(value: Any, low: int, high: int) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < low or number > high:
        return None
    return number


def score_shard(
    node: Any, shard: Sequence[NewsCluster], *, shard_no: int = 1, shard_total: int = 1,
) -> Dict[str, Any]:
    """单片泛读：一次 `_call_llm`，输出该片簇候选（非法项丢弃，失败片整体跳过）。"""
    prompt, display = _shard_prompt(shard, shard_no, shard_total)
    data: Optional[dict] = None
    llm_calls = 0
    for _attempt in range(SHARD_MAX_ATTEMPTS):
        try:
            raw = node._call_llm(
                prompt, temperature=SHARD_TEMPERATURE, max_tokens=SHARD_MAX_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001 — 单片网络/限流失败不拖垮整日
            logger.warning("[Node 4] 分片 %d LLM 异常（跳过该片）: %s", shard_no, exc)
            raw = ""
        llm_calls += 1
        if not raw:
            continue
        parsed = node._extract_json(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("candidates"), list):
            data = parsed
            break

    result: Dict[str, Any] = {
        "shard_no": shard_no,
        "cluster_count": len(shard),
        "input_chars": len(prompt),
        "llm_calls": llm_calls,
        "candidates": [],
        "dropped_invalid": 0,
        "status": "ok",
    }
    if data is None:
        result["status"] = "failed"
        return result

    by_id: Dict[str, dict] = {}
    for entry in data["candidates"]:
        if len(by_id) >= SHARD_CANDIDATE_CAP:
            break
        if not isinstance(entry, dict):
            result["dropped_invalid"] += 1
            continue
        cluster_id = entry.get("cluster_id")
        if not isinstance(cluster_id, str) or cluster_id not in display:
            result["dropped_invalid"] += 1
            continue

        titles = display[cluster_id]
        evidence_index = entry.get("evidence_index")
        evidence_title = titles[0] if titles else ""
        if evidence_index is not None:
            index = _int_between(evidence_index, 1, len(titles))
            if index is None:
                result["dropped_invalid"] += 1
                continue
            evidence_title = titles[index - 1]

        importance = _int_between(entry.get("importance"), 1, 3)
        freshness = _int_between(entry.get("freshness"), 0, 2)
        defaults = importance is None or freshness is None
        candidate = {
            "cluster_id": cluster_id,
            "topic": str(entry.get("topic") or "").strip()[:40],
            "importance": 1 if importance is None else importance,
            "freshness": 0 if freshness is None else freshness,
            "reason": str(entry.get("reason") or "").strip()[:200],
            "evidence_index": evidence_index,
            "evidence_title": evidence_title,
            "field_defaults": defaults,
            "shard_no": shard_no,
        }
        existing = by_id.get(cluster_id)
        if existing is None:
            by_id[cluster_id] = candidate
            continue
        # 同片重复簇：保留更强的一条（importance → freshness → 原顺序）
        if (candidate["importance"], candidate["freshness"]) > (
            existing["importance"], existing["freshness"],
        ):
            by_id[cluster_id] = candidate
        result["dropped_invalid"] += 1

    result["candidates"] = list(by_id.values())
    return result


# =============================================================================
# Step 4: 全局去重 → MMR → 终审
# =============================================================================

def _source_quality(entry: dict) -> float:
    source = (entry.get("source") or "").strip()
    source_type = (entry.get("source_type") or "").strip()
    if source_type == "intelligence" and source.startswith("intelligence_rss-"):
        return _SOURCE_QUALITY_PROFESSIONAL
    if source_type in ("cls", "wallstreetcn", "sina", "caixin", "cctv"):
        return _SOURCE_QUALITY_PROFESSIONAL
    if source_type == "eastmoney":
        return _SOURCE_QUALITY_EASTMONEY
    return _SOURCE_QUALITY_DEFAULT


def merge_shard_candidates(
    candidates: Sequence[dict],
    clusters: Sequence[NewsCluster],
    *,
    drop_out: Optional[Dict[str, int]] = None,
) -> List[dict]:
    """合并同簇/同主题跨片重复；未知 cluster_id 丢弃（任何候选必须能映射回真实簇）。"""
    by_id = {c.cluster_id: c for c in clusters}
    drop_out = drop_out if drop_out is not None else {}
    drop_out.setdefault("unknown_cluster", 0)

    merged: Dict[str, dict] = {}
    order: List[str] = []
    for candidate in candidates:
        cluster_id = candidate.get("cluster_id")
        cluster = by_id.get(cluster_id)
        if cluster is None:
            drop_out["unknown_cluster"] += 1
            continue
        representative = cluster.representative
        record = merged.get(cluster_id)
        if record is None:
            merged[cluster_id] = {
                **candidate,
                "title": (representative.get("title") or "").strip(),
                "source": representative.get("source") or "",
                "source_type": representative.get("source_type") or "",
                "source_quality": _source_quality(representative),
                "title_norm": normalize_title(representative.get("title", "")),
                "member_titles": _member_display(cluster),
                "appearance_count": 1,
                "shards": [candidate.get("shard_no")],
            }
            order.append(cluster_id)
            continue
        record["importance"] = max(record.get("importance", 1), candidate.get("importance", 1))
        record["freshness"] = max(record.get("freshness", 0), candidate.get("freshness", 0))
        if not record.get("topic"):
            record["topic"] = candidate.get("topic", "")
        if not record.get("reason"):
            record["reason"] = candidate.get("reason", "")
        record["appearance_count"] += 1
        record["shards"].append(candidate.get("shard_no"))

    # 跨片同主题 + 同代表标题 → 合并（cluster_id 已保证唯一，保留作显式去重闸门）
    deduped: Dict[tuple, dict] = {}
    for cluster_id in order:
        record = merged[cluster_id]
        key = (record["topic"].lower(), record["title_norm"])
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = record
            continue
        existing["importance"] = max(existing.get("importance", 1), record.get("importance", 1))
        existing["freshness"] = max(existing.get("freshness", 0), record.get("freshness", 0))
        existing["appearance_count"] += record["appearance_count"]
        drop_out["duplicate"] = drop_out.get("duplicate", 0) + 1
    return list(deduped.values())


def _candidate_score(candidate: dict) -> float:
    importance = candidate.get("importance", 1) or 1
    freshness = candidate.get("freshness", 0) or 0
    return (
        0.55 * importance / 3
        + 0.25 * freshness / 2
        + 0.20 * candidate.get("source_quality", _SOURCE_QUALITY_DEFAULT)
    )


def diversify_candidates(
    candidates: Sequence[dict], *, limit: Optional[int] = None,
) -> List[dict]:
    """MMR：`score − 0.60 × 最大主题相似度`，同主题 ≤3、同簇 ≤1。"""
    cap = GOT_NEWS_FINALIST_LIMIT if limit is None else max(1, int(limit))
    pool: List[dict] = []
    for candidate in candidates:
        pool.append({
            **candidate,
            "score": round(_candidate_score(candidate), 6),
            "topic_text": (candidate.get("topic") or candidate.get("title") or "").strip(),
        })

    chosen: List[dict] = []
    topic_counts: Dict[str, int] = {}
    chosen_topics: List[str] = []
    while len(chosen) < cap and pool:
        best: Optional[dict] = None
        best_final = 0.0
        best_penalty = 0.0
        for candidate in pool:
            topic_text = candidate["topic_text"]
            if topic_counts.get(topic_text, 0) >= TOPIC_CAP:
                continue
            penalty = max(
                (title_similarity(topic_text, prior) for prior in chosen_topics),
                default=0.0,
            )
            final = candidate["score"] - MMR_LAMBDA * penalty
            if best is None or final > best_final:
                best, best_final, best_penalty = candidate, final, penalty
        if best is None:
            break
        chosen.append({**best, "mmr_score": round(best_final, 6), "mmr_penalty": round(best_penalty, 6)})
        topic_counts[best["topic_text"]] = topic_counts.get(best["topic_text"], 0) + 1
        chosen_topics.append(best["topic_text"])
        pool.remove(best)
    return chosen


def _final_review_prompt(candidates: Sequence[dict], limit: int) -> str:
    lines: List[str] = []
    for i, candidate in enumerate(candidates, 1):
        lines.append(
            f"{i}. [{candidate['cluster_id']}] importance={candidate.get('importance', 1)}"
            f" freshness={candidate.get('freshness', 0)} topic={candidate.get('topic') or '未命名'}"
            f"\n   {candidate.get('title', '')}"
        )
        members = candidate.get("member_titles") or []
        if len(members) > 1:
            lines.append("   成员: " + " | ".join(members[1:3]))
    return NEWS_FINAL_REVIEW_PROMPT.format(
        count=len(candidates), limit=limit, candidates="\n".join(lines),
    )


def review_candidates(
    node: Any, candidates: Sequence[dict], *, limit: Optional[int] = None,
    stats_out: Optional[Dict[str, int]] = None,
) -> Optional[List[dict]]:
    """终审：一次 `_call_llm`。

    Returns:
        - `[]`：合法空选择（当日没有重要事件），不是失败；
        - `None`：空响应 / JSON 失败 / 全部非法 cluster_id → 调用方回退 MMR 次序；
        - 选中列表：按模型原始顺序，最多 `GOT_NEWS_TITLE_SHORTLIST_SIZE` 条。
    """
    cap = GOT_NEWS_TITLE_SHORTLIST_SIZE if limit is None else max(1, int(limit))
    if not candidates:
        return []

    raw = node._call_llm(
        _final_review_prompt(candidates, cap),
        temperature=FINAL_REVIEW_TEMPERATURE,
        max_tokens=FINAL_REVIEW_MAX_TOKENS,
    )
    if not raw:
        return None
    data = node._extract_json(raw)
    selected = data.get("selected") if isinstance(data, dict) else None
    if not isinstance(selected, list):
        return None

    by_id = {c["cluster_id"]: c for c in candidates}

    def _category_of(cluster_id: str) -> str:
        return news_category(by_id[cluster_id].get("title", ""))

    stats = stats_out if stats_out is not None else {}
    stats["returned"] = len(selected)
    stats["invalid"] = 0
    stats["duplicate"] = 0
    picked: List[dict] = []
    seen: set = set()
    for entry in selected:
        if len(picked) >= cap:
            break
        if not isinstance(entry, dict):
            stats["invalid"] += 1
            continue
        cluster_id = entry.get("cluster_id")
        if not isinstance(cluster_id, str) or cluster_id not in by_id:
            stats["invalid"] += 1
            continue
        if cluster_id in seen:
            stats["duplicate"] += 1
            continue
        seen.add(cluster_id)
        merge_with = entry.get("merge_with")
        merge_ok = (
            isinstance(merge_with, str)
            and merge_with in by_id
            and merge_with != cluster_id
            and _category_of(merge_with) == _category_of(cluster_id)
        )
        picked.append({
            "cluster_id": cluster_id,
            "merge_with": merge_with if merge_ok else None,
            "reason": str(entry.get("reason") or "").strip()[:200],
            "fallback": False,
        })

    if selected and not picked:
        return None
    return picked


def _cluster_item(cluster: NewsCluster, merge_sources: Sequence[NewsCluster]) -> dict:
    """终审选中 → 细读条目：保留代表条目的真实字段，追加簇溯源元数据。"""
    item = dict(cluster.representative)
    titles: List[str] = list(item.get("member_titles") or [item.get("title", "")])
    sources: List[str] = list(item.get("sources") or [])
    for member in cluster.members:
        titles.append(member.get("title", ""))
        titles.extend(member.get("member_titles") or [])
        sources.extend(member.get("sources") or _sources_of(member))
    for extra in merge_sources:
        titles.extend(extra.representative.get("member_titles") or [extra.representative.get("title", "")])
        sources.extend(extra.representative.get("sources") or [])
        for member in extra.members:
            titles.append(member.get("title", ""))
            sources.extend(member.get("sources") or [])
    item["news_cluster_id"] = cluster.cluster_id
    item["member_titles"] = _dedupe(titles)
    item["sources"] = _dedupe(sources)
    return item


def _assemble_shortlist(
    selected: Sequence[dict], clusters: Sequence[NewsCluster],
) -> List[dict]:
    by_id = {c.cluster_id: c for c in clusters}
    merged_away = {
        entry["merge_with"] for entry in selected
        if entry.get("merge_with") in by_id
    }
    ordered_ids: List[str] = []
    extras: Dict[str, List[NewsCluster]] = {}
    for entry in selected:
        cluster_id = entry.get("cluster_id")
        if cluster_id not in by_id or cluster_id in merged_away or cluster_id in ordered_ids:
            continue
        ordered_ids.append(cluster_id)
        merge_with = entry.get("merge_with")
        if merge_with in by_id:
            extras.setdefault(cluster_id, []).append(by_id[merge_with])
    return [_cluster_item(by_id[cid], extras.get(cid, [])) for cid in ordered_ids]


# =============================================================================
# Step 5: 唯一 async 编排入口
# =============================================================================

def _unavailable(msg: str) -> NewsRetrievalUnavailable:
    return NewsRetrievalUnavailable(msg)


async def _embed_title_vectors(
    titles: Sequence[str], run_date: date, *, stats: Optional[Dict[str, Any]] = None,
) -> List[List[float]]:
    """按日缓存的主标题向量（结构复用 glue_pipeline 的 sha1(model+text) 约定）。"""
    cache_path = Path(GOT_NEWS_EMBED_CACHE_ROOT) / f"news_title_embed_{run_date.strftime('%Y%m%d')}.json"
    cache = _load_embed_cache(cache_path)
    entries = cache.get("entries")
    if cache.get("model") != GLUE_EMBEDDING_MODEL or not isinstance(entries, dict):
        # 损坏/换模型 → 本次运行内重建（不用部分坏数据）
        cache = {"model": GLUE_EMBEDDING_MODEL, "entries": {}}
        entries = cache["entries"]

    keys = [_cache_key(GLUE_EMBEDDING_MODEL, title) for title in titles]
    key_to_text = {key: title for key, title in zip(keys, titles)}
    missing = [key for key in dict.fromkeys(keys) if key not in entries]
    if missing:
        texts = [key_to_text[key] for key in missing]
        try:
            vectors = await embed_texts(texts)
        except Exception as exc:  # noqa: BLE001 — 缺 key/网络/限流统一降级
            raise _unavailable(f"标题 embedding 不可用: {exc}") from exc
        if len(vectors) != len(texts):
            raise _unavailable(f"标题 embedding 数量不匹配: {len(vectors)} != {len(texts)}")
        for key, vector in zip(missing, vectors):
            entries[key] = vector
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            tmp.replace(cache_path)
        except OSError as exc:  # 缓存写失败不影响本次聚类
            logger.warning("[Node 4] 标题向量缓存写入失败（已忽略）: %s", exc)
    if stats is not None:
        stats["embedding_calls"] = int(stats.get("embedding_calls", 0)) + len(missing)
        stats["embedding_cache_hits"] = len(keys) - len(missing)
    return [entries[key] for key in keys]


def _write_trace(trace: Dict[str, Any], run_date: date) -> None:
    """落 `GOT_NEWS_TRACE_ROOT/YYYYMMDD/news_retrieval_trace.json`（tmp+replace）。"""
    try:
        day_dir = Path(GOT_NEWS_TRACE_ROOT) / run_date.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        out = day_dir / "news_retrieval_trace.json"
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(out)
    except OSError as exc:
        logger.warning("[Node 4] news_retrieval_trace.json 落盘失败（已忽略）: %s", exc)


async def read_news(
    node: Any, items: Sequence[dict], *, run_date: Optional[date] = None,
) -> tuple:
    """分层粗筛唯一入口：返回 `(shortlist, trace)`。

    降级契约：
    - embedding 不可用 → Jaccard 聚类（`index_mode=jaccard_fallback`）；
    - 单片 LLM 失败 → 该片无候选，其余片照常；
    - 终审空响应/JSON 失败/全部非法 ID → MMR 前 12（`review_status=fallback_mmr`）；
    - 终审合法空数组 → `[]`（当日无重要事件，不伪造结论）。
    """
    started = time.monotonic()
    run_date = run_date or date.today()
    trace: Dict[str, Any] = {
        "date": run_date.strftime("%Y%m%d"),
        "arch": "cluster",
        "index_mode": "embedding",
        "pool_size": len(items or []),
        "cluster_count": 0,
        "shard_count": 0,
        "candidate_count": 0,
        "merged_count": 0,
        "merged_topic_count": 0,
        "finalist_count": 0,
        "selected_count": 0,
        "dropped_duplicate": 0,
        "dropped_invalid": 0,
        "llm_failures": 0,
        "llm_calls": 0,
        "embedding_calls": 0,
        "embedding_cache_hits": 0,
        "review_status": "skipped",
        "review_stats": {},
        "degraded_reason": None,
        "shards": [],
        "finalists": [],
        "selected": [],
    }

    if not items:
        trace["elapsed_seconds"] = round(time.monotonic() - started, 3)
        _write_trace(trace, run_date)
        return [], trace

    bases = _collapse_exact(items)
    if not bases:
        trace["elapsed_seconds"] = round(time.monotonic() - started, 3)
        _write_trace(trace, run_date)
        return [], trace

    try:
        vectors = await _embed_title_vectors(
            [base["title"] for base in bases], run_date, stats=trace,
        )
        clusters = index_news_clusters(items, vectors=vectors)
    except NewsRetrievalUnavailable as exc:
        logger.warning("[Node 4] 事件簇索引降级为 Jaccard 聚类: %s", exc)
        clusters = index_news_clusters(items)
        trace["index_mode"] = "jaccard_fallback"
        trace["degraded_reason"] = str(exc)
    trace["cluster_count"] = len(clusters)
    if not clusters:
        trace["elapsed_seconds"] = round(time.monotonic() - started, 3)
        _write_trace(trace, run_date)
        return [], trace

    shards = make_reading_shards(clusters, shard_size=GOT_NEWS_READ_SHARD_SIZE)
    trace["shard_count"] = len(shards)
    logger.info(
        "[Node 4] 分层粗筛: 池 %d → 簇 %d → 分片 %d（index_mode=%s）",
        len(items), len(clusters), len(shards), trace["index_mode"],
    )

    results: List[Dict[str, Any]] = []
    consecutive_failures = 0
    wave = max(1, GOT_NEWS_SHARD_CONCURRENCY)
    for start in range(0, len(shards), wave):
        shard_wave = shards[start:start + wave]
        if consecutive_failures >= MAX_CONSECUTIVE_SHARD_FAILURES:
            for offset, shard in enumerate(shard_wave):
                results.append({
                    "shard_no": start + offset + 1,
                    "cluster_count": len(shard),
                    "input_chars": 0,
                    "llm_calls": 0,
                    "candidates": [],
                    "dropped_invalid": 0,
                    "status": "skipped",
                })
            continue
        wave_results = await asyncio.gather(*(
            asyncio.to_thread(
                score_shard, node, shard,
                shard_no=start + offset + 1, shard_total=len(shards),
            )
            for offset, shard in enumerate(shard_wave)
        ))
        for result in wave_results:
            results.append(result)
            consecutive_failures = (
                consecutive_failures + 1 if result["status"] == "failed" else 0
            )
        if consecutive_failures >= MAX_CONSECUTIVE_SHARD_FAILURES:
            logger.warning(
                "[Node 4] 连续 %d 个分片 LLM 失败，停止后续分片调用（保留已有候选）",
                consecutive_failures,
            )

    all_candidates: List[dict] = []
    for result in results:
        all_candidates.extend(result["candidates"])
        trace["llm_calls"] += int(result.get("llm_calls", 0))
        trace["dropped_invalid"] += int(result.get("dropped_invalid", 0))
        if result["status"] == "failed":
            trace["llm_failures"] += 1
        trace["shards"].append({
            "shard_no": result["shard_no"],
            "cluster_count": result["cluster_count"],
            "status": result["status"],
            "candidate_count": len(result["candidates"]),
            "input_chars": result["input_chars"],
            "llm_calls": result["llm_calls"],
            "dropped_invalid": result["dropped_invalid"],
        })
    trace["candidate_count"] = len(all_candidates)

    drop_out: Dict[str, int] = {"unknown_cluster": 0, "duplicate": 0}
    merged = merge_shard_candidates(all_candidates, clusters, drop_out=drop_out)
    trace["dropped_duplicate"] = drop_out.get("duplicate", 0) + drop_out.get("unknown_cluster", 0)
    trace["dropped_invalid"] += drop_out.get("unknown_cluster", 0)
    trace["merged_count"] = len(merged)
    trace["merged_topic_count"] = len({
        (record.get("topic") or record.get("title") or "").strip() for record in merged
    })

    finalists = diversify_candidates(merged)
    trace["finalist_count"] = len(finalists)
    trace["finalists"] = [
        {
            "cluster_id": candidate["cluster_id"],
            "title": candidate.get("title", ""),
            "topic": candidate.get("topic", ""),
            "importance": candidate.get("importance", 1),
            "freshness": candidate.get("freshness", 0),
            "source_quality": candidate.get("source_quality", _SOURCE_QUALITY_DEFAULT),
            "score": candidate.get("score"),
            "mmr_score": candidate.get("mmr_score"),
            "appearance_count": candidate.get("appearance_count", 1),
        }
        for candidate in finalists
    ]

    review_stats: Dict[str, int] = {}
    if finalists:
        try:
            review = await asyncio.to_thread(
                review_candidates, node, finalists, stats_out=review_stats,
            )
        except Exception as exc:  # noqa: BLE001 — 终审异常 → MMR 次序回退
            logger.warning("[Node 4] 终审 LLM 异常（回退 MMR 次序）: %s", exc)
            review = None
        trace["llm_calls"] += 1
    else:
        review = []
    trace["review_stats"] = review_stats
    if review is None:
        trace["review_status"] = "fallback_mmr"
        chosen = [
            {
                "cluster_id": candidate["cluster_id"],
                "merge_with": None,
                "reason": "终审失败 → MMR 次序回退",
                "fallback": True,
            }
            for candidate in finalists[:GOT_NEWS_TITLE_SHORTLIST_SIZE]
        ]
    elif not review:
        trace["review_status"] = "empty"
        chosen = []
    else:
        trace["review_status"] = "ok"
        chosen = review

    shortlist = _assemble_shortlist(chosen, clusters)
    trace["selected_count"] = len(shortlist)
    title_by_cluster = {
        cluster.cluster_id: (cluster.representative.get("title") or "").strip()
        for cluster in clusters
    }
    trace["selected"] = [
        {
            "cluster_id": entry["cluster_id"],
            "merge_with": entry.get("merge_with"),
            "reason": entry.get("reason", ""),
            "fallback": bool(entry.get("fallback")),
            "title": title_by_cluster.get(entry["cluster_id"], ""),
        }
        for entry in chosen
    ]

    trace["elapsed_seconds"] = round(time.monotonic() - started, 3)
    _write_trace(trace, run_date)
    logger.info(
        "[Node 4] 分层粗筛完成: 簇 %d / 片 %d / 候选 %d / 终审池 %d / 入选 %d（%.1fs）",
        trace["cluster_count"], trace["shard_count"], trace["candidate_count"],
        trace["finalist_count"], trace["selected_count"], trace["elapsed_seconds"],
    )
    return shortlist, trace
