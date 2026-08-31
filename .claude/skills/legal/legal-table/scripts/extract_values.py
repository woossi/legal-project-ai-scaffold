#!/usr/bin/env python3
"""건폐율·용적률 값 추출 — `norm_values.json` 과 격리 리포트를 만든다.

md 에 살아남은 값만 뽑는다. **없는 값을 만들지 않는다** — 표가 이미 변환 단계에서
소실됐으므로(`_table_loss.json` 참조) 이 산출물의 커버리지는 md 관측 상한이다.

하지 않는 것 (spec §하지 않는 것)
  - 다중값 줄의 열 정렬 복원. 헤더와 값의 열수가 실제로 어긋난다
  - 비교표현 기본값 주입. 근거 없는 `이하` 를 만든 선례가 있다
  - 용지 → 용도지역 환원. 매핑이 시행지침 안에 있는지 확인되지 않았다

입력  output/legal/markdown/{서울,인천,경기}/*.md (189건)
출력  output/legal/table/norm_values.json
      output/legal/table/_norm_value_report.json
전제  table_common 의 줄번호 규약 — 모든 line 은 md 파일 물리 줄번호(1-based)
"""

import argparse
import collections
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import table_common as tc  # noqa: E402
import value_relation as vrel  # noqa: E402

VALUES_PATH = "output/legal/table/norm_values.json"
REPORT_PATH = "output/legal/table/_norm_value_report.json"

# ── 값 표기 ─────────────────────────────────────────────────────────────────
# 실측 1,910건: `%` 1,889 · `퍼센트` 16 · `100분의` 5
VALUE_RE = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>%|퍼센트|프로)"
    r"|100\s*분\s*의\s*(?P<num2>\d+(?:\.\d+)?)"
)
NOTATION = {"%": "퍼센트기호", "퍼센트": "퍼센트한글", "프로": "퍼센트한글"}

# 지표 표기. `용적율` 오기를 보존한다
METRIC_RE = re.compile(r"건폐율|용적률|용적율")
METRIC_CANON = {"건폐율": "건폐율", "용적률": "용적률", "용적율": "용적률"}

# ── 비교표현 ────────────────────────────────────────────────────────────────
# spec §비교표현: 값 표기 직후 인접 구간(같은 줄, 값 끝에서 공백 포함 6자 이내)
# 에서만 읽는다. 못 읽으면 null 이고 그 레코드는 격리한다. **기본값을 넣지 않는다.**
COMPARATOR_RE = re.compile(r"^\s{0,2}(이하|이상|미만|초과|이내|까지)")
COMPARATOR_WINDOW = 6

# ── 컨텍스트 분류 ───────────────────────────────────────────────────────────
# 값의 피수식어로 가른다. `%` 앞에 무엇이 붙어 있는지가 판정 근거다 —
# 줄 전체에 키워드가 있는지로 보면 한 줄에 여러 지표가 섞인 줄에서 전부 오분류된다.
# 값 줄 자체가 예시임을 밝힌 표기. 도해 참조(`<그림Ⅱ-4-1>`)는 여기에 넣지
# 않는다 — 문장 속 인용은 그 줄이 예시라는 뜻이 아니다. 실측에서 `<그림` 만으로
# 걸린 2건(고양삼송 879줄)은 `건폐율70%이하, 용적률900%이하` 라는 실제 규범이
# 뒤쪽의 `<그림Ⅱ-4-1>과 같이 표시한다` 때문에 예시로 뒤집힌 것이었다.
# 도해 안의 값은 줄 단위가 아니라 캡션 구간(`tc.example_zones`)으로 잡는다.
EXAMPLE_RE = re.compile(r"예\s*\)|예시|도면\s*표시|도면표시")
# 상위법 총량(법 제52조제3항). `150% 및 200%를 각각 넘을 수 없다` 형태
RELAX_TOTAL_RE = re.compile(
    r"넘을\s*수\s*없|초과할\s*수\s*없|완화하여\s*적용|이내에서\s*이를\s*완화"
    r"|곱한\s*비율|완화할\s*수\s*있|완화의\s*범위|범위\s*내에서\s*완화"
    r"|범위\s*이내\s*완화")
