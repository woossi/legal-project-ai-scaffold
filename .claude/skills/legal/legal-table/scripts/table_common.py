#!/usr/bin/env python3
"""legal-table 스크립트가 공유하는 md 파싱·표 참조 탐지 규약.

`legal-xref/scripts/xref_common.py` 와 같은 규약을 쓰되 import 하지 않는다
(design §스킬 폴더). 스킬 간 런타임 의존을 만들면 한쪽 갱신이 다른 쪽을 깨뜨린다.

줄번호 규약 — 모든 line 은 md 파일의 물리적 줄번호(1-based)다. frontmatter 를
`split` 으로 잘라낸 body 오프셋을 쓰지 않는다. frontmatter 길이는 원본구성 항목
수에 비례해 문서마다 다르므로(실측 19~40줄), body 오프셋을 쓰면 집계는 맞고
개별 출처가 전부 어긋난다. 파서는 파일 전체를 줄 배열로 읽고 frontmatter 구간은
건너뛰되 인덱스를 유지한다.

입력  없음 (라이브러리)
출력  없음 (라이브러리)
"""

import glob
import os
import re

MD_ROOT = "output/legal/markdown"
REGIONS = ("서울", "인천", "경기")

# ── frontmatter ─────────────────────────────────────────────────────────────
DSTRC_FM = re.compile(r'^지구번호:\s*"?([0-9]{5}[A-Z]{2}[0-9]{7})"?\s*$', re.M)
NAME_FM = re.compile(r'^지구명:\s*"?(.+?)"?\s*$', re.M)
REGION_FM = re.compile(r'^지역:\s*"?(.+?)"?\s*$', re.M)
TABLE_FM = re.compile(r'^표:\s*(\d+)\s*$', re.M)
ARTCNT_FM = re.compile(r'^조문수:\s*(\d+)\s*$', re.M)
EXTRACT_FM = re.compile(r'^\s*추출:\s*"?([^"\n]+?)"?\s*$', re.M)

# ── 조문·표목 ───────────────────────────────────────────────────────────────
HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
# 조문 표제. xref_common.ARTICLE_TITLE 과 같은 판정이다 — 괄호를 무조건 표제로
# 보면 `…높이(3층이상)` 같은 부가설명이 조문으로 섞인다.
ARTICLE_TITLE = re.compile(
    r"^제\s*(\d+)\s*조(?:\s*의\s?(\d+))?\s*[\(（]\s*([^)）]{2,40}?)\s*[\)）]"
)
SEC_NUM = "[0-9IVXⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+"
SEC_RE = re.compile(rf"제\s*({SEC_NUM})\s*(편|장|절)")
# 줄 전체가 편·장·절 표목인 평문 줄. 표목이 헤딩으로 승격되지 않은 문서가 있어
# 편·장·절 문맥을 이것으로 보강한다.
BARE_SEC_HEAD = re.compile(
    rf"^\s*(?:제\s*{SEC_NUM}\s*(?:편|장|절)\s*[^\n]{{0,40}}?\s*)+$")
HTML_COMMENT = re.compile(r"^\s*<!--.*-->\s*$")

_ROMAN = {"Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5, "Ⅵ": 6, "Ⅶ": 7, "Ⅷ": 8,
          "Ⅸ": 9, "Ⅹ": 10, "I": 1, "V": 5, "X": 10}


def sec_num(s):
    """편·장·절 번호를 정수로 만든다. 로마 숫자 표기가 섞여 있다."""
    s = (s or "").strip()
    if s.isdigit():
        return int(s)
    total = prev = 0
    for ch in reversed(s.upper()):
        v = _ROMAN.get(ch)
        if v is None:
            return None
        total += -v if v < prev else v
        prev = max(prev, v)
    return total or None


def sec_chain(title):
    """표목 문자열에서 (편|장|절, 번호) 를 순서대로 읽는다."""
    return [(m.group(2), sec_num(m.group(1))) for m in SEC_RE.finditer(title or "")]


