"""
MH-DeepSeek Agent V4 — 会话管理 & API 错误处理
基于 DeepSeek V4 API 官方文档 (api-docs.deepseek.com) 实现完整错误处理

官方错误码文档：https://api-docs.deepseek.com/zh-cn/quick_start/error_codes
官方限速文档：https://api-docs.deepseek.com/zh-cn/quick_start/rate_limit
官方思考模式：https://api-docs.deepseek.com/zh-cn/guides/thinking_mode
"""

import os
import json
import re
import time
import threading
import logging
import requests
from typing import List, Dict, Generator, Optional, Tuple
import os

from config import (
    DEEPSEEK_API_URL,
    DEEPSEEK_BETA_URL,
    MODEL_FAST, MODEL_EXPERT,
    DEFAULT_REASONING_EFFORT, EXPERT_REASONING_EFFORT,
    MAX_ITERATIONS, MAX_TOOL_OUTPUT_CHARS,
    API_CONSECUTIVE_FAIL_THRESHOLD,
    COMPRESSION_THRESHOLD_PERCENT,
    COMPRESSION_PROTECT_HEAD,
    COMPRESSION_TAIL_TOKEN_BUDGET,
    COMPRESSION_PROTECT_TOOL_TAIL,
    DEFAULT_MAX_OUTPUT_TOKENS,
    THINKING_MAX_OUTPUT_TOKENS,
    MAX_INPUT_TOKENS_PER_CALL,
    PROTECT_LAST_N_MESSAGES,
)
from memory import MemorySystem
from executor import SecureExecutor
from tool_registry import ToolRegistry
from tools import discover_and_register
from long_term_memory import LongTermMemory, set_current_identity
from context_compressor import ContextCompressor, ContextManager, estimate_tokens_rough
from model_provider import create_default_providers, ModelProviderManager, Provider
from task_tracker import TaskTracker, TaskStatus

from config import (
    DEFAULT_PROVIDER, DEFAULT_MODEL,
    MEIJU_PHONE, MEIJU_PASSWORD,
)

logger = logging.getLogger("MHAgent")


# ═══════════════════════════════════════════════════════════════
# DeepSeek V4 API 官方错误码定义（2025版）
# 来源：https://api-docs.deepseek.com/zh-cn/quick_start/error_codes
# ═══════════════════════════════════════════════════════════════

class DeepSeekErrorCode:
    """DeepSeek V4 API 官方错误码及处理策略"""
    
    HTTP_400_INVALID_FORMAT = 400
    HTTP_401_AUTH_FAILED = 401
    HTTP_402_INSUFFICIENT_BALANCE = 402
    HTTP_422_INVALID_PARAMS = 422
    HTTP_429_RATE_LIMIT = 429
    HTTP_500_SERVER_ERROR = 500
    HTTP_503_SERVER_OVERLOADED = 503
    
    KNOWN_ERROR_PATTERNS = {
        "invalid_request_format": ["invalid request body", "bad request", "invalid_json", "parse error"],
        "auth_failed": ["invalid api key", "authentication failed", "unauthorized", "invalid credentials", "api key not found", "invalid_api_key"],
        "insufficient_balance": ["insufficient balance", "insufficient_quota", "quota exceeded", "余额不足", "account balance insufficient"],
        "invalid_parameters": ["invalid parameter", "invalid parameters", "invalid 'messages'", "messages must be", "invalid 'tools'", "invalid 'tool_choice'", "invalid 'temperature'", "invalid 'max_tokens'", "invalid 'stream'", "unsupported parameter", "must be one of", "unknown parameter"],
        "rate_limit": ["rate limit", "too many requests", "rate_limit_reached", "tpm limit", "rpm limit", "requests rate limit"],
        "server_error": ["internal server error", "server error", "internal_error"],
        "server_overloaded": ["service unavailable", "server overloaded", "service overloaded", "temporarily unavailable", "busy", "负载过高", "服务器繁忙"],
        "context_length_exceeded": ["context length", "maximum context length", "token limit", "too many tokens", "max_len", "maximum length"],
        "thinking_mode_error": ["reasoning_content", "thinking mode", "thinking is not supported", "reasoning_effort"],
    }
    
    THINKING_MODE_ERRORS = {
        "missing_reasoning_content": "思考模式下，进行了工具调用的轮次，在后续请求中必须完整回传 reasoning_content。若未正确回传，API 将返回错误。",
        "thinking_not_supported_with_params": "思考模式下不支持 temperature、presence_penalty、frequency_penalty 参数。",
    }
    
    RATE_LIMIT_INFO = "DeepSeek API 会根据负载情况动态限制并发量。到达上限时返回 HTTP 429。请求可能需要等待（非流式返回空行，流式返回 SSE keep-alive）。如果 10 分钟后仍未开始推理，服务器将关闭连接。"
    
    @classmethod
    def classify_error(cls, status_code: int, response_text: str) -> dict:
        text_lower = response_text.lower()
        error_type, matched_pattern = cls._match_known_pattern(text_lower)
        handler = cls._get_status_handler(status_code)
        if handler:
            result = handler(response_text, text_lower, error_type)
            if result:
                return result
        return cls._fallback_handler(status_code, response_text, error_type)
    
    @classmethod
    def _match_known_pattern(cls, text_lower: str) -> Tuple[Optional[str], Optional[str]]:
        for error_type, patterns in cls.KNOWN_ERROR_PATTERNS.items():
            for pattern in patterns:
                if pattern in text_lower:
                    return error_type, pattern
        return None, None
    
    @classmethod
    def _get_status_handler(cls, status_code: int):
        handlers = {400: cls._handle_400, 401: cls._handle_401, 402: cls._handle_402, 422: cls._handle_422, 429: cls._handle_429, 500: cls._handle_500, 503: cls._handle_503}
        return handlers.get(status_code)
    
    @classmethod
    def _handle_400(cls, response_text: str, text_lower: str, error_type: Optional[str]) -> dict:
        # 提取原始错误详情（保留完整文本供 AI 修复使用）
        raw_error = response_text[:1000]
        if error_type == "thinking_mode_error":
            return {"code": 400, "type": "thinking_mode_error", "message": raw_error, "recoverable": True, "action": "remove_conflicting_params", "retry_after": 0, "user_friendly": "思考模式下不支持 temperature 等参数。\n已自动移除冲突参数，正在重试..."}
        if error_type == "context_length_exceeded":
            return {"code": 400, "type": "context_length_exceeded", "message": raw_error, "recoverable": True, "action": "truncate_context", "retry_after": 0, "user_friendly": "对话上下文过长，正在截断早期消息..."}
        if "thinking" in text_lower and "disabled" in text_lower:
            return {"code": 400, "type": "thinking_param_error", "message": raw_error, "recoverable": True, "action": "fix_thinking_param", "retry_after": 0, "user_friendly": "思考模式参数配置异常，正在自动修正..."}
        if "tool" in text_lower and ("must be a response" in text_lower or "response to a preceding" in text_lower):
            return {"code": 400, "type": "tool_response_pairing_error", "message": raw_error, "recoverable": True, "action": "repair_messages", "retry_after": 0, "user_friendly": "Tool 消息配对错误 — 存在孤儿 tool 响应。\n正在自动修复..."}
        return {"code": 400, "type": "invalid_format", "message": raw_error, "recoverable": True, "action": "repair_messages", "retry_after": 0, "user_friendly": f"请求格式错误（HTTP 400）。\n正在自动修复消息格式..."}
    
    @classmethod
    def _handle_401(cls, response_text: str, text_lower: str, error_type: Optional[str]) -> dict:
        return {"code": 401, "type": "auth_failed", "message": f"API Key 认证失败: {response_text[:200]}", "recoverable": False, "action": "check_api_key", "retry_after": 0, "user_friendly": "❌ API Key 认证失败（HTTP 401）。\n原因：API Key 错误或已失效。\n解决方法：\n  1. 请检查 config.py 中的 DEEPSEEK_API_KEY 是否正确\n  2. 如未设置，请运行程序后按提示输入\n  3. 如已设置，请前往 https://platform.deepseek.com 检查 Key 状态\n  4. 确认 Key 是否有调用该模型的权限"}
    
    @classmethod
    def _handle_402(cls, response_text: str, text_lower: str, error_type: Optional[str]) -> dict:
        return {"code": 402, "type": "insufficient_balance", "message": f"API 余额不足: {response_text[:200]}", "recoverable": False, "action": "check_balance", "retry_after": 0, "user_friendly": "❌ API 余额不足（HTTP 402）。\n原因：账户余额不足以支付本次请求。\n解决方法：\n  1. 请前往 https://platform.deepseek.com 充值\n  2. 在「充值」页面使用支付宝/微信在线充值\n  3. 充值余额永久有效，不会过期\n  4. 如已充值，请等待几分钟后重试"}
    
    @classmethod
    def _handle_422(cls, response_text: str, text_lower: str, error_type: Optional[str]) -> dict:
        param_hint = ""
        param_match = re.search(r"'([^']+)'", response_text)
        if param_match:
            param_hint = f"参数 '{param_match.group(1)}'"
        return {"code": 422, "type": "invalid_parameters", "message": f"请求参数错误{(': ' + param_hint) if param_hint else ''}: {response_text[:300]}", "recoverable": True, "action": "fix_parameters", "retry_after": 0, "user_friendly": f"请求参数错误（HTTP 422）。\n{'问题参数: ' + param_hint if param_hint else ''}\n错误详情: {response_text[:200]}\n正在尝试自动修复参数..."}
    
    @classmethod
    def _handle_429(cls, response_text: str, text_lower: str, error_type: Optional[str]) -> dict:
        return {"code": 429, "type": "rate_limit", "message": f"请求速率达到上限: {response_text[:200]}", "recoverable": True, "action": "backoff_retry", "retry_after": 10, "user_friendly": "⏳ 请求速率达到上限（HTTP 429）。\n将在 10 秒后自动重试...\n\n提示：\n  - DeepSeek API 会根据负载动态限制并发\n  - 建议合理规划请求速率\n  - 可暂时切换到其他模型服务提供商"}
    
    @classmethod
    def _handle_500(cls, response_text: str, text_lower: str, error_type: Optional[str]) -> dict:
        return {"code": 500, "type": "server_error", "message": f"DeepSeek 服务器内部故障: {response_text[:200]}", "recoverable": True, "action": "wait_retry", "retry_after": 15, "user_friendly": "⚠️ DeepSeek 服务器内部故障（HTTP 500）。\n将在 15 秒后自动重试...\n如问题持续存在，请联系 DeepSeek 支持。"}
    
    @classmethod
    def _handle_503(cls, response_text: str, text_lower: str, error_type: Optional[str]) -> dict:
        return {"code": 503, "type": "server_overloaded", "message": f"DeepSeek 服务器负载过高: {response_text[:200]}", "recoverable": True, "action": "backoff_retry", "retry_after": 20, "user_friendly": "⚠️ DeepSeek 服务器繁忙（HTTP 503）。\n原因：服务器负载过高。\n将在 20 秒后自动重试...\n建议：可稍后再试或降低请求频率。"}
    
    @classmethod
    def _fallback_handler(cls, status_code: int, response_text: str, error_type: Optional[str]) -> dict:
        if error_type:
            return {"code": status_code, "type": error_type, "message": f"HTTP {status_code}: {response_text[:200]}", "recoverable": status_code >= 500, "action": "retry" if status_code >= 500 else "check_request", "retry_after": 10 if status_code >= 500 else 0, "user_friendly": f"API 返回错误 (HTTP {status_code})。\n{'正在重试...' if status_code >= 500 else '请检查请求参数。'}"}
        return {"code": status_code, "type": "unknown", "message": f"未知 API 错误 HTTP {status_code}: {response_text[:300]}", "recoverable": status_code >= 500, "action": "retry" if status_code >= 500 else "report", "retry_after": 10 if status_code >= 500 else 0, "user_friendly": f"未知 API 错误 (HTTP {status_code})。\n错误详情: {response_text[:200]}\n{'正在重试...' if status_code >= 500 else '请联系开发者。'}"}



