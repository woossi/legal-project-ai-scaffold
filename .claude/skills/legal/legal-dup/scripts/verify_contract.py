#!/usr/bin/env python3
"""collect.py 산출물이 contract/ 의 계약을 만족하는지 검증한다.

  python3 scripts/verify_contract.py              # 전 지역
  python3 scripts/verify_contract.py --region 인천

기본 엔진은 jsonschema 패키지다. 미설치 환경에서만 내장 검증기로
폴백하며, 내장기는 이 계약이 쓰는 키워드만 지원한다: type, enum,
required, properties, items, $ref, $defs, additionalProperties,
minLength, minimum, exclusiveMinimum, pattern.
어느 엔진을 썼는지 첫 줄에 출력한다.

종료코드 0=계약 충족, 1=위반, 2=검증 불가(경로·스키마 문제).
"""
import argparse
from collections import Counter
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.join(HERE, "..", "contract")
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
REGIONS = ("서울", "인천", "경기")

TYPES = {"object": dict, "array": list, "string": str, "integer": int,
         "number": (int, float), "boolean": bool, "null": type(None)}


def _type_ok(val, t):
    if t == "integer":
        # JSON에서 bool은 int의 하위형이지만 정수가 아니다
        return isinstance(val, int) and not isinstance(val, bool)
    if t == "number":
        return isinstance(val, (int, float)) and not isinstance(val, bool)
    return isinstance(val, TYPES[t])


