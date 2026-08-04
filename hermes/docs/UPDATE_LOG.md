# Hermes 服务器 → Win 端更新日志（docs 同步版）

> 权威日志：`/home/ji/ai/WIN_UPDATE_LOG_v0.14.2.md`（完整版，含同步操作指南）
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
