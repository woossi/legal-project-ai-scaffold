#!/usr/bin/env python3
"""수립지침 항 본문에서 규범 단위를 뽑는다.

장·절·항 번호는 주소체계일 뿐이고, 지침이 말하려는 것은 본문에 있다. 이
스크립트는 항 본문을 규범 단위로 쪼개고 각 단위가 무엇을 어떤 구속 양식으로
말하는지를 관측한다.

규범 단위는 문장이 아니다
  머리문장이 `…는 다음과 같다` 로 열고 하위목이 그 목록이면, 하위목 하나하나가
  독립 규범이 아니라 머리문장의 목적어다. 하위목을 그대로 세면 규범 수가
  부풀려진다. 그래서 머리↔목의 열거 관계를 단위마다 붙인다.

문말서법은 관측이지 구속력 판정이 아니다
  `…하여야 한다`·`…할 수 있다` 같은 문말 표현을 그대로 적는다. 이것을 법적
  구속력(L2)으로 승격하는 것은 이 산출물의 일이 아니다. 승격하려면 상위법
  위임 관계를 함께 봐야 한다.

수치가 있다고 규범값이 아니다
  실측하면 `예를 들어` 안의 계산 예시, 재검토기한 같은 시점, 정의문의 파라미터가
  섞여 있다. 맥락을 갈라 담고, 가르지 못한 것은 `미분류` 로 두고 비운다.

입력  output/legal/statute/수립지침_항구조.json
출력  output/legal/statute/수립지침_규범단위.json
      output/legal/statute/_수립지침_규범단위_리포트.json
"""

import argparse
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

SCRIPT_PATH = ".claude/skills/legal/legal-statute/scripts/build_guideline_norm_units.py"
DEFAULT_STRUCTURE = "output/legal/statute/수립지침_항구조.json"
DEFAULT_OUT_DIR = "output/legal/statute"

RE_ITEM_PAREN = re.compile(r"^\s*\((\d+)\)\s*")
RE_ITEM_CIRCLE = re.compile(r"^\s*([①-⑳])\s*")
RE_IMG = re.compile(r"^\s*</?img")

# 머리문장이 목록을 여는가. 이 표현이 있으면 뒤따르는 목은 머리의 목적어다.
RE_ENUM_INTRO = re.compile(
    r"(?:다음|아래)(?:[^.]{0,24})?"
    r"(?:같다|따른다|고려한다|의한다|정한다|본다|검토한다|한다|참고한다)\s*\.?$")

# 문말 표현 계열. 각 값은 표현 묶음이지 법적 구속력 등급이 아니다.
# 긴 것부터 본다 — `할 수 없다` 를 `수 있다` 보다 먼저 걸러야 한다.
#
# `않도록 한다` 를 금지에 넣었더니 대표 사례가 `양 계획간 상충이 발생하지 않도록
# 한다` 로 잡혔다. 결과 회피 지시이지 행위 금지가 아니다. 그래서 금지는
# `할 수 없다`·`하지 아니한다` 계열로 좁히고 `않도록 한다`·`지양한다` 는
# 부정지시로 뺐다.
MODALITY = OrderedDict([
    ("금지", re.compile(
        r"(?:할\s*수\s*없다|하지\s*(?:아니한다|않는다|못한다)|넘을\s*수\s*없다"
        r"|하여서는\s*아니\s*된다|아니\s*된다|금지한다)$")),
    ("의무", re.compile(
        r"(?:하?여야\s*한다|해야\s*한다|되어야\s*한다|받아야\s*한다"
        r"|의무화한다)$")),
    ("원칙", re.compile(r"원칙으로\s*한다$")),
    ("재량", re.compile(r"(?:수\s*있다|가능하다)$")),
    ("권고", re.compile(r"(?:바람직하다|좋다|적당하다|권장한다|권장하도록\s*한다)$")),
    ("부정지시", re.compile(r"(?:않도록\s*한다|지양한다|피한다)$")),
    ("지시", re.compile(r"(?:도록\s*한다|유도한다)$")),
    ("서술", re.compile(r"[가-힣]다$")),
])
RE_DEFINITION = re.compile(r"(?:이란|란|라\s*함은|이라\s*함은|을\s*말한다|라\s*한다)")
RE_DEFINITION_TAIL = re.compile(r"(?:말한다|라\s*한다)$")
# 서술어. 한국어 종결 서술어는 `…다` 로 끝난다. 이것이 없으면 명사구다.
# 어휘 화이트리스트로 명사구를 잡으려 했더니 `시범도시`·`간판의 크기ㆍ형태`
# 같은 열린 집합을 못 받아 66건이 미판정으로 남았다. 어휘가 아니라 서술어
# 유무로 가른다.
RE_PREDICATE = re.compile(r"[가-힣]다$")
RE_DELETED = re.compile(r"^(?:<\s*삭제\s*>|삭제)$")

