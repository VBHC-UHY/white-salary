"""
white_salary/adapters/llm/openai_compatible.py

OpenAI兼容格式的通用LLM适配器。

几乎所有主流LLM API都兼容OpenAI的接口格式（包括DeepSeek、Claude代理、硅基流动、Kimi等），
所以我们只需要这一个适配器就能接入所有这些API。

用法：
    adapter = OpenAICompatibleAdapter(
        api_key="sk-xxx",
        base_url="https://api.siliconflow.cn/v1",
        model="deepseek-ai/DeepSeek-V3.2",
    )
    response = await adapter.chat_completion(messages)
"""

import json
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI

from white_salary.core.exceptions import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMRateLimitError,
    LLMResponseError,
)
from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, MessageRole, ToolCall, ToolResult


def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """
    把我们内部的 Message 对象转成 OpenAI API 需要的字典格式。

    参数:
        messages: 内部消息列表

    返回:
        OpenAI API格式的消息列表
    """
    result: list[dict[str, Any]] = []

    for msg in messages:
        # 防御：跳过非Message对象（可能有字符串意外混入）
        if isinstance(msg, str):
            result.append({"role": "user", "content": msg})
            continue
        if not hasattr(msg, 'role') or not hasattr(msg, 'content'):
            continue

        item: dict[str, Any] = {
            "role": msg.role.value if hasattr(msg.role, 'value') else str(msg.role),
            "content": msg.content,
        }
        # 如果有名字，加上（用于区分不同用户）
        if getattr(msg, 'name', None):
            item["name"] = msg.name
        result.append(item)

    return result


def _convert_tool_calls(raw_tool_calls: Any) -> list[ToolCall]:
    """
    把OpenAI API返回的工具调用数据转成我们内部的 ToolCall 对象。

    参数:
        raw_tool_calls: OpenAI API返回的原始工具调用数据

    返回:
        ToolCall对象列表
    """
    if not raw_tool_calls:
        return []

    result: list[ToolCall] = []
    for tc in raw_tool_calls:
        # 跳过非标准格式（某些API可能返回字符串或None）
        if not hasattr(tc, 'function') or not tc.function:
            continue
        if not hasattr(tc.function, 'name') or not tc.function.name:
            continue

        # 解析参数JSON
        try:
            arguments = json.loads(tc.function.arguments)
        except (json.JSONDecodeError, AttributeError):
            arguments = {}

        result.append(
            ToolCall(
                id=getattr(tc, 'id', '') or '',
                name=tc.function.name,
                arguments=arguments,
            )
        )

    return result


