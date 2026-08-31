"""축 명세 구동 파서. 순수 함수만 둔다 — corpus 나 파일을 읽지 않는다.

kb-norm 의 parse_ordinance.py 가 (용도지역, 퍼센트) 한 형태를 붙박이로 다뤘다면
여기는 주어타입·값타입을 인자로 받아 축마다 다른 형태를 같은 골격으로 읽는다.

골격 5단계는 kb-norm 과 같다.
    근거항 찾기 → 호 분해 → 주어 잡기 → 값 잡기 → 조건 가르기 → 격리

실측으로 확인한 표기 변형(2026-08-13, 조례 90종)만 다룬다. 계약의 표기규약이 정본이다.
    개정마크업   <개정 2015.2.26.> 가 주어 앞에 붙어 '2.26.> 1. 주거지역' 이 된다 (분할 4건)
    한글천단위   1천500퍼센트 · 1천제곱미터
    분수비율     100분의 15 → 15퍼센트 (조경 26 · 공개공지 18)
    분수배율     2분의 1 → 0.5배 (일조 31)
    전각콜론     ： (과천시)
"""
import re

# ── 정규화 ────────────────────────────────────────────────────────────────

# <개정 …> <신설 …> <삭제 …> <본조신설 …>. 값·주어를 잡기 전에 지운다.
MARKUP_RE = re.compile(r"<[^<>\n]{0,80}>")
# 괄호로 쓴 개정 표기. 조례마다 표기가 다르다 — 대부분 <개정 …> 이지만
# 수원·부천 등은 (개정 2015.12.31) 처럼 소괄호를 쓴다. 각괄호만 지우면
# 이 표기가 주어원문에 그대로 남아 근거 문자열이 오염된다(실측 2026-08-14
# 일조 50건이 '…1배 이상으로 한다. 018.02.12) (개정 2022.11.10)' 이 됐다).
# 값은 정상이라 환각 검사에도 안 걸린다 — 근거 필드만 더러워진다.
MARKUP_PAREN_RE = re.compile(
    r"[(（]\s*(?:개정|신설|삭제|본조신설|전문개정|단서삭제)[^)）\n]{0,40}[)）]")

PARA_MARKS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def strip_markup(text):
    """개정 마크업을 지우고 전각 콜론을 반각으로 되돌린다.

    지우지 않으면 마크업 안의 날짜가 호 번호로 읽힌다 — '<개정 2015.2.26.> 1. 주거지역'
    에서 '2.26.' 의 '2.' 를 호 번호로 잡아 주어가 '26.> 1. 주거지역' 이 된다.
    실측 4건(분할 축)이며 값은 정상이라 격리도 안 걸리고 조용히 오염된다.
    """
    if not text:
        return ""
    text = MARKUP_RE.sub(" ", text)
    return MARKUP_PAREN_RE.sub(" ", text).replace("：", ":")


def to_number(s):
    """'1,000' → 1000.0, '1천500' → 1500.0, '1천' → 1000.0, '1만' → 10000.0,
    '1천5백' → 1500.0.

    한글 만·천 단위는 조례 원문에 흔하다. '1천500퍼센트' 를 [0-9]+ 로 읽으면 1 이
    잡힌다. 만 단위는 공개공지에서 '연면적 합계가 1만 제곱미터 이상 2만제곱미터
    미만' 처럼 구간 주어에 쓰여, 없으면 그 호가 통째로 주어_미상 으로 빠진다.
    """
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace(" ", "")
    if not s:
        return None
    m = re.fullmatch(r"(?:(\d*)만)?(?:(\d*)천)?(?:(\d*)백)?(\d*)", s)
    if m and any(m.group(i) is not None for i in (1, 2, 3)):
        def part(g, mult):
            if g is None:
                return 0
            return (int(g) if g else 1) * mult
        return float(part(m.group(1), 10000) + part(m.group(2), 1000)
                     + part(m.group(3), 100)
                     + (int(m.group(4)) if m.group(4) else 0))
    try:
        return float(s)
    except ValueError:
        return None


def fmt_number(v):
    """IRI·리터럴용 표기. 정수는 소수점을 떼 멱등성을 지킨다."""
    if v is None:
        return None
    return str(int(v)) if float(v).is_integer() else str(v)


