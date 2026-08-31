"""축별 규범값 산출. axis_spec.json 을 읽어 축을 돌린다 — 축을 더할 때 이 코드를 고치지 않는다.

산출물
    output/kb/norm/graph/det/norm-value/{슬러그}.ttl     값이 1건 이상인 축만
    output/kb/norm/reports/_norm_value/{슬러그}.json     축별 격리
    output/kb/norm/reports/_axis_coverage.json           축 14개 전부 + 값없음사유 + 분모 3종

기존 norm-value.ttl(건폐율·용적률)은 건드리지 않는다. kb-norm 이 그 정본이다.
"""
import argparse
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
CONTRACT = os.path.join(HERE, "..", "contract")

sys.path.insert(0, HERE)


def _kb_norm_scripts():
    """kb-norm/scripts 를 찾는다. corpus·parse_ordinance 를 여기서 다시 쓰지 않는다.

    kb-norm 은 아직 norm-delegation 워크트리에 있고 병합 전이다. 병합되면 첫 후보가
    맞고, 그 전에는 두 번째가 맞는다. 복제하지 않는 이유는 조례 계통 확정과 근거
    파싱이 kb-norm 의 정본이기 때문이다 — 두 곳이 같은 값을 정하면 반드시 어긋난다.
    """
    cands = [
        os.path.join(HERE, "..", "..", "kb-norm", "scripts"),
        os.path.join(ROOT, "..", "norm-delegation", ".claude", "skills", "kb",
                     "kb-norm", "scripts"),
    ]
    for c in cands:
        c = os.path.abspath(c)
        if os.path.exists(os.path.join(c, "corpus.py")):
            return c
    sys.exit("kb-norm/scripts 를 찾지 못했다. 병합 전이면 norm-delegation 워크트리가 있어야 한다.\n"
             "찾은 자리: " + " / ".join(os.path.abspath(c) for c in cands))


sys.path.insert(0, _kb_norm_scripts())
import axis_engine as E                                    # noqa: E402
import corpus                                              # noqa: E402

KB_ONTOLOGY_SCRIPTS = os.path.abspath(
    os.path.join(HERE, "..", "..", "kb-ontology", "scripts"))
sys.path.insert(0, KB_ONTOLOGY_SCRIPTS)
import mint_iri as M                                       # noqa: E402

OUT_GRAPH = "output/kb/norm/graph/det/norm-value"
OUT_REPORT = "output/kb/norm/reports/_norm_value"
OUT_COVERAGE = "output/kb/norm/reports/_axis_coverage.json"
DELEGATION = "output/kb/norm/graph/det/delegation.ttl"

# 조례 본문의 별표 참조. '별표 7과 같다' · '별표 4에 따른다'
#
# 「…법 시행령」 별표 1 처럼 **다른 법령의 별표**를 가리키는 인용은 제외한다. 이걸
# 세면 조경 축에서 '「체육시설의 설치·이용에 관한 법률 시행령」 별표 1에 따른 골프장'
# 이 조례 별표 결손으로 잡혀, 별표결손_corpus 사유의 근거가 통째로 무너진다.
# 실측: 이 필터 없이 조경 36건·공개공지 23건이 잡혔는데 상당수가 타법령 별표였다.
BYLAW_REF_RE = re.compile(r"별표\s*(\d+)")
# 「…」 로 닫힌 법령명이 별표 앞 25자 안에 있으면 타법령 별표다
_OTHER_ACT_BEFORE = re.compile(r"」\s*[^」]{0,25}$")


def load(name):
    with open(os.path.join(CONTRACT, name), encoding="utf-8") as f:
        return json.load(f)


# ── 근거항 매칭 ───────────────────────────────────────────────────────────

def parse_basis_key(key):
    """'영27의2-2' → ('영','27','2','2'). '법57-1' → ('법','57',None,'1')."""
    m = re.fullmatch(r"(영|법)(\d+)(?:의(\d+))?-(\d+)", key)
    if not m:
        raise ValueError(f"근거항 키 형식 위반: {key!r}")
    return m.group(1), m.group(2), m.group(3), m.group(4)


def basis_matches(basis, unit_key):
    """corpus 에서 뽑은 근거가 추출단위의 근거항과 같은지."""
    src, num, branch, para = parse_basis_key(unit_key)
    return (basis["source"] == src
            and basis["number"] == num
            and (basis["branch"] or None) == branch
            and basis["paragraph"] == para)


def find_paragraph_basis(para_text):
    """항 본문 앞부분의 근거를 찾는다. kb-norm find_bases 를 그대로 쓴다."""
    import parse_ordinance as P
    return P.find_bases(para_text)


# ── 위임 사슬 대조 ────────────────────────────────────────────────────────

