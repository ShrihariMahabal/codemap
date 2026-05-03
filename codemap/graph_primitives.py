"""Shared helpers for building stable node IDs and graph primitives.

Every node and edge in the codemap graph uses a stable, deterministic ID
derived from the file path and entity name. This module provides the
ID-generation logic and data structures used by all extractors.
"""

from __future__ import annotations

import re


def make_id(*parts: str) -> str:
    """Build a stable, lowercase node ID from one or more name parts.

    Examples:
        make_id("sales_order", "SalesOrder")  → "sales_order_salesorder"
        make_id("erpnext.controllers.selling_controller")
            → "erpnext_controllers_selling_controller"
    """
    combined = "_".join(p.strip("_.") for p in parts if p)
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", combined)
    return cleaned.strip("_").lower()


def make_node(
    node_id: str,
    label: str,
    file_type: str,
    source_file: str,
    line_start: int,
    line_end: int,
    **extra: object,
) -> dict:
    """Create a graph node dict with required fields.

    Every node carries source line info so the triage HTML renderer
    can show exact source snippets inline.
    """
    node = {
        "id": node_id,
        "label": label,
        "file_type": file_type,
        "source_file": source_file,
        "source_line_start": line_start,
        "source_line_end": line_end,
    }
    node.update(extra)
    return node


def make_edge(
    source: str,
    target: str,
    relation: str,
    source_file: str,
    line: int,
    confidence: str = "EXTRACTED",
    **extra: object,
) -> dict:
    """Create a graph edge dict."""
    edge = {
        "source": source,
        "target": target,
        "relation": relation,
        "confidence": confidence,
        "source_file": source_file,
        "source_location": f"L{line}",
    }
    edge.update(extra)
    return edge


def read_node_text(node, source: bytes) -> str:
    """Read the source text of a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
