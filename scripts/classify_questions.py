#!/usr/bin/env python3
"""
Automated Question Type Classifier for nl2s-bench

This utility parses SPARQL queries in LSQ format and automatically assigns
question types (Factoid, List, Confirmation, etc.) based on declared structural
features. Classification rules are derived dynamically from the qa-types.ttl ontology.

The classifier:
1. Parses the ontology to extract OWL restrictions for each question type
2. Identifies required features (constraints with owl:someValuesFrom)
3. Builds a hierarchy of question types with their feature requirements
4. Matches queries against these requirements using ontology-aware logic

Usage:
    python classify_questions.py --query-file <path/to/queries.ttl> \\
                                 --ontology <path/to/qa-types.ttl> \\
                                 [--output <path/to/output.ttl>]

Author: GitHub Copilot (Claude Haiku 4.5)
Date: 2026-04-08
"""

import argparse
import sys
import logging
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from rdflib import Graph, Namespace, URIRef, RDF, RDFS, Literal
from rdflib.namespace import OWL
import rdflib.plugins.sparql.parser as _sparql_parser


# Namespace definitions
LSQV = Namespace("http://lsq.aksw.org/vocab#")
QAT = Namespace("https://w3id.org/univr-qa/qatypes#")
QA = Namespace("https://w3id.org/wdaqua/qanary#")


@dataclass
class QuestionTypeDefinition:
    """Extracted definition of a question type from the ontology."""
    
    uri: URIRef
    name: str
    required_features: Set[str]  # Features required via owl:someValuesFrom
    parent_types: Set[str]  # Direct rdfs:subClassOf parent type names
    disjoint_with: Set[str]  # Names of types that are owl:disjointWith this type
    warnings: List[str] = field(default_factory=list)  # Any issues encountered during extraction
    
    def __repr__(self):
        features_str = ", ".join(sorted(self.required_features)) if self.required_features else "(none)"
        disjoint_str = ", ".join(sorted(self.disjoint_with)) if self.disjoint_with else "(none)"
        return f"QuestionTypeDefinition(name='{self.name}', features=[{features_str}], disjoint=[{disjoint_str}])"


