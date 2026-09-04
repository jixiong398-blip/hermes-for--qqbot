"""Read-only compatibility contract for the upstream SessionDB v26 schema.

The local product still owns its v11 schema and its WAL/FTS/lineage behavior.
This module records the v26 table/column contract and exposes a probe that can
inspect a copied database without running migrations, DDL, FTS rebuilds, or
changing ``schema_version``.  It is deliberately independent from the
upstream ``hermes_state_*`` mixins so the operator can review a migration plan
before enabling any write path.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from hermes_constants import get_hermes_home
from hermes_state_schema_probe import SchemaProbeResult, probe_schema


V26_SCHEMA_VERSION = 26

# The first write gate is deliberately additive and narrow.  It creates only
# tables whose rows can remain empty until a separately reviewed backfill is
# available; it never changes the local v11 tables or schema_version marker.
V26_COPY_MAX_BYTES = 8 * 1024 * 1024 * 1024
V26_COPY_MAX_BUSY_TIMEOUT_MS = 5_000
V26_INCREMENTAL_TABLES = (
    "system_prompts",
    "session_model_usage",
    "gateway_routing",
    "gateway_hygiene_state",
    "compression_locks",
    "session_turn_leases",
    "async_delegations",
)

# Explicitly selected v26 columns that can be added without a backfill.  The
# batch is opt-in through ``columns=`` so existing callers of the first table
# gate keep their exact behavior.
V26_INCREMENTAL_COLUMN_DEFS: Mapping[str, Mapping[str, str]] = {
    "sessions": {
        "session_key": "TEXT",
        "chat_id": "TEXT",
        "chat_type": "TEXT",
        "thread_id": "TEXT",
        "display_name": "TEXT",
        "origin_json": "TEXT",
        "expiry_finalized": "INTEGER DEFAULT 0",
        "system_prompt_hash": "TEXT",
        "title_source": "TEXT",
        "last_activity_at": "REAL",
        "last_activity_description": "TEXT",
        "last_activity_provenance": "TEXT",
        "handoff_state": "TEXT",
        "handoff_platform": "TEXT",
        "handoff_error": "TEXT",
        "profile_name": "TEXT",
        "git_metadata_generation": "INTEGER NOT NULL DEFAULT 0",
        "compression_failure_cooldown_until": "REAL",
        "compression_failure_error": "TEXT",
        "compression_fallback_streak": "INTEGER NOT NULL DEFAULT 0",
        "compression_ineffective_count": "INTEGER NOT NULL DEFAULT 0",
        "rewind_count": "INTEGER NOT NULL DEFAULT 0",
        "archived": "INTEGER NOT NULL DEFAULT 0",
        "pinned": "INTEGER NOT NULL DEFAULT 0",
        "hidden": "INTEGER NOT NULL DEFAULT 0",
        "last_read_at": "REAL",
    },
    "messages": {
        "effect_disposition": "TEXT",
        "platform_message_id": "TEXT",
        "observed": "INTEGER DEFAULT 0",
        "_compressed_summary": "INTEGER NOT NULL DEFAULT 0",
        "active": "INTEGER NOT NULL DEFAULT 1",
        "compacted": "INTEGER NOT NULL DEFAULT 0",
        "api_content": "TEXT",
        "display_kind": "TEXT",
        "display_metadata": "TEXT",
    },
}
V26_INCREMENTAL_COLUMN_BATCH = tuple(
    f"{table}.{column}"
    for table, definitions in V26_INCREMENTAL_COLUMN_DEFS.items()
    for column in definitions
)

_V26_INCREMENTAL_DDL: Mapping[str, str] = {
    "system_prompts": """
        CREATE TABLE IF NOT EXISTS "system_prompts" (
            "hash" TEXT PRIMARY KEY,
            "prompt" TEXT NOT NULL
        )
    """,
    "session_model_usage": """
        CREATE TABLE IF NOT EXISTS "session_model_usage" (
            "session_id" TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            "model" TEXT NOT NULL,
            "billing_provider" TEXT NOT NULL DEFAULT '',
            "billing_base_url" TEXT NOT NULL DEFAULT '',
            "billing_mode" TEXT NOT NULL DEFAULT '',
            "task" TEXT NOT NULL DEFAULT '',
            "api_call_count" INTEGER NOT NULL DEFAULT 0,
            "input_tokens" INTEGER NOT NULL DEFAULT 0,
            "output_tokens" INTEGER NOT NULL DEFAULT 0,
            "cache_read_tokens" INTEGER NOT NULL DEFAULT 0,
            "cache_write_tokens" INTEGER NOT NULL DEFAULT 0,
            "reasoning_tokens" INTEGER NOT NULL DEFAULT 0,
            "estimated_cost_usd" REAL NOT NULL DEFAULT 0,
            "actual_cost_usd" REAL NOT NULL DEFAULT 0,
            "cost_status" TEXT,
            "cost_source" TEXT,
            "first_seen" REAL,
            "last_seen" REAL,
            PRIMARY KEY (
                "session_id", "model", "billing_provider", "billing_base_url",
                "billing_mode", "task"
            )
        )
    """,
    "gateway_routing": """
        CREATE TABLE IF NOT EXISTS "gateway_routing" (
            "scope" TEXT NOT NULL DEFAULT '',
            "session_key" TEXT NOT NULL,
            "entry_json" TEXT NOT NULL,
            "updated_at" REAL NOT NULL,
            PRIMARY KEY ("scope", "session_key")
        )
    """,
    "gateway_hygiene_state": """
        CREATE TABLE IF NOT EXISTS "gateway_hygiene_state" (
            "session_key" TEXT PRIMARY KEY,
            "failure_streak" INTEGER NOT NULL DEFAULT 0
        )
    """,
    "compression_locks": """
        CREATE TABLE IF NOT EXISTS "compression_locks" (
            "session_id" TEXT PRIMARY KEY,
            "holder" TEXT NOT NULL,
            "acquired_at" REAL NOT NULL,
            "expires_at" REAL NOT NULL
        )
    """,
    "session_turn_leases": """
        CREATE TABLE IF NOT EXISTS "session_turn_leases" (
            "conversation_id" TEXT PRIMARY KEY,
            "holder" TEXT NOT NULL,
            "acquired_at" REAL NOT NULL,
            "expires_at" REAL NOT NULL
        )
    """,
    "async_delegations": """
        CREATE TABLE IF NOT EXISTS "async_delegations" (
            "delegation_id" TEXT PRIMARY KEY,
            "origin_session" TEXT NOT NULL,
            "origin_ui_session_id" TEXT NOT NULL DEFAULT '',
            "parent_session_id" TEXT,
            "state" TEXT NOT NULL,
            "dispatched_at" REAL NOT NULL,
            "completed_at" REAL,
            "updated_at" REAL NOT NULL,
            "event_json" TEXT,
            "result_json" TEXT,
            "delivery_state" TEXT NOT NULL DEFAULT 'pending',
            "delivery_attempts" INTEGER NOT NULL DEFAULT 0,
            "delivered_at" REAL,
            "owner_pid" INTEGER,
            "owner_started_at" INTEGER,
            "task_json" TEXT,
            "delivery_claim" TEXT,
            "delivery_claimed_at" REAL
        )
    """,
}

_V26_INCREMENTAL_TABLE_COLUMN_DEFS: Mapping[str, Mapping[str, str]] = {
    "system_prompts": {
        "hash": "TEXT",
        "prompt": "TEXT NOT NULL",
    },
    "session_model_usage": {
        "session_id": "TEXT NOT NULL",
        "model": "TEXT NOT NULL",
        "billing_provider": "TEXT NOT NULL DEFAULT ''",
        "billing_base_url": "TEXT NOT NULL DEFAULT ''",
        "billing_mode": "TEXT NOT NULL DEFAULT ''",
        "task": "TEXT NOT NULL DEFAULT ''",
        "api_call_count": "INTEGER NOT NULL DEFAULT 0",
        "input_tokens": "INTEGER NOT NULL DEFAULT 0",
        "output_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cache_read_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cache_write_tokens": "INTEGER NOT NULL DEFAULT 0",
        "reasoning_tokens": "INTEGER NOT NULL DEFAULT 0",
        "estimated_cost_usd": "REAL NOT NULL DEFAULT 0",
        "actual_cost_usd": "REAL NOT NULL DEFAULT 0",
        "cost_status": "TEXT",
        "cost_source": "TEXT",
        "first_seen": "REAL",
        "last_seen": "REAL",
    },
    "gateway_routing": {
        "scope": "TEXT NOT NULL DEFAULT ''",
        "session_key": "TEXT NOT NULL",
        "entry_json": "TEXT NOT NULL",
        "updated_at": "REAL NOT NULL",
    },
    "gateway_hygiene_state": {
        "session_key": "TEXT",
        "failure_streak": "INTEGER NOT NULL DEFAULT 0",
    },
    "compression_locks": {
        "session_id": "TEXT",
        "holder": "TEXT NOT NULL",
        "acquired_at": "REAL NOT NULL",
        "expires_at": "REAL NOT NULL",
    },
    "session_turn_leases": {
        "conversation_id": "TEXT",
        "holder": "TEXT NOT NULL",
        "acquired_at": "REAL NOT NULL",
        "expires_at": "REAL NOT NULL",
    },
    "async_delegations": {
        "delegation_id": "TEXT",
        "origin_session": "TEXT NOT NULL",
        "origin_ui_session_id": "TEXT NOT NULL DEFAULT ''",
        "parent_session_id": "TEXT",
        "state": "TEXT NOT NULL",
        "dispatched_at": "REAL NOT NULL",
        "completed_at": "REAL",
        "updated_at": "REAL NOT NULL",
        "event_json": "TEXT",
        "result_json": "TEXT",
        "delivery_state": "TEXT NOT NULL DEFAULT 'pending'",
        "delivery_attempts": "INTEGER NOT NULL DEFAULT 0",
        "delivered_at": "REAL",
        "owner_pid": "INTEGER",
        "owner_started_at": "INTEGER",
        "task_json": "TEXT",
        "delivery_claim": "TEXT",
        "delivery_claimed_at": "REAL",
    },
}
_V26_INCREMENTAL_TABLE_PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "system_prompts": ("hash",),
    "session_model_usage": (
        "session_id",
        "model",
        "billing_provider",
        "billing_base_url",
        "billing_mode",
        "task",
    ),
    "gateway_routing": ("scope", "session_key"),
    "gateway_hygiene_state": ("session_key",),
    "compression_locks": ("session_id",),
    "session_turn_leases": ("conversation_id",),
    "async_delegations": ("delegation_id",),
}
_V26_INCREMENTAL_TABLE_FOREIGN_KEYS: Mapping[str, tuple[str, str, str]] = {
    "session_model_usage": ("sessions", "session_id", "id"),
}

# Canonical v26 tables and columns from upstream hermes_state_common.py.  FTS
# virtual tables and their triggers are intentionally excluded: they are
# derived indexes and must be handled by a separate, bounded repair gate.
V26_EXPECTED_SCHEMA: Mapping[str, tuple[str, ...]] = {
    "schema_version": ("version",),
    "system_prompts": ("hash", "prompt"),
    "sessions": (
        "id", "source", "user_id", "session_key", "chat_id", "chat_type",
        "thread_id", "display_name", "origin_json", "expiry_finalized",
        "model", "model_config", "system_prompt", "system_prompt_hash",
        "parent_session_id", "started_at", "ended_at", "end_reason",
        "message_count", "tool_call_count", "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "cwd",
        "git_branch", "git_repo_root", "git_metadata_generation",
        "billing_provider", "billing_base_url", "billing_mode",
        "estimated_cost_usd", "actual_cost_usd", "cost_status", "cost_source",
        "pricing_version", "title", "title_source", "last_activity_at",
        "last_activity_description", "last_activity_provenance", "api_call_count",
        "handoff_state", "handoff_platform", "handoff_error",
        "compression_failure_cooldown_until", "compression_failure_error",
        "compression_fallback_streak", "compression_ineffective_count",
        "profile_name", "rewind_count", "archived", "pinned", "hidden",
        "last_read_at",
    ),
    "messages": (
        "id", "session_id", "role", "content", "tool_call_id", "tool_calls",
        "tool_name", "effect_disposition", "timestamp", "token_count",
        "finish_reason", "reasoning", "reasoning_content", "reasoning_details",
        "codex_reasoning_items", "codex_message_items", "platform_message_id",
        "observed", "_compressed_summary", "active", "compacted", "api_content",
        "display_kind", "display_metadata",
    ),
    "session_model_usage": (
        "session_id", "model", "billing_provider", "billing_base_url",
        "billing_mode", "task", "api_call_count", "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
        "estimated_cost_usd", "actual_cost_usd", "cost_status", "cost_source",
        "first_seen", "last_seen",
    ),
    "gateway_routing": ("scope", "session_key", "entry_json", "updated_at"),
    "gateway_hygiene_state": ("session_key", "failure_streak"),
    "compression_locks": ("session_id", "holder", "acquired_at", "expires_at"),
    "session_turn_leases": ("conversation_id", "holder", "acquired_at", "expires_at"),
    "async_delegations": (
        "delegation_id", "origin_session", "origin_ui_session_id",
        "parent_session_id", "state", "dispatched_at", "completed_at",
        "updated_at", "event_json", "result_json", "delivery_state",
        "delivery_attempts", "delivered_at", "owner_pid", "owner_started_at",
        "task_json", "delivery_claim", "delivery_claimed_at",
    ),
    "state_meta": ("key", "value"),
}

# The local v11 base schema, kept here as an explicit comparison baseline. It
# deliberately omits product-specific optional tables created by other
# modules; those are not part of the upstream core schema migration contract.
LOCAL_V11_SCHEMA: Mapping[str, tuple[str, ...]] = {
    "schema_version": ("version",),
    "sessions": (
        "id", "source", "user_id", "model", "model_config", "system_prompt",
        "parent_session_id", "started_at", "ended_at", "end_reason",
        "message_count", "tool_call_count", "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
        "billing_provider", "billing_base_url", "billing_mode",
        "estimated_cost_usd", "actual_cost_usd", "cost_status", "cost_source",
        "pricing_version", "title", "api_call_count",
    ),
    "messages": (
        "id", "session_id", "role", "content", "tool_call_id", "tool_calls",
        "tool_name", "timestamp", "token_count", "finish_reason", "reasoning",
        "reasoning_content", "reasoning_details", "codex_reasoning_items",
        "codex_message_items",
    ),
    "state_meta": ("key", "value"),
}


@dataclass(frozen=True)
class V26SchemaReport:
    """Read-only v26 compatibility result for one database file."""

    schema_version: Optional[int]
    missing_tables: tuple[str, ...]
    missing_columns: tuple[tuple[str, str], ...]
    errors: tuple[str, ...]

    @property
    def is_v26_ready(self) -> bool:
        """Return true only when the full canonical v26 core is readable."""

        return (
            self.schema_version == V26_SCHEMA_VERSION
            and not self.missing_tables
            and not self.missing_columns
            and not self.errors
        )

    @property
    def migration_required(self) -> bool:
        """Return true when this file cannot be treated as an existing v26 DB."""

        return not self.is_v26_ready

    @property
    def state(self) -> str:
        """Return a bounded operator-facing state label."""

        if self.is_v26_ready:
            return "v26_ready"
        if self.errors:
            return "unreadable"
        if self.schema_version == 11:
            return "legacy_v11"
        if self.schema_version == V26_SCHEMA_VERSION:
            return "partial_v26"
        return "unknown_schema"


@dataclass(frozen=True)
class V26MigrationPlan:
    """Read-only migration evidence derived from a :class:`V26SchemaReport`.

    This is deliberately a plan, not an executable migration.  The write gate
    is permanently closed in this compatibility module; the caller must first
    produce a copied-database replay and separate FTS/lineage/PK evidence.
    """

    source_state: str
    source_schema_version: Optional[int]
    target_schema_version: int
    add_tables: tuple[str, ...] = ()
    add_columns: tuple[tuple[str, str], ...] = ()
    blockers: tuple[str, ...] = ()
    deferred_operations: tuple[str, ...] = (
        "fts_rebuild_and_trigger_repair",
        "lineage_backfill_and_parent_heal",
        "primary_key_heal",
        "schema_version_write",
    )
    next_steps: tuple[str, ...] = ()
    requires_copied_database: bool = True
    write_gate_open: bool = False

    @property
    def is_read_only(self) -> bool:
        """Return true for the only mode this compatibility layer permits."""

        return not self.write_gate_open

    @property
    def requires_migration(self) -> bool:
        """Return whether the source can be treated as a complete v26 store."""

        return self.source_state != "v26_ready"

    @property
    def replayable_on_copy(self) -> bool:
        """Return whether a readable legacy/complete copy can enter replay."""

        hard_blockers = {
            "schema_probe_errors",
            "schema_version_unreadable",
            "future_schema_version",
        }
        return (
            self.source_state in {"legacy_v11", "v26_ready"}
            and not any(item in hard_blockers for item in self.blockers)
        )


_V26_DEFERRED_OPERATIONS = (
    "fts_rebuild_and_trigger_repair",
    "lineage_backfill_and_parent_heal",
    "primary_key_heal",
    "schema_version_write",
)


def build_v26_migration_plan(report: V26SchemaReport) -> V26MigrationPlan:
    """Build bounded migration evidence without opening or mutating a DB."""

    if not isinstance(report, V26SchemaReport):
        raise TypeError("report must be a V26SchemaReport")

    # ``schema_version`` is metadata, not an additive table candidate. A
    # missing version is a hard blocker because guessing a source version is
    # exactly the unsafe shortcut this facade is intended to prevent.
    add_tables = tuple(
        table
        for table in report.missing_tables
        if table != "schema_version"
    )
    add_columns = tuple(report.missing_columns)

    blockers: list[str] = []
    version = report.schema_version
    if report.errors:
        blockers.append("schema_probe_errors")
    if version is None:
        blockers.append("schema_version_unreadable")
    elif version > V26_SCHEMA_VERSION:
        blockers.append("future_schema_version")
    elif version < V26_SCHEMA_VERSION:
        blockers.append("legacy_schema_version")
    if report.missing_tables or report.missing_columns:
        blockers.append("missing_v26_objects")

    if report.state == "v26_ready":
        next_steps = (
            "record_copy_hash_and_metadata",
            "compare_fts_indexes_and_triggers_on_copy",
        )
    elif report.state == "legacy_v11" and not report.errors:
        next_steps = (
            "export_and_audit_v11_rows",
            "replay_into_disposable_target",
            "compare_additive_fields_and_lineage",
        )
    elif report.state == "unreadable":
        next_steps = ("obtain_a_readable_database_copy",)
    else:
        next_steps = ("identify_source_schema_before_replay",)

    return V26MigrationPlan(
        source_state=report.state,
        source_schema_version=version,
        target_schema_version=V26_SCHEMA_VERSION,
        add_tables=add_tables,
        add_columns=add_columns,
        blockers=tuple(dict.fromkeys(blockers)),
        deferred_operations=_V26_DEFERRED_OPERATIONS,
        next_steps=next_steps,
    )


class V26CopyGateError(ValueError):
    """Stable, path-free precondition error for the copy-only write gate."""


@dataclass(frozen=True)
class V26CopyWriteReport:
    """Bounded evidence from one explicit v26 copy-only write attempt."""

    accepted: bool
    status: str
    target_name: str = "source-copy"
    backup_name: str = "backup-copy"
    target_sha256_before: str = ""
    target_sha256_after: str = ""
    backup_sha256_before: str = ""
    backup_sha256_after: str = ""
    target_sidecars_before: Mapping[str, Any] = field(default_factory=dict)
    target_sidecars_after: Mapping[str, Any] = field(default_factory=dict)
    backup_sidecars_before: Mapping[str, Any] = field(default_factory=dict)
    backup_sidecars_after: Mapping[str, Any] = field(default_factory=dict)
    schema_version_before: Optional[int] = None
    schema_version_after: Optional[int] = None
    tables_requested: tuple[str, ...] = ()
    tables_would_create: tuple[str, ...] = ()
    tables_created: tuple[str, ...] = ()
    columns_requested: tuple[str, ...] = ()
    columns_would_add: tuple[str, ...] = ()
    columns_added: tuple[str, ...] = ()
    write_operations: int = 0
    backup_checked: bool = False
    backup_unchanged: bool = False
    backup_sidecars_unchanged: bool = False
    target_unchanged: bool = False
    sidecars_unchanged: bool = False
    write_gate_open: bool = False
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in {"dry_run", "committed", "already_applied"} and not self.errors

    @property
    def source_preserved(self) -> bool:
        """Return whether the separately supplied backup remained unchanged."""

        return (
            self.backup_checked
            and self.backup_unchanged
            and self.backup_sidecars_unchanged
        )

    @property
    def rollback_evidence(self) -> Mapping[str, Any]:
        """Return bounded evidence for callers that use the replay contract."""

        return {
            "target_unchanged": self.target_unchanged,
            "backup_checked": self.backup_checked,
            "backup_unchanged": self.backup_unchanged,
            "backup_sidecars_unchanged": self.backup_sidecars_unchanged,
            "sidecars_unchanged": self.sidecars_unchanged,
            "columns_added": self.columns_added,
            "write_operations": self.write_operations,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible report without filesystem paths."""

        return {
            "accepted": self.accepted,
            "status": self.status,
            "ok": self.ok,
            "target_name": self.target_name,
            "backup_name": self.backup_name,
            "target_sha256_before": self.target_sha256_before,
            "target_sha256_after": self.target_sha256_after,
            "backup_sha256_before": self.backup_sha256_before,
            "backup_sha256_after": self.backup_sha256_after,
            "target_sidecars_before": dict(self.target_sidecars_before or {}),
            "target_sidecars_after": dict(self.target_sidecars_after or {}),
            "backup_sidecars_before": dict(self.backup_sidecars_before or {}),
            "backup_sidecars_after": dict(self.backup_sidecars_after or {}),
            "schema_version_before": self.schema_version_before,
            "schema_version_after": self.schema_version_after,
            "tables_requested": list(self.tables_requested),
            "tables_would_create": list(self.tables_would_create),
            "tables_created": list(self.tables_created),
            "columns_requested": list(self.columns_requested),
            "columns_would_add": list(self.columns_would_add),
            "columns_added": list(self.columns_added),
            "write_operations": self.write_operations,
            "backup_checked": self.backup_checked,
            "backup_unchanged": self.backup_unchanged,
            "backup_sidecars_unchanged": self.backup_sidecars_unchanged,
            "source_preserved": self.source_preserved,
            "target_unchanged": self.target_unchanged,
            "sidecars_unchanged": self.sidecars_unchanged,
            "write_gate_open": False,
            "errors": list(self.errors),
            "rollback_evidence": dict(self.rollback_evidence),
        }


