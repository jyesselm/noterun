# noterun

Runs the ```python chunks of an Obsidian note and writes what they print back into the note.
A note then carries its own evidence: every number in it came from code that is right there,
and `noterun check` tells you when the code and the numbers have drifted apart.

The chunks run in order, in one interpreter session, using the Python that the project's
config names. The tool itself needs nothing beyond the standard library.

## Daily use

```bash
noterun run NOTE.md      # after editing a note: run it, fill the outputs, stamp the date
noterun check .          # before trusting a folder: did anything drift?
```

That is the whole workflow. Everything else below is optional.

## What a note looks like

Plain markdown. The tool owns the ```output fences and the `verified:` date; never type into
those by hand.

````markdown
---
verified: 2026-10-02
---

## Setup

```python
# name: setup
from dms_ml.data_sets import load_primary
train = load_primary("training")
print(len(train), "residues")
```

```output
690 residues
```

## A toy

```python
# name: toy
print(train["motif"].nunique(), "motifs")
```

```output
168 motifs
```

Here is the function, for reading only:

```python
# skip: excerpt of dms_ml/models.py
def r_squared(observed, predicted) -> float:
    ...
```
````

Two optional comment lines on the first line of a chunk:

- `# name: slug` lets you address the chunk by name (letters, digits, underscores). Unnamed
  chunks are numbered from 1.
- `# skip` shows the code and never runs it. Use it for excerpts and fragments.

Chunks share one session, so later chunks may use names the earlier ones defined. If a chunk
makes a matplotlib figure, `run` saves it to `attachments/<note>-<chunk>-<k>.png` and adds an
`![[...]]` embed under the chunk's output. Figures a rerun no longer makes are deleted.

## Where the interpreter comes from

A `.noterun.toml` in the project folder, or any folder above the note:

```toml
run-python = "/opt/homebrew/Caskroom/mambaforge/base/envs/py3/bin/python"
run-cwd = "~/Library/CloudStorage/Dropbox/papers/2026-deep-learning-predict-dms/dms_ml"
run-env = "OMP_NUM_THREADS=4"
```

The same three keys in a note's frontmatter override the file, and `--python`, `--cwd`,
`--env` on the command line override both. Relative paths resolve against the file that
declared them. `~` is the home directory. `${VAR}` is expanded too, for a machine that needs an
override; an unset variable is an error.

The point of pinning `run-python` is that the numbers in a note are tied to one environment.
Whatever `python` is on PATH is never used.

## Commands

| Command | Does |
|---|---|
| `noterun run NOTE` | run all chunks, rewrite output fences and figure embeds, stamp `verified:` |
| `noterun check NOTE\|DIR` | rerun and diff without writing; one status line per note in a folder |
| `noterun chunk NOTE NAME` | run setup through one chunk and print only its output |
| `noterun console NOTE [--to NAME]` | run chunks, then stay in IPython with every name they built |
| `noterun list PATH [--names]` | the chunks of a note or folder, with their first line |
| `noterun find VALUE DIR` | which note and chunk printed a value |
| `noterun export NOTE` | write `NOTE.ipynb` beside the note |

`noterun COMMAND --help` shows the flags. Exit codes: 0 ok, 1 a chunk failed or drifted, 2 a
usage, parse or config error. Nothing is written when any chunk fails.

## When something goes wrong

| Message | Meaning |
|---|---|
| `cannot launch /path/to/python` | the interpreter in `.noterun.toml` is not installed here |
| `no run-python: set it in .noterun.toml, frontmatter, or --python` | no config found above the note |
| `environment variable X is not set` | a `${X}` in the config, and the shell has no `X` |
| `DRIFT` with a diff | the code now prints something else; `run` to accept, or fix the code |
| `ERROR` with a traceback | a chunk raised; the note is untouched |
| `not run (earlier chunk failed)` | chunks after the failure were skipped |
| `no chunk 'x'; runnable: ...` | the name is wrong or the chunk is `# skip` |
| `line N: unclosed ``` fence` | a fence without a closing line; fix the note |
| `duplicate chunk name` | two chunks share a `# name:`, compared case-insensitively |
| `note changed during run, nothing written` | the note was edited while it ran; run again |
| `no runnable python fences` | every chunk is `# skip` (fine for reference notes) |

A heavy chunk belongs in a repo script. Keep chunks to about ten seconds and let a chunk read
the table the script produced.

## Install

Once per machine. Windows is untested.

```bash
brew install pipx && pipx ensurepath                  # macOS; elsewhere: python3 -m pip install --user pipx
pipx install git+https://github.com/jyesselm/noterun  # or: pipx install -e /path/to/checkout
```

The project's Python environment must exist at the path its `.noterun.toml` names. Paths are
the same on every machine, so the config travels with the project untouched.

zsh completion of note paths and chunk names: add this line before `compinit` in `~/.zshrc`,
then `rm -f ~/.zcompdump*` and open a new shell.

```zsh
fpath=(~/local/code/python/developing/noterun/completions $fpath)
```

## Development

```bash
pip install -e '.[dev]'
ruff check . && ruff format --check . && mypy noterun && pytest -q
```
