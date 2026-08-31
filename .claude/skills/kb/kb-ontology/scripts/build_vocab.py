"""taxonomy.json 의 axis_B 를 SKOS 어휘로 옮긴다.

기준서 본문을 손으로 옮기면 게이트 5(taxonomy 와 1:1·건수 일치)가 옮겨 적는 실수로
깨진다. 생성해서 그 위험을 없앤다.

    python3 build_vocab.py            # output/kb/ontology/vocab-concept.ttl 갱신
    python3 build_vocab.py --check    # 갱신 없이 어긋남만 확인
"""
import argparse
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..")
TAXONOMY = os.path.join(ROOT, "output/legal/word/taxonomy.json")
OUT = os.path.join(ROOT, "output/kb/ontology/vocab-concept.ttl")

# axis_B 유형명 → lp: 클래스 로컬명.
# '기타' 104건은 전량 classification_confidence=low 이므로 클래스를 배정하지 않는다.
CLASS_OF = {
    "공간객체": "SpatialObject",
    "규제선·규제구간": "RegulationLine",
    "규제지표": "RegulationIndex",
    "용도·시설": "UseAndFacility",
    "건축요소": "BuildingElement",
    "공공·경관요소": "PublicScapeElement",
    "환경·에너지": "EnvEnergy",
    "계획개념·도시상": "PlanningConcept",
    "절차·주체": "ProcessActor",
    "기타": None,
}

HEADER = """@prefix lp:   <https://w3id.org/lp/ont#> .
@prefix lpc:  <https://w3id.org/lp/concept/> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

##  taxonomy.json 의 axis_B 에서 생성했다. 손으로 고치지 않는다.
##  갱신: python3 .claude/skills/kb/kb-ontology/scripts/build_vocab.py

lpc:axis_B a skos:ConceptScheme ;
    skos:prefLabel "개념유형 (axis_B · PRIMARY)"@ko .
"""


def _lit(s):
    """Turtle 긴 문자열 리터럴. 역슬래시와 삼중따옴표를 이스케이프한다.

    taxonomy.json 의 '경계사례'는 문자열이 아니라 리스트로 온다(사례가 여럿).
    리스트면 항목을 개행으로 이어붙인다 — 구분자만 넣을 뿐 각 항목의 원문
    문자는 자르거나 바꾸지 않는다.
    """
    if isinstance(s, list):
        s = "\n".join(s)
    s = (s or "").replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return f'"""{s}"""@ko'


def build(taxonomy):
    axis = taxonomy["axis_B"]
    counts = axis["값별_용어수"]
    out = [HEADER]
    for e in axis["유형별_기준서"]:
        name = e["값"]
        if name not in CLASS_OF:
            raise ValueError(f"CLASS_OF 에 없는 유형: {name!r} — 매핑을 먼저 추가한다")
        lines = [
            f"lpc:{name} a skos:Concept ;",
            f"    skos:inScheme lpc:axis_B ;",
            f'    skos:prefLabel "{name}"@ko ;',
            f"    skos:definition {_lit(e.get('정의'))} ;",
            f"    skos:scopeNote {_lit(e.get('포함기준'))} ;",
            f"    skos:note {_lit(e.get('배제기준'))} ;",
            f"    skos:example {_lit(e.get('경계사례'))} ;",
            f'    lp:termCount "{counts[name]}"^^xsd:integer',
        ]
        cls = CLASS_OF[name]
        if cls:
            lines.append(f"    ;\n    lp:classifiesAs lp:{cls}")
        lines.append(" .")
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="갱신 없이 어긋남만 확인")
    args = ap.parse_args()

    with open(TAXONOMY, encoding="utf-8") as f:
        ttl = build(json.load(f))

    if args.check:
        cur = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if cur != ttl:
            print("vocab-concept.ttl 이 taxonomy.json 과 어긋난다", file=sys.stderr)
            return 1
        print("일치")
        return 0

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(ttl)
    print(f"{OUT} 갱신")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
