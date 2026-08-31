#!/usr/bin/env python3
"""기존 계획규정과 수립지침 항에서 EvidenceRecord를 독립 역발급한다.

입력은 plan-rule.ttl, plan-item.ttl, tables.csv, 시행지침/meta.json,
guideline_article_corpus.jsonl.gz이다. 출력은 evidence.ttl과 _evidence.json이다.
기존 계획규정·계획항목 그래프는 수정하지 않는다.
"""
import argparse
import collections
import csv
import gzip
import json
import os
import sys
from pathlib import Path

import rdflib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mint_iri as M                                          # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
KB = os.path.join(ROOT, "output/kb")
TABLES = os.path.join(ROOT, "output/legal/table/tables.csv")
META = os.path.join(ROOT, "output/legal/시행지침/meta.json")
CORPUS = os.path.join(
    ROOT, "output/legal/statute/guideline_article_corpus.jsonl.gz")

LP = rdflib.Namespace("https://w3id.org/lp/ont#")
NATIONAL_DOCUMENT_KEY = "admrul:2100000241690"
NATIONAL_NAME = "지구단위계획수립지침"
NATIONAL_PDF_NAME = "지구단위계획수립지침(국토교통부훈령) 전문.pdf"
NATIONAL_OFFICIAL_URL = "http://law.go.kr/flDownload.do?flSeq=140733953"


def _record(target, kind, source, locator):
    return {
        "target_iri": str(target),
        "kind": kind,
        "source_iri": str(source),
        "locator": locator,
    }


def _table_index(rows):
    return {(row.get("표ID") or "").strip(): row for row in rows
            if (row.get("표ID") or "").strip()}


def _download_index(meta):
    index = {}
    for district in meta.get("districts", []):
        number = str(district.get("dstrcAppnNo") or "").strip()
        if not number:
            continue
        matches = []
        for item in district.get("downloaded", []):
            source = str(item.get("sourceUrl") or "").strip()
            if not source:
                continue
            for field in ("originalName", "savedAs"):
                name = item.get(field)
                if isinstance(name, str) and name:
                    matches.append((name, source))
        index[number] = sorted(set(matches))
    return index


def _basename(path):
    return os.path.basename((path or "").replace("\\", "/"))


def iter_plan_rule_records(plan_rule_graph, tables_rows, district_meta):
    """정본 필드가 완비된 PlanningRule의 표·공식문서 근거를 순회한다."""
    tables = _table_index(tables_rows)
    downloads = _download_index(district_meta)
    subjects = sorted(set(plan_rule_graph.subjects(
        rdflib.RDF.type, LP.PlanningRule)), key=str)
    for target in subjects:
        source = plan_rule_graph.value(target, LP.근거문서)
        table_id = plan_rule_graph.value(target, LP.표ID)
        source_text = plan_rule_graph.value(target, LP.sourceText)
        district = plan_rule_graph.value(target, LP.inDistrict)
        if not (isinstance(source, rdflib.URIRef) and table_id and source_text):
            continue
        locator = f"표ID={table_id}"
        yield _record(target, "table", source, locator)

        row = tables.get(str(table_id))
        if row is None or not isinstance(district, rdflib.URIRef):
            continue
        number = str(row.get("지구번호") or "").strip()
        if not number or not str(district).endswith("/" + number):
            continue
        filename = _basename(row.get("출처문서"))
        if not filename:
            continue
        exact = sorted({source_url for name, source_url in downloads.get(number, [])
                        if name == filename})
        if exact:
            yield _record(
                target, "officialDocument", exact[0], f"파일명={filename}")


def _national_official_attachment(statute_record):
    if not statute_record:
        return None
    if statute_record.get("document_key") != NATIONAL_DOCUMENT_KEY:
        return None
    if statute_record.get("official_name") != NATIONAL_NAME:
        return None
    for attachment in statute_record.get("attachments", []):
        names = str(attachment.get("name") or "").splitlines()
        urls = str(attachment.get("url") or "").splitlines()
        for name, url in zip(names, urls):
            if name == NATIONAL_PDF_NAME and url == NATIONAL_OFFICIAL_URL:
                return {"name": name, "url": url}
    return None


