#!/usr/bin/env python3
"""지구별 축(facet) 정규화와 대조쌍 생성.

축은 지구를 가르는 요인이다. 대조쌍은 대상축 하나만 다르고 나머지는 같은 지구 두 개다.
쌍의 차이를 대상축에 귀속시킬 수 있어야 변이 명세가 성립하므로, 통제 강도를 Tier 로 기록한다.

출력: output/legal/contrast/facets.json, output/legal/contrast/pairs.json
"""
import csv
import io
import json
import re
import sys
import itertools
import collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
CSV_PATH = ROOT / "output/legal/analysis/시행지침_목차구조_전수조사.csv"
MD_DIR = ROOT / "output/legal/markdown"
OUT_DIR = ROOT / "output/legal/contrast"

# 시기 구간. 경계는 국토계획법 제52조 개정일이다 (2011. 4. 14., 2021. 1. 12.).
# 지구 지정 연도가 어느 조문 판본 아래에 있었는지로 가른다.
PERIODS = [("P1", None, 2011, "~2011 · 제52조 2011.4.14 개정 이전"),
           ("P2", 2012, 2020, "2012~2020 · 2011 개정판 적용기"),
           ("P3", 2021, None, "2021~ · 제52조 2021.1.12 개정 이후")]

# 시행 주체 세분류. 지정 근거법과 별개로 지침 문언 관행을 가르는 요인이다.
ACTOR_RULES = [
    ("LH", "공공", [r"한국토지주택공사"]),
    ("광역공사", "공공", [r"서울주택도시(개발)?공사", r"^SH공사$", r"경기주택도시공사", r"인천도시공사"]),
    ("기초공사", "공공", [r"(김포|시흥|평택|하남)도시공사"]),
    ("지자체", "공공", [r"(특별시|광역시|도)$", r"^경기도$", r"시$", r"군$", r"구청$", r"시장$", r"^의정부$"]),
    ("기타공공", "공공", [r"국방부", r"한국수자원공사", r"^이레일"]),
    ("조합", "민간", [r"조합$"]),
    ("신탁", "민간", [r"신탁"]),
    ("PFV", "민간", [r"피에프브이", r"프로젝트금융투자", r"PFV"]),
]
ACTOR_UNKNOWN = ("불명", "불명", [r"^미지정$", r"^토지개발사업단장?$"])

AXES = ["시기", "자치구", "주체", "근거법령"]
# Tier 는 통제 강도다. A 가 가장 엄격하고 아래로 갈수록 완화해 표본을 넓힌다.
#
# 대상축이 자치구인 경우는 통제축 목록을 뒤집는다. 자치구를 광역으로 "완화" 하면
# 같은 광역 안에서만 비교하게 되어 오히려 통제가 강해지기 때문이다. 이 경우
# A 가 같은 광역 내 비교, B 가 광역 무관 비교다.
TIER_CONTROLS = {
    "A": {"_default": ["시기", "자치구", "주체", "근거법령"],
          "자치구": ["시기", "광역", "주체", "근거법령"]},
    "B": {"_default": ["시기", "광역", "주체", "근거법령"],
          "자치구": ["시기", "주체", "근거법령"]},
    "C": {"_default": ["시기", "주체계열"],
          "주체": ["시기", "광역"]},
}
TIER_DESC = {
    "A": "가장 엄격 — 대상축 외 시기·자치구·주체·근거법령 동일 (자치구축은 같은 광역 내)",
    "B": "자치구를 광역으로 완화 (자치구축은 광역 통제 해제)",
    "C": "시기 구간과 주체 계열만 통제 (주체축은 시기·광역 통제)",
}


def controls_for(axis, tier):
    table = TIER_CONTROLS[tier]
    base = table.get(axis, table["_default"])
    return [c for c in base if c != axis]


def norm_actor(raw):
    s = re.sub(r"[\s()（）㈜]", "", raw or "")
    s = s.replace("주식회사", "").replace("(주)", "")
    for name, family, pats in [ACTOR_UNKNOWN] + ACTOR_RULES:
        for p in pats:
            if re.search(p, s):
                return name, family
    return "민간법인", "민간"


def period(year):
    for code, lo, hi, label in PERIODS:
        if (lo is None or year >= lo) and (hi is None or year <= hi):
            return code, label
    return "미상", "지정연도 파싱 실패"


def parse_dno(dno):
    """지구번호 14자리 = 시군구코드 5 + 사업구분 2 + 지정연도 4 + 일련 3."""
    m = re.match(r"^(\d{5})([A-Z]{2})(\d{4})(\d{3})$", dno)
    if not m:
        return None
    return {"sgg": m.group(1), "code": m.group(2), "year": int(m.group(3))}


def load_frontmatter():
    fm = {}
    for f in sorted(MD_DIR.glob("*/*.md")):
        txt = f.read_text(encoding="utf-8", errors="replace")
        m = re.match(r"^---\n(.*?)\n---\n", txt, re.S)
        if not m:
            continue
        head = m.group(1)
        d = {}
        for key in ("지구번호", "사업단계", "면적", "표", "조문수"):
            mm = re.search(rf"^{key}:\s*\"?([^\"\n]*)\"?\s*$", head, re.M)
            if mm:
                d[key] = mm.group(1).strip()
        if "지구번호" in d:
            fm[d["지구번호"]] = d
    return fm


def area_band(raw):
    try:
        v = float(re.sub(r"[^\d.]", "", raw or ""))
    except ValueError:
        return "미상"
    if v <= 0:
        return "미상"
    if v < 100_000:
        return "S(<10만㎡)"
    if v < 500_000:
        return "M(10~50만㎡)"
    if v < 2_000_000:
        return "L(50~200만㎡)"
    return "XL(200만㎡~)"


