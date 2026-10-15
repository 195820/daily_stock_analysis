"""B3 四态状态机单测（2026-10-15 拍板落地第一版）

holding_thesis_snapshots.lifecycle_state: intact / drift_watch / thesis_broken / closed
覆盖：
1. 迁移回填（旧库三行证据 → closed/drift_watch/intact）+ 幂等重跑
2. 新库 create_all 直建含列
3. 四转换路径：买入=intact（_write_thesis_snapshots 逻辑字段级验证 → 用 ORM 直写模拟）、
   drift_watch（_apply_drift_watch）、thesis_broken（_mark_thesis_broken）、closed（_close_thesis_snapshots 判定）
4. is_open 派生一致性：closed ↔ is_open=False

DB 策略：临时 sqlite 文件 + DatabaseManager._instance = None 单例重置
（参照迁移验证脚本 /tmp/mig_test5.db 模式）。
"""
import os
import sqlite3
import tempfile
from datetime import date, datetime

import pytest

from src.storage import DatabaseManager, HoldingThesisRecord


@pytest.fixture()
def fresh_db():
    """独立临时库 + 单例重置，测试完清理。"""
    DatabaseManager._instance = None
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        os.remove(path)  # DatabaseManager 需要 URL 指向不存在文件以触发 create_all
    except PermissionError:
        # Windows mkstemp 句柄释放有延迟；文件存在时 create_all 仍可补建缺表
        pass
    db = DatabaseManager(db_url=f"sqlite:///{path}")
    yield db, path
    DatabaseManager._instance = None
    try:
        if os.path.exists(path):
            os.remove(path)
    except PermissionError:
        pass


def _rows(path: str):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT symbol, is_open, drift_watch_since, lifecycle_state "
            "FROM holding_thesis_snapshots ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


class TestMigrationBackfill:
    """迁移回填：旧库三行证据冲突 → 回填 + 幂等。"""

    def _make_old_db(self, path: str):
        conn = sqlite3.connect(path)
        # fixture 可能因 Windows 句柄延迟未删净：fixture 已建新表结构，先删再建旧结构
        conn.execute("DROP TABLE IF EXISTS holding_thesis_snapshots")
        conn.execute(
            """CREATE TABLE holding_thesis_snapshots (
            id INTEGER PRIMARY KEY, symbol VARCHAR(16), sector_name VARCHAR(64),
            family_name VARCHAR(64), entry_date DATE, entry_price FLOAT,
            composite_at_entry FLOAT, source VARCHAR(20),
            drift_watch_since DATE, drift_family VARCHAR(64), is_open BOOLEAN,
            created_at DATETIME, updated_at DATETIME,
            CONSTRAINT uix_holding_thesis_symbol_date UNIQUE (symbol, entry_date))"""
        )
        # 行1: open + drift_watch_since → drift_watch
        conn.execute(
            "INSERT INTO holding_thesis_snapshots (symbol, sector_name, family_name, "
            "entry_date, entry_price, source, drift_watch_since, is_open) "
            "VALUES ('688598','半导体','AI 硬件','2026-10-09',100.0,'got_buy','2026-10-15',1)"
        )
        # 行2: closed（is_open=0）→ closed
        conn.execute(
            "INSERT INTO holding_thesis_snapshots (symbol, sector_name, family_name, "
            "entry_date, entry_price, source, is_open) "
            "VALUES ('600000','银行','大金融','2026-10-10',10.0,'got_buy',0)"
        )
        # 行3: 纯净 open → intact
        conn.execute(
            "INSERT INTO holding_thesis_snapshots (symbol, sector_name, family_name, "
            "entry_date, entry_price, source, is_open) "
            "VALUES ('000001','证券','大金融','2026-10-11',11.0,'got_buy',1)"
        )
        conn.commit()
        conn.close()

    def test_backfill_three_rows(self, fresh_db):
        _, path = fresh_db
        DatabaseManager._instance = None  # 先建旧库结构
        self._make_old_db(path)
        DatabaseManager._instance = None  # 触发迁移
        DatabaseManager(db_url=f"sqlite:///{path}")
        rows = {r[0]: r[3] for r in _rows(path)}
        assert rows == {"688598": "drift_watch", "600000": "closed", "000001": "intact"}

    def test_backfill_idempotent(self, fresh_db):
        _, path = fresh_db
        DatabaseManager._instance = None
        self._make_old_db(path)
        DatabaseManager._instance = None
        DatabaseManager(db_url=f"sqlite:///{path}")
        first = _rows(path)
        DatabaseManager._instance = None
        DatabaseManager(db_url=f"sqlite:///{path}")
        assert _rows(path) == first

    def test_new_db_has_columns(self, fresh_db):
        db, path = fresh_db
        conn = sqlite3.connect(path)
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(holding_thesis_snapshots)").fetchall()]
        conn.close()
        assert "lifecycle_state" in cols
        assert "catalyst_event_id" in cols
        assert db is not None


