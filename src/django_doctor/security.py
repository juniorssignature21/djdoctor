"""Redaction helpers. Django Doctor must never print secrets.

Anything that may contain credentials (error messages, settings values,
connection strings) passes through :func:`redact_text` before being displayed
or handed to an optional AI provider.
"""

from __future__ import annotations

import re
from typing import Any

MASK = "********"

_SENSITIVE_NAME = re.compile(
    r"(SECRET|PASSWORD|PASSWD|PWD|TOKEN|API_?KEY|ACCESS_?KEY|PRIVATE|CREDENTIAL|AUTH|SIGNATURE|DSN|SALT|COOKIE)",
    re.IGNORECASE,
)

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # scheme://user:password@host
    (re.compile(r"(?P<pre>\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+:)(?P<secret>[^\s@/]+)(?P<post>@)"), MASK),
    # password=..., "password": "...", PASSWORD: '...', secret_key = '...'
    (
        re.compile(
            r"""(?P<pre>(?:["']?)(?:[A-Za-z_]*?(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)[A-Za-z_]*)(?:["']?)\s*[:=]\s*)(?P<q>["']?)(?P<secret>[^\s"',;}]+)(?P=q)""",
            re.IGNORECASE,
        ),
        MASK,
    ),
    # Authorization: Bearer xxxxx
    (re.compile(r"(?P<pre>\bBearer\s+)(?P<secret>[A-Za-z0-9._~+/=-]{8,})"), MASK),
]

# Values that follow "Key (col)=(value)" in PostgreSQL IntegrityError details may
# be personal data (emails, usernames). Mask the value but keep the column.
_PG_DETAIL = re.compile(r"(Key \([^)]*\)=\()([^)]*)(\))")
_MYSQL_DUPLICATE = re.compile(r"(Duplicate entry ')([^']*)(' for key)")


def is_sensitive_name(name: str) -> bool:
    return bool(_SENSITIVE_NAME.search(name or ""))


def redact_text(text: str) -> str:
    if not text:
        return text
    for pattern, mask in _PATTERNS:
        text = pattern.sub(lambda m, mask=mask: _sub(m, mask), text)
    text = _PG_DETAIL.sub(lambda m: f"{m.group(1)}{MASK}{m.group(3)}", text)
    text = _MYSQL_DUPLICATE.sub(lambda m: f"{m.group(1)}{MASK}{m.group(3)}", text)
    return text


def _sub(match: re.Match[str], mask: str) -> str:
    groups = match.groupdict()
    return f"{groups.get('pre', '')}{groups.get('q') or ''}{mask}{groups.get('q') or ''}{groups.get('post') or ''}"


def redact_mapping(data: Any) -> Any:
    """Recursively mask values whose key looks sensitive."""
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            if isinstance(key, str) and is_sensitive_name(key) and value not in (None, "", [], {}):
                out[key] = MASK
            else:
                out[key] = redact_mapping(value)
        return out
    if isinstance(data, list):
        return [redact_mapping(v) for v in data]
    if isinstance(data, str):
        return redact_text(data)
    return data
