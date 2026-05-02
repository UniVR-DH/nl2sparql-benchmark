"""Tests for sparql_annotator.algebra — complete coverage with parameterization."""

import pytest
import rdflib.plugins.sparql.algebra as _a

from sparql_annotator.algebra import (
    parse_query,
    extract_operators,
    detect_lsq_features,
    compute_metrics,
    referenced_terms,
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


@pytest.mark.parametrize(
    "text",
    [
        "NOT SPARQL AT ALL",
        "SELECT WHERE",
        "{ ?s ?p ?o }",
        "",
    ],
)
def test_parse_invalid(text):
    ok, parsed, err = parse_query(text)
    assert not ok and parsed is None and err is not None


# ---------------------------------------------------------------------------
# compute_metrics — exact (bgp, tp, pv) triples
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("SELECT ?s WHERE { ?s ?p ?o }", (1, 1, 1)),
        ("SELECT ?s ?p WHERE { ?s ?p ?o . ?s a <http://x.org/C> }", (1, 2, 2)),
        ("ASK { ?s ?p ?o }", (1, 1, 0)),  # pv=0 for ASK
        # OPTIONAL adds a BGP per branch
        (
            "SELECT ?s WHERE { ?s ?p ?o . OPTIONAL { ?s <http://x.org/y> ?z } }",
            (2, 2, 1),
        ),
        # nested OPTIONAL
        (
            "SELECT ?s WHERE { ?s ?p ?o . OPTIONAL { ?s <http://x.org/y> ?z . OPTIONAL { ?z <http://x.org/w> ?v } } }",
            (3, 3, 1),
        ),
        # UNION: one BGP per branch
        (
            "SELECT ?s WHERE { { ?s a <http://x.org/A> } UNION { ?s a <http://x.org/B> } }",
            (2, 2, 1),
        ),
        # outer BGP + UNION
        (
            "SELECT ?s WHERE { ?s ?p ?o . { ?s a <http://x.org/A> } UNION { ?s a <http://x.org/B> } }",
            (3, 3, 1),
        ),
        # FILTER NOT EXISTS inner pattern counts as BGP (SPARQL 1.1 §18)
        (
            "SELECT ?s WHERE { ?s ?p ?o . FILTER NOT EXISTS { ?s a <http://x.org/Bad> } }",
            (2, 2, 1),
        ),
        # inner BGP with 2 triples
        (
            "SELECT ?s WHERE { ?s ?p ?o . FILTER NOT EXISTS { ?s a <http://x.org/Bad> . ?s <http://x.org/p> ?v } }",
            (2, 3, 1),
        ),
        # FILTER EXISTS inner pattern counts as BGP
        (
            "SELECT ?s WHERE { ?s ?p ?o . FILTER EXISTS { ?s a <http://x.org/Good> } }",
            (2, 2, 1),
        ),
        # subquery adds inner BGP
        (
            "SELECT ?s WHERE { ?s ?p ?o . { SELECT ?x WHERE { ?x a <http://x.org/C> } } }",
            (2, 2, 1),
        ),
        # outer + OPTIONAL + subquery
        (
            "SELECT ?s WHERE { ?s ?p ?o . OPTIONAL { ?s <http://x.org/y> ?z } . { SELECT ?x WHERE { ?x a <http://x.org/C> } } }",
            (3, 3, 1),
        ),
    ],
)
def test_metrics_exact(text, expected):
    bgp, tp, pv = compute_metrics(_alg(text)[0])
    assert (bgp, tp, pv) == expected, f"got ({bgp},{tp},{pv}), expected {expected}"


