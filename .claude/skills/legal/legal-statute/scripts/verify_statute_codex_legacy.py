#!/usr/bin/env python3
"""output/legal/statute/ 계약 검증.

  기본    스키마 + 교차 제약 + terms.json 정합성
  --full  위 + 인용 표기가 근거 문장에 실재하는지 대조

통과가 갱신 완료 조건이다. 실패 시 종료코드 1.
"""

import argparse
import bisect
import collections
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CONTRACT = BASE / "contract"
HISTORY_OUT_OF_SCOPE = {"조례", "훈령", "예규", "고시", "지침", "규칙"}

fails, warns = [], []


def fail(m):
    fails.append(m)


def warn(m):
    warns.append(m)


def check_schema(data, schema_name, label):
    try:
        import jsonschema
    except ImportError:
        warn("jsonschema 미설치 — 스키마 검증을 건너뛰었다 (pip install jsonschema)")
        return
    p = CONTRACT / schema_name
    if not p.exists():
        fail(f"스키마 파일 없음: {p}")
        return
    v = jsonschema.Draft7Validator(json.loads(p.read_text(encoding="utf-8")))
    errs = sorted(v.iter_errors(data), key=lambda e: list(e.path))
    for e in errs[:15]:
        loc = "/".join(str(x) for x in e.path) or "(root)"
        fail(f"스키마[{label}]: {loc} — {e.message}")
    if len(errs) > 15:
        fail(f"스키마[{label}]: 외 {len(errs) - 15}건")


def check_citations(C):
    meta, cits = C["meta"], C["citations"]
    if meta["인용수"] != len(cits):
        fail(f"meta.인용수 {meta['인용수']} != citations 길이 {len(cits)}")
    if meta["법령수"] != len({c["statute_key"] for c in cits}):
        fail("meta.법령수 != 고유 statute_key 수")
    ids = set()
    for c in cits:
        label = f"{c['term']}/{c['statute_key']}"
        if c["citation_id"] in ids:
            fail(f"중복 citation_id: {c['citation_id']}")
        ids.add(c["citation_id"])
        if not c["districts"]:
            fail(f"[{label}] districts 가 비었다 — 출처 없는 간선")
        if not c["quote"].strip():
            fail(f"[{label}] quote 가 비었다 — 원문 근거 없는 간선")


def check_master(M, C):
    meta, sts = M["meta"], M["statutes"]
    if meta["법령수"] != len(sts):
        fail(f"meta.법령수 {meta['법령수']} != statutes 길이 {len(sts)}")
    tot = sum(s["인용수"] for s in sts)
    if tot != len(C["citations"]):
        fail(f"인용수 합 {tot} != citations 길이 {len(C['citations'])}")
    ver = collections.Counter(s["검증상태"] for s in sts)
    if dict(ver) != meta["검증상태분포"]:
        fail(f"meta.검증상태분포 {meta['검증상태분포']} != 실집계 {dict(ver)}")

    # dangling 없음 — 모든 인용의 법령이 정본 목록에 실재해야 한다
    missing = {c["statute_key"] for c in C["citations"]} - {s["statute_key"] for s in sts}
    if missing:
        fail(f"정본 목록에 없는 statute_key {len(missing)}건: {sorted(missing)[:5]}")

    for s in sts:
        t = s["실측표기"]
        if s["검증상태"] == "정본대조":
            for f in ("정식명칭", "법령ID", "시행일자", "출처URL"):
                if not s[f]:
                    fail(f"[{t}] 정본대조인데 {f} 가 비었다")
        else:
            # 못 찾은 것을 찾은 것처럼 만들지 않는다
            if s["정식명칭"]:
                fail(f"[{t}] 미대조인데 정식명칭이 채워져 있다")
            if s["법령ID"]:
                fail(f"[{t}] 미대조인데 법령ID가 채워져 있다")

    keys = [(-s["인용수"], s["statute_key"]) for s in sts]
    if keys != sorted(keys):
        fail("statutes 가 인용수 내림차순으로 정렬되어 있지 않다")


