"""Tests for codemap.build — extraction dict → NetworkX graph."""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from codemap.build import build, build_from_extraction, build_merge
from codemap.graph_primitives import make_edge, make_node


def _node(nid: str, label: str = "x", file_type: str = "code") -> dict:
    return make_node(nid, label, file_type, "/tmp/x.py", 1, 1)


class TestBuildFromExtraction:
    def test_empty(self):
        G = build_from_extraction({"nodes": [], "edges": []})
        assert G.number_of_nodes() == 0
        assert G.number_of_edges() == 0

    def test_default_undirected(self):
        G = build_from_extraction({"nodes": [_node("a")], "edges": []})
        assert not G.is_directed()

    def test_directed(self):
        G = build_from_extraction(
            {"nodes": [_node("a"), _node("b")],
             "edges": [make_edge("a", "b", "calls", "/x", 1)]},
            directed=True,
        )
        assert G.is_directed()
        assert ("a", "b") in G.edges

    def test_node_attributes_preserved(self):
        node = _node("a", "Alpha", "doctype")
        node["custom_attr"] = "extra"
        G = build_from_extraction({"nodes": [node], "edges": []})
        assert G.nodes["a"]["label"] == "Alpha"
        assert G.nodes["a"]["custom_attr"] == "extra"

    def test_edge_attributes_preserved(self):
        edge = make_edge("a", "b", "calls", "/x", 7, confidence="INFERRED")
        G = build_from_extraction(
            {"nodes": [_node("a"), _node("b")], "edges": [edge]},
        )
        attrs = G["a"]["b"]
        assert attrs["relation"] == "calls"
        assert attrs["confidence"] == "INFERRED"
        assert attrs["source_location"] == "L7"

    def test_dangling_edges_dropped(self):
        edge = make_edge("a", "ghost", "calls", "/x", 1)
        G = build_from_extraction({"nodes": [_node("a")], "edges": [edge]})
        assert G.number_of_edges() == 0

    def test_id_normalization_repairs_edge(self):
        """Edge endpoint with non-canonical punctuation still resolves."""
        G = build_from_extraction({
            "nodes": [_node("sales_order_validate"), _node("b")],
            "edges": [
                {"source": "Sales-Order/Validate", "target": "b",
                 "relation": "calls"},
            ],
        })
        assert G.number_of_edges() == 1

    def test_missing_source_or_target_skipped(self):
        G = build_from_extraction({
            "nodes": [_node("a")],
            "edges": [{"target": "a", "relation": "x"}],
        })
        assert G.number_of_edges() == 0

    def test_node_without_id_skipped(self):
        G = build_from_extraction({
            "nodes": [{"label": "no-id"}],
            "edges": [],
        })
        assert G.number_of_nodes() == 0

    def test_src_tgt_preserved_for_direction(self):
        edge = make_edge("a", "b", "calls", "/x", 1)
        G = build_from_extraction(
            {"nodes": [_node("a"), _node("b")], "edges": [edge]},
        )
        attrs = G["a"]["b"]
        assert attrs["_src"] == "a"
        assert attrs["_tgt"] == "b"


class TestBuildMultipleExtractions:
    def test_merges_nodes_and_edges(self):
        ext1 = {"nodes": [_node("a")], "edges": []}
        ext2 = {"nodes": [_node("b")],
                "edges": [make_edge("a", "b", "calls", "/x", 1)]}
        G = build([ext1, ext2])
        assert set(G.nodes()) == {"a", "b"}
        assert G.number_of_edges() == 1

    def test_later_extraction_overwrites_node_attrs(self):
        ext1 = {"nodes": [_node("a", "first")], "edges": []}
        ext2 = {"nodes": [_node("a", "second")], "edges": []}
        G = build([ext1, ext2])
        assert G.nodes["a"]["label"] == "second"

    def test_edge_resolves_against_later_added_node(self):
        """Edges added in pass 2 see nodes added in any earlier pass."""
        ext1 = {"nodes": [_node("a")], "edges": []}
        ext2 = {
            "nodes": [_node("b")],
            "edges": [make_edge("a", "b", "calls", "/x", 1)],
        }
        G = build([ext1, ext2])
        assert G.number_of_edges() == 1


class TestBuildMerge:
    def test_creates_graph_when_none_exists(self, tmp_path: Path):
        out = tmp_path / "graph.json"
        ext = {"nodes": [_node("a"), _node("b")],
               "edges": [make_edge("a", "b", "calls", "/x", 1)]}
        G = build_merge([ext], graph_path=out)
        assert G.number_of_nodes() == 2

    def test_merges_into_existing_graph(self, tmp_path: Path):
        out = tmp_path / "graph.json"
        # Write an initial graph.json
        initial = {
            "nodes": [_node("a")],
            "edges": [],
        }
        out.write_text(json.dumps(initial), encoding="utf-8")
        new = {"nodes": [_node("b")],
               "edges": [make_edge("a", "b", "calls", "/x", 1)]}
        G = build_merge([new], graph_path=out)
        assert set(G.nodes()) == {"a", "b"}
        assert G.number_of_edges() == 1

    def test_prune_sources_removes_nodes(self, tmp_path: Path):
        out = tmp_path / "graph.json"
        node_a = make_node("a", "A", "code", "/old.py", 1, 1)
        node_b = make_node("b", "B", "code", "/keep.py", 1, 1)
        out.write_text(
            json.dumps({"nodes": [node_a, node_b], "edges": []}),
            encoding="utf-8",
        )
        G = build_merge(
            [{"nodes": [], "edges": []}],
            graph_path=out,
            prune_sources=["/old.py"],
        )
        assert "a" not in G.nodes()
        assert "b" in G.nodes()


class TestNetworkXSerialisation:
    """Round-trip through node_link_data so graph.json reads stay valid."""

    def test_round_trip(self):
        ext = {
            "nodes": [_node("a", "A"), _node("b", "B")],
            "edges": [make_edge("a", "b", "calls", "/x", 5)],
        }
        G = build_from_extraction(ext)
        data = nx.node_link_data(G, edges="edges")
        round_tripped = nx.node_link_graph(data, edges="edges")
        assert set(round_tripped.nodes()) == {"a", "b"}
        assert round_tripped.number_of_edges() == 1
