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
from rdflib.paths import Path as _RDFPath
from typing import Any, Optional, Set, Tuple

from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.term import Variable as _Variable
import rdflib.plugins.sparql.parser as _sparql_parser
import rdflib.plugins.sparql.algebra as _sparql_algebra

_log = logging.getLogger(__name__)

# Canonical LSQ feature names that correspond to SPARQL operators/clauses.
# Single source of truth — imported by reporter.py and cli.py.
OPERATOR_FEATURES: Set[str] = {
    "Select",
    "Ask",
    "Construct",
    "Describe",
    "Distinct",
    "Reduced",
    "Limit",
    "Offset",
    "OrderBy",
    "Filter",
    "Optional",
    "Union",
    "Minus",
    "Graph",
    "Service",
    "Aggregators",
    "GroupBy",
    "Having",
    "SubQuery",
    "PropertyPath",
    "Bind",
    "Values",
}


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


def _count_triples_block(node: CompValue) -> Tuple[int, int]:
    """Recursively count BGPs and triple patterns inside a parse-tree node
    (GroupGraphPatternSub / TriplesBlock) that rdflib does not translate to algebra.
    Returns (bgp_count, tp_count).
    """
    if not isinstance(node, CompValue):
        return 0, 0
    bgps = 0
    tps = 0
    if node.name == "TriplesBlock":
        bgps += 1
        tps += len(node.get("triples") or [])
    for v in node.values():
        if isinstance(v, CompValue):
            b, t = _count_triples_block(v)
            bgps += b
            tps += t
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, CompValue):
                    b, t = _count_triples_block(item)
                    bgps += b
                    tps += t
    return bgps, tps


