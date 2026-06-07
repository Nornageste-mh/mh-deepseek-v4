# agent_coordinator.py
"""
子Agent协调器 — 主Agent控制N个子Agent并发工作
通信层级：
  1. 进程内事件总线 — 即时回调（主Agent↔子Agent，子Agent↔子Agent）
  2. 信号文件 — 跨进程持久化（支持独立进程的子Agent）

信号文件目录：agent_storage/tmp/agent_control/
  {name}.signal  →  主Agent写入指令：pause / resume / stop / status / msg:<text>
  {name}.state   →  子Agent写入状态JSON：status / progress / results_count / elapsed / current_step
  {name}.event   →  子Agent主动推送事件：progress_update / info / warning / error / result

主Agent ←→ 子Agent 通信矩阵：
  主→子: stop / pause / resume / status / msg（控制 + 消息）
  子→主: progress_update / info / warning / error / result / request（状态 + 事件 + 请求）
  子↔子: 经主Agent路由（direct_message / request_info）

使用：
  coordinator = AgentCoordinator(session)
  coordinator.run_sub_agent_async("task_name", "请分析...")   # 异步启动
  coordinator.pause("task_name")                             # 暂停
  coordinator.resume("task_name")                            # 恢复
  coordinator.stop("task_name")                              # 取消
  coordinator.get_progress("task_name")                      # 查看进度
  coordinator.stop_all()                                     # 全部取消
"""

import json
import logging
import threading
import time
import os
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, field

logger = logging.getLogger("MHAgent.Coordinator")

# 信号文件根目录
SIGNAL_DIR = Path(__file__).parent / "agent_storage" / "tmp" / "agent_control"


# ═══════════════════════════════════════════════════════════════
# 事件系统 — 子Agent → 主Agent 异步通信
# ═══════════════════════════════════════════════════════════════

class SubAgentEventType(Enum):
    PROGRESS = "progress_update"   # 进度更新
    INFO = "info"                  # 一般信息
    WARNING = "warning"            # 警告
    ERROR = "error"                # 错误
    RESULT = "result"              # 中间结果
    REQUEST = "request"            # 向主Agent请求（如索要信息）


@dataclass
class SubAgentEvent:
    """子Agent → 主Agent 事件的载体"""
    from_agent: str
    event_type: SubAgentEventType
    payload: Any = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    request_id: str = ""  # 用于 request 类型的回应绑定


class AgentEventBus:
    """
    进程内事件总线 — 主Agent与子Agent即时通信
    
    - 子Agent可 push_event 向主Agent推送
    - 主Agent通过 subscribe 监听特定类型事件
    - 子Agent间通过主Agent路由消息
    """
    def __init__(self):
        self._subscribers: Dict[SubAgentEventType, List[Callable]] = {}
        self._all_subscribers: List[Callable] = []  # 监听所有事件
        self._event_log: List[SubAgentEvent] = []
        self._lock = threading.Lock()

    def subscribe(self, event_type: SubAgentEventType, callback: Callable[[SubAgentEvent], None]):
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(callback)

    def subscribe_all(self, callback: Callable[[SubAgentEvent], None]):
        with self._lock:
            self._all_subscribers.append(callback)

    def push(self, event: SubAgentEvent):
        with self._lock:
            self._event_log.append(event)
            # 通知特定类型订阅者
            for cb in self._subscribers.get(event.event_type, []):
                try: cb(event)
                except Exception: pass
            # 通知全局订阅者
            for cb in self._all_subscribers:
                try: cb(event)
                except Exception: pass

    def get_recent_events(self, agent_name: str = None, limit: int = 20) -> List[dict]:
        with self._lock:
            evts = self._event_log
            if agent_name:
                evts = [e for e in evts if e.from_agent == agent_name]
            return [
                {"from": e.from_agent, "type": e.event_type.value, "payload": str(e.payload)[:200], "time": e.timestamp}
                for e in evts[-limit:]
            ]


