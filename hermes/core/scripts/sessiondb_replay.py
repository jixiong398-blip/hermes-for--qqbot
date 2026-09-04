"""Run the read-only SessionDB history replay contract.

Examples (from the ``core`` directory)::

    python scripts/sessiondb_replay.py --source C:/path/to/copied-state.db
    python scripts/sessiondb_replay.py --source copied.db --query memory --query "OneBot" --output replay.json

The source must be an explicitly selected database copy. The command never
migrates, imports, rebuilds FTS, or writes the source database.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Allow ``python core/scripts/sessiondb_replay.py`` from the distribution root
# as well as ``python scripts/sessiondb_replay.py`` from the core directory.
_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    pass

from hermes_state_replay import (
    ReplayInputError,
    run_replay,
    write_replay_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="explicit copied SQLite database")
    parser.add_argument("--query", action="append", default=[], help="bounded canonical search query (repeatable)")
    parser.add_argument("--output", help="optional JSON report path; stdout when omitted")
    parser.add_argument(
        "--tolerate-wal-shm-read-locks",
        action="store_true",
        help=(
            "tolerate SQLite -shm read-lock changes in a disposable WAL copy; "
            "main/WAL/journal stability is still required"
        ),
    )
    args = parser.parse_args(argv)

    try:
        report = run_replay(
            args.source,
            search_terms=args.query,
            tolerate_wal_shm_read_locks=args.tolerate_wal_shm_read_locks,
        )
        if args.output:
            output = write_replay_report(report, args.output)
            print(json.dumps({"status": report.status, "report": output.name}, ensure_ascii=False))
        else:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report.ok else 1
    except (ReplayInputError, OSError, ValueError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)[:500]}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
