#!/usr/bin/env python
"""B4-1 前置 · 事件锚重建（日历事件 → 880 板块码），离线只读诊断脚本。

来源：docs/got/plans/入场分道与持仓延续性_总计划_20260930.md §C-B4 落地改法第 1 条
「锚重建前置（阻塞 B4-1）」，以及 §2.4「事件锚口径回放失败（43 信号 34 无锚，
根因：日历 `affected_sectors` ↔ 880 板名匹配落空）」。

职责：把每一天的催化剂日历事件（生产当日所见）重建为 **事件 → 880 板块码** 的锚定表，
并对给定信号清单测出「事件锚覆盖率」与无锚归因，为 B4-1（事件对齐窗口评价口径）
提供可用的 join 键。产物 `data/event_anchor_map.json`（`data/` 已 gitignore，脚本入库、
产物不入库，与 `optimal_stop_priors.json` 同惯例）。

**必须复用生产侧同一套匹配件**（计划原文：禁止另写简化版），本脚本不做任何自研匹配：
- relations 板码直连：`src.got.schemas.SemanticGlueResult` + `AggregatorNode._build_relations_index`
  （连 `direction=="negative"` 不补挂、事件回连当日日历取不到即丢 这两条生产护栏都原样继承）
- 板块名 → 880 码：`CatalystRealizationTracker._get_board_code`
  （P6 三级解析：registry 反向精确 → `board_aliases.normalize_sector_name`+别名变体 → difflib 0.68）
- 名 ↔ 名受控匹配：`AggregatorNode._match_sector(min_substr_len=3)`
  （归一化精确 → 别名变体 → 受控子串；`generic`/`narrow` 一律不采信，同 `_lookup_flow_map` 口径）
- 家族同族：`src.got.nodes.sector_analysis._build_frame_group`（FAMILY_KEYWORDS 单一权威源）——
  日历里的「算力/光模块/HBM概念」这类概念词在 Node2 宇宙无同名 880 板，只能落到同族板；
  证据强度最低，工件里单独打 `family_match` 来源并参与排序 tie-break（硬锚优先）

Point-in-time 红线（架构计划 §7 专项）：
- 只用 `--as-of` 之前（含）的归档日；事件取**信号日当天**的 `news.json` 催化剂日历
- 板块码↔名对照表按日**累计**（epoch 递增），解析 D 日事件时只用 ≤D 所见过的板块名，
  不引入未来新板名
- 「窗=0 日的已落地事件不计作前瞻锚」：`event_date == 信号日` 与 `major_events`（当日爆发）
  一律归无锚桶，只计数不当前瞻锚

用法：
    python scripts/rebuild_event_anchor_map.py [--as-of YYYY-MM-DD]
        [--signals .review_tmp/bt_freeze_replay_results.json] [--out data/event_anchor_map.json]
        [--horizon-trading-days 20] [--max-fanout 5] [--print-signals]

本脚本只读归档、只写一个 JSON 工件：不碰 DB、不碰生产常量、不碰在线决策。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

logger = logging.getLogger("rebuild_event_anchor_map")

ARCHIVE_DIR = ROOT_DIR / "data" / "got_daily"
DEFAULT_OUT = ROOT_DIR / "data" / "event_anchor_map.json"
DEFAULT_SIGNALS = ROOT_DIR / ".review_tmp" / "bt_freeze_replay_results.json"

DAY_RE = re.compile(r"^\d{8}$")
UNKNOWN = "unknown"
# _match_sector 的这两类命中在生产侧同样被拒（_lookup_flow_map：排除 generic/narrow）
_UNTRUSTED_MATCH = ("generic", "narrow")


def _to_date(day: str) -> date:
    return datetime.strptime(day, "%Y%m%d").date()


# ─────────────────────────────────────────────────────────────
# 1) 生产匹配件装载（缺件即硬失败，不允许降级为简化版）
# ─────────────────────────────────────────────────────────────

class ProductionMatchers:
    """生产匹配三件套的薄封装（本身不含任何匹配逻辑）。"""

    def __init__(self) -> None:
        from src.got.catalyst_realization import CatalystRealizationTracker
        from src.got.nodes.aggregator import AggregatorNode
        from src.got.nodes.sector_analysis import _build_frame_group
        from src.got.schemas import NewsContext, SemanticGlueResult

        self.match_sector = AggregatorNode._match_sector
        self.build_relations_index = AggregatorNode._build_relations_index
        self.frame_group = _build_frame_group  # 家族词表分族（FAMILY_KEYWORDS 单一权威源）
        self.news_context = NewsContext
        self.glue_result = SemanticGlueResult
        # `_get_board_code` 只依赖 classmethod `_build_name_to_code_map`（进程内缓存），
        # 跳过 `__init__` 以避免离线脚本建立 DB 连接——不复制实现，仍走原方法。
        self._tracker = object.__new__(CatalystRealizationTracker)

    def board_code(self, sector_name: str) -> tuple[Optional[str], str]:
        return self._tracker._get_board_code(sector_name)

    def registry_size(self) -> int:
        return len(self._tracker._build_name_to_code_map())


# ─────────────────────────────────────────────────────────────
# 2) 归档读取
# ─────────────────────────────────────────────────────────────

def list_archive_days(archive_dir: Path, as_of: date) -> list[str]:
    """合规归档日 = 目录名严格 8 位数字且 ≤ as_of。

    必须排除 `20260805_backup` / `20260817_pre_fix` / `_cache` 这类快照目录
    （`data/got_daily` 实测存在），否则同日事件被重复计入。
    """
    days: list[str] = []
    if not archive_dir.is_dir():
        return days
    for p in sorted(archive_dir.iterdir()):
        if not p.is_dir() or not DAY_RE.match(p.name):
            continue
        if _to_date(p.name) > as_of:
            continue
        if (p / "news.json").exists() or (p / "sector.json").exists():
            days.append(p.name)
    return days


def read_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # 归档坏件：诚实跳过并由调用方计数
        logger.warning("读取失败 %s: %s", path, exc)
        return None


def collect_board_code_names(obj: Any, out: dict[str, str]) -> dict[str, str]:
    """递归收集归档里的 {板块码: 板块名}。

    必须递归：`sector.json.active_mainline` 顶层是家族分组、板块藏在 `members[]`
    （GATE-2 同款口径坑），只平铺遍历会丢一批板码。
    """
    if isinstance(obj, dict):
        code = obj.get("code") or obj.get("sector_code")
        name = obj.get("name") or obj.get("sector_name")
        if isinstance(code, str) and code.strip() and isinstance(name, str) and name.strip():
            out.setdefault(code.strip(), name.strip())
        for value in obj.values():
            collect_board_code_names(value, out)
    elif isinstance(obj, list):
        for value in obj:
            collect_board_code_names(value, out)
    return out


def load_day_events(day_dir: Path, m: ProductionMatchers) -> tuple[list[dict], str]:
    """当日催化剂日历（生产所见）。返回 (事件列表, 状态标签)。"""
    raw = read_json(day_dir / "news.json")
    if raw is None:
        return [], "no_news_file"
    try:
        ctx = m.news_context(**raw)
    except Exception as exc:
        logger.debug("news.json 反序列化失败 %s: %s", day_dir, exc)
        return [], "news_bad"
    cal = ctx.catalyst_calendar
    if not cal:
        return [], "no_calendar"
    events = []
    for e in cal.events:
        events.append({
            "event_id": str(e.event_id),
            "event_date": e.event_date,
            "sector": str(e.sector or ""),
            "affected_sectors": [str(x) for x in (e.affected_sectors or []) if x],
            "category_tag": str(e.category_tag or ""),
            "event_type": str(e.event_type or ""),
            "impact_level": int(e.impact_level or 0),
            "direction": str(e.direction or ""),
            "confidence": float(e.confidence if e.confidence is not None else 0.0),
            "is_global": bool(e.is_global),
            "title": str(e.title or ""),
        })
    return events, str(cal.status or "normal")


def load_day_relation_index(day_dir: Path, m: ProductionMatchers) -> dict[str, list[dict]]:
    """当日 glue → {board_code: [{event_id, direction}]}（生产函数，原样护栏）。"""
    raw = read_json(day_dir / "agent_glue.json")
    if raw is None:
        return {}
    try:
        glue = m.glue_result(**raw)
    except Exception as exc:
        logger.debug("agent_glue.json 反序列化失败 %s: %s", day_dir, exc)
        return {}
    try:
        return m.build_relations_index(glue)
    except Exception as exc:
        logger.debug("relations 索引构建失败 %s: %s", day_dir, exc)
        return {}


# ─────────────────────────────────────────────────────────────
# 3) 事件 → 板块码锚定
# ─────────────────────────────────────────────────────────────

def invert_relations_index(rel_index: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """{board_code: relations} → {event_id: [{board_code, direction}]}（不改变护栏语义）。"""
    by_event: dict[str, list[dict]] = {}
    for code, rels in (rel_index or {}).items():
        for rel in rels:
            eid = str(rel.get("event_id") or "")
            if not eid:
                continue
            by_event.setdefault(eid, []).append({
                "board_code": str(code),
                "direction": str(rel.get("direction") or "positive"),
            })
    return by_event


def resolve_name_to_codes(
    name: str,
    universe: dict[str, str],
    matchers: ProductionMatchers,
) -> list[tuple[str, str]]:
    """事件板块名 → 归档板块码：判定完全交给生产 `_match_sector`。

    generic/narrow（泛化词根 / 目标更窄）一律不采信，与 `AggregatorNode._lookup_flow_map`
    拒绝这两类命中的口径一致。
    """
    if not name:
        return []
    hits: list[tuple[str, str]] = []
    for code, uname in universe.items():
        matched, match_type = matchers.match_sector(name, uname, min_substr_len=3)
        if matched and match_type not in _UNTRUSTED_MATCH:
            hits.append((code, match_type))
    return hits


# 证据强度序：relations 板码直连 > registry 板码解析 > 名受控匹配 > 家族同族
RANK_RELATIONS = 0
RANK_BOARD_CODE = 1
RANK_NAME_MATCH = 2
RANK_FAMILY = 3
RANK_LABEL = {
    RANK_RELATIONS: "relations",
    RANK_BOARD_CODE: "board_code",
    RANK_NAME_MATCH: "name_match",
    RANK_FAMILY: "family_match",
    9: "unknown",
}


def source_rank(sources) -> int:
    """事件级最强来源等级（0 最强）；注意它不等于「某个板码的证据强度」，
    逐板码定级必须用 `anchor_event` 产出的 `code_rank`。"""
    order = {"relations": RANK_RELATIONS, "board_code": RANK_BOARD_CODE,
             "name_match": RANK_NAME_MATCH, "family_match": RANK_FAMILY}
    rank = 9
    for name, value in order.items():
        if any(str(s).startswith(name) for s in sources):
            rank = min(rank, value)
    return rank


def anchor_event(
    event: dict,
    rels_by_event: dict[str, list[dict]],
    universe: dict[str, str],
    matchers: ProductionMatchers,
    name_cache: dict[str, list[tuple[str, str]]],
    max_fanout: int,
    family_by_code: Optional[dict[str, str]] = None,
) -> dict:
    """单事件 → {板块码: 证据等级} 锚表。

    证据等级按【码】粒度记（`code_rank`）：一个事件常常同时有「黄金走板码精确、
    同族其他板走家族层」，只记事件级来源会把家族弱锚误报成硬锚。
    """
    code_rank: dict[str, int] = {}
    sources: set[str] = set()

    def add(code: str, rank: int, tag: str) -> None:
        if not code:
            return
        prev = code_rank.get(code, 9)
        if rank < prev:
            code_rank[code] = rank
        sources.add(tag)

    # ① relations 板码直连（最强证据）；生产护栏：利空线不补挂
    for rel in rels_by_event.get(event["event_id"], []):
        if rel["direction"] == "negative":
            continue
        add(rel["board_code"], RANK_RELATIONS, "relations")

    raw_names: list[str] = []
    for n in [event["sector"]] + list(event["affected_sectors"]):
        n = (n or "").strip()
        if n and n not in raw_names:
            raw_names.append(n)

    ambiguous: list[str] = []
    event_families: list[str] = []
    for n in raw_names:
        # ② 板块名 → 880 码（P6 三级解析，走生产 `_get_board_code`）
        code, source = matchers.board_code(n)
        if code:
            add(code, RANK_BOARD_CODE, f"board_code:{source}")
        # ③ 名 ↔ 名受控匹配（对当日累计板块宇宙）
        if n not in name_cache:
            name_cache[n] = resolve_name_to_codes(n, universe, matchers)
        hits = name_cache[n]
        exact_alias = [c for c, mt in hits if mt in ("exact", "alias")]
        picked = exact_alias if len({c for c, _ in hits}) > max_fanout else [c for c, _ in hits]
        if len({c for c, _ in hits}) > max_fanout:
            ambiguous.append(n)
        for c in picked:
            add(c, RANK_NAME_MATCH, "name_match")
        # ④ 家族层：事件名 → 家族 → 同族板块码（日历板名常常是「算力/光模块」这类概念词，
        #    Node2 宇宙里没有同名 880 板，只有同族板；证据强度最低，消费方需分层）
        if family_by_code is not None:
            fam = matchers.frame_group(n)
            if fam and fam != "其他":
                fam_codes = {c for c, f in family_by_code.items() if f == fam}
                if fam_codes:
                    event_families.append(fam)
                    for c in fam_codes:
                        add(c, RANK_FAMILY, "family_match")

    return {
        "event_id": event["event_id"],
        "event_date": event["event_date"],
        "sector": event["sector"],
        "affected_sectors": event["affected_sectors"],
        "category_tag": event["category_tag"],
        "event_type": event["event_type"],
        "impact_level": event["impact_level"],
        "direction": event["direction"],
        "title": event["title"][:60],
        "code_rank": code_rank,
        "n_codes": len(code_rank),
        "sources": sorted(sources),
        "source_rank": source_rank(sources),
        "families": sorted(set(event_families)),
        "n_names": len(raw_names),
        "ambiguous_names": ambiguous,
    }


def build_family_by_code(
    universe: dict[str, str],
    matchers: ProductionMatchers,
    cache: dict[str, str],
) -> dict[str, str]:
    """{板块码: 家族}，分族交给生产 `_build_frame_group`（与两侧同词表、同口径）。"""
    out: dict[str, str] = {}
    for code, name in universe.items():
        if name not in cache:
            cache[name] = matchers.frame_group(name)
        out[code] = cache[name]
    return out


def build_day_anchors(
    events: list[dict],
    rel_index: dict[str, list[dict]],
    universe: dict[str, str],
    matchers: ProductionMatchers,
    max_fanout: int,
    family_by_code: Optional[dict[str, str]] = None,
) -> tuple[list[dict], list[str]]:
    calendar_ids = {e["event_id"] for e in events}
    rels_by_event = invert_relations_index(rel_index)
    # 生产护栏「只信当日日历事件原值」：relation 回连 events 取不到即丢
    orphans = sorted(set(rels_by_event) - calendar_ids)
    name_cache: dict[str, list[tuple[str, str]]] = {}
    anchors = [
        anchor_event(e, rels_by_event, universe, matchers, name_cache, max_fanout, family_by_code)
        for e in events
    ]
    return anchors, orphans


# ─────────────────────────────────────────────────────────────
# 4) 信号 → 前瞻锚
# ─────────────────────────────────────────────────────────────

def pick_forward_anchor(
    anchors: list[dict],
    signal_day: str,
    trading_days: list[str],
    horizon_td: int,
    total_day_events: int = 0,
    signal_code: str = "",
) -> dict:
    """前瞻锚选取：窗=0 的已落地事件不作前瞻锚。

    同窗长内按【该板码的证据强度】`code_rank[signal_code]` → impact_level → 日期 →
    event_id 定序（硬锚优先于家族弱锚），结果确定性。
    """
    if not anchors:
        reason = "no_calendar_events_today" if total_day_events == 0 else "no_covering_event_for_board"
        return {"anchor": None, "reason": reason, "n_candidates": 0, "n_forward_hard": 0}

    def day_index(d: date) -> Optional[int]:
        s = d.strftime("%Y%m%d")
        for i, x in enumerate(trading_days):
            if x >= s:
                return i
        return None

    sig_idx = day_index(_to_date(signal_day))
    if sig_idx is None:
        sig_idx = len(trading_days) - 1

    forward, landed_today, beyond, past_end = [], 0, 0, 0
    for a in anchors:
        ed = a["event_date"]
        if ed <= _to_date(signal_day):
            if ed == _to_date(signal_day):
                landed_today += 1
            continue
        idx = day_index(ed)
        if idx is None:
            past_end += 1  # 事件日超出归档末尾，窗长不可测（右删失）
            continue
        window = idx - sig_idx
        if window <= 0:
            landed_today += 1
            continue
        if window > horizon_td:
            beyond += 1
            continue
        forward.append({
            **a,
            "window_td": window,
            "anchor_rank": (a.get("code_rank") or {}).get(signal_code, a.get("source_rank", 9)),
        })

    n_forward_hard = sum(1 for a in forward if a["anchor_rank"] <= RANK_NAME_MATCH)
    if forward:
        best = min(
            forward,
            key=lambda a: (
                a["window_td"],
                a.get("anchor_rank", 9),
                -a["impact_level"],
                a["event_date"].isoformat(),
                a["event_id"],
            ),
        )
        return {"anchor": best, "reason": "ok", "n_candidates": len(forward),
                "n_forward_hard": n_forward_hard}
    if landed_today:
        return {"anchor": None, "reason": "only_landed_today", "n_candidates": landed_today,
                "n_forward_hard": 0}
    if past_end:
        return {"anchor": None, "reason": "event_beyond_archive_end", "n_candidates": past_end,
                "n_forward_hard": 0}
    if beyond:
        return {"anchor": None, "reason": "beyond_horizon", "n_candidates": beyond,
                "n_forward_hard": 0}
    return {"anchor": None, "reason": "no_forward_event_for_board", "n_candidates": 0,
            "n_forward_hard": 0}


def naive_anchor_hit(anchors: list[dict], signal_day: str, signal_name: str, strict: bool = False) -> bool:
    """旧口径基线（`.review_tmp/bt_event_aligned.py::find_anchor` 的裸子串规则），仅作对照。

    与旧脚本一致：`name in s or s in name` 双向裸子串，且**含** event_date == 信号日。
    `strict=True` 则只算严格晚于信号日的（与新口径可比的 apples-to-apples 基线）。
    """
    sig = _to_date(signal_day)
    for a in anchors:
        if a["event_date"] < sig or (strict and a["event_date"] == sig):
            continue
        for s in ([a["sector"]] + list(a["affected_sectors"])):
            s = (s or "").strip()
            if signal_name and (signal_name in s or s in signal_name):
                return True
    return False


def first_signals(rows: list[dict]) -> list[dict]:
    """去重后的首次信号：按 (板块码, 分组) 取最早日期，剔除 control 组。

    与 `.review_tmp/bt_event_aligned.py` 同口径（不重复计数同一板的连续放行信号）。
    """
    seen: set[tuple] = set()
    out: list[dict] = []
    for r in sorted(rows, key=lambda z: (str(z.get("code")), str(z.get("date")))):
        key = (r.get("code"), r.get("grp"))
        if r.get("grp") == "control" or key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ─────────────────────────────────────────────────────────────
# 5) 汇总
# ─────────────────────────────────────────────────────────────

def summarize(signals: list[dict]) -> dict:
    from collections import Counter

    anchored = [s for s in signals if s.get("anchor")]
    strong = [s for s in anchored if s["anchor"].get("anchor_rank", 9) <= RANK_NAME_MATCH]
    family_only = [s for s in anchored if s["anchor"].get("anchor_rank", 9) == RANK_FAMILY]
    hard_available = sum(1 for s in signals if s.get("n_forward_hard"))
    reasons = Counter(s["no_anchor_reason"] for s in signals if not s.get("anchor"))
    srcs: Counter = Counter()
    for s in anchored:
        for src in s["anchor"]["sources"]:
            srcs[src] += 1
    windows = [s["anchor"]["window_td"] for s in anchored]
    ranks = Counter(s["anchor"].get("anchor_rank", 9) for s in anchored)
    n = len(signals)
    return {
        "signals_total": n,
        "anchored": len(anchored),
        "coverage_pct": round(len(anchored) / n * 100, 1) if n else 0.0,
        # 分层覆盖率：只有板码/名/relations 直接命中才算「硬锚定」，家族同族算弱锚
        "anchored_strong": len(strong),
        "coverage_strong_pct": round(len(strong) / n * 100, 1) if n else 0.0,
        "anchored_family_only": len(family_only),
        "coverage_family_only_pct": round(len(family_only) / n * 100, 1) if n else 0.0,
        # 硬锚上限：只要同板前瞻候选里存在 rank≤2 就算（实际选中的可能因窗长更短是弱锚）
        "hard_anchor_available": hard_available,
        "coverage_hard_available_pct": round(hard_available / n * 100, 1) if n else 0.0,
        "naive_baseline_anchored": sum(1 for s in signals if s.get("naive_hit")),
        "naive_baseline_anchored_strict": sum(1 for s in signals if s.get("naive_hit_strict")),
        "board_covered_any_day": sum(1 for s in signals if s.get("board_covered_any_day")),
        "board_covered_strong_any_day": sum(
            1 for s in signals if s.get("board_covered_strong_any_day")
        ),
        "by_anchor_source": dict(srcs),
        "by_anchor_rank": {RANK_LABEL.get(k, str(k)): v for k, v in sorted(ranks.items())},
        "no_anchor_reasons": dict(reasons.most_common()),
        "window_td": {
            "n": len(windows),
            "p50": statistics.median(windows) if windows else None,
            "mean": round(statistics.fmean(windows), 2) if windows else None,
            "max": max(windows) if windows else None,
        },
        "category_tag_counts": dict(Counter(s["anchor"]["category_tag"] or UNKNOWN for s in anchored)),
    }


def build_artifact(args, matchers, days, signals, per_day_anchors, forensics) -> dict:
    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "as_of": args.as_of.isoformat(),
            "archive_days": len(days),
            "first_archive_day": days[0] if days else None,
            "last_archive_day": days[-1] if days else None,
            "signals_source": str(args.signals),
            "matchers": {
                "relations": "AggregatorNode._build_relations_index（含 negative 不补挂 / 孤儿 event_id 丢弃）",
                "name_to_code": "CatalystRealizationTracker._get_board_code（registry + board_aliases + difflib 0.68）",
                "name_to_name": "AggregatorNode._match_sector(min_substr_len=3)，generic/narrow 不采信",
                "family": "sector_analysis._build_frame_group（FAMILY_KEYWORDS，最弱层，可 --no-family-layer 消融）",
            },
            "params": {
                "horizon_trading_days": args.horizon_trading_days,
                "max_fanout": args.max_fanout,
                "family_layer": "off" if args.no_family_layer else "on",
            },
            "board_universe_size": forensics["board_universe_size"],
            "tdx_registry_size": forensics["tdx_registry_size"],
        },
        "coverage": summarize(signals),
        "signals": [json.loads(json.dumps(s, default=str)) for s in signals],
        "event_anchor_map": per_day_anchors,
        "forensics": forensics,
        "consumer_contract": {
            "join_key": "(signals[].date, signals[].code)；B4-1 评价窗 = [信号日, 锚 event_date + 宽限]",
            "window_semantics": "window_td 用归档日序列计交易日，锚一律严格晚于信号日（窗=0 已落地事件不作前瞻锚）",
            "anchor_rank": "0=relations 板码直连 1=registry 板码解析 2=名受控匹配 3=家族同族（弱）；"
                           "硬结论只认 rank≤2，rank=3 必须单独分桶",
            "tie_break": "同板多候选先按 window_td 最短、再按该板码 anchor_rank 最强；"
                         "`n_forward_hard>0` 表示该信号存在硬锚候选（coverage.hard_anchor_available 上限口径）",
            "event_anchor_map": "逐事件只存 code_rank{板码:rank}，不再存平铺 codes 列表",
            "excess_required": "收益必须减基准（【总计划】§1.1），本工件不含收益列",
        },
        "caveats": [
            "覆盖率只说明「事件能否锚到板块」，不说明事件对该板块有因果强度；"
            "relations/name_match 都可能带来弱关联，消费方需按 impact_level 与来源分层",
            "板块宇宙来自归档 sector.json（≤ 信号日累计），未被 Node2 收录的板名无法锚定 → "
            "no_forward_event_for_board 桶里混有「日历根本没覆盖该板」与「板名不在宇宙」两种成因",
            "glue（agent_glue.json）归档在早期日（如 20260902/0903/0908）缺失 → 这些日子只能靠名匹配",
            "relations 的 event_id 与当日日历不闭合（部分日期孤儿 id 占比高），"
            "按生产护栏必须丢弃，故 relations 层的实际补挂量受此上限约束（见 forensics）",
            "max_fanout 截断：一个事件名命中过多板块时只保留 exact/alias，避免泛化名把锚撒满全场",
            "family_match（家族同族）是弱锚：同族不等于同驱动（如 AI硬件族里芯片与 PCB 可反向），"
            "消费方（B4-1）按 source_rank 分层取用，硬结论只认 rank≤2",
            "event_beyond_archive_end：事件日超出最后一个归档日，窗长不可测（右删失），"
            "靠近 as_of 的信号命中占比偏高，需 as_of 留出前瞻窗",
        ],
    }


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="B4-1 前置：事件锚重建（日历事件 → 880 板块码）")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD，缺省今日")
    p.add_argument("--archive-dir", default=str(ARCHIVE_DIR))
    p.add_argument("--signals", default=str(DEFAULT_SIGNALS), help="回放信号 JSON（含 date/code/name/grp）")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--horizon-trading-days", type=int, default=20, help="前瞻锚的最大窗长（交易日）")
    p.add_argument("--max-fanout", type=int, default=5, help="事件名命中板块数上限，超则只保留 exact/alias")
    p.add_argument("--no-family-layer", action="store_true",
                   help="关闭家族同族层（消融用，看该层对覆盖率的真实贡献）")
    p.add_argument("--print-signals", action="store_true", help="逐条打印信号锚定明细")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    args.as_of = _to_date(args.as_of.replace("-", "")) if args.as_of else date.today()

    try:
        matchers = ProductionMatchers()
    except Exception as exc:
        print(f"FATAL: 生产匹配件装载失败（本脚本禁止降级为简化匹配）: {type(exc).__name__}: {exc}")
        return 2

    days = list_archive_days(Path(args.archive_dir), args.as_of)
    if not days:
        print(f"FATAL: 归档目录无合规日: {args.archive_dir} (as_of={args.as_of})")
        return 2

    rows = read_json(Path(args.signals))
    if not isinstance(rows, list) or not rows:
        print(f"FATAL: 信号文件不可用: {args.signals}")
        return 2
    signals_in = first_signals(rows)
    print(f"归档日 {len(days)} 个（{days[0]}→{days[-1]}），首次信号 {len(signals_in)} 条")

    universe: dict[str, str] = {}
    per_day_anchors: dict[str, list[dict]] = {}
    sig_by_day: dict[str, list[dict]] = {}
    for s in signals_in:
        sig_by_day.setdefault(str(s["date"]), []).append(s)

    forensics = {
        "days_without_relations": [],
        "calendar_status_counts": {},
        "relations_orphan_event_ids": {},
        "ambiguous_names_total": 0,
        "anchorless_events_total": 0,
        "events_total": 0,
        "family_layer": "off" if args.no_family_layer else "on",
    }
    family_cache: dict[str, str] = {}
    covered_codes: set[str] = set()
    covered_strong_codes: set[str] = set()
    results: list[dict] = []
    for day in days:
        day_dir = Path(args.archive_dir) / day
        sector_data = read_json(day_dir / "sector.json")
        if sector_data is not None:
            collect_board_code_names(sector_data, universe)  # 按日累计（PIT：只喂 ≤ 当日见过的名）
        events, cal_status = load_day_events(day_dir, matchers)
        forensics["calendar_status_counts"][cal_status] = forensics["calendar_status_counts"].get(cal_status, 0) + 1
        rel_index = load_day_relation_index(day_dir, matchers)
        family_by_code = None if args.no_family_layer else build_family_by_code(universe, matchers, family_cache)

        anchors, orphans = build_day_anchors(
            events, rel_index, universe, matchers, args.max_fanout, family_by_code
        )
        anchors = [a for a in anchors if a["event_date"] >= _to_date(day)]  # 过期事件不再作锚源
        forensics["events_total"] += len(anchors)
        forensics["anchorless_events_total"] += sum(1 for a in anchors if not a["code_rank"])
        forensics["ambiguous_names_total"] += sum(len(a["ambiguous_names"]) for a in anchors)
        if orphans:
            forensics["relations_orphan_event_ids"][day] = len(orphans)
        if not rel_index:
            forensics["days_without_relations"].append(day)
        for a in anchors:
            for code, rank in a["code_rank"].items():
                covered_codes.add(code)
                if rank <= RANK_NAME_MATCH:
                    covered_strong_codes.add(code)
        per_day_anchors[day] = [
            {**a, "event_date": a["event_date"].isoformat()} for a in anchors
        ]

        for s in sig_by_day.get(day, []):
            code = str(s.get("code") or "")
            hit = [a for a in anchors if code and code in a["code_rank"]]
            picked = pick_forward_anchor(
                hit, day, days, args.horizon_trading_days, len(anchors), code
            )
            anchor = picked["anchor"]
            results.append({
                "date": day,
                "code": s.get("code"),
                "name": s.get("name"),
                "grp": s.get("grp"),
                "anchor": ({**anchor, "event_date": anchor["event_date"].isoformat()} if anchor else None),
                "no_anchor_reason": picked["reason"],
                "n_matching_events": picked["n_candidates"],
                # 同板前瞻候选里存在硬锚（rank≤2）的条数：窗长 tie-break 可能选中更近弱锚，
                # 这个数才是「硬锚可覆盖上限」的真实读数
                "n_forward_hard": picked["n_forward_hard"],
                "naive_hit": naive_anchor_hit(anchors, day, str(s.get("name") or "")),
                "naive_hit_strict": naive_anchor_hit(anchors, day, str(s.get("name") or ""), strict=True),
            })

    forensics["board_universe_size"] = len(universe)
    forensics["tdx_registry_size"] = matchers.registry_size()
    forensics["signal_days_without_archive"] = sorted(
        {str(s["date"]) for s in signals_in} - set(days)
    )
    done_days = {r["date"] for r in results}
    for s in signals_in:
        if str(s["date"]) in done_days:
            continue
        results.append({
            "date": str(s["date"]),
            "code": s.get("code"),
            "name": s.get("name"),
            "grp": s.get("grp"),
            "anchor": None,
            "no_anchor_reason": "signal_day_not_in_archive",
            "n_matching_events": 0,
            "n_forward_hard": 0,
            "naive_hit": False,
            "naive_hit_strict": False,
            "board_covered_any_day": False,
            "board_covered_strong_any_day": False,
        })

    # 窗口内（任意归档日）曾被事件覆盖的板：用于区分「日历从不覆盖该板」与「仅当日无覆盖」
    for r in results:
        code = r.get("code")
        r.setdefault("board_covered_any_day", bool(code) and code in covered_codes)
        r.setdefault("board_covered_strong_any_day", bool(code) and code in covered_strong_codes)
    forensics["boards_covered_by_events"] = len(covered_codes)
    forensics["boards_covered_strong"] = len(covered_strong_codes)

    artifact = build_artifact(args, matchers, days, results, per_day_anchors, forensics)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    cov = artifact["coverage"]
    print(f"\n== 覆盖率（新口径 vs 旧裸子串基线）==")
    print(f"信号 {cov['signals_total']} | 有前瞻锚 {cov['anchored']} ({cov['coverage_pct']}%) "
          f"其中硬锚 {cov['anchored_strong']} ({cov['coverage_strong_pct']}%) / "
          f"仅家族同族弱锚 {cov['anchored_family_only']} | 硬锚候选存在 "
          f"{cov['hard_anchor_available']} ({cov['coverage_hard_available_pct']}%) | "
          f"旧基线 {cov['naive_baseline_anchored']}"
          f"（旧基线去掉窗=0 后 {cov['naive_baseline_anchored_strict']}）")
    print(f"窗口内曾被事件覆盖的信号板：{cov['board_covered_any_day']}/{cov['signals_total']}"
          f"（其中非家族层 {cov['board_covered_strong_any_day']}）")
    print(f"锚证据等级（逐板码）：{cov['by_anchor_rank']}")
    print(f"锚来源：{cov['by_anchor_source']}")
    print(f"无锚归因：{cov['no_anchor_reasons']}")
    print(f"锚窗长（交易日）：{cov['window_td']}  分型：{cov['category_tag_counts']}")
    print(f"取证：事件 {forensics['events_total']} 条（零锚 {forensics['anchorless_events_total']}）、"
          f"板块宇宙 {forensics['board_universe_size']} 码、registry {forensics['tdx_registry_size']} 码、"
          f"无 relations 的归档日 {len(forensics['days_without_relations'])} 个"
          f"{forensics['days_without_relations']}")
    print(f"relations 孤儿 event_id（按日）：{forensics['relations_orphan_event_ids']}")
    print(f"信号日不在归档：{forensics['signal_days_without_archive']}")
    if args.print_signals:
        print("\n== 明细 ==")
        for r in sorted(results, key=lambda z: (z["date"], str(z["code"]))):
            a = r["anchor"]
            if a:
                print(f"{r['date']} {r['code']} {r['name']:<8} → {a['event_date']} "
                      f"({a['category_tag']}/{a['impact_level']}★ {a['title'][:20]}) "
                      f"窗={a['window_td']}td rank={RANK_LABEL.get(a.get('anchor_rank', 9))} "
                      f"事件源={a['sources']} 同板候选={r['n_matching_events']}")
            else:
                print(f"{r['date']} {r['code']} {r['name']:<8} 无锚({r['no_anchor_reason']}, "
                      f"n={r['n_matching_events']}, naive={r['naive_hit']})")
    print(f"\n工件已写出: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
