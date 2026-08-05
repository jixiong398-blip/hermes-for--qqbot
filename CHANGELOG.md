# bot-template 更新日志

## v0.14.6 (2026-08-05)

### memory_store.db 锁问题根治 + @全体信号

**数据库锁根治**（`database is locked` 反复出现）：
- **最终根因**：`_rebuild_fts` 的 FTS5 rebuild 执行后**从未 commit**——事务在事件循环线程连接上永久挂起（线程空闲但锁永久持有）→ 所有其他连接写入永久 BUSY
- **修复**：`_rebuild_fts` 每条 rebuild 后 commit + 异常 rollback
- **连带修复**：episodic 归档 fragment 立即 commit + 失败重试(5×) + rollback 防连锁；sleep_loop 每条 commit；吞异常点（create_memory_edge/prune）rollback
- **验证**：修复后归档/蒸馏全部成功（indexed 3 fragments, distill promoted=8），0 锁警告

**@全体信号**（`[CQ:at,qq=all]`）：
- 之前：@全体完全无信号（不触发查看）
- 现在：进入关注态 + 立即 judge（查看非义务），prompt 标注"@全体，应认真查看"

**涉及文件**：`store.py`、`episodic_index.py`、`adapter.py`、`trigger_coordinator.py`、`semantic_judge.py`

## v0.14.5 (2026-08-05)

### episodic 索引写锁修复

- **症状**：episode 归档频繁 `sqlite3.OperationalError: database is locked`
- **根因**：`episodic_index.index_session` 所有 fragment 共用一个事务，LLM 隐私判定（每 fragment 5-10s）在事务内执行 → 写锁持有 10-60s，其他写入撞锁
- **修复**：每 fragment 写入后立即 `conn.commit()`，锁窗口缩至毫秒级
- **涉及**：`episodic_index.py`

## v0.14.4 (2026-08-04)

### 群内 @机器人 不回复修复（同步自服务器）

**现象**：群里直接 @机器人（"今天天气怎么样"）时，偶尔不回复或延迟 20-40s，多次 @ 只回最后一次。

**三层根因**：
1. **judge 判定主体漂移**：`_judge_worker` 用 `latest_user`（最新消息）做判定主体，但 @ 消息后若有他人消息，judge 判定的是**别人的消息**而非 @ 消息 → LLM 看到"当前消息是别人的"就把 @ 忽略
2. **`jt.seq` 属性错误**：`_JudgeTask` 字段为 `initial_seq`，pending @ reschedule 后访问 `jt.seq` → AttributeError → 判定静默失败
3. **@ 排队 + pending 覆盖**：@ 时 judge in-flight，消息进 `pending_msg` 排队（最长 12s+）；pending 只有一份，多次 @ 时前面的 @ 被覆盖丢失

**修复**：
- judge 判定 = @ 消息（用 `initial_seq` 定位 buffer，不漂移）
- @ 后窗口内消息标 `follow_up`，不替换判定主体
- prompt 强化 @ 强信号规则（对应 @ 消息必须回复，不能回绝）
- @ 强信号不排队不等待：立即取消 in-flight 普通判定，走自己的 judge（1s 窗口）
- `should_reply` 结果携带 `is_mention` 给 executor

**行为约定**（防回归）：
- @ = 强信号，必进 judge；@ 消息自己不排队、不覆盖
- 窗口照常（attentive 1s / idle 5s / 退出倒计时 15s）
- 窗口内后续消息只是背景（可回可不回），不影响 @ 回复

**涉及文件**：`hermes/core/plugins/platforms/onebot/trigger_coordinator.py`、`semantic_judge.py`

## v0.14.3 (2026-08-04)

### 仓库结构重构 — core/ 分层 + 局域网 git 同步

