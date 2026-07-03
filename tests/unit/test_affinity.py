"""
测试好感度系统 affinity/manager.py。

覆盖：等级阈值、效率系数、家人豁免、关键词检测（含白名单防误报）、
软/硬遗忘判定、多用户隔离、上下文提示。

所有实例都用 tmp_path 作数据目录，互不干扰、也不污染仓库。
"""

import time

from white_salary.core.affinity.manager import AffinityManager, AffinityLevel


def _mgr(tmp_path) -> AffinityManager:
    return AffinityManager(data_dir=str(tmp_path))


class TestLevelThresholds:
    """分数 → 等级 的边界判定。"""

    def test_level_boundaries(self, tmp_path) -> None:
        m = _mgr(tmp_path)
        cases = [
            (0, AffinityLevel.STRANGER),
            (15, AffinityLevel.ACQUAINTANCE),
            (40, AffinityLevel.FRIEND),
            (80, AffinityLevel.GOOD_FRIEND),
            (150, AffinityLevel.CLOSE_FRIEND),
            (300, AffinityLevel.BEST_FRIEND),
            (-5, AffinityLevel.COLD),
            (-20, AffinityLevel.UNFAVORABLE),
            (-50, AffinityLevel.DISLIKE),
            (-100, AffinityLevel.HOSTILE),
            (-200, AffinityLevel.HATRED),
        ]
        for pts, expected in cases:
            m.set_points(pts)
            assert m.get_stats()["level_value"] == expected.value, f"{pts}分应为{expected.name}"

    def test_just_below_boundary(self, tmp_path) -> None:
        """边界下一点点属于低一级。"""
        m = _mgr(tmp_path)
        m.set_points(39)
        assert m.get_stats()["level_name"] == "认识"  # 39 < 40，还是认识
        m.set_points(79)
        assert m.get_stats()["level_name"] == "朋友"   # 79 < 80


class TestAddPoints:
    """加减分与等级效率系数。"""

    def test_positive_at_stranger_full_efficiency(self, tmp_path) -> None:
        m = _mgr(tmp_path)  # 0分=陌生人，效率1.0
        actual = m.add_points(10, "测试")
        assert actual == 10.0
        assert m.get_stats()["points"] == 10.0

    def test_positive_at_good_friend_reduced_efficiency(self, tmp_path) -> None:
        m = _mgr(tmp_path)
        m.set_points(100)  # 好朋友，效率0.55
        actual = m.add_points(10, "测试")
        assert actual == 5.5
        assert m.get_stats()["points"] == 105.5

    def test_negative_is_direct(self, tmp_path) -> None:
        """负分不打折，直接生效。"""
        m = _mgr(tmp_path)
        actual = m.add_points(-8, "测试")
        assert actual == -8.0
        assert m.get_stats()["points"] == -8.0


class TestFamilyImmunity:
    """家人不受加减分/衰减/关键词影响。"""

    def test_set_family(self, tmp_path) -> None:
        m = _mgr(tmp_path)
        m.set_family(True)
        stats = m.get_stats()
        assert stats["is_family"] is True
        assert stats["level_value"] == AffinityLevel.FAMILY.value

    def test_add_points_noop_for_family(self, tmp_path) -> None:
        m = _mgr(tmp_path)
        m.set_family(True)
        assert m.add_points(10, "测试") == 0.0

    def test_process_message_noop_for_family(self, tmp_path) -> None:
        m = _mgr(tmp_path)
        m.set_family(True)
        assert m.process_message("你这个傻逼") == []


class TestKeywordDetection:
    """消息内容自动检测好感度变化。"""

    def test_positive_keyword(self, tmp_path) -> None:
        m = _mgr(tmp_path)
        triggered = m.process_message("你真聪明")
        assert "compliment" in triggered
        assert m.get_stats()["points"] > 0

    def test_negative_keyword(self, tmp_path) -> None:
        m = _mgr(tmp_path)
        triggered = m.process_message("你是垃圾")
        assert "insult" in triggered
        assert m.get_stats()["points"] < 0

    def test_whitelist_blocks_false_positive(self, tmp_path) -> None:
        """白名单短语命中时，跳过负面检测（"垃圾桶"含负面词"垃圾"但不该扣分）。"""
        m = _mgr(tmp_path)
        triggered = m.process_message("帮我把垃圾桶拿过来")
        assert "insult" not in triggered
        assert m.get_stats()["points"] == 0.0


class TestForget:
    """软/硬遗忘判定（基于最后互动时间）。"""

    def test_soft_forget_window(self, tmp_path) -> None:
        m = _mgr(tmp_path)
        m._affinity.last_interaction = time.time() - 15 * 86400  # 15天前
        assert m.should_soft_forget() is True
        assert m.should_hard_forget() is False

    def test_hard_forget(self, tmp_path) -> None:
        m = _mgr(tmp_path)
        m._affinity.last_interaction = time.time() - 22 * 86400  # 22天前
        assert m.should_hard_forget() is True
        assert m.should_soft_forget() is False

    def test_recent_no_forget(self, tmp_path) -> None:
        m = _mgr(tmp_path)
        m._affinity.last_interaction = time.time() - 1 * 86400  # 1天前
        assert m.should_soft_forget() is False
        assert m.should_hard_forget() is False

    def test_family_never_forgets(self, tmp_path) -> None:
        m = _mgr(tmp_path)
        m.set_family(True)
        m._affinity.last_interaction = time.time() - 100 * 86400
        assert m.should_soft_forget() is False
        assert m.should_hard_forget() is False


class TestMultiUser:
    """get_for_user 每个用户独立。"""

    def test_users_are_isolated(self, tmp_path) -> None:
        AffinityManager._multi_user_cache.clear()
        try:
            alpha = AffinityManager.get_for_user("u_alpha", data_dir=str(tmp_path))
            alpha.set_points(50)
            beta = AffinityManager.get_for_user("u_beta", data_dir=str(tmp_path))

            assert alpha is not beta
            assert alpha.get_stats()["points"] == 50.0
            assert beta.get_stats()["points"] == 0.0
            # 同一 user_id 再取应是同一实例（缓存）
            assert AffinityManager.get_for_user("u_alpha", data_dir=str(tmp_path)) is alpha
        finally:
            AffinityManager._multi_user_cache.clear()


class TestContextHint:
    """注入提示词随等级变化。"""

    def test_high_level_intimate(self, tmp_path) -> None:
        m = _mgr(tmp_path)
        m.set_points(200)  # 挚友
        hint = m.get_context_hint()
        assert "挚友" in hint
        assert ("亲密" in hint or "撒娇" in hint)

    def test_stranger_polite(self, tmp_path) -> None:
        m = _mgr(tmp_path)  # 0分=陌生人
        hint = m.get_context_hint()
        assert "陌生人" in hint
        assert "礼貌" in hint
