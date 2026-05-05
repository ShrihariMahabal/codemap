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
#
# Note: ``patches`` and ``fixtures`` are intentionally NOT skipped — patches
# define migration functions we want to index, and fixtures contain custom
# fields, property setters, workflows, and other graph-relevant records.
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
    "locale", "change_log", "cypress",
    # Our own output
    "codemap-out",
}

# Files that are never useful to index.
SKIP_FILES: set[str] = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock",
}


# ── JSON record classification ──────────────────────────────────────────────

# Maps the top-level ``"doctype"`` field of a record JSON to the FileType
# we classify it as.  Anything not in this map and not a DocType definition
# falls back to RECORD_JSON.
_RECORD_TYPE_MAP: dict[str, FileType] = {
    "Workflow": FileType.WORKFLOW_JSON,
    "Notification": FileType.NOTIFICATION_JSON,
    "Server Script": FileType.SERVER_SCRIPT_JSON,
    "Client Script": FileType.CLIENT_SCRIPT_JSON,
    "Print Format": FileType.PRINT_FORMAT_JSON,
    "Web Form": FileType.WEB_FORM_JSON,
    "Page": FileType.PAGE_JSON,
    "Custom Field": FileType.CUSTOM_FIELD_JSON,
    "Property Setter": FileType.PROPERTY_SETTER_JSON,
    "Report": FileType.REPORT_JSON,
}


def _read_json_doctype(path: Path) -> str | None:
    """Return the value of the top-level ``"doctype"`` key, or None.

    Frappe fixture files are typically lists of records — each element
    carries its own ``"doctype"`` key.  We treat a list whose first
    element declares a doctype as a record file of that kind.
    Files that aren't valid JSON or don't expose a doctype anywhere
    return ``None`` and are left unclassified.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None

    if isinstance(data, dict):
        kind = data.get("doctype")
        return kind if isinstance(kind, str) else None

    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            kind = first.get("doctype")
            return kind if isinstance(kind, str) else None

    return None


def _is_doctype_path(path: Path) -> bool:
    """Return True if *path* matches the DocType JSON path convention.

    A DocType JSON lives at ``*/doctype/{name}/{name}.json``.  We check
    the path pattern as a cheap pre-filter before reading the file.
    """
    parts = path.parts
    if len(parts) < 3:
        return False
    if parts[-3] != "doctype":
        return False
    return path.stem == parts[-2]


def _classify_json(path: Path) -> FileType | None:
    """Classify a ``.json`` file by reading its top-level ``"doctype"`` key.

    Priority: DocType definitions (path-aware) > known record kinds >
    generic record JSON.  Files without a ``"doctype"`` key (e.g.
    ``package.json``) are left unclassified.
    """
    kind = _read_json_doctype(path)
    if kind is None:
        return None

    if kind == "DocType" and _is_doctype_path(path):
        return FileType.DOCTYPE_JSON

    mapped = _RECORD_TYPE_MAP.get(kind)
    if mapped is not None:
        return mapped

    return FileType.RECORD_JSON


# ── Frappe-aware classification ─────────────────────────────────────────────

def _is_hooks_py(path: Path, app_root: Path) -> bool:
    """Return True if *path* is the app-level hooks.py.

    In a Frappe app, ``hooks.py`` sits directly inside the app package
    directory: ``{app_pkg}/hooks.py``.  We only match this specific
    location — not nested copies like ``tests/hooks.py``.
    """
    if path.name != "hooks.py":
        return False
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


def _is_patches_txt(path: Path, app_root: Path) -> bool:
    """Return True if *path* is the app's patches.txt."""
    if path.name != "patches.txt":
        return False
    try:
        rel = path.relative_to(app_root)
        return len(rel.parts) == 2
    except ValueError:
        return False


def _is_bundle_js(path: Path) -> bool:
    """Return True for ``*.bundle.js`` / ``*.bundle.scss`` entry points.

    Frappe and ERPNext use ``foo.bundle.js`` as a convention for build
    entry points compiled by esbuild.  These reference many other files
    and behave differently from regular ``.js`` modules.
    """
    name = path.name
    return name.endswith(".bundle.js") or name.endswith(".bundle.scss")


def _has_real_content(path: Path) -> bool:
    """Return True if a Python __init__.py has meaningful content.

    Empty or near-empty __init__.py files (just docstrings or encoding
    declarations) are skipped to reduce graph noise.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return False

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
    1. App-level fixed filenames (modules.txt, hooks.py, patches.txt) —
       these match by exact name and location.
    2. JSON record classification by ``"doctype"`` key.
    3. Source code by extension.
    4. Templates / stylesheets / documentation.
    """
    name = path.name
    ext = path.suffix.lower()

    if name == "modules.txt" and _is_modules_txt(path, app_root):
        return FileType.MODULES_TXT

    if name == "patches.txt" and _is_patches_txt(path, app_root):
        return FileType.PATCHES_TXT

    if name == "hooks.py" and _is_hooks_py(path, app_root):
        return FileType.HOOKS

    if ext == ".json":
        return _classify_json(path)

    if _is_dashboard_py(path):
        return FileType.DASHBOARD

    if _is_bundle_js(path):
        return FileType.BUNDLE_JS

    if ext == ".py":
        if name == "__init__.py" and not _has_real_content(path):
            return None
        return FileType.CODE_PY

    if ext == ".js":
        return FileType.CODE_JS

    if ext == ".vue":
        return FileType.CODE_VUE

    if ext == ".html":
        return FileType.TEMPLATE_HTML

    if ext in (".scss", ".css"):
        return FileType.STYLE_SCSS

    if ext in (".md", ".rst"):
        return FileType.DOCUMENT

    if ext == ".txt":
        # Generic .txt files fall through as documentation; modules.txt
        # and patches.txt are matched by name above.
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
