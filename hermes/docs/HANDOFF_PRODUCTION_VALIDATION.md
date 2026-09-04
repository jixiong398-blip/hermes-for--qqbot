# Hermes 生产验证交接文档

> 文档性质：生产环境维护 Agent 的更新与验证 runbook
>
> 代码基线：`74e4828`；当前生产候选 tip：局域网 `main` 的 `ab77144` 快照
>
> 更新日期：2026-09-01
>
> 目标部署：一台机器、一个 Hermes Bot 实例、一个当前登录的 NapCat 账号

本文件只服务于维护者和生产环境 Agent。它不包含服务器地址、真实账号、QQ 号、token、API Key 或本机绝对路径；不要把运行时日志、数据库内容或凭据粘贴回本文件。

## 1. 交接结论

局域网核心仓库至少包含以下连续提交：

1. `a2575d6`：上游兼容合同、SessionDB/Environment/OneBot 边界、可靠性模块、自研记忆 schema 兼容和基础回归。
2. `74e4828`：自研记忆的 chat scope 与完成生命周期，包含 `UPG-MEM-185`、`SEC-REVIEW-186`、6 项 scope/lifecycle 测试及相关维护日志。
3. `57ca089`：OneBot completion contract 修复；真实 pending follow-up 到达时，空 completion 标记为 interrupted，并以群消息水位阻止 synthetic contract retry。
4. `ab77144`：生产验证交接文档对齐最终 LAN tip，并保留 `57ca089` 的代码修复边界。

局域网远端 `origin/main` 已确认指向 `ab77144`。根目录 GitHub 仓库没有本次提交，生产 Agent 不得从 GitHub 代替局域网远端更新。

本次更新的真实含义是：

- STM/EPI 在调用方提供显式 `chat_type` 时执行 scope 过滤；DM 片段不能进入 group recall，group-to-group 仍保持匿名联想。
- Gateway 的 `agent:start` 只记录 Layer 0 原始事件；成功完成的 `agent:end` 才写入 STM。failed、interrupted 和 contract retry 不进入 STM/EPI。
- `MemoryStore` 对旧记忆库只执行固定 allowlist 的 additive schema 兼容；首次启动可能新增列、表、索引并重建 FTS，这是预期行为，但不是 v26 全量迁移。
- LTM 仍是当前产品设计的全局提炼事实层；本次不能宣称所有 user/chat 的 LTM 完全隔离。

## 2. 生产 Agent 的硬规则

### 2.1 Git 边界

- 只在 nested Hermes 核心仓库执行 Git 操作，远端必须是局域网 `origin`。
- 不运行根目录 GitHub 仓库的 `push`，不创建公开 Release，不修改公开版本号。
- 生产工作树有未提交改动时停止，先报告文件清单；不要 `reset --hard`、不要强制 checkout、不要覆盖用户改动。
- 只允许 fast-forward 更新到指定提交；远端出现分叉时停止，不自行 rebase 或 force push。

### 2.2 数据和凭据边界

- 更新前备份 Hermes 的 `state.db`、`memory_store.db` 及存在的 `-wal`、`-shm`、`-journal` sidecar，并记录哈希和大小。
- 保留 `.env`、`config.yaml`、`SOUL.md`、角色文件、SessionDB 和 NapCat 配置；升级代码不得覆盖它们。
- NapCat 自有 SQLite 只属于 NapCat 运行数据，不是 Hermes replay 或 memory migration 输入；不要读取、清空、迁移或删除它。
- 日志只能记录“是否发现 token/是否连接成功”，不能记录 token、Authorization header、API Key、完整 URL 查询参数、原始 QQ payload 或私聊正文。
- 备份文件、临时报告和生产日志不要提交到 Git；使用生产环境既有的受限目录。

### 2.3 消息边界

- 生产 Agent 不主动向 QQ 私聊或群发送测试消息。
- 需要真实入站时，先在维护通道报告 `READY`，等待维护者发送一条已授权测试消息；不得使用 bot 自发消息代替用户入站。
- 测试只允许使用已授权的账号/群和短暂白名单；不得为了“扩大覆盖”放宽到所有用户。

## 3. 更新前检查

