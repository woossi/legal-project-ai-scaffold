"""IRI 발급 규약. 스펙 ②·③이 이 모듈을 import 해 IRI 를 만든다.

전 개체를 자체 네임스페이스로 발급한다. 외부 식별자(law.go.kr·ELIS)는 IRI 가 아니라
lp:sourceUrl 속성으로 붙인다. 그래야 그래프가 외부 수집에 묶이지 않는다.
"""
import hashlib
import re

ONT = "https://w3id.org/lp/ont#"
ID = "https://w3id.org/lp/id/"

DSTRC_RE = re.compile(r"^[0-9]{5}[A-Z]{2}[0-9]{7}$")
TERM_RE = re.compile(r"^T[0-9]{4,}$")
LCCODE_RE = re.compile(r"^[0-9]{5}[0-9]*$")
EFFECTIVE_RE = re.compile(r"^[0-9]{8}$")
EVIDENCE_KINDS = ("text", "table", "officialDocument")

# IRI 경로에서 구분자로 오인되는 문자 + Turtle IRIREF 금지문자를 인코딩한다.
# 한글·중점(·)은 RFC 3987 의 iunreserved 에 들어가므로 그대로 둔다.
#
# Turtle 1.1 IRIREF ::= '<' ([^#x00-#x20<>"{}|^`\] | UCHAR)* '>' — 원문자 그대로는
# 제어문자(공백 포함 0x00~0x20)와 <>"{}|^`\ 를 허용하지 않는다. 이전 검수(Task 2)가
# "현재 데이터 0건, 향후 재사용 시의 구조적 갭"으로 남겨둔 지점인데, 지정근거법
# `공공주택 특별법`(law_roots.json designationRoots, 공백 포함)을 build_boundary.py 가
# statute() 에 넣으면서 실제로 걸렸다 — rdflib 가 공백 든 URIRef 를 직접 재현해 확인
# (`... does not look like a valid URI, I cannot serialize this as N3/Turtle`).
_UNSAFE_PUNCT = '/?#[]@%<>"{}|^`\\'


def _seg(s):
    """IRI 경로 한 조각을 만든다."""
    if not isinstance(s, str) or not s.strip():
        raise ValueError(f"빈 IRI 조각: {s!r}")
    # _UNSAFE_PUNCT 문자와 0x20 이하 제어문자(공백 포함)만 인코딩한다.
    # 한글·중점(·) 등 비 ASCII는 그대로 둔다.
    result = ""
    for ch in s:
        if ch in _UNSAFE_PUNCT or ord(ch) <= 0x20:
            # UTF-8 바이트로 인코딩 후 percent-encode
            result += "".join(f"%{b:02X}" for b in ch.encode('utf-8'))
        else:
            result += ch
    return result


def district(no):
    if not DSTRC_RE.match(no or ""):
        raise ValueError(f"지구번호 형식 위반: {no!r}")
    return f"{ID}district/{no}"


def guideline(no):
    if not DSTRC_RE.match(no or ""):
        raise ValueError(f"지구번호 형식 위반: {no!r}")
    return f"{ID}guideline/{no}"


def term(tid):
    if not TERM_RE.match(tid or ""):
        raise ValueError(f"용어 ID 형식 위반: {tid!r}")
    return f"{ID}term/{tid}"


def occurrence(tid, idx):
    if not TERM_RE.match(tid or ""):
        raise ValueError(f"용어 ID 형식 위반: {tid!r}")
    if not isinstance(idx, int) or idx < 0:
        raise ValueError(f"occurrence 인덱스 위반: {idx!r}")
    return f"{ID}occ/{tid}-{idx:04d}"


def gov(lccode):
    if not LCCODE_RE.match(lccode or ""):
        raise ValueError(f"lcCode 형식 위반: {lccode!r}")
    return f"{ID}gov/{lccode[:5]}"


def statute(name):
    return f"{ID}statute/{_seg(name)}"


def statute_article(name, article):
    return f"{statute(name)}/{_seg(article)}"


def article_version(name, article, effective):
    if not EFFECTIVE_RE.match(effective or ""):
        raise ValueError(f"시행일은 YYYYMMDD 여야 한다: {effective!r}")
    return f"{statute_article(name, article)}@{effective}"


