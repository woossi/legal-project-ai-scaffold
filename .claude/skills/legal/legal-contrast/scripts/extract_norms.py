#!/usr/bin/env python3
"""시행지침 본문에서 규칙 슬롯과 규범 문장을 추출한다.

규범 문장은 지구에 적용되는 행위규범이다. 용어 정의문은 제외한다 — 정의는 이미
output/legal/word/ 가 다룬다. 여기서 다루는 것은 "무엇을 해야 하는가" 이다.

슬롯은 규범이 놓인 자리다. 조문 표제로 잡는다. 같은 슬롯에 놓인 서로 다른 문언이
지구 간 변이이고, 슬롯 자체의 유무가 커버리지다.

출력: output/legal/contrast/slots.json, output/legal/contrast/norms.json
"""
import json
import re
import sys
import collections
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
MD_DIR = ROOT / "output/legal/markdown"
OUT_DIR = ROOT / "output/legal/contrast"

# 규범 강도. 위에서부터 먼저 맞는 것을 채택한다 — 금지가 의무보다 강한 진술이므로 앞에 둔다.
STRENGTH = [
    ("금지", [r"금지한다", r"아니\s?된다", r"아니하여야", r"불허한다", r"제한한다", r"할 수 없다", r"설치하지 않는다"]),
    ("의무", [r"하여야 한다", r"해야 한다", r"하여야 하며", r"되어야 한다", r"따라야", r"따른다",
              r"준수한다", r"적용한다", r"확보한다", r"의한다", r"의하여야"]),
    ("권장", [r"권장한다", r"권장하며", r"바람직하다", r"유도한다", r"지양한다", r"노력한다", r"고려한다", r"권장된다"]),
    ("허용", [r"할 수 있다", r"가능하다", r"허용한다"]),
    ("서술", [r"한다", r"된다", r"이다"]),
]
STRENGTH_RE = [(name, re.compile("|".join(pats))) for name, pats in STRENGTH]

# 정의문. 이 계열은 규범이 아니므로 제외한다.
DEFINITION_RE = re.compile(
    r"(라\s?함은|이라\s?함은|[”\"'’]\s*(이)?란\b|[”\"'’]\s*(은|는)\b).{0,200}?(말한다|의미한다|칭한다)\s*[.。]?\s*$")

# 조문번호는 수치 서명에서 뺀다. 조문 인용 차이가 요건 충돌로 오판되면 안 된다.
ARTICLE_RE = re.compile(r"제\s?\d+\s?(조|항|호|종|편|장|절|목)|별표\s?\d+|\d+-\d+(-\d+)*")
NUM_RE = re.compile(r"\d+(?:\.\d+)?\s?(?:%|㎡|m²|m|㎞|km|층|배|개소|세대|년|일|시간|이상|이하|미만|초과|분의\s?\d+)?")

HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
# 슬롯은 조문이다. 실측상 헤딩 19,083개가 "제N조 (표제)" 형태이고, 이 형태만 조문 표제다.
# 괄호를 무조건 표제로 보면 "…높이(3층이상)" 같은 부가설명이 슬롯으로 섞인다.
ARTICLE_TITLE = re.compile(r"^제\s*\d+\s*조(?:의\s?\d+)?\s*[\(（]\s*([^)）]{2,40}?)\s*[\)）]")
# 편·장·절은 슬롯이 아니라 상위 계층이다. 슬롯 귀속을 끊지 않도록 별도로 다룬다.
SECTION_HEAD = re.compile(r"^제\s*\d+\s*[편장절]")
# 목차 줄은 표제 뒤에 페이지 번호만 남는다. 슬롯으로 세면 커버리지가 부풀려진다.
TOC_LINE = re.compile(r"[\)）]\s*[·.…\s]*\d{1,4}\s*$")

SIM_THRESHOLD = 0.70          # terms.json definition_variance 와 같은 값
JACCARD_THRESHOLD = 0.50      # 어절 공유율. 문자 유사도만으로는 서로 다른 규범이 붙는다
INDEX_TOKENS = 3              # 군집당 역인덱스에 등록할 희소 어절 수
TOKEN_DF_CAP = 3000           # 이보다 흔한 어절은 후보를 좁히지 못하므로 인덱스에서 뺀다


