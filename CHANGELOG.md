# bot-template 更新日志
## v0.4.1.1 (2026-05-25)
- 修正 Dashboard 启动 NapCat 路径：`launcher.bat` → `napcat.bat`

## v0.4.1 (2026-05-25)
- 修复 WebSocket 断连：心跳 15s/30s（之前 20s/10s 太短）
- 优化图片识别：情绪/内容分类 + 多图编号 + 200 字限制
- DB 锁修复：buffer 写入异步化（fire-and-forget）

## v0.4.0 (2026-05-25)
- 从生产环境同步：OneBot 插件适配器更新、定时任务、技能文件
- 替换 NapCat 为 v9.9.27 纯净版（4 插件，防检测全开，319MB）
- 隐私清洗：API Key / QQ号 / Token / 用户名 → 模板变量
- 首个 GitHub Release 发布

## v0.3.133 (2026-05-21)
- QQ 适配器层最终防线过滤
- 记忆维护系统 (STM consolidation + 缓冲裁剪)
- auto-join 回复长度限制
- SOUL.md 括号禁令强化
- 模型供应商切换 OpenCode Go
- 包结构重构 (install.bat + start.bat + README)

## v0.2.0
- 首次通用发布