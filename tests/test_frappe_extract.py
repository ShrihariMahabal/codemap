"""Tests for codemap.frappe_extract — Frappe metadata extractors."""
from pathlib import Path

import pytest

from codemap.frappe_extract import (
    extract_dashboard,
    extract_doctype,
    extract_hooks,
    extract_modules,
    extract_record,
)
from codemap.graph_primitives import make_id

FIXTURE = Path(__file__).parent / "fixtures" / "sample_app"
DOCTYPE_JSON = FIXTURE / "test_app/selling/doctype/sales_order/sales_order.json"
HOOKS_PY = FIXTURE / "test_app/hooks.py"
DASHBOARD_PY = FIXTURE / "test_app/selling/doctype/sales_order/sales_order_dashboard.py"
MODULES_TXT = FIXTURE / "test_app/modules.txt"
REPORT_JSON = FIXTURE / "test_app/selling/report/sales_analytics/sales_analytics.json"


# ── DocType JSON ────────────────────────────────────────────────────────────

class TestDocType:
    def test_doctype_node_created(self):
        result = extract_doctype(DOCTYPE_JSON)
        nodes = [n for n in result["nodes"] if n["file_type"] == "doctype"]
        assert len(nodes) == 1
        assert nodes[0]["label"] == "Sales Order"
        assert nodes[0]["id"] == make_id("Sales Order")

    def test_doctype_has_source_lines(self):
        result = extract_doctype(DOCTYPE_JSON)
        node = result["nodes"][0]
        assert node["source_line_start"] == 1
        assert node["source_line_end"] >= 1

    def test_links_to_edge_for_link_field(self):
        result = extract_doctype(DOCTYPE_JSON)
        link_edges = [e for e in result["edges"] if e["relation"] == "links_to"]
        assert any(e["target"] == make_id("Customer") for e in link_edges)
        # fieldname carried as edge metadata
        customer_edge = next(
            e for e in link_edges if e["target"] == make_id("Customer")
        )
        assert customer_edge["fieldname"] == "customer"

    def test_child_of_edge_for_table_field(self):
        result = extract_doctype(DOCTYPE_JSON)
        child_edges = [e for e in result["edges"] if e["relation"] == "child_of"]
        assert any(e["target"] == make_id("Sales Order Item") for e in child_edges)

    def test_dynamic_link_edge(self, tmp_path):
        """Dynamic Link fields produce a dynamic_link_to edge to the field
        that holds the runtime DocType."""
        p = tmp_path / "comment.json"
        p.write_text("""{
            "doctype": "DocType",
            "name": "Comment",
            "fields": [
                {"fieldname": "reference_doctype", "fieldtype": "Link", "options": "DocType"},
                {"fieldname": "reference_name", "fieldtype": "Dynamic Link", "options": "reference_doctype"}
            ]
        }""")
        result = extract_doctype(p)
        dyn_edges = [e for e in result["edges"] if e["relation"] == "dynamic_link_to"]
        assert len(dyn_edges) == 1
        assert dyn_edges[0]["target"] == make_id("reference_doctype")

    def test_table_multiselect_treated_as_child_of(self, tmp_path):
        p = tmp_path / "user.json"
        p.write_text("""{
            "doctype": "DocType",
            "name": "User",
            "fields": [
                {"fieldname": "roles", "fieldtype": "Table MultiSelect", "options": "Has Role"}
            ]
        }""")
        result = extract_doctype(p)
        child = [e for e in result["edges"] if e["relation"] == "child_of"]
        assert len(child) == 1
        assert child[0]["target"] == make_id("Has Role")

    def test_module_edge_when_module_set(self, tmp_path):
        p = tmp_path / "x.json"
        p.write_text('{"doctype": "DocType", "name": "X", "module": "Selling", "fields": []}')
        result = extract_doctype(p)
        mod_edges = [e for e in result["edges"] if e["relation"] == "belongs_to_module"]
        assert len(mod_edges) == 1
        assert mod_edges[0]["target"] == make_id("Selling")

    def test_no_module_edge_when_module_missing(self):
        # The fixture sales_order.json has no "module" key
        result = extract_doctype(DOCTYPE_JSON)
        mod_edges = [e for e in result["edges"] if e["relation"] == "belongs_to_module"]
        assert mod_edges == []

    def test_duplicate_link_fields_emit_one_edge(self, tmp_path):
        """Two Link fields pointing at the same DocType produce one edge."""
        p = tmp_path / "x.json"
        p.write_text("""{
            "doctype": "DocType",
            "name": "X",
            "fields": [
                {"fieldname": "a", "fieldtype": "Link", "options": "Customer"},
                {"fieldname": "b", "fieldtype": "Link", "options": "Customer"}
            ]
        }""")
        result = extract_doctype(p)
        cust_edges = [e for e in result["edges"] if e["target"] == make_id("Customer")]
        assert len(cust_edges) == 1

    def test_non_doctype_json_returns_empty(self, tmp_path):
        p = tmp_path / "report.json"
        p.write_text('{"doctype": "Report", "name": "X"}')
        assert extract_doctype(p) == {"nodes": [], "edges": []}

    def test_malformed_json_returns_empty(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json {{{")
        assert extract_doctype(p) == {"nodes": [], "edges": []}

    def test_missing_file_returns_empty(self, tmp_path):
        assert extract_doctype(tmp_path / "nope.json") == {"nodes": [], "edges": []}


# ── Modules ─────────────────────────────────────────────────────────────────

class TestModules:
    def test_module_nodes(self):
        result = extract_modules(MODULES_TXT)
        labels = {n["label"] for n in result["nodes"]}
        assert "Selling" in labels
        assert "Buying" in labels

    def test_node_metadata(self):
        result = extract_modules(MODULES_TXT)
        for node in result["nodes"]:
            assert node["file_type"] == "module"
            assert node["source_line_start"] == node["source_line_end"]

    def test_modules_extractor_emits_no_edges(self):
        result = extract_modules(MODULES_TXT)
        assert result["edges"] == []

    def test_blank_lines_and_comments_ignored(self, tmp_path):
        p = tmp_path / "modules.txt"
        p.write_text("Selling\n\n# A comment\nBuying\n")
        result = extract_modules(p)
        assert len(result["nodes"]) == 2

    def test_duplicate_modules_dedup(self, tmp_path):
        p = tmp_path / "modules.txt"
        p.write_text("Selling\nSelling\n")
        result = extract_modules(p)
        assert len(result["nodes"]) == 1


# ── Records ─────────────────────────────────────────────────────────────────

class TestRecord:
    def test_report_node_created(self):
        result = extract_record(REPORT_JSON)
        nodes = [n for n in result["nodes"] if n["file_type"] == "record"]
        assert len(nodes) == 1
        assert nodes[0]["label"] == "Sales Analytics"
        assert nodes[0]["record_kind"] == "Report"

    def test_record_of_edge(self):
        result = extract_record(REPORT_JSON)
        edges = [e for e in result["edges"] if e["relation"] == "record_of"]
        assert len(edges) == 1
        assert edges[0]["target"] == make_id("Report")

    def test_references_doctype_edge(self):
        result = extract_record(REPORT_JSON)
        edges = [e for e in result["edges"] if e["relation"] == "references_doctype"]
        assert len(edges) == 1
        assert edges[0]["target"] == make_id("Sales Order")
        assert edges[0]["via"] == "ref_doctype"

    def test_doctype_json_skipped(self):
        # Passing a DocType JSON to the record extractor returns nothing
        assert extract_record(DOCTYPE_JSON) == {"nodes": [], "edges": []}

    def test_workspace_with_document_type(self, tmp_path):
        p = tmp_path / "ws.json"
        p.write_text("""{
            "doctype": "Workspace",
            "name": "Selling",
            "document_type": "Sales Order"
        }""")
        result = extract_record(p)
        edges = [e for e in result["edges"] if e["relation"] == "references_doctype"]
        assert len(edges) == 1
        assert edges[0]["via"] == "document_type"

    def test_record_id_namespaced_by_kind(self, tmp_path):
        """A Report named X and a Print Format named X must not collide."""
        p_report = tmp_path / "report.json"
        p_report.write_text('{"doctype": "Report", "name": "X"}')
        p_print = tmp_path / "print.json"
        p_print.write_text('{"doctype": "Print Format", "name": "X"}')

        rep = extract_record(p_report)
        prn = extract_record(p_print)
        assert rep["nodes"][0]["id"] != prn["nodes"][0]["id"]


# ── Dashboards ──────────────────────────────────────────────────────────────

class TestDashboard:
    def test_internal_links_emit_dashboard_link(self):
        result = extract_dashboard(DASHBOARD_PY)
        targets = {e["target"] for e in result["edges"]}
        assert make_id("Customer") in targets

    def test_transactions_emit_dashboard_link(self):
        result = extract_dashboard(DASHBOARD_PY)
        targets = {e["target"] for e in result["edges"]}
        assert make_id("Delivery Note") in targets
        assert make_id("Sales Invoice") in targets

    def test_all_edges_have_dashboard_link_relation(self):
        result = extract_dashboard(DASHBOARD_PY)
        for edge in result["edges"]:
            assert edge["relation"] == "dashboard_link"

    def test_source_is_parent_directory_doctype(self):
        result = extract_dashboard(DASHBOARD_PY)
        for edge in result["edges"]:
            # source DocType derived from .../sales_order/sales_order_dashboard.py
            assert edge["source"] == make_id("sales_order")

    def test_dashboard_extractor_emits_no_nodes(self):
        # Dashboards only emit edges; the doctypes themselves are nodes
        # created by the DocType extractor (or referenced as targets).
        result = extract_dashboard(DASHBOARD_PY)
        assert result["nodes"] == []

    def test_handles_i18n_label_calls(self, tmp_path):
        """Dashboards typically wrap labels in _("..."); that shouldn't
        prevent extraction of the items list."""
        p = tmp_path / "x_dashboard.py"
        p.mkdir(parents=False, exist_ok=True) if False else None
        d = tmp_path / "x"
        d.mkdir()
        p = d / "x_dashboard.py"
        p.write_text(
            'from frappe import _\n'
            'def get_data():\n'
            '    return {\n'
            '        "transactions": [\n'
            '            {"label": _("Section"), "items": ["Sales Invoice"]},\n'
            '        ],\n'
            '    }\n'
        )
        result = extract_dashboard(p)
        assert any(e["target"] == make_id("Sales Invoice") for e in result["edges"])

    def test_self_link_skipped(self, tmp_path):
        d = tmp_path / "sales_order"
        d.mkdir()
        p = d / "sales_order_dashboard.py"
        p.write_text(
            'def get_data():\n'
            '    return {"transactions": [{"items": ["Sales Order", "Sales Invoice"]}]}\n'
        )
        result = extract_dashboard(p)
        # Sales Order → Sales Order edge is a self-loop and should be dropped.
        assert all(e["target"] != make_id("Sales Order") for e in result["edges"])
        assert any(e["target"] == make_id("Sales Invoice") for e in result["edges"])

    def test_syntax_error_returns_empty(self, tmp_path):
        d = tmp_path / "x"
        d.mkdir()
        p = d / "x_dashboard.py"
        p.write_text("def get_data(:")  # syntax error
        assert extract_dashboard(p) == {"nodes": [], "edges": []}


# ── Hooks ───────────────────────────────────────────────────────────────────

class TestHooks:
    def test_doc_events_edge(self):
        result = extract_hooks(HOOKS_PY)
        edges = [e for e in result["edges"] if e["relation"] == "hooked_on"]
        assert len(edges) == 1
        assert edges[0]["event"] == "on_submit"
        assert edges[0]["doctype"] == "Sales Order"
        assert edges[0]["target"] == make_id("Sales Order")

    def test_doc_events_handler_node_created(self):
        result = extract_hooks(HOOKS_PY)
        hook_nodes = [n for n in result["nodes"] if n["file_type"] == "hook"]
        labels = {n["label"] for n in hook_nodes}
        assert "test_app.selling.doctype.sales_order.sales_order.on_submit" in labels

    def test_scheduler_event_node_with_schedule_metadata(self):
        result = extract_hooks(HOOKS_PY)
        hook_nodes = [n for n in result["nodes"] if n["file_type"] == "hook"]
        daily = next(
            (n for n in hook_nodes if n["label"] == "test_app.tasks.daily_cleanup"),
            None,
        )
        assert daily is not None
        assert daily.get("schedule") == "daily"

    def test_doc_events_handler_list(self, tmp_path):
        """Multiple handlers per event should each get their own edge."""
        p = tmp_path / "hooks.py"
        p.write_text(
            'doc_events = {\n'
            '    "Sales Order": {"on_submit": ["app.h1", "app.h2"]}\n'
            '}\n'
        )
        result = extract_hooks(p)
        edges = [e for e in result["edges"] if e["relation"] == "hooked_on"]
        assert len(edges) == 2
        sources = {e["source"] for e in edges}
        assert make_id("app.h1") in sources
        assert make_id("app.h2") in sources

    def test_tuple_key_skipped_with_warning_silently(self, tmp_path):
        """A non-literal dict key (e.g. tuple(var)) is skipped, not fatal.

        The rest of the dict is still processed.
        """
        p = tmp_path / "hooks.py"
        p.write_text(
            'pcd = ("X",)\n'
            'doc_events = {\n'
            '    tuple(pcd): {"validate": "app.skip_this"},\n'
            '    "Sales Order": {"on_submit": "app.so_handler"}\n'
            '}\n'
        )
        result = extract_hooks(p)
        edges = [e for e in result["edges"] if e["relation"] == "hooked_on"]
        assert len(edges) == 1
        assert edges[0]["doctype"] == "Sales Order"

    def test_wildcard_doctype_key(self, tmp_path):
        p = tmp_path / "hooks.py"
        p.write_text(
            'doc_events = {"*": {"validate": "app.everywhere"}}\n'
        )
        result = extract_hooks(p)
        edges = [e for e in result["edges"] if e["relation"] == "hooked_on"]
        assert len(edges) == 1
        assert edges[0]["doctype"] == "*"

    def test_override_whitelisted_methods(self, tmp_path):
        p = tmp_path / "hooks.py"
        p.write_text(
            'override_whitelisted_methods = {\n'
            '    "frappe.www.contact.send_message": "erpnext.templates.utils.send_message"\n'
            '}\n'
        )
        result = extract_hooks(p)
        edges = [e for e in result["edges"] if e["relation"] == "overrides"]
        assert len(edges) == 1
        assert edges[0]["source"] == make_id("erpnext.templates.utils.send_message")
        assert edges[0]["target"] == make_id("frappe.www.contact.send_message")

    def test_extend_doctype_class(self, tmp_path):
        p = tmp_path / "hooks.py"
        p.write_text(
            'extend_doctype_class = {"Address": "erpnext.accounts.custom.address.ERPNextAddress"}\n'
        )
        result = extract_hooks(p)
        edges = [e for e in result["edges"] if e["relation"] == "overrides"]
        assert len(edges) == 1
        assert edges[0]["source"] == make_id(
            "erpnext.accounts.custom.address.ERPNextAddress"
        )
        assert edges[0]["target"] == make_id("Address")

    def test_doctype_js_string_value(self, tmp_path):
        p = tmp_path / "hooks.py"
        p.write_text(
            'doctype_js = {"Address": "public/js/address.js"}\n'
        )
        result = extract_hooks(p)
        edges = [e for e in result["edges"] if e["relation"] == "extends_client"]
        assert len(edges) == 1
        assert edges[0]["source"] == make_id("public/js/address.js")
        assert edges[0]["target"] == make_id("Address")

    def test_doctype_js_list_value(self, tmp_path):
        p = tmp_path / "hooks.py"
        p.write_text(
            'doctype_js = {"Address": ["public/js/a.js", "public/js/b.js"]}\n'
        )
        result = extract_hooks(p)
        edges = [e for e in result["edges"] if e["relation"] == "extends_client"]
        assert len(edges) == 2

    def test_scheduler_cron_schedule_metadata(self, tmp_path):
        p = tmp_path / "hooks.py"
        p.write_text(
            'scheduler_events = {\n'
            '    "cron": {"0 * * * *": ["app.tasks.hourly_thing"]}\n'
            '}\n'
        )
        result = extract_hooks(p)
        hook = next(n for n in result["nodes"] if n["label"] == "app.tasks.hourly_thing")
        assert hook["schedule"] == "cron:0 * * * *"

    def test_irrelevant_assignments_ignored(self, tmp_path):
        p = tmp_path / "hooks.py"
        p.write_text(
            'app_name = "myapp"\n'
            'app_title = "My App"\n'
        )
        assert extract_hooks(p) == {"nodes": [], "edges": []}

    def test_syntax_error_returns_empty(self, tmp_path):
        p = tmp_path / "hooks.py"
        p.write_text('doc_events = {bad syntax')
        assert extract_hooks(p) == {"nodes": [], "edges": []}


# ── Smoke test on the dev-bench fixture ─────────────────────────────────────

class TestSmoke:
    """Sanity check: running every extractor against the sample app
    produces a reasonable set of nodes + edges with no exceptions."""

    def test_full_run_on_sample_app(self):
        results = [
            extract_doctype(DOCTYPE_JSON),
            extract_modules(MODULES_TXT),
            extract_record(REPORT_JSON),
            extract_dashboard(DASHBOARD_PY),
            extract_hooks(HOOKS_PY),
        ]
        # Every result has the expected shape
        for r in results:
            assert "nodes" in r and "edges" in r
            assert isinstance(r["nodes"], list)
            assert isinstance(r["edges"], list)

        # Total counts make sense (more than zero, less than absurd)
        total_nodes = sum(len(r["nodes"]) for r in results)
        total_edges = sum(len(r["edges"]) for r in results)
        assert total_nodes > 0
        assert total_edges > 0
        assert total_nodes < 1000
        assert total_edges < 1000
