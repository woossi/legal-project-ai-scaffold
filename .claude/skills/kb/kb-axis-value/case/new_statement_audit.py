"""신규 명제 전건 원문 대조.

주어원문은 조건절을 뺀 재구성 문자열이라 원문에 그대로 없을 수 있다
(0.8배(도시형 0.5배) 이상 → 주어원문 '…0.8배 이상'). 어절 단위로 원문에
실재하는지 본다. 값·구간대상·조건원문은 그대로 대조한다.
"""
import collections
import importlib.util
import re
import subprocess
import sys

sys.path.insert(0, ".claude/skills/kb/kb-norm/scripts")
import corpus
spec = importlib.util.spec_from_file_location(
    'E', '.claude/skills/kb/kb-axis-value/scripts/axis_engine.py')
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)
docs = {d['official_name']: d for d in corpus.ordinance_docs()}
nb = lambda s: re.sub(r"\s+", "", s or "")
_B = {}


def body(nm, art):
    k = (nm, art)
    if k not in _B:
        _B[k] = nb(E.strip_markup(
            [x[2] for x in corpus.articles(docs[nm]) if x[1] == art][0]))
    return _B[k]


def rows(t):
    o = {}
    for blk in t.split("<norm/")[1:]:
        g = lambda k: (re.search(rf'lp:{k} "([^"]+)"', blk) or [None, None])[1]
        o[(g("조례"), g("근거조문"), g("주어원문"), g("값"), g("조건키"))] = {
            "주어키": g("주어키"), "구간대상": g("구간대상"), "값원문": g("값원문"),
            "조건원문": g("조건원문"), "단위": g("단위")}
    return o


BASE = sys.argv[1] if len(sys.argv) > 1 else "91c99aa~1"
P, C = {}, {}
for f in ("landscaping", "public-open-space", "sunlight-height-limit",
          "lot-subdivision"):
    P.update(rows(subprocess.run(
        ["git", "show", f"{BASE}:output/kb/norm/graph/det/norm-value/{f}.ttl"],
        capture_output=True, text=True).stdout))
    C.update(rows(open(f'output/kb/norm/graph/det/norm-value/{f}.ttl',
                       encoding='utf-8').read()))
new = [k for k in C if k not in P]
gone = [k for k in P if k not in C]
bad = []
for k in new:
    nm, art, so, v, ck = k
    r = C[k]
    b = body(nm, art)
    chk = []
    # 주어원문: 조건절을 뺀 재구성이라 어절 단위로 본다
    # 괄호를 마스킹하면 앞뒤 조각이 붙는다 — 원문 '1배(도시형 0.5배)이상' 이
    # 주어원문에서 '1배이상' 이 된다. 어절이 통째로 없으면 그 어절을 다시
    # 조각내 각 조각이 원문에 있는지 본다. 조각까지 없으면 진짜 미실재다.
    def tok_ok(w):
        if nb(w) in b:
            return True
        parts = [x for x in re.split(r"(?<=[배％%])|(?<=미터)", nb(w)) if len(x) >= 2]
        return bool(parts) and all(x in b for x in parts)

    toks = [w for w in re.split(r"[\s(),]+", so or "") if len(nb(w)) >= 3]
    missing = [w for w in toks if not tok_ok(w)]
    if missing:
        chk.append(f"주어어절 미실재 {missing[:2]}")
    if nb(r["값원문"]) not in b and nb(f'{v}{r["단위"]}') not in b:
        chk.append("값 미실재")
    tgt = r["구간대상"]
    if tgt and tgt not in nb(so):
        i = b.find(nb(so))
        if i >= 0 and b[max(0, i - 3):i].endswith("연"):
            chk.append("구간대상 오귀속")
    if r["조건원문"] and nb(r["조건원문"]) not in b:
        chk.append("조건원문 미실재")
    if chk:
        bad.append((nm, art, r["주어키"], v, chk))
print(f"기준선 {len(P)} → 현재 {len(C)} | 신규 {len(new)} · 소멸 {len(gone)}")
print(f"신규 원문 대조 실패: {len(bad)}건")
for x in bad[:10]:
    print("  ", x)
sys.exit(1 if bad else 0)
