"""법령·조례명 정규화.

범위를 정하지 않는다. 범위는 법령 체계가 정하고(contract/law_roots.json), 이 모듈은
법령 체계에서 도출한 이름을 IRI 로 만들 때와 cited_laws 인용 문자열을 그 노드에
맞물릴 때 쓴다.

3단계 — 인용부호 제거 → 공백 제거(중점 표기 통일 포함) → 조문·별표 절단.
그것으로 안 되는 것은 case/정규화_수작업.json 의 대응표로 처리하고,
해석 불가는 IRI 를 발급하지 않고 격리한다 (kb 절대규칙 5).

알려진 한계 — fragmentStrip·ocrFix 는 정확 일치 조회다. 대응표에 없는 새 파편이나
OCR 손상은 격리되지 않고 원문 그대로 정상 법령명으로 통과한다. 실패 모드가 "격리"가
아니라 "조용한 오해석"이므로, terms.json 이 갱신되면 새 cited_laws 를 먼저 대조해
대응표를 갱신해야 한다. 갱신 없이 돌리면 없는 법령의 IRI 가 발급된다.
"""
import json
import os
import re

_CASE = os.path.join(os.path.dirname(__file__), "..", "case", "정규화_수작업.json")

QUOTE_RE = re.compile(r"[「」『』]")
CUT_RE = re.compile(r"(제\s*\d+\s*조(?:의\s*\d+)?|별표|부칙|제\s*\d+\s*장|시행일)")
TRAIL = "및의·,."

# 조문값의 유효 문법. 제N조(의M)? 뒤에 항·호·목이 붙거나, 별표N 단독.
ARTICLE_RE = re.compile(
    r"^(?:제\d+조(?:의\d+)?(?:제\d+항)?(?:제\d+호)?(?:[가-힣]목)?"
    r"|별표\d*"
    r"|부칙"
    r"|제\d+장)"
)

# 중점 변형. 같은 법령이 구분자만 달라 여러 노드로 쪼개지는 것을 막는다.
# 실측 — 신에너지및재생에너지개발·이용·보급촉진법 이 이 변형들 때문에 5종으로 갈렸다.
MIDDOT_VARIANTS = "․‧ㆍ・･"  # ․ ‧ ㆍ ・ ･
MIDDOT = "·"  # ·
_MIDDOT_TABLE = str.maketrans(MIDDOT_VARIANTS, MIDDOT * len(MIDDOT_VARIANTS))

_manual = None


def load_manual():
    global _manual
    if _manual is None:
        with open(_CASE, encoding="utf-8") as f:
            _manual = json.load(f)
    return _manual


def _squash(s):
    """인용부호를 없애고 공백을 전부 제거하며 중점 변형을 하나로 모은다."""
    s = re.sub(r"\s+", "", QUOTE_RE.sub("", s.strip()))
    return s.translate(_MIDDOT_TABLE)


def _split_article(s):
    """조문·별표 앞에서 자른다. (법령명, 조문) 을 낸다.

    입력은 _squash() 를 거쳐 공백이 없다. CUT_RE 의 \\s* 는 이 모듈 안에서는
    실행되지 않으며, 이 함수를 독립적으로 재사용할 때를 위한 여유다.

    조문값은 유효한 조문 표현의 최장 접두만 취한다. 두 인용이 붙은 문자열
    (`건축법제43조동법시행령제27조의2`)에서 첫 위치부터 끝까지 통째로 취하면
    유효하지 않은 조문 IRI 가 발급되기 때문이다. 뒤따르는 두 번째 인용은
    같은 용어의 cited_laws 에 별도 항목으로 이미 존재하는 것이 실측 관행이라
    여기서 버려도 정보 손실이 없다.
    """
    m = CUT_RE.search(s)
    if not m:
        return s, None
    head, tail = s[:m.start()], s[m.start():]
    a = ARTICLE_RE.match(tail)
    return head, (a.group(0) if a else None)


def normalize(raw):
    """인용 문자열 하나를 정규화한다.

    낸다: {"raw", "name", "article", "status", "reason"}
    status 가 "unresolved" 면 name 은 None 이고 reason 에 사유가 든다.
    """
    man = load_manual()
    out = {"raw": raw, "name": None, "article": None,
           "status": "unresolved", "reason": "unparseable"}

    if not raw or not raw.strip():
        return out

    squashed = _squash(raw)

    notlaw = set(man["notALaw"]["values"])
    if squashed in notlaw:
        out["reason"] = "not_a_law"
        return out

    name, article = _split_article(squashed)
    name = name.rstrip(TRAIL)

    if not name:
        return out

    # notALaw 는 절단 전(squashed)에서 걸러지지 않은 것을 절단 후(name)에서 한 번 더
    # 본다 — 기법·개념류 뒤에 조문이 붙어 나오면 절단 전 문자열은 대응표에 없다
    if name in notlaw:
        out["reason"] = "not_a_law"
        return out

    # 문맥 의존은 조문 절단 전후 어느 쪽으로도 걸릴 수 있다
    ctx = set(man["contextDependent"]["values"])
    if name in ctx or squashed in ctx:
        out["reason"] = "context_dependent"
        return out

    if name in set(man["unparseable"]["values"]) or squashed in set(man["unparseable"]["values"]):
        out["reason"] = "unparseable"
        return out

    name = man["fragmentStrip"]["map"].get(name, name)
    name = man["ocrFix"]["map"].get(name, name)

    out["name"] = name
    out["article"] = article
    out["status"] = "resolved"
    out["reason"] = None
    return out
