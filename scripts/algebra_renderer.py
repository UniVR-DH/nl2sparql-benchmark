from rdflib import Graph, Namespace, URIRef, RDF, RDFS, Literal
from rdflib.namespace import OWL
from rdflib.term import Variable as _Variable
import rdflib.plugins.sparql.parser as _sparql_parser
import rdflib.plugins.sparql.algebra as _sparql_algebra
from rdflib.plugins.sparql.parserutils import CompValue

from graphviz import Digraph
from IPython.display import SVG, display

QUERY = """
PREFIX prodi: <http://ld.company.org/prod-instances/>
PREFIX pv: <http://ld.company.org/prod-vocab/>

SELECT ?name (COUNT(?emp) AS ?numEmployees)
WHERE {
  ?dept a pv:Department ;
    pv:name ?name .
  ?emp a pv:Employee ;
    pv:memberOf ?dept .
}
GROUP BY ?dept ?name
HAVING (COUNT(?emp) > 5)
"""

def to_algebra(query_text):
    parsed = _sparql_parser.parseQuery(query_text)
    q = _sparql_algebra.translateQuery(parsed)
    return q.algebra if hasattr(q, "algebra") else q.p

def render_tree(node, indent=0):
    pad = "  " * indent
    if isinstance(node, CompValue):
        lines = [f"{pad}{node.name}("]
        for k, v in node.items():
            lines.append(f"{pad}  {k} = {render_tree(v, indent + 1)}")
        lines.append(f"{pad})")
        return "\n".join(lines)
    if isinstance(node, list):
        return "[\n" + "\n".join(render_tree(x, indent + 1) for x in node) + f"\n{pad}]"
    if isinstance(node, tuple):
        return "(" + ", ".join(render_tree(x, indent + 1) for x in node) + ")"
    if isinstance(node, _Variable):
        return f"?{node}"
    if isinstance(node, URIRef):
        return f"<{node}>"
    if isinstance(node, Literal):
        return node.n3()
    return repr(node)

def dot_label(x):
    if isinstance(x, CompValue):
        return x.name
    if isinstance(x, _Variable):
        return f"?{x}"
    if isinstance(x, URIRef):
        return f"<{x}>"
    if isinstance(x, Literal):
        return x.n3()
    if x is None:
        return "None"
    return str(x)

def build_dot(node):
    dot = Digraph("sparql_algebra")
    dot.attr(rankdir="TB", splines="ortho", nodesep="0.35", ranksep="0.5")
    dot.attr("node", shape="box", style="rounded,filled", fillcolor="#E8F1FF", fontname="Helvetica")
    dot.attr("edge", arrowsize="0.7", fontname="Helvetica")

    counter = [0]

    def add(n, parent=None, edge_label=None):
        counter[0] += 1
        nid = f"n{counter[0]}"
        if isinstance(n, CompValue):
            dot.node(nid, dot_label(n))
            if parent:
                dot.edge(parent, nid, label=edge_label or "")
            for k, v in n.items():
                counter[0] += 1
                kid = f"n{counter[0]}"
                dot.node(kid, str(k), shape="ellipse", style="filled", fillcolor="#EDEDED")
                dot.edge(nid, kid)
                add(v, kid)
        elif isinstance(n, dict):
            dot.node(nid, "dict", shape="box")
            if parent:
                dot.edge(parent, nid, label=edge_label or "")
            for k, v in n.items():
                counter[0] += 1
                kid = f"n{counter[0]}"
                dot.node(kid, str(k), shape="ellipse", style="filled", fillcolor="#EDEDED")
                dot.edge(nid, kid)
                add(v, kid)
        elif isinstance(n, (list, tuple)):
            dot.node(nid, type(n).__name__, shape="box")
            if parent:
                dot.edge(parent, nid, label=edge_label or "")
            for i, item in enumerate(n):
                counter[0] += 1
                kid = f"n{counter[0]}"
                dot.node(kid, f"[{i}]", shape="ellipse", style="filled", fillcolor="#EDEDED")
                dot.edge(nid, kid)
                add(item, kid)
        else:
            dot.node(nid, dot_label(n), shape="oval")
            if parent:
                dot.edge(parent, nid, label=edge_label or "")
        return nid

    add(node)
    return dot

algebra = to_algebra(QUERY)
print(render_tree(algebra))

dot = build_dot(algebra)
display(SVG(dot.pipe(format="svg")))
dot.render("sparql_algebra", format="png", cleanup=True)
#print(dot.source)