# -*- coding: utf-8 -*-
"""Phase 6-1 盘中 0→1 确认闸门：规则判定 + 产物卫生 + 归档存活测试。

docs/got/plans/Phase6-1_盘中闸门影子期实施稿_20261015.md §4 #10
"""

import json
import shutil
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.got.nodes.intraday_confirm as mod
from src.got.nodes.intraday_confirm import (
    IntradayConfirmNode,
    _geometric_mean,
    _order_book_imbalance,
    INTRADAY_SESSIONS,
)
from src.got.catalyst_config import INTRADAY_GATE_SHADOW


# ---------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------

def mk_quote(price=10.5, open_price=10.2, pre_close=10.0, high=10.8, low=10.0,
             change_pct=None, bid_volumes=None, ask_volumes=None,
             provider_timestamp="2026-10-15 09:30:00"):
    if change_pct is None and price is not None and pre_close is not None:
        change_pct = (price - pre_close) / pre_close * 100
    return SimpleNamespace(
        price=price, open_price=open_price, pre_close=pre_close, high=high, low=low,
        change_pct=change_pct, bid_volumes=bid_volumes, ask_volumes=ask_volumes,
        provider_timestamp=provider_timestamp,
    )


class FakeManager:
    def __init__(self, quotes=None):
        self.quotes = quotes or {}

    def get_realtime_quote(self, code):
        return self.quotes.get(code)

    def get_minute_data(self, code):
        return None


@pytest.fixture
def node():
    return IntradayConfirmNode(manager=None)


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setattr(mod, "INTRADAY_GATE_ENABLED", True)
    monkeypatch.setattr(mod, "INTRADAY_GATE_SHADOW", True)


@pytest.fixture
def day_dir(tmp_path, monkeypatch):
    """把 GOT_DAILY_ROOT 指到 tmp，且塞一份当日 portfolio_decision.json。"""
    root = tmp_path / "got_daily"
    d = root / "20261015"
    d.mkdir(parents=True)
    (d / "portfolio_decision.json").write_text(json.dumps({
        "unified_scoring": [
            {"symbol": "600519", "composite_score": 90},
            {"symbol": "000001", "composite_score": 80},
        ]
    }), encoding="utf-8")
    monkeypatch.setattr(mod, "GOT_DAILY_ROOT", root)
    return d


# ---------------------------------------------------------------------
# 开关缺省（一票否决线）
# ---------------------------------------------------------------------

def test_switch_defaults():
    """缺省：ENABLED=false / SHADOW=true；6-4 转正前 SHADOW 禁 false。"""
    import importlib
    import src.got.catalyst_config as cfg
    importlib.reload(cfg)
    assert cfg.INTRADAY_GATE_ENABLED is False
    assert cfg.INTRADAY_GATE_SHADOW is True
    assert cfg.INTRADAY_GATE_TOPN == 8
    assert INTRADAY_GATE_SHADOW is True


def test_run_session_refuses_when_disabled():
    node = IntradayConfirmNode(FakeManager())
    assert node.run_session("20261015", "1125") is None


def test_run_session_refuses_shadow_off(monkeypatch):
    """SHADOW=false → 拒绝执行（写侧防御性拦载，一票否决线兜底）。"""
    monkeypatch.setattr(mod, "INTRADAY_GATE_ENABLED", True)
    monkeypatch.setattr(mod, "INTRADAY_GATE_SHADOW", False)
    node = IntradayConfirmNode(FakeManager())
    assert node.run_session("20261015", "1125") is None


def test_run_session_unknown_session_raises():
    node = IntradayConfirmNode(FakeManager())
    with pytest.raises(ValueError):
        node.run_session("20261015", "1000")


# ---------------------------------------------------------------------
# judge_one 三时点判定路径
# ---------------------------------------------------------------------

def test_0926_gap_below_threshold_waits(node):
    """0926 高开 < θ_gap → wait（不及预期，不追）。"""
    v = node.judge_one("600519", "0926", mk_quote(open_price=10.1, pre_close=10.0))
    assert v.verdict == "wait"
    assert "不及预期" in v.reason
    assert v.evidence["gap_pct"] == pytest.approx(1.0)
    assert v.shadow is True


