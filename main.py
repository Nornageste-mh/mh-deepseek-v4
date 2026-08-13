#!/usr/bin/env python3
"""
MH-DeepSeek Agent V4 — 主入口（跨平台：Android/Linux/Windows）
自动安装依赖 → 初始化密钥 → 初始化聊天桥接 → 启动 Web
"""
import logging
import sys
import platform

from config import WEB_HOST, WEB_PORT, IS_WINDOWS, IS_ANDROID
from key_manager import KeyManager
import web

# ── 终端颜色 ──
GREEN  = "\033[32m"
BLUE   = "\033[34m"
BOLD   = "\033[1m"
RED    = "\033[31m"
RESET  = "\033[0m"

if IS_WINDOWS:
    try:
        import colorama
        colorama.init()
    except ImportError:
        GREEN = BLUE = BOLD = RESET = ""

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("MHAgent.Main")


def _prompt_api_keys(keys_so_far: dict) -> dict:
    """提示输入 API Key，合并到现有 keys 中返回"""
    print("\n🔑 API Keys 配置（直接回车跳过/保留现有值）：")
    current_deepseek = keys_so_far.get("DEEPSEEK_API_KEY", "")
    current_bocha = keys_so_far.get("BOCHA_SEARCH_API_KEY", "")
    deepseek_key = input(f"DeepSeek API Key [当前: {'已设置' if current_deepseek else '空'}]: ").strip()
    bocha_key = input(f"博查搜索 API Key [当前: {'已设置' if current_bocha else '空'}]: ").strip()
    if deepseek_key:
        keys_so_far["DEEPSEEK_API_KEY"] = deepseek_key
    if bocha_key:
        keys_so_far["BOCHA_SEARCH_API_KEY"] = bocha_key
    return keys_so_far


def initialize_keys():
    """初始化密钥管理器"""
    km = KeyManager()
    if not km.is_initialized():
        logger.info("首次运行，生成密钥分片...")
        shares = km.initialize()
        logger.info(f"已生成 {len(shares)} 个分片，存储在 {km.shard_dir}")
        print("\n🔐 首次设置 - 请输入 API Keys（直接回车跳过）：")
        deepseek_key = input("DeepSeek API Key: ").strip()
        bocha_key = input("博查搜索 API Key: ").strip()
        keys = {}
        if deepseek_key:
            keys["DEEPSEEK_API_KEY"] = deepseek_key
        if bocha_key:
            keys["BOCHA_SEARCH_API_KEY"] = bocha_key
        if keys:
            km.save_api_keys(keys)
            logger.info("API Keys 已加密保存")
        else:
            logger.warning("未输入任何 API Key")

    else:
        if km.unlock():
            logger.info("密钥已解锁")
        else:
            logger.error("密钥解锁失败，请检查分片完整性")
            sys.exit(1)

        existing = km.get_api_keys()
        need_update = False

        # 检查 API Key 是否缺失
        if not existing.get("DEEPSEEK_API_KEY") or not existing.get("BOCHA_SEARCH_API_KEY"):
            print("\n🔑 检测到 API Key 缺失，请补充：")
            updated = _prompt_api_keys(existing)
            if updated != existing:
                existing = updated
                need_update = True

        if need_update:
            km.save_api_keys(existing)
            logger.info("凭据已补充保存")

    api_keys = km.get_api_keys()
    import config
    config.DEEPSEEK_API_KEY = api_keys.get("DEEPSEEK_API_KEY", "")
    config.BOCHA_SEARCH_API_KEY = api_keys.get("BOCHA_SEARCH_API_KEY", "")

    return km


def start_web():
    """启动 Flask Web 服务"""
    print(f"\n{BOLD}{BLUE}🌐 启动 MH-DeepSeek Agent V4{RESET}")
    print(f"   模型: deepseek-v4-flash (快速) / deepseek-v4-pro (专家)")
    print(f"   搜索: 博查搜索 API + DuckDuckGo 降级")
    if IS_ANDROID:
        print(f"   平台: Android (Termux)")
    elif IS_WINDOWS:
        print(f"   平台: Windows")
    else:
        print(f"   平台: Linux")
    print(f"   访问地址: http://{WEB_HOST}:{WEB_PORT}")
    # 模型提供商状态
    pm = getattr(web, '_provider_mgr', None)
    if pm and pm.providers:
        for pid, p in pm.providers.items():
            think = "✓思考" if p.supports_thinking else "✗思考"
            print(f"   📡 {p.name}: {len(p.models)} 个模型 ({think})")
    web.app.run(host=WEB_HOST, port=WEB_PORT, debug=False, threaded=True)


# ── 程序入口 ─────────────────────────────────
if __name__ == "__main__":
    # 1. 依赖检查（用户需先运行 install.sh / install.ps1）
    try:
        import flask
    except ImportError:
        print(f"\n{RED}依赖未安装！请先运行安装脚本：{RESET}")
        if IS_WINDOWS:
            print(f"  PowerShell: .\\install.ps1")
        else:
            print(f"  bash install.sh")
        sys.exit(1)

    # 2. 初始化密钥
    key_manager = initialize_keys()
    web.key_manager = key_manager

    # 3. 启动 Web 服务
    start_web()
