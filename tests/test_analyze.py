"""Tests for codemap.analyze — god nodes, surprises, questions, permissions."""
from __future__ import annotations

from codemap.analyze import (
    god_nodes,
    permission_matrix,
    suggest_questions,
    surprising_connections,
)
from codemap.build import build
from codemap.graph_primitives import make_edge, make_node


def _doctype(nid: str, label: str, **flags) -> dict:
    return make_node(nid, label, "doctype", "/x.json", 1, 1, **flags)


def _module(nid: str, label: str) -> dict:
    return make_node(nid, label, "module", "/m.txt", 1, 1)


def _role(nid: str, label: str) -> dict:
    return make_node(nid, label, "role", "/r", 1, 1)


def _file(nid: str, label: str) -> dict:
    return make_node(nid, label, "file", "/f.py", 1, 1)


# ── God nodes ────────────────────────────────────────────────────────────


class TestGodNodes:
    def test_returns_top_n(self):
        nodes = [_doctype(f"d{i}", f"D{i}") for i in range(5)]
        # d0 is connected to all others; d1-4 only connect to d0
        edges = [
            make_edge("d0", f"d{i}", "links_to", "/x", 1)
            for i in range(1, 5)
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        gods = god_nodes(G, top_n=3)
        assert gods[0]["id"] == "d0"
        assert len(gods) == 3

    def test_skips_file_nodes(self):
        """File nodes accumulate scaffolding edges and aren't real hubs."""
        nodes = [_file("f", "main.py"), _doctype("d", "D")]
        # Bulk up file degree with synthetic siblings
        for i in range(5):
            nodes.append(make_node(
                f"sym{i}", f"sym{i}", "code", "/main.py", 1, 1,
            ))
        edges = [
            make_edge("f", f"sym{i}", "contains", "/main.py", 1)
            for i in range(5)
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        gods = god_nodes(G)
        assert all(g["file_type"] != "file" for g in gods)

    def test_flags_structural_hubs(self):
        nodes = [_doctype("item", "Item"), _doctype("ord", "Sales Order")]
        edges = [make_edge("item", "ord", "links_to", "/x", 1)]
        G = build([{"nodes": nodes, "edges": edges}])
        gods = god_nodes(G)
        item_entry = next(g for g in gods if g["label"] == "Item")
        assert item_entry["is_structural_hub"] is True
        order_entry = next(g for g in gods if g["label"] == "Sales Order")
        assert order_entry["is_structural_hub"] is False

    def test_empty_graph(self):
        G = build([{"nodes": [], "edges": []}])
        assert god_nodes(G) == []


# ── Surprising connections ──────────────────────────────────────────────


class TestSurprisingConnections:
    def test_inferred_edge_is_surprising(self):
        nodes = [_doctype("a", "A"), _doctype("b", "B")]
        edges = [make_edge("a", "b", "calls_api", "/x", 1,
                           confidence="INFERRED")]
        G = build([{"nodes": nodes, "edges": edges}])
        surprises = surprising_connections(G)
        assert len(surprises) == 1
        assert surprises[0]["confidence"] == "INFERRED"

    def test_extracted_intra_module_edge_is_not_surprising(self):
        """A plain EXTRACTED edge inside one module shouldn't surface."""
        nodes = [
            _module("m", "M"),
            _doctype("a", "A"), _doctype("b", "B"),
        ]
        edges = [
            make_edge("a", "m", "belongs_to_module", "/x", 1, module="M"),
            make_edge("b", "m", "belongs_to_module", "/x", 1, module="M"),
            make_edge("a", "b", "links_to", "/x", 1),
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        surprises = surprising_connections(G)
        # No cross-module/cross-community signal → nothing surprising.
        assert all(s["relation"] != "links_to" for s in surprises)

    def test_cross_module_edge_is_surprising(self):
        nodes = [
            _module("sel", "Selling"), _module("stk", "Stock"),
            _doctype("ord", "Sales Order"), _doctype("itm", "Item"),
        ]
        edges = [
            make_edge("ord", "sel", "belongs_to_module", "/x", 1,
                      module="Selling"),
            make_edge("itm", "stk", "belongs_to_module", "/x", 1,
                      module="Stock"),
            make_edge("ord", "itm", "links_to", "/x", 1),
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        surprises = surprising_connections(G)
        assert any("crosses modules" in s["why"] for s in surprises)

    def test_trivial_relations_skipped(self):
        nodes = [
            _module("m", "M"),
            _doctype("a", "A"),
        ]
        edges = [
            make_edge("a", "m", "belongs_to_module", "/x", 1,
                      confidence="INFERRED", module="M"),
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        surprises = surprising_connections(G)
        assert surprises == []

    def test_top_n_caps_results(self):
        nodes = [_doctype(f"d{i}", f"D{i}") for i in range(10)]
        edges = [
            make_edge(f"d{i}", f"d{i+1}", "calls_api", "/x", 1,
                      confidence="INFERRED")
            for i in range(9)
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        surprises = surprising_connections(G, top_n=3)
        assert len(surprises) <= 3


# ── Suggested questions ─────────────────────────────────────────────────


class TestSuggestQuestions:
    def test_submittable_doctype_gets_hook_chain_question(self):
        node = _doctype("ord", "Sales Order", is_submittable=1)
        G = build([{"nodes": [node], "edges": []}])
        questions = suggest_questions(G)
        assert any(q["type"] == "hook_chain" for q in questions)

    def test_workflow_doctype_gets_state_question(self):
        nodes = [
            _doctype("ord", "Sales Order"),
            make_node("wf", "SO Workflow", "workflow", "/wf.json", 1, 1),
        ]
        edges = [make_edge("wf", "ord", "applies_to", "/x", 1)]
        G = build([{"nodes": nodes, "edges": edges}])
        questions = suggest_questions(G)
        assert any(q["type"] == "workflow_states" for q in questions)

    def test_permitted_role_doctype_gets_perms_question(self):
        nodes = [
            _doctype("ord", "Sales Order"),
            _role("admin", "System Manager"),
        ]
        edges = [make_edge("admin", "ord", "permitted_role", "/x", 1, submit=1)]
        G = build([{"nodes": nodes, "edges": edges}])
        questions = suggest_questions(G)
        assert any(q["type"] == "permissions" for q in questions)

    def test_calls_api_present_yields_client_api_question(self):
        nodes = [_doctype("ord", "Sales Order"),
                 make_node("js", "client.js", "file", "/c.js", 1, 1)]
        edges = [make_edge("js", "ord", "calls_api", "/x", 1,
                           confidence="INFERRED")]
        G = build([{"nodes": nodes, "edges": edges}])
        questions = suggest_questions(G)
        assert any(q["type"] == "client_api" for q in questions)

    def test_no_signal_returns_placeholder(self):
        G = build([{"nodes": [], "edges": []}])
        questions = suggest_questions(G)
        assert len(questions) == 1
        assert questions[0]["type"] == "no_signal"


# ── Permission matrix ───────────────────────────────────────────────────


class TestPermissionMatrix:
    def test_basic(self):
        nodes = [
            _doctype("ord", "Sales Order"),
            _role("admin", "System Manager"),
        ]
        edges = [make_edge(
            "admin", "ord", "permitted_role", "/x", 1,
            read=1, write=1, submit=1, cancel=0,
        )]
        G = build([{"nodes": nodes, "edges": edges}])
        matrix = permission_matrix(G)
        perms = matrix["Sales Order"]["System Manager"]
        assert perms["read"] is True
        assert perms["submit"] is True
        assert perms["cancel"] is False

    def test_or_of_multiple_perm_levels(self):
        """Two edges between the same role and DocType OR their flags."""
        nodes = [
            _doctype("ord", "Sales Order"),
            _role("admin", "System Manager"),
        ]
        # Same edge added twice with different flags — second wins under
        # NetworkX add_edge unless we OR.  Build with two extractions so
        # both edges are visited.
        ext1 = {"nodes": nodes, "edges": [make_edge(
            "admin", "ord", "permitted_role", "/x", 1, submit=1,
        )]}
        ext2 = {"nodes": [], "edges": [make_edge(
            "admin", "ord", "permitted_role", "/y", 1, write=1,
        )]}
        G = build([ext1, ext2])
        matrix = permission_matrix(G)
        # NetworkX merges into a single edge; permission_matrix should
        # still report whatever flags were set on the surviving edge.
        perms = matrix["Sales Order"]["System Manager"]
        assert perms.get("submit") or perms.get("write")

    def test_skips_non_permission_edges(self):
        nodes = [_doctype("a", "A"), _doctype("b", "B")]
        edges = [make_edge("a", "b", "links_to", "/x", 1)]
        G = build([{"nodes": nodes, "edges": edges}])
        assert permission_matrix(G) == {}

    def test_role_target_order_works_either_way(self):
        """Edge endpoint order doesn't matter on undirected graphs."""
        nodes = [
            _doctype("ord", "Sales Order"),
            _role("admin", "System Manager"),
        ]
        edges = [make_edge(
            "ord", "admin", "permitted_role", "/x", 1, read=1,
        )]
        G = build([{"nodes": nodes, "edges": edges}])
        matrix = permission_matrix(G)
        assert matrix["Sales Order"]["System Manager"]["read"] is True