def test_1125_confirm_with_secondary_signal(node):
    """1125 拉升 + 委比佐证 → confirm。"""
    q = mk_quote(price=10.6, pre_close=10.0, high=10.65,
                 bid_volumes=[100.0] * 5, ask_volumes=[10.0] * 5)
    v = node.judge_one("600519", "1125", q)
    assert v.verdict == "confirm"
    assert v.confidence > 0
    assert "拉升" in v.reason


def test_1125_pullback_reject_precedes_confirm(node):
    """1125 冲高回落 > θ_pull → reject 优先于 confirm（reject 优先级更高）。"""
    # price 10.4 → change 4%；high 10.9 → high_pct 9% → pullback 5pp > 2
    q = mk_quote(price=10.4, pre_close=10.0, high=10.9, change_pct=4.0,
                 bid_volumes=[100.0] * 5, ask_volumes=[10.0] * 5)
    v = node.judge_one("600519", "1125", q)
    assert v.verdict == "reject"
    assert "诱多" in v.reason


def test_1445_turn_down_rejects(node):
    """1445 转跌 → reject（快刀斩乱麻）。"""
    v = node.judge_one("600519", "1445", mk_quote(price=9.9, pre_close=10.0,
                                                  change_pct=-1.0, high=10.5))
    assert v.verdict == "reject"


def test_1445_strong_without_secondary_waits(node):
    """1445 强势但无分时/五档佐证 → 不强行 confirm（共识不成立）。"""
    v = node.judge_one("600519", "1445", mk_quote(price=10.6, pre_close=10.0,
                                                  high=10.65, change_pct=6.0))
    assert v.verdict == "wait"


def test_1445_strong_with_imbalance_confirms(node):
    """1445 强势 + 委比佐证 → confirm。"""
    v = node.judge_one("600519", "1445", mk_quote(price=10.6, pre_close=10.0,
                                                  high=10.65, change_pct=6.0,
                                                  bid_volumes=[100.0] * 5, ask_volumes=[10.0] * 5))
    assert v.verdict == "confirm"


def test_blank_quote_defaults_wait(node):
    """行情全 None → 默认 wait，evidence 允许为空（I1 分层）。"""
    q = mk_quote(price=None, open_price=None, pre_close=None, high=None, low=None,
                 change_pct=None, provider_timestamp=None)
    v = node.judge_one("600519", "1125", q)
    assert v.verdict == "wait"


def test_default_session_wait_for_0926_high_gap(node):
    """0926 高开达标但不构成 confirm（0907 §5：0→1 不在竞价拍板）→ wait。"""
    q = mk_quote(open_price=10.5, pre_close=10.0, high=10.6,
                 bid_volumes=[100.0] * 5, ask_volumes=[10.0] * 5)
    v = node.judge_one("600519", "0926", q)
    assert v.verdict == "wait"
    assert v.evidence["order_book_imbalance"] > 0.15


# ---------------------------------------------------------------------
# 判据函数
# ---------------------------------------------------------------------

def test_geometric_mean_penalizes_extremes():
    assert _geometric_mean([0.6, 0.75]) == pytest.approx((0.6 * 0.75) ** 0.5)
    assert _geometric_mean([]) == 0.0
    assert _geometric_mean([0.0, 0.0]) == 0.0
    assert _geometric_mean([1.0]) == pytest.approx(1.0)
    # 几何平均 <= 算术平均
    vals = [0.2, 0.9]
    assert _geometric_mean(vals) < sum(vals) / len(vals)


def test_order_book_imbalance():
    assert _order_book_imbalance(SimpleNamespace(bid_volumes=[10.0], ask_volumes=[10.0])) == 0.0
    assert _order_book_imbalance(SimpleNamespace(bid_volumes=[15.0], ask_volumes=[5.0])) == pytest.approx(0.5)
    assert _order_book_imbalance(SimpleNamespace(bid_volumes=None, ask_volumes=[5.0])) is None
    assert _order_book_imbalance(SimpleNamespace(bid_volumes=[], ask_volumes=[])) is None


