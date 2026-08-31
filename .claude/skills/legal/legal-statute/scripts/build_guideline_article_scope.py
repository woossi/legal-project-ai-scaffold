#!/usr/bin/env python3
"""시행지침 일반 조문 전체에서 외부 규범 인용의 수집 범위를 확정한다.

이 스크립트는 정본 인용 간선을 만들지 않는다. 189개 시행지침 Markdown의 실재
h4 조문과 그 본문을 전수 순회하여 다음 단계에서 무엇을 수집해야 하는지 기록한다.

* 이름이 확인되는 법률·하위법령·자치법규·행정규칙·가이드라인
* 그 이름 뒤에 직접 붙은 조·항·호·목·별표·별지
* 이름만 있고 조문이 없는 포괄 인용
* 동법·같은 법·관련법령 등 추정해서는 안 되는 미해소 참조

입력  output/legal/markdown/{서울,인천,경기}/*.md
출력  output/legal/statute/guideline_article_scope.json
      output/legal/statute/guideline_article_scope.md
      output/legal/statute/guideline_source_article_corpus.jsonl.gz
"""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import statute_common as sc  # noqa: E402


ROOT = Path(__file__).resolve().parents[5]
ANALYSIS = ROOT / "tools" / "analysis"
if str(ANALYSIS) not in sys.path:
    sys.path.insert(0, str(ANALYSIS))
import parse_toc as TOC  # noqa: E402


QUOTE_RE = re.compile(
    r"(?:「(?P<corner>[^「」\n]{2,240})」|"
    r"『(?P<white_corner>[^『』\n]{2,240})』|"
    r"｢(?P<half_corner>[^｢｣\n]{2,240})｣|"
    r"“(?P<double_curly>[^“”\n]{2,240})”|"
    r"‘(?P<single_curly>[^‘’\n]{2,240})’|"
    r"\"(?P<double_ascii>[^\"\n]{2,240})\")"
)
ARTICLE_TITLE = re.compile(r"^제\s*\d+\s*조(?:\s*의\s*\d+)?(?:\s|\(|（|<|_|$)")
DISTRICT = re.compile(r"^[0-9]{5}[A-Z]{2}[0-9]{7}$")

SOURCE_SUFFIX = re.compile(
    r"(?:법률|특별법|법|시행령|시행규칙|조례|규칙|규정|지침|가이드라인|"
    r"고시|예규|훈령|기준|매뉴얼|편람|종합계획|기본계획|발전계획)$"
)
NON_SOURCE_TAIL = re.compile(r"(?:기법|공법|수법|용법|기준법|치법|방법)$")
GENERIC_NAMES = {
    "법", "법령", "법규", "법률", "조례", "규칙", "지침", "기준", "고시",
    "가이드라인", "매뉴얼", "편람", "시조례", "도조례", "구조례", "시구조례",
    "도지침", "시지침",
    "관계법령", "관련법령", "관계법규", "관련법규", "현행법령", "각종법령",
    "제반관련법규", "본법", "동법", "같은법", "시행령", "시행규칙",
    "동법시행령", "동법시행규칙", "같은법시행령", "같은법시행규칙",
    "지구단위계획",
    "지구단위계획시행지침", "본지침", "시행지침",
}
RELATIVE_RE = re.compile(
    r"(?:(?:동\s*법|같은\s*법|본\s*법)(?:\s*시행령(?:\s*및\s*시행규칙)?|\s*시행규칙)?|"
    r"동\s*시행령|동\s*시행규칙|동\s*령|같은\s*영|동\s*규칙|동\s*조례|"
    r"같은\s*조례|동\s*조|같은\s*조|전\s*항|전\s*호|위\s*항|위\s*조항|"
    r"상기\s*조항)"
)
GENERIC_RE = re.compile(
    r"(?:제반\s*)?(?:관계|관련|현행|각종)\s*(?:법령|법규|법률|조례|규칙|지침)|"
    r"(?:법령|법규)\s*(?:및|·|,|이나|또는)\s*(?:조례|규칙|지침)"
)
REF_RE = re.compile(
    r"제\s*\d+\s*조(?:\s*의\s*\d+)?|"
    r"제\s*\d+\s*항(?:\s*의\s*\d+)?|"
    r"(?:제\s*)?\d+\s*호(?:\s*의\s*\d+)?|"
    r"[가-하]\s*목|"
    r"별표\s*(?:제\s*)?\d+(?:\s*의\s*\d+)?|"
    r"별지\s*(?:제\s*)?\d+\s*호?(?:\s*서식)?|"
    r"부칙"
)


