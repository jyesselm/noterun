"""Runtime resolution: CLI flag > note frontmatter > `.noterun.toml` > default.

Values may use `~` and `${VAR}`; an unset variable is an error, never a literal. Relative paths
resolve against the directory of the file that declared them (CLI: the shell's cwd). A bare
`run-python` command name is left for PATH lookup.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from noterun.note import Note, NoterunError

try:
    import tomllib
except ImportError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef,import-not-found]

CONFIG_NAME = ".noterun.toml"
RUN_KEYS = ("run-python", "run-cwd", "run-env")
DEFAULT_TIMEOUT_S = 600.0


@dataclass(frozen=True)
class Overrides:
    """Runtime values from CLI flags."""

    python: str | None = None
    cwd: str | None = None
    env: tuple[str, ...] = ()
    timeout_s: float = DEFAULT_TIMEOUT_S


@dataclass(frozen=True)
class Runtime:
    """Where and how a note's chunks run."""

    python: str | None
    cwd: Path
    env: dict[str, str]
    timeout_s: float = DEFAULT_TIMEOUT_S


def find_config(start: Path) -> Path | None:
    """The first `.noterun.toml` in `start` or any parent."""
    for folder in (start, *start.parents):
        if (folder / CONFIG_NAME).is_file():
            return folder / CONFIG_NAME
    return None


def load_config(path: Path) -> dict[str, str]:
    """Read a config file; unknown keys and non-string values raise."""
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as exc:
        raise NoterunError(f"{path}: {exc}") from exc
    for key, value in data.items():
        if key not in RUN_KEYS or not isinstance(value, str):
            raise NoterunError(f"{path}: bad key or value: {key}")
    return data


def _expand(value: str, origin: Path) -> str:
    """Expand `${VAR}` then `~`; an unset variable raises, naming the variable and `origin`."""
    out = os.path.expanduser(os.path.expandvars(value))
    if "${" in out or out.startswith("$"):
        unset = ", ".join(m for m in re.findall(r"\$\{?(\w+)", value) if m not in os.environ)
        raise NoterunError(f"{origin}: environment variable {unset} is not set")
    return out


def parse_env(spec: str, origin: Path | None = None) -> dict[str, str]:
    """Split `K=V K2=V2` on whitespace; expand VALUEs when `origin` names the declaring file."""
    env: dict[str, str] = {}
    for pair in spec.split():
        key, sep, value = pair.partition("=")
        if not sep:
            raise NoterunError(f"run-env pair without '=': {pair!r}")
        env[key] = _expand(value, origin) if origin else value
    return env


def declares_runtime(note_dir: Path, meta: dict[str, str]) -> bool:
    """True when frontmatter or a config file names a runtime."""
    return any(meta.get(k) for k in RUN_KEYS) or find_config(note_dir) is not None


def child_environ(runtime: Runtime) -> dict[str, str]:
    """The inherited environment with the declared pairs on top."""
    return {**os.environ, **runtime.env}


def _path(value: str, base: Path) -> Path:
    """Expand `~`; resolve a relative path against `base`."""
    path = Path(os.path.expanduser(value))
    return Path(os.path.normpath(path if path.is_absolute() else base / path))


def resolve_runtime(note: Note, overrides: Overrides) -> Runtime:
    """Combine CLI flags, frontmatter and config into a Runtime."""
    note_dir = note.path.parent
    cfg_path = find_config(note_dir)
    cfg = load_config(cfg_path) if cfg_path else {}
    cfg_dir = cfg_path.parent if cfg_path else note_dir
    sources = [(cfg, cfg_dir, cfg_path or note.path), (note.meta, note_dir, note.path)]

    def pick(key: str, flag: str | None) -> tuple[str | None, Path]:
        """The winning value for `key` and the directory its relative paths resolve against."""
        if flag:
            return flag, Path.cwd()
        for values, base, origin in reversed(sources):
            if values.get(key):
                return _expand(values[key], origin), base
        return None, note_dir

    py, py_base = pick("run-python", overrides.python)
    cwd, cwd_base = pick("run-cwd", overrides.cwd)
    env: dict[str, str] = {}
    for values, _, origin in sources:
        env.update(parse_env(values.get("run-env", ""), origin))
    for pair in overrides.env:
        env.update(parse_env(pair))
    python = (str(_path(py, py_base)) if os.sep in py else py) if py else None
    workdir = _path(cwd, cwd_base) if cwd else note_dir
    return Runtime(python, workdir, env, overrides.timeout_s)
