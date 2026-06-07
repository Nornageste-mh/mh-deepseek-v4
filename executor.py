"""安全命令执行器（跨平台：修复 Android 权限/root 问题）"""
import subprocess
import logging
import re
import sys
import os
import time
import threading
import shlex
from pathlib import Path
from typing import Dict, Any, Optional, List

from config import BLOCKED_COMMANDS, DANGEROUS_PATTERNS, TOOL_CALL_TIMEOUT

IS_WINDOWS = sys.platform == "win32"
IS_ANDROID = sys.platform == "android" or (sys.platform == "linux" and "ANDROID_ROOT" in os.environ)

logger = logging.getLogger("MHAgent")


class SecureExecutor:
    def __init__(self, work_dir: Optional[str] = None):
        self.work_dir = Path(work_dir) if work_dir else Path.cwd()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.pending_authorizations = {}
        self._current_proc = None        # 当前正在运行的子进程（供外部终止）
        self._current_proc_lock = threading.Lock()

        # Android 存储路径
        if IS_ANDROID:
            self._detect_storage_paths()

        if IS_ANDROID:
            self._check_root_availability()
            self._check_adb_availability()
            self._check_shizuku_availability()
        else:
            self.root_available = False
            self.adb_available = False
            self.shizuku_available = False

    def _detect_storage_paths(self):
        """检测 Android 存储路径（支持 Termux 和 APK 内嵌环境）"""
        self.android_storage_paths = []
        candidates = [
            "/sdcard",
            "/storage/emulated/0",
            "/storage/self/primary",
        ]
        termux_storage = os.path.expanduser("~/storage/shared")
        if os.path.isdir(termux_storage):
            candidates.append(termux_storage)
        for env_var in ["EXTERNAL_STORAGE", "SECONDARY_STORAGE"]:
            val = os.environ.get(env_var)
            if val and os.path.isdir(val):
                candidates.append(val)
        for candidate in candidates:
            if os.path.isdir(candidate):
                self.android_storage_paths.append(candidate)
        if self.android_storage_paths:
            self.android_sdcard = self.android_storage_paths[0]
        else:
            self.android_sdcard = "/sdcard"

    def _check_root_availability(self):
        """检测 root 权限（momo/MagiskDetector 方案：多层检测）
        
        策略：
        1. su 文件存在性扫描（momo 方式：遍历 PATH + 已知路径）
        2. which su 检测
        3. su 执行测试（多种参数格式兼容 Magisk/KernelSU/APatch）
        4. Magisk 挂载痕迹检测
        5. SELinux 状态辅助判断
        """
        found_su_paths = []
        
        # === 阶段1：su 文件存在性扫描 ===
        # 静态已知路径（momo 使用的路径列表）
        static_su_paths = [
            "/system/bin/su",
            "/system/xbin/su",
            "/system_ext/bin/su",
            "/product/bin/su",
            "/vendor/bin/su",
            "/odm/bin/su",
            "/sbin/su",
            "/system/sbin/su",
            "/debug_ramdisk/su",
            "/data/adb/ksu/bin/su",
            "/data/adb/magisk/su",
            "/data/adb/ap/bin/su",
            "/data/adb/modules/ksu/bin/su",
            "/data/data/com.termux/files/usr/bin/su",
        ]
        for p in static_su_paths:
            if os.path.isfile(p) and os.access(p, os.X_OK):
                found_su_paths.append(p)
        
        # PATH 遍历（momo 方式：which su + PATH 扫描）
        for path_dir in os.environ.get("PATH", "").split(":"):
            su_path = os.path.join(path_dir, "su")
            if su_path not in found_su_paths and os.path.isfile(su_path) and os.access(su_path, os.X_OK):
                found_su_paths.append(su_path)
        
        # which su
        try:
            r = subprocess.run(["which", "su"], capture_output=True, text=True, timeout=3)
            which_su = r.stdout.strip()
            if which_su and which_su not in found_su_paths and os.path.isfile(which_su):
                found_su_paths.append(which_su)
        except Exception:
            pass
        
        logger.info(f"发现 su 候选: {len(found_su_paths)} 个路径")
        
        # === 阶段2：su 执行测试（多格式兼容） ===
        # 测试命令格式（兼容 Magisk -c / KernelSU 0 / APatch）
        test_cases = [
            ("-c 'echo root_ok'", "root_ok"),
            ("-c echo_root_ok", "root_ok"),
            ("0 id", "uid=0"),
            ("0 sh -c 'echo root_ok'", "root_ok"),
        ]
        
        for su_path in found_su_paths:
            for su_args, expected in test_cases:
                try:
                    full_cmd = f"{su_path} {su_args}"
                    result = subprocess.run(
                        full_cmd, shell=True,
                        capture_output=True, text=True, timeout=5
                    )
                    combined = (result.stdout + result.stderr).lower()
                    if result.returncode == 0 and expected.lower() in combined:
                        self.root_available = True
                        self._su_cmd = su_path
                        self._su_format = su_args.split()[0]
                        logger.info(f"Root 可用: {su_path} (格式: {su_args})")
                        return
                except subprocess.TimeoutExpired:
                    # su 弹窗授权超时是正常的
                    logger.debug(f"su 超时: {su_path} {su_args}")
                    continue
                except Exception:
                    continue
        
        # === 阶段3：Magisk 挂载痕迹检测（辅助判断） ===
        magisk_mounts = False
        try:
            with open("/proc/self/mounts", "r") as f:
                mounts = f.read()
            magisk_indicators = ["magisk", "Magisk", ".magisk", "core/mirror", "core/img"]
            for indicator in magisk_indicators:
                if indicator in mounts:
                    magisk_mounts = True
                    break
        except Exception:
            pass
        
        # === 阶段4：SELinux 检测 ===
        selinux_permissive = False
        try:
            r = subprocess.run(["getenforce"], capture_output=True, text=True, timeout=3)
            selinux_permissive = "Permissive" in r.stdout
        except Exception:
            pass
        
        # === 综合判断 ===
        # 如果找到 su 文件且 Magisk 挂载存在，但 su 执行失败 → 可能是隐藏了 su 但 root 存在
        if found_su_paths and magisk_mounts:
            self.root_available = True
            self._su_cmd = found_su_paths[0]
            self._su_format = "-c"
            logger.info(f"Root 可用（Magisk 挂载痕迹 + su 文件存在）: {found_su_paths[0]}")
            return
        
        self.root_available = False
        self._su_cmd = "su"
        self._su_format = "-c"
        if found_su_paths:
            logger.info(f"发现 su 文件但执行测试失败，可能被隐藏或需要授权")
        logger.info("Root 不可用")


    def _check_shizuku_availability(self):
        """检测 Shizuku 可用性（rish 方案，参照 RikkaApps/Shizuku）
        
        策略：
        1. 检查 rish 文件是否存在且可执行
        2. 检查 Shizuku 服务进程是否运行
        3. rish 连接测试
        """
        # rish 路径候选
        rish_candidates = [
            "/data/local/tmp/rish",
            "/data/local/tmp/rish_shizuku",
            "/sdcard/rish",
        ]
        
        rish_path = None
        for p in rish_candidates:
            if os.path.isfile(p) and os.access(p, os.X_OK):
                rish_path = p
                break
        
        # 也检查 PATH 中是否有 rish
        if not rish_path:
            for path_dir in os.environ.get("PATH", "").split(":"):
                p = os.path.join(path_dir, "rish")
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    rish_path = p
                    break
        
        if not rish_path:
            self.shizuku_available = False
            self._shizuku_cmd = "rish"
            logger.info("Shizuku: rish 未找到，请安装 Shizuku 应用")
            return
        
        # 检查 Shizuku 服务进程是否运行
        shizuku_running = False
        try:
            r = subprocess.run(
                "ps -A 2>/dev/null | grep -i shizuku || ps 2>/dev/null | grep -i shizuku",
                shell=True, capture_output=True, text=True, timeout=3
            )
            # Shizuku 服务进程名通常包含 "shizuku_server" 或 "moe.shizuku"
            if "shizuku" in r.stdout.lower():
                shizuku_running = True
        except Exception:
            pass
        
        # 也通过 dumpsys 检查 Shizuku 服务
        if not shizuku_running:
            try:
                r = subprocess.run(
                    "dumpsys package moe.shizuku.privileged.api 2>/dev/null | head -5",
                    shell=True, capture_output=True, text=True, timeout=3
                )
                if "moe.shizuku" in r.stdout:
                    shizuku_running = True
            except Exception:
                pass
        
        # rish 连接测试
        if shizuku_running and rish_path:
            try:
                # 用 rish 执行简单命令测试连接
                r = subprocess.run(
                    f"{rish_path} -c 'echo shizuku_ok'",
                    shell=True, capture_output=True, text=True, timeout=8
                )
                if r.returncode == 0 and "shizuku_ok" in r.stdout:
                    self.shizuku_available = True
                    self._shizuku_cmd = rish_path
                    logger.info(f"Shizuku 可用: {rish_path}")
                    return
                else:
                    logger.debug(f"rish 连接测试失败: {r.stderr}")
            except subprocess.TimeoutExpired:
                logger.debug("rish 连接超时，可能需要授权")
            except Exception as e:
                logger.debug(f"rish 测试异常: {e}")
        
        # 如果 rish 存在但连接失败，仍然标记为部分可用
        if rish_path and shizuku_running:
            self.shizuku_available = True
            self._shizuku_cmd = rish_path
            logger.info(f"Shizuku: rish 存在且服务运行中（可能需要授权）")
            return
        
        self.shizuku_available = False
        self._shizuku_cmd = rish_path or "rish"
        logger.info(f"Shizuku 不可用 (rish={rish_path}, running={shizuku_running})")

    def _check_adb_availability(self):
        try:
            result = subprocess.run(
                "adb devices", shell=True, capture_output=True,
                text=True, timeout=5
            )
            lines = result.stdout.strip().split('\n')
            self.adb_available = len(lines) > 1 and "device" in lines[1]
        except Exception:
            self.adb_available = False

    def change_workdir(self, new_path: str) -> bool:
        try:
            p = Path(new_path).expanduser().resolve()
            if p.exists() and p.is_dir():
                os.chdir(str(p))
                self.work_dir = p
                return True
            return False
        except Exception:
            return False

    def is_dangerous(self, command: str) -> bool:
        """检测危险命令（修复：精确匹配，避免误拦截）"""
        cmd_lower = command.lower()
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, cmd_lower):
                return True
        # 精确匹配危险关键词（修复 'dd' 误拦截 'add'，'> /dev/' 误拦截 stderr 重定向）
        dangerous_keywords = [
            'format',           # format 命令
            'fdisk',            # fdisk 命令
            'mkfs.',            # mkfs.* 格式化
            'rm -rf /',          # rm -rf 根目录
            'dd if=',           # dd 命令（仅匹配 dd if=，不匹配 add）
        ]
        return any(kw in cmd_lower for kw in dangerous_keywords)

    def is_blocked(self, command: str) -> bool:
        return any(blocked in command for blocked in BLOCKED_COMMANDS)

    def _build_root_command(self, command: str) -> str:
        su_cmd = getattr(self, "_su_cmd", "su")
        su_format = getattr(self, "_su_format", "-c")
        if su_format == "0":
            return f"{su_cmd} 0 sh -c {shlex.quote(command)}"
        else:
            return f"{su_cmd} -c {shlex.quote(command)}"

    def _check_dangerous_for_auth(self, command: str, use_root: bool, use_shizuku: bool) -> bool:
        if use_root or use_shizuku:
            extremely_dangerous = [
                r"mkfs\.", r"dd\s+if=.*of=/dev/",
                r"rm\s+-rf\s+/", r"shutdown", r"reboot"
            ]
            cmd_lower = command.lower()
            for pattern in extremely_dangerous:
                if re.search(pattern, cmd_lower):
                    return True
            return False
        return self.is_dangerous(command)

    def execute(
        self,
        command: str,
        require_auth: bool = True,
        use_root: bool = False,
        use_shizuku: bool = False,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        if self.is_blocked(command):
            return {"returncode": -1, "stdout": "", "stderr": "命令被安全策略拒绝", "success": False}

        if self._check_dangerous_for_auth(command, use_root, use_shizuku):
            if require_auth:
                return {"need_auth": True, "command": command, "message": "危险命令，需要授权"}
            return {"returncode": -1, "stdout": "", "stderr": "危险命令", "success": False}

        if timeout is None:
            timeout = 7200  # 2 小时安全网（AI 可通过 stop 提前终止）

        if use_root and self.root_available:
            full_cmd = self._build_root_command(command)
        elif use_root and not self.root_available:
            return {"returncode": -1, "stdout": "", "stderr": "Root 不可用，请检查设备是否已 root", "success": False}
        elif use_shizuku and self.shizuku_available:
            sh_cmd = getattr(self, "_shizuku_cmd", "rish")
            # rish 使用 -c 参数执行命令
            # 注意：rish 可能通过 app_process 启动，超时稍长
            if timeout is None:
                timeout = max(TOOL_CALL_TIMEOUT, 60)
            full_cmd = f"{sh_cmd} -c {shlex.quote(command)}"
        elif use_shizuku and not self.shizuku_available:
            return {"returncode": -1, "stdout": "", "stderr": "Shizuku 不可用，请检查 Shizuku 服务", "success": False}
        else:
            full_cmd = command

        cmd_stripped = full_cmd.strip()
        cmd_lower = cmd_stripped.lower()
        if cmd_lower.startswith("cd ") or cmd_lower.startswith("chdir "):
            parts = cmd_stripped.split(None, 1)
            if len(parts) > 1:
                new_dir = parts[1].strip().strip('"').strip("'")
                new_dir = os.path.expanduser(new_dir)
                try:
                    os.chdir(new_dir)
                    self.work_dir = Path(new_dir).resolve()
                    return {"returncode": 0, "stdout": "", "stderr": "", "success": True}
                except Exception as e:
                    return {"returncode": 1, "stdout": "", "stderr": str(e), "success": False}

        if IS_WINDOWS:
            return self._execute_windows(full_cmd, timeout)
        else:
            return self._execute_unix(full_cmd, timeout)

    def _execute_windows(self, command: str, timeout: int) -> Dict[str, Any]:
        result_holder = {"done": False, "returncode": None, "stdout": "", "stderr": ""}

        def target():
            proc = None
            try:
                creationflags = 0
                if hasattr(subprocess, 'CREATE_NO_WINDOW'):
                    creationflags |= subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, 'CREATE_NEW_PROCESS_GROUP'):
                    creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP

                proc = subprocess.Popen(
                    command, shell=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,           # ← 防止等待输入卡死
                    text=True, cwd=str(self.work_dir),
                    creationflags=creationflags
                )
                # 注册当前进程（供外部 stop_current 终止）
                with self._current_proc_lock:
                    self._current_proc = proc
                try:
                    try:
                        stdout, stderr = proc.communicate(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        # 多层终止策略（Windows 上 kill/terminate 不可靠）
                        self._kill_windows_process_tree(proc)
                        stdout, stderr = proc.communicate(timeout=5)
                        result_holder["returncode"] = -1
                        result_holder["stdout"] = stdout or ""
                        result_holder["stderr"] = (stderr or "") + f"\n[超时] 命令执行超过 {timeout} 秒"
                        result_holder["done"] = True
                        return
                finally:
                    with self._current_proc_lock:
                        if self._current_proc is proc:
                            self._current_proc = None
                result_holder["returncode"] = proc.returncode
                result_holder["stdout"] = stdout or ""
                result_holder["stderr"] = stderr or ""
                result_holder["done"] = True
            except Exception as e:
                result_holder["returncode"] = -1
                result_holder["stderr"] = str(e)
                result_holder["done"] = True
                if proc and proc.poll() is None:
                    try: self._kill_windows_process_tree(proc)
                    except Exception: pass
                with self._current_proc_lock:
                    if self._current_proc is proc:
                        self._current_proc = None

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(timeout + 10)  # 给 kill + communicate 留更多余量
        if not result_holder["done"]:
            return {"returncode": -1, "stdout": "", "stderr": f"命令执行超时（{timeout}秒）", "success": False}
        return {
            "returncode": result_holder["returncode"],
            "stdout": result_holder["stdout"],
            "stderr": result_holder["stderr"],
            "success": result_holder["returncode"] == 0
        }

    @staticmethod
    def _kill_windows_process_tree(proc):
        """Windows 上可靠终止进程树"""
        pid = proc.pid
        # 第一层：CTRL_BREAK_EVENT（需要 CREATE_NEW_PROCESS_GROUP）
        if hasattr(subprocess, 'CREATE_NEW_PROCESS_GROUP'):
            try:
                import signal
                os.kill(pid, signal.CTRL_BREAK_EVENT)
            except Exception:
                pass
        # 第二层：Popen 内置 terminate/kill
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            import time as _time
            _time.sleep(0.3)
            proc.kill()
        except Exception:
            pass
        # 第三层：taskkill 整棵树（异步，防止自身卡死）
        try:
            subprocess.run(
                f"taskkill /F /T /PID {pid}",
                shell=True, capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
        except Exception:
            pass

    def stop_current(self):
        """由外部调用，终止当前正在执行的命令并返回已捕获的部分输出"""
        with self._current_proc_lock:
            proc = self._current_proc
        if proc and proc.poll() is None:
            if IS_WINDOWS:
                self._kill_windows_process_tree(proc)
            else:
                try:
                    if IS_ANDROID:
                        os.killpg(os.getpgid(proc.pid), 9)
                    else:
                        os.killpg(os.getpgid(proc.pid), 9)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    def _execute_unix(self, command: str, timeout: int) -> Dict[str, Any]:
        env = os.environ.copy()
        if IS_ANDROID:
            if hasattr(self, "android_sdcard"):
                env["EXTERNAL_STORAGE"] = self.android_sdcard
                env["HOME"] = env.get("HOME", self.android_sdcard)
            env["PATH"] = env.get("PATH", "") + ":/system/bin:/system/xbin:/data/local/tmp"

        result_holder = {"done": False, "returncode": None, "stdout": "", "stderr": ""}

        def target():
            try:
                proc = subprocess.Popen(
                    command, shell=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,           # ← 防止等待输入卡死
                    text=True, cwd=str(self.work_dir),
                    env=env,
                    preexec_fn=os.setsid if not IS_ANDROID else os.setpgrp
                )
                # 注册当前进程（供外部 stop_current 终止）
                with self._current_proc_lock:
                    self._current_proc = proc
                try:
                    try:
                        stdout, stderr = proc.communicate(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                        stdout, stderr = proc.communicate(timeout=5)
                        result_holder["returncode"] = -1
                        result_holder["stdout"] = stdout or ""
                        result_holder["stderr"] = (stderr or "") + f"\n[超时] 命令执行超过 {timeout} 秒"
                        result_holder["done"] = True
                        return
                finally:
                    with self._current_proc_lock:
                        if self._current_proc is proc:
                            self._current_proc = None
                result_holder["returncode"] = proc.returncode
                result_holder["stdout"] = stdout or ""
                result_holder["stderr"] = stderr or ""
                result_holder["done"] = True
            except Exception as e:
                result_holder["returncode"] = -1
                result_holder["stderr"] = str(e)
                result_holder["done"] = True
                with self._current_proc_lock:
                    if self._current_proc is proc:
                        self._current_proc = None

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(timeout + 5)
        if not result_holder["done"]:
            return {"returncode": -1, "stdout": "", "stderr": f"命令执行超时（{timeout}秒）", "success": False}
        return {
            "returncode": result_holder["returncode"],
            "stdout": result_holder["stdout"],
            "stderr": result_holder["stderr"],
            "success": result_holder["returncode"] == 0
        }
