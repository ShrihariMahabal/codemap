"""Tests for codemap.frappe_extract — Frappe metadata extractors."""
from pathlib import Path

import pytest

from codemap.frappe_extract import (
    extract_client_script,
    extract_custom_field,
    extract_dashboard,
    extract_doctype,
    extract_hooks,
    extract_modules,
    extract_notification,
    extract_property_setter,
    extract_record,
    extract_server_script,
    extract_workflow,
)
from codemap.graph_primitives import make_id

FIXTURE = Path(__file__).parent / "fixtures" / "sample_app"
DOCTYPE_JSON = FIXTURE / "test_app/selling/doctype/sales_order/sales_order.json"
HOOKS_PY = FIXTURE / "test_app/hooks.py"
DASHBOARD_PY = FIXTURE / "test_app/selling/doctype/sales_order/sales_order_dashboard.py"
MODULES_TXT = FIXTURE / "test_app/modules.txt"
REPORT_JSON = FIXTURE / "test_app/selling/report/sales_analytics/sales_analytics.json"
WORKFLOW_JSON = FIXTURE / "test_app/selling/workflow/sales_order_approval/sales_order_approval.json"
NOTIFICATION_JSON = FIXTURE / "test_app/selling/notification/order_submitted/order_submitted.json"
SERVER_SCRIPT_JSON = FIXTURE / "test_app/selling/server_script/add_region/add_region.json"
CLIENT_SCRIPT_JSON = FIXTURE / "test_app/selling/client_script/highlight_total/highlight_total.json"
CUSTOM_FIELD_JSON = FIXTURE / "test_app/fixtures/custom_field.json"
PROPERTY_SETTER_JSON = FIXTURE / "test_app/fixtures/property_setter.json"


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

    def test_no_module_edge_when_module_missing(self, tmp_path):
        p = tmp_path / "no_module.json"
        p.write_text('{"doctype": "DocType", "name": "NoMod", "fields": []}')
        result = extract_doctype(p)
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

    def test_behavioural_flags_on_doctype_node(self):
        result = extract_doctype(DOCTYPE_JSON)
        node = next(n for n in result["nodes"] if n["file_type"] == "doctype")
        assert node["is_submittable"] == 1
        assert node["track_changes"] == 1
        assert node["autoname"] == "naming_series:"

    def test_permission_role_nodes_emitted(self):
        result = extract_doctype(DOCTYPE_JSON)
        roles = {n["label"] for n in result["nodes"] if n["file_type"] == "role"}
        assert "Sales User" in roles
        assert "Sales Manager" in roles

    def test_permitted_role_edges(self):
        result = extract_doctype(DOCTYPE_JSON)
        perm_edges = [
            e for e in result["edges"] if e["relation"] == "permitted_role"
        ]
        assert len(perm_edges) == 2

        manager = next(e for e in perm_edges if e["role"] == "Sales Manager")
        assert manager["delete"] == 1
        assert manager["export"] == 1
        assert manager["submit"] == 1

        user = next(e for e in perm_edges if e["role"] == "Sales User")
        assert user["read"] == 1
        assert user["write"] == 1
        # Sales User in fixture doesn't have delete
        assert "delete" not in user

    def test_fetch_from_edge_resolves_via_link_field(self):
        """customer_name's fetch_from='customer.customer_name' → edge to Customer."""
        result = extract_doctype(DOCTYPE_JSON)
        fetch_edges = [e for e in result["edges"] if e["relation"] == "fetch_from"]
        assert len(fetch_edges) == 1
        edge = fetch_edges[0]
        assert edge["target"] == make_id("Customer")
        assert edge["link_field"] == "customer"
        assert edge["source_field"] == "customer_name"
        assert edge["confidence"] == "INFERRED"

    def test_fetch_from_skipped_when_link_field_unknown(self, tmp_path):
        p = tmp_path / "x.json"
        p.write_text("""{
            "doctype": "DocType",
            "name": "X",
            "fields": [
                {"fieldname": "customer_name", "fieldtype": "Data",
                 "fetch_from": "nonexistent.field"}
            ]
        }""")
        result = extract_doctype(p)
        fetch_edges = [e for e in result["edges"] if e["relation"] == "fetch_from"]
        assert fetch_edges == []

    def test_permission_with_no_role_skipped(self, tmp_path):
        p = tmp_path / "x.json"
        p.write_text("""{
            "doctype": "DocType",
            "name": "X",
            "fields": [],
            "permissions": [{"read": 1}]
        }""")
        result = extract_doctype(p)
        perm_edges = [e for e in result["edges"] if e["relation"] == "permitted_role"]
        assert perm_edges == []


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

    def test_has_permission_emits_permission_hook_edge(self):
        result = extract_hooks(HOOKS_PY)
        perm_edges = [
            e for e in result["edges"]
            if e["relation"] == "permission_hook"
        ]
        # has_permission + permission_query_conditions
        assert len(perm_edges) == 2
        targets = {e["target"] for e in perm_edges}
        assert make_id("Sales Order") in targets

    def test_permission_hooks_tagged_role(self):
        result = extract_hooks(HOOKS_PY)
        perm_handlers = [
            n for n in result["nodes"]
            if n["file_type"] == "hook" and n.get("role") == "permission"
        ]
        assert len(perm_handlers) == 2

    def test_jinja_methods_tagged(self):
        result = extract_hooks(HOOKS_PY)
        jinja_nodes = [
            n for n in result["nodes"]
            if n["file_type"] == "hook" and n.get("role") == "jinja"
        ]
        # Three Jinja entries — the labelled "money:..." should keep
        # only the right-hand callable as the node identifier.
        labels = {n["label"] for n in jinja_nodes}
        assert "test_app.utils.format_currency" in labels
        assert "test_app.utils.format_money" in labels
        assert "test_app.utils.titlecase" in labels

    def test_app_include_assets_tagged(self):
        result = extract_hooks(HOOKS_PY)
        asset_nodes = [
            n for n in result["nodes"]
            if n["file_type"] == "hook" and n.get("role") == "app_include"
        ]
        kinds = {n["asset_kind"] for n in asset_nodes}
        assert "app_include_js" in kinds
        assert "app_include_css" in kinds

    def test_request_and_session_hooks_tagged(self):
        result = extract_hooks(HOOKS_PY)
        request_hooks = [
            n for n in result["nodes"] if n.get("role") == "request_hook"
        ]
        assert len(request_hooks) == 2
        boot_hooks = [
            n for n in result["nodes"] if n.get("role") == "boot_hook"
        ]
        assert len(boot_hooks) == 1

    def test_regional_overrides_emit_country_metadata(self):
        result = extract_hooks(HOOKS_PY)
        regional_edges = [
            e for e in result["edges"]
            if e["relation"] == "overrides" and e.get("scope") == "regional"
        ]
        assert len(regional_edges) == 1
        assert regional_edges[0]["country"] == "India"

    def test_fixtures_emit_export_edges(self):
        result = extract_hooks(HOOKS_PY)
        fix_edges = [
            e for e in result["edges"] if e["relation"] == "exports_fixture"
        ]
        doctypes = {e["doctype"] for e in fix_edges}
        assert "Custom Field" in doctypes
        assert "Property Setter" in doctypes

    def test_auto_cancel_exempted_emits_edges(self):
        result = extract_hooks(HOOKS_PY)
        edges = [
            e for e in result["edges"]
            if e["relation"] == "auto_cancel_exempted"
        ]
        assert len(edges) == 1
        assert edges[0]["doctype"] == "Sales Invoice"

    def test_dashboard_override(self):
        result = extract_hooks(HOOKS_PY)
        dash_edges = [
            e for e in result["edges"]
            if e["relation"] == "overrides" and e.get("scope") == "dashboard"
        ]
        assert len(dash_edges) == 1
        assert dash_edges[0]["target"] == make_id("Sales Order")

    def test_doctype_list_js_treated_like_doctype_js(self, tmp_path):
        p = tmp_path / "hooks.py"
        p.write_text('doctype_list_js = {"Sales Order": "public/js/so_list.js"}\n')
        result = extract_hooks(p)
        edges = [e for e in result["edges"] if e["relation"] == "extends_client"]
        assert len(edges) == 1
        assert edges[0]["target"] == make_id("Sales Order")