def compact(value: str) -> str:
    """공백과 조판 중점을 접은 비교 키."""
    return sc.strip_separators(value).strip()


def quoted_text(match: re.Match) -> str:
    """짝이 맞는 인용부호 정규식에서 실제 내부 문자열을 반환한다."""
    return next(value for value in match.groups() if value is not None)


def normalize_ref(value: str) -> str:
    value = re.sub(r"\s+", "", value)
    if re.fullmatch(r"\d+호(?:의\d+)?", value):
        value = "제" + value
    return value.replace("별표제", "별표").replace("별지제", "별지")


def source_category(name: str) -> str | None:
    """관측 표기를 정본 여부가 아닌 문서 종류 후보로만 분류한다."""
    key = compact(name)
    if not key or key in GENERIC_NAMES or len(key) < 3:
        return None
    if NON_SOURCE_TAIL.search(key) or not SOURCE_SUFFIX.search(key):
        return None
    if re.match(r"^제?(?:\d+|[Ⅰ-Ⅹ]+)편", key):
        return None
    if key.endswith(("가이드라인", "매뉴얼", "편람", "종합계획", "기본계획", "발전계획")):
        return "계획·비법령기준"
    if key.endswith(("지침", "고시", "예규", "훈령", "기준")):
        return "행정규칙·기준후보"
    if key.endswith("조례"):
        return "자치법규후보"
    if key.endswith("조례시행규칙"):
        return "자치법규후보"
    if key.endswith("시행규칙"):
        return "국가법령후보"
    if key.endswith("규칙"):
        # 원문 표기만으로 중앙부처 규칙과 지자체 규칙을 안정적으로 가를 수 없다.
        return "규칙_정본분류필요"
    return "국가법령후보"


def clean_candidate(raw: str) -> str | None:
    """인용부호 안 또는 기존 인용목록의 문자열에서 명칭만 보존한다."""
    value = raw.strip().strip("「」『』｢｣\"'“”‘’")
    if re.search(r"[「」『』｢｣“”‘’\"]", value):
        return None
    value = re.sub(r"\s+", " ", value)
    value = re.sub(
        r"\s*\((?:19|20)\d{2}(?:[.-]\d{1,2})?(?:[.-]\d{1,2})?\.?\)\s*$", "", value)
    value = re.sub(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.\s]+", "", value)
    # 「도로교통법 및 동법 시행규칙」 같은 합성 표기는 선행 본법만 어휘로 쓰고
    # 상대 부분은 relative_references에서 따로 보존한다.
    value = re.split(r"\s+(?:및|와|과)\s+(?=(?:동|같은)\s*법)", value, maxsplit=1)[0]
    # cited_laws 값은 명칭 뒤에 조문을 포함할 수 있다.
    value = re.split(
        r"\s+(?=제\s*\d+\s*(?:조|항|호)|별표|별지)", value, maxsplit=1)[0]
    value = value.strip(" ,·ㆍ․:;[]()")
    return value if source_category(value) else None


def frontmatter_value(path: Path, key: str) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(rf"^{re.escape(key)}:\s*\"?([^\"\n]+)\"?\s*$", raw, re.M)
    return match.group(1).strip() if match else ""


