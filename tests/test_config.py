"""Config tests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from noterun.config import (
    CONFIG_NAME,
    Overrides,
    Runtime,
    child_environ,
    declares_runtime,
    find_config,
    parse_env,
    resolve_runtime,
)
from noterun.note import Note, NoterunError, parse_note


def note_at(path: Path, **meta: str) -> Note:
    """A parsed note at `path` with the given frontmatter."""
    head = "\n".join(f"{k.replace('_', '-')}: {v}" for k, v in meta.items())
    return parse_note(path, f"---\n{head}\n---\n")


def write_cfg(folder: Path, text: str) -> None:
    """Write a config file."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / CONFIG_NAME).write_text(text)


def test_find_config_searches_upward(tmp_path: Path) -> None:
    write_cfg(tmp_path, 'run-python = "x"\n')
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert find_config(deep) == tmp_path / CONFIG_NAME


@pytest.mark.parametrize("key", ["python", "cwd", "env"])
def test_precedence_cli_over_frontmatter_over_config(tmp_path: Path, key: str) -> None:
    write_cfg(tmp_path, 'run-python = "cfg"\nrun-cwd = "/cfg"\nrun-env = "K=cfg"\n')
    note = note_at(tmp_path / "n.md", run_python="fm", run_cwd="/fm", run_env="K=fm")
    flags = {"python": "cli", "cwd": "/cli", "env": ("K=cli",)}
    rt_fm = resolve_runtime(note, Overrides())
    rt_cli = resolve_runtime(note, Overrides(**{key: flags[key]}))
    assert (rt_fm.python, str(rt_fm.cwd), rt_fm.env) == ("fm", "/fm", {"K": "fm"})
    got = {"python": rt_cli.python, "cwd": str(rt_cli.cwd), "env": rt_cli.env["K"]}
    assert got[key] == {"python": "cli", "cwd": "/cli", "env": "cli"}[key]


def test_empty_frontmatter_value_falls_back_to_config(tmp_path: Path) -> None:
    write_cfg(tmp_path, 'run-cwd = "/cfg"\n')
    rt = resolve_runtime(note_at(tmp_path / "n.md", run_cwd=""), Overrides())
    assert rt.cwd == Path("/cfg")


def test_run_env_merges_per_key(tmp_path: Path) -> None:
    write_cfg(tmp_path, 'run-env = "A=1 B=2"\n')
    note = note_at(tmp_path / "n.md", run_env="B=3 C=4")
    rt = resolve_runtime(note, Overrides(env=("C=5",)))
    assert rt.env == {"A": "1", "B": "3", "C": "5"}


def test_child_environ_declared_wins_over_os_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NR_KEY", "inherited")
    monkeypatch.setenv("NR_OTHER", "kept")
    env = child_environ(Runtime(None, Path("."), {"NR_KEY": "declared"}))
    assert env["NR_KEY"] == "declared" and env["NR_OTHER"] == "kept"


@pytest.mark.parametrize("where", ["config", "frontmatter"])
@pytest.mark.parametrize("value", ["sub", "..", "../repo"])
def test_relative_paths_resolve_against_declaring_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str, value: str
) -> None:
    base = tmp_path / "proj" / "deep"
    base.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    if where == "config":
        write_cfg(base, f'run-cwd = "{value}"\n')
        note = note_at(base / "sub" / "n.md")
    else:
        note = note_at(base / "n.md", run_cwd=value)
    rt = resolve_runtime(note, Overrides())
    assert rt.cwd == (base / value).resolve()


def test_bare_python_name_left_for_path(tmp_path: Path) -> None:
    write_cfg(tmp_path, 'run-python = "python3"\n')
    assert resolve_runtime(note_at(tmp_path / "n.md"), Overrides()).python == "python3"


def test_relative_python_with_separator_resolved(tmp_path: Path) -> None:
    write_cfg(tmp_path, 'run-python = "bin/python"\n')
    rt = resolve_runtime(note_at(tmp_path / "n.md"), Overrides())
    assert rt.python == str(tmp_path / "bin" / "python")


@pytest.mark.parametrize("text", ['run_python = "x"\n', "run-python = 3\n", "run-python = [\n"])
def test_bad_config_raises(tmp_path: Path, text: str) -> None:
    write_cfg(tmp_path, text)
    with pytest.raises(NoterunError):
        resolve_runtime(note_at(tmp_path / "n.md"), Overrides())


def test_env_pair_without_equals_raises() -> None:
    with pytest.raises(NoterunError):
        parse_env("A=1 broken")


def test_no_python_resolves_to_none(tmp_path: Path) -> None:
    rt = resolve_runtime(note_at(tmp_path / "n.md"), Overrides())
    assert rt.python is None and rt.cwd == tmp_path


def test_declares_runtime_from_meta_or_config(tmp_path: Path) -> None:
    assert not declares_runtime(tmp_path, {"run-python": ""})
    assert declares_runtime(tmp_path, {"run-env": "A=1"})
    write_cfg(tmp_path, "")
    assert declares_runtime(tmp_path, {})


def test_expands_vars_and_tilde_in_config_and_frontmatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NR_PY", "/opt/py")
    monkeypatch.setenv("NR_VAL", "v1")
    write_cfg(tmp_path, 'run-python = "${NR_PY}/bin/python"\nrun-env = "A=${NR_VAL}"\n')
    rt = resolve_runtime(note_at(tmp_path / "n.md", run_cwd="~/proj"), Overrides())
    assert rt.python == "/opt/py/bin/python" and rt.env == {"A": "v1"}
    assert rt.cwd == Path.home() / "proj"
    fm = resolve_runtime(note_at(tmp_path / "m.md", run_python="${NR_PY}/p"), Overrides())
    assert fm.python == "/opt/py/p"


@pytest.mark.parametrize("where", ["config", "frontmatter", "env"])
def test_unset_variable_raises_naming_variable_and_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    monkeypatch.delenv("NR_UNSET", raising=False)
    if where == "config":
        write_cfg(tmp_path, 'run-python = "${NR_UNSET}"\n')
        note, declared = note_at(tmp_path / "n.md"), CONFIG_NAME
    elif where == "frontmatter":
        note, declared = note_at(tmp_path / "n.md", run_python="$NR_UNSET"), "n.md"
    else:
        note, declared = note_at(tmp_path / "n.md", run_env="A=${NR_UNSET}"), "n.md"
    with pytest.raises(NoterunError, match=f"NR_UNSET.*|{declared}") as exc:
        resolve_runtime(note, Overrides())
    assert "NR_UNSET" in str(exc.value) and declared in str(exc.value)


def test_package_is_python39_syntax() -> None:
    import noterun

    for path in Path(noterun.__file__).parent.glob("*.py"):
        ast.parse(path.read_text(), feature_version=(3, 9))
