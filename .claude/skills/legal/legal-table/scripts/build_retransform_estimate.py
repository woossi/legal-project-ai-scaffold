#!/usr/bin/env python3
"""통합 재변환 견적 — 조문 무결성(codex) × 표 손실(legal-table) 을 join 한다.

**견적서이지 판단서가 아니다.** 재변환 실행 여부는 사용자가 정한다. 이 산출물은
이득과 대가를 같은 표에 나란히 놓는 데서 멈춘다 — 권고하지 않는다.

join 키 함정 — codex `documents[].lc5` 는 이름과 달리 **14자리 지구번호**다
(`41820DA2016001`). 이쪽 `dstrcAppnNo` 와 189/189 매칭한다. 이쪽 `lc5`(5자리
법정동코드 `41590`)와 붙이면 전건 불일치가 난다. 이름이 같은데 값 도메인이 다른
필드를 나란히 비교하지 않는다(`프로젝트-설계구조.md` §3 재계산 필드).

입력  output/legal/analysis/시행지침_조문_무결성_검증.json
      output/legal/table/_table_loss.json
      output/legal/table/norm_values.json
출력  output/legal/table/_retransform_estimate.json
      output/legal/table/_retransform_estimate.md
"""

import argparse
import collections
import hashlib
import json
import os
import sys

CODEX = "output/legal/analysis/시행지침_조문_무결성_검증.json"
CODEX_MD = "output/legal/analysis/시행지침_무결성_재변환_견적.md"
TABLE_LOSS = "output/legal/table/_table_loss.json"
NORM_VALUES = "output/legal/table/norm_values.json"
OUT_JSON = "output/legal/table/_retransform_estimate.json"
OUT_MD = "output/legal/table/_retransform_estimate.md"

# 대가 4항. codex 견적서 §재변환 연쇄비용이 정본이며 그대로 인용한다.
CHAIN_COST = [
    "`guideline_source_article_corpus.jsonl.gz` 19,357단위의 줄범위와 "
    "SHA-256을 전면 재생성한다.",
    "`legal-statute/GUIDE.md` 검증 게이트 12를 다시 통과시킨다.",
    "줄번호를 정본으로 쓰는 `legal-xref`와 `legal-contrast` 산출물 10개 파일을 "
    "재구축하고 검증한다.",
    "재변환 대상별 표 캡션·절 표목·용지 주어 복원을 다시 검사한다.",
]
CHAIN_COST_SOURCE = f"{CODEX_MD} §재변환 연쇄비용 (그대로 인용)"

# 45 의 모수 정의가 이 저장소 산출물에서 확인되지 않는다. 숫자를 발명하지 않는다.
UNRESOLVED_45 = {
    "수치": 45,
    "출처": "오케스트레이터 실측으로 전달된 값. 이 저장소 산출물에서 재현되지 않는다",
    "확인된_인접값": {
        "조례_관할_지구수": 27,
        "근거": "output/kb/norm/reports/_norm_value.json",
    },
    "상태": "모수_미확정",
    "왜_계산하지_않는가": (
        "45 가 어떤 모수(전체 189 중 조례 규범값과 대조 가능한 지구 수인지, "
        "다른 분모인지)로 산정됐는지 확인되지 않는다. 모수를 모르는 채로 "
        "'45 → N' 형태의 증가분을 내면 숫자를 발명하는 것이다."),
    "대신_무엇을_냈는가": (
        "회수 가능 지구를 이쪽 모수로 정확히 셌다. `회수모수` 절의 수치는 "
        "전부 이 저장소 산출물에서 재현된다."),
    "대입_방법": (
        "45 의 모수 정의가 확인되면, 그 정의에 해당하는 지구 집합과 "
        "`회수모수.재변환시_값회수가능` 의 교집합을 세어 대입한다. "
        "이 파일은 그 교집합을 계산하지 않는다."),
}


