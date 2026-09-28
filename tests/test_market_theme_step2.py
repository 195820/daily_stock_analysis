# -*- coding: utf-8 -*-
"""Phase 4b MarketThemeContext Step 2 单测（批次 5 §2.5，2026-09-28）。

覆盖：
- builder 纯函数（monkeypatch fetcher 喂合成数据）：排名正确性 / phase 判定四分支 /
  取数失败→unknown / 空清单→unknown / 进程内 TTL 缓存
- aggregator 消费点两个 helper：_build_theme_shadow_dict 形状、_theme_confidence_factor
  一致性判定（enabled-only，影子/未知恒 1.0）

三态「影子=决策零改动」由代码 guard（`if _mt_enabled_on`）保证；逐字节回归由
verify_glue_metrics.py --date 20260910 R-C3 十锚点兜底（计划 §2.5 回归锚）。
"""
from __future__ import annotations

import time

import pytest

from src.got import market_theme as mt
from src.got.market_theme import (
    MarketThemeContext,
    build_theme_context,
    reset_market_theme_context,
)
from src.got.nodes.aggregator import AggregatorNode


class _FakeFetcher:
    """可控的 DataFetcherManager 替身，喂固定板块清单 + 快筛动量。"""

    def __init__(self, concepts, industries, quick, *, raise_on=None):
        self._concepts = concepts
        self._industries = industries
        self._quick = quick
        self._raise_on = raise_on

    def get_all_concept_boards(self):
        if self._raise_on == "concepts":
            raise RuntimeError("boom")
        return self._concepts

    def get_all_industry_boards(self):
        return self._industries

    def get_board_quick_change(self, codes, concurrency=20):
        if self._raise_on == "quick":
            raise RuntimeError("boom")
        return {c: v for c, v in self._quick.items() if c in codes}


_CONCEPTS = [
    {"code": "880001", "name": "AI算力"},
    {"code": "880002", "name": "白酒"},
    {"code": "880003", "name": "光伏"},
]
_INDUSTRIES = [
    {"code": "881001", "name": "电子"},
    {"code": "881002", "name": "银行"},
]


@pytest.fixture(autouse=True)
def _reset():
    reset_market_theme_context()
    yield
    reset_market_theme_context()


# ── builder 纯函数 ──

def test_ranking_correctness_and_fields():
    quick = {
        "880001": {"change_1d": 2.0, "change_3d": 5.0, "change_5d": 10.0, "has_crash": False},
        "880002": {"change_1d": -1.0, "change_3d": -2.0, "change_5d": -5.0, "has_crash": True},
        "880003": {"change_1d": 0.5, "change_3d": 1.0, "change_5d": 3.0, "has_crash": False},
        "881001": {"change_1d": 1.0, "change_3d": 3.0, "change_5d": 6.0, "has_crash": False},
        "881002": {"change_1d": -0.5, "change_3d": 0.5, "change_5d": -1.0, "has_crash": False},
    }
    ctx = build_theme_context(_FakeFetcher(_CONCEPTS, _INDUSTRIES, quick), use_cache=False)
    assert ctx.status == "ok"
    # 概念域按 5 日动量降序：AI算力(10) > 光伏(3) > 白酒(-5)
    assert [c.name for c in ctx.leading_concepts] == ["AI算力", "光伏", "白酒"]
    assert [c.rank for c in ctx.leading_concepts] == [1, 2, 3]
    # change_pct 取当日框
    assert ctx.leading_concepts[0].change_pct == pytest.approx(2.0)
    # 落后题材：仅概念域 5 日负者
    assert [l.name for l in ctx.lagging_themes] == ["白酒"]
    assert ctx.is_available is True
    assert "mootdx_quick_change" in ctx.data_quality.source


def test_phase_four_branches():
    quick = {
        # accelerating：三时框全正
        "880001": {"change_1d": 1.0, "change_3d": 3.0, "change_5d": 8.0, "has_crash": False},
        # warming：5 日正但当日非正
        "880002": {"change_1d": -0.5, "change_3d": 1.0, "change_5d": 2.0, "has_crash": False},
        # cooling：5 日负
        "880003": {"change_1d": -1.0, "change_3d": -0.5, "change_5d": -3.0, "has_crash": False},
    }
    ctx = build_theme_context(_FakeFetcher(_CONCEPTS, [], quick), use_cache=False)
    phases = {a.name: a.phase for a in ctx.active_themes}
    assert phases.get("AI算力") == "accelerating"
    assert phases.get("白酒") == "warming"
    # cooling 不进 active_themes，从 breadth 观测
    b = ctx.theme_breadth
    assert b.accelerating_count == 1 and b.warming_count == 1 and b.cooling_count == 1


