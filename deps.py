"""
自动检查并安装 Python 依赖包（跨平台：Android/Linux/Windows）
支持网络安全、Windows自动化、本地模型等扩展功能
"""
import sys
import subprocess
import importlib
import logging
import platform
from pathlib import Path
from typing import List, Tuple

# ── 终端颜色 ──────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RED    = "\033[31m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
BLUE   = "\033[34m"
CYAN   = "\033[36m"

# Windows 终端 ANSI 颜色支持
IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    try:
        import colorama
        colorama.init()
    except ImportError:
        pass

logger = logging.getLogger("MHAgent.Deps")

# ── 核心依赖（必须安装） ──────────────────────
REQUIRED_PACKAGES = [
    "flask",
    "flask-cors",
    "requests",
    "pycryptodome",
    "tenacity",
    "beautifulsoup4",
    "psutil",
]

# ── 可选依赖（推荐安装，缺失不影响核心功能） ──
OPTIONAL_PACKAGES = [
    "Pillow",
    "soundfile",
    "colorama",
]

# ── 网络安全可选依赖 ──────────────────────────
SECURITY_PACKAGES = [
    "dnspython",
    "cryptography",
]

# ── 平台特定可选依赖 ──────────────────────────
if IS_WINDOWS:
    OPTIONAL_PACKAGES.append("pyautogui")

PIP_TIMEOUT = 300


def colored(msg: str, color: str = CYAN, bold: bool = False) -> str:
    return f"{BOLD if bold else ''}{color}{msg}{RESET}"


def install_package(package: str, timeout: int = PIP_TIMEOUT) -> bool:
    print(f"  {CYAN}⏳ 正在安装 {package} ...{RESET}", end=" ", flush=True)
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "--no-input", package],
            timeout=timeout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"{GREEN}✓ 成功{RESET}")
        return True
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}⏱ 超时{RESET}")
    except Exception:
        print(f"{RED}✗ 失败{RESET}")
    return False


def install_system_package(pkg_name: str) -> bool:
    """通过系统包管理器安装（仅 Linux/Termux/macOS）"""
    if IS_WINDOWS:
        return False
    for manager in ["pkg", "apt", "brew", "pacman"]:
        try:
            if manager == "pkg":
                subprocess.check_call(
                    ["pkg", "install", "-y", pkg_name],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
                )
            elif manager == "apt":
                subprocess.check_call(
                    ["sudo", "apt-get", "install", "-y", "-qq", pkg_name],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
                )
            elif manager == "brew":
                subprocess.check_call(
                    ["brew", "install", pkg_name],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
                )
            elif manager == "pacman":
                subprocess.check_call(
                    ["sudo", "pacman", "-S", "--noconfirm", pkg_name],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
                )
            return True
        except:
            continue
    return False


def _detect_w64devkit() -> bool:
    """检测 Windows 上的 w64devkit 编译环境"""
    if not IS_WINDOWS:
        return False
    w64_path = Path("D:/w64DevKit/w64devkit/bin/gcc.exe")
    return w64_path.exists()


def ensure_dependencies(packages: List[str], optional: bool = False) -> Tuple[List[str], List[str]]:
    """检查并安装依赖，返回 (成功列表, 失败列表)"""
    succeeded, failed = [], []
    for pkg in packages:
        try:
            importlib.import_module(pkg.replace("-", "_").replace(".", ""))
            succeeded.append(pkg)
            continue
        except ImportError:
            pass
        if install_package(pkg):
            succeeded.append(pkg)
        else:
            failed.append(pkg)
    return succeeded, failed


