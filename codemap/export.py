"""Graph export — JSON snapshot and interactive HTML visualisation.

Two writers live here:

- :func:`to_json` — dumps the NetworkX graph in ``node_link_data``
  shape with each node tagged by its community ID and label, and a
  shrink guard that refuses to overwrite a larger existing file
  unless the caller passes ``force=True``.
- :func:`to_html` — renders an interactive vis.js page with
  community colouring, Frappe-aware node shapes and a confidence
  filter so reviewers can hide INFERRED / AMBIGUOUS edges to focus on
  the certain core.

All user-controllable strings (labels, source paths, relations) are
HTML-escaped before they reach the page, and embedded JSON is
sanitised so a stray ``</script>`` in a label can't break out of the
script tag.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

import networkx as nx
from networkx.readwrite import json_graph


# ── Visual config ─────────────────────────────────────────────────────────

# Twelve-colour palette modelled on Tableau 10 + a few extras.  Index by
# ``community_id % len(palette)`` so colour assignment is deterministic.
_COMMUNITY_COLORS: tuple[str, ...] = (
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2",
    "#59A14F", "#EDC948", "#B07AA1", "#FF9DA7",
    "#9C755F", "#BAB0AC", "#1f77b4", "#ff7f0e",
)

# Map our schema's ``file_type`` to a vis.js node shape.  The shapes
# match the legend the report references so visual cues line up with
# the markdown.  Anything unmapped falls back to ``"dot"``.
_SHAPE_BY_FILE_TYPE: dict[str, str] = {
    "doctype": "diamond",
    "role": "square",
    "hook": "star",
    "workflow": "box",
    "workflow_state": "box",
    "module": "hexagon",
    "external": "triangle",
    "notification": "triangleDown",
}

# Maximum nodes we will render in HTML.  Beyond this, vis.js physics
# becomes unusable on a laptop and the layout never settles.
_MAX_NODES_FOR_VIZ: int = 5000


# ── JSON writer ───────────────────────────────────────────────────────────


def to_json(
    G: nx.Graph,
    communities: dict[int, list[str]],
    community_labels: dict[int, str] | None,
    output_path: str | Path,
    *,
    force: bool = False,
) -> None:
    """Write *G* to *output_path* as ``node_link_data`` JSON.

    The shrink guard refuses to overwrite an existing file whose node
    count exceeds the new graph's, unless ``force=True`` — this catches
    pipelines where a partial extraction would otherwise stomp a good
    snapshot.
    """
    output_path = Path(output_path)

    if not force and output_path.exists():
        if _existing_nodes(output_path) > G.number_of_nodes():
            print(
                f"[codemap] WARNING: refusing to overwrite "
                f"{output_path} — existing graph has more nodes than "
                f"the new one.  Pass force=True to override.",
                file=sys.stderr,
            )
            return

    node_to_community = _invert_communities(communities)
    labels = community_labels or {}

    # ``edges="edges"`` keeps NetworkX's modern key (vs the older
    # ``"links"``).  Older readers can still cope — we re-map both
    # forms when loading.
    data = json_graph.node_link_data(G, edges="edges")

    for node in data["nodes"]:
        cid = node_to_community.get(node["id"])
        node["community"] = cid
        node["community_label"] = labels.get(cid, "") if cid is not None else ""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _existing_nodes(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return len(data.get("nodes", []))
    except Exception:
        return 0


# ── HTML writer ───────────────────────────────────────────────────────────


def to_html(
    G: nx.Graph,
    communities: dict[int, list[str]],
    community_labels: dict[int, str] | None,
    output_path: str | Path,
) -> None:
    """Render an interactive vis.js page to *output_path*.

    Raises :class:`ValueError` for graphs above ``_MAX_NODES_FOR_VIZ``;
    the report still includes a graph.json link so reviewers can use
    a desktop tool for very large graphs.
    """
    if G.number_of_nodes() > _MAX_NODES_FOR_VIZ:
        raise ValueError(
            f"Graph has {G.number_of_nodes()} nodes — too large for "
            f"HTML viz (limit {_MAX_NODES_FOR_VIZ})."
        )

    node_to_community = _invert_communities(communities)
    labels = community_labels or {}

    vis_nodes = _build_vis_nodes(G, node_to_community, labels)
    vis_edges = _build_vis_edges(G)
    legend = _build_legend(communities, labels)

    page = _render_page(
        title=html.escape(str(output_path)),
        nodes_json=_safe_json(vis_nodes),
        edges_json=_safe_json(vis_edges),
        legend_json=_safe_json(legend),
        stats_html=(
            f"{G.number_of_nodes()} nodes &middot; "
            f"{G.number_of_edges()} edges &middot; "
            f"{len(communities)} communities"
        ),
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(page, encoding="utf-8")


# ── HTML helpers ──────────────────────────────────────────────────────────


def _build_vis_nodes(
    G: nx.Graph,
    node_to_community: dict[str, int],
    labels: dict[int, str],
) -> list[dict]:
    """Convert NetworkX nodes into vis.js node descriptors."""
    degree = dict(G.degree())
    max_deg = max(degree.values(), default=1) or 1

    out: list[dict] = []
    for node_id, attrs in G.nodes(data=True):
        cid = node_to_community.get(node_id, 0)
        color = _COMMUNITY_COLORS[cid % len(_COMMUNITY_COLORS)]
        file_type = attrs.get("file_type", "")
        deg = degree.get(node_id, 1)
        size = 10 + 30 * (deg / max_deg)

        out.append({
            "id": node_id,
            "label": str(attrs.get("label", node_id)),
            "shape": _SHAPE_BY_FILE_TYPE.get(file_type, "dot"),
            "color": {
                "background": color,
                "border": color,
                "highlight": {"background": "#ffffff", "border": color},
            },
            "size": round(size, 1),
            "font": {"size": 12, "color": "#ffffff"},
            "community": cid,
            "community_label": labels.get(cid, ""),
            "file_type": file_type,
            "source_file": str(attrs.get("source_file", "") or ""),
            "degree": deg,
        })
    return out


def _build_vis_edges(G: nx.Graph) -> list[dict]:
    """Convert NetworkX edges into vis.js edge descriptors."""
    out: list[dict] = []
    for u, v, attrs in G.edges(data=True):
        confidence = attrs.get("confidence", "EXTRACTED")
        relation = attrs.get("relation", "")
        out.append({
            "from": attrs.get("_src", u),
            "to": attrs.get("_tgt", v),
            "label": relation,
            "title": f"{relation} [{confidence}]",
            "dashes": confidence != "EXTRACTED",
            "width": 2 if confidence == "EXTRACTED" else 1,
            "confidence": confidence,
        })
    return out


def _build_legend(
    communities: dict[int, list[str]],
    labels: dict[int, str],
) -> list[dict]:
    """Sidebar legend — one entry per community, sorted by ID."""
    return [
        {
            "cid": cid,
            "color": _COMMUNITY_COLORS[cid % len(_COMMUNITY_COLORS)],
            "label": labels.get(cid, f"Community {cid}"),
            "count": len(communities[cid]),
        }
        for cid in sorted(communities)
    ]


def _safe_json(value) -> str:
    """JSON-encode for embedding in a ``<script>`` block.

    Replacing ``</`` with ``<\\/`` defangs any user-supplied label
    that contains the literal ``</script>`` — without this, a malicious
    or accidental string would break out of the script tag.
    """
    return json.dumps(value).replace("</", "<\\/")


def _invert_communities(
    communities: dict[int, list[str]],
) -> dict[str, int]:
    return {n: cid for cid, members in communities.items() for n in members}


# ── HTML page template ────────────────────────────────────────────────────


_PAGE_STYLES = """<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f0f1a; color: #e0e0e0;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         display: flex; height: 100vh; overflow: hidden; }
  #graph { flex: 1; }
  #sidebar { width: 300px; background: #1a1a2e;
             border-left: 1px solid #2a2a4e;
             display: flex; flex-direction: column; overflow: hidden; }
  #search-wrap { padding: 12px; border-bottom: 1px solid #2a2a4e; }
  #search { width: 100%; background: #0f0f1a; border: 1px solid #3a3a5e;
            color: #e0e0e0; padding: 7px 10px; border-radius: 6px;
            font-size: 13px; outline: none; }
  #search:focus { border-color: #4E79A7; }
  #info-panel { padding: 14px; border-bottom: 1px solid #2a2a4e;
                min-height: 140px; }
  #info-panel h3, #legend-wrap h3, #filter-wrap h3 {
    font-size: 13px; color: #aaa; margin-bottom: 8px;
    text-transform: uppercase; letter-spacing: 0.05em; }
  #info-content { font-size: 13px; color: #ccc; line-height: 1.6; }
  .field { margin-bottom: 5px; }
  .field b { color: #e0e0e0; }
  .empty { color: #555; font-style: italic; }
  #filter-wrap { padding: 12px; border-bottom: 1px solid #2a2a4e; }
  .filter-row { display: flex; align-items: center; gap: 8px;
                padding: 4px 0; font-size: 12px; cursor: pointer; }
  .filter-row input { cursor: pointer; }
  #legend-wrap { flex: 1; overflow-y: auto; padding: 12px; }
  .legend-item { display: flex; align-items: center; gap: 8px;
                 padding: 4px 0; cursor: pointer; border-radius: 4px;
                 font-size: 12px; }
  .legend-item:hover { background: #2a2a4e; padding-left: 4px; }
  .legend-item.dimmed { opacity: 0.35; }
  .legend-dot { width: 12px; height: 12px; border-radius: 50%;
                flex-shrink: 0; }
  .legend-label { flex: 1; overflow: hidden; text-overflow: ellipsis;
                  white-space: nowrap; }
  .legend-count { color: #666; font-size: 11px; }
  #stats { padding: 10px 14px; border-top: 1px solid #2a2a4e;
           font-size: 11px; color: #555; }
</style>"""


_PAGE_SCRIPT = """<script>
  const RAW_NODES = __NODES__;
  const RAW_EDGES = __EDGES__;
  const LEGEND = __LEGEND__;

  const nodes = new vis.DataSet(RAW_NODES);
  const edges = new vis.DataSet(RAW_EDGES);
  const network = new vis.Network(
    document.getElementById("graph"),
    { nodes, edges },
    {
      physics: { stabilization: { iterations: 200 } },
      interaction: { hover: true, tooltipDelay: 200 },
      edges: { smooth: { type: "continuous" }, arrows: { to: { enabled: true, scaleFactor: 0.5 } } },
    },
  );

  function setText(el, text) { el.textContent = text; }

  // Click to inspect.
  network.on("click", function (params) {
    const panel = document.getElementById("info-content");
    if (!params.nodes.length) {
      panel.innerHTML = '<span class="empty">Click a node to inspect it</span>';
      return;
    }
    const node = nodes.get(params.nodes[0]);
    panel.innerHTML = "";
    [
      ["label", node.label],
      ["type", node.file_type || "—"],
      ["community", node.community_label || ("#" + node.community)],
      ["source", node.source_file || "—"],
      ["degree", String(node.degree)],
    ].forEach(([k, v]) => {
      const row = document.createElement("div");
      row.className = "field";
      const key = document.createElement("b");
      setText(key, k + ": ");
      row.appendChild(key);
      row.appendChild(document.createTextNode(v));
      panel.appendChild(row);
    });
  });

  // Search.
  document.getElementById("search").addEventListener("input", function (e) {
    const term = e.target.value.toLowerCase();
    if (!term) { network.unselectAll(); return; }
    const matches = RAW_NODES
      .filter(n => n.label.toLowerCase().includes(term))
      .map(n => n.id);
    if (matches.length) {
      network.selectNodes(matches);
      network.focus(matches[0], { scale: 1.2, animation: true });
    }
  });

  // Community legend with click-to-dim.
  const legendEl = document.getElementById("legend");
  const dimmed = new Set();
  LEGEND.forEach(item => {
    const row = document.createElement("div");
    row.className = "legend-item";
    row.dataset.cid = item.cid;
    const dot = document.createElement("span");
    dot.className = "legend-dot";
    dot.style.background = item.color;
    const lbl = document.createElement("span");
    lbl.className = "legend-label";
    setText(lbl, item.label);
    const cnt = document.createElement("span");
    cnt.className = "legend-count";
    setText(cnt, String(item.count));
    row.appendChild(dot);
    row.appendChild(lbl);
    row.appendChild(cnt);
    row.addEventListener("click", () => toggleCommunity(item.cid, row));
    legendEl.appendChild(row);
  });

  function toggleCommunity(cid, row) {
    if (dimmed.has(cid)) {
      dimmed.delete(cid);
      row.classList.remove("dimmed");
    } else {
      dimmed.add(cid);
      row.classList.add("dimmed");
    }
    nodes.update(RAW_NODES.map(n => ({
      id: n.id,
      hidden: dimmed.has(n.community),
    })));
  }

  // Confidence filter.
  const confidenceFilters = { EXTRACTED: true, INFERRED: true, AMBIGUOUS: true };
  document.querySelectorAll("input[data-confidence]").forEach(cb => {
    cb.addEventListener("change", () => {
      confidenceFilters[cb.dataset.confidence] = cb.checked;
      edges.update(RAW_EDGES.map(e => ({
        id: e.id !== undefined ? e.id : (e.from + "->" + e.to + ":" + e.label),
        hidden: !confidenceFilters[e.confidence],
      })));
    });
  });
</script>"""


def _render_page(
    *,
    title: str,
    nodes_json: str,
    edges_json: str,
    legend_json: str,
    stats_html: str,
) -> str:
    """Compose the final HTML.  All inputs must already be safe."""
    script = (
        _PAGE_SCRIPT
        .replace("__NODES__", nodes_json)
        .replace("__EDGES__", edges_json)
        .replace("__LEGEND__", legend_json)
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>codemap — {title}</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
{_PAGE_STYLES}
</head>
<body>
<div id="graph"></div>
<div id="sidebar">
  <div id="search-wrap">
    <input id="search" type="text" placeholder="Search nodes…" autocomplete="off">
  </div>
  <div id="info-panel">
    <h3>Node Info</h3>
    <div id="info-content"><span class="empty">Click a node to inspect it</span></div>
  </div>
  <div id="filter-wrap">
    <h3>Confidence</h3>
    <label class="filter-row">
      <input type="checkbox" data-confidence="EXTRACTED" checked> EXTRACTED
    </label>
    <label class="filter-row">
      <input type="checkbox" data-confidence="INFERRED" checked> INFERRED
    </label>
    <label class="filter-row">
      <input type="checkbox" data-confidence="AMBIGUOUS" checked> AMBIGUOUS
    </label>
  </div>
  <div id="legend-wrap">
    <h3>Communities</h3>
    <div id="legend"></div>
  </div>
  <div id="stats">{stats_html}</div>
</div>
{script}
</body>
</html>"""
