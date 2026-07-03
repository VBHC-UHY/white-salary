"""
websocket_handler 批3改造（真流式语音体验）的单元测试。

覆盖（2026-07-02 审计修复批3新增）：
  - _drain_sentences 增量切句：多句/半句残留/无句尾/空白句剔除
  - _resolve_owner_name 三级回退：conf.yaml qq.owner_name → 画像 user_name → None
    （路径可注入，tmp_path 构造假配置与假画像）
  - _handle_chat_message 真流式集成（fake websocket/agent/tts）：
      * reply_start 帧最先下发，source 按 user/auto 正确标记
      * 句子在 LLM 流结束前就增量下发（真流式，非收完再切）
      * 120秒超时路径：残留 buffer 只追加一次（修"超时内容播两遍"）
      * 用户取消路径：异步生成器被 aclose() 关闭、不发 done
      * TTS worker：sentence_audio 按句 index 下发，done 帧最后到达
      * 情绪标签：逐句提取，emotion 帧下发且正文不带标签
"""

import asyncio
import base64
import json
from pathlib import Path
from typing import Optional

import pytest

from white_salary.core.interfaces.types import AudioData
from white_salary.infrastructure.server import websocket_handler as wsh
from white_salary.infrastructure.server.websocket_handler import (
    CancellationToken,
    _drain_sentences,
    _handle_chat_message,
    _resolve_owner_name,
)


# ---------------------------------------------------------------------------
# _drain_sentences：增量切句纯函数
# ---------------------------------------------------------------------------

def test_drain_multiple_sentences_with_residue():
    """多个完整句子 + 半句残留：句子全部切出，残留原样返回。"""
    sentences, rest = _drain_sentences("你好。今天天气不错！明天呢？还没说完")
    assert sentences == ["你好。", "今天天气不错！", "明天呢？"]
    assert rest == "还没说完"


def test_drain_no_terminator_keeps_buffer():
    """没有任何句尾标点：不切句，整段留在残留里。"""
    sentences, rest = _drain_sentences("这是一段还没说完的半句")
    assert sentences == []
    assert rest == "这是一段还没说完的半句"


def test_drain_empty_buffer():
    """空串输入：无句子、残留为空。"""
    assert _drain_sentences("") == ([], "")


def test_drain_newline_as_terminator():
    """换行符也算句尾（沿用 _SENTENCE_END 现有规则）。"""
    sentences, rest = _drain_sentences("第一行\n第二行")
    assert sentences == ["第一行"]
    assert rest == "第二行"


def test_drain_whitespace_only_segment_dropped():
    """句尾后只剩空白的片段应被剔除，不产生空句子。"""
    sentences, rest = _drain_sentences("你好。 \n")
    assert sentences == ["你好。"]
    assert rest == ""


def test_drain_incremental_across_chunks():
    """模拟 chunk 累积场景：分两次调用，第二次才凑齐句子。"""
    sentences, rest = _drain_sentences("今天")
    assert sentences == [] and rest == "今天"
    sentences, rest = _drain_sentences(rest + "很开心！后面")
    assert sentences == ["今天很开心！"]
    assert rest == "后面"


# ---------------------------------------------------------------------------
# _resolve_owner_name：称呼三级回退
# ---------------------------------------------------------------------------

def _write_conf(tmp_path: Path, content: str) -> Path:
    """写一份临时 conf.yaml，返回路径。"""
    conf = tmp_path / "conf.yaml"
    conf.write_text(content, encoding="utf-8")
    return conf


