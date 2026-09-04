# Hermes 上游升级交接文档

> 文档性质：维护交接 / 新会话启动材料
> 当前状态：已完成第一批兼容融合切片和离线回归，尚未完成 Hermes 0.20.6 级别的全量发布
> 生成日期：2026-08-29

## 1. 用户原始要求

以下要求必须原样保留，后续工作不能偏离：

- “Linux和win都要保留，我们先列计划书，然后你客观审视找一下缺点”
- “写成交接文档吧。”
- “详细分析上游新增的，我们定制的要保留，方案A+B吧，qqbot那个上游更新支持就支持吧不管他”

当前会话的主任务是：在保留 Linux 和 Windows 双平台、保留 QQ OneBot/自研记忆/Live2D 等定制的前提下，制定并逐步执行 Hermes 上游升级计划。

## 2. 项目现状

项目是一个 QQ 群 AI 机器人分发模板，主要组成如下：

- `hermes/core/`：Hermes Agent 核心引擎。
- `plugins/platforms/onebot/`：当前实际使用的 QQ OneBot/NapCat 定制插件。
- `agent/memory/`：项目自研 SQLite 多层记忆系统。
- `gateway/live2d_ws.py`：Live2D WebSocket 桥。
- `modules/napcat/`：NapCat QQ 协议桥分发内容。
- `modules/live2d/`：Electron/Live2D 程序和 Cubism 模型资产。
- `modules/dashboard/`：Dashboard 控制面板。
- `extras/scripts/`：安装、配置、升级、迁移及 QQ 运维脚本。
- `templates/`：根级运行配置模板。

仓库有两个 git 层级：

1. 根仓库：用于 GitHub 分发完整模板。
2. `hermes/` 内部仓库：用于 Hermes 核心代码的局域网同步，服务器是权威源。

未提交改动需要同时注意两个仓库的状态，不能只检查根仓库。

## 3. 已完成的瘦身工作

本次已经执行并验证以下删除：

### 3.1 删除遗留 OneBot adapter

删除：

- `hermes/core/gateway/platforms/onebot/`

依据：该目录的 `adapter.py` 文件头明确标注为 legacy upstream fallback，且全仓没有实际 import；真实使用的是 `hermes/core/plugins/platforms/onebot/`。

### 3.2 删除未使用的官方 QQ Bot

删除：

- `hermes/core/gateway/platforms/qqbot/`
- `hermes/core/tests/gateway/test_qqbot.py`

同步移除：

- `gateway/run.py` 的 `Platform.QQBOT` 工厂分支。
- `hermes_cli/gateway.py` 的 `_setup_qqbot()` 和 setup dispatch 条目。
- `hermes_cli/setup.py` 的 `_setup_qqbot()` 包装函数。
- `gateway/platforms/__init__.py` 的 `QQAdapter` 懒加载导出。
- `test_platform_http_client_limits.py` 中对已删除 qqbot adapter 的 import。

保留了 `Platform.QQBOT` 的配置枚举和少量静态配置 key，原因是删除枚举会扩大现有配置解析的破坏面。它们目前属于无害残留，后续可单独清理。

### 3.3 删除本地备份目录

已删除根目录下的六个 `.bak` 目录，释放约 2GB：

- `hermes.bak`
- `modules.bak`
- `napcat.bak`
- `node.bak`
- `scripts.bak`
- `templates.bak`

这些目录被根 `.gitignore` 的 `.bak` 规则排除，属于本地陈旧备份，不是运行时输入。

## 4. 已完成的验证

- 4 个被修改的 Python 文件通过 `py_compile`。
- 修改文件通过 AST 解析。
- 修改模块、活性 OneBot 插件全链路、保留的多平台插件均能 import。
- 已删除的 `gateway.platforms.onebot` 和 `gateway.platforms.qqbot` 不再可 import。
- 活性 `plugins.platforms.onebot` 保持完整。
- `test_platform_http_client_limits.py`：7 passed。
- `test_onebot_runtime_regressions.py`：2 failed / 2 passed；失败为活性 adapter 的 `_admin_id` 属性问题。
- 通过 git stash 还原到删除前原代码后，该测试仍然以同样错误失败，已确认是预先存在的问题，不是本次瘦身引入。

测试环境中为验证临时安装过 `pytest`；当前 venv 可能因此多了测试依赖。后续是否卸载由维护者决定。

## 5. 上游基线与三方对比

上游仓库：`NousResearch/hermes-agent`。

已在仓库外准备独立的上游目录，目录名应标记为 `hermes-upstream`，不要纳入项目分发仓库。

精确 fork 锚点：

- 上游 v0.13.0。
- 发布日期：2026-05-07。
- commit：`498bfc7bc`。

升级目标：

- 上游 v0.20.6。
- 发布日期：2026-08-27。

三方统计结果：

| 对象 | 文件数 | 说明 |
|---|---:|---|
| 上游 v0.13.0 | 3143 | fork 起点 |
| 我们当前 `hermes/core` | 2742 | fork 后叠加项目定制 |
| 上游 v0.20.6 | 10506 | 迁移目标 |
| 上游 0.13 → 0.20 新增 | 7761 | 其中大量是 tests/apps/website 等附加内容 |
| 上游 0.13 → 0.20 删除 | 398 | 包含顶层 environments 重构 |
| 上游内容变化 | 10785 | 大面积核心演进 |
| 我们相对 v0.13 新增 | 251 | 项目定制文件 |
| 我们相对 v0.13 修改 | 592 | 魔改文件 |
| 三方都有且互相不同 | 535 | 迁移重点 |

上游版本使用日期 tag，例如 `v2026.5.7`、`v2026.8.3`、`v2026.8.27`，不是 `v0.13.0` 格式；v0.13.0 的发布 commit 已作为可靠锚点保存。

## 6. 已识别的上游重大变化

### 6.1 记忆系统

上游 v0.20.6 使用：

- `agent/memory_manager.py`
- `agent/memory_provider.py`
- `plugins/memory/`

`MemoryProvider` 生命周期包括 `initialize`、`prefetch`、`sync_turn`、工具 schema、工具调用和 `shutdown`，并支持压缩前检查、会话切换、记忆写入等 hook。

我们的记忆系统是：

- `agent/memory/gateway.py`
- `retrieval.py`
- `store.py`
- STM/LTM/EPI/Workflow/Wiki 等模块

这是项目的核心自研资产，不能直接用上游记忆系统覆盖。后续应采用适配器桥接，而不是数据迁移或强制替换。

### 6.2 SessionDB 拆分

上游把单体 `hermes_state.py` 拆成：

- `hermes_state_common.py`
- `hermes_state_schema.py`
- `hermes_state_search.py`
- `hermes_state_portability.py`

原 `hermes_state.py` 仍作为 facade，重新导出兼容名称。

迁移时必须以我们的 `hermes_state.py` 为基础拆分，保留已有 FTS、数据库锁和自定义 schema 修复，不能直接覆盖成上游版本。

### 6.3 顶层 environments 重构

上游删除顶层 `environments/`，将实际环境代码重构到 `tools/environments/`。我们的顶层 `environments/` 不是简单保留对象，应该在后续阶段分析后迁移合并。

