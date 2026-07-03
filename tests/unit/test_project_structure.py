"""
测试项目结构是否完整。

验证所有必要的目录和文件是否存在。
"""

from pathlib import Path


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent


class TestDirectoryStructure:
    """测试目录结构完整性。"""

    def test_source_root_exists(self) -> None:
        """源代码根目录 src/white_salary/ 必须存在。"""
        assert (PROJECT_ROOT / "src" / "white_salary").is_dir()

    def test_core_directories_exist(self) -> None:
        """核心域的所有子目录必须存在。"""
        core = PROJECT_ROOT / "src" / "white_salary" / "core"
        expected_dirs = ["agent", "memory", "emotion", "personality", "filter", "interfaces"]
        for d in expected_dirs:
            assert (core / d).is_dir(), f"缺少目录: core/{d}"

    def test_adapter_directories_exist(self) -> None:
        """适配器层的所有子目录必须存在。"""
        adapters = PROJECT_ROOT / "src" / "white_salary" / "adapters"
        expected_dirs = [
            "llm", "asr", "tts", "vad", "vision",
            "singing", "game", "avatar", "tools", "storage",
        ]
        for d in expected_dirs:
            assert (adapters / d).is_dir(), f"缺少目录: adapters/{d}"

    def test_infrastructure_directories_exist(self) -> None:
        """基础设施层的所有子目录必须存在。"""
        infra = PROJECT_ROOT / "src" / "white_salary" / "infrastructure"
        expected_dirs = ["config", "server", "session", "pipeline", "logging", "events"]
        for d in expected_dirs:
            assert (infra / d).is_dir(), f"缺少目录: infrastructure/{d}"

    def test_config_files_exist(self) -> None:
        """配置文件必须存在。"""
        assert (PROJECT_ROOT / "pyproject.toml").is_file()
        assert (PROJECT_ROOT / "conf.default.yaml").is_file()
        assert (PROJECT_ROOT / "conf.yaml").is_file()

    def test_pyproject_runtime_dependency_baseline(self) -> None:
        """uv sync should install the runtime LLM SDK without optional extras."""
        text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert 'requires-python = ">=3.10"' in text
        dependencies_block = text.split("dependencies = [", 1)[1].split(
            "[project.optional-dependencies]", 1
        )[0]
        assert '"openai>=1.50.0"' in dependencies_block

    def test_windows_launcher_uses_project_venv(self) -> None:
        """Install/start scripts should use the Windows project .venv."""
        install = (PROJECT_ROOT / "安装.bat").read_text(encoding="utf-8")
        start_backend = (PROJECT_ROOT / "Start-Backend.bat").read_text(encoding="utf-8")
        assert "python -m venv" in install
        assert ".venv\\Scripts\\python.exe" in install
        assert '"%PROJECT_PYTHON%" -m pip install -e .' in install
        assert ".venv\\Scripts\\python.exe" in start_backend

    def test_gpt_sovits_launchers_use_configured_path(self) -> None:
        """GPT-SoVITS launchers should resolve paths from config/env, not cd to one machine."""
        files = [
            PROJECT_ROOT / "Start.bat",
            PROJECT_ROOT / "Start-TTS.bat",
            PROJECT_ROOT / "Start-TTS-Local.bat",
            PROJECT_ROOT / "scripts" / "train_voice.bat",
            PROJECT_ROOT / "frontend" / "main.js",
        ]
        for path in files:
            text = path.read_text(encoding="utf-8")
            assert "resolve_gpt_sovits_dir.py" in text
            assert 'cd /d "D:\\AI_Tools\\GPT-SoVITS"' not in text
            assert "D:\\\\AI_Tools\\\\GPT-SoVITS &&" not in text

    def test_entry_point_exists(self) -> None:
        """主入口文件必须存在。"""
        assert (PROJECT_ROOT / "run_server.py").is_file()

    def test_data_directories_exist(self) -> None:
        """数据目录必须存在。"""
        data = PROJECT_ROOT / "data"
        assert (data / "chat_history").is_dir()
        assert (data / "memory").is_dir()
        assert (data / "knowledge").is_dir()

    def test_prompts_directory_exists(self) -> None:
        """提示词目录和默认提示词文件必须存在。"""
        assert (PROJECT_ROOT / "prompts").is_dir()
        assert (PROJECT_ROOT / "prompts" / "system_prompt.txt").is_file()
