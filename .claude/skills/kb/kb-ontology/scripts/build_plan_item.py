#!/usr/bin/env python3
"""계획항목(L1) 축을 어휘와 결정론 그래프로 옮긴다.

계약 contract/ontology.json 의 planItemAxis 가 정본이다.

주의 (2026-08-25): 이 스크립트가 전제한 5축 필요요건 구조는 폐기됐다. 온톨로지를
3계층(사업·계획·공간)으로 재설계 중이며 output/kb/ 산출물은 삭제됐다. 재사용 전
.claude/rules/계획규범요소-틀.md 로 새 체계를 확인한다. 아래 상수표의 3-7 근거문
("제3호 법문의 '구획된 일단의 토지'가 가구·획지다")도 틀렸다 — 그것은 획지만이고
가구는 앞부분 "도로로 둘러싸인 일단의 지역"이다.

  절(제3장 19 · 제4장 7) → output/kb/ontology/vocab-plan-item.ttl  (SKOS)
  항(제3·4장)            → output/kb/graph/det/plan-item.ttl        (det)
  범위 밖·미확정          → output/kb/reports/_plan_item.json

기준서 본문을 손으로 옮기면 원천과 어긋나므로 절제목·항 본문은 전부 원천에서 읽는다.
사람이 정하는 것은 제52조 호 매핑 하나뿐이고, 그 표의 절키 집합이 원천 절 목록과
어긋나면 이 스크립트가 실패한다.

    python3 build_plan_item.py            # 세 산출물 갱신
    python3 build_plan_item.py --check    # 갱신 없이 어긋남만 확인
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mint_iri as M                                          # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
SOURCE = os.path.join(ROOT, "output/legal/statute/수립지침_항구조.json")
SLOT_MAP = os.path.join(
    ROOT, ".claude/skills/legal/legal-contrast/case/제52조-슬롯매핑.json")
OUT_VOCAB = os.path.join(ROOT, "output/kb/ontology/vocab-plan-item.ttl")
OUT_GRAPH = os.path.join(ROOT, "output/kb/graph/det/plan-item.ttl")
OUT_REPORT = os.path.join(ROOT, "output/kb/reports/_plan_item.json")

# 계획항목 축에 여는 장. 택지개발지구가 주거형이라 유형별 장 중 제4장만 연다.
# 값 도메인 정본은 계약 planItemAxis.vocab.conceptScope 다.
SCOPE_CHAPTERS = (3, 4)

# 절키 → (제52조제1항 호, 근거원, 근거문).
# 근거원 도메인은 계약 planItemAxis.제52조매핑.evidenceDomain 이다. 호법문·slot_exact 는
# 아래 verify_mapping() 이 legal-contrast 정본 파일과 기계 대조하고, 법정구조표는
# .claude/rules/지구단위계획-법정구조.md 의 대응표 행을 근거문에 적는다.
# slot_patterns 정규식은 쓰지 않는다 — 부분문자열로 표제를 호에 직결하면 오탐이 든다.
ART52 = {
    "3-3": ("1", "호법문", "제1호 법문에 절제목이 그대로 들어 있다"),
    "3-4": ("6", "호법문", "제6호 법문에 절제목이 그대로 들어 있다"),
    "3-5": ("2", "호법문", "제2호 법문에 절제목이 그대로 들어 있다"),
    "3-6": ("7", "호법문", "제7호 법문에 절제목이 그대로 들어 있다"),
    "3-7": ("3", "법정구조표", "대응표 '3 | 가구·획지 규모와 조성'. 제3호 법문의 '구획된 일단의 토지'가 가구·획지다"),
    "3-8": ("4", "slot_exact", "slot_exact 의 '건축물의용도' 항목"),
    "3-9": ("4", "법정구조표", "대응표 '4 | 건축물 용도·건폐율·용적률·높이'"),
    "3-10": ("5", "법정구조표", "대응표 '5 | 배치·형태·색채·건축선'"),
    "3-11": ("5", "법정구조표", "대응표 '5 | 배치·형태·색채·건축선'"),
    "3-16": ("6", "법정구조표", "대응표 '6 | 환경·경관'"),
    "4-2": ("2", "호법문", "제2호 법문에 절제목이 그대로 들어 있다"),
    "4-3": ("3", "법정구조표", "대응표 '3 | 가구·획지 규모와 조성'"),
    "4-4": ("6", "법정구조표", "대응표 '6 | 환경·경관'"),
    "4-5": ("6", "법정구조표", "대응표 '6 | 환경·경관'"),
}

# 호를 확정하지 못한 절과 그 사유. Concept 은 발급하되 lp:법제52조호 를 붙이지 않는다.
ART52_UNRESOLVED = {
    "3-1": "제52조제1항 각 호가 아닌 수립기준 운용 원칙이다",
    "3-2": "법 제52조제3항(행위제한 완화) 계열이며 제1항 각 호의 내용축이 아니다",
    "3-12": "제1항 각 호와 영 제45조제4항 각 호 어디에도 법문 대응이 없다. slot_patterns 는 제8호로 보나 정규식 근거는 쓰지 않는다",
    "3-13": "계획규범요소-틀은 호 대응 정본을 이 어휘의 절별 근거 기록으로 지정하며 3-13 은 미확정으로 남긴다. slot_exact 의 '대지내공지'·'공개공지'는 절제목과 정확일치하지 않는다",
    "3-14": "공원·녹지의 기반시설(제2호) 해당성은 영 제2조 대조가 있어야 확정된다",
    "3-15": "제1항 각 호에 법문 대응이 없다",
    "3-17": "'기반시설'은 부분문자열 일치일 뿐이고 기부채납은 법 제52조의2·제52조제3항 완화 연동이라 제2호로 볼 근거가 없다",
    "3-18": "기존 건축물의 특례는 각 호의 내용축이 아니다",
    "3-19": "가설건축물은 각 호의 내용축이 아니다",
    "4-1": "제1호(용도지역·용도지구)와 제3호(가구·획지)에 걸쳐 있어 단일 호로 확정할 수 없다",
    "4-6": "제7호 '교통처리계획'일 개연성이 높으나 절제목 '교통'이 법문·정본 서술과 정확일치하지 않는다",
    "4-7": "제52조 각 호가 아닌 지침 운용 조항이다",
}

# 대조 키. 공백과 중점 변형을 지운다. mint_iri.normalize_article_title 보다 넓다 —
# 절제목은 한글 중점 'ㆍ'(U+318D)를 쓰고 법문은 가운뎃점 '·'(U+00B7)을 쓴다.
_KEY_STRIP = re.compile(r"[\s·․‧∙・ㆍ]")


def _key(s):
    return _KEY_STRIP.sub("", s or "")


def verify_mapping(sections):
    """매핑 표가 원천 절 목록·legal-contrast 정본과 어긋나지 않는지 본다."""
    keys = {s["절키"] for s in sections}
    covered = set(ART52) | set(ART52_UNRESOLVED)
    if covered != keys:
        raise ValueError(
            f"매핑 표와 원천 절 목록이 다르다 — 표에만 {sorted(covered - keys)} · "
            f"원천에만 {sorted(keys - covered)}")
    overlap = set(ART52) & set(ART52_UNRESOLVED)
    if overlap:
        raise ValueError(f"확정과 미확정에 함께 있는 절: {sorted(overlap)}")

    with open(SLOT_MAP, encoding="utf-8") as f:
        slot = json.load(f)
    title = {s["절키"]: s["절제목"] for s in sections}
    for skey, (ho, basis, _why) in sorted(ART52.items()):
        if ho not in slot["호"]:
            raise ValueError(f"{skey}: 호 {ho!r} 가 제52조-슬롯매핑 정본에 없다")
        if basis == "호법문":
            if _key(title[skey]) not in _key(slot["호"][ho]["법문"]):
                raise ValueError(
                    f"{skey}: 절제목 {title[skey]!r} 이 제{ho}호 법문에 없다 — "
                    f"근거원을 호법문으로 둘 수 없다")
        elif basis == "slot_exact":
            got = slot["slot_exact"].get(_key(title[skey]))
            if got != ho:
                raise ValueError(
                    f"{skey}: slot_exact 정확일치가 {got!r} 라 {ho!r} 와 다르다")
        elif basis != "법정구조표":
            raise ValueError(f"{skey}: 알 수 없는 근거원 {basis!r}")


def _lit(s):
    """Turtle 짧은 리터럴 이스케이프. build_guideline_tree.ttl_escape 와 같은 관례다."""
    return (str(s).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))


VOCAB_HEADER = """@prefix lp:   <https://w3id.org/lp/ont#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

