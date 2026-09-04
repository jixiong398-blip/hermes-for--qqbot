# Hermes 上游同步升级计划书（v0.13.0 → v0.20.6）

> 维护者文档 · 仅本地/LAN（不进 GitHub 分发）· 含服务器同步信息
> 生成日期：2026-08-29 · 版本：**v1.1**（已按 F1–F10 自审修正）

---

## 1. 背景与目标

### 1.1 现状
- 本项目为 `NousResearch/hermes-agent`（MIT）的魔改分发模板（QQ 群 AI 机器人）。
- 我们 fork 自上游 **v0.13.0（2026-05-07, commit `498bfc7bc`）**。
- 当前上游最新 **v0.20.6（2026-08-27）**。
- 落后 **7 个大版本 / 约 3.5 个月**的上游开发。

### 1.2 目标
1. 将引擎核心从 0.13 基线升级到 **v0.20.6 级别**，吸收上游全部有价值的演进。
2. **完整保留本项目定制资产**（QQ onebot 插件、自研多层记忆、Live2D、QQ 运维脚本、Dashboard 等）。
3. **Linux（生产服务器）+ Windows（开发/QQ 客户端）双平台持续可用**。
4. 保持双 git 同步体系（`hermes/` 局域网服务器权威 + 根 GitHub 分发）正常运作。
5. 升级过程**不中断线上服务**（分阶段灰度切换）。

### 1.3 非目标
- 不追求逐文件 100% 与上游一致。
- 不移植与 QQ 机器人无关的上游功能（pet 桌宠、billing、relay 中继、scale-to-zero 等）。
- 不引入上游破坏性架构（如外部记忆 provider 强制替换）。

---

## 2. 基线盘点（精确三方对比）

### 2.1 三方基线
| 基线 | 文件数 | 说明 |
|---|---|---|
| 上游 v0.13.0（fork 锚点） | 3143 | commit `498bfc7bc`（2026-05-07） |
| 我们 current core | 2742 | fork + 843 定制文件 |
| 上游 v0.20.6（目标） | 10506 | 22780 commits |

### 2.2 变更规模
| 变更类型 | 数量 | 含义 |
|---|---|---|
| 上游 0.13→0.20 新增 | **7761** | 吸收对象（tests 2484 / apps 2213 / contributors 821 / website 486 / optional-skills 345 / hermes_cli 228 / plugins 197 / agent 148 / skills 117 / web 106 / tools 76 等） |
| 上游 0.13→0.20 删除 | **398** | 上游移除（skills/ 183、environments/ 43、website/ 40 等） |
| 上游 0.13→0.20 内容变化 | **10785** | 引擎核心大面积演进 |
| 我们定制（新增） | **251** | 纯新增，直接重放 |
| 我们魔改（修改） | **592** | 需三方合并 |
| **三方冲突核心** | **535** | 我们改过 + 上游也改过 → 手工合并难点 |

