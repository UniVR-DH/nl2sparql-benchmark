"""sparql_annotator package entry points."""
from .annotator import Annotator
from .model import QueryRecord, Annotation, OperatorSet

__all__ = ["Annotator", "QueryRecord", "Annotation", "OperatorSet"]
