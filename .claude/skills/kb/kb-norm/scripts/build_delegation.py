"""위임 사슬 — 법률 → 시행령 → 조례 조문.

양방향 문언이 조·항 단위로 맞물릴 때만 lp:delegates 를 낸다. 한쪽만 있으면
간선을 만들지 않고 리포트에 남긴다.

    .venv/bin/python3 .claude/skills/kb/kb-norm/scripts/build_delegation.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import corpus as C
import mint_norm_iri as M
import parse_ordinance as P

sys.path.insert(0, os.path.abspath(
    os.path.join(HERE, "..", "..", "kb-ontology", "scripts")))
import mint_iri as I

OUT_TTL = os.path.join(C.ROOT, "output/kb/norm/graph/det/delegation.ttl")
OUT_REPORT = os.path.join(C.ROOT, "output/kb/norm/reports/_delegation.json")
CONTRACT = os.path.join(HERE, "..", "contract", "delegation.json")

# 상위 법령의 조례 위임 문언. 두 갈래가 서로의 사각지대를 덮으므로 합집합으로 쓴다.
#
#   주체 열거 — '조례'와 동사 사이에 괄호나 목적어가 끼는 경우를 잡는다
#     "조례(자치구의 경우에는 특별시나 광역시의 조례를 말한다. 이하 같다)로 정한다"
#     "해당 지방자치단체의 조례로 적용방법을 따로 정하는 경우"
#   위임 동사 — 주체 표현이 열거에 없는 경우를 잡는다
#     "해당 도의 조례로 정하는" · "시 또는 군의 조례로 정하는" · 수식어 없는 "조례로 정하는"
#
# 한쪽만 쓰면 실측 기준 주체 열거는 18개, 동사 기반은 7개를 놓친다. 놓치는 것 중에
# 국계법 제77·78조(건폐율·용적률)와 건축법 제43조(공개공지)·제60조(높이)가 있어
# 어느 한쪽만으로는 핵심 축이 통째로 빠진다. 합집합으로 발신 조문 133 → 151,
# 격리 1,779 → 1,359 건이 된다.
#
# 동사는 활용형을 모두 적는다. '정하' 만 적으면 '정한다'·'정할'·'정함'을 놓친다.
# '조례로' 와 동사 사이에 목적어가 들어가는 조문이 있어 문장 안에서 60자까지 허용한다 —
# 법 제37조 "…조례로 용도지구의 명칭 및 지정목적, 건축이나 그 밖의 행위의 금지 및
# 제한에 관한 사항 등을 정하여…". 마침표·줄바꿈은 넘지 않는다. 80자로 넓혀도 더
# 잡히는 것이 없어 60자로 고정한다.
#
# 동사 앞의 (?<![가-힣]) 가 없으면 다른 단어 안의 글자를 동사로 읽는다. 실측 —
# 건축법 제78조 "조례에 위반되거나 부당하다고 인정하면" 의 '인정하' 에서 '정하' 가
# 매치돼 거짓 간선 9개(건축안전특별회계 조례 조문들)가 만들어졌다. 이 lookbehind 로
# 오탐 2개(건축법 제78조·건축법시행령 제113조)만 정확히 빠지고 핵심 축은 전부 남는다.
# 법률 → 시행령 발신 근거. 이 단계의 위임 문언은 '조례' 가 아니라 '대통령령' 이다.
# 법 제36조(용도지역 세분)처럼 조문 본문에 '조례' 가 한 번도 안 나오는 축이 있어,
# 조례 문언만으로 발신을 판정하면 그 축의 법률→시행령 간선이 통째로 빠진다.
DECREE_DELEGATION = re.compile(r"대통령령으로 정하는")

DELEGATION_PATTERNS = [
    re.compile(r"도시[·ㆍ]군계획조례"),
    re.compile(r"건축조례"),
    re.compile(r"(해당 지방자치단체의 조례|지방자치단체의 조례|"
               r"시[·ㆍ]도의? 조례|시[·ㆍ]군 또는 구의 조례|"
               r"특별시나 광역시의 조례|그 지방자치단체의 조례)"),
    re.compile(r"조례(?:로|가|에서|에|는)?[^.\n]{0,60}?"
               r"(?<![가-힣])(?:정하|정한|정할|정함|정해|달리|따로|강화|완화)"),
]

# DELEGATION_PATTERNS[3](위임 동사)만 따로 참조한다. 단항 상위 조문 갈래
# (resolve_paragraph)에서만 쓴다 — 그 이유는 resolve_paragraph 의 독스트링 참조.
VERB_DELEGATION = DELEGATION_PATTERNS[3]


def _load_contract():
    with open(CONTRACT, encoding="utf-8") as f:
        return json.load(f)


def collect_senders(axes):
    """상위 법령에서 조례 위임 문언을 가진 조문을 모은다.

    계약의 위임축과 무관하게 6종 법령의 전 조문을 훑는다 — 축 목록은 어느 조문이
    사슬의 어디에 놓이는지를 정할 뿐, 발신 자격을 정하지 않는다. 계약에 적힌 조문만
    발신으로 인정하면, 위임 구조가 3단이 아닌 축(법률이 조례에 직접 위임하는 축)이
    통째로 빠진다.

    반환: {(법령명, 조문라벨): {"문언": str}}
    """
    statutes = C.statute_docs()
    senders = {}
    for name in sorted(statutes):
        for _, label, text, _ in C.articles(statutes[name]):
            phrase = None
            for pat in DELEGATION_PATTERNS:
                m = pat.search(text)
                if m and phrase is None:
                    s = max(0, m.start() - 40)
                    phrase = text[s:m.end() + 40].strip()
            if phrase:
                senders[(name, label)] = {"문언": phrase}
    return senders


# 항 본문이 각 호를 가리키는 표현. corpus 는 호 블록을 조문 끝(마지막 항)에
# 통째로 붙여 두므로, 이 표현이 있으면 그 항의 호가 어디 있는지 알 수 없다.
_REFERS_TO_ITEMS = re.compile(r"다음 각 ?호")


def para_delegates(statute_text, law, article, para):
    """상위 조문의 그 항이 조례에 위임하는가.

    "위임있음" — 항 본문에 조례 위임 문언이 있다 (간선 발급)
    "호귀속불명" — 항 본문에는 없으나 그 항이 '다음 각 호'를 가리키고 조문 어딘가에
                  위임 문언이 있다. corpus 가 호를 항에 귀속시키지 않아 판정할 수 없다
    "위임없음" — 둘 다 아니다 (단순 인용)
    "항부재" — 그 항 자체가 없다

    corpus 의 한계를 정직하게 다룬다. 건축법 제11조는 항이 0~12로 갈리는데 제2항의
    각 호("… 해당 도의 조례로 정하는 건축물은 제외한다")가 제12항 블록(2,596자)에
    들어가 있다. 항 본문만 보고 판정하면 경기도 건축 조례처럼 그 위임의 진짜 수범자를
    격리해 버린다. 상위 법령의 명시 항(①②… 표시가 있는 항) 2,057개 중 142개가
    이 구조다 — 표제부·단항 조문의 암묵적 제0항까지 포함하면 분모는 2,909개로
    늘지만 142는 그대로다(0항은 "다음 각 호"를 지목하는 경우가 없다). 이 142개
    상위 항을 지목한 실제 조례 인용은 1,427건이다(지목항_호귀속불명 실측 —
    .claude/skills/kb/kb-norm/references/common-mistakes.md 참조).
    """
    text = statute_text.get((law, article))
    if text is None:
        return "항부재"
    for no, body in P.split_paragraphs(text):
        if no != para:
            continue
        if any(p.search(body) for p in DELEGATION_PATTERNS):
            return "위임있음"
        if (_REFERS_TO_ITEMS.search(body)
                and any(p.search(text) for p in DELEGATION_PATTERNS)):
            return "호귀속불명"
        return "위임없음"
    return "항부재"


def resolve_paragraph(statute_text, upper, target, paragraph):
    """basis 의 항 번호와 상위 조문의 항 구조에서 판정에 쓸 항 번호를 정한다.

    명시된 항이 있으면 그대로 쓴다. 없고 상위 조문이 단항이면 조문 = 항이므로
    암묵적 제0항(split_paragraphs 가 항 표시 없는 조문에 매기는 번호)을 쓴다 —
    상위 조문 자체에 항이 없으면 조례는 애초에 항을 지목할 방법이 없다. 실측
    342종의 상위 조문이 단항이고, 그중 senders 를 통과한(= 조문 어딘가에 조례
    위임 문언이 있는) 조문을 항 없이 인용한 382건(조문 20종·조례 65종)이 이
    갈래로 복구된다. 대표 사례 — 건축법 제58조(대지 안의 공지) "…해당
    지방자치단체의 조례로 정하는 거리 이상을 띄워야 한다"는 조문 전체가
    단항이라, 조례가 "법 제58조에 따라"라고만 써도 그것이 정상이다.

    없고 상위 조문이 다항이면 어느 항의 위임을 받는지 정할 수 없으므로 None
    을 내 호출부가 지목항_미지정 으로 격리하게 한다. lp:delegates 는 전이추론
    대상이라 거짓이 증폭되므로 다항 조문은 항을 못 박지 못하면 간선을 만들지
    않는다.

    단항이어도 위임 동사(VERB_DELEGATION)가 없으면 None 을 낸다. 단항 조문은
    항이 하나뿐이라 조·항 대조가 조 대조로 퇴화하는데, collect_senders 의
    발신 자격은 주체 열거(DELEGATION_PATTERNS[0~2], 맨단어 '건축조례' 포함)
    만으로도 성립한다. 다항 조문에서는 항 단위 대조가 이 함정(약칭 정의·열거
    조항이 '건축조례'라는 단어만으로 발신 취급되는 것)을 걸러 왔지만, 단항은
    걸러낼 항이 없어 이번(최종 리뷰 3차)에 처음 통과했다. 실측 — 건축법
    시행령 제2조(정의 조항의 "…건축조례(이하 "법령등"이라 한다)" 약칭 정의)와
    제5조의9(민원 대상 열거 "1. 건축조례의 운영 및 집행에 관한 민원")가 이
    조건 없이는 거짓 간선 15건(11+4)을 냈다. 단항 ∩ senders 23종 중 이 조건에
    걸러지는 것은 이 2종뿐이고 나머지 21종은 전부 남는다 — 정탐을 건드리지
    않는다. 다항 조문 판정(para_delegates)에는 이 조건을 걸지 않는다 — 거기는
    항 단위 대조가 이미 이 함정을 거른다.

    상위 조문이 statute_text 에 없으면(정상 흐름에서는 senders 를 통과했으므로
    있어야 하지만) None 을 내 KeyError 대신 기존 격리로 보낸다.
    """
    if paragraph:
        return int(paragraph)
    text = statute_text.get((upper, target))
    if text is None or len(P.split_paragraphs(text)) != 1:
        return None
    if not VERB_DELEGATION.search(text):
        return None
    return 0


def collect_receivers(senders, statute_text):
    """조례 조문에서 상위 시행령을 지목한 것을 모은다.

    반환: [(조례문서, 조문라벨, 항번호, 근거dict, 지목문언)]
    격리는 두 번째 반환값으로 낸다.
    """
    rows, quarantine = [], []
    decree_by_system = {
        "도시계획조례": "국토의 계획 및 이용에 관한 법률 시행령",
        "건축조례": "건축법 시행령",
        "주차장조례": "주차장법 시행령",
    }
    act_by_system = {
        "도시계획조례": "국토의 계획 및 이용에 관한 법률",
        "건축조례": "건축법",
        "주차장조례": "주차장법",
    }
    for doc in C.ordinance_docs():
        name = doc["official_name"]
        system, basis_text = C.ordinance_system(doc)
        if not system:
            quarantine.append({"사유": "계통_미확정", "대상": name,
                               "설명": "제1·2조에서 상위 법률을 찾지 못했다"})
            continue
        lc5 = C.jurisdiction_code(doc.get("authority"))
        if not lc5:
            quarantine.append({"사유": "관할코드_미확정", "대상": name,
                               "설명": f'지자체기관명 {doc.get("authority")!r}'})
            continue
        eff = M.effective_compact(doc.get("current_effective_date"))
        if not eff:
            quarantine.append({"사유": "시행일_미확정", "대상": name,
                               "설명": f'current_effective_date {doc.get("current_effective_date")!r}'})
            continue
        # 상대참조 해소 — '영'은 이 계통의 시행령, '법'은 그 시행령의 모법이다
        source_name = {"영": decree_by_system[system],
                       "법": act_by_system[system]}
        for _, label, text, title in C.articles(doc):
            for para_no, body in P.split_paragraphs(text):
                bases = P.find_bases(body)
                if not bases:
                    continue
                for basis in bases:
                    upper = source_name[basis["source"]]
                    target = f"제{basis['number']}조"
                    if basis["branch"]:
                        target += f"의{basis['branch']}"
                    if (upper, target) not in senders:
                        quarantine.append({
                            "사유": "발신조문_미확인",
                            "대상": f"{name} {label}",
                            "설명": f"{upper} {target} 에 조례 위임 문언이 없다"})
                        continue
                    # 조문 단위 근거만으로는 부족하다. 조례가 지목한 '항'에 실제로
                    # 조례 위임 문언이 있어야 그 항이 이 조례에 위임한 것이다.
                    #
                    # 이 대조가 없으면 단순 인용이 위임으로 둔갑한다. 실측 —
                    # 서울특별시 건축 조례 제16조(안전관리 예치금)는 근거가 법 제13조인데
                    # 본문에서 "법 제11조에 따른 건축허가를 하는 때에" 라는 시점 표현으로
                    # 제11조를 언급한다. 조문 단위로만 보면 법 제11조에 조례 위임 문언이
                    # 있으므로(제2항제1호 "해당 도의 조례로 정하는 건축물은 제외한다")
                    # 간선이 생기는데, 그 위임의 수범자는 도이고 서울특별시는 도가 아니다.
                    #
                    # 규모 — 조례가 지목한 인용 5,490건 중 지목한 항에 위임 문언이 있는
                    # 것은 2,877건(52.4%)이다. 1,931건(35.2%)은 그 항에 위임 문언이
                    # 없고, 682건(12.4%)은 항을 지목하지 않는다(다항 조문을 항 없이
                    # 인용 / 단항인데 위임 동사가 없음 / 상위 조문이 statute_text 에
                    # 없음, 세 경우 전부). resolve_paragraph 가 단항 상위 조문 382건을
                    # 조문 단위 판정으로 복구했다가 위임 동사 없는 17건(최종 리뷰 3차,
                    # 건축법 시행령 제2조·제5조의9)을 다시 뺀 뒤의 수치다.
                    #
                    # lp:delegates 는 grounding.ttl:15 에서 owl:TransitiveProperty 이고
                    # det 층이라 OWL 추론 대상이다. 거짓 진술이 전이추론으로 증폭된다.
                    #
                    # 항을 못 박지 못하는 세 경우(다항 조문을 항 없이 인용 / 단항인데
                    # 위임 동사가 없음 / 상위 조문 자체가 statute_text 에 없음)만
                    # 격리한다. 상위 조문이 단항이고 위임 동사도 있으면 조문 = 항이므로
                    # resolve_paragraph 가 암묵적 제0항을 골라준다.
                    para = resolve_paragraph(statute_text, upper, target,
                                             basis["paragraph"])
                    if para is None:
                        # 사유 코드는 새로 만들지 않는다 — 둘 다 "이 인용으로는
                        # 위임을 확정할 수 없다"는 같은 판정이다. 설명 문자열로만
                        # 단항인데 위임 동사가 없어 뺀 것과 다항이라 항을 못 정한 것을
                        # 구분한다. upper_text 를 다시 조회하는 것은 resolve_paragraph
                        # 안에서 이미 한 조회를 재사용하지 못해서다 — 이 조회는 싸다
                        # (딕셔너리 조회 + 정규식 1회)
                        upper_text = statute_text.get((upper, target))
                        if (upper_text is not None
                                and len(P.split_paragraphs(upper_text)) == 1
                                and not VERB_DELEGATION.search(upper_text)):
                            설명 = (f"{upper} {target} 는 단항 조문이지만 위임 동사가 "
                                   f"없다 — 주체 열거(맨단어 '건축조례' 등) 매치만으로는 "
                                   f"위임으로 보지 않는다")
                        else:
                            설명 = (f"{upper} {target} 를 항 없이 인용해 어느 항의 "
                                   f"위임을 받는지 정할 수 없다")
                        quarantine.append({
                            "사유": "지목항_미지정",
                            "대상": f"{name} {label}",
                            "설명": 설명})
                        continue
                    verdict = para_delegates(statute_text, upper, target, para)
                    # 단항 갈래(위 para=0)는 basis["paragraph"] 가 None 이라 메시지에
                    # "제None항"이 찍히지 않도록 표시를 따로 만든다. 명시 항 인용은
                    # 원래 문구와 동일하다.
                    항자리 = f"제{basis['paragraph']}항" if basis["paragraph"] \
                        else "(단항이라 항 구분 없음)"
                    if verdict == "항부재":
                        quarantine.append({
                            "사유": "지목항_부재",
                            "대상": f"{name} {label}",
                            "설명": f'{upper} {target} 에 {항자리}이 없다'})
                        continue
                    if verdict == "호귀속불명":
                        quarantine.append({
                            "사유": "지목항_호귀속불명",
                            "대상": f"{name} {label}",
                            "설명": f'{upper} {target}{항자리} 이 각 호를 '
                                    f"가리키는데 corpus 가 호를 항에 귀속시키지 않아 "
                                    f"위임 여부를 판정할 수 없다"})
                        continue
                    if verdict == "위임없음":
                        quarantine.append({
                            "사유": "지목항_위임문언_없음",
                            "대상": f"{name} {label}",
                            "설명": f'{upper} {target}{항자리} 에 조례 '
                                    f"위임 문언이 없다 — 위임이 아니라 단순 인용이다"})
                        continue
                    rows.append({
                        "조례": name, "계통": system, "lc5": lc5, "시행일": eff,
                        "조문": label, "조문표제": title, "항": para_no,
                        "근거": basis, "지목문언": body[:120].strip(),
                        "해소근거": basis_text[:200],
                        "상위법령": upper, "상위조문": target,
                    })
    # 같은 (조례조문, 상위조문) 쌍이 여러 항에서 나오면 첫 항만 남긴다
    seen, dedup = set(), []
    for r in sorted(rows, key=lambda r: (r["lc5"], r["계통"], r["조문"],
                                         r["상위법령"], r["상위조문"], r["항"])):
        key = (r["lc5"], r["계통"], r["조문"], r["상위법령"], r["상위조문"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    return dedup, quarantine


def build_ttl(axes, senders, rows, statute_text, quarantine_axes):
    L = ["@prefix lp:   <https://w3id.org/lp/ont#> .",
         "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
         "@base <https://w3id.org/lp/id/> .", "",
         "##  위임 사슬 — 법률 → 시행령 → 조례 조문.",
         "##  양방향 문언이 맞물린 간선만 있다. 한쪽만 있으면 reports/_delegation.json 에 있다.",
         ""]

    # 법률 → 시행령. 시행령 단계가 없는 축(위임형태 법률직접)은 건너뛴다 —
    # 없는 중간 단계를 만들면 거짓 간선이 된다.
    L += ["##  법률 → 시행령", ""]
    seen_pairs = set()
    three_step = [a for a in axes if a.get("시행령조문")]
    for a in sorted(three_step, key=lambda x: (x["법률"], x["법률조문"], x["시행령조문"])):
        key = (a["법률"], a["법률조문"], a["시행령"], a["시행령조문"])
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        law_iri = M.rel(I.statute_article(a["법률"], a["법률조문"]))
        dec_iri = M.rel(I.statute_article(a["시행령"], a["시행령조문"]))
        # 시행령이 조례에 위임하는지는 법률→시행령 간선과 무관하다. 시행령이 한도만
        # 정하는 축(대지 분할·대지 안의 공지)에서 이 값이 없다고 건너뛰면, 실재하는
        # 법률→시행령 관계가 사유도 남기지 않고 사라진다 — 조용한 누락이다.
        sender = senders.get((a["시행령"], a["시행령조문"]))
        # 발신(법률)에 위임근거문언, 수신(시행령)에 수임근거문언을 붙인다.
        # 시행령은 자기 근거 법조문을 본문 앞머리에서 역인용한다 — "법 제77조제1항 및
        # 제2항에 따른 건폐율은…". 실측 13/13 전부 확인됐다. 이것을 안 붙이면
        # test_모든_간선이_양방향_근거를_갖는다 가 이 12개 간선에서만 실패한다.
        law_text = statute_text.get((a["법률"], a["법률조문"]), "")
        lm = DECREE_DELEGATION.search(law_text)
        if not lm:
            quarantine_axes.append({
                "사유": "발신조문_미확인", "대상": f'{a["법률"]} {a["법률조문"]}',
                "설명": f'{a["축"]} 축의 법률 조문에 대통령령 위임 문언이 없다'})
            continue
        law_phrase = law_text[max(0, lm.start() - 40):lm.end() + 40].strip()
        dec_text = statute_text.get((a["시행령"], a["시행령조문"]), "")
        # find_basis 는 위치를 주지 않고 raw 는 공백이 제거된 형태라 원문에서 못 찾는다.
        # 발췌 위치가 필요하므로 정규식 매치를 직접 쓴다 — raw 첫 글자('법')로 찾으면
        # 앞에 무관한 '법'이 있을 때 엉뚱한 자리를 가리킨다.
        bm = next((m for m in P.BASIS_RE.finditer(dec_text)
                   if m.group("source") == "법"), None)
        if bm is None:
            quarantine_axes.append({
                "사유": "수임조문_미확인", "대상": f'{a["시행령"]} {a["시행령조문"]}',
                "설명": f'{a["축"]} 축의 시행령 조문이 법률을 역인용하지 않는다'})
            continue
        back_phrase = dec_text[max(0, bm.start() - 10):bm.end() + 60].strip()

        L += [f"<{M.rel(I.statute(a['법률']))}> a lp:Act .",
              f"<{M.rel(I.statute(a['시행령']))}> a lp:Decree .",
              f"<{law_iri}> a lp:ArticleWork ;",
              f"    lp:inSource <{M.rel(I.statute(a['법률']))}> ;",
              f'    lp:rootStatute "{a["법률"]}" ;',
              f"    lp:delegates <{dec_iri}> ;",
              f'    lp:위임계통 "{a["계통"]}" ;',
              f'    lp:위임근거문언 {json.dumps(law_phrase, ensure_ascii=False)} .',
              "",
              f"<{dec_iri}> a lp:ArticleWork ;",
              f"    lp:inSource <{M.rel(I.statute(a['시행령']))}> ;",
              f'    lp:rootStatute "{a["법률"]}" ;',
              f'    lp:위임계통 "{a["계통"]}" ;',
              f'    lp:수임근거문언 {json.dumps(back_phrase, ensure_ascii=False)} .',
              ""]
        if sender:
            L += [f"<{dec_iri}> lp:위임근거문언 "
                  f'{json.dumps(sender["문언"], ensure_ascii=False)} .', ""]

    # 상위 조문 → 조례 조문. 상위는 시행령일 수도 법률일 수도 있다 —
    # 법률직접 축(주차장 구조·설비, 대지 분할·공지)은 법률이 바로 조례에 위임한다.
    # 위임근거문언을 여기서 부여한다. 위 axes 루프에만 두면 법률직접 축의 발신 조문이
    # 근거 문언 없이 그래프에 들어가 test_모든_간선이_양방향_근거를_갖는다 가 깨진다.
    L += ["##  상위 조문 → 조례 조문", ""]
    ord_seen, upper_seen = set(), set()
    for r in rows:
        ord_iri = M.rel(I.ordinance(r["lc5"], r["계통"]))
        art_iri = M.rel(I.ordinance_article(r["lc5"], r["계통"], r["조문"], r["시행일"]))
        dec_iri = M.rel(I.statute_article(r["상위법령"], r["상위조문"]))
        if dec_iri not in upper_seen:
            upper_seen.add(dec_iri)
            sender = senders[(r["상위법령"], r["상위조문"])]
            L += [f"<{dec_iri}> a lp:ArticleWork ;",
                  f"    lp:inSource <{M.rel(I.statute(r['상위법령']))}> ;",
                  f'    lp:위임근거문언 {json.dumps(sender["문언"], ensure_ascii=False)} .',
                  ""]
        if ord_iri not in ord_seen:
            ord_seen.add(ord_iri)
            L += [f"<{ord_iri}> a lp:Ordinance ;",
                  f'    rdfs:label "{r["조례"]}"@ko ;',
                  f'    lp:상대참조해소근거 {json.dumps(r["해소근거"], ensure_ascii=False)} .',
                  ""]
        L += [f"<{art_iri}> a lp:ArticleWork ;",
              f"    lp:inSource <{ord_iri}> ;",
              f'    rdfs:label "{r["조문표제"]}"@ko ;',
              f'    lp:수임근거문언 {json.dumps(r["지목문언"], ensure_ascii=False)} .',
              "",
              f"<{dec_iri}> lp:delegates <{art_iri}> .",
              ""]
    return L


def main():
    contract = _load_contract()
    axes = contract["위임축"]
    senders = collect_senders(axes)
    statute_text = {(n, lab): txt for n in C.statute_docs()
                    for _, lab, txt, _ in C.articles(C.statute_docs()[n])}
    rows, quarantine = collect_receivers(senders, statute_text)
    quarantine_axes = []
    lines = build_ttl(axes, senders, rows, statute_text, quarantine_axes)
    quarantine += quarantine_axes

    os.makedirs(os.path.dirname(OUT_TTL), exist_ok=True)
    os.makedirs(os.path.dirname(OUT_REPORT), exist_ok=True)
    with open(OUT_TTL, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    report = {
        "meta": {
            "생성근거": "guideline_article_corpus·guideline_t4_article_corpus 의 정본 조문 양방향 대조",
            "스크립트": ".claude/skills/kb/kb-norm/scripts/build_delegation.py",
            "원칙": "발신·수신 문언이 둘 다 있어야 간선을 낸다. 한쪽만 있으면 여기 남는다",
        },
        "발신조문수": len(senders),
        "간선수": len(rows),
        "조례수": len({r["조례"] for r in rows}),
        "격리": sorted(quarantine, key=lambda q: (q["사유"], q["대상"])),
    }
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")
    print(f"발신 {len(senders)} · 간선 {len(rows)} · 격리 {len(quarantine)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
