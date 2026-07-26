"""端到端验证 QQ 群聊的"别说话"停止指令——真后端 + 假 NapCat。

为什么必须端到端测：停止生效的表现是**白没有发出任何消息**。这个"没发生"
只能靠一个真实的 OneBot 服务端来观察——看它有没有收到 send_group_msg。
单测能验证键名一致，但验证不了"从群消息进来到不发消息出去"这条完整链路。

历史缺陷：活动租约的键分属两套命名空间（写入方 `qq:group:{gid}`，
停止指令的读取/关闭方用了聊天上下文的 `group:{gid}`），两套键永不相交，
于是停止指令既判不中也关不掉，白继续接话。而两边各自内部自洽，单测全绿。
"""

from __future__ import annotations

import asyncio
import itertools
import tempfile
import time
from pathlib import Path

import pytest

# 整个文件都是 e2e：默认套件不跑，需 -m e2e 显式开启
pytestmark = pytest.mark.e2e

pytest.importorskip("websockets", reason="端到端测试需要 websockets")

from .fake_napcat import FakeNapCat  # noqa: E402
from .harness import E2EStack, free_port  # noqa: E402

MEMBER_ID = 800200
OWNER_ID = 10001  # 与 harness 里 qq.family_qq 一致

# 每个用例用独立群号：群上下文、活动租约、好感度都会落到项目根的 data/ 下并
# 跨用例/跨运行共享。共用一个群号会让前一个用例留下的租约与上下文影响后一个，
# 出现"单独跑能过、连着跑就挂"这种最难查的假故障。
_GROUP_SEQ = itertools.count(1)


def _fresh_group_id() -> int:
    """本次运行内唯一的群号（时间戳做前缀，避免跨运行撞车）。"""
    return 700_000_000 + (int(time.time()) % 100_000) * 10 + next(_GROUP_SEQ)


@pytest.fixture(scope="module")
def event_loop_policy():
    return asyncio.get_event_loop_policy()


class QQStack:
    """假 NapCat + 真后端（开启 QQ）。"""

    def __init__(self, tmp: Path) -> None:
        self.napcat_port = free_port()
        self.napcat = FakeNapCat(self.napcat_port)
        self.stack = E2EStack(tmp, qq_enabled=True, napcat_port=self.napcat_port)

    async def __aenter__(self) -> "QQStack":
        await self.napcat.start()
        # 后端启动是阻塞的子进程操作，放到线程里以免卡住事件循环
        await asyncio.to_thread(self.stack.start)
        connected = await self.napcat.wait_connected(timeout=45)
        if not connected:
            log = self.stack.backend_log()
            await self.__aexit__(None, None, None)
            pytest.skip(f"白没有连上假 NapCat，QQ 链路无法验证。后端日志：\n{log[-1500:]}")
        # 给 QQ 服务留一点完成初始化的时间（好感度/上下文/租约库）
        await asyncio.sleep(2.0)
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            await self.napcat.stop()
        finally:
            await asyncio.to_thread(self.stack.stop)


@pytest.mark.asyncio
async def test_wake_word_gets_a_reply() -> None:
    """先确认正向链路是通的：叫"白"应当真的收到一条群消息回复。

    这条是后面所有"它不该说话"断言的前提——如果白根本不会说话，
    那些断言就毫无意义（会假通过）。
    """
    tmp = Path(tempfile.mkdtemp(prefix="ws-e2e-qq-wake-"))
    group_id = _fresh_group_id()
    async with QQStack(tmp) as ctx:
        ctx.stack.configure_upstream(chunk_delay=0.05)
        ctx.napcat.clear()

        await ctx.napcat.push_group_message(
            text="白 在吗", group_id=group_id, user_id=MEMBER_ID
        )
        spoke = await ctx.napcat.wait_for_send(timeout=40)

        assert spoke, (
            "被唤醒词点名后白没有回复，QQ 正向链路不通，"
            f"后续停止指令断言无意义。后端日志：\n{ctx.stack.backend_log()[-2000:]}"
        )


@pytest.mark.asyncio
async def test_stop_request_inside_active_window_is_honored() -> None:
    """活动窗口内、**不 @ 白**说"别回了"，白必须立刻闭嘴。

    这正是历史缺陷的确切场景：键名不一致导致 is_candidate 恒 False，
    停止请求被完全忽略，白继续接话。
    """
    tmp = Path(tempfile.mkdtemp(prefix="ws-e2e-qq-stop-"))
    group_id = _fresh_group_id()
    async with QQStack(tmp) as ctx:
        ctx.stack.configure_upstream(chunk_delay=0.05)

        # 1) 先唤醒，建立活动租约
        ctx.napcat.clear()
        await ctx.napcat.push_group_message(
            text="白 陪我聊会儿", group_id=group_id, user_id=MEMBER_ID
        )
        assert await ctx.napcat.wait_for_send(timeout=40), (
            "唤醒阶段白没有回复，无法进入活动窗口"
        )
        await asyncio.sleep(1.0)

        # 2) 同一用户在活动窗口内说停止指令（不带唤醒词、不 @）
        ctx.napcat.clear()
        await ctx.napcat.push_group_message(
            text="先别回复我了", group_id=group_id, user_id=MEMBER_ID
        )
        spoke_after_stop = await ctx.napcat.wait_for_send(timeout=15)
        assert not spoke_after_stop, (
            "对停止指令本身还回了话。收到的消息："
            f"{ctx.napcat.sent_messages()}"
        )

        # 3) 停止之后，同一用户的普通发言也不该再被接话
        ctx.napcat.clear()
        await ctx.napcat.push_group_message(
            text="今天天气还不错啊", group_id=group_id, user_id=MEMBER_ID
        )
        spoke_after_close = await ctx.napcat.wait_for_send(timeout=15)
        assert not spoke_after_close, (
            "停止指令没有真正关闭活动租约——白在窗口内继续接话了，"
            "这就是键名不一致缺陷的表现。收到的消息："
            f"{ctx.napcat.sent_messages()}"
        )


@pytest.mark.asyncio
async def test_unrelated_chatter_is_not_answered() -> None:
    """群里没叫白时不许插嘴（防止把"停止生效"误判成"白本来就不说话"）。"""
    tmp = Path(tempfile.mkdtemp(prefix="ws-e2e-qq-quiet-"))
    group_id = _fresh_group_id()
    async with QQStack(tmp) as ctx:
        ctx.stack.configure_upstream(chunk_delay=0.05)
        ctx.napcat.clear()

        await ctx.napcat.push_group_message(
            text="你们中午吃什么", group_id=group_id, user_id=MEMBER_ID
        )
        await ctx.napcat.push_group_message(
            text="随便吧我都行", group_id=group_id, user_id=MEMBER_ID + 1, nickname="群友乙"
        )
        spoke = await ctx.napcat.wait_for_send(timeout=12)

        assert not spoke, (
            f"没叫白它却插嘴了。收到的消息：{ctx.napcat.sent_messages()}"
        )
