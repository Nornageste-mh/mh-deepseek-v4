# MH-DeepSeek V4

一个基于 DeepSeek V4 API 的轻量级 Web 智能体，支持 Android Termux / Linux / Windows。

---

## 功能

- 多模型支持（DeepSeek V4 Flash / Pro / 妹居DeepSeek）
- 工具调用（终端命令 / 文件操作 / 网页搜索 / 网页浏览 / 网络安全检测）
- 思考模式（推理链可视化）
- 流式响应 + 自动错误恢复（HTTP 400/429/5xx）
- 长期记忆（跨会话用户偏好）
- 上下文压缩（LLM 摘要 + 工具输出修剪）
- 密钥分片加密存储（Shamir 秘密共享 + AES-256-GCM）

## 快速开始

```bash
git clone https://github.com/Nornageste-mh/mh-deepseek-v4.git
cd mh-deepseek-v4
pip install -r requirements.txt
python main.py
```

浏览器打开 `http://localhost:9090`。

首次运行会引导配置 DeepSeek API Key 和博查搜索 Key。

## 项目结构

| 文件 | 说明 |
|------|------|
| `main.py` | 入口，自动初始化密钥 → 启动 Web |
| `session.py` | 会话管理 + API 错误处理 + 流式循环 |
| `model_provider.py` | DeepSeek / 妹居DeepSeek 提供商管理 |
| `executor.py` | 安全命令执行器（root / Shizuku / ADB） |
| `tool_registry.py` | MCP 兼容工具注册中心 |
| `memory.py` | 会话记忆持久化 |
| `long_term_memory.py` | 跨会话长期用户画像 |
| `context_compressor.py` | 上下文压缩器（工具修剪 + LLM 摘要） |
| `config.py` | 全局配置 |
| `index.html` | 前端（纯 JS，零外部依赖） |

## 平台说明

Android Termux 环境下需额外配置 root 或 Shizuku 以获得完整终端能力。普通模式（非 root）下可正常使用大部分功能。

## 许可

MIT
