# MH-DeepSeek Agent（临时过渡版）

> ⚠️ **这是一个过渡版本，功能有限，仅供临时测试。强烈建议转向更成熟的开源方案。**

---

## 🚦 为什么不该用这个项目？

- 代码质量未经充分测试，长期记忆和会话管理存在已知稳定性问题
- Android 控制模块依赖外部环境，成功率不高
- 本项目仅为早期个人学习实验，未来可能归档或停止维护

---

## ✅ 推荐替代项目

以下三个开源项目成熟度更高、社区活跃、文档完善，**强烈建议直接使用**：

### 1️⃣ DeepSeek-TUI

终端原生编程智能体，基于 DeepSeek-V4 构建，由美国独立开发者 Hunter Bown 于 2026 年 1 月发布[reference:0]。

- **核心定位**：终端里的 DeepSeek 版 Claude Code，用 Rust 编写，支持 100 万 Token 超长上下文[reference:1][reference:2]
- **三大运行模式**：自动模式在每一轮交互中自动适配大模型并匹配推理等级[reference:3]
- **安装运行**：一行命令或下载对应平台的预编译二进制即可启动[reference:4]
- **项目地址**：https://github.com/??? （待补充）

### 2️⃣ Hermes Agent

Nous Research 旗下自主 AI Agent 应用层，2026 年 4 月 GitHub Stars 超过 52,800[reference:5]。

- **核心定位**：一行命令部署，内置自学习循环，可自主创建技能、优化行为[reference:6]
- **支持平台**：Linux、macOS、WSL2、Termux，通过 Telegram/Discord/Slack 等 6 大平台统一接入[reference:7]
- **核心差异**：部署方式为一行 curl 安装，支持 6 种执行后端，内置自我进化能力[reference:8]
- **安装运行**：`curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash`[reference:9]
- **项目地址**：https://github.com/NousResearch/hermes-agent

### 3️⃣ OpenClaw

自主开源 AI 助手，截至 2026 年 3 月 GitHub Stars 超过 347,000，成为 GitHub 历史上 Star 数最高的软件仓库[reference:10][reference:11]。

- **核心定位**：运行在你机器上的 AI 助手，通过 WhatsApp、Telegram、Slack、Discord、iMessage 或 Signal 对话并执行真实任务——清理收件箱、发送邮件、管理日历、办理航班值机[reference:12][reference:13]
- **独特能力**：MIT 协议开源，本地优先存储（记忆和数据存为 Markdown 文件），支持社区技能包扩展[reference:14][reference:15]
- **安装运行**：`npm i -g openclaw && openclaw onboard`[reference:16]
- **项目地址**：https://github.com/openclaw/openclaw

---

## 临时测试：本项目快速尝试（不推荐）

```bash
git clone https://github.com/yourusername/mh-deepseek-agent.git
cd mh-deepseek-agent
pip install -r requirements.txt
python main.py