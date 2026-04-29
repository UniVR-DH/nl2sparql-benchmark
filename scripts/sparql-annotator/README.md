# sparql-annotator

Annotate SPARQL queries with operator and structural features, and classify them
by NL question type using an OWL ontology.

## Setup

```bash
cd scripts/sparql-annotator
uv sync --all-extras        # or: uv venv .venv && uv pip install -e . pytest
```

All commands below assume you are at the **repository root** and use the local venv:

```bash
scripts/sparql-annotator/.venv/bin/python -m sparql_annotator.cli <command>
# or after activating: sparql-annotator <command>
```

---

## Commands

### `classify` — replaces `classify_questions_cli.py`

Classify all queries in a Turtle file by question type using `graphs/qa-types.ttl`.

```bash
# Console output only
scripts/sparql-annotator/.venv/bin/python -m sparql_annotator.cli classify \
  --query-file graphs/ck25/ck25-queries.ttl \
  --ontology   graphs/qa-types.ttl

# With TTL output and log file (timestamped)
scripts/sparql-annotator/.venv/bin/python -m sparql_annotator.cli classify \
  --query-file graphs/ck25/ck25-queries.ttl \
  --ontology   graphs/qa-types.ttl \
  --output     .temp/$(date +"%Y-%m-%d-%H-%M")_classified.ttl \
  --log-file   .temp/$(date +"%Y-%m-%d-%H-%M")_classification.log

# Verbose (DEBUG) + print ontology rules
scripts/sparql-annotator/.venv/bin/python -m sparql_annotator.cli classify \
  --query-file graphs/ck25/ck25-queries.ttl \
  --ontology   graphs/qa-types.ttl \
  --verbose --debug
```

### `inspect` — query debugger

Print the SPARQL text, parse tree, algebra tree, and declared LSQ features for
a single query. Useful for diagnosing classification issues.

```bash
scripts/sparql-annotator/.venv/bin/python -m sparql_annotator.cli inspect \
  --query-file graphs/ck25/ck25-queries.ttl \
  --query-id   30
```

The `--query-id` can be a full URI or just the local part (e.g. `30`, `q-30`).

Optionally render the algebra tree as a Graphviz diagram (requires the `graphviz`
system package and the `viz` extra):

```bash
# Install viz extra
uv pip install -e ".[viz]"

# Save as SVG, PNG, or DOT source
scripts/sparql-annotator/.venv/bin/python -m sparql_annotator.cli inspect \
  --query-file graphs/ck25/ck25-queries.ttl \
  --query-id   30 \
  --viz        .temp/q30.svg
```

### `annotate` — generic operator annotation

Annotate queries from TTL, CSV, or JSON files with operator and structural features.

```bash
# TTL input, LSQ-conformant output
scripts/sparql-annotator/.venv/bin/python -m sparql_annotator.cli annotate \
  --input  graphs/ck25/ck25-queries.ttl \
  --format ttl \
  --output .temp/annotated.ttl

# CSV input
scripts/sparql-annotator/.venv/bin/python -m sparql_annotator.cli annotate \
  --input  queries.csv \
  --output annotated.csv
```

---

## Development

```bash
cd scripts/sparql-annotator
uv run pytest          # run tests
uv run ruff check .    # lint
uv run ruff format .   # format
```
