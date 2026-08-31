#!/usr/bin/env python3
"""파이프라인 단계별 커버리지를 원자료에서 재계산해 리포트를 낸다.

단계마다 분자·분모·정본 경로·입력 파일의 생성근거를 담고, 저신뢰·OCR 유래 건을
별도 필드로 갈라 제외 시 수치를 함께 낸다. 입력이 없는 단계는 `미집계` 이며
실패가 아니다.

집계 시각을 담지 않는다 — 같은 입력이면 같은 바이트가 나와야 하기 때문이다.
시각 대신 입력 파일이 스스로 밝힌 생성근거(생성일시·스크립트·근거 문장)를 옮긴다.

  입력  output/legal/시행지침/{meta.json,*/_index.json}
        output/legal/markdown/**/*.md
        output/legal/word/{terms.json,doc_definitions.json,_extraction_report.json,
                           _low_confidence_review.json,_ocr_only_terms.json}
        output/legal/analysis/시행지침_목차구조_전수조사.csv
        output/legal/statute/{statute_citations.json,statute_master.json,_statute_report.json}
        output/kb/reports/_guideline_tree.json
        output/kb/graph/{det,prob}/*.ttl
  출력  output/legal/analysis/_coverage.json

  python3 scripts/build_coverage.py
  python3 scripts/build_coverage.py --baseline output/legal/analysis/_coverage.json
"""
import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
OUT = ROOT / "output/legal/analysis/_coverage.json"
SKILL_REL = ".claude/skills/legal/legal-coverage"

# 모수 정의의 정본은 legal-term 이다. 여기서 다시 정의하지 않는다 —
# 두 곳이 같은 값을 정하면 반드시 어긋난다.
MOSU_CANON = ".claude/skills/legal/legal-term/case/모수규약.md"

REGIONS = ("서울", "인천", "경기")
GUIDE_LABEL = "시행지침"        # 첨부 라벨. fileRegistNo=7 의 라벨 표기
TTL_TYPE_RE = re.compile(r"\ba\s+lp:(\w+)")
# 비율 반올림 자리. 자리수를 바꾸면 이전 리포트와 대조표가 통째로 흔들린다
RATIO_NDIGITS = 4


def rel(p):
    """저장소 기준 상대 경로. 저장소 밖이면 절대 경로를 그대로 쓴다."""
    p = Path(p).resolve()
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def load_json(p):
    path = ROOT / p
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def ratio(num, den):
    if not den:
        return None
    return round(num / den, RATIO_NDIGITS)


def basis(path, **fields):
    """입력 파일이 스스로 밝힌 생성근거. 없는 항목은 담지 않는다."""
    rec = {"파일": path}
    rec.update({k: v for k, v in fields.items() if v})
    return rec


def stage(sid, name, num, den, num_def, den_def, canon, bases,
          den_from=None, isolated=None, excluded=None, note=None):
    return {
        "stage_id": sid,
        "단계": name,
        "상태": "집계",
        "분자": num,
        "분모": den,
        "비율": ratio(num, den),
        "분자_정의": num_def,
        "분모_정의": den_def,
        "분모_출처단계": den_from,
        "정본": canon,
        "입력_생성근거": bases,
        "격리": isolated or {},
        "제외시": excluded or [],
        "비고": note,
    }


def unmeasured(sid, name, num_def, den_def, canon, reason, den_from=None):
    return {
        "stage_id": sid,
        "단계": name,
        "상태": "미집계",
        "분자": None,
        "분모": None,
        "비율": None,
        "분자_정의": num_def,
        "분모_정의": den_def,
        "분모_출처단계": den_from,
        "정본": canon,
        "입력_생성근거": [],
        "격리": {},
        "제외시": [],
        "미집계사유": reason,
        "비고": None,
    }


# ── 단계별 재계산 ──────────────────────────────────────────────────────────

