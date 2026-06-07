# long_term_memory.py
"""
超长期记忆模块 — 跨会话保留用户偏好、称呼、习惯等信息。
支持身份隔离（agent / skuld），不同身份使用不同存储文件。
存储位置：agent_memories/long_term/user_{identity}.json
"""
import json
import os
import re
import time
import uuid
import logging
import threading
from pathlib import Path
from typing import List, Dict, Optional, Callable
from datetime import datetime

from config import MEMORY_DIR

logger = logging.getLogger("MHAgent.LongTermMemory")

# 尝试导入 jieba 分词（若未安装，仍可运行，但检索效果会减弱）
try:
    import jieba
    JIEBA_OK = True
except ImportError:
    JIEBA_OK = False


# ── 线程本地身份上下文 ─────────────────────────
# 用于在工具调用时获取当前会话的身份（agent / skuld），
# 避免在工具函数中直接依赖 session 模块造成循环导入。
_current_ctx = threading.local()


def set_current_identity(identity: str):
    """设置当前线程的身份标识"""
    _current_ctx.identity = identity


def get_current_identity() -> str:
    """获取当前线程的身份标识，默认 agent"""
    return getattr(_current_ctx, 'identity', 'agent')


class LongTermMemory:
    """用户长期画像记忆库（按 identity 隔离存储）"""

    _instances: Dict[str, 'LongTermMemory'] = {}
    _instances_lock = threading.Lock()

    def __new__(cls, identity: str = "agent", storage_dir: str = None):
        # 根据 identity 返回缓存实例（单例模式）
        with cls._instances_lock:
            if identity not in cls._instances:
                instance = super().__new__(cls)
                cls._instances[identity] = instance
            return cls._instances[identity]

    def __init__(self, identity: str = "agent", storage_dir: str = None):
        # 避免重复初始化
        if hasattr(self, '_initialized') and self._initialized:
            return
        self._initialized = True
        self.identity = identity
        if storage_dir is None:
            storage_dir = Path(MEMORY_DIR) / "long_term"
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.storage_dir / f"user_{identity}.json"
        self.lock = threading.Lock()
        self.memories: List[Dict] = []
        self._load()

    # ── 初始化加载 ─────────────────────────────
    def _load(self):
        with self.lock:
            if self.file_path.exists():
                try:
                    with open(self.file_path, 'r', encoding='utf-8') as f:
                        self.memories = json.load(f)
                    logger.info(f"加载长期记忆 [{self.identity}] {len(self.memories)} 条")
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"长期记忆文件损坏，初始化空库: {e}")
                    self.memories = []
            else:
                self.memories = []
                self._save()

    def _save(self):
        # 原子写入：先写 tmp 再替换
        tmp = self.file_path.with_suffix(".json.tmp")
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.memories, f, ensure_ascii=False, indent=2)
            tmp.replace(self.file_path)
        except Exception as e:
            logger.error(f"保存长期记忆失败: {e}")
            # 回退直接写入
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.memories, f, ensure_ascii=False, indent=2)

    # ── 工具 API ──────────────────────────────
    def remember(self, content: str, tags: List[str] = None) -> str:
        """记住一条信息，返回记忆 ID"""
        content = content.strip()
        if not content:
            return "记忆内容不能为空"

        with self.lock:
            mem_id = str(uuid.uuid4())[:8]
            now = datetime.now().isoformat()
            entry = {
                "id": mem_id,
                "content": content,
                "tags": tags or [],
                "created_at": now,
                "updated_at": now
            }
            self.memories.append(entry)
            self._save()
            logger.info(f"新增长期记忆 [{self.identity}] [{mem_id}]: {content[:60]}")
            return f"✅ 已记住 (ID: {mem_id}): {content[:100]}"

    def recall(self, query: str, limit: int = 5) -> str:
        """根据查询检索相关记忆"""
        query = query.strip()
        if not query or not self.memories:
            return "没有找到相关记忆。" if not self.memories else "请输入查询内容。"

        with self.lock:
            scored = self._search(query, limit)
            if not scored:
                return f"未找到与 '{query}' 相关的长期记忆。"
            
            lines = [f"🧠 与 '{query}' 相关的长期记忆："]
            for entry in scored:
                lines.append(f"  📌 [{entry['id']}] {entry['content']}")
                if entry.get('tags'):
                    lines.append(f"     标签: {', '.join(entry['tags'])}")
            return "\n".join(lines)

    def list_all(self, limit: int = 10) -> str:
        """列出最近的记忆"""
        with self.lock:
            recent = sorted(self.memories, key=lambda x: x.get('updated_at', ''), reverse=True)[:limit]
            if not recent:
                return "当前没有任何长期记忆。"
            lines = [f"📋 最近 {len(recent)} 条记忆："]
            for entry in recent:
                lines.append(f"  [{entry['id']}] {entry['content']}")
            return "\n".join(lines)

    def forget(self, memory_id: str) -> str:
        """删除某条记忆"""
        with self.lock:
            for i, entry in enumerate(self.memories):
                if entry.get('id') == memory_id:
                    self.memories.pop(i)
                    self._save()
                    logger.info(f"已删除长期记忆 [{self.identity}] {memory_id}")
                    return f"🗑️ 已遗忘记忆 (ID: {memory_id})"
            return f"未找到 ID 为 {memory_id} 的记忆。"

    # ── 上下文注入用（自动调用） ─────────────────
    def get_context_summary(self, max_tokens: int = 200) -> str:
        """生成一份「用户画像」摘要，注入到系统提示中"""
        with self.lock:
            if not self.memories:
                return ""
            # 简要取最近 10 条
            recent = sorted(self.memories, key=lambda x: x.get('updated_at', ''), reverse=True)[:10]
            lines = ["## 已知用户信息（长期记忆）"]
            for entry in recent:
                lines.append(f"- {entry['content']}")
            summary = "\n".join(lines)
            # 粗略裁剪到约 max_tokens
            if len(summary) > max_tokens * 4:  # 1 token ≈ 4 char
                summary = summary[:max_tokens * 4] + "\n-(已截断)"
            return summary

    # ── 内部检索 ──────────────────────────────
    def _search(self, query: str, limit: int) -> List[Dict]:
        """简单关键词匹配打分"""
        # 分词
        if JIEBA_OK:
            words = list(jieba.cut_for_search(query))
        else:
            # 简单按空格和标点分割
            words = re.findall(r'\w+', query.lower())
        
        scored = []
        for mem in self.memories:
            text = (mem.get('content', '') + ' ' + ' '.join(mem.get('tags', []))).lower()
            score = 0
            for w in words:
                if w in text:
                    score += 1  # 简单计数
            if score > 0:
                scored.append((score, mem))
        
        # 按得分降序，取前 limit
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:limit]]
