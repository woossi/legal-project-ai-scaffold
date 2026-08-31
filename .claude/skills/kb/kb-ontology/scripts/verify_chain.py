"""게이트 8~14. 스펙 ②·③·용어-컴포넌트-연결의 산출물을 입력으로 받는다.

  8  위임 사슬 무결성   조문은 뿌리 법률까지 delegates 로 닿고, 조례는 뿌리에 닿는
                     조문으로부터 조례위임을 받는다 (조례 조문 단위 사슬은 스펙 ② 이후)
  9  적용 실제성        적용 사례는 lp:evidence와 lp:appliedNorm을 가지며, appliedNorm
                     대상은 명시적 lp:ArticleVersion이다 (누락·미상은 인스턴스가 아니다)
 10  법률 직접 적용 금지 appliedNorm 대상에 lp:Act 소속 조문이 없다
 11  시점 정합          asOfYear 가 판본의 시행일~실효일 구간에 든다
 12  시점 근거 필수      temporalBasis는 고시일|인용일|문서작성일만 허용하고,
                     temporalPrecision과 함께 기록한다
 13  조문 트리 정합      IRI 충돌·연결 누락·집계 일치 (guideline.ttl)
 14  계획규정 정합      csv 행수 = 발급 rule 수 + 행 격리 수, 값 도메인·근거문서 dangling·
                     규범축 RegulationIndex 소속 없음

입력이 없으면 실패가 아니라 미검사(skipped)로 집계한다.

    python3 verify_chain.py [--kb output/kb]
"""
import argparse
import glob
import json
import os

import rdflib

HERE = os.path.dirname(__file__)
ROOTS = os.path.join(HERE, "..", "contract", "law_roots.json")
PROJ = os.path.join(HERE, "..", "..", "..", "..", "..")

LP = rdflib.Namespace("https://w3id.org/lp/ont#")

# 게이트 14 값 도메인 — 정본은 legal-tablecsv contract/outputs.json 의 값도메인
# (주체유형·한도구분). kb 사본(grounding.ttl·det.shacl.ttl 의 sh:in)과 이 상수가
# 어긋나지 않는지는 test_kb_value_domain_drift.py 가 별도로 검사한다.
TYPES = {"블록", "획지", "주택유형", "용지", "도면표시", "미상"}
LIMITS = {"최고", "최저", "확정"}


def _det(kb):
    g = rdflib.Graph()
    for p in sorted(glob.glob(os.path.join(kb, "graph/det/*.ttl"))):
        g.parse(p, format="turtle")
    return g


def _regulation_index_set(kb):
    """RegulationIndex 인스턴스 IRI 집합과 파싱 실패 목록.

    output/kb/ontology 와 output/kb/norm/ontology 를 합쳐 구성한다. 파싱이 실패하면
    집합이 조용히 비고, 그 상태로 대조하면 정상 규범축이 전부 '비RegulationIndex' 로
    오탐된다 — 그래서 실패를 삼키지 않고 함께 돌려준다."""
    g = rdflib.Graph()
    failed = []
    for sub in ("ontology", os.path.join("norm", "ontology")):
        for p in sorted(glob.glob(os.path.join(kb, sub, "*.ttl"))):
            try:
                g.parse(p, format="turtle")
            except Exception as e:                       # noqa: BLE001
                failed.append(f"{os.path.relpath(p, kb)}({type(e).__name__})")
    return set(g.subjects(rdflib.RDF.type, LP.RegulationIndex)), failed


def _gate(no, name, status, detail=""):
    return {"no": no, "name": name, "status": status, "detail": detail}


def _root_names():
    with open(ROOTS, encoding="utf-8") as f:
        r = json.load(f)
    names = {e["lawordNm"] for e in r["designationRoots"]}
    names |= {e["statute"] for e in r["systemRoots"]}
    return names


def _reaches_root(g, start, roots):
    """start 가 rootStatute 를 직접 갖거나, delegates 상향 순회로 뿌리 법률에 닿는가."""
    seen, cur = set(), [start]
    while cur:
        n = cur.pop()
        if n in seen:
            continue
        seen.add(n)
        if any(str(v) in roots for v in g.objects(n, LP.rootStatute)):
            return True
        cur.extend(g.subjects(LP.delegates, n))
    return False


