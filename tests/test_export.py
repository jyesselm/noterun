"""Notebook export tests."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from noterun.export import to_notebook
from noterun.note import parse_note
from tests.conftest import FENCE, fenced

TEXT = (
    "---\nrun-python: x\n---\n# Title\n\n"
    + fenced("x = 1")
    + f"\n{FENCE}output\n1\n{FENCE}\n\n![[n-1-1.png]]\n\nmiddle prose\n\n"
    + fenced("# skip: excerpt\ny = 2")
    + f"\n{FENCE}output\nstale\n{FENCE}\n\n"
    + fenced("print(x)")
)


def notebook() -> dict:
    """The exported notebook of TEXT."""
    return to_notebook(parse_note(Path("n.md"), TEXT))


def test_export_structure() -> None:
    nb = notebook()
    assert nb["nbformat"] == 4 and nb["nbformat_minor"] == 5
    assert nb["metadata"]["kernelspec"]["name"] == "python3"
    ids = [c["id"] for c in nb["cells"]]
    assert len(set(ids)) == len(ids) and all(re.match(r"^[a-zA-Z0-9-_]+$", i) for i in ids)
    code = [c["source"] for c in nb["cells"] if c["cell_type"] == "code"]
    assert code == ["x = 1", "print(x)"]
    everything = "\n".join(c["source"] for c in nb["cells"])
    assert "# skip: excerpt" in everything and "```output" not in everything
    assert "![[" not in everything and "run-python" not in everything and "stale" not in everything


def test_export_validates_with_nbformat() -> None:
    nbformat = pytest.importorskip("nbformat")
    nbformat.validate(nbformat.from_dict(notebook()))