##  계획항목(L1) 축. 훈령의 항은 조문이 아니다 — statute_article() 로 발급하면
##  판본(@시행일) 발급과 위임 사슬 순회(게이트 8)의 대상이 되어 조문과 섞인다.

HANG_RE = re.compile(r"^[0-9]+(-[0-9]+){1,3}$")
PLAN_ITEM_SCHEME = "https://w3id.org/lp/concept/계획항목"


def admin_rule_clause(source, hang):
    """훈령 항 노드. source 는 훈령명, hang 은 x-y[-z[-w]] 항번호다."""
    if not HANG_RE.match(hang or ""):
        raise ValueError(f"항번호 형식 위반: {hang!r}")
    return f"{ID}adminRuleClause/{_seg(source)}/{hang}"


def plan_item(notation):
    """계획항목 Concept. notation 은 절키 {장}-{절} 이다.
    SKOS 어휘라 keys.ttl 의 발급 패턴 대상이 아니다 — 패턴 정본은 계약 planItemAxis."""
    if not re.match(r"^[0-9]+-[0-9]+$", notation or ""):
        raise ValueError(f"절키 형식 위반: {notation!r}")
    return f"{PLAN_ITEM_SCHEME}/{notation}"


def plan_item_collection(chapter):
    """장 묶음. skos:Collection 이며 Concept 이 아니다 — 개념 수에 세지 않는다."""
    if not isinstance(chapter, int) or chapter < 1:
        raise ValueError(f"장번호 위반: {chapter!r}")
    return f"{PLAN_ITEM_SCHEME}/제{chapter}장"


def ordinance(lccode, kind):
    if not LCCODE_RE.match(lccode or ""):
        raise ValueError(f"lcCode 형식 위반: {lccode!r}")
    return f"{ID}ordinance/{lccode[:5]}/{_seg(kind)}"


def ordinance_article(lccode, kind, article, effective):
    if not EFFECTIVE_RE.match(effective or ""):
        raise ValueError(f"시행일은 YYYYMMDD 여야 한다: {effective!r}")
    return f"{ordinance(lccode, kind)}/{_seg(article)}@{effective}"


def application(dstrc, name, article):
    """적용 사례. 판본을 넣지 않는다 — 기준 시점이 정밀해져도 IRI 가 바뀌면 안 된다."""
    if not DSTRC_RE.match(dstrc or ""):
        raise ValueError(f"지구번호 형식 위반: {dstrc!r}")
    return f"{ID}application/{dstrc}/{_seg(name)}/{_seg(article)}"


def evidence(target_iri, kind, source_iri, locator):
    """근거 레코드. 네 입력을 NUL로 구분해 SHA-256 IRI를 발급한다."""
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"근거 종류 위반: {kind!r}")
    for name, value in (("target_iri", target_iri), ("source_iri", source_iri),
                        ("locator", locator)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name}는 비어 있지 않은 문자열이어야 한다: {value!r}")
    payload = "\x00".join((target_iri, kind, source_iri, locator)).encode("utf-8")
    return f"{ID}evidence/{hashlib.sha256(payload).hexdigest()}"


# 조문 표제 정규화. extract_norms.normalize_slot 과 같은 관례를 쓴다 —
# 공백·중점을 지운다. _seg() 에 그대로 넣으면 공백이 %20 으로 부풀어 IRI 가 읽히지 않는다.
_TITLE_STRIP = re.compile(r"[\s·․‧∙・]")


