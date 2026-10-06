# -*- coding: utf-8 -*-
"""3-D 埋伏重放 harness 参数化回归（2026-10-06）

覆盖：_write_ambush_buy_snapshots / _close_ambush_snapshots 的 as_of 形参——
缺省 None 时行为与旧代码一致（date.today()），显式传历史日时 entry_date /
exit_date 跟随 as_of（重放标定不再被"硬编今日"锁死）。
纯参数化、零决策语义变化；DB 用 fake session，不触真实存储。
"""
from datetime import date
from types import SimpleNamespace

import pytest

from src.got.nodes.portfolio_decision import PortfolioDecisionNode
from src.got.schemas import AmbushCandidate, UnifiedScoringResult

HIST_DAY = date(2026, 9, 24)


def _mk_alloc():
    return SimpleNamespace(symbol="600xxx", action="buy", quantity_delta=100,
                           price_range=(10.0, 10.5))


def _mk_scoring():
    return UnifiedScoringResult.model_construct(
        symbol="600xxx", name="测试股", sector_name="机械基件")


def _mk_candidate():
    return AmbushCandidate.model_construct(
        sector_name="机械基件", sector_code="880401", category_tag="drift",
        events=["某催化剂事件"], top_event_date=HIST_DAY, top_event_level=3,
        catalyst_score=1.0, accumulation_detail="", gate_notes="",
        accum_strength="missing")


class _FakeQuery:
    def __init__(self, sink, record_cls):
        self._sink = sink
        self._record_cls = record_cls

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return None

    def update(self, values):
        self._sink["updates"].append(values)
        return 1

    def all(self):
        return []


class _FakeSession:
    def __init__(self, sink):
        self._sink = sink

    def query(self, record_cls):
        return _FakeQuery(self._sink, record_cls)

    def add(self, record):
        self._sink["added"].append(record)

    def commit(self):
        self._sink["committed"] = True


class _FakeCtx:
    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False


class _FakeDB:
    def __init__(self, sink):
        self._sink = sink

    def get_session(self):
        return _FakeCtx(self._sink["session"])


@pytest.fixture
def fake_storage(monkeypatch):
    """注入 fake DatabaseManager / Record 类（函数内 from src.storage import 走模块属性）。"""
    import sys

    sink = {"added": [], "updates": [], "committed": False}
    session = _FakeSession(sink)
    sink["session"] = session

    class _FakeRecord:
        # 模拟 SQLAlchemy 记录：接受关键字参数并落属性
        is_open = None
        symbol = None
        entry_date = None

        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    record_cls = _FakeRecord
    fake_module = SimpleNamespace(
        DatabaseManager=lambda: _FakeDB(sink),
        AmbushPositionSnapshotRecord=record_cls,
    )
    monkeypatch.setitem(sys.modules, "src.storage", fake_module)
    return sink


def test_write_snapshot_default_uses_today(fake_storage):
    node = PortfolioDecisionNode(use_mock=True)
    unified = SimpleNamespace(ambush_pool=[_mk_candidate()])
    written, failed = node._write_ambush_buy_snapshots(
        [_mk_alloc()], [_mk_scoring()], unified,
    )
    assert written and not failed
    rec = fake_storage["added"][0]
    assert rec.entry_date == date.today()


def test_write_snapshot_honors_as_of(fake_storage):
    node = PortfolioDecisionNode(use_mock=True)
    unified = SimpleNamespace(ambush_pool=[_mk_candidate()])
    written, _ = node._write_ambush_buy_snapshots(
        [_mk_alloc()], [_mk_scoring()], unified, as_of=HIST_DAY,
    )
    assert written
    rec = fake_storage["added"][0]
    assert rec.entry_date == HIST_DAY  # 重放日不再被硬编今日锁死


def test_close_snapshot_default_and_as_of(fake_storage):
    node = PortfolioDecisionNode(use_mock=True)
    exits = [{"symbol": "600xxx", "exit_signal": "catalyst_hit",
              "sector": "机械基件", "category_tag": "drift", "reason": "t"}]
    portfolio = {"accounts": [{"positions": [{"symbol": "600xxx", "last_price": 11.0}]}]}

    node._close_ambush_snapshots(exits, portfolio)
    assert fake_storage["updates"][-1]["exit_date"] == date.today()

    node._close_ambush_snapshots(exits, portfolio, as_of=HIST_DAY)
    assert fake_storage["updates"][-1]["exit_date"] == HIST_DAY
