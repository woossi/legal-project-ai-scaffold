"""산출물 불변식 검사. 값 실재만 보는 halluc_check.py 가 못 보는 것을 본다.

회수검증(2026-08-13)이 심은 변이 M2~M5 — 비교연산 뒤집기·주어 바꿔치기·값기준
바꿔치기 — 는 값이 원문에 실재하므로 환각 검사를 통과한다. 여기서 잡는다.
"""
import collections
import glob
import json
import os
import re
import sys
import urllib.parse
from fractions import Fraction

import rdflib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))
import axis_engine as E                                    # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "..", "..", ".."))
GRAPH = os.path.join(ROOT, "output/kb/norm/graph/det/norm-value")
DELEG = os.path.join(ROOT, "output/kb/norm/graph/det/delegation.ttl")
CONTRACT = os.path.join(os.path.dirname(__file__), "..", "contract",
                        "axis_value.json")
LP = rdflib.Namespace("https://w3id.org/lp/ont#")
ID = "https://w3id.org/lp/id/"
FORBIDDEN = "https://legal-project.example/"


def _graphs():
    for path in sorted(glob.glob(os.path.join(GRAPH, "*.ttl"))):
        yield path, rdflib.Graph().parse(path, format="turtle")


def forbidden_uri_terms():
    """축별 그래프의 subject·predicate·object URIRef에서 금지 namespace를 찾는다."""
    out = []
    for path, graph in _graphs():
        for triple in graph:
            for position, term in zip(("subject", "predicate", "object"), triple):
                if (isinstance(term, rdflib.URIRef)
                        and str(term).startswith(FORBIDDEN)):
                    out.append({"파일": os.path.basename(path), "위치": position,
                                "IRI": str(term)})
    return out


def _article_parts(iri):
    """canonical 조례 조문 IRI를 (lc5, 계통, 조문, 시행일)로 분해한다."""
    value = urllib.parse.unquote(str(iri or ""))
    prefix = ID + "ordinance/"
    if not value.startswith(prefix):
        return None, None, None, None
    parts = value[len(prefix):].split("/", 2)
    if len(parts) != 3 or "@" not in parts[2]:
        return None, None, None, None
    article, effective = parts[2].rsplit("@", 1)
    return parts[0], parts[1], article, effective


def _condition_key(iri):
    value = urllib.parse.unquote(str(iri or "")).rstrip("/")
    return value.rsplit("/", 1)[-1] if value else None


def _literal(graph, subject, predicate):
    value = graph.value(subject, predicate)
    return str(value) if value is not None else None


def _nb(value):
    return re.sub(r"\s+", "", value or "")


def _value_surfaces(row):
    """정규화 값이 원문에 나타날 수 있는 표기를 낸다."""
    value, unit = row.get("값"), row.get("단위")
    if not value or not unit:
        return []
    out = {_nb(f"{value}{unit}")}
    if "." not in value:
        out.add(_nb(f"{value}.0{unit}"))
    if unit == "퍼센트":
        out.add(_nb(f"100분의{value}"))
    if unit == "배":
        try:
            fraction = Fraction(value).limit_denominator(20)
            out.add(_nb(f"{fraction.denominator}분의{fraction.numerator}"))
        except Exception:
            pass
    return sorted(out, key=len, reverse=True)


def _value_positions(row, text):
    normalized = _nb(text)
    seen = set()
    for surface in _value_surfaces(row):
        for match in re.finditer(re.escape(surface), normalized):
            key = match.start(), match.end()
            if key not in seen:
                seen.add(key)
                yield normalized, match.start(), match.end()


def _operation_near_value(row, text):
    """명제 값 표기 뒤의 연산자만 인정한다. 주어의 연산자는 대신 쓸 수 없다."""
    operator = row.get("비교연산")
    if not operator:
        return True
    return operator in _operators_near_value(row, text)


