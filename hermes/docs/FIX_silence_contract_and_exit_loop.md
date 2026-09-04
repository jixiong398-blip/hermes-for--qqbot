# FIX — 执行层输出契约（沉默标记重构）+ exiting 自我强化循环修复规格

> **状态**：已实施候选 —— 代码与 Windows 离线/合成回归已完成；真实 NapCat/provider、双平台进程/WAL、proxy 和 marker streaming 门禁仍待后续证据，不作为已发布能力。
> **来源**：开源用户实报（@ 后 bot 无回复）+ 维护者逐行代码核实
> **影响文件**：`core/plugins/platforms/onebot/group_executor.py`、`core/plugins/platforms/onebot/adapter.py`、`core/plugins/platforms/onebot/semantic_judge.py`、`core/plugins/platforms/onebot/trigger_coordinator.py`，以及为临时反馈和非持久化重试提供边界的 `core/gateway/run.py`、`core/run_agent.py`。
> **关联文档**：`docs/BUGS_v0.14.4.md`（@ 强信号契约——本规格是该契约在执行层的补完）

---

## 1. 现象

两个实报/观察到的现象：

**现象 A（用户实报，有日志）**：群聊中 @bot 后无回复。日志证据链（已脱敏）：

```
TriggerCoordinator on_ingested MENTION group={{GROUP_ID}}      ← @ 已触发并派发
gateway.run inbound message ... '@{{BOT_QQ}} 在吗？'
run_agent conversation turn ... history=214                     ← agent 已执行
API call #1 ... latency=53.0s  tool_turns=22                   ← LLM 已调通
Sending response (7 chars) to group:{{GROUP_ID}}
adapter: LLM chose [QUIET], going silent, group={{GROUP_ID}}   ← 被适配器吞掉
```

7 字符 = 字面量 `[QUIET]`。完整链路全部正常，最后一步被 `_send_message_impl` 静默丢弃。

**现象 B（维护者观察，无原始日志）**：bot 回复了一句话之后，紧接着自己选择退出对话（`go_quiet`），群友看来像"说一半跑了"。

## 2. 根因（三个独立问题，同属"bot 莫名沉默/退出"家族）

### 2.1 执行层可否决判定层（现象 A 主因）

`group_executor.py` 的 prompt（L268-274）对**所有模式**无条件提供沉默标记菜单：

```
- 不想回话就只输出 [SILENT]（无其他文字），下次有人说话你还可以接
- 觉得话题跟你完全没关系了就输出 [QUIET]（无其他文字），之后不再被叫到就不说话
```

mention 分支（L196-198）没有任何豁免。而 `adapter.py:_send_message_impl`（L3302-3322）收到 `[QUIET]`/`[SILENT]` 即吞掉整条内容并执行 `go_quiet()`/`record_silent()`，不区分本轮是否为用户直接 @ 的硬义务轮次。

**架构层面的问题**：`BUGS_v0.14.4.md` 的行为契约保证了"@ 必进 judge、必判必派"，但契约只覆盖到判定层。执行层（agent 输出）仍握有沉默否决权，判定层的"该回"决策在执行层被单点推翻。

### 2.2 子串匹配吞掉混合内容（独立 bug）

`adapter.py` L3302 是子串匹配：`if content and "[QUIET]" in content:` → **吞掉整条**。

模型输出 `我先撤了哈，你们聊 [QUIET]` 时，正文被一并吞掉，群里什么都看不到。`[SILENT]` 同理（L3313）。prompt 只写了"无其他文字"，对模型没有强约束，"正文+标记尾巴"是常见输出形态。

### 2.3 exiting 自我强化循环（现象 B 最可疑通路）

`semantic_judge.py` 中存在一条反馈链：

```
bot 回复带软收尾措辞（"你们聊"风格，礼貌型人设极易产出）
  → post_reply_recorder 判 episode_phase = "exiting"        (L1222-1226 区域)
  → 下一轮 judge：phase=exiting → should_reply 门槛大幅提高   (L323)
  → 门槛提高 → 弱指向消息进不来 → "仍无新指向消息"成立
  → should_exit = true                                        (L393)
  → go_quiet（或 exit_farewell 说句告别再退）
```

