"""Command line: run, check, console, chunk, export, list, find."""

from __future__ import annotations

import argparse
import math
import shutil
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from noterun import rewrite as rw
from noterun import runner
from noterun.config import DEFAULT_TIMEOUT_S, Overrides, Runtime, declares_runtime, resolve_runtime
from noterun.export import write_notebook
from noterun.note import (
    Chunk,
    InterpreterError,
    Note,
    NoterunError,
    parse_frontmatter,
    parse_note,
    read_note,
    read_raw,
    require_utf8,
    split_lines,
)


def iter_notes(root: Path) -> list[Path]:
    """Markdown files under `root`, skipping dot-directories."""
    notes = sorted(p for p in root.rglob("*.md") if p.is_file())
    return [p for p in notes if not any(x.startswith(".") for x in p.relative_to(root).parts)]


def _err(message: str) -> None:
    """Print to stderr."""
    print(message, file=sys.stderr)


def _overrides(args: argparse.Namespace) -> Overrides:
    """Runtime flags as Overrides."""
    return Overrides(
        args.python, args.cwd, tuple(args.env), getattr(args, "timeout", DEFAULT_TIMEOUT_S)
    )


@contextmanager
def _staged(note: Note, runtime: Runtime) -> Iterator[Path]:
    """A staging dir that is removed on exit."""
    staging = runner.make_staging(note, runtime)
    try:
        yield staging
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _where(note: Note, chunk: Chunk) -> str:
    """`note.md:LINE` for a chunk."""
    return f"{note.path.name}:{chunk.fence.start + 1}"


def _failure_lines(note: Note, results: list[runner.ChunkResult]) -> list[str]:
    """Per-chunk status for a run that stopped early."""
    by_index = {r.index: r for r in results}
    lines = []
    for chunk in note.runnable:
        r = by_index.get(chunk.index)
        if r is None:
            status = "not run (earlier chunk failed)"
        elif r.error:
            status = f"ERROR\n{r.error}"
        else:
            status = "ok"
        lines.append(f"{_where(note, chunk)}: {status}")
    return lines


def _failed(note: Note, results: list[runner.ChunkResult]) -> bool:
    """True when a chunk errored or not every runnable chunk ran."""
    return any(r.error for r in results) or len(results) < len(note.runnable)


def _require_printable(results: list[runner.ChunkResult]) -> None:
    """Raise before printing when any stdout or traceback cannot be encoded as UTF-8."""
    for r in results:
        require_utf8(r.stdout)
        require_utf8(r.error or "")


def _execute(note: Note, runtime: Runtime) -> list[runner.ChunkResult]:
    """Run every runnable chunk in a throwaway staging dir."""
    runner.require_python(runtime)
    with _staged(note, runtime) as staging:
        results = runner.run_chunks(note, note.runnable, runtime, staging)
    _require_printable(results)
    return results


def _compare(note: Note, results: list[runner.ChunkResult]) -> tuple[str, list[str]]:
    """Compare stored regions with `results`; return (ok|DRIFT|ERROR, detail lines)."""
    if _failed(note, results):
        return "ERROR", _failure_lines(note, results)
    fresh = rw.fresh_by_index(note, results)
    lines, status = [], "ok"
    for chunk in note.chunks:
        diff = rw.drift_lines(chunk, fresh[chunk.index])
        status = "DRIFT" if diff else status
        if diff or not chunk.skip:
            lines += [f"{_where(note, chunk)}: {'DRIFT' if diff else 'ok'}", *diff]
    return status, lines


def _commit(note: Note, text: str, results: list[runner.ChunkResult]) -> bool:
    """Commit; report a changed-on-disk abort instead of raising."""
    try:
        rw.commit_note(note, text, results)
    except NoterunError as exc:
        _err(f"noterun: {exc}")
        return False
    return True