def s1_scan():
    """수집 대상 — 택지정보시스템 스캔 지구 중 시행지침 첨부를 가진 것."""
    canon, bases, scanned, with_guide = [], [], 0, 0
    for r in REGIONS:
        p = f"output/legal/시행지침/{r}/_index.json"
        d = load_json(p)
        if d is None:
            continue
        canon.append(p)
        bases.append(basis(p, 스캔일시=d.get("scannedAt"),
                           근거=f"{r} 지구 목록 {d.get('total')}건 색인"))
        for it in d.get("items") or []:
            scanned += 1
            if any(GUIDE_LABEL in (a.get("label") or "")
                   for a in (it.get("attachments") or [])):
                with_guide += 1
    if not canon:
        return unmeasured(
            "S1", "수집 대상 지구",
            "첨부 라벨에 '시행지침' 이 있는 지구 수",
            "택지정보시스템 스캔 지구 전건",
            [f"output/legal/시행지침/{r}/_index.json" for r in REGIONS],
            "지역별 _index.json 이 하나도 없다")
    return stage(
        "S1", "수집 대상 지구", with_guide, scanned,
        "_index.json items 중 첨부 라벨에 '시행지침' 을 가진 지구 수",
        "_index.json items 전건 — 택지정보시스템 스캔 지구",
        canon, bases,
        note="시행지침 첨부가 없는 지구는 결손이 아니라 대상 밖이다")


def s2_collect(prev):
    """수집 성공 — meta.json 의 다운로드 기록. 원본 파일 실재는 보지 않는다."""
    p = "output/legal/시행지침/meta.json"
    d = load_json(p)
    if d is None:
        return unmeasured("S2", "수집 성공",
                          "meta.json districts 중 시행지침 파일 다운로드 기록이 있는 건",
                          "S1 분자", [p], "meta.json 이 없다", den_from="S1")
    ds = d.get("districts") or []
    ok = sum(1 for x in ds
             if any(GUIDE_LABEL in (f.get("label") or "")
                    for f in (x.get("downloaded") or [])))
    ext = collections.Counter()
    for x in ds:
        for f in x.get("downloaded") or []:
            ext[Path(f.get("savedAs") or "").suffix.lower() or "(확장자없음)"] += 1
    return stage(
        "S2", "수집 성공", ok, prev,
        "districts[].downloaded 에 '시행지침' 라벨 파일 기록이 있는 지구 수",
        "S1 분자 — 시행지침 첨부를 가진 지구",
        [p], [basis(p, 생성일시=d.get("generatedAt"),
                    근거="택지정보시스템 첨부 다운로드 기록")],
        den_from="S1",
        note="다운로드 기록을 센 값이다. 원본 파일 실재는 저장소 밖이라 확인하지 않는다"
             f" (확장자 분포 {dict(sorted(ext.items()))})")


def s3_markdown(prev):
    """md 변환 — 변환 결과 파일의 실재를 센다."""
    base = ROOT / "output/legal/markdown"
    if not base.is_dir():
        return unmeasured("S3", "md 변환", "output/legal/markdown 의 .md 파일 수",
                          "S2 분자", ["output/legal/markdown"],
                          "markdown 디렉터리가 없다", den_from="S2")
    files = sorted(base.rglob("*.md"))
    by_region = collections.Counter(f.relative_to(base).parts[0] for f in files)
    rep = load_json("output/legal/word/_extraction_report.json")
    iso, exc, bases = {}, [], [basis("output/legal/markdown",
                                     근거="지구별 병합 md. 변환 결과 파일의 실재를 센다")]
    if rep:
        docs = rep.get("documents") or []
        ocr = [x for x in docs if x.get("source_quality") == "OCR"]
        iso = {"OCR유래": len(ocr),
               "OCR유래_지구": sorted(x["dstrcAppnNo"] for x in ocr),
               "정본": ["output/legal/word/_extraction_report.json"]}
        exc = [{"제외": "OCR 유래 md",
                "분자": len(files) - len(ocr), "분모": prev - len(ocr),
                "비율": ratio(len(files) - len(ocr), prev - len(ocr))}]
        bases.append(basis("output/legal/word/_extraction_report.json",
                           생성일시=(rep.get("meta") or {}).get("생성일시"),
                           근거="문서별 source_quality — OCR 유래 md 판별"))
    return stage(
        "S3", "md 변환", len(files), prev,
        "output/legal/markdown/**/*.md 파일 수",
        "S2 분자 — 수집 성공 지구",
        ["output/legal/markdown"], bases, den_from="S2",
        isolated=iso, excluded=exc,
        note=f"지역별 {dict(sorted(by_region.items()))}")


