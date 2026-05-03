"""Tests for codemap.extract_vue — Vue SFC extraction."""
from pathlib import Path

from codemap.extract_vue import extract_vue

FIXTURE_APP = Path(__file__).parent / "fixtures" / "sample_app"
APP_VUE = FIXTURE_APP / "frontend" / "src" / "App.vue"


class TestVueExtraction:
    """Tests for Vue SFC file extraction."""

    def test_file_node_created(self):
        result = extract_vue(APP_VUE)
        file_nodes = [n for n in result["nodes"] if n["file_type"] == "file"]
        assert len(file_nodes) == 1
        assert file_nodes[0]["label"] == "App.vue"

    def test_no_error(self):
        result = extract_vue(APP_VUE)
        assert "error" not in result

    def test_imports_extracted(self):
        """import from 'vue' and import from '@/components/...' should produce edges."""
        result = extract_vue(APP_VUE)
        import_edges = [e for e in result["edges"] if e["relation"] == "imports_from"]
        assert len(import_edges) >= 2

    def test_function_extracted(self):
        """getActiveEmployees() function should be extracted."""
        result = extract_vue(APP_VUE)
        func_nodes = [
            n for n in result["nodes"]
            if "getActiveEmployees" in n["label"]
        ]
        assert len(func_nodes) >= 1

    def test_source_lines_offset(self):
        """Line numbers should be offset by the <template> block lines."""
        result = extract_vue(APP_VUE)
        # The <script setup> block starts after the template block
        # So all line numbers should be > 1
        for node in result["nodes"]:
            if node["file_type"] != "file" and node["source_line_start"] > 0:
                # Script starts around line 9, so extracted nodes should be > 7
                assert node["source_line_start"] > 1

    def test_source_file_points_to_vue(self):
        """All nodes should reference the .vue file, not a temp .js file."""
        result = extract_vue(APP_VUE)
        for node in result["nodes"]:
            assert node["source_file"].endswith(".vue")

    def test_edges_reference_vue_path(self):
        """All edges should reference the .vue file path."""
        result = extract_vue(APP_VUE)
        for edge in result["edges"]:
            assert edge["source_file"].endswith(".vue")


class TestVueNoScript:
    """Test Vue files without a script block."""

    def _extract_snippet(self, content: str) -> dict:
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".vue", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            f.flush()
            return extract_vue(Path(f.name))

    def test_template_only_vue(self):
        """A Vue file with no <script> block should not crash."""
        result = self._extract_snippet("<template><div>Hello</div></template>")
        assert len(result["nodes"]) == 1  # Just the file node
        assert result["nodes"][0]["file_type"] == "file"

    def test_script_setup_block(self):
        """<script setup> should be parsed correctly."""
        result = self._extract_snippet('''
<template><div>Test</div></template>
<script setup>
import { ref } from "vue"
const count = ref(0)
</script>
''')
        import_edges = [e for e in result["edges"] if e["relation"] == "imports_from"]
        assert len(import_edges) >= 1
