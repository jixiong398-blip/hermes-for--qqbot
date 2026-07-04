# QQBot — 通用 QQ 群 AI 机器人模板

> **v0.9.0** — 离线优先安装 · 零硬编码隐私 · 完整记忆系统 · 配置外提

解压即用。Python 3.12、Node.js、Live2D 已内置离线包。

## 前提

准备一个 **LLM API Key**（DeepSeek / OpenAI / Anthropic 等任意一家即可）。

## 部署流程

```
① 双击 install.bat      → 离线安装 Python + Node.js + Live2D + Hermes
② 启动 NapCat            → 打开 napcat\napcat.bat，扫码登录 QQ
③ 双击 FixNapCat.bat     → 自动开启 WS :3001 + HTTP :3000
④ 双击 配置API.bat        → 选供应商 + 填 API Key
⑤ 双击 start.bat         → Dashboard 启动，一键开 Bot
```

> **注意**：NapCat 必须手动扫码登录，登录后才生成配置。FixNapCat.bat 在登录后运行。

## 创建角色灵魂（必须）

1. 阅读 `templates\SOUL-template.md` 参考格式
2. 让 AI 基于模板写出你想要的角色
3. 保存为 `templates\SOUL.md`
4. 双击 `templates\一键替换灵魂核心.bat` → 写入 `~\.hermes\SOUL.md`

## 功能

| 功能 | 说明 |
|------|------|
| 全语义判定 | DeepSeek v4-flash 判定触发/结束/循环 |
| 统一状态管理 | GroupState 每群独立状态机（关注态/旁观态/静默） |
| Live2D 立绘 | 桌面右下角角色立绘，11 个角色可选 |
| TTS 语音 | GPT-SoVITS 语音合成（10 种情绪） |
| 完整记忆系统 | LLM 蒸馏提取 + 长期/短期/工作流记忆 + 语义召回 |
| 知识库检索 | RAG 全文搜索 + Obsidian 知识库 |
| QQ 空间说说 | 自动发说说到 QZone（可选） |
| Web 控制面板 | :8899，服务启停 + 记忆搜索 + 日志查看 |

### QZone 说说（可选）

1. 确保 NapCat 已登录
2. 在 `.env` 中设置 `ONEBOT_ACCESS_TOKEN`
3. 配置定时任务（cron/任务计划程序），调用 `python scripts/qzone-post.py "说说内容"`

### 恢复聊天记录（可选）

如果升级后发现群聊历史丢失，可运行恢复脚本：

```bash
.venv\Scripts\python hermes\scripts\backfill_corpus.py
```

脚本通过 NapCat API 从 QQ 本地缓存拉取群聊消息写入数据库。
需要 `.env` 中配置好 `ONEBOT_ACCESS_TOKEN` 和 `ONEBOT_HTTP_URL`。

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
├── install.bat              ← 一键安装（离线优先）
├── 配置API.bat               ← API 配置入口
├── FixNapCat.bat             ← 登录后开启端口
├── start.bat                ← 一键启动 Dashboard
├── Stop-All.bat             ← 一键停止
├── python-installer.exe     ← Python 3.12 离线安装包
├── nodejs.zip               ← Node.js v22.11 内置包
├── electron-offline.zip     ← Live2D Electron 离线包（如有）
├── hermes/                  ← 魔改版 Hermes 核心引擎
│   ├── plugins/platforms/onebot/   OneBot QQ 适配器
│   ├── agent/memory/               完整记忆系统
│   └── tools/                      80+ 工具
├── modules/
│   ├── live2d/              ← Live2D 桌面立绘
│   ├── dashboard/           ← Web 控制面板
│   └── knowledge/           ← 知识库（留空）
├── napcat/                  ← NapCat QQ 协议桥
├── templates/
│   ├── SOUL-template.md         ← 人设模板
│   ├── config-template.yaml     ← 主配置模板
│   ├── .env.template            ← 环境变量模板
│   └── 一键替换灵魂核心.bat      ← 安装自定义 SOUL
└── README.md
```

## 更新

已有旧版本？下载最新版 zip 解压覆盖，运行 `python scripts\upgrade.py`。详见 `UPGRADE.md`。