def _copy_gate_error_code(error: BaseException) -> str:
    """Convert arbitrary failures to a bounded, non-sensitive error code."""

    if isinstance(error, V26CopyGateError):
        return str(error)[:80]
    if isinstance(error, sqlite3.OperationalError):
        text = str(error).lower()
        if "locked" in text or "busy" in text:
            return "sqlite_busy"
    name = type(error).__name__
    return f"{name[:64]}" if name else "copy_gate_error"


def _is_sqlite_busy(error: BaseException) -> bool:
    if not isinstance(error, sqlite3.OperationalError):
        return False
    text = str(error).lower()
    return "locked" in text or "busy" in text


def _validate_copy_path(value: str | Path, *, role: str) -> Path:
    if value is None:
        raise V26CopyGateError(f"{role}_required")
    try:
        path = Path(value).expanduser()
    except (TypeError, ValueError) as error:
        raise V26CopyGateError(f"{role}_invalid") from error
    if path.is_symlink():
        raise V26CopyGateError(f"{role}_symlink_rejected")
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as error:
        raise V26CopyGateError(f"{role}_missing") from error
    if not stat.S_ISREG(info.st_mode):
        raise V26CopyGateError(f"{role}_not_regular")
    if int(info.st_size) > V26_COPY_MAX_BYTES:
        raise V26CopyGateError(f"{role}_too_large")

    live_path = get_hermes_home() / "state.db"
    try:
        if resolved == live_path.resolve(strict=False):
            raise V26CopyGateError("runtime_state_rejected")
        if live_path.exists() and os.path.samefile(resolved, live_path):
            raise V26CopyGateError("runtime_state_rejected")
    except V26CopyGateError:
        raise
    except OSError:
        pass
    return resolved


