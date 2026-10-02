# noterun

Run the ```python chunks of an Obsidian note in the note's declared interpreter, keep each chunk's
stdout in an ```output fence under it, and save matplotlib figures as embeds. Standard library
only. The note format is described in the Explainer Template in the vault.

## Install

```bash
pipx install -e .          # puts `noterun` on PATH
pip install -e '.[dev]'    # development
```

## Runtime

`run-python`, `run-cwd`, `run-env` come from, in order: CLI flags, note frontmatter, the first
`.noterun.toml` found searching upward from the note (same keys, TOML strings). Values may use `~` and `${VAR}`; an unset variable is an error.

## Commands

| Command | Does |
|---|---|
| `noterun run NOTE` | run all chunks, rewrite output fences and figure embeds, stamp `verified:` |
| `noterun check NOTE\|DIR` | rerun and diff without writing; one status line per note in a folder |
| `noterun console NOTE [--to NAME]` | run setup through a chunk, then stay in IPython |
| `noterun chunk NOTE NAME` | run setup through a chunk and print only its stdout |
| `noterun export NOTE` | write `NOTE.ipynb` beside the note |
| `noterun list PATH [--names]` | list chunks of a note or folder |
| `noterun find VALUE DIR` | search stored output lines |

Chunks start with `# name: <slug>` to be addressed by name, or `# skip` to be shown, not run.
Exit codes: 0 ok, 1 chunk error or drift, 2 usage, parse or config error.

## On another computer

Windows is untested.

1. Install pipx if missing: `brew install pipx && pipx ensurepath` on macOS, or
   `python3 -m pip install --user pipx` elsewhere.
2. Install noterun: `pipx install git+https://github.com/jyesselm/noterun`. For development use
   `pipx install -e /path/to/checkout`.
3. Point the project config at the published environment in your shell rc:
   `export DMS_PY=/path/to/envs/py3/bin/python`.
4. Add the zsh completion line below.

`.noterun.toml` must only contain machine-independent values (`~`, `${VAR}`).

## zsh completion

Add `fpath=(~/local/code/python/developing/noterun/completions $fpath)` before `compinit` in
`~/.zshrc`, then `rm -f ~/.zcompdump*` and open a new shell.
