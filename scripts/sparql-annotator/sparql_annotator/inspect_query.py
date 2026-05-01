"""
Inspect a single query from a TTL file: print its text, parse tree, algebra tree,
and declared LSQ features. Optionally render the algebra as a Graphviz diagram.

Usage:
    sparql-annotator inspect --query-file <file.ttl> --query-id <id>
    sparql-annotator inspect --query-file <file.ttl> --query-id 30 --viz .temp/q30.svg
    sparql-annotator inspect --query-file <file.ttl> --query-id 30 --viz .temp/q30.png
    sparql-annotator inspect --query-file <file.ttl> --query-id 30 --viz .temp/q30.dot
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import click
from rdflib import Graph, URIRef
from rdflib.namespace import RDFS, RDF
from rdflib.plugins.sparql.parserutils import CompValue
import rdflib.plugins.sparql.parser as _parser
import rdflib.plugins.sparql.algebra as _algebra

from .namespaces import LSQV


def _find_uri(g: Graph, query_id: str) -> Optional[URIRef]:
    for uri in g.subjects(RDF.type, LSQV.Query):
        s = str(uri)
        if s == query_id or s.split("/")[-1] == query_id or query_id in s:
            return uri
    return None


def _print_tree(node, indent: int = 0, label: str = "") -> None:
    prefix = "  " * indent
    if not isinstance(node, CompValue):
        if label:
            click.echo(f"{prefix}{label}: {repr(node)[:120]}")
        return
    header = f"{label}: " if label else ""
    click.echo(f"{prefix}{header}[{node.name}]")
    for k, v in node.items():
        if k.startswith("_"):
            continue
        if isinstance(v, CompValue):
            _print_tree(v, indent + 1, k)
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, CompValue):
                    _print_tree(item, indent + 1, f"{k}[{i}]")
                else:
                    click.echo(f"{'  ' * (indent + 1)}{k}[{i}]: {repr(item)[:80]}")
        else:
            val = repr(v)
            if len(val) > 100:
                val = val[:97] + "..."
            click.echo(f"{'  ' * (indent + 1)}.{k} = {val}")


def inspect_query(
    query_file: Path,
    query_id: str,
    viz_path: Optional[Path] = None,
    check: bool = False,
) -> None:
    g = Graph()
    g.parse(str(query_file), format="turtle")

    uri = _find_uri(g, query_id)
    if uri is None:
        raise click.ClickException(f"No query matching {query_id!r} in {query_file}")

    label = g.value(uri, RDFS.label)
    text_lit = g.value(uri, LSQV.text)

    click.echo(f"URI:   {uri}")
    click.echo(f"Label: {label}")
    click.echo()

    if text_lit is None:
        raise click.ClickException("No lsqv:text found for this query.")

    text = str(text_lit)
    W = 72
    click.echo("=" * W)
    click.echo("SPARQL TEXT")
    click.echo("=" * W)
    click.echo(text)

    click.echo("=" * W)
    click.echo("PARSE TREE (syntax)")
    click.echo("=" * W)
    try:
        parsed = _parser.parseQuery(text)
        _print_tree(parsed[1], label="Query")
    except Exception as exc:
        raise click.ClickException(f"Parse error: {exc}")

    click.echo("=" * W)
    click.echo("ALGEBRA TREE")
    click.echo("=" * W)
    try:
        # Re-parse: translateQuery mutates the parse tree, so we need a fresh copy
        parsed_for_alg = _parser.parseQuery(text)
        alg = _algebra.translateQuery(parsed_for_alg)
        _print_tree(alg.algebra)
    except Exception as exc:
        raise click.ClickException(f"Algebra error: {exc}")

    click.echo("=" * W)
    click.echo("DECLARED LSQ FEATURES")
    click.echo("=" * W)
    for sf in g.objects(uri, LSQV.hasStructuralFeatures):
        for feat in g.objects(sf, LSQV.usesFeature):
            click.echo(f"  {str(feat).split('#')[-1]}")

    if check:
        from .antipatterns import detect_antipatterns

        issues = detect_antipatterns(text, parsed)
        click.echo("=" * W)
        click.echo("ANTIPATTERN CHECK")
        click.echo("=" * W)
        if issues:
            for issue in issues:
                click.echo(f"  [{issue.code}] {issue.message}")
                click.echo(f"         → {issue.hint}")
        else:
            click.echo("  No antipatterns detected.")

    if viz_path is not None:
        fmt = viz_path.suffix.lstrip(".").lower() or "svg"
        if fmt not in {"dot", "svg", "png"}:
            raise click.ClickException(
                f"Unsupported viz format {fmt!r}. Use .dot, .svg, or .png"
            )
        try:
            from .algebra_viz import save_algebra_viz

            out = save_algebra_viz(alg.algebra, viz_path, fmt=fmt, sparql_text=text)
            click.echo(f"\nAlgebra diagram saved → {out}")
        except ImportError as exc:
            raise click.ClickException(str(exc))


@click.command("inspect")
@click.option(
    "--query-file",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="LSQ query file (Turtle).",
)
@click.option("--query-id", required=True, help="Query URI or local ID (e.g. '30').")
@click.option(
    "--viz",
    "viz_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Save algebra diagram to this path (.dot / .svg / .png).",
)
@click.option(
    "--check", is_flag=True, default=False, help="Run antipattern checks on the query."
)
def inspect_cmd(
    query_file: Path, query_id: str, viz_path: Optional[Path], check: bool
) -> None:
    """Print SPARQL text, parse tree, algebra tree, and LSQ features for one query."""
    inspect_query(query_file, query_id, viz_path, check)
