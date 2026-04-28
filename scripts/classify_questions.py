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
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from rdflib import Graph, Namespace, URIRef, RDF, RDFS, Literal
from rdflib.namespace import OWL
import rdflib.plugins.sparql.parser as _sparql_parser
import rdflib.plugins.sparql.algebra as _sparql_algebra
from rdflib.plugins.sparql.parserutils import CompValue

# ---------------------------------------------------------------------------
# Namespace definitions
# ---------------------------------------------------------------------------

LSQV = Namespace("http://lsq.aksw.org/vocab#")
QAT = Namespace("https://w3id.org/univr-qa/qatypes#")
QA = Namespace("https://w3id.org/wdaqua/qanary#")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FeatureRequirement:
    """
    Represents one structural-feature restriction block from the ontology.

    Two shapes are supported:
      - owl:hasValue lsqv:X        → required = {"X"}, alternatives = {}
        The feature X MUST be present.

      - owl:someValuesFrom (union of X Y Z)  → required = {}, alternatives = {"X","Y","Z"}
        AT LEAST ONE of X, Y, Z must be present.

    A restriction block may also combine both (e.g. hasValue + someValuesFrom
    on the same nested bnode), though that is rare in practice.
    """
    required: Set[str] = field(default_factory=set)
    alternatives: Set[str] = field(default_factory=set)

    def satisfied_by(self, features: Set[str]) -> bool:
        req_ok = self.required <= features
        alt_ok = (not self.alternatives) or bool(self.alternatives & features)
        return req_ok and alt_ok


