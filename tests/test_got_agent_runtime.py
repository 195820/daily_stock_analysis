# -*- coding: utf-8 -*-
"""GOT run-level semantic runtime tests (2026-10-30 consolidation)."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, RootModel

from src.got.agent_runtime import GOTAgentRuntime


class _Out(BaseModel):
    value: str
    evidence_ids: list[str] = []


class _Item(BaseModel):
    name: str
    evidence_ids: list[str] = []


class _Items(RootModel[list[_Item]]):
    pass


def _resp(content, *, provider="openai", finish="stop", model="test-model"):
    return SimpleNamespace(
        content=content, provider=provider, model=model,
        finish_reason=finish,
        usage={"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
    )


class _FakeAdapter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def call_text(self, messages, **kwargs):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _runtime(tmp_path, responses, **kwargs):
    return GOTAgentRuntime(
        date(2026, 10, 29),
        adapter=_FakeAdapter(responses),
        archive_root=tmp_path,
        max_retries=kwargs.pop("max_retries", 3),
        retry_backoff_s=0,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_steps_chain_and_usage(tmp_path, monkeypatch):
    usage_rows = []
    monkeypatch.setattr(
        "src.storage.persist_llm_usage",
        lambda **kw: usage_rows.append(kw),
    )
    rt = _runtime(tmp_path, [_resp('{"value": "root"}'), _resp('{"value": "child"}')])
    first = await rt.call_json(
        task_id="node6.parent", prompt="p1", output_model=_Out,
        temperature=0, max_tokens=20,
    )
    second = await rt.call_json(
        task_id="node6.child", prompt="p2", output_model=_Out,
        temperature=0, max_tokens=20, parent_step_ids=[first.step_id],
    )
    assert first.status == "ok" and second.status == "ok"
    assert second.parent_step_ids == [first.step_id]
    assert rt.get("node6.parent").step_id == first.step_id
    assert len(usage_rows) == 2
    assert usage_rows[0]["usage"]["total_tokens"] == 7
    assert usage_rows[0]["call_type"] == "node6.parent"
    assert first.model == "test-model" and first.tokens["total_tokens"] == 7
    assert first.prompt_sha256 and first.step_id.endswith(":001")


@pytest.mark.asyncio
async def test_length_fails_without_retry(tmp_path):
    adapter = _FakeAdapter([_resp("{\"value\": \"x\"}", finish="length"), _resp('{"value": "never"}')])
    rt = GOTAgentRuntime(
        date(2026, 10, 29), adapter=adapter, archive_root=tmp_path,
        max_retries=3, retry_backoff_s=0,
    )
    step = await rt.call_json(
        task_id="node6.semantic_glue", prompt="p", output_model=_Out,
        temperature=0, max_tokens=10,
    )
    assert step.status == "failed"
    assert "length" in (step.error or "")
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_empty_and_exception_retry_then_fail(tmp_path):
    adapter = _FakeAdapter([_resp(""), RuntimeError("boom"), _resp('{"value": "ok"}')])
    rt = GOTAgentRuntime(
        date(2026, 10, 29), adapter=adapter, archive_root=tmp_path,
        max_retries=2, retry_backoff_s=0,
    )
    step = await rt.call_json(
        task_id="node7.sector_verify", prompt="p", output_model=_Out,
        temperature=0, max_tokens=10,
    )
    assert step.status == "ok"
    assert adapter.calls == 3
    attempts = rt.steps_for("node7.sector_verify")
    assert [s.attempt for s in attempts] == [1, 2, 3]
    assert attempts[0].status == "failed" and attempts[1].status == "failed"


@pytest.mark.asyncio
async def test_forged_evidence_is_rejected(tmp_path):
    rt = _runtime(
        tmp_path,
        [_resp('{"value": "x", "evidence_ids": ["ev_forged"]}')],
        max_retries=0,
    )
    real = rt.register_evidence(kind="news", source="n1", locator="e1", content={"t": "x"})
    step = await rt.call_json(
        task_id="node6.semantic_glue", prompt="p", output_model=_Out,
        temperature=0, max_tokens=10, evidence_refs=[real],
    )
    assert step.status == "failed"
    assert "unknown evidence_id" in (step.error or "")


@pytest.mark.asyncio
async def test_legal_abstain_is_ok(tmp_path):
    rt = _runtime(
        tmp_path,
        [_resp(json.dumps([{"name": "x", "evidence_ids": []}], ensure_ascii=False))],
        max_retries=0,
    )
    step = await rt.call_json(
        task_id="narrative.identity", prompt="p", output_model=_Items,
        temperature=0, max_tokens=10,
    )
    assert step.status == "ok"
    assert step.output == [{"name": "x", "evidence_ids": []}]


@pytest.mark.asyncio
async def test_sync_call_inside_to_thread(tmp_path):
    rt = _runtime(tmp_path, [_resp("hello")])
    step = await asyncio.to_thread(
        rt.call_text_sync,
        task_id="node4.event_extraction",
        prompt="p",
        temperature=0,
        max_tokens=5,
    )
    assert step.status == "ok" and step.output == "hello"


@pytest.mark.asyncio
async def test_sync_call_refuses_nested_loop(tmp_path):
    rt = _runtime(tmp_path, [_resp("hello")])
    with pytest.raises(RuntimeError):
        rt.call_text_sync(task_id="node4.llm", prompt="p", temperature=0, max_tokens=5)


@pytest.mark.asyncio
async def test_flush_writes_run_card_and_steps(tmp_path):
    rt = _runtime(tmp_path, [_resp("hello")])
    await rt.call_text(task_id="node4.llm", prompt="p", temperature=0, max_tokens=5)
    path = rt.flush()
    assert path.name == "agent_run.json" and path.exists()
    card = json.loads(path.read_text(encoding="utf-8"))
    assert card["schema_version"] == "got-agent-runtime/v1"
    assert card["run_id"].startswith("got_20261029_")
    assert card["total_tokens"] == 7
    steps = (path.parent / "agent_steps.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(steps) == 1
    assert json.loads(steps[0])["task_id"] == "node4.llm"


@pytest.mark.asyncio
async def test_replay_missing_fixture_skips(tmp_path):
    rt = GOTAgentRuntime(date(2026, 10, 29), archive_root=tmp_path, replay=True)
    step = await rt.call_text(
        task_id="node6.semantic_glue", prompt="p", temperature=0, max_tokens=5,
    )
    assert step.status == "skipped"
    assert step.error == "missing_fixture"


def test_record_completion_mirrors_without_duplicate_usage(tmp_path, monkeypatch):
    usage_rows = []
    monkeypatch.setattr("src.storage.persist_llm_usage", lambda **kw: usage_rows.append(kw))
    rt = _runtime(tmp_path, [])
    step = rt.record_completion(
        task_id="node7.t1",
        messages=[{"role": "user", "content": "x"}],
        response=_resp("verdict"),
    )
    assert step.status == "ok" and step.task_id == "node7.t1"
    assert usage_rows == [], "runner 已落 usage，镜像步骤不得重复写"
    assert rt.get("node7.t1") is step
