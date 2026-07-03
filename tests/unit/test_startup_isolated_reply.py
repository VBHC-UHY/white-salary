"""
测试离线补发的隔离回复（2026-07-02 审计修复（批4）更新）。

批4之前的实现是"快照→置空→恢复"共享 agent 的 _messages：非并发安全，
补发期间并发进入的实时消息会在恢复快照时被整体丢弃。
批4改为【构造轻量独立 agent】：浅拷贝共享 agent、换上一次性 ShortTermMemory，
共享记忆全程不被触碰。本文件按新语义测试：
  1. 补发不污染共享上下文（原上下文原样保留、离线消息不残留）
  2. 补发生成用的是干净的独立上下文（不带共享历史）
  3. 关键回归：补发进行中并发写入共享记忆的消息不会丢失
"""

import asyncio

from white_salary.core.services.startup_checker import StartupChecker
from white_salary.core.memory.short_term import ShortTermMemory


class _FakeAgent:
    """假 agent：带一个真实 ShortTermMemory；chat() 会往 self._memory 写消息。"""

    def __init__(self) -> None:
        self._memory = ShortTermMemory(max_turns=20)

    async def chat(self, user_input: str, **kwargs) -> str:
        # 模拟真实 ChatAgent：把这轮写进（自己当前持有的）短期记忆
        self._memory.add_user_message(user_input)
        self._memory.add_assistant_message("（离线回复）")
        return "（离线回复）"


class _SlowGateAgent(_FakeAgent):
    """chat() 中途挂起等 gate 的假 agent——模拟LLM耗时，制造并发窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        # 用共享dict记录：浅拷贝体和本体共享同一个dict引用，拷贝体写入本体可见
        self.seen: dict[str, int] = {}

    async def chat(self, user_input: str, **kwargs) -> str:
        # 记录进入时自己（可能是拷贝体）看到的上下文条数
        self.seen["at_entry"] = self._memory.message_count
        self._memory.add_user_message(user_input)
        await self.gate.wait()  # 挂起，等测试方放行（模拟等LLM）
        self._memory.add_assistant_message("（离线回复）")
        return "（离线回复）"


class TestIsolatedReply:
    """_isolated_reply 用独立 agent 补发，共享记忆全程不被触碰。"""

    async def test_restores_existing_context(self, tmp_path) -> None:
        """补发后，原有正常对话上下文必须原样还在、且离线消息不残留。"""
        agent = _FakeAgent()
        # 预置一段"正常对话"上下文
        agent._memory.add_user_message("我们刚才在聊周末去哪玩")
        agent._memory.add_assistant_message("对呀，在看露营")
        assert agent._memory.message_count == 2

        checker = StartupChecker(adapter=object(), agent=agent, data_dir=str(tmp_path))
        reply = await checker._isolated_reply("[离线] 有人给你发了消息", user_id="qq_1")

        assert reply == "（离线回复）"
        # 关键断言：原对话未被冲掉，离线消息也没残留进来
        msgs = agent._memory.get_messages()
        assert len(msgs) == 2
        assert msgs[0].content == "我们刚才在聊周末去哪玩"
        assert msgs[1].content == "对呀，在看露营"

    async def test_generation_uses_clean_independent_context(self, tmp_path) -> None:
        """补发生成用的是干净的独立上下文，看不到共享历史。"""
        agent = _SlowGateAgent()
        agent._memory.add_user_message("正常对话A")
        agent._memory.add_assistant_message("正常回复A")
        agent.gate.set()  # 不需要挂起，直接放行

        checker = StartupChecker(adapter=object(), agent=agent, data_dir=str(tmp_path))
        await checker._isolated_reply("[离线] 新消息", user_id="qq_2")

        assert agent.seen["at_entry"] == 0       # 拷贝体进入chat时是全新空上下文
        assert agent._memory.message_count == 2  # 共享记忆没被动过

    async def test_concurrent_message_not_lost_during_reply(self, tmp_path) -> None:
        """
        关键回归（批4修复点）：补发挂起期间并发写入共享记忆的实时消息，
        补发结束后必须原样还在（旧实现会在恢复快照时把它整体丢弃）。
        """
        agent = _SlowGateAgent()
        agent._memory.add_user_message("正常对话A")

        checker = StartupChecker(adapter=object(), agent=agent, data_dir=str(tmp_path))
        task = asyncio.create_task(
            checker._isolated_reply("[离线] 补发消息", user_id="qq_3")
        )
        await asyncio.sleep(0)  # 让补发task跑到 gate.wait() 挂起

        # 模拟实时消息处理并发写入共享记忆
        agent._memory.add_user_message("并发进来的实时消息")
        agent._memory.add_assistant_message("实时回复")

        agent.gate.set()  # 放行补发
        reply = await task
        assert reply == "（离线回复）"

        # 并发写入的记录必须还在，离线补发的输入不能混进共享记忆
        contents = [m.content for m in agent._memory.get_messages()]
        assert "并发进来的实时消息" in contents
        assert "实时回复" in contents
        assert "正常对话A" in contents
        assert all("[离线]" not in c for c in contents)