def gate8(g):
    """조문이 뿌리까지 닿는가 + 조례가 뿌리에 닿는 조문으로부터 조례위임을 받는가.

    두 검사는 별개다 — delegates 상향 순회(조문→조문)는 ArticleWork 만 훑고,
    조례위임(조문→조례)은 lp:Ordinance 가 최소 1개의 발신 조문을 갖는지만 본다.
    조례 자신의 조문 단위 사슬(조례→조례 조문)은 스펙 ②가 조례 조문을 발급한 이후다.
    """
    articles = set(g.subjects(rdflib.RDF.type, LP.ArticleWork))
    if not articles:
        return _gate(8, "위임 사슬 무결성", "skipped", "조문 노드가 없다")
    roots = _root_names()

    broken_articles = sorted(
        str(a) for a in articles if not _reaches_root(g, a, roots))

    ordinances = set(g.subjects(rdflib.RDF.type, LP.Ordinance))
    broken_ordinances = sorted(
        str(o) for o in ordinances
        if not any(_reaches_root(g, s, roots) for s in g.subjects(LP.조례위임, o)))

    broken = broken_articles + broken_ordinances
    detail = (f"조문 {len(articles) - len(broken_articles)}/{len(articles)} 뿌리 도달 · "
              f"조례 {len(ordinances) - len(broken_ordinances)}/{len(ordinances)} 위임 수신")
    if broken:
        detail += f" · 위반 {len(broken)}건: {broken[:5]}"
    return _gate(8, "위임 사슬 무결성", "fail" if broken else "pass", detail)


def gate9(g):
    apps = set(g.subjects(rdflib.RDF.type, LP.LawApplication))
    if not apps:
        return _gate(9, "적용 실제성", "skipped", "적용 사례가 없다")
    bad = []
    for a in apps:
        if not list(g.objects(a, LP.evidence)):
            bad.append(f"{a} evidence 없음")
        norms = list(g.objects(a, LP.appliedNorm))
        if not norms:
            bad.append(f"{a} appliedNorm 없음")
        for norm in norms:
            if (norm, rdflib.RDF.type, LP.ArticleVersion) not in g:
                bad.append(f"{a} appliedNorm 비ArticleVersion {norm}")
    return _gate(9, "적용 실제성", "fail" if bad else "pass",
                 f"근거·판본 결손 적용 사례 {len(bad)}건: {bad[:5]}" if bad
                 else f"적용 사례 {len(apps)}건 전수 통과 · 미발급 지구는 "
                      f"reports/_plan_rule.json 참조 — 게이트 분모 아님. "
                      f"이 게이트는 발급된 사례의 근거·판본 무결성만 본다")


def gate10(g):
    apps = set(g.subjects(rdflib.RDF.type, LP.LawApplication))
    if not apps:
        return _gate(10, "법률 직접 적용 금지", "skipped", "적용 사례가 없다")
    bad = []
    for a in apps:
        for ver in g.objects(a, LP.appliedNorm):
            for work in g.objects(ver, LP.versionOf):
                for src in g.objects(work, LP.inSource):
                    if (src, rdflib.RDF.type, LP.Act) in g:
                        bad.append(str(src))
    return _gate(10, "법률 직접 적용 금지", "fail" if bad else "pass",
                 f"법률 조문을 직접 적용한 사례 {len(bad)}건: {sorted(set(bad))[:5]}"
                 if bad
                 else f"적용 사례 {len(apps)}건 전수 통과 · 미발급 지구는 "
                      f"reports/_plan_rule.json 참조 — 게이트 분모 아님. "
                      f"이 게이트는 발급된 사례의 법률 직접 적용 여부만 본다")


