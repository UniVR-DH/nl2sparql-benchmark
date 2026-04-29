#!/usr/bin/env python3
"""
CLI wrapper for QuestionTypeClassifier.
This utility parses SPARQL queries in LSQ format and automatically assigns
question types (Factoid, AggregateFactoid, Comparative, etc.) based on declared
structural features. Classification rules are derived dynamically from the
qa-types.ttl ontology.


This module provides the command-line interface, logging setup, file-level
orchestration and RDF output. The classification logic lives in
`scripts/classify_questions.py` (imported below).

The classifier:
1. Parses the ontology to extract OWL restrictions for each question type
   (including inherited features via transitive rdfs:subClassOf)
2. Builds a symmetric disjointness closure
3. Matches queries against feature requirements
4. Resolves multiple matches by preferring the most specific type in a hierarchy,
   or flags genuine ambiguity

Usage:
    python classify_questions.py --query-file <path/to/queries.ttl> \\
                                 --ontology <path/to/qa-types.ttl> \\
                                 [--output <path/to/output.ttl>] \\
                                 [--log-file <path/to/log>] \\
                                 [--verbose] [--debug]



"""
from pathlib import Path
import argparse
import logging
import sys
from typing import Optional, Dict, Tuple, Set, List

from classify_questions import QuestionTypeClassifier, LSQV, QAT, QA
from rdflib import Graph, URIRef, Literal, BNode
from rdflib.namespace import RDF, OWL


def setup_logging(log_file: Optional[Path] = None, verbose: bool = False) -> logging.Logger:
        """
        Configure and return a `logging.Logger` used by the CLI and passed
        into `QuestionTypeClassifier`.

        Behaviour:
            - Creates a logger named "classify_questions" and sets the level to
                DEBUG if `verbose` is True, otherwise INFO.
            - Adds a console StreamHandler writing to stdout.
            - If `log_file` is provided, also adds a FileHandler at DEBUG level.

        Rationale:
            The classifier implementation accepts an injected `logger` so the
            CLI is the authoritative place to configure handlers and formatting.
        """
        logger = logging.getLogger("classify_questions")
        # avoid adding multiple handlers if this module is reloaded
        if logger.handlers:
            return logger
        level = logging.DEBUG if verbose else logging.INFO
        logger.setLevel(level)

        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

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


def print_results(
    classifier: QuestionTypeClassifier,
    results: Dict[str, Tuple[Set[str], Set[str], Optional[str], List[str], Dict[str, int]]],
    logger: logging.Logger,
) -> None:
    """Pretty-print classification results to stdout using the CLI logger."""
    W = 80
    print("\n" + "=" * W)
    print("Question Type Classification Results")
    print("=" * W + "\n")

    # Ontology rules summary
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

    # Aggregate count stats
    # total_bgp / total_tp — sum of algebra-derived values across all queries
    # n_counts             — queries for which algebra-derived counts are available
    # Correction tracking (how many were wrong) happens in generate_type_assertions_cli.
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

    # Classified
    print("✓ Classified queries:")
    print("-" * W)
    for qtype in sorted(classifier.type_definitions):
        for short, features, label, counts in classified.get(qtype, []):
            print(f"  [{qtype}] {short} — {label or '(no label)'}")
            print(f"  {'':5s} features: {', '.join(sorted(features))}")
            print(f"  {'':5s} counts: {counts}")

    # Ambiguous
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

    # Unclassifiable
    if unclassifiable:
        print(f"\n✗ Unclassifiable ({len(unclassifiable)}):")
        print("-" * W)
        for short, features, label, counts in unclassifiable:
            print(f"  {short} — {label or '(no label)'}")
            print(f"  {'':5s} features: {', '.join(sorted(features)) or 'none'}")
            print(f"  {'':5s} counts: {counts}")
            logger.error(
                f"Unclassifiable {short!r}: features={sorted(features)}"
            )

    # LSQ annotation warnings
    if queries_with_warnings:
        print(f"\n△  LSQ annotation warnings ({len(queries_with_warnings)} quer"
              f"{'y' if len(queries_with_warnings) == 1 else 'ies'}):")
        print("-" * W)
        for short, label, warnings in queries_with_warnings:
            print(f"  {short} — {label or '(no label)'}")
            for w in warnings:
                print(f"  {'':5s}· {w}")

    # Summary
    total = len(results)
    n_ok = sum(len(v) for v in classified.values())
    n_amb = len(ambiguous)
    n_unc = len(unclassifiable)
    n_warn = len(queries_with_warnings)
    pct = lambda n: f"{100 * n // total if total else 0}%"

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
            logger.warning(f"{n_amb} ambiguous quer{'y' if n_amb == 1 else 'ies'} need review")
        if n_unc:
            logger.error(f"{n_unc} quer{'y' if n_unc == 1 else 'ies'} could not be classified")
        if n_warn:
            logger.warning(f"{n_warn} quer{'y' if n_warn == 1 else 'ies'} have LSQ annotation gaps")


