"""Tests for sparql_annotator.antipatterns."""

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
    assert "AP01" not in _codes("SELECT ?x WHERE { ?x ?p ?o } LIMIT 1")


# ---------------------------------------------------------------------------
# AP02 — DISTINCT with aggregation
# ---------------------------------------------------------------------------


def test_ap02_distinct_with_agg():
    assert "AP02" in _codes(
        "SELECT DISTINCT ?t (COUNT(?x) AS ?c) WHERE { ?x a ?t } GROUP BY ?t"
    )


def test_ap02_distinct_no_agg_no_flag():
    assert "AP02" not in _codes("SELECT DISTINCT ?x WHERE { ?x ?p ?o }")


def test_ap02_agg_no_distinct_no_flag():
    assert "AP02" not in _codes(
        "SELECT (COUNT(?x) AS ?c) WHERE { ?x a <http://x.org/T> }"
    )


def test_ap02_count_distinct_no_flag():
    # COUNT(DISTINCT) is valid - inner DISTINCT is part of the aggregate semantics
    assert "AP02" not in _codes(
        "SELECT ?x (COUNT(DISTINCT ?y) AS ?c) WHERE { ?x <http://x.org/p> ?y } GROUP BY ?x"
    )


def test_ap02_select_distinct_with_count_distinct():
    # SELECT DISTINCT with COUNT(DISTINCT) - outer DISTINCT is redundant
    assert "AP02" in _codes(
        "SELECT DISTINCT ?x (COUNT(DISTINCT ?y) AS ?c) WHERE { ?x <http://x.org/p> ?y } GROUP BY ?x"
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
# AP04 — Cartesian product
# ---------------------------------------------------------------------------


def test_ap04_cartesian():
    assert "AP04" in _codes(
        "SELECT ?x ?y WHERE { ?x a <http://x.org/Person> . ?y a <http://x.org/City> }"
    )


def test_ap04_joined_no_flag():
    assert "AP04" not in _codes(
        "SELECT ?x ?y WHERE { ?x a <http://x.org/Person> . ?x <http://x.org/lives> ?y }"
    )


def test_ap04_single_triple_no_flag():
    assert "AP04" not in _codes("SELECT ?x WHERE { ?x a <http://x.org/T> }")


# ---------------------------------------------------------------------------
# AP05 — non-grouped projected variable
# ---------------------------------------------------------------------------


def test_ap05_non_grouped_var():
    assert "AP05" in _codes(
        "SELECT ?x ?z (COUNT(?y) AS ?c) WHERE { ?x <http://x.org/p> ?y ; <http://x.org/q> ?z } GROUP BY ?x"
    )


def test_ap05_all_grouped_no_flag():
    assert "AP05" not in _codes(
        "SELECT ?x ?z (COUNT(?y) AS ?c) WHERE { ?x <http://x.org/p> ?y ; <http://x.org/q> ?z } GROUP BY ?x ?z"
    )


# ---------------------------------------------------------------------------
# AP09 — unbound projected variable
# ---------------------------------------------------------------------------


def test_ap09_unbound_proj():
    assert "AP09" in _codes("SELECT ?x ?y WHERE { ?x a <http://x.org/T> }")


def test_ap09_all_bound_no_flag():
    assert "AP09" not in _codes(
        "SELECT ?x ?y WHERE { ?x a <http://x.org/T> . ?x <http://x.org/p> ?y }"
    )


# ---------------------------------------------------------------------------
# AP06 — aggregate in FILTER instead of HAVING
# ---------------------------------------------------------------------------


def test_ap06_aggregate_in_filter():
    assert "AP06" in _codes(
        "SELECT ?x WHERE { ?x <http://x.org/p> ?y FILTER(COUNT(?y) > 5) } GROUP BY ?x"
    )


def test_ap06_having_no_flag():
    assert "AP06" not in _codes(
        "SELECT ?x (COUNT(?y) AS ?c) WHERE { ?x <http://x.org/p> ?y } GROUP BY ?x HAVING (COUNT(?y) > 5)"
    )


def test_ap06_filter_on_subquery_aggregate_no_flag():
    # FILTER on aggregate result from subquery is valid (not in same scope as aggregate)
    assert "AP06" not in _codes(
        """SELECT ?x ?cnt WHERE {
          { SELECT ?x (COUNT(?y) AS ?cnt) WHERE { ?x <http://x.org/p> ?y } GROUP BY ?x }
          FILTER(?cnt > 5)
        }"""
    )


# ---------------------------------------------------------------------------
# AP07 — alias reference in same SELECT
# ---------------------------------------------------------------------------


def test_ap07_alias_reference():
    assert "AP07" in _codes(
        "SELECT (COUNT(?x) AS ?c) (?c + 1 AS ?d) WHERE { ?x a <http://x.org/T> }"
    )


def test_ap07_no_cross_ref_no_flag():
    assert "AP07" not in _codes(
        "SELECT (COUNT(?x) AS ?c) WHERE { ?x a <http://x.org/T> }"
    )


# ---------------------------------------------------------------------------
# AP08 — vendor-specific syntax
# ---------------------------------------------------------------------------


def test_ap08_vendor_function():
    # bif:contains is a Virtuoso extension — unknown prefix causes translation error
    assert "AP08" in _codes(
        'SELECT ?x WHERE { ?x <http://x.org/name> ?n FILTER(bif:contains(?n, "Alice")) }'
    )


def test_ap08_standard_function_no_flag():
    assert "AP08" not in _codes(
        'SELECT ?x WHERE { ?x <http://x.org/name> ?n FILTER(REGEX(?n, "Alice")) }'
    )


# ---------------------------------------------------------------------------
# Clean query — no antipatterns
# ---------------------------------------------------------------------------


def test_clean_query_no_issues():
    assert (
        _codes("SELECT ?x ?y WHERE { ?x <http://x.org/p> ?y . ?y a <http://x.org/T> }")
        == set()
    )


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
    assert "AP09" in codes
