# Agent Runtime 三方融合矩阵

> 状态：2026-09-01 阶段性架构基线。
>
> 参与方：本地 Hermes QQ Bot 魔改版、fork 锚点 v0.13、上游 NousResearch/hermes-agent v0.20.6。
>
> 原则：上游 Agent Runtime 只能按职责和结果契约增量融合，禁止用上游 `conversation_loop.py`、`agent_init.py` 或其它大文件覆盖本地 `run_agent.py`。

## 1. 现状规模

| 区域 | 本地状态 | 上游 v0.20.6 | 合并结论 |
|---|---|---|---|
| 主循环 | `core/run_agent.py` 约 824 KB，包含 provider fallback、工具循环、压缩、OneBot/记忆回调和本地错误修复 | `agent/conversation_loop.py` 约 8,161 行，职责已拆分 | 以本地循环和 `TurnOutcome` 为权威，先抽取 seam，不做文件替换 |
| 回合前置 | 内嵌在 `run_conversation()`，与本地 SessionDB、MemoryProvider、上下文注入耦合 | `agent/turn_context.py` 约 1,394 行、`agent/agent_init.py` 约 2,921 行 | 先只读对照和 side-effect contract，再逐段移出 |
| 工具执行 | 主循环直接调用本地 `model_tools`/tool guardrail | `agent/tool_executor.py` 约 2,710 行 | 先稳定工具结果、重试和 interrupt 语义，再提取 executor |
| 回合收尾 | 本地持久化、trajectory、错误面、记忆同步集中在 `run_agent.py` | `agent/turn_finalizer.py` 约 786 行 | 以现有 `error_surface`、`empty_response_guard`、`repetition_guard` 为边界逐步抽取 |
| provider 投影 | 本地有 Codex/ACP 特殊路径，但无统一模块 | `agent/provider_projection.py` 约 55 行 | 需要先补本地 message metadata 端口，不能直接复制 import |
| 消息清洗 | 本地 `run_agent.py` 已有 surrogate、非 ASCII、图片和工具参数清洗；现已由 `agent/message_sanitization.py` 惰性暴露 | `agent/message_sanitization.py` 约 815 行 | 仅移植本地已有清洗函数，不重复清洗或改变 transcript；provider-specific policy 仍未接入 |
| 资源控制 | 本地已有 output spill、iteration budget、interrupt/steer 契约 | `agent/deadline.py`、`agent/estop.py`、`agent/battery.py` 为上游独立模块 | deadline/estop 可单独评估；battery 仅为 UI 能力，不进入 QQ 主链路 |

## 2. 模块级映射

| 上游模块 | 本地对应 | 风险 | 当前动作 | 进入条件 |
|---|---|---|---|---|
| `provider_projection.py` | Codex/ACP response 处理、`run_agent.py` provider 特殊分支 | 可能重复追加已完成 tool rows，或破坏 SessionDB transcript | `UPG-AGENT-104` 已落地无副作用 compatibility port；默认不接主循环 | 建立 bounded projected-message schema、去重 key 和 persistence test 后，再评估局部 wiring |
| `turn_context.py` | `run_agent.py` 回合前置、`context_compressor.py`、MemoryProvider | API sidecar、memory prefetch、session 建立顺序变化 | 保留本地 inline path | 固定 `TurnContext` 字段、前置副作用顺序和失败回退 |
| `agent_init.py` | `AIAgent.__init__`、provider/profile 初始化 | 参数数量和 QQ 运行时回调不兼容 | 不复制 | 先生成签名/属性 parity 清单，再按只读属性拆分 |
| `message_sanitization.py` | `core/agent/message_sanitization.py` -> `run_agent.py` 已验证 helper | 清洗两次、role alternation 或多模态内容丢失 | `UPG-AGENT-088` 已落地惰性 compatibility port；默认主循环不变 | 每种 content/tool-call/Unicode 输入都有同形状回归；provider call-id/reasoning policy 另立门禁 |
| `tool_executor.py` | `model_tools.py`、`run_agent.py`、tool guardrail | approval、环境 backend、并行策略和 iteration budget 交叉 | 不接主路径 | 先稳定 `ToolResult`、interrupt、timeout、spill 和错误面 |
| `conversation_loop.py` | `AIAgent.run_conversation()` | 最大；会同时影响 provider、memory、OneBot 和压缩 | 禁止整文件覆盖 | 只有前置/执行/收尾三个 seam 各自有独立契约后才考虑局部抽取 |
| `turn_finalizer.py` | 本地持久化、trajectory、delivery/error surface | 失败状态、partial response 和 memory sync 顺序改变 | 先复用已完成 guard/error 模块 | `TurnOutcome`、shutdown spool 和 memory sync 的回归完整 |
| `deadline.py` | 本地 iteration/time budget、Gateway deadline | 同时有同步 agent 和异步 Gateway，时钟语义不同 | 只做 API 对照 | 统一 monotonic deadline、provider timeout 和 user-visible failure reason |
| `estop.py` | 暂无同名模块；可接 Gateway admission | 紧急停止必须 fail-closed，不能误拦已在执行的安全收尾 | 后续独立切片 | sentinel 路径、权限失败、恢复命令和跨进程测试 |
| `battery.py` | Dashboard/TUI 状态栏（QQ 主链路无对应物） | 引入 psutil/平台差异，收益与 QQ 运行无关 | 暂不移植 | 只有桌面 UI 明确需求时评估 |
| `conversation_compression.py` | `context_compressor.py`、SessionDB lineage 和自研 memory | 上游 compression child 与本地 EPI/LTM 语义不同 | 保持本地压缩 | 先完成历史副本/lineage 对照，禁止替换 memory backend |