**bot 被自己上一轮的客套话送走了**——没有任何人赶它。状态机把 bot 自己的措辞当成了退出信号，且 exiting 状态没有复位机制，单向棘轮。

---

## 3. 设计原则（新架构契约）

| # | 原则 | 含义 |
|---|---|---|
| P1 | **判定层决策"说不说"，执行层决策"说什么"** | judge/trigger_coordinator 放行进执行层的轮次，执行层不再拥有沉默否决权 |
| P2 | **沉默标记从执行层退役** | 非 exit 轮的 prompt 不再提供 [SILENT]/[QUIET] 菜单；[QUIET] 仅在 exit 轮作为"说完正式退席"的状态信号保留 |
| P3 | **执行层输出契约：每轮必须产出可见文本** | 违约（纯标记/空回复）→ 同会话反馈重试一次 → 仍违约 → 放弃 + 告警日志 |
| P4 | **不写死用户可见兜底文案** | 违约重试利用 agent 会话的多轮能力自然完成，人设由 SOUL 保证；内部系统反馈是不可见的契约常量，重试失败允许不发（此时 LLM 大概率异常，发什么都是噪音） |

统一表述：**能进入执行层的轮次都是判定层放行的（mention 是硬义务直派，judge/attentive/continuation 是 judge 判 `should_reply=true` 放行），执行层一律要求可见文本输出。exit 轮同样要求可见正文（"说最后一句"），仅允许"正文 + [QUIET]"的合法组合。**

### 为什么不用"兜底文案"类方案

评估过三个替代方案，均因"为特例造专用机制"被否决：

- A. 整轮重跑 agent：成本高（实报案例一轮 53s + 22 次工具调用，重跑翻倍）
- B. 专用补写 LLM 调用：新增一条特殊代码路径和专用 prompt，人设一致性仍需显式拼装
- C. 预生成应答池：启动时生成、随机取用——重复露馅，且本质仍是预制文案

反馈重试方案复用 agent 会话既有的多轮机制（与工具调用循环同构），零新增 prompt 文件、零用户可见硬编码文案，对任何"输出不可见"违约普适。

---

## 4. 改动规格

### 4.1 `adapter.py` — 剥离语义重构（替换 L3302-3322 两个 if 块）

在 adapter 的统一 `send()` 入口（再进入 `_send_message_impl`，且**先于**内部泄漏检查和 `[reply:xxx]` 提取）统一剥离。下面是与当前实现一致的缩略伪代码；关键顺序是：**先归一化，再尝试发送，最后仅在发送成功时提交状态**：

```python
_QUIET_RE = re.compile(r'\s*\[(QUIET|SILENT)\]\s*', re.IGNORECASE)

body, markers = self._normalize_control_markers(content)
_gid = chat_id.split(":", 1)[1] if chat_id.startswith("group:") else ""
_nonce = self._group_turn_context.get(_gid) if _gid else None

if markers and not body:
    # 纯标记：不发送。已注册执行轮次延迟状态，交 executor 的输出契约校验。
    result = SendResult(success=True, message_id=None)
    self._apply_control_marker_state(
        _gid, markers, body, result, defer_pure=bool(_nonce)
    )
else:
    # 混合内容：剥离标记，正文继续走正常发送流程。
    result = await self._send_message_impl(chat_id, body, reply_to, metadata)
    # 标记只驱动状态，不再决定发不发；失败时不得提交 quiet。
    self._apply_control_marker_state(
        _gid, markers, body, result, defer_pure=bool(_nonce)
    )

# completion 使用 body（而不是带标记的原始 content），供 executor/recorder 消费。
if _nonce:
    self._resolve_group_send(
        _gid,
        body,
        nonce=_nonce,
        completion=self._completion_for_delivery(
            gid=_gid, body=body, markers=markers, result=result,
        ),
    )
```

**语义对照表**：

