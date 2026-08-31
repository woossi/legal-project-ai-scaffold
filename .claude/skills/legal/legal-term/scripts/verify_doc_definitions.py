#!/usr/bin/env python3
"""doc_definitions.json 계약 검증.

  기본    스키마 + 교차 제약 + terms.json 정합성
  --full  위 + 요약을 해당 원본 md 와 대조

통과가 갱신 완료 조건이다. 실패 시 종료코드 1.
"""

import argparse
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_definitions import norm_md, norm_text  # noqa: E402 — 인용부호·공백 접기 공유

BASE = Path(__file__).resolve().parent.parent      # 스킬 폴더
SCHEMA = BASE / "contract" / "doc_definitions.schema.json"

# 절삭 직전 md 시점. f9b8a45 (`md 189건에 절삭·압축 적용`) 가 정의조항 구간을
# 제거했으므로(정본: legal-textreform case/정의조항제외.md), 그 부모 커밋이
# 정의문이 남아 있는 마지막 시점이다. 이력은 불변이라 고정 참조가 안전하다.
PRE_SLIM_REF = "f9b8a45^"

# 쉼표·가운뎃점 계열. 변이 병합이 이 수준의 차이(`제1종·제2종` vs `제1종, 제2종`)를
# 한 변이로 접으므로, 대표 표기 대조에서도 같은 폭으로 접는다. 어휘는 보존된다.
_PUNCT = re.compile(r"[,·‧․]")


def fold_punct(s):
    return _PUNCT.sub("", s)

fails = []
warns = []


def fail(msg):
    fails.append(msg)


def warn(msg):
    warns.append(msg)


def check_schema(data):
    try:
        import jsonschema
    except ImportError:
        warn("jsonschema 미설치 — 스키마 검증을 건너뛰었다 (pip install jsonschema)")
        return
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    v = jsonschema.Draft7Validator(schema)
    errs = sorted(v.iter_errors(data), key=lambda e: list(e.path))
    for e in errs[:20]:
        loc = "/".join(str(x) for x in e.path) or "(root)"
        fail(f"스키마: {loc} — {e.message}")
    if len(errs) > 20:
        fail(f"스키마: 외 {len(errs) - 20}건")


def check_internal(data):
    meta = data["meta"]
    R = data["records"]

    if meta["레코드수"] != len(R):
        fail(f"meta.레코드수 {meta['레코드수']} != records 길이 {len(R)}")
    if meta["용어수"] != len({r["term_id"] for r in R}):
        fail("meta.용어수 != 고유 term_id 수")
    if meta["정의조항_보유문서수"] != len({r["dstrcAppnNo"] for r in R}):
        fail("meta.정의조항_보유문서수 != 고유 dstrcAppnNo 수")
    if meta["정의조항_보유문서수"] > meta["대상문서수"]:
        fail("정의조항_보유문서수 > 대상문서수")
    if meta["중복병합"]["병합후_레코드수"] != len(R):
        fail("meta.중복병합.병합후_레코드수 != records 길이")
    occ_sum = sum(r["occurrence_count"] for r in R)
    if meta["중복병합"]["병합전_occurrence수"] != occ_sum:
        fail(f"meta.중복병합.병합전_occurrence수 != occurrence_count 합 {occ_sum}")
    if occ_sum > meta["정의문수"]:
        fail("occurrence_count 합 > meta.정의문수")

    sc = collections.Counter(r["definition_status"] for r in R)
    if dict(sc) != meta["요약상태분포"]:
        fail(f"meta.요약상태분포 {meta['요약상태분포']} != 실집계 {dict(sc)}")

    seen = set()
    for r in R:
        label = f"{r['dstrcNm']}/{r['term']}"
        key = (r["source_file"], r["term_id"], r["variant_index"])
        if key in seen:
            fail(f"[{label}] 중복 키 (source_file, term_id, variant_index)")
        seen.add(key)

        # 요약은 정의부 원문의 연속 부분문자열 — 재작성 금지 계약
        if r["definition"] not in r["definition_source_text"]:
            fail(f"[{label}] definition 이 definition_source_text 의 부분문자열이 아니다")
        if r["definition_status"] == "요약실패":
            if r["definition"] != r["definition_source_text"].strip():
                fail(f"[{label}] 요약실패인데 definition 이 정의부 원문과 다르다")
        if not Path(r["source_file"]).exists():
            fail(f"[{label}] source_file 없음: {r['source_file']}")

    keys = [(r["source_file"], r["term"], r["variant_index"]) for r in R]
    if keys != sorted(keys):
        fail("records 가 source_file, term, variant_index 오름차순이 아니다")


