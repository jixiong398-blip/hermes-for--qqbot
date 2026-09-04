"""GatewayRunner wrapper contract for the staged per-session lease."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from gateway.run import GatewayRunner
from gateway.turn_lease import SessionTurnLeaseRegistry


def run(coro):
    return asyncio.run(coro)


def _runner(entry, implementation, *, registry=None):
    runner = object.__new__(GatewayRunner)
    runner.session_store = SimpleNamespace(
        get_or_create_session=lambda _source: entry,
    )
    runner._turn_lease_registry = registry
    runner._turn_lease_generations = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._busy_ack_ts = {}
    runner._handle_message_with_agent_impl = implementation
    return runner


def test_wrapper_acquires_before_impl_and_releases_after_success():
    async def scenario():
        entry = SimpleNamespace(session_id="durable", session_key="key")
        calls = []

        async def implementation(event, source, quick_key, **kwargs):
            calls.append((event, source, quick_key, kwargs))
            token = kwargs["_turn_lease_token"]
            assert token.session_id == "durable"
            assert kwargs["_session_entry_override"] is entry
            return "ok"

        registry = SessionTurnLeaseRegistry()
        runner = _runner(entry, implementation, registry=registry)
        result = await runner._handle_message_with_agent("event", "source", "route")

        assert result == "ok"
        assert len(calls) == 1
        assert len(registry) == 1
        assert runner._turn_lease_generations == {"route": 1}
        runner._release_running_agent_state("route")
        assert runner._turn_lease_generations == {}
        # The token was released even though the registry retains idle entries.
        follow_up = await registry.acquire(
            "durable", owner_key="follow-up", generation=2, timeout=0.1
        )
        assert follow_up is not None
        registry.release(follow_up)

    run(scenario())


def test_wrapper_releases_lease_when_impl_raises():
    async def scenario():
        entry = SimpleNamespace(session_id="durable", session_key="route")

        async def implementation(*_args, **_kwargs):
            raise RuntimeError("implementation failure")

        registry = SessionTurnLeaseRegistry()
        runner = _runner(entry, implementation, registry=registry)
        with pytest.raises(RuntimeError):
            await runner._handle_message_with_agent("event", "source", "route")

        token = await registry.acquire(
            "durable", owner_key="after-error", generation=2, timeout=0.1
        )
        assert token is not None
        registry.release(token)

    run(scenario())


def test_wrapper_fails_closed_on_lease_timeout_without_calling_impl():
    async def scenario():
        entry = SimpleNamespace(session_id="durable", session_key="route")
        called = False

        async def implementation(*_args, **_kwargs):
            nonlocal called
            called = True
            return "unexpected"

        registry = SessionTurnLeaseRegistry()
        holder = await registry.acquire(
            "durable", owner_key="holder", generation=1, timeout=0.1
        )
        runner = _runner(entry, implementation, registry=registry)
        old_timeout = __import__("os").environ.get("HERMES_TURN_LEASE_TIMEOUT")
        __import__("os").environ["HERMES_TURN_LEASE_TIMEOUT"] = "0.01"
        try:
            result = await runner._handle_message_with_agent("event", "source", "route")
        finally:
            if old_timeout is None:
                __import__("os").environ.pop("HERMES_TURN_LEASE_TIMEOUT", None)
            else:
                __import__("os").environ["HERMES_TURN_LEASE_TIMEOUT"] = old_timeout
            registry.release(holder)

        assert called is False
        assert "resend" in result.lower()

    run(scenario())
