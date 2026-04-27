# `sparql-annotator` — Roadmap

## Overview

`sparql-annotator` is an installable Python package that ingests SPARQL queries from various
file formats, parses them, and produces structured annotations of the operators and structural
features present in each query. It is designed around a clean adapter pattern so that new input
and output formats can be added without touching core logic.

---

## Development Environment Setup

This is the first thing to do before any work on the project.

### 1. Prerequisites

- **Python 3.10 or higher** — check with `python3 --version`
- **`uv`** — the only tool you need to install manually:

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # or on macOS via Homebrew
  brew install uv
  ```

  Verify with `uv --version`. All other tooling (virtualenv, dependencies, pytest, ruff, mypy) is managed by `uv` from this point forward. Do not install anything else globally.

### 2. Clone the repository

```bash
git clone https://github.com/eccenca/sparql-annotator
cd sparql-annotator
```

### 3. Install all dependencies

```bash
uv sync --all-extras
```

This creates `.venv/` locally, installs all runtime dependencies, optional extras, and dev
tools (pytest, ruff, mypy) exactly as pinned in `uv.lock`. No activation step is needed —
prefix commands with `uv run`.

### 4. Verify the environment

```bash
uv run pytest                          # all tests should pass
uv run ruff check .                    # no lint errors
uv run mypy sparql_annotator           # no type errors
uv run sparql-annotator --help         # CLI is reachable
```

If all four commands succeed, the environment is fully operational.

### 5. Common day-to-day commands

```bash
uv run pytest                          # run the full test suite
uv run pytest tests/test_parser.py     # run a single test file
uv run ruff check .                    # lint
uv run ruff format .                   # auto-format
uv run mypy sparql_annotator           # type-check
uv run sparql-annotator annotate \
  --input tests/fixtures/sample.ttl \  # run the CLI against a fixture
  --output /tmp/out.ttl
```

### 6. Adding or upgrading dependencies

```bash
uv add <package>                       # add a runtime dependency
uv add --dev <package>                 # add a dev-only dependency
uv add --optional endpoint SPARQLWrapper  # add to an optional extra group
uv lock --upgrade                      # upgrade all deps within constraints
```

Always commit `uv.lock` alongside any `pyproject.toml` change. PRs that modify
`pyproject.toml` without an updated `uv.lock` will fail CI.

---

## Package Structure

```
sparql-annotator/
├── pyproject.toml
├── uv.lock
├── README.md
├── ROADMAP.md
├── sparql_annotator/
│   ├── __init__.py
│   ├── cli.py                  # Entry point: argument parsing, wiring
│   ├── annotator.py            # Core annotation engine
│   ├── model.py                # Dataclasses: QueryRecord, Annotation, OperatorSet
│   ├── parser.py               # SPARQL parsing and algebra walking (rdflib only)
│   ├── operators.py            # Operator taxonomy and extraction rules
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py             # Abstract InputAdapter and OutputAdapter
│   │   ├── ttl.py              # Turtle / LSQ adapter (input + output)
│   │   ├── csv.py              # CSV adapter (input + output)
│   │   └── json.py             # JSON adapter (input + output)
│   └── utils/
│       ├── __init__.py
│       └── logging.py
└── tests/
    ├── fixtures/
    │   ├── sample.ttl
    │   ├── sample.csv
    │   └── sample.json
    ├── test_annotator.py
    ├── test_adapters.py
    ├── test_operators.py
    └── test_parser.py
```

---

## Core Concepts

### `QueryRecord`
A format-agnostic internal representation of a single query:
```python
@dataclass
class QueryRecord:
    uri: Optional[str]       # IRI if available (TTL), else None
    label: Optional[str]     # Human-readable label if available
    text: str                # Raw SPARQL query string
    metadata: dict           # Any extra fields carried through from the source
```

### `Annotation`
The result of successfully processing one `QueryRecord`. Only valid, parseable queries
produce an `Annotation` — there is no partial or degraded annotation state:
```python
@dataclass
class Annotation:
    record: QueryRecord
    operators: OperatorSet
