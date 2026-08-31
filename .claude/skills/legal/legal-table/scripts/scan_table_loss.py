#!/usr/bin/env python3
"""표 소실 계량 — 재변환 견적서 `_table_loss.json` 을 만든다.

md 189건 전건을 훑어 각 지구의 표가 어떤 방식으로 소실됐는지 갈래로 나눈다.
재변환 실행 판단은 이 산출물이 하지 않는다. 판단에 필요한 견적을 낼 뿐이다.

측정하지 않는 것 — 조문 단위 무결성(본문·줄범위·해시)은 다른 작업의 영역이다.
이쪽은 표 단위만 센다. codex 조문 판정과는 `article_integrity_class` 로 join 한다.

입력  output/legal/markdown/{서울,인천,경기}/*.md (189건)
      output/legal/table/_codex_article_integrity.json (있으면. 없으면 null 로 둔다)
출력  output/legal/table/_table_loss.json
전제  frontmatter 에 지구번호·지구명·지역·표 가 있다 (189건 전건 확인)
"""

import argparse
import collections
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import table_common as tc  # noqa: E402

OUT_PATH = "output/legal/table/_table_loss.json"
# 조문 무결성 정본. codex(다른 창)가 만든 산출물이며 main 에 병합돼 있다.
# `documents[].lc5` 는 이름과 달리 14자리 지구번호다 — 이쪽 `dstrcAppnNo` 와
# 붙인다. 이쪽 `lc5`(5자리 법정동코드)와 붙이면 전건 불일치가 난다.
CODEX_PATH = "output/legal/analysis/시행지침_조문_무결성_검증.json"
VALUES_PATH = "output/legal/table/norm_values.json"

# 커버리지 모수를 섞지 못하게 산출물에 박아두는 문장. design §배경 의 경고를
# meta 에 남긴다 — 두 수치를 한 분모로 섞으면 검증 커버리지가 두 배 넘게 부풀려진다.
COVERAGE_NOTE = (
    "이 산출물의 커버리지 93 과 조례 대조 커버리지 45 를 한 분모로 섞지 않는다. "
    "93 은 md 에서 회수 가능한 값 보유 문서 수이고, 45 는 조례 규범값과 대조 "
    "가능한 지구 수다. 전자는 md 관측 상한, 후자는 조례 쪽 관할 교집합이다."
)

# `표없음` 은 R1 검수에서 추가했다 — `소실없음` 이 "값 회수 성공" 과 "잴 표가
# 애초에 없음" 을 같은 칸에 넣고 있었다. 값 도메인을 늘렸으므로 계약
# (`contract/table_loss.schema.json`)을 먼저 갱신하고 여기를 고친다.
LOSS_CLASSES = ("표자체없음", "값깨짐", "OCR훼손", "소실없음", "표없음")
RECOVERY_GRADES = ("전량소실", "부분소실", "회수불가", "관측불가")
# loss_mix 의 키. 갈래마다 모수가 다르므로 단위를 이름에 박는다 —
# 이름이 같으면 나란히 비교하게 되고, 나란히 비교하면 안 되는 값들이다.
LOSS_MIX_KEYS = ("표자체없음_표건수", "값깨짐_값줄수", "소실없음_값줄수",
                 "OCR훼손_훼손줄수")


def _ocr_doc(doc):
    """이 문서가 OCR 추출본인지. frontmatter 원본구성의 `추출:` 이 정본이다."""
    return any("ocr" in m.lower() for m in doc["extraction_methods"])


def _value_lines(doc):
    """건폐율·용적률 값이 살아있는 줄. (1-based line, 값개수) 목록."""
    out = []
    lines = doc["lines"]
    for i in range(doc["body_start"], len(lines)):
        raw = lines[i]
        if tc.HTML_COMMENT.match(raw):
            continue
        if tc.METRIC_KW.search(raw):
            n = len(tc.PERCENT.findall(raw))
            if n:
                out.append((i + 1, n))
    return out


def _ocr_pipe_lines(doc):
    """OCR 이 표 괘선을 문자로 뱉은 줄. 189건 전건에서 정상 GFM 표행은 0이므로
    줄머리 파이프는 표가 아니라 훼손 흔적이다."""
    lines = doc["lines"]
    return [i + 1 for i in range(doc["body_start"], len(lines))
            if tc.OCR_PIPE_LINE.match(lines[i])]


