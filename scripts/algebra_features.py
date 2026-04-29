"""
SPARQL algebra inspection utilities for the NL2SPARQL question-type classifier.
"""

import logging
from typing import Set, Tuple

from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.term import Variable as _Variable

__all__ = [
    "is_user_bind",
    "detect_features_from_algebra",
    "count_from_algebra",
]

_log = logging.getLogger(__name__)


def is_user_bind(node: CompValue, parsed: object) -> bool:
    """
    Return True iff this Extend node corresponds to a user-written BIND.

    rdflib emits Extend nodes for two distinct cases:
      1. User-written BIND(?expr AS ?var)       → should emit lsqv:Bind
      2. Projection alias (e.g. COUNT(...) AS ?result) → must NOT emit lsqv:Bind

    The two cases produce structurally different entries in the parse tree's
    projection list:

      Real projected variable  → keys=['var'],        var=Variable(...),
                                 evar and expr are bare string sentinels
      Projection alias         → keys=['expr', 'evar'], evar=Variable(...),
                                 var is the bare string sentinel 'var'

    The correct discriminator is therefore whether `evar` is a real rdflib
    Variable instance (alias) versus the string sentinel 'evar' (plain var).

    If the Extend target variable matches any alias → NOT a BIND.
    Otherwise → treat as BIND.

    Note: this method is the *fallback* path in detect_features_from_algebra.
    It is reached only when the primary structural check (child is AggregateJoin
    or Group) has already been bypassed — i.e. for Extend nodes whose immediate
    subtree (after unwrapping any Filter nodes) is not an aggregate node.
    For aggregate aliases whose Extend wraps *another* Extend before reaching
    AggregateJoin (rdflib's multi-aggregate nesting), the primary check fires
    on the inner Extend; this method handles the outer one.
    """
    try:
        query_clause = parsed[1]
        projection = query_clause.get("projection", []) if hasattr(query_clause, "get") else []

        # Collect alias target names: entries where evar is a real Variable,
        # not the string sentinel 'evar' that pyparsing uses for absent keys.
        alias_vars = {
            str(item.get("evar"))
            for item in projection
            if hasattr(item, "get")
            and isinstance(item.get("evar"), _Variable)
        }

        var = node.get("var")
        if not isinstance(var, _Variable):
            return False

        return str(var) not in alias_vars

    except Exception as e:
        # Log the failure and conservatively treat this as NOT a user BIND.
        _log.debug(f"_is_user_bind failed: {e}")
        return False


