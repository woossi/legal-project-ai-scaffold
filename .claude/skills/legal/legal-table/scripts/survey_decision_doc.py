#!/usr/bin/env python3
"""결정조서가 md 에 본문째 재수록됐는지 전건 판정하고, 획지↔용도지역 쌍을 실측한다.

용지↔용도지역 대응의 정본은 결정조서·결정도로 판정됐다. 이 스크립트는 그 정본이
md 안에 남아 있는지, 남았다면 몇 문서 몇 건까지 열리는지를 **계량만** 한다.
매핑을 발급하지 않는다 — 원문 근거 없는 매핑 생성은 이 스킬의 금지사항이다.

판정의 뼈대는 어휘 언급과 본문 재수록을 가르는 것이다. `결정조서` 어휘 보유 123문서
중 대부분은 목차·frontmatter·타 조문의 인용이지 조서 본문이 아니다.

입력  output/legal/markdown/{서울,인천,경기}/*.md  (189건)
출력  output/legal/table/_decision_doc_survey.json
      output/legal/table/_decision_doc_survey.md
전제  값·매핑을 발급하지 않는다. 관측과 계량까지다.
      모든 line 은 md 파일 기준 물리적 줄번호(1-based) — table_common 규약.
"""

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import table_common as tc

OUT_DIR = "output/legal/table"
OUT_JSON = "_decision_doc_survey.json"
OUT_MD = "_decision_doc_survey.md"

# ── 조서 어휘 ───────────────────────────────────────────────────────────────
# `결정 조서`처럼 공백이 낀 표기가 실재한다(서울영등포 L37). 공백 허용이 필수다.
JOSEO = re.compile(r"결\s*정\s*\(?\s*변\s*경\s*\)?\s*조\s*서|결\s*정\s*조\s*서")
# 조서류 일반. 도로총괄조서·편입토지조서 등 `결정`이 안 붙는 조서도 있다.
JOSEO_ANY = re.compile(r"조\s*서")

# 조서 본문 표제줄. 원문 실측상 ■ □ ∙ 같은 글머리 또는 번호 항목으로 열린다.
JOSEO_HEAD = re.compile(r"^\s*(?:[■□▪▶●○∙·]|\d+\)|\(\d+\)|[가-힣]\.|[ⅠⅡⅢⅣⅤ]+\.)?\s*"
                        r"[^\n]{0,60}?(?:결\s*정\s*\(?\s*변\s*경\s*\)?\s*조\s*서|"
                        r"결\s*정\s*조\s*서|총\s*괄\s*조\s*서)")

# frontmatter 원본구성의 `구분: "결정조서"` — 어휘 보유지만 본문이 아니다.
FM_GUBUN = re.compile(r'^\s*구분:\s*"?[^"\n]*조서')

# 목차 줄. 끝에 쪽번호가 붙거나 점선이 이어진다.
TOC_LINE = re.compile(r"(?:\.{3,}|…{2,})\s*\d*\s*$|\s\d{1,3}\s*$")

# ── 용도지역 ────────────────────────────────────────────────────────────────
# 국토계획법 시행령 제30조의 세분 용도지역. 표기 흔들림(공백·중점)을 허용한다.
ZONE = re.compile(
    r"제\s*[1-3]\s*종\s*(?:전용|일반)\s*주거지역|준\s*주거지역|"
    r"(?:중심|일반|근린|유통)\s*상업지역|"
    r"(?:전용|일반|준)\s*공업지역|"
    r"(?:보전|생산|자연)\s*녹지지역|"
    r"(?:보전|생산|계획)\s*관리지역|농림지역|자연환경보전지역")

# 획지·블록 코드. 실측 표기 — A1 · A-1 · CM-3 · R3-1 · 단독5 · 주26 · 근생16 ·
# 일상1 · 자족12 · 업무15 · S1-3 · IM2-3 · P2.
LOT_CODE = re.compile(
    r"(?:^|[\s,、·/(\[])"
    r"((?:[A-Z]{1,3}\d{0,2}-\d{1,2}|[A-Z]{1,3}-?\d{1,2}"
    r"|(?:단독|공동|주상복합|근생|일상|자족|업무|주|상|공|산업)\d{1,2}))"
    r"(?=[\s,、·/)\]~∼]|$)")