# 수치+단위. 꼬리 한글 차단(`(?![가-힣])`)을 넣으면 `200%를`·`5%이상`·`10m이상`
# 같은 실규범값 52건이 조용히 사라진다. 넣지 않는다.
# `호` 는 단위가 아니라 법조문 인용 단위(`제1호`)라서 뺀다.
RE_NUMERIC = re.compile(
    r"\d+(?:,\d{3})*(?:\.\d+)?\s*"
    r"(?:%|퍼센트|㎡|제곱미터|km|m|미터|층|년|일|개월|배|명|톤|도)")
RE_DATE = re.compile(r"\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일")
RE_LIMIT = re.compile(
    r"(?:이내|이하|이상|초과|미만|미달|넘을\s*수\s*없다|넘지\s*않도록|까지\s*완화"
    r"|한도|범위안에서|를\s*기준|수준에서|안에서|내에서|내외|적당|까지)")
RE_EXAMPLE = re.compile(r"(?:예를\s*들어|예\)|예시는|예컨대)")
# 기간·기한 단위. 이 단위는 규모 한도가 아니라 시간이다.
RE_DURATION_UNIT = re.compile(r"(?:년|일|개월)\s*$")
# `100～150m` 처럼 물결로 이은 구간값.
RE_RANGE = re.compile(r"[~～∼]\s*$|^\s*\d")
# `1층 전면`·`지하 1층` 처럼 위치를 가리키는 층 표기.
RE_FLOOR_POSITION = re.compile(r"(?:전면|지하|지상|벽면|입지|용도지정|층별)")

RE_XREF_INNER = re.compile(r"\d+(?:-\d+){1,3}(?:\s*\.)?")
RE_XREF_OUTER = re.compile(r"「([^」]{2,60})」")

# 계획규범 내용 구조의 축 A(규범층위)·C(수범자)를 채우기 위한 관측.
# 둘 다 판정이 아니라 문면 관측이며, 뽑히지 않으면 비운다.
# 정본은 `references/계획규범-내용구조.md`.
_VERB_TAILS = [
    r"하?여야\s*한다", r"해야\s*한다", r"하?도록\s*한다", r"하지\s*아니한다",
    r"하지\s*않는다", r"할\s*수\s*있다", r"될\s*수\s*있다", r"할\s*수\s*없다",
    r"하여서는\s*아니\s*된다", r"원칙으로\s*한다", r"바람직하다", r"적당하다",
    r"한다", r"된다", r"있다", r"없다", r"좋다",
]
RE_VERB_TAIL = re.compile("(?:%s)$" % "|".join(_VERB_TAILS))
RE_VERB_STEM = re.compile(r"([가-힣]{1,8})$")
RE_STEM_SUFFIX = re.compile(r"(?:하|되|시켜|시키|지|않|아니|토록|도록)$")
# 어간으로 볼 수 없는 잔재. `것이 바람직하다`·`수 있다` 류가 여기 걸린다.
RE_STEM_REJECT = re.compile(r"^(?:것이?|것을|수|있|없|되|못|의|가|주|잘|맞|등)$")
DIRECTIVE = frozenset(
    {"의무", "금지", "재량", "지시", "부정지시", "권고", "원칙"})
RE_SUBJECT = re.compile(
    r"^(?:\(\d+\)|[①-⑳]|\d+(?:-\d+){1,3}\s*\.)?\s*([^,]{2,28}?)(?:은|는|이|가)\s")


def split_units(hang):
    """항 본문을 머리·하위목·연속 단위로 쪼갠다. 원문은 그대로 둔다."""
    lines = hang["본문"].split("\n")
    start = hang["원문줄범위"]["시작"]
    units, cur_paren = [], None
    for offset, line in enumerate(lines):
        text = line.strip()
        if not text or RE_IMG.match(line):
            continue
        if offset == 0:
            units.append({"목경로": "머리", "단위유형": "머리",
                          "원문줄": start, "원문": text})
            continue
        m = RE_ITEM_PAREN.match(line)
        if m:
            cur_paren = m.group(1)
            units.append({"목경로": "(%s)" % cur_paren, "단위유형": "하위목",
                          "원문줄": start + offset, "원문": text})
            continue
        m = RE_ITEM_CIRCLE.match(line)
        if m:
            path = "(%s)%s" % (cur_paren, m.group(1)) if cur_paren else m.group(1)
            units.append({"목경로": path, "단위유형": "하위목",
                          "원문줄": start + offset, "원문": text})
            continue
        units.append({"목경로": "연속", "단위유형": "연속",
                      "원문줄": start + offset, "원문": text})
    return units


