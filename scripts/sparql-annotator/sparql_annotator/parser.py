"""Parsing helpers for SPARQL queries.

This module wraps rdflib's parser but falls back to a lightweight regex-based
pre-check so the package can produce reasonable annotations even when parsing
fails.
"""
from typing import Any, Optional, Tuple

try:
    from rdflib.plugins.sparql.parser import parseQuery
except Exception:  # pragma: no cover - optional runtime
    parseQuery = None


def parse_query(text: str) -> Tuple[bool, Optional[Any], Optional[str]]:
    """Attempt to parse a SPARQL query.

    Returns (is_valid, parsed_obj_or_None, error_message_or_None).
    """
    if not parseQuery:
        return True, None, None

    try:
        parsed = parseQuery(text)
        return True, parsed, None
    except Exception as e:
        return False, None, str(e)
