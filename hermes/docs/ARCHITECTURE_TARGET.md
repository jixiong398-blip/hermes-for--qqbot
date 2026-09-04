# Hermes QQ Bot 目标架构与上游融合蓝图

> 文档性质：维护者架构基线
>
> 文档状态：设计冻结草案，指导后续实现与三方合并
>
> 适用范围：Hermes 上游 v0.20.6 与本地 QQ Bot 魔改版的长期融合
>
> 原则：本文件只定义架构、边界、迁移顺序和验收规则；老旧代码优化只登记计划，不在本阶段执行。

## 1. 这份文件要解决什么

本项目同时承担两个责任：

1. 作为 Hermes Agent 的本地发行版，吸收上游的推理、工具、生命周期、持久化和插件能力。
2. 作为 QQ 群 AI 机器人产品，保留 OneBot/NapCat、自研记忆、角色人格、媒体投递、Live2D、Dashboard 和 Windows 分发能力。

这两个责任不能通过“把两个目录直接覆盖成一个目录”来解决。上游和本地代码在以下边界上都有深度修改：

- Agent 对话循环和重试策略；
- Gateway 会话、队列、中断和重启生命周期；
- SessionDB schema、FTS、WAL 和锁恢复；
- MemoryProvider 与本地多层记忆；
- 平台适配器和消息投递；
- environments、工具注册和 CLI 配置；
- Windows 编码、脚本和本地运行目录。

因此，本项目的目标不是逐文件复制，而是建立一个可以长期演进的系统：上游负责通用引擎基础设施，本地代码负责 QQ 产品能力，二者通过稳定契约连接。

## 2. 架构总决策

最终系统采用“通用引擎 + 本地产品层 + 明确端口”的结构：

```text
                         Product Policy Layer
              QQ 身份、权限、触发规则、角色、运营策略
                                  |
                                  v
                         Gateway Orchestrator
        摄入、会话、Turn 排队、租约、中断、恢复、投递、生命周期
                         /          |          \
                        /           |           \
                       v            v            v
                Agent Runtime   Persistence   Observability
                推理、工具、重试  SessionDB     事件、日志、指标
                       |            |            |
                       +------------+------------+
                                    |
                         Capability Ports / Events
                  Provider  Tool  Environment  Memory  Platform
                       |            |            |        |
                       v            v            v        v
                 上游 Provider   工具注册   环境后端   记忆编排   平台插件
                                                       |
                                         OneBot/NapCat、其它平台
```

### 2.1 归属原则

| 能力 | 主要归属 | 融合方式 |
|---|---|---|
| Provider、模型请求、工具执行、通用重试 | 上游 Hermes | 以上游实现为主，通过本地兼容层接入 |
| OneBot/NapCat 摄入与 QQ 投递 | 本地产品层 | 保留现有插件，遵循平台端口 |
| STM/EPI/LTM/Workflow/Wiki | 本地产品层 | `UnifiedMemoryGateway` 继续是默认真相 |
| SessionDB 外壳与本地数据语义 | 本地兼容层 | 吸收上游拆分和修复，不改变外部语义 |
| Gateway 生命周期基础设施 | 融合层 | 采用上游状态模型，保留本地 QQ 行为 |
| Live2D、Dashboard、NapCat 分发 | 本地发行层 | 与核心引擎解耦，独立验证 |
| environments 与跨平台终端后端 | 上游能力 + 本地扩展 | 通过 capability 和兼容入口迁移 |
| 旧代码重构和性能优化 | 后续维护阶段 | 只登记，不混入本次融合 |

### 2.2 不可破坏的约束

- Linux 生产环境和 Windows QQ 客户端都必须保留。
- `plugins/platforms/onebot/` 是当前 QQ 接入的权威实现，不能被旧版 fallback 覆盖。
- 本地多层记忆的数据格式和可读性必须保持；不强制迁移到外部 MemoryProvider。
- 线上会话不能因为升级而丢失；所有清空内存的生命周期操作都必须有恢复路径。
- 任何包含服务器信息、真实 QQ 数据、API Key 或本机绝对路径的维护资料不得进入公开分发内容。
- 双 git 职责不能混淆：核心同步与发行分发分别验证、分别记录。

## 3. 当前基线与目标状态

### 3.1 当前基线

