"""Structural metrics and helpers for SPARQL queries.

This module provides lightweight implementations of the M2.5 metrics:

  - bgp_count:          number of BGP nodes in the algebra tree
  - tp_count:           total number of triple patterns across all BGPs
  - project_var_count:  number of projected variables in SELECT
  - referenced_terms:   set of IRI strings referenced in the query

Implementation strategy
-----------------------
All metrics are derived by walking the rdflib algebra tree produced by
``translateQuery(parseQuery(text))``.  No regex is applied to the algebra
repr or to the raw query text for structural metrics.

A text-based fallback is provided only for ``project_var_count`` when the
query cannot be parsed at all, and is clearly labelled as approximate.
``referenced_terms`` uses angle-bracket extraction on the raw text, which
is intentionally pragmatic (see docstring).
"""

from __future__ import annotations

import re
from typing import Generator, Set, Tuple, Optional

from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.plugins.sparql import algebra as _algebra


# ---------------------------------------------------------------------------
# Internal algebra walker
# ---------------------------------------------------------------------------

def _walk(node: object) -> Generator[object, None, None]:
    """
    Yield every node in the rdflib algebra tree via depth-first traversal.

    rdflib algebra nodes are ``CompValue`` instances (dict subclasses) whose
    values may themselves be algebra nodes, lists of nodes, or plain values.
    """
    yield node
    if isinstance(node, _algebra.CompValue):
        for value in node.values():
            if isinstance(value, _algebra.CompValue):
                yield from _walk(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, _algebra.CompValue):
                        yield from _walk(item)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_structural_metrics(query_text: str) -> Tuple[int, int, int]:
    """Return ``(bgp_count, tp_count, project_var_count)`` for *query_text*.

    All three metrics are derived from the rdflib algebra tree:

    ``bgp_count``
        Number of ``BGP`` nodes in the algebra.  Each BGP represents a
        maximal set of triple patterns that share the same graph context and
        are not separated by OPTIONAL / UNION / FILTER boundaries.

    ``tp_count``
        Total number of triple patterns summed across all BGP nodes.  Each
        entry in ``BGP.triples`` is one triple pattern.

    ``project_var_count``
        Number of variables listed in the ``Project.PV`` attribute.  For
        ``SELECT *`` this will be 0 (rdflib does not populate ``PV`` for
        wildcard projections); callers should treat 0 as "all variables".
        For ASK queries this is always 0.

    If the query cannot be parsed, all three values are 0 and a
    ``ValueError`` is raised so the caller can decide how to handle it.
    """
    try:
        parsed = parseQuery(query_text)
        alg = translateQuery(parsed).algebra
    except Exception as exc:
        raise ValueError(f"Could not parse query: {exc}") from exc

    bgp_count = 0
    tp_count = 0
    project_var_count = 0

    for node in _walk(alg):
        name = getattr(node, "name", None)

        if name == "BGP":
            bgp_count += 1
            triples = node.get("triples", [])
            tp_count += len(triples)

        elif name == "Project":
            pv = node.get("PV", [])
            project_var_count = len(pv)

    return bgp_count, tp_count, project_var_count


def referenced_terms(query_text: str) -> Set[str]:
    """Return the set of IRIs explicitly written in angle brackets in *query_text*.

    This is intentionally a text-level extraction: it finds every ``<iri>``
    token in the raw query string.  It does *not* resolve prefixed names
    (e.g. ``dbo:Person``) because doing so requires the prefix declarations
    present in the query header, which vary per query.  Callers that need
    fully resolved IRIs should parse the query with rdflib and inspect the
    algebra's ``initNs`` mapping together with the prefixed names.

    Angle-bracket IRIs cover the common case of explicit resource references
    and are sufficient for the M2.5 term-coverage metric.
    """
    return set(re.findall(r"<([^>]+)>", query_text))


def project_var_count_fallback(query_text: str) -> int:
    """Approximate projected variable count from raw text.

    Used only when ``compute_structural_metrics`` raises (i.e. the query is
    syntactically invalid).  Counts ``?var`` tokens between ``SELECT`` and
    ``WHERE``; will overcount if aggregate expressions contain variables, and
    will return 0 for ``SELECT *``.  Callers should treat this as a best-effort
    estimate and log a warning.
    """
    m = re.search(r"SELECT\s+(?:DISTINCT\s+|REDUCED\s+)?(.+?)\s+WHERE", query_text, re.I | re.S)
    if not m:
        return 0
    return len(re.findall(r"\?\w+", m.group(1)))