```

### `OperatorSet`
The detected operators for a query, structured in groups:
```python
@dataclass
class OperatorSet:
    query_form: str                    # SELECT / ASK / CONSTRUCT / DESCRIBE
    projection_modifiers: Set[str]     # DISTINCT, REDUCED
    graph_patterns: Set[str]           # OPTIONAL, UNION, MINUS, GRAPH, SERVICE
    filters: Set[str]                  # FILTER, FILTER NOT EXISTS, FILTER EXISTS
    aggregates: Set[str]               # COUNT, SUM, AVG, MIN, MAX, GROUP_CONCAT, SAMPLE
    solution_modifiers: Set[str]       # GROUP BY, HAVING, ORDER BY, LIMIT, OFFSET
    assignments: Set[str]              # BIND, VALUES
    subqueries: bool                   # Nested SELECT present
    property_paths: bool               # Any property path operator used
    raw: Set[str]                      # Flat set of all detected operator names
```

---

## Milestones

### M1 — Foundation (v0.1)

Goals: installable package, working SPARQL parser, plain-list input support.

- Set up `pyproject.toml` with `rdflib` and `click` as the only runtime dependencies
- Initialise the project with `uv` (`uv init`, `uv add rdflib click`); commit `uv.lock`
- All dev tasks (install, run, test, lint) use `uv run` — no manual venv activation required
- Implement `model.py` with `QueryRecord`, `Annotation`, `OperatorSet` as described above
- Implement `parser.py`:
  - Use `rdflib.plugins.sparql.parser.parseQuery` and
    `rdflib.plugins.sparql.algebra.translateQuery` as the sole parsing path —
    there is no fallback and no text-based heuristic
  - Queries that fail to parse raise a `ValueError` with a clear message; the caller
    (adapter or CLI) is responsible for logging and skipping the record
  - Invalid queries never produce an `Annotation`
- Implement `operators.py`:
  - Walk the parsed algebra tree (`rdflib.plugins.sparql.algebra`) to extract operators
  - Cover all `OperatorSet` fields
- Implement `adapters/base.py`:
  - `InputAdapter`: abstract class with `read(source) -> Iterable[QueryRecord]`
  - `OutputAdapter`: abstract class with `write(annotations, destination)`
- Accept a plain Python `list[str]` as input (no adapter needed — direct API use)
- Basic `annotator.py` wiring: `Annotator.annotate(records) -> List[Annotation]`
- CLI (`cli.py`): `sparql-annotator annotate --input <file> --format <fmt> [--output <file>]`

---

### M2 — File Adapters (v0.2)

Goals: all three file adapters working end-to-end.

**`adapters/ttl.py` — Turtle / LSQ adapter**

Input:
- Parse with `rdflib`
- Detect query text via `lsqv:text`, `shui:queryText`, or `sh:select` (configurable predicate)
- Extract URI (`rdf:subject`), label (`rdfs:label`), and any declared feature triples into `metadata`

Output:
- Re-serialize the original graph with added annotation triples
- Use a configurable output vocabulary (default: a minimal `sparqla:` annotation namespace)

**`adapters/csv.py` — CSV adapter**

Input:
- Configurable column name for query text (default: `query`)
- Remaining columns carried into `metadata`
- Handles both files and `io.StringIO` streams

Output:
- Original columns preserved, annotation columns appended
- One column per `OperatorSet` field (comma-separated sets where multi-valued)

**`adapters/json.py` — JSON adapter**

Input:
- Supports array-of-objects and object-of-objects (keyed by URI/id)
- Configurable key name for query text (default: `query` or `queryText`)
- Full source object carried into `metadata`

Output:
- Original objects augmented with an `annotations` key containing the `OperatorSet` as a
  nested object

Format auto-detection: if no `--format` flag is given, the CLI infers format from file
extension (`.ttl` / `.turtle` → TTL, `.csv` → CSV, `.json` → JSON). Unknown extensions
raise a clear error.

---

### M2.5 — LSQ-Conformant Output (v0.2.5)

Goals: make TTL output fully conformant with the LSQ / `lsqv:` vocabulary as used in the
target annotation format.

**Structural metrics** — extend `OperatorSet` (or add a parallel `StructuralMetrics`
dataclass) with:
- `bgp_count: int` — number of Basic Graph Pattern groups in the query
- `tp_count: int` — total number of triple patterns
- `project_var_count: int` — number of variables in the SELECT projection

All three are computed by walking the algebra tree in `parser.py`. They are only available
for successfully parsed queries — there is no approximate or heuristic path.

**`dct:subject` extraction** — add a `referenced_terms()` method to `Annotation` that
collects all IRI constants appearing as subject, predicate, or object in triple patterns
(resolved to full IRIs). This set is written as `dct:subject` triples in two places:
directly on the query resource and inside the `lsqv:hasStructuralFeatures` blank node,
matching the dual-placement in the target format.

**`lsqv:hasStructuralFeatures` blank node** — the TTL adapter output must serialize
annotations as:
```turtle
:1 lsqv:hasStructuralFeatures [
    a lsqv:StructuralFeatures ;
    lsqv:bgpCount 1 ;
    lsqv:projectVarCount 1 ;
    lsqv:tpCount 2 ;
    lsqv:usesFeature lsqv:Distinct, lsqv:Select, lsqv:TriplePattern ;
    dct:subject <...>, pv:memberOf
] .
```
The vague "minimal `sparqla:` namespace" plan from M1 is superseded by this LSQ-conformant
blank node structure. The TTL adapter gains an explicit `lsq` output mode flag.

**`lsqv:resultCount`** — optional field, populated only when `--endpoint <url>` is
supplied. The annotator executes each query against the given SPARQL endpoint and records
the result count. Queries that time out or return an endpoint error get
`lsqv:resultCount -1` with a log warning. Note: only queries that have already been
successfully parsed and annotated reach this stage — endpoint errors are distinct from
parse errors and do not affect the annotation result.
Requires `SPARQLWrapper` as an optional dependency (`pip install sparql-annotator[endpoint]`).

---

### M3 — Operator Coverage and Taxonomy (v0.3)

Goals: complete and validated operator extraction.

- Audit `rdflib` algebra node types and map each to `OperatorSet` fields
- Add detection for:
  - `VALUES` (inline data)
  - `SERVICE` (federated queries)
  - `GRAPH` pattern
  - `CONSTRUCT` and `DESCRIBE` query forms
  - Property path operators (`/`, `|`, `*`, `+`, `?`, `^`)
  - Expression functions used in `FILTER` (`REGEX`, `LANG`, `DATATYPE`, `STR`, etc.)
- Introduce an `ExtendedOperatorSet` subclass for optional deeper annotation (off by
  default, enabled via `--extended`)
- Unit tests with fixtures covering each operator type

---

### M4 — Validation Layer (v0.4)

Goals: detect and report structural inconsistencies between declared metadata and actual
query content.

Inspired by the bugs found in `queries.ttl`, add an optional `--validate` mode that checks:

- `queryType` metadata (e.g. `shui:queryType`) matches actual query form (SELECT vs ASK)
- `DISTINCT` is not misplaced at `SELECT` level when the intent is `COUNT(DISTINCT ...)`
- `GROUP BY` variables are all defined in the `WHERE` clause
- `LIMIT 1` with `ORDER BY` where ties are semantically meaningful (heuristic warning)
- `OFFSET` value is consistent with the natural-language label if present (heuristic)
- Declared `relatedProperty` / `relatedClass` metadata matches properties/classes actually used

Validation results are appended to `Annotation` as a `List[ValidationIssue]`:
```python
@dataclass
class ValidationIssue:
    severity: Literal["error", "warning", "info"]
    code: str          # e.g. "WRONG_QUERY_TYPE", "MISPLACED_DISTINCT"
    message: str
    location: Optional[str]  # e.g. "GROUP BY clause"