class OpenAICompatibleAdapter(LLMInterface):
    """
    OpenAI兼容格式的通用LLM适配器。

    支持所有兼容OpenAI接口的API提供商：
      - OpenAI 官方
      - DeepSeek 官方
      - 硅基流动（SiliconFlow）
      - 英伟达（NVIDIA）
      - dmxapi / futureppo（Claude代理）
      - OpenRouter
      - Kimi/Moonshot
      - 本地Ollama（也兼容OpenAI格式）
      - 等等...

    只需要改 api_key、base_url、model 就能切换不同的提供商。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        """
        初始化适配器。

        参数:
            api_key:  API密钥
            base_url: API地址（如 "https://api.deepseek.com/v1"）
            model:    模型名称（如 "deepseek-chat"）
            timeout:  请求超时时间（秒）
        """
        self._model = model
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    async def chat_completion(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """
        发送对话请求，获取完整回复。

        参数:
            messages:    对话历史
            temperature: 创造性程度
            max_tokens:  最大回复长度

        返回:
            LLM的回复文本
        """
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=_convert_messages(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # 提取回复文本
            content = response.choices[0].message.content
            if content is None:
                return ""
            return content

        except Exception as e:
            # 统一异常处理：把各种API错误转成我们自己的异常类型
            self._handle_error(e)
            return ""  # 这行不会执行，_handle_error总是会抛异常

    async def chat_completion_stream(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        """
        流式对话：LLM一边想一边返回文本片段。

        参数:
            messages:    对话历史
            temperature: 创造性程度
            max_tokens:  最大回复长度

        返回:
            异步生成器，逐段返回文本
        """
        try:
            stream = await self._client.chat.completions.create(
                model=self._model,
                messages=_convert_messages(messages),
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,  # 开启流式
            )

            async for chunk in stream:
                # 每个chunk可能包含一小段文本
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            self._handle_error(e)

    async def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> tuple[str, list[ToolCall]]:
        """
        发送对话请求，支持工具调用。

        参数:
            messages:    对话历史
            tools:       可用工具描述（OpenAI Function Calling格式）
            temperature: 创造性程度
            max_tokens:  最大回复长度

        返回:
            (文本回复, 工具调用列表)
        """
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=_convert_messages(messages),
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            choice = response.choices[0]
            content = choice.message.content or ""
            tool_calls = _convert_tool_calls(choice.message.tool_calls)

            return content, tool_calls

        except Exception as e:
            self._handle_error(e)
            return "", []

    async def process_tool_results(
        self,
        messages: list[Message],
        tool_results: list[ToolResult],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """
        处理工具返回结果，生成最终回复。

        把工具的执行结果告诉LLM，让它生成给用户的最终回复。

        参数:
            messages:     对话历史（包含之前的工具调用）
            tool_results: 工具执行结果
            temperature:  创造性程度
            max_tokens:   最大回复长度

        返回:
            最终回复文本
        """
        # 把工具结果转成普通消息（不用role:tool格式，因为主模型可能没发起tool_calls）
        all_messages = _convert_messages(messages)
        # 把所有工具结果合并为一条system消息
        tool_texts = []
        for result in tool_results:
            tool_texts.append(result.content)
        if tool_texts:
            all_messages.append({
                "role": "system",
                "content": (
                    "你刚刚帮用户执行了一些操作，以下是执行结果：\n"
                    + "\n".join(tool_texts)
                    + "\n\n请用你自己的角色语气自然地回复用户。"
                    "绝对不要把上面的技术信息、QQ号、API结果原样告诉用户。"
                    "如果操作成功了就简单说一句就好，不要重复工具返回的内容。"
                    "如果操作失败了就用友好的方式告诉用户。"
                ),
            })

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=all_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            content = response.choices[0].message.content
            return content or ""

        except Exception as e:
            self._handle_error(e)
            return ""

    def _handle_error(self, error: Exception) -> None:
        """
        统一异常处理：把各种API错误转成我们自己的异常类型。

        这样上层代码不需要关心具体是哪个API的错误格式，
        只需要处理我们自定义的异常就行。

        参数:
            error: 原始异常

        异常:
            总是抛出 WhiteSalaryError 的某个子类
        """
        error_str = str(error).lower()

        # 认证失败（API密钥错误）
        if "401" in error_str or "unauthorized" in error_str or "invalid api key" in error_str:
            raise LLMAuthenticationError(
                f"API认证失败（密钥可能错误或过期）: {error}",
                details={"original_error": str(error)},
            ) from error

        # 限流（调用太频繁）
        if "429" in error_str or "rate limit" in error_str or "too many" in error_str:
            raise LLMRateLimitError(
                f"API调用太频繁，被限流了: {error}",
                details={"original_error": str(error)},
            ) from error

        # 余额不足
        if "403" in error_str or "quota" in error_str or "额度" in error_str:
            raise LLMAuthenticationError(
                f"API余额不足或权限不够: {error}",
                details={"original_error": str(error)},
            ) from error

        # 连接失败
        if "connection" in error_str or "timeout" in error_str or "network" in error_str:
            raise LLMConnectionError(
                f"连接LLM服务失败: {error}",
                details={"original_error": str(error)},
            ) from error

        # 其他错误
        raise LLMResponseError(
            f"LLM返回异常: {error}",
            details={"original_error": str(error)},
        ) from error

    async def close(self) -> None:
        """关闭HTTP连接。"""
        await self._client.close()
