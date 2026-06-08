# QQBot — 通用 QQ 群 AI 机器人模板

解压即用。Python 3.12 已内置。

## 前提

准备一个 **LLM API Key**（DeepSeek / OpenAI / Anthropic 等任意一家即可）。

## 部署流程

```
① 双击 install.bat     → 自动装 Python 3.12 + 创建环境 + 安装依赖 + Live2D
② 双击 配置API.bat       → 选择 LLM 供应商，填入 API Key，填 QQ 群号
③ 双击 start.bat        → 启动 Dashboard（浏览器自动打开 :8899）
④ 在 Dashboard 点启动 NapCat → 扫码登录 QQ
⑤ 双击 FixNapCat.bat    → 自动开启 WS :3001 + HTTP :3000 端口
⑥ (可选) 自定义角色       → 见下方
```

> 此后每次只需运行 `start.bat`，在 Dashboard 面板管理所有服务。

Dashboard: http://127.0.0.1:8899

## 自定义角色（最后一步，可选）

1. 阅读 `templates\SOUL-template.md` 了解人设模板格式
2. 让 AI（ChatGPT / DeepSeek / Claude 等）基于此模板写出你想要的角色
3. 保存为 `templates\SOUL.md`
4. 双击 `templates\一键替换灵魂核心.bat` → 覆盖生效

## 功能

| 功能 | 说明 |
|------|------|
| QQ 群聊 | @ 或 # 触发回复，auto_join 主动插话 |
| Live2D 立绘 | 桌面右下角角色立绘，支持切换模型 |
| TTS 语音 | GPT-SoVITS 语音合成（可选，需自行部署） |
| 记忆系统 | 长期记忆 + 会话上下文 |
| 知识库检索 | RAG 全文搜索 |
| Web 控制面板 | :8899，服务启停 + 记忆搜索 + 日志查看 |

## 支持的 LLM 供应商

| 供应商 | 说明 |
|--------|------|
| DeepSeek | 推荐，性价比最高 |
| OpenAI | GPT-4o 系列 |
| Anthropic | Claude 4 系列 |
| OpenCode Go | OpenCode 聚合接口 |
| SiliconFlow | 国产开源模型聚合 |
| Moonshot (Kimi) | 月之暗面 |
| MiniMax | MiniMax-Text-01 |
| Ollama / LM Studio | 本地部署 |

## 文件

```
├── install.bat              ← 一键安装
├── 配置API.bat               ← API 配置（8 家供应商）
├── FixNapCat.bat            ← 端口修复
├── start.bat                ← 一键启动
├── Stop-All.bat             ← 一键停止
├── python-installer.exe     ← Python 3.12 离线包
├── hermes/                  ← 核心引擎
├── modules/                 ← Live2D / TTS / Dashboard
├── napcat/                  ← QQ 协议桥
├── node/                    ← Node.js（Live2D 用）
├── templates/
│   ├── SOUL-template.md         ← 人设模板（AI 参考用）
│   ├── 一键替换灵魂核心.bat      ← 替换人设
│   ├── config-template.yaml     ← 主配置模板
│   └── napcat/                  ← NapCat 配置模板
└── README.md
```