def _integrity_class(d):
    """문서 단위 조문 무결성 지배값과 근거.

    **건수 max 로 뽑지 않는다.** `complete_count` 가 거의 항상 최대라 결함이
    가려진다(실측: max 로 뽑으면 104문서가 `complete`). 결함 유무가 처방을
    가르므로 결함 우선순위로 판정한다.

    `조문없음` 은 원본·markdown 양쪽에 조문이 0개인 문서다. `완전` 과 구분한다 —
    검증할 조문이 없던 것이지 검증을 통과한 것이 아니다.
    """
    if d["verification_status"] != "verified":
        return "미검증", (
            "codex verification_status=unverified — 원본 대조 증거에서 제외됐다. "
            f"사유: {(d.get('unverified_reason') or '')[:120]}")
    if d["source_article_count"] == 0 and d["markdown_h4_article_count"] == 0:
        return "조문없음", (
            "원본·markdown 양쪽 조문 0개. 검증할 조문이 없었던 것이지 "
            "검증을 통과한 것이 아니다")
    parts = (f"완전 {d['complete_count']} · 본문누락 {d['body_missing_count']} · "
             f"구조누락 {d['structure_missing_count']} · "
             f"순서훼손 {d['order_damage_count']} · "
             f"표대기 {d['table_pending_unclassified_count']}")
    if d["structure_missing_count"]:
        return "구조누락", f"구조누락 {d['structure_missing_count']}건 우선. {parts}"
    if d["body_missing_count"]:
        return "본문누락", f"본문누락 {d['body_missing_count']}건 우선. {parts}"
    if d["order_damage_count"]:
        return "순서훼손", f"순서훼손 {d['order_damage_count']}건 우선. {parts}"
    return "완전", f"결함 0건. {parts}"


def load(root):
    with open(os.path.join(root, CODEX), encoding="utf-8") as fh:
        cx = json.load(fh)
    with open(os.path.join(root, TABLE_LOSS), encoding="utf-8") as fh:
        tl = json.load(fh)
    with open(os.path.join(root, NORM_VALUES), encoding="utf-8") as fh:
        nv = json.load(fh)
    return cx, tl, nv