| 场景 | 正文 | 状态动作 | 发送 |
|---|---|---|---|
| 纯 `[QUIET]` | 空 | `go_quiet()` | 不发（交 executor 契约校验） |
| 正文 + `[QUIET]` | 非空 | `go_quiet()` | **发送正文**（"说完这句再退"） |
| 纯 `[SILENT]` | 空 | `record_silent()` | 不发（交 executor 契约校验） |
| 正文 + `[SILENT]` | 非空 | 不 record_silent（说了话就不算 silent） | **发送正文** |
| 无标记 | — | — | 现有逻辑不动 |

**关键变化**：标记从"吞掉整条的指令"降级为"状态信号"；发送决策只看剥离后有无正文。adapter 不需要知道本轮是什么模式——契约校验全部在 executor（4.2），两组件间零新增耦合。

### 4.2 `group_executor.py` — prompt 菜单重构 + 输出契约校验

#### (a) prompt 菜单重构（L268-274）

- **非 exit 轮**：移除 [SILENT]/[QUIET] 两行菜单，保留 `[reply:消息ID]` 和 `[搜索历史]` 说明。菜单位置插入一段通用契约说明（所有非 exit 模式同一段，不按模式分支）：

  ```
  [标记] 想引用某条消息就在回复里用 [reply:消息ID]。
  本轮你已被叫到，必须给出大家能看到的回复——沉默不是可选项。
  ```

- **exit 轮**（L205-208 分支）：保留 [QUIET] 但改措辞：

  ```
  说完最后一句后在末尾附 [QUIET] 表示正式退席。不能只有标记没有话。
  ```

#### (b) 输出契约校验 + 会话内反馈重试（`_run_turn`，L133 `_run_agent_locked` 返回后）

```python
outcome = await self._run_agent_locked(event)
if not (outcome.reply_text or "").strip():
    # 输出契约违约：执行层轮次必须产出可见文本（沉默决策权在判定层）。
    # 重试复用 agent 会话的多轮能力——与工具调用循环同构，不是新增代码路径。
    logger.info("[GroupExecutor] empty output (contract violation), in-session retry, group=%s mode=%s",
                gid, request.mode)
    outcome = await self._run_agent_locked(
        event.with_system_note(
            "[系统] 你的上一条回复没有任何可见内容。本轮你必须给出大家能看到的回复，"
            "请直接用你的语气输出要说的话。"
        )
    )
    if not (outcome.reply_text or "").strip():
        logger.warning("[GroupExecutor] output contract violated twice, giving up, group=%s mode=%s",
                       gid, request.mode)
```

**实现约束（ MUST ）**：

1. 重试**最多 1 次**——局部变量控制即可，不需要持久状态，天然防循环。
2. 重试必须在**同一会话**内进行：违约轮的原始输出（哪怕只是 `[QUIET]`）必须保留在会话历史中，反馈消息追加其后，让模型看到完整上下文自我纠正。实现时确认 `base.handle_message` 的历史写入时机；若空输出不入历史，反馈消息单独出现也可接受（系统消息本身已说明违约事实）。
3. 重试不得强制重跑工具循环——反馈消息只要求"直接文本回答"，是否调工具由模型自行决定（通常不需要）。
4. `event.with_system_note` 是示意 API——按 base.py 实际的事件构造方式实现（如重新构造带附加系统注记的 event，或会话追加接口）。
5. exit 轮同样走此校验（exit 的契约也是"必须有正文"）。

#### (c) `_apply_outcome` 适配（L335-345）

重试仍违约放弃的轮次（reply_text 为空）：走 `record_silent()` 路径，与现状的 silent 语义衔接，不新增 outcome kind。

### 4.3 `semantic_judge.py` — exiting 自我强化循环断链

三处改动，全部围绕"exiting 必须是高置信信号"：

#### (a) recorder 判定收紧（post_reply_recorder prompt，L1222-1226 区域）

exiting 只认**明确离场声明**级别："拜拜""先走了""不打扰了""我去忙了"等。
"你们聊""哈哈你们继续"式**软收尾最多记 `winding_down`，不记 exiting**。
prompt 中补一条边界："bot 的客套/谦让措辞不等于离场意图"。

#### (b) exiting 状态复位机制（judge prompt + trigger_coordinator 双保险）