def article_spans(path: Path) -> tuple[list[dict], int]:
    """목차가 아닌 실재 h4 조문을 물리적 출현 단위로 자른다."""
    fm, _srcs, _heads, body_heads, lines = TOC.parse(str(path))
    heads = [h for h in body_heads
             if h["lv"] == 4 and ARTICLE_TITLE.match(h["tx"])]
    district = fm.get("지구번호", "").strip('"') or frontmatter_value(path, "지구번호")
    if not DISTRICT.fullmatch(district):
        district = ""
    relpath = path.relative_to(ROOT).as_posix()
    raw = path.read_text(encoding="utf-8", errors="replace")
    body_marker = raw.find("\n---\n", 4)
    body_offset = raw[:body_marker + 5].count("\n") if body_marker >= 0 else 0
    raw_line_count = len(raw.splitlines())
    # parse_toc는 개행으로 끝나는 파일에 합성 빈 줄 하나를 남긴다. 원문 줄범위와
    # 본문이 일대일로 재현되도록 그 EOF sentinel은 스캔 단위에서 제외한다.
    content_end = len(lines) - int(bool(lines) and lines[-1] == "" and raw.endswith("\n"))
    out = []
    if heads and any(line.strip() for line in lines[:heads[0]["i"]]):
        end = heads[0]["i"]
        text = "\n".join(lines[:end])
        out.append({
            "district": district,
            "source_file": relpath,
            "article_seq": 0,
            "article_title": "[문서 미귀속 선행본문]",
            "scope_origin": "unassigned_document_preamble",
            "line_start": body_offset + 1,
            "line_end": min(raw_line_count, body_offset + end),
            "body_line_count": end,
            "char_count": len(text),
            "text": text,
        })
    for seq, head in enumerate(heads, start=1):
        end = heads[seq]["i"] if seq < len(heads) else content_end
        # parse_toc의 줄 인덱스는 frontmatter를 제외한 본문 기준이다. 사람이 원문에서
        # 바로 찾을 수 있도록 frontmatter 포함 실제 파일 줄도 별도로 계산한다.
        text = "\n".join(lines[head["i"]:end])
        out.append({
            "district": district,
            "source_file": relpath,
            "article_seq": seq,
            "article_title": head["tx"],
            "scope_origin": "h4_article",
            "line_start": body_offset + head["i"] + 1,
            "line_end": min(raw_line_count, body_offset + end),
            "body_line_count": max(0, end - head["i"] - 1),
            "char_count": len(text),
            "text": text,
        })
    # 일부 변환본은 조문 번호 헤딩이 전부 유실되고 `(목적)` 같은 표제만 남았다.
    # 이 51개 문서를 제외하면 관련 법령 범위가 체계적으로 누락되므로, 조문 경계를
    # 발명하지 않고 문서 본문 전체를 하나의 비구조화 fallback 단위로 보존한다.
    if not out:
        text = "\n".join(lines[:content_end])
        out.append({
            "district": district,
            "source_file": relpath,
            "article_seq": 0,
            "article_title": "[문서 비구조화 본문]",
            "scope_origin": "fallback_unsegmented_document",
            "line_start": body_offset + 1,
            "line_end": min(raw_line_count, body_offset + content_end),
            "body_line_count": content_end,
            "char_count": len(text),
            "text": text,
        })
    return out, content_end


def _add_vocab(forms: dict[str, collections.Counter], value: str | None) -> None:
    if not value or not source_category(value):
        return
    forms[compact(value)][value] += 1