- 本地核心基线：v0.14.x 系列的魔改 Hermes。
- 上游目标：Hermes v0.20.6 级别的能力和生命周期契约。
- 本地主要定制：OneBot/NapCat、QQ 工具、自研记忆、Live2D、Dashboard、Windows 脚本。
- 代码形态：部分上游平台目录已清理，活动 OneBot 位于插件目录；核心单体文件仍保留大量本地行为。

### 3.2 已经具备的融合基础

以下能力可以作为后续合并的稳定地基：

- 重复输出检测和空响应防护；
- 结构化错误 surface 和失败分类传播；
- 代理错误的状态码保留与用户可见文本脱敏；
- 关机 pending spool、活跃 agent 尾部保存和启动恢复；
- Windows/Linux 编码和文件边界检查；
- 现有 OneBot 触发、群状态、媒体和记忆链路。

这些能力说明本地版本已经开始建立上游所需的生命周期边界，但不代表 v0.20.6 已经完成融合。

### 3.3 目标状态

完成后应满足：

1. Gateway 可以用统一状态机管理消息、Turn、会话、投递和恢复。
2. Agent Runtime 可以替换内部实现，而不要求 OneBot、记忆和 Dashboard 同步重写。
3. SessionDB 对外仍提供稳定 facade，对内可以采用上游 mixin 和修复机制。
4. 本地记忆可以作为默认 backend，外部 MemoryProvider 只是可选端口。
5. 平台配置不会出现“已配置但没有注册适配器”的假连接状态。
6. 每个融合切片都有来源、行为、测试、风险和回滚记录。

## 4. 稳定边界契约

下面的对象是架构目标，不要求一次性全部实现。后续代码应逐步让现有路径产生这些形状。

### 4.1 MessageEnvelope

```text
MessageEnvelope {
  message_id: string | null
  platform: string
  chat_id: string
  thread_id: string | null
  sender_id: string | null
  sender_name: string | null
  chat_type: dm | group | channel | thread
  text: string
  media: [MediaRef]
  reply_to: string | null
  timestamp: number
  flags: {
    mentioned: bool
    poke_wake: bool
    internal: bool
  }
}
```

约束：

- Gateway 不读取平台原始 payload；CQ 码、NapCat 字段和平台特殊事件只在插件内转换。
- `platform + chat_id + thread_id + isolation policy` 决定会话路由。
- 原始 payload 只能进入受控调试日志，不得直接进入模型上下文或用户响应。

### 4.2 SessionRef

```text
SessionRef {
  session_key: string
  session_id: string
  platform: string
  chat_id: string
  thread_id: string | null
  user_scope: string | null
  lineage: {
    parent_session_id: string | null
    reason: reset | compression | branch | resume | null
  }
}
```

约束：

- `session_key` 是路由键，`session_id` 是持久化键，二者不能混用。
- SessionDB、Memory 和 Delivery Ledger 使用 `session_id`；Gateway 队列和锁使用 `session_key`。
- session split 必须产生 lineage 记录，不能静默替换 ID。

### 4.3 TurnRequest / TurnOutcome

```text
TurnRequest {
  session: SessionRef
  message: MessageEnvelope
  history: [Message]
  route: ProviderRoute
  capabilities: CapabilitySnapshot
  deadline: number | null
}

TurnOutcome {
  final_response: string | null
  messages: [Message]
  completed: bool
  failed: bool
  interrupted: bool
  partial: bool
  failure_reason: string | null
  failure_retryable: bool | null
  status_code: int | null
  error_surface: ErrorSurface | null
  persistence: PersistenceStatus
  delivery: DeliveryIntent | null
}
```

约束：

- `completed=True` 只能表示成功完成的可见回答；`(empty)` 等 sentinel 不能再被遥测视为成功。
- 重试策略属于 Agent Runtime，是否把结果发给平台属于 Gateway。
- 失败结果必须保留稳定的 `failure_reason`，不能只依赖拼接后的异常字符串。

### 4.4 ProviderResult

```text
ProviderResult {
  content: string | null
  reasoning: string | null
  tool_calls: [ToolCall]
  finish_reason: string | null
  usage: Usage | null
  provider: string
  model: string
  status_code: int | null
  stream_state: complete | partial | empty | dropped | unknown
}
```

Provider 适配器只能返回这个边界对象，不得让 Gateway 依赖 OpenAI、Anthropic 或其它 SDK 的异常类型。