```

---

### M5 — Output Formats and Reporting (v0.5)

Goals: richer output options.

- `--output-format` flag separate from `--format` (input format)
- Supported output formats: TTL, CSV, JSON, Markdown report
- Markdown report mode: generates a human-readable summary listing per-query annotations
  and any validation issues
- `--summary` flag: print aggregate statistics (operator frequency table, validation
  issue counts)

---

### M6 — Public API and Documentation (v1.0)

Goals: stable, documented, tested public API.

- Finalize public API surface in `__init__.py`; mark internal modules with `_`
- Full type annotations throughout
- Docstrings on all public classes and methods (NumPy style)
- `README.md` with quickstart, API reference summary, and CLI reference
- Dedicated **Custom Adapter Guide** in `docs/custom-adapters.md` (see section below)
- Publish to PyPI
- GitHub Actions CI: lint (ruff), type-check (mypy), tests (pytest) on Python 3.10 / 3.11 / 3.12
  - CI uses `uv` for environment setup (`astral-sh/setup-uv` action + `uv sync --frozen`)
    to guarantee reproducible builds from `uv.lock`

---

## Custom Adapter Guide

The built-in TTL, CSV, and JSON adapters cover the most common cases, but any data source
or output format can be supported by implementing the two abstract base classes from
`sparql_annotator.adapters.base` and passing instances directly to the `Annotator` API.
This section is the normative reference for third-party adapter authors.

---

### Base Classes

Both base classes live in `sparql_annotator.adapters.base` and are part of the stable
public API from v1.0 onwards.

```python
from abc import ABC, abstractmethod
from typing import Iterable, Union
from pathlib import Path
import io