### 6.4 Agent 新增

高价值候选：

- `agent/repetition_guard.py`
- `agent/empty_response_guard.py`
- `agent/error_surface.py`
- `agent/errors.py`
- `agent/deadline.py`
- `agent/estop.py`
- `agent/battery.py`
- `agent/conversation_compression.py`
- `agent/monitoring/`

暂不优先：

- `agent/pet/`
- `agent/billing/`
- 与 QQ Bot 当前目标无关的桌宠、计费或远程服务能力。

### 6.5 Gateway 新增

优先评估：

- `gateway/shutdown_watchdog.py`
- `gateway/shutdown_flush.py`
- `gateway/turn_lease.py`
- `gateway/turn_context.py`
- `gateway/session_stall.py`
- `gateway/session_state.py`
- `gateway/delivery_ledger.py`
- `gateway/slash_commands.py`
- `gateway/slash_access.py`

暂不优先：

- `gateway/relay/`
- `scale_to_zero.py`
- 与当前 QQ 机器人无直接需求的远程 relay/自动伸缩能力。

### 6.6 平台系统

上游已大规模转为 `plugins/platforms/*`。我们要保留多平台能力；不能因为当前主要使用 OneBot 就删除多平台框架。

当前 `plugins/platforms/onebot/` 是项目定制的权威实现，必须保留。其它平台是否从 `gateway/platforms/` 迁移到插件式目录，属于后续低优先级工作。

## 7. 依赖变化

上游依赖从 v0.13 的 19 项增加到 v0.20 的 33 项。新增方向包括：

- FastAPI/Uvicorn/WebSocket/Web 表单。
- Pillow、cryptography。
- Windows：`pywin32`、`pywinpty`、`concurrent-log-handler`。
- Linux：`ptyprocess`。
- `psutil`、`tzdata` 等跨平台支持。

上游移除的六项依赖，我们的代码仍在使用，必须保留：

- `anthropic`
- `exa-py`
- `firecrawl-py`
- `parallel-web`
- `fal-client`
- `edge-tts`

因此不能直接复制上游 `pyproject.toml`。应采用“上游依赖 + 我们仍使用的依赖 + 平台条件依赖”的合并清单，并在 Linux/Windows 两边分别验证离线 wheel 可安装性。

## 8. 迁移方案 A+B

### A：保守移植

目标是先吸收低风险、高收益的修复，不替换核心架构：

1. 先做依赖差异清单和离线安装验证。
2. 移植重复响应、空响应、错误分类、优雅关闭等健壮性模块。
3. 对 `model_tools.py`、`toolsets.py`、`run_agent.py` 只做逐项修复移植，不整文件覆盖。
4. 保留现有 OneBot、记忆、Live2D 和 QQ 脚本。
5. 每个小批次在 Linux 和 Windows 都跑编译、导入和相关测试。

### B：渐进式迁移

目标是逐步靠近上游 0.20.6 架构：

1. 以三方基线做迁移，不直接 merge 整个上游仓库。
2. 先拆 `hermes_state.py`，但保留我们的数据库行为。
3. 再建立自研记忆到上游 `MemoryProvider` 的桥接层。
4. 逐步吸收上游核心 agent/gateway 变化。
5. 将自定义顶层 `environments/` 迁移合并到 `tools/environments/`。
6. 保留 `plugins/platforms/onebot/` 和多平台框架。
7. 最后再考虑平台插件化、LSP、monitoring、cron 增强等可选项。

## 9. 推荐执行顺序

### P0：准备和基线

- 建立 Linux/Windows 两个平台的干净测试环境。
- 保存当前代码、运行数据和配置的可恢复快照。
- 记录当前测试基线，特别标注预先存在的 `_admin_id` 失败。
- 生成依赖差异、文件差异和冲突文件报告。
- 建立升级分支，不直接触碰生产分支。

### P1：低风险修复

- 先移植重复响应、空响应、错误处理和关闭 watchdog。
- 先不接入 MemoryManager。
- 每个功能单独提交和验证。

### P2：SessionDB 拆分

- 从我们的 `hermes_state.py` 拆分，不覆盖数据库逻辑。
- facade 保持旧 import 兼容。
- Linux/Windows 分别验证 SQLite、FTS、锁和重启恢复。

### P3：定制依赖重放

建议顺序：

```text
hermes_constants / hermes_bootstrap / hermes_logging
    -> 自研 agent/memory
    -> engine core
    -> tools
    -> tools/environments
    -> plugins/platforms/onebot
    -> QQ scripts / Dashboard / Live2D
```

### P4：记忆桥接

先做最小 demo，只验证：

- prefetch 能把已有记忆注入上下文。
- sync_turn 能写入 STM。
- memory 工具 schema 能被 agent 使用。
- 现有 SQLite 历史数据可读。

失败时回退到当前 `UnifiedMemoryGateway` 直接接入方式，不能让桥接失败阻塞机器人启动。

### P5：双平台回归

- Linux：全量核心测试、服务启动、优雅关闭、数据库恢复。
- Windows：安装脚本、UTF-8 stdio、NapCat、Live2D、Dashboard。
- 两边均验证 OneBot 消息、图片、记忆和多平台框架。
- Windows 跳过明确 POSIX-only 测试，但核心行为不可跳过。

## 10. 冲突处理方法

535 个冲突文件不能全部手工盲合。每个文件需要保存两条 diff：

```text
our-diff = upstream-v0.13 -> our-current
up-diff  = upstream-v0.13 -> upstream-v0.20.6
```

分类：

- `UP-HEAVY`：以上游为主，只回放少量定制。
- `OUR-HEAVY`：以当前项目逻辑为主，选择性吸收上游。
- `CONFLICT`：双方改动同一逻辑区，必须人工审查。
- `RENAME/MOVE`：跟随上游路径变化，并重映射项目 import。

已有机械分类结果：

- `UP-HEAVY`：306。
- `CONFLICT`：229。

注意：其中包含大量文档、locale 和可选技能的微小差异，不能把 229 个全部当作核心人工冲突。真正核心重点包括 `gateway/run.py`、`gateway/status.py`、`gateway/stream_consumer.py`、`agent/model_metadata.py`、`hermes_cli/doctor.py`、`tools/*` 和平台插件。

## 11. 双平台测试规则

Windows 测试不应粗暴删除，而应按能力标记：

- POSIX-only：`pty`、`fcntl`、`pwd`、`grp`、`os.killpg`、`os.getuid/euid`、cgroup、nix。
- Windows-only：pywin32、pywinpty、Windows 进程树、UTF-8 stdio。
- Cross-platform core：agent loop、tool registry、memory、OneBot、SQLite、配置解析必须两边运行。

当前上游测试扫描显示：`pty` 相关约 89 个文件，信号/进程相关若干，合计约 150 个文件可能需要 Windows 条件跳过。这个数量是初筛结果，正式迁移时要按测试实际 import 和 marker 复核，不能直接照数字批量排除。

## 12. 客观风险审查

当前方案仍有以下风险：

