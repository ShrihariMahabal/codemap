"""Security utilities — detect and skip files that may contain secrets."""

from __future__ import annotations

import re
from pathlib import Path

# Patterns that indicate a file likely contains secrets.
# Matched against the filename (not the full path).
_SENSITIVE_PATTERNS = [
    re.compile(r"(^|[\\/])\.(env|envrc)(\.|$)", re.IGNORECASE),
    re.compile(r"\.(pem|key|p12|pfx|cert|crt|der|p8)$", re.IGNORECASE),
    re.compile(r"(credential|secret|passwd|password|token|private_key)", re.IGNORECASE),
    re.compile(r"(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.pub)?$"),
    re.compile(r"(\.netrc|\.pgpass|\.htpasswd)$", re.IGNORECASE),
    re.compile(r"(aws_credentials|gcloud_credentials|service.account)", re.IGNORECASE),
]


def is_sensitive(path: Path) -> bool:
    """Return True if this file likely contains secrets and should be skipped."""
    return any(p.search(path.name) for p in _SENSITIVE_PATTERNS)


def sanitize_label(text: str, max_length: int = 200) -> str:
    """Sanitize a string for use as a graph node label.

    Strips control characters and truncates to ``max_length``.
    Used when building node labels from source code identifiers
    to prevent injection of unrenderable characters into the graph.
    """
    # Remove control characters except newline/tab
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    if len(cleaned) > max_length:
        return cleaned[:max_length] + "…"
    return cleaned
