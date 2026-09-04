# SessionDB 三方合并矩阵

> 状态：SessionDB 三方合并的阶段性架构矩阵（2026-09-01 更新）。
>
> 参与方：本地 Hermes QQ Bot 魔改版、fork 锚点 v0.13、上游 Hermes v0.20.6。
>
> 原则：先证明接口和数据可读，再逐步吸收上游模块；禁止用上游单文件覆盖本地 `hermes_state.py`。

## 1. 三方形态

| 维度 | fork 锚点 / 本地旧版 | 上游 v0.20.6 | 合并策略 |
|---|---|---|---|
| 文件形态 | 单体 `core/hermes_state.py`，约 125KB | facade `hermes_state.py` 约 681KB + `common/schema/search/portability` 四模块 | 保留本地 facade，逐步引入纯辅助和 mixin，不做整文件替换 |
| schema version | 本地/锚点 v11 | v26 | 不直接把版本号改成 26；先建立逐版本迁移映射和只读探针 |
| FTS | `messages_fts` + `messages_fts_trigram`，本地 CJK 和锁恢复修复 | unicode61/trigram/CJK 多路径、外部内容 FTS、增量 rebuild/merge、stale marker | 先保留本地触发器和 `_rebuild_fts` 语义，再逐个移植上游修复 |
| SQLite 生命周期 | 单连接写入，WAL/DELETE fallback，本地重试 | 读连接池、只读路径、WAL reset 保护、checkpoint/后台 token writer | 读连接池另立 Change ID；当前只审计接口，不改连接策略 |
| 会话/消息语义 | SessionEntry、lineage、compression、OneBot transcript | 更丰富的 gateway/session metadata、archive/pin/hide/unread、handoff | 本地字段和返回 shape 是兼容底线；新字段只追加、默认可空 |
| 记忆关系 | STM/EPI/LTM 在 `agent/memory/`，不依赖 SessionDB schema | MemoryManager/Provider 与 SessionDB 解耦 | 不让 SessionDB 合并强制替换本地记忆 backend |
| 平台扩展 | QQ OneBot 通过本地插件读写会话 | 多平台 routing/topic/handoff 表 | routing 仅在证明确认不污染 OneBot session key 后接入 |

## 2. 数据表与索引矩阵

### 2.1 共同核心

两边都必须保持以下对象可读，字段扩展不能改变现有列意义：

- `schema_version`：单行版本记录；升级前必须以备份副本运行迁移。
- `sessions`：session id、source、时间、parent/lineage、system prompt、模型/标题等会话元数据。
- `messages`：按 session 隔离的 transcript；角色、内容、timestamp 和平台消息关联字段不能丢失。
- `state_meta`：迁移标记、FTS stale/rebuild 状态和本地运行元数据。
- `messages_fts` / `messages_fts_trigram`：派生索引，任何损坏都不能删除 canonical `messages`。

### 2.2 上游新增或显著扩展

以下表/字段必须先作为可选能力识别，不得假设旧 DB 已存在：

- `system_prompts`：去重的系统提示正文及引用关系；迁移必须能从旧 `sessions.system_prompt` 回填。
- `session_model_usage`：按 session/model/task 聚合 token、成本、reasoning 等统计；旧记录缺列时使用零值。
- `gateway_routing`：作用域化 session key 路由；旧主键形态需要上游 PK heal，不能直接执行 `ON CONFLICT`。
- `gateway_hygiene_state`：网关清理/停滞状态；不可影响正常消息读取。
- `compression_locks`：压缩租约与过期时间；与本地群锁不是同一把锁。
- `session_turn_leases`：turn 级租约；必须与 OneBot 群串行锁分层。
- `async_delegations`：子代理委托记录；不应被当作 QQ 用户 transcript 注入模型。
- `sessions` 的 handoff、archive、pin、hidden、unread、compression cooldown 等可选字段。
- `messages` 的 reasoning、display metadata、tool-call 和平台消息元数据字段。

