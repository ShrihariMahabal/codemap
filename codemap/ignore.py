""".codemapignore file loading and matching.

Walks upward from the scan root to the nearest .git boundary,
collecting ignore patterns from every .codemapignore file found.
Uses gitignore-style glob matching.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path


def load_ignore_patterns(root: Path) -> list[tuple[Path, str]]:
    """Read .codemapignore files from *root* and its ancestors.

    Walks upward from ``root`` towards the filesystem root, stopping at
    the nearest ``.git`` boundary. Each non-blank, non-comment line is
    collected as a (anchor_dir, pattern) pair.

    Args:
        root: The directory to start searching from.

    Returns:
        A list of (anchor_dir, pattern) tuples. ``anchor_dir`` is the
        directory where the .codemapignore was found; patterns are
        matched relative to both ``root`` and ``anchor_dir``.
    """
    patterns: list[tuple[Path, str]] = []
    current = root.resolve()

    while True:
        ignore_file = current / ".codemapignore"
        if ignore_file.is_file():
            for line in ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append((current, line))

        # Stop once we've processed the directory containing .git
        if (current / ".git").exists():
            break

        parent = current.parent
        if parent == current:
            break  # filesystem root
        current = parent

    return patterns


def is_ignored(
    path: Path,
    root: Path,
    patterns: list[tuple[Path, str]],
) -> bool:
    """Return True if *path* matches any loaded ignore pattern.

    Each pattern is tested against the path relative to the scan root
    and (if different) relative to the anchor directory where the
    .codemapignore was found.
    """
    if not patterns:
        return False

    for anchor, pattern in patterns:
        clean = pattern.strip("/")
        if not clean:
            continue

        # Try matching relative to the scan root
        try:
            rel = str(path.relative_to(root)).replace(os.sep, "/")
            if _matches(rel, path.name, clean):
                return True
        except ValueError:
            pass

        # Try matching relative to the anchor directory
        if anchor != root:
            try:
                rel = str(path.relative_to(anchor)).replace(os.sep, "/")
                if _matches(rel, path.name, clean):
                    return True
            except ValueError:
                pass

    return False


def _matches(rel_path: str, basename: str, pattern: str) -> bool:
    """Check if a relative path matches a single glob pattern.

    Matches are tested in order of specificity:
    1. Full relative path against the pattern.
    2. Basename against the pattern (for patterns like ``*.pyc``).
    3. Each path component and prefix against the pattern
       (for patterns like ``node_modules``).
    """
    if fnmatch.fnmatch(rel_path, pattern):
        return True
    if fnmatch.fnmatch(basename, pattern):
        return True

    parts = rel_path.split("/")
    for i, part in enumerate(parts):
        if fnmatch.fnmatch(part, pattern):
            return True
        if fnmatch.fnmatch("/".join(parts[: i + 1]), pattern):
            return True

    return False
