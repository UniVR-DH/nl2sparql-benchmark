# sparql-annotator

Annotate SPARQL queries with operator and structural features, and classify them
by NL question type using an OWL ontology.

---

## Setup

```bash
cd scripts/sparql-annotator
uv sync          # create .venv and install all dependencies

# With optional Graphviz rendering support
uv sync --extra viz

# With dev tools (pytest, ruff)
uv sync --dev
```

All CLI examples below assume you are at the **repository root**.

```bash
# Shorthand used throughout this README
SA="scripts/sparql-annotator/.venv/bin/python -m sparql_annotator.cli"
```

---

## CLI Commands

### `classify` — ontology-driven question-type classification

Classifies all `lsqv:Query` resources in a Turtle file against the QA types ontology.
Outputs a classification report to the console and optionally writes an enriched TTL file.

```bash
# Console report only
$SA classify \
  --query-file graphs/ck25/ck25-queries.ttl \
  --ontology   graphs/qa-types.ttl

# With enriched TTL output and log file
$SA classify \
  --query-file graphs/ck25/ck25-queries.ttl \
  --ontology   graphs/qa-types.ttl \
  --output     .temp/$(date +"%Y-%m-%d-%H-%M")_classified.ttl \
  --log-file   .temp/$(date +"%Y-%m-%d-%H-%M")_classification.log

# Verbose (DEBUG level) + print extracted ontology rules
$SA classify \
  --query-file graphs/ck25/ck25-queries.ttl \
  --ontology   graphs/qa-types.ttl \
  --verbose --debug

# List only ambiguous queries (no other output)
$SA classify \
  --query-file graphs/ck25/ck25-queries.ttl \
  --ontology   graphs/qa-types.ttl \
  --ambiguous-only

# List only queries with antipatterns (no other output)
$SA classify \
  --query-file graphs/ck25/ck25-queries.ttl \
  --ontology   graphs/qa-types.ttl \
  --ap-only
```

`--ambiguous-only` prints one line per ambiguous query to stdout and suppresses all logging:

```
27    CounterFactualIdentification,RankedListing
36    AggregateEnumeration,LimitedRankedListing
```

`--ap-only` prints one line per query that has at least one antipattern:

```
15    AP01    ORDER BY + LIMIT 1 used to find an extremum.
42    AP01,AP09,AP05    ORDER BY + LIMIT 1 ...; Projected variable(s) ...
```

Both flags suppress all logging and are suitable for piping.

**Options:**

| Flag | Description |
|---|---|
| `--query-file` | LSQ Turtle file containing `lsqv:Query` resources (required) |
| `--ontology` | `qa-types.ttl` ontology file (required) |
| `--output` | Output Turtle file with added `rdf:type` and corrected `lsqv:hasStructuralFeatures` |
| `--log-file` | Write log to file (default: console only) |
| `--verbose` | Enable DEBUG output |
| `--debug` | Print extracted ontology rules before classifying |
| `--ambiguous-only` | Print only ambiguous query IDs and their conflicting types (tab-separated), suppress all logging |
| `--ap-only` | Print only queries with antipatterns (`id\tcodes\tmessages`), suppress all logging |

**Output TTL enrichment:** for each classified query the output adds `rdf:type qat:*` assertions
and rebuilds the `lsqv:hasStructuralFeatures` blank node with algebra-derived features and
corrected `bgpCount` / `tpCount` / `projectVarCount` values.

---

### `inspect` — single-query debugger

Prints the SPARQL text, parse tree (syntax), algebra tree, and declared LSQ features for
one query. Useful for diagnosing classification issues.

```bash
# Text + trees + LSQ features
$SA inspect \
  --query-file graphs/ck25/ck25-queries.ttl \
  --query-id   30

# Save algebra tree as SVG diagram (requires graphviz system package + viz extra)
$SA inspect \
  --query-file graphs/ck25/ck25-queries.ttl \
  --query-id   30 \
  --viz        .temp/q30.svg

# Other diagram formats
$SA inspect --query-file ... --query-id 30 --viz .temp/q30.png
$SA inspect --query-file ... --query-id 30 --viz .temp/q30.dot
```

