# tools/search.py
"""搜索工具 - 博查搜索 API (标准版) + DuckDuckGo/Bing 多级降级"""
import re
import requests
from html import unescape

import config


def register_tools(registry):
    registry.register(
        "web_search",
        _web_search,
        "搜索互联网内容（博查搜索API，失败时自动降级到DuckDuckGo/Bing）",
        {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "最大结果数，默认5"},
            "freshness": {"type": "string", "description": "时间范围: noLimit/oneDay/oneWeek/oneMonth/oneYear"}
        }
    )


def _format_results(items: list, source_name: str, query: str) -> str:
    if not items:
        return f"{source_name} 搜索无结果。"
    lines = [f"🔍 搜索 '{query}' 的结果（{source_name}）：\n"]
    for i, item in enumerate(items):
        title = item.get("title", "无标题").strip()
        link = item.get("link", "")
        snippet = item.get("snippet", "").strip()
        line = f"{i + 1}. {title}"
        if link:
            line += f" | {link}"
        lines.append(line)
        if snippet:
            lines.append(f"   {snippet[:200]}")
    return "\n".join(lines)


def _search_bocha(query: str, max_results: int = 5, freshness: str = "noLimit") -> list | None:
    """调用博查搜索标准版 API"""
    if not config.BOCHA_SEARCH_API_KEY:
        return None
    try:
        headers = {
            "Authorization": f"Bearer {config.BOCHA_SEARCH_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
        payload = {
            "query": query,
            "freshness": freshness,
            "summary": True,
            "count": min(max_results, 50)
        }
        resp = requests.post(config.BOCHA_SEARCH_URL, json=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 200:
                pages = data.get("data", {}).get("pages", [])
                items = []
                for p in pages[:max_results]:
                    items.append({
                        "title": p.get("title", ""),
                        "link": p.get("url", ""),
                        "snippet": p.get("snippet", "")
                    })
                return items
    except Exception:
        pass
    return None


def _search_duckduckgo_html(query: str, max_results: int = 5) -> list | None:
    """DuckDuckGo HTML 降级搜索"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        url = "https://html.duckduckgo.com/html/"
        resp = requests.post(url, data={"q": query}, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None
        raw_links = re.findall(
            r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>',
            resp.text, re.DOTALL
        )
        raw_snippets = re.findall(
            r'<a class="result__snippet"[^>]*>(.*?)</a>',
            resp.text, re.DOTALL
        )
        items = []
        for i, (link, title) in enumerate(raw_links[:max_results]):
            title_clean = unescape(re.sub(r'<[^>]+>', '', title)).strip()
            snippet = unescape(re.sub(r'<[^>]+>', '', raw_snippets[i])) if i < len(raw_snippets) else ""
            items.append({"title": title_clean, "link": link, "snippet": snippet})
        return items if items else None
    except Exception:
        return None


def _search_bing(query: str, max_results: int = 5) -> list | None:
    """Bing 搜索降级"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        url = f"https://cn.bing.com/search?q={requests.utils.quote(query)}"
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None

        html = resp.text
        items = []

        # 尝试多种解析模式
        # 模式1: b_algo 结构（兼容 class="b_algo" 和 <li b_algo ...> 两种写法）
        blocks = re.findall(
            r'<li[^>]*\bb_algo\b[^>]*>(.*?)</li>',
            html, re.DOTALL
        )
        for block in blocks:
            # 提取链接和标题
            # Bing移动版结构: <div class="b_algoheader"><a href="真实链接">真实标题</a></div>
            header = re.search(r'<div class="b_algoheader">(.*?)</div>', block, re.DOTALL)
            link = ""
            title = ""
            if header:
                a_tag = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', header.group(1), re.DOTALL)
                if a_tag:
                    link = a_tag.group(1)
                    title = unescape(re.sub(r'<[^>]+>', '', a_tag.group(2))).strip()
            # 如果b_algoheader没取到，回退到取block中的第一个http链接和第二个a标签文本
            if not title:
                all_a = re.findall(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
                if len(all_a) >= 2:
                    link = all_a[1][0]
                    title = unescape(re.sub(r'<[^>]+>', '', all_a[1][1])).strip()
                elif len(all_a) == 1:
                    link = all_a[0][0]
                    title = unescape(re.sub(r'<[^>]+>', '', all_a[0][1])).strip()
            # 提取摘要
            snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
            snippet = unescape(re.sub(r'<[^>]+>', '', snippet_match.group(1))).strip() if snippet_match else ""
            if link and title:
                items.append({"title": title, "link": link, "snippet": snippet})

        return items[:max_results] if items else None
    except Exception:
        return None


def _web_search(query: str, max_results: int = 5, freshness: str = "noLimit", **kwargs) -> str:
    """
    搜索主函数：优先博查 API，依次降级到 DuckDuckGo → Bing。
    """
    if not query:
        return "搜索查询不能为空"
    query = query.strip()
    max_results = min(int(max_results), 50)

    # 1. 博查 API
    items = _search_bocha(query, max_results, freshness)
    if items:
        return _format_results(items, "博查搜索", query)

    # 2. DuckDuckGo HTML 降级
    items = _search_duckduckgo_html(query, max_results)
    if items:
        return _format_results(items, "DuckDuckGo（降级）", query)

    # 3. Bing 搜索降级
    items = _search_bing(query, max_results)
    if items:
        return _format_results(items, "Bing搜索（降级）", query)

    return "所有搜索源均失败，请检查网络连接或稍后重试。"