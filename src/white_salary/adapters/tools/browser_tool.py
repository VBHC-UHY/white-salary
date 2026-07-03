"""
white_salary/adapters/tools/browser_tool.py

浏览器工具 — 获取和解析网页内容。

功能：
  - 获取网页HTML并提取纯文字内容
  - 提取网页标题
  - 提取网页中的链接
  - 支持自定义User-Agent

不使用无头浏览器（Selenium/Playwright），
只用aiohttp获取HTML + 正则/简单解析提取内容。
轻量级，不需要额外安装浏览器驱动。
"""

import re
import aiohttp
from typing import Optional

from loguru import logger


MAX_CONTENT_LENGTH = 3000  # 最大返回内容长度


async def fetch_webpage(url: str, max_length: int = MAX_CONTENT_LENGTH) -> str:
    """
    获取网页内容并提取纯文字。

    Args:
        url: 网页URL
        max_length: 最大返回字符数

    Returns:
        网页标题 + 正文摘要
    """
    if not url:
        return "请提供网页URL"

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    return f"获取失败 (HTTP {resp.status}): {url}"

                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type and "text/plain" not in content_type:
                    return f"不是HTML页面 (Content-Type: {content_type})"

                html = await resp.text(errors="replace")

        # 提取标题
        title = _extract_title(html)

        # 提取正文
        text = _extract_text(html)

        # 组装结果
        result = f"网页: {url}\n"
        if title:
            result += f"标题: {title}\n"
        result += f"\n{text}"

        # 截断
        if len(result) > max_length:
            result = result[:max_length] + "\n...[内容已截断]"

        logger.debug(f"[Browser] 获取 {url}: {len(text)}字")
        return result

    except aiohttp.ClientError as e:
        return f"网络请求失败: {e}"
    except Exception as e:
        return f"获取网页错误: {e}"


async def extract_links(url: str, max_links: int = 20) -> str:
    """
    提取网页中的所有链接。

    Args:
        url: 网页URL
        max_links: 最多返回链接数

    Returns:
        链接列表
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                html = await resp.text(errors="replace")

        links = re.findall(r'href=["\']([^"\']+)["\']', html)

        # 过滤和去重
        seen = set()
        valid_links = []
        for link in links:
            if link.startswith(("http://", "https://")) and link not in seen:
                seen.add(link)
                valid_links.append(link)
                if len(valid_links) >= max_links:
                    break

        if not valid_links:
            return f"页面 {url} 中没有找到外部链接"

        lines = [f"页面 {url} 中的链接：\n"]
        for i, link in enumerate(valid_links, 1):
            lines.append(f"{i}. {link}")

        return "\n".join(lines)

    except Exception as e:
        return f"提取链接失败: {e}"


def _extract_title(html: str) -> str:
    """提取HTML标题。"""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        title = match.group(1).strip()
        title = re.sub(r"\s+", " ", title)
        return title[:100]
    return ""


def _extract_text(html: str) -> str:
    """
    从HTML中提取纯文字内容。

    简单的HTML→文字转换（不使用BeautifulSoup依赖）。
    """
    text = html

    # 删除script和style标签及内容
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    # 删除head
    text = re.sub(r"<head[^>]*>.*?</head>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # 把<br>和<p>转为换行
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?div[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?h[1-6][^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?li[^>]*>", "\n• ", text, flags=re.IGNORECASE)

    # 删除所有其他HTML标签
    text = re.sub(r"<[^>]+>", "", text)

    # 解码HTML实体
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")

    # 清理空行
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if line]

    # 合并过短的行
    result = "\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result.strip()
