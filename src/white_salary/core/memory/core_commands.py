"""
white_salary/core/memory/core_commands.py

记忆命令系统 — 用户可通过自然语言命令操作记忆。

借鉴v2的memory/core_commands.py（371行）：
  - 正则匹配命令："忘记XXX"/"记住XXX"/"回忆XXX"
  - CRUD执行：调用core_store/long_term/knowledge_graph
  - 反馈生成："好的，已经记住了"

不用LLM，纯正则匹配。

自动发现：导出MODULE供MemoryManager加载。
"""

import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class CommandResult:
    """命令执行结果。"""
    success: bool = False
    command_type: str = ""      # remember/forget/recall/update
    target: str = ""            # 操作目标
    feedback: str = ""          # 反馈文本


# 命令模式
_COMMAND_PATTERNS = [
    # 记住类
    (r"(?:你)?(?:要|得)?记住(?:了)?[，,：:]?\s*(.+)", "remember"),
    (r"(?:帮我)?记(?:一)?下[，,：:]?\s*(.+)", "remember"),
    (r"别忘了[，,：:]?\s*(.+)", "remember"),
    (r"要记得[，,：:]?\s*(.+)", "remember"),

    # 忘记类
    (r"(?:你)?忘(?:记|了|掉)(?:了)?[，,：:]?\s*(.+)", "forget"),
    (r"别记(?:了|着)[，,：:]?\s*(.+)", "forget"),
    (r"(?:把)?(.+?)(?:忘了|忘掉|删掉)(?:吧)?", "forget"),

    # 回忆类
    (r"(?:你)?还记得(.+)(?:吗|么|不)？?", "recall"),
    (r"(?:你)?记(?:不记)?得(.+)(?:吗|么)？?", "recall"),
    (r"(?:帮我)?回忆(?:一下)?(.+)", "recall"),
    (r"(.+?)是什么(?:来着)?", "recall"),

    # 更新类
    (r"(?:其实)?(.+?)(?:不是|应该是|改成|变成)(.+)", "update"),
    (r"(?:我)?(?:之前)?说错了[，,](.+?)(?:应该是|其实是)(.+)", "update"),
]


class MemoryCommandParser:
    """
    记忆命令解析器。

    使用方式:
        parser = MemoryCommandParser()
        cmd = parser.parse("记住我的生日是6月16日")
        # → CommandResult(command_type="remember", target="我的生日是6月16日")
    """

    def __init__(self) -> None:
        self._compiled = [
            (re.compile(p), cmd_type) for p, cmd_type in _COMMAND_PATTERNS
        ]

    def parse(self, text: str) -> Optional[CommandResult]:
        """
        解析用户消息，检测是否是记忆命令。

        Returns:
            CommandResult 或 None（不是命令）
        """
        if not text or len(text) < 4:
            return None

        for pattern, cmd_type in self._compiled:
            match = pattern.search(text)
            if match:
                groups = match.groups()
                target = groups[0].strip() if groups else ""

                if cmd_type == "update" and len(groups) >= 2:
                    old_value = groups[0].strip()
                    new_value = groups[1].strip()
                    return CommandResult(
                        success=True,
                        command_type="update",
                        target=f"{old_value} → {new_value}",
                        feedback=f"好的，已经把「{old_value}」更新为「{new_value}」了",
                    )

                if target and len(target) >= 2:
                    feedback = self._generate_feedback(cmd_type, target)
                    return CommandResult(
                        success=True,
                        command_type=cmd_type,
                        target=target,
                        feedback=feedback,
                    )

        return None

    def _generate_feedback(self, cmd_type: str, target: str) -> str:
        """生成命令反馈文本。"""
        target_short = target[:20]
        if cmd_type == "remember":
            return f"好的，我记住了：{target_short}"
        elif cmd_type == "forget":
            return f"好的，我会把「{target_short}」忘掉的"
        elif cmd_type == "recall":
            return f"让我想想关于「{target_short}」..."
        return ""

    def is_memory_command(self, text: str) -> bool:
        """快速检查是否是记忆命令。"""
        return self.parse(text) is not None


# ================================================================
# 自动发现接口
# ================================================================

class CoreCommandsModule(MemoryModule):
    """记忆命令系统模块 — 自动发现注册。"""
    name = "core_commands"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = MemoryCommandParser()

    def get_context_prompt(self, message: str = "") -> str:
        """检测记忆命令并返回提示。"""
        if not message or not hasattr(self, '_impl'):
            return ""
        cmd = self._impl.parse(message)
        if cmd:
            return f"[记忆命令] 用户发出了{cmd.command_type}命令: {cmd.target}"
        return ""


MODULE = CoreCommandsModule
