"""규범 그래프 IRI 발급. 기존 mint_iri 를 감싸 새 개체만 더한다.

statute·ordinance·gov IRI 는 기존 발급기를 그대로 쓴다. 여기서 다시 만들면
같은 개체가 두 IRI 를 갖게 된다.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(
    os.path.join(HERE, "..", "..", "kb-ontology", "scripts")))
import mint_iri as _M

ID = _M.ID
ONT = _M.ONT
LC5_RE = re.compile(r"^[0-9]{5}$")
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def rel(iri):
    """절대 IRI 를 @base 상대 경로로 줄인다."""
    if not iri.startswith(ID):
        raise ValueError(f"mint_iri 산출이 아니다: {iri!r}")
    return iri[len(ID):]


def effective_compact(date):
    """'2024-12-30' → '20241230'. 없으면 None — 호출부가 격리한다."""
    if not date:
        return None
    m = DATE_RE.match(date.strip())
    if not m:
        return None
    return "".join(m.groups())


def zone(name):
    return f"{ID}zone/{_M._seg(name)}"


def condition(key):
    return f"{ID}cond/{_M._seg(key)}"


def norm(lc5, axis, zone_name, cond_key):
    if not LC5_RE.match(lc5 or ""):
        raise ValueError(f"lcCode5 형식 위반: {lc5!r}")
    return (f"{ID}norm/{lc5}/{_M._seg(axis)}"
            f"/{_M._seg(zone_name)}/{_M._seg(cond_key)}")
