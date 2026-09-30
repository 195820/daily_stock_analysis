# -*- coding: utf-8 -*-
"""WI-P2-9 跨日一致性断言（shadow）测试

覆盖：
1. 纯函数 cross_day_consistency_diff：跨口径别名归一、双向差、空名过滤
2. query_db_mainline_family_sector_names：DB 查询过滤条件（状态三元组 + 当日）
3. _save_context 接线红线：
   - off（缺省）→ 不进入断言分支；save 失败仍按旧路径 raise（行为逐字节不变）
   - shadow 有差异 → 落 warning 日志、不改写、不外抛
   - shadow 查库异常 → 内部吞掉，_save_context 整体不炸
"""

import logging
from datetime import date

import pytest

from src.got.persistence import cross_day_consistency_diff

TODAY = date(2026, 9, 29)


# =============================================================================
# 1. 纯函数
# =============================================================================

class TestCrossDayConsistencyDiff:
    def test_alias_normalized_match_yields_no_diff(self):
        """同花顺口径 JSON 名与东财口径 DB 名归一后相等 → 无差异。"""
        result = cross_day_consistency_diff(["半导体概念"], ["半导体"])
        assert result == {"j_only": [], "db_only": []}

    def test_bidirectional_diff(self):
        result = cross_day_consistency_diff(["芯片"], ["半导体"])
        assert result == {"j_only": ["芯片"], "db_only": ["半导体"]}

    def test_empty_and_blank_names_filtered(self):
        result = cross_day_consistency_diff(["", "芯片"], ["半导体", ""])
        # 空串被过滤；芯片与半导体互异
        assert result == {"j_only": ["芯片"], "db_only": ["半导体"]}

    def test_duplicates_collapse(self):
        result = cross_day_consistency_diff(["半导体", "半导体概念"], ["半导体"])
        assert result == {"j_only": [], "db_only": []}


# =============================================================================
# 2. DB 查询助手
# =============================================================================

class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self.filter_conditions = []

    def filter(self, *conditions):
        self.filter_conditions.extend(conditions)
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        # 单列查询 .all() 返回 Row 元组，以 (name,) 模拟
        self.rows = [(name,) for name in rows]
        self.queries = []

    def query(self, entity):
        q = _FakeQuery(self.rows)
        self.queries.append(q)
        return q

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestQueryDbMainlineFamilySectorNames:
    def test_returns_names_and_filters_by_date_and_states(self, monkeypatch):
        from src import storage

        session = _FakeSession(["半导体", None, "光模块"])
        monkeypatch.setattr(
            storage.DatabaseManager, "get_session", lambda self: session
        )
        from src.got.persistence import query_db_mainline_family_sector_names

        names = query_db_mainline_family_sector_names(TODAY)
        assert names == ["半导体", "光模块"]  # None 被过滤
        # 恰好一次 filter 调用（传入日期 + 状态两个条件）
        assert len(session.queries) == 1
        assert len(session.queries[0].filter_conditions) == 2

    def test_db_failure_propagates(self, monkeypatch):
        from src import storage

        def _boom(self):
            raise RuntimeError("db down")

        monkeypatch.setattr(storage.DatabaseManager, "get_session", _boom)
        from src.got.persistence import query_db_mainline_family_sector_names

        with pytest.raises(RuntimeError, match="db down"):
            query_db_mainline_family_sector_names(TODAY)


# =============================================================================
# 3. _save_context 接线
# =============================================================================

def _make_args():
    from src.got.schemas import MarketRegime, SectorIntel, UnifiedContext

    unified = UnifiedContext(
        status="normal",
        unified_signal="bullish",
        overall_confidence=0.7,
        final_position_limit=0.6,
    )
    regime = MarketRegime(regime="bull", risk_budget=0.5)
    sectors = [
        SectorIntel(
            sector_name="半导体",
            sector_code="BK1036",
            sector_type="industry",
            composite_score=88.0,
            confidence=0.85,
            classification="actionable",
            reasoning="test",
        ),
        SectorIntel(
            sector_name="光模块",
            sector_code="BK1041",
            sector_type="concept",
            composite_score=60.0,
            confidence=0.5,
            classification="caution",
            reasoning="test",
        ),
    ]
    return unified, regime, sectors


