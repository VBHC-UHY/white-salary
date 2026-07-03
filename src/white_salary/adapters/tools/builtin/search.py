"""搜索工具 — 网页搜索/深度搜索/新闻/研究/网页抓取。

2026-07-03 审计修复（批5）：底层 web_search 后端已从被反爬的 DuckDuckGo HTML
端点换成 ddgs 库（见 adapters/tools/web_search.py），本文件的
web_search/deep_search/news_search/research 四个工具随之恢复可用，无需改动。
"""
from ._helpers import tool, P, S


@tool("web_search", "搜索互联网获取最新信息", P(query=S("搜索关键词", True)))
async def web_search(query: str = "") -> str:
    from white_salary.adapters.tools.web_search import web_search as _ws
    return await _ws(query)


@tool("deep_search", "深度搜索（多轮搜索并汇总结果）", P(query=S("搜索词", True)))
async def deep_search(query: str = "") -> str:
    from white_salary.adapters.tools.web_search import web_search as _ws
    result = await _ws(query)
    return f"[深度搜索] {query}\n{result}\n请根据搜索结果详细回答。"


@tool("news_search", "搜索最新新闻资讯", P(topic=S("新闻话题", True)))
async def news_search(topic: str = "") -> str:
    from white_salary.adapters.tools.web_search import web_search as _ws
    return await _ws(f"{topic} 最新新闻")


@tool("quick_answer", "快速简洁回答（不展开）", P(question=S("问题", True)))
async def quick_answer(question: str = "") -> str:
    return f"[快速回答] {question}\n请简洁直接回答。"


@tool("research", "深入研究某个话题", P(topic=S("研究话题", True)))
async def research(topic: str = "") -> str:
    from white_salary.adapters.tools.web_search import web_search as _ws
    return await _ws(f"{topic} 研究 综述 分析")


@tool("fetch_webpage", "获取指定URL网页的文字内容", P(url=S("网页URL", True)))
async def fetch_webpage(url: str = "") -> str:
    from white_salary.adapters.tools.browser_tool import fetch_webpage as _fw
    return await _fw(url)


# 2026-07-03 审计修复（批5）：下架 quick_answer——它只把问题包一层
# 「[快速回答]…请简洁直接回答」文本原样返回，是提示词复读空壳，主模型本来就能直接回答，
# 还加重了全量工具 payload 导致的判断超时（依据 docs/audit-2026-07-02/tools-media.json，
# 口径同批2下架 coding.py 的15个提示词空壳）。函数体保留，待真实现后再加回 TOOLS。
TOOLS = [fn._tool_def for fn in [
    web_search, deep_search, news_search, research, fetch_webpage,
]]
