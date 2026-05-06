#!/usr/bin/env bash
# ============================================================
# extract.sh
#
# Single script that classifies predicates and generates
# vocab + instances files for GPTKB.
#
# Input files:
#   gptkb_v1.5.3.nt        — full NT (rapper-generated, clean)
#   gptkb_v1.5.3_types.nt  — instanceOf triples rewritten as rdf:type
#                            (produced with:
#                              awk '$2 == "<https://gptkb.org/prop/instanceOf>"' gptkb_v1.5.3.nt \
#                              | sed 's|<https://gptkb.org/prop/instanceOf>|<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>|' \
#                              > gptkb_v1.5.3_types.nt)
#   gptkb_v1.5.3.ttl       — original TTL, used only to extract @prefix lines (optional)
#
# Output files (PREFIX defaults to the stem of the first input file,
#               e.g. "gptkb_v1.5.3" from "gptkb_v1.5.3.nt"):
#   graphs/gptkb/<PREFIX>-vocab.nt
#   graphs/gptkb/<PREFIX>-instances.nt
#   graphs/gptkb/<PREFIX>-predicates_datatype.txt
#   graphs/gptkb/<PREFIX>-predicates_object.txt
#   graphs/gptkb/<PREFIX>-predicates_both.txt
#   graphs/gptkb/<PREFIX>-prefixes.ttl
#   graphs/gptkb/<PREFIX>-vocab.ttl
#   graphs/gptkb/<PREFIX>-instances.ttl
#
# Usage:
#   bash extract.sh [main.nt] [types.nt] [output_dir] [source.ttl] [PREFIX]
#
#   source.ttl is optional — if provided, @prefix declarations are
#   extracted from it and used to convert the NT outputs to TTL.
#   Defaults to gptkb_v1.5.3.ttl if the file exists, skipped if not.
#
#   PREFIX is optional — overrides the default stem derived from main.nt.
# ============================================================
export LC_ALL=C
set -euo pipefail

NT_MAIN="${1:-gptkb_v1.5.3.nt}"
NT_TYPES="${2:-gptkb_v1.5.3_types.nt}"
OUTPUT_DIR="${3:-graphs/gptkb}"
SOURCE_TTL="${4:-gptkb_v1.5.3.ttl}"
PREFIX="${5:-$(basename "${NT_MAIN%.*}")}"

VOCAB_OUT="$OUTPUT_DIR/$PREFIX-vocab.nt"
INSTANCES_OUT="$OUTPUT_DIR/$PREFIX-instances.nt"

TMPDIR_WORK="${TMPDIR:-/tmp}/gptkb_extract_$$"
mkdir -p "$OUTPUT_DIR" "$TMPDIR_WORK"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

for f in "$NT_MAIN" "$NT_TYPES"; do
    [ -f "$f" ] || { echo "ERROR: Input file not found: $f"; exit 1; }
done

# ── URI constants ─────────────────────
RDF_TYPE="<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
RDFS_LABEL="<http://www.w3.org/2000/01/rdf-schema#label>"
RDFS_COMMENT="<http://www.w3.org/2000/01/rdf-schema#comment>"
RDFS_SUBCLASSOF="<http://www.w3.org/2000/01/rdf-schema#subClassOf>"
# rdfs:domain and rdfs:range are reserved for future use; add them here
# and to is_schema_pred in the awk pass below when ready to implement.
OWL_CLASS="<http://www.w3.org/2002/07/owl#Class>"
OWL_DTYPE="<http://www.w3.org/2002/07/owl#DatatypeProperty>"
OWL_OBJ="<http://www.w3.org/2002/07/owl#ObjectProperty>"

PROP_PREFIX="<https://gptkb.org/prop/"
ENTITY_PREFIX="<https://gptkb.org/entity/"

# ============================================================
# SINGLE PASS over NT_MAIN
#
# In one streaming read we collect everything we need:
#   (a) predicate classification   → pred_kind_pairs.txt
#   (b) prop-subject triples       → prop_triples.nt        (vocab)
#   (c) entity schema triples      → entity_schema.nt       (vocab)
#       predicates: rdf:type, rdfs:subClassOf, rdfs:label, rdfs:comment
#       (rdfs:domain and rdfs:range reserved for future use)
#   (d) class IRIs from subClassOf → subclassof_classes.txt (vocab)
#       both subject and object of every rdfs:subClassOf triple
#   (e) entity instance triples    → entity_triples.nt      (instances)
#       all entity-subject triples whose predicate is NOT one of
#       the schema predicates listed above
#
# ============================================================
echo "============================================================"
echo "  Single pass over $NT_MAIN"
echo "============================================================"

