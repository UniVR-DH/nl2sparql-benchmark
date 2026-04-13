#!/usr/bin/env python3
"""Hash selected IRIs while preserving file structure.

The script creates a mirrored output directory with the same file names as the
input directory. It hashes only IRIs under configured namespace prefixes.
Query files are copied without hashing by default.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from pathlib import Path

import rdflib
from rdflib import Graph, Literal, URIRef

DEFAULT_NAMESPACES = [
    "http://ld.company.org/prod-vocab/",
    "http://ld.company.org/prod-instances/",
    "http://ld.company.org/prod-inst/",
    "http://dbpedia.org/resource/",
]

SKIP_FILES: set[str] = set()

COPY_ONLY_FILES = {
    "ck25-queries.ttl",
    "ck25-queries.graph",
}

SPARQL_STRING_PREDICATES = {
    "http://lsq.aksw.org/vocab#text",
    "http://www.w3.org/ns/shacl#select",
    "http://www.w3.org/ns/shacl#ask",
    "http://www.w3.org/ns/shacl#construct",
    "http://www.w3.org/ns/shacl#describe",
}

PREFIX_DECL_RE = re.compile(r"@prefix\s+([A-Za-z][\w-]*):\s*<([^>]+)>\s*\.")
SPARQL_PREFIX_RE = re.compile(r"PREFIX\s+([A-Za-z][\w-]*):\s*<([^>]+)>", re.IGNORECASE)
ANGLE_IRI_RE = re.compile(r"<([^>]+)>")


def normalize_namespaces(namespaces: list[str]) -> list[str]:
    normalized: list[str] = []
    for ns in namespaces:
        value = ns.strip()
        if not value:
            continue
        if not value.endswith("/"):
            value += "/"
        if value == "http://ld.company.org/prod-inst/":
            value = "http://ld.company.org/prod-instances/"
        if value not in normalized:
            normalized.append(value)
    return normalized


def short_hash(text: str, length: int) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def hash_full_iri(iri: str, namespaces: list[str], hash_len: int) -> str:
    for ns in namespaces:
        if iri.startswith(ns):
            local = iri[len(ns):]
            # Don't hash if there's no local name
            if not local:
                return iri
            return f"{ns}{short_hash(iri, hash_len)}"
    return iri


def hash_sparql_string(sparql: str, namespaces: list[str], hash_len: int) -> str:
    """Hash IRIs inside an embedded SPARQL query string.

    Handles both full IRIs (<http://...>) and prefixed names (pv:Something).
    PREFIX declarations inside the SPARQL string are preserved intact.
    """
    prefix_to_ns: dict[str, str] = {}
    for m in SPARQL_PREFIX_RE.finditer(sparql):
        prefix, ns = m.group(1), m.group(2)
        if any(ns == n or ns.startswith(n) for n in namespaces):
            prefix_to_ns[prefix] = ns

    def hash_angle_line(line: str) -> str:
        # Skip PREFIX declaration lines entirely
        if SPARQL_PREFIX_RE.match(line.strip()):
            return line
        def repl(m: re.Match[str]) -> str:
            iri = m.group(1)
            # Skip if IRI is exactly a namespace (no local name after it)
            if any(iri == ns or iri == ns.rstrip("/") for ns in namespaces):
                return f"<{iri}>"
            return f"<{hash_full_iri(iri, namespaces, hash_len)}>"
        return ANGLE_IRI_RE.sub(repl, line)
        
    

    lines = sparql.splitlines(keepends=True)
    result = []
    for line in lines:
        hashed_line = hash_angle_line(line)
        for prefix, ns in prefix_to_ns.items():
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(prefix)}:([A-Za-z0-9._~%-]+)"
            )
            def repl_pref(
                m: re.Match[str],
                ns_val: str = ns,
                pref: str = prefix,
            ) -> str:
                full_iri = f"{ns_val}{m.group(1)}"
                return f"{pref}:{short_hash(full_iri, hash_len)}"
            hashed_line = pattern.sub(repl_pref, hashed_line)
        result.append(hashed_line)
    return "".join(result)


def replace_angle_iris(
    text: str,
    namespaces: list[str],
    hash_len: int,
    skip_multiline_strings: bool = False,
) -> str:
    output_lines = []
    in_multiline_string = False
    for line in text.splitlines(keepends=True):
        # Never hash IRIs inside @prefix declarations
        if line.lstrip().startswith("@prefix"):
            output_lines.append(line)
            continue

        # Skip content inside multiline strings if requested
        if skip_multiline_strings and in_multiline_string:
            output_lines.append(line)
            if line.count('"""') % 2 == 1:
                in_multiline_string = False
            continue

        def repl(match: re.Match[str]) -> str:
            iri = match.group(1)
            return f"<{hash_full_iri(iri, namespaces, hash_len)}>"

        output_lines.append(ANGLE_IRI_RE.sub(repl, line))

        if skip_multiline_strings and line.count('"""') % 2 == 1:
            in_multiline_string = True

    return "".join(output_lines)


def replace_prefixed_names(
    text: str,
    namespaces: list[str],
    hash_len: int,
    hash_query_strings: bool,
) -> str:
    prefix_to_ns: dict[str, str] = {}
    for line in text.splitlines():
        m = PREFIX_DECL_RE.match(line.strip())
        if m:
            prefix, ns = m.group(1), m.group(2)
            if ns in namespaces:
                prefix_to_ns[prefix] = ns

        if hash_query_strings:
            sm = SPARQL_PREFIX_RE.search(line)
            if sm:
                prefix, ns = sm.group(1), sm.group(2)
                if ns in namespaces:
                    prefix_to_ns[prefix] = ns

    if not prefix_to_ns:
        return text

    patterns = {
        prefix: re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(prefix)}:([A-Za-z0-9._~%-]+)"
        )
        for prefix in prefix_to_ns
    }

    output_lines: list[str] = []
    in_multiline_string = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("@prefix "):
            output_lines.append(line)
            continue

        if in_multiline_string:
            output_lines.append(line)
            if line.count('"""') % 2 == 1:
                in_multiline_string = False
            continue

        updated = line
        for prefix, pattern in patterns.items():
            ns = prefix_to_ns[prefix]

            def repl(
                match: re.Match[str],
                ns_value: str = ns,
                pref: str = prefix,
            ) -> str:
                local = match.group(1)
                full_iri = f"{ns_value}{local}"
                hashed = short_hash(full_iri, hash_len)
                return f"{pref}:{hashed}"

            updated = pattern.sub(repl, updated)

        output_lines.append(updated)

        if line.count('"""') % 2 == 1:
            in_multiline_string = True

    return "".join(output_lines)


