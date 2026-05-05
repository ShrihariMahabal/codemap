"""Tests for codemap.extract_js — JavaScript AST extraction via tree-sitter."""
from pathlib import Path

from codemap.extract_js import extract_js

FIXTURE_APP = Path(__file__).parent / "fixtures" / "sample_app"
SALES_ORDER_JS = (
    FIXTURE_APP / "test_app" / "selling" / "doctype" / "sales_order" / "sales_order.js"
)


class TestJSExtraction:
    """Tests for single-file JavaScript extraction."""

    def test_file_node_created(self):
        result = extract_js(SALES_ORDER_JS)
        file_nodes = [n for n in result["nodes"] if n["file_type"] == "file"]
        assert len(file_nodes) == 1
        assert file_nodes[0]["label"] == "sales_order.js"

    def test_no_error(self):
        result = extract_js(SALES_ORDER_JS)
        assert "error" not in result

    def test_source_lines_present(self):
        """Every node must have source_line_start and source_line_end."""
        result = extract_js(SALES_ORDER_JS)
        for node in result["nodes"]:
            assert "source_line_start" in node
            assert "source_line_end" in node


class TestFrappeFormOn:
    """Tests for frappe.ui.form.on() method extraction."""

    def test_extends_client_edge(self):
        """frappe.ui.form.on('Sales Order', ...) should create extends_client edge."""
        result = extract_js(SALES_ORDER_JS)
        ext_edges = [e for e in result["edges"] if e["relation"] == "extends_client"]
        assert len(ext_edges) >= 1
        assert any(e.get("doctype") == "Sales Order" for e in ext_edges)

    def test_form_on_methods_extracted(self):
        """setup, refresh, validate should appear as method nodes."""
        result = extract_js(SALES_ORDER_JS)
        method_labels = {n["label"] for n in result["nodes"] if n["file_type"] == "code"}
        assert ".setup()" in method_labels
        assert ".refresh()" in method_labels
        assert ".validate()" in method_labels

    def test_method_edges(self):
        """Methods should have 'method' edges from the DocType node."""
        result = extract_js(SALES_ORDER_JS)
        method_edges = [e for e in result["edges"] if e["relation"] == "method"]
        assert len(method_edges) >= 3  # setup, refresh, validate


class TestFrappeCall:
    """Tests for frappe.call({method: ...}) API detection."""

    def test_calls_api_edge(self):
        """frappe.call({method: '...'}) should produce a calls_api edge."""
        result = extract_js(SALES_ORDER_JS)
        api_edges = [e for e in result["edges"] if e["relation"] == "calls_api"]
        assert len(api_edges) >= 1
        api_paths = {e.get("api_path") for e in api_edges}
        assert "test_app.selling.doctype.sales_order.sales_order.get_stock" in api_paths


class TestCurFrmCscript:
    """Tests for cur_frm.cscript.xxx = 'DocType' detection."""

    def test_references_doctype_edge(self):
        """cur_frm.cscript.tax_table = 'Sales Taxes and Charges' should create edge."""
        result = extract_js(SALES_ORDER_JS)
        ref_edges = [e for e in result["edges"] if e["relation"] == "references_doctype"]
        assert len(ref_edges) >= 1
        doctypes = {e.get("doctype") for e in ref_edges}
        assert "Sales Taxes and Charges" in doctypes


class TestTopLevelFunctions:
    """Tests for top-level function declarations."""

    def test_function_extracted(self):
        """validate_customer() should appear as a top-level function node."""
        result = extract_js(SALES_ORDER_JS)
        func_nodes = [
            n for n in result["nodes"]
            if "validate_customer" in n["label"]
        ]
        assert len(func_nodes) >= 1


class TestJSImports:
    """Tests for import statement extraction."""

    def test_imports_not_present_in_vanilla_js(self):
        """The fixture JS has no import statements — verify no crash."""
        result = extract_js(SALES_ORDER_JS)
        import_edges = [e for e in result["edges"] if e["relation"] == "imports_from"]
        # Vanilla Frappe JS files don't use ES module imports
        assert isinstance(import_edges, list)


