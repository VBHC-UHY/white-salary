"""
white_salary/core/rest_system.py

休息系统 — AI也需要休息，不然一直秒回显得假。

借鉴v2的rest/manager.py和rest/detector.py：
  - v2用关键词检测AI回复中的"我要休息"意图，我们也用
  - v2有sleep/angry/tired三种休息类型，我们保留
  - v2没有最大时长限制，我们加上（最长8小时）
  - v2的状态持久化用JSON，我们也用

功能：
  - 检测AI回复中的休息意图（"我去睡觉了"、"哼不理你了"、"好累想休息"）
  - 自动进入休息模式，期间不主动聊天
  - 用户发消息可以唤醒
  - 休息到期自动恢复
"""

import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass
class RestSession:
    """一次休息会话。"""
    rest_type: str = "sleep"       # sleep / angry / tired
    reason: str = ""               # 原因
    mood: str = "neutral"          # 休息时的心情
    start_time: float = 0.0
    duration_minutes: int = 30     # 预计时长
    end_time: float = 0.0         # 预计结束时间

    @property
    def is_expired(self) -> bool:
        return time.time() > self.end_time if self.end_time > 0 else False

    @property
    def remaining_minutes(self) -> int:
        r = (self.end_time - time.time()) / 60
        return max(0, int(r))


# 休息意图关键词
_SLEEP_KEYWORDS = ["睡觉", "睡了", "晚安", "困了", "去睡", "要睡", "休息了"]
_ANGRY_KEYWORDS = ["不理你", "哼", "生气", "不想说话", "别跟我说话", "走开"]
_TIRED_KEYWORDS = ["好累", "累了", "休息一下", "歇一会", "太累了", "想休息"]

# 时长推测
_DURATION_PATTERNS = [
    (re.compile(r"(\d+)\s*分钟"), lambda m: int(m.group(1))),
    (re.compile(r"(\d+)\s*小时"), lambda m: int(m.group(1)) * 60),
    (re.compile(r"一会[儿]?"), lambda m: 15),
    (re.compile(r"晚安"), lambda m: 480),
]

MAX_REST_MINUTES = 480  # 最长8小时


class RestSystem:
    """
    AI休息系统。

    使用方式:
        rest = RestSystem()
        # AI回复后检测休息意图
        rest.check_ai_reply("我好累，想休息一下...")
        # 检查是否在休息
        if rest.is_resting:
            print(f"休息中，还剩{rest.current_session.remaining_minutes}分钟")
        # 用户发消息唤醒
        rest.wake_up()
    """

    def __init__(self, data_dir: str = "data") -> None:
        self._data_path = Path(data_dir) / "rest_state.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._session: Optional[RestSession] = None
        self._load()

    @property
    def is_resting(self) -> bool:
        """是否在休息中。"""
        if self._session is None:
            return False
        if self._session.is_expired:
            self._end_rest("到点了")
            return False
        return True

    @property
    def current_session(self) -> Optional[RestSession]:
        if self.is_resting:
            return self._session
        return None

    def check_ai_reply(self, ai_reply: str) -> bool:
        """
        检测AI回复中的休息意图。

        Args:
            ai_reply: AI的回复文本

        Returns:
            是否进入了休息模式
        """
        if self.is_resting:
            return False

        text = ai_reply.lower()

        # 检测休息类型
        rest_type = None
        keywords_matched = ""

        for kw in _SLEEP_KEYWORDS:
            if kw in text:
                rest_type = "sleep"
                keywords_matched = kw
                break

        if not rest_type:
            for kw in _ANGRY_KEYWORDS:
                if kw in text:
                    rest_type = "angry"
                    keywords_matched = kw
                    break

        if not rest_type:
            for kw in _TIRED_KEYWORDS:
                if kw in text:
                    rest_type = "tired"
                    keywords_matched = kw
                    break

        if not rest_type:
            return False

        # 必须是AI自己说的（包含"我"或第一人称）
        if not re.search(r"(我|人家|本人|白)", text):
            return False

        # 推测时长
        duration = self._estimate_duration(text, rest_type)

        self._start_rest(rest_type, keywords_matched, duration)
        return True

    def wake_up(self) -> Optional[str]:
        """
        用户发消息唤醒。

        Returns:
            唤醒提示（如"揉揉眼睛，被你吵醒了"），或None（没在休息）
        """
        if not self.is_resting:
            return None

        session = self._session
        self._end_rest("被用户唤醒")

        if session.rest_type == "sleep":
            return "（揉揉眼睛）唔...被你吵醒了...不过没关系啦"
        elif session.rest_type == "angry":
            return "（瞥了你一眼）...哼，说吧，什么事"
        else:
            return "（伸了个懒腰）嗯...休息了一下，好点了"

    def _start_rest(self, rest_type: str, reason: str, duration: int) -> None:
        now = time.time()
        self._session = RestSession(
            rest_type=rest_type,
            reason=reason,
            start_time=now,
            duration_minutes=duration,
            end_time=now + duration * 60,
        )
        self._save()
        logger.info(
            f"[Rest] 进入休息: {rest_type} ({duration}分钟) 原因: {reason}"
        )

    def _end_rest(self, reason: str) -> None:
        if self._session:
            logger.info(f"[Rest] 结束休息: {reason}")
        self._session = None
        self._save()

    def _estimate_duration(self, text: str, rest_type: str) -> int:
        """推测休息时长。"""
        for pattern, extractor in _DURATION_PATTERNS:
            match = pattern.search(text)
            if match:
                return min(extractor(match), MAX_REST_MINUTES)

        # 默认时长
        defaults = {"sleep": 480, "angry": 60, "tired": 30}
        return defaults.get(rest_type, 30)

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text(encoding="utf-8"))
                if data:
                    self._session = RestSession(**data)
                    if self._session.is_expired:
                        self._session = None
            except Exception:
                pass

    def _save(self) -> None:
        try:
            if self._session:
                self._data_path.write_text(
                    json.dumps(asdict(self._session)), encoding="utf-8"
                )
            else:
                self._data_path.write_text("{}", encoding="utf-8")
        except Exception:
            pass
