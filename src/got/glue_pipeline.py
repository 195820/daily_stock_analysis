# -*- coding: utf-8 -*-
"""C-P13 阶段1：事件→板块「接地式召回-验证」管线（L1 检索接地 + L2 逐事件生成 + L3 CoVe 剪枝）。

依据 docs/got/cp13_grounding_pipeline_research_20260926.md §3/§5.3：
- L1（THEME, P1）：板块/事件 embedding 同一空间，cosine top-K 检索候选而非自由生成——
  候选有界化同时压「A 漏挂直接项」与「B 过度归属摊派」两端病灶；
- L2（P1/P3/P7）：逐事件受约束生成，board_code 只允许落在该事件 top-K 内，
  每条必须引用可定位到原文的证据 span（Langextract 式硬护栏，定位不到即丢弃）；
- L3（CoVe, P2/P8）：独立 prompt 批量验证「仅凭证据 span 是否成立」，no 则剪枝；
- 护栏（笔记 §4）：(event_id, board_code) 去重、单事件 relation 硬上限、
  重复循环检测触发「整日作废」（day_void，base relations 保留不替换）。

⚠️ 运行边界：本模块只被 aggregator._agent_semantic_glue 在 replay_mode +
GLUE_PIPELINE_ENABLED=true 时调用；生产链路永不进入。输出与 GlueRelation 同构
（dict 形式），schema 不变；confidence 阶段 1 仍为 LLM 自报（L5 校准属阶段 2）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.got.catalyst_config import (
    GLUE_EMBEDDING_API_KEY,
    GLUE_EMBEDDING_BASE_URL,
    GLUE_EMBEDDING_MODEL,
    GLUE_EVENT_RELATION_CAP,
    GLUE_L1_TOP_K,
    GLUE_L2_CONCURRENCY,
    GLUE_VERIFY_BATCH,
    LLM_CALL_TIMEOUT_S,
)
from src.got.indicators import clip

logger = logging.getLogger(__name__)

# L1 embedding 缓存根目录（可注入：测试重定向到 tmp，避免污染真实归档；
# 与 aggregator.GLUE_ARCHIVE_ROOT 同款惯例）。按日分文件：{root}/l1_embed_{YYYYMMDD}.json
L1_EMBED_CACHE_ROOT = Path("data/got_daily/_cache")

# dashscope compatible-mode 单次 embedding 请求的文本条数上限（保守值）
_EMBED_CHUNK = 10
# L2/L3 单事件输出体量小，4000 tokens 足够（不跟随 GLUE_MAX_TOKENS=16000 的全量口径）
_PIPELINE_MAX_TOKENS = 4000
# source_news 拼入 embedding 文本与 L2 证据区的截断长度
_EVIDENCE_MAX_CHARS = 600

# 护栏④「整日作废」第二条（H-2）：全日去重后/去重前比值低于此阈值判定退化循环（笔记 §4）。
# 治「多事件各挂满 top-K、单事件到不了 3×cap」的 0902 式全日摊派形态——
# 该形态下逐事件计数不超 3×cap，唯有全日去重坍缩比能捕获。
_DAY_VOID_RATIO = 0.5
# ...且去重前样本量需达下限，避免小样本（如 1~2 条重复）假触发整日作废。
_DAY_VOID_MIN_BEFORE = 6


# =============================================================================
# L1 · 检索接地（候选有界化）
# =============================================================================

def _normalize_ws(text: str) -> str:
    """去全部空白 + 弯引号归一，用于证据 span 的子串定位（P7 存在性校验）。"""
    return re.sub(r"\s+", "", text or "").replace("\u201c", '"').replace("\u201d", '"')


def build_sector_text(name: str, detail, role_info: dict) -> str:
    """板块侧 embedding 文本：名 + 角色 + tier + 产业主题组。

    注：设计笔记理想输入为「名+描述+成分股」，但 SectorDetail 无 description/成分股
    字段（计划已 anticipated：无则只用名+角色）；板块名本身是语义主载体，
    冒烟不达标时再扩文本构造（cp13_l1_smoke.py 负责验证）。
    """
    parts = [f"板块名称: {name}"]
    role = (role_info or {}).get("role")
    if role:
        parts.append(f"角色: {role}")
    tier = (role_info or {}).get("tier")
    if tier:
        parts.append(f"层级: {tier}")
    frame_group = getattr(detail, "frame_group", None)
    if frame_group:
        parts.append(f"产业主题: {frame_group}")
    return "；".join(parts)


def build_event_text(ev: dict) -> str:
    """事件侧 embedding 文本：title + sector + affected_sectors + source_news（截断）。"""
    parts = [f"事件: {ev.get('title', '')}"]
    if ev.get("sector"):
        parts.append(f"所属板块: {ev['sector']}")
    affected = ev.get("affected_sectors") or []
    if affected:
        parts.append(f"涉及板块: {'、'.join(str(a) for a in affected)}")
    source_news = str(ev.get("source_news") or "")
    if source_news:
        parts.append(f"原文: {source_news[:_EVIDENCE_MAX_CHARS]}")
    return "；".join(parts)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """litellm.embedding() 走 dashscope compatible-mode（OpenAI 协议）。

    失败/未配 key 抛 RuntimeError，由 run_relations_pipeline 兜底降级
    （status="embedding_unavailable"，base relations 保留不替换）。
    """
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        return []
    try:
        import litellm  # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError(f"litellm 不可用，无法计算 embedding: {e}") from e

    if not GLUE_EMBEDDING_API_KEY:
        raise RuntimeError("GLUE_EMBEDDING_API_KEY 未配置（L1 检索接地需要 embedding 服务）")
    model_ref = GLUE_EMBEDDING_MODEL
    if not re.match(r"^[\w-]+/", model_ref):
        model_ref = f"openai/{model_ref}"  # OpenAI-compat 协议前缀

    vectors: list[list[float]] = []
    for i in range(0, len(texts), _EMBED_CHUNK):
        chunk = texts[i:i + _EMBED_CHUNK]
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = await asyncio.to_thread(
                    litellm.embedding,
                    model=model_ref,
                    input=chunk,
                    api_base=GLUE_EMBEDDING_BASE_URL,
                    api_key=GLUE_EMBEDDING_API_KEY,
                )
                vectors.extend([list(d["embedding"]) for d in resp.data])
                last_exc = None
                break
            except Exception as e:  # noqa: BLE001
                last_exc = e
                logger.warning(f"[C-P13 L1] embedding 调用失败 (attempt {attempt + 1}): {e}")
                await asyncio.sleep(1.0)
        if last_exc is not None:
            raise RuntimeError(f"embedding 调用失败: {last_exc}") from last_exc
    return vectors


def retrieve_topk(
    event_vec: list[float],
    sector_vecs: list[list[float]],
    sector_codes: list[str],
    k: int,
) -> list[tuple[str, float]]:
    """cosine top-K 检索（纯函数便于单测）。返回 [(board_code, score)] 按分数降序。"""
    import numpy as np  # noqa: PLC0415

    if not sector_vecs or not event_vec:
        return []
    ev = np.asarray(event_vec, dtype=float)
    mat = np.asarray(sector_vecs, dtype=float)
    ev_norm = np.linalg.norm(ev)
    mat_norm = np.linalg.norm(mat, axis=1)
    denom = ev_norm * mat_norm
    denom[denom == 0] = 1e-12
    scores = mat @ ev / denom
    order = np.argsort(-scores)[: max(1, k)]
    return [(sector_codes[i], round(float(scores[i]), 4)) for i in order]


def _cache_key(model: str, text: str) -> str:
    return hashlib.sha1(f"{model}\x00{text}".encode("utf-8")).hexdigest()


def _load_embed_cache(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[C-P13 L1] embedding 缓存损坏，忽略重建: {path} ({e})")
    return {"model": GLUE_EMBEDDING_MODEL, "entries": {}}


@dataclass
class L1Index:
    """L1 检索结果：板块向量 + 逐事件 top-K 候选。"""
    sector_codes: list[str]
    sector_names: dict[str, str]
    sector_vecs: list[list[float]]
    event_ids: list[str]
    topk_by_event: dict[str, list[tuple[str, float]]]
    cache_hits: int = 0
    cache_misses: int = 0
    model: str = ""


async def build_l1_index(candidates, events: list[dict], run_date,
                         top_k: int = GLUE_L1_TOP_K) -> L1Index:
    """对候选板块与事件计算 embedding（带按日缓存）并做 cosine top-K 检索。

    缓存: L1_EMBED_CACHE_ROOT/l1_embed_{YYYYMMDD}.json，
    结构 {"model":..., "entries": {sha1(model+text): vec}}——同 model 同 text 命中不重算，
    9 天矩阵重跑只算一次。embedding 失败抛 RuntimeError（上层降级）。
    """
    sectors: list[dict] = []
    seen_codes: set[str] = set()
    for name, detail, role_info in candidates:
        code = getattr(detail, "code", "") or ""
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        sectors.append({
            "code": code,
            "name": name,
            "text": build_sector_text(name, detail, role_info),
        })
    if not sectors:
        raise RuntimeError("L1: 无有效候选板块（code 为空）")

    cache_path = L1_EMBED_CACHE_ROOT / f"l1_embed_{run_date.strftime('%Y%m%d')}.json"
    cache = _load_embed_cache(cache_path)
    if cache.get("model") != GLUE_EMBEDDING_MODEL:
        cache = {"model": GLUE_EMBEDDING_MODEL, "entries": {}}
    entries: dict = cache["entries"]

    all_texts = [s["text"] for s in sectors] + [build_event_text(ev) for ev in events]
    all_keys = [_cache_key(GLUE_EMBEDDING_MODEL, t) for t in all_texts]
    missing_idx = [i for i, key in enumerate(all_keys) if key not in entries]
    hits = len(all_keys) - len(missing_idx)
    if missing_idx:
        new_vecs = await embed_texts([all_texts[i] for i in missing_idx])
        if len(new_vecs) != len(missing_idx):
            raise RuntimeError(f"L1: embedding 数量不匹配（{len(new_vecs)} != {len(missing_idx)}）")
        for i, vec in zip(missing_idx, new_vecs):
            entries[all_keys[i]] = vec
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(cache, ensure_ascii=False), encoding="utf-8",
        )

    sector_vecs = [entries[_cache_key(GLUE_EMBEDDING_MODEL, s["text"])] for s in sectors]
    topk_by_event: dict[str, list[tuple[str, float]]] = {}
    for ev in events:
        vec = entries[_cache_key(GLUE_EMBEDDING_MODEL, build_event_text(ev))]
        topk_by_event[str(ev.get("event_id", ""))] = retrieve_topk(
            vec, sector_vecs, [s["code"] for s in sectors], top_k,
        )
    return L1Index(
        sector_codes=[s["code"] for s in sectors],
        sector_names={s["code"]: s["name"] for s in sectors},
        sector_vecs=sector_vecs,
        event_ids=[str(ev.get("event_id", "")) for ev in events],
        topk_by_event=topk_by_event,
        cache_hits=hits,
        cache_misses=len(missing_idx),
        model=GLUE_EMBEDDING_MODEL,
    )


# =============================================================================
# L2 · 受约束逐事件生成（只允许 top-K 候选 + 证据 span 硬护栏）
# =============================================================================

def build_l2_prompt(event: dict, candidate_boards: list[dict], evidence: str) -> str:
    """单事件受约束生成 prompt。

    candidate_boards: [{"code","name","score"}]（该事件的 L1 top-K）；
    evidence: 可引用的原文证据文本（source_news 等，span 必须能定位到其中）。
    """
    boards_text = "\n".join(
        f"- {b['code']} | {b['name']} | 相关度={b['score']:.3f}" for b in candidate_boards
    )
    return f"""你是 A 股事件-板块因果关联分析引擎。只分析下面这一个事件。

