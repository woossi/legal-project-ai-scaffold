"""corpus 로더. 새 수집 없이 이미 있는 정본 두 개만 읽는다.

    guideline_article_corpus.jsonl.gz      직접범위 정본 365
    guideline_t4_article_corpus.jsonl.gz   T4 파생범위 정본 790

두 파일에 같은 문서가 겹칠 수 있으므로 document_key 로 접는다. 직접범위를 우선한다 —
파생범위가 같은 문서를 덮으면 기원 표시가 섞인다.
"""
import functools
import gzip
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))

CORPUS_FILES = [
    "output/legal/statute/guideline_article_corpus.jsonl.gz",
    "output/legal/statute/guideline_t4_article_corpus.jsonl.gz",
]

# 상위 법령 6종. 정식명칭은 corpus 의 official_name 그대로다.
STATUTE_NAMES = {
    "국토계획법": "국토의 계획 및 이용에 관한 법률",
    "국토계획법시행령": "국토의 계획 및 이용에 관한 법률 시행령",
    "건축법": "건축법",
    "건축법시행령": "건축법 시행령",
    "주차장법": "주차장법",
    "주차장법시행령": "주차장법 시행령",
}
TARGET_STATUTES = set(STATUTE_NAMES.values())

# 조례 3계통. 계통은 조례명이 아니라 제1·2조가 지목한 법률로 정한다.
# 아래 정규식은 계통 후보를 좁히는 1차 필터일 뿐이다.
ORDINANCE_PATTERNS = [
    ("도시계획조례", re.compile(r"(도시계획 조례|군계획 조례)$")),
    ("건축조례", re.compile(r"건축 ?조례$")),
    ("주차장조례", re.compile(r"주차장.*(설치.*관리|조례)$")),
]

# 조례 제1·2조가 지목하는 상위 법률 → 계통
SYSTEM_BY_ACT = {
    "국토의 계획 및 이용에 관한 법률": "도시계획조례",
    "건축법": "건축조례",
    "주차장법": "주차장조례",
}
_ACT_IN_BRACKET = re.compile(r"「([^」]+)」")


@functools.lru_cache(maxsize=1)
def load_documents():
    """두 corpus 의 전 문서. document_key 중복은 먼저 온 것(직접범위)을 남긴다."""
    seen = {}
    for rel in CORPUS_FILES:
        path = os.path.join(ROOT, rel)
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                key = d.get("document_key")
                if key and key not in seen:
                    seen[key] = d
    return [seen[k] for k in sorted(seen)]


@functools.lru_cache(maxsize=1)
def statute_docs():
    """상위 법령 6종. 키는 정식명칭."""
    out = {}
    for d in load_documents():
        name = (d.get("official_name") or "").strip()
        if name in TARGET_STATUTES and name not in out:
            out[name] = d
    return out


@functools.lru_cache(maxsize=1)
def ordinance_docs():
    """3계통 조례 문서. official_name 오름차순."""
    out, seen = [], set()
    for d in load_documents():
        if d.get("official_kind") != "조례":
            continue
        name = (d.get("official_name") or "").strip()
        if not name or name in seen:
            continue
        if any(pat.search(name) for _, pat in ORDINANCE_PATTERNS):
            seen.add(name)
            out.append(d)
    return sorted(out, key=lambda d: d["official_name"])


# 조문 판정. target 마다 값이 다르다 — 이걸 틀리면 장·절 표제가 조문으로 섞인다.
ARTICLE_STATUS = {"law": {"조문"}, "ordin": {"Y"}, "admrul": {"조문"}}


def is_article(target, prov):
    """장·절 표제를 걸러낸다.

    조례는 장 표제가 바로 다음 조문과 같은 article_number 를 갖는다 —
    「서울특별시 건축 조례」의 000100 은 N(제1장 총칙)과 Y(제1조 목적) 둘이다.
    """
    return prov.get("article_status") in ARTICLE_STATUS.get(target, set())


def article_label(target, number, branch):
    """조문 라벨을 만든다. 번호 규약이 target 마다 다르다.

        law/admrul  number='4'      branch='2'  → 제4조의2
        ordin       number='005602' branch=''   → 제56조의2  (조 4자리 + 가지 2자리)
    """
    num = (number or "").strip()
    if not num.isdigit():
        raise ValueError(f"조문번호 형식 위반: {number!r}")
    if target == "ordin":
        n = int(num)
        base = f"제{n // 100}조"
        sub = n % 100
        if sub:
            base += f"의{sub}"
        return base
    base = f"제{int(num)}조"
    if branch and str(branch).strip().isdigit():
        base += f"의{int(branch)}"
    return base


def _sort_key(target, prov):
    num = int(prov["article_number"])
    branch = prov.get("article_branch") or "0"
    branch = int(branch) if str(branch).strip().isdigit() else 0
    return (num, branch) if target != "ordin" else (num, 0)


def articles(doc):
    """조문만 (정렬키, 라벨, 본문, 표제) 로 낸다. 장·절 표제는 빠진다."""
    target = doc.get("target") or ""
    out = []
    for p in doc.get("provisions") or []:
        num = (p.get("article_number") or "").strip()
        if not num.isdigit() or int(num) == 0:
            continue
        if not is_article(target, p):
            continue
        out.append((_sort_key(target, p),
                    article_label(target, num, p.get("article_branch")),
                    p.get("text") or "",
                    p.get("article_title") or ""))
    out.sort(key=lambda t: t[0])
    return out


def _first_articles(doc, limit=2):
    """제1·2조. 장 표제를 빼고 번호 순으로 앞을 취한다."""
    return articles(doc)[:limit]


def ordinance_system(doc):
    """조례의 계통을 제1·2조가 지목한 법률로 확정한다.

    조례명으로 정하지 않는다 — 이름은 표기 변이가 있고 법적 근거가 아니다.
    확정 못 하면 (None, None) 을 내고 호출부가 격리한다.
    """
    for _, _, text, _ in _first_articles(doc):
        for act in _ACT_IN_BRACKET.findall(text):
            act = act.strip()
            if act in SYSTEM_BY_ACT:
                return SYSTEM_BY_ACT[act], text.strip()
    return None, None


CONTRACT_DIR = os.path.join(HERE, "..", "contract")


@functools.lru_cache(maxsize=1)
def _jurisdiction_table():
    path = os.path.join(CONTRACT_DIR, "jurisdiction_code.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)["codes"]


def jurisdiction_code(authority):
    """지자체기관명 → lcCode5. 표에 없으면 None 이며 호출부가 격리한다."""
    return _jurisdiction_table().get((authority or "").strip())