def cmd_run(args: argparse.Namespace) -> int:
    """Run a note and rewrite its owned regions."""
    note = read_note(Path(args.note))
    if not note.runnable:
        print(f"{note.path.name}: no runnable python fences")
        text = rw.render_text(note, rw.rewrite_lines(note, rw.fresh_by_index(note, [])))
        return 0 if text == note.text or _commit(note, text, []) else 1
    runtime = resolve_runtime(note, _overrides(args))
    runner.require_python(runtime)
    with _staged(note, runtime) as staging:
        results = runner.run_chunks(note, note.runnable, runtime, staging)
        if _failed(note, results):
            _require_printable(results)
            print("\n".join(_failure_lines(note, results)))
            print("noterun: FAILED, nothing written")
            return 1
        fresh = rw.fresh_by_index(note, results)
        today = date.today().isoformat()
        lines = rw.stamp_verified(rw.rewrite_lines(note, fresh), note.frontmatter_end, today)
        if not _commit(note, rw.render_text(note, lines), results):
            return 1
    for chunk in note.runnable:
        old = rw.stored(chunk)
        status = "ok"
        if old != fresh[chunk.index]:
            status = "updated" if old.stdout or old.figures else "output added"
        print(f"{_where(note, chunk)}: {status}")
    print(f"noterun: verified {today}")
    return 0


def _check_dir(root: Path, overrides: Overrides) -> int:
    """One status line per note that declares a runtime."""
    worst = 0
    for path in iter_notes(root):
        try:
            status, line = _check_one(path, root, overrides)
        except NoterunError as exc:
            _err(f"noterun: {path.relative_to(root)}: {exc}")
            continue
        if line:
            print(line)
            worst = worst or int(status in ("DRIFT", "ERROR"))
    return worst


def _check_one(path: Path, root: Path, overrides: Overrides) -> tuple[str, str]:
    """(status, line) for one note in a folder check; ('', '') skips it."""
    text = read_raw(path)
    meta, _ = parse_frontmatter(split_lines(text)[0])
    if not declares_runtime(path.parent, meta):
        return "", ""
    rel = path.relative_to(root)
    try:
        note = parse_note(path, text)
        if not note.runnable:
            return "", ""
        runtime = resolve_runtime(note, overrides)
        if runtime.python is None:
            return "unverified", f"unverified  {rel}"
        status = _compare(note, _execute(note, runtime))[0]
        return status, f"{status}  {rel}"
    except NoterunError as exc:
        return "ERROR", f"ERROR  {rel}  {exc}"


def _report(status: str, lines: list[str]) -> int:
    """Print detail lines then the status word; exit code 0 only for ok."""
    print("\n".join([*lines, status]))
    return 0 if status == "ok" else 1


def cmd_check(args: argparse.Namespace) -> int:
    """Rerun a note (or every note in a folder) and diff without writing."""
    path = Path(args.path)
    if path.is_dir():
        return _check_dir(path, _overrides(args))
    note = read_note(path)
    if not note.runnable:
        print(f"{note.path.name}: no runnable python fences")
        return _report(*_compare(note, []))
    runtime = resolve_runtime(note, _overrides(args))
    if runtime.python is None:
        print(f"{note.path.name}: unverified (no run-python)")
        return 0
    return _report(*_compare(note, _execute(note, runtime)))


def cmd_console(args: argparse.Namespace) -> int:
    """Run setup through a chunk and stay in an interactive session."""
    note = read_note(Path(args.note))
    return runner.open_console(note, resolve_runtime(note, _overrides(args)), args.to)


def _emit_chunk(chunks: list[Chunk], results: list[runner.ChunkResult]) -> tuple[int, bool]:
    """Report a chunk run; returns (exit code, target made figures)."""
    for chunk, earlier in zip(chunks[:-1], results):
        if earlier.error:
            _err(f"ERROR {chunk.label}\n{earlier.error}")
            return 1, False
        _err(f"ok {chunk.label}")
    target = results[-1]
    sys.stdout.write(target.stdout)
    for png in target.figures:
        _err(f"figure: {png}")
    if target.error:
        _err(f"ERROR {chunks[-1].label}\n{target.error}")
    return int(bool(target.error)), bool(target.figures)


def cmd_chunk(args: argparse.Namespace) -> int:
    """Run setup through one chunk and print only that chunk's stdout."""
    note = read_note(Path(args.note))
    runtime = resolve_runtime(note, _overrides(args))
    runner.require_python(runtime)
    chunks = runner.select_prefix(note, args.name)
    staging = runner.make_staging(note, runtime)
    keep = False
    try:
        results = runner.run_chunks(note, chunks, runtime, staging)
        _require_printable(results)
        code, keep = _emit_chunk(chunks, results)
        return code
    finally:
        if not keep:
            shutil.rmtree(staging, ignore_errors=True)


