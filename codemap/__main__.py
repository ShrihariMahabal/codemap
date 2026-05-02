"""CLI entry point for codemap.

Usage:
    python -m codemap <app_path>          # Full detection
    python -m codemap --update <app_path> # Incremental (changed files only)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .detect import detect
from .manifest import detect_incremental, save_manifest


def _print_detection_result(result: dict) -> None:
    """Pretty-print the detection result to stdout."""
    print(f"\n  codemap — file detection")
    print(f"  {'─' * 40}")
    print(f"  app root:  {result['app_root']}")
    print()

    files = result["files"]
    for ftype, file_list in sorted(files.items()):
        count = len(file_list)
        if count > 0:
            print(f"  {ftype:<20s} {count:>5d}")

    print(f"  {'─' * 40}")
    print(f"  {'total':<20s} {result['total_files']:>5d}")

    if result.get("incremental"):
        new_total = result.get("new_total", 0)
        deleted = result.get("deleted_files", [])
        print(f"\n  incremental: {new_total} changed, {len(deleted)} deleted")

    if result["skipped_sensitive"]:
        print(f"\n  ⚠ skipped {len(result['skipped_sensitive'])} sensitive file(s)")

    if result["codemapignore_patterns"]:
        print(f"  .codemapignore: {result['codemapignore_patterns']} pattern(s) active")

    print()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="codemap",
        description="Knowledge graph generator for Frappe Framework apps.",
    )
    parser.add_argument(
        "app_path",
        help="Path to the Frappe app root (e.g. apps/erpnext).",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Incremental mode — only show files changed since last run.",
    )

    args = parser.parse_args(argv)

    app_path = Path(args.app_path)
    if not app_path.is_dir():
        print(f"error: {app_path} is not a directory", file=sys.stderr)
        sys.exit(1)

    result = detect(app_path)

    if args.update:
        manifest_path = str(app_path / "codemap-out" / "manifest.json")
        result = detect_incremental(result, manifest_path)
        _print_detection_result(result)
        save_manifest(result["files"], manifest_path)
    else:
        _print_detection_result(result)


if __name__ == "__main__":
    main()
