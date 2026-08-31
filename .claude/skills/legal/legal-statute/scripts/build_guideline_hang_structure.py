#!/usr/bin/env python3
"""훈령 「지구단위계획수립지침」 전문을 장·절·항 단위로 구조화한다.

이 문서는 `guideline_article_corpus.jsonl.gz` 에서 `parse_status: 비조문형_전문`
으로 들어와 있다. 조문(제N조) 체계가 아니라 `x-y-z.` 번호 체계를 쓰므로 조문
파서가 자르지 못하고 전문 한 덩어리로 보존되어 있다. 이 스크립트는 그 덩어리를
원문 줄 단위로 훑어 장·절 표제와 항 번호만으로 경계를 정한다.

경계는 원문에 실재하는 표지로만 정한다. 표지가 없는 줄은 직전 항의 본문으로
붙이며, 항 경계를 추정해서 만들지 않는다.

원문/파생 분리
  본문·번호표기·줄범위는 원문 관측이고, 하위목·별표참조·삭제여부는 판정이다.
  판정 필드는 원문 필드를 덮어쓰지 않는다.

항 번호 체계 (원문 실측)
  2단  9-1.        제9장 행정사항. 이 장에는 절이 없다
  3단  3-8-2.      표준형
  4단  3-2-2-1.    3단 항과 나란히 인용되는 단위 (예: "3-2-2. 및 3-2-2-1.에 따라")
  번호 뒤 마침표 앞에 공백이 끼는 변형이 있다 (`2-6-8 .`).

입력  output/legal/statute/guideline_article_corpus.jsonl.gz
      (document_key == admrul:2100000241690, provisions[0].text)
출력  output/legal/statute/수립지침_항구조.json
      output/legal/statute/_수립지침_파싱_리포트.json
"""

import argparse
import gzip
import hashlib
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

DOCUMENT_KEY = "admrul:2100000241690"
SCRIPT_PATH = ".claude/skills/legal/legal-statute/scripts/build_guideline_hang_structure.py"
DEFAULT_CORPUS = "output/legal/statute/guideline_article_corpus.jsonl.gz"
DEFAULT_OUT_DIR = "output/legal/statute"

# 장·절 표제. 「제1장  총   칙」처럼 글자 사이 공백이 벌어지는 표기를 허용한다.
RE_CHAPTER = re.compile(r"^\s*제\s*(\d+)\s*장\s*(.*)$")
RE_SECTION = re.compile(r"^\s*제\s*(\d+)\s*절\s*(.*)$")
# 항 번호. 2~4단을 받고 마침표 앞 공백 변형을 허용한다.
RE_HANG = re.compile(r"^\s*(\d+(?:-\d+){1,3})(\s*)\.")
# 표준형 정규식. 이것이 놓친 항을 리포트에 남기기 위해서만 쓴다.
RE_HANG_STRICT = re.compile(r"^\s*(\d+-\d+-\d+)\.")

# 하위목 표지. 원문 실측으로 확인한 두 종만 정본이고, 가나다목은 0건임을
# 측정해 두기 위해 함께 돌린다.
RE_ITEM_PAREN = re.compile(r"^\s*\((\d+)\)")
RE_ITEM_CIRCLE = re.compile(r"^\s*([①-⑳])")
# `[가-하]` 로 쓰면 한글 음절 블록 전체를 받아 「예) 가중치의 산정」이 목으로 잡힌다.
# 목 표지로 쓰이는 열네 글자만 명시한다.
RE_ITEM_GANADA = re.compile(r"^\s*([가나다라마바사아자차카타파하])[.)]\s")

# 별표·별첨 참조. 번호를 요구해 부분문자열 오탐을 막는다.
# 어깨번호 없는 `별표`·`별지`만 찾으면 「특별지침」·「생물서식공간」이 걸린다.
REF_PATTERNS = OrderedDict([
    ("별표", re.compile(r"별\s*표\s*제?\s*\d+")),
    ("별첨", re.compile(r"별\s*첨\s*\d+")),
    ("별지서식", re.compile(r"별\s*지\s*제?\s*\d+\s*호(?:\s*서식)?")),
])

