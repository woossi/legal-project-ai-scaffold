#!/usr/bin/env python3
"""legal-xref 스크립트가 공유하는 표기 정규화·문서 파싱 규약.

`build_citations.py` 의 `canon_key`·`SEP` 방식을 그대로 쓴다. 중점 표기가
원본마다 다르고(ㆍ · ‧ ․ ･ ・ ∙) 쉼표로 적은 문서도 있으므로 대조 키에서
접고 본문 매칭에서 다시 허용한다.

조문 IRI 는 kb-ontology `mint_iri.guideline_article` 과 같은 규약으로 만든다.
정본은 `.claude/skills/kb/kb-ontology/scripts/mint_iri.py` 이며 여기서는
같은 값을 재현할 뿐이다 — 조문 노드의 정본을 두 곳에 두지 않는다.

입력  없음 (라이브러리)
출력  없음 (라이브러리)
"""

import re

# ── 인용부호 ────────────────────────────────────────────────────────────────
# 실측: 「 10007 · ｢ 2702 · 『 193 · U+F0850·U+F0852 짝(사설영역) 442.
# U+F000 은 닫는 인용부호로도 쓰이고 글머리표로도 쓰여 짝을 이루지 않으므로
# 인용부호로 보지 않는다.
QUOTE_OPEN = "「『｢\U000f0850\U000f0852"
QUOTE_CLOSE = "」』｣\U000f0851\U000f0853"
QUOTE_SOFT_OPEN = "‘“"           # ‘ “ — 강조에도 쓰인다
QUOTE_SOFT_CLOSE = "’”"
ALL_QUOTES = QUOTE_OPEN + QUOTE_CLOSE + QUOTE_SOFT_OPEN + QUOTE_SOFT_CLOSE

QUOTED = re.compile(
    "[" + re.escape(QUOTE_OPEN + QUOTE_SOFT_OPEN) + "]"
    "([^" + re.escape(QUOTE_CLOSE + QUOTE_SOFT_CLOSE) + "\n]{1,60})"
    "[" + re.escape(QUOTE_CLOSE + QUOTE_SOFT_CLOSE) + "]"
)

# ── 대조 키 ─────────────────────────────────────────────────────────────────
SEP_CHARS = "\\s·‧․･・∙ㆍ,"
SEP = f"[{SEP_CHARS}]*"


def canon_key(name):
    """공백·중점 표기 차이를 접은 대조 키. build_citations.canon_key 와 같다."""
    return re.sub(f"[{SEP_CHARS}]", "", name or "")


def strip_quotes(s):
    return re.sub("[" + re.escape(ALL_QUOTES) + "]", "", s or "")


# ── 참조 표기 ───────────────────────────────────────────────────────────────
# 조문. `제 43조`(공백)·`제27조의2`·`제27조의 2` 를 모두 받는다.
ART_NO = r"제\s*\d+\s*조(?:\s*의\s*\d+)?"
CLAUSE_NO = r"제\s*\d+\s*(?:항|호|목)(?:\s*의\s*\d+)?"
ART_RE = re.compile(ART_NO)
CLAUSE_RE = re.compile(CLAUSE_NO)
# 조문 뒤에 이어붙는 항·호·목 꼬리
TAIL_RE = re.compile(rf"(?:\s*(?:{CLAUSE_NO}))+")

# 별표·별지·서식·부칙
# 여는 괄호 없이 `\s*` 로 시작하면 앞 공백까지 표기에 딸려 들어간다.
ANNEX_RE = re.compile(r"(?:[\[\(（]\s*)?별표\s*(\d+)?\s*[\]\)）]?")
# `별지` 만으로는 참조가 아니다 — `용지별지침` 이 걸린다. 호번호나 서식이 붙어야 한다.
FORM_RE = re.compile(r"별지\s*제\s*(\d+)\s*호\s*(?:서식)?|별지\s*서식")
ADDENDA_RE = re.compile(r"부\s{0,6}칙")

# 범위. `제O조부터 제O조까지` 와 `제O조 내지 제O조`
RANGE_RE = re.compile(
    rf"({ART_NO})\s*(?:부터|내지)\s*({ART_NO})\s*(?:까지)?"
)

# 상대참조. `동법 시행령`·`같은 법 시행령`·`동 조례`·`동 조`
REL_RE = re.compile(
    r"(?:동일?\s*법|같은\s*법|본\s*법)\s*(시행령|시행규칙)?"
    r"|(?:동|같은)\s*조례"
    r"|(?:동|같은)\s*(?:조|항|호)(?![가-힣])"
)

