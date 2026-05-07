"""Tests for TTLAdapter LSQ output — including numeric literal fidelity."""

import textwrap

from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.namespace import XSD

from sparql_annotator.adapters.ttl import TTLAdapter
from sparql_annotator.annotator import Annotator

LSQV = Namespace("http://lsq.aksw.org/vocab#")
DCT = Namespace("http://purl.org/dc/terms/")

_QUERY_TTL = textwrap.dedent("""
    @prefix lsqv: <http://lsq.aksw.org/vocab#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

    <http://example.org/q1> a lsqv:Query ;
        rdfs:label "q1" ;
        lsqv:text "SELECT ?s ?p WHERE { ?s ?p ?o . ?s a <http://example.org/Class> }" .
""")


def _annotate(tmp_path, ttl_text=_QUERY_TTL):
    src = tmp_path / "in.ttl"
    out = tmp_path / "out.ttl"
    src.write_text(ttl_text, encoding="utf-8")
    adapter = TTLAdapter(lsq=True)
    ann = Annotator().annotate_file(str(src), input_adapter=adapter)
    adapter.write(ann, str(out))
    g = Graph()
    g.parse(str(out), format="turtle")
    return g, ann


def test_ttl_adapter_emits_lsq(tmp_path):
    g, ann = _annotate(tmp_path)
    subj = URIRef("http://example.org/q1")
    sfs = list(g.objects(subj, LSQV.hasStructuralFeatures))
    assert sfs, "No lsqv:hasStructuralFeatures found"


def test_ttl_adapter_bgp_count_correct(tmp_path):
    g, ann = _annotate(tmp_path)
    subj = URIRef("http://example.org/q1")
    sf = next(iter(g.objects(subj, LSQV.hasStructuralFeatures)))
    bgp_vals = list(g.objects(sf, LSQV.bgpCount))
    assert bgp_vals, "bgpCount missing"
    assert int(bgp_vals[0]) == 1


def test_ttl_adapter_tp_count_correct(tmp_path):
    g, ann = _annotate(tmp_path)
    subj = URIRef("http://example.org/q1")
    sf = next(iter(g.objects(subj, LSQV.hasStructuralFeatures)))
    tp_vals = list(g.objects(sf, LSQV.tpCount))
    assert tp_vals, "tpCount missing"
    assert int(tp_vals[0]) == 2  # two triple patterns


def test_ttl_adapter_project_var_count_correct(tmp_path):
    g, ann = _annotate(tmp_path)
    subj = URIRef("http://example.org/q1")
    sf = next(iter(g.objects(subj, LSQV.hasStructuralFeatures)))
    pv_vals = list(g.objects(sf, LSQV.projectVarCount))
    assert pv_vals, "projectVarCount missing"
    assert int(pv_vals[0]) == 2  # SELECT ?s ?p


def test_ttl_adapter_referenced_term(tmp_path):
    g, _ = _annotate(tmp_path)
    subj = URIRef("http://example.org/q1")
    assert (subj, DCT.subject, URIRef("http://example.org/Class")) in g


def test_ttl_adapter_count_literals_are_integers(tmp_path):
    """Numeric counts must be typed as xsd:integer, not plain strings."""
    g, _ = _annotate(tmp_path)
    subj = URIRef("http://example.org/q1")
    sf = next(iter(g.objects(subj, LSQV.hasStructuralFeatures)))
    for prop in (LSQV.bgpCount, LSQV.tpCount, LSQV.projectVarCount):
        for val in g.objects(sf, prop):
            assert isinstance(val, Literal), f"{prop} value is not a Literal"
            assert val.datatype in (XSD.integer, XSD.int, XSD.nonNegativeInteger), (
                f"{prop} has unexpected datatype {val.datatype}"
            )


def test_ttl_adapter_multiline_query(tmp_path):
    """Queries with newlines in lsqv:text must be read and annotated correctly."""
    ttl = textwrap.dedent("""
        @prefix lsqv: <http://lsq.aksw.org/vocab#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        <http://example.org/q2> a lsqv:Query ;
            rdfs:label "multiline" ;
            lsqv:text \"\"\"SELECT ?s
WHERE {
  ?s ?p ?o .
  ?s a <http://example.org/C>
}\"\"\" .
    """)
    g, ann = _annotate(tmp_path, ttl)
    assert len(ann) == 1
    assert ann[0].is_valid
    assert ann[0].operators.tp_count == 2


def test_classify_strips_cr_from_text(tmp_path):
    """CRLF (\\r\\n) in lsqv:text must be normalized to LF; standalone \\r preserved."""
    import textwrap
    from rdflib import URIRef
    from sparql_annotator.classifier import QuestionTypeClassifier
    from sparql_annotator.namespaces import LSQV
    from sparql_annotator.cli import _generate_type_assertions
    import logging

    onto = tmp_path / "mini.ttl"
    onto.write_text(
        textwrap.dedent("""
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
                    owl:hasValue lsqv:Select ] ] .
    """),
        encoding="utf-8",
    )

    # Query 1: CRLF line endings — \r\n should become \n
    # Query 2: standalone \r (not followed by \n) — should be preserved
    qf = tmp_path / "q.ttl"
    qf.write_bytes(
        b"@prefix lsqv: <http://lsq.aksw.org/vocab#> .\n"
        b"@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        b"<http://example.org/q1> a lsqv:Query ;\n"
        b'    lsqv:text """SELECT ?s\r\nWHERE { ?s ?p ?o }""" ;\n'
        b"    lsqv:hasStructuralFeatures [ lsqv:usesFeature lsqv:Select ] .\n"
    )

    clf = QuestionTypeClassifier(onto, logger=logging.getLogger("test"))
    results = clf.classify_queries_from_file(qf)
    out = _generate_type_assertions(qf, results, clf, logging.getLogger("test"))

    for text_lit in out.objects(URIRef("http://example.org/q1"), LSQV.text):
        text = str(text_lit)
        assert "\r\n" not in text, f"CRLF found in lsqv:text: {text!r}"
        assert "\n" in text, "LF should be present after CRLF normalization"