# 산정식. `=` 만으로 보면 안 된다 — 표가 줄로 풀린 문서에서 `용적률 최고층수 =
# 350%이하` 처럼 `=` 가 열 구분자로 남아 있어 규범 61건이 산정식으로 뒤집힌다
# (전수 확인: `=` 로 걸린 63건 중 실제 산정식 0건). 산술 연산자가 값 주위에
# 실재할 때만 산정식으로 본다.
FORMULA_RE = re.compile(r"÷\s*(?:대지면적|연면적|대지\s*면적|\()")
# ── 인용서술 판정표 ─────────────────────────────────────────────────────────
# **상위법령·조례를 인용한 줄은 대부분 이 지구의 규범이다.** 인용은 근거 제시이지
# 값의 자격을 없애지 않는다. 정규식으로 인용 어휘를 걸면 규범을 통째로 삼킨다.
#
# 실측(규범 1,223 전건 원문 대조): 인용 표기(「」·법 제N조·조례)가 있는 값은 63건
# 이고, 그중 **62건이 이 지구의 규범**이다.
#
#   적용형 55  `「과천시 도시계획 조례」에 의거 당해 토지 건폐율 ∙ 70% 이하`
#              `「서울시 도시계획조례」제55조 제4항에 따라 용적률 250% 이하로
#               계획하여야 한다`  → 조례를 근거로 든 이 지구의 규범이다
#   서술어없음 7  용지별 규범표가 줄로 풀려 인용이 허용용도 셀에, 값이 건폐율
#              셀에 각각 남은 형태(인천가정 735·752·797줄 등). 전건 규범이다
#   서술형   1  아래 판정표의 유일한 건
#
# 가르는 것은 어휘가 아니라 **주어와 서술어**다. 주어가 이 지구(`본 계획의`·
# 생략된 해당 용지)이고 서술어가 `적용·의거·계획하여야 한다` 면 규범이고,
# 주어가 인용 법령이고 서술어가 `규정하고 있음` 이면 그 법령의 내용 서술이다.
#
# 표면 패턴으로 가를 수 없으므로 (파일명·줄·offset) 판정표로 고정한다 —
# value_relation.ISSUE 와 같은 이유다. 원문이 바뀌면 키가 안 맞아 조용히
# 재분류되지 않고 미해결로 드러난다. 정규식이었다면 새 오탐을 자동 발급했을 것이다.
#
# 값: (context_class, 판정 근거 서술)
CITATION_DESCR = {
    # 공도 승두3 L242 — `「안성시 도시계획조례」상 제2종일반주거지역의 용적률은
    # 250%이하로 규정하고 있음`. 주어가 조례이고 서술어가 `규정하고 있음` 이다.
    # 위치도 규범 조문이 아니라 `4) 개발밀도에 의한 인구추정 가) 법적 규제사항`
    # 이라는 계획 근거 서술 단락이며, 이 지구의 규범은 바로 다음 줄 L244
    # `법적 규제사항에 의거 용적률 250% 이하의 범위에서 … 개발밀도 설정` 이다.
    # **값 자체는 같은 250 이라 결론이 바뀌지 않는다** — 자격만 다르다.
    ("공도 승두3지구 도시개발구역.md", 242, 34): (
        "인용서술",
        "주어가 인용 조례이고 서술어가 `규정하고 있음` 이다. 이 지구의 규범이 "
        "아니라 인용 법령의 내용 서술이며, 위치도 `법적 규제사항` 근거 단락이다"),

    # 공도 용두2 L691 — `예를 들어, 법상 건폐율의 최대한도가 60%이고 도시ㆍ군계획
    # 조례는 50%로 정하고 있는 경우 …`. 60·50 은 완화 규정을 설명하는 **가정
    # 수치**이지 이 지구의 건폐율이 아니다. 줄 자체가 지구단위계획수립지침 훈령
    # 본문을 그대로 옮긴 것이며(같은 문단이 `3-2-2-5.`·`3-2-3.`·`3-2-5.` 로
    # 이어진다), 주어도 `법상`·`도시ㆍ군계획조례` 로 이 지구가 아니다.
    #
    # 같은 줄 세 번째 값(60, offset 208 — `건폐율을 60%까지 완화할 수 있다`)은
    # 이미 `완화총량` 으로 분류돼 있어 판정표에 넣지 않는다. **한 줄 안에서도
    # 값마다 자격이 다르다** — 줄 단위로 뒤집지 않는 이유다.
    ("공도 용두2지구 도시개발구역.md", 691, 148): (
        "인용서술",
        "주어가 `법상`(상위법)이고 `예를 들어 … 경우` 라는 가정 서술이다. "
        "줄 전체가 지구단위계획수립지침 훈령 본문(3-2-4.)의 전재이며 "
        "이 지구의 건폐율이 아니라 완화 규정을 설명하는 예시 수치다"),
    ("공도 용두2지구 도시개발구역.md", 691, 164): (
        "인용서술",
        "주어가 `도시ㆍ군계획조례` 이고 서술어가 `정하고 있는 경우` 다. "
        "위와 같은 줄의 가정 수치이며 이 지구의 건폐율이 아니다"),
}

