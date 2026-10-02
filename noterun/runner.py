"""Launch the note's declared interpreter: chunk runs, staging, console, prefix selection."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from noterun.config import Runtime, child_environ
from noterun.note import Chunk, InterpreterError, Note, NoterunError

DRIVER_SOURCE = Path(__file__).with_name("_driver.py").read_text(encoding="utf-8")
STAGING_SUFFIX = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class ChunkResult:
    """What one executed chunk produced."""

    index: int
    stdout: str
    error: str | None
    figures: list[Path]


def require_python(runtime: Runtime) -> str:
    """The interpreter path, or a usage error naming where to declare it."""
    if runtime.python is None:
        raise NoterunError("no run-python: set it in .noterun.toml, frontmatter, or --python")
    return runtime.python


def _require_cwd(runtime: Runtime) -> None:
    """Fail with the cwd's name when it is not a directory."""
    if not runtime.cwd.is_dir():
        raise InterpreterError(f"run-cwd is not a directory: {runtime.cwd}")


def run_chunks(
    note: Note, chunks: list[Chunk], runtime: Runtime, staging: Path
) -> list[ChunkResult]:
    """Run `chunks` in the note's interpreter; figures land in `staging`."""
    python = require_python(runtime)
    _require_cwd(runtime)
    result_path = staging / "result.json"
    request = {
        "chunks": [{"index": c.index, "label": c.label, "code": c.code} for c in chunks],
        "figure_dir": str(staging),
        "stem": note.stem,
        "result_path": str(result_path),
    }
    env = {**child_environ(runtime), "MPLBACKEND": "Agg"}
    try:
        proc = subprocess.run(
            [python, "-c", DRIVER_SOURCE],
            input=json.dumps(request),
            text=True,
            cwd=runtime.cwd,
            env=env,
            timeout=runtime.timeout_s,
        )
    except OSError as exc:
        raise InterpreterError(f"cannot launch {python}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise InterpreterError(f"timed out after {runtime.timeout_s:g} s") from exc
    try:
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InterpreterError(f"interpreter exited {proc.returncode} without results") from exc
    return [
        ChunkResult(r["index"], r["stdout"], r["error"], [Path(p) for p in r["figures"]])
        for r in raw
    ]


def _is_stale(path: Path, prefix: str, max_age_s: float) -> bool:
    """True for a staging directory of this note that is older than `max_age_s`."""
    suffix = path.name[len(prefix) :]
    if not (path.name.startswith(prefix) and STAGING_SUFFIX.match(suffix) and path.is_dir()):
        return False
    try:
        return time.time() - path.stat().st_mtime >= max_age_s
    except FileNotFoundError:
        return False


def clear_staging(note: Note, max_age_s: float) -> None:
    """Remove this note's staging dirs older than `max_age_s` seconds."""
    prefix = f".noterun-{note.stem}-"
    for path in note.path.parent.iterdir():
        if _is_stale(path, prefix, max_age_s):
            shutil.rmtree(path, ignore_errors=True)


def make_staging(note: Note, runtime: Runtime) -> Path:
    """A fresh dot-directory beside the note, on the same filesystem as `attachments/`."""
    clear_staging(note, runtime.timeout_s)
    made = tempfile.mkdtemp(dir=note.path.parent, prefix=f".noterun-{note.stem}-")
    return Path(made).absolute()


def _matches(chunk: Chunk, target: str) -> bool:
    """True when `target` is the chunk's 1-based index or its name."""
    return chunk.index == int(target) if target.isdecimal() else chunk.name == target


def select_prefix(note: Note, target: str | None) -> list[Chunk]:
    """Runnable chunks from the first through `target` (all when None)."""
    if target is None:
        return note.runnable
    found = [c for c in note.chunks if _matches(c, target)]
    if not found:
        labels = ", ".join(c.label for c in note.runnable)
        raise NoterunError(f"no chunk {target!r}; runnable: {labels}")
    chunk = found[0]
    if chunk.skip:
        raise NoterunError(f"chunk {chunk.label} (line {chunk.fence.start + 1}) is # skip")
    return [c for c in note.runnable if c.index <= chunk.index]


def console_command(python: str, script: str) -> list[str]:
    """Argv that runs `script` then stays interactive, in IPython when available."""
    try:
        probe = subprocess.run([python, "-c", "import IPython"], capture_output=True)
    except OSError as exc:
        raise InterpreterError(f"cannot launch {python}: {exc}") from exc
    if probe.returncode == 0:
        return [python, "-m", "IPython", "--no-banner", "-i", "-c", script]
    print("noterun: IPython is missing in that interpreter; using plain python", file=sys.stderr)
    return [python, "-i", "-c", script]


def open_console(note: Note, runtime: Runtime, target: str | None) -> int:
    """Run setup through `target` and leave the user in an interactive session."""
    python = require_python(runtime)
    _require_cwd(runtime)
    script = "\n\n".join(c.code for c in select_prefix(note, target))
    command = console_command(python, script)
    return subprocess.call(command, cwd=runtime.cwd, env=child_environ(runtime))
