"""Tests for codemap.detect — file discovery and Frappe-aware classification."""
from pathlib import Path

from codemap.detect import classify_file, detect
from codemap.filetype import FileType

# Resolve the fixture path once
FIXTURE_APP = Path(__file__).parent / "fixtures" / "sample_app"


class TestClassifyFile:
    """Tests for individual file classification."""

    def test_doctype_json_detected(self):
        path = FIXTURE_APP / "test_app/selling/doctype/sales_order/sales_order.json"
        assert classify_file(path, FIXTURE_APP) == FileType.DOCTYPE_JSON

    def test_random_json_not_detected_as_doctype(self):
        """A JSON file that isn't in doctype/ path should return None."""
        # report JSON has its own type, but a random JSON should be None
        path = FIXTURE_APP / "test_app" / "some_config.json"
        assert classify_file(path, FIXTURE_APP) is None

    def test_report_json_detected(self):
        path = FIXTURE_APP / "test_app/selling/report/sales_analytics/sales_analytics.json"
        assert classify_file(path, FIXTURE_APP) == FileType.REPORT_JSON

    def test_hooks_py_detected(self):
        path = FIXTURE_APP / "test_app/hooks.py"
        assert classify_file(path, FIXTURE_APP) == FileType.HOOKS

    def test_nested_hooks_not_detected(self):
        """A hooks.py that isn't at the app package level should be CODE_PY."""
        path = FIXTURE_APP / "test_app/selling/doctype/sales_order/hooks.py"
        # Not at the right depth — should be CODE_PY, not HOOKS
        result = classify_file(path, FIXTURE_APP)
        assert result != FileType.HOOKS

    def test_dashboard_detected(self):
        path = FIXTURE_APP / "test_app/selling/doctype/sales_order/sales_order_dashboard.py"
        assert classify_file(path, FIXTURE_APP) == FileType.DASHBOARD

    def test_modules_txt_detected(self):
        path = FIXTURE_APP / "test_app/modules.txt"
        assert classify_file(path, FIXTURE_APP) == FileType.MODULES_TXT

    def test_python_controller_detected(self):
        path = FIXTURE_APP / "test_app/selling/doctype/sales_order/sales_order.py"
        assert classify_file(path, FIXTURE_APP) == FileType.CODE_PY

    def test_shared_controller_detected(self):
        path = FIXTURE_APP / "test_app/controllers/selling_controller.py"
        assert classify_file(path, FIXTURE_APP) == FileType.CODE_PY

    def test_js_detected(self):
        path = FIXTURE_APP / "test_app/selling/doctype/sales_order/sales_order.js"
        assert classify_file(path, FIXTURE_APP) == FileType.CODE_JS

    def test_vue_detected(self):
        path = FIXTURE_APP / "frontend/src/App.vue"
        assert classify_file(path, FIXTURE_APP) == FileType.CODE_VUE

    def test_markdown_detected(self):
        path = FIXTURE_APP / "README.md"
        assert classify_file(path, FIXTURE_APP) == FileType.DOCUMENT

    def test_empty_init_skipped(self):
        path = FIXTURE_APP / "test_app/selling/__init__.py"
        assert classify_file(path, FIXTURE_APP) is None

    def test_nonempty_init_detected(self):
        path = FIXTURE_APP / "test_app/__init__.py"
        assert classify_file(path, FIXTURE_APP) == FileType.CODE_PY

    def test_workflow_json_detected(self):
        path = FIXTURE_APP / "test_app/selling/workflow/sales_order_approval/sales_order_approval.json"
        assert classify_file(path, FIXTURE_APP) == FileType.WORKFLOW_JSON

    def test_notification_json_detected(self):
        path = FIXTURE_APP / "test_app/selling/notification/order_submitted/order_submitted.json"
        assert classify_file(path, FIXTURE_APP) == FileType.NOTIFICATION_JSON

    def test_print_format_json_detected(self):
        path = FIXTURE_APP / "test_app/selling/print_format/sales_order_classic/sales_order_classic.json"
        assert classify_file(path, FIXTURE_APP) == FileType.PRINT_FORMAT_JSON

    def test_custom_field_fixture_detected(self):
        path = FIXTURE_APP / "test_app/fixtures/custom_field.json"
        assert classify_file(path, FIXTURE_APP) == FileType.CUSTOM_FIELD_JSON

    def test_property_setter_fixture_detected(self):
        path = FIXTURE_APP / "test_app/fixtures/property_setter.json"
        assert classify_file(path, FIXTURE_APP) == FileType.PROPERTY_SETTER_JSON

    def test_patches_txt_detected(self):
        path = FIXTURE_APP / "test_app/patches.txt"
        assert classify_file(path, FIXTURE_APP) == FileType.PATCHES_TXT

    def test_bundle_js_detected(self):
        path = FIXTURE_APP / "test_app/public/js/test_app.bundle.js"
        assert classify_file(path, FIXTURE_APP) == FileType.BUNDLE_JS

    def test_template_html_detected(self):
        path = FIXTURE_APP / "test_app/templates/pages/order.html"
        assert classify_file(path, FIXTURE_APP) == FileType.TEMPLATE_HTML

    def test_style_scss_detected(self):
        path = FIXTURE_APP / "test_app/public/scss/main.scss"
        assert classify_file(path, FIXTURE_APP) == FileType.STYLE_SCSS


