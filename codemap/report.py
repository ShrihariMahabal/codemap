"""Generate ``CODEMAP_REPORT.md`` — the human-readable audit trail.

The report is composed section by section.  Each ``_section_*`` helper
takes whatever subset of state it needs and returns a list of
markdown lines; :func:`generate` joins them with blank lines so the
final output stays in canonical Frappe order: corpus → summary →
hubs → cross-cutting concerns → customisations → gaps → questions.

Sections that have nothing to say (e.g. a Frappe-less corpus has no
workflows) are skipped quietly rather than printing empty headings —
the reader sees a tighter document and the absence is itself
informative.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

import networkx as nx


# Canonical ordering for Frappe document lifecycle hooks.  Methods
# extracted from a DocType controller are sorted by this when we render
# the lifecycle section so reviewers see "validate → on_submit" in the
# order they fire, not alphabetical.
_LIFECYCLE_ORDER: tuple[str, ...] = (
    "autoname",
    "before_insert",
    "after_insert",
    "validate",
    "before_save",
    "on_update",
    "after_save",
    "before_submit",
    "on_submit",
    "after_submit",
    "before_cancel",
    "on_cancel",
    "after_cancel",
    "before_update_after_submit",
    "on_update_after_submit",
    "on_change",
    "before_rename",
    "after_rename",
    "on_trash",
    "after_delete",
)


def generate(
    G: nx.Graph,
    *,
    detection: dict,
    communities: dict[int, list[str]],
    community_labels: dict[int, str],
    cohesion: dict[int, float],
    god_node_list: list[dict],
    surprises: list[dict],
    questions: list[dict],
    permissions: dict[str, dict[str, dict[str, bool]]],
    app_root: str,
) -> str:
    """Compose the full report and return it as a single string."""
    sections: list[list[str]] = [
        _section_header(app_root),
        _section_corpus_check(detection),
        _section_summary(G, communities),
        _section_god_nodes(god_node_list),
        _section_surprising_connections(surprises),
        _section_communities(G, communities, community_labels, cohesion),
        _section_module_map(G, communities, community_labels),
        _section_hook_chain(G),
        _section_permission_matrix(permissions),
        _section_lifecycle_order(G),
        _section_workflow_diagrams(G),
        _section_notification_routing(G),
        _section_background_jobs(G),
        _section_customization_map(G),
        _section_controller_hierarchy(G),
        _section_knowledge_gaps(G, communities, community_labels),
        _section_suggested_questions(questions),
    ]
    return "\n".join(line for section in sections for line in section + [""])


# ── Header / corpus / summary ─────────────────────────────────────────────


def _section_header(app_root: str) -> list[str]:
    return [f"# Codemap Report — {app_root}  ({date.today().isoformat()})"]


def _section_corpus_check(detection: dict) -> list[str]:
    """File counts by extractor category."""
    lines = ["## Corpus Check"]
    files = detection.get("files", {})
    total = detection.get("total_files", sum(len(v) for v in files.values()))
    lines.append(f"- {total:,} files detected.")
    if files:
        ranked = sorted(
            ((cat, len(paths)) for cat, paths in files.items() if paths),
            key=lambda pair: (-pair[1], pair[0]),
        )
        body = " · ".join(f"{count} {cat}" for cat, count in ranked[:8])
        if body:
            lines.append(f"- {body}")
    return lines


def _section_summary(
    G: nx.Graph, communities: dict[int, list[str]],
) -> list[str]:
    """Node/edge counts plus the EXTRACTED / INFERRED / AMBIGUOUS split."""
    confidences = Counter(
        attrs.get("confidence", "EXTRACTED")
        for _, _, attrs in G.edges(data=True)
    )
    total = sum(confidences.values()) or 1
    breakdown = " · ".join(
        f"{round(confidences.get(level, 0) / total * 100)}% {level}"
        for level in ("EXTRACTED", "INFERRED", "AMBIGUOUS")
    )
    return [
        "## Summary",
        f"- {G.number_of_nodes():,} nodes · "
        f"{G.number_of_edges():,} edges · "
        f"{len(communities):,} communities",
        f"- Confidence: {breakdown}",
    ]


# ── God nodes / surprises ─────────────────────────────────────────────────


def _section_god_nodes(god_node_list: list[dict]) -> list[str]:
    if not god_node_list:
        return []
    lines = ["## God Nodes — most-connected entities"]
    for i, node in enumerate(god_node_list, 1):
        tag = " *(structural hub — expected for Frappe)*" if node.get(
            "is_structural_hub"
        ) else ""
        lines.append(
            f"{i}. `{node['label']}` — {node['degree']} edges "
            f"({node['file_type']}){tag}"
        )
    return lines


def _section_surprising_connections(surprises: list[dict]) -> list[str]:
    if not surprises:
        return []
    lines = ["## Surprising Connections"]
    for entry in surprises:
        lines.append(
            f"- `{entry['source']}` --{entry['relation']}--> "
            f"`{entry['target']}`  [{entry['confidence']}]"
        )
        why = entry.get("why")
        if why:
            lines.append(f"  _{why}_")
    return lines


# ── Communities & module map ──────────────────────────────────────────────


def _section_communities(
    G: nx.Graph,
    communities: dict[int, list[str]],
    labels: dict[int, str],
    cohesion: dict[int, float],
) -> list[str]:
    lines = ["## Communities"]
    for cid in sorted(communities):
        members = communities[cid]
        real = [m for m in members if G.nodes[m].get("file_type") != "file"]
        if not real:
            continue
        label = labels.get(cid, f"Community {cid}")
        score = cohesion.get(cid, 0.0)
        sample = [G.nodes[m].get("label", m) for m in real[:8]]
        more = f" (+{len(real) - 8} more)" if len(real) > 8 else ""
        lines += [
            "",
            f"### Community {cid} — \"{label}\"  (cohesion: {score})",
            f"{len(real)} members: {', '.join(f'`{l}`' for l in sample)}{more}",
        ]
    return lines


def _section_module_map(
    G: nx.Graph,
    communities: dict[int, list[str]],
    labels: dict[int, str],
) -> list[str]:
    """Show which Frappe module is the centre of mass for which community."""
    module_to_cid: dict[str, int] = {}
    node_to_cid = {n: cid for cid, members in communities.items() for n in members}

    for u, v, data in G.edges(data=True):
        if data.get("relation") != "belongs_to_module":
            continue
        src_id = data.get("_src", u)
        if G.nodes[src_id].get("file_type") != "doctype":
            continue
        module = data.get("module")
        if not module:
            continue
        cid = node_to_cid.get(src_id)
        if cid is not None:
            module_to_cid.setdefault(module, cid)

    if not module_to_cid:
        return []

    lines = ["## Module Map"]
    for module in sorted(module_to_cid):
        cid = module_to_cid[module]
        label = labels.get(cid, f"Community {cid}")
        lines.append(f"- **{module}** → Community {cid} (\"{label}\")")
    return lines


# ── Hook chain & lifecycle ────────────────────────────────────────────────


def _section_hook_chain(G: nx.Graph) -> list[str]:
    """List ``hooks.py`` registrations grouped by their target DocType."""
    by_doctype: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for u, v, data in G.edges(data=True):
        if data.get("relation") != "hooked_on":
            continue
        hook_id = data.get("_src", u)
        target_id = data.get("_tgt", v)
        target = G.nodes[target_id].get("label", target_id)
        hook_label = G.nodes[hook_id].get("label", hook_id)
        event = data.get("event") or ""
        by_doctype[target].append((hook_label, event))

    if not by_doctype:
        return []

    lines = ["## Hook Chain Summary"]
    for doctype in sorted(by_doctype):
        lines.append(f"### {doctype}")
        for hook_label, event in by_doctype[doctype]:
            event_tag = f" ({event})" if event else ""
            lines.append(f"- `{hook_label}`{event_tag}")
    return lines


def _section_lifecycle_order(G: nx.Graph) -> list[str]:
    """For each DocType controller, list lifecycle methods in fire order."""
    order_index = {name: i for i, name in enumerate(_LIFECYCLE_ORDER)}
    by_doctype: dict[str, list[str]] = defaultdict(list)

    for u, v, data in G.edges(data=True):
        if data.get("relation") not in ("lifecycle_method", "method"):
            continue
        src_id = data.get("_src", u)
        tgt_id = data.get("_tgt", v)
        if G.nodes[src_id].get("file_type") != "doctype":
            continue
        method_label = G.nodes[tgt_id].get("label", tgt_id)
        by_doctype[G.nodes[src_id].get("label", src_id)].append(method_label)

    if not by_doctype:
        return []

    def sort_key(method_label: str) -> tuple[int, str]:
        clean = method_label.lstrip(".").rstrip("()")
        return (order_index.get(clean, len(_LIFECYCLE_ORDER)), clean)

    lines = ["## Lifecycle Order"]
    for doctype in sorted(by_doctype):
        methods = sorted(set(by_doctype[doctype]), key=sort_key)
        lines.append(f"### {doctype}")
        for method in methods:
            lines.append(f"- `{method}`")
    return lines


# ── Permission matrix ─────────────────────────────────────────────────────


_PERMISSION_COLUMNS: tuple[str, ...] = (
    "read", "write", "create", "delete",
    "submit", "cancel", "amend",
)


def _section_permission_matrix(
    permissions: dict[str, dict[str, dict[str, bool]]],
) -> list[str]:
    if not permissions:
        return []

    lines = ["## Permission Matrix"]
    header = "| DocType | Role | " + " | ".join(_PERMISSION_COLUMNS) + " |"
    sep = "|" + "|".join(["---"] * (2 + len(_PERMISSION_COLUMNS))) + "|"
    lines += [header, sep]

    for doctype in sorted(permissions):
        for role in sorted(permissions[doctype]):
            flags = permissions[doctype][role]
            cells = " | ".join(
                "✓" if flags.get(col) else "✗" for col in _PERMISSION_COLUMNS
            )
            lines.append(f"| {doctype} | {role} | {cells} |")
    return lines


# ── Workflow diagrams ─────────────────────────────────────────────────────


def _section_workflow_diagrams(G: nx.Graph) -> list[str]:
    """One mermaid flowchart per Workflow node."""
    workflow_nodes = [
        n for n, attrs in G.nodes(data=True)
        if attrs.get("file_type") == "workflow"
    ]
    if not workflow_nodes:
        return []

    lines = ["## Workflow Diagrams"]
    for wf_id in sorted(workflow_nodes):
        wf_label = G.nodes[wf_id].get("label", wf_id)
        transitions = _workflow_transitions(G, wf_id)
        lines.append(f"### {wf_label}")
        if not transitions:
            lines.append("_No transitions extracted._")
            continue
        lines.append("```mermaid")
        lines.append("flowchart LR")
        for src, tgt, action in transitions:
            tag = f"|{action}|" if action else ""
            lines.append(
                f'  {_mermaid_id(src)}["{src}"] -->{tag} '
                f'{_mermaid_id(tgt)}["{tgt}"]'
            )
        lines.append("```")
    return lines


def _workflow_transitions(
    G: nx.Graph, wf_id: str,
) -> list[tuple[str, str, str]]:
    """Return ``(from_state, to_state, action)`` tuples for *wf_id*."""
    out: list[tuple[str, str, str]] = []
    for u, v, data in G.edges(data=True):
        if data.get("relation") != "workflow_transition":
            continue
        if data.get("workflow") != G.nodes[wf_id].get("label", wf_id):
            # Some extractors store the workflow ID instead.
            if data.get("workflow") != wf_id:
                continue
        src = data.get("from_state") or G.nodes[u].get("label", u)
        tgt = data.get("to_state") or G.nodes[v].get("label", v)
        out.append((str(src), str(tgt), str(data.get("action", ""))))
    return out


def _mermaid_id(label: str) -> str:
    """Mermaid node IDs must be alphanumeric/underscore."""
    return "".join(c if c.isalnum() else "_" for c in label) or "n"


# ── Notifications, background jobs, customizations ────────────────────────


def _section_notification_routing(G: nx.Graph) -> list[str]:
    notifications = [
        n for n, attrs in G.nodes(data=True)
        if attrs.get("file_type") == "notification"
    ]
    if not notifications:
        return []

    lines = ["## Email & Notification Routing"]
    for nid in sorted(notifications):
        attrs = G.nodes[nid]
        lines.append(f"### {attrs.get('label', nid)}")
        target = _first_neighbour_with_relation(G, nid, "notification_for")
        if target:
            lines.append(f"- Trigger: `{target}`")
        recipients = _all_neighbours_with_relation(
            G, nid, "notification_recipient",
        )
        if recipients:
            lines.append(f"- Recipients: {', '.join(f'`{r}`' for r in recipients)}")
    return lines


def _section_background_jobs(G: nx.Graph) -> list[str]:
    enqueued: list[tuple[str, str]] = []
    for u, v, data in G.edges(data=True):
        if data.get("relation") != "enqueues_job":
            continue
        src_id = data.get("_src", u)
        tgt_id = data.get("_tgt", v)
        enqueued.append((
            G.nodes[src_id].get("label", src_id),
            G.nodes[tgt_id].get("label", tgt_id),
        ))

    if not enqueued:
        return []

    lines = ["## Background Job Map"]
    for caller, target in sorted(enqueued):
        lines.append(f"- `{caller}` → enqueues `{target}`")
    return lines


_CUSTOMIZATION_TYPES: dict[str, str] = {
    "custom_field": "Custom Field",
    "property_setter": "Property Setter",
    "server_script": "Server Script",
    "client_script": "Client Script",
}


def _section_customization_map(G: nx.Graph) -> list[str]:
    """Group custom fields, property setters, and scripts by DocType."""
    by_doctype: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for node_id, attrs in G.nodes(data=True):
        kind = _CUSTOMIZATION_TYPES.get(attrs.get("file_type", ""))
        if not kind:
            continue
        target = _customization_target(G, node_id)
        if not target:
            continue
        by_doctype[target].append((kind, attrs.get("label", node_id)))

    if not by_doctype:
        return []

    lines = ["## Customization Map"]
    for doctype in sorted(by_doctype):
        lines.append(f"### {doctype}")
        for kind, label in sorted(by_doctype[doctype]):
            lines.append(f"- **{kind}**: `{label}`")
    return lines


def _customization_target(G: nx.Graph, node_id: str) -> str | None:
    """Return the DocType label this customization edits, if any."""
    for neighbour in G.neighbors(node_id):
        if G.nodes[neighbour].get("file_type") == "doctype":
            return G.nodes[neighbour].get("label", neighbour)
    return None


# ── Controller hierarchy ──────────────────────────────────────────────────


def _section_controller_hierarchy(G: nx.Graph) -> list[str]:
    """Walk ``inherits`` edges from every code class node to its base.

    We start each chain at a *leaf* (a class that no other class
    inherits from) so each printed chain is a complete path from
    subclass to ultimate base, never a fragment.
    """
    bases: set[str] = set()
    for u, v, data in G.edges(data=True):
        if data.get("relation") == "inherits":
            bases.add(data.get("_tgt", v))

    chains: list[str] = []
    for node_id, attrs in G.nodes(data=True):
        if attrs.get("file_type") != "code":
            continue
        if node_id in bases:
            continue
        chain = _trace_inheritance(G, node_id)
        if len(chain) >= 2:
            chains.append(" → ".join(f"`{c}`" for c in chain))

    if not chains:
        return []

    lines = ["## Controller Hierarchy"]
    for chain in sorted(set(chains)):
        lines.append(f"- {chain}")
    return lines


def _trace_inheritance(G: nx.Graph, start: str) -> list[str]:
    """Follow ``inherits`` edges from *start* up to the ultimate base.

    Only outbound inherits edges (where *start* is the recorded
    subclass via ``_src``) are traversed, so the chain reads
    subclass → base regardless of whether the underlying graph is
    directed.  Cycles are guarded.
    """
    visited: set[str] = {start}
    chain = [G.nodes[start].get("label", start)]
    current = start
    while True:
        next_id = _outbound_inherits_target(G, current)
        if not next_id or next_id in visited:
            break
        visited.add(next_id)
        chain.append(G.nodes[next_id].get("label", next_id))
        current = next_id
    return chain


def _outbound_inherits_target(G: nx.Graph, node_id: str) -> str | None:
    """Return the base class for *node_id*, if any.

    On undirected graphs each ``inherits`` edge is visited from both
    ends; we use the edge's stored ``_src`` to ensure we only walk
    forward (subclass → base) and never backwards.
    """
    for u, v, data in G.edges(node_id, data=True):
        if data.get("relation") != "inherits":
            continue
        if data.get("_src", u) != node_id:
            continue
        return data.get("_tgt", v if u == node_id else u)
    return None


# ── Knowledge gaps & questions ────────────────────────────────────────────


def _section_knowledge_gaps(
    G: nx.Graph,
    communities: dict[int, list[str]],
    labels: dict[int, str],
) -> list[str]:
    isolated = [
        n for n in G.nodes()
        if G.degree(n) <= 1
        and G.nodes[n].get("file_type") not in ("file", "external")
    ]

    thin = []
    for cid, members in communities.items():
        real = [m for m in members if G.nodes[m].get("file_type") != "file"]
        if 0 < len(real) < 3:
            thin.append((cid, real))

    ambiguous = [
        (u, v, data) for u, v, data in G.edges(data=True)
        if data.get("confidence") == "AMBIGUOUS"
    ]

    if not (isolated or thin or ambiguous):
        return []

    lines = ["## Knowledge Gaps"]
    if isolated:
        sample = [G.nodes[n].get("label", n) for n in isolated[:5]]
        more = f" (+{len(isolated) - 5} more)" if len(isolated) > 5 else ""
        lines.append(
            f"- **{len(isolated)} weakly-connected node(s)**: "
            f"{', '.join(f'`{l}`' for l in sample)}{more}"
        )
    if thin:
        for cid, members in thin:
            label = labels.get(cid, f"Community {cid}")
            names = [G.nodes[n].get("label", n) for n in members]
            lines.append(
                f"- **Thin community `{label}`**: "
                f"{', '.join(f'`{n}`' for n in names)}"
            )
    if ambiguous:
        lines.append(f"- **{len(ambiguous)} AMBIGUOUS edge(s)** awaiting review.")
    return lines


def _section_suggested_questions(questions: list[dict]) -> list[str]:
    if not questions:
        return []
    if len(questions) == 1 and questions[0].get("type") == "no_signal":
        return ["## Suggested Questions", f"_{questions[0].get('why', '')}_"]

    lines = ["## Suggested Questions"]
    for entry in questions:
        if not entry.get("question"):
            continue
        lines.append(f"- **{entry['question']}**")
        if entry.get("why"):
            lines.append(f"  _{entry['why']}_")
    return lines


# ── Tiny shared helpers ───────────────────────────────────────────────────


def _first_neighbour_with_relation(
    G: nx.Graph, node_id: str, relation: str,
) -> str | None:
    """Return the *label* of the first neighbour reached by *relation*."""
    target = _first_neighbour_with_relation_id(G, node_id, relation)
    if target is None:
        return None
    return G.nodes[target].get("label", target)


def _first_neighbour_with_relation_id(
    G: nx.Graph, node_id: str, relation: str,
) -> str | None:
    for u, v, data in G.edges(node_id, data=True):
        if data.get("relation") == relation:
            return v if u == node_id else u
    return None


def _all_neighbours_with_relation(
    G: nx.Graph, node_id: str, relation: str,
) -> list[str]:
    out: list[str] = []
    for u, v, data in G.edges(node_id, data=True):
        if data.get("relation") != relation:
            continue
        other = v if u == node_id else u
        out.append(G.nodes[other].get("label", other))
    return out
