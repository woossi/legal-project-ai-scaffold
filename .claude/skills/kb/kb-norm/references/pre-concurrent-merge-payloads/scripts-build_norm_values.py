"""규범값 — 건폐율·용적률. (관할 × 용도지역 × 조건) → 값.

근거 항이 조건을 결정한다. 영 제84조제1항·제85조제1항은 용도지역 기본값이고,
그 밖의 항은 특례다. 항을 구분하지 않고 값을 뭉치면 기본값과 특례값이 섞인다.

    .venv_kb/bin/python3 .claude/skills/kb/kb-norm/scripts/build_norm_values.py
"""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import corpus as C
import mint_norm_iri as M
import parse_ordinance as P

sys.path.insert(0, os.path.abspath(
    os.path.join(HERE, "..", "..", "kb-ontology", "scripts")))
import mint_iri as I

OUT_TTL = os.path.join(C.ROOT, "output/kb/norm/graph/det/norm-value.ttl")
OUT_REPORT = os.path.join(C.ROOT, "output/kb/norm/reports/_norm_value.json")
CONTRACT = os.path.join(HERE, "..", "contract", "norm_value.json")
DELEGATION_TTL = os.path.join(C.ROOT, "output/kb/norm/graph/det/delegation.ttl")


def delegated_articles():
    """위임 사슬에서 lp:delegates 의 대상이 된 조례 조문 IRI 집합.

    규범명제도 det 이므로 근거 사슬이 이어져야 한다. 위임 사슬이 '판정 불가'로
    격리한 조문에서 값을 뽑아 명제를 만들면 두 산출물이 같은 근거에 다른 판정을
    내리게 된다 — 실측에서 방화지구 건폐율 완화 조문 2종(영 제84조제6항 근거)이
    그 상태였다. 그 항은 각 호를 가리키는데 corpus 가 호를 항에 귀속시키지 않아
    위임 사슬 쪽에서 지목항_호귀속불명 으로 빠진다.

    빈 집합을 내면 즉시 실패시킨다. delegation.ttl 이 비어 있거나(위임 빌더를
    안 돌렸거나 그 빌더가 실패했다) lp:delegates 간선을 하나도 못 읽는 상태에서
    이 함수가 조용히 빈 집합을 내면, 호출부는 모든 조문을 위임사슬_밖 으로
    격리하고 조례 30 · 값산출 0 · 명제 0 · 격리 495 로 **성공 종료(exit 0)한다** —
    명제 0개는 조용한 실패이지 정상 상태가 아니다. 계약(`contract/norm_value.json`)
    선행조건이 실행 순서를 적어 두지만 코드가 강제하지 않으면 이 상황이 그대로
    재현된다.
    """
    import rdflib
    g = rdflib.Graph()
    g.parse(DELEGATION_TTL, format="turtle")
    LP = rdflib.Namespace("https://w3id.org/lp/ont#")
    result = {str(o) for o in g.objects(None, LP.delegates) if "/ordinance/" in str(o)}
    if not result:
        raise RuntimeError(
            f"{DELEGATION_TTL} 에서 조례 조문을 대상으로 하는 lp:delegates 간선을 "
            "하나도 못 읽었다. 위임 사슬 없이 규범값을 만들면 근거 사슬이 끊긴 "
            "명제가 나올 위험이 있어(이 가드가 없던 때는 명제 0개로 조용히 "
            "'성공'만 냈다) 여기서 멈춘다. 보통 원인은 build_delegation.py 를 "
            "먼저 안 돌렸거나 그 산출물이 비어 있는 것이다 — 순서대로 다시 돌려라.\n"
            "  .venv_kb/bin/python3 .claude/skills/kb/kb-norm/scripts/build_delegation.py\n"
            "  .venv_kb/bin/python3 .claude/skills/kb/kb-norm/scripts/build_norm_values.py")
    return result

AXIS_BY_DECREE = {"84": "건폐율", "85": "용적률"}
SYSTEM = "도시계획조례"

# 괄호 밖 예외 문언. parse_zone_values 는 () 안만 잡으므로 여기서 사각지대를 잰다.
EXCEPTION_RE = re.compile(r"(다만|단서|\[)")
EXCEPTION_VALUE_RE = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*퍼센트")


