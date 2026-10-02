"""End-to-end tests through main(argv)."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from noterun.cli import main
from tests.conftest import FENCE, fenced

Maker = Callable[..., Path]
F = FENCE
PLOT = "import matplotlib.pyplot as plt\nplt.plot([1, 2])"


def out_fence(text: str) -> str:
    """An output fence as note text."""
    return f"\n{F}output\n{text}\n{F}\n"


def go(capsys: pytest.CaptureFixture[str], *argv: object) -> tuple[int, str, str]:
    """Run main and return (exit code, stdout, stderr)."""
    code = main([str(a) for a in argv])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def leftovers(folder: Path) -> list[str]:
    """Staging dirs left in a folder."""
    return [p.name for p in folder.iterdir() if p.name.startswith(".noterun-")]


def test_run_writes_output_fences_and_stamps_verified(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('hi')"))
    code, out, _ = go(capsys, "run", path)
    text = path.read_text()
    assert code == 0 and "output added" in out and "noterun: verified" in out
    assert f"{F}output\nhi\n{F}" in text and "verified: 2026-01-01" not in text


def test_run_with_zero_runnable_chunks(make_note: Maker, capsys) -> None:
    body = fenced("# skip\nx = 1")
    path = make_note(body, python=None)
    before = path.read_bytes()
    code, out, _ = go(capsys, "run", path)
    assert code == 0 and "no runnable python fences" in out and path.read_bytes() == before
    path = make_note(body + out_fence("stale"), python=None, name="b.md")
    code, _, _ = go(capsys, "run", path)
    text = path.read_text()
    assert code == 0 and "stale" not in text and "verified: 2026-01-01" in text
    assert go(capsys, "check", path)[0] == 0


def test_check_zero_runnable_with_owned_region_drifts(make_note: Maker, capsys) -> None:
    path = make_note(fenced("# skip\nx = 1") + out_fence("stale"), python=None)
    code, out, _ = go(capsys, "check", path)
    assert code == 1 and "DRIFT" in out


def test_run_twice_is_idempotent(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('hi')"))
    go(capsys, "run", path)
    first = path.read_bytes()
    assert go(capsys, "run", path)[0] == 0 and path.read_bytes() == first


def test_check_reports_drift_with_diff_exit_1(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('hi')") + out_fence("bye"))
    before = path.read_bytes()
    code, out, _ = go(capsys, "check", path)
    assert code == 1 and "DRIFT" in out and "-bye" in out and "+hi" in out
    assert path.read_bytes() == before and not leftovers(path.parent)


def test_check_ok(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('hi')") + out_fence("hi"))
    code, out, _ = go(capsys, "check", path)
    assert code == 0 and out.strip().endswith("ok")


def test_indented_fence_in_list_gets_indented_output_fence(make_note: Maker, capsys) -> None:
    path = make_note(f"- item\n  {F}python\n  print('x')\n  {F}\n")
    go(capsys, "run", path)
    assert f"\n  {F}output\n  x\n  {F}" in path.read_text()


def test_skip_chunk_not_executed(make_note: Maker, capsys) -> None:
    path = make_note(fenced("# skip\nraise SystemExit(3)") + fenced("print(1)"))
    assert go(capsys, "run", path)[0] == 0


def test_skip_chunk_stale_output_removed_on_run_drift_on_check(make_note: Maker, capsys) -> None:
    body = fenced("print(1)") + out_fence("1") + fenced("# skip\nx=1") + out_fence("s")
    path = make_note(body)
    assert go(capsys, "check", path)[0] == 1
    assert go(capsys, "run", path)[0] == 0 and "\ns\n" not in path.read_text()
    assert go(capsys, "check", path)[0] == 0


def test_stale_output_refreshed(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('new')") + out_fence("old"))
    go(capsys, "run", path)
    assert "new" in path.read_text() and "old" not in path.read_text()


def test_chunk_without_stdout_gets_no_fence(make_note: Maker, capsys) -> None:
    path = make_note(fenced("x = 1") + out_fence("old"))
    go(capsys, "run", path)
    assert "output" not in path.read_text()


def test_error_stops_run_writes_nothing_exit_1(make_note: Maker, capsys) -> None:
    pytest.importorskip("matplotlib")
    body = fenced(PLOT) + fenced("raise ValueError('boom')") + fenced("print(3)")
    path = make_note(body)
    before = path.read_bytes()
    code, out, _ = go(capsys, "run", path)
    assert code == 1 and "ValueError: boom" in out and "not run" in out
    assert path.read_bytes() == before and not (path.parent / "attachments").exists()
    assert not leftovers(path.parent)


def test_unclosed_output_fence_raises_and_writes_nothing(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print(1)") + f"{F}output\n1\nprose\n")
    before = path.read_bytes()
    code, _, err = go(capsys, "run", path)
    assert code == 2 and "line" in err and path.read_bytes() == before


def test_run_aborts_when_note_changed_during_run(make_note: Maker, tmp_path: Path, capsys) -> None:
    target = tmp_path / "note.md"
    path = make_note(fenced(f"open({str(target)!r}, 'a').write('EXTRA')\nprint(1)"))
    code, out, err = go(capsys, "run", path)
    text = path.read_text()
    assert code == 1 and "note changed during run" in out + err
    assert text.endswith("EXTRA") and "output" not in text and "2026-01-01" in text


def test_run_without_python_exit_2(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print(1)"), python=None)
    assert go(capsys, "run", path)[0] == 2


def test_check_without_python_reports_unverified_exit_0(make_note: Maker, capsys) -> None:
    code, out, _ = go(capsys, "check", make_note(fenced("print(1)"), python=None))
    assert code == 0 and "unverified" in out


def test_check_dir_one_line_per_note(make_note: Maker, tmp_path: Path, capsys) -> None:
    make_note(fenced("print('a')") + out_fence("a"), name="ok.md")
    make_note(fenced("print('a')") + out_fence("b"), name="drift.md")
    (tmp_path / "unver.md").write_text(f"---\nrun-env: A=1\n---\n{fenced('print(1)')}")
    make_note("just prose\n", name="nochunks.md")
    make_note(f"{F}python\nx=1\n", name="broken.md")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "bad.md").write_text(f"{F}python\nx=1\n")  # no runtime: absent
    code, out, _ = go(capsys, "check", tmp_path)
    lines = sorted(out.strip().splitlines())
    assert code == 1 and len(lines) == 4
    assert lines[0].startswith("DRIFT  drift.md") and lines[1].startswith("ERROR  broken.md")
    assert lines[2] == "ok  ok.md" and lines[3] == "unverified  unver.md"


def test_check_dir_missing_exit_2(tmp_path: Path, capsys) -> None:
    assert go(capsys, "check", tmp_path / "nope")[0] == 2


def test_list_dir(make_note: Maker, tmp_path: Path, capsys) -> None:
    make_note(fenced("# name: setup\nx = 1") + fenced("# skip\ny = 2"), name="a.md")
    code, out, _ = go(capsys, "list", tmp_path)
    lines = out.splitlines()
    assert code == 0
    assert lines[0] == "a.md:6  setup  x = 1"
    assert "[skip] y = 2" in lines[1]
    assert go(capsys, "list", tmp_path / "a.md")[1].startswith("a.md:")


def test_list_continues_past_unparseable_note(make_note: Maker, tmp_path: Path, capsys) -> None:
    make_note(fenced("x=1"), name="good.md")
    make_note(f"{F}python\nx=1\n", name="bad.md")
    code, out, err = go(capsys, "list", tmp_path)
    assert code == 0 and "good.md" in out and "bad.md" in err


def test_list_names_prints_bare_labels_for_single_note(
    make_note: Maker, tmp_path: Path, capsys
) -> None:
    body = (
        fenced("# name: setup\n1")
        + fenced("2")
        + fenced("# skip\n3")
        + fenced("# name: plot\n4")
        + fenced("5")
    )
    path = make_note(body)
    assert go(capsys, "list", path, "--names")[1] == "setup\nplot\n2\n5\n"
    assert go(capsys, "list", tmp_path, "--names")[0] == 2
    assert go(capsys, "list", tmp_path / "nope.md")[0] == 2


def test_find_reports_note_chunk_and_line(make_note: Maker, tmp_path: Path, capsys) -> None:
    make_note(fenced("# name: s\nprint(1)") + out_fence("a\n  value 0.73 here"), name="a.md")
    code, out, _ = go(capsys, "find", "0.73", tmp_path)
    assert code == 0 and out == "a.md:13  s  value 0.73 here\n"


def test_find_no_match_exit_1(make_note: Maker, tmp_path: Path, capsys) -> None:
    make_note(fenced("print(1)") + out_fence("1"))
    assert go(capsys, "find", "zzz", tmp_path)[0] == 1
    assert go(capsys, "find", "zzz", tmp_path / "nope")[0] == 2


def test_find_continues_past_unparseable_note(make_note: Maker, tmp_path: Path, capsys) -> None:
    make_note(f"{F}python\nx=1\n", name="bad.md")
    make_note(fenced("print(1)") + out_fence("needle"), name="good.md")
    code, out, err = go(capsys, "find", "needle", tmp_path)
    assert code == 0 and "good.md" in out and "bad.md" in err


def test_figure_capture_creates_png_and_embed_then_removes_stale(
    make_note: Maker, tmp_path: Path, capsys
) -> None:
    pytest.importorskip("matplotlib")
    att = tmp_path / "attachments"
    att.mkdir()
    (att / "note-x-1-1.png").write_bytes(b"hand")
    hand = "![[note-x-1-1.png]]\n![[other.png|300]]\n"
    path = make_note(fenced(f"# name: plot\n{PLOT}") + hand)
    go(capsys, "run", path)
    text = path.read_text()
    assert (att / "note-plot-1.png").exists() and "![[note-plot-1.png]]" in text
    assert "![[note-x-1-1.png]]" in text and "![[other.png|300]]" in text
    path.write_text(text.replace(PLOT, "x = 1"))
    go(capsys, "run", path)
    text = path.read_text()
    assert not (att / "note-plot-1.png").exists() and "note-plot-1" not in text
    assert "![[note-x-1-1.png]]" in text and (att / "note-x-1-1.png").read_bytes() == b"hand"


def test_check_does_not_touch_attachments(make_note: Maker, tmp_path: Path, capsys) -> None:
    pytest.importorskip("matplotlib")
    path = make_note(fenced(f"# name: plot\n{PLOT}"))
    go(capsys, "run", path)
    png = tmp_path / "attachments" / "note-plot-1.png"
    png.unlink()
    assert go(capsys, "check", path)[0] == 0 and not png.exists()
    path.write_text(path.read_text().replace(PLOT, "x = 1"))
    code, out, _ = go(capsys, "check", path)
    assert code == 1 and "figures:" in out and not png.exists()


def test_config_override_order_via_cli(make_note: Maker, tmp_path: Path, capsys) -> None:
    (tmp_path / ".noterun.toml").write_text('run-python = "/bogus/python"\n')
    path = make_note(fenced("print(1)"))
    assert go(capsys, "run", path)[0] == 0
    assert go(capsys, "run", path, "--python", "/nonexistent")[0] == 1


def test_export_command_writes_ipynb(make_note: Maker, capsys) -> None:
    path = make_note("# T\n\n" + fenced("x = 1"))
    code, out, _ = go(capsys, "export", path)
    nb = json.loads(path.with_suffix(".ipynb").read_text())
    assert code == 0 and out.startswith("wrote ") and nb["nbformat"] == 4


def test_chunk_prints_target_stdout_only(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('a')") + fenced("# name: two\nprint('b')") + fenced("raise X"))
    code, out, err = go(capsys, "chunk", path, "2")
    assert code == 0 and out == "b\n" and "ok 1" in err
    assert go(capsys, "chunk", path, "two")[1] == "b\n"


def test_chunk_earlier_error_exit_1(make_note: Maker, capsys) -> None:
    path = make_note(fenced("raise ValueError('early')") + fenced("print('b')"))
    code, out, err = go(capsys, "chunk", path, "2")
    assert code == 1 and out == "" and "ERROR 1" in err and "ValueError: early" in err
    assert not leftovers(path.parent)


def test_chunk_target_error_keeps_partial_stdout(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('p')\nraise ValueError('late')"))
    code, out, err = go(capsys, "chunk", path, "1")
    assert code == 1 and out == "p\n" and "ERROR 1" in err


def test_chunk_writes_nothing(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('a')"))
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    assert go(capsys, "chunk", path, "1")[0] == 0
    assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime
    assert not (path.parent / "attachments").exists() and not leftovers(path.parent)


def test_chunk_skip_target_exit_2(make_note: Maker, capsys) -> None:
    path = make_note(fenced("x=1") + fenced("# skip\ny=2"))
    code, _, err = go(capsys, "chunk", path, "2")
    assert code == 2 and "line" in err


def test_chunk_interpreter_failure_removes_staging(make_note: Maker, capsys) -> None:
    path = make_note(fenced("x=1"))
    assert go(capsys, "chunk", path, "1", "--python", "/nonexistent")[0] == 1
    assert not leftovers(path.parent)


def test_chunk_leaves_staged_figure_and_prints_path(make_note: Maker, capsys) -> None:
    pytest.importorskip("matplotlib")
    path = make_note(fenced(PLOT))
    code, _, err = go(capsys, "chunk", path, "1")
    line = next(ln for ln in err.splitlines() if ln.startswith("figure: "))
    png = Path(line.removeprefix("figure: "))
    assert code == 0 and png.exists() and png.parent.name.startswith(".noterun-note-")
    assert png.parent.parent == path.parent and not (path.parent / "attachments").exists()


def test_check_removes_stale_staging_dir(make_note: Maker, tmp_path: Path, capsys) -> None:
    path = make_note(fenced("print(1)") + out_fence("1"))
    old, new, other = (
        tmp_path / n
        for n in (".noterun-note-old123", ".noterun-note-new123", ".noterun-note-x-old123")
    )
    for d in (old, new, other):
        d.mkdir()
    past = time.time() - 7200
    for d in (old, other):
        os.utime(d, (past, past))
    go(capsys, "check", path)
    assert not old.exists() and new.exists() and other.exists()


def test_console_runs_prefix(monkeypatch: pytest.MonkeyPatch, make_note: Maker, capsys) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr("noterun.runner.console_command", lambda p, s: [p, "-c", s])
    monkeypatch.setattr("subprocess.call", lambda argv, **kw: seen.append(argv) or 5)
    path = make_note(fenced("# name: a\nx=1") + fenced("y=2"))
    assert go(capsys, "console", path, "--to", "a")[0] == 5
    assert seen[0][2] == "# name: a\nx=1"
    assert go(capsys, "console", path, "--to", "zzz")[0] == 2


def test_missing_note_exit_2(tmp_path: Path, capsys) -> None:
    assert go(capsys, "run", tmp_path / "nope.md")[0] == 2


def test_embed_two_blank_lines_below_chunk_is_prose_and_png_survives(
    make_note: Maker, tmp_path: Path, capsys
) -> None:
    (tmp_path / "attachments").mkdir()
    png = tmp_path / "attachments" / "note-1-1.png"
    png.write_bytes(b"hand")
    path = make_note(fenced("x = 1") + "\n\n![[note-1-1.png]]\n")
    assert go(capsys, "run", path)[0] == 0
    assert "![[note-1-1.png]]" in path.read_text() and png.read_bytes() == b"hand"


def test_non_executable_python_is_clean_error(make_note: Maker, tmp_path: Path, capsys) -> None:
    fake = tmp_path / "notpython"
    fake.write_text("x")
    code, _, err = go(capsys, "run", make_note(fenced("x=1"), python=str(fake)))
    assert code == 1 and "cannot launch" in err and "Traceback" not in err


def test_cwd_that_is_a_file_names_the_cwd(make_note: Maker, tmp_path: Path, capsys) -> None:
    (tmp_path / "afile").write_text("x")
    code, _, err = go(capsys, "run", make_note(fenced("x=1")), "--cwd", tmp_path / "afile")
    assert code == 1 and "afile" in err and "Traceback" not in err


def test_console_with_missing_python_is_clean(make_note: Maker, capsys) -> None:
    path = make_note(fenced("x=1"), python="/nonexistent/python")
    code, _, err = go(capsys, "console", path)
    assert code == 1 and "cannot launch" in err


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "0"])
def test_timeout_rejects_bad_values(make_note: Maker, value: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["run", str(make_note(fenced("x=1"))), "--timeout", value])
    assert exc.value.code == 2


def test_non_utf8_config_is_noterun_error(make_note: Maker, tmp_path: Path, capsys) -> None:
    (tmp_path / ".noterun.toml").write_bytes(b'run-python = "\xff"\n')
    code, _, err = go(capsys, "run", make_note(fenced("x=1"), python=None))
    assert code == 2 and "noterun:" in err


def test_unreadable_notes_do_not_stop_list_find_check(
    make_note: Maker, tmp_path: Path, capsys
) -> None:
    make_note(fenced("print('needle')") + out_fence("needle"), name="good.md")
    (tmp_path / "latin.md").write_bytes(b"caf\xe9\n")
    locked = tmp_path / "d.md"
    locked.write_text("x")
    locked.chmod(0)
    for argv in (("list", tmp_path), ("find", "needle", tmp_path), ("check", tmp_path)):
        code, out, err = go(capsys, *argv)
        assert "good.md" in out and "latin.md" in err and "d.md" in err
        assert len(err.strip().splitlines()) == 2 and code == 0


def test_surrogate_output_aborts_before_png_moves(make_note: Maker, tmp_path: Path, capsys) -> None:
    pytest.importorskip("matplotlib")
    path = make_note(fenced(f"{PLOT}\nprint('\\udc80')"))
    before = path.read_bytes()
    code, _, err = go(capsys, "run", path)
    assert code == 1 and "UTF-8" in err and path.read_bytes() == before
    assert not (tmp_path / "attachments").exists()


def test_case_colliding_names_raise_at_parse(make_note: Maker, tmp_path: Path, capsys) -> None:
    path = make_note(fenced("# name: Plot\n" + PLOT) + fenced("# name: plot\n" + PLOT))
    code, _, err = go(capsys, "run", path)
    assert code == 2 and "duplicate" in err and not (tmp_path / "attachments").exists()


def test_crlf_is_preserved(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('hi')") + out_fence("hi"))
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert go(capsys, "run", path)[0] == 0
    data = path.read_bytes()
    assert b"\n" not in data.replace(b"\r\n", b"") and b"verified: 2026-01-01" not in data
    assert go(capsys, "check", path)[0] == 0


def test_crlf_new_output_uses_crlf(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('hi')"))
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    go(capsys, "run", path)
    assert b"\r\nhi\r\n" in path.read_bytes()
    assert b"\n" not in path.read_bytes().replace(b"\r\n", b"")


def test_carriage_return_output_is_stable(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('50%\\r100%')"))
    assert go(capsys, "run", path)[0] == 0
    assert go(capsys, "check", path)[0] == 0


def test_bom_is_detected_and_kept(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('hi')"))
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert go(capsys, "run", path)[0] == 0
    data = path.read_bytes()
    assert (
        data.startswith(b"\xef\xbb\xbf---")
        and b"hi" in data
        and b"verified: 2026-01-01" not in data
    )


def test_symlinked_note_is_written_through(make_note: Maker, tmp_path: Path, capsys) -> None:
    real = make_note(fenced("print('hi')"), name="real.md")
    link = tmp_path / "link.md"
    link.symlink_to(real)
    assert go(capsys, "run", link)[0] == 0
    assert link.is_symlink() and "hi" in real.read_text()


def test_readonly_note_is_refused(make_note: Maker, tmp_path: Path, capsys) -> None:
    pytest.importorskip("matplotlib")
    path = make_note(fenced(PLOT))
    path.chmod(0o444)
    before = path.read_bytes()
    code, _, err = go(capsys, "run", path)
    assert code == 1 and "not writable" in err and path.read_bytes() == before
    assert not (tmp_path / "attachments").exists()


def test_missing_python_changes_nothing_in_note_dir(
    make_note: Maker, tmp_path: Path, capsys
) -> None:
    path = make_note(fenced("x=1"), python=None)
    before = sorted(p.name for p in tmp_path.iterdir())
    assert go(capsys, "run", path)[0] == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_unchanged_run_keeps_mtime(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('hi')"))
    go(capsys, "run", path)
    mtime = path.stat().st_mtime_ns
    time.sleep(0.01)
    assert go(capsys, "run", path)[0] == 0 and path.stat().st_mtime_ns == mtime


def test_same_day_rerun_refreshes_png_and_restores_deleted(
    make_note: Maker, tmp_path: Path, capsys
) -> None:
    pytest.importorskip("matplotlib")
    path = make_note(fenced(f"# name: plot\n{PLOT}"))
    go(capsys, "run", path)
    png = tmp_path / "attachments" / "note-plot-1.png"
    first = png.read_bytes()
    path.write_text(path.read_text().replace("[1, 2]", "[1, 50]"))
    go(capsys, "run", path)
    second = png.read_bytes()
    assert second != first
    png.unlink()
    go(capsys, "run", path)
    assert png.read_bytes() == second


def test_mixed_line_endings_stay_lf_when_lf_is_majority(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('hi')"))
    path.write_bytes(path.read_bytes().replace(b"type: ref\n", b"type: ref\r\n"))
    go(capsys, "run", path)
    new = path.read_bytes()
    assert new.count(b"\r\n") == 0 and b"\r\n" not in new[new.index(b"```output") :]
    assert new.count(b"\n") > 8


def test_symlink_into_readonly_folder_refused(make_note: Maker, tmp_path: Path, capsys) -> None:
    pytest.importorskip("matplotlib")
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real = make_note(fenced(PLOT), name="real.md")
    real.rename(real_dir / "real.md")
    link = tmp_path / "link.md"
    link.symlink_to(real_dir / "real.md")
    real_dir.chmod(0o555)
    try:
        code, _, err = go(capsys, "run", link)
    finally:
        real_dir.chmod(0o755)
    assert code == 1 and "not writable" in err and not (tmp_path / "attachments").exists()


def test_surrogate_stdout_is_clean_error_in_chunk_and_check(make_note: Maker, capsys) -> None:
    path = make_note(fenced("print('\\udc80')"))
    for argv in (("chunk", path, "1"), ("check", path)):
        code, out, err = go(capsys, *argv)
        assert code == 2 and "UTF-8" in err and "Traceback" not in err + out


def test_fifo_named_md_is_ignored(make_note: Maker, tmp_path: Path, capsys) -> None:
    make_note(fenced("print(1)"), name="good.md")
    os.mkfifo(tmp_path / "pipe.md")
    for argv in (("list", tmp_path), ("find", "x", tmp_path), ("check", tmp_path)):
        code, out, err = go(capsys, *argv)
        assert "pipe.md" not in out + err
