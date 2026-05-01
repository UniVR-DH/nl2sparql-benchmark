from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from rdflib import URIRef


# ---------------------------------------------------------------------------
# Annotation model
# ---------------------------------------------------------------------------


@dataclass
class QueryRecord:
    uri: Optional[str]
    label: Optional[str]
    text: str
    metadata: Dict = field(default_factory=dict)


@dataclass
class OperatorSet:
    query_form: str = "UNKNOWN"
    projection_modifiers: Set[str] = field(default_factory=set)
    graph_patterns: Set[str] = field(default_factory=set)
    filters: Set[str] = field(default_factory=set)
    filter_functions: Set[str] = field(
        default_factory=set
    )  # REGEX, LANG, DATATYPE, etc.
    aggregates: Set[str] = field(default_factory=set)
    solution_modifiers: Set[str] = field(default_factory=set)
    assignments: Set[str] = field(default_factory=set)
    subqueries: bool = False
    property_paths: bool = False
    raw: Set[str] = field(default_factory=set)
    # Structural metrics (populated by algebra.extract_operators)
    bgp_count: int = 0
    tp_count: int = 0
    project_var_count: int = 0


@dataclass
class Annotation:
    record: QueryRecord
    operators: OperatorSet
    is_valid: bool = True
    parse_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Classifier model
# ---------------------------------------------------------------------------


@dataclass
class FeatureRequirement:
    """One structural-feature restriction block from the ontology.

    required     — features that MUST all be present (owl:hasValue)
    alternatives — at least one of these must be present (owl:someValuesFrom union)
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
    own_requirements: List[FeatureRequirement] = field(default_factory=list)
    all_requirements: List[FeatureRequirement] = field(default_factory=list)
    parent_types: Set[str] = field(default_factory=set)
    disjoint_with: Set[str] = field(default_factory=set)

    @property
    def required_features(self) -> Set[str]:
        result: Set[str] = set()
        for req in self.all_requirements:
            result |= req.required
        return result

    def matches(self, features: Set[str]) -> bool:
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
