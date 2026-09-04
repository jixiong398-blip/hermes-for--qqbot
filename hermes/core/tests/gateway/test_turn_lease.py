"""Concurrency and ownership contracts for the staged turn-lease registry."""

from __future__ import annotations

import asyncio

import pytest

from gateway.turn_lease import (
    SessionTurnLeaseRegistry,
    TurnLeaseTimeoutError,
)


def run(coro):
    return asyncio.run(coro)


def test_empty_session_id_is_not_registered():
    async def scenario():
        registry = SessionTurnLeaseRegistry()
        assert await registry.acquire("", owner_key="key", generation=1) is None
        assert len(registry) == 0

    run(scenario())


def test_acquire_release_is_identity_checked_and_idempotent():
    async def scenario():
        registry = SessionTurnLeaseRegistry()
        token = await registry.acquire("sid", owner_key="key", generation=1)
        assert token is not None
        assert registry.release(token) is True
        assert registry.release(token) is False
        assert token.released is True

    run(scenario())


def test_same_session_waiter_is_serialized_until_holder_releases():
    async def scenario():
        registry = SessionTurnLeaseRegistry()
        first = await registry.acquire("sid", owner_key="first", generation=1)
        waiter = asyncio.create_task(
            registry.acquire("sid", owner_key="second", generation=2, timeout=1)
        )
        await asyncio.sleep(0.01)
        assert waiter.done() is False
        assert registry.release(first) is True
        second = await waiter
        assert second is not None
        assert second.owner_key == "second"
        registry.release(second)

    run(scenario())


def test_timeout_fails_closed_and_keeps_holder():
    async def scenario():
        registry = SessionTurnLeaseRegistry()
        first = await registry.acquire("sid", owner_key="first", generation=1)
        with pytest.raises(TurnLeaseTimeoutError) as caught:
            await registry.acquire("sid", owner_key="second", generation=2, timeout=0.01)
        assert caught.value.session_id == "sid"
        assert caught.value.owner_key == "second"
        assert registry.release(first) is True

    run(scenario())


def test_rebind_shares_the_same_lock_and_updates_token_identity():
    async def scenario():
        registry = SessionTurnLeaseRegistry()
        token = await registry.acquire("old", owner_key="key", generation=1)
        assert registry.rebind(token, "new") is True
        waiter = asyncio.create_task(
            registry.acquire("new", owner_key="other", generation=2, timeout=1)
        )
        await asyncio.sleep(0.01)
        assert waiter.done() is False
        assert token.session_id == "new"
        registry.release(token)
        replacement = await waiter
        assert replacement is not None
        registry.release(replacement)

    run(scenario())


def test_rebind_rejects_a_live_target_lease():
    async def scenario():
        registry = SessionTurnLeaseRegistry()
        source = await registry.acquire("source", owner_key="source", generation=1)
        target = await registry.acquire("target", owner_key="target", generation=2)
        assert registry.rebind(source, "target") is False
        assert source.session_id == "source"
        registry.release(source)
        registry.release(target)

    run(scenario())


def test_idle_entries_are_evicted_but_live_entries_are_preserved():
    async def scenario():
        registry = SessionTurnLeaseRegistry(max_entries=2)
        first = await registry.acquire("first", owner_key="first", generation=1)
        registry.release(first)
        second = await registry.acquire("second", owner_key="second", generation=2)
        third = await registry.acquire("third", owner_key="third", generation=3)
        assert third is not None
        assert len(registry) == 2
        # The held third lease cannot be evicted while a new entry is created.
        fourth = await registry.acquire("fourth", owner_key="fourth", generation=4)
        assert fourth is not None
        assert len(registry) >= 2
        registry.release(second)
        registry.release(third)
        registry.release(fourth)

    run(scenario())
