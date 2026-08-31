#!/usr/bin/env python3
"""용어집 산출물이 contract/ 의 계약을 만족하는지 검증한다.

  python3 scripts/verify_contract.py              # 스키마 + 교차 제약
  python3 scripts/verify_contract.py --full       # 정의문 원문 대조까지 (느림)

--full 은 모든 variant 정의문이 해당 지구 md 에 공백 정규화 후 부분문자열로
존재하는지 확인한다(검증 루틴 1번). md 는 output/legal/markdown/ 에서 찾는다.

종료코드 0=계약 충족, 1=위반, 2=검증 불가.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.join(HERE, "..", "contract")
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))

# 절삭 직전 md 시점. f9b8a45 (`md 189건에 절삭·압축 적용`) 가
# 정의조항 구간을 제거했으므로 그 부모 커밋을 전체 SHA로 고정한다.
PRE_SLIM_REF = "bedec510b36d0148d2d9acfc82902fea463e6b36"


def unavailable(message):
    print(f"[2] {message}", file=sys.stderr)
    sys.exit(2)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        unavailable(f"JSON 읽기 실패: {path}: {exc}")
    except json.JSONDecodeError as exc:
        unavailable(f"JSON 파싱 실패: {path}: {exc}")


def decode_json_pointer_token(token):
    return token.replace("~1", "/").replace("~0", "~")


def resolve_schema_ref(schema, schema_ref):
    if "#" not in schema_ref:
        return schema
    fragment = schema_ref.split("#", 1)[1]
    if not fragment:
        return schema
    if not fragment.startswith("/"):
        unavailable(f"JSON Pointer 형식이 아니다: #{fragment}")

    node = schema
    for raw in fragment.lstrip("/").split("/"):
        key = decode_json_pointer_token(raw)
        if not isinstance(node, dict) or key not in node:
            unavailable(f"스키마 참조를 해석하지 못했다: {schema_ref}")
        node = node[key]
    return node


def load_validator(schema_file):
    schema_name = str(schema_file).split("#", 1)[0]
    path = os.path.join(CONTRACT, schema_name)
    try:
        import jsonschema
        schema = load_json(path)
        cls = jsonschema.validators.validator_for(schema)
        cls.check_schema(schema)
        v = cls(schema)
        check_schema = resolve_schema_ref(schema, str(schema_file))
        if check_schema is schema:
            errors = v.iter_errors
        else:
            errors = lambda inst: v.descend(inst, check_schema)
        return lambda inst: [f"{'.'.join(map(str, e.path)) or '$'}: {e.message}"
                             for e in errors(inst)], f"jsonschema/{cls.META_SCHEMA.get('$id', cls.__name__)}"
    except ImportError:
        return None, None
    except jsonschema.SchemaError as exc:
        unavailable(f"스키마 오류: {schema_file}: {exc.message}")


def fold(s):
    """terms.json meta.정의문_저장시_변형규칙 과 동일한 정규화를 md 원문에 적용한다.

    ② 마크다운 강조 `**` 제거, ③ `> [표]`·`> [그림]` 등 변환 마커 줄 제거,
    ① 연속 공백을 단일 스페이스로. ④ 앞머리 번호는 정의문 선두에서만 떼는
    규칙이라 원문 전체 대조에는 적용하지 않는다(줄 중간 번호를 지우면 안 된다).

    이 정규화를 맞추지 않으면 정상 데이터가 대량 불일치로 잡힌다.
    """
    s = s.replace("**", "")
    s = re.sub(r"(?m)^\s*>\s*\[[^\]]*\]\s*$", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fold_needle(s):
    """정의문 쪽 정규화. 저장 시 이미 규칙이 적용돼 있으므로 공백만 맞춘다."""
    return re.sub(r"\s+", " ", s.replace("**", "")).strip()


def main():
    ap = argparse.ArgumentParser(description="legal-term 산출물 계약 검증")
    ap.add_argument("--full", action="store_true", help="정의문 원문 대조 포함")
    ap.add_argument("--pre-slim-ref", default=PRE_SLIM_REF,
                    help="정의조항 절삭 이전 md 를 읽을 git 참조")
    args = ap.parse_args()

    spec = load_json(os.path.join(CONTRACT, "outputs.json"))
    base = os.path.join(ROOT, spec["outBase"])
    if not os.path.isdir(base):
        sys.exit(f"[2] 산출물 없음: {base}")

    validators = {}
    engine = None
    declared_schemas = set()
    for f in spec["files"].values():
        schema = f.get("schema")
        if not schema or schema in validators:
            continue
        declared_schemas.add(schema)
        check, engine = load_validator(schema)
        if check is None:
            sys.exit("[2] jsonschema 미설치: python3 -m pip install jsonschema")
        validators[schema] = check
    if declared_schemas and not validators:
        sys.exit("[2] jsonschema 미설치: python3 -m pip install jsonschema")
    print(f"검증 엔진: {engine or '교차 제약 전용'}  기준: {base}")

    v = 0

    # 1) 파일 존재 + 구조 계약
    loaded = {}
    for name, f in spec["files"].items():
        path = os.path.join(base, name)
        exists = os.path.exists(path)
        if f["required"] and not exists:
            print(f"  ✗ 필수 산출물 없음: {name}")
            v += 1
            continue
        elif not f["required"] and not exists:
            print(f"  · 미생성(계약상 허용): {name} — {f.get('status','')}")
            continue
        data = load_json(path)
        loaded[name] = data
        for key in f.get("topKeys", []):
            if key not in data:
                print(f"  ✗ {name}: top key 없음 — {key}")
                v += 1
        schema = f.get("schema")
        if schema:
            errs = validators[schema](data)
            for e in errs[:40]:
                print(f"  ✗ {name} {e}")
            if len(errs) > 40:
                print(f"  ✗ {name} 스키마 위반 외 {len(errs) - 40}건")
            v += len(errs)

    tpath = os.path.join(base, "terms.json")
    if not os.path.exists(tpath):
        print("\ncontract 위반으로 중단 — terms.json 이 없다")
        sys.exit(1)
    doc = loaded["terms.json"]

    terms = doc.get("terms", [])
    meta = doc.get("meta", {})
    ids = {t["id"] for t in terms if "id" in t}
    id_counts = Counter(t.get("id") for t in terms if t.get("id"))
    duplicate_ids = [tid for tid, count in id_counts.items() if count > 1]
    if duplicate_ids:
        print(f"  ✗ terms.json 중복 id {len(duplicate_ids)}건 (예: {duplicate_ids[:3]})")
        v += len(duplicate_ids)
    normalized_counts = Counter(t.get("normalized") for t in terms if t.get("normalized"))
    duplicate_normalized = [
        value for value, count in normalized_counts.items() if count > 1
    ]
    if duplicate_normalized:
        print(
            f"  ✗ terms.json normalized 중복 {len(duplicate_normalized)}건 "
            f"(예: {duplicate_normalized[:3]})"
        )
        v += len(duplicate_normalized)

    # 2) 교차 제약
    if meta.get("정의문_추출성공_문서수", 0) > meta.get("대상문서수", 0):
        print("  ✗ meta: 정의문_추출성공_문서수 > 대상문서수")
        v += 1
    if meta.get("추출용어수_고유") != len(terms):
        print(f"  ✗ meta.추출용어수_고유({meta.get('추출용어수_고유')}) != terms 길이({len(terms)})")
        v += 1

    for t in terms:
        tid = t.get("id", "?")
        vidx = {x["variant_index"] for x in t.get("variants", [])}
        vcounts = Counter(x.get("variant_index") for x in t.get("variants", []))
        duplicated_variant_indexes = [
            idx for idx, count in vcounts.items() if idx is not None and count > 1
        ]
        if duplicated_variant_indexes:
            print(f"  ✗ {tid} variants.variant_index 중복 {duplicated_variant_indexes[:5]}")
            v += len(duplicated_variant_indexes)
        occ = t.get("occurrences", [])

        if t.get("occurrence_count") != len(occ):
            print(f"  ✗ {tid} occurrence_count({t.get('occurrence_count')}) != occurrences({len(occ)})")
            v += 1
        if t.get("doc_frequency") != len({o.get("dstrcAppnNo") for o in occ}):
            print(f"  ✗ {tid} doc_frequency 가 고유 지구 수와 다르다")
            v += 1
        for o in occ:
            if o.get("variant_index") not in vidx:
                print(f"  ✗ {tid} occurrence.variant_index={o.get('variant_index')} 가 variants 에 없다")
                v += 1
                break
        # 자기모순: cited_laws 가 있는데 지침고유
        if t.get("cited_laws") and t.get("classification", {}).get("legal_status") == "지침고유":
            print(f"  ✗ {tid} cited_laws 가 있는데 legal_status=지침고유 (자기모순)")
            v += 1
        # 모수 정합
        den = meta.get("정의문_추출성공_문서수") or 0
        if den and t.get("doc_frequency") is not None:
            exp = round(t["doc_frequency"] / den, 4)
            if abs(t.get("district_ratio_adj", 0) - exp) > 0.0002 and t["doc_frequency"] <= den:
                print(f"  ✗ {tid} district_ratio_adj={t.get('district_ratio_adj')} 기대 {exp}")
                v += 1

    # 4) 파일 간 id 정합성
    for name in ["core_terms.json", "_ocr_only_terms.json"]:
        p = os.path.join(base, name)
        if not os.path.exists(p):
            continue
        sub = loaded.get(name) or load_json(p)
        subterms = sub.get("terms", []) if isinstance(sub, dict) else sub
        orphan = [x.get("id") for x in subterms if isinstance(x, dict) and x.get("id") not in ids]
        if orphan:
            print(f"  ✗ {name}: terms.json 에 없는 id {len(orphan)}건 (예: {orphan[:3]})")
            v += len(orphan)

    definitions = loaded.get("definiation.json", {}).get("definitions", [])
    definition_orphans = [
        e.get("id") for e in definitions
        if isinstance(e, dict) and e.get("id") not in ids
    ]
    if definition_orphans:
        print(
            f"  ✗ definiation.json: terms.json 에 없는 id "
            f"{len(definition_orphans)}건 (예: {definition_orphans[:3]})"
        )
        v += len(definition_orphans)

    doc_records = loaded.get("doc_definitions.json", {}).get("records", [])
    doc_orphans = [
        r.get("term_id") for r in doc_records
        if isinstance(r, dict) and r.get("term_id") not in ids
    ]
    if doc_orphans:
        print(
            f"  ✗ doc_definitions.json: terms.json 에 없는 term_id "
            f"{len(doc_orphans)}건 (예: {doc_orphans[:3]})"
        )
        v += len(doc_orphans)

    # 5) 정의문 원문 대조
    if args.full:
        # 현재 md 는 정의조항을 절삭한 산출물이라 이 참조가
        # --full 검증의 필수 입력이다. 역사가 없는 상태를 데이터
        # 불일치로 보고하지 않고 계약의 `검증 불가` 상태로 분리한다.
        try:
            preflight = subprocess.run(
                ["git", "cat-file", "-e", f"{args.pre_slim_ref}^{{commit}}"],
                cwd=ROOT, capture_output=True, text=True,
            )
        except OSError as exc:
            print(f"[2] git 이력을 읽을 수 없음: {exc}", file=sys.stderr)
            sys.exit(2)
        if preflight.returncode != 0:
            detail = preflight.stderr.strip()
            suffix = f" ({detail})" if detail else ""
            print(f"[2] 절삭 이전 git 참조 없음: {args.pre_slim_ref}{suffix}",
                  file=sys.stderr)
            sys.exit(2)

        mdroot = os.path.join(ROOT, "output", "legal", "markdown")
        cache, checked, missing, nomd = {}, 0, 0, set()

        # 지구번호 → md 경로 인덱스. 파일명에 지구번호가 없으므로
        # frontmatter 의 `지구번호:` 를 읽어 만든다.
        index = {}
        for dp, _, fns in os.walk(mdroot):
            for fn in fns:
                if not fn.endswith(".md"):
                    continue
                path = os.path.join(dp, fn)
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        m = re.match(r"^지구번호:\s*(\S+)", line)
                        if m:
                            index[m.group(1)] = path
                            break

        def hays_of(d):
            if d not in cache:
                path = index.get(d)
                found = []
                if path:
                    found.append(fold(open(path, encoding="utf-8",
                                           errors="ignore").read()))
                    relpath = os.path.relpath(path, ROOT)
                    p = subprocess.run(
                        ["git", "show", f"{args.pre_slim_ref}:{relpath}"],
                        cwd=ROOT, capture_output=True, text=True,
                    )
                    if p.returncode == 0:
                        found.append(fold(p.stdout))
                cache[d] = found
            return cache[d]

        for t in terms:
            for var in t.get("variants", []):
                needle = fold_needle(var.get("text", ""))
                if not needle:
                    continue
                districts = var.get("districts", [])
                # 이 variant 의 어느 한 지구에서라도 원문이 확인되면 통과다.
                # 첫 지구만 보면 안 된다 — variant 는 여러 지구에 걸쳐 병합되며
                # 대표 문자열이 그중 한 문서의 표기를 따른다.
                hays = [(d, h) for d in districts for h in hays_of(d) if h]
                if not hays:
                    nomd.update(districts)
                    continue
                checked += 1
                if any(needle in h for _, h in hays):
                    continue
                # 공백 삽입/제거 차이는 원문 조판 아티팩트다(`정 한` ↔ `정한`).
                # 계약은 어휘 보존이지 공백 보존이 아니므로 이 경우는 통과시킨다.
                sq = lambda s: re.sub(r"\s+", "", s)
                if any(sq(needle) in sq(h) for _, h in hays):
                    continue
                missing += 1
                if missing <= 10:
                    print(f"  ✗ {t['id']} 정의문이 어느 지구 md 에도 없다"
                          f"({','.join(districts[:3])}): {needle[:60]}…")
        if nomd:
            print(f"  · md 미발견 지구 {len(nomd)}개 — 대조 생략")
        print(f"  · 원문 대조 {checked}건 중 불일치 {missing}건")
        v += missing

    print(f"\n용어 {len(terms)}건 검사 · 위반 {v}건")
    sys.exit(1 if v else 0)


if __name__ == "__main__":
    main()
