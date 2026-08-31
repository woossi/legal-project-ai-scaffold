#!/usr/bin/env python3
"""수집 정본의 명시 cross-reference를 1단계 T4 파생범위로 만든다."""

from __future__ import annotations

import argparse
import collections
import gzip
import importlib.util
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
SCRIPT_DIR = Path(__file__).resolve().parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCOPE = load_module("guideline_scope_builder", SCRIPT_DIR / "build_guideline_article_scope.py")
REVIEW = load_module("guideline_scope_reviewer", SCRIPT_DIR / "review_guideline_article_scope.py")
LEGAL_QUOTE = re.compile(
    r"(?:「(?P<corner>[^「」\n]{2,300})」|"
    r"『(?P<white_corner>[^『』\n]{2,300})』|"
    r"｢(?P<half_corner>[^｢｣\n]{2,300})｣)"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path,
                    default=ROOT / "output/legal/statute/guideline_article_corpus.jsonl.gz")
    ap.add_argument("--master", type=Path,
                    default=ROOT / "output/legal/statute/guideline_statute_master.json")
    ap.add_argument("--report", type=Path,
                    default=ROOT / "output/legal/statute/_guideline_crawl_report.json")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "output/legal/statute")
    args = ap.parse_args()

    master = json.loads(args.master.read_text(encoding="utf-8"))
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if report.get("meta", {}).get("status") != "완료":
        raise SystemExit("직접범위 크롤링이 완료되지 않아 T4를 만들지 않는다")
    known = set()
    for row in master["statutes"]:
        for field in ("source_key", "source_name_hint", "official_name"):
            if row.get(field):
                known.add(REVIEW.canon(row[field]))

    grouped = collections.defaultdict(lambda: {
        "names": collections.Counter(), "units": set(), "count": 0, "examples": []})
    document_count = provision_count = 0
    with gzip.open(args.corpus, "rt", encoding="utf-8") as handle:
        for line in handle:
            document = json.loads(line)
            document_count += 1
            for provision in document["provisions"]:
                provision_count += 1
                for text_line in provision["text"].splitlines():
                    # 공식 정본에서 법령명은 겹낫표로 구분된다. ASCII/curly 따옴표는
                    # 정의어("기준" 등)를 자료명으로 오인하므로 T4에서는 허용하지 않는다.
                    hits = list(LEGAL_QUOTE.finditer(text_line))
                    for index, hit in enumerate(hits):
                        name = REVIEW.independent_candidate(REVIEW.quoted_text(hit))
                        if not name:
                            continue
                        next_source = hits[index + 1].start() if index + 1 < len(hits) else None
                        units = SCOPE.refs_after(text_line, hit.end(), next_source)
                        if not units:
                            continue
                        key = REVIEW.canon(name)
                        if key in known:
                            continue
                        bucket = grouped[key]
                        bucket["names"][name] += 1
                        bucket["units"].update(units)
                        bucket["count"] += 1
                        if len(bucket["examples"]) < 5:
                            bucket["examples"].append({
                                "origin_document_key": document["document_key"],
                                "origin_official_name": document["official_name"],
                                "origin_article_number": provision["article_number"],
                                "origin_article_branch": provision["article_branch"],
                                "evidence": text_line.strip()[:500],
                            })

    crawl_queue, type_queue, manual_queue, quarantine = [], [], [], []
    for key, bucket in sorted(grouped.items()):
        name = bucket["names"].most_common(1)[0][0]
        category = SCOPE.source_category(name)
        row = {
            "source_key": key,
            "source_name_hint": name,
            "source_category": category,
            "mention_count": bucket["count"],
            "explicit_provision_mention_count": bucket["count"],
            "name_only_mention_count": 0,
            "observed_units": sorted(bucket["units"]),
            "scope_tier": "T4_파생범위_1단계",
            "grounding": "수집_정본조문_cross_reference",
            "collection_route": "국가법령정보센터_OPEN_API",
            "examples": bucket["examples"],
        }
        if not category:
            row["reason"] = "외부 규범 명칭 형식 불충족"
            quarantine.append(row)
        elif category == "계획·비법령기준":
            row["collection_route"] = "발행기관_원문수동대조"
            manual_queue.append(row)
        elif category == "규칙_정본분류필요":
            row["reason"] = "중앙부처 규칙·자치법규·비법령 종류 선분류 필요"
            type_queue.append(row)
        else:
            row["targets"] = REVIEW.source_targets(category)
            crawl_queue.append(row)

    result = {
        "meta": {
            "status": "통과",
            "scope_tier": "T4_파생범위_1단계",
            "closure_boundary": (
                "직접범위에서 수집한 정본 조문의 명시 cross-reference만 1단계 확장한다. "
                "T4 정본에서 다시 발견되는 인용은 재귀 확장하지 않는다"
            ),
            "source_corpus": args.corpus.relative_to(ROOT).as_posix(),
            "script": ".claude/skills/legal/legal-statute/scripts/build_guideline_t4_scope.py",
        },
        "summary": {
            "origin_document_count": document_count,
            "origin_provision_count": provision_count,
            "derived_source_count": len(grouped),
            "official_api_queue_count": len(crawl_queue),
            "type_classification_queue_count": len(type_queue),
            "manual_nonstat_queue_count": len(manual_queue),
            "quarantine_count": len(quarantine),
            "explicit_cross_reference_occurrence_count": sum(
                bucket["count"] for bucket in grouped.values()),
            "distinct_source_unit_pair_count": sum(
                len(bucket["units"]) for bucket in grouped.values()),
        },
        "crawl_queue": crawl_queue,
        "type_classification_queue": type_queue,
        "manual_source_queue": manual_queue,
        "quarantine": quarantine,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_json = args.out_dir / "guideline_t4_scope.json"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    s = result["summary"]
    lines = [
        "# 시행지침 외부 규범 T4 파생범위",
        "",
        "직접범위 정본의 명시 cross-reference를 **한 단계만** 확장한 수집 큐다.",
        "T4 정본 안의 인용은 다시 확장하지 않아 법체계 전체로 무한 전이되는 것을 막는다.",
        "",
        f"- 기원 정본 문서: {s['origin_document_count']:,}종",
        f"- 기원 조문/전문 단위: {s['origin_provision_count']:,}개",
        f"- 파생 명칭: {s['derived_source_count']:,}종",
        f"- 공식 API 큐: {s['official_api_queue_count']:,}종",
        f"- 종류 선분류: {s['type_classification_queue_count']:,}종",
        f"- 비법령 수동대조: {s['manual_nonstat_queue_count']:,}종",
        f"- 격리: {s['quarantine_count']:,}종",
        f"- 명시 cross-reference: {s['explicit_cross_reference_occurrence_count']:,}건 / "
        f"자료×단위 {s['distinct_source_unit_pair_count']:,}쌍",
        "",
    ]
    (args.out_dir / "guideline_t4_scope.md").write_text(
        "\n".join(lines), encoding="utf-8")
    print(
        f"T4 1단계 / 파생 {len(grouped)}종 / API {len(crawl_queue)} / "
        f"선분류 {len(type_queue)} / 수동 {len(manual_queue)} / 격리 {len(quarantine)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
