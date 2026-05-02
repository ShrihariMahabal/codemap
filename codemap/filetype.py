"""Frappe-aware file type classifications used by the detection phase."""

from enum import Enum


class FileType(str, Enum):
    """Every file detected by codemap is assigned one of these types.

    The types fall into two categories:
    - Code types (CODE_PY, CODE_JS, CODE_VUE) — parsed by tree-sitter.
    - Metadata types (DOCTYPE_JSON, HOOKS, etc.) — parsed by dedicated
      Frappe-specific extractors.
    """

    # Source code — processed by tree-sitter AST extraction
    CODE_PY = "code_py"
    CODE_JS = "code_js"
    CODE_VUE = "code_vue"

    # Frappe metadata — processed by Frappe-specific extractors
    DOCTYPE_JSON = "doctype_json"
    HOOKS = "hooks"
    DASHBOARD = "dashboard"
    REPORT_JSON = "report_json"
    MODULES_TXT = "modules_txt"

    # Documentation — not extracted, but tracked for completeness
    DOCUMENT = "document"
