"""Tests for codemap.extract_template — Jinja template extraction."""
from __future__ import annotations

import tempfile
from pathlib import Path

from codemap.extract_template import extract_template

FIXTURE_APP = Path(__file__).parent / "fixtures" / "sample_app"
ORDER_HTML = FIXTURE_APP / "test_app" / "templates" / "pages" / "order.html"


def _extract_snippet(content: str) -> dict:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8",
    ) as f:
        f.write(content)
        f.flush()
        return extract_template(Path(f.name))


class TestFileNode:
    """The file node is always emitted, even for empty templates."""

    def test_file_node_created(self):
        result = extract_template(ORDER_HTML)
        files = [n for n in result["nodes"] if n["file_type"] == "template"]
        assert len(files) == 1
        assert files[0]["label"] == "order.html"

    def test_no_error(self):
        result = extract_template(ORDER_HTML)
        assert "error" not in result

    def test_empty_template(self):
        result = _extract_snippet("")
        assert len(result["nodes"]) == 1
        assert result["edges"] == []
        assert result["raw_calls"] == []


class TestRendersTemplate:
    """{% extends/include/import %} produce renders_template edges."""

    def test_extends(self):
        result = _extract_snippet(
            '{% extends "templates/web.html" %}\n<h1>hi</h1>',
        )
        edges = [e for e in result["edges"] if e["relation"] == "renders_template"]
        assert len(edges) == 1
        assert edges[0]["target_template"] == "templates/web.html"
        assert edges[0]["confidence"] == "EXTRACTED"

    def test_include(self):
        result = _extract_snippet(
            '{% include "templates/includes/header.html" %}',
        )
        edges = [e for e in result["edges"] if e["relation"] == "renders_template"]
        assert len(edges) == 1
        assert edges[0]["target_template"] == "templates/includes/header.html"

    def test_import_macro(self):
        result = _extract_snippet(
            '{% import "templates/macros.html" as macros %}',
        )
        edges = [e for e in result["edges"] if e["relation"] == "renders_template"]
        assert len(edges) == 1
        assert edges[0]["target_template"] == "templates/macros.html"

    def test_from_import(self):
        result = _extract_snippet(
            '{% from "templates/macros.html" import row %}',
        )
        edges = [e for e in result["edges"] if e["relation"] == "renders_template"]
        assert len(edges) == 1
        assert edges[0]["target_template"] == "templates/macros.html"

    def test_single_quotes(self):
        result = _extract_snippet("{% extends 'templates/web.html' %}")
        edges = [e for e in result["edges"] if e["relation"] == "renders_template"]
        assert len(edges) == 1

    def test_whitespace_trim_dash(self):
        """Jinja's whitespace-trimming form ``{%-`` is recognised too."""
        result = _extract_snippet('{%- extends "templates/web.html" -%}')
        edges = [e for e in result["edges"] if e["relation"] == "renders_template"]
        assert len(edges) == 1

    def test_multiple_includes(self):
        result = _extract_snippet('''
            {% extends "templates/web.html" %}
            {% include "templates/header.html" %}
            {% include "templates/footer.html" %}
        ''')
        edges = [e for e in result["edges"] if e["relation"] == "renders_template"]
        targets = {e["target_template"] for e in edges}
        assert targets == {
            "templates/web.html",
            "templates/header.html",
            "templates/footer.html",
        }

    def test_dedupes_same_path(self):
        """Importing the same template twice produces one edge."""
        result = _extract_snippet('''
            {% include "templates/x.html" %}
            {% include "templates/x.html" %}
        ''')
        edges = [e for e in result["edges"] if e["relation"] == "renders_template"]
        assert len(edges) == 1

    def test_line_numbers(self):
        result = _extract_snippet(
            'line1\nline2\n{% include "x.html" %}\nline4',
        )
        edges = [e for e in result["edges"] if e["relation"] == "renders_template"]
        assert edges[0]["source_location"] == "L3"


class TestEmbeddedCallables:
    """{{ frappe.utils.foo() }} produces raw_calls for cross-file resolution."""

    def test_dotted_callable(self):
        result = _extract_snippet(
            "{{ frappe.utils.fmt_money(amount) }}",
        )
        callees = {c["callee"] for c in result["raw_calls"]}
        assert "frappe.utils.fmt_money" in callees

    def test_bare_identifier_ignored(self):
        """Single-name calls like ``foo()`` are too noisy to emit."""
        result = _extract_snippet("{{ foo(x) }}")
        assert result["raw_calls"] == []

    def test_field_access_ignored(self):
        """``{{ doc.name }}`` is a field reference, not a call — skip."""
        result = _extract_snippet("{{ doc.name }}")
        assert result["raw_calls"] == []

    def test_callable_in_statement_block_ignored(self):
        """Calls inside ``{% if frappe.x() %}`` are control flow, skip."""
        result = _extract_snippet("{% if frappe.utils.cint(x) %}{% endif %}")
        assert result["raw_calls"] == []

    def test_multiple_calls(self):
        result = _extract_snippet('''
            {{ frappe.utils.fmt_money(x) }}
            {{ frappe.format(y) }}
        ''')
        callees = {c["callee"] for c in result["raw_calls"]}
        assert "frappe.utils.fmt_money" in callees
        assert "frappe.format" in callees

    def test_caller_is_file_node(self):
        result = _extract_snippet("{{ frappe.utils.foo() }}")
        file_node = next(n for n in result["nodes"] if n["file_type"] == "template")
        assert result["raw_calls"][0]["caller_nid"] == file_node["id"]


class TestSampleFixture:
    """Smoke test against the fixture template used by other tests."""

    def test_order_html(self):
        result = extract_template(ORDER_HTML)
        assert "error" not in result
        edges = [e for e in result["edges"] if e["relation"] == "renders_template"]
        assert any(e["target_template"] == "templates/base.html" for e in edges)
