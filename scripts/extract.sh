#!/bin/bash
set -euo pipefail

# GPTKB splitter - uses Virtuoso for metadata and two local inputs for splitting.
# Usage:
#   ./scripts/extract.sh [raw.nt] [output_dir]
#   ./scripts/extract.sh [raw.nt] [types.nt] [output_dir]

INPUT="${1:-gptkb_v1.5.3.nt}"

if [ "$#" -eq 2 ]; then
  TYPES_INPUT="gptkb_v1.5.3_types.nt"
  OUTPUT_DIR="$2"
else
  TYPES_INPUT="${2:-gptkb_v1.5.3_types.nt}"
  OUTPUT_DIR="${3:-graphs/gptkb}"
fi

WORKDIR="${OUTPUT_DIR}/metadata"

SPARQL_ENDPOINT="http://157.27.26.146:8890/sparql"
GRAPH="https://www.gptkb.org/"

INSTANCEOF="<https://gptkb.org/prop/instanceOf>"
RDF_TYPE="<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
RDFS_SUBCLASSOF="<http://www.w3.org/2000/01/rdf-schema#subClassOf>"
RDFS_LABEL="<http://www.w3.org/2000/01/rdf-schema#label>"
RDFS_DOMAIN="<http://www.w3.org/2000/01/rdf-schema#domain>"
RDFS_RANGE="<http://www.w3.org/2000/01/rdf-schema#range>"
OWL_CLASS="<http://www.w3.org/2002/07/owl#Class>"
OWL_OBJECT_PROPERTY="<http://www.w3.org/2002/07/owl#ObjectProperty>"
OWL_DATATYPE_PROPERTY="<http://www.w3.org/2002/07/owl#DatatypeProperty>"
OWL_ANNOTATION_PROPERTY="<http://www.w3.org/2002/07/owl#AnnotationProperty>"
OWL_ONTOLOGY="<http://www.w3.org/2002/07/owl#Ontology>"
RDFS_CLASS="<http://www.w3.org/2000/01/rdf-schema#Class>"
RDF_PROPERTY="<http://www.w3.org/1999/02/22-rdf-syntax-ns#Property>"

PREFIX="https://gptkb.org/entity/"
PROP_PREFIX="https://gptkb.org/prop/"

VOCAB_OUT="${OUTPUT_DIR}/gptkb-data-vocab.ttl"
INST_OUT="${OUTPUT_DIR}/gptkb-data-instances.ttl"

CLASS_IRIS_CSV="${WORKDIR}/classes_iris.csv"
PREDICATES_IRIS_CSV="${WORKDIR}/predicates_iris.csv"
CLASS_DEFINITIONS_CSV="${WORKDIR}/class_definitions.csv"
TOP_PREDICATES_CSV="${WORKDIR}/top_predicates.csv"

PREFIX_HEADER="@prefix gptkb: <${PREFIX}> .
@prefix gptkbp: <${PROP_PREFIX}> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> ."

if [ ! -f "$INPUT" ]; then
  echo "Input file not found: $INPUT"
  exit 1
fi

if [ ! -f "$TYPES_INPUT" ]; then
  echo "Types file not found: $TYPES_INPUT"
  exit 1
fi

mkdir -p "$OUTPUT_DIR" "$WORKDIR"

sparql_query() {
  local query="$1"
  local format="$2"
  local out_file="$3"
  curl -sS "$SPARQL_ENDPOINT" \
    --data-urlencode "query=$query" \
    --data-urlencode "default-graph-uri=$GRAPH" \
    --data "format=$format" > "$out_file"
}

echo "=========================================="
echo "GPTKB Splitter"
echo "=========================================="
echo "Input:           $INPUT"
echo "Types input:     $TYPES_INPUT"
echo "SPARQL endpoint: $SPARQL_ENDPOINT"
echo "Output dir:      $OUTPUT_DIR"

echo ""
echo "Step 1: Querying class IRIs from Virtuoso..."
sparql_query "
  SELECT DISTINCT ?class WHERE {
    ?instance ${INSTANCEOF} ?class .
    FILTER(isIRI(?class))
  }
" "text/csv" "$CLASS_IRIS_CSV"
echo "  Class IRIs: $(tail -n +2 "$CLASS_IRIS_CSV" | wc -l)"

echo ""
echo "Step 2: Querying predicate IRIs from Virtuoso..."
sparql_query "
  SELECT DISTINCT ?p WHERE {
    ?s ?p ?o .
    FILTER(isIRI(?p))
  }
" "text/csv" "$PREDICATES_IRIS_CSV"
echo "  Predicate IRIs: $(tail -n +2 "$PREDICATES_IRIS_CSV" | wc -l)"

