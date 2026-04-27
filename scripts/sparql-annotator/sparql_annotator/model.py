from dataclasses import dataclass, field
from typing import Optional, Set, Dict


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
    aggregates: Set[str] = field(default_factory=set)
    solution_modifiers: Set[str] = field(default_factory=set)
    assignments: Set[str] = field(default_factory=set)
    subqueries: bool = False
    property_paths: bool = False
    raw: Set[str] = field(default_factory=set)


@dataclass
class Annotation:
    record: QueryRecord
    operators: OperatorSet
    is_valid: bool = True
    parse_error: Optional[str] = None
