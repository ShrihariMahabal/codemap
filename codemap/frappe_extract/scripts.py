"""Server / Client Script JSON extractors.

A Server Script and a Client Script are records — code stored in the
database, exported by ``bench export-fixtures``.  The JSON shape::

    {
        "doctype": "Server Script",
        "name": "Add Region On Save",
        "script_type": "DocType Event",
        "reference_doctype": "Sales Order",
        "doctype_event": "Before Save",
        "script": "doc.region = ..."
    }

Client Scripts are similar but the reference field is ``dt`` instead
of ``reference_doctype`` and the trigger is ``view``.  Both need a
graph node so that triage can answer "what custom logic runs when
Sales Order is saved?" without parsing every fixture.

Edges:

- ``server_script`` / ``client_script`` node, with ``script_type``,
  ``event``, and the script body length as attributes.
- ``script_for`` edge to the target DocType (when one is set).

Why script body length and not the body itself?  The body can be
hundreds of lines of arbitrary code — keeping it in the graph would
balloon node sizes and force every triage diff through a noisy
"text changed" signal.  The length is enough to flag "this script
is non-trivial; open the fixture if you want to read it."

Frappe stores a single Server Script JSON as a dict, but ``bench
export-fixtures`` emits an array of records.  Both forms are
supported here.
"""

from __future__ import annotations

from pathlib import Path

from ..graph_primitives import make_edge, make_id, make_node
from ._common import empty_result, file_line_count, load_json


def extract_server_script(path: Path) -> dict:
    """Extract Server Script records from a JSON file."""
    return _extract_scripts(
        path,
        kind="Server Script",
        node_type="server_script",
        doctype_keys=("reference_doctype",),
        event_keys=("doctype_event", "script_type"),
    )


def extract_client_script(path: Path) -> dict:
    """Extract Client Script records from a JSON file."""
    return _extract_scripts(
        path,
        kind="Client Script",
        node_type="client_script",
        doctype_keys=("dt",),
        event_keys=("view",),
    )


def _extract_scripts(
    path: Path,
    *,
    kind: str,
    node_type: str,
    doctype_keys: tuple[str, ...],
    event_keys: tuple[str, ...],
) -> dict:
    """Shared core for the two script extractors.

    Iterates each record in the file (single dict or list of dicts),
    drops the ``script`` body from the node attributes, and emits one
    ``script_for`` edge per record whose target DocType resolves.
    """
    data = load_json(path)
    if data is None:
        return empty_result()

    records = data if isinstance(data, list) else [data]

    str_path = str(path)
    line_end = file_line_count(path)

    nodes: list[dict] = []
    edges: list[dict] = []

    for record in records:
        if not isinstance(record, dict) or record.get("doctype") != kind:
            continue
        name = record.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        script_nid = make_id(node_type, name)

        extras: dict = {"script_kind": kind}
        for key in event_keys:
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                extras[key] = value
        if isinstance(record.get("disabled"), int):
            extras["disabled"] = record["disabled"]
        script_text = record.get("script")
        if isinstance(script_text, str):
            extras["script_lines"] = script_text.count("\n") + 1

        nodes.append(make_node(
            script_nid, name, node_type, str_path,
            1, line_end,
            **extras,
        ))

        target_doctype = _first_doctype(record, doctype_keys)
        if target_doctype is not None:
            edges.append(make_edge(
                script_nid, make_id(target_doctype), "script_for",
                str_path, 1,
                doctype=target_doctype,
                script_kind=kind,
            ))

    return {"nodes": nodes, "edges": edges}


def _first_doctype(record: dict, doctype_keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty DocType reference from *record*."""
    for key in doctype_keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