# ---------------------------------------------------------------------------
# detect_lsq_features
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,present,absent",
    [
        # basic operators
        (
            "SELECT ?s WHERE { ?s ?p ?o . OPTIONAL { ?s <http://x.org/y> ?z } }",
            ["Optional"],
            [],
        ),
        ("SELECT ?s WHERE { ?s ?p ?o . FILTER(?o > 5) }", ["Filter"], []),
        (
            "SELECT ?s WHERE { ?s ?p ?o . FILTER NOT EXISTS { ?s <http://x.org/y> ?z } }",
            ["NotExists"],
            ["Filter"],
        ),
        (
            "SELECT ?s WHERE { ?s ?p ?o . FILTER EXISTS { ?s <http://x.org/y> ?z } }",
            ["fn-exists"],
            ["Filter"],
        ),
        ("SELECT ?s WHERE { { SELECT ?s WHERE { ?s ?p ?o } } }", ["SubQuery"], []),
        ("SELECT ?s ?v WHERE { ?s ?p ?o . BIND(str(?o) AS ?v) }", ["Bind"], []),
        # HAVING regressions
        (
            "SELECT ?name (COUNT(?emp) AS ?n) WHERE { ?dept <http://x.org/name> ?name . ?emp <http://x.org/memberOf> ?dept } GROUP BY ?dept ?name HAVING (COUNT(?emp) > 5)",
            ["Having"],
            ["Filter"],
        ),
        (
            "SELECT ?dept ?name (COUNT(?emp) AS ?n) WHERE { ?dept <http://x.org/name> ?name . ?emp <http://x.org/memberOf> ?dept } GROUP BY ?dept ?name HAVING (COUNT(?emp) > 2)",
            ["Having"],
            ["Filter"],
        ),
        # GROUP BY multi-projection must not emit Bind
        (
            "SELECT ?cat ?name WHERE { ?hw <http://x.org/cat> ?cat . ?cat <http://x.org/name> ?name } GROUP BY ?cat ?name ORDER BY DESC(COUNT(*)) LIMIT 3",
            [],
            ["Bind"],
        ),
        # real BIND alongside aggregates
        (
            "SELECT ?s ?v (COUNT(?o) AS ?c) WHERE { ?s ?p ?o . BIND(str(?s) AS ?v) } GROUP BY ?s ?v",
            ["Bind"],
            [],
        ),
    ],
)
def test_lsq_features(text, present, absent):
    feats = _lsq(text)
    for f in present:
        assert f in feats, f"{f!r} missing from {feats}"
    for f in absent:
        assert f not in feats, f"{f!r} should not be in {feats}"


# ---------------------------------------------------------------------------
# extract_operators — query forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,form",
    [
        ("SELECT ?s WHERE { ?s ?p ?o }", "SELECT"),
        ("ASK { ?s ?p ?o }", "ASK"),
        ("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }", "CONSTRUCT"),
        ("DESCRIBE <http://x.org/A>", "DESCRIBE"),
    ],
)
def test_ops_query_form(text, form):
    assert _ops(text).query_form == form


# ---------------------------------------------------------------------------
# extract_operators — graph patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,pattern",
    [
        (
            "SELECT ?s WHERE { ?s ?p ?o . OPTIONAL { ?s <http://x.org/y> ?z } }",
            "OPTIONAL",
        ),
        (
            "SELECT ?s WHERE { { ?s a <http://x.org/A> } UNION { ?s a <http://x.org/B> } }",
            "UNION",
        ),
        ("SELECT ?s WHERE { ?s ?p ?o . MINUS { ?s a <http://x.org/Bad> } }", "MINUS"),
        ("SELECT ?s WHERE { GRAPH <http://x.org/g> { ?s ?p ?o } }", "GRAPH"),
        (
            "SELECT ?s WHERE { SERVICE <http://dbpedia.org/sparql> { ?s ?p ?o } }",
            "SERVICE",
        ),
    ],
)
def test_ops_graph_patterns(text, pattern):
    assert pattern in _ops(text).graph_patterns


# ---------------------------------------------------------------------------
# extract_operators — filters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,filt",
    [
        ("SELECT ?s WHERE { ?s ?p ?o . FILTER(?o > 5) }", "FILTER"),
        (
            "SELECT ?s WHERE { ?s ?p ?o . FILTER NOT EXISTS { ?s <http://x.org/y> ?z } }",
            "FILTER NOT EXISTS",
        ),
        (
            "SELECT ?s WHERE { ?s ?p ?o . FILTER EXISTS { ?s <http://x.org/y> ?z } }",
            "FILTER EXISTS",
        ),
    ],
)
def test_ops_filters(text, filt):
    assert filt in _ops(text).filters


# ---------------------------------------------------------------------------
# extract_operators — aggregates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,agg",
    [
        ("SELECT (COUNT(?s) AS ?c) WHERE { ?s ?p ?o }", "COUNT"),
        ("SELECT (SUM(?o) AS ?t) WHERE { ?s <http://x.org/val> ?o }", "SUM"),
        ("SELECT (AVG(?o) AS ?a) WHERE { ?s <http://x.org/val> ?o }", "AVG"),
        ("SELECT (MIN(?o) AS ?mn) WHERE { ?s <http://x.org/val> ?o }", "MIN"),
        ("SELECT (MAX(?o) AS ?mx) WHERE { ?s <http://x.org/val> ?o }", "MAX"),
    ],
)
def test_ops_aggregates(text, agg):
    assert agg in _ops(text).aggregates


