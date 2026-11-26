"""Redact capture copies; the MCP transport bytes never pass through this."""
import re

SENSITIVE = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|private[_-]?key)")
PATTERNS = [
    (re.compile(r"(?:sk-ant-api03-|sk-proj-|sk-|github_pat_|gh[pousr]_)[A-Za-z0-9_-]{20,}"), "[REDACTED_KEY]"),
    (re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)((?:neo4j|bolt|https?|postgres(?:ql)?|redis)(?:\+s|\+ssc)?://[^:\s/@]+:)[^@\s]+@"), r"\1[REDACTED]@"),
    (re.compile(r'''(?i)(\b\w*(?:password|passwd|secret|token|api[_-]?key)\w*['"]?\s*[:=]\s*)(['"])(?:\\.|(?!\2).)*?\2'''), r"\1\2[REDACTED]\2"),
]


def sanitize(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SENSITIVE.search(k) else sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        for pattern, replacement in PATTERNS:
            value = pattern.sub(replacement, value)
    return value
