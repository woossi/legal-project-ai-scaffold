#!/usr/bin/env python3
"""md 본문에서 용어 정의 조항 구간을 잘라낸다.

정의 조항의 내용은 definiation.json 이 이미 정본으로 갖고 있다. 개별 문서를
다시 텍스트화할 때 이 구간을 빼면, 변환 품질이 나쁜 부분(글자 소실이 잦은
정의 나열부)을 통째로 우회하면서 남은 본문에 집중할 수 있다.

  --check  잘라낼 구간만 보고하고 파일은 건드리지 않는다 (기본)
  --apply  실제로 잘라내 출력 디렉터리에 쓴다

경계 판정:
  헤딩형  `#### 제5조 (용어의 정의)` → 다음 같은 수준 이하 헤딩 직전까지
  본문형  자체 줄 `(건축물의 배치에 관한 용어의 정의)` → 다음 표제 줄 또는
          헤딩 직전까지
"""

import argparse
import json
import re
import sys
from pathlib import Path

HEAD = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.S)

# definiation.json 과 같은 판정을 쓴다. 정본은 build_definitions.py 다.
ARTICLE_NO = re.compile(r"제\s*([0-9]+)\s*조")
DEF_PATTERNS = [
    re.compile(r"용어\s*(?:의\s*)?(?:정의|정리)"),
    re.compile(r"^\s*(?:제\s*\d+\s*조)?\s*[(<\[]?\s*정\s*의\s*[)>\]]?\s*$"),
    re.compile(r"용어에\s*관한\s*사항"),
    re.compile(r"에\s*관한\s*용어\s*[)>\]]?\s*$"),
]
# 구간을 여는 표제 줄.
#   ① 줄 전체가 괄호·꺾쇠로 감싼 표제      `(건축물의 배치에 관한 용어의 정의)`
#   ② `제N조 …` 로 시작하는 짧은 줄        `제 3 조  용어의 정의`
#   ③ 괄호 없는 짧은 표제 줄               `용어의 정의`, `1-1-5 용어의 정의`
# ③ 은 여는 조건으로만 쓴다. 닫는 조건으로도 쓰면 정의문 나열 중간의 짧은 줄
# (`단독주택용지` 등)에서 구간이 끊겨 커버리지가 오히려 떨어진다(실측 95%→81%).
OPEN_TITLE = re.compile(
    r"^\s*(?:"
    r"[(<\[].{2,60}[)>\]]"
    r"|제\s*\d+\s*조[^\n]{0,60}"
    r"|(?:[0-9]+(?:[-.][0-9]+)*\s+)?[^\n]{2,30}"
    r")\s*$"
)
# 구간을 닫는 표제 줄 — 괄호형·조번호형만. ③ 은 제외한다.
CLOSE_TITLE = re.compile(r"^\s*(?:[(<\[].{2,60}[)>\]]|제\s*\d+\s*조[^\n]{0,60})\s*$")
# 정의 조항의 하위 구분 표제 — 이 줄에서는 구간을 닫지 않는다.
# `<공통사항>` 처럼 주제만 적힌 꺾쇠 표제로 한정한다. 조번호가 붙은 표제는
# 다음 조가 시작된 것이므로 제외한다.
SUBSECTION = re.compile(r"^\s*<[^>]{2,40}>\s*$")
# 조 표제가 아니라 규정문·정의문인 줄을 배제한다.
# `직각배치와 관련한 용어의 정의는 다음과 같다.` 같은 도입문도 여기서 걸린다.
NOT_TITLE = re.compile(
    r"(말한다|말하며|말함|칭한다|의미한다|하여야|한다\.|있다\.|없다\.|같다\.|"
    r"라 함은|이라 함은|이란|다음과)"
)


def is_definition_title(s):
    return any(p.search(s) for p in DEF_PATTERNS)