# 자릿수 표기 사이의 공백을 허용한다 — 원문에 '1천 500 제곱미터' 가 있다.
# 허용하지 않으면 500 만 잡혀 구간 하한이 1500 대신 500 이 된다(실측 광주 제31조).
_NUM = r"[0-9](?:[0-9,]|[천만백](?=[0-9\s])|\s(?=[0-9]))*[0-9만천백]?(?:\.[0-9]+)?"
OPERATORS = ("이하", "이상", "미만", "초과")
_OP = r"(?:" + "|".join(OPERATORS) + r")"


def split_paragraphs(text):
    """조문을 항 단위로 쪼갠다. kb-norm split_paragraphs 와 같은 규약이다."""
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
        out.append((PARA_MARKS.index(ch) + 1, text[pos + 1:end].strip()))
    return out


ITEM_HEAD_RE = re.compile(r"(?:^|\s)(\d{1,2})\.\s+")


# 목 표시. '가. 해당용도로 … : 대지면적의 5퍼센트'
SUBITEM_HEAD_RE = re.compile(r"(?:^|\s)([가-힣])\.\s+")
_SUBITEM_ORDER = "가나다라마바사아자차카타파하"


def split_items(text):
    """항 본문을 호 단위로 쪼갠다. (호번호, 본문) 목록과 선두부를 낸다.

    호 번호 뒤 공백을 요구한다. 요구하지 않으면 '2.26.' 같은 날짜 잔재가 호로 잡힌다 —
    strip_markup 이 앞단에서 걸러도 본문에 남은 날짜에 대한 2차 방어다.

    호 안에 목(가·나·다)이 있고 목마다 값이 다르면 목 단위로 더 쪼갠다. 쪼개지
    않으면 목 3개의 값이 호 하나로 뭉쳐 첫 값만 남고 나머지가 사라진다 —
    실측(2026-08-13) 성남시 건축 조례 제21조①2 의 가·나·다목 5/7/10퍼센트가
    격리 1건으로만 기록됐다. 호 번호는 유지하고 목 순번을 소수부로 붙인다
    (2 → 2.1, 2.2, 2.3) — 호 번호를 잃으면 근거 위치를 못 짚는다.
    """
    heads = list(ITEM_HEAD_RE.finditer(text or ""))
    if not heads:
        return []
    out = []
    for k, m in enumerate(heads):
        end = heads[k + 1].start() if k + 1 < len(heads) else len(text)
        no, body = int(m.group(1)), text[m.end():end].strip()
        subs = list(SUBITEM_HEAD_RE.finditer(body))
        if len(subs) < 2:
            out.append((no, body))
            continue
        for j, sm in enumerate(subs):
            s_end = subs[j + 1].start() if j + 1 < len(subs) else len(body)
            idx = _SUBITEM_ORDER.find(sm.group(1))
            out.append((no + (idx + 1) / 10 if idx >= 0 else no,
                        body[sm.end():s_end].strip()))
    return out


# ── 값 매처 (값타입별) ────────────────────────────────────────────────────

# 비율: '대지면적의 5퍼센트 이상' · '대지면적의 100분의 15 이상'
_RATIO_PCT = re.compile(
    r"(?:(?P<basis>대지면적|연면적)의\s*)?(?P<value>" + _NUM + r")\s*퍼센트"
    r"(?:\s*(?P<op>" + _OP + r"))?")
_RATIO_FRAC = re.compile(
    r"(?:(?P<basis>대지면적|연면적)의\s*)?100\s*분의\s*(?P<value>" + _NUM + r")"
    r"(?:\s*(?P<op>" + _OP + r"))?")
# 면적: '60제곱미터'
_AREA = re.compile(r"(?P<value>" + _NUM + r")\s*제곱미터(?:\s*(?P<op>" + _OP + r"))?")
# 거리: '1.5미터'. 제곱미터를 삼키지 않도록 앞을 막는다
_DIST = re.compile(
    r"(?<!제곱)(?P<value>" + _NUM + r")\s*미터(?:\s*(?P<op>" + _OP + r"))?")
# 배율: '2분의 1' · '1배' · '0.5배'. 100분의N 은 비율이므로 뺀다
_RATE_FRAC = re.compile(
    r"(?<!\d)(?P<den>\d{1,2})\s*분의\s*(?P<num>\d{1,2})"
    r"(?:\s*(?P<op>" + _OP + r"))?")
