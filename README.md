# mh-deepseek-v4

一个 DeepSeek V4 的 Web 客户端，运行在终端里。

支持 Android Termux / Linux / Windows。

---

## 安装

```bash
git clone https://github.com/Nornageste-mh/mh-deepseek-v4.git
cd mh-deepseek-v4
pip install -r requirements.txt
python main.py
```

浏览器打开 `http://localhost:9090`，按提示填入 API Key。

Python 版本建议 3.10+。

---

## 大致功能

- 聊天界面（纯 JS 前端，不依赖任何框架）
- 工具调用（终端、文件、搜索、网页抓取）
- 流式输出，带思考过程展示
- 错误自动重试
- 会话记录持久化

---

## 依赖

```
flask, requests, cryptography, scrapling
```

完整列表见 `requirements.txt`。

---

## 已知局限

- 没有完善的测试
- 某些极端情况下消息格式可能出错（有自动修复，但不保证）
- Android 上完整功能需要 root 或 Shizuku

---

## 许可

MIT
