"""Python AST extraction via tree-sitter.

Extracts classes, functions, methods, imports, inheritance, call graph,
Frappe ORM calls, @frappe.whitelist() tagging, and rationale comments
from .py files.

The extraction is split into two passes:
1. Structure pass: walk the AST top-down to find classes, functions,
   imports, and inheritance. Collect function bodies for pass 2.
2. Call-graph pass: walk each function body to find calls (both
   intra-file and cross-file) and Frappe ORM calls.
"""

from __future__ import annotations

import re
from pathlib import Path

from .graph_primitives import make_edge, make_id, make_node, read_node_text


# ── Tree-sitter setup ──────────────────────────────────────────────────────

def _get_parser():
    """Lazy-load tree-sitter Python parser."""
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser

    language = Language(tspython.language())
    parser = Parser(language)
    return parser


# ── Decorator detection ─────────────────────────────────────────────────────

def _has_frappe_whitelist(node, source: bytes) -> bool:
    """Check if a decorated_definition or function_definition has @frappe.whitelist().

    Handles both:
    - @frappe.whitelist()
    - @frappe.whitelist(allow_guest=True)
    """
    # If the node is inside a decorated_definition, check the parent
    parent = node.parent
    if parent and parent.type == "decorated_definition":
        node = parent

    if node.type != "decorated_definition":
        return False

    for child in node.children:
        if child.type == "decorator":
            # The decorator body is after the @ sign
            for sub in child.children:
                if sub.type == "call":
                    func = sub.child_by_field_name("function")
                    if func and func.type == "attribute":
                        text = read_node_text(func, source)
                        if text == "frappe.whitelist":
                            return True
    return False


# ── Import extraction ───────────────────────────────────────────────────────

def _extract_imports(node, source: bytes, file_nid: str, str_path: str) -> list[dict]:
    """Extract import and import-from edges from a single import node."""
    edges = []
    t = node.type

    if t == "import_statement":
        for child in node.children:
            if child.type in ("dotted_name", "aliased_import"):
                raw = read_node_text(child, source)
                module_name = raw.split(" as ")[0].strip().lstrip(".")
                if module_name:
                    tgt_nid = make_id(module_name)
                    edges.append(make_edge(
                        file_nid, tgt_nid, "imports",
                        str_path, node.start_point[0] + 1,
                    ))

    elif t == "import_from_statement":
        module_node = node.child_by_field_name("module_name")
        if module_node:
            raw = read_node_text(module_node, source)
            module_name = raw.lstrip(".")
            if module_name:
                tgt_nid = make_id(module_name)
                edges.append(make_edge(
                    file_nid, tgt_nid, "imports_from",
                    str_path, node.start_point[0] + 1,
                ))

    return edges


# ── Frappe ORM call detection ──────────────────────────────────────────────

# Patterns: frappe.get_doc("DocType", ...), frappe.get_all("DocType"), etc.
_FRAPPE_ORM_METHODS = frozenset({
    "frappe.get_doc", "frappe.get_all", "frappe.get_list",
    "frappe.get_value", "frappe.get_cached_doc", "frappe.get_last_doc",
    "frappe.new_doc",
    "frappe.db.get_value", "frappe.db.get_all", "frappe.db.get_list",
    "frappe.db.exists", "frappe.db.count", "frappe.db.delete",
    "frappe.qb.DocType",
})

# Pattern for `tabDocTypeName` in SQL strings
_TAB_PATTERN = re.compile(r"`tab([A-Z][A-Za-z0-9 ]+)`")


def _extract_frappe_orm_call(
    node,
    source: bytes,
    caller_nid: str,
    str_path: str,
) -> list[dict]:
    """If this call node is a Frappe ORM call, return queries_doctype edges."""
    edges = []

    func_node = node.child_by_field_name("function")
    if not func_node:
        return edges

    func_text = read_node_text(func_node, source)

    # Check if it's a known Frappe ORM method
    if func_text in _FRAPPE_ORM_METHODS:
        args = node.child_by_field_name("arguments")
        if args:
            # First string argument is the DocType name
            doctype_name = _first_string_arg(args, source)
            if doctype_name:
                edges.append(make_edge(
                    caller_nid,
                    make_id(doctype_name),
                    "queries_doctype",
                    str_path,
                    node.start_point[0] + 1,
                    confidence="INFERRED",
                    doctype=doctype_name,
                ))

    # frappe.db.sql(...) — scan for `tabDocType` patterns
    if func_text == "frappe.db.sql":
        args = node.child_by_field_name("arguments")
        if args:
            sql_text = _first_string_arg(args, source)
            if sql_text:
                for match in _TAB_PATTERN.finditer(sql_text):
                    doctype_name = match.group(1)
                    edges.append(make_edge(
                        caller_nid,
                        make_id(doctype_name),
                        "queries_doctype",
                        str_path,
                        node.start_point[0] + 1,
                        confidence="INFERRED",
                        doctype=doctype_name,
                    ))

    return edges