1. **直接切到上游 0.20.6 仍然风险很高**。535 个三方差异文件中，核心 gateway 和 agent 文件改动很大，不能把“基线替换 + 重放”理解成自动完成。
2. **记忆桥接风险高于计划中的一周估算**。`MemoryProvider` 的生命周期和自研网关的 session/episode 语义不完全相同，必须先做 demo。
3. **environments 迁移不是简单移动文件**。上游改变了 import 路径和工具组织，需确认 benchmark、terminal backend 和 RL 入口是否仍有需求。
4. **依赖版本锁定可能影响离线分发**。上游从范围版本转为精确版本，Linux/Windows wheel、Python 版本和架构都要实际验证。
5. **双 git 会放大生产风险**。`hermes/core` 的提交可能通过局域网同步到服务器，升级分支和生产分支必须隔离，不能在未灰度前直接推权威分支。
6. **上游新增内容很多但不代表都应该合入**。apps、website、contributors、tests、pet、billing 等需要按分发目标筛选，不能追求文件数一致。
7. **当前 upstream clone 是外部参照，不是项目的一部分**。必须保持在仓库外或明确 gitignore，不能把上游完整 clone 打包进用户分发物。

## 13. 下一会话启动步骤

下一次继续时，按以下顺序开始，不要直接修改核心文件：

1. 检查根 git 和 `hermes/` git 的 status，确认没有用户新改动。
2. 阅读本文件和 `hermes/docs/UPGRADE_PLAN.md`。
3. 确认上游工作区仍是 v0.20.6，并核对 commit。
4. 先生成可复现的依赖差异报告，补充 Linux/Windows wheel 清单。
5. 分析 `hermes_state.py` 三方差异，先写拆分设计，不立即改代码。
6. 选择一个低风险 guard 做 A 方案试点，单独验证和回滚。
7. 只有试点通过后，才进入 P1/P2 的批量迁移。

## 14. 当前未完成事项

- 尚未开始上游代码迁移。
- 尚未真正实现记忆桥接 demo。
- 尚未拆分我们的 `hermes_state.py`。
- 尚未迁移顶层 `environments/` 到 `tools/environments/`。
- 尚未完成 Linux 机器上的回归测试。
- 尚未清理 qqbot 静态配置 key 和历史注释。
- 尚未决定是否删除 `hermes/modules/` 和 `hermes/extras/` 空壳。
- 尚未提交本次改动。

## 15. 版本与 git 注意事项

- 当前根 `VERSION` 是 0.14.14。
- `pyproject.toml` 当前仍是 0.13.0，反映 Hermes 核心基线，不应未经设计直接改成上游版本。
- README 和安装脚本存在历史版本号漂移，发布阶段统一处理。
- 当前所有改动均未提交；提交前必须分别检查根仓库和 `hermes/` 仓库的 `status`、`diff`、`log`。
- 不要提交上游 clone、临时 worktree、分析临时文件或包含本地路径/服务器信息的文档。

## 16. 当前执行校正（2026-08-31）

第 1–15 节保留为 2026-08-29 的交接快照；本节是当前工作树的权威增补，防止下一会话把历史“尚未开始”描述误认为现状。

### 已落地的融合切片

- Gateway：错误 surface、空/重复响应防护、shutdown spool/恢复、session stall、turn lease、session boundary security、delivery ledger、restart source-cache/home lifecycle。
- Memory：MemoryProvider/MemoryManager 接口兼容、压缩前 checkpoint/evidence 端口、`BuiltinMemoryProviderAdapter` 隔离实现；本地 `UnifiedMemoryGateway` 仍是默认记忆真相，适配器没有自动 discovery 或双写。
- SessionDB：Gate 0–5 的只读 common/schema probe、FTS/CJK canonical fallback、portability audit facade；没有导入或改写 v11 数据。
- OneBot/NapCat：消息 envelope/capability/delivery 合同、私聊语音路径、WS/HTTP transport 合同、握手/health/receipt、endpoint 与媒体大小边界；OneBot 是唯一 QQ 运行路径，旧 QQBot 仅保留迁移哨兵。
- Environments/provider：capability snapshot、非文件描述符 stdout 回退、SSH/Docker 连接错误降级、Custom/Ollama profile 参数转发和配置模板同步。
- Environments E1：持久 Docker workspace 与 Singularity overlay 已接入 `sanitize_task_id_for_path()`，避免原始 session key 的路径穿越、volume 分隔符和替换碰撞；对应 `UPG-ENV-046`。
- Windows shell：`UPG-ENV-047` 已让本地 backend 优先 Git Bash，排除 `System32\\bash.exe` WSL shim；超时/cleanup 依赖的 `sleep` 命令已恢复。Docker override 执行位测试仍需单独做 Windows 条件适配。
- Environments E1 复审：`UPG-ENV-048` 已补齐 Windows 保留设备名/孤立 surrogate、profile-scoped snapshot 排除和 Docker forward-env 排除；`UPG-ENV-049`、`UPG-ENV-050` 已归档状态与 Windows 测试适配。
- Gateway 配置：`UPG-CONFIG-051` 已补齐 terminal bridge 的 Docker/Vercel 键，CLI/Gateway/terminal 三方配置映射回归 `39 passed, 1 skipped`。
- SessionDB 导入：`UPG-DB-052` 已建立显式 disabled/dry-run/enable 门禁和副本回放测试；当前只投影本地 v11 列，不启用 v26 mixin/import，也不触碰生产库。
- SessionDB 导入边界：`UPG-DB-053` 增加 240 字符 session-id 上限，portability audit/import 副本集合 `15 passed`；v26 schema/mixin、跨进程恢复和生产历史回放仍未完成。
- 安全收据：`SEC-REVIEW-054` 已复核 SessionDB importer、Environments E1、Gateway config、OneBot transport/media 等当前变更，结果 `0 reportable findings`；报告为本机临时工件，真实外部门禁仍 deferred。
- Docker E3：`UPG-ENV-055` 已加入 profile identity、bounded labels 和只清理 Hermes 自有 stale exited 容器的纯合同；未自动启用 runtime reuse、egress/network guard 或 daemon 操作。
- Docker runtime：`UPG-ENV-057` 已将 Hermes/task/profile bounded labels 接入新容器创建；没有启用跨进程 reuse、orphan 自动清理、egress/network guard 或真实 daemon 操作。
- 安全收据：`SEC-REVIEW-058` 已复核 Docker runtime labels、SessionDB importer、Environment E1、Gateway config、OneBot transport/media 等当前变更，结果 `0 reportable findings`；真实外部门禁仍 deferred。
- 安全收据：`SEC-REVIEW-056` 已复核 Docker E3、SessionDB importer、Environment E1、Gateway config、OneBot transport/media 等当前变更，结果 `0 reportable findings`；真实 runtime/外部服务门禁仍 deferred。

### 当前验证证据