def validate(inst, schema, root, path="$", errs=None):
    """내장 검증기. 위반 목록을 반환한다."""
    if errs is None:
        errs = []

    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            errs.append(f"{path}: 지원하지 않는 $ref {ref}")
            return errs
        node = root
        for part in ref[2:].split("/"):
            node = node[part]
        return validate(inst, node, root, path, errs)

    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path}: {inst!r} 은 허용값 {schema['enum']} 이 아니다")
        return errs

    if "type" in schema:
        ts = schema["type"]
        ts = ts if isinstance(ts, list) else [ts]
        if not any(_type_ok(inst, t) for t in ts):
            errs.append(f"{path}: 타입 {ts} 기대, 실제 {type(inst).__name__}")
            return errs

    if isinstance(inst, str):
        if "minLength" in schema and len(inst) < schema["minLength"]:
            errs.append(f"{path}: 최소 길이 {schema['minLength']} 미만")
        if "pattern" in schema and not re.search(schema["pattern"], inst):
            errs.append(f"{path}: 패턴 불일치 {schema['pattern']} — {inst[:60]!r}")

    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in schema and inst < schema["minimum"]:
            errs.append(f"{path}: {inst} < minimum {schema['minimum']}")
        if "exclusiveMinimum" in schema and inst <= schema["exclusiveMinimum"]:
            errs.append(f"{path}: {inst} <= exclusiveMinimum {schema['exclusiveMinimum']}")

    if isinstance(inst, dict):
        for k in schema.get("required", []):
            if k not in inst:
                errs.append(f"{path}: 필수 키 없음 — {k}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for k in inst:
                if k not in props:
                    errs.append(f"{path}: 허용되지 않은 키 — {k}")
        elif isinstance(schema.get("additionalProperties"), dict):
            sub = schema["additionalProperties"]
            for k, v in inst.items():
                if k not in props:
                    validate(v, sub, root, f"{path}.{k}", errs)
        for k, sub in props.items():
            if k in inst:
                validate(inst[k], sub, root, f"{path}.{k}", errs)

    if isinstance(inst, list) and "items" in schema:
        for i, v in enumerate(inst):
            validate(v, schema["items"], root, f"{path}[{i}]", errs)

    return errs


def load_validator(schema_file):
    with open(os.path.join(CONTRACT, schema_file), encoding="utf-8") as fh:
        schema = json.load(fh)
    try:
        import jsonschema
        v = jsonschema.Draft202012Validator(schema)
        return lambda inst: [f"{'.'.join(map(str, e.path)) or '$'}: {e.message}"
                             for e in v.iter_errors(inst)], "jsonschema"
    except ImportError:
        return lambda inst: validate(inst, schema, schema), "내장"


def safe_name(nm):
    """디렉터리명 규약. collect.py 와 같은 치환이어야 한다."""
    return re.sub(r"[\x00-\x1f/\\]", "_", (nm or "").strip())


def is_path_segment(name):
    """savedAs 와 originalName 은 지구 디렉터리 안의 파일명이어야 한다."""
    return (
        isinstance(name, str)
        and name not in ("", ".", "..")
        and os.path.basename(name) == name
    )


def load_json(path, label):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as e:
        return None, f"{label} 읽기 실패: {e}"


def check_store(base, meta_check):
    """통합 meta.json 자체의 계약을 검증한다.

    반환 (store, 위반수). 읽을 수 없으면 store 는 None 이다.
    """
    path = os.path.join(base, "meta.json")
    if not os.path.exists(path):
        print(f"  ✗ meta.json 없음 — fetch 를 먼저 실행해야 한다")
        return None, 1
    store, error = load_json(path, "meta.json")
    if error:
        print(f"  ✗ {error}")
        return None, 1

    v = 0
    for e in meta_check(store):
        print(f"  ✗ meta.json {e}")
        v += 1
    if not isinstance(store, dict):
        return {"districts": []}, v

    items = store.get("districts", [])
    items = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    # 계약: districtCount 는 districts 길이와 같다
    if store.get("districtCount") != len(items):
        print(f"  ✗ meta.json districtCount({store.get('districtCount')}) "
              f"!= districts 길이({len(items)})")
        v += 1

    # 계약: dstrcAppnNo 는 고유하다 — 재개 판정이 이 키로 지구를 찾는다
    nos = [d.get("dstrcAppnNo") for d in items]
    duplicates = sorted(no for no, count in Counter(nos).items() if count > 1)
    if duplicates:
        dup = duplicates
        print(f"  ✗ meta.json dstrcAppnNo 중복: {dup}")
        v += 1

    # 계약: summary 는 districts 에서 재계산한 값이다. 어긋나면 손으로 고친 것이다
    tally = {}
    for d in items:
        r = d.get("region")
        if r:
            tally[r] = tally.get(r, 0) + 1
    summary = store.get("summary", {})
    if summary.get("byRegion") != tally:
        print(f"  ✗ meta.json summary.byRegion 이 districts 와 다르다: "
              f"{summary.get('byRegion')} / 실제 {tally}")
        v += 1
    dl = sum(len(d.get("downloaded", [])) for d in items)
    if summary.get("downloadedFiles") != dl:
        print(f"  ✗ meta.json summary.downloadedFiles"
              f"({summary.get('downloadedFiles')}) != 실제 {dl}")
        v += 1

    return store, v


def main():
    ap = argparse.ArgumentParser(description="legal-dup 산출물 계약 검증")
    ap.add_argument("--region", choices=REGIONS, help="생략 시 전 지역")
    ap.add_argument("--quiet", action="store_true", help="위반만 출력")
    args = ap.parse_args()

    cmds, error = load_json(os.path.join(CONTRACT, "commands.json"), "commands.json")
    if error:
        sys.exit(f"[2] {error}")
    base = os.path.join(ROOT, cmds["outBase"])
    if not os.path.isdir(base):
        sys.exit(f"[2] 산출물 없음: {base}")

    idx_check, engine = load_validator("index.schema.json")
    meta_check, _ = load_validator("meta.schema.json")
    if not args.quiet:
        print(f"검증 엔진: {engine}  기준: {base}")

    if args.region:
        regions = [args.region]
    else:
        output_regions = sorted(
            d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))
        )
        regions = [region for region in REGIONS if region in output_regions]
        regions.extend(region for region in output_regions if region not in REGIONS)

    # 지구 메타는 지역마다 흩어져 있지 않고 통합 파일 하나에 있다.
    store, violations = check_store(base, meta_check)
    if store is None:
        sys.exit(2)
    files = 1          # 통합 meta.json
    checked = 0        # 대조를 마친 지구 수
    all_districts = store.get("districts", [])
    for region in regions:
        rdir = os.path.join(base, region)
        idx_path = os.path.join(rdir, "_index.json")
        if not os.path.exists(idx_path):
            print(f"  ✗ {region}/_index.json 없음 — fetch 의 선행조건 미충족")
            violations += 1
            continue

        idx, error = load_json(idx_path, f"{region}/_index.json")
        if error:
            print(f"  ✗ {error}")
            violations += 1
            continue
        files += 1
        for e in idx_check(idx):
            print(f"  ✗ {region}/_index.json {e}")
            violations += 1
        if not isinstance(idx, dict):
            continue

        # 계약: fetch 완료 입력은 상세 조회 전건과 items 길이가 일치해야 한다.
        total = idx.get("total")
        indexed = idx.get("indexed")
        item_count = len(idx.get("items", [])) if isinstance(idx.get("items"), list) else None
        if indexed != total or item_count != indexed:
            print(f"  ✗ {region}/_index.json 인덱스 미완료: "
                  f"total={total}, indexed={indexed}, items={item_count}")
            violations += 1

        # 계약: _index.json 에 없는 지구 디렉터리는 존재할 수 없다
        known = {x.get("dstrcAppnNo") for x in idx.get("items", [])
                 if isinstance(x, dict)}

        # 이 지역의 지구 메타. region 은 수집 시 디렉터리를 가른 기준이라
        # API 원본 시도명 ctprvnNm 과 다른 지구가 있다(상계 장암·위례).
        # 계약: 디렉터리명은 dstrcNm 을 trim·치환한 값이다 — 이 매핑이 그 계약이다.
        by_dir = {}
        for m in all_districts:
            if m.get("region") != region:
                continue
            name = safe_name(m.get("dstrcNm"))
            if name in by_dir:
                print(f"  ✗ {region}/{name} 지구명이 겹쳐 한 디렉터리를 가리킨다: "
                      f"{by_dir[name].get('dstrcAppnNo')} / {m.get('dstrcAppnNo')}")
                violations += 1
            by_dir[name] = m

        dirs = sorted(d for d in os.listdir(rdir)
                      if os.path.isdir(os.path.join(rdir, d)))

        # 계약: meta.json 에 실린 지구는 디렉터리로 실재해야 한다
        for name in sorted(set(by_dir) - set(dirs)):
            print(f"  ✗ {region}/{name} 디렉터리 없음 — meta.json 에는 있다")
            violations += 1

        for d in dirs:
            ddir = os.path.join(rdir, d)

            # 계약: 디렉터리는 meta.json 에 대응 지구가 있어야 한다.
            # 디렉터리명이 dstrcNm 과 어긋난 경우도 여기서 걸린다.
            meta = by_dir.get(d)
            if meta is None:
                print(f"  ✗ {region}/{d} meta.json 에 없는 지구 디렉터리")
                violations += 1
                continue
            checked += 1

            # 계약: _index.json 에 없는 지구 디렉터리는 존재할 수 없다.
            if meta.get("dstrcAppnNo") not in known:
                print(f"  ✗ {region}/{d} 인덱스에 없는 지구")
                violations += 1

            att = {(a.get("fileCode"), a.get("fileRegistNo"))
                   for a in meta.get("attachments", []) if isinstance(a, dict)}
            for g in meta.get("downloaded", []):
                if not isinstance(g, dict):
                    continue
                file_code = g.get("fileCode")
                file_regist_no = g.get("fileRegistNo")
                label = g.get("label")
                # 계약: downloaded ⊆ attachments
                if (file_code, file_regist_no) not in att:
                    print(f"  ✗ {region}/{d} downloaded 가 attachments 의 부분집합이 아니다: {label}")
                    violations += 1
                # 계약: savedAs 는 실재하고 bytes 와 일치해야 재개 판정이 성립한다
                saved_as = g.get("savedAs")
                if not is_path_segment(saved_as):
                    print(f"  ✗ {region}/{d} savedAs 파일명이 아니다: {saved_as!r}")
                    violations += 1
                    continue
                bytes_recorded = g.get("bytes")
                fp = os.path.join(ddir, saved_as)
                if not os.path.exists(fp):
                    print(f"  ✗ {region}/{d} savedAs 파일 없음: {saved_as}")
                    violations += 1
                elif os.path.getsize(fp) != bytes_recorded:
                    print(f"  ✗ {region}/{d} 크기 불일치: {saved_as} "
                          f"기록 {bytes_recorded} / 실제 {os.path.getsize(fp)}")
                    violations += 1

    print(f"\n검사 {files}개 파일 · 지구 {checked}건 · 위반 {violations}건")
    sys.exit(1 if violations else 0)


if __name__ == "__main__":
    main()