class SubAgent:
    """子Agent — 可被主Agent随时叫停/暂停/恢复，通过信号文件通信"""

    def __init__(self, name: str, session: Any):
        self.name = name
        self.session = session
        self.results: List[Dict] = []
        self.status = "idle"  # idle / running / paused / done / error / stopped
        self.shared_context: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()  # True=已暂停
        self._current_resp = None
        
        # 进度追踪
        self._started_at: Optional[float] = None
        self._current_step = ""
        self._total_steps = 0
        self._completed_steps = 0
        
        # 信号文件路径
        self._signal_dir = SIGNAL_DIR / name
        self._signal_file = self._signal_dir / f"{name}.signal"
        self._state_file = self._signal_dir / f"{name}.state"
        self._signal_dir.mkdir(parents=True, exist_ok=True)

    # ============================================================
    # 信号文件读写
    # ============================================================
    def _read_signal(self) -> Optional[str]:
        """读取主Agent发来的指令"""
        try:
            if self._signal_file.exists():
                signal = self._signal_file.read_text(encoding="utf-8").strip()
                self._signal_file.unlink()  # 消费后删除
                return signal if signal else None
        except Exception:
            pass
        return None

    def _write_state(self):
        """写入当前状态供主Agent查询"""
        try:
            elapsed = (time.time() - self._started_at) if self._started_at else 0
            state = {
                "name": self.name,
                "status": self.status,
                "progress": f"{self._completed_steps}/{self._total_steps}" if self._total_steps > 0 else "N/A",
                "results_count": len(self.results),
                "elapsed_seconds": round(elapsed, 2),
                "current_step": self._current_step,
                "last_updated": datetime.now().isoformat(),
            }
            self._state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"写入状态文件失败: {e}")

    def _check_signals(self):
        """检查并处理信号（在任务循环中调用）"""
        signal = self._read_signal()
        if not signal:
            return
        
        signal = signal.lower().strip()
        
        if signal == "stop":
            self._stop_event.set()
            self._pause_event.clear()  # 取消暂停以让线程退出
            with self._lock:
                self.status = "stopped"
            logger.info(f"子Agent [{self.name}] 收到停止信号")
            
        elif signal == "pause":
            self._pause_event.set()
            with self._lock:
                if self.status == "running":
                    self.status = "paused"
            logger.info(f"子Agent [{self.name}] 收到暂停信号")
            
        elif signal == "resume":
            self._pause_event.clear()
            with self._lock:
                if self.status == "paused":
                    self.status = "running"
            logger.info(f"子Agent [{self.name}] 收到恢复信号")
            
        elif signal == "status":
            # 主Agent查询状态，更新状态文件即可
            self._write_state()

    def _wait_if_paused(self):
        """如果被暂停则阻塞等待恢复或停止"""
        while self._pause_event.is_set() and not self._stop_event.is_set():
            self._check_signals()  # 暂停期间也检查恢复/停止信号
            time.sleep(0.1)

    # ============================================================
    # 核心控制方法
    # ============================================================
    def push_event(self, event_type: SubAgentEventType, payload: Any = None):
        """子Agent → 主Agent 推送事件"""
        if hasattr(self, '_event_bus') and self._event_bus:
            self._event_bus.push(SubAgentEvent(
                from_agent=self.name,
                event_type=event_type,
                payload=payload
            ))

    def stop(self):
        """主Agent叫停此子Agent（进程内直接调用）"""
        self._stop_event.set()
        self._pause_event.clear()
        if self._current_resp:
            try:
                self._current_resp.close()
            except Exception:
                pass
        with self._lock:
            if self.status in ("running", "paused"):
                self.status = "stopped"
        self._write_state()
        logger.info(f"子Agent [{self.name}] 已被叫停")

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    def set_progress(self, current_step: str, completed: int = 0, total: int = 0):
        """更新进度信息"""
        self._current_step = current_step
        if total > 0:
            self._total_steps = total
        self._completed_steps = completed
        self._write_state()

    # ============================================================
    # 任务执行（支持暂停/恢复/取消）
    # ============================================================
    def execute_sync(self, prompt: str) -> str:
        """同步执行任务。支持暂停/恢复/停止信号。"""
        if self._stop_event.is_set():
            self.status = "stopped"
            return f"[已取消] 子Agent [{self.name}] 已被停止"

        with self._lock:
            self.status = "running"
        self._stop_event.clear()
        self._pause_event.clear()
        self._started_at = time.time()
        self._current_step = "初始化"
        self._completed_steps = 0
        self._total_steps = 4  # 初始化 -> 请求 -> 处理 -> 完成
        self._write_state()

        try:
            # Step 1: 检查停止信号
            self._check_signals()
            self._wait_if_paused()
            if self._stop_event.is_set():
                self.status = "stopped"
                return f"[已取消] 子Agent [{self.name}] 已被停止"

            self._current_step = "获取Provider"
            self._completed_steps = 1
            self._write_state()

            provider = self.session.provider_mgr.get(self.session.provider_id)
            if not provider:
                self.status = "error"
                self._write_state()
                return "[错误] Provider 不可用"

            # Step 2: 再次检查
            self._check_signals()
            self._wait_if_paused()
            if self._stop_event.is_set():
                self.status = "stopped"
                return f"[已取消] 子Agent [{self.name}] 已被停止"

            self._current_step = "构建请求"
            self._completed_steps = 2
            self._write_state()

            payload = {
                "model": self.session.model_id,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4096,
                "temperature": 0.3,
                "thinking": {"type": "disabled"},
            }
            payload = provider.adapt_payload(payload, think_mode=False)

            url = f"{provider.api_base}{provider.api_path}"
            headers = provider.get_headers()

            # Step 3: 发送请求前检查
            self._check_signals()
            self._wait_if_paused()
            if self._stop_event.is_set():
                self.status = "stopped"
                return f"[已取消] 子Agent [{self.name}] 已被停止"

            self._current_step = "等待LLM响应"
            self._completed_steps = 3
            self._write_state()

            import requests
            resp = requests.post(url, headers=headers, json=payload, timeout=60, stream=True)
            self._current_resp = resp

            # 流式读取中也可被中断
            if self._stop_event.is_set():
                resp.close()
                self.status = "stopped"
                return f"[已取消] 子Agent [{self.name}] 已被停止"

            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                self.results.append({
                    "prompt": prompt,
                    "result": content,
                    "completed_at": datetime.now().isoformat(),
                })
                
                self._current_step = "完成"
                self._completed_steps = 4
                with self._lock:
                    if self.status not in ("stopped", "paused"):
                        self.status = "done"
                self._write_state()
                return content
            else:
                with self._lock:
                    if self.status not in ("stopped", "paused"):
                        self.status = "error"
                self._write_state()
                return f"[错误] HTTP {resp.status_code}"
        except Exception as e:
            with self._lock:
                if self.status not in ("stopped", "paused"):
                    self.status = "error"
            self._write_state()
            if self._stop_event.is_set():
                return f"[已取消] 子Agent [{self.name}] 已被停止"
            return f"[异常] {e}"
        finally:
            self._current_resp = None
            self._write_state()

    def execute_iterative(self, steps: list, step_executor) -> str:
        """
        多步骤迭代执行 — 每步之间都检查信号，支持逐步骤的暂停/恢复/取消。
        
        steps: 步骤描述列表，如 ["扫描端口", "检查漏洞", "生成报告"]
        step_executor: callable(step_description) → str
        """
        if self._stop_event.is_set():
            self.status = "stopped"
            return f"[已取消] 子Agent [{self.name}] 已被停止"

        with self._lock:
            self.status = "running"
        self._stop_event.clear()
        self._pause_event.clear()
        self._started_at = time.time()
        self._total_steps = len(steps)
        self._completed_steps = 0
        self._write_state()

        all_results = []

        for i, step_desc in enumerate(steps):
            self._check_signals()
            self._wait_if_paused()
            
            if self._stop_event.is_set():
                self.status = "stopped"
                self._write_state()
                if all_results:
                    return f"[部分完成-已取消] 完成 {len(all_results)}/{len(steps)} 步:\n" + "\n".join(all_results)
                return f"[已取消] 子Agent [{self.name}] 已被停止"

            self._current_step = step_desc
            self._completed_steps = i
            self._write_state()

            try:
                result = step_executor(step_desc)
                all_results.append(f"[步骤{i+1}] {step_desc}: {result}")
            except Exception as e:
                all_results.append(f"[步骤{i+1}] {step_desc}: 错误 - {e}")
                self._current_step = f"步骤{i+1}出错"
                self._write_state()

        self._current_step = "全部完成"
        self._completed_steps = len(steps)
        with self._lock:
            if self.status not in ("stopped", "paused"):
                self.status = "done"
        self.results.append({"steps": steps, "results": all_results})
        self._write_state()
        return "\n".join(all_results)

    # ============================================================
    # 上下文管理
    # ============================================================
    def get_context(self) -> Dict[str, Any]:
        return dict(self.shared_context)

    def set_context(self, key: str, value: Any):
        with self._lock:
            self.shared_context[key] = value


