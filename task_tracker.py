# task_tracker.py
"""
异步任务追踪器 — 支持取消/暂停单个工具调用

每个工具调用分配唯一 task_id，在独立线程中执行。
主Agent可以随时取消/查询正在运行的任务。
"""

import threading
import time
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("MHAgent.TaskTracker")


class TaskStatus(Enum):
    PENDING = "pending"     # 等待执行
    RUNNING = "running"     # 执行中
    DONE = "done"           # 已完成
    ERROR = "error"         # 执行出错
    CANCELLED = "cancelled" # 已取消


@dataclass
class Task:
    task_id: str
    tool_name: str
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None
    _cancel_event: threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = None
    started_at: float = 0
    finished_at: float = 0

    def cancel(self):
        self._cancel_event.set()
        if self.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            self.status = TaskStatus.CANCELLED
            self.finished_at = time.time()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()


class TaskTracker:
    """追踪所有运行中的工具任务"""

    def __init__(self):
        self._tasks: Dict[str, Task] = {}
        self._lock = threading.Lock()

    def create(self, task_id: str, tool_name: str) -> Task:
        with self._lock:
            task = Task(task_id=task_id, tool_name=tool_name)
            self._tasks[task_id] = task
            return task

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task and task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            task.cancel()
            logger.info(f"任务已取消: {task_id} ({task.tool_name})")
            return True
        return False

    def cancel_all(self):
        with self._lock:
            for task in self._tasks.values():
                if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    task.cancel()
            logger.info(f"已取消所有运行中的任务 ({len(self._tasks)} 个)")

    def list_running(self) -> List[Dict]:
        return [
            {"task_id": t.task_id, "tool_name": t.tool_name, "status": t.status.value}
            for t in self._tasks.values()
            if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
        ]

    def list_all(self) -> List[Dict]:
        return [
            {
                "task_id": t.task_id,
                "tool_name": t.tool_name,
                "status": t.status.value,
                "result": t.result[:100] if t.result else None
            }
            for t in self._tasks.values()
        ]
