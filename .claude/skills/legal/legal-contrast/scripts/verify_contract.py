#!/usr/bin/env python3
"""산출물 계약 검증. 종료코드 0=충족, 1=위반.

가장 중요한 검사는 대조쌍 불변식이다 — 쌍은 대상축 하나만 달라야 하고 통제축은
모두 같아야 한다. 이것이 깨지면 변이를 축에 귀속시킨 결과 전체가 무의미해진다.

`--full` 은 판정 재계산까지 수행한다. 축 귀속과 보유구간을 원천에서 다시 구해
저장된 값과 대조한다.
"""
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
SKILL = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/legal/contrast"

FILES = ["facets.json", "pairs.json", "slots.json", "norms.json",
         "determinism.json", "variation.json", "_extraction_gap.json"]

errors, checked = [], []


def fail(msg):
    errors.append(msg)


def ok(msg):
    checked.append(msg)


def rel(path):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def print_report():
    print(f"검사 통과 {len(checked)}건")
    for c in checked:
        print(f"  OK  {c}")
    if errors:
        print(f"\n계약 위반 {len(errors)}건")
        for e in errors:
            print(f"  FAIL {e}")


def load_outputs():
    docs = {}
    for name in FILES:
        path = OUT / name
        if not path.exists():
            fail(f"산출물 없음: {name}")
            continue
        try:
            docs[name] = read_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            sys.stderr.write(f"[2] JSON 읽기 실패: {rel(path)} — {exc}\n")
            return None
    return docs if not errors else None


def check_json_schema(schema, docs):
    try:
        import jsonschema
    except ImportError:
        sys.stderr.write("[2] jsonschema 미설치 — 구조 스키마를 검증할 수 없다\n")
        return False

    try:
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
    except jsonschema.SchemaError as exc:
        fail(f"스키마 자체 오류 — {exc.message}")
        return True

    for fname, ref in schema["파일매핑"].items():
        validator = validator_cls({"$ref": ref, "$defs": schema["$defs"]})
        errs = sorted(validator.iter_errors(docs[fname]), key=lambda e: list(e.path))
        for err in errs[:15]:
            loc = "/".join(str(x) for x in err.path) or "(root)"
            fail(f"{fname} 스키마: {loc} — {err.message}")
        if len(errs) > 15:
            fail(f"{fname} 스키마: 외 {len(errs) - 15}건")
    if not errors:
        ok(f"JSON Schema 구조 계약 충족 ({len(schema['파일매핑'])}개 파일)")
    return True


