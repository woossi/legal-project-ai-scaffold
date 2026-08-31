#!/usr/bin/env python3
"""시행지침 조문 본문에서 외부·내부 참조를 뽑아 참조 레코드로 만든다.

`legal-statute/build_citations.py` 는 `terms.json` 의 정의문(`variants[].text`)
에서만 인용을 뽑는다. 정의문 밖의 조문 본문, 내부 참조와 외부 참조의 구분,
별표·별지·부칙, 범위 참조, 준용 관계는 그쪽에 없다. 이 스크립트가 그 빈 곳을
채운다.

판정은 세 겹이다.
  1단 법령명 어휘를 만든다. `statute_master.json` 의 법령 정본과, 코퍼스에서
      인용부호로 묶인 채 법령 접미사로 끝나는 표기를 학습한다.
  2단 조문 구간마다 참조 표기를 찾는다. 인용부호 묶음 · 무인용 법령명 ·
      조문번호 · 별표 · 별지 · 부칙 · 범위 · 상대참조 · 준용 서술.
  3단 내부·외부를 가른다. 법령명 없는 조문 참조를 외부로 승격하지 않는다 —
      선행 법령이 인접해 있을 때만 외부이고, 없으면 문서 조문 트리 실재나
      `이 지침` 명시를 근거로 내부로 판정한다. 근거가 없으면 미판정이다.

해소하지 못한 것은 `xref_index.json` 에 넣지 않고 `_xref_report.json` 에
사유와 함께 격리한다. kb-plan 절대규칙 5(추측 관계 금지)와 같다.

입력  output/legal/markdown/<지역>/<지구명>.md
      output/legal/statute/statute_master.json
      output/legal/statute/article_master.json  (선택 · 범위 전개 실재 확인)
출력  output/legal/xref/xref_index.json
      output/legal/xref/_xref_report.json
"""

import argparse
import collections
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import xref_common as X                                        # noqa: E402


# ── 문장 분절 ───────────────────────────────────────────────────────────────
# 선행 법령 판정의 사정거리다. 줄 하나가 조문 한 항(`- ① …`)인 조판이므로
# 줄을 기본 단위로 삼고, 줄 안에서 `…다.` 로 끝나는 문장을 다시 가른다.
SENT_SPLIT = re.compile(r"(?<=다)\.\s|(?<=[)）])\.\s|;\s")


def sentences(line):
    """줄 하나를 문장 구간 [(시작, 끝)] 으로 가른다."""
    out, cur = [], 0
    for m in SENT_SPLIT.finditer(line):
        out.append((cur, m.end()))
        cur = m.end()
    out.append((cur, len(line)))
    return [(a, b) for a, b in out if b > a]


def sentence_of(spans, pos):
    for a, b in spans:
        if a <= pos < b:
            return a, b
    return 0, 0


# ── 1단: 법령명 어휘 ────────────────────────────────────────────────────────
def split_name_refs(s):
    """인용부호 안 문자열을 대상명과 뒤따르는 참조 표기로 가른다.

    `건축법 시행령 별표 1` → (`건축법 시행령`, 12)
    `국토의 계획 및 이용에 관한 법률 제51조` → (`국토의 계획 및 이용에 관한 법률`, 22)
    """
    cuts = [len(s)]
    for rx in (X.ART_RE, X.CLAUSE_RE, X.ANNEX_RE, X.FORM_RE):
        m = rx.search(s)
        if m and m.start() > 0:
            cuts.append(m.start())
    cut = min(cuts)
    return s[:cut].strip(), cut


def learn_vocab(docs, master):
    """법령명 어휘를 만든다.

    정본(`statute_master.json`)이 먼저다. 코퍼스 학습은 **인용부호로 묶인
    표기에서만** 한다 — 묶이지 않은 표기에는 앞 문장의 서술이 붙어 들어온다
    (build_citations 판정 규칙과 같다).
    """
    vocab, seen = {}, collections.Counter()
    for st in master.values():
        for form in (st.get("실측표기"), st.get("정식명칭")):
            if form:
                vocab.setdefault(X.canon_key(form), form)
        vocab.setdefault(st["statute_key"], st.get("실측표기") or st["statute_key"])

    for d in docs:
        text = "\n".join(d["lines"])
        for m in X.QUOTED.finditer(text):
            name, _ = split_name_refs(m.group(1))
            name = re.sub(r"\s+", " ", name).strip()
            k = X.canon_key(name)
            if len(k) < 3 or k in X.NOT_LAW or X.REL_HEAD.match(k):
                continue
            base = X.ORG_PAREN.sub("", k) or k
            if not X.LAW_SUFFIX.search(base) or X.NOT_LAW_TAIL.search(base):
                continue
            if re.search(r"함은|이란\s|란\s", name) or re.match(r"^제\s*\d", name):
                continue
            if k in d["headings"]:            # 같은 문서의 표목이면 법령이 아니다
                continue
            seen[k] += 1
            vocab.setdefault(k, name)
    return vocab, seen


