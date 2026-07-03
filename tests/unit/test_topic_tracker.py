"""
测试话题追踪 topic_tracker.py。

覆盖：只追踪用户消息、重复达阈值才提示、不同话题不提示、
过短文本跳过、提示10分钟冷却、相似度函数。
"""

from white_salary.core.topic_tracker import TopicTracker


class TestTopicTracker:
    def test_only_tracks_user_messages(self) -> None:
        t = TopicTracker()
        for _ in range(4):
            t.record_message("白回复的内容不该被当成话题来追踪", source="assistant")
        assert t.get_hint() == ""

    def test_repeated_topic_triggers_hint(self) -> None:
        t = TopicTracker()
        for _ in range(4):  # 达到 REPEAT_THRESHOLD
            t.record_message("我想聊聊周末去爬山的事情", source="user")
        hint = t.get_hint()
        assert hint != ""
        assert "话题提示" in hint

    def test_distinct_topics_no_hint(self) -> None:
        t = TopicTracker()
        t.record_message("今天天气真好适合出去散步", source="user")
        t.record_message("我最近在学习弹钢琴好难啊", source="user")
        t.record_message("晚饭想吃火锅还是烧烤呢", source="user")
        assert t.get_hint() == ""

    def test_short_text_skipped(self) -> None:
        t = TopicTracker()
        for _ in range(5):
            t.record_message("嗯", source="user")  # normalize 后 <3 字，跳过
        assert t.get_hint() == ""

    def test_hint_cooldown(self) -> None:
        """提示后 10 分钟内不重复提示。"""
        t = TopicTracker()
        for _ in range(4):
            t.record_message("反复聊的同一个话题内容啊", source="user")
        assert t.get_hint() != ""       # 第一次给提示
        assert t.get_hint() == ""       # 冷却期内不再提示

    def test_similarity(self) -> None:
        t = TopicTracker()
        assert t._similar("爬山爬山爬山", "爬山爬山爬山") is True
        assert t._similar("aaaa", "bbbb") is False