### 4.5 DeliveryIntent

```text
DeliveryIntent {
  session_id: string
  platform: string
  chat_id: string
  thread_id: string | null
  text: string
  media: [MediaRef]
  reply_to: string | null
  idempotency_key: string
}
```

投递必须区分：

```text
created -> attempting -> accepted -> confirmed
                         |           |
                         v           v
                       failed     uncertain
```

`uncertain` 不能直接当成失败重发，也不能直接当成成功；需要结合平台能力和幂等键决定后续动作。

## 5. Gateway 生命周期模型

### 5.1 Gateway 状态

```text
created
  -> starting
  -> running
  -> draining
  -> stopped

starting -> startup_failed
running  -> failed
draining -> forced_exit
```

所有状态转换必须写入统一生命周期事件。`stop()` 的顺序固定为：

1. 标记 `draining`，阻止新 Turn 进入。
2. 通知活跃会话。
3. 等待活跃 Turn 在预算内结束。
4. 中断超时 Turn，并保存未落盘尾部。
5. 在清空 adapter/runner 队列前写入 shutdown spool。
6. 取消后台任务，断开平台适配器。
7. 关闭 SessionDB、释放锁和进程资源。
8. 写 clean-shutdown 或 forced-exit 证据。

启动顺序固定为：

1. 建立 profile 和配置上下文。
2. 恢复生命周期状态和未完成投递证据。
3. 初始化插件 registry。
4. 连接平台适配器并确认 `registered + configured + connected` 三态。
5. 以有界后台任务回放 shutdown spool。
6. 建立 channel directory、cron 和监控。
7. 标记 `running`。

### 5.2 Turn 状态

```text
queued -> admitted -> running -> waiting_tool -> running
                         |             |
                         v             v
                     interrupted    failed
                         |
                         v
                     resumable
```

同一 `session_key` 同时只能有一个 active Turn。后续消息进入 pending queue，由 Gateway 决定合并、排队或作为中断输入。

### 5.3 Session 状态

```text
active -> idle -> expired -> finalized
   |                    |
   v                    v
resume_pending       reset_required
   |
   v
resumed
```

`suspended` 只用于确认存在连续重启/异常恢复风险的会话。它不是普通失败标志，不能由单次 provider 错误直接触发。

## 6. 模块职责与依赖方向

### 6.1 Platform Plugin

平台插件负责：

- 原始事件接收和 `MessageEnvelope` 生成；
- 平台权限、@ 规则、群状态和平台特有命令；
- 文本、图片、文件、语音投递；
- 平台能力声明和连接检查；
- 平台级日志字段和错误转换。

OneBot/NapCat 的目标形态：

```text
OneBotPlugin
├── ingress.py / adapter.py
├── trigger_coordinator.py
├── group_state.py
├── group_executor.py
├── media_pipeline.py
├── semantic_judge.py
└── capability declaration
```

Gateway 不应知道 CQ 码、NapCat action 名称或 OneBot 群事件字段。

### 6.2 Gateway

Gateway 负责：

- `MessageEnvelope -> SessionRef`；
- Turn admission、session lock、lease 和 pending queue；
- 调用 Agent Runtime；
- 持久化与记忆同步的时序；
- DeliveryIntent 生成、投递确认和重启恢复；
- 生命周期状态、shutdown、heartbeat 和 runtime status。

Gateway 不负责：

- 解析平台原始 payload；
- 直接调用 provider SDK；
- 直接拼接平台特有用户提示；
- 用字符串推断所有失败类型。

### 6.3 Agent Runtime

Agent Runtime 负责：

- provider request/response；
- 工具调用循环；
- continuation、compression、fallback 和 retry；
- tool guardrail；
- 生成 `TurnOutcome`。

拆分顺序应是：

```text
现有 run_agent.py
  -> 稳定 TurnOutcome
  -> turn_context / provider_projection
  -> tool_executor
  -> conversation_loop
  -> turn_finalizer
```

在此之前不应直接以上游 `conversation_loop.py` 覆盖本地 `run_agent.py`。

### 6.4 Persistence

SessionDB 采用 facade + 内部模块：

```text
SessionDB Facade
├── schema / migration
├── transaction / lock policy
├── message repository
├── FTS search and repair
├── session lineage
├── import/export
└── recovery / forensic diagnostics
```

