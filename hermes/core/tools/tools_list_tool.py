"""tools_list — Agent 自查询工具

让 Agent 能查看当前注册了哪些工具、可用状态如何。
读取 ToolRegistry 实时数据，不硬编码任何工具列表。
"""

import json
from typing import Any, Dict, List

from tools.registry import registry, _check_fn_cached


TOOLS_LIST_SCHEMA: Dict[str, Any] = {
    "name": "tools_list",
    "description": (
        "List all currently registered tools with their availability status. "
        "Use this when you're unsure what capabilities you have. "
        "Returns: tool name, toolset, available (yes/no), description. "
        "Optionally filter by toolset name."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "toolset": {
                "type": "string",
                "description": "Filter by toolset name (e.g. 'web', 'terminal', 'skills'). Omit to list all.",
            },
        },
        "required": [],
    },
}


def tools_list(toolset: str = "") -> str:
    """Return a JSON listing of all registered tools with availability."""
    entries = registry._snapshot_entries()
    toolset_checks = registry._snapshot_toolset_checks()

    results: List[Dict[str, Any]] = []
    for entry in entries:
        # Filter by toolset if specified
        if toolset and entry.toolset != toolset:
            continue

        # Check availability: toolset-level check + tool-level check
        available = True
        unavailable_reason = ""

        # Toolset-level check
        ts_check = toolset_checks.get(entry.toolset)
        if ts_check is not None:
            available = _check_fn_cached(ts_check)

        # Tool-level check (overrides toolset if more specific)
        if available and entry.check_fn is not None:
            available = _check_fn_cached(entry.check_fn)

        # Extract description from schema
        desc = ""
        if entry.description:
            desc = entry.description
        elif entry.schema and isinstance(entry.schema, dict):
            desc = entry.schema.get("description", "")

        results.append({
            "name": entry.name,
            "toolset": entry.toolset,
            "available": available,
            "description": desc[:120] if desc else "",
            "emoji": entry.emoji or "",
        })

    # Sort: available first, then by toolset, then by name
    results.sort(key=lambda r: (not r["available"], r["toolset"], r["name"]))

    summary = {
        "total": len(results),
        "available": sum(1 for r in results if r["available"]),
        "unavailable": sum(1 for r in results if not r["available"]),
    }

    return json.dumps({
        "success": True,
        "summary": summary,
        "tools": results,
    }, ensure_ascii=False, indent=2)


# ── Registration ────────────────────────────────────────────────────────────

registry.register(
    name="tools_list",
    toolset="core",
    schema=TOOLS_LIST_SCHEMA,
    handler=lambda args, **kw: tools_list(toolset=args.get("toolset", "")),
    emoji="🔧",
)