def gate11(g):
    apps = set(g.subjects(rdflib.RDF.type, LP.LawApplication))
    if not apps:
        return _gate(11, "시점 정합", "skipped", "적용 사례가 없다")
    bad = []
    for a in apps:
        year = g.value(a, LP.asOfYear)
        if year is None:
            continue
        y = int(str(year)[:4])
        for ver in g.objects(a, LP.appliedNorm):
            eff = g.value(ver, LP.시행일)
            exp = g.value(ver, LP.실효일)
            if eff is not None and y < int(str(eff)[:4]):
                bad.append(f"{a} asOf {y} < 시행 {eff}")
            if exp is not None and y > int(str(exp)[:4]):
                bad.append(f"{a} asOf {y} > 실효 {exp}")
    return _gate(11, "시점 정합", "fail" if bad else "pass",
                 f"구간 밖 {len(bad)}건: {bad[:5]}" if bad
                 else f"적용 사례 {len(apps)}건 전수 통과 · 미발급 지구는 "
                      f"reports/_plan_rule.json 참조 — 게이트 분모 아님. "
                      f"이 게이트는 발급된 사례의 시점 구간 정합만 본다")


def gate12(g):
    apps = set(g.subjects(rdflib.RDF.type, LP.LawApplication))
    if not apps:
        return _gate(12, "시점 근거 필수", "skipped", "적용 사례가 없다")
    allowed_basis = {"고시일", "인용일", "문서작성일"}
    bad = []
    for a in apps:
        basis = list(g.objects(a, LP.temporalBasis))
        if not basis or not list(g.objects(a, LP.temporalPrecision)):
            bad.append(f"{a} basis 또는 precision 없음")
        invalid_basis = [str(v) for v in basis if str(v) not in allowed_basis]
        if invalid_basis:
            bad.append(f"{a} temporalBasis 도메인 이탈 {invalid_basis}")
    return _gate(12, "시점 근거 필수", "fail" if bad else "pass",
                 f"근거 없는 적용 사례 {len(bad)}건: {bad[:5]}" if bad
                 else f"적용 사례 {len(apps)}건 전수 통과 · 미발급 지구는 "
                      f"reports/_plan_rule.json 참조 — 게이트 분모 아님. "
                      f"이 게이트는 발급된 사례의 시점 근거 보유만 본다")


def gate13(g):
    """조문 트리 정합 — IRI 충돌·연결 누락·집계 일치. 조항이 없으면 미검사다."""
    name = "조문 트리 정합"
    arts = set(g.subjects(rdflib.RDF.type, LP.GuidelineArticle))
    if not arts:
        return _gate(13, name, "skipped", "조문 노드가 없다")

    bad = []
    ORIGINS = {"heading", "promoted", "definition-restored"}

    off = sorted({str(v) for a in arts for v in g.objects(a, LP.articleOrigin)} - ORIGINS)
    if off:
        bad.append(f"articleOrigin 도메인 이탈 {off}")

    # 같은 IRI 에 label 이 둘 붙으면 동명순번이 서로 다른 조문을 못 갈랐다는 뜻이다
    clash = sorted(str(a) for a in arts if len(set(g.objects(a, rdflib.RDFS.label))) > 1)
    if clash:
        bad.append(f"조항 IRI 충돌 {len(clash)}건: {clash[:3]}")

    stmts = set(g.subjects(rdflib.RDF.type, LP.TermDefinition))
    for p, label in ((LP.ofTerm, "ofTerm"), (LP.inArticle, "inArticle"),
                     (LP.inDistrict, "inDistrict")):
        n = sum(1 for s in stmts if not list(g.objects(s, p)))
        if n:
            bad.append(f"{label} 누락 {n}건")

    # 표목의 선언 지구수와 실제 lp:사례지구 개수를 재계산 대조한다
    mism = []
    for t in sorted(g.subjects(rdflib.RDF.type, LP.PlanElement)):
        declared = list(g.objects(t, LP["지구수"]))
        if not declared:
            continue
        if len(declared) != 1:
            mism.append(f"{t} 지구수 선언 {len(declared)}개")
            continue
        try:
            declared_count = int(declared[0])
        except (TypeError, ValueError):
            mism.append(f"{t} 지구수 비정수 {declared[0]}")
            continue
        actual = len(set(g.objects(t, LP["사례지구"])))
        if declared_count != actual:
            mism.append(f"{t} 선언 {declared_count} vs 실제 {actual}")
    if mism:
        bad.append(f"지구수 불일치 {len(mism)}건: {mism[:3]}")

    detail = "; ".join(bad) if bad else f"조항 {len(arts):,} · 진술 {len(stmts):,}"
    return _gate(13, name, "fail" if bad else "pass", detail)


