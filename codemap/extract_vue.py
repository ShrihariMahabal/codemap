"""Vue SFC extraction.

Extracts the <script> or <script setup> block from .vue files and
parses it as JavaScript using the JS extractor. Template blocks are
not parsed (no useful graph edges in HTML templates).
"""

from __future__ import annotations

import re
from pathlib import Path

from .extract_js import extract_js as _extract_js_from_source
from .graph_primitives import make_id, make_node


# Match <script> or <script setup> blocks, capturing the content.
# Uses re.DOTALL so . matches newlines.
_SCRIPT_PATTERN = re.compile(
    r"<script(?:\s+setup)?[^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)


def _find_script_block(source: str) -> tuple[str, int] | None:
    """Find the <script> block content and its line offset.

    Returns (script_content, line_offset) or None if no script block.
    The line_offset is the number of lines before the script content,
    used to adjust source_line_start/end in extracted nodes.
    """
    match = _SCRIPT_PATTERN.search(source)
    if not match:
        return None

    content = match.group(1)
    # Count lines before the script content to compute offset
    prefix = source[:match.start(1)]
    line_offset = prefix.count("\n")

    return content, line_offset


def extract_vue(path: Path) -> dict:
    """Extract entities from a Vue SFC file.

    Finds the <script> or <script setup> block, parses its content
    as JavaScript, and adjusts all line numbers by the block offset.
    """
    try:
        source_text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"error": str(e), "nodes": [], "edges": [], "raw_calls": []}

    str_path = str(path)
    stem = path.stem

    # Create file node regardless
    file_nid = make_id(str_path)
    file_node = make_node(
        file_nid, path.name, "file", str_path,
        1, source_text.count("\n") + 1,
    )

    result = _find_script_block(source_text)
    if not result:
        # Vue file with no script block — just return the file node
        return {"nodes": [file_node], "edges": [], "raw_calls": []}

    script_content, line_offset = result

    # Write script content to a temp file and extract as JS
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8",
    ) as tmp:
        tmp.write(script_content)
        tmp.flush()
        js_result = _extract_js_from_source(Path(tmp.name))

    if "error" in js_result:
        return {"nodes": [file_node], "edges": [], "raw_calls": []}

    # Rewrite all paths and adjust line numbers
    for node in js_result["nodes"]:
        node["source_file"] = str_path
        node["source_line_start"] += line_offset
        node["source_line_end"] += line_offset

    for edge in js_result["edges"]:
        edge["source_file"] = str_path
        # Adjust source_location "L42" → "L{42+offset}"
        loc = edge.get("source_location", "")
        if loc.startswith("L"):
            try:
                lineno = int(loc[1:]) + line_offset
                edge["source_location"] = f"L{lineno}"
            except ValueError:
                pass

    for call in js_result.get("raw_calls", []):
        call["source_file"] = str_path
        call["line"] += line_offset

    # Replace the temp file's file node with the real Vue file node
    js_result["nodes"] = [
        file_node if n["file_type"] == "file" else n
        for n in js_result["nodes"]
    ]

    # Fix node IDs to point to the real path, not the tmp path
    old_file_nid = make_id(str(tmp.name))
    new_file_nid = file_nid

    if old_file_nid != new_file_nid:
        _rewrite_ids(js_result, old_file_nid, new_file_nid)

    return js_result


def _rewrite_ids(result: dict, old_id: str, new_id: str) -> None:
    """Replace all occurrences of old_id with new_id in nodes and edges."""
    for node in result["nodes"]:
        if node["id"] == old_id:
            node["id"] = new_id

    for edge in result["edges"]:
        if edge["source"] == old_id:
            edge["source"] = new_id
        if edge["target"] == old_id:
            edge["target"] = new_id