_RATE_X = re.compile(r"(?P<value>" + _NUM + r")\s*배(?:\s*(?P<op>" + _OP + r"))?")


def _hit(value, unit, op, basis, raw):
    return {"값": fmt_number(value), "단위": unit, "비교연산": op,
            "값기준": basis, "값원문": raw.strip()}


def mask_spans(text, spans, fill="　"):
    """구간을 같은 길이의 채움문자로 덮는다. 오프셋을 보존해 span 계산이 유지된다.

    **주어 구간을 값 탐색 범위에서 반드시 뺀다.** 빼지 않으면 주어의 임계값이 값으로
    재발행된다 — 실측: 일조 축 '높이 10미터를 초과하는 부분 : … 높이의 2분의 1' 에서
    거리값은 원문에 없는데 주어의 10미터가 거리 10 으로 나왔다. 90건 중 60건이 이
    형태였다. det 층은 OWL 추론 대상이라 날조값이 그대로 증폭된다.
    """
    if not spans:
        return text or ""
    out = list(text or "")
    for s, e in spans:
        for i in range(max(0, s), min(len(out), e)):
            out[i] = fill
    return "".join(out)


def match_values(text, value_type):
    """값타입에 맞는 값을 등장 순서대로 낸다. 없으면 빈 목록."""
    text = text or ""
    out = []
    if value_type == "비율":
        for rx in (_RATIO_PCT, _RATIO_FRAC):
            for m in rx.finditer(text):
                out.append((m.start(), _hit(to_number(m.group("value")), "퍼센트",
                                            m.group("op"), m.group("basis"), m.group(0))))
    elif value_type == "면적":
        for m in _AREA.finditer(text):
            out.append((m.start(), _hit(to_number(m.group("value")), "제곱미터",
                                        m.group("op"), None, m.group(0))))
    elif value_type == "거리":
        for m in _DIST.finditer(text):
            out.append((m.start(), _hit(to_number(m.group("value")), "미터",
                                        m.group("op"), None, m.group(0))))
    elif value_type == "배율":
        for m in _RATE_FRAC.finditer(text):
            den, num = int(m.group("den")), int(m.group("num"))
            if den == 100:          # 100분의 N 은 비율이다
                continue
            out.append((m.start(), _hit(num / den, "배", m.group("op"),
                                        None, m.group(0))))
        for m in _RATE_X.finditer(text):
            out.append((m.start(), _hit(to_number(m.group("value")), "배",
                                        m.group("op"), None, m.group(0))))
    elif value_type == "없음":
        return []
    else:
        raise ValueError(f"값타입이 계약 밖이다: {value_type!r}")
    out.sort(key=lambda t: t[0])
    return [h for _, h in out]


# ── 주어 매처 (주어타입별) ────────────────────────────────────────────────

ZONE_GROUPS = ("주거지역", "상업지역", "공업지역", "녹지지역")
RESIDUAL_KEY = "그밖의지역"
# 잔여 주어. 실측 표기 변형 8가지를 흡수한다 (2026-08-13, 건축조례 30종)
#   제1호부터 제4호까지에 해당하지 아니하는 지역 / 아니한 지역 / 않는 지역
#   제1호부터 제4호까지의 규정에 해당하지 아니하는 지역
#   제1호부터 제4호에 해당하지 아니하는 지역   (까지 없음)
#   제1호부터 제4호까지 외의 지역
#   용도지역의 지정이 없는 지역
# 변형을 하나라도 놓치면 그 호가 주어_미상 으로 빠지는데, 값은 정상이라 조용히 사라진다.
_RESIDUAL = re.compile(
    r"(?:제\s*\d+\s*호\s*(?:부터|내지)\s*제\s*\d+\s*호(?:까지)?"
    r"(?:\s*의\s*규정)?\s*(?:에|외의)?\s*"
    r"(?:해당(?:하지|되지)\s*(?:아니하[는냐]?|아니한|않는)|외)?\s*지역"
    r"|용도지역의\s*지정이\s*없는\s*지역)")
