#!/bin/bash
# Complete reset and rehash script - FIXED VERSION

set -e

echo "========================================="
echo "Complete Reset and Rehash"
echo "========================================="

# Stop and remove Virtuoso
echo "1. Removing Virtuoso container..."
docker stop virtuoso-test 2>/dev/null || true
docker rm virtuoso-test 2>/dev/null || true

# Remove Virtuoso data
echo "2. Removing Virtuoso data directory..."
rm -rf virtuoso-test

# Remove old hashed files (optional - comment out if you want to keep)
echo "3. Removing old hashed files..."
rm -rf graphs/ck25-h

# Re-hash
echo "4. Re-hashing KG..."
uv run python scripts/hash_ck25_iris.py \
  --input-dir graphs/ck25 \
  --output-dir graphs/ck25-h

# Setup fresh Virtuoso
echo "5. Setting up fresh Virtuoso..."
mkdir -p virtuoso-test/database virtuoso-test/import
cp graphs/ck25-h/*.ttl virtuoso-test/import/

cat > virtuoso-test/database/virtuoso.ini << 'EOF'
[Database]
DatabaseFile = virtuoso.db
[Parameters]
ServerPort = 1111
DirsAllowed = ., /opt/virtuoso-opensource/vad, /import
[HTTPServer]
ServerPort = 8890
Enabled = 1
EOF

# Start Virtuoso
echo "6. Starting Virtuoso..."
docker run --name virtuoso-test -d \
  --volume $(pwd)/virtuoso-test/database:/database \
  --volume $(pwd)/virtuoso-test/import:/import \
  -p 8890:8890 -p 1111:1111 \
  openlink/virtuoso-opensource-7:latest

echo "Waiting 25 seconds for Virtuoso to initialize..."
sleep 25

# Load data - Using the WORKING method (individual commands)
echo "7. Loading hashed data into Virtuoso..."

# Clear existing data
docker exec -i virtuoso-test isql 1111 dba dba << 'EOF'
SPARQL CLEAR GRAPH <http://ld.company.org/>;
DELETE FROM DB.DBA.LOAD_LIST;
checkpoint;
EOF

# Add files to load list
docker exec -i virtuoso-test isql 1111 dba dba << 'EOF'
ld_dir('/import', 'ck25-data-vocab.ttl', 'http://ld.company.org/');
ld_dir('/import', 'ck25-data-instances.ttl', 'http://ld.company.org/');
ld_dir('/import', 'ck25-data-shapes.ttl', 'http://ld.company.org/');
ld_dir('/import', 'ck25-examples.ttl', 'http://ld.company.org/');
ld_dir('/import', 'ck25-void.ttl', 'http://ld.company.org/');
EOF

# Verify files are in load list
echo "Files in load list:"
docker exec -i virtuoso-test isql 1111 dba dba << 'EOF'
SELECT * FROM DB.DBA.LOAD_LIST;
EOF

# Run the loader (this is the critical step)
echo "Running loader..."
docker exec -i virtuoso-test isql 1111 dba dba << 'EOF'
rdf_loader_run();
checkpoint;
EOF

# Check final count
echo "Final triple count:"
docker exec -i virtuoso-test isql 1111 dba dba << 'EOF'
SELECT COUNT(*) FROM DB.DBA.RDF_QUAD;
EOF

echo ""
echo "========================================="
echo "✅ Complete! Virtuoso ready at http://localhost:8890/sparql"
echo "========================================="

# Verify data loaded
echo ""
echo "Verifying data..."
sleep 2
curl -s -X POST http://localhost:8890/sparql \
  -H "Accept: application/sparql-results+json" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "query=PREFIX pv: <http://ld.company.org/prod-vocab/> SELECT (COUNT(*) AS ?count) WHERE { ?s a pv:236555b285e132eb }" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Employee count: {d[\"results\"][\"bindings\"][0][\"count\"][\"value\"]}')" 2>/dev/null || echo "Verification failed"