"""SCSS / CSS stylesheet reference extraction.

The only relationship the graph tracks for stylesheets is the import
graph: ``a.scss`` includes ``b.scss`` includes ``c.scss``.  This is
enough for the triage agent to walk "which stylesheet ultimately styles
this print format?" without us implementing a full Sass tokeniser.

Both syntaxes are covered:

- ``@import "frappe/variables";``           (CSS-style, double quotes)
- ``@import 'desk/variables';``             (CSS-style, single quotes)
- ``@use "frappe/variables" as v;``         (Sass module system)
- ``@forward "common/typography";``         (Sass module system)

Comma-separated import lists like
``@import "a", "b";`` are common in plain CSS and are split into one
edge per file.
"""

from __future__ import annotations

import re
from pathlib import Path

from .graph_primitives import make_edge, make_id, make_node


# Match ``@import``, ``@use``, ``@forward`` followed by a comma-separated
# list of quoted paths.  We capture the directive name and the trailing
# slice of the line up to the terminating ``;`` so a second pass can
# pull every quoted path out.
_DIRECTIVE_PATTERN = re.compile(
    r"@(import|use|forward)\b([^;]*);",
    re.IGNORECASE,
)
_QUOTED_PATH_PATTERN = re.compile(r"['\"]([^'\"]+)['\"]")

# Lines starting with ``//`` are SCSS line comments; ``/* ... */`` are
# block comments.  We strip both before scanning so commented-out
# imports don't pollute the edge list.
_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def extract_style(path: Path) -> dict:
    """Emit a style file node and its ``applies_style`` import edges."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": str(exc), "nodes": [], "edges": []}

    str_path = str(path)
    line_count = max(1, source.count("\n") + 1)

    file_nid = make_id(str_path)
    file_node = make_node(
        file_nid, path.name, "style", str_path,
        1, line_count,
    )

    edges = list(_extract_imports(source, str_path, file_nid))

    return {"nodes": [file_node], "edges": edges}


def _strip_comments(source: str) -> str:
    """Remove ``//`` and ``/* ... */`` comments before pattern matching.

    Replacing comments with whitespace of the same length keeps every
    remaining offset valid, so line numbers stay accurate when we map
    a match offset back to a 1-indexed line.
    """
    def blank(match: re.Match) -> str:
        return " " * len(match.group(0))

    cleaned = _BLOCK_COMMENT.sub(blank, source)
    cleaned = _LINE_COMMENT.sub(blank, cleaned)
    return cleaned


def _extract_imports(source: str, str_path: str, file_nid: str):
    """Yield ``applies_style`` edges for every imported stylesheet."""
    cleaned = _strip_comments(source)
    seen: set[tuple[str, int]] = set()

    for directive in _DIRECTIVE_PATTERN.finditer(cleaned):
        kind = directive.group(1).lower()
        body = directive.group(2)
        directive_offset = directive.start()

        for path_match in _QUOTED_PATH_PATTERN.finditer(body):
            target = path_match.group(1).strip()
            if not target:
                continue
            line = source.count("\n", 0, directive_offset) + 1

            key = (target, line)
            if key in seen:
                continue
            seen.add(key)

            yield make_edge(
                file_nid,
                make_id(target),
                "applies_style",
                str_path,
                line,
                target_style=target,
                directive=kind,
            )