# ── 표 참조 표기 ────────────────────────────────────────────────────────────
# 실측 표기: <표Ⅱ-1-1> · <표1-1-1> · <표 2-1-1> · [별표1] · <별표 1> ·
# <표Ⅱ-1-1~3>. 여는 괄호 종류가 원본마다 다르다.
TABLE_REF = re.compile(
    r"[<\[［（(]\s*(별표|표)\s*([0-9IVXⅠ-Ⅹ]+(?:\s*[-–~.]\s*[0-9IVXⅠ-Ⅹ]+)*)?"
    r"\s*[>\]］）)]"
)
# 법령의 별표는 이 지침의 표가 아니다. 앞 문맥이 법령이면 표 참조로 세지 않는다.
# `건축법 시행령 [별표1]` · `「자전거이용 활성화에 관한 법률 시행령」 제7조 및 [별표1]`
# · `「빛공해 방지법」시행규칙 제6조1항 관련[별표]` · `「건축법」 시행별[별표1]`(OCR)
#
# 이 필터가 거르는 424건은 전수를 눈으로 확인했다(전건 건축법·소방법 등 법령 별표).
# 표면 지표로 의미 판정을 대신하지 않는다는 규율에 따라, 규칙을 넓힐 때마다
# 새로 걸리는 항목을 다시 전수로 본다.
# 법령명 꼬리. `시 행령`·`시행별` 처럼 OCR·줄바꿈으로 공백이 끼거나 글자가 깨진
# 표기가 실재해 낱글자 사이 공백을 허용한다.
_LAW_TAIL = (r"(?:법\s*률|법|시\s*행\s*령|시\s*행\s*규\s*칙|시\s*행\s*별|"
             r"규\s*칙|규\s*정|조\s*례|훈\s*령|예\s*규)")
# 법령명과 별표 사이에 끼는 것 — 닫는 인용부호, 조문 번호, 괄호 표제, 연결어
_LAW_GAP = (r"(?:\s*(?:제\s*\d+\s*(?:조|항|호|목)(?:\s*의\s*\d+)?"
            r"|\d+\s*(?:항|호|목)"
            r"|[\(（][^)）]{0,30}[\)）]"
            r"|및|에|의|중|과|와|,|-|관련|따른|따라|의한|규정)\s*){0,6}")
LAW_CONTEXT = re.compile(
    _LAW_TAIL + r"[」』｣’”\"'\s]*" + _LAW_GAP + r"$"
)

# 값 표기 — 이 스킬 전체가 쓰는 규범값 신호
METRIC_KW = re.compile(r"건폐율|용적률|용적율")
PERCENT = re.compile(r"\d+(?:\.\d+)?\s*%")

# OCR 훼손 신호. 실측상 OCR 문서에서만 나오는 표 잔해다 —
# 줄머리 파이프는 표 괘선이 문자로 인식된 흔적이고, GFM 표행이 아니다
# (189문서 전건에서 정상 GFM 표행은 0이다).
OCR_PIPE_LINE = re.compile(r"^\s*\|")


def md_files(root="."):
    """189개 md 를 지역·파일명 정렬로 낸다. 정렬을 고정해 멱등성을 보장한다."""
    out = []
    for region in REGIONS:
        out += sorted(glob.glob(os.path.join(root, MD_ROOT, region, "*.md")))
    return out


def rel_path(path, root="."):
    """산출물에 남기는 경로. 실행 위치와 무관하게 같은 값이어야 한다."""
    return os.path.relpath(os.path.abspath(path),
                           os.path.abspath(root)).replace(os.sep, "/")


