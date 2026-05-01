import json
from pathlib import Path
from typing import Iterable, Union
import io

from ..model import QueryRecord, Annotation
from .base import InputAdapter, OutputAdapter


class JSONAdapter(InputAdapter, OutputAdapter):
    def __init__(self, query_key: str = "query"):
        self.query_key = query_key

    def read(self, source: Union[Path, io.IOBase, str]):
        if isinstance(source, (str, Path)):
            text = Path(source).read_text(encoding="utf-8")
        else:
            text = source.read()

        data = json.loads(text)
        if isinstance(data, list):
            for i, obj in enumerate(data):
                q = obj.get(self.query_key) or obj.get("queryText")
                if not q:
                    continue
                yield QueryRecord(
                    uri=obj.get("uri"),
                    label=obj.get("label") or f"obj-{i + 1}",
                    text=q,
                    metadata=obj,
                )
        elif isinstance(data, dict):
            # object-of-objects
            for key, obj in data.items():
                if isinstance(obj, dict):
                    q = (
                        obj.get(self.query_key)
                        or obj.get("queryText")
                        or obj.get("query")
                    )
                    if not q:
                        continue
                    yield QueryRecord(
                        uri=key, label=obj.get("label") or key, text=q, metadata=obj
                    )

    def write(
        self,
        annotations: Iterable[Annotation],
        destination: Union[Path, io.IOBase, str],
    ):
        out = []
        for ann in annotations:
            item = dict(ann.record.metadata or {})
            item.setdefault("query", ann.record.text)
            item.setdefault("label", ann.record.label)
            op = ann.operators
            item["annotations"] = {
                "query_form": op.query_form,
                "projection_modifiers": sorted(list(op.projection_modifiers)),
                "graph_patterns": sorted(list(op.graph_patterns)),
                "filters": sorted(list(op.filters)),
                "aggregates": sorted(list(op.aggregates)),
                "solution_modifiers": sorted(list(op.solution_modifiers)),
                "assignments": sorted(list(op.assignments)),
                "subqueries": op.subqueries,
                "property_paths": op.property_paths,
                "operators": sorted(list(op.raw)),
            }
            out.append(item)

        text = json.dumps(out, indent=2, ensure_ascii=False)
        if isinstance(destination, (str, Path)):
            Path(destination).write_text(text, encoding="utf-8")
        else:
            destination.write(text)
