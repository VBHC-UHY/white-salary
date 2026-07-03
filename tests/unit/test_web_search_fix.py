"""
2026-07-03 审计修复（批5）的单元测试 — 联网搜索复活（web-search）。

覆盖：
  - web_search 基于 ddgs 结果的格式化逻辑（首行标题/编号/摘要/条数上限/无效条目过滤）
  - 各类失败的中文降级文案（限流/超时/服务出错/依赖缺失/一般异常），且不向上抛异常
  - _search_sync 真实代码路径（用假 ddgs 模块验证传参：max_results/region/超时）
  - builtin/search.py 下架 quick_answer 后注册表内容（搜索四件套+fetch_webpage 保留）
"""

import sys
import types
from typing import Any

import pytest

from white_salary.adapters.tools import web_search as ws_mod
from white_salary.adapters.tools.registry import ToolRegistry
from white_salary.adapters.tools.web_search import (
    DDGSException,
    RatelimitException,
    TimeoutException,
    _format_results,
    web_search,
)


# ================================================================
# 1. 格式化逻辑（mock ddgs 返回）
# ================================================================

FAKE_RESULTS: list[dict[str, Any]] = [
    {"title": "Python 官网", "href": "https://www.python.org/", "body": "Python 是一门编程语言。"},
    {"title": "Python 教程", "href": "https://example.com/tut", "body": "从零开始学 Python。"},
    {"title": "无摘要条目", "href": "https://example.com/x", "body": ""},
]