class TestDetect:
    """Integration tests for the full detect() function."""

    def test_full_detection(self):
        result = detect(FIXTURE_APP)
        files = result["files"]

        # DocType JSON
        doctype_jsons = files[FileType.DOCTYPE_JSON.value]
        assert len(doctype_jsons) == 1
        assert any("sales_order.json" in f for f in doctype_jsons)

        # Report JSON
        report_jsons = files[FileType.REPORT_JSON.value]
        assert len(report_jsons) == 1
        assert any("sales_analytics.json" in f for f in report_jsons)

        # Hooks
        hooks = files[FileType.HOOKS.value]
        assert len(hooks) == 1
        assert any("hooks.py" in f for f in hooks)

        # Dashboard
        dashboards = files[FileType.DASHBOARD.value]
        assert len(dashboards) == 1
        assert any("sales_order_dashboard.py" in f for f in dashboards)

        # Modules.txt
        modules = files[FileType.MODULES_TXT.value]
        assert len(modules) == 1

        # Python files (controller + shared controller + report.py + __init__.py with content)
        py_files = files[FileType.CODE_PY.value]
        py_basenames = {Path(f).name for f in py_files}
        assert "sales_order.py" in py_basenames
        assert "selling_controller.py" in py_basenames
        assert "sales_analytics.py" in py_basenames
        assert "__init__.py" in py_basenames  # The non-empty one

        # JS files
        js_files = files[FileType.CODE_JS.value]
        assert len(js_files) == 1
        assert any("sales_order.js" in f for f in js_files)

        # Vue files
        vue_files = files[FileType.CODE_VUE.value]
        assert len(vue_files) == 1
        assert any("App.vue" in f for f in vue_files)

        # Documents
        docs = files[FileType.DOCUMENT.value]
        assert any("README.md" in f for f in docs)

    def test_sensitive_files_skipped(self):
        result = detect(FIXTURE_APP)
        assert len(result["skipped_sensitive"]) >= 1
        assert any("credentials" in f for f in result["skipped_sensitive"])

    def test_codemapignore_patterns_loaded(self):
        result = detect(FIXTURE_APP)
        assert result["codemapignore_patterns"] >= 1

    def test_codemapignore_excludes_test_files(self):
        """test_sales_order.py should be excluded by .codemapignore."""
        result = detect(FIXTURE_APP)
        all_files = []
        for file_list in result["files"].values():
            all_files.extend(file_list)
        assert not any("test_sales_order.py" in f for f in all_files)

    def test_total_files_count(self):
        result = detect(FIXTURE_APP)
        total = sum(len(v) for v in result["files"].values())
        assert result["total_files"] == total
        assert total > 0