- OneBot transport/ingress/runtime/contract/migration：`37 passed`。
- Gateway platform/delivery/restart/ledger/ephemeral：`152 passed, 3 skipped`。
- Environment/Base/Terminal：`44 passed`。
- E1 环境专项：task-id/profile snapshot/Docker/Singularity/timeout 集合 `62 passed, 1 skipped`；跳过项仅为 Windows NTFS 不提供 POSIX execute-bit 语义，Windows Git Bash timeout 子集 `2 passed`。
- SessionDB 当前完整聚焦集合（原有 schema/WAL/FTS/lineage + Gate 0–5 + portability import）：`270 passed, 1 warning`；warning 仍为既有 `core/tools/skills_guard.py:627` 非法转义。
- 相关 Memory/SessionDB/run-agent 主路径和完整 `test_run_agent.py` 已在前序切片完成回归；`py_compile` 与 `git diff --check` 通过。
- 最新变更集安全审查：`0 reportable findings`；唯一持续 warning 是既有 `core/tools/skills_guard.py:627` 非法转义 `SyntaxWarning`。

### 尚未达到发布条件的门禁

- NapCat 实连：需要操作者开启配置中的 loopback WS/HTTP 端口，先做只读握手/status，再做明确目标的最小发送回执；尚未执行真实登录、媒体 CDN、群聊灰度或 ACK 验证。
- SessionDB：v11 显式导入门禁已有 `14 passed` 副本证据，但上游 v26 mixin/import/migration、字段/列完整投影、失败保留、跨进程锁和双平台历史回放仍未完成。
- Environments：基础 bounded output/spill、profile snapshot 排除、task-id 路径隔离、Windows Git Bash 和 SSH/Docker 初始连接错误分类已完成；Docker reuse/egress、SSH bulk sync、Linux/Windows 远端/容器实测和跨进程 profile race 仍未完成，不得用 Windows local 通过替代 Linux 证据。
- Delivery/memory：跨进程 crash/ACK 丢失、媒体/cron 纳入 ledger、builtin adapter owner 去重和 checkpoint v2 证明仍未完成。
- 发布：版本号、公开 changelog、双 git commit/tag、GitHub Release 和线上灰度均未开始。

### 下一会话启动顺序

1. 先检查根仓库和 `hermes/` 仓库 status，确认没有新用户改动。
2. 让 Volta 继续负责生产代码与测试；根代理维护架构边界、变更日志和发布门禁。
3. NapCat 端口可用后执行只读 transport contract，再执行最小回执测试；失败时保留 evidence，不自动扩大发送范围。
4. 准备脱敏历史 SQLite 副本，先做 SessionDB portability/schema/search 回放，再讨论 v26 import。
5. 按 backend 逐项完成 environments 深层验证，最后才进入版本和双 git 发布流程。

### 文档与 git 纪律

- `docs/UPDATE_LOG.md` 记录每个已落地 Change ID；`docs/UPGRADE_PLAN.md` 记录阶段状态和 deferred 门禁；本文件只做会话交接，不替代变更日志。
- 当前所有代码和文档仍未提交；不得提交安全扫描临时目录、测试运行目录、上游 clone 或含本机/服务器敏感信息的文件。

### 最新 environments 进度（2026-08-31）

- `UPG-ENV-059`：Volta 已完成 Docker E3 runtime 的离线合同测试。复用查询按 Hermes ownership、task/profile label 和 egress fingerprint 限定，network mode 检查未知即拒绝；默认没有改变 Docker 构造生命周期，也没有接入真实 daemon 或 proxy。
- `UPG-ENV-060`：Volta 已完成 SSH bulk sync 的 POSIX 路径 containment 与结构化连接错误分类。同步目录创建、单文件 scp、bulk tar、download 和 delete 均有界；Windows 控制端的远端目录计算改为 `posixpath`，归档仍保留完整 `.hermes` 前缀。
- 最新离线证据：Docker/环境组合 `72 passed, 1 warning`；SSH bulk/upload/sync-back/file-sync `59 passed, 1 skipped, 1 warning`。OpenSSH 不可用导致的连接构造测试仍不能作为 live 证据；没有启动 Docker、SSH、Apptainer 或 NapCat。
- 下一步边界仍是三条：真实 Linux/Windows backend 证据、SessionDB v26 + 脱敏历史副本回放、NapCat loopback WS/HTTP 只读握手和最小回执。完成这些之前不更新发布版本、不提交双 git、不做群聊灰度。
- 安全审查 `SEC-REVIEW-061` 已完成：`0 reportable findings`，但 OneBot 媒体 DNS/SSRF 与大响应流式读取仍列为 deferred；后续不得把这张离线收据当作真实 NapCat/网络安全证明。
- `UPG-ENV-062` 已把 Docker runtime reuse 的最小构造链接上：配置从 CLI/Gateway/terminal 贯通，显式复用才会查找并 attach/start，失败回退新建；默认生命周期保持旧行为。待继续验证真实 daemon、健康等待、crash recovery、orphan sweep 和 egress/network 约束。

### 最新 SessionDB 进度（2026-08-31）

- `UPG-DB-064` 已建立 v26 结构前置合同：`hermes_state_v26_compat.py` 保存上游 v26 核心表/列和本地 v11 基线，`SessionDB.probe_v26_compatibility()` 对现有数据库做 read-only capability probe。
- 当前证据：v26 contract/schema probe/common helper `21 passed, 1 warning`；未触碰生产数据库，未执行 v11→v26 migration，也未改变 `SCHEMA_VERSION=11`。
- v26 真实融合仍需脱敏历史副本、Linux/Windows 回放、FTS/lineage/PK heal 迁移设计和跨进程锁验证；不能用结构清单或空库测试宣称已完成 v26 融合。
- 详细迁移映射已写入 `docs/SESSIONDB_V26_MIGRATION_MAP.md`，下一位执行者应按其中的副本 replay 顺序推进，不得直接复制上游 `hermes_state.py` 或先改版本号。
- `UPG-CONFIG-066` 已修正 `_get_env_config()` 的 backend-scoped parsing：坏的 Docker JSON 不再破坏 local/SSH，Docker 选中时仍 fail-closed；已有配置桥接测试保持通过。
- `SEC-REVIEW-067` 是当前稳定工作树的最终安全收据：`0 reportable findings`、coverage `complete`，且无工作树漂移警告；真实外部服务和 OneBot 媒体 DNS/流式门禁仍需后续执行。
- `SEC-REVIEW-068` 已复核包含 Docker lifecycle、SessionDB v26 contract 和 backend-scoped parsing 的最终稳定工作树，结果 `0 reportable findings`、coverage `complete`。下一步从脱敏历史 SQLite 副本开始双平台 probe/replay，不触碰生产库。

### 下一执行切片：SessionDB 历史副本回放

1. 准备经授权的脱敏副本并记录文件 hash、WAL/SHM 和 SQLite integrity 状态。
2. 在 Windows/Linux 分别运行 v11/v26 probe、migration plan、export/audit、search/FTS fallback 和 v11 dry-run importer。
3. 对比 sessions/messages/lineage/title/multimodal 内容和结果 shape，生成 rollback evidence。
4. 让 Volta 基于证据实现 v26 mixin 的增量写入门禁；未完成前保持 v11 生产路径和版本号不变。

### 副本回放工具（UPG-DB-069）

