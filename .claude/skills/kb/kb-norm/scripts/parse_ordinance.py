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

# 확인된 표기 오탈자. 법정 용어(국토계획법 제36조)는 좌변이 아니라 우변이다.
# 자동 승격 금지 — 21개 용도지역명 × 30개 조례 편집거리 스캔(거리 1~2)으로
# 나온 후보를 원문 대조로 직접 확인한 것만 담는다. 편집거리 스캔에서 나머지
# 후보(거리 3+)는 전부 원문 자체가 없는 정규식 인공물(개정일자 주석 충돌)
# 이었다 — 실체가 있는 후탈자는 이번 스캔에서 아래 하나뿐이었다.
_ZONE_VARIANTS = {
    # 고양시 도시계획 조례 제61조제1항 21호 — "자연환경보존지역"(보존)
    "자연환경보존지역": "자연환경보전지역",
}

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

_DIGITS = r"[0-9][0-9,]*(?:\.[0-9]+)?"
_OP = r"(?P<operator>이하|이상|미만|초과)?"

# '100분의 N' 은 'N퍼센트' 와 값이 같다 — 100분의 40 은 40퍼센트다(분수 표기,
# 국문 법령의 표준 백분율 표기다). 두 표기를 같은 값으로 정규화해서 읽는다.
# lp:근거발췌 에는 원문을 그대로 남긴다(원문/파생값 분리) — 정규화되는 것은
# lp:상한값 뿐이다. 실측 12엔트리·4관할(군포·오산시·인천광역시·평택시)이 이
# 표기만 써서 값_파싱실패로 격리됐었다(norm-followup 2).
#
# '1천500퍼센트' 는 '1500퍼센트' 와 값이 같다(한글 자릿수 표기, 1000 이상
# 값에서만 관측됨 — 중심상업지역 등 고밀도 상업지역 용적률). 30개 조례 전수
# 스캔에서 '1천'·'1천N00'(N 1~9) 넷만 관측됐다 — '만'·'이천' 등은 0건이라
# 지원하지 않는다(관측 안 된 표기를 미리 만들지 않는다). 실측 16엔트리·7관할
# (가평군·구리시·동두천시·부천시·서울특별시·여주시·하남시). 원문에 없는 값이
# 나오지 않게 관측된 자릿수(천)만 정확히 지원한다 — '만' 이 필요해지면 그때
# 실측하고 추가한다(norm-followup, 3건 배정 중 4번).
#
# 세 갈래를 하나의 named group('value')으로 못 묶는다 — alternation 안에서 같은
# 그룹명을 두 번 못 쓴다(Python re 제약). frac/cheon(+cheon_suffix)/pct 로
# 나누고 percent_value() 가 매치 후 어느 쪽이 잡혔는지 골라 정규화한다.
_PCT = (r"(?:100\s*분의\s*(?P<frac>" + _DIGITS + r")"
        r"|(?P<cheon>[0-9]+)\s*천\s*(?P<cheon_suffix>[0-9]{1,3})?\s*퍼센트"
        r"|(?P<pct>" + _DIGITS + r")\s*퍼센트)")


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


# 정식 표기 21종 + 확인된 오탈자를 함께 매칭 후보로 둔다 — 안 그러면
# _ZONE_FIND 가 애초에 "자연환경보존지역"을 용도지역으로 인식하지 못해
# 그 호 자체가 통째로 안 잡힌다(값이 조용히 사라진다). _ZONE_VARIANTS
# 로 넘겨받은 표기는 normalize_zone() 이 정본으로 되돌린다.
_ZONE_ALT = "|".join(_spaced(z) for z in USE_ZONES + tuple(_ZONE_VARIANTS))


def normalize_zone(s):
    """표기 변형을 IRI 키로 쓸 정식 표기로 되돌린다.

    공백은 무조건 지운다(원문 띄어쓰기 변이). 그 다음 확인된 오탈자를
    정본으로 바꾼다 — lp:근거발췌 원문은 안 건드리고 이 정규화된 값만
    zone 필드·IRI 매칭에 쓴다(원문/파생값 분리).
    """
    s = re.sub(r"\s+", "", s)
    return _ZONE_VARIANTS.get(s, s)

# 호 단위: "5. 제3종일반주거지역: 50퍼센트 이하"
# 한 호에 용도지역이 여럿 묶이는 조문이 있다 — 안산시 도시계획 조례 제52조
# "1. 일반상업지역 및 근린상업지역: 90퍼센트 이하". 단일 지역명만 잡으면 그 값이
# 통째로 사라진다. 용도지역명끼리 부분문자열 관계가 없어 alternation 순서는 무관하다.
_ZONE_ONE = r"(?:" + _ZONE_ALT + r")"
_ZONE_GROUP = _ZONE_ONE + r"(?:\s*(?:및|·|ㆍ|,|과|와)\s*" + _ZONE_ONE + r")*"
_ZONE_FIND = re.compile(_ZONE_ONE)
ITEM_RE = re.compile(
    r"(?P<no>\d+)\.\s*(?P<zones>" + _ZONE_GROUP + r")\s*[:：]?\s*"
    + _PCT + r"\s*" + _OP)

