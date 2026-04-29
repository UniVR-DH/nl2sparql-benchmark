"""Operator extraction from query text / parsed objects.

This module implements a pragmatic extractor that satisfies M1/M2: it uses the
parsed object when available, but otherwise falls back to keyword detection
over the raw query text to populate an OperatorSet.
"""
import re
from .model import OperatorSet

from rdflib.plugins.sparql.algebra import translateQuery


KW = {
    "OPTIONAL": "OPTIONAL",
    "UNION": "UNION",
    "MINUS": "MINUS",
    "GRAPH": "GRAPH",
    "SERVICE": "SERVICE",
    "FILTER": "FILTER",
    "BIND": "BIND",
    "VALUES": "VALUES",
    "DISTINCT": "DISTINCT",
    "REDUCED": "REDUCED",
    "GROUP BY": "GROUP BY",
    "HAVING": "HAVING",
    "ORDER BY": "ORDER BY",
    "LIMIT": "LIMIT",
    "OFFSET": "OFFSET",
    "COUNT": "COUNT",
}


def extract_operators(text: str, parsed: object = None) -> OperatorSet:
    ops = OperatorSet()
    up = text.upper()

    # Query form
    if re.search(r"^\s*ASK\b", up, re.I):
        ops.query_form = "ASK"
    elif re.search(r"^\s*CONSTRUCT\b", up, re.I):
        ops.query_form = "CONSTRUCT"
    elif re.search(r"^\s*DESCRIBE\b", up, re.I):
        ops.query_form = "DESCRIBE"
    elif re.search(r"^\s*SELECT\b", up, re.I):
        ops.query_form = "SELECT"
    else:
        ops.query_form = "UNKNOWN"

    # If we have a parsed query and rdflib algebra translator, try inspecting
    # the translated algebra (best-effort). Otherwise fall back to keyword scan.
    if parsed is not None and translateQuery is not None:
        try:
            alg = translateQuery(parsed)
            alg_repr = repr(alg).upper()
        except Exception:
            alg = None
            alg_repr = up

        source_to_scan = alg_repr
    else:
        source_to_scan = up

    for k, v in KW.items():
        if k in source_to_scan:
            if v in ("DISTINCT", "REDUCED"):
                ops.projection_modifiers.add(v)
            elif v in ("OPTIONAL", "UNION", "MINUS", "GRAPH", "SERVICE"):
                ops.graph_patterns.add(v)
            elif v in ("FILTER",):
                ops.filters.add(v)
            elif v in ("COUNT",):
                ops.aggregates.add(v)
            elif v in ("GROUP BY", "HAVING", "ORDER BY", "LIMIT", "OFFSET"):
                ops.solution_modifiers.add(v)
            elif v in ("BIND", "VALUES"):
                ops.assignments.add(v)
            ops.raw.add(v)

    # property paths heuristic
    if re.search(r"[\^\|\*/+]", text):
        ops.property_paths = True
        ops.raw.add("PROPERTY_PATH")

    # subquery heuristic
    if re.search(r"\{\s*SELECT\b", up):
        ops.subqueries = True
        ops.raw.add("SUBQUERY")

    return ops
