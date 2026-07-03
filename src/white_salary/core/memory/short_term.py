"""
white_salary/core/memory/short_term.py

短期记忆（对话上下文）。

短期记忆就是"当前对话中说过的话"。
就像你和朋友聊天，你能记住刚才聊了什么——但如果关掉聊天窗口，就忘了。

功能：
  - 保存对话历史（用户说了什么、AI回了什么）
  - 限制最大轮数（太久的对话会吃太多token，所以要截断）
  - 清空对话（开始新话题时用）
"""

import json
import time
from pathlib import Path

from loguru import logger

from white_salary.core.interfaces.types import Message, MessageRole


class ShortTermMemory:
    """
    短期记忆管理器。

    存储当前对话的历史消息，并在超过最大轮数时自动截断旧消息。
    注意：系统消息（system prompt）不算在轮数里，也不会被截断。
    """

    def __init__(self, max_turns: int = 20, persist_path: str = "") -> None:
        """
        初始化短期记忆。

        参数:
            max_turns: 最多保留多少轮对话（1轮=用户说一句+AI回一句=2条消息）
            persist_path: 持久化文件路径（空=不持久化）
        """
        self._max_turns = max_turns
        self._messages: list[Message] = []
        self._persist_path = Path(persist_path) if persist_path else None

        # 从文件恢复上次的对话
        if self._persist_path:
            self._load_from_file()

    def add_message(self, message: Message) -> None:
        """
        添加一条消息到记忆中。

        如果添加后超过最大轮数，会自动删除最早的消息。
        每次添加后自动持久化到文件。

        参数:
            message: 要添加的消息
        """
        self._messages.append(message)

        # 检查是否需要截断
        self._trim_if_needed()

        # 持久化到文件
        self._save_to_file()

    def add_user_message(self, content: str, name: str | None = None) -> None:
        """
        快捷方法：添加一条用户消息。

        参数:
            content: 用户说的话
            name:    用户名（可选）
        """
        self.add_message(Message(
            role=MessageRole.USER,
            content=content,
            name=name,
        ))

    def add_assistant_message(self, content: str) -> None:
        """
        快捷方法：添加一条AI回复消息。

        参数:
            content: AI的回复
        """
        self.add_message(Message(
            role=MessageRole.ASSISTANT,
            content=content,
        ))

    def get_messages(self) -> list[Message]:
        """
        获取所有记忆中的消息。

        返回:
            消息列表（按时间顺序）
        """
        return list(self._messages)

    def get_context_messages(self, system_message: Message | None = None) -> list[Message]:
        """
        获取完整的对话上下文（系统消息 + 对话历史）。

        这个方法返回的列表可以直接发给LLM。

        参数:
            system_message: 系统消息（可选，会放在最前面）

        返回:
            完整的消息列表
        """
        result: list[Message] = []

        # 系统消息放最前面
        if system_message:
            result.append(system_message)

        # 加上对话历史
        result.extend(self._messages)

        return result

    def clear(self) -> None:
        """清空所有记忆（开始新话题时用）。"""
        count = len(self._messages)
        self._messages.clear()
        logger.debug(f"短期记忆已清空（删除了{count}条消息）")

    @property
    def message_count(self) -> int:
        """当前记忆中的消息数量。"""
        return len(self._messages)

    @property
    def turn_count(self) -> int:
        """
        当前的对话轮数。

        1轮 = 1条用户消息 + 1条AI回复（不是所有消息都成对，所以用用户消息数来算）
        """
        return sum(1 for m in self._messages if m.role == MessageRole.USER)

    def _trim_if_needed(self) -> None:
        """
        如果超过最大轮数，截断最早的消息。

        保留策略：删最早的消息，保留最近的。
        """
        while self.turn_count > self._max_turns and len(self._messages) > 2:
            removed = self._messages.pop(0)
            logger.debug(f"短期记忆截断: 删除了一条 {removed.role.value} 消息")

    def _save_to_file(self) -> None:
        """持久化对话历史到JSON文件。"""
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {"role": m.role.value, "content": m.content, "name": m.name}
                for m in self._messages
            ]
            with open(self._persist_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"对话历史保存失败: {e}")

    def _load_from_file(self) -> None:
        """从JSON文件恢复对话历史。"""
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                role = MessageRole(item["role"])
                msg = Message(role=role, content=item["content"], name=item.get("name"))
                self._messages.append(msg)
            logger.info(f"恢复了 {len(self._messages)} 条对话历史")
        except Exception as e:
            logger.warning(f"对话历史恢复失败: {e}")