def master_status(st):
    """법령 정본 대조 상태. legal-statute 의 검증상태를 그대로 받는다.

    statute_master 에 있다는 것과 법제처 정본에 대조됐다는 것은 다르다.
    미대조 항목을 정본대조로 승격하면 관측값이 확정값으로 굳는다.
    """
    if not st:
        return "미수록"
    return "정본대조" if st.get("검증상태") == "정본대조" else "미대조"


def official(st):
    """법제처 정식명칭. 대조되지 않았으면 비운다."""
    if not st or st.get("검증상태") != "정본대조":
        return ""
    return st.get("정식명칭") or ""


def build_scanner(vocab):
    """어휘를 최장 일치 우선으로 찾는 정규식. 공백·중점 변이를 허용한다."""
    keys = sorted(vocab, key=len, reverse=True)
    parts = [X.SEP.join(re.escape(ch) for ch in k) for k in keys]
    return re.compile("(" + "|".join(parts) + ")") if parts else None


# ── 2단: 조문 구간 스캔 ─────────────────────────────────────────────────────
# 대상명과 그 조문 참조 사이에 끼는 것. 열거·조사·닫는 인용부호만 허용한다.
# 문자 집합으로 두면 `제5조에서 정한 제10조` 처럼 떨어진 참조까지 삼킨다.
TAIL_GAP = re.compile(
    "(?:[\\s,·․‧∙ㆍ()（）\\[\\]" + re.escape(X.QUOTE_CLOSE + X.QUOTE_SOFT_CLOSE)
    + "]|및|와|과|의|그|또는|각각|규정|에\\s*따른|에\\s*의한|에\\s*따라)*"
)


def consume_refs(line, pos, limit=60):
    """대상명 바로 뒤에 이어지는 참조 표기를 모은다.

    `articles_after`(build_citations) 와 같은 방식이되 별표·별지·범위까지
    받는다. 대상명 직후부터 연속으로 이어지는 것만 인정한다.
    """
    refs, annex, form, rng = [], None, None, None
    cur = pos
    end = min(len(line), pos + limit)
    while cur < end:
        g = TAIL_GAP.match(line, cur)
        nxt = g.end() if g else cur
        if nxt >= end:
            break
        m = X.RANGE_RE.match(line, nxt)
        if m and not refs:
            rng = (X.norm_art(m.group(1)), X.norm_art(m.group(2)))
            cur = m.end()
            continue
        m = X.ART_RE.match(line, nxt)
        if m:
            refs.append(X.norm_art(m.group()))
            cur = m.end()
            continue
        m = X.CLAUSE_RE.match(line, nxt)
        if m:
            refs.append(X.norm_art(m.group()))
            cur = m.end()
            continue
        m = X.ANNEX_RE.match(line, nxt)
        if m and m.group(1):
            annex = f"별표{m.group(1)}"
            cur = m.end()
            continue
        m = X.FORM_RE.match(line, nxt)
        if m and m.group(0).strip():
            form = ("별지제%s호서식" % m.group(1)) if m.group(1) else "별지"
            cur = m.end()
            continue
        break
    return refs, annex, form, rng, cur


def occupied(spans, a, b):
    return any(not (b <= s or a >= e) for s, e in spans)


def classify_quoted(name, key, headings):
    """인용부호로 묶인 표기의 유형을 가른다.

    절대규칙 3 — 인용부호로 묶였다는 이유만으로 법령으로 만들지 않는다. 같은
    문서의 표목·조문 표제와 대조하고, 표기만으로 외부 문서인지 이 지침이 속한
    계획의 구성 항목인지 갈리지 않으면 미판정으로 둔다.
    """
    if key in headings or key in X.SELF_DOC:
        return "내부표목"
    # 편·장·절 표기는 문서 내부 구조다. 조문 참조만 남는 경우
    # (`‘제1편 제1장 제14조②항’`)는 조문 참조 쪽에서 편·장 한정어로 다룬다.
    sm = X.SEC_RE.match(key)
    if sm:
        rest = re.sub(rf"^(?:제\s*{X.SEC_NUM}\s*(?:편|장|절)\s*)+", "", key)
        return "내부표목" if rest else None
    if len(key) < 3 or key in X.NOT_LAW or X.REL_HEAD.match(key):
        return None
    # 발행 주체 괄호는 접미사 판정에서 떼고 이름에는 남긴다
    base = X.ORG_PAREN.sub("", key) or key
    if X.LAW_SUFFIX.search(base) and not X.NOT_LAW_TAIL.search(base):
        return "법령"
    if X.DOC_SUFFIX.search(base):
        return "외부문서"
    if X.AMBIG_DOC_SUFFIX.search(base):
        return "외부문서" if X.ORG_PREFIX.search(name) else "미판정문서"
    return None


