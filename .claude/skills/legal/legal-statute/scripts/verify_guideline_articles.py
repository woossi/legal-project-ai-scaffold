#!/usr/bin/env python3
"""일반 조문 직접범위와 T4 파생범위 크롤링 산출물의 전건 계약 검증."""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import statute_common as sc  # noqa: E402

ROOT = Path(__file__).resolve().parents[5]
CASE_PATH = ROOT / ".claude/skills/legal/legal-statute/case/정본대조.json"
MIDDLE_DOTS_RE = re.compile(r"[·‧․･・∙ㆍ]")
QUOTATION_MARKS_RE = re.compile(r"[「」『』〈〉《》]")


def load(path: Path):
    return sc.read_json(path)


def evidence_key(text: str) -> str:
    """자료명 비교에서 조판용 기호·공백 차이만 접는다."""
    normalized = MIDDLE_DOTS_RE.sub("", text)
    normalized = unicodedata.normalize("NFKC", normalized)
    normalized = QUOTATION_MARKS_RE.sub("", normalized)
    return re.sub(r"\s+", "", normalized)


def article_label(title: str) -> str:
    match = re.match(r"^제\s*(\d+)\s*조(?:의\s*(\d+))?", title.strip())
    if not match:
        return ""
    branch = f"의{match.group(2)}" if match.group(2) else ""
    return f"제{match.group(1)}조{branch}"


def verify_source_corpus(out_dir: Path) -> list[str]:
    """원 시행지침 조문 corpus가 Markdown의 실제 줄범위와 같은지 전건 대조한다."""
    failures = []
    scope = load(out_dir / "guideline_article_scope.json")
    with gzip.open(
        out_dir / "guideline_source_article_corpus.jsonl.gz", "rt", encoding="utf-8"
    ) as handle:
        corpus = [json.loads(line) for line in handle if line.strip()]
    expected_count = scope["summary"]["scanned_scope_unit_count"]
    if len(corpus) != expected_count:
        failures.append(f"[원시행지침] corpus {len(corpus)} != 범위 {expected_count}")

    by_article = {}
    source_lines = {}
    unit_ids = set()
    for row in corpus:
        unit_id = row.get("source_unit_id")
        if not unit_id or unit_id in unit_ids:
            failures.append(f"[원시행지침] source_unit_id 누락/중복 {unit_id!r}")
        unit_ids.add(unit_id)
        key = (row.get("source_file"), row.get("article_seq"))
        if key in by_article:
            failures.append(f"[원시행지침] 문서·article_seq 중복 {key}")
        by_article[key] = row
        text = row.get("text", "")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != row.get("text_sha256"):
            failures.append(f"[원시행지침] 본문 해시 불일치 {unit_id}")
        if len(text) != row.get("char_count"):
            failures.append(f"[원시행지침] char_count 불일치 {unit_id}")
        source_file = row.get("source_file", "")
        source_path = (ROOT / source_file).resolve()
        try:
            source_path.relative_to(ROOT.resolve())
        except ValueError:
            failures.append(f"[원시행지침] 작업공간 밖 source_file {source_file}")
            continue
        if source_file not in source_lines:
            source_lines[source_file] = source_path.read_text(
                encoding="utf-8", errors="replace").splitlines()
        lines = source_lines[source_file]
        start, end = row.get("line_start"), row.get("line_end")
        if not isinstance(start, int) or not isinstance(end, int) or not (1 <= start <= end <= len(lines)):
            failures.append(f"[원시행지침] 잘못된 줄범위 {unit_id}: {start}~{end}")
            continue
        if "\n".join(lines[start - 1:end]) != text:
            failures.append(f"[원시행지침] Markdown 본문 불일치 {unit_id}")

    for mention in scope["mentions"]:
        key = (mention["source_file"], mention["article_seq"])
        article = by_article.get(key)
        if article is None:
            failures.append(f"[원시행지침] mention의 조문 corpus 없음 {key}")
            continue
        if not article["line_start"] <= mention["line"] <= article["line_end"]:
            failures.append(f"[원시행지침] mention 줄이 조문 밖 {key}:{mention['line']}")
            continue
        line = source_lines[mention["source_file"]][mention["line"] - 1]
        if mention["source_surface"] not in line:
            failures.append(
                f"[원시행지침] mention 표기가 원문 줄에 없음 {key}:{mention['line']}")

    case = load(CASE_PATH).get("옥외광고물_수집범위")
    if not isinstance(case, dict):
        failures.append("[원시행지침] case 옥외광고물_수집범위 누락")
        return failures
    district = case.get("district")
    target_article = case.get("조문")
    materials = case.get("자료")
    required_phrases = case.get("본문보존필수문구")
    if not isinstance(district, str) or not isinstance(target_article, str):
        failures.append("[원시행지침] case 옥외광고물 대상 district·조문 누락")
        return failures
    if not isinstance(materials, list) or len(materials) != 4:
        failures.append("[원시행지침] case 옥외광고물 필수 자료가 4종이 아님")
        materials = materials if isinstance(materials, list) else []
    if not isinstance(required_phrases, list) or not required_phrases:
        failures.append("[원시행지침] case 옥외광고물 본문보존필수문구 누락")
        required_phrases = []

    material_keys = []
    for material in materials:
        key = evidence_key(material) if isinstance(material, str) else ""
        if not key:
            failures.append("[원시행지침] case 옥외광고물 빈 자료")
            continue
        material_keys.append((material, key))
    if (len(material_keys) == len(materials)
            and len({key for _, key in material_keys}) != len(material_keys)):
        failures.append("[원시행지침] case 옥외광고물 자료 중복")
    phrase_keys = []
    for phrase in required_phrases:
        key = evidence_key(phrase) if isinstance(phrase, str) else ""
        if not key:
            failures.append("[원시행지침] case 옥외광고물 빈 본문 필수문구")
            continue
        phrase_keys.append((phrase, key))

    target_mentions = [
        mention for mention in scope["mentions"]
        if mention.get("district") == district
        and article_label(mention.get("article_title", "")) == evidence_key(target_article)
    ]
    mention_keys = set()
    for mention in target_mentions:
        surface = mention.get("source_surface")
        if not isinstance(surface, str) or not evidence_key(surface):
            continue
        mention_keys.add(evidence_key(surface))
        year = mention.get("year_annotation")
        if year:
            mention_keys.add(evidence_key(f"{surface}({year})"))
    for material, key in material_keys:
        if key not in mention_keys:
            failures.append(
                f"[원시행지침] case 옥외광고물 필수 자료 mention 없음: {material}")

    outdoor = next((
        row for row in corpus
        if row.get("district") == district
        and article_label(row.get("article_title", "")) == evidence_key(target_article)
    ), None)
    if outdoor is None:
        failures.append(
            f"[원시행지침] case 옥외광고물 대상 조문 없음: {district} {target_article}")
        return failures
    outdoor_text = outdoor.get("text", "")
    outdoor_key = evidence_key(outdoor_text)
    for material, key in material_keys:
        if key not in outdoor_key:
            failures.append(f"[원시행지침] case 옥외광고물 필수 자료 없음: {material}")
    for phrase, key in phrase_keys:
        if key not in outdoor_key:
            failures.append(f"[원시행지침] case 옥외광고물 본문 필수문구 없음: {phrase}")
    return failures


