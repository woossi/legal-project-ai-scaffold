#!/usr/bin/env python3
"""비조문형 전문의 줄머리 번호 형식을 독립 경로로 검증한다.

`build_guideline_hang_structure.py` 는 `x-y-z.` 를 전제한다. 그 전제는 문서마다
성립하지 않는다 — 실측하면 도시·군관리계획수립지침의 지배 단수는 4단이고,
도로안전시설 지침은 가나다목이 지배하며, 지구단위계획수립지침 안에도 2단·4단과
종결부호 앞 공백 변형이 섞여 있다. 그래서 형식은 상수가 아니라 문서별 선언
대상이고, 선언되지 않은 형식은 조용히 통과시키지 않는다.

이 검증기는 생성기를 import 하지 않는다. 같은 스크립트가 만든 두 파일이 맞는
것은 당연하므로 검증이 아니다. corpus 원자료를 다시 읽어 shape 을 독립으로
산출하고, 계약·산출물·원문 셋을 서로 대조한다.

두 모드
  --verify    계약에 프로파일이 있는 문서를 게이트 8종으로 검사한다
  --survey    프로파일이 없는 문서의 shape 인벤토리만 낸다. 역할은 비워 둔다.
              역할은 사람이 대표 줄을 원문에서 열어 정한다

입력  output/legal/statute/guideline_article_corpus.jsonl.gz
      .claude/skills/legal/legal-statute/contract/hang_numbering.json
      output/legal/statute/수립지침_항구조.json  (--verify 만)
출력  output/legal/statute/_수립지침_번호형식_검증.json
      output/legal/statute/_비조문형_번호형식_실측.json  (--survey)
"""

import argparse
import gzip
import hashlib
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
REPOSITORY_ROOT = BASE.parents[3]
CONTRACT = BASE / "contract" / "hang_numbering.json"
SCRIPT_PATH = ".claude/skills/legal/legal-statute/scripts/verify_hang_numbering.py"
DEFAULT_CORPUS = "output/legal/statute/guideline_article_corpus.jsonl.gz"
DEFAULT_OUT_DIR = "output/legal/statute"
NONARTICLE = ("비조문형_전문", "첨부_비조문형_전문")

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
GANADA = "가나다라마바사아자차카타파하"
ROMAN = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ"
# 전각 하이픈(U+FF0D)을 빼면 `１－１－１．` 이 shape 을 못 받고 `<본문>` 으로
# 조용히 떨어진다. 미탐지가 본문으로 위장하는 것이 이 검증기가 막을 실패다.
HYPHENS = "-‐‑–—―−－"
TERMINATORS = r".)\]．）］"
DIG, FDIG = "0-9", "０-９"
OPEN_PARENS = "(（"
CLOSE_PARENS = r")）"

RE_IMG_ANY = re.compile(r"^</?img")
RE_HEADING = re.compile(r"^제\s*([%s%s]+)\s*([편장절관조항호])" % (DIG, FDIG))
RE_PAREN = re.compile(
    r"^[%s]\s*(?:[%s%s]+|[%s]|[%s])\s*[%s]"
    % (OPEN_PARENS, DIG, FDIG, GANADA, ROMAN, CLOSE_PARENS))
RE_NUMBER = re.compile(
    r"^([%s%s]+(?:\s*[%s]\s*[%s%s]+)*)(\s*)([%s])"
    % (DIG, FDIG, HYPHENS, DIG, FDIG, TERMINATORS))
RE_GANADA = re.compile(r"^([%s])(\s*)([%s])" % (GANADA, TERMINATORS))
RE_ROMAN = re.compile(r"^([%s]+)(\s*)([%s])" % (ROMAN, TERMINATORS))
RE_SPLIT_HYPHEN = re.compile(r"[%s]" % HYPHENS)