# Note: rdfs:domain and rdfs:range are intentionally omitted from
# the -v list and from is_schema_pred; add both when ready.
awk \
    -v prop_prefix="$PROP_PREFIX" \
    -v entity_prefix="$ENTITY_PREFIX" \
    -v rdf_type_uri="$RDF_TYPE" \
    -v rdfs_label_uri="$RDFS_LABEL" \
    -v rdfs_comment_uri="$RDFS_COMMENT" \
    -v rdfs_subclassof="$RDFS_SUBCLASSOF" \
    -v pred_out="$TMPDIR_WORK/pred_kind_pairs.txt" \
    -v prop_out="$TMPDIR_WORK/prop_triples.nt" \
    -v schema_out="$TMPDIR_WORK/entity_schema.nt" \
    -v subclasses_out="$TMPDIR_WORK/subclassof_classes.txt" \
    -v ent_out="$TMPDIR_WORK/entity_triples.nt" \
'
BEGIN {
    print "" > pred_out;       close(pred_out)
    print "" > prop_out;       close(prop_out)
    print "" > schema_out;     close(schema_out)
    print "" > subclasses_out; close(subclasses_out)
    print "" > ent_out;        close(ent_out)
}

/^[[:space:]]*#/ { next }
/^[[:space:]]*$/ { next }

{
    line = $0

    # ── subject ──────────────────────────────────────────────
    match(line, /^[[:space:]]*(<[^>]*>|_:[^[:space:]]*)/)
    subj = substr(line, RSTART, RLENGTH)
    rest = substr(line, RSTART + RLENGTH)
    sub(/^[[:space:]]+/, "", rest)

    # ── predicate ────────────────────────────────────────────
    match(rest, /^(<[^>]*>|_:[^[:space:]]*)/)
    pred = substr(rest, RSTART, RLENGTH)
    rest2 = substr(rest, RSTART + RLENGTH)
    sub(/^[[:space:]]+/, "", rest2)

    # ── predicate classification ──────────────────────────────
    # Distinguish DatatypeProperty (literal) vs ObjectProperty (IRI)
    first = substr(rest2, 1, 1)
    if (first == "\"") {
        key = pred "\tD"      # DatatypeProperty
    } else if (first == "<" || first == "_") {
        key = pred "\tO"      # ObjectProperty
    } else {
        key = ""
    }
    if (key != "" && !(key in seen_pred)) {
        seen_pred[key] = 1
        print key >> pred_out
    }

    # ── route triple to the right output file ────────────────
    is_prop   = (index(subj, prop_prefix)   == 1)
    is_entity = (index(subj, entity_prefix) == 1)

    # Schema-level predicates: describe the vocabulary itself.
    # rdfs:domain and rdfs:range are reserved for future addition here.
    is_schema_pred = (pred == rdfs_subclassof || \
                      pred == rdf_type_uri    || \
                      pred == rdfs_label_uri  || \
                      pred == rdfs_comment_uri)

    if (is_prop) {
        # All prop-subject triples go to vocab unchanged.
        print line >> prop_out
    } else if (is_entity) {
        if (is_schema_pred) {
            # Class annotations, subClassOf, etc. → vocab
            print line >> schema_out
            # Also harvest both endpoints of subClassOf so that
            # classes appearing only here (never in the types file)
            # still receive owl:Class + rdfs:label in the vocab.
            if (pred == rdfs_subclassof) {
                obj = $3
                gsub(/[[:space:]]*\.[[:space:]]*$/, "", obj)
                if (!(subj in seen_sc)) { seen_sc[subj] = 1; print subj >> subclasses_out }
                if (!(obj  in seen_sc)) { seen_sc[obj]  = 1; print obj  >> subclasses_out }
            }
        } else {
            # Everything else with an entity subject is instance data.
            print line >> ent_out
        }
    }
    # Triples with other subjects are silently ignored.
}
' "$NT_MAIN"

echo "  Pass complete."