RE_IMG = re.compile(r'^\s*<img\s+id="(\d+)"\s*>\s*$')
RE_IMG_CLOSE = re.compile(r"^\s*</img>\s*$")
RE_BRANCH = re.compile(r"^\s*\d+(?:-\d+)*의\s*\d+")

# 표 참조 마커. 어깨번호나 조사를 요구해 「표고」·「지표층」·「안내표지판」·
# 「목표년도」를 걸러낸다. 이 문서에서 맨 `표` 로 찾으면 22건 중 19건이 오탐이다.
TABLE_PATTERNS = OrderedDict([
    ("문서내표지시", re.compile(r"(?:위|아래|다음|상기)\s*표(?=[은는이가을를의에와과로])")),
    ("외부별표", re.compile(r"별\s*표\s*제?\s*\d+")),
    # 시행지침 md 에 남는 `<표Ⅱ-3-2>` 류. 이 문서에 있는지 재기 위해 함께 돌린다.
    ("괄호표마커", re.compile(r"[<〈〔［\[]\s*표[^>〉〕］\]]{0,20}[>〉〕］\]]")),
])

# 그림 참조 마커. `구상도`·`조감도` 류 도해명칭은 넣지 않는다 — 이 문서의
# 유일한 출현(경관관리구상도)이 계획 수립자가 작성할 산출물 지시이지
# 문서 안 그림 참조가 아니다. 기각 근거는 리포트에 남긴다.
FIGURE_PATTERNS = OrderedDict([
    ("그림지시", re.compile(r"[<〈〔［\[]?\s*그\s*림(?:\s*\d+)?")),
])
RE_FIGURE_REJECT = re.compile(
    r"예시도|모식도|개념도|절차도|구상도|조감도|투시도|배치도|단면도|입면도")

# 텍스트 표의 흔적. 이 문서는 0줄이라 표가 전부 이미지로만 있다는 근거가 된다.
RE_TEXT_TABLE = re.compile(r"[\t|｜]")