### 2.3 FTS 派生结构

上游存在 legacy inline FTS、外部内容 FTS、trigram/CJK 多种历史形态。合并时必须区分：

1. canonical transcript：`messages`，永远优先保存；
2. derived index：可重建、可降级到 LIKE；
3. rebuild marker：记录高水位、stale、trash/legacy 迁移状态。

任何 FTS 触发器重建都要满足：事务内完成、失败保留原索引或明确 stale、不能在另一个 gateway writer 活跃时盲删 WAL/SHM。

## 3. 方法兼容分层

| 分层 | 本地代表 | 上游代表 | 处理 |
|---|---|---|---|
| 创建/关闭 | `__init__`, `close`, context manager | 读连接、token writer、WAL guard | 先保持本地同步 facade；异步/读池独立迁移 |
| 会话 CRUD | `create_session`, `ensure_session`, `end_session`, `reopen_session` | 同名 + gateway metadata/handoff | 以本地返回 shape 为基线，新增字段可选 |
| transcript | `append_message`, `replace_messages`, `get_messages`, `get_messages_as_conversation` | batch insert、display/reasoning、rewind/restore | 先加契约测试，再移植 batch/rewind，保留本地重复回放规则 |
| search | `search_messages`, `search_sessions` | `SessionSearchMixin` 多路径搜索、LIKE fallback、anchored view | 先复用本地安全查询和 CJK 结果，再移植纯 sanitizer/排序 helper |
| portability | `export_session`, `export_all`, `import`、删除/清理 | `SessionPortabilityMixin` lineage export/import、大小上限 | 先读兼容；写入迁移必须有大小/字段白名单和失败可重试 |
| schema | `_init_schema`, `_reconcile_columns` | `SessionSchemaMixin` v26 migrations、PK heal、FTS recovery | 不把 v11 直接标成 v26；每一项 migration 可单独回滚/重跑 |
| metadata | `get_meta`, `set_meta` | 更多 migration/hygiene/FTS markers | key 命名隔离，不能覆盖本地 EPI/网关控制键 |

## 4. 不可破坏的数据契约

### 4.1 Session id 与 lineage

- `session_key`（Gateway 路由键）与 `session_id`（持久化键）不能混用。
- `parent_session_id`、compression child、branch/resume 关系必须保持可追溯。
- 任何新表的外键失败都不能删除原 session 或消息。
- OneBot 群隔离和按用户分组策略属于产品层，不能被上游默认的跨平台 routing 覆盖。

### 4.2 Message 内容与显示字段

- `_CONTENT_JSON_PREFIX` 等本地编码的非字符串内容必须可逆。
- assistant reasoning、tool calls、tool results、图片/音频元数据不能被旧读取器误拼进用户可见文本。
- `replace_messages` 和 rewind/restore 的软删除/active watermark 语义必须先以现有测试锁定。
- FTS 只索引允许的文本字段；原始 OneBot payload 不可因为上游 display metadata 扩展而进入普通搜索或模型上下文。

### 4.3 并发与耐久性

- 本地已有的 `database is locked` 修复、逐 fragment commit 和 FTS rebuild 保护是保留项。
- WAL 不兼容文件系统必须继续有 DELETE fallback；不能把网络盘上的 WAL 设定当作稳定事实。
- 所有 schema/FTS 写入都要有 bounded retry 和关闭路径；不能在 Gateway 事件循环里执行无界 VACUUM/rebuild。
- 读连接池、token writer、turn lease 不能改变本地 shutdown spool 的顺序。

## 5. 合并顺序与门禁

### Gate 0：只读三方探针

- 输出三方类/方法/表/索引清单和 schema 版本。
- 对一个空 DB 和一个复制的历史 DB 只执行连接、读取、计数、搜索、导出，不做原库迁移。
- 验收：旧 DB 可读、OneBot 会话 key 不变、`git diff --check`/py_compile 通过。