# ============================================================
# SINGLE PASS over NT_TYPES
#
# Collects:
#   (f) unique class IRIs from rdf:type objects → type_classes.txt
#   (g) entity rdf:type triples                 → type_triples.nt (instances)
#
# After this pass, classes.txt is assembled by merging type_classes.txt
# with subclassof_classes.txt (harvested in the main pass), so that
# classes present only in rdfs:subClassOf statements — but never
# instantiated — still receive owl:Class + rdfs:label in the vocab.
# ============================================================
echo ""
echo "============================================================"
echo "  Single pass over $NT_TYPES"
echo "============================================================"

awk \
    -v rdf_type="$RDF_TYPE" \
    -v entity_prefix="$ENTITY_PREFIX" \
    -v type_classes_out="$TMPDIR_WORK/type_classes.txt" \
    -v types_out="$TMPDIR_WORK/type_triples.nt" \
'
BEGIN {
    print "" > type_classes_out; close(type_classes_out)
    print "" > types_out;        close(types_out)
}
/^[[:space:]]*#/ { next }
/^[[:space:]]*$/ { next }
{
    # Only process rdf:type triples (all lines in this file should be,
    # but guard anyway for safety)
    if ($2 != rdf_type) next

    subj = $1
    obj  = $3
    # strip trailing " ." if present
    gsub(/[[:space:]]*\.[[:space:]]*$/, "", obj)

    # entity-subject lines go to instances
    if (index(subj, entity_prefix) == 1) {
        print $0 >> types_out
    }

    # collect unique class IRIs (objects of rdf:type)
    if (substr(obj, 1, 1) == "<" && !(obj in seen_class)) {
        seen_class[obj] = 1
        print obj >> type_classes_out
    }
}
' "$NT_TYPES"

# Merge class IRIs from both sources into a single deduplicated list.
sort -u "$TMPDIR_WORK/type_classes.txt" "$TMPDIR_WORK/subclassof_classes.txt" \
    > "$TMPDIR_WORK/classes.txt"

echo "  Classes from rdf:type:        $(wc -l < "$TMPDIR_WORK/type_classes.txt")"
echo "  Classes from rdfs:subClassOf: $(wc -l < "$TMPDIR_WORK/subclassof_classes.txt")"
echo "  Total unique classes:         $(wc -l < "$TMPDIR_WORK/classes.txt")"

# ============================================================
# Aggregate predicate classification
# ============================================================
echo ""
echo "============================================================"
echo "  Classifying predicates"
echo "============================================================"

sort -u "$TMPDIR_WORK/pred_kind_pairs.txt" \
| awk -F'\t' '
{
    pred = $1; kind = $2
    if (!(pred in seen)) {
        seen[pred] = kind
    } else if (index(seen[pred], kind) == 0) {
        seen[pred] = seen[pred] " " kind
    }
}
END {
    for (p in seen) print p "\t" seen[p]
}
' | sort > "$TMPDIR_WORK/pred_aggregated.txt"

> "$TMPDIR_WORK/predicates_datatype.txt"
> "$TMPDIR_WORK/predicates_object.txt"
> "$TMPDIR_WORK/predicates_both.txt"

while IFS=$'\t' read -r iri kinds; do
    [ -z "$iri" ] && continue
    case "$kinds" in
        "D")         echo "$iri" >> "$TMPDIR_WORK/predicates_datatype.txt" ;;
        "O")         echo "$iri" >> "$TMPDIR_WORK/predicates_object.txt" ;;
        "D O"|"O D") echo "$iri" >> "$TMPDIR_WORK/predicates_both.txt" ;;
    esac
done < "$TMPDIR_WORK/pred_aggregated.txt"

echo "  owl:DatatypeProperty : $(wc -l < "$TMPDIR_WORK/predicates_datatype.txt")"
echo "  owl:ObjectProperty   : $(wc -l < "$TMPDIR_WORK/predicates_object.txt")"
echo "  Both (review)        : $(wc -l < "$TMPDIR_WORK/predicates_both.txt")"

cp "$TMPDIR_WORK/predicates_datatype.txt" "$OUTPUT_DIR/$PREFIX-predicates_datatype.txt"
cp "$TMPDIR_WORK/predicates_object.txt"   "$OUTPUT_DIR/$PREFIX-predicates_object.txt"
cp "$TMPDIR_WORK/predicates_both.txt"     "$OUTPUT_DIR/$PREFIX-predicates_both.txt"
echo "  Saved predicate files to $OUTPUT_DIR/"

# ============================================================
# Generate vocab NT
# ============================================================
echo ""
echo "============================================================"
echo "  Generating $VOCAB_OUT"
echo "============================================================"