def verify_bundle(out_dir: Path, review_name: str, prefix: str,
                  require_complete: bool, require_cache: bool = False) -> list[str]:
    failures = []
    review = load(out_dir / review_name)
    master = load(out_dir / f"{prefix}_statute_master.json")
    report = load(out_dir / f"_{prefix}_crawl_report.json")
    cache_dir = out_dir / "_guideline_crawl_cache"
    with gzip.open(out_dir / f"{prefix}_article_corpus.jsonl.gz",
                   "rt", encoding="utf-8") as handle:
        documents = [json.loads(line) for line in handle if line.strip()]

    label = prefix
    if review.get("meta", {}).get("status") != "통과":
        failures.append(f"[{label}] 범위 검수 미통과")
    status = report.get("meta", {}).get("status")
    allowed_status = {"완료"} if require_complete else {
        "완료", "부분완료", "실행중", "회로중단", "사용자중단", "오류중단",
    }
    if status not in allowed_status:
        failures.append(f"[{label}] 실행상태 {status!r} 허용 안 됨")

    queue = review["crawl_queue"]
    statutes = master["statutes"]
    queue_keys = {row["source_key"] for row in queue}
    statute_keys = {row["source_key"] for row in statutes}
    if len(statute_keys) != len(statutes):
        failures.append(f"[{label}] source_key 중복")
    if not statute_keys <= queue_keys:
        failures.append(f"[{label}] 범위 큐에 없는 source가 정본 master에 있음")
    if require_complete and statute_keys != queue_keys:
        failures.append(
            f"[{label}] 완료 상태인데 source 누락 {len(queue_keys - statute_keys)}종")
    summary = report["summary"]
    expected = {
        "review_queue_count": len(queue),
        "processed_source_count": len(statutes),
        "verified_source_count": sum(
            row["verification_status"] == "정본대조" for row in statutes),
        "unmatched_source_count": sum(
            row["verification_status"] == "미대조" for row in statutes),
        "deduplicated_official_document_count": len(documents),
        "parsed_provision_count": sum(len(row["provisions"]) for row in documents),
    }
    for field, value in expected.items():
        if summary.get(field) != value:
            failures.append(
                f"[{label}] report.{field}={summary.get(field)} != 실집계 {value}")
    if summary.get("error_source_count") != 0:
        failures.append(f"[{label}] API오류 source가 남음")

    document_by_key = {}
    document_source_keys = set()
    allowed_parse = {"파싱", "비조문형_전문", "첨부_비조문형_전문"}
    for document in documents:
        key = document["document_key"]
        if key in document_by_key:
            failures.append(f"[{label}] 중복 정본 document_key {key}")
        document_by_key[key] = document
        if document.get("application_version_unresolved") is not True:
            failures.append(f"[{label}] {key} 적용판본 미확정 표지 없음")
        expected_param = "ID" if document["target"] == "admrul" else "MST"
        if document.get("detail_param") != expected_param:
            failures.append(
                f"[{label}] {key} 상세 파라미터 {document.get('detail_param')} != {expected_param}")
        if document.get("parse_status") not in allowed_parse:
            failures.append(f"[{label}] {key} 파싱상태 {document.get('parse_status')}")
        if not document.get("provisions"):
            failures.append(f"[{label}] {key} 전문 단위 0개")
        source_keys = document.get("source_keys", [])
        if not source_keys:
            failures.append(f"[{label}] {key} source_keys 비었음")
        document_source_keys.update(source_keys)
        for index, provision in enumerate(document.get("provisions", [])):
            if not provision.get("text", "").strip():
                failures.append(f"[{label}] {key} provision[{index}] 본문 비었음")
                break
        for cache_field in ("cache_file", "attachment_cache_file"):
            if (require_cache and document.get(cache_field)
                    and not (cache_dir / document[cache_field]).exists()):
                failures.append(
                    f"[{label}] {key} 캐시 유실 {document[cache_field]}")

    valid_match_prefixes = (
        "정확일치", "공식약칭일치", "검증된명칭변천", "검증된조회교정",
        "기존_API정본_재사용")
    verified_documents = set()
    for row in statutes:
        key = row["source_key"]
        if row.get("application_version_unresolved") is not True:
            failures.append(f"[{label}] {key} 적용판본 미확정 표지 없음")
        if row["verification_status"] == "정본대조":
            for field in ("official_name", "official_id", "target", "detail_id", "detail_param"):
                if not row.get(field):
                    failures.append(f"[{label}] {key} 정본대조인데 {field} 비었음")
            expected_param = "ID" if row.get("target") == "admrul" else "MST"
            if row.get("detail_param") != expected_param:
                failures.append(
                    f"[{label}] {key} 상세 파라미터 {row.get('detail_param')} != {expected_param}")
            if not row.get("match_method", "").startswith(valid_match_prefixes):
                failures.append(f"[{label}] {key} 허용되지 않은 대조방법 {row.get('match_method')}")
            document_key = f"{row['target']}:{row['detail_id']}"
            verified_documents.add(document_key)
            document = document_by_key.get(document_key)
            if not document:
                failures.append(f"[{label}] {key} 정본 전문 {document_key} 없음")
            elif key not in document.get("source_keys", []):
                failures.append(f"[{label}] {document_key} source_keys에 {key} 없음")
        elif row["verification_status"] == "미대조":
            if row.get("official_name") or row.get("official_id"):
                failures.append(f"[{label}] {key} 미대조인데 정본 필드가 채워짐")
        else:
            failures.append(f"[{label}] {key} 알 수 없는 검증상태")
    if set(document_by_key) != verified_documents:
        failures.append(
            f"[{label}] 정본 문서 집합 불일치: 초과 {len(set(document_by_key)-verified_documents)}, "
            f"누락 {len(verified_documents-set(document_by_key))}")
    if not document_source_keys <= statute_keys:
        failures.append(
            f"[{label}] 정본 문서 source_keys가 master 밖 source를 가리킴 "
            f"{len(document_source_keys - statute_keys)}건")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=ROOT / "output/legal/statute")
    ap.add_argument("--require-t4-complete", action="store_true")
    ap.add_argument(
        "--require-cache", action="store_true",
        help="수집을 실행한 로컬 작업공간에서만 원응답 캐시 실재까지 검사",
    )
    args = ap.parse_args()
    failures = verify_source_corpus(args.out_dir)
    failures.extend(verify_bundle(
        args.out_dir, "guideline_article_scope_review.json", "guideline", True,
        args.require_cache)
    )
    failures.extend(verify_bundle(
        args.out_dir, "guideline_t4_scope.json", "guideline_t4",
        args.require_t4_complete, args.require_cache,
    ))
    if failures:
        print(f"일반 조문 크롤링 계약 실패 {len(failures)}건")
        for failure in failures[:50]:
            print(f"  - {failure}")
        return 1
    direct = load(args.out_dir / "_guideline_crawl_report.json")["summary"]
    t4 = load(args.out_dir / "_guideline_t4_crawl_report.json")["summary"]
    print(
        f"일반 조문 크롤링 계약 통과 / 직접 {direct['processed_source_count']}/"
        f"{direct['review_queue_count']}종·정본 {direct['deduplicated_official_document_count']}종 / "
        f"T4 {t4['processed_source_count']}/{t4['review_queue_count']}종·"
        f"정본 {t4['deduplicated_official_document_count']}종")
    return 0


if __name__ == "__main__":
    sys.exit(main())