class TestFrappeCallSynthetic:
    """Synthetic tests for various frappe.call patterns."""

    def _extract_snippet(self, code: str) -> dict:
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False, encoding="utf-8",
        ) as f:
            f.write(code)
            f.flush()
            return extract_js(Path(f.name))

    def test_frappe_xcall(self):
        result = self._extract_snippet('''
function doStuff() {
    frappe.xcall("erpnext.api.get_data", {filters: {}});
}
''')
        api_edges = [e for e in result["edges"] if e["relation"] == "calls_api"]
        assert len(api_edges) == 1
        assert api_edges[0]["api_path"] == "erpnext.api.get_data"

    def test_map_current_doc(self):
        result = self._extract_snippet('''
function doStuff() {
    erpnext.utils.map_current_doc({
        method: "erpnext.buying.doctype.purchase_order.purchase_order.make_inter_company_sales_order",
        source_doctype: "Purchase Order"
    });
}
''')
        api_edges = [e for e in result["edges"] if e["relation"] == "calls_api"]
        assert len(api_edges) == 1
        assert "make_inter_company_sales_order" in api_edges[0]["api_path"]

    def test_es6_class(self):
        result = self._extract_snippet('''
class SalesOrderController extends TransactionController {
    setup() {
        console.log("setup");
    }
}
''')
        class_nodes = [n for n in result["nodes"] if n["label"] == "SalesOrderController"]
        assert len(class_nodes) == 1
        inherits = [e for e in result["edges"] if e["relation"] == "inherits"]
        assert len(inherits) == 1

    def test_arrow_function(self):
        result = self._extract_snippet('''
const calculateTotal = () => {
    return 42;
}
''')
        func_nodes = [n for n in result["nodes"] if "calculateTotal" in n["label"]]
        assert len(func_nodes) == 1

    def test_es_module_import(self):
        result = self._extract_snippet('''
import { ref, computed } from "vue";
import FormField from "@/components/FormField.vue";
''')
        import_edges = [e for e in result["edges"] if e["relation"] == "imports_from"]
        assert len(import_edges) == 2


class TestFrappeDbJs:
    """Tests for frappe.db.* and frappe.client.* ORM call detection."""

    def test_frappe_db_get_value(self):
        """frappe.db.get_value('Customer', ...) emits queries_doctype edge."""
        result = extract_js(SALES_ORDER_JS)
        edges = [
            e for e in result["edges"]
            if e["relation"] == "queries_doctype"
            and e.get("doctype") == "Customer"
        ]
        assert len(edges) == 1
        assert edges[0]["confidence"] == "INFERRED"
        assert edges[0]["via"] == "frappe.db.get_value"

    def test_frappe_client_get_list(self):
        """frappe.client.get_list({doctype: ...}) emits queries_doctype edge."""
        result = extract_js(SALES_ORDER_JS)
        edges = [
            e for e in result["edges"]
            if e["relation"] == "queries_doctype"
            and e.get("doctype") == "Sales Invoice"
        ]
        assert len(edges) == 1
        assert edges[0]["via"] == "frappe.client.get_list"

    def test_frappe_realtime_on(self):
        """frappe.realtime.on('event', ...) emits subscribes_to_event."""
        result = extract_js(SALES_ORDER_JS)
        sub_edges = [
            e for e in result["edges"]
            if e["relation"] == "subscribes_to_event"
        ]
        assert len(sub_edges) == 1
        assert sub_edges[0]["event"] == "order_update"
        assert sub_edges[0]["confidence"] == "INFERRED"


class TestListviewSettings:
    """Tests for frappe.listview_settings['DT'] assignments."""

    def _extract_snippet(self, code: str) -> dict:
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False, encoding="utf-8",
        ) as f:
            f.write(code)
            f.flush()
            return extract_js(Path(f.name))

    def test_listview_settings_extends_list_view(self):
        result = self._extract_snippet('''
frappe.listview_settings["Sales Order"] = {
    add_fields: ["customer", "status"],
    onload: function(listview) {}
};
''')
        edges = [
            e for e in result["edges"]
            if e["relation"] == "extends_list_view"
        ]
        assert len(edges) == 1
        assert edges[0]["doctype"] == "Sales Order"


class TestVue3CompositionApi:
    """Tests for Vue 3 defineEmits detection."""

    def _extract_snippet(self, code: str) -> dict:
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False, encoding="utf-8",
        ) as f:
            f.write(code)
            f.flush()
            return extract_js(Path(f.name))

    def test_define_emits_array(self):
        result = self._extract_snippet('''
defineEmits(['save', 'cancel']);
''')
        edges = [e for e in result["edges"] if e["relation"] == "emits_event"]
        events = {e["event"] for e in edges}
        assert events == {"save", "cancel"}
