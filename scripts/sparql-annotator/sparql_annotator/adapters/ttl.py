from pathlib import Path
from typing import Iterable, Union, Optional
import io

try:
    from rdflib import Graph, Namespace, RDF, URIRef, BNode, Literal
    from rdflib.namespace import RDFS
except Exception:  # pragma: no cover - optional runtime
    Graph = None

from ..model import QueryRecord, Annotation
from .base import InputAdapter, OutputAdapter


class TTLAdapter(InputAdapter, OutputAdapter):
    # predicates that may hold query text
    DEFAULT_TEXT_PREDICATES = [
        "http://persistence.uni-leipzig.org/nlp2rdf/ontologies/lsqv#text",
        "http://purl.org/ontology/shui#queryText",
        "http://www.w3.org/2000/01/rdf-schema#label",
    ]

    def __init__(
        self,
        query_predicates: Optional[Iterable[str]] = None,
        output_ns: str = "http://example.org/sparqla#",
        lsq: bool = False,
    ):
        self.query_predicates = list(query_predicates) if query_predicates else self.DEFAULT_TEXT_PREDICATES
        self.output_ns = output_ns
        self._last_graph = None
        self.lsq = lsq

    def read(self, source: Union[Path, io.IOBase, str]):
        if not Graph:
            raise RuntimeError("rdflib is required for TTL adapter")

        g = Graph()
        if isinstance(source, (str, Path)):
            g.parse(str(source), format="turtle")
        else:
            g.parse(source, format="turtle")
        # remember the graph for later output
        self._last_graph = g

        for s, p, o in g.triples((None, None, None)):
            if str(p) in self.query_predicates and (o is not None):
                text = str(o)
                uri = str(s)
                label = None
                if (s, RDFS.label, None) in g:
                    label = str(g.value(s, RDFS.label))

                # collect all triples about the subject as metadata
                meta = {}
                for _s, _p, _o in g.triples((s, None, None)):
                    key = str(_p)
                    val = str(_o)
                    if key in meta:
                        # convert to list
                        if isinstance(meta[key], list):
                            meta[key].append(val)
                        else:
                            meta[key] = [meta[key], val]
                    else:
                        meta[key] = val

                yield QueryRecord(uri=uri, label=label, text=text, metadata=meta)

    def write(self, annotations: Iterable[Annotation], destination: Union[Path, io.IOBase, str]):
        if not Graph:
            raise RuntimeError("rdflib is required for TTL adapter")

        # reuse the last-read graph when possible so we preserve original triples
        if getattr(self, "_last_graph", None) is not None:
            g = self._last_graph
        else:
            g = Graph()

        SPARQLA = Namespace(self.output_ns)
        LSQV = Namespace("http://lsq.aksw.org/vocab#")
        DCT = Namespace("http://purl.org/dc/terms/")

        # helper to convert operator names into lsqv term names (rough map)
        def _op_to_lsq_term(op_name: str) -> str:
            # turn UPPER_UNDERSCORE into TitleCase, e.g. "PROPERTY_PATH" -> "PropertyPath"
            return op_name.title().replace("_", "")

        from .. import metrics

        for ann in annotations:
            if ann.record.uri:
                subj = URIRef(ann.record.uri)
            else:
                subj = BNode()

            # keep a simple CSV operators literal for backwards compatibility
            g.add((subj, SPARQLA.operators, Literal(",".join(sorted(ann.operators.raw)))))

            if self.lsq:
                # compute structural metrics from the raw query text
                bgp_count, tp_count, proj_vars = metrics.compute_structural_metrics(ann.record.text)

                sf = BNode()
                g.add((subj, LSQV.hasStructuralFeatures, sf))
                g.add((sf, RDF.type, LSQV.StructuralFeatures))
                g.add((sf, LSQV.bgpCount, Literal(bgp_count)))
                g.add((sf, LSQV.tpCount, Literal(tp_count)))
                g.add((sf, LSQV.projectVarCount, Literal(proj_vars)))

                # usesFeature entries
                for op in sorted(ann.operators.raw):
                    term = _op_to_lsq_term(op)
                    g.add((sf, LSQV.usesFeature, LSQV[term]))

                # dct:subject — referenced IRIs found in the query text
                for iri in metrics.referenced_terms(ann.record.text):
                    try:
                        g.add((subj, DCT.subject, URIRef(iri)))
                        g.add((sf, DCT.subject, URIRef(iri)))
                    except Exception:
                        # ignore malformed IRIs
                        continue

        if isinstance(destination, (str, Path)):
            g.serialize(destination=str(destination), format="turtle")
        else:
            destination.write(g.serialize(format="turtle"))