# 본문으로 떨어졌지만 표지일 수 있는 줄머리. 판정하지 않고 사람에게 넘긴다.
RE_MARKER_SUSPECT = re.compile(
    r"^(?:[%s%s][^\s]{0,3}[%s]"          # 숫자로 시작하는데 위 규칙에 안 걸림
    r"|[가-힣][%s]\s"                     # 한글 한 글자 + 종결부호
    r"|[a-zA-Z][%s]\s"                    # 알파벳 한 글자 + 종결부호
    r"|[▪▫■□●○◆◇★☆※*·※–—][ \t])"        # 글머리 기호
    % (DIG, FDIG, TERMINATORS, TERMINATORS, TERMINATORS))


def report_path(path):
    """보고서에는 checkout 위치가 아닌 저장소 상대 POSIX 경로만 남긴다."""
    return Path(path).resolve().relative_to(REPOSITORY_ROOT).as_posix()


def shape_of(line):
    """줄머리 토큰을 문자 종류로 추상화한다.

    번호와 종결부호까지만 본다. 종결부호 뒤 본문을 shape 에 넣으면
    `2-2-6. 2-2-5.에도` 가 별개 shape 으로 갈려 인벤토리가 터진다.
    """
    if not line.strip():
        return "<빈줄>", ""
    s = line.lstrip()
    if RE_IMG_ANY.match(s):
        return "<이미지태그>", s[:24]
    m = RE_HEADING.match(s)
    if m:
        return "제N%s" % m.group(2), m.group(0)
    m = RE_PAREN.match(s)
    if m:
        return "(N)", m.group(0)
    if s[0] in CIRCLED:
        return "⓪", s[0]
    m = RE_NUMBER.match(s)
    if m:
        token = m.group(1)
        depth = len(RE_SPLIT_HYPHEN.split(re.sub(r"\s", "", token)))
        unit = "Ｎ" if re.search(r"[%s]" % FDIG, token) else "N"
        shape = "-".join([unit] * depth)
        if m.group(2):
            shape += "␠"
        return shape + m.group(3), m.group(0)
    m = RE_GANADA.match(s)
    if m:
        return "㉮" + ("␠" if m.group(2) else "") + m.group(3), m.group(0)
    m = RE_ROMAN.match(s)
    if m:
        return "Ⅰ" + ("␠" if m.group(2) else "") + m.group(3), m.group(0)
    return "<본문>", s[:24]


def inventory(lines):
    """shape 별 건수·대표줄·전체 줄번호를 낸다."""
    counts, first, rows = Counter(), {}, {}
    for idx, line in enumerate(lines, start=1):
        shape, token = shape_of(line)
        counts[shape] += 1
        rows.setdefault(shape, []).append(idx)
        if shape not in first:
            first[shape] = {"줄": idx, "토큰": token, "원문": line[:90]}
    return counts, first, rows


def marker_suspects(lines):
    """`<본문>` 으로 떨어졌지만 표지일 수 있는 줄을 모은다.

    탐지되지 않았다는 것은 존재하지 않는다는 뜻이 아니다. 여기 남은 줄은
    판정한 것이 아니라 사람이 볼 목록이다.
    """
    out = []
    for idx, line in enumerate(lines, start=1):
        shape, _ = shape_of(line)
        if shape != "<본문>":
            continue
        s = line.lstrip()
        if RE_MARKER_SUSPECT.match(s):
            out.append({"줄": idx, "원문": line[:90]})
    return out


