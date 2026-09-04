# Hermes QQ Bot 决策记录

> 维护者文档；只记录架构选择、证据和回滚边界，不包含生产地址、真实账号或密钥。

## DEC-ARCH-001：采用“通用引擎 + 本地产品层 + 能力端口”

- 日期：2026-08-30
- 决策：以上游 Hermes 负责 provider、工具、通用重试和生命周期基础设施；本地负责 OneBot/NapCat、STM/EPI/LTM、角色和 Windows 分发；通过稳定结果和能力端口连接。
- 原因：本地 run_agent.py、gateway/run.py、hermes_state.py 与上游 v0.20.6 均有深度改动，整文件覆盖会同时破坏 QQ 触发、记忆语义和 SessionDB 数据。
- 证据：docs/ARCHITECTURE_TARGET.md 第 1-8 节；三方差异盘点显示核心冲突集中在 agent/gateway/persistence。
- 回滚边界：任一融合切片必须能独立撤回，不允许以“上游版本升级”为理由删除本地 OneBot 或记忆行为。

## DEC-LIFE-002：所有可清空内存的生命周期操作必须先有恢复路径

- 日期：2026-08-30
- 决策：GatewayRunner.stop() 在清空队列前写 shutdown spool；启动恢复在独立线程和时间预算内运行；未解析或失败 payload 保留。
- 原因：本地 adapter pending 队列、runner pending 文本和 active agent transcript 都曾是关闭时唯一副本；SQLite 失败不能等同于数据不存在。
- 证据：core/gateway/shutdown_flush.py；shutdown 聚焦测试 17 通过、1 个 Windows symlink 能力跳过。
- 取舍：恢复线程超时后可能在后台继续完成，换取事件循环和 gateway 启动不被数据库锁拖死。

## DEC-ERR-003：错误分类先统一结果契约，再考虑拆分 conversation loop

- 日期：2026-08-30
- 决策：先让所有终止路径产生可序列化的 failure_reason、failure_retryable、status_code 和 error_surface；暂不直接移植上游 conversation_loop.py。
- 原因：本地 provider fallback、图片降级、连续对话和自研记忆写入都在单体循环内；先建立边界可让后续拆分拥有稳定验收对象。
- 证据：错误面 40 项、guard/continuation 组合回归通过；上游 conversation_loop.py 与本地循环存在大面积职责差异。

## DEC-QQ-004：OneBot/NapCat 是唯一 QQ 运行实现，QQBot 保留为迁移哨兵

- 日期：2026-08-30
- 决策：保留 Platform.QQBOT 枚举用于识别旧配置，但强制 enabled=False、不参与 connected 判定、不出现在用户菜单/cron/toolset；所有新 QQ 发送走 onebot。
- 原因：直接删除枚举会扩大旧配置解析破坏面；继续把 QQBot 判定为 connected 则会出现“已连接但无 adapter”的 phantom state。
- 证据：QQ 迁移聚焦测试 9 通过；旧 GatewayRunner._create_adapter 已无官方分支；活动实现位于 plugins/platforms/onebot。
- 后续：发布一个迁移周期后删除兼容键和不可达 shim，并增加真实 OneBot cron/媒体 contract test。

## DEC-MEM-005：先对齐 MemoryProvider 端口，再重放记忆实现

- 日期：2026-08-30
- 决策：以本地 `MemoryProvider` facade 为稳定端口，先吸收上游可选接口成员和能力声明，再评估 `MemoryManager`、压缩器和外部 memory plugin 的实现重放。
- 保留：`UnifiedMemoryGateway` 仍是本地 STM/EPI/LTM 的默认 backend；历史 SQLite、FTS、EPI/LTM 文件不因接口对齐而迁移或重写。
- 兼容规则：`unavailable_reason()`、`recall_status()`、`backup_paths()` 默认返回空值；`sync_turn(messages=...)`、`on_session_switch(rewound=False)` 都必须兼容旧 provider；checkpoint API 默认保持 v1。
- 原因：上游接口增量本身风险低，但直接覆盖 `memory_provider.py` 会把本地 provider 子类、OneBot 记忆写入和压缩前语义一起卷入未验证的大合并。
- 证据：`docs/MEMORY_PROVIDER_INTERFACE_MATRIX.md`；本地与上游接口逐项 diff；上游 `agent/memory_provider.py` 还包含与本地无关的备份/展示调用方。
- 后续：Volta 完成最小实现和聚焦测试后，再进入 `BuiltinMemoryProviderAdapter` 可行性 demo；在真实恢复测试之前不得接通跨目录 backup/restore。

## DEC-MEM-006：BuiltinMemoryProviderAdapter 先独立验证、后显式启用

- 日期：2026-08-30
- 决策：新增 `BuiltinMemoryProviderAdapter`，把本地 `UnifiedMemoryGateway` 映射到上游 `MemoryProvider`；适配器默认不进入 plugin discovery，也不自动替换现有 `memory_maintenance` hook。
- 原因：本地 gateway hook 已经记录 STM、事件流并在 session end consolidation；直接同时注册 adapter 会造成每轮 user/assistant 双写、turn counter 漂移和重复 consolidation。
- 语义：adapter 只写当前完整 user/assistant exchange，prefetch 返回结构化 gateway recall + STM，并以 `RecallStatus` 提供确定性提示；`on_pre_compress` 继续声明 v1，因为本地 gateway 尚无可证明的 durable checkpoint。
- 资源边界：`is_available()` 只检查 import，不创建 DB 或同步 wiki；adapter 不关闭共享 gateway，备份路径仍由本地 backup 子系统统一拥有。
- 验收：真实临时 SQLite STM 写入、fake gateway recall/session switch、trivial prompt gate 和工具 surface 均通过；显式启用方案必须在后续切片加入 hook 去重开关和跨进程恢复验证。
- 回滚：删除 adapter 与对应测试即可；不修改现有 memory DB、SessionDB 或运行时配置。

## DEC-DB-007：先建立 SessionDB canonical module ports，再逐个接管 mixin

- 日期：2026-09-01。
- 决策：把上游 `hermes_state_common/schema/search/portability` 先作为无副作用、可导入、可测试的 canonical compatibility port；保留本地 `hermes_state.py` facade 和 v11 数据语义，任何真正 mixin 接入必须另立 Change ID、在副本上验证并具备回滚证据。
- 原因：上游 v0.20.6 的四模块方法和本地 FTS/CJK、WAL fallback、OneBot transcript、导入门禁存在大面积实现差异；直接复制会制造循环导入、schema version 假升级和返回 shape 回归。
- 不变量：canonical module import 不打开数据库、不执行 DDL、不替换 `SCHEMA_VERSION`；schema/FTS 文本只能通过显式 local lazy accessor 取得；搜索和 portability 失败必须回退到现有本地路径或 bounded no-op。
- 证据：`core/tests/hermes_state/test_canonical_modules.py` 及 common/schema/search/portability 聚焦回归 `36 passed, 1 warning`；`docs/SESSIONDB_THREE_WAY_MATRIX.md` 和 `docs/SESSIONDB_V26_MIGRATION_MAP.md` 继续作为后续 Gate 约束。
- 后续：先使用授权脱敏历史 SQLite 副本完成 Windows/Linux probe/replay，再把单一上游能力接入 facade；未完成前不改版本号、不做生产 migration、不打开 NapCat 端口。

## DEC-OB-008：OneBot 认证成功必须与 WebSocket 建连分离

- 日期：2026-09-01。
- 决策：OneBot adapter 不能仅凭 TCP/WebSocket handshake 将平台标记为 connected；必须观察首个协议帧。`status=failed` 且认证相关 `retcode=1403` 时，状态必须是 `ws_auth_failed`、不可重试，并关闭 socket。
- 原因：真实 NapCat 回环测试中，当前 token 与不带 token 都收到 `1403/failed`，而旧实现仍保持 `connected=true`；这会让 Gateway 继续尝试 action，掩盖认证配置错误。
- 兼容：`websockets==12.0` 使用 `extra_headers`，15.x 使用 `additional_headers`，两者通过签名检测兼容；未知 header API 且存在 token 时不允许静默丢弃认证头；普通成功 event/response 会结束 pending。
- 证据：`UPG-OB-090`、OneBot transport focused `19 passed, 1 warning`、真实回环 connect/disconnect 和只读 `get_status`/WS 初始帧结果；未发送任何私聊/群消息。
- 后续：用户核对 NapCat WebUI access token 后，重新执行握手和最小发送回执；认证通过前不做消息写操作。

