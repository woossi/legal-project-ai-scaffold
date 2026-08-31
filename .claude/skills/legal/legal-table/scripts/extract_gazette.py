#!/usr/bin/env python3
"""고시·공고 번호 관측 — `gazette_refs.json` 을 만든다.

**관측이지 확정이 아니다.** `asOf`·`적용판본`·`시행일` 필드를 두지 않는다. 두면
관측값이 확정값으로 승격된다(`프로젝트-설계구조.md` §3).

자기지시·외부인용을 자동으로 가르는 규칙은 두 방향 다 오판이 났다 — 지구명 포함
여부로 가르면 고양탄현이 외부로 빠지고, 문서 앞머리 위치로 가르면 문정 도시개발구역의
국토해양부 고시가 자기지시로 올라온다. 그래서 표기 전건을 보존하고
`reference_class` 로 후보만 표시한다.

입력  output/legal/markdown/{서울,인천,경기}/*.md (189건)
출력  output/legal/table/gazette_refs.json
"""

import argparse
import collections
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import table_common as tc  # noqa: E402

OUT_PATH = "output/legal/table/gazette_refs.json"

# 고시·공고 번호. `고시 제2017-361호` · `국 토교통 부고시 제2025-635호`(OCR 공백)
GAZETTE_RE = re.compile(
    r"(?P<issuer>[가-힣]{0,12}(?:\s?[가-힣]{0,6})?)\s*"
    r"(?P<kind>고\s?시|공\s?고|훈\s?령|예\s?규)\s*"
    r"제\s*(?P<no>\d{4}\s*[-–]\s*\d{1,4})\s*호")
# 괄호 안 날짜. `(2024.12.06.)`
DATE_RE = re.compile(r"[(（]?\s*(\d{4}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}\s*\.?)\s*[)）]?")
# 자기지시 신호 — 이 지구의 지구계획·지구지정 승인 서술
SELF_HINT = re.compile(r"지구계획|지구\s*지정|실시계획|개발계획|기정|변경\s*승인|승인")
# 외부인용 신호 — 다른 법령·기준의 제목이 인용부호로 묶여 뒤따른다
EXT_HINT = re.compile(r"[「『｢].{2,40}?[」』｣]|에\s*따른|에\s*의한|규정에\s*의")


def _issuer(surface):
    """발행 주체 표기. 못 읽으면 None — 추정하지 않는다."""
    m = GAZETTE_RE.search(surface)
    if not m:
        return None
    iss = re.sub(r"\s+", "", m.group("issuer") or "")
    return iss or None


def _classify(quote, district):
    """`reference_class` 판정. 확정이 아니라 후보 표시다.

    지구명 포함·문서 위치 어느 쪽으로 갈라도 오판이 나므로, 두 신호가 함께
    걸릴 때만 후보로 올리고 나머지는 `미판정` 으로 둔다.
    """
    name = re.sub(r"\s+", "", district or "")
    core = re.sub(r"(공공주택지구|도시개발구역|택지개발사업|도시개발사업|지구|구역)$",
                  "", name)
    has_name = bool(core) and core in re.sub(r"\s+", "", quote)
    self_hint = bool(SELF_HINT.search(quote))
    ext_hint = bool(EXT_HINT.search(quote))

    if has_name and self_hint:
        return ("자기지시후보",
                "발췌에 지구명이 실재하고 지구계획·승인 서술이 인접")
    if ext_hint and not has_name:
        return ("외부인용후보",
                "발췌에 지구명이 없고 인용부호로 묶인 외부 자료명 또는 "
                "준거 서술이 인접")
    return ("미판정",
            f"자동 판정 신호가 엇갈린다(지구명 {has_name} · 자기지시 {self_hint} "
            f"· 외부인용 {ext_hint}). 어느 쪽으로도 승격하지 않는다")