## 事件
{json.dumps(event, ensure_ascii=False, default=str)}

## 候选板块（受控词表 — board_code 只能从中选择，禁止输出任何候选之外的板块）
{boards_text}

## 证据原文（evidence_span 必须逐字摘自这段文本，且不得超出）
{evidence or "（无证据原文——此时只能依据事件标题/所属板块判断，evidence_span 填所依据的事件字段原文）"}

## 任务
判断该事件通过真实传导链**因果驱动**哪些候选板块，输出 relations：
- board_code: 必须在上方候选词表内
- propagation_path: 传导链，每跳一个实体（公司/产品/技术），终点是该板块
- evidence_span: 支持这条关联的证据原文片段，必须能在「证据原文」中逐字定位；定位不到的条目会被系统直接丢弃
- strength: 事件对该板块的影响强度 (0-1)；direction: positive/negative
- confidence: 你对这条归属的置信度 (0-1)
- 构造不出「事件→实体→候选板块」真实链条时，允许放弃该板块（宁缺毋滥，放弃写入 abandoned）
- 禁止编造 propagation_path 与 evidence_span

## 输出格式（严格 JSON，不要其他内容）
```json
{{
  "relations": [
    {{"board_code": "...", "strength": 0.8, "direction": "positive", "propagation_path": ["实体A", "实体B"], "evidence_span": "原文片段", "confidence": 0.7}}
  ],
  "abandoned": [{{"board_code": "...", "reason": "..."}}]
}}
```"""


def _as_object(result):
    """将 json.loads 结果归一为 dict（H-1 硬护栏）。

    模型偶发返回顶层数组/标量（如 `[{...}]` / `"..."` / `123`）时，直接 `.get` 会
    抛 AttributeError 并击穿整条 Node 6。统一归一为 {}，由上层 parse_* 按空结果
    优雅降级（该事件产出 0 条），而不是抛出异常。
    """
    return result if isinstance(result, dict) else {}


def _parse_json_lenient(raw: str) -> dict:
    """解析 LLM JSON（fence 剥离 + 截断抢救，与 aggregator._parse_glue_response 同款策略）。

    无论走哪条分支，返回必为 dict（顶层数组/标量经 _as_object 归一为 {}）。
    仅当完全找不到 JSON 对象时抛 ValueError（上层 _l2_one 已捕获）。
    """
    cleaned = (raw or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    try:
        return _as_object(json.loads(cleaned))
    except json.JSONDecodeError:
        pass
    # 截断抢救：截到最后一个完整对象，闭合括号
    last_brace = cleaned.rfind("}")
    if last_brace > 0:
        truncated = cleaned[:last_brace + 1]
        truncated += "]" * (truncated.count("[") - truncated.count("]"))
        truncated += "}" * (truncated.count("{") - truncated.count("}"))
        try:
            return _as_object(json.loads(truncated))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        raise ValueError("无 JSON 对象")
    return _as_object(json.loads(m.group()))


def parse_l2_response(raw: str, event: dict) -> tuple[list[dict], int, int]:
    """解析 L2 输出并执行证据 span 存在性校验（P7 硬护栏）。

    Returns: (relations, dropped_no_span, dropped_outside_span)
    - evidence_span 为空 → 丢弃（dropped_no_span）
    - evidence_span 归一化后不是证据原文子串 → 丢弃（dropped_outside_span，编造嫌疑）
    board_code 白名单校验放在 apply_guards（统一记 warning）。
    """
    parsed = _parse_json_lenient(raw)
    if not isinstance(parsed, dict):   # H-1 兑底：归一后仍非 dict → 当空结果处理
        return [], 0, 0
    evidence_norm = _normalize_ws(
        " ".join(str(v) for v in [event.get("source_news"), event.get("title"),
                                  event.get("sector"), *(event.get("affected_sectors") or [])] if v)
    )
    kept: list[dict] = []
    dropped_no_span = dropped_outside = 0
    raw_relations = parsed.get("relations", [])
    if not isinstance(raw_relations, list):   # relations 字段非数组 → 当空
        raw_relations = []
    for r in raw_relations:
        if not isinstance(r, dict):
            continue
        span = str(r.get("evidence_span", "") or "").strip()
        if not span:
            dropped_no_span += 1
            continue
        if evidence_norm and _normalize_ws(span) not in evidence_norm:
            dropped_outside += 1
            continue
        kept.append(r)
    return kept, dropped_no_span, dropped_outside


# =============================================================================
# L3 · CoVe 独立验证剪枝
# =============================================================================

def relation_key(rel: dict) -> str:
    return f"{rel.get('event_id', '')}::{rel.get('board_code', '')}"


@dataclass
class VerifyVerdict:
    verdict: str = ""       # yes / no
    mechanism: str = ""


def build_l3_verify_prompt(items: list[dict]) -> str:
    """独立验证 prompt（不复用 L2 生成时的语义假设，CoVe 核心约束）。

    items: [{"id", "event_title", "board_code", "board_name", "propagation_path", "evidence_span"}]
    """
    lines = []
    for it in items:
        lines.append(json.dumps(it, ensure_ascii=False, default=str))
    items_text = "\n".join(lines)
    return f"""你是独立的事实核查器。不要考虑关联的强度或置信度，只回答「是否成立」。