def load_records(corpus_path):
    out = []
    with gzip.open(corpus_path, "rt", encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("parse_status") in NONARTICLE:
                out.append(rec)
    return out


def doc_text(rec):
    provisions = rec.get("provisions") or []
    if len(provisions) != 1:
        return None
    return provisions[0].get("text") or ""


def gate_result(number, name, passed, detail):
    return {"게이트": number, "이름": name,
            "판정": "통과" if passed else "실패", "상세": detail}


def verify(rec, text, profile, structure):
    """게이트 8종. 하나라도 실패하면 산출물을 쓸 수 없다는 뜻이다."""
    lines = text.split("\n")
    counts, first, rows = inventory(lines)
    gates = []

    total = sum(counts.values())
    gates.append(gate_result(
        1, "shape_인벤토리_전수", total == len(lines),
        {"shape수": len(counts), "합계": total, "원문줄수": len(lines)}))

    declared = profile.get("shape역할", {})
    undeclared = sorted(set(counts) - set(declared))
    gates.append(gate_result(
        2, "미선언_shape", not undeclared,
        {"미선언": [{"shape": s, "건수": counts[s], "대표": first[s]}
                 for s in undeclared]}))

    mismatch = [{"shape": s, "선언": declared[s].get("건수"), "실측": counts.get(s, 0)}
                for s in declared if declared[s].get("건수") != counts.get(s, 0)]
    gates.append(gate_result(
        3, "shape_건수_대조", not mismatch, {"불일치": mismatch}))

    hang_shapes = [s for s, v in declared.items() if v.get("역할") == "항"]
    expected = sorted(i for s in hang_shapes for i in rows.get(s, []))
    if structure is None:
        gates.append(gate_result(
            4, "항_줄집합_일치", False, {"사유": "산출물이 없어 대조하지 못했다"}))
        observed = []
    else:
        observed = sorted(h["원문줄범위"]["시작"] for h in structure["항목록"])
        only_contract = sorted(set(expected) - set(observed))
        only_output = sorted(set(observed) - set(expected))
        gates.append(gate_result(
            4, "항_줄집합_일치", not only_contract and not only_output,
            {"계약만": only_contract, "산출물만": only_output,
             "계약_항줄수": len(expected), "산출물_항줄수": len(observed)}))

    hang_tokens = [first[s]["토큰"] for s in hang_shapes if s in first]
    all_hang_lines = [lines[i - 1] for i in expected]
    has_full = any(re.search(r"[%s]" % FDIG, l) for l in all_hang_lines)
    has_half = any(re.search(r"[%s]" % DIG, l[:20]) for l in all_hang_lines)
    used_hyphens = sorted({ch for l in all_hang_lines for ch in l[:20]
                           if ch in HYPHENS})
    mixed = (has_full and has_half) or len(used_hyphens) > 1
    gates.append(gate_result(
        5, "문자도메인_혼용", not mixed,
        {"전각숫자_사용": has_full, "반각숫자_사용": has_half,
         "관측_하이픈": used_hyphens, "대표토큰": hang_tokens}))

    if structure is None:
        gates.append(gate_result(
            6, "원문_왕복", False, {"사유": "산출물이 없어 재조립하지 못했다"}))
    else:
        recon = {}
        for hang in structure["항목록"]:
            start = hang["원문줄범위"]["시작"]
            for offset, body in enumerate(hang["본문"].split("\n")):
                recon[start + offset] = body
        for chapter in structure["장목록"]:
            recon[chapter["원문줄"]] = chapter["장제목_원문표기"]
        for section in structure["절목록"]:
            recon[section["원문줄"]] = section["절제목_원문표기"]
        missing = sorted(set(range(1, len(lines) + 1)) - set(recon))
        for idx in missing:
            recon[idx] = lines[idx - 1]
        rebuilt = "\n".join(recon[i] for i in sorted(recon))
        same = (hashlib.sha256(rebuilt.encode("utf-8")).hexdigest()
                == hashlib.sha256(text.encode("utf-8")).hexdigest())
        gates.append(gate_result(
            6, "원문_왕복", same,
            {"재조립_줄수": len(recon), "원문_줄수": len(lines),
             "산출물이_채우지_않은_줄": missing,
             "sha256_일치": same}))

    if structure is None:
        gates.append(gate_result(
            7, "계층접두_일치", False, {"사유": "산출물이 없어 대조하지 못했다"}))
    else:
        bad = []
        for hang in structure["항목록"]:
            parts = [int(x) for x in hang["항번호"].split("-")]
            if parts[0] != hang["장번호"]:
                bad.append({"항번호": hang["항번호"], "사유": "장번호 불일치"})
            elif hang["절번호"] is not None and parts[1] != hang["절번호"]:
                bad.append({"항번호": hang["항번호"], "사유": "절번호 불일치"})
            elif hang["절번호"] is None and len(parts) != 2:
                bad.append({"항번호": hang["항번호"], "사유": "절 없는 장의 번호가 2단이 아님"})
        gates.append(gate_result(
            7, "계층접두_일치", not bad,
            {"검사": len(structure["항목록"]), "위반": bad}))

    declared_dominant = profile.get("지배단수")
    depth_counts = Counter()
    for shape in hang_shapes:
        depth_counts[shape.replace("␠", "").rstrip(".)]").count("-") + 1] += \
            counts.get(shape, 0)
    measured_dominant = (depth_counts.most_common(1)[0][0]
                         if depth_counts else None)
    gates.append(gate_result(
        8, "지배단수_고정금지", declared_dominant == measured_dominant,
        {"선언": declared_dominant, "실측": measured_dominant,
         "단수별_건수": dict(sorted(depth_counts.items())),
         "주의": "이 단수는 이 문서의 실측이다. 다른 문서 프로파일로 복제하지 않는다"}))

    suspects = marker_suspects(lines)
    declared_suspects = profile.get("본문분류_잠재마커_건수")
    gates.append(gate_result(
        9, "본문분류_잠재마커", declared_suspects == len(suspects),
        {"선언": declared_suspects, "실측": len(suspects), "목록": suspects,
         "뜻": "본문으로 분류했지만 표지일 수 있는 줄이다. 판정이 아니라 "
              "사람이 볼 목록이며, 건수가 움직이면 원문이 바뀐 것이다"}))

    return gates, counts, first


def survey(records, declared_keys):
    """프로파일이 없는 문서의 shape 인벤토리. 역할은 비운 채로 낸다."""
    out = []
    for rec in sorted(records, key=lambda r: r["document_key"]):
        text = doc_text(rec)
        if text is None:
            out.append({
                "document_key": rec["document_key"],
                "문서명": rec.get("official_name"),
                "격리": "provisions 가 1건이 아니라 전문 한 덩어리 전제가 깨졌다",
            })
            continue
        lines = text.split("\n")
        counts, first, _ = inventory(lines)
        hang_like = {s: n for s, n in counts.items()
                     if re.match(r"^[NＮ㉮Ⅰ⓪]", s) and not s.startswith("(")}
        out.append({
            "document_key": rec["document_key"],
            "문서명": rec.get("official_name"),
            "프로파일_선언됨": rec["document_key"] in declared_keys,
            "전문_줄수": len(lines),
            "전문_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "shape수": len(counts),
            "shape별": [
                {"shape": s, "건수": counts[s], "역할": None,
                 "대표줄": first[s]["줄"], "대표토큰": first[s]["토큰"],
                 "대표원문": first[s]["원문"]}
                for s in sorted(counts, key=lambda k: (-counts[k], k))
            ],
            "번호형_shape": dict(sorted(hang_like.items(),
                                    key=lambda kv: -kv[1])),
            "본문분류_잠재마커": marker_suspects(lines),
            "역할_미기입_사유": "대표 줄을 원문에서 열어 확인하기 전에는 역할을 "
                        "정하지 않는다. 최빈 shape 이 항이라는 보장이 없다. "
                        "`번호형_shape` 은 번호처럼 생긴 shape 의 목록일 뿐 "
                        "항 후보 판정이 아니다",
        })
    return out


def write_json(path, payload):
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--contract", default=str(CONTRACT))
    ap.add_argument("--structure",
                    default="output/legal/statute/수립지침_항구조.json")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--document-key", default="admrul:2100000241690")
    ap.add_argument("--survey", action="store_true",
                    help="프로파일 없는 문서의 shape 인벤토리만 낸다")
    a = ap.parse_args()

    corpus = Path(a.corpus).resolve()
    if not corpus.exists():
        print(f"입력 없음: {corpus}", file=sys.stderr)
        return 1
    contract_path = Path(a.contract).resolve()
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    records = load_records(corpus)
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if a.survey:
        profiles = contract.get("문서프로파일", {})
        payload = {
            "meta": {
                "스크립트": SCRIPT_PATH,
                "계약": report_path(contract_path),
                "입력": report_path(corpus),
                "대상_parse_status": list(NONARTICLE),
                "문서수": len(records),
                "읽는법": "역할이 null 인 shape 은 아직 판정하지 않은 것이다. "
                       "대표줄을 원문에서 열어 역할을 정하고 계약의 문서프로파일에 "
                       "선언한 뒤 --verify 로 넘어간다",
            },
            "문서별": survey(records, set(profiles)),
        }
        write_json(out_dir / "_비조문형_번호형식_실측.json", payload)
        for doc in payload["문서별"]:
            if "격리" in doc:
                print(f"  격리 {doc['문서명']}: {doc['격리']}")
                continue
            mark = "선언됨" if doc["프로파일_선언됨"] else "미선언"
            print("  %-6s %-42s shape %2d  번호형 %s"
                  % (mark, (doc["문서명"] or "")[:41], doc["shape수"],
                     doc["번호형_shape"]))
        print(f"→ {out_dir}/_비조문형_번호형식_실측.json")
        return 0

    rec = next((r for r in records
                if r["document_key"] == a.document_key), None)
    if rec is None:
        print(f"corpus 에 비조문형 {a.document_key} 가 없다", file=sys.stderr)
        return 1
    profile = contract.get("문서프로파일", {}).get(a.document_key)
    if profile is None:
        print(f"계약에 {a.document_key} 프로파일이 없다. --survey 를 먼저 돌린다",
              file=sys.stderr)
        return 2

    text = doc_text(rec)
    structure_path = Path(a.structure).resolve()
    structure = (json.loads(structure_path.read_text(encoding="utf-8"))
                 if structure_path.exists() else None)

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    gates, counts, first = verify(rec, text, profile, structure)
    failed = [g for g in gates if g["판정"] == "실패"]
    sha_ok = digest == profile.get("전문_sha256")

    payload = {
        "meta": {
            "스크립트": SCRIPT_PATH,
            "계약": report_path(contract_path),
            "document_key": a.document_key,
            "문서명": rec.get("official_name"),
            "검사대상_산출물": report_path(structure_path) if structure else None,
            "독립성": "생성기를 import 하지 않고 corpus 원자료에서 shape 을 다시 "
                   "산출했다. 같은 스크립트가 만든 두 파일의 일치는 검증이 아니다",
        },
        "전문_sha256": {"실측": digest, "계약": profile.get("전문_sha256"),
                     "일치": sha_ok},
        "판정": "통과" if not failed and sha_ok else "실패",
        "실패게이트": [g["이름"] for g in failed] + ([] if sha_ok else ["전문_sha256"]),
        "게이트": gates,
        "shape실측": [
            {"shape": s, "건수": counts[s],
             "역할": profile.get("shape역할", {}).get(s, {}).get("역할"),
             "대표": first[s]}
            for s in sorted(counts, key=lambda k: (-counts[k], k))
        ],
    }
    write_json(out_dir / "_수립지침_번호형식_검증.json", payload)

    for g in gates:
        print("  게이트%d %-18s %s" % (g["게이트"], g["이름"], g["판정"]))
    print("  전문_sha256 %s" % ("일치" if sha_ok else "불일치"))
    print(f"판정: {payload['판정']} → {out_dir}/_수립지침_번호형식_검증.json")
    return 0 if payload["판정"] == "통과" else 3


if __name__ == "__main__":
    sys.exit(main())
