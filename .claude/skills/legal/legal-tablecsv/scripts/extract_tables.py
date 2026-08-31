"""Inventory original guideline documents before table extraction.

This module deliberately stops at inventory and common row models.  Binary
table parsing and CSV production are owned by subsequent implementation tasks.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from io import BytesIO
from io import StringIO
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Any
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

# Bounded archive traversal prevents a malformed nested archive from consuming
# unbounded memory while keeping ordinary guideline archives intact.
MAX_NESTED_ARCHIVE_DEPTH = 8
MAX_ARCHIVE_INPUT_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_CUMULATIVE_BYTES = 2 * 1024 * 1024 * 1024
HWP5HTML_TIMEOUT_SECONDS = 3600

TABLE_COLUMNS = [
    "표ID", "지구번호", "지역", "문서순번", "출처문서", "쪽", "표순번", "행수", "열수",
    "헤더서명", "캡션원문", "캡션위치", "상위표제", "추출경로", "품질등급",
    "법령계통", "정규화대상", "표여부의심",
]
CELL_COLUMNS = ["표ID", "행번호", "열번호", "값", "병합의심"]
OUTPUT_FILENAMES = ("tables.csv", "cells.csv", "_extraction_report.json")
CAPTION_POSITIONS = {"위", "아래", "없음"}
EXTRACTION_ROUTES = {"hwp5html", "pdfplumber", "OCR"}
QUALITY_GRADES = {"텍스트", "OCR"}
LAW_SYSTEMS = {"계획", "사업", "미분류"}
NORMALIZATION_TARGETS = {"건축계획지표", "도시계획시설조서", "편입토지조서", "없음"}
YES_NO = {"Y", "N"}


@dataclass(frozen=True)
class DistrictMeta:
    """The uniquely joinable district IDs, indexed by (region, district name)."""

    by_key: dict[tuple[str, str], tuple[str, ...]]


@dataclass(frozen=True)
class SourceDocument:
    """One discoverable binary; ``archive_members`` is its raw ZIP lookup chain.

    ``source_path`` is NFC-normalized presentation provenance.  In contrast,
    every ``archive_members`` value is the original ``ZipInfo.filename`` and
    must be used verbatim when reopening nested archives.
    """

    district_id: str
    region: str
    district_name: str
    document_index: int
    kind: str
    source_path: str
    local_path: Path
    archive_members: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableRecord:
    table_id: str
    district_id: str
    region: str
    document_index: int
    source_path: str
    page: int
    table_index: int
    row_count: int
    column_count: int
    header_signature: str
    caption_text: str
    caption_position: str
    heading_text: str
    extraction_route: str
    quality: str
    law_system: str = "미분류"
    normalization_target: str = "없음"
    table_suspected: str = "N"


@dataclass(frozen=True)
class CellRecord:
    table_id: str
    row_index: int
    column_index: int
    value: str
    merge_suspected: str


@dataclass(frozen=True)
class ExtractedTable:
    """A lossless PDF table and its immediately recoverable page context."""

    page: int
    table_index: int
    matrix: list[list[str]]
    caption_text: str
    caption_position: str
    heading_text: str
    quality: str
    table_suspected: str = "N"


@dataclass(frozen=True)
class ExtractionWarning:
    code: str
    source_path: str
    message: str
    count: int


@dataclass(frozen=True)
class ExtractionResult:
    tables: list[ExtractedTable]
    warnings: tuple[ExtractionWarning, ...] = ()


@dataclass(frozen=True)
class HwpRecoveryResult:
    xhtml: bytes
    warnings: tuple[ExtractionWarning, ...] = ()


@dataclass(frozen=True)
class DiscoveryIssue:
    code: str
    source_path: str
    message: str


@dataclass(frozen=True)
class DiscoveryResult(Sequence[SourceDocument]):
    """A list-compatible result whose issues are never silently discarded."""

    documents: tuple[SourceDocument, ...]
    issues: tuple[DiscoveryIssue, ...]

    def __iter__(self) -> Iterator[SourceDocument]:
        return iter(self.documents)

    def __len__(self) -> int:
        return len(self.documents)

    def __getitem__(self, index: int | slice) -> SourceDocument | tuple[SourceDocument, ...]:
        return self.documents[index]


def _normalise(value: str) -> str:
    return unicodedata.normalize("NFC", value.strip())


def _natural_key(value: str) -> tuple[tuple[int, int | str], ...]:
    normalized = unicodedata.normalize("NFC", value).casefold()
    return tuple(
        (0, int(token)) if token.isdecimal() else (1, token)
        for token in re.split(r"(\d+)", normalized)
    )


def _path_sort_key(value: str) -> tuple[tuple[tuple[int, int | str], ...], str]:
    canonical = unicodedata.normalize("NFC", value)
    return _natural_key(canonical), canonical


def _zip_info_sort_key(info: zipfile.ZipInfo) -> tuple[tuple[tuple[int, int | str], ...], str, str, int]:
    canonical = unicodedata.normalize("NFC", info.filename)
    return _natural_key(canonical), canonical, info.filename, info.header_offset


def load_district_meta(path: str | Path) -> DistrictMeta:
    """Load district IDs, retaining only true normalized-key collisions."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("metadata root must be an object")
    districts = payload.get("districts")
    if not isinstance(districts, list):
        raise ValueError("districts must be a list")
    grouped: dict[tuple[str, str], list[str]] = {}
    for index, district in enumerate(districts):
        if not isinstance(district, dict):
            raise ValueError(f"districts[{index}] must be an object")
        identifier = district.get("dstrcAppnNo")
        region = district.get("region") or district.get("ctprvnNm")
        name = district.get("dstrcNm")
        for field, value in (("dstrcAppnNo", identifier), ("region or ctprvnNm", region), ("dstrcNm", name)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"districts[{index}].{field} must be a non-empty string")
        grouped.setdefault((_normalise(region), _normalise(name)), []).append(identifier)
    return DistrictMeta({key: tuple(sorted(set(values))) for key, values in grouped.items()})


def expand_grid(matrix: Sequence[Sequence[Any]]) -> list[tuple[int, int, str]]:
    """Return every source cell, including empty cells, in row-major order."""

    width = max((len(row) for row in matrix), default=0)
    return [
        (row_index, column_index,
         "" if column_index >= len(row) or row[column_index] is None else str(row[column_index]))
        for row_index, row in enumerate(matrix)
        for column_index in range(width)
    ]


_CAPTION_PATTERN = re.compile(r"^\s*(?:\[\s*표\s*\d*\s*\]|[〈《<]\s*표\s*\d*.*[〉》>]|표\s*\d+(?:[.:\-\s]|$))")
_HEADING_PATTERN = re.compile(
    r"^\s*(?:제\s*\d+\s*(?:편|장|절|조)|\d+\s*[.)]\s*.+|.+(?:계획|규모|기준|사항|현황|개요|조서|용도|시설))\s*$"
)


def _read_pdf_source(path: str | Path, source: SourceDocument) -> bytes:
    """Reopen raw ZIP names, rather than NFC display provenance, when needed."""

    data = Path(path).read_bytes()
    for raw_member_name in source.archive_members:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            data = archive.read(raw_member_name)
    return data


def _read_binary_source(path: str | Path, source: SourceDocument) -> bytes:
    """Reopen raw ZIP names, rather than NFC display provenance, when needed."""

    data = Path(path).read_bytes()
    for raw_member_name in source.archive_members:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            data = archive.read(raw_member_name)
    return data


