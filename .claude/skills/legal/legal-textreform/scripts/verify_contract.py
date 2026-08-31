#!/usr/bin/env python3
"""병합 md 가 contract/frontmatter.json 을 만족하는지 검증한다.

  python3 scripts/verify_contract.py

필드 존재·순서·값 도메인, 파일명과 지구번호 일치, meta.json 대조,
그리고 `표`(기록값 >= 실측, 하한)·`조문수`(기록값 == 실측, 정확 일치) 를
본문에서 실제로 세어 기록값과 대조한다.

PyYAML 없이 동작한다 — frontmatter 는 merge_md.py 가 찍는 고정 형식이므로
줄 단위로 읽는다.

종료코드 0=계약 충족, 1=위반, 2=검증 불가.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.join(HERE, "..", "contract")
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))


def unavailable(message):
    print(f"[2] {message}", file=sys.stderr)
    sys.exit(2)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        unavailable(f"JSON 읽기 실패: {path}: {exc}")
    except json.JSONDecodeError as exc:
        unavailable(f"JSON 파싱 실패: {path}: {exc}")


def parse_fm(text):
    """frontmatter 를 (스칼라 dict, 원본구성 list, 키 순서) 로 파싱."""
    m = re.match(r"---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return None, None, None
    body, scal, items, order = m.group(1), {}, [], []
    cur = None
    for line in body.split("\n"):
        if not line.strip():
            continue
        if re.match(r"^\s+- 파일:", line):
            cur = {}
            items.append(cur)
        if line.startswith(" ") or line.startswith("-"):
            mm = re.match(r"^\s+-?\s*([^:]+):\s*(.*)$", line)
            if mm and cur is not None:
                cur[mm.group(1).strip()] = mm.group(2).strip().strip('"')
            continue
        mm = re.match(r"^([^:]+):\s*(.*)$", line)
        if mm:
            k = mm.group(1).strip()
            order.append(k)
            scal[k] = mm.group(2).strip().strip('"')
    return scal, items, order


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None, help="저장소 루트 (테스트용)")
    a = ap.parse_args()
    root = os.path.abspath(a.root) if a.root else ROOT

    spec = load_json(os.path.join(CONTRACT, "frontmatter.json"))
    base = os.path.join(root, spec["outBase"])
    if not os.path.isdir(base):
        sys.exit(f"[2] 산출물 없음: {base}")

    # 지구 메타는 지구마다 흩어져 있지 않고 통합 파일 하나에 있다.
    metas = {}
    store_path = os.path.join(root, "output", "legal", "시행지침", "meta.json")
    try:
        store = load_json(store_path)
        metas = {d["dstrcAppnNo"]: d for d in store.get("districts", [])}
    except KeyError as exc:
        print(f"  ! {store_path} 의 districts[].dstrcAppnNo 누락으로 지구명·지역 대조를 건너뛴다: {exc}")
    if not metas:
        # 대조 없이 통과시키면 검증이 조용히 무력해진다. 그 사실을 알린다.
        print(f"  ! {store_path} 를 읽지 못해 지구명·지역 대조를 건너뛴다")

    fields, item_spec = spec["fields"], spec["originItem"]
    order_spec = spec["fieldOrder"]
    namepat = re.compile(spec["fileNamePattern"])

    files, v = 0, 0
    for dp, _, fns in os.walk(base):
        for fn in sorted(fns):
            if not fn.endswith(".md"):
                continue
            files += 1
            path = os.path.join(dp, fn)
            rel = os.path.relpath(path, base)
            text = open(path, encoding="utf-8").read()
            scal, items, order = parse_fm(text)
            if scal is None:
                print(f"  ✗ {rel}: frontmatter 없음")
                v += 1
                continue

            # 필수 필드 + 값 도메인
            for k in order:
                if k not in fields:
                    print(f"  ✗ {rel}: 계약에 없는 frontmatter 필드 — {k}")
                    v += 1
            for k, f in fields.items():
                if f["required"] and k not in scal:
                    print(f"  ✗ {rel}: 필수 필드 없음 — {k}")
                    v += 1
                    continue
                if k not in scal:
                    continue
                val = scal[k]
                if f["type"] == "enum" and val not in f["values"]:
                    print(f"  ✗ {rel}: {k} 미허용값 {val!r}")
                    v += 1
                if f["type"] == "int":
                    if not re.fullmatch(r"-?\d+", val):
                        print(f"  ✗ {rel}: {k} 정수 아님 {val!r}")
                        v += 1
                    elif int(val) < f.get("min", -10**9):
                        print(f"  ✗ {rel}: {k}={val} < min {f['min']}")
                        v += 1

            # 필드 순서
            got = [k for k in order if k in order_spec]
            if got != order_spec:
                print(f"  ✗ {rel}: 필드 순서가 계약과 다르다")
                v += 1

            # 파일명 ↔ 지구명 (지구번호는 파일명에 없다. frontmatter ↔ meta.json 으로 대조한다)
            nm = namepat.match(fn)
            if not nm:
                print(f"  ✗ {rel}: 파일명 규약 위반")
                v += 1
            elif nm.group("지구명").strip() != scal.get("지구명", "").strip():
                print(f"  ✗ {rel}: 파일명 지구명({nm.group('지구명')}) != frontmatter({scal.get('지구명')})")
                v += 1

            no = scal.get("지구번호")
            # meta.json 대조
            if no in metas:
                # 원본 dstrcNm 에 후행 공백이 섞여 있다(송도 첨단산업클러스터C).
                # 파일명·frontmatter 는 trim 한 값을 쓰므로 비교도 trim 기준이다.
                if scal.get("지구명", "").strip() != (metas[no].get("dstrcNm") or "").strip():
                    print(f"  ✗ {rel}: 지구명이 meta.json 과 다르다")
                    v += 1
                if scal.get("지역") != metas[no].get("region"):
                    print(f"  ✗ {rel}: 지역이 meta.json 과 다르다")
                    v += 1

            # 원본구성
            for it in items:
                for k in it:
                    if k not in item_spec:
                        print(f"  ✗ {rel}: 원본구성 항목에 계약 외 필드 — {k}")
                        v += 1
                for k, f in item_spec.items():
                    if f["required"] and k not in it:
                        print(f"  ✗ {rel}: 원본구성 항목에 {k} 없음")
                        v += 1
                    elif f["type"] == "enum" and it.get(k) not in f["values"]:
                        print(f"  ✗ {rel}: 원본구성.{k} 미허용값 {it.get(k)!r}")
                        v += 1
            try:
                if int(scal.get("구성문서수", -1)) != len(items):
                    print(f"  ✗ {rel}: 구성문서수({scal.get('구성문서수')}) != 원본구성 길이({len(items)})")
                    v += 1
            except ValueError:
                pass

            # OCR 정합
            if scal.get("추출품질") == "OCR" and not any(
                    it.get("추출", "").startswith("OCR") for it in items):
                print(f"  ✗ {rel}: 추출품질=OCR 인데 원본구성에 OCR 추출이 없다")
                v += 1

            # 본문 실측 대조
            body = text[text.index("---", 3) + 3:]
            tables = len(re.findall(r"(?m)^>\s*\[표\]", body))
            # `제 1 조` 처럼 숫자 앞뒤에 공백이 들어간 조판이 흔하다(원문·OCR 특성).
            # `제\d+조` 로만 세면 696건이 4건으로 잡힌다.
            arts = len(re.findall(r"(?m)^#{1,6}\s*제\s*\d+\s*조", body))
            # 표는 정확 일치가 아니라 하한 검증이다. slim_md.py 가 `> [표]` 마커를
            # 지우면 절삭 후 본문 실측은 항상 0 이 된다 — 기록값은 절삭 전 표 수이므로
            # 실측보다 작을 때만 위반으로 본다(과소기록은 여전히 잡는다).
            try:
                if int(scal.get("표", -1)) < tables:
                    print(f"  ✗ {rel}: 표 기록 {scal.get('표')} < 실측 {tables}")
                    v += 1
            except ValueError:
                pass
            if str(arts) != scal.get("조문수"):
                print(f"  ✗ {rel}: 조문수 기록 {scal.get('조문수')} != 실측 {arts}")
                v += 1

    print(f"\nmd {files}건 검사 · 위반 {v}건")
    sys.exit(1 if v else 0)


if __name__ == "__main__":
    main()
