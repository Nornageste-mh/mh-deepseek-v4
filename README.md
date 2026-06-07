# mh-deepseek-v4

一个 DeepSeek V4 的 Web 客户端，运行在终端里。

支持 Android Termux / Linux / Windows。

---

> 如果你在找一个成熟的 AI Agent，请直接跳到下面的[推荐项目](#推荐项目)。

---

## 安装

```bash
git clone https://github.com/Nornageste-mh/mh-deepseek-v4.git
cd mh-deepseek-v4
pip install -r requirements.txt
python main.py
```

浏览器打开 `http://localhost:9090`，按提示填入 DeepSeek API Key。

---

## 大致功能

- 聊天界面（纯 JS，无框架依赖）
- 工具调用（终端、文件、搜索、网页抓取）
- 流式输出 + 思考过程展示
- 错误自动重试
- 会话记录持久化

---

## 已知局限

- 无自动化测试
- 极端情况下消息格式可能出错
- Android 完整功能需要 root 或 Shizuku

---

## 推荐项目

以下是社区维护的更成熟的 AI Agent，建议优先考虑：

### Hermes Agent

Nous Research 出品。一行命令安装，内置自学习循环，可自主创建技能。支持 Telegram / Discord / Slack 等平台接入。

- 安装：`curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash`
- 地址：https://github.com/NousResearch/hermes-agent

### OpenClaw

自主 AI 助手，可运行在本地或服务器上，通过 WhatsApp / Telegram / Slack / iMessage 等对话并执行真实任务。MIT 协议，本地优先存储。

- 安装：`npm i -g openclaw && openclaw onboard`
- 地址：https://github.com/openclaw/openclaw

### Open Interpreter

让 LLM 在本地运行代码。支持 Python、JavaScript、Shell 等。

- 安装：`pip install open-interpreter`
- 地址：https://github.com/OpenInterpreter/open-interpreter

### Aider

终端里的 AI 结对编程工具，支持 Git 感知的多文件编辑。

- 安装：`pip install aider-chat`
- 地址：https://github.com/Aider-AI/aider

---

## 许可

MIT
