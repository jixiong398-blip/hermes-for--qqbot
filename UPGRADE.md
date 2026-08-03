# 升级指南

> v0.14.3

## 目录结构（v0.14.3+）

```
bot-template 根
├─ install.bat / start.bat / Stop-All.bat / update.bat / 配置API.bat
├─ README.md / LICENSE / VERSION / CHANGELOG.md / UPGRADE.md / AGENTS.md
├─ hermes/          ← 引擎（局域网 git 工作区）
│   └─ core/        ← 引擎权威（含 OneBot 插件）
├─ templates/       ← 配置模板（根级）
├─ modules/         ← 功能模块：napcat/ live2d/ dashboard/ knowledge/
└─ extras/          ← 安装包/构建/运行时：node/ scripts/ 安装包 构建脚本
```

## 如何升级

### 方法一：升级脚本（推荐，保留配置文件）

```
1. 下载最新 bot-template.zip，解压到临时目录
2. 运行临时目录中的升级脚本：
   cd 临时目录
   .venv\Scripts\python extras\scripts\upgrade.py
3. 重启 Gateway
```

> `upgrade.py` 只覆盖源码文件和模板，不会动你的 `config.yaml` / `SOUL.md` / `.env`。

### 方法二：下载覆盖（全新安装）

```
1. 从 GitHub Releases 下载最新 bot-template.zip
2. 解压到新目录
3. 将旧版的配置文件复制到新目录：config.yaml, SOUL.md, .env
4. 运行 install.bat（会跳过已安装的环境）
5. 运行 start.bat 启动
```

### 方法三：git pull（仅限通过 git clone 安装的用户）

```
cd bot-template
git pull origin main
.venv\Scripts\python scripts\upgrade.py
```

---

## 文件安装位置

升级脚本会自动将文件放到正确位置，对照如下：

| 源文件（bot-template 内） | 目标位置（`~/.hermes/`） |
|---|---|
| `hermes/agent/memory/retrieval.py` | `agent/memory/retrieval.py` |
| `hermes/agent/memory/episodic_index.py` | `agent/memory/episodic_index.py` |
| `hermes/agent/memory/gateway.py` | `agent/memory/gateway.py` |
| `hermes/agent/memory/short_term.py` | `agent/memory/short_term.py` |
| `hermes/plugins/platforms/onebot/adapter.py` | `plugins/platforms/onebot/adapter.py` |
| `hermes/plugins/platforms/onebot/trigger_coordinator.py` | `plugins/platforms/onebot/trigger_coordinator.py` |
| `hermes/plugins/platforms/onebot/group_executor.py` | `plugins/platforms/onebot/group_executor.py` |
| `hermes/plugins/platforms/onebot/group_state.py` | `plugins/platforms/onebot/group_state.py` |
| `hermes/plugins/platforms/onebot/semantic_judge.py` | `plugins/platforms/onebot/semantic_judge.py` |
| `hermes/plugins/platforms/onebot/media_pipeline.py` | `plugins/platforms/onebot/media_pipeline.py` |
| `hermes/corpus_history.py` | `corpus_history.py` |
| `hermes/tools/chat_history_search_tool.py` | `tools/chat_history_search_tool.py` |
| `hermes/tools/memory_gateway_tool.py` | `tools/memory_gateway_tool.py` |
| `hermes/tools/browser_tool.py` | `tools/browser_tool.py` |
| `hermes/tools/vision_tools.py` | `tools/vision_tools.py` |
| `hermes/tools/web_tools.py` | `tools/web_tools.py` |
| `hermes/gateway/run.py` | `gateway/run.py` |
| `hermes/plugins/knowledge-base/__init__.py` | `plugins/knowledge-base/__init__.py` |
| `hermes/plugins/knowledge-base/knowledge_base_tool.py` | `plugins/knowledge-base/knowledge_base_tool.py` |
| `hermes/agent/memory/obsidian.py` | `agent/memory/obsidian.py` |
| `scripts/qzone-post.py` | `scripts/qzone-post.py` |
| `scripts/decrypt_cvpkg.py` | `scripts/decrypt_cvpkg.py` |

