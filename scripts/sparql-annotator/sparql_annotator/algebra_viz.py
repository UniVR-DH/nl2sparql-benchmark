"""
Graphviz visualization of a rdflib SPARQL algebra tree.

Public API
----------
render_algebra_dot(algebra_node, sparql_text=None, show_query=True) -> graphviz.Digraph
save_algebra_viz(algebra_node, output_path, fmt="svg", sparql_text=None, show_query=True) -> Path
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.term import URIRef, Literal, Variable as _Variable

_COLOURS = {
    "BGP": "#D4EDDA",
    "Filter": "#FFF3CD",
    "LeftJoin": "#CCE5FF",
    "Union": "#E2D9F3",
    "Minus": "#F8D7DA",
    "AggregateJoin": "#FDEBD0",
    "Group": "#FDEBD0",
    "Extend": "#EAF2FB",
    "Project": "#EAFAF1",
    "Distinct": "#EAFAF1",
    "OrderBy": "#EAFAF1",
    "Slice": "#EAFAF1",
    "ToMultiSet": "#F9F9F9",
    "SelectQuery": "#D6EAF8",
    "Builtin_NOTEXISTS": "#F8D7DA",
    "Builtin_EXISTS": "#FFF3CD",
    "GroupGraphPatternSub": "#F5F5F5",
    "TriplesBlock": "#D4EDDA",
}
_DEFAULT_COLOUR = "#F5F5F5"


def _short(value) -> str:
    """Compact, human-readable label for any rdflib term or plain value."""
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


def _esc(s: str) -> str:
    """Escape for plain Graphviz string labels."""
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("<", "\\<")
        .replace(">", "\\>")
    )


def _html_esc(s: str) -> str:
    """Escape for Graphviz HTML-like labels."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_algebra_dot(
    algebra_node: CompValue,
    sparql_text: Optional[str] = None,
    show_query: bool = True,
):
    """Return a graphviz.Digraph for the algebra tree.

    BGP triple patterns are rendered as compact inline labels ("?s rdf:type C").
    If *sparql_text* is provided and *show_query* is True, the raw SPARQL is
    shown in a white panel to the right of the tree.
    """
    try:
        from graphviz import Digraph
    except ImportError:
        raise ImportError("graphviz package required: uv pip install graphviz")

    dot = Digraph("sparql_algebra")
    dot.attr(rankdir="TB", splines="ortho", nodesep="0.4", ranksep="0.6")
    dot.attr(
        "node", shape="box", style="rounded,filled", fontname="Helvetica", fontsize="11"
    )
    dot.attr("edge", arrowsize="0.7", fontname="Helvetica", fontsize="9")

    _counter = [0]

    def _nid() -> str:
        _counter[0] += 1
        return f"n{_counter[0]}"

    def _add_node(node, parent_id: Optional[str] = None, edge_label: str = "") -> str:
        nid = _nid()

        if isinstance(node, CompValue):
            colour = _COLOURS.get(node.name, _DEFAULT_COLOUR)
            dot.node(nid, _esc(node.name), fillcolor=colour)
            if parent_id:
                dot.edge(parent_id, nid, label=_esc(edge_label))

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
                        elif isinstance(item, tuple):
                            # BGP triple pattern — render as "s p o" inline
                            triple_label = "  ".join(_short(x) for x in item)
                            leaf = _nid()
                            dot.node(
                                leaf,
                                _esc(triple_label),
                                shape="oval",
                                fillcolor="#EEEEEE",
                                fontname="Courier",
                                fontsize="9",
                            )
                            dot.edge(nid, leaf, label=_esc(lbl))
                        elif item is not None:
                            leaf = _nid()
                            dot.node(
                                leaf,
                                _esc(_short(item)),
                                shape="oval",
                                fillcolor="#EEEEEE",
                            )
                            dot.edge(nid, leaf, label=_esc(lbl))

                elif isinstance(v, tuple):
                    # standalone tuple (e.g. a single triple pattern)
                    triple_label = "  ".join(_short(x) for x in v)
                    leaf = _nid()
                    dot.node(
                        leaf,
                        _esc(triple_label),
                        shape="oval",
                        fillcolor="#EEEEEE",
                        fontname="Courier",
                        fontsize="9",
                    )
                    dot.edge(nid, leaf, label=_esc(k))

                elif v is not None:
                    leaf = _nid()
                    dot.node(leaf, _esc(_short(v)), shape="oval", fillcolor="#EEEEEE")
                    dot.edge(nid, leaf, label=_esc(k))

        elif isinstance(node, (list, tuple)):
            dot.node(nid, "[ ]", fillcolor=_DEFAULT_COLOUR)
            if parent_id:
                dot.edge(parent_id, nid, label=_esc(edge_label))
            for i, item in enumerate(node):
                _add_node(item, nid, str(i))

        else:
            dot.node(nid, _esc(_short(node)), shape="oval", fillcolor="#EEEEEE")
            if parent_id:
                dot.edge(parent_id, nid, label=_esc(edge_label))

        return nid

    # Build the algebra tree subgraph
    with dot.subgraph(name="cluster_algebra") as alg_sg:
        alg_sg.attr(label="", style="invis")
        _add_node(algebra_node)

    # Query text panel — white box, right side, good internal padding
    if show_query and sparql_text:
        lines = sparql_text.strip().splitlines()
        rows = "".join(
            f'<TR><TD ALIGN="LEFT" BALIGN="LEFT">'
            f'<FONT FACE="Courier" POINT-SIZE="10">{_html_esc(line) or " "}</FONT>'
            f"</TD></TR>"
            for line in lines
        )
        html_label = (
            '<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="2" CELLPADDING="4">'
            '<TR><TD ALIGN="LEFT"><B><FONT POINT-SIZE="11">SPARQL Query</FONT></B></TD></TR>'
            + rows
            + "</TABLE>>"
        )
        with dot.subgraph(name="cluster_query") as sg:
            sg.attr(
                label="",
                style="rounded,filled",
                fillcolor="white",
                color="#CCCCCC",
                margin="16",
                rank="same",
            )
            sg.node("sparql_text", html_label, shape="none", margin="0")

        # Invisible edge to push the query box to the right of the tree
        dot.edge("n1", "sparql_text", style="invis")

    return dot


def save_algebra_viz(
    algebra_node: CompValue,
    output_path: str | Path,
    fmt: str = "svg",
    sparql_text: Optional[str] = None,
    show_query: bool = True,
) -> Path:
    """Render and save to output_path. fmt: 'dot' | 'svg' | 'png'."""
    output_path = Path(output_path)
    dot = render_algebra_dot(
        algebra_node, sparql_text=sparql_text, show_query=show_query
    )

    if fmt == "dot":
        out = output_path.with_suffix(".dot")
        out.write_text(dot.source, encoding="utf-8")
        return out

    stem = output_path.with_suffix("")
    dot.render(str(stem), format=fmt, cleanup=True)
    return stem.with_suffix(f".{fmt}")