# 각주 표지 `주1)` `주3)` 는 획지코드가 아니다. 저장소 전체에 400건 있다 —
# 서울개봉 L58 이 이것 때문에 짝으로 잡혔다.
FOOTNOTE_MARK = re.compile(r"주\d{1,2}\)")

# 산문 문장 표지. 값이 아니라 서술이면 귀속 근거가 아니다.
PROSE_PAIR = re.compile(r"한다\.|하여야|따른다|설치한다|제외한다|경우는|권장|"
                        r"원칙으로|것으로 한다")

# `주22∼23` `근생17∼18` 처럼 범위로 묶인 표기. 앞쪽만 잡히므로 절단을 표시한다.
RANGE_MARK = re.compile(r"\d\s*[~∼-]\s*\d")

# 조서 필드 어휘. 훈령 별첨1 2-1.·2-2. 의 법정/예시 기재사항.
FIELD_WORDS = ["도면번호", "가구번호", "획지", "위치", "면적", "구분", "계획내용",
               "시설명", "최초결정일", "소재지", "지번", "지목", "소유자",
               "용도지역", "건폐율", "용적률", "높이", "규모", "비고"]


def frontmatter_span(doc):
    """frontmatter 구간의 1-based 줄 범위."""
    return 1, doc["body_start"]


def classify_mentions(doc):
    """조서 어휘 출현을 frontmatter · 목차 · 본문표제 · 산문인용으로 가른다.

    본문 재수록 판정의 핵심이 여기다. 어휘가 있다는 것과 조서가 실려 있다는 것은
    다른 주장이다 — 값 실재와 값 자격을 가르는 것과 같은 규율이다.
    """
    lines = doc["lines"]
    fm_end = doc["body_start"]
    out = {"frontmatter": [], "목차": [], "본문표제": [], "산문인용": []}
    for i, raw in enumerate(lines):
        if not JOSEO.search(raw):
            continue
        line = i + 1
        rec = {"line": line, "text": raw.strip()[:200]}
        if i < fm_end or FM_GUBUN.match(raw):
            out["frontmatter"].append(rec)
        elif TOC_LINE.search(raw.rstrip()) and len(raw.strip()) < 120:
            out["목차"].append(rec)
        elif (JOSEO_HEAD.match(raw) and len(raw.strip()) < 100
              and not PROSE_HEAD.search(raw)):
            out["본문표제"].append(rec)
        else:
            out["산문인용"].append(rec)
    return out


def body_after(doc, line, window=12):
    """표제 줄 다음의 실질 내용 줄. 빈 줄·주석·다음 표제는 내용이 아니다."""
    lines = doc["lines"]
    got = []
    for i in range(line, min(line + window, len(lines))):
        raw = lines[i]
        s = raw.strip()
        if not s:
            continue
        if tc.HTML_COMMENT.match(raw):
            continue
        if s.startswith("#"):
            break
        if JOSEO_HEAD.match(raw) and JOSEO.search(raw):
            break
        got.append({"line": i + 1, "text": s[:200]})
    return got


def is_placeholder(text):
    """`> [표]` · `> [그림]` — 변환이 표를 버리고 남긴 자리표시자."""
    return bool(re.match(r"^>\s*\[(표|그림)\]\s*$", text.strip()))


# 조서 표제로 세면 안 되는 줄. 전수 육안 확인에서 나온 오탐 세 갈래다.
# (1) 목차 항목 — 다음 줄이 또 다른 목차 항목이다
# (2) 조문 서술문 — `- ② 공개공지의 위치를 결정조서에 기재하여…` (쌍문역 L255)
# (3) 표 캡션의 단서 — `<표Ⅱ-4-1> ※ 세부위치는 결정조서 및 결정도에 따름` (과천과천 L861)
PROSE_HEAD = re.compile(r"^\s*-?\s*[①-⑳]|하여야|따른다|따름|한다\.|하며|경우에는|"
                        r"기재하여|^\s*\*\*<표")

# 데이터 행. 표가 줄로 풀리면 셀 사이가 연속 공백(또는 탭)으로 남는다.
# 조서 본문 재수록의 실증 근거는 표제가 아니라 이 행의 존재다.
DATA_ROW = re.compile(r"\S(?:[ \t]{2,}|\t)\S")


