# SessionDB v11 -> v26 Migration Map

> This is an implementation map, not an authorization to migrate a live
> database. The local product remains on `SCHEMA_VERSION=11` until every row
> below has a copied-database replay, rollback evidence, and Windows/Linux
> verification.

## Baseline

The local database has four core objects in its authoritative `SCHEMA_SQL`:

- `schema_version`;
- `sessions`;
- `messages`;
- `state_meta`.

The upstream v26 contract is captured in
`core/hermes_state_v26_compat.py`. `SessionDB.probe_v26_compatibility()` is
read-only and must be run before any future write gate.

## Table Mapping

| v26 object | Local v11 source or default | Migration action | Risk / gate |
|---|---|---|---|
| `schema_version` | Existing single row, value 11 | Advance only after all data steps succeed | Never bump version as a substitute for migration |
| `state_meta` | Existing key/value table | Preserve keys; reserve v26 markers under a namespaced prefix | Key collision with local FTS/WAL/EPI state |
| `system_prompts` | `sessions.system_prompt` | Deduplicate by stable hash, then retain original session text until dual-read verification | Content hash collision, Unicode normalization |
| `session_model_usage` | Session token/cost columns and message metadata | Aggregate by session/model/task; missing historical values become null/zero by field contract | Double counting retries and compressed lineage |
| `gateway_routing` | No local durable equivalent; runtime session store is authoritative | Create only after routing-key/session-id dual-read design | OneBot group isolation and profile boundaries must not be replaced |
| `gateway_hygiene_state` | Gateway in-memory hygiene counters | Backfill only from explicit durable evidence; otherwise initialize zero | Stale counters can trigger premature compression |
| `compression_locks` | Local process/thread lock and compression state | Introduce as a separate lease table; do not reuse OneBot group lock | Cross-process stale lock and clock skew |
| `session_turn_leases` | `gateway.turn_lease.SessionTurnLeaseRegistry` is currently in-memory | Add durable lease only after owner identity/expiry protocol is frozen | Must not create a second active turn or strand shutdown spool |
| `async_delegations` | Local delegate observations and delivery ledger are separate | Import only explicit delegation records with bounded JSON and ownership | Do not inject delegation result JSON into QQ transcript |

## `sessions` Additions

Existing local columns remain canonical. The following v26 columns are
additive and initially nullable or have a documented safe default:

| Fields | Source / default | Gate |
|---|---|---|
| `session_key`, `chat_id`, `chat_type`, `thread_id`, `display_name`, `origin_json` | Gateway source metadata; null when historical row has no provenance | OneBot session key must remain distinct from durable `id` |
| `expiry_finalized` | false/0 for rows without an explicit expiry event | Must not cause an unsolicited memory flush |
| `system_prompt_hash` | Hash of existing `system_prompt` | Must use same normalization as `system_prompts` |
| `cwd`, `git_branch`, `git_repo_root`, `git_metadata_generation` | Runtime snapshot if recorded; null otherwise | Paths are diagnostics only and must be sanitized before public output |
| `title_source` | `"import"` or null for old title rows | Do not rewrite user-visible titles during migration |
| `last_activity_at`, `last_activity_description`, `last_activity_provenance` | Max known message timestamp, then `started_at`; description/provenance null | Must not change local session expiry semantics until dual-read |
| `handoff_state`, `handoff_platform`, `handoff_error` | null / `"none"` | Handoff state cannot authorize a platform send by itself |
| `compression_failure_cooldown_until`, `compression_failure_error`, `compression_fallback_streak`, `compression_ineffective_count` | zero/null | Preserve local compression retry/backoff behavior |
| `profile_name` | active profile only for newly created rows; null for old rows | Never infer one profile for historical mixed data |
| `rewind_count`, `archived`, `pinned`, `hidden`, `last_read_at` | zero/false/null | UI metadata must not alter transcript inclusion or authorization |

## `messages` Additions

| Fields | Source / default | Gate |
|---|---|---|
| `effect_disposition` | null for historical messages | Must not reinterpret tool effects or run commands |
| `platform_message_id` | OneBot `message_id` where separately stored; null otherwise | Keep raw platform id out of session id and FTS syntax |
| `observed` | true for imported canonical rows unless evidence says otherwise | Do not mark synthetic recovery rows as observed |
| `_compressed_summary` | null; only compression writer may set | Must preserve local lineage and active watermark |
| `active` | true for current messages | Rewind/restore must be dual-read before changing search/context |
| `compacted` | false for historical rows | Never hide messages merely because a summary exists |
| `api_content` | null unless provider-normalized content was durably recorded | Keep user-visible `content` and provider payload separate |
| `display_kind`, `display_metadata` | null / bounded JSON | Must not leak raw OneBot payloads into ordinary search/context |

## Derived Indexes And Triggers

FTS is not a canonical data migration. The order is fixed:

1. Verify canonical `messages` row count/content hash on a copied database.
2. Probe current FTS tables/triggers and record a stale/rebuild plan.
3. Build any v26 external-content/trigram/CJK index on a temporary name.
4. Compare search results and snippets with the local v11 baseline.
5. Atomically swap only after readers are quiesced and a rollback copy exists.

A failed FTS operation must leave canonical messages readable and must not
advance the main schema version. No `VACUUM`, `DROP TABLE`, or WAL surgery is
allowed in the first v26 migration slice.

## Lineage, Locks, And Import

- `parent_session_id` remains the only durable lineage edge until a v26
  adoption protocol is proven. Missing/cyclic parents detach; they never delete
  the child or its messages.
- Compression locks, turn leases, OneBot group locks, and delivery-ledger
  claims are separate state machines. A migration must not merge their tables
  or use one expiry field for another.
- Import is copy-only, bounded, idempotent, and transactional. Unknown v26
  fields remain in the audit report until an explicit projection is reviewed.
- Provider credentials, active ownership, gateway permissions, and live
  delivery claims are never reconstructed from a portable transcript alone.

## Replay Sequence

1. Run `python scripts/sessiondb_replay.py --source <copied-state.db>` against
   an untouched source copy; record the report hash, source hash, WAL/SHM state,
   quick-check result and v11/v26 plan.
2. Use `--query` only for bounded canonical search summaries. The tool never
   emits session/message bodies, and it refuses the current runtime `state.db`.
3. Review the bounded export audit for size, field, JSON, and lineage limits;
   treat `export_capture.truncated=true` as incomplete evidence.
4. Replay into a disposable v11 target through the existing disabled/dry-run
   importer; compare counts, content decode, parent edges, and search results.
5. Build a disposable v26-shaped target from the mapping above; compare only
   additive fields and derived indexes.
6. Run concurrent reader/writer, WAL fallback, FTS corruption, shutdown-spool,
   and OneBot transcript tests on both OS families.
7. Produce a rollback report. Only then propose a separately reviewed v26
   schema write gate.

The replay report is diagnostic evidence only: `read_only=true`,
`write_gate_open=false`, `rollback_evidence.write_operations=0`, and the source
hash must remain unchanged. Reports may be written with
`--output <report.json>`; the writer uses a temporary file plus atomic replace
and rejects overwriting the source copy.

## Explicit Non-Goals

- No production database is migrated by this document.
- No `SCHEMA_VERSION` change is implied.
- No upstream `hermes_state.py` or mixin file is copied over the local facade.
- No OneBot routing, memory provider, delivery ledger, or shutdown behavior is
  changed by the mapping itself.
