"""Frappe metadata extraction.

This package extracts Frappe-specific structures (DocType schemas, hooks,
dashboards, modules, generic records) into the same node/edge dict format
used by the tree-sitter extractors.

Each sub-extractor takes a ``Path`` and returns
``{"nodes": [...], "edges": [...]}``.  All sub-extractors are deliberately
isolated — they don't read each other's output, and on any parse error
they return an empty result rather than raising.
"""

from .dashboard import extract_dashboard
from .doctype import extract_doctype
from .hooks import extract_hooks
from .modules import extract_modules
from .notification import extract_notification
from .record import extract_record
from .scripts import extract_client_script, extract_server_script
from .workflow import extract_workflow

__all__ = [
    "extract_client_script",
    "extract_dashboard",
    "extract_doctype",
    "extract_hooks",
    "extract_modules",
    "extract_notification",
    "extract_record",
    "extract_server_script",
    "extract_workflow",
]
