# AGENTS.md — Hermes QQ Bot (bot-template)

> 本文件是给 **AI agent（如 OpenCode / Cursor / Claude Code 等）** 的操作指南。
> 如果你下载了本仓库并希望由 AI agent 帮你安装、配置、运行和维护这个 QQ 机器人，请让 agent 完整阅读本文件后再操作。

---

## 项目是什么

这是一个开箱即用的 **QQ 群 AI 聊天机器人** 分发模板：

- **LLM 对话引擎**：Hermes（魔改版），支持任意 OpenAI 兼容 API
- **QQ 接入**：NapCat（OneBot v11 协议桥）
- **桌面立绘**：Live2D 透明窗口（可选）
- **Web 控制面板**：Dashboard（可选）
- **记忆系统**：多层级（短期 / 长期 / 跨群联想 EPI）
- **知识库**：Obsidian 文件夹全文检索（可选）

角色默认是《BanG Dream!》相关设定（SOUL.md），用户可完全自定义。

---

## 快速开始（给 agent 的安装清单）

对 Windows 用户，**推荐让用户直接双击 `install.bat`**，它会自动完成：
1. Python 3.12 检测/安装
2. Node.js portable（Live2D 用）
3. venv + pip 依赖
4. 初始化 `~/.hermes/`（运行数据目录）

安装后**必须由用户手动完成**（agent 无法代办）：
1. 启动 NapCat → 扫码登录 QQ
2. 打开网页控制面板（start.bat 启动后访问 `http://127.0.0.1:8899`），按引导配置 NapCat 的 WS:3001 / HTTP:3000 端口（NapCat 登录后生成的配置文件是唯一的，无法自动代填）
3. 运行 `配置API.bat` 填写 LLM API Key
4. 运行 `start.bat` 一键启动

### Agent 可直接执行的替代流程

如果用户让你手动安装：

```powershell
# 1. 创建运行目录
python scripts/install.py

# 2. 检查 .env（~/.hermes/.env）已生成
#    - ONEBOT_SELF_ID: bot 的 QQ 号（NapCat 登录后回填）
#    - ONEBOT_ADMIN_ID: 管理员 QQ 号
#    - DEEPSEEK_API_KEY: LLM API Key
#    - ONEBOT_BOT_NAME: 角色名（install.py 已从 SOUL.md 自动提取）

# 3. 配置 LLM（或用 配置API.bat）
python scripts/setup_config.py
```

---

## 核心架构（agent 必须了解）

### 消息处理三阶段流水线

```
QQ 消息 → NapCat (:3001 WS)
  → Phase 1 摄入（adapter.py）: 去重 / 黑名单 / @检测 / buffer / 持久化
  → Phase 2 决策（trigger_coordinator.py）: 
      · 对话态 → 直接 judge
      · @mention → mention batch（去抖动合并）
      · 潜水态 → judge 定时器
  → Phase 3 执行（group_executor.py）: 群锁串行 + agent 推理 + 回复
```

关键文件（`hermes/plugins/platforms/onebot/`）：

| 文件 | 职责 |
|---|---|
| `adapter.py` | OneBot 适配器：消息摄入、CQ 码解析、图片下载、发送回复 |
| `trigger_coordinator.py` | Phase 2 决策层：judge 生命周期、mention 合并、episode phase 管理 |
| `group_executor.py` | Phase 3 执行层：群锁、agent 调用、连续对话、滚动摘要 |
| `semantic_judge.py` | 语义判定：pre_reply_judge（该不该回）+ record_post_reply（回复后状态更新） |
| `group_state.py` | 群状态：消息缓冲、EpisodeState（16 字段对话状态机） |

### 角色名参数化（v0.12.5+）

角色名通过 `ONEBOT_BOT_NAME` 环境变量统一管理，judge/recorder/trigger 全部读取：

```
优先级: ONEBOT_BOT_NAME env > config.yaml platforms.onebot.extra.bot_name > 默认 "Soyo"
```

- `semantic_judge.py` → `_get_bot_name()` 读取 + `_render_prompt()` 模板化渲染
- `trigger_coordinator.py` → `@QQxxx → @<bot_name>` 替换
- `adapter.py` → `self._bot_name` + `_annotate_at`

**换角色流程**：编辑 `SOUL.md`（首行 `# SOUL.md — 角色名`）→ 运行 `一键替换灵魂核心.bat`（自动同步角色名到 .env）。

