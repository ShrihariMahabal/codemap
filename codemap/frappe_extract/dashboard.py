"""Dashboard extractor (Phase 4c).

A Frappe ``{doctype}_dashboard.py`` file lives next to its DocType JSON
and defines a ``get_data()`` function that returns a dict with two keys
we care about:

- ``internal_links``: ``{"DocType Name": [field, ...], ...}`` — DocTypes
  reachable through child-table fields on this DocType.
- ``transactions``: ``[{"label": ..., "items": ["DocType", ...]}, ...]``
  — DocTypes grouped into UI sections on the dashboard.

For every DocType found in either structure we emit a ``dashboard_link``
edge from the source DocType (derived from the parent directory name) to
the related DocType.

We can't use ``ast.literal_eval`` on the return value because dashboards
typically wrap section labels in the i18n function ``_("...")``, which is
a Call node and not a literal.  Instead we walk the AST manually and
ignore any non-string-literal pieces.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator

from ..graph_primitives import make_edge, make_id
from ._common import empty_result


_DASHBOARD_SUFFIX = "_dashboard"


def extract_dashboard(path: Path) -> dict:
    """Extract ``dashboard_link`` edges from a single dashboard.py file."""
    source_doctype = _source_doctype(path)
    if not source_doctype:
        return empty_result()

    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return empty_result()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return empty_result()

    return_node = _find_get_data_return(tree)
    if return_node is None:
        return empty_result()

    str_path = str(path)
    src_nid = make_id(source_doctype)
    edges: list[dict] = []
    seen: set[str] = set()

    for doctype_name, line in _iter_dashboard_doctypes(return_node):
        target_nid = make_id(doctype_name)
        # Skip self-loops (a DocType linked to itself isn't useful) and
        # duplicates (the same DocType can appear in multiple sections).
        if target_nid == src_nid or target_nid in seen:
            continue
        seen.add(target_nid)
        edges.append(make_edge(
            src_nid, target_nid, "dashboard_link",
            str_path, line,
            doctype=doctype_name,
        ))

    return {"nodes": [], "edges": edges}


# ── Internals ────────────────────────────────────────────────────────────────

def _source_doctype(path: Path) -> str | None:
    """Derive the source DocType from the dashboard file's path.

    Convention: ``.../doctype/{doctype_name}/{doctype_name}_dashboard.py``,
    where ``{doctype_name}`` is the snake_case form of the DocType.  We
    use the parent directory name and rely on ``make_id`` to normalise
    later — ``make_id("sales_order") == make_id("Sales Order")``.
    """
    if not path.stem.endswith(_DASHBOARD_SUFFIX):
        return None
    parent = path.parent.name
    return parent or None


def _find_get_data_return(tree: ast.Module) -> ast.AST | None:
    """Locate the ``return`` value inside the top-level ``def get_data()``.

    Only the first such function is considered.  If ``get_data`` returns
    via multiple paths, only the first ``return`` statement we encounter
    is used — Frappe dashboards conventionally have a single return.
    """
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "get_data":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    return sub.value
    return None


def _iter_dashboard_doctypes(
    return_node: ast.AST,
) -> Iterator[tuple[str, int]]:
    """Yield ``(doctype_name, line_number)`` pairs from the return dict.

    Walks two specific keys:

    - ``internal_links`` — keys are DocType names, values are field paths
      (which we ignore — only the keys are graph-relevant).
    - ``transactions`` — list of ``{"items": [DocType, ...]}`` dicts.
    """
    if not isinstance(return_node, ast.Dict):
        return

    for key, value in zip(return_node.keys, return_node.values):
        outer_key = _str_const(key)

        if outer_key == "internal_links":
            yield from _iter_internal_links(value)
        elif outer_key == "transactions":
            yield from _iter_transactions(value)


def _iter_internal_links(node: ast.AST) -> Iterator[tuple[str, int]]:
    if not isinstance(node, ast.Dict):
        return
    for key in node.keys:
        name = _str_const(key)
        if name:
            yield name, getattr(key, "lineno", 1)


def _iter_transactions(node: ast.AST) -> Iterator[tuple[str, int]]:
    if not isinstance(node, ast.List):
        return
    for entry in node.elts:
        if not isinstance(entry, ast.Dict):
            continue
        for inner_key, inner_value in zip(entry.keys, entry.values):
            if _str_const(inner_key) != "items":
                continue
            if not isinstance(inner_value, ast.List):
                continue
            for item in inner_value.elts:
                name = _str_const(item)
                if name:
                    yield name, getattr(item, "lineno", 1)


def _str_const(node: ast.AST | None) -> str | None:
    """Return the string value of a literal AST node, else ``None``.

    Anything that isn't a plain string literal — including ``_("Label")``
    i18n calls and variable references — produces ``None`` so the caller
    can skip it.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None
