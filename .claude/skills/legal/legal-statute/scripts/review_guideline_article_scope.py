#!/usr/bin/env python3
"""일반 조문 인용 범위를 독립 경로로 적대 검수하고 수집 큐를 만든다.

범위 생성기의 어휘만 다시 세는 검사가 아니다. 원 Markdown에서 frontmatter를
독립적으로 제거한 뒤 인용부호 안의 외부 규범 후보를 다시 찾고, 범위 산출물의
근거·집계·tier 불변식과 대조한다. 직접 인용이나 기존 정본 대조로 뒷받침되지 않은
후보는 API에 보내지 않고 quarantine에 둔다.

입력  output/legal/markdown/{서울,인천,경기}/*.md
      output/legal/statute/guideline_article_scope.json
출력  output/legal/statute/guideline_article_scope_review.json
      output/legal/statute/guideline_article_scope_review.md
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import statute_common as sc  # noqa: E402


ROOT = Path(__file__).resolve().parents[5]
QUOTE = re.compile(
    r"(?:「(?P<corner>[^「」\n]{2,300})」|"
    r"『(?P<white_corner>[^『』\n]{2,300})』|"
    r"｢(?P<half_corner>[^｢｣\n]{2,300})｣|"
    r"“(?P<double_curly>[^“”\n]{2,300})”|"
    r"‘(?P<single_curly>[^‘’\n]{2,300})’|"
    r"\"(?P<double_ascii>[^\"\n]{2,300})\")"
)
SOURCE_END = re.compile(
    r"(?:법률|특별법|법|시행령|시행규칙|조례|규칙|규정|지침|가이드라인|"
    r"고시|예규|훈령|기준|매뉴얼|편람|종합계획|기본계획|발전계획)$"
)
NON_SOURCE_END = re.compile(r"(?:기법|공법|수법|용법|기준법|치법|방법)$")
GENERIC = {
    "법", "법령", "법규", "법률", "조례", "규칙", "지침", "기준", "고시",
    "가이드라인", "매뉴얼", "편람", "시조례", "도조례", "구조례", "시구조례",
    "도지침", "시지침", "관계법령", "관련법령", "관계법규",
    "관련법규", "현행법령", "각종법령", "본법", "동법", "같은법", "시행령",
    "시행규칙", "동법시행령", "동법시행규칙", "같은법시행령",
    "같은법시행규칙", "본지침", "시행지침",
    "지구단위계획", "지구단위계획시행지침",
}


def canon(value: str) -> str:
    return sc.strip_separators(value).strip()


def quoted_text(match: re.Match) -> str:
    return next(value for value in match.groups() if value is not None)


def body_without_frontmatter(raw: str) -> tuple[str, int]:
    """YAML frontmatter만 제거하고 본문과 본문 시작 파일 줄을 반환한다."""
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end >= 0:
            prefix = raw[:end + 5]
            return raw[end + 5:], prefix.count("\n") + 1
    return raw, 1


def independent_candidate(raw: str) -> str | None:
    """범위 생성기와 분리된 보수적 인용부호 후보 정리."""
    value = re.sub(r"\s+", " ", raw).strip(" \t\r\n「」『』｢｣\"'“”‘’")
    if re.search(r"[「」『』｢｣“”‘’\"]", value):
        return None
    value = re.sub(
        r"\s*\((?:19|20)\d{2}(?:[.-]\d{1,2})?(?:[.-]\d{1,2})?\.?\)\s*$",
        "", value,
    )
    value = re.sub(r"^[Ⅰ-Ⅹ]+[.\s]+", "", value)
    value = re.split(r"\s+(?:및|와|과)\s+(?=(?:동|같은)\s*법)", value, maxsplit=1)[0]
    value = re.split(
        r"\s+(?=제\s*\d+\s*(?:조|항|호)|별표|별지)", value, maxsplit=1)[0]
    value = value.strip(" ,·ㆍ․:;[]()")
    key = canon(value)
    if len(key) < 3 or key in GENERIC:
        return None
    if re.match(r"^제?[Ⅰ-Ⅹ\d]+편", key):
        return None
    if NON_SOURCE_END.search(key) or not SOURCE_END.search(key):
        return None
    return value


def independent_quote_scan(markdown_dir: Path) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = collections.defaultdict(list)
    for path in sorted(markdown_dir.glob("*/*.md")):
        raw = path.read_text(encoding="utf-8", errors="replace")
        body, first_line = body_without_frontmatter(raw)
        for offset, line in enumerate(body.splitlines()):
            for match in QUOTE.finditer(line):
                name = independent_candidate(quoted_text(match))
                if not name:
                    continue
                key = canon(name)
                if len(found[key]) < 5:
                    found[key].append({
                        "source_name": name,
                        "source_file": path.relative_to(ROOT).as_posix(),
                        "line": first_line + offset,
                        "evidence": re.sub(r"\s+", " ", line).strip()[:360],
                    })
    return found


def source_targets(category: str) -> list[str]:
    if category == "국가법령후보":
        return ["law", "admrul"]
    if category == "자치법규후보":
        return ["ordin"]
    if category == "행정규칙·기준후보":
        return ["admrul", "law"]
    if category == "규칙_정본분류필요":
        return ["law", "ordin", "admrul"]
    return []


def looks_internal_guideline(name: str) -> bool:
    """법제처 정본이 아니라 같은 계획문서 안의 편·부문을 가리키는 표기."""
    key = canon(name)
    if key.startswith("시행지침"):
        return True
    if re.match(r"^제?(?:\d+|[Ⅰ-Ⅹ]+)(?:편|장|절)", key):
        return True
    return bool(re.search(
        r"(?:지구단위계획|민간부문|공공부문|용지별|특별계획구역|"
        r"경관및공공부문|통합계획구간|생태환경부문|에너지부문)시행지침$",
        key,
    ))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", type=Path,
                    default=ROOT / "output/legal/statute/guideline_article_scope.json")
    ap.add_argument("--markdown-dir", type=Path,
                    default=ROOT / "output/legal/markdown")
    ap.add_argument("--master", type=Path,
                    default=ROOT / "output/legal/statute/statute_master.json")
    ap.add_argument("--out-dir", type=Path,
                    default=ROOT / "output/legal/statute")
    args = ap.parse_args()

    scope = json.loads(args.scope.read_text(encoding="utf-8"))
    source_by_key = {row["source_key"]: row for row in scope["sources"]}
    mention_by_key: dict[str, list[dict]] = collections.defaultdict(list)
    errors = []
    for index, row in enumerate(scope["mentions"]):
        mention_by_key[row["source_key"]].append(row)
        if bool(row["cited_units"]) != (row["scope_tier"] == "T1_명시조문"):
            errors.append({"type": "tier_mismatch", "index": index})
        path = ROOT / row["source_file"]
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if not 1 <= row["line"] <= len(lines):
            errors.append({"type": "line_out_of_range", "index": index})
            continue
        actual = canon(lines[row["line"] - 1])
        if row["source_key"] not in actual:
            errors.append({"type": "source_not_on_line", "index": index})
        if canon(row["source_surface"]) != row["source_key"]:
            errors.append({"type": "surface_key_mismatch", "index": index})

    independent = independent_quote_scan(args.markdown_dir)
    missing = []
    for key, examples in sorted(independent.items()):
        if key not in source_by_key:
            missing.append({"source_key": key, "examples": examples})

    master_keys = set()
    if args.master.exists():
        master = json.loads(args.master.read_text(encoding="utf-8"))
        for row in master.get("statutes", []):
            for field in ("statute_key", "실측표기", "정식명칭"):
                if row.get(field):
                    master_keys.add(canon(row[field]))

    crawl_queue, classification_queue, manual_queue, internal_queue, quarantine = [], [], [], [], []
    for key, row in sorted(source_by_key.items()):
        direct = key in independent
        master_grounded = key in master_keys
        base = {
            "source_key": key,
            "source_name_hint": row["source_name_hint"],
            "source_category": row["source_category"],
            "mention_count": row["mention_count"],
            "explicit_provision_mention_count": row["explicit_provision_mention_count"],
            "name_only_mention_count": row["name_only_mention_count"],
            "observed_units": row["observed_units"],
            "scope_tier": ("T2_명칭만_전문후보" if row["name_only_mention_count"]
                           else "T1_명시조문"),
            "grounding": ("원문_직접인용" if direct else "기존_정본대조_평문관측"),
            "example": row["example"],
        }
        if not (direct or master_grounded):
            base["reason"] = (
                "인용부호 직접명칭이나 기존 정본 표기로 뒷받침되지 않은 평문 최장일치; "
                "서술 잔여물 가능성이 있어 API 전송 금지"
            )
            quarantine.append(base)
        elif looks_internal_guideline(row["source_name_hint"]):
            base["reason"] = (
                "시행지침 원문 내부의 편·부문·사업별 지구단위계획 시행지침 표기; "
                "국가법령정보센터 조회 대상이 아님"
            )
            internal_queue.append(base)
        elif row["source_category"] == "규칙_정본분류필요":
            base["reason"] = (
                "원문 꼬리만으로 중앙부처 규칙·자치법규·비법령 규칙을 구분할 수 없어 "
                "자동 다중 API 조회 전에 종류 확정이 필요"
            )
            classification_queue.append(base)
        elif row["source_category"] == "계획·비법령기준":
            base["collection_route"] = "발행기관_원문수동대조"
            manual_queue.append(base)
        else:
            base["targets"] = source_targets(row["source_category"])
            base["collection_route"] = "국가법령정보센터_OPEN_API"
            crawl_queue.append(base)

    source_rollup_errors = []
    for key, row in source_by_key.items():
        actual = mention_by_key.get(key, [])
        checks = {
            "mention_count": len(actual),
            "explicit_provision_mention_count": sum(bool(x["cited_units"]) for x in actual),
            "name_only_mention_count": sum(not x["cited_units"] for x in actual),
        }
        for field, expected in checks.items():
            if row[field] != expected:
                source_rollup_errors.append({
                    "source_key": key, "field": field,
                    "reported": row[field], "actual": expected,
                })
    errors.extend({"type": "source_rollup", **row} for row in source_rollup_errors)

    s = scope["summary"]
    summary_checks = {
        "source_count": len(scope["sources"]),
        "mention_count": len(scope["mentions"]),
        "relative_reference_count": len(scope["relative_references"]),
        "generic_reference_count": len(scope["generic_references"]),
    }
    for field, actual in summary_checks.items():
        if s.get(field) != actual:
            errors.append({
                "type": "summary_mismatch", "field": field,
                "reported": s.get(field), "actual": actual,
            })

    passed = not errors and not missing
    result = {
        "meta": {
            "review_type": "적대적_독립전체본문대조",
            "status": "통과" if passed else "실패",
            "scope_input": args.scope.relative_to(ROOT).as_posix(),
            "principle": (
                "원문 직접인용 또는 기존 정본 대조로 뒷받침되지 않은 후보는 "
                "공식 API에 보내지 않는다"
            ),
            "script": ".claude/skills/legal/legal-statute/scripts/review_guideline_article_scope.py",
        },
        "summary": {
            "independent_direct_quote_source_count": len(independent),
            "independent_direct_quote_missing_count": len(missing),
            "structural_error_count": len(errors),
            "official_api_queue_count": len(crawl_queue),
            "type_classification_queue_count": len(classification_queue),
            "manual_nonstat_queue_count": len(manual_queue),
            "internal_guideline_queue_count": len(internal_queue),
            "quarantine_count": len(quarantine),
        },
        "missing_direct_quote_sources": missing,
        "structural_errors": errors,
        "crawl_queue": crawl_queue,
        "type_classification_queue": classification_queue,
        "manual_source_queue": manual_queue,
        "internal_guideline_queue": internal_queue,
        "quarantine": quarantine,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_json = args.out_dir / "guideline_article_scope_review.json"
    out_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 시행지침 조문 인용 범위 적대적 검수",
        "",
        f"- 판정: **{result['meta']['status']}**",
        f"- 독립 전체본문 직접인용 후보: {len(independent):,}종",
        f"- 범위 산출물 누락: {len(missing):,}종",
        f"- 구조·근거 불변식 오류: {len(errors):,}건",
        f"- 공식 API 큐: {len(crawl_queue):,}종",
        f"- 종류 선분류 큐: {len(classification_queue):,}종",
        f"- 발행기관 원문 수동대조 큐: {len(manual_queue):,}종",
        f"- 시행지침 내부참조 큐: {len(internal_queue):,}종",
        f"- API 전송 금지 격리: {len(quarantine):,}종",
        "",
        "독립 검사는 범위 생성기의 조문 span을 재사용하지 않고, 189개 원 Markdown에서",
        "frontmatter만 제거한 전체본문을 다시 스캔했다. 격리 후보는 실패를 숨긴 것이 아니라",
        "서술 잔여물을 법령명으로 오인해 외부 API에 보내는 것을 막기 위한 검토 대상이다.",
        "",
        "## 격리 후보",
        "",
    ]
    if quarantine:
        lines.extend(
            f"- `{row['source_name_hint']}` — {row['reason']}"
            for row in quarantine
        )
    else:
        lines.append("- 없음")
    if missing:
        lines.extend(["", "## 누락 직접인용 후보", ""])
        lines.extend(f"- `{row['source_key']}`" for row in missing)
    (args.out_dir / "guideline_article_scope_review.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")

    print(
        f"적대검수 {result['meta']['status']} / 독립인용 {len(independent)}종 / "
        f"누락 {len(missing)} / 구조오류 {len(errors)} / "
        f"API큐 {len(crawl_queue)} / 선분류 {len(classification_queue)} / "
        f"수동 {len(manual_queue)} / 내부 {len(internal_queue)} / 격리 {len(quarantine)}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