## DEC-OB-009：登录账号配置驱动 OneBot token，当前实例显式选择

- 日期：2026-09-01。
- 决策：NapCat token 不按 QQ 号计算，也不使用 WebUI token；运行时从 `onebot11_<uin>.json` 读取。Dashboard 让用户为当前 Hermes 实例选择账号，只持久化 `ONEBOT_SELF_ID`，token 保持进程内。
- 选择规则：显式 `ONEBOT_SELF_ID` 精确匹配；未设置时使用最新 `napcat_protocol_<uin>.json` 登录标记；配置缺失、非 regular、非回环、token 不一致或 malformed 时 fail-closed。远程 endpoint 不自动读取本机配置。
- 当前范围：一台机器一个 Hermes Bot 实例；切换账号不自动重启 Gateway。未来多账号/多 NapCat/多 Hermes 必须采用独立 config root、端口、PID/lock、profile、SessionDB、Memory namespace 和 binding 状态机。
- 保留：本地 `UnifiedMemoryGateway`、SessionDB v11、OneBot 群隔离/媒体/语音和 Gateway recovery 不能因为多实例规划被替换。
- 证据：NapCat 源码/配置显示按登录 UIN 读取账号文件；`UPG-OB-092` 配置发现 + OneBot transport `27 passed`、Dashboard `3 passed`，真实自动发现认证和消息回执均通过。

## DEC-OB-010：输出契约在 OneBot executor 收口，状态机必须有代码计数

- 日期：2026-09-01。
- 决策：`FIX_silence_contract_and_exit_loop.md` 的行为方向纳入下一实施切片：判定层放行的 mention/judge/continuation/exit-farewell 轮由 executor 负责可见输出契约，纯标记/空输出最多同会话反馈重试一次；adapter 只归一化控制标记和交付结果，不再用标记单点否决正文。
- 必须保留：OneBot 群隔离、现有 Agent/Memory/SessionDB transcript 语义、base session guard 和 Gateway error surface；不以修改 `base.py` 或整文件替换为默认方案。
- 代码门禁：重试必须按单次 turn 关联完成信号且等待旧任务释放；清洗后的正文要同时成为发送/outcome/recorder/buffer 输入；`exiting_streak` 等价有界计数必须在代码层阻止单轮 `should_exit`，直接 @、别名直呼、reply-to-bot 在 continuation 前复位。
- 原因：当前 GroupExecutor 的 `_group_send_results` 按群覆盖且空 response 可能不调用 send；MessageEvent 没有 `with_system_note`；prompt 无法证明持续轮数。忽略这些事实会造成重复用户回合、future 串线、误 quiet 或重试死等。
- 状态：已由 `DEC-OB-011` / `UPG-OB-098` 实施并通过当前 Windows 聚焦回归；真实 NapCat/provider、双平台进程/WAL、proxy 和 marker streaming 门禁仍未完成，在这些证据齐全前不更新版本号、不写公开 changelog。

## DEC-OB-011：执行层输出合同采用 nonce completion + ephemeral feedback

- 日期：2026-09-01。
- 决策：OneBot adapter 负责控制标记归一化和成功交付确认，GroupExecutor 负责“已放行轮次必须有可见正文”的最多一次反馈重试；每个 turn 使用独立 nonce，不再用群级单槽 future 串联状态消息或并发轮次。
- 反馈边界：复用 `MessageEvent.channel_prompt` 作为临时系统提示，contract retry 传递空 API user turn；Gateway/AIAgent 设置内部非持久化门禁，禁止把原始用户消息重复写入 JSONL、SessionDB 或外部 MemoryProvider。`event.with_system_note` 不属于当前接口，`gateway/platforms/base.py` 保持不变。
- 状态边界：正文成功发送后才允许混合 `[QUIET]` 产生 quiet；纯 marker 先交由执行器重试，失败正文不 quiet；`EpisodeState.exiting_streak` 在代码中限制单轮退出，直接 @、名字别名、reply-to-bot 在 judge/continuation 前清零。
- 证据：`UPG-OB-098`；新增 18 项 Windows asyncio/合成回归，silence + OneBot runtime/contract/transport/error-surface `54 passed, 1 warning`，Gateway lifecycle/ledger/session `37 passed, 1 warning`；`py_compile`、`git diff --check` 通过。warning 与 `test_fast_command` 的 `request_overrides=None` 均为预存问题，未改动。
- 未决：真实 NapCat/QQ/provider 空响应与 marker streaming、Linux/Windows 独立进程 guard/WAL、真实 Gateway + MemoryProvider/SessionDB transcript 回放和 proxy ephemeral retry 仍需独立证据；本决策不授权外部服务或生产数据库访问。

## DEC-AGENT-012：provider_projection 先做纯端口，再评估主循环接入

- 日期：2026-09-01。
- 决策：先新增有界、无副作用的 `agent/provider_projection.py` compatibility port，只接受 assistant/tool 投影行并累加 provider tool iteration 计数；默认不接入 ACP/Codex 或本地 `run_agent` 主循环。
- 原因：上游 agent-as-provider 的已完成 tool rows 不能回流为待执行 tool calls；本地 transcript、MemoryProvider、SessionDB 和 OneBot 结果 owner 与上游不同，直接 wiring 可能重复执行或污染持久化。
- 门禁：后续接入前必须建立 provider response shape、去重 key、role pairing、transcript/MemoryProvider persistence 和真实 agent-as-provider 回归；失败回退到当前 inline path。
- 证据：`UPG-AGENT-104`，port focused `4 passed, 1 warning`；未加载 `run_agent`、未访问数据库或外部服务。

## DEC-OB-012：OneBot 媒体 URL 采用下载边界解析门禁

- 日期：2026-09-01。
- 决策：保留公共 CDN 和显式远程 OneBot HTTP host 的兼容性，但在 image/get_file 与 voice/record 下载前拒绝非 loopback 私有/保留地址、歧义数字或编码 authority、userinfo、嵌套 scheme 和远程 UNC/file URL；公共 hostname 必须在 worker thread 中解析且所有 IPv4/IPv6 结果通过安全检查。
- Redirect：OneBot 媒体 client 显式关闭自动跟随；3xx 响应由 bounded stream helper 作为 HTTP media error 处理，不读取或缓存响应体。file URL 只允许本地 `file://`/Windows 盘符形态。
- 兼容边界：loopback 仅允许配置的 OneBot HTTP scheme+port（未加载配置时保留默认 `http:3000`），显式配置的远程 host 仅按相同 scheme+port 保留；不能把静态/预解析检查当作连接级 DNS pinning。
- 证据：`UPG-OB-108`；媒体 SSRF/stream/transport focused `48 passed, 1 warning`，`py_compile` 和 `git diff --check` 通过；未访问非回环网络、NapCat、生产数据库或真实凭据。
- 未决：真实 NapCat URL provenance、redirect/DNS rebinding TOCTOU、Linux resolver/IPv6/subprocess、真实 CDN allowlist 和生产网络仍需独立证据；本决策不授权外部服务访问或版本发布。

## DEC-OB-013：静默标记规格必须以成功交付为状态提交边界

- 日期：2026-09-01。
- 决策：`docs/FIX_silence_contract_and_exit_loop.md` 的 adapter 伪代码以当前实现为准，固定为 `normalize -> send -> commit marker state`；发送失败不得提交 `QUIET`，已注册执行轮次的纯标记必须延迟给 GroupExecutor 的最多一次反馈重试。
- 原因：原第 4.1 节示意代码在发送前调用 `go_quiet()`，与第 10.2 节的失败不 quiet 硬门禁矛盾，容易被后续实现照抄并重新引入不可逆 quiet。
- 兼容：不改变 `base.py`、OneBot marker regex、nonce completion、Gateway transcript suppress 或 EpisodeState 数据语义；本次只校正文档指令。
- 证据：`test_onebot_silence_contract.py` `18 passed, 1 warning`；真实 provider/NapCat、proxy、跨平台和 SQLite replay 仍未完成，不更新版本号。

