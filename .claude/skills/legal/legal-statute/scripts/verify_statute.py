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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import statute_common as sc  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
ROOT = BASE.parents[3]
CONTRACT = BASE / "contract"
HISTORY_OUT_OF_SCOPE = {"조례", "훈령", "예규", "고시", "지침", "규칙"}

fails, warns = [], []

SCOPE_TARGET = {
    "옥외광고물등관리법",
    "서울특별시구로구옥외광고물등관리조례",
    "서울시옥외광고물가이드라인",
    "서울특별시구로구옥외광고물등의특정구역지정및표시제한완화고시",
}


def load_outputs_contract():
    return sc.read_json(CONTRACT / "outputs.json")


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
    v = jsonschema.Draft7Validator(sc.read_json(p))
    errs = sorted(v.iter_errors(data), key=lambda e: list(e.path))
    for e in errs[:15]:
        loc = "/".join(str(x) for x in e.path) or "(root)"
        fail(f"스키마[{label}]: {loc} — {e.message}")
    if len(errs) > 15:
        fail(f"스키마[{label}]: 외 {len(errs) - 15}건")


def check_declared_outputs(out_dir):
    """contract/outputs.json의 산출물 선언과 실제 파일의 기본 외형을 대조한다."""
    contract = load_outputs_contract()
    files = contract.get("files", {})
    for name, spec in files.items():
        path = out_dir / name
        if spec.get("required") and not path.exists():
            fail(f"산출물 계약 선언 파일 없음: {name}")
            continue
        schema_name = spec.get("schema")
        if schema_name and not (CONTRACT / schema_name).exists():
            fail(f"산출물 계약 스키마 파일 없음: {name} -> {schema_name}")
        top_keys = spec.get("topKeys")
        if not top_keys or not path.exists() or path.suffix != ".json":
            continue
        try:
            data = sc.read_json(path)
        except json.JSONDecodeError as exc:
            fail(f"산출물 JSON 파싱 실패: {name} — {exc.msg}")
            continue
        actual = set(data)
        expected = set(top_keys)
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing or extra:
            fail(f"산출물 topKeys 불일치 {name}: 누락 {missing} / 초과 {extra}")


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


def check_scope(S):
    """일반 조문 전수 범위가 문서·근거·표적 사례 계약을 만족하는지 확인한다."""
    summary = S["summary"]
    documents = S["documents"]
    mentions = S["mentions"]
    sources = S["sources"]
    relative = S["relative_references"]
    generic = S["generic_references"]

    if summary["document_count"] != 189 or len(documents) != 189:
        fail(f"일반 조문 범위 문서수 불일치: meta {summary['document_count']} / 실측 {len(documents)}")
    if len({row["source_file"] for row in documents}) != len(documents):
        fail("일반 조문 범위 documents에 중복 source_file이 있다")

    h4 = sum(row["article_span_count"] for row in documents)
    fallback = sum(row["scope_origin"] == "fallback_unsegmented_document"
                   for row in documents)
    preambles = sum(bool(row.get("has_unassigned_preamble")) for row in documents)
    if summary["article_span_count"] != h4:
        fail("일반 조문 범위 article_span_count가 documents 합계와 다르다")
    if summary["fallback_document_count"] != fallback or fallback == 0:
        fail("h4 조문 없는 문서 fallback 집계가 없거나 documents와 다르다")
    if summary["unassigned_preamble_document_count"] != preambles or preambles == 0:
        fail("첫 h4 조문 전 미귀속 선행본문 집계가 없거나 documents와 다르다")

    expected_counts = {
        "source_count": len(sources),
        "mention_count": len(mentions),
        "explicit_provision_mention_count": sum(bool(r["cited_units"]) for r in mentions),
        "cited_unit_occurrence_count": sum(len(r["cited_units"]) for r in mentions),
        "distinct_source_unit_pair_count": len({
            (r["source_key"], unit) for r in mentions for unit in r["cited_units"]
        }),
        "name_only_mention_count": sum(not r["cited_units"] for r in mentions),
        "relative_reference_count": len(relative),
        "generic_reference_count": len(generic),
    }
    for field, expected in expected_counts.items():
        if summary[field] != expected:
            fail(f"일반 조문 범위 {field} {summary[field]} != 실집계 {expected}")
    if summary["quoted_mention_count"] < summary["quoted_source_candidate_occurrence_count"]:
        fail("인용부호 안 자료명 후보보다 추출된 인용부호 mention이 적다")

    source_keys = {row["source_key"] for row in sources}
    for i, row in enumerate(mentions):
        if row["source_key"] not in source_keys:
            fail(f"scope mention[{i}] source_key가 sources에 없다")
        for field in ("district", "source_file", "article_title", "line", "evidence"):
            if not row.get(field):
                fail(f"scope mention[{i}] 근거 필드 {field}가 비었다")
                break
        if row["scope_tier"] not in {"T1_명시조문", "T2_명칭만_전문후보"}:
            fail(f"scope mention[{i}] 알 수 없는 tier: {row['scope_tier']}")

    target = {
        row["source_key"] for row in mentions
        if row["district"] == "11530PV2016001" and "옥외광고물" in row["article_title"]
    }
    missing = SCOPE_TARGET - target
    if missing:
        fail(f"서울개봉 옥외광고물 표적 범위 누락: {sorted(missing)}")

    # 관측 후보 레이어에 정본 필드가 들어오면 후보와 정본의 경계가 무너진다.
    forbidden = {"정식명칭", "법령ID", "시행일자", "출처URL"}
    for group_name in ("sources", "mentions"):
        for i, row in enumerate(S[group_name]):
            leaked = forbidden & set(row)
            if leaked:
                fail(f"scope {group_name}[{i}]에 정본 필드가 누출됐다: {sorted(leaked)}")


