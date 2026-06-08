# bot-template 更新日志

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