def normalize_slot(title):
    """슬롯 키. 공백·중점만 제거한다. '목적' 과 '목 적' 을 같은 자리로 본다."""
    t = re.sub(r"[\s·․‧∙・]", "", title)
    t = re.sub(r"^제\d+조", "", t)
    return t


def loose(s):
    return re.sub(r"[^가-힣A-Za-z0-9%]", "", s)


def numeric_signature(s):
    body = ARTICLE_RE.sub(" ", s)
    return sorted(re.sub(r"\s", "", m.group(0)) for m in NUM_RE.finditer(body) if any(c.isdigit() for c in m.group(0)))


def strength_of(s):
    """문장 끝 서술어가 규범 강도를 정한다. 끝에서 못 찾으면 복문 중간 절까지 본다."""
    for scope in (s[-40:], s):
        for name, rx in STRENGTH_RE:
            if rx.search(scope):
                return name
    return "미분류"


def split_sentences(body):
    body = re.sub(r"^>\s*\[[^\]]*\]\s*$", "", body, flags=re.M)   # 표·그림 마커 제거
    body = re.sub(r"^#{1,6}.*$", "", body, flags=re.M)            # 헤딩은 문장이 아니다
    for part in re.split(r"(?<=다\.)\s+|\n{2,}", body):
        yield " ".join(part.split())


def strip_lead(s):
    """항·호 앞머리 번호를 뗀다. 문언은 같은데 번호만 달라 다른 규범으로 갈리는 것을 막는다."""
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"^\s*(?:[-•*]|\d+[.)]|\(\d+\)|[①-⑳]|[㉠-㉭]|[가-하][.)]|제?\s?\d+(?:-\d+)*[.)]?)\s*", "", s)
    return s.strip()


def parse_doc(path):
    txt = path.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\n(.*?)\n---\n", txt, re.S)
    head = m.group(1) if m else ""
    dno = re.search(r"지구번호:\s*(\S+)", head)
    dno = dno.group(1) if dno else path.stem
    body = txt[m.end():] if m else txt

    # 조문 표제가 나올 때마다 슬롯이 바뀐다. 그 사이의 문장이 그 슬롯의 규범이다.
    lines = body.split("\n")
    cur_slot, cur_section, slot_hits, norms = None, None, set(), []
    buf = []

    def flush():
        for chunk in split_sentences("\n".join(buf)):
            rec = make_norm(chunk, cur_slot, cur_section)
            if rec:
                norms.append(rec)

    for line in lines:
        hm = HEADING_RE.match(line)
        raw = hm.group(2) if hm else line
        stripped = raw.strip()
        am = ARTICLE_TITLE.match(stripped)
        if am and TOC_LINE.search(stripped):
            continue                    # 목차 줄. 슬롯으로도 규범으로도 세지 않는다.
        if am:                          # 헤딩이든 본문 평문이든 조문 표제면 슬롯이다
            flush()
            buf = []
            cur_slot = normalize_slot(am.group(1))
            slot_hits.add(cur_slot)
            continue
        if hm:
            flush()
            buf = []
            if SECTION_HEAD.match(stripped):
                cur_section = re.sub(r"\s+", " ", stripped)
                cur_slot = None         # 새 장이 열리면 조문 귀속을 끊는다
            continue
        buf.append(line)
    flush()
    return dno, slot_hits, norms


def make_norm(s, slot, section):
    s = strip_lead(s)
    if len(s) < 20 or not s.endswith("."):
        return None
    if DEFINITION_RE.search(s):
        return None
    k = loose(s)
    if len(k) < 15:
        return None
    return {"text": s, "key": k, "slot": slot or "_미귀속", "section": section,
            "strength": strength_of(s), "numsig": numeric_signature(s)}