def load_delegated_articles():
    """delegation.ttl 에서 lp:delegates 의 (조례IRI) 집합. 파일이 없으면 None.

    None 은 '위임 사슬 대조 미검사'다 — 실패가 아니다. delegation.ttl 은 아직
    병합 전 워크트리에 있어 이 워크트리에서는 없을 수 있다. 없다고 명제를 다
    격리하면 축 전체가 0건이 되어 별표결손 같은 진짜 사유가 묻힌다.
    """
    import urllib.parse
    path = os.path.join(ROOT, DELEGATION)
    if not os.path.exists(path):
        return None
    # 실측 형태: ordinance/{lc5}/{계통}/{조문}[@{시행일}]
    pat = re.compile(r"^ordinance/(\d{5})/[^/]+/(제[^/@]+)(?:@\d+)?$")
    out = set()
    with open(path, encoding="utf-8") as f:
        for m in re.finditer(r"<([^>]*)>\s+lp:delegates\s+<([^>]*)>", f.read()):
            got = pat.match(urllib.parse.unquote(m.group(2)))
            if got:
                out.add((got.group(1), got.group(2)))
    return out


# ── 별표 결손 확인 ────────────────────────────────────────────────────────

def _axis_paragraphs(text, axis):
    """이 축의 근거 조문을 인용하는 항만 낸다.

    조문 전체를 훑으면 같은 조문의 다른 항이 가리키는 무관한 별표가 잡힌다 —
    필터 없이는 조경·공개공지·높이 세 축이 전부 '공개공지 안내판 별표 5' 라는
    같은 발췌를 근거로 들었다. 별표결손 주장의 근거가 되려면 발췌가 그 축의
    근거항에서 나와야 한다.
    """
    out = []
    for _, para_text in E.split_paragraphs(E.strip_markup(text)):
        if _cites_axis(para_text, axis):
            out.append(para_text)
    return out


def check_bylaw_gap(doc, article_text):
    """본문이 별표를 가리키는데 corpus 에 그 별표가 없는지 확인한다.

    사람이 '별표가 없다'고 적는 것이 아니라 코드가 확인해 근거 발췌를 남긴다.
    corpus 의 provision 은 article_status 가 Y/N 뿐이고 별표 provision 이 없다 —
    그래도 '없음'을 가정하지 않고 실제로 찾아본 뒤 없음을 기록한다.
    """
    text = article_text or ""
    own = [m for m in BYLAW_REF_RE.finditer(text)
           if not _OTHER_ACT_BEFORE.search(text[max(0, m.start() - 40):m.start()])]
    if not own:
        return None
    titles = " ".join((p.get("article_title") or "") for p in doc.get("provisions") or [])
    missing = [m for m in own
               if not re.search(r"별표\s*%s\b" % re.escape(m.group(1)), titles)]
    if not missing:
        return None
    m0 = missing[0]
    s = max(0, m0.start() - 60)
    return {"별표번호": sorted({m.group(1) for m in missing}),
            "근거발췌": text[s:m0.end() + 40].strip()}


# ── 축 하나 처리 ──────────────────────────────────────────────────────────

def run_axis(axis, docs_by_system, delegated):
    """축 하나의 (명제, 격리, 별표결손) 을 낸다."""
    units = axis.get("추출단위") or []
    system = axis["계통"]
    statements, isolated, bylaw_gaps = [], [], []
    if not units:
        # 값 대상이 아닌 축도 별표 결손은 확인한다 — 사유를 코드로 뒷받침하기 위해서다
        for doc in docs_by_system.get(system, []):
            for _, label, text, _ in corpus.articles(doc):
                if not _cites_axis(text, axis):
                    continue
                for pt in _axis_paragraphs(text, axis):
                    gap = check_bylaw_gap(doc, pt)
                    if gap:
                        bylaw_gaps.append(dict(gap, 조례=doc["official_name"],
                                               조문=label))
        return statements, isolated, bylaw_gaps

    for doc in docs_by_system.get(system, []):
        lc5 = corpus.jurisdiction_code(doc.get("authority"))
        for _, label, raw_text, title in corpus.articles(doc):
            if not _cites_axis(raw_text, axis):
                continue
            gap = None
            for pt in _axis_paragraphs(raw_text, axis):
                g = check_bylaw_gap(doc, pt)
                if g:
                    gap = gap or g
                    bylaw_gaps.append(dict(g, 조례=doc["official_name"], 조문=label))
            text = E.strip_markup(raw_text)
            for para_no, para_text in E.split_paragraphs(text):
                bases = find_paragraph_basis(para_text)
                for unit in _pick_units(units, bases):
                    # 항 선두(첫 호 앞) — 호가 생략한 구간대상 낱말이 여기 있다
                    head = para_text.split("1.")[0][:200]
                    ctx = {"조례": doc["official_name"], "lc5": lc5, "조문": label,
                           "조례계통": system,
                           "시행일": doc.get("current_effective_date"),
                           "조문원문": raw_text,
                           "조문표제": title, "항": para_no, "항선두": head,
                           "근거항": unit["근거항"], "값타입": unit["값타입"]}
                    if lc5 is None:
                        isolated.append(dict(ctx, 사유="관할코드_미확정"))
                        continue
                    if not doc.get("current_effective_date"):
                        isolated.append(dict(ctx, 사유="시행일_미확정"))
                        continue
                    _run_unit(unit, para_text, ctx, gap, statements, isolated)

    _mark_chain(statements, isolated, delegated, axis)
    _dedupe(statements, isolated, axis)
    _drop_crossproduct_noise(statements, isolated)
    _drop_orphan_conditions(statements, isolated)
    return statements, isolated, bylaw_gaps