外部调用继续依赖 `SessionDB`。上游的 `hermes_state_common.py`、`hermes_state_schema.py`、`hermes_state_search.py` 和 `hermes_state_portability.py` 作为内部实现候选，不能直接改变现有数据语义。

### 6.5 Memory Orchestrator

```text
Memory Orchestrator
├── BuiltinMemoryBackend
│   ├── STM
│   ├── EPI
│   ├── LTM
│   ├── Workflow
│   └── Wiki / Obsidian
└── Optional Provider Adapter
    ├── Honcho
    ├── Hindsight
    ├── Mem0
    └── other providers
```

本地 `UnifiedMemoryGateway` 仍是默认 backend。上游 `MemoryProvider` 只通过以下端口接入：

- `initialize()`；
- `prefetch()`；
- `sync_turn()`；
- `on_pre_compress()`；
- `on_session_switch()`；
- `shutdown()`。

外部 provider 失败时必须回退到本地记忆，而不是阻断 QQ 主对话。

## 7. 配置和能力模型

### 7.1 配置优先级

统一采用：

```text
显式运行时覆盖
    > 当前 profile 配置
    > ~/.hermes/.env / profile secrets
    > 项目模板默认值
```

每个字段都要标注：

- 来源；
- 是否允许运行时覆盖；
- 是否属于 secret；
- 是否跨 profile；
- 失败时是拒绝启动、降级还是仅警告。

### 7.2 平台三态

平台状态至少分成：

```text
configured   配置中存在有效字段
registered   registry 中存在 adapter/plugin
connected    adapter 已成功建立运行连接
```

只有 `configured && registered && connected` 才能进入正常投递路径。旧平台配置如果没有实现，应显示迁移提示并被标记为 disabled/unsupported，不能假装 connected。

### 7.3 CapabilitySnapshot

Provider、平台、环境和记忆 provider 都应声明能力，而不是让核心代码堆积特殊判断：

```text
CapabilitySnapshot {
  platform: {
    supports_threads: bool
    supports_message_edit: bool
    supports_media: bool
    supports_system_messages: bool
    max_message_length: int | null
  }
  provider: {
    api_mode: string
    supports_tools: bool
    supports_vision: bool
    supports_reasoning: bool
    context_length: int | null
  }
  environment: {
    backend: string
    persistent: bool
    supports_shell: bool
  }
  memory: {
    builtin: bool
    external: bool
    supports_prefetch: bool
    supports_pre_compress: bool
  }
}
```

## 8. 上游能力吸收矩阵

| 上游能力 | 处理策略 | 当前阶段 |
|---|---|---|
| repetition guard | 直接吸收，保留本地 continuation 规则 | 已落地 |
| empty response guard | 直接吸收，保留本地成本和 fallback 逻辑 | 已落地 |
| error surface / errors | 直接吸收并接入本地结果边界 | 已落地 |
| shutdown flush | 适配本地 SessionDB 和 OneBot 队列 | 已落地 |
| shutdown watchdog | 围绕统一 Gateway 生命周期接入 | 待下一切片 |
| lifecycle ledger / heartbeat | 与 shutdown 状态统一，不另建状态真相 | 合同已落地，Gateway 主路径待接入 |
| turn lease / turn context | 先建立 Gateway lease，再迁移 Agent 内部调用 | lease/TurnContext 合同已落地，Agent 主循环待接入 |
| session stall / session state | 与现有 session store 三态合并 | 模块合同已落地，Gateway 主路径待接入 |
| delivery ledger | 等平台三态和 DeliveryIntent 冻结后接入 | ledger 合同已落地，平台 DeliveryIntent 主接入待做 |
| SessionDB mixin split | 三方合并，保留本地数据语义和锁修复 | Gate 0–5 前置与兼容 port 已完成，历史/Linux 后再接管 |
| MemoryProvider lifecycle | 作为可选适配器接入本地 Memory Orchestrator | 兼容 port 已落地，主生命周期接入待做 |
| tools/environments | 兼容迁移，不删除本地终端入口 | E1/E4 合同已落地，真实 backend 仍待测 |
| provider/auth expansion | 选择性吸收，保留本地 credential pool | 待实施 |
| conversation_loop extraction | 最后实施，禁止整文件覆盖 | API-copy/timestamp/sidecar seam 已落地，完整抽取仍为最后阶段 |
| pet、billing、relay、无关 UI | 默认不纳入 QQ 发行版 | 明确跳过 |

