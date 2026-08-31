#!/usr/bin/env python3
"""택지정보시스템(map.jigu.go.kr) 지구단위계획시행지침 수집기.

3단계 체인:
  ① POST map.jigu.go.kr/search/moreResultDstrc.json  → 지구 목록
  ② POST map.jigu.go.kr/dstrc/dstrcInfo.do           → 첨부 코드 (gubun=detailInfo)
  ③ GET  openapi.jigu.go.kr/file/dstrcFileDownload.json → 파일 원문

차단 방지: 순차 실행 전용. 요청 간 1.3초, 다운로드 간 2초, 지수 백오프 재시도.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import meta_store

MAP_HOST = "https://map.jigu.go.kr"
FILE_HOST = "https://openapi.jigu.go.kr/file"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HDRS = {"User-Agent": UA, "Referer": MAP_HOST + "/map.do",
        "X-Requested-With": "XMLHttpRequest"}

REGIONS = {"서울": "1100000000", "인천": "2800000000", "경기": "4100000000"}
FILE_LABELS = {"2": "위치도", "3": "광역교통계획도", "4": "토지이용계획도",
               "5": "지구단위계획도", "7": "지구단위계획시행지침"}

# 저장 파일명의 어간. 첨부 종류마다 달라야 한 지구 디렉터리 안에서 서로
# 덮어쓰지 않는다 — 도면과 시행지침이 둘 다 .pdf 로 오는 일이 실제로 있다.
# 코드 7 은 190건을 이미 수집했고 legal-textreform·tables.csv 의 출처문서가
# 그 이름을 참조한다. 표시용 FILE_LABELS 와 달리 이 값은 바꾸면 안 된다.
ASSET_STEM = "지구단위계획 시행지침"
ASSET_STEMS = {
    "2": "위치도",
    "3": "광역교통계획도",
    "4": "토지이용계획도",
    "5": "지구단위계획도 (가구 및 획지경계도)",
    "7": ASSET_STEM,
}

REQ_DELAY = 1.3      # 목록/상세 호출 간격
DL_DELAY = 2.0       # 다운로드 간격
MAX_RETRY = 3

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
OUT_BASE = os.path.join(ROOT, "output", "legal", "시행지침")


def _open(req, timeout):
    """지수 백오프 재시도. 즉시 재시도 금지."""
    last = None
    for attempt in range(MAX_RETRY):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            if isinstance(e, urllib.error.HTTPError) and e.code in (403, 429):
                print(f"    [!] HTTP {e.code} — 차단 가능성. 60초 대기", file=sys.stderr)
                time.sleep(60)
            else:
                time.sleep(3 * (attempt + 1))
    raise last


def post(path, data, timeout=30):
    req = urllib.request.Request(
        MAP_HOST + path, data=urllib.parse.urlencode(data).encode(), headers=HDRS)
    with _open(req, timeout) as f:
        return f.read().decode("utf-8", "replace")


def list_dstrc(ctprvn):
    """지구 목록 전체 페이지 순회.

    주의: 이 API는 마지막 페이지를 넘어가도 nextPageNum을 계속 증가시켜 반환한다
    (list만 빈 배열). nextPageNum만 믿고 돌면 무한루프다. 빈 페이지·cnt 도달·
    중복 dstrcAppnNo를 모두 종료 조건으로 둔다.
    """
    page, out, seen = 1, [], set()
    total = None
    while True:
        body = post("/search/moreResultDstrc.json", {
            "pageNum": page, "scCtprvn": ctprvn, "scSigngu": 0,
            "scDstrcGubun": "ALL", "scStepCode": "ALL", "scBldlndSeCode": "ALL",
            "scOpertnGroup": "ALL", "scLawordCode": "ALL", "scSearchDstrcNm": ""})
        j = json.loads(body)
        rows = j.get("list", [])
        if total is None:
            total = j.get("cnt")
        fresh = [r for r in rows if r.get("dstrcAppnNo") not in seen]
        seen.update(r.get("dstrcAppnNo") for r in fresh)
        out += fresh
        print(f"  목록 p{page}: +{len(fresh)} 누적 {len(out)}/{total or '?'}")
        if not rows or not fresh:          # 빈 페이지 또는 전부 중복 → 끝
            break
        if total and len(out) >= int(total):
            break
        nxt = j.get("nextPageNum")
        if not nxt:
            break
        page = nxt
        time.sleep(REQ_DELAY)
    return out


ATTACH_RE = re.compile(r"fnFileDownload\('([^']*)','([^']*)'\);\">\s*([^<]+)<")


def list_attachments(dstrc_appn_no):
    """지구 상세에서 첨부 목록 파싱. gubun=detailInfo 필수."""
    html = post("/dstrc/dstrcInfo.do",
                {"dstrcAppnNo": dstrc_appn_no, "gubun": "detailInfo"})
    return [{"fileCode": c, "fileRegistNo": r, "label": re.sub(r"\s+", " ", l).strip()}
            for c, r, l in ATTACH_RE.findall(html)]


def safe_name(name):
    """경로 구분자·제어문자만 치환. 나머지는 서버 제공 이름 보존."""
    name = re.sub(r"[\x00-\x1f/\\]", "_", name).strip()
    return name or "download.bin"


def atomic_write_json(path, payload):
    """기존 JSON을 보존한 채 임시 파일을 완성한 뒤 교체한다."""
    directory = os.path.dirname(path)
    temporary = os.path.join(
        directory, f".{os.path.basename(path)}.part-{os.getpid()}"
    )
    try:
        with open(temporary, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def load_complete_index(path):
    """상세 조회를 전건 완료한 인덱스만 fetch 입력으로 허용한다."""
    with open(path, encoding="utf-8") as fh:
        index = json.load(fh)
    total = index.get("total")
    indexed = index.get("indexed")
    items = index.get("items")
    if (not isinstance(total, int) or isinstance(total, bool)
            or not isinstance(indexed, int) or isinstance(indexed, bool)
            or not isinstance(items, list)
            or indexed != total or len(items) != indexed):
        sys.exit(
            f"인덱스 미완료: total={total}, indexed={indexed}, "
            f"items={len(items) if isinstance(items, list) else '?'}"
        )
    return index


def asset_name(file_regist_no, original):
    """첨부 종류별 저장 파일명. 확장자는 서버 원본명에서 따온다.

    상수표에 없는 새 종류는 `첨부-<코드>` 로 떨어뜨린다 — 이름을 못 정해서
    기존 파일을 덮어쓰는 것보다 낯선 이름으로 남는 편이 안전하다.
    """
    stem = ASSET_STEMS.get(file_regist_no) or f"첨부-{file_regist_no}"
    return safe_name(stem + os.path.splitext(original)[1].lower())


def part_name(file_code):
    """받는 동안 쓰는 임시 파일명. 저장 이름 공간과 겹치지 않는다.

    서버 원본명으로 곧장 쓰면 그 이름의 기존 파일을 개명 전에 지운다.
    """
    return ".part-" + safe_name(file_code)


def download_url(file_code, file_regist_no):
    return (f"{FILE_HOST}/dstrcFileDownload.json?jobSe=dstrc"
            f"&fileCode={urllib.parse.quote(file_code)}"
            f"&fileRegistNo={file_regist_no}")


def parse_csv_values(raw, label):
    values = {part.strip() for part in raw.split(",") if part.strip()}
    if not values:
        sys.exit(f"{label} 값 없음")
    return values


def unique_name(name, used):
    """이 지구에서 이미 쓴 이름이면 어간 뒤에 -2, -3 을 붙인다.

    같은 fileRegistNo 의 첨부가 한 지구에 둘 이상 달리는 경우에 대비한다.
    접미는 확장자 앞에 넣는다 — 하류가 확장자로 형식을 판정하기 때문이다.
    """
    if name not in used:
        used.add(name)
        return name
    stem, ext = os.path.splitext(name)
    n = 2
    while f"{stem}-{n}{ext}" in used:
        n += 1
    out = f"{stem}-{n}{ext}"
    used.add(out)
    return out


def sniff_ext(blob, name):
    """확장자 검증. 시행지침 .zip이 실제로는 EGG인 경우가 있다."""
    if blob[:4] == b"EGGA" and not name.lower().endswith(".egg"):
        return name + " (EGG archive — unzip 불가, 알집 계열 필요)"
    return None


def download(file_code, file_regist_no, dest_dir):
    req = urllib.request.Request(
        download_url(file_code, file_regist_no),
        headers={"User-Agent": UA, "Referer": MAP_HOST + "/map.do"})
    with _open(req, 300) as f:
        disp = f.headers.get("Content-Disposition", "")
        m = re.search(r"filename=(.+)", disp)
        # Content-Type은 application/x-msdownload로 오므로 신뢰하지 않는다
        original = safe_name(urllib.parse.unquote(m.group(1))) if m else f"{file_code}.bin"
        blob = f.read()
    # 임시명으로 받는다. 서버 원본명으로 쓰면 같은 이름의 기존 첨부를 지운다.
    path = os.path.join(dest_dir, part_name(file_code))
    with open(path, "wb") as fh:
        fh.write(blob)
    return path, len(blob), sniff_ext(blob, original), original


def merge_downloaded(prev_all, got):
    """이번 실행에서 다루지 않은 수집 기록을 보존한다.

    meta_store.upsert 는 지구 단위 교체다. downloaded 를 이번 --file-type 분으로만
    채우면 다른 file-type 으로 받아 둔 기록이 사라진다. 파일은 디스크에 남고
    기록만 없어지므로 계약 검증도 잡지 못한다.
    """
    by_key = {(g["fileCode"], g["fileRegistNo"]): g for g in prev_all}
    for g in got:
        by_key[(g["fileCode"], g["fileRegistNo"])] = g
    return sorted(by_key.values(), key=lambda g: (g["fileRegistNo"], g["fileCode"]))


def select_targets(items, wanted, only):
    """받을 지구를 고른다.

    wanted 는 fileRegistNo 집합, only 는 dstrcAppnNo 집합이거나 None 이다.
    only 를 줘도 파일종류 조건은 풀리지 않는다 — 없는 첨부를 만들 수는 없다.
    """
    return [x for x in items
            if (only is None or x["dstrcAppnNo"] in only)
            and any(a["fileRegistNo"] in wanted for a in x["attachments"])]


def region_dir(region):
    return os.path.join(OUT_BASE, region)


def cmd_index(args):
    region = args.region
    ctprvn = REGIONS[region]
    rdir = region_dir(region)
    os.makedirs(rdir, exist_ok=True)

    print(f"[{region}] 지구 목록 조회")
    items = list_dstrc(ctprvn)

    index, with_attach = [], 0
    for i, it in enumerate(items, 1):
        no, nm = it["dstrcAppnNo"], it["dstrcNm"]
        atts = list_attachments(no)
        if atts:
            with_attach += 1
        # 목록 응답 원본 필드를 손실 없이 보존한다 (stepCode/좌표/newtownNm 등 포함).
        # region·attachments만 덧붙이고, 원본 키는 절대 덮어쓰지 않는다.
        index.append({**it, "region": region, "attachments": atts})
        mark = "★" if any(a["fileRegistNo"] == "7" for a in atts) else (
            "·" if atts else " ")
        print(f"  [{i}/{len(items)}] {mark} {no} {nm[:26]} 첨부 {len(atts)}")
        # 중간 저장 — 중단돼도 재개 가능
        atomic_write_json(os.path.join(rdir, "_index.json"), {
            "region": region,
            "scannedAt": datetime.now().isoformat(),
            "total": len(items),
            "indexed": len(index),
            "items": index,
        })
        time.sleep(REQ_DELAY)

    jichim = sum(1 for x in index if any(a["fileRegistNo"] == "7" for a in x["attachments"]))
    print(f"\n[{region}] 완료 — 지구 {len(index)} / 첨부보유 {with_attach} / 시행지침 {jichim}")
    print(f"  → {os.path.join(rdir, '_index.json')}")


def cmd_fetch(args):
    region = args.region
    rdir = region_dir(region)
    idx_path = os.path.join(rdir, "_index.json")
    if not os.path.exists(idx_path):
        sys.exit(f"인덱스 없음. 먼저 실행: collect.py index --region {region}")

    wanted = parse_csv_values(args.file_type, "--file-type")
    only = parse_csv_values(args.dstrc, "--dstrc") if args.dstrc else None
    idx = load_complete_index(idx_path)
    targets = select_targets(idx["items"], wanted, only)
    scope = f" 지구지정 {len(only)}건" if only else ""
    print(f"[{region}] 대상 지구 {len(targets)}건 (파일종류 {sorted(wanted)}){scope}")
    if only:
        missing = only - {x["dstrcAppnNo"] for x in targets}
        if missing:
            print(f"  [!] 이 region·파일종류에 없는 지구번호: {sorted(missing)}")

    # 재개 판정과 기록이 같은 통합 파일을 본다. 지구별 meta.json 은 두지 않는다.
    store = meta_store.load(OUT_BASE)
    known = meta_store.index_by_appn(store)

    for i, item in enumerate(targets, 1):
        no, nm = item["dstrcAppnNo"], item["dstrcNm"]
        ddir = os.path.join(rdir, safe_name(nm.strip()))
        os.makedirs(ddir, exist_ok=True)

        # 재개 지원 — 통합 meta.json 에 기록된 savedAs 가 실제로 존재하고
        # 크기가 0이 아니면 그 첨부는 다시 받지 않는다.
        prev = {}
        for g in known.get(no, {}).get("downloaded", []):
            fp = os.path.join(ddir, g.get("savedAs", ""))
            try:
                if g.get("savedAs") and os.path.getsize(fp) > 0:
                    prev[(g["fileCode"], g["fileRegistNo"])] = g
            except OSError:
                continue

        got = []
        # 이 지구에서 이미 점유한 저장 이름. meta.json 이 아니라 디스크를 본다 —
        # 기록이 유실돼도 남아 있는 원본을 새 첨부가 덮어쓰면 안 된다.
        # 이름이 겹치면 사본이 하나 더 생길 뿐, 받아 둔 원본은 지워지지 않는다.
        used = set(os.listdir(ddir))
        for a in item["attachments"]:
            if a["fileRegistNo"] not in wanted:
                continue
            label = a["label"] or FILE_LABELS.get(a["fileRegistNo"], "?")
            hit = prev.get((a["fileCode"], a["fileRegistNo"]))
            if hit:
                got.append(hit)
                print(f"  [{i}/{len(targets)}] = {nm[:20]} {label} "
                      f"{hit['bytes']/1048576:.1f}MB (기존 파일, 건너뜀)")
                continue
            try:
                path, size, warn, original = download(
                    a["fileCode"], a["fileRegistNo"], ddir)
                # 임시명으로 받은 뒤 첨부 종류별 이름으로 개명한다.
                # 확장자는 내려받기 전에는 알 수 없어 저장 후에 정한다.
                dst = os.path.join(ddir, unique_name(
                    asset_name(a["fileRegistNo"], original), used))
                if os.path.abspath(path) != os.path.abspath(dst):
                    os.replace(path, dst)
                path = dst
            except Exception as e:
                print(f"  [{i}/{len(targets)}] ✗ {nm[:20]} {label}: {e}")
                time.sleep(DL_DELAY)
                continue
            got.append({**a, "savedAs": os.path.basename(path),
                        "originalName": original, "bytes": size,
                        "sourceUrl": download_url(a["fileCode"], a["fileRegistNo"])})
            note = f" [{warn}]" if warn else ""
            print(f"  [{i}/{len(targets)}] ✓ {nm[:20]} {label} "
                  f"{size/1048576:.1f}MB{note}")
            time.sleep(DL_DELAY)

        # 지구 1건마다 통합 파일을 재기록한다 — 중단해도 그때까지의 결과가
        # 계약을 만족하고, 다음 실행이 재개 판정에 쓸 수 있다.
        meta_store.upsert(store, [{
            **{k: v for k, v in item.items() if k != "attachments"},
            "collectedAt": datetime.now().isoformat(),
            "attachments": item["attachments"],
            "downloaded": merge_downloaded(prev.values(), got)}])
        meta_store.save(OUT_BASE, store)
    print(f"\n[{region}] 다운로드 완료 → {rdir}")
    print(f"[{region}] 메타 → {meta_store.store_path(OUT_BASE)}")


def main():
    p = argparse.ArgumentParser(description="택지정보시스템 시행지침 수집기")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index", help="지구 목록 + 첨부 인덱스 구축")
    pi.add_argument("--region", required=True, choices=list(REGIONS))
    pi.set_defaults(func=cmd_index)

    pf = sub.add_parser("fetch", help="첨부파일 다운로드")
    pf.add_argument("--region", required=True, choices=list(REGIONS))
    pf.add_argument("--file-type", default="7",
                    help="fileRegistNo 콤마구분 (기본 7=시행지침)")
    pf.add_argument("--dstrc", default="",
                    help="dstrcAppnNo 콤마구분. 표본 수집용 — 주면 그 지구만 받는다")
    pf.set_defaults(func=cmd_fetch)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