### 2.3 关键结构差异
| 维度 | 上游 v0.13.0 | 我们 current | 上游 v0.20.6 |
|---|---|---|---|
| QQ onebot 插件 | ❌ | ✅（自研） | ❌ |
| 自研记忆系统 | ❌ | ✅ `agent/memory/*` | ❌（MemoryManager+Provider） |
| Live2D 桥 | ❌ | ✅ | ❌ |
| hermes_state | 单文件 | 单文件 | 拆 4 mixin |
| 平台架构 | gateway/platforms 平铺 | 混合 | plugins/platforms 全插件化 |
| environments/ | ✅ 顶层 | ✅ 顶层（40 定制） | **已重构到 tools/environments/** |
| 记忆架构 | — | UnifiedMemoryGateway | MemoryManager + Provider |

---

## 3. 上游依赖变化（F1 实证）

### 3.1 依赖差异总览
- 上游 v0.13.0：19 项 → 上游 v0.20.6：**33 项**（我们：22 项）

### 3.2 上游新增 18 项（升级需评估离线可装性）
| 依赖 | 用途 | 平台 | 离线可装 |
|---|---|---|---|
| fastapi / uvicorn[standard] | Web 服务 | 双 | ✅ wheel |
| python-multipart | 表单上传 | 双 | ✅ |
| websockets==15.0.1 | WebSocket | 双 | ✅ |
| Pillow==12.3.0 | 图像 | 双 | ✅ |
| cryptography==50.0.0 | 安全（CVE 修复） | 双 | ✅ |
| certifi / packaging / Markdown / snowballstemmer / pathspec / urllib3 | 间接依赖 | 双 | ✅ |
| **pywin32** / **pywinpty** / **concurrent-log-handler** | Windows 专属 | Win | ✅ |
| ptyprocess | 非 Win | Linux | ✅ |
| nemo-relay | 远程中继（多平台） | 双 | 需评估 |
| ruamel.yaml==0.18.17 / tzdata / psutil==7.2.2 | 版本锁定 | 双 | ✅ |

> **关键**：上游从 `>=x,<y`（宽松）改为 `==x`（**精确锁定**）——供应链安全策略。

### 3.3 上游移除 6 项 → **我们全部在用，必须保留**
| 依赖 | 我们引用量 | 结论 |
|---|---|---|
| anthropic | 224 文件 | **保留** |
| firecrawl-py | 55 文件 | **保留** |
| edge-tts | 10 文件 | **保留** |
| parallel-web | 7 文件 | **保留** |
| fal-client | 7 文件 | **保留** |
| exa-py | 5 文件 | **保留** |

> **F1 修正**：升级后我们的 pyproject 依赖 = 上游 33 项 + 我们保留的 6 项 = 39 项（去重）。**不能直接采用上游 pyproject**。

---

## 4. 上游演进分类（升级吸收清单）

### 4.1 高价值·必吸收
| 上游能力 | 文件 | 优先级 |
|---|---|---|
| 优雅关闭 | gateway/shutdown_watchdog.py, shutdown_flush.py | P0 |
| 健壮性 guard | agent/repetition_guard.py, empty_response_guard.py | P0 |
| 错误分类 | agent/error_surface.py, errors.py | P0 |
| turn 资源锁 | gateway/turn_lease.py, turn_context.py | P1 |
| 会话停滞检测 | gateway/session_stall.py, session_state.py | P1 |
| 投递台账 | gateway/delivery_ledger.py | P1 |
| 斜杠命令 | gateway/slash_commands.py, slash_access.py | P1 |
| 流式诊断 | run_agent.py stream_diag | P1 |

### 4.2 中价值·可选
- agent/monitoring/*、agent/deadline、agent/estop、agent/battery
- cron/ 强化、agent/lsp/*（仅当 QQ 群需要代码能力）
- 平台插件化迁移（gateway/platforms → plugins/platforms）

### 4.3 低价值·跳过
- agent/pet/*、agent/billing/*、credits_tracker
- gateway/relay/*、scale_to_zero、cgroup_cleanup（Linux-only，服务器用则保留）
- apps/、contributors/、evals/、ui-tui/、web/（纯前端演示则跳过）
- skills/ 重构（需评估与 SOUL/CORTEX/CEREBELLUM 关系）

### 4.4 上游删除但我们要处理的（F5 实证）
- `environments/`（顶层）→ **不是"上游删了"，是重构为 `tools/environments/`**。我们顶层 environments/（40 定制）应**迁移合并**到 tools/environments/ 而非原样保留。

---

## 5. 约束与不可动摇项

1. **双平台**：Linux（服务器生产）+ Windows（开发/QQ 客户端）都必须可安装/运行/测试。
2. **定制资产完整保留**：onebot 插件、记忆系统、Live2D、QQ 脚本、smart_model_routing、corpus_history、rl_cli、migrate_* 等 843 文件。
3. **隐私红线**：不写真实 QQ 号/群号/服务器路径/API Key（用 {{占位符}}）。
4. **生产连续性**：升级过程线上服务不可中断。
5. **双 git 体系**：`hermes/` LAN git（服务器权威）与根 GitHub 分发同步不破坏。

---

## 6. 升级策略（A+B 混合 + 定制重放）

### 6.1 总体策略
```
上游 v0.20.6（主体基线）
   ├─ 吸收 7761 新增 + 10785 变化
   ├─ 处理 398 删除（评估后清理/迁移）
   ├─ 依赖：上游 33 项 + 我们保留 6 项（F1）
   ├─ 重放我们的 843 定制文件
   │     ├─ 纯新增 251+308：直接拷贝（按依赖顺序 F6）
   │     └─ 冲突 535：机械化分类 + 逐文件三方合并（F2）
   └─ 产出：hermes/core v0.20.6-custom
```

### 6.2 定制重放顺序（F6 依赖闭包实证）
```
1. hermes_constants / hermes_logging / hermes_bootstrap（地基）
2. agent/memory/*（依赖 store + hermes_constants，自闭环）
3. 引擎核心（model_tools / toolsets / run_agent 的自定义接入）
4. tools/*（依赖 engine core）
5. environments/*（依赖 model_tools/tools，迁移合并到 tools/environments/）
6. plugins/platforms/onebot/*（依赖 gateway，自闭环）
7. QQ 运维脚本 scripts/*（依赖 agent/gateway，最后）
```

### 6.3 核心原则
- **以上游为主**：不逐行 diff（10785 太多），采用"基线替换 + 定制重放"。
- **三方合并**：对每个冲突文件做 `our(vs 0.13) + up(0.13→0.20)` 三方合并（`git diff --no-index` 辅助）。
- **记忆桥接**：自研记忆以 MemoryProvider 适配器接入，保留数据与行为（F3）。

---

## 7. 冲突文件处理（F2 机械化分类）

### 7.1 535 冲突文件分类方法
对每个三方冲突文件，生成两路 diff 报告：
```
冲突文件 X：
  our-diff = diff(up-v0.13/X, our-core/X)   ← 我们的改动
  up-diff  = diff(up-v0.13/X, up-HEAD/X)    ← 上游的改动
```
分类标签：
| 标签 | 判定 | 处理 |
|---|---|---|
| `OUR-ADD` | our-diff 是纯新增（上游没动那部分） | 直接合入上游版 |
| `UP-ONLY` | up-diff 是纯新增（我们没动那部分） | 以上游为主 |
| `CONFLICT` | 两路 diff 改同一区域 | 手工三方合并 |
| `UP-RENAME` | 上游改了文件名/结构 | 跟随上游，重映射 |

### 7.2 批量工具
- 脚本：`git diff --no-index --stat` + 逐文件 `--unified=3` 输出到报告目录
- 分类后按目录分组批处理（root 20 / plugins 20 / agent 19 / tools 33 / hermes_cli 38 / gateway 10 / 其余 395）

---

## 8. 记忆桥接方案（F3）

### 8.1 前置可行性 demo（P3 第一步，不可跳过）
写最小可运行验证：
```python
# agent/memory/hermes_bridge.py
class HermesMemoryProvider(MemoryProvider):
    def __init__(self):
        self.gateway = UnifiedMemoryGateway()   # 复用现有
    def prefetch(self, query):  → self.gateway.get_context_for_agent(query, ...)
    def sync_turn(self, u, a):  → self.gateway.process_turn(...)
    def get_tool_schemas(self): → memory_gateway 工具 schema
    def handle_tool_call(self, name, args): → memory_gateway(action=...)
    def shutdown(self): → self.gateway.shutdown()
```
> 先验证：①prefetch 注入上下文；②sync_turn 写 STM；③记忆工具可调用；④历史 SQLite 数据可读。

### 8.2 降级路径（桥接失败时）
**保留自研接入点**：不依赖上游 MemoryManager，维持我们在 prompt_builder/episodic_index 的直接调用（当前工作方式）。桥接仅作为"可选项增强"，失败则退回现状。**绝不强制迁移记忆数据**。

### 8.3 接口差异与映射
| 上游 MemoryProvider | 我们 UnifiedMemoryGateway | 映射 |
|---|---|---|
| initialize() | 构造时初始化 | 直接 |
| prefetch(query) | get_context_for_agent(msg) | 需适配返回结构 |
| sync_turn(user, asst) | process_turn(session, turn, ...) | 需补 session/turn 参数 |
| get_tool_schemas() | memory_gateway 工具 | 需包一层 function schema |
| handle_tool_call() | memory_gateway(action=...) | 需路由 |
| on_pre_compress() | 无直接对应 | 可空实现（API v1 兼容） |

---

## 9. Windows 测试裁剪规则（F7 实证）

### 9.1 需跳过的 POSIX-only 测试模式
| 模式 | 影响测试数 | 处理 |
|---|---|---|
| `pty.` | 89 | 跳过（终端模拟） |
| `signal.SIGTERM` | 20 | 跳过 |
| `os.kill` / `os.killpg` | 18 / 10 | 跳过 |
| `fcntl.` | 11 | 跳过 |
| `os.geteuid/getuid` | 8 / 4 | 跳过 |
| `cgroup` | 11 | 跳过 |
| `nix/` | 6 | 跳过 |
| `pwd.` / `grp.` | 2 / 1 | 跳过 |

> **结论**：约 **150 个上游测试**在 Windows 需跳过（用 pytest `skipif` marker 包装，或在 CI 上排除）。这些是 Linux 专属能力（pty 终端/cgroup/信号）的测试，跳过不影响核心验证。

### 9.2 Windows 保留的测试
- 引擎核心（agent loop / tools / memory）→ 全跑
- gateway 平台 → 全跑（含 onebot）
- 上游仅 3 个测试显式判断 win32，15 个标 integration → 小范围

---

## 10. 分阶段执行计划（含 F8 工作量）

### P0 · 准备（1 周）
- [ ] 完整备份（LAN git + 根 git + 运行数据 + 服务器快照）
- [ ] **依赖差异清单落地**（F1 已产出，转成可执行 requirements）
- [ ] 基线测试跑通 + 记录 baseline 失败清单
- [ ] 分支策略：hermes/ 建 `upgrade/0.20.6` 分支
- [ ] 搭建三方工作区（v0.20.6 + v0.13 worktree + our core）
- [ ] 冲突文件分类脚本生成 535 文件报告（F2）
- **验收**：备份完整；依赖清单确认离线可装；冲突报告生成

### P1 · 引擎对齐（2-3 周）
- [ ] hermes_state 拆分（对齐上游 4-mixin，保留 database-lock 修复）
- [ ] 核心 py 对齐（run_agent/model_tools/toolsets/hermes_logging/hermes_bootstrap）
- [ ] gateway/ 对齐（run/config/session/stream_consumer + 我们的 live2d_ws/builtin_hooks）
- [ ] hermes_cli/ 对齐、tools/ 对齐（registry + qq_napcat_tools/browser_providers）
- [ ] plugins/ 对齐（上游 plugin 系统 + 我们的插件先保留不接）
- 每步 py_compile + 导入冒烟 + 相关测试
- **验收**：引擎核心在 0.20.6 结构上 import 运行

### P2 · 定制重放（2-3 周，风险最高）
- [ ] 纯新增重放（按 F6 依赖顺序：hermes_constants → memory → tools → environments → onebot → scripts）
- [ ] environments 迁移合并到 tools/environments/（F5）
- [ ] 冲突合并（535 文件，按 F2 分类批处理）
- 每批测试 + 双平台冒烟
- **验收**：843 定制文件在 0.20.6 上工作

### P3 · 记忆桥接（1-1.5 周）
- [ ] 可行性 demo（F3 8.1）
- [ ] HermesMemoryProvider 实现 + 注册 plugins/memory/bot_template_local/
- [ ] run_agent 接入 MemoryManager
- [ ] 历史数据可读验证 + 降级路径就绪（F3 8.2）
- **验收**：记忆全功能在 0.20.6 工作，数据保留，失败可回退

### P4 · 双平台回归（1-2 周）
- [ ] Linux 服务器：干净安装 + 全量测试（**跳过 150 POSIX 相关只影响 Win 侧**）
- [ ] Windows：.bat 安装 + venv + 全量测试（**应用 F7 跳过规则**）
- [ ] 真机 QQ 回归（NapCat + 群聊/图片/记忆/Live2D/Dashboard）
- [ ] 性能对比（响应延迟/内存/database locked 频率）
- **验收**：双平台全绿；线上可灰度

### P5 · 发布（3-5 天）
- [ ] 版本号统一、CHANGELOG.md（中性）、UPGRADE.md
- [ ] 隐私扫描（MAINTENANCE.md 清单，**含绝对路径脱敏 F10**）
- [ ] 双 git 同步 + GitHub Release + 发布说明发用户

**总工作量：约 7-11 周**（单人全职）/ 团队可压缩至 5-7 周（P1/P2 并行度高）。

---

## 11. 测试与验收策略

### 11.1 双平台测试矩阵
| 平台 | 环境 | 测试内容 | 跳过规则 |
|---|---|---|---|
| Linux | 服务器 | 全量 pytest + 冒烟 + 真机 | 无 |
| Windows | 开发机 | .bat 安装 + pytest + Live2D + Dashboard | F7 的 ~150 POSIX 测试 |

### 11.2 分层验证
- 每步：py_compile + 导入冒烟（遍历 core 全部 py）
- 每批：相关 pytest（`-o addopts=""` 绕过 xdist）
- 每阶段：双平台冒烟 + 关键路径（agent loop / memory / onebot）回归

### 11.3 性能指标
- 首响应延迟、内存占用、`database is locked` 频率（0.14.x 已根治项不得回归）

---

## 12. 风险登记与缓解

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| 1 | 535 冲突文件手工合并量大 | 高 | F2 机械化分类 + 分组批处理 + 每批测试 |
| 2 | 记忆桥接接口不兼容 | 高 | F3 可行性 demo 前置 + 降级路径 |
| 3 | 上游 33 项依赖离线装不上 | 中 | F1 清单已出；离线 wheel 预案；nemo-relay 需评估 |
| 4 | environments 迁移合并冲突 | 中 | F5 已明确去向，迁移到 tools/environments/ |
| 5 | 双平台 Linux-only 代码在 Win 报错 | 中 | 上游 try/except 防护 + F7 Win 冒烟覆盖 |
| 6 | 升级破坏线上（服务器权威源同步） | 高 | 分阶段 + 分支 + 灰度 + 回滚 |
| 7 | 上游 0.20.6 重构后 onebot 插件适配点失效 | 中 | P2 重点验证 adapter/trigger/executor 调用链 |
| 8 | 测试套件上游膨胀拖慢 CI | 低 | 只跑定制相关 + 冒烟 |
| 9 | Live2D WS 协议与上游 stream 冲突 | 中 | P2 验证 live2d_ws 与 0.20.6 stream 兼容 |
| 10 | 上游移除的 6 依赖我们需保留 → pyproject 定制 | 中 | F1 已确认保留，不跟上游删 |

---

## 13. 回滚方案
- 每阶段完成即 commit + 轻量 tag（`upgrade-p1-done` 等）
- 任一步验收失败 → 回退到上一阶段 tag
- 线上灰度期间保留旧版一键切换（服务器备份 + 系统服务旧版二进制）

---

## 14. 决策点（含推荐答案 F9）

| # | 决策 | 推荐 | 理由 |
|---|---|---|---|
| D1 | 上游删 environments/（重构到 tools/environments/） | **跟随迁移** | F5 实证：逻辑挪到 tools/environments/，我们的 40 定制合并过去 |
| D2 | skills/ 重构对 SOUL/CORTEX/CEREBELLUM 影响 | **P0 评估** | 我们的身份系统挂 prompt_builder，需确认上游 0.20.6 是否保留该钩子 |
| D3 | 是否移植 LSP / monitoring | **暂不** | 与 QQ bot 弱相关，P4 后视需求评估 |
| D4 | 平台插件化迁移 | **暂不** | 我们只用 onebot，平铺无影响；后期可选 |
| D5 | apps/contributors/evals/ui-tui/web 前端 | **不纳入分发** | 体积 vs 完整性，QQ 分发不需要 |
| D6 | 记忆桥接失败兜底 | **保留自研接入点** | F3 降级路径：失败退回现状，绝不强制迁移 |
| D7 | 版本命名 | **v0.20.6-custom** | 跟随上游版本便于对比，加 custom 后缀区分 |

---

## 15. 附：分析数据文件
- 上游克隆：`{{hermes-upstream-dir}}`（v0.20.6, 22780 commits）→ **路径已脱敏，见下方说明**
- v0.13.0 worktree：临时目录
- 清单：up_changed / up_new / our_changed / triple_diff / only_up / only_ours / diff_common

---

## 附：隐私与脱敏说明（F10）
- **本文件所有本地绝对路径已在 v1.1 中改用 {{占位符}}**。
- 任何含盘符（如 `{{盘符}}:\...`、`C:\`、`E:\`）或 Temp 路径的条目，在同步 LAN git / 服务器 / GitHub 前**必须替换为 {{占位符}}**。
- 服务器路径（`ssh://{{server}}/...`）同理脱敏。
- 真实 QQ 号/群号/API Key 一律 {{占位符}}。

---

## 当前执行状态（2026-08-31）

本节是对上方原始计划的追加状态记录，不替换原始 P0–P5 估算和决策。

### 已完成并有回归证据

- 可靠性基础：错误 surface、重复/空响应防护、shutdown spool/恢复、session stall、turn lease、active command、session boundary security、hygiene、delivery ledger、restart source cache/home lifecycle；对应 `UPG-GATEWAY-011` 至 `UPG-GATEWAY-017`、`UPG-GATEWAY-019`、`UPG-GATEWAY-041`、`UPG-GATEWAY-042`。
- QQ/OneBot 收口：QQBot 迁移哨兵、OneBot 离线 envelope/capability/delivery 合同、私聊语音生命周期；对应 `UPG-OB-026`、`UPG-OB-028`、`UPG-OB-035`。
- MemoryProvider 桥接前置：接口兼容、MemoryManager caller compatibility、压缩 evidence/checkpoint 端口和隔离 `BuiltinMemoryProviderAdapter`；对应 `UPG-MEM-021`、`UPG-MEM-022`、`UPG-MEM-027`。
- SessionDB Gate 0–5：只读 schema/common probe、FTS/CJK canonical fallback、portability audit 和无副作用 facade 入口；对应 `UPG-DB-006` 至 `UPG-DB-010`、`UPG-DB-023` 至 `UPG-DB-025`。
- Environments 第一小片：capability snapshot、local-vs-remote 标记和 stdout 无 fd 回退；对应 `UPG-ENV-032`、`UPG-ENV-033`。
- Custom/Ollama profile 上下文转发；对应 `UPG-AGENT-031`。

### 当前验证基线

- 完整 `core/tests/run_agent/test_run_agent.py`：`323 passed`。
- Memory/Provider/Adapter/Context/Run-agent memory 集合：`213 passed`。
- SessionDB Gate 3/4/5 及既有集合：`239 passed`。
- Gateway lifecycle/reset 集合：`96 passed`。
- Delivery ledger/restart/ephemeral producer 集合：`38 passed`。
- Gateway platform/delivery/restart/ledger/ephemeral 集合：`153 passed, 2 skipped`。
- OneBot runtime/contract/migration：`22 passed`。
- Environment/Base/Terminal：`75 passed`。
- 变更集安全收据：`0 reportable findings`，contract `valid`，coverage `partial`；唯一 warning 是既有 `tools/skills_guard.py:627` 非法转义。
- E1 最新实证：Environment task-id/profile snapshot/Base/Docker/Singularity/timeout 集合 `62 passed, 1 skipped`；其中跳过项是原生 Windows 不具备 POSIX execute-bit 语义。Windows shell timeout 子集 `2 passed`，OneBot transport/ingress/runtime/contract/migration 最新 `37 passed`。
- SessionDB 当前完整聚焦集合（原有 schema/WAL/FTS/lineage + Gate 0–5 + portability import）：`270 passed, 1 warning`；warning 仍为既有 `core/tools/skills_guard.py:627` 非法转义。

### 尚未完成的真实融合

- NapCat loopback WS/HTTP、登录态、消息回执、媒体下载和真实群聊灰度；需要操作者开启配置中的端口后执行。
- SessionDB v26 mixin/import/migration、跨进程锁和历史生产副本恢复；当前只读 audit，不写入 v11 数据。
- Environments 深层 backend：Docker profile/reuse/egress、SSH bulk sync/错误分类、真实 Linux/Windows 远程/容器实测和跨进程 profile race；基础 bounded output/spill、profile snapshot 排除、task-id 路径隔离和连接错误第一片已完成。
- BuiltinMemoryProviderAdapter 的 owner 去重、plugin discovery 显式启用和跨进程 memory checkpoint v2 证明。
- 版本发布、双 git commit/tag、GitHub Release 和线上灰度；在上述真实合同通过前不得提前发布。

### 下一执行顺序

1. 用户开启 NapCat WS/HTTP 端口后，先做只读 health/status/handshake，再做明确目标的最小发送回执测试。
2. 同步一份脱敏历史 SQLite 副本，执行 portability/schema/search 双平台回放，仍不触碰生产库。
3. 按 backend 重放上游 environments 深层能力，逐项跑 Windows/Linux 测试。
4. Volta 复审新增 live contract 和深层环境切片，再决定是否进入 adapter/plugin discovery 主路径。
5. 全部门禁通过后，才更新 `VERSION`、`CHANGELOG.md`、`UPGRADE.md` 并走双 git 发布流程。

### 当前状态增补（2026-08-31）

- OneBot/NapCat transport 离线合同已完成（`UPG-OB-043`）：endpoint、默认 WS:3001/HTTP:3000 推导、loopback/鉴权状态、握手/health、HTTP/OneBot receipt、媒体 URL/20 MiB 响应边界均已隔离为纯合同；adapter 的最终 text/image/voice 发送与下载路径已接入安全分类。
- 新增离线 transport 合同测试 `15 passed`；现有 OneBot ingress/runtime/migration 与相关平台回归保持通过。没有打开 NapCat 端口，没有执行真实 QQ 登录、媒体下载或 ACK 测试。
- 安全审查已在 transport 切片前的工作树完成一次 `0 reportable findings` 收据；transport 代码写入后必须按同一威胁模型重新生成变更集审查，未完成前不进入发布阶段。
- Transport 后续门禁：用户明确开启 loopback WS/HTTP 后，先做只读握手/status，再做最小目标的发送回执；媒体 CDN 的 DNS/SSRF allowlist 需结合 NapCat 实际地址单独设计，不能用全局 private-URL 禁止规则直接替代。
- Environments 三方矩阵已建立（`DOC-ENV-045`）：上游 profile-scoped passthrough、snapshot 排除、task-id 路径隔离、Docker reuse/egress 和 SSH 深层错误分类被拆成 E1–E4 门禁；本地 Windows Git Bash、凭据清洗、HERMES_HOME/PYTHONPATH 和 terminal degraded 契约继续作为不可变保留项。
- E1 task-id 路径隔离已落地（`UPG-ENV-046`）：Docker 持久 workspace 与 Singularity overlay 不再直接拼接原始 session key，危险字符替换带稳定 hash 后缀；10 项 sanitizer 测试和 Base/Terminal 回归通过。profile-scoped snapshot、Docker reuse/egress 与 SSH 深层能力仍保持 deferred。
- Windows shell 选择已修正（`UPG-ENV-047`）：本地 backend 优先 Git Bash 并排除 `System32\\bash.exe` WSL shim，终端 `sleep`/timeout 语义恢复；Docker override 的 POSIX 执行位测试差异单独保留为跨平台测试适配项。
- E1 的完整边界复审已由生产实现代理完成（`UPG-ENV-048`）：Windows 保留设备名/孤立 surrogate、profile-scoped snapshot 排除、session/attribution 清理和 Docker forward-env 排除均已覆盖；`test_environment_profile_snapshot.py` 与 task-id 构造回归通过。
- E1 审计与测试适配已归档（`UPG-ENV-049`、`UPG-ENV-050`）：Local/Docker snapshot 排除、Windows NTFS execute-bit 条件和环境专项 `62 passed, 1 skipped` 已明确记录。
- Gateway terminal 配置桥接已补齐（`UPG-CONFIG-051`）：四个 Docker/Vercel 键与 CLI/terminal 消费端一致，配置漂移回归 `39 passed, 1 skipped`；其余环境后端门禁不变。
- SessionDB portability 显式导入门禁已建立（`UPG-DB-052`）：14 项副本测试确认 disabled/dry-run、字段投影、幂等、父链防环和全批回滚；v26 schema/mixin、跨进程恢复和生产数据库导入仍保持 deferred。
- Portability 导入 session-id 资源边界已补齐（`UPG-DB-053`）：id 上限 240 字符，副本回归 `15 passed`；v26 schema/mixin 与生产历史导入仍保持 deferred。
- SessionDB importer 与 Environments E1 的最终安全收据已完成（`SEC-REVIEW-054`）：0 个可报告发现；真实 v26/远端/NapCat/跨进程门禁仍未完成。
- Docker E3 第一片已完成（`UPG-ENV-055`）：profile identity、bounded labels、stale exited orphan-reaper 纯合同与 fake CLI 测试通过；runtime reuse/egress/network/daemon 仍保持 deferred。
- Docker runtime identity labels 已接入（`UPG-ENV-057`）：新容器带 Hermes/task/profile bounded labels，E3 runtime 回归 `52 passed, 1 skipped`；reuse/egress/network/daemon 仍保持 deferred。
- Docker runtime labels 与 SessionDB importer 的最终安全复核已完成（`SEC-REVIEW-058`）：0 个可报告发现；真实 Docker/SSH/NapCat/v26/跨进程门禁仍未完成。
- Docker E3 与 SessionDB portability 当前安全收据已完成（`SEC-REVIEW-056`）：0 个可报告发现，报告覆盖当前新增 helper/importer；runtime reuse/egress、v26/远端/NapCat/跨进程门禁仍未完成。

### Environments 深层切片增补（2026-08-31）

- `UPG-ENV-059` 已完成 Docker E3 的离线 runtime 合同：egress fingerprint 不回显 proxy/token，`network=false` 与 extra args 冲突可被识别，复用候选按 Hermes/profile/task/egress label 过滤并对未知 network mode fail-closed；默认仍是 legacy create/direct 行为。
- `UPG-ENV-060` 已完成 SSH E4 前置合同：远端同步路径限定在 `.hermes` 根，mkdir/scp/bulk upload/download/delete 的远端失败归类为 `EnvironmentConnectionError`，错误提示有界；Windows 控制端使用 POSIX 远端路径运算。没有连接真实主机。
- 本轮离线回归：Docker runtime/identity/环境集合 `72 passed, 1 warning`；SSH bulk/upload/sync-back/file-sync `59 passed, 1 skipped, 1 warning`；`py_compile` 和 `git diff --check` 通过。剩余 warning 为既有 `core/tools/skills_guard.py:627` 非法转义。
- 仍保持 deferred：Docker 构造级跨进程 reuse/start/health/recovery、真实 egress proxy/CA/network 接线、orphan 生命周期、SSH ControlMaster Windows 分支、Linux/Windows 脱敏远端实测、SessionDB v26、NapCat 实连和发布流程。
- `SEC-REVIEW-061`：当前工作树安全差异扫描为 `0 reportable findings`，覆盖 `partial` 仅表示 OneBot 媒体 DNS/SSRF、流式限额和真实外部 backend 仍需后续证据。
- `UPG-ENV-062`：Docker 复用合同已接入构造链，四个 runtime 配置键已在 CLI/Gateway/terminal 三方贯通；默认保持跨进程复用关闭，下一门禁是真实 daemon health/start/recovery 和 egress/network enforcement。

### SessionDB v26 前置进度（2026-08-31）

- `UPG-DB-064` 已完成 v26 结构 capability contract：`core/hermes_state_v26_compat.py` 固化上游核心表/列、计算 v11 缺口，并通过 `SessionDB.probe_v26_compatibility()` 提供只读探针。
- v26 探针不会创建缺失数据库或改写现有 v11 文件；它只报告 schema/version/missing objects，不能替代真实 migration。v26 的 FTS layout、触发器、lineage backfill、PK heal、租约和历史数据投影继续保持 deferred。
- 聚焦回归 `21 passed, 1 warning`；下一步是脱敏历史副本的双平台 v11/v26 probe + search/export 回放，再决定是否接入 mixin。
- `docs/SESSIONDB_V26_MIGRATION_MAP.md` 已补充逐表/逐字段映射、FTS/lineage/租约隔离和固定 replay 顺序；它不改变 `SCHEMA_VERSION`，也不授权生产库迁移。
- `UPG-CONFIG-066` 修正终端配置解析边界：Docker 专属 JSON 仅在 Docker 后端解析，local/SSH 不再被无关坏配置阻断；Docker 仍严格校验。
- `SEC-REVIEW-067` 已完成稳定快照安全复核，结果 `0 reportable findings`、coverage `complete`；3 个 OneBot 媒体外部门禁仍 deferred。
- `SEC-REVIEW-068` 已对包含 Docker lifecycle、SessionDB v26 contract 和 backend-scoped parsing 的最终稳定工作树完成复核：`0 reportable findings`、coverage `complete`、无工作树漂移警告。真实外部服务和 OneBot 媒体 DNS/流式门禁仍 deferred。

### 下一阶段：脱敏历史数据库回放

- 从已授权的历史副本开始，不读取或修改生产 `state.db`；先记录文件 hash、SQLite integrity/schema/version 和 WAL/SHM 是否存在。
- 在 Windows 控制端和 Linux 环境分别运行 `probe_v26_schema()`、`probe_v26_migration_plan()`、export/audit、search/FTS fallback 和现有 v11 importer dry-run；比较会话/消息/lineage/标题/多模态内容的结果 shape。
- 只有副本回放、备份、回滚和并发 writer 证据齐全后，才让 Volta 设计 v26 mixin 的增量写入门禁；禁止先改 `SCHEMA_VERSION` 或把上游 facade 整体覆盖。
- `UPG-DB-069` 回放工具已落地并通过 `18 passed, 1 warning`；可在获得脱敏副本后直接执行 `python scripts/sessiondb_replay.py --source <copy> --output <report>`，报告不含会话正文。当前仍没有历史生产副本，因此 v26 写入迁移和双平台历史证据继续 pending。
- `UPG-DB-071` 完成回放契约复审：canonical/旧 API 与 CLI 输出别名一致，v26 额外字段和 search/export 采用 SQL 前缀上限，SQLite sidecar 符号链接/非 regular 输入 fail-closed，报告写入使用唯一临时文件；回放/schema/portability focused 集合 `58 passed, 1 warning`。v26 mixin 写入、真实历史副本和双平台 WAL/锁证据仍 pending。
- `UPG-DB-072` 完成 v26 第一阶段 copy-only additive gate：显式 target/enable/backup/hash/schema/version 前置，`system_prompts`/`session_model_usage` 事务化、幂等、失败全批回滚；POSIX/Windows 有界 lock 与 SQLite busy 合同通过，`SessionDB` facade 和 `SCHEMA_VERSION=11` 默认路径不变。gate/replay/schema/v26/common/portability/search 组合 `69 passed, 1 warning`，真实历史副本、subprocess 双平台证据和全量 v26 mixin 仍 pending。
- `SEC-REVIEW-073` 已复核当前 v26 copy gate、replay/schema probe、OneBot media streaming、Docker/SSH/environment、Gateway lifecycle 和 MemoryProvider 变更：`0 reportable findings`，coverage `complete`；2 个 OneBot URL `CWE-918` 候选仍 deferred，旧 buffered-media `CWE-400` 候选已由 streaming helper 关闭。真实 NapCat/网络、Linux/Windows 独立进程、历史副本和全量 v26 mixin 仍是后续门禁。
- `UPG-DB-074` 扩展 copy-only gate 的显式 `sessions/messages` additive-column 批次：routing/activity/profile/metadata 等列按 qualified allowlist 进行安全 `ALTER TABLE ADD COLUMN`，支持 dry-run、部分已存在列、幂等、定义不兼容拒绝、第二列失败全批回滚和显式空列拒绝；`columns`-only 不隐式创建表，v11 默认路径及 `SCHEMA_VERSION=11` 不变。gate/replay/schema/v26/common/portability/search 组合 `74 passed, 1 warning`；真实双进程、历史副本、全量 v26 mixin/backfill/FTS/lineage 仍 pending。
- `SEC-REVIEW-075` 已重新覆盖 `UPG-DB-074` additive-column 批次：`0 reportable findings`，coverage `complete`；`ALTER TABLE` allowlist、columns-only 语义、schema/default 校验、事务回滚与跨平台 lock 均通过当前 `74 passed` 聚焦回归。两个 OneBot URL `CWE-918` 仍 deferred，真实 NapCat/网络和双平台独立进程证据仍未完成。
- `UPG-ACT-076` 完成上游 session activity observation contract：新增有界 description/provenance/snapshot/reset helper，SessionDB 仅在 v26 activity 列存在时提供 monotonic touch/clear/get，v11 缺列严格 no-op；AIAgent durable 写入口仅由显式 callback 或 `persist_session_activity=True` 开启，并按 60 秒窗口限频。activity/v26 gate/replay/schema/portability/search 组合 `83 passed, 1 warning`；真实双进程、WAL contention、历史副本、activity backfill 和完整 v26 mixin 仍 pending。
- `SEC-REVIEW-077` 已覆盖 `UPG-ACT-076`：`0 reportable findings`、coverage `complete`；activity/v26/interrupt/steer 聚焦集合 `106 passed`，主 `run_agent` 回归 `323 passed`。两个 OneBot URL `CWE-918` 仍 deferred，真实 NapCat/网络、双平台独立进程和完整 v26 mixin 仍是后续门禁。
- `UPG-DB-078` 完成 v26 剩余状态表 copy-only schema gate：新增 `gateway_routing`、`gateway_hygiene_state`、`compression_locks`、`session_turn_leases`、`async_delegations` 的固定上游 DDL/PK/default/FK allowlist；保留 `tables=None` 默认 `system_prompts`，显式 tables/columns 才写入，混合事务失败全批回滚，未知/重复/约束不兼容 fail-closed，`SCHEMA_VERSION=11` 不变。gate/replay/schema/activity/portability/search/interrupt/steer 组合 `114 passed, 1 warning`；真实双进程/WAL、历史副本、全量 v26 runtime/mixin/backfill/FTS/lineage 仍 pending。
- `SEC-REVIEW-079` 已覆盖 `UPG-DB-078`：`0 reportable findings`、coverage `complete`；七张 v26 状态表的 allowlist/PK/FK/约束、copy-only target/backup/hash/sidecar、跨平台 lock 和事务回滚通过 `114 passed` 聚焦回归。两个 OneBot URL `CWE-918` 仍 deferred，真实 NapCat/网络、双平台独立进程和全量 v26 runtime/mixin 仍未完成。
- `UPG-GW-080` 完成可选 durable gateway routing index：SessionDB 提供 bounded/parameterized save/replace/load/delete CRUD，仅在 `gateway_routing` 表存在且约束完整时工作；`durable_routing` 默认 false，启用后 DB 优先并始终保留 `sessions.json` fallback/mirror，scope 使用稳定 hash，不把绝对路径写入 routing scope 或错误日志。routing/session/config/v26/activity/replay/schema/portability/search/interrupt/steer 回归 `272 passed, 1 warning`；真实跨进程 routing/WAL、生产启用和 stale/prune 双写证据仍 pending。
- `UPG-OB-070` 已统一 OneBot voice/image/get_file HTTP 下载为 streaming + Content-Length/chunk hard cap，响应 buffering 风险已在离线测试中关闭；URL DNS/SSRF 和真实 NapCat 仍 pending。
- `SEC-REVIEW-081` 已覆盖 `UPG-GW-080`：`0 reportable findings`、coverage `complete`；routing/v26/activity/replay/OneBot/environment 聚焦审查通过，两个 OneBot URL `CWE-918` 仍 deferred，真实 NapCat/网络、双平台独立进程和 durable lease/delegation runtime 仍待后续门禁。
- `UPG-GW-082` 完成可选 durable session turn lease 端口：SessionDB 仅在完整 v26 lease 表存在时提供参数化 acquire/refresh/release/get，过期租约原子回收、live/wrong owner 受保护、v11 严格 no-op；`DurableSessionTurnLease`/`SessionTurnLeasePersistence` 使用显式 enable + `asyncio.to_thread`，现有进程内 registry 和 Gateway 默认路径不变。lease/routing/v26/activity/replay/schema/portability/interrupt/steer focused `290 passed, 1 warning`；真实 subprocess/WAL/owner fencing、主路径接入和全量 v26 runtime 仍 pending。
- `SEC-REVIEW-083` 已覆盖 `UPG-GW-082`：`0 reportable findings`、coverage `complete`；lease owner/TTL/expiry/rollback、async timeout 和 v11 no-op 通过审查，两个 OneBot URL `CWE-918` 仍 deferred，真实双平台独立进程/WAL/owner fencing 与 Gateway 主路径接入仍待后续门禁。

### `SEC-REVIEW-085`：canonical SessionDB ports 安全复核（2026-09-01）

- 当前工作树 68 个变更源码文件已完成完整 diff scan，结果为 `0 reportable findings`、coverage `complete`；新增四个 SessionDB canonical module ports 的 import、内存 SQLite probe、SQL/搜索/portability 边界均已覆盖。
- 保留 3 个明确 deferred 候选：OneBot voice URL 的 `CWE-918`、image/get_file URL 的 `CWE-918`，以及仅在未来 mixin 调用暴露时成立的 `reasons_sql` 动态 SQL 片段风险；不把它们伪装成已解决或当前可报告漏洞。
- 证据：扫描 ID `cc8b8787-a7f2-4603-af27-ca8da078c85d`；canonical + common/schema/search/portability focused `36 passed, 1 warning`；`py_compile` 与 `git diff --check` 通过；唯一 warning 仍是预存 `skills_guard.py:627` 非法转义。
- 该安全收据不改变 v11 schema、版本号、NapCat 端口、生产数据库或 git 状态；下一门禁仍是授权脱敏历史 SQLite 的 Windows/Linux replay，然后再设计真正的 mixin 接管。

### `UPG-DB-084`：SessionDB 四模块边界准备（2026-09-01）

- 已新增 `hermes_state_common.py`、`hermes_state_schema.py`、`hermes_state_search.py`、`hermes_state_portability.py` 四个 canonical compatibility port，对齐上游模块名称和低风险 helper 入口。
- 四模块导入无 facade side effect；schema probe/解析只使用内存 SQLite，search/portability 只通过显式 host hook 或既有 bounded compatibility helper；本地 `SessionDB` 仍是 v11、FTS、WAL、事务和返回 shape 的权威实现。
- canonical + common/schema/search/portability 聚焦回归为 `36 passed, 1 warning`，`py_compile` 与 `git diff --check` 通过；warning 是预存 `core/tools/skills_guard.py:627` 非法转义。
- 该切片只完成 Gate 5 的模块边界准备，不代表上游四个 mixin 已接管 facade，也不代表 v11→v26 migration 完成。下一步需在脱敏历史副本上验证 canonical helper 与本地数据语义，再按单一能力逐项接入。
- 由于 Volta 最终回合受到协作模型 404/403 服务限制，日志由根代理补录；不得把该服务故障或本地离线测试当作真实 Linux/Windows、NapCat 或生产数据库证据。

### `DOC-DB-086`：SessionDB canonical module symbol parity matrix（2026-09-01）

- `docs/SESSIONDB_THREE_WAY_MATRIX.md` 已新增四模块声明对照：上游 common/schema/search/portability 为 `23/22/41/21`，本地 canonical port 为 `17/14/9/8`；该统计用于审查覆盖范围，不代表上游方法已经全部接管。
- 矩阵明确已对齐的 helper/probe/sanitizer/audit/hook 入口，以及仍 deferred 的 v26 schema init/PK heal、FTS rebuild/merge、rich search projection、lineage adoption 和完整 importer。
- 当前 `SessionDB` facade、v11 schema、FTS/WAL、OneBot transcript 和 portability gate 保持不变；下一阶段固定为授权副本 replay 后逐个 helper/mixin 接入，任何失败都回退到现有 facade。
- 文档变更不改代码、数据库、版本号、NapCat 或 git 状态；完整测试证据沿用 `97 passed, 1 warning`，唯一 warning 为预存 `skills_guard.py:627` 非法转义。

### `DOC-AG-087`：Agent Runtime 三方融合矩阵（2026-09-01）

- 已新增 `docs/AGENT_RUNTIME_THREE_WAY_MATRIX.md`，把上游 v0.20.6 Agent Runtime 与本地 `run_agent.py` 的差异落成可审计的模块/职责/门禁表。
- 当前明确禁止整文件复制 `conversation_loop.py`、`agent_init.py`、`tool_executor.py` 或 `turn_finalizer.py`；先以本地 `TurnOutcome`、error surface、MemoryProvider、OneBot transcript 和工具结果语义为权威。
- 下一项可独立评估的是 `message_sanitization`/`provider_projection` 的纯端口，但必须先补本地 message metadata 依赖和 projected-message 去重契约；`turn_context`/`tool_executor`/`conversation_loop` 继续保持 deferred。
- 这次只增加规划文档，不改代码、版本号、数据库、NapCat 或 git 状态；Volta 后端 403 仍记录为外部执行限制。

### `UPG-AGENT-088`：消息清洗 canonical compatibility port（2026-09-01）

- 已新增 `core/agent/message_sanitization.py`，以惰性适配方式暴露本地 `run_agent.py` 已验证的 surrogate、非 ASCII、结构、工具参数和图片清洗函数，并补齐中断 tool tail 的最小 assistant 收尾语义。
- import smoke 证明该模块不会加载 `run_agent`；默认主循环、SessionDB、OneBot transcript 和 provider 行为不变。上游的 provider-specific call-id、reasoning_content policy、message metadata 仍保持 deferred。
- 新增 focused `tests/agent/test_message_sanitization_port.py`：`4 passed, 1 warning`。下一阶段若要扩展 provider policy，必须先建立本地 metadata/ID owner 和跨 provider regression，不得直接复制上游完整文件。
- 该切片由根代理在 Volta 账户服务不可用期间完成；不改变版本号、数据库、NapCat 或 git 状态，后续仍需由 Volta 复审生产接入。

### `UPG-OB-090`：OneBot WebSocket 依赖兼容与认证状态修复（2026-09-01）

- 本机 `websockets==12.0` 与上游 15.x 的 header 参数差异已通过 `_connect_onebot_websocket()` 兼容，未知 API 且配置 token 时 fail-closed。
- 真实回环测试确认 3000/3001 可达；NapCat 当前对带/不带 token 的 WS 初始帧均返回 `1403/failed`，HTTP `get_status` 返回 `403`。适配器已修复为 `connected=false`、`ws_auth_failed`、不可重试，不再误报平台已连接。
- OneBot transport focused `19 passed, 1 warning`，`py_compile` 与 `git diff --check` 待安全审查后再次复核；没有发送任何私聊/群消息或其它写 action。
- 下一门禁：用户核对 NapCat WebUI token 并更新 `.env`，然后再做最小发送回执；认证通过前不进行消息测试。

### `SEC-REVIEW-091`：OneBot header/认证状态修复安全复核（2026-09-01）

- 包含 `UPG-OB-090` 的 69 个变更源码文件已完成完整 diff scan，结果 `0 reportable findings`、coverage `complete`；header token 传递和初始 `1403` fail-closed 状态机均已覆盖。
- OneBot voice/image URL 的两个 `CWE-918`、future message finalizer、SessionDB 动态 SQL 片段继续 deferred；真实媒体 URL provenance、DNS/private-address/redirect 和生产网络仍未证明。
- 证据：扫描 ID `e4454615-474f-44bb-830d-0c6ef5eb033c`；OneBot transport focused `19 passed, 1 warning`，真实回环认证结果为当前 token 失败且 adapter 正确报告 `ws_auth_failed`；未发送消息、未访问非回环网络或生产 DB。

### `UPG-OB-092`：登录账号自动发现与 Dashboard 单实例选择器（2026-09-01）

- 已新增有界 `config_discovery.py`：`ONEBOT_SELF_ID` 精确优先，缺省时按最新 `napcat_protocol_<uin>.json` 选择；只读取启用回环 server 的一致 token，token 不出进程内存边界。
- Dashboard 新增账号摘要 GET、当前实例账号选择 POST 和 UI 控件；选择只写 `ONEBOT_SELF_ID`/自动发现开关，Gateway 运行中只返回重启提示。修正 Dashboard 对 `hermes/core` 和 `modules/napcat` 的路径假设。
- 配置向导/模板/用户部署流程已同步；公开 `UPGRADE.md` 只写中性步骤，内部 `docs/NAPCAT_MULTI_INSTANCE_PLAN.md` 记录未来多账号/多 NapCat/多 Hermes 的对象模型和门禁。
- 验证：配置发现 + OneBot transport `27 passed, 1 warning`，Dashboard API `3 passed`；真实 Dashboard GET `200` 返回 3 个账号摘要、无 token；自动发现后真实 `get_login_info` `retcode=0/status=ok`，私聊/测试群各 1 条联调消息均取得回执。未将真实账号/群号写入仓库。
- 兼容：当前仍一机一 Bot；不改变本地记忆、SessionDB v11、OneBot 隔离或 Gateway 默认生命周期；多实例只保留计划，需后续独立 Change ID。

### `PLAN-OB-094`：多账号/多实例长期业务升级边界（2026-09-01）

- 详细计划见 `docs/NAPCAT_MULTI_INSTANCE_PLAN.md`：P0 当前单实例账号选择，P1 只读视图，P2 多 NapCat 进程，P3 多 Hermes profile，P4 Dashboard 控制平面。
- 每个未来实例必须独立 config root、WS/HTTP 端口、PID/lock、Hermes profile、SessionDB、Memory namespace、delivery/routing state 和日志；当前本地 `UnifiedMemoryGateway`、v11 SessionDB、OneBot 群锁和 Gateway recovery 语义不可被替换。
- 在 Linux/Windows 进程/WAL/profile/memory/回滚门禁完成前，不启用多实例、不共享 `state.db`/memory、不更新发布版本。

### `SEC-REVIEW-089`：消息清洗 canonical port 安全复核（2026-09-01）

- 当前 69 个变更源码文件已完成完整 diff scan，结果为 `0 reportable findings`、coverage `complete`；新增惰性 resolver、消息清洗和中断 tool tail 端口均已覆盖。
- 保留 4 个 deferred 候选：中断收尾的 future caller 资源/编码边界、SessionDB `reasons_sql` 片段、OneBot voice URL `CWE-918`、OneBot image/get_file URL `CWE-918`。这些候选均没有在当前默认生产路径形成可报告漏洞，但未来接入必须重新验证。
- 证据：扫描 ID `faea1023-81ed-48ab-b318-98e667e4d38e`；`101 passed, 1 warning`；`py_compile`、`git diff --check` 通过；未访问 NapCat、外部服务或生产数据库。

### `FIX-OB-096`：执行层输出契约与 exiting 状态机修复规格（2026-09-01）

- 用户反馈规格已审阅并补充到 `docs/FIX_silence_contract_and_exit_loop.md`：方向确认，当前仍为待实施，不改变版本号或公开 `CHANGELOG.md`。
- 目标行为：判定层放行的 mention/judge/continuation 轮必须产出可见正文；纯 `[QUIET]`/`[SILENT]` 或空输出最多在同一会话反馈重试一次；正文混合标记时发送正文并在成功交付后处理状态；软收尾不应单独把 episode 推入 exiting。
- 实现硬门禁：按单次处理关联完成信号，不能复用按群覆盖的 future；等待 base session guard 释放后再重试；`MessageEvent` 没有现成 `with_system_note`，反馈不得重复写入普通 user transcript；清洗后的正文必须同时进入发送、outcome、recorder 和 bot buffer。
- 状态硬门禁：新增有界 `exiting_streak` 或等价 coordinator 计数，代码层在连续两轮前强制 `should_exit=false`；直接 @、SOUL 别名直呼、reply-to-bot 均须在 judge/continuation 前复位；recorder 结果需对软收尾做 fail-safe 降级。
- 回归门禁：第 7 节 10 条 + 第 10.4 节 5 条全部通过，尤其覆盖空 `final_response` 不调用 send、旧任务收尾并发、发送失败不提前 quiet、清洗正文传递和 exiting 计数。只通过 prompt 快照或纯 mock 不算完成。
- 责任：实现和测试交给持久化生产实现代理；代理服务不可用时根代理只维护规格/审计，不自行把未验证实现标记为完成。

### `SEC-REVIEW-093`：账号选择与静默规格相关工作树安全收据（2026-09-01）

- nested Hermes 工作树快照完成 72/72 变更源码文件差异审查，结果 `0 reportable findings`；4 个候选保留 deferred，覆盖 NapCat 账号发现、Dashboard selector、OneBot auth、SessionDB compatibility ports、media URL 和 optional manifest。
- 证据：扫描 ID `77417c30-e258-4ee6-8a9c-9505cdbbc140`；OneBot 配置/传输 `28 passed, 1 warning`，Dashboard + setup-config `5 passed`，语法检查通过。扫描期间工作树变化，收据只代表原始快照；根分发全量 inventory 仍受 Windows GBK 非 ASCII 文件名限制。
- Deferred：中断 `final_response` 尾部 future 接入边界、私有 `reasons_sql` 片段、OneBot image/get_file 与 voice URL 的真实 SSRF/redirect/DNS 证据；没有把未验证的部署假设标成漏洞。

### `UPG-OB-097`：NapCat 本地默认值与插件 manifest 对齐（2026-09-01）

- OneBot plugin manifest 的 `requires_env` 现为空；本机 NapCat 默认 WS/HTTP 地址和账号 token 自动发现均转为 `optional_env`，远程 OneBot 仍可显式填写覆盖值。
- 删除重复的 `ONEBOT_HTTP_URL` 元数据并增加 manifest 唯一性断言，避免配置向导出现重复字段；不改变 adapter 的远程 endpoint 兼容路径或 token fail-closed 规则。
- 验证：OneBot 配置发现/传输 `28 passed, 1 warning`，Dashboard + setup-config `5 passed`，manifest 断言包含在配置发现测试内；唯一 warning 为预存 `skills_guard.py:627` 非法转义。
- 该切片只优化首次部署交互，不更新版本号、公开 changelog、数据库或 NapCat 配置。

### `UPG-DEPLOY-102`：升级脚本补齐 Agent Runtime 双写（2026-09-01）

- `extras/scripts/upgrade.py` 的 `UPGRADE_MAP` 新增 `hermes/core/run_agent.py`，确保旧用户执行 `upgrade.py` 时同步 Gateway contract retry 所依赖的 AIAgent persistence suppress 逻辑。
- `UPGRADE.md` 文件安装位置表同步 `run_agent.py -> ~/.hermes/run_agent.py`；editable 安装路径和 `config.yaml`/`.env`/`SOUL.md` 保护规则不变。
- 验证：`extras/scripts/test_setup_config_napcat.py` `4 passed`（含临时目录双写验证），`upgrade.py`/`setup_config.py` `py_compile` 通过；不改数据库、NapCat 配置、版本号或公开 changelog。

### `UPG-DEPLOY-103`：首次安装根目录解析与 NapCat 默认开关（2026-09-01）

- 修复 `extras/scripts/install.py` 将 `extras/` 误当项目根的问题，改为从 `install.bat` + `hermes/core` 哨兵向上解析真实分发根；模板和 `modules/knowledge/.gitkeep` 不再错一层。
- 新建 `.env` 默认写入 `ONEBOT_AUTO_DISCOVER_TOKEN=true`，不改变用户已有 `.env` 的 API Key/角色/账号值，也不读取或写入 NapCat token。
- 验证：`extras/scripts/test_install.py` + `test_setup_config_napcat.py` `6 passed`，install/upgrade/setup 脚本语法检查通过；未访问生产数据库、外部服务或真实账号。

### `UPG-OB-098`：OneBot 执行层静默契约与 exiting 代码门禁（2026-09-01）

- 已实现 `docs/FIX_silence_contract_and_exit_loop.md` 的 OneBot 执行层合同：判定层已放行的 mention/judge/continuation/exit 轮要求可见正文；纯 `[QUIET]`/`[SILENT]` 或空结果最多同会话反馈重试一次，不新增硬编码兜底文案。
- `adapter.py` 以大小写/空白容错正则统一清洗控制标记，按 turn nonce 返回 bounded completion；混合正文继续发送清洗后的正文，状态动作绑定成功可见交付，发送失败或内部 no-op 不提前 quiet。`group_executor.py` 等待旧 base session task 释放，避免 busy/interrupt 死等和 future 串线。
- 重试使用现有 `MessageEvent.channel_prompt` ephemeral 通道，禁止调用不存在的 `with_system_note`；Gateway/AIAgent contract retry 使用空 API user turn、跳过重复 preprocessing，并 suppress JSONL/SessionDB/external memory 的重复原始用户写入。`gateway/platforms/base.py` 未修改。
- `EpisodeState.exiting_streak` 有界序列化并由 judge 代码门禁要求连续两轮；recorder 对“你们聊/你们继续”软收尾 fail-safe 降级，直接 @、SOUL 别名、reply-to-bot（含 @全体回复）在 judge/continuation 前复位。
- 回归：新增 `core/tests/gateway/test_onebot_silence_contract.py` 18 项真实 Base/async 合成测试；silence + OneBot runtime/contract/transport/error-surface `54 passed, 1 warning`，Gateway lifecycle/ledger/session `37 passed, 1 warning`，`py_compile`/`git diff --check` 通过。唯一 warning 为预存 `core/tools/skills_guard.py:627` 非法转义；无关预存 `test_fast_command` 的 `request_overrides=None` 失败未改。
- 保留门禁：真实 NapCat/QQ/provider 空响应与 marker streaming、Linux/Windows 独立进程 guard/WAL、真实 Gateway + MemoryProvider/SessionDB transcript 回放、proxy ephemeral retry、完整平台发送证据仍 deferred；不更新版本号或公开 `CHANGELOG.md`。

### `SEC-REVIEW-100`：OneBot 输出契约与 exiting 门禁安全复核（2026-09-01）

- 当前 nested Hermes 工作树 76/76 review rows 已完成安全差异审查，结果为 `0 reportable findings`，覆盖 marker normalization、nonce completion、Gateway contract retry、AIAgent persistence suppress、EpisodeState exiting_streak、NapCat discovery、SessionDB 和 Environment 边界。
- Deferred：有界 contract retry 的潜在资源放大、私有 `reasons_sql` 动态片段、OneBot image/get_file URL 与 voice URL 的真实 SSRF/redirect/DNS 证据；未把未验证路径升级为漏洞。
- 证据：Codex Security scan `5e5a87d1-9f82-4cff-8579-44629b2de1ea`；静默契约 `18 passed, 1 warning`，OneBot 组合 `45 passed, 1 warning`，Agent Runtime `351 passed, 1 warning`，`py_compile`/`git diff --check` 通过。唯一 warning 为预存 `core/tools/skills_guard.py:627` 非法转义。
- 限制：只覆盖 nested Hermes 快照；根分发 inventory 仍受 Windows GBK 非 ASCII 文件名限制，真实 NapCat/provider 空响应、marker streaming、双平台进程/WAL 和 proxy ephemeral retry 仍 pending；未访问生产数据库、非回环网络、Docker、SSH 或真实 provider。

### `UPG-AGENT-104`：provider_projection 纯兼容端口（2026-09-01）

- 基于上游 `agent/provider_projection.py` 的结果契约，新增 `core/agent/provider_projection.py`；只处理 agent-as-provider 返回的 assistant/tool 投影行和 provider tool iteration 计数，不复制上游 `conversation_loop.py`。
- 端口约束：最多 64 行、每行正文 200k 字符、每行序列化 payload 512kB、assistant tool calls 32 个、tool 字段 512 字符、控制字符拒绝、时间戳有限且不修改 provider 原始 mapping；普通 response 没有投影字段时严格 no-op。
- 当前只写调用方内存 list，不加载 `run_agent`、provider client、SessionDB 或网络，不接入 ACP/Codex 主循环、不改变本地 transcript owner；后续 wiring 必须先有去重 key、role pairing 和 persistence 回归。
- 测试：新增 `core/tests/agent/test_provider_projection_port.py`，`5 passed, 1 warning`（含嵌套 payload 字节上限）；warning 仍为预存 `core/tools/skills_guard.py:627` 非法转义。版本号、数据库、NapCat 和公开 changelog 不变。

### `UPG-DEPLOY-105`：provider_projection 双写清单（2026-09-01）

- `extras/scripts/upgrade.py` 新增 `hermes/core/agent/provider_projection.py` 映射，确保该纯兼容端口随旧用户升级同步到 `~/.hermes/agent/` 与模板目录。
- `UPGRADE.md` 文件安装位置表同步；临时目录双写回归仍通过，未接入主循环、不修改数据库或用户配置。

### `SEC-REVIEW-107`：输出契约与 provider projection 增量安全收据（2026-09-01）

- 以 nested Hermes 基线 `b9b0988` 对当前 working-tree 的 77 个 review rows 完成 Codex Security diff scan，覆盖 OneBot 输出契约、Gateway/AIAgent ephemeral retry、exiting 状态复位、NapCat discovery/auth/media、SessionDB compatibility 和 bounded provider projection。
- 结果：`0 reportable findings`；5 个候选均完成 validation/attack-path。coverage 诚实保持 `partial`：contract retry 并发/取消证明、provider projection 未来接线、legacy `reasons_sql` 参数化，以及 image/get_file 与 voice URL 的隔离负向 SSRF 矩阵仍 deferred。
- 证据：扫描 ID `bcd27a46-a55f-47f1-a111-e835379666d8`；扫描工件保留在本机临时安全目录，不写入仓库。扫描仅覆盖 nested Hermes，根分发 inventory 的 Windows GBK 文件名限制继续单独记录。
- 下一门禁：先让 Volta 完成本地 mock/loopback media negative tests，再在获得脱敏历史 SQLite 与 Linux 运行环境后推进 SessionDB replay；provider projection 未满足 provenance/dedup/role-pairing/persistence/真实 provider 证据前保持默认未接线。

### `UPG-OB-108`：OneBot 媒体 URL SSRF 负向门禁（2026-09-01）

- 已完成 OneBot image/get_file 与 voice/record 的离线媒体 URL 负向矩阵。`transport_contract.py` 现在拒绝非 loopback 私有/保留地址、CGNAT、decimal/hex/octal/percent-encoded/backslash authority、userinfo、嵌套 scheme 和远程 UNC/file scheme；合法本地 `file://`、loopback 与公共 CDN 形状继续兼容。
- adapter 下载前通过线程化 DNS 解析检查所有 IPv4/IPv6 答案；DNS 失败、空/无效解析及私有解析 fail-closed。显式配置的 OneBot HTTP host 仅按相同 scheme+port 保留远程部署兼容；三条 HTTP 下载路径显式关闭自动 redirect。
- 新增 `core/tests/gateway/test_onebot_media_ssrf.py`，覆盖 redirect、IPv4/IPv6/private/link-local/CGNAT、decimal/hex/octal/encoded host、userinfo/scheme/file confusion、DNS rebinding/解析失败、image/get_file sink 和 voice file fallback。媒体 SSRF/stream/transport 聚焦 `48 passed, 1 warning`；`py_compile`、`git diff --check` 通过。
- 唯一 warning 为预存 `core/tools/skills_guard.py:627` 非法转义；未访问非回环网络、NapCat、生产数据库或真实凭据。真实 NapCat URL provenance、redirect/DNS rebinding TOCTOU、Linux resolver/IPv6/subprocess、真实 CDN allowlist 和生产网络仍 deferred，不更新版本号或公开 `CHANGELOG.md`。

### `SEC-REVIEW-110`：媒体 URL 门禁增量安全复核（2026-09-01）

- 当前 nested Hermes working-tree diff 的 77/77 review rows 已完成 Codex Security diff scan，结果 `0 reportable findings`，coverage `partial`；扫描 ID `6a875f21-865b-47cb-b3d4-292ecfd356a9`。
- 已证明：私有/保留/CGNAT 与歧义 authority 解析拒绝、下载前逐地址 DNS 检查、DNS 失败/空/无效解析 fail-closed、三条媒体路径 `follow_redirects=False`、本地 redirect/fallback 回归；未访问外部网络或生产资源。
- Deferred：连接级 DNS pinning/TOCTOU、真实 NapCat URL provenance、Linux resolver/IPv6/subprocess、真实 CDN allowlist/redirect 链；上一阶段 contract retry/provider projection/legacy SQL 的 deferred 门禁保持不变。安全工件不进入仓库。

### `UPG-DEPLOY-111`：OneBot 依赖模块升级双写补齐（2026-09-01）

- `extras/scripts/upgrade.py` 已将 `config_discovery.py`、`contract.py`、`transport_contract.py` 纳入 UPGRADE_MAP，避免旧安装只更新 adapter 后缺少 import 依赖。
- `UPGRADE.md` 文件安装位置表同步；静态映射 + 临时目录双写测试与 install 回归共 `8 passed`。不改变 `.env`/config/SOUL、SessionDB、NapCat 或版本号；这是部署完整性修复，不代表新版本已发布。

### `UPG-AGENT-112`：provider projection 测试顺序隔离（2026-09-01）

- 将 import smoke test 从“进程中绝不能有 `run_agent`”改为“导入前后不新增 `run_agent`”，消除与完整 `run_agent` 组合测试的收集顺序耦合。
- 结果：`run_agent`/steer/参数清洗/provider projection` 组合 `356 passed, 1 warning`；不改变 provider projection 默认未接线、SessionDB、MemoryProvider 或 OneBot 语义。

### `UPG-DEPLOY-113`：本轮活动依赖端口双写（2026-09-01）

- 依据本轮新增/改动的活动 import 路径，将 Gateway ledger/stall/shutdown/lease、Agent guard/error、SessionDB canonical/replay、environment safety 等依赖端口加入 UPGRADE_MAP，并同步公开目标路径表。
- 本轮依赖端口静态断言与 install/upgrade 回归 `9 passed`；不修改用户配置、数据库、NapCat 或版本号。历史清单的全量 import-graph 审计仍是下一道门禁。

### `UPG-DEPLOY-114`：全量升级 import-graph 审计器（2026-09-01）

- 新增只读 `extras/scripts/audit_upgrade_map.py`，用 AST 解析 `hermes/core` 运行时本地导入与显式 UPGRADE_MAP，支持相对/绝对导入、BOM、bounded skipped 和 `--strict`；不改变复制策略。
- 当前基线为 559 个运行时文件、3180 条本地导入边、347 个显式 map 未映射目标；动态 Python 闭包覆盖全部 559 个运行时文件，`effective_missing_count=0`，`--strict` 通过。下一步仍需按 QQ/Agent/SessionDB/环境/可选平台分层，决定哪些非 Python 资产进入显式发布包，不能把显式数字当作盲目复制授权。
- 审计器 + install/upgrade 回归 `13 passed`；在非 Python 资产闭包、旧安装临时升级回放和双平台验证通过前，不宣称升级包完全闭包。

### `UPG-DEPLOY-115`：OneBot manifest 分层双写（2026-09-01）

- 当前活动 OneBot plugin manifest 已加入升级双写；上游其它可选 manifest 保持不自动复制，避免一机一 Bot 默认能力面变化。动态 Python 闭包和显式 OneBot manifest 共同保证当前活动插件可发现。
- 安装/升级/audit 回归 `15 passed`；不改变用户配置、数据库、NapCat 或版本号。

### `SEC-REVIEW-116`：extras/scripts 标准安全复核（2026-09-01）

- root distribution `extras/scripts` 10/10 scoped files 已完成标准 Codex Security scan，结果 `0 reportable findings`、coverage `complete`，扫描 ID `40fe82c0-b58c-40f1-b0be-4294682ca328`。
- 覆盖升级 copy containment/link refusal、动态 Python/AST boundedness、BOM/path-free audit、NapCat token local write、legacy migration/decrypt/Qzone helper；没有外部网络或生产资源访问。
- 该收据不替代 nested Hermes 核心安全审查；后续仍需非 Python 资产闭包、旧安装临时升级回放和双平台验证。

### `UPG-DEPLOY-117`：动态 runtime Python 升级闭包（2026-09-01）

- 保留显式 UPGRADE_MAP，同时自动复制受限的 `hermes/core` runtime `.py`，排除 tests/docs/隐藏/VCS/source symlink；当前 audit 的 559 个 runtime Python 全部有 effective copy coverage。
- 动态闭包不等于全量发布完成：非 Python 资产、可选 provider/platform manifest 仍按分层策略单独进入清单；默认配置能力面不扩大。

### `UPG-DEPLOY-118`：升级路径 containment/link 门禁（2026-09-01）

- 显式/动态升级条目统一拒绝绝对路径、`..`、源/目标 symlink/junction 和解析后越界；traversal/link 临时回归纳入 install/upgrade/audit `15 passed`。
- 该门禁不写数据库、不改用户配置，失败只 bounded skip；后续发布包仍需旧安装回放和双平台验证。

### `DOC-DEPLOY-119`：升级发布包分层矩阵（2026-09-01）

- 新增 `docs/UPGRADE_PACKAGE_MATRIX.md`，冻结 runtime Python 动态闭包、活动 OneBot manifest、模板/配置、可选上游插件 manifest、NapCat/Live2D 二进制五层的所有权和复制策略。
- 当前一机一 Bot profile 只自动复制活动 OneBot manifest；上游其它 provider/platform/memory manifest 保持 opt-in，避免改变默认能力面。非 Python 资产、旧安装回放、Linux 权限/路径和可选插件包仍需独立门禁。
- 该文档只做维护者计划，不改变运行配置、数据库、版本号或发布状态。

### `UPG-DEPLOY-120`：动态闭包增量枚举上限（2026-09-01）

- runtime Python 闭包达到 10,000 文件上限后立即停止增量遍历，移除 `sorted(rglob())` 的预物化开销；新增上限回归，install/upgrade/audit `16 passed`。
- 这是对 `UPG-DEPLOY-117` 的资源健壮性补充；当前根脚本安全收据需以新工作树复审为准。

### `SEC-REVIEW-121`：extras/scripts 最终增量安全复核（2026-09-01）

- 当前 root `extras/scripts` 10/10 scoped files 已完成标准 Codex Security scan，结果 `0 reportable findings`、coverage `complete`，扫描 ID `061fd5e8-c658-42a6-b29d-6440d1baf891`。
- 最终快照覆盖动态 Python 增量停止、路径/link containment、AST audit BOM/path-free 输出、OneBot manifest 双写和本地 token 流程；install/upgrade/audit `16 passed`，未访问生产或外部资源。
- `SEC-REVIEW-116` 保留为前一快照历史记录；发布前以后续工作树的新收据为准。

### `UPG-DEPLOY-123`：stale upgrade entry 清理与完整复制 smoke（2026-09-01）

- 清理旧 OneBot/QQ/模板/NapCat/TTS 映射，替换当前 `配置API.bat`；临时 source smoke 实际复制 `585 files` 且 `0 skipped`，活动 OneBot/Agent/SessionDB 文件均存在。
- install/upgrade/audit `16 passed`；用户配置、数据库、NapCat 和版本号不变。该切片已由后续 `SEC-REVIEW-124` 复核。

### `SEC-REVIEW-124`：升级器最终安全复核（2026-09-01）

- root `extras/scripts` 10/10 文件完成标准安全扫描，结果 `0 reportable findings`、coverage `complete`，扫描 ID `551fe70c-b9e3-416a-9d96-b068bc4fbc4b`。
- 最终快照覆盖 stale map 清理、动态闭包增量上限、containment/link 门禁、AST audit、manifest 双写和 token 处理；install/upgrade/audit `16 passed`，完整临时 smoke `585 updated, 0 skipped`。
- 该收据不替代 nested Hermes 核心扫描；SQLite replay、Linux 和真实 provider/NapCat 证据仍 pending。

### `UPG-OB-125`：NapCat 当前回环账号只读证据（2026-09-01）

- 在当前 NapCat 3000/3001 回环端口上执行 adapter `connect()` + `get_login_info`，自动发现凭据成功，`retcode=0/status=ok`，未执行任何写 action。
- 该证据只关闭当前登录/认证连接问题；真实消息发送、media URL provenance、streaming、DNS pinning 和多实例仍按独立门禁推进。

### `UPG-DEPLOY-126`：Windows installer/release builder 路径统一（2026-09-01）

- Inno/NSIS/update/release builder 已统一当前目录事实：`extras/scripts`、`modules/napcat`、`extras/node`、`hermes/core/requirements.txt`、`配置API.bat`；删除旧 helper 路径。
- 静态 installer/setup/upgrade/audit 回归 `20 passed`，临时完整 upgrade `585 updated/0 skipped`。本机无 ISCC/NSIS，真实安装器编译和安装后启动仍 pending。

### `SEC-REVIEW-127`：extras 安装/更新入口安全复核（2026-09-01）

- root `extras` 14/14 scoped files 已完成标准 Codex Security scan，结果 `0 reportable findings`、coverage `complete`，扫描 ID `96c19777-04ac-4228-9d75-473ca52fc3b5`。
- Inno/NSIS/update/release builder 路径、组件边界、配置保护和 stale helper 引用均已覆盖；installer path/setup/upgrade/audit `20 passed`。ISCC/NSIS 缺失，真实 binary 编译和干净机安装仍 pending。

### `UPG-DEPLOY-128`：NSIS editable install 路径修正（2026-09-01）

- NSIS 两处 pip editable install 已指向 `hermes/core`，与 requirements、extras/scripts 和 Inno/update/release builder 目录事实一致；静态回归 `20 passed`。
- ISCC/NSIS 未安装，真实 binary 编译/干净机安装仍是发布门禁；不改用户配置、数据库或 NapCat。

### `SEC-REVIEW-129`：extras 安装器最终安全复核（2026-09-01）

- root `extras` 14/14 scoped files 完成标准安全扫描，结果 `0 reportable findings`、coverage `complete`，扫描 ID `24ea7c7c-e58b-4d4a-9dfd-f43e4db34653`。
- 最终快照覆盖 Inno/NSIS editable install、模块/脚本路径、update/release builder、containment/link 和配置保护；installer/setup/upgrade/audit `20 passed`，临时完整 upgrade `585 updated/0 skipped`。

### `SEC-REVIEW-130`：extras 最终当前工作树安全复核（2026-09-01）

- root `extras` 14/14 scoped files 完成标准安全扫描，结果 `0 reportable findings`、coverage `complete`，扫描 ID `c38ec7d9-d154-4bcc-8ab1-35827bdbe44b`。
- 最终快照包含 NSIS `hermes/core` editable install 修正；路径、动态 upgrade containment、配置保护和 stale 引用均已复核。installer/setup/upgrade/audit `20 passed`，临时完整 upgrade `585 updated/0 skipped`。
- ISCC/NSIS 未安装，真实 binary 编译和干净机 install/start 仍是发布门禁。

### `UPG-DEPLOY-131`：升级器 dry-run 预演（2026-09-01）

- `upgrade.py --dry-run` 现在只验证并列出升级计划，不创建 `~/.hermes`/模板目录、不复制文件、不修改用户配置；正式统计同步修正为 `updated`。
- 当前 source dry-run `585 planned/0 skipped`，正式临时 smoke `585 updated/0 skipped`，installer/setup/upgrade/audit `22 passed`；真实 binary 编译和旧安装回放仍 pending。

### `SEC-REVIEW-132`：extras/scripts dry-run 最终安全复核（2026-09-01）

- root `extras/scripts` 11/11 scoped files 完成标准安全扫描，结果 `0 reportable findings`、coverage `complete`，扫描 ID `8af92b60-7130-413a-94e9-f17d33b7fab1`。
- dry-run/正式复制控制流、路径/link 门禁、动态闭包和 token/config 保护均已覆盖；`22 passed`，`585 planned/0 skipped` 与 `585 updated/0 skipped` smoke。ISCC/NSIS 和跨平台运行仍 pending。

### `FIX-OB-133`：静默契约文档与实现顺序对齐（2026-09-01）

- 将 `docs/FIX_silence_contract_and_exit_loop.md` 第 4.1 节的 adapter 伪代码改为当前生产实现的事务顺序：`normalize -> send -> commit marker state`；这与第 10.2 节的失败不 quiet 和纯标记 deferred 要求一致。
- P4/替代方案同步明确为“零用户可见硬编码兜底文案”；内部系统反馈是不可见协议常量，不属于用户侧兜底回复。
- 第 10.4 节状态词与文档顶部对齐：当前是“已实施候选”，只有真实 provider/NapCat、跨平台和 replay 门禁齐全后才改为“已实现”。
- 生产实现、UPGRADE_MAP 和回归测试范围不变；仍需真实 provider/NapCat、proxy、跨平台进程/WAL 和 SQLite replay 证据后才可更新版本号或公开发布。

### `UPG-DB-137`：SessionDB Gate 1 `escape_like` canonical facade 接入（2026-09-01）

- 已将上游 `hermes_state_common.escape_like` 以惰性 `SessionDB.escape_like()` facade 接入本地 v11 SessionDB 的 session-id/title 解析和两个 canonical LIKE fallback 调用点；common port 不反向导入 facade，避免循环。
- 该 helper 纯处理 LIKE 转义，不改变 SQL 参数绑定、`ESCAPE '\\'`、搜索/解析返回 shape、排序、事务、WAL、FTS、OneBot 或 MemoryProvider 语义；未修改 `SCHEMA_VERSION=11` 或 DDL。
- `test_canonical_modules.py` 新增 canonical spy 和 literal wildcard 回归；common/canonical/search/schema/v26 focused `40 passed, 1 warning`，`tests/test_hermes_state.py` `212 passed, 1 warning`，`py_compile`/`git diff --check` 通过。唯一 warning 为预存 `core/tools/skills_guard.py:627` 非法转义。
- 另以项目 venv 对 1006 组固定/随机 `%`、`_`、反斜杠和 CJK 输入做 facade/canonical 逐字等价属性检查，全部通过。
- 该证据只完成一个 Gate 1 纯 helper，不代表 schema/search/portability mixin 接管；真实历史副本、Linux/Windows replay、跨进程 WAL、FTS rebuild 和生产切换继续 deferred。

### `UPG-OB-134`：静默契约第 10 节状态去重与最终回归（2026-09-01）

- 复审确认 `GroupExecutor` 的 turn nonce completion、空 `final_response` hook、旧 session guard 释放等待、单次 feedback retry、pending 用户事件优先和 `channel_prompt` ephemeral 边界；没有调用不存在的 `event.with_system_note`。
- `GroupTurnCompletion`/`AgentOutcome` 增加 `state_recorded`，OneBot buffer 真实完成 `GroupState.record_reply()` 后 executor 不再重复计数；失败时 executor 仍可兜底。Gateway contract retry 的 `(empty)` sentinel 只在普通路径转换为用户提示，重试路径保持空并标记失败。
- 现有 marker 清洗、正文向发送/outcome/recorder/buffer 传递、成功交付后 QUIET、`exiting_streak=0/1/2`、直接 @/别名/reply-to-bot reset、recorder soft-close 降级和 continuation 顺序均保持。
- 回归：`test_onebot_silence_contract.py` 21 项及 OneBot runtime/contract `33 passed, 1 warning`；empty-response/repetition/recovery persistence `9 passed, 1 warning`；`py_compile`/`git diff --check` 通过。warning 为预存 `core/tools/skills_guard.py:627` 非法转义；无关 `test_fast_command` 的 `request_overrides=None` 预存失败未改。
- 仍需真实 provider 空响应、marker streaming、Linux/Windows 独立进程 guard/WAL、真实 Gateway + MemoryProvider/SessionDB transcript、proxy retry 和 NapCat 发送证据；不更新版本号或公开 `CHANGELOG.md`。

### `SEC-REVIEW-135`：静默契约增量差异安全收据（2026-09-01）

- nested Hermes 当前工作树相对 `b9b0988fc5f2eca8dad41a24fc91dc5f3bef07e7` 的 77 个 compact diff rows 已完成 Codex Security 差异审查，`0 reportable findings`；扫描 ID `cad5d0a9-7351-4178-93d3-40ab98016cde`。
- 本地证据：OneBot 专项 `90 passed, 1 warning`；Gateway/空响应/lease/persistence 组合 `53 passed, 1 warning`；`py_compile`/`git diff --check` 通过。TAC 状态无法由当前桌面工具面核验，未访问生产或外部资源。
- 发布门禁不变：真实 provider/NapCat、跨平台进程/WAL、真实 SessionDB/MemoryProvider replay、proxy retry 和 installer binary/干净机 install/start 仍需独立证据；不更新版本号或公开 changelog。

### `UPG-DB-136`：SessionDB Gate 0 Windows 合成回放合同（2026-09-01）

- Windows 合成 SQLite fixture 的只读 replay/probe/export/search/import-dry-run/rollback 组合已通过 `53 passed, 1 warning`；源 hash 保持不变，runtime `state.db`、symlink 和 sidecar 恶意形态拒绝有效。
- 该证据只关闭回放工具自身的 Windows 合同；真实脱敏历史副本、Linux WAL/权限、跨进程写入和真实 MemoryProvider/SessionDB transcript 仍 deferred，Gate 1 mixin 接入暂不推进。

### `SEC-REVIEW-138`：SessionDB Gate 1 当前工作树差异安全收据（2026-09-01）

- 当前 nested Hermes 工作树相对 `b9b0988fc5f2eca8dad41a24fc91dc5f3bef07e7` 的 77 个 compact diff rows 已完成 Codex Security 差异审查，`0 reportable findings`；扫描 ID `a896ad4f-beee-49f1-87d3-0c93d68d0634`。
- Gate 1 `escape_like` 惰性 facade、5 个参数化 LIKE 调用点、OneBot/Gateway/Memory/Environment 既有边界均已复核；`40 passed`、`212 passed`、部署 `20 passed`，`py_compile`/`git diff --check` 通过。
- 发布门禁不变：真实历史 SQLite、Linux/WAL、provider/NapCat、proxy 和 installer binary/干净机启动仍待独立证据，不更新版本号或公开 changelog。

### `DOC-AG-139`：TurnContext 只读 seam 候选冻结（2026-09-01）

- 下一条 Agent Runtime 实施切片限定为 `TurnContext` 数据合同、API sidecar 纯函数、index reanchor 和 compression predicates 的 import/边界测试。
- `build_turn_context()`、session/memory/MCP/provider/compression side effect 和 `run_agent` 主循环 wiring 继续 deferred；本地 metadata/transcript owner 未冻结前不得接入 `api_content`。
- 现有候选 port 的离线 import smoke 通过，未加载 `run_agent`/`hermes_state`/`gateway.run`；这只是 side-effect 负向证据，不代表 TurnContext 已实现。

### `UPG-AGENT-140`：TurnContext 纯合同兼容端口（2026-09-01）

- 新增 `core/agent/turn_context_contract.py`，实现 `TurnContext` 数据合同、API sidecar 纯函数、user-index reanchor 和压缩 predicates；不复制上游 `build_turn_context()`，不接入 `run_agent` 主循环。
- 8 项 focused tests、Agent Runtime 组合 `90 passed, 1 warning`，升级器 dry-run `586 files planned, 0 skipped`；`py_compile`/`git diff --check` 通过。
- 主循环 wiring、sidecar metadata/transcript owner、真实 provider/NapCat、Linux/WAL 和生产 SessionDB 仍保持 deferred，后续由 Volta另立接入 Change ID。

### `UPG-AGENT-145`：message metadata 纯合同兼容端口（2026-09-01）

- 新增 `core/agent/message_metadata_contract.py`，只提供 timestamp stamp/append 的内存合同；不接入 `run_agent`、SessionDB 或 provider。
- metadata/TurnContext/Agent Runtime 组合 `114 passed, 1 warning`，升级 dry-run `587 planned/0 skipped`，`py_compile`/`git diff --check` 通过；真实 sidecar owner 和主循环 wiring 继续 deferred。

### `SEC-REVIEW-146`：message metadata 纯合同端口差异安全收据（2026-09-01）

- nested Hermes 当前工作树相对 `b9b0988fc5f2eca8dad41a24fc91dc5f3bef07e7` 的 79 个 compact diff rows 已完成 Codex Security 差异审查，`0 reportable findings`；扫描 ID `5917a80c-fd93-4bc2-9fed-ca37205895b6`。
- metadata/TurnContext/Agent Runtime/部署组合 `114 passed, 1 warning`，升级 dry-run `587 planned/0 skipped`，`py_compile`/`git diff --check` 通过；TAC 状态无法由当前桌面工具面核验。
- 该收据只覆盖纯内存 metadata 端口，不代表 `run_agent`/SessionDB/provider/NapCat 主循环接线、真实历史回放、Linux/WAL、proxy 或 installer binary 已完成；不更新版本号或公开 changelog。

### `UPG-OB-147`：OneBot 空 sentinel 契约路径补强（2026-09-01）

- OneBot `GroupExecutor` 注册 nonce 时显式设置 `_onebot_contract_required`；Gateway 只在该准入回合把 agent `"(empty)"` 保持为空，以便完成钩子触发一次会话内反馈重试，普通平台路径仍保留现有空响应提示。
- 重试使用 `channel_prompt` ephemeral 注记和空 API user turn，不重复媒体/STT 预处理，不写 durable transcript/trajectory/SessionDB/MemoryProvider；最多一次，失败回到 silent/告警。
- 验收：相关组合 `115 passed, 1 warning`；`py_compile`/`git diff --check` 通过。预存 warning 为 `core/tools/skills_guard.py:627` 非法转义。
- 保留门禁：真实 provider/NapCat、marker streaming、proxy、Linux/WAL、生产 SQLite/跨进程 replay 和发布版本仍 deferred。

### `SEC-REVIEW-148`：OneBot 空 sentinel 契约增量安全收据（2026-09-01）

- 当前 nested Hermes 相对 `b9b0988fc5f2eca8dad41a24fc91dc5f3bef07e7` 的 79 个 compact diff rows 已完成 Codex Security diff scan `60bf5098-27e5-472e-be66-0460470aecc0`，`0 reportable findings`，coverage complete。
- 重点复核 `_onebot_contract_required`、`contract_retry`、`(empty)` 归一化、nonce completion、session guard release、marker state 和 retry persistence；TAC 状态无法由当前桌面工具面核验。
- 组合回归 `115 passed, 1 warning`，不代表真实 provider/NapCat/streaming/proxy、Linux/WAL、生产 replay 或发布授权已完成；不更新版本号或公开 changelog。

### `DOC-AG-149`：Sidecar/transcript owner 与 TurnContext 接线顺序冻结（2026-09-01）

- owner 定义：`AIAgent.run_conversation()` 的 live `messages` 是当前回合真相；`api_messages` 是 provider projection；JSONL/SessionDB 是 durable writer；`timestamp`/`api_content`/`display_kind` 等 metadata 由 agent 产生但只能在 schema/writer capability 明确后落库。
- 本地 v11 事实：`messages` 表没有上游 `api_content`、`display_kind`、`display_metadata` 列，SessionDB writer 默认本地 wall clock；现有 `message_metadata_contract.py`/`turn_context_contract.py` 只完成纯内存合同。
- 下一阶段固定为 `structural API-copy`（不改变默认字段行为）→ `timestamp-only` → additive schema gate 后的 sidecar → 单一 TurnContext helper → 最后评估主循环抽取；保留本地 inline fallback，不覆盖上游大文件。
- 进入条件：每一步都要有 import smoke、bounded input、provider/MemoryProvider/SessionDB transcript 对照、Windows/Linux 适用回归、唯一 Change ID 和安全收据；真实 provider/NapCat、Linux/WAL、生产历史副本和版本发布继续 deferred。

### `UPG-AGENT-150`：TurnContext API-copy sidecar 最小可回退 seam（2026-09-01）

- 依据上游 `agent/turn_context.py`/`conversation_loop.py` 的结构化 API-copy 与 `api_content` projection 语义，新增 `core/agent/turn_context_contract.py::clone_message_for_api()`。它只复制 JSON-shaped 容器，共享不可变叶值，并在副本上消费 user/assistant sidecar；不负责 sidecar stamping、持久化或 schema 迁移。
- `core/run_agent.py` 只在现有 provider `api_messages` 构造点使用该 helper；已有 memory/plugin ephemeral 注入在 sidecar 已提供精确 API 内容时不再重复拼接。live transcript、JSONL/SessionDB writer、MemoryProvider 输入、provider fallback、OneBot/NapCat 和 Windows bootstrap 仍使用本地路径。
- 验证：TurnContext contract/sidecar seam `13 passed, 1 warning`；Agent Runtime/Memory/压缩组合 `71 passed, 1 warning`；`test_run_agent.py` 加 OneBot silence/transport `363 passed, 1 warning`；`py_compile`/`git diff --check` 通过。warning 为既存 `core/tools/skills_guard.py:627` 非法转义。
- 本条只关闭 API projection 的结构化 clone/sidecar 副本边界，不代表 `build_turn_context()`、timestamp-only、v11 sidecar writer/loader、contract retry、compression/memory 顺序或跨重启 cache 语义已融合。真实 provider/NapCat、Linux/WAL、生产历史副本和发布继续 deferred。

### `SEC-REVIEW-151`：TurnContext structural API-copy 增量安全收据（2026-09-01）

- 当前 nested Hermes 相对 `b9b0988fc5f2eca8dad41a24fc91dc5f3bef07e7` 的 79 个 compact diff rows 已完成 Codex Security diff scan `61729166-2913-462b-b7f9-ea92ba3678c6`，`0 reportable findings`，coverage complete。
- 重点复核递归 clone 的 nested-container 隔离、`api_content`/display 字段剥离、provider sanitizer、canonical transcript、OneBot nonce/retry 与 SessionDB writer；TurnContext/sidecar/session-meta/Unicode/API-copy `47 passed, 1 warning`。
- 扫描期间发生文档日志工作树漂移，收据以启动时代码快照为准；TAC 状态无法由当前桌面工具面核验。真实 provider/NapCat、proxy、Linux/WAL、生产历史副本和发布版本仍 deferred。
- 回滚：移除该 helper、`run_agent` 单点调用、focused tests 和本条记录即可恢复原 inline API-copy 路径；不修改数据库、配置、缓存或外部资源。

### `UPG-AGENT-152`：timestamp-only 平台事件时间戳接线（2026-09-01）

- Gateway 通过有界 `_event_timestamp_seconds()` 将 `MessageEvent.timestamp` 传给 `AIAgent.run_conversation(persist_user_timestamp=...)`；current user mapping 使用 metadata append/stamp，provider copy 移除 persistence-only `timestamp`。
- OneBot group/DM 从 payload `time` 生成事件时间；JSONL writer 不覆盖已有 timestamp；SessionDB v11 `append_message` 增加可选 timestamp keyword，默认本地 wall clock 和旧 writer 调用形状保持。
- 验收：timestamp/TurnContext/API-copy/SessionDB/OneBot 聚焦 `22 passed, 1 warning`；扩大集合 `243 passed, 1 warning`；`py_compile`/`git diff --check` 通过。预存 warning 为 `skills_guard.py:627` 非法转义。
- 保留门禁：不接入 `api_content`/display schema，不改变 MemoryProvider、OneBot marker/retry 或 v11 DDL；真实 provider/NapCat、Linux/WAL、生产历史副本和发布版本继续 deferred。

### `SEC-REVIEW-153`：timestamp-only 事件 metadata 增量安全收据（2026-09-01）

- 当前 nested Hermes 相对 `b9b0988fc5f2eca8dad41a24fc91dc5f3bef07e7` 的 79 个 compact diff rows 已完成 Codex Security diff scan `9450f9bd-f666-4c48-bbd2-94db33064e86`，`0 reportable findings`，coverage complete。
- 重点复核 OneBot `time` 解析、Gateway handoff、canonical user mapping、provider timestamp 剥离、SessionDB optional keyword 和 legacy writer 分支；timestamp/TurnContext/API-copy/SessionDB/OneBot 聚焦 `22 passed, 1 warning`，扩大集合 `243 passed, 1 warning`。
- TAC 状态无法由当前桌面工具面核验；late-event replay、真实 provider/NapCat、proxy、Linux/WAL、生产 SQLite 和发布版本继续 deferred。

### `UPG-DB-154`：api_content/display metadata additive schema gate 证据（2026-09-01）

- 新增针对 `messages.api_content`、`messages.display_kind`、`messages.display_metadata` 的 v26 copy-only gate dry-run/临时副本提交回归；source hash、backup、`schema_version=11` 和 allowlist 前置均受验证。
- 默认 gate 仍 disabled，未修改运行 `state.db`，未接入 SessionDB writer/loader、sidecar backfill 或 provider replay；仅证明 additive schema gate 可显式、事务化、可回滚地添加列。
- 验收：v26 compat/copy-gate/schema-probe/replay/portability/canonical `79 passed, 1 warning`；`py_compile`/`git diff --check` 通过，warning 为既存 `skills_guard.py:627` 非法转义。
- 下一门禁：真实脱敏历史副本、Linux/WAL/跨进程证据和 sidecar length/surrogate/role/stale-on-rewrite writer contract；在此之前不启用 `api_content` 生产持久化或更新版本号。

### `SEC-REVIEW-155`：可选 message sidecar writer/loader 增量安全收据（2026-09-01）

- 当前 nested Hermes 相对 `b9b0988fc5f2eca8dad41a24fc91dc5f3bef07e7` 的 79 个 compact diff rows 已完成 Codex Security diff scan `7243bbb0-02ed-4f40-9b0d-553057a118ef`，`0 reportable findings`，coverage complete。
- 重点复核 optional-column allowlist、动态 INSERT、sidecar/display metadata 的 role/长度/surrogate/JSON 边界、v11 no-op、loader/provider 剥离、replace/replay 和 Agent flush；SessionDB 组合 `296 passed, 1 warning`，Agent/OneBot 组合 `56 passed, 1 warning`。
- TAC 状态无法由当前桌面工具面核验；真实 provider/NapCat、proxy、Linux/WAL、生产历史副本、late-event replay 和 sidecar stale-rewrite 压力证据继续 deferred。

### `UPG-DB-156`：gated sidecar history timestamp 回放保持（2026-09-01）

- gated v26 optional-column 数据库的 conversation loader 恢复 durable `timestamp`；replace writer 使用输入时间并维持顺序，v11 无 optional columns 时保持原返回 shape/本地 wall clock。
- provider clone 移除 timestamp/display metadata，`api_content` 只替换 user/assistant 的 API content；SessionDB writer/loader 只在实际列存在时启用，默认不自动 DDL。
- 验收：sidecar/timestamp/TurnContext/API-copy `46 passed, 1 warning`，SessionDB/v26/replay/portability/canonical `296 passed, 1 warning`；预存 warning 为 `skills_guard.py:627` 非法转义。
- 下一门禁：sidecar stale-on-rewrite 的全量压缩/lineage 回放、真实历史副本、Linux/WAL/跨进程和 provider/NapCat；在此之前不启用生产 v26 writer 或更新版本号。

### `SEC-REVIEW-157`：gated sidecar timestamp/rewrite 增量安全收据（2026-09-01）

- 当前 nested Hermes 相对 `b9b0988fc5f2eca8dad41a24fc91dc5f3bef07e7` 的 79 个 compact diff rows 已完成 Codex Security diff scan `7cf7041b-c98f-4ec0-bb03-821bb72fdaea`，`0 reportable findings`，coverage complete。
- 重点复核 optional sidecar writer/loader、fixed-column SQL、metadata bounds、v11 no-op、gated timestamp restore、replace ordering、provider stripping 和 Agent flush；SessionDB `296 passed`，Agent/OneBot/sidecar `46 passed`，各有 1 个预存 warning。
- 该收据不关闭 stale-on-rewrite 全量 compression/lineage、真实历史副本、Linux/WAL/跨进程、provider/NapCat 或生产迁移门禁；TAC 状态无法由当前桌面工具面核验。

### `UPG-AGENT-158`：canonical rewrite 的 stale api_content 清理（2026-09-01）

- canonical user override/merge、Unicode/image recovery 和 compression summary rewrite 会清理旧 `api_content`；未重写的 gated v26 rows 保留 sidecar/timestamp，provider clone 继续剥离 persistence/display 字段。
- 验收：`run_agent`/compression/OneBot/sidecar `432 passed, 1 warning`；SessionDB/v26/replay/portability/canonical `296 passed, 1 warning`；stale-sidecar focused `19 passed, 1 warning`；`py_compile`/`git diff --check` 通过。
- 下一门禁：真实压缩 lineage、late-event/历史副本回放、Linux/WAL/跨进程和 provider/NapCat；在证据完成前不启用生产 v26 sidecar 或更新版本号。

### `SEC-REVIEW-160`：stale api_content 清理与 gated replay 安全收据（2026-09-01）

- 当前 nested Hermes 相对 `b9b0988fc5f2eca8dad41a24fc91dc5f3bef07e7` 的 79 个 compact diff rows 已完成 Codex Security diff scan `2d255945-b0d0-4f76-b7e8-202133c1a4e1`，`0 reportable findings`，coverage complete。
- 重点复核 canonical rewrite 清理、optional sidecar/display writer/loader、gated timestamp replace、provider projection、OneBot output contract 和 SessionDB flush；`run_agent`/compression/OneBot/sidecar `432 passed`，SessionDB/v26/replay `296 passed`，stale focused `19 passed`。
- TAC 状态无法由当前桌面工具面核验；真实压缩 lineage、历史副本、provider/NapCat、proxy、Linux/WAL、late-event replay 和生产迁移继续 deferred。

### `SEC-REVIEW-161`：gated api_content producer 与 stale replay 安全收据（2026-09-01）

- 当前 nested Hermes 相对 `b9b0988fc5f2eca8dad41a24fc91dc5f3bef07e7` 的 79 个 compact diff rows 已完成 Codex Security diff scan `18e44803-046c-4697-b1d8-2f9d79ce24a4`，`0 reportable findings`，coverage complete。
- 重点复核 gated producer、memory/plugin 注入去重、optional writer/loader、stale rewrite、timestamp replay、provider projection 和 OneBot contract；`run_agent`/compression/OneBot/sidecar `433 passed`，SessionDB/v26/replay `296 passed`。
- TAC 状态无法由当前桌面工具面核验；真实 provider/NapCat、proxy、Linux/WAL、历史副本/late-event replay、跨进程和生产迁移仍 deferred。

### `UPG-DB-162`：gated sidecar parent/child lineage 合成回放（2026-09-01）

- 临时 v26-shaped copy-only 数据库已验证 parent/child `include_ancestors` 回放：sidecar/display metadata 与 durable timestamp 和 canonical content 对齐；child replace 后仍保持顺序和 payload。
- 验收：sidecar writer/loader/flush/producer、timestamp、TurnContext/API-copy 与 lineage `7 passed, 1 warning`；预存 warning 为 `skills_guard.py:627` 非法转义。
- 该证据只关闭 Windows 合成 lineage 合同；真实脱敏历史库、压缩 lineage、late-event、Linux/WAL/跨进程、provider/NapCat 和生产迁移仍需独立门禁。

### `ENV-163`：跨平台回放与生产数据使用门禁（2026-09-01）

- 已确认本机可以通过局域网 SSH 访问 Linux 主机，但远端包含真实生产数据；升级工作默认不连接、不读取、不复制、不迁移远端数据库。任何生产证据必须先取得明确的只读/脱敏副本，并保留源文件及 WAL/SHM/journal 的哈希和回滚证明。
- 当前本机 WSL 仅有 Docker Desktop，没有用户 Linux 发行版；Linux/WAL/权限/独立进程证据不能用 Windows 或合成 fixture 代替，需在后续准备专用 WSL/CI 环境后单独验收。
- Windows 侧继续使用可删除、可重建的独立 scratch 目录和临时 SQLite；允许从零生成测试库，但不得清理运行 `state.db`、用户配置、缓存或生产文件。
- 门禁保持：真实历史 replay、late-event/压缩 lineage、Linux/WAL/跨进程、真实 provider/NapCat 和生产切换完成前，不接管 v26 schema/mixin，不更新版本号，不发布公开 changelog。


### `SEC-REVIEW-141`：TurnContext 纯合同端口差异安全收据（2026-09-01）

- nested Hermes 当前工作树相对 `b9b0988fc5f2eca8dad41a24fc91dc5f3bef07e7` 的 78 个 compact diff rows 已完成 Codex Security 差异审查，`0 reportable findings`；扫描 ID `661b5bf5-71ef-482f-9470-cc905b1620bc`。
- TurnContext focused `8 passed`、Agent Runtime 组合 `90 passed`、升级 dry-run `586 planned/0 skipped`，`py_compile`/`git diff --check` 通过；真实 provider/NapCat、Linux/WAL、proxy、生产 replay 和主循环 wiring 仍保持 deferred。

### `UPG-AGENT-142`：TurnContext reanchor 合成 user row 边界修正（2026-09-01）

- `reanchor_current_turn_user_idx()` 现在与上游 actionable-user 语义更接近：exact match 优先，fallback 排除 `display_kind`、压缩摘要/微压缩、空白和 synthetic row，保留真实多模态输入。
- 8 项 TurnContext focused 和 Agent Runtime `90` 项组合回归通过；不接入主循环、不写 `api_content`，后续仍需 metadata/transcript owner 评估。

### `SEC-REVIEW-143`：TurnContext reanchor 边界修正差异安全收据（2026-09-01）

- 当前 nested Hermes 工作树相对 `b9b0988fc5f2eca8dad41a24fc91dc5f3bef07e7` 的 78 个 compact diff rows 已完成 Codex Security 差异审查，`0 reportable findings`；扫描 ID `8b3e605e-27e7-46ad-b259-4c8b85b2e965`。
- TurnContext focused `8 passed`、Agent Runtime `90 passed`，并通过 `py_compile`/`git diff --check`；真实运行环境和主循环 wiring 仍保持 deferred。

### `DOC-AG-144`：TurnContext 矩阵状态与实现证据对齐（2026-09-01）

- 矩阵第 6 节现在明确：`turn_context_contract.py` 纯合同已经实现；未完成的是 sidecar metadata/transcript owner、provider/MemoryProvider/SessionDB 对照和 `run_agent` 主循环接入。
- `build_turn_context()`、完整 upstream runtime、大规模 compression/MCP/provider wiring 和真实环境证据继续 deferred。

### `UPG-DB-164`：Windows-first disposable replay/WAL 子进程 harness（2026-09-01）

- 新增 `core/scripts/sessiondb_replay_harness.py` 与 `core/tests/hermes_state/test_replay_harness.py`。runner 不接受 `--source`、不扫描运行目录，只在临时目录从零创建 v11 synthetic parent/child fixture，验证 compression lineage、late-event timestamp ordering、`schema_version=11` 和 v26 runtime table absence。
- harness 启动独立 stdlib WAL writer 子进程，再以独立 `sessiondb_replay` CLI 子进程执行现有只读 probe/export/search/import-dry-run；报告仅输出状态、计数和布尔证据，不输出临时路径、session ID 或消息正文，临时 fixture/report/sidecar 在结束时清理。
- `hermes_state_replay.run_replay()` 增加显式 `tolerate_wal_shm_read_locks=False`；默认严格 source/sidecar 不变语义保持不变。只有 disposable WAL harness 通过 CLI 显式开启该选项，允许 SQLite 只读进程改变 `-shm` 锁页，同时仍强制主文件、WAL、journal hash/size 不变，并单独记录 `shm_changed_during_read` 与容忍状态。
- 验收：replay harness/replay/sidecar focused `21 passed, 1 warning`；SessionDB/v26/copy-gate/schema/replay/portability/canonical 组合 `315 passed, 1 warning`；`py_compile` 与 `git diff --check` 通过。唯一 warning 为既存 `core/tools/skills_guard.py:627` 非法转义 `SyntaxWarning`。
- 保留门禁：当前只证明 Windows synthetic subprocess/WAL 合同；真实授权脱敏历史库、真实压缩 lineage/late-event、Linux/WSL WAL/权限/独立进程、生产数据库、v26 migration/mixin、provider/NapCat 和发布仍 deferred。harness 不改变生产 SessionDB 主路径或自动迁移。
- 回滚：移除 harness 脚本/测试、显式 `-shm` 容忍参数及本条记录即可恢复默认 replay；不会删除或修改生产数据库、运行配置、用户数据或外部资源。

### `UPG-DB-166`：`-shm` 读锁容忍的形状门禁（2026-09-01）

- 只读 WAL replay 的 `-shm` 容忍继续是显式 opt-in，并新增 journal mode、两侧 WAL 存在、`-shm` regular/无 error/同尺寸的联合条件；主库、WAL 和 journal hash/size 仍必须稳定。
- 该条件专门区分 SQLite 正常共享内存读锁页变化与 sidecar 被替换、缺失、尺寸漂移或非 regular 的异常情况；默认历史副本 CLI 仍严格拒绝任何变化。
- 回归：`test_replay.py` 增加正常 WAL 与异常形状/非 WAL 反例；Windows disposable harness、replay 和 SessionDB 回放门禁保持通过。
- 保留门禁：这只是 Windows synthetic subprocess 证据，不关闭真实脱敏历史库、Linux/WSL WAL/权限/跨进程、生产 replay 或 v26 migration。

### `SEC-REVIEW-167`：最终 replay/WAL 快照安全审查收口（2026-09-01）

- 最终工作树 80 个 compact diff rows 已完成 Codex Security diff scan `ff3dd087-a55e-4e56-ae83-7160b5c8689c`，`0 reportable findings`，coverage complete，无工作树漂移警告。
- 审查覆盖 source-copy/runtime state.db 隔离、sidecar allowlist/大小边界、只读连接、显式 `-shm` 容忍、bounded redacted report、子进程 timeout/cleanup 和 v11/v26 不变式。
- 本地 replay/harness focused `15 passed, 1 warning`；warning 为预存 `skills_guard.py:627` 非法转义。TAC、Linux/WSL、真实历史副本、provider/NapCat 和生产数据库仍不在本次证据内。

### `ENV-168`：NapCat 运行 SQL 不进入升级 replay（2026-09-01）

- 本地 NapCat SQLite 主文件及 `-wal/-shm` sidecar 已确认被根仓库和 nested Hermes 的 ignore 规则排除，不会上传 GitHub；它们不是 Hermes SessionDB 的历史副本。
- 当前 Linux 前置只有 Docker Desktop 内部 WSL 项且 Docker CLI/server 不可用；不得将其作为 Linux/WAL 验收环境，后续需准备专用 WSL/CI 或审查过的 Linux 副本环境。
- 升级工作不读取、清空、迁移或改写 NapCat SQL。所有回放和 schema gate 使用独立 scratch/临时副本；可以从零生成测试库，但不得把运行目录当作清理目标。
- 若未来需要历史证据，必须先选择明确授权、脱敏、带哈希的副本，并与 NapCat 运行数据和生产 SSH 数据分开管理。

### `DOC-ARCH-170`：架构能力矩阵状态纠偏（2026-09-01）

- `ARCHITECTURE_TARGET.md` 已将已交付的 lifecycle/lease/stall/ledger 合同、SessionDB 前置 gates、MemoryProvider/Environments 兼容 port 和 API-copy/timestamp/sidecar seam 标为“合同已落地”。
- 这些状态不等于主路径接管：Gateway/Agent wiring、完整 conversation loop、真实历史 replay、Linux/WAL/跨进程、provider/NapCat 和发布仍是独立门禁。

### `UPG-OB-171`：本机 NapCat/OneBot/Gateway 回环实连门禁（2026-09-01）

- 已在隔离 Hermes home 中使用 account-specific NapCat 配置完成真实回环 WS/HTTP 连接；`get_login_info`、Gateway event loop、私聊发送和测试群发送均有 `retcode=0`/message-id 证据。
- Dashboard 临时实例的端口状态、NapCat 账号列表和总状态接口通过；账号发现不回显 token，选择结果不写入真实用户 `.env`。
- 当前只关闭 OneBot “本机连接与发送”前置，不关闭入站用户消息到 agent/judge/memory/provider 的完整响应链；后者仍需在白名单范围内由用户发一条测试消息或使用受控 loopback fixture，不能用 bot 自发消息替代。
- 保留门禁：真实 provider/NapCat streaming、错误/空响应、跨进程 ledger、Linux/WAL、生产数据和发布仍 deferred；本机 smoke 不授权连接生产 Linux 主机。

### `UPG-OB-174`：自动发现 NapCat 当前登录账号 token（2026-09-01）

- 在不提供 `ONEBOT_ACCESS_TOKEN` 的隔离 Gateway 中，使用 bot self-id + `ONEBOT_NAPCAT_CONFIG_DIR` + 自动发现开关完成真实 NapCat WS 连接；account-specific 配置加载和鉴权均通过。
- 该门禁确认 Dashboard/adapter 可以在登录后复用 NapCat 生成的账号配置，不需要按 QQ 号派生 token，也不把 token 写进仓库或测试日志。
- 保留边界：只关闭本机 token discovery/WS 前置；入站白名单消息、LLM response/streaming、Linux/WAL、真实历史 replay 和发布仍需独立证据。

### `DOC-AG-175`：Agent Runtime 矩阵补充 OneBot live 分层门禁（2026-09-01）

- 矩阵已标明本机 transport/outbound live gate 已通过，避免把 NapCat 实连状态继续笼统写成完全 deferred。
- 入站用户消息到 judge/memory/provider 的主链、真实 provider/streaming、Linux/WAL、历史库和发布仍保持独立门禁。

### `UPG-AGENT-176`：首个 selective TurnContext helper 主循环接线（2026-09-01）

- provider API projection 已从重复的本地拼接改为调用 `compose_user_api_content()`；sidecar producer 与 wire projection 使用同一纯函数，带 sidecar 的 user row 不重复注入 memory/plugin context。
- 该切片保持本地 inline prologue、live transcript owner、SessionDB v11、OneBot 和 MemoryProvider 默认语义；不复制上游大文件，也不宣称 `build_turn_context()` 或完整 conversation loop 已融合。
- 验收：相关 focused `13 passed`，`test_run_agent.py` projection/prefetch `4 passed, 319 deselected`，`py_compile` 通过；预存 warning 仍为 `skills_guard.py:627`。
- 下一门禁：provider/MemoryProvider/SessionDB transcript 对照、真实入站 provider response、Linux/WAL/历史 replay 和更多纯 helper 接线；失败时保留 inline fallback。

### `SEC-REVIEW-178`：composition helper 最终差异安全复核（2026-09-01）

- 最终 80 个 compact diff rows 已通过 Codex Security diff scan `d82f515a-e090-4f2e-9525-0e757e8da1e7`，`0 reportable findings`、coverage complete、无工作树漂移警告。
- 复核确认 sidecar producer 与 provider projection 同源、API copy 不回写 canonical transcript、ephemeral context 不重复注入，且 OneBot/v11 默认路径不变；真实 provider/NapCat/Linux/历史 replay 继续独立门禁。

### `UPG-OB-177`：真实入站窗口仍待用户触发（2026-09-01）

- 白名单隔离 Gateway 已连续监听 180 秒，自动 token discovery 和 WS 保持正常，但没有看到授权用户入站消息；因此 agent/provider/delivery 链路不计为通过。
- 下一次测试必须在 Gateway 明确报告 `READY` 后由授权用户发送消息；NapCat 不会把 READY 之前的历史消息回放到新连接。超时应保持负向记录，不放宽白名单或改用 bot self-message。

### `UPG-MEM-181`：自研记忆系统真实集成与旧库 schema 门禁（2026-09-01）

- 已补齐并验证 UnifiedMemoryGateway 的本地实际路径：`memory_maintenance` hook → Layer 0/STM，STM → LTM/EPI consolidation，WFM/core memory CRUD、FTS、graph edge 和 reload/recall。
- `MemoryStore` 现在对旧 v1 `memory_store.db` 执行固定 allowlist 的 additive 字段/表兼容，并在 schema 补列后重建外部 FTS；不自动执行旧表重建、v26 migration 或生产数据清理。
- 旧 table-level unique 的 correction 以 tombstone key 兼容，active-only retrieval 避免旧值重新进入 prompt；正式移除约束仍保留备份门禁。
- 回归：custom memory/builtin provider/MemoryProvider `72 passed, 1 warning`；旧 schema copy、LTM correction、EPI/WFM/core-memory 均有实测。下一步是把 memory provider 生命周期与 `run_agent` 主路径做 transcript 对照，不用 mock 结果替代。

### `SEC-REVIEW-182`：自研记忆 schema 增量修复安全复核（2026-09-01）

- 最终 82 个 compact diff rows 已通过 Codex Security diff scan `7bef11d7-2962-470c-9926-b7f307ce5398`，`0 reportable findings`、coverage complete、无工作树漂移警告。
- 复核覆盖 additive migration allowlist、FTS rebuild、legacy correction tombstone、active-only search、graph/watermark/registry 和 Layer 0/EPI/WFM 真实临时库回归；真实 `memory_store.db`、provider、NapCat、SSH 和生产环境仍未访问。

### `UPG-MEM-183`：真实 Gateway hook scope 传递与 memory lifecycle 对照（2026-09-01）

- Gateway → lifecycle hook 已补齐显式 `chat_type`/chat/user metadata，memory hook 不再依赖 `chat_id` 字符串猜测群/DM；新增纯 context helper 供回归直接验证。
- 真实临时 UnifiedMemoryGateway/MemoryProvider 对照确认完成回合写入 STM、下一轮可 recall，interrupted 回合不写入；custom memory + OneBot runtime 组合 `78 passed, 1 warning`。
- 下一门禁：READY 窗口内真实用户入站触发 agent/provider，跨群 EPI privacy、Layer 0 与 SessionDB transcript 对照；不读取生产 `memory_store.db`。

### `SEC-REVIEW-184`：自研记忆生命周期与 Gateway hook scope 最终安全复核（2026-09-01）

- 当前稳定工作树已完成 Codex Security diff scan `0bf94146-d16e-4f4c-a3ee-f1e6f18c5080`：83/83 审查项闭合，`0 reportable findings`，coverage complete。
- 该收据覆盖 `MemoryStore` additive schema/FTS/correction/active filtering、Layer 0/STM/EPI/WFM/core-memory、Gateway 显式 `chat_type` 与 500 字符 hook context、AIAgent MemoryProvider lifecycle，以及 OneBot/SessionDB 兼容回归。
- 本地实测 custom memory 7 项；组合回归 `78 passed, 1 warning`。预存 warning 为 `skills_guard.py:627` 非法转义。
- 真实 `memory_store.db`、NapCat SQL、SSH/Linux、provider、真实用户入站和发布仍 deferred；该安全收据不扩大迁移或生产权限。下一步仍是 READY 后白名单入站、跨群 EPI privacy、Layer 0 与 SessionDB transcript 对照。

### `UPG-MEM-185`：跨 chat/session memory privacy 与 transcript 对照（2026-09-01）

- `ShortTermMemory`/`MemoryRetriever`/`UnifiedMemoryGateway` 增加可选显式 `chat_type` 过滤；未传该参数时保留旧 session-only 兼容语义，显式 group/dm 请求不会把同一复用 session 中另一种 chat type 的 STM 行带入上下文。
- EPI 增加目标 chat scope 过滤，并由 `BuiltinMemoryProviderAdapter` 通过能力检测传递当前 `chat_type`：group 仍允许既有匿名 group-to-group 联想，但 DM 片段不进入 group recall；没有 chat type 的旧调用继续保留旧搜索行为，不伪称所有 chat 完全隔离。
- Layer 0 `write_message()`/memory hook 增加有界可选 `chat_id`/`thread_id` 来源元数据；opaque chat ID 的 session、chat type、thread 独立写入 temporary JSONL。`MemoryStore.get_chat_buffer()`/`trim_chat_buffer()` 增加显式 type 过滤，避免同一 opaque ID 的 buffer 串 scope。
- 新增 `core/tests/agent/test_memory_scope_integration.py`：临时 UnifiedMemoryGateway/MemoryStore、Gateway hook、Layer 0、EPI、BuiltinMemoryProvider、AIAgent completed/interrupted 与 SessionDB parent/child transcript lineage 共 `6 passed`；memory/provider/session/OneBot runtime 扩大集合 `140 passed, 1 warning`。`py_compile`/`git diff --check` 通过，warning 为既存 `core/tools/skills_guard.py` 非法转义。
- 已知预存：单独组合 `tests/gateway/test_hooks.py` 仍有 4 个旧 built-in hook 注册/handler 断言失败，本切片未修改；不把它们归因于 memory scope 修复。
- 保留边界：LTM 仍是本地产品设计的全局提炼事实层，EPI group→group 仍为匿名联想；要实现按 user/chat 的全量 LTM/EPI 隔离，需要独立 schema/source-user migration gate。真实 `memory_store.db`、真实入站/provider、NapCat SQL、SSH/Linux、跨进程 WAL 与发布仍 deferred。
- 回滚：移除显式 chat_type 过滤、Layer 0 source fields、adapter 参数传递和 focused tests/本条记录即可；不删除或修改真实 memory DB、SessionDB、用户配置或外部资源。

### `SEC-REVIEW-186`：UPG-MEM-185 跨 scope/lifecycle 最终安全复核（2026-09-01）

- 当前 staged working-tree 快照已通过 Codex Security diff scan `ca158acb-af28-4f94-8e36-e7d0e61ed042`：9/9 源码审查项闭合，`0 reportable findings`，coverage complete，无工作树漂移。
- 复核范围：STM/EPI/MemoryRetriever/Gateway 显式 scope、DM→group deny、group→group 匿名联想、deferred STM、failed/interrupted/contract-retry、Layer 0 bounded metadata、chat buffer 参数化查询和 OneBot/SessionDB 兼容。
- 本地验证：scope/lifecycle `6 passed`；扩展 memory/provider/session/OneBot/pre-compress 集合 `93 passed, 1 warning`，另有已记录的更宽 `140 passed, 1 warning` 证据；warning 为预存 `skills_guard.py:627` 非法转义。
- 真实 memory DB、NapCat SQL、provider、SSH/Linux、真实入站、跨进程 WAL 和发布仍 deferred；LTM 全局事实层与 EPI 匿名共享语义没有被该收据改写。

### `DOC-PROD-187`：生产验证 Agent 交接 runbook（2026-09-01）

- 新增 `docs/HANDOFF_PRODUCTION_VALIDATION.md`，把 `74e4828` 代码基线及最终 `ab77144` tip 的生产更新步骤、Git 双仓边界、数据库/sidecar 备份、MemoryStore additive migration 观察、NapCat/OneBot 启动检查、READY 入站验证、证据字段和回滚路径固化为可执行清单。
- 该手册只支持当前一机一 Bot 部署：局域网 `origin/main` 是唯一更新来源；不得从 GitHub 更新生产，不得在生产 Agent 中主动发送 QQ 测试消息或修改 NapCat SQL。
- 手册明确生产验证通过后仍不能宣称 v26 全量写入、LTM 全量 user/chat 隔离、完整 conversation loop、Linux/Windows 全矩阵或公开发布完成。
- 手册本身已经过相对路径/占位符隐私检查，未包含生产凭据；随 nested Hermes 局域网提交，不进入根 GitHub 提交。
