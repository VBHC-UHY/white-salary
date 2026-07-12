"""
测试消息处理 message/processing.py 的三个组件。

覆盖：
  - MessageRouter：分类（命令/画图/搜索/看屏幕/默认聊天）、优先级、工具提示
  - TimePerception：未知用户间隔为 inf、记录后间隔≈0、时间上下文结构
  - MessageBuffer：缓冲、合并、清空、达上限返回 False
"""

import asyncio

from white_salary.core.llm_enhancer import LLMEnhancer, IntentType
from white_salary.core.message.processing import (
    MessageRouter,
    TimePerception,
    MessageBuffer,
)


class TestMessageRouter:
    def test_classify_command(self) -> None:
        assert MessageRouter().classify("/help") == "command"
        assert MessageRouter().classify("！重启服务") == "command"

    def test_classify_image(self) -> None:
        assert MessageRouter().classify("帮我画一张猫咪") == "image"

    def test_classify_search(self) -> None:
        assert MessageRouter().classify("搜一下Python教程") == "search"

    def test_classify_vision(self) -> None:
        assert MessageRouter().classify("看看屏幕上是什么") == "vision"

    def test_classify_default_chat(self) -> None:
        assert MessageRouter().classify("今天天气真好啊") == "chat"

    def test_priority_command_over_keyword(self) -> None:
        """同时命中命令(优先级10)和画图(20)时，命令优先。"""
        assert MessageRouter().classify("!画一张图") == "command"

    def test_tool_hint(self) -> None:
        assert "web_search" in MessageRouter().get_tool_hint("搜一下天气")
        assert MessageRouter().get_tool_hint("今天吃什么好呢") == ""


class TestLLMEnhancer:
    def test_action_requests_are_not_plain_chat(self) -> None:
        enhancer = LLMEnhancer()

        assert enhancer.analyze("白，发个表情包").intent == IntentType.REQUEST
        assert enhancer.analyze("发语音").intent == IntentType.REQUEST
        assert enhancer.analyze("截屏看一下").intent == IntentType.REQUEST

    def test_request_hint_does_not_force_two_word_reply(self) -> None:
        hint = LLMEnhancer().analyze("白，发个表情包").style_hint

        assert "不要只回一两个字" in hint
        assert "不要啰嗦" not in hint


class TestTimePerception:
    def test_gap_unknown_user_is_inf(self) -> None:
        tp = TimePerception()
        assert tp.get_gap_minutes("nobody") == float("inf")

    def test_gap_after_record(self) -> None:
        tp = TimePerception()
        tp.record_interaction("u1")
        assert tp.get_gap_minutes("u1") < 1  # 刚记录，间隔接近0分钟

    def test_time_context_structure(self) -> None:
        ctx = TimePerception().get_time_context("u1")
        assert "现在是" in ctx
        assert "今天是周" in ctx  # 含星期


class TestMessageBuffer:
    def test_add_buffers(self) -> None:
        b = MessageBuffer()
        assert b.add("u1", "你好") is True       # 缓冲中
        assert b.has_pending("u1") is True

    def test_flush_merges(self) -> None:
        b = MessageBuffer()
        b.add("u1", "你好")
        b.add("u1", "在吗")
        assert b.flush_now("u1") == "你好\n在吗"
        assert b.has_pending("u1") is False       # flush 后清空

    def test_flush_empty_returns_none(self) -> None:
        assert MessageBuffer().flush_now("nobody") is None

    def test_max_buffer_returns_false(self) -> None:
        b = MessageBuffer(max_buffer=3)
        assert b.add("u1", "1") is True
        assert b.add("u1", "2") is True
        assert b.add("u1", "3") is False          # 达上限，让调用方立即处理

    async def test_max_buffer_wakes_waiting_flush(self) -> None:
        b = MessageBuffer(wait_timeout=30.0, min_wait=30.0, max_buffer=2)
        assert b.add("u1", "1") is True
        task = asyncio.create_task(b.wait_and_flush("u1"))
        await asyncio.sleep(0)

        assert b.add("u1", "2") is False

        merged = await asyncio.wait_for(task, timeout=0.5)
        assert merged == "1\n2"
