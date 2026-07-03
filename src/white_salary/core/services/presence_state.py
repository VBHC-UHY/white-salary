"""
white_salary/core/services/presence_state.py

在场状态 — 忙碌/静默模式（白真的闭嘴）。

2026-07-03 工具实现（批9）：批2下架的7个「假成功」社交工具（set_busy_mode/
clear_busy_mode/global_silent/switch_filter_mode/check_filter_mode/filter_toggle/
silent_toggle）只回成功文案不写任何状态。本模块是真实现的状态核心：
  - 状态机：normal(正常) / busy(忙碌，到期自动恢复) / silent(静默，直到手动解除)
  - 持久化 data/presence.json（重启不丢状态）
  - 进程级单例（qq_handler 线程 / 桌面主循环 / 工具层共用同一份状态）
  - decide_qq_reply：QQ消息的回复决策（纯内存判断，不拖慢消息处理）

为什么不并入 core/rest_system.RestSystem（读完其代码后的决策）：
  RestSystem 是「白自己累了/生气了要休息」的自主行为状态机——由AI回复关键词
  触发进入、【用户发消息即唤醒】（wake_up）；静默模式正相反——由主人命令进入、
  用户消息【不得】解除（否则设了等于没设）。两个状态机的解除语义互斥，
  并入会导致 RestSystem 的唤醒逻辑把主人下的静默命令冲掉，故独立建模。
"""

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger


# 三种在场状态
MODE_NORMAL = "normal"
MODE_BUSY = "busy"
MODE_SILENT = "silent"

# 主人私聊里出现这些词仍走正常回复（急事必须回）
_URGENT_KEYWORDS = ("紧急", "急事", "很急", "在吗", "在不在", "救命", "快回", "出事", "重要")
# 主人私聊里出现这些词也放行——否则主人在QQ上没法把静默关掉（设了就解不开）
_CLEAR_INTENT_KEYWORDS = ("忙完", "不忙了", "解除", "恢复", "可以说话", "说话吧", "出来吧", "回来了")

# 忙碌模式默认时长（分钟）
DEFAULT_BUSY_MINUTES = 60
# 被@/私聊的「我在忙」简短告知限频间隔（每用户）
NOTICE_INTERVAL_SECONDS = 30 * 60


@dataclass
class QuietReplyDecision:
    """QQ消息在忙碌/静默模式下的回复决策。"""
    action: str            # reply_normal=正常回复 / skip=闭嘴 / brief_notice=简短告知
    notice_text: str = ""  # action=brief_notice 时要回的那句话