def test_sessions_registry():
    assert set(INTRADAY_SESSIONS) == {"0926", "1125", "1445"}


# ---------------------------------------------------------------------
# run_session 端到端（mock manager）+ 产物卫生
# ---------------------------------------------------------------------

def test_run_session_end_to_end_and_artifacts(gate_on, day_dir):
    quotes = {
        "600519": mk_quote(price=10.6, pre_close=10.0, high=10.65,
                           bid_volumes=[100.0] * 5, ask_volumes=[10.0] * 5),
        "000001": mk_quote(price=10.4, pre_close=10.0, high=10.9, change_pct=4.0),
    }
    node = IntradayConfirmNode(FakeManager(quotes))
    rec = node.run_session("20261015", "1125")
    assert rec is not None
    assert rec.candidates_polled == 2
    assert len(rec.verdicts) == 2
    assert rec.i1_coverage == 1.0
    assert rec.i2_confirm_ratio == pytest.approx(0.5)

    vp = day_dir / "intraday" / "1125" / "verdicts.json"
    sp = day_dir / "intraday" / "summary.json"
    assert vp.exists() and sp.exists()

    raw = vp.read_bytes()
    b"\x00" not in raw
    raw.decode("utf-8")  # UTF-8 卫生
    data = json.loads(raw)
    assert data["session"] == "1125"

    summary = json.loads(sp.read_text(encoding="utf-8"))
    assert summary["record"]["sessions_run"] == ["1125"]


def test_run_session_missing_quote_marks_data_degraded(gate_on, day_dir):
    """行情不可达 → wait + 数据降级注记，evidence 空（I1 分层）。"""
    node = IntradayConfirmNode(FakeManager({}))  # 无任何 quote
    rec = node.run_session("20261015", "1125")
    assert rec is not None and rec.candidates_polled == 2
    assert all(v.verdict == "wait" for v in rec.verdicts)
    assert rec.i1_coverage == 0.0


def test_run_session_no_candidates_returns_none(gate_on, day_dir):
    (day_dir / "portfolio_decision.json").write_text('{"unified_scoring": []}', encoding="utf-8")
    node = IntradayConfirmNode(FakeManager())
    assert node.run_session("20261015", "1125") is None


def test_summary_merges_sessions(gate_on, day_dir):
    """先跑 0926 再跑 1445 → summary 合并两个时点。"""
    node = IntradayConfirmNode(FakeManager({"600519": mk_quote(price=10.6, pre_close=10.0),
                                            "000001": mk_quote(price=10.05, pre_close=10.0)}))
    node.run_session("20261015", "0926")
    node.run_session("20261015", "1445")
    summary = json.loads((day_dir / "intraday" / "summary.json").read_text(encoding="utf-8"))
    assert sorted(summary["record"]["sessions_run"]) == ["0926", "1445"]


def test_load_candidates_topn_order(day_dir):
    """候选按 composite_score 降序截 TopN。"""
    (day_dir / "portfolio_decision.json").write_text(json.dumps({
        "unified_scoring": [
            {"symbol": "600001", "composite_score": 50},
            {"symbol": "600003", "composite_score": 90},
            {"symbol": "600002", "composite_score": 70},
        ]
    }), encoding="utf-8")
    node = IntradayConfirmNode(FakeManager())
    rows = node.load_candidates("20261015", topn=2)
    assert [r["symbol"] for r in rows] == ["600003", "600002"]


# ---------------------------------------------------------------------
# archive：intraday/ 原子替换存活 + loader 往返
# ---------------------------------------------------------------------

class _FakeCtx:
    def __init__(self, name):
        self._n = name

    def model_dump(self, mode="python"):
        return {"_fake": self._n}