- `core/scripts/sessiondb_replay.py` 接受显式 `--source` 副本，默认 JSON 输出到 stdout，也可用 `--output` 原子写入报告。
- 报告只保留源文件名/hash、WAL/SHM/quick-check、schema/v26 plan、表计数、bounded export audit、canonical search 计数、import dry-run 和源未变化证据，不输出 session/message 正文或真实 ID。
- 工具拒绝当前运行 `state.db`、symlink、非 regular file、超过资源上限的输入和覆盖源文件；不执行迁移、DDL、FTS rebuild、WAL checkpoint 或生产写入。
- 当前合成副本回归 `18 passed, 1 warning`；待用户提供脱敏历史副本后，再按 `SESSIONDB_V26_MIGRATION_MAP.md` 做 Windows/Linux 回放。

### 最新 OneBot 媒体进度（UPG-OB-070）

- 三条 HTTP 媒体下载路径已改为统一流式读取：先做响应头长度检查，再逐 chunk 检查 20 MiB 硬上限，超过即关闭响应并返回安全错误；不再使用 `client.get()` 后才读取完整 `response.content`。
- 离线测试 `24 passed, 1 warning`；没有访问 URL、打开 NapCat 端口或改变 URL 来源策略。
- 仍需真实 NapCat/网络证据来决定 DNS/SSRF allowlist、重定向和 CDN allowlist；`file://`/base64 内联路径的读取预算也需另立门禁。

### 最新安全收据（SEC-REVIEW-073）

- 当前 working-tree diff 已完成一次完整 Codex Security 审查，结果为 `0 reportable findings`，coverage 为 `complete`。
- v26 copy-only gate 的 target/backup/hash/schema/version 前置、事务回滚、POSIX/Windows lock、sidecar 校验和报告脱敏均通过静态审查与 `69 passed, 1 warning` 聚焦回归。
- OneBot 媒体响应缓冲问题已由 streaming + Content-Length/chunk hard cap 关闭；voice 与 image/get_file URL 的 2 个 `CWE-918` 候选仍因缺少真实 NapCat URL provenance、DNS/redirect/private-address 和主机路由证据而 deferred。
- TAC 状态接口在当前工具面不可用；没有访问外部 URL、打开 NapCat 端口、连接 Docker/SSH 或读取生产数据库。详细安全工件保留在本机临时扫描目录，不进入双 git 或公开分发。

### 最新安全收据（SEC-REVIEW-075）

- 当前工作树已在包含 `UPG-DB-074` additive-column gate 的前提下重新完成 Codex Security diff scan，结果为 `0 reportable findings`，coverage 为 `complete`。
- v26 target/backup/hash/schema/version 前置、固定列 allowlist、`ALTER TABLE` 事务回滚、POSIX/Windows lock、sidecar evidence 和报告脱敏均通过；当前聚焦回归为 `74 passed, 1 warning`。
- OneBot HTTP response buffering 风险已由 streaming helper 关闭；voice 与 image/get_file URL 的 2 个 `CWE-918` 候选继续 deferred，等待真实 NapCat URL provenance、DNS/redirect/private-address 和主机路由证据。
- 安全扫描 ID 为 `a0922473-15f0-49a2-977a-f6b165d63e3c`；TAC 状态接口不可用，未打开 NapCat 端口，未访问外部 URL、Docker、SSH 或生产数据库。详细工件留在本机临时扫描目录，不进入双 git 或公开分发。

### 最新活动切片安全收据（SEC-REVIEW-077）

- `UPG-ACT-076` 已由当前工作树安全审查覆盖，结果为 `0 reportable findings`，coverage 为 `complete`。
- activity observation contract 对描述、来源、时间和 extra 诊断有界；SessionDB v11 缺列 no-op，v26 写入口仅显式启用并保持单调；当前 activity/v26/interrupt/steer 集合 `106 passed`，主 `run_agent` 回归 `323 passed`。
- OneBot voice 与 image/get_file URL 的两个 `CWE-918` 候选仍 deferred，等待真实 NapCat URL provenance、DNS/redirect/private-address 和主机路由证据；没有打开 NapCat 端口或访问外部服务。
- 扫描 ID 为 `546ed441-4712-4239-b4ca-1a82f16426e9`；TAC 状态接口不可用，详细工件只保留在本机临时目录，不进入双 git 或公开分发。

### 最新 v26 状态表安全收据（SEC-REVIEW-079）

- `UPG-DB-078` 已由当前工作树安全审查覆盖，结果为 `0 reportable findings`，coverage 为 `complete`。
- 七张 v26 状态表的固定 DDL、PK/FK/约束检查、copy-only target/backup/hash/sidecar、POSIX/Windows lock、事务 rollback 与 bounded report 均通过；v26/activity/replay/OneBot/环境聚焦集合为 `114 passed, 1 warning`。
- OneBot voice 与 image/get_file URL 的两个 `CWE-918` 候选继续 deferred，等待真实 NapCat URL provenance、DNS/redirect/private-address 和主机路由证据；没有打开端口或访问外部服务。
- 扫描 ID 为 `e7e3ff0b-61b2-4699-89ec-18c51ce1b19f`；TAC 状态接口不可用，工件只留在本机临时目录，不进入双 git 或公开分发。

### 最新 durable routing 安全收据（SEC-REVIEW-081）

- `UPG-GW-080` 已被当前工作树安全审查覆盖，结果为 `0 reportable findings`，coverage 为 `complete`。
- routing CRUD 使用参数化 SQL；scope 通过稳定 hash 隔离且不回显路径，entry/key/count 有界；v11 缺表严格 no-op，DB 失败保留 `sessions.json` fallback，v26 gate/活动/replay/OneBot/环境边界未发现可报告漏洞。
- 两个 OneBot URL `CWE-918` 候选继续 deferred，等待真实 NapCat URL provenance、DNS/redirect/private-address 和主机路由证据；当前没有打开 NapCat 端口或访问外部服务。
- 扫描 ID 为 `588f55f9-9c43-4de4-8c59-de7856364e48`；TAC 状态接口不可用，详细工件只保留在本机临时目录，不进入双 git 或公开分发。

### 最新 durable lease 安全收据（SEC-REVIEW-083）

- `UPG-GW-082` 已由当前工作树安全审查覆盖，结果为 `0 reportable findings`，coverage 为 `complete`。
- SessionDB lease 的 v26 表形状、参数化原子 acquire/refresh/release/get、过期回收、wrong owner 保护和 bounded time/TTL 均通过；async adapter 只在显式 enable 时工作并使用 `to_thread`，不接管默认 Gateway registry。
- OneBot voice 与 image/get_file URL 的两个 `CWE-918` 候选继续 deferred，等待真实 NapCat URL provenance、DNS/redirect/private-address 和主机路由证据；真实双平台 lease/WAL/owner fencing 仍未验证。
- 扫描 ID 为 `a5387b3d-13d6-4e9f-9d30-8f6671bde396`；TAC 状态接口不可用，未打开 NapCat 端口，工件只留在本机临时目录，不进入双 git 或公开分发。

### 当前增补：SessionDB canonical module ports（UPG-DB-084 / SEC-REVIEW-085）

