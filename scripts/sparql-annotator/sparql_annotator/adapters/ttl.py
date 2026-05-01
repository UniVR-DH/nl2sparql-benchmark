from pathlib import Path
from typing import Iterable, Optional, Union
import io

from rdflib import Graph, Namespace, RDF, URIRef, BNode, Literal
from rdflib.namespace import RDFS

from ..model import QueryRecord, Annotation
from .base import InputAdapter, OutputAdapter
from ..namespaces import LSQV
from ..algebra import compute_metrics, referenced_terms

DCT = Namespace("http://purl.org/dc/terms/")
SPARQLA = Namespace("http://example.org/sparqla#")


class TTLAdapter(InputAdapter, OutputAdapter):
    DEFAULT_TEXT_PREDICATES = [
        str(LSQV.text),
        "http://purl.org/ontology/shui#queryText",
    ]

    def __init__(
        self,
        query_predicates: Optional[Iterable[str]] = None,
        output_ns: str = str(SPARQLA),
        lsq: bool = False,
    ):
        self.query_predicates = (
            list(query_predicates) if query_predicates else self.DEFAULT_TEXT_PREDICATES
        )
        self.output_ns = output_ns
        self._last_graph: Optional[Graph] = None
        self.lsq = lsq

    def read(self, source: Union[Path, io.IOBase, str]):
        g = Graph()
        if isinstance(source, (str, Path)):
            g.parse(str(source), format="turtle")
        else:
            g.parse(source, format="turtle")
        self._last_graph = g

        for s, p, o in g.triples((None, None, None)):
            if str(p) in self.query_predicates and o is not None:
                text = str(o)
                uri = str(s)
                label = (
                    str(g.value(s, RDFS.label)) if (s, RDFS.label, None) in g else None
                )
                meta = {}
                for _s, _p, _o in g.triples((s, None, None)):
                    key = str(_p)
                    val = str(_o)
                    if key in meta:
                        meta[key] = (
                            meta[key] if isinstance(meta[key], list) else [meta[key]]
                        )
                        meta[key].append(val)
                    else:
                        meta[key] = val
                yield QueryRecord(uri=uri, label=label, text=text, metadata=meta)

    def write(
        self,
        annotations: Iterable[Annotation],
        destination: Union[Path, io.IOBase, str],
    ):
        g = self._last_graph if self._last_graph is not None else Graph()
        OUT = Namespace(self.output_ns)

        def _op_to_lsq_term(op_name: str) -> str:
            return op_name.title().replace("_", "")

        for ann in annotations:
            subj = URIRef(ann.record.uri) if ann.record.uri else BNode()
            g.add((subj, OUT.operators, Literal(",".join(sorted(ann.operators.raw)))))

            if self.lsq:
                bgp_count, tp_count, proj_vars = compute_metrics_from_text(
                    ann.record.text
                )
                sf = BNode()
                g.add((subj, LSQV.hasStructuralFeatures, sf))
                g.add((sf, RDF.type, LSQV.StructuralFeatures))
                g.add((sf, LSQV.bgpCount, Literal(bgp_count)))
                g.add((sf, LSQV.tpCount, Literal(tp_count)))
                g.add((sf, LSQV.projectVarCount, Literal(proj_vars)))
                for op in sorted(ann.operators.raw):
                    g.add((sf, LSQV.usesFeature, LSQV[_op_to_lsq_term(op)]))
                for iri in referenced_terms(ann.record.text):
                    try:
                        g.add((subj, DCT.subject, URIRef(iri)))
                        g.add((sf, DCT.subject, URIRef(iri)))
                    except Exception:
                        continue

        if isinstance(destination, (str, Path)):
            g.serialize(destination=str(destination), format="turtle")
        else:
            destination.write(g.serialize(format="turtle"))


def compute_metrics_from_text(text: str):
    """Parse text and return (bgp_count, tp_count, project_var_count)."""
    import rdflib.plugins.sparql.parser as _p
    import rdflib.plugins.sparql.algebra as _a

    try:
        parsed = _p.parseQuery(text)
        alg = _a.translateQuery(parsed)
        return compute_metrics(alg.algebra)
    except Exception as exc:
        raise ValueError(f"Could not parse query: {exc}") from exc
