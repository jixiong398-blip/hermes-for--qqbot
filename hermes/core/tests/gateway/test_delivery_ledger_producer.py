"""Final platform sends publish durable delivery-obligation checkpoints."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, SendResult
from gateway.session import SessionSource


class _FakeAdapter(BasePlatformAdapter):
    def __init__(self, result: SendResult):
        # Bypass the heavyweight base constructor; this test targets the
        # ledger wrapper and supplies only the attributes it reads.
        self.platform = Platform("onebot")
        self._delivery_ledger_enabled = True
        self.result = result
        self.send_calls = []

    async def send(self, *args, **kwargs):
        return self.result

    async def connect(self):
        return True

    async def disconnect(self):
        return None

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}

    async def _send_with_retry(self, **kwargs):
        self.send_calls.append(kwargs)
        return self.result

    async def send_typing(self, *args, **kwargs):
        return None

    async def cancel_background_tasks(self):
        return None


def _event():
    source = SessionSource(
        platform=Platform("onebot"),
        chat_id="group:42",
        user_id="user-7",
        chat_type="group",
    )
    return MessageEvent(text="question", source=source, message_id="message-1")


@pytest.mark.asyncio
async def test_final_send_records_attempt_and_marks_delivered(monkeypatch):
    from gateway import delivery_ledger as ledger

    calls = []
    monkeypatch.setattr(ledger, "record_obligation", lambda **kwargs: calls.append(("record", kwargs)))
    monkeypatch.setattr(ledger, "mark_attempting", lambda oid: calls.append(("attempting", oid)))
    monkeypatch.setattr(ledger, "mark_delivered", lambda oid: calls.append(("delivered", oid)))
    monkeypatch.setattr(ledger, "compute_obligation_id", lambda *args: "obligation-1")

    adapter = _FakeAdapter(SendResult(success=True, message_id="sent-1"))
    result = await adapter._send_final_with_delivery_ledger(
        event=_event(),
        session_key="onebot:group:42",
        content="final answer",
        reply_to="message-0",
        metadata={"thread_id": "topic-1"},
    )

    assert result.success is True
    assert [call[0] for call in calls] == ["record", "attempting", "delivered"]
    assert calls[0][1]["session_key"] == "onebot:group:42"
    assert calls[0][1]["platform"] == "onebot"
    assert calls[0][1]["thread_id"] == "topic-1"
    assert adapter.send_calls[0]["content"] == "final answer"


@pytest.mark.asyncio
async def test_failed_retryable_send_is_marked_degraded(monkeypatch):
    from gateway import delivery_ledger as ledger

    calls = []
    monkeypatch.setattr(ledger, "record_obligation", lambda **kwargs: calls.append(("record", kwargs)))
    monkeypatch.setattr(ledger, "mark_attempting", lambda oid: calls.append(("attempting", oid)))
    monkeypatch.setattr(ledger, "mark_failed", lambda oid, error: calls.append(("failed", oid, error)))
    monkeypatch.setattr(ledger, "compute_obligation_id", lambda *args: "obligation-2")

    adapter = _FakeAdapter(
        SendResult(success=False, error="Not connected", retryable=True)
    )
    result = await adapter._send_final_with_delivery_ledger(
        event=_event(),
        session_key="onebot:group:42",
        content="final answer",
        reply_to=None,
        metadata=None,
    )

    assert result.success is False
    assert calls[-1] == ("failed", "obligation-2", "Not connected")


@pytest.mark.asyncio
async def test_ledger_failure_does_not_block_final_send(monkeypatch):
    from gateway import delivery_ledger as ledger

    def fail_record(**kwargs):
        raise OSError("state db temporarily unavailable")

    monkeypatch.setattr(ledger, "record_obligation", fail_record)
    adapter = _FakeAdapter(SendResult(success=True, message_id="sent-2"))

    result = await adapter._send_final_with_delivery_ledger(
        event=_event(),
        session_key="onebot:group:42",
        content="final answer",
        reply_to=None,
        metadata=None,
    )

    assert result.success is True
    assert len(adapter.send_calls) == 1


@pytest.mark.asyncio
async def test_disabled_ledger_preserves_direct_retry_call(monkeypatch):
    adapter = _FakeAdapter(SendResult(success=True))
    adapter._delivery_ledger_enabled = False

    result = await adapter._send_final_with_delivery_ledger(
        event=_event(),
        session_key="onebot:group:42",
        content="final answer",
        reply_to=None,
        metadata=None,
    )

    assert result.success is True
    assert len(adapter.send_calls) == 1


@pytest.mark.asyncio
async def test_gateway_recovery_sends_only_to_connected_platform(monkeypatch):
    from gateway import delivery_ledger as ledger
    from gateway.run import GatewayRunner

    adapter = MagicMock()
    adapter.is_connected = True
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="sent"))
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.adapters = {Platform("onebot"): adapter}

    monkeypatch.setattr(ledger, "ledger_enabled", lambda: True)
    monkeypatch.setattr(
        ledger,
        "sweep_recoverable",
        lambda **kwargs: [
            {
                "obligation_id": "ob-1",
                "platform": "onebot",
                "chat_id": "group:42",
                "thread_id": "topic-1",
                "content": "reply",
                "needs_marker": True,
                "marker": "RECOVERED: ",
            }
        ],
    )
    delivered = []
    monkeypatch.setattr(ledger, "mark_delivered", delivered.append)

    assert await runner._recover_delivery_obligations() == 1
    adapter.send.assert_awaited_once_with(
        "group:42",
        "RECOVERED: reply",
        metadata={"delivery_recovery": True, "thread_id": "topic-1"},
    )
    assert delivered == ["ob-1"]


@pytest.mark.asyncio
async def test_gateway_recovery_does_not_claim_when_no_adapter_is_connected(monkeypatch):
    from gateway import delivery_ledger as ledger
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    adapter = MagicMock()
    adapter.is_connected = False
    runner.adapters = {Platform("onebot"): adapter}
    sweep = MagicMock()
    monkeypatch.setattr(ledger, "ledger_enabled", lambda: True)
    monkeypatch.setattr(ledger, "sweep_recoverable", sweep)

    assert await runner._recover_delivery_obligations() == 0
    sweep.assert_not_called()
