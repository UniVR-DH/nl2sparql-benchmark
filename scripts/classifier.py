#!/usr/bin/env python3
"""
Automated Question Type Classifier for nl2s-bench

The classifier:
1. Parses the ontology to extract OWL restrictions for each question type
    (including inherited features via transitive rdfs:subClassOf)
2. Builds a symmetric disjointness closure
3. Matches queries against feature requirements
4. Resolves multiple matches by preferring the most specific type in a hierarchy,
    or flags genuine ambiguity

The classifier merges LSQ-declared features with algebra-derived features.
Algebra-derived features are treated as semantic enrichments and may include
both structural and functional variants of the same SPARQL construct (e.g.,
NotExists and fn-notexists). When multiple valid representations exist, all
are retained.

Known limitations / design notes
---------------------------------
1. _ancestors() has no explicit cycle guard.
   The method relies on parent_types being acyclic (i.e. the subclass hierarchy
   is a DAG). A cycle in the ontology would cause infinite recursion and a
   RecursionError. In practice ontology subclass hierarchies are always DAGs,
   but a defensive visited-set could be added cheaply if untrusted ontologies
   are ever loaded.

2. classify_queries_from_file() re-parses and re-translates each query's SPARQL
   text a second time (after extract_features already did so) purely to compute
   the algebra-derived counts dict.  This is redundant work.  A future
   refactor could have extract_features() cache and return the algebra object
   so classify_queries_from_file() can reuse it directly.

3. The HAVING-filter detection in detect_features_from_algebra uses a
   parse-tree flag (has_having) combined with a structural check on the Filter
   node's immediate child.  The structural check requires the Filter's direct
   child to be AggregateJoin or Group.  In practice rdflib always places HAVING
   as the outermost Filter over AggregateJoin, so this holds, but it is a
   coupling to rdflib's internal algebra representation that could break across
   rdflib versions.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from rdflib import Graph, Namespace, URIRef, RDF, RDFS, Literal
from rdflib.term import Variable as _Variable
import rdflib.plugins.sparql.parser as _sparql_parser
import rdflib.plugins.sparql.algebra as _sparql_algebra

from model import build_type_definitions, build_depth_cache
from model import FeatureRequirement, QuestionTypeDefinition
from algebra_features import detect_features_from_algebra, count_from_algebra

# ---------------------------------------------------------------------------
# Namespace definitions
# ---------------------------------------------------------------------------

LSQV = Namespace("http://lsq.aksw.org/vocab#")
QAT = Namespace("https://w3id.org/univr-qa/qatypes#")
QA = Namespace("https://w3id.org/wdaqua/qanary#")


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class QuestionTypeClassifier:
    """Classifies SPARQL queries by question type based on LSQ structural features."""

    def __init__(self, ontology_path: Path, logger: Optional[logging.Logger] = None):
        self.ontology_path = ontology_path
        self.logger = logger or logging.getLogger(__name__)

        self.ontology = Graph()
        self.ontology.parse(str(ontology_path), format="turtle")
        self.logger.info(f"Loaded ontology from {ontology_path} ({len(self.ontology)} triples)")

        self.type_definitions: Dict[str, QuestionTypeDefinition] = build_type_definitions(self.ontology, self.logger)
        self.type_uris: Dict[str, URIRef] = {
            name: defn.uri for name, defn in self.type_definitions.items()
        }

        self._depth_cache: Dict[str, int] = build_depth_cache(self.type_definitions)

    # ------------------------------------------------------------------
    # Feature extraction from queries
    # ------------------------------------------------------------------

    def _uri_to_local(self, uri: URIRef) -> Optional[str]:
        """Return the local name (fragment) of a URI, or None."""
        s = str(uri)
        return s.split("#")[-1] if "#" in s else None

    def _check_count_annotations(
        self,
        query_graph: Graph,
        query_uri: URIRef,
        algebra_node,
        short_uri: str,
    ) -> List[str]:
        """
        Compare the numeric count annotations declared in the LSQ
        StructuralFeatures node (projectVarCount, bgpCount, tpCount) against
        the values computed from the algebra tree.

        Returns a list of warning strings for any mismatch found.
        Emits each warning via the logger as well.

        LSQ properties checked:
          lsqv:projectVarCount  — top-level projected variable count
          lsqv:bgpCount         — number of BGP nodes in the query
          lsqv:tpCount          — total triple pattern count across all BGPs
        """
        warnings: List[str] = []

        # Read declared counts from the StructuralFeatures bnode
        declared: Dict[str, Optional[int]] = {
            "projectVarCount": None,
            "bgpCount": None,
            "tpCount": None,
        }
        for sf in query_graph.objects(query_uri, LSQV.hasStructuralFeatures):
            for prop_local, lsqv_prop in (
                ("projectVarCount", LSQV.projectVarCount),
                ("bgpCount",        LSQV.bgpCount),
                ("tpCount",         LSQV.tpCount),
            ):
                for val in query_graph.objects(sf, lsqv_prop):
                    try:
                        declared[prop_local] = int(val)
                    except (ValueError, TypeError):
                        pass

        # Nothing declared — nothing to check
        if all(v is None for v in declared.values()):
            return warnings

        actual_bgp, actual_tp, actual_pv = count_from_algebra(algebra_node)
        actual: Dict[str, int] = {
            "projectVarCount": actual_pv,
            "bgpCount":        actual_bgp,
            "tpCount":         actual_tp,
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
                msg = (
                    f"lsqv:{prop} declared={declared_val} but "
                    f"algebra yields {actual_val}"
                )
                warnings.append(msg)
                self.logger.warning(f"Query {short_uri!r}: {msg}")
            else:
                self.logger.debug(
                    f"Query {short_uri!r}: lsqv:{prop} = {declared_val} ✓"
                )

        return warnings

    def _check_sparql_syntax(self, text: str, short_uri: str) -> bool:
        """
        Check SPARQL syntax and log errors. Returns False if invalid.

        This is a diagnostic check, not a gate: feature extraction
        proceeds regardless of the return value.
        """
        try:
            _sparql_parser.parseQuery(text)
            return True
        except Exception as exc:
            self.logger.error(f"Query {short_uri!r}: invalid SPARQL — {exc}")
            return False

    def extract_features(
        self, query_graph: Graph, query_uri: URIRef
    ) -> Tuple[Set[str], List[str]]:
        """
        Extract LSQ structural features for a query.

        Also strips spurious lsqv:Distinct that arises from COUNT(DISTINCT ...)
        inside a pure-aggregate query (no GROUP BY, no bare projected variables).
        In that case DISTINCT is an implementation detail of the aggregate
        expression, not a SELECT DISTINCT at the query level.

        After extraction, the SPARQL text is compiled into a rdflib algebra
        tree via detect_features_from_algebra. Structural features found in
        the algebra but absent from the declared LSQ feature set are collected
        as warnings and returned alongside the feature set. LSQ-declared
        features are treated as the base annotation and algebra-derived
        features enrich that set; when both representations are semantically
        valid, both are preserved.

        Returns:
            (features, warnings) where warnings is a list of human-readable
            strings describing each LSQ annotation gap found.
        """
        short_uri = str(query_uri).split("/")[-1]
        features: Set[str] = set()
        warnings: List[str] = []

        for sf in query_graph.objects(query_uri, LSQV.hasStructuralFeatures):
            for feat_uri in query_graph.objects(sf, LSQV.usesFeature):
                name = self._uri_to_local(feat_uri)
                if name:
                    features.add(name)

        # Syntax check, pure-aggregate Distinct stripping, and algebra-based
        # structural feature gap detection.
        for sparql_lit in query_graph.objects(query_uri, LSQV.text):
            text = str(sparql_lit)
            syntax_ok = self._check_sparql_syntax(text, short_uri)

            if not syntax_ok:
                continue  # can't build algebra from invalid SPARQL

            try:
                parsed = _sparql_parser.parseQuery(text)
                algebra = _sparql_algebra.translateQuery(parsed)
            except Exception as exc:
                self.logger.error(f"Query {short_uri!r}: algebra translation failed — {exc}")
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
                    pass  # leave features unchanged if projection walk fails

            # Algebra-based structural feature detection and enrichment.
            implied = detect_features_from_algebra(algebra.algebra, parsed)

            # Enrich LSQ-declared features with algebra-derived features.
            # Algebra features are additive and may include:
            # - structural features missing in LSQ annotations
            # - functional variants (e.g. fn-exists, fn-notexists)
            # Both representations are preserved when applicable.
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

            # Numeric count annotation checks (projectVarCount, bgpCount, tpCount).
            warnings.extend(
                self._check_count_annotations(
                    query_graph, query_uri, algebra.algebra, short_uri
                )
            )

        return features, warnings

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def _ancestors(self, name: str) -> Set[str]:
        """
        Return the strict ancestor set of a type — i.e. all types reachable
        via parent_types — NOT including the type itself.

        Note: no explicit cycle guard. See module-level limitation note 1.
        """
        result: Set[str] = set()

        def _visit(n: str) -> None:
            for parent in self.type_definitions[n].parent_types:
                if parent in self.type_definitions and parent not in result:
                    result.add(parent)
                    _visit(parent)

        _visit(name)
        return result

    def classify_query(self, query_uri: URIRef, features: Set[str]) -> Set[str]:
        """
        Classify a query given its feature set.

        Algorithm:
        1. Collect candidate types whose all_requirements are satisfied by features
           (uses FeatureRequirement.satisfied_by, which correctly handles union alternatives)
        2. Among candidates, prefer the most specific type(s):
           remove any candidate that is a strict ancestor of another candidate
        3. Resolve remaining disjointness conflicts by preferring the type with
           more specific feature requirements (larger required_features set)
        4. Return the resulting set (empty = unclassifiable)
        """
        # Step 1: candidates whose requirements are all satisfied
        candidates: List[str] = [
            name for name, defn in self.type_definitions.items()
            if defn.matches(features)
        ]

        if not candidates:
            return set()

        candidate_set = set(candidates)

        # Step 2: remove any candidate that is a strict ancestor of another candidate.
        pruned: Set[str] = set(candidate_set)
        for a in list(candidate_set):
            for b in candidate_set:
                if a == b:
                    continue
                if a in self._ancestors(b):
                    pruned.discard(a)

        if not pruned:
            return set()

        # Step 3: resolve disjointness conservatively
        pruned_list = sorted(pruned)
        resolved: Set[str] = set(pruned)
        for a in pruned_list:
            for b in pruned_list:
                if a == b:
                    continue
                if a in self.type_definitions[b].disjoint_with:
                    len_a = len(self.type_definitions[a].required_features)
                    len_b = len(self.type_definitions[b].required_features)
                    if len_a < len_b:
                        resolved.discard(a)
                    elif len_b < len_a:
                        resolved.discard(b)

        if not resolved:
            return set()

        # Step 4: prefer the deepest (most specific) type in the subclass tree.
        max_depth = max(self._depth_cache.get(n, 0) for n in resolved)
        most_specific = {n for n in resolved if self._depth_cache.get(n, 0) == max_depth}

        return most_specific

    # ------------------------------------------------------------------
    # File-level classification
    # ------------------------------------------------------------------

    def classify_queries_from_file(
        self, query_file: Path
    ) -> Dict[str, Tuple[Set[str], Set[str], Optional[str], List[str], Dict[str, int]]]:
        """
        Classify all lsqv:Query resources in a Turtle file.

        Returns:
            Dict mapping query URI string →
                (matched_types, features, rdfs:label or None, warnings, counts)
            where warnings is the list of LSQ annotation-gap messages
            collected by extract_features for that query, and counts is a dict
            of algebra-derived numeric annotations (bgpCount, tpCount,
            projectVarCount).

        Note: the SPARQL text for each query is parsed and compiled twice —
        once inside extract_features and once here to compute the counts dict.
        This is redundant work. A future refactor could return the algebra
        object from extract_features so it can be reused here. See module-level
        limitation note 2.
        """
        g = Graph()
        try:
            g.parse(str(query_file), format="turtle")
            self.logger.info(f"Loaded {len(g)} triples from {query_file}")
        except Exception as exc:
            self.logger.error(f"Failed to parse {query_file}: {exc}")
            raise

        query_uris = list(g.subjects(RDF.type, LSQV.Query))
        self.logger.info(f"Found {len(query_uris)} queries to classify")

        results: Dict[str, Tuple[Set[str], Set[str], Optional[str], List[str], Dict[str, int]]] = {}
        for uri in query_uris:
            has_text = any(True for _ in g.objects(uri, LSQV.text))
            if not has_text:
                short = str(uri).split("/")[-1]
                self.logger.info(f"Skipping {short!r}: no lsqv:text present")
                continue

            features, warnings = self.extract_features(g, uri)
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

            # Compute algebra-derived counts (bgpCount, tpCount, projectVarCount).
            # TODO: avoid re-parsing by caching the algebra in extract_features.
            counts: Dict[str, int] = {}
            for sparql_lit in g.objects(uri, LSQV.text):
                text = str(sparql_lit)
                try:
                    parsed = _sparql_parser.parseQuery(text)
                    algebra = _sparql_algebra.translateQuery(parsed)
                    bgp_c, tp_c, pv_c = count_from_algebra(algebra.algebra)
                    counts = {
                        "bgpCount": bgp_c,
                        "tpCount": tp_c,
                        "projectVarCount": pv_c,
                    }
                except Exception:
                    counts = {}
                break

            results[str(uri)] = (qtypes, features, label, warnings, counts)

        return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
# Note: CLI entry point moved to scripts/classify_questions_cli.py

# Final Reporting is handled by the CLI wrapper in `scripts/classify_questions_cli.py`,
# which receives the full classification results dict from classify_queries_from_file
# and is responsible for formatting and printing the final report to the console.
# See: `print_results` in `scripts/classify_questions_cli.py` for details.

# Logging is configured by the CLI wrapper in
# `scripts/classify_questions_cli.py`. The `QuestionTypeClassifier`
# accepts an injected `logger` instance (see its constructor) so the
# CLI is responsible for configuring handlers and levels.
# See: `configure_logging` in `scripts/classify_questions_cli.py` for details.

# ------------------------------------------------------------------
# RDF output
# ------------------------------------------------------------------
# RDF output generation is handled by the CLI wrapper in
# `scripts/classify_questions_cli.py`.
# See `generate_type_assertions` in the CLI module for the implementation.
