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
