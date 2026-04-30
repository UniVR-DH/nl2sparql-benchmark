"""Tests for sparql_annotator.antipatterns."""
import pytest
from sparql_annotator.antipatterns import detect_antipatterns


def _codes(text):
    return {i.code for i in detect_antipatterns(text)}


# ---------------------------------------------------------------------------
# AP01 — ORDER BY + LIMIT 1
# ---------------------------------------------------------------------------

def test_ap01_order_limit1():
    assert "AP01" in _codes(
        "SELECT ?x WHERE { ?x <http://x.org/v> ?v } ORDER BY DESC(?v) LIMIT 1"
    )


def test_ap01_order_limit_gt1_no_flag():
    assert "AP01" not in _codes(
        "SELECT ?x WHERE { ?x <http://x.org/v> ?v } ORDER BY DESC(?v) LIMIT 10"
    )


def test_ap01_no_order_no_flag():
    assert "AP01" not in _codes(
        "SELECT ?x WHERE { ?x ?p ?o } LIMIT 1"
    )


# ---------------------------------------------------------------------------
# AP02 — DISTINCT with aggregation
# ---------------------------------------------------------------------------

def test_ap02_distinct_with_agg():
    assert "AP02" in _codes(
        "SELECT DISTINCT (COUNT(?x) AS ?c) WHERE { ?x a <http://x.org/T> }"
    )


def test_ap02_distinct_no_agg_no_flag():
    assert "AP02" not in _codes(
        "SELECT DISTINCT ?x WHERE { ?x ?p ?o }"
    )


def test_ap02_agg_no_distinct_no_flag():
    assert "AP02" not in _codes(
        "SELECT (COUNT(?x) AS ?c) WHERE { ?x a <http://x.org/T> }"
    )


# ---------------------------------------------------------------------------
# AP03 — projected var + aggregate without GROUP BY
# ---------------------------------------------------------------------------

def test_ap03_proj_var_agg_no_groupby():
    assert "AP03" in _codes(
        "SELECT ?x (COUNT(?y) AS ?c) WHERE { ?x <http://x.org/p> ?y }"
    )


def test_ap03_with_groupby_no_flag():
    assert "AP03" not in _codes(
        "SELECT ?x (COUNT(?y) AS ?c) WHERE { ?x <http://x.org/p> ?y } GROUP BY ?x"
    )


def test_ap03_pure_agg_no_flag():
    assert "AP03" not in _codes(
        "SELECT (COUNT(?x) AS ?c) WHERE { ?x a <http://x.org/T> }"
    )


# ---------------------------------------------------------------------------
# AP05 — Cartesian product
# ---------------------------------------------------------------------------

def test_ap05_cartesian():
    assert "AP05" in _codes(
        "SELECT ?x ?y WHERE { ?x a <http://x.org/Person> . ?y a <http://x.org/City> }"
    )


def test_ap05_joined_no_flag():
    assert "AP05" not in _codes(
        "SELECT ?x ?y WHERE { ?x a <http://x.org/Person> . ?x <http://x.org/lives> ?y }"
    )


def test_ap05_single_triple_no_flag():
    assert "AP05" not in _codes(
        "SELECT ?x WHERE { ?x a <http://x.org/T> }"
    )


# ---------------------------------------------------------------------------
# AP06 — non-grouped projected variable
# ---------------------------------------------------------------------------

def test_ap06_non_grouped_var():
    assert "AP06" in _codes(
        "SELECT ?x ?z (COUNT(?y) AS ?c) WHERE { ?x <http://x.org/p> ?y ; <http://x.org/q> ?z } GROUP BY ?x"
    )


def test_ap06_all_grouped_no_flag():
    assert "AP06" not in _codes(
        "SELECT ?x ?z (COUNT(?y) AS ?c) WHERE { ?x <http://x.org/p> ?y ; <http://x.org/q> ?z } GROUP BY ?x ?z"
    )


# ---------------------------------------------------------------------------
# AP11 — unbound projected variable
# ---------------------------------------------------------------------------

def test_ap11_unbound_proj():
    assert "AP11" in _codes(
        "SELECT ?x ?y WHERE { ?x a <http://x.org/T> }"
    )


def test_ap11_all_bound_no_flag():
    assert "AP11" not in _codes(
        "SELECT ?x ?y WHERE { ?x a <http://x.org/T> . ?x <http://x.org/p> ?y }"
    )


# ---------------------------------------------------------------------------
# Clean query — no antipatterns
# ---------------------------------------------------------------------------

def test_clean_query_no_issues():
    assert _codes(
        "SELECT ?x ?y WHERE { ?x <http://x.org/p> ?y . ?y a <http://x.org/T> }"
    ) == set()


# ---------------------------------------------------------------------------
# Invalid query — no crash
# ---------------------------------------------------------------------------

def test_invalid_query_returns_empty():
    assert detect_antipatterns("NOT SPARQL") == []


# ---------------------------------------------------------------------------
# Multiple antipatterns in one query
# ---------------------------------------------------------------------------

def test_multiple_antipatterns():
    # ORDER BY LIMIT 1 + unbound var
    codes = _codes(
        "SELECT ?x ?z WHERE { ?x <http://x.org/v> ?v } ORDER BY DESC(?v) LIMIT 1"
    )
    assert "AP01" in codes
    assert "AP11" in codes
