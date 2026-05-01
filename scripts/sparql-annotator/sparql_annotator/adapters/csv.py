import csv
from pathlib import Path
from typing import Iterable, Union
import io

from ..model import QueryRecord, Annotation
from .base import InputAdapter, OutputAdapter


class CSVAdapter(InputAdapter, OutputAdapter):
    def __init__(self, query_column: str = "query"):
        self.query_column = query_column

    def read(self, source: Union[Path, io.IOBase, str]):
        if isinstance(source, (str, Path)):
            fh = open(source, newline="", encoding="utf-8")
            close = True
        else:
            fh = source
            close = False

        reader = csv.DictReader(fh)
        for i, row in enumerate(reader):
            text = row.get(self.query_column) or row.get(self.query_column.lower())
            if not text:
                continue
            metadata = {k: v for k, v in row.items() if k != self.query_column}
            yield QueryRecord(
                uri=None,
                label=row.get("label") or f"row-{i + 1}",
                text=text,
                metadata=metadata,
            )

        if close:
            fh.close()

    def write(
        self,
        annotations: Iterable[Annotation],
        destination: Union[Path, io.IOBase, str],
    ):
        rows = []
        fieldnames = set()
        for ann in annotations:
            base = dict(ann.record.metadata or {})
            base["query"] = ann.record.text
            base["label"] = ann.record.label
            # flatten operator sets into comma-separated fields (one column per OperatorSet field)
            op = ann.operators
            opmap = {
                "query_form": op.query_form,
                "projection_modifiers": ",".join(sorted(op.projection_modifiers)),
                "graph_patterns": ",".join(sorted(op.graph_patterns)),
                "filters": ",".join(sorted(op.filters)),
                "aggregates": ",".join(sorted(op.aggregates)),
                "solution_modifiers": ",".join(sorted(op.solution_modifiers)),
                "assignments": ",".join(sorted(op.assignments)),
                "subqueries": str(op.subqueries),
                "property_paths": str(op.property_paths),
                "operators": ",".join(sorted(op.raw)),
            }
            base.update(opmap)
            rows.append(base)
            fieldnames.update(base.keys())

        fieldnames = list(sorted(fieldnames))

        if isinstance(destination, (str, Path)):
            fh = open(destination, "w", newline="", encoding="utf-8")
            close = True
        else:
            fh = destination
            close = False

        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

        if close:
            fh.close()