def cmd_export(args: argparse.Namespace) -> int:
    """Write a notebook beside the note."""
    print(f"wrote {write_notebook(read_note(Path(args.note)))}")
    return 0


def _notes(root: Path) -> Iterator[tuple[str, Note]]:
    """(relpath, note) for every parseable note; failures go to stderr."""
    for path in iter_notes(root):
        rel = str(path.relative_to(root))
        try:
            yield rel, read_note(path)
        except NoterunError as exc:
            _err(f"noterun: {rel}: {exc}")


def cmd_list(args: argparse.Namespace) -> int:
    """List chunks of a note or folder, or the runnable labels of one note."""
    path = Path(args.path)
    if not path.exists():
        raise NoterunError(f"no such path: {path}")
    if args.names and path.is_dir():
        raise NoterunError("--names needs a single note")
    if args.names:
        note = read_note(path)
        named = [c.name for c in note.runnable if c.name]
        for label in named + [str(c.index) for c in note.runnable if not c.name]:
            print(label)
        return 0
    pairs = list(_notes(path)) if path.is_dir() else [(path.name, read_note(path))]
    for rel, note in pairs:
        for c in note.chunks:
            skip = "[skip] " if c.skip else ""
            print(f"{rel}:{c.fence.start + 1}  {c.label}  {skip}{c.first_line}")
    return 0


def _find_in_note(rel: str, note: Note, value: str) -> int:
    """Print stored output lines containing `value`; return the hit count."""
    hits = 0
    for c in note.chunks:
        out = c.owned.output
        if out is None:
            continue
        for i, line in enumerate(out.body):
            if value in line:
                print(f"{rel}:{out.start + 2 + i}  {c.label}  {line.strip()}")
                hits += 1
    return hits


def cmd_find(args: argparse.Namespace) -> int:
    """Search stored output lines for a substring."""
    root = Path(args.dir)
    if not root.is_dir():
        raise NoterunError(f"no such folder: {root}")
    hits = sum(_find_in_note(rel, note, args.value) for rel, note in _notes(root))
    return 0 if hits else 1


def _timeout(value: str) -> float:
    """argparse type: a finite, positive number of seconds."""
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive number of seconds: {value}")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    """The argument parser with seven subcommands."""
    flags = argparse.ArgumentParser(add_help=False)
    flags.add_argument("--python", help="override run-python")
    flags.add_argument("--cwd", help="override run-cwd")
    flags.add_argument("--env", action="append", default=[], metavar="KEY=VALUE", help="add to run-env")
    timed = argparse.ArgumentParser(add_help=False, parents=[flags])
    timed.add_argument("--timeout", type=_timeout, default=DEFAULT_TIMEOUT_S, help="seconds")
    parser = argparse.ArgumentParser(prog="noterun", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", parents=[timed], help="run every chunk, write outputs into the note")
    run.add_argument("note")
    check = sub.add_parser("check", parents=[timed], help="rerun and diff, write nothing")
    check.add_argument("path", help="a note or a folder")
    console = sub.add_parser("console", parents=[flags], help="run chunks, then stay in IPython")
    console.add_argument("note")
    console.add_argument("--to", metavar="NAME", help="stop after this chunk")
    chunk = sub.add_parser("chunk", parents=[timed], help="print one chunk's output, write nothing")
    chunk.add_argument("note")
    chunk.add_argument("name")
    export = sub.add_parser("export", help="write NOTE.ipynb beside the note")
    export.add_argument("note")
    listing = sub.add_parser("list", help="list the chunks of a note or folder")
    listing.add_argument("path")
    listing.add_argument("--names", action="store_true", help="print only chunk names")
    find = sub.add_parser("find", help="search stored outputs for a value")
    find.add_argument("value")
    find.add_argument("dir")
    return parser


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "run": cmd_run,
    "check": cmd_check,
    "console": cmd_console,
    "chunk": cmd_chunk,
    "export": cmd_export,
    "list": cmd_list,
    "find": cmd_find,
}


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    args = build_parser().parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except InterpreterError as exc:
        _err(f"noterun: ERROR {exc}")
        return 1
    except (NoterunError, OSError) as exc:
        _err(f"noterun: {exc}")
        return 2
