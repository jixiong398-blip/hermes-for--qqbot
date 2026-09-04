"""Canonical portability compatibility ports for the local SessionDB facade.

The local v11 export/import implementation remains authoritative.  This module
only gives upstream-compatible names to the already audited bounded helpers;
it never opens a database or changes a schema while being imported.
"""

from __future__ import annotations

try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    hermes_bootstrap = None  # type: ignore[assignment]

from typing import Any, Dict, Iterable, List, Mapping, Optional

from hermes_state_common import (
    _PREVIEW_ELIGIBLE_SQL,
    _PREVIEW_RAW_SELECT,
    _shape_preview,
    _sql_session_last_active,
)
from hermes_state_portability_compat import (
    IMPORT_MAX_MESSAGES_PER_SESSION,
    IMPORT_MAX_SESSIONS,
    IMPORT_MAX_SESSION_BYTES,
    IMPORT_MAX_TOTAL_BYTES,
    IMPORT_MAX_TOTAL_MESSAGES,
    MAX_SESSION_ID_CHARS,
    MESSAGE_EXPORT_FIELDS,
    PortabilityAudit,
    PortabilityImportResult,
    SESSION_EXPORT_FIELDS,
    audit_export_payload,
    import_sessions_into_db,
)


def audit_portable_export(payload: Any, **limits: Any) -> PortabilityAudit:
    """Audit an export without opening a file, connection, or host facade."""

    return audit_export_payload(payload, **limits)


def dry_run_import(
    payload: Any,
    *,
    max_sessions: int = IMPORT_MAX_SESSIONS,
    max_messages_per_session: int = IMPORT_MAX_MESSAGES_PER_SESSION,
    max_total_messages: int = IMPORT_MAX_TOTAL_MESSAGES,
) -> PortabilityImportResult:
    """Validate an export and report a copy-only import plan."""

    audit = audit_export_payload(
        payload,
        max_sessions=max_sessions,
        max_messages_per_session=max_messages_per_session,
        max_total_messages=max_total_messages,
    )
    if not audit.ok:
        return PortabilityImportResult(
            ok=False,
            status="rejected",
            errors=tuple({"error": message} for message in audit.errors),
            audit=audit,
        )
    return import_sessions_into_db(None, payload, enable=True, dry_run=True)


class SessionPortabilityMixin:
    """Mixin-compatible facade with explicit host delegation hooks.

    The class is intentionally safe when used by itself.  A host can expose
    ``_canonical_export_session``/``_canonical_export_all`` hooks to opt into
    the richer listing methods without importing the local facade here.
    """

    @staticmethod
    def audit_export_payload(payload: Any, **limits: Any) -> PortabilityAudit:
        return audit_export_payload(payload, **limits)

    @staticmethod
    def import_sessions_into_db(
        db: Any,
        payload: Any,
        *,
        enable: bool = False,
        dry_run: bool = True,
    ) -> PortabilityImportResult:
        return import_sessions_into_db(
            db,
            payload,
            enable=enable,
            dry_run=dry_run,
        )

    def export_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        hook = getattr(self, "_canonical_export_session", None)
        if not callable(hook):
            return None
        try:
            result = hook(session_id)
            return result if isinstance(result, dict) else None
        except Exception:
            return None

    def export_all(self, source: Optional[str] = None) -> List[Dict[str, Any]]:
        hook = getattr(self, "_canonical_export_all", None)
        if not callable(hook):
            return []
        try:
            result = hook(source=source)
            return result if isinstance(result, list) else []
        except Exception:
            return []

    def import_sessions(
        self,
        sessions: Any,
        *,
        enable: bool = False,
        dry_run: bool = True,
    ) -> PortabilityImportResult:
        hook = getattr(self, "_canonical_import_sessions", None)
        if callable(hook):
            try:
                result = hook(sessions, enable=enable, dry_run=dry_run)
                if isinstance(result, PortabilityImportResult):
                    return result
            except Exception:
                pass
        return import_sessions_into_db(
            self,
            sessions,
            enable=enable,
            dry_run=dry_run,
        )


__all__ = [
    "IMPORT_MAX_MESSAGES_PER_SESSION",
    "IMPORT_MAX_SESSIONS",
    "IMPORT_MAX_SESSION_BYTES",
    "IMPORT_MAX_TOTAL_BYTES",
    "IMPORT_MAX_TOTAL_MESSAGES",
    "MAX_SESSION_ID_CHARS",
    "MESSAGE_EXPORT_FIELDS",
    "PortabilityAudit",
    "PortabilityImportResult",
    "SESSION_EXPORT_FIELDS",
    "SessionPortabilityMixin",
    "_PREVIEW_ELIGIBLE_SQL",
    "_PREVIEW_RAW_SELECT",
    "_shape_preview",
    "_sql_session_last_active",
    "audit_export_payload",
    "audit_portable_export",
    "dry_run_import",
    "import_sessions_into_db",
]