- `core/hermes_state_common.py`、`core/hermes_state_schema.py`、`core/hermes_state_search.py`、`core/hermes_state_portability.py` 已作为上游模块名兼容边界落盘；它们不在 import 时打开 SQLite、执行 DDL 或替换本地 `SessionDB` facade。
- 本地 `SCHEMA_VERSION=11`、v11 `SCHEMA_SQL`、FTS/CJK/WAL/事务、OneBot session isolation 和 portability 默认门禁仍是权威路径。四模块只是 Gate 5 的边界准备，尚未接入上游完整 mixin 实现。
- 新测试 `core/tests/hermes_state/test_canonical_modules.py` 与既有 common/schema/search/portability 集合为 `36 passed, 1 warning`；warning 仍是预存 `core/tools/skills_guard.py:627` 非法转义；`py_compile`、`git diff --check` 已通过。
- `SEC-REVIEW-085`（扫描 ID `cc8b8787-a7f2-4603-af27-ca8da078c85d`）覆盖当前 68 个变更源码文件，结果 `0 reportable findings`、coverage `complete`。3 个候选继续 deferred：OneBot voice URL、image/get_file URL 的 DNS/SSRF/redirect 证据，以及未来 mixin 可能暴露的 `reasons_sql` 动态 SQL 片段。
- Volta 本轮代码已落盘，但最终摘要因协作模型服务 404/403 失败；根代理补齐了测试与日志收据。后续仍应优先恢复 Volta 的 gpt-5.6 luna(max) 执行能力，再做真正 mixin 接管。
- 下一步固定顺序：授权脱敏历史 SQLite 副本的 Windows/Linux probe/replay -> 逐项验证 helper 与本地返回 shape -> 单一 mixin 接入 -> 双平台 WAL/锁/回滚证据。未完成前不改版本号、不迁移生产库、不打开 NapCat 端口、不做 git 发布。

### Agent Runtime 规划增补（DOC-AG-087）

- 新增 `docs/AGENT_RUNTIME_THREE_WAY_MATRIX.md`，记录上游 v0.20.6 Agent Runtime 文件规模、与本地 `run_agent.py` 的职责差异、逐模块风险和固定融合顺序。
- 上游 `conversation_loop.py`/`agent_init.py`/`tool_executor.py` 等大文件不能覆盖本地循环；下一项优先候选是经过 message metadata 与去重契约约束的纯 `provider_projection`/`message_sanitization` 端口。
- 该增补只影响规划和审计，不表示 Agent Runtime 已完成上游融合；Volta 仍应在 gpt-5.6 luna(max) 服务恢复后负责生产代码和测试。

### Agent Runtime 消息清洗端口（UPG-AGENT-088）

- 新增 `core/agent/message_sanitization.py`，只惰性转发本地 `run_agent.py` 已验证的清洗 helper，并提供最小 `close_interrupted_tool_sequence()`；导入不会加载 `run_agent`。
- focused 测试为 `4 passed, 1 warning`；默认主循环、SessionDB、OneBot transcript 和 provider-specific policy 均未改变。
- 该端口不是上游完整 815 行模块；call-id、reasoning_content、message metadata 和真实 `TurnOutcome` seam 仍按 `AGENT_RUNTIME_THREE_WAY_MATRIX.md` 的门禁推进。

### OneBot 真实回环门禁（UPG-OB-090）

- NapCat 3000/3001 端口已监听；只读 HTTP `get_status` 返回 `403`，WS 初始帧返回 `status=failed`、`retcode=1403`，带 token 和不带 token 结果一致。未发送任何 QQ 消息或其它写 action。
- 本机 `websockets==12.0` 需要 `extra_headers`，适配器已兼容 12.x/15.x；首个认证失败帧现在会把 adapter 置为 `ws_auth_failed`/不可重试并关闭 socket，避免“TCP 已连但认证失败”的假连接状态。
- OneBot transport focused `19 passed, 1 warning`；下一步由用户在 NapCat WebUI 核对 token 并更新本地 `.env`，认证通过后才执行用户已授权的最小私聊/测试群发送回执。

### OneBot 账号自动发现与 Dashboard 选择（UPG-OB-092 / PLAN-OB-094）

- NapCat 登录后会为每个账号生成 `onebot11_<uin>.json`；当前 `config_discovery.py` 优先按 `ONEBOT_SELF_ID` 精确读取，未指定时按最新登录标记选择，token 不返回 Dashboard、不写普通日志。
- Dashboard 已提供当前实例账号列表/选择控件和 API；选择只写 `ONEBOT_SELF_ID` 与自动发现开关，运行中的 Gateway 需要用户主动重启。Dashboard 的 NapCat 服务路径已修正到真实 `modules/napcat`，Python 路径已修正到 `hermes/core`。
- 真实验证：自动发现当前账号后 WS 认证成功，`get_login_info` `retcode=0/status=ok`；按授权向指定私聊和测试群各发送 1 条联调文本，均取得消息回执。目标账号/群号未写入仓库。
- 当前只支持一台机器一个 Hermes Bot 实例。多账号/多 NapCat/多 Hermes 的对象模型、端口/PID/lock/profile/SessionDB/Memory 隔离和 P0-P4 门禁见 `docs/NAPCAT_MULTI_INSTANCE_PLAN.md`；完成前不启动多实例。

### 消息清洗端口安全收据（SEC-REVIEW-089）

- 当前 69 个变更源码文件已完成安全差异审查，结果为 `0 reportable findings`、coverage `complete`；扫描 ID `faea1023-81ed-48ab-b318-98e667e4d38e`。
- `agent.message_sanitization` 导入不会加载 `run_agent`，本地清洗 helper 行为等价测试和中断收尾测试通过；`tests/agent/test_message_sanitization_port.py` + `tests/hermes_state` 为 `101 passed, 1 warning`。
- 4 个候选继续 deferred：future finalizer 的 `final_response` 资源/编码边界、SessionDB 动态 SQL 片段、OneBot voice URL 和 image/get_file URL 的真实 SSRF 证据。未打开 NapCat 端口，未连接外部服务或生产数据库。

### `UPG-OB-098` 已实施：执行层输出契约与 exiting 状态机

- 用户反馈规格已落地：判定层决定“说不说”，执行层对已放行轮次要求可见正文；纯 `[QUIET]`/`[SILENT]` 或空输出最多同会话反馈重试一次；正文混合标记时发送正文并在成功交付后处理状态。
- 生产实现保持 `gateway/platforms/base.py` 不变：OneBot turn nonce completion、旧 session guard 释放等待、Gateway/AIAgent ephemeral feedback、内部 persistence suppress、`exiting_streak`、软收尾降级和 @/别名/reply 复位均已落盘。
- 已验证的 Windows 离线/合成证据：静默契约 `18 passed, 1 warning`，OneBot 配置/传输/运行时/契约/媒体 `45 passed, 1 warning`，Agent Runtime `351 passed, 1 warning`；唯一 warning 为预存 `skills_guard.py:627` 非法转义。代理声称的组合数字和本地复跑结果略有差异，以本地命令输出为准。
- 尚未完成的运行门禁：真实 NapCat/provider 空响应和 marker streaming、Linux/Windows 独立进程 guard/WAL、真实 Gateway + MemoryProvider/SessionDB transcript 回放、proxy ephemeral retry；完成前不更新版本号或公开 changelog。
- `SEC-REVIEW-100` 已完成当前 nested Hermes 快照的安全差异审查：76/76 review rows、`0 reportable findings`；contract retry 资源放大、动态 SQL 片段和两个 OneBot URL SSRF 分支继续 deferred。安全收据只覆盖 nested Hermes，根分发 inventory 仍受 Windows GBK 非 ASCII 文件名限制。

