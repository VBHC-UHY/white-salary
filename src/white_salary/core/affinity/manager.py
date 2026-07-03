"""
white_salary/core/affinity/manager.py

好感度系统（完整版） — 管理AI与用户的关系等级。

11级好感度体系：
  -5 厌恶(<-100) → -4 敌意(-100~-50) → -3 反感(-50~-20)
  → -2 不喜欢(-20~-5) → -1 冷淡(-5~0) → 0 陌生人(0~15)
  → 1 认识(15~40) → 2 朋友(40~80) → 3 好朋友(80~150)
  → 4 挚友(150~300) → 5 知己(300+)
  特殊：家人（独立等级，不受衰减影响）

核心机制：
  - 正面互动加分，等级越高效率越低（最难升级知己）
  - 负面互动减分，直接生效
  - 每日衰减（高等级衰减更快，需主动维护）
  - 14天不联系软遗忘，21天硬遗忘
  - 连续互动天数奖励
  - 消息内容自动检测好感度变化

参考: WhiteSalary-v2 affinity_manager.py (882行, 35KB)
"""

import json
import shutil
import threading
import time
from dataclasses import dataclass, field, asdict, fields
from enum import IntEnum
from pathlib import Path
from typing import Optional

from loguru import logger


# ================================================================
# 好感度等级定义
# ================================================================

class AffinityLevel(IntEnum):
    HATRED = -5       # 厌恶
    HOSTILE = -4      # 敌意
    DISLIKE = -3      # 反感
    UNFAVORABLE = -2  # 不喜欢
    COLD = -1         # 冷淡
    STRANGER = 0      # 陌生人
    ACQUAINTANCE = 1  # 认识
    FRIEND = 2        # 朋友
    GOOD_FRIEND = 3   # 好朋友
    CLOSE_FRIEND = 4  # 挚友
    BEST_FRIEND = 5   # 知己
    FAMILY = 99       # 家人（特殊）


# 等级配置：名称、分数范围、emoji
LEVEL_CONFIG = {
    AffinityLevel.HATRED:       {"name": "厌恶",   "min": -9999, "max": -100, "emoji": "💀"},
    AffinityLevel.HOSTILE:      {"name": "敌意",   "min": -100,  "max": -50,  "emoji": "😠"},
    AffinityLevel.DISLIKE:      {"name": "反感",   "min": -50,   "max": -20,  "emoji": "😒"},
    AffinityLevel.UNFAVORABLE:  {"name": "不喜欢", "min": -20,   "max": -5,   "emoji": "😐"},
    AffinityLevel.COLD:         {"name": "冷淡",   "min": -5,    "max": 0,    "emoji": "🥶"},
    AffinityLevel.STRANGER:     {"name": "陌生人", "min": 0,     "max": 15,   "emoji": "👤"},
    AffinityLevel.ACQUAINTANCE: {"name": "认识",   "min": 15,    "max": 40,   "emoji": "🤝"},
    AffinityLevel.FRIEND:       {"name": "朋友",   "min": 40,    "max": 80,   "emoji": "😊"},
    AffinityLevel.GOOD_FRIEND:  {"name": "好朋友", "min": 80,    "max": 150,  "emoji": "😄"},
    AffinityLevel.CLOSE_FRIEND: {"name": "挚友",   "min": 150,   "max": 300,  "emoji": "💕"},
    AffinityLevel.BEST_FRIEND:  {"name": "知己",   "min": 300,   "max": 9999, "emoji": "💖"},
    AffinityLevel.FAMILY:       {"name": "家人",   "min": 0,     "max": 9999, "emoji": "👨‍👩‍👧"},
}

# 等级效率系数（正面操作的有效比例，等级越高越难涨分）
LEVEL_EFFICIENCY = {
    AffinityLevel.HATRED:       1.5,   # 负面等级更容易恢复
    AffinityLevel.HOSTILE:      1.3,
    AffinityLevel.DISLIKE:      1.2,
    AffinityLevel.UNFAVORABLE:  1.1,
    AffinityLevel.COLD:         1.0,
    AffinityLevel.STRANGER:     1.0,   # 基准
    AffinityLevel.ACQUAINTANCE: 0.9,
    AffinityLevel.FRIEND:       0.75,
    AffinityLevel.GOOD_FRIEND:  0.55,
    AffinityLevel.CLOSE_FRIEND: 0.35,
    AffinityLevel.BEST_FRIEND:  0.2,   # 最难涨分！
}

