#!/usr/bin/env python3
"""법령의 시행 이력을 수집하고, 별도로 신선도 관측을 남긴다.

산출물이 둘이며 성격이 다르다. 섞지 않는다.

  statute_history.json          법령별 시행 이력. 법제처 응답 그대로인 **사실**
  _freshness_observations.json  지구번호 연도를 proxy 로 삼은 **관측**

**이 산출물은 적용 법령을 판정하지 않는다.** 지구번호에서 얻는 연도는 지구
지정 연도이며 시행지침의 작성일·고시일·인용일이 아니다. 따라서 관측값을
ArticleVersion/LawApplication 의 `asOf` 로 쓰거나 historical applicable law 로
명명해서는 안 된다. 모든 관측 항목은 `temporal_basis` 와 `적용판본_미확정` 을
스스로 달고 다닌다.

결측을 0 으로 흘리지 않는다. `target=eflaw` 는 법령만 다루므로 자치법규·
행정규칙은 `대상밖` 으로 기록한다(실측 조례 31·훈령 3·고시 2).

입력  output/legal/statute/statute_master.json
      output/legal/statute/statute_citations.json
출력  output/legal/statute/statute_history.json
      output/legal/statute/_freshness_observations.json
"""

import argparse
import bisect
import collections
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import statute_common as sc  # noqa: E402

API = "https://www.law.go.kr/DRF/lawSearch.do"
DIST = re.compile(r"^\d{5}[A-Z]{2}(\d{4})\d{3}$")
PAGE_CAP = 12                     # 100건 × 12 = 1200 판본. 넘으면 절단으로 기록한다

# eflaw 가 다루지 않는 법령 구분. 결측이 아니라 대상 밖이다.
# 정확 일치로 판정한다. 부분 일치를 쓰면 `훈령` 이 `대통령령` 과 얽히고,
# `령` 포함 여부로 거르면 훈령이 대상 안으로 새어 들어온다(실측 3종).
OUT_OF_SCOPE = {"조례", "훈령", "예규", "고시", "지침", "규칙"}


def dist_year(code):
    """지구번호에서 지정 연도를 뽑는다. 지정 연도이지 지침 작성일이 아니다."""
    m = DIST.match(code or "")
    return int(m.group(1)) if m else None