def normalize_tail(text):
    """문말 판정용 꼬리를 만든다. 본문 자체는 바꾸지 않는다.

    두 가지를 벗긴다.
      맺음 부호   `…적용할 수 있다)` 처럼 닫는 괄호가 붙으면 문말을 놓친다
      꼬리 괄호절 `…계획한다(입체적인 스케치 모델 예시 포함).` 은 괄호 안이
                 `포함` 으로 끝나 서술어가 없는 것처럼 보인다. 이 한 건이
                 3-1-11 을 표제로 오판하게 했다

    순서가 중요하다. 맺음 부호를 먼저 벗기면 꼬리 괄호절의 닫는 괄호까지
    먹어버려 괄호절 제거가 무력해진다. 마침표·공백만 먼저 벗기고 괄호절을
    통째로 떼어낸 뒤에 남은 부호를 정리한다.
    """
    tail = text.rstrip()
    for _ in range(4):
        prev = tail
        tail = re.sub(r"[\s.]+$", "", tail)
        tail = re.sub(r"\s*\([^()]*\)$", "", tail)
        tail = re.sub(r"[』」’”\'\"\]]+$", "", tail)
        tail = re.sub(r"\)$", "", tail)
        if tail == prev:
            break
    return tail


def main_verb(text):
    """지시문의 본동사 어간을 관측한다. 뽑히지 않으면 None.

    이것으로 규범층위(축 A)를 자동 판정하지 않는다. 지시성 296건 안에서도
    어간이 99종으로 흩어져 어휘 사전으로 전수 분류하면 근거 없는 판정이
    들어간다. 여기서는 어간만 관측하고 층위는 비운다.
    """
    tail = re.sub(r"\s*\([^()]*\)\s*$", "", text.rstrip())
    tail = re.sub(r"[\s.]+$", "", tail)
    m = RE_VERB_TAIL.search(tail)
    if not m:
        return None
    stem = RE_VERB_STEM.search(tail[:m.start()].rstrip())
    if not stem:
        return None
    value = RE_STEM_SUFFIX.sub("", stem.group(1))
    if not value or RE_STEM_REJECT.match(value):
        return None
    return value


def explicit_subject(text):
    """문두 명시 주어를 관측한다. 수범자 판정이 아니다.

    실측하면 최빈 주어가 수범자가 아니라 규율 객체다(`지구단위계획`·
    `당해 구역`). 사람 수범자는 드물고 무주어 지시문이 지배적이다.
    """
    m = RE_SUBJECT.match(text)
    if not m:
        return None
    value = m.group(1).strip()
    return value or None


def trailing_paren(text):
    """문말 판정에서 떼어낸 꼬리 괄호절을 그대로 돌려준다. 없으면 None."""
    m = re.search(r"\(([^()]*)\)[\s.]*$", text.rstrip())
    return m.group(0) if m else None


def modality_of(text, is_enum_item, is_title):
    """문말 표현을 관측한다. 구속력 판정이 아니다."""
    tail = normalize_tail(text)
    if RE_DELETED.match(re.sub(r"^[^\s]*\s*", "", text).strip()) \
            or RE_DELETED.match(tail):
        return "삭제", "본문이 삭제 표기뿐이다"
    if is_title:
        return "표제", "머리가 서술어 없이 끝나고 하위목이 뒤따른다"
    if is_enum_item:
        return "열거항목", "머리문장이 연 목록의 항목이다"
    if RE_DEFINITION.search(tail) and RE_DEFINITION_TAIL.search(tail):
        return "정의", "정의문 도입부와 `말한다` 가 함께 있다"
    for name, pattern in MODALITY.items():
        m = pattern.search(tail)
        if m:
            return name, m.group(0).strip()
    if not RE_PREDICATE.search(tail):
        return "명사구", "종결 서술어가 없다"
    return "미판정", ""


