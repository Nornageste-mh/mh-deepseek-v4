"""文件操作工具（跨平台：Windows/Linux/Android）"""
import os
import sys
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

from config import MAX_TOOL_OUTPUT_CHARS

IS_WINDOWS = sys.platform == "win32"


def register_tools(registry):
    executor = registry.executor

    registry.register("read_file", _read_file, "读取文件内容", {"file_path": {"type": "string"}})
    registry.register("write_file", _write_file, "写入文件内容",
                      {"file_path": {"type": "string"}, "content": {"type": "string"}})
    registry.register("list_directory",
                      lambda **kw: _list_dir(executor, kw.get("path", None)),
                      "列出目录内容", {"path": {"type": "string"}})
    registry.register("copy_file", _copy_file, "复制文件或目录",
                      {"src": {"type": "string"}, "dst": {"type": "string"}})
    registry.register("move_file", _move_file, "移动文件或目录",
                      {"src": {"type": "string"}, "dst": {"type": "string"}})
    registry.register("find_files", _find_files, "按名称或模式查找文件",
                      {"directory": {"type": "string"}, "pattern": {"type": "string"}})
    registry.register("grep_files", _grep_files, "在文件中搜索文本内容",
                      {"directory": {"type": "string"}, "pattern": {"type": "string"}})
    registry.register("unzip_archive", _unzip, "解压zip文件",
                      {"zip_path": {"type": "string"}, "extract_to": {"type": "string"}})
    registry.register("create_zip", _create_zip, "创建zip压缩包",
                      {"source": {"type": "string"}, "output": {"type": "string"}})
    registry.register("get_file_info", _file_info, "获取文件详细信息", {"file_path": {"type": "string"}})
    registry.register("change_directory", 
                      lambda **kw: _change_dir(executor, kw.get("path", "")),
                      "更改工作目录", {"path": {"type": "string"}})
    registry.register("get_work_dir",
                      lambda **kw: str(executor.work_dir),
                      "获取当前工作目录", {})


def _read_file(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        return f"文件不存在: {file_path}"
    try:
        content = path.read_text(encoding='utf-8', errors='ignore')
        if len(content) > MAX_TOOL_OUTPUT_CHARS:
            content = content[:MAX_TOOL_OUTPUT_CHARS] + "...(已截断)"
        return content
    except Exception as e:
        return f"读取失败: {e}"


def _write_file(file_path: str, content: str) -> str:
    try:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return f"成功写入 {file_path} ({len(content)} 字符)"
    except Exception as e:
        return f"写入失败: {e}"


def _list_dir(executor, path: str = None) -> str:
    target = Path(path) if path else executor.work_dir
    if not target.exists():
        return f"路径不存在: {target}"
    try:
        items = list(target.iterdir())
        output_lines = []
        for x in items[:100]:
            icon = '📁' if x.is_dir() else '📄'
            size = ""
            if x.is_file():
                size = f" ({x.stat().st_size} 字节)"
            output_lines.append(f"{icon} {x.name}{size}")
        if len(items) > 100:
            output_lines.append(f"... 共 {len(items)} 项")
        return "\n".join(output_lines)
    except Exception as e:
        return f"列出失败: {e}"


def _copy_file(src: str, dst: str) -> str:
    try:
        shutil.copy2(src, dst)
        return f"成功复制 {src} -> {dst}"
    except Exception as e:
        return f"复制失败: {e}"


def _move_file(src: str, dst: str) -> str:
    try:
        shutil.move(src, dst)
        return f"成功移动 {src} -> {dst}"
    except Exception as e:
        return f"移动失败: {e}"


def _find_files(directory: str, pattern: str) -> str:
    """跨平台文件查找"""
    try:
        if IS_WINDOWS:
            # Windows 使用 dir /s
            cmd = f'dir /s /b "{directory}\\{pattern}" 2>nul'
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            lines = [l.strip() for l in r.stdout.split('\n') if l.strip()]
            if lines:
                result = "\n".join(lines[:50])
                if len(lines) > 50:
                    result += f"\n... 共 {len(lines)} 个匹配"
                return result
            return "未找到匹配文件"
        else:
            # Linux/Android 使用 find
            r = subprocess.run(
                f"find '{directory}' -name '{pattern}' 2>/dev/null | head -50",
                shell=True, capture_output=True, text=True, timeout=30
            )
            return r.stdout if r.stdout else "未找到匹配文件"
    except Exception as e:
        return f"查找失败: {e}"


def _grep_files(directory: str, pattern: str) -> str:
    """跨平台文件内容搜索"""
    try:
        if IS_WINDOWS:
            # Windows 使用 findstr
            cmd = f'findstr /s /n /i "{pattern}" "{directory}\\*" 2>nul'
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            lines = [l.strip() for l in r.stdout.split('\n') if l.strip()]
            if lines:
                result = "\n".join(lines[:20])
                if len(lines) > 20:
                    result += f"\n... 共 {len(lines)} 个匹配"
                return result
            return "未找到匹配内容"
        else:
            # Linux/Android 使用 grep
            r = subprocess.run(
                f"grep -r --include='*' '{pattern}' '{directory}' 2>/dev/null | head -20",
                shell=True, capture_output=True, text=True, timeout=30
            )
            return r.stdout if r.stdout else "未找到匹配内容"
    except Exception as e:
        return f"搜索失败: {e}"


def _unzip(zip_path: str, extract_to: str) -> str:
    try:
        Path(extract_to).mkdir(parents=True, exist_ok=True)
        shutil.unpack_archive(zip_path, extract_to, 'zip')
        return f"解压成功: {extract_to}"
    except Exception as e:
        # 降级：使用命令行的 unzip
        try:
            if IS_WINDOWS:
                cmd = f'powershell "Expand-Archive -Path \'{zip_path}\' -DestinationPath \'{extract_to}\' -Force"'
            else:
                cmd = f"unzip -o '{zip_path}' -d '{extract_to}'"
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                return f"解压成功: {extract_to}"
            return f"解压失败: {r.stderr[:200]}"
        except Exception as e2:
            return f"解压失败: {e} | 降级也失败: {e2}"


def _create_zip(source: str, output: str) -> str:
    try:
        shutil.make_archive(output.replace('.zip', ''), 'zip', source)
        return f"压缩成功: {output}"
    except Exception as e:
        return f"压缩失败: {e}"


def _file_info(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        return "文件不存在"
    stat = path.stat()
    return (f"大小: {stat.st_size} 字节\n"
            f"修改时间: {datetime.fromtimestamp(stat.st_mtime)}\n"
            f"权限: {oct(stat.st_mode)[-3:] if not IS_WINDOWS else 'N/A (Windows)'}\n"
            f"是否为目录: {path.is_dir()}")


def _change_dir(executor, path: str) -> str:
    if executor.change_workdir(path):
        return f"工作目录已更改为: {executor.work_dir}"
    return f"目录不存在或无效: {path}"