def is_data_row(text):
    """줄로 풀린 표의 데이터 행인가. 셀 경계가 2칸 이상 공백으로 남는다."""
    s = text.rstrip()
    if len(s.strip()) < 10 or is_placeholder(s):
        return False
    if s.lstrip().startswith("#") or s.lstrip().startswith(">"):
        return False
    # 셀 경계가 최소 2군데 — 3열 이상이어야 조서 행으로 본다
    return len(DATA_ROW.findall(s)) >= 2


def survey_doc(path, root):
    text = open(path, encoding="utf-8").read()
    doc = tc.parse_document(text)
    rel = tc.rel_path(path, root)
    lines = doc["lines"]

    men = classify_mentions(doc)
    heads = men["본문표제"]

    # 표제 뒤 내용 유무로 재수록을 가른다.
    # 재수록의 근거는 표제가 아니라 **데이터 행의 실재**다. 표제 뒤에 아무 줄이나
    # 있으면 재수록으로 세던 1차본은 57문서를 냈고, 전수 육안 확인 결과 대부분이
    # 다음 목차 항목·조문 서술문이었다. 데이터 행을 요구해 좁혔다.
    heads_with_body, heads_placeholder, heads_empty, heads_prose = [], [], [], []
    for h in heads:
        after = body_after(doc, h["line"])
        if not after:
            heads_empty.append(h)
            continue
        rows = [a for a in after if is_data_row(a["text"])]
        if rows:
            heads_with_body.append({**h, "데이터행": rows[:4]})
        elif all(is_placeholder(a["text"]) for a in after):
            heads_placeholder.append({**h, "placeholder": len(after)})
        else:
            heads_prose.append({**h, "본문줄": after[:3]})

    # 획지↔용도지역 동일줄 짝. 원문 줄에서만 읽고 줄을 건너 잇지 않는다.
    pairs, zone_only, lot_only, quarantined = [], [], [], []
    for i in range(doc["body_start"], len(lines)):
        raw = lines[i]
        if is_placeholder(raw):
            continue
        z = [m.group(0) for m in ZONE.finditer(raw)]
        l = [m.group(1) for m in LOT_CODE.finditer(raw)
             if not FOOTNOTE_MARK.match(raw[m.start(1):])]
        if z and l:
            # 산문 문장은 짝이 아니다. 원문 육안 확인에서 나온 오탐 —
            # 성남판교대장 L922 `주거지역 및 준주거지역 주차장(P2, P3)의 출입구는…`
            # 은 용도지역과 코드가 한 줄에 있을 뿐 귀속 관계가 아니다.
            rec = {
                "line": i + 1,
                "용도지역": sorted(set(z)),
                "획지코드": sorted(set(l)),
                "인용": raw.strip()[:300],
                "범위표기절단": bool(RANGE_MARK.search(raw)),
            }
            if PROSE_PAIR.search(raw):
                rec["격리사유"] = "산문문장"
                quarantined.append(rec)
            else:
                pairs.append(rec)
        elif z and not l:
            zone_only.append(i + 1)
        elif l and not z:
            lot_only.append(i + 1)

    # 조서 필드 어휘가 한 줄에 3개 이상 — 표 머리가 줄로 풀린 흔적.
    field_rows = []
    for i in range(doc["body_start"], len(lines)):
        raw = lines[i]
        if is_placeholder(raw) or len(raw.strip()) < 8:
            continue
        hit = [w for w in FIELD_WORDS if w in raw]
        if len(hit) >= 3:
            field_rows.append({"line": i + 1, "필드": hit,
                               "인용": raw.strip()[:250]})

    # 조서 본문 구간의 용도지역 행이 어느 축을 쓰는가. (2) 의 답이 여기다 —
    # 결정조서를 확보해도 그 표가 획지 축을 안 담으면 매핑이 열리지 않는다.
    zone_rows = []
    for h in heads_with_body:
        for j in range(h["line"] - 1, min(h["line"] + 20, len(lines))):
            raw = lines[j]
            if not ZONE.search(raw) or not is_data_row(raw):
                continue
            codes = [m.group(1) for m in LOT_CODE.finditer(raw)
                     if not FOOTNOTE_MARK.match(raw[m.start(1):])]
            zone_rows.append({
                "line": j + 1,
                "조서표제": h["text"][:60],
                "획지코드": sorted(set(codes)),
                "축": "획지축" if codes else "면적집계축",
                "인용": raw.strip()[:200],
            })

    ph = sum(1 for i in range(len(lines)) if is_placeholder(lines[i]))

    # 재수록 판정. 표제만 있고 내용이 없으면 재수록이 아니다.
    if heads_with_body:
        verdict = "본문재수록"
    elif heads_placeholder:
        verdict = "표제만_표소실"
    elif heads_empty or heads_prose:
        verdict = "표제만_내용없음"
    elif men["목차"] or men["산문인용"] or men["frontmatter"]:
        verdict = "언급만"
    else:
        verdict = "어휘없음"

    return {
        "지구번호": doc["지구번호"],
        "지구명": doc["지구명"],
        "지역": doc["지역"],
        "path": rel,
        "판정": verdict,
        "frontmatter_표": doc["frontmatter_표"],
        "extraction_methods": doc["extraction_methods"],
        "표자리표시자": ph,
        "어휘출현": {k: len(v) for k, v in men.items()},
        "조서표제수": len(heads),
        "표제_데이터행있음": len(heads_with_body),
        "표제_자리표시자": len(heads_placeholder),
        "표제_내용없음": len(heads_empty),
        "표제_산문만": len(heads_prose),
        "조서표제_본문": heads_with_body[:12],
        "조서표제_자리표시자": heads_placeholder[:12],
        "획지용도지역_쌍수": len(pairs),
        "획지용도지역_쌍": pairs[:20],
        "조서_용도지역행수": len(zone_rows),
        "조서_용도지역행_획지축": sum(1 for r in zone_rows if r["축"] == "획지축"),
        "조서_용도지역행": zone_rows[:12],
        "격리_쌍수": len(quarantined),
        "격리_쌍": quarantined[:10],
        "범위표기절단_쌍수": sum(1 for p in pairs if p["범위표기절단"]),
        "용도지역만_줄수": len(zone_only),
        "획지코드만_줄수": len(lot_only),
        "조서필드행수": len(field_rows),
        "조서필드행": field_rows[:10],
    }


