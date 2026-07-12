"""
2026-07-03 工具实现（批9）的单元测试 — 媒体与QQ工具真实现。

覆盖：
  - describe_image：运行实例注册表命中 / conf.yaml 现场构造 / 失败中文指引 三条路径
  - qq_send_file：文件不存在 / QQ未连接 / 成功（只有确认才报成功）/ 未确认如实说
  - download_video：时长与大小超限拒绝 / 成功返回路径与标题 / 报错转译中文
  - qq_inbox：「可能没回的人」推断逻辑 / unread动作 / recent兜底
  - view_learned_style：模块禁用提示 / 数据读取 / 配置已恢复学习
  - deep_think：mock辅助LLM / 通道选择 / 未配置指引 / 出错兜底
  - registry：新工具已注册、旧空壳名保持移除、超时表新条目
"""

import json
import sys
import types
from pathlib import Path

import pytest

from white_salary.adapters.tools.builtin import chat as chat_mod
from white_salary.adapters.tools.builtin import download as download_mod
from white_salary.adapters.tools.builtin import media as media_mod
from white_salary.adapters.tools.builtin import qq_api as qq_api_mod
from white_salary.adapters.tools.builtin import reasoning as reasoning_mod
from white_salary.adapters.tools.registry import (
    TOOL_TIMEOUTS,
    ToolRegistry,
    get_tool_timeout,
)
from white_salary.adapters.tools.errors import ToolOutcomeUnknown
from white_salary.core.interfaces.types import AudioData


PROJECT_ROOT = Path(__file__).parent.parent.parent


# ================================================================
# 1. describe_image 三条路径
# ================================================================

class FakeVisionAdapter:
    """视觉适配器桩：记录收到的base64，返回固定描述。"""

    def __init__(self, reply: str = "一只橘猫趴在键盘上") -> None:
        self.reply = reply
        self.seen_base64: list[str] = []

    async def describe_image(self, image_base64: str, prompt: str = "描述这张图片的内容",
                             max_tokens: int = 500) -> str:
        self.seen_base64.append(image_base64)
        return self.reply