现状：exiting 只进不出（L393 把"无新指向"当退出条件，但 phase 本身永不复位）。
改动：任何新的**指向 bot** 的消息（@、直呼名字、QQ 回复 bot 的消息）出现时，`episode_phase` 立即从 exiting 复位为 mid——

- prompt 层：judge 规则中写明"出现新的指向你的消息时，episode_phase 不再沿用 exiting"；
- 代码层：`trigger_coordinator.on_ingested` 检测到 @ / 名字直呼时直接复位 `gs.episode_state.episode_phase`（mention 快速通道和 name-referral 分支 L133-149 均已有现成的检测点）。

#### (c) should_exit 增加持续条件（judge prompt，L393 区域）

现状：上轮 phase=exiting + 本轮无指向 → should_exit，单轮即可触发。
改动：exiting 状态需**持续 ≥2 个判定轮且无新指向消息**才允许 `should_exit=true`，防止单轮误触发。

---

## 5. 行为对照表

| 场景 | 改动前 | 改动后 |
|---|---|---|
| @ 后模型输出纯 `[QUIET]` | 吞掉，群里无回复 | 反馈重试 → 正常回复；重试仍违约 → 放弃 + 告警 |
| @ 后模型输出"正文 + [QUIET]" | 整条吞掉 | 发送正文 + `go_quiet()` |
| judge 放行轮模型输出纯 `[SILENT]` | 吞掉，record_silent | 反馈重试 → 正常回复 |
| exit 轮输出"告别 + [QUIET]" | 整条吞掉（告别丢失，但仍 quiet） | 发送告别 + `go_quiet()` |
| exit 轮输出纯 `[QUIET]` | 吞掉 + quiet（最后一句没说） | 反馈重试要求说出最后一句 |
| bot 客套"你们聊"后被自动送走 | 单向棘轮，必退 | 软收尾不再触发 exiting；指向消息即时复位；exit 需持续 2 轮 |

## 6. 升级逻辑

### 6.1 文件双写

OneBot 行为文件位于 `core/plugins/platforms/onebot/`；Gateway/AIAgent 的临时反馈边界位于 `core/gateway/run.py` 和 `core/run_agent.py`。`extras/scripts/upgrade.py` 的 UPGRADE_MAP 已逐项覆盖这些文件（双写 `~/.hermes/` 去前缀路径 + BOT_DIR），其中 `run_agent.py` 已在本次部署切片补齐。**升级说明必须点名六个影响文件，不能再用“四文件”概括。**

### 6.2 版本与更新日志

- 并入 Codex 主导的下个大版本，不单发补丁版。
- `CHANGELOG.md` 条目草稿（中性描述）：

  ```
  - 修复：@ 机器人后可能无回复（执行层沉默标记吞掉回复，判定层决策被单点推翻）
  - 修复：回复正文混合沉默标记时整条被丢弃
  - 修复：bot 回复客套收尾措辞后可能被状态机自动判定退出对话
  - 变更：执行层不再提供沉默标记菜单——"说不说"由判定层统一决策，判定放行的轮次必定给出可见回复
  ```

### 6.3 BUGS 行为约定增补

在 `docs/BUGS_v0.14.4.md` 的行为约定一节追加（防回归）：

```
- 执行层输出契约：进入执行层的轮次必须产出可见文本；沉默决策权归判定层，
  执行层不提供沉默标记菜单，违约走会话内反馈重试（最多一次）
- exiting 单向棘轮禁止：bot 自身措辞不得作为退出信号的唯一依据；
  指向消息必须即时复位 exiting
```

### 6.4 音量变化说明（用户预期管理，写进 UPGRADE.md）

改动后 judge 判 `should_reply=true` 的轮次**必定发言**（原来执行层还可能用沉默标记二次否决）。bot 的话量会略有增加，这是设计意图——"说不说"的决策质量现在完全由 judge 层负责。若用户觉得变吵，调优方向是 judge 层（判定门槛/prompt），而不是恢复执行层沉默标记。

## 7. 回归测试清单

新增至 `core/tests/gateway/test_onebot_runtime_regressions.py`（或同目录新文件）：

