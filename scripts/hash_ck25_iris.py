#!/usr/bin/env python3
"""Hash selected CK25 IRIs while preserving file structure.

The script creates a mirrored output directory with the same file names as the
input CK25 directory. It hashes only IRIs under configured namespace prefixes.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

DEFAULT_NAMESPACES = [
    "http://ld.company.org/prod-vocab/",
    "http://ld.company.org/prod-instances/",
    # Alias for the user-provided variant; normalized to prod-instances below.
    "http://ld.company.org/prod-inst/",
    "http://dbpedia.org/resource/",
]

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
            return f"{ns}{short_hash(iri, hash_len)}"
    return iri


def replace_angle_iris(text: str, namespaces: list[str], hash_len: int) -> str:
    output_lines = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("@prefix"):
            output_lines.append(line)
            continue
        def repl(match: re.Match[str]) -> str:
            iri = match.group(1)
            return f"<{hash_full_iri(iri, namespaces, hash_len)}>"
        output_lines.append(ANGLE_IRI_RE.sub(repl, line))
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
        prefix: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(prefix)}:([A-Za-z0-9._~%-]+)")
        for prefix in prefix_to_ns
    }

    output_lines: list[str] = []
    in_multiline_string = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("@prefix "):
            output_lines.append(line)
            continue

        if not hash_query_strings and in_multiline_string:
            output_lines.append(line)
            if line.count('"""') % 2 == 1:
                in_multiline_string = False
            continue

        updated = line
        for prefix, pattern in patterns.items():
            ns = prefix_to_ns[prefix]

            def repl(match: re.Match[str], ns_value: str = ns, pref: str = prefix) -> str:
                local = match.group(1)
                full_iri = f"{ns_value}{local}"
                hashed = short_hash(full_iri, hash_len)
                return f"{pref}:{hashed}"

            updated = pattern.sub(repl, updated)

        output_lines.append(updated)

        if not hash_query_strings and line.count('"""') % 2 == 1:
            in_multiline_string = not in_multiline_string

    return "".join(output_lines)


def transform_text(
    text: str,
    namespaces: list[str],
    hash_len: int,
    hash_query_strings: bool,
) -> str:
    # Replace prefixed names first, while @prefix declarations still point to
    # the original namespaces we match against.
    step1 = replace_prefixed_names(text, namespaces, hash_len, hash_query_strings)
    return replace_angle_iris(step1, namespaces, hash_len)


def process_folder(
    input_dir: Path,
    output_dir: Path,
    namespaces: list[str],
    hash_len: int,
    hash_query_strings: bool,
) -> tuple[int, int]:
    output_dir.mkdir(parents=True, exist_ok=True)

    in_files = sorted([p for p in input_dir.iterdir() if p.is_file()])
    transformed_count = 0

    for src in in_files:
        dst = output_dir / src.name
        content = src.read_text(encoding="utf-8")
        transformed = transform_text(content, namespaces, hash_len, hash_query_strings)
        dst.write_text(transformed, encoding="utf-8")
        transformed_count += 1

    return len(in_files), transformed_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hash selected CK25 IRIs into a mirrored output folder."
    )
    parser.add_argument(
        "--input-dir",
        default="graphs/ck25",
        help="Input CK25 directory (default: graphs/ck25)",
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
        help="Also hash prefixed IRIs inside embedded SPARQL/query strings (default: off).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist or is not a directory: {input_dir}")

    ns_input = args.namespace if args.namespace else DEFAULT_NAMESPACES
    namespaces = normalize_namespaces(ns_input)
    if not namespaces:
        raise SystemExit("No valid namespaces provided.")

    file_count, transformed_count = process_folder(
        input_dir=input_dir,
        output_dir=output_dir,
        namespaces=namespaces,
        hash_len=args.hash_len,
        hash_query_strings=args.hash_query_strings,
    )

    print("CK25 IRI hashing complete")
    print(f"Input dir:   {input_dir}")
    print(f"Output dir:  {output_dir}")
    print(f"Namespaces:  {', '.join(namespaces)}")
    print(f"Hash queries: {args.hash_query_strings}")
    print(f"Files in:    {file_count}")
    print(f"Files out:   {transformed_count}")

    if file_count != transformed_count:
        raise SystemExit("Mismatch between input and output file counts.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