def find_law_mentions(line, scanner, vocab, headings):
    """줄에서 대상명 표기를 찾는다. (시작, 이름끝, 표기끝, 대상명, 인용여부, 유형)"""
    out, spans = [], []
    for m in X.QUOTED.finditer(line):
        name, cut = split_name_refs(m.group(1))
        name = re.sub(r"\s+", " ", name).strip()
        k = X.canon_key(name)
        kind = classify_quoted(name, k, headings)
        if not kind:
            continue
        # 표기 원문은 인용부호를 포함한다. 참조 표기 소비는 부호 안쪽에서 시작해
        # `「건축법 시행령 별표 1」` 과 `「건축법 시행령」 별표 1` 을 함께 받는다.
        out.append((m.start(), m.start(1) + cut, m.end(), name, True, kind))
        spans.append((m.start(), m.end()))

    if scanner:
        for m in scanner.finditer(line):
            k = X.canon_key(m.group(1))
            if k not in vocab or occupied(spans, m.start(), m.end()):
                continue
            if k in headings:
                continue
            out.append((m.start(), m.end(), m.end(), m.group(1), False, "법령"))
    out.sort(key=lambda t: t[0])
    return out


# ── 3단: 내부·외부 판정 ────────────────────────────────────────────────────
def judge_internal(line, sent, pos, doc, refs, art_idx):
    """법령명이 앞에 없는 참조의 내부·외부를 가른다.

    절대규칙 2 — 법령명 없는 조문 참조를 외부로 승격하지 않는다. 판정 근거를
    함께 담고, 근거가 없으면 미판정이다.
    """
    head = line[sent[0]:pos]
    if X.SELF_REF_RE.search(head):
        return "내부", "본지침명시"
    # 어휘가 못 잡은 법령명이 바로 앞에 있으면 내부로 뒤집지 않는다
    if X.LAW_TAIL_BEFORE.search(head[-45:]):
        return "미판정", "선행법령_어휘미포착"
    for r in refs:
        m = re.match(r"제(\d+)조(?:의(\d+))?$", r)
        if m and X.art_key(m.group(1), m.group(2)) in doc["by_artno"]:
            return "내부", "문서조문실재"
    # 조번호 없이 항·호·목만 적힌 참조는 그 조문 안을 가리킨다
    # (`2. 제1호에 의한 건축기준 완화는 …`). 조문 밖이면 가리킬 곳이 없다.
    if art_idx is not None and refs and all(
            re.match(r"제\d+(?:항|호|목)", r) for r in refs):
        return "내부", "현재조문내"
    return "미판정", "근거없음"


SEC_QUALIFIER = re.compile(
    rf"((?:제\s*{X.SEC_NUM}\s*(?:편|장|절)\s*)+)$"
)


def sec_hint(line, sent_start, pos):
    """조문 참조 바로 앞의 편·장·절 한정어를 읽는다.

    `‘제1편 제1장 제14조②항’` · `제2편 제6장 제131조` 처럼 편·장이 붙은 내부
    참조가 실측 다수다. 한정어가 있으면 그 자체가 내부 판정 근거이며 동일
    조번호 후보를 좁히는 근거이기도 하다.
    """
    head = line[max(sent_start, pos - 40):pos]
    m = SEC_QUALIFIER.search(head)
    if not m:
        return None
    out = {"편": None, "장": None, "절": None}
    for lv, n in X.sec_chain(m.group(1)):
        out[lv] = n
    return out if any(out.values()) else None