def hash_sparql_literals_via_rdflib(
    text: str,
    namespaces: list[str],
    hash_len: int,
) -> str:
    """Use rdflib to find embedded SPARQL literals and hash IRIs inside them."""
    g = Graph()
    try:
        g.parse(data=text, format="turtle")
    except Exception:
        return text

    replacements: list[tuple[str, str]] = []
    for pred_iri in SPARQL_STRING_PREDICATES:
        pred = URIRef(pred_iri)
        for s, p, o in g.triples((None, pred, None)):
            if isinstance(o, Literal):
                original = str(o)
                hashed = hash_sparql_string(original, namespaces, hash_len)
                if original != hashed:
                    replacements.append((original, hashed))

    result = text
    for original, hashed in replacements:
        result = result.replace(original, hashed, 1)
    return result


def transform_text(
    text: str,
    namespaces: list[str],
    hash_len: int,
    hash_query_strings: bool,
) -> str:
    if hash_query_strings:
        # Hash SPARQL literals first via rdflib
        text = hash_sparql_literals_via_rdflib(text, namespaces, hash_len)
        # Then hash remaining IRIs but skip multiline strings
        # (already processed above — avoids double hashing)
        step1 = replace_prefixed_names(
            text, namespaces, hash_len, hash_query_strings
        )
        return replace_angle_iris(
            step1, namespaces, hash_len, skip_multiline_strings=True
        )
    else:
        step1 = replace_prefixed_names(
            text, namespaces, hash_len, hash_query_strings
        )
        return replace_angle_iris(step1, namespaces, hash_len)


