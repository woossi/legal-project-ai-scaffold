"""게이트 1~7. output/kb/ 만으로 돈다.

  1 TBox 일관성       unsatisfiable class 가 없다
  2 det/prob 격리      전용 술어가 반대 층에 없다
  3 det SHACL          shapes 문법이 유효하고 det 그래프가 통과한다
  4 prob SHACL         같음
  5 어휘 대조          vocab-concept 이 taxonomy.json 과 1:1·건수 일치
  6 IRI 패턴           keys.ttl 과 contract/ontology.json 이 일치
  7 정규화 멱등·완결    cited_laws 가 전부 발급 또는 격리로 분류된다

    python3 validate_ontology.py [--kb output/kb]
"""
import argparse
import glob
import json
import os
import sys

import pyshacl
import rdflib

sys.path.insert(0, os.path.dirname(__file__))
import normalize_law
import build_plan_item

HERE = os.path.dirname(__file__)
CONTRACT = os.path.join(HERE, "..", "contract", "ontology.json")
PROJ = os.path.join(HERE, "..", "..", "..", "..", "..")
TAXONOMY = os.path.join(PROJ, "output/legal/word/taxonomy.json")
TERMS = os.path.join(PROJ, "output/legal/word/terms.json")

LP = rdflib.Namespace("https://w3id.org/lp/ont#")
LPC = rdflib.Namespace("https://w3id.org/lp/concept/")
SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
OWL = rdflib.Namespace("http://www.w3.org/2002/07/owl#")


def _contract():
    with open(CONTRACT, encoding="utf-8") as f:
        return json.load(f)


def _load(kb, *rel):
    g = rdflib.Graph()
    for r in rel:
        for p in sorted(glob.glob(os.path.join(kb, r))):
            g.parse(p, format="turtle")
    return g


def _gate(no, name, ok, detail=""):
    return {"no": no, "name": name,
            "status": "pass" if ok else "fail", "detail": detail}


def gate1_tbox(kb):
    """subClassOf 순환, 미선언 클래스 참조, 미선언 lp: 술어를 잡는다.

    OWL 추론기의 unsatisfiable class 검사를 순환·미선언 참조 둘로 대신한다. 현 TBox 는
    disjointWith·complementOf 를 쓰지 않으므로 불충족 클래스가 나올 경로가 이 둘뿐이다.
    미선언 술어 검사는 별개 요건이다 — `output/kb/` 만으로 해석되려면(README:3) 쓰는
    lp: 술어가 전부 ontology/ 안에서 owl:*Property 로 선언돼 있어야 한다. graph/ 의
    인스턴스 데이터는 이 검사 대상이 아니다(그래프는 TBox 를 참조할 뿐 정의하지 않는다).
    """
    g = _load(kb, "ontology/*.ttl")
    classes = set(g.subjects(rdflib.RDF.type, OWL.Class))

    cyclic = []
    for c in classes:
        seen, cur = set(), list(g.objects(c, rdflib.RDFS.subClassOf))
        while cur:
            n = cur.pop()
            if n == c:
                cyclic.append(str(c))
                break
            if n in seen:
                continue
            seen.add(n)
            cur.extend(g.objects(n, rdflib.RDFS.subClassOf))

    dangling = []
    for p in [rdflib.RDFS.domain, rdflib.RDFS.range, rdflib.RDFS.subClassOf]:
        for s, _, o in g.triples((None, p, None)):
            if isinstance(o, rdflib.URIRef) and str(o).startswith(str(LP)) \
                    and o not in classes:
                dangling.append(f"{s} {p} {o}")

    prop_types = (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty)
    declared_props = set()
    for t in prop_types:
        declared_props |= set(g.subjects(rdflib.RDF.type, t))
    used_preds = {p for _, p, _ in g if str(p).startswith(str(LP))}
    undeclared = sorted(str(p) for p in used_preds if p not in declared_props)

    ok = not cyclic and not dangling and not undeclared
    detail = []
    if cyclic:
        detail.append(f"순환 {cyclic}")
    if dangling:
        detail.append(f"미선언 참조 {dangling[:5]}")
    if undeclared:
        detail.append(f"미선언 술어 {undeclared}")
    return _gate(1, "TBox 일관성", ok, " · ".join(detail))


def gate2_layer(kb):
    c = _contract()
    prob_only = [q.split(":", 1)[1] for q in c["probOnlyPredicates"]]
    det_only = [q.split(":", 1)[1] for q in c["detOnlyPredicates"]]
    hits = []
    for layer, banned in (("det", prob_only), ("prob", det_only)):
        g = _load(kb, *c["graphLayers"][layer]["dataGlobs"])
        for local in banned:
            if (None, LP[local], None) in g:
                hits.append(f"graph/{layer} 에 lp:{local}")
    return _gate(2, "det/prob 격리", not hits, " · ".join(hits))


def _shacl(kb, layer, shapes_file, no, name):
    shapes_path = os.path.join(kb, "shapes", shapes_file)
    if not os.path.exists(shapes_path):
        return _gate(no, name, False, f"{shapes_file} 가 없다")
    try:
        shapes = rdflib.Graph()
        shapes.parse(shapes_path, format="turtle")
    except Exception as e:
        return _gate(no, name, False, f"shapes 파싱 실패: {e}")
    data = _load(kb, *_contract()["graphLayers"][layer]["dataGlobs"])
    if len(data) == 0:
        return _gate(no, name, True, "그래프가 비어 있어 문법 검사만 수행")
    # norm 층 TBox 도 함께 싣는다 — ontology/ 만 보면 norm-core.ttl 의 클래스·
    # 인스턴스가 구조 검사를 통째로 면한다. 건폐율·용적률 두 지표가 실제로 그
    # 사각에 있었고 계획 지표 4종을 신설하다 우연히 드러났다.
    data += _load(kb, "ontology/*.ttl", "norm/ontology/*.ttl")
    conforms, _, text = pyshacl.validate(
        data, shacl_graph=shapes, inference="rdfs", advanced=True)
    return _gate(no, name, conforms, "" if conforms else text[:1500])