def resolve_internal_article(doc, artno, title_hint, sec=None):
    """내부 조문 표기를 문서 조문 노드에 잇는다.

    같은 조번호가 한 문서에서 여러 번 나오는 것이 실측 다수이므로(조문이 있는
    133개 문서 중 97개, (문서·조번호) 쌍 2,461개), 후보가 둘 이상이면 IRI 를
    비우고 후보수만 남긴다.
    하나를 고르는 것은 추측이다. 표기가 표제를 함께 담거나
    (`제12조의9(블록형 단독주택용지 관리)`) 편·장 한정어를 달면
    (`제1편 제1장 제12조`) 그 근거로 좁힌다.
    """
    m = re.match(r"제(\d+)조(?:의(\d+))?$", artno or "")
    if not m:
        return None, 0
    cands = doc["by_artno"].get(X.art_key(m.group(1), m.group(2)), [])
    if sec and len(cands) > 1:
        narrowed = [i for i in cands
                    if all(sec[lv] is None or doc["articles"][i][lv] == sec[lv]
                           for lv in ("편", "장", "절"))]
        if narrowed:
            cands = narrowed
    if title_hint and len(cands) > 1:
        k = X.canon_key(title_hint)
        narrowed = [i for i in cands if X.canon_key(doc["articles"][i]["표제"]) == k]
        if len(narrowed) == 1:
            cands = narrowed
    if len(cands) == 1:
        return doc["articles"][cands[0]]["iri"], 1
    return None, len(cands)


def title_in_doc(doc, title_hint):
    """표기가 함께 단 표제가 그 문서의 조문 표제인지 본다."""
    if not title_hint:
        return False
    return X.canon_key(title_hint) in doc["headings"]


def expand_range(doc, a, b):
    """범위를 개별 조문으로 전개한다.

    절대규칙 4 — 양 끝 조문이 대상 문서에 실재할 때만 전개한다. 실재하지
    않으면 전개하지 않고 원문 표기만 남긴다.
    """
    ma = re.match(r"제(\d+)조(?:의(\d+))?$", a or "")
    mb = re.match(r"제(\d+)조(?:의(\d+))?$", b or "")
    if not (ma and mb):
        return None, "양 끝 조문번호를 읽지 못했다"
    ka, kb = X.art_key(ma.group(1), ma.group(2)), X.art_key(mb.group(1), mb.group(2))
    if ka not in doc["by_artno"]:
        return None, f"시작 조문 {a} 이 문서 조문 트리에 없다"
    if kb not in doc["by_artno"]:
        return None, f"끝 조문 {b} 이 문서 조문 트리에 없다"
    lo, hi = int(ma.group(1)), int(mb.group(1))
    if hi < lo:
        return None, "끝 조문번호가 시작보다 작다"
    out = []
    for art in doc["articles"]:
        n = int(art["조번호키"].split("-")[0])
        if lo <= n <= hi and art["조번호"] not in out:
            out.append(art["조번호"])
    return out, "문서 조문 트리에 실재하는 양 끝 사이를 채웠다"


# ── 추출 ────────────────────────────────────────────────────────────────────
def quote_of(line, s, e):
    """근거 발췌. 참조 위치 기준이다 — 문장 앞부분을 잘라 담으면 뒤쪽 참조가
    근거 밖으로 밀려난다(legal-statute 실측 9건)."""
    return line[max(0, s - 80): e + 120]


def new_rec(doc, path, region, li, s, e, line, art_idx):
    art = doc["articles"][art_idx] if art_idx is not None else None
    return {
        "dstrcAppnNo": doc["지구번호"],
        "지구명": doc["지구명"],
        "지역": region,
        "source_file": path,
        "line": li + 1,
        "pos": s,
        "article_origin": "조문헤딩" if art else "문서레벨",
        "article_iri": art["iri"] if art else None,
        "article_no": art["조번호"] if art else None,
        "article_label": art["표제"] if art else None,
        "surface": line[s:e],
        "name_surface": None,
        "scope": None,
        "scope_basis": None,
        "kind": None,
        "relation": "참조",
        "resolution": None,
        "target": {"statute_key": None, "statute_official": "", "master_status": None,
                   "articles": [], "annex": None, "form": None,
                   "article_iri": None, "heading": None, "후보수": None},
        "range": None,
        "quote": quote_of(line, s, e),
    }


