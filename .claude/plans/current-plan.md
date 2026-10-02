# Plan: `noterun` v0.1 (revision 2, after plan-critic)

## Goal
Replace `~/notes/900-structure/bin/note-run.py` with an installable, stdlib-only package `noterun`
that runs the ```python chunks of an Obsidian note in the note's declared interpreter, owns the
```output fences and figure embeds under them, and adds check/console/chunk/export/list/find over notes
and folders.

## Read before coding (the only context you get)
1. `~/notes/900-structure/bin/note-run.py`: the working 230-line script this grows from. Reuse its
   frontmatter regex, fence dedent rule, driver idea, `--check` diff and ipynb export. Port; do not
   reinvent.
2. `~/notes/200-projects/papers/2026/2026-deep-learning-predict-dms/r-squared-is-squared-pearson.md`:
   a real verified note (4 python fences, one `# skip: excerpt of ...`, 3 output fences).
3. `~/notes/200-projects/papers/2026/2026-deep-learning-predict-dms/data-and-model-training.md`:
   second real note. Contains a ```bash fence inside a `> ` callout (must stay inert). The sibling
   `results-1-prediction-model.md` has hand-written embeds like
   `![[200-projects/.../results-1-panel-a-2026-09-28.png|720]]`, and `attachments/` holds a
   hand-made `results-1-figure-1-2026-09-28.png`; none of these may ever be treated as tool-owned.
4. `~/.claude/standards/python-style.md`, `~/.claude/standards/leanness.md`.
5. Layout and conventions: `~/local/code/python/developing/yplot2/pyproject.toml`.

## Fixed design decisions (from the user, not open for change)
- The vault is a chunk library: a note holds a few small chunks (< ~10 s each) around one concept;
  heavy work lives in repo scripts whose tables a chunk reads.
- Tool core is standard library only. It ALWAYS launches the note's declared interpreter as a
  subprocess, never the tool's own `sys.executable`. matplotlib is touched only inside the driver
  code running in the note's interpreter, and only if `matplotlib.pyplot` is already imported there.
- Markdown stays plain Obsidian markdown. ```output fences and figure embed lines are tool-owned.
- OUT of scope: prose drift checking, change detection/hashing/caching, a `new` command, Jupyter
  kernels, HTML rendering.

## Design

### Package layout (HARD ceiling 1215 lines total, `wc -l noterun/*.py`; no new modules)
```
noterun/
  __init__.py      ~3   __version__ only
  note.py        ~140   parse markdown into the data model; errors; output normalization
  config.py       ~60   .noterun.toml search, runtime resolution, env merge
  _driver.py      ~50   stdlib script executed INSIDE the note's interpreter (sent via -c)
  runner.py       ~85   subprocess launch, request/result JSON, staging, console, prefix selection
  rewrite.py     ~120   compare stored vs fresh, render owned regions, apply edits, stamp,
                        atomic write, PNG commit
  export.py       ~40   note -> nbformat 4.5 dict -> .ipynb
  cli.py         ~180   argparse, seven subcommands, note discovery, report lines, exit codes
                 ----
                 ~680
```
The ceiling is 1215 because the original 700 counted code lines only, while `wc -l` also counts
formatter blank lines, mandatory docstrings and wrapped imports (about 390 lines).
The user's target was ~500; mandatory docstrings and the `chunk`
subcommand and `list --names` make ~680 realistic. The zsh completion file is not counted. Keep docstrings short
(summary line, plus Args/Returns only where a name is not self-explanatory). If the total goes
over 1215, first remove duplication; if still over, in this order: (1) delete the defensive
unclosed-fence check in `find_owned` (`parse_note` already raises), (2) drop the `--env` flag.
Do NOT add modules.

`_driver.py` is a real module (so ruff/mypy check it and tests import it), but it must:
import nothing from `noterun`, use only stdlib, and stay valid on Python 3.8 because it runs in the
note's interpreter, which may be older than the tool's. Its module docstring states: "Runs inside
the note's interpreter. Python 3.8 syntax and stdlib only: no `match`, no PEP 604 unions outside
annotations, no 3.9+ APIs such as `str.removeprefix`, `dict |`, `zoneinfo`." Use
`from __future__ import annotations`. A test enforces the syntax (Step 4). `runner.py` sends its
source with `Path(__file__).with_name("_driver.py").read_text()` as the `-c` argument.

### Errors (in `note.py`)
```python
class NoterunError(Exception):       # usage/config/parse problems -> exit 2
class InterpreterError(NoterunError): # interpreter crashed, timed out, no result file -> exit 1
```

### Data model (`note.py`)
```python
@dataclass(frozen=True)
class Fence:
    start: int          # 0-based line of the opening fence
    end: int            # exclusive: line after the closing fence (len(lines) if unclosed)
    indent: str         # leading whitespace of the opening line
    ticks: str          # the opening run, e.g. "```" or "````" or "~~~"
    lang: str           # lowercased info word, "" for bare fences
    body: list[str]     # inner lines with `indent` removed where present
    closed: bool        # False when the fence runs to EOF

@dataclass(frozen=True)
class Owned:            # the tool-owned region directly under a python fence
    start: int          # == chunk fence.end
    end: int            # exclusive; == start when nothing is owned
    output: Fence | None
    embeds: list[str]   # owned PNG file names, in order

@dataclass(frozen=True)
class Chunk:
    index: int          # 1-based among ALL ```python fences in the note (skip ones included)
    name: str | None    # from `# name: <slug>`
    skip: bool          # from a `# skip...` directive line
    fence: Fence
    owned: Owned
    # properties:
    label -> str        # name if set else str(index); used in filenames, reports, --to
    code -> str         # "\n".join(fence.body) (directive lines kept: they are comments, and
                        #  keeping them keeps traceback line numbers equal to fence lines)
    first_line -> str   # first body line that is neither blank nor a directive

@dataclass(frozen=True)
class Note:
    path: Path
    text: str                   # exact file text as read (used for the changed-on-disk guard)
    lines: list[str]            # text.split("\n") (a trailing newline survives as a final "")
    meta: dict[str, str]        # flat frontmatter keys, quotes stripped
    frontmatter_end: int        # line index of closing "---", -1 when no frontmatter
    chunks: list[Chunk]
    # properties: stem (path.stem), runnable (chunks with skip False)
```

Functions:
```python
def read_note(path: Path) -> Note   # path.read_text(encoding="utf-8"), nothing else
def parse_note(path: Path, text: str) -> Note
def parse_frontmatter(lines: list[str]) -> tuple[dict[str, str], int]   # port from note-run.py
def find_fences(lines: list[str], start: int) -> list[Fence]
def parse_directives(body: list[str]) -> tuple[str | None, bool]          # (name, skip)
def find_owned(lines: list[str], fence: Fence, fences: list[Fence], stem: str) -> Owned
def normalize_output(text: str) -> str
```

Parsing rules (state these in the module docstring; they are the format spec):
- **Fences.** Opening line matches `^(?P<indent>[ \t]*)(?P<ticks>`{3,}|~{3,})(?P<lang>[^\s`]*)`.
  Bare fences (no lang) ARE recorded as fences, so their contents are inert (the old script
  skipped them, which let a ```python inside a bare fence be misread). Closing line: stripped
  text is a run of the same character, length >= the opening run, nothing else. Scanning resumes
  after the closing line, so nothing inside any fence is ever re-scanned.
