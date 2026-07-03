"""
测试 Bug2 修复：情感模块使用真实 user_id（不再写死 "desktop"）。

修复点：
  1. emotion_memory.EmotionMemoryModule 的 get_context_prompt/on_message
     现在接收并使用真实 user_id（QQ 多用户场景下区分每个人）。
  2. emotion_tracker.EmotionTracker.record_emotion 现在接收 user_id，
     并把它传给好感度情绪系数计算。
"""

import inspect

from white_salary.core.memory.emotion_memory import EmotionMemoryModule
from white_salary.core.memory import emotion_tracker as et_mod


class _StubImpl:
    """假的 EmotionMemoryStore：只记录被传入的 user_id，跳过真实读写。"""

    def __init__(self) -> None:
        self.impression_user_ids: list[str] = []
        self.interaction_user_ids: list[str] = []

    def get_impression_prompt(self, user_id: str) -> str:
        self.impression_user_ids.append(user_id)
        return f"[印象:{user_id}]"

    def on_interaction(self, user_id: str, name: str, msg: str) -> None:
        self.interaction_user_ids.append(user_id)


class TestEmotionMemoryUserId:
    """emotion_memory 模块按真实用户区分情感印象。"""

    def test_signature_accepts_user_id_and_is_group(self) -> None:
        """
        签名必须接收 user_id/is_group。

        否则 manager.get_modules_context 用关键字参数调用时会抛 TypeError，
        被回退到不带参数的旧调用 —— 那样又会退回写死的 "desktop"。
        """
        ctx_sig = inspect.signature(EmotionMemoryModule.get_context_prompt)
        assert "user_id" in ctx_sig.parameters
        assert "is_group" in ctx_sig.parameters

        msg_sig = inspect.signature(EmotionMemoryModule.on_message)
        assert "user_id" in msg_sig.parameters
        assert "is_group" in msg_sig.parameters

    def test_uses_real_user_id(self) -> None:
        """传入真实 user_id 时，底层用的是该 user_id，而不是写死的 "desktop"。"""
        mod = EmotionMemoryModule()
        stub = _StubImpl()
        mod._impl = stub  # 注入桩，绕开真实存储

        out = mod.get_context_prompt("你好", user_id="qq_12345", is_group=True)
        mod.on_message("你好", "回复", user_id="qq_12345", is_group=True)

        assert stub.impression_user_ids == ["qq_12345"]
        assert stub.interaction_user_ids == ["qq_12345"]
        assert "qq_12345" in out

    def test_defaults_to_desktop(self) -> None:
        """不传 user_id 时（桌面端）默认仍是 desktop，保持向后兼容。"""
        mod = EmotionMemoryModule()
        stub = _StubImpl()
        mod._impl = stub

        mod.get_context_prompt("hi")
        assert stub.impression_user_ids == ["desktop"]


class TestEmotionTrackerUserId:
    """emotion_tracker.record_emotion 把真实 user_id 传给好感度系数。"""

    def test_record_emotion_has_user_id_param(self) -> None:
        sig = inspect.signature(et_mod.EmotionTracker.record_emotion)
        assert "user_id" in sig.parameters

    def test_record_emotion_threads_user_id(self, tmp_path, monkeypatch) -> None:
        """record_emotion 应把 user_id 透传给 _get_affinity_emotion_multiplier。"""
        tracker = et_mod.EmotionTracker(data_dir=str(tmp_path))

        captured: dict[str, str] = {}

        def _fake_mult(user_id: str = "desktop") -> float:
            captured["user_id"] = user_id
            return 1.0

        monkeypatch.setattr(
            et_mod.EmotionTracker,
            "_get_affinity_emotion_multiplier",
            staticmethod(_fake_mult),
        )

        tracker.record_emotion("happy", intensity=0.6, user_id="qq_999")
        assert captured["user_id"] == "qq_999"
