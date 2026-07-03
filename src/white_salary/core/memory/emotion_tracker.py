"""
white_salary/core/memory/emotion_tracker.py

情感追踪器 — 记录用户和AI的情绪变化历史。

功能：
  - 追踪每次对话的情绪状态
  - 计算实时心情分数（0-100）
  - 记录情绪变化趋势
  - 提供情绪摘要注入对话上下文

6种基础情绪：
  开心(happy) / 生气(angry) / 难过(sad)
  惊讶(surprised) / 害羞(shy) / 调皮(playful)

心情分数规则：
  - 基准值80分（正常/平静）
  - 正面情绪加分，负面情绪减分
  - 随时间自然回归80分
"""

import json
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from loguru import logger


# ================================================================
# 2026-07-03 面板升级（批6）：情绪→表情映射的配置文件消费
# ================================================================

# 配置文件候选路径：项目根绝对路径优先（不依赖CWD），CWD相对路径兜底
# （本文件位于 src/white_salary/core/memory/，项目根 = parents[4]）
_EXPRESSION_MAP_CANDIDATES: list[Path] = [
    Path(__file__).resolve().parents[4] / "config" / "expression_map.json",
    Path("config/expression_map.json"),
]


def _load_expression_map_overrides(
    candidates: Optional[list[Path]] = None,
) -> dict[str, dict]:
    """
    2026-07-03 面板升级（批6）：实时读取 config/expression_map.json 的表情映射覆盖项。

    照 affinity_persona.py 的"每次调用实时读取"模式（文件很小无性能问题），
    设置面板"表情动作"页保存的拖拽映射从此真实生效、改完即生效
    （依据 docs/panel-audit-2026-07-03/panel-expressions.json：原先只写
    localStorage 无任何读取方）。

    文件格式：{"happy": {"expression": "happy", "motion_group": "idle",
    "mouth_form": 0.3}, ...}——按情绪逐条覆盖硬编码 EXPRESSION_MAP，
    文件里没有的情绪继续用硬编码默认值。

    Args:
        candidates: 显式指定候选路径列表（主要供单测注入）；None=默认候选

    Returns:
        合法的覆盖映射（情绪 → 表情命令 dict）；文件不存在/非法时返回空 dict
        （= 回退纯硬编码现状）
    """
    paths = candidates if candidates is not None else _EXPRESSION_MAP_CANDIDATES
    for path in paths:
        try:
            if not path.exists():
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                logger.warning(
                    f"[Emotion] 表情映射配置格式非法（应为JSON对象），回退硬编码映射: {path}"
                )
                return {}
            overrides: dict[str, dict] = {}
            for emotion, cmd in raw.items():
                # 合法条目 = dict 且至少含字符串类型的 expression 字段
                if isinstance(cmd, dict) and isinstance(cmd.get("expression"), str):
                    overrides[str(emotion)] = cmd
            return overrides
        except Exception as e:
            logger.warning(f"[Emotion] 表情映射配置读取失败，回退硬编码映射: {e}")
            return {}
    return {}


# 情绪类型及其对心情分数的影响
EMOTION_EFFECTS = {
    "happy":     +5,
    "excited":   +8,
    "grateful":  +6,
    "touched":   +7,
    "playful":   +3,
    "shy":       +2,
    "calm":       0,
    "neutral":    0,
    "bored":     -2,
    "confused":  -1,
    "sad":       -5,
    "angry":     -6,
    "frustrated":-4,
    "hurt":      -7,
    "scared":    -3,
    "surprised":  +1,
}

# 心情分数对应的状态描述
MOOD_DESCRIPTIONS = {
    (90, 101): ("非常开心", "😄", "#2ecc71"),
    (80, 90):  ("心情不错", "😊", "#3498db"),
    (60, 80):  ("还行吧", "😐", "#f39c12"),
    (40, 60):  ("有点低落", "😔", "#e67e22"),
    (0, 40):   ("心情很差", "😢", "#e74c3c"),
}

BASELINE_MOOD = 80  # 基准心情分数
RECOVERY_RATE = 2.0  # 每小时回归速度（向基准值靠拢）

# 情绪惯性系数（借鉴v2的情绪惯性模型）
# 从负面转正面需要更多正面刺激（惯性大，不容易开心起来）
# 从正面转负面则更快（惯性小，容易因小事心情变差）
INERTIA_COEFFICIENTS = {
    ("negative", "positive"): 0.5,   # 难过→开心：只有50%效果（需要更多正面）
    ("positive", "negative"): 1.2,   # 开心→难过：120%效果（容易因小事变差）
    ("negative", "neutral"):  0.7,   # 难过→平静：70%效果
    ("positive", "neutral"):  0.9,   # 开心→平静：正常
    ("neutral", "positive"):  1.0,   # 平静→开心：正常
    ("neutral", "negative"):  1.0,   # 平静→难过：正常
}

