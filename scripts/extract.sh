#!/usr/bin/env bash
# ============================================================
# extract.sh
#
# Single script that classifies predicates and generates
# vocab + instances files for GPTKB.
#
# Input files:
#   gptkb_v1.5.3.nt        — full NT (rapper-generated, clean)
#   gptkb_v1.5.3_types.nt  — rdf:type triples only
#                            (instanceOf → rdf:type, entity subjects only)
#   gptkb_v1.5.3.ttl       — original TTL, used only to extract @prefix lines
#                            (optional — TTL conversion is skipped if not found)
#
# Output files:
#   graphs/gptkb/gptkb-data-vocab.nt
#   graphs/gptkb/gptkb-data-instances.nt
#   graphs/gptkb/predicates_datatype.txt
#   graphs/gptkb/predicates_object.txt
#   graphs/gptkb/predicates_both.txt
#   graphs/gptkb/prefixes.ttl          (if source TTL provided)
#   graphs/gptkb/gptkb-data-vocab.ttl  (if source TTL provided)
#   graphs/gptkb/gptkb-data-instances.ttl (if source TTL provided)
#
# Usage:
#   bash extract.sh [main.nt] [types.nt] [output_dir] [source.ttl]
#
#   source.ttl is optional — if provided, @prefix declarations are
#   extracted from it and used to convert the NT outputs to TTL.
#   Defaults to gptkb_v1.5.3.ttl if the file exists, skipped if not.
# ============================================================

set -euo pipefail

NT_MAIN="${1:-gptkb_v1.5.3.nt}"
NT_TYPES="${2:-gptkb_v1.5.3_types.nt}"
OUTPUT_DIR="${3:-graphs/gptkb}"
SOURCE_TTL="${4:-gptkb_v1.5.3.ttl}"

VOCAB_OUT="$OUTPUT_DIR/gptkb-data-vocab.nt"
INSTANCES_OUT="$OUTPUT_DIR/gptkb-data-instances.nt"

TMPDIR_WORK="${TMPDIR:-/tmp}/gptkb_extract_$$"
mkdir -p "$OUTPUT_DIR" "$TMPDIR_WORK"

# Clean up temp dir on exit
trap 'rm -rf "$TMPDIR_WORK"' EXIT

for f in "$NT_MAIN" "$NT_TYPES"; do
    if [ ! -f "$f" ]; then
        echo "ERROR: Input file not found: $f"
        exit 1
    fi
done

# -------------------------------------------------------
# Helper: extract local name from a full IRI string
# e.g. <https://gptkb.org/entity/person>  → person
#      <https://gptkb.org/prop/birthDate> → birthDate
# -------------------------------------------------------
localname() {
    local iri="${1#<}"; iri="${iri%>}"
    if [[ "$iri" == *"#"* ]]; then echo "${iri##*#}"
    else                           echo "${iri##*/}"
    fi
}

RDF_TYPE="<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
RDFS_LABEL="<http://www.w3.org/2000/01/rdf-schema#label>"
OWL_CLASS="<http://www.w3.org/2002/07/owl#Class>"
OWL_DTYPE="<http://www.w3.org/2002/07/owl#DatatypeProperty>"
OWL_OBJ="<http://www.w3.org/2002/07/owl#ObjectProperty>"

# ==============================================================
# PART 1 — Classify predicates
# Single streaming pass over the main NT file.
# Emits <predicate> TAB kind for each triple, then aggregates.
# ==============================================================
echo "============================================================"
echo "  Classifying predicates"
echo "============================================================"
echo "[1/5] Scanning $NT_MAIN (~15-30 min for 17GB)..."

awk '
/^[[:space:]]*#/ { next }
/^[[:space:]]*$/ { next }
{
    # Skip subject token
    sub(/^[[:space:]]*(<[^>]*>|_:[^[:space:]]*)[[:space:]]+/, "")
    # Extract predicate
    match($0, /^<[^>]*>/)
    pred = substr($0, RSTART, RLENGTH)
    rest = substr($0, RSTART + RLENGTH)
    sub(/^[[:space:]]+/, "", rest)
    # Classify by first char of object
    first = substr(rest, 1, 1)
    if (first == "\"") {
        key = pred "\tD"
    } else if (first == "<" || first == "_") {
        key = pred "\tO"
    } else {
        next
    }
    # Deduplicate inside awk — only emit each pred+kind pair once
    if (!(key in seen)) {
        seen[key] = 1
        print key
    }
}
' "$NT_MAIN" | sort > "$TMPDIR_WORK/pred_kind_pairs.txt"

echo "[2/5] Aggregating per predicate..."
awk -F'\t' '
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
' "$TMPDIR_WORK/pred_kind_pairs.txt" | sort > "$TMPDIR_WORK/pred_aggregated.txt"

# Split into three lists
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

# Save predicate files to output directory (overwrites old ones)
cp "$TMPDIR_WORK/predicates_datatype.txt" "$OUTPUT_DIR/"
cp "$TMPDIR_WORK/predicates_object.txt" "$OUTPUT_DIR/"
cp "$TMPDIR_WORK/predicates_both.txt" "$OUTPUT_DIR/"
echo "  Saved predicate files to $OUTPUT_DIR/"

