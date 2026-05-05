"""JavaScript AST extraction via tree-sitter.

Extracts functions, arrow functions, classes, imports, and Frappe-specific
patterns from .js files:
- frappe.ui.form.on("DocType", {...}) — methods become DocType client nodes
- frappe.call({method: "dotted.path"}) — calls_api edges
- frappe.xcall("dotted.path") — calls_api edges
- erpnext.utils.map_current_doc({method: "..."}) — calls_api edges
- frappe.db.get_value / frappe.client.get_list — queries_doctype edges
- frappe.realtime.on("event", ...) — subscribes_to_event edges
- frappe.listview_settings["DT"] = {...} — extends_list_view edges
- cur_frm.cscript.xxx = "DocType" — references to DocType
- ES6 class declarations
"""

from __future__ import annotations

from pathlib import Path

from .graph_primitives import make_edge, make_id, make_node, read_node_text


# ── Frappe JS API surface ──────────────────────────────────────────────────

# Client-side ORM entry points whose first positional argument is a
# DocType name string.  These are the JS analogues of the Python
# ``frappe.db.*`` family handled in extract_python.py.
_JS_DB_METHODS: frozenset[str] = frozenset({
    "frappe.db.get_value",
    "frappe.db.get_list",
    "frappe.db.get_doc",
    "frappe.db.exists",
    "frappe.db.count",
    "frappe.db.set_value",
    "frappe.db.insert",
    "frappe.db.delete",
})

# ``frappe.client.*`` calls take an options object whose ``doctype``
# property names the target.  Same shape as ``frappe.call`` but the
# semantics is a structured query, not a method invocation.
_JS_CLIENT_METHODS: frozenset[str] = frozenset({
    "frappe.client.get_list",
    "frappe.client.get_count",
    "frappe.client.get_value",
    "frappe.client.get",
    "frappe.client.set_value",
    "frappe.client.insert",
    "frappe.client.delete",
})


# ── Tree-sitter setup ──────────────────────────────────────────────────────

def _get_parser():
    """Lazy-load tree-sitter JavaScript parser."""
    import tree_sitter_javascript as tsjs
    from tree_sitter import Language, Parser

    language = Language(tsjs.language())
    parser = Parser(language)
    return parser


# ── Frappe JS pattern detection ────────────────────────────────────────────

def _member_text(node, source: bytes) -> str:
    """Read full member expression text, e.g. 'frappe.ui.form.on'."""
    return read_node_text(node, source)


def _is_frappe_form_on(func_node, source: bytes) -> bool:
    """Check if a call_expression's function is 'frappe.ui.form.on'."""
    return _member_text(func_node, source) == "frappe.ui.form.on"


def _is_frappe_call(func_node, source: bytes) -> bool:
    """Check for frappe.call(...) or frappe.xcall(...)."""
    text = _member_text(func_node, source)
    return text in ("frappe.call", "frappe.xcall")


def _is_map_current_doc(func_node, source: bytes) -> bool:
    """Check for erpnext.utils.map_current_doc(...)."""
    return _member_text(func_node, source) == "erpnext.utils.map_current_doc"


def _extract_string_content(string_node, source: bytes) -> str | None:
    """Extract the text content from a JS string node (without quotes)."""
    for child in string_node.children:
        if child.type == "string_fragment":
            return read_node_text(child, source)
    # Fallback: strip quotes manually
    text = read_node_text(string_node, source)
    if len(text) >= 2:
        return text[1:-1]
    return None


def _find_method_property(obj_node, source: bytes) -> str | None:
    """Find the 'method' key inside an object literal {method: "..."}."""
    return _find_string_property(obj_node, source, "method")


def _find_string_property(obj_node, source: bytes, name: str) -> str | None:
    """Return the string value of ``name: "..."`` in an object literal.

    Mirrors ``_find_method_property`` but parameterised on the key.
    Used to extract ``doctype: "Customer"`` from ``frappe.client.*``
    options objects.
    """
    for child in obj_node.children:
        if child.type != "pair":
            continue
        key = child.child_by_field_name("key")
        value = child.child_by_field_name("value")
        if not key or not value:
            continue
        key_text = read_node_text(key, source)
        if key_text == name and value.type == "string":
            return _extract_string_content(value, source)
    return None


