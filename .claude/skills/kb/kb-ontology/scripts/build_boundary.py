"""바운더리 — 법령에서 지구로 내려가는 결정론 경계.

`docs/superpowers/specs/2026-08-06-법률-바운더리-design.md` 의 구현. 지구별 적용
법령 집합(바운더리)을 지침 인용에서 역조립하지 않고, (지정 근거법 × 관할)에서
유도한다. 지구의 시간값은 신선도 관측 메타데이터로만 붙이며 적용 판본을 정하지 않는다.

    B(지구) = 계통 규범(전 지구 공통) ∪ 지정근거법(lawordNm) ∪ 관할조례(lcCode+광역승격)

전부 `output/legal/시행지침/meta.json` 과 `contract/law_roots.json` — 이미 검증된
수집 정본과 계약에서만 유도한다. 새 추측이 없다.

    .venv_kb/bin/python3 .claude/skills/kb/kb-ontology/scripts/build_boundary.py
"""
import collections
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mint_iri as M
import resolve_asof as R

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
META = os.path.join(ROOT, "output/legal/시행지침/meta.json")
ROOTS = os.path.join(HERE, "..", "contract", "law_roots.json")
OUT_TTL = os.path.join(ROOT, "output/kb/graph/det/boundary.ttl")
OUT_REPORT = os.path.join(ROOT, "output/kb/reports/_boundary_check.json")
OUT_DOT = os.path.join(ROOT, "output/kb/ontology-boundary-example.dot")
OUT_SVG = os.path.join(ROOT, "output/kb/ontology-boundary-example.svg")
OUT_PNG = os.path.join(ROOT, "output/kb/ontology-boundary-example.png")

SEOUL, INCHEON = "11", "28"
SEOUL_GOV, INCHEON_GOV = "11000", "28000"
GOV_NAME = {SEOUL_GOV: "서울특별시", INCHEON_GOV: "인천광역시"}

# 법률 별칭 — md 언급 대조에서만 쓴다. 바운더리 소속(IRI)은 정식 명칭 하나로 발급한다.
LAW_ALIASES = {
    "국토의계획및이용에관한법률": ["국토계획법"],
    "공익사업을위한토지등의취득및보상에관한법률": ["토지보상법"],
}

DEMO_DSTRC = "11740DA2010001"  # 강일 도시개발구역 — 서울 강동구, 도시개발법


def rel(iri):
    """mint_iri 가 낸 절대 IRI 를 @base 상대 경로로 줄인다."""
    assert iri.startswith(M.ID), f"mint_iri 산출이 아니다: {iri!r}"
    return iri[len(M.ID):]


def load():
    with open(META, encoding="utf-8") as f:
        districts = json.load(f)["districts"]
    with open(ROOTS, encoding="utf-8") as f:
        roots = json.load(f)
    return districts, roots


def md_text(d):
    """terms.json 의 file 필드는 구경로라 쓰지 않는다. 규약으로 유도한다 (build_example.py 와 동일)."""
    p = os.path.join(ROOT, "output/legal/markdown",
                     d["region"].strip(), d["dstrcNm"].strip() + ".md")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else None


def ordinance_subject(lc5):
    """조례 주체 lcCode. 서울·인천의 기초 지자체는 광역으로 승격, 경기는 자신.

    실측 근거 — md 본문 조례 언급이 서울특별시 142건·인천광역시 264건으로 광역이
    지배적이다 (design doc). 도시계획·건축·주차장 조례는 광역시에서 광역 단위로 제정된다.
    """
    p2 = lc5[:2]
    if p2 == SEOUL:
        return SEOUL_GOV
    if p2 == INCHEON:
        return INCHEON_GOV
    return lc5


def squash(s):
    return re.sub(r"\s+", "", s or "")


def mentioned(name, squashed_text, aliases=()):
    """md 전문(공백 제거)에 법령·조례명(또는 별칭)이 부분 문자열로 있는지.

    언급은 적용의 증거이지 적용 판정이 아니다 — 이 함수의 결과는 바운더리 소속을
    바꾸지 않는다. 대조 결과 기록용이다.
    """
    if not squashed_text:
        return False
    return any(squash(c) in squashed_text for c in (name, *aliases))


def parse_delegated_by(s):
    """law_roots.json 의 delegatedBy 문자열 '건축법 제43조' 를 (법령명, 조문) 으로 가른다.

    이 데이터셋의 법령명 자체에는 공백이 없으므로(정규화된 systemRoots 표기) 첫 공백에서
    잘라도 안전하다.
    """
    name, article = s.split(" ", 1)
    return name, article


