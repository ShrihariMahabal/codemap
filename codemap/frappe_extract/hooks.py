"""hooks.py extractor (Phase 4b).

Frappe's ``hooks.py`` is a regular Python module: top-level assignments
map well-known hook names to dicts and lists.  We parse the file with
``ast.parse()`` and walk every top-level ``Assign`` statement.  For each
hook variable we recognise, a dedicated handler walks the right-hand
side AST and emits the corresponding nodes/edges.

Why we walk the AST instead of ``ast.literal_eval`` the whole RHS:

ERPNext (and other apps) use Python expressions inside hook dicts that
``literal_eval`` rejects — e.g. ``tuple(period_closing_doctypes)`` as a
dict key, or string concatenation.  The plan calls for skipping such
entries with a warning rather than failing the whole file, which is
straightforward when we control the walk ourselves.

Hooks recognised here (the ones that produce graph edges):

- ``doc_events``           → ``hooked_on``       (handler → DocType)
- ``override_whitelisted_methods``
                           → ``overrides``       (new → original)
- ``extend_doctype_class`` → ``overrides``       (custom class → DocType)
- ``doctype_js``           → ``extends_client``  (js path → DocType)
- ``scheduler_events``     → schedule metadata on hook nodes

Everything else (``app_name``, ``app_title``, install hooks, etc.) is
intentionally ignored — those are configuration, not graph edges.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ..graph_primitives import make_edge, make_id, make_node
from ._common import empty_result


def extract_hooks(path: Path) -> dict:
    """Parse a Frappe ``hooks.py`` and emit graph data for known hooks."""
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return empty_result()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return empty_result()

    str_path = str(path)
    ctx = _Context(str_path=str_path)

    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        # Only handle simple ``name = value`` assignments.  Tuple/multi-
        # target assignments aren't used for hook variables.
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            continue

        handler = _HOOK_HANDLERS.get(stmt.targets[0].id)
        if handler is not None:
            handler(stmt.value, ctx)

    return {"nodes": ctx.nodes, "edges": ctx.edges}


# ── Walk context ─────────────────────────────────────────────────────────────

class _Context:
    """Mutable accumulator threaded through the hook handlers.

    Keeping this in a small dataclass-style object means each handler
    function takes just two arguments (the AST node and the context),
    which keeps signatures uniform and makes the handler dispatch table
    below readable at a glance.
    """

    __slots__ = ("str_path", "nodes", "edges", "_seen")

    def __init__(self, str_path: str) -> None:
        self.str_path = str_path
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self._seen: set[str] = set()

    def add_hook_node(self, dotted: str, line: int, **extra) -> str:
        """Create a ``hook`` node for *dotted* (idempotent).

        Returns the node ID so callers can use it as the source of an edge.
        """
        nid = make_id(dotted)
        if nid not in self._seen:
            self._seen.add(nid)
            self.nodes.append(make_node(
                nid, dotted, "hook", self.str_path,
                line, line,
                **extra,
            ))
        return nid


# ── Handlers per hook variable ───────────────────────────────────────────────

def _handle_doc_events(value: ast.AST, ctx: _Context) -> None:
    """``doc_events = {"DocType": {"event": "handler" | [handlers]}}``."""
    if not isinstance(value, ast.Dict):
        return

    for doctype_node, events_node in zip(value.keys, value.values):
        doctype = _str_const(doctype_node)
        # Skip non-literal keys like ``tuple(period_closing_doctypes)``.
        if doctype is None or not isinstance(events_node, ast.Dict):
            continue

        line = getattr(doctype_node, "lineno", 1)
        doctype_nid = make_id(doctype)

        for event_node, handlers_node in zip(events_node.keys, events_node.values):
            event = _str_const(event_node)
            if event is None:
                continue

            for handler in _str_or_list(handlers_node):
                handler_nid = ctx.add_hook_node(handler, line)
                ctx.edges.append(make_edge(
                    handler_nid, doctype_nid, "hooked_on",
                    ctx.str_path, line,
                    event=event, doctype=doctype, handler=handler,
                ))


def _handle_scheduler_events(value: ast.AST, ctx: _Context) -> None:
    """``scheduler_events = {"daily": [...], "cron": {"expr": [...]}}``.

    Stored as metadata on the hook node — there's no DocType target to
    edge to, so we attach the schedule (e.g. ``"daily"`` or
    ``"cron:0 * * * *"``) as a node attribute.
    """
    if not isinstance(value, ast.Dict):
        return

    for freq_node, val_node in zip(value.keys, value.values):
        frequency = _str_const(freq_node)
        if frequency is None:
            continue
        line = getattr(freq_node, "lineno", 1)

        if frequency == "cron" and isinstance(val_node, ast.Dict):
            for expr_node, handlers_node in zip(val_node.keys, val_node.values):
                cron_expr = _str_const(expr_node)
                if cron_expr is None:
                    continue
                expr_line = getattr(expr_node, "lineno", line)
                for handler in _str_or_list(handlers_node):
                    ctx.add_hook_node(
                        handler, expr_line,
                        schedule=f"cron:{cron_expr}",
                    )
            continue

        for handler in _str_or_list(val_node):
            ctx.add_hook_node(handler, line, schedule=frequency)


def _handle_override_whitelisted_methods(value: ast.AST, ctx: _Context) -> None:
    """``override_whitelisted_methods = {"orig.path": "new.path"}``.

    Edge direction: replacement ``--overrides-->`` original.
    """
    if not isinstance(value, ast.Dict):
        return

    for orig_node, new_node in zip(value.keys, value.values):
        original = _str_const(orig_node)
        replacement = _str_const(new_node)
        if not original or not replacement:
            continue
        line = getattr(orig_node, "lineno", 1)
        new_nid = ctx.add_hook_node(replacement, line)
        ctx.edges.append(make_edge(
            new_nid, make_id(original), "overrides",
            ctx.str_path, line,
            original=original, replacement=replacement,
        ))


def _handle_extend_doctype_class(value: ast.AST, ctx: _Context) -> None:
    """``extend_doctype_class = {"DocType": "app.CustomClass"}``.

    Edge direction: custom class ``--overrides-->`` DocType.
    """
    if not isinstance(value, ast.Dict):
        return

    for doctype_node, class_node in zip(value.keys, value.values):
        doctype = _str_const(doctype_node)
        custom_class = _str_const(class_node)
        if not doctype or not custom_class:
            continue
        line = getattr(doctype_node, "lineno", 1)
        class_nid = ctx.add_hook_node(custom_class, line)
        ctx.edges.append(make_edge(
            class_nid, make_id(doctype), "overrides",
            ctx.str_path, line,
            doctype=doctype, custom_class=custom_class,
        ))


def _handle_doctype_js(value: ast.AST, ctx: _Context) -> None:
    """``doctype_js = {"DocType": "path/to.js" | [paths]}``.

    Each path becomes the source of an ``extends_client`` edge to the
    DocType.  We don't create a node for the path here — the JS extractor
    will create the file node when it parses the JS file.
    """
    if not isinstance(value, ast.Dict):
        return

    for doctype_node, paths_node in zip(value.keys, value.values):
        doctype = _str_const(doctype_node)
        if not doctype:
            continue
        line = getattr(doctype_node, "lineno", 1)
        doctype_nid = make_id(doctype)

        for js_path in _str_or_list(paths_node):
            ctx.edges.append(make_edge(
                make_id(js_path), doctype_nid, "extends_client",
                ctx.str_path, line,
                doctype=doctype, js_path=js_path,
            ))


# Dispatch table — extending support for new hooks is just one entry here.
_HOOK_HANDLERS = {
    "doc_events": _handle_doc_events,
    "scheduler_events": _handle_scheduler_events,
    "override_whitelisted_methods": _handle_override_whitelisted_methods,
    "extend_doctype_class": _handle_extend_doctype_class,
    "doctype_js": _handle_doctype_js,
}


# ── AST helpers ──────────────────────────────────────────────────────────────

def _str_const(node: ast.AST | None) -> str | None:
    """Return the value of a string-literal AST node, else ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _str_or_list(node: ast.AST | None) -> list[str]:
    """Coerce ``"x"`` or ``["x", "y"]`` into a list of strings.

    Anything else (a function call, a variable reference, a number) is
    silently skipped — Frappe hooks rarely use such values for handlers,
    and when they do we can't statically resolve them anyway.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [
            v for elt in node.elts
            if (v := _str_const(elt)) is not None
        ]
    return []