@dataclass
class QuestionTypeDefinition:
    """Extracted definition of a question type from the ontology."""

    uri: URIRef
    name: str
    # Feature requirements declared directly on this class
    own_requirements: List[FeatureRequirement] = field(default_factory=list)
    # All requirements including those inherited from parent types
    all_requirements: List[FeatureRequirement] = field(default_factory=list)
    # Direct rdfs:subClassOf parent type names (excluding qat:QuestionType itself)
    parent_types: Set[str] = field(default_factory=set)
    # Symmetric closure of owl:disjointWith (populated after all types are loaded)
    disjoint_with: Set[str] = field(default_factory=set)

    @property
    def required_features(self) -> Set[str]:
        """Flat set of all mandatory (non-alternative) features — used for specificity ranking."""
        result: Set[str] = set()
        for req in self.all_requirements:
            result |= req.required
        return result

    def matches(self, features: Set[str]) -> bool:
        """True iff every FeatureRequirement in all_requirements is satisfied."""
        return all(req.satisfied_by(features) for req in self.all_requirements)

    def __repr__(self) -> str:
        parts = []
        for r in self.all_requirements:
            s = ""
            if r.required:
                s += f"required={sorted(r.required)}"
            if r.alternatives:
                s += (" " if s else "") + f"alternatives={sorted(r.alternatives)}"
            parts.append(s)
        reqs = "; ".join(parts) or "(none)"
        d = ", ".join(sorted(self.disjoint_with)) or "(none)"
        return f"QuestionTypeDefinition(name={self.name!r}, requirements=[{reqs}], disjoint=[{d}])"


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

        self.type_definitions: Dict[str, QuestionTypeDefinition] = self._build_type_definitions()
        self.type_uris: Dict[str, URIRef] = {
            name: defn.uri for name, defn in self.type_definitions.items()
        }

        # FIX issue 3: depth cache built once after type definitions are finalised,
        # rather than being recomputed as a local dict on every classify_query call.
        self._depth_cache: Dict[str, int] = self._build_depth_cache()

    # ------------------------------------------------------------------
    # Ontology parsing
    # ------------------------------------------------------------------

    def _uri_to_local(self, uri: URIRef) -> Optional[str]:
        """Return the local name (fragment) of a URI, or None."""
        s = str(uri)
        return s.split("#")[-1] if "#" in s else None

    def _all_question_type_uris(self) -> Set[URIRef]:
        """
        Return all URIs that are (directly or transitively) subclasses of
        qat:QuestionType, excluding qat:QuestionType itself.
        """
        root = QAT.QuestionType
        visited: Set[URIRef] = set()

        def _visit(cls: URIRef) -> None:
            for sub in self.ontology.subjects(RDFS.subClassOf, cls):
                if isinstance(sub, URIRef) and sub not in visited:
                    visited.add(sub)
                    _visit(sub)

        _visit(root)
        return visited

    def _parse_feature_restriction(self, restriction) -> Optional[FeatureRequirement]:
        """
        Parse one bnode restriction of the shape:
          [ owl:onProperty lsqv:hasStructuralFeatures ;
            owl:someValuesFrom [
                owl:onProperty lsqv:usesFeature ;
                owl:hasValue lsqv:X               ← required
              OR
                owl:someValuesFrom [ owl:unionOf (lsqv:X lsqv:Y) ]  ← alternatives
            ]
          ]

        Returns a FeatureRequirement or None if the bnode doesn't match the pattern.
        """
        on_prop = list(self.ontology.objects(restriction, OWL.onProperty))
        if not on_prop or on_prop[0] != LSQV.hasStructuralFeatures:
            return None

        req = FeatureRequirement()

        for svf in self.ontology.objects(restriction, OWL.someValuesFrom):
            nested_prop = list(self.ontology.objects(svf, OWL.onProperty))
            if not nested_prop or nested_prop[0] != LSQV.usesFeature:
                continue

            # Case 1: owl:hasValue lsqv:X  → mandatory feature
            for val in self.ontology.objects(svf, OWL.hasValue):
                name = self._uri_to_local(val)
                if name:
                    req.required.add(name)

            # Only the canonical form is kept:
            # iterate owl:someValuesFrom nodes, then walk each owl:unionOf list
            # with self.ontology.items(), which is the correct rdflib API for
            # RDF lists.
            for union_node in self.ontology.objects(svf, OWL.someValuesFrom):
                for union_list in self.ontology.objects(union_node, OWL.unionOf):
                    for member in self.ontology.items(union_list):
                        name = self._uri_to_local(member)
                        if name:
                            req.alternatives.add(name)

        if not req.required and not req.alternatives:
            return None
        return req

    def _own_requirements_of(self, qtype_uri: URIRef) -> List[FeatureRequirement]:
        """
        Extract all FeatureRequirement objects declared directly on qtype_uri.
        Each owl:someValuesFrom restriction block on lsqv:hasStructuralFeatures
        becomes one FeatureRequirement.
        """
        requirements: List[FeatureRequirement] = []
        for restriction in self.ontology.objects(qtype_uri, RDFS.subClassOf):
            if isinstance(restriction, URIRef):
                continue  # named class, not a restriction bnode
            # Each object of rdfs:subClassOf that is a bnode is expected to be
            # an owl:Restriction (rdf:type owl:Restriction). We rely on the
            # structure check inside _parse_feature_restriction (owl:onProperty
            # must be lsqv:hasStructuralFeatures) to filter out non-feature
            # restriction bnodes such as hasAnswerType restrictions.
            req = self._parse_feature_restriction(restriction)
            if req is not None:
                requirements.append(req)
        return requirements

    def _direct_parent_type_names(self, qtype_uri: URIRef, known_uris: Set[URIRef]) -> Set[str]:
        """Return names of direct question-type parents (not qat:QuestionType itself)."""
        parents: Set[str] = set()
        for parent in self.ontology.objects(qtype_uri, RDFS.subClassOf):
            if isinstance(parent, URIRef) and parent in known_uris:
                name = self._uri_to_local(parent)
                if name:
                    parents.add(name)
        return parents

    def _build_type_definitions(self) -> Dict[str, QuestionTypeDefinition]:
        """
        Build the full type definition map:
        1. Collect all question-type URIs (transitive subclasses of qat:QuestionType)
        2. For each, extract own requirements and direct parents
        3. Propagate inherited requirements (transitive closure over parent_types)
        4. Build symmetric disjointness closure

        after building all definitions, verify that every type
        (including abstract ones like Factoid) has at least one extracted
        requirement. A type with no requirements would match every query,
        causing spurious classifications. A warning is emitted and the type
        is logged clearly so the ontology parser can be debugged.
        """
        all_uris = self._all_question_type_uris()

        # Pass 1: create skeleton definitions
        defs: Dict[str, QuestionTypeDefinition] = {}
        for uri in all_uris:
            name = self._uri_to_local(uri)
            if not name:
                continue
            own = self._own_requirements_of(uri)
            parents = self._direct_parent_type_names(uri, all_uris)
            defs[name] = QuestionTypeDefinition(
                uri=uri,
                name=name,
                own_requirements=own,
                all_requirements=list(own),  # will be expanded below
                parent_types=parents,
            )

        # Pass 2: inherit requirements transitively
        def _collect_requirements(name: str, visited: Set[str]) -> List[FeatureRequirement]:
            if name in visited:
                return []
            visited.add(name)
            result = list(defs[name].own_requirements)
            for parent in defs[name].parent_types:
                if parent in defs:
                    result.extend(_collect_requirements(parent, visited))
            return result

        for name, defn in defs.items():
            defn.all_requirements = _collect_requirements(name, set())
        
        # Log each type's extracted requirements at DEBUG level so mismatches
        # between the ontology structure and the parser are immediately visible.
        # Emit a WARNING for any type that ends up with no requirements, since
        # it will vacuously match every query.
        for name, defn in sorted(defs.items()):
            if defn.all_requirements:
                self.logger.debug(
                    f"Type '{name}' extracted {len(defn.all_requirements)} requirement(s): "
                    + "; ".join(
                        f"required={sorted(r.required)} alternatives={sorted(r.alternatives)}"
                        for r in defn.all_requirements
                    )
                )
            else:
                self.logger.warning(
                    f"Type '{name}' has NO extracted requirements (own or inherited). "
                    f"It will vacuously match every query — check the ontology restriction "
                    f"shape for this class."
                )

        # Pass 3: symmetric disjointness closure
        for subj_name, defn in defs.items():
            subj_uri = defn.uri
            for obj_uri in self.ontology.objects(subj_uri, OWL.disjointWith):
                if not isinstance(obj_uri, URIRef):
                    continue
                obj_name = self._uri_to_local(obj_uri)
                if obj_name and obj_name in defs:
                    defn.disjoint_with.add(obj_name)
                    defs[obj_name].disjoint_with.add(subj_name)  # symmetric

        self.logger.info(f"Loaded {len(defs)} question type definitions from ontology")
        return defs

    def _build_depth_cache(self) -> Dict[str, int]:
        """
        pre-compute depth (longest path to a root) for
        every type once, rather than recomputing it as a local dict inside
        classify_query on every call.
        """
        cache: Dict[str, int] = {}

        def _depth(n: str) -> int:
            if n in cache:
                return cache[n]
            parents = self.type_definitions[n].parent_types
            if not parents:
                cache[n] = 0
                return 0
            max_parent = max(
                (1 + _depth(p) for p in parents if p in self.type_definitions),
                default=0,
            )
            cache[n] = max_parent
            return max_parent

        for name in self.type_definitions:
            _depth(name)

        return cache

    # ------------------------------------------------------------------
    # Feature extraction from queries
    # ------------------------------------------------------------------

    @staticmethod
    def _is_user_bind(node: CompValue, parsed: object) -> bool:
        """
        Return True iff this Extend node corresponds to a user-written BIND.

        Strategy:
        - Projection aliases appear in parsed[1]['projection'] as entries with both:
            var (target) AND evar (expression)
        - If the Extend variable matches one of those aliases -> NOT a BIND
        - Otherwise -> treat as BIND
        """
        try:
            query_clause = parsed[1]
            projection = query_clause.get("projection", []) if hasattr(query_clause, "get") else []

            alias_vars = {
                str(item.get("var"))
                for item in projection
                if hasattr(item, "get")
                and item.get("var") is not None
                and item.get("evar") is not None
            }

            var = node.get("var")
            if var is None:
                return False

            return str(var) not in alias_vars

        except Exception as e:
            # Log the failure and conservatively treat this as NOT a user BIND.
            logging.getLogger(__name__).debug(f"_is_user_bind failed: {e}")
            return False

    @staticmethod
    def _detect_features_from_algebra(
        algebra_node: CompValue,
        parsed: object,
    ) -> Set[str]:
        """
        Recursively walk a rdflib SPARQL algebra tree and return the set of
        LSQ feature names that can be determined unambiguously from the
        algebra structure and parse tree.

                Node name → LSQ feature mapping:
                    ToMultiSet { p: Project { … } }  → SubQuery
                        rdflib wraps every inline SELECT in ToMultiSet(Project(…)).
                        A bare GROUP / BGP inside ToMultiSet is NOT a subquery.
                    Filter, discriminated by expr.name and HAVING context:
                        TrueFilter          → (skip) synthetic no-op rdflib inserts for
                                                                     OPTIONAL; not a user-written construct.
                        Builtin_NOTEXISTS   → NotExists + fn-notexists  (FILTER NOT EXISTS { … })
                        Builtin_EXISTS      → fn-exists  (FILTER EXISTS { … })
                        anything else       → Filter     (plain FILTER expression)

                        HAVING special case: rdflib compiles HAVING(expr) into a Filter
                        node wrapping the AggregateJoin/Group subtree. This is
                        indistinguishable from a plain Filter at the algebra level, so
                        HAVING is detected from the parse tree (parsed[1]['having'] is a
                        CompValue when HAVING is present, not the bare string sentinel
                        rdflib uses when it is absent). When HAVING is detected:
                            - lsqv:Having is added to the feature set.
                            - The first Filter node encountered in the algebra walk (which
                                is always the outermost one, i.e. the HAVING) is skipped so
                                it does not also produce a spurious lsqv:Filter.
          Extend                            → Bind
            rdflib compiles BIND(?expr AS ?var) into Extend nodes.
            it also compisles projection aliases such as COUNT(?x) AS ?count into Extend nodes. 
            The former should be mapped to Bind, the latter should not 
            (or should be mapped to a more specific feature such as AggregateAlias) 
            — mapping all Extend to Bind, produces false positives for aggregate aliases. 
            To fix this, we need a discriminator that can access the parse tree context 
            to distinguish user BIND clauses from compiler-generated projection aliases.
          LeftJoin                          → Optional
            rdflib compiles OPTIONAL { … } into LeftJoin nodes.

                Only CompValue nodes are visited; all other value types are ignored.
                A seen-set guards against cycles.

                rdflib uses the `Extend` algebra node both for explicit
                BIND(...) clauses and for compiled projection aliases such as
                `COUNT(...) AS ?alias`. To avoid false positives
                we should distinguish the two cases by consulting the parse tree
                (`parsed`) or the `Extend` node contents:

                    - If the `Extend` corresponds to a user `BIND` clause, emit
                        `Bind`.
                    - If the `Extend` is produced to represent a projection alias
                        (e.g. aggregate alias), do not emit `Bind` (or emit a more
                        specific feature such as `AggregateAlias`).

                # NOTE:
                # Extend → Bind classification is implemented via `_is_user_bind`,
                # which distinguishes user BIND expressions from projection aliases.
                #
                # This heuristic is stable but depends on SPARQL parser structure and
                # may require adjustment if rdflib algebra representation changes.
        """
        found: Set[str] = set()
        seen: Set[int] = set()

        def _expr_contains(node, target: str) -> bool:
            """
            Recursively search for a CompValue node with name == target.
            Handles nested CompValue, list, and tuple structures.
            """
            if isinstance(node, CompValue):
                if getattr(node, "name", None) == target:
                    return True
                for v in node.values():
                    if _expr_contains(v, target):
                        return True

            elif isinstance(node, (list, tuple)):
                for item in node:
                    if _expr_contains(item, target):
                        return True

            return False

        # Detect HAVING from the parse tree. parsed[1]['having'] is a CompValue
        # when HAVING is present; rdflib sets it to the bare string 'having' as
        # a sentinel when it is absent.
        # Note: parsed is a pyparsing.ParseResults (a list subclass). We must
        # access parsed[1] directly on the ParseResults object — not via a
        # list/tuple isinstance guard — because pyparsing overrides __getitem__
        # to return a CompValue proxy. Using list.__getitem__ (triggered by an
        # isinstance check) bypasses that proxy and returns a plain dict instead.
        try:
            query_clause = parsed[1]
            having_node = query_clause.get("having") if hasattr(query_clause, "get") else None
            has_having = isinstance(having_node, CompValue)
        except (IndexError, AttributeError):
            has_having = False
        if has_having:
            found.add("Having")

        # When HAVING is present, the outermost Filter in the algebra tree is
        # the compiled HAVING expression — it must be skipped so it does not
        # also emit a spurious lsqv:Filter. Detection is handled structurally
        # inside the Filter branch below; no traversal-order state is used.

        def _walk(node: CompValue) -> None:
            if not isinstance(node, CompValue):
                return
            node_id = id(node)
            if node_id in seen:
                return
            seen.add(node_id)

            name = node.name

            if name == "ToMultiSet":
                inner = node.get("p")
                if isinstance(inner, CompValue) and inner.name == "Project":
                    found.add("SubQuery")
            elif name == "Filter":
                expr = node.get("expr")

                # Identify HAVING filter structurally (Filter wrapping AggregateJoin/Group)
                child = node.get("p")
                is_having_filter = (
                    has_having
                    and isinstance(child, CompValue)
                    and child.name in {"AggregateJoin", "Group"}
                )

                if is_having_filter:
                    # Skip ONLY HAVING filter
                    pass

                elif expr is not None:
                    # rdflib represents NOT EXISTS / EXISTS as builtin expr
                    # nodes whose `name` is `Builtin_NOTEXISTS` / `Builtin_EXISTS`.
                    # Detect them by direct name equality rather than recursive
                    # traversal of the expression tree.
                    expr_name = getattr(expr, "name", None)

                    # rdflib canonical representation for FILTER NOT EXISTS
                    if expr_name == "Builtin_NOTEXISTS":
                        found.add("NotExists")

                    # rdflib canonical representation for FILTER EXISTS
                    elif expr_name == "Builtin_EXISTS":
                        found.add("fn-exists")

                    else:
                        # Skip synthetic filters inserted for OPTIONAL
                        if expr_name == "TrueFilter":
                            pass
                        else:
                            found.add("Filter")
            elif name == "Extend":
                # Log presence of Extend nodes so we can observe whether rdflib
                # actually emits them and debug alias vs BIND decisions.
                logging.getLogger(__name__).debug(f"Extend node found: {node}")

                # Discriminate Extend coming from user BIND vs projection alias
                is_bind = QuestionTypeClassifier._is_user_bind(node, parsed)

                if is_bind:
                    found.add("Bind")
                    logging.getLogger(__name__).debug("Extend → Bind")
                else:
                    logging.getLogger(__name__).debug("Extend → NOT Bind (alias)")
            elif name == "LeftJoin":
                found.add("Optional")

            for value in node.values():
                if isinstance(value, CompValue):
                    _walk(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, CompValue):
                            _walk(item)

        _walk(algebra_node)
        return found

    @staticmethod
    def _count_from_algebra(algebra_node: CompValue) -> Tuple[int, int, int]:
        """
        Walk the algebra tree and return (bgp_count, tp_count, proj_var_count).

        bgp_count      — number of distinct BGP nodes in the tree (including
                         those inside subqueries, matching LSQ's own counting).
        tp_count       — total number of triple patterns across all BGP nodes.
        proj_var_count — number of variables in the top-level SELECT projection
                         (the PV list on the root SelectQuery node), or 0 for
                         ASK / CONSTRUCT / DESCRIBE queries.

                         Note: rdflib populates PV on AskQuery with all pattern
                         variables, not projected ones. Returning 0 for non-
                         SELECT roots prevents a spurious mismatch against
                         lsqv:projectVarCount, and the check is silently skipped
                         in _check_count_annotations when the computed value is 0
                         and no declared value is present.

        Only CompValue nodes are visited; a seen-set guards against cycles.
        """
        bgp_count = 0
        tp_count = 0
        seen: Set[int] = set()

        def _walk(node: CompValue) -> None:
            nonlocal bgp_count, tp_count
            if not isinstance(node, CompValue):
                return
            node_id = id(node)
            if node_id in seen:
                return
            seen.add(node_id)

            if node.name == "BGP":
                bgp_count += 1
                triples = node.get("triples") or []
                tp_count += len(triples)

            for value in node.values():
                if isinstance(value, CompValue):
                    _walk(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, CompValue):
                            _walk(item)

        _walk(algebra_node)

        # Projection variable count is only meaningful for SELECT queries.
        # For ASK, CONSTRUCT and DESCRIBE, rdflib still populates PV on the
        # root node (with pattern variables for ASK, template variables for
        # CONSTRUCT) — none of which represent a SELECT projection. Return 0
        # for any non-SELECT root so _check_count_annotations skips the check.
        if algebra_node.name == "SelectQuery":
            pv = algebra_node.get("PV") or []
            proj_var_count = len(pv)
        else:
            proj_var_count = 0

        return bgp_count, tp_count, proj_var_count

    def _check_count_annotations(
        self,
        query_graph: Graph,
        query_uri: URIRef,
        algebra_node: CompValue,
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

        actual_bgp, actual_tp, actual_pv = self._count_from_algebra(algebra_node)
        actual: Dict[str, int] = {
            "projectVarCount": actual_pv,
            "bgpCount":        actual_bgp,
            "tpCount":         actual_tp,
        }

        # projectVarCount is only meaningful for SELECT queries. For ASK /
        # CONSTRUCT / DESCRIBE, _count_from_algebra returns 0 to avoid the
        # misleading PV rdflib populates on non-SELECT roots. Skip the check
        # entirely for those query types so a declared value never triggers
        # a false mismatch.
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

        Renamed from _validate_sparql to _check_sparql_syntax to clarify
        that this is a diagnostic check, not a gate: feature extraction
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
        tree via _detect_features_from_algebra. Structural features found in
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
                        item.get("var") is not None and item.get("evar") is None
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
            # We compare what the algebra tree actually contains against what
            # LSQ declared and enrich the declared set with algebra-derived
            # features when they add information. Algebra features are
            # additive and complementary — structural gaps are filled and
            # functional variants (e.g. fn-exists, fn-notexists) are retained
            # alongside structural variants when both are valid.
            #
            # NOTE:
            # Extend → Bind classification is implemented via `_is_user_bind`,
            # which distinguishes user BIND expressions from projection aliases.
            #
            # This heuristic is stable but depends on SPARQL parser structure and
            # may require adjustment if rdflib algebra representation changes.
            implied = self._detect_features_from_algebra(algebra.algebra, parsed)

            # enrich LSQ-declared features with algebra-derived features.
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

                # make algebra features authoritative
                features |= implied

            # Numeric count annotation checks (projectVarCount, bgpCount, tpCount).
            # These are verified exactly against the algebra — any mismatch is a
            # definite LSQ annotation error rather than a heuristic gap.
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
        return the strict ancestor set of a type — i.e. all
        types reachable via parent_types — NOT including the type itself.
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

        # Step 2: remove any candidate that is a strict ancestor of another
        # candidate. Uses the fixed _ancestors() which no longer includes self.
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

        # Step 4: prefer the deepest (most specific) type in the subclass tree
        # using the pre-built depth cache (FIX issue 3).
        max_depth = max(self._depth_cache.get(n, 0) for n in resolved)
        most_specific = {n for n in resolved if self._depth_cache.get(n, 0) == max_depth}

        return most_specific

    # ------------------------------------------------------------------
    # File-level classification
    # ------------------------------------------------------------------

    def classify_queries_from_file(
        self, query_file: Path
    ) -> Dict[str, Tuple[Set[str], Set[str], Optional[str], List[str]]]:
        """
        Classify all lsqv:Query resources in a Turtle file.

        Returns:
            Dict mapping query URI string →
                (matched_types, features, rdfs:label or None, warnings)
            where warnings is the list of LSQ annotation-gap messages
            collected by extract_features for that query.
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

        results: Dict[str, Tuple[Set[str], Set[str], Optional[str], List[str]]] = {}
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

            # Compute algebra-derived counts (bgpCount, tpCount, projectVarCount)
            counts: Dict[str, int] = {}
            for sparql_lit in g.objects(uri, LSQV.text):
                text = str(sparql_lit)
                try:
                    parsed = _sparql_parser.parseQuery(text)
                    algebra = _sparql_algebra.translateQuery(parsed)
                    bgp_c, tp_c, pv_c = self._count_from_algebra(algebra.algebra)
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