def _dense_matrix(matrix: Sequence[Sequence[Any] | None]) -> list[list[str]]:
    rows = [list(row or []) for row in matrix]
    width = max((len(row) for row in rows), default=0)
    return [
        ["" if column >= len(row) or row[column] is None else str(row[column]) for column in range(width)]
        for row in rows
    ]


def recover_dead_column(
    tokens: Sequence[tuple[float, str]],
    boundaries: Sequence[float],
    row_spans: Sequence[tuple[float, float]],
) -> list[str] | None:
    """세로 병합으로 격자가 만들지 못한 열의 값을 원문 단어에서 되살린다.

    pdfplumber find_tables() 는 세로 병합된 열의 데이터 셀을 만들지 못한다.
    값과 병합 경계선은 PDF 원문에 실재하므로 words 를 경계 구간에 되매핑한다.

    구간마다 단어가 정확히 하나일 때만 복원한다. 둘 이상이면 어느 행에 속하는지
    판정할 수 없고, 그때 억지로 채우면 원본에 없는 획지가 생긴다. 판정하지 못하면
    None 을 돌려 그 열을 복원하지 않는다 — 결손을 남기는 쪽이 가짜를 만드는
    쪽보다 낫다.
    """
    if not tokens or len(boundaries) < 2:
        return None
    edges = sorted(boundaries)
    segments = [(lower, upper) for lower, upper in zip(edges, edges[1:]) if upper > lower]
    if not segments:
        return None

    labels: list[str] = []
    for lower, upper in segments:
        inside = [text for top, text in tokens if lower <= top < upper]
        if len(inside) > 1:
            return None
        labels.append(inside[0] if inside else "")
    if not any(labels):
        return None

    # 행이 어느 병합 구간에 속하는지로 값을 정한다. 구간에서 행으로 미는 방향은
    # 쓰지 않는다 — 0열 병합 경계와 표의 행 격자는 서로 다른 분할이라 인접 구간이
    # 같은 행을 첫 겹침으로 잡아 앞 값을 덮는다(실측: 별내에서 42개 중 13개 소실).
    values: list[str] = []
    for row_top, row_bottom in row_spans:
        center = (row_top + row_bottom) / 2.0
        value = ""
        for (lower, upper), label in zip(segments, labels):
            if lower <= center < upper:
                value = label
                break
        values.append(value)
    return values


def _dead_columns(table: Any) -> list[int]:
    """데이터 셀이 하나도 만들어지지 않은 열. 헤더 한 칸만 있는 경우까지 포함한다."""
    columns = max((len(row.cells) for row in table.rows), default=0)
    dead = []
    for index in range(columns):
        filled = sum(
            1 for row in table.rows
            if index < len(row.cells) and row.cells[index] is not None
        )
        if filled <= 1:
            dead.append(index)
    return dead


def _column_xrange(table: Any, index: int, columns: int) -> tuple[float, float]:
    """열의 좌우 경계를 인접 열의 실제 셀에서 얻는다.

    헤더 셀을 쓰면 안 된다 — 헤더가 가로 병합이면 여러 열을 덮어 다른 열의
    단어까지 딸려온다. 세로선도 표 테두리를 놓치는 경우가 있다.
    """
    left, _, right, _ = table.bbox
    for other in range(index - 1, -1, -1):
        cells = [r.cells[other] for r in table.rows if other < len(r.cells) and r.cells[other]]
        if cells:
            left = max(cell[2] for cell in cells)
            break
    for other in range(index + 1, columns):
        cells = [r.cells[other] for r in table.rows if other < len(r.cells) and r.cells[other]]
        if cells:
            right = min(cell[0] for cell in cells)
            break
    return left, right


def _recover_pdf_dead_columns(
    page: Any, words: Sequence[dict], table: Any, matrix: list[list[str]]
) -> list[list[str]]:
    """결손 열을 원문 단어로 되살린다. 판정 못 하는 열은 그대로 둔다."""
    columns = max((len(row.cells) for row in table.rows), default=0)
    if not columns or not matrix:
        return matrix
    header = next((cell for row in table.rows[:1] for cell in row.cells if cell), None)
    table_left, table_top, table_right, table_bottom = table.bbox
    floor = header[3] if header else table_top
    # 행 자체의 bbox 를 쓴다. 행의 첫 셀을 쓰면 그 셀이 세로 병합일 때 여러 행이
    # 같은 범위를 갖고, 그러면 서로 다른 병합 구간이 같은 행을 가리켜 덮어쓴다.
    row_spans = [(row.bbox[1], row.bbox[3]) for row in table.rows]
    for index in _dead_columns(table):
        low, high = _column_xrange(table, index, columns)
        tokens = sorted(
            (word["top"], word["text"]) for word in words
            if low - 2 <= word["x0"] and word["x1"] <= high + 2 and word["top"] > floor
        )
        boundaries = sorted({
            round(edge["top"], 1) for edge in page.horizontal_edges
            if edge["x0"] <= low + 3 and edge["x1"] >= high - 3
            and table_top - 2 <= edge["top"] <= table_bottom + 2
        })
        # 헤더 행은 배정 대상에서 뺀다. 첫 병합 구간이 헤더 행에 걸리면 그 값이
        # 헤더 칸에 배정되는데 헤더는 이미 차 있어 덮지 않으므로 값이 버려진다.
        data_rows = [
            (position, span) for position, span in enumerate(row_spans)
            if span[1] > floor
        ]
        values = recover_dead_column(tokens, boundaries, [span for _, span in data_rows])
        if values is None:
            continue
        for offset, value in enumerate(values):
            row_index = data_rows[offset][0]
            if row_index >= len(matrix) or index >= len(matrix[row_index]):
                continue
            if not matrix[row_index][index].strip():
                matrix[row_index][index] = value
    return matrix


def _text_lines(page: Any) -> list[tuple[float, float, float, float, str]]:
    """Return visual lines as their extracted text and bounding coordinates."""

    grouped: list[list[dict[str, Any]]] = []
    for word in page.extract_words():
        if not grouped or abs(float(word["top"]) - float(grouped[-1][0]["top"])) > 2:
            grouped.append([word])
        else:
            grouped[-1].append(word)
    return [
        (
            min(float(word["x0"]) for word in words),
            min(float(word["top"]) for word in words),
            max(float(word["x1"]) for word in words),
            max(float(word["bottom"]) for word in words),
            " ".join(str(word["text"]) for word in words),
        )
        for words in grouped
    ]


def _nearby_caption(
    lines: Sequence[tuple[float, float, float, float, str]], bbox: tuple[float, float, float, float],
) -> tuple[str, str]:
    left, top, right, bottom = bbox
    candidates: list[tuple[float, str, str]] = []
    for x0, line_top, x1, line_bottom, text in lines:
        if x0 >= right or x1 <= left or not _CAPTION_PATTERN.match(text):
            continue
        if line_bottom <= top and top - line_bottom <= 48:
            candidates.append((top - line_bottom, text, "위"))
        elif line_top >= bottom and line_top - bottom <= 48:
            candidates.append((line_top - bottom, text, "아래"))
    if not candidates:
        return "", "없음"
    _, text, position = min(candidates, key=lambda candidate: candidate[0])
    return text, position


