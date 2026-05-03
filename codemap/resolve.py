"""Cross-file resolution for the two-pass extraction pipeline.

After each file is extracted in isolation, this module resolves
cross-file references:
1. Builds a global index of {entity_name → node_id} across all files.
2. Resolves unresolved calls (raw_calls) to actual node IDs.
3. Creates INFERRED edges for resolved calls, and placeholder nodes
   for truly unresolved symbols.
"""

from __future__ import annotations

from .graph_primitives import make_edge, make_id, make_node


def resolve_cross_file(
    all_results: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Resolve cross-file references across all per-file extraction results.

    Args:
        all_results: list of dicts from extract_python(), each with
                     'nodes', 'edges', and 'raw_calls'.

    Returns:
        A tuple of (new_nodes, new_edges) to add to the merged graph.
        These are the cross-file calls that were successfully resolved,
        plus placeholder nodes for anything that couldn't be resolved.
    """
    # ── Step 1: Build global index ─────────────────────────────────────────
    # Maps normalised label → node_id
    global_index: dict[str, str] = {}
    existing_ids: set[str] = set()

    for result in all_results:
        for node in result.get("nodes", []):
            existing_ids.add(node["id"])
            raw_label = node["label"]
            # Normalise: strip parens and leading dots
            normalised = raw_label.strip("()").lstrip(".").lower()
            if normalised:
                global_index[normalised] = node["id"]

    # ── Step 2: Resolve raw calls ──────────────────────────────────────────
    new_nodes: list[dict] = []
    new_edges: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    created_placeholders: set[str] = set()

    for result in all_results:
        for call in result.get("raw_calls", []):
            caller_nid = call["caller_nid"]
            callee_name = call["callee"]
            source_file = call["source_file"]
            line = call["line"]

            # Try to resolve the callee name
            normalised = callee_name.lower()
            tgt_nid = global_index.get(normalised)

            if tgt_nid and tgt_nid != caller_nid:
                pair = (caller_nid, tgt_nid)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    new_edges.append(make_edge(
                        caller_nid, tgt_nid, "calls",
                        source_file, line,
                        confidence="INFERRED",
                    ))

            elif not tgt_nid:
                # Create a placeholder node for unresolved symbols
                placeholder_nid = make_id("external", callee_name)
                if placeholder_nid not in existing_ids and placeholder_nid not in created_placeholders:
                    created_placeholders.add(placeholder_nid)
                    new_nodes.append(make_node(
                        placeholder_nid,
                        f"{callee_name}()",
                        "external",
                        "",
                        0, 0,
                    ))

    return new_nodes, new_edges
