"""
外部工具路径配置化（2026-07-03 外部依赖优化 批8）的单元测试。

覆盖：
  1. ExternalToolsConfig 默认值全为空串（"不覆盖，用内置默认"）、挂进 AppConfig；
  2. conf.yaml 里 external_tools 节能被 load_config 正确解析、深合并；
  3. external_paths 的三级解析优先级：环境变量 > 配置 > 内置默认值；
  4. ffmpeg 查找的两种历史顺序（prefer_path_first）与显式路径优先；
  5. 缺外部服务时各工具返回明确的中文提示（指向配置文档），不静默含糊。

约束（铁律#4）：配置化不许改变现有默认行为——各内置默认值 == 各文件历史硬编码值。
"""

import os
from pathlib import Path

import pytest

from white_salary.adapters.tools import external_paths as ep
from white_salary.infrastructure.config import ExternalToolsConfig, load_config
from white_salary.infrastructure.config.models import AppConfig


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent


def _write_minimal_default(tmp_path: Path, extra: str = "") -> None:
    """写一份最小可用的 conf.default.yaml 到临时目录。"""
    (tmp_path / "conf.default.yaml").write_text(
        "system:\n  name: \"White Salary\"\n" + extra,
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _reset_external_paths_cache():
    """每个用例前后清空 external_paths 的配置缓存，避免相互污染。"""
    ep.reset_cache()
    yield
    ep.reset_cache()


class TestExternalToolsConfigDefaults:
    """ExternalToolsConfig 默认值与挂载测试。"""

    def test_all_fields_default_empty(self) -> None:
        """默认值全为空串 = '不覆盖，用环境变量或内置默认值'。"""
        et = ExternalToolsConfig()
        assert et.comfyui_bat == ""
        assert et.comfyui_input == ""
        assert et.gpt_sovits_dir == ""
        assert et.cosyvoice_bat == ""
        assert et.wav2lip_dir == ""
        assert et.ffmpeg_path == ""

    def test_appconfig_has_external_tools(self) -> None:
        """AppConfig 必须有 external_tools 节且类型正确。"""
        config = AppConfig()
        assert isinstance(config.external_tools, ExternalToolsConfig)

    def test_real_project_config_has_external_tools(self) -> None:
        """真实项目配置加载后 external_tools 节存在且默认全空（conf.yaml 未覆盖时）。"""
        config = load_config(project_root=PROJECT_ROOT)
        et = config.external_tools
        assert isinstance(et, ExternalToolsConfig)
        assert et.comfyui_bat == ""
        assert et.ffmpeg_path == ""

    def test_parsed_from_user_yaml(self, tmp_path: Path) -> None:
        """用户 conf.yaml 的 external_tools 配置能被解析、深合并。"""
        _write_minimal_default(
            tmp_path,
            "external_tools:\n"
            "  comfyui_bat: \"\"\n"
            "  gpt_sovits_dir: \"\"\n",
        )
        (tmp_path / "conf.yaml").write_text(
            "external_tools:\n"
            "  comfyui_bat: E:/tools/ComfyUI/run.bat\n",
            encoding="utf-8",
        )
        config = load_config(project_root=tmp_path)
        assert config.external_tools.comfyui_bat == "E:/tools/ComfyUI/run.bat"
        # 未覆盖字段保持默认空串
        assert config.external_tools.gpt_sovits_dir == ""


class TestDefaultsMatchHardcoded:
    """内置默认值黄金测试——必须逐字等于各文件历史硬编码值（防回归）。"""

    def test_default_constants(self) -> None:
        assert ep.DEFAULT_COMFYUI_BAT == (
            "D:/cccccccccc/ComfyUI_windows_portable/run_nvidia_gpu.bat"
        )
        assert ep.DEFAULT_COMFYUI_INPUT == (
            "D:/cccccccccc/ComfyUI_windows_portable/ComfyUI/input"
        )
        assert ep.DEFAULT_GPT_SOVITS_DIR == "D:/AI_Tools/GPT-SoVITS"
        assert ep.DEFAULT_COSYVOICE_BAT == "D:/AI_Tools/CosyVoice/start_cosyvoice.bat"
        assert ep.DEFAULT_WAV2LIP_DIR == "D:/AI/Wav2Lip"
        assert ep.DEFAULT_FFMPEG_PATHS == (
            "D:/AI_Tools/ffmpeg-8.0.1-essentials_build/bin/ffmpeg.exe",
            "D:/AI/ffmpeg/ffmpeg.exe",
        )

    def test_resolve_falls_back_to_default_when_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无环境变量、无配置时，各解析函数返回内置默认值。"""
        for var in (
            "WS_COMFYUI_BAT", "WS_COMFYUI_INPUT", "WS_GPT_SOVITS_DIR",
            "WS_COSYVOICE_BAT", "WS_WAV2LIP_DIR",
        ):
            monkeypatch.delenv(var, raising=False)
        # 强制配置为"无 external_tools"
        ep._cached_external_tools = None
        ep._load_attempted = True

        assert str(ep.get_comfyui_bat()) == str(Path(ep.DEFAULT_COMFYUI_BAT))
        assert str(ep.get_comfyui_input()) == str(Path(ep.DEFAULT_COMFYUI_INPUT))
        assert str(ep.get_gpt_sovits_dir()) == str(Path(ep.DEFAULT_GPT_SOVITS_DIR))
        assert str(ep.get_cosyvoice_bat()) == str(Path(ep.DEFAULT_COSYVOICE_BAT))
        assert str(ep.get_wav2lip_dir()) == str(Path(ep.DEFAULT_WAV2LIP_DIR))


class _FakeExternalTools:
    """模拟 ExternalToolsConfig（只带需要的字段）。"""

    def __init__(self, **kw: str) -> None:
        self.comfyui_bat = kw.get("comfyui_bat", "")
        self.comfyui_input = kw.get("comfyui_input", "")
        self.gpt_sovits_dir = kw.get("gpt_sovits_dir", "")
        self.cosyvoice_bat = kw.get("cosyvoice_bat", "")
        self.wav2lip_dir = kw.get("wav2lip_dir", "")
        self.ffmpeg_path = kw.get("ffmpeg_path", "")


class TestResolutionPriority:
    """三级解析优先级：环境变量 > 配置 > 内置默认值。"""

    def test_env_beats_config_and_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """设了环境变量 → 用环境变量（即使配置也有值）。"""
        ep._cached_external_tools = _FakeExternalTools(comfyui_bat="F:/cfg/run.bat")
        ep._load_attempted = True
        monkeypatch.setenv("WS_COMFYUI_BAT", "E:/env/run.bat")
        assert str(ep.get_comfyui_bat()) == str(Path("E:/env/run.bat"))

    def test_config_beats_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无环境变量、有配置 → 用配置。"""
        monkeypatch.delenv("WS_COSYVOICE_BAT", raising=False)
        ep._cached_external_tools = _FakeExternalTools(
            cosyvoice_bat="F:/cfg/cosy.bat"
        )
        ep._load_attempted = True
        assert str(ep.get_cosyvoice_bat()) == str(Path("F:/cfg/cosy.bat"))

    def test_default_when_config_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """配置为空串（用户没填）→ 回退内置默认值。"""
        monkeypatch.delenv("WS_WAV2LIP_DIR", raising=False)
        ep._cached_external_tools = _FakeExternalTools(wav2lip_dir="")
        ep._load_attempted = True
        assert str(ep.get_wav2lip_dir()) == str(Path(ep.DEFAULT_WAV2LIP_DIR))

    def test_empty_env_var_falls_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """环境变量设为空串 → 视为未设置，继续向下回退到配置。"""
        monkeypatch.setenv("WS_GPT_SOVITS_DIR", "")
        ep._cached_external_tools = _FakeExternalTools(
            gpt_sovits_dir="F:/cfg/sovits"
        )
        ep._load_attempted = True
        assert str(ep.get_gpt_sovits_dir()) == str(Path("F:/cfg/sovits"))


class TestFindFfmpeg:
    """ffmpeg 查找顺序测试。"""

    def test_explicit_env_wins_if_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """环境变量 WS_FFMPEG_PATH 指向存在的文件时最优先。"""
        fake = tmp_path / "ffmpeg.exe"
        fake.write_text("x", encoding="utf-8")
        monkeypatch.setenv("WS_FFMPEG_PATH", str(fake))
        ep._cached_external_tools = None
        ep._load_attempted = True
        assert ep.find_ffmpeg() == str(fake)

    def test_config_ffmpeg_wins_if_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """配置 ffmpeg_path 指向存在的文件时优先于 PATH/候选。"""
        fake = tmp_path / "cfg_ffmpeg.exe"
        fake.write_text("x", encoding="utf-8")
        monkeypatch.delenv("WS_FFMPEG_PATH", raising=False)
        ep._cached_external_tools = _FakeExternalTools(ffmpeg_path=str(fake))
        ep._load_attempted = True
        assert ep.find_ffmpeg() == str(fake)

    def test_nonexistent_env_falls_through_to_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """环境变量/配置都指向不存在的文件、PATH 无 ffmpeg、候选不存在 → None。"""
        monkeypatch.setenv("WS_FFMPEG_PATH", "Z:/nope/ffmpeg.exe")
        ep._cached_external_tools = _FakeExternalTools(ffmpeg_path="Z:/also_nope.exe")
        ep._load_attempted = True
        # shutil.which 与内置候选都置空
        import shutil as _shutil

        monkeypatch.setattr(_shutil, "which", lambda name: None)
        monkeypatch.setattr(ep, "DEFAULT_FFMPEG_PATHS", ())
        assert ep.find_ffmpeg() is None

    def test_prefer_path_first_ordering(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """prefer_path_first=True 时先查 PATH（保留 audio_convert 历史顺序）。"""
        monkeypatch.delenv("WS_FFMPEG_PATH", raising=False)
        ep._cached_external_tools = _FakeExternalTools()
        ep._load_attempted = True
        import shutil as _shutil

        monkeypatch.setattr(_shutil, "which", lambda name: "C:/path/ffmpeg.exe")
        # 即便内置候选也"存在"，prefer_path_first 也应先拿 PATH 的
        monkeypatch.setattr(ep, "DEFAULT_FFMPEG_PATHS", ("C:/candidate/ffmpeg.exe",))
        monkeypatch.setattr(Path, "exists", lambda self: True)
        assert ep.find_ffmpeg(prefer_path_first=True) == "C:/path/ffmpeg.exe"


class TestDegradationMessages:
    """缺外部服务时的中文降级文案——必须明确、指向配置文档、不静默含糊。"""

    async def test_lip_sync_missing_wav2lip_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wav2Lip 未安装时返回指向 external_tools.wav2lip_dir + 文档的提示。"""
        from white_salary.adapters.tools.builtin import video as video_tools

        # 让 wav2lip 目录指向一个不存在的临时目录
        monkeypatch.setattr(
            ep, "_cached_external_tools",
            _FakeExternalTools(wav2lip_dir="Z:/nonexistent_wav2lip"),
        )
        monkeypatch.setattr(ep, "_load_attempted", True)
        result = await video_tools.local_lip_sync(
            audio_path="a.wav", video_path="v.mp4"
        )
        assert "Wav2Lip" in result
        assert "EXTERNAL_SERVICES" in result
        assert "external_tools" in result

    async def test_generate_image_all_fail_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """生图三级降级全失败时返回明确中文提示（指向云端 key + 本地路径 + 文档）。"""
        from white_salary.adapters.tools.builtin import media as media_tools

        async def _fake_gen(*args, **kwargs):
            return None

        monkeypatch.setattr(
            "white_salary.adapters.tools.image_gen.generate_image", _fake_gen
        )
        result = await media_tools.generate_image(prompt="一只猫")
        assert "生图失败" in result
        assert "ComfyUI" in result
        assert "硅基流动" in result or "DMXAPI" in result
        assert "EXTERNAL_SERVICES" in result

    async def test_edit_image_fail_message(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """改图失败时返回指向 ComfyUI + external_tools + 文档的提示。"""
        from white_salary.adapters.tools.builtin import media as media_tools

        img = tmp_path / "src.png"
        img.write_bytes(b"\x89PNG\r\n")

        async def _fake_edit(*args, **kwargs):
            return None

        monkeypatch.setattr(
            "white_salary.adapters.tools.image_gen.edit_image", _fake_edit
        )
        result = await media_tools.edit_image_tool(
            image_path=str(img), prompt="换红色衣服"
        )
        assert "失败" in result
        assert "ComfyUI" in result
        assert "EXTERNAL_SERVICES" in result
