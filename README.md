# NL2SPARQL Benchmark for Grounded NL2SPARQL over Knowledge Graphs

This repository consolidates benchmark assets for grounded Natural Language to SPARQL (NL2SPARQL) workflows.

The project is expected to evolve over time. Structure can grow, but a few conventions are stable and should remain coherent.

## Repository Layout

```text
.
├── .external/              # Vendored external vocabularies
│   ├── lsq-vocab.ttl        
│   └── ...
├── .github/                # GitHub-specific files)
├── GUIDELINE.md            # Canonical encoding guidance for test data
├── LICENSE
├── README.md
└── graphs/                 # Core benchmark graph assets
    ├── ck25/
    │   ├── ck25-*.graph
    │   ├── ck25-*.ttl
    │   ├── ck25.graph
    │   └── ck25.ttl
    └── gptkb/
        ├── gptkb-*.graph
        ├── gptkb-*.ttl
        ├── gptkb.graph
        └── gptkb.ttl
```

Graph data is presented as `.ttl` and `.graph` artifacts in the `graphs/` directory.


## Directory Notes

### `graphs/ck25/`

Derived from the [CK25 dataset](https://github.com/eccenca/ck25-dataset), adapted for this benchmark.

- `*-data-*.ttl`: domain data serializations (instances, shapes, vocab).
- `*-queries.ttl`: NL question/SPARQL query pairs with query metadata.
- `*-examples.ttl`: well-known examples queries accessible to an agent.
- `*-void.ttl`: dataset metadata descriptions.
- `*.graph`: companion graph artifacts for corresponding Turtle resources.

### `graphs/gptkb/`

Derived from the [GPT-KB dataset](), adapted for this benchmark.

### `.external/`

Vendored vocabulary resources that the benchmark relies on:

- `lsq-vocab.ttl` the Linked SPARQL Queries Vocabulary, used for annotating SPARQL query features and patterns in the benchmark.
- `sp.ttl` the SP vocabulary, used for representing SPARQL query structures.
- `sparql-service-description.ttl` the SPARQL Service Description vocabulary, used for describing SPARQL endpoints.

---

## Modeling Guidance
Use `GUIDELINE.md` as the source of truth for query encoding patterns, LSQ feature usage, and compact summary-profile conventions.

## QA Types Ontology (`graphs/qa-types.ttl`)
The benchmark uses a dedicated ontology for question typing in `graphs/qa-types.ttl` with prefix `qat:` (`https://w3id.org/univr-qa/qatypes#`).

### Purpose
- Provide a machine-readable taxonomy of NL question types.
- Link each type to expected answer shape (`qat:hasAnswerType` with Qanary `qa:AnswerType`).
- Define structural constraints using LSQ features through:
  - `lsqv:hasStructuralFeatures`
  - `lsqv:usesFeature`

### Core Classes

```text
qat:QuestionType
├── qat:Factoid
│   └── qat:RankedList
├── qat:AggregateFactoid
|   ├── qat:AggregateList
│   └── qat:Comparative
├── qat:EnumerationQuestion
│   └── qat:AggregateList (also subclass of qat:AggregateFactoid)
└── qat:Confirmation
```

Note: `qat:AggregateList` has multiple inheritance — it is a subclass of both
`qat:EnumerationQuestion` and `qat:AggregateFactoid`. It appears under
`EnumerationQuestion` in the tree above because the enumeration semantics
(returning a set of groups) is its primary classification; the aggregation
semantics is inherited to express the computational requirement.

> **Disjointness and Ambiguity**
> The ontology encodes selected `owl:disjointWith` relations for incompatible sibling
> classes. Not all classes are pairwise disjoint by design — `qat:AggregateList`
> intentionally sits at the intersection of enumeration and aggregation semantics
> through multiple inheritance. The classifier handles this by preferring the most
> specific matching type in the hierarchy.

---

## Question Classification Script

The script `scripts/classify_questions.py` classifies LSQ-annotated SPARQL queries using the ontology rules in `graphs/qa-types.ttl`.

### Usage

The classifier does three things:

1. reads ontology constraints from `graphs/qa-types.ttl`,
2. validates and classifies queries from `*-queries.ttl`,
3. writes typed output and a validation log.

Run:

```bash
python scripts/classify_questions.py \
    --query-file graphs/ck25/ck25-queries.ttl \
    --ontology graphs/qa-types.ttl \
    --output .temp/classified_queries.ttl \
    --log-file .temp/classification.log
```

Useful flags:

- `--verbose` for per-query debug details.
- `--debug` to print extracted ontology rules.

Outputs:

- `--output`: Turtle with inferred `rdf:type qat:*` assertions.
- `--log-file`: syntax errors, classification warnings/errors, and final validation status.

## Development
 
### Create the virtual environment and install dependencies
 
```bash
# Create venv and install all dependencies in one step
uv sync
 
# For development dependencies (pytest, ruff, etc.)
uv sync --dev
```
 
`uv sync` reads `pyproject.toml`, creates a `.venv` in the project root, and installs everything. No manual `pip install` needed.
 
### Activate the virtual environment
 
```bash
# Linux / macOS
source .venv/bin/activate
 
# Windows
.venv\Scripts\activate
```
 
Or prefix any command with `uv run` to run it inside the venv without activating:
 
```bash
uv run python classify_questions.py --help
```


```bash
# Run tests
uv run pytest
 
# Run tests with coverage
uv run pytest --cov
 
# Lint and format
uv run ruff check .
uv run ruff format .
```
 




## Common Vocabularies Used and Prefixes

- RDF 1.1 (`rdf:`)
- RDFS (`rdfs:`)
- XML Schema Datatypes (`xsd:`)
- Dublin Core Terms (`dct:`)
- SPARQL Service Description (`sd:`)
- Linked SPARQL Queries Vocabulary (`lsqv:`)
- SP vocabulary (`sp:`)