def _first_string_arg(args_node, source: bytes) -> str | None:
    """Extract the value of the first string literal argument."""
    for child in args_node.children:
        if child.type == "string":
            return _string_content(child, source)
        # Could be inside keyword_argument or positional
        if child.type == "argument" or child.type == "keyword_argument":
            continue
        # Skip commas and parens
    return None


def _string_content(string_node, source: bytes) -> str | None:
    """Extract the text content of a string node (without quotes)."""
    for child in string_node.children:
        if child.type == "string_content":
            return read_node_text(child, source)
    # Fallback: strip quotes manually
    text = read_node_text(string_node, source)
    if len(text) >= 2:
        return text[1:-1]
    return None


# ── Rationale extraction ───────────────────────────────────────────────────

_RATIONALE_PREFIXES = (
    "# NOTE:", "# IMPORTANT:", "# HACK:", "# WHY:",
    "# RATIONALE:", "# TODO:", "# FIXME:",
)


def _extract_rationale_comments(
    source_text: str,
    file_nid: str,
    stem: str,
    str_path: str,
) -> tuple[list[dict], list[dict]]:
    """Extract rationale comments (# NOTE:, # HACK:, etc.) from source."""
    nodes = []
    edges = []

    for lineno, line in enumerate(source_text.splitlines(), start=1):
        stripped = line.strip()
        if any(stripped.startswith(p) for p in _RATIONALE_PREFIXES):
            label = stripped[:80].replace("\n", " ").strip()
            rid = make_id(stem, "rationale", str(lineno))
            nodes.append(make_node(
                rid, label, "rationale", str_path, lineno, lineno,
            ))
            edges.append(make_edge(
                rid, file_nid, "rationale_for", str_path, lineno,
            ))

    return nodes, edges


def _extract_docstring(
    body_node,
    source: bytes,
    parent_nid: str,
    stem: str,
    str_path: str,
) -> tuple[list[dict], list[dict]]:
    """Extract docstring from the first statement of a class/function body."""
    nodes = []
    edges = []

    if not body_node:
        return nodes, edges

    for child in body_node.children:
        if child.type == "expression_statement":
            for sub in child.children:
                if sub.type in ("string", "concatenated_string"):
                    text = read_node_text(sub, source)
                    text = text.strip("\"'").strip('"""').strip("'''").strip()
                    if len(text) > 20:  # Skip trivially short docstrings
                        label = text[:80].replace("\n", " ").strip()
                        line = child.start_point[0] + 1
                        rid = make_id(stem, "docstring", str(line))
                        nodes.append(make_node(
                            rid, label, "rationale", str_path, line,
                            child.end_point[0] + 1,
                        ))
                        edges.append(make_edge(
                            rid, parent_nid, "rationale_for", str_path, line,
                        ))
                        return nodes, edges
        break  # Only check the very first statement

    return nodes, edges


# ── Main extraction ────────────────────────────────────────────────────────

