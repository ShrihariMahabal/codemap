"""Frappe-aware file type classifications used by the detection phase."""

from enum import Enum


class FileType(str, Enum):
    """Every file detected by codemap is assigned one of these types.

    The types fall into three categories:
    - Code types (CODE_PY, CODE_JS, CODE_VUE, BUNDLE_JS) — parsed by tree-sitter.
    - Frappe metadata types (DOCTYPE_JSON, HOOKS, WORKFLOW_JSON, ...) —
      parsed by dedicated Frappe-specific extractors.
    - Documentation / supporting files — tracked but not extracted.
    """

    # Source code — processed by tree-sitter AST extraction
    CODE_PY = "code_py"
    CODE_JS = "code_js"
    CODE_VUE = "code_vue"
    BUNDLE_JS = "bundle_js"

    # Frappe metadata — processed by Frappe-specific extractors
    DOCTYPE_JSON = "doctype_json"
    HOOKS = "hooks"
    DASHBOARD = "dashboard"
    REPORT_JSON = "report_json"
    WORKFLOW_JSON = "workflow_json"
    NOTIFICATION_JSON = "notification_json"
    SERVER_SCRIPT_JSON = "server_script_json"
    CLIENT_SCRIPT_JSON = "client_script_json"
    PRINT_FORMAT_JSON = "print_format_json"
    WEB_FORM_JSON = "web_form_json"
    PAGE_JSON = "page_json"
    CUSTOM_FIELD_JSON = "custom_field_json"
    PROPERTY_SETTER_JSON = "property_setter_json"
    RECORD_JSON = "record_json"
    MODULES_TXT = "modules_txt"
    PATCHES_TXT = "patches_txt"

    # Templates and styles
    TEMPLATE_HTML = "template_html"
    STYLE_SCSS = "style_scss"

    # Documentation — not extracted, but tracked for completeness
    DOCUMENT = "document"
