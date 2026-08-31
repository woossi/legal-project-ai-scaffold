"""지정근거법 사슬 — 지구에서 국토계획법까지 내려가는 법·제도 계통.

`build_boundary.py` 가 지구에 `lp:지정근거법 → Act` 한 줄만 붙여 법률 단위에서 끊긴다.
이 빌더는 그 끊긴 자리를 조문 단위로 잇는다. 지구단위계획이 왜 그 지구에 성립하는지의
근거가 (지정근거법 조문 → 의제·포함 → 국토계획법 조문) 사슬로 닫힌다.

    District ─지정근거법→ Act ←inSource─ ArticleWork ─의제/포함규범→ ArticleWork(국토계획법)

사양(CHAIN)은 법문 대조로 확정한 것이고 corpus 는 그 문언을 검증한다. 검증에 실패한
항목은 간선을 만들지 않고 리포트에 격리한다 — 근거 요건을 못 채우면 만들지 않는다.

의제를 `lp:delegates` 로 표현하지 않는다. 위임은 하위 규범에 정할 권한을 넘기지만
의제는 절차 없이 효과를 성립시킨다. `owl:sameAs` 도 아니다 — 두 지정행위가 같아지는
것이 아니라 한쪽 효과가 다른 쪽에 발생할 뿐이다. 성립과 소멸은 방향이 반대라
`lp:의제` 와 `lp:해제의제` 로 가른다.

수신 조문이 특정되지 않는 경우(대통령령 위임, "국토계획법에 따른" 같은 총칭)는
간선을 만들지 않고 속성으로만 기록한다.

    .venv/bin/python .claude/skills/kb/kb-ontology/scripts/build_legal_chain.py
"""
import gzip
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mint_iri as M  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "../../../../.."))
CORPUS = os.path.join(ROOT, "output/legal/statute/guideline_article_corpus.jsonl.gz")
META = os.path.join(ROOT, "output/legal/시행지침/meta.json")
BOUNDARY = os.path.join(ROOT, "output/kb/graph/det/boundary.ttl")
OUT_TBOX = os.path.join(ROOT, "output/kb/ontology/legal-chain.ttl")
OUT_SHAPES = os.path.join(ROOT, "output/kb/shapes/legal-chain.shacl.ttl")
OUT_GRAPH = os.path.join(ROOT, "output/kb/graph/det/legal-chain.ttl")
OUT_REPORT = os.path.join(ROOT, "output/kb/reports/_legal_chain.json")

# corpus 조회용 정식명과 IRI 발급용 이름을 가른다 — boundary.ttl 의 Act IRI 는
# 계통 규범이 law_roots.json 이름을, 지정근거법이 meta.json lawordNm 을 쓴다.
NAT = "국토의 계획 및 이용에 관한 법률"
NAT_IRI = "국토의계획및이용에관한법률"

# meta.json lawordNm → corpus official_name
LAWORD = {
    "택지개발촉진법": "택지개발촉진법",
    "공공주택 특별법": "공공주택 특별법",
    "도시개발법": "도시개발법",
    "민간임대주택법": "민간임대주택에 관한 특별법",
    "경제자유구역법": "경제자유구역의 지정 및 운영에 관한 특별법",
    "산단절차간소화법": "산업단지 인ㆍ허가 절차 간소화를 위한 특례법",
    "산업입지법": "산업입지 및 개발에 관한 법률",
}

