# sparql-annotator

Annotate SPARQL queries with operator and structural features, and classify them
by NL question type using an OWL ontology.

---

## Setup

```bash
cd scripts/sparql-annotator
uv venv .venv && uv pip install -e .

# With optional Graphviz rendering support
uv pip install -e ".[viz]"

# With dev tools (pytest, ruff)
uv pip install -e . pytest ruff
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
```

`--ambiguous-only` prints one line per ambiguous query to stdout and suppresses all logging:

```
27    CounterFactualIdentification,RankedListing
36    AggregateEnumeration,LimitedRankedListing
```

Useful for piping into other tools or quickly auditing ontology coverage gaps.

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

for uri, (qtypes, features, label, warnings, counts) in results.items():
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

# Generic operator extraction
ops = extract_operators(text, parsed)
print(ops.filters)           # {"FILTER"}
print(ops.filter_functions)  # {"REGEX", "STR"}

# LSQ feature names (used by classifier)
import rdflib.plugins.sparql.algebra as _a
alg = _a.translateQuery(parsed)
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
uv run pytest              # run tests (124 tests)
uv run pytest -x -q        # stop on first failure
uv run ruff check .        # lint
uv run ruff format .       # format
```