def is_branch_article(basis):
    """basis 가 축 본조(제84조·제85조)가 아니라 그 가지조문(제84조의2 등)을
    가리키는가.

    basis['number'] 만 보고 판정하면(branch 를 안 보면) 가지조문이 본조로
    오인된다 — 가지조문은 완화·산정방법 등 본조와 다른 규범이라, 본조로
    오인되면 condition_key() 의 '기본'(제1항, 용도지역별 최대한도)과 충돌한다.
    실측 20건(19관할)이 전부 영 제84조의2(공공시설 부지 제공에 따른 건폐율
    완화)다 — 지금은 원문이 파서가 못 읽는 '100분의' 표기라 값이 안 잡혀
    피해가 0이지만, 그 파서를 지원하는 순간 완화값이 기본 건폐율 버킷으로
    샌다. followup7.
    """
    return bool(basis.get("branch"))


def condition_key(basis, conditional):
    """근거 항과 괄호 조건에서 조건 키를 만든다.

    영 제84조제1항·제85조제1항은 '기본'. 그 밖의 항은 '영{조}-{항}'.
    괄호 조건부는 조건 원문 해시를 붙여 기본값과 절대 겹치지 않게 한다.
    """
    number = basis.get("number")
    para = basis.get("paragraph")
    if conditional:
        digest = hashlib.sha256(conditional.encode("utf-8")).hexdigest()[:8]
        return f"괄호-{digest}"
    if para == "1" and number in AXIS_BY_DECREE:
        return "기본"
    if para:
        return f"영{number}-{para}"
    return f"영{number}-항미상"


