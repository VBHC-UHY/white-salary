"""端到端测试用的假上游服务：OpenAI 兼容 LLM + ASR + TTS。

为什么需要它：项目此前没有任何真实协议级测试——`test_ws_streaming.py` 的用例
都是直接调函数，从没有真的连过 WebSocket。于是"流式回复能不能被打断"、
"噪声帧会不会吞掉回复"这类**只在真实时序下才暴露**的问题，单测一律测不出来。

这个假上游让整条链路可以真跑起来而不依赖云端 API：
  - `POST /v1/chat/completions`：OpenAI 兼容。stream=true 时按 SSE 逐块吐字，
    **每块之间有可控延迟**，这样测试才有机会在回复进行中插入语音帧。
  - `POST /v1/audio/transcriptions`：返回可预设的中文识别结果。
  - `POST /v1/audio/speech`：返回一小段合法 WAV，避免 TTS 报错干扰判断。

同时提供 `/__control__` 端点，测试可以在运行中调整行为（每块延迟、回复内容、
下一次是否失败），不必为每个场景重启服务。
"""

from __future__ import annotations

import asyncio
import json
import struct
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse


class UpstreamState:
    """假上游的可变行为开关（测试通过 /__control__ 调整）。"""

    def __init__(self) -> None:
        # 每个 SSE 块之间的延迟。默认给足，便于在回复中途插入语音帧。
        self.chunk_delay: float = 0.25
        # 流式回复要吐的分块内容
        self.reply_chunks: list[str] = [
            "我在的，", "刚才在想", "一件事。", "你先说吧。",
        ]
        # ASR 固定返回的识别结果
        self.transcription: str = "你在干什么呢"
        # 下一次 chat_completions 是否直接返回 500
        self.fail_next_chat: bool = False
        # 记录收到的请求，便于断言"到底调了几次上游"
        self.chat_calls: list[dict[str, Any]] = []
        self.asr_calls: int = 0
        self.tts_calls: int = 0


state = UpstreamState()
app = FastAPI(title="White Salary fake upstream")


@app.post("/__control__")
async def control(request: Request) -> JSONResponse:
    """运行中调整假上游行为。"""
    payload = await request.json()
    for key, value in payload.items():
        if hasattr(state, key):
            setattr(state, key, value)
    return JSONResponse({"ok": True})


@app.get("/__stats__")
async def stats() -> JSONResponse:
    return JSONResponse(
        {
            "chat_calls": len(state.chat_calls),
            "asr_calls": state.asr_calls,
            "tts_calls": state.tts_calls,
            "last_chat": state.chat_calls[-1] if state.chat_calls else None,
        }
    )


@app.post("/__reset__")
async def reset() -> JSONResponse:
    state.chat_calls.clear()
    state.asr_calls = 0
    state.tts_calls = 0
    state.fail_next_chat = False
    return JSONResponse({"ok": True})


def _sse_chunk(model: str, content: str, *, finish: bool = False) -> bytes:
    payload = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {} if finish else {"content": content},
                "finish_reason": "stop" if finish else None,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    state.chat_calls.append(
        {
            "model": body.get("model"),
            "stream": bool(body.get("stream")),
            "messages": body.get("messages", []),
        }
    )

    if state.fail_next_chat:
        state.fail_next_chat = False
        return JSONResponse({"error": {"message": "fake upstream forced failure"}}, status_code=500)

    model = str(body.get("model") or "fake-model")

    if not body.get("stream"):
        text = "".join(state.reply_chunks)
        return JSONResponse(
            {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )

    async def _stream():
        for piece in state.reply_chunks:
            yield _sse_chunk(model, piece)
            await asyncio.sleep(state.chunk_delay)
        yield _sse_chunk(model, "", finish=True)
        yield b"data: [DONE]\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/audio/transcriptions")
async def transcriptions(request: Request) -> JSONResponse:
    # 消费掉 multipart 主体，模拟真实读取
    await request.form()
    state.asr_calls += 1
    return JSONResponse({"text": state.transcription})


def _tiny_wav(duration_seconds: float = 0.2, sample_rate: int = 16000) -> bytes:
    """生成一段静音 WAV，够让 TTS 链路走通而不必真合成。"""
    frames = int(duration_seconds * sample_rate)
    data = b"\x00\x00" * frames
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


@app.post("/v1/audio/speech")
async def speech(request: Request) -> Response:
    await request.json()
    state.tts_calls += 1
    return Response(content=_tiny_wav(), media_type="audio/wav")


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})
