"""실제 지구 하나로 인스턴스 그래프를 만들어 온톨로지가 도는지 보인다.

TBox 만으로는 클래스·술어가 실제 데이터에 걸리는지 알 수 없다. 이 스크립트는
`output/legal/` 실데이터에서 한 지구·한 용어를 뽑아 ABox 를 조립하고 그림으로 낸다.

**이것은 사례이지 산출물이 아니다.** graph/det 를 채우는 것은 스펙 ③의 일이다.

    .venv_kb/bin/python3 .claude/skills/kb/kb-ontology/scripts/build_example.py
"""
import collections
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mint_iri as M
import normalize_law as N
import resolve_asof as R

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
CASE_TTL = os.path.join(HERE, "..", "case", "사례_강일_공개공지.ttl")
OUT_DOT = os.path.join(ROOT, "output/kb/ontology-instance-example.dot")
OUT_PNG = os.path.join(ROOT, "output/kb/ontology-instance-example.png")
OUT_SVG = os.path.join(ROOT, "output/kb/ontology-instance-example.svg")

DSTRC = "11740DA2010001"      # 강일 도시개발구역 (서울 강동구, 도시개발법)
TERM = "T0020"                # 공개공지 — 건축법·시행령·조례 3단 사슬을 다 가진 용어
SEOUL = "1100000000"          # 서울특별시. 건축조례의 주체는 자치구가 아니라 광역이다


def load():
    terms = {t["id"]: t for t in
             json.load(open(os.path.join(ROOT, "output/legal/word/terms.json"),
                            encoding="utf-8"))["terms"]}
    meta = {d["dstrcAppnNo"]: d for d in
            json.load(open(os.path.join(ROOT, "output/legal/시행지침/meta.json"),
                           encoding="utf-8"))["districts"]}
    return terms, meta


def md_text(d):
    """terms.json 의 file 필드는 구경로라 쓰지 않는다. 규약으로 유도한다."""
    p = os.path.join(ROOT, "output/legal/markdown",
                     d["region"].strip(), d["dstrcNm"].strip() + ".md")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else None


def build():
    terms, meta = load()
    t, d = terms[TERM], meta[DSTRC]
    asof = R.resolve(DSTRC, md_text(d))

    occ_idx = next(i for i, o in enumerate(t["occurrences"])
                   if o["dstrcAppnNo"] == DSTRC)
    occ = t["occurrences"][occ_idx]

    # 이 용어가 인용한 법령 중 해석된 것만. 위계별로 하나씩 고른다.
    resolved = [N.normalize(c) for c in t["cited_laws"]]
    resolved = [r for r in resolved if r["status"] == "resolved" and r["article"]]

    def pick(pred):
        """해당 위계에서 가장 많이 인용된 조문을 고른다.

        첫 매치를 고르면 안 된다 — 공개공지의 근거는 건축법 제43조인데 같은 용어가
        제67조·제58조도 인용해서, 순서대로 집으면 엉뚱한 조문이 뽑힌다.
        인용 빈도가 그 용어의 대표 근거를 가리킨다.
        """
        cand = [r for r in resolved
                if pred(r["name"]) and "동법" not in (r["article"] or "")]
        if not cand:
            return None
        freq = collections.Counter((r["name"], r["article"]) for r in cand)
        (name, article), _ = freq.most_common(1)[0]
        return next(r for r in cand
                    if r["name"] == name and r["article"] == article)

    act = pick(lambda n: n == "건축법")
    dec = pick(lambda n: n == "건축법시행령")
    ordn = pick(lambda n: n == "서울특별시건축조례")

    return dict(term=t, district=d, occ=occ, occ_idx=occ_idx,
                asof=asof, act=act, dec=dec, ordn=ordn,
                unresolved=[N.normalize(c) for c in t["cited_laws"]
                            if N.normalize(c)["status"] == "unresolved"])


