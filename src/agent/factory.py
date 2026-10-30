# -*- coding: utf-8 -*-
"""
Shared factory for building fully-configured AgentExecutor instances.

Centralises construction to eliminate boilerplate duplicated across
api/v1/endpoints/agent.py, bot/commands/chat.py, bot/commands/ask.py,
and src/core/pipeline.py.

Performance notes
-----------------
* ``ToolRegistry`` is built once and cached at module level — tool
  registrations are immutable after setup so the object is safe to share
  across every request.
* ``SkillManager`` is expensive to create (loads YAML files from disk).
  A prototype is built on first use and cheap ``deepcopy`` clones are
  returned for each request, preserving thread-safety (``activate()``
  mutates internal state).

Usage::

    from src.agent.factory import build_agent_executor

    executor = build_agent_executor(config, skills=["bull_trend", "shrink_pullback"])
    result   = executor.chat(message="...", session_id="...")
"""

import copy
import logging
from dataclasses import dataclass
from typing import List, Optional

from src.config import AGENT_MAX_STEPS_DEFAULT

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level caches
# ---------------------------------------------------------------------------
_TOOL_REGISTRY = None
_SKILL_MANAGER_PROTOTYPE = None
# Sentinel used as initial value so None (i.e. no custom dir) compares as "changed"
# on the very first call, forcing a build rather than accidentally skipping it.
_SENTINEL = object()
# Track which custom_dir the prototype was built with so we can invalidate
# the cache if AGENT_SKILL_DIR changes at runtime (e.g. via config reload).
_SKILL_MANAGER_CUSTOM_DIR: object = _SENTINEL


@dataclass
class SkillPromptState:
    """Resolved skill activation + prompt fragments for analysis entrypoints."""

    skill_manager: object
    skills_to_activate: List[str]
    explicit_skill_selection: bool
    use_legacy_default_prompt: bool
    skill_instructions: str
    default_skill_policy: str
    technical_skill_policy: str


def _coerce_config_int(raw_value: object, default: int, *, field_name: str | None = None) -> int:
    """Coerce optional numeric config values to int with a fallback default.

    This protects test doubles and incomplete config objects from propagating
    mock-like values (e.g., MagicMock attributes) into strict numeric paths.

    This function is side-effect free: it only returns a parsed int fallback value
    and intentionally never mutates source config attributes.
    """

    try:
        return int(raw_value)
    except (TypeError, ValueError, OverflowError):
        if field_name:
            logger.warning(
                "[AgentFactory] Invalid value for %s: %r, fallback to default %s",
                field_name,
                raw_value,
                default,
            )
        return default


def _normalize_skill_ids(
    skill_ids: Optional[List[str]],
    *,
    available_skill_ids: set[str],
) -> tuple[List[str], List[str]]:
    """Return validated skill ids plus unknown ids, preserving input order."""
    normalized: List[str] = []
    unknown: List[str] = []

    for skill_id in skill_ids or []:
        if not isinstance(skill_id, str):
            continue
        cleaned = skill_id.strip()
        if not cleaned:
            continue
        if cleaned == "all":
            if "all" not in normalized:
                normalized.append("all")
            continue
        if cleaned in available_skill_ids:
            if cleaned not in normalized:
                normalized.append(cleaned)
            continue
        if cleaned not in unknown:
            unknown.append(cleaned)

    return normalized, unknown


def _resolve_selected_skill_ids(
    *,
    requested_skills: Optional[List[str]],
    configured_skills: Optional[List[str]],
    default_skills: List[str],
    available_skill_ids: set[str],
) -> tuple[List[str], bool]:
    """Resolve active skill ids and whether they came from a valid explicit selection."""
    selection_source = None
    raw_skill_ids = None
    if requested_skills is not None:
        selection_source = "request"
        raw_skill_ids = requested_skills
    elif configured_skills is not None:
        selection_source = "config"
        raw_skill_ids = configured_skills
    else:
        return list(default_skills), False

    selected_skill_ids, unknown_skill_ids = _normalize_skill_ids(
        raw_skill_ids,
        available_skill_ids=available_skill_ids,
    )
    if unknown_skill_ids:
        logger.warning(
            "[AgentFactory] Ignoring unknown %s skill ids: %s",
            selection_source,
            unknown_skill_ids,
        )
    if selected_skill_ids:
        return selected_skill_ids, True

    if raw_skill_ids:
        logger.warning(
            "[AgentFactory] No valid %s skills remain after validation; falling back to default skills: %s",
            selection_source,
            default_skills,
        )
    return list(default_skills), False