def parse_document(text):
    """md 한 건을 파싱한다. 반환값의 모든 줄번호는 파일 기준 1-based.

    반환 dict:
      지구번호 · 지구명 · 지역 · frontmatter_표 · frontmatter_조문수
      extraction_methods  원본구성 `추출:` 값 (등장 순서 유지, 중복 제거)
      lines               파일 전체 줄 배열 (0-base 인덱스)
      body_start          본문 첫 줄의 0-base 인덱스. frontmatter 는 잘라내지 않는다
      articles            [{조번호, 표제, 표제원문, line, 끝줄}] — line 은 1-based
      sections            [{heading, line}] — 편·장·절 표목, line 은 1-based
    """
    lines = text.split("\n")

    # frontmatter 구간을 건너뛰되 인덱스는 유지한다
    body_start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body_start = i + 1
                break
    fm = "\n".join(lines[:body_start])

    dm = DSTRC_FM.search(fm)
    nm = NAME_FM.search(fm)
    rm = REGION_FM.search(fm)
    tm = TABLE_FM.search(fm)
    am = ARTCNT_FM.search(fm)

    methods = []
    for m in EXTRACT_FM.findall(fm):
        m = m.strip()
        if m and m not in methods:
            methods.append(m)

    articles, sections = [], []
    for i in range(body_start, len(lines)):
        raw = lines[i]
        if not raw.strip() or HTML_COMMENT.match(raw):
            continue
        hm = HEADING_RE.match(raw.rstrip())
        if hm:
            title = hm.group(2)
            at = ARTICLE_TITLE.match(title)
            if len(hm.group(1)) == 4 and at:
                articles.append({
                    "조번호": f"제{int(at.group(1))}조"
                              + (f"의{int(at.group(2))}" if at.group(2) else ""),
                    "표제": at.group(3),
                    "표제원문": title,
                    "line": i + 1,
                    "끝줄": len(lines),
                })
            elif sec_chain(title):
                sections.append({"heading": title, "line": i + 1})
        elif len(raw) < 70 and BARE_SEC_HEAD.match(raw) and sec_chain(raw):
            sections.append({"heading": raw.strip(), "line": i + 1})

    for a, b in zip(articles, articles[1:]):
        a["끝줄"] = b["line"] - 1

    return {
        "지구번호": dm.group(1) if dm else None,
        "지구명": nm.group(1) if nm else None,
        "지역": rm.group(1) if rm else None,
        "frontmatter_표": int(tm.group(1)) if tm else None,
        "frontmatter_조문수": int(am.group(1)) if am else None,
        "extraction_methods": methods,
        "lines": lines,
        "body_start": body_start,
        "articles": articles,
        "sections": sections,
    }


def article_at(doc, line):
    """1-based 줄번호가 속한 조문. 없으면 (None, 사유).

    첫 h4 조문 앞의 본문은 조문으로 추정하지 않는다. 없는 조문을 가장 가까운
    h4 로 채우면 `.claude/rules/시행지침-조문-인용범위.md` 가 금지한
    `unassigned_document_preamble` 승격이 된다.
    """
    arts = doc["articles"]
    if not arts:
        return None, "이 문서에 h4 조문이 없다"
    if line < arts[0]["line"]:
        return None, (f"선행 h4 없음 — 이 문서의 첫 조문은 {arts[0]['line']}줄이고 "
                      f"이 표기는 그 앞({line}줄)에 있다")
    hit = None
    for a in arts:
        if a["line"] <= line:
            hit = a
        else:
            break
    return hit, None


def section_at(doc, line):
    """1-based 줄번호 직전의 편·장·절 표목. 없으면 None."""
    hit = None
    for s in doc["sections"]:
        if s["line"] <= line:
            hit = s
        else:
            break
    return hit


def table_refs(doc):
    """표 참조 표기를 낸다. frontmatter 구간은 건너뛴다.

    법령의 별표(`건축법 시행령 [별표1]`)는 이 지침의 표가 아니므로 제외한다.
    """
    out = []
    lines = doc["lines"]
    for i in range(doc["body_start"], len(lines)):
        raw = lines[i]
        if HTML_COMMENT.match(raw):
            continue
        for m in TABLE_REF.finditer(raw):
            pre = raw[:m.start()]
            if m.group(1) == "별표" and LAW_CONTEXT.search(pre.rstrip()):
                continue
            out.append({
                "surface": m.group(0),
                "line": i + 1,
                "col": m.start(),
                "kind": m.group(1),
                "is_caption": _is_caption(raw, m.start()),
                "line_text": raw,
            })
    return out


# 캡션 줄머리에 붙는 글머리표·강조 표기
_CAPTION_LEAD = " \t-*#>·∙▪□○●◦※0123456789.()（）①②③④⑤⑥⑦⑧⑨⑩"


