# Environments 三方融合矩阵

> 维护者文档：记录上游 Hermes v0.20.6、fork 锚点和本地 QQ Bot 魔改版在执行环境层的真实差异。
> 本文件只定义合并边界和验收门禁，不宣称 environments 已完成上游全量迁移。

## 1. 参与方与目标

| 参与方 | 代码入口 | 角色 |
|---|---|---|
| 上游 v0.20.6 | `tools/environments/` | 通用环境协议、输出恢复、profile 隔离、Docker/SSH 生命周期 |
| 本地 fork/QQ 版 | `core/tools/environments/` | Windows Git Bash、Hermes 凭据清洗、QQ 分发兼容、现有终端入口 |
| 目标形态 | `core/tools/environments/` | 保留本地入口，逐项吸收上游能力；不改变 `terminal_tool` 外部结果契约 |

## 2. BaseEnvironment 差异

| 能力 | 上游 | 本地当前 | 融合结论 |
|---|---|---|---|
| `EnvironmentConnectionError` | 有，覆盖 backend 不可达/初始化失败 | 已有，SSH/Docker/terminal degraded 已接入 | 保留本地异常字段，逐 backend 对齐分类 |
| bounded output | `_BoundedOutputCollector`，40/60 head-tail，溢出后私有 spill，spill 上限 5,000,000 字符 | 已有 collector、spill safety、Windows 无 fd stdout 回退 | 以本地 collector 为基础补齐上游状态字段和回放测试 |
| task id 路径 | `sanitize_task_id_for_path()`，长度与 hash 双重限制 | 仍由各 backend 自行拼接路径 | 在 Base 提供纯 helper；Docker/临时文件统一使用，禁止原始 session key 进入 volume 名 |
| profile passthrough | `_profile_scoped_passthrough`、单调 snapshot 排除集合、`_export_dump_excluding_session_vars()` | Local/Docker 已接入 profile-scoped snapshot 排除与 source 后恢复；其它 backend 保持关闭 | 以当前实现和 8 项离线测试为 E1 基线；继续补真实多进程 race 与远端 backend，不得把 profile A 的 secret 留在共享 snapshot |
| Windows shell | 以上游通用 bash 流程为主 | 有 Git Bash/MSYS 路径转换、ASLR/venv/PYTHONPATH/HERMES_HOME 处理 | 本地 Windows helpers 是保留资产，不能被上游 Base 覆盖 |
| CWD/snapshot | 原子 `mktemp + mv`、non-login fallback | 已有 snapshot/CWD 和 no-fd fallback，但缺少上游全部 profile 排除接口 | 只合并缺失的接口与状态，不重写本地 shell 路径逻辑 |

## 3. LocalEnvironment 差异

本地 `local.py` 比上游多出一组 Windows/分发必需能力：

- MSYS/native path 双向转换和失效 cwd 修复；
- Git Bash 启动探针、ASLR 提示、coreutils PATH 修复；
- Hermes managed runtime、profile HOME、Hermes-owned `PYTHONPATH` 清理；
- provider secret blocklist、插件终端环境清洗、session context 注入；
- shell init 文件选择和 Termux/TMPDIR 兼容。

上游 LocalEnvironment 的 profile-scoped passthrough 和环境快照排除已有兼容实现，仍需真实多进程/跨平台证据。融合时的先后顺序必须是：

1. 先保留本地 `build_subprocess_env()`、`_sanitize_subprocess_env()` 和 Windows cwd 行为。
2. 再将上游 profile resolver 的结果投影到本地清洗层，不允许绕过 provider secret blocklist。
3. 对 `init_session()` 增加共享 snapshot 的跨 profile 回归；清空 allowlist 后旧 secret 仍必须保持排除。
4. Windows 不能以 Linux `bash -l` 通过替代 Git Bash、UTF-8 stdio 和进程清理证据。

## 4. DockerEnvironment 差异

| 能力 | 上游 v0.20.6 | 本地当前 | 门禁 |
|---|---|---|---|
| 容器安全 | capability drop、no-new-privileges、PID/CPU/memory、tmpfs/shm | 已有基础 security args、资源参数和网络开关 | 保留本地默认值，逐参数 mock argv，禁止改变默认网络语义 |
| task/profile identity | `hermes-task-id` + `hermes-profile` labels，profile-safe reuse | 生成容器名并支持持久目录，缺少完整 label/reuse/orphan sweep | 先纯函数验证 label/path，再做 fake Docker CLI 回放；不同 profile 不得复用容器或删除对方容器 |
| 跨进程复用 | reusable container、finished-at 检查、重建 | 当前主要是单进程生命周期 | 只在 copied sandbox 上启用，验证 crash/restart/cleanup；生产默认不得扩大 volume 范围 |
| egress | proxy、egress label/fingerprint、collision 检查 | 本地仅保留已有 network/forward env 行为 | 先做配置投影和安全冲突测试，再决定是否启用 egress enforcement |
| profile env | profile-scoped passthrough 与 unset 集合 | 仅有显式 forward env 和 blocklist | 显式 allowlist 优先级必须记录，隐式 passthrough 不能带出 Hermes provider secret |
| backend unavailable | 上游构造前检查 CLI/daemon 并返回 bounded degraded | 本地已接入 `EnvironmentConnectionError` | 保持 terminal 的 degraded JSON；不自动无限重试，不缓存失败 backend |

## 5. SSHEnvironment 差异

上游在 SSH 的核心增强是连接失败分类和文件同步生命周期：

