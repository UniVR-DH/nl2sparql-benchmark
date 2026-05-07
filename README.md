# NL2SPARQL Benchmark for Grounded NL2SPARQL over Knowledge Graphs

This repository consolidates benchmark assets for grounded Natural Language to SPARQL (NL2SPARQL) workflows.

The project is expected to evolve over time. Structure can grow, but a few conventions are stable and should remain coherent.

## Repository Layout

```text
.
├── .external/              # Vendored external vocabularies
│   ├── lsq-vocab.ttl        
│   ├── oa.ttl
│   ├── qanary.ttl
│   ├── sp.ttl
│   └── sparql-service-description.ttl
├── .github/                # GitHub-specific files
├── GUIDELINE.md            # Canonical encoding guidance for test data
├── LICENSE
├── README.md
├── scripts/
│   ├── sparql-annotator/   # Stub — moved to github.com/UniVR-DH/sparql-annotator
│   └── ...
└── graphs/                 # Core benchmark graph assets
    ├── ck25/
    │   ├── ck25-*.graph
    │   ├── ck25-*.ttl
    │   ├── ck25.graph
    │   └── ck25.ttl
    ├── ck25-h/             # CK25 variant with hashed IRIs
    │   └── ck25-h-*.ttl
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

Derived from the [GPT-KB dataset](https://huggingface.co/datasets/Knowledge-aware-AI/GPTKB_v1.5/resolve/main/gptkb_v1.5.3.ttl), adapted for this benchmark.

To regenerate the GPTKB derived artifacts from a source TTL/NT, run the extraction script. Replace the `VERSION` and `DOWNLOAD_URL` values below with the appropriate release and download location for the GPTKB release you are using.

```bash
cd graphs/gptkb
VERSION="gptkb_v1.5.3"
# Direct download URL for the GPTKB release TTL
DOWNLOAD_URL="https://huggingface.co/datasets/Knowledge-aware-AI/GPTKB_v1.5/resolve/main/gptkb_v1.5.3.ttl"

# Download the source TTL
curl -L -o "${VERSION}.ttl" "${DOWNLOAD_URL}"

# Convert TTL → N-Triples using Apache Jena `riot` (via Docker)
docker run --rm --platform=linux/amd64 -v "$PWD":/data stain/jena:5.1.0 bash -lc "riot --output=ntriples --syntax=turtle /data/${VERSION}.ttl > /data/${VERSION}.nt"

# Create the types file (instanceOf → rdf:type) if not provided
awk '$2 == "<https://gptkb.org/prop/instanceOf>"' "${VERSION}.nt" \
  | sed 's|<https://gptkb.org/prop/instanceOf>|<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>|' \
  > "${VERSION}_types.nt"

# Run the extractor (this writes outputs into graphs/gptkb)
bash ../../scripts/extract.sh "${VERSION}.nt" "${VERSION}_types.nt" "./" "${VERSION}.ttl" "${VERSION}"
```

> Notes:
> The extractor produces NT and TTL vocab/instances files plus predicate lists. The predicate lists are generated for inspection and manual review.
> Companion `.graph` files are written with the named graph IRI `http://gptkb.org/` for both `*-vocab.graph` and `*-instances.graph`.
> Existing `.graph` files are not overwritten, so manually curated graph IRIs are preserved across re-runs.



### `.external/`

Vendored vocabulary resources that the benchmark relies on:

- `lsq-vocab.ttl` the Linked SPARQL Queries Vocabulary, used for annotating SPARQL query features and patterns in the benchmark.
- `oa.ttl` the W3C Open Annotation (Web Annotation) vocabulary, used for attaching diagnostic issue annotations to questions.
- `qanary.ttl` the Qanary vocabulary, used for QA answer types and annotation question patterns.
- `sp.ttl` the SP vocabulary, used for representing SPARQL query structures.
- `sparql-service-description.ttl` the SPARQL Service Description vocabulary, used for describing SPARQL endpoints.

### Modeling Guidance

Use `GUIDELINE.md` as the source of truth for query encoding patterns, LSQ feature usage, and compact summary-profile conventions.

---

## QA Types Ontology (`graphs/qa-types.ttl`)

The benchmark uses a dedicated ontology for question typing in `graphs/qa-types.ttl` with prefix `qat:` (`https://w3id.org/qatypes#`).

### Purpose

* Provide a machine-readable taxonomy of NL question types.
* Link each type to expected answer shape (`qat:hasAnswerType` with Qanary `qa:AnswerType` classes).
* Constrain question types via LSQ structural features using:

  * `lsqv:hasStructuralFeatures`
  * `lsqv:usesFeature`

* Complement typing with an orthogonal diagnostic layer for linguistic and semantic issues aligned with Qanary/Open Annotation.

### Core Classes  for Question Types