# 衰减配置
POSITIVE_DECAY_RATE = 1.0   # 基础衰减：-1分/天
LEVEL_DECAY_BONUS = 0.4     # 每个等级额外衰减：+0.4分/天
NEGATIVE_RECOVERY_RATE = 0.3  # 负分恢复速度：+0.3分/天

# ================================================================
# 互动行为定义（正面和负面）
# ================================================================

POSITIVE_ACTIONS = {
    # 基础互动
    "greeting": 1.0,           # 打招呼
    "polite_chat": 1.0,        # 礼貌对话
    "normal_reply": 0.5,       # 普通回复
    # 情感表达
    "compliment": 2.0,         # 夸奖/赞美
    "encouragement": 2.0,      # 鼓励
    "care": 2.0,               # 关心
    "gift": 3.0,               # 送礼物
    # 深度互动
    "long_chat": 2.0,          # 长时间聊天
    "meaningful_talk": 3.0,    # 有意义的深度对话
    "share_personal": 2.0,     # 分享个人故事
    "remember_detail": 3.0,    # 记住白说过的事
    # 特殊行为
    "defend": 5.0,             # 维护
    "help": 4.0,               # 帮助解决问题
    "comfort": 3.0,            # 安慰
    "apologize": 2.0,          # 道歉
    # 连续奖励
    "daily_interaction": 1.0,  # 每日互动
    "consecutive_3days": 3.0,  # 连续3天
    "consecutive_7days": 5.0,  # 连续7天
    "consecutive_30days": 10.0, # 连续30天
}

NEGATIVE_ACTIONS = {
    # 粗鲁
    "rude": -2.0,
    "impolite": -1.0,
    "ignore_greeting": -1.0,
    # 言语攻击
    "insult": -5.0,
    "mock": -3.0,
    "sarcasm": -2.0,
    "curse": -8.0,
    # 骚扰
    "spam": -3.0,
    "annoy": -2.0,
    "inappropriate": -5.0,
    # 信任破坏
    "lie": -5.0,
    "betray": -10.0,
    # 冷淡
    "cold_response": -1.0,
    "end_chat_rudely": -2.0,
}

# 关键词检测映射（借鉴v2的300+关键词库，增强覆盖度）
POSITIVE_KEYWORDS = {
    "compliment": [
        "好厉害", "厉害", "真棒", "太强了", "好聪明", "聪明", "佩服",
        "可爱", "好看", "漂亮", "好萌", "萌萌的", "帅", "美",
        "优秀", "牛", "给力", "666", "nb", "yyds", "绝了",
        "天才", "神", "大佬", "好强", "无敌", "完美", "赞",
    ],
    "care": [
        "吃饭了吗", "早点睡", "注意身体", "怎么了", "心情怎么样",
        "冷不冷", "照顾好自己", "别太累", "别熬夜", "加衣服",
        "记得喝水", "注意休息", "担心你", "你还好吗", "好点了吗",
    ],
    "gift": ["送你", "给你", "礼物", "红包", "请你吃", "买给你", "奖励你"],
    "comfort": [
        "别难过", "没关系", "会好的", "别伤心", "陪你",
        "抱抱", "摸摸头", "乖", "辛苦了", "不哭",
        "我在", "别怕", "有我在",
    ],
    "encouragement": [
        "加油", "你可以的", "相信你", "支持你", "看好你",
        "继续", "不错", "有进步", "越来越好",
    ],
    "love": [
        "喜欢你", "爱你", "爱死你", "最喜欢你", "想你",
        "你最好了", "你是最棒的", "离不开你", "好幸福",
        "蹭蹭", "亲亲", "么么", "❤", "💕", "♥",
    ],
    "greeting": [
        "早安", "早上好", "晚安", "你好", "好久不见",
        "想你了", "终于来了",
    ],
    "apologize": [
        "对不起", "抱歉", "我错了", "原谅我", "不好意思",
        "是我的错", "别生气",
    ],
}

