"""Tests for codemap.cluster — community detection and module labelling."""
from __future__ import annotations

from codemap.build import build
from codemap.cluster import cluster, cohesion_score, label_communities, score_all
from codemap.graph_primitives import make_edge, make_node


def _doctype(nid: str, label: str) -> dict:
    return make_node(nid, label, "doctype", "/x.json", 1, 1)


def _module(nid: str, label: str) -> dict:
    return make_node(nid, label, "module", "/m.txt", 1, 1)


class TestCluster:
    def test_empty_graph(self):
        G = build([{"nodes": [], "edges": []}])
        assert cluster(G) == {}

    def test_single_node(self):
        G = build([{"nodes": [_doctype("a", "A")], "edges": []}])
        communities = cluster(G)
        assert communities == {0: ["a"]}

    def test_isolated_nodes_each_get_own_community(self):
        G = build([{
            "nodes": [_doctype("a", "A"), _doctype("b", "B"), _doctype("c", "C")],
            "edges": [],
        }])
        communities = cluster(G)
        assert len(communities) == 3
        all_members = sum(communities.values(), [])
        assert sorted(all_members) == ["a", "b", "c"]

    def test_connected_pair_in_one_community(self):
        G = build([{
            "nodes": [_doctype("a", "A"), _doctype("b", "B")],
            "edges": [make_edge("a", "b", "links_to", "/x", 1)],
        }])
        communities = cluster(G)
        assert len(communities) == 1
        assert sorted(communities[0]) == ["a", "b"]

    def test_two_disjoint_clusters(self):
        nodes = [_doctype(f"n{i}", f"N{i}") for i in range(6)]
        edges = [
            make_edge("n0", "n1", "links_to", "/x", 1),
            make_edge("n1", "n2", "links_to", "/x", 1),
            make_edge("n0", "n2", "links_to", "/x", 1),
            make_edge("n3", "n4", "links_to", "/x", 1),
            make_edge("n4", "n5", "links_to", "/x", 1),
            make_edge("n3", "n5", "links_to", "/x", 1),
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        communities = cluster(G)
        assert len(communities) == 2

    def test_communities_sorted_by_size_desc(self):
        nodes = [_doctype(f"n{i}", f"N{i}") for i in range(7)]
        edges = [
            make_edge("n0", "n1", "links_to", "/x", 1),
            make_edge("n1", "n2", "links_to", "/x", 1),
            make_edge("n2", "n3", "links_to", "/x", 1),
            make_edge("n0", "n3", "links_to", "/x", 1),
            make_edge("n4", "n5", "links_to", "/x", 1),
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        communities = cluster(G)
        sizes = [len(members) for members in communities.values()]
        assert sizes == sorted(sizes, reverse=True)


class TestCohesionScore:
    def test_singleton_is_one(self):
        G = build([{"nodes": [_doctype("a", "A")], "edges": []}])
        assert cohesion_score(G, ["a"]) == 1.0

    def test_clique_is_one(self):
        nodes = [_doctype("a", "A"), _doctype("b", "B"), _doctype("c", "C")]
        edges = [
            make_edge("a", "b", "links_to", "/x", 1),
            make_edge("b", "c", "links_to", "/x", 1),
            make_edge("a", "c", "links_to", "/x", 1),
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        assert cohesion_score(G, ["a", "b", "c"]) == 1.0

    def test_no_edges_is_zero(self):
        nodes = [_doctype("a", "A"), _doctype("b", "B")]
        G = build([{"nodes": nodes, "edges": []}])
        assert cohesion_score(G, ["a", "b"]) == 0.0

    def test_score_all_returns_per_community(self):
        nodes = [_doctype("a", "A"), _doctype("b", "B")]
        G = build([{"nodes": nodes, "edges": [
            make_edge("a", "b", "links_to", "/x", 1),
        ]}])
        scores = score_all(G, {0: ["a", "b"]})
        assert scores == {0: 1.0}


class TestLabelCommunities:
    def test_majority_module_wins(self):
        nodes = [
            _module("selling", "Selling"),
            _doctype("so", "Sales Order"),
            _doctype("si", "Sales Invoice"),
            _doctype("cust", "Customer"),
        ]
        edges = [
            make_edge("so", "selling", "belongs_to_module", "/x", 1,
                      module="Selling"),
            make_edge("si", "selling", "belongs_to_module", "/x", 1,
                      module="Selling"),
            make_edge("cust", "selling", "belongs_to_module", "/x", 1,
                      module="Selling"),
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        communities = {0: ["so", "si", "cust", "selling"]}
        labels = label_communities(G, communities)
        assert labels[0] == "Selling"

    def test_no_module_dominance_falls_back(self):
        """50/50 split between two modules → fallback label."""
        nodes = [
            _module("a_mod", "A"), _module("b_mod", "B"),
            _doctype("a", "A1"), _doctype("b", "B1"),
        ]
        edges = [
            make_edge("a", "a_mod", "belongs_to_module", "/x", 1, module="A"),
            make_edge("b", "b_mod", "belongs_to_module", "/x", 1, module="B"),
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        labels = label_communities(G, {0: ["a", "b", "a_mod", "b_mod"]})
        assert labels[0].startswith("Community")

    def test_no_module_info_falls_back(self):
        nodes = [_doctype("a", "A"), _doctype("b", "B")]
        G = build([{"nodes": nodes, "edges": []}])
        labels = label_communities(G, {0: ["a", "b"]})
        assert labels[0].startswith("Community")

    def test_uses_module_attribute_over_label(self):
        """The ``module=`` edge attribute preserves human-readable casing."""
        nodes = [_module("stock_mgmt", "stock_mgmt"),  # ID-style label
                 _doctype("a", "A")]
        edges = [
            make_edge("a", "stock_mgmt", "belongs_to_module", "/x", 1,
                      module="Stock Management"),
        ]
        G = build([{"nodes": nodes, "edges": edges}])
        labels = label_communities(G, {0: ["a", "stock_mgmt"]})
        assert labels[0] == "Stock Management"