```text
qat:QuestionType
├── qat:Confirmation
└── qat:Factoid
    ├── qat:Aggregation
    │   ├── qat:Comparative
    │   └── qat:AggregateEnumeration
    ├── qat:Enumeration
    │   ├── qat:Comparative (overlaps via subclasses)
    │   └── qat:AggregateEnumeration (overlaps via subclasses)
    ├── qat:RankedListing
    │   └── qat:LimitedRankedListing
    ├── qat:Sampling
    └── qat:CounterFactualIdentification
```

#### Notes on Modeling

* **Multiple inheritance**

  * `qat:Comparative` ⊑ `qat:Aggregation` ⊓ `qat:Enumeration`
  * `qat:AggregateEnumeration` ⊑ `qat:Aggregation` ⊓ `qat:Enumeration`

* **Answer types**

  * Modeled as classes (subclasses of `qa:AnswerType`) :

    * `qat:BooleanAnswer`
    * `qat:SingleEntityAnswer`
    * `qat:EntityListAnswer`
    * `qat:SampledListAnswer`
    * `qat:RankedListAnswer`
    * `qat:ScalarAnswer`
    * Hybrid types:

      * `qat:RankedEntityAnswer` ⊑ `qat:ScalarAnswer` ⊓ `qat:EntityListAnswer`
      * `qat:AggregateListAnswer` ⊑ `qat:ScalarAnswer` ⊓ `qat:EntityListAnswer`

* **Structural grounding**

  * Question types are not LSQ feature instances; instead they are constrained via nested OWL restrictions over `lsqv:hasStructuralFeatures / lsqv:usesFeature`.

* **Disjointness**

  * `qat:Factoid` ⊥ `qat:Confirmation`
  * No global pairwise disjointness: overlap is intentional where semantics intersect (e.g., aggregation + enumeration).

* **Diagnostic layer (orthogonal)**

  * `qat:LinguisticIssue` and `qat:SemanticIssue` (disjoint)
  * Subclasses capture error categories (e.g., `qat:EntityLinkingAmbiguity`, `qat:AggregationUnderspecification`, etc.)
  * Designed for annotation, not classification of question type.

### Core Classes for the Diagnostic Layer

In addition to question typing, the ontology defines an **orthogonal diagnostic layer** for capturing errors and ambiguities in natural language questions. These are modeled as subclasses of `qa:AnnotationQuestion` and are intended to be attached to questions via the Qanary/Open Annotation pattern (`oa:hasTarget` / `oa:hasBody`).

This layer does **not** affect the question type classification; it complements it by explaining *why* a question may be difficult to interpret or translate into SPARQL.

```text
qat:LinguisticIssue
├── qat:OrthographicNoise
├── qat:SyntacticDistortion
├── qat:LexicalGroundingMismatch
├── qat:AbbreviationAmbiguity
└── qat:DiscourseReferenceFailure

qat:SemanticIssue
├── qat:EntityLinkingAmbiguity
├── qat:EntityAttributeMismatch
├── qat:ImplicitRelationInference
├── qat:AggregationUnderspecification
├── qat:LogicalFormAmbiguity
├── qat:VaguePredicateGrounding
└── qat:SchemaCoverageMismatch
```

####  Notes on Modeling

* **Disjointness**

  * `qat:LinguisticIssue` ⊥ `qat:SemanticIssue`

* **Linguistic issues**

  * Capture problems at the surface form level (spelling, syntax, lexical choice, discourse).
  * Typically arise *before* schema grounding.

* **Semantic issues**

  * Capture mismatches between intended meaning and the underlying RDF/schema/query structure.
  * Often manifest during entity linking, relation construction, or query formulation.

* **Usage pattern**

  * Instances annotate a question independently of its `qat:QuestionType`.
  * Multiple issues may be attached to the same question.


---

## Question Classification

Query classification is handled by the `sparql-annotator` CLI, now maintained in its own repository:
**[https://github.com/UniVR-DH/sparql-annotator](https://github.com/UniVR-DH/sparql-annotator)**

For a related workflow focused on annotating queries with the `qa-types` ontology, see [nl-to-sparql_query_annotation](https://github.com/niccolomarastoni/nl-to-sparql_query_annotation).

Quick start:

```bash
# Install from the standalone repo
git clone git@github.com:UniVR-DH/sparql-annotator.git
cd sparql-annotator
uv venv .venv && uv pip install -e .
source .venv/bin/activate

# Classify queries (run from nl2s-bench root)
python -m sparql_annotator.cli classify \
    --query-file graphs/ck25/ck25-queries.ttl \
    --ontology   graphs/qa-types.ttl \
    --output     .temp/classified.ttl \
    --log-file   .temp/classification.log

# List ambiguous queries only
python -m sparql_annotator.cli classify \
    --query-file graphs/ck25/ck25-queries.ttl \
    --ontology   graphs/qa-types.ttl \
    --ambiguous-only

# List queries with antipatterns only
python -m sparql_annotator.cli classify \
    --query-file graphs/ck25/ck25-queries.ttl \
    --ontology   graphs/qa-types.ttl \
    --ap-only
```

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
uv run python -m sparql_annotator.cli --help
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

