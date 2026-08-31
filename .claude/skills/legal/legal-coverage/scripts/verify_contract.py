#!/usr/bin/env python3
"""_coverage.json 이 contract/ 의 계약을 만족하는지 검증한다.

단계 사슬(앞 단계 분자 = 뒤 단계 분모), 분모 ≥ 분자, 값 도메인, 미집계 단계의
빈 필드, 제외 시 수치의 범위, 정본 경로의 실재를 검사한다.

  입력  output/legal/analysis/_coverage.json
        contract/{outputs.json, coverage.schema.json}
  출력  표준출력에 위반 목록. 종료코드 0=계약 충족, 1=위반, 2=검증 불가

  python3 scripts/verify_contract.py
  python3 scripts/verify_contract.py --strict   # 어긋남이 남아 있으면 실패로 돌린다
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTRACT = HERE.parent / "contract"
ROOT = HERE.parents[4]

FAILS = []
WARNS = []


def rel(p):
    """저장소 기준 상대 경로. 저장소 밖이면 절대 경로를 그대로 쓴다."""
    try:
        return str(Path(p).resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def repo_file(path, label):
    p = Path(path)
    if p.is_absolute() or ".." in p.parts:
        fail(f"{label}: 저장소 상대 경로가 아니다 — {path}")
        return None
    return ROOT / p


def fail(msg):
    FAILS.append(msg)


def warn(msg):
    WARNS.append(msg)


def check_schema(data):
    try:
        import jsonschema
    except ImportError:
        sys.stderr.write("[2] jsonschema 미설치 — 구조 스키마를 검증할 수 없다\n")
        return False
    try:
        schema = read_json(CONTRACT / "coverage.schema.json")
        jsonschema.Draft7Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, jsonschema.SchemaError) as exc:
        fail(f"스키마 자체 오류 — {exc}")
        return True
    v = jsonschema.Draft7Validator(schema)
    errs = sorted(v.iter_errors(data), key=lambda e: list(e.path))
    for e in errs[:15]:
        loc = "/".join(str(x) for x in e.path) or "(root)"
        fail(f"스키마: {loc} — {e.message}")
    if len(errs) > 15:
        fail(f"스키마: 외 {len(errs) - 15}건")
    return True


def check_stages(data, spec):
    stages = data.get("stages") or []
    by_id = {s["stage_id"]: s for s in stages}
    if [s["stage_id"] for s in stages] != sorted(by_id, key=lambda x: int(x[1:])):
        fail("stages 가 stage_id 오름차순이 아니다 — 멱등성 계약 위반")
    domains = spec["valueDomains"]
    for s in stages:
        sid, st = s["stage_id"], s["상태"]
        if st not in domains["stages[].상태"]:
            fail(f"{sid}: 상태 값 도메인 밖 — {st!r}")

        if st == "미집계":
            # 못 센 것을 0 으로 적지 않는다
            for f in ("분자", "분모", "비율"):
                if s.get(f) is not None:
                    fail(f"{sid}: 미집계인데 {f}={s[f]!r} — 못 센 것을 값으로 적지 않는다")
            if not s.get("미집계사유"):
                fail(f"{sid}: 미집계인데 미집계사유가 없다")
            continue

        if s.get("분자") is None:
            fail(f"{sid}: 집계인데 분자가 없다")
            continue
        den = s.get("분모")
        if den is None:
            # 분모 없는 비율은 쓰지 않는다
            if s.get("비율") is not None:
                fail(f"{sid}: 분모가 없는데 비율이 있다 — 분모 없는 비율은 쓰지 않는다")
        else:
            if den < s["분자"]:
                fail(f"{sid}: 분모 {den} < 분자 {s['분자']}")
            want = round(s["분자"] / den, 4) if den else None
            if s.get("비율") != want:
                fail(f"{sid}: 비율 {s.get('비율')!r} ≠ 분자/분모 {want!r}")

        src = s.get("분모_출처단계")
        if src is not None:
            if src not in by_id:
                fail(f"{sid}: 분모_출처단계 {src} 가 stages 에 없다")
            elif by_id[src].get("분자") != den:
                fail(f"{sid}: 분모 {den} ≠ {src} 분자 {by_id[src].get('분자')!r} "
                     f"— 단계 사슬이 끊겼다")
        if not s.get("분모_정의"):
            fail(f"{sid}: 분모_정의가 비었다 — 분모를 함께 적지 않은 비율이다")

        for ex in s.get("제외시") or []:
            if den is not None and ex["분모"] > den:
                fail(f"{sid}: 제외시 분모 {ex['분모']} > 단계 분모 {den}")
            if ex["분자"] > s["분자"]:
                fail(f"{sid}: 제외시 분자 {ex['분자']} > 단계 분자 {s['분자']}")
            if ex["분모"] < ex["분자"]:
                fail(f"{sid}: 제외시 분모 {ex['분모']} < 분자 {ex['분자']}")
            want = round(ex["분자"] / ex["분모"], 4) if ex["분모"] else None
            if ex.get("비율") != want:
                fail(f"{sid}: 제외시 비율 {ex.get('비율')!r} ≠ 분자/분모 {want!r}")

        for p in s.get("정본") or []:
            target = repo_file(p, f"{sid}: 정본 경로")
            if target is not None and not target.exists():
                fail(f"{sid}: 정본 경로가 실재하지 않는다 — {p}")
        for b in s.get("입력_생성근거") or []:
            target = repo_file(b["파일"], f"{sid}: 생성근거 파일")
            if target is not None and not target.exists():
                fail(f"{sid}: 생성근거 파일이 실재하지 않는다 — {b['파일']}")


def check_meta(data):
    meta = data.get("meta") or {}
    stages = data.get("stages") or []
    n_ok = sum(1 for s in stages if s.get("상태") == "집계")
    n_no = sum(1 for s in stages if s.get("상태") == "미집계")
    if meta.get("단계수") != len(stages):
        fail(f"meta.단계수 {meta.get('단계수')!r} ≠ stages 길이 {len(stages)}")
    if meta.get("집계") != n_ok or meta.get("미집계") != n_no:
        fail(f"meta 집계/미집계 {meta.get('집계')!r}/{meta.get('미집계')!r} "
             f"≠ 실측 {n_ok}/{n_no}")
    canon = ".claude/skills/legal/legal-term/case/모수규약.md"
    if meta.get("모수정의_정본") != canon:
        fail(f"meta.모수정의_정본 이 {canon} 이 아니다 — 모수 정의의 정본은 하나다")
    elif not (ROOT / canon).exists():
        fail(f"모수 정의 정본이 실재하지 않는다 — {canon}")


def check_isolation(data):
    for row in data.get("격리") or []:
        if "모수" in row and row["건수"] > row["모수"]:
            fail(f"격리[{row['구분']}]: 건수 {row['건수']} > 모수 {row['모수']}")
        target = repo_file(row["정본"], f"격리[{row['구분']}]: 정본")
        if target is not None and not target.exists():
            fail(f"격리[{row['구분']}]: 정본이 실재하지 않는다 — {row['정본']}")


def check_blind_spots(data):
    if not (data.get("사각지대") or []):
        fail("사각지대가 비었다 — 검사 결과에는 보지 못하는 범위를 함께 적는다")


def check_mismatch_separation(data):
    """어긋남은 같은 파일 안의 대조여야 한다. 파일 간 비교는 재계산필드에 둔다."""
    for m in data.get("어긋남") or []:
        target = repo_file(m["파일"], f"어긋남[{m['필드']}]: 대상 파일")
        if target is not None and not target.exists():
            fail(f"어긋남[{m['필드']}]: 대상 파일이 실재하지 않는다 — {m['파일']}")
        if m.get("대조범위") != "파일내부":
            fail(f"어긋남[{m['필드']}]: 대조범위가 {m.get('대조범위')!r} 이다 — "
                 f"파일 간 동명 필드는 재계산필드로 옮긴다")
    for r in data.get("재계산필드") or []:
        for p in r.get("값") or {}:
            if p.startswith("output/"):
                target = repo_file(p, f"재계산필드[{r['필드명']}]: 대상 파일")
                if target is not None and target.exists():
                    continue
                fail(f"재계산필드[{r['필드명']}]: 대상 파일이 실재하지 않는다 — {p}")
        if len(r.get("값") or {}) < 2:
            fail(f"재계산필드[{r['필드명']}]: 값이 둘 미만이다 — 같은 이름을 쓰는 파일이 "
                 f"둘 이상일 때만 재계산 필드다")


def check_diff(data):
    d = data.get("대조표")
    if d is None:
        return
    ids = {s["stage_id"] for s in data.get("stages") or []}
    for c in d.get("변경") or []:
        if c.get("변화") != "사라진 단계" and c["stage_id"] not in ids:
            fail(f"대조표: 알 수 없는 stage_id {c['stage_id']}")
    if d.get("핵심결론_뒤집힘") not in ("변화없음", "검토필요"):
        fail(f"대조표.핵심결론_뒤집힘 값 도메인 밖 — {d.get('핵심결론_뒤집힘')!r}")


def print_report(path):
    print(f"대상: {rel(path)}")
    for w in WARNS:
        print(f"  경고 {w}")
    for f in FAILS:
        print(f"  위반 {f}")


def main():
    ap = argparse.ArgumentParser(description="legal-coverage 산출물 계약 검증")
    ap.add_argument("--report", help="검증할 리포트 경로 (기본값은 계약의 산출 경로)")
    ap.add_argument("--strict", action="store_true",
                    help="어긋남이 남아 있으면 실패로 돌린다")
    args = ap.parse_args()

    try:
        spec = read_json(CONTRACT / "outputs.json")
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"[2] 계약 파일을 읽지 못했다: {exc}\n")
        return 2
    name = next(iter(spec["files"]))
    path = Path(args.report) if args.report else ROOT / spec["outBase"] / name
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        sys.stderr.write(f"[2] 산출물 없음: {path}\n")
        return 2
    try:
        data = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"[2] 산출물을 읽지 못했다: {rel(path)} — {exc}\n")
        return 2

    top = set(spec["files"][name]["topKeys"])
    if set(data) != top:
        fail(f"최상위 키 불일치 — 누락 {sorted(top - set(data))} "
             f"초과 {sorted(set(data) - top)}")

    if not check_schema(data):
        return 2
    if FAILS:
        print_report(path)
        print(f"계약 위반 {len(FAILS)}건")
        return 1
    check_meta(data)
    check_stages(data, spec)
    check_isolation(data)
    check_blind_spots(data)
    check_mismatch_separation(data)
    check_diff(data)

    print_report(path)

    n_mis = len(data.get("어긋남") or [])
    if n_mis:
        print(f"  어긋남 {n_mis}건 — 상류 산출물의 요약과 원자료가 다르다. "
              f"이 리포트를 고치지 말고 상류 소유자에게 넘긴다")
        for m in data["어긋남"]:
            got = json.dumps(m["재계산"], ensure_ascii=False)
            print(f"    {m['파일']} {m['필드']} — 기재 {m['기재']} vs 재계산 {got}")

    if FAILS:
        print(f"계약 위반 {len(FAILS)}건")
        return 1
    if args.strict and n_mis:
        print(f"--strict: 어긋남 {n_mis}건이 남아 있다")
        return 1
    print(f"계약 충족 (경고 {len(WARNS)}건 · 어긋남 {n_mis}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