# 법문 대조로 확정한 사양.
#   rel      의제 · 해제의제 · 포함규범 · 존속근거
#   to       수신 조문. None 이면 조문이 특정되지 않아 간선을 만들지 않는다
#   probe    corpus 조문 본문에 반드시 있어야 하는 문자열. 없으면 격리한다
CHAIN = [
    # 택지개발촉진법
    dict(law="택지개발촉진법", art="3", para="8", rel="의제", to=(NAT, "51"),
         target="지구단위계획구역", act="지정·해제", effect="절차생략",
         probe="지구단위계획구역의 지정 또는 해제가 있은 것으로 본다"),
    dict(law="택지개발촉진법", art="9", para="2", rel="포함규범", to=(NAT, "52"),
         target="지구단위계획", act="포함", effect="문서종속",
         probe="실시계획에는"),
    dict(law="택지개발촉진법", art="16", para="3", rel="존속근거", to=None,
         target="지구단위계획", act="관리", effect="준공후존속",
         probe="이미 고시된 실시계획에 포함된 지구단위계획으로 관리"),
    # 공공주택 특별법 — 하나의 항이 셋을 동시에 의제한다
    dict(law="공공주택 특별법", art="12", para="4", rel="의제", to=(NAT, "36"),
         target="용도지역", act="지정·변경", effect="절차생략",
         probe="도시지역으로의 용도지역"),
    dict(law="공공주택 특별법", art="12", para="4", rel="의제", to=(NAT, "43"),
         target="도시·군계획시설", act="결정", effect="절차생략",
         probe="결정된 도시ㆍ군계획시설"),
    dict(law="공공주택 특별법", art="12", para="4", rel="의제", to=(NAT, "51"),
         target="지구단위계획구역", act="지정·변경", effect="절차생략",
         probe="제51조제1항에 따른 지구단위계획구역이 지정ㆍ변경된 것으로 보며"),
    # 도시개발법
    dict(law="도시개발법", art="9", para="2", rel="의제", to=None,
         target="지구단위계획구역", act="결정·고시", effect="절차생략",
         probe="대통령령으로 정하는 지구단위계획구역으로 결정되어 고시된 것으로 본다"),
    dict(law="도시개발법", art="10", para="3", rel="해제의제", to=None,
         target="지구단위계획구역", act="환원·폐지", effect="소멸",
         probe="지구단위계획구역은 해당 도시개발구역 지정 전의"),
    dict(law="도시개발법", art="17", para=None, rel="포함규범", to=None,
         target="지구단위계획", act="포함", effect="문서종속",
         probe="실시계획에는 지구단위계획이 포함되어야 한다"),
    dict(law="도시개발법", art="18", para="2", rel="의제", to=None,
         target="도시·군관리계획", act="결정·고시", effect="절차생략",
         probe="도시ㆍ군관리계획(지구단위계획을 포함한다"),
    # 민간임대주택법 — 제51조가 아니라 제50조를 지목한다
    dict(law="민간임대주택에 관한 특별법", art="26", para="9", rel="의제", to=(NAT, "50"),
         target="지구단위계획구역", act="결정·고시", effect="절차생략",
         probe="제50조에 따른 지구단위계획구역"),
    dict(law="민간임대주택에 관한 특별법", art="27", para="3", rel="해제의제", to=None,
         target="지구단위계획구역", act="환원", effect="소멸",
         probe="지정 당시로 환원된 것으로 본다"),
    # 경제자유구역법
    dict(law="경제자유구역의 지정 및 운영에 관한 특별법", art="9", para="4", rel="포함규범", to=(NAT, "52"),
         target="지구단위계획", act="포함", effect="문서종속",
         probe="실시계획에는"),
    dict(law="경제자유구역의 지정 및 운영에 관한 특별법", art="14", para="4", rel="존속근거", to=None,
         target="지구단위계획", act="관리", effect="준공후존속",
         probe="고시된 실시계획에 포함된 지구단위계획에 따라 관리"),
    # 산업입지법 — 제8조 지정·고시를 전제로 한다. 그 전제가 산단절차간소화법과 잇는 접점이다
    dict(law="산업입지 및 개발에 관한 법률", art="23", para="1", rel="의제", to=(NAT, "51"),
         precond=("산업입지 및 개발에 관한 법률", "8"),
         target="지구단위계획구역", act="지정·해제", effect="절차생략",
         probe="제51조에 따른 지구단위계획구역의 지정 또는 해제가 있은 것으로 본다"),
    # 산단절차간소화법 — 지구단위계획 어휘가 33개 조문에서 0회다. 준용도 방향이 반대다
    # (이 법을 산입법에 준용하며, 회귀는 「따른다」 형식이라 조문 인용으로 안 잡힌다).
    # 실체 경로는 고시 의제의 2단 연쇄다.
    dict(law="산업단지 인ㆍ허가 절차 간소화를 위한 특례법", art="8", para="1", rel="의제", to=None,
         target="개발계획·실시계획", act="수립", effect="절차생략",
         probe="개발계획 및 실시계획이 모두 수립된 것으로 본다"),
    dict(law="산업단지 인ㆍ허가 절차 간소화를 위한 특례법", art="15", para="2", rel="의제",
         to=("산업입지 및 개발에 관한 법률", "8"),
         target="산업단지 지정 고시", act="고시", effect="절차생략",
         probe="산업단지의 지정 고시 및 같은 법 제19조의2에 따른 실시계획 승인의 고시로 본다"),
]

