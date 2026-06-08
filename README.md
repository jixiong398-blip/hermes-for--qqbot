# QQBot — 通用 QQ 群 AI 机器人模板

解压即用。Python 3.12 已内置。

## 前提

准备一个 **LLM API Key**（DeepSeek / OpenAI / Anthropic 等任意一家即可）。

## 部署流程

```
① 双击 install.bat     → 自动装 Python + Node.js + Live2D + Hermes
② 双击 配置API.bat       → 选供应商 + API Key + 机器人QQ + 主人QQ + 群号
③ 双击 start.bat        → 启动 Dashboard，点「启动 NapCat」扫码登录
```

> 配置API.bat 会预生成 NapCat 配置，登录后 WS :3001 / HTTP :3000 直接可用。
> 主人 QQ 能执行指令（/voice 等），其他人只能聊天。

## 创建角色灵魂（必须）

机器人需要一个角色人设才能正常对话。

1. 阅读 `templates\SOUL-template.md` 参考格式
2. 让 AI 基于模板写出你想要的角色
3. 保存为 `templates\SOUL.md`
4. 双击 `templates\一键替换灵魂核心.bat` → 写入 `~\.hermes\SOUL.md`

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
├── 配置API.bat               ← API + QQ + NapCat 配置
├── start.bat                ← 一键启动 Dashboard
├── Stop-All.bat             ← 一键停止
├── python-installer.exe     ← Python 3.12 离线包
├── nodejs.zip               ← Node.js v22.11 内置包
├── build-release.ps1        ← 开发者发布构建脚本
├── hermes/                  ← 核心引擎
├── modules/                 ← Live2D / TTS / Dashboard
├── napcat/                  ← QQ 协议桥
├── templates/
│   ├── SOUL-template.md         ← 人设模板（参考用）
│   ├── SOUL.md                  ← 你自己的角色（按上面步骤创建）
│   ├── 一键替换灵魂核心.bat      ← 安装自定义 SOUL
│   ├── config-template.yaml     ← 主配置模板
│   └── napcat/                  ← NapCat 配置模板
└── README.md
```