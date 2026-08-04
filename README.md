# QQBot — 通用 QQ 群 AI 机器人模板

> **v0.14.3** — core/ 分层引擎 · 局域网 git 同步 · judge 提速 · 角色名参数化

解压即用。Python 3.12、Node.js、Live2D Electron 已内置离线包。

## 目录结构

```
bot-template/
├─ install.bat / start.bat / Stop-All.bat / update.bat / 配置API.bat
├─ README.md / LICENSE / VERSION / CHANGELOG.md / UPGRADE.md
├─ hermes/          ← 引擎（局域网 git 工作区）
│   └─ core/        ← 引擎权威（含 OneBot 插件、记忆系统、80+ 工具）
├─ templates/       ← 配置模板（根级）
├─ modules/         ← 功能模块
│   ├─ napcat/      ← NapCat QQ 协议桥
│   ├─ live2d/      ← Live2D 桌面立绘（12 角色，CDN 下载模型）
│   ├─ dashboard/   ← Web 控制面板
│   └─ knowledge/   ← 知识库（Obsidian 文件夹）
└─ extras/          ← 安装包 / 构建 / 运行时
    ├─ node/        ← Node.js v22.11 内置包
    ├─ scripts/     ← 辅助脚本（配置/升级/解密）
    ├─ electron-offline.zip.*  ← Live2D Electron 离线包（分卷）
    ├─ nodejs.zip / python-installer.exe  ← 离线安装包
    └─ build-*.ps1 / build-*.iss  ← 发布构建脚本
```

## 部署流程

```
① 双击 install.bat        → 离线安装 Python + Node.js + Live2D + Hermes
② 启动 NapCat 扫码登录     → modules\napcat\napcat.bat → 扫码登录 → WebUI 开启 WS/HTTP 端口
③ 双击 配置API.bat          → 选供应商 + 填 API Key + 管理员 QQ（自动读 NapCat token）
④ 准备角色灵魂              → 编辑 SOUL.md → 一键替换灵魂核心.bat（自动同步角色名到 .env）
⑤ 双击 start.bat           → Dashboard 启动，一键开 Bot
```

> **注意**：NapCat 必须手动扫码登录。Bot QQ 号/群号/群名自动从 NapCat 发现。以后更新只需 `update.bat`。

## 需要配置的内容

| 配置项 | 在哪里填 | 说明 |
|--------|----------|------|
| LLM API Key | 配置API.bat | 支持 13 家供应商 |
| 管理员 QQ | 配置API.bat | 能执行指令的人 |
| 角色灵魂 | SOUL.md | 见下方说明 |
| 视觉 API Key | 配置API.bat | 图片识别（可选，OpenCode Go 可复用 LLM Key） |

## 创建角色灵魂（必须）

1. 阅读 `templates\SOUL-template.md` 参考格式
2. 让 AI 基于模板写出你想要的角色
3. 保存为 `templates\SOUL.md`
4. 双击 `templates\一键替换灵魂核心.bat` → 写入 `~\.hermes\SOUL.md` 并同步角色名到 .env

> 角色名参数化：`ONEBOT_BOT_NAME` 环境变量（env > config.yaml > 默认值），judge/recorder 自动跟随。

## 功能

| 功能 | 说明 |
|------|------|
| 语义判断 | 两级窗口判定（旁观 5s / 关注 1s），judge 提速 3-6s |
| @ 软信号 | @ 进完整判定流程，被 @ 大概率回复但可合理判不回 |
| Episode State | 16 字段对话状态机，跨会话联想记忆（EPI） |
| Live2D 立绘 | 12 位 Cubism 5 角色，右键菜单控制 |
| 记忆系统 | STM / LTM / EPI / Workflow 多层记忆 |
| 转发消息 | 嵌套递归展开、原位标注、500k 阈值 |
| Dashboard | 服务状态卡片、日志流、记忆检索 |

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

## 两端同步（开发者）

服务器（Linux，权威）与 Win 分发版通过局域网 git 同步引擎：

```bash
# 服务器 → Win（拉引擎更新）
cd hermes && git fetch origin && git checkout origin/main -- core/ templates/ .gitignore

# Win → 服务器（回传独有开发）
cd hermes && git add -A && git commit -m "update" && git push origin main
```

- 同步边界：`hermes/core/` + `templates/`（服务器权威）；`modules/` + `extras/`（Win 本地维护）
- 隐私红线：`.env / config.yaml / SOUL.md / sessions/ / logs/ / *.db` 永不进 git