def verified_ordinance_kinds(roots):
    return sorted((k for k in roots["ordinanceKinds"] if k["verified"]),
                  key=lambda k: k["kind"])


# ---------------------------------------------------------------------------
# TTL — graph/det/boundary.ttl
# ---------------------------------------------------------------------------

def ttl_header():
    return [
        "@prefix lp:   <https://w3id.org/lp/ont#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .",
        "@base <https://w3id.org/lp/id/> .",
        "",
        "##  바운더리 — 법령에서 지구로 내려가는 결정론 경계.",
        "##  B(지구) = 계통 규범(전지구) ∪ 지정근거법(lawordNm) ∪ 관할조례(lcCode+광역승격)",
        "##  docs/superpowers/specs/2026-08-06-법률-바운더리-design.md",
        "##  생성: build_boundary.py (정렬 순회 · 멱등). 손으로 고치지 않는다.",
        "",
    ]


def ttl_system_norms(roots):
    """계통 규범 — systemRoots (statute × articles) 9 + mandatoryClauses 2.

    전 지구가 공통으로 지는 법정 사항이라 지구별 분기 없이 한 번만 발급한다.
    """
    L = ["##  계통 규범 — 전 지구 공통 (국토계획법·토지보상법·건축법·주차장법)", ""]
    for entry in sorted(roots["systemRoots"], key=lambda e: e["statute"]):
        name = entry["statute"]
        st_iri = rel(M.statute(name))
        L += [f"<{st_iri}> a lp:Act ;", f'    rdfs:label "{name}"@ko .', ""]
        for art in sorted(entry["articles"]):
            art_iri = rel(M.statute_article(name, art))
            L += [f"<{art_iri}> a lp:ArticleWork ;",
                  f"    lp:inSource <{st_iri}> ;",
                  f'    lp:rootStatute "{name}" .', ""]

    L += ["##  법정 필수사항 2건 (국토계획법 제52조제1항제2호·제4호) — 없으면 결손이 아니라",
          "##  추출 실패를 먼저 의심한다. lp:mandates 의 대상은 지구별 lp:Guideline 인스턴스가",
          "##  아직 없는(스펙 ③) lp:Guideline 클래스 자체를 가리키는 pun 이다.", ""]
    for m in sorted(roots["mandatoryClauses"], key=lambda e: e["clause"]):
        name, clause = m["statute"], m["clause"]
        art_iri = rel(M.statute_article(name, clause))
        L += [f"<{art_iri}> a lp:ArticleWork ;",
              f"    lp:inSource <{rel(M.statute(name))}> ;",
              f'    lp:rootStatute "{name}" ;',
              f'    rdfs:comment "{m["content"]}"@ko ;',
              "    lp:mandates lp:Guideline .",
              ""]
    return L


def ttl_decree_extra():
    """건축법시행령 제27조의2 — delegatedBy 에만 나오고 systemRoots 에는 없는 유일한 조문.

    inSource 는 건축법시행령(자신이 속한 법원), rootStatute 는 건축법(위임 사슬의 뿌리) —
    둘이 다르다. 게이트 8 은 rootStatute 만 본다.
    """
    L = ["##  건축법시행령 — 건축조례 delegatedBy 에만 나오는 조문. inSource 는 시행령,",
         "##  rootStatute 는 위임 사슬의 뿌리인 건축법이다.", ""]
    dec_name = "건축법시행령"
    dec_iri = rel(M.statute(dec_name))
    art_iri = rel(M.statute_article(dec_name, "제27조의2"))
    L += [f"<{dec_iri}> a lp:Decree ;", f'    rdfs:label "{dec_name}"@ko .', "",
          f"<{art_iri}> a lp:ArticleWork ;",
          f"    lp:inSource <{dec_iri}> ;",
          f'    lp:rootStatute "건축법" .', ""]
    return L


def ttl_designation_acts(roots):
    """지정 근거법 — meta.json lawordNm 7종."""
    L = ["##  지정 근거법 — meta.json lawordNm 7종", ""]
    for e in sorted(roots["designationRoots"], key=lambda e: e["lawordNm"]):
        name = e["lawordNm"]
        iri = rel(M.statute(name))
        L += [f"<{iri}> a lp:Act ;", f'    rdfs:label "{name}"@ko .', ""]
    return L


