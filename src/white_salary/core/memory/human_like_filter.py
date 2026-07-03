"""
white_salary/core/memory/human_like_filter.py

机器话过滤 — 去掉AI回复中的机器味表达。

借鉴v2的features/human_like.py（219行）：
  - 去掉说教语气（"你应该"/"你需要"/"你必须"）
  - 去掉过多感叹号和重复语气词
  - 去掉图片描述机器话（"图中可以看到"）
  - 去掉过于热情的开头（"哇太棒了！"）
  - 定位：21岁女生微信聊天的说话方式
  - 可选postprocess_llm深度润色

后处理位置：主模型回复生成后，发给用户前。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import re
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# ================================================================
# 2026-07-03 面板升级（批6）：禁用词/禁用句式配置消费
# ================================================================

# config/prompt_templates.json 候选路径：项目根绝对路径优先（不依赖CWD），
# CWD相对路径兜底（本文件位于 src/white_salary/core/memory/，项目根 = parents[4]）
_PROMPT_TEMPLATES_CANDIDATES: list[Path] = [
    Path(__file__).resolve().parents[4] / "config" / "prompt_templates.json",
    Path("config/prompt_templates.json"),
]


def _load_filter_config(config_path: Optional[Path] = None) -> dict:
    """
    2026-07-03 面板升级（批6）：实时读取 config/prompt_templates.json 的
    human_like_filter 节（照 affinity_persona.py 的每次读取模式，改完即生效）。

    设置面板"人设管理"页保存的禁用词(banned_words)/禁用句式(banned_lecture)
    从此真实被过滤器执行（依据 docs/panel-audit-2026-07-03/panel-persona.json：
    原先落盘后全仓库零读取）。

    Args:
        config_path: 显式指定配置文件路径（主要供单测注入）；None=默认候选路径

    Returns:
        human_like_filter 配置节 dict；文件/节缺失或非法时返回空 dict
        （= 回退纯硬编码现状）
    """
    candidates = [config_path] if config_path is not None else _PROMPT_TEMPLATES_CANDIDATES
    for path in candidates:
        try:
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            section = data.get("human_like_filter", {})
            return section if isinstance(section, dict) else {}
        except Exception as e:
            logger.debug(f"[HumanLike] 过滤器配置读取失败，回退硬编码规则: {e}")
            return {}
    return {}


# 禁止的机器话模式（正则→替换）
_ROBOTIC_PATTERNS = [
    # 说教语气
    (r"你(?:应该|需要|必须|得)(.{2,20})", None),  # 标记但不自动替换
    # 过多感叹号（3个以上→2个）
    (r"[！!]{3,}", "！！"),
    # 重复语气词（哈哈哈哈→哈哈）
    (r"(哈){4,}", "哈哈哈"),
    (r"(嗯){3,}", "嗯嗯"),
    (r"(啊){3,}", "啊啊"),
    (r"(呜){3,}", "呜呜"),
    # 过于热情的开头
    (r"^哇[！!~～]*\s*", ""),
    (r"^天哪[！!~～]*\s*", ""),
    # 图片描述机器话
    (r"(?:图中|图片中|画面中)?(?:可以看到|展示了|呈现了)", ""),
    (r"(?:从图片|从图中)(?:可以|能够)?(?:看出|看到|观察到)", ""),
    # AI自我暴露
    (r"作为(?:一个)?(?:AI|人工智能|语言模型|助手)", ""),
    (r"我(?:是|只是)(?:一个)?(?:AI|人工智能|语言模型)", ""),
    # 过于正式的连接词
    (r"(?:首先|其次|最后|总之|综上所述)[，,]", ""),
    # 波浪号全部删除（正常人聊天不用这个）
    (r"[~～]+", ""),
    # "呀"字删除（语气太奇怪）
    (r"呀", ""),
]

# 禁止的句式（检测到就标记，让postprocess_llm修正）
_LECTURE_PATTERNS = [
    r"你(?:应该|需要|必须|得)(?:好好|认真|努力)",
    r"(?:建议你|推荐你|我觉得你应该)",
    r"(?:第一|首先).+(?:第二|其次).+(?:第三|最后)",  # 列举式说教
]

# 检测重复开头（最近5条回复的开头词）
_MAX_TRACKED_OPENINGS = 5


class HumanLikeFilter:
    """
    机器话过滤器。

    使用方式:
        f = HumanLikeFilter()
        cleaned = f.filter_response("哇！！！太棒了！你应该好好学习！！！")
        # → "太棒了！你可以好好学习！！"
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        """
        Args:
            config_path: 2026-07-03 面板升级（批6）：显式指定 prompt_templates.json
                         路径（主要供单测注入）；None=默认候选路径（项目根/CWD）
        """
        self._compiled = [(re.compile(p), r) for p, r in _ROBOTIC_PATTERNS]
        self._lecture_compiled = [re.compile(p) for p in _LECTURE_PATTERNS]
        self._recent_openings: list[str] = []
        self._config_path: Optional[Path] = config_path

    def _get_config_rules(self) -> tuple[list["re.Pattern[str]"], list["re.Pattern[str]"]]:
        """
        2026-07-03 面板升级（批6）：把配置节编译成正则规则（每次调用实时读取）。

        - banned_words:   每个词按字面量编译成删除正则（filter_response 里执行删除）
        - banned_lecture: 尝试按正则编译并入说教句式检测；非法正则退化为字面量匹配

        Returns:
            (banned_words 删除正则列表, banned_lecture 检测正则列表)；
            配置缺失时都为空列表（= 纯硬编码现状）
        """
        section = _load_filter_config(self._config_path)
        word_patterns: list["re.Pattern[str]"] = []
        raw_words = section.get("banned_words", [])
        if isinstance(raw_words, list):
            for word in raw_words:
                text = str(word).strip()
                if text:
                    word_patterns.append(re.compile(re.escape(text)))

        lecture_patterns: list["re.Pattern[str]"] = []
        raw_lecture = section.get("banned_lecture", [])
        if isinstance(raw_lecture, list):
            for pat in raw_lecture:
                text = str(pat).strip()
                if not text:
                    continue
                try:
                    lecture_patterns.append(re.compile(text))
                except re.error:
                    # 用户输入的不是合法正则 → 按字面量匹配，不让配置错误炸掉过滤器
                    lecture_patterns.append(re.compile(re.escape(text)))
        return word_patterns, lecture_patterns

    def filter_response(self, text: str) -> str:
        """
        过滤机器话（纯正则，快速）。

        Returns:
            过滤后的文本
        """
        if not text or len(text) < 3:
            return text

        result = text

        # 应用正则替换
        for pattern, replacement in self._compiled:
            if replacement is not None:
                result = pattern.sub(replacement, result)

        # 2026-07-03 面板升级（批6）：应用面板配置的禁用词（逐词删除；
        # 配置缺失时列表为空，行为与原硬编码完全一致）
        banned_word_patterns, _ = self._get_config_rules()
        for pattern in banned_word_patterns:
            result = pattern.sub("", result)

        # 清理多余空格
        result = re.sub(r'\s{2,}', ' ', result).strip()

        return result

    def check_lecture_tone(self, text: str) -> bool:
        """检测是否有说教语气（硬编码句式 + 面板配置的禁用句式）。"""
        for p in self._lecture_compiled:
            if p.search(text):
                return True
        # 2026-07-03 面板升级（批6）：面板配置的 banned_lecture 并入检测
        _, lecture_patterns = self._get_config_rules()
        for p in lecture_patterns:
            if p.search(text):
                return True
        return False

    def get_postprocess_prompt(self, text: str) -> Optional[str]:
        """
        生成给postprocess_llm的修正提示。

        如果检测到严重的机器话，返回修正提示让postprocess_llm重新润色。
        轻微问题直接正则处理，不调LLM。

        Returns:
            修正提示（None=不需要LLM修正）
        """
        issues = []

        if self.check_lecture_tone(text):
            issues.append("有说教语气，改成朋友间的建议语气")

        # 检测列举式回复
        if re.search(r"(?:1[.、)]|①|首先).+(?:2[.、)]|②|其次)", text, re.DOTALL):
            issues.append("像在写列表，改成自然的口语表达")

        # 检测过长（超过200字可能太正式）
        if len(text) > 300:
            issues.append("回复太长了，精简一些，像微信聊天一样")

        # 好感度语气检查
        affinity_hint = self._get_affinity_tone_hint()
        if affinity_hint:
            issues.append(affinity_hint)

        if not issues:
            return None

        return (
            f"请修改以下回复，问题：{'; '.join(issues)}。"
            f"要求：像21岁女生跟朋友微信聊天一样说话，自然、简短、有个性。"
            f"只返回修改后的文本，不要其他解释。\n\n原文：{text}"
        )

    @staticmethod
    def _get_affinity_tone_hint(user_id: str = "desktop") -> str:
        """根据好感度返回语气调整提示。"""
        try:
            from white_salary.core.affinity.manager import AffinityManager
            aff = AffinityManager.get_for_user(user_id)
            stats = aff.get_stats()
            lv = stats.get("level_value", 0)
            if stats.get("is_family") or lv >= 4:
                return ""  # 亲密的人不需要调
            elif lv <= -2:
                return "对方好感度很低，语气要冷淡简短"
        except Exception:
            pass
        return ""

    def track_opening(self, text: str) -> Optional[str]:
        """
        追踪回复开头，检测是否总是用同一个词开头。

        Returns:
            警告（None=没问题）
        """
        if not text or len(text) < 5:
            return None

        opening = text[:4]
        self._recent_openings.append(opening)
        if len(self._recent_openings) > _MAX_TRACKED_OPENINGS:
            self._recent_openings = self._recent_openings[-_MAX_TRACKED_OPENINGS:]

        # 检查是否有3次以上相同开头
        from collections import Counter
        counts = Counter(self._recent_openings)
        for word, count in counts.most_common(1):
            if count >= 3:
                return f"最近{count}条回复都以「{word}」开头，换个开头"
        return None


# ================================================================
# 自动发现接口
# ================================================================

class HumanLikeModule(MemoryModule):
    """机器话过滤模块 — 自动发现注册。"""
    name = "human_like_filter"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = HumanLikeFilter()

    def get_context_prompt(self, message: str = "") -> str:
        """注入防人机提醒。"""
        return ""  # 过滤在后处理阶段，不在上下文注入

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        """追踪AI回复的开头词。"""
        if ai_reply and hasattr(self, '_impl'):
            warning = self._impl.track_opening(ai_reply)
            # warning会在下次get_context_prompt时使用
            self._opening_warning = warning

    def get_context_prompt(self, message: str = "") -> str:
        if hasattr(self, '_opening_warning') and self._opening_warning:
            return f"[回复提醒] {self._opening_warning}"
        return ""


MODULE = HumanLikeModule
