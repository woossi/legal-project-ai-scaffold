#!/usr/bin/env python3
"""시행지침 조문 본문 무결성 전수 검증.

원본 PDF/HWP/ZIP에서 진단용 텍스트를 임시 추출한 뒤, 물리적 조문 표제와
본문이 병합 Markdown에 남아 있는지 대조한다. 표/표 안 값 손실은 별도
legal-table 산출물을 입력으로만 받으며 여기서 재측정하지 않는다.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import zipfile
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[5]
DEFAULT_MD_DIR = ROOT / "output/legal/markdown"
DEFAULT_MAIN_ROOT = Path(os.environ.get("LEGAL_PROJECT_ROOT", str(ROOT)))
DEFAULT_SOURCE_DIR = DEFAULT_MAIN_ROOT / "output/legal/시행지침"
DEFAULT_TABLE_LOSS = DEFAULT_MAIN_ROOT / ".claude/worktrees/legal-table/output/legal/table/_table_loss.json"
DEFAULT_JSON = ROOT / "output/legal/analysis/시행지침_조문_무결성_검증.json"
DEFAULT_MD = ROOT / "output/legal/analysis/시행지침_조문_무결성_검증.md"
DEFAULT_SOURCE_CORPUS = ROOT / "output/legal/statute/guideline_source_article_corpus.jsonl.gz"
DEFAULT_EXTERNAL_CORPUS = ROOT / "output/legal/statute/guideline_article_corpus.jsonl.gz"
DEFAULT_DEFINITIONS = ROOT / "output/legal/word/definiation.json"
DEFAULT_PROMOTION_REPORT = ROOT / "output/legal/analysis/조문표제_승격_리포트.json"
DEFAULT_REVALIDATION = ROOT / "output/legal/analysis/시행지침_재검증_리포트.json"
HWP5TXT_FALLBACK = DEFAULT_MAIN_ROOT / ".venv_hwp/bin/hwp5txt"

ARTICLE_LINE = re.compile(r"^(?P<hash>#{1,6}\s*)?제\s*(?P<num>\d+)\s*조(?P<branch>의\s*\d+)?\s*(?P<title>.*)$")
MARKDOWN_ARTICLE_HEADING = re.compile(r"^#{1,6}\s*제\s*\d+\s*조")
COUNT_FIELD = re.compile(r"(?m)^조문수:\s*(\d+)\s*$")
FRONTMATTER_END = re.compile(r"(?m)^---\s*$")
PARTICLE = re.compile(
    r"^제\s*\d+\s*조(?:의\s*\d+)?(?:제\s*\d+\s*[항호목])?"
    r"(에|의|를|은|는|이|가|와|과|로|으로|부터|까지|및|내지)(?![가-힣])"
)
ENDING = re.compile(r"(한다|된다|있다|없다|하여야|따른다|본다|말한다)\s*\.?\s*$")
PREDICATE = re.compile(r"(하여야|되어야|따라|의하여|의거|불구하고|경우|관련해서|명기|이하 동일)")
FRAGMENT = re.compile(r"^[)\]}~∙ㅣ]")
DOC_EXTS = (".pdf", ".hwp", ".hwpml")
ZIP_EXTS = (".zip",)
TABLE_CAPTION = re.compile(r"(\[?\s*(표|별표|서식)\s*\d|^>\s*\[(표|그림)\]|<\s*(표|별표|서식))")
FOOTER = re.compile(r"^\s*-\s*\d{1,4}\s*-\s*$")
REGEN_COST = {
    "guideline_source_article_corpus.jsonl.gz": "19,357개 원 시행지침 단위의 줄범위·SHA 전면 재생성",
    "gate12": "시행지침-조문-인용범위 게이트 12 재검증",
    "legal-xref": "줄번호 기반 xref 산출물 재구축·검증",
    "legal-contrast": "줄번호 기반 contrast 산출물 재구축·검증",
}
TABLE_LOSS_CONTRACT = {
    "required_record_fields": ["dstrcAppnNo", "lc5", "지역", "district", "loss_class", "표참조"],
    "required_table_ref_fields": ["surface", "section", "본문실재"],
    "required_norm_value_fields_when_present": [
        "surface", "metric", "value", "source_file", "line", "section",
        "table_caption", "subject", "subject_type",
    ],
    "semantic_integrity_definition": (
        "표 본문 + 값 + 표 캡션/표참조 surface + 소속 section(편·장·절 표목) + "
        "값→용지 주어 귀속 가능성"
    ),
    "semantic_loss_class": "값 존재·의미 결손",
    "semantic_loss_rule": "값이 있어도 caption/section이 없거나 subject가 null이면 실패 또는 별도분류",
}
TABLE_ORCHESTRATOR_OBSERVATION = {
    "source": "legal-table 오케스트레이터 실측, _table_loss 입력 대기",
    "interpretation": (
        "건폐율·용적률 값의 주어는 용도지역보다 용지가 지배적이다. "
        "표 손실 재변환 후보의 의미와 복원 스키마는 단독주택용지·공동주택용지·"
        "상업시설용지 등 용지 중심으로 설명하고, 용도지역 중심으로 축소 해석하지 않는다."
    ),
    "subject_distribution": {
        "same_line": {"용지": 52, "용도지역": 10},
        "table_caption": {"용지": 180, "용도지역": 2},
        "section_heading": {"용지": 373, "용도지역": 15},
    },
}


@dataclass
class Article:
    seq: int
    title: str
    line_start: int
    line_end: int
    body: str
    is_h4: bool = False
    source: str = ""

    @property
    def key(self) -> str:
        return article_key(self.title)

    @property
    def normalized_body(self) -> str:
        return normalize_text(self.body, drop_table_lines=True)


@dataclass
class ArticleFinding:
    source_seq: int
    source_title: str
    source_line_start: int
    source_line_end: int
    markdown_seq: int | None
    markdown_line_start: int | None
    title_present: bool
    h4_present: bool
    body_present: bool
    order_ok: bool
    coverage: float
    classification: str
    alternate_canonical_exists: bool
    text_sha256: str
    prose_witness_count: int
    missing_prose_witnesses: list[str]


@dataclass
class DocumentResult:
    lc5: str
    district: str
    region: str
    source_paths: list[str]
    markdown_path: str
    source_mode: str
    source_extract_status: str
    source_extract_notes: list[str]
    source_article_count: int
    markdown_physical_article_count: int
    markdown_h4_article_count: int
    verification_status: str
    unverified_reason: str
    complete_count: int
    body_missing_count: int
    table_pending_unclassified_count: int
    structure_missing_count: int
    order_damage_count: int
    alternate_canonical_count: int
    retransform_candidate: bool
    findings: list[ArticleFinding] = field(default_factory=list)


def load_converter():
    path = SCRIPT.with_name("convert.py")
    spec = importlib.util.spec_from_file_location("legal_textreform_convert", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"convert.py 로드 실패: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    m = list(FRONTMATTER_END.finditer(text))
    if len(m) < 2:
        return {}, text
    end = m[1]
    fm_text = text[m[0].end():end.start()]
    body = text[end.end():].lstrip("\n")
    fm: dict[str, object] = {}
    current_list = None
    current_item = None
    for raw in fm_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("  - "):
            key, value = parse_kv(line[4:])
            current_item = {key: value}
            if current_list is None:
                current_list = []
            current_list.append(current_item)
            continue
        if line.startswith("    ") and current_item is not None:
            key, value = parse_kv(line.strip())
            current_item[key] = value
            continue
        key, value = parse_kv(line)
        if value == "":
            fm[key] = []
            current_list = fm[key]
            current_item = None
        else:
            fm[key] = value
            current_list = None
            current_item = None
    return fm, body


def parse_kv(line: str) -> tuple[str, object]:
    if ":" not in line:
        return line.strip(), ""
    key, value = line.split(":", 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    elif re.fullmatch(r"\d+", value):
        return key.strip(), int(value)
    return key.strip(), value


def normalize_line_for_title(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"\s+", " ", line)
    return line


def reject_article_line(line: str, title: str) -> str | None:
    raw = re.sub(r"^#{1,6}\s*", "", line.strip())
    if PARTICLE.match(raw):
        return "조사부착"
    if ENDING.search(raw):
        return "종결어미"
    if len(raw) > 90 and not re.search(r"(목적|정의|적용|구성|용도|건축|배치|교통|경관|공원|주차)", title):
        return "장문"
    if PREDICATE.search(raw):
        return "서술어절"
    if not title.strip():
        return "표제없음"
    if FRAGMENT.match(title.strip()):
        return "조판파편"
    if len(re.findall(r"제\s*\d+\s*조", raw)) > 1:
        return "제N조중복"
    return None


def extract_articles(text: str, source: str, include_plain: bool = True) -> list[Article]:
    lines = text.splitlines()
    starts: list[tuple[int, str, bool]] = []
    in_fence = False
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = ARTICLE_LINE.match(line.strip())
        if not m:
            continue
        is_heading = bool(m.group("hash"))
        if is_heading and not MARKDOWN_ARTICLE_HEADING.match(line.strip()):
            continue
        if not is_heading and not include_plain:
            continue
        if line != line.lstrip() and not is_heading:
            continue
        title = normalize_line_for_title(line)
        if reject_article_line(line, m.group("title") or ""):
            continue
        starts.append((idx, title, is_heading))
    articles: list[Article] = []
    for seq, (start, title, is_heading) in enumerate(starts, start=1):
        end = starts[seq][0] if seq < len(starts) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        articles.append(Article(seq, title, start + 1, end, body, is_heading, source))
    return articles


def is_table_like_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if TABLE_CAPTION.search(s) or FOOTER.match(s):
        return True
    if re.search(r"\S\s{2,}\S", s):
        hangul = len(re.findall(r"[가-힣]", s))
        digits = len(re.findall(r"\d", s))
        return digits >= 2 and hangul <= 8
    if "|" in s and s.count("|") >= 2:
        return True
    return False


def normalize_text(text: str, drop_table_lines: bool = False) -> str:
    lines = []
    for line in text.splitlines():
        if drop_table_lines and is_table_like_line(line):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line.strip())
        line = re.sub(r"^>\s*\[(표|그림)\]\s*$", "", line)
        line = re.sub(r"^[\-*]\s+", "", line)
        line = re.sub(r"^\d+\.\s+", "", line)
        line = re.sub(r"^[①-⑳]\s*", "", line)
        line = line.replace("**", "")
        lines.append(line)
    s = unicodedata.normalize("NFKC", "\n".join(lines))
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[`*_>#|:;,.·ㆍ\-\(\)\[\]{}<>「」『』“”\"'‘’]", "", s)
    return s


def article_key(title: str) -> str:
    title = normalize_line_for_title(title)
    title = unicodedata.normalize("NFKC", title)
    title = re.sub(r"\s+", "", title)
    title = re.sub(r"[()（）\[\]【】<>〈〉「」『』:：·ㆍ.\-]", "", title)
    return title


def body_coverage(source_body: str, markdown_doc_norm: str) -> float:
    source_norm = normalize_text(source_body, drop_table_lines=True)
    if not source_norm:
        return 1.0
    if source_norm in markdown_doc_norm:
        return 1.0
    if len(source_norm) <= 80:
        return 1.0 if source_norm in markdown_doc_norm else 0.0
    chunks = []
    size = 80
    step = max(40, len(source_norm) // 12)
    for i in range(0, max(1, len(source_norm) - size + 1), step):
        chunks.append(source_norm[i:i + size])
    tail = source_norm[-size:]
    if tail not in chunks:
        chunks.append(tail)
    chunks = [c for c in chunks if len(c) >= 30]
    if not chunks:
        return 0.0
    hits = sum(1 for c in chunks if c in markdown_doc_norm)
    return hits / len(chunks)


def is_noise_witness_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if ARTICLE_LINE.match(s):
        return True
    if re.match(r"^#{1,6}\s*", s):
        return True
    if re.match(r"^제\s*\d+\s*(편|장|절)\b", s):
        return True
    if re.match(r"^제\s*[IVXLCDMⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*(편|장|절)\b", s):
        return True
    if re.search(r"(목\s*차|차\s*례|\.{3,}|…{2,})", s):
        return True
    if TABLE_CAPTION.search(s) or re.search(r"(<\s*그림|\[\s*그림|^>\s*\[(표|그림)\])", s):
        return True
    if is_table_like_line(s):
        return True
    if FOOTER.match(s):
        return True
    compact = normalize_text(s)
    if "지구단위계획시행지침" in compact and len(compact) <= 45:
        return True
    return False


def prose_witnesses(source_body: str) -> list[str]:
    witnesses = []
    for line in source_body.splitlines():
        if is_noise_witness_line(line):
            continue
        s = re.sub(r"^[\-*]\s+", "", line.strip())
        s = re.sub(r"^\d+\.\s+", "", s)
        s = re.sub(r"^[①-⑳]\s*", "", s)
        norm = normalize_text(s)
        if len(norm) < 8 or not re.search(r"[가-힣]", norm):
            continue
        witnesses.append(s)
    return witnesses


def witness_coverage(source_body: str, markdown_doc_norm: str) -> tuple[float, list[str], int]:
    witnesses = prose_witnesses(source_body)
    if not witnesses:
        return 1.0, [], 0
    missing = []
    for witness in witnesses:
        if normalize_text(witness) not in markdown_doc_norm:
            missing.append(witness)
    return (len(witnesses) - len(missing)) / len(witnesses), missing, len(witnesses)


def is_definition_title(title: str, definition_keys: set[str]) -> bool:
    k = article_key(title)
    if k in definition_keys:
        return True
    compact = normalize_text(title)
    return any(term in compact for term in ("용어의정의", "용어정의", "용어정리")) or compact.endswith("정의")


def compare_articles(
    source_articles: list[Article],
    md_articles: list[Article],
    md_text: str,
    definition_keys: set[str],
    table_loss_pending: bool = False,
) -> tuple[list[ArticleFinding], Counter]:
    md_by_key: dict[str, deque[Article]] = defaultdict(deque)
    for article in md_articles:
        md_by_key[article.key].append(article)
    md_doc_norm = normalize_text(md_text, drop_table_lines=True)
    findings: list[ArticleFinding] = []
    stats: Counter = Counter()
    last_md_seq = 0
    for src in source_articles:
        md_match = None
        if md_by_key.get(src.key):
            md_match = md_by_key[src.key].popleft()
        title_present = md_match is not None
        h4_present = bool(md_match and md_match.is_h4)
        coverage, missing_witnesses, witness_count = witness_coverage(src.body, md_doc_norm)
        body_present = not missing_witnesses
        order_ok = True
        if md_match:
            order_ok = md_match.seq >= last_md_seq
            last_md_seq = max(last_md_seq, md_match.seq)
        alternate = (not body_present) and is_definition_title(src.title, definition_keys)
        if not title_present:
            classification = "본문누락" if not body_present else "구조누락"
        elif not h4_present:
            classification = "구조누락"
        elif not order_ok:
            classification = "순서훼손"
        elif not body_present:
            classification = "본문누락"
        else:
            classification = "완전"
        if classification == "본문누락" and table_loss_pending and article_has_table_context(src):
            classification = "미분류_표입력대기"
        if alternate and classification == "본문누락":
            stats["대체정본"] += 1
        stats[classification] += 1
        findings.append(ArticleFinding(
            source_seq=src.seq,
            source_title=src.title,
            source_line_start=src.line_start,
            source_line_end=src.line_end,
            markdown_seq=md_match.seq if md_match else None,
            markdown_line_start=md_match.line_start if md_match else None,
            title_present=title_present,
            h4_present=h4_present,
            body_present=body_present,
            order_ok=order_ok,
            coverage=round(coverage, 4),
            classification=classification,
            alternate_canonical_exists=alternate,
            text_sha256=hashlib.sha256(src.body.encode("utf-8")).hexdigest(),
            prose_witness_count=witness_count,
            missing_prose_witnesses=missing_witnesses[:5],
        ))
    return findings, stats


def article_has_table_context(article: Article) -> bool:
    text = article.title + "\n" + article.body
    return bool(TABLE_CAPTION.search(text) or re.search(r"(건폐율|용적률|용적율|최고층수|세대수).{0,40}<\s*표", text))


def load_definition_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    titles = data.get("definition_articles", {}).get("titles", [])
    return {article_key(item.get("title", "")) for item in titles if item.get("title")}


def decode_zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    for enc in ("cp437", "latin-1"):
        try:
            raw = info.filename.encode(enc)
        except Exception:
            continue
        for dec in ("cp949", "euc-kr", "utf-8"):
            try:
                value = raw.decode(dec)
            except Exception:
                continue
            if value and "\ufffd" not in value:
                return value
    return info.filename


def strip_ext(name: str) -> str:
    return re.sub(r"\.(pdf|hwp|hwpml|zip)$", "", name, flags=re.I)


def normalize_fragment(name: str) -> str:
    name = unicodedata.normalize("NFKC", name)
    name = strip_ext(name)
    name = re.sub(r"[\\/]+", "__", name)
    name = re.sub(r"\s+", "", name)
    return name


def origin_matches_candidate(origin_file: str, candidate_label: str) -> bool:
    origin = normalize_fragment(origin_file)
    cand = normalize_fragment(candidate_label)
    if origin == cand:
        return True
    if "__" in origin:
        tail = origin.split("__", 1)[1]
        return cand.endswith(tail) or tail in cand
    return cand.endswith(origin) or origin in cand


def route_matches(route_name: str, wanted: str) -> bool:
    wanted = wanted.strip()
    if wanted == "PDF":
        return route_name == "pdftotext -layout"
    return route_name == wanted


def select_route(routes: list[tuple[str, object]], wanted: str) -> tuple[str, object] | None:
    for route in routes:
        if route_matches(route[0], wanted):
            return route
    return None


def iter_zip_documents(zip_path: Path, origin_file: str, notes: list[str]) -> Iterable[tuple[str, Path]]:
    try:
        zf = zipfile.ZipFile(zip_path)
    except Exception as exc:
        notes.append(f"{zip_path}: zip 열기 실패: {exc}")
        return
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = decode_zip_name(info)
        if not name.lower().endswith(DOC_EXTS + ZIP_EXTS):
            continue
        label = f"{zip_path.name}__{name}"
        if not origin_matches_candidate(origin_file, label) and not origin_matches_candidate(origin_file, name):
            continue
        suffix = Path(name).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            with zf.open(info) as src:
                shutil.copyfileobj(src, tmp)
            yield label, Path(tmp.name)
    zf.close()


def source_files_for(md_path: Path, fm: dict, source_dir: Path) -> list[Path]:
    region = str(fm.get("지역", md_path.parent.name))
    district = str(fm.get("지구명", md_path.stem.rsplit("_", 1)[0]))
    district_dir = source_dir / region / district
    if not district_dir.exists():
        return []
    return sorted(
        p for p in district_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in DOC_EXTS + ZIP_EXTS
    )


def extract_origin_item_text(
    origin: dict,
    district_files: list[Path],
    converter,
    notes: list[str],
) -> tuple[str | None, str | None, str | None]:
    origin_file = str(origin.get("파일", ""))
    wanted_route = str(origin.get("추출", ""))
    candidates: list[tuple[str, Path]] = []
    for path in district_files:
        if path.suffix.lower() == ".zip":
            candidates.extend(iter_zip_documents(path, origin_file, notes))
        elif origin_matches_candidate(origin_file, path.name) or (len(district_files) == 1 and "__" not in origin_file):
            candidates.append((path.name, path))
    if not candidates:
        notes.append(f"{origin_file}: 원본구성 fragment 매핑 실패")
        return None, None, None
    if len(candidates) > 1:
        notes.append(f"{origin_file}: 후보 {len(candidates)}개라 모호함")
        return None, None, None
    label, path = candidates[0]
    try:
        routes = converter.routes(str(path))
    except Exception as exc:
        notes.append(f"{origin_file}: 추출 경로 확인 실패: {exc}")
        return None, str(path), label
    selected = select_route(routes, wanted_route)
    if not selected:
        if str(path).startswith(tempfile.gettempdir()):
            try:
                path.unlink()
            except OSError:
                pass
        notes.append(f"{origin_file}: 요청 route {wanted_route!r} 재현 불가")
        return None, str(path), label
    route_name, fn = selected
    try:
        text = fn(str(path))
    except Exception as exc:
        if str(path).startswith(tempfile.gettempdir()):
            try:
                path.unlink()
            except OSError:
                pass
        notes.append(f"{origin_file}: {route_name} 예외: {exc}")
        return None, str(path), label
    if not text or not text.strip():
        if str(path).startswith(tempfile.gettempdir()):
            try:
                path.unlink()
            except OSError:
                pass
        notes.append(f"{origin_file}: {route_name} 빈 결과")
        return None, str(path), label
    expected_chars = origin.get("글자")
    if isinstance(expected_chars, int) and expected_chars > 0:
        ratio = abs(len(text) - expected_chars) / expected_chars
        if ratio > 0.35:
            notes.append(f"{origin_file}: 글자수 차이 {len(text)} vs {expected_chars} ({ratio:.1%})")
    if str(path).startswith(tempfile.gettempdir()):
        try:
            path.unlink()
        except OSError:
            pass
    return text, str(path), label


def validate_markdown_origin_boundaries(body: str, origins: list[dict], notes: list[str]) -> None:
    if len(origins) <= 1:
        return
    for origin in origins:
        origin_file = str(origin.get("파일", ""))
        if f"<!-- 원본: {origin_file} -->" not in body:
            notes.append(f"{origin_file}: Markdown 원본 경계 주석 미검출")


def extract_text_from_file(path: Path, converter, notes: list[str]) -> list[tuple[str, str]]:
    suffix = path.suffix.lower()
    if suffix == ".zip":
        out: list[tuple[str, str]] = []
        try:
            zf = zipfile.ZipFile(path)
        except Exception as exc:
            notes.append(f"{path}: zip 열기 실패: {exc}")
            return out
        with tempfile.TemporaryDirectory(prefix="article-integrity-zip-") as td:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = decode_zip_name(info)
                if not name.lower().endswith(DOC_EXTS + ZIP_EXTS):
                    continue
                flat = re.sub(r"[\x00-\x1f/\\]", "_", name)
                tmp = Path(td) / flat
                with zf.open(info) as src, tmp.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                nested = extract_text_from_file(tmp, converter, notes)
                for nested_name, text in nested:
                    out.append((f"{path.name}__{name}__{nested_name}", text))
        zf.close()
        if not out:
            notes.append(f"{path}: zip 내부 문서 없음 또는 추출 실패")
        return out
    try:
        routes = converter.routes(str(path))
    except Exception as exc:
        notes.append(f"{path}: 추출 경로 확인 실패: {exc}")
        return []
    for route_name, fn in routes:
        try:
            text = fn(str(path))
        except Exception as exc:
            notes.append(f"{path}: {route_name} 예외: {exc}")
            continue
        if text and text.strip():
            return [(route_name, text)]
        notes.append(f"{path}: {route_name} 빈 결과")
    notes.append(f"{path}: 지원 추출 경로 없음")
    return []


def source_text_for_document(
    md_path: Path,
    fm: dict,
    body: str,
    source_dir: Path,
    source_mode: str,
    converter,
) -> tuple[str, str, list[str], list[str]]:
    district_files = source_files_for(md_path, fm, source_dir)
    source_paths = [str(p) for p in district_files]
    notes: list[str] = []
    if source_mode == "markdown":
        return body, "markdown_self_check", source_paths, ["source-mode=markdown: 자기대조이며 원본 본문 완전성 증거가 아님"]
    origins = fm.get("원본구성", [])
    chunks: list[str] = []
    mapped = 0
    if isinstance(origins, list) and origins:
        validate_markdown_origin_boundaries(body, [x for x in origins if isinstance(x, dict)], notes)
        for origin in origins:
            if not isinstance(origin, dict):
                continue
            text, path, label = extract_origin_item_text(origin, district_files, converter, notes)
            if text is None:
                continue
            mapped += 1
            chunks.append(f"\n\n<!-- source: {origin.get('파일')} [{origin.get('추출')}] mapped={label} -->\n\n{text}")
        if mapped == len([x for x in origins if isinstance(x, dict)]) and chunks:
            return "\n".join(chunks), "source_extract", source_paths, notes
        if chunks:
            notes.append(f"원본구성 {mapped}/{len(origins)} 조각만 매핑되어 부분 검증으로 격리")
            return "\n".join(chunks), "source_extract_partial", source_paths, notes
    else:
        notes.append("frontmatter 원본구성 없음")
    if source_mode == "source":
        return "", "source_extract_failed", source_paths, notes
    return "", "source_extract_failed", source_paths, notes + ["원본 진단 텍스트 추출 실패. Markdown fallback 자기대조는 완전성 증거에서 제외"]


def count_gzip_jsonl(path: Path) -> int | None:
    if not path.exists():
        return None
    count = 0
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for _ in fh:
            count += 1
    return count


def load_corpus_gate12(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    count = 0
    bad_sha = 0
    bad_lines = 0
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            count += 1
            rec = json.loads(line)
            text = rec.get("text", "")
            if rec.get("text_sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest():
                bad_sha += 1
            if not rec.get("line_start") or not rec.get("line_end"):
                bad_lines += 1
    external_count = count_gzip_jsonl(DEFAULT_EXTERNAL_CORPUS)
    return {
        "exists": True,
        "file": str(path),
        "unit_count": count,
        "bad_sha_count": bad_sha,
        "bad_line_range_count": bad_lines,
        "filename_note": (
            "19,357행 원 시행지침 corpus는 guideline_source_article_corpus.jsonl.gz이다. "
            "guideline_article_corpus.jsonl.gz는 외부 법령 정본 corpus이며 현재 "
            f"{external_count}행이다."
        ),
        "external_guideline_article_corpus_count": external_count,
    }


def load_table_loss(path: Path) -> dict:
    norm_values_path = path.with_name("norm_values.json")
    if not path.exists():
        return {
            "status": "pending_input",
            "path": str(path),
            "accepted": False,
            "norm_values_status": "pending_input",
            "norm_values_path": str(norm_values_path),
            "required_contract": TABLE_LOSS_CONTRACT,
            "orchestrator_observation": TABLE_ORCHESTRATOR_OBSERVATION,
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    records = table_loss_records(data)
    norm_values = load_norm_values(norm_values_path)
    violations = validate_table_loss_records(records) + validate_norm_values(norm_values.get("records", []))
    return {
        "status": "accepted" if not violations else "accepted_with_contract_violations",
        "path": str(path),
        "accepted": True,
        "norm_values_status": norm_values["status"],
        "norm_values_path": str(norm_values_path),
        "required_contract": TABLE_LOSS_CONTRACT,
        "orchestrator_observation": TABLE_ORCHESTRATOR_OBSERVATION,
        "contract_violations": violations,
        "value_semantic_loss_count": count_value_semantic_loss(norm_values.get("records", [])) if norm_values["status"] == "accepted" else None,
        "payload": data,
    }


def load_norm_values(path: Path) -> dict:
    if not path.exists():
        return {"status": "pending_input", "records": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        records = [x for x in data if isinstance(x, dict)]
    elif isinstance(data, dict):
        value = data.get("norm_values") or data.get("records") or data.get("items") or []
        records = [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []
    else:
        records = []
    return {"status": "accepted", "records": records}


def table_loss_records(data: object) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("documents", "table_loss", "records", "items", "재변환후보"):
        value = data.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    if any(k in data for k in TABLE_LOSS_CONTRACT["required_record_fields"]):
        return [data]
    return []


def validate_table_loss_records(records: list[dict]) -> list[dict]:
    violations = []
    for idx, rec in enumerate(records):
        missing = [k for k in TABLE_LOSS_CONTRACT["required_record_fields"] if k not in rec]
        if missing:
            violations.append({"record_index": idx, "missing_fields": missing})
        refs = rec.get("표참조", [])
        if isinstance(refs, list):
            for ref_idx, ref in enumerate(refs):
                if not isinstance(ref, dict):
                    violations.append({"record_index": idx, "table_ref_index": ref_idx, "error": "표참조 항목이 객체가 아님"})
                    continue
                miss_ref = [k for k in TABLE_LOSS_CONTRACT["required_table_ref_fields"] if k not in ref]
                if miss_ref:
                    violations.append({"record_index": idx, "table_ref_index": ref_idx, "missing_fields": miss_ref})
                if ref.get("article") is None and "article_reason" not in ref:
                    violations.append({"record_index": idx, "table_ref_index": ref_idx, "missing_fields": ["article_reason"]})
    return violations


def validate_norm_values(records: list[dict]) -> list[dict]:
    violations = []
    for idx, val in enumerate(records):
        missing_val = [
            k for k in TABLE_LOSS_CONTRACT["required_norm_value_fields_when_present"]
            if k not in val
        ]
        if missing_val:
            violations.append({"norm_value_index": idx, "missing_fields": missing_val})
    return violations


def count_value_semantic_loss(records: list[dict]) -> int:
    count = 0
    for val in records:
        if not isinstance(val, dict):
            continue
        caption = val.get("table_caption")
        section = val.get("section")
        subject = val.get("subject")
        if not caption or not section or subject is None:
            count += 1
    return count


def known_issue_from_revalidation(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    issue = data.get("C_내용완결성", {}).get("조문헤딩승격누락_전수조사", {})
    return {
        "known_documents": issue.get("건수"),
        "known_unpromoted_articles": issue.get("미승격조문총계"),
        "known_list": issue.get("목록", []),
        "prior_classification": "구조 인덱싱 한계, 내용 유실 아님",
    }


def known_issue_current_breakdown(known: dict, results: list[DocumentResult]) -> list[dict]:
    prior = {item.get("지구번호"): item for item in known.get("known_list", [])}
    by_lc5 = {r.lc5: r for r in results}
    out = []
    for lc5, item in prior.items():
        r = by_lc5.get(lc5)
        if not r:
            out.append({"lc5": lc5, "district": item.get("지구"), "current_status": "missing_in_current_results"})
            continue
        classes = Counter(f.classification for f in r.findings)
        out.append({
            "lc5": lc5,
            "district": r.district,
            "prior_unpromoted_articles": item.get("평문미승격"),
            "verification_status": r.verification_status,
            "source_article_count": r.source_article_count,
            "markdown_physical_article_count": r.markdown_physical_article_count,
            "markdown_h4_article_count": r.markdown_h4_article_count,
            "classification_counts": dict(classes),
            "current_interpretation": (
                "미검증" if r.verification_status != "verified"
                else "본문누락/표입력대기/순서훼손 포함" if (r.body_missing_count or r.table_pending_unclassified_count or r.order_damage_count)
                else "현 Markdown에서 구조누락 중심"
            ),
        })
    return out


def build_document_result(
    md_path: Path,
    source_dir: Path,
    source_mode: str,
    definition_keys: set[str],
    converter,
    table_loss_pending: bool = False,
) -> DocumentResult:
    text = md_path.read_text(encoding="utf-8")
    fm, body = split_frontmatter(text)
    source_text, status, source_paths, notes = source_text_for_document(
        md_path, fm, body, source_dir, source_mode, converter
    )
    source_articles = extract_articles(source_text, status, include_plain=True) if source_text else []
    md_physical = extract_articles(body, str(md_path), include_plain=True)
    md_h4 = [a for a in md_physical if a.is_h4]
    verification_status = "verified" if status == "source_extract" else "unverified"
    if status == "markdown_self_check":
        verification_status = "self_check_only"
    if status == "source_extract" and not source_articles and md_physical:
        verification_status = "unverified"
        notes.append("원본 텍스트 추출은 성공했으나 물리 조문 파서가 0개를 검출해 Markdown 조문 완전성 증거에서 제외")
    findings, stats = (
        compare_articles(source_articles, md_physical, body, definition_keys, table_loss_pending)
        if verification_status != "unverified" else ([], Counter())
    )
    return DocumentResult(
        lc5=str(fm.get("지구번호", "")),
        district=str(fm.get("지구명", md_path.stem.rsplit("_", 1)[0])),
        region=str(fm.get("지역", md_path.parent.name)),
        source_paths=source_paths,
        markdown_path=str(md_path.relative_to(ROOT)),
        source_mode=status,
        source_extract_status=status,
        source_extract_notes=notes,
        source_article_count=len(source_articles),
        markdown_physical_article_count=len(md_physical),
        markdown_h4_article_count=len(md_h4),
        verification_status=verification_status,
        unverified_reason="" if verification_status == "verified" else "; ".join(notes[-3:]),
        complete_count=stats["완전"],
        body_missing_count=stats["본문누락"],
        table_pending_unclassified_count=stats["미분류_표입력대기"],
        structure_missing_count=stats["구조누락"],
        order_damage_count=stats["순서훼손"],
        alternate_canonical_count=stats["대체정본"],
        retransform_candidate=bool(stats["본문누락"] or stats["순서훼손"]),
        findings=findings,
    )


def summarize(results: list[DocumentResult], table_loss: dict) -> dict:
    totals = Counter()
    for r in results:
        totals["documents"] += 1
        totals["source_articles"] += r.source_article_count
        totals["markdown_physical_articles"] += r.markdown_physical_article_count
        totals["markdown_h4_articles"] += r.markdown_h4_article_count
        totals["complete"] += r.complete_count
        totals["body_missing"] += r.body_missing_count
        totals["table_pending_unclassified"] += r.table_pending_unclassified_count
        totals["structure_missing"] += r.structure_missing_count
        totals["order_damage"] += r.order_damage_count
        totals["alternate_canonical"] += r.alternate_canonical_count
        if r.source_mode == "source_extract":
            totals["source_extract_documents"] += 1
        if r.source_mode == "markdown_self_check":
            totals["markdown_self_check_documents"] += 1
        if r.source_mode == "source_extract_failed":
            totals["source_extract_failed_documents"] += 1
        if r.source_mode == "source_extract_partial":
            totals["source_extract_partial_documents"] += 1
        if r.verification_status == "verified":
            totals["verified_documents"] += 1
        if r.verification_status == "unverified":
            totals["unverified_documents"] += 1
            if r.source_mode == "source_extract":
                totals["source_extract_no_article_documents"] += 1
        if r.verification_status == "self_check_only":
            totals["self_check_only_documents"] += 1
        if r.retransform_candidate:
            totals["article_retransform_candidate_documents"] += 1
    return dict(totals) | {
        "table_loss_status": table_loss.get("status"),
        "table_loss_accepted": table_loss.get("accepted", False),
        "norm_values_status": table_loss.get("norm_values_status"),
        "table_loss_contract_violation_count": len(table_loss.get("contract_violations", [])),
        "table_value_semantic_loss_count": table_loss.get("value_semantic_loss_count"),
    }


def retransform_candidates(results: list[DocumentResult], table_loss: dict) -> list[dict]:
    candidates = []
    for r in results:
        reasons = []
        if r.body_missing_count:
            reasons.append("조문 본문 누락")
        if r.order_damage_count:
            reasons.append("조문 순서 훼손")
        if reasons:
            candidates.append({
                "lc5": r.lc5,
                "district": r.district,
                "region": r.region,
                "markdown_path": r.markdown_path,
                "reasons": reasons,
                "body_missing_count": r.body_missing_count,
                "table_pending_unclassified_count": r.table_pending_unclassified_count,
                "order_damage_count": r.order_damage_count,
                "alternate_canonical_count": r.alternate_canonical_count,
                "chain_cost": REGEN_COST,
            })
    if table_loss.get("accepted"):
        payload = table_loss.get("payload")
        table_candidates = []
        if isinstance(payload, dict):
            for key in ("retransform_candidates", "재변환후보", "documents"):
                if isinstance(payload.get(key), list):
                    table_candidates = payload[key]
                    break
        if table_candidates:
            candidates.append({
                "lc5": None,
                "district": "legal-table 입력 후보",
                "region": None,
                "markdown_path": None,
                "reasons": ["표 손실 입력 결합"],
                "table_loss_candidates": table_candidates,
                "chain_cost": REGEN_COST,
            })
    return candidates


def write_markdown_report(path: Path, report: dict) -> None:
    summary = report["summary"]
    candidates = report["retransform_candidates"]
    known = report["known_issue_6docs_232"]
    lines = [
        "# 시행지침 조문 무결성 검증",
        "",
        f"- 생성일시: {report['generated_at']}",
        f"- 대상 Markdown: {summary.get('documents', 0)}건",
        f"- 원본 추출 성공 문서: {summary.get('source_extract_documents', 0)}건",
        f"- 검증 판정 문서: {summary.get('verified_documents', 0)}건",
        f"- 미검증 문서: {summary.get('unverified_documents', 0)}건 "
        f"(원본 추출 실패 {summary.get('source_extract_failed_documents', 0)}건, "
        f"추출 후 조문 파서 0건 {summary.get('source_extract_no_article_documents', 0)}건)",
        f"- 원천 조문 모수: {summary.get('source_articles', 0)}개",
        f"- Markdown 물리 조문 후보: {summary.get('markdown_physical_articles', 0)}개",
        f"- Markdown h4 조문: {summary.get('markdown_h4_articles', 0)}개",
        f"- 완전: {summary.get('complete', 0)}개",
        f"- 본문누락: {summary.get('body_missing', 0)}개",
        f"- 구조누락: {summary.get('structure_missing', 0)}개",
        f"- 순서훼손: {summary.get('order_damage', 0)}개",
        f"- 대체정본 존재: {summary.get('alternate_canonical', 0)}개",
        f"- `_table_loss.json`: {summary.get('table_loss_status')}",
        "- 판정 상태: PARTIAL — 조문 자동 대조는 완료했으나 표 손실 입력과 30개 미검증 문서가 남아 있음",
        f"- Gate12 원 시행지침 corpus: {report['gate12_corpus_check'].get('file')} "
        f"({report['gate12_corpus_check'].get('unit_count')}행)",
        f"- 파일명 주의: {report['gate12_corpus_check'].get('filename_note')}",
        "",
        "## 표 손실 입력 계약",
        "",
        f"- 입력 경로: {report['table_loss_input'].get('path')}",
        f"- norm_values 경로: {report['table_loss_input'].get('norm_values_path')}",
        f"- norm_values 상태: {report['table_loss_input'].get('norm_values_status')}",
        f"- 통합 무결성 정의: {report['table_loss_input']['required_contract']['semantic_integrity_definition']}",
        f"- 의미 결손 분류: {report['table_loss_input']['required_contract']['semantic_loss_class']} — "
        f"{report['table_loss_input']['required_contract']['semantic_loss_rule']}",
        "- 필수 문서 필드: " + ", ".join(report["table_loss_input"]["required_contract"]["required_record_fields"]),
        "- 필수 표참조 필드: " + ", ".join(report["table_loss_input"]["required_contract"]["required_table_ref_fields"]),
        "- norm_values 관련 필드(있을 때): " + ", ".join(report["table_loss_input"]["required_contract"]["required_norm_value_fields_when_present"]),
        f"- 계약 위반 수: {summary.get('table_loss_contract_violation_count')}",
        f"- 값 존재·의미 결손 수: {summary.get('table_value_semantic_loss_count')}",
        "",
        "표 주어 해석 입력:",
        f"- 출처: {report['table_loss_input']['orchestrator_observation']['source']}",
        f"- 해석: {report['table_loss_input']['orchestrator_observation']['interpretation']}",
        "- 동일행 용지:용도지역 = "
        f"{report['table_loss_input']['orchestrator_observation']['subject_distribution']['same_line']['용지']}:"
        f"{report['table_loss_input']['orchestrator_observation']['subject_distribution']['same_line']['용도지역']}",
        "- 표 캡션 용지:용도지역 = "
        f"{report['table_loss_input']['orchestrator_observation']['subject_distribution']['table_caption']['용지']}:"
        f"{report['table_loss_input']['orchestrator_observation']['subject_distribution']['table_caption']['용도지역']}",
        "- 편·장·절 표목 용지:용도지역 = "
        f"{report['table_loss_input']['orchestrator_observation']['subject_distribution']['section_heading']['용지']}:"
        f"{report['table_loss_input']['orchestrator_observation']['subject_distribution']['section_heading']['용도지역']}",
        "",
        "## 기존 known issue 6문서/232개",
        "",
        f"- 기존 리포트 모수: {known.get('known_documents')}문서 / {known.get('known_unpromoted_articles')}개",
        f"- 기존 판정: {known.get('prior_classification')}",
        "- 이번 판정은 문서별 `findings[].classification`에서 본문누락과 구조누락을 분리했다.",
        "- 현재 breakdown:",
        "",
        "## 재변환 후보와 연쇄비용",
        "",
    ]
    insert_at = lines.index("## 재변환 후보와 연쇄비용") - 1
    for item in known.get("current_breakdown", []):
        lines.insert(
            insert_at,
            f"  - {item.get('lc5')} {item.get('district')}: {item.get('verification_status')}, "
            f"prior 평문 {item.get('prior_unpromoted_articles')}, "
            f"current {item.get('classification_counts')}",
        )
        insert_at += 1
    if not candidates:
        lines.append("- 조문 본문/순서 기준 재변환 후보 없음.")
    else:
        for item in candidates:
            lines.append(
                f"- {item.get('lc5') or '-'} {item.get('district')}: "
                f"{', '.join(item.get('reasons', []))} "
                f"(본문누락 {item.get('body_missing_count', 0)}, 순서훼손 {item.get('order_damage_count', 0)})"
            )
    lines.extend([
        "",
        "연쇄비용:",
        "- guideline_source_article_corpus.jsonl.gz 19,357개 단위 줄범위·SHA 전면 재생성",
        "- 시행지침-조문-인용범위 게이트 12 재검증",
        "- legal-xref / legal-contrast 줄번호 기반 산출물 재구축·검증",
        "",
        "## 검사 사각지대",
        "",
    ])
    for item in report["blind_spots"]:
        lines.append(f"- {item}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="시행지침 조문 본문 무결성 검증")
    ap.add_argument("--md-dir", type=Path, default=DEFAULT_MD_DIR)
    ap.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    ap.add_argument("--source-mode", choices=["auto", "source", "markdown"], default="auto")
    ap.add_argument("--table-loss", type=Path, default=DEFAULT_TABLE_LOSS)
    ap.add_argument("--json-out", type=Path, default=DEFAULT_JSON)
    ap.add_argument("--md-out", type=Path, default=DEFAULT_MD)
    ap.add_argument("--limit", type=int, default=0, help="개발용 문서 수 제한")
    args = ap.parse_args(argv)

    if "HWP5TXT" not in os.environ and HWP5TXT_FALLBACK.exists():
        os.environ["HWP5TXT"] = str(HWP5TXT_FALLBACK)
    converter = load_converter()
    definition_keys = load_definition_keys(DEFAULT_DEFINITIONS)
    md_files = sorted(args.md_dir.glob("*/*.md"))
    if args.limit:
        md_files = md_files[:args.limit]
    table_loss = load_table_loss(args.table_loss)
    results = [
        build_document_result(
            p,
            args.source_dir,
            args.source_mode,
            definition_keys,
            converter,
            table_loss_pending=table_loss.get("status") == "pending_input",
        )
        for p in md_files
    ]
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "md_dir": str(args.md_dir),
            "source_dir": str(args.source_dir),
            "source_mode_requested": args.source_mode,
            "definition_canonical": str(DEFAULT_DEFINITIONS),
            "promotion_report": str(DEFAULT_PROMOTION_REPORT),
            "revalidation_report": str(DEFAULT_REVALIDATION),
            "table_loss": str(args.table_loss),
        },
        "summary": summarize(results, table_loss),
        "gate12_corpus_check": load_corpus_gate12(DEFAULT_SOURCE_CORPUS),
        "known_issue_6docs_232": known_issue_from_revalidation(DEFAULT_REVALIDATION),
        "table_loss_input": table_loss,
        "retransform_candidates": retransform_candidates(results, table_loss),
        "documents": [asdict(r) for r in results],
        "blind_spots": [
            "표/표 안 값은 재측정하지 않았고, 표 후보 행은 본문 일치율 계산에서 제외했다.",
            "HWP/PDF 진단 추출 도구가 실패한 문서는 `미검증`으로 분리하며 complete/본문누락 판정 분모에서 제외한다.",
            "공백·줄바꿈·마크업 정규화는 허용하므로 시각적 조판 손상은 별도 표/레이아웃 검증 대상이다.",
            "OCR 원본의 좌우 열 뒤섞임은 순서 지표로 잡히지만 문자 오인식 자체는 이 검증의 주 지표가 아니다.",
        ],
    }
    report["known_issue_6docs_232"]["current_breakdown"] = known_issue_current_breakdown(
        report["known_issue_6docs_232"], results
    )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_report(args.md_out, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
