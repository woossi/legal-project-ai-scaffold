#!/usr/bin/env python3
"""대조쌍으로 변이를 축에 귀속시키고 결정론 등급을 확정한다.

등급은 두 축의 교차다. 하나로 합치면 "법정 필수인데 값은 지구마다 다른 것"(건폐율·
용적률이 그렇다)을 놓는 자리가 없어진다.

  세로축 — 법령 근거: 제52조 각 호 매핑(1차) · 본문 인용 법령(2차) · 없음
  가로축 — 문언 변이: 불변 · 축 귀속 변이 · 귀속 불명

축 귀속은 기준선과 견줘 판정한다. 4축이 모두 같은 쌍(BASE)에서도 갈리는 규범이라면
그 차이는 축이 만든 것이 아니라 지구 재량이거나 추출 잡음이다.

출력: output/legal/contrast/{variation,determinism,_extraction_gap}.json
"""
import json
import re
import sys
import collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
SKILL = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output/legal/contrast"

MIN_PAIRS = 10         # 축 판정에 필요한 최소 유효 쌍 수. 미만이면 표본 부족으로 보류한다
MIN_DISTRICTS = 5      # 이보다 적은 지구에만 있는 규범은 축 분석 대상이 아니다
DELTA = 0.10           # 선두 축이 나머지 축의 중앙값보다 이만큼 높아야 그 축에 귀속시킨다
MIN_RATE = 0.50        # 불일치율 자체가 이 값 이상이어야 한다
REQUIRED_HO = ("2", "4")   # 제52조제1항 법정 필수 호
# 보유율 구간. 실측상 최다 규범이 144/181(0.80) 이므로 전 지구 보편 규범은 없다.
# 임계를 낮춰 "불변" 을 만들어내지 않고 구간을 그대로 기록한다.
BANDS = [("준보편", 0.75), ("다수", 0.50), ("소수", 0.25), ("희소", 0.0)]
AXES = ["시기", "자치구", "주체", "근거법령"]
LAW_RE = re.compile(r"[「｢『]\s*([^」｣』]{2,40}?)\s*[」｣』]")


def load(name):
    return json.loads((OUT_DIR / name).read_text(encoding="utf-8"))


def slot_basis(mapping):
    exact = mapping["slot_exact"]
    pats = [(p["호"], re.compile(p["pattern"])) for p in mapping["slot_patterns"]]

    def judge(slot):
        if slot in exact:
            return exact[slot], "slot_exact"
        for ho, rx in pats:
            if rx.search(slot):
                return ho, "slot_pattern"
        return None, None
    return judge