def test_archive_preserves_intraday_dir(tmp_path, monkeypatch):
    """日频归档原子替换不得抹掉当日盘中产物（backlog :663 同坑搬运分支）。"""
    import src.got.archive as arch

    base = tmp_path / "got_daily"
    d = base / "20261015"
    intraday = d / "intraday" / "1125"
    intraday.mkdir(parents=True)
    (intraday / "verdicts.json").write_text('{"session": "1125"}', encoding="utf-8")

    ctxs = {k: _FakeCtx(k) for k in ("market", "sector", "capital", "news", "sentiment", "unified")}
    arch.archive_daily_run(
        market_ctx=ctxs["market"], sector_ctx=ctxs["sector"], capital_ctx=ctxs["capital"],
        news_ctx=ctxs["news"], sentiment_ctx=ctxs["sentiment"], unified_ctx=ctxs["unified"],
        run_date=date(2026, 10, 15), base_dir=base, use_mock=True,
    )
    assert (d / "intraday" / "1125" / "verdicts.json").exists()
    # 常规节点文件照常落
    assert (d / "market.json").exists()


def test_archive_preserves_news_retrieval_trace(tmp_path):
    """日频归档原子替换不得抹掉 Node 4 分层粗筛 trace（与 intraday 同坑搬运）。"""
    import src.got.archive as arch

    base = tmp_path / "got_daily"
    d = base / "20261015"
    d.mkdir(parents=True)
    (d / "news_retrieval_trace.json").write_text(
        '{"cluster_count": 3, "index_mode": "embedding"}', encoding="utf-8",
    )

    ctxs = {k: _FakeCtx(k) for k in ("market", "sector", "capital", "news", "sentiment", "unified")}
    arch.archive_daily_run(
        market_ctx=ctxs["market"], sector_ctx=ctxs["sector"], capital_ctx=ctxs["capital"],
        news_ctx=ctxs["news"], sentiment_ctx=ctxs["sentiment"], unified_ctx=ctxs["unified"],
        run_date=date(2026, 10, 15), base_dir=base, use_mock=True,
    )
    payload = json.loads((d / "news_retrieval_trace.json").read_text(encoding="utf-8"))
    assert payload["cluster_count"] == 3
    assert (d / "market.json").exists()


def test_load_intraday_shadow_record_roundtrip(tmp_path, monkeypatch):
    import src.got.archive as arch
    from src.got.schemas import IntradayShadowRecord, IntradayVerdict

    base = tmp_path / "got_daily"
    d = base / "20261015" / "intraday"
    d.mkdir(parents=True)
    rec = IntradayShadowRecord(
        date="20261015", sessions_run=["1125"], candidates_polled=1,
        verdicts=[IntradayVerdict(code="600519", trigger_time="1125", verdict="confirm")],
    )
    (d / "summary.json").write_text(json.dumps(
        {"written_at": "x", "record": rec.model_dump()}, ensure_ascii=False), encoding="utf-8")

    loaded = arch.load_intraday_shadow_record(date(2026, 10, 15), base_dir=base)
    assert loaded is not None
    assert loaded.candidates_polled == 1 and loaded.sessions_run == ["1125"]
    # 缺日 → None
    assert arch.load_intraday_shadow_record(date(2026, 10, 1), base_dir=base) is None


# ---------------------------------------------------------------------
# realtime_types：五档字段 roundtrip
# ---------------------------------------------------------------------

def test_quote_order_book_fields_roundtrip():
    from data_provider.realtime_types import UnifiedRealtimeQuote

    q = UnifiedRealtimeQuote(code="600519")
    d = q.to_dict()
    assert "bid_prices" not in d and "ask_volumes" not in d  # None 字段不上报

    q2 = UnifiedRealtimeQuote(code="600519", bid_prices=[1.0, 2.0],
                              bid_volumes=[100.0], ask_prices=[1.5], ask_volumes=[50.0])
    d2 = q2.to_dict()
    assert d2["bid_prices"] == [1.0, 2.0]
    assert d2["bid_volumes"] == [100.0]
    assert d2["ask_prices"] == [1.5]
    assert d2["ask_volumes"] == [50.0]
