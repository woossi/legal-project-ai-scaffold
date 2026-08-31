#!/usr/bin/env python3
"""병합 md의 미승격 조문 표제를 `####` 헤딩으로 승격.

상류 변환에서 조문 표제는 `#### 제5조 (용어의 정의)` 형태로 승격된다. 실측
19,215개 헤딩 중 19,080개(99.3%)가 이 **괄호형**이다. 승격을 가른 것은 숫자
앞뒤 공백이 아니라 **괄호 유무** — 괄호가 없으면 승격되지 않았다.

미승격 298개(40개 지구)의 형태는 4종이다.

    공백+무괄호   `제 27 조  가로수 수종선정`
    무괄호        `제3조 가로형간판`
    대괄호        `제1조 [A1] 역세권연결가로`
    표제 없음     `제7조`

이 중 44개는 표제가 아니라 본문이다 — 조문을 인용하는 규정문(`제16조의 규정에
따라 설치하여야 한다.`)과 조판 파편(`제4조)`)이 줄 첫머리에 놓인 것뿐이다.
무조건 승격하면 이들이 헤딩이 되어 목차 구조를 오염시킨다. 배제 규칙 7종이
이를 걸러낸다.

**조사 부착은 공백 없이 붙은 경우만 배제한다.** `제16조의`는 인용문이지만
`제 27 조  가로수…`의 `조` 뒤 공백은 표제의 조판이다. 공백을 허용하면
`제23조 가로축 경관`의 `가`를 조사로 오인해 진짜 표제를 잃는다.

산출: 입력 md를 제자리 수정 (기본은 보고만, `--apply` 로 적용)
"""
import argparse, glob, json, os, re
from collections import Counter, defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
MD = os.path.join(ROOT, "output", "legal", "markdown")
LEVEL = "####"          # 승격 수준. 기존 19,215개 헤딩이 전부 이 수준이다
MAX_LEN = 60            # 표제 길이 상한. 초과분은 실측상 전부 본문이었다

# 조문 표제 후보 — 줄 첫머리(들여쓰기 없음)의 제N조
JOMUN = re.compile(r"^제\s*(\d+)\s*조(?:의\s*\d+)?\s*(.*)$")

# frontmatter `조문수` 의 정본 정의 — verify_contract.py 와 같은 식이어야 한다.
# 승격은 헤딩을 늘리므로 이 값도 같이 올려야 계약이 깨지지 않는다.
HEADING = re.compile(r"(?m)^#{1,6}\s*제\s*\d+\s*조")
COUNT_FIELD = re.compile(r"(?m)^조문수: \d+$")

# 배제 1. 조번호 뒤 조사가 공백 없이 붙음 → 조문을 인용하는 본문
PARTICLE = re.compile(
    r"^제\s*\d+\s*조(?:의\s*\d+)?(?:제\s*\d+\s*[항호목])?"
    r"(에|의|를|은|는|이|가|와|과|로|으로|부터|까지|및|내지)(?![가-힣])"
)
# 배제 2. 종결어미로 끝남 → 완결된 규정문
ENDING = re.compile(r"(한다|된다|있다|없다|하여야|따른다|본다|말한다)\s*\.?\s*$")
# 배제 3. 서술형 어절 포함 → 표제가 아니라 문장
PREDICATE = re.compile(r"(하여야|되어야|따라|의하여|의거|불구하고|경우|관련해서|명기|이하 동일)")
# 배제 4. 조판 파편으로 시작하는 표제
FRAGMENT = re.compile(r"^[)\]}~∙ㅣ]")


def reject(line, title):
    """승격하면 안 되는 줄이면 사유를, 표제면 None을 돌려준다."""
    if PARTICLE.match(line):
        return "조사부착"
    if ENDING.search(line):
        return "종결어미"
    if len(line) > MAX_LEN:
        return "장문"
    if PREDICATE.search(line):
        return "서술어절"
    if not title:
        return "표제없음"
    if FRAGMENT.match(title):
        return "조판파편"
    if len(re.findall(r"제\s*\d+\s*조", line)) > 1:
        return "제N조중복"
    return None


def sync_count(text):
    """frontmatter 의 조문수를 본문 실측 헤딩 수로 맞춘다. (새 본문, 기록값, 실측값)"""
    end = text.index("---", 3)          # frontmatter 닫는 구분자
    fm, body = text[:end], text[end:]
    actual = len(HEADING.findall(body))
    m = re.search(r"(?m)^조문수: (\d+)$", fm)
    if not m:
        return text, None, actual
    return COUNT_FIELD.sub(f"조문수: {actual}", fm, count=1) + body, int(m.group(1)), actual


def scan(path):
    """(승격 대상, 배제 대상) 목록. 각 항목은 (행번호, 원문, 사유)."""
    promote, excluded = [], []
    in_fence = False
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or line.startswith("#"):
            continue
        if line != line.lstrip():        # 들여쓰기 = 표·인용 등 종속 블록
            continue
        m = JOMUN.match(line)
        if not m:
            continue
        why = reject(line, m.group(2).strip())
        (excluded if why else promote).append((i, line, why))
    return lines, promote, excluded


def main():
    ap = argparse.ArgumentParser(description="미승격 조문 표제 헤딩 승격")
    ap.add_argument("--apply", action="store_true", help="실제 파일 수정")
    ap.add_argument("--report", help="판정 결과 JSON 경로")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(MD, "*", "*.md")))
    n_prom = n_excl = n_file = n_sync = 0
    reasons = Counter()
    detail = defaultdict(lambda: {"승격": [], "배제": []})

    for path in files:
        lines, promote, excluded = scan(path)
        rel = os.path.relpath(path, ROOT)
        for _, line, why in excluded:
            reasons[why] += 1
            detail[rel]["배제"].append({"원문": line, "사유": why})
        for _, line, _ in promote:
            detail[rel]["승격"].append({"원문": line})
        for idx, line, _ in promote:
            lines[idx] = f"{LEVEL} {line.strip()}"

        # 조문수 동기화는 승격 여부와 무관하게 돌린다 — 이미 승격된 파일을
        # 다시 실행해도 frontmatter 가 본문과 맞춰지도록(멱등).
        text, before, after = sync_count("\n".join(lines) + "\n")
        stale = before is not None and before != after
        if stale:
            n_sync += 1
            detail[rel]["조문수"] = {"기록": before, "갱신": after}
        if promote:
            n_file += 1
            n_prom += len(promote)
        n_excl += len(excluded)
        if args.apply and (promote or stale):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)

    mode = "적용" if args.apply else "보고만 (--apply 로 적용)"
    print(f"\n조문 표제 승격 — {mode}")
    print(f"  대상 파일 {n_file}개 / 승격 {n_prom}개 / 배제 {n_excl}개")
    print(f"  배제 사유: {dict(reasons)}")
    print(f"  frontmatter 조문수 갱신 {n_sync}개")
    if args.report:
        report_dir = os.path.dirname(args.report)
        if report_dir:
            os.makedirs(report_dir, exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump({"승격": n_prom, "배제": n_excl, "사유": dict(reasons),
                       "조문수갱신": n_sync, "수준": LEVEL, "파일별": detail}, fh,
                      ensure_ascii=False, indent=2)
        print(f"  → {args.report}")


if __name__ == "__main__":
    main()