### Gate 1：纯 common helper

- 只移植不触及连接/事务的常量、字段映射、LIKE/preview/JSON helper。
- 每个 helper 通过本地输入输出契约测试后，才能被 facade 使用。

### Gate 2：schema mixin 的增量读取

- 引入 `SessionSchemaMixin` 的表探针、缺列补齐和 PK heal 的只读判断。
- 迁移动作默认关闭；只报告可迁移项和风险。
- 验收：旧 schema 不被启动过程改变，损坏 FTS 只标记待修复。

### Gate 3：search mixin

- 先接 sanitizer、CJK 选择和 LIKE fallback；保留本地搜索排序/返回字段。
- 再做 bounded rebuild/merge；不得在本阶段接入 VACUUM 或外部 FTS layout optimize。

### Gate 4：portability mixin

- 先实现导出字段白名单和大小上限的只读对照。
- 写入/导入必须使用副本、事务和失败保留，确认历史 SQLite 可回滚后再启用。

### Gate 5：facade 组合

- 只有前四个 Gate 的测试稳定后，才把 mixin 接到本地 `SessionDB` facade。
- 每次只启用一个能力开关；OneBot、记忆、Dashboard 和 shutdown spool 做回归。

## 6. 明确禁止的捷径

- 禁止复制上游 `hermes_state.py` 覆盖本地文件。
- 禁止仅把 `SCHEMA_VERSION` 从 11 改为 26 来伪装迁移完成。
- 禁止删除本地 FTS 表后依赖下次启动“自动恢复”而没有 spool/备份。
- 禁止把 `messages` 全量导入外部 memory provider。
- 禁止为通过上游测试而放宽本地 OneBot session isolation、权限或隐私边界。

## 7. 验收矩阵

| 测试 | 证据 |
|---|---|
| empty DB bootstrap | 表、列、索引、FTS 形态与预期版本一致 |
| copied historical DB read | 会话/消息/lineage/标题/搜索结果与基线一致 |
| schema probe | v11/v26/缺列/旧 PK 都只报告正确迁移动作 |
| FTS corruption | canonical 消息保留，搜索可降级，重建有边界 |
| concurrent writer | 不回归 `database is locked` 修复和 WAL fallback |
| import/export | 大小上限、字段白名单、失败可重试，原库不被破坏 |
| OneBot contract | 群隔离、消息 ID、媒体/显示字段和投递顺序不变 |
| Windows/Linux | Windows 编码/权限和 Linux WAL/信号路径分别验证 |

每个 Gate 使用唯一 Change ID 写入 `docs/UPDATE_LOG.md`；未通过的 Gate 不得推进下一个 Gate。

## 8. 当前 v26 结构合同进度（2026-08-31）

- `UPG-DB-064` 已新增 `core/hermes_state_v26_compat.py`：固化上游 v26 核心表/列合同和本地 v11 基线，提供纯 `schema_delta_from_v11()`、`v26_schema_contract()` 以及只读 `probe_v26_schema()`。
- `SessionDB.probe_v26_compatibility()` 只打开目标文件的 SQLite read-only URI，返回 `v26_ready`、`legacy_v11`、`unknown_schema` 或 `unreadable`；不会执行 DDL、迁移、FTS rebuild、WAL checkpoint 或版本号写入。
- 当前 v11→v26 差异已明确为：7 张新增核心表（`system_prompts`、`session_model_usage`、`gateway_routing`、`gateway_hygiene_state`、`compression_locks`、`session_turn_leases`、`async_delegations`），以及 `sessions`/`messages` 的扩展列；FTS virtual table、触发器、lineage backfill 和 PK heal 仍是独立写入门禁。
- 验证：v26 contract/schema probe/common helper `21 passed, 1 warning`；warning 仍为预存 `core/tools/skills_guard.py:627` 非法转义。没有读取生产数据库，未执行 v26 migration。
- 下一门禁：使用脱敏历史 SQLite 副本分别运行 v11/v26 probe、schema/search/export 回放，再设计可回滚的 mixin/迁移步骤；在此之前不修改 `SCHEMA_VERSION`，不启用上游 `SessionSchemaMixin`。

