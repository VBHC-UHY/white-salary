"""
white_salary/core/interfaces/storage.py

Storage（数据持久化存储）的抽象接口定义。

"持久化"就是把数据保存到硬盘上，关掉程序再打开数据还在。
比如：对话历史记录、长期记忆、配置等都需要持久化。

这个接口定义了两种存储方式：
  1. 键值存储（Key-Value）：像字典一样，用一个key存一个value
  2. 向量存储（Vector）：用于语义搜索（找"意思相近"的内容，长期记忆用）
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


# =============================================================================
# 键值存储接口
# =============================================================================

class KeyValueStorageInterface(ABC):
    """
    键值存储的抽象接口。

    类似于一个持久化的字典：
      - set("user_name", "小明")   → 保存
      - get("user_name")           → 读取，返回 "小明"
      - delete("user_name")        → 删除
    """

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """
        根据key读取数据。

        参数:
            key: 数据的键名

        返回:
            对应的值，如果key不存在则返回None
        """
        ...

    @abstractmethod
    async def set(self, key: str, value: Any) -> None:
        """
        保存一条数据。

        参数:
            key:   数据的键名
            value: 要保存的值（可以是字符串、数字、列表、字典等）
        """
        ...

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """
        删除一条数据。

        参数:
            key: 要删除的键名

        返回:
            True=删除成功，False=key不存在
        """
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """
        检查一个key是否存在。

        参数:
            key: 要检查的键名

        返回:
            True=存在，False=不存在
        """
        ...


# =============================================================================
# 向量存储接口（长期记忆用）
# =============================================================================

@dataclass(frozen=True)
class VectorSearchResult:
    """
    向量搜索的单条结果。

    属性:
        id:       记录的唯一标识
        content:  记录的文本内容
        metadata: 附加元数据（如时间戳、来源等）
        score:    相似度得分（越高越相似）
    """
    id: str
    content: str
    metadata: dict[str, Any]
    score: float


class VectorStorageInterface(ABC):
    """
    向量存储的抽象接口。

    用于长期记忆的语义搜索。
    原理：把文本转成数学向量（一组数字），意思相近的文本向量也相近。
    搜索时：把搜索词转成向量，找到向量最接近的记忆。

    比如：
      存入："今天和小明一起吃了火锅"
      搜索："上次吃饭是什么时候"
      结果：找到 "今天和小明一起吃了火锅"（语义相近）
    """

    @abstractmethod
    async def add(
        self,
        id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        添加一条记忆到向量存储。

        参数:
            id:       唯一标识（用于后续更新或删除）
            content:  文本内容（会自动转成向量）
            metadata: 附加元数据（可选，如时间、标签等）
        """
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[VectorSearchResult]:
        """
        语义搜索：找到和查询语句意思最接近的记忆。

        参数:
            query: 搜索语句
            top_k: 返回最相似的前N条结果

        返回:
            搜索结果列表（按相似度从高到低排列）
        """
        ...

    @abstractmethod
    async def delete(self, id: str) -> bool:
        """
        删除一条记忆。

        参数:
            id: 要删除的记忆的唯一标识

        返回:
            True=删除成功，False=不存在
        """
        ...
