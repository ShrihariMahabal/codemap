"""Mtime manifest for incremental file detection.

Stores the modification time of every file processed in the last run.
On subsequent runs, only files with a newer mtime are re-processed.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_MANIFEST_PATH = "codemap-out/manifest.json"


def load_manifest(manifest_path: str = DEFAULT_MANIFEST_PATH) -> dict[str, float]:
    """Load the file modification time manifest from a previous run.

    Returns an empty dict if the manifest doesn't exist or is corrupt.
    """
    try:
        return json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_manifest(
    files: dict[str, list[str]],
    manifest_path: str = DEFAULT_MANIFEST_PATH,
) -> None:
    """Save current file mtimes for the next incremental run.

    Args:
        files: The ``files`` dict from ``detect()`` output — maps
               FileType values to lists of absolute file paths.
        manifest_path: Where to write the manifest JSON.
    """
    manifest: dict[str, float] = {}
    for file_list in files.values():
        for f in file_list:
            try:
                manifest[f] = Path(f).stat().st_mtime
            except OSError:
                pass  # File deleted between detect() and save — skip

    Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(manifest_path).write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )


def detect_incremental(
    full_result: dict,
    manifest_path: str = DEFAULT_MANIFEST_PATH,
) -> dict:
    """Filter a full detection result to only new or modified files.

    Compares current file mtimes against the stored manifest.
    Adds ``incremental``, ``new_files``, ``unchanged_files``, and
    ``deleted_files`` keys to the result dict.

    Args:
        full_result: The output of ``detect()``.
        manifest_path: Path to the manifest from the previous run.

    Returns:
        The same dict, augmented with incremental diff info.
    """
    manifest = load_manifest(manifest_path)

    if not manifest:
        # No previous run — everything is new
        full_result["incremental"] = True
        full_result["new_files"] = dict(full_result["files"])
        full_result["unchanged_files"] = {k: [] for k in full_result["files"]}
        full_result["deleted_files"] = []
        full_result["new_total"] = full_result["total_files"]
        return full_result

    new_files: dict[str, list[str]] = {k: [] for k in full_result["files"]}
    unchanged_files: dict[str, list[str]] = {k: [] for k in full_result["files"]}

    for ftype, file_list in full_result["files"].items():
        for f in file_list:
            stored_mtime = manifest.get(f)
            try:
                current_mtime = Path(f).stat().st_mtime
            except OSError:
                current_mtime = 0.0

            if stored_mtime is None or current_mtime > stored_mtime:
                new_files[ftype].append(f)
            else:
                unchanged_files[ftype].append(f)

    # Files in the old manifest that no longer exist
    current_files = {f for flist in full_result["files"].values() for f in flist}
    deleted_files = [f for f in manifest if f not in current_files]

    full_result["incremental"] = True
    full_result["new_files"] = new_files
    full_result["unchanged_files"] = unchanged_files
    full_result["deleted_files"] = deleted_files
    full_result["new_total"] = sum(len(v) for v in new_files.values())

    return full_result