# 준용 서술. 뒤따르는 어미로 가른다 — `기준용적률` 안의 `준용` 이 걸린다
JUNYONG_RE = re.compile(r"준용(?=[하한할함])")

# 발행 주체 괄호. 접미사 판정 전에 떼고 이름에는 남긴다
# (`생태면적률 적용지침(환경부)` 이 `지침` 으로 끝나는 것으로 읽혀야 한다)
ORG_PAREN = re.compile(r"[\(（][^)）]{0,30}[\)）]\s*$")

# 어휘에 없는 법령명이 참조 바로 앞에 있는지 보는 꼬리 검사.
# `액화석유가스안전관리및사업법시행규칙 별표 3` 처럼 한 번도 인용부호로 묶인 적
# 없는 법령은 어휘에 없어 못 잡는데, 그 뒤 별표·조문을 그냥 내부로 판정하면
# 외부 참조가 내부로 뒤집힌다. 꼬리가 법령 접미사면 내부로 판정하지 않는다.
LAW_TAIL_BEFORE = re.compile(
    r"(?:[가-힣0-9()·․‧∙ㆍ]+\s?){0,6}[가-힣]{2,}"
    r"(?:법률|법|시행령|시행규칙|조례)\s*(?:의|에|에서|및)?\s*$"
)

# 이 지침을 가리키는 한정어
SELF_REF_RE = re.compile(r"(?:이|본)\s{0,3}지침|본\s{0,3}계획|지구단위계획의")
# 이 지침 자신을 가리키는 대상명
SELF_DOC = {"지구단위계획시행지침", "시행지침", "지침", "본지침", "이지침",
            "지구단위계획지침", "본시행지침"}

# 편·장·절. 아라비아 숫자와 로마 숫자가 섞여 쓰인다
SEC_NUM = "[0-9IVXⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+"
SEC_RE = re.compile(rf"제\s*({SEC_NUM})\s*(편|장|절)")
_ROMAN = {"Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5, "Ⅵ": 6, "Ⅶ": 7, "Ⅷ": 8,
          "Ⅸ": 9, "Ⅹ": 10, "I": 1, "V": 5, "X": 10}


def sec_num(s):
    """편·장·절 번호를 정수로 만든다. 로마 숫자 표기가 섞여 있다."""
    s = s.strip()
    if s.isdigit():
        return int(s)
    total = prev = 0
    for ch in reversed(s.upper()):
        v = _ROMAN.get(ch)
        if v is None:
            return None
        total += -v if v < prev else v
        prev = max(prev, v)
    return total or None


def sec_chain(title):
    """표목 문자열에서 (편, 장, 절) 을 순서대로 읽는다."""
    out = []
    for m in SEC_RE.finditer(title or ""):
        out.append((m.group(2), sec_num(m.group(1))))
    return out


# 편·장·절 연쇄. 인용부호 없이 맨몸으로 적힌 내부 구조 참조를 찾는다
SEC_CHAIN_RE = re.compile(rf"(?:제\s*{SEC_NUM}\s*(?:편|장|절)\s*)+")
# 줄 전체가 편·장·절 표목인 평문 줄. 참조가 아니라 그 문서의 표목 선언이다.
# 표목이 헤딩으로 승격되지 않은 문서가 있어 편·장·절 구조를 이것으로 보강한다.
BARE_SEC_HEAD = re.compile(
    rf"^\s*(?:제\s*{SEC_NUM}\s*(?:편|장|절)\s*[^\n]{{0,40}}?\s*)+$")
# 조문 참조가 바로 뒤따르면 편·장·절은 그 조문의 한정어다
SEC_FOLLOWED_BY_ART = re.compile(r"^[\s의와과,·]{0,4}제\s*\d+\s*(?:조|항|호|목)")
# 문장 머리 — 글머리표·번호만 있고 서술이 없다
SENT_HEAD_ONLY = re.compile(r"^[\s\-•*∙▪□○●◦※\d)．.\(\)①-⑳㉑-㉟]{0,6}$")
# 서술문의 종결형. 목차·머리말·표 캡션과 가른다
PREDICATE_END = re.compile(r"(?:다|함|음|것|바|임)\s*[.。]?\s*$")
# md 변환이 남긴 원본 표시 주석. 문서 본문이 아니다
HTML_COMMENT = re.compile(r"^\s*<!--.*-->\s*$")


