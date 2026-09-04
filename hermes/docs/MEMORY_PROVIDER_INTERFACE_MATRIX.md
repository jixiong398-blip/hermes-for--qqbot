# MemoryProvider Interface Matrix

> 状态：融合前的接口基线，供生产实现代理和后续三方合并使用。
>
> 目标：把上游 Hermes v0.20.6 的 MemoryProvider 增量逐项映射到本地 v0.14.x 魔改版；本文件只描述边界、兼容策略和验收条件，不替代代码或测试。

## 1. 三方基线

| 维度 | 本地旧版 | 上游目标 | 融合结论 |
|---|---|---|---|
| 抽象类位置 | `core/agent/memory_provider.py` | `agent/memory_provider.py` | 保留本地导入路径，内部补齐上游兼容成员 |
| 数据真相 | `UnifiedMemoryGateway` 负责 STM/EPI/LTM | `MemoryManager` 管理一个可选外部 provider | 本地 gateway 继续默认 backend，不迁移历史 SQLite |
| 初始化 | `initialize(session_id, **kwargs)` | 同签名，增加 `user_id_alt` 文档契约 | 只增加可选 kwargs 说明，调用方不得强制要求新键 |
| 可用性诊断 | `is_available()` | 新增 `unavailable_reason()` | 默认空字符串；不得在不可用路径触发网络连接 |
| 召回结果 | `prefetch()` 返回文本 | 新增 `RecallStatus` 与 `recall_status()` | 指示器是可选观察能力，不改变注入文本 |
| 回合同步 | `sync_turn(user, assistant, *, session_id)` | 增加可选 `messages` | 先保持旧 provider 可调用，再逐步升级调用方 |
| 会话切换 | `on_session_switch(..., reset, **kwargs)` | 增加 `rewound=False` | 默认 `False`，只向能理解该语义的 provider 传递 |
| 压缩前钩子 | best-effort、原始消息列表 | checkpoint API v2 可选择 fail-closed | 本地先保留 v1；没有持久 checkpoint 证据时禁止声明 v2 |
| 配置 schema | 基本字段 | 增加 `type/minimum/maximum/step` | 字段为可选元数据，不改变已有配置文件格式 |
| 备份 | 仅扫描 `HERMES_HOME` | 新增 `backup_paths()` | 先定义接口，备份命令接入另一个独立切片 |
| 纯提示分类 | 本地没有统一 helper | 新增 `is_trivial_prompt()` / `TRIVIAL_PROMPT_RE` | 可作为召回节流工具；不能改变 OneBot 触发判定 |

## 2. 需要实际合入的成员

下表是本次 MemoryProvider 兼容切片的最小实现面。所有新增成员必须有默认行为，避免现有本地 provider 子类立即失效。

| Change item | 来源 | 默认行为 | 验收 |
|---|---|---|---|
| `PRE_COMPRESS_CHECKPOINT_API_VERSION = 2` | 上游 | 常量可导入；provider 类默认版本仍为 `1` | 旧 provider 不被强制升级 |
| `RecallStatus` frozen dataclass | 上游 | `provider_label`, `count`, `glyph` 三字段；不参与持久化 | 构造、相等性、不可变性测试 |
| `unavailable_reason()` | 上游 | 返回 `""` | 不可用 provider 的 warning 可以读取，且无网络调用 |
| `recall_status()` | 上游 | 返回 `None` | 旧 provider 和空召回不显示假指标 |
| `sync_turn(..., messages=None)` | 上游 | 新参数可省略；旧调用完全不变 | 旧签名 provider 与新签名 provider 都能运行 |
| `on_session_switch(..., rewound=False)` | 上游 | `False` 且 no-op | 旧调用不改行为，新调用可表达 transcript 截断 |
| `get_config_schema()` 元数据说明 | 上游 | 新字段均可省略 | 既有 setup 配置解析不回归 |
| `backup_paths()` | 上游 | 返回新列表 `[]`，不得返回共享可变对象 | 未初始化、无网络时可安全调用 |
| `is_trivial_prompt()` | 上游 | 空白、slash command、纯问候/确认词为 trivial | `k8s`、`yolo` 等前缀词不得误判 |

