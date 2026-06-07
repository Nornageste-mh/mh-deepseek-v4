"""
MH-DeepSeek Agent V4 全局配置（跨平台：Android/Linux/Windows）
修复：区分正常 root/su 命令和真正的危险操作
"""
import os
import sys
import platform
from pathlib import Path

# ==================== 平台检测 ====================
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform == "linux"
IS_ANDROID = sys.platform == "android" or (IS_LINUX and "ANDROID_ROOT" in os.environ)
IS_MAC = sys.platform == "darwin"

# ==================== DeepSeek API 配置 ====================
DEEPSEEK_API_KEY = ""
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_BETA_URL = "https://api.deepseek.com/beta/chat/completions"

MODEL_FAST = "deepseek-v4-flash"
MODEL_EXPERT = "deepseek-v4-pro"
DEFAULT_REASONING_EFFORT = "high"
EXPERT_REASONING_EFFORT = "max"

# ==================== API 定价 (CNY / 1M tokens, 2026-05) ====================
# 来源: https://api-docs.deepseek.com/zh-cn/quick_start/pricing
# deepseek-v4-pro 当前 2.5 折优惠至 2026/05/31
# 缓存命中价格已降至首发价 1/10
# 输入缓存命中折扣: flash 50x, pro 120x (正常价) / 30x (2.5折期)
API_PRICING = {
    MODEL_FAST:  {"input_cache_miss": 1.0,  "input_cache_hit": 0.02, "output": 2.0},
    MODEL_EXPERT: {"input_cache_miss": 12.0, "input_cache_hit": 0.1,  "output": 24.0},
    # 2.5 折优惠期实际价格
    f"{MODEL_EXPERT}_discount": {"input_cache_miss": 3.0, "input_cache_hit": 0.025, "output": 6.0},
}

# ==================== 输出 Token  ====================
# 模型最大输出 384K，但无限制会造成巨额费用
# 每次 API 调用的 max_tokens 上限
DEFAULT_MAX_OUTPUT_TOKENS = 16384     # 普通对话
THINKING_MAX_OUTPUT_TOKENS = 32768    # 思考模式（需要更多输出空间）

# ==================== 模型提供商配置 ====================
# 支持的提供商：deepseek / meiju
# 每个 provider 管理自己的模型列表

# 默认提供商和模型
DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-v4-flash"

# 妹居DeepSeek 配置（弱密码登录，仅用于测试）
MEIJU_PHONE = "17600000001"
MEIJU_PASSWORD = "12345678"

# ==================== 博查搜索 API ====================
BOCHA_SEARCH_API_KEY = ""
BOCHA_SEARCH_URL = "https://api.bochaai.com/v1/web-search"

# ==================== 搜索降级配置 ====================
SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://search.bus-hit.me",
    "https://searx.tuxcloud.net",
]

# ==================== 安全设置 ====================
ALLOWED_COMMANDS = ["ls", "pwd", "cd", "mkdir", "rm", "cp", "mv", "cat", "head", "tail",
                    "grep", "find", "zip", "unzip", "tar", "python", "pip", "adb",
                    "termux-", "apt", "pkg", "wget", "curl", "git", "su", "shizuku",
                    "baksmali", "smali", "apktool", "jadx", "dex2jar", "echo",
                    "id", "whoami", "pm", "am", "input", "screencap", "screenrecord",
                    "dumpsys", "settings", "content", "cmd"]
if IS_WINDOWS:
    ALLOWED_COMMANDS += ["cmd", "powershell", "where", "dir", "type", "findstr",
                         "tasklist", "taskkill", "wmic", "powercfg", "start",
                         "msedge", "chrome", "firefox", "notepad", "explorer",
                         "pyautogui", "paddleocr", "pytesseract"]

# 只拦截真正危险的操作（格式化、擦除分区、dd 写入设备等）
BLOCKED_COMMANDS = [
    "rm -rf /",      # 删除根目录
    "mkfs",           # 格式化文件系统
    "passwd",         # 修改密码
    "shutdown",       # 关机
    "reboot",         # 重启
]