class AgentCoordinator:
    """主Agent协调器 — 事件总线 + 信号文件 + 直接消息路由"""

    def __init__(self, session: Any):
        self.session = session
        self.sub_agents: Dict[str, SubAgent] = {}
        self._lock = threading.Lock()
        self.event_bus = AgentEventBus()
        self._pending_requests: Dict[str, threading.Event] = {}  # request_id → response
        SIGNAL_DIR.mkdir(parents=True, exist_ok=True)

    # ============================================================
    # Agent 管理
    # ============================================================
    def get_or_create(self, name: str) -> SubAgent:
        with self._lock:
            if name not in self.sub_agents:
                agent = SubAgent(name, self.session)
                agent._event_bus = self.event_bus  # 注入事件总线
                self.sub_agents[name] = agent
                logger.info(f"子Agent 已创建: {name}")
            return self.sub_agents[name]

    def remove(self, name: str):
        """移除子Agent并清理信号文件"""
        with self._lock:
            if name in self.sub_agents:
                self.sub_agents[name].stop()
                del self.sub_agents[name]
            # 清理信号文件
            signal_dir = SIGNAL_DIR / name
            for f in [signal_dir / f"{name}.signal", signal_dir / f"{name}.state"]:
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass
            try:
                signal_dir.rmdir()
            except Exception:
                pass

    # ============================================================
    # 直接调用（进程内）
    # ============================================================
    def run_sub_agent(self, name: str, prompt: str) -> str:
        agent = self.get_or_create(name)
        return agent.execute_sync(prompt)

    def run_sub_agent_async(self, name: str, prompt: str) -> threading.Thread:
        """异步运行子Agent，返回线程对象"""
        agent = self.get_or_create(name)
        t = threading.Thread(target=agent.execute_sync, args=(prompt,), daemon=True, name=f"subagent-{name}")
        t.start()
        return t

    def run_iterative_async(self, name: str, steps: list, step_executor) -> threading.Thread:
        """异步运行多步骤任务"""
        agent = self.get_or_create(name)
        t = threading.Thread(target=agent.execute_iterative, args=(steps, step_executor),
                            daemon=True, name=f"subagent-{name}")
        t.start()
        return t

    def stop(self, name: str):
        """叫停指定子Agent（进程内直接调用）"""
        agent = self.sub_agents.get(name)
        if agent:
            agent.stop()

    def stop_all(self):
        """叫停所有子Agent"""
        with self._lock:
            for agent in self.sub_agents.values():
                agent.stop()
        logger.info(f"主Agent 已叫停所有 {len(self.sub_agents)} 个子Agent")

    # ============================================================
    # 信号文件通信（跨进程可用）
    # ============================================================
    def send_signal(self, name: str, signal: str):
        """
        通过信号文件向子Agent发送指令（支持跨进程）
        
        signal:
          - "pause"   → 暂停执行
          - "resume"  → 恢复执行
          - "stop"    → 取消执行
          - "status"  → 触发状态更新
        """
        signal = signal.lower().strip()
        if signal not in ("pause", "resume", "stop", "status"):
            raise ValueError(f"无效信号: {signal}，可用: pause / resume / stop / status")
        
        signal_dir = SIGNAL_DIR / name
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal_file = signal_dir / f"{name}.signal"
        signal_file.write_text(signal, encoding="utf-8")
        logger.info(f"主Agent → [{name}]: {signal}")

    def send_signal_all(self, signal: str):
        """向所有已知子Agent发送信号"""
        with self._lock:
            for name in list(self.sub_agents.keys()):
                self.send_signal(name, signal)
        # 也扫描信号目录中可能存在的未知子Agent
        if SIGNAL_DIR.exists():
            for d in SIGNAL_DIR.iterdir():
                if d.is_dir() and d.name not in self.sub_agents:
                    self.send_signal(d.name, signal)

    def pause(self, name: str):
        """暂停指定子Agent"""
        self.send_signal(name, "pause")

    def resume(self, name: str):
        """恢复指定子Agent"""
        self.send_signal(name, "resume")

    def pause_all(self):
        """暂停所有子Agent"""
        self.send_signal_all("pause")

    def resume_all(self):
        """恢复所有子Agent"""
        self.send_signal_all("resume")

    # ============================================================
    # 进度查询
    # ============================================================
    def get_progress(self, name: str) -> Dict[str, Any]:
        """获取子Agent当前进度"""
        # 优先从状态文件读取
        state_file = SIGNAL_DIR / name / f"{name}.state"
        if state_file.exists():
            try:
                return json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        
        # 回退到内存对象
        agent = self.sub_agents.get(name)
        if agent:
            elapsed = (time.time() - agent._started_at) if agent._started_at else 0
            return {
                "name": agent.name,
                "status": agent.status,
                "progress": f"{agent._completed_steps}/{agent._total_steps}" if agent._total_steps > 0 else "N/A",
                "results_count": len(agent.results),
                "elapsed_seconds": round(elapsed, 2),
                "current_step": agent._current_step,
            }
        
        return {"name": name, "status": "unknown", "error": "子Agent不存在"}

    def get_all_progress(self) -> List[Dict]:
        """获取所有子Agent进度"""
        # 扫描信号目录获取所有子Agent状态
        all_progress = []
        seen = set()
        
        if SIGNAL_DIR.exists():
            for d in SIGNAL_DIR.iterdir():
                if d.is_dir():
                    name = d.name
                    seen.add(name)
                    state_file = d / f"{name}.state"
                    if state_file.exists():
                        try:
                            all_progress.append(json.loads(state_file.read_text(encoding="utf-8")))
                            continue
                        except Exception:
                            pass
                    all_progress.append({"name": name, "status": "unknown"})
        
        # 补充内存中的子Agent
        for name, agent in self.sub_agents.items():
            if name not in seen:
                all_progress.append(self.get_progress(name))
        
        return all_progress

    def get_status_summary(self) -> str:
        """获取所有子Agent状态摘要（人类可读）"""
        all_prog = self.get_all_progress()
        if not all_prog:
            return "当前无子Agent运行"
        
        lines = ["=" * 55, f"  子Agent状态面板 ({len(all_prog)}个)", "=" * 55]
        lines.append(f"  {'名称':<20s} {'状态':<10s} {'进度':<8s} {'耗时':<8s} {'结果':<6s}")
        lines.append("  " + "-" * 52)
        
        status_order = {"running": 0, "paused": 1, "idle": 2, "done": 3, "stopped": 4, "error": 5}
        
        for p in sorted(all_prog, key=lambda x: status_order.get(x.get("status", ""), 99)):
            name = p.get("name", "?")[:19]
            status = p.get("status", "?")
            progress = p.get("progress", "-")
            elapsed = f"{p.get('elapsed_seconds', 0):.1f}s"
            results = str(p.get("results_count", 0))
            
            # 状态标记
            status_icons = {
                "running": "▶ RUN",
                "paused":  "⏸ PAUSE",
                "done":    "✓ DONE",
                "stopped": "■ STOP",
                "error":   "✗ ERR",
                "idle":    "○ IDLE",
            }
            status_display = status_icons.get(status, status.upper())
            
            lines.append(f"  {name:<20s} {status_display:<10s} {progress:<8s} {elapsed:<8s} {results:<6s}")
        
        lines.append("  " + "-" * 52)
        
        # 统计
        counts = {}
        for p in all_prog:
            s = p.get("status", "?")
            counts[s] = counts.get(s, 0) + 1
        summary_parts = [f"{cnt}{status_icons.get(st, st)}" for st, cnt in sorted(counts.items())]
        lines.append(f"  总计: {' | '.join(summary_parts)}")
        
        return "\n".join(lines)

    # ============================================================
    # 批量操作
    # ============================================================
    def batch_control(self, names_and_signals: List[tuple]):
        """
        批量控制：[(name, signal), ...]
        示例: [("agent-1", "pause"), ("agent-2", "stop"), ("agent-3", "resume")]
        """
        results = []
        for name, signal in names_and_signals:
            try:
                self.send_signal(name, signal)
                results.append({"name": name, "signal": signal, "result": "ok"})
            except Exception as e:
                results.append({"name": name, "signal": signal, "result": str(e)})
        logger.info(f"批量控制完成: {len(results)} 个指令")
        return results

    # ============================================================
    # 上下文操作
    # ============================================================
    def broadcast_context(self, key: str, value: Any):
        with self._lock:
            for agent in self.sub_agents.values():
                agent.set_context(key, value)

    def share_between(self, from_name: str, to_name: str, key: str) -> Any:
        src = self.sub_agents.get(from_name)
        dst = self.sub_agents.get(to_name)
        if src and dst:
            val = src.get_context().get(key)
            if val is not None:
                dst.set_context(key, val)
            return val
        return None

    def list_agents(self) -> List[Dict]:
        return [
            {
                "name": a.name,
                "status": a.status,
                "is_paused": a.is_paused(),
                "results_count": len(a.results),
                "current_step": a._current_step,
            }
            for a in self.sub_agents.values()
        ]

    # ============================================================
    # 子Agent → 子Agent 消息路由（平等通信，经主Agent中转）
    # ============================================================
    def route_message(self, from_name: str, to_name: str, message: str) -> bool:
        """子Agent间直接消息传递 — 主Agent作为路由器"""
        dst = self.sub_agents.get(to_name)
        if not dst:
            return False
        
        event = SubAgentEvent(
            from_agent=from_name,
            event_type=SubAgentEventType.INFO,
            payload={"message": message, "via": "coordinator"}
        )
        self.event_bus.push(event)
        
        # 同时写入目标Agent的上下文
        if not hasattr(dst, '_inbox'):
            dst._inbox = []
        dst._inbox.append({"from": from_name, "message": message, "time": datetime.now().isoformat()})
        logger.info(f"消息路由: [{from_name}] → [{to_name}]: {message[:100]}")
        return True

    def broadcast_message(self, from_name: str, message: str):
        """子Agent广播消息给所有子Agent（不包含自己）"""
        with self._lock:
            for name, agent in self.sub_agents.items():
                if name != from_name:
                    self.route_message(from_name, name, message)

    def get_inbox(self, agent_name: str) -> List[Dict]:
        """获取子Agent的消息收件箱"""
        agent = self.sub_agents.get(agent_name)
        if agent and hasattr(agent, '_inbox'):
            return list(agent._inbox)
        return []

    # ============================================================
    # 子Agent → 主Agent 请求/响应
    # ============================================================
    def respond_to_request(self, request_id: str, response: Any):
        """主Agent回应子Agent的请求"""
        event = self._pending_requests.get(request_id)
        if event:
            event.response = response
            event.set()

    def get_pending_requests(self) -> List[Dict]:
        """获取所有待处理的子Agent请求"""
        pending = []
        for rid, event in self._pending_requests.items():
            if not event.is_set():
                pending.append({"id": rid, "status": "pending"})
        return pending

    # ============================================================
    # 工具注册（供 session.py 注册为 AI 工具）
    # ============================================================
    def register_tools(self, registry):
        """向 ToolRegistry 注册子Agent控制工具"""
        
        def _control_sub_agent(name: str, action: str, **kwargs) -> str:
            """控制子Agent：stop / pause / resume / status"""
            action = action.lower().strip()
            if action == "stop":
                self.stop(name)
                return f"子Agent [{name}] 已停止"
            elif action == "pause":
                self.send_signal(name, "pause")
                return f"子Agent [{name}] 已暂停"
            elif action == "resume":
                self.send_signal(name, "resume")
                return f"子Agent [{name}] 已恢复"
            elif action == "status":
                return json.dumps(self.get_progress(name), ensure_ascii=False, indent=2)
            return f"未知操作: {action}"

        def _query_sub_agent(name: str, query_type: str = "status", **kwargs) -> str:
            """查询子Agent状态/结果/事件"""
            if query_type == "status":
                return json.dumps(self.get_progress(name), ensure_ascii=False, indent=2)
            elif query_type == "events":
                evts = self.event_bus.get_recent_events(name)
                return json.dumps(evts, ensure_ascii=False, indent=2)
            elif query_type == "inbox":
                return json.dumps(self.get_inbox(name), ensure_ascii=False, indent=2)
            elif query_type == "result" and name in self.sub_agents:
                results = self.sub_agents[name].results
                return json.dumps(results[-3:], ensure_ascii=False, indent=2)
            return f"未知查询: {query_type}"

        def _list_sub_agents(**kwargs) -> str:
            """列出所有子Agent"""
            return self.get_status_summary()

        def _broadcast_to_sub_agents(message: str, **kwargs) -> str:
            """向所有子Agent广播消息"""
            self.broadcast_message("master", message)
            return f"已向 {len(self.sub_agents)} 个子Agent广播消息"

        def _send_to_sub_agent(target: str, message: str, **kwargs) -> str:
            """发送消息给指定子Agent"""
            ok = self.route_message("master", target, message)
            return f"消息{'已送达' if ok else '未送达-目标不存在'}: [{target}]"

        registry.register(
            "control_sub_agent",
            _control_sub_agent,
            "控制子Agent：停止/暂停/恢复/查看状态。主Agent对此有绝对控制权。",
            {
                "name": {"type": "string", "description": "子Agent名称"},
                "action": {"type": "string", "enum": ["stop", "pause", "resume", "status"],
                          "description": "操作: stop=停止, pause=暂停, resume=恢复, status=查看状态"}
            }
        )

        registry.register(
            "query_sub_agent",
            _query_sub_agent,
            "查询子Agent的详细信息：状态、事件日志、收件箱、结果。",
            {
                "name": {"type": "string", "description": "子Agent名称"},
                "query_type": {"type": "string", "enum": ["status", "events", "inbox", "result"],
                              "description": "查询类型: status/events/inbox/result"}
            }
        )

        registry.register(
            "list_sub_agents",
            _list_sub_agents,
            "列出所有子Agent及其状态摘要。",
            {}
        )

        registry.register(
            "broadcast_to_sub_agents",
            _broadcast_to_sub_agents,
            "向所有子Agent广播消息（主Agent专用）。",
            {"message": {"type": "string", "description": "广播消息内容"}}
        )

        registry.register(
            "send_to_sub_agent",
            _send_to_sub_agent,
            "向指定子Agent发送消息。",
            {
                "target": {"type": "string", "description": "目标子Agent名称"},
                "message": {"type": "string", "description": "消息内容"}
            }
        )