"""Prevent README drift by verifying local file references exist."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"

LOCAL_REFERENCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._/-])("
    r"\.github/[A-Za-z0-9._/*-]+"
    r"|docs/[A-Za-z0-9._/*-]+"
    r"|src/[A-Za-z0-9._/*-]+"
    r"|tests/[A-Za-z0-9._/*-]+"
    r"|data/[A-Za-z0-9._/*-]+"
    r"|README\.md"
    r"|CHANGELOG\.md"
    r"|LICENSE"
    r"|pyproject\.toml"
    r"|seed\.yaml"
    r")(?![A-Za-z0-9._/-])"
)


def _extract_local_references(readme_text: str) -> list[str]:
    refs = {
        match.group(1).rstrip(".,:;")
        for match in LOCAL_REFERENCE_PATTERN.finditer(readme_text)
    }
    return sorted(refs)


def _reference_exists(ref: str) -> bool:
    if "*" in ref:
        return any(REPO_ROOT.glob(ref))
    return (REPO_ROOT / ref).exists()


def test_readme_local_references_exist() -> None:
    text = README_PATH.read_text(encoding="utf-8")
    references = _extract_local_references(text)

    assert references, "No local references found in README; update the drift check."

    missing = [ref for ref in references if not _reference_exists(ref)]
    assert not missing, f"README contains missing local references: {missing}"