所有命令均应在生产 Hermes 核心仓库执行。命令中的 `<...>` 是运行时本地值，不要把真实值回填到本文件。

### 3.1 确认仓库状态

```text
git branch --show-current
git remote -v
git status --short
git log -5 --oneline --decorate
```

必须满足：

- 当前分支和远端关系可解释；
- `origin` 指向局域网核心仓库；
- 工作树干净，或已得到维护者明确批准处理本地改动；
- 能找到当前生产版本的回滚提交 `<previous_commit>`。

如果工作树不干净，先输出相对路径、修改类型和是否属于配置/数据，不要继续更新。

### 3.2 确认运行状态

- 确认当前 Gateway、Dashboard 和维护脚本的进程归属；不要误停 NapCat。
- 记录 OneBot WS/HTTP 是否已连接、当前 bot self-id 是否已由维护者确认，以及是否有正在执行的 agent turn。
- 选择维护窗口，避免在模型请求、压缩、SessionDB 写入或发送回执进行时替换代码。
- 确认磁盘空间、数据库文件权限、Python 版本和现有虚拟环境可用。

### 3.3 备份和完整性快照

1. 停止新的 Hermes Gateway turn，等待正在执行的安全收尾完成。
2. 对 `state.db`、`memory_store.db` 和全部存在的 sidecar 做只读哈希/大小记录。
3. 将备份放入受限、带时间戳的生产备份目录；备份目录不在 Git 工作树内。
4. 记录当前 schema version、主要表行数、FTS 表存在性和 WAL mode，不输出消息正文。
5. 备份失败、文件正在变化、sidecar 不成套或权限不足时停止更新。

## 4. 从局域网更新

先只获取远端引用，再确认目标提交；不要直接覆盖本地文件。

```text
git fetch origin main
git rev-parse origin/main
git show --stat --oneline origin/main
```

预期目标为 `ab77144`。如果 `origin/main` 比目标更新，先报告新的提交，不能把未来版本混入本次生产验证。

工作树干净且目标确认后，只允许 fast-forward：

```text
git merge --ff-only origin/main
git rev-parse HEAD
git status --short
```

更新后再次确认：

- `HEAD` 等于 `origin/main`；
- `docs/UPDATE_LOG.md` 包含 `UPG-MEM-185` 和 `SEC-REVIEW-186`；
- `docs/UPGRADE_PLAN.md` 包含对应的 scope/lifecycle 门禁；
- `docs/DECISION_LOG.md` 包含 `DEC-MEM-028` 和 `DEC-SEC-033`；
- `.env`、`config.yaml`、`SOUL.md` 和 NapCat 配置没有被 Git 操作修改。

如果依赖需要更新，只按项目既有的受限安装流程执行，并先记录依赖变更；不要直接复制上游 `pyproject.toml` 覆盖本地依赖。

## 5. 代码和记忆库验证

### 5.1 不接触生产数据的快速验证

先执行编译和导入检查。测试应使用独立临时 `HERMES_HOME` 或项目既有 scratch 机制，不能让 pytest 指向生产数据库、生产 sessions 或 NapCat SQL。

最低检查：

```text
python -m py_compile core/agent/memory/store.py core/agent/memory/gateway.py core/agent/memory/retrieval.py core/agent/memory/short_term.py core/agent/builtin_memory_provider.py core/gateway/builtin_hooks/memory_maintenance.py core/gateway/run.py
python -m pytest -o addopts= core/tests/agent/test_memory_scope_integration.py
```

预期：scope/lifecycle 专项 6 项通过。`skills_guard.py:627` 的非法转义 `SyntaxWarning` 是已知预存 warning，不应被当作本次升级成功或失败的唯一依据。

### 5.2 生产 `MemoryStore` additive migration 观察

本版本第一次真正打开旧 `memory_store.db` 时，`MemoryStore._init_db()` 可能执行以下固定范围的 additive 操作：

- 为 `long_term_entries` 补齐 v2 字段；
- 创建 `memory_edges`、`_sleep_watermark`、registry 和索引；
- 为旧 `chat_message_buffer` 补 `message_id`；
- 在 schema 发生补齐后重建外部 FTS。

这不是 v26 migration。生产 Agent 必须：

