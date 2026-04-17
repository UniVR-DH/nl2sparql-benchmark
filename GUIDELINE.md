# Guideline - Encoding NL2SPARQL Queries in LSQ-Compatible Turtle

Purpose: define a compact, reusable approach to model benchmark queries using summary metadata (LSQ + DCT) without storing the full SP syntax tree.

## Scope

- Input: natural-language question + SPARQL query text.
- Output: Turtle resource encoding query metadata, a structural summary node, and referenced schema/IRI terms.

## Structural Annotation of Query Operators

### Canonical Mapping from `text2sparql` Encoding

| Current concept | Legacy pattern | Guideline pattern |
|---|---|---|
| Query type | `shui:SparqlQuery` | `lsqv:Query` |
| Query text | `shui:queryText` | `lsqv:text` |
| Query form and operators | `shui:queryType`, custom feature nodes | `lsqv:hasStructuralFeatures` + `lsqv:usesFeature` (e.g., `lsqv:Select`, `lsqv:Distinct`, `lsqv:Having`) |
| Mentioned schema terms | custom `relatedClass` / `relatedProperty` | `dct:subject` + explicit typing (`rdfs:Class`, `rdf:Property`) |
| Triple patterns | implicit in query text only | summarize with `lsqv:tpCount` and `lsqv:usesFeature lsqv:TriplePattern` |
| Result cardinality | not present | `lsqv:resultCount` |


### Modeling Rules

1. Always type each query as `lsqv:Query`.
2. Keep the full SPARQL source in `lsqv:text`.
3. Use `dct:subject` for schema terms (e.g., `rdfs:Class`, `rdf:Property`) mentioned in the query.
4. Always attach one summary node via `lsqv:hasStructuralFeatures`.
5. Use `lsqv:usesFeature` and structural counters (`lsqv:projectVarCount`, `lsqv:tpCount`, optional `lsqv:bgpCount`) in the summary node.
6. Use `lsqv:resultCount` to indicate the number of results from the provided ground truth graph.
7. Use `dct:subject` only, and include all IRIs appearing in the query (classes, predicates, and any concrete resource/entity IRIs).


#### Blank Node Policy (Summary Encoding)

Use blank nodes for the summary node and related compact metadata nodes.

Must be blank nodes:

- object of `lsqv:hasStructuralFeatures` (the structural feature resource)
- optional helper summary nodes (if you introduce any)

Use named IRIs for:

- `lsqv:Query` resources (e.g., `:1`, `:30`)
- domain schema terms (e.g., `pv:Department`, `pv:memberOf`)

Example (preferred):

```turtle
:1 lsqv:hasStructuralFeatures _:sf1 .

_:sf1 a lsqv:StructuralFeatures ;
  lsqv:usesFeature lsqv:Select, lsqv:Distinct, lsqv:TriplePattern ;
  dct:subject <http://ld.company.org/prod-instances/empl-Karen.Brant%40company.org>, pv:memberOf, pv:Department .
```

Avoid:

```turtle
:1 lsqv:hasStructuralFeatures :sf1 .

:sf1 a lsqv:StructuralFeatures ;
  lsqv:usesFeature lsqv:Select, lsqv:Distinct, lsqv:TriplePattern .
```

### Practical Usage

#### Example 1

##### 1) Source SPARQL Query (from benchmark):

```sparql
PREFIX pv: <http://ld.company.org/prod-vocab/>
SELECT DISTINCT ?result
WHERE {
  <http://ld.company.org/prod-instances/empl-Karen.Brant%40company.org> pv:memberOf ?result .
  ?result a pv:Department .
}
```

##### 2) Legacy text2sparql TTL Encoding (Before)

```turtle
@prefix ns2: <http://purl.org/dc/terms/> .
@prefix shui: <https://vocab.eccenca.com/shui/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix pv: <http://ld.company.org/prod-vocab/> .

<https://text2sparql.aksw.org/2025/corporate/queries/1> a shui:SparqlQuery;
  rdfs:label "In which department is Ms. Brant?";
  ns2:modified "2026-01-31T07:52:01.482Z"^^xsd:dateTime;
  ns2:created "2026-01-31T07:52:01.481Z"^^xsd:dateTime;
  shui:queryText """PREFIX pv: <http://ld.company.org/prod-vocab/>
SELECT DISTINCT ?result
WHERE
{
  <http://ld.company.org/prod-instances/empl-Karen.Brant%40company.org> pv:memberOf ?result .
  ?result a pv:Department .
}
""";
  shui:queryType "SELECT";
  <https://text2sparql.aksw.org/2025/corporate/queries/feature> <https://text2sparql.aksw.org/2025/corporate/queries/features/SELECT>;
  <https://text2sparql.aksw.org/2025/corporate/queries/relatedProperty> pv:memberOf;
  <https://text2sparql.aksw.org/2025/corporate/queries/relatedClass> pv:Employee, pv:Department .
```

