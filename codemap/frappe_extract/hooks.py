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


def _handle_permission_hook(value: ast.AST, ctx: _Context) -> None:
    """``permission_query_conditions``, ``has_permission``,
    ``has_website_permission`` — ``{DocType: handler}``.

    Each entry is a single function dotted path that overrides Frappe's
    default access checks for the named DocType.  We emit a hook node
    tagged ``role = "permission"`` and a ``permission_hook`` edge from
    the handler to the DocType.
    """
    if not isinstance(value, ast.Dict):
        return

    for doctype_node, handler_node in zip(value.keys, value.values):
        doctype = _str_const(doctype_node)
        handler = _str_const(handler_node)
        if not doctype or not handler:
            continue
        line = getattr(doctype_node, "lineno", 1)
        handler_nid = ctx.add_hook_node(handler, line, role="permission")
        ctx.edges.append(make_edge(
            handler_nid, make_id(doctype), "permission_hook",
            ctx.str_path, line,
            doctype=doctype, handler=handler,
        ))


def _handle_override_doctype_class(value: ast.AST, ctx: _Context) -> None:
    """``override_doctype_class = {"DocType": "app.CustomClass"}``.

    Frappe v14+ alias for ``extend_doctype_class``.  Same edge shape.
    """
    _handle_extend_doctype_class(value, ctx)


def _handle_override_doctype_dashboards(value: ast.AST, ctx: _Context) -> None:
    """``override_doctype_dashboards = {"DocType": "app.module.get_data"}``.

    Replaces the dashboard ``get_data`` for a DocType.  Edge: handler
    overrides DocType's dashboard.
    """
    if not isinstance(value, ast.Dict):
        return

    for doctype_node, handler_node in zip(value.keys, value.values):
        doctype = _str_const(doctype_node)
        handler = _str_const(handler_node)
        if not doctype or not handler:
            continue
        line = getattr(doctype_node, "lineno", 1)
        handler_nid = ctx.add_hook_node(handler, line, role="dashboard_override")
        ctx.edges.append(make_edge(
            handler_nid, make_id(doctype), "overrides",
            ctx.str_path, line,
            doctype=doctype, handler=handler,
            scope="dashboard",
        ))


def _handle_jinja(value: ast.AST, ctx: _Context) -> None:
    """``jinja = {"methods": [...], "filters": [...]}``.

    Each entry registers a Python callable as a Jinja method or filter.
    No DocType target — we just record the callable as a hook node so
    template extraction can resolve ``{{ my_method() }}`` later.
    """
    if not isinstance(value, ast.Dict):
        return

    for kind_node, items_node in zip(value.keys, value.values):
        kind = _str_const(kind_node)
        if kind not in ("methods", "filters"):
            continue
        line = getattr(kind_node, "lineno", 1)
        for handler in _str_or_list(items_node):
            # Frappe lets each entry be either ``"app.fn"`` or
            # ``"label:app.fn"``.  We split on ``:`` and keep the
            # right-hand callable as the node identifier.
            callable_path = handler.split(":", 1)[-1].strip()
            if not callable_path:
                continue
            ctx.add_hook_node(
                callable_path, line,
                role="jinja",
                jinja_kind=kind,
            )


def _handle_simple_callable_list(role: str):
    """Build a handler for hooks that take a string or list of dotted paths.

    Used by ``before_request``, ``after_request``, ``boot_session``,
    ``extend_bootinfo``, ``on_session_creation``, ``on_logout``,
    ``notification_config``, etc. — anything where the value is just a
    list of functions Frappe runs at a specific time.
    """
    def handler(value: ast.AST, ctx: _Context) -> None:
        line = getattr(value, "lineno", 1)
        for handler_path in _str_or_list(value):
            ctx.add_hook_node(handler_path, line, role=role)
    return handler


def _handle_static_asset_list(kind: str):
    """Build a handler for app_include_js / web_include_css / etc.

    These are lists of asset paths the framework injects into the HTML
    shell.  We register them as hook nodes so the report can show
    "what assets does this app inject?" without re-parsing hooks.py.
    """
    def handler(value: ast.AST, ctx: _Context) -> None:
        line = getattr(value, "lineno", 1)
        for asset_path in _str_or_list(value):
            ctx.add_hook_node(
                asset_path, line,
                role="app_include",
                asset_kind=kind,
            )
    return handler