# ---------------------------------------------------------------------------
# extract_operators — solution modifiers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,mod",
    [
        ("SELECT ?p (COUNT(?s) AS ?c) WHERE { ?s ?p ?o } GROUP BY ?p", "GROUP BY"),
        (
            "SELECT ?p (COUNT(?s) AS ?c) WHERE { ?s ?p ?o } GROUP BY ?p HAVING (COUNT(?s) > 1)",
            "HAVING",
        ),
        ("SELECT ?s WHERE { ?s ?p ?o } ORDER BY ?s", "ORDER BY"),
        ("SELECT ?s WHERE { ?s ?p ?o } LIMIT 10", "LIMIT"),
        ("SELECT ?s WHERE { ?s ?p ?o } LIMIT 10 OFFSET 5", "OFFSET"),
    ],
)
def test_ops_solution_modifiers(text, mod):
    assert mod in _ops(text).solution_modifiers


# ---------------------------------------------------------------------------
# extract_operators — projection modifiers
# ---------------------------------------------------------------------------


def test_ops_distinct():
    assert (
        "DISTINCT" in _ops("SELECT DISTINCT ?s WHERE { ?s ?p ?o }").projection_modifiers
    )


def test_ops_reduced():
    assert (
        "REDUCED" in _ops("SELECT REDUCED ?s WHERE { ?s ?p ?o }").projection_modifiers
    )


# ---------------------------------------------------------------------------
# extract_operators — assignments
# ---------------------------------------------------------------------------


def test_ops_bind():
    assert (
        "BIND"
        in _ops("SELECT ?s ?v WHERE { ?s ?p ?o . BIND(str(?o) AS ?v) }").assignments
    )


def test_ops_subquery():
    assert (
        _ops("SELECT ?s WHERE { { SELECT ?s WHERE { ?s ?p ?o } } }").subqueries is True
    )


def test_ops_values_single_var():
    assert (
        "VALUES"
        in _ops(
            "SELECT ?s WHERE { VALUES ?s { <http://x.org/A> <http://x.org/B> } . ?s ?p ?o }"
        ).assignments
    )


def test_ops_values_multi_var():
    assert (
        "VALUES"
        in _ops(
            "SELECT ?s ?p WHERE { VALUES (?s ?p) { (<http://x.org/A> <http://x.org/p>) } . ?s ?p ?o }"
        ).assignments
    )


def test_ops_values_empty():
    # Empty VALUES {} — rdflib may not produce a values node; just verify no crash
    ok, parsed, _ = parse_query("SELECT ?s WHERE { VALUES ?s { } . ?s ?p ?o }")
    assert ok  # must parse without error


# ---------------------------------------------------------------------------
# extract_operators — property paths (all path operators)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "SELECT ?s WHERE { ?s <http://x.org/a>/<http://x.org/b> ?o }",  # sequence /
        "SELECT ?s WHERE { ?s <http://x.org/a>|<http://x.org/b> ?o }",  # alternative |
        "SELECT ?s WHERE { ?s <http://x.org/a>* ?o }",  # zero-or-more *
        "SELECT ?s WHERE { ?s <http://x.org/a>+ ?o }",  # one-or-more +
        "SELECT ?s WHERE { ?s <http://x.org/a>? ?o }",  # zero-or-one ?
        "SELECT ?s WHERE { ?s ^<http://x.org/a> ?o }",  # inverse ^
        "SELECT ?s WHERE { ?s (<http://x.org/a>/<http://x.org/b>)* ?o }",  # nested
    ],
)
def test_ops_property_path(text):
    ops = _ops(text)
    assert ops.property_paths is True
    assert "PROPERTY_PATH" in ops.raw


def test_ops_no_property_path():
    ops = _ops("SELECT ?s WHERE { ?s <http://x.org/a> ?o }")
    assert ops.property_paths is False


# ---------------------------------------------------------------------------
# extract_operators — filter functions (M3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,fn",
    [
        ("SELECT ?s WHERE { ?s ?p ?o . FILTER(REGEX(STR(?o), 'foo')) }", "REGEX"),
        ("SELECT ?s WHERE { ?s ?p ?o . FILTER(REGEX(STR(?o), 'foo')) }", "STR"),
        ("SELECT ?s WHERE { ?s ?p ?o . FILTER(LANG(?o) = 'en') }", "LANG"),
        (
            "SELECT ?s WHERE { ?s ?p ?o . FILTER(LANGMATCHES(LANG(?o), 'en')) }",
            "LANGMATCHES",
        ),
        ("SELECT ?s WHERE { ?s ?p ?o . FILTER(BOUND(?o)) }", "BOUND"),
        (
            "SELECT ?s WHERE { ?s ?p ?o . FILTER(DATATYPE(?o) = <http://www.w3.org/2001/XMLSchema#integer>) }",
            "DATATYPE",
        ),
        ("SELECT ?s WHERE { ?s ?p ?o . FILTER(isIRI(?s)) }", "isIRI"),
        ("SELECT ?s WHERE { ?s ?p ?o . FILTER(isLITERAL(?o)) }", "isLITERAL"),
    ],
)
def test_ops_filter_functions(text, fn):
    assert fn in _ops(text).filter_functions