def ttl(x):
    t, d, occ, asof = x["term"], x["district"], x["occ"], x["asof"]
    act, dec, ordn = x["act"], x["dec"], x["ordn"]
    L = ["@prefix lp:  <https://w3id.org/lp/ont#> .",
         "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
         "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
         "@base <https://w3id.org/lp/id/> .",
         "",
         "##  실제 지구 하나로 조립한 인스턴스 사례. 스펙 ③이 만들 그래프의 미리보기다.",
         f"##  지구 {DSTRC} {d['dstrcNm'].strip()} · 용어 {TERM} {t['term']}",
         "##  시행일(@YYYYMMDD)은 스펙 ②가 연혁을 수집해야 채워지므로 여기서는 비운다.",
         ""]

    L += [f"<district/{DSTRC}> a lp:District ;",
          f'    lp:code "{DSTRC}" ;',
          f'    rdfs:label "{d["dstrcNm"].strip()}"@ko ;',
          f"    lp:jurisdiction <gov/{d['lcCode'][:5]}> ;",
          f'    lp:지정당시코드 "{DSTRC[:5]}" ;',
          f'    lp:사업기간 "{d["bsnsOpertnPd"]}" .', "",
          f"<gov/{d['lcCode'][:5]}> a lp:Jurisdiction ;",
          f'    rdfs:label "lcCode {d["lcCode"][:5]}"@ko .', "",
          f"<gov/{SEOUL[:5]}> a lp:Jurisdiction ;",
          f'    rdfs:label "서울특별시"@ko .', ""]

    for r, cls, note in ((act, "Act", "위임만 담는다. 규범값 없음"),
                         (dec, "Decree", "규범값이 여기서 시작한다"),
                         (ordn, "Ordinance", "구체 한도는 지자체가 정한다")):
        if not r:
            continue
        L += [f"<statute/{r['name']}> a lp:{cls} ;",
              f'    rdfs:label "{r["name"]}"@ko ;',
              f'    rdfs:comment "{note}"@ko .', "",
              f"<statute/{r['name']}/{r['article']}> a lp:ArticleWork ;",
              f"    lp:inSource <statute/{r['name']}> ;",
              f'    lp:sourceText "{r["raw"]}" .', ""]

    if act and dec:
        L += [f"<statute/{act['name']}/{act['article']}> "
              f"lp:delegates <statute/{dec['name']}/{dec['article']}> .", ""]
    if dec and ordn:
        L += [f"<statute/{dec['name']}/{dec['article']}> "
              f"lp:delegates <statute/{ordn['name']}/{ordn['article']}> .", ""]

    L += [f"<term/{TERM}> a lp:PlanElement ;",
          f'    lp:code "{TERM}" ;',
          f'    rdfs:label "{t["term"]}"@ko ;',
          f'    lp:sourceText "{t["term"]}" .', "",
          f"<occ/{TERM}-{x['occ_idx']:04d}> a lp:Occurrence ;",
          f"    lp:ofPlanElement <term/{TERM}> ;",
          f"    lp:inDistrict <district/{DSTRC}> ;",
          f'    lp:sourceText "{occ["article"]}" .', ""]

    if dec:
        L += [f"<application/{DSTRC}/{dec['name']}/{dec['article']}> a lp:LawApplication ;",
              f"    lp:inDistrict <district/{DSTRC}> ;",
              f"    lp:evidence <occ/{TERM}-{x['occ_idx']:04d}> ;",
              f'    lp:asOfYear "{asof["asOfYear"]}"^^xsd:gYear ;',
              f'    lp:temporalBasis "{asof["basis"]}" ;',
              f'    lp:temporalPrecision "{asof["precision"]}" ;',
              f'    rdfs:comment "lp:appliedNorm 은 조문 판본을 가리켜야 하는데 '
              f'판본은 스펙 ②의 연혁 수집이 있어야 발급된다. 그때까지 비운다."@ko .', ""]

    # 확률 층 — 유형 배정은 판정이므로 결정론과 섞지 않는다
    c = t["classification"]
    L += [f"<typing/{TERM}> a lp:TypingAssertion ;",
          f"    lp:ofTerm <term/{TERM}> ;",
          f'    lp:conceptType lp:PublicScapeElement ;',
          f'    lp:confidence "{c["classification_confidence"]}" .', ""]
    return "\n".join(L) + "\n"