## 9. 融合实施阶段

### 阶段 A：架构冻结

目标：冻结本文定义的边界对象、状态机、配置优先级和日志格式。

入口条件：

- 双 git 工作区状态已记录；
- 上游版本和本地 fork 基线可复现；
- 生产数据备份策略已确认。

出口条件：

- `MessageEnvelope`、`SessionRef`、`TurnOutcome`、`DeliveryIntent` 字段不再临时变更；
- 每个核心模块有明确 owner；
- 冲突文件被分为基础设施、边界文件和普通文件。

### 阶段 B：可靠性基础

吸收重复输出、空响应、错误面、shutdown spool、watchdog、heartbeat 和基础 forensics。

出口条件：

- 正常、失败、空响应、重复输出的结果都可被结构化消费；
- 关机不再无条件清空 pending 消息；
- 启动恢复有独立预算，不阻塞事件循环；
- Windows 和 POSIX 的文件边界规则有对应测试。

### 阶段 C：Gateway 生命周期统一

吸收 turn lease、session state、session stall、delivery ledger 和 restart recovery。

出口条件：

- 一个 session 同时只有一个 active Turn；
- 中断、排队、重启、恢复和投递确认使用同一套状态；
- 所有清理路径都是幂等的；
- 失败重试不会造成重复投递或重复持久化。

### 阶段 D：QQ/OneBot 边界收口

目标：让 OneBot 成为唯一 QQ 实现，清除旧 QQBot phantom state。

工作内容：

- 旧 `Platform.QQBOT` 配置只作为迁移哨兵，不参与 connected 判定；
- CLI、cron、toolset 和 `send_message` 统一使用 OneBot registry/adapter；
- OneBot 声明消息、媒体、线程、权限和 standalone send 能力；
- 旧官方 QQ Bot REST 路径提供明确迁移提示或彻底禁用；
- 增加配置到 registry 到 adapter 的启动烟测。

出口条件：

- 旧 QQBot 配置不会导致“已连接但无 adapter”；
- `send_message` 不会绕过 OneBot 重新访问已删除的官方接口；
- OneBot 群聊、图片、管理员命令和 cron 投递仍保持现有行为。

### 阶段 E：SessionDB 三方合并

合并顺序：

1. 以本地 `SessionDB` facade 和数据 schema 为基准。
2. 引入上游 common/schema 模块，先只搬内部常量和纯函数。
3. 引入 search/FTS 修复，逐项保留本地 `_rebuild_fts` commit 语义。
4. 引入 portability、lineage、repair fingerprint 和 WAL 诊断。
5. 通过兼容测试确认 JSONL、SQLite、多模态和 session split 行为不变。

出口条件：

- 旧数据库可以被新代码读取；
- FTS 锁和恢复行为不回归；
- Windows/Linux 的 SQLite 测试都通过；
- 所有旧外部调用仍通过 facade 工作。

### 阶段 F：MemoryProvider 桥接

先实现 `BuiltinMemoryProviderAdapter`，把 `UnifiedMemoryGateway` 包装成上游 `MemoryProvider`，再考虑外部 provider。

必须验证：

- prefetch 能注入上下文；
- sync_turn 能写入 STM；
- memory tools 能正常路由；
- 历史 SQLite 数据可读；
- provider 失败时退回本地记忆；
- compression 和 session switch 不会写错 session。

### 阶段 G：Environments、工具和 CLI

把顶层 environments 的本地能力逐项映射到上游 `tools/environments`，保留旧入口作为兼容壳。每迁移一个 backend，都要验证 Windows 进程、Linux shell、权限和 cleanup 行为。

### 阶段 H：Conversation Loop 拆分

只有前面阶段通过后，才拆分 `run_agent.py`：

```text
run_agent.py
  -> agent_init.py
  -> turn_context.py
  -> provider_projection.py
  -> conversation_loop.py
  -> tool_executor.py
  -> turn_finalizer.py
```

每次只移动一个职责，并保持 `run_conversation()` 外部签名和 `TurnOutcome` 结构不变。

## 10. 三方合并方法

### 10.1 三个版本

每个冲突文件都建立三方参照：

```text
BASE   = 上游 v0.13.0 fork 锚点
OURS   = 本地当前魔改版本
THEIRS = 上游 v0.20.6
```