def ttl_jurisdictions(lc5_all):
    """지자체 — lcCode 앞5자리 64개 + 광역 승격 경로 + 광역 2개."""
    L = ["##  지자체 — lcCode 앞5자리 64개. 서울(11)·인천(28) 기초는 광역을 상위지자체로 가리킨다", ""]
    for lc5 in lc5_all:
        iri = rel(M.gov(lc5))
        L += [f"<{iri}> a lp:Jurisdiction ;", f'    rdfs:label "lcCode {lc5}"@ko .', ""]
        if lc5[:2] == SEOUL:
            L += [f"<{iri}> lp:상위지자체 <{rel(M.gov(SEOUL_GOV))}> .", ""]
        elif lc5[:2] == INCHEON:
            L += [f"<{iri}> lp:상위지자체 <{rel(M.gov(INCHEON_GOV))}> .", ""]

    L += ["##  광역 승격 대상 — 자치구 조례는 여기서 제정된다", ""]
    for code in sorted(GOV_NAME):
        iri = rel(M.gov(code))
        L += [f"<{iri}> a lp:Jurisdiction ;", f'    rdfs:label "{GOV_NAME[code]}"@ko .', ""]
    return L


def ttl_ordinances(ord_kinds, subjects):
    """조례 — 조례주체(광역 승격 적용) × 검증 3계통."""
    L = ["##  조례 — 조례주체 × 검증 3계통(도시계획·건축·주차장). 옥외광고물 제외(verified:false)", ""]
    for subj in subjects:
        label_subj = GOV_NAME.get(subj, f"lcCode {subj}")
        for kind_entry in ord_kinds:
            kind = kind_entry["kind"]
            iri = rel(M.ordinance(subj, kind))
            L += [f"<{iri}> a lp:Ordinance ;",
                  f'    rdfs:label "{label_subj} {kind}"@ko .', ""]
    return L


def ttl_ordinance_delegates(roots, subjects):
    """조례 위임 — delegatedBy 조문(이미 발급된 ArticleWork) → 해당 계통의 모든 조례 인스턴스.

    lp:delegates 가 아니라 lp:조례위임 이다 — range 가 Ordinance 라 조례를 조문으로
    오분류하는 RDFS 추론 함정을 피한다 (grounding.ttl 참조).
    """
    L = ["##  조례 위임 — 검증된 3계통만. lp:조례위임 은 조문 → 조례 (lp:delegates 아님)", ""]
    for kind_entry in verified_ordinance_kinds(roots):
        kind = kind_entry["kind"]
        for db in kind_entry["delegatedBy"]:
            name, article = parse_delegated_by(db)
            art_iri = rel(M.statute_article(name, article))
            for subj in subjects:
                ord_iri = rel(M.ordinance(subj, kind))
                L.append(f"<{art_iri}> lp:조례위임 <{ord_iri}> .")
        L.append("")
    return L


def ttl_districts(records, ord_kind_names):
    """지구 190 — 지정근거법·관할·시간 관측값·적용조례."""
    L = ["##  지구 190 — 지정근거법·관할·시간 관측(resolve_asof)·적용조례", "",
         "##  observation* 은 품질 관측용이다. ArticleVersion·LawApplication asOf 로 쓰지 않는다.", ""]
    for rec in records:
        d, obs = rec["d"], rec["observation"]
        no = d["dstrcAppnNo"]
        iri = rel(M.district(no))
        lc5 = d["lcCode"][:5]
        gov_iri = rel(M.gov(lc5))
        name = d["dstrcNm"].strip()
        act_iri = rel(M.statute(d["lawordNm"]))
        subj = ordinance_subject(lc5)
        ord_iris = [rel(M.ordinance(subj, k)) for k in ord_kind_names]

        preds = [
            f'lp:code "{no}"',
            f'rdfs:label "{name}"@ko',
            f"lp:jurisdiction <{gov_iri}>",
            f'lp:지정당시코드 "{no[:5]}"',
            f'lp:observationYear "{obs["observationYear"]}"^^xsd:gYear',
        ]
        if obs["observationDate"]:
            preds.append(f'lp:observationDate "{obs["observationDate"]}"^^xsd:date')
        preds.append(f'lp:observationBasis "{obs["basis"]}"')
        preds.append(f'lp:observationPrecision "{obs["precision"]}"')
        preds.append('lp:applicableVersionUnresolved "true"^^xsd:boolean')
        preds.append(f"lp:지정근거법 <{act_iri}>")
        preds += [f"lp:적용조례 <{oi}>" for oi in ord_iris]

        L.append(f"<{iri}> a lp:District ;")
        for i, p in enumerate(preds):
            L.append(f"    {p}" + (" ." if i == len(preds) - 1 else " ;"))
        L.append("")
    return L


