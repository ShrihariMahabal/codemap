"""Notification JSON extractor.

Frappe Notifications fire emails / system messages when a document
event occurs (Save, Submit, Days Before/After, etc.).  The JSON we
parse here looks like::

    {
        "doctype": "Notification",
        "name": "Order Submitted",
        "document_type": "Sales Order",
        "event": "Submit",
        "channel": "Email",
        "condition": "doc.grand_total > 1000",
        "recipients": [
            {"receiver_by_role": "Sales Manager"},
            {"receiver_by_document_field": "owner"}
        ],
        ...
    }

We emit:

- One ``notification`` node carrying ``event``, ``channel`` and
  ``condition`` so triage can answer "why didn't this email fire?"
  without re-reading the JSON.
- A ``notification_for`` edge from the notification to the DocType
  named in ``document_type``.
- For each ``recipients[]`` entry with a ``receiver_by_role``: a
  ``notification_recipient`` edge from the notification to a ``role``
  node (deduplicated within the file).

Frappe also supports recipients defined by document field (e.g.
``owner``) or by user list — those don't have a stable graph target
the way roles do, so we record them as edge metadata only via the
``receiver_field`` attribute on the notification node.
"""

from __future__ import annotations

from pathlib import Path

from ..graph_primitives import make_edge, make_id, make_node
from ._common import empty_result, file_line_count, load_json


def extract_notification(path: Path) -> dict:
    """Extract graph nodes and edges from a notification JSON file."""
    data = load_json(path)
    if not isinstance(data, dict) or data.get("doctype") != "Notification":
        return empty_result()

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return empty_result()

    str_path = str(path)
    line_end = file_line_count(path)
    notif_nid = make_id("notification", name)

    extras: dict = {}
    for key in ("event", "channel", "condition", "subject", "is_standard"):
        value = data.get(key)
        if value not in (None, "", 0, False):
            extras[key] = value

    nodes: list[dict] = [make_node(
        notif_nid, name, "notification", str_path,
        1, line_end,
        **extras,
    )]
    edges: list[dict] = []

    document_type = data.get("document_type")
    if isinstance(document_type, str) and document_type.strip():
        edges.append(make_edge(
            notif_nid, make_id(document_type), "notification_for",
            str_path, 1,
            doctype=document_type,
        ))

    role_nodes, role_edges = _recipient_graph(data, name, notif_nid, str_path)
    nodes.extend(role_nodes)
    edges.extend(role_edges)

    return {"nodes": nodes, "edges": edges}


def _recipient_graph(
    data: dict,
    name: str,
    notif_nid: str,
    str_path: str,
) -> tuple[list[dict], list[dict]]:
    """Build role nodes + ``notification_recipient`` edges from recipients.

    Recipients without ``receiver_by_role`` (e.g. those keyed by
    ``receiver_by_document_field``) are recorded as edge metadata on
    the notification itself but don't produce edges — there's no role
    node to point at.
    """
    recipients = data.get("recipients")
    if not isinstance(recipients, list):
        return [], []

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_roles: set[str] = set()

    for recipient in recipients:
        if not isinstance(recipient, dict):
            continue
        role = recipient.get("receiver_by_role")
        if not isinstance(role, str) or not role.strip():
            continue

        role_nid = make_id("role", role)
        if role_nid not in seen_roles:
            seen_roles.add(role_nid)
            nodes.append(make_node(
                role_nid, role, "role", "",
                0, 0,
            ))

        edges.append(make_edge(
            notif_nid, role_nid, "notification_recipient",
            str_path, 1,
            role=role, notification=name,
        ))

    return nodes, edges
