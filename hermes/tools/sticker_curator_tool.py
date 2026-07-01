"""Sticker Curator Tool — agent-collected sticker library.

Soyo can pick interesting images she sees in group chats, save them with an
emotion label, and reuse later via the same `[sticker:emotion]` syntax the
adapter already understands. Mirrors how a human saves funny/cute chat images
to use as their own stickers.

Storage layout:
    $SOYO_COLLECTION_ROOT/
        <emotion>/<YYYYMMDD_HHMMSS>_<short_hash>.<ext>
    $HERMES_HOME/soyo_sticker_collection.json   (index: emotion → paths)

Adapter hook: `_sticker_path(name)` in OneBotAdapter consults this index
FIRST for collected, then falls back to built-in chibi stickers.

Actions:
  curate    — copy a cached image to collection under an emotion label
  list      — list emotions with counts (or paths under one emotion)
  remove    — delete a collected sticker by path
  search    — fuzzy-find emotions matching a query
  stats     — overall collection stats
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

COLLECTION_ROOT = Path(os.getenv("SOYO_COLLECTION_ROOT", os.path.join(os.path.expanduser("~"), "Pictures", "soyo_collected")))
INDEX_PATH = Path(os.getenv("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes"))) / "soyo_sticker_collection.json"
MAX_PER_EMOTION = 20
MAX_TOTAL = 200
MAX_SOURCE_BYTES = 50 * 1024 * 1024  # 50MB — prevent OOM and disk-fill via curated sticker

# Allowlist of directories from which Soyo can curate images. Images she sees
# in group chat arrive via NapCat → adapter `_get_image_files` → cached via
# `cache_image_from_bytes` into IMAGE_CACHE_DIR. Paths outside these roots are
# rejected at curate time so a prompt-injected "curate ~/.ssh/id_rsa"
# cannot smuggle secret bytes out as a sticker image.
_ALLOWED_SOURCE_ROOTS = (
    Path(os.getenv("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes"))),     # cache/images/, state/, audio_cache/, ...
    Path("/tmp/"),                  # temp downloads at runtime
)

_EMOTION_NAME_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9_]{1,8}$")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def _load_index() -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    if not INDEX_PATH.exists():
        return {"emotions": {}}
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "emotions" in data:
            return data
    except Exception as e:
        logger.warning("[sticker_curator] failed to load index: %s", e)
    return {"emotions": {}}


def _save_index(idx: Dict[str, Any]) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(INDEX_PATH)


def _normalize_emotion(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("emotion name is required")
    if not _EMOTION_NAME_RE.match(name):
        raise ValueError(
            f"emotion '{name}' must be 1-8 chars of CJK/letters/digits/underscore"
        )
    return name.lower() if name.isascii() else name


def _ext_for(src: Path) -> str:
    ext = src.suffix.lower()
    if ext in _IMAGE_EXTS:
        return ext
    return ".jpg"


def _is_under_allowed(path: Path) -> bool:
    """True if resolved path lives under any allowed-source root.
    `resolve()` follows symlinks before comparison so a symlink pointing
    outside the allowlist can't escape validation.
    """
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    for root in _ALLOWED_SOURCE_ROOTS:
        try:
            if resolved.is_relative_to(root):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _total_count(idx: Dict[str, Any]) -> int:
    return sum(len(v) for v in idx.get("emotions", {}).values())


def _curate(emotion: str, source_path: str, note: Optional[str]) -> Dict[str, Any]:
    emotion = _normalize_emotion(emotion)
    src = Path(source_path).expanduser()
    if not src.is_file():
        raise ValueError(f"source image not found: {src}")
    if src.suffix.lower() not in _IMAGE_EXTS:
        raise ValueError(
            f"source must be an image ({', '.join(_IMAGE_EXTS)}), got: {src.suffix or '(none)'}"
        )
    if not _is_under_allowed(src):
        raise ValueError(
            f"source path is outside the allowed chat-cache roots; "
            f"only curate images you saw in chat"
        )
    try:
        size = src.stat().st_size
    except OSError as e:
        raise ValueError(f"cannot stat source: {e}") from e
    if size > MAX_SOURCE_BYTES:
        raise ValueError(
            f"source too large ({size // (1024*1024)}MB > "
            f"{MAX_SOURCE_BYTES // (1024*1024)}MB cap)"
        )
    idx = _load_index()
    if emotion in idx["emotions"] and len(idx["emotions"][emotion]) >= MAX_PER_EMOTION:
        raise ValueError(
            f"emotion '{emotion}' has reached cap {MAX_PER_EMOTION}; "
            "remove one before curating more"
        )
    if _total_count(idx) >= MAX_TOTAL:
        raise ValueError(
            f"collection full ({MAX_TOTAL}); remove unused stickers first"
        )
    COLLECTION_ROOT.mkdir(parents=True, exist_ok=True)
    (COLLECTION_ROOT / emotion).mkdir(parents=True, exist_ok=True)

    with src.open("rb") as f:
        digest = hashlib.md5(f.read()).hexdigest()[:8]
    unique_id = time.strftime("%Y%m%d_%H%M%S")
    dst = COLLECTION_ROOT / emotion / f"{unique_id}_{digest}{_ext_for(src)}"
    shutil.copy2(src, dst)

    entry = {
        "path": str(dst),
        "added_at": unique_id,
        "source": str(src),
        "note": (note or "").strip()[:200],
    }
    idx["emotions"].setdefault(emotion, []).append(entry)
    _save_index(idx)
    logger.info("[sticker_curator] curated %s → %s (emotion=%s)", src.name, dst, emotion)
    return {"success": True, "emotion": emotion, "path": str(dst)}


def _list(emotion: Optional[str]) -> Dict[str, Any]:
    idx = _load_index()
    emotions = idx.get("emotions", {})
    if emotion:
        emotion = _normalize_emotion(emotion)
        return {
            "emotion": emotion,
            "stickers": emotions.get(emotion, []),
            "count": len(emotions.get(emotion, [])),
        }
    return {
        "emotions": {
            e: {"count": len(v), "latest": v[-1]["added_at"] if v else ""}
            for e, v in emotions.items()
        },
        "total": _total_count(idx),
        "cap_total": MAX_TOTAL,
    }


def _remove(path: str) -> Dict[str, Any]:
    target = Path(path).expanduser()
    idx = _load_index()
    removed = False
    for emotion, entries in idx.get("emotions", {}).items():
        for i, e in enumerate(entries):
            if Path(e.get("path", "")) == target or e.get("path", "") == path:
                del entries[i]
                if not entries:
                    del idx["emotions"][emotion]
                removed = True
                break
        if removed:
            break
    if not removed:
        raise ValueError(f"no collected sticker at: {path}")
    if target.is_file():
        try:
            target.unlink()
        except OSError as e:
            logger.warning("[sticker_curator] couldn't delete %s: %s", target, e)
    _save_index(idx)
    return {"success": True, "removed": path}


def _search(query: str) -> Dict[str, Any]:
    q = (query or "").strip().lower()
    idx = _load_index()
    if not q:
        return {"matches": [], "total": _total_count(idx)}
    hits: List[Dict[str, Any]] = []
    for emotion, entries in idx.get("emotions", {}).items():
        score = 0
        if q in emotion.lower():
            score = 10
        else:
            for w in _split_cjk(q):
                if w in emotion.lower():
                    score += 3
        if any(q in (_e.get("note", "") or "").lower() for _e in entries):
            score = max(score, 2)
        if score > 0:
            hits.append({"emotion": emotion, "score": score, "count": len(entries)})
    hits.sort(key=lambda x: -x["score"])
    return {"matches": hits[:20], "total": _total_count(idx)}


_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


def _split_cjk(s: str) -> List[str]:
    return [m.group(0) for m in _CJK_RUN_RE.finditer(s)]


def _stats() -> Dict[str, Any]:
    idx = _load_index()
    emotions = idx.get("emotions", {})
    return {
        "total": _total_count(idx),
        "cap_total": MAX_TOTAL,
        "emotions_count": len(emotions),
        "top_emotions": sorted(
            ((e, len(v)) for e, v in emotions.items()),
            key=lambda x: -x[1],
        )[:10],
        "collection_root": str(COLLECTION_ROOT),
        "index_path": str(INDEX_PATH),
    }


def sticker_curator_tool(
    action: str,
    emotion: Optional[str] = None,
    image_path: Optional[str] = None,
    note: Optional[str] = None,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        if action == "curate":
            if not emotion or not image_path:
                return {"success": False, "error": "curate requires emotion and image_path"}
            return _curate(emotion, image_path, note)
        if action == "list":
            return _list(emotion)
        if action == "remove":
            if not image_path:
                return {"success": False, "error": "remove requires image_path"}
            return _remove(image_path)
        if action == "search":
            if not query:
                return {"success": False, "error": "search requires query"}
            return _search(query)
        if action == "stats":
            return _stats()
        return {"success": False, "error": f"unknown action: {action}"}
    except Exception as e:
        logger.warning("[sticker_curator] %s failed: %s", action, e, exc_info=True)
        return {"success": False, "error": str(e)}


SCHEMA: Dict[str, Any] = {
    "name": "sticker_curator",
    "description": (
        "保存群聊里看到的有趣图片作为自己的表情包，按情绪归类。"
        "发图时用 [sticker:情绪名] 调用——adapter 会先查这里再 fallback 到 built-in chibi stickers。\n"
        "Actions: curate 保存新图、list 看收藏、remove 删、search 找情绪、stats 总览。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["curate", "list", "remove", "search", "stats"],
                "description": "The curation action to perform.",
            },
            "emotion": {
                "type": "string",
                "description": (
                    "Emotion label for the sticker (curate/list). 1-8 chars of "
                    "CJK/letters/digits/underscore. Can be a new emotion Soyo invents, "
                    "e.g. '摆烂', '无语ed', '嘴角上扬'."
                ),
            },
            "image_path": {
                "type": "string",
                "description": (
                    "Absolute path to source image for curate (copy warm-path the "
                    "LLM sees in context like $HOME/.cache/.../xxx.jpg) or to "
                    "the collected file to delete for remove."
                ),
            },
            "note": {
                "type": "string",
                "description": "Optional 1-line context note (curate).",
            },
            "query": {
                "type": "string",
                "description": "Search query (search only).",
            },
        },
        "required": ["action"],
    },
}


from tools.registry import registry

registry.register(
    name="sticker_curator",
    toolset="memory",
    schema=SCHEMA,
    handler=lambda args, **kw: sticker_curator_tool(
        action=args.get("action", ""),
        emotion=args.get("emotion"),
        image_path=args.get("image_path"),
        note=args.get("note"),
        query=args.get("query"),
    ),
    emoji="🎨",
)