def main():
    facets = load("facets.json")
    pairs = load("pairs.json")["pairs"]
    slots = load("slots.json")
    norms = load("norms.json")
    mapping = json.loads((SKILL / "case/제52조-슬롯매핑.json").read_text(encoding="utf-8"))
    judge = slot_basis(mapping)
    ho_meta = mapping["호"]

    # 지구를 비트로 다룬다. 쌍마다 집합 연산을 반복하므로 비트 검사가 가장 싸다.
    dnos = sorted(d["지구번호"] for d in facets["districts"])
    bit = {d: 1 << i for i, d in enumerate(dnos)}
    norm_docs = set()
    for n in norms["norms"]:
        norm_docs.update(n["districts"])

    # 규범 문장이 하나도 안 잡힌 문서는 비교 대상이 아니다. 분모에서 뺀다.
    usable = {d for d in dnos if d in norm_docs}
    by_axis = collections.defaultdict(lambda: collections.defaultdict(list))
    for p in pairs:
        if p["A"] in usable and p["B"] in usable:
            by_axis[p["대상축"]][p["tier"]].append((bit[p["A"]], bit[p["B"]]))

    base_pairs = by_axis["동일"]["BASE"]

    def disagree_rate(mask, plist):
        """둘 중 하나만 보유한 쌍의 비율. 둘 다 미보유인 쌍은 규범과 무관하므로 뺀다.

        비율에 라플라스 평활을 건다. 주체축 Tier A 는 유효 쌍이 열 몇 개뿐이라
        평활 없이는 한두 쌍 차이로 불일치율이 1.0 이 되어 표본이 작은 축이
        무조건 선택된다.
        """
        eff = dis = 0
        for a, b in plist:
            ha, hb = bool(mask & a), bool(mask & b)
            if not ha and not hb:
                continue
            eff += 1
            if ha != hb:
                dis += 1
        return ((dis + 1) / (eff + 2) if eff else 0.0), eff

    # ── 슬롯 근거 판정 ──────────────────────────────────────────────
    slot_out = []
    D_slot = slots["meta"]["조문표제_보유문서수"]
    ho_districts = collections.defaultdict(set)      # 호 → 그 호의 슬롯을 하나라도 가진 지구
    slot_district_set = set()
    for s in slots["slots"]:
        ho, how = judge(s["slot"])
        slot_out.append({"slot": s["slot"], "district_count": s["district_count"],
                         "coverage": s["coverage"], "제52조호": ho, "판정경로": how,
                         "법정필수": bool(ho and ho_meta.get(ho, {}).get("필수"))})
        slot_district_set.update(s["districts"])
        if ho:
            ho_districts[ho].update(s["districts"])
    slot_ho = {r["slot"]: r["제52조호"] for r in slot_out}

    # 갭은 호 수준에서 본다. 제2호·제4호가 법정 필수라는 것은 그 "내용" 이 있어야
    # 한다는 뜻이지, 특정 슬롯명이 전 지구에 있어야 한다는 뜻이 아니다. 슬롯 단위로
    # 세면 1개 지구에만 있는 세부 조문이 전부 누락으로 잡힌다.
    gap = []
    for ho in REQUIRED_HO:
        have = ho_districts.get(ho, set())
        missing = sorted(slot_district_set - have)
        if missing:
            gap.append({
                "제52조호": ho,
                "법문": ho_meta[ho]["법문"],
                "보유지구수": len(have), "분모": D_slot,
                "미검출지구수": len(missing),
                "coverage": round(len(have) / D_slot, 4) if D_slot else 0.0,
                "미검출지구": missing,
                "해석": "제52조 법정 필수사항이므로 전 지구에 존재해야 한다. "
                      "미검출분은 지구가 규정을 두지 않은 것이 아니라 추출 실패로 본다",
            })
    gap.sort(key=lambda g: -g["미검출지구수"])

    # ── 규범별 축 귀속과 결정론 등급 ─────────────────────────────────
    U = len(usable)
    det, axis_tally = [], collections.Counter()
    for n in norms["norms"]:
        mask = 0
        for d in n["districts"]:
            mask |= bit.get(d, 0)
        cnt = len(n["districts"])
        ratio = cnt / U if U else 0.0

        # 근거 1차: 이 규범이 놓인 슬롯의 제52조 호. 가장 많이 놓인 슬롯을 쓴다.
        # 총칙이 아닌 호를 우선하면, 여러 슬롯에 걸친 총칙 조항이 어쩌다 걸린 실질
        # 호로 끌려간다 — "부칙 … 효력을 발생한다" 가 제8호가 되는 식이다.
        ho, path = None, None
        for sl in n["slots"]:
            if sl["slot"] == "_미귀속":
                continue
            h = slot_ho.get(sl["slot"])
            if h:
                ho, path = h, "슬롯매핑"
                break
        cited = sorted(set(LAW_RE.findall(n["representative"])))
        if ho is None and cited:
            ho, path = "인용", "본문인용"          # 근거 2차
        basis = ("법령근거" if ho and ho not in ("총칙", None) else
                 "지침총칙" if ho == "총칙" else "근거없음")

        # 가로축
        base_rate, base_eff = disagree_rate(mask, base_pairs)

        # 축마다 다른 tier 를 쓰면 통제 강도가 달라 delta 를 견줄 수 없다.
        # 네 축이 동시에 최소 유효 쌍을 넘기는 가장 엄격한 tier 하나를 골라 함께 쓴다.
        chosen, axis_res = None, {}
        for tier in ("A", "B", "C"):
            trial = {}
            for ax in AXES:
                r, eff = disagree_rate(mask, by_axis[ax].get(tier, []))
                trial[ax] = {"rate": round(r, 3), "pairs": eff, "tier": tier,
                             "delta": round(r - base_rate, 3)}
            if all(v["pairs"] >= MIN_PAIRS for v in trial.values()):
                chosen, axis_res = tier, trial
                break
        if chosen is None:
            # 어느 tier 로도 네 축을 함께 채우지 못하면 축별 최선 tier 를 기록만 하고
            # 귀속은 판정하지 않는다. 통제 강도가 다른 값끼리 견주면 결론이 tier 차이를
            # 축 효과로 잘못 읽는다.
            for ax in AXES:
                axis_res[ax] = None
                for tier in ("A", "B", "C"):
                    r, eff = disagree_rate(mask, by_axis[ax].get(tier, []))
                    if eff >= MIN_PAIRS:
                        axis_res[ax] = {"rate": round(r, 3), "pairs": eff, "tier": tier,
                                        "delta": round(r - base_rate, 3)}
                        break

        # 귀속은 축끼리 견줘 판정한다. 기준선(BASE)은 51쌍뿐이라 규범별로 쪼개면
        # 유효 쌍이 한 자릿수로 떨어져 판정 근거가 되지 못한다. 참고값으로만 남긴다.
        rates = {ax: v["rate"] for ax, v in axis_res.items() if v}
        if cnt < MIN_DISTRICTS:
            variation, owner = "지구고유", None
        elif len(rates) < 2 or chosen is None:
            variation, owner = "표본부족", None
        else:
            top = max(rates, key=rates.get)
            others = sorted(v for ax, v in rates.items() if ax != top)
            mid = others[len(others) // 2] if others else 0.0
            if rates[top] >= MIN_RATE and rates[top] - mid >= DELTA:
                variation, owner = "축귀속변이", top
                axis_tally[top] += 1
            else:
                # 어느 축에서나 비슷하게 갈린다. 축이 아니라 지구 재량이 만든 차이다.
                variation, owner = "축무관변이", None

        det.append({
            "norm_id": n["norm_id"],
            "representative": n["representative"],
            "district_count": cnt,
            "보유율": round(ratio, 4),
            "보유구간": next(b for b, lo in BANDS if ratio >= lo),
            "strength": n["strength"],
            "slot": n["slots"][0]["slot"] if n["slots"] else "_미귀속",
            "slot_dispersion": n["slot_dispersion"],
            "제52조호": ho,
            "근거판정경로": path,
            "인용법령": cited,
            "근거축": basis,
            "변이축": variation,
            "귀속축": owner,
            "기준선불일치율": round(base_rate, 3),
            "기준선유효쌍": base_eff,
            "공통tier": chosen,
            "축별불일치율": axis_res,
            "문언변이": n["문언변이"],
            "variant_count": n["variant_count"],
        })

    det.sort(key=lambda x: (-x["district_count"], x["norm_id"]))
    cross = collections.Counter((d["근거축"], d["변이축"]) for d in det)
    band = collections.Counter(d["보유구간"] for d in det)

    # 판정 결과에는 분석 대상만 싣는다. 지구고유 규범 다수를 그대로 담으면 파일이
    # 40MB를 넘어 다루기 어렵고, 내용은 norms.json 에 norm_id 로 그대로 있다.
    analyzed = [d for d in det if d["district_count"] >= MIN_DISTRICTS]
    excluded = collections.Counter(
        (d["근거축"], d["변이축"]) for d in det if d["district_count"] < MIN_DISTRICTS)

    (OUT_DIR / "determinism.json").write_text(json.dumps({
        "meta": {
            "생성스크립트": "scripts/build_contrast.py",
            "판정단위": "규범 문장 군집",
            "모수": U,
            "모수정의": "규범 문장이 하나 이상 추출된 지구. 추출 실패 지구는 분모에서 뺀다",
            "세로축_법령근거": {
                "법령근거": "슬롯이 제52조 각 호에 매핑되거나(1차) 본문에 법령 인용이 있음(2차)",
                "지침총칙": "제52조 각 호가 아닌 지침 운용 조항. 법정 필수가 아니다",
                "근거없음": "매핑도 인용도 없음",
            },
            "가로축_문언변이": {
                "축귀속변이": f"선두 축의 불일치율이 {MIN_RATE} 이상이고 나머지 축 중앙값보다 {DELTA} 이상 높음",
                "축무관변이": "어느 축에서나 비슷하게 갈림. 축이 아니라 지구 재량이 만든 차이",
                "지구고유": f"{MIN_DISTRICTS}개 미만 지구에만 존재. 축 분석 대상이 아니다",
                "표본부족": "네 축이 같은 tier 로 최소 유효 쌍을 함께 채우지 못함. "
                        "통제 강도가 다른 값끼리 견주면 tier 차이를 축 효과로 오독한다",
            },
            "보유구간": {b: f"보유율 {lo} 이상" for b, lo in BANDS},
            "보유율_주의": "실측 최다 규범이 144/181(0.796)이다. 전 지구 보편 규범은 없으므로 "
                       "임계를 낮춰 '불변' 등급을 만들어내지 않고 구간을 그대로 기록한다",
            "축귀속_판정기준": {
                "비교방식": "축끼리 견준다. 기준선(BASE)은 51쌍뿐이라 규범별로 쪼개면 유효 쌍이 "
                        "한 자릿수가 되어 판정 근거가 되지 못한다. 참고값으로만 남긴다",
                "최소유효쌍": MIN_PAIRS,
                "tier선택": "네 축이 동시에 최소 유효 쌍을 넘기는 가장 엄격한 tier 하나를 함께 쓴다. "
                        "축마다 tier가 다르면 통제 강도가 달라 비교가 성립하지 않는다",
                "유효쌍정의": "둘 중 하나라도 그 규범을 보유한 쌍. 둘 다 미보유면 규범과 무관하므로 뺀다",
                "평활": "불일치율에 라플라스 평활 (dis+1)/(eff+2). 표본이 작은 축이 극단값으로 "
                      "선택되는 것을 막는다",
            },
            "교차표": {f"{a} × {b}": c for (a, b), c in sorted(cross.items())},
            "귀속축_분포": dict(axis_tally),
            "보유구간_분포": dict(band),
            "규범군집수_전체": len(det),
            "수록범위": f"{MIN_DISTRICTS}개 이상 지구에 있는 규범만 싣는다. 나머지 "
                    f"{len(det) - len(analyzed)}건은 지구고유이며 norms.json 에 norm_id 로 있다",
            "미수록_교차표": {f"{a} × {b}": c for (a, b), c in sorted(excluded.items())},
        },
        "norms": analyzed}, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 쌍별 변이 ────────────────────────────────────────────────
    norm_bits = []
    for n in norms["norms"]:
        m = 0
        for d in n["districts"]:
            m |= bit.get(d, 0)
        norm_bits.append(m)
    slot_of_district = collections.defaultdict(set)
    for s in slots["slots"]:
        for d in s["districts"]:
            slot_of_district[d].add(s["slot"])
    struct = {d["지구번호"]: d for d in facets["districts"]}

    detail, agg = [], collections.defaultdict(lambda: collections.Counter())
    for p in pairs:
        a, b = p["A"], p["B"]
        if a not in usable or b not in usable:
            continue
        ba, bb = bit[a], bit[b]
        shared = onlyA = onlyB = 0
        for m in norm_bits:
            ha, hb = bool(m & ba), bool(m & bb)
            if ha and hb:
                shared += 1
            elif ha:
                onlyA += 1
            elif hb:
                onlyB += 1
        tot = shared + onlyA + onlyB
        sa, sb = slot_of_district[a], slot_of_district[b]
        jac = len(sa & sb) / len(sa | sb) if (sa or sb) else 0.0
        row = {
            "pair_id": p["pair_id"], "대상축": p["대상축"], "tier": p["tier"],
            "A값": p["A값"], "B값": p["B값"],
            "공유규범": shared, "A전용": onlyA, "B전용": onlyB,
            "규범일치율": round(shared / tot, 4) if tot else 0.0,
            "슬롯Jaccard": round(jac, 4),
            "구조유형일치": struct[a]["구조유형"] == struct[b]["구조유형"],
        }
        k = f"{p['대상축']}/{p['tier']}"
        agg[k]["쌍수"] += 1
        agg[k]["공유규범합"] += shared
        agg[k]["일치율합"] += row["규범일치율"] * 1000
        agg[k]["슬롯Jaccard합"] += jac * 1000
        agg[k]["구조유형일치"] += int(row["구조유형일치"])
        if p["tier"] in ("A", "BASE"):
            detail.append(row)

    summary = {}
    for k, c in sorted(agg.items()):
        n_ = c["쌍수"]
        summary[k] = {"쌍수": n_,
                      "평균규범일치율": round(c["일치율합"] / 1000 / n_, 4),
                      "평균슬롯Jaccard": round(c["슬롯Jaccard합"] / 1000 / n_, 4),
                      "구조유형일치율": round(c["구조유형일치"] / n_, 4)}

    (OUT_DIR / "variation.json").write_text(json.dumps({
        "meta": {
            "생성스크립트": "scripts/build_contrast.py",
            "쌍수_전체": sum(c["쌍수"] for c in agg.values()),
            "상세저장범위": "Tier A 와 BASE 만 쌍별로 저장한다. B·C 는 축별 집계만 남긴다",
            "집계": summary,
            "읽는법": "평균규범일치율이 기준선(동일/BASE)보다 낮은 축이 변이를 만드는 축이다",
        },
        "pairs": detail}, ensure_ascii=False, indent=1), encoding="utf-8")

    (OUT_DIR / "_extraction_gap.json").write_text(json.dumps({
        "meta": {
            "생성스크립트": "scripts/build_contrast.py",
            "근거": "case/제52조-슬롯매핑.json — 제2호·제4호는 법정 필수이므로 "
                  "미검출은 추출 실패 후보로 본다",
            "분모": D_slot,
            "분모정의": "조문 표제가 하나 이상 추출된 지구",
            "갭슬롯수": len(gap),
        },
        "gaps": gap}, ensure_ascii=False, indent=1), encoding="utf-8")

    (OUT_DIR / "slots.json").write_text(json.dumps({
        "meta": {**slots["meta"], "근거판정": "case/제52조-슬롯매핑.json 으로 제52조 각 호에 매핑"},
        "slots": [{**s, **{k: v for k, v in r.items() if k not in ("slot", "district_count", "coverage")}}
                  for s, r in zip(slots["slots"], slot_out)]}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    print(f"모수 {U} · 규범 {len(det)} (수록 {len(analyzed)}) · 슬롯 {len(slot_out)}")
    print(f"보유구간: {dict(band)}")
    print("교차표 (근거축 × 변이축):")
    for (a, b), c in sorted(cross.items(), key=lambda x: -x[1]):
        print(f"  {a:6s} × {b:6s} : {c}")
    print(f"귀속축 분포: {dict(axis_tally)}")
    print("쌍 집계:")
    for k, v in summary.items():
        print(f"  {k:14s} 쌍{v['쌍수']:6d}  규범일치 {v['평균규범일치율']:.3f}  "
              f"슬롯J {v['평균슬롯Jaccard']:.3f}  구조일치 {v['구조유형일치율']:.3f}")
    print("법정필수 호 커버리지:")
    for ho in REQUIRED_HO:
        have = len(ho_districts.get(ho, set()))
        print(f"  제{ho}호 {ho_meta[ho]['법문'][:30]}: {have}/{D_slot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
