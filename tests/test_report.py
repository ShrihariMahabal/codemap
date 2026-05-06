"""Tests for codemap.report — markdown report assembly."""
from __future__ import annotations

from codemap.build import build
from codemap.graph_primitives import make_edge, make_node
from codemap.report import generate


def _doctype(nid: str, label: str, **flags) -> dict:
    return make_node(nid, label, "doctype", "/x.json", 1, 1, **flags)


def _basic_kwargs(G, **overrides) -> dict:
    """Return the minimum kwargs ``generate()`` needs for a smoke run."""
    base = {
        "detection": {"total_files": 5, "files": {"code_py": ["a", "b"]}},
        "communities": {0: list(G.nodes())},
        "community_labels": {0: "Selling"},
        "cohesion": {0: 0.5},
        "god_node_list": [],
        "surprises": [],
        "questions": [],
        "permissions": {},
        "app_root": "/sample",
    }
    base.update(overrides)
    return base


# ── Headline sections ────────────────────────────────────────────────────


class TestHeader:
    def test_includes_app_root_and_date(self):
        G = build([{"nodes": [_doctype("a", "A")], "edges": []}])
        report = generate(G, **_basic_kwargs(G))
        first_line = report.splitlines()[0]
        assert first_line.startswith("# Codemap Report")
        assert "/sample" in first_line


class TestCorpusCheck:
    def test_renders_total_and_categories(self):
        G = build([{"nodes": [_doctype("a", "A")], "edges": []}])
        report = generate(
            G,
            **_basic_kwargs(
                G,
                detection={"total_files": 7,
                           "files": {"code_py": ["a", "b"], "code_js": ["c"]}},
            ),
        )
        assert "## Corpus Check" in report
        assert "7 files" in report
        assert "code_py" in report