`--query-id` accepts a full URI or just the local part (`30`, `q-30`, etc.).

The `--viz` diagram includes the SPARQL query text as a side panel by default.
The algebra tree nodes are colour-coded by type (BGP=green, Filter=yellow, LeftJoin=blue, etc.).

---

### `report` — dataset-level analysis reports

Classifies all queries in a file, detects antipatterns, and writes multiple CSV and/or
LaTeX report files to an output directory.

```bash
# CSV only (default)
$SA report \
  --query-file graphs/ck25/ck25-queries.ttl \
  --ontology   graphs/qa-types.ttl \
  --output-dir .temp/reports \
  --prefix     ck25

# CSV + LaTeX
$SA report \
  --query-file graphs/ck25/ck25-queries.ttl \
  --ontology   graphs/qa-types.ttl \
  --output-dir .temp/reports \
  --prefix     ck25 \
  --format     csv,latex
```

**Options:**

| Flag | Description |
|---|---|
| `--query-file` | LSQ Turtle file containing `lsqv:Query` resources (required) |
| `--ontology` | `qa-types.ttl` ontology file (required) |
| `--output-dir` | Directory for output files — created if missing (required) |
| `--prefix` | Filename prefix for all output files (default: `report`) |
| `--format` | Output format(s): `csv`, `latex`, or `csv,latex` (default: `csv`) |

**Output files** (all prefixed with `<prefix>_`):

| File | Content |
|---|---|
| `features.csv` | LSQ feature presence matrix (one row per query, one column per feature) |
| `operators.csv` | SPARQL operator presence matrix + `query_form` + `filter_functions` |
| `question_types.csv` | Classification status and assigned `qat:*` types per query |
| `antipatterns.csv` | AP01–AP09 flags + JSON messages per query |
| `metrics.csv` | `bgp_count`, `triple_count`, `projected_var_count` per query |
| `summary.csv` | Aggregate statistics (totals, averages, per-AP counts) |
| `count_by_question_type.csv` | Frequency table: queries per question type |
| `count_by_feature.csv` | Frequency table: queries per LSQ structural feature |
| `count_by_operator.csv` | Frequency table: queries per SPARQL operator |

LaTeX output (when `--format` includes `latex`): `features.tex`, `operators.tex`,
`summary.tex`, `antipatterns.tex` — uses `xcolor` (with the `table` option for `\cellcolor`), `booktabs`, and `amssymb`; matrices are
auto-transposed when the dataset has more than 20 queries.

---

### `annotate` — generic operator annotation

Annotates queries from TTL, CSV, or JSON files with `OperatorSet` fields.
Format is auto-detected from the file extension.

```bash
# TTL → TTL (plain operator annotation)
$SA annotate \
  --input  graphs/ck25/ck25-queries.ttl \
  --output .temp/annotated.ttl

# TTL → TTL with LSQ-conformant structural features bnode
$SA annotate \
  --input  graphs/ck25/ck25-queries.ttl \
  --format ttl \
  --output .temp/annotated_lsq.ttl

# CSV input/output
$SA annotate --input queries.csv --output annotated.csv

# JSON input/output
$SA annotate --input queries.json --output annotated.json

# Console summary (no --output)
$SA annotate --input queries.csv
```

**Options:**

| Flag | Description |
|---|---|
| `--input` | Input file (TTL / CSV / JSON) (required) |
| `--format` | Force format: `ttl`, `csv`, `json` (default: auto-detect from extension) |
| `--output` | Output file path (default: print summary to console) |

---

## Python API

### Annotating queries