def auto_install_startup():
    """启动时自动安装依赖"""
    print(f"\n{BOLD}{BLUE}🔧 正在检查依赖环境...{RESET}\n")

    # ── 1. 系统级包 ──
    if not IS_WINDOWS:
        try:
            import soundfile
            print(f"  {GREEN}✓ soundfile 已就绪{RESET}")
        except ImportError:
            print(f"  {YELLOW}⚠ soundfile 缺失，尝试安装系统依赖...{RESET}")
            if install_system_package("libsndfile1") or \
               install_system_package("libsndfile") or \
               install_system_package("libsndfile-dev"):
                print(f"  {GREEN}✓ libsndfile 系统库已安装{RESET}")
            else:
                print(f"  {DIM}  libsndfile 自动安装失败，soundfile 可能不可用{RESET}")

        try:
            import psutil
            print(f"  {GREEN}✓ psutil 已就绪{RESET}")
        except ImportError:
            print(f"  {YELLOW}⚠ psutil 缺失，尝试通过系统包管理器安装...{RESET}")
            if install_system_package("python3-psutil") or install_system_package("python-psutil"):
                print(f"  {GREEN}✓ psutil 已通过系统包安装{RESET}")
            else:
                print(f"  {YELLOW}⚠ psutil 自动安装失败，尝试 pip...{RESET}")
                if install_package("psutil"):
                    print(f"  {GREEN}✓ psutil 已通过 pip 安装{RESET}")
                else:
                    print(f"  {YELLOW}⚠ psutil 安装失败，CPU/内存监控将不可用{RESET}")
    else:
        try:
            import psutil
            print(f"  {GREEN}✓ psutil 已就绪{RESET}")
        except ImportError:
            if install_package("psutil"):
                print(f"  {GREEN}✓ psutil 已安装{RESET}")
            else:
                print(f"  {YELLOW}⚠ psutil 安装失败，CPU/内存监控将受限{RESET}")

    # ── 2. 核心 pip 包 ──
    _, fail_core = ensure_dependencies(REQUIRED_PACKAGES, optional=False)
    if fail_core:
        print(f"  {RED}✗ 核心依赖安装失败: {', '.join(fail_core)}，程序可能无法启动{RESET}")
    else:
        print(f"  {GREEN}✓ 核心依赖全部就绪{RESET}")

    # ── 3. 可选 pip 包 ──
    installed_opt, _ = ensure_dependencies(OPTIONAL_PACKAGES, optional=True)
    if installed_opt:
        print(f"  {GREEN}✓ 可选依赖已安装: {', '.join(installed_opt)}{RESET}")

    # ── 4. 网络安全依赖 ──
    print(f"  {BLUE}📡 网络安全工具依赖...{RESET}")
    installed_sec, failed_sec = ensure_dependencies(SECURITY_PACKAGES, optional=True)
    if installed_sec:
        print(f"  {GREEN}✓ 网络安全增强已安装: {', '.join(installed_sec)}{RESET}")
    if failed_sec:
        for pkg in failed_sec:
            print(f"  {YELLOW}⚠ {pkg} 未安装（网络安全部分功能受限）{RESET}")
            print(f"    安装: pip install {pkg}")

    # ── 5. Windows 自动化依赖提示 ──
    if IS_WINDOWS:
        try:
            import pyautogui
            print(f"  {GREEN}✓ pyautogui (屏幕/鼠标/键盘控制) 已就绪{RESET}")
        except ImportError:
            print(f"  {YELLOW}⚠ pyautogui 未安装，屏幕/鼠标/键盘控制不可用{RESET}")
            print(f"    安装: pip install pyautogui")

        try:
            import pytesseract
            print(f"  {GREEN}✓ pytesseract (OCR) 已就绪{RESET}")
        except ImportError:
            print(f"  {YELLOW}⚠ pytesseract 未安装，OCR 文字识别不可用{RESET}")

        try:
            from paddleocr import PaddleOCR
            print(f"  {GREEN}✓ PaddleOCR (中文OCR) 已就绪{RESET}")
        except ImportError:
            print(f"  {DIM}  PaddleOCR 未安装 (可选，用于中文OCR){RESET}")

    print(f"\n{BOLD}{BLUE}✅ 依赖检查完成{RESET}\n")