- **Unclosed fences.** A fence with no closing line gets `closed=False` and `end=len(lines)`.
  `parse_note` raises `NoterunError("line N: unclosed ``` fence")` for ANY unclosed fence that
  starts at or after the first ```python fence (that covers an unclosed python fence and an
  unclosed output fence under a chunk, which would otherwise make the owned region swallow the
  rest of the note). An unclosed fence before any python fence swallows to EOF, so the note simply
  has no chunks; that is not an error.
- **`find_owned` never attaches an unclosed fence.** If the candidate output fence has
  `closed=False` it raises `NoterunError` naming its line (defensive; `parse_note` already raises).
- **Inert blocks.** Only `lang == "python"` fences become chunks. `output` fences matter only when
  `find_owned` attaches them to a chunk; an unattached output fence is inert prose. Every other
  lang (text, bash, mermaid, bare) is ignored. Lines starting with `>` (callouts, quotes) never
  match the fence regex, so fences inside callouts are inert and never run.
- **Directives.** The leading run of body lines matching `^#\s*name:\s*(\S+)\s*$` or
  `^#\s*skip\b.*$` (either order, no blank lines between). Slug must match
  `^[A-Za-z][A-Za-z0-9_]*$`: starts with a letter so it never collides with an index, and has
  NO hyphen so a filename `<stem>-<label>-<k>.png` splits unambiguously. A bad slug or a duplicate
  name in one note raises `NoterunError` naming the line numbers.
  `# skip: excerpt of dms_ml/models.py` is a skip directive.
