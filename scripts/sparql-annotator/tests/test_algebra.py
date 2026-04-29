"""Tests for sparql_annotator.algebra — complete coverage."""
import pytest
import rdflib.plugins.sparql.algebra as _a

from sparql_annotator.algebra import (
    parse_query, extract_operators, detect_lsq_features, compute_metrics, referenced_terms
)


def _alg(text):
    ok, parsed, err = parse_query(text)
    assert ok, err
    return _a.translateQuery(parsed).algebra, parsed


def _ops(text):
    ok, parsed, err = parse_query(text)
    assert ok, err
    return extract_operators(text, parsed)


def _lsq(text):
    alg, parsed = _alg(text)
    return detect_lsq_features(alg, parsed)


# ---------------------------------------------------------------------------
# parse_query
# ---------------------------------------------------------------------------

def test_parse_valid():
    ok, parsed, err = parse_query("SELECT ?s WHERE { ?s ?p ?o }")
    assert ok and parsed is not None and err is None


def test_parse_invalid():
    ok, parsed, err = parse_query("NOT SPARQL AT ALL")
    assert not ok and parsed is None and err is not None


# ---------------------------------------------------------------------------
# compute_metrics
# ---------------------------------------------------------------------------

def test_metrics_select_one_triple():
    bgp, tp, pv = compute_metrics(_alg("SELECT ?s WHERE { ?s ?p ?o }")[0])
    assert (bgp, tp, pv) == (1, 1, 1)


def test_metrics_select_two_triples():
    bgp, tp, pv = compute_metrics(_alg("SELECT ?s ?p WHERE { ?s ?p ?o . ?s a <http://x.org/C> }")[0])
    assert bgp == 1 and tp == 2 and pv == 2


def test_metrics_ask_pv_zero():
    bgp, tp, pv = compute_metrics(_alg("ASK { ?s ?p ?o }")[0])
    assert bgp == 1 and tp == 1 and pv == 0


def test_metrics_subquery_counts_inner_bgp():
    # subquery adds a second BGP
    text = "SELECT ?s WHERE { ?s ?p ?o . { SELECT ?x WHERE { ?x a <http://x.org/C> } } }"
    bgp, tp, pv = compute_metrics(_alg(text)[0])
    assert bgp == 2


# ---------------------------------------------------------------------------
# compute_metrics — BGP counting with OPTIONAL, UNION, subquery, FILTER NOT EXISTS
# ---------------------------------------------------------------------------

def test_metrics_optional_adds_bgp():
    # OPTIONAL { … } compiles to LeftJoin(BGP, BGP) → 2 BGPs
    text = "SELECT ?s WHERE { ?s ?p ?o . OPTIONAL { ?s <http://x.org/y> ?z } }"
    bgp, tp, pv = compute_metrics(_alg(text)[0])
    assert bgp == 2 and tp == 2


def test_metrics_nested_optional_adds_bgp():
    # Two nested OPTIONALs → 3 BGPs
    text = "SELECT ?s WHERE { ?s ?p ?o . OPTIONAL { ?s <http://x.org/y> ?z . OPTIONAL { ?z <http://x.org/w> ?v } } }"
    bgp, tp, pv = compute_metrics(_alg(text)[0])
    assert bgp == 3 and tp == 3


def test_metrics_union_adds_bgp():
    # UNION { A } { B } → 2 BGPs (one per branch)
    text = "SELECT ?s WHERE { { ?s a <http://x.org/A> } UNION { ?s a <http://x.org/B> } }"
    bgp, tp, pv = compute_metrics(_alg(text)[0])
    assert bgp == 2 and tp == 2


def test_metrics_union_plus_outer_bgp():
    # outer BGP + UNION with 2 branches → 3 BGPs
    text = "SELECT ?s WHERE { ?s ?p ?o . { ?s a <http://x.org/A> } UNION { ?s a <http://x.org/B> } }"
    bgp, tp, pv = compute_metrics(_alg(text)[0])
    assert bgp == 3 and tp == 3


def test_metrics_filter_not_exists_counts_inner_bgp():
    # Per SPARQL 1.1 §18: inner pattern of FILTER NOT EXISTS is a separate BGP
    text = "SELECT ?s WHERE { ?s ?p ?o . FILTER NOT EXISTS { ?s a <http://x.org/Bad> } }"
    bgp, tp, pv = compute_metrics(_alg(text)[0])
    assert bgp == 2 and tp == 2


def test_metrics_filter_not_exists_multi_triple_inner():
    # Inner BGP with 2 triples
    text = "SELECT ?s WHERE { ?s ?p ?o . FILTER NOT EXISTS { ?s a <http://x.org/Bad> . ?s <http://x.org/p> ?v } }"
    bgp, tp, pv = compute_metrics(_alg(text)[0])
    assert bgp == 2 and tp == 3  # outer: 1 tp, inner: 2 tp