def numeric_context(body, match, hang_body=None, in_example_block=False):
    """수치의 맥락을 가른다. 가르지 못하면 미분류로 둔다.

    `예를 들어` 가 다른 줄에 있는 경우가 있어(3-2-2-3 의 가중치 산정 예시)
    항 본문 수준의 예시 구간도 함께 받는다.
    """
    around = body[max(0, match.start() - 8):match.end() + 10]
    if RE_DATE.search(around):
        return "시점"
    before = body[max(0, match.start() - 140):match.start()]
    dot = before.rfind(".")
    sentence_head = before[dot + 1:] if dot >= 0 else before
    if in_example_block or RE_EXAMPLE.search(sentence_head):
        return "예시값"
    if RE_DEFINITION.search(body[max(0, match.start() - 90):match.end() + 40]):
        return "정의파라미터"
    # 괄호절이 끼어 한도어가 멀어지는 경우가 있어 뒤쪽은 괄호를 지우고 다시 본다.
    after = body[match.end():match.end() + 40]
    after_flat = re.sub(r"\([^()]*\)", "", after)
    if (RE_LIMIT.search(after[:24]) or RE_LIMIT.search(after_flat[:24])
            or RE_LIMIT.search(body[max(0, match.start() - 28):match.start()])):
        return "한도·비율"
    token = match.group(0)
    if RE_DURATION_UNIT.search(token):
        return "기간·기한"
    if token.endswith("층") and RE_FLOOR_POSITION.search(
            body[max(0, match.start() - 20):match.end() + 20]):
        return "위치지시"
    if re.search(r"[~～∼]\s*$", body[max(0, match.start() - 3):match.start()]):
        return "구간값"
    return "미분류"


def build(structure_path):
    structure = json.loads(Path(structure_path).read_text(encoding="utf-8"))
    sections = {(s["장번호"], s["절번호"]): s["절제목"]
                for s in structure["절목록"]}
    chapters = {c["장번호"]: c["장제목"] for c in structure["장목록"]}

    records = []
    for hang in structure["항목록"]:
        units = split_units(hang)
        if not units:
            continue
        head_text = units[0]["원문"]
        has_sub = any(u["단위유형"] == "하위목" for u in units)
        enum_intro = bool(RE_ENUM_INTRO.search(head_text))
        head_is_noun = not RE_PREDICATE.search(normalize_tail(head_text))
        is_title = head_is_noun and has_sub and not enum_intro
        head_id = "%s#머리" % hang["항번호"]
        # 예시 구간은 줄을 넘어간다 — `예)` 줄 다음의 계산줄까지가 예시다.
        example_lines = set()
        seen_example = False
        for u in units:
            if RE_EXAMPLE.search(u["원문"]):
                seen_example = True
            if seen_example and u["단위유형"] in ("머리", "연속"):
                example_lines.add(u["원문줄"])

        for unit in units:
            text = unit["원문"]
            is_enum_item = (unit["단위유형"] == "하위목" and enum_intro
                            and not RE_PREDICATE.search(normalize_tail(text)))
            modality, evidence = modality_of(
                text, is_enum_item, is_title and unit["단위유형"] == "머리")

            values = []
            in_example = unit["원문줄"] in example_lines
            for m in RE_NUMERIC.finditer(text):
                values.append({
                    "표기": m.group(0),
                    "맥락": numeric_context(text, m, in_example_block=in_example),
                    "근거발췌": text[max(0, m.start() - 45):m.end() + 25],
                })
            inner = sorted({x.rstrip(" .") for x in RE_XREF_INNER.findall(text)})
            outer = sorted(set(RE_XREF_OUTER.findall(text)))

            records.append({
                "단위id": "%s#%s" % (hang["항번호"], unit["목경로"]),
                "항번호": hang["항번호"],
                "목경로": unit["목경로"],
                "단위유형": unit["단위유형"],
                "장번호": hang["장번호"],
                "장제목": chapters.get(hang["장번호"]),
                "절번호": hang["절번호"],
                "절제목": sections.get((hang["장번호"], hang["절번호"])),
                "원문": text,
                "원문줄": unit["원문줄"],
                "열거관계": {
                    "머리가_열거도입": enum_intro,
                    "열거항목": is_enum_item,
                    "상위단위id": head_id if unit["단위유형"] != "머리" else None,
                },
                "문말서법": {
                    "값": modality,
                    "근거표현": evidence,
                    # 문말 판정에서 뗀 꼬리 괄호절. 단서·예외가 여기 들어가는
                    # 경우가 있어(3-2-10 의 도시지역외 완화) 버리지 않고 남긴다.
                    "꼬리괄호절": trailing_paren(text),
                },
                "값관측": values,
                # 계획규범 내용 구조의 축 A·C 를 채우기 위한 문면 관측.
                # 판정이 아니므로 뽑히지 않으면 비운다.
                "문면관측": {
                    "본동사어간": main_verb(text) if modality in DIRECTIVE else None,
                    "명시주어": explicit_subject(text),
                },
                "교차참조": {"지침내부": inner, "외부자료": outer},
            })
    return structure, records