{
    echo "# ============================================================"
    echo "# $VOCAB_OUT"
    echo "# Auto-generated vocabulary for GPTKB"
    echo "# Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# ============================================================"

    # prop-subject triples (property definitions)
    cat "$TMPDIR_WORK/prop_triples.nt"

    # Entity schema triples (rdfs:label, rdfs:comment, rdfs:subClassOf, etc.)
    cat "$TMPDIR_WORK/entity_schema.nt"

    # owl:Class + rdfs:label for every class
    awk \
        -v rdf_type="$RDF_TYPE" \
        -v rdfs_label="$RDFS_LABEL" \
        -v owl_class="$OWL_CLASS" \
    '
    /^[[:space:]]*$/ { next }
    {
        iri = $0
        # derive local name: take part after last # or /
        local_name = iri
        gsub(/^</, "", local_name)
        gsub(/>$/, "", local_name)
        n = split(local_name, parts, "/")
        local_name = parts[n]
        if (index(local_name, "#")) {
            split(local_name, hparts, "#")
            local_name = hparts[length(hparts)]
        }
        print iri, rdf_type, owl_class, "."
        print iri, rdfs_label, "\"" local_name "\"@en ."
    }
    ' "$TMPDIR_WORK/classes.txt"

    # Synthesise owl:DatatypeProperty / owl:ObjectProperty + rdfs:label
    # for predicates that have no prop-subject triples of their own.
    write_props() {
        local src="$1" ptype="$2"
        [ -s "$src" ] || return 0
        awk \
            -v rdf_type="$RDF_TYPE" \
            -v rdfs_label="$RDFS_LABEL" \
            -v prop_type="$ptype" \
        '
        /^[[:space:]]*$/ { next }
        {
            iri = $0
            local_name = iri
            gsub(/^</, "", local_name); gsub(/>$/, "", local_name)
            n = split(local_name, parts, "/")
            local_name = parts[n]
            if (index(local_name, "#")) {
                split(local_name, hparts, "#")
                local_name = hparts[length(hparts)]
            }
            print iri, rdf_type, prop_type, "."
            print iri, rdfs_label, "\"" local_name "\"@en ."
        }
        ' "$src"
    }

    write_props "$TMPDIR_WORK/predicates_datatype.txt" "$OWL_DTYPE"
    write_props "$TMPDIR_WORK/predicates_object.txt"   "$OWL_OBJ"
    if [ -s "$TMPDIR_WORK/predicates_both.txt" ]; then
        echo "# ============================================================"
        echo "# WARNING: predicates that appear as BOTH DatatypeProperty AND ObjectProperty"
        echo "# Typed as ObjectProperty below — review manually"
        echo "# ============================================================"
        write_props "$TMPDIR_WORK/predicates_both.txt" "$OWL_OBJ"
    fi

} > "$VOCAB_OUT"

SUBCLASSOF_COUNT=$(grep -c " $RDFS_SUBCLASSOF " "$VOCAB_OUT" || true)
echo "  rdfs:subClassOf triples: $SUBCLASSOF_COUNT"
echo "  Written: $VOCAB_OUT ($(wc -l < "$VOCAB_OUT") lines)"

# ============================================================
# Generate instances NT
# ============================================================
echo ""
echo "============================================================"
echo "  Generating $INSTANCES_OUT"
echo "============================================================"

{
    echo "# ============================================================"
    echo "# $INSTANCES_OUT"
    echo "# Auto-generated from: $NT_MAIN + $NT_TYPES"
    echo "# Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "# ============================================================"

    # entity triples from main NT (instance data)
    cat "$TMPDIR_WORK/entity_triples.nt"

    # rdf:type triples from types file
    cat "$TMPDIR_WORK/type_triples.nt"

} > "$INSTANCES_OUT"

echo "  Written: $INSTANCES_OUT ($(wc -l < "$INSTANCES_OUT") lines)"

# ============================================================
# NT → TTL
# ============================================================
echo ""
echo "============================================================"
echo "  Converting to TTL"
echo "============================================================"

PREFIXES_OUT="$OUTPUT_DIR/$PREFIX-prefixes.ttl"
VOCAB_TTL="${VOCAB_OUT%.nt}.ttl"
INSTANCES_TTL="${INSTANCES_OUT%.nt}.ttl"

cat > "$PREFIXES_OUT" <<'EOF'
@prefix rdf:   <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs:  <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl:   <http://www.w3.org/2002/07/owl#> .
@prefix xsd:   <http://www.w3.org/2001/XMLSchema#> .
@prefix gptkb: <https://gptkb.org/entity/> .
@prefix gptkbp: <https://gptkb.org/prop/> .
EOF