## DEC-OB-014：OneBot buffer 状态与 executor completion 只记一次

- 日期：2026-09-01。
- 决策：adapter 若已在 `add_bot_reply_to_buffer()` 完成 `GroupState.record_reply()`，通过 completion 的 `state_recorded` 明确告知 executor；executor 不再重复递增 `reply_count` 或刷新 attentive state。若 adapter buffer 更新异常或旧 adapter 未提供该字段，executor 保留兜底记录。
- 同步：contract retry 的 `(empty)` sentinel 在 Gateway 重试路径归一为空，普通路径继续显示原有用户提示；nonce completion、marker 成功交付边界、exiting streak/reset 语义保持不变。
- 原因：真实 Base marker 回归发现成功发送后 reply bookkeeping 会被 adapter 和 executor 双计数；这会提前改变 judge/continuation 的状态，属于静默契约下的可见行为回归。
- 证据：`UPG-OB-134`；silence + OneBot runtime/contract `33 passed, 1 warning`，`test_onebot_silence_contract.py` 当前 21 项通过，empty-response/repetition/recovery persistence `9 passed, 1 warning`；`py_compile`、`git diff --check` 通过。warning 与 `request_overrides=None` 预存失败未改。
- 未决：真实 provider/NapCat、marker streaming、跨平台 guard/WAL、真实 Gateway+MemoryProvider/SessionDB replay 和 proxy retry 仍需独立证据；本决策不授权外部服务或生产数据库访问。

## DEC-SEC-015：静默契约增量差异安全收据保持离线与 deferred 边界

- 日期：2026-09-01。
- 决策：接受差异安全扫描 `cad5d0a9-7351-4178-93d3-40ab98016cde` 作为当前工作树的源码审查收据：77/77 review rows 覆盖、`0 reportable findings`；不把它升级解释为真实 provider/NapCat 或跨平台运行证明。
- 依据：OneBot `90 passed`、Gateway/空响应/lease/persistence 组合 `53 passed`、`py_compile`/`git diff --check`；TAC 状态因桌面工具未暴露而保持 unknown。
- 保留边界：真实 provider 空响应/marker streaming、Linux/Windows 独立进程 guard/WAL、真实 Gateway+MemoryProvider/SessionDB replay、proxy retry、NapCat 发送和 installer binary 仍是后续门禁；本决策不授权生产数据库、外部网络、QQ 写操作或版本发布。

## DEC-DB-008：先封存 Windows 合成 replay 证据，不提前接入 SessionDB mixin

- 日期：2026-09-01。
- 决策：将 `UPG-DB-136` 视为 Gate 0 工具合同证据：只读打开明确的临时副本，源 hash/sidecar 不变，v11/v26 probe、bounded export/search 和 import dry-run 可复现；不把合成 fixture 视为真实历史库。
- 原因：当前没有用户授权的脱敏历史 SQLite 副本或可用 Linux 发行版；直接接入上游 schema/search/portability mixin 会绕过本地 v11 FTS/WAL/SessionDB 语义验证。
- 后续：获得副本和 Linux runtime 后，按 `hash/integrity -> Windows/Linux replay -> single helper -> single mixin` 顺序推进；任一失败都保持本地 facade、schema version 和现有 OneBot/Memory 行为。

## DEC-DB-009：Gate 1 首个 common helper 只接入纯 LIKE 转义

- 日期：2026-09-01。
- 决策：将 `hermes_state_common.escape_like` 作为惰性 `SessionDB.escape_like()` facade 暴露，并替换本地 session-id/title/LIKE fallback 的重复内联转义；不接入上游 schema/search/portability mixin，不改 `SCHEMA_VERSION=11`、DDL、事务、WAL 或 FTS。
- 原因：该 helper 无数据库副作用且与本地实现逐字等价，存在明确的五个调用方；惰性导入避免 common port 反向依赖 facade 和启动循环。
- 证据：`UPG-DB-137`；common/canonical/search/schema/v26 `40 passed, 1 warning`，本地 SessionDB `212 passed, 1 warning`，`py_compile`/`git diff --check` 通过。warning 为预存 `skills_guard.py:627` 非法转义。
- 补充：1006 组随机/边界 `%`、`_`、反斜杠/CJK 输入的 facade/canonical 逐字等价属性检查全部通过。

## DEC-SEC-016：Gate 1 复核仅关闭纯 helper 风险，不提前扩大迁移边界

- 日期：2026-09-01。
- 决策：接受安全扫描 `a896ad4f-beee-49f1-87d3-0c93d68d0634` 作为 Gate 1 当前工作树收据：77/77 diff rows 覆盖、`0 reportable findings`；`SessionDB.escape_like()` 可继续保留为惰性 facade 委托。
- 依据：helper/canonical/search/schema/v26 `40 passed`、完整 `test_hermes_state.py` `212 passed`、部署 `20 passed`；SQL 仍参数化，`SCHEMA_VERSION=11`、DDL、事务、WAL、FTS 和返回 shape 未改变。
- 保留边界：真实历史副本、Linux/Windows 跨进程 WAL/权限、剩余 schema/search/portability mixin、provider/NapCat、proxy 和 installer binary 仍未证明；本决策不授权生产数据或版本发布。
- 未决：Gate 1 其它 helper、真实历史副本、Linux/Windows replay、mixin/DDL、FTS rebuild、跨进程 WAL 和生产切换保持 deferred；不访问真实数据库或外部环境。

## DEC-AGENT-013：TurnContext 先冻结纯合同，再评估主循环抽取

- 日期：2026-09-01。
- 决策：将上游 `turn_context.py` 的 `TurnContext` 数据类、API sidecar 纯函数、user-index reanchor 和压缩 predicates 作为下一阶段只读 seam 候选；不复制 `build_turn_context()`，不接入本地 `run_agent` 主循环。
- 原因：上游 builder 同时改变 session 建立、MCP refresh、memory prefetch、provider runtime、compression lock 和 checkpoint 顺序；本地还没有 `api_content` 的 metadata/transcript owner，直接 wiring 会破坏本地记忆和 SessionDB 语义。
- 验收：import 不加载 provider/SessionDB/run_agent；字段/类型/长度/sidecar 清洗和 compression predicate 有 focused tests；失败回退到本地 inline prologue。
- 保留边界：未授权真实 provider、NapCat、生产数据库、Linux/WAL 或版本发布；实现必须由 Volta 另立 Change ID 并重新安全复核。
- 现有 canonical port import smoke 已确认无 `run_agent`/`hermes_state`/`gateway.run` 泄漏；TurnContext 代码和主循环接入仍未发生。

## DEC-AGENT-014：TurnContext 先以独立纯合同端口交付

- 日期：2026-09-01。
- 决策：新增 `agent/turn_context_contract.py`，只交付 `TurnContext` 数据类、API sidecar 纯函数、reanchor 和压缩 predicates；不提供或伪造上游 `build_turn_context()`，不接入 `run_agent`。
- 原因：本地 metadata/transcript owner 尚未冻结，上游 builder 会改变 session、MCP、memory、provider 和 compression side effect 顺序；独立端口可以先建立字段和边界证据。
- 验收：8 项 focused tests、Agent Runtime 组合 `90 passed`、import smoke 不加载 `run_agent`/`hermes_state`/`gateway.run`，升级器 dry-run 纳入双写；失败时删除端口即可回退到 inline prologue。
- 保留边界：真实 provider/NapCat、历史 SQLite、Linux/WAL、proxy、sidecar 持久化和主循环抽取仍未授权或未证明。

## DEC-AGENT-017：message metadata 先作为纯内存端口交付

- 日期：2026-09-01。
- 决策：新增 `agent/message_metadata_contract.py`，只交付 timestamp stamp/append 和 persistence-only field 常量；不改变本地 `run_agent`、SessionDB writer、MemoryProvider 或 provider API。
- 原因：上游 metadata helper 可独立测试，但本地 transcript、API sidecar 和 SessionDB 字段 owner 尚未冻结；直接接入会把存储语义与 provider payload 绑定。
- 验收：导入无 runtime/storage side effect，提供时间戳保留/覆盖边界和同 mapping append 测试，升级器双写可预演；后续真实接入必须另立 Change ID。

