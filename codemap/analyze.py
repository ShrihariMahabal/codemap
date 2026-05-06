"""Graph analysis primitives for the codemap report.

Four reader-facing functions live here:

- :func:`god_nodes` — the most-connected non-file entities, with a
  flag for the small set of Frappe DocTypes that are *expected* to be
  hubs (Item, Customer, etc.) so the report can note them as
  structural rather than surprising.
- :func:`surprising_connections` — cross-module / cross-confidence
  edges ranked by how non-obvious they are.  Trivial scaffolding edges
  (``belongs_to_module``, ``contains``, ...) are excluded.
- :func:`suggest_questions` — Frappe-specific question templates
  hydrated against the actual graph (submittable DocTypes get a hook
  chain question, DocTypes with workflows get a workflow question, …).
- :func:`permission_matrix` — flattens every ``permitted_role`` edge
  into a ``{doctype: {role: {flag: bool}}}`` structure suitable for
  rendering as a table.

The functions here are pure: they read the graph and return data
structures, never mutate.  Heavy lifting (NetworkX algorithms) is
behind a couple of helpers that limit work on large graphs.
"""

from __future__ import annotations

from collections import Counter, defaultdict

import networkx as nx


# DocTypes that are universally referenced by other DocTypes.  Their
# high degree is a structural fact, not a code smell — the report
# annotates them so reviewers don't waste time investigating them.
_FRAPPE_STRUCTURAL_HUBS: frozenset[str] = frozenset({
    "Item", "Customer", "User", "Company", "Supplier",
    "Account", "Address", "Contact", "Employee",
    "Warehouse", "Currency", "UOM",
})

# Edges that mostly add scaffolding noise to "surprising" lists:
# they're true by construction (every DocType belongs to a module,
# every file contains its symbols) so they tell the reviewer nothing.
_TRIVIAL_RELATIONS: frozenset[str] = frozenset({
    "belongs_to_module", "contains", "method",
    "imports_from", "imports",
})


# ── God nodes ──────────────────────────────────────────────────────────────


def god_nodes(G: nx.Graph, top_n: int = 10) -> list[dict]:
    """Top-*N* most-connected real entities.

    File nodes are skipped — they accumulate ``contains`` edges
    mechanically and bury actually-interesting hubs.  Every result
    carries an ``is_structural_hub`` flag so the report can label
    well-known Frappe hubs (``Item``, ``Customer``, ...) as
    expected rather than surprising.
    """
    candidates = []
    for node_id, degree in G.degree():
        attrs = G.nodes[node_id]
        if attrs.get("file_type") == "file":
            continue
        candidates.append((node_id, degree))

    candidates.sort(key=lambda pair: (-pair[1], pair[0]))

    result = []
    for node_id, degree in candidates[:top_n]:
        attrs = G.nodes[node_id]
        label = attrs.get("label", node_id)
        result.append({
            "id": node_id,
            "label": label,
            "file_type": attrs.get("file_type", ""),
            "degree": degree,
            "is_structural_hub": label in _FRAPPE_STRUCTURAL_HUBS,
        })
    return result


# ── Surprising connections ────────────────────────────────────────────────


def surprising_connections(
    G: nx.Graph,
    communities: dict[int, list[str]] | None = None,
    top_n: int = 5,
) -> list[dict]:
    """Cross-cutting edges, ranked by how non-obvious they are.

    The score combines confidence (AMBIGUOUS > INFERRED > EXTRACTED)
    with whether the edge crosses a community or module boundary.
    Only the highest-scoring *top_n* are returned.
    """
    node_to_module = _node_module_map(G)
    node_to_community = _invert_communities(communities or {})

    scored: list[tuple[int, dict]] = []
    for u, v, data in G.edges(data=True):
        if data.get("relation", "") in _TRIVIAL_RELATIONS:
            continue
        if _is_file_node(G, u) or _is_file_node(G, v):
            continue

        score, why = _surprise_score(
            G, u, v, data, node_to_module, node_to_community,
        )
        # An edge is surprising only if *something* about it is —
        # the base confidence bonus alone doesn't qualify.
        if not why:
            continue

        src_id = data.get("_src", u)
        tgt_id = data.get("_tgt", v)
        scored.append((
            score,
            {
                "source": G.nodes[src_id].get("label", src_id),
                "target": G.nodes[tgt_id].get("label", tgt_id),
                "relation": data.get("relation", ""),
                "confidence": data.get("confidence", "EXTRACTED"),
                "why": "; ".join(why),
            },
        ))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in scored[:top_n]]