- **引擎分层**：hermes/ 改为 `hermes/core/`（引擎权威，含 OneBot 插件），通过局域网 git（hermes-core.git）与服务器同步
- **根目录精简**：modules/（napcat/live2d/dashboard/knowledge）+ extras/（node/scripts/安装包/构建脚本）+ templates/ 独立于 hermes/，根目录只留安装脚本和文档
- **同步机制**：服务器权威 → `git fetch && git checkout origin/main -- core/` 增量同步；modules/extras 由 Win 本地维护
- **脚本路径更新**：install.bat/update.bat/upgrade.py/setup_config.py/qzone-post.py 指向新结构（extras\node、modules\napcat、hermes\core）
- **SSH 通道**：Win 端 SSH key 免密连接服务器（局域网 IP），替代不可靠的 Y 盘 SFTP
- **git 追踪精简**：hermes/ 只追踪 core/ + .gitignore（局域网同步范围）

## v0.14.1 (2026-08-03)

### Dashboard 视觉重构 + 性能优化 + NapCat 前置检查

- **前端视觉重构**（index.html）：更简洁现代的布局，服务状态卡片突出（状态圆点绿=运行/灰=停止），响应式
- **修复 5 个缺失 JS 函数**：loadWorkflows / runMaintenance / setVoiceMode / startNapcat / stopNapcat（原重构遗漏导致按钮失效）
- **日志区优化**：行间距收紧（line-height 1.25）、去掉 word-break:break-all（NapCat 二维码不再被拆行）、字号 13px
- **知识库标签**：长标签列表自动换行（word-break:break-word），不再溢出卡片
- **服务启动/停止秒响应**（server.py）：启动/停止改异步线程执行（原 2-4s → <0.1s）；`_check_port` 超时 2s → 0.5s；`_check_gateway_process` 先查端口再 WMI 扫描；仅在进程实际在跑时才强制 kill
- **Hermes 启动前置检查**（server.py + index.html）：启动 Hermes 网关前先检查 NapCat（进程 + 3000/3001 端口），未运行则弹 toast 提示"请先启动 NapCat"；日志输出 `NAPCAT_NOT_RUNNING` / `READY`
- **gateway.py 完整性修复**：从服务器拉取完整版（834 行，原截断版 804 行），补齐 `get_stats` / `get_workflow_decay_report` / `on_session_end` / `search_workflows` 完整实现

## v0.14.0 (2026-08-02)

### context 探测链关闭 + 磁盘缓存修复

- **探测链关闭**（model_metadata.py）：`_ENABLE_CONTEXT_PROBE` 默认 False（env `HERMES_CONTEXT_PROBE=1` 才开）——init 不再 30s×2 网络探测黑洞，配置即事实
- **`import json` 修复**（model_metadata.py）：磁盘缓存保存曾因 NameError 静默失败 → 收尾 2s 延迟消除
- **配置要求**：`config.yaml model.context_length: 1000000` 必须配（探测已关，不配走兜底）
- **episodic_index.py 同步**：dropped 修复 + 隐私清洗（MyGO 示例匿名化）
- **context_compressor.py 同步**：get_model_context_length 计时插桩
- **配置模板**：.env.template 加 XIAOMI 视觉变量、config-template.yaml 加 context_length、terminal.cwd 隐私中性化

## v0.13.2 (2026-08-01)

### 判定提速 + SQLite 锁修复 + 成本估算缓存

- **judge 提速**（semantic_judge.py）：`_judge_thinking_param()` thinking 默认 low（env JUDGE_THINKING 可切 disabled/low/default），5 处 LLM 调用加 thinking 参数 — judge 判定 11s → 3-6s
- **退出软处理**（trigger_coordinator.py + group_executor.py）：should_exit 分支 `exit_farewell=true` 才走 mode="exit"（回复后退出），否则静默 go_quiet
- **SQLite 锁修复**（agent/memory/store.py）：`sqlite3.connect(..., timeout=30)` + `PRAGMA busy_timeout=30000` — 修 database is locked
- **成本估算缓存**（agent/model_metadata.py）：`fetch_endpoint_model_metadata` 加内存缓存 — 修收尾 2s 延迟
- **judge LLM 计时插桩** + at_targets 指向规则完整化
- **L268 隐私清洗**：owner 描述通用化