def _get_valence(emotion: str) -> str:
    """获取情绪的效价（正面/负面/中性）。"""
    effect = EMOTION_EFFECTS.get(emotion, 0)
    if effect > 0:
        return "positive"
    elif effect < 0:
        return "negative"
    return "neutral"


@dataclass
class EmotionRecord:
    """一条情绪记录。"""
    emotion: str        # 情绪类型
    intensity: float    # 强度 0.0-1.0
    mood_score: float   # 当时的心情分数
    timestamp: float    # 时间戳
    trigger: str = ""   # 触发原因


class EmotionTracker:
    """
    情感追踪器。

    实时追踪情绪状态，记录变化历史，提供心情分数。

    2026-07-03 审计修复（批5）：进程级共享实例（按 data_dir 归一化路径缓存）。
    审计实锤：settings_api 轮询端点与散点代码（condition_engine / plugins/context /
    natural_expression）各自 new 本类，多份实例读写同一 emotion_history.json，
    既刷 __init__ 日志又相互覆盖心情状态。改为同一 data_dir 全进程只保留一个
    实例，所有散点自动共享同一情绪状态。
    """

    # 进程级共享实例注册表：归一化路径 -> 实例
    _shared_instances: dict[str, "EmotionTracker"] = {}
    _shared_lock: threading.Lock = threading.Lock()

    def __new__(cls, data_dir: str = "data/memory") -> "EmotionTracker":
        # 2026-07-03 审计修复（批5）：按归一化路径复用实例，禁止整套重实例化
        key = str(Path(data_dir).resolve())
        with cls._shared_lock:
            inst = cls._shared_instances.get(key)
            if inst is None:
                inst = super().__new__(cls)
                cls._shared_instances[key] = inst
        return inst

    def __init__(self, data_dir: str = "data/memory") -> None:
        # 2026-07-03 审计修复（批5）：命中共享实例时跳过重复初始化
        # （标志在初始化末尾才置位，若上次初始化中途抛异常会自动重试）
        if getattr(self, "_shared_inited", False):
            return

        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._data_path = self._data_dir / "emotion_history.json"

        self._mood_score = BASELINE_MOOD
        self._current_emotion = "neutral"
        self._last_update = time.time()
        self._history: list[dict] = []

        self._load()
        logger.debug(f"[Emotion] 当前心情: {self._mood_score:.0f}分 ({self._current_emotion})")
        self._shared_inited: bool = True

    def record_emotion(self, emotion: str, intensity: float = 0.5, trigger: str = "", user_id: str = "desktop") -> None:
        """
        记录一次情绪变化。

        Args:
            emotion: 情绪类型（happy/sad/angry等）
            intensity: 强度 0.0-1.0
            trigger: 触发原因描述
            user_id: 触发者的用户id（好感度系数/主人衰减系数依据）
        """
        # 先执行时间回归
        self._apply_recovery()

        # 计算心情变化（应用情绪惯性系数 × 好感度系数 × 主人衰减系数）
        effect = EMOTION_EFFECTS.get(emotion, 0)
        old_valence = _get_valence(self._current_emotion)
        new_valence = _get_valence(emotion)
        inertia = INERTIA_COEFFICIENTS.get((old_valence, new_valence), 1.0)
        # 好感度影响情绪反应强度（好感高→反应更大）
        affinity_mult = self._get_affinity_emotion_multiplier(user_id)
        # 2026-07-02 审计修复（批4）：情绪串味缓解。保持"白只有一个心情"的设计，
        # 但非主人/非家人用户对全局心情的影响乘0.3衰减系数（主人/家人保持1.0），
        # 避免QQ陌生人的情绪全额改写白对主人的心情。
        owner_damp = self._get_owner_dampening(user_id)
        change = effect * intensity * inertia * affinity_mult * owner_damp
        self._mood_score = max(0, min(100, self._mood_score + change))
        self._current_emotion = emotion
        self._last_update = time.time()

        # 记录历史
        # 2026-07-02 审计修复（批4）：history记录补user_id字段（审计：旧记录无该字段，
        # 无法追溯情绪由谁触发；读旧文件时在_load里兼容补空值）
        record = {
            "emotion": emotion,
            "intensity": round(intensity, 2),
            "mood_score": round(self._mood_score, 1),
            "change": round(change, 1),
            "timestamp": self._last_update,
            "time": time.strftime("%Y-%m-%d %H:%M"),
            "trigger": trigger,
            "user_id": user_id,
        }
        self._history.append(record)

        # 只保留最近100条
        if len(self._history) > 100:
            self._history = self._history[-100:]

        self._save()

        if change != 0:
            logger.debug(
                f"[Emotion] {emotion}(强度{intensity:.1f}): "
                f"心情 {change:+.1f} → {self._mood_score:.0f}分"
            )

    @staticmethod
    def _get_owner_dampening(user_id: str = "desktop") -> float:
        """
        2026-07-02 审计修复（批4）：主人衰减系数。

        主人（desktop / conf.yaml qq.family_qq[0]）和家人对全局心情影响为1.0；
        其他用户（QQ陌生人等）的影响乘0.3衰减，缓解多用户情绪串味。

        Returns:
            1.0（主人/家人）或 0.3（其他用户）
        """
        # 桌面端历史身份始终视为主人（不依赖任何外部模块，最快路径）
        if user_id == "desktop":
            return 1.0
        # 主人统一id判定（惰性导入避免与manager循环导入）
        try:
            from white_salary.core.memory.manager import is_owner_user
            if is_owner_user(user_id):
                return 1.0
        except Exception as e:
            logger.debug(f"[Emotion] 主人身份判定失败，按非主人处理: {e}")
        # 家人判定（好感度档案里的is_family）
        try:
            from white_salary.core.affinity.manager import AffinityManager
            stats = AffinityManager.get_for_user(user_id).get_stats()
            if stats.get("is_family"):
                return 1.0
        except Exception as e:
            logger.debug(f"[Emotion] 家人身份判定失败，按非家人处理: {e}")
        return 0.3

    @staticmethod
    def _get_affinity_emotion_multiplier(user_id: str = "desktop") -> float:
        """好感度→情绪反应系数。好感高反应更大，好感低反应弱。"""
        try:
            from white_salary.core.affinity.manager import AffinityManager
            aff = AffinityManager.get_for_user(user_id)
            stats = aff.get_stats()
            if stats.get("is_family"):
                return 1.5  # 家人的话影响最大
            lv = stats.get("level_value", 0)
            if lv >= 3:
                return 1.3  # 好朋友
            elif lv >= 1:
                return 1.0  # 认识的人
            elif lv <= -2:
                return 0.5  # 反感的人影响小
            return 1.0
        except Exception:
            return 1.0

    def _apply_recovery(self) -> None:
        """随时间自然回归基准心情。"""
        now = time.time()
        hours = (now - self._last_update) / 3600
        if hours < 0.1:
            return

        diff = BASELINE_MOOD - self._mood_score
        recovery = min(abs(diff), RECOVERY_RATE * hours)
        if diff > 0:
            self._mood_score += recovery
        elif diff < 0:
            self._mood_score -= recovery

    @property
    def mood_score(self) -> float:
        """当前心情分数（0-100）。"""
        self._apply_recovery()
        return round(self._mood_score, 1)

    @property
    def current_emotion(self) -> str:
        """当前情绪。"""
        return self._current_emotion

    def get_mood_description(self) -> tuple[str, str, str]:
        """获取心情描述（文字, emoji, 颜色）。"""
        score = self.mood_score
        for (low, high), (desc, emoji, color) in MOOD_DESCRIPTIONS.items():
            if low <= score < high:
                return desc, emoji, color
        return "正常", "😊", "#3498db"

    def get_context_hint(self) -> str:
        """获取注入LLM上下文的情绪提示。"""
        desc, emoji, _ = self.get_mood_description()
        return f"[当前心情: {desc} {emoji} ({self.mood_score:.0f}分)]"

    def get_recent_history(self, limit: int = 10) -> list[dict]:
        """获取最近的情绪变化记录。"""
        return self._history[-limit:]

    # ================================================================
    # 情绪→Live2D表情映射
    # ================================================================

    # 默认映射：情绪→Live2D表情/动作参数
    EXPRESSION_MAP = {
        "happy":     {"expression": "happy",    "motion_group": "idle", "mouth_form": 0.3},
        "excited":   {"expression": "happy",    "motion_group": "tap", "mouth_form": 0.5},
        "grateful":  {"expression": "happy",    "motion_group": "idle", "mouth_form": 0.2},
        "touched":   {"expression": "shy",      "motion_group": "idle", "mouth_form": 0.1},
        "playful":   {"expression": "happy",    "motion_group": "tap", "mouth_form": 0.4},
        "shy":       {"expression": "shy",      "motion_group": "idle", "mouth_form": 0.1},
        "calm":      {"expression": "default",  "motion_group": "idle", "mouth_form": 0.0},
        "neutral":   {"expression": "default",  "motion_group": "idle", "mouth_form": 0.0},
        "bored":     {"expression": "default",  "motion_group": "idle", "mouth_form": 0.0},
        "confused":  {"expression": "surprised","motion_group": "idle", "mouth_form": 0.2},
        "sad":       {"expression": "sad",      "motion_group": "idle", "mouth_form": 0.0},
        "angry":     {"expression": "angry",    "motion_group": "tap", "mouth_form": 0.3},
        "frustrated":{"expression": "angry",    "motion_group": "idle", "mouth_form": 0.2},
        "hurt":      {"expression": "sad",      "motion_group": "idle", "mouth_form": 0.0},
        "scared":    {"expression": "surprised","motion_group": "idle", "mouth_form": 0.1},
        "surprised": {"expression": "surprised","motion_group": "tap", "mouth_form": 0.4},
    }

    def get_expression_command(self) -> dict:
        """
        获取当前情绪对应的Live2D表情命令。

        2026-07-03 面板升级（批6）：优先用 config/expression_map.json 的用户自定义
        映射（实时读取，改完即生效），文件缺失/非法或未覆盖当前情绪时回退
        硬编码 EXPRESSION_MAP（= 原行为）。

        Returns:
            {"expression": "happy", "motion_group": "idle", "mouth_form": 0.3}
        """
        effective: dict[str, dict] = dict(self.EXPRESSION_MAP)
        effective.update(_load_expression_map_overrides())
        return effective.get(
            self._current_emotion,
            effective.get("neutral", self.EXPRESSION_MAP["neutral"]),
        )

    # ================================================================
    # 情绪影响语音参数
    # ================================================================

    def get_tts_modifiers(self) -> dict:
        """
        根据当前情绪返回TTS参数调整值。

        Returns:
            {"speed_factor": 1.0, "pitch_shift": 0} 等
        """
        score = self.mood_score
        emotion = self._current_emotion

        # 高兴时语速稍快、音调稍高
        if emotion in ("happy", "excited", "playful"):
            return {"speed_factor": 1.1, "pitch_hint": "slightly_higher"}
        # 难过时语速稍慢
        elif emotion in ("sad", "hurt"):
            return {"speed_factor": 0.9, "pitch_hint": "slightly_lower"}
        # 生气时语速稍快、语气更重
        elif emotion in ("angry", "frustrated"):
            return {"speed_factor": 1.05, "pitch_hint": "slightly_lower"}
        # 害羞时语速稍慢
        elif emotion in ("shy", "touched"):
            return {"speed_factor": 0.95, "pitch_hint": "normal"}

        return {"speed_factor": 1.0, "pitch_hint": "normal"}

    # ================================================================
    # 情绪记忆联动
    # ================================================================

    def should_store_to_memory(self) -> bool:
        """
        判断当前情绪是否足够强烈，值得存入长期记忆。

        极端情绪（心情分数偏离基准太多）自动触发记忆存储。
        """
        return abs(self.mood_score - BASELINE_MOOD) >= 15

    def get_memory_content(self) -> str:
        """生成要存入长期记忆的情绪描述。"""
        desc, emoji, _ = self.get_mood_description()
        return f"[情绪记录] {desc}{emoji} (心情{self.mood_score:.0f}分, 情绪:{self._current_emotion})"

    # ================================================================
    # 统计
    # ================================================================

    def get_stats(self) -> dict:
        """获取情感统计。"""
        desc, emoji, color = self.get_mood_description()
        return {
            "mood_score": self.mood_score,
            "mood_description": desc,
            "mood_emoji": emoji,
            "mood_color": color,
            "current_emotion": self._current_emotion,
            "total_records": len(self._history),
            "expression": self.get_expression_command(),
            "tts_modifiers": self.get_tts_modifiers(),
        }

    def _save(self) -> None:
        data = {
            "mood_score": self._mood_score,
            "current_emotion": self._current_emotion,
            "last_update": self._last_update,
            "history": self._history,
        }
        with open(self._data_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                with open(self._data_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._mood_score = data.get("mood_score", BASELINE_MOOD)
                self._current_emotion = data.get("current_emotion", "neutral")
                self._last_update = data.get("last_update", time.time())
                self._history = data.get("history", [])
                # 2026-07-02 审计修复（批4）：兼容旧文件——旧history记录无user_id字段，
                # 统一补空串占位（""表示来源未知，不冒充desktop/主人）
                for _rec in self._history:
                    if isinstance(_rec, dict):
                        _rec.setdefault("user_id", "")
            except Exception as e:
                # 2026-07-02 审计修复（批4）：原为裸except pass静默重置心情数据，改为醒目告警
                logger.warning(
                    f"[Emotion] 情绪历史加载失败（{self._data_path}），"
                    f"使用默认心情状态: {e}"
                )