echo ""
echo "Step 3: Querying class definitions from Virtuoso..."
sparql_query "
  PREFIX gptkbp: <${PROP_PREFIX}>
  PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
  SELECT DISTINCT ?class ?label WHERE {
    ?instance gptkbp:instanceOf ?class .
    OPTIONAL { ?class rdfs:label ?label }
    FILTER(isIRI(?class))
  }
" "text/csv" "$CLASS_DEFINITIONS_CSV"
echo "  Class definitions: $(tail -n +2 "$CLASS_DEFINITIONS_CSV" | wc -l)"

echo ""
echo "Step 4: Querying top predicates from Virtuoso..."
sparql_query "
  SELECT ?p (COUNT(*) AS ?count) WHERE {
    ?s ?p ?o .
    FILTER(?p != <https://gptkb.org/prop/instanceOf>)
  }
  GROUP BY ?p
  HAVING (COUNT(*) > 1000)
  ORDER BY DESC(?count)
" "text/csv" "$TOP_PREDICATES_CSV"
echo "  Top predicates: $(tail -n +2 "$TOP_PREDICATES_CSV" | wc -l)"

echo ""
echo "Step 5: Discovering classes and properties from local files..."
rm -f "$WORKDIR/classes_raw.txt" "$WORKDIR/properties_raw.txt" "$WORKDIR/instances_raw.txt"

awk \
  -v rdfType="$RDF_TYPE" \
  -v rdfsDomain="$RDFS_DOMAIN" \
  -v rdfsRange="$RDFS_RANGE" \
  -v owlClass="$OWL_CLASS" \
  -v rdfsClass="$RDFS_CLASS" \
  -v rdfProperty="$RDF_PROPERTY" \
  -v owlObjProp="$OWL_OBJECT_PROPERTY" \
  -v owlDataProp="$OWL_DATATYPE_PROPERTY" \
  -v owlAnnProp="$OWL_ANNOTATION_PROPERTY" \
  -v owlOntology="$OWL_ONTOLOGY" \
  -v instances_file="$WORKDIR/instances_raw.txt" \
  -v classes_file="$WORKDIR/classes_raw.txt" '
  {
    gsub(/\r/, "", $0)
    s = $1
    p = $2
    o = $3

    if (p == rdfType) {
      print s >> instances_file
      print o >> classes_file
    }
    if (p == rdfType && (o == owlClass || o == rdfsClass || o == owlOntology)) {
      print s >> classes_file
    }
  }
' "$TYPES_INPUT"

awk \
  -v rdfType="$RDF_TYPE" \
  -v rdfsDomain="$RDFS_DOMAIN" \
  -v rdfsRange="$RDFS_RANGE" \
  -v rdfProperty="$RDF_PROPERTY" \
  -v owlObjProp="$OWL_OBJECT_PROPERTY" \
  -v owlDataProp="$OWL_DATATYPE_PROPERTY" \
  -v owlAnnProp="$OWL_ANNOTATION_PROPERTY" \
  -v properties_file="$WORKDIR/properties_raw.txt" '
  {
    p = $2
    o = $3

    if (p == rdfType && (o == rdfProperty || o == owlObjProp || o == owlDataProp || o == owlAnnProp)) {
      print $1 >> properties_file
    }
    if (p == rdfsDomain || p == rdfsRange) {
      print $1 >> properties_file
    }
  }
' "$INPUT"

# If local schema declarations are sparse, seed property IRIs from endpoint stats.
tail -n +2 "$TOP_PREDICATES_CSV" | sed 's/"//g' | awk -F',' '{
  if ($1 ~ /^https?:\/\//) {
    printf "<%s>\n", $1
  }
}' >> "$WORKDIR/properties_raw.txt"

sort -u "$WORKDIR/classes_raw.txt" > "$WORKDIR/classes.txt" 2>/dev/null || :
sort -u "$WORKDIR/properties_raw.txt" > "$WORKDIR/properties.txt" 2>/dev/null || :
sort -u "$WORKDIR/instances_raw.txt" > "$WORKDIR/instances.txt" 2>/dev/null || :

echo "  Classes:    $(wc -l < "$WORKDIR/classes.txt" 2>/dev/null || echo 0)"
echo "  Properties: $(wc -l < "$WORKDIR/properties.txt" 2>/dev/null || echo 0)"
echo "  Instances:  $(wc -l < "$WORKDIR/instances.txt" 2>/dev/null || echo 0)"

echo ""
echo "Step 6: Splitting triples into vocab and instances..."
echo "  This step can take several minutes on 100M+ triples."
rm -f "$WORKDIR/vocab_raw.nt" "$WORKDIR/inst_raw.nt"

