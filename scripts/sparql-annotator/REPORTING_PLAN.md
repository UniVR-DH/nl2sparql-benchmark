# Reporting Command Implementation Plan

## Overview

Add a `report` CLI command that analyzes a dataset of queries and produces multiple CSV files with different analytical views.

## Command Interface

```bash
sparql-annotator report \
  --query-file graphs/ck25/ck25-queries.ttl \
  --ontology graphs/qa-types.ttl \
  --output-dir .temp/reports \
  --prefix ck25
```

**Arguments:**
- `--query-file`: Input file (TTL, CSV, or JSON)
- `--ontology`: QA types ontology (TTL)
- `--output-dir`: Directory for output CSV files (created if missing)
- `--prefix`: Prefix for output filenames (default: "report")

## Output Files

### 1. `<prefix>_features.csv`
LSQ feature presence matrix.

**Columns:**
- `query_id`: Query identifier
- `Optional`: 1/0
- `Filter`: 1/0
- `Union`: 1/0
- `Distinct`: 1/0
- `OrderBy`: 1/0
- `GroupBy`: 1/0
- `Having`: 1/0
- `Limit`: 1/0
- `Offset`: 1/0
- `Bind`: 1/0
- `Values`: 1/0
- `Subquery`: 1/0
- `PropertyPath`: 1/0
- ... (all LSQ features)

### 2. `<prefix>_operators.csv`
Operator usage matrix.

**Columns:**
- `query_id`: Query identifier
- `query_form`: SELECT/ASK/CONSTRUCT/DESCRIBE
- `Optional`: 1/0
- `Union`: 1/0
- `Filter`: 1/0
- `FilterExists`: 1/0
- `FilterNotExists`: 1/0
- `Bind`: 1/0
- `Values`: 1/0
- `Minus`: 1/0
- `Graph`: 1/0
- `Service`: 1/0
- `Distinct`: 1/0
- `Reduced`: 1/0
- `OrderBy`: 1/0
- `GroupBy`: 1/0
- `Having`: 1/0
- `Limit`: 1/0
- `Offset`: 1/0
- `Subquery`: 1/0
- `PropertyPath`: 1/0
- `Count`: 1/0
- `Sum`: 1/0
- `Avg`: 1/0
- `Min`: 1/0
- `Max`: 1/0
- `filter_functions`: "REGEX,STR,LANG" (comma-separated list)

### 3. `<prefix>_question_types.csv`
Question type classifications.

**Columns:**
- `query_id`: Query identifier
- `question_types`: "Factoid,Enumeration" (comma-separated, sorted)
- `answer_types`: "EntityListAnswer" (comma-separated, sorted)
- `classification_status`: "classified" / "ambiguous" / "unclassified"
- `ambiguity_reason`: (if ambiguous)

### 4. `<prefix>_antipatterns.csv`
Antipattern detection results.

**Columns:**
- `query_id`: Query identifier
- `antipatterns`: "AP01,AP09" (comma-separated, sorted)
- `AP01`: 1/0
- `AP02`: 1/0
- `AP03`: 1/0
- `AP04`: 1/0
- `AP05`: 1/0
- `AP06`: 1/0
- `AP07`: 1/0
- `AP08`: 1/0
- `AP09`: 1/0
- `antipattern_messages`: JSON array of {code, message, hint}

### 5. `<prefix>_metrics.csv`
Structural metrics.

**Columns:**
- `query_id`: Query identifier
- `bgp_count`: Number of BGPs
- `triple_count`: Number of triple patterns
- `join_vertex_count`: Number of join vertices
- `mean_join_vertex_degree`: Average degree
- `projected_var_count`: Number of projected variables

### 6. `<prefix>_summary.csv`
Aggregate statistics across the dataset.

**Columns:**
- `metric`: Metric name
- `value`: Metric value

**Rows:**
- `total_queries`: Total number of queries
- `valid_queries`: Queries that parsed successfully
- `invalid_queries`: Queries that failed to parse
- `classified_queries`: Queries with at least one question type
- `unclassified_queries`: Queries with no question type
- `ambiguous_queries`: Queries with conflicting types
- `queries_with_antipatterns`: Queries with at least one antipattern
- `most_common_feature`: Most common LSQ feature
- `most_common_operator`: Most common operator
- `most_common_question_type`: Most common question type
- `most_common_antipattern`: Most common antipattern
- `avg_bgp_count`: Average BGPs per query
- `avg_triple_count`: Average triples per query
- `avg_projected_vars`: Average projected variables per query

## Implementation Structure

### New Module: `reporter.py`

```python
@dataclass
class ReportRow:
    """Base class for report rows."""
    query_id: str

@dataclass
class FeatureRow(ReportRow):
    features: Dict[str, bool]

@dataclass
class OperatorRow(ReportRow):
    query_form: str
    operators: Dict[str, bool]
    filter_functions: List[str]

@dataclass
class QuestionTypeRow(ReportRow):
    question_types: List[str]
    answer_types: List[str]
    status: str
    ambiguity_reason: Optional[str]

@dataclass
class AntipatternRow(ReportRow):
    antipatterns: List[str]
    antipattern_flags: Dict[str, bool]
    messages: List[Dict[str, str]]

@dataclass
class MetricRow(ReportRow):
    bgp_count: int
    triple_count: int
    join_vertex_count: int
    mean_join_vertex_degree: float
    projected_var_count: int

class ReportGenerator:
    def __init__(self, ontology_path: str):
        self.classifier = QuestionTypeClassifier(ontology_path)
    
    def generate_reports(
        self,
        query_file: str,
        output_dir: str,
        prefix: str = "report"
    ) -> None:
        """Generate all CSV reports."""
        # 1. Load and annotate queries
        # 2. Classify question types
        # 3. Detect antipatterns
        # 4. Generate each CSV file
        # 5. Generate summary
```

### CLI Integration in `cli.py`

```python
@cli.command()
@click.option("--query-file", required=True, type=click.Path(exists=True))
@click.option("--ontology", required=True, type=click.Path(exists=True))
@click.option("--output-dir", required=True, type=click.Path())
@click.option("--prefix", default="report")
def report(query_file: str, ontology: str, output_dir: str, prefix: str):
    """Generate CSV reports for a query dataset."""
    generator = ReportGenerator(ontology)
    generator.generate_reports(query_file, output_dir, prefix)
```

## Implementation Steps

1. Create `reporter.py` with `ReportGenerator` class
2. Implement data collection (reuse `Annotator`, `QuestionTypeClassifier`, `detect_antipatterns`)
3. Implement CSV writers for each report type
4. Implement summary statistics computation
5. Add CLI command in `cli.py`
6. Add tests in `tests/test_reporter.py`
7. Update README with `report` command documentation

## Testing Strategy

- Test with small synthetic dataset (5-10 queries)
- Test with CK25 dataset subset
- Verify CSV format correctness
- Verify summary statistics accuracy
- Test error handling (invalid queries, missing files)
