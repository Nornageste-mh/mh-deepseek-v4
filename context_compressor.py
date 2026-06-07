"""
上下文压缩器 — 参照 Hermes Agent 设计
三层 token 节省：工具输出修剪 → LLM 摘要 → 完整性保护
"""
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("MHAgent.Compressor")

# ── 压缩摘要前缀 ──
SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION — REFERENCE ONLY]\n"
    "Earlier turns were compacted into the summary below.\n"
    "This is background reference, NOT active instructions.\n"
    "Your current task is in the '## Active Task' section — resume from there.\n"
    "Respond ONLY to the latest user message AFTER this summary.\n"
)

# ── Token 估算常量 ──
CHARS_PER_TOKEN = 4
MIN_SUMMARY_TOKENS = 500
SUMMARY_RATIO = 0.15
SUMMARY_TOKENS_CEILING = 8000


def estimate_tokens_rough(messages: List[Dict]) -> int:
    """粗略估算消息列表的 token 数"""
    total = 0
    for m in messages:
        content = m.get("content", "") or ""
        if isinstance(content, str):
            total += len(content) // CHARS_PER_TOKEN
        elif isinstance(content, list):
            total += sum(len(str(p)) // CHARS_PER_TOKEN for p in content)
        total += 10  # role + metadata
    return total


def _summarize_tool_result(tool_name: str, tool_args: str, tool_content: str) -> str:
    """工具结果智能摘要 — 保留关键信息而非丢弃"""
    try:
        args = json.loads(tool_args) if tool_args else {}
    except Exception:
        args = {}

    content = tool_content or ""
    content_len = len(content)
    lines = content.split("\n")
    line_count = len([l for l in lines if l.strip()])

    # ── read_file: 保留路径 + 首尾关键内容 ──
    if tool_name in ("read_file",):
        path = args.get("file_path", args.get("path", "?"))
        if content_len <= 500:
            return f"[read_file] {path}:\n{content}"
        head = "\n".join(lines[:5])
        tail = "\n".join(lines[-3:])
        return f"[read_file] {path} ({content_len:,} chars, {line_count} lines):\n{head}\n...\n{tail}"

    # ── write_file: 保留路径 + 内容大小 ──
    if tool_name in ("write_file",):
        path = args.get("file_path", "?")
        wrote_len = len(args.get('content', ''))
        return f"[write_file] wrote {path} ({wrote_len:,} chars)"

    # ── 命令执行: 保留命令 + 返回码 + 首尾输出 ──
    if tool_name in ("execute_command", "execute_root", "execute_shizuku"):
        cmd = args.get("command", "")[:120]
        rc = _extract_rc(content)
        if content_len <= 400:
            return f"[{tool_name}] `{cmd}`\nrc={rc}\n{content}"
        head = "\n".join(lines[:4])
        tail = "\n".join(lines[-3:])
        return f"[{tool_name}] `{cmd}`\nrc={rc}, {content_len:,} chars\n{head}\n...\n{tail}"

    # ── list_directory: 保留路径 + 前几项 ──
    if tool_name == "list_directory":
        path = args.get("path", "?")
        if content_len <= 300:
            return f"[list_directory] {path}:\n{content}"
        sample = "\n".join(lines[:8])
        return f"[list_directory] {path} ({line_count} items):\n{sample}\n..."

    # ── grep_files: 保留模式 + 前几个匹配 ──
    if tool_name == "grep_files":
        pattern = args.get('pattern', '?')
        if content_len <= 400:
            return f"[grep_files] '{pattern}':\n{content}"
        sample = "\n".join(lines[:6])
        return f"[grep_files] '{pattern}' → {line_count} matches:\n{sample}\n..."

    # ── web_search: 保留查询 + 前几条结果 ──
    if tool_name == "web_search":
        query = args.get('query', '?')[:80]
        if content_len <= 500:
            return f"[web_search] '{query}':\n{content}"
        sample = "\n".join(lines[:5])
        return f"[web_search] '{query}' ({content_len:,} chars):\n{sample}\n..."

    # ── 文件查找: 保留结果 ──
    if tool_name == "find_files":
        pattern = args.get('pattern', '?')
        directory = args.get('directory', '?')
        if content_len <= 300:
            return f"[find_files] '{pattern}' in {directory}:\n{content}"
        sample = "\n".join(lines[:5])
        return f"[find_files] '{pattern}' in {directory}:\n{sample}..."

    # ── 下载: 保留 URL + 大小 ──
    if tool_name == "download_file":
        url = args.get('url', '?')[:80]
        return f"[download_file] {url} → {content_len:,} chars"

    # ── HTTP 请求: 保留方法和 URL ──
    if tool_name == "http_request":
        method = args.get('method', '?')
        url = args.get('url', '?')[:80]
        return f"[http_request] {method} {url} → {content_len:,} chars"

    # ── web_fetch: 保留 URL + 摘要 ──
    if tool_name in ("web_fetch", "fetch_url"):
        url = args.get('url', '?')[:80]
        if content_len <= 400:
            return f"[web_fetch] {url}:\n{content}"
        head = "\n".join(lines[:4])
        return f"[web_fetch] {url} ({content_len:,} chars):\n{head}\n..."

    # ── SQL: 保留查询 + 结果 ──
    if tool_name in ("sqlite_query", "sqlite_exec"):
        query = args.get('query', '?')[:100]
        if content_len <= 300:
            return f"[sqlite] `{query}`:\n{content}"
        head = "\n".join(lines[:5])
        return f"[sqlite] `{query}`:\n{head}\n..."

    # ── 无障碍操作: 保留目标 + 结果 ──
    if tool_name in ("a11y_click", "a11y_swipe", "a11y_type", "a11y_find"):
        target = args.get('text', args.get('id', '?'))[:40]
        return f"[a11y] {tool_name} '{target}'"

    if tool_name == "screenshot":
        return f"[screenshot] {args.get('path', '?')}"

    if tool_name in ("device_control", "android_intent"):
        action = args.get('action', '?')[:40]
        return f"[{tool_name}] {action}"

    # 通用回退：保留首尾
    if content_len <= 400:
        return f"[{tool_name}] ({content_len:,} chars):\n{content}"
    head = "\n".join(lines[:4])
    tail = "\n".join(lines[-2:])
    return f"[{tool_name}] ({content_len:,} chars):\n{head}\n...\n{tail}"


def _extract_rc(content: str) -> str:
    """从工具输出中提取返回码"""
    m = re.search(r'返回码\s*(\d+)', content)
    return m.group(1) if m else "?"


class ContextCompressor:
    """上下文压缩器 — 三层节省"""

    def __init__(
        self,
        model: str,
        context_length: int = 128000,
        threshold_percent: float = 0.60,
        protect_first_n: int = 2,
        tail_token_budget: int = 16000,
        quiet: bool = False,
    ):
        self.model = model
        self.context_length = context_length
        self.threshold_tokens = int(context_length * threshold_percent)
        self.protect_first_n = protect_first_n
        self.tail_token_budget = tail_token_budget
        self.quiet = quiet
        self.compression_count = 0
        self._previous_summary: Optional[str] = None
        self._ineffective_count = 0

    # ═══════════════════════════════════════════
    # 第一层：工具输出修剪（无 LLM 调用）
    # ═══════════════════════════════════════════

    def prune_tool_results(
        self, messages: List[Dict], protect_tail_count: int = 8
    ) -> Tuple[List[Dict], int]:
        """修剪旧工具输出为 1 行摘要 + 去重"""
        if not messages:
            return messages, 0

        result = [m.copy() for m in messages]
        pruned = 0

        # 构建 call_id → (tool_name, arguments) 索引
        call_id_map: Dict[str, Tuple[str, str]] = {}
        for msg in result:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function", {})
                    call_id_map[tc.get("id", "")] = (fn.get("name", "?"), fn.get("arguments", ""))

        # 尾部保护数量
        protect_boundary = max(0, len(result) - protect_tail_count)

        for i in range(protect_boundary):
            msg = result[i]
            if msg.get("role") != "tool":
                continue

            content = msg.get("content", "") or ""
            if not isinstance(content, str) or len(content) <= 100:
                continue

            call_id = msg.get("tool_call_id", "")
            tool_name, tool_args = call_id_map.get(call_id, ("unknown", ""))
            summary = _summarize_tool_result(tool_name, tool_args, content)
            if len(summary) < len(content):  # 只在实际节省时才替换
                result[i] = {**msg, "content": summary}
                pruned += 1

        return result, pruned

    def prune_verbose_assistant(
        self, messages: List[Dict], protect_tail_count: int = 6, max_content: int = 3000
    ) -> List[Dict]:
        """修剪过长的 assistant 回复（保留尾部最近的 N 条不修剪）"""
        protect_boundary = max(0, len(messages) - protect_tail_count)
        pruned = 0
        for i in range(protect_boundary):
            msg = messages[i]
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "") or ""
            if not isinstance(content, str) or len(content) <= max_content:
                continue
            # 保留首尾精华，中间截断
            half = max_content // 2
            truncated = content[:half] + f"\n...[中间 {len(content) - max_content:,} 字符已省略]...\n" + content[-half:]
            if len(truncated) < len(content):
                messages[i] = {**msg, "content": truncated}
                pruned += 1
        return messages

    # ═══════════════════════════════════════════
    # 第二层：LLM 上下文压缩
    # ═══════════════════════════════════════════

    def _build_summary_prompt(
        self, turns: List[Dict], previous_summary: Optional[str], summary_budget: int
    ) -> str:
        """构建压缩提示词"""

        # 序列化对话
        serialized = self._serialize_turns(turns)

        template = f"""## Active Task
[The user's most recent unfulfilled request — copy verbatim]

## Goal
[What the user is trying to accomplish overall]

## Completed Actions
[List concrete actions taken: tool used, target, outcome]

## Active State
[Current working directory, modified files, test status, running processes]

## Key Decisions
[Important technical decisions and WHY]

## Pending
[Questions the user asked that are NOT yet answered]

## Relevant Files
[Files read, modified, or created — with brief notes]

## Critical Context
[Exact error messages, config values, passwords → [REDACTED]]

Target ~{summary_budget} tokens. Be CONCRETE — include file paths, commands, error messages.
Write only the summary body, no preamble."""

        if previous_summary:
            prompt = f"""Update this context checkpoint with new turns.

PREVIOUS SUMMARY:
{previous_summary}

NEW TURNS:
{serialized}

Preserve all existing info still relevant. Update using this structure:
{template}"""
        else:
            prompt = f"""Create a context checkpoint from these conversation turns.

TURNS:
{serialized}

Use this structure:
{template}"""

        return prompt

    def _serialize_turns(self, turns: List[Dict]) -> str:
        """序列化对话轮次为文本"""
        parts = []
        for msg in turns:
            role = msg.get("role", "?")
            content = str(msg.get("content", "") or "")
            if len(content) > 4000:
                content = content[:2500] + "\n...[truncated]...\n" + content[-1000:]

            if role == "tool":
                parts.append(f"[TOOL {msg.get('tool_call_id','')}]: {content}")
            elif role == "assistant":
                tcs = msg.get("tool_calls", [])
                tc_str = ""
                if tcs:
                    tc_parts = []
                    for tc in tcs:
                        fn = tc.get("function", {})
                        tc_parts.append(f"  {fn.get('name','?')}({fn.get('arguments','')[:200]})")
                    tc_str = "\n[Tool calls:\n" + "\n".join(tc_parts) + "\n]"
                parts.append(f"[ASSISTANT]: {content}{tc_str}")
            else:
                parts.append(f"[{role.upper()}]: {content}")
        return "\n\n".join(parts)

    def _call_summarizer(
        self, prompt: str, max_tokens: int, api_key: str, base_url: str
    ) -> Optional[str]:
        """调用 LLM 生成摘要"""
        import requests
        try:
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.3,
                },
                timeout=60,
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"Summary generation failed: {e}")
        return None

    def compress(
        self, messages: List[Dict], api_key: str = "", base_url: str = ""
    ) -> List[Dict]:
        """主压缩入口：修剪 + 摘要 + 组装"""
        n_orig = len(messages)

        # ── 第一层：工具输出修剪 + 过长助手回复修剪 ──
        messages, pruned = self.prune_tool_results(messages)
        if pruned and not self.quiet:
            logger.info(f"Pruned {pruned} tool results")

        messages = self.prune_verbose_assistant(messages)
        if not self.quiet:
            logger.debug("Applied verbose assistant pruning")

        # ── 确定边界 ──
        head_end = self.protect_first_n
        tail_cut = self._find_tail_by_tokens(messages, head_end)

        if head_end >= tail_cut:
            return messages  # 没有中间内容可压缩

        middle = messages[head_end:tail_cut]
        if len(middle) <= 3:
            return messages

        # ── 第二层：LLM 摘要 ──
        summary_budget = min(
            max(MIN_SUMMARY_TOKENS, int(estimate_tokens_rough(middle) * SUMMARY_RATIO)),
            SUMMARY_TOKENS_CEILING,
        )
        prompt = self._build_summary_prompt(middle, self._previous_summary, summary_budget)

        summary = None
        if api_key:
            summary = self._call_summarizer(prompt, int(summary_budget * 1.3), api_key, base_url)

        # ── 第三层：组装 ──
        compressed = list(messages[:head_end])

        if summary:
            text = SUMMARY_PREFIX + summary
            self._previous_summary = summary
        else:
            # 回退：保留一条说明消息
            text = (
                f"{SUMMARY_PREFIX}"
                f"{len(middle)} earlier messages were removed to free context. "
                f"Continue based on the recent messages below."
            )

        compressed.append({"role": "user", "content": text})
        compressed.extend(messages[tail_cut:])

        self.compression_count += 1

        # 防抖动
        savings = n_orig - len(compressed)
        if savings < 3:
            self._ineffective_count += 1
        else:
            self._ineffective_count = 0

        if not self.quiet:
            logger.info(
                f"Compressed: {n_orig} → {len(compressed)} messages "
                f"(~{estimate_tokens_rough(messages) - estimate_tokens_rough(compressed)} tokens saved)"
            )

        return compressed

    def _find_tail_by_tokens(
        self, messages: List[Dict], head_end: int
    ) -> int:
        """基于 token 预算确定尾部起始位置"""
        budget = self.tail_token_budget
        accumulated = 0
        cut = len(messages)
        min_tail = min(4, len(messages) - head_end - 1)

        for i in range(len(messages) - 1, head_end, -1):
            msg = messages[i]
            content = str(msg.get("content", "") or "")
            tokens = len(content) // CHARS_PER_TOKEN + 10
            if accumulated + tokens > budget * 1.5 and (len(messages) - i) >= min_tail:
                break
            accumulated += tokens
            cut = i

        return max(cut, head_end + 2)

    def should_compress(self, messages: List[Dict]) -> bool:
        """判断是否需要压缩"""
        if self._ineffective_count >= 2:
            return False
        return estimate_tokens_rough(messages) > self.threshold_tokens


