# -*- coding: utf-8 -*-
"""T2 叙事校验影子产物：shadow_t2.json 的落盘与 shadow 门控。"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

from src.got import catalyst_calendar as cc
from src.got.catalyst_calendar import CatalystCalendarBuilder
from src.got.schemas import CatalystEvent


def _builder() -> CatalystCalendarBuilder:
    builder = CatalystCalendarBuilder()
    builder._run_date = date(2026, 11, 4)
    builder._critic_drop_ids = {"evt_drop"}
    return builder


def test_t2_shadow_writes_verdicts(tmp_path, monkeypatch):
    real_path = cc.Path

    def fake_path(value):
        path = real_path(value)
        if str(value).startswith("data/got_daily"):
            return tmp_path / path
        return path

    monkeypatch.setattr(cc, "Path", fake_path)
    monkeypatch.setattr("src.got.catalyst_config.NARRATIVE_CRITIC_SHADOW", True)

    verdicts = [
        {"event_id": "evt_keep", "verdict": "keep", "reason": "有证据", "checks": {}},
        {"event_id": "evt_drop", "verdict": "drop", "reason": "时态错误", "checks": {"tense": "fail"}},
    ]
    _builder()._write_t2_shadow([], verdicts, executed=True)

    artifact = tmp_path / "data/got_daily/20261104/shadow_t2.json"
    record = json.loads(artifact.read_text(encoding="utf-8"))
    assert record["line"] == "t2"
    assert record["mode"] == "shadow"
    assert record["executed"] is True
    assert record["drop_ids"] == ["evt_drop"]
    assert [v["verdict"] for v in record["verdicts"]] == ["keep", "drop"]


def test_t2_shadow_respects_off_switch(tmp_path, monkeypatch):
    real_path = cc.Path

    def fake_path(value):
        path = real_path(value)
        if str(value).startswith("data/got_daily"):
            return tmp_path / path
        return path

    monkeypatch.setattr(cc, "Path", fake_path)
    monkeypatch.setattr("src.got.catalyst_config.NARRATIVE_CRITIC_SHADOW", False)

    _builder()._write_t2_shadow([], [], executed=True)
    assert not (tmp_path / "data/got_daily/20261104/shadow_t2.json").exists()


def test_critic_verify_writes_t2_artifact(tmp_path, monkeypatch):
    real_path = cc.Path

    def fake_path(value):
        path = real_path(value)
        if str(value).startswith("data/got_daily"):
            return tmp_path / path
        return path

    monkeypatch.setattr(cc, "Path", fake_path)
    monkeypatch.setattr("src.got.catalyst_config.NARRATIVE_CRITIC_SHADOW", True)

    builder = _builder()

    async def fake_call(*args, **kwargs):
        return json.dumps([
            {"event_id": "evt_drop", "verdict": "drop", "reason": "时态错误",
             "checks": {"tense": "fail"}},
        ])

    monkeypatch.setattr(builder, "_call_llm_async", fake_call)
    event = CatalystEvent(
        event_id="evt_drop",
        event_date=date(2026, 11, 4),
        event_type="narrative",
        title="测试事件",
        sector="测试板块",
        source="llm_narrative",
        source_news="测试新闻",
    )
    result = asyncio.run(builder._critic_verify_narrative([event], []))
    assert result == [event]  # shadow keeps events

    artifact = tmp_path / "data/got_daily/20261104/shadow_t2.json"
    record = json.loads(artifact.read_text(encoding="utf-8"))
    assert record["executed"] is True
    assert record["drop_ids"] == ["evt_drop"]
    assert record["verdicts"][0]["checks"] == {"tense": "fail"}