def check_vs_terms(data, terms_path):
    p = Path(terms_path)
    if not p.exists():
        warn(f"terms.json 없음 — 정합성 검증 생략 ({terms_path})")
        return None
    T = json.loads(p.read_text(encoding="utf-8"))
    tmap = {t["id"]: t for t in T["terms"]}
    extra = {r["term_id"] for r in data["records"]} - set(tmap)
    if extra:
        fail(f"terms.json 에 없는 term_id {len(extra)}건: {sorted(extra)[:5]}")

    # 정의부 원문은 terms.json 의 variant.text_core 와 같아야 한다
    mismatch = 0
    for r in data["records"]:
        t = tmap.get(r["term_id"])
        if t is None:
            continue
        v = next(
            (v for v in t["variants"] if v["variant_index"] == r["variant_index"]), None
        )
        if v is None:
            fail(f"[{r['dstrcNm']}/{r['term']}] variant_index 가 terms.json 에 없다")
            continue
        core = v.get("text_core") or v.get("text") or ""
        if r["definition_source_text"] != core:
            mismatch += 1
            if mismatch <= 5:
                fail(f"[{r['dstrcNm']}/{r['term']}] definition_source_text 가 "
                     f"terms.json variant 와 다르다")
    if mismatch > 5:
        fail(f"definition_source_text 불일치 외 {mismatch - 5}건")
    return T


def check_vs_definiation(data, defi_path):
    p = Path(defi_path)
    if not p.exists():
        warn(f"definiation.json 없음 — 비교 생략 ({defi_path})")
        return
    D = json.loads(p.read_text(encoding="utf-8"))
    # 두 산출물은 같은 조 표제 재판정을 쓰므로 모수가 같아야 한다
    if data["meta"]["정의조항_보유문서수"] != D["meta"]["정의조항_보유문서수"]:
        fail(
            f"정의조항_보유문서수 {data['meta']['정의조항_보유문서수']} != "
            f"definiation.json {D['meta']['정의조항_보유문서수']} — 판정 규칙이 갈라졌다"
        )
    if data["meta"]["용어수"] != D["meta"]["용어수"]:
        warn(
            f"용어수 {data['meta']['용어수']} != definiation.json "
            f"{D['meta']['용어수']} — 병합 기준 차이인지 확인할 것"
        )


