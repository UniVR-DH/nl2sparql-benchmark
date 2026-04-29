"""Tests for sparql_annotator.classifier"""
import textwrap
from pathlib import Path

import pytest
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF, RDFS

from sparql_annotator.classifier import QuestionTypeClassifier

LSQV = Namespace("http://lsq.aksw.org/vocab#")
QAT = Namespace("https://w3id.org/univr-qa/qatypes#")

ONTOLOGY_PATH = Path(__file__).parent.parent.parent.parent / "graphs" / "qa-types.ttl"
QUERIES_PATH = Path(__file__).parent.parent.parent.parent / "graphs" / "ck25" / "ck25-queries.ttl"


@pytest.fixture(scope="module")
def classifier():
    if not ONTOLOGY_PATH.exists():
        pytest.skip(f"Ontology not found: {ONTOLOGY_PATH}")
    return QuestionTypeClassifier(ONTOLOGY_PATH)


def test_loads_type_definitions(classifier):
    assert len(classifier.type_definitions) > 0


def test_all_types_have_requirements(classifier):
    empty = [n for n, d in classifier.type_definitions.items() if not d.all_requirements]
    assert empty == [], f"Types with no requirements: {empty}"


def test_classify_queries_from_file(classifier):
    if not QUERIES_PATH.exists():
        pytest.skip(f"Query file not found: {QUERIES_PATH}")
    results = classifier.classify_queries_from_file(QUERIES_PATH)
    assert len(results) > 0
    classified = sum(1 for _, (qtypes, *_) in results.items() if qtypes)
    assert classified > 0, "No queries were classified"


def test_classify_simple_factoid(classifier, tmp_path):
    """A simple SELECT with one triple should classify as some Factoid type."""
    ttl = textwrap.dedent("""
        @prefix lsqv: <http://lsq.aksw.org/vocab#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

        <http://example.org/q1> a lsqv:Query ;
            rdfs:label "simple factoid" ;
            lsqv:text "SELECT ?s WHERE { ?s a <http://example.org/C> }" ;
            lsqv:hasStructuralFeatures [
                lsqv:usesFeature lsqv:Select, lsqv:TriplePattern
            ] .
    """)
    qfile = tmp_path / "q.ttl"
    qfile.write_text(ttl)
    results = classifier.classify_queries_from_file(qfile)
    assert len(results) == 1
    qtypes, features, label, warnings, counts = next(iter(results.values()))
    assert "Select" in features or "TriplePattern" in features


def test_depth_cache_populated(classifier):
    assert len(classifier._depth_cache) == len(classifier.type_definitions)