class TestSummaryConfidenceBreakdown:
    def test_counts_each_confidence_level(self):
        nodes = [_doctype("a", "A"), _doctype("b", "B"), _doctype("c", "C")]
        edges = [
            make_edge("a", "b", "links_to", "/x", 1, confidence="EXTRACTED"),
            make_edge("a", "c", "links_to", "/x", 1, confidence="INFERRED"),
            make_edge("b", "c", "links_to", "/x", 1, confidence="AMBIGUOUS"),
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        report = generate(G, **_basic_kwargs(G))
        assert "## Summary" in report
        assert "EXTRACTED" in report
        assert "INFERRED" in report
        assert "AMBIGUOUS" in report


# ── Frappe-specific sections ─────────────────────────────────────────────


class TestPermissionMatrix:
    def test_renders_table_when_present(self):
        G = build([{"nodes": [_doctype("ord", "Sales Order")],
                    "edges": []}])
        permissions = {
            "Sales Order": {
                "System Manager": {
                    "read": True, "write": True, "submit": True,
                    "create": False, "delete": False, "cancel": False,
                    "amend": False,
                },
            },
        }
        report = generate(G, **_basic_kwargs(G, permissions=permissions))
        assert "## Permission Matrix" in report
        assert "| Sales Order | System Manager" in report
        assert "✓" in report
        assert "✗" in report

    def test_skipped_when_empty(self):
        G = build([{"nodes": [_doctype("a", "A")], "edges": []}])
        report = generate(G, **_basic_kwargs(G))
        assert "## Permission Matrix" not in report


class TestWorkflowDiagrams:
    def test_renders_mermaid(self):
        nodes = [
            make_node("wf", "SO Workflow", "workflow", "/wf.json", 1, 1),
            make_node("draft", "Draft", "workflow_state", "/wf.json", 1, 1),
            make_node("approved", "Approved", "workflow_state", "/wf.json", 1, 1),
        ]
        edges = [make_edge(
            "draft", "approved", "workflow_transition", "/wf.json", 1,
            workflow="SO Workflow", from_state="Draft", to_state="Approved",
            action="Approve",
        )]
        G = build([{"nodes": nodes, "edges": edges}])
        report = generate(G, **_basic_kwargs(G))
        assert "## Workflow Diagrams" in report
        assert "```mermaid" in report
        assert "Draft" in report and "Approved" in report
        assert "Approve" in report


class TestLifecycleOrder:
    def test_methods_sorted_by_canonical_order(self):
        nodes = [
            _doctype("ord", "Sales Order"),
            make_node("on_submit", ".on_submit()", "code", "/c.py", 1, 1),
            make_node("validate", ".validate()", "code", "/c.py", 1, 1),
            make_node("autoname", ".autoname()", "code", "/c.py", 1, 1),
        ]
        edges = [
            make_edge("ord", "on_submit", "lifecycle_method", "/c.py", 1),
            make_edge("ord", "validate", "lifecycle_method", "/c.py", 1),
            make_edge("ord", "autoname", "lifecycle_method", "/c.py", 1),
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        report = generate(G, **_basic_kwargs(G))
        section = report[report.index("## Lifecycle Order"):]
        autoname_pos = section.index("autoname")
        validate_pos = section.index("validate")
        on_submit_pos = section.index("on_submit")
        assert autoname_pos < validate_pos < on_submit_pos


class TestControllerHierarchy:
    def test_walks_inherits_subclass_to_base(self):
        nodes = [
            make_node("so", "SalesOrder", "code", "/s.py", 1, 1),
            make_node("sc", "SellingController", "code", "/s.py", 1, 1),
            make_node("doc", "Document", "code", "/d.py", 1, 1),
        ]
        edges = [
            make_edge("so", "sc", "inherits", "/s.py", 1),
            make_edge("sc", "doc", "inherits", "/s.py", 1),
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        report = generate(G, **_basic_kwargs(G))
        assert "## Controller Hierarchy" in report
        assert "`SalesOrder` → `SellingController` → `Document`" in report

    def test_starts_chain_at_leaf_only(self):
        """Bases that nothing else extends should not start their own chain."""
        nodes = [
            make_node("leaf", "Leaf", "code", "/x.py", 1, 1),
            make_node("base", "Base", "code", "/x.py", 1, 1),
        ]
        edges = [make_edge("leaf", "base", "inherits", "/x.py", 1)]
        G = build([{"nodes": nodes, "edges": edges}])
        report = generate(G, **_basic_kwargs(G))
        section = report[report.index("## Controller Hierarchy"):]
        # Only one chain line should appear under the heading.
        chain_lines = [
            ln for ln in section.splitlines()
            if ln.startswith("- ") and "→" in ln
        ]
        assert len(chain_lines) == 1


class TestCustomizationMap:
    def test_groups_by_doctype(self):
        nodes = [
            _doctype("ord", "Sales Order"),
            make_node("cf", "tax_id", "custom_field", "/cf.json", 1, 1),
            make_node("ps", "status", "property_setter", "/ps.json", 1, 1),
        ]
        edges = [
            make_edge("cf", "ord", "customizes", "/cf.json", 1),
            make_edge("ps", "ord", "customizes", "/ps.json", 1),
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        report = generate(G, **_basic_kwargs(G))
        section = report[report.index("## Customization Map"):]
        assert "Sales Order" in section
        assert "Custom Field" in section
        assert "Property Setter" in section


class TestNotificationRouting:
    def test_renders_trigger_and_recipients(self):
        nodes = [
            make_node("n", "Order Submitted", "notification", "/n.json", 1, 1),
            _doctype("ord", "Sales Order"),
            make_node("admin", "Sales Manager", "role", "/r", 1, 1),
        ]
        edges = [
            make_edge("n", "ord", "notification_for", "/n.json", 1),
            make_edge("n", "admin", "notification_recipient", "/n.json", 1),
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        report = generate(G, **_basic_kwargs(G))
        section = report[report.index("## Email & Notification Routing"):]
        assert "Order Submitted" in section
        assert "Sales Order" in section
        assert "Sales Manager" in section


class TestBackgroundJobs:
    def test_lists_enqueues_job_edges(self):
        nodes = [
            make_node("caller", "process_queue()", "code", "/c.py", 1, 1),
            make_node("target", "frappe.enqueue", "external", "", 0, 0),
        ]
        edges = [make_edge("caller", "target", "enqueues_job", "/c.py", 1)]
        G = build([{"nodes": nodes, "edges": edges}])
        report = generate(G, **_basic_kwargs(G))
        assert "## Background Job Map" in report
        assert "process_queue" in report
        assert "frappe.enqueue" in report


class TestKnowledgeGaps:
    def test_lists_isolated_and_thin_communities(self):
        nodes = [_doctype(f"n{i}", f"N{i}") for i in range(3)]
        G = build([{"nodes": nodes, "edges": []}])
        report = generate(
            G,
            **_basic_kwargs(
                G,
                communities={0: ["n0"], 1: ["n1", "n2"]},
                community_labels={0: "A", 1: "B"},
                cohesion={0: 1.0, 1: 0.0},
            ),
        )
        assert "## Knowledge Gaps" in report
        assert "weakly-connected" in report
        assert "Thin community" in report


class TestSuggestedQuestions:
    def test_renders_questions(self):
        G = build([{"nodes": [_doctype("a", "A")], "edges": []}])
        questions = [
            {"type": "coupling", "question": "Q1?", "why": "because"},
            {"type": "hook_chain", "question": "Q2?", "why": "reason"},
        ]
        report = generate(G, **_basic_kwargs(G, questions=questions))
        assert "## Suggested Questions" in report
        assert "Q1?" in report and "Q2?" in report

    def test_no_signal_renders_explanation(self):
        G = build([{"nodes": [_doctype("a", "A")], "edges": []}])
        questions = [{"type": "no_signal", "question": None,
                      "why": "Not enough signal."}]
        report = generate(G, **_basic_kwargs(G, questions=questions))
        assert "Not enough signal." in report