def test_metrics_filter_exists_counts_inner_bgp():
    text = "SELECT ?s WHERE { ?s ?p ?o . FILTER EXISTS { ?s a <http://x.org/Good> } }"
    bgp, tp, pv = compute_metrics(_alg(text)[0])
    assert bgp == 2 and tp == 2


def test_metrics_subquery_plus_optional():
    # outer BGP + OPTIONAL BGP + subquery BGP → 3
    text = "SELECT ?s WHERE { ?s ?p ?o . OPTIONAL { ?s <http://x.org/y> ?z } . { SELECT ?x WHERE { ?x a <http://x.org/C> } } }"
    bgp, tp, pv = compute_metrics(_alg(text)[0])
    assert bgp == 3 and tp == 3


# ---------------------------------------------------------------------------
# detect_lsq_features — basic operators
# ---------------------------------------------------------------------------

def test_lsq_optional():
    assert "Optional" in _lsq("SELECT ?s WHERE { ?s ?p ?o . OPTIONAL { ?s <http://x.org/y> ?z } }")


def test_lsq_filter_plain():
    assert "Filter" in _lsq("SELECT ?s WHERE { ?s ?p ?o . FILTER(?o > 5) }")


def test_lsq_filter_not_exists():
    feats = _lsq("SELECT ?s WHERE { ?s ?p ?o . FILTER NOT EXISTS { ?s <http://x.org/y> ?z } }")
    assert "NotExists" in feats
    assert "Filter" not in feats


def test_lsq_filter_exists():
    feats = _lsq("SELECT ?s WHERE { ?s ?p ?o . FILTER EXISTS { ?s <http://x.org/y> ?z } }")
    assert "fn-exists" in feats
    assert "Filter" not in feats


def test_lsq_subquery():
    assert "SubQuery" in _lsq("SELECT ?s WHERE { { SELECT ?s WHERE { ?s ?p ?o } } }")


def test_lsq_bind_user():
    feats = _lsq("SELECT ?s ?v WHERE { ?s ?p ?o . BIND(str(?o) AS ?v) }")
    assert "Bind" in feats


# ---------------------------------------------------------------------------
# detect_lsq_features — HAVING bug fixes (q30, q36 regression tests)
# ---------------------------------------------------------------------------

def test_lsq_having_single_aggregate_no_spurious_filter():
    """q30-style: HAVING with one aggregate projection — must not emit Filter."""
    text = """SELECT ?name (COUNT(?emp) AS ?n)
WHERE { ?dept <http://x.org/name> ?name . ?emp <http://x.org/memberOf> ?dept }
GROUP BY ?dept ?name
HAVING (COUNT(?emp) > 5)"""
    feats = _lsq(text)
    assert "Having" in feats
    assert "Filter" not in feats


def test_lsq_having_multi_aggregate_no_spurious_filter():
    """q30-style with multiple projections — HAVING Filter sits above inner Extend."""
    text = """SELECT ?dept ?name (COUNT(?emp) AS ?n)
WHERE { ?dept <http://x.org/name> ?name . ?emp <http://x.org/memberOf> ?dept }
GROUP BY ?dept ?name
HAVING (COUNT(?emp) > 2)"""
    feats = _lsq(text)
    assert "Having" in feats
    assert "Filter" not in feats


def test_lsq_group_by_multi_projection_no_spurious_bind():
    """q36-style: GROUP BY with multiple projected vars — must not emit Bind."""
    text = """SELECT ?cat ?name
WHERE { ?hw <http://x.org/cat> ?cat . ?cat <http://x.org/name> ?name }
GROUP BY ?cat ?name
ORDER BY DESC(COUNT(*))
LIMIT 3"""
    feats = _lsq(text)
    assert "Bind" not in feats


def test_lsq_real_bind_detected():
    """A real user BIND must still be detected even alongside aggregates."""
    text = """SELECT ?s ?v (COUNT(?o) AS ?c)
WHERE { ?s ?p ?o . BIND(str(?s) AS ?v) }
GROUP BY ?s ?v"""
    feats = _lsq(text)
    assert "Bind" in feats


# ---------------------------------------------------------------------------
# extract_operators — query forms
# ---------------------------------------------------------------------------

def test_ops_select_form():
    assert _ops("SELECT ?s WHERE { ?s ?p ?o }").query_form == "SELECT"


def test_ops_ask_form():
    assert _ops("ASK { ?s ?p ?o }").query_form == "ASK"


def test_ops_construct_form():
    assert _ops("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }").query_form == "CONSTRUCT"


# ---------------------------------------------------------------------------
# extract_operators — graph patterns
# ---------------------------------------------------------------------------

def test_ops_optional():
    assert "OPTIONAL" in _ops("SELECT ?s WHERE { ?s ?p ?o . OPTIONAL { ?s <http://x.org/y> ?z } }").graph_patterns


def test_ops_union():
    assert "UNION" in _ops("SELECT ?s WHERE { { ?s a <http://x.org/A> } UNION { ?s a <http://x.org/B> } }").graph_patterns


