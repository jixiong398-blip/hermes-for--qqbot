# QQBot — 通用 QQ 群 AI 机器人模板

解压即用。双击 `install.bat` 安装，双击 `start.bat` 启动。

## 快速开始

```
1. 双击 install.bat     → 一键安装
2. 编辑 config.yaml     → 填 API Key
3. 编辑 SOUL.md         → 写角色人设
4. 双击 start.bat       → 一键启动（NapCat + Gateway + Dashboard）
   → NapCat 弹窗扫码登录 QQ → 完成
```

Dashboard: http://127.0.0.1:8899

## 文件

```
├── install.bat / .sh    ← 一键安装
├── start.bat / .sh      ← 一键启动
├── hermes/              ← 核心引擎
├── modules/             ← Live2D / TTS / Dashboard
├── napcat/              ← QQ 协议桥
└── templates/           ← 配置模板
```

## 需要自己准备

- DeepSeek API Key → 填到 config.yaml
- GPT-SoVITS → 语音功能（可选）
