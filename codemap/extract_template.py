"""Jinja template extraction.

Frappe ships hundreds of ``.html`` templates: portal pages, web views,
print formats, email layouts.  They are vanilla Jinja2 with a couple of
Frappe-specific helpers (``frappe.utils.fmt_money``, ``frappe.format``)
exposed as global callables.

We don't depend on Jinja2 to parse them — every relationship the graph
cares about can be lifted with a handful of regular expressions:

- ``{% extends "templates/web.html" %}``      → renders_template edge
- ``{% include "templates/header.html" %}``   → renders_template edge
- ``{% import "templates/macros.html" as m %}`` → renders_template edge
- ``{% from "templates/macros.html" import row %}`` → renders_template edge
- ``{{ frappe.utils.fmt_money(amount) }}``    → raw_call (resolved later
  by ``resolve.py`` so it can connect to the function the agent registered
  via the ``jinja`` hook in Phase 4)

Field references like ``{{ doc.customer_name }}`` are intentionally NOT
emitted as edges from generic ``.html`` files.  They are only useful
inside Print Format definitions, which live in JSON and are parsed by
the record extractor.
"""

from __future__ import annotations

import re
from pathlib import Path

from .graph_primitives import make_edge, make_id, make_node


# ``{% extends/include/import "path" %}`` — accepts either single or
# double quotes and any whitespace between keyword and quoted path.
_EXTENDS_PATTERN = re.compile(
    r"\{%-?\s*(?:extends|include|import)\s+['\"]([^'\"]+)['\"]",
)

# ``{% from "path" import name %}`` — Jinja's other macro form.
_FROM_IMPORT_PATTERN = re.compile(
    r"\{%-?\s*from\s+['\"]([^'\"]+)['\"]\s+import\s+",
)

# ``{{ ... }}`` expression block.  Captured greedily up to the next ``}}``.
_EXPRESSION_BLOCK = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)

# Dotted callable inside an expression: ``frappe.utils.fmt_money(`` etc.
# The negative look-behind keeps us from matching the middle of a longer
# identifier (e.g. the second half of ``my.frappe.fn``).  We require at
# least one dot — bare identifiers like ``foo()`` are too noisy to
# emit as graph edges from a template.
_CALLABLE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.])([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)\s*\(",
)


def extract_template(path: Path) -> dict:
    """Extract template references and embedded callables from a Jinja file.

    Returns the standard ``{"nodes", "edges", "raw_calls"}`` dict so the
    cross-file resolution pass can wire ``raw_calls`` to the actual
    Python functions registered via the ``jinja`` hook.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": str(exc), "nodes": [], "edges": [], "raw_calls": []}

    str_path = str(path)
    line_count = max(1, source.count("\n") + 1)

    file_nid = make_id(str_path)
    file_node = make_node(
        file_nid, path.name, "template", str_path,
        1, line_count,
    )

    edges = list(_extract_template_refs(source, str_path, file_nid))
    raw_calls = list(_extract_callables(source, str_path, file_nid))

    return {"nodes": [file_node], "edges": edges, "raw_calls": raw_calls}


def _line_of(source: str, offset: int) -> int:
    """Return the 1-indexed line number of *offset* within *source*."""
    return source.count("\n", 0, offset) + 1


def _extract_template_refs(source: str, str_path: str, file_nid: str):
    """Yield ``renders_template`` edges for include/extends/import/from."""
    seen: set[str] = set()

    for match in _EXTENDS_PATTERN.finditer(source):
        target_path = match.group(1).strip()
        if not target_path or target_path in seen:
            continue
        seen.add(target_path)
        yield make_edge(
            file_nid,
            make_id(target_path),
            "renders_template",
            str_path,
            _line_of(source, match.start()),
            target_template=target_path,
        )

    for match in _FROM_IMPORT_PATTERN.finditer(source):
        target_path = match.group(1).strip()
        if not target_path or target_path in seen:
            continue
        seen.add(target_path)
        yield make_edge(
            file_nid,
            make_id(target_path),
            "renders_template",
            str_path,
            _line_of(source, match.start()),
            target_template=target_path,
        )


def _extract_callables(source: str, str_path: str, file_nid: str):
    """Yield raw_calls for dotted callables inside ``{{ ... }}`` blocks.

    We only inspect ``{{ ... }}`` expression blocks — not ``{% ... %}``
    statement blocks — because the statement form is mostly control
    flow (``{% if %}``, ``{% for %}``) where embedded calls don't
    represent a meaningful "this template invokes function X" relation.
    """
    seen_pairs: set[tuple[str, int]] = set()

    for block in _EXPRESSION_BLOCK.finditer(source):
        block_text = block.group(1)
        block_line = _line_of(source, block.start())
        for call_match in _CALLABLE_PATTERN.finditer(block_text):
            callee = call_match.group(1)
            key = (callee, block_line)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            yield {
                "caller_nid": file_nid,
                "callee": callee,
                "source_file": str_path,
                "line": block_line,
            }