def _body_present(doc, ref_line, window=8):
    """표 참조 아래에 표 본문이 실재하는지 근사한다.

    이 md 에는 정상 마크다운 표가 189건 전건에서 0개다. 그래서 '본문 실재' 는
    파이프 표 유무가 아니라 참조 직후 구간에 셀에 해당하는 값 줄이 있는지로
    본다. 근사값이므로 `본문실재` 는 관측이지 확정이 아니다.
    """
    lines = doc["lines"]
    lo = ref_line  # 1-based ref_line 다음 줄의 0-base 인덱스
    hi = min(len(lines), lo + window)
    for i in range(lo, hi):
        raw = lines[i]
        if not raw.strip() or tc.HTML_COMMENT.match(raw):
            continue
        if tc.PERCENT.search(raw) or tc.OCR_PIPE_LINE.match(raw):
            return True
    return False


def _classify(doc, refs, vlines, ocr_pipes):
    """loss_mix 를 세고 지배 갈래를 고른다.

    갈래를 하나로 접어서 정보를 버리지 않는다 — loss_class 는 지배값 하나이고
    loss_mix 에 갈래별 건수가 남는다.

    ## 지배값을 건수 비교로 정하지 않는다 — 모수가 다르기 때문이다

    각 갈래의 건수는 **단위가 다르다.** `표자체없음` 은 표 개수, `값깨짐`·
    `소실없음` 은 값 줄 개수, `OCR훼손` 은 훼손 줄 개수다. 큰 수를 지배값으로
    삼으면 frontmatter 표 수(최대 270)가 항상 이긴다.

    실제로 그렇게 판정했고 두 번 틀렸다.

      1차  건수 max      → 189건 중 173건이 `표자체없음`. 구분이 안 된다
      2차  표 우선 순서  → 값 보유 93문서 중 84문서가 `표자체없음`.
                           값이 살아 있는데 "표 자체가 없다"고 보고했다

    `.claude/rules/프로젝트-설계구조.md` §3 의 **모수** 항목이 이 실패다 —
    "분모를 잘못 잡으면 개별 수치가 모두 맞아도 분포가 틀린다."

    그래서 **값 회수 상태를 먼저 보고, 값이 없을 때만 표 소실을 본다.** 값이
    회수됐다는 것은 그 문서에서 표가 통째로 사라지지는 않았다는 직접 증거다.

      OCR훼손    문자 자체가 깨졌다 → 재스캔·재OCR. 재변환으로 안 된다
      소실없음   단일값 줄이 있다 → 주어를 붙일 수 있는 값이 회수됐다
      값깨짐     값은 있으나 전부 다중값이다 → 재변환 + 셀 복원이 필요하다
      표자체없음 값 줄이 0인데 표는 선언·참조됐다 → 재변환 회수 가능성이 가장 높다
      표없음     잴 표가 애초에 없다 → 측정 불가. 회수 성공이 아니다

    `소실없음` 은 그 문서의 표가 온전하다는 뜻이 **아니다.** 값 일부가 회수됐다는
    뜻이고, 남은 표 손실 규모는 `loss_mix` 의 `표자체없음_표건수` 가 말한다.
    두 정보를 한 라벨로 접지 않는다.

    loss_mix 의 키에는 **단위를 붙인다.** 이름이 같으면 나란히 비교하게 되고,
    나란히 비교하면 안 되는 값들이다.
    """
    mix = collections.Counter()

    if _ocr_doc(doc):
        mix["OCR훼손_훼손줄수"] = max(len(ocr_pipes), 1)

    # 선언된 표 중 md 에 본문이 남지 않은 것. md 에는 정상 마크다운 표가 189건
    # 전건에서 0개이므로 선언 수가 그대로 소실 표 수가 된다.
    #
    # 표 참조 표기(`<표2-1-1>`)로만 세면 시흥목감(frontmatter 표 153, 참조 표기
    # 0)처럼 표를 평문("다음 표의 범위를 초과하여")으로 가리키는 문서가 통째로
    # `소실없음` 으로 뒤집힌다. 실제로 그렇게 판정했다가 원문에서 오판이 드러났다.
    declared = doc["frontmatter_표"] or 0
    missing_refs = sum(1 for r in refs if not r["본문실재"])
    lost_tables = declared if declared else missing_refs
    if lost_tables:
        mix["표자체없음_표건수"] = lost_tables

    # 다중값 줄 = 열이 깨져 값과 주어가 붙지 않는다. 재변환해도 셀 복원이 필요하다
    broken = sum(1 for _, n in vlines if n >= 2)
    if broken:
        mix["값깨짐_값줄수"] = broken

    # 단일값 줄 = 주어를 붙일 수 있는 값이 그만큼 회수됐다
    intact = sum(1 for _, n in vlines if n == 1)
    if intact:
        mix["소실없음_값줄수"] = intact

    mix = dict(sorted(mix.items()))

    # 판정 우선순위. 건수가 아니라 값 회수 상태로 가른다.
    if _ocr_doc(doc):
        return "OCR훼손", mix
    if intact:
        return "소실없음", mix
    if broken:
        return "값깨짐", mix
    if lost_tables:
        return "표자체없음", mix
    # 값도 없고 표 선언·참조도 없다. 회수 성공이 아니라 **잴 것이 없는** 자리다.
    return "표없음", mix