def check_norm(NB):
    arts = {a["article_id"] for a in NB["articles"]}
    for a in NB["articles"]:
        if a["검증상태"] == "정본대조":
            if not a.get("출처URL"):
                fail(f"[{a['article_id']}] 정본대조인데 출처URL이 없다")
            if not a.get("시행일"):
                fail(f"[{a['article_id']}] 정본대조인데 시행일이 없다")
        if not a.get("요지", "").strip():
            fail(f"[{a['article_id']}] 요지가 비었다")
    for r in NB["relations"]:
        for side in ("from", "to"):
            if r[side] != "조례" and r[side] not in arts:
                fail(f"관계 {r['from']}→{r['to']} 의 {side} '{r[side]}' 가 articles 에 없다")
        if r["관계"] not in ("준용", "위임", "완화"):
            fail(f"관계 값 도메인 위반: {r['관계']}")
        if not r.get("근거", "").strip():
            fail(f"관계 {r['from']}→{r['to']} 에 근거가 없다")
    if NB["meta"]["조문수"] != len(NB["articles"]):
        fail("norm_basis meta.조문수 != articles 길이")
    if NB["meta"]["관계수"] != len(NB["relations"]):
        fail("norm_basis meta.관계수 != relations 길이")


def check_articles(NB, AM):
    """규범값 계통의 조문마다 본문이 수집되어 있는가."""
    got = {a["article_id"]: a for a in AM["articles"]}
    for a in NB["articles"]:
        g = got.get(a["article_id"])
        if g is None:
            fail(f"[{a['article_id']}] article_master 에 본문 항목이 없다")
            continue
        if g["수집상태"] != "수집":
            fail(f"[{a['article_id']}] 본문 수집상태가 {g['수집상태']} 이다")
        elif not g["조문본문"].strip():
            fail(f"[{a['article_id']}] 수집으로 표시됐는데 조문본문이 비었다")
    if AM["meta"]["조문수"] != len(AM["articles"]):
        fail("article_master meta.조문수 != articles 길이")


