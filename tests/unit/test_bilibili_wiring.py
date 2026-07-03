"""
B站直播监听装配（2026-07-03 功能大项 批11二波）的单元测试。

覆盖四块（依据批11二波 bilibili 子任务）：
  1. BilibiliConfig 解析与默认值（默认关闭、room_id=0、不发弹幕、无关键词），
     且能被 load_config 正确解析、挂在 AppConfig.bilibili 上；
  2. 装配决策纯函数 _should_start_bilibili（enabled + room_id 共同决定是否启动）；
  3. 弹幕触发判定 should_reply_to_danmaku + 同用户冷却 DanmakuCooldown（防刷屏）；
  4. bilibili-api 缺失时 connect() 安全跳过（mock import 失败，不崩不抛）+
     凭证读取 read_bili_credential（文件不存在/格式正确/无sessdata）。

全部离线，不实际连B站、不发网络请求。
"""

import builtins
from pathlib import Path

import pytest

from white_salary.adapters.platform.bilibili_live import (
    BilibiliLiveAdapter,
    DanmakuCooldown,
    read_bili_credential,
    should_reply_to_danmaku,
)
from white_salary.infrastructure.config.models import AppConfig, BilibiliConfig


# =============================================================================
# 1. BilibiliConfig 解析与默认值
# =============================================================================