def iter_plan_item_records(plan_item_graph, statute_record):
    """정본 필드가 완비된 AdminRuleClause의 본문·공식문서 근거를 순회한다."""
    official = _national_official_attachment(statute_record)
    subjects = sorted(set(plan_item_graph.subjects(
        rdflib.RDF.type, LP.AdminRuleClause)), key=str)
    for target in subjects:
        source = plan_item_graph.value(target, LP.inAdminRule)
        clause_number = plan_item_graph.value(target, LP.항번호)
        source_text = plan_item_graph.value(target, LP.sourceText)
        if not (isinstance(source, rdflib.URIRef) and clause_number and source_text):
            continue
        locator = f"항번호={clause_number}"
        yield _record(target, "text", source, locator)
        if official:
            yield _record(target, "officialDocument", official["url"], locator)


def _sort_key(record):
    return (record["target_iri"], record["kind"], record["source_iri"],
            record["locator"])


def _unique(records):
    return [dict(zip(("target_iri", "kind", "source_iri", "locator"), key))
            for key in sorted({_sort_key(record) for record in records})]


def _rel(iri):
    return iri[len(M.ID):] if iri.startswith(M.ID) else iri


def _lit(value):
    return (str(value).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))


def serialize(records):
    """레코드를 (target, kind, source, locator) 순으로 Turtle 직렬화한다."""
    out = [
        "@prefix lp: <https://w3id.org/lp/ont#> .\n"
        "@base <https://w3id.org/lp/id/> .\n"
        "\n"
        "##  계획규정·수립지침 항의 독립 근거 레코드.\n"
        "##  생성: build_evidence.py (정렬 순회 · 멱등). 손으로 고치지 않는다.\n"
        "##  LawApplication 전용 기존 술어를 쓰지 않고 lp:hasEvidenceRecord를 쓴다.\n"
        "\n"
    ]
    for record in _unique(records):
        evidence_iri = M.evidence(
            record["target_iri"], record["kind"], record["source_iri"],
            record["locator"])
        out.append(
            f'<{_rel(record["target_iri"])}> lp:hasEvidenceRecord '
            f'<{_rel(evidence_iri)}> .\n\n'
            f'<{_rel(evidence_iri)}> a lp:EvidenceRecord ;\n'
            f'    lp:evidenceKind "{record["kind"]}" ;\n'
            f'    lp:evidenceSource <{_rel(record["source_iri"])}> ;\n'
            f'    lp:evidenceLocator "{_lit(record["locator"])}" .\n\n')
    return "".join(out).rstrip() + "\n"


def build_report(records, deferred):
    unique = _unique(records)
    counts = collections.Counter(record["kind"] for record in unique)
    return {
        "생성스크립트": "scripts/build_evidence.py",
        "원천": [
            "output/kb/graph/det/plan-rule.ttl",
            "output/kb/graph/det/plan-item.ttl",
            "output/legal/table/tables.csv",
            "output/legal/시행지침/meta.json",
            "output/legal/statute/guideline_article_corpus.jsonl.gz",
        ],
        "근거종류별_발급수": {
            kind: counts.get(kind, 0)
            for kind in ("table", "text", "officialDocument")
        },
        "EvidenceRecord수": len(unique),
        "LawApplication수": 0,
        "officialDocument_보류": deferred["officialDocument_보류"],
        "LawApplication_결손_정본": "output/kb/reports/_plan_rule.json",
        "국가수립지침_공식문서_근거": deferred["국가수립지침_공식문서_근거"],
    }