- **Owned region grammar** (the exact lines the tool owns under a python chunk's closing fence):
  ```
  <python fence closing line>
  [at most one blank line]
  <indent>```output            # optional; closed; any backtick run >= 3; any indent on read
  ...stdout...
  <indent>```
  [at most one blank line]
  <indent>![[<stem>-<label>-<k>.png]]   # optional; one or more CONSECUTIVE lines
  ```
  The embed regex is
  `^[ \t]*!\[\[(?P<file>{re.escape(stem)}-[A-Za-z0-9_]+-\d+\.png)\]\]\s*$`:
  exactly two hyphen-separated segments after the stem (label without hyphens, then a figure
  number). Anything with a path, `|size`, a different stem, or extra segments
  (`a-b-1-1.png` under stem `a`; `results-1-figure-1-2026-09-28.png` under stem `results-1`) is
  prose. The label segment is generic (not the current label) so a renamed chunk's old embeds are
  still recognized and cleaned up.
  `Owned.end` is the line after the last owned element; the optional blank line before an element
  is part of the region only when that element exists. With no output and no embeds,
  `Owned.start == Owned.end == fence.end`.
- **Normalization** (`normalize_output`): split on "\n", `rstrip()` every line, drop trailing blank
  lines, join with "\n". Leading blank lines are kept. Used for both stored (`"\n".join(output.body)`)
  and fresh stdout. Empty result means "no output fence".

### Config (`config.py`)
```python
CONFIG_NAME = ".noterun.toml"
RUN_KEYS = ("run-python", "run-cwd", "run-env")

@dataclass(frozen=True)
class Overrides:            # from CLI flags
    python: str | None = None
    cwd: str | None = None
    env: tuple[str, ...] = ()   # each "KEY=VALUE"
    timeout_s: float = 600      # --timeout; resolve_runtime copies it to Runtime.timeout_s

@dataclass(frozen=True)
class Runtime:
    python: str | None      # None => unverified (check) / error (run, console)
    cwd: Path
    env: dict[str, str]     # declared pairs only (not os.environ)
    timeout_s: float = 600  # copied from Overrides.timeout_s; here so run_chunks keeps 4 params

def find_config(start: Path) -> Path | None
def load_config(path: Path) -> dict[str, str]
def parse_env(spec: str) -> dict[str, str]
def resolve_runtime(note: Note, overrides: Overrides) -> Runtime
def declares_runtime(note_dir: Path, meta: dict[str, str]) -> bool
def child_environ(runtime: Runtime) -> dict[str, str]
```
- `find_config`: walk `start, start.parent, ...` to the filesystem root; the FIRST
  `.noterun.toml` found wins. No merging of several config files.
- `load_config`: `tomllib.load`; values must be strings; an unknown key or non-string value raises
  `NoterunError` (catches typos like `run_python`). `tomllib.TOMLDecodeError` -> `NoterunError`.
- `timeout_s` has no file source: `resolve_runtime` copies `overrides.timeout_s` unchanged.
- Precedence per key: CLI flag > note frontmatter > config > default. An EMPTY frontmatter value
  (`run-cwd:`) counts as unset, so config fills it.
- Paths (`_resolve_path(value, base_dir, *, ...)` or two small helpers, your choice, no flag arg):
  always `os.path.expanduser` first. The base dir is the directory of the file that declared the
  value (config dir, or note dir for frontmatter); CLI values use `Path.cwd()`.
  - `run-cwd`: ALWAYS resolved against the base dir when relative (`sub`, `..`, `../repo` all
    resolve against the declaring file's directory, never the shell's cwd).
  - `run-python`: if relative AND contains a path separator, resolve against the base dir; a bare
    command name (`python3`) is left as-is for PATH lookup. This rule applies to run-python only.
- Default `run-cwd` is the note's directory. `run-python` has no default.
- `run-env`: `parse_env` splits on whitespace, `partition("=")`, a pair without `=` raises
  `NoterunError`. Merge per KEY: config pairs, then frontmatter pairs, then each `--env` flag;
  later wins. `child_environ` returns `{**os.environ, **runtime.env}` (declared wins over the
  inherited environment). `--env` stays minimal: repeatable `KEY=VALUE`, no unset syntax, no
  file loading, no quoting rules beyond whitespace splitting of the frontmatter/config string.
- `declares_runtime(note_dir, meta)`: any non-empty `run-*` key in `meta`, or
  `find_config(note_dir)` is not None. Takes only the frontmatter (from `parse_frontmatter`,
  which cannot fail), so `check DIR` can decide whether to visit a note BEFORE full parsing.

### Driver protocol (`_driver.py` + `runner.py`)
Command: `[runtime.python, "-c", DRIVER_SOURCE]`, `cwd=runtime.cwd`,
`env=child_environ(runtime) | {"MPLBACKEND": "Agg"}` (everything launched through `run_chunks`:
run, check and chunk; never console), stdin = request JSON,
stdout and stderr INHERITED (pass straight to the terminal, never stored).
`-c` puts the cwd on `sys.path`, which is what makes `import dms_ml` work from `run-cwd`; keep it.

Request (stdin):
```json
{"chunks": [{"index": 1, "name": "setup", "code": "..."}, ...],
 "figure_dir": "/abs/note-dir/.noterun-XXXX", "stem": "r-squared-is-squared-pearson",
 "result_path": "/abs/note-dir/.noterun-XXXX/result.json"}
```
Only runnable (non-skip) chunks are sent. Results go to `result_path`, NOT stdout, so a chunk or C
extension that writes to fd 1 cannot corrupt the JSON.

Result (file): list, one entry per chunk actually executed, stopping after the first error:
`{"index": 1, "stdout": "...", "error": "<traceback>" | null, "figures": ["/abs/.../x.png"]}`.

`_driver.py` functions:
```python
def run_request(request: dict) -> list[dict]      # one shared namespace {"__name__": "__main__"}
def run_chunk(chunk: dict, namespace: dict) -> tuple[str, str | None]
def save_figures(figure_dir: str, stem: str, label: str) -> list[str]
def main() -> None                                  # stdin -> run_request -> result_path
if __name__ == "__main__": main()                   # `-c` sets __name__ to "__main__"
```
- `run_chunk`: filename `f"<chunk {label}>"`, register the source in `linecache.cache` so
  tracebacks show the code lines, `exec(compile(...), namespace)` under
  `contextlib.redirect_stdout(StringIO)`, catch `BaseException`, return `(stdout, traceback or None)`.
- `save_figures`: `pyplot = sys.modules.get("matplotlib.pyplot")`; if None return `[]` (never
  import matplotlib). For `k, number in enumerate(pyplot.get_fignums(), start=1)` save
  `pyplot.figure(number).savefig(os.path.join(figure_dir, f"{stem}-{label}-{k}.png"))`; then
  `pyplot.close("all")`. Default savefig settings (respect the user's rcParams).
  Called after every successful chunk.

`runner.py`:
```python
@dataclass(frozen=True)
class ChunkResult:
    index: int
    stdout: str
    error: str | None
    figures: list[Path]

def run_chunks(note: Note, chunks: list[Chunk], runtime: Runtime, staging: Path) -> list[ChunkResult]
def make_staging(note: Note, runtime: Runtime) -> Path
def clear_staging(note: Note, max_age_s: float) -> None
def select_prefix(note: Note, target: str | None) -> list[Chunk]
def console_command(python: str, script: str) -> list[str]
def open_console(note: Note, runtime: Runtime, target: str | None) -> int
```
- `run_chunks` raises `InterpreterError` on: `runtime.python is None` (message names
  `.noterun.toml` and the frontmatter keys), `FileNotFoundError` launching it,
  `subprocess.TimeoutExpired`, or a missing/unparseable result file (report the return code).
- `run_chunks` sends exactly the `chunks` it is given (run/check pass `note.runnable`; `chunk`
  passes `select_prefix(note, NAME)`).
- **Staging.** `make_staging(note, runtime)` first calls
  `clear_staging(note, runtime.timeout_s)`, then returns
  `Path(tempfile.mkdtemp(dir=note.path.parent, prefix=f".noterun-{note.stem}-"))`: a dot-directory
  beside `attachments/` (Obsidian ignores dot-dirs), on the same filesystem, so moving a PNG into
  `attachments/` with `os.replace` cannot fail with a cross-device error. Figures ALWAYS go there,
  never straight to `attachments/`, which is what lets an error or `check` write nothing.
  `clear_staging(note, max_age_s)` `shutil.rmtree(..., ignore_errors=True)`s every DIRECTORY in
  the note's folder whose name is `.noterun-{stem}-` followed by a suffix matching `^[a-z0-9_]+$`
  (mkdtemp's alphabet has no hyphen, so stem `a` never matches stem `a-b`'s dirs;
  `write_atomic`'s temp FILES are never touched) AND whose mtime is older than `max_age_s`
  seconds. The age test keeps it from deleting a staging dir that another noterun process is
  using right now (`check DIR` in one terminal, `run NOTE` in another): any live run is killed
  by its own timeout before its dir can be that old. `run` and `check` remove their own staging dir in a `finally` (`shutil.rmtree`), so
  nothing survives them. The ONE exception is `noterun chunk`, which leaves its staging dir so the
  user can open the figures; the first `run`, `check`, or `chunk` of that note after the dir is
  older than the timeout clears it via `make_staging`, and `iter_notes` already skips `.noterun-*`.
- `select_prefix(note, target)`: `None` -> all runnable chunks. Otherwise resolve target to one
  chunk: digits -> `Chunk.index`, else `Chunk.name`. Unknown target, or a target that is a skip
  chunk -> `NoterunError` (a skip target names its fence line: `chunk 2 (line 41) is # skip`;
  an unknown target lists the valid labels). Return runnable chunks with
  `index <= target.index` (setup through target, inclusive; skip chunks excluded).
- `console_command`: probe `subprocess.run([python, "-c", "import IPython"], capture_output=True)`;
  returncode 0 -> `[python, "-m", "IPython", "--no-banner", "-i", "-c", script]`, else
  `[python, "-i", "-c", script]` plus one stderr line saying IPython is missing in that
  interpreter. `-c` is the committed design for both (verified: IPython 9.7.0
  `python -m IPython --no-banner -i -c "x=41+1"` stays interactive with `x` defined and the cwd
  first on `sys.path`). No temp-file script.
- `open_console`: script = chunks from `select_prefix` joined by `"\n\n"`. Env is
  `child_environ(runtime)` WITHOUT `MPLBACKEND`, so figures stay interactive; no figure capture;
  nothing is written to the note or attachments. Returns the child's exit code.

### Rewrite (`rewrite.py`)
```python
@dataclass(frozen=True)
class Fresh:
    stdout: str          # normalized
    figures: list[str]   # PNG file names

@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    lines: list[str]

def stored(chunk: Chunk) -> Fresh
def fresh_by_index(note: Note, results: list[ChunkResult]) -> dict[int, Fresh]
def drift_lines(chunk: Chunk, fresh: Fresh) -> list[str]
def plan_edits(note: Note, fresh: dict[int, Fresh]) -> list[Edit]
def render_owned(indent: str, fresh: Fresh) -> list[str]
def apply_edits(lines: list[str], edits: list[Edit]) -> list[str]
def stamp_verified(lines: list[str], frontmatter_end: int, today: str) -> list[str]
def write_atomic(path: Path, text: str) -> None
def commit_note(note: Note, new_text: str, results: list[ChunkResult]) -> None
```
Algorithm (`noterun run`):
0. **Zero runnable chunks** (checked right after parsing, BEFORE runtime resolution, so no
   run-python is needed): print `<note>: no runnable python fences`. If no skip chunk owns a
   region, write nothing and exit 0. If a skip chunk still owns a stale region, compute the edits
   for those chunks only (steps 3, 4, 6 with every fresh = `Fresh("", [])`), commit them through
   `commit_note` (guard, atomic write, stale PNG deletion), do NOT stamp `verified:`, exit 0.
   `check NOTE` on such a note prints the same line and exits 0, or DRIFT exit 1 if a skip chunk
   owns a region.
1. Parse note (keep `note.text`), resolve runtime (missing python -> exit 2), open the staging
   dir, `run_chunks`.
2. If any result has an error, or fewer results than runnable chunks: print each chunk's status
   (ok / ERROR + traceback / "not run (earlier chunk failed)"), print
   `noterun: FAILED, nothing written`, exit 1. Note file and attachments untouched; staging
   removed.
3. `fresh_by_index`: runnable chunks get `Fresh(normalize_output(stdout), [p.name for p in figures])`;
   skip chunks get `Fresh("", [])` (a skip chunk shows no output, so any owned region under it is
   removed: this cleans up a chunk that was turned into an excerpt).
4. `plan_edits`: for each chunk where `stored(chunk) != fresh`, emit
   `Edit(chunk.owned.start, chunk.owned.end, render_owned(chunk.fence.indent, fresh))`. Unchanged
   chunks get no edit, so the diff of an unchanged note is only the `verified:` line.
5. `render_owned`: if stdout: `["", indent + ticks + "output", *body, indent + ticks]` where each
   non-empty stdout line gets `indent` prefixed, empty lines stay `""` (no trailing spaces).
   `_fence_ticks(stdout)`: for each stdout line take `line.lstrip()` and measure its LEADING
   backtick run; ticks = "`" * max(3, longest_run + 1). Measuring after `lstrip()` matters because
   the parser accepts indented closing fences, so an indented ``` line in stdout would otherwise
   close the output fence early on re-parse. If figures: `[""] + [f"{indent}![[{name}]]" ...]`.
   Both empty -> `[]`.
6. `apply_edits`: sort by `start` descending and splice, so earlier line numbers stay valid.
7. `stamp_verified(lines, fm_end, date.today().isoformat())`: replace the `verified:` line inside
   the frontmatter, else insert `verified: <date>` before the closing `---`; with no frontmatter,
   prepend `["---", f"verified: {date}", "---"]`. Frontmatter is above every edit, so stamping
   after the edits is safe.
8. `commit_note(note, "\n".join(lines), results)`, in this exact order:
   a. **Changed-on-disk guard.** Re-read `note.path` with exactly the call `read_note` uses,
      `Path.read_text(encoding="utf-8")` (same newline translation, str vs str, so CRLF or
      bytes/str mismatches cannot cause a false abort); if it differs from `note.text` (Obsidian
      autosaved during the 7 to 10 s run), raise `NoterunError("note changed during run,
      nothing written")`. No PNG has moved yet, so nothing is written. It is a plain
      `NoterunError`; `cmd_run` catches it around `commit_note` and returns 1 (a failed run, not
      a usage error).
   b. Move new PNGs in: `mkdir attachments/` only if there are new figures; `os.replace` each
      staged PNG into it (same filesystem, see Staging).
   c. `write_atomic(note.path, new_text)`: write to a temp file in the note's directory
      (`tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=note.path.parent,
      prefix=".noterun-", delete=False)`), write, flush, close, then
      `shutil.copymode(note.path, tmp)` so the note keeps its permissions (a NamedTemporaryFile
      is created 0600), then `os.replace(tmp, note.path)`. On any exception, unlink the temp file.
   d. Delete stale PNGs LAST: every name in the old owned embeds (all chunks) that is not in the
      new set, if it exists in `attachments/`. Only names read from owned embed lines are ever
      deleted; never glob. If anything before this fails, at worst an unreferenced new PNG
      remains; an embed never points at a deleted file. Known gap: PNGs of a chunk deleted from
      the note entirely are orphaned (its embeds vanish with its text); acceptable for v0.1.
9. Print per-chunk `updated` / `output added` / `ok`, then `noterun: verified <date>`. Exit 0.

`check` runs steps 1 to 3 (staging removed), then compares `stored(chunk)` to `fresh` per chunk.
`drift_lines`: unified diff of the normalized stdout (`"stored"`, `"now"`, `n=1`, like the old
script) plus, if figure name lists differ, one line `figures: stored [...] now [...]`. Figure
comparison is by file NAME list only (no pixel or hash comparison: out of scope). A skip chunk
with an owned region reports DRIFT (its fresh is empty).

### Export (`export.py`)
```python
def to_notebook(note: Note) -> dict
def write_notebook(note: Note) -> Path   # note.path.with_suffix(".ipynb"), json indent=1
```
- Frontmatter lines are dropped. Prose between runnable chunks -> markdown cells. Skip chunks are
  NOT cut out: their fence lines stay in the surrounding prose, so they render as markdown code
  blocks. Owned regions (output fences and owned embeds) are dropped. Runnable chunks -> code
  cells with dedented `code`, `execution_count: None`, `outputs: []`.
- Cell ids `f"cell-{n}"` (n = 0-based cell position; matches nbformat's `^[a-zA-Z0-9-_]+$`).
- `metadata`: `{"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
  "language_info": {"name": "python"}}`; `nbformat: 4`, `nbformat_minor: 5`.
- Empty/whitespace-only prose segments produce no cell.

### CLI (`cli.py`)
`[project.scripts] noterun = "noterun.cli:main"`. `main(argv: list[str] | None = None) -> int`.
Shared runtime flags on run/check/console/chunk: `--python PATH`, `--cwd DIR`, `--env KEY=VALUE`
(repeatable; nothing more). `--timeout SECONDS` on run/check/chunk (default 600), stored in
`Runtime.timeout_s`.

| Command | Behaviour | Exit codes |
|---|---|---|
| `run NOTE` | algorithm above | 0 written; 1 chunk error / interpreter crash / timeout / note changed during run (nothing written); 2 usage, parse, config, or no run-python |
| `check NOTE` | run without writing; per-chunk lines, diff for each DRIFT, then a final status word | 0 ok, unverified, or no runnable chunks; 1 DRIFT or ERROR; 2 usage/parse/config |
| `check DIR` | per note (see below): one line `<status>  <relpath>` with status ok / DRIFT / ERROR / unverified; no diffs (run `check NOTE` for detail) | 0 all ok/unverified; 1 any DRIFT or ERROR; 2 DIR missing |
| `console NOTE [--to NAME]` | `open_console` | child's exit code; 2 usage, unknown/skip target, no run-python |
| `chunk NOTE NAME` | see "`chunk` behaviour" below; writes nothing to the note or attachments, no `verified:` stamp | 0 ran; 1 a chunk error or interpreter crash/timeout; 2 usage, parse, config, no run-python, unknown or skip target |
| `export NOTE` | `write_notebook`, print `wrote <path>` | 0; 2 usage/parse |
| `list PATH [--names]` | PATH is a DIR (unchanged behaviour) or a single NOTE. Without `--names`: one line per chunk: `<relpath>:<line>  <label>  <first_line>`, `[skip] ` before first_line for skip chunks; line is the 1-based fence start; relpath is relative to DIR, or the note's file name for a NOTE. With `--names` (NOTE only): bare labels of RUNNABLE chunks, one per line, nothing else: all chunk names in note order, then the indices of the unnamed runnable chunks | 0 (also when empty; parse errors in DIR mode do not change it); 2 PATH missing, `--names` with a DIR, or (with `--names`) a parse error: message on stderr, stdout empty |
| `find VALUE DIR` | case-sensitive substring over owned output fence lines only: `<relpath>:<line>  <label>  <matching line stripped>` | 0 at least one match; 1 none; 2 DIR missing |

- **`check DIR` per-note sequence:** read text -> `parse_frontmatter` (cannot fail) ->
  `declares_runtime(note_dir, meta)`; if False, skip silently (a broken note that never declared
  a runtime is not this command's business) -> `parse_note`; a `NoterunError` here prints
  `ERROR  <relpath>  <message>` and the loop continues -> skip silently if no runnable chunks ->
  `resolve_runtime`; python None -> `unverified` (not executed, not a failure) -> run and compare.
  `InterpreterError` -> ERROR line, continue.
- **`chunk` behaviour:** parse, resolve runtime, `chunks = select_prefix(note, NAME)` (same
  resolution as `console --to`: name or 1-based index; skip chunks never run), `staging =
  make_staging(note, runtime)`, `run_chunks(note, chunks, runtime, staging)`. Then: for each EARLIER chunk,
  one status line on stderr (`ok <label>` or `ERROR <label>` + traceback); then the target's raw
  stdout written to stdout exactly as captured (not normalized, no prefix), so it pipes cleanly.
  If the target errored, its partial stdout is still written, then `ERROR` + traceback on stderr,
  exit 1. If an EARLIER chunk errored (the target never ran): statuses + traceback on stderr,
  NOTHING on stdout, exit 1. For each target figure, `figure: <abs path>` on stderr.
  Cleanup: the whole body after `make_staging` sits in `try/finally`; the `finally` removes the
  staging dir unless the target produced at least one figure. So an `InterpreterError` (crash,
  timeout, missing result file) also removes it, and is reported as ERROR, exit 1. Handler
  `cmd_chunk` ~25 lines; keep reporting in a helper.
- **`list` / `find` over a vault:** a note that fails to parse prints
  `noterun: <relpath>: <message>` on stderr and the loop continues; it never kills the command.
- "unverified" means `runtime.python is None`. (See Risks: notes that resolve a runtime through
  config but were never run report DRIFT, by design.)
- Note discovery `iter_notes(root: Path) -> list[Path]`: `sorted(root.rglob("*.md"))`, skipping any
  path with a component starting with `.` (`.obsidian`, `.trash`, `.noterun-*`). Shared by
  check/list/find. `relpath` is relative to the DIR argument.
- `main` catches `NoterunError` -> `noterun: <message>` on stderr, return 2; `InterpreterError`
  -> report ERROR, return 1 (catch the subclass first). The note-changed guard is caught inside
  `cmd_run` and returns 1.
- Dispatch with a `{name: handler}` dict, not an if-chain (keeps `main` complexity low).

### zsh completion (`completions/_noterun`, stdlib-only: no argcomplete)
- `#compdef noterun` file, ≤40 lines, using `_arguments` with `->state` dispatch:
  - first word: the seven subcommands with one-line descriptions (`_describe`).
  - NOTE / DIR / PATH arguments: `_files -g '*.md'` plus directories (`_files -/` alternative;
    `_alternative` or `_files -g '*.md(-.)' ` + `-/` is fine).
  - `chunk NOTE NAME`: NAME completes from the NOTE word already typed (`$line[2]` after
    `_arguments -C`). That word is as typed, with shell quoting and a literal `~`, so unquote and
    expand it first:
    ```zsh
    local note=${(Q)line[2]}; note=${note/#\~/$HOME}
    local -a labels; labels=(${(f)"$(noterun list "$note" --names 2>/dev/null)"})
    compadd -V names -- $labels
    ```
    `compadd -V names` makes an UNSORTED group, so the names-first order printed by `--names`
    survives zsh's default alphabetical sort. Put this in one small helper function
    (`_noterun_labels`) used by both callers.
  - `console NOTE --to NAME`: same helper for the `--to` value.
  - Runtime flags: `--python` (`_files`), `--cwd` (`_files -/`), `--env`, `--timeout` (no
    completion), `--names` on list.
- README documents setup and nothing else is installed: add
  `fpath=(~/local/code/python/developing/noterun/completions $fpath)` BEFORE `compinit` in
  `~/.zshrc`, or symlink `_noterun` into a directory already on `fpath`; then `rm -f ~/.zcompdump*`
  and open a new shell if completion does not appear.

## Style Notes
- All functions typed with docstrings; `@dataclass(frozen=True)` for every container;
  ≤4 params (group runtime flags into `Overrides`).
- Use the domain words consistently: chunk, fence, owned region, label, runtime, staging.
- Functions at risk of exceeding ~30 lines / complexity 10, and how to keep them in bounds:
  - `note.find_fences`: the fence scan loop. Extract `_closing_line(lines, start, fence_char, n)`
    so the loop body stays ≤3 levels.
  - `note.parse_note`: delegate chunk construction to `_build_chunks(lines, fences, stem)`;
    duplicate-name check in `_check_unique_names`; unclosed-fence check in `_check_closed`.
  - `note.find_owned`: two "skip at most one blank line" steps; helper `_after_blank(lines, pos)`.
  - `cli` run and check handlers share `_execute(note, overrides) -> (results, fresh)` (timeout
    travels inside `overrides`, never as a separate argument); reporting goes in
    `_report_chunks`; `check DIR` per-note logic in `_check_one(path, root, overrides) -> str`. Do not let `cmd_run` or `cmd_check` grow past ~30 lines.
  - `rewrite.commit_note`: four ordered sub-steps; keep each a 2 to 5 line block or a helper
    (`_move_figures`, `_delete_stale`).
  - `_driver.run_request`: loop + early break; per-chunk work in `run_chunk`/`save_figures`.
- No flag parameters: `child_environ` stays pure; the runner adds `MPLBACKEND` itself.
- `_driver.py` must stay 3.8-compatible; ruff `per-file-ignores` for `noterun/_driver.py` =
  `["UP"]` so pyupgrade does not modernize it.

## Files
All paths under `~/local/code/python/developing/noterun/` unless absolute.

| File | Action | Lines Est. | What |
|------|--------|------------|------|
| pyproject.toml | create | ~45 | setuptools, `requires-python = ">=3.11"` (tomllib), no runtime deps, `[project.scripts]`, ruff, pytest, mypy config |
| README.md | create | ~45 | install (pipx), the seven commands, zsh completion setup, pointer to the Explainer Template for the note format |
| completions/_noterun | create | ≤40 | zsh completion function (not counted in the Python ceiling) |
| .gitignore | create | ~10 | Python block from yplot2's .gitignore |
| noterun/__init__.py | create | ~3 | `__version__ = "0.1.0"` |
| noterun/note.py | create | ~140 | data model + parser + normalize + errors |
| noterun/config.py | create | ~60 | config search, runtime resolution |
| noterun/_driver.py | create | ~50 | in-interpreter driver |
| noterun/runner.py | create | ~85 | subprocess, staging, console, prefix selection |
| noterun/rewrite.py | create | ~120 | compare, render, edit, stamp, atomic write, PNG commit |
| noterun/export.py | create | ~40 | ipynb export |
| noterun/cli.py | create | ~180 | argparse + handlers (seven subcommands) |
| tests/conftest.py | create | ~40 | `make_note` fixture |
| tests/test_note.py | create | ~130 | parser |
| tests/test_config.py | create | ~80 | config |
| tests/test_runner.py | create | ~90 | driver + runner + prefix |
| tests/test_rewrite.py | create | ~80 | render/apply/stamp/commit |
| tests/test_cli.py | create | ~250 | end-to-end through `main(argv)` |
| tests/test_export.py | create | ~35 | notebook structure |
| ~/notes/200-projects/papers/2026/2026-deep-learning-predict-dms/.noterun.toml | create | 3 | project runtime |
| ~/notes/200-projects/papers/2026/2026-deep-learning-predict-dms/data-and-model-training.md | edit | -3 | drop run-* keys |
| ~/notes/200-projects/papers/2026/2026-deep-learning-predict-dms/r-squared-is-squared-pearson.md | edit | -3 | drop run-* keys |
| ~/notes/900-structure/templates/Explainer Template.md | edit | ~±10 | rules callout + frontmatter |
| ~/notes/900-structure/meta/metadata_schema.md | edit | ~±10 | "Runnable-note fields" section |
| ~/notes/900-structure/bin/note-run.py | delete | -230 | replaced; ONLY after user confirmation (Step 11) |

### pyproject.toml specifics
```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "noterun"
version = "0.1.0"
description = "Run the python chunks of Obsidian notes and keep their outputs in the note"
readme = "README.md"
requires-python = ">=3.11"   # tomllib
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-cov>=4.0", "ruff", "mypy"]

[project.scripts]
noterun = "noterun.cli:main"

[tool.pytest.ini_options]
addopts = "--cov=noterun --cov-report=term-missing --cov-fail-under=90"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "C90"]
[tool.ruff.lint.mccabe]
max-complexity = 10
[tool.ruff.lint.per-file-ignores]
"noterun/_driver.py" = ["UP"]

[tool.mypy]
python_version = "3.11"
disallow_untyped_defs = true

[tool.setuptools.packages.find]
include = ["noterun*"]
```

## Steps

1. [ ] **Scaffold.** `git init` the new directory (do not commit unless asked). Create
   pyproject.toml, README.md, .gitignore, `noterun/__init__.py`, empty `tests/`.
   Install for development into the py3 env:
   `/opt/homebrew/Caskroom/mambaforge/base/envs/py3/bin/python -m pip install -e '.[dev]'`.
   - Verify: `python -c "import noterun"`; `ruff check .` clean.

2. [ ] **`note.py`** + `tests/test_note.py`:
   - `test_frontmatter_quoted_values` (`verified: "2026-10-02"` -> `2026-10-02`)
   - `test_indented_fence_in_list_is_dedented`
   - `test_bare_fence_contents_are_inert` (a ```python line inside a bare fence is not a chunk)
   - `test_four_backtick_fence_contains_triple`
   - `test_callout_fence_is_inert` (`> ```python` never becomes a chunk)
   - `test_skip_with_trailing_text`, `test_name_directive`, `test_name_and_skip_both`
   - `test_duplicate_name_raises`; `test_bad_slug_raises` parametrized over `"1abc"`,
     `"has-hyphen"`, `"sp ace"`
   - `test_unclosed_python_raises`; `test_unclosed_fence_after_chunk_raises` (unclosed ```text
     after a chunk); `test_unclosed_fence_before_any_chunk_is_not_error` (note has no chunks)
   - `test_owned_output_then_embeds`, `test_owned_embeds_only`,
     `test_output_two_blank_lines_not_owned`
   - `test_hand_embed_with_path_or_size_not_owned`
   - `test_embed_with_extra_segments_not_owned`: stem `a`, line `![[a-b-1-1.png]]` directly under
     a chunk -> `owned.embeds == []`, `owned.end == fence.end`. Second case: stem `results-1`,
     `![[results-1-figure-1-2026-09-28.png]]` -> not owned.
   - `test_normalize_rstrips_and_drops_trailing_blanks`
   - Verify: `pytest tests/test_note.py`, `ruff check noterun/note.py` (complexity ≤10).

3. [ ] **`config.py`** + `tests/test_config.py`:
   - `test_find_config_searches_upward` (config two levels above the note)
   - `test_precedence_cli_over_frontmatter_over_config` (parametrized over the three keys)
   - `test_empty_frontmatter_value_falls_back_to_config`
   - `test_run_env_merges_per_key`, `test_child_environ_declared_wins_over_os_environ`
     (monkeypatch an os.environ key)
   - `test_relative_paths_resolve_against_declaring_file`, parametrized for run-cwd over
     `"sub"` (bare dir name), `".."`, `"../repo"`, declared once in config and once in frontmatter;
     run `monkeypatch.chdir` to an unrelated tmp dir first and assert the shell cwd is never used.
   - `test_bare_python_name_left_for_path` (`run-python = "python3"` stays `"python3"`) and
     `test_relative_python_with_separator_resolved`
   - `test_unknown_key_raises`, `test_env_pair_without_equals_raises`,
     `test_no_python_resolves_to_none`
   - `test_declares_runtime_from_meta_or_config`
   - Verify: pytest + ruff + mypy on the module.

4. [ ] **`_driver.py` + `runner.py`** + `tests/test_runner.py`:
   - `test_driver_is_python38_syntax`:
     `ast.parse(Path(noterun._driver.__file__).read_text(), feature_version=(3, 8))`.
   - In-process: `test_run_request_shares_namespace`, `test_run_request_stops_at_first_error`
     (traceback in `error`, later chunks absent), `test_stdout_captured_per_chunk`.
   - Subprocess with `sys.executable`: `test_run_chunks_end_to_end`,
     `test_stray_fd_write_does_not_corrupt_result` (chunk does `os.write(1, b"x\n")`),
     `test_missing_interpreter_raises_interpreter_error`, `test_timeout_raises` (`time.sleep`
     with `timeout_s=0.5`).
   - `test_select_prefix_by_name`, `test_select_prefix_by_index`,
     `test_select_prefix_excludes_skip_chunks`, `test_select_prefix_unknown_target_raises`,
     `test_select_prefix_skip_target_raises`, `test_select_prefix_none_is_all_runnable`.
   - `test_console_command_uses_ipython_when_present` and
     `test_console_command_falls_back_without_ipython` (monkeypatch `subprocess.run` returncode
     0 / 1; assert the exact argv lists). Do NOT launch IPython in tests.
   - Figures: `test_save_figures_without_pyplot_returns_empty`; `test_save_figures_writes_png`
     guarded by `pytest.importorskip("matplotlib")`.
   - Verify: pytest + ruff + mypy.

5. [ ] **`rewrite.py`** + `tests/test_rewrite.py`:
   - `test_render_owned_output_and_embeds`, `test_render_owned_indented`,
     `test_render_owned_no_output_embeds_directly_under_fence`, `test_render_owned_empty`
   - `test_fence_ticks_lengthen_when_stdout_has_backticks`, parametrized over a stdout line
     "```" and an INDENTED line "    ````" (expects 5 ticks); then round-trip both through
     `parse_note` + `stored` and assert the stdout survives intact.
   - `test_apply_edits_bottom_up`
   - `test_stamp_verified_replaces`, `test_stamp_verified_inserts`,
     `test_stamp_verified_creates_frontmatter`
   - `test_rendered_region_reparses_to_same_fresh`
   - `test_write_atomic_leaves_no_temp_file`
   - `test_commit_note_aborts_when_note_changed` (modify the file after parsing; assert
     `NoterunError`, file content is the modified text, no PNG moved into `attachments/`).
   - Verify: pytest + ruff + mypy.

6. [ ] **`export.py`** + `tests/test_export.py`:
   - `test_export_structure` (nbformat 4, minor 5, kernelspec, unique ids matching
     `^[a-zA-Z0-9-_]+$`, code cells for runnable chunks only, skip fence present in a markdown
     cell as a fenced block, no "```output" text and no owned embed anywhere, no frontmatter).
   - `test_export_validates_with_nbformat` under `pytest.importorskip("nbformat")`.
   - Verify: pytest + ruff + mypy.

7. [ ] **`cli.py`** + `tests/conftest.py` + `tests/test_cli.py`. `make_note(tmp_path, body,
   *, python=sys.executable, verified="2026-01-01") -> Path` writes frontmatter + body. All CLI
   tests call `main([...])` and use `capsys`. Required cases:
   - `test_run_writes_output_fences_and_stamps_verified`
   - `test_run_with_zero_runnable_chunks`: (a) note with only a `# skip` chunk and no owned
     region, no run-python anywhere -> prints "no runnable python fences", exit 0, bytes
     unchanged; (b) same note with a stale ```output fence under the skip chunk -> exit 0, fence
     removed, `verified:` line unchanged.
   - `test_run_twice_is_idempotent` (second run leaves the bytes identical; date is already today)
   - `test_check_reports_drift_with_diff_exit_1` (hand-edit a stored output; assert `DRIFT`,
     a `-`/`+` diff line, exit 1, file bytes unchanged, no `.noterun-*` left in the note dir)
   - `test_indented_fence_in_list_gets_indented_output_fence`
   - `test_skip_chunk_not_executed` (skip chunk does `raise SystemExit(3)`; run succeeds)
   - `test_skip_chunk_stale_output_removed_on_run_drift_on_check`: a `# skip` chunk with an
     ```output fence under it -> `check` reports DRIFT exit 1; `run` removes the fence, exit 0;
     `check` then ok.
   - `test_stale_output_refreshed`
   - `test_chunk_without_stdout_gets_no_fence` (and a previously stored fence is removed)
   - `test_error_stops_run_writes_nothing_exit_1` (under `pytest.importorskip("matplotlib")`):
     chunk 1 imports pyplot and makes a figure, chunk 2 raises. Assert exit 1, note bytes
     unchanged, `attachments/` does not exist, no `.noterun-*` dir left, chunk 3 reported
     "not run".
   - `test_unclosed_output_fence_raises_and_writes_nothing`: chunk followed by an unclosed
     ```output fence and more prose; `run` exits 2 with the line number in stderr, bytes unchanged.
   - `test_run_aborts_when_note_changed_during_run`: chunk 1 appends a line to the note file
     itself (its path passed in the chunk code), simulating an Obsidian autosave. Assert exit 1,
     "note changed during run" in output, file holds the chunk's modification only (no output
     fences added, `verified` not restamped).
   - `test_run_without_python_exit_2` and `test_check_without_python_reports_unverified_exit_0`
   - `test_check_dir_one_line_per_note`: ok note, drifting note, unverified note, note with no
     chunks (absent from output), a note with an unclosed python fence that declares a runtime
     (ERROR line, loop continues), and a broken note with no runtime and no config (absent).
     Exit 1.
   - `test_list_dir`, `test_list_continues_past_unparseable_note` (stderr names it, exit 0),
     `test_find_reports_note_chunk_and_line`, `test_find_no_match_exit_1`,
     `test_find_continues_past_unparseable_note`
   - `test_figure_capture_creates_png_and_embed_then_removes_stale`
     (`pytest.importorskip("matplotlib")`; valid because the test's run-python IS
     sys.executable): run 1, chunk named `plot` makes one figure -> `attachments/<stem>-plot-1.png`
     exists and `![[<stem>-plot-1.png]]` is under the chunk; edit the chunk to not plot, run 2 ->
     PNG gone and embed line gone. Before run 1, place `![[<stem>-x-1-1.png]]` DIRECTLY under the
     chunk's closing fence (so it sits where owned embeds go and actually exercises the ownership
     regex), followed by `![[other.png|300]]`, and create a hand-made
     `attachments/<stem>-x-1-1.png`. After both runs, both hand lines are still in the note and
     the hand-made PNG still exists.
   - `test_check_does_not_touch_attachments` (check after removing the PNG reports DRIFT on the
     figure list and does not recreate the file)
   - `test_config_override_order_via_cli` (`.noterun.toml` with a bogus python, frontmatter with
     sys.executable -> runs; `--python /nonexistent` -> exit 1)
   - `test_export_command_writes_ipynb`
   - `test_chunk_prints_target_stdout_only`: three chunks print `a`, `b`, `c`; `chunk NOTE 2`
     -> stdout is exactly `"b\n"`, stderr has `ok 1`, chunk 3 never ran (it would raise).
     Also address a named target (`chunk NOTE plot`-style) once.
   - `test_chunk_earlier_error_exit_1`: chunk 1 raises, target is chunk 2 -> exit 1, stdout
     empty, stderr has `ERROR 1` and the traceback, no `.noterun-*` dir left.
   - `test_chunk_writes_nothing`: note bytes and mtime unchanged, no `attachments/`, no
     `verified:` change, no `.noterun-*` dir left when the target made no figures.
   - `test_chunk_skip_target_exit_2`: target is a `# skip` chunk -> exit 2, stderr names its line.
   - `test_chunk_leaves_staged_figure_and_prints_path` (`pytest.importorskip("matplotlib")`):
     target makes one figure -> stderr has `figure: <path>`, that PNG exists inside a
     `.noterun-<stem>-*` dir in the note's folder, `attachments/` does not exist.
   - `test_check_removes_stale_staging_dir`: create `.noterun-<stem>-old123/` with mtime set
     2 hours back (`os.utime`), `.noterun-<stem>-new123/` with a current mtime, and
     `.noterun-<stem>-x-old123/` (another stem `<stem>-x`, also 2 hours old). Run
     `check NOTE` -> only the first is gone; the fresh one (possibly another live process) and
     the other stem's dir remain.
   - `test_list_names_prints_bare_labels_for_single_note`: chunks `# name: setup`, unnamed,
     `# skip`, `# name: plot`, unnamed -> `list NOTE --names` stdout is exactly
     `"setup\nplot\n2\n5\n"`; `list DIR --names` -> exit 2.
   - Verify: full suite.

8. [ ] **Toolchain** (from the repo root):
   ```bash
   ruff check --fix . && ruff format . && ruff check .
   mypy noterun
   pytest            # addopts enforce --cov-fail-under=90
   wc -l noterun/*.py   # total must be <= 1215 (code lines were ~700; wc -l adds blanks and docstrings)
   ```
   All must pass. If over 1215: remove duplication, then delete the defensive check in
   `find_owned`, then drop `--env`, in that order.

9. [ ] **Install and smoke-test on the real notes (before touching any note).**
   ```bash
   pipx install -e ~/local/code/python/developing/noterun
   cd ~/notes/200-projects/papers/2026/2026-deep-learning-predict-dms
   noterun check data-and-model-training.md      # must print ok, exit 0 (~7 s)
   noterun check r-squared-is-squared-pearson.md # must print ok, exit 0 (~7 s)
   noterun list .
   noterun find 0.731643 .                       # hits in both notes
   ls -a .                                       # no .noterun-* left behind
   noterun chunk r-squared-is-squared-pearson.md 4 | head -1   # "project r_squared : 0.731643"
   noterun list r-squared-is-squared-pearson.md --names        # 1 2 4 (fence 3 is # skip)
   ```
   Manual completion check: in a fresh `zsh -l` whose `~/.zshrc` has the `fpath=(...)` line before
   `compinit`, type `noterun chunk r-squared-is-squared-pearson.md <TAB>`: it must offer `1 2 4`;
   `noterun console r-squared-is-squared-pearson.md --to <TAB>` likewise. Then check quoting and
   tilde: copy that note to `"/tmp/space name.md"` (completion only calls `list --names`, which
   needs no runtime) and confirm `noterun chunk /tmp/space\ name.md <TAB>` and
   `noterun chunk ~/notes/200-projects/papers/2026/2026-deep-learning-predict-dms/r-squared-is-squared-pearson.md <TAB>`
   both offer a NON-EMPTY menu in the order `1 2 4`. Delete the copy afterwards. Do not edit the user's
   `~/.zshrc`. TAB completion cannot be scripted from a non-interactive shell, so if you cannot
   run it, verify `zsh -n completions/_noterun` (syntax) and report the TAB check as left to the
   user.
   pipx's default Python here is py3 3.11, which satisfies `>=3.11`. If either check reports
   DRIFT, STOP and report: it means the parser or normalization disagrees with note-run.py.

10. [ ] **Migration edits (reversible; ~/notes is NOT under git).**
    - FIRST back up into `~/notes/.trash/noterun-migration-backup/` (`mkdir -p`; durable, and a
      dot-directory so Obsidian ignores it; report the path):
      `data-and-model-training.md`, `r-squared-is-squared-pearson.md`,
      `~/notes/900-structure/templates/Explainer Template.md`,
      `~/notes/900-structure/meta/metadata_schema.md`, and `~/notes/900-structure/bin/note-run.py`.
      Use `cp -p`.
    - Create `~/notes/200-projects/papers/2026/2026-deep-learning-predict-dms/.noterun.toml`:
      ```toml
      run-python = "/opt/homebrew/Caskroom/mambaforge/base/envs/py3/bin/python"
      run-cwd = "/Users/jyesselman2/Library/CloudStorage/Dropbox/papers/2026-deep-learning-predict-dms/dms_ml"
      run-env = "OMP_NUM_THREADS=4"
      ```
    - Delete the `run-python`, `run-cwd`, `run-env` frontmatter lines from
      `data-and-model-training.md` and `r-squared-is-squared-pearson.md` (the config holds the
      identical values; keep `verified:`). This is the live test of config resolution.
    - `Explainer Template.md`: remove the three `run-*` frontmatter keys (the project's
      `.noterun.toml` supplies them; an empty `run-cwd:` would only invite confusion). Keep
      `verified:`. Replace the rules-callout bullets that mention `note-run.py`, the runner, and
      "One heavy real-data fence per note" with:
      ```
      > - Every ```python fence is a chunk. Chunks run in order in one interpreter session:
      >   `noterun run <this note>`. `noterun check <note or folder>` reruns and diffs without writing,
      >   `noterun console <this note> --to <name>` runs setup through that chunk and leaves you in IPython,
      >   `noterun chunk <this note> <name>` runs setup through that chunk and prints its output without writing,
      >   `noterun export <this note>` writes a notebook beside the note.
      > - The first chunk is **Setup**; later chunks may use its names. Start a chunk with `# name: <slug>`
      >   (letters, digits, underscores; unique in the note) to address it by name; unnamed chunks are
      >   numbered from 1. A fence that must not run starts with `# skip`.
      > - The runner writes each chunk's stdout into a ```output fence under it, saves open matplotlib
      >   figures to `attachments/<note>-<chunk>-<k>.png` with an embed line under the output, and stamps
      >   `verified:`. Never edit those fences or embed lines, and never type into prose a number the runner could print.
      > - Each chunk runs in under about 10 s. Heavier work belongs in a repo script whose table a chunk reads.
      > - The runtime (`run-python`, `run-cwd`, `run-env`) comes from `.noterun.toml` in the project folder
      >   or above it; the same keys in frontmatter override it. `run-python` pins the published environment,
      >   never whatever is on PATH.
      ```
      Keep the other bullets (one question per note; say what each fence needs; numbers come from
      output fences or the claims ledger; excerpts verbatim with `# skip: excerpt of <file>`).
      Make the template's Setup fence start with `# name: setup`.
    - `metadata_schema.md`, section "Runnable-note fields": change the intro to: notes whose
      ```python chunks are run by `noterun` (`noterun run|check|console|chunk|export|list|find`); the
      runtime keys usually live in a `.noterun.toml` (same three keys, TOML strings) found by
      searching upward from the note; frontmatter overrides it, CLI flags override both;
      a chunk may start with `# name: <slug>` or `# skip`. Keep the table; edit the `run-*` rows to
      say "overrides `.noterun.toml`" and the `verified` row to name `noterun run`.
    - Final verify, BEFORE any deletion:
      `noterun check ~/notes/200-projects/papers/2026/2026-deep-learning-predict-dms/data-and-model-training.md`
      and the same for `r-squared-is-squared-pearson.md`; both must print ok, exit 0. If not,
      restore from the backups and stop.

11. [ ] **HUMAN CHECKPOINT, then the one irreversible step.**
    - STOP. Report to the user: both checks ok, the backup directory path, and that
      `~/notes/900-structure/bin/note-run.py` is ready to delete. Wait for explicit confirmation.
    - Only after the user confirms: `rm ~/notes/900-structure/bin/note-run.py`, then
      `grep -rn "note-run" ~/notes --include=*.md` must return nothing. (The Claude memory note
      that mentions it is the user's job, not yours.)

## Acceptance criteria
- `ruff check .`, `ruff format --check .`, `mypy noterun`, `pytest` (coverage ≥90%) all pass;
  `wc -l noterun/*.py` total ≤ 1215 (the 700 figure counted code lines only; `wc -l` adds blanks and docstrings); `completions/_noterun` ≤ 40 lines.
- No runtime dependencies; `noterun` never imports matplotlib or IPython itself.
- `noterun check` on both real notes prints ok and exits 0, both before and after migration.
- A failed or aborted `run` leaves the note bytes, `attachments/`, and the note dir unchanged.
- `chunk` never writes to the note or `attachments/`; only a figure-producing `chunk` leaves a
  `.noterun-<stem>-*` dir, cleared by the next run/check/chunk of that note.
- Completion offers chunk labels for `chunk` and `console --to` (manual check, Step 9).
- Template and schema name `noterun`, `# name:`, `.noterun.toml`. `note-run.py` is deleted only
  after the user confirms.

## Risks
- **`check DIR` on the dms project folder will exit 1 after migration.** With the folder-level
  `.noterun.toml`, four notes there resolve a runtime but were never written for the runner:
  `cross-validation-groupkfold-and-cross-val-predict.md` (2 python fences, 0 output),
  `feature-attribution-permutation-ablation-shap.md` (8, 0), `gradient-boosting-how-the-model-learns.md`
  (6, 0), `model-features.md` (4, 0). Two of them already carry a hand-set `verified: "2026-10-02"`.
  This follows the user's definition (only a missing run-python means "unverified"). Do NOT run
  or edit those notes; report this to the user, who can mark their excerpts `# skip` or run them.
  The smoke test uses `check NOTE` on the two real notes only, so it is unaffected.
- The changed-on-disk guard compares full text; it cannot catch an edit that lands between the
  re-read and `os.replace` (milliseconds). Acceptable.
- Obsidian resolves `![[file.png]]` by file name vault-wide; the `<stem>-<label>-<k>` scheme is
  unique as long as note stems are unique, which Obsidian already encourages.
- Index-based labels shift when a python fence is inserted above; the PNG for that chunk is then
  renamed on the next run (old one deleted via its owned embed). Name chunks that plot.
- matplotlib's `plt.show()` under Agg prints a UserWarning to stderr; that is passthrough noise,
  not an error.
