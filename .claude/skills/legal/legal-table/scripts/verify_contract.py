#!/usr/bin/env python3
"""legal-table 산출물 계약 검증.

구조(JSON Schema)와 그 밖의 규약(교차 제약·값 도메인·멱등성)을 함께 본다.
자기 일관성은 검증이 아니므로 — 같은 스크립트가 만든 두 필드가 맞는 건 당연하다 —
줄번호와 표기는 md 원문을 다시 열어 대조한다.

입력  output/legal/table/*.json
      output/legal/markdown/{서울,인천,경기}/*.md
      .claude/skills/legal/legal-table/contract/*.json
출력  없음 (표준출력에 게이트별 통과·실패)
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
DATA = "output/legal/table/_table_loss.json"
SCHEMA = os.path.join(SKILL, "contract", "table_loss.schema.json")
VALUES = "output/legal/table/norm_values.json"
REPORT = "output/legal/table/_norm_value_report.json"
GAZETTE = "output/legal/table/gazette_refs.json"
INDEX = "output/legal/table/value_index.json"
VALUE_SCHEMA = os.path.join(SKILL, "contract", "norm_value.schema.json")
GAZETTE_SCHEMA = os.path.join(SKILL, "contract", "gazette_ref.schema.json")
CODEX = "output/legal/analysis/시행지침_조문_무결성_검증.json"
ESTIMATE = "output/legal/table/_retransform_estimate.json"
# 승인된 코퍼스의 정본. 소유는 legal-textreform 이고 여기서는 읽기만 한다 —
# 기대값을 검증기 안에서 다시 정하면 생성기와 함께 움직여 누락을 승인하게 된다.
CORPUS = os.path.join(
    SKILL, "..", "legal-textreform", "contract", "markdown-corpus.json")


def corpus_expected_ids():
    with open(CORPUS, encoding="utf-8") as fh:
        return {i for ids in json.load(fh)["기대ID"].values() for i in ids}
SUBJECT_GAP = "output/legal/table/_subject_gap.json"

# 승격 금지 필드. gazette_refs 에 하나라도 있으면 관측값이 확정값이 된 것이다.
FORBIDDEN_GAZETTE_FIELDS = ("asOf", "as_of", "적용판본", "시행일", "적용일",
                            "판본", "effective_date", "valid_from")


def _lines(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        return fh.read().split("\n")


def _schema_errors(schema_path, data):
    import jsonschema
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    return sorted(jsonschema.Draft202012Validator(schema).iter_errors(data),
                  key=lambda e: list(e.path))


def gate(results, name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _verify_values(root, results):
    """값 추출 파이프라인 게이트. spec §검증 게이트 1~12 를 그대로 건다."""
    vpath = os.path.join(root, VALUES)
    if not os.path.exists(vpath):
        gate(results, "값 추출 산출물 존재", True,
             "norm_values.json 없음 — 미검사(skipped). 실패가 아니다")
        return

    print("\n계약 검증 — 값 추출 파이프라인")
    with open(vpath, encoding="utf-8") as fh:
        vbody = fh.read()
    vdoc = json.loads(vbody)
    with open(os.path.join(root, REPORT), encoding="utf-8") as fh:
        qdoc = json.load(fh)
    with open(os.path.join(root, GAZETTE), encoding="utf-8") as fh:
        gdoc = json.load(fh)
    with open(os.path.join(root, INDEX), encoding="utf-8") as fh:
        idoc = json.load(fh)
    vals, quar, gaz = vdoc["records"], qdoc["records"], gdoc["records"]

    # 구조 계약
    try:
        e1 = _schema_errors(VALUE_SCHEMA, vdoc)
        e2 = _schema_errors(VALUE_SCHEMA, qdoc)
        e3 = _schema_errors(GAZETTE_SCHEMA, gdoc)
        errs = e1 + e2 + e3
        gate(results, "구조 계약(값·격리·고시)", not errs,
             "오류 0" if not errs
             else f"{len(errs)}건 — 첫 건 {list(errs[0].path)}: {errs[0].message[:110]}")
    except ImportError:
        gate(results, "구조 계약(값·격리·고시)", True,
             "jsonschema 미설치 — 미검사(skipped)")

    # 게이트 1 — 번호가 V000001 부터 빈 번호 없이 이어진다
    ids = sorted(r["value_id"] for r in vals + quar)
    expect = [f"V{i:06d}" for i in range(1, len(ids) + 1)]
    gate(results, "[1] value_id 가 V000001 부터 연속", ids == expect,
         f"확정 {len(vals)} + 격리 {len(quar)} = {len(ids)}")

    # 게이트 2 — surface 가 원문 line·offset 위치에 실재한다 (환각 전수 검사)
    cache, bad2, bad3, bad11 = {}, 0, 0, 0
    for r in vals + quar:
        rel = r["source_file"]
        if rel not in cache:
            cache[rel] = _lines(root, rel)
        lines = cache[rel]
        ln = r["line"]
        if ln > len(lines):
            bad2 += 1
            continue
        raw = lines[ln - 1]
        a, b = r["surface_offset"]
        if raw[a:b] != r["surface"]:
            bad2 += 1
        if r["surface"] not in r["quote"]:
            bad3 += 1
        # 게이트 11 — frontmatter 구간을 가리키면 실패
        fm_end = 0
        if lines and lines[0].strip() == "---":
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    fm_end = i + 1
                    break
        if ln <= fm_end:
            bad11 += 1
    gate(results, "[2] surface 가 원문 line·offset 에 실재", bad2 == 0,
         f"불일치 {bad2} / {len(vals) + len(quar)}")
    gate(results, "[3] surface 가 quote 안에 실재", bad3 == 0, f"불일치 {bad3}")

    # 게이트 4 — comparator null 은 확정에 없다
    bad = [r["value_id"] for r in vals if r["comparator"] is None]
    gate(results, "[4] comparator null 레코드가 norm_values 에 없다",
         not bad, f"위반 {len(bad)}")

    # 게이트 5 — 다중값 행에 주어를 붙이지 않는다
    bad = [r["value_id"] for r in vals + quar
           if r["row_value_count"] >= 2 and r["subject"] is not None]
    gate(results, "[5] row_value_count>=2 는 subject null", not bad,
         f"위반 {len(bad)}")

    # 게이트 6 — 규범 외는 확정 집계에서 빠진다
    norm_ids = {r["value_id"] for r in vals if r["context_class"] == "규범"}
    idx_ids = {v for d in idoc["by_district"].values() for v in d["value_ids"]}
    gate(results, "[6] value_index 는 규범만 집계", idx_ids == norm_ids,
         f"인덱스 {len(idx_ids)} · 규범 {len(norm_ids)}")

    # 게이트 7 — 승격 금지 필드가 없다
    bad = sorted({k for r in gaz for k in r if k in FORBIDDEN_GAZETTE_FIELDS})
    gate(results, "[7] gazette_refs 에 asOf 계열 필드 없음", not bad,
         f"발견 {bad}" if bad else "0")

    # 게이트 11 — 줄번호는 파일 기준
    gate(results, "[11] 모든 line 이 파일 기준 물리 줄번호", bad11 == 0,
         f"frontmatter 구간 가리킴 {bad11}")

    # 게이트 12 — article null 에 사유
    bad = [r["value_id"] for r in vals + quar
           if r["article"] is None and not r.get("article_reason")]
    badg = [r["gazette_id"] for r in gaz
            if r["article"] is None and not r.get("article_reason")]
    gate(results, "[12] article null 에 article_reason", not bad and not badg,
         f"값 {len(bad)} · 고시 {len(badg)}")

    _verify_example_zone(root, vals, quar, cache, results)
    _verify_citation(root, vals, quar, cache, results)

    # 모수 — 관측 전건 = 확정 + 격리
    m = vdoc["meta"]
    gate(results, "관측 전건 = 확정 + 격리",
         m["관측_전건"] == m["확정"] + m["격리"] == len(vals) + len(quar),
         f"{m['관측_전건']} = {m['확정']} + {m['격리']}")

    _verify_relation(root, vals, quar, qdoc, cache, results)

    # 게이트 10 — 멱등성. 확정과 격리를 **둘 다** 해시한다. 격리만 바뀌는
    # 변경(관계 필드 발급이 그렇다)이 확정 해시만 봐서는 잡히지 않는다.
    with open(os.path.join(root, REPORT), encoding="utf-8") as fh:
        qbody = fh.read()
    with tempfile.TemporaryDirectory() as td:
        outs = []
        for i in range(2):
            v = os.path.join(td, f"v{i}.json")
            q = os.path.join(td, f"q{i}.json")
            subprocess.run(
                [sys.executable, os.path.join(HERE, "extract_values.py"),
                 "--root", root, "--out-values", v, "--out-report", q],
                check=True, capture_output=True)
            with open(v, "rb") as fh:
                hv = hashlib.sha256(fh.read()).hexdigest()
            with open(q, "rb") as fh:
                hq = hashlib.sha256(fh.read()).hexdigest()
            outs.append((hv, hq))
        same = (outs[0] == outs[1]
                and outs[0][0] == hashlib.sha256(vbody.encode("utf-8")).hexdigest()
                and outs[0][1] == hashlib.sha256(qbody.encode("utf-8")).hexdigest())
        gate(results, "[10] 값 추출 2회 실행 바이트 동일 (확정·격리 둘 다)", same,
             f"확정 {outs[0][0][:16]}… · 격리 {outs[0][1][:16]}…")


def _verify_example_zone(root, vals, quar, cache, results):
    """예시 도해 게이트 (계약 22~23).

    이 결함의 고유 위험은 **예시도 값이 규범으로 실리는 것**이다. 값이 원문에
    실재하는지만 보는 검사는 이걸 못 잡는다 — 숫자는 진짜고 자격이 가짜다.

    `example_zone` 필드를 믿고 세지 않는다. 그건 추출기가 쓴 값이라 그것과
    맞는 것은 자기 일관성이다. md 원문을 다시 열어 캡션 구간을 **독립적으로**
    다시 계산하고, 그 구간에 든 확정값이 규범으로 남아 있으면 실패한다.
    """
    import re as _re

    CAP = _re.compile(
        r"[<\[［（(]\s*(별표|표|그림|사진|도면)\s*"
        r"([0-9IVXⅠ-Ⅹ]+(?:\s*[-–~.]\s*[0-9IVXⅠ-Ⅹ]+)*)?\s*[>\]］）)]")
    EXW = _re.compile(r"예\s*시|예\s*\)|사\s*례|표기\s*한다|표시\s*한다|표기\s*방법|범례")
    # 캡션 참조 없이 줄 전체가 예시 선언인 형태도 구간을 연다. 이걸 빼면
    # 마커가 값 줄에 같이 있는 건만 잡히고 바로 다음 줄의 같은 도해 값은
    # 규범으로 남는다 — 상계 장암 59줄(잡힘) 대 61줄(놓침).
    # **table_common 에서 import 하지 않는다** — 같은 정규식을 쓰면 독립
    # 재계산이 아니라 자기 일관성이 된다.
    DECL = _re.compile(
        r"^\s*[-–•∙※\s]*(?:예\s*\)|\(\s*예\s*\)|<\s*예\s*시?\s*>|예\s*시\s*[):：]?"
        r"|사\s*례\s*[):：]?|표기\s*방법|범\s*례)\s*.{0,14}$")
    CLS = _re.compile(r"^\s*[-–]?\s*[①-⑳]")
    LEAD = " \t-*#>·∙▪□○●◦※0123456789.()（）①②③④⑤⑥⑦⑧⑨⑩"

    def zones(lines):
        """추출기와 독립으로 다시 계산한 예시 구간 줄집합."""
        fm_end = 0
        if lines and lines[0].strip() == "---":
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    fm_end = i + 1
                    break
        caps, heads = [], set()
        for i in range(fm_end, len(lines)):
            raw = lines[i]
            if raw.strip().startswith("<!--"):
                continue
            if _re.match(r"^#{1,6}\s", raw.rstrip()):
                heads.add(i)
            m = CAP.search(raw)
            if m and raw[:m.start()].strip(LEAD) in ("", "**"):
                caps.append((i, CAP.sub(" ", raw).replace("*", " ").strip()))
            elif raw.strip() and DECL.match(raw):
                caps.append((i, raw.strip()))
        capset = {c[0] for c in caps}
        out = set()
        for ci, body in caps:
            if not EXW.search(body):
                continue
            blanks = 0
            end = len(lines)
            for j in range(ci + 1, len(lines)):
                if j in heads or j in capset or CLS.match(lines[j]):
                    end = j
                    break
                if not lines[j].strip():
                    blanks += 1
                    if blanks >= 2:
                        end = j
                        break
                else:
                    blanks = 0
            out |= set(range(ci + 2, end + 1))   # 1-based, 캡션 줄 자체는 뺀다
        return out

    zcache = {}
    violation, missing_basis, phantom = [], [], []
    for r in vals + quar:
        rel = r["source_file"]
        if rel not in cache:
            cache[rel] = _lines(root, rel)
        if rel not in zcache:
            zcache[rel] = zones(cache[rel])
        inside = r["line"] in zcache[rel]
        # 게이트 22 — 구간 안의 확정값이 규범으로 남아 있으면 실패
        if inside and r["context_class"] == "규범" and "quarantine_class" not in r:
            violation.append(r["value_id"])
        # 게이트 23 — example_zone 필드가 독립 재계산과 어긋나면 실패.
        #   구간 안인데 필드가 비었거나(근거 결손), 구간 밖인데 필드가 있으면
        #   (없는 근거 발명) 둘 다 위반이다
        if inside and not r.get("example_zone"):
            missing_basis.append(r["value_id"])
        if not inside and r.get("example_zone"):
            phantom.append(r["value_id"])

    gate(results, "[22] 예시 캡션 구간의 확정값이 규범으로 실려 있지 않다",
         not violation,
         f"위반 {len(violation)}"
         + (f" — {violation[:8]}" if violation else "")
         + f" (구간 안 값 {sum(1 for r in vals + quar if r.get('example_zone'))})")
    gate(results, "[23] example_zone 이 원문 독립 재계산과 일치",
         not missing_basis and not phantom,
         f"근거결손 {len(missing_basis)} · 없는근거 {len(phantom)}")


def _verify_citation(root, vals, quar, cache, results):
    """인용서술 게이트 (계약 24~25).

    이 판정의 고유 위험은 **인용 어휘로 규범을 삼키는 것**이다. 실측에서 인용
    표기가 있는 규범값 63건 중 62건이 이 지구의 규범이었다 — 조례·상위법을
    근거로 든 규범 진술이며, 인용은 근거 제시이지 자격 박탈이 아니다.
    어휘로 걸면 62건이 함께 뒤집힌다.

    그래서 두 방향을 다 본다.
      24) 발급된 인용서술 전건이 원문에서 **주어=인용법령·서술어=규정하고 있음**
          구조인지. 판정표에 있다는 것만으로 통과시키지 않는다 — md 원문을 다시
          열어 그 줄에서 서술 종결을 확인한다
      25) 인용 표기가 있는데 규범으로 남은 값들이 **적용형 서술**인지. 여기서
          서술형이 새로 나타나면 판정표에 빠진 건이 있다는 뜻이다
    """
    import re as _re

    # 인용 표기와 두 갈래 서술어. 판정표 자체를 다시 만드는 것이 아니라
    # **판정표가 잡아야 할 모수를 원문에서 독립으로 세는** 용도다.
    CITE = _re.compile(r"「[^」]{2,40}」|｢[^｣]{2,40}｣|법\s*제\d+조"
                       r"|시행령\s*제\d+조|조례")
    DESCR = _re.compile(r"규정하고\s*있|정하고\s*있|규정되어\s*있|명시하고\s*있"
                        r"|로\s*규정|고\s*있음")

    bad_issue, missed = [], []
    for r in vals + quar:
        rel = r["source_file"]
        if rel not in cache:
            cache[rel] = _lines(root, rel)
        lines = cache[rel]
        raw = lines[r["line"] - 1] if r["line"] - 1 < len(lines) else ""
        if r["context_class"] == "인용서술":
            # 게이트 24 — 발급 근거가 원문에 실재하는가.
            # line_text 를 보지 않는다. 같은 스크립트가 쓴 값이다.
            if not (CITE.search(raw) and DESCR.search(raw)):
                bad_issue.append(r["value_id"])
        elif r["context_class"] == "규범" and CITE.search(raw):
            # 게이트 25 — 인용 표기가 있는 규범에 서술형이 남아 있는가.
            if DESCR.search(raw):
                missed.append(r["value_id"])

    issued = [r for r in vals + quar if r["context_class"] == "인용서술"]
    cited_norm = sum(1 for r in vals + quar
                     if r["context_class"] == "규범"
                     and CITE.search(cache[r["source_file"]][r["line"] - 1]))
    gate(results,
         "[24] 인용서술 발급 전건이 원문에서 인용+서술형 구조",
         not bad_issue,
         f"발급 {len(issued)} · 근거미실재 {len(bad_issue)}"
         + (f" — {bad_issue[:8]}" if bad_issue else ""))
    gate(results,
         "[25] 인용 표기 규범에 서술형 잔여 없음 (어휘로 규범을 삼키지 않았다)",
         not missed,
         f"인용 표기 규범 {cited_norm} 건은 전부 적용형 · 서술형 잔여 "
         f"{len(missed)}" + (f" — {missed[:8]}" if missed else ""))


def _verify_relation(root, vals, quar, qdoc, cache, results):
    """관계 필드 게이트 (계약 17~20).

    이 필드의 고유 위험은 **없는 관계를 발급하는 것**이다. 값 추출은 원문에 있는
    것을 옮기는 일이었지만 관계는 새 주장이므로, 근거 발췌가 원문에 실재하는지를
    전수로 다시 대조한다. 자기 일관성(같은 스크립트가 쓴 두 필드가 맞는 것)은
    검증이 아니다 — md 원문을 다시 연다.
    """
    issued = [r for r in quar if r.get("relation_role")]

    # 게이트 17 — 근거 발췌가 md 원문에 실재한다 (환각 전수 검사).
    # line_text 와 대조하지 않는다. line_text 도 같은 스크립트가 쓴 값이므로
    # 그것과 맞는 것은 자기 일관성이다. 파일을 다시 열어 그 줄에서 찾는다.
    bad = []
    for r in issued:
        rel = r["source_file"]
        if rel not in cache:
            cache[rel] = _lines(root, rel)
        lines = cache[rel]
        raw = lines[r["line"] - 1] if r["line"] <= len(lines) else ""
        if not r["relation_quote"] or r["relation_quote"] not in raw:
            bad.append(r["value_id"])
        # 발췌는 그 값의 표기를 포함하거나, 값의 역할을 정하는 지표 어휘를
        # 담아야 한다. 값과 무관한 구간을 근거로 실으면 대조가 무의미해진다
        elif (r["surface"] not in r["relation_quote"]
              and r["relation_role"] not in r["relation_quote"]):
            bad.append(r["value_id"])
    gate(results, "[17] 관계 근거발췌가 md 원문에 실재", not bad,
         f"발급 {len(issued)} 중 불일치 {len(bad)}"
         + (f" — {bad[:5]}" if bad else ""))

    # 게이트 18 — 짝 value_id 가 실재한다. 확정·격리 어느 쪽이든 있어야 하고,
    # 같은 문서·같은 줄이어야 한다. 줄을 건너 잇는 짝은 아직 근거 요건 밖이다.
    where = {r["value_id"]: r for r in vals + quar}
    bad = []
    for r in issued:
        p = r["relation_pair"]
        if p is None:
            continue
        q = where.get(p)
        if q is None or q["source_file"] != r["source_file"] or q["line"] != r["line"]:
            bad.append(f"{r['value_id']}→{p}")
    paired = sum(1 for r in issued if r["relation_pair"])
    gate(results, "[18] 짝 value_id 가 같은 문서·같은 줄에 실재", not bad,
         f"짝 특정 {paired} 중 불일치 {len(bad)}" + (f" — {bad}" if bad else ""))

    # 게이트 19 — 미발급 사유 결손 0. 발급되지 않은 격리 전건에 사유가 있어야
    # 한다. 사유가 비면 하위 축은 검토하고 뺀 것인지 아직 안 본 것인지 모른다.
    bad = [r["value_id"] for r in quar
           if not r.get("relation_role") and not r.get("relation_reason")]
    both = [r["value_id"] for r in quar
            if r.get("relation_role") and r.get("relation_reason")]
    gate(results, "[19] 미발급 사유 결손 0 · 발급/미발급 동시성립 0",
         not bad and not both,
         f"사유결손 {len(bad)} · 동시성립 {len(both)}")

    # 게이트 20 — 어휘가 없는데 발급된 것 0. 발급된 값의 왼쪽 40자에 역할
    # 수식어와 지표가 원문에 함께 실재해야 한다. 원문을 다시 열어 확인한다.
    import re as _re
    metric_rx = _re.compile(r"건폐율|용적률|용적율")
    bad = []
    for r in issued:
        rel = r["source_file"]
        raw = cache[rel][r["line"] - 1]
        before = raw[:r["surface_offset"][0]]
        win = before[-40:]
        if r["relation_role"] not in win or not metric_rx.search(win):
            bad.append(r["value_id"])
    # 확정 레코드에는 관계 필드가 없어야 한다 — 검토 범위 밖이므로 발급하지 않았다
    leaked = [r["value_id"] for r in vals
              if "relation_role" in r or "relation_reason" in r]
    gate(results, "[20] 어휘 없이 발급된 것 0 · 확정에 관계 필드 유출 0",
         not bad and not leaked,
         f"어휘없음 {len(bad)}" + (f" — {bad}" if bad else "")
         + f" · 확정유출 {len(leaked)}")

    # 게이트 21 — 근거 강도가 기계로 구분된다. 발급했다는 사실만으로는 하위 축이
    # 근거가 얼마나 강한지 알 수 없다 — relation_note 는 자유 텍스트라 못 거른다.
    # 순서 대응은 구문상 직접 수식이 아니므로 근거 서술이 반드시 있어야 한다.
    import collections as _c
    BASIS = ("동일줄_직접수식", "동일줄_순서대응")
    WEAK = ("동일줄_순서대응",)
    bad_dom = [r["value_id"] for r in issued
               if r.get("relation_basis") not in BASIS]
    bad_note = [r["value_id"] for r in issued
                if r.get("relation_basis") in WEAK
                and not (r.get("relation_note") or "").strip()]
    s = qdoc["meta"].get("관계필드", {})
    # 키를 문자열로 고정한다 — null 이 섞이면 정렬이 깨져 게이트가 예외로 죽는다.
    # 검증기는 위반을 만나면 실패를 보고해야지 죽으면 안 된다.
    dist = _c.Counter(
        "(null)" if r.get("relation_basis") is None else str(r["relation_basis"])
        for r in issued)
    dist_ok = s.get("relation_basis_분포") == dict(
        sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])))
    weak_ok = s.get("약한근거") == sorted(
        r["value_id"] for r in issued if r["relation_basis"] in WEAK)
    gate(results, "[21] 근거 강도가 값 도메인 안 · 약한 근거에 근거서술",
         not bad_dom and not bad_note and dist_ok and weak_ok,
         f"도메인밖 {len(bad_dom)} · 서술결손 {len(bad_note)} · "
         f"분포 {dict(dist)} 집계일치 {dist_ok and weak_ok}")

    # 모수 — 관계필드 집계가 실제 레코드와 맞는지. 어휘 후보를 분모로 쓰면
    # 발급률이 부풀려지므로 분모가 격리 전건임을 여기서 못 박는다.
    ok = (s.get("발급") == len(issued)
          and s.get("미발급") == len(quar) - len(issued)
          and s.get("격리_전건") == len(quar)
          and s.get("짝특정") == paired)
    gate(results, "관계필드 모수 = 격리 전건", ok,
         f"발급 {len(issued)} + 미발급 {len(quar) - len(issued)} = 격리 {len(quar)}"
         f" (어휘후보 {s.get('어휘후보')} 는 분모가 아니다)")


def _verify_subject_gap(root, results):
    """주어 미상 견적 게이트 (계약 13~16).

    이 산출물의 고유 위험은 **모수 부풀리기**다. 다중값행은 spec §주어 판정이
    주어를 붙이지 않기로 정한 계약이므로 회수 후보가 아니다. 그것을 후보로 세면
    견적이 4배로 부푼다 — 게이트 14 가 그것만 본다.
    """
    p = os.path.join(root, SUBJECT_GAP)
    if not os.path.exists(p):
        gate(results, "주어 미상 견적 산출물 존재", True,
             "_subject_gap.json 없음 — 미검사(skipped)")
        return
    vp = os.path.join(root, VALUES)
    if not os.path.exists(vp):
        gate(results, "주어 미상 견적 선행조건", True,
             "norm_values.json 없음 — 미검사(skipped)")
        return

    print("\n계약 검증 — 주어 미상 원인 분해와 회수 견적")
    with open(p, encoding="utf-8") as fh:
        gbody = fh.read()
    g = json.loads(gbody)
    recs = g["records"]
    m = g["meta"]
    with open(vp, encoding="utf-8") as fh:
        vals = json.load(fh)["records"]

    # 게이트 13 — 모수 검산. 원자료(norm_values)로 다시 세서 대조한다.
    # 자기 일관성은 검증이 아니므로 meta 끼리 맞추지 않는다.
    none_ids = {v["value_id"] for v in vals if v["subject_type"] is None}
    gap_ids = {r["value_id"] for r in recs}
    multi = [r for r in recs if r["row_value_count"] >= 2]
    single = [r for r in recs if r["row_value_count"] == 1]
    ok = (gap_ids == none_ids
          and len(multi) + len(single) == len(recs)
          and sum(m["원인분포"].values()) == len(recs))
    gate(results, "[13] 주어 미상 모수 검산", ok,
         f"미상 {len(recs)} = 다중값 {len(multi)} + 단일값 {len(single)} · "
         f"원인분포 합 {sum(m['원인분포'].values())} · "
         f"norm_values 대조 {'일치' if gap_ids == none_ids else '불일치'}")

    # 게이트 14 — 회수 후보의 모수. 다중값행·규범아님이 섞이면 부풀려진 견적이다
    recov = [r for r in recs if r["회수가능"]]
    bad = [r["value_id"] for r in recov
           if r["row_value_count"] != 1 or r["context_class"] != "규범"]
    gate(results, "[14] 회수 후보는 단일값·규범만", not bad,
         f"회수 {len(recov)} / 모수 {len(single)} · 위반 {len(bad)}")

    # 회수된 레코드에는 근거가 반드시 있다. 없으면 근거 없는 견적이다
    bad = [r["value_id"] for r in recov
           if not r["시험_subject"] or not r["시험_근거발췌"]
           or not r["시험_확장규칙"]]
    gate(results, "[14b] 회수 레코드에 주어·근거발췌·확장규칙", not bad,
         f"결손 {len(bad)}")

    # 게이트 15 — 견적이 norm_values 로 승격되지 않았다
    promoted = [r["value_id"] for r in recov
                if next((v for v in vals if v["value_id"] == r["value_id"]),
                        {}).get("subject") is not None]
    gate(results, "[15] 견적을 norm_values 로 승격하지 않았다", not promoted,
         f"승격 {len(promoted)} — 0 이어야 한다")

    # 게이트 16 — 상한 관측과 실제 회수를 섞지 않았다
    z = m["용도지역_상한관측"]
    ok = z["표기실재"] >= z["실제회수"] and z["모수"] == len(single)
    gate(results, "[16] 용도지역 상한 >= 실제 회수", ok,
         f"상한 {z['표기실재']} · 실제 {z['실제회수']} · 모수 {z['모수']}")

    # 회수 근거발췌가 원문에 실재하는지 — 환각 전수 검사
    bad = []
    for r in recov:
        ls = _lines(root, r["source_file"])
        frag = r["시험_근거발췌"]
        if not any(frag[:40] in ln for ln in ls):
            bad.append(r["value_id"])
    gate(results, "[16b] 회수 근거발췌가 원문에 실재", not bad,
         f"미실재 {len(bad)} / {len(recov)}")

    # 멱등성
    with tempfile.TemporaryDirectory() as td:
        cur = os.path.join(root, SUBJECT_GAP)
        with open(cur, "rb") as fh:
            before = fh.read()
        subprocess.run(
            [sys.executable, os.path.join(HERE, "analyze_subject_gap.py"),
             "--root", root], check=True, capture_output=True)
        with open(cur, "rb") as fh:
            after = fh.read()
        gate(results, "[10] 견적 2회 실행 바이트 동일", before == after,
             f"sha256 {hashlib.sha256(after).hexdigest()[:16]}…")


def _verify_estimate(root, results):
    """통합 견적 게이트. 견적서가 판단서로 변질되지 않았는지도 본다."""
    p = os.path.join(root, ESTIMATE)
    if not os.path.exists(p):
        gate(results, "통합 견적 산출물 존재", True,
             "_retransform_estimate.json 없음 — 미검사(skipped)")
        return

    print("\n계약 검증 — 통합 재변환 견적")
    with open(p, encoding="utf-8") as fh:
        ebody = fh.read()
    e = json.loads(ebody)
    recs = e["records"]

    n_corpus = len(corpus_expected_ids())
    gate(results, "[E1] 코퍼스 전건", len(recs) == n_corpus,
         f"{len(recs)}건 / 계약 기대 {n_corpus}")

    c1 = e["교차_값미보유96_조문후보80"]
    ok = (c1["둘다"] + c1["표만"] + c1["조문만"] + c1["둘다아님"] == n_corpus
          and c1["합"] == n_corpus)
    gate(results, f"[E2] 교차표 네 칸의 합 = {n_corpus}", ok,
         f"둘다 {c1['둘다']} · 표만 {c1['표만']} · 조문만 {c1['조문만']} "
         f"· 아님 {c1['둘다아님']}")

    # 교차표가 원자료와 맞는지 — _table_loss·codex 를 다시 열어 재현한다
    with open(os.path.join(root, DATA), encoding="utf-8") as fh:
        T = {r["dstrcAppnNo"]: r for r in json.load(fh)["records"]}
    with open(os.path.join(root, CODEX), encoding="utf-8") as fh:
        C = {d["lc5"]: d for d in json.load(fh)["documents"]}
    novalue = {k for k, v in T.items() if v["값줄수"] == 0}
    artcand = {k for k, v in C.items() if v["retransform_candidate"]}
    ok = (len(novalue & artcand) == c1["둘다"]
          and len(novalue - artcand) == c1["표만"]
          and len(artcand - novalue) == c1["조문만"])
    gate(results, "[E3] 교차표 = 원자료 재현", ok,
         f"값미보유 {len(novalue)} · 조문후보 {len(artcand)}")

    # 45 를 대입하지 않았는지 — 숫자 발명 금지.
    # 금지 대상은 `45 → 87` 처럼 **구체적 수를 채운** 증가분이다. 금지를 설명하는
    # 문장('45 → N 형태의 증가분을 내면 숫자를 발명하는 것이다')은 위반이 아니다.
    import re as _re
    u = e["meta"]["미확정_45"]
    txt = json.dumps(e, ensure_ascii=False)
    filled = _re.findall(r"45\s*(?:→|->|에서)\s*\d+", txt)
    ok = u["상태"] == "모수_미확정" and not filled
    gate(results, "[E4] 45 기준 증가분을 단정하지 않았다", ok,
         f"{u['상태']}" + (f" · 위반 {filled}" if filled else ""))

    # 견적서가 판단서로 변질되지 않았는지
    banned = ["재변환을 권고", "재변환해야", "권고한다", "하는 것이 좋다",
              "추천한다", "결론적으로 재변환"]
    found = [b for b in banned if b in txt]
    gate(results, "[E5] 실행 여부를 권고하지 않는다", not found,
         f"발견 {found}" if found else "0")

    # 대가 4항이 codex 정본과 같은지
    cost = e["대가"]["항목"]
    gate(results, "[E6] 대가 4항 인용", len(cost) == 4,
         f"{len(cost)}항 · 출처 {e['대가']['출처'][:44]}")

    # 층화 합이 189·소실표 합계와 맞는지
    s = e["층화"]
    docs = sum(v["문서수"] for v in s.values())
    lost = sum(v["소실표합계"] for v in s.values())
    total_lost = sum(r["소실표건수"] for r in T.values())
    gate(results, f"[E7] 층화 합 = {n_corpus}문서 · 소실표 전량",
         docs == n_corpus and lost == total_lost,
         f"문서 {docs} · 소실표 {lost:,}/{total_lost:,}")

    # 멱등성
    with tempfile.TemporaryDirectory() as td:
        outs = []
        for i in range(2):
            oj = os.path.join(td, f"e{i}.json")
            om = os.path.join(td, f"e{i}.md")
            subprocess.run(
                [sys.executable,
                 os.path.join(HERE, "build_retransform_estimate.py"),
                 "--root", root, "--out-json", oj, "--out-md", om],
                check=True, capture_output=True)
            with open(oj, "rb") as fh:
                outs.append(hashlib.sha256(fh.read()).hexdigest())
        same = outs[0] == outs[1] == hashlib.sha256(
            ebody.encode("utf-8")).hexdigest()
        gate(results, "[E8] 견적 2회 실행 바이트 동일", same,
             f"sha256 {outs[0][:16]}…")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    root = args.root

    with open(os.path.join(root, DATA), encoding="utf-8") as fh:
        body = fh.read()
    data = json.loads(body)
    recs = data["records"]
    meta = data["meta"]
    results = []

    print("계약 검증 — _table_loss.json")

    # 1. 구조 계약
    try:
        import jsonschema
        with open(SCHEMA, encoding="utf-8") as fh:
            schema = json.load(fh)
        errs = sorted(jsonschema.Draft202012Validator(schema).iter_errors(data),
                      key=lambda e: list(e.path))
        gate(results, "구조 계약(JSON Schema)", not errs,
             "오류 0" if not errs
             else f"{len(errs)}건 — 첫 건 {list(errs[0].path)}: {errs[0].message[:120]}")
    except ImportError:
        gate(results, "구조 계약(JSON Schema)", True,
             "jsonschema 미설치 — 미검사(skipped). 실패가 아니다")

    # 2. 코퍼스 전건
    expected = corpus_expected_ids()
    n_corpus = len(expected)
    gate(results, "코퍼스 전건", len(recs) == n_corpus,
         f"{len(recs)}건 / 계약 기대 {n_corpus}")

    # 3. 지구번호 유일
    ids = [r["dstrcAppnNo"] for r in recs]
    gate(results, "dstrcAppnNo 유일·결손 없음",
         len(set(ids)) == n_corpus and all(ids), f"고유 {len(set(ids))}")

    # 3b. 수가 같아도 신원이 같아야 한다 — 한 지구가 다른 지구로 바뀐 사고는
    # 개수 검사로는 잡히지 않는다. 기대 집합은 승인된 코퍼스 계약에서 읽는다.
    missing = sorted(expected - set(ids))
    extra = sorted(set(ids) - expected)
    gate(results, "코퍼스 계약과 ID 집합 일치", not missing and not extra,
         f"결손 {len(missing)} · 미승인 {len(extra)}"
         + (f" — 결손 {missing[:3]}" if missing else "")
         + (f" · 미승인 {extra[:3]}" if extra else ""))

    # 4. loss_class 분포 합
    s = sum(meta["loss_class_분포"].values())
    g = sum(meta["recovery_grade_분포"].values())
    gate(results, f"분포 합 = {n_corpus}", s == n_corpus and g == n_corpus,
         f"loss_class {s} · recovery_grade {g}")

    # 5. article null → article_reason (설계 게이트 12)
    bad = [(r["dstrcAppnNo"], t["line"]) for r in recs for t in r["표참조"]
           if t["article"] is None and not t.get("article_reason")]
    gate(results, "article null 에 article_reason 존재", not bad, f"위반 {len(bad)}")

    # 6. 줄번호가 파일 기준인지 — md 원문 대조 (설계 게이트 11)
    #    frontmatter 구간을 가리키거나 파일 길이를 넘으면 실패다.
    off_fm = over = missing = 0
    for r in recs:
        p = os.path.join(root, r["source_file"])
        with open(p, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        fm_end = 0
        if lines and lines[0].strip() == "---":
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    fm_end = i + 1
                    break
        for t in r["표참조"]:
            ln = t["line"]
            if ln > len(lines):
                over += 1
            elif ln <= fm_end:
                off_fm += 1
            elif t["surface"] not in lines[ln - 1]:
                missing += 1
    gate(results, "모든 line 이 파일 기준 물리 줄번호",
         off_fm == 0 and over == 0,
         f"frontmatter 구간 가리킴 {off_fm} · 파일길이 초과 {over}")

    # 7. surface 가 그 줄에 실재하는지 (환각 전수 검사)
    gate(results, "surface 가 해당 줄에 실재", missing == 0, f"불일치 {missing}")

    # 8. 인용문이 원문 줄과 일치
    mismatch = 0
    for r in recs:
        p = os.path.join(root, r["source_file"])
        with open(p, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        for t in r["표참조"]:
            if t["line"] <= len(lines) and t["인용문"] != lines[t["line"] - 1].strip():
                mismatch += 1
    gate(results, "인용문 = 원문 줄", mismatch == 0, f"불일치 {mismatch}")

    # 9. 값이 살아 있는데 `표자체없음` 으로 판정하지 않았는지.
    #    R1 검수에서 나온 결함이다 — 값 보유 93문서 중 84문서가 `표자체없음` 으로
    #    분류됐다. 값이 있는데 "표 자체가 없다"고 보고하는 것은 견적서로서 오도다.
    #    모수가 다른 건수를 max 비교한 것이 원인이었다.
    bad = [r["dstrcAppnNo"] for r in recs
           if r["값줄수"] > 0 and r["loss_class"] == "표자체없음"]
    gate(results, "값줄수>0 인데 loss_class=표자체없음 이 없다",
         not bad, f"위반 {len(bad)}")

    # 10. `소실없음` 은 값이 실제로 회수된 문서에만 붙는지.
    #     R1 검수 결함 2 — `소실없음` 이 "회수 성공" 과 "잴 표가 없음" 을 같은
    #     칸에 넣고 있었다. 후자는 `표없음` 으로 갈랐다.
    bad = [r["dstrcAppnNo"] for r in recs
           if r["loss_class"] == "소실없음" and r["단일값줄수"] == 0]
    gate(results, "소실없음 은 단일값 줄이 있는 문서만", not bad, f"위반 {len(bad)}")

    bad = [r["dstrcAppnNo"] for r in recs
           if r["loss_class"] == "표없음"
           and (r["값줄수"] or r["소실표건수"] or r["표참조수"])]
    gate(results, "표없음 은 값·소실표·표참조가 모두 0", not bad, f"위반 {len(bad)}")

    # 11. loss_mix 키가 단위를 달고 있는지. 모수가 다른 값을 나란히 비교하지
    #     못하게 하는 장치다.
    allowed = set(meta["loss_mix_keys"])
    bad = [r["dstrcAppnNo"] for r in recs if set(r["loss_mix"]) - allowed]
    gate(results, "loss_mix 키가 단위 명시 도메인 안", not bad, f"위반 {len(bad)}")

    # 12. loss_class 가 loss_mix 에 실재 (표없음 은 mix 가 빌 수 있다)
    bad = [r["dstrcAppnNo"] for r in recs
           if r["loss_class"] != "표없음"
           and not any(k.startswith(r["loss_class"] + "_") for k in r["loss_mix"])]
    gate(results, "loss_class 가 loss_mix 에 실재", not bad, f"위반 {len(bad)}")

    # 13. reconvert_candidate 는 회수할 표 유무에서 나온다 — loss_class 파생이 아니다
    bad = [r["dstrcAppnNo"] for r in recs
           if r["reconvert_candidate"] != (
               not r["loss_mix"].get("OCR훼손_훼손줄수")
               and bool(r["소실표건수"] or r["loss_mix"].get("값깨짐_값줄수")))]
    gate(results, "reconvert_candidate = 회수할 표 유무", not bad, f"위반 {len(bad)}")

    # 14. 소실표건수 = loss_mix 의 같은 값 (최상위로 올린 재계산 필드)
    bad = [r["dstrcAppnNo"] for r in recs
           if r["소실표건수"] != r["loss_mix"].get("표자체없음_표건수", 0)]
    gate(results, "소실표건수 = loss_mix.표자체없음_표건수", not bad, f"위반 {len(bad)}")

    # 15. 확정값수 — norm_values.json 이 있으면 그 값과 맞아야 하고, 없으면
    #     전건 null 이어야 한다. **null 은 미산출이지 0 이 아니다.**
    vpath = os.path.join(root, VALUES)
    if os.path.exists(vpath):
        with open(vpath, encoding="utf-8") as fh:
            vrecs = json.load(fh)["records"]
        cnt = {}
        for v in vrecs:
            if v["context_class"] == "규범":
                cnt[v["dstrcAppnNo"]] = cnt.get(v["dstrcAppnNo"], 0) + 1
        bad = [r["dstrcAppnNo"] for r in recs
               if r["확정값수"] != cnt.get(r["dstrcAppnNo"], 0)]
        gate(results, "확정값수 = norm_values 규범값 수", not bad,
             f"위반 {len(bad)} · 합계 {sum(r['확정값수'] or 0 for r in recs)}")
    else:
        bad = [r["dstrcAppnNo"] for r in recs if r["확정값수"] is not None]
        gate(results, "확정값수 전건 null (값 추출 미완)", not bad,
             f"위반 {len(bad)}")

    # 11. 값보유 문서 수 93 (설계 게이트 9)
    hv = sum(1 for r in recs if r["값줄수"] > 0)
    gate(results, "값 보유 문서 93", hv == 93,
         f"{hv} — 다르면 판정 로직이 바뀐 것이다. 계약을 먼저 갱신한다")

    # 12. 다중값+단일값 = 값줄수
    bad = [r["dstrcAppnNo"] for r in recs
           if r["다중값줄수"] + r["단일값줄수"] != r["값줄수"]]
    gate(results, "다중값 + 단일값 = 값줄수", not bad, f"위반 {len(bad)}")

    # 13. article_integrity_class 를 추정으로 채우지 않았는지 — codex 원본 대조.
    #     자기 일관성이 아니라 원자료(codex 산출물)를 다시 열어 판정을 재현한다.
    cxp = os.path.join(root, CODEX)
    if os.path.exists(cxp):
        with open(cxp, encoding="utf-8") as fh:
            cxdocs = {d["lc5"]: d for d in json.load(fh)["documents"]}
        bad = []
        for r in recs:
            d = cxdocs.get(r["dstrcAppnNo"])
            if d is None:
                if r["article_integrity_class"] is not None:
                    bad.append(r["dstrcAppnNo"])
                continue
            if d["verification_status"] != "verified":
                want = "미검증"
            elif (d["source_article_count"] == 0
                  and d["markdown_h4_article_count"] == 0):
                want = "조문없음"
            elif d["structure_missing_count"]:
                want = "구조누락"
            elif d["body_missing_count"]:
                want = "본문누락"
            elif d["order_damage_count"]:
                want = "순서훼손"
            else:
                want = "완전"
            if r["article_integrity_class"] != want:
                bad.append(r["dstrcAppnNo"])
        gate(results, "article_integrity_class = codex 원자료 재현",
             not bad, f"불일치 {len(bad)} / {len(recs)}")
    else:
        bad = [r["dstrcAppnNo"] for r in recs
               if r["article_integrity_class"] is not None]
        gate(results, "codex 미접근 시 article_integrity_class 전건 null",
             not bad, f"위반 {len(bad)}")

    # 13b. 미검증 문서를 결함 없음으로 읽지 않았는지
    bad = [r["dstrcAppnNo"] for r in recs
           if r["article_integrity_class"] == "미검증"
           and "결함 없음이 아니다" not in r["article_integrity_basis"]]
    gate(results, "미검증 basis 에 '결함 없음이 아니다' 명시", not bad,
         f"위반 {len(bad)}")

    # 14. 멱등성 — 2회 실행 바이트 동일 (설계 게이트 10)
    with tempfile.TemporaryDirectory() as td:
        outs = []
        for i in range(2):
            o = os.path.join(td, f"run{i}.json")
            subprocess.run(
                [sys.executable, os.path.join(HERE, "scan_table_loss.py"),
                 "--root", root, "--out", o],
                check=True, capture_output=True)
            with open(o, "rb") as fh:
                outs.append(hashlib.sha256(fh.read()).hexdigest())
        same = outs[0] == outs[1] and outs[0] == hashlib.sha256(
            body.encode("utf-8")).hexdigest()
        gate(results, "2회 실행 바이트 동일", same, f"sha256 {outs[0][:16]}…")

    _verify_values(root, results)
    _verify_estimate(root, results)
    _verify_subject_gap(root, results)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
    if failed:
        print("실패: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
