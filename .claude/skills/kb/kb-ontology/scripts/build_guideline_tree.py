#!/usr/bin/env python3
"""지침 문서를 조문 트리로 세우고 용어 정의 진술을 매단다.

조문 노드는 md h4 헤딩에서 뽑는다. 헤딩이 아닌 조 표제는 doc_definitions.articles 를
역인덱스로 써서 승격한다 — 일반 정규식으로 평문을 훑으면 괄호만 있는 줄이 전부
조문으로 잡힌다.

출력: output/kb/graph/det/guideline.ttl, output/kb/reports/_guideline_tree.json
설계: docs/superpowers/specs/2026-08-11-용어-컴포넌트-연결-design.md
"""
import collections
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mint_iri as M                                          # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
MD_DIR = os.path.join(ROOT, "output/legal/markdown")
DOC_DEFS = os.path.join(ROOT, "output/legal/word/doc_definitions.json")
OUT_TTL = os.path.join(ROOT, "output/kb/graph/det/guideline.ttl")
OUT_REPORT = os.path.join(ROOT, "output/kb/reports/_guideline_tree.json")

# 조문 표제. extract_norms.py:45 와 같은 판정을 쓴다 — 괄호를 무조건 표제로 보면
# "…높이(3층이상)" 같은 부가설명이 조문으로 섞인다.
ARTICLE_TITLE = re.compile(r"^제\s*\d+\s*조(?:의\s?\d+)?\s*[\(（]\s*([^)）]{2,40}?)\s*[\)）]")
H4 = re.compile(r"^#{4}\s*(.+?)\s*$")

# doc_definitions 는 정의 조항이 있는 161개 문서만 담는다 (전체 189개 중). 나머지 28개는
# 지구번호를 frontmatter 에서 읽는다 — 28/28 실측으로 파싱 가능함을 확인했다.
DSTRC_FM = re.compile(r'^지구번호:\s*"?([0-9]{5}[A-Z]{2}[0-9]{7})"?\s*$', re.M)


def parse_headings(text):
    """md 본문에서 h4 조문 헤딩을 순서대로 뽑는다.

    seq 는 문서 내 조문 출현 순번, dup 은 같은 표제가 몇 번째로 나왔는가다.
    dup 이 IRI 를 구성하고 seq 는 속성으로만 남는다.
    """
    out = []
    seen = collections.Counter()
    for i, line in enumerate(text.split("\n"), start=1):
        m = H4.match(line.rstrip())
        if not m:
            continue
        title = m.group(1)
        if not ARTICLE_TITLE.match(title):
            continue
        seen[title] += 1
        out.append({"title": title, "raw": line.rstrip(), "line": i,
                    "seq": len(out) + 1, "dup": seen[title]})
    return out


def _key(s):
    """표제 대조 키. 공백·중점만 지운다 — mint_iri.normalize_article_title 과 같은 관례다."""
    if not s or not s.strip():
        return ""
    try:
        return M.normalize_article_title(s)
    except ValueError:
        return ""


def classify_article(title, headings, text):
    """조문 표제가 현재 md 어디에 근거를 갖는지 가른다.

    heading             — h4 조문 헤딩에 있다
    promoted            — h4 조문 헤딩은 아니나 현재 md 어딘가에 그 표제가 있다
                          (평문 줄, 또는 h4 가 아닌 헤딩)
    definition-restored — 현재 md 어디에도 없다. 절삭으로 사라진 정의 조항이다
    """
    k = _key(title)
    for h in headings:
        if _key(h["title"]) == k:
            return "heading", h["seq"]
    for line in text.split("\n"):
        s = line.strip().lstrip("-•*#").strip()
        if s and _key(s) == k:
            return "promoted", None
    return "definition-restored", None


_TEXT_KEY = re.compile(r"[^가-힣A-Za-z0-9%]")


def text_key(s):
    """문언 비교 키. 공백·문장부호를 지운다.

    의미 동일성 판정이 아니다 — 문자열 유사도로는 판정할 수 없음이 실측으로 확인됐다
    (건축한계선 140개 지구가 유사도 0.70 에서 7종으로 갈리나 원문은 6종이 같은 의미).
    여기서는 문언이 몇 종인지만 센다.
    """
    return _TEXT_KEY.sub("", s or "")


def aggregate_terms(records):
    """용어 표목별로 사례 지구와 집계 수치를 모은다."""
    acc = collections.defaultdict(
        lambda: {"label": None, "districts": set(), "stmt_count": 0, "texts": set()})
    for x in records:
        a = acc[x["term_id"]]
        if a["label"] is None:
            a["label"] = x["term"]
        a["districts"].add(x["dstrcAppnNo"])
        a["stmt_count"] += 1
        a["texts"].add(text_key(x.get("definition")))
    return {tid: {"label": v["label"],
                  "districts": sorted(v["districts"]),
                  "stmt_count": v["stmt_count"],
                  "text_kinds": len(v["texts"])}
            for tid, v in sorted(acc.items())}


