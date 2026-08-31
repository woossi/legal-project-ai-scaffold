#!/usr/bin/env python3
"""AI 설정 골격의 이식성, 정본 연결, 기본 구문을 검증한다."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/agents",
    ".claude/skills",
    ".agents/skills",
    ".claude/rules",
    ".codex/rules",
    ".claude/team-roster.yaml",
    ".claude/settings.json",
    ".codex/config.toml",
    ".mcp.example.json",
    "templates/agent-memory/MEMORY.md",
    "tools/deepseek_router_proxy.py",
)
FORBIDDEN = (
    "output",
    "docs/ppt",
    ".claude/agent-memory",
    ".claude/worktrees",
    ".worktrees",
    ".codex/logs",
    ".superpowers",
    ".mcp.json",
    ".claude/settings.local.json",
)
UNADAPTED_CANONICAL_SKILLS = {".claude/skills/common/orchestrate/SKILL.md"}
EXTERNAL_AGENT_TYPES = {"gis-figure-designer"}
ADAPTER_POINTER = re.compile(r"정본 `(?P<path>\.claude/skills/[^`]+/SKILL\.md)`")
AGENT_TYPE = re.compile(r"^\s*agent_type:\s*(?P<name>[a-z0-9-]+)\s*$", re.MULTILINE)
TOKEN_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def text_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*"):
        if (
            not path.is_file()
            or ".git" in path.parts
            or "__pycache__" in path.parts
            or ".pytest_cache" in path.parts
            or path.suffix in {".bin", ".pyc"}
            or path.name == ".DS_Store"
        ):
            continue
        files.append(path)
    return files


def validate() -> tuple[list[str], dict[str, int]]:
    errors: list[str] = []

    for item in REQUIRED:
        if not (ROOT / item).exists():
            errors.append(f"required path missing: {item}")
    for item in FORBIDDEN:
        if (ROOT / item).exists():
            errors.append(f"runtime or project-state path present: {item}")

    canonical = {
        relative(path)
        for path in (ROOT / ".claude/skills").glob("*/*/SKILL.md")
    }
    adapters = list((ROOT / ".agents/skills").glob("*/*/SKILL.md"))
    referenced: set[str] = set()
    for adapter in adapters:
        content = adapter.read_text(encoding="utf-8")
        match = ADAPTER_POINTER.search(content)
        if not match:
            errors.append(f"canonical pointer missing: {relative(adapter)}")
            continue
        target = match.group("path")
        referenced.add(target)
        if not (ROOT / target).is_file():
            errors.append(f"adapter target missing: {relative(adapter)} -> {target}")

    unadapted = canonical - referenced
    if unadapted != UNADAPTED_CANONICAL_SKILLS:
        errors.append(
            "unexpected canonical skills without adapters: "
            + ", ".join(sorted(unadapted))
        )

    roster = (ROOT / ".claude/team-roster.yaml").read_text(encoding="utf-8")
    local_agent_types = {path.stem for path in (ROOT / ".claude/agents").glob("*/*.md")}
    for agent_type in AGENT_TYPE.findall(roster):
        if agent_type not in local_agent_types and agent_type not in EXTERNAL_AGENT_TYPES:
            errors.append(f"roster agent definition missing: {agent_type}")

    for path in ROOT.rglob("*.json"):
        if ".git" in path.parts:
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"invalid JSON: {relative(path)}: {exc}")

    python_files = [path for path in ROOT.rglob("*.py") if ".git" not in path.parts]
    for path in python_files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (UnicodeDecodeError, SyntaxError) as exc:
            errors.append(f"invalid Python: {relative(path)}: {exc}")

    verifier = Path(__file__).resolve()
    for path in text_files():
        if path.resolve() == verifier:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "/Users/" in content:
            errors.append(f"absolute user path present: {relative(path)}")
        for pattern in TOKEN_PATTERNS:
            if pattern.search(content):
                errors.append(f"possible embedded secret: {relative(path)}")
                break

    counts = {
        "canonical_skills": len(canonical),
        "codex_adapters": len(adapters),
        "local_agents": len(local_agent_types),
        "python_files": len(python_files),
        "files": len(text_files()),
    }
    return errors, counts


def main() -> int:
    errors, counts = validate()
    report = {"ok": not errors, "counts": counts, "errors": errors}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
