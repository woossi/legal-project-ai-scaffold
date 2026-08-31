#!/usr/bin/env python3
"""주어 미상 레코드의 원인 분해와 회수 견적 — `_subject_gap.json` 을 만든다.

`norm_values.json` 의 `subject_type == null` 전건을 원인 유형으로 가르고, 탐색 범위를
넓힌 **시험 규칙**을 실제로 돌려 회수 가능 건수를 실측한다.

**이 산출물은 견적이다.** 시험 규칙의 판정을 `norm_values.json` 에 반영하지 않는다.
넓힌 규칙이 옳다는 결론이 아니라 "넓히면 몇 건이 잡히는가" 의 관측값이다.

모수 규약 (가장 중요)
  미상 1,358 = 다중값행 1,037 + 단일값 321
  **회수 견적의 모수는 321 이다.** 다중값 1,037 은 spec §주어 판정이 "주어를 붙이지
  않는다" 로 못박은 계약이며 판정 실패가 아니다. 1,358 을 후보로 세면 4배 부풀려진다.

하지 않는 것 (spec §하지 않는 것)
  - 용지 → 용도지역 매핑 발급. 관측 기록으로만 남긴다
  - 원문에 없는 주어를 만들어 붙이기
  - 다중값행 열 정렬 복원

입력  output/legal/table/norm_values.json
      output/legal/markdown/{서울,인천,경기}/*.md (원문 대조)
출력  output/legal/table/_subject_gap.json
      output/legal/table/_subject_gap.md
전제  table_common 의 줄번호 규약 — 모든 line 은 md 파일 물리 줄번호(1-based)
"""

import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import table_common as tc  # noqa: E402
import extract_values as ev  # noqa: E402

VALUES_PATH = "output/legal/table/norm_values.json"
GAP_PATH = "output/legal/table/_subject_gap.json"
GAP_MD_PATH = "output/legal/table/_subject_gap.md"

# ── 시험 규칙 (widened) ─────────────────────────────────────────────────────
# 현행 규칙에서 무엇을 얼마나 넓혔는지 각 규칙에 명시한다. 넓힌 이유가 원문 실측이
# 아니면 넣지 않는다 — 표면 지표로 의미 판정을 대신하지 않는다.

# W1. 용지 — 현행 LOT_RE 는 `용지` 앞에 순한글 2~10자만 받는다. 실측 절 표목
#     `제 1 장 산업시설(A, B) 용지` 처럼 괄호 안 블록 기호가 끼면 전건 실패한다.
#     괄호 구간과 중점·가운뎃점 나열을 허용한다.
#
#     괄호는 **꼬리와 중간 모두** 허용해야 한다. 꼬리만 허용하면 `자족(Ⅰ)시설용지`
#     가 `시설용지` 로 잘려 9건이 잘못된 주어로 회수된다(전수 확인함). 잘린 주어는
#     원문에 없는 주어를 만든 것과 같다.
LOT_WIDE = re.compile(
    r"(?:[가-힣]{2,12}(?:\s*[\(（][^)）]{0,12}[\)）]\s*[가-힣]{0,12})?"
    r"(?:\s*[·․ㆍ,및]\s*[가-힣]{2,12}(?:\s*[\(（][^)）]{0,12}[\)）])?)*"
    r"(?:\s*[\(（][^)）]{0,12}[\)）])?"
    r")\s*용지")
# 주어 앞에 붙는 접속·조건 어구. 그대로 두면 `다만, 유치원 용지` 가 주어가 된다.
#
# **쉼표나 공백이 반드시 뒤따를 때만 벗긴다.** 처음엔 `단` 을 무조건 벗기게 썼다가
# `단독주택용지` 가 `독주택용지` 로 잘렸다(4건). 접속어 한 글자가 주어의 첫 음절과
# 겹치는 경우가 실재한다 — 전후 대조에서 잡아 좁혔다.
LEAD_CONJ = re.compile(
    r"^(?:다만|단|또한|그리고|그러나|이때|이\s*경우|아울러|한편)\s*[,，]\s*"
    r"|^(?:다만|또한|그리고|그러나|이때|아울러|한편)\s+")
