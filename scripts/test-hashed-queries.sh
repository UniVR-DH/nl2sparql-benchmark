#!/bin/bash
# compare-all-50-queries-fixed.sh - Fixed extraction

ORIGINAL_ENDPOINT="http://157.27.26.146:7001"
HASHED_ENDPOINT="http://localhost:8890/sparql"

# Extract queries - each query as a SINGLE block
extract_queries() {
    python3 << EOF
import re
with open('$1', 'r') as f:
    content = f.read()
# Find all lsqv:text blocks
pattern = r'lsqv:text\s+"""(.*?)"""'
matches = re.findall(pattern, content, re.DOTALL)
# Store each query as a single item (preserve newlines within query)
for match in matches:
    # Print with a special separator that won't appear in queries
    print("===QUERY_START===")
    print(match.strip())
    print("===QUERY_END===")
EOF
}

echo "Extracting queries from TTL files..."

# Extract with separators
extract_queries "graphs/ck25/ck25-queries.ttl" > /tmp/orig_queries.txt
extract_queries "graphs/ck25-h/ck25-queries.ttl" > /tmp/hash_queries.txt

# Count queries (count the number of QUERY_START markers)
TOTAL_QUERIES=$(grep -c "===QUERY_START===" /tmp/orig_queries.txt)
echo "Found $TOTAL_QUERIES queries in each file"
echo ""

# Parse queries into arrays using the separators
ORIG_QUERIES=()
HASH_QUERIES=()

# Parse original queries
while IFS= read -r line; do
    if [[ "$line" == "===QUERY_START===" ]]; then
        current=""
    elif [[ "$line" == "===QUERY_END===" ]]; then
        ORIG_QUERIES+=("$current")
    else
        if [ -z "$current" ]; then
            current="$line"
        else
            current="$current"$'\n'"$line"
        fi
    fi
done < /tmp/orig_queries.txt

# Parse hashed queries
while IFS= read -r line; do
    if [[ "$line" == "===QUERY_START===" ]]; then
        current=""
    elif [[ "$line" == "===QUERY_END===" ]]; then
        HASH_QUERIES+=("$current")
    else
        if [ -z "$current" ]; then
            current="$line"
        else
            current="$current"$'\n'"$line"
        fi
    fi
done < /tmp/hash_queries.txt

# Function to execute query on original KG (QLever)
query_original() {
    curl -s -G "$ORIGINAL_ENDPOINT" \
        --data-urlencode "query=$1" \
        -H "Accept: application/sparql-results+json" 2>/dev/null
}

# Function to execute query on hashed KG (Virtuoso)
query_hashed() {
    curl -s -X POST "$HASHED_ENDPOINT" \
        -H "Accept: application/sparql-results+json" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        --data-urlencode "query=$1" 2>/dev/null
}

# Function to extract result
get_result() {
    local result="$1"
    if echo "$result" | grep -q '"boolean"'; then
        echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); val=str(d.get('boolean', 'unknown')).lower(); print('true' if val == 'true' else 'false')" 2>/dev/null
    elif echo "$result" | grep -q '__ASK_RETVAL'; then
        echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); val=d.get('results',{}).get('bindings',[{}])[0].get('__ASK_RETVAL',{}).get('value','0'); print('true' if val == '1' else 'false')" 2>/dev/null
    else
        echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('results',{}).get('bindings',[])))" 2>/dev/null || echo "0"
    fi
}

PASSED=0
FAILED=0
declare -a FAILED_LIST=()

echo "Running $TOTAL_QUERIES queries..."
echo ""

for i in "${!ORIG_QUERIES[@]}"; do
    QUERY_NUM=$((i + 1))
    
    ORIG_QUERY="${ORIG_QUERIES[$i]}"
    HASH_QUERY="${HASH_QUERIES[$i]}"
    
    # Execute
    ORIG_RESULT=$(query_original "$ORIG_QUERY")
    HASH_RESULT=$(query_hashed "$HASH_QUERY")
    
    ORIG_VAL=$(get_result "$ORIG_RESULT")
    HASH_VAL=$(get_result "$HASH_RESULT")
    
    if [ "$ORIG_VAL" = "$HASH_VAL" ]; then
        echo "$QUERY_NUM. ✅ ($ORIG_VAL)"
        ((PASSED++))
    else
        echo "$QUERY_NUM. ❌ (Orig: $ORIG_VAL, Hash: $HASH_VAL)"
        FAILED_LIST+=($QUERY_NUM)
        ((FAILED++))
    fi
    
    # Small delay to avoid overwhelming the endpoints
    sleep 0.1
done

echo ""
echo "========================================="
echo "TEST SUMMARY"
echo "========================================="
echo "Total Queries: $TOTAL_QUERIES"
echo "Passed: $PASSED"
echo "Failed: $FAILED"

if [ $FAILED -gt 0 ]; then
    echo ""
    echo "Failed queries: ${FAILED_LIST[*]}"
fi

if [ $PASSED -eq $TOTAL_QUERIES ]; then
    echo ""
    echo "ALL $TOTAL_QUERIES QUERIES PASSED!"
fi
echo "========================================="

rm -f /tmp/orig_queries.txt /tmp/hash_queries.txt

# Query 25 - Coil with Highest Density: NOT a hashing issue! Integer division rounding in Virtuoso vs decimal in QLever, the hashed data is correct, but the calculation differs between SPARQL engines"

# Query 27 - Phone Directory (Non-Managers): NOT a hashing issue! ORDER BY with OPTIONAL behaves differently in Virtuoso vs QLever, Virtuoso filters NULL values differently when sorting"