NEGATIVE_KEYWORDS = {
    "insult": [
        "傻逼", "白痴", "智障", "废物", "垃圾", "讨厌你",
        "弱智", "脑残", "蠢货", "混蛋", "王八蛋",
    ],
    "rude": [
        "烦死了", "吵死了", "别烦我", "走开", "少废话", "闭嘴",
        "滚开", "别BB", "烦人", "吵人", "别说了",
    ],
    "curse": ["去死", "操你", "妈的", "你妈", "艹"],
    "inappropriate": ["脱衣", "色色", "涩涩"],
    "mock": ["呵呵", "切", "无语", "懒得理你", "不想跟你说话"],
}

# 白名单 — 包含负面词但不应扣分的短语（借鉴v2的白名单防误报）
KEYWORD_WHITELIST = [
    "笨笨的", "傻傻的", "蠢萌", "蠢蠢的",  # 撒娇用语
    "滚动", "滚轮", "滚筒",                  # "滚"的正常用法
    "脱口秀", "摆脱", "脱离",                 # "脱"的正常用法
    "废物利用", "垃圾分类", "垃圾桶",         # "废物/垃圾"的正常用法
]


# ================================================================
# 用户好感度数据
# ================================================================

@dataclass
class UserAffinity:
    user_id: str = "default"
    points: float = 0.0
    level: int = 0
    is_family: bool = False
    consecutive_days: int = 0
    total_interactions: int = 0
    first_interaction: float = 0.0
    last_interaction: float = 0.0
    last_decay_check: float = 0.0
    last_daily_bonus: str = ""
    history: list[dict] = field(default_factory=list)  # 最近20条变化记录