def scan_line(doc, path, region, li, line, art_idx, scanner, vocab, master, out):
    spans = []
    sent_spans = sentences(line)
    mentions = find_law_mentions(line, scanner, vocab, doc["headings"])

    # ① 대상명 + 뒤따르는 참조 표기
    laws_at = []                     # (끝위치, statute_key, 표기) — 상대참조 해소용
    for s, name_end, mend, name, quoted, mtype in mentions:
        if occupied(spans, s, mend):
            continue
        refs, annex, form, rng, end = consume_refs(line, name_end)
        end = max(end, mend)
        r = new_rec(doc, path, region, li, s, end, line, art_idx)
        r["name_surface"] = name
        key = X.canon_key(name)
        if mtype == "내부표목":
            r["scope"], r["scope_basis"] = "내부", "인용부호표목"
            r["kind"], r["resolution"] = "내부표목", "직접"
            r["target"]["heading"] = name
        elif mtype == "미판정문서":
            r["scope"], r["scope_basis"] = "미판정", "문서구분불가"
            r["kind"], r["resolution"] = "외부문서", "미해소"
            r["resolution_note"] = (
                "인용부호로 묶였으나 같은 문서의 표목도, 발행 주체·연도가 붙은 "
                "별개 문서도 아니다. 계획의 구성 항목일 수 있어 승격하지 않는다")
        elif mtype == "외부문서":
            r["scope"], r["scope_basis"] = "외부", "인용부호문서"
            r["kind"], r["resolution"] = "외부문서", "직접"
            r["target"]["heading"] = name
        else:
            laws_at.append((end, key, name))
            st = master.get(key)
            r["scope"] = "외부"
            r["scope_basis"] = "인용부호법령" if quoted else "어휘일치법령"
            r["resolution"] = "직접"
            r["target"]["statute_key"] = key
            r["target"]["master_status"] = master_status(st)
            r["target"]["statute_official"] = official(st)
            if rng:
                r["kind"] = "범위"
                r["range"] = {"from": rng[0], "to": rng[1], "expanded": [],
                              "expand_basis": None,
                              "expand_fail": "대상 법령의 조문 목록이 없어 "
                                             "양 끝 실재를 확인할 수 없다"}
                r["resolution"] = "미해소"
            elif annex:
                r["kind"] = "별표"
                r["target"]["annex"] = annex
                r["target"]["articles"] = refs
            elif form:
                r["kind"] = "별지·서식"
                r["target"]["form"] = form
                r["target"]["articles"] = refs
            elif refs:
                r["kind"] = "법령조문"
                r["target"]["articles"] = refs
            else:
                r["kind"] = "법령"
        spans.append((s, end))
        out.append(r)

    # ② 상대참조 — 같은 문장의 앞선 법령으로 해소한다
    for m in X.REL_RE.finditer(line):
        if occupied(spans, m.start(), m.end()):
            continue
        sa, sb = sentence_of(sent_spans, m.start())
        refs, annex, form, rng, end = consume_refs(line, m.end())
        r = new_rec(doc, path, region, li, m.start(), end, line, art_idx)
        r["name_surface"] = m.group().strip()
        r["scope"], r["scope_basis"] = "외부", "상대참조"
        r["kind"] = "법령조문" if refs else "법령"
        sub = m.group(1) if m.lastindex else None
        prior = [(k, n) for p, k, n in laws_at
                 if sa <= p <= m.start() and not k.endswith(("시행령", "시행규칙"))]
        if not prior:
            r["resolution"] = "미해소"
            r["scope"], r["scope_basis"] = "미판정", "선행법령없음"
            r["resolution_note"] = "문장 안에 선행 법령이 없어 상대참조를 해소하지 못했다"
        else:
            base, base_name = prior[-1]
            key = X.canon_key(base + (sub or ""))
            st = master.get(key)
            r["resolution"] = "상대참조해소"
            r["target"]["statute_key"] = key
            r["target"]["master_status"] = master_status(st)
            r["target"]["statute_official"] = official(st)
            r["resolution_note"] = f"{m.group().strip()} → {base_name}"
        if annex:
            r["kind"], r["target"]["annex"] = "별표", annex
        elif form:
            r["kind"], r["target"]["form"] = "별지·서식", form
        r["target"]["articles"] = refs
        spans.append((m.start(), end))
        out.append(r)

    # ③ 범위 (법령명이 앞에 없는 것) — 내부 범위다
    for m in X.RANGE_RE.finditer(line):
        if occupied(spans, m.start(), m.end()):
            continue
        r = new_rec(doc, path, region, li, m.start(), m.end(), line, art_idx)
        r["kind"] = "범위"
        a, b = X.norm_art(m.group(1)), X.norm_art(m.group(2))
        exp, why = expand_range(doc, a, b)
        r["range"] = {"from": a, "to": b, "expanded": exp or [],
                      "expand_basis": why if exp else None,
                      "expand_fail": None if exp else why}
        if exp:
            r["scope"], r["scope_basis"] = "내부", "문서조문실재"
            r["resolution"] = "범위전개"
        else:
            r["scope"], r["scope_basis"] = "미판정", "근거없음"
            r["resolution"] = "미해소"
        spans.append((m.start(), m.end()))
        out.append(r)

    # ④ 별표·별지·부칙 (대상명이 앞에 없는 것)
    for rx, kind in ((X.ANNEX_RE, "별표"), (X.FORM_RE, "별지·서식"),
                     (X.ADDENDA_RE, "부칙")):
        for m in rx.finditer(line):
            if not m.group().strip() or occupied(spans, m.start(), m.end()):
                continue
            if kind == "별표" and not m.group(1):
                continue
            refs, _a, _f, _r, end = consume_refs(line, m.end())
            # 홀로 선 `부 칙` 은 그 문서의 부칙 표목이지 부칙을 가리키는 참조가
            # 아니다. 조문 참조가 뒤따를 때만 참조로 본다 (실측 133건 중 대부분이
            # 표목 줄이다).
            if kind == "부칙" and not refs:
                continue
            r = new_rec(doc, path, region, li, m.start(), end, line, art_idx)
            r["kind"] = kind
            r["target"]["articles"] = refs
            if kind == "별표":
                r["target"]["annex"] = f"별표{m.group(1)}"
            elif kind == "별지·서식":
                r["target"]["form"] = ("별지제%s호서식" % m.group(1)) if m.group(1) else "별지"
            sa, _sb = sentence_of(sent_spans, m.start())
            head = line[sa:m.start()]
            if X.SELF_REF_RE.search(head):
                r["scope"], r["scope_basis"] = "내부", "본지침명시"
                r["resolution"] = "직접"
            elif X.LAW_TAIL_BEFORE.search(head[-45:]):
                # 어휘가 못 잡은 법령의 별표·별지·부칙이다. 문서 것으로 돌리면
                # 외부 참조가 내부로 뒤집힌다
                r["scope"], r["scope_basis"] = "미판정", "선행법령_어휘미포착"
                r["resolution"] = "미해소"
                r["resolution_note"] = (
                    "바로 앞 표기가 법령 접미사로 끝나나 어휘에 없다. "
                    "인용부호로 묶인 적 없는 법령이다")
            else:
                r["scope"], r["scope_basis"] = "내부", "선행법령없음_문서귀속"
                r["resolution"] = "직접"
            spans.append((m.start(), end))
            out.append(r)

    # ⑤ 남은 조문 참조 — 내부 조문이거나 미판정이다
    for rx in (X.ART_RE, X.CLAUSE_RE):
        for m in rx.finditer(line):
            if occupied(spans, m.start(), m.end()):
                continue
            sa, sb = sentence_of(sent_spans, m.start())
            refs, annex, form, _r, end = consume_refs(line, m.end())
            allrefs = [X.norm_art(m.group())] + refs
            r = new_rec(doc, path, region, li, m.start(), end, line, art_idx)
            r["target"]["articles"] = allrefs
            # 같은 문장에 법령이 있으나 인접하지 않은 경우는 승격하지 않는다
            far = [k for p, k, _n in laws_at if sa <= p < m.start()]
            title_hint = None
            tm = re.match(r"\s*[\(（]([^)）]{2,40})[\)）]", line[m.end():])
            if tm:
                title_hint = tm.group(1)
                end = max(end, m.end() + tm.end())
                r["surface"] = line[m.start():end]
                r["quote"] = quote_of(line, m.start(), end)
            sec = sec_hint(line, sa, m.start())
            if sec:
                # 편·장·절 한정어는 그 자체가 내부 구조 표기다
                r["scope"], r["scope_basis"] = "내부", "편장절한정"
                r["kind"], r["resolution"] = "내부조문", "직접"
                iri, n = resolve_internal_article(doc, allrefs[0], title_hint, sec)
                r["target"]["article_iri"] = iri
                r["target"]["후보수"] = n
            elif far:
                r["scope"], r["scope_basis"] = "미판정", "선행법령_비인접"
                r["kind"], r["resolution"] = "내부조문", "미해소"
                r["resolution_note"] = (
                    f"같은 문장에 {far[-1]} 표기가 있으나 인접하지 않아 "
                    "외부로 승격하지 않았다")
            else:
                scope, basis = judge_internal(line, (sa, sb), m.start(), doc,
                                              allrefs, art_idx)
                if scope == "미판정" and title_in_doc(doc, title_hint):
                    scope, basis = "내부", "문서표제일치"
                r["scope"], r["scope_basis"] = scope, basis
                r["kind"] = "내부조문"
                r["resolution"] = "직접" if scope == "내부" else "미해소"
                if basis == "현재조문내":
                    r["target"]["article_iri"] = doc["articles"][art_idx]["iri"]
                    r["target"]["후보수"] = 1
                elif scope == "내부":
                    iri, n = resolve_internal_article(doc, allrefs[0], title_hint)
                    r["target"]["article_iri"] = iri
                    r["target"]["후보수"] = n
            if annex:
                r["target"]["annex"] = annex
            if form:
                r["target"]["form"] = form
            spans.append((m.start(), end))
            out.append(r)

    # ⑥ 인용부호 없이 맨몸으로 적힌 편·장·절 — 내부 구조 참조다
    #    `제7장 옥외광고물 부분의 관련내용을 준용한다` 처럼 인용부호도 조문번호도
    #    없이 편·장·절만 적는 표기가 실측 2,362건 있다. 인용부호 경로(①)로도
    #    조문 경로(⑤)로도 잡히지 않아 통째로 빠져 있던 자리다.
    for m in X.SEC_CHAIN_RE.finditer(line):
        s, e = m.start(), m.end()
        if occupied(spans, s, e):
            continue
        # 조문 참조가 뒤따르면 그 조문의 한정어다. ⑤ 가 이미 담았다
        if X.SEC_FOLLOWED_BY_ART.match(line[e:]):
            continue
        sa, sb = sentence_of(sent_spans, s)
        # 문장 머리에 있으면 이 문서의 표목 선언이지 참조가 아니다
        if X.SENT_HEAD_ONLY.match(line[sa:s]):
            continue
        # 서술문이 아니면 목차·머리말·표 캡션이다
        if not X.PREDICATE_END.search(line[sa:sb].rstrip()):
            continue
        chain = [(lv, n) for lv, n in X.sec_chain(m.group()) if n]
        if not chain:
            continue
        r = new_rec(doc, path, region, li, s, e, line, art_idx)
        r["name_surface"] = m.group().strip()
        r["kind"] = "내부표목"
        if all(c in doc["sections"] for c in chain):
            r["scope"], r["scope_basis"] = "내부", "편장절실재"
            r["resolution"] = "직접"
            r["target"]["heading"] = X.sec_label(chain)
        else:
            r["scope"], r["scope_basis"] = "미판정", "편장절_대조불가"
            r["resolution"] = "미해소"
            r["resolution_note"] = (
                f"{X.sec_label(chain)} 을 이 문서의 편·장·절 구조와 대조하지 "
                "못했다. 표목이 헤딩으로도 평문 표목 줄로도 잡히지 않았다")
        spans.append((s, e))
        out.append(r)

    # ⑦ 준용 — 참조에 관계 표시를 붙이고, 대상 표기가 없는 준용 서술은 따로 남긴다
    for m in X.JUNYONG_RE.finditer(line):
        sa, sb = sentence_of(sent_spans, m.start())
        tied = [r for r in out
                if r["line"] == li + 1 and sa <= r["pos"] < m.start()
                and r["source_file"] == path]
        if tied:
            # 삽입 순서가 아니라 위치로 고른다. 준용 서술은 바로 앞 참조를
            # 가리키는데 out 은 판정 단계 순서라 마지막 원소가 최근접이 아니다
            max(tied, key=lambda r: r["pos"])["relation"] = "준용"
            continue
        r = new_rec(doc, path, region, li, max(sa, m.start() - 50), m.end(),
                    line, art_idx)
        r["kind"], r["relation"] = "준용", "준용"
        r["scope"], r["scope_basis"] = "미판정", "준용대상표기없음"
        r["resolution"] = "미해소"
        r["resolution_note"] = "준용 서술은 있으나 문장 안에 참조 표기가 없다"
        out.append(r)


