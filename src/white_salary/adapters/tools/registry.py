"""
white_salary/adapters/tools/registry.py

工具注册中心 — 管理所有可用的Function Calling工具。

功能：
  - 注册/注销工具
  - 生成OpenAI格式的tools列表（用于LLM function calling）
  - 执行工具调用
  - 内置基础工具（时间、计算、搜索等）
"""

import json
import time
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Optional

from loguru import logger

from white_salary.adapters.tools.errors import ToolKnownFailure, ToolOutcomeUnknown


# 2026-07-02 审计修复（批2）：超时机制改为「按工具超时表」（工具名→秒数），
# 取代原 _slow_tools 元组一刀切120秒（依据 docs/audit-2026-07-02/tools-media.json）：
#   - make_video：云端轮询上限300秒+多段拼接/本地AnimateDiff降级，给360秒
#   - generate_video/local_generate_video：本地分支60秒ComfyUI冷启动+Wan2.2轮询900秒
#     +SVD降级，给1100秒（local_generate_video 内部就是 make_video/generate_video 同链路）
#   - local_lip_sync：内部 subprocess timeout=120秒，外层给300秒富余
#   - generate_image/draw/generate_sticker：本地冷启动60秒+本地生成120秒后还要给云端
#     降级留出时间，给360秒；旧120/30秒会在进入云端降级前杀掉整个工具
#   - watch_video/deep_search/research/edit_image：维持原120秒
#   - 其余默认 DEFAULT_TOOL_TIMEOUT=30秒
TOOL_TIMEOUTS: dict[str, int] = {
    "make_video": 360,
    "generate_video": 1100,
    "local_generate_video": 1100,
    "local_lip_sync": 300,
    "watch_video": 120,
    "deep_search": 120,
    "research": 120,
    "generate_image": 360,
    "draw": 360,
    "generate_sticker": 360,
    "edit_image": 120,
    # 2026-07-03 工具实现（批9）：download_video 真实现（yt_dlp 下载≤30分钟/≤500MB
    # 的视频，网络+落盘远超默认30秒），给600秒；deep_think 真调辅助LLM做多步推理
    # （推理模型响应慢），给120秒；describe_image 走视觉模型（含图片下载），给90秒
    "download_video": 600,
    "deep_think": 120,
    "describe_image": 90,
}

# 默认工具执行超时（秒）
DEFAULT_TOOL_TIMEOUT: int = 30


def get_tool_timeout(name: str) -> int:
    """
    2026-07-02 审计修复（批2）：按工具超时表返回该工具允许的最长执行秒数。

    Args:
        name: 工具名称

    Returns:
        超时秒数（表内工具取表值，其余取默认30秒）
    """
    return TOOL_TIMEOUTS.get(name, DEFAULT_TOOL_TIMEOUT)


@dataclass
class ToolDefinition:
    """工具定义。"""
    name: str                        # 工具名称（英文标识符）
    description: str                 # 工具描述（告诉LLM这个工具干什么）
    parameters: dict                 # JSON Schema参数定义
    handler: Callable[..., Awaitable[str]]  # 异步执行函数
    category: str = "builtin"        # 分类：builtin/custom/mcp
    platforms: tuple[str, ...] = ()
    requires_permission: str = ""
    requires_service: str = ""
    required_permissions: tuple[str, ...] = ()
    required_services: tuple[str, ...] = ()
    side_effect: bool = False
    side_effect_group: str = "global"


@dataclass(frozen=True)
class ToolExecutionResult:
    """Structured result used by the new agent runtime."""

    name: str
    ok: bool
    content: str
    duration_ms: int
    error_type: str = ""
    side_effect: bool = False
    outcome_known: bool = True