def _first_call_string_arg(call_node, source: bytes) -> str | None:
    """Return the first positional string-literal argument of a call.

    Skips object literals and other non-string positional arguments,
    so callers can rely on the result being a real string the user
    typed (not a reference we silently coerced).
    """
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type == "string":
            return _extract_string_content(child, source)
        if child.type in ("(", ")", ","):
            continue
        # First non-string positional ends the search.
        if child.type not in ("comment",):
            return None
    return None


def _first_call_object_arg(call_node, source: bytes):
    """Return the first object-literal argument node, or None."""
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.children:
        if child.type == "object":
            return child
    return None


# ── frappe.ui.form.on extraction ───────────────────────────────────────────

def _extract_form_on_methods(
    call_node,
    source: bytes,
    file_nid: str,
    stem: str,
    str_path: str,
) -> tuple[list[dict], list[dict]]:
    """Extract methods from frappe.ui.form.on("DocType", {setup: fn, ...}).

    Returns (nodes, edges) for the DocType binding and all handler methods.
    """
    nodes = []
    edges = []

    args = call_node.child_by_field_name("arguments")
    if not args:
        return nodes, edges

    # Find the DocType name (first string arg) and the handlers object
    doctype_name = None
    handlers_obj = None

    for child in args.children:
        if child.type == "string" and doctype_name is None:
            doctype_name = _extract_string_content(child, source)
        elif child.type == "object" and handlers_obj is None:
            handlers_obj = child

    if not doctype_name:
        return nodes, edges

    # Create extends_client edge from file → DocType
    doctype_nid = make_id(doctype_name)
    edges.append(make_edge(
        file_nid, doctype_nid, "extends_client",
        str_path, call_node.start_point[0] + 1,
        doctype=doctype_name,
    ))

    if not handlers_obj:
        return nodes, edges

    # Extract each method/handler from the object literal
    for child in handlers_obj.children:
        method_name = None
        body_node = None

        if child.type == "pair":
            # { setup: function(frm) { ... } }
            key = child.child_by_field_name("key")
            value = child.child_by_field_name("value")
            if key:
                method_name = read_node_text(key, source)
            if value and value.type in ("function_expression", "arrow_function"):
                body_node = value.child_by_field_name("body")

        elif child.type == "method_definition":
            # { refresh(frm) { ... } }
            name_node = child.child_by_field_name("name")
            if name_node:
                method_name = read_node_text(name_node, source)
            body_node = child.child_by_field_name("body")

        if method_name:
            method_nid = make_id(stem, doctype_name, method_name)
            line_start = child.start_point[0] + 1
            line_end = child.end_point[0] + 1

            nodes.append(make_node(
                method_nid,
                f".{method_name}()",
                "code",
                str_path,
                line_start,
                line_end,
                doctype=doctype_name,
            ))
            edges.append(make_edge(
                doctype_nid, method_nid, "method",
                str_path, line_start,
            ))

    return nodes, edges


# ── Import extraction ──────────────────────────────────────────────────────

def _extract_js_import(node, source: bytes, file_nid: str, str_path: str) -> list[dict]:
    """Extract edges from an import statement."""
    edges = []
    for child in node.children:
        if child.type == "string":
            raw = _extract_string_content(child, source)
            if not raw:
                break
            tgt_nid = make_id(raw)
            edges.append(make_edge(
                file_nid, tgt_nid, "imports_from",
                str_path, node.start_point[0] + 1,
            ))
            break
    return edges


# ── cur_frm.cscript detection ─────────────────────────────────────────────

def _extract_cscript_assignment(node, source: bytes, file_nid: str, str_path: str) -> list[dict]:
    """Detect cur_frm.cscript.xxx = "DocType Name" patterns.

    Example: cur_frm.cscript.tax_table = "Sales Taxes and Charges";
    """
    edges = []
    if node.type != "expression_statement":
        return edges

    for child in node.children:
        if child.type == "assignment_expression":
            left = child.child_by_field_name("left")
            right = child.child_by_field_name("right")

            if left and right and right.type == "string":
                left_text = read_node_text(left, source)
                if left_text.startswith("cur_frm.cscript."):
                    doctype_name = _extract_string_content(right, source)
                    if doctype_name:
                        edges.append(make_edge(
                            file_nid,
                            make_id(doctype_name),
                            "references_doctype",
                            str_path,
                            child.start_point[0] + 1,
                            confidence="INFERRED",
                            doctype=doctype_name,
                        ))
    return edges