def build_vocab(articles: list[dict], master_path: Path, terms_path: Path) -> dict[str, str]:
    """본문의 인용부호 표기와 기존 실측 표기를 합친 최장일치 어휘."""
    forms: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for article in articles:
        for match in QUOTE_RE.finditer(article["text"]):
            _add_vocab(forms, clean_candidate(quoted_text(match)))

    if master_path.exists():
        for row in json.loads(master_path.read_text(encoding="utf-8")).get("statutes", []):
            for field in ("실측표기", "정식명칭"):
                _add_vocab(forms, clean_candidate(row.get(field, "")))

    if terms_path.exists():
        data = json.loads(terms_path.read_text(encoding="utf-8"))
        for term in data.get("terms", []):
            for raw in term.get("cited_laws", []):
                quoted = QUOTE_RE.search(raw or "")
                _add_vocab(forms, clean_candidate(quoted_text(quoted) if quoted else raw or ""))

    return {key: counts.most_common(1)[0][0] for key, counts in forms.items()}


def build_scanner(vocab: dict[str, str]) -> re.Pattern:
    parts = []
    for key in sorted(vocab, key=len, reverse=True):
        parts.append(sc.loose_pattern(key))
    return re.compile("(" + "|".join(parts) + ")") if parts else re.compile(r"(?!)")


def refs_after(line: str, end: int, next_source: int | None = None) -> list[str]:
    """명칭 바로 뒤에 문법적으로 이어지는 법조문 단위만 뽑는다."""
    stop = min(len(line), end + 140, next_source if next_source is not None else len(line))
    tail = line[end:stop]
    sentence = re.search(r"[.!?。;]", tail)
    if sentence:
        tail = tail[:sentence.start()]
    refs, cursor = [], 0
    for match in REF_RE.finditer(tail):
        gap = tail[cursor:match.start()]
        if not refs:
            # 첫 조문은 명칭 직후의 괄호·대괄호·조사·공백만 허용한다.
            reduced = re.sub(r"[\s\[\](),·ㆍ․]", "", gap)
            if len(reduced) > 8 or re.search(r"[가-힣]{5,}", reduced):
                break
        else:
            reduced = re.sub(r"[\s,·ㆍ․~\[\]()및와부터까지의]", "", gap)
            if reduced:
                break
        refs.append(normalize_ref(match.group()))
        cursor = match.end()
    return list(dict.fromkeys(refs))


