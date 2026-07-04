"""
Conversation initiative engine.

This module is deliberately small and UI-agnostic.  It only decides whether a
recent user/assistant exchange is worth a natural follow-up after the user goes
quiet for a short time.  It does not send messages, touch devices, or execute
tools by itself.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from loguru import logger

from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, MessageRole


InitiativeAction = Literal["silence", "wait", "speak"]


@dataclass(frozen=True)
class InitiativeConfig:
    enabled: bool = True
    first_delay_seconds: float = 30.0
    wait_delay_seconds: float = 60.0
    max_waits: int = 2
    min_user_chars: int = 2


@dataclass(frozen=True)
class InitiativeDecision:
    action: InitiativeAction
    reason: str = ""
    prompt: str = ""
    delay_seconds: float = 0.0

    @property
    def should_speak(self) -> bool:
        return self.action == "speak" and bool(self.prompt.strip())


@dataclass
class PendingContinuation:
    id: int
    user_message: str
    assistant_reply: str
    platform: str
    user_id: str
    created_at: float
    due_at: float
    waits: int = 0


_JUDGE_SYSTEM = """You decide whether an AI desktop companion should naturally continue a current conversation.

Rules:
- This is about the current conversation only, not random hourly auto chat.
- Do not use keyword shortcuts. Judge from the recent exchange, the user's state, and whether the topic still feels open.
- If the user is busy, ending the chat, asking for quiet, or the topic feels complete, choose silence.
- If one gentle follow-up would feel human, choose speak.
- If it is too soon but the topic may still be open, choose wait.
- The follow-up must be brief, natural, and should not call tools or operate the computer.

Return JSON only:
{
  "action": "silence" | "wait" | "speak",
  "reason": "short reason",
  "prompt": "what the companion should say or think about next",
  "delay_seconds": 60
}
"""


class InitiativeEngine:
    """Tracks one pending conversational follow-up and asks an LLM to judge it."""

    def __init__(
        self,
        llm: Optional[LLMInterface],
        config: Optional[InitiativeConfig] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._llm = llm
        self._config = config or InitiativeConfig()
        self._clock = clock
        self._pending: Optional[PendingContinuation] = None
        self._next_id = 1

    @property
    def pending_id(self) -> int | None:
        return self._pending.id if self._pending is not None else None

    def cancel_pending(self) -> None:
        self._pending = None

    def record_turn(
        self,
        user_message: str,
        assistant_reply: str,
        *,
        platform: str = "desktop",
        user_id: str = "desktop",
    ) -> int | None:
        """Create a pending follow-up after a real user turn."""
        if not self._config.enabled:
            self._pending = None
            return None
        user_text = (user_message or "").strip()
        reply_text = (assistant_reply or "").strip()
        if len(user_text) < self._config.min_user_chars or not reply_text:
            self._pending = None
            return None

        now = self._clock()
        pending = PendingContinuation(
            id=self._next_id,
            user_message=user_text,
            assistant_reply=reply_text,
            platform=platform,
            user_id=user_id,
            created_at=now,
            due_at=now + self._config.first_delay_seconds,
        )
        self._next_id += 1
        self._pending = pending
        return pending.id

    def seconds_until_due(self, pending_id: int | None = None) -> float | None:
        pending = self._pending
        if pending is None:
            return None
        if pending_id is not None and pending.id != pending_id:
            return None
        return max(0.0, pending.due_at - self._clock())

    async def evaluate_if_due(self, pending_id: int) -> InitiativeDecision:
        pending = self._pending
        if pending is None or pending.id != pending_id:
            return InitiativeDecision(action="silence", reason="stale")
        if self._clock() < pending.due_at:
            return InitiativeDecision(
                action="wait",
                reason="not due",
                delay_seconds=pending.due_at - self._clock(),
            )

        decision = await self._judge(pending)
        if decision.action == "wait":
            if pending.waits >= self._config.max_waits:
                self._pending = None
                return InitiativeDecision(action="silence", reason="max waits reached")
            delay = decision.delay_seconds or self._config.wait_delay_seconds
            pending.waits += 1
            pending.due_at = self._clock() + max(5.0, delay)
            return InitiativeDecision(
                action="wait",
                reason=decision.reason,
                delay_seconds=max(5.0, delay),
            )

        self._pending = None
        return decision

    async def _judge(self, pending: PendingContinuation) -> InitiativeDecision:
        if self._llm is None:
            return InitiativeDecision(action="silence", reason="no initiative llm")

        user_prompt = (
            f"Platform: {pending.platform}\n"
            f"User id: {pending.user_id}\n"
            f"User last message:\n{pending.user_message}\n\n"
            f"Assistant last reply:\n{pending.assistant_reply}\n\n"
            "The user has stopped sending messages for a short while. "
            "Judge whether the companion should continue."
        )
        try:
            raw = await self._llm.chat_completion(
                messages=[
                    Message(role=MessageRole.SYSTEM, content=_JUDGE_SYSTEM),
                    Message(role=MessageRole.USER, content=user_prompt),
                ],
                temperature=0.2,
                max_tokens=400,
            )
            return self._parse_decision(raw)
        except Exception as exc:
            logger.warning(f"[Initiative] judge failed: {exc}")
            return InitiativeDecision(action="silence", reason="judge failed")

    def _parse_decision(self, raw: str) -> InitiativeDecision:
        text = (raw or "").strip()
        if not text:
            return InitiativeDecision(action="silence", reason="empty judge reply")

        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            text = match.group(0)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"[Initiative] invalid judge json: {raw[:120]}")
            return InitiativeDecision(action="silence", reason="invalid judge json")

        action = str(data.get("action") or "silence").strip().lower()
        if action not in {"silence", "wait", "speak"}:
            action = "silence"
        reason = str(data.get("reason") or "").strip()
        prompt = str(data.get("prompt") or "").strip()
        try:
            delay = float(data.get("delay_seconds") or 0.0)
        except (TypeError, ValueError):
            delay = 0.0

        if action == "speak" and not prompt:
            action = "silence"
            reason = reason or "missing prompt"
        return InitiativeDecision(
            action=action,  # type: ignore[arg-type]
            reason=reason,
            prompt=prompt,
            delay_seconds=max(0.0, delay),
        )
