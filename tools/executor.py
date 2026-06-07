# tools/executor.py
"""基础命令执行工具（改进：传递 use_root/use_shizuku 到 executor）"""
from executor import SecureExecutor


def register_tools(registry):
    executor = registry.executor

    registry.register(
        "execute_command",
        lambda **kwargs: _execute(executor, kwargs.get("command", ""), False, False),
        "执行Shell命令（安全限制）",
        {"command": {"type": "string"}}
    )
    registry.register(
        "execute_root",
        lambda **kwargs: _execute(executor, kwargs.get("command", ""), True, False),
        "以 root 权限执行命令（需要设备已 root）",
        {"command": {"type": "string"}}
    )
    registry.register(
        "execute_shizuku",
        lambda **kwargs: _execute(executor, kwargs.get("command", ""), False, True),
        "通过 Shizuku 执行高权限命令（需要 Shizuku 服务运行）",
        {"command": {"type": "string"}}
    )


def _execute(executor: SecureExecutor, command: str, use_root: bool, use_shizuku: bool):
    """执行命令并返回格式化的结果"""
    if not command:
        return "错误：命令不能为空"
    r = executor.execute(command, require_auth=True, use_root=use_root, use_shizuku=use_shizuku)
    if r.get("need_auth"):
        return r
    return f"返回码 {r['returncode']}\n{r['stdout']}\n{r['stderr']}"