SRC_ROOT = "output/legal/시행지침"
# 조서뿐 아니라 결정도·용도지역도 센다. 정본 판정이 "결정조서·결정도" 이므로
# 결정도 파일의 실재가 조서와 같은 무게를 갖는다.
ZIP_KW = re.compile(r"조서|결정도|용도지역|획지")


def survey_sources(root="."):
    """원본 zip 의 **목록만** 읽는다. 압축을 풀지 않는다.

    md 에서 소실된 결정조서가 원본에 별도 파일로 남아 있는지 본다. 목록은 관측이지
    회수 확정이 아니다 — 파일이 있다는 것과 표가 파싱된다는 것은 다른 주장이다.
    """
    import zipfile
    base = os.path.join(root, SRC_ROOT)
    zips = sorted(glob.glob(os.path.join(base, "**", "*.zip"), recursive=True))
    hits, broken = [], []
    total_entries = 0
    for z in zips:
        try:
            with zipfile.ZipFile(z) as f:
                names = f.namelist()
        except Exception as e:
            broken.append({"path": tc.rel_path(z, root),
                           "오류": type(e).__name__})
            continue
        dec = []
        for n in names:
            try:
                dec.append(n.encode("cp437").decode("cp949"))
            except Exception:
                dec.append(n)
        total_entries += len(dec)
        h = sorted(n for n in dec if ZIP_KW.search(n))
        if h:
            hits.append({"path": tc.rel_path(z, root),
                         "내부파일수": len(dec), "조서파일": h})
    counts = {}
    for ext, pat in (("hwp", "*.hwp"), ("hwpx", "*.hwpx"),
                     ("pdf", "*.pdf"), ("zip", "*.zip")):
        counts[ext] = len(glob.glob(os.path.join(base, "**", pat),
                                    recursive=True))
    return {
        "원본파일수": counts,
        "zip_열림": len(zips) - len(broken),
        "zip_내부파일_합계": total_entries,
        "조서결정도파일_보유zip": len(hits),
        "조서결정도파일_보유목록": hits,
        "열지못한zip": broken,
        "주의": "목록 관측이다. 압축을 풀지 않았고 표 파싱 가능성은 판정하지 않았다.",
        "hwp_pdf_사각지대": (
            f"단일 hwp {counts['hwp']} · pdf {counts['pdf']} 는 내부에 조서가 "
            "포함됐는지 열어보지 않으면 알 수 없다. 이번 조사 범위 밖이다."
        ),
    }


