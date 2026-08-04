# Bug 汇总 — v0.14.4：群内 @Soyo 不回复

## 现象

- 群聊中直接 @Soyo（如"介绍一下你自己"）有时不回复、延迟 20-40 秒才回、连续 @ 时只有最后一次生效。

## 根因（三层叠加）

### 1. judge 判定主体漂移
`trigger_coordinator._judge_worker` 的判定对象是 `latest_user`（窗口内最新消息）。
@ 消息到达后，若有表情包/闲聊插进窗口，judge 判的是**新消息**而不是 @ 消息本身，
LLM 看到"当前消息是表情包"就把 @ 请求淹没了。

### 2. 属性名错误（`jt.seq`）
`_JudgeTask` 字段为 `initial_seq`，pending 的 @ 消息 reschedule 后 target 定位引用
不存在的 `jt.seq` → 整个 judge 抛 `AttributeError`，回复静默丢失。

### 3. @ 排队 + pending 覆盖
- @ 消息到达时若 judge in-flight，被塞进 `pending_msg` 排队，最长阻塞 12s+；
- pending 只存一份：连续 @ 时**前面的 @ 被后面的覆盖丢失**。

## 修复

| 改动 | 文件 | 说明 |
|---|---|---|
| judge 主体 = @ 消息（按 `initial_seq` 定位 buffer，不漂移） | `trigger_coordinator._judge_worker` | @ 请求永远是判定对象 |
| @ 之后窗口内消息 → `follow_up` 背景字段 | `trigger_coordinator._invoke_judge` | 不替换判定主体 |
| prompt 增加 @ 强信号规则段 | `semantic_judge._build_pre_reply_judge_prompt` | 必须回应 @ 消息；后续相关则一并回，不相关则忽略 |
| @ 强信号不排队不覆盖：到达即取消 in-flight 普通判定、立即开自己的 judge（1s 窗口保留） | `trigger_coordinator.on_ingested` | 每次 @ 必判，互不覆盖 |
| `should_reply` 传递 `is_mention` 给 executor | `trigger_coordinator` | 回复携带 @ 标记 |

## 验证

- 单元验证：@ 消息 + 噪音切分（历史 seq<@ / 主体=@ / follow_up seq>@）通过
- 实群：@ → `reply=True`（reason: 明确@Soyo并呼唤，强正向指向）→ 发送成功
- 二次 @ 秒回；`py_compile` 通过；gateway 重启连接正常

## 影响文件

- `core/plugins/platforms/onebot/trigger_coordinator.py`
- `core/plugins/platforms/onebot/semantic_judge.py`

## 行为约定（防回归）

- @ 是**强信号**：必进 judge、必判 @ 消息自己、不排队、不覆盖
- 防抖窗口照常（attentive 1s / idle 5s / 退出倒计时 15s）
- 窗口内后续消息只是背景：相关可回，不相关忽略，不影响 @ 义务
