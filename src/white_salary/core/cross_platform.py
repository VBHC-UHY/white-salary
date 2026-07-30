"""Durable QQ/desktop bridge backed by the Agent Runtime outbox."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from white_salary.core.runtime.models import (
    ChannelAddress,
    DeliveryRecord,
    DeliveryState,
)
from white_salary.core.runtime.store import RuntimeStore


class CrossPlatformBridge:
    """Process-wide bridge with backwards-compatible push/pop methods."""

    DESKTOP_PLATFORM = "desktop_bridge"
    QQ_PLATFORM = "qq_bridge"
    DIRECT_DELIVERY = "direct"
    EVENT_PROMPT_DELIVERY = "event_prompt"
    # 直投（direct）消息会被原样显示/说给用户。历史上这里泄漏过两类内容
    # （内部提示词被当成消息本体、源端确认语被送到目标端），所以入队前做
    # 最后一道拦截。标记分三类、三种判据——2026-07-29 对抗审查实证：
    # 单一"子串命中即拒"会把"文件我已经发过去了，记得查收"这类最平常的
    # 转告消息也拦死，而转告恰恰是直投工具的本职。
    #
    # ① 结构性标记：内部提示词的括号标签与完整指令句。自然转告文本不会
    #    包含"[跨渠道消息撰写"或"请以白在桌面端"——命中即拒。
    _STRUCTURAL_LEAK_MARKERS = (
        "[跨渠道消息撰写",
        "[工具选择提示]",
        "[意图]",
        "[主动对话触发]",
        "请以白在桌面端",
        "请现在自然地回复用户",
        "上一版仍不能发送",
    )
    # ② 上下文碎片：内部提示词里的语境标注词。单独出现在自然中文里完全
    #    正常（"今天当前心情不错"），但整段上下文块被误贴进消息时会同时
    #    出现多个，或者出现在句首（"当前心情80分，……"）——
    #    句首命中或 ≥2 个不同碎片才拒。
    _CONTEXT_FRAGMENT_MARKERS = (
        "当前心情",
        "语气模式",
        "关系等级",
        "重要记忆",
        "用户习惯",
        "工具小助手",
        "输出最终消息",
        "请直接输出",
        "现在回复用户",
        "system prompt",
        "tool result",
    )
    # ③ 源端确认语：这些话是说给发起转告那一侧听的（"发了，去QQ看看"）。
    #    如果被投递的消息基本上"就是这句话"（长度 ≤ 标记 + 4），说明 LLM
    #    把源端应答填进了 message；长消息里自然提到"发过去了"是转告正文，
    #    放行。
    _SOURCE_CONFIRMATION_MARKERS = (
        "发了，去QQ看看",
        "发了，去桌面看看",
        "已经发到QQ",
        "已经发到桌面",
        "已推送到QQ",
        "已推送到桌面",
        "发过去了",
        "收到你的消息了，有什么吩咐",
        "让我发什么消息",
        "要发什么消息",
    )
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._store = RuntimeStore(
                Path.cwd() / "data" / "runtime" / "agent_runtime.db"
            )
            cls._instance = instance
        return cls._instance

    @classmethod
    def configure(cls, db_path: str | Path) -> "CrossPlatformBridge":
        bridge = cls()
        bridge._store = RuntimeStore(db_path)
        return bridge

    @property
    def store(self) -> RuntimeStore:
        return self._store

    def push_to_desktop(
        self,
        message: str,
        from_user: str = "",
        source: str = "qq",
        delivery_kind: str = "",
    ) -> str:
        source = str(source or "qq").strip() or "qq"
        delivery_kind = self._normalize_desktop_delivery_kind(
            delivery_kind,
            source=source,
        )
        message = self._validated_message(
            message,
            direct=delivery_kind == self.DIRECT_DELIVERY,
            context=f"desktop, source={source}",
        )
        delivery = self._store.enqueue_delivery(
            ChannelAddress(platform=self.DESKTOP_PLATFORM, address="primary"),
            {
                "message": message,
                "from_user": str(from_user),
                "source": source,
                "delivery_kind": delivery_kind,
            },
            conversation_key="bridge:desktop:primary",
            replay_safe=False,
        )
        logger.debug(f"[Bridge] queued -> desktop: {str(message)[:30]}")
        return delivery.id

    def claim_desktop_messages(self, limit: int = 50) -> list[dict[str, Any]]:
        records = self._store.claim_due_deliveries(
            platform=self.DESKTOP_PLATFORM,
            limit=limit,
            lease_seconds=30.0,
        )
        return [self._to_message(record) for record in records]

    def pop_desktop_messages(self) -> list[dict[str, Any]]:
        """Legacy destructive pop; new consumers should claim and then ack."""
        messages = self.claim_desktop_messages()
        for message in messages:
            self.ack_message(message, receipt={"mode": "legacy_pop"})
        return messages

    def push_to_qq(
        self,
        message: str,
        target_id: str = "",
        is_group: bool = False,
    ) -> str:
        target_id = str(target_id or "").strip()
        # QQ 方向没有 event_prompt 概念，永远是把文本原样发给对方 → 恒为直投
        message = self._validated_message(
            message, direct=True, context=f"qq, target={target_id or 'default'}"
        )
        delivery = self._store.enqueue_delivery(
            ChannelAddress(
                platform=self.QQ_PLATFORM,
                address=target_id,
                is_group=bool(is_group),
            ),
            {
                "message": message,
                "target_id": target_id,
                "is_group": bool(is_group),
            },
            conversation_key=(
                f"bridge:qq:group:{target_id}"
                if is_group
                else f"bridge:qq:private:{target_id or 'default'}"
            ),
            replay_safe=False,
        )
        logger.debug(f"[Bridge] queued -> QQ: {str(message)[:30]}")
        return delivery.id

    def claim_qq_messages(self, limit: int = 50) -> list[dict[str, Any]]:
        records = self._store.claim_due_deliveries(
            platform=self.QQ_PLATFORM,
            limit=limit,
            lease_seconds=30.0,
        )
        return [self._to_message(record) for record in records]

    def pop_qq_messages(self) -> list[dict[str, Any]]:
        """Legacy destructive pop; new consumers should claim and then ack."""
        messages = self.claim_qq_messages()
        for message in messages:
            self.ack_message(message, receipt={"mode": "legacy_pop"})
        return messages

    def ack_message(
        self,
        message_or_id: dict[str, Any] | str,
        *,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        delivery_id = self._delivery_id(message_or_id)
        if delivery_id:
            self._store.mark_delivery_delivered(
                delivery_id,
                receipt or {"accepted": True},
                claim_token=self._claim_token(message_or_id),
            )

    def retry_message(self, message_or_id: dict[str, Any] | str, error: str) -> None:
        delivery_id = self._delivery_id(message_or_id)
        if delivery_id:
            self._store.mark_delivery_failed(
                delivery_id,
                error,
                claim_token=self._claim_token(message_or_id),
            )

    def mark_message_unknown(self, message_or_id: dict[str, Any] | str, error: str) -> None:
        delivery_id = self._delivery_id(message_or_id)
        if delivery_id:
            self._store.mark_delivery_unknown(
                delivery_id,
                error,
                claim_token=self._claim_token(message_or_id),
            )

    def reject_message(self, message_or_id: dict[str, Any] | str, error: str) -> None:
        delivery_id = self._delivery_id(message_or_id)
        if delivery_id:
            self._store.mark_delivery_permanently_failed(
                delivery_id,
                error,
                claim_token=self._claim_token(message_or_id),
            )

    async def wait_for_delivery(
        self,
        delivery_id: str,
        *,
        timeout: float = 20.0,
        poll_interval: float = 0.2,
    ) -> DeliveryRecord | None:
        """等待消费端回执——不把"已入队"当成"已送达"。

        终态集合与 store 的状态机对齐：
        - DELIVERED：消费端明确 ack；
        - FAILED：只有耗尽 max_attempts 或被 reject_message 永久拒绝才会写入
          （可重试失败会回到 PENDING + 退避，见 store.mark_delivery_failed），
          所以把 FAILED 报给上游不存在"稍后重试又送到了 → 上游已换路重发
          → 用户收到两条"的窗口；
        - UNKNOWN：结果不明，只能人工 requeue_unknown_delivery 复活，
          对回执而言就是终态；
        - CANCELLED：已取消。
        超时返回当前记录（可能仍是 PENDING/SENDING），由调用方决定怎么表述
        "尚未确认"；返回 None 表示记录不存在。

        用轮询而非事件通知：状态的权威在 SQLite，结算路径有多条（消费循环
        ack / 租约过期清扫 / 人工 requeue），事件通知得给每条路径插桩且仍要
        兜漏；按默认参数最多 100 次索引读，代价可忽略。
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, float(timeout))
        terminal_states = {
            DeliveryState.DELIVERED,
            DeliveryState.FAILED,
            DeliveryState.UNKNOWN,
            DeliveryState.CANCELLED,
        }

        while True:
            delivery = self._store.get_delivery(delivery_id)
            if delivery is None or delivery.state in terminal_states:
                return delivery

            remaining = deadline - loop.time()
            if remaining <= 0:
                return delivery
            await asyncio.sleep(min(max(0.01, poll_interval), remaining))

    @property
    def has_desktop_messages(self) -> bool:
        return self._store.has_pending_deliveries(self.DESKTOP_PLATFORM)

    @property
    def has_qq_messages(self) -> bool:
        return self._store.has_pending_deliveries(self.QQ_PLATFORM)

    @classmethod
    def _to_message(cls, record: DeliveryRecord) -> dict[str, Any]:
        message = dict(record.payload)
        if record.target.platform == cls.DESKTOP_PLATFORM:
            message["delivery_kind"] = cls._normalize_desktop_delivery_kind(
                message.get("delivery_kind", ""),
                source=str(message.get("source", "qq")),
            )
        message["_delivery_id"] = record.id
        message["_delivery_attempt"] = record.attempts
        message["_delivery_claim_token"] = record.claim_token
        return message

    @classmethod
    def _normalize_desktop_delivery_kind(cls, value: object, *, source: str) -> str:
        """Keep old outbox rows compatible while separating transport from prompts."""
        normalized = str(value or "").strip().lower()
        if normalized in {cls.DIRECT_DELIVERY, cls.EVENT_PROMPT_DELIVERY}:
            return normalized
        # Existing game rows are raw events that need one persona response. Every
        # other legacy source already contains user-facing text and must not be
        # sent through the LLM again.
        if str(source or "").strip().lower() == "game":
            return cls.EVENT_PROMPT_DELIVERY
        return cls.DIRECT_DELIVERY

    @classmethod
    def _find_unsafe_marker(cls, message: str) -> str | None:
        """按三类判据扫描直投消息；返回命中说明，None 表示安全。"""
        lowered = message.lower()
        for marker in cls._STRUCTURAL_LEAK_MARKERS:
            if marker.lower() in lowered:
                return f"internal prompt text (marker: {marker!r})"
        fragment_hits = [
            marker
            for marker in cls._CONTEXT_FRAGMENT_MARKERS
            if marker.lower() in lowered
        ]
        if fragment_hits and (
            len(fragment_hits) >= 2
            or any(lowered.startswith(m.lower()) for m in fragment_hits)
        ):
            return f"internal context fragments (markers: {fragment_hits!r})"
        for marker in cls._SOURCE_CONFIRMATION_MARKERS:
            if marker.lower() in lowered and len(message) <= len(marker) + 4:
                return f"source-side confirmation text (marker: {marker!r})"
        return None

    @classmethod
    def _validated_message(cls, value: object, *, direct: bool, context: str) -> str:
        """入队前的最后一道拦截，坏消息不落库。

        空消息对任何投递类型都是缺陷（此前 str(None) 会把字面量 "None"
        投给用户）。标记扫描只对直投生效——event_prompt 是给 LLM 的事件
        提示，本来就该包含"[意图]"之类的内部标注。

        每次拦截都记 WARNING（含 context 与消息前缀）：误伤必须可观测，
        否则"每次都有几条无声丢弃"和"真泄漏被拦下"在日志里毫无区别。
        拒因写进异常消息，工具层把它转告给模型即可改写重试；服务方
        （reminder/qzone/game）的文案经三类判据评估过不会撞上——
        提醒文案带"到点啦"前缀必然超过确认语长度阈值，也不含结构性标签。
        """
        message = str(value or "").strip()
        if not message:
            raise ValueError("cross-platform delivery message is empty")
        if not direct:
            return message

        verdict = cls._find_unsafe_marker(message)
        if verdict is not None:
            logger.warning(
                f"[Bridge] 直投消息被拦截（{context}）: {verdict}; "
                f"preview={message[:40]!r}"
            )
            raise ValueError(f"cross-platform direct delivery contains {verdict}")
        return message

    @staticmethod
    def _delivery_id(message_or_id: dict[str, Any] | str) -> str:
        if isinstance(message_or_id, dict):
            return str(message_or_id.get("_delivery_id", ""))
        return str(message_or_id or "")

    @staticmethod
    def _claim_token(message_or_id: dict[str, Any] | str) -> str:
        if isinstance(message_or_id, dict):
            return str(message_or_id.get("_delivery_claim_token", ""))
        return ""
