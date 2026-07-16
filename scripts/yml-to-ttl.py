#!/usr/bin/env python3
"""
Convert YAML SPARQL examples to Turtle (.ttl) format for QLever.
Supports both SELECT and ASK queries.
"""

import yaml
import sys
import uuid
import re
from datetime import datetime

def detect_query_type(query_text):
    """Detect if the query is SELECT, ASK, or other."""
    query_upper = query_text.upper().strip()
    if query_upper.startswith('ASK'):
        return 'ASK'
    elif query_upper.startswith('SELECT'):
        return 'SELECT'
    elif query_upper.startswith('CONSTRUCT'):
        return 'CONSTRUCT'
    elif query_upper.startswith('DESCRIBE'):
        return 'DESCRIBE'
    else:
        return 'UNKNOWN'

def yml_to_ttl(input_file, output_file):
    try:
        with open(input_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', content)
        data = yaml.safe_load(content)
        
    except Exception as e:
        print(f"Error parsing YAML: {e}")
        sys.exit(1)
    
    if not isinstance(data, list):
        print("Error: YAML should contain a list of queries.")
        sys.exit(1)
    
    # Build TTL
    ttl = [
        "# Generated on " + datetime.now().isoformat(),
        "",
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix schema: <https://schema.org/> .",
        "@prefix ex: <http://ld.company.org/example#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix spex: <https://purl.expasy.org/sparql-examples/ontology#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        ""
    ]
    
    # Track statistics
    stats = {'SELECT': 0, 'ASK': 0, 'OTHER': 0}
    
    for idx, entry in enumerate(data):
        if not isinstance(entry, dict) or 'query' not in entry:
            print(f"Warning: Skipping invalid entry at index {idx}")
            continue
        
        query_hash = str(uuid.uuid4())[:8]
        case_id = f"ex:case_{idx}_{query_hash}"
        query_text = entry['query'].strip()
        
        # Detect query type
        query_type = detect_query_type(query_text)
        stats[query_type] = stats.get(query_type, 0) + 1
        
        # Determine the correct SHACL class
        if query_type == 'ASK':
            shacl_type = 'sh:SPARQLAskExecutable'
        else:
            shacl_type = 'sh:SPARQLSelectExecutable'
        
        ttl.extend([
            f"{case_id}",
            f"  a sh:SPARQLExecutable,",
            f"    {shacl_type};",
        ])
        
        if 'question' in entry:
            comment = entry['question'].replace('"', '\\"')
            ttl.append(f'  rdfs:comment "{comment}";')
        
        predicate = 'sh:ask' if query_type == 'ASK' else 'sh:select'
        ttl.append(f'  {predicate} """')
        ttl.append(query_text)
        ttl.append(f'""" ;')
        ttl.append(f'  schema:target <https://qlever.cs.uni-freiburg.de/> .')
        ttl.append('')
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(ttl))
    
    print(f"✅ Successfully converted {len(data)} queries to: {output_file}")
    print(f"📊 Query statistics:")
    print(f"   - SELECT: {stats.get('SELECT', 0)}")
    print(f"   - ASK: {stats.get('ASK', 0)}")
    print(f"   - OTHER: {stats.get('OTHER', 0)}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: yml-to-ttl-ask.py <input.yml> <output.ttl>")
        print("Example: uv run yml-to-ttl.py ../graphs/dbpedia/dbpedia-examples.yml ../graphs/dbpedia/dbpedia-examples.ttl")
        sys.exit(1)
    
    yml_to_ttl(sys.argv[1], sys.argv[2])