def test_ops_no_filter_functions_when_plain_filter():
    # A plain comparison filter has no Builtin_* nodes
    ops = _ops("SELECT ?s WHERE { ?s ?p ?o . FILTER(?o > 5) }")
    assert ops.filter_functions == set()


# ---------------------------------------------------------------------------
# extract_operators — structural metrics (exact values)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,bgp,tp,pv",
    [
        ("SELECT ?s ?p WHERE { ?s ?p ?o . ?s a <http://x.org/C> }", 1, 2, 2),
        ("ASK { ?s ?p ?o }", 1, 1, 0),
        ("SELECT ?s WHERE { ?s ?p ?o . OPTIONAL { ?s <http://x.org/y> ?z } }", 2, 2, 1),
        # extract_operators counts algebra BGP nodes only (not inner FILTER NOT EXISTS patterns)
        # use compute_metrics directly for the spec-correct count
        (
            "SELECT ?s WHERE { ?s ?p ?o . FILTER NOT EXISTS { ?s a <http://x.org/Bad> } }",
            1,
            1,
            1,
        ),
    ],
)
def test_ops_metrics_exact(text, bgp, tp, pv):
    ops = _ops(text)
    assert ops.bgp_count == bgp, f"bgp: got {ops.bgp_count}, expected {bgp}"
    assert ops.tp_count == tp, f"tp: got {ops.tp_count}, expected {tp}"
    assert ops.project_var_count == pv, (
        f"pv: got {ops.project_var_count}, expected {pv}"
    )


# ---------------------------------------------------------------------------
# extract_operators — invalid / boundary
# ---------------------------------------------------------------------------


def test_ops_invalid_query_returns_empty():
    ops = extract_operators("NOT VALID SPARQL", None)
    assert ops.query_form == "UNKNOWN"
    assert not ops.raw
    assert ops.filter_functions == set()


def test_ops_empty_where():
    # rdflib produces an empty BGP node for WHERE {}
    ops = _ops("SELECT ?s WHERE {}")
    assert ops.query_form == "SELECT"
    assert ops.bgp_count == 1 and ops.tp_count == 0


# ---------------------------------------------------------------------------
# referenced_terms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected_iris",
    [
        (
            "SELECT ?s WHERE { ?s a <http://example.org/Class> . ?s <http://example.org/prop> ?o }",
            {"http://example.org/Class", "http://example.org/prop"},
        ),
        ("SELECT ?s WHERE { ?s ?p ?o }", set()),
        (
            "ASK { <http://x.org/A> <http://x.org/p> <http://x.org/B> }",
            {"http://x.org/A", "http://x.org/p", "http://x.org/B"},
        ),
    ],
)
def test_referenced_terms(text, expected_iris):
    terms = referenced_terms(text)
    assert (
        expected_iris <= terms
    )  # expected is a subset (PREFIX declarations may add more)


# ---------------------------------------------------------------------------
# detect_lsq_features — subquery feature propagation
# ---------------------------------------------------------------------------

_COMPARATIVE_QUERY = """\
PREFIX pv: <http://ld.company.org/prod-vocab/>
SELECT ?result ?deptCount
WHERE {
  {
    SELECT (MAX(?c) AS ?maxCount)
    WHERE {
      SELECT ?dept (COUNT(?p) AS ?c)
      WHERE { ?dept pv:responsibleFor ?p . }
      GROUP BY ?dept
    }
  }
  {
    SELECT ?result (COUNT(?p) AS ?deptCount)
    WHERE { ?result pv:responsibleFor ?p . }
    GROUP BY ?result
  }
  FILTER(?deptCount = ?maxCount)
}"""


def test_detect_features_subquery_aggregators():
    """Aggregators used inside subqueries must be detected at the outer level."""
    feats = _lsq(_COMPARATIVE_QUERY)
    assert "Aggregators" in feats


def test_detect_features_subquery_groupby():
    """GroupBy used inside subqueries must be detected at the outer level."""
    feats = _lsq(_COMPARATIVE_QUERY)
    assert "GroupBy" in feats


def test_detect_features_subquery_select():
    """Select must be detected for a SELECT query."""
    feats = _lsq(_COMPARATIVE_QUERY)
    assert "Select" in feats


def test_detect_features_comparative_full():
    """Full feature set for the comparative query."""
    feats = _lsq(_COMPARATIVE_QUERY)
    assert {"Select", "SubQuery", "Filter", "Aggregators", "GroupBy"} <= feats