def build_ttl(districts, roots, records):
    ord_kinds = verified_ordinance_kinds(roots)
    ord_kind_names = [k["kind"] for k in ord_kinds]
    lc5_all = sorted({d["lcCode"][:5] for d in districts})
    subjects = sorted({ordinance_subject(lc5) for lc5 in lc5_all})

    L = []
    L += ttl_header()
    L += ttl_system_norms(roots)
    L += ttl_decree_extra()
    L += ttl_designation_acts(roots)
    L += ttl_jurisdictions(lc5_all)
    L += ttl_ordinances(ord_kinds, subjects)
    L += ttl_ordinance_delegates(roots, subjects)
    L += ttl_districts(records, ord_kind_names)
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# 대조 리포트 — reports/_boundary_check.json
# ---------------------------------------------------------------------------

def member_checks(rec, system_names, ord_kind_names):
    """지구 하나의 바운더리 멤버별 md 언급 여부. 순서 보존(OrderedDict, py3.7+ dict 도 보존)."""
    d = rec["d"]
    squashed = rec["squashed"]
    out = {}
    for name in system_names:
        out[name] = "언급확인" if mentioned(name, squashed, LAW_ALIASES.get(name, ())) \
            else "언급없음"
    law = d["lawordNm"]
    out[law] = "언급확인" if mentioned(law, squashed) else "언급없음"
    for kind in ord_kind_names:
        out[kind] = "언급확인" if mentioned(kind, squashed) else "언급없음"
    return out


def build_report(records, system_names, ord_kind_names):
    agg = {}
    districts_out = []
    for rec in records:
        d = rec["d"]
        checks = member_checks(rec, system_names, ord_kind_names)
        for k, v in checks.items():
            bucket = agg.setdefault(k, {"언급확인": 0, "언급없음": 0})
            bucket[v] += 1
        districts_out.append({
            "dstrcAppnNo": d["dstrcAppnNo"],
            "dstrcNm": d["dstrcNm"].strip(),
            "mdAvailable": rec["md"] is not None,
            "members": checks,
        })

    aggregate = {}
    for k in sorted(agg):
        확인, 없음 = agg[k]["언급확인"], agg[k]["언급없음"]
        aggregate[k] = {"언급확인": 확인, "언급없음": 없음, "분모": 확인 + 없음}

    return {
        "$comment": (
            "지구별 바운더리 멤버(계통 규범 4·지정근거법 1·적용조례 3=8종)와 시행지침 md "
            "본문 언급 대조. md 전문은 공백 제거(re.sub(r'\\s+','',...)) 후 부분 문자열로 "
            "검색한다. 판정 필드 언급확인/언급없음은 '언급은 적용의 증거이지 적용 판정이 "
            "아니다'라는 뜻이다 — 언급이 없어도 바운더리 소속은 유지된다(결정론의 정의). "
            "조례는 계통 수준 언급만 본다(예: '도시계획조례' 문자열 존재 여부) — 지자체명까지 "
            "붙인 정밀 대조(예: '서울특별시 도시계획조례')는 lcCode→시군명 표가 없어 v1 에서 "
            "못 한다. 정밀 대조는 스펙 ③ 의 실제성 검토 입력이다. md 가 없는 지구(광교 1건, "
            "output/legal/markdown 변환 예외)는 mdAvailable=false 이며 전 멤버 언급없음으로 "
            "집계된다 — '확인되지 않음'이지 '언급이 없다고 확정됨'이 아니므로 mdAvailable 로 "
            "구분한다. 법률 별칭 국토의계획및이용에관한법률→국토계획법, "
            "공익사업을위한토지등의취득및보상에관한법률→토지보상법 도 언급확인으로 인정한다."
        ),
        "districtCount": len(records),
        "aggregate": aggregate,
        "districts": districts_out,
    }


# ---------------------------------------------------------------------------
# 강일 전방향 유도 도해 — ontology-boundary-example.{dot,svg,png}
# ---------------------------------------------------------------------------

