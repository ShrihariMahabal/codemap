"""Tests for codemap.extract_style — SCSS/CSS import extraction."""
from __future__ import annotations

import tempfile
from pathlib import Path

from codemap.extract_style import extract_style

FIXTURE_APP = Path(__file__).parent / "fixtures" / "sample_app"
MAIN_SCSS = FIXTURE_APP / "test_app" / "public" / "scss" / "main.scss"


def _extract_snippet(content: str) -> dict:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".scss", delete=False, encoding="utf-8",
    ) as f:
        f.write(content)
        f.flush()
        return extract_style(Path(f.name))


class TestFileNode:
    """A style file node is always emitted."""

    def test_file_node_created(self):
        result = extract_style(MAIN_SCSS)
        nodes = [n for n in result["nodes"] if n["file_type"] == "style"]
        assert len(nodes) == 1
        assert nodes[0]["label"] == "main.scss"

    def test_no_error(self):
        result = extract_style(MAIN_SCSS)
        assert "error" not in result

    def test_empty_file(self):
        result = _extract_snippet("")
        assert len(result["nodes"]) == 1
        assert result["edges"] == []


class TestAppliesStyleEdges:
    """@import / @use / @forward produce applies_style edges."""

    def test_import_double_quotes(self):
        result = _extract_snippet('@import "frappe/variables";')
        edges = [e for e in result["edges"] if e["relation"] == "applies_style"]
        assert len(edges) == 1
        assert edges[0]["target_style"] == "frappe/variables"

    def test_import_single_quotes(self):
        result = _extract_snippet("@import 'frappe/variables';")
        edges = [e for e in result["edges"] if e["relation"] == "applies_style"]
        assert len(edges) == 1

    def test_use_directive(self):
        result = _extract_snippet('@use "frappe/colors" as c;')
        edges = [e for e in result["edges"] if e["relation"] == "applies_style"]
        assert len(edges) == 1
        assert edges[0]["directive"] == "use"

    def test_forward_directive(self):
        result = _extract_snippet('@forward "common/typography";')
        edges = [e for e in result["edges"] if e["relation"] == "applies_style"]
        assert len(edges) == 1
        assert edges[0]["directive"] == "forward"

    def test_comma_separated_imports(self):
        result = _extract_snippet('@import "a", "b", "c";')
        edges = [e for e in result["edges"] if e["relation"] == "applies_style"]
        assert len(edges) == 3
        targets = {e["target_style"] for e in edges}
        assert targets == {"a", "b", "c"}

    def test_multiple_statements(self):
        result = _extract_snippet('@import "a";\n@use "b";\n@forward "c";')
        edges = [e for e in result["edges"] if e["relation"] == "applies_style"]
        assert len(edges) == 3

    def test_line_comment_ignored(self):
        result = _extract_snippet('// @import "commented-out";\n@import "real";')
        edges = [e for e in result["edges"] if e["relation"] == "applies_style"]
        assert len(edges) == 1
        assert edges[0]["target_style"] == "real"

    def test_block_comment_ignored(self):
        result = _extract_snippet('/* @import "no"; */\n@import "yes";')
        edges = [e for e in result["edges"] if e["relation"] == "applies_style"]
        assert len(edges) == 1
        assert edges[0]["target_style"] == "yes"


class TestSampleFixture:
    """Smoke test against the fixture stylesheet."""

    def test_main_scss(self):
        result = extract_style(MAIN_SCSS)
        edges = [e for e in result["edges"] if e["relation"] == "applies_style"]
        targets = {e["target_style"] for e in edges}
        assert "./variables" in targets