## 3. 不变量

- `hermes_bootstrap` 必须是核心模块的第一项 import；兼容模块不能因为导入上游 helper 而提前初始化 provider、SessionDB 或网络客户端。
- `TurnOutcome` 是 Gateway 可见的唯一回合结果边界；provider、tool、compression 内部异常必须映射到已有 `failure_reason`、`failure_retryable`、`status_code` 和 `error_surface`。
- 存储内容与 API sidecar 分离：清洗、memory 注入、provider projection 只能改变 API 副本，不能无意改写本地 canonical transcript。
- OneBot 群锁、Gateway turn lease、SessionDB transaction、compression lock 和 delivery ledger 是不同状态机；任何 Runtime 拆分不能合并这些锁或改变 shutdown 顺序。
- 上游新增能力默认关闭；第三方 provider、工具、环境 backend 或 UI 依赖失败时必须回退到现有本地实现，不阻断 QQ 对话。
- 所有新模块都要有 import smoke、bounded input、异常隔离和 Windows/Linux 条件差异测试；没有真实外部服务证据时只能标记为 offline contract。

## 4. 融合顺序

1. 完成本地 `TurnOutcome`、provider response、tool result 和 error surface 的字段 parity 清单。
2. 对 `message_sanitization` 和 `provider_projection` 做纯函数/只写入副本的最小 port，保持默认不接入。
3. 抽取 `turn_context` 的只读局部，验证 API sidecar、memory prefetch、SessionDB 建立和压缩前 hook 顺序。
4. 抽取 `tool_executor`，先复用现有 approval、environment、spill、interrupt 和 iteration budget。
5. 抽取 `turn_finalizer`，验证 delivery、trajectory、SessionDB、memory sync、shutdown spool 和 partial response。
6. 最后才评估 `conversation_loop` 局部接入；任何失败回退到本地 inline loop。
7. `deadline`/`estop` 另立资源控制切片；`battery`、LSP、monitoring 等非 QQ 主路径能力不与核心 Runtime 融合混做。

`UPG-AGENT-088` 的边界：canonical `message_sanitization` import 不加载 `run_agent`；调用时才惰性解析本地 helper。`close_interrupted_tool_sequence` 只补齐本地缺失的语义标记，不负责 SessionDB 持久化或 timestamp stamping；现有 transcript writer 仍是唯一持久化 owner。

`UPG-AGENT-104` 的边界：`provider_projection` 只接受 assistant/tool 投影行，限制行数、正文、tool-call 数量、时间戳和 provider iteration 计数；import 不加载 `run_agent`、provider client、SessionDB 或网络。它只写调用方提供的内存 list，当前不接入 ACP/Codex response 主循环，也不改变本地 transcript owner。

## 5. 验收证据

每个模块必须提供：

- 上游/本地符号和调用方清单；
- 不改变现有返回 shape 的 focused tests；
- import side-effect 和 `hermes_bootstrap` 顺序检查；
- provider/tool/memory/SessionDB/Gateway 关键路径回归；
- Windows/Linux 适用的进程、时钟、文件和信号证据；
- 唯一 Change ID、回滚说明和安全审查收据。

在 `conversation_loop`、SessionDB v26、MemoryProvider plugin discovery、OneBot live contract 和双平台 backend 证据完成前，不更新发布版本，不做生产灰度。

## 6. TurnContext 只读 seam（合同已实现，主循环未接入）

上游 `agent/turn_context.py` 中可以独立验证的部分已由 `UPG-AGENT-140`/`UPG-AGENT-142` 以 `core/agent/turn_context_contract.py` 交付；这只是数据合同和纯函数兼容端口，不是完整上游文件：

- `TurnContext` 字段合同：`user_message`、`original_user_message`、`messages`、`conversation_history`、`active_system_prompt`、`effective_task_id`、`turn_id`、`current_turn_user_idx`、`should_review_memory`、`plugin_user_context`、`ext_prefetch_cache`、`preflight_compression_blocked`。
- 纯 API sidecar 函数候选：`compose_user_api_content`、`substitute_api_content`、`drop_stale_api_content`、`extract_api_content_sidecar`、`consume_gateway_turn_context_notes`、`append_notes_to_multimodal_content`。
- 纯索引/压缩 predicate 候选：`reanchor_current_turn_user_idx`、`compression_made_progress`、`_compression_warrants_another_preflight_pass`、`_should_run_preflight_estimate`、`_should_idle_compact`、`_review_fork_first_request_pending`。