def load_text(corpus_path, document_key):
    """corpus 에서 대상 문서의 전문과 서지를 꺼낸다."""
    with gzip.open(corpus_path, "rt", encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("document_key") != document_key:
                continue
            provisions = rec.get("provisions") or []
            if len(provisions) != 1:
                raise ValueError(
                    f"provisions 가 1건이 아니다: {len(provisions)}건. "
                    "전문 한 덩어리 전제가 깨졌다")
            return rec, provisions[0]["text"]
    raise KeyError(f"corpus 에 {document_key} 가 없다: {corpus_path}")


def classify_line(line):
    """줄을 구조 표지로 분류한다. 판정이 아니라 표지 관측이다."""
    m = RE_CHAPTER.match(line)
    if m:
        return "장표제", m
    m = RE_SECTION.match(line)
    if m:
        return "절표제", m
    m = RE_HANG.match(line)
    if m:
        return "항머리", m
    if not line.strip():
        return "공백", None
    return "본문", None


def scan(lines):
    """원문을 한 번 훑어 장·절 표제와 항 머리 위치를 모은다."""
    chapters, sections, heads = [], [], []
    cur_ch = cur_se = None
    for idx, line in enumerate(lines, start=1):
        kind, m = classify_line(line)
        if kind == "장표제":
            cur_ch = {
                "장번호": int(m.group(1)),
                "장제목": normalize_title(m.group(2)),
                "장제목_원문표기": line.rstrip("\n"),
                "원문줄": idx,
            }
            chapters.append(cur_ch)
            cur_se = None
        elif kind == "절표제":
            if cur_ch is None:
                raise ValueError(f"{idx}줄: 장 밖에 절 표제가 있다")
            cur_se = {
                "장번호": cur_ch["장번호"],
                "절번호": int(m.group(1)),
                "절제목": normalize_title(m.group(2)),
                "절제목_원문표기": line.rstrip("\n"),
                "원문줄": idx,
            }
            sections.append(cur_se)
        elif kind == "항머리":
            heads.append({
                "줄": idx,
                "번호": m.group(1),
                "번호표기": line[m.start(1):m.end(0)],
                "머리끝_열": m.end(0),
                "장": cur_ch,
                "절": cur_se,
            })
    return chapters, sections, heads


def normalize_title(raw):
    """표제에서 글자 사이 늘린 공백만 접는다. 글자는 건드리지 않는다."""
    return re.sub(r"\s+", " ", raw).strip()


def detect_items(body_lines):
    """하위목 표지를 센다. 항 머리줄은 제외하고 뒤따르는 줄만 본다."""
    paren = [m.group(1) for m in
             (RE_ITEM_PAREN.match(l) for l in body_lines) if m]
    circle = [m.group(1) for m in
              (RE_ITEM_CIRCLE.match(l) for l in body_lines) if m]
    ganada = [m.group(1) for m in
              (RE_ITEM_GANADA.match(l) for l in body_lines) if m]
    return {
        "존재": bool(paren or circle or ganada),
        "괄호숫자목_수": len(paren),
        "원숫자목_수": len(circle),
        "가나다목_수": len(ganada),
        "괄호숫자목_표기": paren,
        "원숫자목_표기": circle,
        "가나다목_표기": ganada,
    }


def detect_refs(block_text):
    """별표·별첨 참조를 어깨번호까지 요구해 찾는다."""
    hits = []
    for name, pat in REF_PATTERNS.items():
        for m in pat.finditer(block_text):
            start = max(0, m.start() - 30)
            end = min(len(block_text), m.end() + 30)
            hits.append({
                "종류": name,
                "표기": m.group(0),
                "근거발췌": block_text[start:end].replace("\n", " "),
            })
    return {"존재": bool(hits), "참조수": len(hits), "참조": hits}


def detect_images(block_lines):
    ids = [m.group(1) for m in (RE_IMG.match(l) for l in block_lines) if m]
    return {"존재": bool(ids), "이미지id": ids}


def scan_markers(block_lines, start_line, patterns):
    """줄 단위로 참조 마커를 찾고 절대 줄번호와 발췌를 붙인다."""
    found = []
    for offset, line in enumerate(block_lines):
        for kind, pat in patterns.items():
            for m in pat.finditer(line):
                s = max(0, m.start() - 30)
                e = min(len(line), m.end() + 30)
                found.append({
                    "종류": kind,
                    "표기": m.group(0),
                    "줄": start_line + offset,
                    "근거발췌": line[s:e].strip(),
                })
    return found


def detect_channels(block_lines, start_line, images):
    """표·그림 채널 마커와 유실 의심을 가른다.

    이 corpus 의 본문은 표·그림을 조용히 지우지 않고 `<img id>` 로 자리를
    남긴다. 그래서 유실의 채널이 둘로 갈린다 — 자리는 남고 내용만 없는 것과,
    지시 문구만 있고 자리조차 없는 것이다.
    """
    table = scan_markers(block_lines, start_line, TABLE_PATTERNS)
    figure = scan_markers(block_lines, start_line, FIGURE_PATTERNS)
    for img_id in images["이미지id"]:
        figure.append({
            "종류": "이미지태그",
            "표기": f'<img id="{img_id}">',
            "줄": next(start_line + i for i, l in enumerate(block_lines)
                      if RE_IMG.match(l) and RE_IMG.match(l).group(1) == img_id),
            "근거발췌": "본문에 이미지 자리표시자만 있고 내용 텍스트는 없다",
        })
    figure.sort(key=lambda x: (x["줄"], x["종류"]))

    has_text_table = any(RE_TEXT_TABLE.search(l) for l in block_lines)
    doc_table = [m for m in table if m["종류"] != "외부별표"]
    plain_figure = [m for m in figure if m["종류"] != "이미지태그"]

    reasons = []
    if images["존재"]:
        reasons.append("이미지태그_내용부재")
    if doc_table and not images["존재"] and not has_text_table:
        reasons.append("표지시_대상부재")
    if plain_figure and not images["존재"]:
        reasons.append("그림지시_대상부재")

    return (
        {"존재": bool(table), "마커수": len(table), "마커": table},
        {"존재": bool(figure), "마커수": len(figure), "마커": figure},
        bool(reasons),
        reasons,
        has_text_table,
    )


def build_hang(heads, lines, chapters):
    """항 머리와 다음 경계 사이를 본문으로 묶는다."""
    boundaries = set(h["줄"] for h in heads)
    for ch in chapters:
        boundaries.add(ch["원문줄"])
    section_lines = set()
    for idx, line in enumerate(lines, start=1):
        if RE_SECTION.match(line) and RE_CHAPTER.match(line) is None:
            section_lines.add(idx)
    boundaries |= section_lines

    ordered = sorted(boundaries)
    result = []
    line_owner = {}
    trailing_blank = []
    for head in heads:
        start = head["줄"]
        nxt = next((b for b in ordered if b > start), len(lines) + 1)
        end = nxt - 1
        # 장 사이 구분용 공백줄은 본문에 넣지 않는다.
        while end > start and not lines[end - 1].strip():
            trailing_blank.append(end)
            end -= 1
        block_lines = lines[start - 1:end]
        for ln in range(start, end + 1):
            line_owner[ln] = head["번호"]
        parts = head["번호"].split("-")
        level = len(parts)
        parent = "-".join(parts[:-1]) if level == 4 else None
        body_text = "\n".join(block_lines)
        # 번호표기는 번호 첫자리부터 재므로 줄 앞 들여쓰기만큼 어긋난다.
        # 잔여 본문은 매치 끝 열에서 자른다.
        residue = block_lines[0][head["머리끝_열"]:].strip()
        images = detect_images(block_lines)
        tbl, fig, lost, lost_reasons, has_text_table = detect_channels(
            block_lines, start, images)
        result.append({
            "항번호": head["번호"],
            "항번호_레벨": level,
            "번호표기_원문": head["번호표기"],
            "번호접두_상위항": parent,
            "장번호": head["장"]["장번호"],
            "장제목": head["장"]["장제목"],
            "절번호": head["절"]["절번호"] if head["절"] else None,
            "절제목": head["절"]["절제목"] if head["절"] else None,
            "본문": body_text,
            "원문줄범위": {"시작": start, "끝": end},
            "본문_줄수": len(block_lines),
            "본문_문자수": len(body_text),
            "하위목": detect_items(block_lines[1:]),
            "별표별첨참조": detect_refs(body_text),
            "삽입이미지": images,
            "표참조": tbl,
            "그림참조": fig,
            "텍스트표_흔적": has_text_table,
            "유실의심": lost,
            "유실의심_사유": lost_reasons,
            "삭제표기": residue in ("삭제", "삭제.") and len(block_lines) == 1,
        })
    return result, line_owner, sorted(trailing_blank)


def summarize_channels(hangs, lines, rec):
    """표·그림 채널을 집계하고 문서 메타의 appendix_count 와 대조한다."""
    tbl_kind, fig_kind, reason_kind = Counter(), Counter(), Counter()
    for h in hangs:
        for m in h["표참조"]["마커"]:
            tbl_kind[m["종류"]] += 1
        for m in h["그림참조"]["마커"]:
            fig_kind[m["종류"]] += 1
        for r in h["유실의심_사유"]:
            reason_kind[r] += 1

    rejected = []
    for idx, line in enumerate(lines, start=1):
        for m in RE_FIGURE_REJECT.finditer(line):
            rejected.append({
                "줄": idx,
                "표기": m.group(0),
                "근거발췌": line[max(0, m.start() - 35):m.end() + 35].strip(),
                "기각사유": "계획 수립자가 작성할 도면 지시이고 이 문서 안의 "
                        "그림을 가리키지 않는다",
            })

    attach = rec.get("attachments") or []
    names = [n for a in attach for n in (a.get("name") or "").split("\n") if n]
    return {
        "표참조_항수": sum(1 for h in hangs if h["표참조"]["존재"]),
        "표참조_마커수": sum(h["표참조"]["마커수"] for h in hangs),
        "표참조_종류별": dict(tbl_kind),
        "그림참조_항수": sum(1 for h in hangs if h["그림참조"]["존재"]),
        "그림참조_마커수": sum(h["그림참조"]["마커수"] for h in hangs),
        "그림참조_종류별": dict(fig_kind),
        "유실의심_항수": sum(1 for h in hangs if h["유실의심"]),
        "유실의심_사유별": dict(reason_kind),
        "이미지태그_총수": sum(len(h["삽입이미지"]["이미지id"]) for h in hangs),
        "텍스트표_흔적_줄수": sum(1 for l in lines if RE_TEXT_TABLE.search(l)),
        "그림후보_기각": rejected,
        "appendix_count_대조": {
            "메타값": rec.get("appendix_count"),
            "첨부파일명": names,
            "판정": "appendix_count 는 별표·별첨의 수가 아니라 상세화면에 붙은 "
                  "다운로드 파일 수다. 4건은 전문 hwpx·전문 pdf·신구조문대비표·"
                  "제개정이유서이며 본문이 참조하는 별첨1·별첨2 문서와 대응하지 "
                  "않는다. 표·그림 유실의 분모로 쓸 수 없다",
            "유실_분모로_사용": False,
        },
        "문서수준_유실": {
            "별첨_참조": sorted({m["표기"] for h in hangs
                              for m in h["별표별첨참조"]["참조"]
                              if m["종류"] == "별첨"}),
            "별첨_본문_수록": False,
            "설명": "본문이 별첨1(계획도서·계획설명서 작성기준)과 별첨2(동의서 "
                  "서식)를 참조하나 별첨 본문은 이 전문 텍스트에 없다. 항 단위 "
                  "유실의심과 별개인 문서수준 결락이다",
        },
        "채널_판정근거": {
            "텍스트표_부재": "본문 전 줄에 탭·파이프 구분자가 0줄이다. 이 문서의 "
                        "표는 전부 이미지로만 있고 텍스트 표는 없다",
            "표마커_협소화": "맨 `표` 로 찾으면 22건 중 19건이 목표·표고·지표층·"
                        "안내표지판 같은 부분문자열 오탐이다. 조사·어깨번호를 "
                        "요구해 3건으로 좁혔고 전건을 원문에서 확인했다",
            "그림마커_실측": "`그림`·`<그림>`·`예시도`·`모식도` 표기는 이 문서에 "
                        "0건이다. 그림의 유일한 실물 표지는 `<img id>` 태그다",
        },
    }


def continuity(hangs):
    """같은 부모 아래에서 마지막 자리가 1부터 연속하는지 본다."""
    groups = {}
    for h in hangs:
        parts = h["항번호"].split("-")
        key = "-".join(parts[:-1])
        groups.setdefault(key, []).append((int(parts[-1]), h["항번호"],
                                           h["원문줄범위"]["시작"]))
    report = []
    for key in sorted(groups, key=lambda k: [int(x) for x in k.split("-")]):
        seq = groups[key]
        nums = [s[0] for s in seq]
        counts = Counter(nums)
        dup = sorted(n for n, c in counts.items() if c > 1)
        gap = [n for n in range(1, max(nums) + 1) if n not in counts]
        report.append({
            "그룹": key,
            "그룹_레벨": len(key.split("-")) + 1,
            "항수": len(nums),
            "관측번호": nums,
            "1부터_연속": nums == list(range(1, len(nums) + 1)),
            "결번": gap,
            "중복": dup,
        })
    return report


def build(corpus_path, out_dir, document_key=DOCUMENT_KEY):
    rec, text = load_text(corpus_path, document_key)
    lines = text.split("\n")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    chapters, sections, heads = scan(lines)
    hangs, line_owner, struct_blank = build_hang(heads, lines, chapters)

    # 커버리지. 장·절 표제줄과 장 구분 공백줄은 분모에서 뺀다.
    chapter_lines = {c["원문줄"] for c in chapters}
    section_lines = {s["원문줄"] for s in sections}
    heading_lines = chapter_lines | section_lines
    struct_blank_set = set(struct_blank)
    unassigned = []
    for idx, line in enumerate(lines, start=1):
        if idx in heading_lines or idx in line_owner:
            continue
        unassigned.append({
            "줄": idx,
            "원문": line,
            "공백줄": not line.strip(),
            "장구분_공백줄": idx in struct_blank_set,
        })

    level_counts = Counter(h["항번호_레벨"] for h in hangs)
    strict_missed = [
        {"줄": h["원문줄범위"]["시작"], "항번호": h["항번호"],
         "번호표기_원문": h["번호표기_원문"], "레벨": h["항번호_레벨"]}
        for h in hangs
        if not RE_HANG_STRICT.match(lines[h["원문줄범위"]["시작"] - 1])
    ]
    branch = [{"줄": i, "원문": l} for i, l in enumerate(lines, start=1)
              if RE_BRANCH.match(l)]

    prefix_violation = []
    for h in hangs:
        parts = [int(x) for x in h["항번호"].split("-")]
        if parts[0] != h["장번호"]:
            prefix_violation.append({"항번호": h["항번호"], "장번호": h["장번호"],
                                     "사유": "첫자리가 장번호와 다르다"})
        elif h["절번호"] is None and len(parts) != 2:
            prefix_violation.append({"항번호": h["항번호"], "절번호": None,
                                     "사유": "절 없는 장인데 번호가 2단이 아니다"})
        elif h["절번호"] is not None and parts[1] != h["절번호"]:
            prefix_violation.append({"항번호": h["항번호"], "절번호": h["절번호"],
                                     "사유": "둘째자리가 절번호와 다르다"})

    sections_by_chapter = Counter(s["장번호"] for s in sections)
    hang_by_chapter = Counter(h["장번호"] for h in hangs)
    channel = summarize_channels(hangs, lines, rec)

    meta = {
        "생성근거": {
            "입력파일": str(corpus_path),
            "document_key": document_key,
            "official_name": rec.get("official_name"),
            "official_kind": rec.get("official_kind"),
            "official_id": rec.get("official_id"),
            "current_effective_date": rec.get("current_effective_date"),
            "parse_status": rec.get("parse_status"),
            "application_version_unresolved": rec.get(
                "application_version_unresolved"),
            "provision_index": 0,
            "전문_sha256": digest,
            "전문_문자수": len(text),
            "전문_줄수": len(lines),
        },
        "스크립트": SCRIPT_PATH,
        "정렬": "항목록은 원문 줄번호 오름차순. 같은 줄에 두 항이 오지 않는다",
        "모수": {
            "장": len(chapters),
            "절": len(sections),
            "항_전체": len(hangs),
            "항_2단": level_counts.get(2, 0),
            "항_3단": level_counts.get(3, 0),
            "항_4단": level_counts.get(4, 0),
            "절없는_장": sorted(c["장번호"] for c in chapters
                             if sections_by_chapter.get(c["장번호"], 0) == 0),
            "장별_절수": {str(c["장번호"]): sections_by_chapter.get(c["장번호"], 0)
                       for c in chapters},
            "장별_항수": {str(c["장번호"]): hang_by_chapter.get(c["장번호"], 0)
                       for c in chapters},
        },
        "판정규약": {
            "항": "원문 줄머리의 x-y[-z[-w]]. 번호로 시작하는 단위. 마침표 앞 공백 변형 허용",
            "본문": "항 머리줄부터 다음 항·절·장 표제 직전까지의 원문 줄 그대로. 장 구분 공백줄만 제외",
            "하위목": "본문 줄머리의 (n)·원숫자·가나다 표지 집계. 판정 필드이며 본문을 바꾸지 않는다",
            "별표별첨참조": "어깨번호를 요구하는 표기만 인정한다. 「특별지침」·「생물서식공간」 같은 부분문자열 오탐을 막기 위함",
            "번호접두_상위항": "4단 번호의 앞 3자리. 번호 표기상의 접두 관계이며 법적 종속 판정이 아니다",
            "표참조": "조사 또는 어깨번호를 요구하는 표기만 인정한다. 맨 `표` 로 찾으면 22건 중 19건이 오탐이다",
            "그림참조": "`그림` 류 지시 표기와 `<img id>` 자리표시자. 도해명칭(구상도 등)은 작성 지시라 넣지 않는다",
            "유실의심": "참조 대상의 실물이 본문 텍스트에 없다는 관측이다. 원본 hwpx·pdf 에 있는지는 판정하지 않았다",
        },
        "채널요약": channel,
    }

    structure = {
        "meta": meta,
        "장목록": [
            {"장번호": c["장번호"], "장제목": c["장제목"],
             "장제목_원문표기": c["장제목_원문표기"], "원문줄": c["원문줄"],
             "절수": sections_by_chapter.get(c["장번호"], 0),
             "항수": hang_by_chapter.get(c["장번호"], 0)}
            for c in chapters
        ],
        "절목록": [
            {"절키": f"{s['장번호']}-{s['절번호']}", "장번호": s["장번호"],
             "절번호": s["절번호"], "절제목": s["절제목"],
             "절제목_원문표기": s["절제목_원문표기"], "원문줄": s["원문줄"],
             "항수": sum(1 for h in hangs
                        if h["장번호"] == s["장번호"] and h["절번호"] == s["절번호"])}
            for s in sections
        ],
        "항목록": hangs,
    }

    cont = continuity(hangs)
    content_unassigned = [u for u in unassigned if not u["공백줄"]]
    report = {
        "meta": {
            "생성근거": meta["생성근거"],
            "스크립트": SCRIPT_PATH,
            "대상산출물": "output/legal/statute/수립지침_항구조.json",
        },
        "라인커버리지": {
            "전체줄수": len(lines),
            "장표제줄": len(chapter_lines),
            "절표제줄": len(section_lines),
            "분모_표제제외": len(lines) - len(heading_lines),
            "항배정줄": len(line_owner),
            "미배정줄": len(unassigned),
            "미배정_내용줄": len(content_unassigned),
            "미배정_공백줄": len(unassigned) - len(content_unassigned),
            "배정률_표제제외": round(
                len(line_owner) / (len(lines) - len(heading_lines)), 6),
            "배정률_공백까지제외": round(
                len(line_owner) /
                (len(lines) - len(heading_lines) -
                 (len(unassigned) - len(content_unassigned))), 6)
            if len(lines) - len(heading_lines) -
            (len(unassigned) - len(content_unassigned)) else None,
            "미배정줄목록": unassigned,
        },
        "번호연속성": {
            "그룹수": len(cont),
            "위반그룹수": sum(1 for g in cont
                          if not g["1부터_연속"] or g["결번"] or g["중복"]),
            "결번목록": [{"그룹": g["그룹"], "결번": g["결번"]}
                     for g in cont if g["결번"]],
            "중복목록": [{"그룹": g["그룹"], "중복": g["중복"]}
                     for g in cont if g["중복"]],
            "그룹별": cont,
        },
        "채널요약": channel,
        "유실의심_항목록": [
            {"항번호": h["항번호"], "장번호": h["장번호"], "절번호": h["절번호"],
             "사유": h["유실의심_사유"],
             "이미지id": h["삽입이미지"]["이미지id"],
             "표참조": [m["표기"] for m in h["표참조"]["마커"]],
             "머리줄": h["원문줄범위"]["시작"]}
            for h in hangs if h["유실의심"]
        ],
        "가지번호": {"건수": len(branch), "목록": branch,
                 "탐지식": RE_BRANCH.pattern},
        "접두일치": {
            "검사대상": len(hangs),
            "위반": len(prefix_violation),
            "위반목록": prefix_violation,
        },
        "표준정규식_미포착": {
            "표준정규식": RE_HANG_STRICT.pattern,
            "표준정규식_포착": len(hangs) - len(strict_missed),
            "미포착": len(strict_missed),
            "미포착목록": strict_missed,
        },
        "격리": [],
    }
    return structure, report, lines


def add_isolations(report, structure, lines):
    """판정하지 못한 것만 격리에 남긴다. 판정한 것은 넣지 않는다."""
    iso = []

    dup_line = None
    for idx, line in enumerate(lines, start=1):
        if line.count("또한, 동의서 서식은 별첨2를 참고하여") > 1:
            dup_line = idx
            break
    if dup_line:
        owner = next((h["항번호"] for h in structure["항목록"]
                      if h["원문줄범위"]["시작"] <= dup_line
                      <= h["원문줄범위"]["끝"]), None)
        iso.append({
            "대상": f"원문 {dup_line}줄 (항 {owner} 본문)",
            "사유": "같은 문장이 한 줄 안에서 두 번 나온다. 법제처 원문의 중복인지 "
                  "corpus 수집·변환 단계의 중복인지 이 저장소 자산만으로는 가릴 수 "
                  "없다. 원응답 캐시가 Git 밖이라 대조할 수 없었다",
            "처리": "본문을 원문 그대로 두었다. 중복 제거·교정을 하지 않았다",
        })

    four = [h["항번호"] for h in structure["항목록"] if h["항번호_레벨"] == 4]
    if four:
        iso.append({
            "대상": f"4단 번호 {len(four)}건: {', '.join(four)}",
            "사유": "번호가 4단이라 3단 항의 하위 단위인지 3단 항과 대등한 단위인지 "
                  "번호만으로는 정해지지 않는다. 본문에서 '3-2-2. 및 3-2-2-1.에 따라' "
                  "처럼 3단과 나란히 인용되나, 이는 인용 관행이지 층위 확정 근거가 아니다",
            "처리": "항목록에 담되 `항번호_레벨: 4` 와 `번호접두_상위항` 으로 표시만 "
                  "했다. 층위 판정 필드는 두지 않았다",
        })

    report["격리"] = iso
    report["격리건수"] = len(iso)
    return report


def write_json(path, payload):
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
        + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--document-key", default=DOCUMENT_KEY)
    a = ap.parse_args()

    corpus = Path(a.corpus)
    if not corpus.exists():
        print(f"입력 없음: {corpus}", file=sys.stderr)
        return 1

    structure, report, lines = build(corpus, a.out_dir, a.document_key)
    report = add_isolations(report, structure, lines)

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "수립지침_항구조.json", structure)
    write_json(out_dir / "_수립지침_파싱_리포트.json", report)

    m = structure["meta"]["모수"]
    cov = report["라인커버리지"]
    print(f"장 {m['장']} / 절 {m['절']} / 항 {m['항_전체']} "
          f"(2단 {m['항_2단']}, 3단 {m['항_3단']}, 4단 {m['항_4단']})")
    print(f"커버리지: 미배정 {cov['미배정줄']}줄 "
          f"(내용 {cov['미배정_내용줄']}, 공백 {cov['미배정_공백줄']}) "
          f"/ 표제제외 분모 {cov['분모_표제제외']}")
    print(f"연속성 위반그룹 {report['번호연속성']['위반그룹수']} / "
          f"가지번호 {report['가지번호']['건수']} / "
          f"접두 위반 {report['접두일치']['위반']} / "
          f"격리 {report['격리건수']}")
    ch = report["채널요약"]
    print(f"채널: 표참조 {ch['표참조_항수']}항({ch['표참조_마커수']}건) / "
          f"그림참조 {ch['그림참조_항수']}항({ch['그림참조_마커수']}건) / "
          f"유실의심 {ch['유실의심_항수']}항 {ch['유실의심_사유별']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