# W2. 블록·획지 — 현행 BLOCK_RE 는 `A-1블록` 처럼 `블록` 글자가 붙은 것만 받는다.
#     실측 표 셀에는 `I1, I2` · `J1` · `E1~E3` · `주10` · `Sd-3` · `업무5` 같은
#     도면표시 코드가 맨몸으로 온다. 맨몸 코드는 표 셀 조각과 구분되지 않으므로
#     **줄머리에 홀로 선 것만** 받는다. 그래도 근거는 현행보다 약하다.
BLOCK_WIDE = re.compile(
    r"^\s*(?:[A-Za-z]{1,3}[a-z]?[-‐–]?\d{1,3}(?:\s*[~,·]\s*[A-Za-z]{0,3}\d{1,3})*"
    r"|[가-힣]{1,4}\d{1,3}(?:\s*[~,·]\s*[가-힣]{0,4}\d{1,3})*)\s*(?:$|\s{2,})")
# W3. 캡션 창 — 현행 12줄. 실측 표 본문이 줄로 풀린 문서에서 캡션이 더 위에 있다.
CAPTION_WINDOW_WIDE = 30
# W4. 조문 표제·조문 첫 문장. 실측 `#### 제3조 (건폐율․용적률․높이)` 바로 아래
#     `- ① 주차장용지의 건폐율 및 용적률…` 이 주어를 담는다.
ARTICLE_LEAD_WINDOW = 6

# 절 표목·캡션에서 주어로 쓰면 안 되는 표기. `제2편 용지별 시행지침` 의 `용지별`
# 은 특정 용지를 가리키지 않는다 — 이걸 주어로 쓰면 원문에 없는 주어를 만드는 것이다.
NON_SUBJECT = re.compile(r"용지별|용지의?\s*구분|각\s*용지|해당\s*용지|전체\s*용지")

# 표 머리 라벨. 주어가 아니라 열 이름이다. 넓힌 규칙으로 표 머리 셀을 훑으면
# `구 분  계 획 내 용` 의 오른쪽 셀이 주어로 올라온다 — 전수 확인에서 68건 중
# 13건이 이것이었다. 라벨 어휘를 주어에서 뺀다.
TABLE_LABEL = re.compile(
    r"^(?:계\s*획\s*내\s*용|계획내용|구\s*분|비\s*고|내\s*용|면\s*적"
    r"|위\s*치|용\s*도|규\s*모|번\s*호|항\s*목|기\s*준|합\s*계|소\s*계"
    r"|계)\s*(?:[\(（][^)）]{0,10}[\)）])?\s*$")

# 무주어 산문. 값 줄이 종결형인데 주어 자리에 지표만 있는 것 —
# `건폐율은 60% 이하로 적용한다`. 원문에 애초에 주어가 없다.
SUBJECTLESS_PROSE = re.compile(
    r"^\s*[-–•∙※\s]*(?:[①-⑳]|\d+[).]|[가-힣][).])?\s*"
    r"(?:건폐율|용적률|용적율)\s*(?:은|는|의|이|가)\s")


def _wide_hit(text, allow_block=False):
    """넓힌 어휘로 주어를 찾는다. (표기, subject_type) 또는 None."""
    if not text:
        return None
    for rx, typ in ((ev.ZONE_RE, "용도지역"), (LOT_WIDE, "용지")):
        for m in reversed(list(rx.finditer(text))):
            surf = LEAD_CONJ.sub("", m.group(0).strip()).strip()
            if typ == "용지" and NON_SUBJECT.search(surf):
                continue
            if surf:
                return surf, typ
    if allow_block:
        m = ev.BLOCK_RE.search(text)
        if m:
            return m.group(0).strip(), "블록·획지"
    return None


