# -*- coding: utf-8 -*-
"""板块名解析器（board_name_resolver）单元测试"""

import json
from pathlib import Path

import pytest

from src.services.board_name_resolver import (
    SELF_HEAL_SIM_THRESHOLD,
    BoardNameResolver,
    _is_dirty_cache,
    _is_narrowing_alias,
    clean_board_name,
)

_NULL = "\x00"


# ── L3 清洗 ──

def test_clean_board_name_strips_null_and_space():
    assert clean_board_name("医药" + _NULL * 4) == "医药"
    assert clean_board_name("  石墨烯" + _NULL + " ") == "石墨烯"
    assert clean_board_name("") == ""
    assert clean_board_name("生物制药") == "生物制药"


# ── L1 别名表 ──

def test_l1_alias_hit(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({
        "aliases": {"生物制药": {"target": "生物医药", "source": "manual"}}
    }), encoding="utf-8")

    resolver = BoardNameResolver(alias_path=alias_file)
    resolver._ths_concept_names = []
    resolver._ths_industry_names = []
    name, kind = resolver.resolve_with_kind("生物制药")
    assert name == "生物医药"
    assert kind is None

    assert resolver.resolve("生物制药") == "生物医药"
    assert resolver.resolve("其他板块") == "其他板块"


def test_l1_alias_with_kind(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({
        "aliases": {
            "水产品": {"target": "养殖业", "source": "manual", "kind": "industry"},
            "生物制药": {"target": "生物医药", "source": "manual", "kind": "concept"},
        }
    }), encoding="utf-8")

    resolver = BoardNameResolver(alias_path=alias_file)
    resolver._ths_concept_names = []
    resolver._ths_industry_names = []

    name, kind = resolver.resolve_with_kind("水产品")
    assert name == "养殖业"
    assert kind == "industry"

    name, kind = resolver.resolve_with_kind("生物制药")
    assert name == "生物医药"
    assert kind == "concept"


def test_l1_alias_with_dirty_input(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({
        "aliases": {"生物制药": {"target": "生物医药"}}
    }), encoding="utf-8")

    resolver = BoardNameResolver(alias_path=alias_file)
    resolver._ths_concept_names = []
    resolver._ths_industry_names = []
    assert resolver.resolve("生物制药" + _NULL * 2) == "生物医药"


def test_is_narrowing_alias_blocks_parent_to_child():
    assert _is_narrowing_alias("化工", "氟化工概念") is True
    assert _is_narrowing_alias("化工", "化工原料") is True
    assert _is_narrowing_alias("半导体", "半导体概念") is False
    assert _is_narrowing_alias("生物制药", "生物医药") is False
    assert _is_narrowing_alias("化工", "化工") is False


def test_l1_skips_auto_narrowing_alias(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({
        "aliases": {"化工": {"target": "氟化工概念", "source": "auto", "kind": "concept"}}
    }), encoding="utf-8")

    resolver = BoardNameResolver(alias_path=alias_file)
    resolver._ths_concept_names = []
    resolver._ths_industry_names = []
    name, kind = resolver.resolve_with_kind("化工")
    assert name == "化工"
    assert kind is None


def test_l1_allows_manual_narrowing_alias(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({
        "aliases": {"化工": {"target": "氟化工概念", "source": "manual", "kind": "concept"}}
    }), encoding="utf-8")

    resolver = BoardNameResolver(alias_path=alias_file)
    resolver._ths_concept_names = []
    resolver._ths_industry_names = []
    name, kind = resolver.resolve_with_kind("化工")
    assert name == "氟化工概念"
    assert kind == "concept"


# ── L2 模糊匹配 ──

CONCEPT_NAMES = ["生物医药", "生物制品", "合成生物", "创新药", "减肥药", "白酒概念", "汽车芯片"]
INDUSTRY_NAMES = ["养殖业", "汽车整车", "煤炭开采", "半导体", "生物医药"]


def test_fuzzy_match_near_name():
    result = BoardNameResolver._fuzzy_match("生物制药", CONCEPT_NAMES)
    assert result == "生物医药"


def test_fuzzy_match_exact_returns_directly():
    assert BoardNameResolver._fuzzy_match("创新药", CONCEPT_NAMES) == "创新药"


def test_fuzzy_match_unrelated_returns_none():
    assert BoardNameResolver._fuzzy_match("量子纠缠计算", CONCEPT_NAMES) is None