def build_facets():
    rows = list(csv.DictReader(io.open(CSV_PATH, encoding="utf-8-sig")))
    fm = load_frontmatter()
    districts, malformed = [], []
    for r in rows:
        dno = r["지구번호"].strip()
        p = parse_dno(dno)
        if not p:
            malformed.append(dno)
            continue
        actor, family = norm_actor(r["시행자"])
        pcode, plabel = period(p["year"])
        extra = fm.get(dno, {})
        districts.append({
            "지구번호": dno,
            "지구명": r["지구명"],
            "시기": pcode,
            "시기라벨": plabel,
            "지정연도": p["year"],
            "자치구": p["sgg"],
            "광역": r["지역"],
            "주체": actor,
            "주체계열": family,
            "시행자원문": r["시행자"],
            "근거법령": r["근거법령"],
            "사업구분코드": p["code"],
            "사업단계": extra.get("사업단계", "미상"),
            "면적구간": area_band(extra.get("면적")),
            "표마커수": int(r["표마커"] or 0),
            "구조유형": r["유형"],
            "구조신뢰도": r["신뢰도"],
        })
    districts.sort(key=lambda d: d["지구번호"])
    return districts, malformed


def build_pairs(districts):
    """대상축 하나만 다르고 통제축은 모두 같은 쌍을 Tier 별로 생성한다.

    한 쌍이 여러 Tier 를 만족하면 가장 엄격한 Tier 하나만 남긴다. 느슨한 Tier 가
    같은 쌍을 다시 세면 표본 수가 부풀려져 귀속 판정이 왜곡된다.
    """
    pairs, seen = [], {}

    # 기준선. 4축이 모두 같은 지구 쌍이다. 같은 조건인데도 규범이 갈린다면 그것은 축
    # 효과가 아니라 지구 재량이거나 추출 잡음이다. 축 귀속은 이 값과 견줘 판정한다.
    base_ctrl = ["시기", "자치구", "주체", "근거법령"]
    buckets = collections.defaultdict(list)
    for d in districts:
        buckets[tuple(d[c] for c in base_ctrl)].append(d)
    for key, grp in buckets.items():
        for a, b in itertools.combinations(sorted(grp, key=lambda x: x["지구번호"]), 2):
            pairs.append({
                "pair_id": f"동일:{a['지구번호']}:{b['지구번호']}",
                "대상축": "동일",
                "tier": "BASE",
                "tier설명": "기준선 — 4축이 모두 같은 쌍. 축 귀속 판정의 대조군",
                "통제축": base_ctrl,
                "통제값": dict(zip(base_ctrl, key)),
                "A": a["지구번호"], "B": b["지구번호"],
                "A값": "동일", "B값": "동일",
            })

    for tier in ("A", "B", "C"):
        for axis in AXES:
            ctrl = controls_for(axis, tier)
            buckets = collections.defaultdict(list)
            for d in districts:
                buckets[tuple(d[c] for c in ctrl)].append(d)
            for key, grp in buckets.items():
                for a, b in itertools.combinations(sorted(grp, key=lambda x: x["지구번호"]), 2):
                    if a[axis] == b[axis]:
                        continue
                    pid = (axis, a["지구번호"], b["지구번호"])
                    if pid in seen:
                        continue
                    seen[pid] = tier
                    pairs.append({
                        "pair_id": f"{axis}:{a['지구번호']}:{b['지구번호']}",
                        "대상축": axis,
                        "tier": tier,
                        "tier설명": TIER_DESC[tier],
                        "통제축": ctrl,
                        "통제값": dict(zip(ctrl, key)),
                        "A": a["지구번호"],
                        "B": b["지구번호"],
                        "A값": a[axis],
                        "B값": b[axis],
                    })
    return pairs


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    districts, malformed = build_facets()
    pairs = build_pairs(districts)

    axis_dist = {ax: dict(collections.Counter(d[ax] for d in districts)) for ax in
                 ["시기", "주체", "주체계열", "근거법령", "광역", "면적구간", "사업단계"]}
    tier_dist = collections.Counter((p["tier"], p["대상축"]) for p in pairs)

    facets = {
        "meta": {
            "생성스크립트": "scripts/build_facets.py",
            "지구수": len(districts),
            "지구번호_파싱실패": malformed,
            "자치구수": len(set(d["자치구"] for d in districts)),
            "시기구간근거": "국토계획법 제52조 개정일 2011.4.14 · 2021.1.12 을 경계로 삼는다",
            "축분포": axis_dist,
        },
        "districts": districts,
    }
    (OUT_DIR / "facets.json").write_text(
        json.dumps(facets, ensure_ascii=False, indent=1), encoding="utf-8")

    pairs_out = {
        "meta": {
            "생성스크립트": "scripts/build_facets.py",
            "쌍수": len(pairs),
            "대상축": AXES,
            "tier정의": [{"tier": t, "설명": TIER_DESC[t],
                        "대상축별_통제축": {ax: controls_for(ax, t) for ax in AXES}}
                       for t in ("A", "B", "C")],
            "중복제거": "같은 (대상축, A, B) 조합은 가장 엄격한 tier 하나만 남긴다",
            "tier별_대상축별_쌍수": {f"{t}/{a}": n for (t, a), n in sorted(tier_dist.items())},
        },
        "pairs": pairs,
    }
    (OUT_DIR / "pairs.json").write_text(
        json.dumps(pairs_out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"지구 {len(districts)} · 파싱실패 {len(malformed)}")
    for ax, c in axis_dist.items():
        print(f"  {ax}: {dict(sorted(c.items(), key=lambda x: -x[1]))}")
    print(f"대조쌍 {len(pairs)}")
    for (t, a), n in sorted(tier_dist.items()):
        print(f"  Tier {t} / {a}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
