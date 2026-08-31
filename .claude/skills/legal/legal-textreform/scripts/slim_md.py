#!/usr/bin/env python3
"""md 본문에서 정본이 다른 산출물에 있는 구간을 잘라내고 남은 텍스트를 압축한다.

  --check  잘라낼 구간만 보고하고 파일은 건드리지 않는다 (기본)
  --apply  실제로 잘라내 md 를 제자리에서 덮어쓴다

단계
  S1 용어 정의 조항  정본 output/legal/word/definiation.json
  S2 표 블록·마커    정본 output/legal/table/*.csv (예정)
  S3 목차 블록·목차 헤딩  정본 본문 헤딩 구조
  S4 페이지 푸터     정본 없음 (노이즈)
  S5 쪼개진 문장 병합·연속 빈줄 축약 (손실 없음)

삭제는 S1~S4 의 구간을 모아 한 번에 수행한다. 단계마다 지우면 인덱스가 흔들린다.
설계 정본은 docs/superpowers/specs/2026-08-05-md-절삭압축-design.md 다.
"""

import argparse
import json
import re
import sys
from pathlib import Path

FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.S)
HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
MULTISPACE = re.compile(r"\S {2,}\S")
ITEM = re.compile(r"^\s*(?:-\s*)?(?:[①-⑳]|\d+\.\s|[가-하]\.\s)")
CAPTION = re.compile(r"^\s*[\[<【]\s*(?:표|별표|서식)")
# 페이지 푸터는 양쪽 하이픈이 있는 형태로 한정한다. 숫자만 있는 행은
# 표 안의 수치 셀일 수 있다.
PAGE_FOOT = re.compile(r"^\s*-\s*\d{1,4}\s*-\s*$")
ENDING = re.compile(r"(?:[.!?:;]|다|음|함|것|임)\s*$")
MARKER_START = re.compile(
    r"^\s*(?:#{1,6}\s|[-*>]\s|\d+\.\s|[①-⑳]|[가-하]\.\s|[\[<【])")

SHORT_MAX = 12          # 단문 기준 (자)
MERGE_MIN = 30          # 문장 병합 최소 길이 (자)

# S1 은 strip_definitions.py 의 경계 판정을 그대로 쓴다. 두 곳이 어긋나면
# definiation.json 커버리지 실측(98.9%)이 깨진다.
sys.path.insert(0, str(Path(__file__).parent))
from strip_definitions import find_spans as _def_spans, is_definition_title   # noqa: E402

TOC_TITLE = re.compile(r"^#{0,6}\s*(?:목\s*차|차\s*례|CONTENTS)\s*$", re.I)
# 목차형 행 — 점선+페이지, 제목+페이지, (1)·1. 번호, 로마숫자, 제N편/장/절
TOC_LINE = re.compile(
    r"(?:[·⋯…\.]{3,}\s*\d{1,4}\s*$)"
    r"|(?:\S\s+\d{1,4}\s*$)"
    r"|(?:^\s*[\(（]?\d{1,2}[\)）\.]\s*\S)"
    r"|(?:^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ][\.\s])"
    r"|(?:^\s*제\s*\d+\s*[편장절]\s)")
# 헤딩 끝의 페이지번호·점선 — 목차 헤딩의 표지
TOC_TAIL = re.compile(r"(?:[·⋯…\.]{3,}\s*\d{1,4}|\s+\d{1,4})\s*$")
ORIGIN = re.compile(r"^<!--\s*원본:")

TOC_MISS_MAX = 6        # 목차형이 아닌 행이 이만큼 연속하면 목차 끝
TOC_SCAN_MAX = 400      # 목차 블록 탐색 상한 (행)

TABLE_MARKER = re.compile(r"^>\s*\[표\]\s*$")
FIGURE_MARKER = re.compile(r"^>\s*\[그림\]\s*$")
# 표 각주는 표의 일부다 — 표와 함께 삭제한다 (실측 69개).
NOTE = re.compile(r"^\s*(?:주\s*\d*\s*[\)\.]|※|\*|비고|자료|출처)")