class MessageRepairer:
    """消息格式清洗器（零干预模式）
    
    仅做两项完全无损的操作：
    1. 基础字段清洗（确保 role 和 content 存在）
    2. 合并连续的 user 消息
    不再执行任何插入、删除、重排操作。
    """
    
    @staticmethod
    def repair(messages: list) -> list:
        """主入口：仅做无损清洗"""
        messages = MessageRepairer._ensure_basic_structure(messages)
        messages = MessageRepairer._merge_consecutive_users(messages)
        return messages

    @staticmethod
    def _ensure_basic_structure(messages):
        """确保每条消息都有 role 和 content（字符串类型）"""
        repaired = []
        for msg in messages:
            if not isinstance(msg, dict): continue
            role = msg.get("role", "")
            if role not in ("system", "user", "assistant", "tool"): continue
            content = msg.get("content", "")
            repaired.append({**msg, "role": role, "content": str(content) if content else ""})
        return repaired

    @staticmethod
    def _merge_consecutive_users(messages):
        """合并连续的 user 消息（将后一个 user 的 content 拼接到前一个）"""
        merged = []
        for msg in messages:
            if merged and msg["role"] == "user" and merged[-1]["role"] == "user":
                merged[-1]["content"] += "\n" + (msg.get("content") or "")
            else:
                merged.append(dict(msg))
        return merged