def _caption_wide(doc, line_no):
    """넓힌 창에서 표 캡션을 찾는다."""
    lines = doc["lines"]
    lo = max(doc["body_start"] - 1, line_no - 2 - CAPTION_WINDOW_WIDE)
    for i in range(line_no - 2, lo, -1):
        if i < 0:
            break
        raw = lines[i]
        m = tc.TABLE_REF.search(raw)
        if m and tc._is_caption(raw, m.start()):
            return {"text": raw.strip(), "line": i + 1,
                    "distance": line_no - (i + 1)}
    return None


def _article_lead(doc, rec):
    """조문 표제 직후 ARTICLE_LEAD_WINDOW 줄 안의 첫 항 문장."""
    a = rec.get("article")
    if not a:
        return None
    lines = doc["lines"]
    start = a["line"]
    for i in range(start, min(len(lines), start + ARTICLE_LEAD_WINDOW)):
        raw = lines[i]
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            break
        if i + 1 >= rec["line"]:
            break
        return {"text": raw.strip(), "line": i + 1}
    return None


def classify(rec, doc):
    """원인 유형 하나와 회수 시험 결과를 낸다.

    반환: (cause, detail, trial) — trial 은 회수됐으면
    {"subject","subject_type","subject_basis","widen","evidence"} 아니면 None.
    """
    # ── 계약상 제외. 회수 대상이 아니다 ────────────────────────────────────
    if rec["row_value_count"] >= 2:
        return ("다중값행_계약상제외",
                f"row_value_count={rec['row_value_count']}. spec §주어 판정이 "
                "주어를 붙이지 않기로 정한 것이며 판정 실패가 아니다", None)

    # ── 규범이 아닌 값은 회수 후보에서 먼저 뺀다 ─────────────────────────
    #     확정 집계에서 이미 빠지는 레코드라 주어를 붙여도 실익이 없고, 넓힌
    #     규칙에 오탐이 몰린다. 전수 확인에서 `탐색범위밖_동일줄_창초과` 9건이
    #     전건 `타지표`(비주거용도비율·연면적 70%)였고 그중 한 건은 값과 무관한
    #     줄에서 주어를 끌어왔다(영종하늘도시 1480줄). 순서를 뒤로 두면 이런
    #     오탐이 회수 견적에 그대로 실린다.
    if rec["context_class"] != "규범":
        return ("규범아님",
                f"context_class={rec['context_class']}. 확정 집계에서 이미 빠지는 "
                "레코드이며 주어 회수의 실익이 없다. 회수 후보에서 뺀다", None)

    line = rec["line_text"]
    pos = rec["surface_offset"][0]
    before = line[:pos]

    # ── 1) 같은 줄에 주어가 있는데 40자 창 밖 ─────────────────────────────
    hit = _wide_hit(before)
    if hit and not _wide_hit(before[-40:]):
        return ("탐색범위밖_동일줄_창초과",
                f"주어 표기가 값 앞 {pos - before.rfind(hit[0])}자 위치에 있어 "
                "현행 40자 창을 벗어난다",
                {"subject": hit[0], "subject_type": hit[1],
                 "subject_basis": "동일줄_인접명시",
                 "widen": "동일줄 창 40자 → 줄 전체",
                 "evidence": before.strip()[:120]})

    # ── 2) 같은 줄인데 현행 어휘가 못 잡음 ────────────────────────────────
    if hit:
        return ("어휘부족_동일줄",
                "주어 표기가 현행 40자 창 안에 있으나 현행 정규식이 못 잡는다",
                {"subject": hit[0], "subject_type": hit[1],
                 "subject_basis": "동일줄_인접명시",
                 "widen": "LOT_RE → LOT_WIDE (괄호·나열 허용)",
                 "evidence": before.strip()[:120]})

    # ── 3) 표 캡션 — 현행 12줄 창 밖 ──────────────────────────────────────
    cap = _caption_wide(doc, rec["line"])
    if cap and not rec.get("table_caption"):
        h = _wide_hit(cap["text"])
        if h:
            return ("탐색범위밖_캡션창초과",
                    f"표 캡션이 {cap['distance']}줄 위에 있어 현행 "
                    f"{ev.CAPTION_WINDOW}줄 창을 벗어난다",
                    {"subject": h[0], "subject_type": h[1],
                     "subject_basis": "단일값_표캡션",
                     "widen": f"캡션 창 {ev.CAPTION_WINDOW}줄 → "
                              f"{CAPTION_WINDOW_WIDE}줄",
                     "evidence": cap["text"][:120]})

    # ── 4) 캡션은 창 안에 있는데 어휘가 못 잡음 ───────────────────────────
    if rec.get("table_caption"):
        h = _wide_hit(rec["table_caption"]["text"])
        if h:
            return ("어휘부족_캡션",
                    "표 캡션이 현행 창 안에 있으나 현행 정규식이 못 잡는다",
                    {"subject": h[0], "subject_type": h[1],
                     "subject_basis": "단일값_표캡션",
                     "widen": "LOT_RE → LOT_WIDE (괄호·나열 허용)",
                     "evidence": rec["table_caption"]["text"][:120]})

    # ── 5) 절 표목 — 어휘가 못 잡음 ───────────────────────────────────────
    sec = rec.get("section")
    if sec:
        h = _wide_hit(sec["heading"])
        if h:
            return ("어휘부족_절표목",
                    "편·장·절 표목이 주어를 담고 있으나 현행 정규식이 못 잡는다",
                    {"subject": h[0], "subject_type": h[1],
                     "subject_basis": "단일값_장절",
                     "widen": "LOT_RE → LOT_WIDE (괄호·나열 허용)",
                     "evidence": sec["heading"][:120]})

    # ── 6) 조문 표제·조문 첫 항 ───────────────────────────────────────────
    a = rec.get("article")
    if a:
        h = _wide_hit(a["label"])
        if h:
            # 근거발췌는 **원문 줄을 그대로** 남긴다. 파싱된 조번호·표제를 다시
            # 조립하면 원문과 공백이 달라져 원문 대조 검사(게이트 16b)를 통과하지
            # 못한다 — 실측 `#### 제39조 (단독주택변 공동주택용지) _ [5-3…]` 대
            # 조립값 `제39조(단독주택변 공동주택용지)`. 조립값은 원문이 아니다.
            return ("탐색범위밖_조문표제",
                    "조문 표제가 주어를 담는다. 현행 규칙은 조문 표제를 보지 않는다",
                    {"subject": h[0], "subject_type": h[1],
                     "subject_basis": "단일값_조문표제",
                     "widen": "탐색원 추가 — 조문 표제",
                     "evidence": doc["lines"][a["line"] - 1].strip()[:120]})
        lead = _article_lead(doc, rec)
        if lead:
            h = _wide_hit(lead["text"])
            if h:
                return ("탐색범위밖_조문첫항",
                        f"조문 첫 항({lead['line']}줄)이 주어를 담는다. 현행 규칙은 "
                        "조문 본문을 보지 않는다",
                        {"subject": h[0], "subject_type": h[1],
                         "subject_basis": "단일값_조문첫항",
                         "widen": "탐색원 추가 — 조문 표제 직후 "
                                  f"{ARTICLE_LEAD_WINDOW}줄",
                         "evidence": lead["text"][:120]})

    # ── 7) 표 셀 도면표시 코드가 값과 분리 ────────────────────────────────
    #     `블록번호  R1` / `도면표시  공공` 이 값보다 위 줄에 홀로 있다.
    #     맨몸 코드는 표 셀 조각과 구분되지 않으므로 회수 후보로만 세고
    #     subject_type 은 `블록·획지` 로 둔다 — 용도지역으로 승격하지 않는다.
    lines = doc["lines"]
    for dist in range(1, 13):
        i = rec["line"] - 1 - dist
        if i < doc["body_start"]:
            break
        raw = lines[i]
        if not raw.strip():
            continue
        if ev.METRIC_RE.search(raw):
            continue
        hm = re.match(
            r"^\s*(?:블록번호|도면표시|단지구분)\s{2,}(\S.*)$", raw)
        if hm:
            code = re.split(r"\s{2,}", hm.group(1).strip())[0].strip()
            if code and len(code) <= 20 and not TABLE_LABEL.match(code):
                return ("표구조분리_도면표시코드",
                        f"주어가 표 머리 셀({i + 1}줄)에 있고 값과 다른 줄로 "
                        "풀렸다. 맨몸 코드라 근거가 약하다",
                        {"subject": code, "subject_type": "블록·획지",
                         "subject_basis": "표머리셀_인접",
                         "widen": "탐색원 추가 — 위 12줄 안의 표 머리 셀",
                         "evidence": raw.strip()[:120]})
        if BLOCK_WIDE.match(raw) and len(raw.strip()) <= 20:
            # 셀이 두 번 찍힌 잔해(`대1     대1`)는 앞 조각만 쓴다
            code = re.split(r"\s{2,}", raw.strip())[0].strip()
            if code and not TABLE_LABEL.match(code):
                return ("표구조분리_맨몸코드행",
                        f"주어로 보이는 코드가 {i + 1}줄에 홀로 있다. 표 셀 조각과 "
                        "구분되지 않아 근거가 약하다",
                        {"subject": code, "subject_type": "블록·획지",
                         "subject_basis": "표머리셀_인접",
                         "widen": "탐색원 추가 — 위 12줄 안의 맨몸 코드행",
                         "evidence": raw.strip()[:120]})

    # ── 8) 원문에 애초에 주어가 없음 ──────────────────────────────────────
    if SUBJECTLESS_PROSE.match(line):
        return ("무주어산문",
                "값 줄이 `건폐율은 … 이하` 형태로 지표 자체가 주어다. "
                "원문에 주어가 없으므로 회수 대상이 아니다", None)

    return ("주어없음_미분류",
            "넓힌 탐색원 어디에서도 주어 표기가 나오지 않는다. 원문에 주어가 "
            "없거나 표가 이 문서에서 소실된 것으로 보이나 유형을 확정하지 못했다",
            None)