def test_fuzzy_match_empty_returns_none():
    assert BoardNameResolver._fuzzy_match("", CONCEPT_NAMES) is None


def test_resolve_with_kind_l2_concept_hit(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    resolver = BoardNameResolver(alias_path=alias_file)
    resolver._ths_concept_names = CONCEPT_NAMES
    resolver._ths_industry_names = INDUSTRY_NAMES

    name, kind = resolver.resolve_with_kind("汽车芯片")
    assert name == "汽车芯片"
    assert kind == "concept"


def test_resolve_with_kind_l2_industry_hit(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    resolver = BoardNameResolver(alias_path=alias_file)
    resolver._ths_concept_names = CONCEPT_NAMES
    resolver._ths_industry_names = INDUSTRY_NAMES

    name, kind = resolver.resolve_with_kind("煤炭开采")
    assert name == "煤炭开采"
    assert kind == "industry"


def test_resolve_with_kind_no_hit(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    resolver = BoardNameResolver(alias_path=alias_file)
    resolver._ths_concept_names = CONCEPT_NAMES
    resolver._ths_industry_names = INDUSTRY_NAMES

    name, kind = resolver.resolve_with_kind("量子纠缠计算")
    assert name == "量子纠缠计算"
    assert kind is None


# ── confirm() 含 kind ──

def test_confirm_with_kind(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    resolver = BoardNameResolver(alias_path=alias_file)

    resolver.confirm("生物制药", "生物医药", kind="concept")
    saved = json.loads(alias_file.read_text(encoding="utf-8"))
    assert saved["aliases"]["生物制药"]["target"] == "生物医药"
    assert saved["aliases"]["生物制药"]["kind"] == "concept"
    assert saved["aliases"]["生物制药"]["source"] == "auto"


def test_confirm_rejects_auto_narrowing_alias(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    resolver = BoardNameResolver(alias_path=alias_file)

    resolver.confirm("化工", "氟化工概念", kind="concept")
    assert not alias_file.exists() or "化工" not in json.loads(
        alias_file.read_text(encoding="utf-8")
    ).get("aliases", {})

    # 人工确认仍允许，用于人工裁决
    resolver.confirm("化工", "氟化工概念", kind="concept", source="manual")
    saved = json.loads(alias_file.read_text(encoding="utf-8"))
    assert saved["aliases"]["化工"]["target"] == "氟化工概念"
    assert saved["aliases"]["化工"]["source"] == "manual"


def test_confirm_allows_same_core_concept_suffix(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    resolver = BoardNameResolver(alias_path=alias_file)

    resolver.confirm("半导体", "半导体概念", kind="concept")
    saved = json.loads(alias_file.read_text(encoding="utf-8"))
    assert saved["aliases"]["半导体"]["target"] == "半导体概念"


def test_confirm_skips_same_name(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    resolver = BoardNameResolver(alias_path=alias_file)
    resolver.confirm("创新药", "创新药" + _NULL)
    assert not alias_file.exists() or not json.loads(
        alias_file.read_text(encoding="utf-8")
    ).get("aliases")


def test_confirm_no_duplicate_with_same_kind(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    resolver = BoardNameResolver(alias_path=alias_file)

    resolver.confirm("X", "Y", kind="concept")
    resolver.confirm("X", "Y", kind="concept")
    saved = json.loads(alias_file.read_text(encoding="utf-8"))
    assert len(saved["aliases"]) == 1


def test_confirm_updates_kind(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    resolver = BoardNameResolver(alias_path=alias_file)

    resolver.confirm("X", "Y", kind="concept")
    resolver.confirm("X", "Y", kind="industry")
    saved = json.loads(alias_file.read_text(encoding="utf-8"))
    assert saved["aliases"]["X"]["kind"] == "industry"


# ── _pick_name_column ──

def test_pick_name_column_prefers_name():
    cols = ["name", "code", "date"]
    assert BoardNameResolver._pick_name_column(cols) == "name"


def test_pick_name_column_chinese():
    cols = ["code", "名称", "date"]
    assert BoardNameResolver._pick_name_column(cols) == "名称"


def test_pick_name_column_fallback():
    cols = ["code", "date", "value"]
    assert BoardNameResolver._pick_name_column(cols) == "code"


# ── _is_dirty_cache ──

def test_dirty_cache_all_digits():
    assert _is_dirty_cache(["300008", "300013", "300019", "300023"]) is True


def test_dirty_cache_mixed():
    names = ["300008", "300013", "300019", "生物医药", "创新药"]
    assert _is_dirty_cache(names) is True


def test_clean_cache_normal_names():
    names = ["生物医药", "创新药", "汽车芯片", "白酒概念"]
    assert _is_dirty_cache(names) is False


def test_dirty_cache_empty():
    assert _is_dirty_cache([]) is True


# ── 综合场景 ──

def test_resolve_via_official_list(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    resolver = BoardNameResolver(alias_path=alias_file)
    resolver._ths_concept_names = CONCEPT_NAMES
    resolver._ths_industry_names = INDUSTRY_NAMES

    assert resolver.resolve("生物制药") == "生物医药"

    resolver.confirm("生物制药", "生物医药", kind="concept")
    saved = json.loads(alias_file.read_text(encoding="utf-8"))
    assert saved["aliases"]["生物制药"]["target"] == "生物医药"
    assert saved["aliases"]["生物制药"]["source"] == "auto"

    resolver.confirm("生物制药", "生物医药", kind="concept")
    saved = json.loads(alias_file.read_text(encoding="utf-8"))
    assert len(saved["aliases"]) == 1


# ── 审核修复 3.2：自愈阈值 + auto_lowconf 隔离 ──

def test_aggressive_match_below_threshold_returns_none():
    """最高相似度 < 0.6 → 宁可不自愈，返回 None"""
    resolver = BoardNameResolver(alias_path=Path("unused.json"))
    resolver._ths_concept_names = ["白酒概念", "煤炭开采"]
    resolver._ths_industry_names = []
    assert resolver.aggressive_match("量子纠缠计算") is None


def test_aggressive_match_above_threshold_returns_best():
    resolver = BoardNameResolver(alias_path=Path("unused.json"))
    resolver._ths_concept_names = ["生物医药", "白酒概念"]
    resolver._ths_industry_names = []
    result = resolver.aggressive_match("生物制药")
    assert result == "生物医药"


def test_aggressive_match_same_name_in_list_returns_none():
    """官方列表里有同名概念 → 失败非名字问题，不自愈"""
    resolver = BoardNameResolver(alias_path=Path("unused.json"))
    resolver._ths_concept_names = ["创新药"]
    resolver._ths_industry_names = []
    assert resolver.aggressive_match("创新药") is None


def test_confirm_auto_lowconf_written_but_not_consumed(tmp_path: Path):
    """自愈回写 source=auto_lowconf → L1 解析跳过，留痕不自动生效"""
    alias_file = tmp_path / "aliases.json"
    resolver = BoardNameResolver(alias_path=alias_file)
    resolver._ths_concept_names = []
    resolver._ths_industry_names = []

    resolver.confirm("生物制药", "生物医药", kind="concept", source="auto_lowconf")
    saved = json.loads(alias_file.read_text(encoding="utf-8"))
    assert saved["aliases"]["生物制药"]["source"] == "auto_lowconf"

    # L1 不消费：无模糊匹配候选时原样返回
    assert resolver.resolve("生物制药") == "生物制药"


def test_auto_lowconf_upgraded_to_manual_takes_effect(tmp_path: Path):
    """人工把 source 改为 manual 后生效"""
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({
        "aliases": {"生物制药": {"target": "生物医药", "source": "manual"}}
    }), encoding="utf-8")
    resolver = BoardNameResolver(alias_path=alias_file)
    resolver._ths_concept_names = []
    resolver._ths_industry_names = []
    assert resolver.resolve("生物制药") == "生物医药"


def test_confirm_lowconf_does_not_override_verified_alias(tmp_path: Path):
    """低置信回写不得覆盖已验证（manual/auto）别名"""
    alias_file = tmp_path / "aliases.json"
    resolver = BoardNameResolver(alias_path=alias_file)

    resolver.confirm("生物制药", "生物医药", kind="concept", source="auto")
    resolver.confirm("生物制药", "化学制药", kind="concept", source="auto_lowconf")
    saved = json.loads(alias_file.read_text(encoding="utf-8"))
    assert saved["aliases"]["生物制药"]["target"] == "生物医药"
    assert saved["aliases"]["生物制药"]["source"] == "auto"
