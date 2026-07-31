#!/usr/bin/env python3
"""Hash selected IRIs while preserving file structure — streaming edition.

The script creates a mirrored output directory structure and writes output files
using the input file names with a ``-h`` suffix. It hashes only IRIs under
configured namespace prefixes.

Processing modes:
- streaming for large files (instances/vocab/shapes/void), 
- rdflib for small files (queries/examples) - loads entirely into memory (KB scale)

Typical usage
-------------
# GPTKB
uv run python scripts/hash-iris.py \
  --input-dir graphs/gptkb \
  --output-dir graphs/gptkb-h \
  --namespace https://gptkb.org/entity/ \
  --namespace https://gptkb.org/prop/ \
  --hash-len 10 \
  --hash-format int

# CK25
uv run python scripts/hash-iris.py \
  --input-dir graphs/ck25 \
  --output-dir graphs/ck25-h \
  --namespace http://dbpedia.org/resource/ \
  --namespace http://ld.company.org/prod-vocab/ \
  --namespace http://ld.company.org/prod-instances/ \
  --namespace http://ld.company.org/prod-inst/ \
  --namespace http://dbpedia.org/ontology/ \
  --hash-len 6 \
  --hash-format int
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Fixed defaults (dataset-independent)
# ---------------------------------------------------------------------------

SKIP_FILES: set[str] = set()


def _auto_defaults(input_dir: Path) -> tuple[set[str], set[str]]:
    kg = input_dir.name
    copy_only: set[str] = {f"{kg}-croissant.jsonld"}
    query_str: set[str] = {
        f"{kg}-queries.ttl",
        f"{kg}-examples.ttl",
        f"{kg}-queries.yaml",
    }
    return copy_only, query_str


SPARQL_STRING_PREDICATES = {
    "http://lsq.aksw.org/vocab#text",
    "http://www.w3.org/ns/shacl#select",
    "http://www.w3.org/ns/shacl#ask",
    "http://www.w3.org/ns/shacl#construct",
    "http://www.w3.org/ns/shacl#describe",
}

PREFIX_DECL_RE = re.compile(
    r"(?:@prefix|PREFIX)\s+([A-Za-z][\w-]*):\s*<([^>]+)>",
    re.IGNORECASE,
)
SPARQL_PREFIX_RE = re.compile(
    r"PREFIX\s+([A-Za-z][\w-]*):\s*<([^>]+)>",
    re.IGNORECASE,
)
ANGLE_IRI_RE = re.compile(r"<([^>]+)>")

# ---------------------------------------------------------------------------
# Namespace helpers
# ---------------------------------------------------------------------------


def normalize_namespace(ns: str) -> str:
    value = ns.strip()
    if not value:
        return value
    if not value[-1] in ("/", "#", "=", ":"):
        value += "/"
    return value


def normalize_namespaces(namespaces: list[str]) -> list[str]:
    normalized: list[str] = []
    for ns in namespaces:
        value = normalize_namespace(ns)
        if value and value not in normalized:
            normalized.append(value)
    return normalized


# ---------------------------------------------------------------------------
# HyperLogLog cardinality estimator (p=12)
# ---------------------------------------------------------------------------

class _TinyHLL:
    """Minimal HyperLogLog sketch (p=12, ~4 KB, ±1.6 % error)."""

    def __init__(self, p: int = 12) -> None:
        self.p = p
        self.m = 1 << p
        self.reg = [0] * self.m
        self._bits = 128 - p

    def add(self, x: str) -> None:
        h = int(hashlib.md5(x.encode()).hexdigest(), 16)
        idx = h & (self.m - 1)
        w = h >> self.p
        rank = self._bits - w.bit_length() + 1
        if rank > self.reg[idx]:
            self.reg[idx] = rank

    def count(self) -> float:
        import math
        alpha = 0.7213 / (1 + 1.079 / self.m)
        raw = alpha * self.m ** 2 / sum(2 ** -r for r in self.reg)
        if raw <= 2.5 * self.m:
            zeros = self.reg.count(0)
            if zeros:
                return self.m * math.log(self.m / zeros)
        return raw


_hll_per_ns: dict[str, _TinyHLL] = {}
_COLLISION_WARN_THRESHOLD = 0.1


def check_collision_warnings(hash_len: int, fmt: str) -> None:
    import math
    if fmt == "int":
        slots = 10 ** hash_len
    elif fmt == "alpha":
        slots = 26 ** hash_len
    else:  # hex
        slots = 16 ** hash_len
    threshold = slots * _COLLISION_WARN_THRESHOLD
    for ns, hll in _hll_per_ns.items():
        count = hll.count()
        if count >= threshold:
            p_collision = 1 - math.exp(-count * (count - 1) / (2 * slots))
            print(
                f"  WARNING: Collision risk — ~{count:,.0f} unique IRIs under <{ns}> "
                f"({count / slots:.1%} of {slots:,} slots, "
                f"P(collision) ≈ {p_collision:.2%}, "
                f"hash-len={hash_len}, format={fmt}). "
                "Consider increasing --hash-len."
            )


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _to_base26(n: int, length: int) -> str:
    """Convert a non-negative integer to a base-26 lowercase string of fixed length.

    Digits are 'a' (0) … 'z' (25).  The result is left-padded with 'a' (the
    zero digit) to exactly *length* characters, mirroring how ``int`` mode
    zero-pads with '0'.
    """
    chars: list[str] = []
    for _ in range(length):
        chars.append(chr(ord("a") + n % 26))
        n //= 26
    return "".join(reversed(chars))


def short_hash(text: str, length: int, fmt: str = "hex") -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if fmt == "int":
        return str(int(digest, 16) % (10 ** length)).zfill(length)
    if fmt == "alpha":
        return _to_base26(int(digest, 16) % (26 ** length), length)
    return digest[:length]

def hash_full_iri(iri: str, namespaces: list[str], hash_len: int, fmt: str) -> str:
    for ns in namespaces:
        if iri.startswith(ns):
            local = iri[len(ns):]
            if not local:
                return iri
            if ns not in _hll_per_ns:
                _hll_per_ns[ns] = _TinyHLL()
            _hll_per_ns[ns].add(iri)
            return f"{ns}{short_hash(iri, hash_len, fmt)}"
    return iri


# ---------------------------------------------------------------------------
# Prefix extraction
# ---------------------------------------------------------------------------


def collect_prefixes(path: Path, namespaces: list[str]) -> dict[str, str]:
    normalized_ns_set = {normalize_namespace(ns) for ns in namespaces}
    prefix_to_ns: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            m = PREFIX_DECL_RE.search(line)
            if m:
                prefix, ns = m.group(1), m.group(2)
                ns_norm = normalize_namespace(ns)
                if ns_norm in normalized_ns_set:
                    prefix_to_ns[prefix] = ns_norm
    return prefix_to_ns


# ---------------------------------------------------------------------------
# SPARQL-string hashing
# ---------------------------------------------------------------------------


def hash_sparql_string(
    sparql: str, namespaces: list[str], hash_len: int, fmt: str
) -> str:
    prefix_to_ns: dict[str, str] = {}
    for line in sparql.splitlines():
        for sm in SPARQL_PREFIX_RE.finditer(line):
            pref, ns = sm.group(1), sm.group(2)
            ns_norm = normalize_namespace(ns)
            if ns_norm in namespaces:
                prefix_to_ns[pref] = ns_norm

    def hash_angle_line(line: str) -> str:
        if SPARQL_PREFIX_RE.match(line.strip()):
            return line

        def repl(m: re.Match[str]) -> str:
            iri = m.group(1)
            if any(iri == ns or iri == ns.rstrip("/") for ns in namespaces):
                return f"<{iri}>"
            return f"<{hash_full_iri(iri, namespaces, hash_len, fmt)}>"

        return ANGLE_IRI_RE.sub(repl, line)

    lines = sparql.splitlines(keepends=True)
    result = []
    for line in lines:
        hashed_line = hash_angle_line(line)
        for prefix, ns in prefix_to_ns.items():
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(prefix)}:([A-Za-z0-9_~%-][A-Za-z0-9._~%-]*[A-Za-z0-9_~%-]|[A-Za-z0-9_~%-]+)"
            )

            def repl_pref(
                m: re.Match[str],
                ns_val: str = ns,
                pref: str = prefix,
            ) -> str:
                full_iri = f"{ns_val}{m.group(1)}"
                return f"{pref}:{short_hash(full_iri, hash_len, fmt)}"

            hashed_line = pattern.sub(repl_pref, hashed_line)
        result.append(hashed_line)
    return "".join(result)


def _turtle_unescape(raw: str) -> str:
    return raw.replace("\\\\", "\\")


def _turtle_reescape(s: str) -> str:
    return s.replace("\\", "\\\\")


_TRIPLE_QUOTED_RE = re.compile(r'"""(.*?)"""', re.DOTALL)


def hash_sparql_literals_in_text(
    text: str, namespaces: list[str], hash_len: int, fmt: str
) -> str:
    try:
        import rdflib
    except ImportError as exc:
        raise RuntimeError(
            "hash_sparql_literals_in_text requires 'rdflib' to hash SPARQL "
            "query strings. Install rdflib or disable query-string hashing."
        ) from exc

    g = rdflib.Graph()
    try:
        g.parse(data=text, format="turtle")
    except Exception:
        return text

    sparql_pred_refs = {rdflib.URIRef(iri) for iri in SPARQL_STRING_PREDICATES}
    literals_to_hash: dict[str, str] = {}
    for s, p, o in g:
        if p in sparql_pred_refs and isinstance(o, rdflib.term.Literal):
            literals_to_hash[str(o)] = str(p)

    if not literals_to_hash:
        return text

    def replace_literal(m: re.Match[str]) -> str:
        raw_content = m.group(1)
        decoded = _turtle_unescape(raw_content)
        if decoded not in literals_to_hash:
            return m.group(0)
        hashed = hash_sparql_string(decoded, namespaces, hash_len, fmt)
        if hashed == decoded:
            return m.group(0)
        return f'"""{_turtle_reescape(hashed)}"""'

    return _TRIPLE_QUOTED_RE.sub(replace_literal, text)


# ---------------------------------------------------------------------------
# Line-level transformers (streaming)
# ---------------------------------------------------------------------------


def _is_prefix_line(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("@prefix ") or stripped.upper().startswith("PREFIX ")


_TRIPLE_QUOTE_RE = re.compile(r'"""')