##  계획항목(L1) 어휘. 훈령 「지구단위계획수립지침」의 절에서 생성했다.
##  손으로 고치지 않는다.
##  갱신: python3 .claude/skills/kb/kb-ontology/scripts/build_plan_item.py
##  계약: .claude/skills/kb/kb-ontology/contract/ontology.json planItemAxis
##
##  이것은 SKOS 어휘이지 OWL 클래스가 아니다. 제52조 호 대응은 근거원이 확인된 것만
##  붙이고, 미확정 사유는 output/kb/reports/_plan_item.json 에 있다.
"""


def build_vocab(data):
    sections = [s for s in data["절목록"] if s["장번호"] in SCOPE_CHAPTERS]
    chapters = [c for c in data["장목록"] if c["장번호"] in SCOPE_CHAPTERS]
    verify_mapping(sections)

    out = [VOCAB_HEADER]
    out.append(
        f'<{M.PLAN_ITEM_SCHEME}> a skos:ConceptScheme ;\n'
        f'    skos:prefLabel "계획항목 (L1)"@ko ;\n'
        f'    skos:definition """계획 개념의 canonical 분류. 수립지침 제3장 공통 '
        f'수립기준의 절과 유형별 장의 절이다. 실측 표제·용어는 이 축의 출처가 아니라 '
        f'아래로 매핑되는 커버리지 검증셋이다."""@ko .\n')

    for c in sorted(chapters, key=lambda x: x["장번호"]):
        members = [s for s in sections if s["장번호"] == c["장번호"]]
        member_iris = " ,\n        ".join(
            f'<{M.plan_item(s["절키"])}>'
            for s in sorted(members, key=lambda x: x["절번호"]))
        out.append(
            f'<{M.plan_item_collection(c["장번호"])}> a skos:Collection ;\n'
            f'    skos:prefLabel "{_lit(c["장제목"])}"@ko ;\n'
            f'    lp:sourceText "{_lit(c["장제목_원문표기"])}" ;\n'
            f'    skos:member {member_iris} .\n')

    for s in sorted(sections, key=lambda x: (x["장번호"], x["절번호"])):
        skey = s["절키"]
        lines = [
            f'<{M.plan_item(skey)}> a skos:Concept ;',
            f'    skos:inScheme <{M.PLAN_ITEM_SCHEME}> ;',
            f'    skos:prefLabel "{_lit(s["절제목"])}"@ko ;',
            f'    skos:notation "{skey}" ;',
            f'    lp:sourceText "{_lit(s["절제목_원문표기"])}" ;',
            f'    lp:항수 "{s["항수"]}"^^xsd:integer',
        ]
        if skey in ART52:
            ho, basis, _why = ART52[skey]
            lines.append(f'    ;\n    lp:법제52조호 "{ho}"')
            lines.append(f'    ;\n    lp:법제52조호근거 "{basis}"')
        lines.append(" .")
        out.append("\n".join(lines) + "\n")

    return "\n".join(out)