def _should_use_legacy_default_prompt(
    *,
    skills_to_activate: List[str],
    explicit_skill_selection: bool,
    skill_catalog: List[object],
) -> bool:
    """Keep the legacy prompt only for the implicit built-in bull_trend fallback."""
    if explicit_skill_selection or skills_to_activate != ["bull_trend"]:
        return False

    bull_trend_skill = next(
        (
            skill
            for skill in skill_catalog
            if str(getattr(skill, "name", "")).strip() == "bull_trend"
        ),
        None,
    )
    return getattr(bull_trend_skill, "source", None) == "builtin"


def get_tool_registry():
    """Return a cached ToolRegistry (built once, shared across requests)."""
    global _TOOL_REGISTRY
    if _TOOL_REGISTRY is not None:
        return _TOOL_REGISTRY

    from src.agent.tools.registry import ToolRegistry
    from src.agent.tools.data_tools import ALL_DATA_TOOLS
    from src.agent.tools.analysis_tools import ALL_ANALYSIS_TOOLS
    from src.agent.tools.search_tools import ALL_SEARCH_TOOLS
    from src.agent.tools.market_tools import ALL_MARKET_TOOLS
    from src.agent.tools.backtest_tools import ALL_BACKTEST_TOOLS
    from src.agent.tools.screener_tools import ALL_SCREENER_TOOLS

    registry = ToolRegistry()
    for tool_fn in ALL_DATA_TOOLS + ALL_ANALYSIS_TOOLS + ALL_SEARCH_TOOLS + ALL_MARKET_TOOLS + ALL_BACKTEST_TOOLS + ALL_SCREENER_TOOLS:
        registry.register(tool_fn)

    _TOOL_REGISTRY = registry
    logger.info("[AgentFactory] ToolRegistry cached (%d tools)", len(registry._tools) if hasattr(registry, "_tools") else -1)
    return _TOOL_REGISTRY


def get_skill_manager(config=None):
    """Return a deepcopy-clone of the cached SkillManager prototype.

    The prototype is initialised from disk on first call; subsequent calls
    return ``copy.deepcopy(prototype)`` which is ~10× faster than re-reading
    YAML files.  Each clone is independent so ``.activate()`` calls do not
    bleed between requests.

    Cache invalidation: if ``config.agent_skill_dir`` changes at runtime
    (e.g. via the web settings reload), the prototype is rebuilt automatically.
    """
    global _SKILL_MANAGER_PROTOTYPE, _SKILL_MANAGER_CUSTOM_DIR

    if config is None:
        from src.config import get_config
        config = get_config()

    current_custom_dir = getattr(config, "agent_skill_dir", None)
    if _SKILL_MANAGER_PROTOTYPE is not None and current_custom_dir == _SKILL_MANAGER_CUSTOM_DIR:
        return copy.deepcopy(_SKILL_MANAGER_PROTOTYPE)

    from src.agent.skills.base import SkillManager

    if _SKILL_MANAGER_PROTOTYPE is not None:
        logger.info("[AgentFactory] SkillManager prototype invalidated (agent_skill_dir changed: %r -> %r)",
                    _SKILL_MANAGER_CUSTOM_DIR, current_custom_dir)

    skill_manager = SkillManager()
    skill_manager.load_builtin_skills()

    if current_custom_dir:
        try:
            skill_manager.load_custom_skills(current_custom_dir)
        except Exception as exc:
            logger.warning("[AgentFactory] Failed to load custom skills from %s: %s", current_custom_dir, exc)

    _SKILL_MANAGER_PROTOTYPE = skill_manager
    _SKILL_MANAGER_CUSTOM_DIR = current_custom_dir
    logger.info("[AgentFactory] SkillManager prototype cached (%d skills)", len(skill_manager._skills))
    return copy.deepcopy(_SKILL_MANAGER_PROTOTYPE)