def _run_save_context(
    monkeypatch, tmp_path, save_effect=None, query_effect=None, db_result=None,
):
    """驱动 AggregatorNode._save_context，返回 save 调用次数。

    db_result：模拟 DB 主线族板块名返回；query_effect：模拟查库抛异常。
    两者互斥；均为 None 时默认返回 ["半导体"]（与 JSON actionable 一致 → 无差异）。
    """
    from src.got.nodes import aggregator as agg_mod
    from src.got.nodes.aggregator import AggregatorNode

    calls = {"save": 0}

    def _save(context, trading_date):
        calls["save"] += 1
        if save_effect is not None:
            raise save_effect
        # 不落真实目录：断言口径由 db_result/query_effect 侧模拟
        return tmp_path / f"previous_context_{trading_date:%Y%m%d}.json"

    monkeypatch.setattr(agg_mod, "save_current_context", _save)
    # EMA 段整体走异常短路（load_ema_baseline 抛 → 被既有 try 吞为 warning），
    # 避免触碰真实 data 目录
    monkeypatch.setattr(
        agg_mod, "load_ema_baseline",
        lambda: (_ for _ in ()).throw(RuntimeError("ema off-path for test")),
    )
    # 影子断言内部会 import persistence.query...，patch 模块属性使其不碰真库
    from src.got import persistence

    if query_effect is not None:
        monkeypatch.setattr(
            persistence, "query_db_mainline_family_sector_names",
            lambda d: (_ for _ in ()).throw(query_effect),
        )
    else:
        monkeypatch.setattr(
            persistence, "query_db_mainline_family_sector_names",
            lambda d: db_result if db_result is not None else ["半导体"],
        )

    unified, regime, sectors = _make_args()
    node = AggregatorNode(use_mock=True)
    node._save_context(unified, regime, sectors, TODAY)
    return calls["save"]


class TestSaveContextWiring:
    def test_off_default_never_enters_consistency_branch(self, monkeypatch, tmp_path, caplog):
        """缺省 off：不查库、无任何 P2-9 日志。"""
        from src.got import persistence

        def _no_query(d):
            raise AssertionError("off 态不应查库")

        monkeypatch.delenv("GOT_CONTEXT_CONSISTENCY", raising=False)
        monkeypatch.setattr(
            persistence, "query_db_mainline_family_sector_names", _no_query
        )
        with caplog.at_level(logging.WARNING):
            saved = _run_save_context(monkeypatch, tmp_path)
        assert saved == 1
        assert "P2-9" not in caplog.text

    def test_off_save_failure_swallowed_legacy_no_p29(self, monkeypatch, tmp_path, caplog):
        """红线：off 态 save_current_context 异常仍按旧路径被 try 吞成
        “上下文持久化失败” warning（非新抛），且不进入 P2-9 分支。"""
        monkeypatch.delenv("GOT_CONTEXT_CONSISTENCY", raising=False)
        with caplog.at_level(logging.WARNING):
            saved = _run_save_context(
                monkeypatch, tmp_path, save_effect=RuntimeError("disk full")
            )
        assert saved == 1  # _save 被调了一次（抛异常前已计数）
        assert "上下文持久化失败" in caplog.text
        assert "P2-9" not in caplog.text

    def test_shadow_logs_diff_without_raising(self, monkeypatch, tmp_path, caplog):
        """shadow：DB 有/JSON 无（光模块在 DB 主线族、JSON 仅半导体 actionable）
        → 落差异 warning，_save_context 正常返回。"""
        monkeypatch.setenv("GOT_CONTEXT_CONSISTENCY", "shadow")
        with caplog.at_level(logging.WARNING):
            saved = _run_save_context(
                monkeypatch, tmp_path, db_result=["半导体", "光模块"]
            )
        assert saved == 1
        assert "P2-9 shadow" in caplog.text
        assert "光模块" in caplog.text

    def test_shadow_db_exception_swallowed(self, monkeypatch, tmp_path, caplog):
        """shadow：查库异常 → 内部吞掉并留痕，_save_context 不向外炸。"""
        monkeypatch.setenv("GOT_CONTEXT_CONSISTENCY", "shadow")
        with caplog.at_level(logging.WARNING):
            saved = _run_save_context(
                monkeypatch, tmp_path, query_effect=RuntimeError("db down")
            )
        assert saved == 1
        assert "一致性断言异常" in caplog.text

    def test_shadow_no_diff_no_warning(self, monkeypatch, tmp_path, caplog):
        """shadow：JSON 与 DB 完全一致 → 不打差异日志。"""
        monkeypatch.setenv("GOT_CONTEXT_CONSISTENCY", "shadow")
        with caplog.at_level(logging.WARNING):
            _run_save_context(monkeypatch, tmp_path)
        assert "跨日一致性差异" not in caplog.text