DENSITY_MIN = 0.60      # 블록 밀도 게이트
BLOCK_MAX = 60          # 블록 상한 (행)

# 계약의 조문수 정의와 같은 식을 쓴다 — 두 곳이 어긋나면 계약이 깨진다.
JOMUN = re.compile(r"^#{1,6}\s*제\s*\d+\s*조", re.M)
# 조문항목 행 안의 규범 수치. 표 안 수치는 세지 않는다.
NORM_NUM = re.compile(r"(?:건폐율|용적률|층수|높이|세대수)[^\n]{0,20}?\d")
CUT_RATE_WARN = 0.60

# 정의 서술 특유의 어미. strip_definitions.py 의 NOT_TITLE 은 "표제가 아니라
# 문장이다"를 가르는 넓은 집합(하여야·한다.·있다. 등 일반 종결어미 포함)이라
# 정의문과 일반 규범 문장을 구분하지 못한다. 이 좁은 집합만 "그 줄이 실제로
# 용어를 정의하는 문장이다"로 본다 (실측: 이 기준으로 136개 중 70개가
# 정의문이 아닌 조문항목으로 갈렸다).
DEF_SENTENCE = re.compile(r"말한다|말하며|말함|칭한다|의미한다|라 함은|이라 함은|이란")


def is_multispace(line):
    return bool(MULTISPACE.search(line))


def is_short(line):
    return len(line.strip()) <= SHORT_MAX


def find_page_footers(lines):
    return [{"start": i, "end": i + 1, "종류": "페이지푸터", "단계": "S4"}
            for i, l in enumerate(lines) if PAGE_FOOT.match(l)]


def find_definition_spans(lines):
    # body 계열(무괄호 맨줄 표제)은 헤딩 마크업이 없어 종료 조건이 약하다 —
    # 원본구성 조각 끝까지 흘러 실질 규범 조항까지 삼킨다 (실측: 159구간
    # 평균 67.4행, 인천검단은 `용어의 정의` 하나가 395행을 삼켜 `행정사항`·
    # `본 지침은 고시일부터 효력을 발생한다.` 를 지웠다).
    # heading 계열만 쓴다 — 평균 24.7행으로 경계가 안정적이다.
    #
    # 표제(원문 헤딩 텍스트)는 반환값에 담지 않는다 — 계획의 Global Constraint
    # "잘라낸 구간의 내용은 보존하지 않는다. 위치·행수만 기록한다"를 어겨
    # 리포트에 원문이 그대로 실렸다(실측 724구간/14,492자, 소비처 없음).
    # Task 3 계획의 Produces 인터페이스는 `표제` 를 포함했었지만 이 계획
    # 원칙과 충돌해 뺀다.
    out = []
    for s, e, kind, _t in _def_spans(lines):
        if kind != "heading":
            continue
        m = HEAD.match(lines[s])
        # 표제가 종결형으로 끝나면 헤딩이 아니라 PDF 추출로 쪼개진 문장 꼬리다.
        # `## 제1장 제9조(...용어의 정의)' 및 ... 결정도를 따른다.` 같은 행이
        # `## ` 로 시작한다는 이유로 헤딩이 되고, 레벨2라 다음 레벨2 헤딩까지
        # 실제 조문을 통째로 삼킨다 (실측 14구간 / 조문 112개).
        if m and ENDING.search(m.group(2).strip()):
            continue
        out.append({"start": s, "end": e, "종류": "정의조항", "단계": "S1"})
    return out


def find_toc_blocks(lines):
    blocks = []
    n = len(lines)
    i = 0
    while i < n:
        if not TOC_TITLE.match(lines[i].strip()):
            i += 1
            continue
        j, miss, end = i + 1, 0, i + 1
        while j < n and j - i < TOC_SCAN_MAX:
            s = lines[j].strip()
            if ORIGIN.match(s) or TOC_TITLE.match(s):
                break
            if not s:
                j += 1
                continue
            if TOC_LINE.search(s):
                miss = 0
                end = j + 1
            else:
                miss += 1
                if miss >= TOC_MISS_MAX:
                    break
            j += 1
        if end > i + 1:
            blocks.append({"start": i, "end": end, "종류": "목차", "단계": "S3"})
            i = end
        else:
            i += 1
    return blocks