def _write_profile(tmp_path: Path, owner_id: str, payload: dict) -> Path:
    """写一份临时用户画像 json，返回画像目录。"""
    profiles = tmp_path / "profiles"
    profiles.mkdir(exist_ok=True)
    (profiles / f"{owner_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return profiles


def test_owner_name_from_conf_first(tmp_path: Path):
    """第一级：conf.yaml 配了 qq.owner_name 时最优先（画像里的名字被忽略）。"""
    conf = _write_conf(tmp_path, "qq:\n  owner_name: 老板\n  family_qq:\n    - 10086\n")
    profiles = _write_profile(tmp_path, "10086", {"user_name": "小白"})
    assert _resolve_owner_name("10086", conf_path=conf, profiles_dir=profiles) == "老板"


def test_owner_name_falls_back_to_profile(tmp_path: Path):
    """第二级：conf 没配 owner_name，用画像 user_name（实测值"小白"场景）。"""
    conf = _write_conf(tmp_path, "qq:\n  family_qq:\n    - 1234567890\n")
    profiles = _write_profile(tmp_path, "1234567890", {"user_name": "小白"})
    assert _resolve_owner_name("1234567890", conf_path=conf, profiles_dir=profiles) == "小白"


def test_owner_name_none_when_nothing_configured(tmp_path: Path):
    """第三级：conf 无 owner_name 且画像不存在 → None（让模型自然称呼）。"""
    conf = _write_conf(tmp_path, "qq:\n  family_qq: []\n")
    profiles = tmp_path / "profiles_empty"
    profiles.mkdir()
    assert _resolve_owner_name("10086", conf_path=conf, profiles_dir=profiles) is None


def test_owner_name_profile_without_user_name(tmp_path: Path):
    """画像文件存在但没有 user_name 字段 → None。"""
    conf = _write_conf(tmp_path, "server:\n  port: 12400\n")
    profiles = _write_profile(tmp_path, "10086", {"interests": ["测试"]})
    assert _resolve_owner_name("10086", conf_path=conf, profiles_dir=profiles) is None


def test_owner_name_broken_inputs_return_none(tmp_path: Path):
    """conf YAML 损坏 + 画像 JSON 损坏：不抛异常，返回 None。"""
    conf = _write_conf(tmp_path, "qq: [unclosed\n  owner_name: {{bad\n")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "10086.json").write_text("{not-json", encoding="utf-8")
    assert _resolve_owner_name("10086", conf_path=conf, profiles_dir=profiles) is None


def test_owner_name_blank_values_skipped(tmp_path: Path):
    """owner_name / user_name 为空白串时视为未配置。"""
    conf = _write_conf(tmp_path, 'qq:\n  owner_name: "  "\n')
    profiles = _write_profile(tmp_path, "10086", {"user_name": ""})
    assert _resolve_owner_name("10086", conf_path=conf, profiles_dir=profiles) is None


# ---------------------------------------------------------------------------
# _handle_chat_message 流式集成测试用的假对象
# ---------------------------------------------------------------------------

class FakeWebSocket:
    """收集所有下发帧（解析回 dict）的假 WebSocket。"""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))

    def types(self) -> list[str]:
        """按顺序返回所有帧的 type。"""
        return [f.get("type", "") for f in self.frames]

    def of_type(self, frame_type: str) -> list[dict]:
        """返回指定 type 的全部帧。"""
        return [f for f in self.frames if f.get("type") == frame_type]


class FakeAgent:
    """
    假 ChatAgent：chat_stream_with_tools 逐个吐出预设 chunk。

    - closed:      生成器 finally 是否执行（aclose/终结的证据）
    - hang_at_end: 吐完 chunk 后挂起（模拟 LLM 卡死，供超时测试）
    - on_yield:    每次 yield 前回调 (index)，供测试注入取消等副作用
    """

    def __init__(self, chunks: list[str], hang_at_end: bool = False, on_yield=None) -> None:
        self.chunks = chunks
        self.hang_at_end = hang_at_end
        self.on_yield = on_yield
        self.closed = False

    async def chat_stream_with_tools(
        self,
        user_input: str,
        user_name: Optional[str] = None,
        user_id: str = "desktop",
        **kwargs,
    ):
        self.seen_user_name = user_name
        self.seen_user_id = user_id
        try:
            for i, chunk in enumerate(self.chunks):
                if self.on_yield is not None:
                    self.on_yield(i)
                yield chunk
            if self.hang_at_end:
                await asyncio.sleep(999)  # 模拟 LLM 卡死，等 wait_for 超时
        finally:
            self.closed = True


