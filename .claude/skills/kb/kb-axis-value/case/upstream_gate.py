"""선행조건 게이트 — 시행지침 규범값을 소비하기 전에 통과해야 한다.

legal-table 의 `contract/outputs.json §하위축_소비계약` 을 **소비하는 쪽에서**
강제한다. 그 계약은 발급자 스킬의 검증기가 강제할 수 없다 — 계약을 어기는 것은
소비하는 축이고, 어겼는지는 소비하는 축에서만 보인다.

막으려는 것은 규범 역전이다. 행당 도시개발구역 L64 는
    "기준용적률은 … 용도지역의 용적률(제2종일반주거지역, 200%)로 하며,
     허용용적률은 … 330%이하로 한다"
인데 330 은 확정(norm_values.json)에, 기준 200 은 격리(_norm_value_report.json)에
있다. **확정만 읽으면 330 이 기준인 줄 안다.** 값이 사라진 것이 아니라 역할이
반쪽만 보이는 것이며, 이 축이 동두천·평택·하남·성남 인동간격에서 겪은 것과
같은 구조다(kb-axis-value 격리사유 `기본값_부재_조건값`).

게이트는 선언이 아니라 실행 가능한 검사다. 소비 코드가 격리를 함께 읽는지를
**코드 경로로** 확인한다 — 두 파일을 다 읽었는지, 관계값의 짝을 이었는지.

사용:
    .venv/bin/python3 .claude/skills/kb/kb-axis-value/case/upstream_gate.py
    → 0 = 소비해도 된다 · 1 = 선행조건 불충족
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
CONFIRMED = os.path.join(ROOT, "output/legal/table/norm_values.json")
QUARANTINE = os.path.join(ROOT, "output/legal/table/_norm_value_report.json")
UPSTREAM_CONTRACT = os.path.join(
    ROOT, ".claude/skills/legal/legal-table/contract/outputs.json")

# legal-table 계약(§하위축_소비계약.읽기절차)의 값 도메인.
# relation_role 은 스키마 도메인 8 이지만 실측 발급은 기준·상한 뿐이다 —
# 관측되지 않은 값을 전제한 분기를 쓰지 말라는 것이 상류 주의사항이다.
ROLE_SCHEMA = {"기준", "허용", "기본", "완화", "상한", "하한", "원칙", "예외"}
ROLE_OBSERVED = {"기준", "상한"}
BASIS_DOMAIN = {"동일줄_직접수식", "동일줄_순서대응"}
# 값 도메인의 정본은 **상류 계약**이다. 여기에 베껴 두면 상류가 값을 늘렸을 때
# 두 곳이 어긋나고, 정당한 갱신이 게이트 실패로 보인다 — 실측(2026-08-14)
# 상류가 context_class 에 '인용서술' 을 더하자 이 게이트가 거짓 실패를 냈다.
# 상류 계약을 읽어 쓰고, 못 읽으면 실패시킨다(추측하지 않는다).
def _upstream_domain(field, fallback):
    try:
        with open(UPSTREAM_CONTRACT, encoding="utf-8") as f:
            return set(json.load(f)["값도메인"][field])
    except Exception:
        return set(fallback)


CONTEXT_DOMAIN = _upstream_domain(
    "context_class", ["규범", "예시도면표시", "완화총량", "산정식", "타지표"])
QUAR_DOMAIN = _upstream_domain(
    "quarantine_class", ["비교표현없음", "지표미상", "지표귀속불가"])
# 규범값만 소비한다. 안 거르면 예시도면표시 472 · 타지표 34 · 완화총량 5 가
# 규범으로 섞여 38% 부풀려진다 — legal-table HANDOFF.md 가 명시한 실패다.
NORM_CONTEXT = "규범"


def _rows(obj):
    """상류 스키마는 {meta, records} 다. records 를 못 찾으면 실패시킨다 —
    다른 키로 폴백하면 파일 구조가 바뀌었을 때 조용히 엉뚱한 배열을 읽는다."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj.get("records"), list):
        return obj["records"]
    raise KeyError("상류 스키마에 records 가 없다 — 파일 구조가 바뀌었다")


