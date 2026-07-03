"""
white_salary/adapters/tools/task_planner.py

任务规划器 — 把复杂任务拆解为多个步骤并自动执行。

流程：
  1. 用户提出一个复杂任务（如"帮我搜索Python异步编程的教程并总结"）
  2. LLM把任务拆解为多个步骤
  3. 按顺序执行每个步骤（调用工具）
  4. 汇总所有步骤的结果
  5. 返回最终结果

参考: OpenManus / Manus AI 的任务执行模式
"""

import json
from typing import Optional

from loguru import logger

from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, MessageRole


PLANNER_PROMPT = """你是一个任务规划助手。用户会给你一个任务，你要把它拆解成具体的执行步骤。

可用的工具：
- web_search: 搜索互联网（参数: query）
- fetch_webpage: 获取网页内容（参数: url）
- calculator: 数学计算（参数: expression）
- get_current_time: 获取当前时间
- execute_code: 执行Python代码（参数: code）

回复格式（JSON数组）：
[
  {"step": 1, "tool": "web_search", "args": {"query": "搜索词"}, "description": "搜索相关信息"},
  {"step": 2, "tool": "fetch_webpage", "args": {"url": "xxx"}, "description": "获取详细内容"},
  {"step": 3, "tool": null, "args": {}, "description": "总结所有信息并回复用户"}
]

注意：
- 步骤要具体可执行
- 最后一步通常是"总结"（tool=null）
- 最多5个步骤
- 如果任务很简单不需要工具，返回 []
"""


class TaskPlanner:
    """
    任务规划器。

    使用LLM拆解任务，按步骤执行。
    """

    def __init__(self, llm: Optional[LLMInterface] = None) -> None:
        self._llm = llm

    async def plan(self, task: str) -> list[dict]:
        """
        让LLM把任务拆解为执行步骤。

        Args:
            task: 任务描述

        Returns:
            步骤列表
        """
        if not self._llm:
            return []

        try:
            response = await self._llm.chat_completion(
                messages=[
                    Message(role=MessageRole.SYSTEM, content=PLANNER_PROMPT),
                    Message(role=MessageRole.USER, content=f"任务: {task}"),
                ],
                temperature=0.3,
                max_tokens=500,
            )

            # 解析JSON
            text = response.strip()
            start = text.find("[")
            end = text.rfind("]")
            if start == -1 or end == -1:
                return []

            steps = json.loads(text[start:end + 1])
            if not isinstance(steps, list):
                return []

            logger.info(f"[Planner] 拆解为 {len(steps)} 个步骤")
            return steps

        except Exception as e:
            logger.warning(f"[Planner] 规划失败: {e}")
            return []

    async def execute_plan(
        self,
        steps: list[dict],
        tool_executor,  # ToolRegistry.execute
    ) -> str:
        """
        按步骤执行计划。

        Args:
            steps: plan()返回的步骤列表
            tool_executor: 工具执行函数（async def execute(name, args) -> str）

        Returns:
            所有步骤的执行结果汇总
        """
        if not steps:
            return ""

        results = []

        for step in steps:
            step_num = step.get("step", "?")
            tool = step.get("tool")
            args = step.get("args", {})
            desc = step.get("description", "")

            logger.debug(f"[Planner] 执行步骤 {step_num}: {desc}")

            if tool:
                try:
                    result = await tool_executor(tool, args)
                    results.append(f"步骤{step_num} ({desc}): {result}")
                except Exception as e:
                    results.append(f"步骤{step_num} ({desc}): [执行失败] {e}")
            else:
                results.append(f"步骤{step_num}: {desc}")

        return "\n\n".join(results)
