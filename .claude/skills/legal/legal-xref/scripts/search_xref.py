#!/usr/bin/env python3
"""조문의 외부 참조를 검색한다.

두 방향을 모두 받는다. `--district`·`--article` 은 그 조문이 무엇을 참조하는가,
`--target` 은 그 대상을 참조하는 조문이 어디인가를 답한다. `--unresolved` 는
`xref_index.json` 이 아니라 `_xref_report.json` 을 본다 — 격리된 것은 산출물에
없으므로 따로 물어야 한다.

결과 건수와 함께 **사각지대**(이 검출기가 못 보는 범위)를 항상 출력한다.
건수만 보면 없는 것이 0건인지 못 본 것인지 갈리지 않는다.

입력  output/legal/xref/xref_index.json
      output/legal/xref/xref_by_article.json
      output/legal/xref/_xref_report.json
출력  표준출력 (text 또는 json)
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import xref_common as X                                        # noqa: E402

BLIND_SPOTS = [
    "인용부호 없이 처음 나오는 법령명은 못 본다. 어휘는 인용부호로 묶인 표기에서만 "
    "학습하므로, 코퍼스 어디에서도 묶인 적 없는 법령은 조문 참조만 남고 미판정이 된다",
    "같은 조번호가 한 문서에 여럿이면 조문 노드로 좁히지 못한다. 편·장 한정어나 "
    "표제가 함께 적혔을 때만 좁히고, 아니면 후보수만 남긴다",
    "외부 법령의 범위 참조는 전개하지 않는다. 그 법령의 조문 목록이 없어 양 끝 "
    "실재를 확인할 수 없기 때문이다 (article_master 는 규범값 계통 8개 조문뿐)",
    "표·도면·별표 본문 안의 참조는 조문 귀속이 약하다. 조문 헤딩이 없는 문서 56건은 "
    "전부 문서레벨로 잡히고, 결정조서·표만 남은 문서 5건은 참조가 0건이다",
    "법령의 시점을 보지 않는다. 지침은 2002~2024년에 걸쳐 있어 같은 조문 번호가 "
    "인용 시점과 현행에서 다를 수 있다",
    "인용부호로 묶였으나 발행 주체·연도가 없는 `…계획` 표기는 미판정이다. 같은 "
    "지구단위계획의 구성 항목인지 별개 문서인지 표기만으로 갈리지 않는다",
    "맨몸 편·장·절 참조는 문장이 종결형으로 끝날 때만 담는다. 줄바꿈으로 서술이 다음 "
    "줄로 넘어간 참조와 글머리표 바로 뒤에 오는 참조는 놓친다(제외 661건 중 일부)",
]


def load(d):
    out = {}
    for name in ("xref_index.json", "xref_by_article.json", "_xref_report.json"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                out[name] = json.load(f)
    return out


def require_records(data, name, key):
    if name not in data:
        raise ValueError(f"입력 없음: {name}")
    records = data[name].get(key)
    if not isinstance(records, list):
        raise ValueError(f"{name}: {key} 배열이 없다")
    return records


def match_district(r, q):
    if not q:
        return True
    return (r["dstrcAppnNo"] == q
            or q in (r["지구명"] or "")
            or q in (r["source_file"] or ""))


def match_article(r, q):
    if not q:
        return True
    qn = X.norm_art(q)
    return (X.norm_art(r["article_no"] or "") == qn
            or qn in X.canon_key(r["article_label"] or "")
            or qn in X.canon_key(r["article_iri"] or ""))


def match_target(r, q):
    if not q:
        return True
    k = X.canon_key(q)
    t = r["target"]
    hay = [t["statute_key"], t["statute_official"], t["heading"],
           r["name_surface"], t["annex"], t["form"]]
    return any(k in X.canon_key(h) for h in hay if h)


def filter_records(recs, a):
    out = []
    for r in recs:
        if not match_district(r, a.district):
            continue
        if not match_article(r, a.article):
            continue
        if not match_target(r, a.target):
            continue
        if a.kind and r["kind"] not in a.kind:
            continue
        if a.scope and r["scope"] not in a.scope:
            continue
        if a.relation and r["relation"] != a.relation:
            continue
        out.append(r)
    return out


def fmt_target(r):
    t = r["target"]
    bits = []
    if t["statute_official"] or t["statute_key"]:
        bits.append(t["statute_official"] or t["statute_key"])
        if t["master_status"] in ("미수록", "미대조"):
            bits[-1] += f"(법령정본 {t['master_status']})"
    if t["annex"]:
        bits.append(t["annex"])
    if t["form"]:
        bits.append(t["form"])
    if t["articles"]:
        bits.append(" ".join(t["articles"]))
    if t["heading"]:
        bits.append(f"표목 {t['heading']}")
    if t["article_iri"]:
        bits.append(f"→ {t['article_iri']}")
    elif t["후보수"] and t["후보수"] > 1:
        bits.append(f"(문서 내 동일 조번호 후보 {t['후보수']}개 — 노드 미확정)")
    if r["range"]:
        rg = r["range"]
        bits.append(f"{rg['from']}~{rg['to']}"
                    + (f" 전개 {len(rg['expanded'])}개" if rg["expanded"]
                       else " 전개 안 함"))
    return " · ".join(b for b in bits if b) or "(대상 비어 있음)"


def print_text(recs, a, data, total):
    src = "_xref_report.json(격리)" if a.unresolved else "xref_index.json"
    print(f"질의 대상 {src} — 전체 {total:,}건 중 {len(recs):,}건")
    for r in recs[:a.limit]:
        where = r["article_iri"] or f"문서레벨/{r['dstrcAppnNo']}"
        print(f"\n[{r['xref_id']}] {r['지구명']}({r['dstrcAppnNo']}) "
              f"{r['article_no'] or '조문없음'} {r['article_label'] or ''}")
        print(f"  출처   {where} · {r['source_file']}:{r['line']}")
        print(f"  표기   {r['surface'].strip()}")
        print(f"  판정   scope={r['scope']}({r['scope_basis']}) kind={r['kind']} "
              f"relation={r['relation']} resolution={r['resolution']}")
        print(f"  대상   {fmt_target(r)}")
        if r.get("resolution_note"):
            print(f"  비고   {r['resolution_note']}")
        print(f"  근거   {r['quote'].strip()[:200]}")
    if len(recs) > a.limit:
        print(f"\n… {len(recs) - a.limit:,}건 더 있음 (--limit 로 늘린다)")

    rep = data.get("_xref_report.json", {}).get("meta", {})
    print(f"\n결과 {len(recs):,}건")
    print(f"격리(질의 밖) {rep.get('격리수', '?'):,}건 — "
          f"미해소·미판정은 xref_index 에 없다. --unresolved 로 본다")
    print("사각지대 — 이 검출기가 못 보는 범위")
    for s in BLIND_SPOTS:
        print(f"  · {s}")


def main():
    ap = argparse.ArgumentParser(
        description="조문의 외부 참조를 검색한다")
    ap.add_argument("--dir", default="output/legal/xref")
    ap.add_argument("--district", help="지구번호 또는 지구명(부분일치)")
    ap.add_argument("--article", help="조문번호(제N조) 또는 표제(부분일치)")
    ap.add_argument("--target", help="법령명·법령키·별표·표목(부분일치)")
    ap.add_argument("--kind", action="append",
                    help="참조유형. 여러 번 줄 수 있다")
    ap.add_argument("--scope", action="append", choices=["내부", "외부", "미판정"])
    ap.add_argument("--relation", choices=["참조", "준용"])
    ap.add_argument("--unresolved", action="store_true",
                    help="격리된 것(_xref_report.json)에서 찾는다")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()
    if a.limit < 0:
        print("--limit 은 0 이상이어야 한다", file=sys.stderr)
        return 1

    try:
        data = load(a.dir)
        if a.unresolved:
            recs = require_records(data, "_xref_report.json", "isolated")
        else:
            recs = require_records(data, "xref_index.json", "xrefs")
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"입력 계약 위반: {exc}", file=sys.stderr)
        return 1
    total = len(recs)
    hits = filter_records(recs, a)

    if a.format == "json":
        json.dump({
            "질의": {k: v for k, v in vars(a).items() if v not in (None, False)},
            "모수": total,
            "결과수": len(hits),
            "사각지대": BLIND_SPOTS,
            "results": hits[:a.limit],
        }, sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
    else:
        print_text(hits, a, data, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
