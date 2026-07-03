"""
测试短期记忆系统。
"""

from white_salary.core.interfaces.types import Message, MessageRole
from white_salary.core.memory.short_term import ShortTermMemory


class TestShortTermMemory:
    """测试短期记忆。"""

    def test_add_and_get_messages(self) -> None:
        """添加消息后能正确获取。"""
        mem = ShortTermMemory()
        mem.add_user_message("你好")
        mem.add_assistant_message("你好呀！")

        msgs = mem.get_messages()
        assert len(msgs) == 2
        assert msgs[0].role == MessageRole.USER
        assert msgs[0].content == "你好"
        assert msgs[1].role == MessageRole.ASSISTANT

    def test_message_count(self) -> None:
        """消息计数正确。"""
        mem = ShortTermMemory()
        assert mem.message_count == 0
        mem.add_user_message("1")
        assert mem.message_count == 1
        mem.add_assistant_message("2")
        assert mem.message_count == 2

    def test_turn_count(self) -> None:
        """轮数计算正确（轮数 = 用户消息数）。"""
        mem = ShortTermMemory()
        assert mem.turn_count == 0
        mem.add_user_message("问题1")
        assert mem.turn_count == 1
        mem.add_assistant_message("回答1")
        assert mem.turn_count == 1  # 还是1轮
        mem.add_user_message("问题2")
        assert mem.turn_count == 2

    def test_trim_when_exceeds_max_turns(self) -> None:
        """超过最大轮数时自动截断旧消息。"""
        mem = ShortTermMemory(max_turns=2)

        # 添加3轮对话
        for i in range(3):
            mem.add_user_message(f"问题{i}")
            mem.add_assistant_message(f"回答{i}")

        # 应该只保留最近2轮
        assert mem.turn_count <= 2

    def test_clear(self) -> None:
        """清空记忆。"""
        mem = ShortTermMemory()
        mem.add_user_message("测试")
        mem.add_assistant_message("回复")
        assert mem.message_count == 2

        mem.clear()
        assert mem.message_count == 0
        assert mem.turn_count == 0

    def test_get_context_with_system_message(self) -> None:
        """获取上下文时系统消息在最前面。"""
        mem = ShortTermMemory()
        mem.add_user_message("你好")

        sys_msg = Message(role=MessageRole.SYSTEM, content="你是AI")
        context = mem.get_context_messages(system_message=sys_msg)

        assert len(context) == 2
        assert context[0].role == MessageRole.SYSTEM  # 系统消息在最前
        assert context[1].role == MessageRole.USER

    def test_get_context_without_system_message(self) -> None:
        """没有系统消息时只返回对话历史。"""
        mem = ShortTermMemory()
        mem.add_user_message("你好")

        context = mem.get_context_messages()
        assert len(context) == 1
        assert context[0].role == MessageRole.USER

    def test_user_name(self) -> None:
        """用户名能正确保存。"""
        mem = ShortTermMemory()
        mem.add_user_message("你好", name="小白")

        msgs = mem.get_messages()
        assert msgs[0].name == "小白"
