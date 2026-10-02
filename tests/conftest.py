"""Shared fixtures."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

FENCE = "`" * 3


def fenced(code: str, lang: str = "python") -> str:
    """A fenced block as note text."""
    return f"{FENCE}{lang}\n{code}\n{FENCE}\n"


@pytest.fixture
def make_note(tmp_path: Path) -> Callable[..., Path]:
    """Write a note with frontmatter plus body and return its path."""

    def _make(
        body: str,
        *,
        name: str = "note.md",
        python: str | None = sys.executable,
        verified: str = "2026-01-01",
    ) -> Path:
        head = ["---", "type: ref"]
        if python:
            head.append(f"run-python: {python}")
        head += [f"verified: {verified}", "---", ""]
        path = tmp_path / name
        path.write_text("\n".join(head) + body, encoding="utf-8")
        return path

    return _make
