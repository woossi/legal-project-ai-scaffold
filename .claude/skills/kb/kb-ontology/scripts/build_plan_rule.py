#!/usr/bin/env python3
"""계획규정 발급기 — norm_건축계획지표.csv 를 lp:PlanningRule 로 물화한다.

지구는 문서의 단위이고 획지는 규범 적용의 단위다. csv 한 행이 규정 하나다.

출력: output/kb/graph/det/plan-rule.ttl, output/kb/reports/_plan_rule.json
설계: docs/adr/0015-planningrule-스키마.md

리포트는 2계열을 `사유` 로 가른다 — 섞으면 게이트 14 의 등식이 깨진다.

  행격리        발급하지 못한 행. csv 행수 = 발급 rule 수 + 행격리 수
  결손·미성립    rule 은 유효하게 발급됐고 딸린 사실만 없다
                (파싱실패사유·시점근거_미확보·적용판본_미보유·용도지역_매핑_미발급)
"""
import argparse
import collections
import csv
import glob
import json
import os
import sys

import rdflib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mint_iri as M                                          # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
CSV = os.path.join(ROOT, "output/legal/table/norm_건축계획지표.csv")
KB = os.path.join(ROOT, "output/kb")
# 값 도메인 정본은 kb 가 아니라 산출물 소유 스킬의 계약이다 — 복제하지 않고 읽는다.
TABLECSV_CONTRACT = os.path.join(
    ROOT, ".claude/skills/legal/legal-tablecsv/contract/outputs.json")
LOT_ZONE = os.path.join(
    ROOT, "output/legal/lot-zone-audit/용지블록_용도지역_매핑가능성_조사.json")

LP = rdflib.Namespace("https://w3id.org/lp/ont#")

# 지표명 → 규범축 IRI. 문자열 축("일조 높이제한" 등)과 이름이 비슷해도 병합하지 않는다 —
# lp:높이 는 법 제52조제1항제4호이고 일조 높이제한은 건축법 제61조 위임이라 다른 규범이다.
AXIS_LOCAL = {"건폐율": "건폐율", "용적률": "용적률", "층수": "층수",
              "높이": "높이", "부지면적": "부지면적", "세대수": "세대수"}


def value_domains():
    with open(TABLECSV_CONTRACT, encoding="utf-8") as f:
        vd = json.load(f)["값도메인"]
    return set(vd["주체유형"]), set(vd["한도구분"]), set(vd["지표명"])


def anchors(kb):
    """근거문서·지구 앵커. 자기 산출물은 읽지 않는다 — 이전 실행이 다음 실행의
    입력이 되면 지운 rule 이 되살아나 멱등이 깨진다."""
    g = rdflib.Graph()
    for p in sorted(glob.glob(os.path.join(kb, "graph/det/*.ttl"))):
        if os.path.basename(p) == "plan-rule.ttl":
            continue
        g.parse(p, format="turtle")
    return (set(g.subjects(rdflib.RDF.type, LP.Guideline)),
            set(g.subjects(rdflib.RDF.type, LP.District)))


def classify(row, types, limits, indices, guidelines, districts):
    """행격리 사유를 정한다. 격리 대상이 아니면 None 이다."""
    no = (row.get("지구번호") or "").strip()
    if not M.DSTRC_RE.match(no):
        return "지구번호_형식위반"
    for c in ("주체", "지표명", "값_원문"):
        if not (row.get(c) or "").strip():
            return "필수필드_결측"
    if (row.get("주체유형") or "") not in types:
        return "주체유형_도메인이탈"
    if (row.get("한도구분") or "") not in limits:
        return "한도구분_도메인이탈"
    if row["지표명"] not in indices or row["지표명"] not in AXIS_LOCAL:
        return "지표명_도메인이탈"
    if rdflib.URIRef(M.guideline(no)) not in guidelines:
        return "근거문서_부재"
    if rdflib.URIRef(M.district(no)) not in districts:
        return "지구노드_부재"
    return None


def _sort_key(row):
    """동명순번의 정렬 키. 설계가 정한 (단서원문, 표ID) 가 앞이고, 나머지 컬럼은
    완전 동치인 행끼리도 순서를 고정하기 위한 tie-break 다 — 주체가 유일키가
    아니라서 (단서, 표ID) 가 같은 행이 실측 9,310건 있다."""
    return tuple(row.get(c) or "" for c in
                 ("단서", "표ID", "값_원문", "값_수치", "단위", "주체유형",
                  "파싱실패사유", "추출경로", "품질등급"))


def assign(rows):
    """(지구·주체·지표·한도구분) 그룹 안에서 동명순번을 매긴다."""
    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r["지구번호"], r["주체"], r["지표명"], r["한도구분"])].append(r)
    out, over = [], []
    for key in sorted(groups):
        for i, r in enumerate(sorted(groups[key], key=_sort_key), start=1):
            if i > 999:
                over.append(r)
                continue
            out.append((M.plan_rule(key[0], key[1], key[2], key[3], i), r))
    return out, over


def ttl_escape(s):
    return (s.replace("\\", "\\\\").replace('"', '\\"')
             .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))


def rel(iri):
    return iri[len(M.ID):] if iri.startswith(M.ID) else iri