def resolve_skill_prompt_state(config=None, skills: Optional[List[str]] = None) -> SkillPromptState:
    """Resolve active skills and prompt fragments for analyzer / agent entrypoints."""
    if config is None:
        from src.config import get_config
        config = get_config()

    from src.agent.skills.defaults import (
        get_default_active_skill_ids,
        get_default_technical_skill_policy,
        get_default_trading_skill_policy,
    )

    skill_manager = get_skill_manager(config)
    skill_catalog = list(skill_manager.list_skills())
    available_skill_ids = {
        str(getattr(skill, "name", "")).strip()
        for skill in skill_catalog
        if str(getattr(skill, "name", "")).strip()
    }
    configured_skills = getattr(config, "agent_skills", None)
    if configured_skills == []:
        configured_skills = None
    default_skills = get_default_active_skill_ids(
        skill_catalog,
        available_skill_ids=available_skill_ids or None,
    )
    skills_to_activate, explicit_skill_selection = _resolve_selected_skill_ids(
        requested_skills=skills,
        configured_skills=configured_skills,
        default_skills=default_skills,
        available_skill_ids=available_skill_ids,
    )

    use_legacy_default_prompt = _should_use_legacy_default_prompt(
        skills_to_activate=skills_to_activate,
        explicit_skill_selection=explicit_skill_selection,
        skill_catalog=skill_catalog,
    )

    skill_manager.activate(skills_to_activate)
    logger.info("[AgentFactory] Activated skills: %s", skills_to_activate)

    return SkillPromptState(
        skill_manager=skill_manager,
        skills_to_activate=skills_to_activate,
        explicit_skill_selection=explicit_skill_selection,
        use_legacy_default_prompt=use_legacy_default_prompt,
        skill_instructions=skill_manager.get_skill_instructions(),
        default_skill_policy=get_default_trading_skill_policy(
            explicit_skill_selection=not use_legacy_default_prompt,
        ),
        technical_skill_policy=get_default_technical_skill_policy(
            explicit_skill_selection=not use_legacy_default_prompt,
        ),
    )


def build_agent_executor(config=None, skills: Optional[List[str]] = None):
    """Build and return a configured AgentExecutor (or future orchestrator).

    When ``AGENT_ARCH=multi``, this returns an orchestrator that manages
    multiple specialised agents. Otherwise it returns the legacy single-agent
    executor.

    Args:
        config: Application config object.  When *None*, ``get_config()`` is
                called automatically.
        skills: Skill ids to activate.  When *None* falls back to
                ``config.agent_skills``; if that is also empty falls back to
                the central default skill set.

    Returns:
        A ready-to-call :class:`src.agent.executor.AgentExecutor` instance.
    """
    if config is None:
        from src.config import get_config
        config = get_config()

    arch = getattr(config, "agent_arch", "single")

    from src.agent.llm_adapter import LLMToolAdapter

    registry = get_tool_registry()
    prompt_state = resolve_skill_prompt_state(config, skills=skills)
    skill_manager = prompt_state.skill_manager
    logger.info(
        "[AgentFactory] Resolved skill prompt state: skills=%s (arch=%s, explicit=%s, legacy_default_prompt=%s)",
        prompt_state.skills_to_activate,
        arch,
        prompt_state.explicit_skill_selection,
        prompt_state.use_legacy_default_prompt,
    )

    llm_adapter = LLMToolAdapter(config)

    if arch == "multi":
        return _build_orchestrator(
            config,
            registry,
            llm_adapter,
            skill_manager,
            technical_skill_policy=prompt_state.technical_skill_policy,
        )

    from src.agent.executor import AgentExecutor
    # Intentionally do not mutate config routing fields here. We only coerce
    # execution params (max_steps/timeout_seconds) from config values; provider,
    # model, base URL and channel routes stay unchanged and are consumed by
    # downstream adapter logic as-is.
    return AgentExecutor(
        tool_registry=registry,
        llm_adapter=llm_adapter,
        skill_instructions=prompt_state.skill_instructions,
        default_skill_policy=prompt_state.default_skill_policy,
        use_legacy_default_prompt=prompt_state.use_legacy_default_prompt,
        max_steps=_coerce_config_int(
            getattr(config, "agent_max_steps", AGENT_MAX_STEPS_DEFAULT),
            AGENT_MAX_STEPS_DEFAULT,
            field_name="agent_max_steps",
        ),
        timeout_seconds=_coerce_config_int(
            getattr(config, "agent_orchestrator_timeout_s", 0),
            0,
            field_name="agent_orchestrator_timeout_s",
        ),
    )