def s4_definitions(prev, prev_ocr=0):
    """정의문 추출 성공 — terms.json occurrence 의 지구를 다시 센다.

    제외 시 수치에서 분자와 분모는 각자의 OCR 건수를 뺀다. 분모의 OCR 건수는 S3 의
    OCR 유래 md 수이며, 분자의 것과 다르다 — 효행지구는 md 는 OCR 로 적재됐으나
    정의문이 0건이라 분자에 들어 있지 않다.
    """
    p = "output/legal/word/terms.json"
    d = load_json(p)
    if d is None:
        return unmeasured("S4", "정의문 추출 성공",
                          "terms.json occurrences 의 dstrcAppnNo 고유 수",
                          "S3 분자", [p], "terms.json 이 없다", den_from="S3")
    terms = d.get("terms") or []
    docs, ocr_docs, n_occ, n_ocr = set(), set(), 0, 0
    for t in terms:
        for o in t.get("occurrences") or []:
            n_occ += 1
            docs.add(o.get("dstrcAppnNo"))
            if o.get("source_quality") == "OCR":
                n_ocr += 1
                ocr_docs.add(o.get("dstrcAppnNo"))
    low = load_json("output/legal/word/_low_confidence_review.json")
    ocr_only = load_json("output/legal/word/_ocr_only_terms.json")
    iso = {"OCR유래": len(ocr_docs), "OCR유래_지구": sorted(ocr_docs),
           "OCR유래_정의문": n_ocr, "정본": [p]}
    if low is not None:
        iso["저신뢰_분류용어"] = len(low.get("items") or [])
        iso["정본"].append("output/legal/word/_low_confidence_review.json")
    if ocr_only is not None:
        iso["OCR전용용어"] = len(ocr_only.get("terms") or [])
        iso["정본"].append("output/legal/word/_ocr_only_terms.json")
    n, dn = len(docs) - len(ocr_docs), prev - prev_ocr
    meta = d.get("meta") or {}
    return stage(
        "S4", "정의문 추출 성공", len(docs), prev,
        "terms.json occurrences 의 dstrcAppnNo 고유 수",
        "S3 분자 — md 변환 성공 문서",
        [p], [basis(p, 생성일시=meta.get("생성일시"),
                    근거=(meta.get("추출방법") or "")[:60],
                    스크립트=meta.get("스크립트경로"))],
        den_from="S3", isolated=iso,
        excluded=[{"제외": f"OCR 유래 — 분자에서 {len(ocr_docs)}건 · 분모에서 {prev_ocr}건",
                   "분자": n, "분모": dn, "비율": ratio(n, dn)}],
        note=f"정의문 총 {n_occ}건. 용어 {len(terms)}종. "
             f"등급 분모의 정본은 {MOSU_CANON} 이다")


def s5_definition_article(prev):
    """정의 조항 보유 — 조 표제로 재판정한 문서. S4 와 분모가 다르다."""
    p = "output/legal/word/doc_definitions.json"
    d = load_json(p)
    if d is None:
        return unmeasured("S5", "정의 조항 보유",
                          "doc_definitions.json records 의 source_file 고유 수",
                          "S4 분자", [p], "doc_definitions.json 이 없다", den_from="S4")
    recs = d.get("records") or []
    docs = {r.get("source_file") for r in recs if r.get("source_file")}
    meta = d.get("meta") or {}
    return stage(
        "S5", "정의 조항 보유", len(docs), prev,
        "doc_definitions.json records 의 source_file 고유 수",
        "S4 분자 — 정의문 추출 성공 문서",
        [p], [basis(p, 생성일시=meta.get("원본_생성일시"),
                    근거=meta.get("생성근거"), 스크립트=meta.get("스크립트"))],
        den_from="S4",
        note=f"레코드 {len(recs)}건. 정의 조항 소속만 센 값이라 S4 와 모수가 다르다 — "
             f"terms.json 의 동명 필드와 나란히 비교하지 않는다")


