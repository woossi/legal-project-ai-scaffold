#!/usr/bin/env python3
"""값 역인덱스 — `value_index.json` 을 만든다.

**전부 재계산 필드다.** `norm_values.json` 을 다시 센 값이므로 다른 파일의 동명
필드와 나란히 비교하지 않는다(`프로젝트-설계구조.md` §3). 질의 편의를 위한
파생물이고 정본이 아니다.

입력  output/legal/table/norm_values.json
출력  output/legal/table/value_index.json
"""

import argparse
import collections
import hashlib
import json
import os

SRC = "output/legal/table/norm_values.json"
OUT_PATH = "output/legal/table/value_index.json"


def _stats(vals):
    vs = sorted(vals)
    return {"건수": len(vs), "최소": vs[0], "최대": vs[-1],
            "중앙값": vs[len(vs) // 2]}


def build(root="."):
    with open(os.path.join(root, SRC), encoding="utf-8") as fh:
        src = json.load(fh)
    # 전체 코퍼스는 이 파일의 입력에서 복원할 수 없다 — 레코드에는 값이 관측된
    # 문서만 있어서 값 없는 문서가 통째로 빠진다. upstream 관측을 계승한다.
    # 없으면 멈춘다. 기본값을 쓰면 출처 없는 수치가 산출물에 남는다.
    if "전체문서수" not in src.get("meta", {}):
        raise ValueError(
            f"{SRC} 의 meta.전체문서수 가 없다 — 값 역인덱스는 전체 코퍼스를"
            " 스스로 셀 수 없으므로 upstream 관측을 계승해야 한다")
    전체문서수 = src["meta"]["전체문서수"]
    # 확정 집계는 규범만 쓴다. context_class != 규범 은 제외한다.
    norm = [r for r in src["records"] if r["context_class"] == "규범"]

    by_district = collections.defaultdict(list)
    by_metric = collections.defaultdict(list)
    by_subject = collections.defaultdict(list)
    for r in norm:
        by_district[r["dstrcAppnNo"]].append(r)
        if r["metric"]:
            by_metric[r["metric"]].append(r)
        if r["subject"]:
            by_subject[f"{r['metric']}|{r['subject']}"].append(r)

    districts = {}
    for k in sorted(by_district):
        rs = by_district[k]
        districts[k] = {
            "지역": rs[0]["지역"],
            "source_file": rs[0]["source_file"],
            "규범값수": len(rs),
            "value_ids": sorted(r["value_id"] for r in rs),
            "metric별": {m: len([r for r in rs if r["metric"] == m])
                         for m in sorted({r["metric"] for r in rs if r["metric"]})},
            "주어해소수": sum(1 for r in rs if r["subject"]),
        }

    metrics = {m: dict(_stats([r["value"] for r in by_metric[m]]),
                       **{"주어해소수": sum(1 for r in by_metric[m] if r["subject"]),
                          "문서수": len({r["dstrcAppnNo"] for r in by_metric[m]})})
               for m in sorted(by_metric)}

    subjects = {k: {"건수": len(v),
                    "문서수": len({r["dstrcAppnNo"] for r in v}),
                    "값": sorted({r["value"] for r in v})}
                for k, v in sorted(by_subject.items()) if len(v) >= 2}

    return {
        "meta": {
            "생성기": "legal-table/scripts/build_value_index.py",
            "입력": SRC,
            "재계산필드": (
                "이 파일의 모든 수치는 norm_values.json 을 다시 센 재계산 "
                "필드다. 이름이 같아도 다른 파일의 동명 필드와 나란히 비교하지 "
                "않는다. 정본은 norm_values.json 이다"),
            "집계모수": (
                "context_class == 규범 인 레코드만 센다. 규범이 아닌 것은 "
                "norm_values.json 에 남아 있으나 이 인덱스에는 들어오지 않는다"),
            "규범값수": len(norm),
            "문서수": len(districts),
            "전체문서수": 전체문서수,
            "전체문서수_출처": f"{SRC} 의 meta.전체문서수 계승",
        },
        "by_district": districts,
        "by_metric": metrics,
        "by_metric_subject": subjects,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    data = build(args.root)
    out = args.out or os.path.join(args.root, OUT_PATH)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(body)
    print(f"{out}  문서 {data['meta']['문서수']}  규범값 {data['meta']['규범값수']}  "
          f"sha256 {hashlib.sha256(body.encode()).hexdigest()[:16]}…")


if __name__ == "__main__":
    main()
