"""조례 조문 텍스트 파서. 순수 함수만 둔다 — corpus 나 파일을 읽지 않는다.

corpus 의 provisions[].text 는 통짜 문자열이다. paragraph_count·item_count 가 0 이라
항·호 분해를 여기서 한다.

실측 함정 셋을 다룬다 (2026-08-12 도시계획조례 30종 기준).
  1) 천단위 쉼표 31건/17개 조례 — "1,000퍼센트" 를 소박한 [0-9]{1,4} 로 읽으면
     "000" 이 잡혀 값이 0 이 된다
  2) 괄호 조건부 값 98건/21개 조례 — "20퍼센트 이하 (취락지구인 경우에는 40퍼센트
     이하)". 괄호 안을 기본값과 섞으면 취락지구 값이 기본값을 덮는다
  3) 조사 누락 — "영 제85조제1항 따라" 처럼 '에' 가 빠진 원문이 있다
"""
import re

USE_ZONES = (
    "제1종전용주거지역", "제2종전용주거지역",
    "제1종일반주거지역", "제2종일반주거지역", "제3종일반주거지역",
    "준주거지역",
    "중심상업지역", "일반상업지역", "근린상업지역", "유통상업지역",
    "전용공업지역", "일반공업지역", "준공업지역",
    "보전녹지지역", "생산녹지지역", "자연녹지지역",
    "보전관리지역", "생산관리지역", "계획관리지역",
    "농림지역", "자연환경보전지역",
)

PARA_MARKS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

# 조사('에')를 요구하지 않는다 — 원문에 빠진 사례가 있다.
# 원천은 영·법 둘 다 잡는다. 조례는 "법 제6조제2항에 따라" 로도 상위를 지목하며,
# 주차장조례는 법 지목 387건 대 영 지목 36건으로 법이 압도적이다.
BASIS_RE = re.compile(
    r"(?P<source>영|법)\s*제(?P<number>\d+)조(?:의\s*(?P<branch>\d+))?"
    r"(?:\s*제(?P<paragraph>\d+)항)?(?:\s*제(?P<item>\d+)호)?")

# '법' 앞에 이 문자가 오면 다른 법률명의 꼬리다 (find_bases 주석 참조)
_WORD_TAIL = re.compile(r"[가-힣A-Za-z0-9]")
# 띄어 쓴 상대참조 지시어. '같은 법 제108조' 는 앞서 나온 다른 법률을 가리킨다
_RELATIVE_PREFIX = re.compile(r"(같은|동|본)\s+$")

_NUM = r"(?P<value>[0-9][0-9,]*(?:\.[0-9]+)?)"
_OP = r"(?P<operator>이하|이상|미만|초과)?"


def _spaced(zone):
    """용도지역명 글자 사이에 공백 0~1개를 허용한다.

    원문이 띄어 쓰는 경우가 실측 171건이다 — '제1종 전용주거지역' 165건이 압도적이고
    '준주거 지역'·'준공업 지역'·'자연녹지 지역'·'자연환경보전 지역'·'농 림 지 역' 이
    6건이다. 붙여 쓴 이름만 담으면 과천시 제54조처럼 주거지역 5종이 통째로 빠지는데,
    같은 항의 다른 호가 값을 내므로 값_파싱실패 격리도 안 걸려 조용히 사라진다.

    공백 0~1개로 171건이 전부 잡히고 과잉 매칭은 실측 0이다 — 글자 순서가 정확히
    일치해야 하므로 다른 낱말을 삼키지 않는다.
    """
    return r"[ ]?".join(map(re.escape, zone))


_ZONE_ALT = "|".join(_spaced(z) for z in USE_ZONES)


def normalize_zone(s):
    """표기 변형을 IRI 키로 쓸 정식 표기로 되돌린다."""
    return re.sub(r"\s+", "", s)

# 호 단위: "5. 제3종일반주거지역: 50퍼센트 이하"
# 한 호에 용도지역이 여럿 묶이는 조문이 있다 — 안산시 도시계획 조례 제52조
# "1. 일반상업지역 및 근린상업지역: 90퍼센트 이하". 단일 지역명만 잡으면 그 값이
# 통째로 사라진다. 용도지역명끼리 부분문자열 관계가 없어 alternation 순서는 무관하다.
_ZONE_ONE = r"(?:" + _ZONE_ALT + r")"
_ZONE_GROUP = _ZONE_ONE + r"(?:\s*(?:및|·|ㆍ|,|과|와)\s*" + _ZONE_ONE + r")*"
_ZONE_FIND = re.compile(_ZONE_ONE)
ITEM_RE = re.compile(
    r"(?P<no>\d+)\.\s*(?P<zones>" + _ZONE_GROUP + r")\s*[:：]?\s*"
    + _NUM + r"\s*퍼센트\s*" + _OP)

# 괄호 조건부: "(취락지구인 경우에는 40퍼센트 이하)"
PAREN_RE = re.compile(r"\((?P<inner>[^()]*?[0-9][^()]*?퍼센트[^()]*?)\)")
PAREN_VALUE_RE = re.compile(_NUM + r"\s*퍼센트\s*" + _OP)


def to_decimal(s):
    """천단위 쉼표를 뗀다. '1,000' → '1000'."""
    return s.replace(",", "")