class TestBilibiliConfig:
    """BilibiliConfig 默认值与 AppConfig 挂载测试。"""

    def test_defaults_are_disabled_and_safe(self) -> None:
        """默认必须是关闭 + 安全值（不影响现有用户）。"""
        conf = BilibiliConfig()
        assert conf.enabled is False
        assert conf.room_id == 0
        assert conf.reply_danmaku is False
        assert conf.trigger_keywords == []

    def test_appconfig_has_bilibili_section(self) -> None:
        """AppConfig 必须有 bilibili 字段且是 BilibiliConfig 实例。"""
        app = AppConfig()
        assert isinstance(app.bilibili, BilibiliConfig)
        assert app.bilibili.enabled is False

    def test_parses_user_values(self) -> None:
        """用户填的值能被正确解析。"""
        conf = BilibiliConfig(
            enabled=True,
            room_id=123456,
            reply_danmaku=True,
            trigger_keywords=["白", "小白"],
        )
        assert conf.enabled is True
        assert conf.room_id == 123456
        assert conf.reply_danmaku is True
        assert conf.trigger_keywords == ["白", "小白"]

    def test_room_id_rejects_negative(self) -> None:
        """room_id 有 ge=0 约束，负数应报错。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            BilibiliConfig(room_id=-1)

    def test_load_config_merges_bilibili(self, tmp_path: Path) -> None:
        """load_config 深合并后 bilibili 节生效（不被 Pydantic 静默丢弃）。"""
        from white_salary.infrastructure.config.loader import load_config

        (tmp_path / "conf.default.yaml").write_text(
            "system:\n  name: \"White Salary\"\n"
            "bilibili:\n"
            "  enabled: true\n"
            "  room_id: 777\n"
            "  reply_danmaku: true\n"
            "  trigger_keywords:\n"
            "    - \"白\"\n",
            encoding="utf-8",
        )
        cfg = load_config(project_root=tmp_path)
        assert cfg.bilibili.enabled is True
        assert cfg.bilibili.room_id == 777
        assert cfg.bilibili.reply_danmaku is True
        assert cfg.bilibili.trigger_keywords == ["白"]


# =============================================================================
# 2. 装配决策纯函数 _should_start_bilibili
# =============================================================================

class TestShouldStartBilibili:
    """run_server._should_start_bilibili 装配决策测试。"""

    def _make_config(
        self, enabled: bool, room_id: int
    ) -> AppConfig:
        app = AppConfig()
        app.bilibili = BilibiliConfig(enabled=enabled, room_id=room_id)
        return app

    def test_disabled_does_not_start(self) -> None:
        """enabled=false 一律不启动（即使填了 room_id）。"""
        from run_server import _should_start_bilibili

        assert _should_start_bilibili(self._make_config(False, 123)) is False

    def test_enabled_without_room_does_not_start(self) -> None:
        """enabled=true 但 room_id=0（未填）不启动。"""
        from run_server import _should_start_bilibili

        assert _should_start_bilibili(self._make_config(True, 0)) is False

    def test_enabled_with_room_starts(self) -> None:
        """enabled=true 且 room_id>0 才启动。"""
        from run_server import _should_start_bilibili

        assert _should_start_bilibili(self._make_config(True, 123)) is True

    def test_bad_config_is_safe(self) -> None:
        """配置对象缺 bilibili 属性时保守不启动，不抛异常。"""
        from run_server import _should_start_bilibili

        class _Empty:
            pass

        assert _should_start_bilibili(_Empty()) is False


# =============================================================================
# 3. 弹幕触发判定 + 同用户冷却
# =============================================================================

class TestShouldReplyToDanmaku:
    """should_reply_to_danmaku 触发判定测试。"""

    def test_reply_when_bot_name_mentioned(self) -> None:
        """弹幕提到机器人名字就回复。"""
        assert should_reply_to_danmaku("白你好呀", "白", []) is True

    def test_reply_when_at_bot(self) -> None:
        """@机器人（名字含在文本里）就回复。"""
        assert should_reply_to_danmaku("@白 在吗", "白", []) is True

    def test_skip_unrelated_danmaku(self) -> None:
        """无关弹幕（防刷屏关键）不回复。"""
        assert should_reply_to_danmaku("6666", "白", []) is False
        assert should_reply_to_danmaku("主播好帅", "白", []) is False

    def test_reply_on_keyword_hit(self) -> None:
        """配置了触发关键词，命中任一就回复。"""
        assert should_reply_to_danmaku("这游戏叫什么", "白", ["游戏", "音乐"]) is True

    def test_skip_when_keyword_not_hit(self) -> None:
        """有关键词但都没命中、也没提名字，不回复。"""
        assert should_reply_to_danmaku("今天天气不错", "白", ["游戏"]) is False

    def test_empty_text_skipped(self) -> None:
        """空弹幕/纯空白不回复。"""
        assert should_reply_to_danmaku("", "白", ["白"]) is False
        assert should_reply_to_danmaku("   ", "白", []) is False


class TestDanmakuCooldown:
    """DanmakuCooldown 同用户冷却测试（防刷屏被封）。"""

    def test_first_reply_allowed(self) -> None:
        """首次回复某用户放行。"""
        cd = DanmakuCooldown(cooldown_seconds=30.0)
        assert cd.allow("userA", now=1000.0) is True

    def test_within_cooldown_blocked(self) -> None:
        """冷却期内同一用户被拦截。"""
        cd = DanmakuCooldown(cooldown_seconds=30.0)
        cd.allow("userA", now=1000.0)
        assert cd.allow("userA", now=1020.0) is False  # 才过20秒

    def test_after_cooldown_allowed(self) -> None:
        """冷却期满后再次放行。"""
        cd = DanmakuCooldown(cooldown_seconds=30.0)
        cd.allow("userA", now=1000.0)
        assert cd.allow("userA", now=1031.0) is True  # 过了31秒

    def test_different_users_independent(self) -> None:
        """不同用户的冷却互不影响。"""
        cd = DanmakuCooldown(cooldown_seconds=30.0)
        assert cd.allow("userA", now=1000.0) is True
        assert cd.allow("userB", now=1001.0) is True  # 另一个人不受A的冷却影响


# =============================================================================
# 4. 凭证读取 + bilibili-api 缺失时安全跳过
# =============================================================================

class TestReadBiliCredential:
    """read_bili_credential 凭证读取测试。"""

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """文件不存在返回 None（=未登录，只读不发）。"""
        assert read_bili_credential(str(tmp_path / "no.ini")) is None

    def test_reads_valid_ini(self, tmp_path: Path) -> None:
        """正确格式的 bili.ini（与 bili_qr_login 写入格式一致）能读出凭证。"""
        ini = tmp_path / "bili.ini"
        ini.write_text(
            "[bili]\n"
            "sessdata = abc123\n"
            "bili_jct = jct456\n"
            "buvid3 = buv789\n"
            "dedeuserid = 10001\n"
            "ac_time_value = \n",
            encoding="utf-8",
        )
        cred = read_bili_credential(str(ini))
        assert cred is not None
        assert cred["sessdata"] == "abc123"
        assert cred["bili_jct"] == "jct456"
        assert cred["buvid3"] == "buv789"
        assert cred["dedeuserid"] == "10001"

    def test_empty_sessdata_returns_none(self, tmp_path: Path) -> None:
        """sessdata 为空视为未登录，返回 None。"""
        ini = tmp_path / "bili.ini"
        ini.write_text(
            "[bili]\nsessdata = \nbili_jct = jct456\n",
            encoding="utf-8",
        )
        assert read_bili_credential(str(ini)) is None


class TestAdapterGracefulDegrade:
    """bilibili-api 未安装时 connect() 安全跳过测试。"""

    async def test_connect_skips_when_lib_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        mock bilibili_api import 失败——connect() 必须优雅返回（不抛异常、不崩溃），
        这样 run_server 装配段即使没装库也不会拖垮主程序。
        """
        real_import = builtins.__import__

        def _fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "bilibili_api" or name.startswith("bilibili_api."):
                raise ImportError("mocked: bilibili-api-python not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        adapter = BilibiliLiveAdapter(room_id=123, credential=None)
        # 不应抛异常；库缺失时直接返回
        await adapter.connect()
        assert adapter._running is False  # 没设为 True（提前 return 了）

    async def test_disconnect_is_safe(self) -> None:
        """disconnect() 不依赖任何外部库，永远安全。"""
        adapter = BilibiliLiveAdapter(room_id=123)
        adapter._running = True
        await adapter.disconnect()
        assert adapter._running is False