## v0.13.1 (2026-08-01)

### 判定系统软改造 — @是强信号不是硬锁 + 驱赶软处理

- **judge 队列**（trigger_coordinator.py）：judge in-flight 时新消息入队（不再静默丢弃），当前轮结束后自动补一轮
- **软退出**（trigger_coordinator.py + group_executor.py）：should_exit 不再静默 go_quiet，改为回复最后一句（嘴硬/告别）后再安静，新增 mode="exit"
- **@ 软信号**：@mention 不再直发 agent，带 `_is_mentioned=True` 进完整 judge 流程（保留上下文/媒体），judge 可判不回（如 @ 骂人）
- **at_targets 指向规则**（semantic_judge.py）：at_targets 含自己 → 强正向；含其他人名 → 强反证（"玩去吧"等词是对别人说的）；recent 消息的 is_at/at_targets 同样有效
- **驱赶判定收紧**：should_exit 必须直接指向 $bot_name，对别人说的驱赶词不算
- **at_targets 运行时动态解析**（adapter.py + group_state.py）：`_group_uid_name_map` 从 buffer 动态学名字，零硬编码；BufferedMessage 新增 at_targets/at_self 字段
- **删除**：`_batch_has_dismissal` 硬驱赶词拦截（死代码）

## v0.13.0 (2026-08-01)

### 串线修复 + 慢响应修复 + 诊断插桩

- **串线修复**（adapter.py）：
  - 私聊 MessageEvent 从 image/mface/face 段提取 URL 填入 media_urls/media_types（原来硬编码 None）— 修复私聊图片被误当群聊图
  - `_get_image_files` face 段带 url/file 时不再跳过（动画表情丢失 → [图片] 占位）
- **慢响应修复**（memory_maintenance.py）：
  - 每小时蒸馏 + session 结束 consolidate 包 `asyncio.to_thread` — 修复同步 requests 阻塞事件循环导致收尾黑洞（16-43s）
- **诊断增强**：
  - vision_tools.py 报错明确化（"Local image path does not exist" 替代笼统 "Invalid image source"）
  - media_pipeline.py 失败诊断日志（显示段类型）
  - gateway/run.py + run_agent.py 增加 `[PERF]` 段级耗时插桩
- **隐私清洗**：semantic_judge.py owner 描述通用化（服务器端已同步）

## v0.12.6 (2026-07-31)

### 转发处理完整修复 + 角色名参数化 + 系统消息屏蔽

- **转发消息处理**（adapter.py）：
  - 转发文字进 session（不再 msg=''）
  - 嵌套转发递归展开（套娃 ≤5 层，防死循环）
  - 图片/语音/视频原位标注（不提到开头）
  - 转发阈值 500k（1M 上下文不轻易截断）
  - 超长 LLM 分段压缩（30k chunk）
  - 群聊 buffer 用完整 detail
  - 补 `_has_video_message`
- **角色名参数化**：`ONEBOT_BOT_NAME`（env > config.yaml > 默认 Soyo），judge/recorder/trigger 全模板化 `$bot_name`
- **Bug 修复**：
  - `_bg_review_send` 加 SUPPORTS_SYSTEM_MESSAGES 检查（💾 系统消息不弹 QQ）
  - mention 复位时清空 progression_guidance
  - should_exit 分支 @ 消息不 go_quiet
  - mention 模式跳过指导注入
  - 图片/语音识别 max_tokens 65536 + 超时 60s
- **字段改名**：`soyo_should_exit → should_exit`、`soyo_moves → bot_moves`（兼容旧键）、`soyo_reply → bot_reply`
- **安装/更新流程**：
  - install.py 完整重写（从 SOUL.md 提取角色名 + 容错编码）
  - upgrade.py 目标路径修复（双写 ~/.hermes/ + BOT_DIR）
  - 一键替换灵魂核心.bat 同步角色名到 .env
  - update.bat 自动调用 upgrade.py
  - FixNapCat.bat 引导改为 Dashboard（NapCat 配置文件唯一）