分别计算：

```text
our-diff = BASE -> OURS
up-diff  = BASE -> THEIRS
```

### 10.2 分类

| 分类 | 处理 |
|---|---|
| OUR-ADD | 保留本地新增，确认不违反上游新接口 |
| UP-ONLY | 采用上游实现 |
| CONFLICT | 以边界契约为准手工合并 |
| UP-RENAME | 跟随上游路径，再建立本地兼容入口 |
| DELETE-REVIEW | 判断删除是否会破坏本地产品能力 |

### 10.3 高风险文件规则

- `run_agent.py`：只做局部移植，不整文件替换。
- `gateway/run.py`：先合并状态和生命周期，再合并平台分支。
- `hermes_state.py`：先保护 facade 和数据库语义，再拆内部模块。
- `model_tools.py` / `toolsets.py`：先保护工具注册和 QQ 工具，再吸收上游 registry。
- `config.py` / `hermes_cli`：先冻结配置优先级，避免环境变量和 YAML 互相覆盖。
- `plugins/platforms/onebot/`：视为本地产品代码，以上游接口适配为主，不把上游平台实现覆盖进来。

## 11. 更新日志和决策记录规范

长期维护使用三层记录：

```text
UPGRADE_PLAN.md   未来要做什么，以及依赖和验收条件
UPDATE_LOG.md     已实际完成什么，以及测试和风险
DECISION_LOG.md   为什么采用或拒绝某个架构选择
```

每个实际切片必须有唯一 Change ID：

```text
Change ID: UPG-<AREA>-<NNN>
日期:
上游来源: tag / commit / module
本地影响范围:
架构目的:
保留的本地行为:
新增行为:
配置或数据迁移:
测试命令与结果:
已知预存失败:
安全审查结果:
回滚方式:
后续动作:
```

推荐的事件日志字段：

```text
event_name
event_version
timestamp
process_id
profile
session_key_hash
session_id
platform
turn_id
outcome
failure_reason
duration_ms
source_change_id
```

日志原则：

- 结构化字段优先于拼接长字符串；
- secret、原始 token、完整 URL 查询参数和用户私密内容不进入普通日志；
- 运行日志记录“发生了什么”，决策日志记录“为什么这样设计”；
- 测试失败要区分本次引入、预存失败和环境缺失；
- 每次修改完成后先更新 `UPDATE_LOG.md`，再进行发布或双 git 同步。

## 12. 测试和验收矩阵

### 12.1 每个切片的最低验证

```text
py_compile / AST parse
相关单元测试
关键集成测试
导入冒烟
git diff --check
隐私扫描
```

### 12.2 Windows

- `hermes_bootstrap` 必须先于其它核心 import；
- UTF-8 stdio、`.bat`、portable Node、NapCat 和 Dashboard 启动链路；
- OneBot WS/HTTP、图片、群状态和管理员权限；
- SQLite WAL、文件 ACL、junction/symlink 和进程清理；
- 对明确 POSIX-only 的 pty、signal、cgroup 测试使用条件跳过，但核心行为不能跳过。

### 12.3 Linux

- 全量核心 agent、gateway、SessionDB、memory 和 OneBot 插件测试；
- 真正的 WAL/FTS 锁恢复和 shutdown/restart；
- provider fallback、stream drop、delivery retry；
- systemd/服务重启和异常退出证据；
- 不把“本地 Windows 通过”当作 Linux 生产证明。

### 12.4 发布验收

只有以下条件同时满足，才可称为“完成一次上游融合发布”：

- 当前目标版本、基线和变更清单可复现；
- 所有高风险文件都有三方合并记录；
- OneBot、记忆、SessionDB、Live2D 和 Dashboard 的保留行为有测试证据；
- Linux/Windows 必测矩阵完成；
- `UPDATE_LOG.md`、`DECISION_LOG.md` 和公开 changelog 已分层更新；
- 安全扫描没有未处理的高风险发现；
- 双 git 状态、回滚点和发布说明完整。

## 13. 老旧代码优化清单（只规划，不在融合阶段执行）

以下项目列入后续维护路线，当前不得与上游融合混做：

