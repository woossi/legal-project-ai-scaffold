#!/usr/bin/env python3
"""적대 검수를 통과한 시행지침 인용 자료의 현행 정본 전문을 안전하게 수집한다.

이 스크립트는 정의문 전용 기존 수집기를 재사용하지 않는다. 모든 네트워크 응답을
먼저 캐시하고 후보마다 checkpoint를 남긴다. 403/429·차단문구에서는 우회하거나
재시도하지 않고 즉시 중단하며, 연속 서비스/네트워크 오류에도 회로를 연다.

중요: 수집되는 전문은 수집일 기준 정본이다. 시행지침 작성 당시 적용 판본으로
귀속시키지 않으며 모든 행에 application_version_unresolved=true를 둔다.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import html
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import io
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import statute_common as sc  # noqa: E402


ROOT = Path(__file__).resolve().parents[5]
SEARCH_API = "https://www.law.go.kr/DRF/lawSearch.do"
DETAIL_API = "https://www.law.go.kr/DRF/lawService.do"
BLOCK_MARKERS = (
    "captcha", "access denied", "too many requests", "비정상적인 접근",
    "자동입력 방지", "서비스 이용이 제한", "접근이 차단",
)
OFFICIAL_XML_ROOTS = {
    "LawSearch", "OrdinSearch", "AdmRulSearch",
    "법령", "LawService", "AdmRulService",
}
GOV_ABBR = (
    ("서울특별시시", "서울특별시"), ("서울시", "서울특별시"),
    ("인천시", "인천광역시"), ("부산시", "부산광역시"),
    ("대구시", "대구광역시"), ("대전시", "대전광역시"),
    ("울산시", "울산광역시"), ("세종시", "세종특별자치시"),
)
BODY_TAGS = ("조문내용", "조내용", "항내용", "호내용", "목내용")


class CircuitOpen(RuntimeError):
    """차단 신호나 연속 오류 때문에 네트워크 수집을 안전 정지한다."""


def canon(value: str) -> str:
    return sc.strip_separators(value).strip()


def iso(value: str) -> str | None:
    return sc.iso_from_digit_text(value)


def tag_text(block: str, name: str) -> str:
    return sc.xml_tag_text(block, name, unescape_html=True)


def expand_gov(name: str) -> str:
    for short, full in GOV_ABBR:
        if name.startswith(short):
            return full + name[len(short):]
    return name


def block_reason(status: int, text: str) -> str | None:
    """실제 차단 페이지와 정상 법령 XML 안의 동명 문구를 구조로 구분한다."""
    if status in (403, 429):
        return f"HTTP {status}"
    lowered = text.lower()
    marker = next((value for value in BLOCK_MARKERS if value in lowered), None)
    if not marker:
        return None
    root_match = re.search(
        r"^\s*(?:<\?xml[^>]*>\s*)?<(?P<root>[^\s>/]+)", text, re.I)
    root = root_match.group("root") if root_match else ""
    if root in OFFICIAL_XML_ROOTS:
        return None
    return f"차단문구={marker}, root={root or '없음'}"


def parse_search(xml: str, target: str) -> list[dict]:
    rows = []
    element = "admrul" if target == "admrul" else "law"
    for block in re.findall(rf"<{element} id=\"\d+\">(.*?)</{element}>", xml, re.S):
        if target == "law":
            row = {
                "official_id": tag_text(block, "법령ID"),
                "detail_id": tag_text(block, "법령일련번호"),
                "official_name": tag_text(block, "법령명한글"),
                "abbreviation": tag_text(block, "법령약칭명"),
                "kind": tag_text(block, "법령구분명"),
                "authority": tag_text(block, "소관부처명"),
                "promulgation_date": tag_text(block, "공포일자"),
                "promulgation_no": tag_text(block, "공포번호"),
                "effective_date": tag_text(block, "시행일자"),
                "current_history": tag_text(block, "현행연혁코드"),
                "detail_param": "MST",
            }
        elif target == "ordin":
            row = {
                "official_id": tag_text(block, "자치법규ID"),
                "detail_id": tag_text(block, "자치법규일련번호"),
                "official_name": tag_text(block, "자치법규명"),
                "abbreviation": "",
                "kind": tag_text(block, "자치법규종류"),
                "authority": tag_text(block, "지자체기관명"),
                "promulgation_date": tag_text(block, "공포일자"),
                "promulgation_no": tag_text(block, "공포번호"),
                "effective_date": tag_text(block, "시행일자"),
                "current_history": "",
                "detail_param": "MST",
            }
        else:
            issued = tag_text(block, "발령일자")
            row = {
                "official_id": tag_text(block, "행정규칙ID"),
                "detail_id": tag_text(block, "행정규칙일련번호"),
                "official_name": tag_text(block, "행정규칙명"),
                "abbreviation": "",
                "kind": tag_text(block, "행정규칙종류"),
                "authority": tag_text(block, "소관부처명"),
                "promulgation_date": issued,
                "promulgation_no": tag_text(block, "발령번호"),
                "effective_date": tag_text(block, "시행일자") or issued,
                "current_history": tag_text(block, "현행연혁구분"),
                # 행정규칙 상세 API의 ID는 검색 응답 '행정규칙일련번호'다.
                "detail_param": "ID",
            }
        if row["official_name"] and row["detail_id"]:
            rows.append(row)
    return rows


def exact_pick(candidates: list[dict], wanted: str,
               renamed_to: str | None = None) -> tuple[dict | None, str]:
    exact = [row for row in candidates if canon(row["official_name"]) == wanted]
    if len(exact) == 1:
        return exact[0], "정확일치"
    if len(exact) > 1:
        current = [row for row in exact if row["current_history"] == "현행"]
        return (current or exact)[0], "정확일치(현행선택)"
    abbreviations = [row for row in candidates
                     if row["abbreviation"] and canon(row["abbreviation"]) == wanted]
    if len(abbreviations) == 1:
        return abbreviations[0], "공식약칭일치"
    if renamed_to:
        renamed = [row for row in candidates
                   if canon(row["official_name"]) == canon(renamed_to)]
        if len(renamed) == 1:
            return renamed[0], f"검증된명칭변천({renamed_to})"
    return None, "결과없음" if not candidates else f"불일치_후보{len(candidates)}"


def load_seed(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    if not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    corrections = {
        canon(key): row["교정표기"]
        for key, row in data.get("표기교정", {}).items()
        if isinstance(row, dict) and row.get("교정표기")
    }
    renames = {}
    for statute in data.get("statutes", []):
        for old in statute.get("명칭변천", []):
            if old.get("statute_key"):
                renames[canon(old["statute_key"])] = statute["정식명칭"]
    return corrections, renames


def load_existing(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    for row in json.loads(path.read_text(encoding="utf-8")).get("statutes", []):
        if row.get("검증상태") != "정본대조":
            continue
        match = re.search(r"[?&]target=(law|ordin|admrul).*?[?&]MST=(\d+)",
                          row.get("출처URL", ""))
        if not match:
            match = re.search(r"[?&]MST=(\d+)", row.get("출처URL", ""))
            target = "ordin" if "조례" in row.get("법령구분", "") else "law"
            detail_id = match.group(1) if match else ""
        else:
            target, detail_id = match.group(1), match.group(2)
        if not detail_id or target == "admrul":
            # 구 수집기는 행정규칙 URL에 MST를 사용해 ID 의미가 불확실하다.
            continue
        normalized = {
            "official_id": row.get("법령ID", ""),
            "detail_id": detail_id,
            "official_name": row.get("정식명칭", ""),
            "abbreviation": row.get("약칭", ""),
            "kind": row.get("법령구분", ""),
            "authority": row.get("소관", ""),
            "promulgation_date": (row.get("공포일자") or "").replace("-", ""),
            "promulgation_no": row.get("공포번호", ""),
            "effective_date": (row.get("시행일자") or "").replace("-", ""),
            "current_history": "현행",
            "detail_param": "MST",
            "target": target,
        }
        for field in ("statute_key", "실측표기", "정식명칭"):
            if row.get(field):
                out[canon(row[field])] = normalized
    return out


class SafeFetcher:
    def __init__(self, cache_dir: Path, oc: str, min_interval: float,
                 jitter: float, retries: int, max_consecutive_errors: int,
                 user_agent: str):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.oc = oc
        self.min_interval = max(1.0, min_interval)
        self.jitter = max(0.0, jitter)
        self.retries = max(0, retries)
        self.max_consecutive_errors = max(1, max_consecutive_errors)
        self.user_agent = user_agent
        self.last_request = 0.0
        self.consecutive_errors = 0
        self.network_request_count = 0
        self.cache_hit_count = 0

    def _key(self, endpoint: str, params: dict[str, str]) -> str:
        safe = {key: value for key, value in params.items() if key != "OC"}
        raw = endpoint + "?" + urllib.parse.urlencode(sorted(safe.items()))
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, endpoint: str, params: dict[str, str]) -> tuple[str, str, bool]:
        key = self._key(endpoint, params)
        body_path = self.cache_dir / f"{key}.xml.gz"
        meta_path = self.cache_dir / f"{key}.json"
        if body_path.exists():
            self.cache_hit_count += 1
            with gzip.open(body_path, "rt", encoding="utf-8", errors="replace") as handle:
                return handle.read(), body_path.name, True

        query = {**params, "OC": self.oc, "type": "XML"}
        url = endpoint + "?" + urllib.parse.urlencode(query)
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            if self.jitter:
                time.sleep(random.Random(key + str(attempt)).uniform(0, self.jitter))
            request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            try:
                self.last_request = time.monotonic()
                self.network_request_count += 1
                with urllib.request.urlopen(request, timeout=40) as response:
                    status = getattr(response, "status", 200)
                    raw = response.read()
                text = raw.decode("utf-8", "replace")
                blocked = block_reason(status, text)
                if blocked:
                    raise CircuitOpen(f"차단 신호 감지({blocked}); 우회 없이 중단")
                if status >= 500:
                    raise urllib.error.HTTPError(url, status, "server error", {}, None)
                with gzip.open(body_path, "wt", encoding="utf-8") as handle:
                    handle.write(text)
                meta_path.write_text(json.dumps({
                    "endpoint": endpoint,
                    "params_without_OC": {k: v for k, v in params.items() if k != "OC"},
                    "status": status,
                    "received_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                self.consecutive_errors = 0
                return text, body_path.name, False
            except CircuitOpen:
                raise
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 429):
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    message = f"HTTP {exc.code}; 우회·재시도 없이 중단"
                    if retry_after:
                        message += f" (Retry-After={retry_after})"
                    raise CircuitOpen(message) from exc
                self.consecutive_errors += 1
                error = f"HTTP {exc.code}"
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                self.consecutive_errors += 1
                detail = re.sub(r"\s+", " ", str(getattr(exc, "reason", exc)))[:160]
                error = f"{type(exc).__name__}: {detail}"

            if self.consecutive_errors >= self.max_consecutive_errors:
                raise CircuitOpen(
                    f"연속 오류 {self.consecutive_errors}회({error}); 회로 중단")
            if attempt >= self.retries:
                raise RuntimeError(f"요청 실패({error}, 재시도 {attempt}회)")
            time.sleep(min(30.0, 2.0 ** attempt))
        raise RuntimeError("unreachable")

    def get_binary(self, url: str) -> tuple[bytes, str, bool]:
        """공식 첨부파일을 같은 속도제한·차단회로·캐시 정책으로 받는다."""
        key = hashlib.sha256(url.encode()).hexdigest()
        body_path = self.cache_dir / f"{key}.bin"
        meta_path = self.cache_dir / f"{key}.json"
        if body_path.exists():
            self.cache_hit_count += 1
            return body_path.read_bytes(), body_path.name, True
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            if self.jitter:
                time.sleep(random.Random(key + str(attempt)).uniform(0, self.jitter))
            request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            try:
                self.last_request = time.monotonic()
                self.network_request_count += 1
                with urllib.request.urlopen(request, timeout=60) as response:
                    status = getattr(response, "status", 200)
                    raw = response.read()
                lowered = raw[:16384].decode("utf-8", "ignore").lower()
                if status in (403, 429) or any(marker in lowered for marker in BLOCK_MARKERS):
                    raise CircuitOpen(f"첨부 차단 신호 감지(status={status}); 우회 없이 중단")
                if status >= 500:
                    raise urllib.error.HTTPError(url, status, "server error", {}, None)
                body_path.write_bytes(raw)
                meta_path.write_text(json.dumps({
                    "url": url,
                    "status": status,
                    "received_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "content_length": len(raw),
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                self.consecutive_errors = 0
                return raw, body_path.name, False
            except CircuitOpen:
                raise
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 429):
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    message = f"첨부 HTTP {exc.code}; 우회·재시도 없이 중단"
                    if retry_after:
                        message += f" (Retry-After={retry_after})"
                    raise CircuitOpen(message) from exc
                self.consecutive_errors += 1
                error = f"HTTP {exc.code}"
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                self.consecutive_errors += 1
                detail = re.sub(r"\s+", " ", str(getattr(exc, "reason", exc)))[:160]
                error = f"{type(exc).__name__}: {detail}"
            if self.consecutive_errors >= self.max_consecutive_errors:
                raise CircuitOpen(
                    f"첨부 연속 오류 {self.consecutive_errors}회({error}); 회로 중단")
            if attempt >= self.retries:
                raise RuntimeError(f"첨부 요청 실패({error}, 재시도 {attempt}회)")
            time.sleep(min(30.0, 2.0 ** attempt))
        raise RuntimeError("unreachable")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def text_of(element: ET.Element, tag: str) -> str:
    values = []
    for child in element.iter():
        if local_name(child.tag) == tag and child.text:
            value = re.sub(r"\s+", " ", html.unescape(child.text)).strip()
            if value:
                values.append(value)
    return "\n".join(values)


def parse_provisions(xml: str) -> tuple[list[dict], dict]:
    """법령·자치법규·행정규칙 XML의 조문 계층을 공통 행으로 편다."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return [], {"parse_status": "XML파싱실패", "appendix_count": 0}
    law_units = [node for node in root.iter() if local_name(node.tag) == "조문단위"]
    ordinance_units = [node for node in root.iter() if local_name(node.tag) == "조"]
    units = law_units or ordinance_units
    rows, seen = [], set()
    for node in units:
        number = text_of(node, "조문번호") or text_of(node, "조번호")
        content_parts = []
        for body_tag in BODY_TAGS:
            content_parts.extend(
                re.sub(r"\s+", " ", html.unescape(child.text or "")).strip()
                for child in node.iter() if local_name(child.tag) == body_tag
            )
        content_parts = [part for part in content_parts if part]
        if not number and not content_parts:
            continue
        branch = text_of(node, "조문가지번호")
        title = text_of(node, "조문제목") or text_of(node, "조제목")
        signature = (number, branch, title, "\n".join(content_parts))
        if signature in seen:
            continue
        seen.add(signature)
        rows.append({
            "article_number": number,
            "article_branch": branch,
            "article_title": title,
            "article_status": text_of(node, "조문여부"),
            "article_effective_date": iso(text_of(node, "조문시행일자")),
            "text": "\n".join(content_parts),
            "paragraph_count": sum(1 for child in node.iter()
                                   if local_name(child.tag) == "항내용"),
            "item_count": sum(1 for child in node.iter()
                              if local_name(child.tag) == "호내용"),
            "subitem_count": sum(1 for child in node.iter()
                                 if local_name(child.tag) == "목내용"),
        })
    appendix_count = sum(
        1 for node in root.iter()
        if local_name(node.tag) in ("별표", "별지", "부칙", "별표단위", "별지단위")
    )
    attachments = []
    for node in root.iter():
        if local_name(node.tag) != "첨부파일":
            continue
        name = text_of(node, "첨부파일명")
        link = text_of(node, "첨부파일링크")
        if link:
            attachments.append({"name": name, "url": link.strip()})

    # 행정규칙 상세 XML은 구조화된 조문 노드 없이 `조문내용`을 형제 요소로
    # 반복하는 경우가 있다. 장 표제는 제외하고 `제n조(제목)` 단위만 보존한다.
    if not rows:
        article_re = re.compile(
            r"^\s*제\s*(?P<number>\d+)\s*조(?:\s*의\s*(?P<branch>\d+))?"
            r"(?:\s*[（(](?P<title>[^)）]+)[)）])?"
        )
        for node in root.iter():
            if local_name(node.tag) != "조문내용" or not node.text:
                continue
            content = re.sub(r"[ \t\r\f\v]+", " ", html.unescape(node.text)).strip()
            match = article_re.match(content)
            if not match:
                continue
            rows.append({
                "article_number": match.group("number"),
                "article_branch": match.group("branch") or "",
                "article_title": (match.group("title") or "").strip(),
                "article_status": "조문",
                "article_effective_date": None,
                "text": content,
                "paragraph_count": len(re.findall(r"(?:^|\n)\s*[①-⑳]", content)),
                "item_count": len(re.findall(r"(?:^|\n)\s*\d+\.", content)),
                "subitem_count": len(re.findall(r"(?:^|\n)\s*[가-하]\.", content)),
            })
    if not rows:
        fulltexts = []
        for node in root.iter():
            if local_name(node.tag) == "조문내용" and node.text:
                value = html.unescape(node.text).strip()
                if value:
                    fulltexts.append(value)
        if fulltexts:
            text = "\n".join(fulltexts)
            rows.append({
                "article_number": "",
                "article_branch": "",
                "article_title": "[비조문형 전문]",
                "article_status": "비조문형_전문",
                "article_effective_date": None,
                "text": text,
                "paragraph_count": len([line for line in text.splitlines() if line.strip()]),
                "item_count": 0,
                "subitem_count": 0,
            })
    return rows, {
        "parse_status": (
            "비조문형_전문" if rows and rows[0]["article_status"] == "비조문형_전문"
            else "파싱" if rows else "첨부전문_수집대기" if attachments else "조문구조없음"
        ),
        "appendix_count": appendix_count,
        "attachments": attachments,
    }