class FakeTTS:
    """假 TTS：记录合成过的文本，返回固定 3 字节音频。"""

    def __init__(self) -> None:
        self.synthesized: list[str] = []

    async def synthesize(self, text: str) -> AudioData:
        self.synthesized.append(text)
        return AudioData(samples=b"\x01\x02\x03", sample_rate=16000, dtype="wav")


class _StubConvLog:
    """假对话日志：吞掉 record 调用，避免测试写真实 sqlite。"""

    def record(self, **kwargs) -> None:
        pass


class _IdentityHumanFilter:
    """假机器话过滤器：原样返回，避免真实过滤器干扰断言文本。"""

    def filter_response(self, text: str) -> str:
        return text


@pytest.fixture()
def quiet_side_effects(monkeypatch):
    """屏蔽 _handle_chat_message 收尾的真实副作用（sqlite 日志/机器话过滤）。"""
    from white_salary.core.memory.conversation_log import ConversationLog
    monkeypatch.setattr(ConversationLog, "get_instance", staticmethod(lambda: _StubConvLog()))
    monkeypatch.setattr(_handle_chat_message, "_human_filter", _IdentityHumanFilter(), raising=False)


def _run(coro):
    """在新事件循环里跑一个协程（不依赖 pytest-asyncio）。"""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 真流式行为
# ---------------------------------------------------------------------------

def test_reply_start_first_and_source_user(quiet_side_effects):
    """reply_start 必须是本轮第一帧，用户消息 source=user。"""
    ws = FakeWebSocket()
    agent = FakeAgent(["你好。"])
    _run(_handle_chat_message(ws, agent, None, "在吗", CancellationToken(), is_user_message=True))
    assert ws.frames[0] == {"type": "reply_start", "source": "user"}
    assert ws.types()[-1] == "done"


def test_reply_start_source_auto_for_system_input(quiet_side_effects):
    """auto_chat/桥/图片等系统构造输入（is_user_message=False）source=auto。"""
    ws = FakeWebSocket()
    agent = FakeAgent(["主动打个招呼。"])
    _run(_handle_chat_message(ws, agent, None, "[主动对话触发] 早安", CancellationToken(), is_user_message=False))
    assert ws.frames[0] == {"type": "reply_start", "source": "auto"}


def test_sentences_streamed_before_llm_finishes(quiet_side_effects):
    """真流式：第一句应在 LLM 流吐完之前就已下发（不再收完全文才切句）。"""
    ws = FakeWebSocket()
    sentences_seen_mid_stream: list[int] = []

    def _on_yield(i: int) -> None:
        # 吐最后一个 chunk 之前，统计前端已收到的 sentence 帧数
        if i == 2:
            sentences_seen_mid_stream.append(len(ws.of_type("sentence")))

    agent = FakeAgent(["你好", "。今天天气", "不错！结尾半句"], on_yield=_on_yield)
    _run(_handle_chat_message(ws, agent, None, "聊聊", CancellationToken()))

    # 吐第3个chunk前，"你好。"已经作为 sentence 下发（真流式的关键断言）
    assert sentences_seen_mid_stream == [1]
    contents = [f["content"] for f in ws.of_type("sentence")]
    assert contents == ["你好。", "今天天气不错！", "结尾半句"]
    indexes = [f["index"] for f in ws.of_type("sentence")]
    assert indexes == [0, 1, 2]
    # done 正文 = 全部句子拼接
    assert ws.of_type("done")[0]["content"] == "你好。今天天气不错！结尾半句"


def test_timeout_tail_not_duplicated(quiet_side_effects, monkeypatch):
    """120秒超时路径：残留 buffer 只追加/下发一次（修"超时内容播两遍"）。"""
    monkeypatch.setattr(wsh, "_LLM_STREAM_TIMEOUT", 0.2)
    ws = FakeWebSocket()
    agent = FakeAgent(["超时前的半句", "还在憋"], hang_at_end=True)
    _run(_handle_chat_message(ws, agent, None, "说个长的", CancellationToken()))

    sentence_frames = ws.of_type("sentence")
    assert len(sentence_frames) == 1
    assert sentence_frames[0]["content"] == "超时前的半句还在憋"
    done = ws.of_type("done")
    assert len(done) == 1
    # done 正文里该内容只出现一次（不重复播两遍）
    assert done[0]["content"].count("超时前的半句还在憋") == 1
    # 超时后生成器被终结/关闭
    assert agent.closed is True