from sparql_annotator.model import QueryRecord, Annotation


class InputAdapter(ABC):
    """Reads a source and yields QueryRecord objects.

    Implementors must override `read()`. The source argument may be a file
    path, an open stream, a URL string, or any other object your adapter
    understands — the contract with the core engine is solely the return type.
    """

    @abstractmethod
    def read(
        self, source: Union[Path, io.IOBase, str]
    ) -> Iterable[QueryRecord]:
        """Parse *source* and return an iterable of QueryRecord objects.

        Args:
            source: The data source. Type is adapter-defined.

        Yields:
            QueryRecord: One record per SPARQL query found in the source.
        """


class OutputAdapter(ABC):
    """Serialises a list of Annotation objects to a destination.

    Implementors must override `write()`. The destination argument follows
    the same open convention as InputAdapter.source.
    """

    @abstractmethod
    def write(
        self,
        annotations: Iterable[Annotation],
        destination: Union[Path, io.IOBase, str],
    ) -> None:
        """Serialise *annotations* to *destination*.

        Args:
            annotations: The annotations produced by the core engine.
            destination: Where to write output. Type is adapter-defined.
        """
```

---

### Minimal Example — Plain-text file, one query per line

```python
# my_package/adapters/plaintext.py
from pathlib import Path
from typing import Iterable, Union
import io

from sparql_annotator.adapters.base import InputAdapter, OutputAdapter
from sparql_annotator.model import QueryRecord, Annotation


class PlainTextInputAdapter(InputAdapter):
    """Reads a UTF-8 text file with one SPARQL query per line."""

    def read(self, source: Union[Path, io.IOBase, str]) -> Iterable[QueryRecord]:
        if isinstance(source, (str, Path)):
            lines = Path(source).read_text(encoding="utf-8").splitlines()
        else:
            lines = source.read().splitlines()

        for i, line in enumerate(lines):
            line = line.strip()
            if line and not line.startswith("#"):
                yield QueryRecord(
                    uri=None,
                    label=f"query-{i+1}",
                    text=line,
                    metadata={"source_line": i + 1},
                )


class PlainTextOutputAdapter(OutputAdapter):
    """Writes one annotation summary per line: label TAB operators."""

    def write(
        self,
        annotations: Iterable[Annotation],
        destination: Union[Path, io.IOBase, str],
    ) -> None:
        lines = []
        for ann in annotations:
            label = ann.record.label or ann.record.uri or "unknown"
            ops = ", ".join(sorted(ann.operators.raw))
            lines.append(f"{label}\t{ops}")

        output = "\n".join(lines)
        if isinstance(destination, (str, Path)):
            Path(destination).write_text(output, encoding="utf-8")
        else:
            destination.write(output)
```

---

### Passing a Custom Adapter to the API

Custom adapters are passed directly to `Annotator.annotate_file()` or used to construct
`QueryRecord` lists for `Annotator.annotate()`. No registration or plugin system is needed.

```python
from sparql_annotator import Annotator
from my_package.adapters.plaintext import PlainTextInputAdapter, PlainTextOutputAdapter

annotator = Annotator()

# Using annotate_file() — adapter handles I/O, engine handles parsing
annotations = annotator.annotate_file(
    source="queries.txt",
    input_adapter=PlainTextInputAdapter(),
)

# Write output with a custom output adapter
PlainTextOutputAdapter().write(annotations, "annotations.txt")

# Alternatively, build records manually and call annotate() directly
records = list(PlainTextInputAdapter().read("queries.txt"))
annotations = annotator.annotate(records)
```

The CLI does not support custom adapters by design (it only knows about built-in formats).
Custom adapters are a Python API feature.

---

### Passing a Custom Adapter via the `Annotator` constructor

If your adapter requires configuration (credentials, namespace maps, endpoint URLs, etc.),
instantiate it with its own arguments and pass it in:

```python
from sparql_annotator import Annotator
from my_package.adapters.sparql_endpoint import EndpointInputAdapter

