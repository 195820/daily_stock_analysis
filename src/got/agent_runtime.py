# -*- coding: utf-8 -*-
"""GOT run-level semantic runtime.

One runtime per GOT run. It is the single place where GOT semantic LLM calls
are issued, evidence is registered, and per-attempt provenance is recorded.

Design constraints (docs/got/plans + 2026-10-30 consolidation plan):
- Deterministic GOT DAG stays authoritative; this module never scores, never
  allocates and never mutates decision inputs on its own.
- Fixed single-shot structured calls are plain completion calls, not ReAct.
  Only T1/T1-H keep their bounded tool loop (via an instrumented executor).
- Every real attempt is persisted to ``llm_usage`` and to a local trace so a
  final recommendation can be traced back to the exact prompt/model/evidence.
- Replay never writes production tables and never reaches the network.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from pydantic import BaseModel, RootModel

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "got-agent-runtime/v1"
DEFAULT_PROMPT_TEMPLATE_VERSION = "got-semantic/v1"
_TRACE_ENV = "GOT_LLM_TRACE_ENABLED"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceRef:
    """Opaque, content-addressed reference to one piece of input evidence."""

    evidence_id: str
    kind: str                 # news / event / market / capital / sentiment / thesis / web / memory
    source: str
    locator: str              # event_id / raw-news id / context JSON pointer / URL / thesis id
    content_hash: str
    observed_at: Optional[str] = None
    quote: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "source": self.source,
            "locator": self.locator,
            "content_hash": self.content_hash,
            "observed_at": self.observed_at,
            "quote": self.quote,
        }


@dataclass
class SemanticStep:
    """One semantic LLM attempt (success or failure) inside a GOT run."""

    run_id: str
    task_id: str
    step_id: str              # f"{task_id}:{sequence:03d}"
    parent_step_ids: list[str]
    status: str               # ok / skipped / degraded / failed
    model: str
    provider: str
    finish_reason: Optional[str]
    input_refs: list[str]
    evidence_refs: list[EvidenceRef]
    output: Any
    prompt_text: str
    raw_response: str
    prompt_sha256: str
    prompt_template_version: str
    attempt: int
    tokens: dict[str, int]
    latency_ms: int
    error: Optional[str] = None

    def to_dict(self, *, include_raw: bool = True) -> dict:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "parent_step_ids": list(self.parent_step_ids),
            "status": self.status,
            "model": self.model,
            "provider": self.provider,
            "finish_reason": self.finish_reason,
            "input_refs": list(self.input_refs),
            "evidence_refs": [e.to_dict() for e in self.evidence_refs],
            "output": self.output,
            "prompt_text": self.prompt_text if include_raw else "",
            "raw_response": self.raw_response if include_raw else "",
            "prompt_sha256": self.prompt_sha256,
            "prompt_template_version": self.prompt_template_version,
            "attempt": self.attempt,
            "tokens": dict(self.tokens),
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


class MissingFixtureError(RuntimeError):
    """Replay asked for a response that is not present in the trace fixtures."""


def ensure_runtime(owner: Any, run_date: Optional[date] = None) -> "GOTAgentRuntime":
    """Return ``owner.got_runtime``, lazily creating one for standalone node use."""
    runtime = getattr(owner, "got_runtime", None)
    if runtime is None:
        runtime = GOTAgentRuntime(run_date or date.today())
        owner.got_runtime = runtime
    return runtime


class GOTAgentRuntime:
    """Run-scoped semantic runtime shared by Node 1-7, T1/T1-H and replay."""

    def __init__(
        self,
        run_date: date,
        *,
        adapter: Any = None,
        archive_root: Optional[Path] = None,
        replay: bool = False,
        prompt_template_version: str = DEFAULT_PROMPT_TEMPLATE_VERSION,
        response_fixtures: Optional[dict[tuple[str, str], str]] = None,
        max_retries: int = 3,
        retry_backoff_s: float = 1.0,
        base_dir: Optional[Path] = None,
    ):
        self.run_date = run_date
        self.replay = bool(replay)
        self._adapter = adapter
        self._archive_root = Path(archive_root) if archive_root is not None else Path("data/got_daily")
        self.prompt_template_version = prompt_template_version
        self.response_fixtures = dict(response_fixtures or {})
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_s = max(0.0, float(retry_backoff_s))
        self.base_dir = Path(base_dir) if base_dir is not None else Path(".")
        self._lock = threading.RLock()
        self._steps: list[SemanticStep] = []
        self._by_task: dict[str, list[SemanticStep]] = {}
        self._evidence: dict[str, EvidenceRef] = {}
        self._seq = 0
        self._run_id = f"got_{run_date.strftime('%Y%m%d')}_{_utc_stamp()}_{os.getpid()}"
        self._flushed_path: Optional[Path] = None

    # ------------------------------------------------------------------
    # identity / evidence
    # ------------------------------------------------------------------
    @property
    def run_id(self) -> str:
        return self._run_id

    def register_evidence(
        self,
        *,
        kind: str,
        source: str,
        locator: str,
        content: Any,
        observed_at: Optional[str] = None,
        quote: Optional[str] = None,
    ) -> EvidenceRef:
        """Register one evidence item; returns a content-addressed reference."""
        payload = {
            "kind": str(kind or ""),
            "source": str(source or ""),
            "locator": str(locator or ""),
            "content": content,
        }
        content_hash = _sha256(_canonical_json(payload))
        evidence_id = f"ev_{content_hash[:16]}"
        ref = EvidenceRef(
            evidence_id=evidence_id,
            kind=str(kind or ""),
            source=str(source or ""),
            locator=str(locator or ""),
            content_hash=content_hash,
            observed_at=observed_at,
            quote=(quote[:500] if quote else None),
        )
        with self._lock:
            self._evidence.setdefault(evidence_id, ref)
        return ref

    def get_evidence(self, evidence_id: str) -> Optional[EvidenceRef]:
        with self._lock:
            return self._evidence.get(evidence_id)

    def evidence_refs(self) -> list[EvidenceRef]:
        with self._lock:
            return list(self._evidence.values())

    # ------------------------------------------------------------------
    # step registry
    # ------------------------------------------------------------------
    @property
    def steps(self) -> list[SemanticStep]:
        with self._lock:
            return list(self._steps)

    def get(self, task_id: str) -> Optional[SemanticStep]:
        """Latest successful step for ``task_id``, else the final failed attempt."""
        with self._lock:
            attempts = list(self._by_task.get(task_id) or [])
        for step in reversed(attempts):
            if step.status == "ok":
                return step
        return attempts[-1] if attempts else None

    def get_step(self, step_id: str) -> Optional[SemanticStep]:
        with self._lock:
            for step in self._steps:
                if step.step_id == step_id:
                    return step
        return None

    def steps_for(self, task_id: str) -> list[SemanticStep]:
        with self._lock:
            return list(self._by_task.get(task_id) or [])

    def require_ok(self, step: Optional[SemanticStep]) -> SemanticStep:
        if step is None or step.status != "ok":
            step_id = step.step_id if step is not None else "<missing>"
            detail = step.error if step is not None else "no step recorded"
            raise RuntimeError(f"GOT semantic step not ok: {step_id} ({detail})")
        return step

    # ------------------------------------------------------------------
    # calls
    # ------------------------------------------------------------------
    async def call_text(
        self,
        *,
        task_id: str,
        prompt: str,
        input_refs: Optional[list[str]] = None,
        evidence_refs: Optional[list[EvidenceRef]] = None,
        temperature: float,
        max_tokens: int,
        max_retries: Optional[int] = None,
        timeout: float = 60.0,
        parent_step_ids: Optional[list[str]] = None,
    ) -> SemanticStep:
        return await self._call(
            task_id=task_id,
            prompt=prompt,
            output_model=None,
            input_refs=input_refs,
            evidence_refs=evidence_refs,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
            timeout=timeout,
            parent_step_ids=parent_step_ids,
        )

    async def call_json(
        self,
        *,
        task_id: str,
        prompt: str,
        output_model: type[BaseModel],
        input_refs: Optional[list[str]] = None,
        evidence_refs: Optional[list[EvidenceRef]] = None,
        temperature: float,
        max_tokens: int,
        max_retries: Optional[int] = None,
        timeout: float = 60.0,
        parent_step_ids: Optional[list[str]] = None,
    ) -> SemanticStep:
        if not isinstance(output_model, type) or not issubclass(output_model, BaseModel):
            raise TypeError("output_model must be a pydantic BaseModel/RootModel subclass")
        return await self._call(
            task_id=task_id,
            prompt=prompt,
            output_model=output_model,
            input_refs=input_refs,
            evidence_refs=evidence_refs,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
            timeout=timeout,
            parent_step_ids=parent_step_ids,
        )

    def call_text_sync(self, **kwargs: Any) -> SemanticStep:
        """Run :meth:`call_text` from a thread without an event loop.

        GOT synchronous nodes execute inside ``asyncio.to_thread``; that worker
        thread has no running loop, so ``asyncio.run`` is safe. Refuse to nest.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.call_text(**kwargs))
        raise RuntimeError(
            "call_text_sync must not be used inside a running event loop; await call_text instead"
        )

    def call_json_sync(self, **kwargs: Any) -> SemanticStep:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.call_json(**kwargs))
        raise RuntimeError(
            "call_json_sync must not be used inside a running event loop; await call_json instead"
        )

    async def _call(
        self,
        *,
        task_id: str,
        prompt: str,
        output_model: Optional[type[BaseModel]],
        input_refs: Optional[list[str]],
        evidence_refs: Optional[list[EvidenceRef]],
        temperature: float,
        max_tokens: int,
        max_retries: Optional[int],
        timeout: float,
        parent_step_ids: Optional[list[str]],
    ) -> SemanticStep:
        prompt = prompt or ""
        prompt_hash = _sha256(prompt)
        refs = list(evidence_refs or [])
        ref_ids = {ref.evidence_id for ref in refs}
        retries = self.max_retries if max_retries is None else max(0, int(max_retries))
        last: Optional[SemanticStep] = None

        for attempt in range(retries + 1):
            step = self._new_step(
                task_id=task_id,
                parent_step_ids=list(parent_step_ids or []),
                input_refs=list(input_refs or []),
                evidence_refs=refs,
                prompt_text=prompt,
                prompt_sha256=prompt_hash,
                attempt=attempt + 1,
            )
            started = time.time()
            response = None
            try:
                response = await self._invoke(prompt, task_id, prompt_hash, temperature, max_tokens, timeout)
            except MissingFixtureError as exc:
                step.status = "skipped"
                step.error = "missing_fixture"
                step.latency_ms = int((time.time() - started) * 1000)
                self._record(step)
                logger.info("[GOTRuntime] %s skipped: %s", task_id, exc)
                return step
            except Exception as exc:  # noqa: BLE001 — provider/transport errors are recorded and retried
                step.status = "failed"
                step.error = f"{type(exc).__name__}: {exc}"
                step.latency_ms = int((time.time() - started) * 1000)
                self._record(step)
                last = step
                if attempt < retries:
                    await asyncio.sleep(self.retry_backoff_s)
                continue

            content = (getattr(response, "content", None) or "")
            provider = str(getattr(response, "provider", "") or "")
            step.model = str(getattr(response, "model", "") or "")
            step.provider = provider
            step.finish_reason = getattr(response, "finish_reason", None)
            step.raw_response = content
            usage = getattr(response, "usage", None) or {}
            step.tokens = {
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            }
            self._persist_usage(step)
            step.latency_ms = int((time.time() - started) * 1000)

            if step.finish_reason == "length":
                step.status = "failed"
                step.error = "finish_reason=length (truncated; no retry)"
                self._record(step)
                logger.warning("[GOTRuntime] %s truncated by max_tokens — fail fast", task_id)
                return step

            if provider == "error":
                step.status = "failed"
                step.error = f"provider error: {content[:300]}"
                self._record(step)
                last = step
                if attempt < retries:
                    await asyncio.sleep(self.retry_backoff_s)
                continue

            if not content.strip():
                step.status = "failed"
                step.error = "empty response"
                self._record(step)
                last = step
                if attempt < retries:
                    await asyncio.sleep(self.retry_backoff_s)
                continue

            if output_model is None:
                step.output = content.strip()
                step.status = "ok"
                self._record(step)
                return step

            # Structured path: parse/validate/closure are deterministic → no retry.
            try:
                payload = self._parse_structured(content, output_model)
                validated = output_model.model_validate(payload)
                dumped = validated.model_dump()
                self._check_evidence_closure(dumped, ref_ids)
            except Exception as exc:  # noqa: BLE001
                step.status = "failed"
                step.error = f"{type(exc).__name__}: {exc}"
                self._record(step)
                logger.warning("[GOTRuntime] %s structured parse/validate failed: %s", task_id, exc)
                return step

            step.output = dumped
            step.status = "ok"
            self._record(step)
            return step

        assert last is not None
        return last

    def record_completion(
        self,
        *,
        task_id: str,
        messages: Any,
        response: Any = None,
        error: Optional[BaseException] = None,
        latency_ms: int = 0,
        parent_step_ids: Optional[list[str]] = None,
    ) -> SemanticStep:
        """Record a tool-enabled completion executed by an instrumented adapter.

        T1/T1-H keep their bounded ReAct loop; this only mirrors each model call
        into the run trace and usage ledger.
        """
        prompt = _canonical_json(messages) if not isinstance(messages, str) else messages
        step = self._new_step(
            task_id=task_id,
            parent_step_ids=list(parent_step_ids or []),
            input_refs=[],
            evidence_refs=[],
            prompt_text=prompt,
            prompt_sha256=_sha256(prompt),
            attempt=1,
        )
        step.latency_ms = int(latency_ms or 0)
        if error is not None:
            step.status = "failed"
            step.error = f"{type(error).__name__}: {error}"
            self._record(step)
            return step
        step.model = str(getattr(response, "model", "") or "")
        step.provider = str(getattr(response, "provider", "") or "")
        step.finish_reason = getattr(response, "finish_reason", None)
        step.raw_response = str(getattr(response, "content", "") or "")
        usage = getattr(response, "usage", None) or {}
        step.tokens = {
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
        }
        step.output = {
            "content": step.raw_response,
            "tool_calls": [
                {"name": getattr(call, "name", ""), "arguments": getattr(call, "arguments", {})}
                for call in (getattr(response, "tool_calls", None) or [])
            ],
        }
        step.status = "ok"
        # runner 自身已对同一次调用写 llm_usage；此处仅镜像 trace，避免重复计费行
        self._record(step)
        return step

    async def _invoke(
        self,
        prompt: str,
        task_id: str,
        prompt_hash: str,
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> Any:
        if self.replay:
            fixture = self.response_fixtures.get((task_id, prompt_hash))
            if fixture is None:
                fixture = self.response_fixtures.get(task_id)
            if fixture is None and "*" in self.response_fixtures:
                fixture = self.response_fixtures.get("*")
            if fixture is None:
                raise MissingFixtureError(f"no fixture for {task_id}/{prompt_hash[:12]}")
            return _FixtureResponse(fixture)
        adapter = self._get_adapter()
        return await asyncio.to_thread(
            adapter.call_text,
            [{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    def _get_adapter(self) -> Any:
        if self._adapter is None:
            from src.agent.llm_adapter import LLMToolAdapter

            self._adapter = LLMToolAdapter()
        return self._adapter

    def _persist_usage(self, step: SemanticStep) -> None:
        if self.replay:
            return
        try:
            from src.storage import persist_llm_usage

            persist_llm_usage(
                usage=dict(step.tokens),
                model=step.model or "unknown",
                call_type=step.task_id,
            )
        except Exception as exc:  # noqa: BLE001 — usage bookkeeping must not break decisions
            logger.debug("[GOTRuntime] persist_llm_usage failed: %s", exc)

    # ------------------------------------------------------------------
    # structured parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _is_root_model(output_model: type[BaseModel]) -> bool:
        return issubclass(output_model, RootModel)

    @staticmethod
    def _strip_fence(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[:-3]
        return cleaned.strip()

    def _parse_structured(self, content: str, output_model: type[BaseModel]) -> Any:
        if self._is_root_model(output_model):
            # Arrays: only a complete array is acceptable — never repair/truncate-fill.
            cleaned = self._strip_fence(content)
            if not cleaned.startswith("["):
                raise ValueError("expected a complete JSON array")
            payload = json.loads(cleaned)
            if not isinstance(payload, list):
                raise ValueError("root payload is not a list")
            return payload
        from src.agent.runner import try_parse_json

        payload = try_parse_json(content)
        if not isinstance(payload, dict):
            raise ValueError("no JSON object found")
        return payload

    @staticmethod
    def _iter_evidence_ids(value: Any) -> Iterable[str]:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("evidence_ids", "evidence_id"):
                    if isinstance(item, str):
                        yield item
                    elif isinstance(item, list):
                        for entry in item:
                            if isinstance(entry, str):
                                yield entry
                else:
                    yield from GOTAgentRuntime._iter_evidence_ids(item)
        elif isinstance(value, list):
            for item in value:
                yield from GOTAgentRuntime._iter_evidence_ids(item)

    @classmethod
    def _check_evidence_closure(cls, output: Any, allowed: set[str]) -> None:
        unknown = sorted({eid for eid in cls._iter_evidence_ids(output) if eid not in allowed})
        if unknown:
            raise ValueError(f"unknown evidence_id(s): {', '.join(unknown[:5])}")

    # ------------------------------------------------------------------
    # step bookkeeping
    # ------------------------------------------------------------------
    def _new_step(
        self,
        *,
        task_id: str,
        parent_step_ids: list[str],
        input_refs: list[str],
        evidence_refs: list[EvidenceRef],
        prompt_text: str,
        prompt_sha256: str,
        attempt: int,
    ) -> SemanticStep:
        with self._lock:
            self._seq += 1
            seq = self._seq
        return SemanticStep(
            run_id=self._run_id,
            task_id=task_id,
            step_id=f"{task_id}:{seq:03d}",
            parent_step_ids=parent_step_ids,
            status="failed",
            model="",
            provider="",
            finish_reason=None,
            input_refs=input_refs,
            evidence_refs=evidence_refs,
            output=None,
            prompt_text=prompt_text,
            raw_response="",
            prompt_sha256=prompt_sha256,
            prompt_template_version=self.prompt_template_version,
            attempt=attempt,
            tokens={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            latency_ms=0,
        )

    def _record(self, step: SemanticStep) -> None:
        with self._lock:
            self._steps.append(step)
            self._by_task.setdefault(step.task_id, []).append(step)

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def trace_dir(self) -> Path:
        day = self.run_date.strftime("%Y%m%d")
        if self.replay:
            return self._archive_root / day / "_replay" / "agent_runtime"
        return self._archive_root / day

    def summary(self) -> dict:
        steps = self.steps
        total_tokens = sum(step.tokens.get("total_tokens", 0) for step in steps)
        failed = [step for step in steps if step.status == "failed"]
        ok = [step for step in steps if step.status == "ok"]
        if not steps:
            status = "empty"
        elif not failed:
            status = "ok"
        elif ok:
            status = "degraded"
        else:
            status = "failed"
        return {
            "status": status,
            "step_count": len(steps),
            "ok_steps": len(ok),
            "failed_steps": len(failed),
            "total_tokens": total_tokens,
            "tasks": sorted({step.task_id for step in steps}),
        }

    def _config_fingerprint(self) -> str:
        payload: dict[str, Any] = {"prompt_template_version": self.prompt_template_version}
        try:
            from src.config import (
                get_config,
                get_effective_agent_models_to_try,
                get_effective_agent_primary_model,
            )

            config = get_config()
            payload["primary_model"] = get_effective_agent_primary_model(config)
            payload["fallback_models"] = list(get_effective_agent_models_to_try(config))
        except Exception as exc:  # noqa: BLE001 — fingerprint is best-effort metadata
            payload["config_error"] = str(exc)
        try:
            from src.got.catalyst_config import SCORING_FORMULA_VERSION

            payload["scoring_formula_version"] = SCORING_FORMULA_VERSION
        except Exception:  # noqa: BLE001
            payload["scoring_formula_version"] = "unknown"
        secret_markers = ("KEY", "TOKEN", "COOKIE", "SECRET", "PASSWORD")
        for key, value in os.environ.items():
            if not key.startswith("GOT_"):
                continue
            if any(marker in key.upper() for marker in secret_markers):
                continue
            payload[key] = value
        return _sha256(_canonical_json(payload))

    def _git_commit(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2,
                cwd=str(Path(__file__).resolve().parents[2]),
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:  # noqa: BLE001 — metadata only
            pass
        return "unknown"

    def flush(self) -> Path:
        """Atomically write ``agent_run.json`` + ``agent_steps.jsonl``."""
        trace_dir = self.trace_dir()
        trace_dir.mkdir(parents=True, exist_ok=True)
        include_raw = os.getenv(_TRACE_ENV, "true").strip().lower() not in ("0", "false", "off", "no")
        summary = self.summary()
        models = {"primary": "", "fallback": []}
        try:
            from src.config import (
                get_config,
                get_effective_agent_models_to_try,
                get_effective_agent_primary_model,
            )

            config = get_config()
            models["primary"] = get_effective_agent_primary_model(config)
            models["fallback"] = list(get_effective_agent_models_to_try(config))[1:]
        except Exception:  # noqa: BLE001
            pass
        try:
            from src.got.catalyst_config import SCORING_FORMULA_VERSION as _sfv
        except Exception:  # noqa: BLE001
            _sfv = "unknown"
        run_card = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self._run_id,
            "run_date": self.run_date.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "git_commit": self._git_commit(),
            "scoring_formula_version": _sfv,
            "prompt_template_version": self.prompt_template_version,
            "models": models,
            "config_fingerprint": self._config_fingerprint(),
            "replay": self.replay,
            "trace_raw_enabled": include_raw,
            **summary,
        }
        run_path = trace_dir / "agent_run.json"
        steps_path = trace_dir / "agent_steps.jsonl"
        self._atomic_write(run_path, json.dumps(run_card, ensure_ascii=False, indent=2, default=str))
        lines = [
            json.dumps(step.to_dict(include_raw=include_raw), ensure_ascii=False, default=str)
            for step in self.steps
        ]
        self._atomic_write(steps_path, "\n".join(lines) + ("\n" if lines else ""))
        self._flushed_path = run_path
        logger.info(
            "[GOTRuntime] trace flushed: %s (%d steps, %d tokens, status=%s)",
            run_path, summary["step_count"], summary["total_tokens"], summary["status"],
        )
        return run_path

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)


class _FixtureResponse:
    """Minimal LLMResponse-compatible object for replay fixtures."""

    def __init__(self, content: str, *, model: str = "fixture", provider: str = "fixture"):
        self.content = content
        self.provider = provider
        self.model = model
        self.finish_reason = "stop"
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.tool_calls: list = []
        self.raw = None