def build_demo_dot(records, roots, system_names, ord_kind_names):
    rec = next(r for r in records if r["d"]["dstrcAppnNo"] == DEMO_DSTRC)
    d, obs = rec["d"], rec["observation"]
    lc5 = d["lcCode"][:5]
    subj = ordinance_subject(lc5)
    law = d["lawordNm"]
    checks = member_checks(rec, system_names, ord_kind_names)
    mandatory = sorted(roots["mandatoryClauses"], key=lambda e: e["clause"])

    sys_ids = {name: f"sys{i}" for i, name in enumerate(system_names)}
    ord_ids = {kind: f"ordk{i}" for i, kind in enumerate(ord_kind_names)}

    def esc(s):
        return str(s).replace('"', '\\"')

    def n(id_, label, fill, color, extra=""):
        return f'  "{id_}" [label="{esc(label)}", fillcolor="{fill}", color="{color}"{extra}];'

    B, G, GR, RED = "#1a5490", "#0b6b3a", "#888888", "#b00030"
    gov_label = GOV_NAME.get(subj, f"lcCode {subj}")

    L = ["digraph boundary {",
         '  graph [rankdir=TB, fontname="Helvetica", labelloc=t, fontsize=18,',
         f'         label="법령에서 지구로 — 바운더리 유도  (ontology-instance-example.png 의 역방향)\\n'
         f'{d["dstrcNm"].strip()} ({DEMO_DSTRC}) 하나로 보는 전방향 유도 · md 언급 대조\\n'
         f'.claude/skills/kb/kb-ontology/scripts/build_boundary.py 가 생성\\n ",',
         '         nodesep=0.35, ranksep=0.65, bgcolor="white", splines=spline];',
         '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10,',
         '        margin="0.15,0.09"];',
         '  edge [fontname="Helvetica", fontsize=8, arrowsize=0.7];', ""]

    # 계통 규범
    L += ["  subgraph cluster_system {",
          f'    label="계통 규범 (전 지구 공통)"; fontsize=11; fontcolor="{B}";',
          f'    style="rounded,filled"; color="{B}"; fillcolor="#eef4fa"; penwidth=1.4;']
    for name in system_names:
        extra = ", penwidth=2.2" if name == "국토의계획및이용에관한법률" else ""
        L.append(n(sys_ids[name], name, "#dce8f5", B, extra))
    L.append("  }")
    L.append("")

    # 제52조 필수 2호 — 강조 (장식 — 유도·대조 간선에는 참여하지 않는다)
    L += ["  subgraph cluster_mandatory {",
          f'    label="국토계획법 제52조제1항 법정 필수 (전 지구)"; fontsize=10; fontcolor="{RED}";',
          f'    style="rounded,filled,dashed"; color="{RED}"; fillcolor="#fdeef2"; penwidth=1.6;']
    for i, m in enumerate(mandatory):
        L.append(n(f"mand{i}", f'{m["clause"]}\\n{m["content"]}', "#fbdbe4", RED))
    L.append("  }")
    for i in range(len(mandatory)):
        L.append(f'  "{sys_ids["국토의계획및이용에관한법률"]}" -> "mand{i}" '
                 f'[style=dotted, arrowhead=none, color="{RED}"];')
    L.append("")

    # 지정근거법
    L += ["  subgraph cluster_design {",
          f'    label="지정 근거법 (lawordNm)"; fontsize=11; fontcolor="{B}";',
          f'    style="rounded,filled"; color="{B}"; fillcolor="#eef4fa"; penwidth=1.4;',
          n("law", law, "#dce8f5", B, ", penwidth=2.2"),
          "  }", ""]

    # 적용조례 3
    L += ["  subgraph cluster_ord {",
          f'    label="{gov_label} 적용 조례 (광역 승격)"; fontsize=11; fontcolor="{B}";',
          f'    style="rounded,filled"; color="{B}"; fillcolor="#eef4fa"; penwidth=1.4;']
    for kind in ord_kind_names:
        L.append(n(ord_ids[kind], f"{gov_label}\\n{kind}", "#dce8f5", B))
    L.append("  }")
    L.append("")

    # 지구
    L.append(f'  "dist" [label="lp:District\\n{d["dstrcNm"].strip()}\\n{DEMO_DSTRC}\\n'
              f'관측 {obs["observationYear"]}년 ({obs["basis"]}·{obs["precision"]})\\n'
              f'적용 판본 미확정", '
              f'fillcolor="#c9e8d6", color="{G}", penwidth=2.4, fontsize=12];')
    L.append("")

    members = [(sys_ids[nm], nm) for nm in system_names] + [("law", law)] + \
              [(ord_ids[k], k) for k in ord_kind_names]

    L.append("  ##  유도 — 법령에서 지구로")
    for node_id, _ in members:
        L.append(f'  "{node_id}" -> "dist" [label="유도", color="{B}", fontcolor="{B}", '
                  f'penwidth=1.6];')

    L.append("")
    L.append("  ##  대조 — 지구에서 지침 md 로. 실선=언급확인 · 점선=언급없음")
    for node_id, member_name in members:
        status = checks[member_name]
        if status == "언급확인":
            L.append(f'  "dist" -> "{node_id}" [label="언급확인", color="{G}", fontcolor="{G}", '
                      f'style=solid, penwidth=1.4, constraint=false];')
        else:
            L.append(f'  "dist" -> "{node_id}" [label="언급없음", color="{GR}", fontcolor="{GR}", '
                      f'style=dashed, penwidth=1.0, constraint=false];')

    L += ["", "  subgraph cluster_legend {",
          '    label="읽는 법"; fontsize=11; fontcolor="#333333";',
          '    style="rounded,filled"; color="#cccccc"; fillcolor="#fbfbfb"; margin=10;',
          '    "legend" [shape=plaintext, style="", fillcolor="#fbfbfb", label=<',
          '      <table border="0" cellborder="0" cellspacing="3">',
          f'      <tr><td align="left"><font point-size="10" color="{B}">'
          f'━━ 파랑 실선 = 유도 (법령 → 지구, 결정론 — 지침 인용과 무관하게 성립)</font></td></tr>',
          f'      <tr><td align="left"><font point-size="10" color="{G}">'
          f'━━ 초록 실선 = 언급확인 (md 대조 — 적용의 증거이지 판정 아님)</font></td></tr>',
          f'      <tr><td align="left"><font point-size="10" color="{GR}">'
          f'┅┅ 회색 점선 = 언급없음 (바운더리 소속은 그대로 유지된다)</font></td></tr>',
          f'      <tr><td align="left"><font point-size="10" color="{RED}">'
          f'┄┄ 빨강 점선 = 국토계획법 제52조제1항 법정 필수 2호 (전 지구 커버리지 제약)</font></td></tr>',
          '      </table>>];',
          "  }", "}"]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------