# ── Main extraction ────────────────────────────────────────────────────────

def extract_js(path: Path) -> dict:
    """Extract all entities from a JavaScript file.

    Returns a dict with:
    - nodes: list of graph nodes (file, classes, functions, methods)
    - edges: list of graph edges (contains, calls_api, extends_client, etc.)
    - raw_calls: unresolved calls for cross-file resolution
    """
    parser = _get_parser()

    try:
        source = path.read_bytes()
    except OSError as e:
        return {"error": str(e), "nodes": [], "edges": [], "raw_calls": []}

    tree = parser.parse(source)
    root = tree.root_node
    stem = path.stem
    str_path = str(path)

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()
    function_bodies: list[tuple[str, object]] = []

    # File node
    file_nid = make_id(str_path)
    nodes.append(make_node(
        file_nid, path.name, "file", str_path,
        1, root.end_point[0] + 1,
    ))
    seen_ids.add(file_nid)

    def walk(node, parent_nid: str | None = None) -> None:
        t = node.type

        # Imports
        if t == "import_statement":
            edges.extend(_extract_js_import(node, source, file_nid, str_path))
            return

        # cur_frm.cscript assignments
        if t == "expression_statement":
            cscript_edges = _extract_cscript_assignment(node, source, file_nid, str_path)
            if cscript_edges:
                edges.extend(cscript_edges)
                return

            # Check if this is a frappe.ui.form.on() call
            for child in node.children:
                if child.type == "call_expression":
                    func = child.child_by_field_name("function")
                    if func and _is_frappe_form_on(func, source):
                        form_nodes, form_edges = _extract_form_on_methods(
                            child, source, file_nid, stem, str_path,
                        )
                        nodes.extend(form_nodes)
                        edges.extend(form_edges)

                        # Collect method bodies for call-graph pass
                        args = child.child_by_field_name("arguments")
                        if args:
                            for arg in args.children:
                                if arg.type == "object":
                                    _collect_method_bodies(
                                        arg, source, stem, str_path,
                                        function_bodies, seen_ids,
                                    )
                        return

        # ES6 class declarations
        if t == "class_declaration":
            name_node = child_by_field_safe(node, "name")
            if name_node:
                class_name = read_node_text(name_node, source)
                class_nid = make_id(stem, class_name)
                line_start = node.start_point[0] + 1
                line_end = node.end_point[0] + 1

                if class_nid not in seen_ids:
                    nodes.append(make_node(
                        class_nid, class_name, "code", str_path,
                        line_start, line_end,
                    ))
                    seen_ids.add(class_nid)
                edges.append(make_edge(
                    file_nid, class_nid, "contains", str_path, line_start,
                ))

                # Inheritance — look for class_heritage child
                for child in node.children:
                    if child.type == "class_heritage":
                        for sub in child.children:
                            if sub.type == "identifier":
                                base = read_node_text(sub, source)
                                base_nid = make_id(base)
                                if base_nid not in seen_ids:
                                    nodes.append(make_node(
                                        base_nid, base, "code", "", 0, 0,
                                    ))
                                    seen_ids.add(base_nid)
                                edges.append(make_edge(
                                    class_nid, base_nid, "inherits",
                                    str_path, line_start,
                                ))

                # Walk class body
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        if child.type == "method_definition":
                            _extract_method(
                                child, source, class_nid, stem,
                                str_path, nodes, edges, seen_ids,
                                function_bodies,
                            )
                return

        # Top-level function declarations
        if t == "function_declaration":
            name_node = child_by_field_safe(node, "name")
            if name_node:
                func_name = read_node_text(name_node, source)
                func_nid = make_id(stem, func_name)
                line_start = node.start_point[0] + 1
                line_end = node.end_point[0] + 1

                if func_nid not in seen_ids:
                    nodes.append(make_node(
                        func_nid, f"{func_name}()", "code", str_path,
                        line_start, line_end,
                    ))
                    seen_ids.add(func_nid)
                edges.append(make_edge(
                    file_nid, func_nid, "contains", str_path, line_start,
                ))

                body = node.child_by_field_name("body")
                if body:
                    function_bodies.append((func_nid, body))
            return

        # Arrow functions assigned to variables: const foo = () => { ... }
        if t == "lexical_declaration":
            for child in node.children:
                if child.type == "variable_declarator":
                    value = child.child_by_field_name("value")
                    if value and value.type == "arrow_function":
                        name_node = child.child_by_field_name("name")
                        if name_node:
                            func_name = read_node_text(name_node, source)
                            func_nid = make_id(stem, func_name)
                            line_start = child.start_point[0] + 1
                            line_end = child.end_point[0] + 1

                            if func_nid not in seen_ids:
                                nodes.append(make_node(
                                    func_nid, f"{func_name}()", "code",
                                    str_path, line_start, line_end,
                                ))
                                seen_ids.add(func_nid)
                            edges.append(make_edge(
                                file_nid, func_nid, "contains",
                                str_path, line_start,
                            ))

                            body = value.child_by_field_name("body")
                            if body:
                                function_bodies.append((func_nid, body))
            return

        # Default: recurse
        for child in node.children:
            walk(child, parent_nid)

    walk(root)

    # ── Call-graph pass ────────────────────────────────────────────────────
    seen_call_pairs: set[tuple[str, str]] = set()

    label_to_nid: dict[str, str] = {}
    for n in nodes:
        raw = n["label"]
        normalised = raw.strip("()").lstrip(".")
        label_to_nid[normalised.lower()] = n["id"]

    raw_calls: list[dict] = []

    def walk_calls(node, caller_nid: str) -> None:
        # Don't descend into nested function boundaries
        if node.type in ("function_declaration", "arrow_function",
                         "function_expression", "method_definition"):
            return

        if node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func:
                func_text = read_node_text(func, source)

                # frappe.call / frappe.xcall → calls_api edge
                if func_text in ("frappe.call", "frappe.xcall"):
                    api_path = _extract_api_method(node, func_text, source)
                    if api_path:
                        edges.append(make_edge(
                            caller_nid,
                            make_id(api_path),
                            "calls_api",
                            str_path,
                            node.start_point[0] + 1,
                            confidence="INFERRED",
                            api_path=api_path,
                        ))

                # erpnext.utils.map_current_doc → calls_api edge
                elif func_text == "erpnext.utils.map_current_doc":
                    api_path = _extract_api_method(node, func_text, source)
                    if api_path:
                        edges.append(make_edge(
                            caller_nid,
                            make_id(api_path),
                            "calls_api",
                            str_path,
                            node.start_point[0] + 1,
                            confidence="INFERRED",
                            api_path=api_path,
                        ))

                # frappe.db.* — first positional string is the DocType.
                elif func_text in _JS_DB_METHODS:
                    doctype = _first_call_string_arg(node, source)
                    if doctype:
                        edges.append(make_edge(
                            caller_nid,
                            make_id(doctype),
                            "queries_doctype",
                            str_path,
                            node.start_point[0] + 1,
                            confidence="INFERRED",
                            doctype=doctype,
                            via=func_text,
                        ))

                # frappe.client.* — DocType lives inside an options object.
                elif func_text in _JS_CLIENT_METHODS:
                    obj = _first_call_object_arg(node, source)
                    doctype = (
                        _find_string_property(obj, source, "doctype")
                        if obj is not None else None
                    )
                    if doctype:
                        edges.append(make_edge(
                            caller_nid,
                            make_id(doctype),
                            "queries_doctype",
                            str_path,
                            node.start_point[0] + 1,
                            confidence="INFERRED",
                            doctype=doctype,
                            via=func_text,
                        ))

                # Regular function calls — try intra-file resolution
                else:
                    callee_name = None
                    if func.type == "identifier":
                        callee_name = func_text
                    elif func.type == "member_expression":
                        prop = func.child_by_field_name("property")
                        if prop:
                            callee_name = read_node_text(prop, source)

                    if callee_name:
                        tgt_nid = label_to_nid.get(callee_name.lower())
                        if tgt_nid and tgt_nid != caller_nid:
                            pair = (caller_nid, tgt_nid)
                            if pair not in seen_call_pairs:
                                seen_call_pairs.add(pair)
                                edges.append(make_edge(
                                    caller_nid, tgt_nid, "calls",
                                    str_path, node.start_point[0] + 1,
                                ))
                        elif not tgt_nid:
                            raw_calls.append({
                                "caller_nid": caller_nid,
                                "callee": callee_name,
                                "source_file": str_path,
                                "line": node.start_point[0] + 1,
                            })

        for child in node.children:
            walk_calls(child, caller_nid)

    for caller_nid, body_node in function_bodies:
        walk_calls(body_node, caller_nid)

    return {"nodes": nodes, "edges": edges, "raw_calls": raw_calls}