# ── Workflow ────────────────────────────────────────────────────────────────

class TestWorkflow:
    def test_workflow_node_created(self):
        result = extract_workflow(WORKFLOW_JSON)
        wf_nodes = [n for n in result["nodes"] if n["file_type"] == "workflow"]
        assert len(wf_nodes) == 1
        assert wf_nodes[0]["label"] == "Sales Order Approval"

    def test_workflow_for_doctype_edge(self):
        result = extract_workflow(WORKFLOW_JSON)
        edges = [e for e in result["edges"] if e["relation"] == "workflow_for"]
        assert len(edges) == 1
        assert edges[0]["target"] == make_id("Sales Order")

    def test_state_nodes_created(self):
        result = extract_workflow(WORKFLOW_JSON)
        states = {n["label"] for n in result["nodes"] if n["file_type"] == "workflow_state"}
        assert states == {"Draft", "Approved"}

    def test_has_state_edges(self):
        result = extract_workflow(WORKFLOW_JSON)
        has_state = [e for e in result["edges"] if e["relation"] == "has_state"]
        assert len(has_state) == 2

    def test_transition_edge(self):
        result = extract_workflow(WORKFLOW_JSON)
        transitions = [
            e for e in result["edges"] if e["relation"] == "workflow_transition"
        ]
        assert len(transitions) == 1
        edge = transitions[0]
        assert edge["action"] == "Approve"
        assert edge["allowed"] == "Sales Manager"
        assert edge["from_state"] == "Draft"
        assert edge["to_state"] == "Approved"

    def test_non_workflow_returns_empty(self, tmp_path):
        p = tmp_path / "x.json"
        p.write_text('{"doctype": "DocType", "name": "X"}')
        assert extract_workflow(p) == {"nodes": [], "edges": []}

    def test_transition_with_unknown_state_skipped(self, tmp_path):
        p = tmp_path / "wf.json"
        p.write_text("""{
            "doctype": "Workflow",
            "name": "W",
            "document_type": "Sales Order",
            "states": [{"state": "Draft", "doc_status": "0"}],
            "transitions": [
                {"state": "Draft", "next_state": "Ghost",
                 "action": "Approve", "allowed": "Manager"}
            ]
        }""")
        result = extract_workflow(p)
        transitions = [e for e in result["edges"] if e["relation"] == "workflow_transition"]
        assert transitions == []