def _transform_line_with_state(
    line: str,
    prefix_to_ns: dict[str, str],
    patterns: dict[str, re.Pattern[str]],
    namespaces: list[str],
    hash_len: int,
    fmt: str,
    starts_inside: bool = False,
) -> tuple[str, bool]:
    positions = [m.start() for m in _TRIPLE_QUOTE_RE.finditer(line)]

    if not positions:
        if not starts_inside:
            line = transform_line_prefixed(line, prefix_to_ns, patterns, hash_len, fmt)
            line = transform_line_angle(line, namespaces, hash_len, fmt)
        return line, starts_inside

    parts = []
    cursor = 0
    inside = starts_inside

    for pos in positions:
        segment = line[cursor:pos]
        if not inside:
            segment = transform_line_prefixed(
                segment, prefix_to_ns, patterns, hash_len, fmt
            )
            segment = transform_line_angle(segment, namespaces, hash_len, fmt)
        parts.append(segment)
        parts.append('"""')
        cursor = pos + 3
        inside = not inside

    tail = line[cursor:]
    if not inside:
        tail = transform_line_prefixed(tail, prefix_to_ns, patterns, hash_len, fmt)
        tail = transform_line_angle(tail, namespaces, hash_len, fmt)
    parts.append(tail)

    return "".join(parts), inside