class PresenceState:
    """
    忙碌/静默状态（进程级单例）。

    使用方式:
        state = PresenceState.get_instance()
        state.set_quiet(MODE_BUSY, duration_minutes=60)   # 工具层设置
        if state.is_quiet: ...                            # 管线检查
        decision = state.decide_qq_reply(...)             # QQ回复决策
    """

    _instance: Optional["PresenceState"] = None
    _instance_lock = threading.Lock()

    def __init__(self, data_dir: str = "data") -> None:
        """
        Args:
            data_dir: 数据目录（存 presence.json）
        """
        self._path = Path(data_dir) / "presence.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()   # 多线程共用（QQ线程/主循环/工具层）
        self._mode: str = MODE_NORMAL
        self._until_ts: float = 0.0     # 到期时间戳；0=无限期（仅silent允许）
        self._reason: str = ""
        self._since_ts: float = 0.0
        # 「我在忙」简短告知的限频记录（user_id → 上次告知时间戳；不持久化）
        self._last_notice: dict[str, float] = {}
        self._load()

    # ------------------------------------------------------------
    # 进程级单例
    # ------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "PresenceState":
        """取进程级单例（懒创建，默认 data 目录）。"""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def set_instance(cls, instance: "PresenceState") -> None:
        """注入实例（测试用）。"""
        with cls._instance_lock:
            cls._instance = instance

    @classmethod
    def reset_instance(cls) -> None:
        """清除单例（测试收尾用）。"""
        with cls._instance_lock:
            cls._instance = None

    # ------------------------------------------------------------
    # 状态读写
    # ------------------------------------------------------------

    def get_mode(self, now: Optional[float] = None) -> str:
        """
        取当前状态（含到期自动恢复：忙碌/限时静默过点自动回 normal 并落盘）。

        Args:
            now: 当前时间戳（测试注入；不传取 time.time()）
        """
        current = now if now is not None else time.time()
        with self._lock:
            if self._mode != MODE_NORMAL and 0 < self._until_ts <= current:
                logger.info(f"[Presence] {self._mode} 模式到期，自动恢复正常")
                self._mode = MODE_NORMAL
                self._until_ts = 0.0
                self._reason = ""
                self._save_locked()
            return self._mode

    @property
    def is_quiet(self) -> bool:
        """是否处于忙碌/静默模式（含自动恢复检查）。"""
        return self.get_mode() != MODE_NORMAL

    def set_quiet(self, mode: str, duration_minutes: int = 0, reason: str = "",
                  now: Optional[float] = None) -> str:
        """
        进入忙碌/静默模式。

        Args:
            mode: busy（到期自动恢复，缺省60分钟）或 silent（不给时长=直到手动解除）
            duration_minutes: 持续分钟数（busy<=0取默认60；silent<=0=无限期）
            reason: 原因（记录用）
            now: 当前时间戳（测试注入）

        Returns:
            给用户的确认文案
        """
        current = now if now is not None else time.time()
        if mode not in (MODE_BUSY, MODE_SILENT):
            return f"不认识的模式「{mode}」，只有 busy（忙碌）和 silent（静默）两种。"
        with self._lock:
            self._mode = mode
            self._reason = reason
            self._since_ts = current
            if mode == MODE_BUSY:
                minutes = duration_minutes if duration_minutes > 0 else DEFAULT_BUSY_MINUTES
                self._until_ts = current + minutes * 60.0
            else:
                self._until_ts = (
                    current + duration_minutes * 60.0 if duration_minutes > 0 else 0.0
                )
            self._save_locked()
        if mode == MODE_BUSY:
            minutes = duration_minutes if duration_minutes > 0 else DEFAULT_BUSY_MINUTES
            logger.info(f"[Presence] 进入忙碌模式 {minutes} 分钟（{reason or '无原因'}）")
            return f"好，这{minutes}分钟我不吵你，你专心忙。到点我自己恢复。"
        if duration_minutes > 0:
            logger.info(f"[Presence] 进入静默模式 {duration_minutes} 分钟（{reason or '无原因'}）")
            return f"好，我安静{duration_minutes}分钟，有急事叫我。"
        logger.info(f"[Presence] 进入静默模式（直到手动解除，{reason or '无原因'}）")
        return "好，我闭嘴了。想让我说话的时候跟我说一声「可以说话了」就行。"

    def clear(self) -> str:
        """
        解除忙碌/静默模式，恢复正常。

        Returns:
            给用户的确认文案
        """
        with self._lock:
            was_quiet = self._mode != MODE_NORMAL
            self._mode = MODE_NORMAL
            self._until_ts = 0.0
            self._reason = ""
            self._save_locked()
        if was_quiet:
            logger.info("[Presence] 忙碌/静默模式已手动解除")
            return "好耶，可以说话了！你忙完啦？"
        return "我本来就没静音呀，一直都在。"

    def describe(self, now: Optional[float] = None) -> str:
        """当前状态的人话描述（get_quiet_status 工具用）。"""
        current = now if now is not None else time.time()
        mode = self.get_mode(now=current)
        if mode == MODE_NORMAL:
            return "现在是正常模式，随时可以聊。"
        with self._lock:
            if self._until_ts > 0:
                remaining = max(0, int((self._until_ts - current) / 60))
                mode_name = "忙碌" if mode == MODE_BUSY else "静默"
                return f"现在是{mode_name}模式，还有大约{remaining}分钟自动恢复。"
            return "现在是静默模式（没设时长），要我说话就说一声「可以说话了」。"

    def remaining_minutes(self, now: Optional[float] = None) -> int:
        """距自动恢复的剩余分钟数（无限期/正常模式返回0）。"""
        current = now if now is not None else time.time()
        if self.get_mode(now=current) == MODE_NORMAL:
            return 0
        with self._lock:
            if self._until_ts <= 0:
                return 0
            return max(0, int((self._until_ts - current) / 60))

    # ------------------------------------------------------------
    # QQ 回复决策
    # ------------------------------------------------------------

    def decide_qq_reply(self, user_id: str, text: str, is_group: bool,
                        is_at_me: bool, is_owner: bool,
                        now: Optional[float] = None) -> QuietReplyDecision:
        """
        忙碌/静默模式下的QQ回复决策（正常模式恒 reply_normal）。

        规则：
          - 主人私聊带紧急词（"紧急/在吗"等）或解除意图词（"忙完了"等）→ 正常回复
            （解除词放行是为了主人能在QQ上把静默关掉——工具得有机会执行）
          - 群聊未@白 → 闭嘴（skip）
          - 群聊被@ 或 私聊 → 礼貌简短回一句"我在忙"（每用户每30分钟最多一次，防刷屏；
            限频内的重复消息 skip）

        Args:
            user_id: 发送者id
            text: 消息文本
            is_group: 是否群聊
            is_at_me: 是否@了白
            is_owner: 是否主人（family_qq）
            now: 当前时间戳（测试注入）

        Returns:
            QuietReplyDecision（action=reply_normal/skip/brief_notice）
        """
        current = now if now is not None else time.time()
        mode = self.get_mode(now=current)
        if mode == MODE_NORMAL:
            return QuietReplyDecision(action="reply_normal")

        # 主人私聊：紧急/解除意图 → 正常回复
        if is_owner and not is_group:
            if any(kw in text for kw in _URGENT_KEYWORDS + _CLEAR_INTENT_KEYWORDS):
                return QuietReplyDecision(action="reply_normal")

        # 群聊闲聊（未@）：真的闭嘴
        if is_group and not is_at_me:
            return QuietReplyDecision(action="skip")

        # 群聊被@ / 私聊：限频简短告知
        if self._allow_notice(user_id, now=current):
            if mode == MODE_BUSY:
                notice = "我在忙，稍后找你哈。"
            else:
                notice = "我现在不太方便说话，晚点再找你。"
            return QuietReplyDecision(action="brief_notice", notice_text=notice)
        return QuietReplyDecision(action="skip")

    def _allow_notice(self, user_id: str, now: float) -> bool:
        """检查并登记「我在忙」告知限频（每用户每30分钟最多一次）。"""
        with self._lock:
            last = self._last_notice.get(user_id, 0.0)
            if now - last < NOTICE_INTERVAL_SECONDS:
                return False
            self._last_notice[user_id] = now
            return True

    # ------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------

    def _load(self) -> None:
        """启动加载 presence.json（已到期的状态直接丢弃）。"""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8")) or {}
            mode = data.get("mode", MODE_NORMAL)
            until_ts = float(data.get("until_ts", 0.0))
            if mode in (MODE_BUSY, MODE_SILENT):
                if 0 < until_ts <= time.time():
                    return  # 已到期，保持 normal
                self._mode = mode
                self._until_ts = until_ts
                self._reason = str(data.get("reason", ""))
                self._since_ts = float(data.get("since_ts", 0.0))
                logger.info(f"[Presence] 恢复上次的 {mode} 状态")
        except Exception as e:
            logger.warning(f"[Presence] 状态文件加载失败（按正常模式）: {e}")

    def _save_locked(self) -> None:
        """落盘（调用方必须已持有 self._lock）。"""
        try:
            payload = {
                "mode": self._mode,
                "until_ts": self._until_ts,
                "reason": self._reason,
                "since_ts": self._since_ts,
            }
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[Presence] 状态落盘失败: {e}")