def generate_type_assertions_cli(
    query_file: Path,
    results: Dict[str, Tuple[Set[str], Set[str], Optional[str], List[str], Dict[str, int]]],
    classifier: QuestionTypeClassifier,
    logger: logging.Logger,
) -> Graph:
    """Return a copy of the query graph enriched with rdf:type assertions and counts."""
    out = Graph()
    out.parse(str(query_file), format="turtle")
    out.bind("lsqv", LSQV)
    out.bind("qat", QAT)
    out.bind("qa", QA)

    count = 0

    # Counters for how many count values were updated/written
    updated_bgp = 0
    updated_tp = 0
    updated_pv = 0

    for uri_str, (qtypes, features, _label, _warnings, counts) in results.items():
        uri = URIRef(uri_str)

        for qtype in qtypes:
            type_uri = classifier.type_uris.get(qtype)
            if type_uri:
                out.add((URIRef(uri_str), RDF.type, type_uri))
                count += 1

        # --- Read declared counts from existing sf bnode(s) BEFORE removal ---
        # Keys: "bgpCount", "tpCount", "projectVarCount"
        declared_counts: Dict[str, int] = {}
        for sf in out.objects(uri, LSQV.hasStructuralFeatures):
            for prop_local in ("bgpCount", "tpCount", "projectVarCount"):
                for val in out.objects(sf, LSQV[prop_local]):
                    try:
                        declared_counts[prop_local] = int(val)
                    except (ValueError, TypeError):
                        pass

        # --- Remove old feature structures (including count literals on sf bnode) ---
        for sf in list(out.objects(uri, LSQV.hasStructuralFeatures)):
            for prop_local in ("bgpCount", "tpCount", "projectVarCount"):
                for val in list(out.objects(sf, LSQV[prop_local])):
                    out.remove((sf, LSQV[prop_local], val))
            # Remove usesFeature triples (flat or nested with owl:hasValue)
            for uses in list(out.objects(sf, LSQV.usesFeature)):
                for val in list(out.objects(uses, OWL.hasValue)):
                    out.remove((uses, OWL.hasValue, val))
                out.remove((sf, LSQV.usesFeature, uses))
            out.remove((uri, LSQV.hasStructuralFeatures, sf))

        # --- Rebuild structural-features bnode with features AND counts ---
        # Emits: lsqv:hasStructuralFeatures [ lsqv:usesFeature lsqv:Feat1, lsqv:Feat2 ;
        #                                     lsqv:bgpCount N ; lsqv:tpCount N ; ... ]
        sf_node = BNode()
        out.add((uri, LSQV.hasStructuralFeatures, sf_node))

        for feat in sorted(features):
            feat_uri = None
            try:
                feat_uri = classifier.ontology.namespace_manager.expand_curie(f"lsqv:{feat}")
            except Exception:
                feat_uri = None
            if feat_uri:
                out.add((sf_node, LSQV.usesFeature, URIRef(feat_uri)))

        # Write algebra-derived counts onto the sf bnode; track only real corrections
        # (value was absent or differed from what the algebra computed).
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify SPARQL queries by question type using LSQ features.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/classify_questions_cli.py \
    --query-file graphs/ck25/ck25-queries.ttl \
    --ontology graphs/qa-types.ttl

  python scripts/classify_questions_cli.py \
    --query-file graphs/ck25/ck25-queries.ttl \
    --ontology graphs/qa-types.ttl \
    --output graphs/ck25/ck25-queries-classified.ttl \
    --log-file .temp/classification.log
        """,
    )
    parser.add_argument("--query-file", type=Path, required=True,
                        help="LSQ query file (Turtle).")
    parser.add_argument("--ontology", type=Path, required=True,
                        help="qa-types.ttl ontology file.")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output Turtle file with added rdf:type assertions.")
    parser.add_argument("--log-file", type=Path, default=None,
                        help="Log file path (default: console only).")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable DEBUG output.")
    parser.add_argument("--debug", action="store_true",
                        help="Print extracted ontology rules before classifying.")
    args = parser.parse_args()

    logger = setup_logging(log_file=args.log_file, verbose=args.verbose)

    for label, path in [("Query file", args.query_file), ("Ontology", args.ontology)]:
        if not path.exists():
            logger.error(f"{label} not found: {path}")
            sys.exit(1)

    try:
        classifier = QuestionTypeClassifier(args.ontology, logger=logger)

        if args.debug:
            for name, defn in sorted(classifier.type_definitions.items()):
                logger.info(repr(defn))

        results = classifier.classify_queries_from_file(args.query_file)
        print_results(classifier, results, logger)

        if args.output:
            out_graph = generate_type_assertions_cli(args.query_file, results, classifier, logger)
            out_graph.serialize(destination=str(args.output), format="turtle")
            logger.info(f"Saved classified queries to {args.output}")

    except Exception as exc:
        logger.exception(f"Fatal error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()