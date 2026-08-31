"""온톨로지 지도를 그린다. output/kb/ontology/*.ttl 에서 읽는다.

손으로 그리지 않는다 — TTL 이 정본이고 그림은 그 파생이다. 구조가 바뀌면 다시 돌린다.

    .venv_kb/bin/python3 .claude/skills/kb/kb-ontology/scripts/draw_ontology.py
"""
import os
import subprocess
import sys

import rdflib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
ONT = os.path.join(ROOT, "output/kb/ontology")
OUT_DOT = os.path.join(ROOT, "output/kb/ontology-map.dot")
OUT_SVG = os.path.join(ROOT, "output/kb/ontology-map.svg")
OUT_PNG = os.path.join(ROOT, "output/kb/ontology-map.png")
PNG_DPI = "150"

LP = rdflib.Namespace("https://w3id.org/lp/ont#")
LPC = rdflib.Namespace("https://w3id.org/lp/concept/")
OWL = rdflib.Namespace("http://www.w3.org/2002/07/owl#")
SKOS = rdflib.Namespace("http://www.w3.org/2004/02/skos/core#")
RDFS = rdflib.RDFS

# 층 배치. 클래스 로컬명 → (클러스터 키, 색)
LAYER = {
    "Act": "src", "Decree": "src", "Rule": "src",
    "Ordinance": "src", "AdminRule": "src", "LegalSource": "src",
    "ArticleWork": "art", "ArticleVersion": "art",
    "LawApplication": "app",
    "District": "adm", "Jurisdiction": "adm",
    "NormativeInformationEntity": "plan",
    "DistrictUnitPlan": "plan",
    "PlanState": "plan", "PlanningRule": "plan",
    "PlanDecisionEvent": "event", "PlanEstablishmentEvent": "event",
    "PlanChangeEvent": "event",
    "PlanningDocument": "doc", "Guideline": "doc",
    "GuidelineArticle": "doc", "TermDefinition": "doc",
    "Occurrence": "doc",
    "PlanElement": "elem",
    "CadastralRecord": "cad", "FacilityRecord": "cad", "LandRecord": "cad",
    "Assertion": "prob", "TypingAssertion": "prob", "RelationAssertion": "prob",
}

CLUSTER = [
    ("src", "법원(法源) — 위계", "#1a5490", "#e8f0f8"),
    ("art", "조문과 판본", "#1a5490", "#e8f0f8"),
    ("app", "적용 사례 — 사슬의 종착점", "#0b6b3a", "#e6f4ec"),
    ("plan", "법정계획 — 본체와 효력 상태", "#5b3f8c", "#f1ecf8"),
    ("space", "법정 계획구역", "#237a7a", "#e7f5f5"),
    ("event", "계획 결정 사건", "#8a4f22", "#f8eee7"),
    ("adm", "행정", "#555555", "#f2f2f2"),
    ("doc", "시행지침 문서", "#555555", "#f2f2f2"),
    ("elem", "계획 요소 (axis_B 9유형)", "#7a4b00", "#fdf2e0"),
    ("cad", "조서 — 사업 계통 (미구현)", "#888888", "#fafafa"),
    ("prob", "확률론 — 판정 진술 (추론 금지)", "#a13d00", "#fdeee6"),
]

# 간선으로 그릴 술어. 나머지는 노드 속성이라 생략한다.
EDGE_STYLE = {
    "delegates": ("위임", "#1a5490", "bold"),
    "mandates": ("강제", "#1a5490", "solid"),
    "authorizes": ("완화 허용", "#1a5490", "dashed"),
    "defines": ("정의", "#1a5490", "dotted"),
    "versionOf": ("판본", "#1a5490", "solid"),
    "appliedNorm": ("적용 규범", "#0b6b3a", "bold"),
    "inDistrict": ("성립 지구", "#0b6b3a", "solid"),
    "evidence": ("근거", "#0b6b3a", "bold"),
    "jurisdiction": ("관할", "#555555", "solid"),
    "ofPlanElement": ("대상 요소", "#555555", "solid"),
    "inSource": ("소속 법원", "#1a5490", "solid"),
    "inGuideline": ("소속 지침", "#555555", "solid"),
    "inArticle": ("소속 조항", "#555555", "solid"),
    "ofTerm": ("대상 용어", "#a13d00", "solid"),
    "subject": ("주어", "#a13d00", "solid"),
    "상위지자체": ("상위 지자체", "#555555", "solid"),
    "지정근거법": ("지정 근거법", "#1a5490", "bold"),
    "적용조례": ("적용 조례", "#1a5490", "solid"),
    "조례위임": ("조례 위임", "#1a5490", "dashed"),
    "사례지구": ("정의 지구", "#555555", "dotted"),
    "governsPlanningArea": ("규율 구역", "#237a7a", "bold"),
    "hasState": ("효력 상태", "#5b3f8c", "bold"),
    "stateOf": ("계획 본체", "#5b3f8c", "dashed"),
    "hasRule": ("계획 규칙", "#5b3f8c", "solid"),
    "expressedBy": ("문서 표현", "#555555", "solid"),
    "effectiveDuring": ("효력 기간", "#8a4f22", "solid"),
    "establishedBy": ("성립 사건", "#8a4f22", "bold"),
    "initiates": ("상태 시작", "#8a4f22", "solid"),
    "terminates": ("상태 종료", "#8a4f22", "dashed"),
}