# 값 바로 앞에 붙은 다른 지표. 이 어휘가 값의 피수식어면 우리 지표가 아니다.
OTHER_METRIC = re.compile(
    r"(생태면적률|공공기여율|재사용|사용수량|연면적|에너지|소비총량|세대수|"
    r"주택유형|호수밀도|녹지율|주차장?\s*설치율|경사도|보급률|비율|점유율|"
    r"인센티브|가중치|할증|분담률|부담률)[^%]{0,12}$")

# ── 주어 ────────────────────────────────────────────────────────────────────
# spec §주어 판정: 근거가 강한 것만 채택하고, 아무것도 안 걸리면 null 로 둔다.
# 용지·블록·용도지역 어휘는 실측 표기에서 추린다. **용지 → 용도지역 매핑은
# 만들지 않는다** — 원문 주어를 보존하고 subject_type 으로 분류만 한다.
ZONE_RE = re.compile(
    r"(?:제[1-3１-３일이삼]종(?:일반|전용)?주거지역|준주거지역|중심상업지역|"
    r"일반상업지역|근린상업지역|유통상업지역|전용공업지역|일반공업지역|"
    r"준공업지역|보전녹지지역|생산녹지지역|자연녹지지역|계획관리지역|"
    r"생산관리지역|보전관리지역|농림지역|자연환경보전지역|주거지역|상업지역|"
    r"공업지역|녹지지역)")
# 용지. 편·장·절 접두(`제2장 `)를 주어에 넣지 않는다 — 표목에서 뽑을 때 그대로
# 딸려 들어오면 `제2장 공동주택용지` 가 주어가 된다. 한정어가 최소 2자 있어야
# 하며(맨몸 `용지` 는 주어가 아니다), 그 한정어에 숫자·장절 표기는 넣지 않는다.
LOT_RE = re.compile(r"(?:[가-힣]{2,10}(?:\s*및\s*[가-힣]{2,10})?)\s*용지")
# 블록·획지. 맨몸 `J1` 같은 표기는 표 셀 조각일 수 있어 블록 표기가 명시된
# 형태만 받는다 — `A-1블록`·`제3획지`. 근거가 약한 것은 붙이지 않는다.
BLOCK_RE = re.compile(
    r"(?:[A-Za-z]{1,3}[-‐–]?\d{1,3}(?:[-‐–]\d{1,3})?\s*(?:블록|BL|블럭)"
    r"|제?\s*\d{1,3}\s*(?:블록|획지|가구))")

# 산문 주어. `…은/는 … 이하로 한다` 구조
PROSE_SUBJ_RE = re.compile(
    r"^\s*(?:[-–•∙※\s]*(?:[①-⑳]|\d+[).]|[가-힣][).])\s*)?"
    r"(?P<subj>[^,.]{2,40}?)\s*(?:의|은|는|을|를)\s")
SENTENCE_END = re.compile(r"(?:한다|된다|적용한다|계획한다|따른다|본다)\s*[.。]?\s*$")

# 표 캡션 탐색 범위 (spec 실측: 12줄 이내)
CAPTION_WINDOW = 12


def _metric_at(line, pos):
    """값 위치에서 왼쪽으로 가장 가까운 지표 표기. (표기, 시작offset) 또는 None.

    같은 줄에 지표가 여러 번 나오면 값에 가장 가까운 것이 그 값의 지표다.
    """
    best = None
    for m in METRIC_RE.finditer(line):
        if m.start() < pos:
            best = m
    return (best.group(0), best.start()) if best else None