- SSH/SCP 缺失、连接超时、远端目录创建、bulk upload/download、delete 失败都归类为 `EnvironmentConnectionError`；
- ControlMaster 使用短 hash socket，Windows 跳过不兼容的 Unix socket multiplexing；
- FileSyncManager 的同步动作在 command 前执行，失败不应被误报为远端命令的普通非零退出；
- cleanup、远端 `.hermes` 和本地临时目录均有界，失败时保留可重试状态。

本地 SSH 已有基础异常类、ControlMaster Windows 条件和 file-sync 入口，但仍需：

1. 为 `_establish_connection`、SCP、bulk sync、delete 分别补 fake subprocess 回归。
2. 确认 `key_path`、host、user、remote path 不进入用户可见错误的未截断文本。
3. Linux 真实连接只允许在脱敏测试主机进行；Windows 仅验证 OpenSSH 可用/不可用分支。
4. 远端环境必须保持 `is_local=False`，不能继承控制机的路径、平台或 secret 行为。

## 6. 融合顺序与不可变约束

### Gate E1：Base/common

- 合并 task-id sanitizer、profile snapshot 排除和输出 collector 状态；
- 保留本地 Windows path/env/UTF-8/cleanup 行为；
- `py_compile`、Base unit、output spill、snapshot race 双平台通过。

### Gate E2：Local

- profile resolver 只能缩小环境暴露范围；
- secret blocklist、HERMES_HOME、PYTHONPATH 和 shell init 不能回归；
- Windows Git Bash 实测前不得改默认启动方式。

### Gate E3：Docker

- 先 fake CLI 和纯 identity/path 测试，再接跨进程 reuse/orphan sweep；
- 不允许跨 profile volume/container 删除；
- Docker daemon 不可用时只返回 bounded degraded，不污染 terminal transcript。

### Gate E4：SSH/remote

- 逐操作错误分类、file-sync 和 cleanup mock 通过；
- Linux 脱敏主机实测、Windows OpenSSH 条件测试通过；
- 未完成前保持 remote backend deferred，不把 local 通过当作远程证明。

## 7. 目标不变量

1. `terminal_tool` 的命令非零退出仍是普通 `exit_code`，backend 不可达才是 degraded。
2. 所有用户/模型可影响的 task id、volume 名、spill 路径和日志字段都有界；不能直接拼入 shell/volume 规格。
3. profile A 的凭据不能通过共享 snapshot、Docker env、SSH 错误或 spill 文件泄露给 profile B。
4. 输出截断不能丢失可恢复路径；spill 创建必须拒绝 planted symlink 并使用私有权限。
5. cleanup、restart、跨进程恢复都必须可观察、可重试且不删除其他 session/profile 资产。
6. 本地 OneBot、SessionDB、MemoryProvider 和 Dashboard 的外部行为不因 environments 内部迁移而改变。

## 8. 当前状态

- E1 的 capability snapshot、stdout fallback、连接错误、task-id 路径隔离、Windows Git Bash 选择和 Local/Docker profile snapshot 排除已落地，对应 `UPG-ENV-032`、`UPG-ENV-033`、`UPG-ENV-039`、`UPG-ENV-046`、`UPG-ENV-047`、`UPG-ENV-048`、`UPG-ENV-049`、`UPG-ENV-050`。
- 上游 Docker profile/reuse/egress、SSH bulk sync/错误分类、远端真实测试和跨进程 profile race 尚未全部接入；当前保持 `environment-backend-deep-port` deferred。
- E3 第一片（`UPG-ENV-055`）已加入纯 Docker profile identity/label/orphan-reaper 合同和 fake CLI 测试；runtime labels、跨进程 reuse、network/egress guard 和 daemon 实测仍未启用。
- 本文件对应后续生产切片的输入，任何代码改变都必须新增 Change ID、测试证据和回滚说明。

### 当前切片增补（2026-08-31）

- `UPG-ENV-059`：Docker runtime 增加了默认关闭的 egress fingerprint、network policy、extra-args 冲突识别、profile/task label 查询和显式 `container_reuse_action()` 合同。查询只读、候选数量有界、未知 network mode 拒绝复用；Docker 构造函数仍未自动开启跨进程复用、proxy 注入或 orphan sweep。
- `UPG-ENV-060`：SSH bulk sync 增加 POSIX remote sync-root containment、bounded subprocess 诊断和 `EnvironmentConnectionError` 分类；`file_sync.unique_parent_dirs()` 改用 `posixpath`，Windows 控制端不再把远端目录改写成反斜杠。bulk upload 保留完整 archive prefix，仍解压到远端 `/`，避免改变既有 `.hermes` 布局。
- 离线证据：Docker identity/runtime/task-id/profile 及环境集合 `72 passed, 1 warning`；SSH bulk/upload/sync-back/file-sync 组合 `59 passed, 1 skipped, 1 warning`。唯一持续 warning 是既有 `core/tools/skills_guard.py:627` 非法转义 `SyntaxWarning`。
- 未完成门禁：真实 Docker daemon、iron-proxy/egress 接线、跨进程 start/health/recovery、Windows ControlMaster 条件化、Linux/Windows 脱敏远端实测、profile race 和性能数据仍 deferred；不能用离线 fake subprocess 证据替代真实环境证据。
- `SEC-REVIEW-061` 已对当前工作树完成差异安全复核：`0 reportable findings`；OneBot 媒体 DNS/SSRF 和响应流式限额作为外部证据不足的 deferred 候选保留。
- `UPG-ENV-062` 已把复用查询接入 Docker 构造器，并完成 CLI/Gateway/terminal 配置桥接；复用默认关闭，running 直接 attach、exited/created 只尝试一次 `docker start`，失败回退新建。真实 daemon、健康等待、crash recovery 和 egress enforcement 仍未开启。
