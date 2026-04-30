import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import click
from rdflib import Graph, URIRef, Literal, BNode
from rdflib.namespace import RDF, OWL

from .annotator import Annotator
from .adapters import CSVAdapter, JSONAdapter, TTLAdapter
from .classifier import QuestionTypeClassifier
from .namespaces import LSQV, QAT, QA
from .inspect_query import inspect_cmd


def _detect_format(path: str):
    suffix = Path(path).suffix.lower()
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


cli.add_command(inspect_cmd)


# ---------------------------------------------------------------------------
# annotate sub-command
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--input", "input_path", required=True, help="Input file path")
@click.option("--format", "fmt", default=None, help="Input format: ttl/csv/json")
@click.option("--output", "output_path", default=None, help="Output file path")
def annotate(input_path, fmt, output_path):
    """Annotate queries in a file."""
    if fmt is None:
        fmt = _detect_format(input_path)
        if fmt is None:
            raise click.ClickException(
                "Could not detect input format; please provide --format"
            )

    if fmt == "csv":
        adapter = CSVAdapter()
    elif fmt == "json":
        adapter = JSONAdapter()
    elif fmt == "ttl":
        adapter = TTLAdapter()
    else:
        raise click.ClickException(f"Unsupported format: {fmt}")

    annotations = Annotator().annotate_file(input_path, input_adapter=adapter)

    if output_path:
        adapter.write(annotations, output_path)
    else:
        for ann in annotations:
            label = ann.record.label or ann.record.uri or "<unnamed>"
            status = "OK" if ann.is_valid else f"ERR: {ann.parse_error}"
            ops = ",".join(sorted(ann.operators.raw))
            click.echo(f"{label}\t{status}\t{ops}")


# ---------------------------------------------------------------------------
# classify sub-command — helpers
# ---------------------------------------------------------------------------


def _setup_logging(log_file: Optional[Path], verbose: bool) -> logging.Logger:
    logger = logging.getLogger("sparql_annotator.classify")
    if logger.handlers:
        return logger
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    if log_file:
        fh = logging.FileHandler(log_file, mode="w")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


def _print_results(
    classifier: QuestionTypeClassifier,
    results: Dict[
        str, Tuple[Set[str], Set[str], Optional[str], List[str], Dict[str, int]]
    ],
    logger: logging.Logger,
) -> None:
    W = 80
    print("\n" + "=" * W)
    print("Question Type Classification Results")
    print("=" * W + "\n")

    print("Extracted ontology rules:")
    print("-" * W)
    for name in sorted(classifier.type_definitions):
        defn = classifier.type_definitions[name]
        print(f"  {name}")
        for req in defn.all_requirements:
            if req.required:
                print(f"    required:     {sorted(req.required)}")
            if req.alternatives:
                print(f"    alternatives: {sorted(req.alternatives)} (any one)")
        d = ", ".join(sorted(defn.disjoint_with)) or "(none)"
        print(f"    disjoint: {d}")
    print()

    classified: Dict[str, List] = {}
    ambiguous: List = []
    unclassifiable: List = []
    queries_with_warnings: List[Tuple[str, Optional[str], List[str]]] = []
    total_bgp = 0
    total_tp = 0
    n_counts = 0

    for uri_str, (qtypes, features, label, warnings, counts) in results.items():
        short = uri_str.split("/")[-1]
        if warnings:
            queries_with_warnings.append((short, label, warnings))
        if not qtypes:
            unclassifiable.append((short, features, label, counts))
        elif len(qtypes) == 1:
            qtype = next(iter(qtypes))
            classified.setdefault(qtype, []).append((short, features, label, counts))
        else:
            ambiguous.append((short, qtypes, features, label, counts))
        if counts:
            n_counts += 1
            total_bgp += counts.get("bgpCount", 0)
            total_tp += counts.get("tpCount", 0)

    print("✓ Classified queries:")
    print("-" * W)
    for qtype in sorted(classifier.type_definitions):
        for short, features, label, counts in classified.get(qtype, []):
            print(f"  [{qtype}] {short} — {label or '(no label)'}")
            print(f"  {'':5s} features: {', '.join(sorted(features))}")
            print(f"  {'':5s} counts: {counts}")

    if ambiguous:
        print(f"\n⚠  Ambiguous ({len(ambiguous)}):")
        print("-" * W)
        for short, qtypes, features, label, counts in ambiguous:
            print(f"  {short} — {label or '(no label)'}")
            print(f"  {'':5s} conflicting types: {', '.join(sorted(qtypes))}")
            print(f"  {'':5s} features: {', '.join(sorted(features))}")
            print(f"  {'':5s} counts: {counts}")
            logger.warning(
                f"Ambiguous {short!r}: types={sorted(qtypes)} features={sorted(features)}"
            )

    if unclassifiable:
        print(f"\n✗ Unclassifiable ({len(unclassifiable)}):")
        print("-" * W)
        for short, features, label, counts in unclassifiable:
            print(f"  {short} — {label or '(no label)'}")
            print(f"  {'':5s} features: {', '.join(sorted(features)) or 'none'}")
            print(f"  {'':5s} counts: {counts}")
            logger.error(f"Unclassifiable {short!r}: features={sorted(features)}")

    if queries_with_warnings:
        n = len(queries_with_warnings)
        print(f"\n△  LSQ annotation warnings ({n} quer{'y' if n == 1 else 'ies'}):")
        print("-" * W)
        for short, label, warnings in queries_with_warnings:
            print(f"  {short} — {label or '(no label)'}")
            for w in warnings:
                print(f"  {'':5s}· {w}")

    total = len(results)
    n_ok = sum(len(v) for v in classified.values())
    n_amb = len(ambiguous)
    n_unc = len(unclassifiable)
    n_warn = len(queries_with_warnings)
    def pct(n):
        return f"{100 * n // total if total else 0}%"

    print("\n" + "=" * W)
    print(
        f"Total: {total} | Classified: {n_ok} ({pct(n_ok)}) | "
        f"Ambiguous: {n_amb} ({pct(n_amb)}) | Unclassifiable: {n_unc} ({pct(n_unc)}) | "
        f"\nLSQ warnings: {n_warn} ({pct(n_warn)}) | Queries with counts: {n_counts}"
    )
    print(f"Total BGP count: {total_bgp}")
    print(f"Total TP count: {total_tp}")
    print("=" * W + "\n")

    logger.info(
        f"Done — {total} queries: {n_ok} classified, {n_amb} ambiguous, "
        f"{n_unc} unclassifiable, {n_warn} with LSQ annotation warnings"
    )
    if n_amb == 0 and n_unc == 0 and n_warn == 0:
        logger.info("All queries classified cleanly.")
    else:
        if n_amb:
            logger.warning(
                f"{n_amb} ambiguous quer{'y' if n_amb == 1 else 'ies'} need review"
            )
        if n_unc:
            logger.error(
                f"{n_unc} quer{'y' if n_unc == 1 else 'ies'} could not be classified"
            )
        if n_warn:
            logger.warning(
                f"{n_warn} quer{'y' if n_warn == 1 else 'ies'} have LSQ annotation gaps"
            )