def _operators_near_value(row, text):
    """명제 값 표기 바로 뒤에서 관측된 비교연산자 집합을 낸다."""
    operators = {"이하", "이상", "미만", "초과"}
    found = set()
    tail_re = re.compile(r"^(?:\([^()]{0,200}\)|\[[^\[\]]{0,200}\])?.{0,6}")
    for normalized, _start, end in _value_positions(row, text):
        segment = tail_re.match(normalized[end:end + 220])
        if segment:
            found.update(op for op in operators if op in segment.group(0))
    return found


def _basis_immediately_before(value, basis, start):
    prefix = value[max(0, start - 20):start]
    return prefix.endswith(basis) or prefix.endswith(basis + "의")


def rows():
    for path, graph in _graphs():
        for subject in sorted(set(graph.subjects(rdflib.RDF.type, LP.NormStatement))):
            article_iri = graph.value(subject, LP.근거조문)
            lc5, system, article, effective = _article_parts(article_iri)
            absolute = urllib.parse.unquote(str(subject))
            prefix = ID + "norm/"
            iri = absolute[len(prefix):] if absolute.startswith(prefix) else absolute
            yield {
                "파일": os.path.basename(path), "iri": iri, "lc5": lc5,
                "조례": None, "조례계통": system, "조문": article,
                "시행일": effective, "근거조문IRI": str(article_iri or ""),
                "적용관할": str(graph.value(subject, LP.적용관할) or ""),
                "규범축": str(graph.value(subject, LP.규범축) or ""),
                "근거항": _literal(graph, subject, LP.추출근거항키),
                "주어키": _literal(graph, subject, LP.주어키),
                "값": _literal(graph, subject, LP.값),
                "단위": _literal(graph, subject, LP.단위),
                "값타입": _literal(graph, subject, LP.값타입),
                "값기준": _literal(graph, subject, LP.값기준),
                "조건키": _condition_key(graph.value(subject, LP.적용조건)),
                "비교연산": _literal(graph, subject, LP.비교연산),
                "미표기": _literal(graph, subject, LP.비교연산_미표기),
                "항": _literal(graph, subject, LP.근거항번호),
                "호": _literal(graph, subject, LP.근거호번호),
                "구간대상": _literal(graph, subject, LP.구간대상),
                "근거발췌": _literal(graph, subject, LP.근거발췌),
                "주어원문": _literal(graph, subject, LP.주어원문),
            }


