# bot-template 更新日志




## v0.10.4 (2026-07-16)
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