class AffinityManager:
    """
    好感度管理器（完整版）。

    管理用户与AI的关系等级、互动积分、衰减和奖励。

    2026-07-03 审计修复（批5）：进程级共享实例（按 data_dir 归一化路径缓存）。
    审计实锤：settings_api 的 GET /memory 每分钟被前端轮询并直接 new 本类，
    __init__ 日志（"[Affinity] 用户关系: ..."）单日上千次，且多份实例
    并发读写同一 affinity.json 有覆盖风险。改为同一 data_dir 全进程只保留
    一个实例；get_for_user 的多用户路径用 shared=False 逃生口保持
    每用户独立实例（由 _multi_user_cache 按 user_id 缓存）。
    """

    # 进程级共享实例注册表：归一化路径 -> 实例
    _shared_instances: dict[str, "AffinityManager"] = {}
    _shared_lock: threading.Lock = threading.Lock()

    def __new__(cls, data_dir: str = "data/affinity", shared: bool = True) -> "AffinityManager":
        # 2026-07-03 审计修复（批5）：按归一化路径复用实例，禁止整套重实例化。
        # shared=False 时绕过注册表（get_for_user 多用户路径专用，
        # 它会在构造后改写 _data_path，共享会导致所有用户串成同一份档案）
        if not shared:
            return super().__new__(cls)
        key = str(Path(data_dir).resolve())
        with cls._shared_lock:
            inst = cls._shared_instances.get(key)
            if inst is None:
                inst = super().__new__(cls)
                cls._shared_instances[key] = inst
        return inst

    def __init__(self, data_dir: str = "data/affinity", shared: bool = True) -> None:
        # 2026-07-03 审计修复（批5）：命中共享实例时跳过重复初始化
        # （标志在初始化末尾才置位，若上次初始化中途抛异常会自动重试）
        if getattr(self, "_shared_inited", False):
            return

        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._data_path = self._data_dir / "affinity.json"
        self._affinity = self._load()

        logger.info(
            f"[Affinity] 用户关系: {self._get_level_name()} "
            f"{self._get_emoji()} ({self._affinity.points:.1f}分, "
            f"连续{self._affinity.consecutive_days}天)"
        )
        self._shared_inited: bool = True

    # ================================================================
    # 持久化
    # ================================================================

    def _load(self) -> UserAffinity:
        """
        2026-07-02 审计修复（批4）：好感度反序列化白名单。

        原实现 UserAffinity(**data) 遇到历史遗留的未知字段（如老版本写入的
        recent_changes）会抛 TypeError，整份好感度档案被静默重置为默认值
        （多年积分/连续天数/家人标记全丢）。改为：
          1. 按 dataclass fields 白名单过滤未知字段（过滤动作记warning日志）；
          2. 真正解析失败时先把原文件复制为 .corrupt.bak 再用默认值，
             并用 logger.error 醒目告警，不再静默丢数据。
        """
        if self._data_path.exists():
            try:
                with open(self._data_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError(f"好感度文件顶层应为dict，实际为 {type(data).__name__}")
                # 按 dataclass 字段白名单过滤未知字段
                known_fields = {f.name for f in fields(UserAffinity)}
                unknown = set(data.keys()) - known_fields
                if unknown:
                    logger.warning(
                        f"[Affinity] {self._data_path.name} 含未知字段 "
                        f"{sorted(unknown)}，已按白名单过滤（数据保留加载）"
                    )
                filtered = {k: v for k, v in data.items() if k in known_fields}
                return UserAffinity(**filtered)
            except Exception as e:
                # 加载失败：先备份原文件再回退默认值，避免下次_save覆盖掉唯一现场
                backup_path = self._data_path.with_name(self._data_path.name + ".corrupt.bak")
                try:
                    shutil.copy2(self._data_path, backup_path)
                    logger.error(
                        f"[Affinity] !!! 好感度档案加载失败，原文件已备份到 "
                        f"{backup_path}，本次使用默认值（原因: {e}）"
                    )
                except Exception as be:
                    logger.error(
                        f"[Affinity] !!! 好感度档案加载失败且备份也失败 "
                        f"({self._data_path} → {backup_path}): 加载错误={e}, 备份错误={be}"
                    )
        return UserAffinity(first_interaction=time.time())

    def _save(self) -> None:
        with open(self._data_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self._affinity), f, ensure_ascii=False, indent=2)

    # ================================================================
    # 多用户好感度支持（QQ集成用）
    # ================================================================

    _multi_user_cache: dict[str, "AffinityManager"] = {}

    @classmethod
    def get_for_user(cls, user_id: str, data_dir: str = "data/affinity") -> "AffinityManager":
        """
        获取指定用户的好感度管理器（多用户模式）。

        每个QQ用户有独立的好感度文件。

        Args:
            user_id: 用户唯一标识（如QQ号）
            data_dir: 数据目录

        Returns:
            该用户的AffinityManager实例
        """
        if user_id not in cls._multi_user_cache:
            user_dir = str(Path(data_dir) / "users")
            Path(user_dir).mkdir(parents=True, exist_ok=True)
            # 2026-07-03 审计修复（批5）：多用户路径必须绕过进程级共享注册表
            # （shared=False），否则所有用户拿到同一个实例、_data_path 相互改写
            mgr = cls(data_dir=user_dir, shared=False)
            # 用用户专属文件
            mgr._data_path = Path(user_dir) / f"affinity_{user_id}.json"
            mgr._affinity = mgr._load()
            mgr._affinity.user_id = user_id
            cls._multi_user_cache[user_id] = mgr
        return cls._multi_user_cache[user_id]

    # ================================================================
    # 积分操作
    # ================================================================

    def add_points(self, delta: float, reason: str = "") -> float:
        """
        增加/减少好感度积分。

        正值会被效率系数衰减（等级越高效率越低）。
        负值直接生效。

        Returns: 实际变化量
        """
        if self._affinity.is_family:
            return 0.0

        old_points = self._affinity.points
        level = self._get_level()

        # 正值乘以效率系数
        if delta > 0:
            efficiency = LEVEL_EFFICIENCY.get(level, 1.0)
            actual = round(delta * efficiency, 2)
        else:
            actual = delta

        self._affinity.points += actual
        self._affinity.level = self._get_level().value

        # 记录历史（最多保留20条）
        self._affinity.history.append({
            "time": time.strftime("%Y-%m-%d %H:%M"),
            "delta": actual,
            "reason": reason,
            "old": round(old_points, 1),
            "new": round(self._affinity.points, 1),
        })
        if len(self._affinity.history) > 20:
            self._affinity.history = self._affinity.history[-20:]

        if reason:
            logger.debug(
                f"[Affinity] {reason}: {actual:+.1f}分 "
                f"({old_points:.1f} → {self._affinity.points:.1f})"
            )

        self._save()
        return actual

    # ================================================================
    # 互动处理（每次对话自动调用）
    # ================================================================

    def process_interaction(self) -> None:
        """
        处理一次互动。

        自动执行：衰减检查、基础互动加分、每日奖励、连续天数奖励。
        """
        if self._affinity.is_family:
            return

        now = time.time()
        today = time.strftime("%Y-%m-%d")

        # 1. 衰减检查
        self._check_decay()

        # 2. 不活跃检查
        self._check_inactivity()

        # 3. 更新互动统计
        self._affinity.total_interactions += 1
        self._affinity.last_interaction = now
        if self._affinity.first_interaction == 0:
            self._affinity.first_interaction = now

        # 4. 基础互动加分
        self.add_points(POSITIVE_ACTIONS["normal_reply"], "互动")

        # 5. 每日首次互动奖励
        if self._affinity.last_daily_bonus != today:
            # 计算间隔天数
            if self._affinity.last_daily_bonus:
                try:
                    last_ts = time.mktime(time.strptime(self._affinity.last_daily_bonus, "%Y-%m-%d"))
                    gap_days = (now - last_ts) / 86400
                    if gap_days > 2:
                        # 超过2天没互动，重置连续天数
                        self._affinity.consecutive_days = 0
                except ValueError:
                    pass

            self._affinity.last_daily_bonus = today
            self._affinity.consecutive_days += 1

            # 每日奖励
            self.add_points(POSITIVE_ACTIONS["daily_interaction"], "每日互动")

            # 连续天数里程碑奖励
            days = self._affinity.consecutive_days
            if days >= 30 and days % 30 == 0:
                self.add_points(POSITIVE_ACTIONS["consecutive_30days"], f"连续{days}天")
            elif days >= 7 and days % 7 == 0:
                self.add_points(POSITIVE_ACTIONS["consecutive_7days"], f"连续{days}天")
            elif days >= 3 and days % 3 == 0:
                self.add_points(POSITIVE_ACTIONS["consecutive_3days"], f"连续{days}天")

    def process_message(self, message: str) -> list[str]:
        """
        分析消息内容，自动检测好感度变化。

        Args:
            message: 用户发送的消息

        Returns:
            触发的行为列表
        """
        if self._affinity.is_family:
            return []

        triggered = []

        # 白名单检查：如果消息包含白名单短语，跳过该部分的负面检测
        is_whitelisted = any(wl in message for wl in KEYWORD_WHITELIST)

        # 检测正面关键词
        for action, keywords in POSITIVE_KEYWORDS.items():
            for kw in keywords:
                if kw in message:
                    pts = POSITIVE_ACTIONS.get(action, 1.0)
                    self.add_points(pts, f"正面:{action}/{kw}")
                    triggered.append(action)
                    break

        # 检测负面关键词（白名单命中时跳过）
        if not is_whitelisted:
            for action, keywords in NEGATIVE_KEYWORDS.items():
                for kw in keywords:
                    if kw in message:
                        pts = NEGATIVE_ACTIONS.get(action, -1.0)
                        self.add_points(pts, f"负面:{action}/{kw}")
                        triggered.append(action)
                        break

        return triggered

    # ================================================================
    # 衰减和不活跃处理
    # ================================================================

    def _check_decay(self) -> None:
        """每日衰减检查。高等级衰减更快。"""
        now = time.time()
        if self._affinity.last_decay_check == 0:
            self._affinity.last_decay_check = now
            return

        days_since = (now - self._affinity.last_decay_check) / 86400
        if days_since < 1:
            return

        int_days = int(days_since)

        if self._affinity.points > 0:
            # 正面好感度衰减
            level = self._get_level()
            level_value = max(0, level.value)
            decay_rate = POSITIVE_DECAY_RATE + (LEVEL_DECAY_BONUS * level_value)
            total_decay = decay_rate * int_days
            self._affinity.points = max(0, self._affinity.points - total_decay)
            if total_decay > 0:
                logger.debug(f"[Affinity] 衰减: -{total_decay:.1f}分 ({int_days}天)")
        elif self._affinity.points < 0:
            # 负面好感度自然恢复
            recovery = NEGATIVE_RECOVERY_RATE * int_days
            self._affinity.points = min(0, self._affinity.points + recovery)

        self._affinity.last_decay_check = now

    def _check_inactivity(self) -> None:
        """不活跃检查：14天软遗忘，21天硬遗忘。"""
        if self._affinity.last_interaction == 0:
            return

        days_inactive = (time.time() - self._affinity.last_interaction) / 86400

        if days_inactive >= 21:
            # 硬遗忘：好感度重置
            old_points = self._affinity.points
            if old_points > 15:  # 只有超过认识级别才重置
                self._affinity.points = 15  # 降到认识级别
                self._affinity.consecutive_days = 0
                logger.warning(
                    f"[Affinity] 硬遗忘: {days_inactive:.0f}天未联系, "
                    f"{old_points:.1f} → 15分"
                )
        elif days_inactive >= 14:
            # 软遗忘：额外衰减
            extra_decay = (days_inactive - 14) * 1.0
            if extra_decay > 0 and self._affinity.points > 0:
                self._affinity.points = max(0, self._affinity.points - extra_decay)
                logger.debug(f"[Affinity] 软遗忘额外衰减: -{extra_decay:.1f}分")

    # ================================================================
    # 等级查询
    # ================================================================

    def _get_level(self) -> AffinityLevel:
        if self._affinity.is_family:
            return AffinityLevel.FAMILY

        pts = self._affinity.points
        if pts >= 300: return AffinityLevel.BEST_FRIEND
        if pts >= 150: return AffinityLevel.CLOSE_FRIEND
        if pts >= 80:  return AffinityLevel.GOOD_FRIEND
        if pts >= 40:  return AffinityLevel.FRIEND
        if pts >= 15:  return AffinityLevel.ACQUAINTANCE
        if pts >= 0:   return AffinityLevel.STRANGER
        if pts >= -5:  return AffinityLevel.COLD
        if pts >= -20: return AffinityLevel.UNFAVORABLE
        if pts >= -50: return AffinityLevel.DISLIKE
        if pts >= -100: return AffinityLevel.HOSTILE
        return AffinityLevel.HATRED

    def _get_level_name(self) -> str:
        level = self._get_level()
        return LEVEL_CONFIG[level]["name"]

    def _get_emoji(self) -> str:
        level = self._get_level()
        return LEVEL_CONFIG[level]["emoji"]

    # ================================================================
    # 上下文注入
    # ================================================================

    def get_context_hint(self) -> str:
        """
        获取注入系统提示词的好感度提示。

        让AI根据关系等级调整说话风格和亲密度。
        """
        level = self._get_level()
        config = LEVEL_CONFIG[level]
        days = self._affinity.consecutive_days

        hint = (
            f"\n[与用户的关系等级: {config['name']} {config['emoji']} "
            f"({self._affinity.points:.0f}分"
        )
        if days > 0:
            hint += f", 连续互动{days}天"
        hint += ")]\n"

        # 根据等级给出行为指引
        if level.value >= 4:
            hint += "关系很亲密，可以用撒娇、调侃、亲昵的语气。"
        elif level.value >= 2:
            hint += "关系不错，可以用轻松友好的语气，偶尔开玩笑。"
        elif level.value >= 0:
            hint += "关系一般，用礼貌友好但不过分亲密的语气。"
        elif level.value >= -2:
            hint += "关系不好，用冷淡克制的语气，不主动热情。"
        else:
            hint += "关系很差，用敷衍简短的语气，不想多聊。"

        return hint

    # ================================================================
    # 统计
    # ================================================================

    # ================================================================
    # 等级变化事件
    # ================================================================

    def _check_level_change(self, old_points: float) -> Optional[str]:
        """
        检查好感度等级是否发生变化，返回事件描述。

        用于触发特殊对话或解锁功能。
        """
        old_level = self._points_to_level(old_points)
        new_level = self._get_level()

        if old_level == new_level:
            return None

        old_name = LEVEL_CONFIG[old_level]["name"]
        new_name = LEVEL_CONFIG[new_level]["name"]

        if new_level.value > old_level.value:
            event = f"好感度升级: {old_name} → {new_name}"
            logger.info(f"[Affinity] {event}")
        else:
            event = f"好感度降级: {old_name} → {new_name}"
            logger.warning(f"[Affinity] {event}")

        return event

    def _points_to_level(self, pts: float) -> AffinityLevel:
        """纯分数→等级转换。"""
        if pts >= 300: return AffinityLevel.BEST_FRIEND
        if pts >= 150: return AffinityLevel.CLOSE_FRIEND
        if pts >= 80:  return AffinityLevel.GOOD_FRIEND
        if pts >= 40:  return AffinityLevel.FRIEND
        if pts >= 15:  return AffinityLevel.ACQUAINTANCE
        if pts >= 0:   return AffinityLevel.STRANGER
        if pts >= -5:  return AffinityLevel.COLD
        if pts >= -20: return AffinityLevel.UNFAVORABLE
        if pts >= -50: return AffinityLevel.DISLIKE
        if pts >= -100: return AffinityLevel.HOSTILE
        return AffinityLevel.HATRED

    def get_level_change_event(self) -> Optional[str]:
        """获取最近一次等级变化事件（如果有的话）。"""
        if len(self._affinity.history) < 2:
            return None
        last = self._affinity.history[-1]
        old_level = self._points_to_level(last["old"])
        new_level = self._points_to_level(last["new"])
        if old_level != new_level:
            old_name = LEVEL_CONFIG[old_level]["name"]
            new_name = LEVEL_CONFIG[new_level]["name"]
            direction = "升级" if new_level.value > old_level.value else "降级"
            return f"关系{direction}：{old_name} → {new_name}"
        return None

    # ================================================================
    # 好感度影响记忆
    # ================================================================

    def should_soft_forget(self) -> bool:
        """是否应该触发软遗忘（14-21天不联系）。"""
        if self._affinity.is_family or self._affinity.last_interaction == 0:
            return False
        days = (time.time() - self._affinity.last_interaction) / 86400
        return 14 <= days < 21

    def should_hard_forget(self) -> bool:
        """是否应该触发硬遗忘（21+天不联系）。"""
        if self._affinity.is_family or self._affinity.last_interaction == 0:
            return False
        days = (time.time() - self._affinity.last_interaction) / 86400
        return days >= 21

    # ================================================================
    # 手动操作
    # ================================================================

    def set_family(self, is_family: bool) -> None:
        """设置/取消家人状态。"""
        self._affinity.is_family = is_family
        if is_family:
            self._affinity.points = 999999.0
            self._affinity.level = AffinityLevel.FAMILY.value
        self._save()
        logger.info(f"[Affinity] 家人状态: {is_family}")

    def set_points(self, points: float) -> None:
        """手动设置好感度积分。"""
        self._affinity.points = points
        self._affinity.level = self._get_level().value
        self._save()

    # ================================================================
    # 统计
    # ================================================================

    def get_stats(self) -> dict:
        level = self._get_level()
        config = LEVEL_CONFIG[level]
        return {
            "points": round(self._affinity.points, 1),
            "level_name": config["name"],
            "level_value": level.value,
            "emoji": config["emoji"],
            "consecutive_days": self._affinity.consecutive_days,
            "total_interactions": self._affinity.total_interactions,
            "is_family": self._affinity.is_family,
            "history": self._affinity.history[-10:],
            "level_change": self.get_level_change_event(),
        }