def fetch(query, oc, page, timeout=30, retries=2):
    q = urllib.parse.urlencode({
        "OC": oc, "target": "eflaw", "type": "XML",
        "query": query, "display": "100", "page": str(page),
    })
    for i in range(retries + 1):
        try:
            with urllib.request.urlopen(f"{API}?{q}", timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if i == retries:
                return f"__ERROR__{e}"
            time.sleep(1.5 * (i + 1))
    return "__ERROR__unreachable"


def versions_for(name, law_id, oc, sleep):
    """법령명으로 검색해 같은 법령ID 의 시행 이력만 모은다.

    반환 (판본목록, 절단여부, 오류). 법령명 검색은 시행령·시행규칙까지 걸리므로
    법령ID 로 좁힌다.
    """
    out, page, total, truncated = [], 1, None, False
    while True:
        xml = fetch(name, oc, page)
        if xml.startswith("__ERROR__"):
            return out, truncated, xml[9:60]
        if total is None:
            m = re.search(r"<totalCnt>(\d+)</totalCnt>", xml)
            total = int(m.group(1)) if m else 0
        for b in re.findall(r"<law id=\"\d+\">(.*?)</law>", xml, re.S):
            if sc.xml_tag_text(b, "법령ID") != law_id:
                continue
            out.append({
                "시행일자": sc.iso_yyyymmdd(sc.xml_tag_text(b, "시행일자")),
                "공포일자": sc.iso_yyyymmdd(sc.xml_tag_text(b, "공포일자")),
                "공포번호": sc.xml_tag_text(b, "공포번호"),
                "제개정": sc.xml_tag_text(b, "제개정구분명"),
                "현행연혁": sc.xml_tag_text(b, "현행연혁코드"),
                "MST": sc.xml_tag_text(b, "법령일련번호"),
            })
        if page * 100 >= (total or 0):
            break
        if page >= PAGE_CAP:
            truncated = True
            break
        page += 1
        time.sleep(sleep)
    out.sort(key=lambda v: v["시행일자"] or "9999-99-99")
    return out, truncated, None


def build(master_path, cite_path, out_dir, oc, sleep, limit):
    M = json.loads(Path(master_path).read_text(encoding="utf-8"))
    C = json.loads(Path(cite_path).read_text(encoding="utf-8"))

    # (법령, 지구) 별 인용을 모은다 — 분포를 보존하기 위해 최대값으로 접지 않는다
    per_pair = collections.defaultdict(list)
    for c in C["citations"]:
        for d in c["districts"]:
            per_pair[(c["statute_key"], d)].append(c["citation_id"])

    targets = [s for s in M["statutes"] if s["검증상태"] == "정본대조" and s["법령ID"]]
    if limit:
        targets = targets[:limit]

    hist_rows, obs_rows = [], []
    hstat, ostat = collections.Counter(), collections.Counter()
    version_cache = {}

    for i, s in enumerate(targets, 1):
        key, kind = s["statute_key"], s["법령구분"]
        scope_out = kind in OUT_OF_SCOPE
        if scope_out:
            vs, trunc, err, state = [], False, None, "대상밖"
        else:
            cache_key = (s["정식명칭"], s["법령ID"])
            if cache_key not in version_cache:
                version_cache[cache_key] = versions_for(
                    s["정식명칭"], s["법령ID"], oc, sleep)
            vs, trunc, err = version_cache[cache_key]
            if vs and (err or trunc):
                state = "부분수집"
            elif vs:
                state = "수집"
            else:
                state = "API오류" if err else "결과없음"

        hist_rows.append({
            "statute_key": key,
            "정식명칭": s["정식명칭"],
            "법령ID": s["법령ID"],
            "법령구분": kind,
            "인용수": s["인용수"],
            "수집상태": state,
            "대상밖_사유": (
                "target=eflaw 는 법령(법률·대통령령·부령)만 다룬다. 자치법규(조례)와 "
                "행정규칙(훈령·고시·예규)은 범위 밖이며, 이력이 필요하면 "
                "target=ordin·admrul 쪽 별도 조회가 필요하다" if scope_out else None),
            "절단여부": trunc,
            "버전수": len(vs),
            "최초시행일": vs[0]["시행일자"] if vs else None,
            "최종시행일": vs[-1]["시행일자"] if vs else None,
            "오류": err,
            "versions": vs,
        })
        hstat[state] += 1

        eff = [v["시행일자"] for v in vs if v["시행일자"]]
        for (sk, dno), cids in sorted(per_pair.items()):
            if sk != key:
                continue
            y = dist_year(dno)
            o = {
                "statute_key": key,
                "정식명칭": s["정식명칭"],
                "dstrcAppnNo": dno,
                "지구지정연도": y,
                "인용수": len(cids),
                "citation_ids": cids,
                "temporal_basis": "지구번호연도_proxy",
                "적용판본_미확정": True,
                "이력수집상태": state,
                "이력절단여부": trunc,
                "법령제정전_proxy": None,
                "proxy시점_시행중_판본_시행일": None,
                "proxy이후_개정판본수": None,
                "관측불가_사유": None,
            }
            if state != "수집":
                o["관측불가_사유"] = (
                    "이력이 대상 밖이라 관측할 수 없다" if state == "대상밖"
                    else f"이력 수집 실패({state})")
                ostat["관측불가"] += 1
            elif y is None:
                o["관측불가_사유"] = "지구번호에서 연도를 파싱하지 못했다"
                ostat["관측불가"] += 1
            else:
                idx = bisect.bisect_right(eff, f"{y}-12-31")
                o["법령제정전_proxy"] = idx == 0
                o["proxy시점_시행중_판본_시행일"] = eff[idx - 1] if idx else None
                o["proxy이후_개정판본수"] = max(0, len(eff) - idx)
                ostat["관측" if idx else "관측(법령제정전_proxy)"] += 1
            obs_rows.append(o)

        print(f"  [{i}/{len(targets)}] {s['정식명칭'][:28]:30s} {state:6s} "
              f"버전 {len(vs):3d} / 지구 {sum(1 for k, _ in per_pair if k == key):3d}")
        if not scope_out:
            time.sleep(sleep)

    hist_rows.sort(key=lambda r: (-r["인용수"], r["statute_key"]))
    hist = {
        "meta": {
            "생성근거": "법제처 OPEN API target=eflaw 에서 법령별 시행 이력을 조회",
            "수집일": time.strftime("%Y-%m-%d"),
            "성격": "법제처 응답 그대로인 사실이다. 해석·판정을 담지 않는다",
            "법령수": len(hist_rows),
            "수집상태분포": dict(hstat),
                "수집상태_의미": {
                    "수집": "판본 목록을 받았다",
                    "부분수집": "일부 판본 뒤 API 오류가 났거나 페이지 상한에서 절단됐다. 관측에 쓰지 않는다",
                    "대상밖": "target=eflaw 가 다루지 않는 자치법규·행정규칙이다. 결측이 아니다",
                    "결과없음": "법령이지만 판본이 조회되지 않았다. 확인이 필요하다",
                "API오류": "네트워크·서버 오류. 재시도 대상",
            },
            "법령ID_필터": "법령명 검색은 시행령·시행규칙까지 걸리므로 법령ID 로 좁힌다",
            "절단규약": f"페이지 {PAGE_CAP} (판본 {PAGE_CAP * 100}건) 를 넘으면 절단여부=true 로 기록한다",
            "스크립트": ".claude/skills/legal/legal-statute/scripts/collect_history.py",
        },
        "statutes": hist_rows,
    }

    obs = {
        "meta": {
            "성격": (
                "품질·신선도 **관측**이며 적용 법령 판정이 아니다. "
                "지구에 특정 판본을 귀속시키지 않는다"
            ),
            "temporal_basis": "지구번호연도_proxy",
            "적용판본_미확정": True,
            "proxy_한계": (
                "지구번호에서 얻는 연도는 지구 지정 연도이며 시행지침의 작성일·고시일·"
                "인용일이 아니다. 지구 지정 이후에 지침이 작성되고 이후 변경되기도 한다"
            ),
            "쓰면_안_되는_용도": [
                "ArticleVersion·LawApplication 의 asOf 값으로 쓰는 것",
                "historical applicable law 로 명명하거나 그렇게 질의하는 것",
                "특정 지구에 특정 판본을 귀속시키는 것",
                "proxy이후_개정판본수를 개정 이력의 확정 집계로 인용하는 것",
            ],
            "쓸_수_있는_용도": [
                "근거 조항을 원문에서 다시 확인해야 할 우선순위 판단",
                "현행 조문을 지구에 그대로 귀속시키면 안 되는 정도의 가늠",
            ],
            "관측수": len(obs_rows),
            "관측상태분포": dict(ostat),
            "결측규약": (
                "이력이 대상 밖이거나 수집 실패면 관측값을 null 로 두고 관측불가_사유를 "
                "남긴다. 0 으로 채우지 않는다"
            ),
            "법령제정전_proxy_의미": (
                "지구 지정 연도가 법령의 최초 시행일보다 앞선 경우다. 지침이 지구 지정 "
                "이후에 작성되었다는 증거이며, proxy 를 작성 시점으로 쓸 수 없다는 근거다"
            ),
            "수집일": time.strftime("%Y-%m-%d"),
            "스크립트": ".claude/skills/legal/legal-statute/scripts/collect_history.py",
        },
        "observations": obs_rows,
    }

    od = Path(out_dir)
    od.mkdir(parents=True, exist_ok=True)
    (od / "statute_history.json").write_text(
        json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    (od / "_freshness_observations.json").write_text(
        json.dumps(obs, ensure_ascii=False, indent=2), encoding="utf-8")
    return hist, obs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", default="output/legal/statute/statute_master.json")
    ap.add_argument("--citations", default="output/legal/statute/statute_citations.json")
    ap.add_argument("--out-dir", default="output/legal/statute")
    ap.add_argument("--oc", default="test")
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    for p in (a.master, a.citations):
        if not Path(p).exists():
            print(f"입력 없음: {p}", file=sys.stderr)
            return 1
    hist, obs = build(a.master, a.citations, a.out_dir, a.oc, a.sleep, a.limit)
    print(f"\n법령 {hist['meta']['법령수']}종 이력 → statute_history.json")
    print(f"  수집상태: {hist['meta']['수집상태분포']}")
    print(f"관측 {obs['meta']['관측수']}건 → _freshness_observations.json")
    print(f"  관측상태: {obs['meta']['관측상태분포']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
