#!/usr/bin/env python3
"""목차구조 전수조사 CSV 가 contract/columns.json 을 만족하는지 검증한다.

  python3 scripts/verify_contract.py

열 구성·값 도메인·행 제약·md 와의 지구 집합 일치를 본다.
종료코드 0=계약 충족, 1=위반, 2=검증 불가.
"""
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.join(HERE, "..", "contract")
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))

OK = 0
VIOLATION = 1
UNAVAILABLE = 2


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[2] 계약 파일 없음: {path}")
        return None
    except json.JSONDecodeError as exc:
        print(f"[2] 계약 JSON 파싱 실패: {path}:{exc.lineno}:{exc.colno} {exc.msg}")
        return None
    if not isinstance(data, dict):
        print(f"[2] 계약 루트가 객체가 아니다: {path}")
        return None
    return data


def read_rows(path, encoding):
    try:
        with open(path, encoding=encoding, newline="") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"[2] 산출물 없음: {path}")
        return None
    if not rows:
        print("[2] 빈 CSV")
        return None
    return rows


def check_columns(rows, cols):
    declared = [c["name"] for c in cols]
    actual = list(rows[0].keys())
    if actual == declared:
        return 0

    violations = 0
    missing = [c for c in declared if c not in actual]
    extra = [c for c in actual if c not in declared]
    if missing:
        print(f"  ✗ 계약에 있으나 CSV 에 없는 열: {missing}")
        violations += len(missing)
    if extra:
        print(f"  ✗ CSV 에 있으나 계약에 없는 열: {extra}")
        violations += len(extra)
    if not missing and not extra:
        print("  ✗ 열 순서가 계약과 다르다")
        violations += 1
    return violations


def check_cell(row_no, row, col):
    name = col["name"]
    if name not in row:
        return 0

    val = (row[name] or "").strip()
    violations = 0
    if col.get("required") and not val:
        print(f"  ✗ {row_no}행 {name}: 필수값 비어 있음")
        violations += 1
    if col["type"] == "enum" and val and val not in col["values"]:
        print(f"  ✗ {row_no}행 {name}: 미허용값 {val!r} (허용 {col['values']})")
        violations += 1
    if col["type"] == "int" and val:
        try:
            iv = int(val)
        except ValueError:
            print(f"  ✗ {row_no}행 {name}: 정수가 아니다 {val!r}")
            return violations + 1
        if "min" in col and iv < col["min"]:
            print(f"  ✗ {row_no}행 {name}: {iv} < min {col['min']}")
            violations += 1
    return violations


def check_row_constraints(row_no, row):
    violations = 0
    top = (row.get("최상위단위") or "").strip()
    cnt = (row.get("최상위개수") or "0").strip()
    if top == "없음" and cnt not in ("0", ""):
        print(f"  ✗ {row_no}행 최상위단위=없음 인데 최상위개수={cnt}")
        violations += 1
    if top == "편":
        try:
            if int(row.get("편수") or 0) < 1:
                print(f"  ✗ {row_no}행 최상위단위=편 인데 편수={row.get('편수')}")
                violations += 1
        except ValueError:
            pass
    return violations


def check_rows(rows, cols, key):
    violations = 0
    seen = set()
    for row_no, row in enumerate(rows, 2):
        for col in cols:
            violations += check_cell(row_no, row, col)
        row_key = (row.get(key) or "").strip()
        if row_key in seen:
            print(f"  ✗ {row_no}행 {key} 중복: {row_key}")
            violations += 1
        seen.add(row_key)
        violations += check_row_constraints(row_no, row)
    return seen, violations


def read_md_districts(mdroot):
    md = set()
    if not os.path.isdir(mdroot):
        return md

    for dp, _, fns in os.walk(mdroot):
        for fn in fns:
            if not fn.endswith(".md"):
                continue
            # 지구번호는 파일명이 아니라 frontmatter 에서 읽는다.
            # 파일명 규약이 바뀌어도 검증이 조용히 비어버리지 않는다.
            with open(os.path.join(dp, fn), encoding="utf-8") as fh:
                for line in fh:
                    m = re.match(r"^지구번호:\s*(\S+)", line)
                    if m:
                        md.add(m.group(1))
                        break
    return md


def check_md_set(csv_districts):
    md = read_md_districts(os.path.join(ROOT, "output", "legal", "markdown"))
    if not md:
        return 0

    violations = 0
    only_csv = csv_districts - md
    only_md = md - csv_districts
    if only_csv:
        print(f"  ✗ md 없는 유령 행 {len(only_csv)}건: {sorted(only_csv)[:3]}")
        violations += len(only_csv)
    if only_md:
        print(f"  ✗ 조사 누락 지구 {len(only_md)}건: {sorted(only_md)[:3]}")
        violations += len(only_md)
    return violations


def main():
    spec = load_json(os.path.join(CONTRACT, "columns.json"))
    if spec is None:
        return UNAVAILABLE

    path = os.path.join(ROOT, spec["outBase"], spec["file"])
    if not os.path.exists(path):
        print(f"[2] 산출물 없음: {path}")
        return UNAVAILABLE

    rows = read_rows(path, spec.get("encoding", "utf-8-sig"))
    if rows is None:
        return UNAVAILABLE
    print(f"기준: {path}\n행 {len(rows)}건")

    cols = spec["columns"]
    violations = check_columns(rows, cols)
    seen, row_violations = check_rows(rows, cols, spec["keyColumn"])
    violations += row_violations
    violations += check_md_set(seen)

    print(f"\n위반 {violations}건")
    return VIOLATION if violations else OK


if __name__ == "__main__":
    sys.exit(main())
