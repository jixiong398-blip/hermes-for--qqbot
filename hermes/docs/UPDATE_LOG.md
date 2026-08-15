# Hermes 服务器 → Win 端更新日志（docs 同步版）

> 权威日志：`WIN_UPDATE_LOG_v0.14.2.md`（服务器端完整版，含同步操作指南）
> 本文件为仓库内同步副本，Win 端 pull 后参考。

## v0.14.4 — @ 不回复 bug 修复（2026-08-04）

**Bug 汇总见 [BUGS_v0.14.4.md](./BUGS_v0.14.4.md)**

- 修复：群内 @Soyo 不回复/延迟/被覆盖（三层根因：判定主体漂移、`jt.seq` 属性错、@ 排队+覆盖）
- 行为约定：@ = 强信号，必判 @ 消息自己、不排队、不覆盖；防抖窗口照常
- 涉及：`core/plugins/platforms/onebot/trigger_coordinator.py`、`semantic_judge.py`

## v0.14.3 — core/ 结构迁移（2026-08-04）

- 引擎迁入 `core/`；systemd `WorkingDirectory` 指向 core/
- 局域网仓库 `~/hermes-core.git` 建立；同步范围 `core/ + templates/ + docs/ + .gitignore`
- live2d 模型 = 下载式（gitignore）；GitHub 发布结构由 Win 端决定
- 隐私红线：`.env`/`config.yaml`/`SOUL.md`/`sessions/`/`logs/`/`*.db` 永不进 git

## v0.14.2 — 两级窗口判定机制

- 旁观态 5s / 关注态 1s 判定窗口 + 15s 退出倒计时
- @ = 强信号进 judge；`exit_farewell` 可选最后一句
- judge thinking 默认 low（`JUDGE_THINKING` env）；性能修复（consolidation→to_thread、busy_timeout、context probe 关闭）

## v0.14.5 — memory_store.db 写锁修复（2026-08-05）

- 症状：episode 归档频繁 `sqlite3.OperationalError: database is locked`
- 根因：`episodic_index.index_session` 所有 fragment 共用一个事务，LLM 隐私判定（每 fragment 5-10s）在事务内执行 → 写锁持有 10-60s，其他写入撞锁
- 修复：每 fragment 写入后立即 `conn.commit()`，锁窗口缩至毫秒级
- 涉及：`core/agent/memory/episodic_index.py`

## v0.14.6 — @全体信号 + memory_store.db 锁问题根治（2026-08-05）

### @全体（[CQ:at,qq=all]）
- 之前：@全体完全无信号（不触发查看）
- 现在：进入关注态 + 立即 judge（查看非义务），prompt 标注"@全体，应认真查看"
- 涉及：`core/plugins/platforms/onebot/{adapter,trigger_coordinator,semantic_judge}.py`

### memory_store.db 锁问题（database is locked 反复出现）
- **根因**：`_rebuild_fts` 的 FTS5 rebuild 指令（`INSERT INTO fts VALUES('rebuild')`，内部 ~1461 次变更）执行后**从未 commit**——事务在事件循环线程连接上永久挂起（线程空闲但锁永久持有）→ 所有其他连接写入永久 BUSY
- **修复**：`_rebuild_fts` 每条 rebuild 后 commit + 异常 rollback（store.py）
- 连带修复：episodic 归档 fragment 立即 commit + 失败重试(5×) + rollback 防同连接 LOCKED 连锁；sleep_loop 每条 commit；吞异常点（create_memory_edge/prune）rollback
- 验证：修复后归档/蒸馏全部成功（indexed 3 fragments, distill promoted=8），0 锁警告

## v0.14.8 — 隐私修复 + 知识库路径统一（Win 端回传，2026-08-06）

**隐私清洗**（Win 端 v0.14.8 发布后回传服务器同步）：
- `templates/.env.template`：飞书真实凭证与服务器贴纸路径 → 全部占位符化
- 代码/文档中本地与服务器真实路径移除（`gateway.py` obsidian 默认、`obsidian.py` docstring、`sticker_curator_tool.py` schema、`smoke_corpus_history.py` 注释、CHANGELOG 等公开文档路径脱敏）
- CHANGELOG/UPGRADE/AGENTS.md 使用中性描述，隐私细节保留在 Win 端本地 MAINTENANCE.md（不上 GitHub）

**知识库路径统一（OBSIDIAN_VAULT_PATH）**：
- `core/agent/memory/gateway.py`：默认知识库路径 → **环境变量优先，未指定默认项目内 `modules/knowledge`**
- 服务器生产 `.env` 已配置 `OBSIDIAN_VAULT_PATH`（fallback 不触发，生产无影响）
- `extras/scripts/install.py`/`setup_config.py`：Win 端安装引导同步（仅模板分发，服务器无影响）

**变更原因**：Win 端 v0.14.8 GitHub 发布前全仓隐私审查——移除模板中的真实凭证与路径，统一知识库默认路径到项目内。服务器同步以保持代码一致性（生产已单独配置环境变量，实际行为不变）。

**涉及文件**：`templates/.env.template`、`core/agent/memory/gateway.py`、`core/agent/memory/obsidian.py`、`core/tools/sticker_curator_tool.py`、`core/tests/smoke_corpus_history.py`、`docs/UPDATE_LOG.md`

## v0.14.9 — 群聊执行契约修复（Kazusa 对照报告 review 落地）（2026-08-11）

对照 Kazusa 认知链报告（gateway 侧静态分析）审查 plugins 侧实际运行代码，修复 2 个真实风险：

1. **返回值契约错位**（报告 9.1 #4）：
   - `base.handle_message` 是后台语义（spawn task 立即返回 None），GroupExecutor 之前把返回值当回复文本（str(None)="None"）并在 agent 实际完成前更新群状态
   - 修复：adapter 新增 `_group_send_results` Future 注册表 + send() wrapper 幂等 resolve；GroupExecutor await 真实发送结果（45s 兜底），状态在发送后更新，reply_text 为真实文本
2. **judge 模式契约**（报告 9.1 #2）：GroupExecutor 模式分支无 judge → 落"[旁观模式]"措辞；新增 judge 分支（[对话模式] + judge reason）
- 涉及：`core/plugins/platforms/onebot/adapter.py`、`group_executor.py`

## v0.14.10 — 看图时序修复 + 记忆/成长决策存档（2026-08-15）

### 修复：发图后紧跟 @ 提问时 bot 看不到图
- 根因：executor 只等 latest_user 的媒体完成；发图后紧跟的 @/引用消息无 media task → 立即返回 → prompt 里图片还是 [image:pending] 占位 → bot 回复"没加载出来"
- 修复：`group_executor._run_turn` snapshot 后并发等待窗口内所有 [image:pending] 消息的媒体完成（单任务 30s 上限，最多 5 张）
- 附带：`media_pipeline` 下载失败时占位改为 [图片下载失败]（原来静默删标记，bot 误以为"没发图"）
- 涉及：`core/plugins/platforms/onebot/group_executor.py`、`media_pipeline.py`

### 决策存档（Kazusa 对照讨论结论，2026-08-15）
1. **记忆作用域 = 全局共享**：存储层不分用户/群桶（单角色多群人设，跨群记性好是特性）。真正的边界在**展示层**——检索进 prompt 时不该展现的内容不外显（当前 EPI share_level 分级已有基础，展示层过滤暂不做）
2. **身份成长 = 自传记忆**：不加自动人格修订（SOUL 人格由用户掌控，人工改 SOUL 即回滚）；最接近"成长感"的机制是 `core_memories`（SOUL 自传体记忆，`core_remember` self_write 写入，不衰减全量加载）
