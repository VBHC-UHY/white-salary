"""
white_salary/core/memory/module_base.py

记忆功能模块基类 — 所有可自动发现的记忆功能模块继承这个。

自动发现机制：
  MemoryManager扫描memory/目录下所有.py文件
  找到继承MemoryModule的类 → 自动实例化 → 注册到manager

新增功能模块只需：
  1. 创建.py文件
  2. 写一个继承MemoryModule的类
  3. 不用改manager.py
"""

from abc import ABC


class MemoryModule(ABC):
    """
    记忆功能模块基类。

    子类需要实现：
      - name: 模块名（str）
      - init(data_dir, **kwargs): 初始化

    可选实现：
      - get_context_prompt(message): 返回注入system prompt的文本
      - on_message(user_msg, ai_reply): 每次对话后调用
      - on_session_start(): 会话开始时调用
      - on_session_end(): 会话结束时调用
    """

    name: str = "unnamed"

    def init(self, data_dir: str = "data/memory", **kwargs) -> None:
        """初始化模块。"""
        pass

    def get_context_prompt(self, message: str = "",
                          user_id: str = "desktop",
                          is_group: bool = False) -> str:
        """返回要注入system prompt的上下文文本。"""
        return ""

    def on_message(self, user_msg: str = "", ai_reply: str = "",
                   user_id: str = "desktop",
                   is_group: bool = False) -> None:
        """每次对话后调用。"""
        pass

    def on_session_start(self) -> None:
        """会话开始时调用。"""
        pass

    def on_session_end(self) -> None:
        """会话结束时调用。"""
        pass
