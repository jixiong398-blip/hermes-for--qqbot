"""
Obsidian Vault Adapter — local markdown knowledge base with graph-aware search.

Key features:
  - Parse Obsidian markdown: YAML frontmatter, [[wikilinks]], #tags
  - Graph-based retrieval: linked notes get relevance boost
  - Full-text search across all notes
  - Automatic re-indexing on file changes (mtime tracking)
  - MOC (Map of Content) detection and priority boosting
  - Integration with wiki.py indexing + memory gateway
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class ObsidianNote:
    """Parsed representation of an Obsidian markdown note."""

    def __init__(self, path: Path, vault_root: Path):
        self.path = path
        self.rel_path = str(path.relative_to(vault_root))
        self.title = path.stem
        self.content = ""
        self.frontmatter: Dict = {}
        self.tags: List[str] = []
        self.wikilinks: List[str] = []       # [[target]]
        self.backlinks: List[str] = []       # notes that link to this
        self.headings: List[str] = []
        self.mtime: float = 0.0
        self.is_moc: bool = False
        self._loaded: bool = False

    def load(self) -> bool:
        """Parse the markdown file. Returns True if content changed."""
        try:
            raw = self.path.read_text(encoding="utf-8", errors="replace")
        except UnicodeDecodeError:
            try:
                raw = self.path.read_text(encoding="gbk", errors="replace")
            except Exception:
                return False
        except Exception:
            return False

        old_hash = hashlib.md5(self.content.encode()).hexdigest() if self.content else ""
        new_hash = hashlib.md5(raw.encode()).hexdigest()
        if old_hash == new_hash and self._loaded:
            return False

        self._loaded = True
        self.mtime = self.path.stat().st_mtime

        # Parse YAML frontmatter
        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', raw, re.DOTALL)
        body = raw
        if fm_match:
            body = raw[fm_match.end():]
            self.frontmatter = self._parse_simple_yaml(fm_match.group(1))
            self.tags = self.frontmatter.get("tags", [])
            if isinstance(self.tags, str):
                self.tags = [t.strip() for t in self.tags.split(",")]

        self.content = body

        # Extract wikilinks [[target]] and [[target|alias]]
        self.wikilinks = []
        for m in re.finditer(r'\[\[([^\]|#]+?)(?:[|#][^\]]+)?\]\]', body):
            target = m.group(1).strip()
            if target and target not in self.wikilinks:
                self.wikilinks.append(target)

        # Extract headings
        self.headings = re.findall(r'^#{1,3}\s+(.+)$', body, re.MULTILINE)

        # Extract inline #tags from body (not in frontmatter)
        inline_tags = re.findall(r'(?<!\w)#([a-zA-Z\u4e00-\u9fff][\w\u4e00-\u9fff/-]+)', body)
        for t in inline_tags:
            if t not in self.tags:
                self.tags.append(t)

        # Detect MOC (Map of Content): many wikilinks + heading "索引" or "index"
        link_density = len(self.wikilinks) / max(1, len(body.split("\n")))
        has_moc_heading = any("索引" in h or "index" in h.lower() for h in self.headings)
        self.is_moc = link_density > 0.1 or has_moc_heading

        return True

    def _parse_simple_yaml(self, text: str) -> Dict:
        """Simple YAML parser for frontmatter (no pyyaml dependency)."""
        result = {}
        for line in text.strip().split("\n"):
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                # Parse list: [a, b, c]
                if val.startswith("[") and val.endswith("]"):
                    val = [v.strip().strip('"').strip("'")
                           for v in val[1:-1].split(",") if v.strip()]
                result[key] = val
        return result

    def snippet(self, max_len: int = 300) -> str:
        """Get a content snippet for display."""
        # Remove markdown syntax for clean snippet
        clean = re.sub(r'[#*`\[\]\(\)|>]', '', self.content[:max_len * 2])
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean[:max_len]


class ObsidianVault:
    """Obsidian vault knowledge base with graph-aware search.

    Usage:
        vault = ObsidianVault(Path("~/knowledge"))
        vault.index()  # Initial indexing
        results = vault.search("transformer architecture", top_k=5)
        for note, score in results:
            print(f"{note.title}: {score:.2f}")
    """

    def __init__(self, vault_path: Path):
        self.vault_path = Path(vault_path)
        self.notes: Dict[str, ObsidianNote] = {}  # keyed by title
        self.notes_by_path: Dict[str, ObsidianNote] = {}  # keyed by rel_path

    def index(self, force: bool = False) -> Dict[str, int]:
        """Index all markdown files in the vault.

        Returns: {"added": N, "updated": N, "skipped": N}
        """
        if not self.vault_path.exists():
            logger.warning("Vault path not found: %s", self.vault_path)
            return {"added": 0, "updated": 0, "skipped": 0}

        stats = {"added": 0, "updated": 0, "skipped": 0}

        for md_file in self.vault_path.rglob("*.md"):
            # Skip .obsidian and template dirs
            if any(p.startswith(".") for p in md_file.parts):
                continue
            if "templates" in md_file.parts:
                continue

            rel = str(md_file.relative_to(self.vault_path))
            is_new = rel not in self.notes_by_path

            note = ObsidianNote(md_file, self.vault_path)
            changed = note.load()

            if is_new:
                stats["added"] += 1
            elif changed:
                stats["updated"] += 1
            else:
                stats["skipped"] += 1

            self.notes[note.title] = note
            self.notes_by_path[rel] = note

        # Build backlinks
        self._build_backlinks()

        logger.info("Vault indexed: %d notes (added=%d, updated=%d, skipped=%d)",
                     len(self.notes), stats["added"], stats["updated"], stats["skipped"])
        return stats

    def _build_backlinks(self):
        """Compute backlinks for all notes."""
        # Clear old backlinks
        for note in self.notes.values():
            note.backlinks = []

        # For each wikilink, find the target note and add a backlink
        for note in self.notes.values():
            for link_target in note.wikilinks:
                # Try exact title match
                if link_target in self.notes:
                    self.notes[link_target].backlinks.append(note.title)
                else:
                    # Try fuzzy match (case-insensitive, no extension)
                    for title, target_note in self.notes.items():
                        if (title.lower() == link_target.lower() or
                                title.lower().startswith(link_target.lower())):
                            target_note.backlinks.append(note.title)
                            break

    def search(self, query: str, top_k: int = 5,
               graph_boost: float = 0.3) -> List[Tuple[ObsidianNote, float]]:
        """Search the vault with hybrid keyword + graph scoring.

        Args:
            query: Search query
            top_k: Number of results
            graph_boost: How much to boost linked notes (0-1)

        Returns:
            List of (note, relevance_score) sorted by score descending
        """
        if not self.notes:
            return []

        query_lower = query.lower()
        query_terms = re.findall(r'[\w\u4e00-\u9fff]+', query_lower)
        scores: Dict[str, float] = {}

        for title, note in self.notes.items():
            score = 0.0
            content_lower = note.content.lower()
            title_lower = title.lower()

            # Title match (strong signal)
            if query_lower in title_lower:
                score += 3.0
            elif any(t in title_lower for t in query_terms):
                score += 1.5

            # Content keyword match
            if query_lower in content_lower:
                score += 2.0
            term_matches = sum(1 for t in query_terms if t in content_lower)
            score += term_matches * 0.3

            # Tag match
            tag_matches = sum(1 for t in query_terms if any(t in tag.lower() for tag in note.tags))
            score += tag_matches * 0.5

            # Heading match
            heading_matches = sum(1 for t in query_terms if any(t in h.lower() for h in note.headings))
            score += heading_matches * 0.4

            # MOC boost (important hub notes)
            if note.is_moc:
                score *= 1.2

            if score > 0:
                scores[title] = score

        # Graph boost: notes that link to high-scoring notes get boosted
        if graph_boost > 0:
            boosted: Dict[str, float] = {}
            for title, score in scores.items():
                note = self.notes.get(title)
                if note:
                    for bl_title in note.backlinks:
                        if bl_title not in scores:
                            boosted[bl_title] = boosted.get(bl_title, 0) + score * graph_boost
            for title, boost in boosted.items():
                scores[title] = scores.get(title, 0.1) + boost

        # Sort and return top-k
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return [(self.notes[title], score) for title, score in ranked[:top_k]
                if title in self.notes]

    def get_note(self, title: str) -> Optional[ObsidianNote]:
        """Get a note by title (exact or fuzzy)."""
        if title in self.notes:
            return self.notes[title]
        # Fuzzy match
        title_lower = title.lower()
        for t, note in self.notes.items():
            if t.lower() == title_lower or title_lower in t.lower():
                return note
        return None

    def get_linked_notes(self, title: str) -> List[ObsidianNote]:
        """Get all notes that this note links to."""
        note = self.get_note(title)
        if not note:
            return []
        linked = []
        for link_target in note.wikilinks:
            target = self.get_note(link_target)
            if target:
                linked.append(target)
        return linked

    def get_backlinks(self, title: str) -> List[ObsidianNote]:
        """Get all notes that link to this note."""
        note = self.get_note(title)
        if not note:
            return []
        return [self.notes[bl] for bl in note.backlinks if bl in self.notes]

    def get_graph_context(self, title: str, depth: int = 1) -> List[ObsidianNote]:
        """Get a note plus its linked notes (graph neighborhood)."""
        seen: Set[str] = set()
        result: List[ObsidianNote] = []

        note = self.get_note(title)
        if not note:
            return []

        queue = [(note, 0)]
        while queue:
            current, d = queue.pop(0)
            if current.title in seen:
                continue
            seen.add(current.title)
            result.append(current)

            if d < depth:
                for link_title in current.wikilinks:
                    linked = self.get_note(link_title)
                    if linked and linked.title not in seen:
                        queue.append((linked, d + 1))

        return result

    def build_search_context(self, query: str, max_chars: int = 3000) -> str:
        """Build a context string for LLM prompt injection."""
        results = self.search(query, top_k=5)
        if not results:
            return ""

        lines = ["## Obsidian 知识库\n"]
        total = 0

        for note, score in results:
            tags_str = f" [{', '.join(note.tags[:5])}]" if note.tags else ""
            link_str = ""
            if note.wikilinks:
                link_str = f" (链接: {', '.join(note.wikilinks[:3])})"
            moc_tag = " [MOC]" if note.is_moc else ""

            header = f"### {note.title}{moc_tag}{tags_str}{link_str}\n"
            snippet = note.snippet(300)

            chunk = header + snippet + "\n"
            if total + len(chunk) > max_chars:
                break
            lines.append(chunk)
            total += len(chunk)

        return "\n".join(lines)

    def stats(self) -> Dict:
        """Get vault statistics."""
        total_links = sum(len(n.wikilinks) for n in self.notes.values())
        total_backlinks = sum(len(n.backlinks) for n in self.notes.values())
        all_tags = set()
        for n in self.notes.values():
            all_tags.update(n.tags)

        return {
            "vault_path": str(self.vault_path),
            "total_notes": len(self.notes),
            "total_links": total_links,
            "total_backlinks": total_backlinks,
            "moc_notes": sum(1 for n in self.notes.values() if n.is_moc),
            "unique_tags": len(all_tags),
            "tags": sorted(all_tags)[:20],
        }
