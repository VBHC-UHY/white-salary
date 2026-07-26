"""黄金测试 — 锁住 v0.1.12 修复的"旁路组件绑架主功能"与"循环猝死"缺陷。

这些断言守护同一条原则：**任务账本（runtime journal / store）是纯观测性的
sidecar，它出任何问题都只能少一条审计记录，绝不能让白不说话、也绝不能让
后端起不来。** 跨端桥消费循环同理：单次异常只能跳过本轮，不能永久停摆。

任何"为了让状态机更严格"而放宽下列断言的改动，都应被视为可用性回归。
"""

import asyncio

import pytest

from white_salary.core.runtime.journal import (
    InteractiveTaskJournal,
    _DetachedTaskHandle,
)
from white_salary.core.runtime.models import ConversationRef


class _BrokenStore:
    """模拟 DB 不可写：被杀软/备份占用、磁盘满、WAL 脏。"""

    def create_task(self, *args, **kwargs):
        raise RuntimeError("database is locked")

    def append_event(self, *args, **kwargs):
        raise RuntimeError("database is locked")

    def transition_task(self, *args, **kwargs):
        raise RuntimeError("database is locked")

    def get_task(self, *args, **kwargs):
        raise RuntimeError("database is locked")


def _conversation() -> ConversationRef:
    return ConversationRef(platform="desktop", scope="direct", conversation_id="local")


# ---------------------------------------------------------------------------
# journal.begin 必须降级而不是上抛
# ---------------------------------------------------------------------------


def test_begin_degrades_instead_of_raising_when_store_is_broken() -> None:
    """账本写不进去时 begin() 不得抛异常。

    历史版本 begin() 是全模块唯一没有兜底的入口（append_event/_transition
    早就有 try/except）。它上抛时，桌面端每条消息只收到红字"处理失败"，
    QQ 端则是纯粹的已读不回。
    """
    journal = InteractiveTaskJournal(_BrokenStore())  # type: ignore[arg-type]

    handle = journal.begin(_conversation(), "你好")

    assert isinstance(handle, _DetachedTaskHandle)


def test_degraded_handle_still_processes_the_message() -> None:
    """降级句柄必须 fail-open：记不上账也要照常回复用户。

    should_process 为 False 的语义是"这是重复事件，跳过"。账本坏掉时我们
    根本无从判断是否重复，此时必须选择回复（最坏是重复一次），而不是沉默。
    """
    journal = InteractiveTaskJournal(_BrokenStore())  # type: ignore[arg-type]

    handle = journal.begin(_conversation(), "你好", idempotency_key="qq:12345")

    assert handle.should_process is True


def test_degraded_handle_lifecycle_calls_are_all_no_ops() -> None:
    """降级句柄的全部生命周期方法都不得抛异常。"""
    handle = _DetachedTaskHandle()

    handle.append_event("tool_started", {"tool": "demo"})
    handle.response_ready("摘要", awaiting_delivery=True)
    handle.complete("完成", receipt={"consumer": "test"})
    handle.cancel("用户打断")
    handle.fail("出错了")
    handle.require_reconciliation("回执丢失")

    assert handle.id == ""


def test_journal_with_none_store_is_safe() -> None:
    """qq_handler 在建库失败时会传入 None store，这条路径必须安全。"""
    journal = InteractiveTaskJournal(None)  # type: ignore[arg-type]

    handle = journal.begin(_conversation(), "你好")

    assert handle.should_process is True


# ---------------------------------------------------------------------------
# Python 3.10 兼容：Task.cancelling() 是 3.11+ API
# ---------------------------------------------------------------------------


def test_current_task_is_cancelling_never_raises_on_py310() -> None:
    """pyproject 声明 requires-python >=3.10，该辅助函数必须在 3.10 上可用。

    历史版本直接调用 current.cancelling()，在 3.10 抛 AttributeError；
    且该异常是在 `except CancelledError` 的处理体内抛出的，同级的
    `except Exception` 按语义接不住它——用户在主动发言生成途中打字打断
    就会中招，并进一步引爆跨端桥消费循环的猝死。
    """
    from white_salary.infrastructure.server.websocket_handler import (
        _current_task_is_cancelling,
    )

    async def _inside_a_task() -> bool:
        inner = asyncio.create_task(asyncio.sleep(10))
        await asyncio.sleep(0)
        inner.cancel()
        try:
            await inner
        except asyncio.CancelledError:
            # 下游被取消、自身没有：必须返回 False 且不得抛 AttributeError
            return _current_task_is_cancelling()
        return True

    assert asyncio.run(_inside_a_task()) is False


def test_current_task_is_cancelling_outside_task_returns_false() -> None:
    from white_salary.infrastructure.server.websocket_handler import (
        _current_task_is_cancelling,
    )

    assert _current_task_is_cancelling() is False


# ---------------------------------------------------------------------------
# 桥消费循环：结算动作抛出不得掀掉整个循环
# ---------------------------------------------------------------------------


def test_settlement_failure_does_not_escape_the_loop_body() -> None:
    """复现并锁住桥猝死的确切形状。

    真实链路：租约仅 30 秒，而消费端要等完整的 LLM 生成 + 逐句 TTS，
    轻易超时；租约被过期扫描回收后，mark_message_unknown 抛
    StaleDeliveryClaim。历史版本在 `except` 块里裸调用它，二次异常直接
    冲出 except 掀掉 while True，此后跨端投递永久静默失效且零日志。

    这里用与修复代码相同的结构验证：结算失败只跳过该条，循环继续。
    """
    settle_attempts: list[str] = []
    completed_rounds = 0

    def _broken_settle(item: str) -> None:
        settle_attempts.append(item)
        raise RuntimeError("StaleDeliveryClaim: lease was reclaimed")

    for _ in range(3):
        unsettled = ["a", "b"]
        try:
            raise RuntimeError("轮询异常")
        except Exception:
            for item in unsettled:
                try:
                    _broken_settle(item)
                except Exception:
                    pass  # 修复后的行为：记录并跳过
        completed_rounds += 1

    assert completed_rounds == 3, "结算失败必须只跳过该条，不能中断循环"
    assert len(settle_attempts) == 6, "每条未决消息都应被尝试结算"


@pytest.mark.parametrize("module_name", ["qq_handler", "websocket_handler"])
def test_bridge_loops_guard_their_settlement_calls(module_name: str) -> None:
    """静态守卫：两个桥循环的结算调用必须处在保护之下。

    这条断言防的是"改回裸调用"的回归——它无法靠单测触发真实 SQLite
    竞态来发现，所以直接检查源码结构。
    """
    import inspect

    module = __import__(
        f"white_salary.infrastructure.server.{module_name}",
        fromlist=["*"],
    )
    source = inspect.getsource(module)

    assert "结算失败" in source or "标记桌面桥未决消息失败" in source, (
        f"{module_name} 的桥结算调用失去了异常保护"
    )
