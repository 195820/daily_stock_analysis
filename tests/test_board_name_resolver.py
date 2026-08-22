# -*- coding: utf-8 -*-
"""板块名解析器（board_name_resolver）单元测试

覆盖：
- L3 清洗：\u0000 填充 / 首尾空白
- L1 静态别名表加载与命中
- L2 模糊匹配打分与阈值
- confirm() 回写别名表
"""

import json
from pathlib import Path

import pytest

from src.services.board_name_resolver import (
    BoardNameResolver,
    clean_board_name,
)


# ── L3 清洗 ──

def test_clean_board_name_strips_null_and_space():
    assert clean_board_name("医药\u0000\u0000\u0000\u0000") == "医药"
    assert clean_board_name("  石墨烯\u0000\u0000 ") == "石墨烯"
    assert clean_board_name("") == ""
    assert clean_board_name("生物制药") == "生物制药"


# ── L1 别名表 ──

def test_l1_alias_hit(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({
        "aliases": {"生物制药": {"target": "生物医药", "source": "manual"}}
    }), encoding="utf-8")

    resolver = BoardNameResolver(alias_path=alias_file)
    resolver._ths_names = []  # 禁用 L2，避免真实网络请求
    assert resolver.resolve("生物制药") == "生物医药"
    # 未收录的名字原样返回（L2 列表为空时跳过）
    assert resolver.resolve("其他板块") == "其他板块"


def test_l1_alias_with_dirty_input(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({
        "aliases": {"生物制药": {"target": "生物医药"}}
    }), encoding="utf-8")

    resolver = BoardNameResolver(alias_path=alias_file)
    # 带 \u0000 的脏名字清洗后也能命中别名
    assert resolver.resolve("生物制药\u0000\u0000") == "生物医药"


# ── L2 模糊匹配 ──

OFFICIAL_NAMES = ["生物医药", "生物制品", "合成生物", "创新药", "减肥药", "白酒概念"]


def test_fuzzy_match_near_name():
    # 生物制药 vs 生物医药：3/4 字相同，应命中
    result = BoardNameResolver._fuzzy_match("生物制药", OFFICIAL_NAMES)
    assert result == "生物医药"


def test_fuzzy_match_exact_returns_directly():
    assert BoardNameResolver._fuzzy_match("创新药", OFFICIAL_NAMES) == "创新药"


def test_fuzzy_match_unrelated_returns_none():
    # 与官方列表完全不相关的名字不应强行匹配
    assert BoardNameResolver._fuzzy_match("量子纠缠计算", OFFICIAL_NAMES) is None


def test_fuzzy_match_empty_returns_none():
    assert BoardNameResolver._fuzzy_match("", OFFICIAL_NAMES) is None


def test_resolve_via_official_list(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    resolver = BoardNameResolver(alias_path=alias_file)
    # 注入官方列表，避免真实网络请求
    resolver._ths_names = OFFICIAL_NAMES

    assert resolver.resolve("生物制药") == "生物医药"

    # confirm 后写入别名表（source=auto）
    resolver.confirm("生物制药", "生物医药")
    saved = json.loads(alias_file.read_text(encoding="utf-8"))
    assert saved["aliases"]["生物制药"]["target"] == "生物医药"
    assert saved["aliases"]["生物制药"]["source"] == "auto"

    # 重复 confirm 不报错、不产生重复条目
    resolver.confirm("生物制药", "生物医药")
    saved = json.loads(alias_file.read_text(encoding="utf-8"))
    assert len(saved["aliases"]) == 1


def test_confirm_skips_same_name(tmp_path: Path):
    alias_file = tmp_path / "aliases.json"
    resolver = BoardNameResolver(alias_path=alias_file)
    # 清洗后同名（无需映射）不回写
    resolver.confirm("创新药", "创新药\u0000")
    assert not alias_file.exists() or not json.loads(
        alias_file.read_text(encoding="utf-8")
    ).get("aliases")
