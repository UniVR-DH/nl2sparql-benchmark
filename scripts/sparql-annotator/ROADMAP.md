# `sparql-annotator` — Roadmap

## Overview

`sparql-annotator` is an installable Python CLI and library for annotating, classifying,
and reporting on SPARQL queries. All structural analysis derives from the rdflib algebra
tree — no keyword scanning, no `repr()` hacks.

---

## Package Structure

```
sparql-annotator/
├── pyproject.toml
├── sparql_annotator/
│   ├── algebra.py          # parse_query, extract_operators, detect_lsq_features,
│   │                       # compute_metrics, referenced_terms
│   ├── antipatterns.py     # AP01–AP09 detectors
│   ├── classifier.py       # QuestionTypeClassifier (ontology-driven)
│   ├── reporter.py         # ReportGenerator (CSV + LaTeX outputs)
│   ├── cli.py              # annotate / classify / inspect / report commands
│   ├── inspect_query.py
│   ├── annotator.py
│   ├── ontology.py
│   ├── model.py
│   ├── namespaces.py
│   └── adapters/           # TTL, CSV, JSON
└── tests/                  # 185 tests
```

---

## Milestones

### ✅ M1–M3 — Core Annotation & Classification (v0.1–v0.3)

- `algebra.py`: single algebra-walk for operators, LSQ features, metrics
- `classifier.py`: OWL ontology → question type taxonomy (10 types)
- `antipatterns.py`: 9 structural antipattern detectors (AP01–AP09)
- `adapters/`: TTL, CSV, JSON I/O
- CLI: `annotate`, `classify` (with `--ambiguous-only`, `--ap-only`), `inspect` (with `--viz`, `--check`)
- Key fixes: spurious Bind/Filter on GROUP BY re-projections and HAVING; parse tree mutation from `translateQuery`; CRLF normalisation in output TTL; orphaned blank nodes in classified output

### ✅ M4 — Reporting Command (v0.4)

- `reporter.py`: `ReportGenerator` producing 9 CSV files and 4 LaTeX tables per dataset
- CLI: `report --query-file --ontology --output-dir [--prefix] [--format csv,latex]`
- Output: features, operators, question_types, antipatterns, metrics, summary (with per-AP counts), count_by_question_type, count_by_feature, count_by_operator
- Error handling: `FileNotFoundError` for missing files, `ValueError` for invalid TTL
- 185 tests passing

---

### 🔲 M5 — Batch Validation (v0.5)

Add a `--validate` flag to `classify` / `report` that surfaces annotation quality issues.

**Planned checks:**
- `VAL_SYNTAX` — SPARQL parse errors
- `VAL_COUNT_MISMATCH` — declared LSQ counts differ from `compute_metrics`
- `VAL_ANTIPATTERN_*` — selected antipatterns as validation warnings

**CLI additions:**
- `--validate` on `classify` / `report`
- `--fail-on {error,warning}` for CI gating

**Output:** console summary + optional `<prefix>_validation.csv`

---

### 🔲 M6 — Public API & PyPI Release (v1.0)

- Finalise `__init__.py`; mark internals with `_`
- Full type annotations and NumPy-style docstrings
- Publish to PyPI via GitHub Actions + `uv`

---

## Design Principles

- **One algebra walk** — all structural analysis from the rdflib algebra tree
- **Invalid queries are rejected** — no partial annotation, no silent degradation
- **Adapter pattern** — core logic never imports from `adapters/`
- **`uv` only** — no bare `pip install`
