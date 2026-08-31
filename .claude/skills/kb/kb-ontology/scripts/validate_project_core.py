"""Validate the self-contained Project Core M1 RDF slice."""

import json
import sys
from pathlib import Path

from pyshacl import validate as shacl_validate
from rdflib import Graph, Literal, Namespace, OWL, RDF, SKOS
from rdflib.namespace import SH, XSD


LP = Namespace("https://w3id.org/lp/ont#")
LPD = Namespace("https://w3id.org/lp/id/")
GATE_IDS = (
    "tbox-parse",
    "shape-parse",
    "pilot-shacl",
    "document-event-cardinality",
    "lifecycle-source-separation",
    "source-identity-separation",
)
CONCEPT_GROUPS = (
    "assertionKinds",
    "conditionStatuses",
    "availabilityStatuses",
    "eventTypes",
)
SHAPE_TARGETS = {
    "AssertionShape": ((SH.targetClass, LP.Assertion), (SH.targetSubjectsOf, LP.assertionKind),
                       (SH.targetSubjectsOf, LP.inputAssertion), (SH.targetObjectsOf, LP.inputAssertion)),
    "SourceAssertionShape": ((SH.targetClass, LP.Assertion), (SH.targetSubjectsOf, LP.assertionKind)),
    "InterpretationAssertionShape": ((SH.targetClass, LP.Assertion), (SH.targetSubjectsOf, LP.assertionKind)),
    "InferredAssertionShape": ((SH.targetClass, LP.Assertion), (SH.targetSubjectsOf, LP.assertionKind)),
    "EvidenceRecordShape": ((SH.targetClass, LP.EvidenceRecord), (SH.targetObjectsOf, LP.hasEvidenceRecord)),
    "InstitutionalEventShape": ((SH.targetClass, LP.InstitutionalEvent), (SH.targetObjectsOf, LP.recordsEvent)),
    "CoreDisjointnessShape": ((SH.targetSubjectsOf, RDF.type),),
}
SHAPE_PROPERTIES = {
    "AssertionShape": ((LP.assertionKind, 1, 1), (LP.assertionSubject, 1, 1),
                       (LP.assertionPredicate, 1, 1), (LP.assertionObject, 1, 1),
                       (LP.hasEvidenceRecord, 1, None)),
    "EvidenceRecordShape": ((LP.evidenceKind, 1, 1), (LP.evidenceSource, 1, 1),
                            (LP.evidenceLocator, 1, 1)),
    "InstitutionalEventShape": ((LP.eventType, 1, None), (LP.eventDate, 1, None),
                                (LP.affectsEntity, 1, None), (LP.representedBy, 1, None),
                                (LP.hasEvidenceRecord, 1, None)),
}
SPARQL_SIGNATURES = {
    "SourceAssertionShape": ("sourceFact", "observedAt", "extractionMethod"),
    "InterpretationAssertionShape": ("ontologicalInterpretation", "interpretationCriterion", "inputAssertion"),
    "InferredAssertionShape": ("inferredFact", "ruleId", "asOf", "inputAssertion"),
    "CoreDisjointnessShape": ("Project", "ProjectDistrict", "PlanVersion", "InstitutionalEvent", "Assertion"),
}
DOMAIN_CORE_CLASSES = frozenset({
    LP.Project, LP.ProjectDistrict, LP.Plan, LP.PlanVersion, LP.PlanContent, LP.PlanningArea,
})


def _parse(path: Path) -> Graph:
    return Graph().parse(path, format="turtle")


def _gate_detail(passed: bool, detail: str) -> dict[str, str]:
    return {"status": "pass" if passed else "fail", "detail": detail}


def _source_fact_subjects(graph: Graph) -> set[object]:
    return {
        subject for assertion, _, _ in graph.triples((None, LP.assertionKind, LP.sourceFact))
        for subject in graph.objects(assertion, LP.assertionSubject)
    }


def _verify_tbox(graph: Graph, contract: dict) -> tuple[bool, str]:
    required_classes = contract["coreClasses"] + contract["reusedClasses"]
    missing_classes = [name for name in required_classes if (LP[name], RDF.type, OWL.Class) not in graph]
    missing_properties = [
        name for name in contract["objectProperties"]
        if (LP[name], RDF.type, OWL.ObjectProperty) not in graph
    ]
    missing_concepts = [
        name for group in CONCEPT_GROUPS for name in contract[group]
        if (LP[name], RDF.type, SKOS.Concept) not in graph
    ]
    missing = missing_classes + missing_properties + missing_concepts
    if missing:
        return False, "missing required Project Core terms: " + ", ".join(missing)
    return True, "parsed required Project Core classes, object properties, and controlled concepts"


