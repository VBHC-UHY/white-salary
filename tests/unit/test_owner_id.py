"""
_resolve_owner_id（跨平台身份统一）的单元测试。

覆盖：正常取 qq.family_qq 第一个号、int/str 两种写法、family_qq 为空、
无 qq 段、配置文件不存在、YAML 损坏等回退路径。

（2026-07-02 审计修复批2新增：桌面端 user_id 不再硬编码"desktop"，
改为解析 conf.yaml 的主人 QQ 号，与 QQ 端共用好感度/画像。）
"""

from pathlib import Path

from white_salary.infrastructure.server.websocket_handler import _resolve_owner_id


def _write_conf(tmp_path: Path, content: str) -> Path:
    """把给定内容写成临时 conf.yaml，返回路径。"""
    conf = tmp_path / "conf.yaml"
    conf.write_text(content, encoding="utf-8")
    return conf


def test_resolve_first_family_qq_int(tmp_path: Path):
    """family_qq 是 int 列表时，应取第一个并转成字符串。"""
    conf = _write_conf(
        tmp_path,
        "qq:\n"
        "  enabled: true\n"
        "  family_qq:\n"
        "    - 1234567890\n"
        "    - 1234567890\n",
    )
    assert _resolve_owner_id(conf) == "1234567890"


def test_resolve_first_family_qq_str(tmp_path: Path):
    """family_qq 写成字符串也应正常返回。"""
    conf = _write_conf(
        tmp_path,
        "qq:\n"
        "  family_qq:\n"
        '    - "10086"\n',
    )
    assert _resolve_owner_id(conf) == "10086"


def test_resolve_empty_family_qq_falls_back(tmp_path: Path):
    """family_qq 为空列表时应回退 desktop。"""
    conf = _write_conf(tmp_path, "qq:\n  enabled: true\n  family_qq: []\n")
    assert _resolve_owner_id(conf) == "desktop"


def test_resolve_family_qq_null_falls_back(tmp_path: Path):
    """family_qq 显式为 null 时应回退 desktop（不抛异常）。"""
    conf = _write_conf(tmp_path, "qq:\n  family_qq:\n")
    assert _resolve_owner_id(conf) == "desktop"


def test_resolve_no_qq_section_falls_back(tmp_path: Path):
    """配置里没有 qq 段时应回退 desktop。"""
    conf = _write_conf(tmp_path, "server:\n  port: 12400\n")
    assert _resolve_owner_id(conf) == "desktop"


def test_resolve_missing_file_falls_back(tmp_path: Path):
    """指定的配置文件不存在时应回退 desktop。"""
    assert _resolve_owner_id(tmp_path / "no_such_conf.yaml") == "desktop"


def test_resolve_broken_yaml_falls_back(tmp_path: Path):
    """YAML 语法损坏时应回退 desktop（异常被捕获并告警，不上抛）。"""
    conf = _write_conf(tmp_path, "qq: [unclosed\n  family_qq: {{bad\n")
    assert _resolve_owner_id(conf) == "desktop"


def test_resolve_empty_file_falls_back(tmp_path: Path):
    """空配置文件（safe_load 返回 None）应回退 desktop。"""
    conf = _write_conf(tmp_path, "")
    assert _resolve_owner_id(conf) == "desktop"


def test_resolve_real_project_conf():
    """项目真实 conf.yaml 应能解析出非空的统一 id（存在 qq 配置的前提下）。"""
    project_conf = Path(__file__).resolve().parents[2] / "conf.yaml"
    if not project_conf.exists():
        # 环境里没有真实配置就只验证回退不抛异常
        assert _resolve_owner_id(project_conf) == "desktop"
        return
    owner = _resolve_owner_id(project_conf)
    assert isinstance(owner, str)
    assert owner  # 非空：要么是QQ号，要么回退desktop