def split_paragraphs(text):
    """조문 텍스트를 항 단위로 쪼갠다.

    항 표시(①②③)가 없으면 전체를 (0, text) 하나로 낸다 — 항이 없는 단문 조문이다.
    첫 항 표시 앞의 표제부는 (0, 표제부) 로 남긴다.
    """
    if not text:
        return [(0, "")]
    marks = [(i, ch) for i, ch in enumerate(text) if ch in PARA_MARKS]
    if not marks:
        return [(0, text)]
    out = []
    head = text[:marks[0][0]].strip()
    if head:
        out.append((0, head))
    for k, (pos, ch) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(text)
        body = text[pos + 1:end].strip()
        out.append((PARA_MARKS.index(ch) + 1, body))
    return out


def find_bases(text):
    """상위 근거를 등장 순서대로 전부 뽑는다.

    "법 제77조 및 영 제84조제1항에 따라" 처럼 둘이 함께 오는 조문이 있다
    (도시계획조례 287건). 첫 매치만 취하면 시행령 근거를 놓친다.

    '법' 앞에 한글·영숫자가 붙으면 버린다 — 다른 법률명의 꼬리다. 실측
    6,973 매치 중 앞에 단어가 붙은 것이 28건인데, source 가 '법' 인 27건은
    전부 오탐이었다: 타법령 인용 23건(건축물관리법 제13조 · 주택법 제15조 ·
    도로교통법 제4조 …), 상대참조 4건(동법 · 같은법). 이것들을 근거로 삼으면
    조례 계통의 법률로 잘못 해소해 없는 간선이 생긴다.

    '영' 에는 같은 조건을 걸지 않는다. 한국어 법령명이 '영' 으로 끝나지 않아
    같은 위험이 없고, 실측에서 앞에 단어가 붙은 '영' 1건은 띄어쓰기 누락
    (안산시 건축 조례 제26조 "…에 대해서는영 제27조의2제4항") 인 정탐이었다.

    띄어 쓴 상대참조 '같은 법 제N조' 도 버린다. 실측 171건(2.46%)이며 전부 앞서
    「도로법」·「영유아보육법」 같은 다른 법률을 지목한 뒤의 T3 상대참조다 —
    "「도로법」 제2조제1호에 따른 도로(같은 법 제108조에 따른 준용도로를 포함한다)".
    이걸 근거로 삼으면 도로법 제108조를 조례 계통의 법률 제108조로 해소해 전혀 다른
    법의 조문에 간선이 걸린다. 프로젝트 규칙상 상대참조는 선행 명칭이 확인되기 전에는
    간선으로 만들지 않는다.
    """
    text = text or ""
    out = []
    for m in BASIS_RE.finditer(text):
        i = m.start()
        if _RELATIVE_PREFIX.search(text[max(0, i - 4):i]):
            continue
        if m.group("source") == "법" and i > 0 and _WORD_TAIL.match(text[i - 1]):
            continue
        out.append({
            "source": m.group("source"),
            "number": m.group("number"),
            "branch": m.group("branch"),
            "paragraph": m.group("paragraph"),
            "item": m.group("item"),
            "raw": m.group(0).replace(" ", ""),
        })
    return out


def find_basis(text, source=None):
    """근거 하나를 고른다. source 를 주면 그 원천의 첫 매치, 없으면 전체 첫 매치."""
    for b in find_bases(text):
        if source is None or b["source"] == source:
            return b
    return None


def _paren_spans(text):
    """괄호 조건부 구간과 그 안의 (값, 연산자, 원문)."""
    out = []
    for m in PAREN_RE.finditer(text):
        inner = m.group("inner")
        vm = PAREN_VALUE_RE.search(inner)
        if vm:
            out.append((m.start(), m.end(), to_decimal(vm.group("value")),
                        vm.group("operator"), inner.strip()))
    return out


def parse_zone_values(text):
    """호 단위 (용도지역, 값)을 뽑는다. 괄호 조건부 값은 별도 레코드로 낸다.

    반환 순서는 원문 등장 순서다 — 정렬은 호출부가 IRI 로 한다.
    """
    if not text:
        return []
    parens = _paren_spans(text)
    used_parens = set()
    out = []
    for m in ITEM_RE.finditer(text):
        # 이 호가 차지하는 구간: 이 매치 시작부터 다음 호 시작 직전까지
        start = m.start()
        nxt = ITEM_RE.search(text, m.end())
        end = nxt.start() if nxt else len(text)
        excerpt = text[start:end].strip()
        # 한 호에 묶인 용도지역을 각각 별개 레코드로 낸다
        zones = [normalize_zone(z) for z in _ZONE_FIND.findall(m.group("zones"))]
        for z in zones:
            out.append({
                "zone": z,
                "value": to_decimal(m.group("value")),
                "operator": m.group("operator"),
                "excerpt": excerpt,
                "conditional": None,
            })
        for idx, (ps, pe, pval, pop, pinner) in enumerate(parens):
            if start <= ps < end and idx not in used_parens:
                used_parens.add(idx)
                for z in zones:
                    out.append({
                        "zone": z,
                        "value": pval,
                        "operator": pop,
                        "excerpt": excerpt,
                        "conditional": pinner,
                    })
    return out
