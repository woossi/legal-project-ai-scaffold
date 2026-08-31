"""project_core.json에서 Project Core TBox를 생성한다."""

import json
from pathlib import Path

from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, OWL, SKOS
from rdflib.collection import Collection


TIME = Namespace("http://www.w3.org/2006/time#")
CONCEPT_SCHEMES = {
    "assertionKinds": "AssertionKindScheme",
    "conditionStatuses": "ConditionStatusScheme",
    "availabilityStatuses": "AvailabilityStatusScheme",
    "eventTypes": "EventTypeScheme",
}


def _label(graph: Graph, iri, label: str) -> None:
    graph.add((iri, RDFS.label, Literal(label, lang="ko")))


def _expression(graph: Graph, namespace: Namespace, names: list[str]):
    if len(names) == 1:
        return TIME.Interval if names[0] == "time:Interval" else namespace[names[0]]
    union = BNode()
    members = BNode()
    graph.add((union, OWL.unionOf, members))
    Collection(graph, members, [
        TIME.Interval if name == "time:Interval" else namespace[name] for name in names
    ])
    return union


def _add_concepts(graph: Graph, namespace: Namespace, values: list[str], scheme_name: str) -> None:
    scheme = namespace[scheme_name]
    graph.add((scheme, RDF.type, SKOS.ConceptScheme))
    _label(graph, scheme, scheme_name)
    for value in values:
        concept = namespace[value]
        graph.add((concept, RDF.type, SKOS.Concept))
        graph.add((concept, SKOS.inScheme, scheme))
        _label(graph, concept, value)


def build(contract_path: Path, output_path: Path) -> dict[str, int]:
    """계약의 클래스·관계·통제어휘를 Turtle TBox로 기록한다."""
    contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    namespace = Namespace(contract["namespace"])
    graph = Graph()
    graph.bind("lp", namespace)
    graph.bind("owl", OWL)
    graph.bind("rdf", RDF)
    graph.bind("rdfs", RDFS)
    graph.bind("skos", SKOS)
    graph.bind("time", TIME)

    labels = contract["labels"]
    core_classes = contract["coreClasses"]
    for name in core_classes + contract["reusedClasses"]:
        iri = namespace[name]
        graph.add((iri, RDF.type, OWL.Class))
        _label(graph, iri, labels[name])

    disjoint = BNode()
    members = BNode()
    graph.add((disjoint, RDF.type, OWL.AllDisjointClasses))
    graph.add((disjoint, OWL.members, members))
    Collection(graph, members, [namespace[name] for name in core_classes])

    for name, declaration in contract["objectProperties"].items():
        iri = namespace[name]
        graph.add((iri, RDF.type, OWL.ObjectProperty))
        _label(graph, iri, labels[name])
        if declaration["domain"]:
            graph.add((iri, RDFS.domain, _expression(graph, namespace, declaration["domain"])))
        if declaration["range"]:
            graph.add((iri, RDFS.range, _expression(graph, namespace, declaration["range"])))

    for name in contract["genericProperties"]:
        iri = namespace[name]
        graph.add((iri, RDF.type, RDF.Property))
        _label(graph, iri, labels[name])
    for name in contract["conceptProperties"]:
        iri = namespace[name]
        graph.add((iri, RDF.type, OWL.ObjectProperty))
        _label(graph, iri, labels[name])
    for name in contract["dataProperties"]:
        iri = namespace[name]
        graph.add((iri, RDF.type, OWL.DatatypeProperty))
        _label(graph, iri, labels[name])

    for group, scheme_name in CONCEPT_SCHEMES.items():
        _add_concepts(graph, namespace, contract[group], scheme_name)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        graph.serialize(format="turtle").rstrip() + "\n", encoding="utf-8")
    return {
        "coreClassCount": len(core_classes),
        "objectPropertyCount": len(contract["objectProperties"]),
        "conceptCount": sum(len(contract[key]) for key in CONCEPT_SCHEMES),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[5]
    report = build(
        Path(__file__).resolve().parent.parent / "contract/project_core.json",
        root / "output/kb/ontology/project-core.ttl",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
