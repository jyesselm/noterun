"""Runs inside the note's interpreter.

Python 3.8 syntax and stdlib only: no `match`, no PEP 604 unions outside annotations, no 3.9+
APIs such as `str.removeprefix`, `dict |`, `zoneinfo`. Imports nothing from noterun.
"""

from __future__ import annotations

import contextlib
import io
import json
import linecache
import os
import sys
import traceback
from typing import Any, Dict, List, Optional, Tuple


def save_figures(figure_dir: str, stem: str, label: str) -> List[str]:
    """Save and close open matplotlib figures, only if pyplot is already imported."""
    pyplot = sys.modules.get("matplotlib.pyplot")
    if pyplot is None:
        return []
    paths = []
    for k, number in enumerate(pyplot.get_fignums(), start=1):
        path = os.path.join(figure_dir, "{}-{}-{}.png".format(stem, label, k))
        pyplot.figure(number).savefig(path)
        paths.append(path)
    pyplot.close("all")
    return paths


def run_chunk(chunk: Dict[str, Any], namespace: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Execute one chunk; return (stdout, traceback or None)."""
    filename = "<chunk {}>".format(chunk["label"])
    code = chunk["code"]
    linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            exec(compile(code, filename, "exec"), namespace)
    except BaseException:
        return buffer.getvalue(), traceback.format_exc()
    return buffer.getvalue(), None


def run_request(request: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Run chunks in one shared namespace, stopping after the first error."""
    namespace = {"__name__": "__main__"}  # type: Dict[str, Any]
    results = []
    for chunk in request["chunks"]:
        stdout, error = run_chunk(chunk, namespace)
        figures = []  # type: List[str]
        if error is None:
            figures = save_figures(request["figure_dir"], request["stem"], chunk["label"])
        result = {"index": chunk["index"], "stdout": stdout, "error": error, "figures": figures}
        results.append(result)
        if error is not None:
            break
    return results


def main() -> None:
    """Read the request from stdin and write the results file."""
    request = json.load(sys.stdin)
    results = run_request(request)
    with open(request["result_path"], "w", encoding="utf-8") as fh:
        json.dump(results, fh)


if __name__ == "__main__":
    main()