class TestFormatResults:
    """web_search 对 ddgs 结果的格式化。"""

    async def test_normal_results_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常结果：首行「搜索 'xx' 的结果：」+ 编号标题 + 缩进摘要。"""
        monkeypatch.setattr(ws_mod, "_search_sync",
                            lambda query, max_results: list(FAKE_RESULTS))
        text = await web_search("python", max_results=5)

        assert text.startswith("搜索 'python' 的结果：")
        assert "1. Python 官网" in text
        assert "   Python 是一门编程语言。" in text
        assert "2. Python 教程" in text
        assert "3. 无摘要条目" in text

    async def test_max_results_slicing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """结果多于 max_results 时只格式化前 N 条。"""
        many = [{"title": f"标题{i}", "href": "", "body": f"摘要{i}"} for i in range(10)]
        monkeypatch.setattr(ws_mod, "_search_sync",
                            lambda query, max_results: many)
        text = await web_search("测试", max_results=3)

        assert "3. 标题2" in text
        assert "标题3" not in text  # 第4条被截断

    async def test_titleless_entries_filtered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无标题的无效条目被过滤；全部无效时按「没有找到」处理。"""
        junk = [{"title": "", "href": "https://a", "body": "有摘要没标题"},
                {"title": "   ", "href": "https://b", "body": "空白标题"}]
        monkeypatch.setattr(ws_mod, "_search_sync",
                            lambda query, max_results: junk)
        text = await web_search("怪查询")

        assert text == "没有找到关于 '怪查询' 的搜索结果"

    async def test_empty_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ddgs 返回空列表 → 「没有找到」。"""
        monkeypatch.setattr(ws_mod, "_search_sync",
                            lambda query, max_results: [])
        text = await web_search("不存在的东西xyzzy")

        assert text == "没有找到关于 '不存在的东西xyzzy' 的搜索结果"

    async def test_empty_query(self) -> None:
        """空查询直接返回提示，不触发搜索。"""
        assert await web_search("") == "请提供搜索关键词"

    def test_format_results_helper(self) -> None:
        """_format_results 独立行为：首行标题 + 每条编号/摘要/空行分隔。"""
        text = _format_results("q", FAKE_RESULTS, max_results=2)
        lines = text.split("\n")

        assert lines[0] == "搜索 'q' 的结果："
        assert "1. Python 官网" in lines
        assert "   Python 是一门编程语言。" in lines
        assert "2. Python 教程" in lines
        # 只取前2条
        assert "3. 无摘要条目" not in text


# ================================================================
# 2. 失败降级文案（不抛异常）
# ================================================================

def _raiser(exc: BaseException):
    """返回一个总是抛出指定异常的假 _search_sync。"""
    def _fake(query: str, max_results: int) -> list[dict[str, Any]]:
        raise exc
    return _fake


class TestFailureFallback:
    """各类失败都要返回明确中文文案，而不是把异常抛给调用方。"""

    async def test_ratelimit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """限流 → 「暂时限流」提示。"""
        monkeypatch.setattr(ws_mod, "_search_sync", _raiser(RatelimitException("429")))
        text = await web_search("天气")
        assert text == "搜索失败：搜索服务暂时限流，请稍后再试"

    async def test_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """超时 → 「响应超时」提示。"""
        monkeypatch.setattr(ws_mod, "_search_sync", _raiser(TimeoutException("timeout")))
        text = await web_search("天气")
        assert text == "搜索失败：搜索服务响应超时，请稍后再试"

    async def test_generic_ddgs_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ddgs 通用错误 → 「服务出错」提示。"""
        monkeypatch.setattr(ws_mod, "_search_sync", _raiser(DDGSException("boom")))
        text = await web_search("天气")
        assert text == "搜索失败：搜索服务出错，请稍后再试"

    async def test_missing_dependency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ddgs 未安装 → 依赖缺失提示。"""
        monkeypatch.setattr(ws_mod, "_search_sync",
                            _raiser(ModuleNotFoundError("No module named 'ddgs'")))
        text = await web_search("天气")
        assert text == "搜索失败：搜索依赖库（ddgs）未安装，请联系维护者"

    async def test_network_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """断网等一般异常 → 「搜索出错」提示（保持旧文案前缀）。"""
        monkeypatch.setattr(ws_mod, "_search_sync",
                            _raiser(ConnectionError("网络不可达")))
        text = await web_search("天气")
        assert text.startswith("搜索出错: ")
        assert "网络不可达" in text


# ================================================================
# 3. _search_sync 真实代码路径（假 ddgs 模块验证传参）
# ================================================================

class _FakeDDGS:
    """假 DDGS 客户端：记录构造参数与 text() 调用参数。"""

    last_init_kwargs: dict[str, Any] = {}
    last_text_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeDDGS.last_init_kwargs = dict(kwargs)

    def __enter__(self) -> "_FakeDDGS":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def text(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        _FakeDDGS.last_text_kwargs = {"query": query, **kwargs}
        return list(FAKE_RESULTS)


class TestSearchSyncWiring:
    """通过注入假 ddgs 模块，走通 web_search → to_thread → _search_sync 全链路。"""

    async def test_end_to_end_with_fake_ddgs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """全链路：构造参数带超时、text() 收到 max_results 与 region。"""
        fake_module = types.ModuleType("ddgs")
        fake_module.DDGS = _FakeDDGS  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "ddgs", fake_module)

        text = await web_search("python 教程", max_results=2)

        # 构造参数：底层 HTTP 超时
        assert _FakeDDGS.last_init_kwargs.get("timeout") == ws_mod._SEARCH_TIMEOUT_SECONDS
        # text() 传参：查询词、条数上限、地区
        assert _FakeDDGS.last_text_kwargs.get("query") == "python 教程"
        assert _FakeDDGS.last_text_kwargs.get("max_results") == 2
        assert _FakeDDGS.last_text_kwargs.get("region") == ws_mod._SEARCH_REGION
        # 输出仍是兼容格式
        assert text.startswith("搜索 'python 教程' 的结果：")
        assert "1. Python 官网" in text


# ================================================================
# 4. quick_answer 下架后的注册表
# ================================================================

class TestQuickAnswerDelisted:
    """builtin/search.py 下架 quick_answer 后的注册表内容。"""

    def _registry_names(self) -> set:
        return {t.name for t in ToolRegistry().get_all()}

    def test_quick_answer_not_registered(self) -> None:
        """quick_answer（提示词复读空壳）不再出现在注册表。"""
        assert "quick_answer" not in self._registry_names()

    def test_search_tools_still_registered(self) -> None:
        """搜索四件套与 fetch_webpage 保留（防止误删/文件加载失败）。"""
        names = self._registry_names()
        for name in ("web_search", "deep_search", "news_search",
                     "research", "fetch_webpage"):
            assert name in names, f"{name} 意外丢失"

    def test_quick_answer_function_kept(self) -> None:
        """quick_answer 函数体保留（同批2口径：只下架不删函数）。"""
        from white_salary.adapters.tools.builtin import search as search_mod
        assert callable(search_mod.quick_answer)
