"""Tests for sparql_annotator.reporter — ReportGenerator."""

import csv
import textwrap
from pathlib import Path

import pytest

from sparql_annotator.reporter import ReportGenerator

# ---------------------------------------------------------------------------
# Minimal hermetic ontology (same as test_classifier.py)
# ---------------------------------------------------------------------------

_MINI_ONTOLOGY = textwrap.dedent("""
    @prefix owl:  <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix lsqv: <http://lsq.aksw.org/vocab#> .
    @prefix qat:  <https://w3id.org/qatypes#> .

    qat:QuestionType a owl:Class .

    qat:Factoid rdfs:subClassOf qat:QuestionType ;
        rdfs:subClassOf [
            owl:onProperty lsqv:hasStructuralFeatures ;
            owl:someValuesFrom [
                owl:onProperty lsqv:usesFeature ;
                owl:hasValue lsqv:Select
            ]
        ] .

    qat:Confirmation rdfs:subClassOf qat:QuestionType ;
        rdfs:subClassOf [
            owl:onProperty lsqv:hasStructuralFeatures ;
            owl:someValuesFrom [
                owl:onProperty lsqv:usesFeature ;
                owl:hasValue lsqv:Ask
            ]
        ] ;
        owl:disjointWith qat:Factoid .
""")


