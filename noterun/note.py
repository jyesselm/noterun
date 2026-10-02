"""Parse an Obsidian note into chunks; this docstring is the format spec.

Fences: indent + 3+ backticks or tildes + optional info word, closed by the same character, at
least as long. Bare fences are recorded so their contents are inert; `>` lines never match. An
unclosed fence at or after the first python fence raises. Chunks are ```python fences; leading
`# name: <slug>` and `# skip...` lines are directives. Under a chunk the tool owns: at most one
blank line, a closed ```output fence, one blank line, then `![[<stem>-<label>-<k>.png]]` lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

FENCE_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<ticks>`{3,}|~{3,})(?P<lang>[^\s`]*)")
NAME_RE = re.compile(r"^#\s*name:\s*(.*?)\s*$")
SKIP_RE = re.compile(r"^#\s*skip\b.*$")
SLUG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
META_RE = re.compile(r"^([\w-]+):\s*(.*)$")


class NoterunError(Exception):
    """Usage, config or parse problem (exit 2)."""


class InterpreterError(NoterunError):
    """The note's interpreter crashed, timed out or left no result (exit 1)."""


@dataclass(frozen=True)
class Fence:
    """One fenced block; `end` is exclusive, `body` has the indent removed."""

    start: int
    end: int
    indent: str
    ticks: str
    lang: str
    body: list[str]
    closed: bool


@dataclass(frozen=True)
class Owned:
    """The tool-owned region under a python fence (empty when start == end)."""

    start: int
    end: int
    output: Fence | None
    embeds: list[str]


@dataclass(frozen=True)
class Chunk:
    """A python fence plus its directives and owned region."""

    index: int
    name: str | None
    skip: bool
    fence: Fence
    owned: Owned

    @property
    def label(self) -> str:
        """Name if set, else the 1-based index as text."""
        return self.name or str(self.index)

    @property
    def code(self) -> str:
        """Fence body; directives stay so traceback lines match fence lines."""
        return "\n".join(self.fence.body)

    @property
    def first_line(self) -> str:
        """First body line that is neither blank nor a directive."""
        body = [
            ln.strip() for ln in self.fence.body if not NAME_RE.match(ln) and not SKIP_RE.match(ln)
        ]
        return next((ln for ln in body if ln), "")


@dataclass(frozen=True)
class Note:
    """A parsed note; `text` is the raw file text, `lines` the BOM-free, LF-split text."""

    path: Path
    text: str
    lines: list[str]
    meta: dict[str, str]
    frontmatter_end: int
    chunks: list[Chunk]
    newline: str = "\n"
    bom: bool = False

    @property
    def stem(self) -> str:
        """File name without extension."""
        return self.path.stem

    @property
    def runnable(self) -> list[Chunk]:
        """Chunks that are not skipped."""
        return [c for c in self.chunks if not c.skip]