## DEC-SEC-017：TurnContext 端口安全收据不等于主循环融合完成

- 日期：2026-09-01。
- 决策：接受差异安全扫描 `661b5bf5-71ef-482f-9470-cc905b1620bc` 作为 `turn_context_contract.py` 的离线安全收据：78/78 rows 覆盖、`0 reportable findings`；只读合同可以保留，主循环接入仍需独立 Change ID。
- 依据：TurnContext focused `8 passed`、Agent Runtime `90 passed`、升级 dry-run `586 planned/0 skipped`；模块导入不加载 provider、SessionDB、`run_agent` 或 `gateway.run`。
- 保留边界：真实 provider/NapCat、Linux/WAL、proxy、生产 SQLite/MemoryProvider、sidecar persistence owner 和 `build_turn_context()`/`run_agent` wiring 仍未证明；不授权外部资源或版本发布。

## DEC-AGENT-015：reanchor fallback 只允许可行动 user row

- 日期：2026-09-01。
- 决策：TurnContext 兼容端口的 reanchor 在 fallback 路径跳过 `display_kind`、压缩摘要/微压缩、空白和 synthetic user row；如果 exact content 匹配，仍允许定位该物理行以保留当前回合锚点。
- 原因：上游会把内部通知、压缩 carrier 和空 user echo 放在 `messages` 中；将其当成人类回合会让未来压缩/持久化 override 落到错误行。
- 兼容：当前端口不接 `run_agent`，不改变本地 transcript、SessionDB、MemoryProvider 或 OneBot 行为；多模态真实输入保留为 actionable。
- 证据：TurnContext focused `8 passed`、Agent Runtime `90 passed`、差异安全 scan `8b3e605e-27e7-46ad-b259-4c8b85b2e965` `0 reportable findings`。

## DEC-SEC-018：reanchor 边界修正保持只读和 deferred 语义

- 日期：2026-09-01。
- 决策：接受 `8b3e605e-27e7-46ad-b259-4c8b85b2e965` 作为 TurnContext reanchor 修正的差异安全收据；exact match 优先、fallback 只选 actionable user row，当前不接主循环。
- 依据：78/78 diff rows、`0 reportable findings`、TurnContext `8 passed`、Agent Runtime `90 passed`；没有 provider/SessionDB/network import side effect。
- 保留边界：真实 sidecar metadata/transcript owner、provider/NapCat、Linux/WAL、生产 replay 和 `build_turn_context()`/`run_agent` wiring 仍未证明，不授权外部资源或版本发布。

## DEC-SEC-019：message metadata 纯合同端口安全收据不扩大接入边界

- 日期：2026-09-01。
- 决策：接受差异安全扫描 `5917a80c-fd93-4bc2-9fed-ca37205895b6` 作为 `message_metadata_contract.py` 的离线安全收据：79/79 rows 覆盖、`0 reportable findings`；保留 timestamp stamp/append 纯内存端口，但不接入 `run_agent`、SessionDB、MemoryProvider 或 provider API。
- 依据：metadata/TurnContext/Agent Runtime/部署组合 `114 passed, 1 warning`，升级 dry-run `587 planned/0 skipped`，`py_compile`/`git diff --check` 通过；模块导入无 runtime/storage side effect。
- 保留边界：真实 sidecar/transcript owner、provider/NapCat、Linux/WAL、proxy、生产 replay 和主循环 wiring 仍需独立 Change ID、回归和安全收据；不授权外部资源或版本发布。

## DEC-OB-015：OneBot 空 sentinel 只在准入契约路径触发反馈重试

- 日期：2026-09-01。
- 决策：保留 `_onebot_contract_required` 作为 OneBot GroupExecutor 到 Gateway 的最小内部边界；仅对已准入的 group turn 将 agent `"(empty)"` 归一化为空并交给一次会话内反馈重试。普通平台和非契约路径继续使用现有用户提示。
- 原因：执行层输出契约要求 admitted turn 必须有可见正文，但不能让通用 gateway 的空响应语义或 proxy 行为被 OneBot 特例改写；显式标记把范围锁在 OneBot。
- 兼容：重试仍使用 ephemeral `channel_prompt`、空 API user turn 和 persistence suppression，不重复原始用户消息、媒体预处理、SessionDB/MemoryProvider 写入；完成 guard、pending follow-up 和 marker state 语义保持不变。
- 验收：silence/empty/exit/nonce 与 Agent Runtime 组合 `115 passed, 1 warning`；真实 provider/NapCat、streaming、proxy、Linux/WAL 和生产 replay 仍 deferred。

## DEC-SEC-020：空 sentinel 契约增量安全收据不扩大发布边界

- 日期：2026-09-01。
- 决策：接受 Codex Security diff scan `60bf5098-27e5-472e-be66-0460470aecc0` 作为当前工作树增量安全收据：79/79 rows、`0 reportable findings`、coverage complete；不因该收据提前发布或接入真实环境。
- 依据：重点路径覆盖 `_onebot_contract_required`、`contract_retry`、`(empty)` 归一化、nonce completion、session guard release、marker state 和 persistence suppression；组合回归 `115 passed, 1 warning`。
- 保留边界：TAC 状态无法由当前桌面工具面核验；真实 provider/NapCat/streaming/proxy、Linux/WAL、生产 SQLite、跨进程 replay 和版本发布仍需独立证据与 Change ID。

## DEC-AGENT-018：Sidecar 与 transcript owner 分离，按 schema gate 分阶段接线

- 日期：2026-09-01。
- 决策：`AIAgent.run_conversation()` 内 live `messages` 作为当前回合 owner，`api_messages` 作为 provider projection，现有 JSONL/SessionDB writer 继续作为 durable transcript owner；`api_content`、`display_kind` 和平台时间戳不得未经 writer/schema capability 直接落库。
- 原因：上游 `api_content` 是“精确发送字节”的持久化 sidecar，而本地 v11 schema 没有相应列；直接复制上游 `build_turn_context()` 会改变 memory prefetch、SessionDB、provider、compression 和 transcript 顺序。
- 接线顺序：先 timestamp-only，再 additive schema gate + sidecar，再单一纯 TurnContext helper，最后才评估主循环局部抽取；失败时保留本地 inline path，不替换大文件。
- 兼容与回滚：默认 v11 启动、OneBot、MemoryProvider、SessionDB 返回 shape 和用户数据不变；回滚只移除本阶段接线和 focused tests，不删除数据库、配置、缓存或外部资源。

## DEC-AGENT-019：只接入 TurnContext 的 API-copy clone seam

- 日期：2026-09-01。
- 决策：将上游结构化 message clone 与 `api_content` projection 作为一个最小、可回退的 API 边界接入本地 `run_agent`；`clone_message_for_api()` 只修改 provider 副本，live transcript、SessionDB、MemoryProvider 和 contract retry owner 保持本地实现。
- 原因：本地循环原先使用浅层 `dict.copy()`，后续 provider 清洗可能通过嵌套 `tool_calls`/多模态容器回写 canonical history；上游已明确要求结构化 clone。该 seam 不需要改变 session 建立、prefetch、压缩、schema 或发送顺序。
- 兼容门禁：有非空 sidecar 时跳过本地同一轮 memory/plugin 注入，避免 `api_content` 与 ephemeral `channel_prompt` 重复拼接；无 sidecar 和空 user turn 保持现有行为。timestamp stamping、v11 `api_content` writer/loader、跨重启 cache、`build_turn_context()` 和主循环抽取继续 deferred。
- 证据：TurnContext contract/sidecar seam `13 passed`，Agent Runtime/Memory/压缩组合 `71 passed`，`run_agent` + OneBot silence/transport `363 passed`；`py_compile`/`git diff --check` 通过。未访问 provider、NapCat、生产数据库或外部网络。
- 回滚：删除 `clone_message_for_api()`、`run_agent` 单点调用和对应 focused tests 即恢复原 inline API-copy 路径；不修改数据库、配置、缓存或用户数据。

## DEC-SEC-021：structural API-copy 安全收据仅覆盖代码快照