class TestStateTransitions:
    """四转换路径：intact → drift_watch → thesis_broken → closed + 买入复位。"""

    def _insert_open(self, db, symbol="688598", **kw):
        with db.get_session() as session:
            record = HoldingThesisRecord(
                symbol=symbol,
                sector_name=kw.get("sector_name", "半导体"),
                family_name=kw.get("family_name", "AI 硬件"),
                entry_date=kw.get("entry_date", date(2026, 10, 9)),
                entry_price=100.0,
                composite_at_entry=75.0,
                source="got_buy",
                lifecycle_state="intact",
                is_open=True,
            )
            session.add(record)
            session.commit()
            return record.id

    def test_buy_writes_intact(self, fresh_db):
        db, path = fresh_db
        rid = self._insert_open(db)
        with db.get_session() as session:
            rec = session.get(HoldingThesisRecord, rid)
            assert rec.lifecycle_state == "intact"
            assert rec.is_open is True

    def test_drift_watch_transition(self, fresh_db):
        """_apply_drift_watch 的 UPDATE 分支字段级验证。"""
        db, path = fresh_db
        rid = self._insert_open(db)
        today = date.today()
        # 复刻 _apply_drift_watch UPDATE 分支
        with db.get_session() as session:
            record = (
                session.query(HoldingThesisRecord)
                .filter(HoldingThesisRecord.symbol == "688598",
                        HoldingThesisRecord.is_open == True)  # noqa: E712
                .first()
            )
            record.drift_watch_since = today
            record.drift_family = "AI 硬件"
            record.lifecycle_state = "drift_watch"
            session.commit()
        rows = _rows(path)
        assert rows[0][2] == today.isoformat()
        assert rows[0][3] == "drift_watch"

    def test_thesis_broken_via_mark_helper(self, fresh_db):
        """_mark_thesis_broken：open 行 → thesis_broken；无 open 行零写入。"""
        db, path = fresh_db
        self._insert_open(db)
        node = _make_node()
        node._mark_thesis_broken("688598")
        assert _rows(path)[0][3] == "thesis_broken"
        # closed 行不被触碰
        with db.get_session() as session:
            session.get(HoldingThesisRecord, 1).is_open = False
            session.get(HoldingThesisRecord, 1).lifecycle_state = "closed"
            session.commit()
        node._mark_thesis_broken("688598")
        assert _rows(path)[0][3] == "closed"

    def test_thesis_broken_missing_symbol_noop(self, fresh_db):
        db, _ = fresh_db
        node = _make_node()
        node._mark_thesis_broken("999999")  # 不存在 → 不抛异常

    def test_intact_rewrite_clears_drift_watch_since(self, fresh_db):
        """B3 不变式：intact ⟹ drift_watch_since NULL。

        迁移回填谓词「lifecycle_state='intact' AND drift_watch_since IS NOT NULL → drift_watch」
        随每次 init 重跑：同日重写重置 intact 若不清 since，下次重启会把行翻回 drift_watch。
        """
        db, path = fresh_db
        self._insert_open(db)
        with db.get_session() as session:
            rec = session.query(HoldingThesisRecord).filter_by(symbol="688598").first()
            rec.drift_watch_since = date.today()  # 先置 drift_watch 态证据
            rec.lifecycle_state = "drift_watch"
            session.commit()
        # 复刻 _write_thesis_snapshots 同日重写分支：重置 intact 必须同步清 since
        with db.get_session() as session:
            rec = session.query(HoldingThesisRecord).filter_by(symbol="688598").first()
            rec.lifecycle_state = "intact"
            rec.drift_watch_since = None
            rec.drift_family = None
            session.commit()
        # 回填谓词重放：不得命中（幂等、不翻回）
        import sqlite3
        conn = sqlite3.connect(path)
        conn.execute(
            "UPDATE holding_thesis_snapshots "
            "SET lifecycle_state = CASE WHEN is_open = 0 THEN 'closed' ELSE 'drift_watch' END "
            "WHERE lifecycle_state = 'intact' "
            "AND (is_open = 0 OR drift_watch_since IS NOT NULL)"
        )
        conn.commit()
        assert conn.execute(
            "SELECT lifecycle_state FROM holding_thesis_snapshots WHERE symbol='688598'"
        ).fetchone()[0] == "intact"
        conn.close()

    def test_drift_watch_transition_real_method(self, fresh_db):
        """直调真实 _apply_drift_watch（非复刻 SQL）：UPDATE 分支落 drift_watch。"""
        db, path = fresh_db
        self._insert_open(db)
        node = _make_node()
        node._apply_drift_watch(
            symbol="688598", family="AI 硬件", today=date.today(),
            anchor_composite=70.0, thesis=None, sector_name="半导体",
        )
        rows = _rows(path)
        assert rows[0][2] == date.today().isoformat()
        assert rows[0][3] == "drift_watch"

    def test_closed_transition_real_method(self, fresh_db):
        """直调真实 _close_thesis_snapshots（非复刻 SQL）：清仓 → closed、部分减仓保持。"""
        db, path = fresh_db
        self._insert_open(db)
        node = _make_node()
        portfolio = {"accounts": [{"positions": [{"symbol": "688598", "quantity": 100}]}]}
        # 部分减仓：卖 40，剩 60 → 保持 open
        node._close_thesis_snapshots(
            [_FakeAlloc(symbol="688598", quantity_delta=-40)], portfolio
        )
        assert _rows(path)[0][1] == 1
        assert _rows(path)[0][3] == "intact"
        # 清仓：卖 100，剩 0 → closed
        node._close_thesis_snapshots(
            [_FakeAlloc(symbol="688598", quantity_delta=-100)], portfolio
        )
        rows = _rows(path)
        assert rows[0][1] == 0
        assert rows[0][3] == "closed"

    def test_closed_transition(self, fresh_db):
        """_close_thesis_snapshots 清仓判定：UPDATE is_open=False + lifecycle_state=closed。"""
        db, path = fresh_db
        self._insert_open(db)
        with db.get_session() as session:
            session.query(HoldingThesisRecord).filter(
                HoldingThesisRecord.symbol == "688598",
                HoldingThesisRecord.is_open == True,  # noqa: E712
            ).update({"is_open": False, "lifecycle_state": "closed"})
            session.commit()
        rows = _rows(path)
        assert rows[0][1] == 0  # is_open False（派生一致性）
        assert rows[0][3] == "closed"

    def test_full_lifecycle_chain(self, fresh_db):
        """完整链：intact → drift_watch → thesis_broken → closed。"""
        db, path = fresh_db
        self._insert_open(db)
        node = _make_node()
        states = []

        def snap():
            states.append(_rows(path)[0][3])

        snap()  # intact
        with db.get_session() as session:
            rec = session.query(HoldingThesisRecord).filter_by(symbol="688598").first()
            rec.drift_watch_since = date.today()
            rec.lifecycle_state = "drift_watch"
            session.commit()
        snap()  # drift_watch
        node._mark_thesis_broken("688598")
        snap()  # thesis_broken
        with db.get_session() as session:
            session.query(HoldingThesisRecord).filter_by(symbol="688598").update(
                {"is_open": False, "lifecycle_state": "closed"})
            session.commit()
        snap()  # closed
        assert states == ["intact", "drift_watch", "thesis_broken", "closed"]