def report(structure, records):
    mod = Counter(r["문말서법"]["값"] for r in records)
    kind = Counter(r["단위유형"] for r in records)
    ctx = Counter(v["맥락"] for r in records for v in r["값관측"])

    by_chapter = {}
    for hang in structure["항목록"]:
        by_chapter.setdefault(hang["장번호"], {"항": 0, "값보유항": 0})
        by_chapter[hang["장번호"]]["항"] += 1
    hang_with_value = set()
    for r in records:
        if r["값관측"]:
            hang_with_value.add(r["항번호"])
    for hang in structure["항목록"]:
        if hang["항번호"] in hang_with_value:
            by_chapter[hang["장번호"]]["값보유항"] += 1

    limit_by_chapter = Counter()
    for r in records:
        for v in r["값관측"]:
            if v["맥락"] == "한도·비율":
                limit_by_chapter[r["장번호"]] += 1

    samples = {}
    for r in records:
        samples.setdefault(r["문말서법"]["값"], []).append(
            {"단위id": r["단위id"], "근거표현": r["문말서법"]["근거표현"],
             "원문": r["원문"][:130]})

    unresolved = [
        {"단위id": r["단위id"], "원문": r["원문"][:120]}
        for r in records if r["문말서법"]["값"] == "미판정"]
    unclassified = [
        {"단위id": r["단위id"], "표기": v["표기"], "근거발췌": v["근거발췌"]}
        for r in records for v in r["값관측"] if v["맥락"] == "미분류"]

    # 항 본문의 어느 줄이 규범 단위로 가지 못했는지 원자료로 다시 센다.
    covered = {r["원문줄"] for r in records}
    missed = []
    for hang in structure["항목록"]:
        start = hang["원문줄범위"]["시작"]
        for offset, line in enumerate(hang["본문"].split("\n")):
            if not line.strip() or RE_IMG.match(line):
                continue
            if start + offset not in covered:
                missed.append({"줄": start + offset, "원문": line[:90]})
    body_lines = sum(
        1 for hang in structure["항목록"]
        for line in hang["본문"].split("\n")
        if line.strip() and not RE_IMG.match(line))
    coverage = {
        "항본문_내용줄": body_lines,
        "규범단위": len(records),
        "미배정줄": len(missed),
        "미배정목록": missed,
        "이미지태그줄_제외": sum(
            1 for hang in structure["항목록"]
            for line in hang["본문"].split("\n") if RE_IMG.match(line)),
    }

    directive = [r for r in records if r["문말서법"]["값"] in DIRECTIVE]
    verbs = Counter(r["문면관측"]["본동사어간"] for r in directive
                    if r["문면관측"]["본동사어간"])
    subjects = Counter(r["문면관측"]["명시주어"] for r in records
                       if r["문면관측"]["명시주어"])
    axes = {
        "정본": ".claude/skills/legal/legal-statute/references/계획규범-내용구조.md",
        "축A_규범층위": {
            "상태": "미완",
            "사유": "본동사 어간이 열린 집합이라 지시성 단위 안에서도 흩어진다. "
                  "어휘 사전으로 전수 분류하면 근거 없는 판정이 들어간다",
            "지시성_단위": len(directive),
            "본동사_추출": sum(1 for r in directive
                          if r["문면관측"]["본동사어간"]),
            "본동사_어간종수": len(verbs),
            "본동사_빈도": dict(verbs.most_common(40)),
        },
        "축C_수범자": {
            "상태": "부분",
            "사유": "무주어 지시문이 지배적이고, 명시 주어의 최빈은 수범자가 "
                  "아니라 규율 객체다",
            "명시주어_보유": sum(1 for r in records
                          if r["문면관측"]["명시주어"]),
            "전체단위": len(records),
            "명시주어_빈도": dict(subjects.most_common(25)),
        },
        "축B_규범성격": {"상태": "완료", "근거필드": "문말서법.값"},
        "축E_규율객체": {"상태": "절 단위까지", "근거필드": "절제목"},
        "축G_규정형식": {"상태": "완료", "근거필드": "값관측[].맥락"},
        "미구현": ["축D 규범권위(문서 단위 고정)", "축F 적용조건", "축H 확정도"],
    }
    return {
        "meta": {
            "스크립트": SCRIPT_PATH,
            "대상산출물": "output/legal/statute/수립지침_규범단위.json",
            "입력": structure["meta"]["생성근거"],
        },
        "모수": {
            "항": len(structure["항목록"]),
            "규범단위": len(records),
            "단위유형별": dict(kind),
            "열거도입_머리": sum(1 for r in records
                            if r["단위유형"] == "머리"
                            and r["열거관계"]["머리가_열거도입"]),
            "열거항목": sum(1 for r in records if r["열거관계"]["열거항목"]),
        },
        "문말서법_분포": dict(mod.most_common()),
        "문말서법_대표사례": {k: v[:3] for k, v in samples.items()},
        "값관측": {
            "총": sum(len(r["값관측"]) for r in records),
            "맥락별": dict(ctx.most_common()),
            "값보유_항": len(hang_with_value),
            "장별": {str(k): v for k, v in sorted(by_chapter.items())},
            "한도·비율_장별": {str(k): v for k, v in sorted(limit_by_chapter.items())},
            "전수_한도비율": [
                {"단위id": r["단위id"], "장번호": r["장번호"], "절제목": r["절제목"],
                 "표기": v["표기"], "근거발췌": v["근거발췌"]}
                for r in records for v in r["값관측"]
                if v["맥락"] == "한도·비율"],
        },
        "미판정": {"문말서법": unresolved, "값맥락": unclassified},
        "커버리지": coverage,
        "내용구조_축관측": axes,
        "격리": [
            {"대상": "문말서법 → 법적 구속력(L2) 승격",
             "사유": "문말 표현은 표면 지표다. `…할 수 있다` 가 재량인지 상위법이 "
                   "준 완화 권한인지는 위임 관계를 함께 봐야 정해진다",
             "처리": "`문말서법` 으로만 담고 L2 값 도메인에 매핑하지 않았다"},
            {"대상": "규율대상(무엇에 관한 규정인가) 어휘",
             "사유": "`건폐율`·`높이` 같은 어휘를 부분문자열로 맞추면 `지표층`·"
                   "`표고` 류 오탐이 들어온다. 이 라운드에서 전수 확인을 하지 못했다",
             "처리": "필드를 만들지 않았다. 현재 규율대상의 근거는 절 제목(L1)뿐이다"},
        ],
    }