# ── 집계 ────────────────────────────────────────────────────────────────────
IN_INDEX_RES = {"직접", "상대참조해소", "범위전개"}


def build(md_dir, statute_dir, out_dir, limit=None):
    paths = sorted(glob.glob(os.path.join(md_dir, "*", "*.md")))
    if limit:
        paths = paths[:limit]

    master = {}
    mpath = os.path.join(statute_dir, "statute_master.json")
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            for st in json.load(f)["statutes"]:
                master[st["statute_key"]] = st

    docs, doc_meta = [], []
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
        d = X.parse_document(text)
        docs.append(d)
        doc_meta.append((p, os.path.basename(os.path.dirname(p))))

    vocab, learned = learn_vocab(docs, master)
    scanner = build_scanner(vocab)

    records = []
    no_dstrc = []
    for d, (p, region) in zip(docs, doc_meta):
        if not d["지구번호"]:
            no_dstrc.append(p)
            continue
        rel = os.path.relpath(p, os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(md_dir)))))
        rel = p if rel.startswith("..") else rel
        for li in range(d["body_start"], len(d["lines"])):
            line = d["lines"][li]
            if not line.strip():
                continue
            # md 변환이 남긴 원본 표시 주석은 문서 본문이 아니다
            if X.HTML_COMMENT.match(line):
                continue
            if li in d["heading_lines"]:
                m = X.HEADING_RE.match(line.rstrip())
                # 조문 표제 헤딩은 그 조문 자신의 선언이지 참조가 아니다
                if m and X.ARTICLE_TITLE.match(m.group(2)):
                    continue
                line = m.group(2) if m else line
            scan_line(d, rel, region, li, line, d["seg"][li],
                      scanner, vocab, master, records)

    # 정렬로 순서를 고정한다 — 같은 입력에 같은 산출물이어야 한다
    records.sort(key=lambda r: (r["dstrcAppnNo"], r["source_file"], r["line"],
                                r["pos"], r["kind"] or "", r["surface"]))
    index, report = [], []
    for i, r in enumerate(records, start=1):
        r["xref_id"] = f"X{i:06d}"
        keep = (r["scope"] in ("내부", "외부")
                and r["resolution"] in IN_INDEX_RES)
        (index if keep else report).append(r)

    stat = collections.Counter
    kinds = stat(r["kind"] for r in index)
    scopes = stat(r["scope"] for r in index)
    resols = stat(r["resolution"] for r in index)
    rel_c = stat(r["relation"] for r in index)
    mstat = stat(r["target"]["master_status"] for r in index
                 if r["target"]["master_status"])
    multi = [r for r in index if (r["target"]["후보수"] or 0) > 1]
    reasons = stat((r["scope"], r["scope_basis"], r["resolution"]) for r in report)

    arts = sum(len(d["articles"]) for d in docs)
    out = {
        "meta": {
            "생성근거": "시행지침 md 조문 본문에서 외부·내부 참조를 추출",
            "스크립트": ".claude/skills/legal/legal-xref/scripts/extract_xref.py",
            "문서수": len(paths),
            "지구번호_없는_문서": len(no_dstrc),
            "조문수": arts,
            "조문헤딩_있는_문서수": sum(1 for d in docs if d["articles"]),
            "참조수": len(index),
            "격리수": len(report),
            "scope별": dict(sorted(scopes.items())),
            "kind별": dict(sorted(kinds.items())),
            "resolution별": dict(sorted(resols.items())),
            "relation별": dict(sorted(rel_c.items())),
            "법령정본_대조별": dict(sorted(mstat.items())),
            "내부조문_후보다중": len(multi),
            "어휘": {"정본_법령수": len(master), "학습_표기수": len(learned),
                     "어휘_총키수": len(vocab)},
            "경계": ("legal-statute 는 terms.json 정의문의 인용 간선이 정본이고, "
                     "legal-xref 는 지침 조문 본문의 참조가 정본이다"),
            "격리_취급": ("미해소·미판정·범위전개 실패는 xref_index 에 넣지 않고 "
                          "_xref_report.json 에만 남긴다. kb-plan 절대규칙 5"),
        },
        "xrefs": index,
    }
    rep = {
        "meta": {
            "설명": "참조 추출 이력 — 미해소·미판정·범위전개 실패·후보 다중",
            "격리수": len(report),
            "사유별": {f"{a}/{b}/{c}": n
                       for (a, b, c), n in sorted(reasons.items(),
                                                  key=lambda kv: -kv[1])},
            "후보다중수": len(multi),
            "법령정본_미수록수": sum(1 for r in index
                                     if r["target"]["master_status"] == "미수록"),
            "지구번호_없는_문서": no_dstrc,
        },
        "isolated": report,
        "후보다중": [{"xref_id": r["xref_id"], "dstrcAppnNo": r["dstrcAppnNo"],
                      "article_iri": r["article_iri"], "surface": r["surface"],
                      "후보수": r["target"]["후보수"]} for r in multi],
        "법령정본_미수록": sorted({r["target"]["statute_key"] for r in index
                                   if r["target"]["master_status"] == "미수록"}),
    }

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "xref_index.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    with open(os.path.join(out_dir, "_xref_report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return out, rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md-dir", default="output/legal/markdown")
    ap.add_argument("--statute-dir", default="output/legal/statute")
    ap.add_argument("--out-dir", default="output/legal/xref")
    ap.add_argument("--limit", type=int, default=None, help="앞 N개 문서만 (점검용)")
    a = ap.parse_args()
    if not os.path.isdir(a.md_dir):
        print(f"입력 없음: {a.md_dir}", file=sys.stderr)
        return 1
    out, rep = build(a.md_dir, a.statute_dir, a.out_dir, a.limit)
    m = out["meta"]
    print(f"문서 {m['문서수']} · 조문 {m['조문수']:,} · 참조 {m['참조수']:,}"
          f" → {a.out_dir}/xref_index.json")
    print(f"scope {m['scope별']}")
    print(f"kind  {m['kind별']}")
    print(f"resolution {m['resolution별']}")
    print(f"격리 {m['격리수']:,}건 → {a.out_dir}/_xref_report.json")
    for k, n in list(rep["meta"]["사유별"].items())[:5]:
        print(f"   {n:6,}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