def _drop_orphan_conditions(statements, isolated):
    """기본값이 없이 조건값만 남은 명제를 격리한다.

    `_dedupe` 가 한 자리의 기본값들을 충돌로 격리하면 같은 자리의 괄호 예외값만
    살아남는다. 그러면 그래프가 예외를 기준값으로 말한다 — 실측(2026-08-13
    회수검증) 동두천 인동간격이 0.5배(도시형 생활주택 예외)로 실렸는데 조례
    본문은 0.8배·1배다. 값 날조는 아니지만 소비처가 조례와 반대되는 기준을 읽는다.

    **격리가 예외를 이기는 것보다 낫다.** 기본값을 판정할 수 없으면 그 자리의
    조건값도 내지 않는다.
    """
    base = {(s["조례"], s["근거항"], s["주어키"]) for s in statements
            if s["조건키"] == "기본"}
    keep = []
    for s in statements:
        k = (s["조례"], s["근거항"], s["주어키"])
        if s["조건키"] != "기본" and k not in base:
            isolated.append({
                "조례": s["조례"], "조문": s["조문"], "항": s["항"], "호": s["호"],
                "근거항": s["근거항"], "값타입": s["값타입"], "주어키": s["주어키"],
                "값": s["값"], "값원문": s["값원문"], "조건원문": s.get("조건원문"),
                "사유": "기본값_부재_조건값"})
        else:
            keep.append(s)
    statements[:] = keep


def _drop_crossproduct_noise(statements, isolated):
    """다른 값타입 단위가 같은 자리에서 성공했으면 그 격리는 잡음이다.

    한 근거항에 값타입이 둘이면(_pick_units) 호1의 거리값에 배율 단위가, 호2의
    배율값에 거리 단위가 헛돈다. 값 손실은 없지만 격리 총계를 두 배로 부풀려
    진짜 누락을 가린다 — 실측 일조 영86-1 에서 60건이 이 잡음이었다.

    값을 실제로 못 만든 사유(값_파싱실패)만 대상이다. 값을 파싱해 놓고 버린
    사유는 심각도가 달라 지우지 않는다.
    """
    done = {(s["lc5"], s["근거항"], s["항"], s["호"]) for s in statements}
    keep = []
    for x in isolated:
        slot = (x.get("lc5"), x.get("근거항"), x.get("항"), x.get("호"))
        if x.get("사유") == "값_파싱실패" and slot in done:
            continue
        # 값을 파싱해 놓고 버린 사유도, **그 자리의 값이 이미 다른 구간대상
        # 단위로 명제가 됐다면** 잡음이다. 한 근거항에 구간대상이 둘 이상이면
        # (공개공지 영27의2-2 의 연면적·바닥면적) 맞지 않는 단위가 같은 호를
        # 훑고 주어_미상 을 낸다 — 실측 43건. 값 손실은 없고 총계만 부푼다.
        #
        # 자리가 같기만 하면 지우는 것이 아니라 **같은 발췌에서 값이 실제로
        # 나왔을 때만** 지운다. 그러지 않으면 진짜 누락이 함께 묻힌다.
        if (x.get("사유") in ("주어_미상", "별표참조_값없음")
                and slot in done
                and any(s["값원문"] and s["값원문"] in (x.get("발췌") or "")
                        for s in statements
                        if (s["lc5"], s["근거항"], s["항"], s["호"]) == slot)):
            continue
        keep.append(x)
    isolated[:] = keep


def _dedupe(statements, isolated, axis):
    """같은 IRI 키에 값이 둘 이상이면 전부 격리한다.

    조용히 뒤 값으로 덮으면 값 손실이 기록 없이 사라진다. 계약의 교차제약이
    '하나뿐' 이므로 충돌은 판정 불가이지 선택 대상이 아니다 — 어느 쪽이 맞는지
    파서가 정할 수 없다.
    """
    groups = collections.defaultdict(list)
    for s in statements:
        groups[(s["lc5"], s["근거항"], s["주어키"], s["값타입"],
                s["조건키"])].append(s)
    keep = []
    for key, rows in groups.items():
        distinct = {(r["값"], r["단위"]) for r in rows}
        if len(rows) == 1:
            keep.append(rows[0])
        elif len(distinct) == 1:
            keep.append(rows[0])            # 같은 값 재발행. 하나만 남긴다
        else:
            for r in rows:
                isolated.append({
                    "조례": r["조례"], "조문": r["조문"], "항": r["항"], "호": r["호"],
                    "근거항": r["근거항"], "주어키": r["주어키"],
                    "값타입": r["값타입"], "값": r["값"], "값원문": r["값원문"],
                    "사유": "중복명제_충돌"})
    statements[:] = keep