1. 在 Gateway 启动前保存数据库及 sidecar 快照。
2. 启动后记录 schema delta、表行数、FTS 状态和 quick-check 结果。
3. 确认只出现 allowlist 内的 additive 变化，没有删除表、删除列、改写正文或修改 `SCHEMA_VERSION=11`。
4. 遇到 `database is locked`、FTS malformed、WAL/SHM 不成套、行数异常或事务错误时立即停止 Gateway，保留现场和备份，不执行自动修复。
5. 不运行旧表重建、不执行 v26 mixin、不删除 WAL/SHM、不手工编辑 SQLite。

## 6. NapCat / OneBot 启动检查

### 6.1 账号配置

- NapCat 必须先完成登录，并由 NapCat 生成账号专属配置。
- Hermes 使用当前配置中的 bot self-id 和账号配置目录自动发现 token；token 不是按 QQ 号计算出来的。
- `ONEBOT_ACCESS_TOKEN` 可以作为兼容 fallback，但本机 NapCat 优先使用账号专属配置。
- Dashboard 只展示账号摘要、连接状态和端口状态，不回显 token。

### 6.2 启动顺序

1. 确认 NapCat 已登录并且 WS/HTTP 端口正常。
2. 启动 Hermes Gateway，不启动第二个 Hermes 实例，不共享其它实例的 SessionDB 或 memory namespace。
3. 等待日志出现连接建立、账号选择和 event loop ready；日志不得出现 token 内容。
4. 通过 Dashboard 读取端口/账号/总状态，确认当前选择和 bot self-id 对齐。
5. 先观察连接和健康状态，不主动发送 QQ 消息。

本地已证明的只到 transport/outbound 层：WS/HTTP 鉴权、`get_login_info`、私聊/测试群发送和 Dashboard 读取均有回环证据；这不等于真实用户入站经过 judge、provider、memory 和 delivery 全链路。

### 6.3 多平台 websocket 共存检查

- 当 Feishu 与 OneBot 同时启用时，Feishu SDK 的 websocket runtime override 必须只作用于 Lark client 自己持有的 module reference；不得替换进程共享的 `websockets.connect`。
- 原因：共享函数被包装成通用 `*args/**kwargs` 后，OneBot 的版本兼容探测无法识别认证 header 参数，导致本机 NapCat 连接被误报为 transport failure。
- 受控验证通过的信号是：Gateway 在同一次启动中记录 Feishu connected、OneBot connected、OneBot event loop ready 和首个协议事件；没有 `transport_error` 或认证拒绝。此检查不发送 QQ 消息。
- 即使 transport 与普通入站摄入均已观察到，READY 协议仍独立执行：只有维护者在授权范围发送的测试消息才能用于 judge、provider、memory、SessionDB 与 delivery 的闭环结论。

## 7. READY 入站验证协议

真实入站测试必须严格按以下顺序：

1. 生产 Agent 在维护通道报告：`READY: gateway connected; waiting for authorized inbound test.`
2. 维护者发送一条短、无敏感信息的测试消息到已授权私聊或测试群。
3. 记录是否捕获到新的 OneBot inbound event；不要读取或回放 READY 之前的历史消息。
4. 核对同一事件的阶段性证据：
   - adapter 摄入和去重；
   - judge/trigger 是否放行；
   - agent turn 是否创建；
   - provider request/response 是否成功；
   - `agent:start` 是否只留下 Layer 0；
   - 成功 `agent:end` 是否写入 STM；
   - SessionDB transcript 是否有对应 user/assistant 行；
   - OneBot delivery 是否有成功回执。
5. 在同一授权范围内发送第二条包含合成 marker 的消息，确认下一轮能召回上一轮允许持久化的内容。
6. 确认其它 chat scope 没有出现该 marker，且 Layer 0/日志没有泄露不必要的正文、QQ 号或 token。
7. 测试结束后撤销临时白名单，恢复生产默认 admission；保存脱敏报告，不保存原始聊天内容。

如果第 2 步没有产生 inbound event，保持负向证据，不把 bot 自发消息、旧消息或 outbound receipt 当作入站替代品。

## 8. 失败处理和回滚