1. mention 轮 + 模型输出纯 `[QUIET]` → 触发反馈重试 → 最终发送非空回复
2. mention 轮 + 重试仍纯标记 → 放弃，不发送，warning 日志，record_silent 路径
3. judge 轮 + 纯 `[SILENT]` → 反馈重试
4. exit 轮 + "正文 + [QUIET]" → 发送正文 + go_quiet 生效
5. exit 轮 + 纯 `[QUIET]` → 反馈重试（exit 也要求正文）
6. 混合内容：judge 轮 + "正文 + [SILENT]" → 发送正文，不 record_silent
7. 重试最多一次：构造连续违约，验证第三次调用不存在
8. exiting 断链：recorder 输入软收尾措辞 → phase 不得为 exiting；输入明确告别 → phase=exiting；exiting 后出现指向消息 → phase 复位
9. should_exit 持续条件：exiting 单轮无指向 → should_exit=false；连续两轮无指向 → true
10. 非守卫路径不回归：无标记正常回复发送流程不变；`[reply:xxx]` 提取、内部泄漏拦截顺序不受剥离影响

## 8. 明确不做

- **不动** `gateway/platforms/base.py`（上游共享代码）——所有改动收口在 onebot 插件层
- **不新增**任何硬编码回复文案（兜底文案概念被反馈重试机制取代）
- **不保留**执行层非 exit 轮的沉默标记菜单（不做"模式分支式"prompt 特判）
- **不改动** v0.14.4 的 judge/mention 强信号契约（@ 必进 judge、必判必派、不排队不覆盖）；允许在 `semantic_judge.py` 增加 exiting 计数/结果清洗门禁，但不能用它削弱 @ 放行语义
- **不删除** `record_silent`/`go_quiet` 状态方法（exit 轮和重试放弃路径仍在使用）
- 用户侧调优（会话重置、reasoning_effort、API 配额）不进代码——见第 9 节

## 9. 用户侧建议（不进代码，给部署方的运维指引）

1. **长会话重置**：实报案例中会话积累 214 条历史，单轮 22 次工具调用、53s 延迟，且长历史会强化模型的"旁观"锚定。建议用 `/new` 重置长会话，预期延迟回落至 5-15s。
2. **reasoning_effort**：`xhigh` 档对轻量模型偏重，过度推理后模型倾向选择省事的沉默标记。建议 `medium`/`high`。
3. **API 配额**：实报案例曾出现周用量耗尽（429），加剧无回复表象。关注供应商配额余量。
4. 升级本修复后无需迁移配置；若曾对本目录文件打过本地补丁（如 dispatch 超时、端口适配），升级前自行备份 diff。

## 10. Codex 实施前接口补充（根代理审查）

本规格的策略和用户可见行为确认；下面是根据当前工作树接口补上的硬门禁，不能按“示意 API”忽略。

### 10.1 输出完成信号必须按单次处理关联

- 当前 `GroupExecutor._run_agent_locked` 只等待 OneBot adapter 的 `_group_send_results[group_id]`。Gateway 的空 `final_response` 可能不调用 `send()`，这时只会等到超时，不能把超时误当成已完成的空回复。实现必须提供按单次处理关联的完成结果，至少区分 `completed`、`timed_out`、`interrupted`、`delivery_text` 和 `normalized_text`。
- `_group_send_results` 现在按群覆盖；反馈重试不能覆盖仍在收尾的旧 future，也不能让别的状态消息唤醒本轮。需要 turn nonce/队列或等价的单次关联，并新增并发回归。
- 当前 `_run_agent_locked` 即使 `reply_text` 为空也返回 `AgentOutcome(kind="sent")`，而 `_apply_outcome` 会把它当作一次成功回复；重试仍违约时必须沿用现有 `silent`/`failed` 语义，不能调用 `record_reply()` 或伪造成功交付。
- 第一次处理的后台任务必须完成并释放会话所有权后才能启动反馈重试。直接在旧任务仍持有 base session guard 时再次调用 `adapter.handle_message()`，会被当前 busy/interrupt 路径当成新的用户消息，触发中断、ack 或排队，可能形成死等和重复用户回合。
- “同一会话”不等于重复追加原始用户消息。`MessageEvent` 当前没有 `with_system_note`，也没有被 Gateway 消费的 `system_note` 字段；实现需明确采用现有 `channel_prompt`/steer 或新增 onebot→Gateway 的最小内部契约，并证明反馈不会以普通 user 文本污染 transcript、MemoryProvider 或 SessionDB。除非测试证明必要，不修改 `gateway/platforms/base.py`。
- 如果完成信号或反馈注记最终需要改 `gateway/run.py`（例如让空 response 也发布 turn-complete，或支持不持久化的内部反馈），必须把它加入影响文件、`UPGRADE_MAP` 和回滚清单；不能为了维持“四文件”叙述而在 adapter 中偷偷复制 Gateway 会话逻辑。

