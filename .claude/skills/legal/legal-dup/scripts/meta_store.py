"""통합 meta.json 의 로드·병합·집계.

collect.py 와 verify_contract.py 가 함께 쓴다. 지구 정보는 지구마다 흩어진
meta.json 이 아니라 `output/legal/시행지침/meta.json` 한 곳에 모인다.

`districts[]` 항목은 택지정보시스템 API 원본 필드를 그대로 보존한다. 래퍼
(schemaVersion·generatedAt·summary)만 이 프로젝트가 정의한 것이다. 원본을
변형하지 않으므로 지구 단위 스키마는 개별 파일 시절 계약을 그대로 쓴다.
"""

import json
import os
from datetime import datetime

SCHEMA_VERSION = 1
STORE_NAME = "meta.json"

# 계약의 region enum 순서. 정렬을 이 순서에 고정해야 재생성이 멱등하다.
REGION_ORDER = ["서울", "인천", "경기"]


def store_path(base):
    """base 는 output/legal/시행지침 절대경로."""
    return os.path.join(base, STORE_NAME)


def load(base):
    """통합 파일을 읽는다. 없으면 빈 구조를 준다 — 최초 수집도 같은 경로를 탄다."""
    path = store_path(base)
    if not os.path.exists(path):
        return {"schemaVersion": SCHEMA_VERSION, "districtCount": 0,
                "summary": {}, "districts": []}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def districts(store):
    return store.get("districts", [])


def index_by_appn(store):
    """지구번호 → 지구. dstrcAppnNo 는 190건 전수에서 고유하다."""
    return {d["dstrcAppnNo"]: d for d in districts(store)}


def sort_key(d):
    """region 은 계약 enum 순서, 그 안에서는 지구명 가나다순."""
    region = d.get("region", "")
    rank = REGION_ORDER.index(region) if region in REGION_ORDER else len(REGION_ORDER)
    return (rank, region, (d.get("dstrcNm") or "").strip())


def _area_sum(items):
    """ar 은 "548,239.7" 꼴의 문자열이다. 합계는 참고값이므로 파싱 실패는 건너뛴다."""
    total = 0.0
    for d in items:
        try:
            total += float(str(d.get("ar", "")).replace(",", ""))
        except ValueError:
            continue
    return round(total, 1)


def _tally(items, key):
    """값별 건수. 많은 순으로 정렬해 개괄에서 바로 읽히게 한다."""
    counts = {}
    for d in items:
        v = d.get(key)
        if v is None:
            continue
        counts[v] = counts.get(v, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def summarize(items):
    """사업 개괄. 지구 배열에서 매번 다시 계산하므로 본문과 어긋날 수 없다."""
    collected = [d["collectedAt"] for d in items if d.get("collectedAt")]
    downloaded = sum(len(d.get("downloaded", [])) for d in items)
    return {
        "byRegion": _tally(items, "region"),
        "byLaword": _tally(items, "lawordNm"),
        "byStep": _tally(items, "stepNm"),
        "byNewtown": _tally(items, "newtownNm"),
        "totalAreaSqm": _area_sum(items),
        "downloadedFiles": downloaded,
        "collectedRange": {"first": min(collected), "last": max(collected)}
        if collected else None,
    }


def upsert(store, incoming):
    """지구번호 단위로 교체·추가한다.

    region 통째로 갈아끼우지 않는 이유: fetch 는 --file-type 에 걸린 지구만
    대상으로 삼는다. region 을 비우면 다른 file-type 으로 받아 둔 지구가
    사라진다.
    """
    by_no = {d["dstrcAppnNo"]: d for d in districts(store)}
    for d in incoming:
        by_no[d["dstrcAppnNo"]] = d
    store["districts"] = sorted(by_no.values(), key=sort_key)
    return store


def save(base, store):
    """정렬·집계를 다시 맞춰 기록한다. 부분 기록이 남지 않도록 임시 파일을 거친다."""
    items = sorted(districts(store), key=sort_key)
    store["schemaVersion"] = SCHEMA_VERSION
    store["generatedAt"] = datetime.now().isoformat()
    store["districtCount"] = len(items)
    store["summary"] = summarize(items)
    store["districts"] = items

    path = store_path(base)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(store, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path