```python
from sparql_annotator import Annotator, QueryRecord
from sparql_annotator.adapters.ttl import TTLAdapter
from sparql_annotator.adapters.csv import CSVAdapter
from sparql_annotator.adapters.json import JSONAdapter

# From a list of raw strings
annotator = Annotator()
records = [QueryRecord(uri=None, label="q1", text="SELECT ?s WHERE { ?s ?p ?o }", metadata={})]
annotations = annotator.annotate(records)

ann = annotations[0]
print(ann.operators.query_form)          # "SELECT"
print(ann.operators.graph_patterns)      # set of OPTIONAL/UNION/MINUS/GRAPH/SERVICE
print(ann.operators.filters)             # FILTER / FILTER NOT EXISTS / FILTER EXISTS
print(ann.operators.filter_functions)    # REGEX / LANG / DATATYPE / BOUND / STR / ...
print(ann.operators.aggregates)          # COUNT / SUM / AVG / MIN / MAX / ...
print(ann.operators.solution_modifiers)  # GROUP BY / HAVING / ORDER BY / LIMIT / OFFSET
print(ann.operators.assignments)         # BIND / VALUES
print(ann.operators.projection_modifiers)# DISTINCT / REDUCED
print(ann.operators.subqueries)          # bool
print(ann.operators.property_paths)      # bool
print(ann.operators.raw)                 # flat set of all detected operator names
print(ann.operators.bgp_count)           # int — number of BGP nodes
print(ann.operators.tp_count)            # int — total triple patterns
print(ann.operators.project_var_count)   # int — projected variables (SELECT only)

# From a file
adapter = TTLAdapter()
annotations = annotator.annotate_file("graphs/ck25/ck25-queries.ttl", input_adapter=adapter)
```

### Classifying queries

```python
from pathlib import Path
from sparql_annotator import QuestionTypeClassifier

clf = QuestionTypeClassifier(Path("graphs/qa-types.ttl"))

# Classify all queries in a file
results = clf.classify_queries_from_file(Path("graphs/ck25/ck25-queries.ttl"))

for uri, (qtypes, features, label, warnings, counts, *_) in results.items():
    print(f"{uri.split('/')[-1]}: {qtypes}")
    print(f"  features: {sorted(features)}")
    print(f"  counts:   {counts}")
    if warnings:
        print(f"  warnings: {warnings}")
```

### Algebra inspection

```python
from sparql_annotator.algebra import (
    parse_query,
    extract_operators,
    detect_lsq_features,
    compute_metrics,
    referenced_terms,
)

text = "SELECT ?s WHERE { ?s ?p ?o . FILTER(REGEX(STR(?s), 'foo')) }"
ok, parsed, err = parse_query(text)

# Translate once; pass alg= to avoid a second translateQuery on the mutated tree
import rdflib.plugins.sparql.algebra as _a
alg = _a.translateQuery(parsed)

# Generic operator extraction — reuses the already-translated algebra
ops = extract_operators(text, parsed, alg=alg)
print(ops.filters)           # {"FILTER"}
print(ops.filter_functions)  # {"REGEX", "STR"}

# LSQ feature names (used by classifier) — reuse the same alg
feats = detect_lsq_features(alg.algebra, parsed)
print(feats)  # {"Filter"}

# Structural metrics (SPARQL 1.1 §18 compliant BGP counting)
bgp, tp, pv = compute_metrics(alg.algebra)

# IRIs referenced in angle brackets
iris = referenced_terms(text)
```

### Algebra visualization

```python
from sparql_annotator.algebra_viz import save_algebra_viz
import rdflib.plugins.sparql.algebra as _a
from sparql_annotator.algebra import parse_query

text = "SELECT ?s WHERE { ?s ?p ?o . OPTIONAL { ?s <http://x.org/y> ?z } }"
_, parsed, _ = parse_query(text)
alg = _a.translateQuery(parsed)

# Save as SVG with SPARQL text panel (default)
save_algebra_viz(alg.algebra, ".temp/query.svg", fmt="svg", sparql_text=text)

# Without text panel
save_algebra_viz(alg.algebra, ".temp/query.png", fmt="png", show_query=False)

# DOT source only
save_algebra_viz(alg.algebra, ".temp/query.dot", fmt="dot", sparql_text=text)

# Lower-level: get a graphviz.Digraph object for further customization
from sparql_annotator.algebra_viz import render_algebra_dot
dot = render_algebra_dot(alg.algebra, sparql_text=text)
print(dot.source)          # raw DOT source
dot.render("query", format="svg", cleanup=True)
```

---

## Antipattern Detection

Detect structural issues in SPARQL queries before classification or annotation.

### CLI — `inspect --check`

```bash
$SA inspect \
  --query-file graphs/ck25/ck25-queries.ttl \
  --query-id   30 \
  --check
```

