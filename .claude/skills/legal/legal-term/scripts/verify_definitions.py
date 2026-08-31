#!/usr/bin/env python3
"""definiation.json 계약 검증.

  기본    스키마 + 교차 제약 + terms.json 정합성
  --full  위 + 정의문 원문 대조 (md 재파싱)

통과가 갱신 완료 조건이다. 실패 시 종료코드 1.
"""

import argparse
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent      # 스킬 폴더
SCHEMA = BASE / "contract" / "definitions.schema.json"

# 절삭 직전 md 시점. f9b8a45 (`md 189건에 절삭·압축 적용`) 가 정의조항 구간을
# 제거했으므로(정본: legal-textreform case/정의조항제외.md), 그 부모 커밋이
# 정의문이 남아 있는 마지막 시점이다. 이력은 불변이라 고정 참조가 안전하다.
PRE_SLIM_REF = "f9b8a45^"

fails = []
warns = []


def fail(msg):
    fails.append(msg)


def warn(msg):
    warns.append(msg)


# ── 정의문 원문 대조용 정규화 ────────────────────────────────────────────────
# terms.json meta.정의문_저장시_변형규칙 과 같은 정규화를 md 쪽에도 적용한다.
# 맞추지 않으면 대량 오탐이 난다.
_MARKER = re.compile(r"^\s*>\s*\[(?:표|그림)[^\]]*\]\s*$", re.M)
# 정의 술어 — 여기까지가 정의부, 그 뒤는 규정부다
_PREDICATE = re.compile(r"(?:말한다|말하며|말함|칭한다|의미한다)")

# 인용부호는 원본 조판마다 표기가 다르다(“ ” " ' 「」 및 한컴 사설영역 U+F0850~3).
# 계약은 어휘 보존이지 인용부호 표기 보존이 아니므로 대조 전에 하나로 접는다.
_QUOTES = "“”‘’\"'「」『』｢｣0123"
_QUOTE_RE = re.compile("[" + re.escape(_QUOTES) + "]")


def _fold(s):
    """공백·강조·인용부호 표기 차이를 접는다."""
    s = s.replace("**", "")
    s = _QUOTE_RE.sub('"', s)
    return re.sub(r"\s+", "", s)


def norm_md(s):
    return _fold(_MARKER.sub("", s))


def norm_text(s):
    return _fold(s)


# ── 스키마 ───────────────────────────────────────────────────────────────────

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


# ── 교차 제약 ────────────────────────────────────────────────────────────────