- 日期：2026-09-01。
- 决策：接受 Codex Security diff scan `61729166-2913-462b-b7f9-ea92ba3678c6` 作为 structural API-copy 增量离线收据：79/79 rows、`0 reportable findings`、coverage complete；不把扫描期间发生的文档漂移解释成代码发布或生产授权。
- 依据：递归 clone、`api_content`/display 剥离、provider sanitizer 隔离、canonical transcript、OneBot nonce/retry 和 SessionDB writer 均完成源审查；相关聚焦回归 `47 passed, 1 warning`。
- 保留边界：TAC 状态无法由当前桌面工具面核验；真实 provider/NapCat、proxy、Linux/WAL、生产历史副本、timestamp-only 后续接线和版本发布仍需独立证据。

## DEC-AGENT-020：timestamp-only 只扩展可选事件时间，不启用 sidecar schema

- 日期：2026-09-01。
- 决策：将有效 `MessageEvent.timestamp` 以可选 `persist_user_timestamp` 传入 Agent 当前 user mapping，并由 SessionDB v11 `append_message(timestamp=...)` 使用；未提供或无效时维持本地 wall clock。
- 原因：平台事件时间属于 canonical transcript 的排序/审计 metadata，但上游 `api_content`、display metadata 和完整 TurnContext builder 仍没有本地 v11 writer/schema owner；先做 timestamp-only 可验证且不扩大 schema 边界。
- 兼容：provider API copy 移除 timestamp，JSONL 保留已有值，旧 SessionDB/fake writer 无 timestamp keyword 时按旧签名调用；OneBot group/DM 仅从 payload `time` 生成共享事件时间。
- 验收与回滚：timestamp/TurnContext/API-copy/SessionDB/OneBot 聚焦 `22 passed, 1 warning`，扩大集合 `243 passed, 1 warning`；移除 timestamp keyword、事件转换和测试即可回退，不删除数据库、配置、缓存或用户数据。

## DEC-SEC-022：timestamp-only 安全收据保持离线和 schema 边界

- 日期：2026-09-01。
- 决策：接受 Codex Security diff scan `9450f9bd-f666-4c48-bbd2-94db33064e86` 作为 timestamp-only 增量离线收据：79/79 rows、`0 reportable findings`、coverage complete；不因该收据启用 `api_content`/display schema 或生产发布。
- 依据：OneBot `time`、Gateway handoff、canonical mapping、provider timestamp 剥离、SessionDB optional writer 和旧签名兼容均完成源审查；相关聚焦 `22 passed, 1 warning`，扩大集合 `243 passed, 1 warning`。
- 保留边界：真实 provider/NapCat、proxy、Linux/WAL、late-event replay、生产 SQLite 和 TAC 状态仍未证明；不授权外部资源或版本发布。

## DEC-DB-010：sidecar 列只通过显式 copy-only gate 添加

- 日期：2026-09-01。
- 决策：接受 `UPG-DB-154` 的 additive schema gate 证据；`messages.api_content`、`display_kind`、`display_metadata` 只能在显式 enable、独立 backup、expected SHA-256、临时副本和事务回滚前置满足时添加，默认 v11 启动保持不变。
- 原因：上游 sidecar 需要持久化 writer/loader 对照，不能用未声明列或自动启动 DDL “顺便”接入；copy-only gate 可以先验证列合同而不改变生产数据。
- 兼容：`schema_version` 保持 11，allowlist 只新增 nullable metadata 列；当前仍不传递 `api_content` 到 SessionDB、不做 backfill、不改变 provider/MemoryProvider/OneBot 语义。
- 证据与边界：v26 compat/copy-gate/schema-probe/replay/portability/canonical `79 passed, 1 warning`；真实历史副本、Linux/WAL、跨进程、provider replay 和生产切换继续 deferred。

## DEC-SEC-023：可选 sidecar writer/loader 安全收据不扩大 v11 边界

- 日期：2026-09-01。
- 决策：接受 Codex Security diff scan `7243bbb0-02ed-4f40-9b0d-553057a118ef` 作为 optional message sidecar writer/loader 的离线安全收据：79/79 rows、`0 reportable findings`、coverage complete；不因该收据自动迁移或发布。
- 依据：固定 optional-column allowlist、参数化动态 INSERT、role/长度/surrogate/JSON 限制、v11 no-op、loader/provider 剥离、replace/replay 和 Agent flush 均完成源审查；SessionDB `296 passed`、Agent/OneBot `56 passed`。
- 保留边界：sidecar 只在显式 gated columns 存在时读写；真实 provider/NapCat、proxy、Linux/WAL、生产历史副本、late-event replay、stale-rewrite 压力和 TAC 状态仍未证明。

## DEC-DB-011：gated sidecar loader/replace 保留 durable timestamp

- 日期：2026-09-01。
- 决策：在 v26 optional columns 已显式存在的数据库中恢复 message `timestamp` 到 conversation mapping，并让 `replace_messages()` 保留输入 timestamp；v11 没有 sidecar 列时继续原有返回 shape 和本地 wall clock。
- 原因：sidecar 回放需要同时保持 API-only metadata 与 canonical event ordering；如果 replace 重新生成当前时间，压缩/恢复会破坏时间线。该逻辑不需要修改 schema_version 或自动 DDL。
- 兼容：provider API clone 移除 timestamp/display 字段，SessionDB writer/loader 通过实际列检测启用；OneBot、MemoryProvider、routing、WAL 和现有 v11 查询语义不变。
- 证据与边界：sidecar/timestamp/TurnContext/API-copy `46 passed, 1 warning`，SessionDB/v26/replay/portability/canonical `296 passed, 1 warning`；全量 compression/lineage、真实历史副本和 Linux/WAL 仍需独立门禁。

## DEC-SEC-024：gated sidecar timestamp/rewrite 安全收据不等于生产迁移

- 日期：2026-09-01。
- 决策：接受 Codex Security diff scan `7cf7041b-c98f-4ec0-bb03-821bb72fdaea` 作为 gated sidecar timestamp/rewrite 增量离线收据：79/79 rows、`0 reportable findings`、coverage complete；不因该收据开启自动 DDL、生产 v26 writer 或版本发布。
- 依据：optional-column allowlist、参数化写入、metadata bounds、v11 no-op、provider stripping、gated timestamp restore、replace ordering 和 Agent flush 均完成源审查；SessionDB `296 passed`、Agent/OneBot/sidecar `46 passed`。
- 保留边界：stale-on-rewrite 全量 compression/lineage、真实历史副本、Linux/WAL/跨进程、provider/NapCat、late-event replay 和 TAC 状态仍未证明；不授权外部资源或生产数据。

## DEC-AGENT-021：canonical content 重写必须清除 stale api_content

- 日期：2026-09-01。
- 决策：在本地 canonical user override/merge、surrogate/ASCII/image recovery 和 compression summary rewrite 后调用 `drop_stale_api_content()`；只有未被重写的 gated v26 message row 才能继续携带 sidecar/timestamp。
- 原因：`api_content` 表示过去实际发送的 provider bytes；canonical content 改变后继续回放旧 sidecar 会让 provider 看到已删除的上下文，破坏 transcript/API 一致性。
- 兼容：v11 无 sidecar 列路径不变；provider clone 仍剥离 timestamp/display metadata，SessionDB schema/version、OneBot、MemoryProvider 和默认 writer 语义不变。
- 证据与回滚：run_agent/compression/OneBot/sidecar `432 passed`，SessionDB/v26/replay `296 passed`，stale focused `19 passed`；移除清理调用和测试即可回退，不删除数据库、配置或缓存。

## DEC-SEC-026：stale sidecar/replay 收据不扩大生产迁移权限

- 日期：2026-09-01。
- 决策：接受 Codex Security diff scan `2d255945-b0d0-4f76-b7e8-202133c1a4e1` 作为 stale `api_content` 清理与 gated replay 的离线安全收据：79/79 rows、`0 reportable findings`、coverage complete；不因该收据启用自动 DDL、生产 sidecar 或版本发布。
- 依据：canonical rewrite 清理、optional metadata bounds/allowlist、v11 no-op、provider projection、gated timestamp/replace、OneBot contract 和 SessionDB flush 均完成源审查；相关回归 `432` 与 `296` 通过。
- 保留边界：真实压缩 lineage、历史副本、provider/NapCat、proxy、Linux/WAL、late-event replay 和 TAC 状态仍未证明；不授权外部资源或生产数据。

