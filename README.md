# QQBot — 通用 QQ 群 AI 机器人模板

> **v0.10.0** — Cubism 5 Live2D (12 角色) · 语义判断 v2 · 13 家 LLM · 零硬编码隐私

解压即用。Python 3.12、Node.js、Live2D Electron 已内置离线包。

## 部署流程

```
① 双击 install.bat        → 离线安装 Python + Node.js + Live2D + Hermes
② 启动 NapCat 扫码登录     → napcat.bat → 扫码登录 → WebUI 开启 WS/HTTP 端口
③ 双击 配置API.bat          → 选供应商 + 填 API Key + 管理员 QQ（自动读 NapCat token）
④ 准备角色灵魂              → 编辑 SOUL.md → 一键替换灵魂核心.bat
⑤ 双击 start.bat           → Dashboard 启动，一键开 Bot
```

> **注意**：NapCat 必须手动扫码登录。Bot QQ 号/群号/群名自动从 NapCat 发现。以后更新只需 `update.bat`。

## 需要配置的内容

| 配置项 | 在哪里填 | 说明 |
|--------|----------|------|
| LLM API Key | 配置API.bat | 支持 13 家供应商 |
| 管理员 QQ | 配置API.bat | 能执行指令的人 |
| 角色灵魂 | SOUL.md | 见下方说明 |

## 创建角色灵魂（必须）

1. 阅读 `templates\SOUL-template.md` 参考格式
2. 让 AI 基于模板写出你想要的角色
3. 保存为 `templates\SOUL.md`
4. 双击 `templates\一键替换灵魂核心.bat` → 写入 `~\.hermes\SOUL.md`

## 功能

| 功能 | 说明 |
|------|------|
| 语义判断 v2 | 噪音等级/连续性/间接对话/证据层级 |
| Live2D 立绘 | 12 位 Cubism 5 角色，右键菜单控 |

...(output truncated for display)

### QZone 说说（可选）

NapCat 登录后自动可用，配置 cron 定时任务即可。

## 更新

已有旧版本？双击 `update.bat` 即可自动升级（保护 config.yaml / SOUL.md / .env 不被覆盖）。

## 支持的 LLM 供应商（13 家）

| 供应商 | 说明 |
|--------|------|
| DeepSeek | 性价比高 |
| OpenCode Go | 推荐，一站式聚合 | 
| 智谱 GLM | glm-5.2 |
| 火山方舟（豆包） | 字节跳动 |
| 阿里百炼（通义千问） | qwen3.7-plus |
| MiniMax | MiniMax-Text-01 |
| Moonshot（Kimi） | kimi-k2 |
| OpenAI | gpt-4o |
| Anthropic | Claude 4 |
| SiliconFlow | 国产开源模型聚合 |
| OpenRouter | 400+ 模型聚合 |
| Ollama / LM Studio | 本地部署 |
| 自定义 | 任意 OpenAI 兼容端点 |

## 文件

```
├── install.bat              ← 一键安装（离线优先）
├── update.bat               ← 一键更新（保护配置）
├── 配置API.bat               ← API 配置入口
├── start.bat                ← 一键启动 Dashboard
├── Stop-All.bat             ← 一键停止
├── python-installer.exe     ← Python 3.12 离线安装包
├── nodejs.zip               ← Node.js v22.11 内置包
├── electron-offline.zip     ← Live2D Electron 离线包（分卷）
├── hermes/                  ← 魔改版 Hermes 核心引擎
│   ├── plugins/platforms/onebot/   OneBot QQ 适配器
│   ├── agent/memory/               完整记忆系统
│   ├── gateway/                    Live2D 自动控制
│   ├── tools/                      80+ 工具
│   └── scripts/                    辅助脚本（恢复/监控/健康检查）
├── modules/
│   ├── live2d/              ← Live2D 桌面立绘
│   ├── dashboard/           ← Web 控制面板（含 onboarding 引导）
│   └── knowledge/           ← 知识库（留空）
├── napcat/                  ← NapCat QQ 协议桥
├── templates/
│   ├── SOUL-template.md         ← 人设模板
│   ├── CORTEX.md                ← 行为模板
│   ├── CEREBELLUM.md            ← Live2D 控制
│   ├── config-template.yaml     ← 主配置模板
│   ├── .env.template            ← 环境变量模板
│   └── 一键替换灵魂核心.bat      ← 安装自定义 SOUL
└── README.md
```