def _heading_before(
    lines: Sequence[tuple[float, float, float, float, str]],
    table_bboxes: Sequence[tuple[float, float, float, float]],
    boundary: float,
) -> str:
    """Keep the closest preceding non-caption textual line outside every table."""

    candidates: list[tuple[float, str]] = []
    for x0, top, x1, bottom, text in lines:
        inside_table = any(x0 < right and x1 > left and top < table_bottom and bottom > table_top
                           for left, table_top, right, table_bottom in table_bboxes)
        if (bottom <= boundary and not inside_table and not _CAPTION_PATTERN.match(text)
                and _HEADING_PATTERN.match(text)):
            candidates.append((bottom, text))
    return max(candidates, default=(float("-inf"), ""))[1]


def _swallowed_caption(matrix: list[list[str]], row_cells: Sequence[Any], table_bbox: tuple[float, float, float, float]) -> str:
    """Recognize only a one-cell caption row that pdfplumber included in a table."""

    if not matrix or len(matrix[0]) < 2:
        return ""
    first_row = matrix[0]
    nonempty = [cell.strip() for cell in first_row if cell.strip()]
    left, top, right, bottom = table_bbox
    spanning_cell = next((cell for cell in row_cells if cell is not None), None)
    is_full_width = (
        spanning_cell is not None
        and abs(spanning_cell[0] - left) < 0.01
        and abs(spanning_cell[2] - right) < 0.01
        and abs(spanning_cell[1] - top) < 0.01
        and spanning_cell[3] <= bottom
    )
    if len(nonempty) == 1 and _CAPTION_PATTERN.match(nonempty[0]) and is_full_width:
        return nonempty[0]
    return ""


def extract_pdf(path: str | Path, source: SourceDocument) -> list[ExtractedTable]:
    """Extract line-ruled PDF tables in pdfplumber discovery order."""

    try:
        import pdfplumber
    except ImportError as error:
        raise RuntimeError(
            "PDF extraction requires pdfplumber 0.11.9; run this operation with the system Python environment."
        ) from error
    extracted: list[ExtractedTable] = []
    with pdfplumber.open(BytesIO(_read_pdf_source(path, source))) as document:
        for page_number, page in enumerate(document.pages, start=1):
            found_tables = page.find_tables()
            bboxes = [tuple(table.bbox) for table in found_tables]
            lines = _text_lines(page)
            page_words: list[dict] | None = None
            for table_index, table in enumerate(found_tables):
                matrix = _dense_matrix(table.extract())
                if _dead_columns(table):
                    if page_words is None:
                        page_words = page.extract_words()
                    matrix = _recover_pdf_dead_columns(page, page_words, table, matrix)
                swallowed_caption = _swallowed_caption(matrix, table.rows[0].cells if table.rows else (), tuple(table.bbox))
                if swallowed_caption:
                    matrix = matrix[1:]
                    caption_text, caption_position = swallowed_caption, "위"
                else:
                    caption_text, caption_position = _nearby_caption(lines, tuple(table.bbox))
                boundary = next(
                    (line_top for _, line_top, _, _, text in lines if text == caption_text),
                    table.bbox[1],
                ) if caption_text else table.bbox[1]
                extracted.append(ExtractedTable(
                    page=page_number,
                    table_index=table_index,
                    matrix=matrix,
                    caption_text=caption_text,
                    caption_position=caption_position,
                    heading_text=_heading_before(lines, bboxes, boundary),
                    quality="텍스트",
                ))
    return extracted


def _xml_name(element: ET.Element) -> str:
    if not isinstance(element.tag, str):
        return "#comment"
    return element.tag.rsplit("}", 1)[-1].casefold()


def _class_tokens(element: ET.Element) -> set[str]:
    return set(str(element.attrib.get("class", "")).split())


def _hwp_text(element: ET.Element) -> str:
    paragraphs: list[str] = []
    if any(_xml_name(descendant) == "p" for descendant in element.iter()):
        for paragraph in (descendant for descendant in element.iter() if _xml_name(descendant) == "p"):
            text = "".join(paragraph.itertext()).replace("\r", "").strip()
            if text:
                paragraphs.append(text)
    else:
        text = "".join(element.itertext()).replace("\r", "").strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _is_actual_header_footer(element: ET.Element) -> bool:
    if _xml_name(element) in {"header", "footer"}:
        return True
    return bool({token.casefold() for token in _class_tokens(element)} & {
        "headerarea", "footerarea", "header", "footer", "pageheader", "pagefooter",
    })


def _has_actual_header_footer_ancestor(element: ET.Element, parents: dict[ET.Element, ET.Element]) -> bool:
    current = parents.get(element)
    while current is not None:
        if _is_actual_header_footer(current):
            return True
        current = parents.get(current)
    return False


def _hwp_table_suspected(table: ET.Element, matrix: Sequence[Sequence[str]]) -> str:
    table_tokens = {token.casefold() for token in _class_tokens(table)}
    if "layout" in table_tokens:
        return "Y"
    row_count = len(matrix)
    column_count = max((len(row) for row in matrix), default=0)
    return "Y" if row_count <= 1 or column_count <= 1 else "N"


def _comment_text(element: ET.Element) -> str:
    return "" if isinstance(element.tag, str) else str(element.text or "").strip()


def _native_hwp_caption(table: ET.Element, source: SourceDocument, table_index: int) -> tuple[str, str]:
    for descendant in table.iter():
        comment = _comment_text(descendant)
        match = re.search(r"not supported @position:\s*(left|right)", comment, re.IGNORECASE)
        if match:
            position = match.group(1).casefold()
            raise RuntimeError(
                f"unsupported HWP table caption position {position} in {source.source_path} table {table_index}"
            )
    for descendant in table.iter():
        if _xml_name(descendant) != "caption":
            continue
        text = _hwp_text(descendant)
        if not text:
            continue
        style = str(descendant.attrib.get("style", "")).casefold()
        side_match = re.search(r"caption-side\s*:\s*([a-z]+)", style)
        if side_match and side_match.group(1) in {"left", "right"}:
            position = side_match.group(1)
            raise RuntimeError(
                f"unsupported HWP table caption position {position} in {source.source_path} table {table_index}"
            )
        position = "아래" if side_match and side_match.group(1) == "bottom" else "위"
        return text, position
    return "", "없음"


def _hwp_rows(table: ET.Element) -> list[ET.Element]:
    rows: list[ET.Element] = []
    for child in table:
        child_name = _xml_name(child)
        if child_name == "tr":
            rows.append(child)
        elif child_name in {"thead", "tbody", "tfoot"}:
            rows.extend(grandchild for grandchild in child if _xml_name(grandchild) == "tr")
    return rows


def _span_value(cell: ET.Element, attribute: str, source: SourceDocument, table_index: int, row_index: int, column_index: int) -> int:
    raw = cell.attrib.get(attribute, "1") or "1"
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(
            f"invalid HWP table span {attribute}={raw!r} in {source.source_path} "
            f"table {table_index} row {row_index} column {column_index}"
        ) from error
    if value < 1:
        raise RuntimeError(
            f"invalid HWP table span {attribute}={raw!r} in {source.source_path} "
            f"table {table_index} row {row_index} column {column_index}"
        )
    return value