def s6_article_tree(prev):
    """조문 트리 발급 — 트리 리포트와 TTL 인스턴스를 각각 센다."""
    p = "output/kb/reports/_guideline_tree.json"
    d = load_json(p)
    ttl = ROOT / "output/kb/graph/det/guideline.ttl"
    if d is None and not ttl.exists():
        return unmeasured("S6", "조문 트리 발급",
                          "guideline.ttl 의 lp:Guideline 인스턴스 수",
                          "S3 분자", [p, "output/kb/graph/det/guideline.ttl"],
                          "조문 트리 리포트도 guideline.ttl 도 없다", den_from="S3")
    canon, bases, counts = [], [], {}
    if ttl.exists():
        canon.append("output/kb/graph/det/guideline.ttl")
        counts = collections.Counter(TTL_TYPE_RE.findall(ttl.read_text(encoding="utf-8")))
        bases.append(basis("output/kb/graph/det/guideline.ttl",
                           근거="det 층 지침 조문 트리. 정렬 순회로 생성"))
    no_def = []
    if d is not None:
        canon.insert(0, p)
        no_def = d.get("정의조항_없음") or []
        bases.insert(0, basis(p, 스크립트=d.get("생성스크립트"),
                              근거="조문 트리 발급 이력과 결손 목록"))
    num = counts.get("Guideline")
    if num is None:
        num = (d or {}).get("지침수")
    return stage(
        "S6", "조문 트리 발급", num, prev,
        "guideline.ttl 의 lp:Guideline 인스턴스 수",
        "S3 분자 — md 변환 성공 문서",
        canon, bases, den_from="S3",
        isolated={"정의조항_없는_지침": len(no_def), "정본": [p]} if d is not None else {},
        note=f"조항 {counts.get('GuidelineArticle')}개 · 정의 진술 "
             f"{counts.get('TermDefinition')}건 · 표목 {counts.get('PlanElement')}개")


def s7_citations(prev):
    """인용 간선 — 간선이 실제로 붙은 지구를 다시 센다."""
    p = "output/legal/statute/statute_citations.json"
    d = load_json(p)
    if d is None:
        return unmeasured("S7", "인용 간선",
                          "statute_citations.json citations[].districts 합집합 크기",
                          "S4 분자", [p], "statute_citations.json 이 없다", den_from="S4")
    cits = d.get("citations") or []
    districts, terms = set(), set()
    for c in cits:
        districts |= set(c.get("districts") or [])
        terms.add(c.get("term_id"))
    meta = d.get("meta") or {}
    canon, bases = [p], [basis(p, 생성일시=meta.get("원본_생성일시"),
                               근거=meta.get("생성근거"), 스크립트=meta.get("스크립트"))]
    iso = {"정본": []}
    master = load_json("output/legal/statute/statute_master.json")
    if master is not None:
        st = master.get("statutes") or []
        iso["법령_미대조"] = sum(1 for s in st if s.get("검증상태") == "미대조")
        iso["법령수"] = len(st)
        iso["정본"].append("output/legal/statute/statute_master.json")
        canon.append("output/legal/statute/statute_master.json")
    rep = load_json("output/legal/statute/_statute_report.json")
    if rep is not None:
        iso["미해소_인용"] = len(rep.get("unresolved") or [])
        iso["어휘_미포착"] = len(rep.get("vocab_missed") or [])
        iso["정본"].append("output/legal/statute/_statute_report.json")
    return stage(
        "S7", "인용 간선", len(districts), prev,
        "citations[].districts 합집합 크기 — 간선이 실제로 붙은 지구",
        "S4 분자 — 정의문 추출 성공 문서",
        canon, bases, den_from="S4", isolated=iso,
        note=f"간선 {len(cits)}건 · 인용 용어 {len(terms)}종. "
             f"미해소 인용은 간선을 만들지 않고 리포트에만 남는다")