def _is_caption(raw, start):
    """표 참조가 줄머리에 홀로 선 캡션인지. 문장 속 인용과 가른다."""
    return raw[:start].strip(_CAPTION_LEAD) in ("", "**")


# ── 도해 캡션 ───────────────────────────────────────────────────────────────
# `TABLE_REF` 를 넓히지 않고 따로 둔다. **그림은 표가 아니다** —
# `scan_table_loss.py` 의 표참조·본문실재 판정이 `TABLE_REF` 를 쓰므로,
# 여기에 그림을 넣으면 표 소실 계량이 통째로 오염된다.
#
# 실측(189문서 전건): 캡션 위치 참조는 표 3,769 · 그림 4,508 · 별표 44.
# 그림 캡션이 표만큼 많은데도 값 추출이 표만 봤다. 그 결과 그림 도해 안의
# 라벨값이 규범으로 실렸다.
CAPTION_REF = re.compile(
    r"[<\[［（(]\s*(별표|표|그림|사진|도면)\s*"
    r"([0-9IVXⅠ-Ⅹ]+(?:\s*[-–~.]\s*[0-9IVXⅠ-Ⅹ]+)*)?\s*[>\]］）)]"
)

# 도해가 규범이 아니라 **표기 방법·사례를 보인다**고 캡션이 명시한 표기.
#
# 어휘를 넓히면 실제 규범을 삼킨다. 넓은 후보(계획도·개념도·위치도·구상도·
# 기본방향·가이드라인 등)를 넣어 전수로 확인한 결과, 그 어휘가 추가로 끌어온
# 값은 14건이고 **전건이 실제 규범**이었다 — 의왕초평 2513~2699줄
# `<그림Ⅳ-2-17> 산림조망권역 경관계획도` 캡션 뒤의 용지별 도시건축사항 표
# (`건폐율 30% 이하 60% 이하`)가 통째로 예시로 뒤집힌다. 그래서 "예시·사례·
# 표기한다·범례" 계열만 남겼다. 이 어휘를 넓힐 때는 새로 걸리는 값을 다시
# 전수로 눈으로 본다.
EXAMPLE_CAPTION = re.compile(
    r"예\s*시|예\s*\)|사\s*례|표기\s*한다|표시\s*한다|표기\s*방법|범례")

# 캡션 참조(`<그림…>`) 없이 **줄 전체가 예시 선언**인 형태. 이것도 구간을 연다.
#
# `CAPTION_REF` 가 없으면 `caption_at` 이 None 이라 `example_zones` 가 구간을
# 만들지 않는다. 그러면 `예)` 바로 아래 줄의 값이 규범으로 남는다 — 상계 장암
# 59줄은 마커가 값 줄에 같이 있어 잡혔는데(V001293) **바로 다음 줄인 61줄
# `용적률 200%이하` 는 안 잡혔다**(V001294). `<그림>` 을 안 보던 1차 결함과
# 같은 구조다: 마커를 줄 단위로만 읽고 구간을 안 열었다.
#
# 어휘는 `EXAMPLE_CAPTION` 계열 그대로 두되 **줄 전체가 그 선언일 때만** 연다.
# 문장 속 `예)` 는 열지 않는다 — `예) 초등학교` 처럼 예시 대상이 같은 줄에
# 붙은 형태(강일 408줄)까지 받으려면 뒤에 짧은 꼬리를 허용해야 하는데, 그
# 완화가 문장 인용까지 먹는지는 전수로 확인했다(아래).
#
# 실측(189문서 전건): 캡션 없는 예시 선언 줄은 **19건**이고, 그중 값을 끌어오는
# 것은 상계 장암 3건(59·222·285줄)뿐이다. 나머지 16건(`사례` 4 · `범 례` 3 ·
# `예시` 3 · `표기방법` 1 · `예 시 도` 1 · `예시도` 1 · `범례` 1 · `예) 초등학교` 1)
# 구간 안에는 건폐율·용적률 값이 하나도 없다. 넓혀도 규범을 삼키지 않음을
# 전건으로 확인한 근거다.
EXAMPLE_DECL_LINE = re.compile(
    r"^\s*[-–•∙※\s]*(?:예\s*\)|\(\s*예\s*\)|<\s*예\s*시?\s*>|예\s*시\s*[):：]?"
    r"|사\s*례\s*[):：]?|표기\s*방법|범\s*례)\s*.{0,14}$")

