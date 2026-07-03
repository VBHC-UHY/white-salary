"""
测试回复去重 response_dedup.py。

覆盖：完全重复、指纹归一化（忽略标点）、不同内容不判重、
过短文本放行、时间窗过期、清空、相似度函数。
"""

import time

from white_salary.core.response_dedup import ResponseDeduplicator


class TestResponseDeduplicator:
    def test_exact_repeat_is_duplicate(self) -> None:
        d = ResponseDeduplicator()
        assert d.check_and_record("你好啊今天天气真不错") is False  # 首次：记录，不判重
        assert d.is_duplicate("你好啊今天天气真不错") is True       # 再来一次：判重

    def test_different_text_not_duplicate(self) -> None:
        d = ResponseDeduplicator()
        d.record("今天天气真好我们一起去公园散步吧")
        assert d.is_duplicate("我想吃西瓜还有冰淇淋和蛋糕") is False

    def test_fingerprint_ignores_punctuation(self) -> None:
        """去掉标点后指纹相同 → 判重。"""
        d = ResponseDeduplicator()
        d.record("你好，今天天气不错！")
        assert d.is_duplicate("你好今天天气不错") is True

    def test_short_text_never_duplicate(self) -> None:
        d = ResponseDeduplicator()
        d.record("嗯")
        assert d.is_duplicate("嗯") is False  # 长度<3直接放行

    def test_clear(self) -> None:
        d = ResponseDeduplicator()
        d.record("一段足够长的回复内容用于测试")
        d.clear()
        assert d.is_duplicate("一段足够长的回复内容用于测试") is False

    def test_time_window_expired(self) -> None:
        """超出时间窗的历史不参与判重。"""
        d = ResponseDeduplicator(time_window=10)
        d.record("一段足够长的回复内容用于测试时间窗")
        # 手动把这条历史时间戳改成很久以前，模拟超出时间窗
        fp, _ = d._history[-1]
        d._history[-1] = (fp, time.time() - 10000)
        assert d.is_duplicate("一段足够长的回复内容用于测试时间窗") is False

    def test_similarity_function(self) -> None:
        assert ResponseDeduplicator._similarity("abcabc", "abcabc") == 1.0
        assert ResponseDeduplicator._similarity("aaaa", "bbbb") == 0.0