# 용도지역 상한 관측 반경. 어떤 방어 가능한 규칙보다도 넓다 — 이 수가 상한이므로
# 실제 회수 가능 건수는 반드시 이보다 작다.
ZONE_PROBE_WINDOW = 30


def _zone_ceiling(rec, doc):
    """단일값 레코드 반경에 용도지역 표기가 있는지. **상한 관측이지 회수가 아니다.**

    조례 대조 병목이 용도지역이므로, 넓힌 규칙이 회수하지 못했더라도 원문 반경에
    용도지역이 실재하는지를 따로 센다. 여기 걸린 것을 주어로 채택하면 안 된다 —
    실측 표본은 `당해 용도지역(준주거지역)에서 허용하는 공장의 범위` 처럼 값의
    주어가 아니라 지나가는 언급이 대부분이다.
    """
    if ev.ZONE_RE.search(rec["quote"]):
        return "값줄"
    sec = rec.get("section")
    if sec and ev.ZONE_RE.search(sec["heading"]):
        return "절표목"
    lines = doc["lines"]
    for dist in range(1, ZONE_PROBE_WINDOW + 1):
        i = rec["line"] - 1 - dist
        if i < doc["body_start"]:
            break
        if ev.ZONE_RE.search(lines[i]):
            return f"위{dist}줄"
    return None


