#!/usr/bin/env python3
"""등록된 검증기를 순서대로 실행하고 통과·실패·미검사를 집계한다.

입력  contract/verifier_registry.json — 검증기 경로·실행 순서·선행조건
      각 검증기가 읽는 산출물 (선행조건 경로로 선언돼 있다)
출력  output/kb/reports/_contract_verify.json — contract/report.schema.json 구조
      표준출력에 [OK ]·[FAIL]·[SKIP] 한 줄씩과 집계

  python3 scripts/run_verifiers.py                    # 전부
  python3 scripts/run_verifiers.py --skill legal-term # 일부만 (반복 지정 가능)
  python3 scripts/run_verifiers.py --dry-run          # 실행 계획만

선행조건이 없어 돌지 못한 검증기는 실패가 아니라 미검사다. 종료코드 2 를 낸
검증기도 검증 불가이므로 미검사로 집계한다. 종료코드 0=실패 없음, 1=실패 있음.
"""
import argparse
import glob
import importlib.util
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
ROOT = SKILL.parents[3]                      # .claude/skills/common/<스킬>/scripts
REGISTRY = SKILL / "contract" / "verifier_registry.json"

MARK = {"통과": "OK  ", "실패": "FAIL", "미검사": "SKIP"}
# 실패 표지. 검증기마다 보고 형식이 달라 한 패턴으로는 잡히지 않는다.
ERROR_MARKS = ("✗", "FAIL", "Traceback", "Error", "실패", "위반", "  - ")
NON_ERROR_MARKS = ("[PASS]", "[OK", "✓", "통과", "실패 없음", "위반 0", "오류 0")
VALID_KINDS = {"계약 검증", "품질 검증", "검사기 검증", "선행조건 게이트"}
REQUIRED_VERIFIER_FIELDS = {
    "id", "team", "skill", "order", "kind", "script", "args", "requires",
}
REQUIRED_IGNORED_FIELDS = {"script", "reason"}
VERIFIER_PATTERNS = (
    ".claude/skills/*/*/scripts/verify*.py",
    ".claude/skills/*/*/scripts/validate*.py",
    ".claude/skills/*/*/case/*check.py",
    ".claude/skills/*/*/case/*_test.py",
    ".claude/skills/*/*/case/*gate.py",
)


def relative_path_error(value):
    """저장소 상대경로 계약의 위반 사유. 빈 문자열이면 안전하다."""
    if not isinstance(value, str) or not value.strip():
        return "비어 있거나 문자열이 아니다"
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return "저장소 상대경로가 아니다"
    return ""


def string_list_error(value):
    """문자열 배열 계약의 위반 사유. 빈 문자열이면 안전하다."""
    if not isinstance(value, list):
        return "배열이 아니다"
    if any(not isinstance(item, str) or not item.strip() for item in value):
        return "비어 있거나 문자열이 아닌 항목이 있다"
    return ""


def missing_requirements(root, req):
    """선행조건 미충족 목록. 비어 있으면 돌릴 수 있다."""
    miss = []
    for pat in req.get("paths", []):
        # 매직 문자가 없는 경로도 glob 이 존재 여부로 답한다. 경로 결합은
        # os.path.join 으로 한다 — Path / 는 끝의 `/` 를 지워 디렉터리만
        # 고르는 패턴(`*/*/`)이 파일까지 무는 패턴으로 바뀐다.
        if not glob.glob(os.path.join(str(root), pat), recursive=True):
            miss.append(f"입력 없음 {pat}")
    for mod in req.get("modules", []):
        try:
            found = importlib.util.find_spec(mod) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            miss.append(f"모듈 없음 {mod}")
    return miss