##### 3) LSQ-Compatible TTL Encoding (After)

```turtle
@prefix : <https://text2sparql.aksw.org/2025/corporate/queries/> .
@prefix lsqv: <http://lsq.aksw.org/vocab#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix pv: <http://ld.company.org/prod-vocab/> .

:1 a lsqv:Query ;
  rdfs:label "In which department is Ms. Brant?" ;
  dct:created "2026-01-31T07:52:01.481Z"^^xsd:dateTime ;
  dct:modified "2026-01-31T07:52:01.482Z"^^xsd:dateTime ;
  lsqv:text """PREFIX pv: <http://ld.company.org/prod-vocab/>
SELECT DISTINCT ?result
WHERE {
  <http://ld.company.org/prod-instances/empl-Karen.Brant%40company.org> pv:memberOf ?result .
  ?result a pv:Department .
}""" ;
  lsqv:hasStructuralFeatures _:sf1 ;
  lsqv:resultCount "1"^^xsd:integer ;
  dct:subject pv:memberOf, pv:Department,
  <http://ld.company.org/prod-instances/empl-Karen.Brant%40company.org> .

_:sf1 a lsqv:StructuralFeatures ;
  lsqv:usesFeature lsqv:Select, lsqv:Distinct, lsqv:TriplePattern ;
  lsqv:projectVarCount "1"^^xsd:integer ;
  lsqv:tpCount "2"^^xsd:integer ;
  lsqv:bgpCount "1"^^xsd:integer ;  
  dct:subject <http://ld.company.org/prod-instances/empl-Karen.Brant%40company.org>, pv:memberOf, pv:Department .
```

#### Example 2

##### 1) Source SPARQL Query (from benchmark):

```sparql
PREFIX prodi: <http://ld.company.org/prod-instances/>
PREFIX pv: <http://ld.company.org/prod-vocab/>

SELECT ?name (COUNT(?emp) AS ?numEmployees)
WHERE {
  ?dept a pv:Department ;
    pv:name ?name .
  ?emp a pv:Employee ;
    pv:memberOf ?dept .
}
GROUP BY ?dept ?name
HAVING (COUNT(?emp) > 5)
```

##### 2) Legacy text2sparql TTL Encoding (Before)

```turtle
<https://text2sparql.aksw.org/2025/corporate/queries/30> a shui:SparqlQuery;
  rdfs:label "Which department have more than 5 employees? I need their names and the number of employees.";
  ns2:modified "2026-01-31T07:52:01.497Z"^^xsd:dateTime;
  ns2:created "2026-01-31T07:52:01.497Z"^^xsd:dateTime;
  shui:queryText """PREFIX prodi: <http://ld.company.org/prod-instances/>
PREFIX pv: <http://ld.company.org/prod-vocab/>

SELECT ?name (COUNT(?emp) AS ?numEmployees)
WHERE {
  ?dept a pv:Department ;
    pv:name ?name .
  ?emp a pv:Employee ;
    pv:memberOf ?dept .
}
GROUP BY ?dept ?name
HAVING (COUNT(?emp) > 5)
""";
  shui:queryType "SELECT";
  <https://text2sparql.aksw.org/2025/corporate/queries/feature> <https://text2sparql.aksw.org/2025/corporate/queries/features/SELECT>,
    <https://text2sparql.aksw.org/2025/corporate/queries/features/COUNT>, <https://text2sparql.aksw.org/2025/corporate/queries/features/GROUP>,
    <https://text2sparql.aksw.org/2025/corporate/queries/features/HAVING>;
  <https://text2sparql.aksw.org/2025/corporate/queries/relatedProperty> pv:memberOf,
    pv:name;
  <https://text2sparql.aksw.org/2025/corporate/queries/relatedClass> pv:Employee, pv:Department .
```

##### 3) LSQ-Compatible TTL Encoding (After)

```turtle
@prefix : <https://text2sparql.aksw.org/2025/corporate/queries/> .
@prefix lsqv: <http://lsq.aksw.org/vocab#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix pv: <http://ld.company.org/prod-vocab/> .

:30 a lsqv:Query ;
  rdfs:label "Which department have more than 5 employees? I need their names and the number of employees." ;
  dct:created "2026-01-31T07:52:01.497Z"^^xsd:dateTime ;
  dct:modified "2026-01-31T07:52:01.497Z"^^xsd:dateTime ;
  lsqv:text """PREFIX prodi: <http://ld.company.org/prod-instances/>
PREFIX pv: <http://ld.company.org/prod-vocab/>

SELECT ?name (COUNT(?emp) AS ?numEmployees)
WHERE {
  ?dept a pv:Department ;
    pv:name ?name .
  ?emp a pv:Employee ;
    pv:memberOf ?dept .
}
GROUP BY ?dept ?name
HAVING (COUNT(?emp) > 5)
""" ;
  lsqv:hasStructuralFeatures _:sf30 ;
  dct:subject pv:Department, pv:Employee, pv:memberOf, pv:name .

_:sf30 a lsqv:StructuralFeatures ;
  lsqv:usesFeature lsqv:Select, lsqv:GroupBy, lsqv:Having, lsqv:TriplePattern, lsqv:Aggregators, lsqv:agg-count, lsqv:fn-gt ;
  lsqv:projectVarCount "2"^^xsd:integer ;
  lsqv:tpCount "4"^^xsd:integer ;
  lsqv:bgpCount "1"^^xsd:integer ;
  dct:subject pv:Department, pv:Employee, pv:memberOf, pv:name .
```