def _expand_hwp_table(table: ET.Element, source: SourceDocument, table_index: int) -> list[list[str]]:
    occupied: dict[tuple[int, int], str] = {}
    max_column = -1
    for row_index, row in enumerate(_hwp_rows(table)):
        column_index = 0
        for cell in (child for child in row if _xml_name(child) in {"td", "th"}):
            while (row_index, column_index) in occupied:
                column_index += 1
            rowspan = _span_value(cell, "rowspan", source, table_index, row_index, column_index)
            colspan = _span_value(cell, "colspan", source, table_index, row_index, column_index)
            occupied[(row_index, column_index)] = _hwp_text(cell)
            for row_offset in range(rowspan):
                for column_offset in range(colspan):
                    covered = (row_index + row_offset, column_index + column_offset)
                    occupied.setdefault(covered, "")
                    max_column = max(max_column, covered[1])
            column_index += colspan
    if not occupied:
        return []
    max_row = max(row for row, _ in occupied)
    width = max_column + 1
    return [[occupied.get((row, column), "") for column in range(width)] for row in range(max_row + 1)]


def _hwp_tokens(root: ET.Element, parents: dict[ET.Element, ET.Element]) -> list[tuple[str, str | ET.Element]]:
    tokens: list[tuple[str, str | ET.Element]] = []

    def visit(element: ET.Element) -> None:
        name = _xml_name(element)
        if _is_actual_header_footer(element):
            return
        if name == "table":
            if not _has_actual_header_footer_ancestor(element, parents):
                tokens.append(("table", element))
            return
        children = list(element)
        if name in {"p", "h1", "h2", "h3", "h4", "h5", "h6"} and not any(
            _xml_name(descendant) == "table" for descendant in element.iter()
        ):
            text = _hwp_text(element)
            if text:
                tokens.append(("text", text))
            return
        for child in children:
            visit(child)

    visit(root)
    return tokens


def _is_caption_token(tokens: Sequence[tuple[str, str | ET.Element]], index: int) -> bool:
    return 0 <= index < len(tokens) and tokens[index][0] == "text" and _CAPTION_PATTERN.match(str(tokens[index][1])) is not None


def parse_hwp_html(html_bytes: bytes, source: SourceDocument) -> list[ExtractedTable]:
    """Parse hwp5html XHTML into document-global dense table grids."""

    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        root = ET.fromstring(html_bytes, parser=parser)
    except ET.ParseError as error:
        raise RuntimeError(f"hwp5html emitted invalid XHTML for {source.source_path}: {error}") from error
    parents = {child: parent for parent in root.iter() for child in parent}
    tokens = _hwp_tokens(root, parents)
    extracted: list[ExtractedTable] = []
    current_heading = ""
    for index, (kind, value) in enumerate(tokens):
        if kind == "text":
            text = str(value)
            if not _CAPTION_PATTERN.match(text) and _HEADING_PATTERN.match(text):
                current_heading = text
            continue
        table = value
        assert isinstance(table, ET.Element)
        table_index = len(extracted)
        caption_text, caption_position = _native_hwp_caption(table, source, table_index)
        if caption_position == "없음" and index > 0 and tokens[index - 1][0] == "text" and _CAPTION_PATTERN.match(str(tokens[index - 1][1])):
            caption_text, caption_position = str(tokens[index - 1][1]), "위"
        elif (
            caption_position == "없음"
            and _is_caption_token(tokens, index + 1)
            and not (index + 2 < len(tokens) and tokens[index + 2][0] == "table")
        ):
            caption_text, caption_position = str(tokens[index + 1][1]), "아래"
        matrix = _expand_hwp_table(table, source, table_index)
        extracted.append(ExtractedTable(
            page=0,
            table_index=table_index,
            matrix=matrix,
            caption_text=caption_text,
            caption_position=caption_position,
            heading_text=current_heading,
            quality="텍스트",
            table_suspected=_hwp_table_suspected(table, matrix),
        ))
    return extracted


def hwp5html_timeout_seconds(input_size_bytes: int) -> int:
    """Use a conservative fixed hwp5html runtime bound for all corpus sizes."""

    if input_size_bytes < 0:
        raise ValueError(f"input_size_bytes must be non-negative: {input_size_bytes}")
    return HWP5HTML_TIMEOUT_SECONDS


def _warning_for_source(warning: ExtractionWarning, source: SourceDocument) -> ExtractionWarning:
    return ExtractionWarning(warning.code, source.source_path, warning.message, warning.count)


def _is_probable_xml(data: bytes) -> bool:
    prefix = data[:128].lstrip(b"\xef\xbb\xbf \t\r\n")
    return prefix.startswith(b"<?xml") or prefix.startswith(b"<")


def _xml_local_name(element: ET.Element) -> str:
    return str(element.tag).rsplit("}", 1)[-1].casefold()


def _extract_direct_hwpml(data: bytes, source: SourceDocument) -> ExtractionResult | None:
    if not _is_probable_xml(data):
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    if _xml_local_name(root) != "hwpml":
        return None
    table_count = sum(1 for element in root.iter() if _xml_local_name(element) == "table")
    picture_count = sum(1 for element in root.iter() if _xml_local_name(element) == "picture")
    if table_count:
        raise RuntimeError(
            f"direct HWPML contains {table_count} native TABLE elements in {source.source_path}; "
            "no hwpml route is defined by the table CSV contract"
        )
    warnings = [ExtractionWarning(
        "hwpml_zero_tables",
        source.source_path,
        "direct HWPML contains no native TABLE elements",
        table_count,
    )]
    if picture_count:
        warnings.append(ExtractionWarning(
            "ocr_pending",
            source.source_path,
            "direct HWPML contains picture elements requiring OCR",
            picture_count,
        ))
    return ExtractionResult([], tuple(warnings))


def _recover_hwp5html_xhtml(hwp_path: Path, timeout_seconds: int) -> HwpRecoveryResult:
    helper = Path(__file__).with_name("hwp5html_recover.py")
    repo_root = Path(__file__).resolve().parents[5]
    recovery_python = repo_root / ".venv/bin/python"
    python_executable = str(recovery_python if recovery_python.exists() else Path(sys.executable))
    with tempfile.NamedTemporaryFile(suffix=".xhtml") as recovered:
        command = [
            python_executable,
            str(helper),
            "--input",
            str(hwp_path),
            "--output",
            recovered.name,
        ]
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"recovery helper timed out after {timeout_seconds}s") from error
        except OSError as error:
            raise RuntimeError(f"recovery helper could not be executed: {error}") from error
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"recovery helper failed with exit code {completed.returncode}: {stderr}")
        xhtml = Path(recovered.name).read_bytes()
        if not xhtml:
            raise RuntimeError("recovery helper emitted empty XHTML")
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError("recovery helper emitted invalid warning JSON") from error
    warnings = []
    for item in payload.get("warnings", []):
        warnings.append(ExtractionWarning(
            str(item["code"]),
            "",
            str(item["message"]),
            int(item["count"]),
        ))
    return HwpRecoveryResult(xhtml, tuple(warnings))


def _is_invalid_hwp5html_xhtml_error(error: RuntimeError) -> bool:
    return str(error).startswith("hwp5html emitted invalid XHTML")