def test_ops_minus():
    assert "MINUS" in _ops("SELECT ?s WHERE { ?s ?p ?o . MINUS { ?s a <http://x.org/Bad> } }").graph_patterns


# ---------------------------------------------------------------------------
# extract_operators — filters
# ---------------------------------------------------------------------------

def test_ops_filter_plain():
    assert "FILTER" in _ops("SELECT ?s WHERE { ?s ?p ?o . FILTER(?o > 5) }").filters


def test_ops_filter_not_exists():
    assert "FILTER NOT EXISTS" in _ops("SELECT ?s WHERE { ?s ?p ?o . FILTER NOT EXISTS { ?s <http://x.org/y> ?z } }").filters


def test_ops_filter_exists():
    assert "FILTER EXISTS" in _ops("SELECT ?s WHERE { ?s ?p ?o . FILTER EXISTS { ?s <http://x.org/y> ?z } }").filters


# ---------------------------------------------------------------------------
# extract_operators — aggregates
# ---------------------------------------------------------------------------

def test_ops_count():
    assert "COUNT" in _ops("SELECT (COUNT(?s) AS ?c) WHERE { ?s ?p ?o }").aggregates


def test_ops_sum():
    assert "SUM" in _ops("SELECT (SUM(?o) AS ?total) WHERE { ?s <http://x.org/val> ?o }").aggregates


def test_ops_avg():
    assert "AVG" in _ops("SELECT (AVG(?o) AS ?avg) WHERE { ?s <http://x.org/val> ?o }").aggregates


def test_ops_min_max():
    ops = _ops("SELECT (MIN(?o) AS ?mn) (MAX(?o) AS ?mx) WHERE { ?s <http://x.org/val> ?o }")
    assert "MIN" in ops.aggregates and "MAX" in ops.aggregates


# ---------------------------------------------------------------------------
# extract_operators — solution modifiers
# ---------------------------------------------------------------------------

def test_ops_group_by():
    assert "GROUP BY" in _ops("SELECT ?p (COUNT(?s) AS ?c) WHERE { ?s ?p ?o } GROUP BY ?p").solution_modifiers


def test_ops_having():
    assert "HAVING" in _ops("SELECT ?p (COUNT(?s) AS ?c) WHERE { ?s ?p ?o } GROUP BY ?p HAVING (COUNT(?s) > 1)").solution_modifiers


def test_ops_order_limit_offset():
    ops = _ops("SELECT ?s WHERE { ?s ?p ?o } ORDER BY ?s LIMIT 10 OFFSET 5")
    assert "ORDER BY" in ops.solution_modifiers
    assert "LIMIT" in ops.solution_modifiers
    assert "OFFSET" in ops.solution_modifiers


# ---------------------------------------------------------------------------
# extract_operators — projection modifiers
# ---------------------------------------------------------------------------

def test_ops_distinct():
    assert "DISTINCT" in _ops("SELECT DISTINCT ?s WHERE { ?s ?p ?o }").projection_modifiers


def test_ops_reduced():
    assert "REDUCED" in _ops("SELECT REDUCED ?s WHERE { ?s ?p ?o }").projection_modifiers


# ---------------------------------------------------------------------------
# extract_operators — assignments
# ---------------------------------------------------------------------------

def test_ops_bind():
    assert "BIND" in _ops("SELECT ?s ?v WHERE { ?s ?p ?o . BIND(str(?o) AS ?v) }").assignments


def test_ops_subquery():
    assert _ops("SELECT ?s WHERE { { SELECT ?s WHERE { ?s ?p ?o } } }").subqueries is True


# ---------------------------------------------------------------------------
# extract_operators — structural metrics
# ---------------------------------------------------------------------------

def test_ops_metrics_populated():
    ops = _ops("SELECT ?s ?p WHERE { ?s ?p ?o . ?s a <http://x.org/C> }")
    assert ops.bgp_count == 1 and ops.tp_count == 2 and ops.project_var_count == 2


def test_ops_metrics_ask_pv_zero():
    ops = _ops("ASK { ?s ?p ?o }")
    assert ops.project_var_count == 0


# ---------------------------------------------------------------------------
# extract_operators — invalid query returns empty OperatorSet
# ---------------------------------------------------------------------------

def test_ops_invalid_query_returns_empty():
    ok, parsed, _ = parse_query("NOT VALID SPARQL")
    assert not ok
    ops = extract_operators("NOT VALID SPARQL", None)
    assert ops.query_form == "UNKNOWN"
    assert not ops.raw


# ---------------------------------------------------------------------------
# referenced_terms
# ---------------------------------------------------------------------------

def test_referenced_terms():
    text = "SELECT ?s WHERE { ?s a <http://example.org/Class> . ?s <http://example.org/prop> ?o }"
    terms = referenced_terms(text)
    assert "http://example.org/Class" in terms
    assert "http://example.org/prop" in terms


def test_referenced_terms_empty():
    assert referenced_terms("SELECT ?s WHERE { ?s ?p ?o }") == set()