def _verify_shapes(graph: Graph) -> tuple[bool, str]:
    required = tuple(SHAPE_TARGETS)
    missing = [name for name in required if (LP[name], RDF.type, SH.NodeShape) not in graph]
    if missing:
        return False, "missing required Project Core node shapes: " + ", ".join(missing)
    for name, targets in SHAPE_TARGETS.items():
        shape = LP[name]
        if any((shape, predicate, value) not in graph for predicate, value in targets):
            return False, f"{name} has incomplete target signatures"
    for name, properties in SHAPE_PROPERTIES.items():
        shape = LP[name]
        constraints = {graph.value(node, SH.path): node for node in graph.objects(shape, SH.property)}
        for path, minimum, maximum in properties:
            constraint = constraints.get(path)
            if constraint is None or graph.value(constraint, SH.minCount) != Literal(minimum):
                return False, f"{name} has invalid minimum count for {path}"
            if maximum is None:
                if graph.value(constraint, SH.maxCount) is not None:
                    return False, f"{name} has an unexpected maximum count for {path}"
            elif graph.value(constraint, SH.maxCount) != Literal(maximum):
                return False, f"{name} has invalid maximum count for {path}"
    for name, tokens in SPARQL_SIGNATURES.items():
        query_text = " ".join(str(graph.value(node, SH.select, default="")) for node in graph.objects(LP[name], SH.sparql))
        if not query_text or any(token not in query_text for token in tokens):
            return False, f"{name} has incomplete SPARQL constraint signature"
    return True, "parsed required Project Core node shapes"


def _completion_event_exists(graph: Graph, shapes: Graph, tbox: Graph, entity) -> bool:
    for event in graph.subjects(LP.eventType, LP.completion):
        if ((event, RDF.type, LP.InstitutionalEvent) not in graph or
                (event, LP.affectsEntity, entity) not in graph):
            continue
        focus = Graph()
        for triple in graph.triples((event, None, None)):
            focus.add(triple)
        for evidence in graph.objects(event, LP.hasEvidenceRecord):
            for triple in graph.triples((evidence, None, None)):
                focus.add(triple)
        conforms, _, _ = shacl_validate(
            data_graph=focus, shacl_graph=shapes, ont_graph=tbox, inference="none",
        )
        if conforms:
            return True
    return False


def _gate_document_event_cardinality(graph: Graph) -> tuple[bool, str]:
    document = LPD["informationObject/molit-2008-773"]
    events = set(graph.objects(document, LP.recordsEvent))
    required_types = (
        LP.developmentPlanChangeApproval,
        LP.implementationPlanChangeApproval,
        LP.topographicMapNotice,
    )
    if len(events) != 3:
        return False, "official notice must record exactly three InstitutionalEvents"
    if not all((event, RDF.type, LP.InstitutionalEvent) in graph for event in events):
        return False, "official notice records a non-InstitutionalEvent target"
    event_types = {event: set(graph.objects(event, LP.eventType)) for event in events}
    if any(len(types) != 1 for types in event_types.values()):
        return False, "each official-notice event must have exactly one event type"
    singleton_types = {next(iter(types)) for types in event_types.values()}
    if singleton_types != set(required_types):
        return False, "official notice must record each required event type exactly once"
    return True, "official notice records each required event type exactly once"


def _gate_lifecycle_source_separation(
    graph: Graph, shapes: Graph, tbox: Graph, execution_statuses: set[str],
) -> tuple[bool, str]:
    source_subjects = _source_fact_subjects(graph)
    for subject, _, status in graph.triples((None, LP.executionStatus, None)):
        if (not isinstance(status, Literal) or status.language is not None or
                status.datatype not in (None, XSD.string) or str(status) not in execution_statuses):
            return False, "execution status must be an allowed canonical string literal"
        if subject in source_subjects:
            return False, "a source record cannot carry an execution status"
        if str(status) == "completed" and not _completion_event_exists(graph, shapes, tbox, subject):
            return False, "completed status requires an explicit completion event"
    return True, "source facts do not complete a Project without a completion event"


