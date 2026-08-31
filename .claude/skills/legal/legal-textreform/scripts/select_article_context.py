#!/usr/bin/env python3
"""원 시행지침 조문 corpus에서 필요한 단위만 선택해 에이전트 컨텍스트를 만든다.

Markdown 원문은 수정하지 않는다. `guideline_source_article_corpus.jsonl.gz`의
줄범위와 SHA provenance를 그대로 전달해 전 파일 로드 비용을 피한다.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[5]
DEFAULT_CORPUS = ROOT / "output/legal/statute/guideline_source_article_corpus.jsonl.gz"
TEXT_FIELDS = (
    "source_unit_id",
    "district",
    "source_file",
    "article_seq",
    "article_title",
    "scope_origin",
    "line_start",
    "line_end",
    "body_line_count",
    "char_count",
    "text_sha256",
)


def estimate_tokens(chars: int) -> int:
    """로컬 tokenizer가 없을 때 쓰는 보수적 근사치.

    한국어와 Markdown이 섞인 문서는 모델별 tokenizer 편차가 크므로, 이 값은
    비교용 추정치다. 동일 산식으로 전후만 비교한다.
    """
    return math.ceil(chars / 3)


def read_jsonl_gz(path: Path) -> Iterable[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _matches(row: dict, *, district=None, source_file=None, article_seq=None,
             article_title=None, source_unit_id=None, contains=None) -> bool:
    if district is not None and row.get("district") != district:
        return False
    if source_file is not None and row.get("source_file") != source_file:
        return False
    if article_seq is not None and row.get("article_seq") != article_seq:
        return False
    if article_title is not None and article_title not in row.get("article_title", ""):
        return False
    if source_unit_id is not None and row.get("source_unit_id") != source_unit_id:
        return False
    if contains is not None and contains not in row.get("text", ""):
        return False
    return True


def _article_payload(row: dict, include_text: bool) -> dict:
    out = {field: row.get(field) for field in TEXT_FIELDS if field in row}
    if include_text:
        out["text"] = row.get("text", "")
    return out


def _resolve_source(path_text: str, root: Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return root / path


def verify_selected_articles(articles: list[dict], root: Path) -> list[str]:
    failures = []
    cache: dict[str, list[str]] = {}
    for row in articles:
        unit_id = row.get("source_unit_id", "<unknown>")
        text = row.get("text", "")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != row.get("text_sha256"):
            failures.append(f"SHA 불일치: {unit_id}")
        source_file = row.get("source_file")
        if not source_file:
            failures.append(f"source_file 없음: {unit_id}")
            continue
        try:
            source_path = _resolve_source(source_file, root).resolve()
            source_path.relative_to(root.resolve())
        except ValueError:
            failures.append(f"작업공간 밖 source_file: {source_file}")
            continue
        except OSError as exc:
            failures.append(f"source_file 해석 실패: {source_file}: {exc}")
            continue
        if source_file not in cache:
            try:
                cache[source_file] = source_path.read_text(
                    encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                failures.append(f"source_file 읽기 실패: {source_file}: {exc}")
                continue
        start, end = row.get("line_start"), row.get("line_end")
        lines = cache[source_file]
        if not isinstance(start, int) or not isinstance(end, int) or not (1 <= start <= end <= len(lines)):
            failures.append(f"줄범위 오류: {unit_id}: {start}~{end}")
            continue
        if "\n".join(lines[start - 1:end]) != text:
            failures.append(f"Markdown 줄범위 본문 불일치: {unit_id}")
    return failures


def select_context(corpus: Path = DEFAULT_CORPUS, *, district=None, source_file=None,
                   article_seq=None, article_title=None, source_unit_id=None,
                   contains=None, include_text=True, verify_markdown=False,
                   root: Path = ROOT) -> dict:
    root = Path(root)
    corpus = Path(corpus)
    selected = []
    scanned = corpus_chars = corpus_bytes = 0
    for row in read_jsonl_gz(corpus):
        scanned += 1
        text = row.get("text", "")
        if isinstance(text, str):
            corpus_chars += len(text)
            corpus_bytes += len(text.encode("utf-8"))
        if _matches(
            row,
            district=district,
            source_file=source_file,
            article_seq=article_seq,
            article_title=article_title,
            source_unit_id=source_unit_id,
            contains=contains,
        ):
            selected.append(_article_payload(row, include_text))

    selected_text_chars = sum(len(row.get("text", "")) for row in selected)
    selected_text_bytes = sum(len(row.get("text", "").encode("utf-8")) for row in selected)
    manifest_chars = len(json.dumps(
        {"articles": [_article_payload(row, False) for row in selected]},
        ensure_ascii=False,
        separators=(",", ":"),
    ))
    failures = verify_selected_articles(selected, root) if verify_markdown and include_text else []
    return {
        "meta": {
            "corpus": str(corpus),
            "corpus_units_scanned": scanned,
            "selected_units": len(selected),
            "include_text": include_text,
            "verification": "markdown_line_sha" if verify_markdown and include_text else "not_run",
            "selected_text_sha_mismatches": len(failures),
            "corpus_text_chars": corpus_chars,
            "corpus_text_bytes": corpus_bytes,
            "corpus_estimated_tokens": estimate_tokens(corpus_chars),
            "selected_text_chars": selected_text_chars,
            "selected_text_bytes": selected_text_bytes,
            "selected_estimated_tokens": estimate_tokens(selected_text_chars),
            "selected_manifest_chars": manifest_chars,
            "selected_manifest_estimated_tokens": estimate_tokens(manifest_chars),
            "token_estimator": "ceil(chars/3), tokenizer unavailable; compare ratios only",
        },
        "articles": selected,
        "verification_failures": failures,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="시행지침 조문 corpus 선택 로더")
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--district")
    ap.add_argument("--source-file")
    ap.add_argument("--article-seq", type=int)
    ap.add_argument("--article-title")
    ap.add_argument("--source-unit-id")
    ap.add_argument("--contains")
    ap.add_argument("--manifest", action="store_true", help="본문 없이 provenance manifest만 출력")
    ap.add_argument("--verify-markdown", action="store_true")
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("-o", "--output", type=Path)
    args = ap.parse_args()

    selectors = (
        args.district,
        args.source_file,
        args.article_seq,
        args.article_title,
        args.source_unit_id,
        args.contains,
    )
    if not any(value is not None for value in selectors):
        ap.error(
            "선택자를 최소 하나 지정해야 합니다: --district/--source-file/"
            "--article-seq/--article-title/--source-unit-id/--contains"
        )

    result = select_context(
        args.corpus,
        district=args.district,
        source_file=args.source_file,
        article_seq=args.article_seq,
        article_title=args.article_title,
        source_unit_id=args.source_unit_id,
        contains=args.contains,
        include_text=not args.manifest,
        verify_markdown=args.verify_markdown,
        root=args.root,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 1 if result["verification_failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