### 记忆系统（`hermes/agent/memory/`）

| 层 | 作用 | 生命周期 |
|---|---|---|
| STM | 原始对话，按群隔离 | 24 小时 |
| EPI | 跨会话联想记忆（EpisodeIndex） | 持久 |
| LTM | 提炼后的事实 | 持久 |
| Workflow / Wiki | 工作流 / 知识库 | 持久 |

入口：`UnifiedMemoryGateway`（gateway.py）→ `MemoryRetriever`（retrieval.py）多源召回。

---

## 配置项（~/.hermes/.env）

| 变量 | 必填 | 说明 |
|---|---|---|
| `ONEBOT_SELF_ID` | ✅ | Bot 的 QQ 号 |
| `ONEBOT_ADMIN_ID` | ✅ | 管理员 QQ 号 |
| `DEEPSEEK_API_KEY` | ✅ | LLM API Key |
| `DEEPSEEK_BASE_URL` | ✅ | LLM API 地址（默认 OpenCode 兼容端点） |
| `DEEPSEEK_MODEL` | ✅ | 模型名 |
| `ONEBOT_BOT_NAME` | ✅ | 角色名 |
| `ONEBOT_WS_URL` | | NapCat WS 地址（默认 ws://127.0.0.1:3001/） |
| `ONEBOT_HTTP_URL` | | NapCat HTTP 地址（默认 http://127.0.0.1:3000） |
| `OBSIDIAN_VAULT_PATH` | | 知识库文件夹路径 |

---

## 升级（agent 可操作）

### 方式一：update.bat（推荐，给用户）

`update.bat` 自动：下载 GitHub 最新版 → robocopy 覆盖代码 → 更新依赖 → 调用 `upgrade.py` 同步到 `~/.hermes/`。**不会覆盖** `config.yaml` / `SOUL.md` / `.env`。

### 方式二：手动 upgrade.py（agent 直接执行）

```powershell
# 下载新版本到临时目录后
python <新版本目录>\scripts\upgrade.py <新版本目录>
```

`upgrade.py` 会把 `UPGRADE_MAP` 中列出的文件**双写**：
1. `~/.hermes/`（实际运行目录，自动去掉 `hermes/` 前缀）
2. `BOT_DIR`（模板目录保留）

### 方式三：git clone 用户

```powershell
git pull origin main
.venv\Scripts\python scripts\upgrade.py
```

---

## 排错（agent 排查顺序）

| 症状 | 检查项 |
|---|---|
| Bot 不回复 | `~/.hermes/logs/gateway.log` 有无 `MemoryRetriever` / `ImportError` / `Judge` 报错；确认 `.env` 的 `ONEBOT_SELF_ID` 已填 |
| 群聊 @ 了不回 | 确认 `ONEBOT_BOT_NAME` 已设置；查看 judge 日志是否 `should_reply=false`；检查 `trigger_coordinator` 的 episode phase 是否卡住 |
| `database is locked` | adapter.py 是否含 WAL PRAGMA（v0.10.3+ 已内置）；运行 upgrade.py 更新 |
| 图片识别失败 | 检查 vision API 配置；日志有无 `max_tokens` 相关报错（v0.12.5 已统一 65536） |
| Live2D 不显示 | `modules/live2d` 依赖是否装好；Dashboard 端口 8899 是否被占用 |
| 系统消息弹出到 QQ | 确认 gateway/run.py `_bg_review_send` 有 SUPPORTS_SYSTEM_MESSAGES 检查（v0.12.5+） |

---

## 给维护者的发布流程

```powershell
# 1. 从生产服务器同步代码（服务器是权威源）
# 2. 隐私清洗：所有硬编码路径/QQ号/人名 → 环境变量或 {{占位符}}
#    （佛像 ASCII art 内的名字是玄学赐福，保留不清理）
# 3. 更新 VERSION + CHANGELOG.md + UPGRADE.md
# 4. 全仓隐私扫描（见 MAINTENANCE.md）
# 5. commit + tag + push + 创建 GitHub Release
```

---

## 隐私红线（严禁写入仓库）

- 真实 QQ 号 / 群号
- API Key / Token
- 用户名 / 真实姓名
- 生产服务器路径（Linux 盘符、SFTP 映射路径）
- 本地开发机绝对路径（`C:\...`、`E:\...`）

以上一律使用 `{{占位符}}` 或环境变量引用。
