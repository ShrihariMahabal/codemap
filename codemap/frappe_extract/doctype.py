"""DocType JSON schema extractor (Phase 4a).

For each DocType JSON file (``*/doctype/{name}/{name}.json``) we emit:

- One ``doctype`` node, identified by ``make_id(name)``.
- A ``belongs_to_module`` edge from the doctype to its declared module.
- For every ``Link`` field — a ``links_to`` edge to the target DocType.
- For every ``Table`` (or ``Table MultiSelect``) field — a ``child_of`` edge
  to the child DocType.
- For every ``Dynamic Link`` field — a ``dynamic_link_to`` edge whose
  target is the *fieldname* that holds the runtime DocType name.

We deliberately do **not** create a node per field.  The plan calls for
direct DocType-to-DocType edges with the fieldname stored as edge metadata.
"""

from __future__ import annotations

from pathlib import Path

from ..graph_primitives import make_edge, make_id, make_node
from ._common import empty_result, file_line_count, load_json


# Field types whose ``options`` value points at another DocType.
_FIELD_RELATIONS = {
    "Link": "links_to",
    "Table": "child_of",
    "Table MultiSelect": "child_of",
    "Dynamic Link": "dynamic_link_to",
}


def extract_doctype(path: Path) -> dict:
    """Extract graph nodes and edges from a single DocType JSON file.

    Returns the standard ``{"nodes": [...], "edges": [...]}`` dict.  A
    malformed file or one whose top-level ``doctype`` key isn't
    ``"DocType"`` produces an empty result — this extractor is safe to
    call on any JSON file.
    """
    data = load_json(path)
    if not data or data.get("doctype") != "DocType":
        return empty_result()

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return empty_result()

    str_path = str(path)
    line_end = file_line_count(path)
    doctype_nid = make_id(name)

    nodes: list[dict] = [make_node(
        doctype_nid, name, "doctype", str_path,
        1, line_end,
    )]
    edges: list[dict] = []

    edges.extend(_module_edges(data, doctype_nid, str_path))
    edges.extend(_field_edges(data, doctype_nid, str_path))

    return {"nodes": nodes, "edges": edges}


# ── Internals ────────────────────────────────────────────────────────────────

def _module_edges(data: dict, doctype_nid: str, str_path: str) -> list[dict]:
    """Return a single ``belongs_to_module`` edge if the JSON declares one."""
    module = data.get("module")
    if not isinstance(module, str) or not module.strip():
        return []
    return [make_edge(
        doctype_nid, make_id(module), "belongs_to_module",
        str_path, 1,
        module=module,
    )]


def _field_edges(data: dict, doctype_nid: str, str_path: str) -> list[dict]:
    """Walk the ``fields`` array and emit one edge per linking field."""
    fields = data.get("fields")
    if not isinstance(fields, list):
        return []

    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for field in fields:
        if not isinstance(field, dict):
            continue

        relation = _FIELD_RELATIONS.get(field.get("fieldtype"))
        if relation is None:
            continue

        options = field.get("options")
        if not isinstance(options, str):
            continue
        options = options.strip()
        if not options:
            continue

        target_nid = make_id(options)
        # Deduplicate identical edges — a doctype with two Link fields to
        # Customer should produce a single edge, not two.
        key = (relation, target_nid)
        if key in seen:
            continue
        seen.add(key)

        edges.append(make_edge(
            doctype_nid, target_nid, relation,
            str_path, 1,
            fieldname=field.get("fieldname", ""),
            options=options,
        ))

    return edges