def test_fetch_failure_returns_unknown():
    ctx = build_theme_context(
        _FakeFetcher(_CONCEPTS, _INDUSTRIES, {}, raise_on="quick"), use_cache=False
    )
    assert ctx.status == "unknown"
    assert ctx.is_available is False


def test_empty_boards_returns_unknown():
    ctx = build_theme_context(_FakeFetcher([], [], {}), use_cache=False)
    assert ctx.status == "unknown"


def test_exception_in_fetch_returns_unknown():
    ctx = build_theme_context(
        _FakeFetcher(_CONCEPTS, _INDUSTRIES, {}, raise_on="concepts"), use_cache=False
    )
    assert ctx.status == "unknown"


def test_ttl_cache_success_then_hit():
    quick = {"880001": {"change_1d": 1.0, "change_3d": 1.0, "change_5d": 2.0, "has_crash": False}}
    fetcher = _FakeFetcher(_CONCEPTS[:1], [], quick)
    first = build_theme_context(fetcher, use_cache=True)
    assert first.status == "ok"
    # 命中缓存：即便换成会抛错的 fetcher，也应返回缓存对象
    boom = _FakeFetcher(_CONCEPTS[:1], [], {}, raise_on="concepts")
    second = build_theme_context(boom, use_cache=True)
    assert second is first


def test_fail_cache_short_ttl(monkeypatch):
    ctx = build_theme_context(_FakeFetcher([], [], {}), use_cache=True)
    assert ctx.status == "unknown"
    # 失败 TTL=30s；把缓存过期时间手动改到过去，再成功构造应重算
    mt._theme_cache["expires_at"] = time.monotonic() - 1
    quick = {"880001": {"change_1d": 1.0, "change_3d": 1.0, "change_5d": 2.0, "has_crash": False}}
    rebuilt = build_theme_context(_FakeFetcher(_CONCEPTS[:1], [], quick), use_cache=True)
    assert rebuilt.status == "ok"


# ── aggregator helper ──

def test_build_theme_shadow_dict_ok():
    quick = {
        "880001": {"change_1d": 2.0, "change_3d": 5.0, "change_5d": 10.0, "has_crash": False},
        "880002": {"change_1d": -1.0, "change_3d": -2.0, "change_5d": -5.0, "has_crash": True},
    }
    ctx = build_theme_context(_FakeFetcher(_CONCEPTS[:2], [], quick), use_cache=False)
    d = AggregatorNode._build_theme_shadow_dict(ctx)
    assert d["status"] == "ok"
    assert d["top5_concepts"][0]["name"] == "AI算力"
    assert "breadth" in d and "note" in d


def test_build_theme_shadow_dict_unknown():
    d = AggregatorNode._build_theme_shadow_dict(MarketThemeContext.unknown())
    assert d["status"] == "unknown"
    assert "未启用" in d["note"] or "unknown" in d["note"].lower()


class _Sector:
    def __init__(self, active_mainline):
        self.active_mainline = active_mainline


def _theme_with_breadth(acc, warm, cool):
    from src.got.market_theme import ThemeBreadth, MarketStructureDataQuality
    return MarketThemeContext(
        status="ok",
        theme_breadth=ThemeBreadth(
            accelerating_count=acc, warming_count=warm, cooling_count=cool,
            total_count=acc + warm + cool,
        ),
        data_quality=MarketStructureDataQuality(status="ok", source="test"),
    )


def test_confidence_factor_mismatch_is_09():
    # 题材偏多（acc+warm>cool）但无 leading 主线 → 不一致 0.9
    theme = _theme_with_breadth(acc=5, warm=2, cool=1)
    assert AggregatorNode._theme_confidence_factor(theme, _Sector([])) == 0.9
    # 题材偏多 + 有 leading 主线 → 一致 1.0
    assert AggregatorNode._theme_confidence_factor(theme, _Sector(["某主线"])) == 1.0


def test_confidence_factor_unknown_theme_is_neutral():
    factor = AggregatorNode._theme_confidence_factor(MarketThemeContext.unknown(), _Sector(["x"]))
    assert factor == 1.0