本候选不包含 `build_turn_context()` 及其 session row 创建、MCP refresh、memory prefetch、provider runtime 绑定、compression lock、checkpoint 或任何 `run_agent` 主循环 wiring。当前本地 runtime 没有 `api_content` metadata owner，不能只复制 sidecar helper 后声称 prompt-cache 语义已融合。

已完成证据：TurnContext focused `8 passed`、Agent Runtime 组合 `90 passed`、reanchor actionable-row 修正和 `SEC-REVIEW-143`。剩余进入条件：先冻结 sidecar metadata/transcript owner，并完成与本地 `run_agent` 前置顺序、MemoryProvider、SessionDB 和 provider API 的只读对照；之后才允许选择性接入一个 helper。失败时保留本地 inline prologue。

## 7. Sidecar 与 transcript owner 冻结（2026-09-01）

### 7.1 Owner 定义

- **live transcript owner**：`AIAgent.run_conversation()` 内的 `messages` 列表；它承载当前回合、工具链和压缩后的内存状态。
- **API projection owner**：每次 provider 请求构造的 `api_messages` 副本；`api_content` 只能改变这份副本，不得原地改写 canonical `messages` 的 `content`。
- **durable transcript owner**：本地 `AIAgent._persist_session()` 统一调用的 JSONL 备份与 `SessionDB` writer；Gateway 只负责传递事件时间戳和回合结果，不得另写一份 sidecar 语义。
- **metadata owner**：消息 mapping 上的 `timestamp`、`api_content`、`display_kind` 等字段由 agent/runtime 产生；SessionDB 是否能保留它们由 schema 版本和 writer capability 决定，不能靠未声明字段“顺便”落库。

### 7.2 本地 v11 的事实边界

当前本地 SessionDB v11 `messages` 表只保存 `timestamp`、reasoning/tool 等既有列，没有上游 `api_content`、`display_kind` 或 `display_metadata` 列；`SessionDB.append_message()` 默认仍使用本地 `time.time()`，但现在可接收经过边界校验的可选平台时间戳。因此 `api_content` 仍不能宣称具备上游的跨重启 prompt-cache 字节一致性。

### 7.3 分阶段接线规则

0. **Structural API-copy slice**：先用递归容器 clone 构造 provider 副本，确保 sanitizer/canonicalizer 不能反向修改 canonical transcript；该切片不生产 `api_content`、不写 SessionDB。
1. **Timestamp-only slice**：再让 Gateway/Agent 把平台事件时间戳传入当前 user mapping，并证明旧调用方默认仍使用本地 wall clock；补排序、压缩、JSONL 和 SessionDB 回归。
2. **API sidecar slice**：additive schema gate 已提供显式 v26 columns 的兼容 writer/loader；生产启用前仍需完成 sidecar 长度、surrogate、role、stale-on-rewrite 和 API projection/恢复历史逐字一致的全量回放。
3. **Selective TurnContext helper**：每次只接一个纯 helper，保留本地 inline prologue 作为 fallback；接线前必须有 provider/MemoryProvider/SessionDB transcript 对照和 import smoke。
4. **主循环抽取**：`build_turn_context()`、`conversation_loop.py`/`run_agent.py` 大文件替换和完整上游 finalizer 只有在前述阶段及真实 Windows/Linux 证据齐全后才允许评估。

### 7.4 不变量与回滚

- API-only context、memory prefetch 和 gateway ephemeral note 不能改变 canonical transcript 的用户正文；重写正文必须先丢弃 stale sidecar。
- retry、compression、interrupt、pending follow-up 和 shutdown flush 不能共用一把锁，也不能重复写同一逻辑 user turn。
- 任一 slice 失败时，删除接线和 focused tests 即回退到本地 inline path；不删除数据库、用户配置、缓存或外部资源。

本节是 owner/迁移顺序冻结；structural API-copy、timestamp-only 和 gated sidecar writer/loader slice 已实现，但 sidecar stale-on-rewrite 全量回放、生产启用和主循环抽取仍未完成。真实 provider、NapCat、Linux/WAL、生产历史副本和版本发布继续 deferred。

## 8. 本机 OneBot live gate（2026-09-01）

- 已通过本机真实回环的分层前置：使用 bot self-id 自动发现 NapCat account-specific token，OneBot HTTP `get_login_info`、forward WebSocket、Gateway event loop、私聊发送和测试群发送均返回成功回执；Dashboard 端口状态、账号列表和总状态读取也通过。
- 测试运行在一次性 Hermes home、空 SessionDB 和入站账号白名单中；未写入真实 `.env`、`state.db`、NapCat SQL 或 token，也没有连接 Linux 生产机或真实 provider。
- 该 gate 只关闭 `NapCat config -> OneBot auth -> Gateway transport -> outbound receipt`，不关闭 `user inbound -> judge -> memory -> provider -> delivery`。bot 自发消息被 adapter 忽略，不能代替用户入站；完整链路需在白名单用户发送真实测试消息后单独验收。
- 迁移规则：默认保留本地 OneBot/SessionDB/UnifiedMemoryGateway owner；live gate 通过不授权 v26 sidecar、conversation loop 全量替换、生产灰度或版本发布。
