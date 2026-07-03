"""
测试记忆管理器 memory/manager.py 的正则/关键词提取器。

覆盖核心信息（名字/年龄/生日/地点）、喜好（like/dislike）、
长期记忆关键词触发、情感关键词（正面/负面/里程碑），以及无匹配情形。

直接断言各提取器返回的结果列表（如 "核心:user_name=小明"），不依赖底层存储格式。
用 tmp_path 作数据目录，互不干扰。
"""

import pytest

from white_salary.core.memory.manager import MemoryManager


@pytest.fixture
def mgr(tmp_path):
    # MemoryManager 只需 data_dir 即可构造（LLM 通道默认 None）
    return MemoryManager(data_dir=str(tmp_path))


class TestCoreInfoExtraction:
    def test_name(self, mgr) -> None:
        assert "核心:user_name=小明" in mgr._extract_core_info("我叫小明")

    def test_age(self, mgr) -> None:
        assert "核心:user_age=21" in mgr._extract_core_info("我今年21岁")

    def test_birthday(self, mgr) -> None:
        assert "核心:user_birthday=6月18日" in mgr._extract_core_info("我生日是6月18日")

    def test_location(self, mgr) -> None:
        assert "核心:user_location=北京" in mgr._extract_core_info("我住在北京")

    def test_no_match(self, mgr) -> None:
        assert mgr._extract_core_info("今天天气真好啊") == []


class TestPreferenceExtraction:
    def test_like(self, mgr) -> None:
        results = mgr._extract_preferences("我喜欢吃水果蛋糕")
        assert any(r.startswith("偏好:like") for r in results)

    def test_dislike(self, mgr) -> None:
        results = mgr._extract_preferences("我讨厌下雨天气")
        assert any(r.startswith("偏好:dislike") for r in results)

    def test_no_preference(self, mgr) -> None:
        assert mgr._extract_preferences("我们一起出去走走") == []


class TestLongTermKeyword:
    def test_keyword_triggers(self, mgr) -> None:
        assert "长期:记住触发" in mgr._extract_long_term_by_keywords("记住我下周要考试", "")

    def test_no_keyword(self, mgr) -> None:
        assert mgr._extract_long_term_by_keywords("随便聊聊天气吧", "") == []


class TestEmotionExtraction:
    def test_positive_strong(self, mgr) -> None:
        results = mgr._extract_emotion_memory("我今天好开心啊")
        assert any(r.startswith("情感:positive_strong") for r in results)

    def test_negative_strong(self, mgr) -> None:
        results = mgr._extract_emotion_memory("我好难过想哭")
        assert any(r.startswith("情感:negative_strong") for r in results)

    def test_milestone(self, mgr) -> None:
        results = mgr._extract_emotion_memory("我考试成功了")
        assert any(r.startswith("情感:milestone") for r in results)

    def test_no_emotion(self, mgr) -> None:
        assert mgr._extract_emotion_memory("今天吃了午饭") == []