def s8_ontology(prev):
    """온톨로지 적재 (det) — TTL 인스턴스를 센다."""
    det = ROOT / "output/kb/graph/det"
    if not det.is_dir():
        return unmeasured("S8", "온톨로지 적재 (det)",
                          "boundary.ttl 의 lp:District 인스턴스 수",
                          "S2 분자", ["output/kb/graph/det"],
                          "det 그래프 디렉터리가 없다", den_from="S2")
    counts, canon, bases = collections.Counter(), [], []
    for f in sorted(det.glob("*.ttl")):
        counts += collections.Counter(TTL_TYPE_RE.findall(f.read_text(encoding="utf-8")))
        canon.append(rel(f))
        bases.append(basis(rel(f), 근거="det 층 — 근거 사슬이 이어지는 진술"))
    if not canon:
        return unmeasured("S8", "온톨로지 적재 (det)",
                          "boundary.ttl 의 lp:District 인스턴스 수",
                          "S2 분자", ["output/kb/graph/det"],
                          "det 층 ttl 파일이 없다", den_from="S2")
    return stage(
        "S8", "온톨로지 적재 (det)", counts.get("District", 0), prev,
        "det 층 ttl 의 lp:District 인스턴스 수",
        "S2 분자 — 수집 성공 지구",
        canon, bases, den_from="S2",
        note="타입별 인스턴스 " + json.dumps(dict(sorted(counts.items())), ensure_ascii=False))


def s9_prob():
    """온톨로지 적재 (prob) — 입력이 없으면 미집계다. 0 이 아니다."""
    prob = ROOT / "output/kb/graph/prob"
    files = sorted(prob.glob("*.ttl")) if prob.is_dir() else []
    if not files:
        return unmeasured(
            "S9", "온톨로지 적재 (prob)",
            "prob 층 ttl 의 인스턴스 수",
            "S4 분자 — 판정·분류의 대상 문서",
            ["output/kb/graph/prob"],
            "prob 층 ttl 파일이 없다. 판정층이 아직 산출되지 않았으므로 0 이 아니라 미집계다",
            den_from="S4")
    counts = collections.Counter()
    for f in files:
        counts += collections.Counter(TTL_TYPE_RE.findall(f.read_text(encoding="utf-8")))
    return stage(
        "S9", "온톨로지 적재 (prob)", sum(counts.values()), None,
        "prob 층 ttl 의 인스턴스 수", "없음 — 진술 수는 문서 모수와 축이 다르다",
        [rel(f) for f in files],
        [basis(rel(f), 근거="prob 층 — 판정·분류·빈도. 추론 금지") for f in files],
        note="타입별 인스턴스 " + json.dumps(dict(sorted(counts.items())), ensure_ascii=False))


# ── 어긋남 · 격리 · 사각지대 ──────────────────────────────────────────────

def ubiquity_denominator(candidates):
    """taxonomy.json 의 등급 분포를 재현하는 분모를 찾아 설명 문구와 대조한다.

    대조하는 두 값(`axis_D.설명` 이 밝힌 분모와 `값별_용어수`)은 모두 taxonomy.json
    안에 있다. terms.json 은 등급을 다시 세는 도구이지 대조 상대가 아니다.

    등급 판정에 어느 모수를 쓰는지는 legal-term 의 모수규약이 정본이다. 여기서는
    문서에 적힌 분모와 실제로 쓰인 분모가 같은지만 본다.
    """
    tx = load_json("output/legal/word/taxonomy.json")
    t = load_json("output/legal/word/terms.json")
    if tx is None or t is None:
        return None
    ax = tx.get("axis_D") or {}
    dist = ax.get("값별_용어수")
    rules = ax.get("판정기준")
    if not dist or not rules:
        return None
    # 판정기준에서 각 등급의 하한을 뽑아 내림차순 사다리를 만든다.
    # 하한이 없는 규칙(`ratio < 0.10`)이 최하 등급이다
    ladder = []
    for r in rules:
        m = re.search(r"([0-9.]+)\s*<=|>=\s*([0-9.]+)", r.get("조건", ""))
        if m:
            ladder.append((float(m.group(1) or m.group(2)), r["값"]))
    if not ladder:
        return None
    ladder.sort(reverse=True)
    graded = {name for _, name in ladder}
    lowest = [r["값"] for r in rules if r["값"] not in graded]
    freqs = [x.get("doc_frequency") or 0 for x in (t.get("terms") or [])]

    def grade(den):
        c = collections.Counter()
        for f in freqs:
            r = f / den
            for thr, name in ladder:
                if r >= thr:
                    c[name] += 1
                    break
            else:
                c[lowest[0] if lowest else "미상"] += 1
        return dict(c)

    reproduced = [d for d in sorted(set(candidates)) if d and grade(d) == dist]
    declared = re.search(r"대상문서수\s*\((\d+)\)", ax.get("설명") or "")
    declared = int(declared.group(1)) if declared else None
    if declared is None or (reproduced and declared in reproduced):
        return None
    return {
        "파일": "output/legal/word/taxonomy.json",
        "필드": "axis_D.설명 의 분모",
        "대조범위": "파일내부",
        "기재": declared,
        "재계산": {
            "값별_용어수를 재현하는 분모": reproduced or "후보 분모 어느 것도 재현하지 못함",
            f"분모 {declared} 로 재계산한 분포": grade(declared),
            "실린 분포": dist,
        },
        "판정": "어긋남 — 설명에 적힌 분모로는 실린 등급 분포가 나오지 않는다. "
              "분포 자체가 아니라 분모 설명이 틀렸을 수 있으므로 고치지 말고 넘긴다",
    }