def _recovery_grade(doc, vlines, mix):
    """재변환 우선순위 등급. `loss_class` 와 축이 다르다.

    `loss_class` 는 "무엇이 필요한가"(처방), 이 등급은 "어느 지구부터 손대야
    하는가"(회수량)다. 한 문서가 `소실없음` 이면서 `부분소실` 일 수 있다 —
    값 일부는 회수됐고 표 다수는 여전히 비어 있는 상태다. 두 축을 한 라벨로
    접으면 그 상태를 표현할 수 없다.

      회수불가   OCR 훼손. 재변환으로 안 된다
      전량소실   표를 선언했는데 값이 한 줄도 남지 않았다. 회수량이 가장 크다
      부분소실   값 일부가 남았고 나머지는 표와 함께 사라졌다
      관측불가   표 선언도 값도 깨진 값도 없다. 이 검사가 볼 근거가 없다
    """
    if mix.get("OCR훼손_훼손줄수"):
        return "회수불가"
    if mix.get("표자체없음_표건수"):
        return "전량소실" if not vlines else "부분소실"
    if mix.get("값깨짐_값줄수"):
        # 소실 표는 못 셌지만 열이 깨진 값 줄이 실재한다. 관측된 손실이다.
        return "부분소실"
    return "관측불가"


def _reconvert(mix):
    """재변환 후보인지. **`loss_class` 에서 파생하지 않는다.**

    `loss_class` 는 값 회수 상태로 정한 처방 라벨이라, 값 일부가 살아난 문서는
    `소실없음` 이 된다. 그 문서에도 비어 있는 표가 수십 개 남아 있다 — 라벨로
    파생하면 그 표들이 견적에서 통째로 빠진다.

    후보 조건은 **회수할 표가 남아 있는가** 다. OCR 문서는 재변환으로 회수되지
    않으므로(재스캔·재OCR 문제) 제외한다.
    """
    if mix.get("OCR훼손_훼손줄수"):
        return False
    return bool(mix.get("표자체없음_표건수") or mix.get("값깨짐_값줄수"))


def _reconvert_basis(mix):
    if mix.get("OCR훼손_훼손줄수"):
        return "OCR 훼손 — 재변환 대상이 아니다. 재스캔·재OCR 문제다"
    lost = mix.get("표자체없음_표건수", 0)
    broken = mix.get("값깨짐_값줄수", 0)
    if lost or broken:
        return (f"회수 대상 — 본문 없는 표 {lost}건 · 열 깨진 값 줄 {broken}건")
    return "회수할 표를 관측하지 못했다 — 표 선언·참조·깨진 값이 모두 0"