def find_toc_headings(lines):
    """같은 헤딩이 뒤에 또 나오고, 앞의 것이 페이지번호로 끝나면 목차 행이다."""
    seen = {}
    for i, l in enumerate(lines):
        m = HEAD.match(l)
        if m:
            key = re.sub(r"\s+", "", TOC_TAIL.sub("", m.group(2)))
            if key:
                seen.setdefault(key, []).append(i)
    out = []
    for idxs in seen.values():
        if len(idxs) < 2:
            continue
        first = idxs[0]
        if TOC_TAIL.search(lines[first]):
            out.append({"start": first, "end": first + 1,
                        "종류": "목차헤딩", "단계": "S3"})
    return sorted(out, key=lambda s: s["start"])


def find_table_markers(lines):
    # 그림 마커도 표 잔해와 함께 삭제하지만(둘 다 조판 잔해), 표수 계산은
    # 종류로 표마커만 골라 센다 — 계약(contract/frontmatter.json:28)의
    # 표 필드 정의가 `> [표]` 마커 수(A류)이지 그림을 포함하지 않는다.
    # 실측: 그림 마커 3,817개가 표수에 합산돼 171개 문서에서 표 필드가
    # 부풀었다(+5,821, 그중 그림 3,817). verify_contract.py 는 기록값이
    # 실측보다 작을 때만 위반으로 보는 하한 검증이라 부풀림을 못 잡는다.
    out = []
    for i, l in enumerate(lines):
        if TABLE_MARKER.match(l):
            out.append({"start": i, "end": i + 1, "종류": "표마커", "단계": "S2"})
        elif FIGURE_MARKER.match(l):
            out.append({"start": i, "end": i + 1, "종류": "그림마커", "단계": "S2"})
    return out


def find_table_blocks(lines):
    """표 잔해 블록. 여는 조건은 느슨하게, 밀도 게이트로 조인다."""
    blocks = []
    n = len(lines)
    i = 0
    while i < n:
        # 헤딩과 조문항목은 표를 여는 줄이 될 수 없다 — 닫는 조건과 대칭이다.
        # 조문 항목이 우연히 이중공백을 품으면 그 항목이 표로 오인돼 삭제된다
        # (실측: 195개 블록 / 1,033행). 표 중간의 `- ①` 셀은 닫는 조건에서
        # 그대로 다룬다 — 표 안의 셀도 `- ①` 로 시작할 수 있기 때문이다.
        opened = (not HEAD.match(lines[i]) and not ITEM.match(lines[i]) and (
            bool(CAPTION.match(lines[i])) or (
                is_multispace(lines[i]) and i + 1 < n
                and is_multispace(lines[i + 1]))))
        if not opened:
            i += 1
            continue

        j, blank, capped = i + 1, 0, False
        last = i                    # 마지막으로 내용이 있던 행
        while j < n:
            if j - i >= BLOCK_MAX:
                capped = True
                break
            s = lines[j]
            if not s.strip():
                blank += 1
                if blank >= 3:
                    break
                j += 1
                continue
            blank = 0
            if (HEAD.match(s) or CAPTION.match(s)
                    or TABLE_MARKER.match(s) or FIGURE_MARKER.match(s)):
                break
            # 표 안의 셀도 - ① 로 시작할 수 있다. 다중공백이 없을 때만 닫는다.
            if ITEM.match(s) and not is_multispace(s):
                break
            # 표 뒤에 빈 줄 없이 붙는 설명문은 표가 아니다. 앞쪽 표 데이터의
            # 높은 밀도가 상쇄해 밀도 게이트도 이를 취소하지 못한다
            # (실측 64개 블록에서 정의 조항·규범 문장이 삭제됐다).
            if (not is_multispace(s) and not is_short(s)
                    and ENDING.search(s.strip()) and not NOTE.match(s)):
                break
            last = j
            j += 1

        # 여는 줄 하나만 남으면 표가 아니다 — 캡션만 지우는 일이 없어야 한다.
        if last == i:
            i += 1
            continue

        # 블록 끝은 마지막 내용 행까지다. 뒤따르는 빈 줄을 삼키면 안 된다.
        j = last + 1
        body = [l for l in lines[i:j] if l.strip()]
        dense = sum(1 for l in body if is_multispace(l) or is_short(l))
        density = dense / len(body) if body else 0.0
        if density >= DENSITY_MIN:
            blocks.append({"start": i, "end": j, "종류": "표블록", "단계": "S2",
                           "밀도": round(density, 2), "상한도달": capped})
            i = j
        else:
            i += 1      # 취소 — 지우지 않고 다음 행부터 다시 본다
    return blocks