def compute_metrics(algebra_node: CompValue) -> Tuple[int, int, int]:
    """Return (bgp_count, tp_count, project_var_count) from an algebra tree.

    Counts BGPs in the main algebra tree AND inside FILTER NOT EXISTS /
    FILTER EXISTS inner patterns (which rdflib keeps as parse-tree nodes,
    not algebra BGP nodes), per the SPARQL 1.1 spec (§18.1–18.2).

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
        elif node.name in {"Builtin_NOTEXISTS", "Builtin_EXISTS"}:
            # Inner pattern is kept as a parse-tree node (GroupGraphPatternSub)
            # rather than translated to algebra — count its TriplesBlocks explicitly.
            graph = node.get("graph")
            if graph is not None:
                b, t = _count_triples_block(graph)
                bgp_count += b
                tp_count += t

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
        projection = (
            query_clause.get("projection", []) if hasattr(query_clause, "get") else []
        )
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
        having_node = (
            query_clause.get("having") if hasattr(query_clause, "get") else None
        )
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
            # rdflib nests multiple aggregate aliases as Extend→Extend→…→AggregateJoin.
            # GROUP BY re-projections also compile to Extend nodes (using Aggregate_Sample
            # internally), so the structural check on AggregateJoin correctly catches both
            # real aliases and re-projections — neither should emit Bind.
            while isinstance(child, CompValue) and child.name in {"Filter", "Extend"}:
                _log.debug(f"Unwrapping {child.name} under Extend")
                child = child.get("p")

            if isinstance(child, CompValue) and child.name in {
                "AggregateJoin",
                "Group",
            }:
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

        elif name == "AggregateJoin":
            found.add("Aggregators")

        elif name == "Group":
            if node.get("expr"):  # None = implicit aggregate group, not a real GROUP BY
                found.add("GroupBy")

        elif name == "SelectQuery":
            found.add("Select")

        elif name == "AskQuery":
            found.add("Ask")

        elif name == "ConstructQuery":
            found.add("Construct")

        elif name == "DescribeQuery":
            found.add("Describe")

        elif name == "Distinct":
            found.add("Distinct")

        elif name == "OrderBy":
            found.add("OrderBy")

        elif name == "Slice":
            if node.get("length") is not None:
                found.add("Limit")
            if node.get("start") not in (None, 0):
                found.add("Offset")

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


def extract_operators(text: str, parsed: object, alg=None):
    """Walk the algebra tree and return a populated OperatorSet.

    Parameters
    ----------
    text:
        The raw SPARQL query string. Used for query-form fallback when *parsed*
        is None, and for HAVING detection via the parse tree.
    parsed:
        The rdflib parse tree produced by ``_sparql_parser.parseQuery(text)``.
        Pass None to get a best-effort result from text inspection only.
    alg:
        A pre-translated algebra object produced by
        ``_sparql_algebra.translateQuery(parsed)`` for the **same** *parsed*
        tree.  When provided, ``translateQuery`` is not called again, saving
        one redundant translation per query.

    .. warning:: Caller coherence contract
        *parsed* and *alg* **must** correspond to the same *text*.
        rdflib mutates the parse tree in-place during ``translateQuery``, so
        once ``translateQuery`` has been called on a *parsed* object that object
        is no longer a valid input for a second ``translateQuery`` call.
        Passing a *parsed* tree that was already translated (without supplying
        the matching *alg*) will produce silently wrong results.
        There is no cheap way to assert coherence at runtime; the caller is
        solely responsible for maintaining it.  The canonical safe pattern is::

            parsed = _sparql_parser.parseQuery(text)
            alg    = _sparql_algebra.translateQuery(parsed)
            ops    = extract_operators(text, parsed, alg=alg)
            # do NOT call translateQuery(parsed) again after this point

    Imports OperatorSet locally to avoid a circular import with model.py.
    """
    from .model import OperatorSet

    ops = OperatorSet()

    # Query form — use algebra root node name (reliable) with text fallback
    if parsed is None:
        up = text.upper().lstrip()
        ops.query_form = (
            "ASK"
            if up.startswith("ASK")
            else "CONSTRUCT"
            if up.startswith("CONSTRUCT")
            else "DESCRIBE"
            if up.startswith("DESCRIBE")
            else "SELECT"
            if up.startswith("SELECT")
            else "UNKNOWN"
        )
        return ops

    if alg is None:
        try:
            alg = _sparql_algebra.translateQuery(parsed)
        except Exception:
            return ops

    root_name = alg.algebra.name
    if root_name == "SelectQuery":
        ops.query_form = "SELECT"
    elif root_name == "AskQuery":
        ops.query_form = "ASK"
    elif root_name == "ConstructQuery":
        ops.query_form = "CONSTRUCT"
    elif root_name == "DescribeQuery":
        ops.query_form = "DESCRIBE"
    else:
        ops.query_form = "UNKNOWN"

    # FILTER function names that map to Builtin_* nodes
    _FILTER_FUNCS = {
        "Builtin_REGEX": "REGEX",
        "Builtin_LANG": "LANG",
        "Builtin_LANGMATCHES": "LANGMATCHES",
        "Builtin_DATATYPE": "DATATYPE",
        "Builtin_BOUND": "BOUND",
        "Builtin_IRI": "IRI",
        "Builtin_URI": "URI",
        "Builtin_BNODE": "BNODE",
        "Builtin_STR": "STR",
        "Builtin_STRDT": "STRDT",
        "Builtin_STRLANG": "STRLANG",
        "Builtin_STRLEN": "STRLEN",
        "Builtin_SUBSTR": "SUBSTR",
        "Builtin_UCASE": "UCASE",
        "Builtin_LCASE": "LCASE",
        "Builtin_STRSTARTS": "STRSTARTS",
        "Builtin_STRENDS": "STRENDS",
        "Builtin_CONTAINS": "CONTAINS",
        "Builtin_ENCODE_FOR_URI": "ENCODE_FOR_URI",
        "Builtin_CONCAT": "CONCAT",
        "Builtin_REPLACE": "REPLACE",
        "Builtin_ABS": "ABS",
        "Builtin_ROUND": "ROUND",
        "Builtin_CEIL": "CEIL",
        "Builtin_FLOOR": "FLOOR",
        "Builtin_RAND": "RAND",
        "Builtin_NOW": "NOW",
        "Builtin_YEAR": "YEAR",
        "Builtin_MONTH": "MONTH",
        "Builtin_DAY": "DAY",
        "Builtin_HOURS": "HOURS",
        "Builtin_MINUTES": "MINUTES",
        "Builtin_SECONDS": "SECONDS",
        "Builtin_TIMEZONE": "TIMEZONE",
        "Builtin_TZ": "TZ",
        "Builtin_MD5": "MD5",
        "Builtin_SHA1": "SHA1",
        "Builtin_SHA256": "SHA256",
        "Builtin_SHA384": "SHA384",
        "Builtin_SHA512": "SHA512",
        "Builtin_isIRI": "isIRI",
        "Builtin_isURI": "isURI",
        "Builtin_isLITERAL": "isLITERAL",
        "Builtin_isNUMERIC": "isNUMERIC",
        "Builtin_isBLANK": "isBLANK",
        "Builtin_sameTerm": "sameTerm",
        "Builtin_IN": "IN",
        "Builtin_NOT_IN": "NOT_IN",
        "Builtin_IF": "IF",
        "Builtin_COALESCE": "COALESCE",
    }

    seen: Set[int] = set()

    def _check_expr_for_builtins(expr) -> None:
        """Recursively find Builtin_* nodes inside a filter expression."""
        if not isinstance(expr, CompValue):
            return
        fn = _FILTER_FUNCS.get(expr.name)
        if fn:
            ops.filter_functions.add(fn)
            ops.raw.add(fn)
        for v in expr.values():
            if isinstance(v, CompValue):
                _check_expr_for_builtins(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, CompValue):
                        _check_expr_for_builtins(item)

    for node in _walk_algebra(alg.algebra):
        nid = id(node)
        if nid in seen:
            continue
        seen.add(nid)
        name = node.name

        if name == "BGP":
            ops.bgp_count += 1
            triples = node.get("triples") or []
            ops.tp_count += len(triples)
            # Property paths: check predicate position of each triple
            for triple in triples:
                if len(triple) >= 2:
                    pred = triple[1]
                    if isinstance(pred, _RDFPath):
                        ops.property_paths = True
                        ops.raw.add("PROPERTY_PATH")

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

        elif name == "ServiceGraphPattern":
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
                _check_expr_for_builtins(expr)

        elif name == "Extend":
            if _is_user_bind(node, parsed):
                ops.assignments.add("BIND")
                ops.raw.add("BIND")

        elif name == "ToMultiSet":
            inner = node.get("p")
            if isinstance(inner, CompValue):
                if inner.name == "Project":
                    ops.subqueries = True
                    ops.raw.add("SUBQUERY")
                elif inner.name == "values":
                    ops.assignments.add("VALUES")
                    ops.raw.add("VALUES")

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

    # HAVING: check parse tree
    try:
        query_clause = parsed[1]
        having_node = (
            query_clause.get("having") if hasattr(query_clause, "get") else None
        )
        if isinstance(having_node, CompValue):
            ops.solution_modifiers.add("HAVING")
            ops.raw.add("HAVING")
    except (IndexError, AttributeError):
        pass

    # project_var_count (SELECT only)
    if alg.algebra.name == "SelectQuery":
        ops.project_var_count = len(alg.algebra.get("PV") or [])

    return ops


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def referenced_terms(text: str) -> Set[str]:
    """Return the set of IRIs written in angle brackets in *text*."""
    return set(re.findall(r"<([^>]+)>", text))