adapter = EndpointInputAdapter(
    endpoint_url="https://dbpedia.org/sparql",
    graph="http://dbpedia.org",
    query_property="http://example.org/hasQuery",
)

annotator = Annotator()
annotations = annotator.annotate_file(
    source="https://dbpedia.org/sparql",
    input_adapter=adapter,
)
```

---

### Checklist for Custom Adapter Authors

Before publishing a third-party adapter, verify the following:

- `read()` never yields `None` — yield nothing rather than `None`
- `read()` carries all source-specific fields (IDs, timestamps, etc.) into
  `QueryRecord.metadata` so they survive the round-trip to output
- `read()` does not pre-parse or validate SPARQL — pass the raw string; the engine will
  raise a `ValueError` for unparseable queries and the CLI/API will skip and log them
- `write()` receives only successfully annotated queries — `Annotation` objects are always
  fully populated; there is no `is_valid` guard needed
- `write()` is idempotent with respect to the destination (calling it twice should not
  corrupt the output)
- Both classes have NumPy-style docstrings
- A `tests/` directory with at least one round-trip test (read → annotate → write → verify)
- The adapter is listed under `[project.optional-dependencies]` in your own `pyproject.toml`
  if it has extra dependencies beyond `sparql-annotator`

---

## Dependencies

### Runtime

| Package | Purpose |
|---------|---------|
| `rdflib` | SPARQL parsing, algebra walking, TTL I/O — **required, no fallback** |
| `click` | CLI |
| `SPARQLWrapper` | Optional: execute queries for `lsqv:resultCount` (`pip install sparql-annotator[endpoint]`) |

`rdflib` is a hard dependency. The package will not import without it. There is no fallback
parser and no text-based heuristic path — all structural metrics and operator extraction
require a valid, parseable SPARQL query and a successfully translated algebra tree.

### Local Development with `uv`

`uv` is the required tool for local development. It manages the virtualenv, dependency
locking, and script running without requiring manual environment activation.

**Initial setup:**
```bash
# Install uv (once, system-wide)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and set up the project
git clone https://github.com/eccenca/sparql-annotator
cd sparql-annotator
uv sync --all-extras          # installs all deps including optional ones and dev tools
```

**Day-to-day commands:**
```bash
uv run pytest                 # run tests
uv run ruff check .           # lint
uv run ruff format .          # format
uv run mypy sparql_annotator  # type-check
uv run sparql-annotator --help  # run the CLI
```

**Adding a dependency:**
```bash
uv add <package>              # runtime dependency → updates pyproject.toml + uv.lock
uv add --dev <package>        # dev-only dependency
uv add --optional endpoint SPARQLWrapper   # optional extra
```

**Keeping the lockfile current:**
```bash
uv lock --upgrade             # upgrade all deps within declared constraints
```

`uv.lock` must be committed. PRs that change `pyproject.toml` without a matching `uv.lock`
update will fail CI.

---

## Design Principles

- **Adapter pattern strictly enforced**: core logic never imports from `adapters/`; adapters
  only produce/consume `QueryRecord` and `Annotation`
- **No format assumed**: the engine works on `QueryRecord` objects; format handling is
  entirely the adapter's responsibility
- **`rdflib` is the only parser**: all structural analysis is derived from the rdflib algebra
  tree. There is no fallback path and no text-based heuristic. If rdflib cannot parse a
  query, that query is invalid and is rejected — it does not produce an `Annotation`
- **Invalid queries are rejected, not annotated**: a query that fails to parse raises an
  exception at the boundary. Callers (adapters, CLI) log the error and skip the record.
  There is no `is_valid` flag, no partial annotation, and no silent degradation
- **Validation is opt-in**: annotation and validation are separate concerns; `--validate`
  adds the layer without coupling it to basic annotation
- **Custom adapters are first-class**: any third party can implement `InputAdapter` /
  `OutputAdapter` and pass instances directly to `Annotator` without modifying or forking
  the package; no plugin registry is required
- **`uv` for local dev**: all contributors use `uv` for environment management; no
  `pip install -e .` or manual venv instructions appear anywhere in the documentation