def detect_features_from_algebra(
    algebra_node: CompValue,
    parsed: object,
) -> Set[str]:
    """
    Recursively walk a rdflib SPARQL algebra tree and return the set of
    LSQ feature names that can be determined unambiguously from the
    algebra structure and parse tree.

    Node name → LSQ feature mapping
    --------------------------------
    ToMultiSet { p: Project { … } }  → SubQuery
        rdflib wraps every inline SELECT in ToMultiSet(Project(…)).
        A bare GROUP / BGP inside ToMultiSet is NOT a subquery.

    Filter, discriminated by expr.name and HAVING context:
        TrueFilter          → (skip) synthetic no-op rdflib inserts for
                              OPTIONAL; not a user-written construct.
        Builtin_NOTEXISTS   → NotExists  (FILTER NOT EXISTS { … })
        Builtin_EXISTS      → fn-exists  (FILTER EXISTS { … })
        anything else       → Filter     (plain FILTER expression)

        HAVING special case: rdflib compiles HAVING(expr) into a Filter
        node wrapping the AggregateJoin/Group subtree. HAVING is detected
        from the parse tree (parsed[1]['having'] is a CompValue when
        present). When detected:
            - lsqv:Having is added to the feature set.
            - The outermost Filter (the HAVING) is skipped so it does
              not also produce a spurious lsqv:Filter.

    Extend → Bind (user-written) or skipped (projection alias)
        rdflib compiles BIND(?expr AS ?var) and projection aliases such
        as COUNT(?x) AS ?count both into Extend nodes.

        Classification strategy (two layers):

        Layer 1 — structural child check (primary, fast):
            After unwrapping any Filter nodes between this Extend and its
            aggregate subtree, if the immediate child is AggregateJoin or
            Group, this Extend is an aggregate alias.  In that case Bind is
            NOT emitted.  Crucially, execution does NOT return early — it
            falls through to the normal recursion loop so that any real
            BIND nodes nested *inside* the aggregate subtree (e.g. a BIND
            used in a GROUP BY expression compiled inside
            AggregateJoin → Group → Extend(BGP)) are still visited and
            correctly classified.

            rdflib's algebra for multiple aggregates nests Extend nodes:
                Extend(alias_n) → Extend(alias_n-1) → … → AggregateJoin
            Only the innermost Extend sees AggregateJoin as its direct
            child; outer Extend nodes see another Extend.  Those outer
            nodes therefore bypass Layer 1 and are handled by Layer 2.

        Layer 2 — parse-tree alias check (fallback, is_user_bind):
            Inspects the SELECT projection list.  Projection aliases have
            evar=Variable(...); plain projected variables have the string
            sentinel 'evar'.  An Extend whose target variable appears as
            a projection alias is NOT a BIND; otherwise it is.

    LeftJoin → Optional
        rdflib compiles OPTIONAL { … } into LeftJoin nodes.

    Only CompValue nodes are visited; all other value types are ignored.
    A seen-set guards against cycles.
    """
    found: Set[str] = set()
    seen: Set[int] = set()

    def _expr_contains(node, target: str) -> bool:
        """
        Recursively search for a CompValue node with name == target.
        Handles nested CompValue, list, and tuple structures.
        """
        if isinstance(node, CompValue):
            if getattr(node, "name", None) == target:
                return True
            for v in node.values():
                if _expr_contains(v, target):
                    return True
        elif isinstance(node, (list, tuple)):
            for item in node:
                if _expr_contains(item, target):
                    return True
        return False

    # Detect HAVING from the parse tree. parsed[1]['having'] is a CompValue
    # when HAVING is present; rdflib sets it to the bare string 'having' as
    # a sentinel when it is absent.
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
        node_id = id(node)
        if node_id in seen:
            return
        seen.add(node_id)

        name = node.name

        if name == "ToMultiSet":
            inner = node.get("p")
            if isinstance(inner, CompValue) and inner.name == "Project":
                found.add("SubQuery")

        elif name == "Filter":
            expr = node.get("expr")

            # Identify HAVING filter structurally (Filter wrapping AggregateJoin/Group).
            # Unwrap Extend nodes between this Filter and the aggregate root —
            # rdflib places the HAVING Filter above inner Extend aliases when
            # there are multiple aggregate projections.
            child = node.get("p")
            _c = child
            while isinstance(_c, CompValue) and _c.name == "Extend":
                _c = _c.get("p")
            is_having_filter = (
                has_having
                and isinstance(_c, CompValue)
                and _c.name in {"AggregateJoin", "Group"}
            )

            if is_having_filter:
                pass  # skip — already recorded as Having above

            elif expr is not None:
                expr_name = getattr(expr, "name", None)

                if expr_name == "Builtin_NOTEXISTS":
                    found.add("NotExists")
                elif expr_name == "Builtin_EXISTS":
                    found.add("fn-exists")
                elif expr_name == "TrueFilter":
                    pass  # synthetic filter for OPTIONAL, not user-written
                else:
                    found.add("Filter")

        elif name == "Extend":
            _log.debug(f"Extend node found: {node}")

            child = node.get("p")

            # STEP 1: unwrap Filter nodes that sit between this Extend and
            # the aggregate subtree (rdflib places the HAVING Filter between
            # the outermost Extend and AggregateJoin when HAVING is present).
            while isinstance(child, CompValue) and child.name in {"Filter", "Extend"}:
                _log.debug(f"Unwrapping {child.name} node under Extend")
                child = child.get("p")

            # STEP 2: structural child check — primary path for aggregate aliases.
            # When the immediate child (after Filter unwrapping) is AggregateJoin
            # or Group, this Extend is an aggregate alias; do NOT emit Bind.
            #
            # Important: do NOT `return` here.  rdflib nests multiple aggregate
            # aliases as a chain of Extend nodes (outermost alias → inner alias
            # → … → AggregateJoin).  Only the innermost Extend in the chain has
            # AggregateJoin as its direct child; outer aliases have another Extend.
            # Those outer Extend nodes fall through to is_user_bind (Step 3),
            # which correctly identifies them as aliases via the projection list.
            #
            # Additionally, `return` would skip the recursion loop below and
            # prevent visiting genuine BIND nodes that appear inside the aggregate
            # subtree (e.g. BIND used in a GROUP BY expression compiled inside
            # AggregateJoin → Group → Extend(BGP)).  Falling through ensures
            # the full subtree is always visited.
            if isinstance(child, CompValue) and child.name in {"AggregateJoin", "Group"}:
                _log.debug(
                    "Extend → innermost aggregation alias (NOT Bind); "
                    "continuing recursion into subtree"
                )
                # Fall through to the recursion loop — do NOT return.

            else:
                # STEP 3: fallback parse-tree alias check for outer aggregate
                # Extend nodes (multi-aggregate chains) and for real user BINDs.
                is_bind = is_user_bind(node, parsed)

                if is_bind:
                    found.add("Bind")
                    _log.debug("Extend → Bind")
                else:
                    _log.debug("Extend → NOT Bind (aggregate alias)")

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


def count_from_algebra(algebra_node: CompValue) -> Tuple[int, int, int]:
    """
    Walk the algebra tree and return (bgp_count, tp_count, proj_var_count).

    bgp_count      — number of distinct BGP nodes in the tree (including
                     those inside subqueries, matching LSQ's own counting).
    tp_count       — total number of triple patterns across all BGP nodes.
    proj_var_count — number of variables in the top-level SELECT projection
                     (the PV list on the root SelectQuery node), or 0 for
                     ASK / CONSTRUCT / DESCRIBE queries.

                     Note: rdflib populates PV on AskQuery with all pattern
                     variables, not projected ones. Returning 0 for non-
                     SELECT roots prevents a spurious mismatch against
                     lsqv:projectVarCount, and the check is silently skipped
                     in _check_count_annotations when the computed value is 0
                     and no declared value is present.

    Only CompValue nodes are visited; a seen-set guards against cycles.
    """
    bgp_count = 0
    tp_count = 0
    seen: Set[int] = set()

    def _walk(node: CompValue) -> None:
        nonlocal bgp_count, tp_count
        if not isinstance(node, CompValue):
            return
        node_id = id(node)
        if node_id in seen:
            return
        seen.add(node_id)

        if node.name == "BGP":
            bgp_count += 1
            triples = node.get("triples") or []
            tp_count += len(triples)

        for value in node.values():
            if isinstance(value, CompValue):
                _walk(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, CompValue):
                        _walk(item)

    _walk(algebra_node)

    # Projection variable count is only meaningful for SELECT queries.
    if algebra_node.name == "SelectQuery":
        pv = algebra_node.get("PV") or []
        proj_var_count = len(pv)
    else:
        proj_var_count = 0

    return bgp_count, tp_count, proj_var_count
