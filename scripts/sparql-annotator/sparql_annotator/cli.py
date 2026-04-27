import click
from pathlib import Path
from .annotator import Annotator
from .adapters import CSVAdapter, JSONAdapter, TTLAdapter


def _detect_format(path: str):
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".ttl", ".turtle"):
        return "ttl"
    if suffix == ".csv":
        return "csv"
    if suffix == ".json":
        return "json"
    return None


@click.group()
def cli():
    pass


@cli.command()
@click.option("--input", "input_path", required=True, help="Input file path")
@click.option("--format", "fmt", default=None, help="Input format: ttl/csv/json")
@click.option("--output", "output_path", default=None, help="Output file path")
def annotate(input_path, fmt, output_path):
    """Annotate queries in a file."""
    if fmt is None:
        fmt = _detect_format(input_path)
        if fmt is None:
            raise click.ClickException("Could not detect input format; please provide --format")

    if fmt == "csv":
        adapter = CSVAdapter()
    elif fmt == "json":
        adapter = JSONAdapter()
    elif fmt == "ttl":
        adapter = TTLAdapter()
    else:
        raise click.ClickException(f"Unsupported format: {fmt}")

    annotator = Annotator()
    annotations = annotator.annotate_file(input_path, input_adapter=adapter)

    if output_path:
        adapter.write(annotations, output_path)
    else:
        # print summary
        for ann in annotations:
            label = ann.record.label or ann.record.uri or "<unnamed>"
            status = "OK" if ann.is_valid else f"ERR: {ann.parse_error}"
            ops = ",".join(sorted(ann.operators.raw))
            click.echo(f"{label}\t{status}\t{ops}")


if __name__ == "__main__":
    cli()
