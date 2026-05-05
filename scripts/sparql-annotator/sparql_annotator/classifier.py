"""QuestionTypeClassifier: classifies SPARQL queries by question type using LSQ features."""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from rdflib import Graph, URIRef, RDF, RDFS
from rdflib.term import Variable as _Variable
import rdflib.plugins.sparql.parser as _sparql_parser
import rdflib.plugins.sparql.algebra as _sparql_algebra

from .model import QuestionTypeDefinition, OperatorSet
from .ontology import build_type_definitions, build_depth_cache, _uri_to_local
from .algebra import detect_lsq_features, compute_metrics, extract_operators
from .namespaces import LSQV

# Return type for classify_queries_from_graph / classify_queries_from_file:
# (qtypes, features, label, warnings, counts, op_set)
QueryResult = Tuple[
    Set[str], Set[str], Optional[str], List[str], Dict[str, int], Optional[OperatorSet]
]


class QuestionTypeClassifier:
    """Classifies SPARQL queries by question type based on LSQ structural features."""

    def __init__(self, ontology_path: Path, logger: Optional[logging.Logger] = None):
        self.ontology_path = ontology_path
        self.logger = logger or logging.getLogger(__name__)

        self.ontology = Graph()
        self.ontology.parse(str(ontology_path), format="turtle")
        self.logger.info(
            f"Loaded ontology from {ontology_path} ({len(self.ontology)} triples)"
        )

        self.type_definitions: Dict[str, QuestionTypeDefinition] = (
            build_type_definitions(self.ontology, self.logger)
        )
        self.type_uris: Dict[str, URIRef] = {
            name: defn.uri for name, defn in self.type_definitions.items()
        }
        self._depth_cache: Dict[str, int] = build_depth_cache(self.type_definitions)

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------

    def _check_count_annotations(
        self, query_graph: Graph, query_uri: URIRef, algebra_node, short_uri: str
    ) -> List[str]:
        warnings: List[str] = []
        declared: Dict[str, Optional[int]] = {
            "projectVarCount": None,
            "bgpCount": None,
            "tpCount": None,
        }
        for sf in query_graph.objects(query_uri, LSQV.hasStructuralFeatures):
            for prop_local, lsqv_prop in (
                ("projectVarCount", LSQV.projectVarCount),
                ("bgpCount", LSQV.bgpCount),
                ("tpCount", LSQV.tpCount),
            ):
                for val in query_graph.objects(sf, lsqv_prop):
                    try:
                        declared[prop_local] = int(val)
                    except (ValueError, TypeError):
                        pass

        if all(v is None for v in declared.values()):
            return warnings

        actual_bgp, actual_tp, actual_pv = compute_metrics(algebra_node)
        actual = {
            "projectVarCount": actual_pv,
            "bgpCount": actual_bgp,
            "tpCount": actual_tp,
        }
        is_select = algebra_node.name == "SelectQuery"

        for prop, declared_val in declared.items():
            if declared_val is None:
                continue
            if prop == "projectVarCount" and not is_select:
                self.logger.debug(
                    f"Query {short_uri!r}: skipping lsqv:projectVarCount check "
                    f"(query type is {algebra_node.name}, not SelectQuery)"
                )
                continue
            actual_val = actual[prop]
            if declared_val != actual_val:
                msg = f"lsqv:{prop} declared={declared_val} but algebra yields {actual_val}"
                warnings.append(msg)
                self.logger.warning(f"Query {short_uri!r}: {msg}")
            else:
                self.logger.debug(
                    f"Query {short_uri!r}: lsqv:{prop} = {declared_val} ✓"
                )
        return warnings

    def _check_sparql_syntax(self, text: str, short_uri: str) -> bool:
        try:
            _sparql_parser.parseQuery(text)
            return True
        except Exception as exc:
            self.logger.error(f"Query {short_uri!r}: invalid SPARQL — {exc}")
            return False

    def extract_features(
        self,
        query_graph: Graph,
        query_uri: URIRef,
        parsed: object = None,
        alg: object = None,
    ) -> Tuple[Set[str], List[str]]:
        short_uri = str(query_uri).split("/")[-1]
        features: Set[str] = set()
        warnings: List[str] = []

        for sf in query_graph.objects(query_uri, LSQV.hasStructuralFeatures):
            for feat_uri in query_graph.objects(sf, LSQV.usesFeature):
                name = _uri_to_local(feat_uri)
                if name:
                    features.add(name)

        sparql_lits = list(query_graph.objects(query_uri, LSQV.text))
        if len(sparql_lits) > 1:
            self.logger.warning(
                f"Query {short_uri!r}: {len(sparql_lits)} lsqv:text values found; "
                f"only the first will be used"
            )
        for sparql_lit in sparql_lits[:1]:
            text = str(sparql_lit)
            # Use pre-parsed objects when provided (avoids redundant parse/translate)
            if parsed is None or alg is None:
                if not self._check_sparql_syntax(text, short_uri):
                    continue
                try:
                    parsed = _sparql_parser.parseQuery(text)
                    alg = _sparql_algebra.translateQuery(parsed)
                except Exception as exc:
                    self.logger.error(
                        f"Query {short_uri!r}: algebra translation failed — {exc}"
                    )
                    continue

            # Pure-aggregate Distinct stripping
            if (
                "Aggregators" in features
                and "GroupBy" not in features
                and "Distinct" in features
            ):
                try:
                    projection = parsed[1].get("projection", [])
                    has_bare_var = any(
                        isinstance(item.get("var"), _Variable)
                        and not isinstance(item.get("evar"), _Variable)
                        for item in projection
                        if hasattr(item, "get")
                    )
                    if not has_bare_var:
                        self.logger.debug(
                            f"Query {short_uri!r}: stripping spurious Distinct "
                            f"(COUNT(DISTINCT ...) inside aggregate, no bare vars)"
                        )
                        features.discard("Distinct")
                except Exception:
                    pass

            implied = detect_lsq_features(alg.algebra, parsed)
            missing = implied - features
            if missing:
                for feat_name in sorted(missing):
                    msg = (
                        f"algebra contains '{feat_name}' node but it is absent "
                        f"from declared LSQ features — merging into feature set"
                    )
                    warnings.append(msg)
                    self.logger.warning(f"Query {short_uri!r}: {msg}")
                features |= implied

            warnings.extend(
                self._check_count_annotations(
                    query_graph, query_uri, alg.algebra, short_uri
                )
            )

        return features, warnings

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def _ancestors(self, name: str) -> Set[str]:
        result: Set[str] = set()

        def _visit(n: str) -> None:
            for parent in self.type_definitions[n].parent_types:
                if parent in self.type_definitions and parent not in result:
                    result.add(parent)
                    _visit(parent)

        _visit(name)
        return result

    def classify_query(self, query_uri: URIRef, features: Set[str]) -> Set[str]:
        candidates = [
            name
            for name, defn in self.type_definitions.items()
            if defn.matches(features)
        ]
        if not candidates:
            return set()

        candidate_set = set(candidates)
        pruned: Set[str] = set(candidate_set)
        for a in list(candidate_set):
            for b in candidate_set:
                if a != b and a in self._ancestors(b):
                    pruned.discard(a)

        if not pruned:
            return set()

        pruned_list = sorted(pruned)
        resolved: Set[str] = set(pruned)
        for a in pruned_list:
            for b in pruned_list:
                if a != b and a in self.type_definitions[b].disjoint_with:
                    len_a = len(self.type_definitions[a].required_features)
                    len_b = len(self.type_definitions[b].required_features)
                    if len_a < len_b:
                        resolved.discard(a)
                    elif len_b < len_a:
                        resolved.discard(b)

        if not resolved:
            return set()

        max_depth = max(self._depth_cache.get(n, 0) for n in resolved)
        return {n for n in resolved if self._depth_cache.get(n, 0) == max_depth}

    # ------------------------------------------------------------------
    # File-level classification
    # ------------------------------------------------------------------

    def classify_queries_from_graph(self, g: Graph) -> Dict[str, QueryResult]:
        """Classify queries from an already-parsed RDF graph (avoids re-parsing the TTL)."""
        query_uris = sorted(set(g.subjects(RDF.type, LSQV.Query)), key=str)
        self.logger.info(f"Found {len(query_uris)} queries to classify")

        results: Dict[str, QueryResult] = {}
        for uri in query_uris:
            if not any(True for _ in g.objects(uri, LSQV.text)):
                short = str(uri).split("/")[-1]
                self.logger.info(f"Skipping {short!r}: no lsqv:text present")
                continue

            # Parse once per query and reuse for features, metrics, and operators
            text: Optional[str] = None
            parsed = None
            alg = None
            for sparql_lit in g.objects(uri, LSQV.text):
                text = str(sparql_lit)
                try:
                    parsed = _sparql_parser.parseQuery(text)
                    alg = _sparql_algebra.translateQuery(parsed)
                except Exception as exc:
                    short = str(uri).split("/")[-1]
                    self.logger.error(
                        f"Query {short!r}: parse/translate failed — {exc}"
                    )
                break

            if not text or not alg:
                # Parse/translate failed — still collect TTL-declared features
                features, warnings = self.extract_features(g, uri)
                if text and not alg:
                    short = str(uri).split("/")[-1]
                    warnings.append(
                        "SPARQL parse/translate failed; algebra-derived features unavailable"
                    )
                qtypes = self.classify_query(uri, features)
                label: Optional[str] = None
                for lit in g.objects(uri, RDFS.label):
                    label = str(lit)
                    break
                results[str(uri)] = (qtypes, features, label, warnings, {}, None)
                continue

            features, warnings = self.extract_features(g, uri, parsed=parsed, alg=alg)
            qtypes = self.classify_query(uri, features)

            label: Optional[str] = None
            for lit in g.objects(uri, RDFS.label):
                label = str(lit)
                break

            short = str(uri).split("/")[-1]
            if qtypes:
                self.logger.debug(f"Query {short}: → {', '.join(sorted(qtypes))}")
            else:
                self.logger.warning(
                    f"Query {short}: unclassifiable "
                    f"(features: {', '.join(sorted(features)) or 'none'})"
                )

            counts: Dict[str, int] = {}
            op_set = None
            try:
                bgp_c, tp_c, pv_c = compute_metrics(alg.algebra)
                counts = {"bgpCount": bgp_c, "tpCount": tp_c, "projectVarCount": pv_c}
            except Exception as exc:
                self.logger.error(
                    f"Query {short!r}: metrics computation failed — {exc}"
                )
            try:
                op_set = extract_operators(text, parsed, alg=alg)
            except Exception as exc:
                self.logger.error(
                    f"Query {short!r}: operator extraction failed — {exc}"
                )

            results[str(uri)] = (qtypes, features, label, warnings, counts, op_set)

        return results

    def classify_queries_from_file(self, query_file: Path) -> Dict[str, QueryResult]:
        g = Graph()
        try:
            g.parse(str(query_file), format="turtle")
            self.logger.info(f"Loaded {len(g)} triples from {query_file}")
        except Exception as exc:
            self.logger.error(f"Failed to parse {query_file}: {exc}")
            raise
        return self.classify_queries_from_graph(g)
