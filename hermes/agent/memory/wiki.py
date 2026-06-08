"""
Wiki Knowledge Base — Karpathy's LLM Wiki integration.

Fetches, parses, chunks, and indexes the karpathy/llm-wiki GitHub repository
or similar markdown knowledge bases. Provides automatic cross-referencing
with user queries and periodic sync.

Sources:
  - https://github.com/karpathy/llm-wiki (primary)
  - Custom wiki directories (configurable)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.error import URLError

from .store import MemoryStore, WikiEntry

logger = logging.getLogger(__name__)

# Karpathy LLM Wiki GitHub API
KARPATHY_WIKI_API = "https://api.github.com/repos/karpathy/llm101n/contents"
KARPATHY_RAW_BASE = "https://raw.githubusercontent.com/karpathy/llm101n/main"
KARPATHY_WIKI_REPO = "https://github.com/karpathy/llm101n"

CHUNK_SIZE = 2000  # Characters per wiki chunk
CHUNK_OVERLAP = 200  # Character overlap between chunks


class WikiKnowledgeBase:
    """Manages external wiki knowledge base integration."""

    def __init__(self, store: MemoryStore,
                 wiki_dirs: Optional[List[Path]] = None,
                 github_repos: Optional[List[str]] = None):
        self._store = store
        self._wiki_dirs = wiki_dirs or []
        self._github_repos = github_repos or [KARPATHY_WIKI_REPO]
        self._last_sync: Dict[str, float] = {}

    def fetch_github_wiki(self, repo_url: str) -> List[Dict]:
        """Fetch all markdown files from a GitHub wiki repository.
        Returns list of dicts with: path, title, content."""
        api_url = repo_url.replace("https://github.com/", "https://api.github.com/repos/") + "/contents"
        raw_base = repo_url.replace("https://github.com/", "https://raw.githubusercontent.com/") + "/main"

        try:
            req = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github.v3+json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                contents = json.loads(resp.read().decode())
        except (URLError, json.JSONDecodeError) as e:
            logger.warning("Failed to fetch GitHub wiki from %s: %s", repo_url, e)
            return []

        documents = []
        for item in contents:
            if item.get("type") != "file":
                continue
            name = item.get("name", "")
            if not name.endswith((".md", ".mdx", ".txt")):
                continue

            download_url = item.get("download_url", "")
            if not download_url:
                download_url = f"{raw_base}/{name}"

            try:
                req = urllib.request.Request(download_url)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    content = resp.read().decode("utf-8", errors="replace")
            except URLError as e:
                logger.warning("Failed to download %s: %s", name, e)
                continue

            title = name.replace(".md", "").replace(".mdx", "").replace(".txt", "")
            title = title.replace("-", " ").replace("_", " ").title()

            documents.append({
                "path": name,
                "title": title,
                "content": content,
                "source_url": download_url,
            })

        logger.info("Fetched %d documents from %s", len(documents), repo_url)
        return documents

    def fetch_local_wiki(self, wiki_dir: Path) -> List[Dict]:
        """Fetch all markdown files from a local directory."""
        if not wiki_dir.exists():
            logger.warning("Wiki directory not found: %s", wiki_dir)
            return []

        documents = []
        for md_file in wiki_dir.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8", errors="replace")
                title = md_file.stem.replace("-", " ").replace("_", " ").title()
                documents.append({
                    "path": str(md_file.relative_to(wiki_dir)),
                    "title": title,
                    "content": content,
                    "source_url": f"file://{md_file}",
                })
            except Exception as e:
                logger.warning("Failed to read %s: %s", md_file, e)

        logger.info("Fetched %d documents from %s", len(documents), wiki_dir)
        return documents

    def chunk_document(self, title: str, content: str, source_url: str) -> List[WikiEntry]:
        """Split a document into overlapping chunks for indexing."""
        sentences = re.split(r'(?<=[.!?。！？\n])\s+', content)
        chunks = []
        current = ""
        chunk_idx = 0

        for sentence in sentences:
            if len(current) + len(sentence) > CHUNK_SIZE and current:
                chunks.append(current.strip())
                # Overlap: keep last part
                overlap_text = current[-CHUNK_OVERLAP:] if len(current) > CHUNK_OVERLAP else current
                current = overlap_text + " " + sentence
            else:
                current += " " + sentence if current else sentence

        if current.strip():
            chunks.append(current.strip())

        entries = []
        for i, chunk in enumerate(chunks):
            tags = self._extract_tags(title, chunk)
            content_hash = hashlib.sha256(chunk.encode()).hexdigest()[:16]

            entries.append(WikiEntry(
                source_url=source_url,
                title=title,
                section=self._detect_section(chunk),
                content=chunk,
                chunk_index=i,
                tags=tags,
                embedding_hash=content_hash,
                created_at=datetime.now(timezone.utc).timestamp(),
                updated_at=datetime.now(timezone.utc).timestamp(),
            ))

        return entries

    def _extract_tags(self, title: str, content: str) -> List[str]:
        """Extract relevant tags from title and content."""
        tags = set()

        # Title words as tags
        title_words = re.findall(r'\b[a-zA-Z]{3,}\b', title.lower())
        tags.update(title_words[:5])

        # Common AI/ML keywords
        ai_keywords = [
            "transformer", "attention", "llm", "gpt", "bert", "fine-tuning",
            "training", "inference", "tokenization", "embedding", "vector",
            "rag", "retrieval", "prompt", "chain", "agent", "memory",
            "rlhf", "ppo", "dpo", "lora", "qlora", "quantization",
            "diffusion", "vae", "gan", "lstm", "rnn", "cnn",
            "openai", "anthropic", "claude", "gemini", "mistral",
        ]
        content_lower = content.lower()
        for kw in ai_keywords:
            if kw in content_lower or kw in title.lower():
                tags.add(kw)

        return list(tags)[:10]

    def _detect_section(self, content: str) -> str:
        """Detect the first heading in a chunk as its section."""
        match = re.search(r'^#{1,3}\s+(.+?)$', content, re.MULTILINE)
        return match.group(1) if match else ""

    def sync(self, force: bool = False) -> Dict[str, int]:
        """Synchronize all configured wiki sources.
        Returns stats: {"added": N, "updated": N, "skipped": N}."""
        stats = {"added": 0, "updated": 0}

        # Sync GitHub repos
        for repo_url in self._github_repos:
            documents = self.fetch_github_wiki(repo_url)
            for doc in documents:
                entries = self.chunk_document(doc["title"], doc["content"], doc["source_url"])
                for entry in entries:
                    self._store.upsert_wiki(entry)
                    stats["added"] += 1

        # Sync local directories
        for wiki_dir in self._wiki_dirs:
            documents = self.fetch_local_wiki(wiki_dir)
            for doc in documents:
                entries = self.chunk_document(doc["title"], doc["content"], doc["source_url"])
                for entry in entries:
                    self._store.upsert_wiki(entry)
                    stats["added"] += 1

        self._last_sync["all"] = datetime.now(timezone.utc).timestamp()
        logger.info("Wiki sync complete: added=%d", stats["added"])
        return stats

    def search(self, query: str, limit: int = 5) -> List[WikiEntry]:
        """Search the wiki for relevant knowledge."""
        results = self._store.search_wiki(query, limit)
        for r in results:
            self._store.record_wiki_retrieval(r.id)
        return results

    def get_relevant_context(self, query: str, max_chars: int = 2000) -> str:
        """Get wiki context relevant to a query, bounded by max_chars."""
        results = self.search(query, limit=5)
        if not results:
            return ""

        lines = ["## Wiki Knowledge\n"]
        total = 0
        seen_titles: Set[str] = set()

        for r in results:
            if r.title in seen_titles:
                continue
            seen_titles.add(r.title)

            snippet = r.content[:500]
            section_suffix = f" > {r.section}" if r.section else ""
            chunk = f"### {r.title}{section_suffix}\n{snippet}\n"

            if total + len(chunk) > max_chars:
                break
            lines.append(chunk)
            total += len(chunk)

        return "\n".join(lines)

    def auto_context_injection(self, user_message: str) -> str:
        """Automatically find and return relevant wiki context for a user message."""
        # Extract potential query terms
        query_terms = re.findall(r'\b[a-zA-Z]{4,}\b', user_message.lower())
        if not query_terms:
            return ""

        query = " OR ".join(query_terms[:5])
        return self.get_relevant_context(query)

    def get_stats(self) -> Dict:
        """Get wiki knowledge base statistics."""
        return self._store.get_wiki_stats()

    def clear(self):
        """Clear all wiki entries."""
        self._store.clear_wiki()
