"""Iterative, observable tool execution that preserves the existing tool LLM."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
import weakref
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from loguru import logger

from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, MessageRole, ToolCall, ToolResult

from .actors import CancellationToken
from .store import RuntimeStore


class ToolDefinitionLike(Protocol):
    name: str
    side_effect: bool
    side_effect_group: str


class DetailedExecutionLike(Protocol):
    name: str
    ok: bool
    content: str
    duration_ms: int
    error_type: str
    side_effect: bool
    outcome_known: bool


class ToolRegistryLike(Protocol):
    def get_openai_tools(self, context: Any = None) -> list[dict]: ...

    def get_tool(self, name: str) -> ToolDefinitionLike | None: ...

    async def execute_detailed(
        self,
        name: str,
        arguments: dict,
        context: Any = None,
    ) -> DetailedExecutionLike: ...


ProgressCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]


@dataclass(frozen=True)
class ToolRun:
    call_id: str
    name: str
    arguments: dict[str, Any]
    ok: bool
    content: str
    duration_ms: int
    side_effect: bool
    error_type: str = ""
    outcome_known: bool = True

    def as_tool_result(self) -> ToolResult:
        if not self.outcome_known:
            status = "outcome_unknown_do_not_retry"
        else:
            status = "success" if self.ok else f"failed:{self.error_type or 'unknown'}"
        return ToolResult(
            call_id=self.call_id,
            content=f"[{self.name} | {status}] {self.content}",
        )


@dataclass
class ToolLoopOutcome:
    runs: list[ToolRun] = field(default_factory=list)
    rounds: int = 0
    stop_reason: str = "no_tools"
    judge_text: str = ""

    @property
    def tool_results(self) -> list[ToolResult]:
        return [run.as_tool_result() for run in self.runs]

    @property
    def successful_side_effects(self) -> list[str]:
        return [run.name for run in self.runs if run.ok and run.side_effect]

    @property
    def unconfirmed_side_effects(self) -> list[str]:
        return [run.name for run in self.runs if run.side_effect and not run.outcome_known]


class _SideEffectLocks:
    """Event-loop-local locks shared by every ToolLoopRunner instance."""

    _by_loop: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

    @classmethod
    def get(cls, group: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        locks = cls._by_loop.setdefault(loop, {})
        key = group.strip() or "global"
        lock = locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            locks[key] = lock
        return lock


class ToolLoopRunner:
    """Run tool selection until the tool LLM says the task is ready to answer."""

    def __init__(
        self,
        *,
        tool_llm: LLMInterface,
        registry: ToolRegistryLike,
        max_rounds: int = 4,
        judge_timeout: float = 15.0,
    ) -> None:
        self._tool_llm = tool_llm
        self._registry = registry
        self._max_rounds = max(1, int(max_rounds))
        self._judge_timeout = max(1.0, float(judge_timeout))

    async def run(
        self,
        messages: list[Message],
        *,
        access_context: Any = None,
        cancellation: CancellationToken | None = None,
        store: RuntimeStore | None = None,
        task_id: str = "",
        progress: ProgressCallback | None = None,
        initial_results: list[ToolRun] | None = None,
    ) -> ToolLoopOutcome:
        outcome = ToolLoopOutcome(runs=list(initial_results or []))
        executed: set[tuple[str, str]] = {
            (run.name, self._argument_key(run.arguments)) for run in outcome.runs
        }
        tools_payload = self._get_openai_tools(access_context)
        if not tools_payload:
            outcome.stop_reason = "no_available_tools"
            return outcome

        for round_number in range(1, self._max_rounds + 1):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            outcome.rounds = round_number
            judge_messages = self._build_judge_messages(messages, outcome.runs)
            await self._emit(progress, "tool_judging", {"round": round_number})
            self._append_store_event(
                store,
                task_id,
                "tool_judging",
                {"round": round_number},
            )
            try:
                judge_text, calls = await asyncio.wait_for(
                    self._tool_llm.chat_with_tools(
                        messages=judge_messages,
                        tools=tools_payload,
                        temperature=0.2,
                        max_tokens=1024,
                    ),
                    timeout=self._judge_timeout,
                )
            except asyncio.TimeoutError:
                outcome.stop_reason = "judge_timeout"
                self._append_store_event(
                    store,
                    task_id,
                    "tool_judge_failed",
                    {"round": round_number, "error_type": "timeout"},
                )
                return outcome
            except Exception as exc:
                outcome.stop_reason = "judge_error"
                self._append_store_event(
                    store,
                    task_id,
                    "tool_judge_failed",
                    {"round": round_number, "error_type": "exception", "error": str(exc)[:500]},
                )
                return outcome

            outcome.judge_text = judge_text or outcome.judge_text
            if not calls:
                outcome.stop_reason = "no_more_tools"
                return outcome

            planned = self._deduplicate_calls(calls, executed)
            if not planned:
                outcome.stop_reason = "duplicate_cycle"
                self._append_store_event(
                    store,
                    task_id,
                    "tool_loop_stopped",
                    {"round": round_number, "reason": outcome.stop_reason},
                )
                return outcome

            self._append_store_event(
                store,
                task_id,
                "tool_calls_planned",
                {"round": round_number, "tools": [call.name for call, _ in planned]},
            )
            read_only: list[tuple[ToolCall, dict[str, Any]]] = []
            side_effects: list[tuple[ToolCall, dict[str, Any]]] = []
            for call, arguments in planned:
                definition = self._registry.get_tool(call.name)
                if definition is not None and bool(getattr(definition, "side_effect", False)):
                    side_effects.append((call, arguments))
                else:
                    read_only.append((call, arguments))

            if read_only:
                parallel_runs = await asyncio.gather(*(
                    self._execute_one(
                        call,
                        arguments,
                        access_context=access_context,
                        cancellation=cancellation,
                        store=store,
                        task_id=task_id,
                        progress=progress,
                    )
                    for call, arguments in read_only
                ))
                outcome.runs.extend(parallel_runs)

            for call, arguments in side_effects:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                outcome.runs.append(await self._execute_one(
                    call,
                    arguments,
                    access_context=access_context,
                    cancellation=cancellation,
                    store=store,
                    task_id=task_id,
                    progress=progress,
                ))

        outcome.stop_reason = "max_rounds"
        self._append_store_event(
            store,
            task_id,
            "tool_loop_stopped",
            {"round": outcome.rounds, "reason": outcome.stop_reason},
        )
        return outcome

    async def _execute_one(
        self,
        call: ToolCall,
        arguments: dict[str, Any],
        *,
        access_context: Any,
        cancellation: CancellationToken | None,
        store: RuntimeStore | None,
        task_id: str,
        progress: ProgressCallback | None,
    ) -> ToolRun:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        definition = self._registry.get_tool(call.name)
        side_effect = bool(definition is not None and getattr(definition, "side_effect", False))
        event_payload = {
            "call_id": call.id,
            "tool": call.name,
            "side_effect": side_effect,
        }
        await self._emit(progress, "tool_started", event_payload)
        self._append_store_event(
            store,
            task_id,
            "tool_started",
            event_payload,
            strict=side_effect,
        )
        started = time.perf_counter()

        async def invoke() -> ToolRun:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            if hasattr(self._registry, "execute_detailed"):
                execute = self._registry.execute_detailed
                parameters = inspect.signature(execute).parameters.values()
                supports_context = any(
                    parameter.name == "context"
                    or parameter.kind == inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters
                )
                if supports_context:
                    result = await execute(call.name, arguments, context=access_context)
                else:
                    result = await execute(call.name, arguments)
                return ToolRun(
                    call_id=call.id,
                    name=call.name,
                    arguments=arguments,
                    ok=bool(result.ok),
                    content=str(result.content),
                    duration_ms=int(result.duration_ms),
                    side_effect=bool(result.side_effect),
                    error_type=str(result.error_type),
                    outcome_known=bool(getattr(result, "outcome_known", True)),
                )

            execute = self._registry.execute
            parameters = inspect.signature(execute).parameters.values()
            supports_context = any(
                parameter.name == "context"
                or parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
            if supports_context:
                content = await execute(call.name, arguments, context=access_context)
            else:
                content = await execute(call.name, arguments)
            return ToolRun(
                call_id=call.id,
                name=call.name,
                arguments=arguments,
                ok=True,
                content=str(content),
                duration_ms=int((time.perf_counter() - started) * 1000),
                side_effect=side_effect,
            )

        try:
            if side_effect:
                group = str(getattr(definition, "side_effect_group", "global") or "global")
                async with _SideEffectLocks.get(group):
                    run = await invoke()
            else:
                run = await invoke()
        except Exception as exc:
            run = ToolRun(
                call_id=call.id,
                name=call.name,
                arguments=arguments,
                ok=False,
                content=f"Tool execution failed: {exc}",
                duration_ms=int((time.perf_counter() - started) * 1000),
                side_effect=side_effect,
                error_type=type(exc).__name__,
                outcome_known=not side_effect,
            )
        completed_payload = {
            **event_payload,
            "ok": run.ok,
            "duration_ms": run.duration_ms,
            "error_type": run.error_type,
            "outcome_known": run.outcome_known,
            "result_preview": run.content[:500],
        }
        await self._emit(progress, "tool_completed", completed_payload)
        self._append_store_event(store, task_id, "tool_completed", completed_payload)
        return run

    def _get_openai_tools(self, access_context: Any) -> list[dict]:
        getter = self._registry.get_openai_tools
        parameters = inspect.signature(getter).parameters.values()
        supports_context = any(
            parameter.name == "context"
            or parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        if supports_context:
            return getter(context=access_context)
        return getter()

    @staticmethod
    def _build_judge_messages(messages: list[Message], runs: list[ToolRun]) -> list[Message]:
        if not runs:
            return list(messages)
        lines = [
            "The following tools have already run for this user request.",
            "Decide whether another tool is required. Do not repeat an identical call.",
        ]
        for run in runs:
            if not run.outcome_known:
                status = "outcome unknown; do not retry without reconciliation"
            else:
                status = "success" if run.ok else f"failed:{run.error_type or 'unknown'}"
            lines.append(f"- {run.name} ({status}): {run.content[:1000]}")
        return [
            *messages,
            Message(role=MessageRole.SYSTEM, content="\n".join(lines)),
        ]

    @classmethod
    def _deduplicate_calls(
        cls,
        calls: list[ToolCall],
        executed: set[tuple[str, str]],
    ) -> list[tuple[ToolCall, dict[str, Any]]]:
        planned: list[tuple[ToolCall, dict[str, Any]]] = []
        for call in calls:
            arguments = cls._parse_arguments(call.arguments)
            key = (call.name, cls._argument_key(arguments))
            if key in executed:
                continue
            executed.add(key)
            planned.append((call, arguments))
        return planned

    @staticmethod
    def _parse_arguments(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _argument_key(arguments: dict[str, Any]) -> str:
        return json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    async def _emit(
        progress: ProgressCallback | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if progress is None:
            return
        try:
            value = progress(event_type, payload)
            if inspect.isawaitable(value):
                await value
        except Exception as exc:
            logger.warning(f"[ToolLoop] 进度回调失败 {event_type}: {exc}")

    @staticmethod
    def _append_store_event(
        store: RuntimeStore | None,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        strict: bool = False,
    ) -> None:
        if store is None or not task_id:
            return
        try:
            store.append_event(task_id, event_type, payload)
        except Exception as exc:
            if strict:
                raise
            logger.warning(f"[ToolLoop] 任务事件写入失败 {event_type}: {exc}")
