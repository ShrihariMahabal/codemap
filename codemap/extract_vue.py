"""Vue SFC extraction.

Extracts the ``<script>`` or ``<script setup>`` block from ``.vue`` files
and parses it as JavaScript using the JS extractor.  The ``<template>``
block is also scanned for child-component references — every
``<EmployeeAvatar />`` style tag becomes a ``renders_template`` edge so
the graph captures parent → child component composition.

Native HTML elements (``<div>``, ``<span>``, ...) and Vue built-ins
(``<template>``, ``<slot>``, ``<transition>``, ``<component>``) are not
emitted as edges — they aren't user-defined components and would just
add noise.
"""

from __future__ import annotations

import re
from pathlib import Path

from .extract_js import extract_js as _extract_js_from_source
from .graph_primitives import make_edge, make_id, make_node


# Match <script> or <script setup> blocks, capturing the content.
# Uses re.DOTALL so . matches newlines.
_SCRIPT_PATTERN = re.compile(
    r"<script(?:\s+setup)?[^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)

# Match the top-level <template> block of a Vue SFC.
_TEMPLATE_PATTERN = re.compile(
    r"<template(?:\s[^>]*)?>(.*?)</template>",
    re.DOTALL | re.IGNORECASE,
)

# Match an opening or self-closing tag whose name starts with an
# uppercase letter — Vue's convention for user-defined components.
# Examples that match: ``<EmployeeAvatar``, ``<MonthViewTable``,
# ``<FeatherIcon``.  Examples that don't: ``<div``, ``<span``, ``<img``.
_COMPONENT_TAG_PATTERN = re.compile(r"<([A-Z][A-Za-z0-9_]*)\b")

# Vue built-ins that happen to be PascalCase but aren't user components.
_VUE_BUILTINS: frozenset[str] = frozenset({
    "Transition", "TransitionGroup", "KeepAlive", "Teleport", "Suspense",
    "Component",
})


def _extract_template_components(
    source: str,
    str_path: str,
    file_nid: str,
) -> list[dict]:
    """Return ``renders_template`` edges for components used in <template>.

    Vue's component convention is straightforward: PascalCase tags are
    user-defined components, lowercase tags are native HTML.  We scan
    the first ``<template>`` block (Vue SFCs only allow one) and emit
    one edge per distinct component reference, with the line number of
    the first occurrence.
    """
    match = _TEMPLATE_PATTERN.search(source)
    if not match:
        return []

    block = match.group(1)
    block_start = match.start(1)

    edges: list[dict] = []
    seen: set[str] = set()

    for tag_match in _COMPONENT_TAG_PATTERN.finditer(block):
        component = tag_match.group(1)
        if component in _VUE_BUILTINS or component in seen:
            continue
        seen.add(component)

        absolute_offset = block_start + tag_match.start()
        line = source.count("\n", 0, absolute_offset) + 1

        edges.append(make_edge(
            file_nid,
            make_id(component),
            "renders_template",
            str_path,
            line,
            confidence="INFERRED",
            component=component,
        ))

    return edges


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

    template_edges = _extract_template_components(
        source_text, str_path, file_nid,
    )

    result = _find_script_block(source_text)
    if not result:
        # Vue file with no script block — emit the file node and any
        # component references we found in the template.
        return {
            "nodes": [file_node],
            "edges": template_edges,
            "raw_calls": [],
        }

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

    js_result["edges"].extend(template_edges)
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