def build(root="."):
    cx, tl, nv = load(root)
    C = {d["lc5"]: d for d in cx["documents"]}
    T = {r["dstrcAppnNo"]: r for r in tl["records"]}

    join_ok = set(C) == set(T)
    path_mismatch = [k for k in C
                     if k in T and C[k]["markdown_path"] != T[k]["source_file"]]

    # 값이 회수된 지구 (규범값 기준)
    norm_docs = {r["dstrcAppnNo"] for r in nv["records"]
                 if r["context_class"] == "규범"}
    # 주어까지 붙은 값이 있는 지구 — codex 가 정의한 `값 존재·의미 결손` 의 반대편
    subject_docs = {r["dstrcAppnNo"] for r in nv["records"]
                    if r["context_class"] == "규범" and r["subject"]}

    rows = []
    for k in sorted(T):
        t, c = T[k], C.get(k)
        icls, ibasis = _integrity_class(c) if c else (None, "codex 문서 없음")
        rows.append({
            "dstrcAppnNo": k,
            "lc5": t["lc5"],
            "지역": t["지역"],
            "district": t["district"],
            "source_file": t["source_file"],
            # 표 축
            "loss_class": t["loss_class"],
            "recovery_grade": t["recovery_grade"],
            "소실표건수": t["소실표건수"],
            "값줄수": t["값줄수"],
            "확정값수": t["확정값수"],
            "표_재변환후보": t["reconvert_candidate"],
            # 조문 축
            "article_integrity_class": icls,
            "article_integrity_basis": ibasis,
            "조문_재변환후보": bool(c and c["retransform_candidate"]),
            "조문_재변환사유": (
                [r for cand in cx["retransform_candidates"]
                 if cand["lc5"] == k for r in cand["reasons"]] or None),
            "codex_verification_status": c["verification_status"] if c else None,
            # 의미 결손 — codex 가 정의한 판정
            "값_의미결손": (k in norm_docs and k not in subject_docs),
        })

    novalue = {r["dstrcAppnNo"] for r in rows if r["값줄수"] == 0}
    artcand = {r["dstrcAppnNo"] for r in rows if r["조문_재변환후보"]}
    tabcand = {r["dstrcAppnNo"] for r in rows if r["표_재변환후보"]}
    allk = set(T)

    cross = {
        "설명": "값 미보유 96 × 조문 재변환 후보 80. 네 칸의 합이 189다",
        "둘다": sorted(novalue & artcand),
        "표만": sorted(novalue - artcand),
        "조문만": sorted(artcand - novalue),
        "둘다아님": sorted(allk - novalue - artcand),
    }
    cross_counts = {k: len(v) for k, v in cross.items() if isinstance(v, list)}
    cross_counts["합"] = sum(cross_counts.values())

    cross2 = {
        "설명": "표 재변환 후보 182 × 조문 재변환 후보 80",
        "둘다": len(tabcand & artcand),
        "표만": len(tabcand - artcand),
        "조문만": len(artcand - tabcand),
        "둘다아님": len(allk - tabcand - artcand),
    }
    cross2["합"] = sum(v for k, v in cross2.items() if isinstance(v, int))

    # 층화 — 소실표건수 구간별 이득·대가
    def stratum(lo, hi=None):
        sel = [r for r in rows
               if r["소실표건수"] >= lo and (hi is None or r["소실표건수"] < hi)]
        ids = {r["dstrcAppnNo"] for r in sel}
        return {
            "문서수": len(sel),
            "소실표합계": sum(r["소실표건수"] for r in sel),
            "값미보유문서": len(ids & novalue),
            "조문후보와겹침": len(ids & artcand),
            "조문후보아님": len(ids - artcand),
            "이미회수된규범값": sum(r["확정값수"] or 0 for r in sel),
        }

    total_lost = sum(r["소실표건수"] for r in rows)
    strata = {
        "소실표>=100": stratum(100),
        "소실표 50~99": stratum(50, 100),
        "소실표 20~49": stratum(20, 50),
        "소실표 1~19": stratum(1, 20),
        "소실표 0": stratum(0, 1),
    }
    for k, v in strata.items():
        v["소실표_비중"] = (round(100 * v["소실표합계"] / total_lost, 1)
                           if total_lost else 0.0)

    # 회수 모수 — 이쪽 산출물에서 전부 재현되는 수치만 쓴다
    recover = {
        "전체문서": len(rows),
        "규범값_회수된_지구": len(norm_docs),
        "규범값_없는_지구": len(allk - norm_docs),
        "재변환시_값회수가능": len(tabcand),
        "그중_조문후보와겹침": len(tabcand & artcand),
        "그중_조문후보아님": len(tabcand - artcand),
        "재변환대상아님": sorted(allk - tabcand),
        "값_의미결손_지구": sorted(r["dstrcAppnNo"] for r in rows
                                  if r["값_의미결손"]),
        "모수설명": (
            "`재변환시_값회수가능` 은 본문 없는 표 또는 열이 깨진 값 줄이 남아 "
            "있는 문서 수다(_table_loss.reconvert_candidate). OCR 훼손 3문서는 "
            "재변환으로 회수되지 않으므로 제외돼 있다. **회수를 보장하는 수가 "
            "아니라 회수 가능성이 있는 상한이다** — 원본에 표가 실재하는지는 "
            "이 저장소에서 확인할 수 없다(원본 hwp·pdf 가 git 밖에 있다)."),
    }

    dist_int = collections.Counter(r["article_integrity_class"] for r in rows)
    dist_loss = collections.Counter(r["loss_class"] for r in rows)

    meta = {
        "생성기": "legal-table/scripts/build_retransform_estimate.py",
        "입력": [CODEX, TABLE_LOSS, NORM_VALUES],
        "성격": (
            "견적서이지 판단서가 아니다. 재변환 실행 여부는 사용자가 정한다. "
            "이 산출물은 이득과 대가를 나란히 놓는 데서 멈추며 권고하지 않는다"),
        "join": {
            "키": "codex documents[].lc5 ↔ _table_loss dstrcAppnNo",
            "매칭": f"{len(set(C) & set(T))}/189",
            "완전매칭": join_ok,
            "markdown_path_불일치": len(path_mismatch),
            "함정": (
                "codex 의 `lc5` 필드는 이름과 달리 14자리 지구번호다"
                "(41820DA2016001). 이쪽 `lc5` 는 5자리 법정동코드(41590)이며 "
                "값 도메인이 다르다. 이름이 같다고 나란히 붙이면 전건 불일치가 "
                "난다"),
        },
        "article_integrity_판정규약": (
            "건수 max 로 지배값을 뽑지 않는다. complete_count 가 거의 항상 "
            "최대라 결함이 가려진다(max 로 뽑으면 104문서가 complete). 결함 "
            "우선순위로 판정한다 — 구조누락 > 본문누락 > 순서훼손 > 완전. "
            "`조문없음` 은 원본·md 양쪽 조문 0개인 문서이고 `완전` 과 구분한다"),
        "article_integrity_class_분포": dict(sorted(dist_int.items())),
        "loss_class_분포": dict(sorted(dist_loss.items())),
        "codex_summary": cx["summary"],
        "미확정_45": UNRESOLVED_45,
        "사각지대": [
            "원본 hwp·pdf 가 이 워크트리 밖(main 디스크)에 있어 '재변환하면 "
            "표가 실제로 회수되는가' 를 직접 확인할 수 없다. 회수 가능성은 "
            "상한이지 보장이 아니다",
            "codex 의 본문누락 1,199건은 prose witness 자동 대조 결과이며 "
            "전건을 사람이 재확인한 것이 아니다(codex 견적서 §현재 결론)",
            "codex 미검증 30문서는 조문 판정 분모에서 빠져 있다. 이 문서들의 "
            "조문 무결성은 알 수 없으며 `미검증` 은 결함 없음이 아니다",
            "표 소실 수(소실표건수)는 frontmatter 선언값 기준이라 하한이다",
            "`값_의미결손` 은 규범값이 있으나 주어가 하나도 붙지 않은 지구다. "
            "codex 가 정의한 판정을 이쪽 모수로 적용한 것이며, 주어 미해소의 "
            "다수는 다중값 행이라 재변환 없이는 풀리지 않는다",
        ],
    }

    return {
        "meta": meta,
        "교차_값미보유96_조문후보80": dict(cross_counts, **{
            "설명": cross["설명"],
            "지구목록": {k: v for k, v in cross.items() if isinstance(v, list)},
        }),
        "교차_표후보182_조문후보80": cross2,
        "회수모수": recover,
        "층화": strata,
        "대가": {
            "출처": CHAIN_COST_SOURCE,
            "항목": CHAIN_COST,
            "인용_주의": (
                "3항의 '줄번호를 정본으로 쓰는' 은 10개 중 xref_index.json·"
                "_xref_report.json 2개에만 해당한다. contrast 7개는 줄번호를 싣지 "
                "않으며 md 파생이라 재구축 대상이다"),
            "층과의관계": (
                "대가 1~3항은 **재변환 문서 수와 무관하게 한 번 발생한다** — "
                "corpus 생성기(`build_guideline_article_scope.py`)는 md 를 전건 glob 해 "
                "19,357단위의 줄범위·SHA 를 다시 만들고 gz 전체를 덮어쓰며, 게이트 12 "
                "검증기(`verify_guideline_articles.py`)와 downstream 10개 파일"
                "(xref 3 · contrast 7) 생성기 어디에도 변경분만 처리하는 증분 경로가 "
                "없다. 4항도 **스캔 자체는 전건이다**(`scan_table_loss.py`·"
                "`analyze_subject_gap.py` 는 189건을 무조건 훑는다) — 재변환 문서 수에 "
                "비례하는 것은 복원 성공 여부를 문서별로 확인하는 몫뿐이다. 따라서 "
                "부분 재변환이 대가를 비례해 줄이지 않는다"),
            "배치분할_관찰": (
                "1~3항이 발생 횟수 기준 상수이므로, 부분 재변환을 N 배치로 나누면 "
                "1~3항은 N 회 발생한다. 이는 코드 구조에서 따라오는 관찰이며 벽시계 "
                "비용을 측정한 것은 아니다"),
        },
        "records": rows,
    }