def drop_spans(lines, spans):
    drop = set()
    for s in spans:
        drop.update(range(s["start"], s["end"]))
    return [l for i, l in enumerate(lines) if i not in drop]


def merge_wrapped(lines):
    """PDF 추출로 쪼개진 문장을 한 행으로 되돌린다."""
    out = []
    for line in lines:
        s = line.rstrip()
        prev = out[-1] if out else ""
        if (out and s.strip()
                and len(prev.strip()) >= MERGE_MIN
                and not ENDING.search(prev.strip())
                and not MARKER_START.match(s)
                and not is_multispace(s)
                and not is_multispace(prev)):
            out[-1] = prev.rstrip() + " " + s.lstrip()
        else:
            out.append(s)
    return out


def collapse_blanks(lines):
    out, blank = [], 0
    for l in lines:
        if l.strip():
            blank = 0
            out.append(l)
        else:
            blank += 1
            if blank == 1:
                out.append("")
    return out


def count_jomun(body):
    return len(JOMUN.findall(body))


def count_norm_numbers(body):
    return sum(len(NORM_NUM.findall(l))
               for l in body.split("\n") if ITEM.match(l))


def expected_from_spans(before, spans):
    """S1(정의조항) 구간이 의도적으로 지우는 조문수·규범수치·글자수의
    기대 감소분 (exp_j, exp_n, exp_chars) 을 계산한다. gate() 의 세 축을
    한 곳에 모아 서로 다른 근거로 어긋나지 않게 한다. spans 의 start/end
    는 절삭 전 본문(frontmatter 제외) 줄 번호 기준이다.

    exp_j: 스팬 안에서 그 자신이 정의조항 표제인 조문 헤딩만 센다. 스팬
    안의 다른 조문까지 면제하면 스팬이 잘못 잡힐수록 게이트가 더
    조용해진다 — 안전망이 뒤집힌다.

    exp_n·exp_chars: exp_j 와 같은 모양으로, 스팬 전체가 아니라 스팬 안의
    각 조문항목(ITEM) 줄에 대해 그 줄 자체가 정의형 문장(DEF_SENTENCE)인
    경우에만 면제 대상으로 삼는다. 조문항목이 아닌 줄(표제·도면안내 등)은
    그대로 면제한다 — 정의조항의 정당한 서술이기 때문이다. 정의조항 스팬은
    여러 정의 항목을 나열하는데 그중 일부(예: "공개공지의 시설기준")는
    정의가 아니라 일반 규범 지시문이며, 스팬 전체를 면제하면 이런 비정의
    규범수치까지 조용히 사라진다(실측 136개 중 70개).
    """
    exp_j = exp_n = exp_chars = 0
    if not spans:
        return exp_j, exp_n, exp_chars
    fm = FRONTMATTER.match(before)
    head = fm.group(0) if fm else ""
    lines = before[len(head):].split("\n")
    for s in spans:
        if s.get("단계") != "S1":
            continue
        seg_lines = lines[s["start"]:s["end"]]
        exempt_lines = []
        for l in seg_lines:
            m = HEAD.match(l)
            if JOMUN.match(l) and m and is_definition_title(m.group(2)):
                exp_j += 1
            if ITEM.match(l):
                if DEF_SENTENCE.search(l):
                    exp_n += len(NORM_NUM.findall(l))
                    exempt_lines.append(l)
                # 정의형이 아닌 조문항목은 면제하지 않는다 — 실손실 가능성.
            else:
                exempt_lines.append(l)
        exp_chars += len("\n".join(exempt_lines))
    return exp_j, exp_n, exp_chars