def collect():
    rows, quarantine = [], []
    docs = 0
    value_docs = set()
    chained = delegated_articles()
    for doc in C.ordinance_docs():
        system, _ = C.ordinance_system(doc)
        if system != SYSTEM:
            continue
        docs += 1
        name = doc["official_name"]
        lc5 = C.jurisdiction_code(doc.get("authority"))
        if not lc5:
            quarantine.append({"사유": "관할코드_미확정", "대상": name,
                               "설명": f'지자체기관명 {doc.get("authority")!r}'})
            continue
        eff = M.effective_compact(doc.get("current_effective_date"))
        if not eff:
            quarantine.append({"사유": "시행일_미확정", "대상": name,
                               "설명": f'{doc.get("current_effective_date")!r}'})
            continue
        for _, label, text, _ in C.articles(doc):
            for para_no, body in P.split_paragraphs(text):
                # 값의 근거는 시행령이다. "법 제77조 및 영 제84조제1항" 처럼 둘이
                # 함께 오는 조문이 많아 원천을 '영'으로 못박고 고른다.
                basis = P.find_basis(body, source="영")
                if not basis or basis["number"] not in AXIS_BY_DECREE:
                    # 근거가 없는데 용도지역 값이 있으면 조용히 버리지 않는다.
                    # "제1항에도 불구하고 …" 처럼 후속 항이 영을 재인용하지 않는 경우가 있다.
                    if P.parse_zone_values(body):
                        설명 = ("용도지역 값은 있으나 이 항에 영 근거가 없다"
                              if not basis else
                              f'용도지역 값은 있으나 영 근거가 제{basis["number"]}조라 '
                              f'건폐율(제84조)·용적률(제85조) 축이 아니다')
                        quarantine.append({
                            "사유": "근거미상_값문단",
                            "대상": f"{name} {label} 제{para_no}항",
                            "설명": 설명})
                    continue
                if is_branch_article(basis):
                    # 가지조문(영 제84조의2 등)은 본조(제84조·제85조)와 다른 규범이다.
                    # is_branch_article() 독스트링 참조.
                    #
                    # 지금은 20건 전부 원문이 '100분의' 등 이 파서가 못 읽는 표기라
                    # parse_zone_values 가 값을 못 내(실질 피해 0) 값 유무를 안 보고도
                    # 여기서 걸러진다. 하지만 100분의 파서를 지원하는 순간 이 완화값이
                    # 조용히 기본 건폐율·용적률 버킷으로 샌다 — 그래서 값이 잡히든 안
                    # 잡히든 가지조문이면 항상 이 사유로 격리한다. 값_파싱실패 로
                    # 뭉뚱그리면 "본조인데 파싱 실패"와 "애초에 본조가 아님"이 섞인다.
                    quarantine.append({
                        "사유": "가지조문_제외",
                        "대상": f"{name} {label} 제{para_no}항",
                        "설명": f'근거 {basis["raw"]} 는 본조 제{basis["number"]}조가 '
                                f'아니라 가지조문 제{basis["number"]}조의'
                                f'{basis["branch"]}다 — 기본/특례 축과 다른 규범이라 '
                                f"이 스킬은 아직 다루지 않는다"})
                    continue
                axis = AXIS_BY_DECREE[basis["number"]]
                values = P.parse_zone_values(body)
                if not values:
                    quarantine.append({
                        "사유": "값_파싱실패", "대상": f"{name} {label} 제{para_no}항",
                        "설명": f'근거 {basis["raw"]} 인데 용도지역 값이 안 잡힌다'})
                    continue
                # 괄호 밖 예외 문언은 파서가 잡지 못한다. 값이 나왔더라도 남겨 사각지대를
                # 드러낸다 — 실측 "다만 … 퍼센트" 33건/11개 조례, 대괄호 1건.
                if EXCEPTION_RE.search(body):
                    got = {v["value"] for v in values}
                    miss = [n for n in EXCEPTION_VALUE_RE.findall(body)
                            if P.to_decimal(n) not in got]
                    if miss:
                        quarantine.append({
                            "사유": "예외값_미파싱",
                            "대상": f"{name} {label} 제{para_no}항",
                            "설명": f"괄호 밖 예외 문언의 값 {sorted(set(miss))} 이 명제로 안 들어간다"})
                if not basis["paragraph"]:
                    quarantine.append({
                        "사유": "근거항_미확정", "대상": f"{name} {label} 제{para_no}항",
                        "설명": f'{basis["raw"]} — 항이 없어 조건을 정할 수 없다'})
                    continue
                art_iri = I.ordinance_article(lc5, SYSTEM, label, eff)
                if art_iri not in chained:
                    quarantine.append({
                        "사유": "위임사슬_밖",
                        "대상": f"{name} {label} 제{para_no}항",
                        "설명": f'근거 {basis["raw"]} 이 위임 사슬에서 판정되지 않아 '
                                f"이 조문은 lp:delegates 의 대상이 아니다"})
                    continue
                value_docs.add(name)
                for v in values:
                    rows.append({
                        "lc5": lc5, "조례": name, "시행일": eff,
                        "조문": label, "축": axis, "용도지역": v["zone"],
                        # _OP 는 optional 이라 원문에 비교연산자가 없으면 파서가
                        # 정직하게 None 을 낸다. 여기서 "이하"로 채우면 원문에 없는
                        # 값을 만든 것이 된다 — 건폐율·용적률은 최고한도라 문맥상
                        # "이하"가 맞다는 것은 법 해석이지 원문 근거가 아니다.
                        # 시행지침이 "60% 이상"인데 우리가 "이하"로 저장하면 대조
                        # 기준값 자체가 오염된다. None 은 build_ttl 이 lp:비교연산_
                        # 미표기 플래그로 드러낸다 — 격리하지 않는다, 상한값 자체는
                        # 유효하다.
                        "값": v["value"], "연산": v["operator"],
                        "발췌": v["excerpt"],
                        # 가지조문은 위에서 이미 걸러 이 줄까지 안 오지만, 방어적으로
                        # 가지 번호를 넣는다 — 지우면 "영 제84조의2제1항"이 "영
                        # 제84조제1항"으로 기록돼 명제 쪽에 근거가 틀리게 남는다.
                        "근거항": (f'영 제{basis["number"]}조의{basis["branch"]}'
                                  f'제{basis["paragraph"]}항' if basis["branch"] else
                                  f'영 제{basis["number"]}조제{basis["paragraph"]}항'),
                        "조건키": condition_key(basis, v["conditional"]),
                        "조건원문": v["conditional"],
                    })
    # 같은 조합이 두 번 나오면 충돌로 격리한다 — 조용히 덮지 않는다
    by_key, dedup = {}, []
    for r in sorted(rows, key=lambda x: (x["lc5"], x["축"], x["용도지역"],
                                         x["조건키"], x["조문"])):
        key = (r["lc5"], r["축"], r["용도지역"], r["조건키"])
        if key in by_key:
            if by_key[key]["값"] != r["값"]:
                quarantine.append({
                    "사유": "중복명제_충돌", "대상": f'{r["조례"]} {r["용도지역"]} {r["축"]}',
                    "설명": f'{by_key[key]["값"]} vs {r["값"]} — 같은 조합에 다른 값'})
            continue
        by_key[key] = r
        dedup.append(r)
    return dedup, quarantine, docs, len(value_docs)


