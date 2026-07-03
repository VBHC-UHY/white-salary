"""
测试对话冲突检测 conflict_detector.py。

覆盖：修正/补充/打断 三类检测、优先级、无冲突、过短、should_regenerate 标志。
（撤回类 RETRACTION 已废弃为空模式，不再走文本匹配。）
"""

from white_salary.core.conflict_detector import ConflictDetector, ConflictType


class TestConflictDetector:
    def test_correction(self) -> None:
        r = ConflictDetector().check("不对不对，我说的是另一个")
        assert r.has_conflict is True
        assert r.conflict_type == ConflictType.CORRECTION
        assert r.should_regenerate is True
        assert r.hint  # 有给 LLM 的提示

    def test_supplement(self) -> None:
        r = ConflictDetector().check("对了还有一件事")
        assert r.conflict_type == ConflictType.SUPPLEMENT
        assert r.should_regenerate is True

    def test_interrupt_no_regenerate(self) -> None:
        r = ConflictDetector().check("等等，先别回")
        assert r.conflict_type == ConflictType.INTERRUPT
        assert r.should_regenerate is False

    def test_no_conflict(self) -> None:
        r = ConflictDetector().check("今天天气怎么样")
        assert r.has_conflict is False
        assert r.conflict_type == ConflictType.NONE
        assert r.should_regenerate is False

    def test_too_short(self) -> None:
        r = ConflictDetector().check("嗯")
        assert r.conflict_type == ConflictType.NONE

    def test_priority_interrupt_over_correction(self) -> None:
        """同时含打断与修正信号时，打断优先。"""
        r = ConflictDetector().check("等等，不对")
        assert r.conflict_type == ConflictType.INTERRUPT
