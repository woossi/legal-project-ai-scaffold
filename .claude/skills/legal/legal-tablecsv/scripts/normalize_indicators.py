#!/usr/bin/env python3
"""1층 tables.csv·cells.csv에서 2층 norm_건축계획지표.csv를 생성한다.

판정 규칙은 contract/outputs.json의 판정파라미터.2층생성규칙이 정본이고
이 스크립트는 그것을 집행한다. 규칙을 코드에 하드코딩하지 않는다.

종료코드 0=생성 성공, 2=선행조건 미충족으로 생성 불가.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from io import StringIO
from pathlib import Path
from typing import Any, Iterable, Sequence

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
DEFAULT_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_CONTRACT_DIR = SKILL_DIR / "contract"
DEFAULT_CASE = SKILL_DIR / "case/norm_건축계획지표.json"

OUTPUT_CSV = "norm_건축계획지표.csv"
OUTPUT_REPORT = "_norm_indicator_report.json"

csv.field_size_limit(10 * 1024 * 1024)


# --------------------------------------------------------------------------- io


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path, encoding: str = "utf-8-sig") -> list[dict[str, str]]:
    with path.open(encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {key: (value if value is not None else "") for key, value in row.items() if key is not None}
            for row in reader
        ]


def _fold(text: str) -> str:
    """공백을 지운 비교용 표기. 원문은 절대 바꾸지 않는다."""
    return re.sub(r"\s+", "", text or "")


# ------------------------------------------------------------------- 규칙 컴파일


class Rules:
    """계약의 2층생성규칙을 실행 가능한 형태로 컴파일한다."""

    def __init__(self, contract: dict[str, Any], case: dict[str, Any]) -> None:
        params = contract["판정파라미터"]
        spec = params["2층생성규칙"]
        self.columns: list[str] = list(contract["columns"])
        self.unit_by_metric: dict[str, str] = dict(contract["값도메인"]["지표별단위"])
        self.condition = re.compile(params["조건괄호정규식"])
        self.paren = re.compile(spec["괄호제거정규식"])

        self.metric_rules = [
            (
                item["지표명"],
                re.compile(item["포함정규식"]),
                re.compile(item["제외정규식"]) if item.get("제외정규식") else None,
                bool(item.get("값기준_층높이분기", False)),
            )
            for item in spec["지표명매핑"]
        ]
        self.metric_order: list[str] = list(spec["지표명우선순위"])

        self.limit_high_header = re.compile(spec["한도구분규칙"]["헤더_최고정규식"])
        self.limit_low_header = re.compile(spec["한도구분규칙"]["헤더_최저정규식"])
        self.limit_high_value = re.compile(spec["한도구분규칙"]["값_최고정규식"])
        self.limit_low_value = re.compile(spec["한도구분규칙"]["값_최저정규식"])
        self.limit_default: str = spec["한도구분규칙"]["기본값"]

        self.subject_header_rules = [
            (item["주체유형"], re.compile(item["포함정규식"]))
            for item in spec["주체유형매핑"]
        ]
        self.subject_default: str = spec["주체열선택"]["미판정_주체유형"]
        self.subject_unmarked: str = spec["주체열선택"]["주체무표기치"]
        self.subject_tiers = [
            re.compile(spec["주체열선택"]["선호헤더정규식_1순위"]),
            re.compile(spec["주체열선택"]["선호헤더정규식_2순위"]),
        ]
        self.subject_excluded = re.compile(spec["주체열선택"]["제외헤더정규식"])
        self.subject_fill_floor: float = float(spec["주체열선택"]["선호_최소채움률"])
        self.subject_fill_carry: bool = bool(spec["주체열선택"]["채움률_병합이월반영"])

        transposed = spec["전치표규칙"]
        self.t_subject_label = re.compile(transposed["주체라벨정규식"])
        self.t_label_max: int = int(transposed["라벨최대길이"])
        self.t_separators = set(transposed["분리자표기"])

        self.aggregate = re.compile(spec["집계행정규식"])
        self.novalue = set(spec["무값표기"])
        parsing = spec["값파싱규칙"]
        self.numeric = re.compile(parsing["수치정규식"])
        self.strip_tokens = re.compile(parsing["제거정규식"])
        self.height_meter = re.compile(parsing["미터표기정규식"])
        self.height_floor = re.compile(parsing["층표기정규식"])
        self.fail_reasons: dict[str, str] = dict(parsing["파싱실패사유"])

        self.blocked = [
            (item["case_id"], re.compile(item["pattern"]))
            for item in case["비규범표_캡션규칙"]
        ]


# ------------------------------------------------------------------ 표 단위 해석


def _grid(cells: Iterable[dict[str, str]]) -> dict[int, dict[int, str]]:
    grid: dict[int, dict[int, str]] = defaultdict(dict)
    for cell in cells:
        try:
            row = int(cell["행번호"])
            col = int(cell["열번호"])
        except (KeyError, ValueError):
            continue
        grid[row][col] = cell.get("값", "")
    return grid


_FOOTNOTE = re.compile(r"주?\d+\s*\)")


def _looks_numeric(rules: Rules, raw: str) -> bool:
    """헤더 블록 판정용. 괄호와 각주 표기를 걷어낸 뒤에도 숫자가 남는가."""
    stripped = rules.paren.sub(" ", raw)
    stripped = _FOOTNOTE.sub("", stripped)
    return bool(re.search(r"\d", stripped))


def _header_depth(rules: Rules, grid: dict[int, dict[int, str]], rows: list[int]) -> int:
    """선두 연속 비수치 행을 헤더 블록으로 본다. 최소 1행, 최대 3행."""
    depth = 1
    for index in rows[1:3]:
        values = [value for value in grid[index].values() if value.strip()]
        if not values:
            break
        if any(_looks_numeric(rules, value) for value in values):
            break
        depth += 1
    return depth


def _header_text(grid: dict[int, dict[int, str]], rows: list[int], depth: int, col: int) -> str:
    parts: list[str] = []
    for index in rows[:depth]:
        piece = _fold(grid[index].get(col, ""))
        if piece and piece not in parts:
            parts.append(piece)
    return "".join(parts)


def _metric_of(rules: Rules, header: str) -> tuple[str, bool] | None:
    """헤더 표제를 지표명으로 푼다. 두 번째 값은 값기준 층·높이 분기 여부."""
    hits: list[tuple[str, bool]] = []
    for metric, include, exclude, value_branch in rules.metric_rules:
        if exclude is not None and exclude.search(header):
            continue
        if include.search(header):
            hits.append((metric, value_branch))
    if not hits:
        return None
    hits.sort(key=lambda item: rules.metric_order.index(item[0]))
    return hits[0]


def _limit_of(rules: Rules, header: str, value: str) -> str:
    outside = rules.paren.sub(" ", value)
    if rules.limit_high_value.search(outside):
        return "최고"
    if rules.limit_low_value.search(outside):
        return "최저"
    if rules.limit_high_header.search(header):
        return "최고"
    if rules.limit_low_header.search(header):
        return "최저"
    return rules.limit_default


def _subject_type(rules: Rules, header: str) -> str:
    for subject_type, pattern in rules.subject_header_rules:
        if pattern.search(header):
            return subject_type
    return rules.subject_default


def _is_aggregate_row(rules: Rules, row: dict[int, str]) -> bool:
    """가장 왼쪽 비공란 셀이 집계 라벨이면 그 행 전체가 집계행이다."""
    for col in sorted(row):
        text = row[col].strip()
        if text:
            return bool(rules.aggregate.fullmatch(_fold(text)))
    return False


def _pick_subject_column(
    rules: Rules,
    headers: dict[int, str],
    metric_cols: set[int],
    data_rows: list[int],
    grid: dict[int, dict[int, str]],
) -> int | None:
    """지표열이 아닌 열 중 주체 후보를 고른다. 1순위 표제 → 2순위 표제 → 채움률."""
    entity_rows = [row for row in data_rows if not _is_aggregate_row(rules, grid[row])]
    if not entity_rows:
        return None
    candidates: list[tuple[int, str, float]] = []
    for col, header in sorted(headers.items()):
        if col in metric_cols or rules.subject_excluded.search(header):
            continue
        filled = 0
        carried = False
        for row in entity_rows:
            value = grid[row].get(col, "").strip()
            if value:
                if rules.aggregate.fullmatch(_fold(value)):
                    carried = False
                    continue
                filled += 1
                carried = True
            elif carried and rules.subject_fill_carry:
                filled += 1
        if not filled:
            continue
        candidates.append((col, header, filled / len(entity_rows)))
    if not candidates:
        return None
    for tier in rules.subject_tiers:
        preferred = [
            item for item in candidates
            if tier.search(item[1]) and item[2] >= rules.subject_fill_floor
        ]
        if preferred:
            return preferred[0][0]
    best = max(item[2] for item in candidates)
    return next(col for col, _, ratio in candidates if ratio == best)


_RANGE = re.compile(r"\d\s*[~〜∼-]\s*\d")


def _canonical_number(rules: Rules, text: str) -> str | None:
    """스키마의 값_수치 표기로 정규화한다. 불가능하면 None."""
    number = text
    if "." in number:
        number = number.rstrip("0").rstrip(".")
    number = re.sub(r"^0+(?=\d)", "", number)
    if not number:
        number = "0"
    return number if rules.numeric.fullmatch(number) else None


def _parse_value(rules: Rules, raw: str) -> tuple[str, str, str]:
    """값_원문에서 값_수치·단서·파싱실패사유를 뽑는다.

    수를 세는 것이 먼저다. 단위·한도어를 먼저 걷으면 `3층5층`이 `35`가 된다.
    """
    notes = [match.group(1).strip() for match in rules.condition.finditer(raw)]
    note = " / ".join(dict.fromkeys(notes))

    body = rules.paren.sub(" ", raw).replace(",", "")
    numbers = re.findall(r"\d+(?:\.\d+)?", body)

    if _RANGE.search(body):
        return "", note, rules.fail_reasons["범위값"]
    if not numbers:
        return "", note, rules.fail_reasons["비수치"]
    if len(numbers) > 1:
        return "", note, rules.fail_reasons["다중값"]

    residual = _fold(rules.strip_tokens.sub("", body))
    if residual != numbers[0]:
        return "", note, rules.fail_reasons["부가문자"]
    canonical = _canonical_number(rules, numbers[0])
    if canonical is None:
        return "", note, rules.fail_reasons["비수치"]
    return canonical, note, ""


def _resolve_height_metric(rules: Rules, raw: str) -> str:
    outside = rules.paren.sub(" ", raw)
    if rules.height_floor.search(outside):
        return "층수"
    if rules.height_meter.search(outside):
        return "높이"
    if rules.height_floor.search(raw):
        return "층수"
    return "높이"


# ----------------------------------------------------------------- 표 읽기 모드


class Tally:
    """표를 가로지르는 관측치. 판정에 쓰지 않고 리포트에만 쓴다."""

    def __init__(self) -> None:
        self.novalue_cells = 0
        self.aggregate_rows = 0


def _emit(
    rules: Rules,
    table: dict[str, str],
    subject: str,
    subject_type: str,
    metric: str,
    value_branch: bool,
    header: str,
    raw: str,
) -> dict[str, str]:
    resolved = _resolve_height_metric(rules, raw) if value_branch else metric
    numeric, note, reason = _parse_value(rules, raw)
    return {
        "지구번호": table.get("지구번호", ""),
        "주체": subject,
        "주체유형": subject_type,
        "지표명": resolved,
        "한도구분": _limit_of(rules, header, raw),
        "값_원문": raw,
        "값_수치": numeric,
        "단위": rules.unit_by_metric[resolved],
        "단서": note,
        "파싱실패사유": reason,
        "표ID": table["표ID"],
        "추출경로": table.get("추출경로", ""),
        "품질등급": table.get("품질등급", ""),
    }


def _skip_value(rules: Rules, raw: str, tally: Tally) -> bool:
    if not raw.strip():
        return True
    if _fold(raw) in rules.novalue:
        tally.novalue_cells += 1
        return True
    return False


def _read_by_header(
    rules: Rules,
    table: dict[str, str],
    grid: dict[int, dict[int, str]],
    row_indexes: list[int],
    tally: Tally,
    allow_unmarked: bool = False,
) -> tuple[list[dict[str, str]], str | None]:
    """열 표제가 지표인 통상 배치를 읽는다."""
    depth = _header_depth(rules, grid, row_indexes)
    data_rows = row_indexes[depth:]
    if not data_rows:
        return [], "헤더만존재"

    all_cols = sorted({col for index in row_indexes for col in grid[index]})
    headers = {col: _header_text(grid, row_indexes, depth, col) for col in all_cols}

    metric_by_col: dict[int, tuple[str, bool]] = {}
    for col in all_cols:
        resolved = _metric_of(rules, headers[col])
        if resolved is not None:
            metric_by_col[col] = resolved
    if not metric_by_col:
        return [], "지표열없음"

    subject_col = _pick_subject_column(rules, headers, set(metric_by_col), data_rows, grid)
    if subject_col is None and not allow_unmarked:
        return [], "주체열없음"
    subject_type = _subject_type(rules, headers[subject_col]) if subject_col is not None else rules.subject_default

    rows: list[dict[str, str]] = []
    carried = ""
    for row_index in data_rows:
        if _is_aggregate_row(rules, grid[row_index]):
            tally.aggregate_rows += 1
            continue
        subject_raw = grid[row_index].get(subject_col, "").strip() if subject_col is not None else ""
        if subject_raw:
            # 집계 라벨은 이월하지 않는다. 이월하면 아래 개체 행이 전부 집계로 접힌다.
            carried = "" if rules.aggregate.fullmatch(_fold(subject_raw)) else subject_raw
        subject = subject_raw or carried
        if not subject:
            if not allow_unmarked:
                continue
            subject = rules.subject_unmarked
        if rules.aggregate.fullmatch(_fold(subject)):
            tally.aggregate_rows += 1
            continue
        for col, (metric, value_branch) in sorted(metric_by_col.items()):
            raw = grid[row_index].get(col, "")
            if _skip_value(rules, raw, tally):
                continue
            rows.append(
                _emit(rules, table, subject, subject_type, metric, value_branch, headers[col], raw)
            )
    return rows, None if rows else "지표셀무값"


def _is_label(rules: Rules, text: str) -> bool:
    folded = _fold(text)
    if not folded or len(folded) > rules.t_label_max:
        return False
    return _metric_of(rules, folded) is not None or bool(rules.t_subject_label.search(folded))


def _pair_labels(rules: Rules, row: dict[int, str]) -> list[tuple[str, str]]:
    """분리자를 걷은 뒤 연속 열 구간으로 라벨 블록과 값 블록을 가른다."""
    live = [
        col for col in sorted(row)
        if row[col].strip() and row[col].strip() not in rules.t_separators
    ]
    if not live:
        return []
    runs: list[list[int]] = [[live[0]]]
    for col in live[1:]:
        if col == runs[-1][-1] + 1:
            runs[-1].append(col)
        else:
            runs.append([col])
    if len(runs) == 1:
        if len(runs[0]) != 2 or not _is_label(rules, row[runs[0][0]]):
            return []
        return [(row[runs[0][0]], row[runs[0][1]])]
    labels = [row[col] for col in runs[0]]
    values = [row[col] for col in runs[1]]
    if not any(_is_label(rules, label) for label in labels):
        return []
    return list(zip(labels, values))


def _read_transposed(
    rules: Rules,
    table: dict[str, str],
    grid: dict[int, dict[int, str]],
    row_indexes: list[int],
    tally: Tally,
) -> tuple[list[dict[str, str]], str | None]:
    """지표명이 셀 라벨로 있고 값이 오른쪽에 있는 전치 배치를 읽는다."""
    pairs_by_row = {index: _pair_labels(rules, grid[index]) for index in row_indexes}

    subject = ""
    subject_type = rules.subject_default
    for index in row_indexes:
        for label, value in pairs_by_row[index]:
            folded = _fold(label)
            if len(folded) > rules.t_label_max:
                continue
            if rules.t_subject_label.search(folded) and value.strip():
                if _fold(value) in rules.novalue:
                    continue
                subject = value.strip()
                subject_type = _subject_type(rules, folded)
                break
        if subject:
            break
    if not subject:
        return [], "전치_주체없음"
    if rules.aggregate.fullmatch(_fold(subject)):
        tally.aggregate_rows += 1
        return [], "전치_집계행"

    rows: list[dict[str, str]] = []
    for index in row_indexes:
        for label, raw in pairs_by_row[index]:
            resolved = _metric_of(rules, _fold(label))
            if resolved is None:
                continue
            if _skip_value(rules, raw, tally):
                continue
            metric, value_branch = resolved
            rows.append(
                _emit(rules, table, subject, subject_type, metric, value_branch, _fold(label), raw)
            )
    return rows, None if rows else "전치_지표없음"


# ------------------------------------------------------------------------ 생성


def normalize(
    rules: Rules,
    tables: list[dict[str, str]],
    cells_by_table: dict[str, list[dict[str, str]]],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    candidates = [table for table in tables if table.get("정규화대상") == "건축계획지표"]
    blocked_ids: dict[str, list[str]] = defaultdict(list)
    for case_id, pattern in rules.blocked:
        for table in candidates:
            if pattern.search(table.get("캡션원문", "")):
                blocked_ids[table["표ID"]].append(case_id)
    targets = [table for table in candidates if table["표ID"] not in blocked_ids]

    rows: list[dict[str, str]] = []
    isolated: list[dict[str, str]] = []
    mode_counts: dict[str, int] = {"열표제": 0, "전치": 0, "열표제_주체미표기": 0}
    tally = Tally()

    for table in sorted(targets, key=lambda item: item["표ID"]):
        table_id = table["표ID"]
        grid = _grid(cells_by_table.get(table_id, []))
        row_indexes = sorted(grid)
        if not row_indexes:
            isolated.append({"표ID": table_id, "지구번호": table.get("지구번호", ""), "사유": "셀없음"})
            continue

        table_rows, reason = _read_by_header(rules, table, grid, row_indexes, tally)
        mode = "열표제"
        if not table_rows:
            fallback_rows, fallback_reason = _read_transposed(rules, table, grid, row_indexes, tally)
            if fallback_rows:
                table_rows, reason, mode = fallback_rows, None, "전치"
            else:
                unmarked_rows, unmarked_reason = _read_by_header(
                    rules, table, grid, row_indexes, tally, allow_unmarked=True
                )
                if unmarked_rows:
                    table_rows, reason, mode = unmarked_rows, None, "열표제_주체미표기"
                else:
                    reason = f"{reason}/{fallback_reason}/{unmarked_reason}"

        if not table_rows:
            isolated.append({"표ID": table_id, "지구번호": table.get("지구번호", ""), "사유": reason or "미상"})
            continue
        mode_counts[mode] += 1
        rows.extend(table_rows)

    rows.sort(
        key=lambda row: (
            row["지구번호"],
            row["표ID"],
            row["주체"],
            row["지표명"],
            row["한도구분"],
            row["값_원문"],
        )
    )

    report = {
        "1층후보표수": len(candidates),
        "case배제표수": len(blocked_ids),
        "2층target표수": len(targets),
        "norm행수": len(rows),
        "norm행보유표수": len({row["표ID"] for row in rows}),
        "norm행보유지구수": len({row["지구번호"] for row in rows}),
        "target지구수": len({table.get("지구번호", "") for table in targets}),
        "값_수치보유행수": sum(1 for row in rows if row["값_수치"]),
        "파싱실패행수": sum(1 for row in rows if row["파싱실패사유"]),
        "단서보유행수": sum(1 for row in rows if row["단서"]),
        "지표명분포": _counts(row["지표명"] for row in rows),
        "한도구분분포": _counts(row["한도구분"] for row in rows),
        "주체유형분포": _counts(row["주체유형"] for row in rows),
        "파싱실패사유분포": _counts(row["파싱실패사유"] for row in rows if row["파싱실패사유"]),
        "읽기모드분포": mode_counts,
        "동일값중복행수": len(rows) - len({tuple(row.values()) for row in rows}),
        "동일값중복행수$뜻": "모든 필드가 같은 행의 초과분. 주체가 유일키가 아니라는 관측이지 오류가 아니다. 도면번호가 병합으로 비어 획지 축이 주택규모로 내려앉은 표에서 나온다. norm 행 수를 개체 수로 세지 않는다.",
        "무값표기셀수": tally.novalue_cells,
        "집계행제외수": tally.aggregate_rows,
        "격리표수": len(isolated),
        "격리사유분포": _counts(item["사유"] for item in isolated),
        "격리표": sorted(isolated, key=lambda item: item["표ID"]),
    }
    return rows, report


def _counts(values: Iterable[str]) -> dict[str, int]:
    counter: dict[str, int] = {}
    for value in values:
        counter[value] = counter.get(value, 0) + 1
    return dict(sorted(counter.items()))


# ------------------------------------------------------------------------ 출력


def _render_csv_bytes(columns: Sequence[str], rows: Sequence[dict[str, str]]) -> bytes:
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def _write_atomic(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return hashlib.sha256(payload).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--contract-dir", type=Path, default=DEFAULT_CONTRACT_DIR)
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE)
    parser.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 집계만 출력한다")
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    contract_dir = args.contract_dir.resolve()

    contract_path = contract_dir / "outputs.json"
    case_path = args.case.resolve()
    missing = [path for path in (contract_path, case_path) if not path.is_file()]
    if missing:
        for path in missing:
            print(f"  - 선행조건 누락: {path}")
        return 2

    contract = _load_json(contract_path)
    case = _load_json(case_path)
    try:
        rules = Rules(contract, case)
    except KeyError as exc:
        print(f"  - 계약에 2층생성규칙 없음: {exc}")
        return 2

    output_root = root / contract["output_root"]
    tables_path = output_root / "tables.csv"
    cells_path = output_root / "cells.csv"
    missing = [path for path in (tables_path, cells_path) if not path.is_file()]
    if missing:
        for path in missing:
            print(f"  - 선행조건 누락: {path}")
        return 2

    tables = _read_csv(tables_path)
    wanted = {
        table["표ID"] for table in tables if table.get("정규화대상") == "건축계획지표"
    }
    cells_by_table: dict[str, list[dict[str, str]]] = defaultdict(list)
    with cells_path.open(encoding="utf-8-sig", newline="") as handle:
        for cell in csv.DictReader(handle):
            table_id = cell.get("표ID", "")
            if table_id in wanted:
                cells_by_table[table_id].append(cell)

    rows, report = normalize(rules, tables, cells_by_table)

    payload = _render_csv_bytes(rules.columns, rows)
    report["csv_sha256"] = hashlib.sha256(payload).hexdigest()

    if not args.dry_run:
        _write_atomic(output_root / OUTPUT_CSV, payload)
        _write_atomic(
            output_root / OUTPUT_REPORT,
            (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )

    print(json.dumps({key: value for key, value in report.items() if key != "격리표"},
                     ensure_ascii=False, indent=2))
    print(f"격리표 {report['격리표수']}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