def registry_errors(reg):
    """등록부 입력 계약 위반 목록. 빈 목록이면 실행할 수 있다."""
    if not isinstance(reg, dict):
        return ["등록부 최상위 값이 객체가 아니다"]
    report_path_error = relative_path_error(reg.get("reportPath"))
    if report_path_error:
        return [f"reportPath 가 {report_path_error}"]
    verifiers = reg.get("verifiers")
    if not isinstance(verifiers, list):
        return ["verifiers 가 배열이 아니다"]

    ignored = reg.get("ignoredVerifiers", [])
    if not isinstance(ignored, list):
        return ["ignoredVerifiers 가 배열이 아니다"]

    errors = []
    ids = set()
    active_scripts = set()
    for index, verifier in enumerate(verifiers, 1):
        if isinstance(verifier, dict):
            label = verifier.get("id", f"#{index}")
        else:
            label = f"#{index}"
        if not isinstance(verifier, dict):
            errors.append(f"{label}: 검증기 항목이 객체가 아니다")
            continue
        missing = sorted(REQUIRED_VERIFIER_FIELDS - set(verifier))
        if missing:
            errors.append(f"{label}: 필수 필드 없음 {missing}")
        verifier_id = verifier.get("id")
        if not isinstance(verifier_id, str) or not verifier_id:
            errors.append(f"{label}: id 가 비어 있거나 문자열이 아니다")
        elif verifier_id in ids:
            errors.append(f"{label}: id 중복")
        else:
            ids.add(verifier_id)
        for field in ("team", "skill"):
            if not isinstance(verifier.get(field), str) or not verifier.get(field):
                errors.append(f"{label}: {field} 가 비어 있거나 문자열이 아니다")
        if verifier.get("kind") not in VALID_KINDS:
            errors.append(f"{label}: kind 값 도메인 이탈 {verifier.get('kind')!r}")
        order = verifier.get("order")
        if (
            isinstance(order, bool)
            or not isinstance(order, (int, float))
            or not math.isfinite(order)
            or order < 1
        ):
            errors.append(f"{label}: order 가 1 이상의 유한한 숫자가 아니다")
        requires = verifier.get("requires")
        if not isinstance(requires, dict):
            errors.append(f"{label}: requires 가 객체가 아니다")
        else:
            paths = requires.get("paths", [])
            modules = requires.get("modules", [])
            path_items_error = string_list_error(paths)
            module_items_error = string_list_error(modules)
            if path_items_error:
                errors.append(f"{label}: requires.paths 가 {path_items_error}")
            elif any(relative_path_error(path) for path in paths):
                errors.append(f"{label}: requires.paths 에 저장소 밖 경로가 있다")
            if module_items_error:
                errors.append(f"{label}: requires.modules 가 {module_items_error}")
        args_error = string_list_error(verifier.get("args", []))
        if args_error:
            errors.append(f"{label}: args 가 {args_error}")
        script_error = relative_path_error(verifier.get("script"))
        if script_error:
            errors.append(f"{label}: script 가 {script_error}")
        elif not verifier["script"].endswith(".py"):
            errors.append(f"{label}: script 가 Python 파일이 아니다")
        else:
            if verifier["script"] in active_scripts:
                errors.append(f"{label}: script 중복")
            active_scripts.add(verifier["script"])

    ignored_scripts = set()
    for index, item in enumerate(ignored, 1):
        label = f"ignoredVerifiers #{index}"
        if not isinstance(item, dict):
            errors.append(f"{label}: 항목이 객체가 아니다")
            continue
        missing = sorted(REQUIRED_IGNORED_FIELDS - set(item))
        extra = sorted(set(item) - REQUIRED_IGNORED_FIELDS)
        if missing:
            errors.append(f"{label}: 필수 필드 없음 {missing}")
        if extra:
            errors.append(f"{label}: 허용되지 않은 필드 {extra}")
        script = item.get("script")
        script_error = relative_path_error(script)
        if script_error:
            errors.append(f"{label}: script 가 {script_error}")
        elif not script.endswith(".py"):
            errors.append(f"{label}: script 가 Python 파일이 아니다")
        elif script in active_scripts:
            errors.append(f"{label}: 실행 검증기와 script 가 중복된다")
        elif script in ignored_scripts:
            errors.append(f"{label}: script 중복")
        else:
            ignored_scripts.add(script)
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{label}: reason 이 비어 있거나 문자열이 아니다")
    return errors


