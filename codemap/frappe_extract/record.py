"""Generic record JSON extractor (Phase 4e).

A *record* is any non-DocType JSON file that carries a top-level
``"doctype": "..."`` key — for example a Report, Workspace, Print Format,
or Dashboard Chart.  These files describe an instance of *some* Frappe
DocType (the kind), and many of them additionally reference another
DocType the record is *about* (``ref_doctype`` for reports, etc.).

For each such file we emit:

- One ``record`` node, identified by ``make_id(kind, name)`` so that two
  records with the same name but different kinds (e.g. a Report and a
  Print Format both named ``Sales Order``) don't collide.
- A ``record_of`` edge from the record to the DocType node for its kind.
- Up to one ``references_doctype`` edge per known reference key
  (``ref_doctype``, ``document_type``, ``reference_doctype``).

DocType JSON files (``"doctype": "DocType"``) are intentionally ignored
here — they're handled by the DocType extractor.
"""

from __future__ import annotations

from pathlib import Path

from ..graph_primitives import make_edge, make_id, make_node
from ._common import empty_result, file_line_count, load_json


# Top-level keys that point at the DocType the record is *about*.
# Different record kinds use different conventions; we check them all.
_REFERENCE_KEYS = ("ref_doctype", "document_type", "reference_doctype")


def extract_record(path: Path) -> dict:
    """Extract one record node + its outbound edges from a JSON file.

    Returns an empty result for DocType JSONs, malformed files, and any
    JSON whose ``name`` field is missing or empty.
    """
    data = load_json(path)
    if not isinstance(data, dict):
        return empty_result()

    kind = data.get("doctype")
    if not isinstance(kind, str) or kind == "DocType":
        return empty_result()

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return empty_result()

    str_path = str(path)
    line_end = file_line_count(path)

    record_nid = make_id(kind, name)
    nodes: list[dict] = [make_node(
        record_nid, name, "record", str_path,
        1, line_end,
        record_kind=kind,
    )]

    edges: list[dict] = [make_edge(
        record_nid, make_id(kind), "record_of",
        str_path, 1,
        kind=kind,
    )]
    edges.extend(_reference_edges(data, record_nid, str_path))

    return {"nodes": nodes, "edges": edges}


def _reference_edges(data: dict, record_nid: str, str_path: str) -> list[dict]:
    """Emit ``references_doctype`` edges for any reference keys present."""
    edges: list[dict] = []
    seen: set[str] = set()

    for key in _REFERENCE_KEYS:
        target = data.get(key)
        if not isinstance(target, str) or not target.strip():
            continue

        target_nid = make_id(target)
        # Skip self-references and duplicates (different keys can point
        # at the same DocType).
        if target_nid == record_nid or target_nid in seen:
            continue
        seen.add(target_nid)

        edges.append(make_edge(
            record_nid, target_nid, "references_doctype",
            str_path, 1,
            via=key,
            doctype=target,
        ))

    return edges
