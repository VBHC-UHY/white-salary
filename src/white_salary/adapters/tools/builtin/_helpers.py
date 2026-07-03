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


def tool(name, description, params=None):
    """装饰器：标记一个async函数为工具。"""
    def decorator(func):
        func._tool_def = {
            "name": name,
            "description": description,
            "parameters": params or NONE_PARAMS,
            "handler": func,
        }
        return func
    return decorator
