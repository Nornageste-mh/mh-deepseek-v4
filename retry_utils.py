# retry_utils.py
"""
三层防御重试系统 — 参考 DeepSeek TUI 设计

Layer 1: send_with_retry — HTTP 入口 + 令牌桶限流 + 调用 with_retry
Layer 2: with_retry — 指数退避 + 抖动 + Retry-After + 可重试性判断
Layer 3: LlmError — 错误分类（将可恢复的 400 重新归类为可重试）

设计原则：
- 大多数 400 不可重试（InvalidRequest → 立即失败）
- 配额不足的 400 → RateLimited（允许有限重试）
- 5xx/429/网络错误 → 可重试
- 尊重服务器 Retry-After 头
"""
import time
import random
import logging
import functools
from enum import Enum
from typing import Any, Callable, Generator, Optional, Tuple

import requests
from requests.exceptions import (
    ConnectionError as ReqConnectionError,
    Timeout,
    HTTPError,
)

from config import RETRY_MAX_ATTEMPTS, RETRY_MIN_WAIT, RETRY_MAX_WAIT

logger = logging.getLogger("MHAgent.Retry")


# ═══════════════════════════════════════════════════════════════
# Layer 3: LlmError — 错误分类
# ═══════════════════════════════════════════════════════════════

class ErrorType(Enum):
    INVALID_REQUEST = "invalid_request"       # 400 通用（不可重试）
    INSUFFICIENT_QUOTA = "insufficient_quota" # 402 变体（可重试）
    CONTEXT_LENGTH = "context_length"         # context 超长（不可重试，但可截断）
    CONTENT_POLICY = "content_policy"         # 内容审查（不可重试）
    MODEL_ERROR = "model_error"               # 模型不存在/不可用（不可重试）
    THINKING_ERROR = "thinking_error"         # 思考模式错误（可自动修复后重试）
    AUTH_FAILED = "auth_failed"               # 认证失败（不可重试）
    RATE_LIMITED = "rate_limited"             # 429（可重试）
    SERVER_ERROR = "server_error"             # 500（可重试）
    SERVER_OVERLOADED = "server_overloaded"   # 503（可重试）
    NETWORK_ERROR = "network_error"           # 网络/连接错误（可重试）
    TIMEOUT = "timeout"                       # 超时（可重试）
    UNKNOWN = "unknown"


class LlmError(Exception):
    """分类后的 LLM API 错误"""

    def __init__(self, error_type: ErrorType, status_code: int = 0,
                 message: str = "", retry_after: int = 0,
                 raw_body: str = "", recoverable_action: str = None):
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code
        self.message = message
        self.retry_after = retry_after
        self.raw_body = raw_body
        self.recoverable_action = recoverable_action

    def is_retryable(self) -> bool:
        """是否值得重试"""
        return self.error_type in (
            ErrorType.RATE_LIMITED,
            ErrorType.SERVER_ERROR,
            ErrorType.SERVER_OVERLOADED,
            ErrorType.NETWORK_ERROR,
            ErrorType.TIMEOUT,
            ErrorType.INSUFFICIENT_QUOTA,
            ErrorType.THINKING_ERROR,
            ErrorType.CONTEXT_LENGTH,
        )

    def is_auto_fixable(self) -> bool:
        return self.recoverable_action is not None

    def to_dict(self) -> dict:
        return {
            "code": self.status_code,
            "type": self.error_type.value,
            "message": self.message,
            "recoverable": self.is_retryable(),
            "action": self.recoverable_action,
            "retry_after": self.retry_after,
        }

    @classmethod
    def from_http_response(cls, status_code: int, response_text: str,
                           retry_after: int = 0) -> 'LlmError':
        """从 HTTP 响应分类错误（Layer 3 核心）"""
        text_lower = response_text.lower()
        body = response_text[:2000]

        # 429 → RateLimited
        if status_code == 429:
            return cls(ErrorType.RATE_LIMITED, status_code,
                       f"Rate limited (HTTP 429): {body[:200]}",
                       retry_after=max(retry_after, 5))

        # 5xx → ServerError / ServerOverloaded
        if status_code == 503 or (status_code == 500 and any(
            kw in text_lower for kw in ("overloaded", "busy", "unavailable",
                                         "负载", "繁忙"))):
            return cls(ErrorType.SERVER_OVERLOADED, status_code,
                       f"Server overloaded (HTTP {status_code})",
                       retry_after=2)
        if 500 <= status_code < 600:
            return cls(ErrorType.SERVER_ERROR, status_code,
                       f"Server error (HTTP {status_code})",
                       retry_after=1)

        # 400 细分
        if status_code == 400 or status_code == 422:
            if any(kw in text_lower for kw in (
                "insufficient_quota", "quota exceeded", "余额不足",
                "account balance insufficient")):
                return cls(ErrorType.INSUFFICIENT_QUOTA, status_code,
                           f"Insufficient quota (HTTP {status_code})",
                           retry_after=30)

            if any(kw in text_lower for kw in (
                "context length", "maximum context", "token limit",
                "too many tokens", "max_len")):
                return cls(ErrorType.CONTEXT_LENGTH, status_code,
                           f"Context length exceeded (HTTP {status_code})",
                           recoverable_action="truncate_context")

            if any(kw in text_lower for kw in (
                "content policy", "content_filter", "safety", "moderation")):
                return cls(ErrorType.CONTENT_POLICY, status_code,
                           f"Content policy violation (HTTP {status_code})")

            if any(kw in text_lower for kw in (
                "thinking", "reasoning_effort", "reasoning_content")):
                return cls(ErrorType.THINKING_ERROR, status_code,
                           f"Thinking mode error (HTTP {status_code})",
                           recoverable_action="remove_conflicting_params")

            if any(kw in text_lower for kw in (
                "model", "not found", "not available", "not supported")):
                return cls(ErrorType.MODEL_ERROR, status_code,
                           f"Model not found (HTTP {status_code})")

            return cls(ErrorType.INVALID_REQUEST, status_code,
                       f"Invalid request (HTTP {status_code}): {body[:300]}")

        # 401/402
        if status_code == 401:
            return cls(ErrorType.AUTH_FAILED, status_code,
                       f"Auth failed (HTTP 401)")
        if status_code == 402:
            return cls(ErrorType.INSUFFICIENT_QUOTA, status_code,
                       f"Insufficient balance (HTTP 402)", retry_after=30)

        return cls(ErrorType.UNKNOWN, status_code,
                   f"Unknown error (HTTP {status_code}): {body[:300]}")

    @classmethod
    def from_exception(cls, e: Exception) -> 'LlmError':
        """从网络异常分类错误"""
        msg = str(e)[:500]
        if isinstance(e, ReqConnectionError):
            return cls(ErrorType.NETWORK_ERROR, 0, f"Connection error: {msg}")
        if isinstance(e, Timeout):
            return cls(ErrorType.TIMEOUT, 0, f"Timeout: {msg}")
        return cls(ErrorType.UNKNOWN, 0, f"Exception: {msg}")