### `UPG-AGENT-104`：provider_projection 边界准备

- 已新增 `core/agent/provider_projection.py` 和 5 项离线测试，采用有界 assistant/tool 投影行、时间戳、provider iteration 计数和 512kB 行 payload 上限；import 不加载 `run_agent`、provider、SessionDB 或网络。
- 当前不接入 ACP/Codex 主循环，避免重复 tool rows、破坏本地 transcript 或改变 MemoryProvider/SessionDB 语义。下一步必须先建立去重 key、role pairing、真实 agent-as-provider response shape 和副本 persistence 证据。

### `SEC-REVIEW-107`：最新 nested Hermes 安全收据

- 当前 nested Hermes working-tree diff 的 77/77 review rows 已完成安全审查，结果为 `0 reportable findings`；扫描 ID 为 `bcd27a46-a55f-47f1-a111-e835379666d8`，coverage `partial`。
- 5 个候选均已记录验证与攻击路径：contract retry 资源边界、provider projection 未来接线、legacy `reasons_sql` 私有 helper、image/get_file URL、voice/record URL。前两类与 SQL helper 当前没有已证明的不可信可达路径；两个媒体分支仍需隔离的 redirect/DNS/private-address 负向证据。
- 这份收据只代表 nested Hermes，不能替代根分发全量扫描；根 inventory 仍受 Windows GBK 非 ASCII 文件名限制。未访问生产数据库、非回环网络、Docker、SSH 或 provider，工件只留本机临时安全目录。
- 后续固定顺序：Volta 先补 media negative tests；再准备授权脱敏历史 SQLite 做 Windows/Linux replay；随后才评估 SessionDB mixin 和 provider projection 主循环接入。未完成这些门禁前不改版本号、不做 git 发布。

### `UPG-OB-108` / `DEC-OB-012`：媒体 URL 下载门禁

- Volta 已落地 `validate_media_url` 的静态 authority 检查、下载前线程化 DNS 解析和三条 HTTP 媒体路径的 no-redirect；合法 loopback/file/公共 CDN 形状的既有行为保持兼容，voice `file_id` fallback 仍可用。
- 新增本地 fake resolver/httpx 回归 24 项；OneBot 全套 `87 passed, 1 warning`，媒体/stream/transport 聚焦 `48 passed, 1 warning`。未访问非回环网络、NapCat、生产数据库或真实凭据。
- 真实 NapCat URL provenance、连接级 DNS pinning/TOCTOU、Linux resolver/IPv6/subprocess 和真实 CDN allowlist 仍未证明；这批代码可作为离线硬化候选，不能当成发布级全平台安全结论。

### `SEC-REVIEW-110`：媒体门禁增量安全收据

- 以 nested Hermes 基线 `b9b0988` 完成 77/77 review rows 的安全审查，结果 `0 reportable findings`，扫描 ID `6a875f21-865b-47cb-b3d4-292ecfd356a9`，coverage `partial`。
- 5 个候选均完成 validation/attack-path：contract retry 资源边界、provider projection 未来接线、legacy `reasons_sql`、image/get_file URL 和 voice/record URL。当前没有已证明的可报告漏洞；后四项按未来接线或真实运行证据保留 deferred。
- 下一步仍是授权脱敏历史 SQLite 的 Windows/Linux replay，然后才推进 SessionDB mixin/Agent Runtime seam；provider projection 默认不接主循环，OneBot 真实 streaming/URL provenance 需独立门禁。未完成前不改版本号或做 git 发布。

### `UPG-DEPLOY-111`：OneBot 依赖模块双写补齐

- 发现并修复旧安装升级缺口：`adapter.py` 依赖的 `config_discovery.py`、`contract.py`、`transport_contract.py` 已加入 `extras/scripts/upgrade.py` UPGRADE_MAP，并同步 `UPGRADE.md` 目标路径表。
- 静态映射与临时目录双写回归、install 回归共 `8 passed`；不改变用户配置、SessionDB、NapCat 或版本号。该修复是部署完整性门禁，未重新宣称媒体/OneBot 真实环境已验证。

### `UPG-AGENT-112`：provider projection 测试顺序修正

- import smoke test 现在按前后状态比较，不受 `run_agent` 测试先后顺序影响；完整组合 `356 passed, 1 warning`。
- 该修正仅调整测试契约，不接入 provider projection 主循环，不改变本地 transcript、MemoryProvider、SessionDB 或 OneBot 运行路径。

### `UPG-DEPLOY-113`：本轮活动依赖端口双写补齐

- 升级清单已覆盖本轮新增/改动并进入活动路径的 Gateway ledger/stall/shutdown/lease、Agent guard/error、SessionDB canonical/replay 和 environment safety helper，并同步 `UPGRADE.md` 目标路径。
- 静态依赖断言 + install/upgrade 回归 `9 passed`；无数据库/NapCat/用户配置写入。历史升级清单的完整 import-graph 审计仍未完成，后续新增运行时模块必须先进入依赖闭包审计，再允许发布。

### `UPG-DEPLOY-114`：升级 import-graph 审计器

- 新增只读 `extras/scripts/audit_upgrade_map.py`，按 AST 解析本地运行时导入和 UPGRADE_MAP，Windows BOM 可读、单文件异常 bounded skipped、输出无绝对路径。
- 实际基线：559 个运行时文件、3180 条本地导入边、89 个显式 map entries、347 个显式未映射目标、0 skipped；动态 Python 闭包覆盖 559/559，`effective_missing_count=0`，`--strict` 通过。显式缺口主要含上游可选平台和 CLI，仍需分层决定非 Python 资产和发布策略。
- 审计器/依赖映射/install 回归 `13 passed`；非 Python 资产闭包、旧安装回放和双平台验证仍是进行中门禁，SQLite replay 和真实 provider/NapCat 证据顺序不变。

### `UPG-DEPLOY-115`：OneBot manifest 双写

- 当前 OneBot 活动插件的 `plugin.yaml` 已加入升级双写；上游其它可选 provider/platform manifest 不自动复制。动态 Python 闭包排除 tests/docs/隐藏目录，保持一机一 Bot 默认能力面稳定。
- 安装/升级/audit 回归 `15 passed`；无配置、数据库、NapCat 或版本变更。

### `SEC-REVIEW-116`：根 extras/scripts 安全收据

- 标准安全审查覆盖 root `extras/scripts` 10/10 文件，结果 `0 reportable findings`、coverage `complete`，扫描 ID `40fe82c0-b58c-40f1-b0be-4294682ca328`。
- 升级 copy containment/link refusal、动态 Python/AST boundedness、BOM/path-free 输出和本地 token 处理均有证据；这份收据不替代 nested Hermes 核心扫描，也不证明真实 NapCat/SQLite/Linux 运行门禁。

### `UPG-DEPLOY-117`：动态 runtime Python 闭包

