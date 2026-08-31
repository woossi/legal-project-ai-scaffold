#!/usr/bin/env python3
"""법제처 OPEN API 에서 법령 정본을 수집한다.

`statute_citations.json` 은 시행지침이 무엇을 인용했는지만 안다. 그 인용이
가리키는 법령의 **정식 명칭·법령ID·시행일·소관**은 문서 밖에 있고, 사례 문서의
표기에서 추론하면 안 된다(실측 표기는 조판 변이와 약칭이 섞여 있다).
국가법령정보센터가 정본이므로 여기서 직접 받는다.

  법령   https://www.law.go.kr/DRF/lawSearch.do?target=law&query=…
  자치법규 https://www.law.go.kr/DRF/lawSearch.do?target=ordin&query=…

실측 표기는 `실측표기` 필드에 격리하고 `정식명칭` 은 API 응답만 담는다.
매칭에 실패하면 `검증상태: 미대조` 로 두고 정식명칭을 비운다 — 못 찾은 것을
찾은 것처럼 만들지 않는다.

입력  output/legal/statute/statute_citations.json
출력  output/legal/statute/statute_master.json
      output/legal/statute/_collect_report.json
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

API = "https://www.law.go.kr/DRF/lawSearch.do"


def canon(s):
    return sc.strip_separators(s)


# 지자체 약칭 → 정식 행정구역명. 자치법규 검색은 정식명만 받는다
# (`서울시 건축조례` 는 totalCnt 0 이다).
GOV_ABBR = [
    ("서울특별시시", "서울특별시"),      # 실측 오타
    ("서울시", "서울특별시"),
    ("인천시", "인천광역시"),
    ("부산시", "부산광역시"),
    ("대구시", "대구광역시"),
    ("광주시광역", "광주광역시"),
    ("대전시", "대전광역시"),
    ("울산시", "울산광역시"),
    ("세종시", "세종특별자치시"),
]


def expand_gov(name):
    """지자체 약칭을 정식명으로 편다. 조례 조회 전에 적용한다."""
    for ab, full in GOV_ABBR:
        if name.startswith(ab):
            return full + name[len(ab):]
    return name


def fetch(target, query, oc, timeout=30, retries=2, display="100"):
    # 자치법규는 지자체 가나다순으로 대량 반환된다(`서울특별시 건축 조례` 는
    # 123건 중 뒤쪽). display 를 작게 두면 본청 조례를 놓친다.
    q = urllib.parse.urlencode(
        {"OC": oc, "target": target, "type": "XML", "query": query,
         "display": display}
    )
    url = f"{API}?{q}"
    for i in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:                       # 네트워크 실패는 재시도
            if i == retries:
                return f"__ERROR__{e}"
            time.sleep(1.5 * (i + 1))
    return "__ERROR__unreachable"


def parse_law(xml):
    out = []
    for b in re.findall(r"<law id=\"\d+\">(.*?)</law>", xml, re.S):
        out.append({
            "법령ID": sc.xml_tag_text(b, "법령ID"),
            "MST": sc.xml_tag_text(b, "법령일련번호"),
            "정식명칭": sc.xml_tag_text(b, "법령명한글"),
            "약칭": sc.xml_tag_text(b, "법령약칭명"),
            "법령구분": sc.xml_tag_text(b, "법령구분명"),
            "소관": sc.xml_tag_text(b, "소관부처명"),
            "공포일자": sc.xml_tag_text(b, "공포일자"),
            "공포번호": sc.xml_tag_text(b, "공포번호"),
            "시행일자": sc.xml_tag_text(b, "시행일자"),
            "현행연혁": sc.xml_tag_text(b, "현행연혁코드"),
        })
    return out


def parse_ordin(xml):
    out = []
    for b in re.findall(r"<law id=\"\d+\">(.*?)</law>", xml, re.S):
        out.append({
            "법령ID": sc.xml_tag_text(b, "자치법규ID"),
            "MST": sc.xml_tag_text(b, "자치법규일련번호"),
            "정식명칭": sc.xml_tag_text(b, "자치법규명"),
            "약칭": "",
            "법령구분": sc.xml_tag_text(b, "자치법규종류"),
            "소관": sc.xml_tag_text(b, "지자체기관명"),
            "공포일자": sc.xml_tag_text(b, "공포일자"),
            "공포번호": sc.xml_tag_text(b, "공포번호"),
            "시행일자": sc.xml_tag_text(b, "시행일자"),
            "현행연혁": "",
        })
    return out


def parse_admrul(xml):
    """행정규칙은 태그명이 다르고 공포 대신 발령을 쓴다."""
    out = []
    for b in re.findall(r"<admrul id=\"\d+\">(.*?)</admrul>", xml, re.S):
        발령 = sc.xml_tag_text(b, "발령일자")
        out.append({
            "법령ID": sc.xml_tag_text(b, "행정규칙ID"),
            "MST": sc.xml_tag_text(b, "행정규칙일련번호"),
            "정식명칭": sc.xml_tag_text(b, "행정규칙명"),
            "약칭": "",
            "법령구분": sc.xml_tag_text(b, "행정규칙종류"),
            "소관": sc.xml_tag_text(b, "소관부처명"),
            "공포일자": 발령,
            "공포번호": sc.xml_tag_text(b, "발령번호"),
            "시행일자": sc.xml_tag_text(b, "시행일자") or 발령,
            "현행연혁": sc.xml_tag_text(b, "현행연혁구분"),
        })
    return out


PARSERS = {"law": parse_law, "ordin": parse_ordin, "admrul": parse_admrul}


def targets_for(name):
    """표기 꼬리로 조회 대상을 정한다. 앞의 것부터 시도한다."""
    k = canon(name)
    if k.endswith("조례"):
        return ["ordin"]
    if k.endswith(("지침", "고시", "예규", "훈령", "기준")):
        return ["admrul", "law"]
    return ["law", "admrul"]


def pick(cands, want_key, renamed_to=None):
    """후보 중 실측 표기와 가장 잘 맞는 것을 고른다. 애매하면 None.

    renamed_to 는 `case/정본대조.json` 이 확인해 둔 개칭 후 명칭이다. 개칭된
    법령은 옛 명칭으로는 정확일치가 나지 않으므로 이 근거가 있을 때만 받는다.
    단일 후보라는 이유로 받으면 추측이 된다.
    """
    if not cands:
        return None, "결과없음"
    exact = [c for c in cands if canon(c["정식명칭"]) == want_key]
    if len(exact) == 1:
        return exact[0], "정확일치"
    if len(exact) > 1:
        cur = [c for c in exact if c["현행연혁"] == "현행"] or exact
        return cur[0], "정확일치(현행 선택)"
    ab = [c for c in cands if c["약칭"] and canon(c["약칭"]) == want_key]
    if len(ab) == 1:
        return ab[0], "약칭일치"
    if renamed_to:
        rn = [c for c in cands if canon(c["정식명칭"]) == canon(renamed_to)]
        if len(rn) == 1:
            return rn[0], f"명칭변천({renamed_to})"
    return None, f"불일치(후보 {len(cands)}건: {cands[0]['정식명칭'][:24]}…)"


def load_renames(seed_path):
    """옛 명칭 키 → 현행 정식명칭. case/정본대조.json 이 확인해 둔 것만 쓴다."""
    p = Path(seed_path)
    if not p.exists():
        return {}
    seed = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for s in seed.get("statutes", []):
        for old in s.get("명칭변천", []):
            if old.get("statute_key"):
                out[old["statute_key"]] = s["정식명칭"]
    return out


def load_corrections(seed_path):
    """실측 표기 키 → 조회에 쓸 교정 표기.

    오탈자(`주택건설기준에관한규정`)와 개칭(`문화재 보호법`)을 담는다. 교정은
    근거를 확인한 것만 seed 에 넣는다. 교정해도 정확일치가 나지 않으면 미대조로
    남으므로, 교정 자체가 결과를 만들어내지는 않는다.
    """
    p = Path(seed_path)
    if not p.exists():
        return {}
    seed = json.loads(p.read_text(encoding="utf-8"))
    # statute_key 는 중점·공백이 접힌 형태이므로 seed 키도 같은 폭으로 접는다.
    # 접지 않으면 `측량‧수로조사…` 처럼 중점을 쓴 항목이 매칭되지 않는다.
    return {canon(k): v["교정표기"] for k, v in seed.get("표기교정", {}).items()
            if isinstance(v, dict) and v.get("교정표기")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--citations", default="output/legal/statute/statute_citations.json")
    ap.add_argument("--out-dir", default="output/legal/statute")
    ap.add_argument("--oc", default="test", help="법제처 OPEN API 사용자 ID")
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seed",
                    default=".claude/skills/legal/legal-statute/case/정본대조.json",
                    help="명칭 변천이 확인된 법령 목록")
    a = ap.parse_args()

    src = Path(a.citations)
    if not src.exists():
        print(f"입력 없음: {src} — build_citations.py 를 먼저 실행한다", file=sys.stderr)
        return 1
    C = json.loads(src.read_text(encoding="utf-8"))

    use = collections.Counter()
    observed = {}
    terms_of = collections.defaultdict(set)
    dists_of = collections.defaultdict(set)
    arts_of = collections.defaultdict(collections.Counter)
    surf_of = collections.defaultdict(set)
    for c in C["citations"]:
        k = c["statute_key"]
        use[k] += 1
        observed.setdefault(k, c["statute_name"] or k)
        terms_of[k].add(c["term_id"])
        dists_of[k] |= set(c["districts"])
        surf_of[k].add(c["surface"])
        for art in c["articles"]:
            arts_of[k][art] += 1

    keys = sorted(use, key=lambda k: -use[k])
    if a.limit:
        keys = keys[: a.limit]

    renames = load_renames(a.seed)
    corrections = load_corrections(a.seed)
    nodes, report = [], []
    stat = collections.Counter()
    for i, k in enumerate(keys, 1):
        name = observed[k]
        query = expand_gov(corrections.get(k) or name)
        want = canon(query)
        hit, how, target, cands = None, "결과없음", None, []
        for tg in targets_for(name):
            xml = fetch(tg, query, a.oc)
            if xml.startswith("__ERROR__"):
                how = "API오류: " + xml[9:60]
                continue
            cs = PARSERS[tg](xml)
            h, w = pick(cs, want, renames.get(k))
            if h and corrections.get(k):
                w += f" ← 표기교정 {corrections[k]!r}"
            target, cands = tg, cs or cands
            if h:
                hit, how = h, w
                break
            how = w
        target = target or targets_for(name)[0]

        node = {
            "statute_key": k,
            "실측표기": name,
            "정식명칭": hit["정식명칭"] if hit else "",
            "약칭": hit["약칭"] if hit else "",
            "법령ID": hit["법령ID"] if hit else "",
            "법령구분": hit["법령구분"] if hit else "",
            "소관": hit["소관"] if hit else "",
            "공포번호": hit["공포번호"] if hit else "",
            "공포일자": sc.iso_yyyymmdd(hit["공포일자"]) if hit else None,
            "시행일자": sc.iso_yyyymmdd(hit["시행일자"]) if hit else None,
            "출처URL": (f"https://www.law.go.kr/DRF/lawService.do?OC={a.oc}"
                        f"&target={target}&MST={hit['MST']}&type=HTML") if hit else "",
            "검증상태": "정본대조" if hit else "미대조",
            "대조방법": how,
            "인용수": use[k],
            "인용용어수": len(terms_of[k]),
            "출현지구수": len(dists_of[k]),
            "인용조문": [x for x, _ in arts_of[k].most_common()],
            "표기변이수": len(surf_of[k]),
        }
        nodes.append(node)
        stat[node["검증상태"]] += 1
        if not hit:
            report.append({"statute_key": k, "실측표기": name, "사유": how,
                           "후보": [c["정식명칭"] for c in cands[:5]]})
        print(f"  [{i}/{len(keys)}] {name[:34]:36s} {node['검증상태']} ({how})")
        time.sleep(a.sleep)

    nodes.sort(key=lambda n: (-n["인용수"], n["statute_key"]))
    out = {
        "meta": {
            "생성근거": "법제처 OPEN API(국가법령정보센터)에서 법령 정본을 조회",
            "수집일": time.strftime("%Y-%m-%d"),
            "API": {"법령": f"{API}?target=law", "자치법규": f"{API}?target=ordin",
                    "OC": a.oc},
            "법령수": len(nodes),
            "검증상태분포": dict(stat),
            "원칙": (
                "정식명칭·법령ID·시행일·소관은 API 응답만 담는다. 사례 문서의 표기는 "
                "실측표기 필드에 격리한다. 매칭 실패 시 정식명칭을 비우고 미대조로 둔다"
            ),
            "시행일_주의": (
                "시행일자는 수집일 기준 현행 법령의 것이다. 시행지침이 인용한 시점의 "
                "법령과 다를 수 있다. 문서는 2002~2024년에 걸쳐 있다"
            ),
            "스크립트": ".claude/skills/legal/legal-statute/scripts/collect_statutes.py",
        },
        "statutes": nodes,
    }
    od = Path(a.out_dir)
    od.mkdir(parents=True, exist_ok=True)
    (od / "statute_master.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (od / "_collect_report.json").write_text(
        json.dumps({"meta": {"미대조수": len(report)}, "unmatched": report},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n법령 {len(nodes)}종 → {od}/statute_master.json")
    print(f"검증상태: {dict(stat)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