def _write_query_ttl(path: Path, queries: list[dict]) -> None:
    """Write a minimal LSQ Turtle file with multiple queries.

    Each dict: {id, label, sparql, features}
    """
    lines = [
        "@prefix lsqv: <http://lsq.aksw.org/vocab#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "",
    ]
    for q in queries:
        features = q["features"] or ["Select"]
        # Each usesFeature must be a separate triple inside the blank node
        feat_lines = " ;\n        ".join(f"lsqv:usesFeature lsqv:{f}" for f in features)
        lines += [
            f"<http://example.org/{q['id']}> a lsqv:Query ;",
            f'    rdfs:label "{q["label"]}" ;',
            f'    lsqv:text """{q["sparql"]}""" ;',
            f"    lsqv:hasStructuralFeatures [ {feat_lines} ] .",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture(scope="module")
def onto(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("onto") / "mini.ttl"
    p.write_text(_MINI_ONTOLOGY, encoding="utf-8")
    return p


@pytest.fixture(scope="module")
def query_file(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("queries") / "queries.ttl"
    _write_query_ttl(
        p,
        [
            {
                "id": "q1",
                "label": "Simple select",
                "sparql": "SELECT ?s WHERE { ?s ?p ?o }",
                "features": ["Select"],
            },
            {
                "id": "q2",
                "label": "Ask query",
                "sparql": "ASK { ?s ?p ?o }",
                "features": ["Ask"],
            },
            {
                "id": "q3",
                "label": "Select with filter",
                "sparql": "SELECT ?s WHERE { ?s ?p ?o . FILTER(?o > 1) }",
                "features": ["Select", "Filter"],
            },
        ],
    )
    return p


@pytest.fixture(scope="module")
def reports(tmp_path_factory, onto, query_file) -> Path:
    out = tmp_path_factory.mktemp("reports")
    gen = ReportGenerator(str(onto))
    gen.generate_reports(
        str(query_file), str(out), prefix="test", formats=["csv", "latex"]
    )
    return out


# ---------------------------------------------------------------------------
# CSV output tests
# ---------------------------------------------------------------------------


def test_features_csv_exists(reports):
    assert (reports / "test_features.csv").exists()


def test_features_csv_columns(reports):
    rows = list(csv.DictReader((reports / "test_features.csv").open(encoding="utf-8")))
    assert len(rows) == 3
    assert "query_id" in rows[0]
    assert "Select" in rows[0]


def test_features_csv_values(reports):
    rows = {
        r["query_id"]: r
        for r in csv.DictReader((reports / "test_features.csv").open(encoding="utf-8"))
    }
    assert rows["q1"]["Select"] == "1"
    assert rows["q2"]["Ask"] == "1"
    assert rows["q2"]["Select"] == "0"


def test_operators_csv_exists(reports):
    assert (reports / "test_operators.csv").exists()


def test_operators_csv_has_query_form(reports):
    rows = list(csv.DictReader((reports / "test_operators.csv").open(encoding="utf-8")))
    forms = {r["query_id"]: r["query_form"] for r in rows}
    assert forms["q1"] == "SELECT"
    assert forms["q2"] == "ASK"
    # parse_ok must be present and numeric
    parse_ok = {r["query_id"]: r["parse_ok"] for r in rows}
    assert parse_ok["q1"] == "1"
    assert parse_ok["q2"] == "1"


def test_question_types_csv(reports):
    rows = {
        r["query_id"]: r
        for r in csv.DictReader(
            (reports / "test_question_types.csv").open(encoding="utf-8")
        )
    }
    assert rows["q1"]["question_types"] == "Factoid"
    assert rows["q1"]["classification_status"] == "classified"
    assert rows["q2"]["question_types"] == "Confirmation"


def test_antipatterns_csv_exists(reports):
    assert (reports / "test_antipatterns.csv").exists()


def test_antipatterns_csv_columns(reports):
    rows = list(
        csv.DictReader((reports / "test_antipatterns.csv").open(encoding="utf-8"))
    )
    assert "AP01" in rows[0]
    assert "antipattern_messages" in rows[0]


def test_metrics_csv(reports):
    rows = {
        r["query_id"]: r
        for r in csv.DictReader((reports / "test_metrics.csv").open(encoding="utf-8"))
    }
    assert "bgp_count" in rows["q1"]
    assert rows["q1"]["bgp_count"] != ""


def test_summary_csv(reports):
    rows = {
        r["metric"]: r["value"]
        for r in csv.DictReader((reports / "test_summary.csv").open(encoding="utf-8"))
    }
    assert rows["total_queries"] == "3"
    assert rows["classified_queries"] == "3"
    assert rows["unclassified_queries"] == "0"
    # per-AP count rows must be present for all codes
    for code in [
        "AP01",
        "AP02",
        "AP03",
        "AP04",
        "AP05",
        "AP06",
        "AP07",
        "AP08",
        "AP09",
    ]:
        assert f"queries_with_{code}" in rows


def test_count_by_question_type_csv(reports):
    rows = list(
        csv.DictReader(
            (reports / "test_count_by_question_type.csv").open(encoding="utf-8")
        )
    )
    types = {r["question_type"]: int(r["count"]) for r in rows}
    assert types.get("Factoid", 0) == 2  # q1 and q3
    assert types.get("Confirmation", 0) == 1


def test_count_by_feature_csv(reports):
    rows = list(
        csv.DictReader((reports / "test_count_by_feature.csv").open(encoding="utf-8"))
    )
    feats = {r["feature"]: int(r["count"]) for r in rows}
    assert feats.get("Select", 0) == 2
    assert feats.get("Ask", 0) == 1


def test_count_by_operator_csv(reports):
    p = reports / "test_count_by_operator.csv"
    assert p.exists()
    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    ops = {r["operator"] for r in rows}
    # Select and Ask are operator-level features
    assert "Select" in ops or "Ask" in ops


# ---------------------------------------------------------------------------
# LaTeX output tests
# ---------------------------------------------------------------------------


def test_features_latex_exists(reports):
    assert (reports / "test_features.tex").exists()


def test_features_latex_content(reports):
    tex = (reports / "test_features.tex").read_text(encoding="utf-8")
    assert r"\begin{table}" in tex
    assert r"\toprule" in tex
    assert r"\cellcolor" in tex
    assert r"\usepackage[table]{xcolor}" in tex


def test_operators_latex_exists(reports):
    assert (reports / "test_operators.tex").exists()


def test_summary_latex_exists(reports):
    tex = (reports / "test_summary.tex").read_text(encoding="utf-8")
    assert "Total queries" in tex


def test_antipatterns_latex_exists(reports):
    assert (reports / "test_antipatterns.tex").exists()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_output_dir_created(tmp_path, onto, query_file):
    out = tmp_path / "new" / "nested" / "dir"
    gen = ReportGenerator(str(onto))
    gen.generate_reports(str(query_file), str(out), prefix="x", formats=["csv"])
    assert (out / "x_summary.csv").exists()


def test_empty_query_file(tmp_path, onto):
    qf = tmp_path / "empty.ttl"
    qf.write_text(
        "@prefix lsqv: <http://lsq.aksw.org/vocab#> .\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    gen = ReportGenerator(str(onto))
    gen.generate_reports(str(qf), str(out), prefix="empty", formats=["csv"])
    rows = list(csv.DictReader((out / "empty_summary.csv").open(encoding="utf-8")))
    totals = {r["metric"]: r["value"] for r in rows}
    assert totals["total_queries"] == "0"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_missing_query_file_raises(tmp_path, onto):
    gen = ReportGenerator(str(onto))
    with pytest.raises(FileNotFoundError, match="not found"):
        gen.generate_reports(
            str(tmp_path / "nonexistent.ttl"), str(tmp_path), formats=["csv"]
        )


def test_invalid_ttl_raises(tmp_path, onto):
    bad = tmp_path / "bad.ttl"
    bad.write_text("this is not valid turtle @@@@", encoding="utf-8")
    gen = ReportGenerator(str(onto))
    with pytest.raises(ValueError, match="Failed to parse"):
        gen.generate_reports(str(bad), str(tmp_path), formats=["csv"])


def test_invalid_format_ignored(tmp_path, onto, query_file):
    """Unknown format strings are warned and ignored (no csv/latex output, no crash)."""
    out = tmp_path / "out"
    gen = ReportGenerator(str(onto))
    gen.generate_reports(str(query_file), str(out), prefix="x", formats=["unknown"])
    # No files written, but no exception either
    assert not list(out.glob("*.csv"))


def test_prefix_path_traversal_raises(tmp_path, onto, query_file):
    """prefix must be rejected if it contains anything outside [A-Za-z0-9_-]."""
    gen = ReportGenerator(str(onto))
    for bad in ["../evil", "pre}fix", "pre\\fix", "pre%fix"]:
        with pytest.raises(ValueError, match="letters, digits"):
            gen.generate_reports(str(query_file), str(tmp_path), prefix=bad)


def test_operators_latex_unparseable_query(tmp_path, onto):
    """Queries that fail SPARQL parsing (ops is None) must render \\cellcolor cells, not blanks."""
    qf = tmp_path / "q.ttl"
    _write_query_ttl(
        qf,
        [
            {
                "id": "bad",
                "label": "bad",
                "sparql": "NOT VALID SPARQL !!!!",
                "features": ["Select"],
            }
        ],
    )
    out = tmp_path / "out"
    gen = ReportGenerator(str(onto))
    gen.generate_reports(str(qf), str(out), prefix="x", formats=["latex"])
    tex = (out / "x_operators.tex").read_text(encoding="utf-8")
    # Every operator cell must be a \cellcolor command, not an empty cell
    assert r"\cellcolor" in tex


def test_latex_escaping(tmp_path, onto):
    """Query IDs with _ and & must be escaped in LaTeX output."""
    qf = tmp_path / "q.ttl"
    _write_query_ttl(
        qf,
        [
            {
                "id": "q_1&2",
                "label": "test_label",
                "sparql": "SELECT ?s WHERE { ?s ?p ?o }",
                "features": ["Select"],
            }
        ],
    )
    out = tmp_path / "out"
    gen = ReportGenerator(str(onto))
    gen.generate_reports(str(qf), str(out), prefix="x", formats=["latex"])
    tex = (out / "x_features.tex").read_text(encoding="utf-8")
    assert r"q\_1\&2" in tex
    assert r"\checkmark" in tex or r"$\times$" in tex
    assert "✓" not in tex and "✗" not in tex
