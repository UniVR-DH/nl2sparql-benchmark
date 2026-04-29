"""
Single algebra-walk module for all SPARQL structural analysis.

Replaces: operators.py, metrics.py, parser.py, scripts/algebra_features.py

Public API
----------
parse_query(text)                       -> (is_valid, parsed_tree, error_or_None)
extract_operators(text, parsed)         -> OperatorSet  (with bgp/tp/pv counts)
detect_lsq_features(algebra_node, parsed) -> Set[str] of LSQ feature names
compute_metrics(algebra_node)           -> (bgp_count, tp_count, project_var_count)
referenced_terms(text)                  -> Set[str] of angle-bracket IRIs
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Set, Tuple

from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.term import Variable as _Variable
import rdflib.plugins.sparql.parser as _sparql_parser
import rdflib.plugins.sparql.algebra as _sparql_algebra

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_query(text: str) -> Tuple[bool, Optional[Any], Optional[str]]:
    """Attempt to parse a SPARQL query string via rdflib.

    Returns (is_valid, parsed_tree_or_None, error_message_or_None).
    """
    try:
        parsed = _sparql_parser.parseQuery(text)
        return True, parsed, None
    except Exception as exc:
        return False, None, str(exc)


# ---------------------------------------------------------------------------
# Shared algebra walker
# ---------------------------------------------------------------------------

def _walk_algebra(node: CompValue):
    """Depth-first generator over all CompValue nodes in an algebra tree."""
    if not isinstance(node, CompValue):
        return
    yield node
    for value in node.values():
        if isinstance(value, CompValue):
            yield from _walk_algebra(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, CompValue):
                    yield from _walk_algebra(item)


# ---------------------------------------------------------------------------
# Structural metrics
# ---------------------------------------------------------------------------

def compute_metrics(algebra_node: CompValue) -> Tuple[int, int, int]:
    """Return (bgp_count, tp_count, project_var_count) from an algebra tree.

    project_var_count is 0 for non-SELECT queries (ASK/CONSTRUCT/DESCRIBE).
    """
    bgp_count = 0
    tp_count = 0
    seen: Set[int] = set()

    for node in _walk_algebra(algebra_node):
        nid = id(node)
        if nid in seen:
            continue
        seen.add(nid)
        if node.name == "BGP":
            bgp_count += 1
            tp_count += len(node.get("triples") or [])

    if algebra_node.name == "SelectQuery":
        project_var_count = len(algebra_node.get("PV") or [])
    else:
        project_var_count = 0

    return bgp_count, tp_count, project_var_count


# ---------------------------------------------------------------------------
# LSQ feature detection (used by classifier)
# ---------------------------------------------------------------------------

def _is_user_bind(node: CompValue, parsed: object) -> bool:
    """Return True iff this Extend node is a user-written BIND (not a projection alias)."""
    try:
        query_clause = parsed[1]
        projection = query_clause.get("projection", []) if hasattr(query_clause, "get") else []
        alias_vars = {
            str(item.get("evar"))
            for item in projection
            if hasattr(item, "get") and isinstance(item.get("evar"), _Variable)
        }
        var = node.get("var")
        if not isinstance(var, _Variable):
            return False
        return str(var) not in alias_vars
    except Exception as exc:
        _log.debug(f"_is_user_bind failed: {exc}")
        return False


def detect_lsq_features(algebra_node: CompValue, parsed: object) -> Set[str]:
    """Walk the algebra tree and return the set of LSQ feature name strings.

    Mapping:
      ToMultiSet(Project(...))          → SubQuery
      Filter(Builtin_NOTEXISTS)         → NotExists
      Filter(Builtin_EXISTS)            → fn-exists
      Filter(TrueFilter)                → (skip — synthetic OPTIONAL filter)
      Filter(other), not HAVING         → Filter
      HAVING (from parse tree)          → Having  (the wrapping Filter is skipped)
      Extend (user BIND)                → Bind
      Extend (aggregate alias)          → (skip)
      LeftJoin                          → Optional
    """
    found: Set[str] = set()
    seen: Set[int] = set()

    # Detect HAVING from parse tree
    try:
        query_clause = parsed[1]
        having_node = query_clause.get("having") if hasattr(query_clause, "get") else None
        has_having = isinstance(having_node, CompValue)
    except (IndexError, AttributeError):
        has_having = False
    if has_having:
        found.add("Having")

    def _walk(node: CompValue) -> None:
        if not isinstance(node, CompValue):
            return
        nid = id(node)
        if nid in seen:
            return
        seen.add(nid)

        name = node.name

        if name == "ToMultiSet":
            inner = node.get("p")
            if isinstance(inner, CompValue) and inner.name == "Project":
                found.add("SubQuery")

        elif name == "Filter":
            expr = node.get("expr")
            child = node.get("p")
            # Unwrap Extend nodes between this Filter and the aggregate root —
            # rdflib places the HAVING Filter above inner Extend aliases when
            # there are multiple aggregate projections (e.g. SELECT ?name (COUNT(?x) AS ?c)).
            _c = child
            while isinstance(_c, CompValue) and _c.name == "Extend":
                _c = _c.get("p")
            is_having_filter = (
                has_having
                and isinstance(_c, CompValue)
                and _c.name in {"AggregateJoin", "Group"}
            )
            if not is_having_filter and expr is not None:
                expr_name = getattr(expr, "name", None)
                if expr_name == "Builtin_NOTEXISTS":
                    found.add("NotExists")
                elif expr_name == "Builtin_EXISTS":
                    found.add("fn-exists")
                elif expr_name != "TrueFilter":
                    found.add("Filter")

        elif name == "Extend":
            _log.debug(f"Extend node: {node}")
            child = node.get("p")
            # Unwrap Filter and nested Extend nodes to find the aggregate root.
            # rdflib nests multiple aggregate aliases as Extend→Extend→…→AggregateJoin,
            # and may place a HAVING Filter between the outermost Extend and AggregateJoin.
            while isinstance(child, CompValue) and child.name in {"Filter", "Extend"}:
                _log.debug(f"Unwrapping {child.name} under Extend")
                child = child.get("p")

            if isinstance(child, CompValue) and child.name in {"AggregateJoin", "Group"}:
                _log.debug("Extend → aggregate alias (NOT Bind)")
                # fall through to recurse into subtree
            else:
                if _is_user_bind(node, parsed):
                    found.add("Bind")
                    _log.debug("Extend → Bind")
                else:
                    _log.debug("Extend → aggregate alias (NOT Bind)")

        elif name == "LeftJoin":
            found.add("Optional")

        for value in node.values():
            if isinstance(value, CompValue):
                _walk(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, CompValue):
                        _walk(item)

    _walk(algebra_node)
    return found


# ---------------------------------------------------------------------------
# Generic operator extraction (used by annotate pipeline)
# ---------------------------------------------------------------------------

def extract_operators(text: str, parsed: object):
    """Walk the algebra tree and return a populated OperatorSet.

    Imports OperatorSet locally to avoid a circular import with model.py.
    """
    from .model import OperatorSet

    ops = OperatorSet()

    # Query form from parse tree
    up = text.upper().lstrip()
    if up.startswith("ASK"):
        ops.query_form = "ASK"
    elif up.startswith("CONSTRUCT"):
        ops.query_form = "CONSTRUCT"
    elif up.startswith("DESCRIBE"):
        ops.query_form = "DESCRIBE"
    elif up.startswith("SELECT"):
        ops.query_form = "SELECT"
    else:
        ops.query_form = "UNKNOWN"

    if parsed is None:
        return ops

    try:
        alg = _sparql_algebra.translateQuery(parsed)
    except Exception:
        return ops

    seen: Set[int] = set()

    for node in _walk_algebra(alg.algebra):
        nid = id(node)
        if nid in seen:
            continue
        seen.add(nid)
        name = node.name

        if name == "BGP":
            ops.bgp_count += 1
            ops.tp_count += len(node.get("triples") or [])

        elif name == "LeftJoin":
            ops.graph_patterns.add("OPTIONAL")
            ops.raw.add("OPTIONAL")

        elif name == "Union":
            ops.graph_patterns.add("UNION")
            ops.raw.add("UNION")

        elif name == "Minus":
            ops.graph_patterns.add("MINUS")
            ops.raw.add("MINUS")

        elif name == "Graph":
            ops.graph_patterns.add("GRAPH")
            ops.raw.add("GRAPH")

        elif name == "Service":
            ops.graph_patterns.add("SERVICE")
            ops.raw.add("SERVICE")

        elif name == "Filter":
            expr = node.get("expr")
            expr_name = getattr(expr, "name", None) if expr else None
            if expr_name == "TrueFilter":
                pass
            elif expr_name == "Builtin_NOTEXISTS":
                ops.filters.add("FILTER NOT EXISTS")
                ops.raw.add("FILTER NOT EXISTS")
            elif expr_name == "Builtin_EXISTS":
                ops.filters.add("FILTER EXISTS")
                ops.raw.add("FILTER EXISTS")
            elif expr_name is not None:
                ops.filters.add("FILTER")
                ops.raw.add("FILTER")

        elif name == "Extend":
            if _is_user_bind(node, parsed):
                ops.assignments.add("BIND")
                ops.raw.add("BIND")

        elif name == "ToMultiSet":
            inner = node.get("p")
            if isinstance(inner, CompValue) and inner.name == "Project":
                ops.subqueries = True
                ops.raw.add("SUBQUERY")

        elif name == "AggregateJoin":
            aggs = node.get("A") or []
            for agg in aggs:
                agg_name = getattr(agg, "name", "")
                if "Count" in agg_name:
                    ops.aggregates.add("COUNT")
                    ops.raw.add("COUNT")
                elif "Sum" in agg_name:
                    ops.aggregates.add("SUM")
                    ops.raw.add("SUM")
                elif "Avg" in agg_name:
                    ops.aggregates.add("AVG")
                    ops.raw.add("AVG")
                elif "Min" in agg_name:
                    ops.aggregates.add("MIN")
                    ops.raw.add("MIN")
                elif "Max" in agg_name:
                    ops.aggregates.add("MAX")
                    ops.raw.add("MAX")
                elif "Sample" in agg_name:
                    ops.aggregates.add("SAMPLE")
                    ops.raw.add("SAMPLE")
                elif "GroupConcat" in agg_name:
                    ops.aggregates.add("GROUP_CONCAT")
                    ops.raw.add("GROUP_CONCAT")

        elif name == "Group":
            ops.solution_modifiers.add("GROUP BY")
            ops.raw.add("GROUP BY")

        elif name == "OrderBy":
            ops.solution_modifiers.add("ORDER BY")
            ops.raw.add("ORDER BY")

        elif name == "Slice":
            if node.get("start") not in (None, 0):
                ops.solution_modifiers.add("OFFSET")
                ops.raw.add("OFFSET")
            if node.get("length") is not None:
                ops.solution_modifiers.add("LIMIT")
                ops.raw.add("LIMIT")

        elif name == "Distinct":
            ops.projection_modifiers.add("DISTINCT")
            ops.raw.add("DISTINCT")

        elif name == "Reduced":
            ops.projection_modifiers.add("REDUCED")
            ops.raw.add("REDUCED")

        elif name == "Join" and node.get("p1") is not None:
            # VALUES inline data compiles to a Join with a ToMultiSet(values) on one side
            p1 = node.get("p1")
            p2 = node.get("p2")
            for side in (p1, p2):
                if isinstance(side, CompValue) and side.name == "ToMultiSet":
                    inner = side.get("p")
                    if isinstance(inner, CompValue) and inner.name != "Project":
                        ops.assignments.add("VALUES")
                        ops.raw.add("VALUES")

    # HAVING: check parse tree
    try:
        query_clause = parsed[1]
        having_node = query_clause.get("having") if hasattr(query_clause, "get") else None
        if isinstance(having_node, CompValue):
            ops.solution_modifiers.add("HAVING")
            ops.raw.add("HAVING")
    except (IndexError, AttributeError):
        pass

    # project_var_count
    if alg.algebra.name == "SelectQuery":
        ops.project_var_count = len(alg.algebra.get("PV") or [])

    # property paths: any NegatedPath / AlternativePath / SequencePath / etc.
    for node in _walk_algebra(alg.algebra):
        if isinstance(node, CompValue) and "Path" in node.name:
            ops.property_paths = True
            ops.raw.add("PROPERTY_PATH")
            break

    return ops


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def referenced_terms(text: str) -> Set[str]:
    """Return the set of IRIs written in angle brackets in *text*."""
    return set(re.findall(r"<([^>]+)>", text))