def gate(before, after, spans=None):
    """위반 메시지 목록. 빈 목록이면 통과.

    spans 가 주어지면 S1(정의조항) 구간이 의도적으로 지우는 조문·규범수치·
    글자수를 기대 감소분으로 빼고 그 초과분만 위반으로 본다 — 계산은
    expected_from_spans() 가 한다. S1 은 정의 조항을 지우는데 정의 조항은
    `제N조` 헤딩이므로, 빼지 않으면 S1 이 작동한 문서가 전부 위반이 된다.
    """
    out = []
    exp_j, exp_n, exp_chars = expected_from_spans(before, spans)

    jb, ja = count_jomun(before), count_jomun(after)
    if jb - ja > exp_j:
        out.append(f"조문수 감소 {jb} → {ja}")
    nb, na = count_norm_numbers(before), count_norm_numbers(after)
    if nb - na > exp_n:
        out.append(f"조문항목 규범수치 감소 {nb} → {na}")
    denom = len(before) - exp_chars
    if denom > 0:
        rate = (len(before) - len(after) - exp_chars) / denom
        if rate > CUT_RATE_WARN:
            out.append(f"절삭률 {rate:.1%} > {CUT_RATE_WARN:.0%}")
    return out


def set_fm_field(head, key, value):
    """frontmatter 에 이미 있는 필드만 갱신한다. 없으면 만들지 않는다."""
    pat = re.compile(rf"^({re.escape(key)}:[ \t]*).*$", re.M)
    if pat.search(head):
        return pat.sub(lambda m: m.group(1) + str(value), head, count=1)
    return head


def slim(text):
    """(절삭·압축된 텍스트, 내역) 을 돌려준다.

    frontmatter 는 표·조문수 필드가 이미 있을 때만 갱신하고, 없는 필드는
    새로 만들지 않는다.
    """
    fm = FRONTMATTER.match(text)
    head = fm.group(0) if fm else ""
    body = text[len(head):]
    lines = body.split("\n")

    markers = find_table_markers(lines)
    tblocks = find_table_blocks(lines)
    spans = []
    spans += find_definition_spans(lines)
    spans += markers
    spans += tblocks
    spans += find_toc_blocks(lines)
    spans += find_toc_headings(lines)
    spans += find_page_footers(lines)
    spans.sort(key=lambda s: s["start"])

    kept = drop_spans(lines, spans)
    kept = merge_wrapped(kept)
    kept = collapse_blanks(kept)
    new_body = "\n".join(kept)

    table_markers = sum(1 for m in markers if m["종류"] == "표마커")
    rec = {
        "행수_before": len(lines),
        "행수_after": len(kept),
        "글자_before": len(body),
        "글자_after": len(new_body),
        "구간": spans,
        "표수": table_markers + len(tblocks),
        "상한도달수": sum(1 for b in tblocks if b["상한도달"]),
    }

    # 표수는 검출값이 0보다 클 때만 쓴다. 절삭이 끝난 md 를 다시 돌리면 검출값이
    # 0 이 되는데, 그때 0 으로 덮으면 멱등성이 깨진다.
    if rec["표수"] > 0:
        head = set_fm_field(head, "표", rec["표수"])
    head = set_fm_field(head, "조문수", count_jomun(new_body))
    return head + new_body, rec