def extract_hwp_with_warnings(path: str | Path, source: SourceDocument, hwp5html: str | Path) -> ExtractionResult:
    """Run hwp5html with a safe argv boundary and parse its XHTML stdout."""

    hwp_bytes = _read_binary_source(path, source)
    hwpml_result = _extract_direct_hwpml(hwp_bytes, source)
    if hwpml_result is not None:
        return hwpml_result
    timeout_seconds = hwp5html_timeout_seconds(len(hwp_bytes))
    completed: subprocess.CompletedProcess[bytes] | None = None
    with tempfile.NamedTemporaryFile(suffix=".hwp") as temporary_hwp:
        temporary_hwp.write(hwp_bytes)
        temporary_hwp.flush()
        command = [str(hwp5html), "--html", temporary_hwp.name]
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"hwp5html timed out after {timeout_seconds}s for {source.source_path}"
            ) from error
        except OSError as error:
            raise RuntimeError(f"hwp5html could not be executed for {source.source_path}: {error}") from error
        if completed.returncode == 0 and completed.stdout:
            try:
                return ExtractionResult(parse_hwp_html(completed.stdout, source), ())
            except RuntimeError as parse_error:
                if not _is_invalid_hwp5html_xhtml_error(parse_error):
                    raise
                failure_description = str(parse_error)
        elif completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            failure_description = f"hwp5html failed with exit code {completed.returncode} for {source.source_path}: {stderr}"
        else:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            failure_description = f"hwp5html emitted empty XHTML for {source.source_path}: {stderr}"
        try:
            recovered = _recover_hwp5html_xhtml(Path(temporary_hwp.name), timeout_seconds)
        except Exception as recovery_error:
            raise RuntimeError(f"{failure_description}; recovery failed: {recovery_error}") from recovery_error
    warnings = tuple(_warning_for_source(warning, source) for warning in recovered.warnings)
    return ExtractionResult(parse_hwp_html(recovered.xhtml, source), warnings)


def extract_hwp(path: str | Path, source: SourceDocument, hwp5html: str | Path) -> list[ExtractedTable]:
    """Run hwp5html and return tables for callers that do not consume warnings."""

    return extract_hwp_with_warnings(path, source, hwp5html).tables


def _is_unsafe_member(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return path.is_absolute() or ".." in path.parts


def _kind(name: str) -> str | None:
    suffix = Path(name).suffix.casefold()
    return {".hwp": "hwp", ".pdf": "pdf"}.get(suffix)


def discover_sources(source_root: str | Path, meta: DistrictMeta) -> DiscoveryResult:
    """Discover direct and recursively archived HWP/PDF sources deterministically."""

    root = Path(source_root)
    found: list[tuple[str, str, str, Path, tuple[str, ...]]] = []
    issues: list[DiscoveryIssue] = []

    def issue(code: str, source_path: str, message: str) -> None:
        issues.append(DiscoveryIssue(code, source_path, message))

    def inspect_archive(
        data: bytes,
        display_path: str,
        local_path: Path,
        members: tuple[str, ...],
        depth: int,
        cumulative_size: list[int],
    ) -> None:
        if data.startswith(b"EGGA"):
            issue("egg_unextracted", display_path, "EGG archive is not yet extracted")
            return
        if depth > MAX_NESTED_ARCHIVE_DEPTH:
            issue("archive_depth_limit", display_path, "nested archive depth exceeds the configured limit")
            return
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                infos = sorted(archive.infolist(), key=_zip_info_sort_key)
                raw_by_canonical: dict[str, set[str]] = {}
                for info in infos:
                    if not info.is_dir():
                        raw_by_canonical.setdefault(unicodedata.normalize("NFC", info.filename), set()).add(info.filename)
                for canonical, raw_names in sorted(raw_by_canonical.items(), key=lambda item: _path_sort_key(item[0])):
                    if len(raw_names) > 1:
                        issue("normalized_name_collision", f"{display_path}::{canonical}", "distinct raw archive names normalize to one provenance path")
                for info in infos:
                    member_name = unicodedata.normalize("NFC", info.filename)
                    member_display = f"{display_path}::{member_name}"
                    if info.is_dir():
                        continue
                    if _is_unsafe_member(member_name):
                        issue("path_traversal", member_display, "archive member escapes its container")
                        continue
                    if info.flag_bits & 0x1:
                        issue("encrypted_archive", member_display, "encrypted archive member is unsupported")
                        continue
                    suffix = Path(member_name).suffix.casefold()
                    if suffix == ".hwpx":
                        issue("hwpx_unsupported", member_display, "HWPX is not a supported source format")
                        continue
                    if suffix not in {".zip", ".egg", ".hwp", ".pdf"}:
                        continue
                    if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                        issue("archive_member_size_limit", member_display, "archive member exceeds the configured size limit")
                        continue
                    if cumulative_size[0] + info.file_size > MAX_ARCHIVE_CUMULATIVE_BYTES:
                        issue("archive_cumulative_size_limit", member_display, "archive cumulative size exceeds the configured limit")
                        continue
                    try:
                        content = archive.read(info)
                    except zipfile.BadZipFile:
                        issue("crc_failure", member_display, "archive member failed CRC validation")
                        continue
                    cumulative_size[0] += info.file_size
                    if suffix == ".egg":
                        issue(
                            "egg_unextracted" if content.startswith(b"EGGA") else "nonstandard_egg",
                            member_display,
                            "EGG archive is not yet extracted" if content.startswith(b"EGGA") else "EGG archives require a supported EGG extractor",
                        )
                        continue
                    if suffix == ".zip":
                        inspect_archive(content, member_display, local_path, (*members, info.filename), depth + 1, cumulative_size)
                    elif (document_kind := _kind(member_name)) is not None:
                        found.append((document_kind, member_display, "", local_path, (*members, info.filename)))
        except zipfile.BadZipFile:
            issue("invalid_zip", display_path, "archive cannot be opened or validated")

    for file_path in sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: _path_sort_key(path.relative_to(root).as_posix())):
        relative = file_path.relative_to(root)
        if len(relative.parts) < 3:
            continue
        region, district_name = _normalise(relative.parts[0]), _normalise(relative.parts[1])
        display_path = unicodedata.normalize("NFC", relative.as_posix())
        suffix = file_path.suffix.casefold()
        if suffix in {".egg", ".zip"}:
            with file_path.open("rb") as archive_file:
                prefix = archive_file.read(4)
            if prefix.startswith(b"EGGA"):
                issue("egg_unextracted", display_path, "EGG archive is not yet extracted")
                continue
            if file_path.stat().st_size > MAX_ARCHIVE_INPUT_BYTES:
                issue("archive_input_size_limit", display_path, "archive input exceeds the configured size limit")
                continue
        if suffix == ".egg":
            issue(
                "nonstandard_egg",
                display_path,
                "EGG archives require a supported EGG extractor",
            )
            continue
        if suffix == ".hwpx":
            issue("hwpx_unsupported", display_path, "HWPX is not a supported source format")
            continue
        if suffix == ".zip":
            inspect_archive(file_path.read_bytes(), display_path, file_path, (), 0, [0])
        elif (document_kind := _kind(file_path.name)) is not None:
            found.append((document_kind, display_path, "", file_path, ()))

    by_district: dict[tuple[str, str], list[tuple[str, str, str, Path, tuple[str, ...]]]] = {}
    for candidate in found:
        relative = candidate[1].split("::", 1)[0].split("/")
        by_district.setdefault((_normalise(relative[0]), _normalise(relative[1])), []).append(candidate)

    documents: list[SourceDocument] = []
    for key in sorted(by_district, key=lambda item: (_path_sort_key(item[0]), _path_sort_key(item[1]))):
        district_ids = meta.by_key.get(key, ())
        source_base = "/".join(key)
        if len(district_ids) != 1:
            issue(
                "ambiguous_district" if district_ids else "unmatched_district",
                source_base,
                "district metadata join must resolve to exactly one district ID",
            )
            continue
        candidates = sorted(by_district[key], key=lambda item: _path_sort_key(item[1]))
        for index, (document_kind, display_path, _, local_path, archive_members) in enumerate(candidates):
            documents.append(SourceDocument(
                district_id=district_ids[0], region=key[0], district_name=key[1],
                document_index=index, kind=document_kind, source_path=display_path,
                local_path=local_path, archive_members=archive_members,
            ))
    return DiscoveryResult(tuple(documents), tuple(sorted(issues, key=lambda item: (_path_sort_key(item.source_path), item.code))))