def _handle_auto_cancel_exempted_doctypes(value: ast.AST, ctx: _Context) -> None:
    """``auto_cancel_exempted_doctypes = ["DocType", ...]``.

    Marks DocTypes that should NOT auto-cancel when their parent doc is
    cancelled.  We tag the existing DocType node via a metadata edge
    rather than mutating the doctype node directly (which the DocType
    extractor owns).
    """
    line = getattr(value, "lineno", 1)
    for doctype in _str_or_list(value):
        ctx.edges.append(make_edge(
            make_id("auto_cancel_exempted"),
            make_id(doctype),
            "auto_cancel_exempted",
            ctx.str_path, line,
            doctype=doctype,
        ))


def _handle_regional_overrides(value: ast.AST, ctx: _Context) -> None:
    """``regional_overrides = {country: {hook_path: replacement_path}}``.

    Stored as override edges with country metadata so triage can answer
    "is this customer's country swapping out a default handler?"
    """
    if not isinstance(value, ast.Dict):
        return

    for country_node, mapping_node in zip(value.keys, value.values):
        country = _str_const(country_node)
        if not country or not isinstance(mapping_node, ast.Dict):
            continue
        for orig_node, new_node in zip(mapping_node.keys, mapping_node.values):
            original = _str_const(orig_node)
            replacement = _str_const(new_node)
            if not original or not replacement:
                continue
            line = getattr(orig_node, "lineno", 1)
            new_nid = ctx.add_hook_node(replacement, line, role="regional_override")
            ctx.edges.append(make_edge(
                new_nid, make_id(original), "overrides",
                ctx.str_path, line,
                original=original, replacement=replacement,
                scope="regional", country=country,
            ))


def _handle_fixtures(value: ast.AST, ctx: _Context) -> None:
    """``fixtures = ["DocType Name" | {"dt": "...", "filters": [...]}]``.

    Records that this app exports the named DocType as a fixture during
    install/migrate.  We emit one ``exports_fixture`` edge per entry —
    the source is a synthetic ``fixtures`` node so the report can list
    every fixture for an app at a glance.
    """
    if not isinstance(value, (ast.List, ast.Tuple)):
        return

    fixtures_nid = make_id("fixtures")
    for elt in value.elts:
        line = getattr(elt, "lineno", 1)
        doctype: str | None = None
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            doctype = elt.value
        elif isinstance(elt, ast.Dict):
            for k, v in zip(elt.keys, elt.values):
                if _str_const(k) in ("dt", "doctype"):
                    doctype = _str_const(v)
                    break
        if not doctype:
            continue
        ctx.edges.append(make_edge(
            fixtures_nid, make_id(doctype), "exports_fixture",
            ctx.str_path, line,
            doctype=doctype,
        ))


# Dispatch table — extending support for new hooks is just one entry here.
_HOOK_HANDLERS = {
    "doc_events": _handle_doc_events,
    "scheduler_events": _handle_scheduler_events,
    "override_whitelisted_methods": _handle_override_whitelisted_methods,
    "extend_doctype_class": _handle_extend_doctype_class,
    "override_doctype_class": _handle_override_doctype_class,
    "override_doctype_dashboards": _handle_override_doctype_dashboards,
    "doctype_js": _handle_doctype_js,
    "doctype_list_js": _handle_doctype_js,
    "doctype_calendar_js": _handle_doctype_js,
    "doctype_tree_js": _handle_doctype_js,
    "permission_query_conditions": _handle_permission_hook,
    "has_permission": _handle_permission_hook,
    "has_website_permission": _handle_permission_hook,
    "jinja": _handle_jinja,
    "before_request": _handle_simple_callable_list("request_hook"),
    "after_request": _handle_simple_callable_list("request_hook"),
    "before_job": _handle_simple_callable_list("job_hook"),
    "after_job": _handle_simple_callable_list("job_hook"),
    "boot_session": _handle_simple_callable_list("boot_hook"),
    "extend_bootinfo": _handle_simple_callable_list("boot_hook"),
    "on_session_creation": _handle_simple_callable_list("session_hook"),
    "on_logout": _handle_simple_callable_list("session_hook"),
    "notification_config": _handle_simple_callable_list("notification_config"),
    "additional_print_settings": _handle_simple_callable_list("print_hook"),
    "app_include_js": _handle_static_asset_list("app_include_js"),
    "app_include_css": _handle_static_asset_list("app_include_css"),
    "web_include_js": _handle_static_asset_list("web_include_js"),
    "web_include_css": _handle_static_asset_list("web_include_css"),
    "auto_cancel_exempted_doctypes": _handle_auto_cancel_exempted_doctypes,
    "regional_overrides": _handle_regional_overrides,
    "fixtures": _handle_fixtures,
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
