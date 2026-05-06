"""Assemble extractor output into a NetworkX graph.

Each extractor in :mod:`codemap` returns a small dict of the form::

    {"nodes": [...], "edges": [...], "raw_calls": [...]}

``build()`` flattens many of these dicts into a single graph.  The
``raw_calls`` list is consumed earlier by :mod:`codemap.resolve` — by
the time we get here every edge endpoint is supposed to be a real node
ID.  Dangling edges still happen in practice (an import targeting a
third-party module, an inferred call we couldn't resolve), and we drop
them silently so the graph stays well-formed.

Three layers of node deduplication keep the graph honest:

1. **Within a file.**  Each extractor uses ``make_id()`` and a local
   ``seen`` set to ensure the same entity isn't emitted twice from one
   source file.
2. **Between files.**  ``G.add_node()`` is idempotent — re-adding a
   node merges its attributes with the existing entry.  Later
   extractions overwrite earlier ones, so cross-file resolution
   results take precedence over the placeholder nodes the AST pass
   inserts for missing imports.
3. **Across runs.**  :func:`build_merge` reads an existing
   ``graph.json``, treats it as an extra extraction, and refuses to
   shrink the graph silently.

The graph itself is undirected by default.  Pass ``directed=True`` if
you want a ``DiGraph`` (useful for call-chain queries that care about
direction); the original endpoint order survives on every edge as
``_src`` / ``_tgt`` either way.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import networkx as nx


# ── ID normalisation ───────────────────────────────────────────────────────

_ID_PATTERN = re.compile(r"[^a-zA-Z0-9]+")


def _normalize_id(node_id: str) -> str:
    """Lower-case alphanumeric form of *node_id*.

    Used to repair edges whose endpoints were spelled with a slightly
    different punctuation pattern than the corresponding node.  Mirrors
    :func:`codemap.graph_primitives.make_id` so the two stay in sync.
    """
    return _ID_PATTERN.sub("_", node_id).strip("_").lower()


# ── Public API ─────────────────────────────────────────────────────────────


def build_from_extraction(
    extraction: dict, *, directed: bool = False,
) -> nx.Graph:
    """Build a graph from one extraction dict.

    Unknown fields on nodes/edges are kept verbatim as attributes — the
    report and analysis layers read them directly, so we don't filter.
    """
    G: nx.Graph = nx.DiGraph() if directed else nx.Graph()

    for node in extraction.get("nodes", []):
        node_id = node.get("id")
        if not node_id:
            continue
        attrs = {k: v for k, v in node.items() if k != "id"}
        G.add_node(node_id, **attrs)

    _add_edges(G, extraction.get("edges", []))
    return G


def build(
    extractions: list[dict], *, directed: bool = False,
) -> nx.Graph:
    """Merge many extraction dicts into a single graph.

    Order matters when two extractions touch the same node ID: later
    entries overwrite earlier ones via NetworkX's idempotent
    ``add_node``.  Pass cross-file resolution results last — they
    typically carry richer labels than the placeholder nodes the AST
    pass emits.
    """
    G: nx.Graph = nx.DiGraph() if directed else nx.Graph()

    for extraction in extractions:
        for node in extraction.get("nodes", []):
            node_id = node.get("id")
            if not node_id:
                continue
            attrs = {k: v for k, v in node.items() if k != "id"}
            G.add_node(node_id, **attrs)

    for extraction in extractions:
        _add_edges(G, extraction.get("edges", []))

    return G


def build_merge(
    new_extractions: list[dict],
    graph_path: str | Path = "codemap-out/graph.json",
    *,
    prune_sources: list[str] | None = None,
    directed: bool = False,
) -> nx.Graph:
    """Load ``graph.json`` (if any), merge new extractions, return graph.

    ``prune_sources`` removes nodes whose ``source_file`` matches one of
    the listed paths — the caller passes the list of files deleted
    since the last run.  The shrink guard at the end refuses to write
    a smaller graph unless something was explicitly pruned.
    """
    graph_path = Path(graph_path)
    base: list[dict] = []
    existing_count = 0

    if graph_path.exists():
        existing = _load_graph_json(graph_path)
        base.append(existing)
        existing_count = len(existing.get("nodes", []))

    G = build(base + list(new_extractions), directed=directed)

    if prune_sources:
        prune_set = set(prune_sources)
        to_remove = [
            n for n, attrs in G.nodes(data=True)
            if attrs.get("source_file") in prune_set
        ]
        if to_remove:
            G.remove_nodes_from(to_remove)
            print(
                f"[codemap] Pruned {len(to_remove)} node(s) from deleted sources.",
                file=sys.stderr,
            )

    if existing_count and not prune_sources:
        if G.number_of_nodes() < existing_count:
            raise ValueError(
                f"build_merge would shrink graph from {existing_count} → "
                f"{G.number_of_nodes()} nodes.  Pass prune_sources explicitly "
                f"if you intend to remove nodes."
            )

    return G


# ── Internals ──────────────────────────────────────────────────────────────


def _add_edges(G: nx.Graph, edges: list[dict]) -> None:
    """Insert edges into *G*, repairing or dropping bad endpoints.

    We accept three failure modes silently:

    - missing ``source`` / ``target`` keys (malformed extractor output);
    - endpoint IDs that almost match an existing node (whitespace or
      punctuation drift) — repaired via :func:`_normalize_id`;
    - endpoints that match nothing at all (cross-file references to
      stdlib or third-party code) — dropped.
    """
    if not edges:
        return

    node_set = set(G.nodes())
    norm_to_id = {_normalize_id(n): n for n in node_set}

    for edge in edges:
        src = edge.get("source")
        tgt = edge.get("target")
        if not src or not tgt:
            continue

        if src not in node_set:
            src = norm_to_id.get(_normalize_id(src), src)
        if tgt not in node_set:
            tgt = norm_to_id.get(_normalize_id(tgt), tgt)

        if src not in node_set or tgt not in node_set:
            continue

        attrs = {k: v for k, v in edge.items() if k not in ("source", "target")}
        # Preserve the original direction on undirected graphs so the
        # report renderer can still print "A → B" the right way round.
        attrs.setdefault("_src", src)
        attrs.setdefault("_tgt", tgt)
        G.add_edge(src, tgt, **attrs)


def _load_graph_json(path: Path) -> dict:
    """Read a saved graph back into the {nodes, edges} extraction shape."""
    data = json.loads(path.read_text(encoding="utf-8"))

    raw_nodes = data.get("nodes", [])
    raw_edges = data.get("edges") or data.get("links") or []

    nodes: list[dict] = []
    for node in raw_nodes:
        if "id" not in node:
            continue
        nodes.append(dict(node))

    edges: list[dict] = []
    for edge in raw_edges:
        e = dict(edge)
        if "source" not in e and "from" in e:
            e["source"] = e.pop("from")
        if "target" not in e and "to" in e:
            e["target"] = e.pop("to")
        edges.append(e)

    return {"nodes": nodes, "edges": edges}