# 군 이름 앞에 세분 접두어가 붙으면 그것은 더 좁은 주어다 — '전용주거지역' 을
# '주거지역' 으로 접으면 서로 다른 규범이 한 명제로 합쳐진다. 실측: 성남시 건축
# 조례 제23조만 21종 세분을 쓰는데(전용/일반/준주거 · 중심/일반/근린/유통상업 …),
# 접두어를 무시하면 '주거지역 150제곱미터' 라는 거짓 명제가 생긴다(원문은 60).
# 세분 주어는 접두어까지 포함해 그대로 주어키로 쓴다.
_ZONE_PREFIX = r"(?:제?\s*\d*\s*종\s*)?(?:전용|일반|준|중심|근린|유통|보전|생산|자연|계획)?\s*"
_ZONE_GROUP_RE = re.compile(_ZONE_PREFIX + r"(?:" + "|".join(ZONE_GROUPS) + r")")

# 수치구간: '연면적의 합계가 1천제곱미터 이상 2천제곱미터 미만인 건축물'
#
# 하한·상한을 둘 다 선택으로 두면 정규식이 아무것도 소비하지 않고 성공한다 —
# '연면적' 만 맞고 lo·hi 가 전부 None 인 헛매치가 난다. 그래서 셋으로 나눠
# (하한+상한) · (상한만) · (하한만) 순으로 시도하고, 하나도 안 맞으면 None 을 낸다.
# 단위와 연산자 사이의 조사를 허용한다 — '10미터를 초과하는 부분'·'2천제곱미터 미만인'.
# 허용하지 않으면 일조 축의 '높이 10미터를 초과하는 부분' 이 통째로 주어_미상 이 된다.
_PARTICLE = r"(?:[를을은는이가]\s*)?"
_BOUND_LO = (r"(?P<lo>" + _NUM + r")\s*(?P<lounit>제곱미터|미터)\s*" + _PARTICLE
             + r"(?P<loop>이상|초과)")
_BOUND_HI = (r"(?P<hi>" + _NUM + r")\s*(?P<hiunit>제곱미터|미터)\s*" + _PARTICLE
             + r"(?P<hiop>미만|이하)")
# 구간대상과 수치 사이의 틈. 긴 괄호 삽입구를 하나 허용한다 —
# '연면적(동일 대지 안에 … 이하 이 조에서 같다)이 2천제곱미터 이상' 처럼
# 정의 괄호가 끼는 원문이 있다. 20자 제한만 두면 이 형태가 통째로 주어_미상 이
# 된다(실측 2026-08-13 조경 12건). 괄호 안은 콜론을 포함할 수 있으므로 별도로 센다.
# 구간대상 낱말. 계약이 선언한 대상만 후보로 둔다.
#
# **'대지면적' 을 값기준으로 쓰는 자리와 구간대상으로 쓰는 자리를 구분해야 한다.**
# '…는 대지면적의 6퍼센트 이상' 의 대지면적은 값의 기준이지 구간의 대상이 아니다.
# 구분 없이 후보로 넣으면 그 자리가 다음 구간의 시작으로 잡혀 앞의 값이 통째로
# 다음 주어에 먹힌다 — 실측 화성시 제31조 6퍼센트 1건이 그렇게 사라졌다.
# 구간대상은 뒤에 크기 표현(제곱미터)이 따라오는 자리이므로, '…의 N퍼센트' 처럼
# 비율이 바로 뒤에 오는 자리는 제외한다.
#
# **맨 '면적' 앞에 한글이 오면 다른 낱말의 꼬리다.** 앞을 막지 않으면 재시도
# 스캔(pos = m.start() + 1)이 '연면적' 안으로 들어가 '면적' 부터 다시 매칭한다 —
# 실측(2026-08-14) 조경 23건이 '연면적이 1천제곱미터 미만' 을 대지면적 구간으로
# 읽어 조례에 없는 '대지면적 1,000제곱미터 미만이면 5%' 를 그래프에 실었다.
# 값은 원문에 있고 올바른 연면적_* 명제도 따로 있어 환각 검사·소멸 검사를
# 둘 다 통과했다 — 신규를 원문 대조하지 않으면 안 걸리는 유형이다.
_TARGET = (r"(?P<target>연면적|바닥면적|높이"
           r"|(?<![가-힣])(?:대지)?면적(?!\s*의?\s*[0-9]+\s*(?:퍼센트|분의)))")
_GAP = r"(?:[^:\n(]{0,20}?)(?:\([^()]{0,200}\))?(?:[^:\n(]{0,20}?)"
_RANGE_RES = (
    re.compile(_TARGET + _GAP + _BOUND_LO + r"\s*" + _BOUND_HI),
    re.compile(_TARGET + _GAP + _BOUND_HI),
    re.compile(_TARGET + _GAP + _BOUND_LO),
)