def transform_line_angle(
    line: str,
    namespaces: list[str],
    hash_len: int,
    fmt: str,
) -> str:
    if _is_prefix_line(line):
        return line

    def repl(match: re.Match[str]) -> str:
        iri = match.group(1)
        return f"<{hash_full_iri(iri, namespaces, hash_len, fmt)}>"

    return ANGLE_IRI_RE.sub(repl, line)


def transform_line_prefixed(
    line: str,
    prefix_to_ns: dict[str, str],
    patterns: dict[str, re.Pattern[str]],
    hash_len: int,
    fmt: str,
) -> str:
    if _is_prefix_line(line) or not prefix_to_ns:
        return line

    updated = line
    for prefix, pattern in patterns.items():
        ns = prefix_to_ns[prefix]

        def repl(
            m: re.Match[str],
            ns_value: str = ns,
            pref: str = prefix,
        ) -> str:
            local = m.group(1).rstrip(".")
            full_iri = f"{ns_value}{local}"
            return f"{pref}:{short_hash(full_iri, hash_len, fmt)}"

        updated = pattern.sub(repl, updated)
    return updated


# ---------------------------------------------------------------------------
# File processor
# ---------------------------------------------------------------------------
def _process_yaml_queries(
    src: Path,
    dst: Path,
    namespaces: list[str],
    hash_len: int,
    fmt: str,
) -> None:
    """Hash IRIs inside SPARQL strings in a YAML question/query file.

    Only values stored under a 'sparql' key are touched — dataset metadata
    fields (id, defaultNamespace, question text, …) are left verbatim.
    """
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "Processing YAML query files requires 'pyyaml'. "
            "Install it with: pip install pyyaml"
        ) from exc

    data = yaml.safe_load(src.read_text(encoding="utf-8"))

    def _hash_obj(obj: object) -> object:
        if isinstance(obj, dict):
            return {
                k: (
                    hash_sparql_string(v, namespaces, hash_len, fmt)
                    if k == "sparql" and isinstance(v, str)
                    else _hash_obj(v)
                )
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_hash_obj(item) for item in obj]
        return obj

    hashed = _hash_obj(data)

    class _Lit(str):
        """Marker so the custom representer can force block style."""

    class _LiteralDumper(yaml.Dumper):
        pass

    _LiteralDumper.add_representer(
        _Lit,
        lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|"),
    )

    def _literalise(obj: object) -> object:
        if isinstance(obj, dict):
            return {k: _literalise(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_literalise(item) for item in obj]
        if isinstance(obj, str) and "\n" in obj:
            return _Lit(obj)
        return obj

    dst.write_text(
        yaml.dump(
            _literalise(hashed),
            Dumper=_LiteralDumper,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

import json

def _process_json_file(
    src: Path,
    dst: Path,
    namespaces: list[str],
    hash_len: int,
    fmt: str,
) -> None:
    import json
    data = json.load(src.open(encoding="utf-8"))

    def _hash_obj(obj: object) -> object:
        if isinstance(obj, dict):
            new_dict = {}
            for k, v in obj.items():
                if k == "sparql" and isinstance(v, str):
                    # Hash IRIs inside the SPARQL query string
                    new_dict[k] = hash_sparql_string(v, namespaces, hash_len, fmt)
                else:
                    new_dict[k] = _hash_obj(v)
            return new_dict
        if isinstance(obj, list):
            return [_hash_obj(item) for item in obj]
        if isinstance(obj, str):
            # Hash plain IRIs that start with a configured namespace
            for ns in namespaces:
                if obj.startswith(ns):
                    return hash_full_iri(obj, namespaces, hash_len, fmt)
            return obj
        return obj

    hashed = _hash_obj(data)
    json.dump(hashed, dst.open("w", encoding="utf-8"), indent=2, ensure_ascii=False)

def process_file_streaming(
    src: Path,
    dst: Path,
    namespaces: list[str],
    hash_len: int,
    fmt: str,
    hash_query_strings: bool,
) -> None:
    if src.suffix in (".json", ".jsonld"):
        _process_json_file(src, dst, namespaces, hash_len, fmt)
        return
    
    if hash_query_strings and src.suffix in (".yaml", ".yml"):
        _process_yaml_queries(src, dst, namespaces, hash_len, fmt)
        return

    prefix_to_ns = collect_prefixes(src, namespaces)

    patterns: dict[str, re.Pattern[str]] = {
        prefix: re.compile(
            rf"(?<![A-Za-z0-9_]){re.escape(prefix)}:([^\s;,/\"'<>\(\)\[\]{{}}]+)"
        )
        for prefix in prefix_to_ns
    }

    if hash_query_strings:
        text = src.read_text(encoding="utf-8")
        text = hash_sparql_literals_in_text(text, namespaces, hash_len, fmt)
        lines_iter = iter(text.splitlines(keepends=True))
    else:
        lines_iter = src.open("r", encoding="utf-8")

    try:
        with dst.open("w", encoding="utf-8") as out:
            inside_literal = False
            for line in lines_iter:
                if _is_prefix_line(line):
                    out.write(line)
                    continue
                transformed_line, inside_literal = _transform_line_with_state(
                    line, prefix_to_ns, patterns, namespaces, hash_len, fmt,
                    starts_inside=inside_literal
                )
                out.write(transformed_line)
    finally:
        if not hash_query_strings:
            try:
                lines_iter.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Folder processor
# ---------------------------------------------------------------------------


def _get_output_filename(src: Path) -> str:
    name = src.name
    suffixes = ''.join(src.suffixes)
    stem = name[:-len(suffixes)] if suffixes else name
    return f"{stem}-h{suffixes}"


def process_folder(
    input_dir: Path,
    output_dir: Path,
    namespaces: list[str],
    hash_len: int,
    fmt: str,
    hash_query_strings: bool,
    skip_files: set[str],
    copy_only_files: set[str],
    query_string_files: set[str],
) -> tuple[int, int, int, int, int]:
    if output_dir == input_dir:
        raise ValueError(f"Output directory cannot be the same as input directory: {output_dir}")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    in_files = sorted([p for p in input_dir.iterdir() if p.is_file()])
    transformed_count = 0
    skipped_count = 0
    copied_count = 0
    error_count = 0

    for src in in_files:
        if src.name in skip_files:
            print(f"  Skipping:   {src.name}")
            skipped_count += 1
            continue

        out_name = _get_output_filename(src)
        dst = output_dir / out_name

        if src.name in copy_only_files:
            shutil.copy2(src, dst)
            print(f"  Copied:     {src.name} -> {out_name}")
            copied_count += 1
            continue

        file_hash_qs = hash_query_strings and (src.name in query_string_files)

        try:
            process_file_streaming(src, dst, namespaces, hash_len, fmt, file_hash_qs)
            print(f"  Processed:  {src.name} -> {out_name}")
            transformed_count += 1
        except Exception as exc:
            print(f"  ERROR:      {src.name}: {exc}")
            error_count += 1
            if dst.exists():
                dst.unlink()

    return len(in_files), transformed_count, skipped_count, copied_count, error_count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Hash selected IRIs into a mirrored output folder (streaming). "
            "Works with any KG — GPTKB, CK25, or others."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Typical usage")[1] if "Typical usage" in __doc__ else "",
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Input directory containing the KG files (e.g. graphs/gptkb).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Output directory (default: <input-dir>-h, "
            "e.g. graphs/gptkb → graphs/gptkb-h)."
        ),
    )
    parser.add_argument(
        "--namespace",
        action="append",
        default=None,
        required=True,
        help="Namespace to hash (repeatable, required).",
    )
    parser.add_argument(
        "--hash-len",
        type=int,
        default=6,
        help="Number of characters (hex/alpha) or digits (int) to keep (default: 6).",
    )
    parser.add_argument(
        "--hash-format",
        choices=["hex", "int", "alpha"],
        default="hex",
        help=(
            "Output format for hashes: "
            "'hex' keeps the first --hash-len hex chars of the SHA-256 digest; "
            "'int' converts the digest to a decimal integer modulo 10**hash-len; "
            "'alpha' converts the digest to a lowercase letter string (a–z) "
            "modulo 26**hash-len."
        ),
    )
    parser.add_argument(
        "--hash-query-strings",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Hash IRIs inside embedded SPARQL strings using rdflib-based parsing. "
            "(only applied to --query-string-file entries; default: on)."
        ),
    )
    parser.add_argument(
        "--query-string-file",
        action="append",
        default=None,
        help=(
            "File name that contains embedded SPARQL strings and should be "
            "processed with rdflib (repeatable). "
            "Default: auto-detected as {kg}-queries.ttl and {kg}-examples.ttl "
            "where {kg} is the input directory name."
        ),
    )
    parser.add_argument(
        "--copy-only-file",
        action="append",
        default=None,
        help=(
            "File name to copy without hashing (repeatable). "
            "Default: auto-detected as {kg}-croissant.jsonld."
        ),
    )
    parser.add_argument(
        "--skip-file",
        action="append",
        default=None,
        help="File name to skip entirely (repeatable).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)

    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(
            f"Input directory does not exist or is not a directory: {input_dir}"
        )

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = input_dir.parent / f"{input_dir.name}-h"

    namespaces = normalize_namespaces(args.namespace)
    if not namespaces:
        raise SystemExit("No valid namespaces provided.")

    auto_copy_only, auto_query_str = _auto_defaults(input_dir)
    skip_files = set(args.skip_file) if args.skip_file else SKIP_FILES
    copy_only_files = set(args.copy_only_file) if args.copy_only_file else auto_copy_only
    query_string_files = (
        set(args.query_string_file) if args.query_string_file else auto_query_str
    )

    print(f"Namespaces:   {', '.join(namespaces)}")
    print(f"Hash format:  {args.hash_format} / len={args.hash_len}")
    print(f"Copy-only:    {', '.join(sorted(copy_only_files))}")
    print(f"Query files:  {', '.join(sorted(query_string_files))}")
    print("")

    file_count, transformed_count, skipped_count, copied_count, error_count = (
        process_folder(
            input_dir=input_dir,
            output_dir=output_dir,
            namespaces=namespaces,
            hash_len=args.hash_len,
            fmt=args.hash_format,
            hash_query_strings=args.hash_query_strings,
            skip_files=skip_files,
            copy_only_files=copy_only_files,
            query_string_files=query_string_files,
        )
    )

    print("")
    print("IRI hashing complete")
    print(f"  Input dir:    {input_dir}")
    print(f"  Output dir:   {output_dir}")
    print(f"  Files in:     {file_count}")
    print(f"  Processed:    {transformed_count}")
    print(f"  Copied:       {copied_count}")
    print(f"  Skipped:      {skipped_count}")
    if error_count:
        print(f"  Errors:       {error_count}")
    check_collision_warnings(args.hash_len, args.hash_format)

    return 1 if error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())