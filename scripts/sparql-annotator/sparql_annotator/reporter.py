"""
ReportGenerator: produces CSV and LaTeX report files from a classified query dataset.

Output files (all prefixed with <prefix>_):
  1. features.csv              — LSQ feature presence matrix (per query)
  2. operators.csv             — SPARQL operator presence matrix (per query)
  3. question_types.csv        — question type classification (per query)
  4. antipatterns.csv          — antipattern detection results (per query)
  5. metrics.csv               — structural metrics (per query)
  6. summary.csv               — aggregate statistics
  7. count_by_question_type.csv — frequency table: queries per question type
  8. count_by_feature.csv       — frequency table: queries per LSQ feature
  9. count_by_operator.csv      — frequency table: queries per SPARQL operator

LaTeX equivalents are generated for files 1, 2, 4, 6 when format includes "latex".
"""

from __future__ import annotations

import csv
import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from rdflib import Graph, URIRef

from .antipatterns import detect_antipatterns, AntipatternIssue
from .classifier import QuestionTypeClassifier
from .algebra import OPERATOR_FEATURES
from .namespaces import LSQV

_log = logging.getLogger(__name__)

_ALL_AP_CODES = ["AP01", "AP02", "AP03", "AP04", "AP05", "AP06", "AP07", "AP08", "AP09"]

# Operator column name → canonical token in OperatorSet.raw
_RAW_MAP: Dict[str, str] = {
    "Optional": "OPTIONAL",
    "Union": "UNION",
    "Filter": "FILTER",
    "FilterExists": "FILTER EXISTS",
    "FilterNotExists": "FILTER NOT EXISTS",
    "Bind": "BIND",
    "Values": "VALUES",
    "Minus": "MINUS",
    "Graph": "GRAPH",
    "Service": "SERVICE",
    "Distinct": "DISTINCT",
    "Reduced": "REDUCED",
    "OrderBy": "ORDER BY",
    "GroupBy": "GROUP BY",
    "Having": "HAVING",
    "Limit": "LIMIT",
    "Offset": "OFFSET",
    "SubQuery": "SUBQUERY",
    "PropertyPath": "PROPERTY_PATH",
}

# Aggregate column name → canonical token in OperatorSet.aggregates
_AGG_MAP: Dict[str, str] = {
    "Count": "COUNT",
    "Sum": "SUM",
    "Avg": "AVG",
    "Min": "MIN",
    "Max": "MAX",
}


# ---------------------------------------------------------------------------
# LaTeX helpers
# ---------------------------------------------------------------------------

_LATEX_PREAMBLE_COMMENT = "% Required packages:\n%   \\usepackage[table]{xcolor}\n%   \\usepackage{booktabs}\n%   \\usepackage{amssymb}  % for $\\checkmark$ and $\\times$ (used in math mode)\n%\n"

_GREEN = r"\cellcolor{green!20}"
_RED = r"\cellcolor{red!10}"
_ORANGE = r"\cellcolor{orange!30}"

# Characters that must be escaped in LaTeX text mode
_LATEX_ESCAPE = str.maketrans(
    {
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
        "\\": r"\textbackslash{}",
    }
)


def _tex(s: str) -> str:
    """Escape a plain-text string for safe use in a LaTeX table cell."""
    return s.translate(_LATEX_ESCAPE)


class _RawLatex(str):
    """A str subclass marking content as already-valid LaTeX — never re-escaped."""


def _latex_bool(val: bool, antipattern: bool = False) -> _RawLatex:
    if antipattern:
        return _RawLatex(f"{_ORANGE}$\\checkmark$" if val else f"{_RED}$\\times$")
    return _RawLatex(f"{_GREEN}$\\checkmark$" if val else f"{_RED}$\\times$")


