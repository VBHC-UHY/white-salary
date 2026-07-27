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

# 单元测试里关闭外部工具自动探测。
#
# 探测会真的去扫本机磁盘找 GPT-SoVITS / ComfyUI 等安装目录。放任它在测试里跑会有
# 两个问题：一是结果随机器而变——"未配置时应回退到空"这类用例，在真装了这些工具
# 的开发机上会拿到一个真实路径而失败；二是每次扫盘都要几秒，白白拖慢整个套件。
#
# 探测本身由 tests/unit/test_tool_discovery.py 用临时目录单独验证（那里显式传
# search_roots，不碰真实磁盘）；端到端测试想验证真实探测时可自行取消该环境变量。
os.environ.setdefault("WS_DISABLE_TOOL_AUTODETECT", "1")


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
