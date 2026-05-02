"""
SPARQL antipattern detection.

Detects structural issues in SPARQL queries that are syntactically valid but
semantically problematic, non-portable, or likely unintentional.

Public API
----------
detect_antipatterns(text, parsed=None) -> List[AntipatternIssue]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Set

from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.term import Variable
import rdflib.plugins.sparql.algebra as _sparql_algebra

from .algebra import parse_query, _walk_algebra


@dataclass
class AntipatternIssue:
    code: str  # e.g. "AP01"
    message: str
    hint: str  # suggested fix


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _aggregate_alias_vars(alg_root: CompValue) -> Set[str]:
    """Return variable names that are true aggregate aliases (not GROUP BY re-projections).

    Aggregate aliases are Extend nodes whose expr is an __agg_N__ variable that
    corresponds to a non-Sample aggregate in AggregateJoin.A (COUNT/SUM/AVG/etc.).
    GROUP BY re-projections use Aggregate_Sample and are NOT aliases.
    """
    # Collect all __agg_N__ → aggregate_name mappings from AggregateJoin nodes
    agg_map: dict[str, str] = {}  # __agg_N__ → aggregate name
    for node in _walk_algebra(alg_root):
        if node.name == "AggregateJoin":
            for agg in node.get("A") or []:
                res = agg.get("res")
                if isinstance(res, Variable):
                    agg_map[str(res)] = agg.name

    # Collect Extend vars whose expr is a non-Sample aggregate
    aliases: Set[str] = set()
    for node in _walk_algebra(alg_root):
        if node.name == "Extend":
            var = node.get("var")
            expr = node.get("expr")
            if isinstance(var, Variable) and isinstance(expr, Variable):
                agg_name = agg_map.get(str(expr))
                if agg_name and agg_name != "Aggregate_Sample":
                    aliases.add(str(var))
    return aliases


def _bgp_bound_vars(alg_root: CompValue) -> Set[str]:
    """Return all variables that appear in BGP triple patterns (i.e. are bound by the query body)."""
    bound: Set[str] = set()
    for node in _walk_algebra(alg_root):
        if node.name == "BGP":
            for triple in node.get("triples") or []:
                for item in triple:
                    if isinstance(item, Variable):
                        bound.add(str(item))
    return bound


def _bgp_components(triples) -> int:
    """Return number of connected components in the variable graph of a BGP."""
    vars_per_triple = []
    for t in triples:
        vs = frozenset(str(i) for i in t if isinstance(i, Variable))
        vars_per_triple.append(vs)

    if not vars_per_triple:
        return 0

    # Union-Find
    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(a, b):
        parent[find(a)] = find(b)

    for vs in vars_per_triple:
        lst = list(vs)
        for v in lst:
            parent.setdefault(v, v)
        for i in range(1, len(lst)):
            union(lst[0], lst[i])

    all_vars = set(parent.keys())
    return len({find(v) for v in all_vars})


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def _ap01_order_limit1(alg: CompValue) -> Optional[AntipatternIssue]:
    """ORDER BY + LIMIT 1 for extrema — use MIN/MAX aggregate instead."""
    has_order = any(n.name == "OrderBy" for n in _walk_algebra(alg))
    if not has_order:
        return None
    for node in _walk_algebra(alg):
        if node.name == "Slice" and node.get("length") == 1:
            return AntipatternIssue(
                code="AP01",
                message="ORDER BY + LIMIT 1 used to find an extremum.",
                hint="Use MIN() or MAX() aggregate instead for better performance.",
            )
    return None


def _ap02_distinct_with_aggregation(alg: CompValue) -> Optional[AntipatternIssue]:
    """SELECT DISTINCT with aggregation — DISTINCT is redundant or misleading."""
    has_distinct = any(n.name == "Distinct" for n in _walk_algebra(alg))
    has_agg = any(n.name == "AggregateJoin" for n in _walk_algebra(alg))
    if has_distinct and has_agg:
        return AntipatternIssue(
            code="AP02",
            message="DISTINCT used together with aggregation.",
            hint="DISTINCT is redundant when aggregating; remove it or use GROUP BY.",
        )
    return None


def _ap03_proj_var_agg_no_groupby(
    alg: CompValue, parsed: object
) -> Optional[AntipatternIssue]:
    """Non-aggregate projected variable alongside aggregate without GROUP BY."""
    has_agg = any(n.name == "AggregateJoin" for n in _walk_algebra(alg))
    if not has_agg:
        return None

    # Check if Group has no explicit GROUP BY expressions
    for node in _walk_algebra(alg):
        if node.name == "Group":
            expr = node.get("expr")
            if expr:  # explicit GROUP BY present
                return None

    # No GROUP BY: check if any projected var is not an aggregate alias
    pv = [str(v) for v in (alg.get("PV") or []) if isinstance(v, Variable)]
    aliases = _aggregate_alias_vars(alg)
    non_agg_proj = [v for v in pv if v not in aliases]
    if non_agg_proj:
        return AntipatternIssue(
            code="AP03",
            message=f"Variable(s) {non_agg_proj} projected alongside aggregate without GROUP BY.",
            hint="Add GROUP BY for each non-aggregate projected variable.",
        )
    return None


def _ap04_cartesian_product(alg: CompValue) -> Optional[AntipatternIssue]:
    """BGP with disconnected variable components — likely unintended Cartesian product."""
    for node in _walk_algebra(alg):
        if node.name == "BGP":
            triples = node.get("triples") or []
            if len(triples) >= 2 and _bgp_components(triples) > 1:
                return AntipatternIssue(
                    code="AP04",
                    message="BGP contains disconnected triple patterns (Cartesian product).",
                    hint="Add a join condition linking the disconnected variable sets.",
                )
    return None


def _ap05_non_grouped_vars(
    alg: CompValue, parsed: object = None
) -> Optional[AntipatternIssue]:
    """Projected variables not in GROUP BY and not aggregate aliases."""
    has_agg = any(n.name == "AggregateJoin" for n in _walk_algebra(alg))
    if not has_agg:
        return None

    group_vars: Set[str] = set()
    for node in _walk_algebra(alg):
        if node.name == "Group":
            for v in node.get("expr") or []:
                if isinstance(v, Variable):
                    group_vars.add(str(v))

    if not group_vars:
        return None  # no GROUP BY — covered by AP03

    pv = [str(v) for v in (alg.get("PV") or []) if isinstance(v, Variable)]
    aliases = _aggregate_alias_vars(alg)
    non_grouped = [v for v in pv if v not in group_vars and v not in aliases]
    if non_grouped:
        return AntipatternIssue(
            code="AP05",
            message=f"Variable(s) {non_grouped} projected but not in GROUP BY and not aggregated.",
            hint="Add missing variables to GROUP BY or wrap them in an aggregate.",
        )
    return None


def _ap09_unbound_projected_vars(alg: CompValue) -> Optional[AntipatternIssue]:
    """Projected variables that are never bound in the query body."""
    pv = {str(v) for v in (alg.get("PV") or []) if isinstance(v, Variable)}
    if not pv:
        return None
    # Bound = vars appearing in BGP triples + aggregate aliases
    bound = _bgp_bound_vars(alg) | _aggregate_alias_vars(alg)
    unbound = pv - bound
    if unbound:
        return AntipatternIssue(
            code="AP09",
            message=f"Projected variable(s) {sorted(unbound)} are never bound in the query body.",
            hint="Remove the unbound variable(s) from SELECT or add patterns that bind them.",
        )
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _ap06_aggregate_in_filter(alg: CompValue) -> Optional[AntipatternIssue]:
    """Aggregate used in FILTER instead of HAVING."""
    for node in _walk_algebra(alg):
        if node.name == "Filter":
            # Check if the filter expression contains any Aggregate_* node
            expr = node.get("expr")
            if expr is not None:
                for expr_node in _walk_algebra(expr):
                    if isinstance(expr_node, CompValue) and expr_node.name.startswith(
                        "Aggregate_"
                    ):
                        return AntipatternIssue(
                            code="AP06",
                            message="Aggregate function used in FILTER instead of HAVING.",
                            hint="Move the aggregate condition to a HAVING clause.",
                        )
    return None


def _ap07_alias_reference_in_select(alg: CompValue) -> Optional[AntipatternIssue]:
    """SELECT alias referenced in the same SELECT clause.

    Detected when an Extend's expression references an __agg_N__ variable that
    is itself a Aggregate_Sample of a projected alias variable (i.e. rdflib's
    encoding of a forward reference to another SELECT alias).
    """
    # Build map: __agg_N__ -> original var for Aggregate_Sample entries
    sample_map: dict[str, str] = {}  # __agg_N__ -> sampled var name
    for node in _walk_algebra(alg):
        if node.name == "AggregateJoin":
            for agg in node.get("A") or []:
                if agg.name == "Aggregate_Sample":
                    res = agg.get("res")
                    var = agg.get("vars")
                    if isinstance(res, Variable) and isinstance(var, Variable):
                        sample_map[str(res)] = str(var)

    if not sample_map:
        return None

    # Projected alias names (non-aggregate, i.e. defined via Extend from __agg_N__)
    alias_names: set[str] = set()
    for node in _walk_algebra(alg):
        if node.name == "Extend":
            var = node.get("var")
            expr = node.get("expr")
            if isinstance(var, Variable) and isinstance(expr, Variable):
                if str(expr).startswith("__agg_") and str(expr) not in sample_map:
                    alias_names.add(str(var))

    # Check if any Extend's expr references an __agg_N__ that is a Sample of an alias
    for node in _walk_algebra(alg):
        if node.name == "Extend":
            expr = node.get("expr")
            if expr is None:
                continue
            expr_vars = {
                str(v)
                for v in (getattr(expr, "_vars", None) or set())
                if isinstance(v, Variable)
            }
            for agg_var in expr_vars:
                sampled = sample_map.get(agg_var)
                if sampled and sampled in alias_names:
                    return AntipatternIssue(
                        code="AP07",
                        message="SELECT alias referenced within the same SELECT clause.",
                        hint="Aliases defined in SELECT cannot be referenced in the same SELECT; use a subquery.",
                    )
    return None


def _ap08_vendor_syntax(text: str, parsed: object) -> Optional[AntipatternIssue]:
    """Query parses but fails algebra translation — likely vendor-specific extension."""
    import rdflib.plugins.sparql.algebra as _alg

    try:
        _alg.translateQuery(parsed)
        return None
    except Exception as exc:
        return AntipatternIssue(
            code="AP08",
            message=f"Query uses non-standard or vendor-specific syntax: {exc}",
            hint="Remove or replace vendor-specific functions/extensions for portability.",
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DETECTORS = [
    _ap01_order_limit1,
    _ap02_distinct_with_aggregation,
    _ap04_cartesian_product,
    _ap06_aggregate_in_filter,
    _ap07_alias_reference_in_select,
    _ap09_unbound_projected_vars,
]

_DETECTORS_WITH_PARSED = [
    _ap03_proj_var_agg_no_groupby,
    _ap05_non_grouped_vars,
]


def detect_antipatterns(text: str, parsed: object = None) -> List[AntipatternIssue]:
    """Detect SPARQL antipatterns in *text*.

    If *parsed* is not provided, the query is parsed internally.
    Returns an empty list if the query cannot be parsed.

    Note: rdflib mutates the parse tree during translateQuery, so we always
    re-parse a fresh copy of the text before translating to algebra.

    Detected antipatterns
    ---------------------
    AP01  ORDER BY + LIMIT 1 for extrema (use MIN/MAX instead)
    AP02  DISTINCT with aggregation (redundant/misleading)
    AP03  Non-aggregate projected variable alongside aggregate without GROUP BY
    AP04  Cartesian product (disconnected BGP components)
    AP05  Projected variable not in GROUP BY and not aggregated
    AP06  Aggregate used in FILTER instead of HAVING
    AP07  SELECT alias referenced in the same SELECT clause
    AP08  Non-standard/vendor-specific syntax (algebra translation fails)
    AP09  Projected variable never bound in the query body
    """
    # Always parse fresh — rdflib mutates the parse tree during translateQuery
    ok, fresh_parsed, _ = parse_query(text)
    if not ok:
        return []

    # AP08: check before translation (translation itself may fail)
    ap08 = _ap08_vendor_syntax(text, fresh_parsed)
    if ap08:
        return [ap08]

    # Re-parse for translation (AP08 check above may have mutated fresh_parsed)
    _, alg_parsed, _ = parse_query(text)

    try:
        alg = _sparql_algebra.translateQuery(alg_parsed).algebra
    except Exception:
        return []

    issues: List[AntipatternIssue] = []
    for detector in _DETECTORS:
        issue = detector(alg)
        if issue:
            issues.append(issue)
    for detector in _DETECTORS_WITH_PARSED:
        issue = detector(alg, alg_parsed)
        if issue:
            issues.append(issue)

    return issues