# ==============================================================
# PART 2 — Generate gptkb-data-vocab.nt
# ==============================================================
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
} > "$VOCAB_OUT"

# ---- Step 3: prop-subject triples ----
echo "[3/5] Extracting prop-subject triples..."
grep '^<https://gptkb.org/prop/' "$NT_MAIN" >> "$VOCAB_OUT"
# To exlcude alternativeName
# grep '^<https://gptkb.org/prop/' "$NT_MAIN" \
#     | grep -v '<https://gptkb.org/prop/alternativeName>' >> "$VOCAB_OUT"

# ---- Step 4: owl:Class + rdfs:label from types file ----
echo "[4/5] Extracting class IRIs from $NT_TYPES ..."
CLASSES_TMP="$TMPDIR_WORK/classes.txt"
awk '
/^[[:space:]]*#/ { next }
/^[[:space:]]*$/ { next }
{
    if ($2 == "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>") {
        obj = $3
        gsub(/[[:space:]]*\.[[:space:]]*$/, "", obj)
        if (substr(obj,1,1) == "<") print obj
    }
}
' "$NT_TYPES" | sort -u > "$CLASSES_TMP"
echo "  Found $(wc -l < "$CLASSES_TMP") unique classes"

while IFS= read -r iri; do
    [ -z "$iri" ] && continue
    label=$(localname "$iri")
    echo "$iri $RDF_TYPE $OWL_CLASS ."
    echo "$iri $RDFS_LABEL \"${label}\"@en ."
done < "$CLASSES_TMP" >> "$VOCAB_OUT"

# ---- Step 5: owl:DatatypeProperty / owl:ObjectProperty + rdfs:label ----
echo "[5/5] Writing property declarations..."

write_properties() {
    local file="$1"
    local prop_type="$2"
    [ -s "$file" ] || return 0
    while IFS= read -r iri; do
        [ -z "$iri" ] && continue
        label=$(localname "$iri")
        echo "$iri $RDF_TYPE ${prop_type} ."
        echo "$iri $RDFS_LABEL \"${label}\"@en ."
    done < "$file"
}

{
    write_properties "$TMPDIR_WORK/predicates_datatype.txt" "$OWL_DTYPE"
    write_properties "$TMPDIR_WORK/predicates_object.txt"   "$OWL_OBJ"
    if [ -s "$TMPDIR_WORK/predicates_both.txt" ]; then
        echo "# Ambiguous predicates — typed as ObjectProperty, review manually"
        write_properties "$TMPDIR_WORK/predicates_both.txt" "$OWL_OBJ"
    fi
} >> "$VOCAB_OUT"

echo "  Written: $VOCAB_OUT ($(wc -l < "$VOCAB_OUT") lines)"

# ==============================================================
# PART 3 — Generate gptkb-data-instances.nt
# ==============================================================
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
    # All entity-subject triples from main NT
    grep '^<https://gptkb.org/entity/' "$NT_MAIN"
    # rdf:type triples from types file (entity subjects only)
    grep '^<https://gptkb.org/entity/' "$NT_TYPES"
} > "$INSTANCES_OUT"

echo "  Written: $INSTANCES_OUT ($(wc -l < "$INSTANCES_OUT") lines)"

# ==============================================================
# PART 4 — Convert NT → TTL using prefixes from source TTL
# ==============================================================
echo ""
echo "============================================================"
echo "  Converting to TTL"
echo "============================================================"

if [ ! -f "$SOURCE_TTL" ]; then
    echo "  Skipping — source TTL not found: $SOURCE_TTL"
    echo "  To convert manually:"
    echo "    riot --output=turtle prefixes.ttl $VOCAB_OUT > ${VOCAB_OUT%.nt}.ttl"
    echo "    riot --output=turtle prefixes.ttl $INSTANCES_OUT > ${INSTANCES_OUT%.nt}.ttl"
else
    PREFIXES_OUT="$OUTPUT_DIR/prefixes.ttl"
    VOCAB_TTL="${VOCAB_OUT%.nt}.ttl"
    INSTANCES_TTL="${INSTANCES_OUT%.nt}.ttl"

    echo "  Extracting prefixes from $SOURCE_TTL ..."
    grep '^@prefix' "$SOURCE_TTL" > "$PREFIXES_OUT"
    echo "  Found $(wc -l < "$PREFIXES_OUT") prefix declarations"

    echo "  Converting $VOCAB_OUT → $VOCAB_TTL ..."
    cat "$PREFIXES_OUT" "$VOCAB_OUT" | riot --output=turtle --syntax=turtle > "$VOCAB_TTL"

    echo "  Converting $INSTANCES_OUT → $INSTANCES_TTL ..."
    cat "$PREFIXES_OUT" "$INSTANCES_OUT" | riot --output=turtle --syntax=turtle > "$INSTANCES_TTL"

    echo "  Written: $VOCAB_TTL"
    echo "  Written: $INSTANCES_TTL"
fi

echo ""
echo "============================================================"
echo "  Done!"
echo "============================================================"
echo ""
ls -lh "$VOCAB_OUT" "$INSTANCES_OUT" "$OUTPUT_DIR"/predicates_*.txt
echo ""
echo "Validate with:"
echo "  riot --validate $VOCAB_OUT"
echo "  riot --validate $INSTANCES_OUT"