- **文档**：新增开源 AGENTS.md（面向下载用户 agent 的操作指南），本地维护文档改名 MAINTENANCE.md（gitignore）

## v0.12.5 (2026-07-31)

### adapter.py 同步 + 隐私清洗 + 数据流验证

- **adapter.py 同步**：从服务器同步 1470 行重大更新，覆盖 Phase 1 摄入/Phase 2 触发/Phase 3 执行全链路
- **post_reply_recorder 超时**：20s → 60s，修复回复后 episode state 更新因超时丢失
- **隐私全仓清洗**（佛像保留）：gateway.py 硬编码路径 → 环境变量 fallback、episodic_index.py docstring 匿名化、semantic_judge.py prompt 示例脱敏、onboarding.html / settings.html / package.json 占位符化
- **browser_tool.py / vision_tools.py**：修复尾行缺失闭合括号
- **全仓隐私扫描**：佛像之外零泄露（E:/ai、/home/ji/、QQ号、API Key、Token 全部通过）

## v0.12.4 (2026-07-31)

### 同步服务器修复 — Bug：Bot 不回复
- **P0 retrieval.py**：`MemoryRetriever.__init__` 增加 `epi=None` 参数 + `DEFAULT_SOURCE_WEIGHTS` 加 `"episode": 0.9` + `recall()` 加 episode 检索分支。修复 gateway.py 传 `epi=self._epi` 导致 `TypeError` → memory gateway 崩溃 → bot 不回复
- **P2 trigger_coordinator.py**：@mention 时增加 episode phase 复位（`exiting`/`winding_down`/空 → `starting`），修复对话收束后 @mention 被静默丢弃
- **P3 corpus_history.py**（新增）：FTS5 群聊全文搜索模块，解决 `chat_history_search_tool.py` import 找不到模块
- **UPGRADE.md** 重写到 v0.12.4，增加完整文件安装位置对照表
- 隐私清洗：gateway.py 默认路径、episodic_index.py docstring、semantic_judge.py prompt、onboarding.html、live2d settings.html、package.json

## v0.12.3 (2026-07-31)

### EPI 跨群记忆归档修复
- **episodic_index.py:L295**：补 `dropped = max(0, len(chunk) - len(lines))`，修复 `NameError: name 'dropped' is not defined` 导致 episode 归档定时任务崩溃

## v0.12.0 ~ v0.12.2 (2026-07-29)

### Episode State 系统
- 16 字段 episode state 注入 judge / executor
- `max_tokens` 全局统一 65536
- `chat_history_search_tool.py` 截断修复
- Stop-All.bat / start.bat 进程冲突检测

## v0.11.0 ~ v0.11.1 (2026-07-28)

### EpisodeIndex 记忆层（EPI）
- 跨会话联想记忆层：STM → EPI → LTM
- upgrade.py UPGRADE_MAP 补全（7 → 47 文件）

## v0.10.6 (2026-07-16)

### 消息处理架构重构 — 三阶段流水线
- **Phase 1**：buffer/persist/图片/judge 并发（semaphore 20）
- **Phase 2**：trigger_coordinator 决策层（1s timer + judge）
- **Phase 3**：group_executor 串行执行（群锁 + agent + 摘要 + 连续对话）
- 新增 `media_pipeline.py` / `trigger_coordinator.py` / `group_executor.py`
- 重写 `adapter.py`（774 → ~100 行）/ `group_state.py`（seq 体系）/ `semantic_judge.py`（fail-closed）
- 跨平台 SOCKS 代理修复 + `--replace` CLI 参数





## v0.10.5 (2026-07-16)

### 消息处理架构重构 — 延迟降低 40-70%
- **移除群锁**：Phase 1（buffer/persist/图片/judge）并发处理，Phase 2（agent/摘要）群锁串行
- **图片 fire-and-forget**：_preload_group_images 异步下载+识别，不阻塞消息处理
- **judge 延迟判定**：非对话态 1s timer 聚合，对话态/@@直接触发
- **_dispatch_to_agent** 统一入口：群锁 + 滚动摘要注入 + 连续对话检测

