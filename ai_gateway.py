"""
AI Gateway — 插件驱动多 AI 网关
================================

插件架构：
  每个第三方 AI 提供商是一个独立插件文件（plugins/provider_*.py），
  网关通过 PluginLoader 自动发现并加载。

特性：
  - 插件自动发现：放入 plugins/ 目录即可
  - 权限管理：插件声明所需权限，加载时校验
  - 动态令牌：支持自动获取/刷新临时 API Key
  - 链式降级：插件按优先级调用，全部失败回退 DeepSeek

使用:
    from ai_gateway import AIGateway
    from plugins.loader import PluginLoader

    loader = PluginLoader()
    gateway = AIGateway(loader)
    gateway.load_plugins({
        "openrouter": {"api_key": "sk-or-v1-...", "model": "anthropic/claude-sonnet-4"},
        "groq": {"api_key": "gsk_...", "model": "llama-4-maverick-17b-128e-instruct"},
    })
    session.attach_gateway(gateway)
"""

import json
import logging
import time
from typing import Any, Callable, Dict, Generator, List, Optional

import requests

from plugins.base import BaseProviderPlugin, Permission
from plugins.loader import PluginLoader

logger = logging.getLogger("MHAgent.Gateway")

DEFAULT_TIMEOUT = 120


class AIGateway:
    """插件驱动 AI 网关 — 自动发现、权限管理、链式降级"""

    # ── ANSI 颜色 ──
    _C = {'R': '\033[91m', 'G': '\033[92m', 'Y': '\033[93m', 'C': '\033[96m', 'W': '\033[0m'}

    def __init__(self, loader: Optional[PluginLoader] = None):
        self.loader = loader or PluginLoader()
        self.plugins: List[BaseProviderPlugin] = []
        self._fallback_caller: Optional[Callable] = None
        self._total_calls = 0
        self._total_fallbacks = 0

    # ═══════════════════════════════════════════
    # 插件管理
    # ═══════════════════════════════════════════

    def load_plugins(self, configs: Optional[Dict[str, Dict[str, Any]]] = None) -> int:
        self.plugins = self.loader.discover_and_load(configs)
        logger.info(f"Gateway: 已加载 {len(self.plugins)} 个插件")
        for p in self.plugins:
            logger.info(f"  [{p.name}] {p.description} → {p.get_model()}")
        return len(self.plugins)

    def add_plugin(self, plugin: BaseProviderPlugin):
        self.plugins.append(plugin)

    def remove_plugin(self, name: str):
        self.plugins = [p for p in self.plugins if p.name != name]

    def set_fallback(self, caller: Callable):
        self._fallback_caller = caller

    # ═══════════════════════════════════════════
    # 状态查询
    # ═══════════════════════════════════════════

    def get_status(self) -> dict:
        return {
            "total_calls": self._total_calls,
            "total_fallbacks": self._total_fallbacks,
            "loader": self.loader.get_status(),
            "plugins": [p.to_dict() for p in self.plugins],
        }

    # ═══════════════════════════════════════════
    # 核心调用
    # ═══════════════════════════════════════════

    def call_stream(
        self,
        url: str,
        headers: dict,
        payload: dict,
        stream: bool = True,
    ) -> Generator[Dict[str, Any], None, None]:
        """插件优先级链式流式调用

        支持两种响应格式：
        1. SSE 流式（data: {...}）
        2. 标准 JSON 非流式（适用于不支持流式的 API）
        """
        self._total_calls += 1

        for plugin in self.plugins:
            if not plugin.is_loaded:
                continue

            if plugin.need_token_refresh:
                if not plugin.refresh_token():
                    logger.warning(f"Gateway: [{plugin.name}] 令牌刷新失败，跳过")
                    continue

            model = plugin.get_model()
            logger.info(f"Gateway: 尝试插件 [{plugin.name}] ({model})")
            print(f"  {self._C['C']}⏳ [{plugin.name}]{self._C['W']} 调用中...", end='', flush=True)

            try:
                prov_url = f"{plugin.get_api_base()}{plugin.get_api_path()}"
                prov_headers = plugin.get_headers()
                prov_payload = plugin.adapt_payload(payload)
                prov_payload = plugin.on_pre_request(prov_payload)

                # 判断是否使用流式：如果payload中stream=False则非流式
                use_stream = prov_payload.get("stream", stream)

                resp = requests.post(
                    prov_url,
                    headers=prov_headers,
                    json=prov_payload,
                    stream=use_stream,
                    timeout=DEFAULT_TIMEOUT,
                )

                if resp.status_code == 200:
                    logger.info(f"Gateway: [{plugin.name}] 调用成功")
                    print(f"\r  {self._C['G']}✅ [{plugin.name}]{self._C['W']} 调用成功{' ' * 20}")

                    if use_stream:
                        # SSE 流式解析（防御非标准chunk/解码失败）
                        for line in resp.iter_lines():
                            if not line:
                                continue
                            try:
                                line_str = line.decode("utf-8", errors="replace")
                            except Exception:
                                continue
                            if not line_str or line_str.startswith(":") or line_str == "\r":
                                continue
                            if line_str.startswith("data: "):
                                raw = line_str[6:]
                                if raw.strip() == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(raw)
                                    if not isinstance(chunk, dict):
                                        continue
                                    chunk = plugin.on_post_chunk(chunk)
                                    if isinstance(chunk, dict) and "choices" in chunk:
                                        yield chunk
                                except (json.JSONDecodeError, TypeError, AttributeError):
                                    pass
                    else:
                        # 非流式 JSON 解析（防御非JSON/无choices响应）
                        try:
                            data = resp.json()
                        except (json.JSONDecodeError, ValueError):
                            logger.warning(f"Gateway: [{plugin.name}] 非JSON响应")
                            continue
                        if not isinstance(data, dict):
                            logger.warning(f"Gateway: [{plugin.name}] 响应格式异常")
                            continue
                        choices = data.get("choices", [])
                        if not choices or not isinstance(choices, list):
                            logger.warning(f"Gateway: [{plugin.name}] 缺少choices")
                            continue
                        msg = choices[0].get("message", {}) if choices else {}
                        wrapped = {
                            "choices": [{
                                "delta": msg,
                                "finish_reason": choices[0].get("finish_reason", "stop") if choices else "stop",
                                "index": 0,
                            }]
                        }
                        try:
                            wrapped = plugin.on_post_chunk(wrapped)
                        except Exception:
                            pass
                        if isinstance(wrapped, dict) and wrapped.get("choices"):
                            yield wrapped

                    return  # 成功，结束
                else:
                    err_text = resp.text[:500]
                    logger.warning(f"Gateway: [{plugin.name}] HTTP {resp.status_code}: {err_text}")
                    print(f"\r  {self._C['R']}❌ [{plugin.name}]{self._C['W']} HTTP {resp.status_code}{' ' * 20}")
                    if resp.status_code not in (429, 500, 502, 503, 504):
                        continue

            except requests.exceptions.Timeout:
                logger.warning(f"Gateway: [{plugin.name}] 超时")
                print(f"\r  {self._C['Y']}⏱ [{plugin.name}]{self._C['W']} 超时{' ' * 20}")
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Gateway: [{plugin.name}] 连接失败: {e}")
                print(f"\r  {self._C['Y']}🔌 [{plugin.name}]{self._C['W']} 连接失败{' ' * 20}")
            except Exception as e:
                logger.exception(f"Gateway: [{plugin.name}] 异常: {e}")
                print(f"\r  {self._C['R']}💥 [{plugin.name}]{self._C['W']} {type(e).__name__}{' ' * 20}")
                if not plugin.on_error(e):
                    continue

        # 全部失败 → 回退
        self._total_fallbacks += 1
        enabled = [p.name for p in self.plugins if p.is_loaded]
        logger.warning(f"Gateway: 所有插件失败 ({enabled}), 降级到原始 DeepSeek")
        print(f"  {self._C['R']}⬇ 降级到 DeepSeek{self._C['W']}")
        if self._fallback_caller:
            try:
                yield from self._fallback_caller()
            except TypeError as te:
                logger.exception(f"Fallback TypeError: {te}")
                yield {"error": f"Fallback error: {te}"}
        else:
            yield {"error": "所有 AI 提供商均不可用，且未设置回退函数。"}


# ═══════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════

def create_gateway(configs: Optional[Dict[str, Dict[str, Any]]] = None) -> AIGateway:
    gateway = AIGateway()
    if configs:
        gateway.load_plugins(configs)
    return gateway
