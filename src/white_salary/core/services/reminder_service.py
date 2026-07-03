"""
white_salary/core/services/reminder_service.py

提醒服务 — 自然语言时间解析 + 后台调度 + 双通道到点通知。

2026-07-03 工具实现（批9）：批2下架的提醒三件套（set_reminder/cancel_reminder/
list_reminders）当时是「开发中」空壳——模型答应用户设提醒但什么都不发生。
本服务是真实现的核心：
  - 存储 data/reminders.json（id/text/due_ts/repeat/created_by/status），增删改即时落盘
  - asyncio 后台调度循环（60秒粒度检查到期；懒启动防重，
    写法参照 MemoryManager._ensure_flush_task 批5模式）
  - 到点通知双通道：
      ① 桌面 → CrossPlatformBridge.push_to_desktop(source="reminder")，
        由 websocket_handler 的桥轮询接走、经 reply_start 链路让白开口说出来
      ② QQ 兜底 → 经注入的发送回调给主人发私聊（回调由 run_server 装配时注入，
        本服务不直接依赖 qq 模块）
  - 后端宕机期间错过的提醒：调度恢复后补通知一次并标注「迟到的提醒」
  - 自然语言时间解析（parse_when 纯函数，可单测）：
    10分钟后/半小时后/3点/下午3点/明天早上8点/今晚9点半/每天8点(repeat=daily)；
    解析不出返回 None——工具层据此返回追问文案，绝不瞎猜时间
"""

import asyncio
import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from loguru import logger


# ================================================================
# 自然语言时间解析（纯函数，无副作用，便于单测）
# ================================================================

@dataclass
class ParsedWhen:
    """时间解析结果。"""
    due_ts: float          # 到期的 Unix 时间戳
    repeat: str = "none"   # none=一次性 / daily=每天重复


# 中文数字表（口语常用：一~十、两）
_ZH_NUM: dict[str, int] = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

# 数字 token：阿拉伯数字 或 中文数字（含"十一/二十三"等组合）
_NUM_PATTERN = r"\d{1,2}|[零一二两三四五六七八九十]{1,3}"


def _zh_to_int(token: str) -> Optional[int]:
    """
    数字 token 转整数（支持阿拉伯数字与"一~九十九"的中文口语数字）。

    Args:
        token: 数字片段，如 "8" / "八" / "十" / "二十三"

    Returns:
        整数值；无法解析返回 None
    """
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if "十" in token:
        parts = token.split("十")
        if len(parts) != 2:
            return None
        tens_token, ones_token = parts[0], parts[1]
        if tens_token and tens_token not in _ZH_NUM:
            return None
        if ones_token and ones_token not in _ZH_NUM:
            return None
        tens = _ZH_NUM[tens_token] if tens_token else 1
        ones = _ZH_NUM[ones_token] if ones_token else 0
        return tens * 10 + ones
    if len(token) == 1:
        return _ZH_NUM.get(token)
    return None