def main():
    C = json.load(open(CONTRACT, encoding="utf-8"))
    dom = C["값도메인"]
    R = list(rows())
    fails = []

    nb = _nb
    sys.path.insert(0, os.path.join(ROOT, ".claude/skills/kb/kb-norm/scripts"))
    import corpus                                          # noqa: E402
    src, paras, ordinance_names = {}, {}, {}
    for d in corpus.ordinance_docs():
        system, _ = corpus.ordinance_system(d)
        lc5 = corpus.jurisdiction_code(d.get("authority"))
        for _, lab, txt, _t in corpus.articles(d):
            key = (lc5, system, lab)
            src[key] = nb(txt)
            ordinance_names[key] = d["official_name"]
            # 조문의 실제 항 번호 집합. 근거항번호가 이 안에 없으면 위조이거나
            # 파서가 항을 밀려 센 것이다(M7d).
            paras[key] = {
                no for no, _ in E.split_paragraphs(E.strip_markup(txt))}

    def source_key(row):
        return row["lc5"], row["조례계통"], row["조문"]

    for row in R:
        row["조례"] = ordinance_names.get(source_key(row))

    def bad(name, items):
        if items:
            fails.append((name, len(items), items[:3]))

    # 0 canonical namespace. 텍스트 prefix가 아니라 파싱된 RDF term 세 자리를 본다.
    bad("금지 네임스페이스", forbidden_uri_terms())

    # 1 비교연산 배타성
    bad("비교연산 배타성", [r for r in R if bool(r["비교연산"]) == bool(r["미표기"])])
    # 2 값 도메인
    bad("단위 도메인밖", [r for r in R if r["단위"] not in dom["단위"]])
    bad("비교연산 도메인밖",
        [r for r in R if r["비교연산"] and r["비교연산"] not in dom["비교연산"]])
    bad("값기준 도메인밖",
        [r for r in R if r["값기준"] and r["값기준"] not in dom["값기준"]])
    # 3 IRI 중복
    c = collections.Counter(r["iri"] for r in R)
    bad("IRI 중복", [k for k, v in c.items() if v > 1])
    # 4 기본값 없이 조건값만 (규범 역전)
    base = {(r["조례"], r["근거항"], r["주어키"]) for r in R if r["조건키"] == "기본"}
    bad("기본값 부재 조건값",
        [r for r in R if r["조건키"] != "기본"
         and (r["조례"], r["근거항"], r["주어키"]) not in base])
    # 5 비교연산이 근거발췌의 **명제 값 바로 뒤** 연산자와 어긋남 (M2 표적).
    # 전체 발췌 어딘가에 같은 연산자가 있는지만 보면 주어의 '10미터 이하'가
    # 값의 '1.5미터 이상' 변이를 대신 통과시킨다.
    bad("비교연산↔근거발췌 불일치",
        [r for r in R if not _operation_near_value(r, r["근거발췌"])])

    # 5b 근거발췌가 원문 조문에 실재하는가 (M3 표적).
    # 발췌 자체를 원문과 대조해야 닫힌다. 표기 차이는 halluc_check와 같은
    # 폴백(값+단위 실재)으로 흡수한다.
    def vw_ok(r):
        body = src.get(source_key(r), "")
        vw = nb(r["근거발췌"])
        if vw in body:
            return True
        if not any(True for _ in _value_positions(r, body)):
            return False
        excerpt_operators = _operators_near_value(r, r["근거발췌"])
        if not excerpt_operators:
            return True
        return excerpt_operators <= _operators_near_value(r, body)

    bad("근거발췌↔원문 불일치", [r for r in R if not vw_ok(r)])

    # 6 값이 근거발췌에서 유도되는가 (M1 표적)
    # '2분의 1' -> 0.5 처럼 분수 표기는 숫자가 다르다. 분수는 계산해서 대조한다.

    def derives(r):
        raw = (r["근거발췌"] or "").replace(" ", "")
        v = r["값"]
        if v in raw or (v + ".0") in raw:
            return True
        if "." not in v and (v + "0") in raw.replace(".", ""):
            pass
        fm = re.search(r"(\d+)분의(\d+)", raw)
        if fm:
            try:
                return abs(float(Fraction(int(fm.group(2)), int(fm.group(1))))
                           - float(v)) < 1e-9
            except Exception:
                return False
        nums = re.findall(r"\d+(?:\.\d+)?", raw)
        return any(abs(float(n) - float(v)) < 1e-9 for n in nums)

    bad("값↔근거발췌 불일치", [r for r in R if not derives(r)])
    # 6b 주어키가 주어원문에서 유도되는가 (M4 표적)
    # 용도지역군·세분은 이름이 주어원문에 그대로 있어야 하고, 구간은 하한/상한
    # 숫자가, 관계는 항·호가 맞아야 한다.
    groups = set(dom["용도지역군"]) | set(dom["용도지역군_세분"]["관측값"])

    def subj_ok(r):
        key, raw = r["주어키"] or "", (r["주어원문"] or "").replace(" ", "")
        if key in groups:
            return key in raw
        if key == dom["잔여주어키"]:
            return True
        m = re.search(r"_항(\d+)_호([\d_]+)$", key)
        if m:
            # 관계 주어. 항·호는 위치 정보라 주어원문에 없지만, **면제하면 안 된다** —
            # 실측 435건 중 88건(20%)이 이 경로로 주어키 검증을 통째로 면제받았고
            # 항·호를 위조해도 통과했다(M7c). 명제가 기록한 항·호와 대조한다.
            if r.get("항") is None or r.get("호") is None:
                return False          # 위치를 안 남기면 검증 불가 = 위반
            if not (str(r["항"]) == m.group(1)
                    and str(r["호"]).replace(".", "_") == m.group(2)):
                return False
            # **자기 정합만 보면 안 된다(M7d).** 주어키·IRI·근거항번호를 함께
            # 위조하면 셋이 서로 맞아 통과한다. 파서가 삭제 항을 안 세서 이후 항이
            # 하나씩 밀려도 세 곳이 일관되게 틀린다. 조례 원문의 실제 항 집합과
            # 대조해야 닫힌다 — M5 를 자기 정합으로 닫은 것과 정반대 구조다.
            have = paras.get(source_key(r))
            if have is None:
                return False          # 조문을 못 찾으면 검증 불가 = 위반
            return int(m.group(1)) in have
        # 구간 주어. 하한 0 은 원문에 없는 합성값이므로(원문은 '미만'만 말한다)
        # 대조 대상에서 뺀다. 나머지 경계 숫자는 주어원문에 실재해야 한다.
        # 한글 자릿수 표기(1천·1만)는 숫자와 다르므로 to_number 로 환산해 비교한다.
        nums = [n for n in re.findall(r"\d+", key) if n != "0"]
        if not nums:
            return True
        raw_nums = set()
        raw = raw.replace(" ", "")     # '1천 500' 같은 띄어쓴 자릿수 표기
        for m in re.finditer(r"[0-9][0-9,]*(?:만[0-9]*)?(?:천[0-9]*)?(?:백[0-9]*)?",
                             raw):
            v = E.to_number(m.group(0))
            if v is not None:
                raw_nums.add(int(v))
        return all(int(n) in raw_nums for n in nums)

    bad("주어키↔주어원문 불일치", [r for r in R if not subj_ok(r)])

    # 6b-2 **구간대상 낱말이 주어원문에 실재하는가.**
    # 경계 숫자만 보면 못 잡는다 — 대지면적_0_1000 은 nums 가 1000 뿐이고
    # 주어원문 '연면적이 1천제곱미터 미만' 에 1천이 있어 통과한다. 실측
    # (2026-08-14) 조경 23건이 연면적 구간을 대지면적으로 읽고도 이 검사를
    # 빠져나갔다. 값은 원문에 있고 올바른 연면적_* 명제도 따로 있어 환각·소멸
    # 검사도 통과했다.
    #
    # 다만 '면적 200제곱미터 이상 300제곱미터 미만인 대지에 건축하는 건축물'
    # 처럼 대지면적을 '면적' 으로 축약한 정당한 원문이 있다(실측 6건). 낱말이
    # 없으면 조문 원문에서 그 구간이 무엇에 걸리는지 — 앞 글자가 '연' 이면
    # 연면적의 꼬리다 — 까지 봐야 한다.
    def target_ok(r):
        tgt, so = r["구간대상"], nb(r["주어원문"])
        if not tgt:
            return True
        if tgt in so:
            return True
        body = src.get(source_key(r), "")
        i = body.find(so)
        if i < 0:
            return False
        pre = body[max(0, i - 3):i]
        if pre.endswith("연"):
            return False          # '연면적' 의 꼬리를 잘라 읽었다
        # 대지면적을 '면적' 으로 축약한 자리. **뒤가 '인 대지' 로 이어져야 한다.**
        #
        # 앞 글자만 보면 '연' 계열만 막힌다 — '바닥면적' 은 pre 가 '바닥' 이라
        # 빠져나간다(M8b). 공개공지는 바닥면적·연면적 두 단위를 함께 다루므로
        # 실제로 일어나는 형태이며, 검증자가 12건에 심었을 때 전건 미검출이었다.
        # 조경에서 '연면적' 앞 글자를 흘린 것과 같은 계열이다.
        #
        # 정탐 7건은 전부 '…인 대지에 건축하는 건축물' 로 이어지므로 이 조건으로
        # 하나도 죽지 않는다.
        if tgt != "대지면적" or not so.startswith("면적"):
            return False
        tail = body[i + len(so):i + len(so) + 6]
        return tail.startswith("인대지")

    bad("구간대상↔주어원문 불일치", [r for r in R if not target_ok(r)])

    # 6c 값기준이 근거발췌에 실재하는가 (M5 표적)
    # 값기준은 원문 조문 본문에 실재해야 한다. 발췌만 보면 '대지 면적의'처럼
    # 띄어 쓴 원문에서 거짓 경보가 날 수 있으므로 조문 본문도 대조한다.
    # 값기준은 그 값 바로 앞에 붙어야 한다 — 조문 어딘가에 있기만 하면 통과시키면
    # 값기준 바꿔치기(M5)를 못 잡는다. 근거발췌의 수치 위치를 원문에서 찾아
    # 그 앞 20자 안에 값기준이 있는지 본다.
    def basis_ok(r):
        """값기준이 근거발췌와 자기 정합인가. 정합이면 원문 대조가 필요 없다.

        **자기 정합을 먼저 본다.** 발췌가 '대지면적의 5퍼센트 이상'인데 값기준이
        '연면적' 이면 파일 안에서 이미 모순이다 — 원문을 볼 것도 없다. 실측 150건 중
        139건(93%)이 발췌에 값기준을 담고 있어 이 한 줄로 닫힌다.

        원문 창 대조로만 판정하면 못 닫는다 — 이 축의 지배적 문장형이
        '연면적이 N제곱미터인 건축물: 대지면적의 M퍼센트 이상' 이라 주어절의
        '연면적' 이 값 바로 앞 20자에 항상 들어온다. 창을 좁히는 방식으로는
        안 닫히고, 실측 미검출 40%(60/150)가 그렇게 났다.
        """
        b = nb(r["값기준"])
        # 근거발췌 안에서 **명제 값 바로 앞**의 값기준만 인정한다. 주어절의
        # '연면적'이 발췌 앞부분에 있다는 이유로 대지면적→연면적 변이를
        # 통과시키지 않는다.
        for excerpt, start, _end in _value_positions(r, r["근거발췌"]):
            if _basis_immediately_before(excerpt, b, start):
                return True
        # 근거발췌가 값기준을 생략한 경우만 원문을 본다. 항 선두가 값기준을
        # 선언하는 형태다 — 오산 제30조② '공개공지 면적은 대지면적에 대하여'.
        body = src.get(source_key(r), "")
        if re.search(re.escape(b) + r"(에대하여|에대한)", body):
            return True
        # 발췌가 값기준을 생략했으면 조문 원문에서 값 표기 바로 앞을 본다.
        for source, start, _end in _value_positions(r, body):
            if _basis_immediately_before(source, b, start):
                return True
        return False

    bad("값기준↔원문 불일치",
        [r for r in R if r["값기준"] and r["값기준"] in dom["값기준"]
         and r["단위"] == "퍼센트" and not basis_ok(r)])
    # 조건 블록도 검사한다. 조건키 '기본' 만 보면 그 블록은 값기준 검증이 아예
    # 없어 통째로 면제된다 — 실측 미검출 7건이 전부 조건 블록이었다.

    # 7 위임사슬
    have = set()
    if os.path.exists(DELEG):
        delegation = rdflib.Graph().parse(DELEG, format="turtle")
        for target in delegation.objects(None, LP.delegates):
            lc5, _system, article, _effective = _article_parts(target)
            if lc5 and article:
                have.add((lc5, article))
        bad("위임사슬 밖",
            [r for r in R if (r["lc5"], r["조문"]) not in have])

    if not R:
        for name, count, examples in fails:
            print(f"  FAIL {name}: {count}건")
            for example in examples:
                print("     ", example)
        print(f"FAIL 명제 0건 — 산출물을 못 찾았다: {GRAPH}")
        return 1
    print(f"명제 {len(R)}")
    for n, cnt, ex in fails:
        print(f"  FAIL {n}: {cnt}건")
        for e in ex:
            if isinstance(e, str):
                summary = e
            else:
                keys = ("조례", "조문", "주어키", "값", "비교연산")
                summary = {key: e[key] for key in keys if key in e} or e
            print("     ", summary)
    print("불변식 위반 없음" if not fails else f"위반 {len(fails)}종")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
