"""File discovery and Frappe-aware classification.

Walks a Frappe app directory, classifies every file into a ``FileType``,
and returns a structured result dict that downstream phases consume.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .filetype import FileType
from .ignore import is_ignored, load_ignore_patterns
from .security import is_sensitive


# ── Skip rules ──────────────────────────────────────────────────────────────

# Directories that are never useful to traverse.
SKIP_DIRS: set[str] = {
    # Python
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".eggs", "*.egg-info",
    "venv", ".venv", "env", ".env",
    "site-packages",
    # JS
    "node_modules",
    # Build artifacts
    "dist", "build",
    # Version control
    ".git",
    # Frappe-specific noise
    "locale", "patches", "change_log", "cypress",
    # Our own output
    "codemap-out",
}

# Files that are never useful to index.
SKIP_FILES: set[str] = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock",
}


# ── Frappe-aware classification ─────────────────────────────────────────────

def _is_doctype_json(path: Path) -> bool:
    """Return True if *path* is a DocType schema JSON.

    A DocType JSON lives at ``*/doctype/{name}/{name}.json`` and contains
    ``"doctype": "DocType"`` at the top level.  We check the path pattern
    first (cheap) and only read the file if it matches.
    """
    parts = path.parts
    # Pattern: .../doctype/{name}/{name}.json
    # Need at least 3 parts: [..., "doctype", "{name}", "{name}.json"]
    if len(parts) < 3:
        return False
    if parts[-3] != "doctype":
        return False
    expected_name = parts[-2]
    if path.stem != expected_name:
        return False

    # Path pattern matches — confirm by reading the JSON
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("doctype") == "DocType"
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return False


def _is_report_json(path: Path) -> bool:
    """Return True if *path* is a Report definition JSON.

    A Report JSON lives at ``*/report/{name}/{name}.json`` and contains
    ``"doctype": "Report"`` at the top level.
    """
    parts = path.parts
    if len(parts) < 3:
        return False
    if parts[-3] != "report":
        return False
    expected_name = parts[-2]
    if path.stem != expected_name:
        return False

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("doctype") == "Report"
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return False


def _is_hooks_py(path: Path, app_root: Path) -> bool:
    """Return True if *path* is the app-level hooks.py.

    In a Frappe app, ``hooks.py`` sits directly inside the app package
    directory: ``{app_pkg}/hooks.py``.  We only match this specific
    location — not nested copies like ``tests/hooks.py``.
    """
    if path.name != "hooks.py":
        return False
    # hooks.py should be exactly one level below app_root
    # e.g. app_root = apps/erpnext, hooks at apps/erpnext/erpnext/hooks.py
    # We detect this by checking that the parent is a direct child of app_root
    # OR the parent IS app_root (for flat layouts).
    try:
        rel = path.relative_to(app_root)
        # Expected: {app_pkg}/hooks.py → 2 parts
        return len(rel.parts) == 2
    except ValueError:
        return False


def _is_dashboard_py(path: Path) -> bool:
    """Return True if *path* is a DocType dashboard configuration.

    Dashboard files are named ``{doctype}_dashboard.py`` and live inside
    ``*/doctype/{name}/`` directories.
    """
    if not path.name.endswith("_dashboard.py"):
        return False
    # Must be inside a doctype directory
    return "doctype" in path.parts


def _is_modules_txt(path: Path, app_root: Path) -> bool:
    """Return True if *path* is the app's modules.txt."""
    if path.name != "modules.txt":
        return False
    try:
        rel = path.relative_to(app_root)
        return len(rel.parts) == 2
    except ValueError:
        return False


def _has_real_content(path: Path) -> bool:
    """Return True if a Python __init__.py has meaningful content.

    Empty or near-empty __init__.py files (just docstrings or encoding
    declarations) are skipped to reduce graph noise.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return False

    # Strip encoding declaration and docstrings
    lines = [
        line for line in text.splitlines()
        if line.strip()
        and not line.strip().startswith("#")
        and not line.strip().startswith('"""')
        and not line.strip().startswith("'''")
    ]
    return len(lines) > 0