def _classify_context(line, pos, end, zone=None, key=None):
    """`context_class` 판정. spec §컨텍스트 분류의 값 도메인을 그대로 쓴다.

    판정은 **값의 피수식어**로 한다. 줄 전체에 어떤 낱말이 있는지로 보면 한 줄에
    여러 지표가 섞인 줄에서 전부 오분류된다(실측: `완화` 를 줄 단위로 보면
    중수도 재사용률·증축 연면적이 상위법 총량으로 올라온다).

    `zone` 은 이 값이 든 예시 도해 구간(`tc.example_zones` 산출) 또는 None.
    `key` 는 `(파일명, line, offset)` — 인용서술 판정표 조회용이다.
    """
    before = line[:pos]
    around = line[max(0, pos - 30):end + 30]
    # 인용서술은 판정표로만 잡는다. 인용 어휘(「」·법 제N조·조례)를 정규식으로
    # 걸면 규범 62건을 함께 삼킨다 — 인용은 근거 제시이지 자격 박탈이 아니다.
    if key is not None and key in CITATION_DESCR:
        return CITATION_DESCR[key]
    # 예시 도해는 두 경로로 잡는다.
    #
    # (1) 값 줄 자체에 예시 표기가 있는 경우.
    # (2) 값이 **예시 캡션 구간 안**에 있는 경우. spec 은 "앞 6줄" 을 조건으로
    #     적었으나 그대로 넣으면 584건이 걸리고 그중 572건이 실제 규범이다
    #     (과천갈현 471줄·고양창릉 5199줄). 줄수 창이 아니라 캡션이 여는
    #     구간으로 잡아야 한다 — 구간은 heading·다음 캡션·공백 2줄에서 끊는다.
    #
    # 캡션 없는 값을 위치만으로 예시로 몰지 않는다. 구간의 근거는 언제나
    # 실재하는 캡션 한 줄이고, `context_basis` 에 그 캡션을 그대로 남긴다.
    if EXAMPLE_RE.search(line):
        return "예시도면표시", "값 줄에 예시·도면표시 표기"
    if zone:
        return "예시도면표시", (
            f"예시 캡션 구간({zone['line']}~{zone['end']}줄) 안의 값 — "
            f"캡션: {zone['caption'][:80]}")
    if FORMULA_RE.search(line):
        return "산정식", "같은 줄에 산정식 기호(÷·×·= 와 괄호)"
    if OTHER_METRIC.search(before):
        return "타지표", f"값의 피수식어가 우리 지표가 아니다: {before[-24:].strip()!r}"
    if RELAX_TOTAL_RE.search(around):
        return "완화총량", "값 인접 구간에 완화·총량 서술(상위법 제52조제3항 계열)"
    return "규범", "제외 조건에 걸리지 않음"


def _subject(line, pos, row_value_count, caption, section):
    """주어를 해소한다. 근거가 강한 것만 채택하고 아니면 null.

    **`row_value_count >= 2` 이면 주어를 붙이지 않는다.** 이건 판정 실패가 아니라
    계약이다(spec §주어 판정). 다중값 줄은 헤더와 값의 열수가 실제로 어긋나
    열 정렬 복원이 불가능하다. 맞추면 조작이 된다.
    """
    if row_value_count >= 2:
        return None, None, None, None, "다중값행_열정렬불가"

    before = line[:pos]

    # 1) 동일줄_인접명시 — 값 앞 40자 안의 주어 표기
    win = before[-40:]
    for rx, typ in ((ZONE_RE, "용도지역"), (LOT_RE, "용지"), (BLOCK_RE, "블록·획지")):
        ms = list(rx.finditer(win))
        if ms:
            surf = ms[-1].group(0).strip()
            if surf:
                return surf, surf, typ, "동일줄_인접명시", None

    # 2) 산문문장주어 — 종결형 문장이고 `…은/는 … 이하로 한다` 구조.
    #    문장 머리 조각을 그대로 주어로 쓰지 않는다. 그렇게 하면 `건폐율`·
    #    `10층 이상`·`※ 용적률 10% 이상 설치` 같은 비주어가 주어가 된다
    #    (전수 확인에서 30건 중 다수가 그랬다). 용지·용도지역·블록으로 식별되는
    #    것만 받는다 — 근거가 강한 것만 채택한다는 spec §주어 판정 그대로다.
    if SENTENCE_END.search(line):
        pm = PROSE_SUBJ_RE.match(line)
        if pm:
            surf = pm.group("subj").strip()
            if 2 <= len(surf) <= 40 and not METRIC_RE.search(surf):
                for rx, typ in ((ZONE_RE, "용도지역"), (LOT_RE, "용지"),
                                (BLOCK_RE, "블록·획지")):
                    m = rx.search(surf)
                    if m:
                        return m.group(0).strip(), surf, typ, "산문문장주어", None

    # 3) 단일값_표캡션
    if caption:
        for rx, typ in ((ZONE_RE, "용도지역"), (LOT_RE, "용지")):
            m = rx.search(caption["text"])
            if m:
                return m.group(0).strip(), caption["text"], typ, "단일값_표캡션", None

    # 4) 단일값_장절
    if section:
        for rx, typ in ((ZONE_RE, "용도지역"), (LOT_RE, "용지")):
            m = rx.search(section["heading"])
            if m:
                return (m.group(0).strip(), section["heading"], typ,
                        "단일값_장절", None)

    return None, None, None, None, "근거강한주어없음"


