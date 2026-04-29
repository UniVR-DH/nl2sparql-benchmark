import textwrap
from pathlib import Path

from rdflib import Graph, Namespace, URIRef

from sparql_annotator.adapters.ttl import TTLAdapter
from sparql_annotator.annotator import Annotator


LSQV = Namespace("http://lsq.aksw.org/vocab#")
DCT = Namespace("http://purl.org/dc/terms/")


def test_ttl_adapter_emits_lsq(tmp_path: Path):
    src = tmp_path / "in.ttl"
    out = tmp_path / "out.ttl"
    data = textwrap.dedent(
        """
        @prefix lsqv: <http://lsq.aksw.org/vocab#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

        <http://example.org/q1> a lsqv:Query ;
            rdfs:label "q1" ;
            lsqv:text "SELECT ?s WHERE { ?s a <http://example.org/Class> }" .
        """
    )
    src.write_text(data, encoding="utf-8")

    adapter = TTLAdapter(lsq=True)
    ann = Annotator().annotate_file(str(src), input_adapter=adapter)
    assert len(ann) == 1
    adapter.write(ann, str(out))

    g = Graph()
    g.parse(str(out), format="turtle")

    subj = URIRef("http://example.org/q1")
    sfs = list(g.objects(subj, LSQV.hasStructuralFeatures))
    assert sfs, "No lsqv:hasStructuralFeatures found"
    sf = sfs[0]

    # structural metrics present
    assert (sf, LSQV.bgpCount, None) in g
    assert (sf, LSQV.tpCount, None) in g
    assert (sf, LSQV.projectVarCount, None) in g

    # referenced term present as dct:subject
    assert (subj, DCT.subject, URIRef("http://example.org/Class")) in g