> 运行 `python scripts/upgrade.py` 自动完成以上复制，无需手动操作。

---

## 配置文件搬迁清单

**必须保留（你的个人配置）：**

| 文件 | 内容 |
|------|------|
| `config.yaml` | LLM 模型配置、群号、端口 |
| `SOUL.md` | 角色人设 |
| `.env` | API Key、Bot QQ、Admin QQ |

---

## 版本历史

### v0.14.1
- **Dashboard 视觉重构**：更简洁现代，服务状态卡片（状态圆点）、响应式
- **服务启停秒响应**：异步线程执行（原 2-4s → <0.1s）
- **Hermes 启动前置检查**：先检查 NapCat（进程 + 3000/3001 端口），未运行弹 toast 提示
- **日志区优化**：行间距收紧，NapCat 二维码不再被拆行
- **gateway.py 完整性修复**：补齐 get_stats / get_workflow_decay_report / on_session_end

### v0.14.0
- **context 探测链关闭**：`_ENABLE_CONTEXT_PROBE` 默认 False（`HERMES_CONTEXT_PROBE=1` 才开）——init 不再 30s×2 探测黑洞
- **`import json` 修复**：磁盘缓存保存不再静默失败（收尾 2s 延迟消除）
- **⚠️ 必须配**：`config.yaml model.context_length: 1000000`（探测已关，不配走兜底值）
- **episodic_index.py / context_compressor.py** 同步（dropped 修复 + 计时插桩）

### v0.13.2
- **judge 提速**：thinking 默认 low（env JUDGE_THINKING 可切 disabled/low/default），judge 判定 11s → 3-6s
- **SQLite 锁修复**：store.py `sqlite3.connect(timeout=30)` + `PRAGMA busy_timeout=30000`
- **成本估算缓存**：model_metadata.py endpoint 元数据内存缓存（收尾 2s 延迟）
- **退出软处理**：`exit_farewell=true` 才走 mode="exit"（回复最后一句后退出），否则静默 go_quiet

### v0.13.1
- **judge 队列**：judge in-flight 时新消息入队（不丢消息），结束后补轮
- **@ 软信号**：@mention 带 `_is_mentioned=True` 进完整 judge 流程（不强锁必回）
- **at_targets 指向规则**：@自己=强正向，@别人=强反证（"玩去吧"等词是对别人说的）
- **运行时 @ 解析**：`_group_uid_name_map` 动态学名字，零硬编码
- **删除** `_batch_has_dismissal`（硬驱赶词拦截死代码）

### v0.13.0
- **串线修复**：私聊图片 URL 直进 vision 工具（media_urls/media_types）
- **慢响应修复**：memory consolidation 包 `asyncio.to_thread`（收尾黑洞 16-43s 消除）
- **诊断增强**：vision 报错明确化 + PERF 段级耗时插桩

### v0.12.6
- **转发消息处理**：嵌套递归展开（≤5 层防死循环）+ 500k 阈值 + 30k 分块压缩 + 原位标注
- **角色名参数化**：ONEBOT_BOT_NAME（env > config.yaml > 默认 Soyo）
- **系统消息屏蔽**：`_bg_review_send` 加 SUPPORTS_SYSTEM_MESSAGES 检查
- **安装/更新流程**：install.py 重写（SOUL.md 提取角色名）、upgrade.py 双写目标、update.bat 自动调 upgrade.py
- **文档**：新增开源 AGENTS.md（agent 操作指南）

### v0.12.5
- **角色名参数化**：ONEBOT_BOT_NAME 环境变量（env > config.yaml > 默认 Soyo）
- **Bug 修复**：
  - mention 复位时清空 progression_guidance（防 LLM 读到旧"不要插嘴"指令）
  - should_exit 分支 @ 消息不 go_quiet（防 mention 被压掉）
  - mention 模式跳过指导注入
  - 图片/语音识别加 max_tokens 65536 + 超时 60s（thinking 吃满 token 导致识别失败）
  - `_bg_review_send` 加 SUPPORTS_SYSTEM_MESSAGES 检查（防 "💾 Memory updated" 发到 QQ）