def parse_when(when_text: str, now: Optional[datetime] = None) -> Optional[ParsedWhen]:
    """
    解析自然语言时间表达，返回到期时间戳与重复规则。

    支持的表达（覆盖高频口语）：
      - 相对：10分钟后 / 半小时后 / 两小时后 / 一个半小时后
      - 绝对：3点 / 下午3点 / 今晚9点半 / 明天早上8点 / 后天10点 / 3点20
      - 重复：每天8点（repeat=daily）
    裸"3点"（无时段词、无日期词）按「{今天h点, 今天h+12点, 明天h点} 里
    最近的未来时刻」消歧；带时段词但已过点的（如16:00说"下午3点"）顺延到明天。

    Args:
        when_text: 用户的时间原话
        now: 当前时刻（不传取系统时间；测试注入固定值）

    Returns:
        ParsedWhen；解析不出返回 None（调用方应追问，不许瞎猜）
    """
    if not when_text or not when_text.strip():
        return None
    text = when_text.strip()
    base_now = now or datetime.now()

    repeat = "daily" if re.search(r"每天|每日|天天", text) else "none"

    # ---------- 相对时间（"每天"不与相对时间组合） ----------
    if repeat == "none":
        minutes: Optional[int] = None
        # "一个半小时后"（必须先于裸"半小时后"检查——后者是它的子串）
        m = re.search(rf"({_NUM_PATTERN})\s*个半\s*(?:小时|钟头)[之以]?后", text)
        if m:
            value = _zh_to_int(m.group(1))
            if value is not None:
                minutes = value * 60 + 30
        # "半小时后 / 半个小时后"
        elif re.search(r"半\s*个?\s*(?:小时|钟头)[之以]?后", text):
            minutes = 30
        else:
            # "2小时后 / 两个小时后"
            m = re.search(rf"({_NUM_PATTERN})\s*个?\s*(?:小时|钟头)[之以]?后", text)
            if m:
                value = _zh_to_int(m.group(1))
                if value is not None:
                    minutes = value * 60
            else:
                # "10分钟后"
                m = re.search(rf"({_NUM_PATTERN})\s*分钟[之以]?后", text)
                if m:
                    value = _zh_to_int(m.group(1))
                    if value is not None:
                        minutes = value
        if minutes is not None and minutes > 0:
            due = base_now + timedelta(minutes=minutes)
            return ParsedWhen(due_ts=due.timestamp(), repeat="none")

    # ---------- 绝对时间 ----------
    day_offset = 0
    if "后天" in text:
        day_offset = 2
    elif re.search(r"明天|明早|明晚|明儿", text):
        day_offset = 1

    is_pm_hint = bool(re.search(r"下午|傍晚|晚上|今晚|明晚|晚间|夜里", text))
    is_noon_hint = "中午" in text
    is_am_hint = bool(re.search(r"早上|早晨|上午|凌晨|清晨|一早|明早", text))
    explicit_day = day_offset > 0 or bool(re.search(r"今天|今晚|今早", text))

    m = re.search(rf"({_NUM_PATTERN})\s*[点时]\s*(半|一刻|三刻|{_NUM_PATTERN})?", text)
    if not m:
        return None
    hour = _zh_to_int(m.group(1))
    if hour is None or hour > 24:
        return None
    if hour == 24:
        hour = 0
    minute = 0
    minute_token = m.group(2)
    if minute_token == "半":
        minute = 30
    elif minute_token == "一刻":
        minute = 15
    elif minute_token == "三刻":
        minute = 45
    elif minute_token:
        minute_value = _zh_to_int(minute_token)
        if minute_value is None:
            return None
        minute = minute_value
    if minute > 59:
        return None

    # 时段词修正到24小时制
    if is_pm_hint and hour < 12:
        hour += 12
    elif is_noon_hint and hour <= 2:
        hour += 12  # "中午1点" = 13:00
    if hour > 23:
        return None

    def _combine(offset_days: int) -> datetime:
        """把解析出的时/分落到 offset_days 天后的日期上。"""
        return base_now.replace(
            hour=hour, minute=minute, second=0, microsecond=0,
        ) + timedelta(days=offset_days)

    if repeat == "daily":
        # 每天重复：首次到期取今天，已过点则从明天开始
        due = _combine(0)
        if due <= base_now:
            due += timedelta(days=1)
    elif day_offset > 0:
        due = _combine(day_offset)
    elif not is_pm_hint and not is_noon_hint and not is_am_hint and not explicit_day:
        # 裸"3点"：在 {今天h, 今天h+12, 明天h} 里取最近的未来时刻
        candidates = [_combine(0), _combine(1)]
        if hour < 12:
            candidates.append(_combine(0) + timedelta(hours=12))
        future = sorted(c for c in candidates if c > base_now)
        if not future:
            return None
        due = future[0]
    else:
        due = _combine(0)
        if due <= base_now:
            due += timedelta(days=1)  # 带时段词但已过点 → 顺延到明天同一时刻

    return ParsedWhen(due_ts=due.timestamp(), repeat=repeat)


# ================================================================
# 提醒数据模型
# ================================================================