def test_timeout_with_no_content_sends_fallback(quiet_side_effects, monkeypatch):
    """超时且一个字都没生成：下发兜底句，不静默。"""
    monkeypatch.setattr(wsh, "_LLM_STREAM_TIMEOUT", 0.2)
    ws = FakeWebSocket()
    agent = FakeAgent([], hang_at_end=True)
    _run(_handle_chat_message(ws, agent, None, "在吗", CancellationToken()))
    sentence_frames = ws.of_type("sentence")
    assert len(sentence_frames) == 1
    assert "走神" in sentence_frames[0]["content"]


def test_empty_finished_stream_sends_fallback_and_done(quiet_side_effects):
    """LLM正常结束但空回复：下发兜底句和done，避免前端一直等。"""
    ws = FakeWebSocket()
    agent = FakeAgent([])
    _run(_handle_chat_message(ws, agent, None, "在吗", CancellationToken()))

    sentence_frames = ws.of_type("sentence")
    assert len(sentence_frames) == 1
    assert "走神" in sentence_frames[0]["content"]
    assert len(ws.of_type("done")) == 1


def test_cancel_closes_generator_and_skips_done(quiet_side_effects):
    """用户取消路径：生成器被 aclose()，不再发后续句子和 done 帧。"""
    ws = FakeWebSocket()
    token = CancellationToken()

    def _on_yield(i: int) -> None:
        if i == 1:  # 第一句已下发后，用户取消
            token.cancel()

    agent = FakeAgent(["第一句。", "第二句。", "第三句。"], on_yield=_on_yield)
    _run(_handle_chat_message(ws, agent, None, "说三句", token))

    assert agent.closed is True          # 退出路径调用了 aclose()
    assert len(ws.of_type("sentence")) == 1
    assert ws.of_type("done") == []      # 取消后不发 done


def test_tts_audio_per_sentence_and_done_last(quiet_side_effects):
    """TTS worker：每句一条 sentence_audio（index 对应），done 帧最后到达。"""
    ws = FakeWebSocket()
    tts = FakeTTS()
    agent = FakeAgent(["第一句完整。", "第二句也完整！"])
    _run(_handle_chat_message(ws, agent, tts, "说两句", CancellationToken()))

    audio_frames = ws.of_type("sentence_audio")
    assert sorted(f["index"] for f in audio_frames) == [0, 1]
    expected_b64 = base64.b64encode(b"\x01\x02\x03").decode("ascii")
    assert all(f["content"] == expected_b64 and f["format"] == "wav" for f in audio_frames)
    # done 必须是最后一帧（TTS 队列清空后才收尾）
    assert ws.types()[-1] == "done"
    assert tts.synthesized == ["第一句完整。", "第二句也完整！"]


def test_emotion_extracted_per_sentence(quiet_side_effects):
    """情绪标签逐句提取：emotion 帧下发一次，正文不带 [tag]。"""
    ws = FakeWebSocket()
    agent = FakeAgent(["[happy]你好呀。", "[excited]今天真开心！"])
    _run(_handle_chat_message(ws, agent, None, "打招呼", CancellationToken()))

    emotion_frames = ws.of_type("emotion")
    assert len(emotion_frames) == 1          # 每轮只发第一次
    assert emotion_frames[0]["content"] == "happy"
    contents = [f["content"] for f in ws.of_type("sentence")]
    assert contents == ["你好呀。", "今天真开心！"]
    assert "[happy]" not in ws.of_type("done")[0]["content"]


def test_owner_name_none_passed_through(quiet_side_effects):
    """称呼修复：owner_name=None 时原样传给 agent（不再兜底成"主人"）。"""
    ws = FakeWebSocket()
    agent = FakeAgent(["好的。"])
    _run(_handle_chat_message(
        ws, agent, None, "在吗", CancellationToken(),
        owner_id="10086", owner_name=None,
    ))
    assert agent.seen_user_name is None
    assert agent.seen_user_id == "10086"
