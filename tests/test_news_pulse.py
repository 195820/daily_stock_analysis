# -*- coding: utf-8 -*-
"""批次 D：news_pulse 持仓异动归因观测单测（纯离线，mock 取数/LLM/上下文）。

覆盖：
- 未启用 → None、零调用
- 无持仓 / 无触发（涨幅未过阈）→ None
- 触发标的 → 结构化归因解析 + 产物落盘（tmp 归档目录）
- max_stocks 硬上限
- LLM 失败 → 诚实 error 留痕，不抛
- 口径标注（盘中 vs 收盘）
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.got.news_pulse import run_news_pulse, NEWS_PULSE_ARTIFACT


def _scoring():
    return [
        SimpleNamespace(symbol="600000.SH", name="浦发银行", is_holding=True, sector_name="银行"),
        SimpleNamespace(symbol="600001.SH", name="试点持仓", is_holding=True, sector_name="半导体"),
        SimpleNamespace(symbol="000002.SZ", name="非持仓候选", is_holding=False, sector_name="银行"),
    ]


def _manager(changes):
    m = MagicMock()
    m.get_stock_quick_change.return_value = changes
    return m


def _llm(payload):
    llm = MagicMock()
    llm.call_text.return_value = SimpleNamespace(
        content=json.dumps(payload, ensure_ascii=False), usage={}, model="test"
    )
    return llm


def _ctx():
    news = SimpleNamespace(major_events=[
        SimpleNamespace(title="浦发银行发布业绩", sentiment="negative",
                        affected_stocks=["浦发银行"], affected_sectors=["银行"]),
    ])
    sector = SimpleNamespace(confirmed_sectors=[], active_mainline=[])
    market = SimpleNamespace(timeframe_got=SimpleNamespace(regime="mild_trending"))
    decision = SimpleNamespace(unified_scoring=_scoring())
    return decision, news, sector, market


def _attr_payload():
    return {
        "one_line": "情绪面主导",
        "attribution_table": [{"explanation": "板块拖累", "estimated_contrib": "-5%",
                                "confidence": "中", "kept": True}],
        "nature": {"type": "情绪波动", "breakdown": "情绪70%/基本面0%"},
        "counter_evidence": ["同板块其它成员普跌"],
    }


@pytest.fixture
def archive_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_disabled_returns_none(archive_dir):
    decision, news, sector, market = _ctx()
    rec = run_news_pulse(
        decision=decision, news_ctx=news, sector_ctx=sector, market_ctx=market,
        target_date=date(2026, 10, 11), manager=_manager({}), llm=_llm({}),
        enabled=False, threshold=7.0, max_stocks=3,
    )
    assert rec is None
    assert not (archive_dir / "data" / "got_daily").exists()


def test_no_trigger_returns_none(archive_dir):
    decision, news, sector, market = _ctx()
    changes = {"600000.SH": {"change_pct": -1.0}, "600001.SH": {"change_pct": 0.5}}
    with patch("src.got.archive._is_bar_complete", return_value=True):
        rec = run_news_pulse(
            decision=decision, news_ctx=news, sector_ctx=sector, market_ctx=market,
            target_date=date(2026, 10, 11), manager=_manager(changes), llm=_llm(_attr_payload()),
            enabled=True, threshold=7.0, max_stocks=3,
        )
    assert rec is None


def test_trigger_parsed_and_written(archive_dir):
    decision, news, sector, market = _ctx()
    changes = {"600000.SH": {"change_pct": -9.0}, "600001.SH": {"change_pct": 8.0},
               "000002.SZ": {"change_pct": -6.0}}
    llm = _llm(_attr_payload())
    persist = MagicMock()
    with patch("src.got.archive._is_bar_complete", return_value=True):
        rec = run_news_pulse(
            decision=decision, news_ctx=news, sector_ctx=sector, market_ctx=market,
            target_date=date(2026, 10, 11), manager=_manager(changes), llm=llm,
            enabled=True, threshold=7.0, max_stocks=3, persist_usage=persist,
        )
    assert rec is not None
    assert rec["triggered"] == 2  # 两只持仓均 |涨跌|>=7
    assert rec["caliber"] == "收盘归因口径"
    assert rec["items"][0]["change_pct"] == -9.0  # 按 |涨跌| 降序
    assert rec["items"][0]["attribution"]["nature"]["type"] == "情绪波动"
    assert rec["llm_calls"] == 2
    assert persist.called
    # 产物落盘到 tmp 归档目录
    art = archive_dir / "data" / "got_daily" / "20261011" / NEWS_PULSE_ARTIFACT
    assert art.exists()
    assert json.loads(art.read_text(encoding="utf-8"))["triggered"] == 2


def test_max_stocks_cap(archive_dir):
    decision, news, sector, market = _ctx()
    # 增加持仓触发数
    extra = _scoring() + [
        SimpleNamespace(symbol=f"60000{i}.SH", name=f"持{i}", is_holding=True, sector_name="钢铁")
        for i in range(2, 6)
    ]
    decision.unified_scoring = extra
    changes = {s.symbol: {"change_pct": -10.0} for s in extra if s.is_holding}
    with patch("src.got.archive._is_bar_complete", return_value=True):
        rec = run_news_pulse(
            decision=decision, news_ctx=news, sector_ctx=sector, market_ctx=market,
            target_date=date(2026, 10, 11), manager=_manager(changes), llm=_llm(_attr_payload()),
            enabled=True, threshold=7.0, max_stocks=3,
        )
    assert rec["triggered"] == 3


def test_llm_failure_degrades(archive_dir):
    decision, news, sector, market = _ctx()
    changes = {"600000.SH": {"change_pct": -9.0}, "600001.SH": {"change_pct": 1.0}}
    llm = MagicMock()
    llm.call_text.side_effect = RuntimeError("llm down")
    with patch("src.got.archive._is_bar_complete", return_value=False):
        rec = run_news_pulse(
            decision=decision, news_ctx=news, sector_ctx=sector, market_ctx=market,
            target_date=date(2026, 10, 11), manager=_manager(changes), llm=llm,
            enabled=True, threshold=7.0, max_stocks=3,
        )
    assert rec["caliber"].startswith("盘中预警口径")
    item = rec["items"][0]
    assert item["attribution"] is None
    assert "RuntimeError" in item["error"]


def test_batch_quote_failure_no_trigger(archive_dir):
    decision, news, sector, market = _ctx()
    mgr = MagicMock()
    mgr.get_stock_quick_change.side_effect = OSError("net down")
    with patch("src.got.archive._is_bar_complete", return_value=True):
        rec = run_news_pulse(
            decision=decision, news_ctx=news, sector_ctx=sector, market_ctx=market,
            target_date=date(2026, 10, 11), manager=mgr, llm=_llm(_attr_payload()),
            enabled=True, threshold=7.0, max_stocks=3,
        )
    # 缺源 → 无触发集合 → None，且不调用 LLM
    assert rec is None
