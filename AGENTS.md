# AGENTS.md — bot-template 打包维护

> 最后更新: 2026-05-26 | v0.5.3.1

## 这是什么

bot-template 是 Hermes QQ Bot 的通用化分发模板。任何人都可以下载、安装、配置自己的 QQ 群 AI 机器人。

源码来自 `E:\ai\hermes-agent\`（魔改版 Hermes），已做隐私清洗。

## 目录结构

```
E:\ai\bot-template\
├── hermes/              ← 魔改版 Hermes 源码
│   ├── plugins/platforms/onebot/adapter.py   OneBot 适配器（插件方式加载）
│   ├── gateway/platforms/onebot/adapter.py   OneBot 适配器（gateway 内置）
│   ├── tools/memory_gateway_tool.py          记忆网关
│   ├── toolsets.py                           工具集定义
│   └── requirements.txt                      精简依赖（38 包）
├── modules/
│   ├── live2d/          ← Live2D 桌面立绘
│   ├── dashboard/       ← Web 控制面板
│   ├── tts/             ← TTS 调用代码 + ts_adapter_template.py
│   └── knowledge/       ← 知识库（留空，用户自行放入 .md）
├── napcat/              ← NapCat QQ 协议桥 v9.9.27
├── node/                ← Node.js portable（Live2D 用）
├── templates/           ← 配置模板
│   ├── config-template.yaml
│   ├── SOUL-template.md
│   ├── .env.template
│   └── napcat/ (onebot11.json, napcat.json)
├── scripts/
│   ├── install.py       ← 初始化 ~/.hermes/
│   ├── setup_config.py  ← 多供应商 API 配置
│   ├── fix_napcat.py    ← 登录后开端口
│   └── upgrade.py       ← 版本升级脚本
├── install.bat          ← 一键安装（Python + venv + pip + Live2D）
├── 配置API.bat           ← API 配置入口
├── FixNapCat.bat        ← 端口修复入口
├── start.bat            ← 一键启动 Dashboard
├── Stop-All.bat         ← 一键停止所有服务
├── Install-Live2D.bat   ← Live2D 独立安装
└── README.md            ← 用户说明
```

## 部署流程

```
install.bat → 配置API.bat → start.bat → 扫码登录 NapCat → FixNapCat.bat
```

## 保持同步

当 `E:\ai\hermes-agent\` 有改动时：

```powershell
robocopy E:\ai\hermes-agent E:\ai\bot-template\hermes /E /XD venv cache build logs .git
```

然后必须执行隐私清洗（见下方清单）。

## 隐私清洗清单

| 原文 | 替换为 |
|------|--------|
| `sk-CfBJRQ8u5...` | `{{DEEPSEEK_API_KEY}}` |
| `tp-czpw66y9...` | `{{MIMO_TOKEN}}` |
| `3560998016` | `{{BOT_QQ_ID}}` |
| `2910137276` | `{{HOME_CHANNEL}}` |
| `cTDW~Sv_EAjgJ0kF` | `{{ONEBOT_TOKEN}}` |
| `长崎素世` | `QQBot` |
| `jixiong233` | `{{USERNAME}}` |
| `清尘璃落` | `{{CHANNEL_NAME}}` |
| `/home/ji/` | `/home/{{USERNAME}}/` |
| `E:/ai/` | 相对路径 |

## 发布步骤

```powershell
cd E:\ai\bot-template
python scripts/upgrade.py           # 同步最新文件
# 检查隐私 → 更新 VERSION → 更新 CHANGELOG.md
git add -A
git commit -m "vX.Y.Z: description"
git tag vX.Y.Z
git push origin main
git push origin vX.Y.Z
# 去 GitHub Releases 创建 Release
```

## AI Agent 升级说明

详见 `UPGRADE.md`。升级后必须运行隐私扫描：

```python
import os
for root, dirs, files in os.walk("."):
    for f in files:
        if f.endswith((".py",".md",".yaml",".json")) and f != "AGENTS.md":
            t = open(os.path.join(root,f), encoding="utf-8").read()
            for p in ["3560998016","cTDW~Sv_EAjgJ0kF","sk-CfBJRQ8u",
                       "清尘璃落","jixiong233","/home/ji/","E:/ai/"]:
                if p in t: print(f"LEAK: {f} - {p}")
```
