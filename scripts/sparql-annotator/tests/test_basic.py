import io
from sparql_annotator import Annotator, QueryRecord
from sparql_annotator.adapters.csv import CSVAdapter


def test_basic_csv_read_write(tmp_path):
    csv_data = "label,query\nq1,SELECT * WHERE { ?s ?p ?o }\n"
    src = tmp_path / "in.csv"
    src.write_text(csv_data, encoding="utf-8")

    adapter = CSVAdapter()
    ann = Annotator().annotate_file(str(src), input_adapter=adapter)
    assert len(ann) == 1
    assert ann[0].is_valid
