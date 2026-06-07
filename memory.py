# memory.py
import json
import re
import uuid
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict

from config import MEMORY_DIR, MAX_MEMORY_TURNS, MAX_TOOL_OUTPUT_CHARS, MAX_HISTORY_TOKENS_EST

logger = logging.getLogger("MHAgent.Memory")

class MemorySystem:
    def __init__(self, identity: str = "agent", memory_root: str = MEMORY_DIR, max_turns: int = MAX_MEMORY_TURNS):
        self.identity = identity
        self.memory_dir = Path(memory_root) / identity
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.max_turns = max_turns
        self.meta_file = self.memory_dir / "session_meta.json"
        self._load_meta()

    def _load_meta(self):
        if self.meta_file.exists():
            try:
                with open(self.meta_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                if not content.strip():
                    self.meta = {}
                    return
                self.meta = json.loads(content)
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
                logger.warning(f"会话元数据文件损坏，将重置: {e}")
                # 备份损坏文件
                bak = self.meta_file.with_suffix(".json.bak")
                try:
                    import shutil
                    shutil.copy2(self.meta_file, bak)
                    logger.info(f"已备份损坏文件至: {bak}")
                except:
                    pass
                self.meta = {}
                self._save_meta()
        else:
            self.meta = {}

    def _save_meta(self):
        # 先写临时文件再重命名，防止写入过程中崩溃导致文件损坏
        tmp = self.meta_file.with_suffix(".json.tmp")
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.meta, f, ensure_ascii=False, indent=2)
            tmp.replace(self.meta_file)
        except Exception as e:
            logger.error(f"保存元数据失败: {e}")
            # 回退：直接写原文件
            with open(self.meta_file, 'w', encoding='utf-8') as f:
                json.dump(self.meta, f, ensure_ascii=False, indent=2)

    def get_meta(self, session_id: str) -> Dict:
        return self.meta.get(session_id, {"title": "新对话", "updated_at": datetime.now().isoformat()})

    def update_meta(self, session_id: str, title: str = None):
        if session_id not in self.meta:
            self.meta[session_id] = {"title": "新对话", "updated_at": datetime.now().isoformat()}
        if title:
            self.meta[session_id]["title"] = title
        self.meta[session_id]["updated_at"] = datetime.now().isoformat()
        self._save_meta()

    def _get_session_file(self, session_id: str) -> Path:
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', session_id)
        return self.memory_dir / f"{safe_id}.json"

    def list_all_sessions(self) -> List[Dict]:
        sessions = []
        for f in self.memory_dir.glob("*.json"):
            sid = f.stem
            if sid == "session_meta" or sid.endswith(".bak") or sid.endswith(".tmp"):
                continue
            try:
                with open(f, 'r', encoding='utf-8') as fp:
                    data = json.load(fp)
                    if isinstance(data, list) and len(data) > 0:
                        meta = self.get_meta(sid)
                        sessions.append({
                            "id": sid,
                            "title": meta.get("title", "新对话"),
                            "updated_at": meta.get("updated_at", "")
                        })
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"跳过损坏的会话文件 {f.name}: {e}")
                continue
        sessions.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return sessions

    def _truncate_tool_output(self, content: str) -> str:
        if not content:
            return ""
        if len(content) > MAX_TOOL_OUTPUT_CHARS:
            return content[:MAX_TOOL_OUTPUT_CHARS] + f"\n... [输出过长，已截断，总长度 {len(content)} 字符]"
        return content

    def _estimate_tokens(self, messages: List[Dict]) -> int:
        total_chars = 0
        for msg in messages:
            if "content" in msg and msg["content"]:
                total_chars += len(msg["content"])
            if "reasoning_content" in msg and msg["reasoning_content"]:
                total_chars += len(msg["reasoning_content"])
            if "tool_calls" in msg:
                total_chars += len(json.dumps(msg["tool_calls"]))
        return total_chars // 4

    def _smart_truncate(self, conversation: List[Dict]) -> List[Dict]:
        if not conversation:
            return []
        system_msgs = [m for m in conversation if m.get("role") == "system"]
        others = [m for m in conversation if m.get("role") != "system"]

        for msg in others:
            if msg.get("role") == "tool" and "content" in msg:
                msg["content"] = self._truncate_tool_output(msg["content"])

        first_user_idx = None
        for i, msg in enumerate(others):
            if msg.get("role") == "user":
                first_user_idx = i
                break

        preserved_first_user = []
        if first_user_idx is not None:
            preserved_first_user = [others.pop(first_user_idx)]

        user_indices = [i for i, m in enumerate(others) if m.get("role") == "user"]
        keep_from_idx = 0
        if len(user_indices) > self.max_turns:
            keep_from_idx = user_indices[-self.max_turns]

        if keep_from_idx > 0:
            others = others[keep_from_idx:]

        if preserved_first_user:
            others = preserved_first_user + others

        test_msgs = system_msgs + others
        while self._estimate_tokens(test_msgs) > MAX_HISTORY_TOKENS_EST and len(others) > 6:
            for i in range(1, len(others)):
                if others[i].get("role") in ["user", "assistant", "tool"]:
                    del others[i]
                    break
            test_msgs = system_msgs + others

        return system_msgs + others

    def basic_repair(self, conversation: List[Dict]) -> List[Dict]:
        if not conversation:
            return []
        repaired = []
        for msg in conversation:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role not in ["system", "user", "assistant", "tool"]:
                continue
            new_msg = {"role": role}

            if "reasoning_content" in msg and msg["reasoning_content"]:
                new_msg["reasoning_content"] = msg["reasoning_content"]

            if "content" in msg and msg["content"] is not None:
                content = str(msg["content"])
                if role == "tool":
                    content = self._truncate_tool_output(content)
                new_msg["content"] = content
            elif role in ["user", "assistant"] and "tool_calls" not in msg and "reasoning_content" not in msg:
                new_msg["content"] = "[空消息]"

            if "tool_calls" in msg and msg["tool_calls"]:
                tool_calls = msg["tool_calls"]
                if isinstance(tool_calls, list):
                    cleaned_calls = []
                    for tc in tool_calls:
                        if not isinstance(tc, dict):
                            continue
                        tc_id = tc.get("id", str(uuid.uuid4()))
                        tc_type = tc.get("type", "function")
                        func = tc.get("function", {})
                        if not func.get("name"):
                            continue
                        args = func.get("arguments", "{}")
                        if not isinstance(args, str):
                            args = json.dumps(args)
                        try:
                            json.loads(args)
                        except:
                            args = "{}"
                        cleaned_calls.append({
                            "id": tc_id,
                            "type": tc_type,
                            "function": {
                                "name": func["name"],
                                "arguments": args
                            }
                        })
                    if cleaned_calls:
                        new_msg["tool_calls"] = cleaned_calls

            if "tool_call_id" in msg and msg["tool_call_id"]:
                new_msg["tool_call_id"] = msg["tool_call_id"]

            repaired.append(new_msg)
        return repaired

    def save_conversation(self, session_id: str, conversation: List[Dict]):
        if not conversation:
            return
        repaired = self.basic_repair(conversation)
        cleaned = self._smart_truncate(repaired)
        minimal = []
        for msg in cleaned:
            m = {"role": msg["role"]}
            if "content" in msg and msg["content"]:
                m["content"] = msg["content"]
            if "reasoning_content" in msg and msg["reasoning_content"]:
                m["reasoning_content"] = msg["reasoning_content"]
            if "tool_calls" in msg:
                m["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"]
                        }
                    }
                    for tc in msg["tool_calls"]
                ]
            if "tool_call_id" in msg:
                m["tool_call_id"] = msg["tool_call_id"]
            minimal.append(m)
        # 原子写入：先写 tmp 再 rename
        fpath = self._get_session_file(session_id)
        tmp = fpath.with_suffix(".json.tmp")
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(minimal, f, ensure_ascii=False, indent=2)
            tmp.replace(fpath)
        except Exception as e:
            logger.error(f"保存会话失败 {session_id}: {e}")
            # 回退直接写入
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(minimal, f, ensure_ascii=False, indent=2)
        self.update_meta(session_id)

    def load_conversation(self, session_id: str) -> List[Dict]:
        path = self._get_session_file(session_id)
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"加载会话 {session_id} 失败: {e}")
                return []
        return []

    def delete_conversation(self, session_id: str):
        path = self._get_session_file(session_id)
        if path.exists():
            path.unlink()
        # 也清理 tmp 文件
        tmp = path.with_suffix(".json.tmp")
        if tmp.exists():
            tmp.unlink()
        if session_id in self.meta:
            del self.meta[session_id]
            self._save_meta()