def _copy_digest(path: Path) -> str:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                if total > V26_COPY_MAX_BYTES:
                    raise V26CopyGateError("copy_too_large")
                digest.update(chunk)
    except V26CopyGateError:
        raise
    except PermissionError as error:
        # Windows may deny a read of SQLite's journal while another process
        # owns the write lock.  Surface that as a bounded contention result;
        # do not include the filesystem path or raw OS message.
        raise V26CopyGateError("sqlite_busy") from error
    except OSError as error:
        raise V26CopyGateError("copy_unreadable") from error
    return digest.hexdigest()


def _copy_sidecar_state(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, suffix in (("wal", "-wal"), ("shm", "-shm"), ("journal", "-journal")):
        sidecar = Path(f"{path}{suffix}")
        try:
            info = sidecar.lstat()
        except FileNotFoundError:
            result[label] = {"present": False, "size": 0, "sha256": ""}
            continue
        except OSError as error:
            raise V26CopyGateError("sidecar_unreadable") from error
        if stat.S_ISLNK(info.st_mode):
            raise V26CopyGateError("sidecar_symlink_rejected")
        if not stat.S_ISREG(info.st_mode):
            raise V26CopyGateError("sidecar_not_regular")
        if int(info.st_size) > V26_COPY_MAX_BYTES:
            raise V26CopyGateError("sidecar_too_large")
        result[label] = {
            "present": True,
            "size": int(info.st_size),
            "sha256": _copy_digest(sidecar),
        }
    return result


class _CopyGateFileLock:
    """Small cross-process lock for the copy migration critical section."""

    def __init__(self, target: Path) -> None:
        self._path = target.with_name(f".{target.name}.v26-gate.lock")
        self._stream = None
        self._locked = False

    def _open(self) -> Any:
        try:
            info = self._path.lstat()
        except FileNotFoundError:
            try:
                descriptor = os.open(
                    str(self._path),
                    os.O_RDWR | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                descriptor = None
            if descriptor is not None:
                stream = os.fdopen(descriptor, "a+b")
                stream.write(b"0")
                stream.flush()
                return stream
            info = self._path.lstat()
        except OSError as error:
            raise V26CopyGateError("file_lock_path_unreadable") from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise V26CopyGateError("file_lock_path_rejected")
        try:
            return self._path.open("a+b")
        except OSError as error:
            raise V26CopyGateError("file_lock_path_unreadable") from error

    def _try_lock(self, stream: Any) -> bool:
        stream.seek(0)
        if os.name == "nt":
            try:
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except (ImportError, OSError):
                return False
        try:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (ImportError, OSError):
            return False

    def acquire(self, timeout_ms: int) -> bool:
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while True:
            stream = self._open()
            if self._try_lock(stream):
                self._stream = stream
                self._locked = True
                return True
            stream.close()
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def release(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                try:
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                except (ImportError, OSError):
                    pass
            else:
                try:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                except (ImportError, OSError):
                    pass
        finally:
            self._locked = False
            stream.close()


def _open_copy_read_only(path: Path) -> sqlite3.Connection:
    sidecars = _copy_sidecar_state(path)
    has_sidecars = any(bool(item.get("present")) for item in sidecars.values())
    query = "mode=ro" if has_sidecars else "mode=ro&immutable=1"
    connection = sqlite3.connect(f"{path.as_uri()}?{query}", uri=True, timeout=1.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _copy_table_info(path: Path, table: str) -> dict[str, dict[str, Any]]:
    connection = None
    try:
        connection = _open_copy_read_only(path)
        rows = connection.execute(
            f'PRAGMA table_info("{table}")'
        ).fetchall()
        return {
            str(row[1]): {
                "type": str(row[2] or "").upper(),
                "notnull": bool(row[3]),
                "default": None if row[4] is None else str(row[4]).strip(),
                "pk": int(row[5] or 0),
            }
            for row in rows
        }
    except V26CopyGateError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise V26CopyGateError("target_schema_unreadable") from error
    finally:
        if connection is not None:
            connection.close()


def _copy_table_foreign_keys(path: Path, table: str) -> tuple[tuple[str, str, str], ...]:
    connection = None
    try:
        connection = _open_copy_read_only(path)
        rows = connection.execute(
            f'PRAGMA foreign_key_list("{table}")'
        ).fetchall()
        return tuple(
            (str(row[2]), str(row[3]), str(row[4]))
            for row in rows
        )
    except V26CopyGateError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise V26CopyGateError("target_schema_unreadable") from error
    finally:
        if connection is not None:
            connection.close()


def _validate_existing_incremental_table(path: Path, table: str) -> None:
    info = _copy_table_info(path, table)
    expected = _V26_INCREMENTAL_TABLE_COLUMN_DEFS[table]
    if not info or not set(expected) <= set(info):
        raise V26CopyGateError("target_incremental_table_incompatible")
    for column, definition in expected.items():
        if not _column_definition_matches(info[column], definition):
            raise V26CopyGateError("target_incremental_table_incompatible")
    primary_key = tuple(
        column for column, metadata in sorted(
            info.items(), key=lambda item: int(item[1].get("pk") or 0)
        )
        if int(metadata.get("pk") or 0) > 0
    )
    if primary_key != _V26_INCREMENTAL_TABLE_PRIMARY_KEYS[table]:
        raise V26CopyGateError("target_incremental_table_incompatible")
    expected_foreign_key = _V26_INCREMENTAL_TABLE_FOREIGN_KEYS.get(table)
    if expected_foreign_key and expected_foreign_key not in _copy_table_foreign_keys(path, table):
        raise V26CopyGateError("target_incremental_table_incompatible")


def _normalize_incremental_tables(
    tables: Iterable[str] | str | None,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if tables is None:
        values: tuple[Any, ...] = ("system_prompts",)
    elif isinstance(tables, str):
        values = (tables,)
    else:
        try:
            values = tuple(tables)
        except TypeError as error:
            raise V26CopyGateError("tables_invalid") from error
    if not values and allow_empty:
        return ()
    if not values or len(values) > len(V26_INCREMENTAL_TABLES):
        raise V26CopyGateError("tables_invalid")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or value not in V26_INCREMENTAL_TABLES:
            raise V26CopyGateError("table_not_allowed")
        if value in normalized:
            raise V26CopyGateError("table_duplicate")
        normalized.append(value)
    if not normalized:
        raise V26CopyGateError("tables_invalid")
    return tuple(normalized)


def _normalize_incremental_columns(
    columns: Iterable[str] | Mapping[str, Iterable[str]] | str | None,
) -> tuple[str, ...]:
    if columns is None:
        return ()
    values: list[str] = []
    if isinstance(columns, Mapping):
        for table, names in columns.items():
            if not isinstance(table, str) or isinstance(names, (str, bytes)):
                raise V26CopyGateError("columns_invalid")
            try:
                names_tuple = tuple(names)
            except TypeError as error:
                raise V26CopyGateError("columns_invalid") from error
            values.extend(f"{table}.{name}" for name in names_tuple)
    elif isinstance(columns, str):
        values.append(columns)
    else:
        try:
            values.extend(columns)
        except TypeError as error:
            raise V26CopyGateError("columns_invalid") from error

    if not values:
        raise V26CopyGateError("columns_invalid")

    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or value not in V26_INCREMENTAL_COLUMN_BATCH:
            raise V26CopyGateError("column_not_allowed")
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _column_definition_matches(
    info: Mapping[str, Any],
    definition: str,
) -> bool:
    parts = definition.upper().split()
    if not parts or str(info.get("type", "")).upper() != parts[0]:
        return False
    expected_notnull = "NOT NULL" in definition.upper()
    if bool(info.get("notnull")) != expected_notnull:
        return False
    expected_default = None
    upper = definition.upper()
    marker = " DEFAULT "
    if marker in upper:
        expected_default = definition[upper.index(marker) + len(marker):].strip()
    actual_default = info.get("default")
    if expected_default is None:
        return actual_default is None
    return str(actual_default or "").strip().upper() == expected_default.upper()


def _normalize_busy_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError) as error:
        raise V26CopyGateError("busy_timeout_invalid") from error
    if timeout < 0 or timeout > V26_COPY_MAX_BUSY_TIMEOUT_MS:
        raise V26CopyGateError("busy_timeout_invalid")
    return timeout


def _create_incremental_table(connection: sqlite3.Connection, table: str) -> None:
    connection.execute(_V26_INCREMENTAL_DDL[table])


def _add_incremental_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    if table not in V26_INCREMENTAL_COLUMN_DEFS:
        raise V26CopyGateError("column_not_allowed")
    if column not in V26_INCREMENTAL_COLUMN_DEFS[table]:
        raise V26CopyGateError("column_not_allowed")
    connection.execute(
        f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}'
    )


def _write_gate_report(
    *,
    accepted: bool,
    status: str,
    target_hash_before: str = "",
    target_hash_after: str = "",
    backup_hash_before: str = "",
    backup_hash_after: str = "",
    target_sidecars_before: Mapping[str, Any] | None = None,
    target_sidecars_after: Mapping[str, Any] | None = None,
    backup_sidecars_before: Mapping[str, Any] | None = None,
    backup_sidecars_after: Mapping[str, Any] | None = None,
    schema_before: Optional[int] = None,
    schema_after: Optional[int] = None,
    requested: tuple[str, ...] = (),
    would_create: tuple[str, ...] = (),
    created: tuple[str, ...] = (),
    requested_columns: tuple[str, ...] = (),
    would_add: tuple[str, ...] = (),
    added: tuple[str, ...] = (),
    backup_checked: bool = False,
    errors: Iterable[str] = (),
) -> V26CopyWriteReport:
    before = dict(target_sidecars_before or {})
    after = dict(target_sidecars_after or {})
    backup_before = dict(backup_sidecars_before or {})
    backup_after = dict(backup_sidecars_after or {})
    target_unchanged = bool(
        target_hash_before
        and target_hash_before == target_hash_after
    )
    sidecars_unchanged = bool(before == after) if before or after else False
    backup_unchanged = bool(
        backup_checked
        and backup_hash_before
        and backup_hash_before == backup_hash_after
    )
    backup_sidecars_unchanged = bool(
        backup_checked and backup_before == backup_after
    )
    return V26CopyWriteReport(
        accepted=accepted,
        status=status,
        target_sha256_before=target_hash_before,
        target_sha256_after=target_hash_after,
        backup_sha256_before=backup_hash_before,
        backup_sha256_after=backup_hash_after,
        target_sidecars_before=before,
        target_sidecars_after=after,
        backup_sidecars_before=backup_before,
        backup_sidecars_after=backup_after,
        schema_version_before=schema_before,
        schema_version_after=schema_after,
        tables_requested=requested,
        tables_would_create=would_create,
        tables_created=created,
        columns_requested=requested_columns,
        columns_would_add=would_add,
        columns_added=added,
        write_operations=len(created) + len(added),
        backup_checked=backup_checked,
        backup_sidecars_unchanged=backup_sidecars_unchanged,
        backup_unchanged=backup_unchanged,
        target_unchanged=target_unchanged,
        sidecars_unchanged=sidecars_unchanged,
        errors=tuple(dict.fromkeys(str(item)[:80] for item in errors)),
    )


def apply_v26_copy_gate(
    target_path: str | Path,
    *,
    enable: bool = False,
    dry_run: bool = True,
    backup_path: str | Path | None = None,
    expected_sha256: str | None = None,
    tables: Iterable[str] | str | None = None,
    columns: Iterable[str] | Mapping[str, Iterable[str]] | str | None = None,
    busy_timeout_ms: int = 1_000,
) -> V26CopyWriteReport:
    """Apply one narrow v26 additive schema step to an explicit database copy.

    The default is a no-write disabled report.  A real write requires
    ``enable=True``, ``dry_run=False``, a separate regular backup copy, and an
    expected SHA-256 matching the target immediately before the transaction.
    The target must be a complete local v11 database; this function never
    updates ``schema_version`` and never opens the runtime ``state.db``.
    ``columns=`` is an explicit list of qualified ``table.column`` names (or
    a table-to-column mapping); omitted columns preserve the original table
    gate behavior.
    """

    try:
        requested_columns = _normalize_incremental_columns(columns)
        table_input = () if columns is not None and tables is None else tables
        requested = _normalize_incremental_tables(
            table_input,
            allow_empty=columns is not None,
        )
        timeout_ms = _normalize_busy_timeout(busy_timeout_ms)
        target = _validate_copy_path(target_path, role="target")
        target_hash_before = _copy_digest(target)
        target_sidecars_before = _copy_sidecar_state(target)

        local_report = probe_schema(target, LOCAL_V11_SCHEMA)
        if local_report.errors:
            raise V26CopyGateError("target_schema_unreadable")
        if local_report.schema_version != 11:
            raise V26CopyGateError("target_schema_version_mismatch")
        if local_report.missing_tables or local_report.missing_columns:
            raise V26CopyGateError("target_v11_schema_incomplete")

        existing: set[str] = set()
        for table in requested:
            if not _copy_table_info(target, table):
                continue
            _validate_existing_incremental_table(target, table)
            existing.add(table)
        pending = tuple(table for table in requested if table not in existing)
        pending_columns: list[str] = []
        for qualified in requested_columns:
            table, column = qualified.split(".", 1)
            table_info = _copy_table_info(target, table)
            if column in table_info:
                if not _column_definition_matches(
                    table_info[column],
                    V26_INCREMENTAL_COLUMN_DEFS[table][column],
                ):
                    raise V26CopyGateError("target_incremental_column_incompatible")
            else:
                pending_columns.append(qualified)
        schema_before = local_report.schema_version
    except V26CopyGateError as error:
        error_code = str(error)
        return _write_gate_report(
            accepted=error_code == "sqlite_busy",
            status="busy" if error_code == "sqlite_busy" else "rejected",
            requested=locals().get("requested", ()),
            requested_columns=locals().get("requested_columns", ()),
            errors=(error_code,),
        )
    except Exception as error:
        return _write_gate_report(
            accepted=False,
            status="rejected",
            requested=locals().get("requested", ()),
            requested_columns=locals().get("requested_columns", ()),
            errors=(_copy_gate_error_code(error),),
        )

    if not enable:
        return _write_gate_report(
            accepted=True,
            status="disabled",
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_before,
            target_sidecars_before=target_sidecars_before,
            target_sidecars_after=target_sidecars_before,
            schema_before=schema_before,
            schema_after=schema_before,
            requested=requested,
            would_create=pending,
            created=(),
            requested_columns=requested_columns,
            would_add=tuple(pending_columns),
            added=(),
        )
    if dry_run:
        return _write_gate_report(
            accepted=True,
            status="dry_run",
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_before,
            target_sidecars_before=target_sidecars_before,
            target_sidecars_after=target_sidecars_before,
            schema_before=schema_before,
            schema_after=schema_before,
            requested=requested,
            would_create=pending,
            requested_columns=requested_columns,
            would_add=tuple(pending_columns),
            created=(),
        )

    if not pending and not pending_columns:
        # No write needs protection after every requested table already
        # satisfies the contract.  This keeps the gate idempotent when the
        # original pre-migration backup no longer hashes like the target.
        return _write_gate_report(
            accepted=True,
            status="already_applied",
            target_hash_before=target_hash_before,
            target_hash_after=target_hash_before,
            target_sidecars_before=target_sidecars_before,
            target_sidecars_after=target_sidecars_before,
            schema_before=schema_before,
            schema_after=schema_before,
            requested=requested,
            requested_columns=requested_columns,
            created=(),
        )

    backup_hash_before = ""
    backup_hash_after = ""
    backup_sidecars_before: Mapping[str, Any] = {}
    backup_sidecars_after: Mapping[str, Any] = {}
    backup_checked = False
    target_sidecars_after: Mapping[str, Any] = {}
    target_hash_after = ""
    schema_after: Optional[int] = None
    created: list[str] = []
    added: list[str] = []
    status = "rolled_back"
    errors: list[str] = []
    write_attempted = False
    gate_lock: _CopyGateFileLock | None = None

    try:
        if not expected_sha256:
            raise V26CopyGateError("expected_hash_required")
        normalized_hash = str(expected_sha256).strip().lower()
        if len(normalized_hash) != 64 or any(
            character not in "0123456789abcdef" for character in normalized_hash
        ):
            raise V26CopyGateError("expected_hash_invalid")
        if normalized_hash != target_hash_before:
            raise V26CopyGateError("target_hash_mismatch")

        backup = _validate_copy_path(backup_path, role="backup")
        try:
            if os.path.samefile(target, backup):
                raise V26CopyGateError("backup_must_be_distinct")
        except V26CopyGateError:
            raise
        except OSError as error:
            raise V26CopyGateError("backup_unreadable") from error
        backup_hash_before = _copy_digest(backup)
        backup_sidecars_before = _copy_sidecar_state(backup)
        backup_checked = True
        if backup_hash_before != target_hash_before:
            raise V26CopyGateError("backup_hash_mismatch")

        # Recheck the target immediately before taking the lock/write path so
        # an out-of-band replacement cannot satisfy the earlier hash check.
        if _copy_digest(target) != target_hash_before:
            raise V26CopyGateError("target_changed_before_write")
        if _copy_sidecar_state(target) != target_sidecars_before:
            raise V26CopyGateError("target_sidecars_changed_before_write")

        gate_lock = _CopyGateFileLock(target)
        if not gate_lock.acquire(timeout_ms):
            raise V26CopyGateError("file_lock_busy")
        write_attempted = True

        connection = None
        transaction_open = False
        try:
            connection = sqlite3.connect(
                str(target),
                timeout=timeout_ms / 1000.0,
                isolation_level=None,
            )
            connection.execute(f"PRAGMA busy_timeout = {timeout_ms}")
            connection.execute("BEGIN IMMEDIATE")
            transaction_open = True
            for table in pending:
                _create_incremental_table(connection, table)
                created.append(table)
            for qualified in pending_columns:
                table, column = qualified.split(".", 1)
                _add_incremental_column(
                    connection,
                    table,
                    column,
                    V26_INCREMENTAL_COLUMN_DEFS[table][column],
                )
                added.append(qualified)
            row = connection.execute(
                'SELECT "version" FROM "schema_version" LIMIT 1'
            ).fetchone()
            if row is None or int(row[0]) != 11:
                raise V26CopyGateError("target_schema_version_changed")
            connection.commit()
            transaction_open = False
        except BaseException:
            if transaction_open and connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            raise
        finally:
            if connection is not None:
                connection.close()
        status = "committed" if (created or added) else "already_applied"
    except V26CopyGateError as error:
        error_code = str(error)
        if error_code in {"file_lock_busy", "sqlite_busy"}:
            status = "busy"
        elif write_attempted:
            status = "rolled_back"
        else:
            status = "rejected"
        if write_attempted:
            created.clear()
            added.clear()
        errors.append(error_code)
    except sqlite3.OperationalError as error:
        status = "busy" if _is_sqlite_busy(error) else "rolled_back"
        created.clear()
        added.clear()
        errors.append(_copy_gate_error_code(error))
    except Exception as error:
        status = "rolled_back"
        created.clear()
        added.clear()
        errors.append(_copy_gate_error_code(error))
    finally:
        try:
            target_hash_after = _copy_digest(target)
            target_sidecars_after = _copy_sidecar_state(target)
            after_report = probe_schema(target, LOCAL_V11_SCHEMA)
            schema_after = after_report.schema_version
        except Exception as error:
            errors.append(_copy_gate_error_code(error))
        if backup_checked:
            try:
                backup_hash_after = _copy_digest(backup)
                backup_sidecars_after = _copy_sidecar_state(backup)
            except Exception as error:
                errors.append(_copy_gate_error_code(error))
        if gate_lock is not None:
            gate_lock.release()

    # A committed write is only reported as successful after the target still
    # presents the v11 version marker and the backup remains byte-identical.
    if status in {"committed", "already_applied"} and schema_after != 11:
        status = "verification_failed"
        errors.append("target_schema_version_changed")
    if status == "committed" and backup_checked and backup_hash_after != backup_hash_before:
        status = "verification_failed"
        errors.append("backup_changed")
    return _write_gate_report(
        accepted=True,
        status=status,
        target_hash_before=target_hash_before,
        target_hash_after=target_hash_after,
        backup_hash_before=backup_hash_before,
        backup_hash_after=backup_hash_after,
        target_sidecars_before=target_sidecars_before,
        target_sidecars_after=target_sidecars_after,
        backup_sidecars_before=backup_sidecars_before,
        backup_sidecars_after=backup_sidecars_after,
        schema_before=schema_before,
        schema_after=schema_after,
        requested=requested,
        would_create=pending,
        created=tuple(created),
        requested_columns=requested_columns,
        would_add=tuple(pending_columns),
        added=tuple(added),
        backup_checked=backup_checked,
        errors=errors,
    )


def v26_schema_contract() -> dict[str, tuple[str, ...]]:
    """Return a detached copy of the v26 expected schema mapping."""

    return {table: tuple(columns) for table, columns in V26_EXPECTED_SCHEMA.items()}


def schema_delta_from_v11() -> dict[str, tuple[str, ...]]:
    """Return v26 tables/columns absent from the local v11 baseline.

    The result is pure data for migration planning. It does not imply that an
    ``ALTER TABLE`` or data backfill is safe to run.
    """

    delta: dict[str, tuple[str, ...]] = {}
    for table, columns in V26_EXPECTED_SCHEMA.items():
        old = set(LOCAL_V11_SCHEMA.get(table, ()))
        additions = tuple(column for column in columns if column not in old)
        if additions:
            delta[table] = additions
    return delta


def probe_v26_schema(db_path: str | Path) -> V26SchemaReport:
    """Inspect an existing database against the v26 contract without mutation."""

    result: SchemaProbeResult = probe_schema(db_path, V26_EXPECTED_SCHEMA)
    return V26SchemaReport(
        schema_version=result.schema_version,
        missing_tables=result.missing_tables,
        missing_columns=result.missing_columns,
        errors=result.errors,
    )


def probe_v26_migration_plan(db_path: str | Path) -> V26MigrationPlan:
    """Probe *db_path* and return read-only v11-to-v26 migration evidence."""

    return build_v26_migration_plan(probe_v26_schema(db_path))


__all__ = [
    "LOCAL_V11_SCHEMA",
    "V26CopyGateError",
    "V26CopyWriteReport",
    "V26_COPY_MAX_BUSY_TIMEOUT_MS",
    "V26_COPY_MAX_BYTES",
    "V26_EXPECTED_SCHEMA",
    "V26_INCREMENTAL_COLUMN_BATCH",
    "V26_INCREMENTAL_COLUMN_DEFS",
    "V26_INCREMENTAL_TABLES",
    "V26_SCHEMA_VERSION",
    "apply_v26_copy_gate",
    "V26MigrationPlan",
    "V26SchemaReport",
    "build_v26_migration_plan",
    "probe_v26_migration_plan",
    "probe_v26_schema",
    "schema_delta_from_v11",
    "v26_schema_contract",
]
