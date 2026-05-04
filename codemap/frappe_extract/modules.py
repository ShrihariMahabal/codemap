"""modules.txt extractor (Phase 4d).

Each non-blank, non-comment line in ``modules.txt`` is a module name.
We emit one ``module`` node per unique line.

The DocType-to-Module edges (``belongs_to_module``) come from the DocType
extractor reading the ``"module"`` field of each DocType JSON, so this
extractor is intentionally edge-free.
"""

from __future__ import annotations

from pathlib import Path

from ..graph_primitives import make_id, make_node
from ._common import empty_result


def extract_modules(path: Path) -> dict:
    """Extract module nodes from a modules.txt file.

    Lines starting with ``#`` and blank lines are ignored.  Duplicate
    module names produce a single node — the first occurrence wins.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return empty_result()

    str_path = str(path)
    nodes: list[dict] = []
    seen: set[str] = set()

    for lineno, raw in enumerate(text.splitlines(), start=1):
        name = raw.strip()
        if not name or name.startswith("#"):
            continue
        nid = make_id(name)
        if nid in seen:
            continue
        seen.add(nid)
        nodes.append(make_node(
            nid, name, "module", str_path,
            lineno, lineno,
        ))

    return {"nodes": nodes, "edges": []}
