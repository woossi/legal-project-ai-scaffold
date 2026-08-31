#!/usr/bin/env python3
"""시행지침 정의문에서 상위법령 인용을 추출해 인용 간선으로 만든다.

`terms.json` 의 `cited_laws` 는 용어 단위 집계라 지구 출처가 없다. kb 그래프
계약(`kb-plan/contract/graph.json`)이 "모든 관계에 출처(지구번호·원문 근거)가
있다"를 불변식으로 요구하므로, 인용을 `variants[].text` 에서 다시 파싱한다.
variant 는 지구 목록을 가지므로 간선마다 지구와 근거 문장이 붙는다.

법령명 어휘는 2단으로 만든다.
  1단 깨끗한 인용(`「건축법 시행령」 제2조`)에서 법령명을 학습한다.
  2단 학습한 어휘로 지저분한 문자열(`별도로 정해져 있지 않은 경우에는 건축법
      제43조`)에서 최장 일치를 찾는다.
상대참조(`동법`·`같은 법`)는 같은 문장의 앞선 법령으로 해소한다. 해소되지
않으면 간선을 만들지 않고 리포트에만 남긴다 — kb-plan 절대규칙 5(추측 관계
금지)를 따른다.

입력  output/legal/word/terms.json
출력  output/legal/statute/statute_citations.json
      output/legal/statute/_statute_report.json
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import statute_common as sc  # noqa: E402

QUOTES = "「」『』｢｣“”‘’\"'\U000f0850\U000f0851\U000f0852\U000f0853"
QRE = re.compile("[" + re.escape(QUOTES) + "]")

# OCR·조판 오탈자. 실측에서 확인된 것만 넣는다.
TYPO = {"주택전설기준": "주택건설기준"}

# 법령 접미사 — 법령명은 이것으로 끝난다
SUFFIX = (
    r"(?:법률|특별법|법|시행령|시행규칙|규칙|규정|조례|지침|기준|고시|예규|훈령)"
)
# 조문 참조
ARTREF = re.compile(r"제\s*\d+\s*(?:조|항|호|편|장|절)(?:\s*의\s*\d+)?|별표\s*\d*|별지\s*\d*")
# 상대참조 — 앞 문장의 법령을 가리킨다
RELREF = re.compile(r"(?:동법|같은\s*법|본법)\s*(시행령|시행규칙)?")
# 법령이 아닌 일반 지칭
NOT_LAW = {"관계법령", "관련법령", "동법", "같은법", "본법", "법", "조례", "시건축조례"}
# `법` 으로 끝나지만 법령이 아닌 꼬리. `…옥상녹화 조성기법` 이 법령으로
# 학습되던 실측 사례에서 나왔다.
NOT_LAW_TAIL = re.compile(r"(?:기법|공법|수법|용법|기준법|치법)$")


def clean(s):
    s = QRE.sub(" ", s or "")
    for bad, good in TYPO.items():
        s = s.replace(bad, good)
    return re.sub(r"\s+", " ", s).strip()


# 법령명 안의 열거 구분자. 원본 조판마다 중점 표기가 다르고(ㆍ · ‧ ․ ･ ・ ∙)
# 쉼표로 적은 문서도 있다. 법령명에 쉼표가 정상적으로 들어가는 사례는 없으므로
# 함께 접는다.


def canon_key(name):
    """공백·중점 표기 차이를 접은 대조 키."""
    return sc.strip_separators(name)


# ── 1단: 깨끗한 인용에서 법령명 어휘를 학습한다 ──────────────────────────────

CLEAN_CITE = re.compile(rf"^([가-힣A-Za-z0-9()·‧․･・∙\s]{{2,40}}?{SUFFIX})((?:\s*(?:{ARTREF.pattern}))*)\s*$")


QUOTE_OPEN = set("「『｢“‘\"'\U000f0850\U000f0851\U000f0852\U000f0853")


def _candidate(raw):
    """인용 문자열에서 법령명 후보를 뽑는다. 아니면 None."""
    m = CLEAN_CITE.match(clean(raw))
    if not m:
        return None
    name = m.group(1).strip()
    if canon_key(name) in NOT_LAW or len(canon_key(name)) < 3:
        return None
    if ARTREF.search(name) or RELREF.search(name) or re.match(r"^제\s*\d", name):
        return None
    if NOT_LAW_TAIL.search(canon_key(name)):
        return None
    # 정의문 도입부는 법령명일 수 없다. `주택단지 라 함은 주택건설기준에관한규정`
    # 처럼 뒤쪽 법령 표기가 오기라 어휘에 없으면 2단 위치 규칙도 통과하므로,
    # 도입부 자체를 구조적으로 배제한다.
    if re.search(r"함은|이란\s|란\s", name):
        return None
    return name


def learn_vocab(terms):
    """법령명 어휘를 2단으로 학습한다.

    1단 **인용부호로 시작하는 인용만** 학습한다. 법령명은 관례상 「」로 묶이고,
         묶이지 않은 인용에는 앞 문장의 서술이 붙어 들어온다.
    2단 인용부호 없는 인용은, 1단 어휘로 스캔했을 때 **0번 위치가 아닌 곳에서
         이미 아는 법령이 발견되면 버린다.** 그 앞부분이 서술 잔여물이라는
         뜻이다(`말하여 이때 건축법` → `건축법` 이 7번 위치). 아는 법령이 없거나
         0번에서 시작하면 새 법령이므로 학습한다(`도로법`,
         `산업집적활성화 및 공장설립에 관한 법률 시행규칙`).

    어미 목록으로 거르는 방식은 `위하여`·`높이는`·`정해진` 을 놓쳐 서술
    잔여물 8종이 법령 노드로 올라왔다. 위치 기반 규칙이 그 두더지잡기를 없앤다.
    """
    forms = collections.Counter()
    quoted, plain = [], []
    for t in terms:
        for raw in t.get("cited_laws", []):
            s = (raw or "").strip()
            (quoted if s and s[0] in QUOTE_OPEN else plain).append(raw)

    for raw in quoted:
        name = _candidate(raw)
        if name:
            forms[name] += 1

    if forms:                                  # 1단 어휘로 2단을 심사한다
        v1 = {canon_key(n): n for n in forms}
        sc1, _ = build_scanner(v1)
        for raw in plain:
            name = _candidate(raw)
            if not name:
                continue
            if any(st > 0 for _k, _s, st, _e in scan(name, sc1, v1)):
                continue                       # 앞부분이 서술 잔여물
            forms[name] += 1

    # 공백 변이를 묶고 최빈 표기를 대표로 삼는다
    groups = collections.defaultdict(collections.Counter)
    for name, n in forms.items():
        groups[canon_key(name)][name] += n
    return {k: c.most_common(1)[0][0] for k, c in groups.items()}, groups


# ── 2단: 학습한 어휘로 본문에서 인용을 찾는다 ────────────────────────────────

def build_scanner(vocab):
    """어휘를 최장 일치 우선으로 찾는 정규식. 공백 변이를 허용한다."""
    # 긴 것 먼저 — `건축법 시행령` 이 `건축법` 보다 먼저 잡혀야 한다
    keys = sorted(vocab, key=len, reverse=True)
    parts = []
    for k in keys:
        # 키의 각 글자 사이에 공백과 중점을 허용해 조판 변이를 흡수한다.
        # 중점은 canon_key 에서 제거되므로 여기서 다시 허용하지 않으면
        # `신에너지 및 재생에너지 개발·이용·보급 촉진법` 이 매칭되지 않는다.
        parts.append(sc.loose_pattern(k))
    return re.compile("(" + "|".join(parts) + ")"), keys


def scan(text, scanner, vocab):
    """문장에서 (법령키, 매치문자열, 시작, 끝) 을 순서대로 뽑는다."""
    out = []
    for m in scanner.finditer(text):
        key = canon_key(m.group(1))
        if key in vocab:
            out.append((key, m.group(1), m.start(), m.end()))
    return out


def articles_after(text, pos, limit=40):
    """법령명 바로 뒤에 이어지는 조문 참조를 모은다."""
    tail = text[pos: pos + limit]
    refs = []
    cur = 0
    for m in ARTREF.finditer(tail):
        # 법령명 직후부터 연속으로 이어지는 것만 인정한다
        between = tail[cur: m.start()]
        if not re.fullmatch(r"[\s,·및그의]*", between):
            break
        refs.append(re.sub(r"\s+", "", m.group()))
        cur = m.end()
    return refs


# 조례 앞에 붙는 지자체명. 어휘가 지자체 없는 `도시계획조례` 만 학습한 경우
# 근거 문장에서 되살린다(실측: 안성시·인천시·하남시).
GOV_PREFIX = re.compile(
    r"([가-힣]{2,7}(?:특별자치시|특별자치도|특별시|광역시|시|군|구))\s*$"
)


def extract(text, scanner, vocab):
    """문장 하나에서 인용 목록을 만든다. 상대참조는 앞 법령으로 해소한다."""
    text = clean(text)
    hits = scan(text, scanner, vocab)
    cites = []
    for key, surface, st, en in hits:
        note = None
        if key.endswith("조례") and not GOV_PREFIX.search(surface):
            # 표제어에 지자체가 없으면 바로 앞을 본다. 근거 문장에 있는 값만
            # 쓰므로 추측이 아니다.
            g = GOV_PREFIX.search(text[max(0, st - 12): st])
            if g:
                surface = g.group(1) + " " + surface
                key = canon_key(surface)
                st -= len(g.group(1))
                note = f"지자체 복원 → {g.group(1)}"
            else:
                # `인천시 도시계획조례(이하 도시계획조례 라 한다)` 처럼 문장이
                # 스스로 약칭을 정의하는 경우, 같은 문장의 앞선 조례로 잇는다.
                prior = [c for c in cites
                         if c["statute_key"].endswith("조례") and c["pos"] < st
                         and c["statute_key"] != key]
                if prior:
                    src = prior[-1]
                    note = f"문장 내 약칭 해소 → {src['surface']}"
                    key = src["statute_key"]
                    surface = src["surface"]
        c = {
            "statute_key": key,
            "surface": surface,
            "articles": articles_after(text, en),
            "resolution": "직접",
            "pos": st,
        }
        if note:
            c["note"] = note
            c["name_hint"] = surface
        cites.append(c)

    # 상대참조 — 같은 문장에서 앞선 본법을 찾아 해소한다
    for m in RELREF.finditer(text):
        # 이미 어휘로 잡힌 구간이면 건너뛴다
        if any(c["pos"] <= m.start() < c["pos"] + len(c["surface"]) for c in cites):
            continue
        sub = m.group(1)      # 시행령 | 시행규칙 | None
        prior = [c for c in cites if c["pos"] < m.start() and not
                 c["statute_key"].endswith(("시행령", "시행규칙"))]
        if not prior:
            cites.append({
                "statute_key": None, "surface": m.group(),
                "articles": articles_after(text, m.end()),
                "resolution": "미해소", "pos": m.start(),
                "note": "문장 안에 선행 법령이 없어 동법을 해소하지 못했다",
            })
            continue
        base = prior[-1]["statute_key"]
        key = canon_key(base + (sub or ""))
        # 합성 키가 어휘에 없어도 정당한 법령이다(`…촉진법` + `시행규칙`).
        # 표시명을 본법 대표표기로 조립해 두지 않으면 노드 이름이 빈다.
        name_hint = vocab.get(key) or (vocab.get(base, base) + (f" {sub}" if sub else ""))
        cites.append({
            "statute_key": key,
            "name_hint": name_hint,
            "surface": m.group(),
            "articles": articles_after(text, m.end()),
            "resolution": "상대참조해소",
            "pos": m.start(),
            "note": f"동법 → {vocab.get(base, base)}",
        })

    cites.sort(key=lambda c: c["pos"])
    for c in cites:
        # 근거는 인용 위치 기준 발췌다. 문장 앞부분을 잘라 담으면 뒤쪽 인용이
        # 근거 밖으로 밀려나 대조에 실패한다(실측 9건).
        p = c.pop("pos")
        c["evidence"] = text[max(0, p - 100): p + len(c["surface"]) + 150]
    return cites


# ── 집계 ─────────────────────────────────────────────────────────────────────

def build(terms_path, out_dir):
    data = json.loads(Path(terms_path).read_text(encoding="utf-8"))
    terms = data["terms"]
    vocab, groups = learn_vocab(terms)
    scanner, _ = build_scanner(vocab)

    citations = []
    unresolved = []
    stat_use = collections.Counter()
    res_count = collections.Counter()
    cid = 0

    for t in terms:
        for v in t["variants"]:
            text = v.get("text") or ""
            if not text:
                continue
            for c in extract(text, scanner, vocab):
                res_count[c["resolution"]] += 1
                rec = {
                    "citation_id": f"C{cid:05d}",
                    "term": t["term"],
                    "term_id": t["id"],
                    "variant_index": v.get("variant_index"),
                    "statute_key": c["statute_key"],
                    "statute_name": (vocab.get(c["statute_key"] or "")
                                     or c.get("name_hint", "")),
                    "surface": c["surface"],
                    "articles": c["articles"],
                    "resolution": c["resolution"],
                    "districts": sorted(v.get("districts", [])),
                    "quote": c["evidence"],
                }
                if c.get("note"):
                    rec["resolution_note"] = c["note"]
                cid += 1
                if c["resolution"] == "미해소" or not c["statute_key"]:
                    unresolved.append(rec)
                    continue
                if not rec["districts"]:
                    rec["_no_district"] = True
                citations.append(rec)
                stat_use[c["statute_key"]] += 1

    # 어휘로 잡히지 않은 cited_laws — 커버리지 진단
    missed = []
    for t in terms:
        for raw in t.get("cited_laws", []):
            s = clean(raw)
            if not scan(s, scanner, vocab) and not RELREF.search(s):
                missed.append({"term": t["term"], "raw": raw})

    nodes_needed = [
        {"statute_key": k, "statute_name": vocab[k], "인용수": stat_use.get(k, 0),
         "표기변이": sorted(groups[k])}
        for k in sorted(vocab, key=lambda x: -stat_use.get(x, 0))
    ]

    out = {
        "meta": {
            "생성근거": "terms.json 의 variants[].text 에서 상위법령 인용을 재파싱",
            "원본_생성일시": data.get("meta", {}).get("생성일시"),
            "어휘학습": {
                "방법": "cited_laws 중 통째로 법령명+조문참조인 문자열에서 학습",
                "학습_법령수": len(vocab),
            },
            "인용수": len(citations),
            "미해소수": len(unresolved),
            "해소방법별": dict(res_count),
            # 상대참조로 합성된 키는 어휘에 없으므로 어휘가 아니라
            # 실제 간선의 고유 키를 센다
            "법령수": len({c["statute_key"] for c in citations}),
            "출처규약": (
                "간선마다 districts 와 quote 를 담는다. kb-plan/contract/graph.json 의 "
                "불변식 '모든 관계에 출처(지구번호·원문 근거)가 있다' 를 만족시키기 위함이다"
            ),
            "미해소_취급": (
                "상대참조를 해소하지 못한 인용은 간선을 만들지 않고 리포트에만 남긴다. "
                "kb-plan 절대규칙 5(추측 관계 금지)를 따른다"
            ),
            "스크립트": ".claude/skills/legal/legal-statute/scripts/build_citations.py",
        },
        "statutes_needed": nodes_needed,
        "citations": citations,
    }
    report = {
        "meta": {
            "설명": "인용 추출 이력 — 미해소 인용과 어휘 미포착 문자열",
            "미해소수": len(unresolved),
            "어휘미포착수": len(missed),
        },
        "unresolved": unresolved,
        "vocab_missed": missed,
    }

    od = Path(out_dir)
    od.mkdir(parents=True, exist_ok=True)
    (od / "statute_citations.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (od / "_statute_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terms", default="output/legal/word/terms.json")
    ap.add_argument("--out-dir", default="output/legal/statute")
    a = ap.parse_args()
    if not Path(a.terms).exists():
        print(f"입력 없음: {a.terms}", file=sys.stderr)
        return 1
    out, rep = build(a.terms, a.out_dir)
    m = out["meta"]
    print(f"인용 {m['인용수']}건 / 법령 {m['법령수']}종 → {a.out_dir}/statute_citations.json")
    print(f"해소방법: {m['해소방법별']}")
    print(f"미해소 {rep['meta']['미해소수']}건 / 어휘미포착 {rep['meta']['어휘미포착수']}건")
    print("상위 법령:", ", ".join(
        f"{n['statute_name']}({n['인용수']})" for n in out["statutes_needed"][:8]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
