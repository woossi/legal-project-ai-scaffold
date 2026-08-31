#!/usr/bin/env python3
"""택지정보 파일데이터의 BLS5_DSTRC_MASTER 원본 스냅샷을 수집한다.

이 스크립트는 지구단계정보 ZIP·CSV만 소유한다. 시행지침과 도면 첨부는
``collect.py index/fetch``가 계속 수집한다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


TABLE = "BLS5_DSTRC_MASTER"
HOST = "https://openapi.jigu.go.kr"
SOURCE_PAGE = f"{HOST}/down/detail.do?table={TABLE}"
TITLE_ENDPOINT = f"{HOST}/down/title.json"
LIST_ENDPOINT = f"{HOST}/api/list.json"
FILE_EXISTS_ENDPOINT = f"{HOST}/openApi/fileExist.json"
DOWNLOAD_ENDPOINT = f"{HOST}/openApi/down.do"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36")
PROJECT_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_OUT = PROJECT_ROOT / "output/legal/source/jigu/bls5_dstrc_master"
MANIFEST_SCHEMA = Path(__file__).resolve().parents[1] / "contract/master.schema.json"
REQUIRED_COLUMNS = {"DSTRC_APPN_NO", "DSTRC_NM"}
USAGE_KEYS = {
    "userJobTp", "userJobCpNm", "userJobClassNm", "userUsePurpose", "field"
}
USAGE_OPTIONAL_KEYS = {"userJobTpEtc", "userUsePurposeEtc", "fieldEtc"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def select_master_asset(payload: dict) -> dict:
    """목록 응답에서 전국 CSV 중 가장 최근에 생성된 파일을 고른다."""
    rows = payload.get("list") if isinstance(payload, dict) else None
    candidates = [
        row for row in (rows or [])
        if row.get("table") == TABLE
        and str(row.get("fileTy", "")).lower() == "csv"
        and str(row.get("ctprvn", "")) == "00"
    ]
    if not candidates:
        raise ValueError(f"{TABLE} 전국 CSV가 목록 응답에 없다")
    return max(candidates, key=lambda row: (
        str(row.get("stdrDe") or str(row.get("dt", "")).replace("-", "")),
        int(row.get("fileNo") or 0),
    ))


def _decode_csv(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV 인코딩은 UTF-8 또는 CP949가 아니다")


def _parse_csv(raw: bytes) -> dict:
    text, encoding = _decode_csv(raw)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    columns = reader.fieldnames or []
    missing = REQUIRED_COLUMNS - set(columns)
    if missing:
        raise ValueError(f"지구 마스터 필수 컬럼이 없다: {sorted(missing)}")
    rows = list(reader)
    districts = {
        row["DSTRC_APPN_NO"].strip()
        for row in rows
        if row.get("DSTRC_APPN_NO", "").strip()
    }
    return {
        "encoding": encoding,
        "columns": columns,
        "rowCount": len(rows),
        "uniqueDistrictCount": len(districts),
    }


def parse_master_archive(blob: bytes) -> dict:
    """공식 ZIP에서 지구 마스터 CSV 한 개를 읽고 구조를 검사한다."""
    if not blob or not zipfile.is_zipfile(io.BytesIO(blob)):
        raise ValueError("다운로드 응답은 ZIP이 아니다")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        members = [name for name in archive.namelist()
                   if not name.endswith("/") and name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"ZIP의 CSV 파일 수는 1이어야 한다: {len(members)}")
        member_name = members[0]
        raw = archive.read(member_name)
    parsed = _parse_csv(raw)
    return {"memberName": member_name, "csvBytes": raw, **parsed}


def _asset_query(asset: dict) -> dict[str, str]:
    return {
        "fileTy": str(asset["fileTy"]),
        "stdrDe": str(asset.get("stdrDe") or str(asset["dt"]).replace("-", "")),
        "ctprvn": str(asset["ctprvn"]),
        "table": str(asset["table"]),
        "fileNo": str(asset["fileNo"]),
    }


def public_download_url(asset: dict) -> str:
    """개인·기관 이용정보를 제외한 공식 파일 식별 URL을 만든다."""
    return DOWNLOAD_ENDPOINT + "?" + urllib.parse.urlencode(_asset_query(asset))


def validate_usage_profile(profile: dict) -> dict:
    missing = sorted(key for key in USAGE_KEYS if not profile.get(key))
    if missing:
        raise ValueError(f"이용정보 필수값이 없다: {missing}")
    allowed = USAGE_KEYS | USAGE_OPTIONAL_KEYS
    normalized = {key: profile[key] for key in allowed if key in profile}
    field = normalized["field"]
    if isinstance(field, list):
        normalized["field"] = ",".join(str(value) for value in field)
    else:
        normalized["field"] = str(field)
    normalized.setdefault("userJobTpEtc", "SYSTEM_NONE")
    normalized.setdefault("userUsePurposeEtc", "SYSTEM_NONE")
    normalized.setdefault("fieldEtc", "SYSTEM_NONE")
    return normalized


def build_download_url(asset: dict, usage_profile: dict) -> str:
    """공식 화면이 요구하는 이용정보를 포함한 실제 다운로드 URL을 만든다."""
    params = {
        **_asset_query(asset),
        **validate_usage_profile(usage_profile),
        "dt": str(asset["dt"]),
        "nm": str(asset["table"]),
        "ntfcDe": str(asset["ntfcDe"]),
        "fileNm": str(asset["fileNm"]),
        "testAt": "Y",
    }
    return DOWNLOAD_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _safe_file_name(name: str) -> str:
    name = re.sub(r"[\x00-\x1f/\\]", "_", name).strip()
    return name or "source.csv"


def _snapshot_consistency_problems(asset: dict, csv_name: str,
                                   row_count: int) -> list[str]:
    """공식 자산 메타와 ZIP 내부 CSV가 같은 판본인지 검사한다."""
    problems = []
    stdr_de = asset.get("stdrDe")
    if not isinstance(stdr_de, str) or not re.fullmatch(r"[0-9]{8}", stdr_de):
        problems.append("asset stdrDe는 8자리 날짜여야 한다")
    elif stdr_de not in Path(csv_name).name:
        problems.append(
            f"ZIP 내부 CSV 기준일이 asset stdrDe와 다르다: {csv_name} / {stdr_de}"
        )

    expected_rows = asset.get("totcnt")
    if not isinstance(expected_rows, int) or isinstance(expected_rows, bool):
        problems.append("asset totcnt는 정수여야 한다")
    elif expected_rows != row_count:
        problems.append(
            f"공식 행 수와 CSV 행 수가 다르다: {expected_rows} / {row_count}"
        )
    return problems


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part-{os.getpid()}")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def save_snapshot(out_base: Path | str, asset: dict, archive_blob: bytes,
                  source_mode: str, force: bool = False) -> Path:
    """원본 ZIP·CSV와 해시 메타를 기준일 디렉터리에 보존한다."""
    parsed = parse_master_archive(archive_blob)
    consistency_problems = _snapshot_consistency_problems(
        asset, parsed["memberName"], parsed["rowCount"]
    )
    if consistency_problems:
        raise ValueError("; ".join(consistency_problems))
    out_base = Path(out_base)
    snapshot = out_base / str(asset["stdrDe"])
    manifest_path = snapshot / "manifest.json"
    archive_hash = sha256(archive_blob)
    csv_hash = sha256(parsed["csvBytes"])

    if manifest_path.is_file() and not force:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (existing.get("archive", {}).get("sha256") == archive_hash
                and existing.get("csv", {}).get("sha256") == csv_hash):
            problems = verify_snapshot(snapshot)
            if problems:
                raise ValueError("기존 스냅샷 검증 실패: " + "; ".join(problems))
            return snapshot
        raise FileExistsError(f"기준일 {asset['stdrDe']} 스냅샷의 바이트가 다르다")

    csv_name = _safe_file_name(Path(parsed["memberName"]).name)
    archive_name = _safe_file_name(str(asset["fileNm"]) + ".zip")
    manifest = {
        "schemaVersion": 1,
        "table": TABLE,
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "source": {
            "kind": "jigu-file-data",
            "mode": source_mode,
            "page": SOURCE_PAGE,
            "listEndpoint": LIST_ENDPOINT,
            "downloadUrl": public_download_url(asset),
            "usageProfileStored": False,
        },
        "asset": {key: asset.get(key) for key in (
            "fileNo", "fileTy", "fileNm", "stdrDe", "dt", "ctprvn",
            "ctprvnNm", "ntfcDe", "totcnt"
        )},
        "archive": {
            "fileName": archive_name,
            "bytes": len(archive_blob),
            "sha256": archive_hash,
        },
        "csv": {
            "fileName": csv_name,
            "bytes": len(parsed["csvBytes"]),
            "sha256": csv_hash,
            "encoding": parsed["encoding"],
            "columns": parsed["columns"],
            "rowCount": parsed["rowCount"],
            "uniqueDistrictCount": parsed["uniqueDistrictCount"],
        },
    }
    _atomic_write(snapshot / archive_name, archive_blob)
    _atomic_write(snapshot / csv_name, parsed["csvBytes"])
    _atomic_write(manifest_path,
                  (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
    return snapshot


def verify_snapshot(snapshot: Path | str) -> list[str]:
    snapshot = Path(snapshot)
    manifest_path = snapshot / "manifest.json"
    if not manifest_path.is_file():
        return ["manifest.json 없음"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"manifest.json 읽기 실패: {error}"]
    problems = []
    schema = json.loads(MANIFEST_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for error in sorted(validator.iter_errors(manifest), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in error.path) or "$"
        problems.append(f"manifest schema {location}: {error.message}")
    if manifest.get("schemaVersion") != 1 or manifest.get("table") != TABLE:
        problems.append("manifest 식별자 불일치")
    for label in ("archive", "csv"):
        info = manifest.get(label, {})
        file_name = str(info.get("fileName", ""))
        if not file_name or Path(file_name).is_absolute() or Path(file_name).name != file_name:
            problems.append(f"{label} fileName 경로가 스냅샷 밖을 가리킨다")
            continue
        path = snapshot / file_name
        if not path.is_file():
            problems.append(f"{label} 파일 없음")
            continue
        blob = path.read_bytes()
        if sha256(blob) != info.get("sha256"):
            problems.append(f"{label.upper()} sha256 불일치")
        if len(blob) != info.get("bytes"):
            problems.append(f"{label} bytes 불일치")
    csv_info = manifest.get("csv", {})
    csv_name = str(csv_info.get("fileName", ""))
    csv_path = snapshot / csv_name if Path(csv_name).name == csv_name else snapshot / "__invalid__"
    if csv_path.is_file() and not any("CSV sha256" in item for item in problems):
        try:
            parsed = _parse_csv(csv_path.read_bytes())
        except ValueError as error:
            problems.append(str(error))
        else:
            for key in ("columns", "rowCount", "uniqueDistrictCount", "encoding"):
                if parsed[key] != csv_info.get(key):
                    problems.append(f"CSV {key} 불일치")
            asset_info = manifest.get("asset", {})
            if isinstance(asset_info, dict):
                problems.extend(_snapshot_consistency_problems(
                    asset_info, csv_name, parsed["rowCount"]
                ))
    return problems


def _post_json(url: str, data: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode(),
        headers={"User-Agent": UA, "Referer": SOURCE_PAGE,
                 "X-Requested-With": "XMLHttpRequest"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def discover_master_asset() -> dict:
    title = _post_json(TITLE_ENDPOINT, {"table": TABLE})
    notice_month = title.get("ntfcDe")
    if not notice_month:
        raise ValueError("최신 기준 고시월을 확인할 수 없다")
    payload = _post_json(LIST_ENDPOINT, {
        "tNm": TABLE, "table": TABLE, "ctprvn": "", "ntfcDe": notice_month,
    })
    return select_master_asset(payload)


def _file_exists(asset: dict) -> bool:
    payload = _post_json(FILE_EXISTS_ENDPOINT, _asset_query(asset))
    return bool(payload.get("exist"))


def download_master(asset: dict, usage_profile: dict) -> bytes:
    if not _file_exists(asset):
        raise FileNotFoundError("공식 파일 존재 확인이 실패했다")
    request = urllib.request.Request(
        build_download_url(asset, usage_profile),
        headers={"User-Agent": UA, "Referer": SOURCE_PAGE},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            blob = response.read()
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("공식 파일 다운로드 실패") from None
    parse_master_archive(blob)
    return blob


def _load_profile(path: Path) -> dict:
    return validate_usage_profile(json.loads(path.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="택지정보 지구 마스터 원본 수집기")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("discover", help="최신 전국 CSV 메타만 조회")

    fetch = commands.add_parser("fetch", help="최신 전국 CSV ZIP 스냅샷 보존")
    source = fetch.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-zip", type=Path,
                        help="공식 화면에서 직접 받은 ZIP")
    source.add_argument("--usage-profile", type=Path,
                        help="공식 이용정보 JSON. 값은 산출물에 저장하지 않는다")
    fetch.add_argument("--out-base", type=Path, default=DEFAULT_OUT)
    fetch.add_argument("--force", action="store_true")

    verify = commands.add_parser("verify", help="보존한 스냅샷 해시·행 수 검증")
    verify.add_argument("--snapshot", type=Path)
    verify.add_argument("--out-base", type=Path, default=DEFAULT_OUT)

    args = parser.parse_args(argv)
    if args.command == "discover":
        print(json.dumps(discover_master_asset(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "fetch":
        asset = discover_master_asset()
        if args.source_zip:
            blob = args.source_zip.read_bytes()
            mode = "manual"
        else:
            blob = download_master(asset, _load_profile(args.usage_profile))
            mode = "official-form"
        snapshot = save_snapshot(args.out_base, asset, blob, mode, args.force)
        print(snapshot)
        return 0

    snapshots = [args.snapshot] if args.snapshot else [
        path for path in args.out_base.iterdir() if path.is_dir()
    ] if args.out_base.is_dir() else []
    if not snapshots:
        print("검증할 지구 마스터 스냅샷이 없다", file=sys.stderr)
        return 1
    failed = False
    for snapshot in sorted(snapshots):
        problems = verify_snapshot(snapshot)
        if problems:
            failed = True
            for problem in problems:
                print(f"{snapshot}: {problem}", file=sys.stderr)
        else:
            print(f"{snapshot}: 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
