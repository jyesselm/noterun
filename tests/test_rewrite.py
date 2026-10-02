"""Rewrite tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from noterun.note import NoterunError, parse_note
from noterun.rewrite import (
    Edit,
    Fresh,
    _fence_ticks,
    apply_edits,
    commit_note,
    fresh_by_index,
    plan_edits,
    render_owned,
    render_text,
    rewrite_lines,
    stamp_verified,
    stored,
    write_atomic,
)
from noterun.runner import ChunkResult
from tests.conftest import FENCE, fenced

F = FENCE


def test_render_owned_output_and_embeds() -> None:
    got = render_owned("", Fresh("a\n\nb", ["n-1-1.png"]))
    assert got == ["", f"{F}output", "a", "", "b", F, "", "![[n-1-1.png]]"]


def test_render_owned_indented() -> None:
    assert render_owned("  ", Fresh("a\n\nb", [])) == [
        "",
        f"  {F}output",
        "  a",
        "",
        "  b",
        f"  {F}",
    ]


def test_render_owned_no_output_embeds_directly_under_fence() -> None:
    assert render_owned("", Fresh("", ["x.png"])) == ["", "![[x.png]]"]


def test_render_owned_empty() -> None:
    assert render_owned("", Fresh("", [])) == []


@pytest.mark.parametrize("line", [F, "    ````"])
def test_fence_ticks_lengthen_when_stdout_has_backticks(line: str) -> None:
    stdout = f"before\n{line}\nafter"
    ticks = _fence_ticks(stdout)
    assert len(ticks) == len(line.strip()) + 1
    note = parse_note(Path("n.md"), fenced("x") + "\n".join(render_owned("", Fresh(stdout, []))))
    assert stored(note.chunks[0]).stdout == stdout


def test_apply_edits_bottom_up() -> None:
    edits = [Edit(0, 1, ["a", "b"]), Edit(2, 2, ["c"])]
    assert apply_edits(["x", "y", "z"], edits) == ["a", "b", "y", "c", "z"]


def test_stamp_verified_replaces() -> None:
    assert stamp_verified(["---", "verified: old", "---"], 2, "D") == [
        "---",
        "verified: D",
        "---",
    ]


def test_stamp_verified_inserts() -> None:
    assert stamp_verified(["---", "a: 1", "---", "x"], 2, "D") == [
        "---",
        "a: 1",
        "verified: D",
        "---",
        "x",
    ]


def test_stamp_verified_creates_frontmatter() -> None:
    assert stamp_verified(["x"], -1, "D") == ["---", "verified: D", "---", "x"]


def test_rendered_region_reparses_to_same_fresh() -> None:
    path = Path("n.md")
    note = parse_note(path, fenced("x") + "tail\n")
    fresh = {1: Fresh("1\n2", ["n-1-1.png", "n-1-2.png"])}
    again = parse_note(path, render_text(note, rewrite_lines(note, fresh)))
    assert stored(again.chunks[0]) == fresh[1]
    assert plan_edits(again, fresh_by_index(again, [])) != []
    assert plan_edits(again, {1: fresh[1]}) == []


def test_write_atomic_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "n.md"
    path.write_text("old")
    path.chmod(0o640)
    write_atomic(path, "new")
    assert path.read_text() == "new" and path.stat().st_mode & 0o777 == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["n.md"]


def test_commit_note_aborts_when_note_changed(tmp_path: Path) -> None:
    path = tmp_path / "n.md"
    path.write_text(fenced("x"))
    note = parse_note(path, path.read_text())
    path.write_text("edited elsewhere")
    png = tmp_path / "n-1-1.png"
    png.write_bytes(b"png")
    with pytest.raises(NoterunError, match="changed during run"):
        commit_note(note, "new", [ChunkResult(1, "", None, [png])])
    assert path.read_text() == "edited elsewhere" and not (tmp_path / "attachments").exists()