def evidence(line: str, start: int, end: int, width: int = 180) -> str:
    left = max(0, start - width // 2)
    right = min(len(line), end + width // 2)
    return re.sub(r"\s+", " ", line[left:right]).strip()


def extract_mentions(article: dict, scanner: re.Pattern, vocab: dict[str, str]) -> tuple[list[dict], list[dict], list[dict]]:
    mentions, relative, generic = [], [], []
    for offset, line in enumerate(article["text"].splitlines()):
        line_no = article["line_start"] + offset
        hits = list(scanner.finditer(line))
        for i, match in enumerate(hits):
            key = compact(match.group())
            if key not in vocab:
                continue
            next_start = hits[i + 1].start() if i + 1 < len(hits) else None
            refs = refs_after(line, match.end(), next_start)
            left = line[:match.start()].rstrip()
            quoted = bool(left and left[-1] in "「『｢“‘\"")
            year_match = re.match(
                r"[」』｣”’\"]?\s*\((?P<date>(?:19|20)\d{2}"
                r"(?:[.-]\d{1,2})?(?:[.-]\d{1,2})?\.?)\)",
                line[match.end():],
            )
            mentions.append({
                "district": article["district"],
                "source_file": article["source_file"],
                "article_seq": article["article_seq"],
                "article_title": article["article_title"],
                "line": line_no,
                "source_key": key,
                "source_surface": match.group(),
                "source_name_hint": vocab[key],
                "source_category": source_category(vocab[key]),
                "mention_form": "인용부호" if quoted else "평문",
                "cited_units": refs,
                "scope_tier": "T1_명시조문" if refs else "T2_명칭만_전문후보",
                "year_annotation": year_match.group("date").rstrip(".") if year_match else None,
                "evidence": evidence(line, match.start(), match.end()),
            })

        hit_ranges = [(m.start(), m.end()) for m in hits]
        for match in RELATIVE_RE.finditer(line):
            if any(a <= match.start() < b for a, b in hit_ranges):
                continue
            relative.append({
                "district": article["district"],
                "source_file": article["source_file"],
                "article_seq": article["article_seq"],
                "article_title": article["article_title"],
                "line": line_no,
                "surface": match.group(),
                "cited_units": refs_after(line, match.end()),
                "status": "미해소_후보",
                "evidence": evidence(line, match.start(), match.end()),
            })

        for match in GENERIC_RE.finditer(line):
            if any(a <= match.start() < b for a, b in hit_ranges):
                continue
            generic.append({
                "district": article["district"],
                "source_file": article["source_file"],
                "article_seq": article["article_seq"],
                "article_title": article["article_title"],
                "line": line_no,
                "surface": match.group(),
                "status": "명칭미지정_포괄참조",
                "evidence": evidence(line, match.start(), match.end()),
            })
    return mentions, relative, generic


def aggregate_sources(mentions: list[dict], master_keys: set[str]) -> list[dict]:
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for row in mentions:
        grouped[row["source_key"]].append(row)
    out = []
    for key, rows in grouped.items():
        units = sorted({unit for row in rows for unit in row["cited_units"]})
        explicit = sum(bool(row["cited_units"]) for row in rows)
        out.append({
            "source_key": key,
            "source_name_hint": collections.Counter(
                row["source_name_hint"] for row in rows).most_common(1)[0][0],
            "source_category": rows[0]["source_category"],
            "mention_count": len(rows),
            "district_count": len({row["district"] for row in rows if row["district"]}),
            "explicit_provision_mention_count": explicit,
            "name_only_mention_count": len(rows) - explicit,
            "observed_units": units,
            "normalization_status": ("기존_statute_master_표기일치"
                                     if key in master_keys else "원문후보_정본미대조"),
            "crawl_scope": "명시단위+인용문맥" if explicit and explicit == len(rows)
                           else "전문수집+전조문_후보파싱",
            "example": rows[0]["evidence"],
        })
    return sorted(out, key=lambda row: (-row["mention_count"], row["source_key"]))


def write_markdown(data: dict, path: Path) -> None:
    s = data["summary"]
    cats = collections.Counter(row["source_category"] for row in data["sources"])
    top = data["sources"][:30]
    example_rows = [row for row in data["mentions"]
                    if row["district"] == "11530PV2016001"
                    and "옥외광고물" in row["article_title"]]
    lines = [
        "# 시행지침 조문·외부 규범 인용 범위",
        "",
        "> 이 문서는 189개 시행지침 Markdown의 **실재 일반 조문 본문 전체**를 전수 순회해",
        "> 다음 법령 수집 단계의 범위를 확정한 파생 산출물이다. 정본 확인 결과나 적용 판본",
        "> 판정이 아니며, `source_name_hint`는 원문 표기다.",
        "",
        "## 확정 범위",
        "",
        f"- 입력 문서: {s['document_count']:,}개",
        f"- 실재 h4 조문 출현: {s['article_span_count']:,}개 (동일 표제 반복도 각각 포함)",
        f"- h4 조문이 없어 문서 전체를 fallback한 문서: {s['fallback_document_count']:,}개",
        f"- 첫 h4 조문 전 미귀속 선행본문을 별도 보존한 문서: {s['unassigned_preamble_document_count']:,}개",
        f"- 전체 스캔 범위 본문: {s['article_body_line_count']:,}줄, {s['article_char_count']:,}자",
        f"- 외부 자료 원문 명칭후보: {s['source_count']:,}종 / 언급 {s['mention_count']:,}건",
        f"- 기존 statute_master 표기 일치: {s['master_matched_source_count']:,}종 / 정본 미대조 후보: {s['unmatched_source_candidate_count']:,}종",
        f"- 명시 조문 인용: {s['explicit_provision_mention_count']:,}건",
        f"- 명시 법조문 단위: {s['cited_unit_occurrence_count']:,}회 / 자료명×단위 고유쌍 {s['distinct_source_unit_pair_count']:,}개",
        f"- 자료명만 있는 포괄 인용: {s['name_only_mention_count']:,}건",
        f"- 상대참조: {s['relative_reference_count']:,}건 / 명칭 미지정 포괄참조: {s['generic_reference_count']:,}건",
        f"- 인용부호 안 자료명 후보: {s['quoted_source_candidate_occurrence_count']:,}건 / 추출 mention: {s['quoted_mention_count']:,}건",
        (f"- 기존 정의문 전용 인용 간선: {s['existing_definition_only_citation_count']:,}건"
         if s["existing_definition_only_citation_count"] is not None
         else "- 기존 정의문 전용 인용 간선: 비교 입력 없음"),
        "",
        "본문 범위에는 조문 헤딩 다음부터 다음 조문 직전까지의 문단·목록·표 셀·주석·각주·",
        "부록성 텍스트를 모두 포함한다. 첫 h4 조문 전의 텍스트는 조문으로 발명하지 않고",
        "`unassigned_document_preamble`로 격리한다. h4 조문이 하나도 없는 문서는 누락시키지 않고",
        "frontmatter를 제외한 문서 본문 전체를 비구조화 fallback으로 포함한다. Markdown에서",
        "복원되지 않은 `definition-restored` 노드는 원문 본문이 없으므로 이번 본문 범위에 넣지 않는다.",
        "",
        "## 후속 크롤링 폐쇄 규칙",
        "",
        "1. `T1_명시조문`: 자료명과 조·항·호·목·별표가 함께 있으면 해당 단위의 본문과 계층을 수집한다.",
        "2. `T2_명칭만_전문후보`: 자료명만 있으면 전문을 수집하고 전 조문을 잠재 후보로 파싱한다.",
        "3. `동법·같은 법`: 선행 명칭과 판본을 검증하기 전에는 인용 간선을 만들지 않는다.",
        "4. `관련법령·관계법규`: 문서만으로 닫히지 않는 개방 범위로 보존하고 별도 법률 검토 대상으로 둔다.",
        "5. 수집 중 만난 위임·준용·법정 cross-reference는 폐쇄 범위에 추가하되 `파생범위`로 표시한다.",
        "6. 고시일·작성일 등 실제 기준시점이 확인된 판본만 적용 판본으로 연결한다. 지구번호 연도는 금지한다.",
        "",
        "## 자료 종류별 고유 명칭 수",
        "",
        "| 종류 | 수 |",
        "|---|---:|",
    ]
    for key, count in sorted(cats.items()):
        lines.append(f"| {key} | {count:,} |")
    lines.extend([
        "",
        "## 언급 빈도 상위 자료",
        "",
        "| 원문 명칭 힌트 | 종류 | 언급 | 지구 | 명시 조문 | 명칭만 | 후속 범위 |",
        "|---|---|---:|---:|---:|---:|---|",
    ])
    for row in top:
        lines.append(
            f"| {row['source_name_hint']} | {row['source_category']} | {row['mention_count']:,} | "
            f"{row['district_count']:,} | {row['explicit_provision_mention_count']:,} | "
            f"{row['name_only_mention_count']:,} | {row['crawl_scope']} |"
        )
    lines.extend(["", "## 옥외광고물 사례 확인", ""])
    if example_rows:
        for row in example_rows:
            units = ", ".join(row["cited_units"]) or "명칭만 — 전문·전 조문 후보"
            lines.append(
                f"- `{row['source_name_hint']}` / {units} / "
                f"[{row['source_file']}:{row['line']}]({ROOT / row['source_file']}:{row['line']})"
            )
        lines.append("")
        lines.append("이 사례의 법률·구 조례·2008 가이드라인·특정구역 고시는 모두 범위에 들어간다. "
                     "다만 명시 조문 번호가 없으므로 네 자료 모두 `T2`이며 전문과 전 조문을 후보로 파싱한다.")
    else:
        lines.append("- 표적 사례가 추출되지 않았다. 이 상태는 검증 실패다.")
    lines.extend([
        "",
        "## 해석 한계",
        "",
        "- 이 결과는 원문에 관측된 **수집 범위**다. 원문 명칭후보를 법제처 정식명칭으로 간주하지 않는다.",
        "- 이름 없는 상대·포괄 참조 때문에 외부 법체계 범위는 시행지침 텍스트만으로 완전히 닫히지 않는다.",
        "- OCR/Markdown에 존재하지 않는 조문 본문은 이번 집계로 복구되지 않는다.",
        "- 상세 근거·전건 목록은 `guideline_article_scope.json`의 `mentions`, `relative_references`,",
        "  `generic_references`, `documents`에서 확인한다.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_source_corpus(articles: list[dict], path: Path) -> None:
    """원 시행지침 조문 본문을 재현 가능한 gzip JSONL로 보존한다."""
    rows = []
    for article in articles:
        row = dict(article)
        row["source_unit_id"] = (
            f"{article['source_file']}:{article['line_start']}:"
            f"{article['scope_origin']}"
        )
        row["text_sha256"] = hashlib.sha256(
            article["text"].encode("utf-8")).hexdigest()
        rows.append(row)
    payload = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")
    path.write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))


def build(markdown_dir: Path, out_dir: Path, master_path: Path, terms_path: Path) -> dict:
    paths = sorted(markdown_dir.glob("*/*.md"))
    articles, document_rows = [], []
    for path in paths:
        spans, document_body_lines = article_spans(path)
        articles.extend(spans)
        h4_count = sum(row["scope_origin"] == "h4_article" for row in spans)
        document_rows.append({
            "source_file": path.relative_to(ROOT).as_posix(),
            "district": spans[0]["district"] if spans else frontmatter_value(path, "지구번호"),
            "article_span_count": h4_count,
            "scope_origin": ("h4_articles" if h4_count else "fallback_unsegmented_document"),
            "has_unassigned_preamble": any(
                row["scope_origin"] == "unassigned_document_preamble" for row in spans),
            "document_body_line_count": document_body_lines,
            "article_body_line_count": sum(row["body_line_count"] for row in spans),
            "article_char_count": sum(row["char_count"] for row in spans),
        })

    vocab = build_vocab(articles, master_path, terms_path)
    scanner = build_scanner(vocab)
    mentions, relative, generic = [], [], []
    for article in articles:
        m, r, g = extract_mentions(article, scanner, vocab)
        mentions.extend(m)
        relative.extend(r)
        generic.extend(g)
    master_keys = set()
    if master_path.exists():
        for row in json.loads(master_path.read_text(encoding="utf-8")).get("statutes", []):
            for field in ("statute_key", "실측표기", "정식명칭"):
                if row.get(field):
                    master_keys.add(compact(row[field]))
    sources = aggregate_sources(mentions, master_keys)

    quoted_candidates = sum(
        1 for article in articles for match in QUOTE_RE.finditer(article["text"])
        if clean_candidate(quoted_text(match))
    )
    existing_definition_citations = None
    old_citations = out_dir / "statute_citations.json"
    if old_citations.exists():
        existing_definition_citations = len(json.loads(
            old_citations.read_text(encoding="utf-8")).get("citations", []))

    summary = {
        "document_count": len(paths),
        "fallback_document_count": sum(not row["article_span_count"] for row in document_rows),
        "unassigned_preamble_document_count": sum(
            row["has_unassigned_preamble"] for row in document_rows),
        "scanned_scope_unit_count": len(articles),
        "article_span_count": sum(row["scope_origin"] == "h4_article" for row in articles),
        "article_body_line_count": sum(row["body_line_count"] for row in articles),
        "article_char_count": sum(row["char_count"] for row in articles),
        "source_count": len(sources),
        "master_matched_source_count": sum(
            row["normalization_status"] == "기존_statute_master_표기일치" for row in sources),
        "unmatched_source_candidate_count": sum(
            row["normalization_status"] == "원문후보_정본미대조" for row in sources),
        "mention_count": len(mentions),
        "explicit_provision_mention_count": sum(bool(row["cited_units"]) for row in mentions),
        "cited_unit_occurrence_count": sum(len(row["cited_units"]) for row in mentions),
        "distinct_source_unit_pair_count": len({
            (row["source_key"], unit) for row in mentions for unit in row["cited_units"]
        }),
        "name_only_mention_count": sum(not row["cited_units"] for row in mentions),
        "relative_reference_count": len(relative),
        "generic_reference_count": len(generic),
        "quoted_source_candidate_occurrence_count": quoted_candidates,
        "quoted_mention_count": sum(row["mention_form"] == "인용부호" for row in mentions),
        "existing_definition_only_citation_count": existing_definition_citations,
    }
    data = {
        "meta": {
            "scope_status": "관측범위_확정",
            "input": "output/legal/markdown/{서울,인천,경기}/*.md",
            "source_semantics": "source_name_hint는 원문 표기이며 정본명·법적 지위 확정값이 아니다",
            "script": ".claude/skills/legal/legal-statute/scripts/build_guideline_article_scope.py",
        },
        "scope_contract": {
            "included": [
                "목차가 아닌 실재 h4 조문 헤딩",
                "각 조문 헤딩부터 다음 조문 직전까지의 문단·목록·표 셀·주석·각주·부록성 텍스트",
                "첫 h4 조문 이전의 frontmatter 제외 미귀속 선행본문",
                "h4 조문이 없는 문서의 frontmatter 제외 비구조화 본문 전체",
                "이름이 확인되는 국가법령·자치법규·행정규칙·고시·지침·가이드라인",
                "조·항·호·목·별표·별지와 명칭만 있는 포괄 인용",
                "동법·같은 법·관련법령 등 미해소 참조",
            ],
            "excluded": [
                "frontmatter",
                "Markdown에 본문 근거가 없는 definition-restored 노드",
                "정본 대조·적용 판본 판정·법적 효력 추론",
            ],
            "crawl_tiers": {
                "T1_명시조문": "명시 단위 본문·상하위 계층과 인용문맥 수집",
                "T2_명칭만_전문후보": "자료 전문 수집 후 전 조문을 잠재 관련 조문으로 파싱",
                "T3_미해소참조": "동법·같은 법·관계법령을 추측 연결하지 않고 검토 큐에 유지",
                "T4_법정폐쇄": "수집 조문이 명시적으로 위임·준용·인용한 단위를 파생범위로 추가",
            },
        },
        "summary": summary,
        "sources": sources,
        "mentions": mentions,
        "relative_references": relative,
        "generic_references": generic,
        "documents": document_rows,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "guideline_article_scope.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(data, out_dir / "guideline_article_scope.md")
    write_source_corpus(articles, out_dir / "guideline_source_article_corpus.jsonl.gz")
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown-dir", type=Path, default=ROOT / "output/legal/markdown")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "output/legal/statute")
    parser.add_argument("--master", type=Path, default=ROOT / "output/legal/statute/statute_master.json")
    parser.add_argument("--terms", type=Path, default=ROOT / "output/legal/word/terms.json")
    args = parser.parse_args()
    data = build(args.markdown_dir, args.out_dir, args.master, args.terms)
    s = data["summary"]
    print(
        f"문서 {s['document_count']} / 조문 {s['article_span_count']} / "
        f"외부자료 {s['source_count']}종 / 언급 {s['mention_count']}건"
    )
    print(
        f"명시조문 {s['explicit_provision_mention_count']} / "
        f"명칭만 {s['name_only_mention_count']} / 상대참조 {s['relative_reference_count']} / "
        f"포괄참조 {s['generic_reference_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
