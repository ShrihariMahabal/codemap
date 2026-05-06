"""Tests for codemap.export — graph.json and graph.html writers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codemap.build import build
from codemap.export import to_html, to_json
from codemap.graph_primitives import make_edge, make_node


def _doctype(nid: str, label: str, **flags) -> dict:
    return make_node(nid, label, "doctype", "/x.json", 1, 1, **flags)


def _sample_graph():
    nodes = [
        _doctype("ord", "Sales Order"),
        _doctype("cust", "Customer"),
        make_node("admin", "System Manager", "role", "/r", 1, 1),
        make_node("wf", "SO Workflow", "workflow", "/w", 1, 1),
    ]
    edges = [
        make_edge("ord", "cust", "links_to", "/x", 1),
        make_edge("admin", "ord", "permitted_role", "/x", 1,
                  confidence="INFERRED"),
        make_edge("wf", "ord", "applies_to", "/x", 1,
                  confidence="AMBIGUOUS"),
    ]
    return build([{"nodes": nodes, "edges": edges}])


# ── to_json ──────────────────────────────────────────────────────────────


class TestToJson:
    def test_writes_node_link_data(self, tmp_path: Path):
        G = _sample_graph()
        out = tmp_path / "graph.json"
        to_json(G, {0: ["ord", "cust"], 1: ["admin"]}, {0: "Selling"}, out)
        data = json.loads(out.read_text())
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) == G.number_of_nodes()

    def test_node_carries_community_metadata(self, tmp_path: Path):
        G = _sample_graph()
        out = tmp_path / "graph.json"
        to_json(G, {0: ["ord", "cust"]}, {0: "Selling"}, out)
        data = json.loads(out.read_text())
        ord_node = next(n for n in data["nodes"] if n["id"] == "ord")
        assert ord_node["community"] == 0
        assert ord_node["community_label"] == "Selling"

    def test_shrink_guard_refuses(self, tmp_path: Path, capsys):
        out = tmp_path / "graph.json"
        # Write a "bigger" graph first.
        out.write_text(json.dumps({
            "nodes": [{"id": str(i)} for i in range(20)],
            "edges": [],
        }))
        small = build([{"nodes": [_doctype("a", "A")], "edges": []}])
        to_json(small, {0: ["a"]}, {}, out)
        # File should still hold the big version.
        data = json.loads(out.read_text())
        assert len(data["nodes"]) == 20
        captured = capsys.readouterr()
        assert "refusing to overwrite" in captured.err

    def test_shrink_guard_force_overrides(self, tmp_path: Path):
        out = tmp_path / "graph.json"
        out.write_text(json.dumps({
            "nodes": [{"id": str(i)} for i in range(20)],
            "edges": [],
        }))
        small = build([{"nodes": [_doctype("a", "A")], "edges": []}])
        to_json(small, {0: ["a"]}, {}, out, force=True)
        data = json.loads(out.read_text())
        assert len(data["nodes"]) == 1


# ── to_html ──────────────────────────────────────────────────────────────


class TestToHtml:
    def test_writes_html_file(self, tmp_path: Path):
        G = _sample_graph()
        out = tmp_path / "graph.html"
        to_html(G, {0: ["ord", "cust", "admin", "wf"]}, {0: "Selling"}, out)
        text = out.read_text()
        assert text.startswith("<!DOCTYPE html>")
        assert "vis-network" in text
        assert "Sales Order" in text

    def test_html_contains_confidence_filter(self, tmp_path: Path):
        G = _sample_graph()
        out = tmp_path / "graph.html"
        to_html(G, {0: ["ord", "cust", "admin", "wf"]}, {0: "Selling"}, out)
        text = out.read_text()
        for level in ("EXTRACTED", "INFERRED", "AMBIGUOUS"):
            assert level in text

    def test_too_many_nodes_raises(self, tmp_path: Path):
        # Build a graph just over the cap.
        nodes = [_doctype(f"n{i}", f"N{i}") for i in range(5001)]
        G = build([{"nodes": nodes, "edges": []}])
        with pytest.raises(ValueError, match="too large"):
            to_html(G, {0: [n["id"] for n in nodes]}, {}, tmp_path / "g.html")

    def test_node_shapes_for_file_types(self, tmp_path: Path):
        G = _sample_graph()
        out = tmp_path / "graph.html"
        to_html(G, {0: ["ord", "cust", "admin", "wf"]}, {0: "Selling"}, out)
        text = out.read_text()
        # DocType → diamond, role → square, workflow → box should appear.
        assert "diamond" in text
        assert "square" in text
        assert "box" in text

    def test_script_breakout_is_neutralised(self, tmp_path: Path):
        """Labels containing </script> must not break out of the script tag."""
        G = build([{
            "nodes": [_doctype("a", "Sales</script>Order")],
            "edges": [],
        }])
        out = tmp_path / "graph.html"
        to_html(G, {0: ["a"]}, {0: "X"}, out)
        text = out.read_text()
        # The literal closing tag must be defanged inside JSON blobs.
        # We allow </script> only as the actual closing of our embedded script,
        # never as part of node data.
        assert "Sales</script>Order" not in text
        assert "Sales<\\/script>Order" in text