1. 拆分超大 `run_agent.py`，减少跨职责共享状态。
2. 拆分 `gateway/run.py`，把平台路由、生命周期和业务命令分离。
3. 统一同步/异步 HTTP client 的创建、关闭和超时策略。
4. 用 capability registry 替代 provider/platform 的散落条件分支。
5. 将多组 session 字典迁移到显式 `SessionState`，减少清理遗漏。

## 14. NapCat 账号与实例边界（2026-09-01）

当前生产部署固定为“一台机器一个 Hermes Bot 实例”。NapCat 登录后生成的账号专属 `onebot11_<uin>.json` 由 Dashboard 选择器和 OneBot adapter 自动发现；选择只持久化 `ONEBOT_SELF_ID`，token 留在本机进程内，不通过 Dashboard API 返回。

未来多账号/多 NapCat/多 Hermes 必须显式建模 `NapCatAccount`、`NapCatInstance`、`HermesBotInstance` 和 `OneBotBinding`，分别拥有账号配置、进程/PID/端口、Hermes profile/SessionDB/Memory scope 和绑定状态。不同实例不得共享 WS/HTTP 端口、Gateway lock、SessionDB、delivery ledger 或记忆命名空间。

多实例编排目前只进入长期计划，具体阶段、API、隔离约束和进入门禁见 `docs/NAPCAT_MULTI_INSTANCE_PLAN.md`。在 Windows/Linux 独立进程、端口/WAL、profile/memory 隔离和回滚证据齐全前，不启动多 NapCat 进程、不改变现有 v11 数据、不把上游多平台 routing 当作 QQ 实例隔离。
6. 统一错误分类、用户提示、日志和遥测字段。
7. 给 SessionDB 增加 repository 层和可观测事务指标。
8. 将媒体下载、压缩和发送改为可取消、可恢复的任务图。
9. 清理历史兼容入口，先加迁移告警，再删除死代码。
10. 补齐类型标注、协议对象和跨平台路径抽象。
11. 建立真实 provider/OneBot 的 contract test，而不只依赖 mock。
12. 对长消息、重复响应、工具失败和数据库锁建立长期回归样本库。

每项优化都必须等融合发布稳定后，单独建立 Change ID、性能基线、回滚点和测试计划。

## 14. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| 上游核心重构覆盖本地行为 | 高 | 先冻结边界，逐切片三方合并 |
| SessionDB schema/FTS 语义变化 | 高 | facade、迁移测试、备份和双读校验 |
| 外部 MemoryProvider 接管本地记忆 | 高 | 本地 backend 保持默认，provider 只做可选适配器 |
| QQ 平台 phantom state | 高 | configured/registered/connected 三态和启动烟测 |
| shutdown 恢复线程残留 | 中 | 有界预算、可观测线程、下一次启动重试 |
| Windows/Linux 行为分叉 | 高 | 双平台必测矩阵和能力条件分支 |
| 依赖锁定导致离线安装失败 | 中 | 生成兼容依赖清单，保留本地必要依赖 |
| 文档和代码状态漂移 | 中 | 每切片先写 UPDATE_LOG，再同步双 git |
| 大量冲突导致误删定制资产 | 高 | 变更清单、路径保护、删除前静态引用扫描 |

## 15. 下一步执行顺序

按以下顺序推进，不跳阶段：

1. 固化本文件作为架构基线，并在 `UPDATE_LOG.md` 记录 Change ID。
2. 完成 QQBot 残留入口清理，确保 OneBot 是唯一 QQ 运行路径。
3. 将 shutdown watchdog、lifecycle ledger 和 heartbeat 接入统一 Gateway 状态机。
4. 建立 turn lease、session state 和 delivery ledger 的最小契约。
5. 开始 SessionDB 三方合并：先 facade/common，再 schema/search/portability。
6. 实现 `BuiltinMemoryProviderAdapter`，做数据只读和降级验证。
7. 迁移 environments 和工具入口，保留兼容壳。
8. 最后拆分 conversation loop，并以 `TurnOutcome` 做行为对照。
9. 完成 Linux/Windows 矩阵、隐私审查、双 git 检查和发布记录。

## 16. 本文件自身的变更记录

| Change ID | 内容 | 状态 |
|---|---|---|
| ARCH-000 | 建立 Hermes 上游融合目标架构、边界契约、状态机、阶段顺序和记录规范 | 本文件初版 |

本文件不宣称上游融合已经完成；它是后续每个代码切片的架构约束和审查依据。
