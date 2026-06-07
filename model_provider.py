# model_provider.py
"""
模型提供商管理 — DeepSeek + 妹居DeepSeek
==========================================

设计原则：
1. 每个 provider 管理自己的模型列表和 API 端点
2. 妹居DeepSeek 不支持思考模式（thinking），自动禁用
3. 支持热切换：session 可在运行时切换 provider/model
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional

import requests

from retry_utils import (
    LlmError,
    ErrorType,
    TokenBucket,
    with_retry,
)

logger = logging.getLogger("MHAgent.Provider")

# ── ANSI 颜色 ──
_C = {'G': '\033[92m', 'Y': '\033[93m', 'C': '\033[96m', 'R': '\033[91m', 'W': '\033[0m'}


# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class ModelInfo:
    """单个模型的信息"""
    id: str                    # 模型 ID，如 "deepseek-v4-flash"
    display_name: str          # 显示名称，如 "V4 Flash（快速）"
    supports_thinking: bool = True
    context_length: int = 128000
    description: str = ""


@dataclass
class Provider:
    """模型提供商"""
    id: str                    # "deepseek" / "meiju"
    name: str                  # "DeepSeek" / "妹居DeepSeek"
    api_base: str              # API 基础 URL
    api_path: str = "/chat/completions"
    supports_thinking: bool = True  # 提供商级别是否支持思考
    models: List[ModelInfo] = field(default_factory=list)
    # 鉴权（DeepSeek 用 api_key，妹居用 JWT）
    auth_type: str = "api_key"    # "api_key" / "jwt"
    _api_key: str = ""
    _jwt: Optional[str] = None
    _jwt_refreshed_at: float = 0
    _config: Dict[str, Any] = field(default_factory=dict)

    @property
    def api_key(self) -> str:
        return self._api_key

    def set_api_key(self, key: str):
        self._api_key = key

    def set_jwt_config(self, phone: str, password: str):
        self._config["phone"] = phone
        self._config["password"] = password

    def get_token(self) -> Optional[str]:
        """获取鉴权 token（api_key 或 JWT）"""
        if self.auth_type == "api_key":
            return self._api_key
        elif self.auth_type == "jwt":
            if not self._jwt or time.time() - self._jwt_refreshed_at > 21600:
                self._refresh_jwt()
            return self._jwt
        return None

    def _refresh_jwt(self) -> bool:
        """刷新/获取 JWT token"""
        phone = self._config.get("phone")
        password = self._config.get("password")
        if not phone or not password:
            return False

        # 先尝试 refresh
        if self._jwt:
            try:
                resp = requests.post(
                    f"{self.api_base}/auth/refresh",
                    headers={"Authorization": f"Bearer {self._jwt}",
                             "Content-Type": "application/json"},
                    json={}, timeout=10, verify=False,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("token"):
                        self._jwt = data["token"]
                        self._jwt_refreshed_at = time.time()
                        return True
            except Exception:
                pass

        # 登录
        try:
            resp = requests.post(
                f"{self.api_base}/auth/login",
                json={"phone": phone, "password": password},
                headers={"Content-Type": "application/json"},
                timeout=10, verify=False,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._jwt = data.get("token")
                self._jwt_refreshed_at = time.time()
                logger.info(f"妹居DeepSeek: 登录成功 ({phone})")
                return True
        except Exception as e:
            logger.warning(f"妹居DeepSeek: 登录失败: {e}")

        return False

    def fetch_models(self) -> List[ModelInfo]:
        """从 API 拉取真实可用模型列表"""
        if self.id == "deepseek":
            return self._fetch_deepseek_models()
        elif self.id == "meiju":
            return self._fetch_meiju_models()
        return self.models

    def _fetch_deepseek_models(self) -> List[ModelInfo]:
        """DeepSeek 已知模型列表"""
        return [
            ModelInfo(
                id="deepseek-v4-flash",
                display_name="V4 Flash（快速）",
                supports_thinking=True,
                context_length=128000,
                description="快速推理，适合日常对话和简单任务"
            ),
            ModelInfo(
                id="deepseek-v4-pro",
                display_name="V4 Pro（专家）",
                supports_thinking=True,
                context_length=128000,
                description="深度推理，适合复杂分析和编码"
            ),
        ]

    def _fetch_meiju_models(self) -> List[ModelInfo]:
        """妹居DeepSeek 模型列表（不支持 thinking）"""
        return [
            ModelInfo(
                id="妹居deepseek",
                display_name="妹居DeepSeek",
                supports_thinking=False,
                context_length=64000,
                description="妹居物语后端模型（不支持思考模式）"
            ),
        ]

    def get_headers(self) -> Dict[str, str]:
        token = self.get_token()
        if self.auth_type == "api_key":
            return {
                "Authorization": f"Bearer {token}" if token else "",
                "Content-Type": "application/json",
            }
        else:
            return {
                "Authorization": f"Bearer {token}" if token else "",
                "Content-Type": "application/json",
            }

    def adapt_payload(self, payload: Dict, think_mode: bool = False,
                      model_id: str = None) -> Dict:
        """适配请求 payload"""
        adapted = dict(payload)

        if self.id == "meiju":
            # 妹居使用 /chat/forward 和特定格式
            adapted["provider"] = "妹居deepseek"
            # 移除 thinking — 妹居不支持
            adapted.pop("thinking", None)
            adapted.pop("reasoning_effort", None)
            adapted.pop("presence_penalty", None)
            adapted.pop("frequency_penalty", None)
            # 移除 agent system prompt，使用妹居自己的
            if "messages" in adapted:
                adapted["messages"] = [m for m in adapted["messages"]
                                       if m.get("role") != "system"]
            adapted["stream"] = False  # 妹居不支持流式
        else:
            # DeepSeek：正常格式
            if model_id:
                adapted["model"] = model_id
            if think_mode and self.supports_thinking:
                adapted["thinking"] = {"type": "enabled"}
            elif not think_mode:
                adapted["thinking"] = {"type": "disabled"}

        return adapted

    def call_stream(self, payload: Dict, think_mode: bool = False,
                    model_id: str = None) -> Generator:
        """流式调用 API"""
        url = f"{self.api_base}{self.api_path}"
        adapted = self.adapt_payload(payload, think_mode, model_id)

        if self.id == "meiju":
            # 妹居：非流式调用，包装为流式
            yield from self._call_meiju(url, adapted)
        else:
            yield from self._call_deepseek_with_retry(url, adapted)

    def _call_deepseek_with_retry(self, url: str, payload: Dict) -> Generator:
        """DeepSeek 流式调用 — Layer 1: 令牌桶 + with_retry"""
        # 令牌桶限流
        if not hasattr(self, '_token_bucket'):
            self._token_bucket = TokenBucket(rate=3.0, burst=5)
        wait = self._token_bucket.acquire()
        if wait > 0:
            time.sleep(wait)

        headers = self.get_headers()

        def try_request():
            """单次 HTTP 请求，成功返回 response，失败 raise LlmError"""
            resp = requests.post(url, headers=headers, json=payload,
                                 stream=True, timeout=120)
            if resp.status_code == 200:
                return resp
            # 读取错误体并分类
            try:
                error_body = resp.text[:2000]
            except Exception:
                error_body = ""
            retry_after = 0
            try:
                ra = resp.headers.get("Retry-After", "")
                retry_after = int(ra) if ra.isdigit() else 0
            except Exception:
                pass
            raise LlmError.from_http_response(
                resp.status_code, error_body, retry_after
            )

        def on_retry(err: LlmError, attempt: int, delay: float):
            """重试回调：向前端发送 retry_info"""
            yield {"retry_info": {
                "wait": delay, "attempt": attempt,
                "max_retries": 3, "error_type": err.error_type.value
            }}

        try:
            resp = with_retry(
                {"max_attempts": 3, "min_wait": 1, "max_wait": 30},
                try_request,
                None  # 流式场景下不传 on_retry，因为 yield 不能跨函数
            )
        except LlmError as e:
            yield {"error": f"API 错误 (HTTP {e.status_code}): {e.message[:500]}"}
            return
        except Exception as e:
            yield {"error": f"网络异常: {e}"}
            return

        if resp is None:
            yield {"error": "重试耗尽，请求失败。"}
            return

        # 流式解析
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
                    if isinstance(chunk, dict) and "choices" in chunk:
                        yield chunk
                except (json.JSONDecodeError, TypeError):
                    pass

    def _call_meiju(self, url: str, payload: Dict) -> Generator:
        """妹居非流式调用，包装为流式"""
        headers = self.get_headers()
        try:
            resp = requests.post(url, headers=headers, json=payload,
                                 timeout=120, verify=False)
            if resp.status_code != 200:
                yield {"error": f"妹居 API 错误 (HTTP {resp.status_code}): {resp.text[:500]}"}
                return

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                yield {"error": "妹居 API 返回空结果"}
                return

            msg = choices[0].get("message", {})
            content = msg.get("content", "")
            finish_reason = choices[0].get("finish_reason", "stop")

            # 构建 delta
            delta = {}
            if content:
                delta["content"] = content
            # 提取 tool_calls（妹居透传 DeepSeek 格式）
            if "tool_calls" in msg and msg["tool_calls"]:
                delta["tool_calls"] = msg["tool_calls"]

            # 将非流式结果包装为单 chunk
            chunk = {
                "choices": [{
                    "delta": delta,
                    "finish_reason": finish_reason,
                    "index": 0,
                }]
            }
            yield chunk
        except requests.exceptions.ConnectionError as e:
            yield {"error": f"妹居网络连接失败: {e}"}
        except requests.exceptions.Timeout:
            yield {"error": "妹居请求超时（120秒）"}
        except Exception as e:
            yield {"error": f"妹居 API 调用异常: {e}"}


# ═══════════════════════════════════════════════════════════════
# 模型管理器
# ═══════════════════════════════════════════════════════════════

class ModelProviderManager:
    """模型提供商管理器 — 单例，管理所有 provider"""

    def __init__(self):
        self.providers: Dict[str, Provider] = {}

    def register(self, provider: Provider):
        self.providers[provider.id] = provider
        logger.info(f"Provider 已注册: {provider.name} ({len(provider.models)} 个模型)")

    def get(self, provider_id: str) -> Optional[Provider]:
        return self.providers.get(provider_id)

    def list_providers(self) -> List[Dict]:
        """列出所有提供商及其模型"""
        result = []
        for p in self.providers.values():
            result.append({
                "id": p.id,
                "name": p.name,
                "supports_thinking": p.supports_thinking,
                "models": [
                    {
                        "id": m.id,
                        "display_name": m.display_name,
                        "supports_thinking": m.supports_thinking,
                        "context_length": m.context_length,
                        "description": m.description,
                    }
                    for m in p.models
                ]
            })
        return result


def create_default_providers(deepseek_api_key: str = "",
                              meiju_phone: str = "17600000001",
                              meiju_password: str = "12345678") -> ModelProviderManager:
    """创建默认的 provider 管理器"""
    mgr = ModelProviderManager()

    # DeepSeek
    ds = Provider(
        id="deepseek",
        name="DeepSeek",
        api_base="https://api.deepseek.com",
        supports_thinking=True,
        auth_type="api_key",
    )
    ds.set_api_key(deepseek_api_key)
    ds.models = ds.fetch_models()
    mgr.register(ds)

    # 妹居DeepSeek
    mj = Provider(
        id="meiju",
        name="妹居DeepSeek",
        api_base="https://test.yukiwithyou.asia/api/v1",
        api_path="/chat/forward",
        supports_thinking=False,
        auth_type="jwt",
    )
    mj.set_jwt_config(meiju_phone, meiju_password)
    mj.models = mj.fetch_models()
    mgr.register(mj)

    return mgr