def to_md(data):
    m = data["meta"]
    c1 = data["교차_값미보유96_조문후보80"]
    c2 = data["교차_표후보182_조문후보80"]
    r = data["회수모수"]
    L = []
    L.append("# 통합 재변환 견적 — 조문 무결성 × 표 손실\n")
    L.append(f"생성기 `{m['생성기']}` · join {m['join']['매칭']}\n")
    L.append("> " + m["성격"] + "\n")

    L.append("## 교차표 — 값 미보유 96 × 조문 재변환 후보 80\n")
    L.append("| 구분 | 문서 |")
    L.append("|---|---:|")
    for k in ("둘다", "표만", "조문만", "둘다아님", "합"):
        L.append(f"| {k} | {c1[k]} |")
    L.append("")
    L.append("## 교차표 — 표 재변환 후보 182 × 조문 재변환 후보 80\n")
    L.append("| 구분 | 문서 |")
    L.append("|---|---:|")
    for k in ("둘다", "표만", "조문만", "둘다아님", "합"):
        L.append(f"| {k} | {c2[k]} |")
    L.append("")

    L.append("## 층별 이득과 대가\n")
    L.append("| 층 | 문서 | 소실 표 | 비중 | 값 미보유 | 조문후보 겹침 | 이미 회수된 규범값 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for k, v in data["층화"].items():
        L.append(f"| {k} | {v['문서수']} | {v['소실표합계']:,} | "
                 f"{v['소실표_비중']}% | {v['값미보유문서']} | "
                 f"{v['조문후보와겹침']} | {v['이미회수된규범값']:,} |")
    L.append("")
    L.append("**대가는 층에 비례하지 않는다.** " + data["대가"]["층과의관계"] + "\n")
    L.append(data["대가"]["배치분할_관찰"] + "\n")
    L.append(f"대가 4항 (출처 {data['대가']['출처']}):\n")
    for i, item in enumerate(data["대가"]["항목"], 1):
        L.append(f"{i}. {item}")
    L.append("")
    L.append("> 인용 주의 — " + data["대가"]["인용_주의"] + "\n")

    L.append("## 회수 모수\n")
    L.append("| 항목 | 수 |")
    L.append("|---|---:|")
    for k in ("전체문서", "규범값_회수된_지구", "규범값_없는_지구",
              "재변환시_값회수가능", "그중_조문후보와겹침", "그중_조문후보아님"):
        L.append(f"| {k} | {r[k]} |")
    L.append("")
    L.append(r["모수설명"] + "\n")

    L.append("## 45 는 대입하지 않는다\n")
    u = m["미확정_45"]
    L.append(f"- 상태: **{u['상태']}**")
    L.append(f"- {u['왜_계산하지_않는가']}")
    L.append(f"- {u['대신_무엇을_냈는가']}")
    L.append(f"- 대입 방법: {u['대입_방법']}\n")

    L.append("## 조문 무결성 분포\n")
    L.append("| article_integrity_class | 문서 |")
    L.append("|---|---:|")
    for k, v in m["article_integrity_class_분포"].items():
        L.append(f"| {k} | {v} |")
    L.append("")
    L.append("판정 규약: " + m["article_integrity_판정규약"] + "\n")

    L.append("## 사각지대\n")
    for s in m["사각지대"]:
        L.append(f"- {s}")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--out-md", default=None)
    args = ap.parse_args()

    data = build(args.root)
    oj = args.out_json or os.path.join(args.root, OUT_JSON)
    om = args.out_md or os.path.join(args.root, OUT_MD)
    os.makedirs(os.path.dirname(oj), exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with open(oj, "w", encoding="utf-8") as fh:
        fh.write(body)
    md = to_md(data)
    with open(om, "w", encoding="utf-8") as fh:
        fh.write(md)

    c1 = data["교차_값미보유96_조문후보80"]
    print(f"{oj}  sha256 {hashlib.sha256(body.encode()).hexdigest()[:16]}…")
    print(f"{om}")
    print(f"  join {data['meta']['join']['매칭']}")
    print(f"  교차 둘다 {c1['둘다']} · 표만 {c1['표만']} · 조문만 {c1['조문만']} "
          f"· 아님 {c1['둘다아님']} · 합 {c1['합']}")
    print(f"  article_integrity: {data['meta']['article_integrity_class_분포']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