def main():
    full = "--full" in sys.argv
    docs = load_outputs()
    if docs is None:
        print_report()
        return 1

    try:
        contract = read_json(SKILL / "contract/outputs.json")
        schema = read_json(SKILL / "contract/contrast.schema.json")
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"[2] 계약 파일 읽기 실패 — {exc}\n")
        return 2

    if not check_json_schema(schema, docs):
        return 2
    if errors:
        print_report()
        return 1

    facets = docs["facets.json"]
    pairs = docs["pairs.json"]
    slots = docs["slots.json"]
    norms = docs["norms.json"]
    det = docs["determinism.json"]
    gap = docs["_extraction_gap.json"]

    # ── 축 ──────────────────────────────────────────────────
    ds = facets["districts"]
    dmap = {d["지구번호"]: d for d in ds}
    if len(dmap) != len(ds):
        fail(f"지구번호 중복: {len(ds)}건 중 고유 {len(dmap)}")
    else:
        ok(f"지구 {len(ds)}건 고유")
    if facets["meta"]["지구수"] != len(ds):
        fail("facets.meta.지구수 불일치")
    if facets["meta"]["자치구수"] != len({d["자치구"] for d in ds}):
        fail("facets.meta.자치구수 불일치")
    if facets["meta"]["지구번호_파싱실패"]:
        fail(f"지구번호 파싱 실패 {facets['meta']['지구번호_파싱실패']}")
    else:
        ok("지구번호 전건 파싱")

    dom = contract["값도메인"]
    for ax, allowed in dom.items():
        bad = sorted({d[ax] for d in ds} - set(allowed))
        if bad:
            fail(f"{ax} 값 도메인 위반: {bad}")
    ok("축 값 도메인 충족")

    # ── 대조쌍 불변식 ────────────────────────────────────────
    bad_ctrl = bad_target = missing = 0
    for p in pairs["pairs"]:
        a, b = dmap.get(p["A"]), dmap.get(p["B"])
        if not a or not b:
            missing += 1
            continue
        for c in p["통제축"]:
            if a[c] != b[c]:
                bad_ctrl += 1
                break
        if p["대상축"] == "동일":
            if any(a[x] != b[x] for x in ("시기", "자치구", "주체", "근거법령")):
                bad_target += 1
        elif a[p["대상축"]] == b[p["대상축"]]:
            bad_target += 1
    if missing:
        fail(f"쌍의 지구가 facets 에 없음: {missing}건")
    if bad_ctrl:
        fail(f"통제축이 다른 쌍: {bad_ctrl}건 — 축 귀속 결과를 신뢰할 수 없다")
    if bad_target:
        fail(f"대상축이 같은 쌍: {bad_target}건 — 대조가 성립하지 않는다")
    if not (missing or bad_ctrl or bad_target):
        ok(f"대조쌍 불변식 {len(pairs['pairs'])}건 충족 (대상축만 상이, 통제축 동일)")

    pid = {p["pair_id"] for p in pairs["pairs"]}
    if len(pid) != len(pairs["pairs"]):
        fail("pair_id 중복")
    else:
        ok("pair_id 고유")
    if pairs["meta"]["쌍수"] != len(pairs["pairs"]):
        fail("pairs.meta.쌍수 불일치")

    # ── 슬롯 ────────────────────────────────────────────────
    D = slots["meta"]["조문표제_보유문서수"]
    bad = [s["slot"] for s in slots["slots"]
           if abs(s["coverage"] - s["district_count"] / D) > 1e-4]
    if bad:
        fail(f"슬롯 coverage 재계산 불일치 {len(bad)}건: {bad[:3]}")
    else:
        ok(f"슬롯 {len(slots['slots'])}건 coverage 정합")
    if any(len(s["districts"]) != s["district_count"] for s in slots["slots"]):
        fail("슬롯 district_count 와 districts 길이 불일치")
    if slots["meta"]["슬롯수"] != len(slots["slots"]):
        fail("slots.meta.슬롯수 불일치")
    slot_districts = {d for s in slots["slots"] for d in s["districts"]}
    unknown = sorted(slot_districts - set(dmap))
    if unknown:
        fail(f"slots 의 지구가 facets 에 없음 {len(unknown)}건: {unknown[:3]}")

    # ── 규범 ────────────────────────────────────────────────
    nmap = {n["norm_id"]: n for n in norms["norms"]}
    if len(nmap) != len(norms["norms"]):
        fail("norm_id 중복")
    bad = [n["norm_id"] for n in norms["norms"]
           if len(n["districts"]) != n["district_count"]]
    if bad:
        fail(f"규범 district_count 불일치 {len(bad)}건")
    else:
        ok(f"규범 {len(norms['norms'])}건 district_count 정합")
    if norms["meta"]["규범문장군집수"] != len(norms["norms"]):
        fail("norms.meta.규범문장군집수 불일치")
    norm_districts = {d for n in norms["norms"] for d in n["districts"]}
    unknown = sorted(norm_districts - set(dmap))
    if unknown:
        fail(f"norms 의 지구가 facets 에 없음 {len(unknown)}건: {unknown[:3]}")
    strengths = set(norms["meta"]["강도등급"])
    bad = {n["strength"] for n in norms["norms"]} - strengths
    if bad:
        fail(f"강도 등급 도메인 위반: {bad}")

    # variants 의 지구 합집합이 군집 지구와 같아야 한다
    bad = []
    for n in norms["norms"]:
        if not n["variants"]:
            continue
        u = set()
        for v in n["variants"]:
            u |= set(v["districts"])
        if u != set(n["districts"]):
            bad.append(n["norm_id"])
    if bad:
        fail(f"variants 지구합집합 ≠ 군집 지구 {len(bad)}건: {bad[:3]}")
    else:
        ok("variants 지구 합집합 정합")

    # ── 판정 ────────────────────────────────────────────────
    minD = contract["판정파라미터"]["MIN_DISTRICTS"]
    orphan = [d["norm_id"] for d in det["norms"] if d["norm_id"] not in nmap]
    if orphan:
        fail(f"determinism 의 norm_id 가 norms 에 없음 {len(orphan)}건")
    else:
        ok(f"판정 {len(det['norms'])}건 norm_id 정합")
    count_mismatch = [d["norm_id"] for d in det["norms"]
                      if d["norm_id"] in nmap
                      and d["district_count"] != nmap[d["norm_id"]]["district_count"]]
    if count_mismatch:
        fail(f"determinism district_count 가 norms 와 다름 {len(count_mismatch)}건")
    under = [d["norm_id"] for d in det["norms"] if d["district_count"] < minD]
    if under:
        fail(f"수록 범위 위반 — {minD}지구 미만이 실림 {len(under)}건")
    else:
        ok(f"수록 범위 충족 ({minD}지구 이상만)")

    U = det["meta"]["모수"]
    bands = contract["보유구간"]
    bad = []
    for d in det["norms"]:
        if abs(d["보유율"] - d["district_count"] / U) > 1e-3:
            bad.append(d["norm_id"])
            continue
        expect = next(b for b, lo in bands if d["보유율"] >= lo)
        if d["보유구간"] != expect:
            bad.append(d["norm_id"])
    if bad:
        fail(f"보유율·보유구간 재계산 불일치 {len(bad)}건: {bad[:3]}")
    else:
        ok("보유율·보유구간 정합")

    # 귀속축은 반드시 축별불일치율에서 선두여야 한다
    bad = []
    for d in det["norms"]:
        if d["변이축"] != "축귀속변이":
            if d["귀속축"] is not None:
                bad.append(d["norm_id"])
            continue
        rates = {ax: v["rate"] for ax, v in d["축별불일치율"].items() if v}
        if not rates or d["귀속축"] not in rates:
            bad.append(d["norm_id"])
        elif rates[d["귀속축"]] != max(rates.values()):
            bad.append(d["norm_id"])
    if bad:
        fail(f"귀속축이 불일치율 선두가 아님 {len(bad)}건: {bad[:3]}")
    else:
        ok("귀속축 정합")

    if full:
        p = contract["판정파라미터"]
        bad = []
        for d in det["norms"]:
            rates = {ax: v["rate"] for ax, v in d["축별불일치율"].items() if v}
            if d["district_count"] < p["MIN_DISTRICTS"]:
                expect = "지구고유"
            elif len(rates) < 2 or d.get("공통tier") is None:
                expect = "표본부족"
            else:
                top = max(rates, key=rates.get)
                others = sorted(v for ax, v in rates.items() if ax != top)
                mid = others[len(others) // 2] if others else 0.0
                expect = ("축귀속변이" if rates[top] >= p["MIN_RATE"]
                          and rates[top] - mid >= p["DELTA"] else "축무관변이")
            if d["변이축"] != expect:
                bad.append((d["norm_id"], d["변이축"], expect))
        if bad:
            fail(f"변이축 재계산 불일치 {len(bad)}건: {bad[:3]}")
        else:
            ok(f"변이축 재계산 일치 ({len(det['norms'])}건)")

        tiers = {v["tier"] for d in det["norms"] for v in d["축별불일치율"].values() if v}
        bad = []
        for d in det["norms"]:
            used = {v["tier"] for v in d["축별불일치율"].values() if v}
            if len(used) > 1 and d["변이축"] == "축귀속변이":
                bad.append(d["norm_id"])   # tier 가 섞이면 귀속을 판정해서는 안 된다
        if bad:
            fail(f"tier 혼용인데 축귀속변이로 판정됨 {len(bad)}건: {bad[:3]}")
        else:
            ok(f"축별 tier 일관 (사용 tier {sorted(tiers)}, 혼용 시 판정 보류)")

    # ── 갭 ──────────────────────────────────────────────────
    req = set(contract["법정필수호"])
    got = {g["제52조호"] for g in gap["gaps"]}
    if not got <= req:
        fail(f"갭에 법정 필수 아닌 호가 있음: {got - req}")
    else:
        ok(f"법정필수 갭 {len(gap['gaps'])}건 (제{','.join(sorted(got))}호)")
    gap_bad = [g["제52조호"] for g in gap["gaps"]
               if abs(g["coverage"] - g["보유지구수"] / g["분모"]) > 1e-4]
    if gap_bad:
        fail(f"갭 coverage 재계산 불일치 {len(gap_bad)}건: {gap_bad[:3]}")
    if gap["meta"]["갭슬롯수"] != len(gap["gaps"]):
        fail("gap.meta.갭슬롯수 불일치")

    variation = docs["variation.json"]
    unknown_pairs = [p["pair_id"] for p in variation["pairs"] if p["pair_id"] not in pid]
    if unknown_pairs:
        fail(f"variation 의 pair_id 가 pairs 에 없음 {len(unknown_pairs)}건")
    else:
        ok(f"variation 상세 pair_id {len(variation['pairs'])}건 정합")

    print_report()
    if errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
