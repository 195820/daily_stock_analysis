# -*- coding: utf-8 -*-
"""K 线家族簇（family cluster）：用 Node 2 的相关性聚类维护「家族 identity」。

问题（2026-09-28–10-30 归档复核）：FTS/FXG 家族级闸门依赖 `frame_group` 关键词首命中，
生产 47/93 个 actionable 主线板块落「其他」（50.5%）→ endurance 家族分组把石油开采/石油/酿酒
合并成同一个 E2 P90 分母、FTS 台账用「其他」键聚合异质板块、Phase 2 层 A join 空转。

修法：Node 2 `theme_families`（90 日收益相关矩阵 + complete linkage）本就是 K 线簇，
本模块把它抬成家族 identity 第一性来源：
- identity = `cluster_id`（成员集 sha1；跨日按成员 Jaccard ≥ jaccard_min 继承，保证 FTS
  latch 键（days_suppressed）跨日稳定）；
- name = 受控家族词表（`PROMPT_EXPOSED_FAMILIES` 12 族），兼容 FTS latch 键与 Phase 2 join；
- 词表多数票（「其他」成员不计票）只作输入，A6 LLM 复核承担标签裁决（事件只做证据，绝不作 key）。

词表两条失效模式都要堵：漏报（落「其他」）与误报（一致命中错家族）。故 vote 不默认生效：
- `REVIEW=shadow`：全簇复核，产出 `a6_label` 与 `vote_a6_agree_rate` 实测误报率；
- `REVIEW=live`：纯「其他」簇必审；vote 簇仅当影子期同意率 ≥ `VOTE_TRUST_MIN` 才豁免，
  且 glue 可用日做零成本分歧检测（成员 glue 多数标签 ≠ vote → 升级复核）。

纪律：任何异常（LLM 超时/解析失败/glue 缺失/IO 失败）一律降级，绝不 raise —— 与生产 glue
链的 fail-fast 相反，本模块是旁路观测/标签裁决层，不能击穿 Node 6 主链路。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from src.got.family_keywords import PROMPT_EXPOSED_FAMILIES

if TYPE_CHECKING:  # 仅类型提示：运行时零 Node 依赖
    from src.got.schemas import ThemeFamily

logger = logging.getLogger(__name__)

OTHER_FAMILY = "其他"
FAMILY_CLUSTER_MODES = ("off", "shadow", "live")
# live 期 vote 簇豁免 A6 复核的同意率门槛（影子期实测值；低于门槛则全簇复核）
VOTE_TRUST_MIN = 0.80
DEFAULT_JACCARD_MIN = 0.34

# glue 状态白名单：非 ok/degraded（no_candidates / S*_failed / replay_mode 等）时
# glue.families 不参与分歧检测（缺数据 ≠ 无分歧）
_GLUE_USABLE_STATUSES = ("ok", "degraded")

# A6 复核护栏（旁路调用不能拖死 Node 6）：单簇不重试、总预算超时或连续失败即熔断，
# 剩余簇标 error（不伪装成 skipped）——LLM 故障日必须显式体现在 a6_error 上。
REVIEW_CALL_TIMEOUT_S = 30.0
REVIEW_MAX_CONSECUTIVE_ERRORS = 3
DEFAULT_REVIEW_BUDGET_S = 600.0

# A6 复核拒绝口径（LLM 明确表达「无法归属」）
_REJECT_LABELS = {"其他", "无", "无法归属", "无法确定", "unknown", "none", "null", "n/a"}

_REVIEWED_STATUSES = ("shadow_only", "approved", "rejected")


# =============================================================================
# 开关层（仿 family_embed.resolve_mode：非法值一律保守缺省）
# =============================================================================

def enabled() -> bool:
    """总开关（缺省 0 = 不构建簇，分组路径逐字节回现状）。"""
    return os.getenv("GOT_FAMILY_CLUSTER_ENABLED", "0").strip().lower() in ("1", "true", "yes")


def resolve_review_mode(raw: Optional[str] = None) -> str:
    """A6 复核三态归一：off（纯规则层，零 LLM）/ shadow（离线抽样用，不回写）/ live（回写）。

    缺省 off：规则层（cluster_id 分组键）本身由历史回测结案，不需要每日 LLM 复核；
    shadow 只在做 A6 标签抽样核验时手工开。非法值回落 off。
    """
    value = os.getenv("GOT_FAMILY_CLUSTER_REVIEW", "off") if raw is None else raw
    mode = (value or "").strip().lower()
    if mode not in FAMILY_CLUSTER_MODES:
        logger.warning("[family_cluster] 非法 REVIEW=%r，回落 off", value)
        return "off"
    return mode


def resolve_review_budget_s(raw: Optional[str] = None) -> float:
    """单日复核总预算（秒）：超时即熔断剩余簇，防 LLM 故障把 Node 6 拖到小时级。"""
    value = (
        os.getenv("GOT_FAMILY_CLUSTER_REVIEW_BUDGET_S", str(DEFAULT_REVIEW_BUDGET_S))
        if raw is None else raw
    )
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        logger.warning("[family_cluster] REVIEW_BUDGET_S 解析失败（%r），回落 %.0fs",
                       value, DEFAULT_REVIEW_BUDGET_S)
        return DEFAULT_REVIEW_BUDGET_S


def resolve_jaccard_min(raw: Optional[str] = None) -> float:
    """簇继承的成员集 Jaccard 门槛（缺省 0.34，clamp 到 [0, 1]）。"""
    value = (
        os.getenv("GOT_FAMILY_CLUSTER_JACCARD_MIN", str(DEFAULT_JACCARD_MIN))
        if raw is None else raw
    )
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        logger.warning("[family_cluster] JACCARD_MIN 解析失败（%r），回落 %.2f", value, DEFAULT_JACCARD_MIN)
        return DEFAULT_JACCARD_MIN
    return min(1.0, max(0.0, parsed))


# =============================================================================
# 数据结构
# =============================================================================

@dataclass
class ClusterRecord:
    """单个 K 线簇的家族身份与复核结果（JSON 由 to_dict 投影）。"""
    cluster_id: str
    member_boards: list[str]
    family_label: Optional[str] = None
    # vote（词表簇内一致票）/ a6_review（LLM 裁决）/ cluster_id（未决议，键=簇 ID）
    label_source: str = "cluster_id"
    gate_eligible: bool = False
    # skipped / shadow_only / approved / rejected / error
    review_status: str = "skipped"
    vote_label: Optional[str] = None
    a6_label: Optional[str] = None
    evidence_ids: list[str] = field(default_factory=list)
    event_theme: Optional[str] = None
    would_rewrite: dict[str, str] = field(default_factory=dict)
    a6_reason: Optional[str] = None
    review_error: Optional[str] = None


# =============================================================================
# 纯函数：簇 ID / 继承 / 投票 / 键解析
# =============================================================================

def build_cluster_id(member_boards: list[str]) -> str:
    """成员集哈希 ID：对成员排序去重后 sha1 前 12 位（同日重跑确定）。"""
    key = "|".join(sorted({str(b) for b in (member_boards or []) if str(b).strip()}))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def jaccard_similarity(left: list[str], right: list[str]) -> float:
    """成员集 Jaccard；任一为空 → 0.0。"""
    a = {str(x) for x in (left or []) if str(x).strip()}
    b = {str(x) for x in (right or []) if str(x).strip()}
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def inherit_cluster_ids(
    members_list: list[list[str]],
    prev_clusters: Optional[list[dict]],
    jaccard_min: float,
) -> list[str]:
    """跨日继承：与前日簇逐一算 Jaccard，≥ 门槛取最高分者的 cluster_id。

    前日无 shadow 字段（含 2026-11-02 前全部历史日）→ 保留当日哈希 ID（链首）。
    多命中同分时按前日顺序取首个（确定性）。
    """
    prev = [
        (
            str(c.get("cluster_id") or ""),
            [str(m) for m in (c.get("member_boards") or [])],
        )
        for c in (prev_clusters or [])
        if isinstance(c, dict) and c.get("cluster_id")
    ]
    ids: list[str] = []
    for members in members_list:
        best_id, best_score = "", 0.0
        for prev_id, prev_members in prev:
            score = jaccard_similarity(members, prev_members)
            if score > best_score:
                best_id, best_score = prev_id, score
        ids.append(best_id if best_id and best_score >= jaccard_min else build_cluster_id(members))
    return ids


def majority_frame_group(
    members: list[str], frame_group_lookup: Optional[dict[str, str]],
) -> Optional[str]:
    """词表多数票：簇内非「其他」家族中出现次数最高者（并列 → 无票）。

    口径要点：
    - 「其他」成员不计票——它们正是词表漏报的对象，由同簇兄弟板块的命中来命名，
      这是把 47/93 落「其他」的板块拉回受控词表的主要路径；
    - 并列第一视为簇内误报/混合驱动信号，无票 → 键回 cluster_id（live 由 A6 裁决）；
    - 全簇都落「其他」→ 无票（不硬造家族，交 A6/簇键）。
    """
    counts: dict[str, int] = {}
    for name in (members or []):
        label = str((frame_group_lookup or {}).get(name) or "").strip()
        if label and label != OTHER_FAMILY:
            counts[label] = counts.get(label, 0) + 1
    if not counts:
        return None
    top = max(counts.values())
    winners = [label for label, count in counts.items() if count == top]
    return winners[0] if len(winners) == 1 else None


def majority_glue_family(
    members: list[str], glue_families: Optional[dict[str, str]],
) -> tuple[Optional[str], bool]:
    """glue 家族多数标签：返回 (标签, 是否平票)。

    平票视为分歧（保守升审）；无命中返回 (None, False)。
    """
    counts: dict[str, int] = {}
    for name in (members or []):
        label = (glue_families or {}).get(name)
        label = str(label).strip() if label else ""
        if label and label != OTHER_FAMILY:
            counts[label] = counts.get(label, 0) + 1
    if not counts:
        return None, False
    top = max(counts.values())
    winners = [label for label, n in counts.items() if n == top]
    return winners[0], len(winners) > 1


def resolve_endurance_key(
    board_name: str,
    *,
    frame_group: Optional[str] = None,
    glue_family: Optional[str] = None,
    clusters_by_board: Optional[dict[str, ClusterRecord]] = None,
    mode: str = "shadow",
) -> tuple[str, str]:
    """endurance 分组键解析（唯一裁决点，聚合器与 coverage 同源）。

    返回 (key, key_source)：
    - 无簇覆盖：glue.families → frame_group → sector_name（现状三级，逐字节不变）
    逐板块解析（off/shadow/live 同一原则）：已有具名键（glue 或 frame_group 非「其他」）的板块
    维持现状键；落「其他」/缺失的板块才用簇键——off/shadow 用 cluster_id，live 用 family_label
    （A6 复核后的受控家族名，复核拒绝则回落 cluster_id）。簇标签永不改判已具名板块。
    """
    baseline = glue_family or frame_group or board_name
    record = (clusters_by_board or {}).get(board_name)
    if record is None:
        return baseline, "baseline"
    named = bool(
        (glue_family and glue_family != OTHER_FAMILY)
        or (frame_group and frame_group != OTHER_FAMILY)
    )
    if mode == "live":
        # live 同样逐板块：已具名板块永不改判（A6 标签只补给落「其他」的成员），
        # 避免「东数西算/数据中心」这类簇被整簇标成 AI软件 后连带改族。
        if named:
            return baseline, "baseline"
        if record.family_label:
            return record.family_label, "cluster_label"
        return record.cluster_id, "cluster_id"
    # off/shadow：具名板块维持现状键（不让簇投票改判已命中的家族），
    # 只有落「其他」/缺失的板块换成簇 ID——既堵漏报又不制造误报。
    named = bool(
        (glue_family and glue_family != OTHER_FAMILY)
        or (frame_group and frame_group != OTHER_FAMILY)
    )
    if named:
        return baseline, "baseline"
    return record.cluster_id, "cluster_id"


# =============================================================================
# A6 复核（prompt / 解析 / 单簇裁决）
# =============================================================================

def _loads_json(text: str) -> Optional[dict]:
    """从 LLM 输出提取 JSON 对象；失败返回 None（不 raise）。"""
    content = (text or "").strip()
    if not content:
        return None
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
    if fenced:
        content = fenced.group(1).strip()
    try:
        obj = json.loads(content)
    except (TypeError, ValueError):
        brace = re.search(r"\{.*\}", content, re.DOTALL)
        if not brace:
            return None
        try:
            obj = json.loads(brace.group(0))
        except (TypeError, ValueError):
            return None
    return obj if isinstance(obj, dict) else None


def parse_review_label(raw: str) -> tuple[Optional[str], Optional[str]]:
    """LLM 输出 → (家族名, 理由)。白名单外/无法归属 → (None, 理由)。

    白名单 = `PROMPT_EXPOSED_FAMILIES`（华为生态/前沿科技刻意不入，与 glue 同源）。
    """
    obj = _loads_json(raw)
    if obj is None:
        return None, None
    family = str(obj.get("family") or "").strip()
    reason = str(obj.get("reason") or "").strip()[:80] or None
    if family in PROMPT_EXPOSED_FAMILIES:
        return family, reason
    if family.lower() in _REJECT_LABELS or family in _REJECT_LABELS:
        return None, reason
    # 白名单外的受控家族（盲区族）或未知字符串一律拒绝：不默认信任 LLM 扩权
    return None, reason


def build_review_prompt(
    members: list[str],
    metrics: Optional[dict[str, dict]] = None,
    evidence_index: Optional[dict[str, dict]] = None,
) -> str:
    """单簇复核 prompt：成员 + 近期涨跌/强度 + 催化事件证据（只作旁证）。"""
    whitelist = "、".join(PROMPT_EXPOSED_FAMILIES)
    lines = []
    for name in members:
        metric = (metrics or {}).get(name) or {}
        change = metric.get("change")
        strength = metric.get("strength")
        parts = []
        if isinstance(change, (int, float)):
            parts.append(f"今日涨跌 {change:+.2f}%")
        if isinstance(strength, (int, float)):
            parts.append(f"强度评分 {strength:.1f}")
        lines.append(f"- {name}" + (f"（{'，'.join(parts)}）" if parts else ""))
    evidence_lines = []
    for name in members:
        slot = (evidence_index or {}).get(name) or {}
        if not isinstance(slot, dict):
            continue
        ids = [str(x) for x in (slot.get("event_ids") or [])][:3]
        theme = str(slot.get("theme") or "").strip()
        if ids or theme:
            evidence_lines.append(f"- {name}: {theme or '（无主题）'} {','.join(ids)}")
    evidence_block = "\n".join(evidence_lines) if evidence_lines else "（无）"
    return (
        "你是 A 股板块家族分类审核员。下面这些板块来自同一 K 线相关性聚类（90 日收益相关，"
        "完全连锁聚类），需要判断它们共同归属的「主线家族」。\n\n"
        f"受控家族（只能从中选一个，或回答「其他」）：{whitelist}\n\n"
        "聚类成员：\n"
        + "\n".join(lines)
        + "\n\n相关催化事件（只作旁证证据，不得作为家族归属的唯一依据；事件主题不得直接当家族名）：\n"
        + evidence_block
        + "\n\n判定要求：\n"
        "1. 家族归属以成员的产业结构/驱动逻辑为准；跨驱动逻辑的混合簇回答「其他」；\n"
        "2. 无法确定时回答「其他」，不要强行归类；\n"
        "3. 只输出 JSON：{\"family\": \"家族名或其他\", \"confidence\": 0.0-1.0, \"reason\": \"≤40字\"}"
    )


def _glue_disagrees(
    record: ClusterRecord, glue_families: Optional[dict[str, str]], glue_status: str,
) -> bool:
    """glue-ok 日的零成本分歧检测：成员 glue 多数标签 ≠ vote 或平票 → 升审。"""
    if (glue_status or "").strip().lower() not in _GLUE_USABLE_STATUSES:
        return False
    if not glue_families or not record.vote_label:
        return False
    label, tie = majority_glue_family(record.member_boards, glue_families)
    if label is None:
        return False
    return tie or label != record.vote_label


def _needs_review(
    record: ClusterRecord,
    review_mode: str,
    vote_trust: bool,
    glue_families: Optional[dict[str, str]],
    glue_status: str,
) -> bool:
    """复核必要性：shadow 全审；live 纯「其他」簇必审，vote 簇按信任度 + glue 分歧。"""
    if review_mode != "live":
        return True
    if record.vote_label is None:
        return True
    if not vote_trust:
        return True
    return _glue_disagrees(record, glue_families, glue_status)


async def _review_cluster(
    record: ClusterRecord,
    *,
    review_mode: str,
    llm_call: Callable[..., Awaitable[str]],
    evidence_index: Optional[dict[str, dict]],
    board_metrics: Optional[dict[str, dict]],
) -> None:
    """单簇 A6 复核：失败只置 error，绝不 raise（Bailian 429/限额风险不进主链路）。"""
    prompt = build_review_prompt(record.member_boards, board_metrics, evidence_index)
    try:
        raw = await llm_call(
            prompt,
            temperature=0.0,
            max_tokens=300,
            max_retries=0,
            timeout=REVIEW_CALL_TIMEOUT_S,
            call_type="node6.family_cluster_review",
        )
    except Exception as exc:  # noqa: BLE001 — 复核旁路：降级为 error
        record.review_status = "error"
        record.review_error = str(exc)[:160]
        logger.warning("[family_cluster] 簇 %s 复核异常（降级）: %s", record.cluster_id, exc)
        return
    text = raw if isinstance(raw, str) else ""
    if not text.strip():
        record.review_status = "error"
        record.review_error = "empty_response"
        return
    label, reason = parse_review_label(text)
    record.a6_label = label
    record.a6_reason = reason
    if review_mode == "live":
        record.review_status = "approved" if label else "rejected"
    else:
        record.review_status = "shadow_only"


# =============================================================================
# 主流程
# =============================================================================

def _prev_shadow(prev_unified: Optional[dict]) -> Optional[dict]:
    """前日归档兼容：接受 unified.json 原样（含 family_cluster_shadow）或 shadow 本身。"""
    if not isinstance(prev_unified, dict):
        return None
    shadow = prev_unified.get("family_cluster_shadow")
    if isinstance(shadow, dict):
        return shadow
    if "clusters" in prev_unified:
        return prev_unified
    return None


def _vote_trust_from_prev(prev_unified: Optional[dict]) -> bool:
    """live 期 vote 簇豁免门槛：读前日 shadow 的 vote_a6_agree_rate。"""
    shadow = _prev_shadow(prev_unified) or {}
    stats = shadow.get("review_stats") or {}
    try:
        rate = float(stats.get("vote_a6_agree_rate"))
    except (TypeError, ValueError):
        return False
    return rate >= VOTE_TRUST_MIN


def _collect_evidence(
    members: list[str], evidence_index: Optional[dict[str, dict]],
) -> tuple[list[str], Optional[str]]:
    """簇级证据聚合（只作展示/复核输入，绝不作 key）。"""
    ids: list[str] = []
    theme: Optional[str] = None
    for name in members:
        slot = (evidence_index or {}).get(name)
        if isinstance(slot, dict):
            for eid in slot.get("event_ids") or []:
                text = str(eid).strip()
                if text and text not in ids:
                    ids.append(text)
            if theme is None and str(slot.get("theme") or "").strip():
                theme = str(slot.get("theme")).strip()
        elif isinstance(slot, (list, tuple)):
            for eid in slot:
                text = str(eid).strip()
                if text and text not in ids:
                    ids.append(text)
    return ids, theme


def finalize_records(
    records: list[ClusterRecord],
    *,
    mode: str,
    actionable_boards: Optional[set[str]] = None,
    frame_group_lookup: Optional[dict[str, str]] = None,
) -> None:
    """终值裁决 + would_rewrite 记录（不回写；回写由聚合器按 REVIEW=live 执行）。

    would_rewrite 只覆盖「当前落其他/无键」的 actionable 成员——不论标签来自 vote 还是 A6。
    K 线簇是**同涨同跌组**（38 成员簇里军贸/机器人/新能源/AI硬件混编很常见），任何单一簇标签
    对已具名成员都是越权改判（回测实测：AI眼镜 AI软件→军工、化工机械 周期资源→AI硬件）。
    簇标签只用于给词表漏报的成员补名；已具名成员由自身 frame_group 负责。
    """
    actionable = set(actionable_boards) if actionable_boards is not None else None
    unnamed_cache: dict[str, set[str]] = {}
    for record in records:
        if mode == "live":
            if record.a6_label:
                record.family_label, record.label_source = record.a6_label, "a6_review"
            elif record.review_status == "rejected":
                # A6 明确判「无归属」时不得回落到 vote：否则「其他」成员会被写成一个
                # 它已被复核否定的家族（审计实测：石油加工→新能源、DRG-DIP→AI软件）。
                record.family_label, record.label_source = None, "cluster_id"
            elif record.vote_label:
                record.family_label, record.label_source = record.vote_label, "vote"
            else:
                record.family_label, record.label_source = None, "cluster_id"
        else:
            if record.vote_label:
                record.family_label, record.label_source = record.vote_label, "vote"
            else:
                record.family_label, record.label_source = None, "cluster_id"
        record.gate_eligible = bool(
            record.family_label and record.family_label != OTHER_FAMILY
        )
        # shadow 期 would_rewrite 反映「若切 live 会改成什么」，故优先用 A6 标签
        rewrite_label = record.family_label if mode == "live" else (record.a6_label or record.vote_label)
        if rewrite_label and rewrite_label != OTHER_FAMILY:
            if record.cluster_id not in unnamed_cache:
                unnamed_cache[record.cluster_id] = {
                    board for board in record.member_boards
                    if str((frame_group_lookup or {}).get(board) or "").strip()
                    in ("", OTHER_FAMILY)
                }
            record.would_rewrite = {
                board: rewrite_label
                for board in unnamed_cache[record.cluster_id]
                if actionable is None or board in actionable
            }
        else:
            record.would_rewrite = {}


async def build_family_clusters(
    theme_families: list["ThemeFamily"],
    frame_group_lookup: dict[str, str],
    run_date: date,
    jaccard_min: float,
    review_mode: str,
    prev_unified: Optional[dict],
    glue_families: Optional[dict[str, str]],
    glue_status: str,
    evidence_index: Optional[dict[str, dict]],
    *,
    actionable_boards: Optional[list[str]] = None,
    board_metrics: Optional[dict[str, dict]] = None,
    llm_call: Optional[Callable[..., Awaitable[str]]] = None,
) -> list[ClusterRecord]:
    """把 Node 2 的 K 线簇抬成家族 identity，并按 REVIEW 分级产出标签候选。

    Args:
        theme_families: Node 2 产物（member_boards = K 线簇成员名，未经清洗，按原样哈希）。
        frame_group_lookup: SectorContext.frame_group_lookup（板块名 → 词表家族）。
        run_date: 运行日（仅留痕/日志；簇继承基准由 prev_unified 决定）。
        jaccard_min: 跨日成员集 Jaccard 继承门槛。
        review_mode: shadow / live。
        prev_unified: 前日归档 unified.json 原样（或 family_cluster_shadow 本身）；
            None = 链首，全部当日哈希 ID。
        glue_families: 板块名 → glue 家族（调用方由 board_code 归一而来）；
            glue_status 非 ok/degraded 时本模块视为空（缺数据 ≠ 无分歧）。
        glue_status: 当日 glue 产物状态。
        evidence_index: 板块名 → {"event_ids": [...], "theme": "..."}，只作复核输入/展示。
        actionable_boards: actionable 主线板块名（决定 would_rewrite 覆盖面；None=全成员）。
        board_metrics: 板块名 → {"change": 涨跌幅, "strength": 强度}，只喂 prompt。
        llm_call: 异步 LLM 调用（聚合器注入 `_call_llm_async`）；None = 不复核（replay）。

    Returns:
        ClusterRecord 列表；输入无簇时返回空列表。绝不 raise（内部逐簇降级）。
    """
    records: list[ClusterRecord] = []
    for family in theme_families or []:
        members: list[str] = []
        for raw in (getattr(family, "member_boards", None) or []):
            name = str(raw or "").strip()
            if name and name not in members:
                members.append(name)
        if not members:
            continue
        records.append(ClusterRecord(
            cluster_id=build_cluster_id(members),
            member_boards=members,
        ))
    if not records:
        return []

    prev_shadow = _prev_shadow(prev_unified)
    prev_clusters = (prev_shadow or {}).get("clusters") or []
    inherited = inherit_cluster_ids(
        [r.member_boards for r in records], prev_clusters, jaccard_min,
    )
    for record, cluster_id in zip(records, inherited):
        record.cluster_id = cluster_id

    vote_trust = _vote_trust_from_prev(prev_unified)
    deadline = time.monotonic() + resolve_review_budget_s()
    consecutive_errors = 0
    for record in records:
        record.vote_label = majority_frame_group(record.member_boards, frame_group_lookup)
        record.evidence_ids, record.event_theme = _collect_evidence(
            record.member_boards, evidence_index,
        )
        if llm_call is None or review_mode == "off":
            record.review_status = "skipped"
            continue
        if not _needs_review(record, review_mode, vote_trust, glue_families, glue_status):
            record.review_status = "skipped"
            continue
        # 熔断：连续失败或超总预算 → 剩余簇标 error（LLM 故障必须在 a6_error 上可见），
        # 不再发起调用；旁路复核不得把 Node 6 拖死。
        if consecutive_errors >= REVIEW_MAX_CONSECUTIVE_ERRORS:
            record.review_status = "error"
            record.review_error = "aborted_after_consecutive_errors"
            continue
        if time.monotonic() >= deadline:
            record.review_status = "error"
            record.review_error = "aborted_after_budget"
            continue
        await _review_cluster(
            record,
            review_mode=review_mode,
            llm_call=llm_call,
            evidence_index=evidence_index,
            board_metrics=board_metrics,
        )
        consecutive_errors = consecutive_errors + 1 if record.review_status == "error" else 0

    finalize_records(
        records, mode=review_mode, actionable_boards=actionable_boards,
        frame_group_lookup=frame_group_lookup,
    )

    logger.info(
        "[family_cluster] %s 簇 %d 个（vote %d / 复核 %d / A6 命中 %d / 异常 %d）",
        run_date, len(records),
        sum(1 for r in records if r.vote_label),
        sum(1 for r in records if r.review_status in _REVIEWED_STATUSES),
        sum(1 for r in records if r.a6_label),
        sum(1 for r in records if r.review_status == "error"),
    )
    return records


# =============================================================================
# 观测投影
# =============================================================================

def summarize_review(records: list[ClusterRecord]) -> dict:
    """复核统计（vote_a6_agree_rate 为 live 转正判据，分母=已复核的 vote 簇）。"""
    reviewed = [r for r in records if r.review_status in _REVIEWED_STATUSES]
    vote_reviewed = [r for r in reviewed if r.vote_label]
    agree = sum(1 for r in vote_reviewed if r.a6_label == r.vote_label)
    return {
        "total": len(records),
        "vote_clusters": sum(1 for r in records if r.vote_label),
        "a6_reviewed": len(reviewed),
        "a6_approved": sum(1 for r in records if r.a6_label),
        "a6_error": sum(1 for r in records if r.review_status == "error"),
        "vote_a6_agree_rate": round(agree / len(vote_reviewed), 3) if vote_reviewed else None,
    }


def compute_coverage(
    records: list[ClusterRecord],
    actionable_boards: Optional[list[str]],
    frame_group_lookup: Optional[dict[str, str]],
    glue_families: Optional[dict[str, str]],
    mode: str,
) -> tuple[dict, dict]:
    """覆盖读数：(coverage_before, coverage_after)。

    before = 现网 frame_group == 「其他」占比；after = 按 REVIEW 分级解析后的分组键口径。
    """
    boards = [str(b) for b in (actionable_boards or []) if str(b).strip()]
    if not boards:
        return {"其他_rate": None}, {"其他_rate": None, "cluster_id_rate": None}
    clusters_by_board = {
        board: record for record in records for board in record.member_boards
    }
    lookup = frame_group_lookup or {}
    before_other = sum(1 for b in boards if (lookup.get(b) or "") == OTHER_FAMILY)
    after_other = 0
    cluster_id_keys = 0
    for board in boards:
        key, source = resolve_endurance_key(
            board,
            frame_group=lookup.get(board),
            glue_family=(glue_families or {}).get(board),
            clusters_by_board=clusters_by_board,
            mode=mode,
        )
        if key == OTHER_FAMILY:
            after_other += 1
        if source == "cluster_id":
            cluster_id_keys += 1
    total = len(boards)
    return (
        {"其他_rate": round(before_other / total, 3)},
        {
            "其他_rate": round(after_other / total, 3),
            "cluster_id_rate": round(cluster_id_keys / total, 3),
        },
    )


def record_to_dict(record: ClusterRecord) -> dict:
    """ClusterRecord → JSON 投影（落 family_cluster_shadow.clusters）。"""
    payload = {
        "cluster_id": record.cluster_id,
        "member_boards": list(record.member_boards),
        "family_label": record.family_label,
        "label_source": record.label_source,
        "gate_eligible": record.gate_eligible,
        "review_status": record.review_status,
        "vote_label": record.vote_label,
        "a6_label": record.a6_label,
        "evidence_ids": list(record.evidence_ids),
        "event_theme": record.event_theme,
        "would_rewrite": dict(record.would_rewrite),
    }
    if record.a6_reason:
        payload["a6_reason"] = record.a6_reason
    if record.review_error:
        payload["review_error"] = record.review_error
    return payload


def build_shadow_payload(
    records: list[ClusterRecord],
    *,
    mode: str,
    jaccard_min: float,
    actionable_boards: Optional[list[str]],
    frame_group_lookup: Optional[dict[str, str]],
    glue_families: Optional[dict[str, str]],
    prev_unified: Optional[dict] = None,
) -> dict:
    """UnifiedContext.family_cluster_shadow 的完整投影（随 unified.json 落盘）。

    `vote_a6_agree_rate` 在当日没有已复核 vote 簇时**顺延前日读数**（source=carried）：
    live 期 vote 簇被信任豁免后当日必然无样本，不顺延会让门槛隔日失效（跳过→次日全审→再跳过），
    转正判据也就永远凑不齐连续 5 日。glue 分歧升审发生时仍有新样本，读数会被当日重算覆盖。
    """
    before, after = compute_coverage(
        records, actionable_boards, frame_group_lookup, glue_families, mode,
    )
    stats = summarize_review(records)
    if mode == "live" and stats["vote_a6_agree_rate"] is None:
        prev_stats = (_prev_shadow(prev_unified) or {}).get("review_stats") or {}
        try:
            carried = float(prev_stats.get("vote_a6_agree_rate"))
        except (TypeError, ValueError):
            carried = None
        if carried is not None:
            stats["vote_a6_agree_rate"] = round(carried, 3)
            stats["vote_a6_agree_rate_source"] = "carried"
    if "vote_a6_agree_rate_source" not in stats:
        stats["vote_a6_agree_rate_source"] = (
            "today" if stats["vote_a6_agree_rate"] is not None else None
        )
    return {
        "enabled": True,
        "mode": mode,
        "jaccard_min": jaccard_min,
        "clusters": [record_to_dict(r) for r in records],
        "review_stats": stats,
        "coverage_before": before,
        "coverage_after": after,
    }


__all__ = [
    "OTHER_FAMILY",
    "VOTE_TRUST_MIN",
    "ClusterRecord",
    "build_family_clusters",
    "build_review_prompt",
    "build_shadow_payload",
    "compute_coverage",
    "enabled",
    "finalize_records",
    "inherit_cluster_ids",
    "jaccard_similarity",
    "majority_frame_group",
    "majority_glue_family",
    "parse_review_label",
    "record_to_dict",
    "resolve_endurance_key",
    "resolve_jaccard_min",
    "resolve_review_budget_s",
    "resolve_review_mode",
    "summarize_review",
]