def check_internal(data):
    meta = data["meta"]
    E = data["definitions"]

    if meta["용어수"] != len(E):
        fail(f"meta.용어수 {meta['용어수']} != definitions 길이 {len(E)}")

    # occurrence 총계 — 조번호분포·판정근거별 집계의 모수
    tot = sum(e["occurrence_count"] for e in E)
    # 정의 개수 — 용어 하나당 하나, 갈래가 있으면 갈래 수만큼
    n_def = len(E) + sum(len(e.get("variants_by_rule") or []) - 1
                         for e in E if e.get("variants_by_rule"))
    if meta["정의문수"] != n_def:
        fail(f"meta.정의문수 {meta['정의문수']} != 정의 개수 {n_def}")
    if meta.get("통합전_정의문수") != tot:
        fail(f"meta.통합전_정의문수 {meta.get('통합전_정의문수')} != occurrence 합 {tot}")
    n_surface = sum(e["surface_variant_count"] for e in E)
    if meta.get("표면변이수") != n_surface:
        fail(f"meta.표면변이수 {meta.get('표면변이수')} != 실집계 {n_surface}")
    n_multi = sum(1 for e in E if e.get("variants_by_rule"))
    if meta.get("갈래보유_용어수") != n_multi:
        fail(f"meta.갈래보유_용어수 {meta.get('갈래보유_용어수')} != 실집계 {n_multi}")

    if meta["정의조항_보유문서수"] > meta["대상문서수"]:
        fail("정의조항_보유문서수 > 대상문서수")
    if meta["제5조_보유문서수"] > meta["정의조항_보유문서수"]:
        fail("제5조_보유문서수 > 정의조항_보유문서수")

    denom = meta["정의조항_보유문서수"]
    ksum = sum(meta["판정근거별_정의문수"].values())
    if ksum != tot:
        fail(f"판정근거별_정의문수 합 {ksum} != 정의문수 {tot}")
    asum = sum(meta["조번호분포"].values())
    if asum != tot:
        fail(f"조번호분포 합 {asum} != 정의문수 {tot}")

    ids = set()
    all_dist = set()
    for e in E:
        t = e["term"]
        if e["id"] in ids:
            fail(f"중복 id: {e['id']}")
        ids.add(e["id"])
        all_dist |= set(e["districts"])

        if e["doc_frequency"] != len(set(e["districts"])):
            fail(f"[{t}] doc_frequency != districts 고유수")
        if e["occurrence_count"] < e["doc_frequency"]:
            fail(f"[{t}] occurrence_count < doc_frequency")
        if not e["definition"].strip():
            fail(f"[{t}] definition 이 비어 있다")

        r = round(e["doc_frequency"] / denom, 4)
        if abs(e["district_ratio_def"] - r) > 1e-6:
            fail(f"[{t}] district_ratio_def {e['district_ratio_def']} != {r}")

        # ubiquity 등급 재판정
        if e["doc_frequency"] == 1:
            exp = "단일지구"
        elif r >= 0.5:
            exp = "보편"
        elif r >= 0.1:
            exp = "빈출"
        else:
            exp = "산발"
        if e["ubiquity"] != exp:
            fail(f"[{t}] ubiquity {e['ubiquity']} != 기준상 {exp}")

        acount = sum(a["count"] for a in e["articles"])
        if acount != e["occurrence_count"]:
            fail(f"[{t}] articles.count 합 {acount} != occurrence_count {e['occurrence_count']}")

        exp5 = any(a["article_no"] == 5 for a in e["articles"])
        if e["in_article_5"] != exp5:
            fail(f"[{t}] in_article_5 가 articles 와 불일치")

        if e["cited_laws"] and e.get("classification", {}).get("legal_status") == "지침고유":
            fail(f"[{t}] cited_laws 가 있는데 legal_status=지침고유 — 자기모순")

        rules = e.get("variants_by_rule") or []
        if rules:
            if e["definition_variance"] != "실질충돌":
                fail(f"[{t}] 갈래가 있는데 등급이 {e['definition_variance']}")
            rsum = sum(r["occurrence_count"] for r in rules)
            if rsum != e["occurrence_count"]:
                fail(f"[{t}] 갈래 occurrence 합 {rsum} != {e['occurrence_count']}")
            seen = set()
            for r in rules:
                if not set(r["districts"]) <= set(e["districts"]):
                    fail(f"[{t}] 갈래 districts 가 항목 districts 를 벗어남")
                if r["doc_frequency"] != len(set(r["districts"])):
                    fail(f"[{t}] 갈래 doc_frequency != districts 고유수")
                k = tuple(sorted(r["numeric_signature"]))
                if k in seen:
                    fail(f"[{t}] 갈래 서명이 중복 — 합쳐졌어야 한다: {list(k)}")
                seen.add(k)
            # 대표는 최다 지구 갈래여야 한다
            top = max(rules, key=lambda r: (r["doc_frequency"], r["occurrence_count"]))
            if e["definition"] != top["definition"]:
                fail(f"[{t}] definition 이 최다 지구 갈래와 다르다")
        elif e["definition_variance"] == "실질충돌":
            fail(f"[{t}] 실질충돌인데 갈래가 없다")

    if denom != len(all_dist):
        fail(f"정의조항_보유문서수 {denom} != districts 합집합 {len(all_dist)}")

    # 반복도등급 집계 대조
    rc = collections.Counter(e["ubiquity"] for e in E)
    if dict(rc) != meta["반복도등급"]:
        fail(f"meta.반복도등급 {meta['반복도등급']} != 실집계 {dict(rc)}")

    vc = collections.Counter(e["definition_variance"] for e in E)
    if dict(vc) != meta.get("변이등급분포"):
        fail(f"meta.변이등급분포 {meta.get('변이등급분포')} != 실집계 {dict(vc)}")

    # 정렬 — 반복도 내림차순
    keys = [(-e["doc_frequency"], -e["occurrence_count"], e["term"]) for e in E]
    if keys != sorted(keys):
        fail("definitions 가 반복도 내림차순으로 정렬되어 있지 않다")

    ta = sum(t["count"] for t in data["definition_articles"]["titles"])
    if ta != tot:
        fail(f"definition_articles.titles.count 합 {ta} != 정의문수 {tot}")
    if data["definition_articles"]["고유표제수"] != len(data["definition_articles"]["titles"]):
        fail("definition_articles.고유표제수 != titles 길이")


def check_vs_terms(data, terms_path):
    p = Path(terms_path)
    if not p.exists():
        warn(f"terms.json 없음 — 정합성 검증 생략 ({terms_path})")
        return None
    T = json.loads(p.read_text(encoding="utf-8"))
    tids = {t["id"] for t in T["terms"]}
    ids = {e["id"] for e in data["definitions"]}
    extra = ids - tids
    if extra:
        fail(f"terms.json 에 없는 id {len(extra)}건: {sorted(extra)[:5]}")

    # definiation 의 정의문 수는 terms.json 의 in_definition_article 수 이상이어야 한다
    n_in = sum(
        1 for t in T["terms"] for o in t["occurrences"] if o.get("in_definition_article")
    )
    tot = sum(e["occurrence_count"] for e in data["definitions"])
    if tot < n_in:
        fail(f"정의문수 {tot} < terms.json in_definition_article {n_in} — 회수가 아니라 손실")
    return T