### 滚动摘要 + 连续对话
- **_update_rolling_summary**：agent 跑完后 DeepSeek 生成 2-3 句摘要，注入下一轮上下文
- **_check_continuation**：agent 跑完检查积压消息，持续对话循环（max 3 轮）
- **generate_rolling_summary**（semantic_judge.py）：摘要生成函数
- **group_state.py**：新增 olling_summary + last_agent_ts 字段

### 延迟对比
| 场景 | 旧 | 新 |
|------|-----|-----|
| 对话态 | 19s | 10s |
| @mentioned | 19s | 10s |
| 潜水态 | 19s | ~12s |
| 50条刷屏 | 450s | ~12s |
## v0.10.4 (2026-07-16)
- **qq-db-recover.py 重写**: 修复致命 bug — [40090] 列是群名片不是消息文本，改为从 [40800] protobuf BLOB 提取真文本（CJK 片段过滤，178K/182K 覆盖率）
- **新增 extract_qq_chat.py**: 独立聊天导出工具，纯文本输出
- **新增 qq_chat_restore.py**: 从服务器同步的完整恢复脚本（3 层提取 + 回复链重建）
- **列映射修正**: [40030] 替代 [40021] 做群号过滤，[40003] 替代 [40002] 做发送者 QQ 号
- **解密 DB 保留**: 不再自动删除，供用户自行备份
- **SKILL.md 同步**: qq-db-decrypt 方法论更新（CJK 提取 + 列映射）
- **隐私清洗**: AGENTS.md + SKILL.md 全部真实值 → 占位符

- **qq-db-recover.py 重写**: 修复致命 bug — [40090] 列是群名片不是消息文本，改为从 [40800] protobuf BLOB 提取真文本（CJK 片段过滤）
- **文本提取方法**: UTF-8 解码 → 去 protobuf 噪音（文件引用/下载URL/hex hash） → 留 CJK 片段拼接
- **解密 DB 保留**: 不再自动删除解密后的 QQ 数据库，供用户自行备份

## v0.10.3 (2026-07-16)
- **state.db 损坏修复**: persist worker 缺 PRAGMA journal_mode=WAL 导致 SQLite B-tree 损坏（#bug-report），新增 WAL + NORMAL synchronous
- **数据恢复指南**: 见下方「数据库恢复」章节

### 数据库恢复
如果 state.db 损坏（database disk image is malformed），恢复步骤：
1. 停止 Hermes Gateway
2. 删除损坏的 state.db（~/.hermes/state.db）
3. 重启 Gateway — persist worker 会自动建表
4. 通过 NapCat debug WS 重新加载 dbexport 插件解密 QQ 本地数据库
5. 如有备份，从 ~/.hermes/state.db.bak.* 恢复
6. 运行 hermes/scripts/qq-db-recover.py 补入群聊历史

> 更多细节参考 hermes/skills/devops/qq-db-decrypt/SKILL.md

## v0.10.2 (2026-07-14)
- **CDN 模型分发**: 移除 Git LFS 模型追踪，改为 CDN 下载 + 内置 decrypt_cvpkg.py 解密
- **解密脚本内置**: scripts/decrypt_cvpkg.py（纯 stdlib，141 行），download-backend.js 路径改为相对
- **Dashboard 空模型检测**: /api/live2d/models 返回 empty 字段 + hint 提示
- **install.bat**: Live2D 步骤从"预安装"改为"Dashboard 下载"

## v0.10.1 (2026-07-14)
- **qzone-post.py 修复**: {{ONEBOT_TOKEN}} 占位符替换为运行时自动检测（env → config.yaml → NapCat onebot11_*.json），用户不再需要手动填 token


## v0.10.0 (2026-07-13)