def unregistered(root, reg):
    """디스크에는 있으나 레지스트리에 없는 검증기.

    등록부가 조용히 낡으면 검사하지 않은 것이 통과로 읽힌다. 실패로 세지는
    않는다 — 새 스킬이 만들어지는 중일 수 있다.
    """
    known = {v["script"] for v in reg["verifiers"]}
    known.update(item["script"] for item in reg.get("ignoredVerifiers", []))
    found = set()
    for pat in VERIFIER_PATTERNS:
        for p in glob.glob(os.path.join(str(root), pat)):
            found.add(os.path.relpath(p, root))
    return sorted(found - known)


def summarize(text, keep):
    """긴 출력을 뒤에서 keep 줄만 남긴다. 줄 길이도 잘라 리포트가 붓지 않게 한다."""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    return [l[:300] for l in lines[-keep:]]


def first_error(stdout, stderr):
    """처음 나온 실패 표지 줄. 무엇이 깨졌는지 리포트만 보고 알 수 있어야 한다."""
    for text in (stderr, stdout):
        for line in text.splitlines():
            s = line.strip()
            if (s and not any(m in s for m in NON_ERROR_MARKS)
                    and any(m in s for m in ERROR_MARKS)):
                return s[:300]
    return None


def run_one(root, v, timeout):
    """검증기 하나를 실행해 결과 레코드를 만든다."""
    rec = {"id": v["id"], "team": v["team"], "skill": v["skill"],
           "kind": v["kind"], "order": v["order"], "판정": None,
           "종료코드": None, "미검사사유": None, "명령": [], "소요초": None,
           "첫오류줄": None, "stdout요약": [], "stderr요약": []}

    script = root / v["script"]
    if not script.exists():
        rec["판정"] = "미검사"
        rec["미검사사유"] = f"검증기 없음 {v['script']}"
        return rec

    miss = missing_requirements(root, v.get("requires", {}))
    if miss:
        rec["판정"] = "미검사"
        rec["미검사사유"] = " · ".join(miss)
        return rec

    cmd = [sys.executable, str(script), *v.get("args", [])]
    rec["명령"] = [sys.executable, v["script"], *v.get("args", [])]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=str(root), capture_output=True,
                           text=True, timeout=timeout)
        code, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rec["판정"] = "실패"
        rec["소요초"] = round(time.time() - t0, 2)
        rec["첫오류줄"] = f"제한 시간 {timeout}초 초과"
        return rec
    rec["소요초"] = round(time.time() - t0, 2)
    rec["종료코드"] = code
    rec["stdout요약"] = summarize(out, 12)
    rec["stderr요약"] = summarize(err, 5)

    if code == 0:
        rec["판정"] = "통과"
    elif code == 2:
        # 검증 불가는 계약 위반이 아니다. 실패로 집계하면 거짓 경보가 된다.
        rec["판정"] = "미검사"
        rec["미검사사유"] = f"종료코드 2 (검증 불가) — {first_error(out, err) or ''}".strip()
    else:
        rec["판정"] = "실패"
        rec["첫오류줄"] = first_error(out, err) or f"종료코드 {code}"
    return rec


