from typing import Iterable, List, Optional

from .model import QueryRecord, Annotation
from .parser import parse_query
from .operators import extract_operators


class Annotator:
    def annotate(self, records: Iterable[QueryRecord]) -> List[Annotation]:
        out = []
        for rec in records:
            is_valid, parsed, err = parse_query(rec.text)
            if not is_valid:
                ann = Annotation(record=rec, operators=extract_operators(rec.text, None), is_valid=False, parse_error=err)
            else:
                ops = extract_operators(rec.text, parsed)
                ann = Annotation(record=rec, operators=ops, is_valid=True)
            out.append(ann)
        return out

    def annotate_file(self, source, input_adapter=None):
        if input_adapter is None:
            raise ValueError("input_adapter required for file annotation")
        records = list(input_adapter.read(source))
        return self.annotate(records)
