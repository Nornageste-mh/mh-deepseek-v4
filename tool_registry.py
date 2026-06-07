# tool_registry.py
"""
MCP 兼容工具注册中心 — 现代通用接口
支持热插拔：运行时动态添加/移除工具
兼容 MCP (Model Context Protocol) 工具定义格式

工具输出模式（output_mode）：
  - head:  返回前 200 字符
  - tail:  返回后 200 字符
  - full:  返回完整结果（默认）
AI 可以在任何工具调用时添加 _output_mode 参数来控制输出长度。
"""

import logging
from typing import Dict, Callable, Union, List, Any

logger = logging.getLogger("MHAgent.ToolRegistry")

# ── 输出截取长度 ──
OUTPUT_HEAD_CHARS = 200
OUTPUT_TAIL_CHARS = 200


class ToolRegistry:
    """
    MCP 兼容工具注册中心。

    设计原则：
    1. 热插拔 — register / unregister 随时可用
    2. MCP 兼容 — 工具 schema 遵循 JSON Schema (Draft-07)
    3. 优雅降级 — 工具执行异常返回结构化错误而非崩溃
    4. output_mode — AI 可控制工具结果输出长度
    """

    def __init__(self, executor=None):
        self.executor = executor
        self.tools: Dict[str, dict] = {}

    # ── 注册 / 注销 ──────────────────────

    def register(self, name: str, handler: Callable, description: str,
                 parameters: dict = None):
        """注册一个工具"""
        props = {}
        required = []
        if parameters:
            for param, conf in parameters.items():
                if isinstance(conf, dict):
                    prop_def = {"type": conf.get("type", "string")}
                    if "description" in conf:
                        prop_def["description"] = conf["description"]
                    if "enum" in conf:
                        prop_def["enum"] = conf["enum"]
                    props[param] = prop_def
                    if conf.get("required", True):
                        required.append(param)
                else:
                    props[param] = {"type": str(conf)}
                    required.append(param)

        # 所有工具自动添加 _output_mode 参数
        props["_output_mode"] = {
            "type": "string",
            "enum": ["head", "tail", "full"],
            "description": "输出模式：head=前200字符, tail=后200字符, full=完整（默认）"
        }

        schema = {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": props,
                "required": required
            }
        }

        self.tools[name] = {"handler": handler, "schema": schema}
        logger.debug(f"工具已注册: {name}")

    def unregister(self, name: str) -> bool:
        if name in self.tools:
            del self.tools[name]
            logger.debug(f"工具已注销: {name}")
            return True
        return False

    # ── MCP 协议接口 ──────────────────────

    def list_tools(self) -> List[dict]:
        return [t["schema"] for t in self.tools.values()]

    def call_tool(self, name: str, arguments: dict) -> dict:
        """MCP tools/call — 支持 _output_mode 控制输出长度"""
        if name not in self.tools:
            return {
                "content": [{"type": "text", "text": f"未知工具: {name}"}],
                "isError": True
            }

        # 提取 _output_mode
        output_mode = arguments.pop("_output_mode", "full")

        try:
            handler = self.tools[name]["handler"]
            result = handler(**arguments)

            if isinstance(result, dict) and result.get("need_auth"):
                return {
                    "content": [{"type": "text", "text": str(result)}],
                    "isError": False,
                    "_need_auth": result
                }

            # 应用 output_mode
            text = str(result)
            if output_mode == "head" and len(text) > OUTPUT_HEAD_CHARS:
                text = text[:OUTPUT_HEAD_CHARS] + f"\n... [截取前 {OUTPUT_HEAD_CHARS} 字符，完整输出共 {len(result)} 字符]"
            elif output_mode == "tail" and len(text) > OUTPUT_TAIL_CHARS:
                text = f"[输出共 {len(result)} 字符，显示后 {OUTPUT_TAIL_CHARS} 字符]\n..." + text[-OUTPUT_TAIL_CHARS:]

            return {
                "content": [{"type": "text", "text": text}],
                "isError": False
            }

        except TypeError as e:
            logger.warning(f"工具参数错误 {name}: {e}")
            return {
                "content": [{"type": "text", "text": f"参数错误: {e}"}],
                "isError": True
            }
        except Exception as e:
            logger.exception(f"工具执行失败 {name}: {e}")
            return {
                "content": [{"type": "text", "text": f"工具执行错误: {str(e)}"}],
                "isError": True
            }

    # ── 兼容旧接口 ──────────────────────

    def get_schemas(self) -> list:
        """兼容旧版 API — 将 MCP schema 转为 OpenAI function calling 格式"""
        schemas = []
        for t in self.tools.values():
            s = t["schema"]
            schemas.append({
                "type": "function",
                "function": {
                    "name": s["name"],
                    "description": s["description"],
                    "parameters": s["inputSchema"]
                }
            })
        return schemas

    def call(self, name: str, arguments: dict, session_id: str = None) -> Union[str, dict]:
        """兼容旧版 API — 返回字符串结果（need_auth 返回 dict）"""
        result = self.call_tool(name, arguments)
        if result.get("_need_auth"):
            return result["_need_auth"]
        text = result["content"][0]["text"] if result["content"] else ""
        return text
