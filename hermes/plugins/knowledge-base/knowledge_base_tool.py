"""
Knowledge Base Tool — obsidian_search / obsidian_read
for the Nagasaki Soyo knowledge base at ~/knowledge/
"""
from __future__ import annotations

import logging, os, re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

KNOWLEDGE_ROOT = Path.home() / "ai/ai/knowledge"


def knowledge_search(query: str, limit: int = 5) -> str:
    """Full-text search across all .md files in the knowledge base."""
    if not KNOWLEDGE_ROOT.is_dir():
        return json.dumps({"error": f"Knowledge base not found at {KNOWLEDGE_ROOT}"})

    results = []
    keywords = query.lower().split()
    for md in sorted(KNOWLEDGE_ROOT.rglob("*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="replace").lower()
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                rel = str(md.relative_to(KNOWLEDGE_ROOT))
                title = md.stem
                snippet = _extract_snippet(text, keywords[0], 120) if keywords else text[:120]
                results.append((score, rel, title, snippet))
        except Exception:
            pass

    results.sort(key=lambda x: x[0], reverse=True)
    top = results[:limit]

    if not top:
        return json.dumps({"results": [], "query": query, "hint": "Try different keywords"})

    out = []
    for _, rel, title, snippet in top:
        out.append({"title": title, "path": rel, "snippet": snippet})

    vault_stats = {
        "total_md": sum(1 for _ in KNOWLEDGE_ROOT.rglob("*.md")),
        "categories": [d.name for d in sorted(KNOWLEDGE_ROOT.iterdir()) if d.is_dir() and not d.name.startswith(".")],
    }
    return json.dumps({"results": out, "query": query, "vault": vault_stats}, ensure_ascii=False)


def knowledge_read(path_or_title: str) -> str:
    """Read full content of a specific knowledge base file by path or title."""
    if not KNOWLEDGE_ROOT.is_dir():
        return json.dumps({"error": f"Knowledge base not found at {KNOWLEDGE_ROOT}"})

    q = path_or_title.strip()
    md_files = list(KNOWLEDGE_ROOT.rglob("*.md"))

    # Exact path match
    target = KNOWLEDGE_ROOT / q
    if target.is_file() and target.suffix == ".md":
        return _read_md(target)

    # Case-insensitive path match
    for f in md_files:
        if str(f.relative_to(KNOWLEDGE_ROOT)).lower() == q.lower():
            return _read_md(f)

    # Title match (filename without .md)
    for f in md_files:
        if f.stem.lower() == q.lower():
            return _read_md(f)

    # Fuzzy filename match
    q_lower = q.lower()
    for f in md_files:
        name = f.stem.lower()
        if q_lower in name or name in q_lower:
            return _read_md(f)

    # Full-text title search in first line (# Title)
    for f in md_files:
        try:
            first = f.read_text(encoding="utf-8", errors="replace").split("\n")[0]
            if first.startswith("# ") and q_lower in first.lower():
                return _read_md(f)
        except Exception:
            pass

    return json.dumps({"error": f"Not found: {q}", "hint": f"Try knowledge_search first. {len(md_files)} files available."})


def _read_md(path: Path) -> str:
    content = path.read_text(encoding="utf-8", errors="replace")
    rel = str(path.relative_to(KNOWLEDGE_ROOT))
    lines = content.split("\n")
    title = lines[0].replace("# ", "") if lines and lines[0].startswith("# ") else path.stem
    return json.dumps({
        "title": title,
        "path": rel,
        "content": content[:8000],
        "lines": len(lines),
        "truncated": len(content) > 8000,
    }, ensure_ascii=False)


def _extract_snippet(text: str, keyword: str, width: int = 120) -> str:
    idx = text.find(keyword)
    if idx < 0:
        return text[:width]
    start = max(0, idx - width // 2)
    end = min(len(text), idx + width // 2)
    snip = text[start:end].replace("\n", " ").strip()
    if start > 0:
        snip = "..." + snip
    if end < len(text):
        snip = snip + "..."
    return snip


# ── Tool schemas for Hermes Agent ──

OBSIDIAN_SEARCH_SCHEMA = {
    "name": "knowledge_search",
    "description": (
        f"搜索知识库（{sum(1 for _ in KNOWLEDGE_ROOT.rglob('*.md'))} 篇笔记）。"
        "返回匹配的标题、路径和内容摘要。找到感兴趣的笔记后，用 knowledge_read 读取全文。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
        },
        "required": ["query"],
    },
}

OBSIDIAN_READ_SCHEMA = {
    "name": "knowledge_read",
    "description": "读取知识库中指定笔记的全文。传入文件名（不含.md）或相对路径。",
    "parameters": {
        "type": "object",
        "properties": {
            "path_or_title": {"type": "string", "description": "文件名或路径"},
        },
        "required": ["path_or_title"],
    },
}