def check_history(M, C, H, O):
    """연혁 사실과 proxy 관측을 섞지 않고, 입력과의 전건 정합성을 확인한다."""
    targets = {
        s["statute_key"]: s for s in M["statutes"]
        if s["검증상태"] == "정본대조" and s["법령ID"]
    }
    rows = H["statutes"]
    if H["meta"]["법령수"] != len(rows):
        fail("statute_history meta.법령수 != statutes 길이")
    keys = [r["statute_key"] for r in rows]
    if len(keys) != len(set(keys)):
        fail("statute_history 에 중복 statute_key 가 있다")
    if set(keys) != set(targets):
        missing = sorted(set(targets) - set(keys))
        extra = sorted(set(keys) - set(targets))
        fail(f"statute_history 대상 불일치: 누락 {missing[:5]} / 초과 {extra[:5]}")
    states = collections.Counter(r["수집상태"] for r in rows)
    if dict(states) != H["meta"]["수집상태분포"]:
        fail("statute_history meta.수집상태분포 != 실집계")

    history_by_key = {}
    allowed = {"수집", "부분수집", "대상밖", "결과없음", "API오류"}
    for r in rows:
        key, state, versions = r["statute_key"], r["수집상태"], r["versions"]
        history_by_key[key] = r
        src = targets.get(key)
        if not src:
            continue
        for field in ("정식명칭", "법령ID", "법령구분", "인용수"):
            if r[field] != src[field]:
                fail(f"[{key}] statute_history.{field} 가 statute_master 와 다르다")
        if state not in allowed:
            fail(f"[{key}] 알 수 없는 이력 수집상태: {state}")
        expected_out = r["법령구분"] in HISTORY_OUT_OF_SCOPE
        if expected_out and state != "대상밖":
            fail(f"[{key}] {r['법령구분']}은 eflaw 대상밖인데 상태가 {state}이다")
        if not expected_out and state == "대상밖":
            fail(f"[{key}] {r['법령구분']}을 eflaw 대상밖으로 잘못 분류했다")
        if r["버전수"] != len(versions):
            fail(f"[{key}] 버전수 {r['버전수']} != versions 길이 {len(versions)}")
        effective = [v["시행일자"] for v in versions]
        if any(not d for d in effective):
            fail(f"[{key}] 시행일자 없는 판본이 있다")
        if effective != sorted(effective):
            fail(f"[{key}] 판본이 시행일자순이 아니다")
        first = effective[0] if effective else None
        last = effective[-1] if effective else None
        if r["최초시행일"] != first or r["최종시행일"] != last:
            fail(f"[{key}] 최초·최종시행일이 versions 와 다르다")
        if state == "수집" and (not versions or r["오류"] or r["절단여부"]):
            fail(f"[{key}] 수집 상태인데 비었거나 오류·절단 표시가 있다")
        if state == "부분수집" and not (versions and (r["오류"] or r["절단여부"])):
            fail(f"[{key}] 부분수집 상태와 versions·오류·절단 표시가 맞지 않는다")
        if state in {"대상밖", "결과없음", "API오류"} and versions:
            fail(f"[{key}] {state} 상태인데 versions 가 채워져 있다")
        if state == "대상밖" and not r["대상밖_사유"]:
            fail(f"[{key}] 대상밖 사유가 없다")

    per_pair = collections.defaultdict(list)
    for c in C["citations"]:
        if c["statute_key"] not in targets:
            continue
        for district in c["districts"]:
            per_pair[(c["statute_key"], district)].append(c["citation_id"])

    observations = O["observations"]
    if O["meta"]["관측수"] != len(observations):
        fail("freshness meta.관측수 != observations 길이")
    obs_by_pair = {}
    observed_states = collections.Counter()
    for o in observations:
        pair = (o["statute_key"], o["dstrcAppnNo"])
        if pair in obs_by_pair:
            fail(f"중복 신선도 관측: {pair[0]}/{pair[1]}")
        obs_by_pair[pair] = o
        if o["temporal_basis"] != "지구번호연도_proxy" or o["적용판본_미확정"] is not True:
            fail(f"[{pair[0]}/{pair[1]}] proxy 한계 표지가 없다")
        if o["citation_ids"] != per_pair.get(pair):
            fail(f"[{pair[0]}/{pair[1]}] citation_ids 가 인용 간선과 다르다")
        history = history_by_key.get(pair[0])
        if not history:
            continue
        if o["이력수집상태"] != history["수집상태"]:
            fail(f"[{pair[0]}/{pair[1]}] 이력수집상태가 statute_history 와 다르다")

        effective = [v["시행일자"] for v in history["versions"]]
        year = o["지구지정연도"]
        if history["수집상태"] != "수집" or year is None:
            observed_states["관측불가"] += 1
            for field in ("법령제정전_proxy", "proxy시점_시행중_판본_시행일",
                          "proxy이후_개정판본수"):
                if o[field] is not None:
                    fail(f"[{pair[0]}/{pair[1]}] 관측불가인데 {field} 가 채워져 있다")
            if not o["관측불가_사유"]:
                fail(f"[{pair[0]}/{pair[1]}] 관측불가 사유가 없다")
            continue

        idx = bisect.bisect_right(effective, f"{year}-12-31")
        expected_date = effective[idx - 1] if idx else None
        expected_after = len(effective) - idx
        if o["법령제정전_proxy"] != (idx == 0):
            fail(f"[{pair[0]}/{pair[1]}] 법령제정전_proxy 계산이 다르다")
        if o["proxy시점_시행중_판본_시행일"] != expected_date:
            fail(f"[{pair[0]}/{pair[1]}] proxy 판본 시행일 계산이 다르다")
        if o["proxy이후_개정판본수"] != expected_after:
            fail(f"[{pair[0]}/{pair[1]}] proxy 이후 판본수 계산이 다르다")
        if o["관측불가_사유"] is not None:
            fail(f"[{pair[0]}/{pair[1]}] 관측 가능하지만 불가 사유가 있다")
        observed_states["관측(법령제정전_proxy)" if idx == 0 else "관측"] += 1

    if set(obs_by_pair) != set(per_pair):
        missing = sorted(set(per_pair) - set(obs_by_pair))
        extra = sorted(set(obs_by_pair) - set(per_pair))
        fail(f"신선도 관측 대상 불일치: 누락 {missing[:5]} / 초과 {extra[:5]}")
    if dict(observed_states) != O["meta"]["관측상태분포"]:
        fail("freshness meta.관측상태분포 != 실집계")


