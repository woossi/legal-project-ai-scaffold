#!/usr/bin/env python3
"""규범값 근거 계통의 조문 본문을 법제처 OPEN API 에서 수집한다.

`norm_basis.json` 은 조문의 **요지**만 담는다. 요지는 사람이 요약한 값이라
파생값이고, 조문 본문이 정본이다. 둘을 같은 필드에 섞지 않는다
(kb-plan 절대규칙 1). 이 스크립트가 본문을 별도 산출물로 받아 온다.

  조문본문  /DRF/lawService.do?target=law&MST=…&JO=005600
            JO 는 조문번호 4자리 + 가지번호 2자리다. 제56조 → 005600,
            제27조의2 → 002702

입력  output/legal/statute/norm_basis.json
      output/legal/statute/statute_master.json
출력  output/legal/statute/article_master.json
"""

import argparse
import collections
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import statute_common as sc  # noqa: E402

SVC = "https://www.law.go.kr/DRF/lawService.do"
ART = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")


def jo_code(article):
    """`제56조` → `005600`, `제27조의2` → `002702`. 실패 시 None."""
    m = ART.search(article or "")
    if not m:
        return None
    return f"{int(m.group(1)):04d}{int(m.group(2) or 0):02d}"


def mst_of(node):
    """statute_master 의 출처URL 에서 MST 를 뽑는다."""
    m = re.search(r"MST=(\d+)", node.get("출처URL") or "")
    return m.group(1) if m else None


def fetch(mst, jo, oc, target="law", timeout=30, retries=2):
    q = urllib.parse.urlencode(
        {"OC": oc, "target": target, "type": "XML", "MST": mst, "JO": jo}
    )
    for i in range(retries + 1):
        try:
            with urllib.request.urlopen(f"{SVC}?{q}", timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if i == retries:
                return f"__ERROR__{e}"
            time.sleep(1.5 * (i + 1))
    return "__ERROR__unreachable"


# 조문 본문은 한 태그에 있지 않다. 항이 있는 조문은 `조문내용` 에 제목만 오고
# 본문이 `항내용`·`호내용`·`목내용` 으로 쪼개진다(국토계획법 시행령 제84조는
# 항 9개). XML 등장 순서대로 모아 붙인다.
BODY_TAG = re.compile(
    r"<(조문내용|항내용|호내용|목내용)>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</\1>", re.S
)


def body_text(xml):
    parts = [re.sub(r"\s+", " ", v).strip() for _t, v in BODY_TAG.findall(xml)]
    return "\n".join(p for p in parts if p)


def build(nb_path, master_path, out_path, oc, sleep):
    NB = json.loads(Path(nb_path).read_text(encoding="utf-8"))
    M = json.loads(Path(master_path).read_text(encoding="utf-8"))
    by_key = {s["statute_key"]: s for s in M["statutes"]}

    rows, stat = [], collections.Counter()
    for a in NB["articles"]:
        node = by_key.get(a["statute_key"])
        mst, jo = (mst_of(node) if node else None), jo_code(a["조문"])
        row = {
            "article_id": a["article_id"],
            "statute_key": a["statute_key"],
            "정식명칭": (node or {}).get("정식명칭", ""),
            "조문": a["조문"],
            "제목": a.get("제목", ""),
            "JO": jo,
            "조문본문": "",
            "본문_시행일자": None,
            "본문_공포번호": "",
            "출처URL": "",
            "항수": 0,
            "수집상태": "미수집",
        }
        if not (mst and jo):
            row["수집상태"] = "MST없음" if not mst else "조문번호파싱실패"
            stat[row["수집상태"]] += 1
            rows.append(row)
            print(f"  {a['article_id']} {a['조문']:12s} {row['수집상태']}")
            time.sleep(0)
            continue

        xml = fetch(mst, jo, oc)
        if xml.startswith("__ERROR__"):
            row["수집상태"] = "API오류"
        else:
            body = body_text(xml)
            if body:
                row["조문본문"] = body
                row["항수"] = len(re.findall(r"<항내용>", xml))
                row["본문_시행일자"] = sc.iso_yyyymmdd(
                    sc.xml_tag_text(xml, "시행일자"))
                row["본문_공포번호"] = sc.xml_tag_text(xml, "공포번호")
                row["출처URL"] = (f"{SVC}?OC={oc}&target=law&MST={mst}"
                                  f"&type=HTML&JO={jo}")
                row["수집상태"] = "수집"
            else:
                row["수집상태"] = "조문없음"
        stat[row["수집상태"]] += 1
        rows.append(row)
        print(f"  {a['article_id']} {a['조문']:12s} {row['수집상태']}"
              f"  {row['조문본문'][:46]}")
        time.sleep(sleep)

    out = {
        "meta": {
            "생성근거": "법제처 OPEN API lawService.do 에서 규범값 계통 조문 본문을 조회",
            "수집일": time.strftime("%Y-%m-%d"),
            "조문수": len(rows),
            "수집상태분포": dict(stat),
            "원칙": (
                "조문본문은 API 응답 원문이다. norm_basis.json 의 요지는 사람이 요약한 "
                "파생값이므로 같은 필드에 섞지 않는다"
            ),
            "JO규약": "조문번호 4자리 + 가지번호 2자리. 제56조 → 005600, 제27조의2 → 002702",
            "시행일_주의": (
                "본문_시행일자는 수집일 기준 현행 조문의 것이다. 시행지침이 인용한 "
                "시점의 조문과 다를 수 있다"
            ),
            "스크립트": ".claude/skills/legal/legal-statute/scripts/collect_articles.py",
        },
        "articles": rows,
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--norm-basis", default="output/legal/statute/norm_basis.json")
    ap.add_argument("--master", default="output/legal/statute/statute_master.json")
    ap.add_argument("--out", default="output/legal/statute/article_master.json")
    ap.add_argument("--oc", default="test")
    ap.add_argument("--sleep", type=float, default=0.4)
    a = ap.parse_args()
    for p in (a.norm_basis, a.master):
        if not Path(p).exists():
            print(f"입력 없음: {p}", file=sys.stderr)
            return 1
    out = build(a.norm_basis, a.master, a.out, a.oc, a.sleep)
    print(f"\n조문 {out['meta']['조문수']}개 → {a.out}")
    print(f"수집상태: {out['meta']['수집상태분포']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