def check_scope_determinism(scope_dir):
    """같은 입력의 범위 생성기가 JSON·Markdown을 byte-for-byte 재현하는지 확인한다."""
    script = BASE / "scripts" / "build_guideline_article_scope.py"
    with tempfile.TemporaryDirectory(prefix="legal-scope-verify-") as tmp:
        out = Path(tmp)
        # summary의 기존 정의문 전용 인용수도 결정적 비교 대상이므로 같은 입력을 둔다.
        shutil.copy2(scope_dir / "statute_citations.json", out / "statute_citations.json")
        run = subprocess.run(
            [sys.executable, str(script), "--out-dir", str(out)],
            cwd=ROOT, capture_output=True, text=True,
        )
        if run.returncode:
            fail(f"일반 조문 범위 결정성 재생성 실패: {run.stderr.strip() or run.stdout.strip()}")
            return
        for name in ("guideline_article_scope.json", "guideline_article_scope.md"):
            if (scope_dir / name).read_bytes() != (out / name).read_bytes():
                fail(f"일반 조문 범위 결정성 실패: 재생성한 {name}이 현재 산출물과 다르다")


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




def check_source(C):
    miss = 0
    for c in C["citations"]:
        if sc.strip_separators(c["surface"]) not in sc.strip_separators(c["quote"]):
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
    check_declared_outputs(d)
    need = ["guideline_article_scope.json", "guideline_article_scope.md",
            "statute_citations.json", "statute_master.json", "norm_basis.json",
            "article_master.json", "statute_history.json",
            "_freshness_observations.json"]
    for f in need:
        if not (d / f).exists():
            print(f"산출물 없음: {d / f}", file=sys.stderr)
            return 1
    S = sc.read_json(d / "guideline_article_scope.json")
    C = sc.read_json(d / "statute_citations.json")
    M = sc.read_json(d / "statute_master.json")
    NB = sc.read_json(d / "norm_basis.json")
    AM = sc.read_json(d / "article_master.json")
    H = sc.read_json(d / "statute_history.json")
    O = sc.read_json(d / "_freshness_observations.json")

    check_scope(S)
    if a.full:
        check_scope_determinism(d)
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
    print(f"  일반 조문 범위: 문서 {S['summary']['document_count']} / "
          f"h4 조문 {S['summary']['article_span_count']} / "
          f"fallback 문서 {S['summary']['fallback_document_count']} / "
          f"미귀속 선행본문 {S['summary']['unassigned_preamble_document_count']} / "
          f"명칭후보 {S['summary']['source_count']}종")
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