def _cites_axis(text, axis):
    """조문이 이 축의 근거 조문을 인용하는가."""
    for unit in (axis.get("추출단위") or []):
        src, num, branch, _ = parse_basis_key(unit["근거항"])
        pat = r"%s\s*제%s조%s" % (src, num, (r"의\s*%s" % branch) if branch else r"(?!의)")
        if re.search(pat, text or ""):
            return True
    if not axis.get("추출단위"):
        for key, art in (("영", axis.get("시행령조문")), ("법", axis.get("법률조문"))):
            if not art:
                continue
            m = re.fullmatch(r"제(\d+)조(?:의(\d+))?", art)
            if m and re.search(r"%s\s*제%s조%s" % (
                    key, m.group(1),
                    (r"의\s*%s" % m.group(2)) if m.group(2) else r"(?!의)"), text or ""):
                return True
    return False


def _pick_units(units, bases):
    """항의 근거와 맞는 추출단위 **전부**. 항 번호가 없으면 빈 목록 — 조 단위로 구제하지 않는다.

    한 축에서 항마다 값타입이 갈리므로(공개공지 제1항 용도목록 · 제2항 비율)
    조 단위 판정으로 구제하면 용도목록 항의 열거가 값으로 들어간다.

    **한 근거항에 추출단위가 둘 이상일 수 있다.** 일조 영86-1 은 같은 항에서
    '1.5미터'(거리)와 '높이의 2분의 1'(배율)이 호마다 갈려 계약이 단위를 둘 선언한다.
    첫 단위만 돌리면 배율 단위가 영원히 안 돌아 '2분의 1' 값이 통째로 사라진다 —
    실측 30건이 값_파싱실패로 격리돼 있었다.
    """
    out = []
    for unit in units:
        if any(basis_matches(b, unit["근거항"]) for b in bases):
            out.append(unit)
    return out


def _run_unit(unit, para_text, ctx, gap, statements, isolated):
    """추출단위 하나를 항 본문에 적용한다."""
    vtype = unit["값타입"]
    if vtype == "없음":
        return                                  # 값 없는 항이다. 격리 사유가 아니다
    items = E.split_items(para_text)
    if not items:
        # 호가 없는 항. 항 본문 자체가 주어와 값을 갖는 경우가 있다 —
        # 일조 축의 '다세대주택의 경우 … 수평거리는 2미터 이상으로 한다' 가 그렇다.
        # 호가 없다고 바로 격리하면 이 형태가 통째로 사라진다.
        items = [(0, para_text)]
    for no, body in items:
        base_text, cond_texts = E.split_conditions(body, vtype)
        stype = unit["주어타입"]
        if stype in ("수치구간", "관계구간"):
            _run_range_unit(unit, base_text, cond_texts, body, no, ctx, gap,
                            statements, isolated)
            continue
        subj = E.match_subject(base_text, stype, unit)
        vals = E.match_values(base_text, vtype)
        if stype == "관계":
            # 구간이 없어 주어를 구간으로 가를 수 없으므로 조례의 항·호로 가른다.
            # **항을 빼면 서로 다른 항이 같은 키로 충돌하고, 그 충돌에서 괄호
            # 예외값이 기본값을 이긴다** — 실측 동두천 제32조 ③(0.8배)·④(1배)가
            # 둘 다 _호0 이 되어 도시형 생활주택 예외 0.5배만 남았다. 규범 역전 4건.
            if not vals:
                continue                        # 값 없는 호는 이 단위 대상이 아니다
            subj = ("%s_항%s_호%s" % (subj[0], ctx["항"], _no_key(no)),
                    subj[1], subj[2])
        if subj is None and not vals:
            continue                            # 이 호는 이 단위의 대상이 아니다
        if subj is None:
            if gap:
                isolated.append(dict(ctx, 호=no, 사유="별표참조_값없음",
                                     별표=gap["별표번호"], 발췌=body[:120]))
            else:
                isolated.append(dict(ctx, 호=no, 사유="주어_미상", 발췌=body[:120]))
            continue
        if not vals:
            isolated.append(dict(ctx, 호=no, 사유="값_파싱실패", 갈래="값_없음",
                                 주어=subj[0], 발췌=body[:120]))
            continue
        key, subj_raw, extra = subj
        for v in vals:
            _emit(statements, isolated, ctx, no, key, subj_raw, extra, v, unit,
                  "기본", None, source_excerpt(ctx["조문원문"], body))
        for ci, ct in enumerate(cond_texts, 1):
            for v in E.match_values(ct, vtype):
                _emit(statements, isolated, ctx, no, key, subj_raw, extra, v, unit,
                      "조건%d" % ci, ct, source_excerpt(ctx["조문원문"], body))