def build(root="."):
    docs = [survey_doc(p, root) for p in tc.md_files(root)]

    verdicts = {}
    for d in docs:
        verdicts[d["판정"]] = verdicts.get(d["판정"], 0) + 1

    pair_docs = [d for d in docs if d["획지용도지역_쌍수"] > 0]
    body_docs = [d for d in docs if d["판정"] == "본문재수록"]

    meta = {
        "생성기": "survey_decision_doc.py",
        "문서수": len(docs),
        "판정분포": dict(sorted(verdicts.items())),
        "조서어휘_보유문서": sum(1 for d in docs
                            if sum(d["어휘출현"].values()) > 0),
        "조서표제_보유문서": sum(1 for d in docs if d["조서표제수"] > 0),
        "본문재수록_문서": len(body_docs),
        "표소실_문서": sum(1 for d in docs if d["판정"] == "표제만_표소실"),
        "획지용도지역_쌍보유문서": len(pair_docs),
        "획지용도지역_쌍_총건수": sum(d["획지용도지역_쌍수"] for d in docs),
        "격리_쌍_총건수": sum(d["격리_쌍수"] for d in docs),
        "범위표기절단_쌍_총건수": sum(d["범위표기절단_쌍수"] for d in docs),
        "조서본문_내_쌍_총건수": sum(d["획지용도지역_쌍수"] for d in docs
                             if d["판정"] == "본문재수록"),
        "조서_용도지역행_총수": sum(d["조서_용도지역행수"] for d in docs),
        "조서_용도지역행_획지축_총수": sum(d["조서_용도지역행_획지축"]
                                for d in docs),
        "표자리표시자_보유문서": sum(1 for d in docs if d["표자리표시자"] > 0),
        "표자리표시자_총건수": sum(d["표자리표시자"] for d in docs),
        "모수주의": (
            "조서어휘 보유와 조서본문 재수록은 다른 모수다. 어휘는 목차·"
            "frontmatter·산문인용을 포함한다. 획지↔용도지역 쌍은 조서 본문이 아니라 "
            "시행지침 본문 표가 줄로 풀린 자리에서도 나온다 — 출처를 섞지 않는다."
        ),
        "사각지대": [
            "표 자리표시자(> [표])로 버려진 표의 내부는 md 로 판정 불가다. "
            "쌍이 0건이어도 원본 hwp 에는 있을 수 있다.",
            "획지코드 정규식은 실측 표기에서 귀납했다. 미관측 표기는 놓친다.",
            "동일줄 짝만 센다. 표가 줄로 풀리며 코드와 용도지역이 다른 줄로 "
            "갈린 경우는 짝으로 세지 않는다 — 줄을 건너 이으면 근거 없는 매핑이 된다.",
        ],
        "하지않은것": [
            "용지→용도지역 매핑 발급",
            "zip 압축 해제",
            "쌍의 법적 정합성 판정",
        ],
    }

    return {"meta": meta, "원본조사": survey_sources(root), "documents": docs}


