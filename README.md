# QQBot — 通用 QQ 群 AI 机器人模板

解压即用。QQ 群 AI 聊天机器人，支持角色扮演、语音回复、Live2D 立绘。

## 快速开始

```
1. 双击 install.bat     (Linux: bash install.sh)
2. 编辑 SOUL.md          ← 角色人设
3. 编辑 config.yaml      ← API Key
4. 双击 start.bat        (Linux: bash start.sh)
   → 选 1 一键启动 → NapCat 弹窗扫码 → 完成
```

## 启动菜单 (start.bat)

| 选项 | 作用 |
|------|------|
| 1 | 一键全部启动 |
| 2 | 只开 NapCat (QQ 桥) |
| 3 | 只开 Gateway + Dashboard |
| 4 | 全部停止 |
| 5 | 打开控制面板 (:8899) |

## 目录

```
├── install.bat / .sh    ← 一键安装
├── start.bat / .sh      ← 总控启动
├── hermes/              ← 核心引擎
├── modules/             ← Live2D / TTS / Dashboard
├── napcat/              ← QQ 协议桥
└── templates/           ← 配置模板
```

## 需要自己准备

- DeepSeek API Key → config.yaml
- GPT-SoVITS → 语音功能（可选）
