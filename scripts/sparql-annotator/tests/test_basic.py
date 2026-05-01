"""Tests for Annotator, CSVAdapter, JSONAdapter round-trips."""

import csv
import io
import json

from sparql_annotator import Annotator, QueryRecord
from sparql_annotator.adapters.csv import CSVAdapter
from sparql_annotator.adapters.json import JSONAdapter


# ---------------------------------------------------------------------------
# Annotator
# ---------------------------------------------------------------------------


def test_annotate_valid_query():
    records = [
        QueryRecord(
            uri=None, label="q1", text="SELECT ?s WHERE { ?s ?p ?o }", metadata={}
        )
    ]
    anns = Annotator().annotate(records)
    assert len(anns) == 1
    assert anns[0].is_valid
    assert anns[0].operators.query_form == "SELECT"


def test_annotate_invalid_query():
    records = [QueryRecord(uri=None, label="bad", text="NOT SPARQL", metadata={})]
    anns = Annotator().annotate(records)
    assert len(anns) == 1
    assert not anns[0].is_valid
    assert anns[0].parse_error is not None


def test_annotate_mixed():
    records = [
        QueryRecord(
            uri=None, label="ok", text="SELECT ?s WHERE { ?s ?p ?o }", metadata={}
        ),
        QueryRecord(uri=None, label="bad", text="NOT SPARQL", metadata={}),
    ]
    anns = Annotator().annotate(records)
    assert anns[0].is_valid
    assert not anns[1].is_valid


def test_annotate_file_requires_adapter():
    import pytest

    with pytest.raises(ValueError):
        Annotator().annotate_file("anything.ttl", input_adapter=None)


# ---------------------------------------------------------------------------
# CSVAdapter — read
# ---------------------------------------------------------------------------


def test_csv_read(tmp_path):
    src = tmp_path / "in.csv"
    src.write_text("label,query\nq1,SELECT * WHERE { ?s ?p ?o }\n", encoding="utf-8")
    records = list(CSVAdapter().read(str(src)))
    assert len(records) == 1
    assert records[0].label == "q1"
    assert "SELECT" in records[0].text


def test_csv_read_skips_empty_query(tmp_path):
    src = tmp_path / "in.csv"
    src.write_text(
        "label,query\nq1,\nq2,SELECT ?s WHERE { ?s ?p ?o }\n", encoding="utf-8"
    )
    records = list(CSVAdapter().read(str(src)))
    assert len(records) == 1


def test_csv_read_stream():
    data = "label,query\nq1,SELECT ?s WHERE { ?s ?p ?o }\n"
    records = list(CSVAdapter().read(io.StringIO(data)))
    assert len(records) == 1


# ---------------------------------------------------------------------------
# CSVAdapter — write round-trip
# ---------------------------------------------------------------------------


def test_csv_write_round_trip(tmp_path):
    src = tmp_path / "in.csv"
    out = tmp_path / "out.csv"
    src.write_text(
        "label,query\nq1,SELECT DISTINCT ?s WHERE { ?s ?p ?o }\n", encoding="utf-8"
    )

    adapter = CSVAdapter()
    anns = Annotator().annotate_file(str(src), input_adapter=adapter)
    adapter.write(anns, str(out))

    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["query_form"] == "SELECT"
    assert "DISTINCT" in rows[0]["projection_modifiers"]


# ---------------------------------------------------------------------------
# JSONAdapter — read
# ---------------------------------------------------------------------------


def test_json_read_array(tmp_path):
    src = tmp_path / "in.json"
    src.write_text(
        json.dumps([{"label": "q1", "query": "SELECT ?s WHERE { ?s ?p ?o }"}]),
        encoding="utf-8",
    )
    records = list(JSONAdapter().read(str(src)))
    assert len(records) == 1
    assert records[0].label == "q1"


def test_json_read_object_of_objects(tmp_path):
    src = tmp_path / "in.json"
    src.write_text(
        json.dumps({"q1": {"query": "SELECT ?s WHERE { ?s ?p ?o }"}}), encoding="utf-8"
    )
    records = list(JSONAdapter().read(str(src)))
    assert len(records) == 1
    assert records[0].uri == "q1"


def test_json_read_skips_missing_query(tmp_path):
    src = tmp_path / "in.json"
    src.write_text(
        json.dumps(
            [{"label": "no-query"}, {"label": "q1", "query": "ASK { ?s ?p ?o }"}]
        ),
        encoding="utf-8",
    )
    records = list(JSONAdapter().read(str(src)))
    assert len(records) == 1


# ---------------------------------------------------------------------------
# JSONAdapter — write round-trip
# ---------------------------------------------------------------------------


def test_json_write_round_trip(tmp_path):
    src = tmp_path / "in.json"
    out = tmp_path / "out.json"
    src.write_text(
        json.dumps([{"label": "q1", "query": "SELECT ?s WHERE { ?s ?p ?o }"}]),
        encoding="utf-8",
    )

    adapter = JSONAdapter()
    anns = Annotator().annotate_file(str(src), input_adapter=adapter)
    adapter.write(anns, str(out))

    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert "annotations" in data[0]
    assert data[0]["annotations"]["query_form"] == "SELECT"