### Checklist for New Query Entries

- Add or verify required prefixes.
- Create one `lsqv:Query` resource.
- Set `rdfs:label`, `dct:created`, `dct:modified`, and `lsqv:text`.
- Add `lsqv:hasStructuralFeatures` and explicit `lsqv:usesFeature` values.
- Add `dct:subject` to capture classes, predicates, and any concrete resource/entity IRIs appearing in the query.
- Add `lsqv:resultCount` to capture the number of results when in the benchmark output.


## Semantic Annotation of Question Noise

A 2-Level Taxonomy of Failure Sources in NL → SPARQL Generation

---

# 1. Linguistic Form Layer (Surface Realization & Interpretation Issues)

* **Orthographic noise**

  * Spelling mistakes, typos, ASR errors, MT artifacts that corrupt tokens before parsing
  * *Example:* “emplyees with hgh salery”
  * *Example:* “averge revenu of compnies”


* **Syntactic distortion**

  * Ungrammatical or Ill-formed or non-canonical syntax affecting parsing or dependency structure
  * Includes fragment queries and scrambled constituent order
  * *Example:* “employees high sales?”
  * *Example:* “salary employees highest department which”
  * *Example:* “in Berlin departments managers list”


* **Lexical grounding mismatch**

  * Lexical variation requiring normalization prior to schema alignment: synonyms, paraphrases, and compositional phrases 
  * *Example:* “French poets” → poets with France as nationality/affiliation
  * *Example:* “pay” → salary
  * *Example:* “companies that make cars” → automotive manufacturers

* **Abbreviation ambiguity**

 * Underspecified shorthand requiring expansion or contextual disambiguation

  * *Example:* “GDP growth in EU”
  * *Example:* “avg temp in US states”

* **Discourse reference failure**

  * Ambiguous or unresolved coreference (pronouns, ellipsis, implicit referents)
  * *Example:* “Who is John's manager and what is his salary?”
  * *Example:* “Which companies acquired startups and where are they located?”



---

# 2. Semantic–Structural & Schema Alignment Layer (Meaning → RDF/SPARQL Mapping Issues)

* **Entity linking ambiguity**

  * Multiple candidate entities for the same surface form
  * *Example:* “Paris” → city vs mythology entity
  * *Example:* “John” → multiple people named John

* **Entity/attribute mismatch**

  * Concept exists in schema but requires transformation or derived computation
  * *Example:* “customer age” → derived from `birthDate`
  * *Example:* “company size” → number of employees
  * *Example:* “products with volume < 15 cm3” → computed from `length × width × height`
    

* **Implicit relation inference**

  * Missing explicit predicates (join gaps) requiring multi-hop traversal or inferred joins between entities not directly connected in the query
  * *Example:* “managers in departments in Berlin”, no direct `manager → city` relation;
  * *Example:* “employees with skills in data science”, no direct `employee → skill` relation, requires join through `hasSkill` and `skillCategory`
  * *Example:* “People married in 2020” → requires join between `hasMarriage`,`spouse` and `hasDate` attributes

* **Aggregation underspecification**

  * * Missing or ambiguous aggregation intent or ranking criteria generating ambiguity in constructs (COUNT, SUM, ORDER BY, LIMIT)
  * *Example:* “top selling products”
  * *Example:* “number of employees in each department” (COUNT vs GROUP BY)


* **Logical form ambiguity**

  * Ambiguity in quantification (e.g., for all vs exist), scope of constraints, and logical composition of clauses (AND/OR/NOT/OPTIONAL)
  * *Example:* “departments where employees earn more than 50k”  
      → all employees vs at least one employee

  * *Example:* “average salary of employees in hardware departments” → average within each department vs average across all departments 

  * *Example:* “number of employees in sales or in marketing who are managers”  
      → `(sales OR marketing) AND manager` vs `sales OR (marketing AND manager)`
  
* **Vague predicate grounding**

  * Filters or attributes are expressed without explicit measurable thresholds or formal grounding in the schema
  * *Example:* “young employees”
  * *Example:* “high salary”
  * *Example:* “recent publications”

* **Schema coverage mismatch**

  * Required concept not present or not modeled in the ontology
  * *Example:* “employee satisfaction score” not in schema
  * *Example:* “manager nationality” only current address is modeled but no nationality modeled in schema 




