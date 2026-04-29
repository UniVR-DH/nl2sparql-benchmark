"""
Graphviz visualization of a rdflib SPARQL algebra tree.

Public API
----------
render_algebra_dot(algebra_node, parsed=None) -> graphviz.Digraph
    Build a Digraph from an algebra tree (and optionally the parse tree for
    FILTER NOT EXISTS / FILTER EXISTS inner patterns).

save_algebra_viz(algebra_node, parsed, output_path, fmt="svg")
    Render and save to .dot / .svg / .png.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.term import URIRef, Literal, Variable as _Variable

# Node colours by algebra node type
_COLOURS = {
    "BGP":            "#D4EDDA",   # green  — triple patterns
    "Filter":         "#FFF3CD",   # yellow — filter
    "LeftJoin":       "#CCE5FF",   # blue   — optional
    "Union":          "#E2D9F3",   # purple — union
    "Minus":          "#F8D7DA",   # red    — minus
    "AggregateJoin":  "#FDEBD0",   # orange — aggregation
    "Group":          "#FDEBD0",
    "Extend":         "#EAF2FB",   # light blue — bind/alias
    "Project":        "#EAFAF1",
    "Distinct":       "#EAFAF1",
    "OrderBy":        "#EAFAF1",
    "Slice":          "#EAFAF1",
    "ToMultiSet":     "#F9F9F9",
    "SelectQuery":    "#D6EAF8",
    "Builtin_NOTEXISTS": "#F8D7DA",
    "Builtin_EXISTS":    "#FFF3CD",
    "GroupGraphPatternSub": "#F5F5F5",
    "TriplesBlock":   "#D4EDDA",
}
_DEFAULT_COLOUR = "#F5F5F5"


def _short(value) -> str:
    """Compact label for a leaf value."""
    if isinstance(value, _Variable):
        return f"?{value}"
    if isinstance(value, URIRef):
        s = str(value)
        return s.split("#")[-1] if "#" in s else s.split("/")[-1]
    if isinstance(value, Literal):
        return value.n3()
    if value is None:
        return "∅"
    s = str(value)
    return s if len(s) <= 40 else s[:37] + "…"


def _escape(s: str) -> str:
    return s.replace('"', '\\"').replace("\n", "\\n").replace("<", "\\<").replace(">", "\\>")


def render_algebra_dot(algebra_node: CompValue, parsed: object = None):
    """Return a graphviz.Digraph for the algebra tree."""
    try:
        from graphviz import Digraph
    except ImportError:
        raise ImportError("graphviz package required: uv add graphviz")

    dot = Digraph("sparql_algebra")
    dot.attr(rankdir="TB", splines="ortho", nodesep="0.4", ranksep="0.6")
    dot.attr("node", shape="box", style="rounded,filled", fontname="Helvetica", fontsize="11")
    dot.attr("edge", arrowsize="0.7", fontname="Helvetica", fontsize="9")

    _counter = [0]

    def _nid() -> str:
        _counter[0] += 1
        return f"n{_counter[0]}"

    def _add_node(node, parent_id: Optional[str] = None, edge_label: str = "") -> str:
        nid = _nid()

        if isinstance(node, CompValue):
            colour = _COLOURS.get(node.name, _DEFAULT_COLOUR)
            dot.node(nid, _escape(node.name), fillcolor=colour)
            if parent_id:
                dot.edge(parent_id, nid, label=_escape(edge_label))

            for k, v in node.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, CompValue):
                    _add_node(v, nid, k)
                elif isinstance(v, list):
                    for i, item in enumerate(v):
                        lbl = f"{k}[{i}]" if len(v) > 1 else k
                        if isinstance(item, CompValue):
                            _add_node(item, nid, lbl)
                        elif item is not None:
                            leaf = _nid()
                            dot.node(leaf, _escape(_short(item)), shape="oval",
                                     fillcolor="#EEEEEE")
                            dot.edge(nid, leaf, label=_escape(lbl))
                elif isinstance(v, tuple):
                    for i, item in enumerate(v):
                        if isinstance(item, CompValue):
                            _add_node(item, nid, f"{k}[{i}]")
                        elif item is not None:
                            leaf = _nid()
                            dot.node(leaf, _escape(_short(item)), shape="oval",
                                     fillcolor="#EEEEEE")
                            dot.edge(nid, leaf, label=_escape(f"{k}[{i}]"))
                elif v is not None:
                    leaf = _nid()
                    dot.node(leaf, _escape(_short(v)), shape="oval", fillcolor="#EEEEEE")
                    dot.edge(nid, leaf, label=_escape(k))

        elif isinstance(node, (list, tuple)):
            dot.node(nid, "[ ]", fillcolor=_DEFAULT_COLOUR)
            if parent_id:
                dot.edge(parent_id, nid, label=_escape(edge_label))
            for i, item in enumerate(node):
                _add_node(item, nid, str(i))

        else:
            dot.node(nid, _escape(_short(node)), shape="oval", fillcolor="#EEEEEE")
            if parent_id:
                dot.edge(parent_id, nid, label=_escape(edge_label))

        return nid

    _add_node(algebra_node)
    return dot


def save_algebra_viz(
    algebra_node: CompValue,
    output_path: str | Path,
    fmt: str = "svg",
) -> Path:
    """Render the algebra tree and save to *output_path*.

    fmt: "dot" | "svg" | "png"
    The file extension of output_path is set to fmt automatically.
    """
    output_path = Path(output_path)
    dot = render_algebra_dot(algebra_node)

    if fmt == "dot":
        out = output_path.with_suffix(".dot")
        out.write_text(dot.source, encoding="utf-8")
        return out

    # svg / png — graphviz renders to a temp file then we move it
    stem = output_path.with_suffix("")
    dot.render(str(stem), format=fmt, cleanup=True)
    return stem.with_suffix(f".{fmt}")