def check_source(data, T, limit, pre_slim_ref):
    """요약이 원본 md 에 (표기 접기 후) 부분문자열로 존재하는가.

    요약은 표제어 도입부를 제거한 본문이라 인용부호 표기 차이의 영향을 받지
    않는다. 3단으로 본다.
    ① 해당 문서 — 현재 md, 없으면 절삭 직전 시점(git 이력)의 md. 현재 md 는
       정의조항 구간이 절삭되어 있다(legal-textreform case/정의조항제외.md).
    ② 구두점 접기 재시도 — 변이 병합이 쉼표·가운뎃점 차이를 한 변이로 접는다.
    ③ 같은 변이의 타 지구 md — 대표 표기는 병합된 지구 중 한 문서의 표기를
       따르므로 해당 문서에 없을 수 있다. 여기서 잡히면 경고 집계다.
    셋 다 실패해야 계약 위반이다.
    """
    cache = {}
    pre_cache = {}

    def body(f):
        if f not in cache:
            fp = Path(f)
            cache[f] = norm_md(fp.read_text(encoding="utf-8")) if fp.exists() else None
        return cache[f]

    def pre_body(f):
        if f not in pre_cache:
            p = subprocess.run(
                ["git", "show", f"{pre_slim_ref}:{f}"],
                capture_output=True, text=True,
            )
            pre_cache[f] = norm_md(p.stdout) if p.returncode == 0 else None
        return pre_cache[f]

    def found_in_doc(needle, f):
        b = body(f)
        if b is None:
            return None  # 문서 자체가 없다
        if needle in b or fold_punct(needle) in fold_punct(b):
            return True
        pb = pre_body(f)
        if pb is not None and (needle in pb or fold_punct(needle) in fold_punct(pb)):
            return True
        return False

    # 같은 변이를 공유하는 타 지구 문서 — (term_id, variant_index) -> 파일 목록
    peer_files = collections.defaultdict(set)
    if T is not None:
        code_to_file = {
            r["dstrcAppnNo"]: r["source_file"] for r in data["records"]
        }
        for t in T["terms"]:
            for o in t["occurrences"]:
                f = code_to_file.get(o["dstrcAppnNo"])
                if f:
                    peer_files[(t["id"], o.get("variant_index"))].add(f)

    checked = miss = skipped = in_doc = in_peer = 0
    R = data["records"][:limit] if limit else data["records"]
    for r in R:
        needle = norm_text(r["definition"])
        if not needle:
            continue
        hit = found_in_doc(needle, r["source_file"])
        if hit is None:
            skipped += 1
            continue
        checked += 1
        if hit:
            in_doc += 1
            continue
        peers = peer_files.get((r["term_id"], r["variant_index"]), set())
        if any(found_in_doc(needle, f) for f in peers if f != r["source_file"]):
            in_peer += 1
            continue
        miss += 1
        if miss <= 10:
            fail(f"원문없음 [{r['dstrcNm']}/{r['term']}]: {r['definition'][:60]}…")
    if miss > 10:
        fail(f"원문없음 외 {miss - 10}건")
    if skipped:
        warn(f"원문 대조 생략 {skipped}건 — md 파일을 찾지 못했다")
    if in_peer:
        warn(
            f"타 지구 표기 일치 {in_peer}건 — 대표 표기가 해당 문서 표기와 "
            f"구두점 이상으로 다르다. definition_source_text 는 대표 표기임을 유의"
        )
    print(
        f"  원문 대조: {checked}건 — 해당 문서 일치 {in_doc}, "
        f"타 지구 표기 일치 {in_peer}, 불일치 {miss}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="output/legal/word/doc_definitions.json")
    ap.add_argument("--terms", default="output/legal/word/terms.json")
    ap.add_argument("--definiation", default="output/legal/word/definiation.json")
    ap.add_argument("--full", action="store_true", help="요약 원문 대조까지")
    ap.add_argument("--limit", type=int, default=0, help="원문 대조 상위 N 레코드만")
    ap.add_argument("--pre-slim-ref", default=PRE_SLIM_REF,
                    help="정의조항 절삭 이전 md 를 읽을 git 참조")
    a = ap.parse_args()

    p = Path(a.file)
    if not p.exists():
        print(f"산출물 없음: {a.file}", file=sys.stderr)
        return 1
    data = json.loads(p.read_text(encoding="utf-8"))

    check_schema(data)
    check_internal(data)
    T = check_vs_terms(data, a.terms)
    check_vs_definiation(data, a.definiation)
    if a.full:
        check_source(data, T, a.limit, a.pre_slim_ref)

    m = data["meta"]
    print(
        f"  레코드 {m['레코드수']} / 용어 {m['용어수']} / "
        f"문서 {m['정의조항_보유문서수']} / 요약상태 {m['요약상태분포']}"
    )
    for w in warns:
        print(f"  경고: {w}")
    if fails:
        print(f"\n실패 {len(fails)}건")
        for f in fails[:40]:
            print(f"  - {f}")
        if len(fails) > 40:
            print(f"  … 외 {len(fails) - 40}건")
        return 1
    print("계약 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
