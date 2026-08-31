"""지구의 시간 관측값을 만든다. 적용 법령 판본을 결정하지 않는다.

우선순위와 제외 근거는 contract/temporal.json 에 있다. 지구번호 연도는 지구 지정
시점의 proxy일 뿐 시행지침 작성일·인용일이 아니므로 ArticleVersion·LawApplication의
``asOf`` 값으로 쓰면 안 된다. 사업기간도 같은 이유로 관측 기준에서 제외한다.
"""
import re

DSTRC_RE = re.compile(r"^[0-9]{5}[A-Z]{2}([0-9]{4})[0-9]{3}$")

# 고시·결정·승인·변경 뒤의 (연.월.일). 고시번호가 끼어도 잡는다.
NOTICE_RE = re.compile(
    r"(?:고시|결정|승인|변경)"
    r"[^\n]{0,24}?"
    r"[(（]?\s*(\d{4})\s*[.\-년]\s*(\d{1,2})\s*[.\-월]\s*(\d{1,2})"
)

YEAR_MIN, YEAR_MAX = 1980, 2030

FRONTMATTER_NAME_RE = re.compile(r'^지구명:\s*"?(.+?)"?\s*$', re.M)


def year_from_dstrc(no):
    m = DSTRC_RE.match(no or "")
    if not m:
        raise ValueError(f"지구번호 형식 위반: {no!r}")
    y = int(m.group(1))
    if not (YEAR_MIN <= y <= YEAR_MAX):
        raise ValueError(f"지구번호의 연도가 범위를 벗어난다: {y}")
    return y


def own_district_name(md_text):
    """frontmatter 의 지구명을 뽑는다. 없으면 None."""
    if not md_text:
        return None
    parts = md_text.split("---", 2)
    fm = parts[1] if len(parts) >= 3 else md_text[:2000]
    m = FRONTMATTER_NAME_RE.search(fm)
    return m.group(1).strip() if m else None


def find_notice_date(md_text, floor_year):
    """본문에서 고시일을 찾는다.

    세 조건을 모두 만족해야 채택한다. 근접성만으로 잡으면 다른 지구의 고시일을
    자기 것으로 삼는 귀속 오류가 난다 — 실측 반례가 아래 두 테스트에 있다.

      1. floor_year 이후일 것. 지구 지정보다 이른 날짜는 오매칭이다
      2. 고시 문구와 날짜가 **같은 줄**에 있을 것. 표지의 별개 줄을 근접성으로 묶지 않는다
      3. 그 줄에 **자기 지구명**이 있을 것. frontmatter 에 지구명이 있을 때만 건다

    조건 3을 걸지 않으면 이웃 지구의 고시일이 채택된다. 실측 — 부천역곡 문서에서
    NOTICE_RE 가 잡는 유일한 매치가 부천원종 지구의 고시일이다.
    """
    if not md_text:
        return None
    own = own_district_name(md_text)
    for line in md_text.splitlines():
        m = NOTICE_RE.search(line)
        if not m:
            continue
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < floor_year or not (1 <= mo <= 12) or not (1 <= d <= 31):
            continue
        if not (YEAR_MIN <= y <= YEAR_MAX):
            continue
        if own and own not in line:
            continue
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def resolve(dstrc_no, md_text):
    """지구의 시간 관측값과 근거를 낸다.

    반환값은 District의 관측 메타데이터 전용이다. 이름을 ``observation*``으로 분리해
    적용 판본의 ``asOf*``로 오인되는 것을 막는다. 고시일도 현재 문서가 어느 판본인지
    확정하는 자료가 아니므로 ``applicableVersionUnresolved``는 항상 참이다.
    """
    year = year_from_dstrc(dstrc_no)
    date = find_notice_date(md_text, floor_year=year)
    if date:
        return {"observationYear": int(date[:4]), "observationDate": date,
                "basis": "고시일", "precision": "day",
                "applicableVersionUnresolved": True}
    return {"observationYear": year, "observationDate": None,
            "basis": "지구번호연도_proxy", "precision": "year",
            "applicableVersionUnresolved": True}