def process_folder(
    input_dir: Path,
    output_dir: Path,
    namespaces: list[str],
    hash_len: int,
    hash_query_strings: bool,
    skip_files: set[str],
    copy_only_files: set[str],
) -> tuple[int, int, int, int]:
    # Always overwrite output dir cleanly
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    in_files = sorted([p for p in input_dir.iterdir() if p.is_file()])
    transformed_count = 0
    skipped_count = 0
    copied_count = 0

    for src in in_files:
        dst = output_dir / src.name

        if src.name in skip_files:
            print(f"  Skipping:   {src.name}")
            skipped_count += 1
            continue

        if src.name in copy_only_files:
            shutil.copy2(src, dst)
            print(f"  Copied:     {src.name}")
            copied_count += 1
            continue

        content = src.read_text(encoding="utf-8")
        transformed = transform_text(
            content, namespaces, hash_len, hash_query_strings
        )
        dst.write_text(transformed, encoding="utf-8")
        transformed_count += 1
        print(f"  Processed:  {src.name}")

    return len(in_files), transformed_count, skipped_count, copied_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hash selected IRIs into a mirrored output folder."
    )
    parser.add_argument(
        "--input-dir",
        default="graphs/ck25",
        help="Input directory (default: graphs/ck25)",
    )
    parser.add_argument(
        "--output-dir",
        default="graphs/ck25-h",
        help="Output directory (default: graphs/ck25-h)",
    )
    parser.add_argument(
        "--namespace",
        action="append",
        default=None,
        help=(
            "Namespace to hash (repeatable). "
            "Defaults to prod-vocab, prod-instances/prod-inst alias, dbpedia/resource."
        ),
    )
    parser.add_argument(
        "--hash-len",
        type=int,
        default=16,
        help="Hex chars from sha256 to keep (default: 16)",
    )
    parser.add_argument(
        "--hash-query-strings",
        action="store_true",
        help="Hash IRIs inside embedded SPARQL strings using rdflib (default: off).",
    )
    parser.add_argument(
        "--skip-file",
        action="append",
        default=None,
        help="File name to skip entirely (repeatable).",
    )
    parser.add_argument(
        "--copy-only-file",
        action="append",
        default=None,
        help=(
            "File name to copy without hashing (repeatable). "
            "Defaults to ck25-queries.ttl and ck25-queries.graph."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(
            f"Input directory does not exist or is not a directory: {input_dir}"
        )

    ns_input = args.namespace if args.namespace else DEFAULT_NAMESPACES
    namespaces = normalize_namespaces(ns_input)
    if not namespaces:
        raise SystemExit("No valid namespaces provided.")

    skip_files = set(args.skip_file) if args.skip_file else SKIP_FILES
    copy_only_files = (
        set(args.copy_only_file) if args.copy_only_file else COPY_ONLY_FILES
    )

    file_count, transformed_count, skipped_count, copied_count = process_folder(
        input_dir=input_dir,
        output_dir=output_dir,
        namespaces=namespaces,
        hash_len=args.hash_len,
        hash_query_strings=args.hash_query_strings,
        skip_files=skip_files,
        copy_only_files=copy_only_files,
    )

    print("")
    print("IRI hashing complete")
    print(f"  Input dir:    {input_dir}")
    print(f"  Output dir:   {output_dir}")
    print(f"  Namespaces:   {', '.join(namespaces)}")
    print(f"  Hash queries: {args.hash_query_strings}")
    print(f"  Files in:     {file_count}")
    print(f"  Processed:    {transformed_count}")
    print(f"  Copied:       {copied_count}")
    print(f"  Skipped:      {skipped_count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())