# ═══════════════════════════════════════════════════════════════
# ContextManager — 统一上下文管理管道
# ═══════════════════════════════════════════════════════════════

class ContextManager:
    """统一上下文管理 — 单管道替代所有分散的截断逻辑

    管道阶段（按顺序）：
    S1: 消息规范化 → 确保每条消息有合法 role + content
    S2: 工具输出修剪 → 旧工具结果压缩为首尾摘要
    S3: 助手消息裁剪 → 旧助手回复保留首尾
    S4: Token 预算控制 → 超预算时从旧到新删除消息
    S5: 孤儿清理 → 确保 tool_call/tool 消息双向配对

    设计参考：deepseek-tui / Hermes / Aider 的上下文压缩策略
    """

    def __init__(
        self,
        max_input_tokens: int = 80000,
        protect_recent: int = 12,
        tool_head_chars: int = 600,
        tool_tail_chars: int = 200,
        assistant_head_chars: int = 400,
        assistant_tail_chars: int = 200,
    ):
        self.max_input_tokens = max_input_tokens
        self.protect_recent = protect_recent
        self.tool_head_chars = tool_head_chars
        self.tool_tail_chars = tool_tail_chars
        self.assistant_head_chars = assistant_head_chars
        self.assistant_tail_chars = assistant_tail_chars
        self.total_compressions = 0

    # ═══════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════

    def prepare(self, messages: List[Dict], think_mode: bool = False) -> List[Dict]:
        """完整管道：规范化 → 修剪 → 裁剪 → 预算控制 → 清理"""
        msgs = [dict(m) for m in messages]  # 深拷贝
        msgs = self._s1_normalize(msgs)
        msgs = self._s2_prune_tools(msgs)
        msgs = self._s3_trim_assistants(msgs, think_mode)
        msgs = self._s4_enforce_budget(msgs)
        msgs = self._s5_repair_orphans(msgs)
        return msgs

    # ═══════════════════════════════════════════
    # S1: 消息规范化
    # ═══════════════════════════════════════════

    @staticmethod
    def _s1_normalize(messages: List[Dict]) -> List[Dict]:
        """确保每条消息有合法 role + content 字符串"""
        result = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "")
            if role not in ("system", "user", "assistant", "tool"):
                continue
            content = msg.get("content")
            if content is None:
                content = ""
            elif not isinstance(content, str):
                content = str(content)
            # 移除思考过程标记（旧格式兼容）
            content = re.sub(
                r'\[思考过程\].*?\[/思考过程\]\s*', '', content,
                flags=re.DOTALL
            ).strip()
            cleaned = {"role": role, "content": content}
            if role == "assistant":
                if msg.get("reasoning_content"):
                    cleaned["reasoning_content"] = msg["reasoning_content"]
                if msg.get("tool_calls"):
                    cleaned["tool_calls"] = msg["tool_calls"]
            if role == "tool":
                if msg.get("tool_call_id"):
                    cleaned["tool_call_id"] = msg["tool_call_id"]
                if msg.get("tool_name"):
                    cleaned["tool_name"] = msg["tool_name"]
            result.append(cleaned)
        return result

    # ═══════════════════════════════════════════
    # S2: 工具输出修剪
    # ═══════════════════════════════════════════

    def _s2_prune_tools(self, messages: List[Dict]) -> List[Dict]:
        """旧工具输出压缩为首尾摘要（保护最近 N 条）"""
        if len(messages) <= self.protect_recent:
            return messages
        boundary = max(0, len(messages) - self.protect_recent)
        for i in range(boundary):
            msg = messages[i]
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            if len(content) <= self.tool_head_chars + self.tool_tail_chars + 100:
                continue
            head = content[:self.tool_head_chars]
            tail = content[-self.tool_tail_chars:]
            trimmed = len(content) - self.tool_head_chars - self.tool_tail_chars
            tool_name = msg.get("tool_name", "?")
            messages[i]["content"] = (
                f"[{tool_name}] " + head + f"\n... [{trimmed:,} chars] ...\n" + tail
            )
        return messages

    # ═══════════════════════════════════════════
    # S3: 助手消息裁剪
    # ═══════════════════════════════════════════

    def _s3_trim_assistants(self, messages: List[Dict], think_mode: bool) -> List[Dict]:
        """旧助手消息保留首尾 + 截断旧 reasoning_content"""
        if len(messages) <= self.protect_recent:
            return messages
        boundary = max(0, len(messages) - self.protect_recent)
        for i in range(boundary):
            msg = messages[i]
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "")
            if len(content) > self.assistant_head_chars + self.assistant_tail_chars + 100:
                head = content[:self.assistant_head_chars]
                tail = content[-self.assistant_tail_chars:]
                trimmed = len(content) - self.assistant_head_chars - self.assistant_tail_chars
                messages[i]["content"] = (
                    head + f"\n... [{trimmed:,} chars] ...\n" + tail
                )
            # 截断旧 reasoning_content（保留最近 3 条的完整思考）
            if msg.get("reasoning_content"):
                recent_reasoning = sum(
                    1 for m in messages[boundary:]
                    if m.get("role") == "assistant" and m.get("reasoning_content")
                )
                if recent_reasoning >= 3:
                    rc = str(msg["reasoning_content"])
                    if len(rc) > 300:
                        messages[i]["reasoning_content"] = rc[:300] + "..."
        return messages

    # ═══════════════════════════════════════════
    # S4: Token 预算控制
    # ═══════════════════════════════════════════

    def _s4_enforce_budget(self, messages: List[Dict]) -> List[Dict]:
        """超预算时从旧到新逐条删除非 system 消息"""
        est = estimate_tokens_rough(messages)
        if est <= self.max_input_tokens:
            return messages

        system_msgs = [m for m in messages if m.get("role") == "system"]
        other_msgs = [m for m in messages if m.get("role") != "system"]

        # 保护最近 N 条
        if len(other_msgs) > self.protect_recent:
            protected = other_msgs[-self.protect_recent:]
            pool = other_msgs[:-self.protect_recent]
        else:
            protected = other_msgs
            pool = []

        # 从旧到新删除，直到满足预算
        while pool and estimate_tokens_rough(system_msgs + pool + protected) > self.max_input_tokens:
            pool.pop(0)

        return system_msgs + pool + protected

    # ═══════════════════════════════════════════
    # S5: 孤儿消息清理
    # ═══════════════════════════════════════════

    @staticmethod
    def _s5_repair_orphans(messages: List[Dict]) -> List[Dict]:
        """双向清理：移除无 tool_calls 的 tool 消息 + 移除无 tool 响应的 tool_calls"""
        tool_response_ids = {m["tool_call_id"] for m in messages if m.get("role") == "tool" and m.get("tool_call_id")}
        assistant_tc_ids = set()
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if tc.get("id"):
                        assistant_tc_ids.add(tc["id"])

        result = []
        removed = 0
        for msg in messages:
            role = msg.get("role", "")
            if role == "assistant" and msg.get("tool_calls"):
                valid = [tc for tc in msg["tool_calls"] if tc.get("id", "") in tool_response_ids]
                if valid:
                    msg = dict(msg)
                    msg["tool_calls"] = valid
                else:
                    msg = {k: v for k, v in msg.items() if k != "tool_calls"}
            elif role == "tool":
                if msg.get("tool_call_id", "") not in assistant_tc_ids:
                    removed += 1
                    continue
            result.append(msg)

        if removed:
            logger.info(f"ContextManager: 移除 {removed} 条孤儿 tool 消息")
        return result
