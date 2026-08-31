#!/usr/bin/env python3
"""norm_건축계획지표.csv의 구조·선언적 계약·원표 대조를 검증한다.

종료코드 0=실패 없음, 1=계약 위반, 2=선행조건 미충족으로 검증 불가.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
DEFAULT_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_CONTRACT_DIR = SKILL_DIR / "contract"
DEFAULT_CASE = SKILL_DIR / "case/norm_건축계획지표.json"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path, encoding: str = "utf-8-sig") -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames or [])
        rows = []
        for row in reader:
            rows.append({key: value if value is not None else "" for key, value in row.items() if key is not None})
    return header, rows


def _fold(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} 이 문자열 배열이 아님")
    return list(value)


def _csv_contract(contract: dict[str, Any], name: str) -> tuple[list[str], dict[str, Any]]:
    columns = _string_list(contract["input_columns"][name], f"input_columns.{name}")
    field_contract = contract.get("input_field_contract", {}).get(name, {})
    if not isinstance(field_contract, dict):
        raise ValueError(f"input_field_contract.{name} 이 객체가 아님")
    return columns, field_contract


def _validate_header(
    actual: list[str],
    expected: list[str],
    label: str,
    violations: list[str],
    skipped: list[str],
) -> bool:
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        skipped.append(f"{label} 필수 열 누락: {missing}")
        return False
    if actual != expected:
        violations.append(
            f"{label} 열 계약 위반: 실제 {actual!r} != 계약 {expected!r}"
            + (f" · 추가열 {extra}" if extra else "")
        )
    return True


def _validate_domain(
    row: dict[str, str],
    domains: dict[str, set[str]],
    label: str,
    violations: list[str],
) -> None:
    for field, allowed in domains.items():
        value = row.get(field, "")
        if value not in allowed:
            violations.append(f"{label} {field} 미허용값: {value!r}")


def _domain_sets(field_contract: dict[str, Any], label: str) -> dict[str, set[str]]:
    raw_domains = field_contract.get("domains", {})
    if not isinstance(raw_domains, dict):
        raise ValueError(f"{label}.domains 이 객체가 아님")
    return {
        field: set(_string_list(values, f"{label}.domains.{field}"))
        for field, values in raw_domains.items()
    }


def _non_negative_int(value: str, label: str, violations: list[str]) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        violations.append(f"{label} 정수 아님: {value!r}")
        return None
    if parsed < 0:
        violations.append(f"{label} 음수: {parsed}")
        return None
    return parsed


def _schema_errors(schema: dict[str, Any], rows: list[dict[str, str]]) -> list[str]:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - CLI environment guard
        raise RuntimeError("jsonschema 모듈 없음") from exc

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    failures = []
    for error in sorted(validator.iter_errors(rows), key=lambda item: list(item.absolute_path)):
        path = list(error.absolute_path)
        if path and isinstance(path[0], int):
            row_label = f"{path[0] + 2}행"
            field = f" {path[-1]}" if len(path) > 1 else ""
        else:
            row_label = "CSV"
            field = ""
        failures.append(f"구조 계약 {row_label}{field}: {error.message}")
    return failures


def verify(
    root: Path,
    contract_dir: Path,
    case_path: Path,
    output_path: Path | None = None,
) -> tuple[list[str], list[str], bool]:
    """위반, 미검사 사유, 전체 검증불가 여부를 반환한다."""
    violations: list[str] = []
    skipped: list[str] = []

    outputs_path = contract_dir / "outputs.json"
    bootstrap = [outputs_path, case_path]
    missing_bootstrap = [path for path in bootstrap if not path.is_file()]
    if missing_bootstrap:
        skipped.extend(f"계약 파일 없음: {path}" for path in missing_bootstrap)
        return violations, skipped, True

    try:
        contract = _load_json(outputs_path)
        case = _load_json(case_path)
        output_spec = contract["outputs"]["norm_건축계획지표.csv"]
        schema_path = contract_dir / Path(output_spec["schema"]).name
        schema = _load_json(schema_path)
        columns = contract["columns"]
        unit_by_metric = contract["값도메인"]["지표별단위"]
        table_columns, table_contract = _csv_contract(contract, "tables.csv")
        cell_columns, cell_contract = _csv_contract(contract, "cells.csv")
        table_integer_fields = _string_list(
            table_contract.get("integer_fields", []),
            "input_field_contract.tables.csv.integer_fields",
        )
        cell_integer_fields = _string_list(
            cell_contract.get("integer_fields", []),
            "input_field_contract.cells.csv.integer_fields",
        )
        table_nonempty_fields = _string_list(
            table_contract.get("nonempty_fields", []),
            "input_field_contract.tables.csv.nonempty_fields",
        )
        cell_nonempty_fields = _string_list(
            cell_contract.get("nonempty_fields", []),
            "input_field_contract.cells.csv.nonempty_fields",
        )
        table_domains = _domain_sets(table_contract, "input_field_contract.tables.csv")
        cell_domains = _domain_sets(cell_contract, "input_field_contract.cells.csv")
        field_contract = contract.get("input_field_contract", {})
        if not isinstance(field_contract, dict):
            raise ValueError("input_field_contract 이 객체가 아님")
        parse_failure_reasons = set(
            _string_list(
                field_contract["norm_건축계획지표.csv"]["파싱실패사유"],
                "input_field_contract.norm_건축계획지표.csv.파싱실패사유",
            )
        )
        condition_pattern = re.compile(contract["판정파라미터"]["조건괄호정규식"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, re.error, OSError) as exc:
        skipped.append(f"계약 해석 불가: {exc}")
        return violations, skipped, True

    output_root = root / contract["output_root"]
    norm_path = output_path or output_root / "norm_건축계획지표.csv"
    tables_path = output_root / "tables.csv"
    cells_path = output_root / "cells.csv"
    required_paths = [schema_path, tables_path, cells_path, norm_path]
    missing = [path for path in required_paths if not path.is_file()]
    if missing:
        skipped.extend(f"선행조건 누락: {path}" for path in missing)
        return violations, skipped, True

    encoding = output_spec.get("encoding", "utf-8-sig")
    try:
        table_header, table_rows = _read_csv(tables_path, encoding)
        cell_header, cell_rows = _read_csv(cells_path, encoding)
        norm_header, norm_rows = _read_csv(norm_path, encoding)
        violations.extend(_schema_errors(schema, norm_rows))
    except (OSError, UnicodeError, csv.Error, ValueError, RuntimeError) as exc:
        skipped.append(f"입력 해석 불가: {exc}")
        return violations, skipped, True

    table_header_ok = _validate_header(
        table_header, table_columns, "tables.csv", violations, skipped
    )
    cell_header_ok = _validate_header(
        cell_header, cell_columns, "cells.csv", violations, skipped
    )
    if not table_header_ok or not cell_header_ok:
        return violations, skipped, True

    if norm_header != columns:
        violations.append(
            f"열 순서 고정: 실제 {norm_header!r} != 계약 {columns!r}"
        )

    tables: dict[str, dict[str, str]] = {}
    table_shapes: dict[str, tuple[int, int]] = {}
    for csv_row, table in enumerate(table_rows, 2):
        label = f"tables.csv {csv_row}행"
        for field in table_nonempty_fields:
            if not table.get(field, "").strip():
                violations.append(f"{label} {field} 비어 있음")
        _validate_domain(table, table_domains, label, violations)
        parsed_ints = {
            field: _non_negative_int(table.get(field, ""), f"{label} {field}", violations)
            for field in table_integer_fields
        }
        table_id = table.get("표ID", "")
        if not table_id:
            continue
        if table_id in tables:
            violations.append(f"{label} 표ID 중복: {table_id}")
            continue
        tables[table_id] = table
        row_count = parsed_ints.get("행수")
        column_count = parsed_ints.get("열수")
        if row_count is not None and column_count is not None:
            table_shapes[table_id] = (row_count, column_count)
        caption = table.get("캡션원문", "")
        caption_position = table.get("캡션위치", "")
        if caption and caption_position not in {"위", "아래"}:
            violations.append(
                f"{label} 캡션원문·캡션위치 불일치: "
                f"캡션이 있는데 위치={caption_position!r}"
            )
        if not caption and caption_position != "없음":
            violations.append(
                f"{label} 캡션원문·캡션위치 불일치: "
                f"캡션이 없는데 위치={caption_position!r}"
            )

    cells_by_table: dict[str, list[str]] = defaultdict(list)
    max_row_by_table: dict[str, int] = {}
    coordinates_by_table: dict[str, Counter[tuple[int, int]]] = defaultdict(Counter)
    for csv_row, cell in enumerate(cell_rows, 2):
        label = f"cells.csv {csv_row}행"
        for field in cell_nonempty_fields:
            if not cell.get(field, "").strip():
                violations.append(f"{label} {field} 비어 있음")
        _validate_domain(cell, cell_domains, label, violations)
        table_id = cell.get("표ID", "")
        cells_by_table[table_id].append(cell.get("값", ""))
        if table_id and table_id not in tables:
            violations.append(f"{label} 표ID 원표 없음: {table_id}")
        parsed_ints = {
            field: _non_negative_int(cell.get(field, ""), f"{label} {field}", violations)
            for field in cell_integer_fields
        }
        row_index = parsed_ints.get("행번호")
        column_index = parsed_ints.get("열번호")
        if row_index is None or column_index is None:
            continue
        max_row_by_table[table_id] = max(row_index, max_row_by_table.get(table_id, -1))
        coordinates_by_table[table_id][(row_index, column_index)] += 1
        shape = table_shapes.get(table_id)
        if shape and (row_index >= shape[0] or column_index >= shape[1]):
            violations.append(
                f"{label} 셀 좌표 범위 초과: 표ID={table_id}, "
                f"좌표=({row_index}, {column_index}), 행열={shape}"
            )
    for table_id, coordinates in coordinates_by_table.items():
        duplicate_coordinates = sorted(
            coordinate for coordinate, count in coordinates.items() if count > 1
        )
        if duplicate_coordinates:
            violations.append(
                f"cells.csv 셀 좌표 중복: 표ID={table_id}, 좌표={duplicate_coordinates[:5]}"
            )

    blocked_by_table: dict[str, list[str]] = defaultdict(list)
    targets = case.get("비규범표_캡션규칙")
    if not isinstance(targets, list):
        skipped.append("case 비규범표_캡션규칙이 배열이 아님")
        return violations, skipped, True
    compiled_targets: list[tuple[str, re.Pattern[str]]] = []
    for target in targets:
        if not isinstance(target, dict):
            skipped.append("case 비규범표_캡션규칙 항목이 객체가 아님")
            return violations, skipped, True
        case_id = target.get("case_id")
        pattern_text = target.get("pattern")
        if not isinstance(case_id, str) or not case_id or not isinstance(pattern_text, str):
            skipped.append("case 비규범표 표적의 case_id·pattern 누락")
            return violations, skipped, True
        try:
            pattern = re.compile(pattern_text)
        except re.error as exc:
            skipped.append(f"case {case_id} 정규식 오류: {exc}")
            return violations, skipped, True
        compiled_targets.append((case_id, pattern))

    preserved = case.get("자동배제금지_캡션사례")
    if not isinstance(preserved, list):
        skipped.append("case 자동배제금지_캡션사례가 배열이 아님")
        return violations, skipped, True
    for item in preserved:
        if not isinstance(item, dict):
            skipped.append("case 자동배제금지_캡션사례 항목이 객체가 아님")
            return violations, skipped, True
        preserve_id = item.get("case_id")
        caption = item.get("캡션원문")
        if not isinstance(preserve_id, str) or not preserve_id or not isinstance(caption, str):
            skipped.append("case 자동배제금지 사례의 case_id·캡션원문 누락")
            return violations, skipped, True
        for case_id, pattern in compiled_targets:
            if pattern.search(caption):
                skipped.append(
                    f"case {case_id}가 자동배제금지 {preserve_id} 캡션을 삼킴"
                )
                return violations, skipped, True

    for case_id, pattern in compiled_targets:
        for table_id, table in tables.items():
            if pattern.search(table.get("캡션원문", "")):
                blocked_by_table[table_id].append(case_id)

    note_targets = case.get("단서보존_표적")
    if not isinstance(note_targets, list):
        skipped.append("case 단서보존_표적이 배열이 아님")
        return violations, skipped, True

    rows_by_table: dict[str, int] = defaultdict(int)
    for csv_row, row in enumerate(norm_rows, 2):
        label = f"{csv_row}행"
        table_id = row.get("표ID", "")
        rows_by_table[table_id] += 1

        numeric = bool(row.get("값_수치", "").strip())
        reason = bool(row.get("파싱실패사유", "").strip())
        if numeric == reason:
            violations.append(
                f"{label} 값_수치·파싱실패사유 배타성 위반: "
                f"값_수치={row.get('값_수치', '')!r}, "
                f"파싱실패사유={row.get('파싱실패사유', '')!r}"
            )
        if row.get("파싱실패사유", "") not in parse_failure_reasons:
            violations.append(
                f"{label} 파싱실패사유 미허용값: {row.get('파싱실패사유', '')!r}"
            )

        raw_value = row.get("값_원문", "")
        note = row.get("단서", "")
        for match in condition_pattern.finditer(raw_value):
            condition = match.group(1).strip()
            if not note.strip():
                violations.append(
                    f"{label} 괄호 조건 단서 보존 위반: 단서 비어 있음 ({condition})"
                )
            elif _fold(condition) not in _fold(note):
                violations.append(
                    f"{label} 괄호 조건 단서 보존 위반: {condition!r}가 단서에 없음"
                )

        metric = row.get("지표명", "")
        unit = row.get("단위", "")
        expected_unit = unit_by_metric.get(metric)
        if expected_unit is not None and unit != expected_unit:
            violations.append(
                f"{label} 지표명·단위 대응 위반: {metric}는 {expected_unit}, 실제 {unit}"
            )

        table = tables.get(table_id)
        if table is None:
            violations.append(f"{label} 표ID 원표 없음: {table_id!r}")
            continue
        if table.get("정규화대상") != "건축계획지표":
            violations.append(
                f"{label} 정규화 대상 표 참조 위반: {table_id}의 정규화대상="
                f"{table.get('정규화대상')!r}"
            )
        for field in ("지구번호", "추출경로", "품질등급"):
            if row.get(field, "") != table.get(field, ""):
                violations.append(
                    f"{label} {field} 원표 불일치: norm={row.get(field, '')!r}, "
                    f"tables={table.get(field, '')!r}"
                )

        if raw_value not in cells_by_table.get(table_id, []):
            violations.append(
                f"{label} 값_원문이 cells.csv에 없음: 표ID={table_id}, 값={raw_value!r}"
            )

        for case_id in blocked_by_table.get(table_id, []):
            violations.append(
                f"{label} 비규범 예시표 정규화 금지 위반: 표ID={table_id}, case={case_id}"
            )

        for target in note_targets:
            if not isinstance(target, dict) or target.get("값_원문") != raw_value:
                continue
            expected = target.get("기대")
            case_id = target.get("case_id", "단서보존_case")
            if not isinstance(expected, dict):
                skipped.append(f"case {case_id} 기대값이 객체가 아님")
                return violations, skipped, True
            for field, value in expected.items():
                if row.get(field) != value:
                    violations.append(
                        f"{label} case {case_id} 불일치: {field}={row.get(field)!r}, 기대={value!r}"
                    )

    candidate_table_ids = {
        table_id
        for table_id, table in tables.items()
        if table.get("정규화대상") == "건축계획지표"
    }
    target_table_ids = candidate_table_ids - set(blocked_by_table)

    exemption = contract["판정파라미터"].get("2층생성규칙", {}).get("게이트6_면제", {})
    listed_exempt = {
        item.get("표ID")
        for item in exemption.get("열거면제", [])
        if isinstance(item, dict)
    }
    for table_id in sorted(target_table_ids):
        if rows_by_table.get(table_id, 0) != 0:
            continue
        # 면제는 계약이 정한 두 갈래로만 인정하고 tables.csv·cells.csv에서 다시 계산한다.
        if max_row_by_table.get(table_id, -1) <= 0:
            continue
        if not any(re.search(r"\d", value) for value in cells_by_table.get(table_id, [])):
            continue
        if table_id in listed_exempt:
            continue
        violations.append(
            f"정규화 대상 표 참조 위반: {table_id}에 norm 행이 없음"
        )

    if not target_table_ids and not norm_rows:
        skipped.append("tables.csv에 건축계획지표 정규화 대상 표가 없어 값 검증 미검사")
        return violations, skipped, True

    skipped.append(
        "원본문서 보유 189지구 전수 커버리지: 1층 extraction report 계약 확정 전"
    )
    skipped.append(
        "생성 멱등성: 이 검증기는 산출물만 본다. 2회 생성 바이트 동일성은 "
        "tools/tests/test_legal_tablecsv_normalize.py가 검사한다"
    )
    return violations, skipped, False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--contract-dir", type=Path, default=DEFAULT_CONTRACT_DIR)
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    violations, skipped, unverifiable = verify(
        args.root.resolve(),
        args.contract_dir.resolve(),
        args.case.resolve(),
        args.output.resolve() if args.output else None,
    )

    for message in violations:
        print(f"  ✗ {message}")
    for message in skipped:
        print(f"  - 미검사: {message}")
    print(f"\n위반 {len(violations)}건 · 미검사 {len(skipped)}건")

    if unverifiable:
        return 2
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