def sec_label(chain):
    """(편, 장, 절) 연쇄를 정규 표기로 만든다. `제3편` · `제5편 제1장`."""
    return " ".join(f"제{n}{lv}" for lv, n in chain if n)

# 법령 접미사 — 법령명은 이것으로 끝난다 (build_citations.SUFFIX 와 같다)
LAW_SUFFIX = re.compile(
    r"(?:법률|특별법|법|시행령|시행규칙|규칙|규정|조례|훈령|예규)$"
)
# 법령이 아니면서 외부 문서임이 표기만으로 확정되는 꼬리.
DOC_SUFFIX = re.compile(
    r"(?:지침|지침서|가이드라인|매뉴얼|고시|공고|요령|체크리스트|설계기준"
    r"|인증기준|기준)$"
)
# 외부 문서일 수도, 이 지침이 속한 계획의 구성 항목일 수도 있는 꼬리.
# `가구 및 획지계획` 은 같은 지구단위계획의 구성 항목이고
# `2030 안성 도시기본계획` 은 별개의 외부 문서다. 표기만으로는 갈리지 않는다.
AMBIG_DOC_SUFFIX = re.compile(r"(?:계획|방안|협약|보고서|조서|매뉴얼)$")
# 발행 주체·연도가 붙으면 별개 문서다. 실측 표기에서 추린다.
ORG_PREFIX = re.compile(
    r"^(?:\d{4}\s*년?\s*)"
    r"|^[가-힣]{2,10}(?:특별자치시|특별자치도|특별시|광역시|시|군|구|도)\s"
    r"|[\(（](?:[^)）]{0,20}(?:부|청|처|위원회|공사|공단|도|시|군|구|연구원)"
    r"[^)）]{0,20})[\)）]"
)
# `법` 으로 끝나도 법령이 아닌 꼬리 (build_citations.NOT_LAW_TAIL)
NOT_LAW_TAIL = re.compile(r"(?:기법|공법|수법|용법|기준법|치법)$")
NOT_LAW = {"관계법령", "관련법령", "동법", "같은법", "본법", "법", "조례",
           "관계법", "관련법", "해당법령", "기타법령"}
# 문맥 의존 참조는 법령명이 아니다. `「동법 시행령」` 처럼 인용부호로 묶여 있어도
# 어휘로 학습하면 `동법시행령` 이 법령 노드가 된다(실측 278건이 그렇게 올라왔다).
# kb-ontology 규칙 "해석 불가는 IRI 를 발급하지 않는다" 와 같다.
REL_HEAD = re.compile(r"^(?:동일?|같은|본|해당|당해)\s*(?:법|조례|조|항|호|규정)")


# ── md 문서 파싱 ────────────────────────────────────────────────────────────
HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
# 조문 표제. kb-ontology build_guideline_tree.ARTICLE_TITLE 과 같은 판정이다 —
# 괄호를 무조건 표제로 보면 `…높이(3층이상)` 같은 부가설명이 조문으로 섞인다.
ARTICLE_TITLE = re.compile(
    r"^제\s*(\d+)\s*조(?:\s*의\s?(\d+))?\s*[\(（]\s*([^)）]{2,40}?)\s*[\)）]"
)
DSTRC_FM = re.compile(r'^지구번호:\s*"?([0-9]{5}[A-Z]{2}[0-9]{7})"?\s*$', re.M)
NAME_FM = re.compile(r'^지구명:\s*"?(.+?)"?\s*$', re.M)

_TITLE_STRIP = re.compile(r"[\s·․‧∙・]")
_UNSAFE_PUNCT = '/?#[]@%<>"{}|^`\\'


def _seg(s):
    """IRI 경로 한 조각. mint_iri._seg 와 같은 규약이다."""
    out = ""
    for ch in s:
        if ch in _UNSAFE_PUNCT or ord(ch) <= 0x20:
            out += "".join(f"%{b:02X}" for b in ch.encode("utf-8"))
        else:
            out += ch
    return out


def article_iri(dstrc, title, dup_index):
    """조문 노드 IRI(@base 상대형). mint_iri.guideline_article 과 같은 값이다."""
    return (f"guidelineArticle/{dstrc}/"
            f"{_seg(_TITLE_STRIP.sub('', title))}-{dup_index:02d}")


def art_key(no, sub=None):
    """조문번호 대조 키. `제27조의2` → `27-2`, `제 43 조` → `43-0`."""
    return f"{int(no)}-{int(sub) if sub else 0}"


def norm_art(surface):
    """조문 표기의 공백을 접는다. `제 43 조` → `제43조`."""
    return re.sub(r"\s+", "", surface or "")