class ErrorRecoveryExecutor:
    """基于 DeepSeek V4 官方错误码的错误恢复执行器"""
    
    def __init__(self, session):
        self.session = session
        self.consecutive_errors = 0
        self.max_consecutive_errors = 5
        self.last_recovery_action = None
    
    def execute_with_recovery(self, messages: List[Dict], payload: dict, headers: dict, url: str, think_mode: bool = False, max_retries: int = 3) -> Generator:
        retry_count = 0
        backoff_time = 1
        while retry_count <= max_retries:
            try:
                resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=120)
                if resp.status_code == 200:
                    self.consecutive_errors = 0
                    failure_tracker.record_success()
                    for chunk in self._process_stream_response(resp):
                        yield chunk
                    return
                error_info = DeepSeekErrorCode.classify_error(resp.status_code, resp.text)
                logger.warning(f"API 错误 [HTTP {error_info['code']}]: {error_info['type']} - {error_info['message'][:100]}")
                failure_tracker.record_failure(f"HTTP {error_info['code']} {error_info['type']}")
                if not error_info['recoverable']:
                    yield {"error": error_info['user_friendly']}
                    return
                if retry_count >= max_retries:
                    yield {"error": f"已重试 {max_retries} 次仍失败 [HTTP {error_info['code']}]。\n最后错误: {error_info['message'][:200]}\n建议: {error_info['user_friendly']}"}
                    return
                recovery_result = self._execute_recovery(error_info, payload, messages, think_mode)
                if recovery_result:
                    payload, messages = recovery_result
                    logger.info(f"恢复成功: {error_info['action']}")
                    yield {"recovery": {"type": error_info['type'], "message": error_info['user_friendly'], "action": error_info['action']}}
                retry_count += 1
                wait_time = error_info['retry_after'] if error_info['retry_after'] > 0 else backoff_time
                if wait_time > 0:
                    logger.info(f"等待 {wait_time} 秒后重试...")
                    yield {"retry_info": {"wait": wait_time, "attempt": retry_count, "max_retries": max_retries, "error_type": error_info['type']}}
                    time.sleep(wait_time)
                backoff_time = min(backoff_time * 2, 60)
            except requests.exceptions.ConnectionError as e:
                self.consecutive_errors += 1
                failure_tracker.record_failure(f"ConnectionError: {e}")
                if retry_count >= max_retries:
                    yield {"error": f"网络连接失败，已重试 {max_retries} 次: {e}"}
                    return
                retry_count += 1
                wait = backoff_time
                yield {"retry_info": {"wait": wait, "attempt": retry_count, "max_retries": max_retries, "error_type": "connection_error"}}
                time.sleep(wait)
                backoff_time = min(backoff_time * 2, 30)
            except requests.exceptions.Timeout as e:
                self.consecutive_errors += 1
                failure_tracker.record_failure(f"Timeout: {e}")
                if retry_count >= max_retries:
                    yield {"error": f"请求超时，已重试 {max_retries} 次: {e}"}
                    return
                retry_count += 1
                wait = backoff_time
                yield {"retry_info": {"wait": wait, "attempt": retry_count, "max_retries": max_retries, "error_type": "timeout"}}
                time.sleep(wait)
                backoff_time = min(backoff_time * 2, 30)
            except Exception as e:
                logger.exception(f"API 调用异常: {e}")
                failure_tracker.record_failure(f"Exception: {e}")
                yield {"error": f"API 调用异常: {e}"}
                return
        yield {"error": f"达到最大重试次数 ({max_retries})，请求失败。"}
    
    def _execute_recovery(self, error_info: dict, payload: dict, messages: List[Dict], think_mode: bool) -> Optional[Tuple]:
        action = error_info['action']
        self.last_recovery_action = action
        if action == "remove_conflicting_params":
            payload.pop('temperature', None); payload.pop('presence_penalty', None); payload.pop('frequency_penalty', None)
            return (payload, messages)
        elif action == "fix_thinking_param":
            if think_mode: payload['thinking'] = {"type": "enabled"}
            else: payload.pop('thinking', None)
            return (payload, messages)
        elif action == "truncate_context":
            truncated = self._truncate_messages(messages)
            if truncated: payload['messages'] = truncated; return (payload, truncated)
            return None
        elif action == "repair_messages":
            repaired = self.session._repair_400(messages)
            if repaired != messages:
                payload['messages'] = repaired
                return (payload, repaired)
            # 若基础修复未改变，尝试 AI 修复
            ai_fixed = self.session._ai_repair_messages(messages, error_info['message'])
            if ai_fixed and ai_fixed != messages:
                payload['messages'] = ai_fixed
                return (payload, ai_fixed)
            return None
        elif action == "fix_parameters":
            if self._fix_parameters(payload, error_info): return (payload, messages)
            return None
        elif action in ("backoff_retry", "wait_retry", "retry"):
            return (payload, messages)
        return None
    
    def _truncate_messages(self, messages: List[Dict]) -> Optional[List[Dict]]:
        """Hermes 风格上下文压缩：工具修剪 + LLM 摘要 + token 预算保护"""
        if len(messages) <= 3:
            return None

        # 检查是否需要压缩
        est_tokens = estimate_tokens_rough(messages)
        if est_tokens < self.compressor.threshold_tokens:
            # 低于阈值但消息很多时，做轻量修剪
            if len(messages) > 30:
                pruned, count = self.compressor.prune_tool_results(messages)
                if count > 0:
                    return pruned
            return None

        # 完整压缩
        api_key = getattr(self, '_api_key_override', '') or ''
        base_url = DEEPSEEK_API_URL.rsplit('/', 2)[0]  # https://api.deepseek.com

        compressed = self.compressor.compress(
            messages,
            api_key=api_key,
            base_url=base_url,
        )

        # 验证压缩效果
        if len(compressed) >= len(messages):
            return None  # 压缩无效

        return compressed
    
    def _fix_parameters(self, payload: dict, error_info: dict) -> bool:
        fixed = False
        model = payload.get('model', '')
        valid_models = [MODEL_FAST, MODEL_EXPERT, "deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash", "deepseek-v4-pro"]
        if model not in valid_models: payload['model'] = MODEL_FAST; fixed = True
        max_tokens = payload.get('max_tokens', 4096)
        if not isinstance(max_tokens, int) or max_tokens < 1: payload['max_tokens'] = 4096; fixed = True
        elif max_tokens > 8192: payload['max_tokens'] = 8192; fixed = True
        temp = payload.get('temperature', 0.7)
        if not isinstance(temp, (int, float)) or temp < 0 or temp > 2: payload['temperature'] = 0.7; fixed = True
        tool_choice = payload.get('tool_choice', 'auto')
        if tool_choice not in ('auto', 'none', 'required'): payload['tool_choice'] = 'auto'; fixed = True
        return fixed
    
    def _process_stream_response(self, resp: requests.Response) -> Generator:
        for line in resp.iter_lines():
            if not line: continue
            line_str = line.decode('utf-8')
            if line_str == ': keep-alive': continue
            if line_str.startswith('data: '):
                data = line_str[6:]
                if data == '[DONE]': break
                try: yield json.loads(data)
                except json.JSONDecodeError: logger.debug(f"跳过不完整JSON: {data[:50]}...")