def find_spans(lines):
    """잘라낼 (시작, 끝) 줄 인덱스 목록. 끝은 배타적."""
    spans = []
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        m = HEAD.match(line)
        if m and is_definition_title(m.group(2)):
            level = len(m.group(1))
            j = i + 1
            while j < n:
                m2 = HEAD.match(lines[j])
                if m2 and len(m2.group(1)) <= level:
                    break
                j += 1
            spans.append((i, j, "heading", m.group(2)))
            i = j
            continue

        if (
            not m
            and OPEN_TITLE.match(line)
            and is_definition_title(line)
            and not NOT_TITLE.search(line)
        ):
            j = i + 1
            while j < n:
                if HEAD.match(lines[j]):
                    break
                nxt = lines[j]
                if (
                    CLOSE_TITLE.match(nxt)
                    and not NOT_TITLE.search(nxt)
                    and nxt.strip()
                ):
                    # 다음 표제도 정의 조항이면 이어서 같은 구간으로 삼는다.
                    # `용어의 정의` 아래 `<공통사항>` `<가구 및 획지에 관한 용어>`
                    # 처럼 하위 구분이 딸려오는 문서가 있다(실측: 인천검단지구).
                    if is_definition_title(nxt) or SUBSECTION.match(nxt):
                        j += 1
                        continue
                    break
                j += 1
            spans.append((i, j, "body", line.strip()))
            i = j
            continue
        i += 1
    return spans


def process(path, out_dir, apply_):
    text = Path(path).read_text(encoding="utf-8")
    fm = FRONTMATTER.match(text)
    head = fm.group(0) if fm else ""
    body = text[len(head):]
    lines = body.split("\n")

    spans = find_spans(lines)
    cut = sum(e - s for s, e, *_ in spans)
    chars = sum(len("\n".join(lines[s:e])) for s, e, *_ in spans)

    if apply_ and spans:
        keep = []
        drop = {i for s, e, *_ in spans for i in range(s, e)}
        for i, l in enumerate(lines):
            if i not in drop:
                keep.append(l)
        marker = (
            "\n> [용어의 정의 조항 제외] "
            f"{len(spans)}개 구간 / {cut}행 — 정본은 output/legal/word/definiation.json\n"
        )
        out = head + marker + "\n".join(keep)
        op = Path(out_dir) / Path(path).relative_to(Path(path).parents[2])
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(out, encoding="utf-8")

    return {
        "file": str(path),
        "spans": len(spans),
        "lines_cut": cut,
        "chars_cut": chars,
        "lines_total": len(lines),
        "titles": [t for *_x, t in spans][:20],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md-root", default="output/legal/markdown")
    ap.add_argument("--out", default="output/legal/markdown_nodef")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", default="")
    a = ap.parse_args()

    files = sorted(Path(a.md_root).rglob("*.md"))
    if not files:
        print(f"md 없음: {a.md_root}", file=sys.stderr)
        return 1

    rows = [process(f, a.out, a.apply) for f in files]
    tot_l = sum(r["lines_cut"] for r in rows)
    tot_all = sum(r["lines_total"] for r in rows)
    tot_c = sum(r["chars_cut"] for r in rows)
    hit = sum(1 for r in rows if r["spans"])
    cut_ratio = tot_l / tot_all if tot_all else 0

    print(f"문서 {len(rows)}건 중 정의 조항 검출 {hit}건")
    print(f"제외 구간 {sum(r['spans'] for r in rows)}개 / {tot_l}행 "
          f"({cut_ratio * 100:.1f}%) / {tot_c:,}자")
    if a.apply:
        print(f"→ {a.out}")
    else:
        print("(--check 모드. 파일을 쓰지 않았다. 적용은 --apply)")

    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(
            json.dumps(
                {
                    "meta": {
                        "문서수": len(rows),
                        "정의조항_검출문서수": hit,
                        "제외구간수": sum(r["spans"] for r in rows),
                        "제외행수": tot_l,
                        "전체행수": tot_all,
                        "제외비율": round(cut_ratio, 4),
                        "제외문자수": tot_c,
                    },
                    "documents": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"리포트 → {a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