class QuestionTypeClassifier:
    """Classifies SPARQL queries by question type based on LSQ structural features."""

    def __init__(self, ontology_path: Path, logger: Optional[logging.Logger] = None):
        """
        Initialize classifier by parsing ontology rules dynamically.

        Args:
            ontology_path: Path to qa-types.ttl containing type definitions.
            logger: Optional logger for recording issues and progress.
        """
        self.ontology_path = ontology_path
        self.logger = logger or logging.getLogger(__name__)
        self.ontology = Graph()
        self.ontology.parse(str(ontology_path), format="turtle")
        self.logger.info(f"Loaded ontology from {ontology_path}")

        # Build type definitions from ontology
        self.type_definitions: Dict[str, QuestionTypeDefinition] = self._extract_type_definitions()
        self.type_uris = {name: defn.uri for name, defn in self.type_definitions.items()}
        
        # Build reverse mapping: URI -> name
        self.uri_to_name = {defn.uri: name for name, defn in self.type_definitions.items()}
        
        # Collect warnings from type extraction
        for defn in self.type_definitions.values():
            for warning in defn.warnings:
                self.logger.warning(warning)

    def _extract_type_definitions(self) -> Dict[str, QuestionTypeDefinition]:
        """
        Extract question type definitions from ontology by parsing OWL restrictions.

        For each class that is a subclass of qat:QuestionType, extract:
        1. Required features via owl:Restriction chains (lsqv:hasStructuralFeatures → lsqv:usesFeature)
        2. Parent types via rdfs:subClassOf
        3. Disjoint types via owl:disjointWith

        Returns:
            Dict mapping type name → QuestionTypeDefinition
        """
        definitions = {}
        
        # Find all subclasses of qat:QuestionType
        question_type_class = QAT.QuestionType
        
        for qtype_uri in self.ontology.subjects(RDFS.subClassOf, question_type_class):
            type_name = self._uri_to_type_name(qtype_uri)
            if not type_name:
                continue
            
            # Extract required features
            required_features = self._extract_required_features(qtype_uri)
            
            # Extract parent types
            parent_types = self._extract_parent_types(qtype_uri)
            
            # Extract disjoint types
            disjoint_types = self._extract_disjoint_types(qtype_uri)
            
            warnings = []
            if not required_features:
                warnings.append(f"Type '{type_name}' has no required features defined")
            
            definitions[type_name] = QuestionTypeDefinition(
                uri=qtype_uri,
                name=type_name,
                required_features=required_features,
                parent_types=parent_types,
                disjoint_with=disjoint_types,
                warnings=warnings
            )
        
        return definitions

    def _uri_to_type_name(self, uri: URIRef) -> Optional[str]:
        """
        Extract question type name from URI fragment.
        E.g., https://w3id.org/univr-qa/qatypes#Factoid → "Factoid"
        """
        uri_str = str(uri)
        if "#" in uri_str:
            return uri_str.split("#")[-1]
        return None

    def _extract_required_features(self, qtype_uri: URIRef) -> Set[str]:
        """
        Extract required LSQ features from a question type's OWL restrictions.

        Looks for patterns like:
          [ rdf:type owl:Restriction ;
              owl:onProperty lsqv:hasStructuralFeatures ;
              owl:someValuesFrom [ rdf:type owl:Restriction ;
                  owl:onProperty lsqv:usesFeature ;
                  owl:hasValue lsqv:Select
              ]
          ]
        """
        features = set()
        
        # Get all blank nodes that are rdfs:subClassOf this type
        for restriction_node in self.ontology.objects(qtype_uri, RDFS.subClassOf):
            if not isinstance(restriction_node, URIRef):
                # It's a blank node (BNode)
                features_found = self._extract_features_from_restriction(restriction_node)
                features.update(features_found)
        
        return features

    def _extract_features_from_restriction(self, restriction_node) -> Set[str]:
        """
        Extract features from a single OWL restriction node.
        Handles both direct lsqv:usesFeature and nested restrictions.
        """
        features = set()
        
        # Check if this restriction is about lsqv:hasStructuralFeatures
        on_property = list(self.ontology.objects(restriction_node, OWL.onProperty))
        if not on_property or on_property[0] != LSQV.hasStructuralFeatures:
            return features
        
        # Get the someValuesFrom target
        some_values_from = list(self.ontology.objects(restriction_node, OWL.someValuesFrom))
        if not some_values_from:
            return features
        
        nested_restriction = some_values_from[0]
        
        # Check the nested restriction
        nested_on_property = list(self.ontology.objects(nested_restriction, OWL.onProperty))
        if nested_on_property and nested_on_property[0] == LSQV.usesFeature:
            # Get the hasValue (feature URI)
            has_value = list(self.ontology.objects(nested_restriction, OWL.hasValue))
            if has_value:
                feature_name = self._feature_uri_to_name(has_value[0])
                if feature_name:
                    features.add(feature_name)
        
        return features

    def _feature_uri_to_name(self, feature_uri: URIRef) -> Optional[str]:
        """
        Convert LSQ feature URI to its name.
        E.g., http://lsq.aksw.org/vocab#Select → "Select"
        """
        uri_str = str(feature_uri)
        if "#" in uri_str:
            return uri_str.split("#")[-1]
        return None

    def _extract_parent_types(self, qtype_uri: URIRef) -> Set[str]:
        """
        Extract parent question type names from rdfs:subClassOf.
        Filters out non-question-type parents (like qat:QuestionType itself).
        """
        parents = set()
        
        for parent_uri in self.ontology.objects(qtype_uri, RDFS.subClassOf):
            # Skip blank nodes (restrictions)
            if not isinstance(parent_uri, URIRef):
                continue
            
            # Only include parent question types
            if parent_uri == QAT.QuestionType:
                continue
            
            parent_name = self._uri_to_type_name(parent_uri)
            if parent_name:
                parents.add(parent_name)
        
        return parents

    def _extract_disjoint_types(self, qtype_uri: URIRef) -> Set[str]:
        """
        Extract disjoint type names from owl:disjointWith axioms.
        """
        disjoint = set()
        
        for disjoint_uri in self.ontology.objects(qtype_uri, OWL.disjointWith):
            # Skip blank nodes
            if not isinstance(disjoint_uri, URIRef):
                continue
            
            disjoint_name = self._uri_to_type_name(disjoint_uri)
            if disjoint_name:
                disjoint.add(disjoint_name)
        
        return disjoint

    def classify_query(self, query_uri: URIRef, features: Set[str]) -> Set[str]:
        """
        Classify a single query based on its structural features using ontology rules.

        Algorithm:
        1. Find all question types whose required features are satisfied by the query
        2. Apply disjointness constraints: keep only types that don't conflict
        3. Return the set of all valid, non-conflicting matching types

        This preserves ambiguity for downstream validation. If multiple valid types
        match (e.g., a type and one of its ancestors), all are returned. Validation
        can then flag undecidability or conflicts.

        Args:
            query_uri: RDF URI of the query being classified.
            features: Set of feature names extracted from the query.

        Returns:
            Set of question type names that match without conflicts (possibly empty).
        """
        # Step 1: Find all types whose required features are satisfied
        candidates = []
        
        for type_name, defn in self.type_definitions.items():
            # All required features must be present (subset check)
            if defn.required_features <= features:
                candidates.append(type_name)
        
        if not candidates:
            return set()
        
        # Step 2: Apply disjointness filtering
        # Remove types that are disjoint with ANY other candidate
        valid_types = []
        for type_name in candidates:
            defn = self.type_definitions[type_name]
            # Check if this type conflicts with any other candidate
            conflicts = False
            for other_type in candidates:
                if other_type != type_name and other_type in defn.disjoint_with:
                    conflicts = True
                    break
            if not conflicts:
                valid_types.append(type_name)
        
        return set(valid_types) if valid_types else set()

    def _validate_sparql(self, sparql_text: str, short_uri: str) -> bool:
        """
        Validate SPARQL syntax using rdflib's built-in parser.
        Logs an error if the syntax is invalid.

        Returns:
            True if valid, False if invalid.
        """
        try:
            _sparql_parser.parseQuery(sparql_text)
            return True
        except Exception as e:
            self.logger.error(f"Query {short_uri!r}: invalid SPARQL syntax — {e}")
            return False

    def extract_features(self, query_graph: Graph, query_uri: URIRef) -> Set[str]:
        """
        Extract structural features from a query in LSQ format.
        Also validates SPARQL syntax via rdflib parser and detects pure-aggregate
        queries (Aggregators present, no GroupBy, no unbound projected variables),
        removing spurious lsqv:Distinct from the feature set in that case.

        Args:
            query_graph: RDF graph containing the query.
            query_uri: URI of the lsqv:Query resource.

        Returns:
            Set of feature names (e.g., {'Select', 'Distinct', 'TriplePattern'}).
        """
        features_set = set()
        short_uri = str(query_uri).split("/")[-1]

        # Get structural features resource via lsqv:hasStructuralFeatures
        for sf_uri in query_graph.objects(query_uri, LSQV.hasStructuralFeatures):
            # Get all features from lsqv:usesFeature
            for feature_uri in query_graph.objects(sf_uri, LSQV.usesFeature):
                # Map URI back to feature name
                feature_name = self._feature_uri_to_name(feature_uri)
                if feature_name:
                    features_set.add(feature_name)

        # Validate SPARQL syntax and detect pure-aggregate queries
        for sparql_text in query_graph.objects(query_uri, LSQV.text):
            text = str(sparql_text)
            self._validate_sparql(text, short_uri)

            # Pure-aggregate detection: Aggregators present, no GroupBy, only
            # aggregate expressions in projection (no bare variables).
            # In this case lsqv:Distinct was fired by COUNT(DISTINCT ...) inside
            # the aggregate, not by SELECT DISTINCT — strip it to avoid false
            # List matches.
            if (
                "Aggregators" in features_set
                and "GroupBy" not in features_set
                and "Distinct" in features_set
            ):
                try:
                    parsed = _sparql_parser.parseQuery(text)
                    # parsed[1] is the SelectQuery/AskQuery body
                    select_clause = parsed[1].get("projection", [])
                    # A pure-aggregate projection has no bare variables —
                    # every projected item carries an expression (the aggregate).
                    has_bare_var = any(
                        item.get("var") is not None and item.get("evar") is None
                        for item in select_clause
                        if hasattr(item, "get")
                    )
                    if not has_bare_var:
                        self.logger.debug(
                            f"Query {short_uri!r}: removing spurious Distinct "
                            f"(COUNT(DISTINCT ...) inside aggregate, no bare variables)"
                        )
                        features_set.discard("Distinct")
                except Exception:
                    pass  # parser already logged the error above; leave features unchanged

        return features_set

    def classify_queries_from_file(
        self, query_file: Path
    ) -> Dict[str, Tuple[Set[str], Set[str], Optional[str]]]:
        """
        Classify all queries in an LSQ-format Turtle file.

        Args:
            query_file: Path to Turtle file containing lsqv:Query resources.

        Returns:
            Dict mapping query URI → (matching_types_set, features_set, label).
            matching_types_set contains all valid types that satisfy constraints and don't conflict.
        """
        query_graph = Graph()
        try:
            query_graph.parse(str(query_file), format="turtle")
            self.logger.info(f"Loaded {len(query_graph)} triples from {query_file}")
        except Exception as e:
            self.logger.error(f"Failed to parse query file {query_file}: {e}")
            raise

        results = {}
        query_uris = list(query_graph.subjects(RDF.type, LSQV.Query))
        self.logger.info(f"Found {len(query_uris)} queries to classify")

        # Find all lsqv:Query resources
        for query_uri in query_uris:
            features = self.extract_features(query_graph, query_uri)
            qtypes = self.classify_query(query_uri, features)

            query_label = None
            for label in query_graph.objects(query_uri, RDFS.label):
                query_label = str(label)
                break

            # Log classification result
            short_uri = str(query_uri).split("/")[-1]
            if qtypes:
                self.logger.debug(f"Query {short_uri}: classified as {', '.join(sorted(qtypes))}")
            else:
                self.logger.warning(f"Query {short_uri}: unclassifiable (features: {', '.join(sorted(features)) if features else 'none'})")

            # Store result with humanized label
            results[str(query_uri)] = (qtypes, features, query_label)

        return results

    def _is_valid_hierarchy(self, type_names: Set[str]) -> bool:
        """
        Check if a set of matching types forms a valid class hierarchy.
        Valid if one type is an ancestor of all others (transitive subClassOf).
        
        Args:
            type_names: Set of matched type names.
        
        Returns:
            True if types form a valid hierarchy, False if there are conflicts.
        """
        if len(type_names) <= 1:
            return True
        
        # Build ancestry for each type
        def _get_ancestors(type_name: str) -> Set[str]:
            ancestors = set()
            visited = set()
            
            def visit(tname: str):
                if tname in visited:
                    return
                visited.add(tname)
                if tname in self.type_definitions:
                    for parent in self.type_definitions[tname].parent_types:
                        ancestors.add(parent)
                        visit(parent)
            
            visit(type_name)
            return ancestors
        
        # Check if types form a chain: for each type, all other types should be 
        # either ancestors or descendants (transitive subclass relationship)
        for t1 in type_names:
            ancestors_t1 = _get_ancestors(t1)
            for t2 in type_names:
                if t1 == t2:
                    continue
                # t2 must be an ancestor of t1 OR t1 must be an ancestor of t2
                ancestors_t2 = _get_ancestors(t2)
                if not (t2 in ancestors_t1 or t1 in ancestors_t2):
                    # t1 and t2 are not in hierarchy—ambiguous
                    return False
        
        return True

    def _find_most_specific(self, type_names: Set[str]) -> str:
        """
        Find the most specific (deepest in hierarchy) type from a set.
        Prefers the type with the most required features.
        """
        return max(
            type_names,
            key=lambda t: len(self.type_definitions[t].required_features)
        )

    def generate_type_assertions(
        self, query_file: Path, results: Dict[str, Tuple[Set[str], Set[str], Optional[str]]]
    ) -> Graph:
        """
        Generate RDF assertions adding question type classes to query resources.

        Args:
            query_file: Path to original query file.
            results: Dict mapping query URI → (matching_types_set, features_set, label).

        Returns:
            RDF Graph with added rdf:type assertions.
        """
        output_graph = Graph()
        output_graph.parse(str(query_file), format="turtle")
        
        # Bind namespaces to prefixes for proper output formatting
        output_graph.bind("lsqv", LSQV)
        output_graph.bind("qat", QAT)
        output_graph.bind("qa", QA)
        output_graph.bind("rdf", RDF)
        output_graph.bind("rdfs", RDFS)
        output_graph.bind("owl", OWL)

        # Add type assertions for all matching types
        assertions_added = 0
        for query_uri_str, (qtypes, features, label) in results.items():
            if qtypes:
                query_uri = URIRef(query_uri_str)
                for qtype in qtypes:
                    type_uri = self.type_uris.get(qtype)
                    if type_uri:
                        output_graph.add((query_uri, RDF.type, type_uri))
                        assertions_added += 1
        
        self.logger.info(f"Added {assertions_added} type assertions to {len(results)} queries")
        return output_graph

    def print_results(self, results: Dict[str, Tuple[Set[str], Set[str], Optional[str]]]):
        """Pretty-print classification results with validation reporting."""
        print("\n" + "=" * 80)
        print("Question Type Classification Results")
        print("=" * 80 + "\n")
        
        # Print ontology rules
        print("📖 Extracted ontology rules:")
        print("-" * 80)
        for type_name in sorted(self.type_definitions.keys()):
            defn = self.type_definitions[type_name]
            features_str = ", ".join(sorted(defn.required_features)) if defn.required_features else "(none)"
            disjoint_str = ", ".join(sorted(defn.disjoint_with)) if defn.disjoint_with else "(none)"
            print(f"  {type_name:20s} | Features: {features_str:40s} | Disjoint: {disjoint_str}")
        print()

        # Categorize results
        unique = {}
        hierarchical = []
        ambiguous = []
        unclassifiable = []

        for uri_str, (qtypes, features, label) in results.items():
            if not qtypes:
                unclassifiable.append((uri_str, features, label))
            elif len(qtypes) == 1:
                qtype = list(qtypes)[0]
                if qtype not in unique:
                    unique[qtype] = []
                unique[qtype].append((uri_str, features, label))
            else:
                # Multiple matches: check if they form a valid hierarchy
                if self._is_valid_hierarchy(qtypes):
                    # Valid hierarchy: parent + child(ren) matched
                    most_specific = self._find_most_specific(qtypes)
                    ancestors = [t for t in qtypes if t != most_specific]
                    hierarchical.append((uri_str, most_specific, ancestors, features, label))
                else:
                    # Real ambiguity: non-hierarchical siblings or conflicts
                    ambiguous.append((uri_str, qtypes, features, label))

        # Print uniquely classified
        print("✓ Uniquely classified queries:")
        print("-" * 80)
        unique_count = 0
        for qtype in sorted(self.type_definitions.keys()):
            if qtype in unique:
                print(f"\n  {qtype}")
                for uri, features, label in unique[qtype]:
                    unique_count += 1
                    short_uri = uri.split("/")[-1] if "/" in uri else uri
                    print(f"    {short_uri:20s} | {label or '(no label)'}")
                    print(f"    {' ' * 20} | Matched: {', '.join(sorted(features))}")

        # Print hierarchical classifications
        if hierarchical:
            print(f"\n✓ Valid hierarchical classifications ({len(hierarchical)}):")
            print("-" * 80)
            for uri, most_specific, ancestors, features, label in hierarchical:
                short_uri = uri.split("/")[-1] if "/" in uri else uri
                ancestors_str = " ← ".join([most_specific] + sorted(ancestors))
                print(f"  {short_uri:20s} | {label or '(no label)'}")
                print(f"  {' ' * 20} | Hierarchy: {ancestors_str}")
                print(f"  {' ' * 20} | Features: {', '.join(sorted(features))}")
            unique_count += len(hierarchical)

        # Print ambiguous cases (real undecidability)
        if ambiguous:
            print(f"\n⚠️  Ambiguous/Undecidable queries ({len(ambiguous)}):")
            print("-" * 80)
            for uri, qtypes, features, label in ambiguous:
                short_uri = uri.split("/")[-1] if "/" in uri else uri
                types_str = ", ".join(sorted(qtypes))
                print(f"  {short_uri:20s} | {label or '(no label)'}")
                print(f"  {' ' * 20} | Conflicting types: {types_str}")
                print(f"  {' ' * 20} | Features: {', '.join(sorted(features))}")
                self.logger.warning(
                    f"Ambiguous query {short_uri!r}: conflicting types [{types_str}]"
                    f" — features: {', '.join(sorted(features))}"
                )

        # Print unclassifiable
        if unclassifiable:
            print(f"\n❌ Unclassifiable queries ({len(unclassifiable)}):")
            print("-" * 80)
            for uri, features, label in unclassifiable:
                short_uri = uri.split("/")[-1] if "/" in uri else uri
                print(f"  {short_uri:20s} | {label or '(no label)'}")
                print(f"  {' ' * 20} | Features: {', '.join(sorted(features))}")
                self.logger.error(
                    f"Unclassifiable query {short_uri!r}: no matching type"
                    f" — features: {', '.join(sorted(features)) if features else 'none'}"
                )

        print("\n" + "=" * 80 + "\n")

        # Summary with validation info
        total = len(results)
        ambig_count = len(ambiguous)
        unclass_count = len(unclassifiable)
        
        print(f"Summary:")
        print(f"  Total queries: {total}")
        print(f"  Valid classifications: {unique_count} ({100*unique_count//total if total else 0}%)")
        if unique_count > 0:
            unique_only = len([1 for u in unique.values() for _ in u])
            hier_only = len(hierarchical)
            print(f"    - Unique: {unique_only}")
            print(f"    - Hierarchical: {hier_only}")
        print(f"  Ambiguous/Conflicting: {ambig_count} ({100*ambig_count//total if total else 0}%)")
        print(f"  Unclassifiable: {unclass_count} ({100*unclass_count//total if total else 0}%)")
        print()

        # Log validation summary
        self.logger.info(
            f"Validation complete — {total} queries: "
            f"{unique_count} classified ({100*unique_count//total if total else 0}%), "
            f"{ambig_count} ambiguous, "
            f"{unclass_count} unclassifiable"
        )
        if ambig_count == 0 and unclass_count == 0:
            self.logger.info("Validation passed: no problems detected")
        else:
            if ambig_count > 0:
                self.logger.warning(f"Validation issue: {ambig_count} ambiguous/conflicting quer{'y' if ambig_count == 1 else 'ies'} require manual review")
            if unclass_count > 0:
                self.logger.error(f"Validation issue: {unclass_count} quer{'y' if unclass_count == 1 else 'ies'} could not be classified")