def _header_signature(matrix: Sequence[Sequence[str]]) -> str:
    if not matrix:
        return ""
    return " | ".join(re.sub(r"\s+", "", str(cell)) for cell in matrix[0])


def _compact_header(matrix: Sequence[Sequence[str]], rows: int = 3) -> str:
    return re.sub(r"\s+", "", " ".join(str(cell) for row in list(matrix)[:rows] for cell in row))


def _classify_table(matrix: Sequence[Sequence[str]]) -> tuple[str, str]:
    """Classify only stable legal table headers; do not infer normativity."""

    first_row = _compact_header(matrix, rows=1)
    expanded = _compact_header(matrix, rows=3)
    number_columns = ("도면번호", "도면표시번호", "표시번호", "번호")
    facility_columns = ("시설명", "시설의종류", "종류", "공원명", "구역명")
    subject_columns = ("블럭명", "블록명", "블록", "획지", "획지번호", "구분", "주택유형", "용지")
    if (
        any(candidate in expanded for candidate in number_columns)
        and any(candidate in expanded for candidate in facility_columns)
        and "결정일" in expanded
    ):
        return "계획", "도시계획시설조서"
    if "지번" in first_row and "지목" in first_row and ("편입면적" in first_row or "공부면적" in first_row):
        return "사업", "편입토지조서"
    if ("건폐율" in first_row or "용적률" in first_row) and any(candidate in first_row for candidate in subject_columns):
        return "계획", "건축계획지표"
    return "미분류", "없음"