def build_graph(data):
    """항 노드. 소속 계획항목이 있는 것만 낸다 — 나머지는 리포트로 간다."""
    meta = data["meta"]["생성근거"]
    source_name = meta["official_name"]
    sections = {s["절키"] for s in data["절목록"] if s["장번호"] in SCOPE_CHAPTERS}

    kept, dropped = [], []
    for h in data["항목록"]:
        if h["장번호"] not in SCOPE_CHAPTERS:
            dropped.append({
                "사유": "계획항목축_범위밖", "대상": h["항번호"],
                "설명": f"제{h['장번호']}장은 계약 planItemAxis.vocab.conceptScope 밖이라 "
                        f"소속 계획항목 Concept 이 없다"})
            continue
        if h.get("절번호") is None:
            dropped.append({
                "사유": "절없음", "대상": h["항번호"],
                "설명": f"제{h['장번호']}장에 절이 없어 계획항목에 소속시킬 수 없다"})
            continue
        skey = f'{h["장번호"]}-{h["절번호"]}'
        if skey not in sections:
            dropped.append({
                "사유": "절없음", "대상": h["항번호"],
                "설명": f"절키 {skey} 가 발급된 계획항목 Concept 집합에 없다"})
            continue
        kept.append((h, skey))

    out = [
        "@prefix lp:   <https://w3id.org/lp/ont#> .\n"
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        "@base <https://w3id.org/lp/id/> .\n"
        "\n"
        "##  계획항목(L1) 축의 항 노드.\n"
        f"##  원천: {meta['document_key']} 「{source_name}」({meta['official_kind']}) "
        f"전문 sha256 {meta['전문_sha256'][:16]}…\n"
        "##  현행 정본 시행일은 적용 판본이 아니다 — 판본·시행일을 노드에 붙이지 않는다.\n"
        "##  생성: build_plan_item.py (정렬 순회 · 멱등). 손으로 고치지 않는다.\n"
        "\n"
        "##  훈령 법원\n"
        "\n",
        f'<{_rel(M.statute(source_name))}> a lp:AdminRule ;\n'
        f'    rdfs:label "{_lit(source_name)}"@ko .\n'
        "\n"
        "##  항\n"
        "\n",
    ]
    for h, skey in sorted(kept, key=lambda x: _hang_sort(x[0]["항번호"])):
        iri = M.admin_rule_clause(source_name, h["항번호"])
        out.append(
            f'<{_rel(iri)}> a lp:AdminRuleClause ;\n'
            f'    rdfs:label "{_lit(h["항번호"])}" ;\n'
            f'    lp:항번호 "{_lit(h["항번호"])}" ;\n'
            f'    lp:계획항목 <{M.plan_item(skey)}> ;\n'
            f'    lp:inAdminRule <{_rel(M.statute(source_name))}> ;\n'
            f'    lp:sourceText "{_lit(h["본문"])}" .\n\n')
    return "".join(out).rstrip() + "\n", kept, dropped