def _subject_type(surface):
    if ZONE_RE.search(surface):
        return "용도지역"
    if LOT_RE.search(surface):
        return "용지"
    if BLOCK_RE.search(surface):
        return "블록·획지"
    return "그밖"


def _caption_near(doc, line_no):
    """값 줄 위쪽 CAPTION_WINDOW 줄 안의 표 캡션. 1-based line."""
    lines = doc["lines"]
    for i in range(line_no - 2, max(doc["body_start"] - 1, line_no - 2 - CAPTION_WINDOW), -1):
        if i < 0:
            break
        raw = lines[i]
        m = tc.TABLE_REF.search(raw)
        if m and tc._is_caption(raw, m.start()):
            return {"text": raw.strip(), "line": i + 1, "distance": line_no - (i + 1)}
    return None


def build(root="."):
    values, quarantine = [], []
    seq = 0
    # 처리 시작 때 목록을 고정한다 — 메타에 적는 문서 수는 이 목록의 길이여야
    # 실제 처리분과 어긋나지 않는다.
    paths = tc.md_files(root)
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            doc = tc.parse_document(fh.read())
        lines = doc["lines"]
        rel = tc.rel_path(path, root)
        dstrc = doc["지구번호"]
        ocr = any("ocr" in m.lower() for m in doc["extraction_methods"])
        method = doc["extraction_methods"][0] if doc["extraction_methods"] else None
        # 예시 도해 구간. 줄번호 → 구간 으로 펼쳐 O(1) 조회한다.
        zone_by_line = {}
        for z in tc.example_zones(doc):
            for ln in range(z["start"], z["end"] + 1):
                zone_by_line.setdefault(ln, z)

        for i in range(doc["body_start"], len(lines)):
            raw = lines[i]
            if tc.HTML_COMMENT.match(raw) or not METRIC_RE.search(raw):
                continue
            hits = list(VALUE_RE.finditer(raw))
            if not hits:
                continue
            line_no = i + 1
            row_value_count = len(hits)
            zone = zone_by_line.get(line_no)
            caption = _caption_near(doc, line_no)
            art, art_why = tc.article_at(doc, line_no)
            sec = tc.section_at(doc, line_no)

            for ordinal, m in enumerate(hits, 1):
                seq += 1
                vid = f"V{seq:06d}"
                surface = m.group(0)
                num = m.group("num") or m.group("num2")
                unit_surface = m.group("unit")
                notation = (NOTATION[unit_surface] if unit_surface else "100분의")
                metric = _metric_at(raw, m.start())

                tail = raw[m.end():m.end() + COMPARATOR_WINDOW]
                cm = COMPARATOR_RE.match(tail)
                ctx, ctx_basis = _classify_context(
                    raw, m.start(), m.end(), zone,
                    (os.path.basename(path), line_no, m.start()))
                subj, subj_surf, subj_type, subj_basis, subj_reason = _subject(
                    raw, m.start(), row_value_count, caption, sec)

                rec = {
                    "value_id": vid,
                    # 원문 — 그대로 복원 가능해야 한다
                    "line_text": raw,
                    "surface": surface,
                    "surface_offset": [m.start(), m.end()],
                    "quote": raw.strip(),
                    # 파생 — 미검증값. 원문과 필드를 나눈다
                    "metric": METRIC_CANON.get(metric[0]) if metric else None,
                    "metric_surface": metric[0] if metric else None,
                    "value": float(num.replace(",", "")),
                    "unit": "percent",
                    "notation": notation,
                    "comparator": cm.group(1) if cm else None,
                    "comparator_basis": "값직후인접" if cm else None,
                    "range": None,
                    # 출처 — 전 필드 필수
                    "dstrcAppnNo": dstrc,
                    "lc5": dstrc[:5] if dstrc else None,
                    "지역": doc["지역"],
                    "source_file": rel,
                    "line": line_no,
                    "article": ({"no": art["조번호"], "label": art["표제"],
                                 "line": art["line"], "origin": "조문헤딩"}
                                if art else None),
                    "section": ({"heading": sec["heading"], "line": sec["line"]}
                                if sec else None),
                    "table_caption": caption,
                    # 예시 도해 구간 — 이 값이 도해 안에 있으면 근거 캡션을
                    # 그대로 남긴다. 판정을 원문으로 되짚을 수 있어야 한다
                    "example_zone": ({"kind": zone["kind"],
                                      "caption": zone["caption"],
                                      "caption_line": zone["line"],
                                      "range": [zone["start"], zone["end"]]}
                                     if zone else None),
                    # 주어 — 해소한 것만 채운다
                    "subject": subj,
                    "subject_surface": subj_surf,
                    "subject_type": subj_type,
                    "subject_basis": subj_basis,
                    "subject_reason": subj_reason,
                    # 신뢰
                    "row_shape": "다중값" if row_value_count >= 2 else "단일값",
                    "value_ordinal": ordinal,
                    "row_value_count": row_value_count,
                    "context_class": ctx,
                    "context_basis": ctx_basis,
                    "extraction_method": method,
                    "ocr_risk": ocr,
                }
                if art is None:
                    rec["article_reason"] = art_why

                # 격리 — 판정할 수 없는 것. 규범이 아닌 것의 자리가 아니다.
                if metric is None:
                    quarantine.append(_q(rec, "지표미상",
                                         "값 왼쪽에 건폐율·용적률 표기가 없다"))
                elif rec["metric"] == "건폐율" and rec["value"] > 100:
                    # 건폐율은 100%를 넘을 수 없다(대지면적 대비 건축면적 비율).
                    # 넘는다면 지표 귀속이 틀린 것이다 — 표가 줄로 풀린 문서에서
                    # `건폐율 … 70% 이하 560% 이하` 처럼 용적률 값이 건폐율 라벨
                    # 오른쪽에 붙는다. 열 정렬을 복원해 옳은 지표를 **추정하지
                    # 않는다**(spec §하지 않는 것). 값은 실재하므로 버리지 않고
                    # 격리해 사유와 함께 남긴다.
                    quarantine.append(_q(
                        rec, "지표귀속불가",
                        f"건폐율로 귀속됐으나 {rec['value']:g}% 로 100%를 넘는다. "
                        "열이 깨진 줄에서 다른 지표의 값이 붙은 것으로 보이며, "
                        "옳은 지표를 추정하지 않는다"))
                elif cm is None:
                    quarantine.append(_q(
                        rec, "비교표현없음",
                        f"값 직후 {COMPARATOR_WINDOW}자 이내에 비교표현이 없다: "
                        f"{tail!r}. 기본값을 넣지 않는다"))
                else:
                    values.append(rec)

    # 관계 필드는 격리 전건이 모인 뒤에 붙인다 — 짝 value_id 를 해소하려면
    # 같은 줄의 다른 격리 레코드가 이미 있어야 한다. 발급은 원문 어휘가 명시된
    # 것만이며 판정표는 value_relation.py 가 정본이다.
    vrel.apply(quarantine, values)
    return values, quarantine, len(paths)


