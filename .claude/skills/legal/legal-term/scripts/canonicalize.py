#!/usr/bin/env python3
"""용어 하나에 정의 하나. definiation.json 을 정본 구조로 재구성한다.

같은 용어가 지구마다 정의되어 정의문이 6,318개였다. 문언만 다른 것은 하나로
합치되, 수치·요건이 실제로 다른 것은 갈래로 남긴다. 예를 들어 `보조색` 은
지구에 따라 벽면의 10~30% 와 20~30% 로 규제가 다르므로, 하나만 남기면
복원할 수 없는 정보가 사라진다.

  definition        대표 정의문 하나 — 항상 존재
  variants_by_rule  수치·요건이 다른 갈래. 실질충돌 용어에만 존재

입력  output/legal/word/definiation.json
      output/legal/word/terms.json      (numeric_signature 를 여기서 가져온다)
출력  output/legal/word/definiation.json (제자리 갱신)
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

# ── 대표 정의문 선정 ─────────────────────────────────────────────────────────
# 기본은 최빈이지만, 최빈이 품질 결함을 안고 있으면 다음 후보로 넘긴다.
# 실측 결함: 한컴 사설영역 잔재 3건, `말함` 비표준 종결 33건, 절단 251건.

PUA = re.compile(r"[-\U000F0000-\U000FFFFD]")
NONSTD_END = re.compile(r"말함\s*\.?\s*$")


def quality_penalty(v):
    """작을수록 좋은 후보. 같은 빈도면 이 값으로 가른다."""
    s = v.get("text_core") or v.get("text") or ""
    p = 0
    if v.get("truncated"):
        p += 8           # 문장이 잘린 것은 정의로 쓸 수 없다
    if PUA.search(s):
        p += 4           # 한컴 사설영역이 깨져 남은 글자
    if NONSTD_END.search(s):
        p += 2           # `말함` — 지배 종결형(`말한다`)이 아니다
    if v.get("source_quality") == "OCR":
        p += 1
    return p


def pick_representative(variants):
    """빈도 우선, 동률이면 품질, 그래도 동률이면 긴 쪽."""
    return min(
        variants,
        key=lambda v: (
            quality_penalty(v),
            -v.get("count", 0),
            -len(v.get("text_core") or v.get("text") or ""),
        ),
    )


# ── 수치·요건 갈래 ───────────────────────────────────────────────────────────

# 기준 명사가 없는 맨숫자는 규제 수치가 아니다. `2이상×1` 은 정의문의
# `대지에 2이상의 건축물이 있는 경우` 를 잡은 것으로, 같은 뜻의 `둘 이상` 과
# 갈려 허위 갈래를 만든다. 서명 형태는 `기준:값부등호×횟수`.
_BARE_NUM = re.compile(r"^[0-9.]+(?:이상|이하|초과|미만)?×\d+$")


def signature_key(sig):
    """numeric_signature 를 비교 가능한 키로. 순서 차이와 맨숫자는 무시한다."""
    return tuple(sorted(s for s in (sig or []) if not _BARE_NUM.match(s)))


def build_rule_variants(variants, sigmap):
    """수치·요건 서명이 다른 갈래만 남긴다. 서명이 1종이면 갈래가 없다."""
    groups = collections.defaultdict(
        lambda: {"variants": [], "districts": set(), "count": 0}
    )
    for v in variants:
        k = signature_key(sigmap.get(v.get("variant_index")))
        g = groups[k]
        g["variants"].append(v)
        g["districts"] |= set(v.get("districts", []))
        g["count"] += v.get("count", 0)

    if len(groups) <= 1:
        return []

    out = []
    for k, g in sorted(groups.items(), key=lambda x: -x[1]["count"]):
        rep = pick_representative(g["variants"])
        out.append({
            "numeric_signature": list(k),
            "definition": rep.get("text_core") or rep.get("text", ""),
            "definition_full": rep.get("text", ""),
            "districts": sorted(g["districts"]),
            "doc_frequency": len(g["districts"]),
            "occurrence_count": g["count"],
            "surface_variant_count": len(g["variants"]),
        })
    return out


def canonicalize(defs_path, terms_path, out_path, keep_surface):
    data = json.loads(Path(defs_path).read_text(encoding="utf-8"))
    terms = json.loads(Path(terms_path).read_text(encoding="utf-8"))

    # numeric_signature 는 terms.json 에만 있다
    sig = {
        t["id"]: {v["variant_index"]: v.get("numeric_signature") for v in t["variants"]}
        for t in terms["terms"]
    }

    n_rule = n_surface = 0
    for e in data["definitions"]:
        variants = e.pop("variants")
        e.pop("variant_count", None)

        rep = pick_representative(variants)
        e["definition"] = rep.get("text_core") or rep.get("text", "")
        e["definition_full"] = rep.get("text", "")
        e["definition_rule_part"] = rep.get("text_rule", "")
        e["definition_district"] = (rep.get("districts") or [None])[0]
        e["surface_variant_count"] = len(variants)
        n_surface += len(variants)

        rules = build_rule_variants(variants, sig.get(e["id"], {}))
        if rules:
            e["variants_by_rule"] = rules
            n_rule += len(rules)
            # 대표는 최다 지구 갈래의 것으로 맞춘다. 갈래를 남긴 이상
            # 대표와 최대 갈래가 어긋나 있으면 읽는 쪽이 혼란스럽다.
            top = max(rules, key=lambda r: (r["doc_frequency"], r["occurrence_count"]))
            e["definition"] = top["definition"]
            e["definition_full"] = top["definition_full"]

        # 대안이 없어 결함을 안은 채 채택한 경우를 표시한다. variant 가 하나뿐인
        # 용어에서 나오며, 고치면 원문 훼손이라 그대로 둔다.
        flags = []
        if PUA.search(e["definition"]):
            flags.append("한컴 사설영역 문자")
        if NONSTD_END.search(e["definition"]):
            flags.append("`말함` 종결")
        if rep.get("truncated") and e["definition"] == (
            rep.get("text_core") or rep.get("text", "")
        ):
            flags.append("절단")
        if flags:
            e["definition_quality_flags"] = flags

        # 등급을 갈래 기준으로 다시 매긴다. 맨숫자를 뺀 뒤에도 서명이 갈리면
        # 실질충돌, 문언만 다르면 표현차이, 변이가 하나면 일치다.
        if rules:
            e["definition_variance"] = "실질충돌"
            e["variance_evidence"] = (
                f"수치·요건 서명 {len(rules)}종 — 지구별로 규제가 다르다"
            )
        elif len(variants) > 1:
            e["definition_variance"] = "표현차이"
            e["variance_evidence"] = (
                f"표면 변이 {len(variants)}종이나 수치·요건 서명은 1종 — 문언·표기 차이"
            )
        else:
            e["definition_variance"] = "일치"
            e["variance_evidence"] = "변이 없음"

        # 통합 전 대표를 남겨 둔다 — 재구성 이력을 추적할 수 있게
        e.pop("definition_representative", None)

        if keep_surface:
            e["surface_variants"] = [
                {
                    "definition": v.get("text_core") or v.get("text", ""),
                    "districts": v.get("districts", []),
                    "count": v.get("count", 0),
                }
                for v in sorted(variants, key=lambda x: -x.get("count", 0))
            ]

    E = data["definitions"]
    multi = [e for e in E if e.get("variants_by_rule")]
    m = data["meta"]
    m["구조"] = (
        "용어 하나에 정의 하나. definition 이 정본이다. "
        "수치·요건이 지구마다 실제로 다른 용어에만 variants_by_rule 로 갈래를 남긴다."
    )
    m["대표정의_선정"] = (
        "최빈 정의문. 단 절단·한컴 사설영역 잔재·`말함` 비표준 종결·OCR 은 감점해 "
        "다음 후보로 넘긴다. 동률이면 긴 쪽."
    )
    m["갈래_판정"] = (
        "terms.json 의 numeric_signature 가 다를 때만 갈래로 남긴다. "
        "`1/10` 과 `10%` 는 서명이 같아 자동으로 합쳐진다. "
        "분모(`벽면적의 10%` vs `건축물의 10%`)와 부등호(`이상` vs `초과`)는 보존된다."
    )
    m["정의문수"] = len(E) + sum(len(e["variants_by_rule"]) - 1 for e in multi)
    m["통합전_정의문수"] = 6318
    m["표면변이수"] = n_surface
    m["갈래보유_용어수"] = len(multi)
    m["갈래수"] = n_rule
    m["변이등급분포"] = dict(
        collections.Counter(e["definition_variance"] for e in E).most_common()
    )
    m["맨숫자_제외"] = (
        "기준 명사가 없는 맨숫자(`2이상×1`)는 서명에서 뺀다. 정의문의 "
        "`대지에 2이상의 건축물이 있는 경우` 를 잡은 것이라 같은 뜻의 `둘 이상` 과 "
        "갈려 허위 갈래를 만든다."
    )

    Path(out_path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="output/legal/word/definiation.json")
    ap.add_argument("--terms", default="output/legal/word/terms.json")
    ap.add_argument("--out", default="")
    ap.add_argument(
        "--keep-surface",
        action="store_true",
        help="문언만 다른 표면 변이도 surface_variants 로 남긴다",
    )
    a = ap.parse_args()

    out = a.out or a.file
    if not Path(a.file).exists():
        print(f"입력 없음: {a.file}", file=sys.stderr)
        return 1

    data = canonicalize(a.file, a.terms, out, a.keep_surface)
    m = data["meta"]
    print(f"용어 {m['용어수']}건 → 정의 {m['용어수']}개 (1용어 1정의)")
    print(f"  통합 전 정의문 {m['통합전_정의문수']} / 표면 변이 {m['표면변이수']}")
    print(f"  수치·요건 갈래를 남긴 용어 {m['갈래보유_용어수']}건 → 갈래 {m['갈래수']}개")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
