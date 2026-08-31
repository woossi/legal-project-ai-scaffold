"""검사기 변이 시험. **검출률을 실측한다 — 종료코드로 판정하지 않는다.**

2026-08-14 규명: 종료코드 이진 판정은 거짓 통과를 낸다. 값기준 106건을 바꿨는데
56건만 검출돼도 종료코드는 1 이라 "잡힘" 으로 찍혔고, 미검출 50건을 못 봤다.
검증자가 전건 변이로 미검출 40% 를 실측해 반증했다.

**변이를 심었으면 몇 건 심었고 몇 건 잡혔는지를 센다.** 한 건이라도 미검출이면
그 변이 유형은 미달이다 — 검사기는 전건을 잡아야 한다.

사용:
    .venv/bin/python3 .claude/skills/kb/kb-axis-value/case/mutation_test.py
"""
import contextlib
import importlib.util
import io
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
GRAPH = os.path.join(ROOT, "output/kb/norm/graph/det/norm-value")
CHECKER = os.path.join(HERE, "invariant_check.py")

# **실제 산출물을 제자리에서 덮어쓰지 않는다.** 이 스크립트는 등록부 order 16 이라
# 검증기 일괄 실행에서 돌고, 제자리 변이는 예외·인터럽트 한 번에 산출물을 변이
# 상태로 남긴다. 검증자가 이 위험 때문에 이 스크립트를 안 돌리고 자기 사본으로만
# 시험했다. 임시 사본에서 돌리고, 그래도 try/finally 로 원본 복원을 보장한다.
WORKDIR = None          # 임시 사본 경로. setup_sandbox 가 채운다


def graph_dir():
    return WORKDIR if WORKDIR else GRAPH


