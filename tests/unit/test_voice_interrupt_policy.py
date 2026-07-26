"""黄金测试 — 锁住语音打断策略：噪声不得吞掉回复，真人说话仍必须能打断。

历史缺陷：voice 帧一到就无条件 `_cancel_current_reply()`。持续监听的分段是
按音量/时长阈值自动切出来的，一声咳嗽、关门、电视声都会送上一帧，于是环境里
一点动静就把白正在流式输出的整段话彻底取消，而且被取消的那轮**不会重投递**，
那段话永久丢失。

修复后的策略（两个方向都要守住，缺一个都是回归）：
  - push_to_talk：用户手动按下，意图明确 → 立即打断；
  - continuous：推迟到 ASR 确认有语音内容之后再打断。识别出文字后前端作为
    chat 消息发回，_launch_reply 的第一步就是 _cancel_current_reply()，
    因此打断照样发生；噪声帧识别为空，白把话说完。
"""

import inspect
import re

import pytest

from white_salary.infrastructure.server import websocket_handler


def _voice_branch_source() -> str:
    """取出 websocket 主循环里 voice 分支的源码片段。"""
    source = inspect.getsource(websocket_handler)
    start = source.index('elif msg_type == "voice":')
    # 截到下一个同级分支为止
    rest = source[start + 10 :]
    match = re.search(r"\n            elif msg_type ==", rest)
    end = start + 10 + (match.start() if match else len(rest))
    return source[start:end]


def test_push_to_talk_still_interrupts_immediately() -> None:
    """长按说话必须保留"立刻打断"——否则用户按下按钮白还在自说自话。"""
    branch = _voice_branch_source()

    assert 'if mode == "push_to_talk":' in branch, (
        "长按说话的立即打断分支不见了"
    )
    ptt_index = branch.index('if mode == "push_to_talk":')
    after_ptt = branch[ptt_index : ptt_index + 200]
    assert "_cancel_current_reply()" in after_ptt, (
        "长按说话不再立即取消进行中的回复，真实打断会失灵"
    )


def test_voice_frames_are_not_cancelled_unconditionally() -> None:
    """continuous 帧不得再无条件取消回复。

    检查方式：voice 分支里对 _cancel_current_reply 的调用必须处在
    push_to_talk 条件之下，不能有裸调用。
    """
    branch = _voice_branch_source()

    for line in branch.splitlines():
        stripped = line.strip()
        if stripped.startswith("await _cancel_current_reply()"):
            # 允许存在，但必须缩进在 push_to_talk 判断内部（缩进更深）
            indent = len(line) - len(line.lstrip())
            assert indent >= 20, (
                "voice 分支里出现了无条件的 _cancel_current_reply()："
                "持续监听的噪声帧会重新开始吞掉白正在说的话"
            )


def test_continuous_mode_relies_on_launch_reply_to_interrupt() -> None:
    """确认打断链路仍然成立：_launch_reply 必须以取消上一轮开头。

    continuous 的打断依赖这条路径（识别出文字 → 前端发 chat → _launch_reply）。
    如果哪天 _launch_reply 不再取消上一轮，持续监听就彻底打不断了，
    这条断言会先炸。
    """
    source = inspect.getsource(websocket_handler)
    start = source.index("async def _launch_reply(")
    body = source[start : start + 1200]

    assert "await _cancel_current_reply()" in body, (
        "_launch_reply 不再取消上一轮回复，continuous 模式将无法打断"
    )


@pytest.mark.parametrize("mode_value", ["push_to_talk", "continuous"])
def test_mode_normalization_is_explicit(mode_value: str) -> None:
    """mode 只有这两种取值，任何第三种都归一为 continuous（更保守的一侧）。"""
    branch = _voice_branch_source()

    assert '"push_to_talk"' in branch
    assert '"continuous"' in branch
    # 归一逻辑：data.get("mode") == "push_to_talk" 才是长按，其余一律 continuous
    assert 'data.get("mode") == "push_to_talk"' in branch, (
        "mode 归一逻辑变了，需重新确认未知取值是否仍落到保守的 continuous 一侧"
    )