def build(root="."):
    with open(os.path.join(root, VALUES_PATH), encoding="utf-8") as fh:
        vals = json.load(fh)["records"]

    docs = {}

    def getdoc(rel):
        if rel not in docs:
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                docs[rel] = tc.parse_document(fh.read())
        return docs[rel]

    unresolved = [r for r in vals if r["subject_type"] is None]
    records = []
    for r in sorted(unresolved,
                    key=lambda x: (x["dstrcAppnNo"] or "", x["line"],
                                   x["surface_offset"][0])):
        doc = getdoc(r["source_file"])
        cause, detail, trial = classify(r, doc)
        rec = {
            "value_id": r["value_id"],
            "dstrcAppnNo": r["dstrcAppnNo"],
            "lc5": r["lc5"],
            "지역": r["지역"],
            "source_file": r["source_file"],
            "line": r["line"],
            "quote": r["quote"][:200],
            "metric": r["metric"],
            "value": r["value"],
            "row_shape": r["row_shape"],
            "row_value_count": r["row_value_count"],
            "context_class": r["context_class"],
            "현행_subject_reason": r["subject_reason"],
            "cause": cause,
            "cause_basis": detail,
            "회수가능": trial is not None,
            "시험_subject": trial["subject"] if trial else None,
            "시험_subject_type": trial["subject_type"] if trial else None,
            "시험_subject_basis": trial["subject_basis"] if trial else None,
            "시험_확장규칙": trial["widen"] if trial else None,
            "시험_근거발췌": trial["evidence"] if trial else None,
        }
        if r["row_value_count"] == 1:
            rec["용도지역_반경관측"] = _zone_ceiling(r, doc)
        records.append(rec)

    multi = [r for r in records if r["row_value_count"] >= 2]
    single = [r for r in records if r["row_value_count"] == 1]
    recov = [r for r in single if r["회수가능"]]

    cur_type = collections.Counter(v["subject_type"] for v in vals)
    trial_type = collections.Counter(r["시험_subject_type"] for r in recov)

    meta = {
        "생성기": "scripts/analyze_subject_gap.py",
        "성격": ("견적이다. 시험 규칙의 판정을 norm_values.json 에 반영하지 않는다. "
               "넓힌 규칙이 옳다는 결론이 아니라 넓히면 몇 건이 잡히는가의 관측값이다"),
        "모수": {
            "norm_values_전건": len(vals),
            "주어미상": len(records),
            "다중값행_계약상제외": len(multi),
            "단일값_회수모수": len(single),
            "$comment": ("회수 견적의 분모는 단일값 " + str(len(single)) +
                         " 이다. 다중값 " + str(len(multi)) +
                         " 은 spec §주어 판정이 주어를 붙이지 않기로 정한 계약이며 "
                         "판정 실패가 아니다. 미상 전건을 후보로 세면 "
                         f"{len(records) / max(len(single), 1):.1f}배 부풀려진다"),
        },
        "원인분포": dict(sorted(collections.Counter(
            r["cause"] for r in records).items())),
        "원인분포_단일값만": dict(sorted(collections.Counter(
            r["cause"] for r in single).items())),
        "회수견적": {
            "모수": len(single),
            "회수가능": len(recov),
            "회수불가": len(single) - len(recov),
            "확장규칙별": dict(sorted(collections.Counter(
                r["시험_확장규칙"] for r in recov).items())),
            "subject_basis별": dict(sorted(collections.Counter(
                r["시험_subject_basis"] for r in recov).items())),
        },
        "subject_type_증감": {
            "현행": {"용도지역": cur_type.get("용도지역", 0),
                   "용지": cur_type.get("용지", 0),
                   "블록·획지": cur_type.get("블록·획지", 0),
                   "미상": cur_type.get(None, 0)},
            "회수분": dict(sorted(trial_type.items(),
                                key=lambda x: (x[0] or ""))),
            "회수후": {
                "용도지역": cur_type.get("용도지역", 0)
                         + trial_type.get("용도지역", 0),
                "용지": cur_type.get("용지", 0) + trial_type.get("용지", 0),
                "블록·획지": cur_type.get("블록·획지", 0)
                          + trial_type.get("블록·획지", 0),
                "미상": cur_type.get(None, 0) - len(recov),
            },
            "$comment": ("조례 규범값의 주어는 용도지역이다. 용지가 아무리 늘어도 "
                         "그것만으로 조례 대조가 열리지 않는다. 용지 → 용도지역 "
                         "매핑은 발급하지 않는다(spec §하지 않는 것)"),
        },
        "회수후_용도지역_지구분포": dict(sorted(collections.Counter(
            r["dstrcAppnNo"] for r in recov
            if r["시험_subject_type"] == "용도지역").items())),
        "용도지역_상한관측": {
            "$comment": (
                f"단일값 {len(single)} 중 반경 {ZONE_PROBE_WINDOW}줄 안 어디든 "
                "용도지역 표기가 실재하는 건수. **회수 가능 건수가 아니라 상한이다** "
                "— 어떤 방어 가능한 규칙보다 넓게 잡은 값이므로 실제 회수는 반드시 "
                "이보다 작다. 표본을 눈으로 보면 `당해 용도지역(준주거지역)에서 "
                "허용하는 공장의 범위` 처럼 값의 주어가 아닌 지나가는 언급이 "
                "대부분이다. 이 수를 회수 견적으로 보고하면 안 된다"),
            "반경": ZONE_PROBE_WINDOW,
            "모수": len(single),
            "표기실재": sum(1 for r in single if r.get("용도지역_반경관측")),
            "위치별": dict(sorted(collections.Counter(
                r["용도지역_반경관측"] for r in single
                if r.get("용도지역_반경관측")).items())),
            "실제회수": trial_type.get("용도지역", 0),
        },
        "하지않은것": [
            "용지 → 용도지역 매핑 발급 — 지구마다 다르고 근거가 필요하다",
            "다중값행 열 정렬 복원 — spec §하지 않는 것",
            "원문에 없는 주어를 만들어 붙이기",
            "시험 결과를 norm_values.json 에 반영",
        ],
        "육안검수": [
            "회수 154건의 주어 표기를 중복 접어 56종 전수로 눈으로 확인했다. "
            "표 머리 라벨·잘린 주어·셀 잔해가 남아 있지 않다",
            "넓힌 규칙 1차본에서 오탐 3종을 눈으로 잡아 좁혔다 — 표 머리 라벨 "
            "13건(`계 획 내 용`·`구분`·`면 적 (㎡)`), 괄호가 중간에 낀 주어의 "
            "절단 9건(`자족(Ⅰ)시설용지` → `시설용지`), 접속어가 붙은 주어 1건. "
            "좁히는 과정에서 `단` 을 무조건 벗기다 `단독주택용지` 를 "
            "`독주택용지` 로 자른 자기 회귀도 전후 대조로 잡았다",
            "`규범아님` 을 회수 후보에서 먼저 빼기 전에는 `탐색범위밖_동일줄_"
            "창초과` 9건이 전건 `타지표` 였고 그중 1건은 값과 무관한 줄에서 "
            "주어를 끌어왔다(영종하늘도시 1480줄). 판정 순서를 고쳐 뺐다",
        ],
        "사각지대": [
            "md 에 남지 않은 표는 이 분석의 시야 밖이다. `_table_loss.json` 기준 "
            "표 소실 문서의 값은 애초에 norm_values.json 에 없어 미상으로도 세지 않는다",
            "맨몸 도면표시 코드(`R1`·`I1, I2`)는 표 셀 조각과 구분되지 않는다. "
            "회수 후보로 세되 근거는 현행 규칙보다 약하다",
            "회수된 주어가 그 값의 옳은 주어인지는 이 분석이 검증하지 않는다. "
            "탐색원에 주어 표기가 실재한다는 것까지만 관측한다",
            "다중값행 1,037 은 원인 분해에서 한 유형으로 접었다. 그 안의 세부 "
            "구조(열수 어긋남의 양상)는 측정하지 않는다",
        ],
    }
    return {"meta": meta, "records": records}