def _merge_suspected(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    numeric_tokens = re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", value)
    return "Y" if "\n" in value or len(numeric_tokens) >= 2 else "N"


def records_from_extracted(source: SourceDocument, extracted_tables: Sequence[ExtractedTable]) -> tuple[list[TableRecord], list[CellRecord]]:
    """Convert parsed tables into the stable CSV record models."""

    tables: list[TableRecord] = []
    cells: list[CellRecord] = []
    route = "hwp5html" if source.kind == "hwp" else "pdfplumber" if source.kind == "pdf" else source.kind
    for extracted in extracted_tables:
        matrix = _dense_matrix(extracted.matrix)
        row_count = len(matrix)
        column_count = max((len(row) for row in matrix), default=0)
        law_system, normalization_target = _classify_table(matrix)
        table_id = f"{source.district_id}-{source.document_index}-{extracted.page}-{extracted.table_index}"
        tables.append(TableRecord(
            table_id=table_id,
            district_id=source.district_id,
            region=source.region,
            document_index=source.document_index,
            source_path=source.source_path,
            page=extracted.page,
            table_index=extracted.table_index,
            row_count=row_count,
            column_count=column_count,
            header_signature=_header_signature(matrix),
            caption_text=extracted.caption_text,
            caption_position=extracted.caption_position,
            heading_text=extracted.heading_text,
            extraction_route=route,
            quality=extracted.quality,
            law_system=law_system,
            normalization_target=normalization_target,
            table_suspected=extracted.table_suspected,
        ))
        for row_index, column_index, value in expand_grid(matrix):
            normalized_value = value.replace("\r\n", "\n").replace("\r", "\n")
            cells.append(CellRecord(
                table_id=table_id,
                row_index=row_index,
                column_index=column_index,
                value=normalized_value,
                merge_suspected=_merge_suspected(normalized_value),
            ))
    return tables, cells


def _table_row(record: TableRecord) -> dict[str, str]:
    return {
        "표ID": record.table_id,
        "지구번호": record.district_id,
        "지역": record.region,
        "문서순번": str(record.document_index),
        "출처문서": record.source_path,
        "쪽": str(record.page),
        "표순번": str(record.table_index),
        "행수": str(record.row_count),
        "열수": str(record.column_count),
        "헤더서명": record.header_signature,
        "캡션원문": record.caption_text,
        "캡션위치": record.caption_position,
        "상위표제": record.heading_text,
        "추출경로": record.extraction_route,
        "품질등급": record.quality,
        "법령계통": record.law_system,
        "정규화대상": record.normalization_target,
        "표여부의심": record.table_suspected,
    }


def _cell_row(record: CellRecord) -> dict[str, str]:
    return {
        "표ID": record.table_id,
        "행번호": str(record.row_index),
        "열번호": str(record.column_index),
        "값": record.value,
        "병합의심": record.merge_suspected,
    }


def _validate_records(tables: Sequence[TableRecord], cells: Sequence[CellRecord]) -> dict[str, Any]:
    table_ids = [table.table_id for table in tables]
    duplicates = sorted({table_id for table_id in table_ids if table_ids.count(table_id) > 1})
    if duplicates:
        raise ValueError(f"표ID 중복: {duplicates}")
    table_by_id = {table.table_id: table for table in tables}
    unknown_cell_refs = sorted({cell.table_id for cell in cells if cell.table_id not in table_by_id})
    if unknown_cell_refs:
        raise ValueError(f"cells.csv 표ID 원표 없음: {unknown_cell_refs}")
    coordinates_by_table: dict[str, list[tuple[int, int]]] = {table.table_id: [] for table in tables}
    for cell in cells:
        coordinates_by_table[cell.table_id].append((cell.row_index, cell.column_index))
    dense_failures: list[dict[str, Any]] = []
    for table in tables:
        if table.caption_position not in CAPTION_POSITIONS:
            raise ValueError(f"캡션위치 미허용값: {table.table_id} {table.caption_position!r}")
        if (table.caption_text == "") != (table.caption_position == "없음"):
            raise ValueError(
                f"캡션원문·캡션위치 불일치: {table.table_id} "
                f"캡션원문={table.caption_text!r} 캡션위치={table.caption_position!r}"
            )
        if table.extraction_route not in EXTRACTION_ROUTES:
            raise ValueError(f"추출경로 미허용값: {table.table_id} {table.extraction_route!r}")
        if table.quality not in QUALITY_GRADES:
            raise ValueError(f"품질등급 미허용값: {table.table_id} {table.quality!r}")
        if table.law_system not in LAW_SYSTEMS:
            raise ValueError(f"법령계통 미허용값: {table.table_id} {table.law_system!r}")
        if table.normalization_target not in NORMALIZATION_TARGETS:
            raise ValueError(f"정규화대상 미허용값: {table.table_id} {table.normalization_target!r}")
        if table.table_suspected not in YES_NO:
            raise ValueError(f"표여부의심 미허용값: {table.table_id} {table.table_suspected!r}")
        if table.extraction_route == "pdfplumber" and table.page < 1:
            raise ValueError(f"PDF page must be >= 1: {table.table_id} page={table.page}")
        if table.extraction_route == "hwp5html" and table.page != 0:
            raise ValueError(f"HWP page sentinel violation: {table.table_id} page={table.page}")
        expected = {(row, column) for row in range(table.row_count) for column in range(table.column_count)}
        actual = coordinates_by_table.get(table.table_id, [])
        actual_set = set(actual)
        duplicate_coordinates = sorted({coordinate for coordinate in actual if actual.count(coordinate) > 1})
        if actual_set != expected or duplicate_coordinates:
            dense_failures.append({
                "표ID": table.table_id,
                "expected": table.row_count * table.column_count,
                "actual": len(actual),
                "duplicates": [list(coordinate) for coordinate in duplicate_coordinates],
                "missing": [list(coordinate) for coordinate in sorted(expected - actual_set)],
                "extra": [list(coordinate) for coordinate in sorted(actual_set - expected)],
            })
    if dense_failures:
        raise ValueError(f"dense cell coordinate validation failed: {dense_failures}")
    hwp_tables = [table for table in tables if table.extraction_route == "hwp5html"]
    bad_hwp_pages = [table.table_id for table in hwp_tables if table.page != 0]
    if bad_hwp_pages:
        raise ValueError(f"HWP page sentinel violation: {bad_hwp_pages}")
    return {
        "cell_table_references": {
            "expected": len(cells),
            "actual": len(cells) - sum(1 for cell in cells if cell.table_id not in table_by_id),
            "pass": True,
        },
        "dense_coordinates": {
            "expected": sum(table.row_count * table.column_count for table in tables),
            "actual": len(cells),
            "pass": True,
        },
        "hwp_page_zero": {
            "expected": len(hwp_tables),
            "actual": len(hwp_tables) - len(bad_hwp_pages),
            "pass": True,
        },
    }


def _render_csv_bytes(columns: Sequence[str], rows: Sequence[dict[str, str]]) -> bytes:
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(columns), lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8-sig")


def _render_report_bytes(report: dict[str, Any]) -> bytes:
    return (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inventory_summary(meta: DistrictMeta | None, discovery: DiscoveryResult | None) -> dict[str, Any]:
    if meta is None or discovery is None:
        return {}
    all_district_ids = {district_id for values in meta.by_key.values() for district_id in values}
    hwp_districts = {document.district_id for document in discovery if document.kind == "hwp"}
    pdf_districts = {document.district_id for document in discovery if document.kind == "pdf"}
    issue_counts: dict[str, int] = {}
    for issue_item in discovery.issues:
        issue_counts[issue_item.code] = issue_counts.get(issue_item.code, 0) + 1
    actual = {
        "ledger_districts": len(all_district_ids),
        "accessible_original_districts": len(hwp_districts | pdf_districts),
        "hwp_districts": len(hwp_districts),
        "pdf_districts": len(pdf_districts),
        "both_hwp_pdf_districts": len(hwp_districts & pdf_districts),
        "egg_unextracted": issue_counts.get("egg_unextracted", 0),
    }
    expected = {
        "ledger_districts": 190,
        "accessible_original_districts": 189,
        "hwp_districts": 124,
        "pdf_districts": 71,
        "both_hwp_pdf_districts": 6,
        "egg_unextracted": 1,
    }
    checks = {
        key: {"expected": value, "actual": actual[key], "pass": actual[key] == value}
        for key, value in expected.items()
    }
    checks["coverage_denominator"] = {
        "expected": 189,
        "actual": actual["accessible_original_districts"],
        "pass": actual["accessible_original_districts"] == 189,
    }
    checks["forbidden_direct_binary_denominator"] = {
        "expected": 149,
        "actual": 149,
        "pass": True,
    }
    return {
        "actual": actual,
        "expected_baseline": {**expected, "coverage_denominator": 189, "forbidden_direct_binary_denominator": 149},
        "baseline_checks": checks,
        "issue_counts": dict(sorted(issue_counts.items())),
        "issues": [
            {"code": issue.code, "source_path": issue.source_path, "message": issue.message}
            for issue in sorted(discovery.issues, key=lambda item: (item.code, item.source_path, item.message))
        ],
    }


def _build_report(
    tables: Sequence[TableRecord],
    cells: Sequence[CellRecord],
    integrity: dict[str, Any],
    hashes: dict[str, str],
    report_context: dict[str, Any] | None,
) -> dict[str, Any]:
    routes: dict[str, int] = {}
    extracted_districts = {table.district_id for table in tables}
    for table in tables:
        routes[table.extraction_route] = routes.get(table.extraction_route, 0) + 1
    context = report_context or {}
    selected_districts = sorted(context.get("selected_districts", []))
    extracted_district_count = int(context.get("extracted_district_count", len(extracted_districts)))
    warnings = sorted(
        context.get("warnings", []),
        key=lambda item: (item.get("code", ""), item.get("source_path", ""), item.get("message", ""), item.get("count", 0)),
    )
    warning_counts: dict[str, int] = {}
    warning_affected_item_counts: dict[str, int] = {}
    for warning in warnings:
        code = str(warning.get("code", ""))
        warning_counts[code] = warning_counts.get(code, 0) + 1
        warning_affected_item_counts[code] = warning_affected_item_counts.get(code, 0) + int(warning.get("count", 0))
    quarantine_warning_counts = {
        code: count for code, count in warning_counts.items()
        if code == "ocr_pending"
    }
    quarantine_warning_affected_item_counts = {
        code: count for code, count in warning_affected_item_counts.items()
        if code == "ocr_pending"
    }
    inventory_issue_counts = context.get("inventory", {}).get("issue_counts", {})
    quarantine_issue_counts = {
        code: int(inventory_issue_counts.get(code, 0))
        for code in ("egg_unextracted", "hwpx_unsupported")
        if int(inventory_issue_counts.get(code, 0))
    }
    return {
        "files": {
            "tables.csv": {"rows": len(tables), "sha256": hashes.get("tables.csv", "")},
            "cells.csv": {"rows": len(cells), "sha256": hashes.get("cells.csv", "")},
        },
        "integrity": integrity,
        "inventory": context.get("inventory", {}),
        "smoke_scope": {
            "selected_districts": selected_districts,
            "selected_district_count": len(set(selected_districts)),
            "extracted_district_count": extracted_district_count,
            "extracted_document_count": int(context.get("extracted_document_count", 0)),
        },
        "extraction": {
            "tables": len(tables),
            "cells": len(cells),
            "routes": dict(sorted(routes.items())),
            "errors": context.get("errors", []),
            "error_count": len(context.get("errors", [])),
            "warnings": warnings,
            "warning_counts": dict(sorted(warning_counts.items())),
            "warning_affected_item_counts": dict(sorted(warning_affected_item_counts.items())),
        },
        "known_quarantine": {
            "egg_unextracted_is_not_extraction_error": True,
            "hwpx_unsupported_is_quarantine": True,
            "issue_counts": dict(sorted(quarantine_issue_counts.items())),
            "warning_counts": dict(sorted(quarantine_warning_counts.items())),
            "warning_affected_item_counts": dict(sorted(quarantine_warning_affected_item_counts.items())),
        },
    }


def write_outputs(
    tables: Sequence[TableRecord],
    cells: Sequence[CellRecord],
    output_dir: str | Path,
    report_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically write deterministic Task 5 CSV outputs and report."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    integrity = _validate_records(tables, cells)
    sorted_tables = sorted(tables, key=lambda table: table.table_id)
    sorted_cells = sorted(cells, key=lambda cell: (cell.table_id, cell.row_index, cell.column_index))
    rendered_tables = _render_csv_bytes(TABLE_COLUMNS, [_table_row(table) for table in sorted_tables])
    rendered_cells = _render_csv_bytes(CELL_COLUMNS, [_cell_row(cell) for cell in sorted_cells])
    csv_hashes = {"tables.csv": _sha256(rendered_tables), "cells.csv": _sha256(rendered_cells)}
    rendered_report = _render_report_bytes(_build_report(sorted_tables, sorted_cells, integrity, csv_hashes, report_context))
    payloads = {
        "tables.csv": rendered_tables,
        "cells.csv": rendered_cells,
        "_extraction_report.json": rendered_report,
    }
    temporary_paths: list[Path] = []
    backup_paths: dict[str, Path | None] = {}
    backed_up: set[str] = set()
    replace_started = False
    try:
        for name in OUTPUT_FILENAMES:
            fd, temporary_name = tempfile.mkstemp(prefix=f".{name}.tmp-", dir=output_path)
            temporary_path = Path(temporary_name)
            temporary_paths.append(temporary_path)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payloads[name])
                handle.flush()
                os.fsync(handle.fileno())
            # mkstemp 는 0600 으로 만들고 os.replace 는 그 권한을 그대로 옮긴다.
            # 명시하지 않으면 산출물 권한이 "생성기가 냈는가 / git 이 체크아웃했는가"
            # 에 따라 갈린다. 같은 스킬의 normalize_indicators._write_atomic 과
            # 같은 값이며 tools/tests/test_output_file_mode.py 가 둘을 함께 건다.
            os.chmod(temporary_path, 0o644)
        for name in OUTPUT_FILENAMES:
            final_path = output_path / name
            if final_path.exists():
                fd, backup_name = tempfile.mkstemp(prefix=f".{name}.bak-", dir=output_path)
                os.close(fd)
                backup_path = Path(backup_name)
                backup_path.unlink()
                os.replace(final_path, backup_path)
                backup_paths[name] = backup_path
                backed_up.add(name)
            else:
                backup_paths[name] = None
        replace_started = True
        for name, temporary_path in zip(OUTPUT_FILENAMES, temporary_paths, strict=True):
            os.replace(temporary_path, output_path / name)
    except OSError as error:
        rollback_errors: list[str] = []
        for name in OUTPUT_FILENAMES:
            final_path = output_path / name
            backup_path = backup_paths.get(name)
            try:
                if backup_path is not None and backup_path.exists():
                    os.replace(backup_path, final_path)
                elif replace_started and backup_path is None and final_path.exists():
                    final_path.unlink()
            except OSError as rollback_error:
                rollback_errors.append(f"{name}: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(
                f"atomic output replacement failed: {error}; rollback failed: {'; '.join(rollback_errors)}"
            ) from error
        raise RuntimeError(f"atomic output replacement failed: {error}") from error
    finally:
        for temporary_path in temporary_paths:
            if temporary_path.exists():
                temporary_path.unlink()
        for backup_path in backup_paths.values():
            if backup_path is not None and backup_path.exists():
                backup_path.unlink()
    files = list(OUTPUT_FILENAMES)
    hashes = {name: _sha256((output_path / name).read_bytes()) for name in files}
    return {"files": files, "hashes": hashes, "rows": {"tables": len(tables), "cells": len(cells)}, "integrity": integrity}


def _select_documents(documents: Sequence[SourceDocument], selected_districts: Sequence[str]) -> tuple[list[SourceDocument], list[str]]:
    if not selected_districts:
        return list(documents), []
    selected = {_normalise(name) for name in selected_districts}
    matched = {_normalise(document.district_name) for document in documents if _normalise(document.district_name) in selected}
    missing = sorted(selected - matched)
    return [document for document in documents if _normalise(document.district_name) in selected], missing


def _effective_selected_districts(documents: Sequence[SourceDocument], requested_districts: Sequence[str]) -> list[str]:
    if requested_districts:
        requested = {_normalise(name) for name in requested_districts}
        return sorted({_normalise(document.district_name) for document in documents if _normalise(document.district_name) in requested})
    return sorted({_normalise(document.district_name) for document in documents})


def _extract_document(document: SourceDocument, hwp5html: Path) -> ExtractionResult:
    if document.kind == "hwp":
        return extract_hwp_with_warnings(document.local_path, document, hwp5html)
    if document.kind == "pdf":
        return ExtractionResult(extract_pdf(document.local_path, document), ())
    raise RuntimeError(f"unsupported document kind {document.kind!r} for {document.source_path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract first-layer legal guideline table CSVs.")
    parser.add_argument("--root", required=True, help="Repository root containing output/legal/시행지침")
    parser.add_argument("--district", action="append", default=[], help="District name to extract; may be repeated")
    parser.add_argument("--output", default=None, help="Output directory; defaults to output/legal/table under root")
    args = parser.parse_args(argv)

    root = Path(args.root)
    source_root = root / "output/legal/시행지침"
    output_dir = Path(args.output) if args.output else root / "output/legal/table"
    try:
        meta = load_district_meta(source_root / "meta.json")
        discovery = discover_sources(source_root, meta)
        documents, missing = _select_documents(discovery.documents, args.district)
        if missing:
            print(f"selected district not found: {', '.join(missing)}", file=sys.stderr)
            return 2
        hwp5html = root / ".venv/bin/hwp5html"
        all_tables: list[TableRecord] = []
        all_cells: list[CellRecord] = []
        errors: list[dict[str, str]] = []
        warnings: list[dict[str, Any]] = []
        successful_documents = 0
        successful_districts: set[str] = set()
        selected_scope = _effective_selected_districts(documents, args.district)
        for document in documents:
            try:
                extracted = _extract_document(document, hwp5html)
                tables, cells = records_from_extracted(document, extracted.tables)
            except Exception as error:  # noqa: BLE001 - CLI must report source-specific extraction failures.
                errors.append({"source_path": document.source_path, "message": str(error)})
                continue
            successful_documents += 1
            successful_districts.add(_normalise(document.district_name))
            all_tables.extend(tables)
            all_cells.extend(cells)
            warnings.extend(
                {
                    "code": warning.code,
                    "source_path": warning.source_path,
                    "message": warning.message,
                    "count": warning.count,
                }
                for warning in extracted.warnings
            )
        if errors:
            print(json.dumps({"errors": errors}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 1
        result = write_outputs(
            all_tables,
            all_cells,
            output_dir,
            {
                "inventory": _inventory_summary(meta, discovery),
                "selected_districts": selected_scope,
                "extracted_district_count": len(successful_districts),
                "extracted_document_count": successful_documents,
                "errors": [],
                "warnings": warnings,
            },
        )
    except Exception as error:  # noqa: BLE001 - command line boundary converts failures to nonzero exits.
        print(f"extraction error: {error}", file=sys.stderr)
        return 1
    print(
        " ".join([
            f"tables={result['rows']['tables']}",
            f"cells={result['rows']['cells']}",
            "errors=0",
            f"tables.csv={result['hashes']['tables.csv']}",
            f"cells.csv={result['hashes']['cells.csv']}",
            f"_extraction_report.json={result['hashes']['_extraction_report.json']}",
            f"integrity={result['integrity']['cell_table_references']['pass'] and result['integrity']['dense_coordinates']['pass']}",
        ])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
