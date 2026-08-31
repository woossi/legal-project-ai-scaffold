"""Protégé가 편집한 TBox를 project_core.json 계약으로 되읽는다.

TTL 은 생성물이고 계약이 정본이다. build_project_core.py 의 역방향을 닫아
GUI 편집 결과를 계약으로 되돌린다.

    .venv/bin/python3 .claude/skills/kb/kb-ontology/scripts/ingest_project_core.py
"""

import json
from pathlib import Path

from rdflib import Graph, Namespace, RDF, RDFS, OWL, SKOS
from rdflib.collection import Collection


TIME = Namespace("http://www.w3.org/2006/time#")

SCHEME_KEYS = {
    "AssertionKindScheme": "assertionKinds",
    "ConditionStatusScheme": "conditionStatuses",
    "AvailabilityStatusScheme": "availabilityStatuses",
    "EventTypeScheme": "eventTypes",
}


def _local(namespace: Namespace, iri) -> str:
    return str(iri)[len(str(namespace)):]


def _ordered(base: list[str], found: set[str]) -> list[str]:
    """계약의 기존 순서를 유지하고 새로 등장한 이름만 뒤에 붙인다."""
    return [name for name in base if name in found] + sorted(found - set(base))


def _names(graph: Graph, namespace: Namespace, expression) -> list[str]:
    """domain·range 표현을 이름 목록으로 되돌린다."""
    members = graph.value(expression, OWL.unionOf)
    nodes = list(Collection(graph, members)) if members else [expression]
    return [
        "time:Interval" if node == TIME.Interval else _local(namespace, node)
        for node in nodes
    ]


def ingest(ttl_path: Path, contract_path: Path) -> dict:
    """TBox 에서 복원 가능한 필드를 갈아끼운 계약을 돌려준다.

    schemaVersion, idBase, executionStatuses 는 TBox 에 나타나지 않으므로
    기존 계약에서 그대로 물려받는다.
    """
    base = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    graph = Graph().parse(ttl_path, format="turtle")
    namespace = Namespace(base["namespace"])

    disjoint = next(graph.subjects(RDF.type, OWL.AllDisjointClasses), None)
    core_classes = [
        _local(namespace, member)
        for member in Collection(graph, graph.value(disjoint, OWL.members))
    ] if disjoint is not None else []

    all_classes = {_local(namespace, s) for s in graph.subjects(RDF.type, OWL.Class)}

    object_properties: dict[str, dict] = {}
    concept_properties: set[str] = set()
    for subject in graph.subjects(RDF.type, OWL.ObjectProperty):
        name = _local(namespace, subject)
        domain = graph.value(subject, RDFS.domain)
        range_ = graph.value(subject, RDFS.range)
        if domain is None and range_ is None:
            concept_properties.add(name)
            continue
        object_properties[name] = {
            "domain": _names(graph, namespace, domain) if domain is not None else [],
            "range": _names(graph, namespace, range_) if range_ is not None else [],
        }

    generic_properties = {
        _local(namespace, s) for s in graph.subjects(RDF.type, RDF.Property)}
    data_properties = {
        _local(namespace, s) for s in graph.subjects(RDF.type, OWL.DatatypeProperty)}

    contract = dict(base)
    contract["coreClasses"] = core_classes
    contract["reusedClasses"] = _ordered(
        base["reusedClasses"], all_classes - set(core_classes))
    contract["objectProperties"] = {
        name: object_properties[name]
        for name in _ordered(list(base["objectProperties"]), set(object_properties))
    }
    contract["genericProperties"] = _ordered(
        base["genericProperties"], generic_properties)
    contract["conceptProperties"] = _ordered(
        base["conceptProperties"], concept_properties)
    contract["dataProperties"] = _ordered(base["dataProperties"], data_properties)
    for scheme_name, key in SCHEME_KEYS.items():
        found = {
            _local(namespace, concept)
            for concept in graph.subjects(SKOS.inScheme, namespace[scheme_name])
        }
        contract[key] = _ordered(base[key], found)

    labels: dict[str, str] = {}
    unlabeled: list[str] = []
    for name in (
        contract["coreClasses"] + contract["reusedClasses"]
        + list(contract["objectProperties"]) + contract["genericProperties"]
        + contract["conceptProperties"] + contract["dataProperties"]
    ):
        label = graph.value(namespace[name], RDFS.label)
        if label is None:
            unlabeled.append(name)
        else:
            labels[name] = str(label)
    if unlabeled:
        raise ValueError(
            "rdfs:label 이 없어 계약으로 되읽을 수 없다. "
            "편집기에서 라벨을 넣고 다시 저장한다: " + ", ".join(unlabeled))
    contract["labels"] = labels
    return contract


def dumps(contract: dict) -> str:
    """계약 파일 포맷으로 직렬화한다.

    최상위 키만 줄을 나누고 그 아래는 인라인으로 둔다. 최상위 값이 객체이면
    항목마다 한 줄을 쓴다. 표준 indent 직렬화는 리스트를 펼쳐 diff 를 뒤집는다.
    """
    lines = []
    for key, value in contract.items():
        if isinstance(value, dict):
            inner = ",\n".join(
                f"    {json.dumps(name, ensure_ascii=False)}: "
                f"{json.dumps(member, ensure_ascii=False)}"
                for name, member in value.items())
            lines.append(f"  {json.dumps(key, ensure_ascii=False)}: {{\n{inner}\n  }}")
        else:
            lines.append(
                f"  {json.dumps(key, ensure_ascii=False)}: "
                f"{json.dumps(value, ensure_ascii=False)}")
    return "{\n" + ",\n".join(lines) + "\n}\n"


def main() -> None:
    root = Path(__file__).resolve().parents[5]
    contract_path = Path(__file__).resolve().parent.parent / "contract/project_core.json"
    contract = ingest(root / "output/kb/ontology/project-core.ttl", contract_path)
    contract_path.write_text(dumps(contract), encoding="utf-8")
    print(json.dumps({
        "coreClassCount": len(contract["coreClasses"]),
        "objectPropertyCount": len(contract["objectProperties"]),
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