### 10.2 adapter 清洗结果必须向上游可见

- 当前 `OneBotAdapter.send()` 在 `_send_message_impl()` 返回后仍用原始 `content` 解析 `_group_send_results`。如果只在 `_send_message_impl` 的局部变量中剥离标记，`正文 + [QUIET]` 会实际发送正文，却把带标记的原文交给 executor/recorder。实现必须让完成结果携带剥离后的正文，或在 adapter 层统一归一化后再 resolve；测试必须断言 recorder 看不到控制标记。
- `QUIET`/`SILENT` 的状态动作要和发送结果绑定：正文发送失败时不能提前把会话不可逆地置为 quiet；纯标记进入反馈重试期间也不能阻止重试。若保留 adapter 无模式解析，需在文档和测试中明确“非 exit 轮正文+标记”的兼容语义，避免与“标记只在 exit prompt 出现”混淆。
- 标记解析必须有边界测试（大小写、空白、多标记、标记出现在引用/代码文本中、正文只剩空白），并保持 `[reply:...]` 提取、内部泄漏拦截和多行发送顺序。
- 当前实际 `TriggerRequest` 只产生 `mention`、`judge`、`continuation` 和 `exit`；规格中的 `attentive` 作为行为分类即可，不要新增没有生产调用方的 phantom mode，避免契约覆盖范围和测试假设漂移。

### 10.3 exiting 的“两轮”必须有代码状态，不可只写 prompt

- LLM prompt 不能可靠地知道 `exiting` 已持续了几轮。需要在 `GroupState`/`EpisodeState` 或 coordinator 中保存有界的 `exiting_streak`（或等价计数），序列化并在每次新指向时清零；在计数未达到 2 前，代码层必须强制 `should_exit=false`，不能只依赖模型自报。
- 复位覆盖三类真实指向：直接 @、SOUL 别名直呼、QQ reply 目标是 bot。当前 trigger 已对直接 @ 做 phase reset，但名字直呼和 reply 目标需要独立覆盖；复位应发生在提交 judge/执行前，避免旧 phase 影响门槛。
- recorder 的 prompt 收紧之外，还要有结果边界：bot 的“你们聊/你们继续”软收尾不得单凭模型输出把 phase 提升为 `exiting`；非法/矛盾的 recorder JSON 应回退到上一状态或 `winding_down`。明确告别仍可进入 exiting。
- 现有 `_run_loop` 在 `episode_phase == "exiting"` 时会停止后续 continuation；实现必须验证“复位发生在 continuation 判断之前”，否则新指向虽然改了状态仍可能被旧分支丢弃。

### 10.4 回归门禁扩展

在第 7 节 10 条之外，至少补充：

11. 空 `final_response` 不调用 adapter.send 时，完成信号仍能结束本轮并只重试一次。
12. 旧任务收尾期间到达的真实用户消息不会被反馈重试覆盖、合并或重复发送。
13. 清洗后的正文同时用于 OneBot 发送、GroupExecutor outcome、episode recorder 和 bot buffer。
14. 发送失败时 `QUIET` 状态不提前提交；重试成功后的状态和正文保持一致。
15. `exiting_streak=0/1/2` 的代码门禁分别验证；@、名字直呼、reply-to-bot 各自复位。

完成这些门禁后，才把本规格从“已实施候选”改为“已实现”；不得把只通过 prompt 快照或纯 mock 的测试当成真实会话重试证据。