def mismatches(candidates=()):
    """같은 파일 안의 요약과 그 파일의 원자료를 대조한다.

    파일 간 동명 필드는 재계산 필드이므로 여기서 비교하지 않는다.
    """
    out = []
    t = load_json("output/legal/word/terms.json")
    if t is not None:
        terms = t.get("terms") or []
        paths = {
            "occurrences 배열 길이 합": sum(len(x.get("occurrences") or []) for x in terms),
            "occurrence_count 필드 합": sum(x.get("occurrence_count") or 0 for x in terms),
            "variants[].count 합": sum(v.get("count") or 0 for x in terms
                                       for v in (x.get("variants") or [])),
        }
        declared = (t.get("meta") or {}).get("추출정의문수_총")
        if declared is not None and any(v != declared for v in paths.values()):
            out.append({
                "파일": "output/legal/word/terms.json",
                "필드": "meta.추출정의문수_총",
                "대조범위": "파일내부",
                "기재": declared,
                "재계산": paths,
                "판정": "어긋남 — 요약이 원자료와 다르다. 요약을 그대로 옮기지 않는다",
            })
        docs = {o.get("dstrcAppnNo") for x in terms for o in (x.get("occurrences") or [])}
        dec_docs = (t.get("meta") or {}).get("정의문_추출성공_문서수")
        if dec_docs is not None and dec_docs != len(docs):
            out.append({
                "파일": "output/legal/word/terms.json",
                "필드": "meta.정의문_추출성공_문서수",
                "대조범위": "파일내부",
                "기재": dec_docs,
                "재계산": {"occurrences 의 dstrcAppnNo 고유 수": len(docs)},
                "판정": "어긋남",
            })
    rep = load_json("output/legal/word/_extraction_report.json")
    if rep is not None:
        docs = rep.get("documents") or []
        s = rep.get("summary") or {}
        for field, got in (("총문서수", len(docs)),
                           ("정의조항_보유문서수",
                            sum(1 for x in docs if x.get("has_definition_article"))),
                           ("정의조항_없는문서수",
                            sum(1 for x in docs if not x.get("has_definition_article")))):
            if field in s and s[field] != got:
                out.append({
                    "파일": "output/legal/word/_extraction_report.json",
                    "필드": f"summary.{field}", "대조범위": "파일내부", "기재": s[field],
                    "재계산": {"documents 재계산": got}, "판정": "어긋남",
                })
    sc = load_json("output/legal/statute/statute_citations.json")
    if sc is not None:
        dec = (sc.get("meta") or {}).get("인용수")
        got = len(sc.get("citations") or [])
        if dec is not None and dec != got:
            out.append({
                "파일": "output/legal/statute/statute_citations.json",
                "필드": "meta.인용수", "대조범위": "파일내부", "기재": dec,
                "재계산": {"citations 길이": got}, "판정": "어긋남",
            })
    ub = ubiquity_denominator(candidates)
    if ub:
        out.append(ub)
    return out