### Live2D Cubism 5 — 完整重写
- **渲染引擎**: pixi.js 7 + pixi-live2d-display-cubism4 + Live2D Cubism 5 Core
- **角色模型**: 12 位 Cubism 4/5 角色 (anon, mutsumi, nyamu, rana, sakiko, soyo, sub, taki, tomori, uika, umiri, yachiyo)，旧 Cubism 2 `assets/figure/` 已删除
- **Hermes WS 协议**: `ws://127.0.0.1:19919/hermes`，支持 loadModel/expression/motion/look/getScreenshot/listModels
- **设置面板**: settings.html (4 标签: 显示/模型/Hermes/关于)
- **下载管理器**: download.html + download-backend.js，CDN 下载 .cvpkg + 解密安装
- **右键菜单**: 自定义菜单（点击透过/置顶/鼠标跟随/帧率/窗口大小/直播模式/切换角色）
- **Dashboard 集成**: 模型切换 + 默认保存 (live2d_pref.json) + 12 角色扫描
- **Bug 修复**: koffi 依赖缺失 → fix `npm install`；preload.js electronAPI 桥接恢复；switch_model→loadModel 转译

### 转发消息 — 检测加固 + 私聊修复
- **服务端同步**: adapter.py 3 级 fallback 转发提取 + 64 行死代码删除
- **DM 锁 + 去重**: `_dm_locks` 逐用户串行 + `_seen_forward_ids` 5 秒去重 + 50 条 prune
- **语义判断重写**: semantic_judge.py 全文重写 (189→303 行)，新增噪音等级计算/连续性检测/间接对话/指向证据层级
- **Gateway 补丁**: SUPPORTS_SYSTEM_MESSAGES → 防状态消息泄漏到 QQ 群

### 配置工具 v2.0 — `配置API.bat` 重写
- **13 家 LLM 供应商**: DeepSeek/OpenCode Go/智谱GLM/火山方舟/阿里百炼/MiniMax/Kimi/OpenAI/Anthropic/SiliconFlow/OpenRouter + 自定义
- **7 家视觉供应商**: OpenCode Go/智谱GLM/火山方舟/阿里百炼/OpenAI/TokenPlan + 自定义
- **Token 自动读取**: 从 NapCat `onebot11_{QQ}.json` 自动提取 access_token
- **交互流程**: 供应商 → 模型 → 密钥，OpenCode Go 一键复用
- **隐私清洗**: config-template.yaml 全部占位符，删除硬编码 feishu/anysearch key

### Dashboard 增强
- **NapCat 配置**: 端口状态 + 反检测一键 + WebUI 链接
- **Live2D 控制**: 模型切换 + 默认保存 + 管理模型按钮
- **隐私**: QQ 官方 Bot 配置已删除 (.env + config-soyo.yaml)

### 工程改进
- **Git LFS**: 模型文件走 LFS (1.14 GB → 指针文件)
- **隐私审查**: 全仓库 API Key/Token/QQ 号/硬编码路径扫描通过
- **install.bat**: Live2D 步骤更新 + koffi 依赖安装

## v0.9.3 (2026-07-07)
- **静默 Bug 清扫**: 修复 8 个被 except Exception 吞掉的静默失效
- 语音转写: MiMo schema 修复 (400→200) + 持久化对齐图片分支
- session 过期 watcher: 字段改名导致 MEMORY.md flush 冻结 2 月 → 修复 4 处
- consolidate: self.stm→self._stm 字段名修复
- 每小时蒸馏: 从未运行 → 恢复 (memory_maintenance.py)
- flush agent: 手动初始化 memory store
- home channel 提示: 检查 config.yaml 而非仅 env
- 回归测试: 4 个 test 锁定修复

## v0.9.2 (2026-07-07)
- **脑功能分区**: CORTEX.md + CEREBELLUM.md 架构，SOUL→CORTEX→CEREBELLUM 注入链
- **安装引导**: Dashboard onboarding 三步向导 + GitHub 更新检查 + NSIS 安装包
- **稳定性**: persist worker 错误分类捕获 + health_check.py 四项健康检查
- **Agent 自查询**: tools_list_tool.py 78 个工具清单
- **Bug 修复**: @ 引用乱序、私聊崩溃、onboarding 占位符脱敏