## DEC-SEC-027：gated api_content producer 安全收据不扩大 v11/生产边界

- 日期：2026-09-01。
- 决策：接受 Codex Security diff scan `18e44803-046c-4697-b1d8-2f9d79ce24a4` 作为 gated `api_content` producer 与 stale replay 增量离线安全收据：79/79 rows、`0 reportable findings`、coverage complete；不因该收据启用自动 DDL、生产 sidecar 或版本发布。
- 依据：producer 仅在实际 sidecar 列存在时生成精确 memory/plugin API content；v11 no-op、writer/loader bounds、provider field stripping、stale cleanup、timestamp replay 和 OneBot contract 均完成源审查；相关回归 `433` 与 `296` 通过。
- 保留边界：真实 provider/NapCat、proxy、Linux/WAL、历史副本/late-event replay、跨进程压测和 TAC 状态仍未证明；不授权外部资源或生产数据。

## DEC-DB-012：sidecar lineage 先用合成副本验证，不启用生产迁移

- 日期：2026-09-01。
- 决策：接受 `UPG-DB-162` 的 Windows 合成 parent/child lineage 回放作为 gated sidecar 合同证据；`include_ancestors`、child replace、timestamp 和 sidecar 对齐通过，但不自动迁移真实数据库。
- 原因：lineage 回放必须证明 sidecar 与 canonical content/时间线绑定，且不能因 child rewrite 丢失 parent rows；临时 copy-only 数据库可以在不触碰生产数据的情况下验证这一点。
- 兼容：`schema_version=11`、v11 default writer/no-op、OneBot、MemoryProvider、provider projection 和 WAL 行为不变；真实压缩 lineage、late-event、Linux/WAL/跨进程和 provider replay 继续 deferred。
- 证据与回滚：lineage focused `7 passed, 1 warning`；删除合成 test 和本条记录即可回退，不删除数据库、备份、缓存或配置。

## DEC-ENV-013：SSH 可达不等于生产数据库可用于升级验证

- 日期：2026-09-01。
- 决策：把局域网 SSH 能力视为后续获取“只读、脱敏、带哈希的历史副本”的通道，而不是当前测试授权；本轮不连接生产 Linux 主机，不读写生产数据库，不执行迁移、DDL、WAL 或清理。
- 环境事实：当前 Windows WSL 只有 Docker Desktop，没有用户 Linux 发行版；因此 Linux/WAL、文件权限和独立进程 contention 仍必须在专用 WSL/CI 或经审查的 Linux 副本环境中独立验收。
- 本地规则：可以在独立 scratch 目录从零创建或清空临时 SQL fixture；不得清理运行 `state.db`、用户配置、缓存、备份或任何生产路径。生产副本进入测试前必须先做脱敏、源哈希、sidecar 完整性和回滚证明。
- 保留边界：在真实历史 replay、late-event/压缩 lineage、Linux/WAL/跨进程与 provider/NapCat 证据完成前，不启用生产 v26 sidecar，不改变 `SCHEMA_VERSION=11`，不更新版本号或公开 changelog。

## DEC-DB-014：WAL replay 只在 disposable harness 中容忍 -shm 读锁页变化

- 日期：2026-09-01。
- 决策：保留 `run_replay()` 默认严格的主文件及全部 SQLite sidecar 不变合同；新增显式 `tolerate_wal_shm_read_locks` 仅供 Windows-first disposable subprocess harness 使用。该模式允许 SQLite 只读 WAL 连接更新 `-shm` 锁页，但必须保持主库/WAL/journal hash 与 size 不变，并把变化单独写入 bounded report。
- 原因：受控 Windows 复现证明第二个只读进程打开 WAL copy 时 `-shm` 共享内存锁页会变化，而 canonical database/WAL frames 不变；若把它当作业务写入会误报 replay 失败，若全局放宽则会掩盖未知 sidecar 变化。显式 opt-in 将两种语义分开。
- 兼容：默认 `sessiondb_replay.py --source` 不开启容忍；harness 不接收 source path，只在临时 synthetic v11 parent/child fixture 上运行独立 writer/replay 子进程。v11 SessionDB、自动 migration、v26 runtime、OneBot、MemoryProvider 和生产数据库边界不变。
- 证据：`UPG-DB-164`；harness/replay/sidecar `21 passed`，SessionDB/v26/copy-gate/schema/replay/portability/canonical `315 passed`，`py_compile`/`git diff --check` 通过；未访问生产、Linux 主机、WSL 外部资源、NapCat 或真实 provider。
- 保留门禁：真实授权历史库、真实压缩 lineage/late-event、Linux/WSL WAL/权限/跨进程行为、生产 replay 和 v26 migration 仍须独立验收。
- 回滚：移除显式容忍参数、harness/测试和本条记录即可恢复原严格 replay；不删除或修改数据库、配置、缓存和外部资源。

## DEC-DB-015：`-shm` 容忍必须证明是 WAL 读锁形状变化

- 日期：2026-09-01。
- 决策：在 `DEC-DB-014` 的显式 opt-in 之上，只有 journal mode 为 WAL、读前/读后 WAL 存在、`-shm` 两侧均为无错误 regular 文件且尺寸一致时，才允许把 `-shm` hash 变化解释为 SQLite 读锁页 churn。
- 原因：仅凭 WAL 存在不足以排除 sidecar 被替换、缺失、尺寸漂移或非 regular 文件；主库、WAL、journal 仍使用 hash/size 稳定性作为 canonical replay 不变式。
- 证据：新增异常形状和非 WAL 反例回归；replay/harness focused `15 passed, 1 warning`，直接 harness 返回 `ok`。
- 兼容与回滚：默认 `run_replay` 和 v11 SessionDB 行为不变；移除该形状条件及测试即可回退，不修改数据库或外部资源。

## DEC-SEC-029：最终 replay/WAL 安全收据不扩大生产权限

- 日期：2026-09-01。
- 决策：接受最终快照 Codex Security diff scan `ff3dd087-a55e-4e56-ae83-7160b5c8689c` 作为 replay/WAL 增量离线收据：80/80 rows、`0 reportable findings`、coverage complete；不因该收据启用生产 sidecar、v26 migration 或版本发布。
- 依据：source-copy/runtime state.db 隔离、只读连接、WAL/`-shm` 形状门禁、bounded report、subprocess cleanup 和 focused regression 均完成复核；扫描无工作树漂移警告。
- 保留边界：TAC 状态不可核验；真实脱敏历史库、Linux/WSL WAL/权限/跨进程、真实 provider/NapCat 和生产 replay 仍 deferred；未访问 SSH 或生产资源。

## DEC-ENV-014：NapCat SQL 与 Hermes replay scratch 必须隔离

- 日期：2026-09-01。
- 决策：把本地 NapCat 的 SQLite 主文件及 `-wal/-shm` sidecar 视为运行数据，只依赖现有根仓库/nested Hermes ignore 规则防止分发；升级 harness、replay 和 schema gate 永远不自动发现、读取、清空或迁移这些文件。
- 依据：只读检查确认 ignore 规则覆盖 `*.db`、`*.db-wal`、`*.db-shm`；当前文件内容未读取，未执行删除或清空，未作为 Hermes replay 输入。
- 环境补充：当前仅有 Docker Desktop 内部 WSL 项，Docker CLI/server 不可用；不安装系统发行版、不修改启动项，也不把该内部项当作 Linux 验收证据。
- 本地规则：允许在专用 scratch 目录从零重建测试 SQL；任何清理运行数据库都必须另行指定精确目标、备份和回滚证明，不能由通用测试脚本完成。
- 保留边界：NapCat 账号/消息数据、Hermes `state.db`、生产 SSH 数据和脱敏历史副本均保持独立；不因 ignore 规则而获得生产迁移权限。

## DEC-ARCH-016：架构矩阵区分合同完成与主路径融合

- 日期：2026-09-01。
- 决策：`ARCHITECTURE_TARGET.md` 的能力矩阵必须把已实现的兼容合同/gated port 与尚未接入 Gateway/Agent 主路径的运行时状态分开表示；本次只修正文档状态，不把合同完成解释为完整上游融合。
- 依据：turn lease、session stall、delivery ledger、SessionDB Gate 0–5 前置、MemoryProvider/Environments port、API-copy/timestamp/sidecar seam 均有独立代码/测试/日志证据，但真实历史库、Linux/WAL、provider/NapCat、conversation loop 和生产切换仍 deferred。
- 兼容：不改变 OneBot、UnifiedMemoryGateway、SessionDB v11、版本号、数据库或双 Git 规则；后续主路径接入仍需单独 Change ID、回归和安全收据。

