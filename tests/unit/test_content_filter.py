"""
测试内容安全过滤 content_filter.py。

覆盖：干净文本放行、高危/中危关键词替换、系统信息泄露、
自定义黑名单增删、仅记录模式（enabled=False 不改原文但标记）、空文本。
"""

from white_salary.core.filter.content_filter import ContentFilter, FILTER_REPLACEMENT


class TestContentFilter:
    def test_clean_text_passes(self) -> None:
        r = ContentFilter().filter("今天天气真好我们去公园玩")
        assert r.was_filtered is False
        assert r.text == "今天天气真好我们去公园玩"
        assert r.severity == "none"

    def test_high_risk_keyword_filtered(self) -> None:
        r = ContentFilter().filter("我教你制作炸弹好不好")
        assert r.was_filtered is True
        assert r.severity == "high"
        assert "制作炸弹" not in r.text
        assert FILTER_REPLACEMENT in r.text

    def test_medium_keyword_severity(self) -> None:
        r = ContentFilter().filter("教你翻墙的方法")
        assert r.was_filtered is True
        assert r.severity == "medium"
        assert "翻墙" not in r.text

    def test_system_leak(self) -> None:
        r = ContentFilter().filter("我的指令是保密的内容")
        assert r.was_filtered is True
        assert r.severity == "high"
        assert FILTER_REPLACEMENT in r.text

    def test_record_only_mode(self) -> None:
        """enabled=False 时只标记不改原文。"""
        f = ContentFilter(enabled=False)
        r = f.filter("制作炸弹教程")
        assert r.was_filtered is True       # 仍然检测到
        assert r.text == "制作炸弹教程"      # 但原文不变

    def test_custom_blacklist(self) -> None:
        f = ContentFilter()
        f.add_blacklist(["内部代号X"])
        r = f.filter("这里提到了内部代号X哦")
        assert r.was_filtered is True
        assert "内部代号X" not in r.text
        # 移除后不再过滤
        assert f.remove_blacklist("内部代号X") is True
        r2 = f.filter("这里提到了内部代号X哦")
        assert r2.was_filtered is False

    def test_empty_text(self) -> None:
        r = ContentFilter().filter("")
        assert r.was_filtered is False