## v0.9.1 (2026-07-04)
- **QQ 数据库解密工具**: qq-db-decrypt skill + decrypt plugin + recovery script
- 可从 QQ 本地加密 `nt_msg.db` 恢复完整群聊历史（突破 NapCat API 25,000 条上限）
- 零硬编码：passphrase 运行时从 NapCat 获取，路径自动探测
- 新增 `backfill_corpus.py` 备用恢复（NapCat API 方式）

## v0.9.0 (2026-07-01)
- **零硬编码隐私**: QQ号/Token/路径 → os.getenv() 配置外提
- **P0 修复**: session:end 传递 session_id，consolidation 正常触发
- **完整记忆系统**: LLM 蒸馏提取 + 1天半衰期 + 增强召回评分
- **@Soyo 误判修复**: 反子串匹配 + @匿名化保护
- **图片上下文隔离**: 纯文本消息不污染旧图描述
- **离线优先安装**: install.bat 环境检测 + 跳过已安装 + 离线包优先
- **install.bat 重写**: Python 版本检查 + Node.js 智能检测 + 路径空格兼容
- **新增工具**: sticker_curator, check_memory, evening_news, render_briefing
- 删除所有 .sh 文件，Windows-only 发布

## v0.5.4 (2026-05-27)
- Dashboard NapCat 启动命令修正 (launcher.bat → napcat.bat)
- GPT-SoVITS TTS 适配器整合到 Hermes 工具链
- Live2D WS 服务器自动随 Gateway 启动/停止
- voice_mode smart_voice 模式恢复 (LLM 自判语音合成)

## v0.5.3.1 (2026-05-26)
- 完整贴纸系统：自定义贴纸 + QQ 原生表情 CQ 码转换
- 群聊触发修复：仅 @ 和 # 触发，auto_join 主动插话
- Gateway HERMES_HOME 路径修正（Dashboard 传 ~/.hermes 而非代码根目录）
- PeiZhiAPI.bat 去掉知识库路径手动输入
- 飞书平台默认关闭
- 配置模板全部 API URL 改用 {{占位符}}
- NapCat 停止修复：Dashboard 正确终结 node.exe 进程
- 新增 Stop-All.bat 一键停服
- 新增 FixNapCat.bat 登录后自动开启 WS/HTTP 端口
- README 部署流程更新

## v0.5.2 (2026-05-26)
- requirements.txt 纯 ASCII（修复 pip GBK 崩溃）
- adapter.py 编码修复（UTF-8 乱码 → 从本地 Windows 源二进制复制）
- Dashboard 日志行间距优化（12px/1.3）
- Live2D 前端保存默认模型按钮
- install.bat 五步完整流程（Python → Node.js → venv → Hermes → Config）
- 独立 Node.js 目录（npm/npx，用于 Live2D）
- 新增 Install-Live2D.bat + UPGRADE.md + upgrade.py
- Live2D kill 模式修复 + 默认模型保存到 live2d_pref.json
- knowledge-base 插件补全 + 去硬编码 ~/ai/ai

## v0.5.1 (2026-05-26)
- adapter.py 编码修复（插件 + 网关两份）
- NapCat kill 模式更新
- Live2D 路径相对化 + kill 修复
- Gateway 收发排查

## v0.5.0 (2026-05-26)
- Dashboard server.py 重写（1339→330 行）
- 全部硬编码路径清零
- .bat UTF-8 BOM + CRLF
- install.bat 始终装内置 Python 3.12
- pip 精简：--no-deps + minimal requirements.txt
- TTS 模块附带 ts_adapter_template.py

## v0.4.x (2026-05-25)
- NapCat 升级 v9.9.27 / WS 心跳优化 / 图片识别 / 隐私清洗 / 多供应商 API 配置