## DEC-OB-017：本机真实回环只关闭连接与发送前置

- 日期：2026-09-01。
- 决策：接受隔离 Hermes Gateway + 本机 NapCat 的真实回环 smoke 作为 OneBot 连接/鉴权/发送前置证据；不把它等同于完整入站对话或生产部署验收。
- 证据：account-specific config 通过 `get_login_info` 200/`retcode=0`；Gateway WS connected/event loop started；私聊和测试群直接 OneBot HTTP endpoint 均 200/`retcode=0`/message id；Dashboard port/account/status API 通过。
- 安全边界：测试使用临时 `HERMES_HOME`、账号白名单和内存 token；未改写真实 `.env`、`state.db`、NapCat SQL 或 token；没有连接 Linux 生产机、调用真实 provider 或使用 bot 自发消息假装入站用户。
- 保留门禁：judge/MemoryProvider/provider response、streaming、delivery ledger、Linux/WAL、真实历史库和发布仍 deferred；下一次入站验证必须限制在已授权用户/测试群，并单独记录结果。

## DEC-OB-018：OneBot token 以 NapCat 账号配置为权威来源

- 日期：2026-09-01。
- 决策：接受“无 `ONEBOT_ACCESS_TOKEN`、仅提供 bot self-id 和配置目录”的本机真实 smoke 作为 token 自动发现证据；token 不按 QQ 号计算，不回显、不写日志、不复制到仓库。
- 证据：account-specific NapCat 配置被加载，Gateway WS 成功连接且无 auth failure；临时 HERMES_HOME/SessionDB 创建后清理，真实用户 `.env` 和 NapCat 配置未改写。
- 兼容：手工 `ONEBOT_ACCESS_TOKEN` 仍作为远端 OneBot 兼容服务器的 fallback；本地登录 NapCat 优先使用账号专属配置，账号不明确或配置不安全时 fail-closed。
- 保留门禁：真实入站用户对话、provider/streaming、Linux/WAL/生产 replay 和发布仍未验证；该 smoke 不授权生产 Linux 访问。

## DEC-AGENT-022：Agent Runtime 矩阵使用分层 live gate

- 日期：2026-09-01。
- 决策：把本机 NapCat/OneBot/Gateway 的连接、鉴权和 outbound receipt 记录为已通过的 transport gate，同时把 user inbound → judge → memory → provider → delivery 保持为未验证的独立 gate。
- 原因：真实回环可以证明账号配置和网络边界，但 bot 自发消息会被 self-message 规则忽略，不能证明用户消息触发了 agent 主循环；两者必须分开记录。
- 兼容：不改变本地 OneBot、UnifiedMemoryGateway、SessionDB v11、TurnContext owner 或发布门禁；后续完整入站验证仍需白名单用户测试消息和 provider 证据。

## DEC-AGENT-023：sidecar producer 与 provider projection 共用 composition helper

- 日期：2026-09-01。
- 决策：首个 selective TurnContext 主循环接线只把 `compose_user_api_content()` 接入 `run_agent` 的 API projection；已有 `api_content` sidecar 时不再追加本轮 ephemeral context，没有 sidecar 时保持原行为。
- 原因：producer 写入的 API-only bytes 必须与实际 wire projection 同源，否则跨重启 replay 会产生 prompt-cache 前缀漂移或重复 memory/plugin 注入。
- 证据：TurnContext sidecar/timestamp/memory focused `13 passed`，run_agent projection/prefetch `4 passed`，`py_compile` 通过；无 SessionDB schema、OneBot、provider client 或全量 conversation loop 替换。
- 兼容与回滚：保留本地 inline prologue 和 v11 no-op；移除单点调用与测试即可回退，不改变数据库、配置、缓存或外部资源。

## DEC-SEC-030：composition helper 安全收据不等于主循环完成

- 日期：2026-09-01。
- 决策：接受 `d82f515a-e090-4f2e-9525-0e757e8da1e7` 作为 `compose_user_api_content()` selective wiring 的离线安全收据：80/80 rows、`0 reportable findings`、coverage complete；不因该收据宣称完整 `build_turn_context()`/conversation loop 或真实 provider 已融合。
- 依据：sidecar producer 与 API projection 同源，canonical transcript、persistence-only metadata、OneBot contract 和 v11 no-op 均完成静态/聚焦复核；未访问外部服务或生产数据。
- 保留边界：真实用户入站、MemoryProvider/SessionDB transcript 对照、provider/streaming、Linux/WAL、历史库和发布仍 deferred；保持 inline fallback 和现有 owner。

## DEC-OB-019：连接成功但无入站事件时不得关闭完整链路门禁

- 日期：2026-09-01。
- 决策：把 180 秒白名单监听窗口记录为负向证据：NapCat/Gateway WS 已连接，但没有捕获授权用户入站，因此不改变 judge、memory、provider、streaming 或 delivery 的 deferred 状态。
- 原因：新 WS 连接不会回放连接建立前的消息；bot self-message 会被 adapter 忽略。只有 READY 之后的真实用户消息或受控 fixture 才能证明完整链路。
- 兼容与安全：保持自动 token discovery、白名单和临时 HERMES_HOME；不放宽用户范围，不读取历史消息，不改写真实 `.env`/`state.db`/NapCat SQL，也不连接生产 Linux。

## DEC-MEM-026：自研记忆 schema 先做 additive compatibility，再单独处理表重建

- 日期：2026-09-01。
- 决策：把 UnifiedMemoryGateway 使用的 LTM/EPI/WFM/core-memory 字段和依赖表纳入 `MemoryStore` 的 code-owned schema；对已有 v1 数据库只执行固定列/表/索引的 additive 兼容，外部 FTS 在补列后重建一次，不在正常启动中删除或重建旧表。
- 依据：真实临时集成回归此前暴露 `long_term_entries.active` 缺失、`memory_edges`/`_sleep_watermark` 缺失和旧 FTS stale 问题；修复后 custom memory `72 passed`，旧 v1 copy 的 facts、FTS、correction、active/inactive 关系可恢复。
- 兼容边界：旧 table-level unique 通过 inactive tombstone key 兼容 correction；新库使用 active-only unique index；旧表彻底重建仍需独立 backup/hash/rollback gate，不自动触碰本地真实 memory_store.db。

## DEC-SEC-031：自研记忆 schema 安全收据不扩大迁移权限

- 日期：2026-09-01。
- 决策：接受 `7bef11d7-2962-470c-9926-b7f307ce5398` 作为自研 memory schema/FTS/correction 增量安全收据：82/82 rows、`0 reportable findings`、coverage complete；不因该收据自动重建旧表、迁移真实 memory_store.db 或启用外部 provider。
- 依据：固定 additive 列/表/索引、FTS rebuild、legacy correction tombstone、active-only retrieval、Layer 0/EPI/WFM 真实临时回归和事务边界均完成复核。
- 保留边界：生产/真实本地数据库仍需独立备份、哈希、脱敏、Linux/WAL/跨进程和回滚证据；`SCHEMA_VERSION=11`、OneBot、SessionDB 和公开版本号不变。

## DEC-MEM-027：memory hook scope 以 Gateway 显式 chat_type 为准

- 日期：2026-09-01。
- 决策：`agent:start/agent:end` hook context 必须携带 `SessionSource.chat_type` 及 chat/user metadata；`memory_maintenance` 先使用显式类型，再对旧调用方使用有限启发式回退。
- 原因：只依赖 chat-id 字符串会把真实群消息写入 DM scope，破坏 STM 隔离、EPI privacy 和 Layer 0 的来源标记；统一 helper 也避免不同 hook 构造 shape 漂移。
- 证据：opaque chat-id + explicit group type 的 custom-memory 回归、AIAgent sync、interrupted skip 和 OneBot runtime 组合 `78 passed, 1 warning`；未访问外部 provider 或生产数据库。
- 兼容：消息正文上限、OneBot/SessionDB/UnifiedMemoryGateway owner 和 v11 schema 不变；缺失 chat_type 的 legacy hook 调用仍可回退，不自动扩大 scope。

