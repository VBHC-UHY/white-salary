"""Conversation-scoped orchestration built on the durable runtime primitives."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .actors import CancellationRegistry, CancellationToken, ConversationActorRegistry
from .delivery import DeliveryRouter, DeliveryWorker
from .models import ChannelAddress, ConversationRef, TaskRecord, TaskState
from .store import InvalidTaskTransition, RuntimeStore


@dataclass(frozen=True)
class TaskExecutionResult:
    """Structured result returned by a platform-neutral task runner."""

    state: TaskState = TaskState.COMPLETED
    summary: str = ""
    error: str = ""
    response_payload: dict[str, Any] | None = None
    response_replay_safe: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


TaskRunner = Callable[
    [TaskRecord, CancellationToken],
    Awaitable[TaskExecutionResult | str | None],
]


@dataclass(frozen=True)
class _QueuedTask:
    task_id: str
    runner: TaskRunner
    hard_cancel: bool = False


@dataclass(frozen=True)
class _Submit:
    queued: _QueuedTask


@dataclass(frozen=True)
class _Cancel:
    task_id: str
    reason: str = ""


@dataclass(frozen=True)
class _Finished:
    task_id: str
    result: TaskExecutionResult


@dataclass
class _ConversationState:
    active_task_id: str = ""
    pending: deque[_QueuedTask] = field(default_factory=deque)


class AgentRuntimeService:
    """Runs one task at a time per conversation without blocking its mailbox.

    Platform adapters keep owning message parsing and rendering. This service
    owns durable task state, per-conversation ordering, cooperative
    cancellation, and the reliable response outbox.
    """

    def __init__(
        self,
        store: RuntimeStore,
        *,
        delivery_router: DeliveryRouter | None = None,
        delivery_worker: DeliveryWorker | None = None,
        shutdown_grace_seconds: float = 2.0,
    ) -> None:
        self.store = store
        self.actors = ConversationActorRegistry()
        self.cancellations = CancellationRegistry()
        self.delivery_router = delivery_router or DeliveryRouter()
        self.delivery_worker = delivery_worker or DeliveryWorker(store, self.delivery_router)
        self._states: dict[str, _ConversationState] = {}
        self._executions: dict[str, asyncio.Task[None]] = {}
        self._closing = False
        self._shutdown_grace_seconds = max(0.0, float(shutdown_grace_seconds))

    def start_delivery_worker(self) -> None:
        self.delivery_worker.start()

    async def submit(
        self,
        conversation: ConversationRef,
        request_text: str,
        runner: TaskRunner,
        *,
        owner_id: str = "",
        response_address: ChannelAddress | None = None,
        metadata: dict[str, Any] | None = None,
        parent_task_id: str = "",
        idempotency_key: str = "",
        task_id: str = "",
        hard_cancel: bool = False,
    ) -> TaskRecord:
        if self._closing:
            raise RuntimeError("Agent runtime is closing")
        record = self.store.create_task(
            conversation,
            request_text,
            owner_id=owner_id,
            response_address=response_address,
            metadata=metadata,
            parent_task_id=parent_task_id,
            idempotency_key=idempotency_key,
            task_id=task_id,
        )
        if record.state.terminal or record.id in self._executions:
            return record
        actor = await self._actor_for(record.conversation.key)
        await actor.ask(_Submit(_QueuedTask(record.id, runner, hard_cancel)))
        return self.store.get_task(record.id) or record

    async def resume(
        self,
        task_id: str,
        runner: TaskRunner,
        *,
        hard_cancel: bool = False,
    ) -> TaskRecord:
        record = self.store.get_task(task_id)
        if record is None:
            raise KeyError(f"Unknown task: {task_id}")
        if record.state.terminal:
            return record
        if record.state not in {TaskState.SUBMITTED, TaskState.WORKING}:
            raise InvalidTaskTransition(
                f"Task {task_id} in {record.state.value} requires its dedicated gate API"
            )
        actor = await self._actor_for(record.conversation.key)
        await actor.ask(_Submit(_QueuedTask(record.id, runner, hard_cancel)))
        return self.store.get_task(record.id) or record

    async def provide_input(
        self,
        task_id: str,
        runner: TaskRunner,
        *,
        field_names: tuple[str, ...] = (),
        hard_cancel: bool = False,
    ) -> TaskRecord:
        """Resume only an INPUT_REQUIRED task without persisting input values."""

        record = self.store.get_task(task_id)
        if record is None:
            raise KeyError(f"Unknown task: {task_id}")
        if record.state != TaskState.INPUT_REQUIRED:
            raise InvalidTaskTransition(f"Task {task_id} is not waiting for input")
        self.store.append_event(
            task_id,
            "task_input_provided",
            {"fields": sorted(set(str(name) for name in field_names if str(name)))},
        )
        actor = await self._actor_for(record.conversation.key)
        await actor.ask(_Submit(_QueuedTask(record.id, runner, hard_cancel)))
        return self.store.get_task(record.id) or record

    async def authorize(
        self,
        task_id: str,
        runner: TaskRunner,
        *,
        scope: str = "",
        hard_cancel: bool = False,
    ) -> TaskRecord:
        """Resume only an AUTH_REQUIRED task after an explicit authorization."""

        record = self.store.get_task(task_id)
        if record is None:
            raise KeyError(f"Unknown task: {task_id}")
        if record.state != TaskState.AUTH_REQUIRED:
            raise InvalidTaskTransition(f"Task {task_id} is not waiting for authorization")
        self.store.append_event(
            task_id,
            "task_authorized",
            {"scope": str(scope)},
        )
        actor = await self._actor_for(record.conversation.key)
        await actor.ask(_Submit(_QueuedTask(record.id, runner, hard_cancel)))
        return self.store.get_task(record.id) or record

    async def cancel(self, task_id: str, reason: str = "") -> TaskRecord:
        record = self.store.get_task(task_id)
        if record is None:
            raise KeyError(f"Unknown task: {task_id}")
        if record.state.terminal:
            return record
        actor = await self._actor_for(record.conversation.key)
        await actor.ask(_Cancel(task_id, reason))
        return self.store.get_task(task_id) or record

    def reconcile(
        self,
        task_id: str,
        outcome: TaskState,
        *,
        summary: str = "",
        error: str = "",
    ) -> TaskRecord:
        """Record a manually verified outcome for an ambiguous side effect."""

        record = self.store.get_task(task_id)
        if record is None:
            raise KeyError(f"Unknown task: {task_id}")
        if record.state != TaskState.RECONCILIATION_REQUIRED:
            raise InvalidTaskTransition(f"Task {task_id} does not require reconciliation")
        if outcome not in {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}:
            raise InvalidTaskTransition("Reconciliation outcome must be terminal")
        return self.store.transition_task(
            task_id,
            outcome,
            result_summary=summary,
            error=error,
            event_payload={"source": "manual_reconciliation"},
        )

    def recoverable_tasks(self) -> list[TaskRecord]:
        """Return unfinished durable tasks without replaying side effects."""
        return self.store.list_active_tasks()

    async def wait_for_idle(self, conversation_key: str = "") -> None:
        if conversation_key:
            actor = self.actors.get(conversation_key)
            if actor is not None:
                await actor.join()
            while self._states.get(conversation_key, _ConversationState()).active_task_id:
                await asyncio.sleep(0)
            return
        while self._executions:
            await asyncio.sleep(0)
        await asyncio.gather(
            *(actor.join() for actor in self.actors.active_actors()),
            return_exceptions=True,
        )

    async def close(self) -> None:
        """Close an idle service. Running tasks remain durable for recovery."""
        self._closing = True
        await self.delivery_worker.stop()
        snapshot = dict(self._executions)
        unsafe: dict[str, asyncio.Task[None]] = {}
        for task_id, execution in snapshot.items():
            if self.cancellations.is_hard_cancel_safe(task_id):
                execution.cancel("runtime_shutdown")
            else:
                self.cancellations.cancel(task_id, "runtime_shutdown")
                unsafe[task_id] = execution

        if snapshot:
            _, pending = await asyncio.wait(
                snapshot.values(),
                timeout=self._shutdown_grace_seconds,
            )
            pending_set = set(pending)
            for task_id, execution in unsafe.items():
                if execution not in pending_set:
                    continue
                record = self.store.get_task(task_id)
                if record is not None and record.state in {
                    TaskState.WORKING,
                    TaskState.CANCEL_REQUESTED,
                }:
                    self.store.transition_task(
                        task_id,
                        TaskState.RECONCILIATION_REQUIRED,
                        error="Runtime stopped before the side-effect outcome was confirmed",
                        event_payload={"reason": "runtime_shutdown_timeout"},
                    )
                execution.cancel("runtime_shutdown_unconfirmed")
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        await self.actors.close_all()

    async def _actor_for(self, conversation_key: str):
        return await self.actors.get_or_create(
            conversation_key,
            lambda message: self._handle_actor_message(conversation_key, message),
        )

    async def _handle_actor_message(self, key: str, message: Any) -> None:
        state = self._states.setdefault(key, _ConversationState())
        if isinstance(message, _Submit):
            if message.queued.task_id == state.active_task_id:
                return
            if any(item.task_id == message.queued.task_id for item in state.pending):
                return
            if state.active_task_id:
                state.pending.append(message.queued)
            else:
                self._launch(key, state, message.queued)
            return
        if isinstance(message, _Cancel):
            self._cancel_in_actor(state, message)
            return
        if isinstance(message, _Finished):
            self._finish_in_actor(key, state, message)

    def _launch(self, key: str, state: _ConversationState, queued: _QueuedTask) -> None:
        record = self.store.get_task(queued.task_id)
        if record is None or record.state.terminal:
            self._launch_next(key, state)
            return
        if record.state == TaskState.WORKING:
            self.store.append_event(record.id, "task_resumed", {"source": "runtime"})
        else:
            self.store.transition_task(record.id, TaskState.WORKING)
        state.active_task_id = record.id
        token = self.cancellations.get_or_create(record.id)
        execution = asyncio.create_task(
            self._run_task(key, record.id, queued.runner, token),
            name=f"agent-task:{record.id}",
        )
        self.cancellations.bind_task(record.id, execution, hard_cancel=queued.hard_cancel)
        self._executions[record.id] = execution

    async def _run_task(
        self,
        key: str,
        task_id: str,
        runner: TaskRunner,
        token: CancellationToken,
    ) -> None:
        try:
            record = self.store.get_task(task_id)
            if record is None:
                raise KeyError(f"Unknown task: {task_id}")
            value = await runner(record, token)
            if isinstance(value, TaskExecutionResult):
                result = value
            elif isinstance(value, str):
                result = TaskExecutionResult(summary=value)
            else:
                result = TaskExecutionResult()
        except asyncio.CancelledError:
            if self._closing:
                if self.cancellations.is_hard_cancel_safe(task_id):
                    self.store.append_event(
                        task_id,
                        "task_suspended",
                        {"reason": "runtime_shutdown", "replay_safe": True},
                    )
                    return
                result = TaskExecutionResult(
                    state=TaskState.RECONCILIATION_REQUIRED,
                    error="Runtime stopped before the side-effect outcome was confirmed",
                    metadata={"reason": "runtime_shutdown"},
                )
            else:
                result = TaskExecutionResult(
                    state=TaskState.CANCELLED,
                    error=token.reason or "Task cancelled",
                )
        except Exception as exc:
            result = TaskExecutionResult(state=TaskState.FAILED, error=str(exc))
        finally:
            self._executions.pop(task_id, None)
        actor = self.actors.get(key)
        if actor is not None:
            await actor.publish(_Finished(task_id, result))

    def _cancel_in_actor(self, state: _ConversationState, command: _Cancel) -> None:
        if state.active_task_id == command.task_id:
            record = self.store.get_task(command.task_id)
            if record is not None and record.state not in {
                TaskState.CANCEL_REQUESTED,
                TaskState.CANCELLED,
            }:
                self.store.transition_task(
                    command.task_id,
                    TaskState.CANCEL_REQUESTED,
                    event_payload={"reason": command.reason},
                )
            self.cancellations.cancel(command.task_id, command.reason)
            return

        remaining = deque(item for item in state.pending if item.task_id != command.task_id)
        removed = len(remaining) != len(state.pending)
        state.pending = remaining
        record = self.store.get_task(command.task_id)
        if record is not None and not record.state.terminal:
            if record.state == TaskState.RECONCILIATION_REQUIRED:
                self.store.transition_task(
                    command.task_id,
                    TaskState.CANCELLED,
                    error=command.reason,
                    event_payload={"reason": command.reason},
                )
                return
            self.store.transition_task(
                command.task_id,
                TaskState.CANCELLED if removed else TaskState.CANCEL_REQUESTED,
                error=command.reason,
                event_payload={"reason": command.reason},
            )

    def _finish_in_actor(self, key: str, state: _ConversationState, message: _Finished) -> None:
        if state.active_task_id != message.task_id:
            return
        state.active_task_id = ""
        self.cancellations.remove(message.task_id)
        record = self.store.get_task(message.task_id)
        if record is not None and not record.state.terminal:
            result = message.result
            target_state = result.state
            if target_state == TaskState.WORKING or target_state == TaskState.SUBMITTED:
                target_state = TaskState.COMPLETED
            if result.response_payload is not None and record.response_address is not None:
                self.store.transition_task_and_enqueue_delivery(
                    message.task_id,
                    target_state,
                    target=record.response_address,
                    payload=result.response_payload,
                    result_summary=result.summary,
                    error=result.error,
                    event_payload=result.metadata,
                    idempotency_key=f"task-response:{record.id}",
                    replay_safe=result.response_replay_safe,
                )
            else:
                self.store.transition_task(
                    message.task_id,
                    target_state,
                    result_summary=result.summary,
                    error=result.error,
                    event_payload=result.metadata,
                )
        self._launch_next(key, state)

    def _launch_next(self, key: str, state: _ConversationState) -> None:
        while state.pending and not state.active_task_id:
            queued = state.pending.popleft()
            record = self.store.get_task(queued.task_id)
            if record is not None and not record.state.terminal:
                self._launch(key, state, queued)
                return
