"""Durability and routing tests for the QQ/desktop bridge."""

from __future__ import annotations

import asyncio

import pytest

from white_salary.core.cross_platform import CrossPlatformBridge
from white_salary.core.runtime.models import DeliveryState


def _bridge(tmp_path) -> CrossPlatformBridge:
    return CrossPlatformBridge.configure(tmp_path / "runtime.db")


def test_desktop_message_is_claimed_then_explicitly_acked(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    delivery_id = bridge.push_to_desktop("hello", from_user="u1", source="qq")

    messages = bridge.claim_desktop_messages()
    assert len(messages) == 1
    assert messages[0]["message"] == "hello"
    assert messages[0]["delivery_kind"] == bridge.DIRECT_DELIVERY
    assert bridge.claim_desktop_messages() == []

    bridge.ack_message(messages[0], receipt={"accepted": True})
    saved = bridge.store.get_delivery(delivery_id)
    assert saved is not None and saved.state == DeliveryState.DELIVERED


def test_platform_claims_do_not_steal_each_others_messages(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    bridge.push_to_desktop("desktop")
    bridge.push_to_qq("qq", target_id="10001")

    desktop = bridge.claim_desktop_messages()
    qq = bridge.claim_qq_messages()

    assert [item["message"] for item in desktop] == ["desktop"]
    assert [item["message"] for item in qq] == ["qq"]


def test_unknown_non_replay_safe_delivery_is_not_duplicated(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    delivery_id = bridge.push_to_qq("once", target_id="10001")
    claimed = bridge.claim_qq_messages()
    assert len(claimed) == 1
    saved = bridge.store.get_delivery(delivery_id)
    assert saved is not None

    retried = bridge.store.claim_due_deliveries(
        platform=bridge.QQ_PLATFORM,
        now=saved.lease_until + 1,
    )
    after = bridge.store.get_delivery(delivery_id)

    assert retried == []
    assert after is not None and after.state == DeliveryState.UNKNOWN


def test_known_failure_can_be_retried(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    delivery_id = bridge.push_to_desktop("retry")
    message = bridge.claim_desktop_messages()[0]
    bridge.retry_message(message, "desktop temporarily unavailable")
    failed = bridge.store.get_delivery(delivery_id)
    assert failed is not None and failed.state == DeliveryState.PENDING

    claimed_again = bridge.store.claim_due_deliveries(
        platform=bridge.DESKTOP_PLATFORM,
        now=failed.available_at + 1,
    )
    assert [item.id for item in claimed_again] == [delivery_id]


def test_legacy_pop_preserves_shape_and_marks_delivered(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    delivery_id = bridge.push_to_desktop("legacy", source="reminder")

    messages = bridge.pop_desktop_messages()

    assert messages[0]["message"] == "legacy"
    assert messages[0]["source"] == "reminder"
    assert messages[0]["delivery_kind"] == bridge.DIRECT_DELIVERY
    saved = bridge.store.get_delivery(delivery_id)
    assert saved is not None and saved.state == DeliveryState.DELIVERED


def test_game_event_defaults_to_one_model_prompt(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    bridge.push_to_desktop("刚打赢了 Boss", source="game")

    message = bridge.claim_desktop_messages()[0]

    assert message["delivery_kind"] == bridge.EVENT_PROMPT_DELIVERY


def test_explicit_delivery_kind_overrides_source_default(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    bridge.push_to_desktop(
        "已经组织好的游戏播报",
        source="game",
        delivery_kind=bridge.DIRECT_DELIVERY,
    )

    message = bridge.claim_desktop_messages()[0]

    assert message["delivery_kind"] == bridge.DIRECT_DELIVERY


# ---------------------------------------------------------------------------
# 入队校验：坏消息不落库
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("unsafe", [
    "请以白在桌面端对小白说话的口吻输出最终消息：",
    "当前心情80分，家人999999分，请直接输出这条消息。",
    "发了，去QQ看看。",
    "好，发过去了。",
    "现在回复用户吧。",
    "收到你的消息了，有什么吩咐？",
])
def test_direct_delivery_rejects_internal_or_source_only_text(
    tmp_path,
    unsafe: str,
) -> None:
    """直投消息里出现内部提示词片段或源端确认语 → 拒绝且不入队。

    这两类泄漏都真实发生过：桌面上的白把"[跨渠道消息撰写...]"整段指令
    念了出来；或对着用户说"发了，去QQ看看"（那句话是说给发起方听的）。
    """
    bridge = _bridge(tmp_path)

    with pytest.raises(ValueError):
        bridge.push_to_desktop(unsafe, source="qq")
    with pytest.raises(ValueError):
        bridge.push_to_qq(unsafe, target_id="10001")

    assert bridge.claim_desktop_messages() == []
    assert bridge.claim_qq_messages() == []


def test_empty_message_is_rejected_before_enqueue(tmp_path) -> None:
    """空消息对任何投递类型都是缺陷。

    修复前 str(None) 会把字面量 "None" 原样投给用户。
    """
    bridge = _bridge(tmp_path)

    for bad in ["", "   ", None]:
        with pytest.raises(ValueError):
            bridge.push_to_desktop(bad)
        with pytest.raises(ValueError):
            bridge.push_to_qq(bad, target_id="10001")
        with pytest.raises(ValueError):
            bridge.push_to_desktop(bad, source="game")  # event_prompt 也拦空

    assert bridge.claim_desktop_messages() == []
    assert bridge.claim_qq_messages() == []


def test_event_prompt_delivery_keeps_game_event_semantics(tmp_path) -> None:
    """event_prompt 是给 LLM 的事件提示，"[意图]"这类内部标注是它的正文，
    标记扫描必须放行。"""
    bridge = _bridge(tmp_path)
    bridge.push_to_desktop(
        "[意图] 游戏刚刚打赢 Boss，请形成一次角色反应",
        source="game",
    )

    message = bridge.claim_desktop_messages()[0]
    assert message["delivery_kind"] == bridge.EVENT_PROMPT_DELIVERY


def test_marker_scan_follows_normalized_kind_not_source(tmp_path) -> None:
    """game 来源 + 显式 direct → 仍按直投拦截。判据是最终投递类型，不是来源。"""
    bridge = _bridge(tmp_path)

    with pytest.raises(ValueError):
        bridge.push_to_desktop(
            "[意图] 这段内部标注不该直投",
            source="game",
            delivery_kind=bridge.DIRECT_DELIVERY,
        )
    assert bridge.claim_desktop_messages() == []


@pytest.mark.parametrize("natural", [
    # 2026-07-29 对抗审查实证的误伤样本：转告恰恰是直投工具的本职，
    # 这些最平常的转告消息必须放行。单一"子串命中即拒"曾把它们全拦死。
    "文件我已经发过去了，记得查收",
    "你要的照片我刚发过去了，记得查收",
    "作业已经发到QQ群了，记得下载看一下",
    "刚才的会议纪要已推送到QQ，回家路上可以看",
    "今天当前心情不错，出去走走吧",
    "他问你要发什么消息给妈妈",
    "帮我看下 system prompt 那个 PR",
    "他说请直接输出 json 就行",
    "到点啦，你让我提醒你：检查报表是不是发过去了",
])
def test_natural_relay_messages_are_not_false_killed(tmp_path, natural: str) -> None:
    """自然转告文本只是"提到"了标记词（长句包含确认语、单个上下文碎片、
    非句首），不是泄漏——必须正常入队。"""
    bridge = _bridge(tmp_path)

    bridge.push_to_desktop(natural, source="qq")
    bridge.push_to_qq(natural, target_id="10001")

    assert [m["message"] for m in bridge.claim_desktop_messages()] == [natural]
    assert [m["message"] for m in bridge.claim_qq_messages()] == [natural]


async def test_push_tool_surfaces_rejection_reason_to_model(tmp_path) -> None:
    """桥拒收时，工具必须把拒因转告给模型，而不是笼统的"推送失败了"。

    2026-07-29 对抗审查：拒因被吞掉的话，模型只能盲重试同样的文本再次
    被拒——设计上"被拒后改写重试"的恢复路径就不存在了。
    """
    from white_salary.adapters.tools.builtin.chat import push_to_desktop

    _bridge(tmp_path)

    rejected = await push_to_desktop("发了，去QQ看看。")
    assert "被安全校验拒绝" in rejected
    assert "marker" in rejected  # 具体标记要在，模型才知道该避开什么
    assert rejected != "推送失败了"

    accepted = await push_to_desktop("文件我已经发过去了，记得查收")
    assert accepted == "已推送到桌面端"


# ---------------------------------------------------------------------------
# 回执等待：不把"已入队"当成"已送达"
# ---------------------------------------------------------------------------


async def test_wait_for_delivery_returns_after_consumer_ack(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    delivery_id = bridge.push_to_desktop("等待回执")

    async def consume() -> None:
        await asyncio.sleep(0.05)
        message = bridge.claim_desktop_messages()[0]
        bridge.ack_message(message, receipt={"accepted": True})

    task = asyncio.create_task(consume())
    record = await bridge.wait_for_delivery(
        delivery_id, timeout=5.0, poll_interval=0.01
    )
    await task

    assert record is not None
    assert record.state == DeliveryState.DELIVERED


async def test_wait_for_delivery_timeout_returns_pending_record(tmp_path) -> None:
    """没人消费时超时返回当前记录（仍是 PENDING），而不是 None——
    调用方要靠它区分"记录不存在"和"尚未确认"。"""
    bridge = _bridge(tmp_path)
    delivery_id = bridge.push_to_desktop("没人消费")

    record = await bridge.wait_for_delivery(
        delivery_id, timeout=0.05, poll_interval=0.01
    )

    assert record is not None
    assert record.state == DeliveryState.PENDING


async def test_wait_for_delivery_unknown_id_returns_none(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    assert await bridge.wait_for_delivery("no-such-id", timeout=0.05) is None


async def test_retryable_failure_is_not_reported_as_terminal(tmp_path) -> None:
    """可重试失败必须继续等，不能当场报"对方没收到"。

    否则上游会换一条路重发，而原投递退避后重试成功 → 用户收到两条。
    store.mark_delivery_failed 在未耗尽 max_attempts 时回 PENDING + 退避，
    这里断言 wait_for_delivery 看到的正是 PENDING 而不是 FAILED。
    """
    bridge = _bridge(tmp_path)
    delivery_id = bridge.push_to_desktop("会重试")
    message = bridge.claim_desktop_messages()[0]
    bridge.retry_message(message, "desktop offline for a moment")

    record = await bridge.wait_for_delivery(
        delivery_id, timeout=0.05, poll_interval=0.01
    )

    assert record is not None
    assert record.state == DeliveryState.PENDING


async def test_wait_for_delivery_sees_permanent_rejection(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    delivery_id = bridge.push_to_desktop("会被拒")
    message = bridge.claim_desktop_messages()[0]
    bridge.reject_message(message, "direct delivery refused by consumer")

    record = await bridge.wait_for_delivery(
        delivery_id, timeout=5.0, poll_interval=0.01
    )

    assert record is not None
    assert record.state == DeliveryState.FAILED
    assert "refused" in record.last_error


async def test_wait_for_delivery_sees_unknown_outcome(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    delivery_id = bridge.push_to_qq("结果不明", target_id="10001")
    message = bridge.claim_qq_messages()[0]
    bridge.mark_message_unknown(message, "send attempt timed out")

    record = await bridge.wait_for_delivery(
        delivery_id, timeout=5.0, poll_interval=0.01
    )

    assert record is not None
    assert record.state == DeliveryState.UNKNOWN