def gate14(g, kb, csv_path=None):
    """계획규정 정합 — csv 행수 = 발급 rule 수 + 행 격리 수. IRI 충돌·도메인 이탈·앵커 dangling·
    규범축 RegulationIndex 소속 0건. reports/_plan_rule.json 최상위 키 격리(사유=행격리)만
    센다 — 결손·미성립 기록 계열(파싱실패사유·시점근거_미확보·적용판본_미보유·
    용도지역_매핑_미발급)과 섞으면 등식이 깨진다."""
    import csv as _csv
    name = "계획규정 정합"
    csv_path = csv_path or os.path.join(PROJ, "output/legal/table/norm_건축계획지표.csv")
    if not os.path.exists(csv_path):
        return _gate(14, name, "skipped", "norm_건축계획지표.csv 가 없다")
    with open(csv_path, encoding="utf-8-sig") as f:
        n_rows = sum(1 for _ in _csv.DictReader(f))
    rules = set(g.subjects(rdflib.RDF.type, LP.PlanningRule))
    rep_path = os.path.join(kb, "reports", "_plan_rule.json")
    if not rules and not os.path.exists(rep_path):
        return _gate(14, name, "skipped", "발급기 미실행 — rule 그래프·리포트 둘 다 없다")
    n_iso = 0
    if os.path.exists(rep_path):
        with open(rep_path, encoding="utf-8") as f:
            n_iso = sum(1 for r in json.load(f).get("격리", [])
                        if r.get("사유") == "행격리")
    bad = []
    # IRI 충돌(같은 IRI 로 발급된 서로 다른 규정 두 건)은 rdflib 그래프에서 자동
    # 병합돼 len(rules) 로 직접 셀 수 없다 — 이 집계 불일치로만 간접적으로 잡힌다.
    if n_rows != len(rules) + n_iso:
        bad.append(f"집계 불일치 csv {n_rows} != rule {len(rules)} + 행격리 {n_iso}"
                    " — IRI 충돌(서로 다른 행이 같은 IRI 로 병합)일 수 있다")
    reg_index, parse_failed = _regulation_index_set(kb)
    if parse_failed:
        # 규범축 대조를 강행하면 전건이 비RegulationIndex 로 오탐된다. 검사를
        # 접고 파싱 실패 자체를 위반으로 보고한다 — 크래시도 오탐도 아니다.
        bad.append(f"ontology TTL 파싱 실패 {len(parse_failed)}건: {parse_failed[:3]}"
                   " — 규범축 소속 검사를 수행하지 못했다")
        reg_index = None
    for r in rules:
        for v in g.objects(r, LP.적용대상유형):
            if str(v) not in TYPES:
                bad.append(f"적용대상유형 이탈 {v}")
        for v in g.objects(r, LP.한도구분):
            if str(v) not in LIMITS:
                bad.append(f"한도구분 이탈 {v}")
        for doc in g.objects(r, LP.근거문서):
            if (doc, rdflib.RDF.type, LP.Guideline) not in g:
                bad.append(f"근거문서 dangling {doc}")
        if reg_index is not None:
            for v in g.objects(r, LP.규범축):
                if v not in reg_index:
                    bad.append(f"규범축 비RegulationIndex {v}")
    detail = f"csv {n_rows} = rule {len(rules)} + 행격리 {n_iso}"
    if bad:
        detail = f"위반 {len(bad)}건: {bad[:5]}"
    return _gate(14, name, "fail" if bad else "pass", detail)


def run(kb_dir):
    g = _det(kb_dir)
    gates = [gate8(g), gate9(g), gate10(g), gate11(g), gate12(g), gate13(g),
             gate14(g, kb_dir)]
    return {"gates": gates,
            "failed": sum(1 for x in gates if x["status"] == "fail"),
            "skipped": sum(1 for x in gates if x["status"] == "skipped")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.path.join(PROJ, "output/kb"))
    args = ap.parse_args()
    r = run(args.kb)
    mark = {"pass": "OK  ", "fail": "FAIL", "skipped": "SKIP"}
    for x in r["gates"]:
        print(f"[{mark[x['status']]}] 게이트 {x['no']} {x['name']}"
              + (f" — {x['detail']}" if x["detail"] else ""))
    print(f"\n실패 {r['failed']} · 미검사 {r['skipped']} / 7")
    return 1 if r["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