REL_PRED = {"의제": "lp:의제", "해제의제": "lp:해제의제",
            "포함규범": "lp:포함규범", "존속근거": "lp:존속근거"}

PARA_MARK = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


OFFICIAL2IRI = {v: k for k, v in LAWORD.items()}
OFFICIAL2IRI[NAT] = NAT_IRI


def iri_statute(official):
    """boundary.ttl 과 같은 IRI 를 낸다. mint_iri.statute() 를 쓰고 @base 접두만 뗀다.

    발신 법률(지정근거법)은 meta.json lawordNm 으로, 수신 법률(국토계획법)은
    law_roots.json 이름으로 발급된 기존 노드에 붙어야 한다. corpus 정식명을 그대로
    쓰면 `공공주택특별법` 이 되어 boundary.ttl 의 `공공주택%20특별법` 과 어긋난다.
    """
    return M.statute(OFFICIAL2IRI[official])[len(M.ID):]


def iri_article(official, art, para=None):
    tail = "제%s조" % art + ("제%s항" % para if para else "")
    return M.statute_article(OFFICIAL2IRI[official], tail)[len(M.ID):]


def load_corpus():
    out = {}
    with gzip.open(CORPUS, "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            if d.get("official_kind") == "법률":
                out[d["official_name"]] = d
    return out


def para_text(text, para):
    """조문 본문에서 지정한 항만 잘라낸다. 항이 없으면 조문 전체."""
    if not para:
        return text
    i = int(para) - 1
    if i >= len(PARA_MARK):
        return text
    mark = PARA_MARK[i]
    pos = text.find(mark)
    if pos < 0:
        return None
    nxt = text.find(PARA_MARK[i + 1]) if i + 1 < len(PARA_MARK) else -1
    return text[pos:nxt] if nxt > pos else text[pos:]


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def main():
    corpus = load_corpus()
    meta = json.load(open(META, encoding="utf-8"))

    # 지구별 근거법
    by_law = {}
    for d in meta["districts"]:
        by_law.setdefault(d["lawordNm"], []).append(d["dstrcAppnNo"])

    ok, failed = [], []
    for spec in CHAIN:
        doc = corpus.get(spec["law"])
        if not doc:
            failed.append(dict(spec_law=spec["law"], art=spec["art"], 사유="corpus 에 법률 없음"))
            continue
        prov = next((p for p in doc["provisions"]
                     if p["article_number"] == spec["art"] and not p["article_branch"]), None)
        if not prov:
            failed.append(dict(spec_law=spec["law"], art=spec["art"], 사유="조문 없음"))
            continue
        seg = para_text(prov["text"], spec["para"])
        if seg is None:
            failed.append(dict(spec_law=spec["law"], art=spec["art"], para=spec["para"],
                               사유="항 표지를 본문에서 찾지 못함"))
            continue
        if spec["probe"] not in seg:
            failed.append(dict(spec_law=spec["law"], art=spec["art"], para=spec["para"],
                               사유="probe 문언 불일치", probe=spec["probe"]))
            continue
        rec = dict(spec)
        rec["title"] = prov["article_title"]
        rec["quote"] = re.sub(r"\s+", " ", seg).strip()[:400]
        rec["effective"] = doc.get("current_effective_date")
        ok.append(rec)

    # ── TBox
    tb = ['##  지정근거법 사슬 TBox — output/kb/ontology/core.ttl 의 클래스를 전제한다.',
          '##  생성: build_legal_chain.py. 손으로 고치지 않는다.',
          '',
          '@prefix lp:   <https://w3id.org/lp/ont#> .',
          '@prefix owl:  <http://www.w3.org/2002/07/owl#> .',
          '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .',
          '@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .',
          '']
    for pred, label, comment in [
        ("lp:의제", "법률상 의제",
         "다른 법률의 지정·결정 효과를 절차 없이 성립시키는 조문 간 간선. lp:delegates 와 구별한다 — 위임은 하위 규범에 정할 권한을 넘기지만 의제는 절차 자체를 생략한다. owl:sameAs 와도 구별한다 — 두 행위가 같아지는 것이 아니라 한쪽 효과가 다른 쪽에 발생할 뿐이다."),
        ("lp:해제의제", "해제 의제",
         "지정 해제·환원·폐지 효과의 의제. lp:의제 와 방향이 반대라 같은 술어로 두지 않는다."),
        ("lp:포함규범", "포함 규범",
         "한 문서가 다른 법률에 따라 작성된 계획을 부분으로 담아야 함을 정하는 조문 간 간선. 의제가 아니다 — 효과를 성립시키는 것이 아니라 편입을 명한다."),
        ("lp:존속근거", "존속 근거",
         "사업 준공 후에도 계획이 효력을 유지하는 근거 조문. 성립도 소멸도 아니다."),
        ("lp:의제전제", "의제 전제 조문",
         "이 의제가 성립하려면 선행해야 하는 다른 조문의 행위. 의제 자체가 아니라 그 조건이다. 산입법 제23조제1항이 제8조 지정·고시를 전제하고, 산단절차간소화법 제15조제2항이 그 제8조 고시를 다시 의제하는 2단 연쇄가 이 술어로 이어진다."),
    ]:
        tb += ['%s a owl:ObjectProperty ;' % pred,
               '    rdfs:label "%s"@ko ;' % label,
               '    rdfs:comment "%s"@ko ;' % esc(comment),
               '    owl:propertyDisjointWith lp:delegates ;',
               '    rdfs:domain lp:ArticleWork ; rdfs:range lp:ArticleWork .', '']
    for pred, label, comment in [
        ("lp:의제대상", "의제 대상", "의제·포함의 대상이 되는 제도. 용도지역·도시·군계획시설·지구단위계획구역·지구단위계획·도시·군관리계획."),
        ("lp:의제행위", "의제 행위", "지정·결정·고시·해제·환원 중 원문이 쓴 표현. 지정 의제와 결정 의제는 법적 효과가 다르므로 뭉치지 않는다."),
        ("lp:의제효과", "의제 효과", "절차생략·문서종속·소멸·준공후존속. 법마다 조합이 달라 클래스가 아니라 속성으로 둔다."),
        ("lp:의제문언", "의제 근거 문언", "효과를 낳은 조문 원문. 이 값이 없으면 간선을 만들지 않는다."),
        ("lp:수신조문미특정", "수신 조문 미특정", "참이면 원문이 수신 조문을 특정하지 않아(대통령령 위임·총칭 인용) 간선을 만들지 않았다는 뜻이다."),
        ("lp:조문시행일", "조문 시행일", "이 간선을 낳은 법령 판본의 시행일. corpus current_effective_date 다."),
        ("lp:조문판본미대조", "조문 판본 미대조", "참이면 현행본에서 읽었고 지구 지정 시점 판본과 대조하지 않았다는 뜻이다. 지구는 2002~2024년에 지정됐고 조문은 2025~2026년 현행본이다. lp:applicableVersionUnresolved 를 재사용하지 않는다 — 그 술어는 domain 이 lp:District 라 조문에 붙이면 RDFS 추론이 조문을 지구로 오분류한다."),
    ]:
        kind = "owl:DatatypeProperty"
        rng = "xsd:boolean" if pred.endswith(("미특정", "미대조")) else "xsd:string"
        tb += ['%s a %s ;' % (pred, kind),
               '    rdfs:label "%s"@ko ;' % label,
               '    rdfs:comment "%s"@ko ;' % esc(comment),
               '    rdfs:domain lp:ArticleWork ; rdfs:range %s .' % rng, '']

    # ── 그래프
    g = ['##  지정근거법 사슬 — 지구에서 국토계획법까지. 조문 단위.',
         '##  생성: build_legal_chain.py (정렬 순회 · 멱등). 손으로 고치지 않는다.',
         '##  근거: output/legal/statute/guideline_article_corpus.jsonl.gz 정본 문언 대조.',
         '##',
         '##  시점 경고 — 조문은 전부 2025~2026년 현행본이고 지구는 2002~2024년에 지정됐다.',
         '##  지정 당시 판본으로 재판정하면 수신 조문과 의제 구조가 바뀔 수 있다. 특히 민임법',
         '##  제26조제9항은 2017.1.17 개정본이고 공주법은 2014년 개칭됐다. 모든 발신 조문에',
         '##  lp:조문판본미대조 를 단다 — 이 사슬을 적용 판본 확정으로 읽지 않는다.',
         '',
         '@prefix lp:   <https://w3id.org/lp/ont#> .',
         '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .',
         '@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .',
         '@base <https://w3id.org/lp/id/> .',
         '',
         '##  수신 조문. boundary.ttl 의 제52조와 같은 IRI 규칙이다.', '']

    RECV_LABEL = {(NAT, "36"): "용도지역의 지정", (NAT, "43"): "도시·군계획시설의 결정",
                  (NAT, "50"): "지구단위계획구역 및 지구단위계획의 결정",
                  (NAT, "51"): "지구단위계획구역의 지정", (NAT, "52"): "지구단위계획의 내용",
                  ("산업입지 및 개발에 관한 법률", "8"): "산업단지의 지정·고시"}
    recv = sorted({r["to"] for r in ok if r["to"]} |
                  {r["precond"] for r in ok if r.get("precond")})
    for law, a in recv:
        g += ['<%s> a lp:ArticleWork ;' % iri_article(law, a),
              '    lp:inSource <%s> ;' % iri_statute(law),
              '    lp:rootStatute "%s" ;' % OFFICIAL2IRI[law],
              '    rdfs:comment "%s"@ko .' % RECV_LABEL.get((law, a), ""), '']

    g += ['##  발신 조문 — 지정근거법. 항 단위로 발급한다.', '']
    seen = set()
    for r in sorted(ok, key=lambda x: (x["law"], int(x["art"]), x["para"] or "0", x["target"])):
        node = iri_article(r["law"], r["art"], r["para"])
        if node not in seen:
            seen.add(node)
            g += ['<%s> a lp:ArticleWork ;' % node,
                  '    lp:inSource <%s> ;' % iri_statute(r["law"]),
                  '    lp:rootStatute "%s" ;' % OFFICIAL2IRI[r["law"]],
                  '    rdfs:label "%s 제%s조%s(%s)"@ko ;' % (
                      r["law"], r["art"], ("제%s항" % r["para"]) if r["para"] else "", r["title"]),
                  '    lp:조문시행일 "%s" ;' % r["effective"],
                  '    lp:조문판본미대조 "true"^^xsd:boolean ;',
                  '    lp:의제문언 "%s" .' % esc(r["quote"]), '']
            if r.get("precond"):
                g += ['<%s> lp:의제전제 <%s> .' % (node, iri_article(*r["precond"])), '']
        lines = ['<%s>' % node]
        if r["to"]:
            lines.append('    %s <%s> ;' % (REL_PRED[r["rel"]], iri_article(*r["to"])))
        else:
            lines.append('    lp:수신조문미특정 "true"^^xsd:boolean ;')
        lines.append('    lp:의제대상 "%s" ;' % r["target"])
        lines.append('    lp:의제행위 "%s" ;' % r["act"])
        lines.append('    lp:의제효과 "%s" .' % r["effect"])
        g += lines + ['']

    # ── 리포트
    reach = {}
    laws_with_chain = {r["law"] for r in ok}
    for lawordNm, codes in sorted(by_law.items()):
        official = LAWORD.get(lawordNm)
        has = official in laws_with_chain
        reach[lawordNm] = dict(지구수=len(codes), corpus명=official,
                               사슬보유=has,
                               간선수=sum(1 for r in ok if r["law"] == official),
                               사유="" if has else "이 법률에 지구단위계획 조항이 없다")
    # ── IRI 분열 실측. 같은 조문이 두 노드로 존재하면 사슬이 만나지 않는다.
    deleg = os.path.join(ROOT, "output/kb/norm/graph/det/delegation.ttl")
    split = {}
    if os.path.exists(deleg):
        body = open(deleg, encoding="utf-8").read()
        norm_iris = set(re.findall(r"<(statute/[^>]+)>", body))
        chain_iris = set(re.findall(r"<(statute/[^>]+)>", "\n".join(g)))
        bnd = open(BOUNDARY, encoding="utf-8").read()
        bnd_iris = set(re.findall(r"<(statute/[^>]+)>", bnd))
        split = dict(
            norm층_조문노드=len(norm_iris),
            det층_조문노드=len(chain_iris | bnd_iris),
            공유노드=len(norm_iris & (chain_iris | bnd_iris)),
            norm층_국토계획법_표기=sorted(i for i in norm_iris if "국토" in i)[:1],
            det층_국토계획법_표기=sorted(i for i in (chain_iris | bnd_iris) if "국토" in i)[:1],
            진단=("norm 층은 corpus official_name(공백 %20 인코딩)을, det 층은 "
                  "law_roots.json 이름(공백 없음)을 쓴다. 같은 조문이 두 IRI 로 존재하므로 "
                  "지구→지정근거법→국토계획법 사슬(det)과 국토계획법→시행령→조례 사슬(norm)이 "
                  "만나지 않는다. 이 빌더가 만든 문제가 아니라 기존 분열이다."),
        )

    report = dict(
        생성기="build_legal_chain.py",
        시점경고=("조문은 2025~2026년 현행본이고 지구는 2002~2024년 지정이다. 지정 당시 판본으로 "
                "재판정하면 수신 조문과 의제 구조가 바뀔 수 있다 — 민임법 제26조제9항은 2017.1.17 "
                "개정본, 공주법은 2014년 개칭, 국토계획법 제51조제1항은 2011~2017년 6차 개정. "
                "발신 조문 전건에 lp:조문판본미대조 를 달았다. 이 사슬을 적용 판본 확정으로 읽지 않는다."),
        조문시행일={r["law"]: r["effective"] for r in ok},
        iri분열=split,
        사양수=len(CHAIN), 검증통과=len(ok), 격리=len(failed),
        간선유형={k: sum(1 for r in ok if r["rel"] == k) for k in REL_PRED},
        수신조문미특정=sum(1 for r in ok if not r["to"]),
        지구도달=reach,
        도달지구수=sum(v["지구수"] for v in reach.values() if v["사슬보유"]),
        미도달지구수=sum(v["지구수"] for v in reach.values() if not v["사슬보유"]),
        격리목록=failed,
    )

    # ── SHACL. owl:propertyDisjointWith 는 OWL2 구성이라 pyshacl 이 보지 않는다.
    #    sh:disjoint 로 같은 (주어, 목적어) 쌍이 두 술어에 동시에 놓이는 것을 막는다.
    sh = ['##  지정근거법 사슬 SHACL — 의제 계열과 위임의 배타성을 강제한다.',
          '##  생성: build_legal_chain.py. 손으로 고치지 않는다.',
          '##',
          '##  lp:delegates 는 owl:TransitiveProperty 다(grounding.ttl). 의제가 그 폐포에',
          '##  섞이면 "위임으로 도달했다"와 "의제로 성립했다"를 가릴 수 없게 된다.',
          '##  실측 2026-08-25: delegation.ttl 과 legal-chain.ttl 은 술어 0건·노드 0건 겹친다.',
          '##  다만 겹치지 않는 이유가 설계가 아니라 IRI 분열이다 — reports/_legal_chain.json',
          '##  의 iri분열 참조. 분열이 해소되면 이 셰이프가 실제로 작동하기 시작한다.',
          '',
          '@prefix lp:   <https://w3id.org/lp/ont#> .',
          '@prefix sh:   <http://www.w3.org/ns/shacl#> .',
          '']
    for pred in sorted(REL_PRED.values()):
        nm = pred.split(":")[1]
        sh += ['lp:%s_배타Shape a sh:NodeShape ;' % nm,
               '    sh:targetSubjectsOf %s ;' % pred,
               '    sh:property [',
               '        sh:path %s ;' % pred,
               '        sh:disjoint lp:delegates ;',
               '        sh:message "%s 와 lp:delegates 가 같은 조문 쌍에 동시에 놓였다. 위임과 의제는 배타다"@ko ;' % pred,
               '    ] .', '']

    for path, body in [(OUT_TBOX, "\n".join(tb)), (OUT_GRAPH, "\n".join(g)),
                       (OUT_SHAPES, "\n".join(sh))]:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body.rstrip() + "\n")
    os.makedirs(os.path.dirname(OUT_REPORT), exist_ok=True)
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print("사양 %d · 검증통과 %d · 격리 %d" % (len(CHAIN), len(ok), len(failed)))
    print("간선", report["간선유형"], "· 수신미특정", report["수신조문미특정"])
    print("도달 지구 %d / 미도달 %d" % (report["도달지구수"], report["미도달지구수"]))
    for f_ in failed:
        print("  격리:", f_)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