def build(root="."):
    out = []
    seq = 0
    # 처리 시작 때 목록을 고정한다 — 메타에 적는 문서 수는 이 목록의 길이여야
    # 실제 처리분과 어긋나지 않는다.
    paths = tc.md_files(root)
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            doc = tc.parse_document(fh.read())
        lines = doc["lines"]
        rel = tc.rel_path(path, root)
        dstrc = doc["지구번호"]
        for i in range(doc["body_start"], len(lines)):
            raw = lines[i]
            if tc.HTML_COMMENT.match(raw):
                continue
            for m in GAZETTE_RE.finditer(raw):
                seq += 1
                quote = raw.strip()
                # 날짜는 표기 뒤 30자 안에서만 읽는다. 못 읽으면 null.
                tail = raw[m.end():m.end() + 30]
                dm = DATE_RE.search(tail)
                cls, basis = _classify(quote, doc["지구명"])
                art, art_why = tc.article_at(doc, i + 1)
                rec = {
                    "gazette_id": f"G{seq:06d}",
                    "dstrcAppnNo": dstrc,
                    "lc5": dstrc[:5] if dstrc else None,
                    "지역": doc["지역"],
                    "district": doc["지구명"],
                    "source_file": rel,
                    "surface": re.sub(r"\s+", " ", m.group(0)).strip(),
                    "surface_offset": [m.start(), m.end()],
                    "date_surface": dm.group(1).strip() if dm else None,
                    "line": i + 1,
                    "quote": quote,
                    "reference_class": cls,
                    "class_basis": basis,
                    "issuer_surface": _issuer(m.group(0)),
                    "article": ({"no": art["조번호"], "label": art["표제"],
                                 "line": art["line"], "origin": "조문헤딩"}
                                if art else None),
                }
                if art is None:
                    rec["article_reason"] = art_why
                out.append(rec)
    out.sort(key=lambda r: (r["dstrcAppnNo"] or "", r["line"],
                            r["surface_offset"][0]))
    # 정렬 후 번호를 다시 매겨 멱등성을 보장한다
    for n, r in enumerate(out, 1):
        r["gazette_id"] = f"G{n:06d}"
    return out, len(paths)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    recs, n_docs = build(args.root)
    doc = {
        "meta": {
            "생성기": "legal-table/scripts/extract_gazette.py",
            "입력": "output/legal/markdown/{서울,인천,경기}/*.md",
            "레코드수": len(recs),
            "보유문서수": len({r["dstrcAppnNo"] for r in recs}),
            # 실제로 순회한 입력 목록의 길이다 — 관측값이지 계약 기대값이 아니다.
            # 계약(gazette_ref.schema.json)의 const 189 와 어긋나면 코퍼스가
            # 변했다는 뜻이고, 그 대조가 누락을 잡는 자리다.
            "전체문서수": n_docs,
            "reference_class_분포": dict(sorted(
                collections.Counter(r["reference_class"] for r in recs).items(),
                key=lambda kv: (-kv[1], kv[0]))),
            "승격금지": (
                "이 파일의 어떤 값도 asOf·적용판본·시행일로 승격하지 않는다. "
                "reference_class 는 후보 표시이지 확정이 아니다. 지구번호 연도도 "
                "적용일이 아니다"),
            "판정_한계": (
                "자기지시·외부인용을 자동으로 가르는 규칙은 두 방향 다 오판이 "
                "났다. 지구명 포함으로 가르면 고양탄현이 외부로 빠지고, 문서 "
                "앞머리 위치로 가르면 문정 도시개발구역의 국토해양부 고시가 "
                "자기지시로 올라온다. 그래서 신호가 엇갈리면 미판정으로 둔다"),
        },
        "records": recs,
    }
    out = args.out or os.path.join(args.root, OUT_PATH)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    body = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(body)
    print(f"{out}  {len(recs)}건  문서 {doc['meta']['보유문서수']}  "
          f"sha256 {hashlib.sha256(body.encode()).hexdigest()[:16]}…")
    print(f"  {doc['meta']['reference_class_분포']}")


if __name__ == "__main__":
    main()
