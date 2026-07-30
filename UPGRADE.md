# 升级指南

> v0.12.4

## 如何升级

### 方法一：升级脚本（推荐，保留配置文件）

```
1. 下载最新 bot-template.zip，解压到临时目录
2. 运行临时目录中的升级脚本：
   cd 临时目录
   .venv\Scripts\python scripts\upgrade.py
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
