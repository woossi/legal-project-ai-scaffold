"""환각 전수검사. canonical RDF term의 근거발췌와 값을 조례 원문에서 확인한다.

근거발췌 substring이 실패해도 값+단위 실재를 원문에서 재확인한다. 원문에는
`0.7배(…) 이상`처럼 값과 연산자 사이에 괄호가 끼므로, 값이 실재하면 재구성 표기
차이로 집계한다. 조문 IRI의 관할·계통·조문을 corpus와 대조하며 문자열 내부 필드에
의존하지 않는다.
"""
import glob
import os
import re
import sys
import urllib.parse
from fractions import Fraction

import rdflib


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
GRAPH = os.path.join(ROOT, "output/kb/norm/graph/det/norm-value")
LP = rdflib.Namespace("https://w3id.org/lp/ont#")
ID = "https://w3id.org/lp/id/"

sys.path.insert(0, os.path.join(ROOT, ".claude/skills/kb/kb-norm/scripts"))
import corpus                                              # noqa: E402


def norm(value):
    return re.sub(r"\s+", "", value or "")


def _article_parts(iri):
    value = urllib.parse.unquote(str(iri or ""))
    prefix = ID + "ordinance/"
    if not value.startswith(prefix):
        return None, None, None
    parts = value[len(prefix):].split("/", 2)
    if len(parts) != 3 or "@" not in parts[2]:
        return None, None, None
    article, _effective = parts[2].rsplit("@", 1)
    return parts[0], parts[1], article


def read_rows(graph_dir=GRAPH):
    """축별 TTL을 RDF로 읽어 환각 검사에 필요한 canonical 필드만 낸다."""
    for path in sorted(glob.glob(os.path.join(graph_dir, "*.ttl"))):
        graph = rdflib.Graph().parse(path, format="turtle")
        for subject in sorted(set(graph.subjects(rdflib.RDF.type, LP.NormStatement))):
            lc5, system, article = _article_parts(graph.value(subject, LP.근거조문))
            value = graph.value(subject, LP.값)
            unit = graph.value(subject, LP.단위)
            excerpt = graph.value(subject, LP.근거발췌)
            yield {
                "파일": os.path.basename(path), "lc5": lc5,
                "조례계통": system, "조문": article,
                "값": str(value) if value is not None else None,
                "단위": str(unit) if unit is not None else None,
                "근거발췌": str(excerpt) if excerpt is not None else None,
            }


def _source_articles():
    out = {}
    names = {}
    for doc in corpus.ordinance_docs():
        system, _ = corpus.ordinance_system(doc)
        lc5 = corpus.jurisdiction_code(doc.get("authority"))
        for _, label, text, _title in corpus.articles(doc):
            key = (lc5, system, label)
            out[key] = text
            names[key] = doc["official_name"]
    return out, names


def main(graph_dir=GRAPH):
    sources, names = _source_articles()
    bad = count = soft = 0
    for row in read_rows(graph_dir):
        count += 1
        key = (row["lc5"], row["조례계통"], row["조문"])
        source = sources.get(key)
        if source is None:
            bad += 1
            print("  조문없음", names.get(key), row["조문"])
            continue
        source_norm = norm(source)
        if norm(row["근거발췌"]) in source_norm:
            continue
        value, unit = row["값"], row["단위"]
        ok = bool(value and unit and norm(f"{value}{unit}") in source_norm)
        if not ok and value and unit and "." not in value:
            ok = norm(f"{value}.0{unit}") in source_norm
        if not ok and value and unit == "배":
            try:
                fraction = Fraction(value).limit_denominator(20)
                ok = norm(f"{fraction.denominator}분의{fraction.numerator}") in source_norm
            except Exception:
                pass
        if ok:
            soft += 1
        else:
            bad += 1
            print("  **미실재**", names.get(key), row["조문"], value, unit,
                  "|", row["근거발췌"])
    print(f"명제 {count} | 원문 미확인 {bad} | 재구성표기 차이(값 실재 확인) {soft}")
    if count == 0:
        print(f"명제 0건 — 산출물을 못 찾았다: {graph_dir}")
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