# ── Internal helpers ───────────────────────────────────────────────────────

def child_by_field_safe(node, field: str):
    """Safe wrapper — returns None if field doesn't exist."""
    try:
        return node.child_by_field_name(field)
    except Exception:
        return None


def _extract_method(
    node,
    source: bytes,
    parent_nid: str,
    stem: str,
    str_path: str,
    nodes: list,
    edges: list,
    seen_ids: set,
    function_bodies: list,
) -> None:
    """Extract a method_definition from a class body."""
    name_node = child_by_field_safe(node, "name")
    if not name_node:
        return

    method_name = read_node_text(name_node, source)
    method_nid = make_id(parent_nid, method_name)
    line_start = node.start_point[0] + 1
    line_end = node.end_point[0] + 1

    if method_nid not in seen_ids:
        nodes.append(make_node(
            method_nid, f".{method_name}()", "code", str_path,
            line_start, line_end,
        ))
        seen_ids.add(method_nid)

    edges.append(make_edge(
        parent_nid, method_nid, "method", str_path, line_start,
    ))

    body = node.child_by_field_name("body")
    if body:
        function_bodies.append((method_nid, body))


def _collect_method_bodies(
    obj_node,
    source: bytes,
    stem: str,
    str_path: str,
    function_bodies: list,
    seen_ids: set,
) -> None:
    """Collect function bodies from a frappe.ui.form.on handler object.

    We need the bodies for the call-graph pass so we can find
    frappe.call() patterns inside form handlers.
    """
    # Find the doctype name from the call's first argument
    # (already parsed — we just need to match nids for the bodies)
    parent_call = obj_node.parent  # arguments node
    if parent_call:
        parent_call = parent_call.parent  # call_expression

    doctype_name = None
    if parent_call and parent_call.type == "call_expression":
        args = parent_call.child_by_field_name("arguments")
        if args:
            for child in args.children:
                if child.type == "string":
                    doctype_name = _extract_string_content(child, source)
                    break

    if not doctype_name:
        return

    for child in obj_node.children:
        method_name = None
        body_node = None

        if child.type == "pair":
            key = child.child_by_field_name("key")
            value = child.child_by_field_name("value")
            if key:
                method_name = read_node_text(key, source)
            if value and value.type in ("function_expression", "arrow_function"):
                body_node = value.child_by_field_name("body")

        elif child.type == "method_definition":
            name_node = child.child_by_field_name("name")
            if name_node:
                method_name = read_node_text(name_node, source)
            body_node = child.child_by_field_name("body")

        if method_name and body_node:
            method_nid = make_id(stem, doctype_name, method_name)
            if method_nid in seen_ids or True:  # Always collect bodies
                function_bodies.append((method_nid, body_node))


def _extract_api_method(
    call_node,
    func_text: str,
    source: bytes,
) -> str | None:
    """Extract the 'method' property from frappe.call({method: "..."}) or
    the first argument from frappe.xcall("dotted.path", ...).
    """
    args = call_node.child_by_field_name("arguments")
    if not args:
        return None

    if func_text == "frappe.xcall":
        # frappe.xcall("dotted.path", ...) — first arg is the method
        for child in args.children:
            if child.type == "string":
                return _extract_string_content(child, source)
        return None

    # frappe.call({method: "..."}) or erpnext.utils.map_current_doc({method: "..."})
    for child in args.children:
        if child.type == "object":
            return _find_method_property(child, source)

    return None