def _latex_table(
    caption: str,
    label: str,
    headers: List[str],
    rows: List[List[str]],
    transpose: bool = False,
    transpose_header: str = "Feature",
) -> str:
    if transpose and rows:
        # rows become columns: first col is header name, rest are query values
        col_headers = [transpose_header] + [r[0] for r in rows]
        data_rows = []
        for i, h in enumerate(headers[1:], start=1):
            data_rows.append([h] + [r[i] for r in rows])
        headers = col_headers
        rows = data_rows

    col_spec = "l" + "c" * (len(headers) - 1)
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        f"\\caption{{{_tex(caption)}}}",
        f"\\label{{tab:{label}}}",
        f"\\begin{{tabular}}{{{col_spec}}}",
        r"\toprule",
        " & ".join(_tex(h) for h in headers) + r" \\",
        r"\midrule",
    ]
    for row in rows:
        # Only _RawLatex instances (produced by _latex_bool) bypass escaping.
        # Plain str cells — including user-supplied query IDs — are always escaped.
        escaped = [c if isinstance(c, _RawLatex) else _tex(c) for c in row]
        lines.append(" & ".join(escaped) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return _LATEX_PREAMBLE_COMMENT + "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# ReportGenerator
# ---------------------------------------------------------------------------


class ReportGenerator:
    def __init__(
        self,
        ontology_path: str,
        logger: Optional[logging.Logger] = None,
    ):
        self.logger = logger or _log
        self.classifier = QuestionTypeClassifier(
            Path(ontology_path), logger=self.logger
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def generate_reports(
        self,
        query_file: str,
        output_dir: str,
        prefix: str = "report",
        formats: Optional[List[str]] = None,
    ) -> None:
        formats = [f.strip().lower() for f in (formats or ["csv"])]
        unknown = [f for f in formats if f not in ("csv", "latex")]
        if unknown:
            self.logger.warning(f"Ignoring unknown report format(s): {unknown}")
        formats = [f for f in formats if f in ("csv", "latex")]
        if not formats:
            self.logger.info("No valid formats requested; nothing to write.")
            return
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", prefix):
            raise ValueError(
                f"prefix must contain only letters, digits, hyphens, and underscores, got: {prefix!r}"
            )

        # Validate inputs before creating any output directories
        self.logger.info(f"Loading queries from {query_file}")
        data = self._collect(query_file)

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        do_csv = "csv" in formats
        do_latex = "latex" in formats

        if do_csv:
            self._write_features_csv(data, out / f"{prefix}_features.csv")
            self._write_operators_csv(data, out / f"{prefix}_operators.csv")
            self._write_question_types_csv(data, out / f"{prefix}_question_types.csv")
            self._write_antipatterns_csv(data, out / f"{prefix}_antipatterns.csv")
            self._write_metrics_csv(data, out / f"{prefix}_metrics.csv")
            self._write_summary_csv(data, out / f"{prefix}_summary.csv")
            self._write_count_by_question_type_csv(
                data, out / f"{prefix}_count_by_question_type.csv"
            )
            self._write_count_by_feature_csv(
                data, out / f"{prefix}_count_by_feature.csv"
            )
            self._write_count_by_operator_csv(
                data, out / f"{prefix}_count_by_operator.csv"
            )

        if do_latex:
            self._write_features_latex(data, out / f"{prefix}_features.tex", prefix)
            self._write_operators_latex(data, out / f"{prefix}_operators.tex", prefix)
            self._write_summary_latex(data, out / f"{prefix}_summary.tex", prefix)
            self._write_antipatterns_latex(
                data, out / f"{prefix}_antipatterns.tex", prefix
            )

        self.logger.info(f"Reports written to {out}")

    # ------------------------------------------------------------------
    # Data collection
    # ------------------------------------------------------------------

    def _collect(self, query_file: str) -> List[Dict]:
        """Load TTL once, classify, annotate, detect antipatterns. Returns list of row dicts."""
        path = Path(query_file)
        if not path.exists():
            raise FileNotFoundError(f"Query file not found: {query_file}")

        g = Graph()
        try:
            g.parse(str(path), format="turtle")
        except Exception as exc:
            raise ValueError(f"Failed to parse query file {query_file}: {exc}") from exc

        classify_results = self.classifier.classify_queries_from_graph(g)

        rows = []
        for uri_str, (
            qtypes,
            features,
            label,
            warnings,
            counts,
            op_set,
            *_,
        ) in classify_results.items():
            uri = URIRef(uri_str)
            query_id = uri_str.split("/")[-1]

            # SPARQL text
            text: Optional[str] = None
            for lit in g.objects(uri, LSQV.text):
                text = str(lit)
                break

            parse_ok = op_set is not None

            # Antipatterns
            issues: List[AntipatternIssue] = []
            if text:
                issues = detect_antipatterns(text)

            # Classification status
            if len(qtypes) == 0:
                status = "unclassified"
            elif len(qtypes) > 1:
                status = "ambiguous"
            else:
                status = "classified"

            rows.append(
                {
                    "query_id": query_id,
                    "uri": uri_str,
                    "label": label or "",
                    "text": text or "",
                    "parse_ok": parse_ok,
                    "features": features,
                    "qtypes": qtypes,
                    "status": status,
                    "counts": counts,
                    "op_set": op_set,
                    "issues": issues,
                    "warnings": warnings,
                }
            )

        return rows

    # ------------------------------------------------------------------
    # CSV writers
    # ------------------------------------------------------------------

    def _write_features_csv(self, data: List[Dict], path: Path) -> None:
        all_features: List[str] = sorted({f for row in data for f in row["features"]})
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["query_id"] + all_features)
            for row in data:
                w.writerow(
                    [row["query_id"]]
                    + [1 if f in row["features"] else 0 for f in all_features]
                )
        self.logger.info(f"Wrote {path}")

    def _write_operators_csv(self, data: List[Dict], path: Path) -> None:
        # Map CSV column name → how to check presence in OperatorSet
        # graph_patterns/solution_modifiers use uppercase tokens in .raw
        # aggregates uses uppercase names in .aggregates
        op_cols = list(_RAW_MAP) + list(_AGG_MAP)

        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(
                ["query_id", "parse_ok", "query_form"] + op_cols + ["filter_functions"]
            )
            for row in data:
                ops = row["op_set"]
                if ops is None:
                    w.writerow([row["query_id"], 0, ""] + [0] * len(op_cols) + [""])
                    continue
                flags = [1 if _RAW_MAP[c] in ops.raw else 0 for c in _RAW_MAP] + [
                    1 if _AGG_MAP[c] in ops.aggregates else 0 for c in _AGG_MAP
                ]
                w.writerow(
                    [row["query_id"], 1, ops.query_form]
                    + flags
                    + [",".join(sorted(ops.filter_functions))]
                )
        self.logger.info(f"Wrote {path}")

    def _write_question_types_csv(self, data: List[Dict], path: Path) -> None:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(
                [
                    "query_id",
                    "question_types",
                    "classification_status",
                    "ambiguity_reason",
                ]
            )
            for row in data:
                qtypes_str = ",".join(sorted(row["qtypes"]))
                ambiguity = qtypes_str if row["status"] == "ambiguous" else ""
                w.writerow([row["query_id"], qtypes_str, row["status"], ambiguity])
        self.logger.info(f"Wrote {path}")

    def _write_antipatterns_csv(self, data: List[Dict], path: Path) -> None:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(
                ["query_id", "antipatterns"] + _ALL_AP_CODES + ["antipattern_messages"]
            )
            for row in data:
                issues = row["issues"]
                codes = {i.code for i in issues}
                codes_str = ",".join(sorted(codes))
                flags = [1 if c in codes else 0 for c in _ALL_AP_CODES]
                msgs = json.dumps(
                    [
                        {"code": i.code, "message": i.message, "hint": i.hint}
                        for i in issues
                    ]
                )
                w.writerow([row["query_id"], codes_str] + flags + [msgs])
        self.logger.info(f"Wrote {path}")

    def _write_metrics_csv(self, data: List[Dict], path: Path) -> None:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["query_id", "bgp_count", "triple_count", "projected_var_count"])
            for row in data:
                c = row["counts"]
                w.writerow(
                    [
                        row["query_id"],
                        c.get("bgpCount", ""),
                        c.get("tpCount", ""),
                        c.get("projectVarCount", ""),
                    ]
                )
        self.logger.info(f"Wrote {path}")

    def _write_summary_csv(self, data: List[Dict], path: Path) -> None:
        total = len(data)
        valid = sum(1 for r in data if r["parse_ok"])
        classified = sum(1 for r in data if r["status"] == "classified")
        unclassified = sum(1 for r in data if r["status"] == "unclassified")
        ambiguous = sum(1 for r in data if r["status"] == "ambiguous")
        with_ap = sum(1 for r in data if r["issues"])

        ap_counter: Counter = Counter(i.code for r in data for i in r["issues"])
        bgp_vals = [r["counts"].get("bgpCount", 0) for r in data if r["counts"]]
        tp_vals = [r["counts"].get("tpCount", 0) for r in data if r["counts"]]
        pv_vals = [r["counts"].get("projectVarCount", 0) for r in data if r["counts"]]

        def avg(lst):
            return f"{sum(lst) / len(lst):.2f}" if lst else ""

        rows = [
            ("total_queries", total),
            ("valid_queries", valid),
            ("invalid_queries", total - valid),
            ("classified_queries", classified),
            ("unclassified_queries", unclassified),
            ("ambiguous_queries", ambiguous),
            ("queries_with_antipatterns", with_ap),
            ("avg_bgp_count", avg(bgp_vals)),
            ("avg_triple_count", avg(tp_vals)),
            ("avg_projected_vars", avg(pv_vals)),
        ] + [
            (f"queries_with_{code}", ap_counter.get(code, 0)) for code in _ALL_AP_CODES
        ]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["metric", "value"])
            w.writerows(rows)
        self.logger.info(f"Wrote {path}")

    def _write_count_by_question_type_csv(self, data: List[Dict], path: Path) -> None:
        counter: Counter = Counter()
        for row in data:
            for qt in row["qtypes"]:
                counter[qt] += 1
            if not row["qtypes"]:
                counter["(unclassified)"] += 1
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["question_type", "count"])
            for qt, cnt in counter.most_common():
                w.writerow([qt, cnt])
        self.logger.info(f"Wrote {path}")

    def _write_count_by_feature_csv(self, data: List[Dict], path: Path) -> None:
        counter: Counter = Counter(f for row in data for f in row["features"])
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["feature", "count"])
            for feat, cnt in counter.most_common():
                w.writerow([feat, cnt])
        self.logger.info(f"Wrote {path}")

    def _write_count_by_operator_csv(self, data: List[Dict], path: Path) -> None:
        # Counts are derived from LSQ structural features (row["features"]), not from
        # op_set.raw, so they reflect what the TTL declares rather than what the SPARQL
        # parser extracted. This is intentional: it stays consistent with features.csv.
        counter: Counter = Counter(
            f for row in data for f in row["features"] if f in OPERATOR_FEATURES
        )
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["operator", "count"])
            for op, cnt in counter.most_common():
                w.writerow([op, cnt])
        self.logger.info(f"Wrote {path}")

    # ------------------------------------------------------------------
    # LaTeX writers
    # ------------------------------------------------------------------

    def _write_features_latex(self, data: List[Dict], path: Path, prefix: str) -> None:
        all_features = sorted({f for row in data for f in row["features"]})
        headers = ["Query"] + all_features
        rows = [
            [row["query_id"]]
            + [_latex_bool(f in row["features"]) for f in all_features]
            for row in data
        ]
        transpose = len(data) > 20
        tex = _latex_table(
            "LSQ Feature Presence Matrix",
            f"{prefix}:features",
            headers,
            rows,
            transpose=transpose,
            transpose_header="Feature",
        )
        path.write_text(tex, encoding="utf-8")
        self.logger.info(f"Wrote {path}")

    def _write_operators_latex(self, data: List[Dict], path: Path, prefix: str) -> None:
        op_cols = list(_RAW_MAP) + list(_AGG_MAP)
        headers = ["Query", "Form"] + op_cols
        rows = []
        for row in data:
            ops = row["op_set"]
            if ops is None:
                rows.append([row["query_id"], ""] + [_latex_bool(False)] * len(op_cols))
            else:
                flags = [_latex_bool(_RAW_MAP[c] in ops.raw) for c in _RAW_MAP] + [
                    _latex_bool(_AGG_MAP[c] in ops.aggregates) for c in _AGG_MAP
                ]
                rows.append([row["query_id"], ops.query_form] + flags)
        transpose = len(data) > 20
        tex = _latex_table(
            "SPARQL Operator Usage Matrix",
            f"{prefix}:operators",
            headers,
            rows,
            transpose=transpose,
            transpose_header="Operator",
        )
        path.write_text(tex, encoding="utf-8")
        self.logger.info(f"Wrote {path}")

    def _write_summary_latex(self, data: List[Dict], path: Path, prefix: str) -> None:
        # Re-use summary CSV data by building rows inline
        total = len(data)
        classified = sum(1 for r in data if r["status"] == "classified")
        unclassified = sum(1 for r in data if r["status"] == "unclassified")
        ambiguous = sum(1 for r in data if r["status"] == "ambiguous")
        with_ap = sum(1 for r in data if r["issues"])
        feat_counter: Counter = Counter(f for r in data for f in r["features"])
        type_counter: Counter = Counter(qt for r in data for qt in r["qtypes"])

        summary_rows = [
            ["Total queries", str(total)],
            ["Classified", str(classified)],
            ["Unclassified", str(unclassified)],
            ["Ambiguous", str(ambiguous)],
            ["With antipatterns", str(with_ap)],
            [
                "Most common feature",
                feat_counter.most_common(1)[0][0] if feat_counter else "—",
            ],
            [
                "Most common type",
                type_counter.most_common(1)[0][0] if type_counter else "—",
            ],
        ]
        tex = _latex_table(
            "Dataset Summary", f"{prefix}:summary", ["Metric", "Value"], summary_rows
        )
        path.write_text(tex, encoding="utf-8")
        self.logger.info(f"Wrote {path}")

    def _write_antipatterns_latex(
        self, data: List[Dict], path: Path, prefix: str
    ) -> None:
        headers = ["Query"] + _ALL_AP_CODES
        rows = [
            [row["query_id"]]
            + [
                _latex_bool(any(i.code == c for i in row["issues"]), antipattern=True)
                for c in _ALL_AP_CODES
            ]
            for row in data
        ]
        transpose = len(data) > 20
        tex = _latex_table(
            "Antipattern Detection Summary",
            f"{prefix}:antipatterns",
            headers,
            rows,
            transpose=transpose,
            transpose_header="Antipattern",
        )
        path.write_text(tex, encoding="utf-8")
        self.logger.info(f"Wrote {path}")