def main():
    ap = argparse.ArgumentParser(description="등록된 계약 검증기 일괄 실행")
    ap.add_argument("--skill", action="append",
                    help="이 스킬의 검증기만 실행한다. 반복 지정 가능, 생략 시 전부")
    ap.add_argument("--dry-run", action="store_true", help="실행 계획만 출력한다")
    ap.add_argument("--report", default=None,
                    help="리포트 경로. 생략 시 레지스트리의 reportPath")
    ap.add_argument("--root", default=None, help="저장소 루트 (테스트용)")
    ap.add_argument("--timeout", type=int, default=600, help="검증기별 제한 시간(초)")
    a = ap.parse_args()

    root = Path(a.root).resolve() if a.root else ROOT
    try:
        reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[2] 등록부를 읽지 못했다: {e}", file=sys.stderr)
        return 2

    errors = registry_errors(reg)
    if errors:
        for error in errors:
            print(f"[2] 등록부 형식 오류: {error}", file=sys.stderr)
        return 2

    verifiers = sorted(reg["verifiers"], key=lambda v: v["order"])
    registered = len(verifiers)

    if a.skill:
        want = set(a.skill)
        unknown = want - {v["skill"] for v in verifiers}
        if unknown:
            print(f"[2] 등록되지 않은 스킬: {sorted(unknown)}", file=sys.stderr)
            return 2
        verifiers = [v for v in verifiers if v["skill"] in want]

    stale = unregistered(root, reg)
    ignored = reg.get("ignoredVerifiers", [])
    print(f"저장소 루트: {root}")
    print(f"등록 {registered}건 중 대상 {len(verifiers)}건")
    if ignored:
        print(f"명시적 제외 {len(ignored)}건")
        for item in ignored:
            print(f"  제외 {item['script']} — {item['reason']}")
    if stale:
        print(f"미등록 검증기 {len(stale)}건 — 레지스트리에 넣어야 한다: "
              f"{', '.join(stale)}")
    print()

    if a.dry_run:
        for v in verifiers:
            miss = missing_requirements(root, v.get("requires", {}))
            state = "미검사 예정" if miss else "실행 예정"
            args = " ".join(v.get("args", []))
            print(f"{v['order']:>2}. [{state}] {v['id']} ({v['kind']})")
            print(f"      python3 {v['script']}" + (f" {args}" if args else ""))
            if miss:
                print(f"      사유: {' · '.join(miss)}")
        print(f"\n실행 예정 "
              f"{sum(1 for v in verifiers if not missing_requirements(root, v.get('requires', {})))} · "
              f"미검사 예정 "
              f"{sum(1 for v in verifiers if missing_requirements(root, v.get('requires', {})))}"
              f" / 대상 {len(verifiers)}")
        return 0

    results = []
    for v in verifiers:
        rec = run_one(root, v, a.timeout)
        results.append(rec)
        tail = rec["미검사사유"] or rec["첫오류줄"] or ""
        print(f"[{MARK[rec['판정']]}] {rec['id']} ({rec['kind']})"
              + (f" — {tail}" if tail else ""))

    tally = {k: sum(1 for r in results if r["판정"] == k)
             for k in ("통과", "실패", "미검사")}
    report = {
        "meta": {
            "생성시각": datetime.now().astimezone().isoformat(timespec="seconds"),
            "저장소루트": str(root),
            "레지스트리": os.path.relpath(REGISTRY, root),
            "인터프리터": sys.executable,
            "등록수": registered,
            "선택스킬": sorted(set(a.skill)) if a.skill else None,
            "미등록검증기": stale,
            "명시적제외검증기": ignored,
        },
        "summary": {
            "대상": len(results), **tally,
            "$comment": "미검사는 실패가 아니다. 선행조건 미충족과 종료코드 2 를 함께 담는다",
        },
        "results": results,
    }

    out = Path(a.report) if a.report else root / reg["reportPath"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")

    print(f"\n통과 {tally['통과']} · 실패 {tally['실패']} · 미검사 {tally['미검사']}"
          f" / 대상 {len(results)}")
    for r in results:
        if r["판정"] == "실패":
            print(f"  FAIL {r['id']} (종료코드 {r['종료코드']}): {r['첫오류줄'] or ''}")
    print(f"리포트: {out}")
    return 1 if tally["실패"] else 0


if __name__ == "__main__":
    sys.exit(main())