### Python API

```python
from sparql_annotator.antipatterns import detect_antipatterns, AntipatternIssue

issues = detect_antipatterns("SELECT ?x ?y WHERE { ?x a <http://x.org/T> }")
for issue in issues:
    print(f"[{issue.code}] {issue.message}")
    print(f"  → {issue.hint}")
```

### Detected antipatterns

| Code | Description |
|---|---|
| AP01 | `ORDER BY` + `LIMIT 1` for extrema — use `MIN()`/`MAX()` instead |
| AP02 | `DISTINCT` with aggregation — redundant or misleading |
| AP03 | Non-aggregate projected variable alongside aggregate without `GROUP BY` |
| AP04 | Cartesian product — disconnected BGP components |
| AP05 | Projected variable not in `GROUP BY` and not aggregated |
| AP06 | Aggregate used in `FILTER` instead of `HAVING` |
| AP07 | `SELECT` alias referenced in the same `SELECT` clause |
| AP08 | Non-standard/vendor-specific syntax (algebra translation fails) |
| AP09 | Projected variable never bound in the query body |

Returns an empty list for unparseable queries (no crash).

---

## BGP counting semantics

`compute_metrics` follows SPARQL 1.1 §18: the inner pattern of `FILTER NOT EXISTS` /
`FILTER EXISTS` is a separate group graph pattern and counts as a distinct BGP.

`extract_operators` counts only algebra-level BGP nodes (does not recurse into
`Builtin_NOTEXISTS` / `Builtin_EXISTS` inner patterns). Use `compute_metrics` directly
when you need the spec-correct count.

---

## Development

```bash
cd scripts/sparql-annotator
uv run pytest              # run tests (185 tests)
uv run pytest -x -q        # stop on first failure
uv run ruff check .        # lint
uv run ruff format .       # format
```

### Architectural decisions and guidelines

#### rdflib parse-tree mutation

`rdflib.plugins.sparql.algebra.translateQuery(parsed)` **mutates the parse
tree in-place**.  After it returns, the `parsed` object is no longer a valid
input for a second `translateQuery` call — doing so produces silently wrong
results (wrong query form, missing operators, etc.).

Rules that follow from this:

1. **Parse fresh for every independent translation.**  If two subsystems both
   need to translate the same query text, each must call `parseQuery` on its
   own copy of the text.
2. **Share the algebra, not the parse tree.**  When a single call site needs
   both the algebra (for metrics / operator extraction) and the parse tree (for
   HAVING detection), translate once and pass the resulting `alg` object to
   every consumer.  `extract_operators(text, parsed, alg=alg)` supports this
   pattern explicitly.
3. **`detect_antipatterns` always re-parses.**  It calls `parseQuery` internally
   and ignores any `parsed` argument for this reason.  Do not try to share a
   parse tree with it.

#### Single-parse pipeline

`QuestionTypeClassifier.classify_queries_from_graph` is the single entry point
for per-query SPARQL analysis.  It:

- parses the query text once (`parseQuery`),
- translates once (`translateQuery`),
- passes `parsed` and `alg` to `extract_features` so feature extraction,
  implied-feature detection, and count-annotation checks all reuse the same
  algebra object without re-parsing,
- computes metrics and extracts operators from the same `alg` object,
- returns the `OperatorSet` as the 6th element of the result tuple.

`ReportGenerator._collect` unpacks the `OperatorSet` directly from the
classifier result and does **not** re-parse.  `detect_antipatterns` is called
separately (it re-parses internally, which is unavoidable).

If you add a new analysis step that needs the algebra, add it inside
`classify_queries_from_graph` alongside the existing metrics/operator
extraction — do not add a new `parseQuery` / `translateQuery` call in
`_collect`, `extract_features`, or any downstream consumer.

#### Classifier result tuple

`classify_queries_from_graph` returns a dict mapping URI strings to 6-tuples:

```
(qtypes, features, label, warnings, counts, op_set)
```

Unpack with `*_` for any elements you do not need, so that adding a 7th
element in the future does not break callers:

```python
for uri, (qtypes, features, label, warnings, counts, *_) in results.items():
    ...
```