## DEC-SEC-032：自研记忆生命周期与 hook scope 安全收据不扩大生产权限

- 日期：2026-09-01。
- 决策：接受 `0bf94146-d16e-4f4c-a3ee-f1e6f18c5080` 作为当前自研 memory lifecycle、Gateway hook scope 和 schema/FTS/correction 工作树的最终离线安全收据：83/83 rows、`0 reportable findings`、coverage complete。
- 依据：显式 `chat_type` scope、bounded hook metadata、active-only retrieval、旧 v1 additive migration、legacy correction tombstone、FTS rebuild、AIAgent MemoryProvider 完成/中断语义和 OneBot/SessionDB 组合回归均有 disposable/local 证据；custom memory 7 项、组合 `78 passed, 1 warning`。
- 保留边界：未访问真实本地或生产 `memory_store.db`、NapCat SQL、SSH/Linux、provider、凭据或真实用户入站；不得因该收据自动重建旧表、迁移生产数据、开启真实 provider 链路、关闭 Linux/WAL/跨进程门禁或发布版本。
- 兼容与回滚：不改变 `SCHEMA_VERSION=11`、OneBot、SessionDB、UnifiedMemoryGateway owner、配置或版本号；本条仅为审查记录，可移除而不影响运行行为。


## DEC-AGENT-016：区分 TurnContext 合同完成与主循环融合完成

- 日期：2026-09-01。
- 决策：将矩阵状态拆成两层：`turn_context_contract.py` 的纯合同、边界测试和升级双写已实现；sidecar metadata/transcript owner、provider/MemoryProvider/SessionDB 对照及 `run_agent` 主循环 wiring 仍未完成。
- 原因：纯 port 可以先提供稳定字段/函数接口，但不能凭此声称上游 `build_turn_context()` 或 `conversation_loop` 已融合。
- 兼容：保持本地 inline prologue、SessionDB v11、OneBot/Gateway 和 memory backend 不变；任何选择性 helper 接入必须另立 Change ID、回归和安全收据。

## DEC-MEM-028：显式 chat scope 先隔离 STM/EPI，保留 LTM 全局设计

- 日期：2026-09-01。
- 决策：对已携带明确 `chat_type` 的 Gateway/MemoryProvider 请求，在 STM 与 EPI recall 边界执行目标 scope 过滤；group 继续允许匿名 group-to-group EPI 联想，DM 片段禁止进入 group。未携带 scope 的旧 API 继续保留旧兼容行为。
- 原因：本地 STM rows 已带 `chat_type`，但原检索只按 session id；EPI 原检索只按 `share_level`，会把可分享 DM fragment 带入 group。显式目标 scope 可在不改变现有数据格式的情况下关闭 DM→group 泄漏，并保留产品已有的匿名跨群联想。
- 来源合同：Layer 0 message event 增加 bounded optional `chat_id/thread_id`，MemoryStore chat buffer 增加显式 type 过滤；Gateway hook 的 `session_id/chat_id/chat_type/thread_id` 保持同源。AIAgent completed turn 才同步 BuiltinMemoryProvider，interrupted turn 不同步；SessionDB 仍保留原始 transcript/parent lineage。
- 兼容：`chat_type=None` 不改变旧调用；没有新增 memory schema、source-user migration 或 OneBot/SessionDB 版本变化。LTM 仍为全局提炼事实层，EPI group→group 匿名共享不被误标为“完全隔离”。
- 证据：`UPG-MEM-185`；privacy/lifecycle focused `6 passed`，memory/provider/session/OneBot runtime `140 passed, 1 warning`，`py_compile`/`git diff --check` 通过。单独 `test_hooks.py` 的 4 个旧 built-in handler 断言失败保持预存，未访问真实 memory DB/NapCat/SSH/provider。
- 保留门禁：按同一 user 在不同 chat 实现完整 LTM/EPI 隔离仍需 source user/chat schema、历史副本脱敏、迁移/回滚和跨平台证据；真实用户入站/provider、Linux/WAL、生产数据和版本发布继续 deferred。
- 回滚：移除 scope 参数传递、EPI/STM filter、Layer 0 source fields、focused tests 与本条记录即可；不删除或修改任何真实数据库、配置或外部资源。

## DEC-SEC-033：UPG-MEM-185 安全收据不扩大记忆迁移权限

- 日期：2026-09-01。
- 决策：接受 Codex Security diff scan `ca158acb-af28-4f94-8e36-e7d0e61ed042` 作为 UPG-MEM-185 的最终离线安全收据：9/9 源码审查项、`0 reportable findings`、coverage complete、无工作树漂移。
- 依据：显式 STM/EPI scope、DM→group deny、匿名 group→group、deferred lifecycle、bounded Layer 0 metadata、参数化 chat buffer、SessionDB parent/child 对照和 OneBot 回归均有 disposable/local 证据；scope/lifecycle `6 passed`，扩展集合 `93 passed, 1 warning`。
- 保留边界：LTM 仍为全局提炼事实层；不因该收据启用全量 user/chat 隔离、真实 DB migration、旧表重建、provider/NapCat 入站、Linux/WAL、生产 replay 或发布。
- 兼容与回滚：不改变 `SCHEMA_VERSION=11`、OneBot、SessionDB、UnifiedMemoryGateway owner、用户配置或外部资源；本条仅为安全审查记录。

## DEC-OPS-034：生产验证必须以局域网快进、备份和 READY 协议为边界

- 日期：2026-09-01。
- 决策：把 `docs/HANDOFF_PRODUCTION_VALIDATION.md` 作为 `74e4828` 代码基线、最终 `ab77144` tip 的生产维护 Agent 入口；生产只从 nested Hermes 的局域网 `origin/main` 快进更新，先备份数据库及 WAL/SHM/journal，再观察 additive migration，最后由维护者在 READY 后触发受控入站验证。
- 原因：本次代码包含旧 `memory_store.db` 的固定 allowlist additive 兼容，首次启动可能改变 schema/FTS；同时本机只证明了 NapCat transport/outbound，不能用 bot 自发消息证明完整 user inbound → judge → provider → memory → delivery 链路。
- 安全边界：工作树有未授权改动、备份失败、schema delta 超出 allowlist、NapCat 未登录或 token/凭据可能泄露时必须停止；不使用 GitHub 替代局域网源，不删除/清空运行数据库或 NapCat SQL，不主动发送 QQ 测试消息。
- 兼容与回滚：保留 `.env`、`config.yaml`、`SOUL.md`、SessionDB v11、OneBot 和现有数据；代码回滚与数据库恢复分开处理，不能用 `reset --hard` 或手工 WAL surgery 代替备份恢复。

## DEC-OPS-035：Feishu websocket override 必须隔离于进程共享模块

- 日期：2026-09-04。
- 决策：Feishu 为 Lark SDK 注入 ping/reconnect runtime override 时，只替换 `lark_oapi.ws.client` 持有的 websocket reference，并通过代理委托其它 websocket 属性；不得改写进程共享的 `websockets.connect`。
- 原因：Feishu 的长连接线程此前将共享 `connect` 替换为通用 `*args/**kwargs` wrapper。OneBot 随后通过 signature 检测 `additional_headers`/`extra_headers` 时得到空结果，因而在尚未打开 socket 前 fail-closed，错误归类为 `transport_error`。这不是 NapCat 端口、token 或 OneBot 协议问题。
- 证据：真实生产边界 probe 确认 NapCat HTTP/WS 可用且同一 OneBot adapter 单独连接成功；修复后同一 Gateway 启动同时记录 Feishu connected、OneBot connected、OneBot event loop 和协议事件。Feishu runtime override focused `2 passed`，OneBot config/transport/silence focused `51 passed`，Feishu 非 reaction 集合 `187 passed`。
- 保留边界：Feishu reaction 测试中有 8 个既有 `Typing` 对 `HEART` 断言不一致，未触及该行为，也不以其掩盖 websocket 回归结果；完整 READY 入站、judge/provider/memory/delivery 闭环、Linux/WAL 与发布继续受独立门禁约束。