对下面每条「事件→板块」关联，仅凭该条自带的证据片段判断：
该事件是否通过所述传导路径，改变这个板块的需求或基本面？

## 待验证关联（每行一条 JSON，id 为关联编号）
{items_text}

## 输出格式（严格 JSON，不要其他内容）
```json
{{"results": [{{"id": "...", "verdict": "yes 或 no", "mechanism": "一句话机制"}}]}}
```"""


def parse_verify_response(raw: str, expected_ids: list[str]) -> dict[str, VerifyVerdict]:
    """解析验证输出。缺失/无法解析的 id 不出现在返回值中（上层按「保守保留」处理）。"""
    try:
        parsed = _parse_json_lenient(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):   # H-1 兑底：归一后仍非 dict → 当空（上层保守保留）
        return {}
    out: dict[str, VerifyVerdict] = {}
    expected = set(expected_ids)
    raw_results = parsed.get("results", [])
    if not isinstance(raw_results, list):   # results 字段非数组 → 当空
        raw_results = []
    for r in raw_results:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id", ""))
        if rid not in expected:
            continue
        verdict = str(r.get("verdict", "")).strip().lower()
        out[rid] = VerifyVerdict(
            verdict="yes" if verdict.startswith("y") else ("no" if verdict.startswith("n") else ""),
            mechanism=str(r.get("mechanism", "")),
        )
    return out


# =============================================================================
# 护栏（笔记 §4）
# =============================================================================

def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_path(v) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if v is None:
        return []
    return [str(v)]


def apply_guards(
    relations: list[dict],
    topk_by_event: dict[str, list[str]],
    cap: int = GLUE_EVENT_RELATION_CAP,
) -> tuple[list[dict], list[str], bool]:
    """结构性护栏，Returns: (kept_relations, warnings, day_void)。

    ① board_code ∉ 该事件 top-K 白名单 → 强制丢弃（结构性杜绝 24 板乱摊派）
    ② (event_id, board_code) 去重保 strength 最大（0902 退化循环的第一道防线）
    ③ 单事件 relation 数 > cap → 按 strength 降序截断（笔记 §4 硬上限兜底）
    ④ 重复循环检测→ day_void=True（整日作废，base relations 保留不替换）：
       4a. 任一事件去重前产出 > 3×cap 条（单事件退化循环）；
       4b. 全日去重后/去重前 < _DAY_VOID_RATIO（且去重前 >= _DAY_VOID_MIN_BEFORE）
           —— 治「多事件各挂满 top-K、单事件到不了 3×cap」的全日摊派形态。
       两条件任一命中均避免 191 条式垃圾污染样本与后续 L5 自一致频率。
    """
    warnings: list[str] = []
    raw_count_by_event: dict[str, int] = {}
    for r in relations:
        raw_count_by_event[str(r.get("event_id", ""))] = (
            raw_count_by_event.get(str(r.get("event_id", "")), 0) + 1
        )
    day_void = any(n > 3 * cap for n in raw_count_by_event.values())
    if day_void:
        offenders = [eid for eid, n in raw_count_by_event.items() if n > 3 * cap]
        warnings.append(
            f"C-P13 护栏④a 重复循环检测触发（事件 {offenders} 各产出 >{3 * cap} 条）→ 整日作废，"
            f"管线 relations 不替换 base"
        )
        return [], warnings, True

    best: dict[tuple[str, str], dict] = {}
    dropped_outside_topk = 0
    dropped_unknown_event = 0
    for r in relations:
        eid = str(r.get("event_id", ""))
        code = str(r.get("board_code", ""))
        if eid not in topk_by_event:
            dropped_unknown_event += 1
            continue
        if code not in topk_by_event[eid]:
            dropped_outside_topk += 1
            continue
        key = (eid, code)
        if key not in best or _to_float(r.get("strength")) > _to_float(best[key].get("strength")):
            best[key] = r
    if dropped_outside_topk:
        warnings.append(f"C-P13 护栏① 丢弃 top-K 白名单外 relation {dropped_outside_topk} 条")
    if dropped_unknown_event:
        warnings.append(f"C-P13 护栏① 丢弃 event_id 不在管线事件集内的 relation {dropped_unknown_event} 条")

    dup = len(relations) - dropped_outside_topk - dropped_unknown_event - len(best)
    if dup > 0:
        warnings.append(f"C-P13 护栏② (event_id, board_code) 去重删除 {dup} 条")

    # 护栏④b（H-2）：全日去重坠缩比——单事件到不了 3×cap 但全日高度重复的摊派形态
    before_dedup = len(relations) - dropped_outside_topk - dropped_unknown_event
    after_dedup = len(best)
    if before_dedup >= _DAY_VOID_MIN_BEFORE and after_dedup / before_dedup < _DAY_VOID_RATIO:
        warnings.insert(0, (
            f"C-P13 护栏④b 全日去重坠缩 {after_dedup}/{before_dedup}"
            f"（比值 {after_dedup / before_dedup:.2f} < {_DAY_VOID_RATIO}）→ 整日作废，"
            f"管线 relations 不替换 base"
        ))
        return [], warnings, True

    kept: list[dict] = []
    by_event: dict[str, list[dict]] = {}
    for r in best.values():
        by_event.setdefault(str(r.get("event_id", "")), []).append(r)
    for eid, rels in by_event.items():
        rels.sort(key=lambda x: _to_float(x.get("strength")), reverse=True)
        if len(rels) > cap:
            warnings.append(f"C-P13 护栏③ 事件 {eid} relation 超上限 {cap}，截断 {len(rels) - cap} 条")
            rels = rels[:cap]
        kept.extend(rels)
    kept.sort(key=lambda r: (str(r.get("event_id", "")), -_to_float(r.get("strength"))))
    return kept, warnings, False


# =============================================================================
# 编排
# =============================================================================

@dataclass
class PipelineResult:
    """管线输出：relations 与 GlueRelation 同构（dict），status 语义见 run_relations_pipeline。"""
    relations: list[dict] = field(default_factory=list)
    trace: dict = field(default_factory=dict)
    status: str = "ok"   # ok / embedding_unavailable / day_void / l2_failed / l2_partial / pipeline_error
    warnings: list[str] = field(default_factory=list)


async def run_relations_pipeline(
    node,
    candidates,
    events: list[dict],
    run_date,
    *,
    top_k: int = GLUE_L1_TOP_K,
    cap: int = GLUE_EVENT_RELATION_CAP,
    verify_batch: int = GLUE_VERIFY_BATCH,
) -> PipelineResult:
    """管线编排入口（H-1 安全网）：内部任何未预期异常都降级为 pipeline_error，绝不抛出。

    aggregator 侧非 "ok" 一律保留 base relations；即使 L2/L3/护栏段出现
    此前未预见形状的崩溃（如模型输出结构意外导致类型错误），也不会击穿整条 Node 6。
    """
    try:
        return await _run_relations_pipeline_impl(
            node, candidates, events, run_date,
            top_k=top_k, cap=cap, verify_batch=verify_batch,
        )
    except Exception as e:  # noqa: BLE001 —— 最后一道兵栏：任何异常降级，不阻断 glue 主流程
        logger.exception(f"[C-P13] 管线未预期异常，降级保留 base relations: {e}")
        return PipelineResult(
            relations=[], trace={"pipeline_error": repr(e)},
            status="pipeline_error",
            warnings=[f"C-P13 管线未预期异常，保留 base relations: {e!r}"],
        )


async def _run_relations_pipeline_impl(
    node,
    candidates,
    events: list[dict],
    run_date,
    *,
    top_k: int = GLUE_L1_TOP_K,
    cap: int = GLUE_EVENT_RELATION_CAP,
    verify_batch: int = GLUE_VERIFY_BATCH,
) -> PipelineResult:
    """L1 → L2 → L3 → 护栏 全链路编排（仅 replay 调用，见模块 docstring）。

    Args:
        node: AggregatorNode 实例（复用 _call_llm_async 的 timeout/重试/usage 落库）
        candidates: [(name, SectorDetail, role_info)]（与 glue 主调用同源）
        events: 管线事件 dict 列表（catalyst 事件 + major_events 合成条目，
                aggregator._collect_pipeline_events 组装）
        run_date: 回放日（缓存文件名用）

    降级语义（任何失败都不抛出，由 aggregator 决定保留 base relations）：
    - embedding 不可用/失败 → status="embedding_unavailable"
    - 全部 L2 调用失败 → status="l2_failed"
    - 部分 L2 事件失败（H-3）→ status="l2_partial"（残缺产出不当效果读入，relations 置空保留 base）
    - 护栏④ day_void → status="day_void"（relations 置空）
    - 任何未预期异常 → 上层 run_relations_pipeline 兑底 status="pipeline_error"
    """
    trace: dict = {
        "run_date": run_date.strftime("%Y%m%d"),
        "top_k": top_k,
        "cap": cap,
        "n_candidates": len(candidates),
        "n_events": len(events),
        "events": [],
    }
    if not events:
        return PipelineResult(relations=[], trace=trace, status="ok",
                              warnings=["C-P13 管线：当日无事件，relations 置空"])

    # ── L1 检索接地 ──
    try:
        index = await build_l1_index(candidates, events, run_date, top_k=top_k)
    except Exception as e:  # noqa: BLE001 —— 降级语义：embedding 失败不阻断 glue 主流程
        logger.warning(f"[C-P13] L1 embedding 失败，管线降级（base relations 保留）: {e}")
        trace["l1_error"] = str(e)
        return PipelineResult(relations=[], trace=trace,
                              status="embedding_unavailable",
                              warnings=[f"C-P13 L1 embedding 不可用，保留 base relations: {e}"])
    trace["l1"] = {
        "model": index.model,
        "n_sectors": len(index.sector_codes),
        "cache_hits": index.cache_hits,
        "cache_misses": index.cache_misses,
        "topk_by_event": {eid: [[c, s] for c, s in tk] for eid, tk in index.topk_by_event.items()},
    }

    # ── L2 受约束逐事件生成 ──
    semaphore = asyncio.Semaphore(max(1, GLUE_L2_CONCURRENCY))
    event_trace: dict[str, dict] = {}

    async def _l2_one(ev: dict) -> list[dict]:
        eid = str(ev.get("event_id", ""))
        topk = index.topk_by_event.get(eid, [])
        t = {
            "event_id": eid,
            "title": str(ev.get("title", ""))[:60],
            "topk": [[c, s] for c, s in topk],
            "l2_raw_n": 0, "dropped_no_span": 0, "dropped_outside_span": 0,
            "dropped_outside_topk": 0, "cove": {"verified": 0, "kept": 0, "dropped": 0},
        }
        event_trace[eid] = t
        if not topk:
            t["error"] = "L1 无候选"
            return []
        boards = [
            {"code": c, "name": index.sector_names.get(c, c), "score": s}
            for c, s in topk
        ]
        evidence = str(ev.get("source_news") or "")[:_EVIDENCE_MAX_CHARS]
        prompt = build_l2_prompt(ev, boards, evidence)
        async with semaphore:
            raw = await node._call_llm_async(
                prompt,
                temperature=0.0,
                max_tokens=_PIPELINE_MAX_TOKENS,
                max_retries=1,
                call_type="got_cp13_l2_generate",
                timeout=float(LLM_CALL_TIMEOUT_S),
            )
        if not raw or not raw.strip():
            t["error"] = "L2 空响应"
            return []
        try:
            kept, no_span, outside_span = parse_l2_response(raw, ev)
        except (json.JSONDecodeError, ValueError) as e:
            t["error"] = f"L2 解析失败: {e}"
            return []
        allowed = {c for c, _ in topk}
        outside_topk = sum(1 for r in kept if str(r.get("board_code", "")) not in allowed)
        rels = []
        for r in kept:
            if str(r.get("board_code", "")) not in allowed:
                continue
            rels.append({
                "event_id": eid,
                "board_code": str(r.get("board_code", "")),
                "strength": clip(_to_float(r.get("strength"), 0.0), 0.0, 1.0),
                "direction": str(r.get("direction", "positive")),
                "propagation_path": _to_path(r.get("propagation_path")),
                "confidence": clip(_to_float(r.get("confidence"), 0.5), 0.0, 1.0),
                "evidence_span": str(r.get("evidence_span", "")),
            })
        t.update({
            "l2_raw_n": len(rels) + outside_topk,
            "dropped_no_span": no_span,
            "dropped_outside_span": outside_span,
            "dropped_outside_topk": outside_topk,
        })
        return rels

    l2_results = await asyncio.gather(
        *[_l2_one(ev) for ev in events], return_exceptions=True
    )
    relations: list[dict] = []
    for ev, batch in zip(events, l2_results):
        if isinstance(batch, BaseException):
            # H-1：_l2_one 抛出的未预期异常就地隔离，记为事件错误（计入 l2_error_n）
            eid = str(ev.get("event_id", ""))
            t = event_trace.get(eid)
            if t is not None:
                t["error"] = f"L2 未预期异常: {batch!r}"
            logger.warning(f"[C-P13] 事件 {eid} L2 抛异常（已隔离，不当作有效产出）: {batch!r}")
            continue
        relations.extend(batch)

    l2_error_n = sum(1 for t in event_trace.values() if t.get("error"))
    if l2_error_n == len(events) and events:
        trace["events"] = list(event_trace.values())
        return PipelineResult(relations=[], trace=trace, status="l2_failed",
                              warnings=[f"C-P13 L2 全部 {l2_error_n} 个事件调用失败，保留 base relations"])

    # ── L3 CoVe 独立验证剪枝 ──
    cove_dropped_keys: set[str] = set()
    cove_warnings: list[str] = []
    # C-P13 阶段2 反事实标定留痕：逐条记录进入 L3 的关系特征 + CoVe 判定，
    # 使 kept/dropped 两支都能回溯到 realized（仅 replay trace 落盘，不进 GlueRelation）。
    cove_detail: list[dict] = []
    topk_rank_sim = {
        eid: {str(c): (rank + 1, s) for rank, (c, s) in enumerate(tk)}
        for eid, tk in index.topk_by_event.items()
    }
    if relations:
        for i in range(0, len(relations), max(1, verify_batch)):
            batch = relations[i:i + max(1, verify_batch)]
            items = []
            for j, rel in enumerate(batch):
                items.append({
                    "id": str(j),
                    "event_title": next(
                        (str(ev.get("title", "")) for ev in events
                         if str(ev.get("event_id", "")) == rel["event_id"]), ""),
                    "board_code": rel["board_code"],
                    "board_name": index.sector_names.get(rel["board_code"], ""),
                    "propagation_path": rel["propagation_path"],
                    "evidence_span": rel["evidence_span"],
                })
            prompt = build_l3_verify_prompt(items)
            raw = await node._call_llm_async(
                prompt,
                temperature=0.0,
                max_tokens=_PIPELINE_MAX_TOKENS,
                max_retries=1,
                call_type="got_cp13_l3_verify",
                timeout=float(LLM_CALL_TIMEOUT_S),
            )
            expected_ids = [it["id"] for it in items]
            verdicts = parse_verify_response(raw, expected_ids) if raw and raw.strip() else {}
            if not verdicts:
                cove_warnings.append(
                    f"C-P13 L3 批次 {i // max(1, verify_batch) + 1} 验证失败/不可解析 → 保守保留该批 {len(batch)} 条"
                )
                continue
            for j, rel in enumerate(batch):
                v = verdicts.get(str(j))
                t = event_trace.get(rel["event_id"])
                _verdict = ("no" if (v is not None and v.verdict == "no")
                            else ("yes" if v is not None else "missing"))
                _rs = topk_rank_sim.get(rel["event_id"], {}).get(str(rel["board_code"]))
                cove_detail.append({
                    "relation_key": relation_key(rel),
                    "event_id": rel["event_id"],
                    "board_code": rel["board_code"],
                    "direction": rel.get("direction", ""),
                    "confidence": rel.get("confidence"),
                    "strength": rel.get("strength"),
                    "topk_rank": _rs[0] if _rs else None,
                    "topk_sim": _rs[1] if _rs else None,
                    "cove_verdict": _verdict,
                })
                if v is not None:
                    if t is not None:
                        t["cove"]["verified"] += 1
                if v is not None and v.verdict == "no":
                    cove_dropped_keys.add(relation_key(rel))
                    if t is not None:
                        t["cove"]["dropped"] += 1
                elif v is not None:
                    if t is not None:
                        t["cove"]["kept"] += 1
                else:
                    # verdict 缺失/非法 → 保守保留并记 warning
                    cove_warnings.append(
                        f"C-P13 L3 id={i + j}（{rel['event_id']}→{rel['board_code']}）判定缺失 → 保守保留"
                    )
        if cove_dropped_keys:
            relations = [r for r in relations if relation_key(r) not in cove_dropped_keys]

    # ── 护栏 ──
    topk_whitelist = {eid: [c for c, _ in tk] for eid, tk in index.topk_by_event.items()}
    relations, guard_warnings, day_void = apply_guards(relations, topk_whitelist, cap=cap)

    # evidence_span 是管线内部证据字段，不进 GlueRelation（schema 不变）
    for r in relations:
        r.pop("evidence_span", None)

    # C-P13 阶段2 反事实标定：标注每条进入 L3 的关系是否最终进入产出（护栏/day_void 后）
    final_keys = {relation_key(r) for r in relations}
    for d in cove_detail:
        d["survived"] = d.pop("relation_key") in final_keys
    trace["cove_detail"] = cove_detail

    warnings = cove_warnings + guard_warnings
    trace["events"] = list(event_trace.values())
    trace["summary"] = {
        "l2_relations": sum(t["l2_raw_n"] for t in event_trace.values()),
        "cove_dropped": len(cove_dropped_keys),
        "final_relations": len(relations),
        "day_void": day_void,
        "l2_error_events": l2_error_n,
    }
    # 状态优先级（H-3）：整日作废 > 部分 L2 失败 > 正常（全部失败已前面早返回 l2_failed）
    if day_void:
        status = "day_void"
        relations = []
    elif l2_error_n:
        # 部分事件 L2 失败 → 残缺产出不当效果读入，置空 relations 让 aggregator 保留 base
        status = "l2_partial"
        relations = []
        warnings.insert(0, (
            f"C-P13 L2 部分失败 {l2_error_n}/{len(events)} 事件，"
            f"残缺产出不替换 base relations（该日需重跑）"
        ))
    else:
        status = "ok"
    return PipelineResult(relations=relations, trace=trace, status=status, warnings=warnings)
