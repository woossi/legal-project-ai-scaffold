"""Project Core 인스턴스 IRI 발급기."""

from urllib.parse import quote


ID_BASE = "https://w3id.org/lp/id/"
KINDS = {
    "sourceRecord", "candidate", "informationObject", "event", "assertion",
    "evidence", "interval", "instant",
}


def mint(kind: str, *segments: str) -> str:
    """종류와 공백 없는 경로 조각으로 Project Core IRI를 발급한다."""
    if kind not in KINDS:
        raise ValueError(f"unsupported project-core IRI kind: {kind}")
    if not segments or any(not str(segment).strip() for segment in segments):
        raise ValueError("project-core IRI segments must be non-empty")
    encoded = "/".join(quote(str(segment).strip(), safe="") for segment in segments)
    return f"{ID_BASE}{kind}/{encoded}"