def run_checker():
    """검사기를 돌려 (종료코드, 위반유형별 건수) 를 낸다."""
    spec = importlib.util.spec_from_file_location("IC", CHECKER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    if WORKDIR:
        m.GRAPH = WORKDIR        # 검사기가 사본을 보게 한다
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = m.main()
    counts = {k: int(v) for k, v in
              re.findall(r"FAIL ([^:]+): (\d+)건", buf.getvalue())}
    return rc, counts


# M7d 전용. 주어키(평문)·IRI(퍼센트인코딩)·근거항번호(정수)를 **함께** 바꾼다.
# 하나만 바꾸면 자기 정합이 깨져 M7c 가 잡아 버리므로 M7d 시험이 되지 않는다.
M7D_PAIRS = [
    ("_항3_호", "_항9_호"),                       # lp:주어키 평문
    ("_%ED%95%AD3_%ED%98%B8", "_%ED%95%AD9_%ED%98%B8"),   # IRI 인코딩
    ('lp:근거항번호 "3"', 'lp:근거항번호 "9"'),      # 위치 필드
]


def apply_m7d(text):
    for a, b in M7D_PAIRS:
        text = text.replace(a, b)
    return text


def snapshot():
    return {f: open(os.path.join(graph_dir(), f), encoding="utf-8").read()
            for f in sorted(os.listdir(graph_dir())) if f.endswith(".ttl")}


def restore(snap):
    for f, s in snap.items():
        open(os.path.join(graph_dir(), f), "w", encoding="utf-8").write(s)


# 변이 정의. (설명, 치환 전, 치환 후, 이 변이를 잡아야 할 위반유형)
# 전건 치환이다 — 1건만 바꾸면 검출률을 못 잰다.
MUTATIONS = [
    ("M1 값 날조", 'lp:값 "0.5"^^xsd:decimal', 'lp:값 "9.9"^^xsd:decimal',
     "값↔근거발췌 불일치"),
    ("M2 비교연산 뒤집기", 'lp:비교연산 "이상"', 'lp:비교연산 "이하"',
     "비교연산↔근거발췌 불일치"),
    ("M4 주어키 교체", 'lp:주어키 "주거지역"', 'lp:주어키 "상업지역"',
     "주어키↔주어원문 불일치"),
    ("M5 값기준 교체", 'lp:값기준 "대지면적"', 'lp:값기준 "연면적"',
     "값기준↔원문 불일치"),
    # M3 은 근거발췌의 값 연산자를 뒤집는다. RDF 비교연산과 별개로 발췌 자체를
    # 원문과 대조해야 닫힌다.
    # M3 잔여 5/99(5%) 는 원문 자체에 'N퍼센트 이하' 가 1,643곳 실재해
    # 뒤집은 값이 같은 조문의 다른 자리와 우연히 맞는 경우다. 문자열 대조의
    # 원리적 한계이며 상한 10% 로 둔다.
    #
    # 실측(2026-08-14) — 정탐을 지키는 것은 **괄호 건너뛰기**이지 창 크기가 아니다.
    #   괄호건너뛰기 + 6자(현재) · + 2자 · + 0자  → 정탐 사망 0건
    #   괄호건너뛰기 없음 + 6자 → 30건 사망 · + 0자 → 34건 사망
    # 창을 6자에서 0자로 줄여도 하나도 안 죽는다. 원문이
    # '0.8배(도시형 생활주택 0.4배) 이상' 처럼 연산자를 괄호 뒤로 미루기 때문에
    # 괄호를 건너뛰는 것이 30건을 지킨다. 창을 좁히면 정탐이 죽는다는 앞선
    # 서술은 틀렸다.
    ("M3 근거발췌 연산자", '퍼센트 이상"', '퍼센트 이하"', "근거발췌↔원문 불일치",
     "상한10"),
    # M7c 는 관계 주어의 항·호를 위조한다. 위치를 명제에 안 남기면 검증 불가라
    # 통째로 면제됐다 — 실측 435건 중 88건(20%)이 그 경로였다.
    ("M7c 관계 항 위조", '_항3_호', '_항9_호', "주어키↔주어원문 불일치"),
    # M7d 는 주어키·IRI·근거항번호를 **함께 일관되게** 위조한다. 셋이 서로 맞아
    # 자기 정합 검사를 통과한다 — 파서가 삭제 항을 안 세서 이후 항이 하나씩
    # 밀리면 현실적으로 이렇게 된다. 조례 원문의 실제 항 집합과 대조해야 닫힌다.
    ("M7d 항 일관 위조", "@M7D@", "@M7D@", "주어키↔주어원문 불일치", "일관"),
    # M8 은 구간대상을 연면적 → 대지면적 으로 바꾼다. 경계 숫자는 그대로라
    # 주어키↔주어원문 경계 대조를 통과한다 — 실측 조경 23건이 이 형태로 새
    # 나갔다(파서가 '연면적' 의 앞 글자를 흘리고 '면적' 부터 매칭). 구간대상
    # 낱말을 주어원문·조문 원문과 대조해야 닫힌다.
    ("M8 구간대상 바꿔치기", 'lp:구간대상 "연면적"', 'lp:구간대상 "대지면적"',
     "구간대상↔주어원문 불일치"),
    # M8b 는 바닥면적을 대지면적으로 바꾼다. 앞 글자가 '바닥' 이라 '연' 검사를
    # 빠져나간다 — 주석은 "'…인 대지' 로 이어지면 대지면적" 이라 적혀 있는데
    # 코드가 그 확인을 안 해 생긴 구멍이다(검증자 실측 12건 전건 미검출).
    ("M8b 바닥면적 바꿔치기", 'lp:구간대상 "바닥면적"', 'lp:구간대상 "대지면적"',
     "구간대상↔주어원문 불일치"),
    # M6 은 전건 치환하면 기본값이 하나도 안 남아 조건값 전부가 고아가 된다.
    # 심은 수보다 검출 수가 커질 수 있으므로 '심은 수 이상 검출' 로 판정한다.
    # M6 은 canonical 조건 IRI의 **적용조건 목적어**에서 기본 세그먼트를 바꾼다.
    # 조건 노드 선언·NormStatement IRI까지 함께 세면 심은 수가 세 배가 되므로,
    # 목적어 줄(`> ;`)만 바꿔 명제 수와 심은 수를 같은 단위로 둔다.
    ("M6 규범 역전", '/%EA%B8%B0%EB%B3%B8> ;', '/%EC%A1%B0%EA%B1%B4> ;',
     "기본값 부재 조건값", "이상"),
    # N1 은 canonical ontology namespace와 instance base를 RFC 2606 예약 도메인으로
    # 되돌린다. 검사기는 Turtle prefix 문자열이 아니라 파싱된 RDF term 세 자리를
    # 검사해야 한다.
    ("N1 namespace 역치환", "https://w3id.org/lp/",
     "https://legal-project.example/", "금지 네임스페이스", "파일전건"),
]


def main():
    snap = snapshot()
    rc, base = run_checker()
    if rc != 0 or base:
        print(f"기준선이 이미 위반 상태다: {base}")
        return 1
    print(f"기준선 통과. 변이 {len(MUTATIONS)}종을 전건 치환으로 시험한다.\n")
    fails = []
    for mut in MUTATIONS:
        name, old, new, kind = mut[:4]
        mode = mut[4] if len(mut) > 4 else "전건"
        if mode == "일관":
            planted = sum(s.count(M7D_PAIRS[0][0]) for s in snap.values())
        elif mode == "파일전건":
            planted = sum(1 for s in snap.values() if old in s)
        else:
            planted = sum(s.count(old) for s in snap.values())
        if not planted:
            print(f"  {name:16s} SKIP — 대상 0건 (변이를 심지 못했다)")
            fails.append((name, "대상 0건"))
            continue
        if mode == "파일전건":
            caught = 0
            for target, original in snap.items():
                restore(snap)
                if old not in original:
                    continue
                open(os.path.join(graph_dir(), target), "w", encoding="utf-8").write(
                    original.replace(old, new))
                _, got = run_checker()
                if got.get(kind, 0) > 0:
                    caught += 1
            restore(snap)
            miss = planted - caught
            rate = 100 * miss / planted
            mark = "OK  " if miss == 0 else "MISS"
            print(f"  {mark} {name:16s} 파일 {planted:3d} · 검출 {caught:3d} · "
                  f"미검출 {miss:3d} ({rate:.0f}%)")
            if miss:
                fails.append((name, f"파일 미검출 {miss}/{planted} ({rate:.0f}%)"))
            continue
        for f, s in snap.items():
            mutated = apply_m7d(s) if mode == "일관" else s.replace(old, new)
            open(os.path.join(graph_dir(), f), "w", encoding="utf-8").write(mutated)
        _, got = run_checker()
        caught = got.get(kind, 0)
        restore(snap)
        if mode == "일관":
            miss = planted - caught
            rate = 100 * miss / planted
            mark = "OK  " if miss == 0 else "MISS"
            print(f"  {mark} {name:16s} 심음 {planted:3d} · 검출 {caught:3d} · "
                  f"미검출 {miss:3d} ({rate:.0f}%)")
            if miss:
                fails.append((name, f"미검출 {miss}/{planted} ({rate:.0f}%)"))
            continue
        if mode == "상한10":
            miss = planted - caught
            rate = 100 * miss / planted
            ok = rate <= 10
            print(f"  {'OK  ' if ok else 'MISS'} {name:16s} 심음 {planted:3d} · "
                  f"검출 {caught:3d} · 미검출 {miss:3d} ({rate:.0f}%, 상한 10%)")
            if not ok:
                fails.append((name, f"미검출 {rate:.0f}% > 상한 10%"))
            continue
        if mode == "이상":
            # 심은 수 이상 검출되면 통과. 전건 치환이 2차 위반을 낳는 변이다.
            ok = caught >= planted
            print(f"  {'OK  ' if ok else 'MISS'} {name:16s} 심음 {planted:3d} · "
                  f"검출 {caught:3d} (심은 수 이상이면 통과)")
            if not ok:
                fails.append((name, f"검출 {caught} < 심음 {planted}"))
            continue
        miss = planted - caught
        rate = 100 * miss / planted
        mark = "OK  " if miss == 0 else "MISS"
        print(f"  {mark} {name:16s} 심음 {planted:3d} · 검출 {caught:3d} · "
              f"미검출 {miss:3d} ({rate:.0f}%)")
        if miss:
            fails.append((name, f"미검출 {miss}/{planted} ({rate:.0f}%)"))
    rc2, after = run_checker()
    if rc2 != 0 or after:
        print(f"\n복원 실패 — 산출물이 변이 상태로 남았다: {after}")
        return 1
    print("\n복원 확인. 기준선 통과.")
    if fails:
        print(f"\n미달 {len(fails)}종:")
        for n, why in fails:
            print(f"  {n} — {why}")
        return 1
    print("전 변이 기준 충족.")
    return 0


def run():
    """임시 사본을 만들어 그 안에서 돌린다. 원본은 읽기만 한다."""
    global WORKDIR
    tmp = tempfile.mkdtemp(prefix="kb-axis-mutation-")
    try:
        WORKDIR = os.path.join(tmp, "norm-value")
        shutil.copytree(GRAPH, WORKDIR)
        return main()
    finally:
        WORKDIR = None
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(run())
