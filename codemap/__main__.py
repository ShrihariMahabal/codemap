"""CLI entry point for codemap.

Usage:
    python -m codemap detect <app_path>           # File detection
    python -m codemap detect --update <app_path>  # Incremental detection
    python -m codemap extract <app_path>          # Full extraction
"""

from __future__ import annotations

import argparse
import json
import sys
import time
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


def _cmd_detect(args: argparse.Namespace) -> None:
    """Run file detection."""
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


def _cmd_extract(args: argparse.Namespace) -> None:
    """Run detection + extraction for Python, JS, and Vue files."""
    from .cache import load_cached, save_cached
    from .extract_js import extract_js
    from .extract_python import extract_python
    from .extract_vue import extract_vue
    from .resolve import resolve_cross_file

    app_path = Path(args.app_path)
    if not app_path.is_dir():
        print(f"error: {app_path} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Step 1: Detect files
    result = detect(app_path)
    _print_detection_result(result)

    all_results: list[dict] = []
    stats: dict[str, dict[str, int]] = {}
    start = time.time()

    # Step 2: Extract each language
    extractors = [
        ("Python", "code_py", extract_python),
        ("JavaScript", "code_js", extract_js),
        ("Vue", "code_vue", extract_vue),
    ]

    for lang_name, file_key, extractor_fn in extractors:
        files = result["files"].get(file_key, [])
        if not files:
            continue

        print(f"  extracting {len(files)} {lang_name} files...")
        cached_count = 0
        extracted_count = 0

        for fpath in files:
            p = Path(fpath)

            cached = load_cached(p, app_path)
            if cached is not None:
                all_results.append(cached)
                cached_count += 1
                continue

            extraction = extractor_fn(p)
            save_cached(p, extraction, app_path)
            all_results.append(extraction)
            extracted_count += 1

        stats[lang_name] = {
            "extracted": extracted_count,
            "cached": cached_count,
            "total": len(files),
        }

    elapsed = time.time() - start

    # Step 3: Cross-file resolution
    new_nodes, new_edges = resolve_cross_file(all_results)

    # Step 4: Collect stats
    total_nodes = sum(len(r.get("nodes", [])) for r in all_results) + len(new_nodes)
    total_edges = sum(len(r.get("edges", [])) for r in all_results) + len(new_edges)
    total_raw = sum(len(r.get("raw_calls", [])) for r in all_results)

    print(f"\n  codemap — extraction")
    print(f"  {'─' * 40}")
    for lang_name, s in stats.items():
        print(f"  {lang_name:<12s}  {s['extracted']:>4d} extracted, {s['cached']:>4d} cached")
    print(f"  {'─' * 40}")
    print(f"  {'nodes':<20s} {total_nodes:>5d}")
    print(f"  {'edges':<20s} {total_edges:>5d}")
    print(f"  {'cross-file calls':<20s} {len(new_edges):>5d}  (resolved from {total_raw} raw)")
    print(f"  {'time':<20s} {elapsed:>5.1f}s")
    print()

    # Step 5: Save merged extraction to codemap-out/
    out_dir = app_path / "codemap-out"
    out_dir.mkdir(parents=True, exist_ok=True)

    merged_nodes = []
    merged_edges = []
    for r in all_results:
        merged_nodes.extend(r.get("nodes", []))
        merged_edges.extend(r.get("edges", []))
    merged_nodes.extend(new_nodes)
    merged_edges.extend(new_edges)

    extraction_out = {
        "nodes": merged_nodes,
        "edges": merged_edges,
    }
    out_path = out_dir / "extraction.json"
    out_path.write_text(json.dumps(extraction_out, indent=2), encoding="utf-8")
    print(f"  saved → {out_path}")
    print()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="codemap",
        description="Knowledge graph generator for Frappe Framework apps.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # detect subcommand
    detect_parser = subparsers.add_parser(
        "detect", help="Discover and classify files in a Frappe app.",
    )
    detect_parser.add_argument("app_path", help="Path to the Frappe app root.")
    detect_parser.add_argument(
        "--update", action="store_true",
        help="Incremental mode — only show files changed since last run.",
    )

    # extract subcommand
    extract_parser = subparsers.add_parser(
        "extract", help="Extract Python AST from a Frappe app.",
    )
    extract_parser.add_argument("app_path", help="Path to the Frappe app root.")

    args = parser.parse_args(argv)

    if args.command == "detect":
        _cmd_detect(args)
    elif args.command == "extract":
        _cmd_extract(args)


if __name__ == "__main__":
    main()
