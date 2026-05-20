# OneBot Gateway 重连问题

> **最新状态 (2026-05-14): 已修复** — OneBot adapter `_ws_loop` 添加了自动重连逻辑。

## 现象（修复前）

NapCat 重启后（整点重启或手动重启），Hermes Gateway 的 OneBot 连接不会自动恢复。从 Gateway 日志看，OneBot adapter 表现为"仍处于已连接状态"，但实际已不工作。

## 根因（修复前）

OneBot adapter 的 `_ws_loop` 使用 `async for raw in self._ws` 监听 WebSocket 消息。当 NapCat 被杀死时：

1. `websockets.exceptions.ConnectionClosed` 异常被捕获（`adapter.py:290`）
2. `self._mark_disconnected()` 被调用
3. **方法直接返回，没有重连逻辑**
4. Gateway 没有收到断线通知（`_set_fatal_error` 未被调用），所以不对 OneBot 进行后台重连
5. NapCat 重新启动后，Gateway 仍处于"断线但没重试"的状态

日志证据：
```
# 最后一次 OneBot 连接成功（02:49）
2026-05-14 02:49:24,636 INFO gateway.run: ✓ onebot connected
# 之后 Gateway 持续运行，OneBot 再无活动
# 无 disconnected 记录（Gateway 认为连接仍在）
```

## 修复：`_ws_loop` 自动重连

修改文件：`/home/ji/.hermes/plugins/platforms/onebot/adapter.py`

### 修改内容

`_ws_loop()` 方法改为了带自动重连的 `while` 循环：

```python
async def _ws_loop(self) -> None:
    max_retry_delay = 60
    retry_delay = 5

    while not self._stopping:
        if not self._ws:
            return

        logger.info("[OneBot] WebSocket event loop started")
        try:
            async for raw in self._ws:
                # ... 处理消息 ...
        except websockets.exceptions.ConnectionClosed:
            logger.warning("[OneBot] WebSocket connection closed — will reconnect")
            self._mark_disconnected()
        except asyncio.CancelledError:
            logger.info("[OneBot] WebSocket loop cancelled (shutdown)")
            return
        except Exception as e:
            logger.error("[OneBot] Error in event loop: %s", e)
            self._mark_disconnected()

        if self._stopping:
            return

        # 自动重连，指数退避
        await asyncio.sleep(retry_delay)
        try:
            new_ws = await websockets.connect(self._ws_url, ...)
            self._ws = new_ws
            self._mark_connected()
            retry_delay = 5  # 成功则重置
            asyncio.create_task(self._recover_missed_messages())
        except Exception:
            retry_delay = min(retry_delay * 2, max_retry_delay)
```

### 配套修改

- `__init__`: 新增 `self._stopping = False`
- `disconnect()`: 在第一行设置 `self._stopping = True`，确保主动关闭时不会卡在重连循环

### 重连行为

| 条件 | 行为 |
|------|------|
| WebSocket 断线 | 立即标记断线，等 5 秒后尝试重连 |
| 重连失败 | 等待时间翻倍（10s → 20s → 40s → 60s cap） |
| 重连成功 | 重置退避为 5s，自动拉取断线期间遗漏的群消息 |
| 主动关闭 Gateway | `_stopping = True`，重连循环立即退出 |

## 手动验证

重启 Gateway 使改动生效：

```bash
hermes gateway restart
```

验证重连是否正常：

```bash
# 观察 Gateway 日志
tail -f ~/.hermes/logs/gateway.log | grep -a "OneBot"

# 预期输出：
# [OneBot] Connecting to WS ws://127.0.0.1:3001/onebot/v11/ws, HTTP http://127.0.0.1:3000
# [OneBot] Connected successfully
# [OneBot] WebSocket event loop started
```

模拟断线测试（需要 `kill` 其中一个端口进程的测试，暂不可行，只能等一小时后的自动重启验证）：
1. 等待 NapCat 整点重启
2. 观察 Gateway 日志是否出现 `[OneBot] Reconnecting in 5s...` → `[OneBot] Reconnected successfully`