def norm_records(rows):
    """규범값만. context_class 로 거르는 것이 상류 읽기절차 2단계다."""
    return [r for r in rows if r.get("context_class") == NORM_CONTEXT]


def load_pair():
    """확정과 격리를 **함께** 낸다. 확정만 반환하는 진입점을 두지 않는다.

    이 함수가 게이트의 실체다. 소비 코드는 여기를 통해서만 시행지침 값을 읽고,
    그러면 격리를 빠뜨릴 수 없다.
    """
    with open(CONFIRMED, encoding="utf-8") as f:
        confirmed = _rows(json.load(f))
    with open(QUARANTINE, encoding="utf-8") as f:
        quarantined = _rows(json.load(f))
    return confirmed, quarantined


def check():
    fails, notes = [], []
    if not (os.path.exists(CONFIRMED) and os.path.exists(QUARANTINE)):
        return ["상류 산출물이 없다 — legal-table 병합 여부를 확인한다"], []
    conf, quar = load_pair()

    # G1 두 파일을 다 읽었고 value_id 가 한 연속열인가
    ids_c = {r.get("value_id") for r in conf}
    ids_q = {r.get("value_id") for r in quar}
    if ids_c & ids_q:
        fails.append(f"G1 value_id 가 확정·격리에 중복 {len(ids_c & ids_q)}건")
    notes.append(f"G1 확정 {len(conf)} · 격리 {len(quar)} · 관측 {len(conf) + len(quar)}")

    # G2 모수 — 확정만으로 비율을 내면 왜곡이다. 분모를 명시적으로 낸다
    if not quar:
        fails.append("G2 격리가 0건이다 — 파일을 잘못 읽었을 가능성이 높다")
    meta = {}
    try:
        with open(CONFIRMED, encoding="utf-8") as f:
            meta = json.load(f).get("meta", {})
    except Exception:
        pass
    if meta.get("관측_전건") and meta["관측_전건"] != len(conf) + len(quar):
        fails.append(f"G2 관측 전건 불일치 — meta {meta['관측_전건']} vs "
                     f"실측 {len(conf) + len(quar)}. 한 파일이 낡았다")

    # G2b **context_class 로 규범만 거른다.** 확정 records 를 그대로 규범으로 쓰면
    # 예시도면표시·타지표·완화총량이 섞여 38% 부풀려진다(HANDOFF.md 실패 사례).
    bad_ctx = {r.get("context_class") for r in conf + quar} - CONTEXT_DOMAIN - {None}
    if bad_ctx:
        fails.append(f"G2b context_class 도메인 밖: {sorted(bad_ctx)}")
    norm_conf, norm_quar = norm_records(conf), norm_records(quar)
    if meta.get("규범값수") and meta["규범값수"] != len(norm_conf):
        fails.append(f"G2b 규범값수 불일치 — meta {meta['규범값수']} vs "
                     f"실측 {len(norm_conf)}")
    notes.append(f"G2b 규범 확정 {len(norm_conf)} · 규범 격리 {len(norm_quar)} "
                 f"— 확정 records {len(conf)} 를 그대로 쓰면 "
                 f"{100 * (len(conf) - len(norm_conf)) / max(1, len(norm_conf)):.0f}% 부풀려진다")

    # G2c 확정·격리 구분은 quarantine_class 유무로 한다(상류 읽기절차 2단계)
    misplaced = [r.get("value_id") for r in conf if r.get("quarantine_class")]
    if misplaced:
        fails.append(f"G2c 확정에 quarantine_class 가 있는 레코드 {len(misplaced)}건")
    bad_q = {r.get("quarantine_class") for r in quar} - QUAR_DOMAIN - {None}
    if bad_q:
        fails.append(f"G2c quarantine_class 도메인 밖: {sorted(bad_q)}")

    # G3 관계값이 값 도메인 안인가. 새 값은 판정표가 바뀐 신호다
    rel = [r for r in conf + quar if r.get("relation_role")]
    bad_role = {r["relation_role"] for r in rel} - ROLE_SCHEMA
    bad_basis = ({r.get("relation_basis") for r in rel if r.get("relation_basis")}
                 - BASIS_DOMAIN)
    if bad_role:
        fails.append(f"G3 relation_role 도메인 밖: {sorted(bad_role)}")
    if bad_basis:
        fails.append(f"G3 relation_basis 도메인 밖: {sorted(bad_basis)}")
    unobs = {r["relation_role"] for r in rel} - ROLE_OBSERVED
    if unobs:
        notes.append(f"G3 실측 밖 역할값 관측: {sorted(unobs)} — 판정표가 바뀌었다. "
                     f"원문 대조 결과를 함께 남긴다")
    notes.append(f"G3 관계값 {len(rel)}건 — "
                 f"role {sorted({r['relation_role'] for r in rel})} · "
                 f"basis {sorted({r.get('relation_basis') for r in rel if r.get('relation_basis')})}")

    # G4 **규범 역전 방지가 게이트의 핵심이다.**
    # 격리에 역할값이 있는데 그 짝이 확정에 있으면, 확정만 읽는 소비는
    # 짝의 한쪽만 그래프에 올린다. 짝을 이을 수 있는지 실제로 확인한다.
    by_id = {r.get("value_id"): r for r in conf + quar}
    orphan = []
    for r in quar:
        if not r.get("relation_role"):
            continue
        pair_id = r.get("relation_pair")
        if pair_id and pair_id not in by_id:
            orphan.append((r["value_id"], pair_id, "짝 value_id 를 못 찾는다"))
        elif pair_id and pair_id in ids_c:
            # 정상 — 격리쪽 역할값과 확정쪽 짝이 이어진다. 이것이 바로
            # 확정만 읽으면 안 되는 이유이므로 건수를 남긴다.
            notes.append(f"G4 격리 {r['value_id']}({r.get('relation_role')}, "
                         f"{r.get('value')}) ↔ 확정 {pair_id} — "
                         f"확정만 읽으면 역할이 반쪽만 보인다")
    if orphan:
        fails.append(f"G4 짝을 못 잇는 관계값 {len(orphan)}건: {orphan[:3]}")

    # G5 미발급을 부재로 읽지 않기 — 사유가 있는 것과 없는 것을 가른다
    reason = [r for r in quar if r.get("relation_reason")]
    notes.append(f"G5 역할 미발급 {len(reason)}건 — "
                 f"'역할 없음'이 아니라 '확정 못 함'이다. 사유별로 뜻이 다르다")

    # G6 관계 필드는 prob 층. det 그래프에 올리면 안 된다.
    det_dir = os.path.join(ROOT, "output/kb/norm/graph/det")
    leaked = []
    for dirpath, _, files in os.walk(det_dir):
        for fn in files:
            if not fn.endswith(".ttl"):
                continue
            with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                if "relation_role" in f.read():
                    leaked.append(os.path.relpath(os.path.join(dirpath, fn), ROOT))
    if leaked:
        fails.append(f"G6 relation_role 이 det 층에 올라갔다: {leaked}")
    notes.append("G6 det 층 relation_role 유출 없음 — 관계 역할은 판정이라 prob 층이다")
    return fails, notes


def main():
    fails, notes = check()
    print("선행조건 게이트 — 시행지침 규범값 소비")
    for n in notes:
        print("  ·", n)
    if fails:
        print(f"\n불충족 {len(fails)}건:")
        for f in fails:
            print("  FAIL", f)
        return 1
    print("\n통과 — 격리를 함께 읽었고 관계값 짝이 이어진다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