## 3. 调用方兼容规则

### 3.1 `sync_turn` 的可选 `messages`

上游调用方可能向 provider 传完整 OpenAI 消息列表，而本地第三方 provider 仍只有旧签名。融合期间调用方必须满足以下顺序：

1. 若 provider 明确声明支持消息列表，则传 `messages=`。
2. 若 provider 为旧实现，允许一次受控的签名兼容回退到旧参数集合。
3. 任何 provider 内部异常仍按现有 best-effort 语义记录，不得吞掉成功回合或修改 OneBot 投递结果。
4. 兼容回退必须有测试，不能用无界 `except TypeError` 把 provider 内部真正的类型错误伪装成签名不支持。

### 3.2 压缩前 checkpoint

`pre_compress_checkpoint_api_version` 是能力声明，不是配置开关：

- `1`：沿用本地 best-effort，失败只记录诊断；
- `2`：provider 自己保证成功调用会产生可恢复 checkpoint，并接受规范化 evidence；
- 本地 `UnifiedMemoryGateway` 在有持久化证据和恢复测试之前保持 `1`；
- 不能因为上游常量被导入，就把本地数据路径宣称为 checkpoint v2。

### 3.3 RecallStatus

`recall_status()` 只表示最近一次 `prefetch()` 实际注入的内容。它是观察/展示端口：

- 返回 `None` 表示没有可展示的召回；
- `count == 0` 可以表示有内容但没有离散条目计数；
- 不得复用上一次回合的旧状态；
- 不得把状态指示文本写入 STM/EPI/LTM。

### 3.4 会话切换与 `rewound`

`rewound=True` 表示 session id 没有改变但 transcript 被截短。它与 `reset=True` 不等价：

- `reset=True`：新对话，清理 provider 的会话缓存；
- `rewound=True`：同一逻辑会话，失效按回合缓存；
- `/resume`、`/branch` 和压缩续接仍使用既有 `parent_session_id` 语义。

## 4. 不在本切片内做的事

- 不把上游 `MemoryManager`、外部 memory plugin 或 `conversation_compression.py` 整文件覆盖到本地。
- 不把 `UnifiedMemoryGateway` 的 SQLite 表、FTS、EPI/LTM 文件迁移到外部 provider。
- 不修改 OneBot 的触发、群状态、媒体、权限或投递路径。
- 不在没有真实恢复测试前接通 `backup_paths()` 的跨目录归档/还原。
- 不把上游 `is_trivial_prompt()` 直接用于 QQ 回复判定；它最多先作为 provider 召回节流 helper。

## 5. 聚焦验收矩阵

| 测试组 | 证明内容 | 失败处理 |
|---|---|---|
| interface defaults | 新成员的默认值、类型和空操作行为 | 阻断该切片合入 |
| legacy provider | 仅实现旧方法的 provider 仍可实例化、初始化、同步和关闭 | 阻断，说明兼容层破坏 |
| enriched provider | 接受 `messages`、`rewound`、`RecallStatus` 的 provider 能收到精确参数 | 阻断，说明上游契约断链 |
| trivial prompt | 锚定匹配和 slash/空白边界 | 只修 helper，不触碰 OneBot 判定 |
| import/compile | Windows UTF-8 bootstrap 规则和核心导入无新增错误 | 阻断 |
| existing memory tests | 本地 gateway、STM/EPI/LTM、历史 SQLite 可读性 | 任何数据语义回归都回滚该切片 |

## 6. 后续切片顺序

1. 合入本文件对应的抽象接口默认成员和契约测试。
2. 在不改变默认 backend 的前提下，增加 `BuiltinMemoryProviderAdapter` 的最小可行性 demo。
3. 让 `MemoryManager` 以能力检测方式传递 `messages` / `rewound`，并保留旧 provider fallback。
4. 单独评估 `backup_paths()` 与上游 `hermes backup` 的归档边界。
5. 最后才考虑 conversation loop 和压缩器的上游重放。

每一步都必须在 `docs/UPDATE_LOG.md` 使用新的 Change ID 记录来源、保留行为、测试、风险和回滚点。