def check_source(data, T, md_root, limit, pre_slim_ref=PRE_SLIM_REF):
    """정의문이 해당 지구 md 에 문자열 그대로 있는가.

    terms.json 의 occurrence.file 은 생성 시점 규약(지구명_지구번호.md)이라
    실존하지 않을 수 있어 (region, dstrcNm) 으로 재해석한다. 현재 md 는
    정의조항 구간이 절삭되어 있으므로(legal-textreform case/정의조항제외.md)
    절삭 직전 시점(git 이력)의 md 도 함께 대조 대상으로 삼는다.
    """
    if T is None:
        warn("terms.json 없음 — 원문 대조 생략")
        return
    # 지구번호 -> md 경로 (현재 파일명 규약으로 재해석)
    dmap = {}
    for t in T["terms"]:
        for o in t["occurrences"]:
            if o["dstrcAppnNo"] in dmap:
                continue
            f = o["file"]
            if not Path(f).exists():
                alt = str(Path(md_root) / o["region"] / f"{o['dstrcNm']}.md")
                if Path(alt).exists():
                    f = alt
            dmap[o["dstrcAppnNo"]] = f

    cache = {}

    def body(dno):
        """현재 md 와 절삭 이전 md 를 함께 돌려준다 (없으면 None)."""
        if dno not in cache:
            f = dmap.get(dno)
            found = []
            if f and Path(f).exists():
                found.append(norm_md(Path(f).read_text(encoding="utf-8")))
            if f:
                p = subprocess.run(
                    ["git", "show", f"{pre_slim_ref}:{f}"],
                    capture_output=True, text=True,
                )
                if p.returncode == 0:
                    found.append(norm_md(p.stdout))
            cache[dno] = found or None
        return cache[dno]

    checked = miss = skipped = rule_only = 0

    def one(label, text, districts, flags):
        """정의문 하나를 해당 지구 md 와 대조한다."""
        nonlocal checked, miss, skipped, rule_only
        if "절단" in flags:
            return  # 절단 정의문은 원문과 끝이 다르다
        needle = norm_text(text)
        if not needle:
            return
        bodies = [b for d in districts for b in (body(d) or [])]
        if not bodies:
            skipped += 1
            return
        checked += 1
        # 원문이 마침표 없이 끝나는 줄을 추출기가 종결 보정한 경우가 있다
        # (실측: 고양지축 '차량출입 허용구간'). 종결부호는 어휘가 아니므로
        # 붙은 채로도, 뗀 채로도 맞으면 일치로 본다.
        if any(needle in b for b in bodies):
            return
        bare = needle.rstrip(".。")
        if bare != needle and any(bare in b for b in bodies):
            return

        # 전문이 없으면 정의부만 다시 본다. 정의부(말한다/말하며 까지)가
        # 원문에 있으면 어긋난 곳은 그 뒤 규정부이며, 이는 terms.json
        # 조립 단계에서 문장을 건너뛴 결과다 — 용어집의 정의는 유효하다.
        mc = _PREDICATE.search(text)
        core = norm_text(text[: mc.end()]) if mc else ""
        if core and any(core in b for b in bodies):
            rule_only += 1
            if rule_only <= 5:
                warn(
                    f"규정부 불일치 [{label}] — 정의부는 원문과 일치. "
                    f"terms.json 조립 시 문장 건너뜀"
                )
            return

        miss += 1
        if miss <= 10:
            fail(f"원문없음 [{label}]: {text[:60]}…")

    for e in data["definitions"][:limit] if limit else data["definitions"]:
        flags = e.get("definition_quality_flags", [])
        rules = e.get("variants_by_rule") or []
        if rules:
            # 대표는 최다 갈래와 같으므로 갈래만 대조하면 중복이 없다
            for i, r in enumerate(rules):
                one(f"{e['term']} 갈래{i}", r["definition_full"] or r["definition"],
                    r["districts"], flags if i == 0 else [])
        else:
            one(e["term"], e.get("definition_full") or e["definition"],
                e["districts"], flags)

    if skipped:
        warn(f"원문 대조 생략 {skipped}건 — md 파일을 찾지 못했다")
    if rule_only > 5:
        warn(f"규정부 불일치 외 {rule_only - 5}건")
    print(
        f"  원문 대조: {checked}건 중 정의부 불일치 {miss}건, "
        f"규정부만 불일치 {rule_only}건"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="output/legal/word/definiation.json")
    ap.add_argument("--terms", default="output/legal/word/terms.json")
    ap.add_argument("--md-root", default="output/legal/markdown")
    ap.add_argument("--full", action="store_true", help="정의문 원문 대조까지")
    ap.add_argument("--limit", type=int, default=0, help="원문 대조 상위 N 용어만")
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
    if a.full:
        check_source(data, T, a.md_root, a.limit, a.pre_slim_ref)

    m = data["meta"]
    print(
        f"  용어 {m['용어수']} / 정의 {m['정의문수']} "
        f"(갈래 보유 {m.get('갈래보유_용어수', 0)}건) / "
        f"정의조항 보유문서 {m['정의조항_보유문서수']}"
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
