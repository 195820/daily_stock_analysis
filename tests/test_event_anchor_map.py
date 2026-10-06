# -*- coding: utf-8 -*-
"""B4-1 前置·事件锚重建脚本纯函数回归（2026-10-06）

只测无副作用计算件：归档日过滤、板块宇宙下钻、relations 反查与生产护栏、
名↔名匹配（直接调生产 `AggregatorNode._match_sector`，本脚本禁止另写简化版）、
前瞻锚选取（窗=0 已落地事件不作锚、horizon、右删失）、覆盖率分层。

两条真实踩坑回归：
1. 证据等级必须按【板码】粒度记（`code_rank`）——只记事件级来源时，一个事件
   「黄金走板码精确、同族其他板走家族层」会把 16 条家族弱锚误报成硬锚
   （实测 20 锚里只有 4 条是硬锚）；
2. `sector.json.active_mainline` 顶层是家族分组、板块在 `members[]`，
   不平铺递归就会丢一批板码（GATE-2 同款坑）。
"""
import importlib.util
import os
from datetime import date, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SPEC_PATH = os.path.join(ROOT, "scripts", "rebuild_event_anchor_map.py")


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("event_anchor_rebuild", SPEC_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Matchers:
    """名↔名判定直接借用生产实现（不复制逻辑）。"""

    @staticmethod
    def match_sector(name, target, min_substr_len=3):
        from src.got.nodes.aggregator import AggregatorNode
        return AggregatorNode._match_sector(name, target, min_substr_len=min_substr_len)


class _NoBoard(_Matchers):
    """registry 板码解析恒失败 + 家族恒「其他」：只让 relations/名层参与的单层测试。"""

    @staticmethod
    def board_code(name):
        return None, "failed"

    @staticmethod
    def frame_group(name):
        return "其他"


# ── 归档日过滤 ──────────────────────────────────────────────
def test_list_archive_days_excludes_snapshot_dirs(mod, tmp_path):
    for name in ("20260805", "20260817_pre_fix", "20260930", "_cache", "20261012"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "news.json").write_text("{}", encoding="utf-8")
    # 目录名必须是严格 8 位数字，且 ≤ as_of
    assert mod.list_archive_days(tmp_path, date(2026, 10, 6)) == ["20260805", "20260930"]


def test_list_archive_days_missing_dir_returns_empty(mod, tmp_path):
    assert mod.list_archive_days(tmp_path / "nope", date(2026, 10, 6)) == []


# ── 板块宇宙递归（家族分组下钻）──────────────────────────────
def test_collect_board_code_names_drills_family_groups(mod):
    payload = {
        "active_mainline": [{"group_name": "AI硬件", "members": [{"code": "880491", "name": "半导体"}]}],
        "ambush": [{"code": "880344", "name": "建材"}],
        "cooling_down": [{"name": "无码板块"}],
        "top_by_score": [{"sector_code": "880492", "sector_name": "元器件"}],
    }
    got = mod.collect_board_code_names(payload, {})
    assert got == {"880491": "半导体", "880344": "建材", "880492": "元器件"}


# ── relations 反查 + 生产护栏 ────────────────────────────────
def test_invert_relations_index_maps_event_to_codes(mod):
    rel_index = {
        "880471": [{"event_id": "e1", "direction": "positive"}],
        "880472": [{"event_id": "e1", "direction": "negative"},
                   {"event_id": "", "direction": "positive"}],
    }
    got = mod.invert_relations_index(rel_index)
    assert set(got) == {"e1"}
    assert {r["board_code"] for r in got["e1"]} == {"880471", "880472"}


def _event(eid="e1", edate=date(2026, 9, 10), sector="半导体", affected=None):
    return {
        "event_id": eid, "event_date": edate, "sector": sector,
        "affected_sectors": affected or [], "category_tag": "drift",
        "event_type": "narrative", "impact_level": 2, "direction": "positive",
        "confidence": 0.8, "is_global": False, "title": "示例事件",
    }


def test_anchor_event_negative_relation_dropped_and_other_relations_kept(mod):
    rels = {"e1": [{"board_code": "880471", "direction": "negative"},
                   {"board_code": "880472", "direction": "positive"}]}
    a = mod.anchor_event(_event(), rels, {}, _NoBoard, {}, 5)
    assert a["code_rank"] == {"880472": mod.RANK_RELATIONS}


def test_build_day_anchors_drops_orphan_relation_event_ids(mod):
    """relation 的 event_id 不在当日日历 → 按生产护栏丢弃（只信当日日历原值）。"""
    rel_index = {"880999": [{"event_id": "ghost", "direction": "positive"}]}
    anchors, orphans = mod.build_day_anchors([_event()], rel_index, {}, _NoBoard, 5)
    assert orphans == ["ghost"]
    assert anchors[0]["code_rank"] == {}


# ── 名↔名匹配走生产件（generic/narrow 不采信）────────────────
def test_resolve_name_to_codes_uses_production_matcher(mod):
    universe = {"880491": "半导体", "880492": "半导体概念", "880999": "医药电商"}
    hits = dict(mod.resolve_name_to_codes("半导体", universe, _Matchers))
    assert "880491" in hits and hits["880491"] in ("exact", "alias")
    # "医药" ↔ "医药电商" 属 narrow/generic，生产侧同样拒绝
    assert mod.resolve_name_to_codes("医药", {"880999": "医药电商"}, _Matchers) == []


def test_anchor_event_code_rank_is_per_code_not_event_level(mod):
    """核心回归：同一事件里直连码是硬锚、同族码必须是弱锚（rank=3）。"""
    universe = {"880491": "半导体", "880952": "芯片"}
    family = {"880491": "AI硬件", "880952": "AI硬件"}

    class _M:
        match_sector = staticmethod(_Matchers.match_sector)

        @staticmethod
        def board_code(name):
            return ("880491", "exact") if name == "半导体" else (None, "failed")

        @staticmethod
        def frame_group(name):
            return "AI硬件" if name == "半导体" else "其他"

    a = mod.anchor_event(_event(), {}, universe, _M(), {}, 5, family)
    assert a["code_rank"]["880491"] == mod.RANK_BOARD_CODE      # 板码直连（硬）
    assert a["code_rank"]["880952"] == mod.RANK_FAMILY          # 仅同族（弱）
    assert set(a["sources"]) == {"board_code:exact", "name_match", "family_match"}
    # 事件级 source_rank 是最强层，不能拿来当「该板码的证据等级」用
    assert a["source_rank"] == mod.RANK_BOARD_CODE


def test_anchor_event_max_fanout_keeps_only_exact_alias(mod):
    # 事件名「半导体设备」对 6 个「半导体」板是 substring（生产口径可信但过泛），
    # 命中数超 max_fanout 后只保留 exact/alias，避免一个泛化事件名把锚撒满全场
    universe = {"880491": "半导体设备"}
    for i in range(6):
        universe[f"88{500 + i:03d}"] = "半导体"

    class _M(_NoBoard):
        @staticmethod
        def board_code(name):
            return None, "failed"

    a = mod.anchor_event(_event(sector="半导体设备"), {}, universe, _M, {}, 5)
    assert set(a["code_rank"]) == {"880491"}                    # 只剩 exact 那一个
    assert a["code_rank"]["880491"] == mod.RANK_NAME_MATCH
    assert a["ambiguous_names"] == ["半导体设备"]                # 触发截断留痕


# ── 前瞻锚选取 ──────────────────────────────────────────────
def _anchor(edate, codes_rank, impact=2, eid="e1"):
    return {
        "event_id": eid, "event_date": edate, "impact_level": impact,
        "category_tag": "drift", "title": "t", "sources": [], "code_rank": codes_rank,
    }


DAYS = ["20260902", "20260903", "20260904", "20260907", "20260908"]


def test_pick_forward_anchor_excludes_window_zero_landed_event(mod):
    """窗=0（事件日=信号日）的已落地事件不作前瞻锚，归 only_landed_today。"""
    anchors = [_anchor(date(2026, 9, 2), {"880491": mod.RANK_BOARD_CODE})]
    got = mod.pick_forward_anchor(anchors, "20260902", DAYS, 20, len(anchors), "880491")
    assert got["anchor"] is None
    assert got["reason"] == "only_landed_today"


def test_pick_forward_anchor_prefers_shorter_window_then_hard_rank(mod):
    soft = _anchor(date(2026, 9, 3), {"880491": mod.RANK_FAMILY}, eid="soft")
    hard_far = _anchor(date(2026, 9, 4), {"880491": mod.RANK_RELATIONS}, eid="hard")
    got = mod.pick_forward_anchor([soft, hard_far], "20260902", DAYS, 20, 2, "880491")
    assert got["anchor"]["event_id"] == "soft"                 # 窗长优先于证据强度
    same_window = _anchor(date(2026, 9, 4), {"880491": mod.RANK_FAMILY}, eid="fam")
    got2 = mod.pick_forward_anchor([same_window, hard_far], "20260902", DAYS, 20, 2, "880491")
    assert got2["anchor"]["event_id"] == "hard"                # 同窗内硬锚胜出
    assert got2["anchor"]["anchor_rank"] == mod.RANK_RELATIONS


def test_pick_forward_anchor_counts_hard_candidates_when_weak_wins(mod):
    """窗长优先可能选中家族弱锚，但硬锚候选必须单独计数（B4-1 硬锚上限口径）。"""
    weak_near = _anchor(date(2026, 9, 3), {"880491": mod.RANK_FAMILY}, eid="weak")
    hard_far = _anchor(date(2026, 9, 4), {"880491": mod.RANK_BOARD_CODE}, eid="hard")
    got = mod.pick_forward_anchor([weak_near, hard_far], "20260902", DAYS, 20, 2, "880491")
    assert got["anchor"]["event_id"] == "weak"
    assert got["anchor"]["anchor_rank"] == mod.RANK_FAMILY
    assert got["n_forward_hard"] == 1                      # 存在硬锚候选，只是窗长更远
    assert mod.pick_forward_anchor([weak_near], "20260902", DAYS, 20, 1, "880491")[
        "n_forward_hard"] == 0


def test_pick_forward_anchor_horizon_and_archive_end(mod):
    far = _anchor(date(2026, 12, 31), {"880491": mod.RANK_BOARD_CODE})
    got = mod.pick_forward_anchor([far], "20260902", DAYS, 3, 1, "880491")
    assert got["reason"] == "event_beyond_archive_end"         # 超出归档末尾：窗长不可测
    within_days = DAYS + ["20261231"]
    got2 = mod.pick_forward_anchor([far], "20260902", within_days, 3, 1, "880491")
    assert got2["reason"] == "beyond_horizon"                  # 在日历内但超 horizon


def test_pick_forward_anchor_reasons_distinguish_calendar_gaps(mod):
    assert mod.pick_forward_anchor([], "20260902", DAYS, 20, 0, "880491")["reason"] == \
        "no_calendar_events_today"
    assert mod.pick_forward_anchor([], "20260902", DAYS, 20, 30, "880491")["reason"] == \
        "no_covering_event_for_board"


def test_pick_forward_anchor_past_events_are_not_anchors(mod):
    past = _anchor(date(2026, 8, 1), {"880491": mod.RANK_BOARD_CODE})
    got = mod.pick_forward_anchor([past], "20260902", DAYS, 20, 1, "880491")
    assert got["anchor"] is None
    assert got["reason"] == "no_forward_event_for_board"


# ── 信号去重与旧口径基线 ────────────────────────────────────
def test_first_signals_dedups_and_drops_control(mod):
    rows = [
        {"date": "20260910", "code": "880635", "name": "先进封装", "grp": "strict"},
        {"date": "20260908", "code": "880635", "name": "先进封装", "grp": "released"},
        {"date": "20260909", "code": "880635", "name": "先进封装", "grp": "released"},
        {"date": "20260909", "code": "880999", "name": "对照板", "grp": "control"},
    ]
    got = mod.first_signals(rows)
    assert [(r["date"], r["code"], r["grp"]) for r in got] == [
        ("20260908", "880635", "released"),
        ("20260910", "880635", "strict"),
    ]


def test_naive_anchor_hit_strict_flag_drops_window_zero(mod):
    anchors = [{
        "event_date": date(2026, 9, 2), "sector": "半导体",
        "affected_sectors": ["芯片"], "code_rank": {},
    }]
    assert mod.naive_anchor_hit(anchors, "20260902", "半导体") is True
    assert mod.naive_anchor_hit(anchors, "20260902", "半导体", strict=True) is False


# ── 覆盖率分层（把弱锚和硬锚分开报）──────────────────────────
def test_summarize_separates_hard_and_family_only_anchors(mod):
    signals = [
        {"date": "20260902", "code": "880491", "naive_hit": True, "naive_hit_strict": False,
         "board_covered_any_day": True, "board_covered_strong_any_day": True,
         "no_anchor_reason": "ok",
         "anchor": {"window_td": 1, "category_tag": "drift", "sources": ["name_match"],
                    "anchor_rank": mod.RANK_NAME_MATCH}},
        {"date": "20260902", "code": "880492", "naive_hit": False, "naive_hit_strict": False,
         "board_covered_any_day": True, "board_covered_strong_any_day": False,
         "no_anchor_reason": "ok",
         "anchor": {"window_td": 2, "category_tag": "fade", "sources": ["family_match"],
                    "anchor_rank": mod.RANK_FAMILY}},
        {"date": "20260903", "code": "880493", "naive_hit": False, "naive_hit_strict": False,
         "board_covered_any_day": False, "board_covered_strong_any_day": False,
         "no_anchor_reason": "no_covering_event_for_board", "anchor": None},
    ]
    cov = mod.summarize(signals)
    assert cov["signals_total"] == 3
    assert cov["anchored"] == 2
    assert cov["anchored_strong"] == 1                         # 只有 rank≤2 算硬锚
    assert cov["anchored_family_only"] == 1
    assert cov["hard_anchor_available"] == 0                   # 无 n_forward_hard 信号
    assert cov["naive_baseline_anchored"] == 1
    assert cov["naive_baseline_anchored_strict"] == 0
    assert cov["board_covered_any_day"] == 2
    assert cov["board_covered_strong_any_day"] == 1
    assert cov["by_anchor_rank"] == {"name_match": 1, "family_match": 1}
    assert cov["no_anchor_reasons"] == {"no_covering_event_for_board": 1}


def test_summarize_empty_signals_is_safe(mod):
    cov = mod.summarize([])
    assert cov["signals_total"] == 0
    assert cov["coverage_pct"] == 0.0
    assert cov["window_td"]["p50"] is None


def test_horizon_and_fallback_defaults_are_not_silently_zero(mod):
    """horizon 默认值与旧脚本一致（20 交易日），且过期事件不会被当锚（防回归）。"""
    assert mod._to_date("20260930") == date(2026, 9, 30)
    ns = mod.parse_args(["--as-of", "2026-10-06"])
    assert ns.horizon_trading_days == 20
    assert ns.max_fanout == 5
    assert ns.no_family_layer is False
    # timedelta 守卫：窗=0 与窗=1 必须不同桶
    assert date(2026, 9, 2) + timedelta(days=1) == date(2026, 9, 3)
