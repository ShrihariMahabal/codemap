"""Helpers shared across the Frappe metadata sub-extractors.

Every extractor in this package follows the same contract: it is given a
``Path`` and it returns a ``{"nodes": [...], "edges": [...]}`` dict.  These
helpers centralise the repetitive bits — file reading, JSON parsing, and
the empty-result sentinel — so the sub-extractors stay focused on the
Frappe-specific structure they care about.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_json(path: Path) -> dict | list | None:
    """Read and parse a JSON file.

    Returns ``None`` for files that are missing, unreadable, or
    malformed.  The top-level value is returned verbatim — Frappe
    fixture files are sometimes single objects and sometimes JSON
    arrays of records, so callers must handle both shapes (or check
    :func:`isinstance` and bail out for the one they can't read).
    """
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def file_line_count(path: Path) -> int:
    """Return the number of lines in *path* (minimum 1).

    Used to set ``source_line_end`` on metadata nodes that span the whole
    file (DocType JSONs, modules.txt entries, etc.).  Falls back to 1 if
    the file can't be read.
    """
    try:
        with open(path, "rb") as f:
            return max(1, sum(1 for _ in f))
    except OSError:
        return 1


def empty_result() -> dict:
    """Return the standard empty extraction result."""
    return {"nodes": [], "edges": []}
