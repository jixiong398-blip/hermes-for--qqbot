# QQBot — 通用 QQ 群 AI 机器人模板

解压即用。Python 3.12 已内置，无需手动安装任何东西。

## 快速开始

```
1. 双击 install.bat     → 自动装 Python + 创建环境 + 安装依赖
2. 双击 配置API.bat       → 选择 LLM 供应商，填入 API Key（支持 8 家）
3. 双击 start.bat         → 启动（NapCat + Gateway + Live2D + Dashboard）
   → NapCat 弹窗扫码登录 QQ → 完成
```

> 已安装过的用户可以直接运行 `配置API.bat` 更换供应商或 Key，无需重装。

Dashboard: http://127.0.0.1:8899

## 功能

| 功能 | 说明 |
|------|------|
| QQ 群聊 | @ 或 # 触发，多轮对话 + 角色扮演 |
| Live2D 立绘 | 桌面右下角角色立绘，Gateway 启动后自动连接 |
| TTS 语音 | 支持 GPT-SoVITS 合成，LLM 自行判断是否调用 |
| 记忆系统 | 长期记忆 + 会话上下文，自动积累 |
| 知识库检索 | RAG 知识库，支持全文搜索 |
| Web 控制面板 | :8899，服务管理 + 记忆搜索 + 日志查看 |

## 文件

```
├── install.bat          ← 一键安装（含 Python 自动部署）
├── start.bat / .sh      ← 一键启动
├── python-installer.exe ← Python 3.12 离线安装包
├── hermes/              ← 核心引擎
├── modules/             ← Live2D / TTS / Dashboard
├── napcat/              ← QQ 协议桥
└── templates/           ← 配置模板
```

## 支持的 LLM 供应商

| 供应商 | 说明 |
|--------|------|
| DeepSeek | 推荐，性价比最高 |
| OpenCode Go | OpenCode 聚合接口 |
| OpenAI | GPT-4o 系列 |
| Anthropic | Claude 4 系列 |
| SiliconFlow | 国产开源模型聚合 |
| Moonshot (Kimi) | 月之暗面 |
| MiniMax | MiniMax-Text-01 |
| Ollama / LM Studio | 本地部署 |

首次安装时交互式选择，填入 API Key 即自动配置。

## 需要自己准备

- LLM API Key（安装时直接填入）
- Live2D 立绘模型 → `modules/live2d/assets/figure/`（已内置 11 角色）
- GPT-SoVITS → TTS 语音（可选，需自行安装）
- QQ 扫码登录（首次启动 NapCat 弹窗）