def _loss_basis(doc, refs, vlines, ocr_pipes, dominant, mix):
    """판정 근거 문장. 수치를 넣어 원문 대조가 가능하게 한다."""
    fm = doc["frontmatter_표"]
    missing = sum(1 for r in refs if not r["본문실재"])
    multi = sum(1 for _, n in vlines if n >= 2)
    single = sum(1 for _, n in vlines if n == 1)
    parts = [
        f"frontmatter 선언 표 {fm} 대비 md 본문 표 0",
        f"표 참조 표기 {len(refs)}건 중 직후 구간에 내용 실재 {len(refs) - missing}건",
        f"값 줄 {len(vlines)}건(다중값 {multi} · 단일값 {single})",
    ]
    if _ocr_doc(doc):
        parts.append(
            f"OCR 추출본 — 문자 훼손. 표 괘선 잔해 줄 {len(ocr_pipes)}건. "
            "건수와 무관하게 OCR훼손이 지배값이다")
    parts.append(f"지배 갈래 {dominant} (값 회수 상태 우선. 건수 비교 아님)")
    if dominant == "소실없음" and mix.get("표자체없음_표건수"):
        parts.append(
            f"단, 본문 없는 표 {mix['표자체없음_표건수']}건이 남아 있다 — "
            "소실없음은 값 일부 회수를 뜻하지 표가 온전하다는 뜻이 아니다")
    return ". ".join(parts)


def _load_confirmed_values(root):
    """지구별 확정 규범값 수. `norm_values.json` 이 없으면 None.

    없으면 전건 `null` 이고 **0 이 아니다** — 미산출이지 결손이 아니다.
    """
    p = os.path.join(root, VALUES_PATH)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        recs = json.load(fh)["records"]
    out = collections.Counter(
        r["dstrcAppnNo"] for r in recs if r["context_class"] == "규범")
    return out


def _integrity_class(d):
    """문서 단위 조문 무결성 지배값과 근거.

    **건수 max 로 뽑지 않는다.** `complete_count` 가 거의 항상 최대라 결함이
    가려진다(실측: max 로 뽑으면 104문서가 `complete` 로 나온다). 결함 유무가
    처방을 가르므로 결함 우선순위로 판정한다.

    `조문없음` 은 원본·markdown 양쪽에 조문이 0개인 문서다(48건). `완전` 과
    구분한다 — 검증할 조문이 없던 것이지 검증을 통과한 것이 아니다.

    판정 정의의 정본은 `build_retransform_estimate.py._integrity_class` 이며
    여기서는 같은 규칙을 재현한다. 두 곳이 갈리면 안 되므로 규칙을 바꿀 때
    함께 고친다.
    """
    if d["verification_status"] != "verified":
        return "미검증", (
            "codex verification_status=unverified — 원본 대조 증거에서 제외됐다. "
            "미검증은 결함 없음이 아니다. 사유: "
            f"{(d.get('unverified_reason') or '')[:100]}")
    if d["source_article_count"] == 0 and d["markdown_h4_article_count"] == 0:
        return "조문없음", ("원본·markdown 양쪽 조문 0개. 검증할 조문이 없었던 "
                            "것이지 검증을 통과한 것이 아니다")
    parts = (f"완전 {d['complete_count']} · 본문누락 {d['body_missing_count']} · "
             f"구조누락 {d['structure_missing_count']} · "
             f"순서훼손 {d['order_damage_count']}")
    if d["structure_missing_count"]:
        return "구조누락", f"구조누락 {d['structure_missing_count']}건 우선. {parts}"
    if d["body_missing_count"]:
        return "본문누락", f"본문누락 {d['body_missing_count']}건 우선. {parts}"
    if d["order_damage_count"]:
        return "순서훼손", f"순서훼손 {d['order_damage_count']}건 우선. {parts}"
    return "완전", f"결함 0건. {parts}"


def _load_codex(root):
    """codex 조문 무결성 판정을 읽는다. 없으면 None — 추정해 채우지 않는다.

    join 키는 `documents[].lc5`(14자리 지구번호)다. 이름이 `lc5` 라고 이쪽
    5자리 법정동코드와 붙이면 전건 불일치가 난다.
    """
    p = os.path.join(root, CODEX_PATH)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        raw = json.load(fh)
    out = {}
    for d in raw.get("documents", []):
        key = d.get("lc5")
        if key:
            out[key] = _integrity_class(d)
    return out


