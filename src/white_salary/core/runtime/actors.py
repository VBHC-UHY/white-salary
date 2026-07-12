"""Per-conversation serialization and cooperative task cancellation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


ActorHandler = Callable[[Any], Awaitable[Any]]


@dataclass
class _Envelope:
    message: Any
    future: asyncio.Future[Any] | None = None
    started: bool = False
    cancelled: bool = False


class ConversationActor:
    """A single FIFO consumer for one conversation key."""

    def __init__(self, key: str, handler: ActorHandler) -> None:
        self.key = key
        self._handler = handler
        self._queue: asyncio.Queue[_Envelope | None] = asyncio.Queue()
        self._runner: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self) -> None:
        if self._closed:
            raise RuntimeError(f"Conversation actor {self.key} is closed")
        if self._runner is None or self._runner.done():
            self._runner = asyncio.create_task(self._run(), name=f"conversation:{self.key}")

    async def ask(self, message: Any, *, timeout: float | None = None) -> Any:
        self.start()
        future = asyncio.get_running_loop().create_future()
        envelope = _Envelope(message=message, future=future)
        await self._queue.put(envelope)
        if timeout is None:
            return await future
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError:
            # A timeout may abandon a message only while it is still queued.
            # Once the handler starts, its durable task/cancellation contract
            # owns the outcome and the actor must not pretend it was stopped.
            if not envelope.started:
                envelope.cancelled = True
            if not future.done():
                future.cancel()
            raise

    async def publish(self, message: Any) -> None:
        self.start()
        await self._queue.put(_Envelope(message=message))

    async def join(self) -> None:
        await self._queue.join()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._runner is None:
            return
        await self._queue.put(None)
        await self._runner

    async def _run(self) -> None:
        while True:
            envelope = await self._queue.get()
            try:
                if envelope is None:
                    return
                if envelope.cancelled or (
                    envelope.future is not None and envelope.future.cancelled()
                ):
                    continue
                envelope.started = True
                try:
                    result = await self._handler(envelope.message)
                except asyncio.CancelledError:
                    if envelope.future is not None and not envelope.future.done():
                        envelope.future.cancel()
                    # Cooperative cancellation belongs to this message, not to
                    # the conversation mailbox. Later messages must still run.
                    # Actors are closed with a sentinel rather than Task.cancel,
                    # which also keeps this compatible with Python 3.10.
                    continue
                except Exception as exc:
                    if envelope.future is not None and not envelope.future.done():
                        envelope.future.set_exception(exc)
                else:
                    if envelope.future is not None and not envelope.future.done():
                        envelope.future.set_result(result)
            finally:
                self._queue.task_done()


class ConversationActorRegistry:
    """Creates one actor per conversation and keeps actor state isolated."""

    def __init__(self) -> None:
        self._actors: dict[str, ConversationActor] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, key: str, handler: ActorHandler) -> ConversationActor:
        async with self._lock:
            actor = self._actors.get(key)
            if actor is None or actor.closed:
                actor = ConversationActor(key, handler)
                actor.start()
                self._actors[key] = actor
            return actor

    def get(self, key: str) -> ConversationActor | None:
        actor = self._actors.get(key)
        return None if actor is not None and actor.closed else actor

    def active_actors(self) -> tuple[ConversationActor, ...]:
        return tuple(actor for actor in self._actors.values() if not actor.closed)

    async def close(self, key: str) -> None:
        async with self._lock:
            actor = self._actors.pop(key, None)
        if actor is not None:
            await actor.close()

    async def close_all(self) -> None:
        async with self._lock:
            actors = list(self._actors.values())
            self._actors.clear()
        await asyncio.gather(*(actor.close() for actor in actors), return_exceptions=True)


@dataclass
class CancellationToken:
    """Cooperative cancellation token passed through tools and workers."""

    task_id: str
    _event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = ""

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()

    def cancel(self, reason: str = "") -> None:
        self.reason = reason
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError(self.reason or f"Task {self.task_id} was cancelled")


class CancellationRegistry:
    """Tracks cancellation tokens without sharing mutable conversation state."""

    def __init__(self) -> None:
        self._tokens: dict[str, CancellationToken] = {}
        self._tasks: dict[str, tuple[asyncio.Task[Any], bool]] = {}

    def get_or_create(self, task_id: str) -> CancellationToken:
        token = self._tokens.get(task_id)
        if token is None:
            token = CancellationToken(task_id=task_id)
            self._tokens[task_id] = token
        return token

    def cancel(self, task_id: str, reason: str = "") -> bool:
        token = self._tokens.get(task_id)
        if token is None:
            return False
        token.cancel(reason)
        bound = self._tasks.get(task_id)
        if bound is not None:
            task, hard_cancel = bound
            if hard_cancel and not task.done():
                task.cancel(reason)
        return True

    def bind_task(
        self,
        task_id: str,
        task: asyncio.Task[Any],
        *,
        hard_cancel: bool,
    ) -> CancellationToken:
        """Bind a real task to a token.

        `hard_cancel` is only valid for operations known to be cancellation
        safe. Side-effecting tools should use cooperative cancellation and
        report their real terminal result.
        """

        token = self.get_or_create(task_id)
        self._tasks[task_id] = (task, hard_cancel)
        return token

    def remove(self, task_id: str) -> None:
        self._tokens.pop(task_id, None)
        self._tasks.pop(task_id, None)

    def is_hard_cancel_safe(self, task_id: str) -> bool:
        bound = self._tasks.get(task_id)
        return bool(bound and bound[1])

    def cancel_all(self, reason: str = "") -> int:
        for task_id in list(self._tokens):
            self.cancel(task_id, reason)
        return len(self._tokens)