def dot(x):
    t, d, occ, asof = x["term"], x["district"], x["occ"], x["asof"]
    act, dec, ordn = x["act"], x["dec"], x["ordn"]
    nun = len(x["unresolved"])

    def n(id_, label, fill, color, extra=""):
        return f'  "{id_}" [label="{label}", fillcolor="{fill}", color="{color}"{extra}];'

    L = ["digraph example {",
         '  graph [rankdir=TB, fontname="Helvetica", labelloc=t, fontsize=19,',
         f'         label="온톨로지가 실제로 걸리는 모습  —  {d["dstrcNm"].strip()} '
         f'({DSTRC}) 의 「{t["term"]}」\\n'
         f'클래스와 술어가 실데이터에 어떻게 배선되는가    ·    '
         f'.claude/skills/kb/kb-ontology/scripts/build_example.py 가 생성\\n ",',
         '         nodesep=0.3, ranksep=0.5, bgcolor="white", splines=spline];',
         '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10,',
         '        margin="0.16,0.09"];',
         '  edge [fontname="Helvetica", fontsize=9, arrowsize=0.7];', ""]

    L += ["  subgraph cluster_chain {",
          '    label="결정론 — 근거 사슬 (OWL 추론 허용)"; fontsize=12; fontcolor="#1a5490";',
          '    style="rounded,filled"; color="#1a5490"; fillcolor="#eef4fa"; penwidth=1.5;']
    if act:
        L.append(n("act", f"lp:Act\\n{act['name']} {act['article']}\\n"
                          f"— 위임만. 규범값 없음", "#dce8f5", "#1a5490"))
    if dec:
        L.append(n("dec", f"lp:Decree\\n{dec['name']}\\n{dec['article']}\\n"
                          f"— 규범값이 여기서 시작", "#c9dcf0", "#1a5490", ", penwidth=1.8"))
    if ordn:
        L.append(n("ord", f"lp:Ordinance\\n{ordn['name']}\\n{ordn['article']}\\n"
                          f"— 구체 한도는 지자체가", "#c9dcf0", "#1a5490"))
    L.append("  }")
    L.append("")

    L += ['  "app" [label="lp:LawApplication\\n'
          f'적용 사례\\n{DSTRC} × {dec["name"] if dec else "?"} {dec["article"] if dec else ""}",'
          ' shape=box, style="rounded,filled", fillcolor="#c9e8d6", color="#0b6b3a",'
          ' penwidth=2.2, fontsize=11];', ""]

    L += ["  subgraph cluster_doc {",
          '    label="시행지침 원문"; fontsize=11; fontcolor="#555555";',
          '    style="rounded,filled"; color="#aaaaaa"; fillcolor="#f4f4f4";',
          n("occ", f"lp:Occurrence\\n{TERM}-{x['occ_idx']:04d}\\n"
                   f"{occ['article']}", "white", "#888888"),
          n("term", f"lp:PlanElement\\n{t['term']} ({TERM})", "white", "#888888"),
          "  }", ""]

    L += ["  subgraph cluster_adm {",
          '    label="행정"; fontsize=11; fontcolor="#555555";',
          '    style="rounded,filled"; color="#aaaaaa"; fillcolor="#f4f4f4";',
          n("dist", f"lp:District\\n{d['dstrcNm'].strip()}\\n{DSTRC}\\n"
                    f"기준 {asof['asOfYear']}년 ({asof['basis']}·{asof['precision']})",
            "white", "#888888"),
          n("gov", f"lp:Jurisdiction\\nlcCode {d['lcCode'][:5]}", "white", "#888888"),
          n("gov2", "lp:Jurisdiction\\n서울특별시 (11000)", "#fff8e0", "#c08000",
            ', style="rounded,filled,dashed"'),
          "  }", ""]

    L += ["  subgraph cluster_prob {",
          '    label="확률론 — 판정 (추론 금지)"; fontsize=11; fontcolor="#a13d00";',
          '    style="rounded,filled"; color="#a13d00"; fillcolor="#fdeee6";',
          n("typ", f"lp:TypingAssertion\\n공공·경관요소\\n"
                   f"confidence={t['classification']['classification_confidence']}",
            "white", "#a13d00"),
          "  }", ""]

    B, G, K, O = "#1a5490", "#0b6b3a", "#555555", "#a13d00"
    L += [f'  "act" -> "dec" [label="lp:delegates 위임", color="{B}", fontcolor="{B}", penwidth=2.0];',
          f'  "dec" -> "ord" [label="lp:delegates 위임", color="{B}", fontcolor="{B}", penwidth=2.0];',
          f'  "app" -> "dec" [label="lp:appliedNorm 적용 규범", color="{G}", fontcolor="{G}", penwidth=2.2];',
          f'  "app" -> "dist" [label="lp:inDistrict 성립 지구", color="{G}", fontcolor="{G}", penwidth=1.6];',
          f'  "app" -> "occ" [label="lp:evidence 근거", color="{G}", fontcolor="{G}", penwidth=2.2];',
          f'  "occ" -> "term" [label="lp:ofPlanElement", color="{K}", fontcolor="{K}"];',
          f'  "occ" -> "dist" [label="lp:inDistrict", color="{K}", fontcolor="{K}"];',
          f'  "dist" -> "gov" [label="lp:jurisdiction 관할", color="{K}", fontcolor="{K}"];',
          f'  "typ" -> "term" [label="lp:ofTerm", color="{O}", fontcolor="{O}"];',
          f'  "gov" -> "gov2" [label="상위 지자체 — 술어 없음", color="#c08000",'
          f' fontcolor="#c08000", style=dashed];',
          f'  "ord" -> "gov2" [label="조례 주체", color="#c08000", fontcolor="#c08000", style=dashed];',
          ""]

    L += ["  subgraph cluster_note {",
          '    label="이 사례가 드러낸 것"; fontsize=11; fontcolor="#333333";',
          '    style="rounded,filled"; color="#cccccc"; fillcolor="#fbfbfb";',
          '    "note" [shape=plaintext, style="", fillcolor="#fbfbfb", label=<',
          '      <table border="0" cellborder="0" cellspacing="3">',
          f'      <tr><td align="left"><font point-size="10">① 사슬은 실재한다 — '
          f'건축법 제43조 → 시행령 제27조의2 → 서울특별시건축조례 제26조</font></td></tr>',
          f'      <tr><td align="left"><font point-size="10" color="#a13d00">'
          f'② 판본(@시행일)이 비어 있다 — 스펙 ②의 연혁 수집이 있어야 채워진다</font></td></tr>',
          f'      <tr><td align="left"><font point-size="10" color="#c08000">'
          f'③ 지자체 위계 술어가 없다 — 지구 관할은 강동구(11740)인데 건축조례 주체는 '
          f'서울특별시(11000)다</font></td></tr>',
          f'      <tr><td align="left"><font point-size="10" color="#a13d00">'
          f'④ 이 용어의 인용 {len(t["cited_laws"])}건 중 {nun}건이 「동법 시행령」류라 격리됐다 — '
          f'철자된 3건이 있어 사슬은 성립한다</font></td></tr>',
          '      </table>>];',
          "  }", "}"]
    return "\n".join(L) + "\n"


def main():
    x = build()
    os.makedirs(os.path.dirname(CASE_TTL), exist_ok=True)
    with open(CASE_TTL, "w", encoding="utf-8") as f:
        f.write(ttl(x))
    with open(OUT_DOT, "w", encoding="utf-8") as f:
        f.write(dot(x))
    for fmt, out, extra in (("svg", OUT_SVG, []), ("png", OUT_PNG, ["-Gdpi=150"])):
        r = subprocess.run(["dot", f"-T{fmt}"] + extra + [OUT_DOT, "-o", out],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr, file=sys.stderr)
            return 1
    print(f"지구 {DSTRC} · 용어 {TERM} {x['term']['term']}")
    print(f"  사슬  {x['act']['name']} {x['act']['article']}"
          f" → {x['dec']['name']} {x['dec']['article']}"
          f" → {x['ordn']['name']} {x['ordn']['article']}")
    print(f"  격리  {len(x['unresolved'])}/{len(x['term']['cited_laws'])} 인용")
    for p in (CASE_TTL, OUT_DOT, OUT_SVG, OUT_PNG):
        print(f"  {os.path.relpath(p, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
