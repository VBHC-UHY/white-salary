"""
white_salary/adapters/game/base_game.py

游戏AI基类 — 所有游戏适配器的抽象基类。

每个游戏继承这个基类，实现自己的决策逻辑。
使用策略模式，不同游戏用不同策略，统一接口。

支持的游戏类型：
  - Minecraft（通过MCP或API控制）
  - 视觉小说（屏幕截图→决策→键盘操作）
  - 通用游戏（截屏理解→LLM决策→按键执行）
"""

from abc import ABC, abstractmethod
from typing import Optional

from loguru import logger


class GameAction:
    """游戏操作。"""
    def __init__(self, action_type: str, params: dict = None):
        self.action_type = action_type  # key_press / mouse_click / text_input / wait
        self.params = params or {}

    def __repr__(self):
        return f"GameAction({self.action_type}, {self.params})"


class BaseGameAdapter(ABC):
    """
    游戏AI基类。

    所有游戏适配器必须实现：
      - observe(): 观察当前游戏状态
      - decide(): 根据状态决定下一步操作
      - execute(): 执行操作
    """

    name: str = "base_game"
    description: str = ""

    @abstractmethod
    async def observe(self) -> dict:
        """
        观察当前游戏状态。

        Returns:
            游戏状态描述字典（如截屏描述、血量、位置等）
        """
        ...

    @abstractmethod
    async def decide(self, state: dict) -> list[GameAction]:
        """
        根据当前状态决定操作。

        Args:
            state: observe()返回的状态

        Returns:
            要执行的操作列表
        """
        ...

    @abstractmethod
    async def execute(self, actions: list[GameAction]) -> bool:
        """
        执行操作。

        Args:
            actions: decide()返回的操作列表

        Returns:
            是否执行成功
        """
        ...

    async def play_one_step(self) -> str:
        """执行一步完整的游戏循环：观察→决策→执行。"""
        try:
            state = await self.observe()
            actions = await self.decide(state)
            success = await self.execute(actions)
            return f"观察→决策({len(actions)}个操作)→{'成功' if success else '失败'}"
        except Exception as e:
            logger.error(f"[Game:{self.name}] 执行失败: {e}")
            return f"游戏操作失败: {e}"

    async def is_available(self) -> bool:
        """检查游戏是否可用。"""
        return False
