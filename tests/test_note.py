"""Parser tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from noterun.note import (
    NoterunError,
    normalize_output,
    parse_frontmatter,
    parse_note,
    split_lines,
)
from tests.conftest import FENCE, fenced

P = Path("a.md")


def test_frontmatter_quoted_values() -> None:
    meta, end = parse_frontmatter(["---", 'verified: "2026-10-02"', "k: 'v'", "---"])
    assert meta == {"verified": "2026-10-02", "k": "v"} and end == 3


def test_frontmatter_absent() -> None:
    assert parse_frontmatter(["# hi"]) == ({}, -1)


def test_indented_fence_in_list_is_dedented() -> None:
    note = parse_note(P, f"- item\n  {FENCE}python\n  x = 1\n  {FENCE}\n")
    assert note.chunks[0].fence.body == ["x = 1"]
    assert note.chunks[0].fence.indent == "  "


def test_bare_fence_contents_are_inert() -> None:
    assert parse_note(P, f"{FENCE}\n{fenced('x=1')}{FENCE}\n").chunks == []


def test_four_backtick_fence_contains_triple() -> None:
    text = f"````text\n{FENCE}python\nx=1\n{FENCE}\n````\n"
    assert parse_note(P, text).chunks == []


def test_callout_fence_is_inert() -> None:
    assert parse_note(P, f"> {FENCE}python\n> x=1\n> {FENCE}\n").chunks == []


def test_skip_with_trailing_text() -> None:
    chunk = parse_note(P, fenced("# skip: excerpt of x.py\ny=2")).chunks[0]
    assert chunk.skip and chunk.first_line == "y=2"


def test_name_directive() -> None:
    chunk = parse_note(P, fenced("# name: setup\nx=1")).chunks[0]
    assert chunk.name == "setup" and chunk.label == "setup" and not chunk.skip


def test_name_and_skip_both() -> None:
    chunk = parse_note(P, fenced("# skip\n# name: ex\nx=1")).chunks[0]
    assert chunk.name == "ex" and chunk.skip


def test_unnamed_label_is_index() -> None:
    note = parse_note(P, fenced("a=1") + fenced("b=2"))
    assert [c.label for c in note.chunks] == ["1", "2"]
    assert note.runnable == note.chunks and note.stem == "a"


def test_duplicate_name_raises() -> None:
    with pytest.raises(NoterunError, match="duplicate"):
        parse_note(P, fenced("# name: a\nx=1") + fenced("# name: a\nx=2"))


@pytest.mark.parametrize("slug", ["1abc", "has-hyphen", "sp ace"])
def test_bad_slug_raises(slug: str) -> None:
    with pytest.raises(NoterunError, match="bad chunk name"):
        parse_note(P, fenced(f"# name: {slug}\nx=1"))


def test_unclosed_python_raises() -> None:
    with pytest.raises(NoterunError, match="line 1: unclosed"):
        parse_note(P, f"{FENCE}python\nx=1\n")


def test_unclosed_fence_after_chunk_raises() -> None:
    with pytest.raises(NoterunError, match="line 5"):
        parse_note(P, fenced("x=1") + f"\n{FENCE}text\nmore\n")


def test_unclosed_fence_before_any_chunk_is_not_error() -> None:
    assert parse_note(P, f"{FENCE}text\n{fenced('x=1')}").chunks == []


def test_owned_output_then_embeds() -> None:
    text = (
        fenced("x=1")
        + f"\n{FENCE}output\n1\n{FENCE}\n\n![[a-plot-1.png]]\n![[a-plot-2.png]]\nprose\n"
    )
    owned = parse_note(P, text).chunks[0].owned
    assert owned.output is not None and owned.output.body == ["1"]
    assert owned.embeds == ["a-plot-1.png", "a-plot-2.png"]
    assert owned.end == 10


def test_owned_embeds_only() -> None:
    owned = parse_note(P, fenced("x=1") + "![[a-1-1.png]]\n").chunks[0].owned
    assert owned.output is None and owned.embeds == ["a-1-1.png"] and owned.end == 4


def test_owned_nothing() -> None:
    chunk = parse_note(P, fenced("x=1") + "prose\n").chunks[0]
    assert chunk.owned.start == chunk.owned.end == chunk.fence.end


def test_output_two_blank_lines_not_owned() -> None:
    text = fenced("x=1") + f"\n\n{FENCE}output\n1\n{FENCE}\n"
    chunk = parse_note(P, text).chunks[0]
    assert chunk.owned.output is None and chunk.owned.end == chunk.fence.end


def test_hand_embed_with_path_or_size_not_owned() -> None:
    for line in ("![[dir/a-plot-1.png]]", "![[a-plot-1.png|300]]", "![[b-plot-1.png]]"):
        chunk = parse_note(P, fenced("x=1") + line + "\n").chunks[0]
        assert chunk.owned.embeds == []


@pytest.mark.parametrize(
    ("stem", "line"),
    [
        ("a", "![[a-b-1-1.png]]"),
        ("results-1", "![[results-1-figure-1-2026-09-28.png]]"),
    ],
)
def test_embed_with_extra_segments_not_owned(stem: str, line: str) -> None:
    chunk = parse_note(Path(f"{stem}.md"), fenced("x=1") + line + "\n").chunks[0]
    assert chunk.owned.embeds == [] and chunk.owned.end == chunk.fence.end


def test_unclosed_output_fence_raises() -> None:
    with pytest.raises(NoterunError, match="unclosed"):
        parse_note(P, fenced("x=1") + f"{FENCE}output\n1\nprose\n")


def test_normalize_rstrips_and_drops_trailing_blanks() -> None:
    assert normalize_output("\n a  \n\nb \n\n  \n") == "\n a\n\nb"


def test_embed_two_blank_lines_below_chunk_without_output_is_not_owned() -> None:
    chunk = parse_note(P, fenced("x=1") + "\n\n![[a-1-1.png]]\n").chunks[0]
    assert chunk.owned.embeds == [] and chunk.owned.end == chunk.fence.end


def test_case_insensitive_duplicate_name_raises() -> None:
    with pytest.raises(NoterunError, match="lines 1 and 5"):
        parse_note(P, fenced("# name: Plot\nx=1") + fenced("# name: plot\nx=2"))


def test_crlf_and_bom_are_detected() -> None:
    raw = "\ufeff---\r\nk: v\r\n---\r\n" + fenced("x=1").replace("\n", "\r\n")
    note = parse_note(P, raw)
    assert note.bom and note.newline == "\r\n" and note.meta == {"k": "v"}
    assert note.chunks[0].fence.body == ["x=1"]


def test_normalize_maps_carriage_returns() -> None:
    assert normalize_output("a\r\nb\rc") == "a\nb\nc"


def test_split_lines_crlf_only_when_majority() -> None:
    assert split_lines("a\r\nb\r\nc\n")[1] == "\r\n"
    assert split_lines("a\nb\nc\r\n")[1] == "\n"