def build_ttl(rows):
    L = ["@prefix lp:   <https://w3id.org/lp/ont#> .",
         "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
         "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .",
         "@base <https://w3id.org/lp/id/> .", "",
         "##  규범값 — (관할 × 용도지역 × 조건) → 값. 근거 항이 조건을 결정한다.", ""]

    zones = sorted({r["용도지역"] for r in rows})
    L += ["##  용도지역", ""]
    for z in zones:
        L += [f"<{M.rel(M.zone(z))}> a lp:UseZone ;", f'    rdfs:label "{z}"@ko .', ""]

    conds = {}
    for r in rows:
        conds.setdefault(r["조건키"], r["조건원문"])
    L += ["##  적용조건", ""]
    for key in sorted(conds):
        L.append(f"<{M.rel(M.condition(key))}> a lp:NormCondition ;")
        if conds[key]:
            L.append(f'    lp:조건원문 {json.dumps(conds[key], ensure_ascii=False)} ;')
        L += [f'    rdfs:label "{key}"@ko .', ""]

    L += ["##  규범명제", ""]
    for r in rows:
        iri = M.rel(M.norm(r["lc5"], r["축"], r["용도지역"], r["조건키"]))
        art = M.rel(I.ordinance_article(r["lc5"], SYSTEM, r["조문"], r["시행일"]))
        L += [f"<{iri}> a lp:NormStatement ;",
              f"    lp:적용관할 <{M.rel(I.gov(r['lc5']))}> ;",
              f"    lp:적용용도지역 <{M.rel(M.zone(r['용도지역']))}> ;",
              f"    lp:규범축 lp:{r['축']} ;",
              f"    lp:적용조건 <{M.rel(M.condition(r['조건키']))}> ;",
              f"    lp:근거조문 <{art}> ;",
              f'    lp:상한값 "{r["값"]}"^^xsd:decimal ;',
              '    lp:단위 "퍼센트" ;']
        # 원문에 비교연산자가 있을 때만 lp:비교연산 을 붙인다. 없다고 빈 문자열이나
        # "None"(str(None) 문자 그대로)을 내보내면 그게 더 나쁘다 — 대신
        # lp:비교연산_미표기 를 달아 원문 결손을 명시적으로 드러낸다. 저신뢰
        # 플래그이지 격리 사유가 아니다 — 상한값 자체는 유효해 명제를 만든다.
        if r["연산"]:
            L.append(f'    lp:비교연산 "{r["연산"]}" ;')
        else:
            L.append('    lp:비교연산_미표기 "true"^^xsd:boolean ;')
        L += [f'    lp:근거발췌 {json.dumps(r["발췌"], ensure_ascii=False)} ;',
              f'    lp:위임근거항 "{r["근거항"]}" .',
              ""]
    return L


def main():
    rows, quarantine, docs, value_docs = collect()
    lines = build_ttl(rows)
    os.makedirs(os.path.dirname(OUT_TTL), exist_ok=True)
    os.makedirs(os.path.dirname(OUT_REPORT), exist_ok=True)
    with open(OUT_TTL, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    report = {
        "meta": {
            "생성근거": "도시계획조례 정본의 영 제84·85조 근거 조문 파싱",
            "스크립트": ".claude/skills/kb/kb-norm/scripts/build_norm_values.py",
            "원칙": "근거 항이 조건을 결정한다. 기본값과 특례값을 한 명제에 섞지 않는다",
        },
        "도시계획조례수": docs,
        "값산출조례수": value_docs,
        "명제수": len(rows),
        "관할수": len({r["lc5"] for r in rows}),
        "격리": sorted(quarantine, key=lambda q: (q["사유"], q["대상"])),
    }
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")
    print(f"조례 {docs} · 값산출 {value_docs} · 명제 {len(rows)} · 격리 {len(quarantine)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