def extract_python(path: Path) -> dict:
    """Extract all code entities from a Python file.

    Returns a dict with:
    - nodes: list of graph nodes (classes, functions, file, rationale)
    - edges: list of graph edges (contains, inherits, calls, imports, etc.)
    - raw_calls: unresolved cross-file calls for Phase 2 resolution
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

    # Function bodies collected in pass 1, walked in pass 2
    function_bodies: list[tuple[str, object]] = []

    # ── File node ──────────────────────────────────────────────────────────
    file_nid = make_id(str_path)
    nodes.append(make_node(
        file_nid, path.name, "file", str_path,
        1, root.end_point[0] + 1,
    ))
    seen_ids.add(file_nid)

    # ── Pass 1: Structure walk ─────────────────────────────────────────────

    def walk(node, parent_class_nid: str | None = None) -> None:
        t = node.type

        # Imports
        if t in ("import_statement", "import_from_statement"):
            edges.extend(_extract_imports(node, source, file_nid, str_path))
            return

        # Decorated definitions — unwrap to find the actual class/function
        if t == "decorated_definition":
            for child in node.children:
                if child.type in ("class_definition", "function_definition"):
                    walk(child, parent_class_nid)
            return

        # Classes
        if t == "class_definition":
            name_node = node.child_by_field_name("name")
            if not name_node:
                return

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

            # Inheritance — extract superclass names
            superclasses = node.child_by_field_name("superclasses")
            if superclasses:
                for arg in superclasses.children:
                    if arg.type == "identifier":
                        base_name = read_node_text(arg, source)
                        base_nid = make_id(base_name)
                        if base_nid not in seen_ids:
                            # Placeholder node — resolved cross-file later
                            nodes.append(make_node(
                                base_nid, base_name, "code", "",
                                0, 0,
                            ))
                            seen_ids.add(base_nid)
                        edges.append(make_edge(
                            class_nid, base_nid, "inherits",
                            str_path, line_start,
                        ))
                    elif arg.type == "attribute":
                        # e.g. frappe.model.document.Document
                        base_name = read_node_text(arg, source)
                        base_nid = make_id(base_name)
                        if base_nid not in seen_ids:
                            nodes.append(make_node(
                                base_nid, base_name, "code", "",
                                0, 0,
                            ))
                            seen_ids.add(base_nid)
                        edges.append(make_edge(
                            class_nid, base_nid, "inherits",
                            str_path, line_start,
                        ))

            # Docstring
            body = node.child_by_field_name("body")
            ds_nodes, ds_edges = _extract_docstring(
                body, source, class_nid, stem, str_path,
            )
            nodes.extend(ds_nodes)
            edges.extend(ds_edges)

            # Recurse into class body
            if body:
                for child in body.children:
                    walk(child, parent_class_nid=class_nid)
            return

        # Functions / methods
        if t == "function_definition":
            name_node = node.child_by_field_name("name")
            if not name_node:
                return

            func_name = read_node_text(name_node, source)
            line_start = node.start_point[0] + 1
            line_end = node.end_point[0] + 1

            if parent_class_nid:
                func_nid = make_id(parent_class_nid, func_name)
                label = f".{func_name}()"
                relation = "method"
                edge_source = parent_class_nid
            else:
                func_nid = make_id(stem, func_name)
                label = f"{func_name}()"
                relation = "contains"
                edge_source = file_nid

            # Check for @frappe.whitelist() — tag as API node
            is_api = _has_frappe_whitelist(node, source)
            node_type = "api" if is_api else "code"

            if func_nid not in seen_ids:
                extra = {}
                if is_api:
                    # Build a stable dotted API path for triage matching
                    # e.g. erpnext.selling.doctype.sales_order.sales_order.make_invoice
                    parts = Path(str_path).with_suffix("").parts
                    extra["api_path"] = ".".join(parts) + "." + func_name

                nodes.append(make_node(
                    func_nid, label, node_type, str_path,
                    line_start, line_end,
                    **extra,
                ))
                seen_ids.add(func_nid)

            edges.append(make_edge(
                edge_source, func_nid, relation, str_path, line_start,
            ))

            # Docstring
            body = node.child_by_field_name("body")
            ds_nodes, ds_edges = _extract_docstring(
                body, source, func_nid, stem, str_path,
            )
            nodes.extend(ds_nodes)
            edges.extend(ds_edges)

            # Collect body for pass 2
            if body:
                function_bodies.append((func_nid, body))
            return

        # Default: recurse into children
        for child in node.children:
            walk(child, parent_class_nid=None)

    walk(root)

    # ── Pass 2: Call-graph walk ─────────────────────────────────────────────

    # Build a label→nid index for intra-file call resolution
    label_to_nid: dict[str, str] = {}
    for n in nodes:
        raw = n["label"]
        normalised = raw.strip("()").lstrip(".")
        label_to_nid[normalised.lower()] = n["id"]

    seen_call_pairs: set[tuple[str, str]] = set()
    raw_calls: list[dict] = []

    def walk_calls(node, caller_nid: str) -> None:
        # Don't descend into nested function definitions
        if node.type == "function_definition":
            return

        if node.type == "call":
            # ── Frappe ORM calls ──
            orm_edges = _extract_frappe_orm_call(
                node, source, caller_nid, str_path,
            )
            edges.extend(orm_edges)

            # ── Regular function calls ──
            func_node = node.child_by_field_name("function")
            callee_name: str | None = None

            if func_node:
                if func_node.type == "identifier":
                    callee_name = read_node_text(func_node, source)
                elif func_node.type == "attribute":
                    # self.validate_customer() → extract "validate_customer"
                    attr = func_node.child_by_field_name("attribute")
                    if attr:
                        callee_name = read_node_text(attr, source)

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
                    # Unresolved — save for cross-file resolution
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

    # ── Rationale comments ─────────────────────────────────────────────────
    source_text = source.decode("utf-8", errors="replace")
    rat_nodes, rat_edges = _extract_rationale_comments(
        source_text, file_nid, stem, str_path,
    )
    nodes.extend(rat_nodes)
    edges.extend(rat_edges)

    # ── Module-level docstring ─────────────────────────────────────────────
    ds_nodes, ds_edges = _extract_docstring(
        root, source, file_nid, stem, str_path,
    )
    nodes.extend(ds_nodes)
    edges.extend(ds_edges)

    return {"nodes": nodes, "edges": edges, "raw_calls": raw_calls}