# ── Notification ────────────────────────────────────────────────────────────

class TestNotification:
    def test_notification_node_with_metadata(self):
        result = extract_notification(NOTIFICATION_JSON)
        nodes = [n for n in result["nodes"] if n["file_type"] == "notification"]
        assert len(nodes) == 1
        node = nodes[0]
        assert node["label"] == "Order Submitted"
        assert node["event"] == "Submit"
        assert node["channel"] == "Email"

    def test_notification_for_doctype_edge(self):
        result = extract_notification(NOTIFICATION_JSON)
        edges = [e for e in result["edges"] if e["relation"] == "notification_for"]
        assert len(edges) == 1
        assert edges[0]["target"] == make_id("Sales Order")

    def test_recipient_role_edge(self):
        result = extract_notification(NOTIFICATION_JSON)
        edges = [
            e for e in result["edges"]
            if e["relation"] == "notification_recipient"
        ]
        assert len(edges) == 1
        assert edges[0]["role"] == "Sales Manager"

    def test_role_node_created(self):
        result = extract_notification(NOTIFICATION_JSON)
        roles = {n["label"] for n in result["nodes"] if n["file_type"] == "role"}
        assert roles == {"Sales Manager"}

    def test_non_notification_returns_empty(self, tmp_path):
        p = tmp_path / "x.json"
        p.write_text('{"doctype": "DocType", "name": "X"}')
        assert extract_notification(p) == {"nodes": [], "edges": []}

    def test_recipient_without_role_skipped(self, tmp_path):
        p = tmp_path / "n.json"
        p.write_text("""{
            "doctype": "Notification",
            "name": "N",
            "document_type": "Sales Order",
            "recipients": [{"receiver_by_document_field": "owner"}]
        }""")
        result = extract_notification(p)
        edges = [e for e in result["edges"] if e["relation"] == "notification_recipient"]
        assert edges == []


