"""What the checks of the last Vibe run decided, for ./tn to print when Vibe exits. Standard library only.

usage: python -m traceable.verdict --count <folder>            number of checks recorded so far
       python -m traceable.verdict <folder> [<count before>]    the verdict of the checks recorded since then
"""
from __future__ import annotations
import json, pathlib, sys

LOUD = "!" * 78
_CALCULATED = ("calculated", "calculated_with_assumption")

def _checks(folder: pathlib.Path) -> list[dict] | None:
    path = folder / ".traceable" / "ledger.json"
    return (json.loads(path.read_text()).get("checks") or []) if path.exists() else None

def count(folder) -> int:
    try:
        return len(_checks(pathlib.Path(folder)) or [])
    except (OSError, ValueError, AttributeError):
        return 0

def _loud(*lines: str) -> str:
    return "\n".join([LOUD, *(f"!!! {line}" for line in lines), LOUD])

def summary(folder, before: int | None = None) -> str:
    """One line for the last check since `before` (the count when the run started), or a loud NOT CHECKED block."""
    folder = pathlib.Path(folder)
    report = (folder / ".traceable" / "report.html").resolve()
    try:
        checks = _checks(folder)
    except (OSError, ValueError, AttributeError) as e:
        return _loud(f"traceable: NOT CHECKED: the ledger could not be read ({type(e).__name__})", f"report: {report}")
    if checks is None:
        return "traceable: no document was read in this folder, so nothing was checked"
    new = checks[before:] if before is not None else checks[-1:]
    if not new:
        return _loud("traceable: NOT CHECKED: no check was recorded for this run",
                     "usually a turn, price or token limit ends the run before Vibe's post-agent hooks can check it;",
                     "otherwise: is .vibe/hooks.toml installed in this folder, and is the folder trusted?")
    last = new[-1]
    if last.get("decision") == "not_checked":
        return _loud(f"traceable: NOT CHECKED: {last.get('reason')}", f"report: {report}")
    figs = last.get("figures") or []
    traced = sum(1 for f in figs if f.get("status") == "traced")
    calculated = sum(1 for f in figs if f.get("status") in _CALCULATED)
    asked = sum(1 for f in figs if f.get("status") == "from_question")
    head = ("traceable: checked: allow" if last.get("decision") == "allow" else
            f"traceable: DENIED (attempt {last.get('attempt')}): the answer shown still has untraced figures")
    line = (f"{head} · {len(figs)} figure{'' if len(figs) == 1 else 's'}: {traced} traced, {calculated} calculated, "
            f"{len(figs) - traced - calculated - asked} untraced")
    if asked:
        line += f", {asked} from your question, not verified"
    line += f" · report: {report}"
    skipped = sum(1 for c in new if c.get("decision") == "not_checked")
    if skipped:
        line += "\n" + _loud(f"traceable: NOT CHECKED: {skipped} of the {len(new)} answers in this run")
    return line

def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "--count":
        print(count(argv[1]))
    elif len(argv) in (1, 2):
        print(summary(argv[0], int(argv[1]) if len(argv) == 2 and argv[1].isdigit() else None))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