def slim_to_fixpoint(text, max_pass=3):
    """수렴할 때까지(최대 max_pass 회) slim() 을 반복 적용한다.

    멱등성 위반의 두 원인 — merge_wrapped 가 표블록 밀도 재판정의 입력을
    바꾸는 것(30건), find_toc_headings 가 중복 헤딩 그룹당 1개만 지워
    병합 문서 수만큼 반복이 필요한 것(1건) — 모두 과소삭제 방향이라
    반복이 안전하다. 반환 rec 은 1회차의 before 와 최종 회차의 after 를
    합친다. `구간`은 1회차 것을 그대로 쓴다 — gate() 가 이 인덱스로 원본
    text(1회차 입력)를 슬라이스하므로, 이후 회차의 구간을 쓰면 어긋난다.

    frontmatter 의 표 필드도 1회차 검출값으로 고정한다. slim() 은 매 회차
    자신의 검출값으로 표 필드를 쓰므로, 2회차 이후 새로 표를 찾으면(위 30건
    원인과 같은 경로) 그 회차가 1회차보다 작은 값으로 덮어쓸 수 있다 —
    계약이 정한 "절삭 전 표 수" 는 1회차 값이지 중간 회차 값이 아니다.
    """
    fm = FRONTMATTER.match(text)
    orig_head = fm.group(0) if fm else ""

    out, rec = slim(text)
    first_rec = rec
    passes = 1
    while passes < max_pass:
        nxt, nrec = slim(out)
        if nxt == out:
            break
        out, rec = nxt, nrec
        passes += 1

    fm = FRONTMATTER.match(out)
    body = out[len(fm.group(0)):] if fm else out
    head = orig_head
    if first_rec["표수"] > 0:
        head = set_fm_field(head, "표", first_rec["표수"])
    head = set_fm_field(head, "조문수", count_jomun(body))
    out = head + body

    rec = dict(rec)
    rec["행수_before"] = first_rec["행수_before"]
    rec["글자_before"] = first_rec["글자_before"]
    rec["구간"] = first_rec["구간"]
    rec["표수"] = first_rec["표수"]
    rec["적용횟수"] = passes
    return out, rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md-root", default="output/legal/markdown")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", default="")
    a = ap.parse_args()

    files = sorted(Path(a.md_root).rglob("*.md"))
    if not files:
        print(f"md 없음: {a.md_root}", file=sys.stderr)
        return 1

    rows, blocked = [], 0
    for p in files:
        text = p.read_text(encoding="utf-8")
        out, rec = slim_to_fixpoint(text)
        rec["file"] = str(p)
        rec["게이트위반"] = gate(text, out, rec["구간"])
        if rec["게이트위반"]:
            blocked += 1
            rec["행수_after"] = rec["행수_before"]
            rec["글자_after"] = rec["글자_before"]
            rows.append(rec)
            continue
        rows.append(rec)
        if a.apply and out != text:
            p.write_text(out, encoding="utf-8")

    cb = sum(r["글자_before"] for r in rows)
    ca = sum(r["글자_after"] for r in rows)
    lb = sum(r["행수_before"] for r in rows)
    la = sum(r["행수_after"] for r in rows)
    line_reduction = (lb - la) / lb if lb else 0
    char_reduction = (cb - ca) / cb if cb else 0
    print(f"md {len(rows)}건")
    print(f"행  {lb:,} → {la:,} ({line_reduction * 100:.1f}% 감소)")
    print(f"글자 {cb:,} → {ca:,} ({char_reduction * 100:.1f}% 감소)")
    print(f"게이트 위반으로 건너뛴 문서 {blocked}건")
    if not a.apply:
        print("(--check 모드. 파일을 쓰지 않았다. 적용은 --apply)")

    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps({
            "meta": {"문서수": len(rows), "행_before": lb, "행_after": la,
                     "글자_before": cb, "글자_after": ca,
                     "글자감소율": round(char_reduction, 4),
                     "게이트위반문서수": blocked},
            "documents": rows,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"리포트 → {a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