def serialize(pairs):
    out = [
        "@prefix lp:   <https://w3id.org/lp/ont#> .\n"
        "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
        "@base <https://w3id.org/lp/id/> .\n"
        "\n"
        "##  계획규정 — 획지·가구 단위 규범값. 원천은 output/legal/table/norm_건축계획지표.csv.\n"
        "##  docs/adr/0015-planningrule-스키마.md\n"
        "##  생성: build_plan_rule.py (정렬 순회 · 멱등). 손으로 고치지 않는다.\n"
        "##  rule 은 무시점이다 — 시점은 planState 의 hasRule 귀속이 정한다.\n"
        "\n"]
    for iri, r in pairs:
        no = r["지구번호"]
        lines = [f"<{rel(iri)}> a lp:PlanningRule ;",
                 f'    lp:적용대상원문 "{ttl_escape(r["주체"])}" ;',
                 f'    lp:적용대상유형 "{r["주체유형"]}" ;',
                 f'    lp:규범축 lp:{AXIS_LOCAL[r["지표명"]]} ;',
                 f'    lp:한도구분 "{r["한도구분"]}" ;',
                 f'    lp:sourceText "{ttl_escape(r["값_원문"])}" ;']
        if (r.get("값_수치") or "").strip():
            lines.append(f'    lp:규정값 "{r["값_수치"].strip()}"^^xsd:decimal ;')
        if (r.get("단위") or "").strip():
            lines.append(f'    lp:단위 "{ttl_escape(r["단위"])}" ;')
        if (r.get("단서") or "").strip():
            lines.append(f'    lp:단서원문 "{ttl_escape(r["단서"])}" ;')
        lines += [f"    lp:근거문서 <{rel(M.guideline(no))}> ;",
                  f"    lp:inDistrict <{rel(M.district(no))}> ;",
                  f'    lp:표ID "{ttl_escape(r["표ID"])}" ;',
                  f'    lp:추출경로 "{r["추출경로"]}" ;',
                  f'    lp:품질등급 "{r["품질등급"]}" .']
        out.append("\n".join(lines) + "\n\n")
    return "".join(out)


def _zone_mapping_issued():
    """획지↔용도지역 간선 스위치. 정본은 lot-zone 산출물이고 kb 는 읽기만 한다."""
    if not os.path.exists(LOT_ZONE):
        return False, "lot-zone 산출물 부재"
    with open(LOT_ZONE, encoding="utf-8") as f:
        d = json.load(f)
    issued = bool(d.get("mapping_issued"))
    return issued, f"mapping_issued={str(issued).lower()}"


def run(csv_path=CSV, kb=KB):
    types, limits, indices = value_domains()
    guidelines, districts = anchors(kb)

    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    keep, 격리 = [], []
    for r in rows:
        why = classify(r, types, limits, indices, guidelines, districts)
        if why is None:
            keep.append(r)
            continue
        격리.append({"사유": "행격리", "상세": why,
                    "지구번호": r.get("지구번호"), "주체": r.get("주체"),
                    "지표명": r.get("지표명"), "한도구분": r.get("한도구분"),
                    "표ID": r.get("표ID")})

    pairs, over = assign(keep)
    for r in over:
        격리.append({"사유": "행격리", "상세": "동명순번_한도초과",
                    "지구번호": r.get("지구번호"), "주체": r.get("주체"),
                    "지표명": r.get("지표명"), "한도구분": r.get("한도구분"),
                    "표ID": r.get("표ID")})

    # ---- 발급된 rule 의 결손·미성립. 행격리가 아니므로 게이트 14 등식에 들어가지 않는다.
    for iri, r in pairs:
        if not (r.get("값_수치") or "").strip():
            격리.append({"사유": "파싱실패사유",
                        "상세": (r.get("파싱실패사유") or "").strip() or "사유미기재",
                        "iri": rel(iri), "지구번호": r["지구번호"],
                        "값_원문": r["값_원문"]})
    issued, zone_detail = _zone_mapping_issued()
    for no in sorted({r["지구번호"] for _, r in pairs}):
        격리.append({"사유": "시점근거_미확보", "지구번호": no,
                    "상세": "LawApplication 미발급 — 이 발급기는 법령 연결을 만들지 않는다"})
        if not issued:
            격리.append({"사유": "용도지역_매핑_미발급", "지구번호": no,
                        "상세": zone_detail})

    ttl_path = os.path.join(kb, "graph/det/plan-rule.ttl")
    os.makedirs(os.path.dirname(ttl_path), exist_ok=True)
    with open(ttl_path, "w", encoding="utf-8") as f:
        f.write(serialize(pairs))

    n_iso = sum(1 for x in 격리 if x["사유"] == "행격리")
    report = {
        "생성스크립트": "scripts/build_plan_rule.py",
        "$comment": ("격리는 2계열이다 — 사유=행격리 만 게이트 14 의 등식에 들어가고, "
                     "나머지는 발급된 rule 의 결손·미성립 기록이다. "
                     "적용판본_미보유는 LawApplication 을 발급해야 판정 가능하므로 "
                     "이 발급기 범위에서 0건이다."),
        "csv행수": len(rows),
        "발급수": len(pairs),
        "행격리수": n_iso,
        "지구수": len({r["지구번호"] for _, r in pairs}),
        "행격리_사유분포": dict(sorted(collections.Counter(
            x["상세"] for x in 격리 if x["사유"] == "행격리").items())),
        "결손_사유분포": dict(sorted(collections.Counter(
            x["사유"] for x in 격리 if x["사유"] != "행격리").items())),
        "격리": 격리,
    }
    rep_path = os.path.join(kb, "reports", "_plan_rule.json")
    os.makedirs(os.path.dirname(rep_path), exist_ok=True)
    with open(rep_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=CSV)
    ap.add_argument("--kb", default=KB)
    args = ap.parse_args()
    r = run(args.csv, args.kb)
    print(f"csv {r['csv행수']:,} = rule {r['발급수']:,} + 행격리 {r['행격리수']:,}"
          f" · 지구 {r['지구수']}")
    if r["행격리_사유분포"]:
        print(f"행격리 {r['행격리_사유분포']}")
    print(f"결손 {r['결손_사유분포']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
