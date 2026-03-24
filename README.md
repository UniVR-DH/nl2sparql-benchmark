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

## Modeling Guidance

Use `GUIDELINE.md` as the source of truth for query encoding patterns, LSQ feature usage, and compact summary-profile conventions.

## Common Vocabularies Used and Prefixes

- RDF 1.1 (`rdf:`)
- RDFS (`rdfs:`)
- XML Schema Datatypes (`xsd:`)
- Dublin Core Terms (`dct:`)
- SPARQL Service Description (`sd:`)
- Linked SPARQL Queries Vocabulary (`lsqv:`)
- SP vocabulary (`sp:`)

