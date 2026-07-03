"""
white_salary/adapters/tools/web_search.py

网页搜索工具 — 让AI能搜索互联网获取信息。

2026-07-03 审计修复（批5）：DuckDuckGo HTML 端点已反爬（本机实测 HTTP 202 挑战页、
0 条结果，依据 docs/audit-2026-07-02/tools-media.json），原 aiohttp 抓 HTML +
正则解析的方案导致 web_search/deep_search/news_search/research 四个搜索工具全灭。
改用已安装的 ddgs 库（多引擎元搜索聚合，免 API Key）作为后端：
  - ddgs 是同步库，用 asyncio.to_thread 包裹避免阻塞事件循环；
  - 对外函数签名 web_search(query, max_results) 与返回文本格式保持不变；
  - 任何失败（限流/超时/断网/依赖缺失）都返回明确的中文失败信息，不向上抛异常。
"""

import asyncio
from typing import Any

from loguru import logger

# 2026-07-03 审计修复（批5）：ddgs 的异常类型（限流/超时/通用错误）。
# 兜底占位类防止 ddgs 未安装时模块导入期直接崩溃——
# 此时 _search_sync 里的 import 会抛 ModuleNotFoundError，
# 由 web_search 捕获并转成中文提示。
try:
    from ddgs.exceptions import (
        DDGSException,
        RatelimitException,
        TimeoutException,
    )
except ImportError:  # pragma: no cover - ddgs is optional; keep imports safe
    class DDGSException(Exception):  # type: ignore[no-redef]
        """ddgs 未安装时的占位异常基类。"""

    class RatelimitException(DDGSException):  # type: ignore[no-redef]
        """ddgs 未安装时的占位限流异常。"""

    class TimeoutException(DDGSException):  # type: ignore[no-redef]
        """ddgs 未安装时的占位超时异常。"""


# 单次搜索的底层 HTTP 超时秒数（与旧实现 aiohttp total=10 保持一致）
_SEARCH_TIMEOUT_SECONDS: int = 10

# 搜索地区：本项目是中文陪伴体，中文查询在 zh-cn 地区命中率明显更高（本机实测对比）
_SEARCH_REGION: str = "zh-cn"


async def web_search(query: str, max_results: int = 5) -> str:
    """
    搜索互联网并返回结果摘要。

    使用 ddgs 库做多引擎元搜索（免 API Key）。

    Args:
        query: 搜索关键词
        max_results: 最多返回几条结果

    Returns:
        搜索结果的文本摘要；失败时返回中文失败信息（不抛异常）
    """
    if not query:
        return "请提供搜索关键词"

    try:
        # 2026-07-03 审计修复（批5）：ddgs 是同步库，放到线程池避免阻塞事件循环
        raw_results = await asyncio.to_thread(_search_sync, query, max_results)
    except RatelimitException as e:
        logger.warning(f"[WebSearch] 搜索被限流: {e}")
        return "搜索失败：搜索服务暂时限流，请稍后再试"
    except TimeoutException as e:
        logger.warning(f"[WebSearch] 搜索超时: {e}")
        return "搜索失败：搜索服务响应超时，请稍后再试"
    except DDGSException as e:
        logger.warning(f"[WebSearch] 搜索服务出错: {e}")
        return "搜索失败：搜索服务出错，请稍后再试"
    except ModuleNotFoundError as e:
        logger.error(f"[WebSearch] ddgs 库未安装: {e}")
        return "搜索失败：搜索依赖库（ddgs）未安装，请联系维护者"
    except Exception as e:
        # 网络断开等其它异常也不向上抛，统一转成中文提示
        logger.warning(f"[WebSearch] 搜索出错: {type(e).__name__}: {e}")
        return f"搜索出错: {e}"

    # 过滤掉没有标题的无效条目（与旧实现只收录有标题的结果一致）
    results = [r for r in raw_results if str(r.get("title", "")).strip()]
    if not results:
        return f"没有找到关于 '{query}' 的搜索结果"

    return _format_results(query, results, max_results)


def _search_sync(query: str, max_results: int) -> list[dict[str, Any]]:
    """
    同步执行一次 ddgs 文本搜索（在线程池中运行）。

    Args:
        query: 搜索关键词
        max_results: 最多返回几条结果

    Returns:
        ddgs 返回的结果字典列表，每项含 title/href/body 三个键
    """
    # 局部导入：ddgs 未安装时由调用方 web_search 捕获 ModuleNotFoundError
    from ddgs import DDGS

    with DDGS(timeout=_SEARCH_TIMEOUT_SECONDS) as client:
        return client.text(
            query,
            max_results=max_results,
            region=_SEARCH_REGION,
        )


def _format_results(
    query: str,
    results: list[dict[str, Any]],
    max_results: int,
) -> str:
    """
    把 ddgs 结果列表格式化成与旧实现一致的文本摘要。

    格式：首行「搜索 'xx' 的结果：」，随后每条为编号标题 + 缩进摘要 + 空行。

    Args:
        query: 搜索关键词（用于首行标题）
        results: ddgs 结果字典列表（title/href/body）
        max_results: 最多格式化几条

    Returns:
        文本摘要
    """
    lines: list[str] = [f"搜索 '{query}' 的结果：\n"]
    for i, item in enumerate(results[:max_results], 1):
        title = str(item.get("title", "")).strip()
        snippet = str(item.get("body", "")).strip()
        lines.append(f"{i}. {title}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")

    return "\n".join(lines)
