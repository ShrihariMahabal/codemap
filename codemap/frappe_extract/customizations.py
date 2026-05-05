"""Custom Field and Property Setter extractors.

Both record kinds describe customisations layered on top of an
existing DocType.  They typically live in ``fixtures/`` as arrays
exported by ``bench export-fixtures``.

A Custom Field record looks like::

    {
        "doctype": "Custom Field",
        "name": "Sales Order-region",
        "dt": "Sales Order",
        "fieldname": "region",
        "fieldtype": "Data",
        "insert_after": "customer"
    }

A Property Setter record looks like::

    {
        "doctype": "Property Setter",
        "name": "Sales Order-customer-reqd",
        "doc_type": "Sales Order",
        "field_name": "customer",
        "property": "reqd",
        "value": "1",
        "property_type": "Check"
    }

For each record we emit:

- One ``custom_field`` / ``property_setter`` node carrying enough
  metadata to render the customisation in a "what's modified about
  this DocType?" report.
- A ``custom_field_on`` / ``property_override_on`` edge from the
  customisation node to the target DocType node.

The triage layer overlays these onto the DocType controller graph
so an agent can quickly spot "this site has a Property Setter making
``customer`` mandatory — is that the cause of the Mandatory error?"
"""

from __future__ import annotations

from pathlib import Path

from ..graph_primitives import make_edge, make_id, make_node
from ._common import empty_result, file_line_count, load_json


def extract_custom_field(path: Path) -> dict:
    """Extract Custom Field records from a JSON file."""
    return _extract_customisations(
        path,
        kind="Custom Field",
        node_type="custom_field",
        relation="custom_field_on",
        doctype_keys=("dt", "doc_type"),
        attribute_keys=(
            "fieldname", "fieldtype", "label",
            "insert_after", "options", "reqd",
            "depends_on", "fetch_from",
        ),
    )


def extract_property_setter(path: Path) -> dict:
    """Extract Property Setter records from a JSON file."""
    return _extract_customisations(
        path,
        kind="Property Setter",
        node_type="property_setter",
        relation="property_override_on",
        doctype_keys=("doc_type", "dt"),
        attribute_keys=(
            "field_name", "property", "value",
            "property_type", "row_name",
        ),
    )


def _extract_customisations(
    path: Path,
    *,
    kind: str,
    node_type: str,
    relation: str,
    doctype_keys: tuple[str, ...],
    attribute_keys: tuple[str, ...],
) -> dict:
    """Shared core for Custom Field and Property Setter extraction.

    Both types are exported as arrays of records, but legacy single-
    record dicts also appear in the wild (e.g. when developers hand-
    craft fixtures).  Both shapes are accepted.
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

        record_nid = make_id(node_type, name)
        extras = _collect_attributes(record, attribute_keys)
        extras["customisation_kind"] = kind

        nodes.append(make_node(
            record_nid, name, node_type, str_path,
            1, line_end,
            **extras,
        ))

        target_doctype = _first_doctype(record, doctype_keys)
        if target_doctype is not None:
            edge_extras: dict = {
                "doctype": target_doctype,
                "customisation_kind": kind,
            }
            for key in attribute_keys:
                value = record.get(key)
                if isinstance(value, str) and value.strip():
                    edge_extras[key] = value

            edges.append(make_edge(
                record_nid, make_id(target_doctype), relation,
                str_path, 1,
                **edge_extras,
            ))

    return {"nodes": nodes, "edges": edges}


# Keys we never copy into a node's ``**extras`` because they collide
# with the positional fields of :func:`make_node`.
_RESERVED_NODE_KEYS = frozenset({
    "id", "label", "file_type", "source_file",
    "source_line_start", "source_line_end",
})


def _collect_attributes(record: dict, keys: tuple[str, ...]) -> dict:
    """Pull a small, fixed set of attributes off *record*.

    Skips empty strings, ``None``, and ``0`` so the resulting node
    attributes stay scannable in the graph viewer.  Keys that would
    collide with ``make_node``'s positional fields are renamed with
    a ``field_`` prefix instead of dropped — losing the ``label`` of
    a Custom Field would erase useful triage data.
    """
    out: dict = {}
    for key in keys:
        value = record.get(key)
        if value in (None, "", 0, False):
            continue
        target_key = f"field_{key}" if key in _RESERVED_NODE_KEYS else key
        out[target_key] = value
    return out


def _first_doctype(record: dict, doctype_keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty DocType reference from *record*.

    Custom Field uses ``dt``; Property Setter uses ``doc_type``.  We
    accept either to keep callers from having to remember the spec.
    """
    for key in doctype_keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
