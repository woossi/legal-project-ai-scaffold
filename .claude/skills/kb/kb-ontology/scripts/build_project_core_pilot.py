"""Build the ShinNae2 source-fact pilot without ontological promotion."""

import json
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from pyshacl import validate
from rdflib import Graph, Literal, Namespace, RDF, URIRef


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from project_core_iri import mint


LP = Namespace("https://w3id.org/lp/ont#")


def _read_and_validate(path: Path, schema: dict) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload), key=str
    )
    if errors:
        raise ValueError(f"invalid source fixture {path}: {errors[0].message}")
    if payload.get("recordKind") == "officialNotice":
        source_url = urlsplit(payload["sourceUrl"])
        if source_url.scheme not in {"http", "https"} or not source_url.netloc or any(
            character.isspace() for character in payload["sourceUrl"]
        ):
            raise ValueError(f"invalid source fixture {path}: sourceUrl must be an absolute HTTP(S) URI")
        try:
            date.fromisoformat(payload["publicationDate"])
            datetime.fromisoformat(payload["verifiedAt"].replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"invalid source fixture {path}: invalid official-notice date format") from error
    return payload


def _add_evidence(graph: Graph, evidence: URIRef, source: URIRef, kind: str, locator: str) -> None:
    graph.add((evidence, RDF.type, LP.EvidenceRecord))
    graph.add((evidence, LP.evidenceKind, Literal(kind)))
    graph.add((evidence, LP.evidenceSource, source))
    graph.add((evidence, LP.evidenceLocator, Literal(locator)))


def build(notice_path, tis_path, output_path, report_path) -> dict:
    """Validate two source fixtures and emit deterministic source-fact RDF."""
    root = Path(__file__).resolve().parents[5]
    schema = json.loads((SCRIPT_DIR.parent / "contract/project_core_source.schema.json").read_text(encoding="utf-8"))
    notice = _read_and_validate(Path(notice_path), schema)
    tis = _read_and_validate(Path(tis_path), schema)
    if notice["recordKind"] != "officialNotice" or tis["recordKind"] != "tisDistrictRecord":
        raise ValueError("notice and TIS fixtures must use their respective record kinds")
    event_ids = [item["eventId"] for item in notice["events"]]
    if len(set(event_ids)) != len(event_ids):
        raise ValueError("duplicate notice event ID")

    graph = Graph()
    graph.bind("lp", LP)
    notice_document = URIRef(mint("informationObject", notice["documentId"]))
    tis_document = URIRef(mint("informationObject", "tis", tis["sourceRecordId"]))
    tis_source = URIRef(mint("sourceRecord", "tis", tis["sourceRecordId"]))
    graph.add((notice_document, RDF.type, LP.InformationObject))
    graph.add((tis_document, RDF.type, LP.InformationObject))
    graph.add((tis_source, LP.representedBy, tis_document))
    graph.add((notice_document, LP.sourceUrl, Literal(notice["sourceUrl"])))
    graph.add((notice_document, LP.noticeNumberAsWritten, Literal(notice["noticeNumber"])))
    graph.add((notice_document, LP.titleAsWritten, Literal(notice["title"])))
    graph.add((notice_document, LP.issuerAsWritten, Literal(notice["issuer"])))
    graph.add((notice_document, LP.projectNameAsWritten, Literal(notice["projectNameAsWritten"])))
    graph.add((notice_document, LP.districtNameAsWritten, Literal(notice["districtNameAsWritten"])))
    for citation in notice["legalCitationsAsWritten"]:
        graph.add((notice_document, LP.legalCitationAsWritten, Literal(citation)))

    event_iris = []
    evidence_iris = []
    for item in notice["events"]:
        event = URIRef(mint("event", notice["documentId"], item["eventId"]))
        target = URIRef(mint("candidate", item["targetId"]))
        evidence = URIRef(mint("evidence", notice["documentId"], item["eventId"]))
        event_iris.append(event)
        evidence_iris.append(evidence)
        graph.add((notice_document, LP.recordsEvent, event))
        graph.add((event, RDF.type, LP.InstitutionalEvent))
        graph.add((event, LP.eventType, LP[item["eventType"]]))
        graph.add((event, LP.eventDate, Literal(notice["publicationDate"])))
        graph.add((event, LP.affectsEntity, target))
        graph.add((event, LP.representedBy, notice_document))
        graph.add((event, LP.hasEvidenceRecord, evidence))
        _add_evidence(graph, evidence, notice_document, "officialNotice", item["sourceLocator"])
    if len(set(event_iris)) != len(event_iris) or len(set(evidence_iris)) != len(evidence_iris):
        raise ValueError("notice event or evidence IRI collision")

    step_assertion = URIRef(mint("assertion", "source", "tis", tis["sourceRecordId"], "stepNm"))
    step_evidence = URIRef(mint("evidence", "tis", tis["sourceRecordId"], "stepNm"))
    graph.add((step_assertion, RDF.type, LP.Assertion))
    graph.add((step_assertion, LP.assertionKind, LP.sourceFact))
    graph.add((step_assertion, LP.assertionSubject, tis_source))
    graph.add((step_assertion, LP.assertionPredicate, LP.reportedStepNm))
    graph.add((step_assertion, LP.assertionObject, Literal(tis["fields"]["stepNm"])))
    graph.add((step_assertion, LP.hasEvidenceRecord, step_evidence))
    graph.add((step_assertion, LP.observedAt, Literal(tis["observationDateTime"])))
    graph.add((step_assertion, LP.extractionMethod, Literal(tis["extractionMethod"])))
    _add_evidence(graph, step_evidence, tis_document, "tisDistrictRecord", tis["sourcePath"])

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(graph.serialize(format="turtle").rstrip() + "\n", encoding="utf-8")
    conforms, _, shacl_report = validate(
        data_graph=graph,
        shacl_graph=Graph().parse(root / "output/kb/shapes/project-core.shacl.ttl", format="turtle"),
        ont_graph=Graph().parse(root / "output/kb/ontology/project-core.ttl", format="turtle"),
        inference="none",
    )
    report = {
        "assertionCount": len(set(graph.subjects(RDF.type, LP.Assertion))),
        "documentCount": len(set(graph.subjects(RDF.type, LP.InformationObject))),
        "errors": 0 if conforms else 1,
        "eventCount": len(set(graph.subjects(RDF.type, LP.InstitutionalEvent))),
        "evidenceRecordCount": len(set(graph.subjects(RDF.type, LP.EvidenceRecord))),
        "shaclConforms": bool(conforms),
        "shaclReport": str(shacl_report),
    }
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    root = Path(__file__).resolve().parents[5]
    report = build(
        root / "output/kb/pilot/신내2/source/notice-2008-773.json",
        root / "output/kb/pilot/신내2/source/tis-11260DA2005001.json",
        root / "output/kb/graph/det/shinnae2-2008-773.ttl",
        root / "output/kb/reports/_project_core_pilot.json",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
