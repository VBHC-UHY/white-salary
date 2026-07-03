"""
white_salary/core/interfaces/llm.py

LLM（大语言模型）的抽象接口定义。

这个文件定义了所有LLM适配器必须实现的方法。
核心代码只依赖这个接口，不依赖具体的LLM实现（如OpenAI、Claude等）。
这样做的好处是：换LLM引擎只需要写新的适配器，核心代码不用改。
"""

from abc import ABC, abstractmethod
from typing import AsyncGenerator

from white_salary.core.interfaces.types import Message, ToolCall, ToolResult


class LLMInterface(ABC):
    """
    LLM（大语言模型）的抽象接口。

    所有LLM适配器（OpenAI、Claude、Ollama等）都必须继承这个类，
    并实现下面定义的所有方法。
    """

    @abstractmethod
    async def chat_completion(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """
        发送对话请求，获取完整回复。

        这是最基本的对话方法。把整个对话历史发给LLM，等它全部想完再一次性返回。
        适用于不需要实时显示的场景。

        参数:
            messages:    对话历史记录列表（按时间顺序排列）
            temperature: 创造性程度（0.0=最保守确定，1.0=最随机创意）
            max_tokens:  回复的最大长度（token数，1个中文字约等于1-2个token）

        返回:
            LLM生成的回复文本
        """
        ...

    @abstractmethod
    async def chat_completion_stream(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        """
        发送对话请求，以流式方式逐字返回回复。

        LLM一边想一边返回，不用等全部想完。
        这样用户能更快看到/听到回复的开头，体验更好。
        实时对话场景必须用这个方法。

        参数:
            messages:    对话历史记录列表
            temperature: 创造性程度
            max_tokens:  回复的最大长度

        返回:
            异步生成器，逐段（chunk）返回LLM生成的文本片段
        """
        ...
        # yield 是为了让 Python 识别这是一个 async generator
        yield ""  # pragma: no cover

    @abstractmethod
    async def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> tuple[str, list[ToolCall]]:
        """
        发送对话请求，支持工具调用。

        LLM在回复时可能会决定调用某个工具（比如搜索网页、执行代码）。
        这个方法会返回LLM的文本回复和它想调用的工具列表。

        参数:
            messages:    对话历史记录列表
            tools:       可用工具的描述列表（JSON Schema格式）
            temperature: 创造性程度
            max_tokens:  回复的最大长度

        返回:
            一个元组：(文本回复, 工具调用列表)
            - 文本回复可能为空（如果LLM只想调用工具）
            - 工具调用列表可能为空（如果LLM只想回复文字）
        """
        ...

    @abstractmethod
    async def process_tool_results(
        self,
        messages: list[Message],
        tool_results: list[ToolResult],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """
        处理工具调用的返回结果，生成最终回复。

        当工具执行完毕后，把结果告诉LLM，让它根据工具返回的信息生成最终回复。

        参数:
            messages:     对话历史记录列表（包含之前的工具调用请求）
            tool_results: 工具执行的结果列表
            temperature:  创造性程度
            max_tokens:   回复的最大长度

        返回:
            LLM根据工具结果生成的最终回复文本
        """
        ...