def _match_range(text, pos=0):
    """구간을 하한+상한 → 상한만 → 하한만 순으로 시도한다. pos 이후만 본다."""
    best = None
    for rx in _RANGE_RES:
        m = rx.search(text, pos)
        if m and (best is None or m.start() < best.start()):
            best = m
        if best is not None and best.start() == pos:
            break
    return best


def iter_ranges(text, spec):
    """한 항/호에 여러 구간이 있을 때 전부 낸다. (주어키, 주어원문, extra, span).

    한 항에 구간이 둘 이상인데 첫 구간만 쓰면 뒤 값이 앞 값을 덮는다 — 이건 중복이
    아니라 **값 손실**이다. 실측: 화성시 건축 조례 제31조가 한 항에
    '5천~3만 → 6퍼센트, 3만 이상 → 8퍼센트' 를 나란히 쓰는데 구간이 키에 하나만
    들어가 8퍼센트가 6퍼센트를 덮었다.
    """
    out, pos = [], 0
    while True:
        m = _match_range(text, pos)
        if not m:
            return out
        seg = text[m.start():m.end()]
        got = match_subject(seg, spec.get("주어타입") or "수치구간", spec)
        if got:
            out.append((got[0], seg, got[2], (m.start(), m.end())))
            pos = max(m.end(), m.start() + 1)
        else:
            # 이 구간은 다른 구간대상의 것이다. 구간 전체를 건너뛰면 그 안에
            # 겹쳐 있는 **선언 대상의 구간까지 잃는다** — 실측 화성시 제31조에서
            # 대지면적 단위가 5천~3만 구간을 먼저 집어 연면적 6퍼센트가 사라졌다.
            # 대상 낱말 바로 뒤부터 다시 찾는다.
            pos = m.start() + 1


def match_subject(text, subject_type, spec):
    """주어를 잡는다. (주어키, 주어원문, 보조필드) 또는 None."""
    text = (text or "").strip()
    if subject_type == "용도지역군":
        if _RESIDUAL.search(text):
            return (RESIDUAL_KEY, text, {})
        m = _ZONE_GROUP_RE.search(text)
        if not m:
            return None
        key = re.sub(r"\s+", "", m.group(0))
        return (key, text, {"세분주어": "true"} if key not in ZONE_GROUPS else {})
    if subject_type in ("수치구간", "관계구간"):
        target = spec.get("구간대상")
        m = _match_range(text)
        if not m:
            return None
        g = m.groupdict()
        # 원문이 실제로 맞춘 구간대상과 계약이 선언한 구간대상이 다르면 그 구간은
        # 이 단위의 주어가 아니다. 덮어쓰면 다른 대상의 구간이 선언 대상으로
        # 둔갑한다 — 실측 의정부 제29조③2 '높이가 2미터 이상 … 100분의 50'(조경면적
        # 산정방법)이 `연면적_2_이상 값 50` 이 됐다. 원문에 없는 주어를 지어낸 것이다.
        # 면적 단위를 기대하는 구간대상에 길이 경계(미터)가 잡히면 그 구간은 이
        # 단위의 주어가 아니다. 대상 낱말만으로 거르면(높이 != 연면적) 공개공지의
        # '바닥면적' 처럼 정당한 대체 낱말까지 죽는다 — 실측 73건이 사라졌다.
        # 경계의 **단위**로 거르면 그 손실 없이 오귀속만 걸린다.
        if target in ("연면적", "바닥면적"):
            units = {g.get("lounit"), g.get("hiunit")} - {None}
            if units and units <= {"미터"}:
                return None
        lo, hi = to_number(g.get("lo")), to_number(g.get("hi"))
        # **원문이 실제로 맞춘 낱말을 쓴다.** 계약 선언값으로 덮으면 같은 조문에
        # 연면적 구간과 대지면적 구간이 함께 있을 때 서로의 이름을 뒤집어쓴다 —
        # 실측 30건이 그렇게 어긋났다(평택 제29조 '연면적의 합계가 1천500' 이
        # 대지면적_1500_2000 으로, 서울 제24조 '면적 200' 이 연면적_200_300 으로).
        # 맨 '면적' 은 대지면적의 준말이라 선언값이 대지면적일 때만 그것으로 읽는다.
        matched = g.get("target")
        if matched == "면적":
            # 맨 '면적' 은 대지면적의 준말이다. 두 단위가 같은 조문을 훑으므로
            # 대지면적 단위에만 준다 — 연면적 단위가 가져가면 이름이 뒤집힌다.
            matched = "대지면적"
        key_target = matched or target
        # 수치구간은 선언 대상과 맞춘 낱말이 같아야 한다. 다르면 그 구간은 이
        # 추출단위의 주어가 아니다. 관계구간은 관계 서술이 주어라 이 대조를
        # 적용하지 않는다 — 적용하면 일조·공개공지가 통째로 죽는다(실측 72건).
        if subject_type == "수치구간" and target and key_target != target:
            return None
        key = "%s_%s_%s" % (key_target, fmt_number(lo) if lo is not None else "0",
                            fmt_number(hi) if hi is not None else "이상")
        extra = {"구간하한": fmt_number(lo), "구간상한": fmt_number(hi),
                 "구간대상": key_target}
        if subject_type == "관계구간" and spec.get("관계"):
            key = "%s_%s" % (_slug_relation(spec["관계"]), key)
            extra["관계"] = spec["관계"]
        return (key, text, extra)
    if subject_type == "관계":
        # 구간이 없는 관계 주어. 인동간격 규정이 그렇다 —
        # '채광을 위한 창문 등이 있는 벽면으로부터 직각방향으로 … 높이의 1배 이상'.
        # 구간을 요구하면(관계구간) 값을 파싱해 놓고 통째로 버린다.
        rel = spec.get("관계") or "관계"
        return (_slug_relation(rel), text, {"관계": rel})
    if subject_type == "용도목록":
        return None            # 값이 없는 주어다. 호출부가 값없음_목록형으로 다룬다
    if subject_type == "용도지역열거":
        raise ValueError("용도지역열거는 kb-norm 이 정본이다 — 이 엔진의 범위가 아니다")
    raise ValueError(f"주어타입이 계약 밖이다: {subject_type!r}")