def _gate_source_identity_separation(graph: Graph) -> tuple[bool, str]:
    source_subjects = _source_fact_subjects(graph)
    source_subjects.update(subject for subject in graph.all_nodes() if str(subject).startswith(str(LPD["sourceRecord/"])))
    for source in source_subjects:
        if any(type_ in DOMAIN_CORE_CLASSES for type_ in graph.objects(source, RDF.type)):
            return False, "a source record cannot be typed as a domain core class"
    candidate_prefix = str(LPD["candidate/"])
    adjacent: dict[object, set[object]] = {}
    for left, _, right in graph.triples((None, OWL.sameAs, None)):
        adjacent.setdefault(left, set()).add(right)
        adjacent.setdefault(right, set()).add(left)
    visited: set[object] = set()
    for start in adjacent:
        if start in visited:
            continue
        stack, component = [start], set()
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(adjacent.get(node, set()))
        visited.update(component)
        has_source = bool(component & source_subjects)
        has_candidate = any(str(node).startswith(candidate_prefix) for node in component)
        has_domain_core = any(
            any(type_ in DOMAIN_CORE_CLASSES for type_ in graph.objects(node, RDF.type))
            for node in component
        )
        if has_source and (has_candidate or has_domain_core):
            return False, "an owl:sameAs component cannot contain both a source record and candidate"
    return True, "source-record and candidate identity paths remain separate"


def validate(root: Path) -> dict:
    """Run every Project Core M1 gate and return a JSON-serializable report."""
    root = Path(root)
    paths = {
        "tbox": root / "output/kb/ontology/project-core.ttl",
        "shapes": root / "output/kb/shapes/project-core.shacl.ttl",
        "pilot": root / "output/kb/graph/det/shinnae2-2008-773.ttl",
        "contract": root / ".claude/skills/kb/kb-ontology/contract/project_core.json",
    }
    gates: dict[str, dict[str, str]] = {}
    graphs: dict[str, Graph] = {}
    contract: dict = {}
    try:
        contract = json.loads(paths["contract"].read_text(encoding="utf-8"))
        graphs["tbox"] = _parse(paths["tbox"])
        passed, detail = _verify_tbox(graphs["tbox"], contract)
        gates["tbox-parse"] = _gate_detail(passed, detail)
    except Exception as error:
        gates["tbox-parse"] = {"status": "fail", "detail": str(error)}
    try:
        graphs["shapes"] = _parse(paths["shapes"])
        passed, detail = _verify_shapes(graphs["shapes"])
        gates["shape-parse"] = _gate_detail(passed, detail)
    except Exception as error:
        gates["shape-parse"] = {"status": "fail", "detail": str(error)}

    try:
        graphs["pilot"] = _parse(paths["pilot"])
    except Exception as error:
        graphs["pilot"] = Graph()
        gates["pilot-shacl"] = {"status": "fail", "detail": f"pilot parse failed: {error}"}
    if "pilot-shacl" not in gates:
        if gates["shape-parse"]["status"] == "fail" or gates["tbox-parse"]["status"] == "fail":
            gates["pilot-shacl"] = {"status": "fail", "detail": "TBox and shape graphs must pass before SHACL validation"}
        else:
            try:
                conforms, _, report = shacl_validate(
                    data_graph=graphs["pilot"], shacl_graph=graphs["shapes"],
                    ont_graph=graphs["tbox"], inference="none",
                )
                detail = "pilot conforms to Project Core SHACL" if conforms else str(report)
                gates["pilot-shacl"] = _gate_detail(conforms, detail)
            except Exception as error:
                gates["pilot-shacl"] = {"status": "fail", "detail": f"SHACL validation failed: {error}"}

    for gate_id, checker in (
        ("document-event-cardinality", _gate_document_event_cardinality),
        ("lifecycle-source-separation", lambda graph: _gate_lifecycle_source_separation(
            graph, graphs["shapes"], graphs["tbox"], set(contract["executionStatuses"]),
        )),
        ("source-identity-separation", _gate_source_identity_separation),
    ):
        if not paths["pilot"].exists() or not graphs["pilot"]:
            gates[gate_id] = {"status": "fail", "detail": "pilot graph did not parse"}
            continue
        try:
            passed, detail = checker(graphs["pilot"])
            gates[gate_id] = _gate_detail(passed, detail)
        except Exception as error:
            gates[gate_id] = {"status": "fail", "detail": f"checker failed: {error}"}

    failed = [gate_id for gate_id in GATE_IDS if gates[gate_id]["status"] == "fail"]
    return {"status": "pass" if not failed else "fail", "failedGates": failed, "gates": gates}


def main() -> int:
    root = Path(__file__).resolve().parents[5]
    if len(sys.argv) == 3 and sys.argv[1] == "--root":
        root = Path(sys.argv[2])
    try:
        report = validate(root)
    except Exception as error:
        report = {
            "status": "fail", "failedGates": ["validator-runtime"],
            "gates": {"validator-runtime": {"status": "fail", "detail": str(error)}},
        }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