# EDGE_STYLE 에 없어도 경고 없이 도식에서 빼는 ObjectProperty. 나머지 미등재는 stderr 로
# 경고한다 — 허용목록이 조용히 술어를 삼키는 것(finding 3)을 막기 위해서다.
#   delegates                    domain=range=ArticleWork 라 자기 루프. "위임 사슬"
#                                 절에서 Act→Decree→Rule/Ordinance 로 따로 그린다
#   conceptType                  prob 층 판정 진술. range 미선언(class-as-value punning)
#   classifiesAs/fromType/toType 어휘 계층 punning. domain=skos:Concept, range=owl:Class 라
#                                 이 도식이 그리는 클래스 계층(core.ttl 의 owl:Class) 노드가 아니다
SKIP_EDGES = {"conceptType", "delegates", "classifiesAs", "fromType", "toType"}


def load(*names):
    g = rdflib.Graph()
    for n in names:
        p = os.path.join(ONT, n)
        if os.path.exists(p):
            g.parse(p, format="turtle")
    return g


def local(u):
    return str(u).rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def esc(s):
    return str(s).replace('"', '\\"')


def main():
    g = load("core.ttl", "grounding.ttl")
    vocab = load("vocab-concept.ttl")

    classes = sorted({local(c) for c in g.subjects(rdflib.RDF.type, OWL.Class)})
    label = {}
    for c in g.subjects(rdflib.RDF.type, OWL.Class):
        for o in g.objects(c, RDFS.label):
            if getattr(o, "language", None) == "ko":
                label[local(c)] = str(o)

    # axis_B 유형별 용어 수 — vocab-concept.ttl 의 lp:termCount 가 정본
    counts = {}
    for con in vocab.subjects(rdflib.RDF.type, SKOS.Concept):
        cls = vocab.value(con, LP.classifiesAs)
        n = vocab.value(con, LP.termCount)
        if cls is not None and n is not None:
            counts[local(cls)] = int(n)

    L = []
    L.append("digraph ontology {")
    L.append('  graph [rankdir=TB, fontname="Helvetica", labelloc=t, fontsize=22,')
    L.append('         label="택지개발지구 지구단위계획 온톨로지\\n'
             '근거 사슬이 이어지면 결정론 · 끊기면 확률론    —    output/kb/ontology/*.ttl 에서 생성\\n ",')
    L.append('         splines=spline, nodesep=0.3, ranksep=0.7, bgcolor="white", compound=true];')
    L.append('  node  [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11,')
    L.append('         color="#888888", fillcolor="white", margin="0.14,0.08"];')
    L.append('  edge  [fontname="Helvetica", fontsize=9, color="#777777", arrowsize=0.7];')
    L.append("")

    for key, title, pen, fill in CLUSTER:
        members = [c for c in classes if LAYER.get(c) == key]
        if key == "elem":
            members = ["PlanElement"] + sorted(
                (c for c in classes if c in counts), key=lambda x: -counts[x])
        if not members:
            continue
        L.append(f'  subgraph cluster_{key} {{')
        L.append(f'    label="{title}"; fontsize=13; fontcolor="{pen}";')
        L.append(f'    style="rounded,filled"; color="{pen}"; fillcolor="{fill}"; penwidth=1.4;')
        for c in members:
            ko = label.get(c, c)
            if c in counts:
                txt = f"{ko}\\n{counts[c]:,}"
            elif c == "District":
                txt = f"{ko}\\n190"
            elif c == "Jurisdiction":
                txt = f"{ko}\\n64"
            elif c == "Guideline":
                txt = f"{ko}\\n189"
            elif c == "GuidelineArticle":
                txt = f"{ko}\\n19,316"
            elif c == "TermDefinition":
                txt = f"{ko}\\n6,027"
            elif c == "Occurrence":
                txt = f"{ko}\\n7,325"
            elif c == "PlanElement":
                txt = f"{ko}\\n1,062"
            else:
                txt = ko
            extra = ""
            if c in ("Act",):
                extra = ', fillcolor="#dce8f5"'
            if c in ("LawApplication",):
                extra = ', fillcolor="#c9e8d6", penwidth=2, color="#0b6b3a"'
            if c in ("FacilityRecord", "LandRecord"):
                extra = ', style="rounded,filled,dashed", fillcolor="#f5f5f5", fontcolor="#888888"'
            L.append(f'    "{c}" [label="{esc(txt)}"{extra}];')
        L.append("  }")
        L.append("")

    # subClassOf — 옅은 회색 실선
    L.append("  // 클래스 계층")
    for s, _, o in g.triples((None, RDFS.subClassOf, None)):
        a, b = local(s), local(o)
        if a in classes and b in classes:
            L.append(f'  "{b}" -> "{a}" [color="#bbbbbb", arrowhead=none, penwidth=0.8];')
    L.append("")

    # 술어 — domain → range.
    # delegates 는 domain·range 가 둘 다 ArticleWork 라 자기 루프가 된다. 위계 흐름으로
    # 따로 그리므로 여기서는 건너뛴다(위에서 name == "delegates" 로 조기 제외).
    # 다른 자기참조 술어(예: 상위지자체 Jurisdiction→Jurisdiction)는 delegates 와 달리
    # 대체 표현이 없으므로 self-loop 를 그대로 그린다 — d == r 을 이유로 건너뛰지 않는다.
    # set 순회 순서는 실행마다 달라진다. 정렬하지 않으면 같은 입력에 다른 바이트가 나와
    # 커밋된 그림이 재생성할 때마다 흔들린다.
    L.append("  // 술어 (domain → range)")
    seen = set()
    for p in sorted(set(g.subjects(rdflib.RDF.type, OWL.ObjectProperty)), key=local):
        name = local(p)
        if name == "delegates":
            continue
        if name not in EDGE_STYLE:
            if name not in SKIP_EDGES:
                print(f"경고: lp:{name} 이 EDGE_STYLE 에 없어 도식에서 빠진다"
                      " (의도적 생략이면 SKIP_EDGES 에 추가할 것)", file=sys.stderr)
            continue
        doms = [local(x) for x in g.objects(p, RDFS.domain)]
        rngs = [local(x) for x in g.objects(p, RDFS.range)]
        ko, color, style = EDGE_STYLE[name]
        for d in doms or ["?"]:
            for r in rngs or ["?"]:
                if d not in classes or r not in classes:
                    continue
                k = (d, r, name)
                if k in seen:
                    continue
                seen.add(k)
                pw = "2.0" if style == "bold" else "1.0"
                st = "solid" if style == "bold" else style
                L.append(f'  "{d}" -> "{r}" [label="{ko}", color="{color}", '
                         f'fontcolor="{color}", style={st}, penwidth={pw}];')

    # lp:inDistrict 는 domain 이 없다 — LawApplication·Occurrence·TermDefinition 셋이
    # 쓴다. domain 을 하나로 좁히면 RDFS 추론이 나머지를 그 클래스로 오분류한다.
    # lp:ofTerm 도 TermDefinition·TypingAssertion 양쪽이 써 같은 이유로 domain 이 없다.
    # 자동 추출할 수 없는 소비자별 간선을 명시한다.
    L.append("")
    L.append("  // domain 없는 공유 술어 — 소비자별 간선을 명시한다")
    L.append('  "LawApplication" -> "District" [label="성립 지구", color="#0b6b3a", '
             'fontcolor="#0b6b3a", penwidth=1.4];')
    L.append('  "Occurrence" -> "District" [label="출현 지구", color="#555555", '
             'fontcolor="#555555", penwidth=1.0];')
    L.append('  "TermDefinition" -> "District" [label="정의 지구", color="#555555", '
             'fontcolor="#555555", penwidth=1.0];')
    L.append('  "TermDefinition" -> "PlanElement" [label="대상 용어", color="#555555", '
             'fontcolor="#555555", penwidth=1.0];')
    L.append('  "TypingAssertion" -> "PlanElement" [label="대상 용어", color="#a13d00", '
             'fontcolor="#a13d00", penwidth=1.0];')

    L.append("")
    L.append("  // 위임 사슬 — 법률에는 규범값이 없고 시행령부터 실질이 시작된다")
    L.append('  "Act" -> "Decree" [label="위임", color="#1a5490", fontcolor="#1a5490", penwidth=2.0];')
    L.append('  "Decree" -> "Rule" [label="위임", color="#1a5490", fontcolor="#1a5490", penwidth=2.0];')
    L.append('  "Decree" -> "Ordinance" [label="위임", color="#1a5490", fontcolor="#1a5490", penwidth=2.0];')

    # 사슬이 위에서 아래로 읽히도록 가중치를 준다.
    # rank=same 은 클러스터와 충돌해 graphviz 가 죽으므로 쓰지 않는다 (실측).
    L.append("")
    L.append("  // 사슬을 세로로 당기는 보이지 않는 간선")
    for a, b in [("Ordinance", "ArticleWork"), ("ArticleVersion", "LawApplication"),
                 ("LawApplication", "Occurrence"), ("Occurrence", "PlanElement")]:
        L.append(f'  "{a}" -> "{b}" [style=invis, weight=10];')

    L.append("")
    L.append('  // 범례')
    L.append('  subgraph cluster_legend {')
    L.append('    label="읽는 법"; fontsize=12; fontcolor="#333333";')
    L.append('    style="rounded,filled"; color="#cccccc"; fillcolor="#fbfbfb"; margin=10;')
    L.append('    "legend" [shape=plaintext, style="", fillcolor="#fbfbfb", label=<')
    L.append('      <table border="0" cellborder="0" cellspacing="3">')
    L.append('      <tr><td align="left"><font point-size="10" color="#1a5490">'
             '━━ 파랑 = 결정론. 근거 사슬이 이어진다. OWL 추론 허용</font></td></tr>')
    L.append('      <tr><td align="left"><font point-size="10" color="#0b6b3a">'
             '━━ 초록 = 적용 사례. 사슬의 종착점</font></td></tr>')
    L.append('      <tr><td align="left"><font point-size="10" color="#5b3f8c">'
             '━━ 보라 = 법정계획의 본체·시점별 효력 상태</font></td></tr>')
    L.append('      <tr><td align="left"><font point-size="10" color="#237a7a">'
             '━━ 청록 = 법정 지구단위계획구역</font></td></tr>')
    L.append('      <tr><td align="left"><font point-size="10" color="#8a4f22">'
             '━━ 갈색 = 계획상태를 시작·종료하는 결정 사건</font></td></tr>')
    L.append('      <tr><td align="left"><font point-size="10" color="#a13d00">'
             '━━ 주황 = 확률론. 판정 진술. 추론 금지</font></td></tr>')
    L.append('      <tr><td align="left"><font point-size="10" color="#999999">'
             '─── 회색 얇은 선 = 클래스 계층 (rdfs:subClassOf)</font></td></tr>')
    L.append('      <tr><td align="left"><font point-size="10" color="#888888">'
             '점선 테두리 = 클래스만 선언, 인스턴스는 후속</font></td></tr>')
    L.append('      <tr><td align="left"><font point-size="10">'
             '숫자 = 실측 건수</font></td></tr>')
    L.append('      </table>>];')
    L.append("  }")
    L.append("}")

    dot = "\n".join(L) + "\n"
    os.makedirs(os.path.dirname(OUT_DOT), exist_ok=True)
    with open(OUT_DOT, "w", encoding="utf-8") as f:
        f.write(dot)

    # SVG 는 벡터라 확대해도 안 깨지고, PNG 는 미리보기·문서 삽입용이다. 둘 다 낸다.
    for fmt, out, extra in (("svg", OUT_SVG, []),
                            ("png", OUT_PNG, [f"-Gdpi={PNG_DPI}"])):
        r = subprocess.run(["dot", f"-T{fmt}"] + extra + [OUT_DOT, "-o", out],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr, file=sys.stderr)
            return 1

    print(f"클래스 {len(classes)}개 · 술어 간선 {len(seen)}개")
    for p in (OUT_DOT, OUT_SVG, OUT_PNG):
        print(f"  {os.path.relpath(p, ROOT)}  ({os.path.getsize(p):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