def _no_key(no):
    """호 번호를 IRI 안전 문자열로. 목 분할로 2.1 같은 소수가 온다."""
    return str(no).replace(".", "_")


def _run_range_unit(unit, base_text, cond_texts, body, no, ctx, gap,
                    statements, isolated):
    """구간 주어 단위. 구간마다 자기 값만 갖고, 주어 구간은 값 탐색에서 뺀다.

    두 결함을 함께 막는다.
      (1) 주어의 임계값이 값으로 재발행되는 것 — 주어 구간을 마스킹한다
      (2) 한 항의 둘째 구간 값이 첫째를 덮는 것 — 구간마다 자기 뒤 구간만 본다
    """
    vtype = unit["값타입"]
    ranges = E.iter_ranges(base_text, unit)
    if not ranges and ctx.get("항선두") and unit.get("구간대상"):
        # 호가 구간대상 낱말을 생략하고 항 선두가 대신 선언하는 원문이 있다 —
        # 군포시 건축 조례 제30조① '…기준은 연면적의 합계로 산정하며 다음 각 호와
        # 같다. 1. 2천제곱미터 이상인 건축물 : …'. 항 선두에 구간대상이 있을 때만
        # 호에 그 낱말을 빌려준다. 항 선두에도 없으면 빌려주지 않는다 — 없는
        # 주어를 지어내면 값 날조와 같은 종류의 잘못이 된다.
        # 빌려주기 전에 그 호가 정말 **면적** 구간을 말하는지 본다. 항 선두에
        # '연면적' 이 있다고 아무 호에나 빌려주면 산정방법 조항이 연면적 구간으로
        # 둔갑한다 — 실측 의정부 제29조 '높이가 2미터 이상 … 100분의 50' 이
        # `연면적_2_이상 값 50` 이 됐다. 원문에 없는 주어를 지어낸 것이다.
        if (unit["구간대상"] in ctx["항선두"]
                and unit["구간대상"] in ("연면적", "바닥면적")
                and re.search(r"제곱미터", base_text)):
            # 빌린 낱말을 앞에 붙여 다시 잡고, span 은 붙인 만큼 되돌린다.
            # span 을 0 으로 두면 주어 마스킹이 풀려 주어 숫자가 값으로 샌다.
            pre = unit["구간대상"] + " "
            got = E.iter_ranges(pre + base_text, unit)
            ranges = [(k, s, x, (max(0, a - len(pre)), max(0, b - len(pre))))
                      for k, s, x, (a, b) in got]
    if not ranges:
        if E.match_values(base_text, vtype):
            if gap:
                isolated.append(dict(ctx, 호=no, 사유="별표참조_값없음",
                                     별표=gap["별표번호"], 발췌=body[:120]))
            else:
                isolated.append(dict(ctx, 호=no, 사유="주어_미상", 발췌=body[:120]))
        return
    spans = [r[3] for r in ranges]
    for idx, (key, subj_raw, extra, (rs, re_)) in enumerate(ranges):
        # 이 구간의 값 영역: 구간 끝부터 다음 구간 시작 전까지
        nxt = spans[idx + 1][0] if idx + 1 < len(spans) else len(base_text)
        seg = E.mask_spans(base_text[:nxt], spans)[re_:nxt]
        vals = E.match_values(seg, vtype)
        if not vals:
            isolated.append(dict(ctx, 호=no, 사유="값_파싱실패",
                                 갈래="주어외_값없음", 주어=key,
                                 발췌=body[:120]))
            continue
        for v in vals:
            _emit(statements, isolated, ctx, no, key, subj_raw, extra, v, unit,
                  "기본", None, source_excerpt(ctx["조문원문"], body))
    # 조건 구간의 값은 첫 구간 주어에 붙인다 — 괄호·단서는 구간을 다시 쓰지 않는다
    key, subj_raw, extra, _ = ranges[0]
    for ci, ct in enumerate(cond_texts, 1):
        for v in E.match_values(ct, vtype):
            _emit(statements, isolated, ctx, no, key, subj_raw, extra, v, unit,
                  "조건%d" % ci, ct, source_excerpt(ctx["조문원문"], body))


def _emit(statements, isolated, ctx, no, key, subj_raw, extra, v, unit,
          cond_kind, cond_raw, source_excerpt):
    basis = v.get("값기준") or unit.get("값기준")
    if unit["값타입"] == "비율" and not basis:
        isolated.append(dict(ctx, 호=no, 사유="값기준_미상", 주어=key, 값=v["값"]))
        return
    statements.append({
        "lc5": ctx["lc5"], "조례": ctx["조례"], "조문": ctx["조문"],
        "조례계통": ctx["조례계통"], "시행일": ctx["시행일"],
        "조문표제": ctx["조문표제"], "항": ctx["항"], "호": no,
        "근거항": ctx["근거항"], "값타입": unit["값타입"],
        "주어키": key, "주어원문": subj_raw,
        "값": v["값"], "단위": v["단위"], "비교연산": v["비교연산"],
        "값기준": basis, "값원문": v["값원문"],
        "근거발췌": source_excerpt,
        # 조건키 형식은 contract/axis_value.json 의 조건키_규약 이 정본이다.
        # 예외가 하나뿐이라고 전제하면 한 호의 예외들이 서로 충돌한다.
        "조건키": ("기본" if cond_kind == "기본"
                else "%s_%s" % (cond_kind, v["값"])),
        "조건원문": cond_raw,
        **{k: val for k, val in extra.items() if val is not None},
    })