def check_vs_terms(C, terms_path):
    p = Path(terms_path)
    if not p.exists():
        warn(f"terms.json 없음 — 정합성 검증 생략 ({terms_path})")
        return
    T = json.loads(p.read_text(encoding="utf-8"))
    extra = {c["term_id"] for c in C["citations"]} - {t["id"] for t in T["terms"]}
    if extra:
        fail(f"terms.json 에 없는 term_id {len(extra)}건: {sorted(extra)[:5]}")


_SEP = re.compile(r"[\s·‧․･・∙ㆍ,]")


def check_source(C):
    miss = 0
    for c in C["citations"]:
        if _SEP.sub("", c["surface"]) not in _SEP.sub("", c["quote"]):
            miss += 1
            if miss <= 10:
                fail(f"근거없음 [{c['term']}/{c['statute_key']}]: surface={c['surface']!r}")
    if miss > 10:
        fail(f"근거없음 외 {miss - 10}건")
    print(f"  근거 대조: {len(C['citations'])}건 중 불일치 {miss}건")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="output/legal/statute")
    ap.add_argument("--terms", default="output/legal/word/terms.json")
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args()

    d = Path(a.dir)
    need = ["statute_citations.json", "statute_master.json", "norm_basis.json",
            "article_master.json", "statute_history.json",
            "_freshness_observations.json"]
    for f in need:
        if not (d / f).exists():
            print(f"산출물 없음: {d / f}", file=sys.stderr)
            return 1
    C = json.loads((d / "statute_citations.json").read_text(encoding="utf-8"))
    M = json.loads((d / "statute_master.json").read_text(encoding="utf-8"))
    NB = json.loads((d / "norm_basis.json").read_text(encoding="utf-8"))
    AM = json.loads((d / "article_master.json").read_text(encoding="utf-8"))
    H = json.loads((d / "statute_history.json").read_text(encoding="utf-8"))
    O = json.loads((d / "_freshness_observations.json").read_text(encoding="utf-8"))

    check_schema(C, "statute_citations.schema.json", "citations")
    check_schema(M, "statute_master.schema.json", "master")
    check_citations(C)
    check_master(M, C)
    check_norm(NB)
    check_articles(NB, AM)
    check_history(M, C, H, O)
    check_vs_terms(C, a.terms)
    if a.full:
        check_source(C)

    ok = sum(1 for s in M["statutes"] if s["검증상태"] == "정본대조")
    cov = sum(s["인용수"] for s in M["statutes"] if s["검증상태"] == "정본대조")
    print(f"  법령 {M['meta']['법령수']}종 (정본대조 {ok}) / 인용 {C['meta']['인용수']}건 "
          f"(정본 커버 {cov}, {cov / max(1, C['meta']['인용수']):.1%})")
    print(f"  규범값 계통: 조문 {NB['meta']['조문수']} / 관계 {NB['meta']['관계수']} "
          f"/ 본문 {AM['meta']['수집상태분포']}")
    print(f"  법령 연혁: {H['meta']['수집상태분포']} / "
          f"신선도 관측 {O['meta']['관측상태분포']}")
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