def _surprise_score(
    G: nx.Graph,
    u: str, v: str, data: dict,
    node_to_module: dict[str, str],
    node_to_community: dict[str, int],
) -> tuple[int, list[str]]:
    """Per-edge score and the reasons that contributed to it."""
    score = 0
    reasons: list[str] = []

    confidence = data.get("confidence", "EXTRACTED")
    bonus = {"AMBIGUOUS": 4, "INFERRED": 2, "EXTRACTED": 1}.get(confidence, 1)
    score += bonus
    if confidence in ("AMBIGUOUS", "INFERRED"):
        reasons.append(f"{confidence.lower()} edge")

    mod_u = node_to_module.get(u)
    mod_v = node_to_module.get(v)
    if mod_u and mod_v and mod_u != mod_v:
        score += 3
        reasons.append(f"crosses modules ({mod_u} ↔ {mod_v})")

    cid_u = node_to_community.get(u)
    cid_v = node_to_community.get(v)
    if cid_u is not None and cid_v is not None and cid_u != cid_v:
        score += 1
        reasons.append("bridges separate communities")

    deg_u = G.degree(u)
    deg_v = G.degree(v)
    if min(deg_u, deg_v) <= 2 and max(deg_u, deg_v) >= 8:
        score += 1
        reasons.append("peripheral node connects to a hub")

    return score, reasons


# ── Suggested questions ───────────────────────────────────────────────────


def suggest_questions(
    G: nx.Graph,
    communities: dict[int, list[str]] | None = None,
    community_labels: dict[int, str] | None = None,
    top_n: int = 7,
) -> list[dict]:
    """Frappe-specific questions the graph is well placed to answer.

    The pool is generated from facts already in the graph (which
    DocTypes are submittable, which have workflows, which methods
    enqueue background jobs, …) so every question maps to a concrete
    investigation the reviewer can follow up.
    """
    questions: list[dict] = []

    for entry in god_nodes(G, top_n=3):
        if entry["file_type"] == "doctype":
            questions.append({
                "type": "coupling",
                "question": (
                    f"Which DocTypes are most tightly coupled to "
                    f"`{entry['label']}`?"
                ),
                "why": (
                    f"`{entry['label']}` has {entry['degree']} connections — "
                    f"changes here ripple widely."
                ),
            })

    for node_id, attrs in G.nodes(data=True):
        if attrs.get("file_type") != "doctype":
            continue
        label = attrs.get("label", node_id)

        if attrs.get("is_submittable"):
            questions.append({
                "type": "hook_chain",
                "question": (
                    f"What is the full hook chain triggered when "
                    f"`{label}` is submitted?"
                ),
                "why": f"`{label}` is submittable — submit hooks fire downstream.",
            })

        if _doctype_has_workflow(G, node_id):
            questions.append({
                "type": "workflow_states",
                "question": (
                    f"Which workflow states does `{label}` pass through "
                    f"and who can transition them?"
                ),
                "why": f"`{label}` has a Workflow attached.",
            })

        if _doctype_has_permissions(G, node_id):
            questions.append({
                "type": "permissions",
                "question": (
                    f"What roles have submit permission on `{label}`?"
                ),
                "why": f"`{label}` has explicit role permissions defined.",
            })

    if _has_node_with_relation(G, "calls_api"):
        questions.append({
            "type": "client_api",
            "question": (
                "Which JS client scripts call APIs that modify "
                "submittable DocTypes?"
            ),
            "why": "`calls_api` edges are present — UI mutations may bypass server validation.",
        })

    if _has_node_with_relation(G, "enqueues_job"):
        questions.append({
            "type": "background_jobs",
            "question": (
                "Which background jobs are triggered by DocType "
                "lifecycle events?"
            ),
            "why": "`enqueues_job` edges are present — async work may run after submit/cancel.",
        })

    if not questions:
        return [{
            "type": "no_signal",
            "question": None,
            "why": (
                "Not enough graph signal to suggest questions. "
                "Run extraction over a larger app or wait for more "
                "extractors to fill in."
            ),
        }]

    return questions[:top_n]


