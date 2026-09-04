"""Per-session turn leases staged from upstream Hermes.

The local gateway currently guards routing keys.  This registry is a separate
capability port for the stronger invariant that two routing keys resolving to
the same durable ``session_id`` must not load, run, and flush concurrently.
It is intentionally not imported by ``gateway.run`` until the local session
resolution and compression-rebind paths have been reviewed together.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_LEASES = 512
DEFAULT_LEASE_WAIT = 1800.0
DEFAULT_DURABLE_LEASE_TTL = 300.0
DEFAULT_DURABLE_LEASE_TIMEOUT = 2.0
MAX_DURABLE_LEASE_TIMEOUT = 10.0


class TurnLeaseTimeoutError(TimeoutError):
    """The caller waited out its lease budget and was not admitted."""

    def __init__(
        self,
        session_id: str,
        *,
        owner_key: str,
        generation: int,
        wait_seconds: float,
    ) -> None:
        self.session_id = session_id
        self.owner_key = owner_key
        self.generation = generation
        self.wait_seconds = wait_seconds
        super().__init__(
            f"turn lease wait timed out after {wait_seconds:.0f}s on session "
            f"{session_id} for routing key {owner_key} (gen {generation})"
        )


class TurnLeaseToken:
    """Identity-checked handle for one held session lease."""

    __slots__ = ("session_id", "owner_key", "generation", "released")

    def __init__(self, session_id: str, owner_key: str, generation: int) -> None:
        self.session_id = session_id
        self.owner_key = owner_key
        self.generation = generation
        self.released = False

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"TurnLeaseToken(session_id={self.session_id!r}, "
            f"owner_key={self.owner_key!r}, generation={self.generation}, "
            f"released={self.released})"
        )


class _SessionLease:
    __slots__ = (
        "lock",
        "holder",
        "acquired_at",
        "last_used",
        "pending_acquires",
    )

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.holder: Optional[TurnLeaseToken] = None
        self.acquired_at = 0.0
        self.last_used = time.time()
        self.pending_acquires = 0

    @property
    def idle(self) -> bool:
        return (
            self.holder is None
            and not self.lock.locked()
            and self.pending_acquires == 0
        )


class SessionTurnLeaseRegistry:
    """Bounded asyncio registry keyed by resolved durable session id."""

    def __init__(self, max_entries: int = DEFAULT_MAX_LEASES) -> None:
        self._leases: Dict[str, _SessionLease] = {}
        self._max_entries = max(1, int(max_entries))

    def __len__(self) -> int:
        return len(self._leases)

    def _get_or_create(self, session_id: str) -> _SessionLease:
        lease = self._leases.get(session_id)
        if lease is None:
            self._evict_idle()
            lease = _SessionLease()
            self._leases[session_id] = lease
        lease.last_used = time.time()
        return lease

    def _evict_idle(self) -> None:
        overflow = len(self._leases) - self._max_entries + 1
        if overflow <= 0:
            return
        idle_ids = sorted(
            (sid for sid, lease in self._leases.items() if lease.idle),
            key=lambda sid: self._leases[sid].last_used,
        )
        for sid in idle_ids[:overflow]:
            self._leases.pop(sid, None)

    async def acquire(
        self,
        session_id: str,
        *,
        owner_key: str,
        generation: int,
        timeout: Optional[float] = None,
    ) -> Optional[TurnLeaseToken]:
        """Acquire a session lease or raise on a bounded wait timeout."""

        if not session_id:
            return None
        wait = float(timeout) if timeout and timeout > 0 else DEFAULT_LEASE_WAIT
        token = TurnLeaseToken(session_id, owner_key, int(generation))
        lease = self._get_or_create(session_id)

        if lease.lock.locked():
            holder = lease.holder
            logger.warning(
                "turn lease contention on session %s: routing key %s (gen %s) "
                "waiting behind routing key %s (gen %s)",
                session_id,
                owner_key,
                generation,
                holder.owner_key if holder else "?",
                holder.generation if holder else "?",
            )

        # Count pending waiters across the release handoff so eviction can
        # never replace a lock object while another coroutine is acquiring it.
        lease.pending_acquires += 1
        try:
            await asyncio.wait_for(lease.lock.acquire(), timeout=wait)
        except asyncio.TimeoutError:
            holder = lease.holder
            logger.error(
                "turn lease timed out on session %s (waiter key %s gen %s; "
                "holder key %s gen %s); refusing unserialized execution",
                session_id,
                owner_key,
                generation,
                holder.owner_key if holder else "?",
                holder.generation if holder else "?",
            )
            raise TurnLeaseTimeoutError(
                session_id,
                owner_key=owner_key,
                generation=int(generation),
                wait_seconds=wait,
            ) from None
        finally:
            lease.pending_acquires -= 1

        lease.holder = token
        lease.acquired_at = time.time()
        lease.last_used = lease.acquired_at
        return token

    def rebind(self, token: Optional[TurnLeaseToken], new_session_id: str) -> bool:
        """Alias a held lease after a mid-turn durable session rotation."""

        if (
            token is None
            or token.released
            or not new_session_id
            or new_session_id == token.session_id
        ):
            return False
        lease = self._leases.get(token.session_id)
        if lease is None or lease.holder is not token:
            return False

        existing = self._leases.get(new_session_id)
        if existing is not None and existing is not lease and not existing.idle:
            logger.warning(
                "turn lease rebind blocked from %s to live session %s",
                token.session_id,
                new_session_id,
            )
            return False

        self._leases[new_session_id] = lease
        lease.last_used = time.time()
        token.session_id = new_session_id
        return True


    def release(self, token: Optional[TurnLeaseToken]) -> bool:
        """Release exactly the current owner; repeated release is harmless."""

        if token is None or token.released:
            return False
        token.released = True
        lease = self._leases.get(token.session_id)
        if lease is None or lease.holder is not token:
            return False
        lease.holder = None
        lease.acquired_at = 0.0
        lease.last_used = time.time()
        if lease.lock.locked():
            lease.lock.release()
        return True


class DurableSessionTurnLease:
    """Explicit async adapter for the optional SessionDB durable lease port.

    The existing :class:`SessionTurnLeaseRegistry` remains the default
    in-process implementation.  This adapter is deliberately inert unless
    ``enabled=True`` is supplied by an integration owner.  Database work runs
    in a worker thread so a busy SQLite writer cannot block the event loop.
    """

    def __init__(
        self,
        session_db: Any,
        conversation_id: str,
        holder: str,
        *,
        ttl_seconds: float = DEFAULT_DURABLE_LEASE_TTL,
        enabled: bool = False,
        timeout_seconds: float = DEFAULT_DURABLE_LEASE_TIMEOUT,
    ) -> None:
        self._session_db = session_db
        self.conversation_id = conversation_id
        self.holder = holder
        self.ttl_seconds = ttl_seconds
        self.enabled = bool(enabled)
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError, OverflowError):
            timeout = DEFAULT_DURABLE_LEASE_TIMEOUT
        self.timeout_seconds = min(
            MAX_DURABLE_LEASE_TIMEOUT,
            max(0.01, timeout),
        )

    async def try_acquire(
        self,
        *,
        ttl_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> bool:
        """Try to acquire without waiting; timeout/errors fail closed."""
        if not self.enabled:
            return False
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._session_db.try_acquire_session_turn_lease,
                    self.conversation_id,
                    self.holder,
                    ttl_seconds=self.ttl_seconds if ttl_seconds is None else ttl_seconds,
                    now=now,
                ),
                timeout=self.timeout_seconds,
            )
            return bool(result)
        except Exception as error:
            logger.debug(
                "Durable session lease acquire skipped (%s)",
                type(error).__name__,
            )
            return False

    async def acquire(
        self,
        *,
        ttl_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> bool:
        """Compatibility alias for :meth:`try_acquire`."""
        return await self.try_acquire(ttl_seconds=ttl_seconds, now=now)

    async def refresh(
        self,
        *,
        ttl_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> bool:
        """Refresh only while this adapter's holder owns the lease."""
        if not self.enabled:
            return False
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._session_db.refresh_session_turn_lease,
                    self.conversation_id,
                    self.holder,
                    ttl_seconds=self.ttl_seconds if ttl_seconds is None else ttl_seconds,
                    now=now,
                ),
                timeout=self.timeout_seconds,
            )
            return bool(result)
        except Exception as error:
            logger.debug(
                "Durable session lease refresh skipped (%s)",
                type(error).__name__,
            )
            return False

    async def release(self) -> bool:
        """Release only this holder's lease; repeated calls are harmless."""
        if not self.enabled:
            return False
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._session_db.release_session_turn_lease,
                    self.conversation_id,
                    self.holder,
                ),
                timeout=self.timeout_seconds,
            )
            return bool(result)
        except Exception as error:
            logger.debug(
                "Durable session lease release skipped (%s)",
                type(error).__name__,
            )
            return False

    async def get(self, *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Read the current non-expired lease, or return ``None``."""
        if not self.enabled:
            return None
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._session_db.get_session_turn_lease,
                    self.conversation_id,
                    now=now,
                ),
                timeout=self.timeout_seconds,
            )
            return result if isinstance(result, dict) else None
        except Exception as error:
            logger.debug(
                "Durable session lease read skipped (%s)",
                type(error).__name__,
            )
            return None


class SessionTurnLeasePersistence:
    """Opt-in factory for durable lease adapters; never touches the registry."""

    def __init__(
        self,
        session_db: Any,
        *,
        enabled: bool = False,
        timeout_seconds: float = DEFAULT_DURABLE_LEASE_TIMEOUT,
    ) -> None:
        self._session_db = session_db
        self.enabled = bool(enabled)
        self.timeout_seconds = timeout_seconds

    def lease(
        self,
        conversation_id: str,
        holder: str,
        *,
        ttl_seconds: float = DEFAULT_DURABLE_LEASE_TTL,
    ) -> DurableSessionTurnLease:
        return DurableSessionTurnLease(
            self._session_db,
            conversation_id,
            holder,
            ttl_seconds=ttl_seconds,
            enabled=self.enabled,
            timeout_seconds=self.timeout_seconds,
        )

    def for_conversation(
        self,
        conversation_id: str,
        holder: str,
        *,
        ttl_seconds: float = DEFAULT_DURABLE_LEASE_TTL,
    ) -> DurableSessionTurnLease:
        """Descriptive alias for :meth:`lease`."""
        return self.lease(
            conversation_id,
            holder,
            ttl_seconds=ttl_seconds,
        )

__all__ = [
    "DEFAULT_MAX_LEASES",
    "DEFAULT_LEASE_WAIT",
    "DEFAULT_DURABLE_LEASE_TIMEOUT",
    "DEFAULT_DURABLE_LEASE_TTL",
    "DurableSessionTurnLease",
    "SessionTurnLeasePersistence",
    "SessionTurnLeaseRegistry",
    "TurnLeaseTimeoutError",
    "TurnLeaseToken",
]