# DANGEROUS_PATTERNS：只拦截真正的危险操作
# 注意：不再拦截 "su -" 开头的命令，因为 execute_root 需要它
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",          # rm -rf /
    r"dd\s+if=.*\s+of=/dev/", # dd 写入设备（不是 dd if= 读取）
    r"mkfs\.",                # mkfs.*
    r">\s*/dev/(?!null)",     # 重定向到设备（排除 /dev/null）
    r"chmod\s+777\s+/",       # chmod 777 根路径
    r"shutdown",              # 关机
    r"reboot",                # 重启
    r"kill\s+-9\s+1\b",       # kill -9 1 (init)
    r"pkill\s+init",          # pkill init
    r"killall\s+init",        # killall init
]

# ==================== Web 服务 ====================
WEB_HOST = "0.0.0.0"
WEB_PORT = 9090
SECRET_KEY = "mh-deepseek-secret-key"

# ==================== Agent 运行时 ====================
MAX_ITERATIONS = 1024
TOOL_CALL_TIMEOUT = 512
MEMORY_DIR = "./agent_memories"
MAX_MEMORY_TURNS = 2048
MAX_TOOL_OUTPUT_CHARS = 8000
MAX_HISTORY_TOKENS_EST = 120000

# ==================== 输入 Token  ====================
# 每次 API 调用允许的最大输入 token 数
# 超出预算时自动截断旧消息、修剪工具输出
MAX_INPUT_TOKENS_PER_CALL = 800000
# 保留的最近 N 条消息不截断（滑动窗口大小）
PROTECT_LAST_N_MESSAGES = 12

# ==================== 上下文压缩配置 ====================
# 触发压缩的 token 阈值比例（占模型上下文窗口的比例）
COMPRESSION_THRESHOLD_PERCENT = 0.40
# 保护的头部消息数（system prompt + 首轮交换）
COMPRESSION_PROTECT_HEAD = 2
# 尾部 token 预算（保护最近的 ~16K tokens）
COMPRESSION_TAIL_TOKEN_BUDGET = 12000
# 工具输出修剪：保护最近的 N 条工具结果不被修剪
COMPRESSION_PROTECT_TOOL_TAIL = 8
# 摘要最大 tokens
COMPRESSION_MAX_SUMMARY_TOKENS = 8000

# ==================== 网络防灾 ====================
RETRY_MAX_ATTEMPTS = 5
RETRY_MIN_WAIT = 1
RETRY_MAX_WAIT = 30
API_CONSECUTIVE_FAIL_THRESHOLD = 3

# ==================== 密钥加密配置 ====================
SHARD_N = 5
SHARD_K = 3
SHARD_DIR = str(Path.home() / ".config" / "mh-agent" / "keys")
ENCRYPTED_CONFIG_FILE = str(Path.home() / ".config" / "mh-agent" / "config.enc")
KEY_LOCK_TIMEOUT = 1800

# ==================== 可选依赖标记 ====================
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    from PIL import Image
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

try:
    import UnityPy
    UNITY_AVAILABLE = True
except ImportError:
    UNITY_AVAILABLE = False

if IS_WINDOWS:
    try:
        import pyautogui
        PYAUTOGUI_AVAILABLE = True
    except ImportError:
        PYAUTOGUI_AVAILABLE = False
    try:
        import pytesseract
        TESSERACT_AVAILABLE = True
    except ImportError:
        TESSERACT_AVAILABLE = False
    try:
        from paddleocr import PaddleOCR
        PADDLE_OCR_AVAILABLE = True
    except ImportError:
        PADDLE_OCR_AVAILABLE = False
else:
    PYAUTOGUI_AVAILABLE = False
    TESSERACT_AVAILABLE = False
    PADDLE_OCR_AVAILABLE = False



# ==================== 长期记忆配置 ====================
LONG_TERM_MEMORY_DIR = str(Path(MEMORY_DIR) / "long_term")

# ==================== SMTP 邮件发送配置（运行时从密钥管理器注入） ====================
SMTP_SERVER = ""        # 运行时由 main.py 从密钥管理器注入
SMTP_PORT = 587
SMTP_USER = ""
SMTP_PASSWORD = ""
