#!/usr/bin/env python3
"""참조 레코드를 조문 역인덱스와 대상 역인덱스로 뒤집는다.

`xref_index.json` 은 참조 하나가 한 행인 평면 목록이라 "이 조문이 무엇을
참조하는가"·"이 법령을 참조하는 조문은 어디인가" 를 매번 전건 훑어야 답할 수
있다. `search_xref.py` 가 두 방향 질의를 상수 시간에 하도록 미리 뒤집는다.

대상 키는 원문 표기가 아니라 해소된 값으로 만든다. 해소되지 않은 참조는
애초에 `xref_index.json` 에 없으므로 역인덱스에도 들어가지 않는다.

입력  output/legal/xref/xref_index.json
출력  output/legal/xref/xref_by_article.json
"""

import argparse
import collections
import json
import os
import sys


def target_key(r):
    """참조가 가리키는 대상의 키와 표시명. 해소된 값으로만 만든다."""
    t = r["target"]
    kind = r["kind"]
    if kind in ("법령", "법령조문", "범위") and t["statute_key"]:
        return f"statute:{t['statute_key']}", (t["statute_official"]
                                               or r["name_surface"] or "")
    if kind == "별표":
        if t["statute_key"]:
            return (f"annex:{t['statute_key']}/{t['annex']}",
                    f"{t['statute_official'] or r['name_surface'] or ''} {t['annex']}")
        return (f"annex:내부/{r['dstrcAppnNo']}/{t['annex']}",
                f"{r['지구명']} {t['annex']}")
    if kind == "별지·서식":
        base = t["statute_key"] or f"내부/{r['dstrcAppnNo']}"
        return f"form:{base}/{t['form']}", f"{t['form']}"
    if kind == "부칙":
        base = t["statute_key"] or f"내부/{r['dstrcAppnNo']}"
        return f"addenda:{base}", "부칙"
    if kind == "외부문서" and t["heading"]:
        return f"doc:{t['heading']}", t["heading"]
    if kind == "내부표목" and t["heading"]:
        return f"heading:{r['dstrcAppnNo']}/{t['heading']}", t["heading"]
    if kind == "내부조문":
        if t["article_iri"]:
            return f"article:{t['article_iri']}", t["article_iri"].rsplit("/", 1)[-1]
        # 후보가 여럿이면 노드가 아니라 조문번호로만 잇는다 — 추측하지 않는다
        no = (t["articles"] or [""])[0]
        return f"articleNo:{r['dstrcAppnNo']}/{no}", no
    return None, None


def article_key(r):
    if r["article_iri"]:
        return r["article_iri"]
    return f"문서레벨/{r['dstrcAppnNo']}"


def require_index(data, index_path):
    if not isinstance(data, dict):
        raise ValueError(f"{index_path}: JSON 루트가 객체가 아니다")
    if not isinstance(data.get("xrefs"), list):
        raise ValueError(f"{index_path}: xrefs 배열이 없다")
    ids = [r.get("xref_id") for r in data["xrefs"] if isinstance(r, dict)]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{index_path}: xref_id 중복이 있다")
    return data["xrefs"]


def build(index_path, out_path):
    with open(index_path, encoding="utf-8") as f:
        src = json.load(f)
    xrefs = require_index(src, index_path)

    arts = collections.OrderedDict()
    for r in sorted(xrefs, key=lambda x: (article_key(x), x["xref_id"])):
        k = article_key(r)
        a = arts.setdefault(k, {
            "article_key": k,
            "article_iri": r["article_iri"],
            "dstrcAppnNo": r["dstrcAppnNo"],
            "지구명": r["지구명"],
            "지역": r["지역"],
            "source_file": r["source_file"],
            "article_no": r["article_no"],
            "article_label": r["article_label"],
            "article_origin": r["article_origin"],
            "xref_ids": [],
            "kind별": collections.Counter(),
            "scope별": collections.Counter(),
        })
        a["xref_ids"].append(r["xref_id"])
        a["kind별"][r["kind"]] += 1
        a["scope별"][r["scope"]] += 1

    tgts = collections.OrderedDict()
    unkeyed = 0
    for r in sorted(xrefs, key=lambda x: x["xref_id"]):
        k, label = target_key(r)
        if not k:
            unkeyed += 1
            continue
        t = tgts.setdefault(k, {
            "target_key": k,
            "target_label": label,
            "target_kind": r["kind"],
            "scope": r["scope"],
            "master_status": r["target"]["master_status"],
            "refs": [],
            "지구": set(),
        })
        t["refs"].append({"xref_id": r["xref_id"],
                          "dstrcAppnNo": r["dstrcAppnNo"],
                          "article_key": article_key(r),
                          "article_no": r["article_no"],
                          "articles": r["target"]["articles"]})
        t["지구"].add(r["dstrcAppnNo"])

    by_article = []
    for a in arts.values():
        a["kind별"] = dict(sorted(a["kind별"].items()))
        a["scope별"] = dict(sorted(a["scope별"].items()))
        a["참조수"] = len(a["xref_ids"])
        by_article.append(a)
    by_article.sort(key=lambda a: a["article_key"])

    by_target = []
    for t in tgts.values():
        t["지구수"] = len(t["지구"])
        t["참조수"] = len(t["refs"])
        t["지구"] = sorted(t["지구"])
        t["refs"].sort(key=lambda x: x["xref_id"])
        by_target.append(t)
    by_target.sort(key=lambda t: (-t["참조수"], t["target_key"]))

    out = {
        "meta": {
            "생성근거": "xref_index.json 을 조문·대상 두 방향으로 뒤집은 역인덱스",
            "스크립트": ".claude/skills/legal/legal-xref/scripts/build_article_index.py",
            "원본_참조수": len(xrefs),
            "조문키수": len(by_article),
            "문서레벨_조문키수": sum(1 for a in by_article
                                     if a["article_origin"] == "문서레벨"),
            "대상키수": len(by_target),
            "대상키_미발급": unkeyed,
            "대상키_규약": ("statute: 법령키 · annex: 별표 · form: 별지서식 · "
                            "addenda: 부칙 · doc: 외부문서 · heading: 내부표목 · "
                            "article: 내부조문 노드 · articleNo: 후보 다중이라 "
                            "노드로 좁히지 못한 내부조문"),
            "재계산_주의": ("참조수는 xref_index 를 다시 센 값이다. "
                            "xref_index.meta 의 수치와 나란히 비교하지 않는다"),
        },
        "by_article": by_article,
        "by_target": by_target,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="output/legal/xref/xref_index.json")
    ap.add_argument("--out", default="output/legal/xref/xref_by_article.json")
    a = ap.parse_args()
    if not os.path.exists(a.index):
        print(f"입력 없음: {a.index}", file=sys.stderr)
        return 1
    try:
        out = build(a.index, a.out)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"입력 계약 위반: {exc}", file=sys.stderr)
        return 1
    m = out["meta"]
    print(f"조문키 {m['조문키수']:,} (문서레벨 {m['문서레벨_조문키수']}) · "
          f"대상키 {m['대상키수']:,} → {a.out}")
    print("참조 많은 대상:", ", ".join(
        f"{t['target_label'] or t['target_key']}({t['참조수']})"
        for t in out["by_target"][:8]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