class MHSession:
    SYSTEM_PROMPT = """# MH-DeepSeek Agent V4

当前系统时间：{current_time}

你是 {platform_name} 上的专家代理。**用工具获取事实，拒绝凭空臆测。**

## 核心原则
1. **事实优先**：系统状态、文件、网络结果均用工具获取。
2. **并行调用**：独立工具一次提交多个，减少轮次。
3. **安全**：写操作在 `{work_dir}/agent_storage/` 下。删除/高危命令需说明理由。不可逆操作先备份。
4. **失败切换**：同一工具失败 2 次立即换备选方案，禁止第三次尝试。

## 工具使用
- 严格遵守工具 schema。
- 下载超时改用 `curl -L`，解压失败试 `7z x`，写入拒绝改到 agent_storage。

## 子Agent
复杂任务可派生子Agent并行执行。`list_sub_agents` / `control_sub_agent` / `query_sub_agent` / `send_to_sub_agent` / `broadcast_to_sub_agents`。主Agent绝对控制，子Agent平等可推送事件。

## 输出
简单操作直接返回。复杂任务：【分析】→ 【执行】→ 【结论】（证据+风险）

## 环境
平台：{platform_name} | 无 sudo/apt/systemd | 无 GUI 浏览器

## 工作目录
当前工作目录：`{work_dir}`。所有相对路径均基于此。
    """

    def __init__(self, session_id: str, work_dir: str = None,
                 api_key: str = None,
                 provider_id: str = None,
                 model_id: str = None,
                 identity: str = "agent",
                 meiju_phone: str = None,
                 meiju_password: str = None):
        self.session_id = session_id
        self.api_key = api_key
        # 模型选择
        self.provider_id = provider_id or DEFAULT_PROVIDER
        self.model_id = model_id or DEFAULT_MODEL
        self.identity = identity

        # 设置线程身份，以便工具调用时获取正确身份
        set_current_identity(identity)

        self.executor = SecureExecutor(work_dir)
        self.tool_registry = ToolRegistry(self.executor)
        discover_and_register(self.tool_registry)

        self.memory = MemorySystem(identity=identity)
        self.conversation_history = self.memory.load_conversation(session_id)
        self._convert_legacy_reasoning()

        if not self.conversation_history:
            base_prompt = self.SYSTEM_PROMPT
            # 注入长期记忆摘要
            long_term_mem = LongTermMemory(identity=self.identity)
            long_term_context = long_term_mem.get_context_summary()
            if long_term_context:
                base_prompt = base_prompt + "\n\n" + long_term_context
            full_system = base_prompt.format(work_dir=self.executor.work_dir, current_time="加载中...", platform_ua=self._get_platform_ua(), platform_name=self._get_platform_name())
            self.conversation_history = [{"role": "system", "content": full_system}]
            self.update_system_prompt()
        else:
            self.update_system_prompt()

        self.pending_auth = None
        self.global_auth_granted = False
        self.auto_drive = False
        self.last_assistant_incomplete = False
        self._stop_requested = False
        self.task_tracker = TaskTracker()
        self._override_messages_for_next_call = None
        self._override_base_url_for_next_call = None
        
        self.error_recovery = ErrorRecoveryExecutor(self)

        # 统一上下文管理器
        self.ctx_manager = ContextManager(
            max_input_tokens=MAX_INPUT_TOKENS_PER_CALL,
            protect_recent=PROTECT_LAST_N_MESSAGES,
        )

        # 模型提供商管理器
        self.provider_mgr = create_default_providers(
            deepseek_api_key=api_key or "",
            meiju_phone=meiju_phone or MEIJU_PHONE,
            meiju_password=meiju_password or MEIJU_PASSWORD,
        )

    def stop(self):
        """请求停止当前流输出"""
        self._stop_requested = True

    def _save_memory_safe(self):
        """安全保存 conversation_history —— """
        """异常时静默失败，不中断主流程"""
        if not self.conversation_history:
            return
        try:
            self.memory.save_conversation(self.session_id, self.conversation_history)
        except Exception:
            logger.exception(f"保存会话记忆失败 [{self.session_id}]")

    def _convert_legacy_reasoning(self):
        changed = False
        for msg in self.conversation_history:
            if msg.get("role") == "assistant" and msg.get("content"):
                if "reasoning_content" in msg: continue
                match = re.search(r'\[思考过程\]\s*(.*?)\s*\[/思考过程\]', msg["content"], flags=re.DOTALL)
                if match:
                    reasoning = match.group(1).strip()
                    clean_content = re.sub(r'\[思考过程\].*?\[/思考过程\]\s*', '', msg["content"], flags=re.DOTALL).strip()
                    msg["content"] = clean_content if clean_content else ""
                    msg["reasoning_content"] = reasoning
                    changed = True
        if changed:
            self.memory.save_conversation(self.session_id, self.conversation_history)

    def update_system_prompt(self):
        from datetime import datetime
        base_prompt = self.SYSTEM_PROMPT
        # 注入长期记忆摘要
        long_term_mem = LongTermMemory(identity=self.identity)
        long_term_context = long_term_mem.get_context_summary()
        if long_term_context:
            base_prompt = base_prompt + "\n\n" + long_term_context
        full_prompt = base_prompt
        # 格式化
        full_prompt = full_prompt.format(
            work_dir=self.executor.work_dir,
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            platform_ua=self._get_platform_ua(),
            platform_name=self._get_platform_name()
        )
        for msg in self.conversation_history:
            if msg.get("role") == "system":
                msg["content"] = full_prompt
                break


    @staticmethod
    def _get_platform_ua() -> str:
        """返回平台 UA（APK 或 Termux）"""
        if os.path.exists('/data/data/com.mhdeepseek/files/py/.extracted'):
            return "MH-DeepSeek-Android-APK/v0.5.1"
        return "MH-DeepSeek-Android-Termux/v0.5.1"

    @staticmethod
    def _get_platform_name() -> str:
        """返回平台名称"""
        if os.path.exists('/data/data/com.mhdeepseek/files/py/.extracted'):
            return "Android-APK"
        return "Android-Termux"

    def _prepare_messages_for_api(self, think_mode: bool) -> List[Dict]:
        """准备发送给 API 的消息列表 — ContextManager 统一管道"""
        return self.ctx_manager.prepare(self.conversation_history, think_mode)

    
    def _ensure_tool_call_responses(self, messages: List[Dict]) -> List[Dict]:
        """双向确保 tool_calls 与 tool 响应的配对完整性"""
        # 收集所有 tool_call_id
        tool_response_ids = set()
        for msg in messages:
            if msg.get('role') == 'tool' and msg.get('tool_call_id'):
                tool_response_ids.add(msg['tool_call_id'])
        assistant_tool_call_ids = set()
        for msg in messages:
            if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                for tc in msg['tool_calls']:
                    if tc.get('id'):
                        assistant_tool_call_ids.add(tc['id'])

        result = []
        orphan_tool = 0
        for msg in messages:
            role = msg.get('role', '')
            if role == 'assistant' and msg.get('tool_calls'):
                valid_calls = [tc for tc in msg['tool_calls'] if tc.get('id', '') in tool_response_ids]
                if valid_calls:
                    msg = dict(msg); msg['tool_calls'] = valid_calls
                else:
                    msg = {k: v for k, v in msg.items() if k != 'tool_calls'}
            elif role == 'tool':
                if msg.get('tool_call_id', '') not in assistant_tool_call_ids:
                    orphan_tool += 1
                    continue
            result.append(msg)
        if orphan_tool:
            logger.info(f'移除 {orphan_tool} 条孤儿 tool 消息')
        return result

    def _repair_400(self, messages: List[Dict]) -> List[Dict]:
        """使用 MessageRepairer 全面修复消息格式（处理已知所有 400 错误）"""
        repaired = self.memory.basic_repair(messages)
        repaired = MessageRepairer.repair(repaired)
        repaired = self._ensure_tool_call_responses(repaired)
        return repaired

    def _ai_repair_messages(self, messages: List[Dict], error_text: str) -> Optional[List[Dict]]:
        """利用 AI 自动修复导致 HTTP 400 的消息格式

        error_text 是 DeepSeek API 返回的原始错误文本（如 JSON 格式的错误详情），
        会完整传递给修复 agent，不截断。
        """
        if not self.api_key or not messages:
            return None
        try:
            # 取最近 10 条非 system 消息
            system_msgs = [m for m in messages if m.get("role") == "system"]
            non_system = [m for m in messages if m.get("role") != "system"]
            recent = non_system[-10:] if len(non_system) > 10 else non_system

            # 错误类型分析（帮助 agent 聚焦）
            err_lower = error_text.lower()
            if 'tool' in err_lower and ('response' in err_lower or 'preceding' in err_lower):
                focus = "存在孤儿 tool 消息（tool 响应没有对应的 assistant tool_calls）。请移除没有对应 tool_calls 的 tool 消息。"
            elif 'tool_call' in err_lower and 'id' in err_lower:
                focus = "tool_calls 的 id 字段缺失或格式错误。请确保每个 tool_call 有唯一的 id。"
            elif 'reasoning_content' in err_lower:
                focus = "reasoning_content 在 tool_calls 后未正确回传。请确保有 tool_calls 的 assistant 消息在后续请求中保留 reasoning_content。"
            elif 'content' in err_lower and 'must' in err_lower:
                focus = "消息缺少 content 字段。请确保每条 assistant 和 tool 消息都有 content。"
            else:
                focus = "请检查所有消息的 role、content、tool_calls、tool_call_id 字段是否合法。"

            repair_prompt = (
                "你是 DeepSeek API 消息格式修复器。以下是导致 HTTP 400 的完整错误信息和消息列表。\n\n"
                "=== API 原始错误 ===\n"
                + error_text[:1200] + "\n\n"
                "=== 诊断建议 ===\n"
                + focus + "\n\n"
                "=== 最近的消息 ===\n"
                + json.dumps(recent, ensure_ascii=False, indent=2) + "\n\n"
                "请返回 JSON：\n"
                '{"fixed_messages": [...], "explanation": "修复说明"}\n\n'
                "规则：\n"
                "1. 每条消息有 role 和 content (字符串)\n"
                "2. assistant 有 tool_calls 时每个必须有 id/type='function'/function.name/function.arguments\n"
                "3. tool 消息的 tool_call_id 必须在前面 assistant 的 tool_calls 中出现过\n"
                "4. 不能有连续的 user 消息\n"
                "5. 移除所有孤儿 tool 消息（无对应 tool_calls 的）\n"
                "6. 移除所有孤儿 tool_calls（无对应 tool 响应的）\n\n"
                "只返回 JSON，不要额外文本。"
            )
            repair_payload = {
                "model": MODEL_FAST,
                "messages": [{"role": "user", "content": repair_prompt}],
                "max_tokens": 3000,
                "temperature": 0.0,
                "stream": False
            }
            resp = requests.post(
                DEEPSEEK_API_URL,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=repair_payload,
                timeout=20
            )
            if resp.status_code == 200:
                result = resp.json()
                content = result["choices"][0]["message"]["content"]
                # 提取 JSON — 支持裸 JSON 或 ```json ... ``` 代码块
                json_str = None
                m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
                if m:
                    json_str = m.group(1).strip()
                else:
                    m = re.search(r'\{.*"fixed_messages"\s*:\s*\[.*\]\s*[,}].*\}', content, re.DOTALL)
                    if m:
                        json_str = m.group(0)
                if json_str:
                    try:
                        repair_data = json.loads(json_str)
                        fixed = repair_data.get("fixed_messages", [])
                        if fixed and isinstance(fixed, list) and len(fixed) > 0:
                            new_history = system_msgs + messages[len(system_msgs):-len(recent)] + fixed
                            logger.info(f"AI 修复成功: {repair_data.get('explanation', '无说明')}")
                            return new_history
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning(f"AI 修复 JSON 解析失败: {e}")
                logger.warning("AI 修复未返回有效结构，回退到基础修复")
            else:
                logger.warning(f"AI 修复请求失败: HTTP {resp.status_code}")
        except Exception as e:
            logger.exception(f"AI 修复异常: {e}")
        return None

    def _call_deepseek_stream(self, messages: List[Dict], think_mode: bool = False,
                              reasoning_effort: str = None, base_url: str = None) -> Generator:
        """
        通过当前 provider 调用 API 流式接口。
        妹居DeepSeek 自动禁用 thinking（提供商不支持）。
        """
        # 获取当前 provider
        provider = self.provider_mgr.get(self.provider_id)
        if not provider:
            yield {"error": f"未知提供商: {self.provider_id}"}
            return

        # 妹居DeepSeek 不支持 thinking
        if not provider.supports_thinking:
            think_mode = False

        # 构建 payload
        prepared_messages = self._prepare_messages_for_api(think_mode)
        tools_schema = self.tool_registry.get_schemas()
        model = self.model_id

        if reasoning_effort is None:
            reasoning_effort = DEFAULT_REASONING_EFFORT

        payload = {
            "model": model,
            "messages": prepared_messages,
            "tools": tools_schema if tools_schema else None,
            "tool_choice": "auto" if tools_schema else "none",
            "temperature": 0.7,
            "max_tokens": THINKING_MAX_OUTPUT_TOKENS if think_mode else DEFAULT_MAX_OUTPUT_TOKENS,
            "stream": True
        }

        if think_mode:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = reasoning_effort
            payload.pop('temperature', None)
            payload.pop('presence_penalty', None)
            payload.pop('frequency_penalty', None)
        else:
            payload["thinking"] = {"type": "disabled"}

        payload = {k: v for k, v in payload.items() if v is not None}

        # 通过 provider 调用
        yield from provider.call_stream(payload, think_mode, model)

    def _process_tool_calls(self, tool_calls: List[Dict]):
        tool_results = []
        need_auth = None
        for tc in tool_calls:
            if self._stop_requested:
                tool_results.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": "[已中断] 用户停止了工具执行"
                })
                continue

            func_name = tc["function"]["name"]
            try: func_args = json.loads(tc["function"]["arguments"])
            except Exception: func_args = {}

            # 在独立线程中执行工具调用，以便轮询 _stop_requested
            result_holder = {"result": None, "done": False}

            def execute_tool():
                try:
                    result_holder["result"] = self.tool_registry.call(
                        func_name, func_args, self.session_id
                    )
                except Exception as e:
                    result_holder["result"] = f"[工具执行异常] {e}"
                result_holder["done"] = True

            thread = threading.Thread(target=execute_tool, daemon=True)
            thread.start()

            # 轮询等待，每次检查停止标记
            while not result_holder["done"]:
                if self._stop_requested:
                    self.executor.stop_current()  # 终止底层子进程
                    thread.join(timeout=5)
                    break
                thread.join(timeout=0.5)

            result = result_holder.get("result")
            if result is None:
                tool_results.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": "[已中断] 用户停止了工具执行"
                })
                continue

            if isinstance(result, dict) and result.get("need_auth"):
                if self.auto_drive or self.global_auth_granted:
                    # 全局已授权，直接执行
                    actual_cmd = result.get("command", "")
                    use_root = "root" in func_name or (func_name == "device_control" and func_args.get("action") in ["root_shell", "install"])
                    use_shizuku = "shizuku" in func_name

                    # 授权命令也在线程中执行，可被 stop 中断
                    auth_holder = {"result": None, "done": False}
                    def execute_auth():
                        try:
                            auth_holder["result"] = self.executor.execute(
                                actual_cmd, require_auth=False,
                                use_root=use_root, use_shizuku=use_shizuku
                            )
                        except Exception as e:
                            auth_holder["result"] = {"returncode": -1, "stdout": "", "stderr": str(e)}
                        auth_holder["done"] = True

                    auth_thread = threading.Thread(target=execute_auth, daemon=True)
                    auth_thread.start()
                    while not auth_holder["done"]:
                        if self._stop_requested:
                            self.executor.stop_current()
                            auth_thread.join(timeout=5)
                            break
                        auth_thread.join(timeout=0.5)

                    exec_result = auth_holder.get("result")
                    if exec_result is None:
                        tool_results.append({
                            "role": "tool", "tool_call_id": tc["id"],
                            "content": "[已中断] 用户停止了工具执行"
                        })
                        continue

                    result_str = f"返回码 {exec_result['returncode']}\n{exec_result['stdout']}\n{exec_result['stderr']}"
                    tool_results.append({"role": "tool", "tool_call_id": tc["id"], "content": result_str[:MAX_TOOL_OUTPUT_CHARS] + "\n[已完成，已全局授权]"})
                else:
                    # 需要用户授权：记录第一个授权请求，并插入占位结果
                    if need_auth is None:
                        actual_cmd = result.get("command", func_name)
                        use_root = "root" in func_name or (func_name == "device_control" and func_args.get("action") in ["root_shell", "install"])
                        use_shizuku = "shizuku" in func_name
                        need_auth = (tc["id"], actual_cmd, use_root, use_shizuku)
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": "[需要用户授权]"
                    })
            else:
                result_str = str(result)[:MAX_TOOL_OUTPUT_CHARS]
                tool_results.append({"role": "tool", "tool_call_id": tc["id"], "content": result_str})
        return tool_results, need_auth

    def _stream_inner_loop(self, think_mode: bool = False) -> Generator:
        # 确保线程身份正确
        set_current_identity(self.identity)
        
        for iteration in range(MAX_ITERATIONS):
            if self._stop_requested:
                yield "data: " + json.dumps({"type": "stopped", "message": "用户中断了输出"}) + "\n\n"
                yield "data: [DONE]\n\n"
                return
            full_content = ""
            accumulated_reasoning = ""
            tool_calls_accumulator = {}
            finish_reason = None

            if self._override_messages_for_next_call is not None:
                messages_for_api = self._override_messages_for_next_call
                base_url = self._override_base_url_for_next_call
            else:
                messages_for_api = self._prepare_messages_for_api(think_mode)
                base_url = None

            for chunk in self._call_deepseek_stream(messages_for_api, think_mode, base_url=base_url):
                if "error" in chunk:
                    self._save_memory_safe()
                    yield "data: " + json.dumps({"type": "error", "error": chunk["error"]}) + "\n\n"
                    self.last_assistant_incomplete = True
                    return
                if "recovery" in chunk:
                    yield "data: " + json.dumps({"type": "recovery", "recovery": chunk["recovery"]}) + "\n\n"
                    continue
                if "retry_info" in chunk:
                    yield "data: " + json.dumps({"type": "retry_info", "retry_info": chunk["retry_info"]}) + "\n\n"
                    continue

                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason", finish_reason)

                if think_mode and "reasoning_content" in delta and delta["reasoning_content"]:
                    accumulated_reasoning += delta["reasoning_content"]
                    yield "data: " + json.dumps({"type": "reasoning", "chunk": delta["reasoning_content"]}) + "\n\n"
                if "content" in delta and delta["content"]:
                    full_content += delta["content"]
                    yield "data: " + json.dumps({"type": "content", "chunk": delta["content"]}) + "\n\n"
                if "tool_calls" in delta:
                    for tc_delta in delta["tool_calls"]:
                        idx = tc_delta.get("index", 0)
                        # 第一次出现 tool_call → 通知前端"准备执行"
                        if idx not in tool_calls_accumulator and "function" in tc_delta:
                            name = tc_delta["function"].get("name", "?")
                            yield "data: " + json.dumps({"type": "tool_preparing", "name": name}) + "\n\n"
                        if idx not in tool_calls_accumulator:
                            tool_calls_accumulator[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                        current = tool_calls_accumulator[idx]
                        if "id" in tc_delta: current["id"] = tc_delta["id"]
                        if "function" in tc_delta:
                            if "name" in tc_delta["function"]: current["function"]["name"] = tc_delta["function"]["name"]
                            if "arguments" in tc_delta["function"]: current["function"]["arguments"] += tc_delta["function"]["arguments"]

            assistant_msg = {"role": "assistant", "content": full_content or ""}
            if think_mode: assistant_msg["reasoning_content"] = accumulated_reasoning

            if finish_reason == "tool_calls" and tool_calls_accumulator:
                tool_calls = [tool_calls_accumulator[i] for i in sorted(tool_calls_accumulator.keys())]
                assistant_msg["tool_calls"] = tool_calls
                yield "data: " + json.dumps({"type": "tool_call", "calls": [{"id": tc["id"], "name": tc["function"]["name"], "arguments": tc["function"]["arguments"]} for tc in tool_calls]}) + "\n\n"

                self.conversation_history.append(assistant_msg)

                tool_results, need_auth = self._process_tool_calls(tool_calls)

                # 先写入所有工具结果（包括授权占位），确保每个 tool_call_id 都有回应
                for tr in tool_results:
                    self.conversation_history.append(tr)
                    yield "data: " + json.dumps({"type": "tool_result", "tool_call_id": tr["tool_call_id"], "result": tr["content"][:500]}) + "\n\n"

                self.memory.save_conversation(self.session_id, self.conversation_history)

                if need_auth:
                    self.pending_auth = need_auth
                    tool_call_id, actual_cmd, _, _ = need_auth
                    yield "data: " + json.dumps({"type": "auth_required", "tool_call_id": tool_call_id, "command": actual_cmd}) + "\n\n"
                    return

                self._override_messages_for_next_call = None
                self._override_base_url_for_next_call = None
                continue

            self.conversation_history.append(assistant_msg)
            self.memory.save_conversation(self.session_id, self.conversation_history)
            self.last_assistant_incomplete = False
            return

        self._save_memory_safe()
        yield "data: " + json.dumps({"type": "error", "error": "已达到最大迭代次数，请简化任务或手动干预。"}) + "\n\n"

    def chat_stream(self, user_input: str, think_mode: bool = False, continue_mode: bool = False, auto_drive: bool = False):
        # 设置线程身份，确保工具调用时获取正确身份
        set_current_identity(self.identity)
        self.auto_drive = auto_drive
        
        auth_keywords = ["全部允许", "所有请求全部允许", "自动授权", "同意所有", "后续所有请求"]
        if any(kw in user_input for kw in auth_keywords):
            self.global_auth_granted = True
            self.conversation_history.append({"role": "system", "content": "[系统提示] 用户已授权本次会话中的所有后续操作，无需再单独请求授权。"})

        if user_input == "AUTH_APPROVE" and self.pending_auth is not None:
            tool_call_id, actual_cmd, use_root, use_shizuku = self.pending_auth
            self.pending_auth = None
            yield f"data: {json.dumps({'type': 'auth_approved', 'command': actual_cmd})}\n\n"

            # 授权命令在线程中执行，可被 stop 中断
            auth_holder = {"result": None, "done": False}
            def execute_auth():
                try:
                    auth_holder["result"] = self.executor.execute(
                        actual_cmd, require_auth=False,
                        use_root=use_root, use_shizuku=use_shizuku
                    )
                except Exception as e:
                    auth_holder["result"] = {"returncode": -1, "stdout": "", "stderr": str(e)}
                auth_holder["done"] = True

            auth_thread = threading.Thread(target=execute_auth, daemon=True)
            auth_thread.start()
            while not auth_holder["done"]:
                if self._stop_requested:
                    self.executor.stop_current()
                    auth_thread.join(timeout=5)
                    break
                auth_thread.join(timeout=0.5)

            result = auth_holder.get("result")
            if result is None:
                yield f"data: {json.dumps({'type': 'stopped', 'message': '用户中断了工具执行'})}\n\n"
                yield "data: [DONE]\n\n"
                return
            tool_result = f"返回码 {result['returncode']}\n{result['stdout']}\n{result['stderr']}"

            # 找到历史中最后一个包含该 tool_call_id 的 assistant 消息的位置
            insert_pos = -1
            for i, msg in enumerate(self.conversation_history):
                if msg.get("role") == "assistant" and "tool_calls" in msg:
                    for tc in msg["tool_calls"]:
                        if tc.get("id") == tool_call_id:
                            insert_pos = i + 1   # 应在该 assistant 后面插入 tool 结果
                            break
            if insert_pos >= 0:
                # 先移除可能存在的旧占位 tool 消息（防止重复）
                self.conversation_history = [
                    m for m in self.conversation_history
                    if not (m.get("role") == "tool" and m.get("tool_call_id") == tool_call_id)
                ]
                # 在正确位置插入真实结果
                self.conversation_history.insert(insert_pos, {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": tool_result[:MAX_TOOL_OUTPUT_CHARS] + "\n[已完成，手动授权]"
                })
                self.memory.save_conversation(self.session_id, self.conversation_history)
                yield f"data: {json.dumps({'type': 'tool_result', 'tool_call_id': tool_call_id, 'result': tool_result[:500]})}\n\n"
                yield "data: [DONE]\n\n"
                return
            else:
                # 历史中找不到对应的 assistant，无法安全插入，给出错误提示
                logger.error(f"授权失败：找不到包含 tool_call_id={tool_call_id} 的 assistant 消息")
                yield f"data: {json.dumps({'type': 'error', 'error': '授权失败：历史记录中缺少相应的工具调用请求'})}\n\n"
                yield "data: [DONE]\n\n"
                return

        if continue_mode:
            if user_input:
                user_input = f"[请继续完成你未说完的内容] {user_input}"
            else:
                user_input = "[请继续完成你未说完的内容]"
            # 不需额外处理，后续会追加到 history

        if user_input and user_input != "AUTH_APPROVE" and not continue_mode:
            self.conversation_history.append({"role": "user", "content": user_input})
            self.memory.save_conversation(self.session_id, self.conversation_history)
            self.update_system_prompt()
            if len([m for m in self.conversation_history if m["role"] == "user"]) == 1:
                self._generate_title_async(user_input)

        self._stop_requested = False  # 每次新消息重置
        for iteration in range(MAX_ITERATIONS):
            if self._stop_requested:
                self._save_memory_safe()
                yield f"data: {json.dumps({'type': 'stopped', 'message': '用户中断了输出'})}\n\n"
                yield "data: [DONE]\n\n"
                return
            full_content = ""
            accumulated_reasoning = ""
            tool_calls_accumulator = {}
            finish_reason = None

            messages_for_api = self._prepare_messages_for_api(think_mode)

            for chunk in self._call_deepseek_stream(messages_for_api, think_mode):
                if "error" in chunk:
                    self._save_memory_safe()
                    yield f"data: {json.dumps({'type': 'error', 'error': chunk['error']})}\n\n"
                    self.last_assistant_incomplete = True
                    return
                if "recovery" in chunk:
                    yield f"data: {json.dumps({'type': 'recovery', 'recovery': chunk['recovery']})}\n\n"
                    continue
                if "retry_info" in chunk:
                    yield f"data: {json.dumps({'type': 'retry_info', 'retry_info': chunk['retry_info']})}\n\n"
                    continue

                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason", finish_reason)

                if think_mode and "reasoning_content" in delta and delta["reasoning_content"]:
                    accumulated_reasoning += delta["reasoning_content"]
                    yield f"data: {json.dumps({'type': 'reasoning', 'chunk': delta['reasoning_content']})}\n\n"
                if "content" in delta and delta["content"]:
                    full_content += delta["content"]
                    yield f"data: {json.dumps({'type': 'content', 'chunk': delta['content']})}\n\n"
                if "tool_calls" in delta:
                    for tc_delta in delta["tool_calls"]:
                        idx = tc_delta.get("index", 0)
                        # 第一次出现 tool_call → 通知前端"准备执行"
                        if idx not in tool_calls_accumulator and "function" in tc_delta:
                            name = tc_delta["function"].get("name", "?")
                            yield f"data: {json.dumps({'type': 'tool_preparing', 'name': name})}\n\n"
                        if idx not in tool_calls_accumulator:
                            tool_calls_accumulator[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                        current = tool_calls_accumulator[idx]
                        if "id" in tc_delta: current["id"] = tc_delta["id"]
                        if "function" in tc_delta:
                            if "name" in tc_delta["function"]: current["function"]["name"] = tc_delta["function"]["name"]
                            if "arguments" in tc_delta["function"]: current["function"]["arguments"] += tc_delta["function"]["arguments"]

            assistant_msg = {"role": "assistant", "content": full_content or ""}
            if think_mode: assistant_msg["reasoning_content"] = accumulated_reasoning

            if finish_reason == "tool_calls" and tool_calls_accumulator:
                tool_calls = [tool_calls_accumulator[i] for i in sorted(tool_calls_accumulator.keys())]
                assistant_msg["tool_calls"] = tool_calls
                yield f"data: {json.dumps({'type': 'tool_call', 'calls': [{'id': tc['id'], 'name': tc['function']['name'], 'arguments': tc['function']['arguments']} for tc in tool_calls]})}\n\n"

                self.conversation_history.append(assistant_msg)

                tool_results, need_auth = self._process_tool_calls(tool_calls)

                for tr in tool_results:
                    self.conversation_history.append(tr)
                    yield f"data: {json.dumps({'type': 'tool_result', 'tool_call_id': tr['tool_call_id'], 'result': tr['content'][:500]})}\n\n"

                self.memory.save_conversation(self.session_id, self.conversation_history)

                if need_auth:
                    self.pending_auth = need_auth
                    tool_call_id, actual_cmd, _, _ = need_auth
                    yield f"data: {json.dumps({'type': 'auth_required', 'tool_call_id': tool_call_id, 'command': actual_cmd})}\n\n"
                    return

                continue

            self.conversation_history.append(assistant_msg)
            self.memory.save_conversation(self.session_id, self.conversation_history)
            self.last_assistant_incomplete = False
            yield "data: [DONE]\n\n"
            return

        self._save_memory_safe()
        yield f"data: {json.dumps({'type': 'error', 'error': '已达到最大迭代次数，请简化任务或手动干预。'})}\n\n"
        yield "data: [DONE]\n\n"

    def _save_partial(self, full_content, accumulated_reasoning, think_mode):
        """增量保存部分助手回复（静默失败）"""
        try:
            content = "[STREAMING] " + (full_content or "")
            partial = {"role": "assistant", "content": content}
            if think_mode and accumulated_reasoning:
                partial["reasoning_content"] = accumulated_reasoning
            for i in range(len(self.conversation_history) - 1, -1, -1):
                if str(self.conversation_history[i].get("content", "")).startswith("[STREAMING] "):
                    self.conversation_history[i] = partial
                    break
            else:
                self.conversation_history.append(partial)
            self.memory.save_conversation(self.session_id, self.conversation_history)
        except Exception:
            pass

    def _cleanup_streaming(self):
        """清理 [STREAMING] 标记消息"""
        self.conversation_history = [
            m for m in self.conversation_history
            if not (m.get("role") == "assistant" and str(m.get("content", "")).startswith("[STREAMING] "))
        ]

    def _emergency_summarize_and_restart(self) -> bool:
        """紧急摘要重启 — 零信息丢失总结整个上下文

        原则：不截断任何消息，完整保留所有关键信息。
        用一个不携带历史的纯 API 调用生成**全面**摘要，
        替换 conversation_history 为 [system, 摘要, 最后用户消息]。

        Returns:
            bool: True 表示重启成功
        """
        if not self.api_key or len(self.conversation_history) < 3:
            return False

        try:
            # 找到最后一条用户消息
            last_user_msg = None
            for msg in reversed(self.conversation_history):
                if msg.get("role") == "user" and not str(msg.get("content", "")).startswith("[CONTEXT"):
                    last_user_msg = msg
                    break
            if not last_user_msg:
                return False

            # 完整序列化对话历史（不截断，保留所有信息）
            parts = []
            for msg in self.conversation_history:
                role = msg.get("role", "?")
                content = str(msg.get("content", ""))
                # 不截断内容：完整保留工具输出、助手回复、用户消息
                if role == "tool":
                    tool_name = msg.get("tool_name", "?")
                    parts.append(f"[TOOL {tool_name}]:\n{content}")
                elif role == "assistant":
                    reasoning = str(msg.get("reasoning_content", ""))
                    if reasoning:
                        parts.append(f"[ASSISTANT 思考]: {reasoning}")
                    parts.append(f"[ASSISTANT]:\n{content}")
                elif role == "user":
                    parts.append(f"[USER]:\n{content}")
                elif role == "system":
                    parts.append(f"[SYSTEM]:\n{content[:2000]}")
            serialized = "\n\n---\n\n".join(parts)

            # 如果序列化内容过长（> 80000 字符），压缩重复/冗余部分
            if len(serialized) > 80000:
                # 保留首尾，中段取首尾摘要
                head = serialized[:20000]
                tail = serialized[-40000:]
                mid = serialized[20000:-40000]
                if len(mid) > 10000:
                    # 对中段做段落级采样（保留每段首尾）
                    mid_lines = mid.split("\n")
                    sampled = mid_lines[:200] + ["\n... [中间省略] ...\n"] + mid_lines[-200:]
                    mid = "\n".join(sampled)
                serialized = head + "\n...\n" + mid + "\n...\n" + tail

            # 构建摘要请求
            summary_prompt = (
                "你是一个对话历史归档器。请将以下完整对话整理为详尽的结构化记录。\n\n"
                "⚠️ 重要：不要省略任何关键信息。必须包含：\n"
                "- 所有文件路径（读/写/创建/修改的）\n"
                "- 所有执行的命令和它们的返回码/输出\n"
                "- 所有搜索结果和发现\n"
                "- 所有错误信息和异常\n"
                "- 用户的目标和每一步进展\n"
                "- 当前工作状态和未完成的任务\n"
                "- Agent 做出的关键决策和原因\n\n"
                "格式：\n"
                "## 用户目标\n"
                "## 已完成操作（按时间顺序，每条包含工具名和关键结果）\n"
                "## 文件清单（所有涉及的文件路径）\n"
                "## 命令记录（命令 + 返回码）\n"
                "## 发现与错误\n"
                "## 当前状态\n\n"
                "对话历史:\n" + serialized + "\n\n"
                "请输出详尽的结构化记录，不要省略任何细节。"
            )
            resp = requests.post(
                DEEPSEEK_API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL_EXPERT,  # 用专家模型确保摘要质量
                    "messages": [{"role": "user", "content": summary_prompt}],
                    "max_tokens": 8000,      # 足够大的输出空间
                    "temperature": 0.2,
                },
                timeout=60,
            )
            if resp.status_code != 200:
                logger.warning(f"紧急摘要请求失败: HTTP {resp.status_code}")
                return False

            summary = resp.json()["choices"][0]["message"]["content"]

            # 重建对话历史
            system_msg = self.conversation_history[0] if self.conversation_history[0].get("role") == "system" else {"role": "system", "content": ""}
            summary_msg = {
                "role": "system",
                "content": "[上下文摘要 — 先前对话完整记录]\n\n" + summary
            }
            new_history = [
                system_msg,
                summary_msg,
                last_user_msg,
            ]
            self.conversation_history = new_history
            self.memory.save_conversation(self.session_id, self.conversation_history)
            logger.info("紧急摘要重启成功，对话历史已重建（完整摘要）")
            return True

        except Exception as e:
            logger.exception(f"紧急摘要重启失败: {e}")
            return False

    def _generate_title_async(self, first_message: str):
        """生成会话标题 — 走原始 HTTP 非流式调用（避免 provider.call_stream 只处理 SSE 的问题）"""
        try:
            provider = self.provider_mgr.get(self.provider_id)
            if not provider:
                return

            prompt = f"根据以下对话的第一条消息，生成一个极简短的标题（不超过10个字，不要引号）：\n消息：{first_message}\n标题："

            headers = provider.get_headers()
            payload = {
                "model": self.model_id,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 20,
                "temperature": 0.3,
                "thinking": {"type": "disabled"},
            }
            payload = provider.adapt_payload(payload, think_mode=False, model_id=self.model_id)

            url = f"{provider.api_base}{provider.api_path}"
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                title = content.strip().strip('"\'"\'"')
                if title:
                    self.memory.update_meta(self.session_id, title=title)
            else:
                logger.warning(f"生成标题 API 错误 (HTTP {resp.status_code}): {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"生成标题失败: {e}")

    def regenerate_stream(self) -> Generator:
        # 设置线程身份
        set_current_identity(self.identity)
        
        if len(self.conversation_history) < 2:
            yield "data: " + json.dumps({"type": "error", "error": "没有可重新生成的消息"}) + "\n\n"; return
        last_assistant_idx = -1
        for i in range(len(self.conversation_history)-1, -1, -1):
            if self.conversation_history[i].get("role") == "assistant": last_assistant_idx = i; break
        if last_assistant_idx != -1:
            # 获取要删除的助手消息，记住它是否使用了思考模式
            removed_msg = self.conversation_history[last_assistant_idx]
            had_thinking = bool(removed_msg.get("reasoning_content"))
            self.conversation_history = self.conversation_history[:last_assistant_idx]
            self.memory.save_conversation(self.session_id, self.conversation_history)
            # 沿用原来的思考模式
            use_think = had_thinking
        else:
            use_think = False  # 默认关闭思考

        for chunk in self._stream_inner_loop(think_mode=use_think):
            yield chunk
        yield "data: [DONE]\n\n"

    def continue_stream(self) -> Generator:
        # 设置线程身份
        set_current_identity(self.identity)
        
        if not self.conversation_history:
            yield "data: " + json.dumps({"type": "error", "error": "没有消息可续写"}) + "\n\n"; return
        # 改用 chat_stream 的 continue_mode，更稳定可靠
        for chunk in self.chat_stream(user_input="", think_mode=False, continue_mode=True):
            yield chunk
        yield "data: [DONE]\n\n"