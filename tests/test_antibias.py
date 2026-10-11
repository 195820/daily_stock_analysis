# -*- coding: utf-8 -*-
"""批次 B：antibias 轻量反偏见层单测（一期纯影子，mock LLM）。

覆盖：shadow 关闭→None；无 actionable 板块→None；正常→产物落盘 + 板块对齐 +
红线枚举校验（越界标注）；LLM 失败→error 留痕（供"报错率"门槛计数）；max_sectors 上限。
"""
from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.got.antibias import ANTIBIAS_ARTIFACT, run_antibias, RED_LINES


def _scoring():
    return [
        SimpleNamespace(sector_name="半导体", is_holding=True, action_recommendation="hold"),
        SimpleNamespace(sector_name="AI算力", is_holding=False, action_recommendation="buy_trend"),
        SimpleNamespace(sector_name="煤炭", is_holding=False, action_recommendation="avoid"),
        SimpleNamespace(sector_name="其他", is_holding=True, action_recommendation="hold"),
    ]


def _decision():
    return SimpleNamespace(unified_scoring=_scoring())


def _llm(payload):
    llm = MagicMock()
    llm.call_text.return_value = SimpleNamespace(
        content=json.dumps(payload, ensure_ascii=False), usage={}, model="test"
    )
    return llm


@pytest.fixture
def archive_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_shadow_off_returns_none(archive_dir):
    rec = run_antibias(decision=_decision(), target_date=date(2026, 10, 11),
                       llm=_llm([]), shadow=False, max_sectors=8)
    assert rec is None
    assert not (archive_dir / "data").exists()


def test_no_actionable_returns_none(archive_dir):
    d = SimpleNamespace(unified_scoring=[
        SimpleNamespace(sector_name="煤炭", is_holding=False, action_recommendation="avoid")])
    rec = run_antibias(decision=d, target_date=date(2026, 10, 11),
                       llm=_llm([]), shadow=True, max_sectors=8)
    assert rec is None


def test_normal_written_and_aligned(archive_dir):
    payload = [
        {"sector": "半导体", "counter_thesis": "国产替代进度不及预期", "red_line_hit": "追高位置"},
        {"sector": "AI算力", "counter_thesis": "海外云厂商 capex 指引下修", "red_line_hit": "单维高分独裁"},
    ]
    persist = MagicMock()
    rec = run_antibias(decision=_decision(), target_date=date(2026, 10, 11),
                       llm=_llm(payload), shadow=True, max_sectors=8, persist_usage=persist)
    assert rec["sectors_evaluated"] == 2  # 半导体 + AI算力（煤炭 avoid / 其他 被排除）
    assert rec["counter_thesis_nonempty"] == 2
    assert rec["mode"] == "shadow"
    by = {it["sector"]: it for it in rec["items"]}
    assert by["半导体"]["red_line_hit"] == "追高位置"
    assert "煤炭" not in by and "其他" not in by
    assert persist.called
    art = archive_dir / "data" / "got_daily" / "20261011" / ANTIBIAS_ARTIFACT
    assert art.exists()
    assert json.loads(art.read_text(encoding="utf-8"))["line"] == "antibias"


def test_red_line_out_of_enum_flagged(archive_dir):
    payload = [{"sector": "半导体", "counter_thesis": "原因A", "red_line_hit": "瞎写的档"}]
    # 只保留一个 actionable 板块避免对齐缺失
    d = SimpleNamespace(unified_scoring=[
        SimpleNamespace(sector_name="半导体", is_holding=True, action_recommendation="hold")])
    rec = run_antibias(decision=d, target_date=date(2026, 10, 11),
                       llm=_llm(payload), shadow=True, max_sectors=8)
    item = rec["items"][0]
    assert item["red_line_hit"].startswith("越界:")
    assert item["red_line_hit"] not in RED_LINES


def test_llm_failure_records_error(archive_dir):
    llm = MagicMock()
    llm.call_text.side_effect = RuntimeError("llm down")
    rec = run_antibias(decision=_decision(), target_date=date(2026, 10, 11),
                       llm=llm, shadow=True, max_sectors=8)
    assert rec is not None and rec["llm_error"].startswith("RuntimeError")
    # 失败也落产物（供"报错率>10%"转正门槛计数）
    art = archive_dir / "data" / "got_daily" / "20261011" / ANTIBIAS_ARTIFACT
    assert art.exists()


def test_max_sectors_cap(archive_dir):
    d = SimpleNamespace(unified_scoring=[
        SimpleNamespace(sector_name=f"板块{i}", is_holding=True, action_recommendation="hold")
        for i in range(10)
    ])
    rec = run_antibias(decision=d, target_date=date(2026, 10, 11),
                       llm=_llm([]), shadow=True, max_sectors=3)
    assert rec["sectors_evaluated"] == 3
