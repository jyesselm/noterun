"""Driver and runner tests."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import noterun._driver as driver
from noterun import runner
from noterun.config import Runtime
from noterun.note import InterpreterError, NoterunError, parse_note
from noterun.runner import make_staging, run_chunks, select_prefix
from tests.conftest import fenced


def request(*codes: str, tmp: Path | None = None) -> dict:
    """A driver request for the given chunk sources."""
    chunks = [{"index": i, "label": str(i), "code": c} for i, c in enumerate(codes, 1)]
    return {"chunks": chunks, "figure_dir": str(tmp or "."), "stem": "n"}


def test_driver_is_python38_syntax() -> None:
    ast.parse(Path(driver.__file__).read_text(), feature_version=(3, 8))


def test_driver_imports_nothing_from_package() -> None:
    tree = ast.parse(Path(driver.__file__).read_text())
    names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    names += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not [n for n in names if n.startswith("noterun")]


def test_run_request_shares_namespace() -> None:
    res = driver.run_request(request("x = 2", "print(x * 3)"))
    assert res[1]["stdout"] == "6\n" and res[1]["error"] is None


def test_run_request_stops_at_first_error() -> None:
    res = driver.run_request(request("raise ValueError('boom')", "print(1)"))
    assert len(res) == 1 and "ValueError: boom" in res[0]["error"]
    assert "raise ValueError('boom')" in res[0]["error"]


def test_stdout_captured_per_chunk() -> None:
    res = driver.run_request(request("print('a')", "print('b')"))
    assert [r["stdout"] for r in res] == ["a\n", "b\n"]


def test_save_figures_without_pyplot_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "matplotlib.pyplot", raising=False)
    assert driver.save_figures(".", "n", "1") == []


def test_save_figures_writes_png(tmp_path: Path) -> None:
    plt = pytest.importorskip("matplotlib.pyplot")
    plt.figure()
    paths = driver.save_figures(str(tmp_path), "n", "plot")
    assert [Path(p).name for p in paths] == ["n-plot-1.png"] and Path(paths[0]).exists()


def build(tmp_path: Path, *codes: str, python: str = sys.executable, timeout: float = 60):
    """A note on disk plus a runtime and staging dir."""
    path = tmp_path / "n.md"
    note = parse_note(path, "".join(fenced(c) for c in codes))
    runtime = Runtime(python, tmp_path, {}, timeout)
    return note, runtime, make_staging(note, runtime)


def test_run_chunks_end_to_end(tmp_path: Path) -> None:
    note, runtime, staging = build(tmp_path, "x = 4", "print(x)")
    results = run_chunks(note, note.runnable, runtime, staging)
    assert [r.stdout for r in results] == ["", "4\n"] and results[1].error is None


def test_stray_fd_write_does_not_corrupt_result(tmp_path: Path) -> None:
    note, runtime, staging = build(tmp_path, "import os; os.write(1, b'x\\n'); print('ok')")
    assert run_chunks(note, note.runnable, runtime, staging)[0].stdout == "ok\n"


def test_missing_interpreter_raises_interpreter_error(tmp_path: Path) -> None:
    note, runtime, staging = build(tmp_path, "x=1", python="/nonexistent/python")
    with pytest.raises(InterpreterError):
        run_chunks(note, note.runnable, runtime, staging)


def test_no_python_raises_usage_error(tmp_path: Path) -> None:
    note, runtime, staging = build(tmp_path, "x=1")
    with pytest.raises(NoterunError, match="run-python"):
        run_chunks(note, note.runnable, Runtime(None, tmp_path, {}), staging)


def test_timeout_raises(tmp_path: Path) -> None:
    note, runtime, staging = build(tmp_path, "import time; time.sleep(5)", timeout=0.5)
    with pytest.raises(InterpreterError, match="timed out"):
        run_chunks(note, note.runnable, runtime, staging)


def test_missing_result_file_raises(tmp_path: Path) -> None:
    note, runtime, staging = build(tmp_path, "import os; os._exit(3)")
    with pytest.raises(InterpreterError, match="exited 3"):
        run_chunks(note, note.runnable, runtime, staging)


def prefix_note():
    """Four chunks: setup, plot, a skip, and an unnamed one."""
    text = fenced("# name: setup\n1") + fenced("# name: plot\n2")
    return parse_note(Path("n.md"), text + fenced("# skip\n3") + fenced("4"))


def test_select_prefix_by_name() -> None:
    assert [c.label for c in select_prefix(prefix_note(), "plot")] == ["setup", "plot"]


def test_select_prefix_by_index() -> None:
    assert [c.index for c in select_prefix(prefix_note(), "4")] == [1, 2, 4]


def test_select_prefix_unknown_target_raises() -> None:
    with pytest.raises(NoterunError, match="runnable: setup, plot, 4"):
        select_prefix(prefix_note(), "nope")


def test_select_prefix_skip_target_raises() -> None:
    with pytest.raises(NoterunError, match="line 9"):
        select_prefix(prefix_note(), "3")


def test_select_prefix_none_is_all_runnable() -> None:
    assert len(select_prefix(prefix_note(), None)) == 3


@pytest.mark.parametrize(("code", "head"), [(0, "-m"), (1, "-i")])
def test_console_command(monkeypatch: pytest.MonkeyPatch, code: int, head: str) -> None:
    done = subprocess.CompletedProcess([], code)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: done)
    argv = runner.console_command("py", "x=1")
    if code == 0:
        assert argv == ["py", "-m", "IPython", "--no-banner", "-i", "-c", "x=1"]
    else:
        assert argv == ["py", "-i", "-c", "x=1"]


def test_open_console_passes_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    note, runtime, _ = build(tmp_path, "x=1")
    seen: dict = {}

    def fake_call(argv: list[str], **kw: object) -> int:
        seen.update(kw)
        return 7

    monkeypatch.setattr(runner, "console_command", lambda p, s: ["py", s])
    monkeypatch.setattr(subprocess, "call", fake_call)
    assert runner.open_console(note, runtime, None) == 7
    assert "MPLBACKEND" not in seen["env"] or seen["env"]["MPLBACKEND"] == os.environ.get(
        "MPLBACKEND"
    )


def test_relative_note_path_gives_absolute_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path)
    note = parse_note(Path("n.md"), fenced("print(1)"))
    runtime = Runtime(sys.executable, tmp_path / "work", {}, 60)
    staging = make_staging(note, runtime)
    assert staging.is_absolute()
    assert run_chunks(note, note.runnable, runtime, staging)[0].stdout == "1\n"


def test_clear_staging_handles_bracket_stem_and_races(tmp_path: Path) -> None:
    note = parse_note(tmp_path / "a[1].md", fenced("x=1"))
    old = tmp_path / ".noterun-a[1]-old123"
    other = tmp_path / ".noterun-a[1]-file"
    old.mkdir()
    other.write_text("not a dir")
    past = os.stat(old).st_mtime - 7200
    os.utime(old, (past, past))
    runner.clear_staging(note, 60)
    assert not old.exists() and other.exists()
    assert not runner._is_stale(tmp_path / ".noterun-a[1]-gone", ".noterun-a[1]-", 0)
