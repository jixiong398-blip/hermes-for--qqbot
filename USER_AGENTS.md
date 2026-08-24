# USER_AGENTS.md — QQ 机器人 agent 操作指南（面向用户）

> 本文件给 **AI agent（OpenCode / Cursor / Claude Code 等）** 看，帮助你安装、配置、运行这个 QQ 群 AI 机器人模板。
> 本文件不含任何维护者/服务器内部信息，用户可安全使用。

---

## 这是什么

开箱即用的 **QQ 群 AI 聊天机器人** 分发模板：
- **LLM 对话引擎**：Hermes（魔改版），支持任意 OpenAI 兼容 API
- **QQ 接入**：NapCat（OneBot v11 协议桥）
- **桌面立绘**：Live2D 透明窗口（可选）
- **Web 控制面板**：Dashboard（可选）
- **记忆系统**：多层级（短期 / 长期 / 跨群联想 EPI）
- **知识库**：Obsidian 文件夹全文检索（可选）

## 快速开始

### 方式一：双击 install.bat（推荐）

**Windows 用户直接双击 `install.bat`**，自动完成：
1. Python 3.12 检测/安装（离线）
2. Node.js portable（Live2D 用）
3. venv + pip 依赖
4. 初始化 `~/.hermes/`（运行数据目录）

### 方式二：agent 手动执行

```powershell
# 1. 创建运行目录
python extras/scripts/install.py

# 2. 检查 ~/.hermes/.env 已生成（核心变量）
#    - ONEBOT_SELF_ID: bot 的 QQ 号（NapCat 登录后回填）
#    - ONEBOT_ADMIN_ID: 管理员 QQ 号
#    - DEEPSEEK_API_KEY: LLM API Key
#    - ONEBOT_BOT_NAME: 角色名（通常已自动提取）

# 3. 配置 LLM（或用 配置API.bat 交互式）
python extras/scripts/setup_config.py
```

## 安装后必须用户手动完成（agent 无法代办）

1. **启动 NapCat 扫码登录**（`modules\napcat\napcat.bat`）——QQ 会话是唯一的
2. **配置 NapCat 端口**：登录后打开 `http://127.0.0.1:8899`，按引导配置 WS:3001 / HTTP:3000
3. 运行 `配置API.bat` 填 LLM API Key + 管理员 QQ
4. 运行 `start.bat` 一键启动

> NapCat 登录后生成的配置文件是唯一的，无法自动代填，必须让用户手动操作。

## 创建角色灵魂（必须）

1. 阅读 `templates\SOUL-template.md` 参考格式
2. 让 AI 基于模板写出你想要的角色（或让 agent 帮你生成）
3. 保存为 `templates\SOUL.md`
4. 双击 `templates\一键替换灵魂核心.bat` → 写入 `~\.hermes\SOUL.md` 并同步角色名到 .env

> **SOUL.md 必须含 `## 称呼` 节**（正式名 + 别名列表）——否则名字直呼识别（v0.14.13）失效：
>
> ```
> ## 称呼
> 正式名：你的角色名
> 别名：名字1, 名字2, 小名, ...
> ```

## 运行

```
① 启动 NapCat 扫码登录（modules\napcat\napcat.bat）
② start.bat → Dashboard 启动，一键开 Bot
```

## 更新

已有旧版本？双击 `update.bat` 自动升级（保护 config.yaml / SOUL.md / .env 不被覆盖）。

## 默认配置值（~/.hermes/.env）

| 变量 | 必填 | 说明 |
|---|---|---|
| `ONEBOT_SELF_ID` | ✅ | Bot 的 QQ 号 |
| `ONEBOT_ADMIN_ID` | ✅ | 管理员 QQ 号 |
| `DEEPSEEK_API_KEY` | ✅ | LLM API Key |
| `DEEPSEEK_BASE_URL` | ✅ | LLM API 地址 |
| `DEEPSEEK_MODEL` | ✅ | 模型名 |
| `ONEBOT_BOT_NAME` | ✅ | 角色名 |
| `ONEBOT_WS_URL` | | NapCat WS（默认 ws://127.0.0.1:3001/） |
| `ONEBOT_HTTP_URL` | | NapCat HTTP（默认 http://127.0.0.1:3000） |
| `OBSIDIAN_VAULT_PATH` | | 知识库文件夹路径（默认为项目内 modules/knowledge） |

## 常见问题排查

| 症状 | 检查 |
|---|---|
| Bot 不回复 | `~/.hermes/logs/gateway.log` 有无 ImportError/Judge 报错；确认 ONEBOT_SELF_ID 已填 |
| 群聊 @ 了不回 | 确认 ONEBOT_BOT_NAME + SOUL「称呼」节已设置 |
| 名字直呼不回 | SOUL「称呼」节别名列表是否完整 |
| 图片识别失败 | 检查 vision API 配置 |
| Live2D 不显示 | modules/live2d 依赖装好；Dashboard 8899 端口未被占用 |

## 安全提示

- `.env`、`config.yaml`、`SOUL.md`、`*.db`、`sessions/`、`logs/` 含你的私密数据（QQ 号、API Key），**永不提交到任何 git 仓库**
- 修改角色：只编辑 `templates\SOUL.md`（模板）或 `~\.hermes\SOUL.md`（运行时），不要动代码