if [ -f "$SOURCE_TTL" ]; then
    echo "  Adding extra prefixes from $SOURCE_TTL ..."
    grep '^@prefix' "$SOURCE_TTL" | grep -v -E '(rdf:|rdfs:|owl:|xsd:|gptkb:|gptkbp:)' >> "$PREFIXES_OUT" || true
fi

echo "  Prefixes: $(grep -c '^@prefix' "$PREFIXES_OUT")"

if command -v riot &> /dev/null; then
    echo "  Converting $VOCAB_OUT → $VOCAB_TTL ..."
    cat "$PREFIXES_OUT" "$VOCAB_OUT" | riot --output=turtle --syntax=turtle > "$VOCAB_TTL"

    echo "  Converting $INSTANCES_OUT → $INSTANCES_TTL ..."
    cat "$PREFIXES_OUT" "$INSTANCES_OUT" | riot --output=turtle --syntax=turtle > "$INSTANCES_TTL"

    echo "  Written: $VOCAB_TTL"
    echo "  Written: $INSTANCES_TTL"
else
    echo "  ERROR: riot not found - cannot convert to TTL"
    echo "  Install Apache Jena or use manual conversion:"
    echo "    cat $PREFIXES_OUT $VOCAB_OUT | riot --output=turtle --syntax=turtle > $VOCAB_TTL"
    echo "    cat $PREFIXES_OUT $INSTANCES_OUT | riot --output=turtle --syntax=turtle > $INSTANCES_TTL"
    exit 1
fi

# ============================================================
# Create companion .graph files (small pointer files used by project tooling)
# ============================================================
echo ""
echo "============================================================"
echo "  Generating .graph files"
echo "============================================================"

VOCAB_GRAPH="$OUTPUT_DIR/$PREFIX-vocab.graph"
INSTANCES_GRAPH="$OUTPUT_DIR/$PREFIX-instances.graph"

# Derive gptkb: prefix URI: prefer SOURCE_TTL (reflects actual data),
# fall back to the known stable GPTKB base.
GPTKB_PREFIX_URI=""
if [ -f "$SOURCE_TTL" ]; then
    GPTKB_PREFIX_URI=$(grep '^@prefix gptkb:' "$SOURCE_TTL" \
        | sed -n "s/^@prefix gptkb:[[:space:]]*<\(.*\)>[[:space:]]*\..*/\1/p" \
        | head -n 1 || true)
fi
if [ -z "$GPTKB_PREFIX_URI" ]; then
    GPTKB_PREFIX_URI="https://gptkb.org/entity/"
    echo "WARNING: could not extract gptkb: prefix from SOURCE_TTL; using fallback <$GPTKB_PREFIX_URI>" >&2
fi

VOCAB_URI=$(echo "$GPTKB_PREFIX_URI" | sed -E 's#(/entity/?)$#/vocab/#')
INST_URI=$(echo "$GPTKB_PREFIX_URI"  | sed -E 's#(/entity/?)$#/#')

echo "$VOCAB_URI" > "$VOCAB_GRAPH"
echo "$INST_URI"  > "$INSTANCES_GRAPH"
echo "  Written: $VOCAB_GRAPH ($VOCAB_URI)"
echo "  Written: $INSTANCES_GRAPH ($INST_URI)"

ls -lh "$VOCAB_OUT" "$INSTANCES_OUT" \
       "$OUTPUT_DIR/$PREFIX-predicates_datatype.txt" \
       "$OUTPUT_DIR/$PREFIX-predicates_object.txt" \
       "$OUTPUT_DIR/$PREFIX-predicates_both.txt" \
       "$VOCAB_GRAPH" "$INSTANCES_GRAPH"
echo ""
echo "Validate with:"
echo "  riot --validate $VOCAB_OUT"
echo "  riot --validate $INSTANCES_OUT"
echo ""
if [ -s "$TMPDIR_WORK/predicates_both.txt" ]; then
    echo "WARNING: $(wc -l < "$TMPDIR_WORK/predicates_both.txt") predicates appear as both DatatypeProperty and ObjectProperty"
    echo "Check $OUTPUT_DIR/$PREFIX-predicates_both.txt for manual review"
fi

echo ""
echo "============================================================"
echo "  Done"
echo "============================================================"
echo ""