def _read_statute_record(corpus_gz):
    with gzip.open(corpus_gz, "rt", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            if (record.get("document_key") == NATIONAL_DOCUMENT_KEY and
                    record.get("official_name") == NATIONAL_NAME):
                return record
    return None


def _build(kb, tables_csv, meta_json, corpus_gz):
    plan_rule_graph = rdflib.Graph().parse(
        os.path.join(kb, "graph/det/plan-rule.ttl"), format="turtle")
    plan_item_graph = rdflib.Graph().parse(
        os.path.join(kb, "graph/det/plan-item.ttl"), format="turtle")
    with open(tables_csv, encoding="utf-8-sig", newline="") as stream:
        tables_rows = list(csv.DictReader(stream))
    with open(meta_json, encoding="utf-8") as stream:
        district_meta = json.load(stream)
    statute_record = _read_statute_record(corpus_gz)

    records = list(iter_plan_rule_records(
        plan_rule_graph, tables_rows, district_meta))
    records += list(iter_plan_item_records(plan_item_graph, statute_record))

    table_targets = {record["target_iri"] for record in records
                     if record["kind"] == "table"}
    planning_rule_official_targets = {
        record["target_iri"] for record in records
        if (record["kind"] == "officialDocument" and
            record["target_iri"] in table_targets)
    }
    missing_targets = table_targets - planning_rule_official_targets
    missing_districts = set()
    for target in missing_targets:
        district = plan_rule_graph.value(rdflib.URIRef(target), LP.inDistrict)
        if district:
            missing_districts.add(str(district))
    observed = _national_official_attachment(statute_record)
    deferred = {
        "officialDocument_보류": {
            "사유": ("tables.csv 출처문서 basename과 해당 지구 meta.json "
                   "downloaded[].originalName|savedAs의 정확일치 없음"),
            "건수": len(missing_targets),
            "지구수": len(missing_districts),
        },
        "국가수립지침_공식문서_근거": {
            "corpus_record": statute_record.get("document_key") if statute_record else None,
            "official_name": statute_record.get("official_name") if statute_record else None,
            "attachment_name": observed["name"] if observed else None,
            "source_url": observed["url"] if observed else None,
        },
    }
    report = build_report(records, deferred)
    graph_text = serialize(records)
    report_text = json.dumps(report, ensure_ascii=False, indent=1) + "\n"
    return report, graph_text, report_text


def run(kb=KB, tables_csv=TABLES, meta_json=META, corpus_gz=CORPUS):
    report, graph_text, report_text = _build(
        kb, tables_csv, meta_json, corpus_gz)
    graph_path = os.path.join(kb, "graph/det/evidence.ttl")
    report_path = os.path.join(kb, "reports/_evidence.json")
    for path, text in ((graph_path, graph_text), (report_path, report_text)):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(text)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb", default=KB)
    parser.add_argument("--tables-csv", default=TABLES)
    parser.add_argument("--meta-json", default=META)
    parser.add_argument("--corpus-gz", default=CORPUS)
    parser.add_argument("--check", action="store_true",
                        help="파일을 쓰지 않고 현재 산출물과 기대 바이트를 비교한다")
    args = parser.parse_args()
    if args.check:
        report, graph_text, report_text = _build(
            args.kb, args.tables_csv, args.meta_json, args.corpus_gz)
        targets = (
            (os.path.join(args.kb, "graph/det/evidence.ttl"), graph_text),
            (os.path.join(args.kb, "reports/_evidence.json"), report_text),
        )
        bad = [path for path, expected in targets
               if (Path(path).read_bytes() if Path(path).exists() else b"") !=
               expected.encode("utf-8")]
        if bad:
            for path in bad:
                print(f"{os.path.relpath(path, ROOT)} 이 원천과 어긋난다",
                      file=sys.stderr)
            return 1
        print("일치")
        return 0
    report = run(args.kb, args.tables_csv, args.meta_json, args.corpus_gz)
    counts = report["근거종류별_발급수"]
    print(f"table {counts['table']:,} · text {counts['text']:,} · "
          f"officialDocument {counts['officialDocument']:,} · "
          f"EvidenceRecord {report['EvidenceRecord수']:,} · LawApplication 0")
    held = report["officialDocument_보류"]
    print(f"officialDocument 보류 {held['건수']:,} · 지구 {held['지구수']:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