awk \
  -v classesFile="$WORKDIR/classes.txt" \
  -v propertiesFile="$WORKDIR/properties.txt" \
  -v vocabOut="$WORKDIR/vocab_raw.nt" \
  -v instOut="$WORKDIR/inst_raw.nt" \
  -v rdfType="$RDF_TYPE" \
  -v rdfsSubClassOf="$RDFS_SUBCLASSOF" \
  -v rdfsLabel="$RDFS_LABEL" \
  -v rdfsDomain="$RDFS_DOMAIN" \
  -v rdfsRange="$RDFS_RANGE" '
  function is_schema_pred(p) {
    return (p == rdfType || p == rdfsSubClassOf || p == rdfsLabel || p == rdfsDomain || p == rdfsRange)
  }

  BEGIN {
    while ((getline c < classesFile) > 0) classes[c] = 1
    close(classesFile)
    while ((getline p < propertiesFile) > 0) properties[p] = 1
    close(propertiesFile)
  }

  {
    s = $1
    p = $2
    o = $3
    is_vocab = 0

    if (is_schema_pred(p)) is_vocab = 1
    if (p == rdfType && (o in classes)) is_vocab = 1
    if (s in classes || s in properties) is_vocab = 1

    if (is_vocab) print >> vocabOut
    else print >> instOut
  }
' "$INPUT"

echo "  Vocab triples:    $(wc -l < "$WORKDIR/vocab_raw.nt")"
echo "  Instance triples:  $(wc -l < "$WORKDIR/inst_raw.nt")"

echo ""
echo "Step 7: Generating vocab TTL..."
(
  echo "$PREFIX_HEADER"
  echo ""
  echo "# Ontology"
  echo "<${PREFIX}>"
  echo "  a owl:Ontology ;"
  echo "  rdfs:label \"GPTKB Knowledge Graph Vocabulary\"@en ."
  echo ""
  echo "# Classes"
  tail -n +2 "$CLASS_DEFINITIONS_CSV" | sed 's/"//g' | awk -F',' -v pp="$PREFIX" '{
    class=$1
    label=$2
    sub(pp, "gptkb:", class)
    if (label != "") {
      printf "%s\n  a owl:Class ;\n  rdfs:label \"%s\"@en .\n\n", class, label
    } else {
      split(class, parts, ":")
      printf "%s\n  a owl:Class ;\n  rdfs:label \"%s\"@en .\n\n", class, parts[2]
    }
  }'
  echo "# Properties"
  tail -n +2 "$TOP_PREDICATES_CSV" | sed 's/"//g' | awk -F',' -v pp="$PROP_PREFIX" '{
    pred=$1
    sub(pp, "gptkbp:", pred)
    if (pred !~ /rdf-syntax-ns|rdf-schema/) {
      printf "%s\n  a owl:ObjectProperty .\n\n", pred
    }
  }'
) > "$VOCAB_OUT"
echo "  Created $VOCAB_OUT: $(wc -l < "$VOCAB_OUT") lines"

echo ""
echo "Step 8: Converting instances to TTL..."
(
  echo "$PREFIX_HEADER"
  echo ""
  awk -v ep="$PREFIX" -v pp="$PROP_PREFIX" '{
    gsub(/\r/, "", $0)
    if ($NF == ".") NF--

    if (index($1, "<" ep) == 1) {
      $1 = "gptkb:" substr($1, length(ep) + 2)
      sub(">$", "", $1)
    }

    if (index($2, "<" pp) == 1) {
      $2 = "gptkbp:" substr($2, length(pp) + 2)
      sub(">$", "", $2)
    }

    if (index($3, "<" ep) == 1) {
      $3 = "gptkb:" substr($3, length(ep) + 2)
      sub(">$", "", $3)
    }

    print $0 " ."
  }' "$WORKDIR/inst_raw.nt"
) > "$INST_OUT"
echo "  Created $INST_OUT: $(wc -l < "$INST_OUT") lines"

echo ""
echo "=========================================="
echo "Pipeline Complete"
echo "=========================================="
echo "Output files:"
echo "  - $VOCAB_OUT     ($(wc -l < "$VOCAB_OUT") lines)"
echo "  - $INST_OUT      ($(wc -l < "$INST_OUT") lines)"
echo "Intermediate files in $WORKDIR:"
echo "  - classes_iris.csv"
echo "  - predicates_iris.csv"
echo "  - class_definitions.csv"
echo "  - top_predicates.csv"
echo "  - classes.txt"
echo "  - properties.txt"
echo "  - instances.txt"