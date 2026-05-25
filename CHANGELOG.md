# bot-template 更新日志
## v0.5.0 (2026-05-26)
- **Dashboard 全重写** — server.py 从 1339 行精简到 330 行，index.html 从 436 行重构
- 所有硬编码路径清零（E:/ai、/home/ji、~/ai/ai 等）
- TTS 服务改为可选项，附带 ts_adapter_template.py 示例脚本
- Live2D 路径自动检测，不存在时优雅降级
- 服务管理统一 kill 逻辑，修复 NapCat 停止失效
- 知识库插件缺失文件补充 + 导入路径修正
- .bat 全套 UTF-8 BOM + CRLF 编码，中文 Windows 不乱码
- install.bat 始终安装内置 Python 3.12 + 自动 PATH
- pip 安装精简：--no-deps + minimal requirements.txt（38 包）
- 新增 FixNapCat.bat 登录后自动开启 WS/HTTP 端口
- 新增 PeiZhiAPI.bat 多供应商 API 配置（8 家）

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