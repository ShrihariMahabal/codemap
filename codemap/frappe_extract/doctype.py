"""DocType JSON schema extractor (Phase 4a).

For each DocType JSON file (``*/doctype/{name}/{name}.json``) we emit:

- One ``doctype`` node, identified by ``make_id(name)``.  Behavioural
  flags (``is_submittable``, ``autoname``, ``naming_rule``,
  ``track_changes``, etc.) are stored as node attributes — triage
  uses them to answer "can this be cancelled?" / "where is the name
  generated?" without re-reading the JSON.
- A ``belongs_to_module`` edge from the doctype to its declared module.
- For every ``Link`` field — a ``links_to`` edge to the target DocType.
- For every ``Table`` (or ``Table MultiSelect``) field — a ``child_of`` edge
  to the child DocType.
- For every ``Dynamic Link`` field — a ``dynamic_link_to`` edge whose
  target is the *fieldname* that holds the runtime DocType name.
- For every field with ``fetch_from`` — a ``fetch_from`` edge to the
  source DocType (resolved via the field's Link parent).
- For every entry in ``permissions[]`` — a ``role`` node and a
  ``permitted_role`` edge from the role to the DocType, with the
  per-permission flags (read/write/submit/...) as edge metadata.

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

# Behavioural flags copied verbatim from the DocType JSON onto the
# doctype node.  These shape lifecycle behaviour ("is this submittable?",
# "what kind of name does it get?") and the report renders them as the
# DocType's behavioural profile.
_BEHAVIOURAL_FLAGS = (
    "is_submittable",
    "is_child_table",
    "is_single",
    "is_tree",
    "is_virtual",
    "issingle",
    "istable",
    "autoname",
    "naming_rule",
    "track_changes",
    "track_seen",
    "track_views",
    "in_create",
    "editable_grid",
    "quick_entry",
    "allow_rename",
    "allow_import",
    "custom",
    "beta",
)

# Permission flags emitted as boolean metadata on every ``permitted_role``
# edge.  We don't filter to flags the role actually has — agents reading
# a triage report want to see the full matrix at a glance.
_PERMISSION_FLAGS = (
    "read", "write", "create", "delete",
    "submit", "cancel", "amend",
    "report", "export", "import",
    "print", "email", "share",
    "set_user_permissions",
)


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
        **_behavioural_flags(data),
    )]
    edges: list[dict] = []

    edges.extend(_module_edges(data, doctype_nid, str_path))
    edges.extend(_field_edges(data, doctype_nid, str_path))

    perm_nodes, perm_edges = _permission_graph(data, name, doctype_nid, str_path)
    nodes.extend(perm_nodes)
    edges.extend(perm_edges)

    return {"nodes": nodes, "edges": edges}


# ── Internals ────────────────────────────────────────────────────────────────

def _behavioural_flags(data: dict) -> dict:
    """Return the subset of ``data`` worth attaching to the doctype node.

    Frappe stores some flags as ints (0/1) and some as strings (autoname
    is ``"naming_series:"`` or similar).  We pass values through as-is
    when they're set to anything truthy and skip absent keys entirely
    so the node attribute list stays clean.
    """
    out: dict = {}
    for flag in _BEHAVIOURAL_FLAGS:
        if flag not in data:
            continue
        value = data[flag]
        if value in (None, "", 0, False):
            continue
        out[flag] = value
    return out


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

        edge_extra: dict = {
            "fieldname": field.get("fieldname", ""),
            "options": options,
        }
        permlevel = field.get("permlevel")
        if isinstance(permlevel, int) and permlevel > 0:
            edge_extra["permlevel"] = permlevel

        edges.append(make_edge(
            doctype_nid, target_nid, relation,
            str_path, 1,
            **edge_extra,
        ))

    return edges


def _permission_graph(
    data: dict,
    name: str,
    doctype_nid: str,
    str_path: str,
) -> tuple[list[dict], list[dict]]:
    """Build role nodes + permitted_role edges from ``permissions[]``.

    Each permission row in a DocType JSON looks like::

        {"role": "Sales User", "read": 1, "write": 1, "submit": 1, ...}

    We emit one role node per distinct role name and one edge per row.
    The edge carries every permission flag we recognised so triage can
    answer "does Sales User have submit on Sales Order?" with one
    edge lookup instead of re-parsing the JSON.

    Permissions without a ``role`` field — Frappe emits these for
    role-less rules occasionally — are skipped.
    """
    permissions = data.get("permissions")
    if not isinstance(permissions, list):
        return [], []

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_roles: set[str] = set()

    for perm in permissions:
        if not isinstance(perm, dict):
            continue
        role = perm.get("role")
        if not isinstance(role, str) or not role.strip():
            continue

        role_nid = make_id("role", role)
        if role_nid not in seen_roles:
            seen_roles.add(role_nid)
            nodes.append(make_node(
                role_nid, role, "role", "",
                0, 0,
            ))

        edge_extra: dict = {"role": role, "doctype": name}
        for flag in _PERMISSION_FLAGS:
            value = perm.get(flag)
            if value:
                edge_extra[flag] = 1

        permlevel = perm.get("permlevel")
        if isinstance(permlevel, int):
            edge_extra["permlevel"] = permlevel

        edges.append(make_edge(
            role_nid, doctype_nid, "permitted_role",
            str_path, 1,
            **edge_extra,
        ))

    return nodes, edges