def write_md(data, root="."):
    m = data["meta"]
    p = m["모수"]
    e = m["회수견적"]
    t = m["subject_type_증감"]
    L = []
    L.append("# 주어 미상 원인 분해와 회수 견적\n")
    L.append("`scripts/analyze_subject_gap.py` 산출. **견적이지 판정이 아니다** — "
             "시험 규칙의 결과를 `norm_values.json` 에 반영하지 않았다.\n")
    L.append("## 모수\n")
    L.append("| 층 | 건수 |")
    L.append("|---|---:|")
    L.append(f"| norm_values 전건 | {p['norm_values_전건']:,} |")
    L.append(f"| 주어 미상 | {p['주어미상']:,} |")
    L.append(f"| 다중값행 계약상 제외 (회수 대상 아님) | {p['다중값행_계약상제외']:,} |")
    L.append(f"| **단일값 — 회수 견적의 모수** | **{p['단일값_회수모수']:,}** |")
    L.append("")
    L.append(p["$comment"] + "\n")
    L.append("## 원인별 분포 (미상 전건)\n")
    L.append("| 원인 | 건수 |")
    L.append("|---|---:|")
    for k, v in sorted(m["원인분포"].items(), key=lambda x: -x[1]):
        L.append(f"| {k} | {v:,} |")
    L.append(f"| **합** | **{sum(m['원인분포'].values()):,}** |")
    L.append("")
    L.append("## 회수 견적 — 단일값 모수 기준\n")
    L.append(f"모수 {e['모수']} · 회수 가능 {e['회수가능']} · 회수 불가 "
             f"{e['회수불가']} ({e['회수가능'] / max(e['모수'], 1) * 100:.1f}%)\n")
    L.append("| 확장 규칙 | 회수 건수 |")
    L.append("|---|---:|")
    for k, v in sorted(e["확장규칙별"].items(), key=lambda x: -x[1]):
        L.append(f"| {k} | {v:,} |")
    L.append("")
    L.append("## subject_type 증감 — 조례 대조 병목\n")
    L.append("| subject_type | 현행 | 회수분 | 회수 후 |")
    L.append("|---|---:|---:|---:|")
    for k in ("용도지역", "용지", "블록·획지"):
        L.append(f"| {k} | {t['현행'][k]:,} | {t['회수분'].get(k, 0):,} | "
                 f"{t['회수후'][k]:,} |")
    L.append(f"| 미상 | {t['현행']['미상']:,} | -{e['회수가능']:,} | "
             f"{t['회수후']['미상']:,} |")
    L.append("")
    L.append(t["$comment"] + "\n")
    z = m["용도지역_상한관측"]
    L.append("## 용도지역 상한 관측 — 회수 견적이 아니다\n")
    L.append(f"단일값 {z['모수']} 중 반경 {z['반경']}줄 안에 용도지역 표기가 "
             f"실재하는 것 **{z['표기실재']}건**. 넓힌 규칙이 실제로 주어로 채택한 것은 "
             f"{z['실제회수']}건이다.\n")
    L.append(z["$comment"] + "\n")
    L.append("## 육안 검수\n")
    for s in m["육안검수"]:
        L.append(f"- {s}")
    L.append("")
    L.append("## 사각지대\n")
    for s in m["사각지대"]:
        L.append(f"- {s}")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    data = build(args.root)
    out = os.path.join(args.root, GAP_PATH)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    with open(os.path.join(args.root, GAP_MD_PATH), "w", encoding="utf-8") as fh:
        fh.write(write_md(data, args.root))
    m = data["meta"]
    print(f"주어미상 {m['모수']['주어미상']} = 다중값 "
          f"{m['모수']['다중값행_계약상제외']} + 단일값 "
          f"{m['모수']['단일값_회수모수']}")
    print(f"회수가능 {m['회수견적']['회수가능']}/{m['회수견적']['모수']}")
    print("용도지역", m["subject_type_증감"]["현행"]["용도지역"], "→",
          m["subject_type_증감"]["회수후"]["용도지역"])


if __name__ == "__main__":
    main()