def ttl_escape(s):
    """Turtle 짧은 리터럴용 이스케이프."""
    return (s.replace("\\", "\\\\").replace('"', '\\"')
             .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))


def rel(iri):
    """@base 기준 상대 IRI. build_boundary.py 와 같은 방식이다."""
    return iri[len(M.ID):] if iri.startswith(M.ID) else iri


def ttl_header():
    return (
        "@prefix lp:   <https://w3id.org/lp/ont#> .\n"
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
        "@base <https://w3id.org/lp/id/> .\n"
        "\n"
        "##  지침 조문 트리와 용어 정의 진술.\n"
        "##  docs/superpowers/specs/2026-08-11-용어-컴포넌트-연결-design.md\n"
        "##  생성: build_guideline_tree.py (정렬 순회 · 멱등). 손으로 고치지 않는다.\n"
        "\n")


def main():
    with open(DOC_DEFS, encoding="utf-8") as f:
        records = json.load(f)["records"]

    # 문서 집합은 md 전건(189개)에서 뽑는다. doc_definitions 는 정의 조항이 있는 161개
    # 문서만 담으므로 그 집합으로 좁히면 나머지 28개 지구의 조문 노드가 통째로 빠진다
    # (design.md:271 "조문 노드는 189개 전건 발급되므로 트리 자체는 성립한다").
    doc_paths = sorted(os.path.relpath(p, ROOT)
                        for p in glob.glob(os.path.join(MD_DIR, "*", "*.md")))
    have_defs = set(x["source_file"] for x in records)
    no_defs = [p for p in doc_paths if p not in have_defs]

    # source_file -> dstrcAppnNo 사전 조회. 161개는 doc_definitions 에서, 나머지 28개는
    # md frontmatter 의 지구번호로 보충한다.
    dstrc_by_path = {x["source_file"]: x["dstrcAppnNo"] for x in records}

    # 문서별 파싱은 한 번만 한다.
    md_cache = {}
    for path in doc_paths:
        full = os.path.join(ROOT, path) if not os.path.isabs(path) else path
        if not os.path.exists(full):
            md_cache[path] = None
            continue
        with open(full, encoding="utf-8", errors="replace") as f:
            text = f.read()
        md_cache[path] = (parse_headings(text), text)
        if path not in dstrc_by_path:
            dstrc = _dstrc_from_frontmatter(text)
            if dstrc is not None:
                dstrc_by_path[path] = dstrc

    # 조문 노드 — md h4 전건 + doc_definitions 가 가리키는 표제
    articles = {}          # iri -> dict
    missing_md = []
    for path in doc_paths:
        if md_cache[path] is None:
            missing_md.append(path)
            continue
        headings, _ = md_cache[path]
        dstrc = dstrc_by_path.get(path)
        if dstrc is None:
            continue
        for h in headings:
            iri = M.guideline_article(dstrc, h["title"], h["dup"])
            articles[iri] = {"dstrc": dstrc, "label": h["title"], "raw": h["raw"],
                             "origin": "heading", "seq": h["seq"]}

    # 정의 진술과 그것이 붙을 조항
    stmts, orphan = [], []
    variant = collections.Counter()
    for x in sorted(records, key=lambda r: (r["dstrcAppnNo"], r["term_id"],
                                            r.get("variant_index") or 0)):
        path, dstrc = x["source_file"], x["dstrcAppnNo"]
        if md_cache.get(path) is None:
            orphan.append({"사유": "md 없음", "지구": dstrc, "용어": x["term_id"]})
            continue
        headings, text = md_cache[path]
        titles = x.get("articles") or []
        if not titles:
            orphan.append({"사유": "articles 없음", "지구": dstrc, "용어": x["term_id"]})
            continue
        # 한 진술이 두 조문에서 선언되는 경우가 66건 있다 — 전부 잇는다.
        art_iris = []
        for title in titles:
            origin, seq = classify_article(title, headings, text)
            dup = _dup_for(title, headings, origin)
            art_iri = M.guideline_article(dstrc, title, dup)
            if art_iri not in articles:
                articles[art_iri] = {"dstrc": dstrc, "label": title, "raw": title,
                                     "origin": origin, "seq": seq}
            if art_iri not in art_iris:
                art_iris.append(art_iri)
        variant[(dstrc, x["term_id"])] += 1
        stmts.append({
            "iri": M.term_def(dstrc, x["term_id"], variant[(dstrc, x["term_id"])]),
            "term": x["term_id"], "articles": sorted(art_iris), "dstrc": dstrc,
            "text": x.get("definition") or "",
            "raw": x.get("definition_source_text") or "",
        })

    terms = aggregate_terms(records)

    # ---- 직렬화 (정렬 순회로 멱등을 보장한다)
    # Guideline 은 articles 가 아니라 문서 189개 전건에서 뽑는다. h4 헤딩도 정의진술도
    # 없는 22개 문서(정의조항_없음 28건 중 일부)는 articles 에 전혀 등록되지 않으므로
    # articles 기준으로 파생하면 지침수가 189보다 적게 나온다.
    out = [ttl_header(), "##  시행지침 문서\n\n"]
    for dstrc in sorted(set(dstrc_by_path.values())):
        out.append(f"<{rel(M.guideline(dstrc))}> a lp:Guideline ;\n"
                   f"    lp:inDistrict <{rel(M.district(dstrc))}> .\n\n")

    out.append("##  지침 조항\n\n")
    for iri in sorted(articles):
        a = articles[iri]
        out.append(f"<{rel(iri)}> a lp:GuidelineArticle ;\n"
                   f'    rdfs:label "{ttl_escape(a["label"])}" ;\n'
                   f'    lp:sourceText "{ttl_escape(a["raw"])}" ;\n'
                   f"    lp:inGuideline <{rel(M.guideline(a['dstrc']))}> ;\n"
                   f'    lp:articleOrigin "{a["origin"]}"')
        if a["seq"] is not None:
            out.append(f' ;\n    lp:articleSeq {a["seq"]}')
        out.append(" .\n\n")

    out.append("##  정의 진술\n\n")
    for s in sorted(stmts, key=lambda r: r["iri"]):
        arts = " , ".join(f"<{rel(a)}>" for a in s["articles"])
        out.append(f"<{rel(s['iri'])}> a lp:TermDefinition ;\n"
                   f"    lp:ofTerm <{rel(M.term(s['term']))}> ;\n"
                   f"    lp:inArticle {arts} ;\n"
                   f"    lp:inDistrict <{rel(M.district(s['dstrc']))}> ;\n"
                   f'    lp:definitionText "{ttl_escape(s["text"])}" ;\n'
                   f'    lp:sourceText "{ttl_escape(s["raw"])}" .\n\n')

    out.append("##  용어 표목\n\n")
    for tid in sorted(terms):
        t = terms[tid]
        ds = " , ".join(f"<{rel(M.district(d))}>" for d in t["districts"])
        out.append(f"<{rel(M.term(tid))}> a lp:PlanElement ;\n"
                   f'    rdfs:label "{ttl_escape(t["label"])}" ;\n'
                   f"    lp:사례지구 {ds} ;\n"
                   f"    lp:지구수 {len(t['districts'])} ;\n"
                   f"    lp:정의진술수 {t['stmt_count']} ;\n"
                   f"    lp:문언종수 {t['text_kinds']} .\n\n")

    os.makedirs(os.path.dirname(OUT_TTL), exist_ok=True)
    with open(OUT_TTL, "w", encoding="utf-8") as f:
        f.write("".join(out))

    origins = collections.Counter(a["origin"] for a in articles.values())
    report = {
        "생성스크립트": "scripts/build_guideline_tree.py",
        "조항수": len(articles),
        "출처분포": dict(sorted(origins.items())),
        "정의진술수": len(stmts),
        "표목수": len(terms),
        "지침수": len(set(dstrc_by_path.values())),
        "md_없음": sorted(missing_md),
        "정의조항_없음": sorted(dstrc_by_path[p] for p in no_defs if p in dstrc_by_path),
        "결손": orphan[:200],
        "결손수": len(orphan),
        "조항_위치대조": sorted(
            ({"iri": rel(i), "label": a["label"], "dup": rel(i).rsplit("-", 1)[-1],
              "seq": a["seq"]} for i, a in articles.items() if a["seq"] is not None),
            key=lambda r: r["iri"])[:500],
    }
    os.makedirs(os.path.dirname(OUT_REPORT), exist_ok=True)
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
        f.write("\n")

    print(f"조항 {len(articles):,} · 정의진술 {len(stmts):,} · 표목 {len(terms):,}")
    print(f"출처 {dict(sorted(origins.items()))}")
    print(f"결손 {len(orphan):,}")
    return 0


def _dstrc_from_frontmatter(text):
    """md frontmatter 의 지구번호를 읽는다. doc_definitions 에 없는 28개 문서용 폴백이다."""
    m = DSTRC_FM.search(text)
    return m.group(1) if m else None


def _dup_for(title, headings, origin):
    """heading 이면 그 헤딩의 동명순번, 아니면 1 이다.
    promoted·restored 는 문서 내 반복이 관측되지 않으므로 1 로 둔다 — 게이트가 충돌을 잡는다."""
    if origin != "heading":
        return 1
    k = _key(title)
    for h in headings:
        if _key(h["title"]) == k:
            return h["dup"]
    return 1


if __name__ == "__main__":
    sys.exit(main())