## 9. Canonical module symbol parity（2026-09-01）

`UPG-DB-084` 先建立了四个上游模块名对应的本地 compatibility port。下面的数量是基于上游 v0.20.6 与本地工作树的顶层 `def`/`class`/常量声明清单，不代表方法已逐一替换；“已对齐”只表示入口、约束或委托语义已经有明确测试。

| 模块 | 上游声明数 | 本地 port 声明数 | 当前已对齐 | 明确未融合 | 下一门禁 |
|---|---:|---:|---|---|---|
| `hermes_state_common.py` | 23 | 17 | `escape_like`、SQL/lineage helper、版本标签、local schema/FTS lazy boundary | v26 `SCHEMA_SQL`、FTS DDL、完整 preview/marker 常量仍由本地 facade 保有 | 在副本 replay 后逐项移植纯 helper，并为每个常量建立输出契约 |
| `hermes_state_schema.py` | 22 | 14 | `schema_read_probe_statements`、内存 schema parser、受控 authorizer、显式 FTS host hooks | v26 `_init_schema`、PK heal、FTS recovery、列 reconcile 尚未接管 facade | 先验证历史副本的 schema delta，再做单一 additive migration |
| `hermes_state_search.py` | 41 | 9 | 有界 sanitizer、CJK 检测、pagination bounds、显式 search hook | 上游 FTS rebuild/merge、anchored view、rich result projection 仍由本地实现负责 | 比较 v11 FTS/LIKE/CJK 结果后再接入一个纯 search helper |
| `hermes_state_portability.py` | 21 | 8 | bounded export audit、dry-run import、显式 export/import hooks | rich row、lineage adoption、完整上游 importer 尚未接管；v11 写入仍由 compatibility gate 控制 | 双平台副本回放、字段投影/失败保留证据齐全后再扩大写入口 |

### 9.1 组合与兼容规则

- 四个 port 导入时不打开 SQLite、不执行 DDL、不改变 `SCHEMA_VERSION=11`；任何需要本地 schema/FTS 文本的调用必须经过显式 lazy accessor。
- `SessionDB` facade 继续是生产入口；canonical port 不通过继承覆盖现有方法，避免同名 mixin 改变 OneBot transcript、FTS 返回 shape、WAL retry 或 v11 import 行为。
- 当前测试只证明模块边界和委托合同：`tests/hermes_state/test_canonical_modules.py` 加既有 common/schema/search/portability 集合为 `97 passed, 1 warning`；它不能证明 v26 migration、跨进程 WAL 或真实历史库恢复。
- 下一阶段固定顺序为：授权脱敏副本 hash/integrity -> Windows/Linux probe/replay -> 单 helper 对照 -> 单 mixin 方法接入 -> rollback/lock/WAL 回归；任一步失败都保持当前 facade 和版本号。
- 具体字段/表的来源、默认值、回填风险和固定 replay 顺序见 `docs/SESSIONDB_V26_MIGRATION_MAP.md`；该文档是实施映射，不是生产迁移授权。
- `SEC-REVIEW-067` 已对稳定快照复核 v26 probe/contract 与 v11 import 边界，结果 `0 reportable findings`；只读探针和受控导入仍未替代真实 v26 migration。
- `UPG-DB-069` 已加入 `hermes_state_replay.py` 与 `scripts/sessiondb_replay.py`：对明确指定的脱敏副本输出 hash/WAL/quick-check/schema/v26 plan、bounded export audit、canonical search 摘要、import dry-run 和 rollback unchanged evidence；当前 runtime `state.db`、symlink 和源覆盖均拒绝。