# 조항 항목 줄. 규범 서술이 재개됐다는 신호이므로 도해 구간을 여기서 끊는다.
CLAUSE_ITEM = re.compile(r"^\s*[-–]?\s*[①-⑳]")


def caption_at(raw):
    """이 줄이 도해·표 캡션이면 (kind, 캡션본문). 아니면 None.

    캡션본문은 참조 표기(`<그림 2-1-1>`)와 강조기호를 걷어낸 나머지다.
    """
    m = CAPTION_REF.search(raw)
    if not m or not _is_caption(raw, m.start()):
        return None
    body = CAPTION_REF.sub(" ", raw).replace("*", " ").strip()
    return m.group(1), body


def example_zones(doc):
    """예시 도해 구간을 낸다. [{kind, caption, line, start, end}] — 1-based.

    구간은 캡션 **다음 줄부터** `end` 줄까지이며(캡션 줄 자체는 제외),
    end 는 다음 셋 중 가장 이른 것의 직전 줄이다.

      1) heading (`#`~`######`)   — h4 조문에서 반드시 끊어야 한다.
         왕숙2 1177줄 예시도는 1189줄 `#### 제12조` 에서 끝난다. 이걸로
         끊지 않으면 뒤따르는 조문의 규범값을 통째로 삼킨다
      2) 다음 캡션
      3) 조항 항목(`- ③`) — 규범 서술이 재개된 자리다
      4) 공백줄 2개 이상 (도해 블록은 붙어 있고, 캡션 직후 공백 1줄은 정상)

    (3) 은 전수 검증에서 나왔다. 과천과천 2494줄 예시도는 도해가 2500줄에서
    끝나는데 공백줄이 한 줄씩 번갈아 있어 (4) 가 걸리지 않고, 다음 heading 인
    2510줄까지 삼켜 2508줄 `근린생활시설 설치규모는 지상 건축연면적의 10%를
    초과할 수 없으며` 라는 **실제 규범**이 예시로 뒤집혔다. (3) 을 넣어 걸러지는
    값은 전수로 세어 그 1건뿐이다.

    (1)(2)(4) 만으로 잡은 값과 (1)(2)(3)(4) 로 잡은 값을 전건 비교해 차이가
    이 1건임을 확인했다 — 경계를 좁혀도 예시도 값을 잃지 않는다.

    구간을 여는 것은 **캡션 참조가 있는 캡션**과 **줄 전체가 예시 선언인 줄**
    (`EXAMPLE_DECL_LINE`, `kind == 예시선언`) 둘이다. 뒤엣것을 안 열면 마커가
    값 줄에 같이 있는 건만 잡히고 **바로 다음 줄의 같은 도해 값이 규범으로
    남는다** — 상계 장암 59줄(잡힘) 대 61줄(놓침).
    """
    lines = doc["lines"]
    n = len(lines)
    caps, heads = [], set()
    for i in range(doc["body_start"], n):
        raw = lines[i]
        if HTML_COMMENT.match(raw):
            continue
        if HEADING_RE.match(raw.rstrip()):
            heads.add(i)
        c = caption_at(raw)
        if c:
            caps.append((i, c[0], c[1], raw.strip()))
            continue
        if raw.strip() and EXAMPLE_DECL_LINE.match(raw):
            caps.append((i, "예시선언", raw.strip(), raw.strip()))
    capset = {c[0] for c in caps}

    out = []
    for ci, kind, body, text in caps:
        if not EXAMPLE_CAPTION.search(body):
            continue
        end = n
        blanks = 0
        for j in range(ci + 1, n):
            if j in heads or j in capset or CLAUSE_ITEM.match(lines[j]):
                end = j
                break
            if not lines[j].strip():
                blanks += 1
                if blanks >= 2:
                    end = j
                    break
            else:
                blanks = 0
        out.append({"kind": kind, "caption": text, "line": ci + 1,
                    "start": ci + 2, "end": end})
    return out
