# NapCat 多账号 / 多实例长期升级计划

> 状态：2026-09-01。当前生产策略为一台机器部署一个 Hermes Bot 实例；本文件描述未来扩展边界，不授权现在启动多进程或迁移数据。

## 1. 当前阶段

当前只实现“单机单 Bot + Dashboard 选择 NapCat 登录账号”：

- NapCat 登录后按账号生成 `onebot11_<uin>.json`。
- Dashboard 列出账号专属配置的安全摘要，选择后只写入当前 Hermes 实例的 `ONEBOT_SELF_ID` 和 `ONEBOT_AUTO_DISCOVER_TOKEN=true`。
- Hermes adapter 在回环 HTTP/WS 模式下按 self-id 精确读取对应 token；未设置 self-id 时使用最新 `napcat_protocol_<uin>.json` 登录标记。
- token 只在本机进程内使用，不返回给 Dashboard、不进入普通日志、不写入公开文档。
- 选择账号后不自动重启正在运行的 Gateway；返回 `requires_gateway_restart`，由用户确认重启，避免中断当前会话。

## 2. 未来对象模型

未来多账号/多实例必须显式区分以下对象，不能继续用一个全局 `napcat` 服务字典代替：

| 对象 | 关键字段 | 所有权 |
|---|---|---|
| `NapCatAccount` | `account_id`、登录状态、账号专属 config 文件、token 来源 | NapCat 配置发现器；token 不出内存 |
| `NapCatInstance` | `instance_id`、进程 PID、NapCat 根目录、配置根、HTTP/WS 端口、版本、健康状态 | Dashboard 进程编排器 |
| `HermesBotInstance` | `instance_id`、Hermes profile、`HERMES_HOME`、Gateway 端口、SessionDB 路径、Memory namespace | Gateway/profile 管理器 |
| `OneBotBinding` | `bot_instance_id`、`napcat_instance_id`、`account_id`、WS/HTTP endpoint、连接状态 | OneBot adapter registry |

关系约束：


- 一个 `HermesBotInstance` 在当前阶段只能绑定一个 `OneBotBinding`。
- 一个 `NapCatInstance` 的 HTTP/WS 端口不能与其它实例重叠；不能共享同一运行态 config 文件。
- 每个 Hermes profile 必须有独立的 Gateway PID/lock、SessionDB、delivery ledger、routing scope 和 memory namespace。
- `session_key`、`session_id`、账号 ID 和实例 ID 不能互相替代；跨实例转发必须写 lineage/来源，不得把另一账号的 transcript 注入当前 Bot。
- token 只能来自账号专属 NapCat config 或用户显式配置；Dashboard API 只返回摘要和状态。

## 3. 分阶段路线

### P0：单实例账号选择（当前）

- 完成账号专属 token/self-id 自动发现。
- Dashboard 提供账号列表和选择 API/UI。
- 选择只持久化非秘密 selector；Gateway 运行中提示重启，不自动重启。
- 保留现有 `~/.hermes`、v11 SessionDB、STM/EPI/LTM 和 OneBot 群隔离。

### P1：单机多账号的管理视图（只读）

- Dashboard 显示每个账号的登录/端口/认证健康状态和最后探测时间。
- 允许为一个当前 Hermes profile 保存 binding 草稿，但一次只激活一个账号。
- 为配置文件做 regular-file、大小、版本和 token 一致性检查；不做跨进程启动。
- 需要 fixture 覆盖多个账号、旧账号文件、登录切换、配置损坏和 selector 回滚。

### P2：多 NapCat 实例进程编排

- 每个 NapCat 实例拥有独立配置根、账号、HTTP/WS 端口、PID/lock 和日志流。
- Dashboard start/stop/restart 必须按 `instance_id` 操作，禁止按裸端口或模糊进程名误杀其它实例。
- 端口分配使用显式保留表和启动前 probe；实例崩溃恢复、孤儿清理和升级必须有 owner label。
- Windows 进程树、权限、窗口句柄和 Linux signal/systemd 路径分别验证；默认仍关闭。

### P3：多 Hermes Bot 实例

- 每个 Bot 使用独立 Hermes profile 与 `HERMES_HOME`，隔离 SessionDB、Memory、logs、gateway port、delivery/routing state。
- 共享只允许显式的只读资源（角色模板、知识库快照）；消息 transcript、记忆和权限默认不共享。
- OneBot binding 切换必须是带版本的状态变更，旧 binding drain 后新 binding 才能进入 running。
- 任何 profile/adoption 或历史数据库迁移都必须在副本上完成，不能直接复用当前 v11 `state.db`。

### P4：统一 Dashboard 控制平面

计划 API（命名暂定）：

- `GET /api/napcat/accounts`：账号摘要，不返回 token。
- `POST /api/napcat/accounts/select`：为当前 profile 选择账号，返回是否需要重启。
- `GET /api/instances`：NapCat/Hermes 实例和 binding 状态。
- `POST /api/instances`：创建实例草稿，先做端口/config/profile 冲突检查。
- `POST /api/instances/<id>/start|stop|restart`：按实例 owner 操作进程。
- `GET /api/instances/<id>/health`：loopback、认证、协议、DB/memory scope 健康摘要。

所有写操作必须有 idempotency key、并发版本和回滚记录；API 不返回 token、完整环境变量、绝对私密路径或消息正文。

## 4. 保留的本地资产

多实例扩展不能替换以下本地魔改：

- `UnifiedMemoryGateway` 的 STM/EPI/LTM/Workflow/Wiki 语义和跨群联想边界；
- SessionDB v11 的 FTS/CJK、WAL fallback、database-lock 修复、lineage 和返回 shape；
- OneBot 群锁、judge/trigger/group executor、媒体字段和私聊语音降级；
- Gateway shutdown spool、delivery ledger、turn lease、error surface 和恢复顺序；
- Dashboard 的本地维护日志与公开 GitHub changelog 分层。

上游的 SessionDB v26、MemoryProvider plugin discovery、Gateway routing 和多实例对象只能以可选增量接入，不能用全局表或全局字典覆盖上述资产。

## 5. 进入多实例前置门禁

在 P2 之前必须完成：

1. Windows/Linux 独立进程的 PID/lock/端口/WAL/日志 owner 证据；
2. 多账号配置 fixture 和真实 NapCat 登录切换回放；
3. Hermes profile、SessionDB、memory namespace 的隔离测试；
4. Dashboard 写 API 的鉴权、CSRF/并发版本、secret redaction 和定向进程清理；
5. 迁移/升级/回滚演练，确认旧单实例可以无损回到 P0；
6. 公开文档只描述中性能力，内部 `UPDATE_LOG.md` 保留运维细节，不把账号/路径/token 上传到 GitHub。

未满足这些门禁前，生产只支持一台机器一个 Hermes Bot 实例；不以“发现了多个 `onebot11_*.json`”作为多实例支持的证明。
