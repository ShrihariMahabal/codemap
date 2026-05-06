"""Community detection on a codemap graph.

Communities are the report's primary unit of grouping: each one becomes
a section in ``CODEMAP_REPORT.md`` and a coloured cluster in
``graph.html``.  We use Leiden when ``graspologic`` is available
(better quality, handles weighted graphs natively) and fall back to the
Louvain implementation built into NetworkX otherwise.

Two adjustments make the results read well for Frappe apps:

- **Oversized communities are split.**  Leiden occasionally collapses
  half the graph into a single community.  We re-run the algorithm on
  any community that exceeds 25% of the total node count, which yields
  more focused groupings without us having to tune resolution.
- **Communities are named after Frappe modules.**  Every DocType node
  carries a ``belongs_to_module`` edge.  When a community has a clear
  module majority we label it after that module, so the report can say
  "Selling" instead of "Community 3".
"""

from __future__ import annotations

import contextlib
import inspect
import io
import sys
from collections import Counter

import networkx as nx


# ── Partitioning ───────────────────────────────────────────────────────────


def _suppress_stdout():
    """Swallow library chatter (graspologic prints ANSI progress)."""
    return contextlib.redirect_stdout(io.StringIO())


def _partition(G: nx.Graph) -> dict[str, int]:
    """Return ``{node_id: community_id}`` for the input graph.

    Tries Leiden first; falls back to Louvain.  Suppressing stderr
    around the Leiden call keeps PowerShell scroll buffers happy on
    Windows — graspologic emits ANSI escape codes that some terminals
    don't render correctly.
    """
    try:
        from graspologic.partition import leiden  # type: ignore
    except ImportError:
        return _louvain_partition(G)

    saved_stderr = sys.stderr
    try:
        sys.stderr = io.StringIO()
        with _suppress_stdout():
            return leiden(G)
    finally:
        sys.stderr = saved_stderr


def _louvain_partition(G: nx.Graph) -> dict[str, int]:
    """NetworkX Louvain with version-tolerant kwargs."""
    kwargs: dict = {"seed": 42, "threshold": 1e-4}
    sig = inspect.signature(nx.community.louvain_communities).parameters
    if "max_level" in sig:
        kwargs["max_level"] = 10
    communities = nx.community.louvain_communities(G, **kwargs)
    return {
        node: cid
        for cid, members in enumerate(communities)
        for node in members
    }


# ── Public API ─────────────────────────────────────────────────────────────


_MAX_COMMUNITY_FRACTION = 0.25
_MIN_SPLIT_SIZE = 10


def cluster(G: nx.Graph) -> dict[int, list[str]]:
    """Return ``{community_id: [node_ids]}`` for *G*.

    Community IDs are stable across runs of the same input (sorted by
    descending size, ties broken by sorted node IDs).  Isolated nodes
    each become their own one-node community so the report can still
    mention them.
    """
    if G.number_of_nodes() == 0:
        return {}

    if G.is_directed():
        G = G.to_undirected()

    if G.number_of_edges() == 0:
        return {i: [n] for i, n in enumerate(sorted(G.nodes()))}

    isolates = [n for n in G.nodes() if G.degree(n) == 0]
    connected = G.subgraph([n for n in G.nodes() if G.degree(n) > 0])

    raw: dict[int, list[str]] = {}
    if connected.number_of_nodes() > 0:
        for node, cid in _partition(connected).items():
            raw.setdefault(cid, []).append(node)

    next_cid = max(raw.keys(), default=-1) + 1
    for node in isolates:
        raw[next_cid] = [node]
        next_cid += 1

    max_size = max(
        _MIN_SPLIT_SIZE,
        int(G.number_of_nodes() * _MAX_COMMUNITY_FRACTION),
    )
    final: list[list[str]] = []
    for nodes in raw.values():
        if len(nodes) > max_size:
            final.extend(_split_community(G, nodes))
        else:
            final.append(nodes)

    final.sort(key=lambda c: (-len(c), c[0] if c else ""))
    return {i: sorted(nodes) for i, nodes in enumerate(final)}


def cohesion_score(G: nx.Graph, community_nodes: list[str]) -> float:
    """Fraction of possible intra-community edges that actually exist.

    ``1.0`` for a clique, ``0.0`` for an edgeless community, ``1.0``
    for a singleton (vacuously cohesive).  Use this to flag
    low-cohesion communities in the report.
    """
    n = len(community_nodes)
    if n <= 1:
        return 1.0
    sub = G.subgraph(community_nodes)
    possible = n * (n - 1) / 2
    return round(sub.number_of_edges() / possible, 2) if possible else 0.0


def score_all(
    G: nx.Graph, communities: dict[int, list[str]],
) -> dict[int, float]:
    """Per-community cohesion scores keyed by community ID."""
    return {cid: cohesion_score(G, nodes) for cid, nodes in communities.items()}


# ── Frappe module labelling ────────────────────────────────────────────────


_MODULE_DOMINANCE_THRESHOLD = 0.5


def label_communities(
    G: nx.Graph, communities: dict[int, list[str]],
) -> dict[int, str]:
    """Return ``{community_id: label}`` using Frappe module hints.

    For each community we count how many member DocTypes belong to
    each module (via ``belongs_to_module`` edges).  If one module
    accounts for more than half of the labelled DocTypes the community
    inherits that module's name.  Otherwise the label falls back to a
    generic ``"Community N"``.
    """
    doctype_to_module = _doctype_module_map(G)
    labels: dict[int, str] = {}

    for cid, nodes in communities.items():
        module_counts: Counter[str] = Counter()
        labelled = 0
        for node_id in nodes:
            module = doctype_to_module.get(node_id)
            if module:
                module_counts[module] += 1
                labelled += 1

        if labelled and module_counts:
            top_module, top_count = module_counts.most_common(1)[0]
            if top_count / labelled >= _MODULE_DOMINANCE_THRESHOLD:
                labels[cid] = top_module
                continue

        labels[cid] = f"Community {cid}"

    return labels


def _doctype_module_map(G: nx.Graph) -> dict[str, str]:
    """Map every DocType node ID to the human-readable module name.

    The module name comes from the ``module=`` attribute on the
    ``belongs_to_module`` edge (set by the DocType extractor).  We
    prefer the attribute over the target node label because the
    module node ID is normalised (e.g. ``stock_management``) while
    the attribute preserves the original casing (``Stock Management``).
    """
    mapping: dict[str, str] = {}
    for u, v, data in G.edges(data=True):
        if data.get("relation") != "belongs_to_module":
            continue
        src_id = data.get("_src", u)
        if src_id not in G.nodes:
            src_id = u
        tgt_id = data.get("_tgt", v)
        if tgt_id not in G.nodes:
            tgt_id = v

        module_name = data.get("module") or G.nodes[tgt_id].get("label", "")
        if not module_name:
            continue

        if G.nodes[src_id].get("file_type") == "doctype":
            mapping[src_id] = module_name
    return mapping


# ── Internals ──────────────────────────────────────────────────────────────


def _split_community(G: nx.Graph, nodes: list[str]) -> list[list[str]]:
    """Re-partition an oversized community by re-running Leiden on it."""
    sub = G.subgraph(nodes)
    if sub.number_of_edges() == 0:
        return [[n] for n in sorted(nodes)]

    try:
        partition = _partition(sub)
    except Exception:
        return [sorted(nodes)]

    by_cid: dict[int, list[str]] = {}
    for node, cid in partition.items():
        by_cid.setdefault(cid, []).append(node)

    if len(by_cid) <= 1:
        return [sorted(nodes)]
    return [sorted(group) for group in by_cid.values()]
