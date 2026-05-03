"""Per-file extraction cache using content-addressable hashing.

Each file's extraction result is stored at:
    codemap-out/cache/{sha256}.json

The SHA256 is computed from file contents + relative path, so:
- Identical content at different paths gets separate cache entries.
- Moving a checkout to a different machine doesn't invalidate caches
  (because we use relative paths, not absolute).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


DEFAULT_CACHE_DIR = "codemap-out/cache"


def file_hash(path: Path, root: Path) -> str:
    """Compute SHA256 of file contents + relative path.

    The relative path ensures two files with identical content at
    different locations get distinct cache keys.
    """
    p = Path(path)
    if not p.is_file():
        raise IsADirectoryError(f"file_hash requires a file, got: {p}")

    h = hashlib.sha256()
    h.update(p.read_bytes())
    h.update(b"\x00")  # separator between content and path

    try:
        rel = p.resolve().relative_to(Path(root).resolve())
        h.update(str(rel).encode())
    except ValueError:
        # File is outside root — fall back to absolute path
        h.update(str(p.resolve()).encode())

    return h.hexdigest()


def _cache_dir(root: Path) -> Path:
    """Return the cache directory, creating it if needed."""
    d = Path(root).resolve() / DEFAULT_CACHE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_cached(path: Path, root: Path) -> dict | None:
    """Load cached extraction result for a file, or None if stale/missing.

    Returns the cached dict (with 'nodes' and 'edges' keys) if the
    file's content hash matches an existing cache entry.
    """
    try:
        h = file_hash(path, root)
    except OSError:
        return None

    entry = _cache_dir(root) / f"{h}.json"
    if not entry.exists():
        return None

    try:
        return json.loads(entry.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_cached(path: Path, result: dict, root: Path) -> None:
    """Save extraction result keyed by file content hash.

    Uses atomic write (write to .tmp, then os.replace) to avoid
    corrupted cache entries from interrupted writes.
    """
    p = Path(path)
    if not p.is_file():
        return

    h = file_hash(p, root)
    entry = _cache_dir(root) / f"{h}.json"
    tmp = entry.with_suffix(".tmp")

    try:
        tmp.write_text(json.dumps(result), encoding="utf-8")
        os.replace(tmp, entry)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def clear_cache(root: Path) -> int:
    """Delete all cached extraction results. Returns count of files removed."""
    d = _cache_dir(root)
    count = 0
    for f in d.glob("*.json"):
        f.unlink()
        count += 1
    return count