- `upgrade.py` 现在在显式清单之外自动复制受限 `hermes/core/**/*.py`，排除 tests/docs/隐藏/VCS/source symlink；`audit_upgrade_map.py` 报告 559/559 runtime Python 的 effective coverage。
- 非 Python 资产和可选平台 manifest 仍必须分层进入显式清单；动态闭包不代表发布包整体已完成。

### `UPG-DEPLOY-118`：升级 containment/link 门禁

- 源/目标统一拒绝绝对路径、`..` 穿越、symlink/junction 和解析后越界；临时 traversal/link 回归通过，install/upgrade/audit 共 `15 passed`。
- 失败 fail-closed 且不写数据库/用户配置；旧安装回放、双平台和最终发布前安全复核仍 pending。

### `DOC-DEPLOY-119`：升级发布包分层矩阵

- 新增 `docs/UPGRADE_PACKAGE_MATRIX.md`：runtime Python 动态闭包、活动 OneBot manifest、模板/配置、可选上游插件 manifest、NapCat/Live2D 二进制按层管理，避免把所有上游 manifest 自动带入一机一 Bot profile。
- 当前 runtime Python effective coverage 为 559/559；活动 OneBot manifest 已显式双写。非 Python 资产、可选插件 profile、旧安装回放和 Linux/Windows 发布证据继续 pending。

### `UPG-DEPLOY-120`：动态闭包上限增量修复

- `upgrade.py` 达到 10,000 runtime Python 文件上限后立即停止增量 `rglob`，不再先 `sorted()` 物化完整目录树；新增上限回归，install/upgrade/audit `16 passed`。
- 本次改动后需以新的根 `extras/scripts` 安全收据为准；之前 `SEC-REVIEW-116` 只覆盖上一快照。

### `SEC-REVIEW-121`：根 extras/scripts 最终安全收据

- 当前 root `extras/scripts` 10/10 scoped files 的标准安全审查已完成，结果 `0 reportable findings`、coverage `complete`，扫描 ID `061fd5e8-c658-42a6-b29d-6440d1baf891`。
- 这张收据覆盖最终动态枚举上限、containment/link 门禁、AST audit、manifest 双写和 token 处理；install/upgrade/audit `16 passed`。nested Hermes 核心仍使用独立收据，不可混用范围。

### `UPG-DEPLOY-123`：stale upgrade entry 清理与完整 smoke

- 删除旧 OneBot/QQ/模板/NapCat/TTS 映射，修正 `配置API.bat`；临时完整升级 smoke 为 `585 files updated, 0 skipped`，活动 OneBot/Agent/SessionDB 文件均成功双写。
- install/upgrade/audit `16 passed`；无用户配置、数据库或 NapCat 写入，当前版本未发布。

### `SEC-REVIEW-124`：升级器最终当前工作树安全收据

- root `extras/scripts` 10/10 scoped files 完成标准安全审查，结果 `0 reportable findings`、coverage `complete`，扫描 ID `551fe70c-b9e3-416a-9d96-b068bc4fbc4b`。
- 最终复核覆盖动态 Python 闭包/增量上限、路径/link containment、AST audit、OneBot manifest 双写和 token 处理；`16 passed`，完整 smoke `585 updated, 0 skipped`。nested Hermes、SQLite replay、Linux/真实 provider 门禁仍按独立计划推进。

### `UPG-OB-125`：NapCat 当前账号只读连接

- 当前 NapCat 仅回环监听 3000/3001；adapter 自动发现凭据后正常连接，`get_login_info` 返回 `retcode=0/status=ok`，未发送任何消息或其它写 action，具体账号/token 未写日志。
- 这是当前认证/绑定的真实回环证据，不替代真实 media/streaming、Linux、SQLite replay 或多实例门禁。

### `UPG-DEPLOY-126`：Windows 部署入口路径统一

- Inno/NSIS/update/release builder 已对齐当前 `extras/scripts`、`modules/napcat`、`hermes/core` 和 `配置API.bat`；不存在的 FixNapCat/PeiZhiAPI 与旧 `scripts`/`napcat` 路径已清除。
- 静态 installer/setup/upgrade/audit `20 passed`，完整临时 upgrade `585 updated/0 skipped`；本机没有 ISCC/NSIS，真实 installer binary 仍 pending。

### `SEC-REVIEW-127`：extras 安装/更新安全收据

- root `extras` 14/14 scoped files 的标准安全审查已完成，结果 `0 reportable findings`、coverage `complete`，扫描 ID `96c19777-04ac-4228-9d75-473ca52fc3b5`。
- 路径、组件、配置保护和旧 helper 引用均已复核；`20 passed`。本机无 ISCC/NSIS，真实安装器编译、安装后启动和干净机验证仍是发布门禁。

### `UPG-DEPLOY-128`：NSIS editable install 目标修正

- NSIS 的 pip editable install 已从 `hermes` 根修正为 `hermes/core`，和当前 pyproject/requirements、Inno/update/release builder 事实一致；静态 installer/setup/upgrade/audit `20 passed`。
- ISCC/NSIS 缺失，真实 binary 编译和干净机安装启动仍 pending；无用户配置、数据库或 NapCat 写入。

### `SEC-REVIEW-129`：extras 安装器最终安全收据

- root `extras` 14/14 scoped files 的标准安全审查已完成，结果 `0 reportable findings`、coverage `complete`，扫描 ID `24ea7c7c-e58b-4d4a-9dfd-f43e4db34653`。
- Inno/NSIS、update/release builder、动态升级 containment 和配置保护均已覆盖；`20 passed`，临时完整 upgrade `585 updated/0 skipped`。真实安装器编译/干净机运行仍需工具链。

### `SEC-REVIEW-130`：extras 最终当前工作树安全收据

- root `extras` 14/14 scoped files 的最终标准安全审查结果为 `0 reportable findings`、coverage `complete`，扫描 ID `c38ec7d9-d154-4bcc-8ab1-35827bdbe44b`。
- NSIS `hermes/core` editable install、Inno/NSIS/update/release 路径、动态升级 containment 和配置保护均在同一快照；`20 passed`，临时完整 upgrade `585 updated/0 skipped`。ISCC/NSIS 缺失，真实 installer binary/干净机运行仍 pending。

### `UPG-DEPLOY-131`：升级器 dry-run 预演

- `upgrade.py --dry-run` 复用所有路径/link/动态闭包校验，只输出 `585 planned/0 skipped`，不创建目标目录、不复制文件，现有配置哨兵保持不变；正式临时 smoke `585 updated/0 skipped`。
- installer/setup/upgrade/audit `22 passed`；正式 binary 编译、旧安装回放和 Linux 证据仍 pending。

### `SEC-REVIEW-132`：extras/scripts dry-run 安全收据

- root `extras/scripts` 11/11 scoped files 的最终标准安全审查结果为 `0 reportable findings`、coverage `complete`，扫描 ID `8af92b60-7130-413a-94e9-f17d33b7fab1`。
- dry-run 与正式复制共享 containment/link/动态上限校验；`22 passed`，`585 planned/0 skipped` 和 `585 updated/0 skipped` 均通过。真实 installer binary、干净机、SQLite/Linux 和 provider/NapCat 门禁仍 pending。