def _mark_chain(statements, isolated, delegated, axis):
    """위임 사슬 밖 명제를 격리로 옮긴다. delegated 가 None 이면 미검사다.

    대조는 (lc5, 조문) 쌍으로 한다. 부분문자열 매칭을 쓰지 않는다 — 실측 조례 IRI 는
    `ordinance/{lc5}/{계통}/{조문}@{시행일}` 이라 계통 마디가 lc5 와 조문 사이에 있고,
    `ordinance/{lc5}/{조문}` 을 substring 으로 찾으면 **한 건도 안 맞는다**. 이 결함은
    delegation.ttl 이 없던 동안 미검사에 가려져 있다가 main 병합으로 드러났다 —
    느슨한 매칭은 거짓 통과뿐 아니라 전면 거짓 실패도 낸다.
    """
    if delegated is None:
        return
    keep = []
    for s in statements:
        if (s["lc5"], s["조문"]) in delegated:
            keep.append(s)
        else:
            isolated.append({k: s[k] for k in ("조례", "조문", "항", "호", "근거항",
                                               "주어키", "값")}
                            | {"사유": "위임사슬_밖"})
    statements[:] = keep


# ── TTL ───────────────────────────────────────────────────────────────────

PREFIX = """@prefix lp: <https://w3id.org/lp/ont#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@base <https://w3id.org/lp/id/> .

"""