def render_md(data):
    m = data["meta"]
    L = ["# 결정조서 md 재수록 전수조사", "",
         f"문서 {m['문서수']} · 조서어휘 보유 {m['조서어휘_보유문서']} · "
         f"조서표제 보유 {m['조서표제_보유문서']} · **본문재수록 "
         f"{m['본문재수록_문서']}**", "",
         "## 판정 분포", "", "| 판정 | 문서수 |", "|---|---:|"]
    for k, v in m["판정분포"].items():
        L.append(f"| {k} | {v} |")

    L += ["", "## 획지↔용도지역 쌍", "",
          f"쌍 보유 문서 {m['획지용도지역_쌍보유문서']} · "
          f"총 {m['획지용도지역_쌍_총건수']}건 · "
          f"격리 {m['격리_쌍_총건수']}건 · "
          f"범위표기 절단 {m['범위표기절단_쌍_총건수']}건", "",
          f"**조서 본문 안에서 나온 쌍은 {m['조서본문_내_쌍_총건수']}건이다.** "
          "관측된 쌍은 전부 시행지침 본문 표가 줄로 풀린 자리에서 나왔다 — "
          "출처가 다르므로 결정조서 회수 견적에 넣지 않는다.", "",
          "| 지구명 | 지역 | 판정 | 쌍 | 조서표제 | 자리표시자 |",
          "|---|---|---|---:|---:|---:|"]
    for d in sorted(data["documents"], key=lambda x: -x["획지용도지역_쌍수"]):
        if d["획지용도지역_쌍수"] == 0:
            continue
        L.append(f"| {d['지구명']} | {d['지역']} | {d['판정']} | "
                 f"{d['획지용도지역_쌍수']} | {d['조서표제수']} | "
                 f"{d['표자리표시자']} |")

    L += ["", "## 결정조서는 어느 축으로 용도지역을 담는가", "",
          f"조서 본문 구간의 용도지역 데이터행 {m['조서_용도지역행_총수']}건 중 "
          f"획지 코드를 같은 행에 담은 것은 "
          f"**{m['조서_용도지역행_획지축_총수']}건**이다.", "",
          "관측된 용도지역 결정조서의 열 구성은 "
          "`용도지역 · 면적(㎡) · 구성비(%)` 로, 지구 전체의 **면적 집계**다. "
          "획지·블록 축이 아니므로 결정조서를 확보해도 이 표만으로는 "
          "획지→용도지역 매핑이 열리지 않는다.", ""]

    L += ["", "## 본문 재수록 문서", "",
          "| 지구명 | 지역 | 조서표제 | 데이터행표제 | 쌍 |",
          "|---|---|---:|---:|---:|"]
    for d in data["documents"]:
        if d["판정"] != "본문재수록":
            continue
        L.append(f"| {d['지구명']} | {d['지역']} | {d['조서표제수']} | "
                 f"{d['표제_데이터행있음']} | {d['획지용도지역_쌍수']} |")

    s = data["원본조사"]
    L += ["", "## 원본 zip 목록 조사 (압축 해제 없음)", "",
          f"원본 {s['원본파일수']} · zip 열림 {s['zip_열림']} · "
          f"내부파일 {s['zip_내부파일_합계']} · "
          f"조서·결정도 보유 zip {s['조서결정도파일_보유zip']} · "
          f"열지 못한 zip {len(s['열지못한zip'])}", ""]
    for b in s["열지못한zip"]:
        L.append(f"- 열지 못함: `{b['path']}` ({b['오류']})")
    if s["열지못한zip"]:
        L.append("")
    for h in s["조서결정도파일_보유목록"]:
        L.append(f"- `{h['path']}` ({h['내부파일수']}개)")
        for n in h["조서파일"]:
            L.append(f"  - {n}")
    L += ["", f"{s['hwp_pdf_사각지대']}"]

    L += ["", "## 사각지대", ""] + [f"- {s2}" for s2 in m["사각지대"]]
    L += ["", "## 하지 않은 것", ""] + [f"- {s}" for s in m["하지않은것"]] + [""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    a = ap.parse_args()

    data = build(a.root)
    out = os.path.join(a.root, OUT_DIR)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, OUT_JSON), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")
    with open(os.path.join(out, OUT_MD), "w", encoding="utf-8") as f:
        f.write(render_md(data))

    m = data["meta"]
    print(f"문서 {m['문서수']} · 본문재수록 {m['본문재수록_문서']} · "
          f"쌍 {m['획지용도지역_쌍_총건수']}건 / "
          f"{m['획지용도지역_쌍보유문서']}문서")
    for k, v in m["판정분포"].items():
        print(f"  {k}: {v}")


# 산출물을 쓰는 스크립트에는 가드를 건다. import 만으로 전수조사 산출물이
# 덮어써진 사고가 이 저장소에 있다.
if __name__ == "__main__":
    main()
