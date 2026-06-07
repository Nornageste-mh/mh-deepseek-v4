# skills.py (修改 - 删除 ocr_screen 技能)
"""
技能管理器 - 已移除 ocr_screen 技能
"""
import re
import json
from typing import Dict

from tool_registry import ToolRegistry


class SkillManager:
    def __init__(self, tool_registry: ToolRegistry):
        self.skills = {}
        self.tools = tool_registry
        self._register_skills()

    def _register_skills(self):
        self.skills["analyze_android"] = {
            "description": "分析Android APK基本信息",
            "workflow": [
                ("execute_command", "file {apk_path}"),
                ("execute_command", "unzip -l {apk_path} | head -30")
            ]
        }
        self.skills["clean_temp"] = {
            "description": "清理临时文件",
            "workflow": [("execute_command", "rm -rf /tmp/*_tmp")]
        }
        self.skills["backup_folder"] = {
            "description": "备份文件夹到当前目录",
            "workflow": [("execute_command", "cp -r {folder} {folder}_backup")]
        }
        self.skills["edit_dex"] = {
            "description": "编辑DEX文件：反编译为smali，提示修改后重新打包",
            "workflow": [
                ("execute_command", "baksmali d {dex_path} -o {output_dir}"),
                ("ask_ai", "DEX反编译完成，smali代码已输出到 {output_dir}。请手动修改需要的smali文件，完成后回复'继续'以重新打包为DEX。"),
                ("execute_command", "smali a {output_dir} -o {output_dex}")
            ]
        }
        self.skills["apk_tool"] = {
            "description": "使用apktool解包APK，修改后重打包",
            "workflow": [
                ("execute_command", "apktool d {apk_path} -o {output_dir}"),
                ("ask_ai", "APK解包完成，文件已输出到 {output_dir}。请手动修改资源或smali代码，完成后回复'继续'以重新打包APK。"),
                ("execute_command", "apktool b {output_dir} -o {output_apk}")
            ]
        }
        self.skills["dex_info"] = {
            "description": "查看DEX文件信息（类、方法数等）",
            "workflow": [
                ("execute_command", "dexdump -f {dex_path} | head -50")
            ]
        }
        self.skills["browse_web"] = {
            "description": "模拟浏览器访问网页，获取内容并提取关键信息",
            "workflow": [
                ("fetch_webpage", "url={url} method=GET"),
                ("ask_ai", "已获取网页内容。请分析并提取用户关心的信息，或根据需要进一步操作（例如跟随链接、提交表单）。")
            ]
        }
        # 注意：已移除 ocr_screen 技能
        self.skills["smart_download"] = {
            "description": "智能下载文件：依次尝试 requests、curl、wget，直到成功",
            "workflow": [
                ("download_file", "url={url} output={output}"),
                ("execute_command", "curl -L -H 'User-Agent: Mozilla/5.0' '{url}' -o '{output}'"),
                ("execute_command", "wget -U 'Mozilla/5.0' '{url}' -O '{output}'")
            ]
        }
        self.skills["download_bilibili_video"] = {
            "description": "下载B站视频：自动获取最新播放URL，并使用curl带完整浏览器headers下载。",
            "workflow": [
                ("fetch_webpage", "url=https://api.bilibili.com/x/player/playurl?avid={avid}&bvid={bvid}&cid={cid}&qn=80&fnval=1&fnver=0&fourk=1 method=GET"),
                ("ask_ai", "从上一步fetch_webpage返回的JSON中提取data.durl[0].url字段，并对URL中的unicode转义进行处理（如\\u0026转为&）。然后构造curl命令..."),
                ("execute_command", "{curl_command}")
            ]
        }
        self.skills["batch_download_bilibili"] = {
            "description": "批量下载B站视频（基于yt-dlp），自动处理ffmpeg安装和音视频合并。",
            "workflow": [
                ("execute_command", "mkdir -p {output_dir}"),
                ("execute_command", "command -v ffmpeg >/dev/null 2>&1 || pkg install -y ffmpeg"),
                ("ask_ai", "请将BV号列表'{bvids}'转换为逐个下载的命令序列..."),
                ("execute_command", "{download_commands}"),
                ("execute_command", "find {output_dir} -type f -name '*.part' -o -name '*.ytdl' -delete 2>/dev/null"),
                ("list_directory", "path={output_dir}")
            ]
        }

    # execute_skill 和 get_skills_description 方法保持不变
    def execute_skill(self, skill_name: str, params: Dict = None) -> str:
        if skill_name not in self.skills:
            return f"未知技能: {skill_name}"
        skill = self.skills[skill_name]
        output = f"执行技能: {skill_name}\n"
        params = params or {}
        last_ai_response = ""
        for tool_name, cmd_template in skill["workflow"]:
            current_cmd = cmd_template
            for k, v in params.items():
                current_cmd = current_cmd.replace(f"{{{k}}}", str(v))
            if "{curl_command}" in current_cmd and last_ai_response:
                current_cmd = last_ai_response.strip()
            if "{download_commands}" in current_cmd and last_ai_response:
                current_cmd = last_ai_response.strip()

            if tool_name == "ask_ai":
                result = self.tools.call(tool_name, {"prompt": current_cmd})
                last_ai_response = result
            elif tool_name in ["fetch_webpage", "ocr_image"]:
                arg_dict = {}
                for match in re.finditer(r'(\w+)=("[^"]*"|\'[^\']*\'|\S+)', current_cmd):
                    key = match.group(1)
                    val = match.group(2).strip('"\'')
                    arg_dict[key] = val
                result = self.tools.call(tool_name, arg_dict)
            elif tool_name == "device_control":
                if "action=" in current_cmd:
                    action_match = re.search(r'action=(\w+)', current_cmd)
                    action = action_match.group(1) if action_match else "screenshot"
                    params_str = re.search(r'params=(\{.*\})', current_cmd)
                    params_dict = {}
                    if params_str:
                        try:
                            params_dict = json.loads(params_str.group(1))
                        except Exception:
                            pass
                    result = self.tools.call(tool_name, {"action": action, "params": params_dict})
                else:
                    result = self.tools.call(tool_name, {"command": current_cmd})
            elif tool_name == "list_directory":
                result = self.tools.call(tool_name, {"path": current_cmd.split("=")[-1].strip() if "=" in current_cmd else current_cmd})
            else:
                if not current_cmd.strip():
                    result = "命令为空，跳过"
                else:
                    result = self.tools.call(tool_name, {"command": current_cmd})

            output += f"\n🔧 {tool_name}: {current_cmd}\n{result}\n"
            if tool_name == "ask_ai" and "继续" in current_cmd:
                output += "\n⏸️ 请手动完成修改后，再次调用技能并传入适当参数继续。\n"
                break

        summary = f"\n技能 {skill_name} 执行完毕。"
        if "失败" in output or "错误" in output:
            summary += " 部分步骤失败，详见输出。"
        else:
            summary += " 所有步骤成功。"
        if len(output) > 1000:
            output = output[:1000] + "\n...[输出过长已截断]"
        return summary + "\n" + output

    def get_skills_description(self) -> str:
        desc = "可用技能:\n"
        for name, skill in self.skills.items():
            desc += f"- {name}: {skill['description']}\n"
        return desc