def esc(s):
    return (str(s).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", " ").replace("\r", " "))


def seg(s):
    import urllib.parse
    return urllib.parse.quote(str(s), safe="")


def _replace_with_origin(items, pattern):
    """정규식 치환 뒤에도 각 출력 문자의 원문 시작·끝 위치를 보존한다."""
    text = "".join(char for char, _start, _end in items)
    out, pos = [], 0
    for match in pattern.finditer(text):
        out.extend(items[pos:match.start()])
        start = items[match.start()][1]
        end = items[match.end() - 1][2]
        out.append((" ", start, end))
        pos = match.end()
    out.extend(items[pos:])
    return out


def source_excerpt(raw_text, parsed_fragment):
    """파싱용 정규화 조각에 대응하는 연속 원문 구간을 돌려준다.

    axis_engine.strip_markup은 개정 표기를 공백으로 치환하고 전각 콜론을 반각으로
    바꾼다. 파싱 문자열을 근거발췌로 쓰면 과천시 조례의 `：`가 `:`로 바뀌어 원문에
    존재하지 않는 발췌가 된다. 치환 전후 문자 위치를 보존해 원문 구간을 되찾는다.
    """
    items = [(char, index, index + 1) for index, char in enumerate(raw_text or "")]
    for pattern in (E.MARKUP_RE, E.MARKUP_PAREN_RE):
        items = _replace_with_origin(items, pattern)
    items = [(":" if char == "：" else char, start, end)
             for char, start, end in items]
    normalized = "".join(char for char, _start, _end in items)
    start = normalized.find(parsed_fragment)
    if start < 0:
        raise ValueError("파싱 조각을 조례 원문에 역대조하지 못했다: %r" % parsed_fragment)
    end = start + len(parsed_fragment)
    return raw_text[items[start][1]:items[end - 1][2]]


def _effective_compact(date):
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", date or "")
    if not m:
        raise ValueError("시행일은 YYYY-MM-DD 여야 한다: %r" % date)
    return "".join(m.groups())


def _basis_label(key):
    src, number, branch, paragraph = parse_basis_key(key)
    article = "제%s조" % number
    if branch:
        article += "의%s" % branch
    return "%s %s제%s항" % (src, article, paragraph)


def _rel(iri):
    if not iri.startswith(M.ID):
        raise ValueError("canonical 인스턴스 IRI가 아니다: %r" % iri)
    return iri[len(M.ID):]


def _value_iri(kind, axis, statement):
    return "%s/%s/%s/%s/%s/%s/%s" % (
        kind, statement["lc5"], seg(axis["슬러그"]), seg(statement["근거항"]),
        seg(statement["주어키"]), seg(statement["값타입"]),
        seg(statement["조건키"]))


def to_ttl(axis, statements):
    lines = [PREFIX, "# 축: %s (축번호 %s)\n" % (axis["축명"], axis["축번호"]),
             "# 슬러그: %s — 계약 axis_spec.json 의 슬러그 필드가 정본\n\n" % axis["슬러그"]]
    for s in sorted(statements, key=_stmt_key):
        # 패턴 정본은 contract/axis_value.json 의 IRI_패턴 이다.
        # 값타입을 넣지 않으면 한 근거항의 거리·배율이 같은 IRI 로 충돌해 값이 손실된다.
        iri = _value_iri("norm", axis, s)
        cond_iri = _value_iri("cond", axis, s)
        article_iri = _rel(M.ordinance_article(
            s["lc5"], axis["계통"], s["조문"], _effective_compact(s["시행일"])))
        condition = ["<%s> a lp:NormCondition" % cond_iri]
        if s.get("조건원문"):
            condition[0] += " ;"
            condition.append('  lp:조건원문 "%s" .' % esc(s["조건원문"]))
        else:
            condition[0] += " ."
        lines.append("\n".join(condition) + "\n\n")

        b = ["<%s> a lp:NormStatement ;" % iri,
             "  lp:적용관할 <%s> ;" % _rel(M.gov(s["lc5"])),
             "  lp:규범축 <%s> ;" % axis["규범축IRI"],
             "  lp:적용조건 <%s> ;" % cond_iri,
             "  lp:근거조문 <%s> ;" % article_iri,
             '  lp:주어키 "%s" ;' % esc(s["주어키"]),
             '  lp:주어원문 "%s" ;' % esc(s["주어원문"]),
             '  lp:값 "%s"^^xsd:decimal ;' % esc(s["값"]),
             '  lp:값타입 "%s" ;' % esc(s["값타입"]),
             '  lp:추출근거항키 "%s" ;' % esc(s["근거항"]),
             '  lp:단위 "%s" ;' % esc(s["단위"]),
             '  lp:근거발췌 "%s" ;' % esc(s["근거발췌"]),
             '  lp:위임근거항 "%s" ;' % esc(_basis_label(s["근거항"]))]
        if s.get("값기준"):
            b.append('  lp:값기준 "%s" ;' % esc(s["값기준"]))
        # 관계 주어는 항·호가 주어키의 일부다. 근거 위치를 명제에 남기지 않으면
        # 주어키의 항·호 주장이 검증 불가가 되고, 위조해도 통과한다(M7c).
        if "_항" in (s["주어키"] or ""):
            b.append('  lp:근거항번호 "%s"^^xsd:integer ;' % esc(s["항"]))
            b.append('  lp:근거호번호 "%s" ;' % esc(s["호"]))
        if s.get("세분주어"):
            b.append('  lp:세분주어 "true"^^xsd:boolean ;')
        for k in ("구간하한", "구간상한"):
            if s.get(k):
                b.append('  lp:%s "%s"^^xsd:decimal ;' % (k, esc(s[k])))
        for k in ("구간대상", "관계"):
            if s.get(k):
                b.append('  lp:%s "%s" ;' % (k, esc(s[k])))
        if s["비교연산"]:
            b.append('  lp:비교연산 "%s" .' % esc(s["비교연산"]))
        else:
            b.append('  lp:비교연산_미표기 "true"^^xsd:boolean .')
        lines.append("\n".join(b) + "\n\n")
    return "".join(lines)


def _stmt_key(s):
    return (s["lc5"], s["근거항"], s["주어키"], s["값타입"], s["조건키"],
            s["값"], s["조문"])


# ── main ──────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--axis", help="슬러그. 없으면 전 축")
    args = ap.parse_args()

    spec = load("axis_spec.json")
    axes = spec["축"]
    if args.axis:
        axes = [a for a in axes if a["슬러그"] == args.axis]
        if not axes:
            sys.exit("슬러그가 계약에 없다: %s" % args.axis)

    docs_by_system = collections.defaultdict(list)
    for d in corpus.ordinance_docs():
        s, _ = corpus.ordinance_system(d)
        if s:
            docs_by_system[s].append(d)

    delegated = load_delegated_articles()
    os.makedirs(os.path.join(ROOT, OUT_GRAPH), exist_ok=True)
    os.makedirs(os.path.join(ROOT, OUT_REPORT), exist_ok=True)

    coverage = {"기준일": "2026-08-13",
                "위임사슬_대조": "미검사 — delegation.ttl 없음" if delegated is None
                else "검사 — delegates (lc5,조문) 쌍 %d개" % len(delegated),
                "축": []}
    for axis in axes:
        st, iso, gaps = run_axis(axis, docs_by_system, delegated)
        slug = axis["슬러그"]
        gpath = os.path.join(ROOT, OUT_GRAPH, "%s.ttl" % slug)
        if st:
            with open(gpath, "w", encoding="utf-8") as f:
                f.write(to_ttl(axis, st))
        elif os.path.exists(gpath):
            os.remove(gpath)        # 값 0건인 축에 빈 그래프를 남기지 않는다
        for _x in iso:
            _x.pop("항선두", None)
            _x.pop("조문원문", None)
            _x.pop("조례계통", None)
            _x.pop("시행일", None)
        with open(os.path.join(ROOT, OUT_REPORT, "%s.json" % slug), "w",
                  encoding="utf-8") as f:
            json.dump({"축": axis["축명"], "슬러그": slug,
                       "격리": sorted(iso, key=lambda x: json.dumps(x, ensure_ascii=False)),
                       "격리_사유별": dict(sorted(collections.Counter(
                           x["사유"] for x in iso).items())),
                       "별표결손": sorted(gaps, key=lambda x: (x["조례"], x["조문"]))},
                      f, ensure_ascii=False, indent=2, sort_keys=False)
            f.write("\n")
        coverage["축"].append(_axis_coverage(axis, st, iso, gaps))

    # --axis 한 축만 돌렸을 때 커버리지를 덮어쓰지 않는다. 덮으면 분모가 1이 되어
    # 전 축 실행이 낸 '값가능축 4' 가 조용히 '1' 로 바뀐다 — 커버리지는 전 축을
    # 돌렸을 때만 성립하는 집계다.
    if args.axis:
        print("\n--axis 실행이라 %s 는 갱신하지 않는다 (분모는 전 축 실행에서만 성립)"
              % OUT_COVERAGE)
    else:
        coverage["분모"] = _denominators(spec, coverage["축"])
        # 알려진 오판정을 계약에서 그대로 실어 나른다. 리포트만 보는 사람이
        # 틀린 사유를 믿지 않게 하려는 것이다.
        if spec.get("오판정_기록"):
            coverage["알려진_오판정"] = spec["오판정_기록"]
        with open(os.path.join(ROOT, OUT_COVERAGE), "w", encoding="utf-8") as f:
            json.dump(coverage, f, ensure_ascii=False, indent=2)
            f.write("\n")

    for a in coverage["축"]:
        print("%-26s 명제 %5d  격리 %4d  %s" % (
            a["축명"], a["명제"], a["격리"], ",".join(a["값없음사유"]) or ""))
    if not args.axis:
        print("\n분모: %s" % json.dumps(coverage["분모"], ensure_ascii=False))


# 값을 파싱해 놓고 버린 격리. 값이 실제로 없는 격리와 심각도가 정반대다.
DISCARDED_VALUE_REASONS = {"주어_미상", "별표참조_값없음", "값기준_미상",
                           "중복명제_충돌"}


def _axis_coverage(axis, st, iso, gaps):
    out = {"축번호": axis["축번호"], "축명": axis["축명"], "슬러그": axis["슬러그"],
           "추출단위수": len(axis.get("추출단위") or []),
           "명제": len(st), "격리": len(iso),
           "버려진값수": sum(1 for x in iso
                         if x.get("사유") in DISCARDED_VALUE_REASONS),
           "관할수": len({s["lc5"] for s in st}),
           "값없음사유": list(axis.get("값없음사유") or [])}
    # 추출단위 단위 집계. 축 단위로만 세면 한 단위가 0건이어도 다른 단위가
    # 가려 통과한다 — 실측: 일조 영86-3 명제 0·격리 56 이 축 60건에 묻혔다.
    units = []
    for u in (axis.get("추출단위") or []):
        k = (u["근거항"], u["값타입"])
        n_st = sum(1 for s in st if (s["근거항"], s["값타입"]) == k)
        n_iso = sum(1 for x in iso
                    if (x.get("근거항"), x.get("값타입")) == k)
        row = {"근거항": u["근거항"], "값타입": u["값타입"],
               "주어타입": u["주어타입"], "명제": n_st, "격리": n_iso,
               "버려진값수": sum(1 for x in iso
                             if (x.get("근거항"), x.get("값타입")) == k
                             and x.get("사유") in DISCARDED_VALUE_REASONS)}
        if u["값타입"] != "없음" and n_st == 0:
            row["경고"] = ("추출단위 명제 0건 — 주어 모형 오선언 또는 파서 결함을 "
                         "의심한다. 계약이 선언한 단위이므로 범위 밖 배제가 아니다")
        units.append(row)
    out["추출단위"] = units
    if gaps:
        out["별표결손_확인"] = {"조례수": len({g["조례"] for g in gaps}),
                            "건수": len(gaps),
                            "근거발췌_표본": gaps[0]["근거발췌"][:120]}
    if not st and not out["값없음사유"]:
        out["경고"] = "추출단위가 있는데 명제가 0건이다 — 파서 결함을 의심한다"
    return out


def _denominators(spec, axis_cov):
    """분모 3종. 하나만 적으면 전혀 다른 이야기가 된다."""
    return {
        "전체축": 16,
        "전체축_설명": "kb-norm delegation.json 의 위임축 전수. 이 계약은 그중 14축(3~16)을 다룬다",
        "값가능축": sum(1 for a in axis_cov if a["추출단위수"] > 0),
        "값가능축_설명": "추출단위가 비어 있지 않은 축. 원문 구조상 값이 나올 수 있다고 판정한 것",
        "명제산출축": sum(1 for a in axis_cov if a["명제"] > 0),
        "명제산출축_설명": "실제로 명제가 1건 이상 나온 축",
    }


if __name__ == "__main__":
    main()