### 8.1 启动前失败

- 远端分叉、工作树有未授权改动、备份失败、依赖不完整或 NapCat 未登录：不启动新代码，保留旧版本运行，报告阻塞原因。

### 8.2 启动后失败

- 连接失败、token 发现失败、schema delta 超出 allowlist、FTS/锁错误或 provider 配置错误：停止 Hermes Gateway，保留日志和数据库快照，不删除运行文件。
- 如果数据库完整性检查失败，先使用备份副本做只读对比；只有在维护者明确授权后才恢复生产数据库。

### 8.3 代码回滚

在工作树干净且确认 `<previous_commit>` 后，生产 Agent 可以回到上一已知提交；不要强制重写远端历史。回滚代码不等于回滚数据库：本次 additive 列/表通常可被旧代码忽略，只有数据完整性异常时才按备份恢复整套数据库及 sidecar。

回滚后必须重新执行：

- `git status` 和当前提交确认；
- Gateway/OneBot transport 健康检查；
- SessionDB/memory quick-check；
- 脱敏证据报告和维护日志。

## 9. 交接证据模板

生产 Agent 完成后只提交以下脱敏摘要，不提交原始数据库或完整聊天记录：

```text
target_commit: <commit>
previous_commit: <commit>
update_mode: fast-forward | not-started | rolled-back
working_tree_clean: true | false
backup_complete: true | false
state_db_hash_recorded: true | false
memory_db_hash_recorded: true | false
memory_schema_delta: <allowlist summary only>
memory_rows_before_after: <counts only>
fts_status: unchanged | rebuilt | stale | failed
napcat_token_discovered: true | false
onebot_ws: connected | failed | not-tested
gateway_ready: true | false
inbound_event_captured: true | false
judge_agent_provider_chain: passed | failed | not-tested
memory_completed_turn: passed | failed | not-tested
memory_interrupted_turn: passed | failed | not-tested
sessiondb_transcript: passed | failed | not-tested
delivery_receipt: passed | failed | not-tested
cross_scope_check: passed | failed | not-tested
rollback_used: true | false
known_warnings: <sanitized list>
```

禁止填入：真实 QQ 号/群号、私聊正文、token、API Key、Authorization header、服务器路径、生产日志全文、数据库文件名中包含的敏感标识。

## 10. 当前不应宣称完成的门禁

即使本次生产验证通过，也只能关闭“指定版本的一机一 Bot 更新和受控入站验证”门禁，不能宣称以下事项已经完成：

- 上游 `conversation_loop.py`、`agent_init.py`、`tool_executor.py` 或 `turn_finalizer.py` 已整文件替换本地 `run_agent.py`；
- SessionDB v11 已完成 v26 全量写入迁移、PK heal、FTS merge 或 lineage adoption；
- LTM 已实现按 user/chat 的全量隔离；
- Linux/Windows 全量独立进程、WAL、权限和干净机安装矩阵已全部通过；
- 多 NapCat、多 Hermes、多账号统一 Dashboard 已可生产运行；
- 公开版本号、公开 changelog、GitHub Release 已授权发布。

## 11. 参考资料

- `docs/HANDOFF_UPSTREAM_UPGRADE.md`：完整上游差异和历史交接。
- `docs/ARCHITECTURE_TARGET.md`：目标架构、owner 和不变量。
- `docs/AGENT_RUNTIME_THREE_WAY_MATRIX.md`：Agent Runtime 三方融合边界。
- `docs/SESSIONDB_THREE_WAY_MATRIX.md`：SessionDB Gate 0–5 与 v11/v26 边界。
- `docs/SESSIONDB_V26_MIGRATION_MAP.md`：v26 实施映射，不是生产迁移授权。
- `docs/UPGRADE_PLAN.md`：阶段计划、门禁和 deferred 列表。
- `docs/UPDATE_LOG.md`：维护者事实日志；不得复制到公开 GitHub 文档。
- `docs/DECISION_LOG.md`：架构决策和安全边界。

生产 Agent 的默认原则是：先保留旧运行状态和可恢复快照，再做最小、可观察、可回滚的更新；无法证明的链路保持 `not-tested/deferred`，不要用推测填绿。