class TestAllPositions:
    """B1-b：_all_positions 多账户合并视图。"""

    def test_merge_multi_account_dedup(self):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        node = PortfolioDecisionNode.__new__(PortfolioDecisionNode)
        portfolio = {
            "accounts": [
                {"positions": [{"symbol": "688598", "quantity": 100},
                               {"symbol": "600000", "quantity": 200}]},
                {"positions": [{"symbol": "600000", "quantity": 300},  # 重复取先出现
                               {"symbol": "000001", "quantity": 400}]},
            ],
        }
        merged = node._all_positions(portfolio)
        assert [p["symbol"] for p in merged] == ["688598", "600000", "000001"]

    def test_single_account_equivalence(self):
        """单账户现状下与 accounts[0].positions 逐字节等价。"""
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        node = PortfolioDecisionNode.__new__(PortfolioDecisionNode)
        positions = [{"symbol": "688598", "quantity": 100}]
        portfolio = {"accounts": [{"positions": positions}]}
        assert node._all_positions(portfolio) == positions

    def test_empty_portfolio(self):
        from src.got.nodes.portfolio_decision import PortfolioDecisionNode
        node = PortfolioDecisionNode.__new__(PortfolioDecisionNode)
        assert node._all_positions({}) == []
        assert node._all_positions({"accounts": []}) == []


def _make_node():
    """构造最小 PortfolioDecisionNode 实例（只跑 _mark_thesis_broken）。"""
    from src.got.nodes.portfolio_decision import PortfolioDecisionNode
    return PortfolioDecisionNode.__new__(PortfolioDecisionNode)


class _FakeAlloc:
    """最小 TradeAllocation 替身（_close_thesis_snapshots 只读 symbol/quantity_delta/action）。"""

    def __init__(self, symbol: str, quantity_delta: float, action: str = "sell"):
        self.symbol = symbol
        self.quantity_delta = quantity_delta
        self.action = action
