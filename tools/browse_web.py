# tools/browse_web.py
"""
浏览网页工具 — 基于 Scrapling 的智能网页抓取
GitHub: D4Vinci/Scrapling

提供：
- browse_web: 获取网页内容并解析（自动反反爬）
- browse_web_js: 使用 JavaScript 渲染后获取（需 Playwright）

Scrapling 特性：
- StealthyFetcher: 自动绕过反爬检测
- 自适应选择器: auto_save / auto_match 应对网站改版
- 文本提取: 智能清理 HTML 噪声
"""

import logging

logger = logging.getLogger("MHAgent.Tools.BrowseWeb")

# ── 优雅降级标志 ──
_scrapling_available = False
_scrapling_playwright_available = False

try:
    from scrapling import Fetcher, StealthyFetcher
    _scrapling_available = True
    try:
        from scrapling import PlayWrightFetcher
        _scrapling_playwright_available = True
    except ImportError:
        logger.info("Scrapling PlayWrightFetcher 不可用（需安装 playwright）")
except ImportError:
    logger.warning("Scrapling 未安装，将以 requests + BeautifulSoup 降级运行。安装命令: pip install scrapling")


def _browse_web(url: str, extract_text: bool = False, selector: str = None,
                stealth: bool = True, timeout: int = 15, **kwargs) -> str:
    """
    浏览网页并获取内容。

    参数:
        url: 目标网页 URL
        extract_text: 是否提取纯文本（剥离 HTML 标签），默认保留 HTML
        selector: CSS 选择器，只提取匹配元素的内容
        stealth: 是否启用隐身模式（绕过反爬），默认 True
        timeout: 超时时间（秒），默认 15
    """
    if not url:
        return "错误：URL 不能为空"

    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    # ── 主路径：Scrapling ──
    if _scrapling_available:
        try:
            if stealth:
                d = StealthyFetcher(auto_match=True)
            else:
                d = Fetcher(auto_match=True)

            page = d.fetch(url, timeout=timeout)
            if page is None or page.status is None or page.status >= 400:
                return f"获取失败 (HTTP {getattr(page, 'status', '?')}): {url}"

            # 选择器模式
            if selector:
                elements = page.css(selector)
                if elements:
                    texts = []
                    for el in elements[:20]:
                        t = el.text if hasattr(el, 'text') else str(el)
                        if t:
                            texts.append(t.strip())
                    result = "\n\n---\n\n".join(texts)
                    if len(elements) > 20:
                        result += f"\n\n... 共 {len(elements)} 个匹配元素（仅显示前 20）"
                    return result
                else:
                    return f"未找到匹配选择器 '{selector}' 的元素"

            # 文本提取模式
            if extract_text:
                return page.text

            # 默认：返回完整 HTML
            return page.html

        except Exception as e:
            logger.warning(f"Scrapling 获取失败，降级到 requests: {e}")

    # ── 降级路径：requests + BeautifulSoup ──
    try:
        import requests as req
    except ImportError:
        return "错误：requests 未安装，无法降级获取网页"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        resp = req.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code >= 400:
            return f"获取失败 (HTTP {resp.status_code}): {url}"

        if selector or extract_text:
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, 'lxml')
            except ImportError:
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp.text, 'html.parser')
                except ImportError:
                    # 纯文本降级
                    if extract_text:
                        return _strip_html_simple(resp.text)
                    return resp.text

            if selector:
                elements = soup.select(selector)
                if elements:
                    texts = []
                    for el in elements[:20]:
                        t = el.get_text(separator=' ', strip=True)
                        if t:
                            texts.append(t)
                    result = "\n\n---\n\n".join(texts)
                    if len(elements) > 20:
                        result += f"\n\n... 共 {len(elements)} 个匹配元素（仅显示前 20）"
                    return result
                else:
                    return f"未找到匹配选择器 '{selector}' 的元素"

            # 提取文本
            text = soup.get_text(separator='\n', strip=True)
            return text

        return resp.text

    except req.exceptions.Timeout:
        return f"请求超时 ({timeout}秒): {url}"
    except req.exceptions.ConnectionError as e:
        return f"连接失败: {url} — {e}"
    except Exception as e:
        return f"获取失败: {e}"


def _browse_web_js(url: str, extract_text: bool = False, selector: str = None,
                   timeout: int = 30, wait_for: str = None, **kwargs) -> str:
    """
    使用 JavaScript 渲染引擎浏览网页（需要 Scrapling + Playwright）。

    参数:
        url: 目标网页 URL
        extract_text: 提取纯文本
        selector: CSS 选择器
        timeout: 超时时间（秒）
        wait_for: 等待某个选择器出现后再提取
    """
    if not url:
        return "错误：URL 不能为空"

    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    if _scrapling_playwright_available:
        try:
            d = PlayWrightFetcher(auto_match=True)
            page = d.fetch(url, timeout=timeout)

            if page is None or page.status is None or page.status >= 400:
                return f"获取失败 (HTTP {getattr(page, 'status', '?')}): {url}"

            if selector:
                if wait_for:
                    page.wait_for_selector(wait_for, timeout=timeout)
                elements = page.css(selector)
                if elements:
                    texts = []
                    for el in elements[:20]:
                        t = el.text if hasattr(el, 'text') else str(el)
                        if t:
                            texts.append(t.strip())
                    return "\n\n---\n\n".join(texts)
                return f"未找到匹配选择器 '{selector}' 的元素"

            return page.text if extract_text else page.html

        except Exception as e:
            logger.warning(f"PlayWright 获取失败: {e}")
            return f"JavaScript 渲染获取失败: {e}"

    return ("JavaScript 渲染模式不可用。\n"
            "请安装: pip install scrapling playwright && python -m playwright install")


def _strip_html_simple(html: str) -> str:
    """简单 HTML 标签剥离（无 BeautifulSoup 时的降级方案）"""
    import re
    # 移除 script 和 style
    html = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # 移除所有标签
    text = re.sub(r'<[^>]+>', ' ', html)
    # 合并空白
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def register_tools(registry):
    registry.register(
        "browse_web",
        _browse_web,
        "浏览网页：获取网页的 HTML 内容或纯文本。支持 CSS 选择器精确提取。"
        "基于 Scrapling 的隐身模式自动绕过反爬检测，失败时降级到 requests。",
        {
            "url": {"type": "string", "description": "目标网页 URL"},
            "extract_text": {"type": "boolean", "description": "是否提取纯文本（剥离HTML标签），默认false（返回HTML）"},
            "selector": {"type": "string", "description": "CSS选择器，只提取匹配元素的内容（可选）"},
            "stealth": {"type": "boolean", "description": "是否启用隐身模式绕过反爬，默认true"},
            "timeout": {"type": "integer", "description": "超时时间(秒)，默认15"}
        }
    )

    registry.register(
        "browse_web_js",
        _browse_web_js,
        "JavaScript 渲染浏览网页：使用无头浏览器获取需要 JS 渲染的页面内容。"
        "需要安装 Scrapling + Playwright。",
        {
            "url": {"type": "string", "description": "目标网页 URL"},
            "extract_text": {"type": "boolean", "description": "是否提取纯文本"},
            "selector": {"type": "string", "description": "CSS选择器（可选）"},
            "timeout": {"type": "integer", "description": "超时时间(秒)，默认30"},
            "wait_for": {"type": "string", "description": "等待某个CSS选择器出现后再提取（可选）"}
        }
    )