# ── Permission matrix ─────────────────────────────────────────────────────


_PERMISSION_FLAGS = (
    "read", "write", "create", "delete",
    "submit", "cancel", "amend",
    "report", "export", "import",
    "print", "email", "share",
    "set_user_permissions",
)


def permission_matrix(G: nx.Graph) -> dict[str, dict[str, dict[str, bool]]]:
    """Return ``{doctype_label: {role_label: {flag: bool}}}``.

    Flattens every ``permitted_role`` edge.  Roles that appear with
    multiple ``permlevel`` rows on the same DocType have their flags
    ORed together — the matrix represents whether the role *can* do
    the action at any permission level, not the per-level breakdown.
    """
    matrix: dict[str, dict[str, dict[str, bool]]] = defaultdict(
        lambda: defaultdict(dict),
    )

    for u, v, data in G.edges(data=True):
        if data.get("relation") != "permitted_role":
            continue

        src_id = data.get("_src", u)
        tgt_id = data.get("_tgt", v)
        if src_id not in G.nodes or tgt_id not in G.nodes:
            continue

        # The edge runs role → doctype.  We tolerate either order on
        # undirected graphs by checking file_type rather than direction.
        if G.nodes[src_id].get("file_type") == "role":
            role_id, doctype_id = src_id, tgt_id
        else:
            role_id, doctype_id = tgt_id, src_id

        if G.nodes[doctype_id].get("file_type") != "doctype":
            continue

        role_label = G.nodes[role_id].get("label", role_id)
        doctype_label = G.nodes[doctype_id].get("label", doctype_id)

        for flag in _PERMISSION_FLAGS:
            value = bool(data.get(flag))
            matrix[doctype_label][role_label][flag] = (
                matrix[doctype_label][role_label].get(flag, False) or value
            )

    return {dt: dict(roles) for dt, roles in matrix.items()}


# ── Internals ──────────────────────────────────────────────────────────────


def _is_file_node(G: nx.Graph, node_id: str) -> bool:
    return G.nodes[node_id].get("file_type") == "file"


def _invert_communities(
    communities: dict[int, list[str]],
) -> dict[str, int]:
    return {n: cid for cid, nodes in communities.items() for n in nodes}


def _node_module_map(G: nx.Graph) -> dict[str, str]:
    """Build ``{node_id: module_name}`` from ``belongs_to_module`` edges."""
    mapping: dict[str, str] = {}
    for u, v, data in G.edges(data=True):
        if data.get("relation") != "belongs_to_module":
            continue
        src_id = data.get("_src", u)
        if src_id not in G.nodes:
            src_id = u
        module_name = data.get("module") or G.nodes[
            data.get("_tgt", v)
        ].get("label", "")
        if module_name:
            mapping[src_id] = module_name
    return mapping


def _doctype_has_workflow(G: nx.Graph, doctype_id: str) -> bool:
    """True if any Workflow node references *doctype_id*."""
    for neighbour in G.neighbors(doctype_id):
        if G.nodes[neighbour].get("file_type") == "workflow":
            return True
    return False


def _doctype_has_permissions(G: nx.Graph, doctype_id: str) -> bool:
    for u, v, data in G.edges(doctype_id, data=True):
        if data.get("relation") == "permitted_role":
            return True
    return False


def _has_node_with_relation(G: nx.Graph, relation: str) -> bool:
    for _, _, data in G.edges(data=True):
        if data.get("relation") == relation:
            return True
    return False
