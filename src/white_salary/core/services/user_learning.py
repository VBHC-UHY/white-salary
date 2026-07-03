"""
white_salary/core/services/user_learning.py

用户学习服务 — 自动分析每个用户的画像和偏好（多用户）。

借鉴v2的user_profile_analyzer.py和preference_learning.py：
  - 多用户：桌面端用户 + QQ上的每个好友/群友都是独立用户
  - 每个用户独立追踪消息计数、独立触发学习
  - LLM分析用户的兴趣、喜好、性格、沟通风格
  - 结果按user_id持久化

功能：
  - 每个用户每50条对话或7天自动触发一次学习
  - LLM分析用户特征
  - 结果存入独立的用户画像文件
  - 桌面端用户的画像额外注入核心记忆
"""

import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger


class _UserState:
    """单个用户的学习状态。"""
    def __init__(self) -> None:
        self.message_count: int = 0
        self.last_learn_time: float = 0.0
        self.recent_messages: list[str] = []
        self.user_name: str = ""

    def to_dict(self) -> dict:
        return {
            "message_count": self.message_count,
            "last_learn_time": self.last_learn_time,
            "recent_messages": self.recent_messages[-200:],
            "user_name": self.user_name,
        }

    @staticmethod
    def from_dict(d: dict) -> "_UserState":
        s = _UserState()
        s.message_count = d.get("message_count", 0)
        s.last_learn_time = d.get("last_learn_time", 0.0)
        s.recent_messages = d.get("recent_messages", [])
        s.user_name = d.get("user_name", "")
        return s


