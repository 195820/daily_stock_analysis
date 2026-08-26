# -*- coding: utf-8 -*-
"""板块名解析器（board_name_resolver）单元测试"""

import json
from pathlib import Path

import pytest

from src.services.board_name_resolver import (
    BoardNameResolver,
    _is_dirty_cache,
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