@dataclass(frozen=True)
class ToolAccessContext:
    """工具候选过滤上下文，只做安全/可用性过滤，不替代 tool_llm 判断。"""
    platform: str = ""
    permissions: frozenset[str] = field(default_factory=frozenset)
    available_services: frozenset[str] = field(default_factory=frozenset)
    allow_side_effects: bool = True

    @classmethod
    def from_value(cls, value: "ToolAccessContext | dict | None") -> "ToolAccessContext | None":
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        permissions = value.get("permissions") or ()
        services = value.get("available_services") or ()
        return cls(
            platform=str(value.get("platform") or ""),
            permissions=frozenset(str(p) for p in permissions),
            available_services=frozenset(str(s) for s in services),
            allow_side_effects=bool(value.get("allow_side_effects", True)),
        )


class ToolRegistry:
    """
    工具注册中心。

    管理所有可用工具，生成LLM function calling所需的格式。
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._register_builtins()

    def register(self, tool: ToolDefinition, *, replace: bool = False) -> None:
        """注册一个工具。

        Duplicate names are rejected by default. A marketplace plugin must
        never silently replace a builtin tool and later remove it on unload.
        Explicit replacement is reserved for controlled internal migrations.
        """
        if tool.name in self._tools and not replace:
            existing = self._tools[tool.name]
            raise ValueError(
                f"工具名冲突: {tool.name} 已由 {existing.category} 注册"
            )
        self._tools[tool.name] = tool
        logger.debug(f"[Tools] 注册: {tool.name} ({tool.category})")

    def unregister(self, name: str) -> bool:
        """注销一个工具。"""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        """获取工具定义。"""
        return self._tools.get(name)

    def get_all(self) -> list[ToolDefinition]:
        """获取所有注册的工具。"""
        return list(self._tools.values())

    async def execute(
        self,
        name: str,
        arguments: dict,
        context: ToolAccessContext | dict | None = None,
    ) -> str:
        """
        执行一个工具调用。

        Args:
            name: 工具名称
            arguments: 参数字典

        Returns:
            工具执行结果（字符串）
        """
        result = await self.execute_detailed(name, arguments, context=context)
        return result.content

    async def execute_detailed(
        self,
        name: str,
        arguments: dict,
        context: ToolAccessContext | dict | None = None,
    ) -> ToolExecutionResult:
        """Execute a tool and re-check access at the execution boundary."""

        import asyncio

        started = time.perf_counter()
        tool = self._tools.get(name)
        if not tool:
            return ToolExecutionResult(
                name=name,
                ok=False,
                content=f"[错误] 未知工具: {name}",
                duration_ms=0,
                error_type="not_found",
            )

        access = ToolAccessContext.from_value(context)
        if not self._is_available_for_context(tool, access):
            logger.warning(f"[Tools] 拒绝未授权执行: {name}")
            return ToolExecutionResult(
                name=name,
                ok=False,
                content=f"[权限拒绝] 当前会话无权执行工具 {name}",
                duration_ms=int((time.perf_counter() - started) * 1000),
                error_type="permission_denied",
                side_effect=tool.side_effect,
                outcome_known=True,
            )

        try:
            tool_timeout = get_tool_timeout(name)
            value = await asyncio.wait_for(tool.handler(**arguments), timeout=tool_timeout)
            if value is None:
                logger.warning(f"[Tools] {name} 未返回可验证结果")
                return ToolExecutionResult(
                    name=name,
                    ok=False,
                    content=f"工具 {name} 没有返回可验证结果",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error_type="empty_result",
                    side_effect=tool.side_effect,
                    outcome_known=not tool.side_effect,
                )
            content = str(value)
            logger.debug(f"[Tools] 执行 {name}: {content[:100]}")
            return ToolExecutionResult(
                name=name,
                ok=True,
                content=content,
                duration_ms=int((time.perf_counter() - started) * 1000),
                side_effect=tool.side_effect,
                outcome_known=True,
            )
        except ToolKnownFailure as exc:
            logger.warning(f"[Tools] 执行 {name} 已知失败: {exc}")
            return ToolExecutionResult(
                name=name,
                ok=False,
                content=str(exc),
                duration_ms=int((time.perf_counter() - started) * 1000),
                error_type="known_failure",
                side_effect=tool.side_effect,
                outcome_known=True,
            )
        except ToolOutcomeUnknown as exc:
            logger.warning(f"[Tools] 执行 {name} 结果未知: {exc}")
            return ToolExecutionResult(
                name=name,
                ok=False,
                content=str(exc),
                duration_ms=int((time.perf_counter() - started) * 1000),
                error_type="outcome_unknown",
                side_effect=tool.side_effect,
                outcome_known=False,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[Tools] 执行 {name} 超时")
            return ToolExecutionResult(
                name=name,
                ok=False,
                content=f"工具 {name} 执行超时",
                duration_ms=int((time.perf_counter() - started) * 1000),
                error_type="timeout",
                side_effect=tool.side_effect,
                outcome_known=not tool.side_effect,
            )
        except Exception as exc:
            logger.warning(f"[Tools] 执行 {name} 失败: {exc}")
            return ToolExecutionResult(
                name=name,
                ok=False,
                content=f"工具 {name} 执行失败: {exc}",
                duration_ms=int((time.perf_counter() - started) * 1000),
                error_type="exception",
                side_effect=tool.side_effect,
                outcome_known=not tool.side_effect,
            )

    def get_openai_tools(
        self,
        context: ToolAccessContext | dict | None = None,
    ) -> list[dict]:
        """
        生成OpenAI格式的tools列表（用于function calling）。

        Returns:
            OpenAI API格式的工具定义列表
        """
        access = ToolAccessContext.from_value(context)
        tools = []
        for tool in self._tools.values():
            if not self._is_available_for_context(tool, access):
                continue
            tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })
        return tools

    def _is_available_for_context(
        self,
        tool: ToolDefinition,
        context: ToolAccessContext | None,
    ) -> bool:
        if context is None:
            return True
        if tool.platforms and context.platform and context.platform not in tool.platforms:
            return False
        permissions = set(tool.required_permissions)
        if tool.requires_permission:
            permissions.add(tool.requires_permission)
        if not permissions.issubset(context.permissions):
            return False
        services = set(tool.required_services)
        if tool.requires_service:
            services.add(tool.requires_service)
        if not services.issubset(context.available_services):
            return False
        if tool.side_effect and not context.allow_side_effects:
            return False
        return True

    @property
    def count(self) -> int:
        return len(self._tools)

    # ================================================================
    # 内置工具
    # ================================================================

    def _register_builtins(self) -> None:
        """自动发现并注册 builtin/ 目录下的所有工具。"""
        # ============ 自动发现 builtin/ 目录下的所有工具 ============
        # 新增工具只需在 builtin/ 下对应分类文件里加一条，或创建新文件
        # 每个文件导出 TOOLS 列表，格式: [{"name", "description", "parameters", "handler"}, ...]
        import importlib
        from pathlib import Path

        builtin_dir = Path(__file__).parent / "builtin"
        loaded_files = 0
        for py_file in sorted(builtin_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"white_salary.adapters.tools.builtin.{py_file.stem}"
            try:
                mod = importlib.import_module(module_name)
                tools_list = getattr(mod, "TOOLS", [])
                from white_salary.adapters.tools.builtin._policy import get_builtin_policy

                for t in tools_list:
                    policy = get_builtin_policy(py_file.stem, t["name"])
                    self.register(ToolDefinition(
                        name=t["name"],
                        description=t["description"],
                        parameters=t["parameters"],
                        handler=t["handler"],
                        category="builtin",
                        platforms=tuple(t.get("platforms") or policy.platforms),
                        requires_permission=str(
                            t.get("requires_permission") or policy.requires_permission
                        ),
                        requires_service=str(t.get("requires_service") or policy.requires_service),
                        side_effect=(
                            bool(t["side_effect"])
                            if "side_effect" in t
                            else policy.side_effect
                        ),
                    ))
                loaded_files += 1
            except Exception as e:
                logger.warning(f"[Tools] 加载 {py_file.name} 失败: {e}")

        logger.info(f"[Tools] 从 {loaded_files} 个分类文件注册了 {self.count} 个工具")

    # 所有工具handler现在在 builtin/ 各分类文件中，registry.py只负责发现和注册