def parse_frontmatter(lines: list[str]) -> tuple[dict[str, str], int]:
    """Flat frontmatter keys and the line index of the closing `---` (-1 if none)."""
    if not lines or lines[0].strip() != "---":
        return {}, -1
    meta: dict[str, str] = {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return meta, i
        m = META_RE.match(lines[i])
        if m:
            meta[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return {}, -1


def _closing_line(lines: list[str], begin: int, char: str, size: int) -> int | None:
    """Index of the first closing line at or after `begin`, or None."""
    for j in range(begin, len(lines)):
        s = lines[j].strip()
        if len(s) >= size and set(s) == {char}:
            return j
    return None


def find_fences(lines: list[str], start: int) -> list[Fence]:
    """Every fence from line `start` on; nothing inside a fence is rescanned."""
    fences: list[Fence] = []
    i = start
    while i < len(lines):
        m = FENCE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        indent, ticks = m.group("indent"), m.group("ticks")
        close = _closing_line(lines, i + 1, ticks[0], len(ticks))
        stop = len(lines) if close is None else close
        body = [ln[len(indent) :] if ln.startswith(indent) else ln for ln in lines[i + 1 : stop]]
        end = len(lines) if close is None else close + 1
        fence = Fence(i, end, indent, ticks, m.group("lang").lower(), body, close is not None)
        fences.append(fence)
        i = end
    return fences


def parse_directives(body: list[str]) -> tuple[str | None, bool]:
    """(name, skip) from the leading run of directive lines."""
    name, skip = None, False
    for line in body:
        if m := NAME_RE.match(line):
            name = m.group(1)
        elif SKIP_RE.match(line):
            skip = True
        else:
            break
    return name, skip


def _after_blank(lines: list[str], pos: int) -> int:
    """`pos`, advanced past one blank line if there is one."""
    return pos + 1 if pos < len(lines) and not lines[pos].strip() else pos


def find_owned(lines: list[str], fence: Fence, fences: list[Fence], stem: str) -> Owned:
    """The owned region directly under a python fence."""
    embed_re = re.compile(
        rf"^[ \t]*!\[\[(?P<file>{re.escape(stem)}-[A-Za-z0-9_]+-\d+\.png)\]\]\s*$"
    )
    pos = _after_blank(lines, fence.end)
    output = next((f for f in fences if f.start == pos and f.lang == "output"), None)
    end = fence.end
    if output is not None:
        pos = _after_blank(lines, output.end)
        end = output.end
    embeds: list[str] = []
    while pos < len(lines) and (m := embed_re.match(lines[pos])):
        embeds.append(m.group("file"))
        pos += 1
        end = pos
    return Owned(fence.end, end, output, embeds)


def _check_closed(fences: list[Fence]) -> None:
    """Raise for an unclosed fence at or after the first python fence."""
    python = [f.start for f in fences if f.lang == "python"]
    for f in fences:
        if python and f.start >= python[0] and not f.closed:
            raise NoterunError(f"line {f.start + 1}: unclosed {f.ticks} fence")


def _build_chunks(lines: list[str], fences: list[Fence], stem: str) -> list[Chunk]:
    """Chunks for every python fence, with directives validated."""
    chunks: list[Chunk] = []
    seen: dict[str, int] = {}  # lower-cased name -> line (macOS file names ignore case)
    for fence in (f for f in fences if f.lang == "python"):
        name, skip = parse_directives(fence.body)
        line = fence.start + 1
        if name is not None and not SLUG_RE.match(name):
            raise NoterunError(f"line {line}: bad chunk name {name!r}")
        if name is not None and name.lower() in seen:
            first = seen[name.lower()]
            raise NoterunError(f"lines {first} and {line}: duplicate chunk name {name!r}")
        if name is not None:
            seen[name.lower()] = line
        owned = find_owned(lines, fence, fences, stem)
        chunks.append(Chunk(len(chunks) + 1, name, skip, fence, owned))
    return chunks


def split_lines(text: str) -> tuple[list[str], str, bool]:
    """(LF-split lines, newline to write back, had BOM). CRLF wins only when in the majority."""
    bom = text.startswith("\ufeff")
    body = text.removeprefix("\ufeff")
    newline = "\r\n" if body.count("\r\n") * 2 > body.count("\n") else "\n"
    return body.replace("\r\n", "\n").split("\n"), newline, bom


def require_utf8(text: str) -> None:
    """Raise NoterunError when `text` (for example stdout with surrogates) cannot be encoded."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise NoterunError(f"output is not valid UTF-8 text: {exc.reason}") from exc


def parse_note(path: Path, text: str) -> Note:
    """Parse raw note text; raises NoterunError on malformed notes.

    BOM is stripped and kept; the note is written back with CRLF only if most lines use CRLF.
    """
    lines, newline, bom = split_lines(text)
    meta, fm_end = parse_frontmatter(lines)
    fences = find_fences(lines, fm_end + 1)
    _check_closed(fences)
    chunks = _build_chunks(lines, fences, path.stem)
    return Note(path, text, lines, meta, fm_end, chunks, newline, bom)


def read_raw(path: Path) -> str:
    """The file's exact text (no newline translation); unreadable files raise NoterunError."""
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise NoterunError(f"cannot read {path}: {exc}") from exc


def read_note(path: Path) -> Note:
    """Read and parse a note file."""
    return parse_note(path, read_raw(path))


def normalize_output(text: str) -> str:
    """Map CR and CRLF to LF, rstrip every line, drop trailing blank lines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).rstrip("\n")