def write_json(path, payload):
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--structure", default=DEFAULT_STRUCTURE)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    a = ap.parse_args()

    structure_path = Path(a.structure)
    if not structure_path.exists():
        print(f"입력 없음: {structure_path}", file=sys.stderr)
        return 1

    structure, records = build(structure_path)
    rep = report(structure, records)

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "수립지침_규범단위.json", {
        "meta": {
            "생성근거": structure["meta"]["생성근거"],
            "스크립트": SCRIPT_PATH,
            "입력산출물": str(structure_path),
            "정렬": "원문 줄번호 오름차순",
            "모수": rep["모수"],
            "판정규약": {
                "규범단위": "항 본문의 머리문장·하위목·연속줄. 이미지 태그 줄은 뺀다",
                "열거항목": "머리가 목록을 열고 목이 서술어 없이 끝나면 그 목은 "
                        "독립 규범이 아니라 머리의 목적어다",
                "문말서법": "문말 표현의 관측이다. 법적 구속력 판정이 아니다",
                "값관측": "수치+단위의 관측이다. 맥락을 갈라 담되 가르지 못한 것은 "
                       "`미분류` 로 둔다. 수치가 있다고 규범값인 것은 아니다",
                "만들지_않은_필드": "규율대상 어휘. 부분문자열 오탐을 전수로 확인하지 "
                             "못해 두지 않았다",
            },
        },
        "규범단위": records,
    })
    write_json(out_dir / "_수립지침_규범단위_리포트.json", rep)

    m = rep["모수"]
    print(f"항 {m['항']} → 규범단위 {m['규범단위']} "
          f"({m['단위유형별']}), 열거항목 {m['열거항목']}")
    print(f"문말서법: {rep['문말서법_분포']}")
    v = rep["값관측"]
    print(f"값관측 {v['총']}건 / 값보유 항 {v['값보유_항']} / 맥락 {v['맥락별']}")
    print(f"미판정: 서법 {len(rep['미판정']['문말서법'])} · "
          f"값맥락 {len(rep['미판정']['값맥락'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
