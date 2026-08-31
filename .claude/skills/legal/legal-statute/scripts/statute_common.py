#!/usr/bin/env python3
"""자료명 표기 차이를 접는 이 스킬의 공통 규약.

원문은 같은 자료명을 중점 변형·공백·쉼표로 다르게 적는다. 자료명을 키로 삼거나
본문에서 되찾을 때 그 차이를 접어야 같은 자료가 여러 건으로 갈리지 않는다.
관측된 중점 변형은 `legal-xref/case/판정규칙.md` 의 「표기 변이」 절이 정본이다.

두 가지만 담는다.

  strip_separators  키 비교용. 분리자를 전부 지운다
  loose_pattern     본문 탐색용. 글자 사이에 분리자가 끼어도 걸리게 조립한다

`verify_guideline_articles.evidence_key` 는 여기 넣지 않는다 — 쉼표를 남기고
NFKC 정규화와 인용부호 제거까지 하는 별개 규약이라 문자 집합만 겹친다.
`legal-xref/scripts/xref_common.py` 에 같은 성격의 상수가 있으나 import 하지
않는다. 스킬 간 런타임 의존을 만들지 않는 것이 이 저장소의 규약이다.
"""

import html
import json
import re
from pathlib import Path

# 분리자. 중점 7종에 공백과 쉼표를 더한다.
SEPARATOR_CHARS = r"\s·‧․･・∙ㆍ,"

_SEPARATOR_RE = re.compile("[%s]" % SEPARATOR_CHARS)
_YYYYMMDD_RE = re.compile(r"\d{8}")


def strip_separators(value):
    """자료명에서 분리자를 전부 지운 비교용 키를 만든다."""
    return _SEPARATOR_RE.sub("", value or "")


def loose_pattern(key):
    """글자 사이에 분리자가 끼어 있어도 매칭되는 정규식 조각을 만든다."""
    return ("[%s]*" % SEPARATOR_CHARS).join(re.escape(ch) for ch in key)


def read_json(path):
    """UTF-8 JSON 파일을 읽는다."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, payload, *, final_newline=True):
    """UTF-8 JSON 파일을 결정적인 들여쓰기로 쓴다."""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if final_newline:
        text += "\n"
    Path(path).write_text(
        text,
        encoding="utf-8",
    )


def iso_yyyymmdd(value):
    """`YYYYMMDD` 문자열을 `YYYY-MM-DD`로 바꾼다. 다른 형식은 None."""
    text = value or ""
    if not _YYYYMMDD_RE.fullmatch(text):
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def iso_from_digit_text(value):
    """구분자가 섞인 날짜 텍스트에서 숫자 8자리를 읽어 ISO 날짜로 바꾼다."""
    digits = re.sub(r"\D", "", value or "")
    return iso_yyyymmdd(digits)


def xml_tag_text(block, name, *, unescape_html=False):
    """단순 XML 태그의 텍스트를 읽는다. CDATA 래퍼는 제거한다."""
    match = re.search(
        rf"<{name}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>",
        block,
        re.S,
    )
    value = (match.group(1) if match else "").strip()
    return html.unescape(value) if unescape_html else value