- **安装/更新流程**：
  - install.py 生成完整 .env（含 ONEBOT_BOT_NAME）
  - 一键替换灵魂核心.bat 同步角色名到 .env
  - upgrade.py 目标路径修复（真正更新 ~/.hermes/）
  - update.bat 自动调用 upgrade.py

### v0.12.4
- **P0 修复**：MemoryRetriever 增加 `epi=None` 参数，修复 memory gateway 初始化崩溃导致 bot 不回复
- **P2 修复**：trigger_coordinator @mention 增加 episode phase 复位（exiting/winding_down → starting）
- **P3 补充**：新增 `corpus_history.py`，FTS5 群聊全文搜索模块

### v0.12.3
- 修复 EPI 跨群记忆归档 `dropped` 变量未定义崩溃

### v0.12.2
- Stop-All.bat/start.bat 进程管理改进
- `chat_history_search_tool.py` 截断修复
- max_tokens 统一 65536

### v0.12.0-12.1
- Episode State 系统（16 字段）
- EpisodeIndex 记忆层（EPI）

### v0.11.0-11.1
- EpisodeIndex 记忆层完整实现
- upgrade.py UPGRADE_MAP 补全

### v0.10.6
- 消息处理架构重构（三阶段流水线）
- 新增 media_pipeline / trigger_coordinator / group_executor

---

## 两端（服务器 ↔ Win）同步流程

### 同步边界

| 目录 | 归属 | 同步方式 |
|---|---|---|
| `hermes/core/` | 引擎权威 | **局域网 git**（服务器权威，Win 拉取） |
| `templates/` | 配置模板 | 局域网 git（服务器权威） |
| `modules/` | 功能模块（napcat/live2d/dashboard） | **Win 本地维护**（可回传） |
| `extras/` | 安装包/构建/node/scripts | **Win 本地维护** |

### 服务器 → Win（拉取引擎更新）

```bash
cd hermes
git fetch origin
git checkout origin/main -- core/ templates/ .gitignore
```

### Win → 服务器（回传 Win 独有开发）

```bash
cd hermes
git add core/ modules/ extras/ templates/
git commit -m "Win update"
git push origin main
```

### 局域网仓库

- 服务器：`~/hermes-core.git`（bare repo）
- Win remote：`ssh://ji@192.168.2.16/home/ji/hermes-core.git`
- 首次接入：`git clone ssh://ji@192.168.2.16/home/ji/hermes-core.git`

### 隐私红线

`.env / config.yaml / SOUL.md / sessions/ / logs/ / *.db` 永不进 git（.gitignore 已排除）

---

## 常见问题

**Q: 升级后 bot 不回复？**

1. 检查日志 `~/.hermes/logs/gateway.log` 有无 `MemoryRetriever` 或 `ImportError` 报错
2. 确认 `.env` 中的 `ONEBOT_SELF_ID` 已设置
3. 确认所有文件已安装到正确位置（对照上方文件安装位置表）

**Q: `database is locked` 持续刷屏？**

A: 你的 `~/.hermes/plugins/platforms/onebot/adapter.py` 是旧版，缺 WAL PRAGMA。运行 `python scripts/upgrade.py` 升级即可。

**Q: 群聊 @ 了但不回复？**

A: v0.12.4 已修复。如仍发生，检查 gateway 日志中是否有 `episode` 或 `MemoryRetriever` 相关错误。

**Q: 升级后 Live2D 不显示？**

A: `electron-offline.zip` 未包含时需联网安装，运行 `cd modules\live2d && ..\..\node\npm.cmd install`。

**Q: 想保留旧版配置？**

A: 方法二创建新目录，只把旧版的 config.yaml / SOUL.md / .env 复制过去即可。