def recalc_fields():
    """이름이 같아도 나란히 비교하지 않는 필드. 비교 금지의 근거를 남긴다."""
    rows = []
    rep = load_json("output/legal/word/_extraction_report.json")
    doc = load_json("output/legal/word/doc_definitions.json")
    if rep is not None and doc is not None:
        rows.append({
            "필드명": "정의조항_보유문서수",
            "값": {
                "output/legal/word/_extraction_report.json":
                    (rep.get("summary") or {}).get("정의조항_보유문서수"),
                "output/legal/word/doc_definitions.json":
                    (doc.get("meta") or {}).get("정의조항_보유문서수"),
            },
            "비교금지_사유": "판정 규칙이 다르다. 앞은 추출기의 has_definition_article, "
                          "뒤는 조 표제로 다시 판정한 값이다. 어느 쪽이 옳은지는 계약이 "
                          "정할 일이며 이 집계는 어긋남으로 세지 않는다",
        })
    t = load_json("output/legal/word/terms.json")
    if t is not None and doc is not None:
        rows.append({
            "필드명": "정의문수 / 정의문_추출성공 모수",
            "값": {
                "output/legal/word/terms.json":
                    (t.get("meta") or {}).get("정의문_추출성공_문서수"),
                "output/legal/word/doc_definitions.json":
                    (doc.get("meta") or {}).get("정의조항_보유문서수"),
            },
            "비교금지_사유": f"등급 체계와 분모가 다르다. 정본은 {MOSU_CANON}",
        })
    return rows


def isolation_summary():
    """저신뢰·OCR 격리 파일의 건수를 한자리에 모은다. 확정분과 섞지 않는다."""
    rows = []
    for p, key, label in (
        ("output/legal/word/_low_confidence_review.json", "items", "분류 저신뢰 용어"),
        ("output/legal/word/_ocr_only_terms.json", "terms", "OCR 문서에만 있는 용어"),
    ):
        d = load_json(p)
        if d is not None:
            rows.append({"정본": p, "구분": label, "건수": len(d.get(key) or [])})
    d = load_json("output/legal/statute/_statute_report.json")
    if d is not None:
        rows.append({"정본": "output/legal/statute/_statute_report.json",
                     "구분": "미해소 인용 (간선 미생성)", "건수": len(d.get("unresolved") or [])})
    d = load_json("output/legal/statute/_collect_report.json")
    if d is not None:
        rows.append({"정본": "output/legal/statute/_collect_report.json",
                     "구분": "법령 정본 미대조", "건수": len(d.get("unmatched") or [])})
    csv_path = ROOT / "output/legal/analysis/시행지침_목차구조_전수조사.csv"
    if csv_path.exists():
        with csv_path.open(encoding="utf-8-sig") as fh:
            rows_csv = list(csv.DictReader(fh))
        rows.append({"정본": "output/legal/analysis/시행지침_목차구조_전수조사.csv",
                     "구분": "목차 구조 판정 신뢰도 낮음",
                     "건수": sum(1 for r in rows_csv if r.get("신뢰도") == "낮음"),
                     "모수": len(rows_csv)})
        rows.append({"정본": "output/legal/analysis/시행지침_목차구조_전수조사.csv",
                     "구분": "OCR 유래 md",
                     "건수": sum(1 for r in rows_csv if r.get("OCR여부") == "Y"),
                     "모수": len(rows_csv)})
    return rows


BLIND_SPOTS = [
    "원본 hwp·pdf 는 저장소 밖(.gitignore)이라 S2 는 파일 실재가 아니라 meta.json 의 "
    "다운로드 기록을 센다. 기록은 있는데 파일이 깨진 경우를 이 집계는 보지 못한다",
    "지구 단위로만 센다. 한 지구의 md 안에서 편·장이 빠졌는지는 보지 않는다 — "
    "그것은 legal-toc 의 목차구조 전수조사가 다룬다",
    "값의 옳고 그름을 보지 않는다. 정의문이 추출됐다는 것과 그 정의문이 맞다는 것은 다르다",
    "격리 파일에 기록되지 않은 결손은 셀 수 없다. 리포트에 남지 않은 누락은 이 집계에서 "
    "성공으로 보인다",
    "meta 요약과 원자료의 어긋남은 같은 파일 안에서만 본다. 파일 간 동명 필드는 재계산 "
    "필드라 어긋남으로 세지 않으므로, 두 파일이 함께 틀린 경우는 드러나지 않는다",
]


# ── 전후 대조표 ────────────────────────────────────────────────────────────