def _slug_relation(s):
    return re.sub(r"\s+", "", s)


# ── 조건 가르기 ───────────────────────────────────────────────────────────

# 괄호 조건. 소괄호만 보면 대괄호로 예외를 묶은 조례를 놓친다 —
# 평택 제37조④ '0.8배[도시형 생활주택 0.6배, 제로에너지 0.7배] 이상'.
# 대괄호 안에 소괄호가 중첩되므로(30세대 이상 공동주택 제외) 중첩을 허용한다.
_PAREN = re.compile(r"\((?P<inner>[^()]{0,200})\)"
                    r"|\[(?P<inner2>(?:[^\[\]]|\([^()]{0,80}\)){0,300})\]")
# 단서. '다만 …' 과 '…에서는 … 완화할 수 있다' 형 재량 완화를 둘 다 잡는다.
# 완화 조항의 값을 기본값과 같은 자리에 두면 서로 충돌해 둘 다 격리된다 —
# 실측 평택 제37조④ 의 0.7배 완화값이 기본 0.8배와 부딪혔다.
_PROVISO = re.compile(r"다만[,\s].{0,200}"
                      r"|[^,.]{0,60}(?:구역|지역)에서는[^.]{0,200}?완화할\s*수\s*있다")


def split_conditions(item_text, value_type):
    """호 본문을 (기본구간, 조건구간 목록)으로 가른다.

    괄호와 '다만' 단서 안의 값은 기본값이 아니다. 섞으면 예외값이 기본값을 덮는다 —
    kb-norm 실측 98건/21개 조례의 함정이며 조경 축에서도 '(다만, 중심상업지역은
    N퍼센트 이상)' 표기로 실측 2건 나온다.
    """
    text = item_text or ""
    spans = []
    for m in _PAREN.finditer(text):
        inner = m.group("inner") or m.group("inner2") or ""
        if match_values(inner, value_type):
            spans.append((m.start(), m.end(), inner.strip()))
    for m in _PROVISO.finditer(text):
        if match_values(m.group(0), value_type):
            spans.append((m.start(), m.end(), m.group(0).strip()))
    spans.sort()
    merged, base = [], []
    last = 0
    for s, e, inner in spans:
        if s < last:
            continue
        base.append(text[last:s])
        merged.append(inner)
        last = e
    base.append(text[last:])
    return "".join(base), merged