# 괄호 조건부: "(취락지구인 경우에는 40퍼센트 이하)" 또는 "(...100분의 40 이하)".
# 존재만 확인하는 1차 필터라 '퍼센트'·'분의' 중 하나만 있어도 된다 — 실제 값
# 추출은 PAREN_VALUE_RE(_PCT 그대로)가 한다.
PAREN_RE = re.compile(r"\((?P<inner>[^()]*?[0-9][^()]*?(?:퍼센트|분의)[^()]*?)\)")
PAREN_VALUE_RE = re.compile(_PCT + r"\s*" + _OP)

# 괄호 밖 '다만 … 퍼센트'/'다만 … 100분의 N' 예외 문언 스캔용. 연산자(이하 등)는
# 요구하지 않는다 — build_norm_values.py 는 값의 존재만 확인한다(기존 동작 보존).
PERCENT_VALUE_RE = re.compile(_PCT)

# '다만'/'단서'/'[' 로 시작하는 예외 문언의 존재 확인. build_norm_values.py 의
# EXCEPTION_RE 가 이 패턴을 그대로 쓴다(정본은 여기 하나) — 이 문언이 있는
# 항은 괄호 밖에도 값이 있을 수 있어 그 사각지대를 예외값_미파싱 으로 남긴다.
# parse_zone_values() 의 괄호 조건부 값 판정과는 별개다 — 그 판정은 '다만'
# 유무가 아니라 값이 호 바로 뒤에 붙어 있는지로 가른다(GAP_RE 참조).
EXCEPTION_MARK_RE = re.compile(r"다만|단서|\[")


def to_decimal(s):
    """천단위 쉼표를 뗀다. '1,000' → '1000'."""
    return s.replace(",", "")


def percent_value(match):
    """_PCT(ITEM_RE·PAREN_VALUE_RE·PERCENT_VALUE_RE 공용) 매치에서 정규화된 값을
    뽑는다. '100분의 40'·'1천500퍼센트'·'40퍼센트' 는 각각 "40"·"1500"·"40" 이다."""
    if match.group("frac") is not None:
        return to_decimal(match.group("frac"))
    if match.group("cheon") is not None:
        n = int(match.group("cheon")) * 1000 + int(match.group("cheon_suffix") or 0)
        return str(n)
    return to_decimal(match.group("pct"))


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
            out.append((m.start(), m.end(), percent_value(vm),
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
                "value": percent_value(m),
                "operator": m.group("operator"),
                "excerpt": excerpt,
                "conditional": None,
            })
        for idx, (ps, pe, pval, pop, pinner) in enumerate(parens):
            if not (start <= ps < end) or idx in used_parens:
                continue
            # 괄호 조건부 값은 이 호의 값+연산자 바로 뒤에 붙어야 한다 —
            # "20퍼센트 이하 (취락지구는 40퍼센트 이하)"처럼 사이에 공백만
            # 있어야 한다. 공백이 아닌 글자가 끼면(문장이 이어지면) 그 괄호는
            # 이 호의 조건부 값이 아니다. 실제 사례 둘(norm-followup 2) —
            # (1) 군포시 도시계획 조례 제53조제1항 13호: 값 뒤로 "다만, …
            # 설치" 프로즈가 한참 이어진 뒤에야 괄호가 나온다("…300 이하.
            # 다만, …지식산업센터를 설치(70퍼센트 이상의 경우를 말한다)하는
            # 경우에는…") — 그 괄호는 '설치'의 정의(적용 대상 조건)이지 값이
            # 아니다. (2) 수원시 도시계획 조례 제70조제1항 6호(준주거지역):
            # "일반건축은 250퍼센트"처럼 콜론과 값 사이에 낱말이 끼는 호가
            # 있으면(이 스킬 범위 밖 결손) ITEM_RE 가 그 호를 통째로 못 읽어
            # 앞 호(준주거지역)의 구간이 다음 매치까지 부풀고, 그 부푼 구간
            # 속 뒤쪽 호들의 괄호가 훨씬 뒤(자기 호가 아닌 앞 호)에 잘못
            # 붙는다 — 그 괄호들도 전부 "값 바로 뒤"가 아니라 한참 뒤에 있다.
            # 반대로 정상 사례(김포시 제59조제1항 등)는 "다만"으로 시작하는
            # 괄호라도 값 바로 뒤에 붙어 있으면 그대로 살린다 — '다만' 자체는
            # 조건부 값의 정상적인 표현이다. 판정 기준은 마커 낱말이 아니라
            # 위치(인접성)다.
            if text[m.end():ps].strip():
                continue
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