def _rel(iri):
    """@base 기준 상대 IRI. build_boundary.py·build_guideline_tree.py 와 같은 방식이다."""
    return iri[len(M.ID):] if iri.startswith(M.ID) else iri


def _hang_sort(no):
    """항번호를 자연 정렬한다 — 문자열 정렬이면 3-8-10 이 3-8-2 앞에 온다."""
    return tuple(int(x) for x in no.split("-"))


def build_report(data, kept, dropped):
    sections = [s for s in data["절목록"] if s["장번호"] in SCOPE_CHAPTERS]
    격리 = list(dropped)
    for skey, why in sorted(ART52_UNRESOLVED.items()):
        격리.append({"사유": "제52조호_미확정", "대상": skey, "설명": why})
    return {
        "생성스크립트": "scripts/build_plan_item.py",
        "원천": "output/legal/statute/수립지침_항구조.json",
        "계약": "contract/ontology.json planItemAxis",
        "계획항목수": len(sections),
        "장별_계획항목수": {str(c): sum(1 for s in sections if s["장번호"] == c)
                            for c in SCOPE_CHAPTERS},
        "제52조호_확정": len(ART52),
        "제52조호_확정_근거원별": {
            b: sorted(k for k, v in ART52.items() if v[1] == b)
            for b in sorted({v[1] for v in ART52.values()})},
        "제52조호_미확정": len(ART52_UNRESOLVED),
        "항_전체": len(data["항목록"]),
        "항_발급": len(kept),
        "항_격리": len(dropped),
        "격리": sorted(격리, key=lambda r: (r["사유"], r["대상"])),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="갱신 없이 어긋남만 확인")
    args = ap.parse_args()

    with open(SOURCE, encoding="utf-8") as f:
        data = json.load(f)

    vocab = build_vocab(data)
    graph, kept, dropped = build_graph(data)
    report = json.dumps(build_report(data, kept, dropped),
                        ensure_ascii=False, indent=1) + "\n"

    targets = [(OUT_VOCAB, vocab), (OUT_GRAPH, graph), (OUT_REPORT, report)]
    if args.check:
        bad = [p for p, want in targets
               if (open(p, encoding="utf-8").read() if os.path.exists(p) else "") != want]
        if bad:
            for p in bad:
                print(f"{os.path.relpath(p, ROOT)} 이 원천과 어긋난다", file=sys.stderr)
            return 1
        print("일치")
        return 0

    for p, text in targets:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    print(f"계획항목 {len([s for s in data['절목록'] if s['장번호'] in SCOPE_CHAPTERS])} · "
          f"항 발급 {len(kept):,} / 전체 {len(data['항목록']):,}")
    print(f"제52조 호 확정 {len(ART52)} · 미확정 {len(ART52_UNRESOLVED)} · "
          f"항 격리 {len(dropped):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
