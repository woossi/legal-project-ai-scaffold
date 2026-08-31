#!/usr/bin/env python3
"""terms.json 의 정의 조항 소속 정의문을 문서 단위 정의 레코드로 재구성한다.

definiation.json 이 용어 축(1용어 1정의)이라면 이 산출물은 문서 축이다 —
"한 문서에 적용되는 용어의 정의" 를 문서별로 조회할 수 있게 레코드마다
원본 문서를 필드로 담는다. 정의는 요약해 서술한다: 표제어 도입부
(`"X"라 함은` 등)와 정의 술어(`…를 말한다` 계열)를 절단한 핵심 서술부다.
요약은 정의부 원문의 연속 부분문자열이며 재작성하지 않는다.

용어의 class 는 '법률 용어' 로 고정한다 — 정의 조항에서 선언된 용어를
법률 용어로 분류한다는 사용자 규약(2026-08-11)이다.

입력  output/legal/word/terms.json
출력  output/legal/word/doc_definitions.json
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_definitions import normalize_article  # noqa: E402

TERM_CLASS = "법률 용어"

# 닫는 인용부호. U+F0853 은 한컴 사설영역 닫는 인용부호(󰡓) — hwp 변환 md 에 실존한다.
QUOTE_CLOSE = "”\"'」』｣’\U000f0853"

# 정의 술어 — 최초 출현 위치에서 자른다. 정의부(첫 술어까지) 철학과 일치하며,
# `…를 말하며 <규정부>` 처럼 술어 뒤에 규정이 이어지는 정의문에서 규정부 혼입을 막는다.
PRED = re.compile(
    r"[\s,]*(?:을|를)?\s*(?:말\s*한\s*다|말\s*하\s*며|말\s*함|칭\s*한\s*다|칭\s*하\s*며|"
    r"의\s*미\s*한\s*다|의\s*미\s*하\s*며)"
)
# 절단 변이에서 술어가 `말` 까지만 남은 꼬리 (`…공간을 말`)
PRED_CUT = re.compile(r"[\s,]*(?:을|를)?\s*말\s*$")


def strip_head(s, mode, term_surface):
    """match_mode 별 표제어 도입부 절단. 실패 시 None."""
    window = len(term_surface) + 25
    if "함은" in mode or mode == "무인용부호":
        i = s.find("함은", 0, window + 4)
        if i >= 0:
            return s[i + 2:]
        m = re.search(r"함\s*은", s[: window + 5])  # OCR 공백 변형 `함 은`
        return s[m.end():] if m else None
    if mode in ("인용부호+이란", "인용부호+란"):
        m = re.search(rf"[{QUOTE_CLOSE}]\s*(?:이란|란)", s[: window + 4])
        return s[m.end():] if m else None
    if mode in ("인용부호+는", "인용부호+은"):
        m = re.search(rf"[{QUOTE_CLOSE}]\s*(?:이라는 것)?[은는]", s[: window + 8])
        return s[m.end():] if m else None
    if mode == "무인용부호+콜론":
        hits = [i for i in (s.find(":", 0, window), s.find("：", 0, window)) if i >= 0]
        return s[min(hits) + 1:] if hits else None
    return None


def summarize(core, mode, term_surface):
    """정의부 → (요약, 상태). 요약은 항상 core 의 연속 부분문자열이다."""
    body = strip_head(core, mode, term_surface)
    if body is None:
        return core.strip(), "요약실패"
    m = PRED.search(body)
    if m:
        stripped = body[: m.start()].strip()
        # 술어가 정의부 끝이면 통상형, 중간이면 뒤에 규정이 이어진 형태
        status = "술어일치" if m.end() >= len(body.rstrip(" .,")) - 1 else "중간술어"
    else:
        mcut = PRED_CUT.search(body)
        if mcut:
            stripped = body[: mcut.start()].strip()
            status = "술어절단복원"
        else:
            stripped = body.strip().rstrip(" .,")
            status = "술어없음"
    if not stripped or stripped not in core:
        return core.strip(), "요약실패"
    return stripped, status


def resolve_file(o):
    """occurrence 의 md 경로를 현재 파일명 규약으로 재해석한다.

    terms.json 의 file 은 생성 시점의 `지구명_지구번호.md` 규약이라 파일명
    개편 뒤에는 실존하지 않는다. (region, dstrcNm) 이 전 지구에서 고유함을
    실측 확인했으므로 현재 규약 `지구명.md` 로 바꿔 잡는다.
    """
    f = o["file"]
    if Path(f).exists():
        return f
    alt = f"output/legal/markdown/{o['region']}/{o['dstrcNm']}.md"
    return alt if Path(alt).exists() else f


def build(terms_path, out_path):
    data = json.loads(Path(terms_path).read_text(encoding="utf-8"))
    terms = data["terms"]

    all_docs = set()
    merged = {}          # (source_file, term_id, variant_index) -> record
    status_counter = collections.Counter()
    occurrence_total = 0
    variant_missing = 0

    for t in terms:
        vmap = {v["variant_index"]: v for v in t["variants"]}
        for o in t["occurrences"]:
            all_docs.add(o["dstrcAppnNo"])
            *_, isdef, _kind = normalize_article(o.get("article"))
            if not isdef:
                continue
            occurrence_total += 1
            v = vmap.get(o.get("variant_index"))
            if v is None:
                variant_missing += 1
                continue
            core = v.get("text_core") or v.get("text") or ""
            surface = o.get("term_surface") or t["term"]
            src = resolve_file(o)
            key = (src, t["id"], o.get("variant_index"))
            rec = merged.get(key)
            if rec is not None:
                rec["occurrence_count"] += 1
                if (o.get("article") or "").strip() not in rec["articles"]:
                    rec["articles"].append((o.get("article") or "").strip())
                continue
            definition, status = summarize(core, o.get("match_mode", ""), surface)
            status_counter[status] += 1
            merged[key] = {
                "term": t["term"],
                "term_id": t["id"],
                "term_surface": surface,
                "class": TERM_CLASS,
                "definition": definition,
                "definition_status": status,
                "definition_source_text": core,
                "source_file": src,
                "dstrcNm": o["dstrcNm"],
                "dstrcAppnNo": o["dstrcAppnNo"],
                "region": o["region"],
                "articles": [(o.get("article") or "").strip()],
                "match_mode": o.get("match_mode", ""),
                "variant_index": o.get("variant_index"),
                "truncated": bool(v.get("truncated", False)),
                "source_quality": v.get("source_quality", ""),
                "occurrence_count": 1,
            }

    records = sorted(
        merged.values(), key=lambda r: (r["source_file"], r["term"], r["variant_index"])
    )
    def_docs = {r["dstrcAppnNo"] for r in records}
    doc_counts = collections.Counter(r["source_file"] for r in records)

    out = {
        "meta": {
            "생성근거": (
                "output/legal/word/terms.json 의 occurrence 를 조 표제로 재판정해 "
                "정의 조항 소속 건만 문서 단위 레코드로 재구성"
            ),
            "원본_생성일시": data.get("meta", {}).get("생성일시"),
            "대상문서수": len(all_docs),
            "정의조항_보유문서수": len(def_docs),
            "레코드수": len(records),
            "용어수": len({r["term_id"] for r in records}),
            "정의문수": occurrence_total,
            "중복병합": {
                "설명": "(source_file, term_id, variant_index) 가 같은 occurrence 를 "
                        "한 레코드로 합치고 occurrence_count 로 남긴다",
                "병합전_occurrence수": occurrence_total - variant_missing,
                "병합후_레코드수": len(records),
            },
            "class규칙": (
                "정의 조항에서 선언된 용어는 모두 class '법률 용어' 로 분류한다 — "
                "사용자 규약 (2026-08-11)"
            ),
            "요약규칙": {
                "대상": "variant.text_core (정의부 — 문장 첫머리부터 정의 술어까지)",
                "head절단": "match_mode 별 표제어 도입부 제거 — `\"X\"라 함은`·"
                            "`\"X\"(이)란`·`\"X\"은/는`·`X :`·무인용부호 `X라 함은`",
                "tail절단": "최초 출현 정의 술어(말한다·말하며·말함·칭한다·의미한다 "
                            "계열)와 직전 조사(을/를)를 제거. 절단 변이의 `…을 말` "
                            "꼬리도 같은 규칙으로 제거",
                "보존": "요약은 정의부 원문의 연속 부분문자열이다. 어휘 재작성·"
                        "맞춤법 교정·윤문을 하지 않는다",
                "실패시": "요약실패 상태로 표시하고 definition 에 정의부 원문을 그대로 둔다",
            },
            "요약상태분포": dict(status_counter.most_common()),
            "요약상태_의미": {
                "술어일치": "정의부 끝의 술어를 절단한 통상형",
                "중간술어": "술어 뒤에 규정이 이어져 최초 술어에서 절단",
                "술어절단복원": "절단 변이의 `…을 말` 꼬리를 술어로 보고 절단",
                "술어없음": "술어가 없어 도입부 제거 후 본문을 그대로 요약으로 채택 "
                            "(명사형 종결·절단 변이·콜론형 유개념-종차)",
                "요약실패": "도입부를 찾지 못해 정의부 원문을 그대로 둔 건",
            },
            "원본문서_주의": (
                "source_file 은 저장소 안의 변환 md 경로다. hwp·pdf 시행지침 원본은 "
                "저장소 밖에 있어 여기서 참조하지 않는다. terms.json 의 file 은 "
                "생성 시점 규약(지구명_지구번호.md)이라 현재 규약(지구명.md)으로 "
                "재해석해 담았다"
            ),
            "정렬": "source_file, term, variant_index 오름차순",
            "스크립트": ".claude/skills/legal/legal-term/scripts/build_doc_definitions.py",
        },
        "records": records,
    }

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out, doc_counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terms", default="output/legal/word/terms.json")
    ap.add_argument("--out", default="output/legal/word/doc_definitions.json")
    a = ap.parse_args()

    if not Path(a.terms).exists():
        print(f"입력 없음: {a.terms}", file=sys.stderr)
        return 1

    out, doc_counts = build(a.terms, a.out)
    m = out["meta"]
    print(f"레코드 {m['레코드수']}건 / 용어 {m['용어수']}건 / "
          f"문서 {m['정의조항_보유문서수']}건 → {a.out}")
    print(f"요약상태: {m['요약상태분포']}")
    top = doc_counts.most_common(3)
    print("문서별 최다:", ", ".join(f"{Path(f).stem} {c}건" for f, c in top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
