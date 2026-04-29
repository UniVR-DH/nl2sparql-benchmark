"""Tests for sparql_annotator.algebra"""
import pytest
from sparql_annotator.algebra import (
    parse_query, extract_operators, detect_lsq_features, compute_metrics, referenced_terms
)


def _parse(text):
    ok, parsed, err = parse_query(text)
    assert ok, err
    return parsed


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

def test_metrics_simple_select():
    import rdflib.plugins.sparql.algebra as _a
    parsed = _parse("SELECT ?s WHERE { ?s ?p ?o }")
    alg = _a.translateQuery(parsed)
    bgp, tp, pv = compute_metrics(alg.algebra)
    assert bgp == 1
    assert tp == 1
    assert pv == 1


def test_metrics_ask():
    import rdflib.plugins.sparql.algebra as _a
    parsed = _parse("ASK { ?s ?p ?o }")
    alg = _a.translateQuery(parsed)
    bgp, tp, pv = compute_metrics(alg.algebra)
    assert bgp == 1
    assert tp == 1
    assert pv == 0  # non-SELECT


def test_metrics_two_triples():
    import rdflib.plugins.sparql.algebra as _a
    parsed = _parse("SELECT ?s ?p WHERE { ?s ?p ?o . ?s a <http://example.org/C> }")
    alg = _a.translateQuery(parsed)
    bgp, tp, pv = compute_metrics(alg.algebra)
    assert bgp == 1
    assert tp == 2
    assert pv == 2


# ---------------------------------------------------------------------------
# detect_lsq_features
# ---------------------------------------------------------------------------

def test_lsq_optional():
    import rdflib.plugins.sparql.algebra as _a
    text = "SELECT ?s WHERE { ?s ?p ?o . OPTIONAL { ?s <http://x.org/y> ?z } }"
    parsed = _parse(text)
    alg = _a.translateQuery(parsed)
    feats = detect_lsq_features(alg.algebra, parsed)
    assert "Optional" in feats


def test_lsq_filter():
    import rdflib.plugins.sparql.algebra as _a
    text = "SELECT ?s WHERE { ?s ?p ?o . FILTER(?o > 5) }"
    parsed = _parse(text)
    alg = _a.translateQuery(parsed)
    feats = detect_lsq_features(alg.algebra, parsed)
    assert "Filter" in feats


def test_lsq_subquery():
    import rdflib.plugins.sparql.algebra as _a
    text = "SELECT ?s WHERE { { SELECT ?s WHERE { ?s ?p ?o } } }"
    parsed = _parse(text)
    alg = _a.translateQuery(parsed)
    feats = detect_lsq_features(alg.algebra, parsed)
    assert "SubQuery" in feats


def test_lsq_filter_not_exists():
    import rdflib.plugins.sparql.algebra as _a
    text = "SELECT ?s WHERE { ?s ?p ?o . FILTER NOT EXISTS { ?s <http://x.org/y> ?z } }"
    parsed = _parse(text)
    alg = _a.translateQuery(parsed)
    feats = detect_lsq_features(alg.algebra, parsed)
    assert "NotExists" in feats


# ---------------------------------------------------------------------------
# extract_operators
# ---------------------------------------------------------------------------

def test_extract_select_form():
    parsed = _parse("SELECT ?s WHERE { ?s ?p ?o }")
    ops = extract_operators("SELECT ?s WHERE { ?s ?p ?o }", parsed)
    assert ops.query_form == "SELECT"


def test_extract_ask_form():
    parsed = _parse("ASK { ?s ?p ?o }")
    ops = extract_operators("ASK { ?s ?p ?o }", parsed)
    assert ops.query_form == "ASK"


def test_extract_optional():
    text = "SELECT ?s WHERE { ?s ?p ?o . OPTIONAL { ?s <http://x.org/y> ?z } }"
    parsed = _parse(text)
    ops = extract_operators(text, parsed)
    assert "OPTIONAL" in ops.graph_patterns


def test_extract_distinct():
    text = "SELECT DISTINCT ?s WHERE { ?s ?p ?o }"
    parsed = _parse(text)
    ops = extract_operators(text, parsed)
    assert "DISTINCT" in ops.projection_modifiers


def test_extract_metrics_populated():
    text = "SELECT ?s ?p WHERE { ?s ?p ?o . ?s a <http://example.org/C> }"
    parsed = _parse(text)
    ops = extract_operators(text, parsed)
    assert ops.bgp_count == 1
    assert ops.tp_count == 2
    assert ops.project_var_count == 2


def test_extract_group_by_having():
    text = "SELECT ?p (COUNT(?s) AS ?c) WHERE { ?s ?p ?o } GROUP BY ?p HAVING (COUNT(?s) > 1)"
    parsed = _parse(text)
    ops = extract_operators(text, parsed)
    assert "GROUP BY" in ops.solution_modifiers
    assert "HAVING" in ops.solution_modifiers


def test_extract_order_limit_offset():
    text = "SELECT ?s WHERE { ?s ?p ?o } ORDER BY ?s LIMIT 10 OFFSET 5"
    parsed = _parse(text)
    ops = extract_operators(text, parsed)
    assert "ORDER BY" in ops.solution_modifiers
    assert "LIMIT" in ops.solution_modifiers
    assert "OFFSET" in ops.solution_modifiers


# ---------------------------------------------------------------------------
# referenced_terms
# ---------------------------------------------------------------------------

def test_referenced_terms():
    text = "SELECT ?s WHERE { ?s a <http://example.org/Class> . ?s <http://example.org/prop> ?o }"
    terms = referenced_terms(text)
    assert "http://example.org/Class" in terms
    assert "http://example.org/prop" in terms