def merge_clusters(clusters):
    """수치 서명이 같고 유사도 0.70 이상인 군집을 하나로 본다 (표현차이).

    terms.json 의 definition_variance 판정과 같은 기준이다. 서명이 다르면 병합하지
    않는다 — 수치가 다르면 요건이 다르다.

    군집은 슬롯을 가리지 않고 전역으로 묶는다. 같은 규범이 지구마다 다른 조문 아래
    놓이므로, 슬롯별로 나눠 세면 공유 지구 수가 과소평가된다.

    병합은 star 방식이다 — 지구 수가 가장 많은 군집을 시드로 삼고 시드와 직접 유사한
    것만 흡수한다. 흡수된 것은 다시 시드가 되지 않는다. 연결요소로 묶으면 A~B, B~C 가
    유사하다는 이유만으로 A 와 C 가 한 군집에 들어가 서로 다른 규범이 뭉개진다.

    전수 비교는 O(n²) 이라 불가능하므로 희소 어절 역인덱스로 후보를 좁힌다. 유사도
    0.70 이상이면 어절 대부분을 공유하므로 후보에서 빠지지 않는다.
    """
    keys = sorted(clusters, key=lambda k: (-len(clusters[k]["districts"]), k))
    df = collections.Counter()
    toks = {}
    for k in keys:
        t = {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", clusters[k]["rep"])}
        toks[k] = t
        df.update(t)

    index = collections.defaultdict(list)
    for k in keys:
        # 동점일 때 어절 자체를 2차 키로 둔다. set 순회 순서에 기대면 실행마다 색인이 바뀐다.
        pick = sorted((w for w in toks[k] if df[w] <= TOKEN_DF_CAP),
                      key=lambda w: (df[w], w))[:INDEX_TOKENS]
        if not pick:                      # 흔한 어절뿐이면 가장 드문 것 하나라도 쓴다
            pick = sorted(toks[k], key=lambda w: (df[w], w))[:1]
        for w in pick:
            index[w].append(k)

    compared = 0
    groups = collections.defaultdict(list)
    taken = {}
    for k in keys:
        if k in taken:
            continue
        groups[k].append(k)          # 아직 아무에게도 흡수되지 않았으면 시드가 된다
        taken[k] = k
        cand = set()
        for w in toks[k]:
            if w in index and len(index[w]) <= TOKEN_DF_CAP:
                cand.update(index[w])
        cand.discard(k)
        a = clusters[k]
        sm = SequenceMatcher(None, a["rep"], "", autojunk=False)
        # 흡수 순서가 결과를 바꾸므로 set 을 그대로 돌지 않는다
        for c in sorted(cand):
            if c in taken:
                continue
            b = clusters[c]
            if a["numsig"] != b["numsig"]:
                continue
            inter = len(toks[k] & toks[c])
            union = len(toks[k] | toks[c])
            if not union or inter / union < JACCARD_THRESHOLD:
                continue            # 어절을 이만큼도 공유하지 않으면 다른 규범이다
            sm.set_seq2(b["rep"])
            if sm.real_quick_ratio() < SIM_THRESHOLD or sm.quick_ratio() < SIM_THRESHOLD:
                continue
            compared += 1
            if sm.ratio() >= SIM_THRESHOLD:
                groups[k].append(c)
                taken[c] = k
    return groups, compared


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(MD_DIR.glob("*/*.md"))
    slot_districts = collections.defaultdict(set)
    clusters = collections.defaultdict(
        lambda: {"districts": set(), "rep": None, "numsig": None,
                 "strength": collections.Counter(), "slots": collections.Counter()})
    doc_with_slot, doc_with_norm, all_docs = set(), set(), set()

    for f in files:
        dno, slots, norms = parse_doc(f)
        all_docs.add(dno)
        if slots:
            doc_with_slot.add(dno)
        for s in sorted(slots):          # 슬롯 삽입 순서가 동점 정렬 결과를 좌우한다
            slot_districts[s].add(dno)
        if norms:
            doc_with_norm.add(dno)
        for n in norms:
            c = clusters[n["key"]]
            c["districts"].add(dno)
            c["strength"][n["strength"]] += 1
            c["slots"][n["slot"]] += 1
            if c["rep"] is None or len(n["text"]) < len(c["rep"]):
                c["rep"], c["numsig"] = n["text"], n["numsig"]

    groups, compared = merge_clusters(clusters)

    norms_out = []
    nid = 0
    for root, members in groups.items():
        ds, st, sl = set(), collections.Counter(), collections.Counter()
        variants = []
        for k in members:
            c = clusters[k]
            ds |= c["districts"]
            st += c["strength"]
            sl += c["slots"]
            variants.append({"text": c["rep"], "districts": sorted(c["districts"]),
                             "count": len(c["districts"])})
        variants.sort(key=lambda v: (-v["count"], v["text"]))
        nid += 1
        norms_out.append({
            "norm_id": f"N{nid:06d}",
            "representative": variants[0]["text"],
            "slots": [{"slot": s, "count": n} for s, n in sl.most_common()],
            "slot_dispersion": len(sl),
            "numeric_signature": clusters[root]["numsig"],
            "strength": st.most_common(1)[0][0],
            "strength_mix": dict(st),
            "variant_count": len(variants),
            "district_count": len(ds),
            "districts": sorted(ds),
            "variants": variants if len(variants) > 1 else [],
            "문언변이": "일치" if len(variants) == 1 else "표현차이",
        })

    norms_out.sort(key=lambda n: (-n["district_count"], n["representative"]))
    D = len(doc_with_slot)
    slots_out = []
    for s, ds in sorted(slot_districts.items(), key=lambda x: (-len(x[1]), x[0])):
        slots_out.append({"slot": s, "district_count": len(ds),
                          "coverage": round(len(ds) / D, 4) if D else 0.0,
                          "districts": sorted(ds)})

    (OUT_DIR / "slots.json").write_text(json.dumps({
        "meta": {
            "생성스크립트": "scripts/extract_norms.py",
            "대상문서수": len(all_docs),
            "조문표제_보유문서수": D,
            "조문표제_미보유문서수": len(all_docs) - D,
            "커버리지_분모": "조문표제 보유 문서수. 미보유 문서는 변환 실패이므로 분모에서 뺀다",
            "슬롯수": len(slots_out),
            "정규화": "공백·중점 제거, 선행 '제N조' 제거",
            "목차줄_배제": "표제 뒤에 페이지 번호만 남는 줄은 슬롯으로 세지 않는다",
        },
        "slots": slots_out}, ensure_ascii=False, indent=1), encoding="utf-8")

    (OUT_DIR / "norms.json").write_text(json.dumps({
        "meta": {
            "생성스크립트": "scripts/extract_norms.py",
            "규범문장군집수": len(norms_out),
            "규범문장_보유문서수": len(doc_with_norm),
            "정의문_제외": "'라 함은 … 말한다' 계열은 규범이 아니므로 뺀다",
            "문언변이_판정": f"수치 서명 동일 AND 유사도 {SIM_THRESHOLD} 이상이면 같은 규범의 표현차이로 본다",
            "군집범위": "슬롯 무관 전역. 같은 규범이 지구마다 다른 조문 아래 놓이므로 슬롯별로 나눠 세지 않는다",
            "후보생성": f"희소 어절 역인덱스 (군집당 {INDEX_TOKENS}개, 문서빈도 {TOKEN_DF_CAP} 이하). "
                    f"유사도 계산 {compared}회",
            "강도등급": [s[0] for s in STRENGTH] + ["미분류"],
            "저장형식": "군집이 4만 건에 가까워 들여쓰기 없이 저장한다. 레코드마다 한 줄이다",
        },
        "norms": norms_out}, ensure_ascii=False, indent=None,
        separators=(",", ":")), encoding="utf-8")

    print(f"문서 {len(all_docs)} · 조문표제 보유 {D} · 규범문장 보유 {len(doc_with_norm)}")
    print(f"슬롯 {len(slots_out)} · 규범군집 {len(norms_out)} (병합전 {len(clusters)}, 유사도계산 {compared}회)")
    print("커버리지 상위 10:")
    for s in slots_out[:10]:
        print(f"  {s['district_count']:3d} ({s['coverage']*100:4.1f}%) {s['slot']}")
    print("최다 공유 규범 5:")
    for n in norms_out[:5]:
        print(f"  {n['district_count']:3d}지구 [{n['strength']}] {n['representative'][:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