def normalize_article_title(title):
    """조문 표제를 IRI 조각으로 만든다. 원문은 rdfs:label 로 따로 남긴다."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"빈 조문 표제: {title!r}")
    out = _TITLE_STRIP.sub("", title)
    if not out:
        raise ValueError(f"정규화 후 빈 표제: {title!r}")
    return out


def guideline_article(dstrc, title, dup_index):
    """조문 노드. 표제의 39.3% 가 조번호를 갖지 않으므로 표제 전체를 키로 쓴다.
    동명순번은 같은 표제가 한 문서에서 반복될 때의 순서다 (72개 파일에서 발생)."""
    if not DSTRC_RE.match(dstrc or ""):
        raise ValueError(f"지구번호 형식 위반: {dstrc!r}")
    if not isinstance(dup_index, int) or dup_index < 1:
        raise ValueError(f"동명순번 위반: {dup_index!r}")
    return f"{ID}guidelineArticle/{dstrc}/{_seg(normalize_article_title(title))}-{dup_index:02d}"


def term_def(dstrc, tid, variant_index):
    """정의 진술. doc_definitions 레코드와 1:1 이다."""
    if not DSTRC_RE.match(dstrc or ""):
        raise ValueError(f"지구번호 형식 위반: {dstrc!r}")
    if not TERM_RE.match(tid or ""):
        raise ValueError(f"용어 ID 형식 위반: {tid!r}")
    if not isinstance(variant_index, int) or variant_index < 1:
        raise ValueError(f"variant_index 위반: {variant_index!r}")
    return f"{ID}termDef/{dstrc}/{tid}-{variant_index:02d}"


##  지구단위계획 계통 (core.ttl 계획 본체 절). 발급 근거 요건은 contract/ontology.json
##  planMinting 이 정본이다 — 요건 미달이면 부르지 않는다(인스턴스가 아니라 reports/ 격리).
##  lp:PlanningRule 은 의도적으로 발급 함수가 없다. 규정 추출 산출물 확정 전에는 열지 않는다.


def plan(no):
    """계획 본체. 지구번호는 발급 키(proxy)일 뿐 lp:District 와의 동일성 주장이 아니다."""
    if not DSTRC_RE.match(no or ""):
        raise ValueError(f"지구번호 형식 위반: {no!r}")
    return f"{ID}plan/{no}"


def plan_state(no, effective):
    """계획상태. effective 는 상태를 시작시킨 고시일이다 — 지구번호 연도 proxy 금지."""
    if not DSTRC_RE.match(no or ""):
        raise ValueError(f"지구번호 형식 위반: {no!r}")
    if not EFFECTIVE_RE.match(effective or ""):
        raise ValueError(f"고시일은 YYYYMMDD 여야 한다: {effective!r}")
    return f"{ID}planState/{no}@{effective}"


def plan_event(no, effective):
    """계획결정 사건. 성립·변경 구분은 IRI 가 아니라 rdf:type 이 담당한다."""
    if not DSTRC_RE.match(no or ""):
        raise ValueError(f"지구번호 형식 위반: {no!r}")
    if not EFFECTIVE_RE.match(effective or ""):
        raise ValueError(f"고시일은 YYYYMMDD 여야 한다: {effective!r}")
    return f"{ID}planEvent/{no}/{effective}"


def plan_doc(no, label):
    """계획문서(시행지침 외 첨부). 시행지침은 guideline() 을 쓴다 — 이중 IRI 금지.
    라벨 값 도메인은 계약 planMinting.planDoc.kindDomain 이 정본이다."""
    if not DSTRC_RE.match(no or ""):
        raise ValueError(f"지구번호 형식 위반: {no!r}")
    seg = normalize_article_title(label)
    if seg == "지구단위계획시행지침":
        raise ValueError("시행지침은 guideline() 으로 발급한다 — 이중 IRI 금지")
    return f"{ID}planDoc/{no}/{_seg(seg)}"


def plan_rule(no, subject, index, limit_kind, seq):
    """계획규정. 무시점 IRI — 시점은 planState 의 hasRule 귀속이 담당한다.
    seq 는 같은 (지구·주체·지표·한도구분) 안의 동명순번 — 발급기가 (단서원문, 표ID)
    정렬로 부여한다(멱등). 값 도메인 검사는 SHACL·게이트 14 의 몫이다.

    순번은 최소 2자리로 영채우고 넘치면 자릿수를 늘린다. 주체는 유일키가 아니라서
    한 그룹이 100행을 넘는다 — 2자리로 고정하면 실측(2026-08-20) 4그룹 24행이
    IRI 를 못 받고 사라진다. 01~99 는 이전과 같은 문자열이라 기존 IRI 는 불변이다."""
    if not DSTRC_RE.match(no or ""):
        raise ValueError(f"지구번호 형식 위반: {no!r}")
    if not isinstance(seq, int) or not (1 <= seq <= 999):
        raise ValueError(f"동명순번은 1~999 정수여야 한다: {seq!r}")
    return f"{ID}planRule/{no}/{_seg(subject)}/{_seg(index)}/{_seg(limit_kind)}-{seq:02d}"
