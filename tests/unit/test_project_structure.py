"""
测试项目结构是否完整。

验证所有必要的目录和文件是否存在。
"""

import json
import re
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
        """公开仓库应提交配置模板，而不是用户私密配置。"""
        assert (PROJECT_ROOT / "pyproject.toml").is_file()
        assert (PROJECT_ROOT / "conf.default.yaml").is_file()
        gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "conf.yaml" in gitignore

    def test_docker_files_keep_dependencies_and_secrets_separate(self) -> None:
        """Docker 构建应走 pyproject 依赖，并排除本地隐私/大文件上下文。"""
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        dockerignore_path = PROJECT_ROOT / ".dockerignore"
        dockerignore = dockerignore_path.read_text(encoding="utf-8")

        assert dockerignore_path.is_file()
        assert 'pip install --no-cache-dir -e ".[memory-vector]"' in dockerfile
        assert "pip install --no-cache-dir fastapi uvicorn" not in dockerfile
        assert "COPY pyproject.toml README.md ./" in dockerfile
        assert "COPY src/ src/" in dockerfile

        for required_ignore in [
            ".git/",
            ".venv/",
            "conf.yaml",
            "data/",
            "logs/",
            "NapCat/",
            "frontend/node_modules/",
            "prompts/system_prompt.txt",
        ]:
            assert required_ignore in dockerignore

    def test_pyproject_runtime_dependency_baseline(self) -> None:
        """uv sync should install the runtime LLM SDK without optional extras."""
        text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert 'requires-python = ">=3.10,<3.13"' in text
        dependencies_block = text.split("dependencies = [", 1)[1].split(
            "[project.optional-dependencies]", 1
        )[0]
        assert '"openai>=1.50.0"' in dependencies_block
        assert '"httpx>=0.27.0"' in dependencies_block
        assert '"yt-dlp>=2026.1.0"' in dependencies_block
        assert '"pillow>=10.0.0"' in dependencies_block
        assert '"mss>=9.0.0"' in dependencies_block

    def test_version_metadata_is_consistent(self) -> None:
        """Published version metadata should not drift across Python/frontend/docs."""
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        init_py = (PROJECT_ROOT / "src" / "white_salary" / "__init__.py").read_text(
            encoding="utf-8"
        )
        config_models = (
            PROJECT_ROOT / "src" / "white_salary" / "infrastructure" / "config" / "models.py"
        ).read_text(encoding="utf-8")
        frontend_pkg = json.loads((PROJECT_ROOT / "frontend" / "package.json").read_text(
            encoding="utf-8"
        ))

        version = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE).group(1)
        assert f'__version__ = "{version}"' in init_py
        assert f'default="{version}"' in config_models
        assert frontend_pkg["version"] == version

    def test_pyproject_optional_dependency_groups_cover_feature_imports(self) -> None:
        """Optional feature imports should have matching extras for users who enable them."""
        text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "desktop-control = [" in text
        assert '"pyautogui>=0.9.54"' in text
        assert '"pyperclip>=1.9.0"' in text
        assert "bilibili = [" in text
        assert '"bilibili-api-python>=17.0.0"' in text
        assert '"qrcode[pil]>=8.0"' in text
        assert '"cryptography>=42.0.0"' in text
        assert "vad-silero = [" in text
        assert '"torch>=2.0.0"' in text
        assert "singing-rvc = []" in text
        assert '"rvc-python>=0.1.0"' not in text
        all_block = text.split("all = [", 1)[1].split("]", 1)[0]
        assert "rvc-python" not in all_block

    def test_windows_launcher_uses_project_venv(self) -> None:
        """Install/start scripts should use the Windows project .venv."""
        install = (PROJECT_ROOT / "安装.bat").read_text(encoding="utf-8")
        start_backend = (PROJECT_ROOT / "Start-Backend.bat").read_text(encoding="utf-8")
        assert "python -m venv" in install
        assert ".venv\\Scripts\\python.exe" in install
        assert '"%PROJECT_PYTHON%" -m pip install -e .' in install
        assert "yt_dlp" in install
        assert "PIL" in install
        assert "mss" in install
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
        """运行时数据目录应由程序创建，不随公开仓库提交。"""
        gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "data/" in gitignore

    def test_prompts_directory_exists(self) -> None:
        """提示词目录和可复制模板必须存在。"""
        assert (PROJECT_ROOT / "prompts").is_dir()
        assert (PROJECT_ROOT / "prompts" / "system_prompt.example.txt").is_file()