class TestDescribeImage:
    """describe_image 接真视觉链路。"""

    def _make_image(self, tmp_path: Path) -> Path:
        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fakebody" * 20)
        return img

    async def test_registry_hit_path(self, tmp_path: Path, monkeypatch) -> None:
        """路径1：运行实例注册表里有 'vision' 时直接复用。"""
        from white_salary.infrastructure.server import settings_api
        fake = FakeVisionAdapter()
        monkeypatch.setitem(settings_api._runtime_registry, "vision", fake)

        img = self._make_image(tmp_path)
        result = await media_mod.describe_image(image_path=str(img))

        assert result == "一只橘猫趴在键盘上"
        assert len(fake.seen_base64) == 1 and fake.seen_base64[0]

    async def test_onthefly_construction_path(self, tmp_path: Path, monkeypatch) -> None:
        """路径2：注册表没有时按 conf.yaml llm_vision 现场构造适配器。"""
        from white_salary.infrastructure.server import settings_api
        monkeypatch.delitem(settings_api._runtime_registry, "vision", raising=False)

        # 假项目根：conf.yaml 带完整 llm_vision 节
        fake_root = tmp_path / "root"
        fake_root.mkdir()
        (fake_root / "conf.yaml").write_text(
            "llm_vision:\n  api_key: sk-test\n  base_url: https://api.test/v1\n"
            "  model: test-vl\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(media_mod, "_project_root_path", lambda: fake_root)

        constructed: list[dict] = []

        class FakeMultimodal(FakeVisionAdapter):
            def __init__(self, api_key: str, base_url: str, model: str) -> None:
                super().__init__(reply="现场构造的描述")
                constructed.append(
                    {"api_key": api_key, "base_url": base_url, "model": model}
                )

        import white_salary.adapters.vision.multimodal_adapter as mm
        monkeypatch.setattr(mm, "MultimodalVisionAdapter", FakeMultimodal)

        img = self._make_image(tmp_path)
        result = await media_mod.describe_image(image_path=str(img))

        assert result == "现场构造的描述"
        assert constructed == [
            {"api_key": "sk-test", "base_url": "https://api.test/v1", "model": "test-vl"}
        ]

    async def test_failure_returns_chinese_guidance(self, tmp_path: Path, monkeypatch) -> None:
        """两条路都不通时返回中文配置指引（指向 docs/EXTERNAL_SERVICES.md）。"""
        from white_salary.infrastructure.server import settings_api
        monkeypatch.delitem(settings_api._runtime_registry, "vision", raising=False)

        # 假项目根：conf.yaml 缺 llm_vision 节 → 构造失败
        fake_root = tmp_path / "root"
        fake_root.mkdir()
        (fake_root / "conf.yaml").write_text("llm: {}\n", encoding="utf-8")
        monkeypatch.setattr(media_mod, "_project_root_path", lambda: fake_root)

        img = self._make_image(tmp_path)
        result = await media_mod.describe_image(image_path=str(img))

        assert "看图失败" in result
        assert "docs/EXTERNAL_SERVICES.md" in result

    async def test_local_image_missing(self, monkeypatch) -> None:
        """本地图片不存在时给明确中文原因（不假装成功）。"""
        from white_salary.infrastructure.server import settings_api
        monkeypatch.setitem(settings_api._runtime_registry, "vision", FakeVisionAdapter())

        result = await media_mod.describe_image(image_path="D:/不存在/图.png")
        assert "本地图片不存在" in result

    async def test_empty_path_rejected(self) -> None:
        assert "请提供" in await media_mod.describe_image(image_path="")

    async def test_screenshot_analyzes_captured_image(self, monkeypatch) -> None:
        """screenshot should feed the captured image into the vision adapter."""
        fake = FakeVisionAdapter(reply="屏幕上有一个聊天窗口")

        async def fake_capture_screenshot():
            return "ZmFrZQ=="

        monkeypatch.setattr(
            "white_salary.adapters.vision.screenshot.capture_screenshot",
            fake_capture_screenshot,
        )
        monkeypatch.setattr(media_mod, "_get_vision_adapter", lambda: (fake, ""))

        result = await media_mod.screenshot()

        assert "屏幕上有一个聊天窗口" in result
        assert fake.seen_base64 == ["ZmFrZQ=="]


# ================================================================
# 2. qq_send_file
# ================================================================

class FakeQQAdapter:
    """QQ适配器桩：记录 _call_api 调用，返回预设结果。"""

    def __init__(self, api_result=None) -> None:
        self._ws = object()  # 模拟已连接
        self._api_result = api_result
        self.calls: list[tuple[str, dict]] = []

    async def _call_api(self, action: str, params: dict, wait_response: bool = False):
        self.calls.append((action, dict(params)))
        return self._api_result


class TestQQSendFile:
    """qq_send_file 文件发送（NapCat upload_group_file/upload_private_file）。"""

    async def test_file_not_exist(self, monkeypatch) -> None:
        """文件不存在：明确中文失败原因，不调API。"""
        adapter = FakeQQAdapter()
        monkeypatch.setattr(qq_api_mod, "_qq_adapter", adapter)

        result = await qq_api_mod.qq_send_file(target="123", file_path="D:/不存在的文件.zip")
        assert "文件不存在" in result
        assert adapter.calls == []

    async def test_qq_not_connected(self, tmp_path: Path, monkeypatch) -> None:
        """QQ未连接：明确中文失败原因。"""
        f = tmp_path / "a.txt"
        f.write_text("hi", encoding="utf-8")
        monkeypatch.setattr(qq_api_mod, "_qq_adapter", None)

        result = await qq_api_mod.qq_send_file(target="123", file_path=str(f))
        assert "QQ未连接" in result

    async def test_private_success(self, tmp_path: Path, monkeypatch) -> None:
        """私聊成功：拿到NapCat响应数据才报「已发送」。"""
        f = tmp_path / "报告.pdf"
        f.write_bytes(b"pdf")
        adapter = FakeQQAdapter(api_result={"ok": 1})
        monkeypatch.setattr(qq_api_mod, "_qq_adapter", adapter)

        result = await qq_api_mod.qq_send_file(
            target="10086", file_path=str(f), is_group="false")

        assert "文件已发送" in result
        assert len(adapter.calls) == 1
        action, params = adapter.calls[0]
        assert action == "upload_private_file"
        assert params["user_id"] == 10086
        assert params["name"] == "报告.pdf"
        assert Path(params["file"]).exists()

    async def test_group_success(self, tmp_path: Path, monkeypatch) -> None:
        """群文件：走 upload_group_file。"""
        f = tmp_path / "b.txt"
        f.write_text("x", encoding="utf-8")
        adapter = FakeQQAdapter(api_result={"ok": 1})
        monkeypatch.setattr(qq_api_mod, "_qq_adapter", adapter)

        result = await qq_api_mod.qq_send_file(
            target="7777", file_path=str(f), is_group="true")

        assert "文件已发送" in result
        action, params = adapter.calls[0]
        assert action == "upload_group_file"
        assert params["group_id"] == 7777

    async def test_unconfirmed_is_honest(self, tmp_path: Path, monkeypatch) -> None:
        """没收到确认时如实说「未确认」，绝不谎报成功（qq_send_voice教训）。"""
        f = tmp_path / "c.txt"
        f.write_text("x", encoding="utf-8")
        adapter = FakeQQAdapter(api_result=None)
        monkeypatch.setattr(qq_api_mod, "_qq_adapter", adapter)

        result = await qq_api_mod.qq_send_file(
            target="10086", file_path=str(f), is_group="false")

        assert "文件已发送" not in result
        assert "没收到" in result or "未" in result

    async def test_context_fallback_to_group(self, tmp_path: Path, monkeypatch) -> None:
        """不传target时按当前会话上下文兜底（群聊→发群文件）。"""
        f = tmp_path / "d.txt"
        f.write_text("x", encoding="utf-8")
        adapter = FakeQQAdapter(api_result={"ok": 1})
        monkeypatch.setattr(qq_api_mod, "_qq_adapter", adapter)
        qq_api_mod.set_msg_context(group_id="888", user_id="999", is_group=True)

        result = await qq_api_mod.qq_send_file(file_path=str(f))

        assert "文件已发送" in result
        action, params = adapter.calls[0]
        assert action == "upload_group_file"
        assert params["group_id"] == 888

    async def test_invalid_target(self, tmp_path: Path, monkeypatch) -> None:
        """目标不是纯数字时拒绝。"""
        f = tmp_path / "e.txt"
        f.write_text("x", encoding="utf-8")
        monkeypatch.setattr(qq_api_mod, "_qq_adapter", FakeQQAdapter())

        result = await qq_api_mod.qq_send_file(target="小明", file_path=str(f))
        assert "纯数字" in result


class TestQQSendVoiceReceipt:
    """语音发送只有拿到真实 message_id 才能报告成功。"""

    @staticmethod
    def _patch_tts(monkeypatch) -> None:
        from white_salary.adapters.tts.gpt_sovits_adapter import GPTSoVITSAdapter

        async def synthesize(self, text: str):
            return AudioData(samples=b"RIFFfake", sample_rate=16000, dtype="int16")

        monkeypatch.setattr(GPTSoVITSAdapter, "synthesize", synthesize)
        monkeypatch.setattr(qq_api_mod, "_schedule_temp_cleanup", lambda path: None)

    async def test_success_requires_message_id(self, tmp_path: Path, monkeypatch) -> None:
        self._patch_tts(monkeypatch)
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        adapter = FakeQQAdapter(api_result={"message_id": 321})
        monkeypatch.setattr(qq_api_mod, "_qq_adapter", adapter)

        result = await qq_api_mod.qq_send_voice(user_id="10086", text="你好")

        assert result == "语音已发送"
        assert adapter.calls[0][0] == "send_private_msg"

    async def test_missing_receipt_is_unknown_not_success(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        self._patch_tts(monkeypatch)
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        adapter = FakeQQAdapter(api_result=None)
        monkeypatch.setattr(qq_api_mod, "_qq_adapter", adapter)

        with pytest.raises(ToolOutcomeUnknown, match="不要自动重发"):
            await qq_api_mod.qq_send_voice(user_id="10086", text="你好")


class TestQQSendExistingSticker:
    """“发已有表情包”与“AI生成新图”必须是两条独立工具链。"""

    class FakeStickerManager:
        def __init__(self, path: Path | None) -> None:
            self.path = path

        def get_next(self):
            return "1" if self.path else None

        def get_path(self, sticker_id):
            return self.path if sticker_id == "1" else None

    async def test_group_send_uses_existing_file_and_real_receipt(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        sticker = tmp_path / "sticker.png"
        sticker.write_bytes(b"png")
        adapter = FakeQQAdapter(api_result={"message_id": 456})
        monkeypatch.setattr(qq_api_mod, "_qq_adapter", adapter)
        monkeypatch.setattr(
            qq_api_mod,
            "_sticker_manager",
            self.FakeStickerManager(sticker),
        )
        qq_api_mod.set_msg_context(group_id="7788", user_id="10086", is_group=True)

        result = await qq_api_mod.qq_send_sticker()

        assert result == "表情包已发送"
        action, params = adapter.calls[0]
        assert action == "send_group_msg"
        assert params["group_id"] == 7788
        assert params["message"][0]["type"] == "image"
        assert params["message"][0]["data"]["file"] == sticker.resolve().as_uri()

    async def test_missing_receipt_is_unknown_not_duplicate_retry(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        sticker = tmp_path / "sticker.png"
        sticker.write_bytes(b"png")
        monkeypatch.setattr(qq_api_mod, "_qq_adapter", FakeQQAdapter(api_result=None))
        monkeypatch.setattr(
            qq_api_mod,
            "_sticker_manager",
            self.FakeStickerManager(sticker),
        )

        with pytest.raises(ToolOutcomeUnknown, match="不要自动重发"):
            await qq_api_mod.qq_send_sticker(user_id="10086")

    def test_tool_descriptions_keep_send_and_generate_separate(self) -> None:
        registry = ToolRegistry()
        send_tool = registry.get_tool("qq_send_sticker")
        generate_tool = registry.get_tool("generate_sticker")

        assert send_tool is not None and generate_tool is not None
        assert "不生成图片" in send_tool.description
        assert "全新的" in generate_tool.description
        assert "qq_send_sticker" in generate_tool.description


class TestQQSendImageReceipt:
    """图片发送和语音/表情包一样，必须拿到真实QQ消息回执。"""

    async def test_local_image_is_normalized_and_confirmed(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        image = tmp_path / "generated.png"
        image.write_bytes(b"png")
        adapter = FakeQQAdapter(api_result={"message_id": 789})
        monkeypatch.setattr(qq_api_mod, "_qq_adapter", adapter)
        qq_api_mod.set_msg_context(group_id="", user_id="10086", is_group=False)

        result = await qq_api_mod.qq_send_image(image_url=str(image))

        assert result == "图片已发送"
        action, params = adapter.calls[0]
        assert action == "send_private_msg"
        assert params["message"][0]["data"]["file"] == image.resolve().as_uri()

    async def test_missing_receipt_is_unknown(self, monkeypatch) -> None:
        monkeypatch.setattr(qq_api_mod, "_qq_adapter", FakeQQAdapter(api_result=None))

        with pytest.raises(ToolOutcomeUnknown, match="不要自动重发"):
            await qq_api_mod.qq_send_image(
                user_id="10086",
                image_url="https://example.test/image.png",
            )


class TestGeneratedImageQQDelivery:
    async def test_qq_context_auto_sends_generated_image(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        image = tmp_path / "generated.png"
        image.write_bytes(b"png")

        async def fake_generate(*args, **kwargs):
            return str(image)

        monkeypatch.setattr(
            "white_salary.adapters.tools.image_gen.generate_image",
            fake_generate,
        )
        adapter = FakeQQAdapter(api_result={"message_id": 800})
        monkeypatch.setattr(qq_api_mod, "_qq_adapter", adapter)
        qq_api_mod.set_msg_context(group_id="216", user_id="10086", is_group=True)

        result = await media_mod.generate_image(prompt="月光下的白", send_qq="auto")

        assert "已生成并发送到QQ" in result
        assert adapter.calls[0][0] == "send_group_msg"
        assert adapter.calls[0][1]["group_id"] == 216

    def test_generation_tools_have_fallback_budget(self) -> None:
        assert get_tool_timeout("generate_image") >= 360
        assert get_tool_timeout("draw") >= 360
        assert get_tool_timeout("generate_sticker") >= 360


# ================================================================
# 3. download_video
# ================================================================

def _install_fake_ytdlp(monkeypatch, info: dict, create_file: Path | None = None,
                        raise_on_extract: Exception | None = None):
    """往 sys.modules 注入假 yt_dlp 模块（extract_info 返回预设info）。"""

    class FakeYDL:
        def __init__(self, opts: dict) -> None:
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url: str, download: bool = False):
            if raise_on_extract is not None:
                raise raise_on_extract
            if download and create_file is not None:
                create_file.parent.mkdir(parents=True, exist_ok=True)
                create_file.write_bytes(b"video-bytes")
            return dict(info)

        def prepare_filename(self, result: dict) -> str:
            return str(create_file) if create_file is not None else ""

    fake_mod = types.SimpleNamespace(YoutubeDL=FakeYDL)
    monkeypatch.setitem(sys.modules, "yt_dlp", fake_mod)


class TestDownloadVideo:
    """download_video 真下载（yt_dlp）。"""

    async def test_rejects_non_url(self) -> None:
        result = await download_mod.download_video(url="随便一句话")
        assert "不是有效的网址" in result

    async def test_rejects_over_duration(self, tmp_path: Path, monkeypatch) -> None:
        """时长超30分钟：预检拒绝并说明，不下载。"""
        monkeypatch.setattr(download_mod, "_downloads_dir", lambda: tmp_path)
        _install_fake_ytdlp(monkeypatch, {"title": "超长视频", "duration": 3600})

        result = await download_mod.download_video(url="https://video.test/v/1")
        assert "下载失败" in result
        assert "30分钟" in result and "超长视频" in result

    async def test_rejects_over_filesize(self, tmp_path: Path, monkeypatch) -> None:
        """预估大小超500MB：预检拒绝并说明。"""
        monkeypatch.setattr(download_mod, "_downloads_dir", lambda: tmp_path)
        _install_fake_ytdlp(monkeypatch, {
            "title": "大文件", "duration": 60,
            "filesize": 600 * 1024 * 1024,
        })

        result = await download_mod.download_video(url="https://video.test/v/2")
        assert "下载失败" in result and "500MB" in result

    async def test_success_returns_path_and_title(self, tmp_path: Path, monkeypatch) -> None:
        """成功：返回保存路径与标题，文件真实落盘。"""
        monkeypatch.setattr(download_mod, "_downloads_dir", lambda: tmp_path)
        saved = tmp_path / "好视频-abc.mp4"
        _install_fake_ytdlp(
            monkeypatch,
            {"title": "好视频", "duration": 120, "filesize": 10 * 1024 * 1024},
            create_file=saved,
        )

        result = await download_mod.download_video(url="https://video.test/v/3")
        assert "好视频" in result
        assert str(saved) in result
        assert saved.exists()

    async def test_ytdlp_error_translated_to_chinese(self, tmp_path: Path, monkeypatch) -> None:
        """yt_dlp 报错（如非视频站直链）转译成中文。"""
        monkeypatch.setattr(download_mod, "_downloads_dir", lambda: tmp_path)
        _install_fake_ytdlp(
            monkeypatch, {},
            raise_on_extract=RuntimeError("ERROR: Unsupported URL: https://example.com/a.zip"),
        )

        result = await download_mod.download_video(url="https://example.com/a.zip")
        assert "下载失败" in result
        assert "不是受支持的视频页面" in result

    def test_timeout_budget_600(self) -> None:
        """registry 超时表给 download_video 600秒预算。"""
        assert get_tool_timeout("download_video") >= 600


# ================================================================
# 4. qq_inbox
# ================================================================

class _Entry:
    """ConversationEntry 桩。"""

    def __init__(self, user_id: str, user_name: str, user_msg: str,
                 ai_reply: str, group_id: str = "") -> None:
        self.user_id = user_id
        self.user_name = user_name
        self.user_msg = user_msg
        self.ai_reply = ai_reply
        self.group_id = group_id
        self.time_str = "07-03 10:00"


class TestQQInboxInference:
    """qq_inbox 的「可能没回的人」推断逻辑。"""

    def test_infer_unreplied_basic(self) -> None:
        """最后一条没回→列入；回过→不列；群聊→跳过；按用户去重取最新。"""
        entries = [  # 按时间倒序
            _Entry("111", "小明", "在吗？", ""),               # 没回 → 列入
            _Entry("222", "小红", "晚安", "晚安～"),            # 回过 → 不列
            _Entry("333", "群友", "群消息", "", group_id="99"),  # 群聊 → 跳过
            _Entry("111", "小明", "早上的消息", "早～"),         # 更早的记录，已去重
        ]
        result = chat_mod._infer_unreplied(entries)
        assert [u["user_id"] for u in result] == ["111"]
        assert result[0]["last_msg"] == "在吗？"

    def test_infer_unreplied_empty(self) -> None:
        assert chat_mod._infer_unreplied([]) == []

    async def test_unread_action_reports_inference(self, monkeypatch) -> None:
        """action=unread：推断结果如实标注「推断」。"""
        from white_salary.core.memory.conversation_log import ConversationLog

        class FakeLog:
            def search(self, platform: str = "", limit: int = 15, days: int = 30):
                return [_Entry("111", "小明", "在吗？", "")]

        monkeypatch.setattr(ConversationLog, "get_instance",
                            lambda data_dir="data/memory": FakeLog())

        result = await chat_mod.qq_inbox(action="unread")
        assert "小明" in result
        assert "推断" in result  # 如实说明是推断

    async def test_unread_action_all_replied(self, monkeypatch) -> None:
        """都回过时明确说没有发现。"""
        from white_salary.core.memory.conversation_log import ConversationLog

        class FakeLog:
            def search(self, platform: str = "", limit: int = 15, days: int = 30):
                return [_Entry("222", "小红", "晚安", "晚安～")]

        monkeypatch.setattr(ConversationLog, "get_instance",
                            lambda data_dir="data/memory": FakeLog())

        result = await chat_mod.qq_inbox(action="unread")
        assert "没有发现" in result

    async def test_recent_action_fallback_when_qq_down(self, monkeypatch) -> None:
        """action=recent：QQ未连接时退回本地日志统计并如实说明。"""
        from white_salary.core.memory.conversation_log import ConversationLog

        async def fake_call(action: str, params: dict) -> str:
            return "操作失败：QQ未连接"

        monkeypatch.setattr(qq_api_mod, "_call", fake_call)

        class FakeLog:
            def get_active_users(self, days: int = 7, limit: int = 20):
                return [{"user_id": "111", "user_name": "小明", "count": 5, "last": 0.0}]

        monkeypatch.setattr(ConversationLog, "get_instance",
                            lambda data_dir="data/memory": FakeLog())

        result = await chat_mod.qq_inbox(action="recent")
        assert "小明" in result
        assert "本地聊天记录" in result  # 如实说明不是QQ实时数据


# ================================================================
# 5. view_learned_style
# ================================================================

def _make_style_root(tmp_path: Path, disabled: list[str]) -> Path:
    """搭一个假项目根：config/memory_settings.json + data/memory。"""
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    (root / "data" / "memory").mkdir(parents=True)
    (root / "config" / "memory_settings.json").write_text(
        json.dumps({"modules": {"disabled": disabled}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


class TestViewLearnedStyle:
    """view_learned_style 学习数据查看。"""

    async def test_disabled_module_hint(self, tmp_path: Path, monkeypatch) -> None:
        """模块被禁用时如实提示去控制面板开启。"""
        root = _make_style_root(tmp_path, ["slang_learner", "expression_learner"])
        monkeypatch.setattr(chat_mod, "_project_root_path", lambda: root)

        result = await chat_mod.view_learned_style(kind="slang")
        assert "已关闭" in result and "控制面板" in result
        result2 = await chat_mod.view_learned_style(kind="phrases")
        assert "已关闭" in result2

    async def test_slang_data_reading(self, tmp_path: Path, monkeypatch) -> None:
        """读 learned_slang.json 并格式化输出。"""
        root = _make_style_root(tmp_path, [])
        (root / "data" / "memory" / "learned_slang.json").write_text(
            json.dumps({
                "xdm": {"meaning": "兄弟们", "usage": "xdm冲了", "seen_count": 8},
                "yysy": {"meaning": "有一说一", "usage": "", "seen_count": 3},
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        monkeypatch.setattr(chat_mod, "_project_root_path", lambda: root)

        result = await chat_mod.view_learned_style(kind="slang")
        assert "2 个网络用语" in result
        assert "xdm" in result and "兄弟们" in result

    async def test_phrases_data_reading(self, tmp_path: Path, monkeypatch) -> None:
        """读 expression_styles/*.json 风格画像。"""
        root = _make_style_root(tmp_path, [])
        styles_dir = root / "data" / "memory" / "expression_styles"
        styles_dir.mkdir(parents=True)
        (styles_dir / "111.json").write_text(
            json.dumps({
                "user_name": "小明", "user_id": "111", "tone": "活泼",
                "vocabulary": ["哈哈", "绝了"], "habits": ["爱用~结尾"],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        monkeypatch.setattr(chat_mod, "_project_root_path", lambda: root)

        result = await chat_mod.view_learned_style(kind="phrases")
        assert "小明" in result and "活泼" in result

    async def test_no_data_yet(self, tmp_path: Path, monkeypatch) -> None:
        """模块开着但还没数据时如实说「还没学到」。"""
        root = _make_style_root(tmp_path, [])
        monkeypatch.setattr(chat_mod, "_project_root_path", lambda: root)

        result = await chat_mod.view_learned_style(kind="all")
        assert result.count("还没学到") == 2

    def test_project_config_reenabled_learning(self) -> None:
        """批9承诺：真实项目配置已把两个学习模块从禁用列表移除（恢复学习）。"""
        cfg = json.loads(
            (PROJECT_ROOT / "config" / "memory_settings.json").read_text(encoding="utf-8")
        )
        disabled = set((cfg.get("modules") or {}).get("disabled") or [])
        assert "slang_learner" not in disabled
        assert "expression_learner" not in disabled


# ================================================================
# 6. deep_think
# ================================================================

class FakeReasoningLLM:
    """OpenAICompatibleAdapter 桩：记录构造参数与消息，返回预设推理。"""

    instances: list["FakeReasoningLLM"] = []

    def __init__(self, api_key: str, base_url: str, model: str,
                 timeout: float = 60.0) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.messages_seen: list = []
        self.closed = False
        self.reply: str = "结论：应该选方案A。依据：成本更低。"
        self.raise_on_chat: Exception | None = None
        FakeReasoningLLM.instances.append(self)

    async def chat_completion(self, messages, temperature: float = 0.7,
                              max_tokens: int = 2048) -> str:
        self.messages_seen = list(messages)
        if self.raise_on_chat is not None:
            raise self.raise_on_chat
        return self.reply

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_reasoning_llm(monkeypatch):
    """把 OpenAICompatibleAdapter 换成桩（deep_think 函数内按属性名取，patch源模块即可）。"""
    FakeReasoningLLM.instances = []
    import white_salary.adapters.llm.openai_compatible as oc
    monkeypatch.setattr(oc, "OpenAICompatibleAdapter", FakeReasoningLLM)
    return FakeReasoningLLM


class TestDeepThink:
    """deep_think 真调辅助LLM推理。"""

    async def test_uses_llm_background_channel(self, monkeypatch, fake_reasoning_llm) -> None:
        """优先用 llm_background 通道，system提示为深度思考助手，用完关闭连接。"""
        monkeypatch.setattr(reasoning_mod, "_load_conf", lambda: {
            "llm_background": {"api_key": "k1", "base_url": "https://bg/v1", "model": "m1"},
            "llm_postprocess": {"api_key": "k2", "base_url": "https://pp/v1", "model": "m2"},
        })

        result = await reasoning_mod.deep_think(question="方案A和方案B怎么选？")

        assert "结论：应该选方案A" in result
        assert "llm_background" in result
        inst = fake_reasoning_llm.instances[0]
        assert inst.api_key == "k1" and inst.model == "m1"
        assert inst.closed is True
        # system 提示是深度思考助手
        assert "深度思考助手" in inst.messages_seen[0].content
        assert "方案A和方案B怎么选" in inst.messages_seen[1].content

    async def test_fallback_to_postprocess(self, monkeypatch, fake_reasoning_llm) -> None:
        """llm_background 没配时用 llm_postprocess 兜底。"""
        monkeypatch.setattr(reasoning_mod, "_load_conf", lambda: {
            "llm_postprocess": {"api_key": "k2", "base_url": "https://pp/v1", "model": "m2"},
        })

        result = await reasoning_mod.deep_think(question="为什么天空是蓝的？")
        assert "llm_postprocess" in result
        assert fake_reasoning_llm.instances[0].api_key == "k2"

    async def test_no_channel_configured(self, monkeypatch, fake_reasoning_llm) -> None:
        """两个通道都没配时给中文配置指引。"""
        monkeypatch.setattr(reasoning_mod, "_load_conf", lambda: {})

        result = await reasoning_mod.deep_think(question="问题")
        assert "深度思考失败" in result and "llm_background" in result
        assert fake_reasoning_llm.instances == []  # 没有白白构造适配器

    async def test_llm_error_is_reported_and_closed(self, monkeypatch, fake_reasoning_llm) -> None:
        """LLM报错时中文兜底，且连接仍被关闭。"""
        monkeypatch.setattr(reasoning_mod, "_load_conf", lambda: {
            "llm_background": {"api_key": "k1", "base_url": "https://bg/v1", "model": "m1"},
        })

        class Boom(FakeReasoningLLM):
            def __init__(self, *a, **kw) -> None:
                super().__init__(*a, **kw)
                self.raise_on_chat = RuntimeError("boom")

        import white_salary.adapters.llm.openai_compatible as oc
        monkeypatch.setattr(oc, "OpenAICompatibleAdapter", Boom)

        result = await reasoning_mod.deep_think(question="问题")
        assert "深度思考失败" in result and "出错" in result
        assert FakeReasoningLLM.instances[-1].closed is True

    async def test_empty_question(self) -> None:
        assert "请提供" in await reasoning_mod.deep_think(question="")


# ================================================================
# 7. 注册表：新工具上架、旧名保持移除、超时表
# ================================================================

class TestBatch9Registry:
    """批9工具上架后的注册表校验。"""

    NEW_TOOLS = ["describe_image", "qq_send_file", "download_video",
                 "qq_inbox", "view_learned_style", "deep_think"]
    STILL_DELISTED = ["check_unread", "dm_cleanup", "view_learned_phrases",
                      "view_learned_slang", "reasoning", "deep_reasoning",
                      "send_file", "sing", "music_gen"]

    def test_new_tools_registered(self) -> None:
        names = {t.name for t in ToolRegistry().get_all()}
        missing = [n for n in self.NEW_TOOLS if n not in names]
        assert not missing, f"批9工具未注册: {missing}"

    def test_old_names_stay_delisted(self) -> None:
        names = {t.name for t in ToolRegistry().get_all()}
        leaked = [n for n in self.STILL_DELISTED if n in names]
        assert not leaked, f"被合并/下架的旧名不应回到注册表: {leaked}"

    def test_timeout_table_entries(self) -> None:
        assert TOOL_TIMEOUTS.get("download_video", 0) >= 600
        assert TOOL_TIMEOUTS.get("deep_think", 0) >= 120

    def test_registry_under_deepseek_limit(self) -> None:
        """加上批9工具后总数仍远离 DeepSeek 128 上限。"""
        assert ToolRegistry().count < 126