def extract_hwpx_text(raw: bytes) -> str:
    """HWPX ZIP의 section XML에서 문단별 텍스트를 읽는다."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        return ""
    lines = []
    for name in sorted(n for n in archive.namelist()
                       if n.startswith("Contents/section") and n.endswith(".xml")):
        try:
            root = ET.fromstring(archive.read(name))
        except ET.ParseError:
            continue
        for paragraph in root.iter():
            if local_name(paragraph.tag) != "p":
                continue
            parts = [node.text or "" for node in paragraph.iter()
                     if local_name(node.tag) == "t"]
            line = re.sub(r"\s+", " ", "".join(parts)).strip()
            if line:
                lines.append(line)
    return "\n".join(lines)


def hydrate_attachment_documents(state: dict, fetcher: SafeFetcher) -> None:
    """상세 XML 본문이 비어 있고 HWPX 첨부만 있는 정본을 보완한다."""
    for row in state.get("documents", {}).values():
        if row.get("provisions") or not row.get("attachments"):
            continue
        attachment = next(
            (item for item in row["attachments"]
             if item.get("name", "").lower().endswith(".hwpx")),
            row["attachments"][0],
        )
        raw, cache_file, cached = fetcher.get_binary(attachment["url"])
        text = extract_hwpx_text(raw) if attachment.get("name", "").lower().endswith(".hwpx") else ""
        row["attachment_cache_file"] = cache_file
        row["attachment_cache_hit"] = cached
        if text:
            row["provisions"] = [{
                "article_number": "",
                "article_branch": "",
                "article_title": "[첨부 비조문형 전문]",
                "article_status": "첨부_비조문형_전문",
                "article_effective_date": None,
                "text": text,
                "paragraph_count": len(text.splitlines()),
                "item_count": 0,
                "subitem_count": 0,
            }]
            row["parse_status"] = "첨부_비조문형_전문"
        else:
            row["parse_status"] = "첨부파싱실패"


def checkpoint_write(path: Path, state: dict) -> None:
    lightweight = {**state, "documents": {}}
    for key, row in state["documents"].items():
        lightweight["documents"][key] = {
            field: value for field, value in row.items() if field != "provisions"
        }
        lightweight["documents"][key]["provision_count"] = len(row.get("provisions", []))
    path.write_text(json.dumps(lightweight, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def rehydrate_documents(state: dict, cache_dir: Path) -> None:
    """재개 시 원 XML 캐시에서 조문 배열을 네트워크 없이 다시 구성한다."""
    for row in state.get("documents", {}).values():
        if "provisions" in row:
            continue
        cache_file = cache_dir / row["cache_file"]
        if not cache_file.exists():
            row["provisions"] = []
            row["parse_status"] = "캐시유실"
            continue
        with gzip.open(cache_file, "rt", encoding="utf-8", errors="replace") as handle:
            provisions, parse_meta = parse_provisions(handle.read())
        row.update(parse_meta)
        row["provisions"] = provisions


def materialize_outputs(state: dict, out_dir: Path, fetcher: SafeFetcher,
                        review: dict, artifact_prefix: str) -> None:
    statutes = sorted(state["statutes"].values(), key=lambda row: row["source_key"])
    status = collections.Counter(row["verification_status"] for row in statutes)
    master = {
        "meta": {
            "source": "국가법령정보센터 OPEN API",
            "collection_date": dt.date.today().isoformat(),
            "scope_review_status": review["meta"]["status"],
            "statute_count": len(statutes),
            "verification_status_distribution": dict(status),
            "application_version_warning": (
                "현행 정본 수집 결과이며 시행지침 작성 당시 적용 판본은 미확정"
            ),
            "script": ".claude/skills/legal/legal-statute/scripts/collect_guideline_articles.py",
        },
        "statutes": statutes,
    }
    (out_dir / f"{artifact_prefix}_statute_master.json").write_text(
        json.dumps(master, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    corpus_rows = []
    for document in sorted(state["documents"].values(),
                           key=lambda row: (row["target"], row["detail_id"])):
        corpus_rows.append(document)
    corpus_path = out_dir / f"{artifact_prefix}_article_corpus.jsonl.gz"
    # gzip header의 시각·파일명까지 고정해 checkpoint 재출력도 byte-identical하게 만든다.
    with corpus_path.open("wb") as raw_handle:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw_handle, compresslevel=9, mtime=0
        ) as zipped:
            with io.TextIOWrapper(zipped, encoding="utf-8", newline="\n") as handle:
                for row in corpus_rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = {
        "meta": {
            "status": state.get("run_status", "실행중"),
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "network_policy": {
                "min_interval_seconds": fetcher.min_interval,
                "jitter_seconds": fetcher.jitter,
                "retries": fetcher.retries,
                "circuit_break": "403/429/차단문구 즉시, 연속 오류 임계치",
                "cache": "모든 성공 응답 gzip 캐시; 동일 요청 재전송 금지",
            },
            "current_only": True,
            "application_version_unresolved": True,
        },
        "summary": {
            "review_queue_count": len(review["crawl_queue"]),
            "processed_source_count": len(statutes),
            "verified_source_count": status.get("정본대조", 0),
            "unmatched_source_count": status.get("미대조", 0),
            "error_source_count": status.get("API오류", 0),
            "deduplicated_official_document_count": len(corpus_rows),
            "parsed_provision_count": sum(len(row["provisions"]) for row in corpus_rows),
            "network_request_count_this_run": fetcher.network_request_count,
            "cache_hit_count_this_run": fetcher.cache_hit_count,
        },
        "circuit_reason": state.get("circuit_reason"),
        "unmatched": [row for row in statutes if row["verification_status"] != "정본대조"],
        "excluded_queues": {
            "type_classification_count": len(review.get("type_classification_queue", [])),
            "manual_nonstat_count": len(review.get("manual_source_queue", [])),
            "internal_guideline_count": len(review.get("internal_guideline_queue", [])),
            "quarantine_count": len(review.get("quarantine", [])),
        },
    }
    (out_dir / f"_{artifact_prefix}_crawl_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", type=Path,
                    default=ROOT / "output/legal/statute/guideline_article_scope_review.json")
    ap.add_argument("--existing-master", type=Path,
                    default=ROOT / "output/legal/statute/statute_master.json")
    ap.add_argument("--seed", type=Path,
                    default=ROOT / ".claude/skills/legal/legal-statute/case/정본대조.json")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "output/legal/statute")
    ap.add_argument("--cache-dir", type=Path,
                    default=ROOT / "output/legal/statute/_guideline_crawl_cache")
    ap.add_argument("--oc", default="test")
    ap.add_argument("--min-interval", type=float, default=1.2)
    ap.add_argument("--jitter", type=float, default=0.15)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--max-consecutive-errors", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0,
                    help="이번 실행에서 새로 처리할 source 수(0=전부); pilot에 사용")
    ap.add_argument("--source-key", action="append", default=[],
                    help="지정 source_key만 처리(반복 가능, 대표 유형 pilot에 사용)")
    ap.add_argument("--user-agent", default="legal-project-guideline-crawler/1.0")
    ap.add_argument("--artifact-prefix", default="guideline",
                    help="산출물 접두사(기본 guideline, T4는 guideline_t4)")
    ap.add_argument("--reconcile-only", action="store_true",
                    help="변경된 큐와 checkpoint를 네트워크 없이 대조·재출력")
    args = ap.parse_args()
    if not re.fullmatch(r"[a-z0-9_]+", args.artifact_prefix):
        print("artifact-prefix는 영문 소문자·숫자·밑줄만 허용", file=sys.stderr)
        return 2

    review = json.loads(args.review.read_text(encoding="utf-8"))
    if review.get("meta", {}).get("status") != "통과":
        print("적대 검수 미통과: 크롤링을 시작하지 않는다", file=sys.stderr)
        return 2
    args.out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = args.out_dir / f"_{args.artifact_prefix}_crawl_checkpoint.json"
    state = ({"statutes": {}, "documents": {}, "run_status": "실행중"}
             if not checkpoint.exists()
             else json.loads(checkpoint.read_text(encoding="utf-8")))
    rehydrate_documents(state, args.cache_dir)
    current_keys = {row["source_key"] for row in review["crawl_queue"]}
    state["statutes"] = {
        key: row for key, row in state["statutes"].items() if key in current_keys
    }
    reconciled_documents = {}
    for document_key, row in state["documents"].items():
        row["source_keys"] = [key for key in row.get("source_keys", [])
                              if key in current_keys]
        if row["source_keys"]:
            reconciled_documents[document_key] = row
    state["documents"] = reconciled_documents
    # 명시적으로 source-key를 준 호출은 이미 처리된 행도 다시 대조한다. 전체 미대조를
    # 재질의하지 않고, 검증된 교정표가 추가된 표적만 안전하게 갱신할 때 사용한다.
    if args.source_key:
        selected = set(args.source_key)
        for key in selected:
            state["statutes"].pop(key, None)
        refreshed_documents = {}
        for document_key, row in state["documents"].items():
            row["source_keys"] = [
                key for key in row.get("source_keys", []) if key not in selected
            ]
            if row["source_keys"]:
                refreshed_documents[document_key] = row
        state["documents"] = refreshed_documents
    # 회로가 이전에 열렸더라도 사용자가 다시 실행한 것은 명시적 재개다. 캐시는 유지한다.
    state["run_status"] = "실행중"
    state["circuit_reason"] = None
    corrections, renames = load_seed(args.seed)
    existing = load_existing(args.existing_master)
    fetcher = SafeFetcher(
        args.cache_dir, args.oc, args.min_interval, args.jitter,
        args.retries, args.max_consecutive_errors, args.user_agent,
    )
    if args.reconcile_only:
        remaining = len(review["crawl_queue"]) - len(state["statutes"])
        state["run_status"] = "완료" if remaining == 0 else "부분완료"
        checkpoint_write(checkpoint, state)
        materialize_outputs(state, args.out_dir, fetcher, review, args.artifact_prefix)
        print(
            f"네트워크 없는 큐 대조: 처리 {len(state['statutes'])}/"
            f"{len(review['crawl_queue'])}, 남음 {remaining}"
        )
        return 0
    try:
        hydrate_attachment_documents(state, fetcher)
    except CircuitOpen as exc:
        state["run_status"] = "회로중단"
        state["circuit_reason"] = str(exc)
        checkpoint_write(checkpoint, state)
        materialize_outputs(state, args.out_dir, fetcher, review, args.artifact_prefix)
        print(f"안전 회로 중단: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        state["run_status"] = "사용자중단"
        state["circuit_reason"] = "사용자 중단; 캐시와 마지막 완료 checkpoint 보존"
        checkpoint_write(checkpoint, state)
        materialize_outputs(state, args.out_dir, fetcher, review, args.artifact_prefix)
        print("사용자 중단: 캐시·checkpoint를 보존했다", file=sys.stderr)
        return 130

    pending = [row for row in review["crawl_queue"]
               if row["source_key"] not in state["statutes"]]
    if args.source_key:
        selected = set(args.source_key)
        pending = [row for row in pending if row["source_key"] in selected]
    if args.limit:
        pending = pending[:args.limit]
    try:
        for index, source in enumerate(pending, 1):
            key = source["source_key"]
            name = source["source_name_hint"]
            query = expand_gov(corrections.get(key) or name)
            wanted = canon(query)
            hit, match_method, hit_target = None, "결과없음", ""
            search_log = []

            seeded = existing.get(key) or existing.get(canon(name))
            if seeded and canon(seeded["official_name"]) in (key, wanted):
                hit = dict(seeded)
                hit_target = hit.pop("target")
                match_method = "기존_API정본_재사용"
            else:
                for target in source["targets"]:
                    xml, cache_file, cached = fetcher.get(SEARCH_API, {
                        "target": target, "query": query, "display": "100",
                    })
                    candidates = parse_search(xml, target)
                    candidate, how = exact_pick(candidates, wanted, renames.get(key))
                    if candidate and canon(query) != canon(name):
                        how = f"검증된조회교정({how})"
                    search_log.append({
                        "target": target, "query": query,
                        "candidate_count": len(candidates), "result": how,
                        "cache_file": cache_file, "cache_hit": cached,
                    })
                    if candidate:
                        hit, match_method, hit_target = candidate, how, target
                        break

            row = {
                **source,
                "verification_status": "정본대조" if hit else "미대조",
                "match_method": match_method,
                "query_name": query if canon(query) != canon(name) else None,
                "official_name": hit["official_name"] if hit else "",
                "official_id": hit["official_id"] if hit else "",
                "official_kind": hit["kind"] if hit else "",
                "authority": hit["authority"] if hit else "",
                "promulgation_number": hit["promulgation_no"] if hit else "",
                "promulgation_date": iso(hit["promulgation_date"]) if hit else None,
                "current_effective_date": iso(hit["effective_date"]) if hit else None,
                "target": hit_target,
                "detail_id": hit["detail_id"] if hit else "",
                "detail_param": hit["detail_param"] if hit else "",
                "application_version_unresolved": True,
                "search_log": search_log,
            }
            if hit:
                document_key = f"{hit_target}:{hit['detail_id']}"
                if document_key not in state["documents"]:
                    detail_xml, cache_file, cached = fetcher.get(DETAIL_API, {
                        "target": hit_target, hit["detail_param"]: hit["detail_id"],
                    })
                    provisions, parse_meta = parse_provisions(detail_xml)
                    state["documents"][document_key] = {
                        "document_key": document_key,
                        "source_keys": [key],
                        "target": hit_target,
                        "detail_id": hit["detail_id"],
                        "detail_param": hit["detail_param"],
                        "official_id": hit["official_id"],
                        "official_name": hit["official_name"],
                        "official_kind": hit["kind"],
                        "authority": hit["authority"],
                        "current_effective_date": iso(hit["effective_date"]),
                        "application_version_unresolved": True,
                        "cache_file": cache_file,
                        "cache_hit": cached,
                        **parse_meta,
                        "provisions": provisions,
                    }
                elif key not in state["documents"][document_key]["source_keys"]:
                    state["documents"][document_key]["source_keys"].append(key)

            # 상세 수집까지 성공한 뒤에만 source를 완료 처리한다. 상세 요청 중
            # 회로가 열리면 다음 실행에서 검색 캐시를 재사용해 같은 source를 재개한다.
            state["statutes"][key] = row

            checkpoint_write(checkpoint, state)
            if index % 25 == 0:
                materialize_outputs(state, args.out_dir, fetcher, review, args.artifact_prefix)
            print(
                f"[{index}/{len(pending)}] {name[:42]:44s} "
                f"{row['verification_status']} {match_method}", flush=True,
            )
    except CircuitOpen as exc:
        state["run_status"] = "회로중단"
        state["circuit_reason"] = str(exc)
        checkpoint_write(checkpoint, state)
        materialize_outputs(state, args.out_dir, fetcher, review, args.artifact_prefix)
        print(f"안전 회로 중단: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        state["run_status"] = "사용자중단"
        state["circuit_reason"] = "사용자 중단; 캐시와 마지막 완료 checkpoint 보존"
        checkpoint_write(checkpoint, state)
        materialize_outputs(state, args.out_dir, fetcher, review, args.artifact_prefix)
        print("사용자 중단: 캐시·checkpoint를 보존했다", file=sys.stderr)
        return 130
    except Exception as exc:
        state["run_status"] = "오류중단"
        state["circuit_reason"] = f"{type(exc).__name__}: {exc}"
        checkpoint_write(checkpoint, state)
        materialize_outputs(state, args.out_dir, fetcher, review, args.artifact_prefix)
        print(f"오류 중단: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4

    remaining = len(review["crawl_queue"]) - len(state["statutes"])
    state["run_status"] = "완료" if remaining == 0 else "부분완료"
    checkpoint_write(checkpoint, state)
    materialize_outputs(state, args.out_dir, fetcher, review, args.artifact_prefix)
    print(
        f"{state['run_status']}: 처리 {len(state['statutes'])}/"
        f"{len(review['crawl_queue'])}, 정본 문서 {len(state['documents'])}, "
        f"남음 {remaining}, 네트워크 {fetcher.network_request_count}, "
        f"캐시 적중 {fetcher.cache_hit_count}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