def parse_document(text):
    """md 한 건을 조문 구간과 표목 사전으로 가른다.

    반환 dict:
      지구번호 · 지구명
      articles  조문 목록 [{조번호키, 표제, 표제원문, 동명순번, iri, 시작줄, 끝줄}]
      by_artno  조번호키 → 조문 인덱스 목록 (동일 조번호 다중 후보 판정용)
      headings  표목 대조 키 집합 (편·장·절 접두 제거형 포함)
      sections  이 문서에 실재하는 (편|장|절, 번호) 집합
      sec_head_lines  줄 전체가 편·장·절 표목인 평문 줄 번호(0-base)
      lines     줄 목록
      seg       줄번호(0-base) → 조문 인덱스 또는 None(문서레벨)
    """
    lines = text.split("\n")
    dm = DSTRC_FM.search(text)
    nm = NAME_FM.search(text)

    # frontmatter 구간은 본문이 아니다
    body_start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, min(len(lines), 60)):
            if lines[i].strip() == "---":
                body_start = i + 1
                break

    articles, headings = [], set()
    heading_lines = set()
    dup = {}
    cur = {"편": None, "장": None, "절": None}
    for i in range(body_start, len(lines)):
        m = HEADING_RE.match(lines[i].rstrip())
        if not m:
            continue
        heading_lines.add(i)
        title = m.group(2)
        am = ARTICLE_TITLE.match(title)
        if len(m.group(1)) == 4 and am:
            k = art_key(am.group(1), am.group(2))
            dup[title] = dup.get(title, 0) + 1
            articles.append({
                "조번호키": k,
                "조번호": f"제{int(am.group(1))}조"
                          + (f"의{int(am.group(2))}" if am.group(2) else ""),
                "표제": am.group(3),
                "표제원문": title,
                "동명순번": dup[title],
                "편": cur["편"], "장": cur["장"], "절": cur["절"],
                "시작줄": i,
                "끝줄": len(lines),
            })
        else:
            headings.add(canon_key(title))
            # 편·장·절 접두를 뗀 형태도 표목으로 받는다
            bare = re.sub(r"^(?:제\s*[0-9IVXⅠ-Ⅹ]+\s*(?:편|장|절)\s*)+", "", title)
            if bare and bare != title:
                headings.add(canon_key(bare))
            # 조문이 어느 편·장·절에 속하는지는 헤딩 순서로 정한다
            for lv, n in sec_chain(title):
                cur[lv] = n
                if lv == "편":
                    cur["장"] = cur["절"] = None
                elif lv == "장":
                    cur["절"] = None

    for a, b in zip(articles, articles[1:]):
        a["끝줄"] = b["시작줄"]

    dstrc = dm.group(1) if dm else None
    for a in articles:
        a["iri"] = article_iri(dstrc, a["표제원문"], a["동명순번"]) if dstrc else None
        headings.add(canon_key(a["표제"]))
        headings.add(canon_key(a["표제원문"]))

    # 이 문서에 실재하는 편·장·절. 헤딩에서 먼저 세우고, 줄 전체가 편·장·절
    # 표목인 평문 줄로 보강한다 — 표목이 헤딩으로 승격되지 않은 문서가 있어
    # 헤딩만으로는 대조 모수가 모자란다(실측 대조불가 970 → 746).
    sections = set()
    sec_head_lines = set()
    for i in range(body_start, len(lines)):
        raw = lines[i]
        if not raw.strip():
            continue
        if i in heading_lines:
            m = HEADING_RE.match(raw.rstrip())
            for lv, n in sec_chain(m.group(2) if m else raw):
                if n:
                    sections.add((lv, n))
            continue
        if len(raw) < 70 and BARE_SEC_HEAD.match(raw):
            sec_head_lines.add(i)
            for lv, n in sec_chain(raw):
                if n:
                    sections.add((lv, n))

    seg = [None] * len(lines)
    for idx, a in enumerate(articles):
        for i in range(a["시작줄"], a["끝줄"]):
            seg[i] = idx

    by_artno = {}
    for idx, a in enumerate(articles):
        by_artno.setdefault(a["조번호키"], []).append(idx)

    return {
        "지구번호": dstrc,
        "지구명": nm.group(1) if nm else None,
        "lines": lines,
        "body_start": body_start,
        "heading_lines": heading_lines,
        "articles": articles,
        "by_artno": by_artno,
        "headings": headings,
        "sections": sections,
        "sec_head_lines": sec_head_lines,
        "seg": seg,
    }
