"""Recover pyhwp XHTML through the same XML-to-XSLT hwp5html route."""

from __future__ import annotations

import argparse
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import sys


def _sanitize_xml_text(value: str) -> tuple[str, int]:
    cleaned: list[str] = []
    removed = 0
    for char in value:
        codepoint = ord(char)
        if (
            char in "\t\n\r"
            or 0x20 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        ):
            cleaned.append(char)
        else:
            removed += 1
    return "".join(cleaned), removed


def _dump_xhwp5(input_path: Path) -> tuple[bytes, int]:
    import hwp5.dataio as dataio
    from hwp5.xmlmodel import Hwp5File

    replacement_count = 0
    original_decode = dataio.decode_utf16le_with_hypua

    def tolerant_decode(raw: bytes) -> str:
        nonlocal replacement_count
        try:
            return original_decode(raw)
        except UnicodeDecodeError:
            decoded = raw.decode("utf-16le", errors="replace")
            replacement_count += decoded.count("\ufffd")
            return decoded

    dataio.decode_utf16le_with_hypua = tolerant_decode
    try:
        with closing(Hwp5File(str(input_path))) as hwp5file:
            output = BytesIO()
            hwp5file.xmlevents(embedbin=False).dump(output)
            return output.getvalue(), replacement_count
    finally:
        dataio.decode_utf16le_with_hypua = original_decode


def recover(input_path: Path, output_path: Path) -> list[dict[str, int | str]]:
    from hwp5.hwp5html import HTMLTransform

    xml_bytes, replacement_count = _dump_xhwp5(input_path)
    xml_text = xml_bytes.decode("utf-8", errors="strict")
    sanitized_xml, removed_count = _sanitize_xml_text(xml_text)
    xml_path = output_path.with_name(output_path.name + ".xhwp5.xml")
    xml_path.write_bytes(sanitized_xml.encode("utf-8"))
    try:
        with output_path.open("wb") as output:
            HTMLTransform().transform_xhwp5_to_xhtml(str(xml_path), output)
    finally:
        try:
            xml_path.unlink()
        except FileNotFoundError:
            pass
    warnings: list[dict[str, int | str]] = []
    if removed_count:
        warnings.append({
            "code": "hwp5html_xml_sanitized",
            "message": "removed XML-illegal codepoints",
            "count": removed_count,
        })
    if replacement_count:
        warnings.append({
            "code": "hwp5html_utf16_replaced",
            "message": "replaced malformed UTF-16 text chunks",
            "count": replacement_count,
        })
    return warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover hwp5html XHTML through pyhwp XML/XSLT.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    warnings = recover(Path(args.input), Path(args.output))
    print(json.dumps({"warnings": warnings}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
