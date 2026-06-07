"""
工具模块 — MCP 可插拔架构
精简为核心工具集：文件操作 / 终端执行 / 网页搜索 / 网页浏览 / 网络安全
支持优雅降级：缺失的子模块不会导致系统崩溃
"""
import sys
import logging
import importlib

logger = logging.getLogger("MHAgent.Tools")

# ── 全局注册计数：防止多次调用刷屏 ──
_discover_call_count = 0

# 核心工具模块（精简后）
TOOL_MODULES = [
    'tools.filesystem',         # 文件操作（读写/查找/压缩）
    'tools.executor',           # 终端命令执行（Shell/root/Shizuku）
    'tools.search',             # 网页搜索（博查API → DDG → Bing 降级）
    'tools.browse_web',         # 浏览网页（基于 Scrapling，隐身模式+自适应）
    'tools.network_security',   # 网络安全工具（DNS/SSL/端口扫描等 20+ 工具）
]


def discover_and_register(registry):
    """
    自动发现并注册所有工具模块。
    缺失的模块会被跳过并记录警告，不影响整体启动。
    多次调用时只打印简要日志，避免刷屏。
    """
    global _discover_call_count
    _discover_call_count += 1
    registered_count = 0
    failed_modules = []

    for module_name in TOOL_MODULES:
        try:
            mod = importlib.import_module(module_name)
            if hasattr(mod, 'register_tools'):
                mod.register_tools(registry)
                registered_count += 1
                if _discover_call_count <= 1:
                    logger.info(f"  ✓ {module_name}")
            else:
                if _discover_call_count <= 1:
                    logger.warning(f"  ✗ {module_name} 缺少 register_tools")
                failed_modules.append(module_name)
        except ImportError as e:
            if _discover_call_count <= 1:
                logger.warning(f"  ✗ {module_name} 未安装: {e}")
            failed_modules.append(module_name)
        except Exception as e:
            if _discover_call_count <= 1:
                logger.error(f"  ✗ {module_name} 加载失败: {e}")
            failed_modules.append(module_name)

    if _discover_call_count == 1:
        logger.info(f"MCP 工具加载完成: {registered_count}/{len(TOOL_MODULES)} 个模块成功")
        if failed_modules:
            logger.warning(f"缺失模块（系统仍可运行）: {', '.join(failed_modules)}")
    else:
        logger.debug(f"MCP 工具重新注册 (#{_discover_call_count}): {registered_count}/{len(TOOL_MODULES)} 成功")