def _q(rec, reason_class, reason):
    out = dict(rec)
    out["quarantine_class"] = reason_class
    out["quarantine_reason"] = reason
    return out


def _dist(recs, key):
    """분포. 키를 문자열로 고정한다 — None 이 섞이면 정렬이 깨지고, 무엇보다
    `null` 이 몇 건인지가 분포에서 보여야 한다."""
    c = collections.Counter(
        "(null)" if r[key] is None else str(r[key]) for r in recs)
    return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out-values", default=None)
    ap.add_argument("--out-report", default=None)
    args = ap.parse_args()
    root = args.root

    values, quarantine, n_docs = build(root)
    total = len(values) + len(quarantine)
    norm = [v for v in values if v["context_class"] == "규범"]
    docs_all = {v["dstrcAppnNo"] for v in values} | {q["dstrcAppnNo"] for q in quarantine}

    meta_common = {
        "생성기": "legal-table/scripts/extract_values.py",
        "입력": "output/legal/markdown/{서울,인천,경기}/*.md",
        "줄번호규약": "모든 line 은 md 파일 물리 줄번호(1-based)다",
        "관측_전건": total,
        "확정": len(values),
        "격리": len(quarantine),
        "모수_규약": [
            "관측 전건 = 확정 + 격리. 두 파일을 합치면 value_id 가 V000001 부터 "
            "빈 번호 없이 이어진다",
            "커버리지 분모는 필드마다 다르다 — 값 보유 문서 93 은 md 회수 상한, "
            "전체 문서는 이 메타의 전체문서수(실제 순회한 입력 수)다. 한 분모로 "
            "섞지 않는다",
            "이 산출물의 커버리지와 조례 대조 커버리지 45 를 한 분모로 섞지 "
            "않는다. 섞으면 검증 커버리지가 두 배 넘게 부풀려진다",
            "context_class != 규범 은 확정 집계에서 뺀다. 격리하지 않고 플래그로 "
            "남긴다 — 격리는 판정할 수 없는 것의 자리이지 규범이 아닌 것의 "
            "자리가 아니다",
        ],
    }

    values_doc = {
        "meta": dict(meta_common, **{
            "레코드수": len(values),
            "규범값수": len(norm),
            "값보유문서수": len({v["dstrcAppnNo"] for v in values}),
            # 실제로 순회한 입력 목록의 길이다 — 관측값이지 계약 기대값이 아니다.
            # 하류(build_value_index.py)가 이 값을 계승한다.
            "전체문서수": n_docs,
            "context_class_분포": _dist(values, "context_class"),
            "subject_basis_분포": _dist(values, "subject_basis"),
            "subject_type_분포": _dist(values, "subject_type"),
            "metric_분포": _dist(values, "metric"),
            "comparator_분포": _dist(values, "comparator"),
            "row_shape_분포": _dist(values, "row_shape"),
            "주어해소": {
                "해소": sum(1 for v in values if v["subject"]),
                "미해소": sum(1 for v in values if not v["subject"]),
                "미해소_다중값행": sum(
                    1 for v in values
                    if v["subject_reason"] == "다중값행_열정렬불가"),
            },
            "한계": [
                "다중값 줄에는 주어를 붙이지 않는다. 판정 실패가 아니라 계약이다 "
                "— 헤더와 값의 열수가 실제로 어긋나 복원하면 조작이 된다",
                "용지 → 용도지역 매핑을 만들지 않는다. 원문 주어를 보존하고 "
                "subject_type 으로 분류만 한다",
            ],
        }),
        "records": values,
    }
    report_doc = {
        "meta": dict(meta_common, **{
            "레코드수": len(quarantine),
            "quarantine_class_분포": _dist(quarantine, "quarantine_class"),
            "관계필드": vrel.summary(quarantine),
            "설명": "판정할 수 없어 norm_values.json 에 넣지 못한 것과 사유. "
                    "결손이 아니라 기록이다. **하위 축은 이 파일을 반드시 함께 "
                    "읽는다** — 관계 필드가 붙은 값은 확정값과 함께 해석해야 "
                    "하며, 확정만으로 분포를 내면 모수 왜곡이다",
        }),
        "records": quarantine,
    }

    for doc, path, arg in ((values_doc, VALUES_PATH, args.out_values),
                           (report_doc, REPORT_PATH, args.out_report)):
        out = arg or os.path.join(root, path)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        body = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(body)
        print(f"{out}  {len(doc['records'])}건  "
              f"sha256 {hashlib.sha256(body.encode()).hexdigest()[:16]}…")

    print(f"  관측 전건 {total} = 확정 {len(values)} + 격리 {len(quarantine)}")
    print(f"  규범 {len(norm)} · 값 보유 문서 "
          f"{len({v['dstrcAppnNo'] for v in values})} / {n_docs} "
          f"(관측 문서 {len(docs_all)})")


if __name__ == "__main__":
    main()
