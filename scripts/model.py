"""
Ontology data model and parsing logic for the NL2SPARQL question-type classifier.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDFS

from rdflib import Namespace

LSQV = Namespace("http://lsq.aksw.org/vocab#")
QAT = Namespace("https://w3id.org/univr-qa/qatypes#")

__all__ = [
    "FeatureRequirement",
    "QuestionTypeDefinition",
    "build_type_definitions",
    "build_depth_cache",
]


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
# Ontology parsing (private)
# ---------------------------------------------------------------------------

def _uri_to_local(uri: URIRef) -> Optional[str]:
    """Return the local name (fragment) of a URI, or None."""
    s = str(uri)
    return s.split("#")[-1] if "#" in s else None


def _all_question_type_uris(ontology: Graph) -> Set[URIRef]:
    """
    Return all URIs that are (directly or transitively) subclasses of
    qat:QuestionType, excluding qat:QuestionType itself.
    """
    root = QAT.QuestionType
    visited: Set[URIRef] = set()

    def _visit(cls: URIRef) -> None:
        for sub in ontology.subjects(RDFS.subClassOf, cls):
            if isinstance(sub, URIRef) and sub not in visited:
                visited.add(sub)
                _visit(sub)

    _visit(root)
    return visited


def _parse_feature_restriction(ontology: Graph, restriction) -> Optional[FeatureRequirement]:
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
    on_prop = list(ontology.objects(restriction, OWL.onProperty))
    if not on_prop or on_prop[0] != LSQV.hasStructuralFeatures:
        return None

    req = FeatureRequirement()

    for svf in ontology.objects(restriction, OWL.someValuesFrom):
        nested_prop = list(ontology.objects(svf, OWL.onProperty))
        if not nested_prop or nested_prop[0] != LSQV.usesFeature:
            continue

        # Case 1: owl:hasValue lsqv:X  → mandatory feature
        for val in ontology.objects(svf, OWL.hasValue):
            name = _uri_to_local(val)
            if name:
                req.required.add(name)

        for union_node in ontology.objects(svf, OWL.someValuesFrom):
            for union_list in ontology.objects(union_node, OWL.unionOf):
                for member in ontology.items(union_list):
                    name = _uri_to_local(member)
                    if name:
                        req.alternatives.add(name)

    if not req.required and not req.alternatives:
        return None
    return req


def _own_requirements_of(ontology: Graph, qtype_uri: URIRef) -> List[FeatureRequirement]:
    """
    Extract all FeatureRequirement objects declared directly on qtype_uri.
    Each owl:someValuesFrom restriction block on lsqv:hasStructuralFeatures
    becomes one FeatureRequirement.
    """
    requirements: List[FeatureRequirement] = []
    for restriction in ontology.objects(qtype_uri, RDFS.subClassOf):
        if isinstance(restriction, URIRef):
            continue  # named class, not a restriction bnode
        req = _parse_feature_restriction(ontology, restriction)
        if req is not None:
            requirements.append(req)
    return requirements


def _direct_parent_type_names(ontology: Graph, qtype_uri: URIRef, known_uris: Set[URIRef]) -> Set[str]:
    """Return names of direct question-type parents (not qat:QuestionType itself)."""
    parents: Set[str] = set()
    for parent in ontology.objects(qtype_uri, RDFS.subClassOf):
        if isinstance(parent, URIRef) and parent in known_uris:
            name = _uri_to_local(parent)
            if name:
                parents.add(name)
    return parents


def _build_type_definitions(ontology: Graph, logger: logging.Logger) -> Dict[str, QuestionTypeDefinition]:
    """
    Build the full type definition map:
    1. Collect all question-type URIs (transitive subclasses of qat:QuestionType)
    2. For each, extract own requirements and direct parents
    3. Propagate inherited requirements (transitive closure over parent_types)
    4. Build symmetric disjointness closure

    After building all definitions, verify that every type
    (including abstract ones like Factoid) has at least one extracted
    requirement. A type with no requirements would match every query,
    causing spurious classifications. A warning is emitted and the type
    is logged clearly so the ontology parser can be debugged.
    """
    all_uris = _all_question_type_uris(ontology)

    # Pass 1: create skeleton definitions
    defs: Dict[str, QuestionTypeDefinition] = {}
    for uri in all_uris:
        name = _uri_to_local(uri)
        if not name:
            continue
        own = _own_requirements_of(ontology, uri)
        parents = _direct_parent_type_names(ontology, uri, all_uris)
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
            logger.debug(
                f"Type '{name}' extracted {len(defn.all_requirements)} requirement(s): "
                + "; ".join(
                    f"required={sorted(r.required)} alternatives={sorted(r.alternatives)}"
                    for r in defn.all_requirements
                )
            )
        else:
            logger.warning(
                f"Type '{name}' has NO extracted requirements (own or inherited). "
                f"It will vacuously match every query — check the ontology restriction "
                f"shape for this class."
            )

    # Pass 3: symmetric disjointness closure
    for subj_name, defn in defs.items():
        subj_uri = defn.uri
        for obj_uri in ontology.objects(subj_uri, OWL.disjointWith):
            if not isinstance(obj_uri, URIRef):
                continue
            obj_name = _uri_to_local(obj_uri)
            if obj_name and obj_name in defs:
                defn.disjoint_with.add(obj_name)
                defs[obj_name].disjoint_with.add(subj_name)  # symmetric

    logger.info(f"Loaded {len(defs)} question type definitions from ontology")
    return defs


def _build_depth_cache(type_definitions: Dict[str, QuestionTypeDefinition]) -> Dict[str, int]:
    """
    Pre-compute depth (longest path to a root) for every type once, rather
    than recomputing it as a local dict inside classify_query on every call.

    Note: assumes the parent_types graph is acyclic (i.e. a valid subclass
    DAG). A cycle would cause infinite recursion here. See module-level
    limitation note 1.
    """
    cache: Dict[str, int] = {}

    def _depth(n: str) -> int:
        if n in cache:
            return cache[n]
        parents = type_definitions[n].parent_types
        if not parents:
            cache[n] = 0
            return 0
        max_parent = max(
            (1 + _depth(p) for p in parents if p in type_definitions),
            default=0,
        )
        cache[n] = max_parent
        return max_parent

    for name in type_definitions:
        _depth(name)

    return cache


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_type_definitions(ontology: Graph, logger: logging.Logger) -> Dict[str, QuestionTypeDefinition]:
    return _build_type_definitions(ontology, logger)


def build_depth_cache(type_definitions: Dict[str, QuestionTypeDefinition]) -> Dict[str, int]:
    return _build_depth_cache(type_definitions)