def _build_orchestrator(config, registry, llm_adapter, skill_manager, *, technical_skill_policy: str = ""):
    """Build and return an :class:`AgentOrchestrator` (multi-agent mode).

    The orchestrator presents the same ``run()`` / ``chat()`` interface as
    :class:`AgentExecutor` so callers need no changes.
    """
    from src.agent.orchestrator import AgentOrchestrator

    mode = getattr(config, "agent_orchestrator_mode", "standard")
    logger.info("[AgentFactory] Building AgentOrchestrator (mode=%s)", mode)

    return AgentOrchestrator(
        tool_registry=registry,
        llm_adapter=llm_adapter,
        skill_instructions=skill_manager.get_skill_instructions(),
        technical_skill_policy=technical_skill_policy,
        max_steps=_coerce_config_int(
            getattr(config, "agent_max_steps", AGENT_MAX_STEPS_DEFAULT),
            AGENT_MAX_STEPS_DEFAULT,
            field_name="agent_max_steps",
        ),
        mode=mode,
        skill_manager=skill_manager,
        config=config,
    )


# ---------------------------------------------------------------------------
# §15 审查层 Review Executor（轻量 Agent，用于 T1 候选股终审 / T1-H 持仓审查）
# ---------------------------------------------------------------------------

# 缓存审查专用工具注册表（只构建一次）
_REVIEW_TOOL_REGISTRY = None

# 审查专用系统提示词（T1 候选股终审）：输出纯 JSON {verdict, reasoning, confidence, risk_flags}
# verdict 词表：confirm / warn / reject（§15.3）
_REVIEW_SYSTEM_PROMPT_T1 = """你是一名个股审查分析师。你的任务是根据提供的股票信息，做出审查判断。

## 输出格式

**必须**输出且仅输出以下 JSON 结构（不要输出其他内容）：

```json
{
  "verdict": "confirm|warn|reject",
  "reasoning": "简短判断依据（≤60字）",
  "confidence": 0.0-1.0,
  "risk_flags": ["风险提示1", ...]
}
```

- `verdict`: confirm（确认推荐）/ warn（存疑但不过滤）/ reject（强烈不推荐）
- `confidence`: 判断置信度
- `risk_flags`: 识别到的风险因素列表（可为空）

## 判断标准

- **confirm**: 技术面+基本面+催化剂共振，无明显风险
- **warn**: 存在不确定性但不足以否决，标注风险点
- **reject**: 存在严重风险信号（如：高位放量滞涨、利好兑现迹象、板块整体走弱）

## 约束

- 你**只能**输出 JSON，不要输出任何解释或前言
- 基于提供的数据做判断，不要臆测
- 保守原则：不确定时给 warn，不轻易 reject"""


# 审查专用系统提示词（T1-H 持仓审查）：verdict 词表 hold / add / reduce / exit（§15.4）
# 与 T1 词表不同：下游 _compute_holding_penalty 仅对 reduce/exit 施加降档惩罚
_REVIEW_SYSTEM_PROMPT_T1H = """你是一名持仓审查分析师。你的任务是根据提供的持仓股票信息，给出处置意见。

## 输出格式

**必须**输出且仅输出以下 JSON 结构（不要输出其他内容）：

```json
{
  "verdict": "hold|add|reduce|exit",
  "reasoning": "一句话理由（30字内）",
  "confidence": 0.0-1.0,
  "risk_flags": ["风险点1", ...]
}
```

- `verdict`: hold（继续持有）/ add（看好加仓）/ reduce（存在风险减仓）/ exit（严重风险清仓）
- `confidence`: 判断置信度（0.0-1.0，不要使用百分制）
- `risk_flags`: 识别到的风险因素列表（可为空）

## 判断标准

- **hold**: 维持现状，无明显加减仓理由
- **add**: 催化剂兑现、趋势向好，可加仓
- **reduce**: 出现风险信号（浮亏扩大、生命周期 late_stage、板块走弱），建议减仓
- **exit**: 严重风险（利好兑现、退出信号触发、高位放量滞涨），建议清仓

## 约束

- 你**只能**输出 JSON，不要输出任何解释或前言
- 基于提供的数据做判断，不要臆测
- 保守原则：不确定时给 hold，不轻易 exit"""


# 兼容旧名：默认指向 T1 词表
_REVIEW_SYSTEM_PROMPT = _REVIEW_SYSTEM_PROMPT_T1

# 审查 profile：review_type → (系统提示词, call_type 归因)
_REVIEW_PROFILES = {
    "t1": {"system_prompt": _REVIEW_SYSTEM_PROMPT_T1, "call_type": "agent_review_t1"},
    "t1h": {"system_prompt": _REVIEW_SYSTEM_PROMPT_T1H, "call_type": "agent_review_t1h"},
}


