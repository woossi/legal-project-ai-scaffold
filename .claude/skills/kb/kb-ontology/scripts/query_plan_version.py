"""OWL-Time 유효기간으로 계획 판본을 기준일에 선택한다."""

from datetime import date

from rdflib import BNode, Graph, Literal, Namespace, URIRef, XSD


LP = Namespace("https://w3id.org/lp/ont#")
TIME = Namespace("http://www.w3.org/2006/time#")
RULE_ID = "PLAN-VERSION-ASOF-001"


def _date_at(graph: Graph, instant) -> date | None:
    """instant의 유일한 유효 xsd:date 값을 읽는다."""
    values = list(graph.objects(instant, TIME.inXSDDate))
    if len(values) != 1:
        return None
    value = values[0]
    if (
        not isinstance(value, Literal)
        or value.datatype != XSD.date
        or value.ill_typed is not False
    ):
        return None
    typed_value = value.toPython()
    return typed_value if isinstance(typed_value, date) else None


def _includes(graph: Graph, interval: URIRef, as_of: date) -> bool:
    beginnings = list(graph.objects(interval, TIME.hasBeginning))
    if len(beginnings) != 1:
        return False
    if not isinstance(beginnings[0], (URIRef, BNode)):
        return False
    start_date = _date_at(graph, beginnings[0])
    if start_date is None:
        return False

    ends = list(graph.objects(interval, TIME.hasEnd))
    if not ends:
        return as_of >= start_date
    if len(ends) != 1:
        return False
    if not isinstance(ends[0], (URIRef, BNode)):
        return False
    end_date = _date_at(graph, ends[0])
    if end_date is None:
        return False
    return start_date <= as_of <= end_date


def _is_valid_version(graph: Graph, version: URIRef, as_of: date) -> bool:
    intervals = list(graph.objects(version, LP.validDuring))
    return (
        len(intervals) == 1
        and isinstance(intervals[0], (URIRef, BNode))
        and _includes(graph, intervals[0], as_of)
    )


def select_plan_versions(graph: Graph, plan: URIRef, as_of: date) -> dict:
    """Return every plan version whose inclusive validity interval contains as_of."""
    candidates = []
    for version in graph.objects(plan, LP.hasVersion):
        if isinstance(version, URIRef) and _is_valid_version(graph, version, as_of):
            candidates.append(str(version))
    candidates.sort()
    status = "resolved" if len(candidates) == 1 else (
        "conflicting" if candidates else "unknown")
    return {
        "status": status,
        "asOf": as_of.isoformat(),
        "candidates": candidates,
        "ruleId": RULE_ID,
    }
