# `sparql-annotator` — Roadmap

## Overview

`sparql-annotator` is an installable Python package that ingests SPARQL queries from various
file formats, parses them, and produces structured annotations of the operators and structural
features present in each query. It also hosts an ontology-driven **question-type classifier**
that maps LSQ-annotated queries to a taxonomy of NL question types.

All structural analysis (operators, LSQ features, metrics) is derived from the rdflib algebra
tree via `algebra.py`. No keyword-scan, no `repr()` hacks.

---

## Development Environment

```bash
cd scripts/sparql-annotator
uv venv .venv && uv pip install -e . pytest
uv run pytest          # tests
uv run ruff check .    # lint
uv run mypy sparql_annotator
```

---

## Package Structure

```
sparql-annotator/
├── pyproject.toml
├── sparql_annotator/
│   ├── __init__.py
│   ├── namespaces.py       # LSQV, QAT, QA
│   ├── model.py            # QueryRecord, Annotation, OperatorSet,
│   │                       # FeatureRequirement, QuestionTypeDefinition
│   ├── algebra.py          # parse_query, extract_operators,
│   │                       # detect_lsq_features, compute_metrics, referenced_terms
│   ├── ontology.py         # build_type_definitions, build_depth_cache
│   ├── classifier.py       # QuestionTypeClassifier
│   ├── annotator.py        # Annotator
│   ├── inspect_query.py    # inspect CLI sub-command
│   ├── cli.py              # CLI: annotate / classify / inspect
│   └── adapters/
│       ├── base.py
│       ├── ttl.py
│       ├── csv.py
│       └── json.py
└── tests/
    ├── test_basic.py
    ├── test_ttl_lsq.py
    ├── test_algebra.py
    └── test_classifier.py
```

---

## Milestones

### ✅ M1–M2.6 — Foundation through Classifier Integration — DONE

Everything up to and including the classifier migration is complete:

- `model.py`: `QueryRecord`, `Annotation`, `OperatorSet` (with structural metrics),
  `FeatureRequirement`, `QuestionTypeDefinition`
- `algebra.py`: single proper algebra-walk module — replaces all prior keyword-scan and
  duplicate metric implementations. Includes fixes for:
  - Spurious `Bind` on multi-aggregate `Extend` chains (GROUP BY re-projection)
  - Spurious `Filter` on HAVING when inner `Extend` aliases sit between the HAVING
    `Filter` and `AggregateJoin`
- `ontology.py`: ontology parsing → type definitions + depth cache
- `classifier.py`: `QuestionTypeClassifier` — LSQ feature extraction + ontology-driven
  classification
- `adapters/ttl.py`, `adapters/csv.py`, `adapters/json.py`: all file adapters
- `cli.py`: `annotate`, `classify`, `inspect` sub-commands
- `inspect_query.py`: query debugger (text + parse tree + algebra tree + LSQ features)
- 24 tests passing

---

### ✅ M3 — Complete Operator Coverage (v0.3) — DONE

`extract_operators` in `algebra.py` now covers:
- `VALUES` — detected via `ToMultiSet(values)` node
- `SERVICE` — detected via `ServiceGraphPattern` node
- `GRAPH` — detected via `Graph` node
- `CONSTRUCT` / `DESCRIBE` — query form from algebra root node name
- Property path operators — detected via `rdflib.paths.Path` instances in BGP triple predicates
- FILTER expression functions (`REGEX`, `LANG`, `DATATYPE`, `STR`, `BOUND`, `IN`, etc.) — detected via `Builtin_*` nodes inside Filter expressions, stored in `OperatorSet.filter_functions`
- `OperatorSet.filter_functions: Set[str]` field added to model

---

### 🔲 M4 — Validation Layer (v0.4)

Optional `--validate` mode. Seeds already in `classifier.py`:
`_check_count_annotations`, `_check_sparql_syntax`.

```python
@dataclass
class ValidationIssue:
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    location: Optional[str]
```

---

### 🔲 M5 — Reporting (v0.5)

`--output-format` flag; Markdown report mode; `--summary` aggregate statistics.

---

### 🔲 M6 — Public API and Documentation (v1.0)

- Finalize `__init__.py`; mark internals with `_`
- Full type annotations, NumPy-style docstrings
- Publish to PyPI
- GitHub Actions CI via `uv`

---

## Dependencies

| Package | Purpose |
|---|---|
| `rdflib` | SPARQL parsing, algebra walking, TTL I/O — **required** |
| `click` | CLI |
| `SPARQLWrapper` | Optional: `lsqv:resultCount` (`sparql-annotator[endpoint]`) |

## Design Principles

- **One algebra walk**: all structural analysis derives from the rdflib algebra tree. No keyword-scan.
- **Invalid queries are rejected**: no partial annotation, no silent degradation.
- **Adapter pattern**: core logic never imports from `adapters/`.
- **Flat module layout**: no sub-packages.
- **`uv` for local dev**.