# ── Server / Client Scripts ────────────────────────────────────────────────

class TestServerScript:
    def test_node_and_edge_emitted(self):
        result = extract_server_script(SERVER_SCRIPT_JSON)
        nodes = [n for n in result["nodes"] if n["file_type"] == "server_script"]
        assert len(nodes) == 1
        node = nodes[0]
        assert node["label"] == "Add Region On Save"
        assert node["doctype_event"] == "Before Save"
        assert "script" not in node  # Body is intentionally excluded
        assert node["script_lines"] >= 1

        edges = [e for e in result["edges"] if e["relation"] == "script_for"]
        assert len(edges) == 1
        assert edges[0]["target"] == make_id("Sales Order")

    def test_handles_array_form(self, tmp_path):
        p = tmp_path / "ss.json"
        p.write_text("""[
            {"doctype": "Server Script", "name": "A",
             "reference_doctype": "Sales Order", "script": "x"},
            {"doctype": "Server Script", "name": "B",
             "reference_doctype": "Sales Invoice", "script": "y"}
        ]""")
        result = extract_server_script(p)
        nodes = [n for n in result["nodes"] if n["file_type"] == "server_script"]
        assert len(nodes) == 2

    def test_non_server_script_returns_empty(self, tmp_path):
        p = tmp_path / "x.json"
        p.write_text('{"doctype": "DocType", "name": "X"}')
        assert extract_server_script(p) == {"nodes": [], "edges": []}


class TestClientScript:
    def test_node_and_edge_emitted(self):
        result = extract_client_script(CLIENT_SCRIPT_JSON)
        nodes = [n for n in result["nodes"] if n["file_type"] == "client_script"]
        assert len(nodes) == 1
        assert nodes[0]["view"] == "Form"

        edges = [e for e in result["edges"] if e["relation"] == "script_for"]
        assert len(edges) == 1
        assert edges[0]["target"] == make_id("Sales Order")


# ── Custom Field / Property Setter ─────────────────────────────────────────

class TestCustomField:
    def test_node_and_edge_emitted(self):
        result = extract_custom_field(CUSTOM_FIELD_JSON)
        nodes = [n for n in result["nodes"] if n["file_type"] == "custom_field"]
        assert len(nodes) == 1
        assert nodes[0]["fieldname"] == "region"
        assert nodes[0]["fieldtype"] == "Data"
        assert nodes[0]["insert_after"] == "customer"

        edges = [e for e in result["edges"] if e["relation"] == "custom_field_on"]
        assert len(edges) == 1
        assert edges[0]["target"] == make_id("Sales Order")

    def test_non_custom_field_returns_empty(self, tmp_path):
        p = tmp_path / "x.json"
        p.write_text('{"doctype": "DocType", "name": "X"}')
        assert extract_custom_field(p) == {"nodes": [], "edges": []}


class TestPropertySetter:
    def test_node_and_edge_emitted(self):
        result = extract_property_setter(PROPERTY_SETTER_JSON)
        nodes = [n for n in result["nodes"] if n["file_type"] == "property_setter"]
        assert len(nodes) == 1
        node = nodes[0]
        assert node["field_name"] == "customer"
        assert node["property"] == "reqd"
        assert node["value"] == "1"

        edges = [e for e in result["edges"] if e["relation"] == "property_override_on"]
        assert len(edges) == 1
        assert edges[0]["target"] == make_id("Sales Order")

    def test_handles_single_dict_form(self, tmp_path):
        p = tmp_path / "ps.json"
        p.write_text("""{
            "doctype": "Property Setter",
            "name": "X-y-z",
            "doc_type": "Sales Order",
            "field_name": "y",
            "property": "z",
            "value": "1"
        }""")
        result = extract_property_setter(p)
        nodes = [n for n in result["nodes"] if n["file_type"] == "property_setter"]
        assert len(nodes) == 1


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