def classify_file(path: Path, app_root: Path) -> FileType | None:
    """Classify a single file into a Frappe-aware FileType.

    Returns None if the file should be skipped (e.g. lock files,
    empty __init__.py, or unrecognised extensions).

    Classification priority:
    1. Frappe metadata files (hooks, doctype json, etc.) — most specific.
    2. Source code files by extension.
    3. Documentation files.
    """
    name = path.name
    ext = path.suffix.lower()

    # ── Frappe metadata (checked first — more specific than extension) ──

    if name == "modules.txt" and _is_modules_txt(path, app_root):
        return FileType.MODULES_TXT

    if name == "hooks.py" and _is_hooks_py(path, app_root):
        return FileType.HOOKS

    if ext == ".json":
        if _is_doctype_json(path):
            return FileType.DOCTYPE_JSON
        if _is_report_json(path):
            return FileType.REPORT_JSON
        return None  # Other JSON files (package.json, etc.) are not useful

    if _is_dashboard_py(path):
        return FileType.DASHBOARD

    # ── Source code ──

    if ext == ".py":
        # Skip empty __init__.py files
        if name == "__init__.py" and not _has_real_content(path):
            return None
        return FileType.CODE_PY

    if ext == ".js":
        return FileType.CODE_JS

    if ext == ".vue":
        return FileType.CODE_VUE

    # ── Documentation ──

    if ext in (".md", ".txt", ".rst"):
        return FileType.DOCUMENT

    return None


# ── Directory skip logic ────────────────────────────────────────────────────

def _should_skip_dir(dirname: str) -> bool:
    """Return True if this directory should never be traversed."""
    if dirname.startswith("."):
        return True
    if dirname in SKIP_DIRS:
        return True
    if dirname.endswith(".egg-info"):
        return True
    if dirname.endswith("_venv") or dirname.endswith("_env"):
        return True
    return False


# ── Main detection ──────────────────────────────────────────────────────────

def detect(root: str | Path) -> dict:
    """Walk a Frappe app directory and classify every file.

    Args:
        root: Path to the Frappe app root (e.g. ``apps/erpnext``).
              This should be the directory containing ``pyproject.toml``
              or ``setup.py`` and the app package directory.

    Returns:
        A dict with:
        - ``files``: dict mapping FileType values to lists of absolute paths.
        - ``total_files``: total number of classified files.
        - ``app_root``: resolved absolute path to the app root.
        - ``skipped_sensitive``: list of paths skipped for security.
        - ``codemapignore_patterns``: number of active ignore patterns.
    """
    app_root = Path(root).resolve()
    ignore_patterns = load_ignore_patterns(app_root)

    files: dict[str, list[str]] = {ft.value: [] for ft in FileType}
    skipped_sensitive: list[str] = []

    for dirpath, dirnames, filenames in os.walk(app_root, followlinks=False):
        dp = Path(dirpath)

        # Prune directories in-place so os.walk never descends into them
        dirnames[:] = [
            d for d in dirnames
            if not _should_skip_dir(d)
            and not is_ignored(dp / d, app_root, ignore_patterns)
        ]

        for fname in filenames:
            if fname in SKIP_FILES:
                continue

            fpath = dp / fname
            if is_ignored(fpath, app_root, ignore_patterns):
                continue
            if is_sensitive(fpath):
                skipped_sensitive.append(str(fpath))
                continue

            ftype = classify_file(fpath, app_root)
            if ftype is not None:
                files[ftype.value].append(str(fpath))

    total_files = sum(len(v) for v in files.values())

    return {
        "files": files,
        "total_files": total_files,
        "app_root": str(app_root),
        "skipped_sensitive": skipped_sensitive,
        "codemapignore_patterns": len(ignore_patterns),
    }