def build_records(districts):
    records = []
    for d in sorted(districts, key=lambda x: x["dstrcAppnNo"]):
        text = md_text(d)
        records.append(dict(d=d, md=text,
                             squashed=squash(text) if text else None,
                             observation=R.resolve(d["dstrcAppnNo"], text)))
    return records


def main():
    districts, roots = load()
    records = build_records(districts)
    system_names = sorted(e["statute"] for e in roots["systemRoots"])
    ord_kind_names = [k["kind"] for k in verified_ordinance_kinds(roots)]

    ttl_text = build_ttl(districts, roots, records)
    os.makedirs(os.path.dirname(OUT_TTL), exist_ok=True)
    with open(OUT_TTL, "w", encoding="utf-8") as f:
        f.write(ttl_text)

    report = build_report(records, system_names, ord_kind_names)
    os.makedirs(os.path.dirname(OUT_REPORT), exist_ok=True)
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")

    dot_text = build_demo_dot(records, roots, system_names, ord_kind_names)
    with open(OUT_DOT, "w", encoding="utf-8") as f:
        f.write(dot_text)
    for fmt, out, extra in (("svg", OUT_SVG, []), ("png", OUT_PNG, ["-Gdpi=150"])):
        r = subprocess.run(["dot", f"-T{fmt}"] + extra + [OUT_DOT, "-o", out],
                            capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr, file=sys.stderr)
            return 1

    print(f"지구 {len(districts)} · boundary.ttl {len(ttl_text.splitlines())} 줄")
    mand_conf = collections.Counter(
        s for rec in records for s in member_checks(rec, system_names, ord_kind_names).values())
    print(f"  언급확인 {mand_conf['언급확인']} · 언급없음 {mand_conf['언급없음']}")
    for p in (OUT_TTL, OUT_REPORT, OUT_DOT, OUT_SVG, OUT_PNG):
        print(f"  {os.path.relpath(p, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
