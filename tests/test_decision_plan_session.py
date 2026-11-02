# -*- coding: utf-8 -*-
"""decision_plan 装配/对账的会话生命周期回归。

生产接线（portfolio_decision.py:419-437）在 assemble()/reconcile() 返回**之后**才调用
assemble_log_lines()/reconcile_log_lines()；而这两个函数自建 session 并在返回前 commit+close。
若 ORM 属性在 commit 时被过期（sessionmaker 缺省 expire_on_commit=True），日志层读取会抛
DetachedInstanceError，于是「装配成功、日志报失败」——2026-10-27 13:20 生产实跑复现：
trade_plans 两行已落库，trade_plan_log 却写着「当日计划装配失败（不阻断）:
Instance <TradePlanRecord …> is not bound to a Session」。
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.config import Config
from src.got.decision_plan import (
    assemble,
    assemble_log_lines,
    reconcile,
    reconcile_log_lines,
)
from src.storage import (
    AmbushPositionSnapshotRecord,
    DatabaseManager,
    HoldingThesisRecord,
    TradePlanRecord,
)


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(tmp_path / "decision_plan_session.db")
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


def _allocation(**overrides) -> SimpleNamespace:
    fields = dict(
        symbol="600519",
        name="贵州茅台",
        action="buy",
        target_quantity=100,
        target_value=150000.0,
        price_range=[1500.0, 1520.0],
        blocked_reason="",
        is_ambush_entry=False,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _decision(allocation) -> SimpleNamespace:
    return SimpleNamespace(allocation=allocation, unified_scoring=[])


def test_assemble_records_readable_after_session_close(isolated_db) -> None:
    written = assemble(
        _decision([_allocation()]), SimpleNamespace(), date(2026, 10, 27)
    )

    # 返回值必须能喂给日志层（生产接线顺序），且不得退化成「装配失败」
    lines = assemble_log_lines(written)
    assert lines[0] == "当日计划装配: 1 条写入 trade_plans"
    assert not any("失败" in line for line in lines)
    assert "600519 buy 100股" in lines[1]

    with isolated_db.get_session() as session:
        rows = session.query(TradePlanRecord).all()
    assert len(rows) == 1
    assert rows[0].execution_status == "pending"


def test_reconcile_records_readable_after_session_close(isolated_db) -> None:
    yesterday = date(2026, 10, 26)
    session = isolated_db.get_session()
    session.add(
        TradePlanRecord(
            plan_date=yesterday,
            symbol="600519",
            name="贵州茅台",
            lane="mainline",
            action="buy",
            target_quantity=100.0,
            target_value=150000.0,
            execution_status="pending",
            # valid_until 相对「今天」而非固定 plan_date：reconcile 的过期判据读
            # date.today()，固定日期会在若干天后把本用例变成恒 expired（2026-11-02 实锤）。
            valid_until=date.today() + timedelta(days=3),
        )
    )
    session.commit()
    session.close()

    updated = reconcile(yesterday, {"600519": 100})

    lines = reconcile_log_lines(yesterday, updated)
    assert lines[0] == "昨日对账(2026-10-26): 1 条状态回填"
    assert not any("失败" in line for line in lines)
    assert "→ executed" in lines[1]

    with isolated_db.get_session() as check:
        assert check.query(TradePlanRecord).one().execution_status == "executed"


def test_reconcile_cancelled_buy_closes_open_anchors(isolated_db) -> None:
    """I-4：买入计划 cancelled 后，对应 open thesis / ambush 快照必须关闭。"""
    yesterday = date(2026, 10, 26)
    session = isolated_db.get_session()
    session.add_all([
        TradePlanRecord(
            plan_date=yesterday,
            symbol="002741",
            name="光华科技",
            lane="ambush",
            action="buy",
            target_quantity=100.0,
            target_value=1000.0,
            execution_status="pending",
            valid_until=date.today() + timedelta(days=3),
        ),
        HoldingThesisRecord(
            symbol="002741",
            sector_name="机器人",
            family_name="机器人",
            entry_date=date(2026, 10, 25),
            entry_price=10.0,
            source="got_buy",
            lifecycle_state="intact",
            is_open=True,
        ),
        AmbushPositionSnapshotRecord(
            symbol="002741",
            sector_name="机器人",
            category_tag="drift",
            entry_date=date(2026, 10, 25),
            entry_price=10.0,
            is_open=True,
        ),
        # 对照：没有 cancelled buy plan 的 open thesis 不得被误关
        HoldingThesisRecord(
            symbol="600519",
            sector_name="白酒",
            family_name="消费",
            entry_date=date(2026, 10, 25),
            entry_price=1500.0,
            source="got_buy",
            lifecycle_state="intact",
            is_open=True,
        ),
    ])
    session.commit()
    session.close()

    updated = reconcile(yesterday, {})
    assert len(updated) == 1

    with isolated_db.get_session() as check:
        assert check.query(TradePlanRecord).one().execution_status == "cancelled"
        thesis = check.query(HoldingThesisRecord).filter_by(symbol="002741").one()
        assert thesis.is_open is False
        assert thesis.lifecycle_state == "closed"
        snap = check.query(AmbushPositionSnapshotRecord).filter_by(symbol="002741").one()
        assert snap.is_open is False
        assert snap.exit_signal == "plan_cancelled"
        control = check.query(HoldingThesisRecord).filter_by(symbol="600519").one()
        assert control.is_open is True
