# v0.10.6 升级指南

## 概述

本次版本重构了消息处理架构，从单线程群锁改为三阶段流水线，延迟降低 40-70%。新增 3 个模块，重写 4 个文件。

## 架构变更

```
旧: 群锁锁住一切（buffer + persist + 图片 + judge + agent）= 串行
新: Phase 1 并发摄入（semaphore 20）
    Phase 2 决策判定（trigger_coordinator，1s timer + judge）
    Phase 3 串行执行（group_executor，群锁 + agent + 摘要 + 连续对话）
```

## 新增文件

| 文件 | 职责 | 行数 |
|------|------|------|
| `media_pipeline.py` | 图片 task 所有权管理，按 seq 注册，cancel_all() | 111 |
| `trigger_coordinator.py` | Phase 2 决策层，judge 生命周期，mention deadline-batch | 238 |
| `group_executor.py` | Phase 3 single-flight runner，群锁 + 摘要 + 连续对话 | 258 |

## 重写文件

| 文件 | 改动 |
|------|------|
| `adapter.py` | _process_message_impl 774->~100 行，删除 6 个旧方法，新增 _process_message_bounded + _schedule_group_run |
| `group_state.py` | seq 号体系（next_seq/last_user_seq/last_consumed_seq/last_judged_seq），decision_epoch 废弃，snapshot() 不可变快照，prune() 自动修剪 |
| `semantic_judge.py` | fail-closed（缺 key/超时 -> should_reply=False），独立 semaphore（judge=16/summary=8），schema 校验 |
| `bandori_sync.py` | RSS feed 解析（news/events/releases），11 支优先乐队，100 页/轮 |

## .env 新增

```bash
OBSIDIAN_VAULT_PATH=/path/to/your/knowledge  # Obsidian 知识库路径
```

## 升级步骤

1. 停止 Gateway
2. 替换以下文件（从 v0.10.6 release 下载）:
   - `hermes/plugins/platforms/onebot/adapter.py`
   - `hermes/plugins/platforms/onebot/group_state.py`
   - `hermes/plugins/platforms/onebot/semantic_judge.py`
   - `hermes/plugins/platforms/onebot/media_pipeline.py` (新)
   - `hermes/plugins/platforms/onebot/trigger_coordinator.py` (新)
   - `hermes/plugins/platforms/onebot/group_executor.py` (新)
   - `hermes/scripts/bandori_sync.py`
3. 在 `~/.hermes/.env` 添加 `OBSIDIAN_VAULT_PATH`（如使用知识库）
4. 重启 Gateway

## 延迟对比

| 场景 | 旧 (v0.10.5) | 新 (v0.10.6) |
|------|-------------|-------------|
| 对话态 | 19s | 10s |
| @mentioned | 19s | 10s |
| 潜水态 | 19s | ~12s |
| 50条刷屏 | 450s | ~12s |

## 注意事项

- `group_state.py` 的 seq 体系是全新字段，旧 state.db 不影响（字段自动初始化）
- `semantic_judge.py` 改为 fail-closed：缺 API key 时只 @ 才回复（不再无差别回复）
- `media_pipeline.py` 的图片描述 prompt 改为情绪意图判断（"害羞"/"得意"等 1-3 词）
- `bandori_sync.py` 的 RSS 拉取需要网络访问 bandori.fans