class UserLearningService:
    """
    多用户学习服务。

    使用方式:
        service = UserLearningService(memory_manager, llm)
        service.on_message("user_123", "小白", "用户消息")
        if service.should_learn("user_123"):
            await service.learn("user_123")
    """

    def __init__(
        self,
        memory_manager=None,
        learning_llm=None,
        data_dir: str = "data/memory",
        message_threshold: int = 50,
        day_threshold: int = 7,
    ) -> None:
        self._memory = memory_manager
        self._llm = learning_llm
        self._profiles_dir = Path(data_dir) / "user_profiles"
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        self._states_path = Path(data_dir) / "user_learning_states.json"

        self._msg_threshold = message_threshold
        self._day_threshold = day_threshold

        # 多用户状态 {user_id: _UserState}
        self._users: dict[str, _UserState] = {}
        # 多用户画像 {user_id: dict}
        self._profiles: dict[str, dict] = {}

        self._load_all()

    def on_message(self, user_id: str, user_name: str, user_msg: str) -> None:
        """每次对话后调用。自动检测并触发后台学习。"""
        if not user_id or not user_msg or len(user_msg) < 3:
            return

        state = self._get_state(user_id)
        state.message_count += 1
        state.user_name = user_name or state.user_name
        state.recent_messages.append(user_msg)
        if len(state.recent_messages) > 200:
            state.recent_messages = state.recent_messages[-200:]

        # 自动触发后台学习
        if self.should_learn(user_id):
            self._trigger_background_learn(user_id)

    def _trigger_background_learn(self, user_id: str) -> None:
        """后台异步触发学习（不阻塞对话）。"""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.learn(user_id))
            else:
                loop.run_until_complete(self.learn(user_id))
        except RuntimeError:
            pass

    def should_learn(self, user_id: str) -> bool:
        """检查某个用户是否需要触发学习。"""
        if not self._llm:
            return False

        state = self._get_state(user_id)

        if state.message_count >= self._msg_threshold:
            return True

        if state.last_learn_time > 0:
            days = (time.time() - state.last_learn_time) / 86400
            if days >= self._day_threshold and len(state.recent_messages) >= 10:
                return True

        if state.last_learn_time == 0 and len(state.recent_messages) >= 20:
            return True

        return False

    async def learn(self, user_id: str) -> Optional[dict]:
        """执行一次用户学习。"""
        state = self._get_state(user_id)
        if not self._llm or len(state.recent_messages) < 10:
            return None

        name = state.user_name or user_id
        logger.info(f"[UserLearn] 分析用户 {name} ({len(state.recent_messages)} 条消息)...")

        try:
            sample = state.recent_messages[-50:]
            messages_text = "\n".join(f"- {m}" for m in sample)

            from white_salary.core.interfaces.types import Message, MessageRole
            prompt_messages = [
                Message(
                    role=MessageRole.SYSTEM,
                    content=(
                        "你是一个用户画像分析专家。根据用户的聊天记录，分析这个人的特征。\n"
                        "请返回JSON格式，包含以下字段：\n"
                        "- interests: 兴趣爱好列表\n"
                        "- likes: 喜欢的事物（附带为什么喜欢的原因）\n"
                        "- dislikes: 不喜欢的事物（附带原因）\n"
                        "- deep_preferences: 深层偏好（如: '喜欢Minecraft因为自由度高'）\n"
                        "- personality: 性格特点\n"
                        "- communication_style: 沟通风格\n"
                        "- topics: 经常聊的话题\n"
                        "- mood_triggers: {happy: [让他开心的事], unhappy: [让他不开心的事]}\n"
                        "只返回JSON，不要其他文字。每个字段3-5条即可。"
                    ),
                ),
                Message(
                    role=MessageRole.USER,
                    content=f"用户昵称: {name}\n以下是该用户最近的聊天记录：\n{messages_text}",
                ),
            ]

            reply = await self._llm.chat_completion(prompt_messages, temperature=0.3, max_tokens=800)
            profile = self._parse_json(reply)

            if profile:
                profile["user_name"] = name
                profile["user_id"] = user_id
                profile["analyzed_at"] = time.strftime("%Y-%m-%d %H:%M")
                profile["message_count"] = len(state.recent_messages)

                # 跨源验证：与旧画像对比，保留一致的特征
                old_profile = self._profiles.get(user_id, {})
                if old_profile:
                    profile = self._cross_validate(old_profile, profile)

                self._profiles[user_id] = profile
                state.last_learn_time = time.time()
                state.message_count = 0
                self._save_all()
                self._save_profile(user_id, profile)

                # 桌面端用户额外注入核心记忆
                if user_id == "desktop":
                    await self._inject_to_core_memory(profile)

                logger.info(f"[UserLearn] {name} 学习完成")
                return profile

        except Exception as e:
            logger.error(f"[UserLearn] {name} 学习失败: {e}")

        return None

    def get_profile(self, user_id: str = "desktop") -> dict:
        """获取某个用户的画像。"""
        return self._profiles.get(user_id, {}).copy()

    def get_all_profiles(self) -> dict[str, dict]:
        """获取所有用户画像。"""
        return {uid: p.copy() for uid, p in self._profiles.items()}

    def get_profile_prompt(self, user_id: str = "desktop") -> str:
        """生成可注入对话的用户画像提示。"""
        profile = self._profiles.get(user_id, {})
        if not profile:
            return ""

        name = profile.get("user_name", "用户")
        parts = [f"[{name}的画像]"]
        if profile.get("interests"):
            parts.append(f"兴趣: {', '.join(profile['interests'][:5])}")
        if profile.get("likes"):
            parts.append(f"喜欢: {', '.join(profile['likes'][:5])}")
        if profile.get("dislikes"):
            parts.append(f"不喜欢: {', '.join(profile['dislikes'][:5])}")
        if profile.get("communication_style"):
            style = profile["communication_style"]
            if isinstance(style, list):
                style = ", ".join(style[:3])
            parts.append(f"沟通风格: {style}")

        return "\n".join(parts) if len(parts) > 1 else ""

    async def _inject_to_core_memory(self, profile: dict) -> None:
        """桌面端用户的画像注入核心记忆。"""
        if not self._memory or not hasattr(self._memory, '_core'):
            return
        core = self._memory._core
        try:
            if profile.get("interests"):
                core.set("user_interests", ", ".join(profile["interests"][:5]),
                         category="preference")
            if profile.get("likes"):
                core.set("user_likes", ", ".join(profile["likes"][:5]),
                         category="preference")
            if profile.get("dislikes"):
                core.set("user_dislikes", ", ".join(profile["dislikes"][:5]),
                         category="preference")
            if profile.get("personality"):
                core.set("user_personality", ", ".join(profile["personality"][:5]),
                         category="basic_info")
        except Exception as e:
            logger.debug(f"[UserLearn] 注入核心记忆失败: {e}")

    def _cross_validate(self, old: dict, new: dict) -> dict:
        """跨源验证：合并新旧画像，保留一致的，合并新增的。"""
        merged = dict(new)  # 新画像为基础

        for field in ("interests", "likes", "dislikes", "topics"):
            old_items = set(old.get(field, []))
            new_items = set(new.get(field, []))
            if old_items and new_items:
                # 保留两次都出现的（高置信）+ 新出现的
                confirmed = old_items & new_items
                fresh = new_items - old_items
                # 确认的排前面
                merged[field] = list(confirmed) + list(fresh)

        # personality和communication_style取新的（LLM最新分析更准）
        # deep_preferences合并去重
        old_deep = old.get("deep_preferences", [])
        new_deep = new.get("deep_preferences", [])
        if old_deep and new_deep:
            merged["deep_preferences"] = list(set(old_deep + new_deep))[:10]

        # 记录验证次数
        merged["validation_count"] = old.get("validation_count", 0) + 1

        return merged

    def _get_state(self, user_id: str) -> _UserState:
        if user_id not in self._users:
            self._users[user_id] = _UserState()
        return self._users[user_id]

    def _parse_json(self, text: str) -> Optional[dict]:
        import re
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return None

    def _load_all(self) -> None:
        # 加载状态
        if self._states_path.exists():
            try:
                data = json.loads(self._states_path.read_text(encoding="utf-8"))
                for uid, d in data.items():
                    self._users[uid] = _UserState.from_dict(d)
            except Exception:
                pass
        # 加载画像
        for f in self._profiles_dir.glob("*.json"):
            try:
                uid = f.stem
                self._profiles[uid] = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass

    def _save_all(self) -> None:
        try:
            data = {uid: s.to_dict() for uid, s in self._users.items()}
            self._states_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _save_profile(self, user_id: str, profile: dict) -> None:
        try:
            path = self._profiles_dir / f"{user_id}.json"
            path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
