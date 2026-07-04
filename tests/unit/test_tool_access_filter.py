from white_salary.adapters.tools.registry import (
    ToolAccessContext,
    ToolDefinition,
    ToolRegistry,
)


async def _handler(**kwargs) -> str:
    return "ok"


def _empty_registry() -> ToolRegistry:
    registry = ToolRegistry.__new__(ToolRegistry)
    registry._tools = {}
    return registry


def _tool_names(payload: list[dict]) -> set[str]:
    return {item["function"]["name"] for item in payload}


def test_get_openai_tools_keeps_unmarked_tools_without_context() -> None:
    registry = _empty_registry()
    registry.register(ToolDefinition(
        name="plain",
        description="plain tool",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
    ))
    registry.register(ToolDefinition(
        name="qq_only",
        description="qq tool",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        platforms=("qq",),
    ))

    assert _tool_names(registry.get_openai_tools()) == {"plain", "qq_only"}


def test_get_openai_tools_filters_explicit_platform_permission_and_service() -> None:
    registry = _empty_registry()
    registry.register(ToolDefinition(
        name="desktop_only",
        description="desktop tool",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        platforms=("desktop",),
    ))
    registry.register(ToolDefinition(
        name="owner_tool",
        description="owner tool",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        requires_permission="owner",
    ))
    registry.register(ToolDefinition(
        name="comfy_tool",
        description="service tool",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        requires_service="comfyui",
    ))

    context = ToolAccessContext(
        platform="qq",
        permissions=frozenset({"owner"}),
        available_services=frozenset(),
    )

    assert _tool_names(registry.get_openai_tools(context=context)) == {"owner_tool"}


def test_get_openai_tools_filters_side_effects_when_disallowed() -> None:
    registry = _empty_registry()
    registry.register(ToolDefinition(
        name="read_only",
        description="read only",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
    ))
    registry.register(ToolDefinition(
        name="dangerous_action",
        description="side effect",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        side_effect=True,
    ))

    payload = registry.get_openai_tools(context={"allow_side_effects": False})

    assert _tool_names(payload) == {"read_only"}
