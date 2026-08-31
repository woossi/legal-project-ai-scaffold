#!/usr/bin/env python3
"""발표 근거 파일의 해시와 형식별 규모를 직접 실측한다."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, TextIO


FORMAT_BY_SUFFIX = {
    ".csv": "csv",
    ".html": "html",
    ".json": "json",
    ".jsonl": "jsonl",
    ".md": "md",
    ".pdf": "pdf",
    ".png": "png",
    ".pptx": "pptx",
    ".svg": "svg",
    ".ttl": "ttl",
}


def sha256_file(path: Path) -> str:
    """파일 바이트의 SHA-256을 반환한다."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise ValueError("JSON pointer는 '/'로 시작해야 함")

    current = value
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(current, list):
                current = current[int(part)]
            else:
                current = current[part]
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise ValueError(f"JSON pointer를 찾을 수 없음: {pointer}") from error
    return current


def _count_value(value: Any) -> int | None:
    if isinstance(value, (dict, list)):
        return len(value)
    return None


def _file_format(source: Path) -> str:
    suffixes = [suffix.lower() for suffix in source.suffixes]
    if suffixes[-2:] == [".jsonl", ".gz"]:
        return "jsonl"
    return FORMAT_BY_SUFFIX.get(source.suffix.lower(), "other")


def _open_text(source: Path) -> TextIO:
    if source.suffix.lower() == ".gz":
        return gzip.open(source, "rt", encoding="utf-8")
    return source.open(encoding="utf-8")


def _measure_csv_value(source: Path, locator: str) -> int:
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames or []
        if locator == "@columns":
            return len(fieldnames)
        if locator == "@rows":
            return sum(1 for _ in reader)
        if locator.startswith("@distinct/"):
            column = locator.removeprefix("@distinct/")
            return len({row[column] for row in reader if row.get(column, "") != ""})
        if locator.startswith("@nonempty/"):
            column = locator.removeprefix("@nonempty/")
            return sum(1 for row in reader if row.get(column, "") != "")
    raise ValueError(f"지원하지 않는 CSV locator: {locator}")


def measure(path: Path | str, pointer: str | None = None) -> dict[str, Any]:
    """파일을 직접 읽어 SHA-256과 형식별 규모를 반환한다."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)

    file_format = _file_format(source)
    result: dict[str, Any] = {
        "path": str(source),
        "format": file_format,
        "bytes": source.stat().st_size,
        "sha256": sha256_file(source),
    }

    if file_format == "json":
        value = json.loads(source.read_text(encoding="utf-8"))
        selected = _resolve_pointer(value, pointer) if pointer is not None else value
        result["pointer"] = pointer
        count = _count_value(selected)
        if count is not None:
            result["count"] = count
        result["value_type"] = type(selected).__name__
    elif file_format == "csv":
        with source.open(encoding="utf-8-sig", newline="") as stream:
            rows = csv.reader(stream)
            header = next(rows, [])
            result["rows"] = sum(1 for _ in rows)
            result["columns"] = len(header)
            result["header"] = header
    elif file_format == "jsonl":
        with _open_text(source) as stream:
            result["records"] = sum(1 for line in stream if line.strip())

    return result


def measure_value(path: Path | str, locator: str) -> Any:
    """fact locator가 지정한 값을 원천 파일에서 다시 계산한다."""
    source = Path(path)
    file_format = _file_format(source)

    if file_format == "json":
        value = json.loads(source.read_text(encoding="utf-8"))
        selected = _resolve_pointer(value, locator)
        return len(selected) if isinstance(selected, (dict, list)) else selected
    if file_format == "csv":
        return _measure_csv_value(source, locator)
    if file_format == "jsonl" and locator == "@records":
        return measure(source)["records"]
    if locator == "@bytes":
        return source.stat().st_size
    raise ValueError(f"지원하지 않는 locator: {locator} ({file_format})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--pointer", help="실측할 RFC 6901 JSON pointer")
    args = parser.parse_args()

    try:
        result = measure(args.path, args.pointer)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
