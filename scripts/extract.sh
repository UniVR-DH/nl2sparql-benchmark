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
export LC_ALL=C 
set -euo pipefail

NT_MAIN="${1:-gptkb_v1.5.3.nt}"
NT_TYPES="${2:-gptkb_v1.5.3_types.nt}"
OUTPUT_DIR="${3:-graphs/gptkb}"
SOURCE_TTL="${4:-gptkb_v1.5.3.ttl}"

VOCAB_OUT="$OUTPUT_DIR/gptkb-data-vocab.nt"
INSTANCES_OUT="$OUTPUT_DIR/gptkb-data-instances.nt"

TMPDIR_WORK="${TMPDIR:-/tmp}/gptkb_extract_$$"
mkdir -p "$OUTPUT_DIR" "$TMPDIR_WORK"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

for f in "$NT_MAIN" "$NT_TYPES"; do
    [ -f "$f" ] || { echo "ERROR: Input file not found: $f"; exit 1; }
done

# ── URI constants (used in awk -v args) ─────────────────────
RDF_TYPE="<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
RDFS_LABEL="<http://www.w3.org/2000/01/rdf-schema#label>"
RDFS_SUBCLASSOF="<http://www.w3.org/2000/01/rdf-schema#subClassOf>"
OWL_CLASS="<http://www.w3.org/2002/07/owl#Class>"
OWL_DTYPE="<http://www.w3.org/2002/07/owl#DatatypeProperty>"
OWL_OBJ="<http://www.w3.org/2002/07/owl#ObjectProperty>"

PROP_PREFIX="<https://gptkb.org/prop/"
ENTITY_PREFIX="<https://gptkb.org/entity/"

# ============================================================
# SINGLE PASS over NT_MAIN
#
# In one streaming read we collect everything we need:
#   (a) predicate classification  → pred_kind_pairs.txt
#   (b) prop-subject triples      → prop_triples.nt       (vocab)
#   (c) rdfs:subClassOf triples   → subclassof_triples.nt (vocab)
#   (d) entity-subject triples    → entity_triples.nt     (instances)
#       (subClassOf already split out, so this file excludes them)
#
# All four outputs are written in a single awk pass — the file
# is read from disk exactly once regardless of its size.
# ============================================================
echo "============================================================"
echo "  Single pass over $NT_MAIN"
echo "============================================================"

awk \
    -v prop_prefix="$PROP_PREFIX" \
    -v entity_prefix="$ENTITY_PREFIX" \
    -v rdfs_subclassof="$RDFS_SUBCLASSOF" \
    -v pred_out="$TMPDIR_WORK/pred_kind_pairs.txt" \
    -v prop_out="$TMPDIR_WORK/prop_triples.nt" \
    -v sub_out="$TMPDIR_WORK/subclassof_triples.nt" \
    -v ent_out="$TMPDIR_WORK/entity_triples.nt" \
'
BEGIN {
    # open all output files once
    print "" > pred_out; close(pred_out)
    print "" > prop_out; close(prop_out)
    print "" > sub_out;  close(sub_out)
    print "" > ent_out;  close(ent_out)
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
    first = substr(rest2, 1, 1)
    if (first == "\"") {
        key = pred "\tD"
    } else if (first == "<" || first == "_") {
        key = pred "\tO"
    } else {
        key = ""
    }
    if (key != "" && !(key in seen_pred)) {
        seen_pred[key] = 1
        print key >> pred_out
    }

    # ── route triple to the right output file ────────────────
    is_prop   = (index(subj, prop_prefix)    == 1)
    is_entity = (index(subj, entity_prefix)  == 1)
    is_sub    = (pred == rdfs_subclassof)

    if (is_prop) {
        print line >> prop_out
    } else if (is_entity) {
        if (is_sub) {
            print line >> sub_out
        } else {
            print line >> ent_out
        }
    }
    # triples with other subjects are silently ignored
    # (same behaviour as the original grep-based routing)
}
' "$NT_MAIN"

echo "  Pass complete."

# ============================================================
# SINGLE PASS over NT_TYPES
#
# Collects:
#   (e) unique class IRIs              → classes.txt
#   (f) entity rdf:type triples        → type_triples.nt  (instances)
# ============================================================
echo ""
echo "============================================================"
echo "  Single pass over $NT_TYPES"
echo "============================================================"

awk \
    -v rdf_type="$RDF_TYPE" \
    -v entity_prefix="$ENTITY_PREFIX" \
    -v classes_out="$TMPDIR_WORK/classes.txt" \
    -v types_out="$TMPDIR_WORK/type_triples.nt" \
'
BEGIN {
    print "" > classes_out; close(classes_out)
    print "" > types_out;   close(types_out)
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
        print obj >> classes_out
    }
}
' "$NT_TYPES"

# sort -u the classes file (awk deduplicates within a run but let's be safe)
# sort -u "$TMPDIR_WORK/classes.txt" -o "$TMPDIR_WORK/classes.txt"
echo "  Found $(wc -l < "$TMPDIR_WORK/classes.txt") unique classes"

# ============================================================
# Aggregate predicate classification
# (same logic as before, but the raw pairs file is now fully
#  populated from the single NT_MAIN pass above)
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

cp "$TMPDIR_WORK/predicates_datatype.txt" "$OUTPUT_DIR/"
cp "$TMPDIR_WORK/predicates_object.txt"   "$OUTPUT_DIR/"
cp "$TMPDIR_WORK/predicates_both.txt"     "$OUTPUT_DIR/"
echo "  Saved predicate files to $OUTPUT_DIR/"

# ============================================================
# Generate gptkb-data-vocab.nt
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

    # prop-subject triples (already extracted)
    cat "$TMPDIR_WORK/prop_triples.nt"

    # rdfs:subClassOf triples (already extracted)
    cat "$TMPDIR_WORK/subclassof_triples.nt"

    # owl:Class + rdfs:label for every class
    # Pure awk — no per-line bash subshell
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

    # owl:DatatypeProperty / owl:ObjectProperty + rdfs:label
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
        echo "# Ambiguous predicates — typed as ObjectProperty, review manually"
        write_props "$TMPDIR_WORK/predicates_both.txt" "$OWL_OBJ"
    fi

} > "$VOCAB_OUT"

SUBCLASSOF_COUNT=$(grep -c " $RDFS_SUBCLASSOF " "$VOCAB_OUT" || true)
echo "  rdfs:subClassOf triples: $SUBCLASSOF_COUNT"
echo "  Written: $VOCAB_OUT ($(wc -l < "$VOCAB_OUT") lines)"

# ============================================================
# Generate gptkb-data-instances.nt
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

    # entity triples from main NT (subClassOf already excluded)
    cat "$TMPDIR_WORK/entity_triples.nt"

    # rdf:type triples from types file
    cat "$TMPDIR_WORK/type_triples.nt"

} > "$INSTANCES_OUT"

echo "  Written: $INSTANCES_OUT ($(wc -l < "$INSTANCES_OUT") lines)"

# ============================================================
# PART 4 — NT → TTL
# ============================================================
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
echo "  Done"
echo "============================================================"
echo ""
ls -lh "$VOCAB_OUT" "$INSTANCES_OUT" "$OUTPUT_DIR"/predicates_*.txt
echo ""
echo "Validate with:"
echo "  riot --validate $VOCAB_OUT"
echo "  riot --validate $INSTANCES_OUT"