def build(root="."):
    codex = _load_codex(root)
    confirmed = _load_confirmed_values(root)
    records = []
    for path in tc.md_files(root):
        with open(path, encoding="utf-8") as fh:
            doc = tc.parse_document(fh.read())

        refs_raw = tc.table_refs(doc)
        vlines = _value_lines(doc)
        ocr_pipes = _ocr_pipe_lines(doc)

        refs = []
        for r in refs_raw:
            art, why = tc.article_at(doc, r["line"])
            sec = tc.section_at(doc, r["line"])
            rec = {
                "surface": r["surface"],
                "line": r["line"],
                "kind": r["kind"],
                "is_caption": r["is_caption"],
                "article": ({"no": art["조번호"], "label": art["표제"],
                             "line": art["line"], "origin": "조문헤딩"}
                            if art else None),
                "section": sec["heading"] if sec else None,
                "section_line": sec["line"] if sec else None,
                "본문실재": _body_present(doc, r["line"]),
                "인용문": r["line_text"].strip(),
            }
            if art is None:
                # 없는 조문을 가장 가까운 h4 로 채우지 않는다
                rec["article_reason"] = why
            refs.append(rec)

        dominant, mix = _classify(doc, refs, vlines, ocr_pipes)
        recovery = _recovery_grade(doc, vlines, mix)
        dstrc = doc["지구번호"]
        records.append({
            "dstrcAppnNo": dstrc,
            "lc5": dstrc[:5] if dstrc else None,
            "지역": doc["지역"],
            "district": doc["지구명"],
            "source_file": tc.rel_path(path, root),
            "extraction_methods": doc["extraction_methods"],
            "frontmatter_표": doc["frontmatter_표"],
            "본문_파이프표": 0,
            "본문_OCR파이프줄": len(ocr_pipes),
            "표참조수": len(refs),
            "표참조_본문실재수": sum(1 for r in refs if r["본문실재"]),
            "값줄수": len(vlines),
            "다중값줄수": sum(1 for _, n in vlines if n >= 2),
            "단일값줄수": sum(1 for _, n in vlines if n == 1),
            "loss_class": dominant,
            "loss_mix": mix,
            "recovery_grade": recovery,
            "loss_basis": _loss_basis(doc, refs, vlines, ocr_pipes,
                                      dominant, mix),
            "소실표건수": mix.get("표자체없음_표건수", 0),
            "확정값수": (None if confirmed is None
                        else confirmed.get(dstrc, 0)),
            "reconvert_candidate": _reconvert(mix),
            "reconvert_basis": _reconvert_basis(mix),
            "article_integrity_class": (codex or {}).get(dstrc, (None, None))[0],
            "article_integrity_basis": (
                f"codex 산출물 미접근 — {CODEX_PATH} 가 없다. 추정해 채우지 않는다"
                if codex is None
                else (codex.get(dstrc, (None, "codex 문서에 이 지구가 없다"))[1])),
            "표참조": refs,
        })

    records.sort(key=lambda r: (r["dstrcAppnNo"] or "", r["source_file"]))

    by_class = collections.Counter(r["loss_class"] for r in records)
    by_grade = collections.Counter(r["recovery_grade"] for r in records)
    meta = {
        "생성기": "legal-table/scripts/scan_table_loss.py",
        "입력": "output/legal/markdown/{서울,인천,경기}/*.md",
        "문서수": len(records),
        "coverage_note": COVERAGE_NOTE,
        "줄번호규약": "모든 line 은 md 파일 물리 줄번호(1-based)다. "
                      "frontmatter 를 잘라낸 body 오프셋이 아니다",
        "loss_class_domain": list(LOSS_CLASSES),
        "loss_class_분포": {k: by_class.get(k, 0) for k in LOSS_CLASSES},
        "recovery_grade_domain": list(RECOVERY_GRADES),
        "recovery_grade_분포": {k: by_grade.get(k, 0) for k in RECOVERY_GRADES},
        "loss_mix_keys": list(LOSS_MIX_KEYS),
        "reconvert_candidate수": sum(1 for r in records
                                     if r["reconvert_candidate"]),
        "값보유문서수": sum(1 for r in records if r["값줄수"] > 0),
        "본문_파이프표_보유문서수": 0,
        "선언표_합계": sum(r["frontmatter_표"] or 0 for r in records),
        "소실표_합계": sum(r["소실표건수"] for r in records),
        "견적_사용법": (
            "reconvert_candidate 는 '회수할 표가 남아 있는가' 의 참·거짓이라 "
            "189건 중 대부분이 참이 된다. 그 자체로는 범위를 좁히지 못한다. "
            "범위는 소실표건수로 정한다 — 소실표>=100 인 52문서가 소실 표의 "
            "62%, >=50 인 100문서가 88% 를 안고 있다. recovery_grade=전량소실 "
            "92문서(표 6,754건)는 값이 한 줄도 남지 않아 회수 이득이 가장 크다"),
        "확정값수_상태": (
            "확정값수는 norm_values.json 의 context_class=규범 레코드를 지구별로 "
            "센 값이다. norm_values.json 이 없으면 전건 null 이 되며, 그 null 은 "
            "미산출이지 0 이 아니다. 값이 0인 지구는 표가 소실돼 회수된 규범값이 "
            "없다는 뜻이고, null 과 구분해 읽는다"
            if any(r["확정값수"] is not None for r in records)
            else "확정값수는 전건 null 이다. 값 추출이 미완이라 아직 산출되지 "
                 "않았다 — **0 이 아니다.** 결손이 아니라 미산출이므로 0 으로 "
                 "읽거나 집계에 넣지 않는다"),
        "확정값_합계": (None if all(r["확정값수"] is None for r in records)
                       else sum(r["확정값수"] or 0 for r in records)),
        "확정값_보유문서수": (
            None if all(r["확정값수"] is None for r in records)
            else sum(1 for r in records if r["확정값수"])),
        "판정_규약": [
            "loss_class 는 건수 비교가 아니라 값 회수 상태로 정한다 "
            "(OCR훼손 > 소실없음 > 값깨짐 > 표자체없음 > 표없음). 갈래마다 "
            "모수가 다르므로(표 개수 대 값 줄 개수) 큰 수를 지배값으로 삼으면 "
            "frontmatter 표 수가 항상 이긴다",
            "loss_mix 의 키에는 단위가 붙어 있다. 이름이 같으면 나란히 "
            "비교하게 되는데, 모수가 달라 비교하면 안 되는 값들이다",
            "소실없음 은 '그 문서의 표가 온전하다' 가 아니라 '주어를 붙일 수 "
            "있는 값이 회수됐다' 다. 남은 표 손실 규모는 loss_mix 의 "
            "표자체없음_표건수 가 말한다",
            "표없음 은 잴 표가 애초에 없는 자리다. 회수 성공과 구분한다",
            "reconvert_candidate 는 loss_class 에서 파생하지 않는다. 값이 "
            "일부 살아난 문서(소실없음)에도 비어 있는 표가 남아 있어, 라벨로 "
            "파생하면 그 표들이 견적에서 빠진다. 조건은 '회수할 표가 남아 "
            "있는가' 다",
            "loss_class 와 recovery_grade 는 축이 다르다. 한 문서가 "
            "소실없음이면서 부분소실일 수 있다",
        ],
        "관측_한계": [
            "본문실재 는 표 참조 직후 8줄 안의 값 줄·괘선 잔해로 근사한 관측값이다. "
            "확정값이 아니다",
            "정상 마크다운 표는 189건 전건에서 0개다. 설계가 말한 '파이프 표 보유 "
            "4문서' 는 OCR 이 괘선을 문자로 뱉은 줄이며 표가 아니다",
            "frontmatter 표 수는 원본에서 센 값이라 소실 표 수의 하한이다. "
            "표=0 으로 선언된 문서에도 셀이 줄로 풀린 표 흔적이 실재한다"
            "(죽산지구 도시개발구역)",
            "조문 단위 무결성(본문·줄범위·해시)은 이 산출물이 측정하지 않는다",
        ],
    }
    return {"meta": meta, "records": records}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    data = build(args.root)
    out = args.out or os.path.join(args.root, OUT_PATH)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(body)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    print(f"{out}  문서 {len(data['records'])}  sha256 {digest}")
    for k in LOSS_CLASSES:
        print(f"  loss_class {k}: {data['meta']['loss_class_분포'][k]}")
    for k in RECOVERY_GRADES:
        print(f"  recovery_grade {k}: {data['meta']['recovery_grade_분포'][k]}")


if __name__ == "__main__":
    main()