def setup_logging(log_file: Optional[Path] = None, verbose: bool = False) -> logging.Logger:
    """
    Set up logging configuration.
    
    Args:
        log_file: Optional path to log file. If provided, logs to both console and file.
        verbose: If True, use DEBUG level; otherwise use INFO level.
    
    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger("classify_questions")
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # File handler (if requested)
    if log_file:
        file_handler = logging.FileHandler(log_file, mode="w")
        file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file
        file_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    
    return logger


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Classify SPARQL queries by question type using LSQ features.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Classify queries and print results
  python classify_questions.py \\
    --query-file graphs/ck25/ck25-queries.ttl \\
    --ontology graphs/qa-types.ttl

  # Classify and save output with type assertions and log file
  python classify_questions.py \\
    --query-file graphs/ck25/ck25-queries.ttl \\
    --ontology graphs/qa-types.ttl \\
    --output graphs/ck25/ck25-queries-classified.ttl \\
    --log-file .temp/classification.log
        """,
    )

    parser.add_argument(
        "--query-file",
        type=Path,
        required=True,
        help="Path to LSQ query file (Turtle format).",
    )
    parser.add_argument(
        "--ontology",
        type=Path,
        required=True,
        help="Path to qa-types.ttl ontology.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output file for classified queries (adds rdf:type assertions).",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Optional log file to record messages, warnings, and errors (default: log to console only).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) output.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print extracted ontology rules before classification.",
    )

    args = parser.parse_args()
    
    # Set up logging
    logger = setup_logging(log_file=args.log_file, verbose=args.verbose)
    
    logger.info("Starting question type classification...")
    logger.info(f"Query file: {args.query_file}")
    logger.info(f"Ontology file: {args.ontology}")
    if args.output:
        logger.info(f"Output file: {args.output}")
    if args.log_file:
        logger.info(f"Log file: {args.log_file}")

    # Validate inputs
    if not args.query_file.exists():
        logger.error(f"Query file not found: {args.query_file}")
        sys.exit(1)
    if not args.ontology.exists():
        logger.error(f"Ontology file not found: {args.ontology}")
        sys.exit(1)

    try:
        classifier = QuestionTypeClassifier(args.ontology, logger=logger)
        
        # Debug: print extracted rules
        if args.debug:
            logger.info("\n📖 Extracted Ontology Rules:")
            logger.info("=" * 80)
            for type_name in sorted(classifier.type_definitions.keys()):
                defn = classifier.type_definitions[type_name]
                features_str = ", ".join(sorted(defn.required_features)) if defn.required_features else "(none)"
                disjoint_str = ", ".join(sorted(defn.disjoint_with)) if defn.disjoint_with else "(none)"
                logger.info(f"  {type_name:20s}")
                logger.info(f"    Features: {features_str}")
                logger.info(f"    Disjoint with: {disjoint_str}")
            logger.info("=" * 80 + "\n")

        logger.info(f"Classifying queries from {args.query_file}...")
        results = classifier.classify_queries_from_file(args.query_file)

        # Display results
        classifier.print_results(results)

        # Save output if requested
        if args.output:
            logger.info(f"Writing classified queries to {args.output}...")
            output_graph = classifier.generate_type_assertions(args.query_file, results)
            output_graph.serialize(destination=str(args.output), format="turtle")
            # Count total type assertions
            total_assertions = sum(len(qtypes) for qtypes, _, _ in results.values() if qtypes)
            logger.info(f"✓ Saved {len(results)} queries with {total_assertions} total type assertions.")
        
        logger.info("Classification completed successfully.")
    
    except Exception as e:
        logger.exception(f"Error during classification: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
