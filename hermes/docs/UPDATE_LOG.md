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