def _generate_type_assertions(
    query_file: Path,
    results: Dict[
        str, Tuple[Set[str], Set[str], Optional[str], List[str], Dict[str, int]]
    ],
    classifier: QuestionTypeClassifier,
    logger: logging.Logger,
) -> Graph:
    out = Graph()
    out.parse(str(query_file), format="turtle")
    out.bind("lsqv", LSQV)
    out.bind("qat", QAT)
    out.bind("qa", QA)

    count = 0
    updated_bgp = updated_tp = updated_pv = 0

    for uri_str, (qtypes, features, _label, _warnings, counts) in results.items():
        uri = URIRef(uri_str)

        for qtype in qtypes:
            type_uri = classifier.type_uris.get(qtype)
            if type_uri:
                out.add((uri, RDF.type, type_uri))
                count += 1

        declared_counts: Dict[str, int] = {}
        for sf in out.objects(uri, LSQV.hasStructuralFeatures):
            for prop_local in ("bgpCount", "tpCount", "projectVarCount"):
                for val in out.objects(sf, LSQV[prop_local]):
                    try:
                        declared_counts[prop_local] = int(val)
                    except (ValueError, TypeError):
                        pass

        for sf in list(out.objects(uri, LSQV.hasStructuralFeatures)):
            for prop_local in ("bgpCount", "tpCount", "projectVarCount"):
                for val in list(out.objects(sf, LSQV[prop_local])):
                    out.remove((sf, LSQV[prop_local], val))
            for uses in list(out.objects(sf, LSQV.usesFeature)):
                for val in list(out.objects(uses, OWL.hasValue)):
                    out.remove((uses, OWL.hasValue, val))
                out.remove((sf, LSQV.usesFeature, uses))
            out.remove((uri, LSQV.hasStructuralFeatures, sf))

        sf_node = BNode()
        out.add((uri, LSQV.hasStructuralFeatures, sf_node))

        for feat in sorted(features):
            feat_uri = None
            try:
                feat_uri = classifier.ontology.namespace_manager.expand_curie(
                    f"lsqv:{feat}"
                )
            except Exception:
                feat_uri = None
            if feat_uri:
                out.add((sf_node, LSQV.usesFeature, URIRef(feat_uri)))

        for key, new_value in counts.items():
            out.add((sf_node, LSQV[key], Literal(new_value)))
            old_value = declared_counts.get(key)
            if old_value is None or old_value != new_value:
                if key == "bgpCount":
                    updated_bgp += 1
                elif key == "tpCount":
                    updated_tp += 1
                elif key == "projectVarCount":
                    updated_pv += 1

    logger.info(f"Added {count} type assertions across {len(results)} queries")
    logger.info(
        f"Counts corrected (absent or mismatched) → "
        f"bgpCount={updated_bgp}, tpCount={updated_tp}, projectVarCount={updated_pv}"
    )
    return out


# ---------------------------------------------------------------------------
# classify sub-command
# ---------------------------------------------------------------------------


@cli.command("classify")
@click.option(
    "--query-file",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="LSQ query file (Turtle).",
)
@click.option(
    "--ontology",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="qa-types.ttl ontology file.",
)
@click.option(
    "--output",
    default=None,
    type=click.Path(path_type=Path),
    help="Output Turtle file with added rdf:type assertions.",
)
@click.option(
    "--log-file",
    default=None,
    type=click.Path(path_type=Path),
    help="Log file path (default: console only).",
)
@click.option("--verbose", is_flag=True, help="Enable DEBUG output.")
@click.option(
    "--debug", is_flag=True, help="Print extracted ontology rules before classifying."
)
def classify_cmd(query_file, ontology, output, log_file, verbose, debug):
    """Classify SPARQL queries by question type using LSQ features."""
    logger = _setup_logging(log_file, verbose)
    try:
        classifier = QuestionTypeClassifier(ontology, logger=logger)
        if debug:
            for name, defn in sorted(classifier.type_definitions.items()):
                logger.info(repr(defn))
        results = classifier.classify_queries_from_file(query_file)
        _print_results(classifier, results, logger)
        if output:
            out_graph = _generate_type_assertions(
                query_file, results, classifier, logger
            )
            out_graph.serialize(destination=str(output), format="turtle")
            logger.info(f"Saved classified queries to {output}")
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":
    cli()