def _get_review_tool_registry():
    """返回审查专用轻量工具注册表（仅 3 个工具）。"""
    global _REVIEW_TOOL_REGISTRY
    if _REVIEW_TOOL_REGISTRY is not None:
        return _REVIEW_TOOL_REGISTRY

    from src.agent.tools.registry import ToolRegistry

    full_registry = get_tool_registry()
    from src.got.catalyst_config import AGENT_REVIEW_TOOLS

    review_registry = ToolRegistry()
    for tool_name in AGENT_REVIEW_TOOLS:
        tool_def = full_registry.get(tool_name)
        if tool_def:
            review_registry.register(tool_def)
        else:
            logger.warning(f"[ReviewExecutor] 工具 {tool_name!r} 未注册，跳过")

    _REVIEW_TOOL_REGISTRY = review_registry
    logger.info(f"[ReviewExecutor] 轻量工具注册表构建完成（{len(review_registry._tools)} 个工具）")
    return review_registry


def _instrument_adapter(adapter, runtime, task_id: str):
    """Wrap a tool-enabled adapter so every completion lands in the run trace."""
    import time as _time

    class _RuntimeInstrumentedAdapter:
        def __init__(self, inner, rt, tid):
            self._inner = inner
            self._runtime = rt
            self._task_id = tid

        def call_with_tools(self, messages, tools, **kwargs):
            started = _time.time()
            try:
                response = self._inner.call_with_tools(messages, tools, **kwargs)
            except Exception as exc:  # noqa: BLE001
                self._runtime.record_completion(
                    task_id=self._task_id, messages=messages, error=exc,
                    latency_ms=int((_time.time() - started) * 1000),
                )
                raise
            self._runtime.record_completion(
                task_id=self._task_id, messages=messages, response=response,
                latency_ms=int((_time.time() - started) * 1000),
            )
            return response

        def __getattr__(self, name):
            return getattr(self._inner, name)

    return _RuntimeInstrumentedAdapter(adapter, runtime, task_id)


def build_review_executor(config=None, review_type: str = "t1", got_runtime=None) -> "AgentExecutor":
    """构建审查专用轻量 AgentExecutor（§15 T1/T1-H）。

    与全量 Agent 的区别：
    - 系统提示词：审查专用（输出纯 JSON verdict），按 review_type 选词表
    - 工具子集：仅 get_realtime_quote / get_daily_history / search_stock_news
    - max_steps: 4（全量默认 10）
    - temperature: AGENT_REVIEW_TEMPERATURE（确定性输出，§15.2 前置 B2）
    - call_type: agent_review_t1 / agent_review_t1h（§15.6 成本红线可度量）
    - 无 skill_instructions / default_skill_policy（审查不需要交易技能）

    Args:
        config: 可选配置对象（None → get_config()）
        review_type: "t1"（候选股终审，verdict=confirm/warn/reject）或
                     "t1h"（持仓审查，verdict=hold/add/reduce/exit）
        got_runtime: 可选 GOT run 级 runtime；提供时每次模型调用写入同一 trace

    Returns:
        配置好的 AgentExecutor 实例
    """
    if config is None:
        from src.config import get_config
        config = get_config()

    from src.agent.llm_adapter import LLMToolAdapter
    from src.agent.executor import AgentExecutor
    from src.got.catalyst_config import (
        AGENT_REVIEW_MAX_STEPS,
        AGENT_REVIEW_TIMEOUT_S,
        AGENT_REVIEW_TEMPERATURE,
    )

    profile = _REVIEW_PROFILES.get(review_type, _REVIEW_PROFILES["t1"])
    review_registry = _get_review_tool_registry()
    llm_adapter = LLMToolAdapter(config)
    if got_runtime is not None:
        # T1/T1-H 保留有界工具循环；仅把每次模型调用镜像进同一 run trace
        llm_adapter = _instrument_adapter(llm_adapter, got_runtime, f"node7.{review_type}")

    return AgentExecutor(
        tool_registry=review_registry,
        llm_adapter=llm_adapter,
        skill_instructions="",       # 审查不需要技能
        default_skill_policy="",     # 审查不需要技能策略
        use_legacy_default_prompt=False,
        max_steps=AGENT_REVIEW_MAX_STEPS,
        timeout_seconds=AGENT_REVIEW_TIMEOUT_S,
        system_prompt_override=profile["system_prompt"],
        temperature=AGENT_REVIEW_TEMPERATURE,
        call_type=profile["call_type"],
    )


# Keep legacy alias so any external callers using the old name still work.
build_executor = build_agent_executor
