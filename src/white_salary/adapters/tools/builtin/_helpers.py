"""工具定义辅助函数。"""

NONE_PARAMS = {"type": "object", "properties": {}, "required": []}


def S(desc, required=False):
    """字符串参数。"""
    return {"type": "string", "description": desc, "_req": required}


def I(desc):
    """整数参数。"""
    return {"type": "integer", "description": desc}


def P(**props):
    """构建参数Schema。"""
    required = [k for k, v in props.items() if isinstance(v, dict) and v.get("_req")]
    clean = {}
    for k, v in props.items():
        cv = dict(v)
        cv.pop("_req", None)
        clean[k] = cv
    return {"type": "object", "properties": clean, "required": required}


def tool(
    name,
    description,
    params=None,
    *,
    platforms=(),
    permission="",
    service="",
    side_effect=None,
):
    """Decorate an async function with tool metadata.

    Older tools can omit the metadata. The registry applies a conservative
    built-in policy while modules are migrated to explicit declarations.
    """
    def decorator(func):
        definition = {
            "name": name,
            "description": description,
            "parameters": params or NONE_PARAMS,
            "handler": func,
            "platforms": tuple(platforms),
            "requires_permission": permission,
            "requires_service": service,
        }
        if side_effect is not None:
            definition["side_effect"] = bool(side_effect)
        func._tool_def = definition
        return func
    return decorator