@dataclass
class Reminder:
    """一条提醒。"""
    id: str                 # 短id（uuid前8位）
    text: str               # 提醒内容（"开会"）
    due_ts: float           # 到期时间戳
    repeat: str = "none"    # none=一次性 / daily=每天
    created_by: str = ""    # 谁设的（user_id）
    status: str = "pending"  # pending / done / cancelled
    created_ts: float = 0.0


# 追问文案：时间解析不出时由工具原样返回（绝不瞎猜时间）
ASK_WHEN_TEXT = "你想让我几点提醒你？可以说「10分钟后」「下午3点」或「明天早上8点」这样。"


class ReminderService:
    """
    提醒服务（进程级单例，run_server 装配后全进程共用）。

    使用方式:
        service = ReminderService(data_dir="data", qq_send=回调, owner_id="12345")
        ReminderService.set_instance(service)      # run_server 装配
        service.ensure_schedule_task()             # 事件循环内启动后台调度
        ok, msg = service.add("开会", "下午3点")    # 工具层调用
    """

    _instance: Optional["ReminderService"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        data_dir: str = "data",
        qq_send: Optional[Callable[[str, str], bool]] = None,
        owner_id: str = "",
        desktop_push: Optional[Callable[[str], None]] = None,
        check_interval_seconds: float = 60.0,
        late_threshold_seconds: float = 120.0,
    ) -> None:
        """
        Args:
            data_dir: 数据目录（存 reminders.json）
            qq_send: QQ发送回调 (user_id, text) -> 是否已调度；由 run_server 注入，
                     内部经 run_coroutine_threadsafe 跨线程调度到QQ事件循环，
                     本服务不直接依赖qq模块；None=QQ通道不可用
            owner_id: 主人的统一 user_id（QQ兜底通知的收件人）
            desktop_push: 桌面通道回调 (text) -> None；None=默认走 CrossPlatformBridge
                          （参数主要供单测注入假通道）
            check_interval_seconds: 调度检查粒度（默认60秒）
            late_threshold_seconds: 超过该秒数才通知的算「迟到的提醒」
                                    （默认120秒 > 检查粒度，正常到点不会误标迟到）
        """
        self._path = Path(data_dir) / "reminders.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()          # 多线程（桌面/QQ/QZone各loop）共用，加锁
        self._reminders: list[Reminder] = []
        self._qq_send = qq_send
        self._owner_id = owner_id
        self._desktop_push = desktop_push
        self._check_interval_seconds = check_interval_seconds
        self._late_threshold_seconds = late_threshold_seconds
        self._schedule_task: Optional[asyncio.Task] = None
        self._load()

    # ------------------------------------------------------------
    # 进程级单例（run_server 装配注入；工具层经 get_instance 取用）
    # ------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "ReminderService":
        """取进程级单例；未装配时懒创建默认实例（无QQ通道，仅桌面+落盘）。"""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def set_instance(cls, instance: "ReminderService") -> None:
        """run_server 装配（或测试）时注入配置好的实例。"""
        with cls._instance_lock:
            cls._instance = instance

    @classmethod
    def reset_instance(cls) -> None:
        """清除单例（测试收尾用）。"""
        with cls._instance_lock:
            cls._instance = None

    # ------------------------------------------------------------
    # 增删查（工具层入口）
    # ------------------------------------------------------------

    def add(self, text: str, when_text: str, created_by: str = "") -> tuple[bool, str]:
        """
        新增一条提醒。

        Args:
            text: 提醒内容
            when_text: 时间原话（"下午3点"）
            created_by: 设提醒的用户id

        Returns:
            (是否成功, 给用户的回复文案)——解析不出时间时返回追问文案，不瞎猜
        """
        if not text or not text.strip():
            return False, "要提醒你什么事？告诉我内容和时间，比如「提醒我三点开会」。"
        parsed = parse_when(when_text)
        if parsed is None:
            # when 解析不出时，退一步从内容原话里找时间（tool_llm 可能没拆干净）
            parsed = parse_when(text)
        if parsed is None:
            return False, ASK_WHEN_TEXT

        reminder = Reminder(
            id=uuid.uuid4().hex[:8],
            text=text.strip(),
            due_ts=parsed.due_ts,
            repeat=parsed.repeat,
            created_by=created_by,
            created_ts=time.time(),
        )
        with self._lock:
            self._reminders.append(reminder)
            self._save_locked()
        self.ensure_schedule_task()
        due_str = self._format_due(reminder)
        logger.info(f"[Reminder] 新增提醒 {reminder.id}: {due_str} {reminder.text!r}")
        return True, f"好，记下了：{due_str}提醒你「{reminder.text}」。"

    def cancel(self, query: str) -> str:
        """
        取消提醒（按短id精确匹配，或按内容关键词模糊匹配）。

        Args:
            query: 提醒id 或 内容关键词

        Returns:
            给用户的回复文案
        """
        query = (query or "").strip()
        if not query:
            return "想取消哪条提醒？告诉我内容关键词，比如「取消开会的提醒」。"
        with self._lock:
            pending = [r for r in self._reminders if r.status == "pending"]
            matches = [r for r in pending if query == r.id or query in r.text]
            if not matches:
                return f"没找到内容含「{query}」的待提醒事项。" + self._describe_pending_locked()
            if len(matches) > 1:
                lines = [f"有{len(matches)}条提醒都匹配「{query}」，你想取消哪条？"]
                for r in matches:
                    lines.append(f"  [{r.id}] {self._format_due(r)} {r.text}")
                return "\n".join(lines)
            matches[0].status = "cancelled"
            self._save_locked()
            logger.info(f"[Reminder] 取消提醒 {matches[0].id}: {matches[0].text!r}")
            return f"好，已取消提醒「{matches[0].text}」。"

    def describe_pending(self) -> str:
        """列出全部待提醒事项（工具 list_reminders 用）。"""
        with self._lock:
            return self._describe_pending_locked(standalone=True)

    @property
    def pending_count(self) -> int:
        """待提醒条数。"""
        with self._lock:
            return sum(1 for r in self._reminders if r.status == "pending")

    # ------------------------------------------------------------
    # 后台调度（懒启动防重，参照 MemoryManager._ensure_flush_task 批5写法）
    # ------------------------------------------------------------

    def ensure_schedule_task(self) -> None:
        """
        懒启动后台调度任务（防重）。

        当前线程没有运行中的事件循环时静默跳过（如测试/同步脚本），
        等下次在事件循环内调用（add / run_server 的 startup 钩子）时补启动。
        """
        if self._schedule_task is not None and not self._schedule_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 当前线程没有事件循环，等下次再试
        self._schedule_task = loop.create_task(self._schedule_loop())
        logger.info(
            f"[Reminder] 提醒调度已启动"
            f"（每 {self._check_interval_seconds:.0f} 秒检查到期，"
            f"待提醒 {self.pending_count} 条）"
        )

    async def _schedule_loop(self) -> None:
        """后台循环：每60秒检查一次到期提醒（首轮也等60秒——给QQ连接留时间）。"""
        while True:
            try:
                await asyncio.sleep(self._check_interval_seconds)
                self.check_due()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[Reminder] 调度检查异常（下轮重试）: {e}")

    def check_due(self, now: Optional[float] = None) -> list[str]:
        """
        检查并通知所有到期提醒（同步函数，测试可注入 now 直接驱动）。

        - 到点的：双通道通知（桌面桥 + QQ兜底），文案自然口语
        - 迟到的（后端宕机期间错过的，超过 late_threshold）：补通知一次并标注
        - repeat=daily 的：通知后推进到下一个未来时刻；一次性的标记 done

        Args:
            now: 当前时间戳（测试注入；不传取 time.time()）

        Returns:
            本轮发出的通知文案列表（空=无到期）
        """
        current = now if now is not None else time.time()
        fired: list[str] = []
        with self._lock:
            for r in self._reminders:
                if r.status != "pending" or r.due_ts > current:
                    continue
                is_late = (current - r.due_ts) > self._late_threshold_seconds
                if is_late:
                    message = f"这是条迟到的提醒——我刚才不在线。你之前让我提醒你：{r.text}"
                else:
                    message = f"到点啦，你让我提醒你：{r.text}"
                fired.append(message)
                if r.repeat == "daily":
                    # 推进到下一个未来时刻（宕机跨多天也只补一次通知）
                    while r.due_ts <= current:
                        r.due_ts += 86400.0
                else:
                    r.status = "done"
                logger.info(f"[Reminder] 触发提醒 {r.id}{'（迟到）' if is_late else ''}: {r.text!r}")
            if fired:
                self._save_locked()
        # 通知放锁外发（回调不可控，不能占着锁）
        for message in fired:
            self._notify(message)
        return fired

    def _notify(self, message: str) -> None:
        """
        双通道到点通知。

        ① 桌面：推入跨平台桥（source="reminder"），websocket_handler 的桥轮询
          接走后经 reply_start 链路让白开口说出来（提醒穿透静默模式——用户明确
          设的提醒不算"主动搭话"）；桌面不在线时消息留在桥队列，同时有②兜底。
        ② QQ：经注入回调给主人发私聊（同时发，双保险——通知必须真到人）。
        """
        try:
            if self._desktop_push is not None:
                self._desktop_push(message)
            else:
                from white_salary.core.cross_platform import CrossPlatformBridge
                CrossPlatformBridge().push_to_desktop(
                    message, from_user=self._owner_id, source="reminder",
                )
        except Exception as e:
            logger.warning(f"[Reminder] 桌面通道通知失败: {e}")
        try:
            if self._qq_send is not None and self._owner_id:
                self._qq_send(self._owner_id, message)
        except Exception as e:
            logger.warning(f"[Reminder] QQ通道通知失败: {e}")

    # ------------------------------------------------------------
    # 持久化与格式化
    # ------------------------------------------------------------

    def _load(self) -> None:
        """启动加载 reminders.json（损坏时从空开始，不炸服务）。"""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8")) or {}
            items = data.get("reminders", []) if isinstance(data, dict) else data
            self._reminders = [Reminder(**item) for item in items]
            logger.info(
                f"[Reminder] 已加载 {len(self._reminders)} 条提醒记录"
                f"（待提醒 {sum(1 for r in self._reminders if r.status == 'pending')} 条）"
            )
        except Exception as e:
            logger.warning(f"[Reminder] 提醒文件加载失败（从空开始）: {e}")
            self._reminders = []

    def _save_locked(self) -> None:
        """落盘（调用方必须已持有 self._lock）。顺带清理7天前的已完成/已取消记录。"""
        try:
            cutoff = time.time() - 7 * 86400
            self._reminders = [
                r for r in self._reminders
                if r.status == "pending" or r.created_ts > cutoff
            ]
            payload = {"reminders": [asdict(r) for r in self._reminders]}
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[Reminder] 提醒落盘失败: {e}")

    def _describe_pending_locked(self, standalone: bool = False) -> str:
        """格式化待提醒列表（调用方必须已持有 self._lock）。"""
        pending = sorted(
            (r for r in self._reminders if r.status == "pending"),
            key=lambda r: r.due_ts,
        )
        if not pending:
            return "现在没有待提醒的事项。" if standalone else ""
        lines = [f"当前有{len(pending)}条待提醒："]
        for r in pending:
            lines.append(f"  [{r.id}] {self._format_due(r)} {r.text}")
        return "\n" + "\n".join(lines) if not standalone else "\n".join(lines)

    @staticmethod
    def _format_due(r: Reminder) -> str:
        """把到期时间格式化成人话（今天15:00 / 明天08:00 / 每天08:00）。"""
        dt = datetime.fromtimestamp(r.due_ts)
        if r.repeat == "daily":
            return f"每天{dt:%H:%M}"
        today = datetime.now().date()
        if dt.date() == today:
            day_part = "今天"
        elif dt.date() == today + timedelta(days=1):
            day_part = "明天"
        else:
            day_part = f"{dt.month}月{dt.day}日"
        return f"{day_part}{dt:%H:%M}"
