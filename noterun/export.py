"""Export a note as an nbformat 4.5 notebook."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from noterun.note import Note

KERNELSPEC = {"name": "python3", "display_name": "Python 3", "language": "python"}


def to_notebook(note: Note) -> dict[str, Any]:
    """Prose becomes markdown cells, runnable chunks code cells; owned regions are dropped."""
    cells: list[dict[str, Any]] = []
    pending: list[str] = []

    def flush() -> None:
        text = "\n".join(pending).strip("\n")
        pending.clear()
        if text.strip():
            cells.append(_cell("markdown", text, len(cells)))

    cursor = note.frontmatter_end + 1
    for chunk in note.chunks:
        if chunk.skip:
            pending.extend(note.lines[cursor : chunk.fence.end])
        else:
            pending.extend(note.lines[cursor : chunk.fence.start])
            flush()
            cells.append(_cell("code", chunk.code, len(cells)))
        cursor = chunk.owned.end
    pending.extend(note.lines[cursor:])
    flush()
    return {
        "cells": cells,
        "metadata": {"kernelspec": KERNELSPEC, "language_info": {"name": "python"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _cell(kind: str, source: str, position: int) -> dict[str, Any]:
    """One notebook cell with id `cell-<position>`."""
    cell = {"cell_type": kind, "id": f"cell-{position}", "metadata": {}, "source": source}
    return {**cell, "execution_count": None, "outputs": []} if kind == "code" else cell


def write_notebook(note: Note) -> Path:
    """Write the notebook beside the note."""
    out = note.path.with_suffix(".ipynb")
    out.write_text(json.dumps(to_notebook(note), indent=1), encoding="utf-8")
    return out
