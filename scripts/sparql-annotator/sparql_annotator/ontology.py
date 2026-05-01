"""Ontology parsing: builds QuestionTypeDefinition map and depth cache."""

import logging
from typing import Dict, List, Optional, Set

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDFS

from .model import FeatureRequirement, QuestionTypeDefinition
from .namespaces import LSQV, QAT


def _uri_to_local(uri: URIRef) -> Optional[str]:
    s = str(uri)
    return s.split("#")[-1] if "#" in s else None


def _all_question_type_uris(ontology: Graph) -> Set[URIRef]:
    root = QAT.QuestionType
    visited: Set[URIRef] = set()

    def _visit(cls: URIRef) -> None:
        for sub in ontology.subjects(RDFS.subClassOf, cls):
            if isinstance(sub, URIRef) and sub not in visited:
                visited.add(sub)
                _visit(sub)

    _visit(root)
    return visited


def _parse_feature_restriction(
    ontology: Graph, restriction
) -> Optional[FeatureRequirement]:
    on_prop = list(ontology.objects(restriction, OWL.onProperty))
    if not on_prop or on_prop[0] != LSQV.hasStructuralFeatures:
        return None

    req = FeatureRequirement()
    for svf in ontology.objects(restriction, OWL.someValuesFrom):
        nested_prop = list(ontology.objects(svf, OWL.onProperty))
        if not nested_prop or nested_prop[0] != LSQV.usesFeature:
            continue
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


def _own_requirements_of(
    ontology: Graph, qtype_uri: URIRef
) -> List[FeatureRequirement]:
    requirements: List[FeatureRequirement] = []
    for restriction in ontology.objects(qtype_uri, RDFS.subClassOf):
        if isinstance(restriction, URIRef):
            continue
        req = _parse_feature_restriction(ontology, restriction)
        if req is not None:
            requirements.append(req)
    return requirements


def _direct_parent_type_names(
    ontology: Graph, qtype_uri: URIRef, known_uris: Set[URIRef]
) -> Set[str]:
    parents: Set[str] = set()
    for parent in ontology.objects(qtype_uri, RDFS.subClassOf):
        if isinstance(parent, URIRef) and parent in known_uris:
            name = _uri_to_local(parent)
            if name:
                parents.add(name)
    return parents


def build_type_definitions(
    ontology: Graph, logger: logging.Logger
) -> Dict[str, QuestionTypeDefinition]:
    """Parse the ontology and return the full type definition map."""
    all_uris = _all_question_type_uris(ontology)

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
            all_requirements=list(own),
            parent_types=parents,
        )

    def _collect(name: str, visited: Set[str]) -> List[FeatureRequirement]:
        if name in visited:
            return []
        visited.add(name)
        result = list(defs[name].own_requirements)
        for parent in defs[name].parent_types:
            if parent in defs:
                result.extend(_collect(parent, visited))
        return result

    for name, defn in defs.items():
        defn.all_requirements = _collect(name, set())

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

    for subj_name, defn in defs.items():
        for obj_uri in ontology.objects(defn.uri, OWL.disjointWith):
            if not isinstance(obj_uri, URIRef):
                continue
            obj_name = _uri_to_local(obj_uri)
            if obj_name and obj_name in defs:
                defn.disjoint_with.add(obj_name)
                defs[obj_name].disjoint_with.add(subj_name)

    logger.info(f"Loaded {len(defs)} question type definitions from ontology")
    return defs


def build_depth_cache(
    type_definitions: Dict[str, QuestionTypeDefinition],
) -> Dict[str, int]:
    """Pre-compute longest-path depth for every type."""
    cache: Dict[str, int] = {}

    def _depth(n: str) -> int:
        if n in cache:
            return cache[n]
        parents = type_definitions[n].parent_types
        if not parents:
            cache[n] = 0
            return 0
        d = max((1 + _depth(p) for p in parents if p in type_definitions), default=0)
        cache[n] = d
        return d

    for name in type_definitions:
        _depth(name)
    return cache