def diff(baseline, stages):
    """이전 리포트와의 전후 대조. 무엇이 얼마나 움직였는지만 적는다."""
    old = {s["stage_id"]: s for s in baseline.get("stages") or []}
    new = {s["stage_id"]: s for s in stages}
    changes = []
    for sid in sorted(set(old) | set(new)):
        o, n = old.get(sid), new.get(sid)
        if o is None:
            changes.append({"stage_id": sid, "단계": n["단계"], "변화": "신규 단계"})
            continue
        if n is None:
            changes.append({"stage_id": sid, "단계": o["단계"], "변화": "사라진 단계"})
            continue
        for f in ("상태", "분자", "분모", "비율"):
            if o.get(f) != n.get(f):
                changes.append({"stage_id": sid, "단계": n["단계"], "필드": f,
                                "이전": o.get(f), "이후": n.get(f)})
    moved = [c for c in changes
             if c.get("필드") == "비율" and c.get("이전") is not None
             and c.get("이후") is not None and abs(c["이후"] - c["이전"]) >= 0.05]
    state = [c for c in changes if c.get("필드") == "상태" or "변화" in c]
    return {
        "기준": baseline.get("_기준경로"),
        "변경": changes,
        "핵심결론_뒤집힘": "변화없음" if not (moved or state) else "검토필요",
        "판정근거": "비율이 0.05 이상 움직인 단계도, 상태가 바뀐 단계도 없다"
        if not (moved or state) else
        f"비율 0.05 이상 이동 {len(moved)}건 · 상태 변화 {len(state)}건. "
        f"결론이 실제로 뒤집히는지는 소유자가 한 줄로 답한다",
    }


def main():
    ap = argparse.ArgumentParser(description="파이프라인 단계별 커버리지 재계산")
    ap.add_argument("--baseline", help="전후 대조표를 낼 이전 리포트 경로")
    ap.add_argument("--out", default=str(OUT), help="출력 경로")
    args = ap.parse_args()

    stages = []
    s1 = s1_scan(); stages.append(s1)
    s2 = s2_collect(s1["분자"]); stages.append(s2)
    s3 = s3_markdown(s2["분자"]); stages.append(s3)
    s4 = s4_definitions(s3["분자"], (s3["격리"] or {}).get("OCR유래", 0)); stages.append(s4)
    stages.append(s5_definition_article(s4["분자"]))
    stages.append(s6_article_tree(s3["분자"]))
    stages.append(s7_citations(s4["분자"]))
    stages.append(s8_ontology(s2["분자"]))
    stages.append(s9_prob())

    report = {
        "meta": {
            "생성근거": "각 단계의 원자료를 다시 세어 산출한다. 남이 만든 meta 요약을 "
                     "옮기지 않는다",
            "스크립트": f"{SKILL_REL}/scripts/build_coverage.py",
            "모수정의_정본": MOSU_CANON,
            "집계시각_미기재_사유": "같은 입력에 같은 산출물이어야 한다. 시각 대신 입력 "
                              "파일이 스스로 밝힌 생성근거를 옮긴다",
            "단계수": len(stages),
            "집계": sum(1 for s in stages if s["상태"] == "집계"),
            "미집계": sum(1 for s in stages if s["상태"] == "미집계"),
        },
        "stages": stages,
        "격리": isolation_summary(),
        "재계산필드": recalc_fields(),
        "어긋남": mismatches([s["분자"] for s in stages if s["상태"] == "집계"]),
        "사각지대": BLIND_SPOTS,
        "대조표": None,
    }
    if args.baseline:
        bp = Path(args.baseline)
        if not bp.is_absolute():
            bp = ROOT / bp
        if not bp.exists():
            sys.stderr.write(f"기준 리포트 없음: {bp}\n")
            return 2
        base = json.loads(bp.read_text(encoding="utf-8"))
        base["_기준경로"] = rel(bp)
        report["대조표"] = diff(base, stages)

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")

    for s in stages:
        if s["상태"] == "미집계":
            print(f"  {s['stage_id']} {s['단계']}: 미집계 — {s['미집계사유']}")
        else:
            r = "" if s["비율"] is None else f" ({s['비율']:.1%})"
            print(f"  {s['stage_id']} {s['단계']}: {s['분자']}/{s['분모']}{r}")
    print(f"어긋남 {len(report['어긋남'])}건 · 격리 {len(report['격리'])}종 → {rel(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
