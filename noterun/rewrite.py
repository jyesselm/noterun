"""Compare stored regions with fresh results, rewrite owned regions, commit the note."""

from __future__ import annotations

import difflib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from noterun.note import Chunk, Note, NoterunError, normalize_output, read_raw, require_utf8
from noterun.runner import ChunkResult


@dataclass(frozen=True)
class Fresh:
    """Normalized stdout and figure file names for one chunk."""

    stdout: str
    figures: list[str]


@dataclass(frozen=True)
class Edit:
    """Replace lines [start, end) with `lines`."""

    start: int
    end: int
    lines: list[str]


def stored(chunk: Chunk) -> Fresh:
    """What the note currently holds under the chunk."""
    out = chunk.owned.output
    text = normalize_output("\n".join(out.body)) if out else ""
    return Fresh(text, list(chunk.owned.embeds))


def fresh_by_index(note: Note, results: list[ChunkResult]) -> dict[int, Fresh]:
    """Fresh state per chunk index; skip chunks show nothing."""
    by_index = {r.index: r for r in results}
    fresh = {}
    for chunk in note.chunks:
        r = by_index.get(chunk.index)
        if chunk.skip or r is None:
            fresh[chunk.index] = Fresh("", [])
        else:
            fresh[chunk.index] = Fresh(normalize_output(r.stdout), [p.name for p in r.figures])
    return fresh


def drift_lines(chunk: Chunk, fresh: Fresh) -> list[str]:
    """Diff lines between stored and fresh state."""
    old = stored(chunk)
    lines: list[str] = []
    if old.stdout != fresh.stdout:
        old_lines, new_lines = old.stdout.split("\n"), fresh.stdout.split("\n")
        lines += difflib.unified_diff(old_lines, new_lines, "stored", "now", lineterm="", n=1)
    if old.figures != fresh.figures:
        lines.append(f"figures: stored {old.figures} now {fresh.figures}")
    return lines


def _fence_ticks(stdout: str) -> str:
    """A backtick run longer than any leading run in the stdout lines."""
    longest = 0
    for line in stdout.split("\n"):
        text = line.lstrip()
        longest = max(longest, len(text) - len(text.lstrip("`")))
    return "`" * max(3, longest + 1)


def render_owned(indent: str, fresh: Fresh) -> list[str]:
    """The lines of an owned region: output fence, then embeds."""
    lines: list[str] = []
    if fresh.stdout:
        ticks = _fence_ticks(fresh.stdout)
        body = [indent + ln if ln else "" for ln in fresh.stdout.split("\n")]
        lines += ["", indent + ticks + "output", *body, indent + ticks]
    if fresh.figures:
        lines += ["", *(f"{indent}![[{name}]]" for name in fresh.figures)]
    return lines


def plan_edits(note: Note, fresh: dict[int, Fresh]) -> list[Edit]:
    """One edit per chunk whose owned region differs from the fresh state."""
    return [
        Edit(c.owned.start, c.owned.end, render_owned(c.fence.indent, fresh[c.index]))
        for c in note.chunks
        if stored(c) != fresh[c.index]
    ]


def apply_edits(lines: list[str], edits: list[Edit]) -> list[str]:
    """Splice edits bottom-up so earlier line numbers stay valid."""
    out = list(lines)
    for e in sorted(edits, key=lambda e: e.start, reverse=True):
        out[e.start : e.end] = e.lines
    return out


def stamp_verified(lines: list[str], frontmatter_end: int, today: str) -> list[str]:
    """Set `verified:` in the frontmatter, creating it if needed."""
    stamp = f"verified: {today}"
    if frontmatter_end < 0:
        return ["---", stamp, "---", *lines]
    out = list(lines)
    found = [i for i in range(1, frontmatter_end) if out[i].startswith("verified:")]
    if found:
        out[found[0]] = stamp
    else:
        out.insert(frontmatter_end, stamp)
    return out


def rewrite_lines(note: Note, fresh: dict[int, Fresh]) -> list[str]:
    """The note's lines with every changed owned region replaced."""
    return apply_edits(note.lines, plan_edits(note, fresh))


def render_text(note: Note, lines: list[str]) -> str:
    """Lines joined with the note's original newline and BOM."""
    return ("\ufeff" if note.bom else "") + note.newline.join(lines)


def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file beside the real file (through symlinks), keeping permissions."""
    target = Path(os.path.realpath(path))
    tmp = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=target.parent, prefix=".noterun-", delete=False
    )
    tmp_path = Path(tmp.name)
    try:
        with tmp:
            tmp.write(text)
        shutil.copymode(target, tmp_path)
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _move_figures(note: Note, results: list[ChunkResult]) -> None:
    """Move staged PNGs into `attachments/`, creating it only if needed."""
    target = note.path.parent / "attachments"
    for png in (p for r in results for p in r.figures):
        target.mkdir(exist_ok=True)
        os.replace(png, target / png.name)


def _delete_stale(note: Note, results: list[ChunkResult]) -> None:
    """Delete PNGs that were owned embeds and are no longer produced."""
    keep = {p.name for r in results for p in r.figures}
    for name in {n for c in note.chunks for n in c.owned.embeds} - keep:
        (note.path.parent / "attachments" / name).unlink(missing_ok=True)


def commit_note(note: Note, new_text: str, results: list[ChunkResult]) -> None:
    """Check, move figures, write the note atomically, then delete stale PNGs."""
    if read_raw(note.path) != note.text:
        raise NoterunError("note changed during run, nothing written")
    require_utf8(new_text)
    changed = new_text != note.text
    folder = os.path.dirname(os.path.realpath(note.path))
    if changed and not (os.access(note.path, os.W_OK) and os.access(folder, os.W_OK)):
        raise NoterunError(f"{note.path} or its folder is not writable, nothing written")
    _move_figures(note, results)
    if changed:
        write_atomic(note.path, new_text)
    _delete_stale(note, results)