# ═══════════════════════════════════════════════════════════════
# Layer 2: with_retry — 指数退避 + 抖动
# ═══════════════════════════════════════════════════════════════

def with_retry(retry_config: dict, operation: Callable,
               on_retry: Callable = None) -> Any:
    """
    重试编排器（Layer 2）

    参数:
        retry_config: {"max_attempts": 3, "min_wait": 1, "max_wait": 30}
        operation: 无参 callable，返回结果或 raise LlmError
        on_retry: 每次重试前的回调 (error, attempt, delay) -> None
    """
    max_attempts = retry_config.get("max_attempts", 3)
    min_wait = retry_config.get("min_wait", 1)
    max_wait = retry_config.get("max_wait", 30)

    last_error = None

    for attempt in range(max_attempts):
        try:
            return operation()
        except LlmError as e:
            last_error = e
            if not e.is_retryable():
                raise

            # 使用服务器 Retry-After 或指数退避
            delay = e.retry_after if e.retry_after > 0 else min(
                max_wait, min_wait * (2 ** attempt) + random.uniform(0, 1)
            )

            if attempt < max_attempts - 1:
                logger.info(
                    f"重试 {attempt+1}/{max_attempts}（{e.error_type.value}），"
                    f"等待 {delay:.1f}s"
                )
                if on_retry:
                    on_retry(e, attempt + 1, delay)
                time.sleep(delay)
            else:
                raise

        except Exception as e:
            last_error = LlmError.from_exception(e)
            if not last_error.is_retryable():
                raise

            delay = min(max_wait, min_wait * (2 ** attempt) + random.uniform(0, 1))
            if attempt < max_attempts - 1:
                logger.info(
                    f"重试 {attempt+1}/{max_attempts}（网络错误），等待 {delay:.1f}s"
                )
                if on_retry:
                    on_retry(last_error, attempt + 1, delay)
                time.sleep(delay)
            else:
                raise last_error

    raise last_error or RuntimeError("Retry exhausted")


# ═══════════════════════════════════════════════════════════════
# 令牌桶（Layer 1 的一部分）
# ═══════════════════════════════════════════════════════════════

class TokenBucket:
    """简单令牌桶限流器"""

    def __init__(self, rate: float = 3.0, burst: int = 5):
        self.rate = rate          # 每秒生成 rate 个令牌
        self.burst = burst        # 最大令牌数
        self.tokens = float(burst)
        self.last_refill = time.monotonic()

    def acquire(self) -> float:
        """获取一个令牌，返回需要等待的秒数（0 表示立即可用）"""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0
        wait = (1.0 - self.tokens) / self.rate
        self.tokens = 0.0
        return wait


# ═══════════════════════════════════════════════════════════════
# 断路器模式（保留向后兼容）
# ═══════════════════════════════════════════════════════════════

class CircuitBreaker:
    """断路器 — 兼容旧代码的 failure_tracker"""

    CLOSED = "closed"; OPEN = "open"; HALF_OPEN = "half_open"

    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 30):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = self.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time = 0.0
        self._pause_reason = ""

    @property
    def paused(self) -> bool:
        return self._state != self.CLOSED

    @property
    def pause_reason(self) -> str:
        return self._pause_reason

    @paused.setter
    def paused(self, value: bool):
        if value:
            if self._state == self.CLOSED:
                self._state = self.OPEN
        else:
            self._state = self.CLOSED
            self.consecutive_failures = 0

    def record_success(self):
        self.consecutive_failures = 0
        self._state = self.CLOSED

    def record_failure(self, reason: str = ""):
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        if self._state == self.HALF_OPEN:
            self._state = self.OPEN
        elif self.consecutive_failures >= self.failure_threshold:
            self._state = self.OPEN
            self._pause_reason = reason

    def can_proceed(self) -> bool:
        if self._state in (self.CLOSED, self.HALF_OPEN):
            return True
        if time.time() - self.last_failure_time >= self.recovery_timeout:
            self._state = self.HALF_OPEN
            return True
        return False

    def reset(self):
        self.consecutive_failures = 0
        self._state = self.CLOSED
        self._pause_reason = ""


# 全局实例（向后兼容 session.py）
circuit_breaker = CircuitBreaker()
failure_tracker = circuit_breaker


def is_network_ok() -> bool:
    """快速网络检测"""
    targets = ["https://api.deepseek.com/ping", "https://www.baidu.com"]
    for target in targets:
        try:
            requests.get(target, timeout=3)
            return True
        except Exception:
            continue
    return False
