"""端到端验证语音打断策略——真后端、真 WebSocket、真时序。

为什么必须端到端测：这条缺陷的本质是**时序**问题（回复流到一半时插入一个语音帧
会发生什么），而单测都是直接调函数，永远构造不出"回复正在流"这个状态。
项目此前没有任何真实 WebSocket 测试，所以这类问题只能靠用户在真机上体感发现。

守住的两个方向（缺任何一个都是回归）：
  - 持续监听的噪声帧【不得】取消进行中的回复；
  - 长按说话【仍必须】立刻打断。
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import tempfile
from pathlib import Path

import pytest

# 整个文件都是 e2e：默认套件不跑，需 -m e2e 显式开启
pytestmark = pytest.mark.e2e

pytest.importorskip("websockets", reason="端到端测试需要 websockets 客户端")
import websockets  # noqa: E402

from .harness import E2EStack  # noqa: E402


def _wav_bytes(duration_seconds: float = 0.6, sample_rate: int = 16000) -> bytes:
    """一段合法的静音 WAV，用来当作"用户说了一句话"的载荷。"""
    frames = int(duration_seconds * sample_rate)
    data = b"\x00\x00" * frames
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


VOICE_PAYLOAD = base64.b64encode(_wav_bytes()).decode("ascii")


@pytest.fixture(scope="module")
def stack():
    tmp = Path(tempfile.mkdtemp(prefix="ws-e2e-voice-"))
    try:
        with E2EStack(tmp) as running:
            yield running
    except RuntimeError as exc:  # 端口被占 / 环境不具备时明确跳过而不是伪装失败
        pytest.skip(f"端到端环境无法就绪：{exc}")


async def _drain_opening_frames(ws, timeout: float = 1.5) -> None:
    """吃掉连接建立后的开场帧（历史消息等），让后续断言只面对本轮回复。"""
    try:
        while True:
            await asyncio.wait_for(ws.recv(), timeout=timeout)
    except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
        return


async def _collect_until_done(ws, *, timeout: float, inject=None):
    """收帧直到 done / 超时。inject 是在收到 reply_start 后执行的协程。"""
    frames: list[dict] = []
    injected = False
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - loop.time()))
        except asyncio.TimeoutError:
            break
        frame = json.loads(raw)
        frames.append(frame)
        if frame.get("type") == "reply_start" and inject is not None and not injected:
            injected = True
            await inject()
        if frame.get("type") == "done":
            break
    return frames


def _types(frames: list[dict]) -> list[str]:
    return [f.get("type", "") for f in frames]


@pytest.mark.asyncio
async def test_continuous_voice_frame_does_not_kill_the_reply(stack: E2EStack) -> None:
    """持续监听的分段（可能只是咳嗽/关门声）不得吞掉白正在说的整段话。

    修复前：任何 voice 帧都无条件 _cancel_current_reply()，且被取消的那轮
    不会重投递 —— 环境里一点动静就让整段回复永久消失。
    """
    stack.reset_upstream()
    # 分块之间留足时间，确保语音帧一定落在"回复正在流"的窗口里
    stack.configure_upstream(chunk_delay=1.0)

    async with websockets.connect(stack.ws_url, max_size=None) as ws:
        await _drain_opening_frames(ws)
        await ws.send(json.dumps({"type": "chat", "content": "跟我说点什么吧"}))

        async def inject_noise():
            await asyncio.sleep(0.3)  # 让回复真的开始流
            await ws.send(json.dumps({
                "type": "voice",
                "content": VOICE_PAYLOAD,
                "mode": "continuous",
                "request_id": "noise-1",
            }))

        frames = await _collect_until_done(ws, timeout=45, inject=inject_noise)

    kinds = _types(frames)
    assert "reply_start" in kinds, f"回复没有开始，帧序列：{kinds}"
    assert "done" in kinds, (
        "持续监听的语音帧把整段回复取消了——这正是本次修复的缺陷。"
        f"帧序列：{kinds}"
    )
    sentences = [f for f in frames if f.get("type") == "sentence"]
    assert len(sentences) >= 2, (
        f"回复被截断，只收到 {len(sentences)} 句；帧序列：{kinds}"
    )
    # 语音帧本身仍应被正常受理（排队 → 识别），而不是被丢弃
    assert any(f.get("type") == "voice_status" for f in frames), (
        f"语音帧没有进入识别流程，帧序列：{kinds}"
    )


@pytest.mark.asyncio
async def test_push_to_talk_still_interrupts_immediately(stack: E2EStack) -> None:
    """长按说话必须仍能立刻打断——修噪声问题不能把真实打断一起修没了。"""
    stack.reset_upstream()
    stack.configure_upstream(chunk_delay=1.0)

    async with websockets.connect(stack.ws_url, max_size=None) as ws:
        await _drain_opening_frames(ws)
        await ws.send(json.dumps({"type": "chat", "content": "讲个长一点的事情"}))

        async def press_to_talk():
            await asyncio.sleep(0.3)
            await ws.send(json.dumps({
                "type": "voice",
                "content": VOICE_PAYLOAD,
                "mode": "push_to_talk",
                "request_id": "ptt-1",
            }))

        frames = await _collect_until_done(ws, timeout=30, inject=press_to_talk)

    kinds = _types(frames)
    assert "reply_start" in kinds, f"回复没有开始，帧序列：{kinds}"
    sentences = [f for f in frames if f.get("type") == "sentence"]
    # 打断的判据：这一轮没有正常走到 done，或者句子数明显少于完整回复（4 块 → 2 句）
    assert "done" not in kinds or len(sentences) < 2, (
        "长按说话没有打断进行中的回复，真实打断能力失灵了。"
        f"帧序列：{kinds}"
    )


@pytest.mark.asyncio
async def test_transcribed_speech_still_interrupts_via_chat_path(stack: E2EStack) -> None:
    """continuous 的打断依赖"识别出文字→作为 chat 发回→_launch_reply 取消上一轮"。

    这条把修复的另一半论断也钉死：推迟打断不等于不能打断。
    """
    stack.reset_upstream()
    # 注意：这里的后续文本必须是**中性提问**。
    # 若用"那你先别说了"这类语句，ConflictDetector 会正确判为 INTERRUPT，
    # 后端取消回复并只回一条 info（"好，我先停下，你说。"）而不开启新一轮——
    # 那是另一条正确行为（见下一个用例），会让本用例的判据失效。
    follow_up = "你觉得猫好还是狗好"
    stack.configure_upstream(chunk_delay=1.0, transcription=follow_up)

    async with websockets.connect(stack.ws_url, max_size=None) as ws:
        await _drain_opening_frames(ws)
        await ws.send(json.dumps({"type": "chat", "content": "说点什么"}))

        async def speak_then_send_text():
            await asyncio.sleep(0.3)
            await ws.send(json.dumps({
                "type": "voice",
                "content": VOICE_PAYLOAD,
                "mode": "continuous",
                "request_id": "speech-1",
            }))
            # 模拟前端行为：拿到 transcription 后作为 chat 消息发回
            await asyncio.sleep(1.2)
            await ws.send(json.dumps({"type": "chat", "content": follow_up}))

        frames = await _collect_until_done(ws, timeout=45, inject=speak_then_send_text)

    kinds = _types(frames)
    # 第二次 chat 会开启新一轮：应当能看到两次 reply_start
    assert kinds.count("reply_start") >= 2, (
        "真实语音（识别出文字后走 chat 路径）没有触发新一轮回复，"
        f"打断链路断了。帧序列：{kinds}"
    )


@pytest.mark.asyncio
async def test_spoken_stop_phrase_cancels_and_acknowledges(stack: E2EStack) -> None:
    """说"别说了"这类打断语时，应当停下并回一条确认，而不是静默或继续说。

    这条行为是写本文件时实测发现并顺手锁住的：ConflictDetector 判为 INTERRUPT
    后走的是"取消当前 + 回一条 info"，批3 特意加的这条 info 就是为了避免用户
    以为消息被吞。它与上一个用例互补——上一个证明普通后续语句会开启新一轮，
    这一个证明打断语句只停不续。
    """
    stack.reset_upstream()
    stack.configure_upstream(chunk_delay=1.0)

    async with websockets.connect(stack.ws_url, max_size=None) as ws:
        await _drain_opening_frames(ws)
        await ws.send(json.dumps({"type": "chat", "content": "讲个长故事"}))

        async def say_stop():
            await asyncio.sleep(0.4)
            await ws.send(json.dumps({"type": "chat", "content": "那你先别说了"}))

        frames = await _collect_until_done(ws, timeout=35, inject=say_stop)

    kinds = _types(frames)
    assert "info" in kinds, (
        f"打断后没有回确认，用户会以为消息被吞。帧序列：{kinds}"
    )
    assert kinds.count("reply_start") == 1, (
        f"打断语句不应开启新一轮回复。帧序列：{kinds}"
    )


@pytest.mark.asyncio
async def test_asr_result_is_delivered_to_client(stack: E2EStack) -> None:
    """顺带验证 ASR 链路真的通（含本次新增的 asr.base_url 配置生效）。"""
    stack.reset_upstream()
    stack.configure_upstream(transcription="今天天气不错")

    async with websockets.connect(stack.ws_url, max_size=None) as ws:
        await _drain_opening_frames(ws)
        await ws.send(json.dumps({
            "type": "voice",
            "content": VOICE_PAYLOAD,
            "mode": "push_to_talk",
            "request_id": "asr-1",
        }))

        transcription = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 25
        while loop.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - loop.time()))
            except asyncio.TimeoutError:
                break
            frame = json.loads(raw)
            if frame.get("type") == "transcription":
                transcription = frame.get("content")
                break

    assert transcription == "今天天气不错", (
        f"ASR 链路没把识别结果送回客户端（收到 {transcription!r}）。"
        "若为 None，说明 asr.base_url 没生效或 ASR 适配器未启用。"
    )
    stats = stack.upstream_stats()
    assert stats["asr_calls"] >= 1, "假上游没收到 ASR 请求，说明请求没打到配置的地址"
