"""
pytest 全局配置文件。

这个文件会在运行测试时自动加载。
在这里定义所有测试共享的 fixture（测试夹具）。
"""

import os
import sys
from pathlib import Path

import pytest

# 把项目根目录加入Python路径，确保测试能正确导入项目代码
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


@pytest.fixture
def project_root() -> Path:
    """
    返回项目根目录的路径。

    用法（在测试函数中）：
        def test_something(project_root):
            config_file = project_root / "conf.default.yaml"
    """
    return PROJECT_ROOT


@pytest.fixture
def sample_config_path(project_root: Path) -> Path:
    """
    返回默认配置文件的路径。

    用法（在测试函数中）：
        def test_config(sample_config_path):
            assert sample_config_path.exists()
    """
    return project_root / "conf.default.yaml"
