# AGENTS.md — bot-template 打包维护

> 最后更新: 2026-05-25 | v0.4.1 | WS心跳修复 + 图片识别优化

## 这是什么

bot-template 是 Hermes QQ Bot 的通用化分发模板。任何人都可以下载、安装、配置自己的 QQ 群 AI 机器人。

源码来自 `E:\ai\hermes-agent\`（魔改版 Hermes），已做隐私清洗和品牌去敏。

## 目录结构

```
E:\ai\bot-template\
├── hermes/              ← 魔改版 Hermes 源码 (3413 files)
│   ├── gateway/platforms/onebot/adapter.py   OneBot 适配器
│   ├── tools/memory_gateway_tool.py          记忆网关
│   ├── toolsets.py                           _HERMES_ONEBOT_TOOLS
│   └── pyproject.toml                        精简依赖
├── modules/
│   ├── live2d/          ← Live2D 桌面立绘 (11 角色, 15245 files)
│   ├── dashboard/       ← Web 控制面板 (server.py)
│   ├── tts/             ← TTS 调用代码 (不含模型)
│   └── knowledge/       ← 知识库 (留空)
├── napcat/              ← NapCat QQ 协议桥
├── templates/           ← 配置模板
│   ├── config-template.yaml
│   └── SOUL-template.md
├── scripts/install.py   ← 安装器
├── install.bat / .sh    ← 一键安装
├── start.bat / .sh      ← 一键启动
├── python-installer.exe ← Python 3.12 离线包
└── README.md            ← 用户说明
```

## 安装流程

```
install.bat
  ├─ 检查 Python → 若无则装 python-installer.exe
  ├─ pip install -e hermes\          # 安装魔改版 Hermes + 依赖
  └─ python scripts\install.py       # 创建 ~/.hermes/ + 写配置模板

start.bat
  ├─ 清理旧进程
  ├─ 启动 NapCat (launcher.bat)
  ├─ 启动 Gateway (hermes gateway)
  ├─ 启动 Dashboard (server.py :8899)
  └─ 打开浏览器
```

## 保持同步

当 `E:\ai\hermes-agent\` 有改动时：

```powershell
# 1. 覆盖源码
robocopy E:\ai\hermes-agent E:\ai\bot-template\hermes /E /XD venv cache build logs

# 2. 隐私清洗 (替换所有密钥/QQ号/路径)
# 3. git add -A && git commit && git push
```

## 隐私清洗清单

| 原文 | 替换为 |
|------|--------|
| `sk-CfBJRQ8u5...` (OpenCode Go) | `{{DEEPSEEK_API_KEY}}` |
| `tp-czpw66y9...` (MiMo) | `{{MIMO_TOKEN}}` |
| `3560998016` (QQ 号) | `{{BOT_QQ_ID}}` |
| `2910137276` (群号) | `{{HOME_CHANNEL}}` |
| `cTDW~Sv_EAjgJ0kF` (Token) | `{{ONEBOT_TOKEN}}` |
| `长崎素世` | `QQBot` |
| `jixiong233` | `{{USERNAME}}` |
| `清尘璃落` | `{{CHANNEL_NAME}}` |

## 发布步骤

```powershell
cd E:\ai\bot-template
git add -A
git commit -m "v0.3.x: description"
git push origin main  # 需要梯子
```