def gate5_vocab(kb):
    with open(TAXONOMY, encoding="utf-8") as f:
        counts = json.load(f)["axis_B"]["값별_용어수"]
    g = _load(kb, "ontology/vocab-concept.ttl")
    bad = []
    for name, n in counts.items():
        got = g.value(LPC[name], LP.termCount)
        if got is None:
            bad.append(f"{name} 없음")
        elif int(got) != n:
            bad.append(f"{name} ttl {int(got)} vs taxonomy {n}")
    skos = rdflib.Namespace("http://www.w3.org/2004/02/skos/core#")
    n_concepts = len(set(g.subjects(rdflib.RDF.type, skos.Concept)))
    if n_concepts != len(counts):
        bad.append(f"개념 {n_concepts}개 vs taxonomy {len(counts)}종")

    # L1은 수립지침 원천에서 정렬·발급한 세 산출물이 정본이다. build_plan_item의
    # 순수 생성 함수를 호출해 메모리 기대값을 만들므로 이 gate는 KB 파일을 쓰지 않는다.
    with open(build_plan_item.SOURCE, encoding="utf-8") as f:
        plan_item_data = json.load(f)
    expected_vocab = build_plan_item.build_vocab(plan_item_data)
    expected_graph, kept, dropped = build_plan_item.build_graph(plan_item_data)
    expected_report = json.dumps(
        build_plan_item.build_report(plan_item_data, kept, dropped),
        ensure_ascii=False, indent=1) + "\n"
    for rel, expected in (
            ("ontology/vocab-plan-item.ttl", expected_vocab),
            ("graph/det/plan-item.ttl", expected_graph),
            ("reports/_plan_item.json", expected_report)):
        path = os.path.join(kb, rel)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                actual = f.read()
        else:
            actual = ""
        if actual != expected:
            bad.append(f"L1 산출물 불일치 {rel}")
    return _gate(5, "어휘 대조", not bad, " · ".join(bad))


def gate6_iri(kb):
    declared = {}
    g = _load(kb, "ontology/keys.ttl")
    for s, _, o in g.triples((None, SH.pattern, None)):
        declared[str(g.value(s, rdflib.RDFS.label))] = str(o)
    contract = _contract()["iriPatterns"]
    bad = []
    if set(declared) != set(contract):
        bad.append(f"목록 불일치 ttl {sorted(declared)} vs 계약 {sorted(contract)}")
    for k in set(declared) & set(contract):
        if declared[k] != contract[k]:
            bad.append(f"{k}: ttl {declared[k]!r} vs 계약 {contract[k]!r}")
    return _gate(6, "IRI 패턴", not bad, " · ".join(bad))


def gate7_normalize(_kb):
    with open(TERMS, encoding="utf-8") as f:
        terms = json.load(f)["terms"]
    raws = sorted({L for t in terms for L in t.get("cited_laws", [])})
    results = [normalize_law.normalize(r) for r in raws]
    bad = []
    unclassified = [r for r in results if r["status"] not in ("resolved", "unresolved")]
    if unclassified:
        bad.append(f"미분류 {len(unclassified)}종")
    reasons = {"context_dependent", "not_a_law", "unparseable"}
    off = [r for r in results if r["status"] == "unresolved" and r["reason"] not in reasons]
    if off:
        bad.append(f"사유 도메인 이탈 {len(off)}종")
    for r in results:
        if r["status"] == "resolved" and \
                normalize_law.normalize(r["name"])["name"] != r["name"]:
            bad.append(f"멱등성 위반: {r['raw']!r}")
            break
    names = {r["name"] for r in results if r["status"] == "resolved"}
    unresolved = [r for r in results if r["status"] == "unresolved"]
    detail = f"{len(raws)}종 → 법령명 {len(names)}종 · 격리 {len(unresolved)}건"
    return _gate(7, "정규화 멱등·완결", not bad, " · ".join(bad) if bad else detail)


def run(kb_dir):
    gates = [
        gate1_tbox(kb_dir),
        gate2_layer(kb_dir),
        _shacl(kb_dir, "det", "det.shacl.ttl", 3, "det SHACL"),
        _shacl(kb_dir, "prob", "prob.shacl.ttl", 4, "prob SHACL"),
        gate5_vocab(kb_dir),
        gate6_iri(kb_dir),
        gate7_normalize(kb_dir),
    ]
    return {"gates": gates, "failed": sum(1 for g in gates if g["status"] == "fail")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.path.join(PROJ, "output/kb"))
    args = ap.parse_args()
    r = run(args.kb)
    for g in r["gates"]:
        mark = "OK  " if g["status"] == "pass" else "FAIL"
        print(f"[{mark}] 게이트 {g['no']} {g['name']}"
              + (f" — {g['detail']}" if g["detail"] else ""))
    print(f"\n실패 {r['failed']}/7")
    return 1 if r["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
