"""Workflow JSON extractor.

A Frappe Workflow record describes a state machine bolted onto a
DocType.  The JSON shape we care about::

    {
        "doctype": "Workflow",
        "name": "Sales Order Approval",
        "document_type": "Sales Order",
        "states": [
            {"state": "Draft",    "doc_status": "0"},
            {"state": "Approved", "doc_status": "1", ...},
            ...
        ],
        "transitions": [
            {"state": "Draft", "action": "Approve",
             "next_state": "Approved", "allowed": "Sales Manager"},
            ...
        ]
    }

We emit:

- One ``workflow`` node for the workflow itself.
- A ``workflow_for`` edge: workflow → DocType named in ``document_type``.
- One ``workflow_state`` node per ``states[]`` entry, with
  ``doc_status`` etc. as attributes; a ``has_state`` edge from the
  workflow to the state.
- One ``workflow_transition`` edge per ``transitions[]`` entry:
  ``state_from --workflow_transition--> state_to`` with the action
  name and allowed role as edge metadata.

Why this matters: workflow-related tickets ("user can't transition
Draft → Approved") are common and the answer almost always lives in
the transition's ``allowed`` field.  Storing transitions as graph
edges lets triage find the answer in one hop.
"""

from __future__ import annotations

from pathlib import Path

from ..graph_primitives import make_edge, make_id, make_node
from ._common import empty_result, file_line_count, load_json


def extract_workflow(path: Path) -> dict:
    """Extract graph nodes and edges from a single workflow JSON file."""
    data = load_json(path)
    if not data or data.get("doctype") != "Workflow":
        return empty_result()

    name = data.get("name")
    document_type = data.get("document_type")
    if not isinstance(name, str) or not name.strip():
        return empty_result()

    str_path = str(path)
    line_end = file_line_count(path)
    workflow_nid = make_id("workflow", name)

    nodes: list[dict] = [make_node(
        workflow_nid, name, "workflow", str_path,
        1, line_end,
        is_active=data.get("is_active", 0),
        send_email_alert=data.get("send_email_alert", 0),
    )]
    edges: list[dict] = []

    if isinstance(document_type, str) and document_type.strip():
        edges.append(make_edge(
            workflow_nid, make_id(document_type), "workflow_for",
            str_path, 1,
            doctype=document_type,
        ))

    state_nodes, state_edges, state_nids = _extract_states(
        data, name, workflow_nid, str_path,
    )
    nodes.extend(state_nodes)
    edges.extend(state_edges)

    edges.extend(_extract_transitions(data, name, state_nids, str_path))

    return {"nodes": nodes, "edges": edges}


def _extract_states(
    data: dict,
    workflow_name: str,
    workflow_nid: str,
    str_path: str,
) -> tuple[list[dict], list[dict], dict[str, str]]:
    """Build state nodes and ``has_state`` edges.

    Returns ``(nodes, edges, state_name_to_nid)`` so the transition
    walker can resolve state names without re-scanning the list.
    """
    states = data.get("states")
    if not isinstance(states, list):
        return [], [], {}

    nodes: list[dict] = []
    edges: list[dict] = []
    state_nids: dict[str, str] = {}

    for state in states:
        if not isinstance(state, dict):
            continue
        state_name = state.get("state")
        if not isinstance(state_name, str) or not state_name.strip():
            continue

        state_nid = make_id("workflow_state", workflow_name, state_name)
        state_nids[state_name] = state_nid

        nodes.append(make_node(
            state_nid, state_name, "workflow_state", str_path,
            1, 1,
            workflow=workflow_name,
            doc_status=state.get("doc_status"),
            is_optional_state=state.get("is_optional_state", 0),
        ))
        edges.append(make_edge(
            workflow_nid, state_nid, "has_state",
            str_path, 1,
            state=state_name,
        ))

    return nodes, edges, state_nids


def _extract_transitions(
    data: dict,
    workflow_name: str,
    state_nids: dict[str, str],
    str_path: str,
) -> list[dict]:
    """Build ``workflow_transition`` edges between state nodes.

    Transitions whose endpoints aren't declared as states in the same
    workflow are skipped — Frappe rejects those at runtime, and graph
    edges into nonexistent states aren't useful for triage.
    """
    transitions = data.get("transitions")
    if not isinstance(transitions, list):
        return []

    edges: list[dict] = []
    for transition in transitions:
        if not isinstance(transition, dict):
            continue
        from_state = transition.get("state")
        to_state = transition.get("next_state")
        action = transition.get("action")
        allowed = transition.get("allowed")

        from_nid = state_nids.get(from_state) if isinstance(from_state, str) else None
        to_nid = state_nids.get(to_state) if isinstance(to_state, str) else None
        if from_nid is None or to_nid is None:
            continue

        edge_extra: dict = {
            "workflow": workflow_name,
            "action": action if isinstance(action, str) else "",
            "from_state": from_state,
            "to_state": to_state,
        }
        if isinstance(allowed, str) and allowed.strip():
            edge_extra["allowed"] = allowed
        condition = transition.get("condition")
        if isinstance(condition, str) and condition.strip():
            edge_extra["condition"] = condition

        edges.append(make_edge(
            from_nid, to_nid, "workflow_transition",
            str_path, 1,
            **edge_extra,
        ))

    return edges
