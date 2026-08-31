#!/usr/bin/env python3
"""`contract/outputs.json` 형식의 선언적 계약에 공통 검사를 건다.

입력  .claude/skills/<팀>/<스킬>/contract/outputs.json — outBase/files,
      output_root/outputs, 산출물[] 형식의 실제 산출물
출력  표준출력에 스킬별 판정과 집계. --json 은 같은 내용을 기계 판독으로 낸다

  python3 scripts/check_outputs_contract.py                  # 전 스킬
  python3 scripts/check_outputs_contract.py --skill legal-term
  python3 scripts/check_outputs_contract.py --json

검사 셋만 본다 — required 파일 존재, topKeys 존재, schema 가 지정된 JSON·CSV
산출물의 JSON Schema 대조. 값 도메인·교차 제약은 각 스킬 검증기의 몫이며
여기서 복제하지 않는다.

종료코드 0=위반 없음, 1=위반 있음, 2=검사 대상 없음.
"""
import argparse
import csv
import json
import sys
from glob import glob
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
ROOT = SKILL.parents[3]
SKILLS = ROOT / ".claude" / "skills"


def safe_child(base, value, label):
    """value를 base 내부의 상대경로로 해석한다."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}가 비어 있거나 문자열이 아니다")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label}가 허용 경로 밖을 가리킨다: {value}")
    resolved_base = base.resolve()
    resolved = (resolved_base / relative).resolve(strict=False)
    if resolved != resolved_base and resolved_base not in resolved.parents:
        raise ValueError(f"{label}가 허용 경로 밖을 가리킨다: {value}")
    return resolved


def decode_json_pointer_token(token):
    """JSON Pointer 토큰 하나를 RFC 6901 폭으로 푼다."""
    return token.replace("~1", "/").replace("~0", "~")


def resolve_schema_ref(schema, schema_ref):
    """`file.json#/pointer` 참조에서 실제 하위 스키마를 고른다."""
    if "#" not in schema_ref:
        return schema
    fragment = schema_ref.split("#", 1)[1]
    if not fragment:
        return schema
    if not fragment.startswith("/"):
        raise ValueError(f"JSON Pointer 형식이 아니다: #{fragment}")

    node = schema
    for raw in fragment.lstrip("/").split("/"):
        key = decode_json_pointer_token(raw)
        if not isinstance(node, dict) or key not in node:
            raise KeyError(key)
        node = node[key]
    return node


def load_validator(contract_dir, schema_ref):
    """(검증함수, 미검사사유, 계약오류여부) 를 돌려준다."""
    try:
        import jsonschema
    except ImportError:
        why = "jsonschema 미설치 — 구조 계약을 건너뛰었다 (pip install jsonschema)"
        return None, why, False

    schema_name = str(schema_ref).split("#", 1)[0]
    schema_parts = Path(schema_name).parts
    schema_base = contract_dir.parent if schema_parts and schema_parts[0] == "contract" else contract_dir
    try:
        schema_path = safe_child(schema_base, schema_name, "schema")
    except ValueError as e:
        return None, str(e), True
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"스키마를 읽지 못했다 {schema_ref}: {e}", True
    try:
        check_schema = resolve_schema_ref(schema, str(schema_ref))
    except (KeyError, ValueError) as e:
        return None, f"스키마 참조를 해석하지 못했다 {schema_ref}: {e}", True

    # 검증기는 스키마의 `$schema` 선언에서 고른다. 이 저장소는 draft-07 과
    # 2020-12 를 함께 쓰며(실측 8개 중 4:4), 한쪽으로 고정하면 items·$ref
    # 해석이 달라져 없는 위반이 생기거나 있는 위반이 묻힌다.
    v = jsonschema.validators.validator_for(schema)(schema)
    if check_schema is schema:
        errors = v.iter_errors
    else:
        errors = lambda inst: v.descend(inst, check_schema)
    return (lambda inst: ["/".join(str(x) for x in e.path) + f" — {e.message}"
                          if e.path else e.message
                          for e in errors(inst)]), None, False


def read_output(path):
    """스키마 대조용 산출물을 파일 형식에 맞게 읽는다."""
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if path.suffix == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))
    raise ValueError(f"지원하지 않는 스키마 대조 파일 형식: {path.suffix}")


def expand_placeholders(path_text):
    """`<deck-id>` 같은 자리표시자를 현재 산출물 파일 목록으로 펼친다."""
    safe_child(ROOT, path_text, "산출물 path")
    if path_text.count("<") != path_text.count(">"):
        raise ValueError(f"산출물 path 자리표시자가 닫히지 않았다: {path_text}")
    if any(mark in path_text for mark in ("*", "?", "[")):
        raise ValueError(f"산출물 path에 직접 glob 문자를 쓸 수 없다: {path_text}")
    if "<" not in path_text and "sNN" not in path_text:
        return [safe_child(ROOT, path_text, "산출물 path")]

    pattern = ""
    in_placeholder = False
    for char in path_text:
        if char == "<":
            pattern += "*"
            in_placeholder = True
        elif char == ">":
            in_placeholder = False
        elif not in_placeholder:
            pattern += char
    pattern = pattern.replace("sNN", "s[0-9][0-9]")
    return [Path(match) for match in sorted(glob(str(ROOT / pattern)))]


def output_entries(spec):
    """지원하는 outputs.json 형식을 공통 엔트리로 정규화한다."""
    if isinstance(spec.get("files"), dict) and spec.get("outBase"):
        base = safe_child(ROOT, spec["outBase"], "outBase")
        return [
            {"name": name, "paths": [safe_child(base, name, "files key")], "spec": file_spec}
            for name, file_spec in sorted(spec["files"].items())
        ], None

    if isinstance(spec.get("outputs"), dict) and spec.get("output_root"):
        base = safe_child(ROOT, spec["output_root"], "output_root")
        return [
            {"name": name, "paths": [safe_child(base, name, "outputs key")], "spec": file_spec}
            for name, file_spec in sorted(spec["outputs"].items())
        ], None

    if isinstance(spec.get("산출물"), list):
        entries = []
        for item in spec["산출물"]:
            if isinstance(item, dict) and item.get("path"):
                entries.append({
                    "name": item["path"],
                    "paths": expand_placeholders(item["path"]),
                    "spec": item,
                })
        return entries, None

    keys = sorted(k for k in spec if not k.startswith("$"))
    return [], f"지원 형식이 아니다 — 최상위 키 {keys}"


def is_required(file_spec):
    """영문·국문 계약의 필수 여부를 같은 폭으로 읽는다."""
    if "required" in file_spec:
        return bool(file_spec["required"])
    if "필수" in file_spec:
        return bool(file_spec["필수"])
    return True


def check_one(spec_path):
    """outputs.json 하나를 검사해 결과 레코드를 만든다."""
    rel = spec_path.relative_to(ROOT)
    skill = spec_path.parents[1].name
    team = spec_path.parents[2].name
    rec = {"team": team, "skill": skill, "계약": str(rel), "판정": None,
           "미검사사유": None, "검사파일수": 0, "위반": [], "비고": []}

    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        rec["판정"] = "미검사"
        rec["미검사사유"] = f"계약 파일을 읽지 못했다: {e}"
        return rec

    try:
        entries, unsupported = output_entries(spec)
    except ValueError as e:
        rec["판정"] = "미검사"
        rec["미검사사유"] = f"산출물 경로 계약 오류: {e}"
        return rec
    if unsupported:
        rec["판정"] = "미검사"
        rec["미검사사유"] = unsupported
        return rec
    if not entries:
        rec["판정"] = "미검사"
        rec["미검사사유"] = "검사 가능한 산출물 선언 없음"
        return rec

    contract_dir = spec_path.parent
    for entry in entries:
        name = entry["name"]
        f = entry["spec"]
        if not isinstance(f, dict):
            rec["위반"].append(f"{name}: 파일 계약이 객체가 아니다")
            continue

        paths = [path for path in entry["paths"] if path.exists()]
        if not paths:
            if is_required(f):
                rec["위반"].append(f"필수 산출물 없음: {name}")
            else:
                # 계약이 미생성을 허용한 것은 결손이 아니다.
                status = f.get("status", f.get("상태", ""))
                note = f"미생성(계약상 허용): {name} — {status}"
                rec["비고"].append(note.rstrip(" —"))
            continue

        top_keys = f.get("topKeys", [])
        if top_keys and not isinstance(top_keys, list):
            rec["위반"].append(f"{name}: topKeys 가 배열이 아니다")
            top_keys = []

        for path in paths:
            if not top_keys and not f.get("schema"):
                rec["검사파일수"] += 1
                continue

            try:
                doc = read_output(path)
            except (OSError, UnicodeDecodeError, ValueError) as e:
                rec["위반"].append(f"{name}: 파일 파싱 실패 — {e}")
                continue
            rec["검사파일수"] += 1

            for key in top_keys:
                if not isinstance(doc, dict) or key not in doc:
                    rec["위반"].append(f"{name}: topKey 없음 — {key}")

            if f.get("schema"):
                check, why, contract_error = load_validator(contract_dir, f["schema"])
                if check is None:
                    bucket = rec["위반"] if contract_error else rec["비고"]
                    bucket.append(f"{name}: {why}")
                    continue
                errs = check(doc)
                for e in errs[:5]:
                    rec["위반"].append(f"{name} 스키마: {e[:200]}")
                if len(errs) > 5:
                    rec["위반"].append(f"{name} 스키마: 외 {len(errs) - 5}건")

    rec["판정"] = "실패" if rec["위반"] else "통과"
    return rec


def main():
    ap = argparse.ArgumentParser(description="outputs.json 선언적 계약 공통 검사")
    ap.add_argument("--skill", action="append",
                    help="이 스킬만 검사한다. 반복 지정 가능, 생략 시 전부")
    ap.add_argument("--json", action="store_true", help="기계 판독 출력")
    ap.add_argument("--root", default=None, help="저장소 루트 (테스트용)")
    a = ap.parse_args()

    global ROOT, SKILLS
    if a.root:
        ROOT = Path(a.root).resolve()
        SKILLS = ROOT / ".claude" / "skills"

    specs = sorted(SKILLS.glob("*/*/contract/outputs.json"))
    if a.skill:
        want = set(a.skill)
        specs = [p for p in specs if p.parents[1].name in want]
    if not specs:
        print("[2] outputs.json 을 가진 스킬이 없다", file=sys.stderr)
        return 2

    results = [check_one(p) for p in specs]
    tally = {k: sum(1 for r in results if r["판정"] == k)
             for k in ("통과", "실패", "미검사")}

    if a.json:
        print(json.dumps({"summary": {"대상": len(results), **tally},
                          "results": results}, ensure_ascii=False, indent=2))
    else:
        mark = {"통과": "OK  ", "실패": "FAIL", "미검사": "SKIP"}
        for r in results:
            tail = r["미검사사유"] or f"파일 {r['검사파일수']}건 검사"
            print(f"[{mark[r['판정']]}] {r['team']}/{r['skill']} — {tail}")
            for n in r["비고"]:
                print(f"       · {n}")
            for w in r["위반"]:
                print(f"       ✗ {w}")
        print(f"\n통과 {tally['통과']} · 실패 {tally['실패']} · "
              f"미검사 {tally['미검사']} / 대상 {len(results)}")

    return 1 if tally["실패"] else 0


if __name__ == "__main__":
    sys.exit(main())
