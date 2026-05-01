from typing import Iterable, List

from .model import QueryRecord, Annotation
from .algebra import parse_query, extract_operators


class Annotator:
    def annotate(self, records: Iterable[QueryRecord]) -> List[Annotation]:
        out = []
        for rec in records:
            is_valid, parsed, err = parse_query(rec.text)
            if not is_valid:
                ann = Annotation(
                    record=rec,
                    operators=extract_operators(rec.text, None),
                    is_valid=False,
                    parse_error=err,
                )
            else:
                ann = Annotation(
                    record=rec, operators=extract_operators(rec.text, parsed)
                )
            out.append(ann)
        return out

    def annotate_file(self, source, input_adapter=None) -> List[Annotation]:
        if input_adapter is None:
            raise ValueError("input_adapter required for file annotation")
        return self.annotate(list(input_adapter.read(source)))
