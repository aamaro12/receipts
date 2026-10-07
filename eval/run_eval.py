#!/usr/bin/env python3
"""Receipts eval: the questions in eval/questions.yaml, in three arms, scored against eval/answers.yaml.

usage (from the repo root, after ./install.sh, with the demo documents and their OCR cache in demo/*/.traceable/ocr):
  uv run --project server python eval/run_eval.py [--runs 3] [--arms read,calc,full] [--only f3,t1]
  uv run --project server python eval/run_eval.py --score-only        re-score eval/work, no Vibe runs

Arms, each with its own VIBE_HOME so their configurations never mix:
  read   eval/.home-read   traceable_read_document only (TRACEABLE_TOOLS=read): no prompt rules, skill, calculator or hook
  calc   eval/.home-calc   the tools, the prompt rules and the skill, but no hook in the run folders: nothing is checked
  full   eval/.home-full   everything, including the post_agent hook
Each run gets a fresh folder eval/work/<arm>/<qid>/<run>/ with copies of the question's PDFs and of the shipped OCR
cache, and runs: perl -e 'alarm 300; exec @ARGV' vibe --legacy-harness -p "Using <paths>, <question>" --trust
--max-turns 16 --max-price 0.5 --output text, with stdin closed. The full arm runs first, and the eval stops when a
full-arm run gives an answer without a hook check, or with a NOT CHECKED one: a harness change would otherwise turn the
full arm into the calc arm without anyone noticing.

Headline: questions answered correctly and fully traced. Correct: every expected figure of the key is in the final
answer (any rounding the check accepts, with the same percent flag, unit, currency and sign), no figure is wrong, and
every must_say pattern matches. Wrong: a figure that is neither in the key (expect or allow), nor a value stated in the
question's documents (the check's own matching, without citations), nor a number from the question. Fully traced: the
check finds no problem in the final answer against that run's ledger. Writes results.md (raw counts) and denials.md
(every denial, for a manual false-alarm review) next to the work folder.
"""
from __future__ import annotations
import argparse, dataclasses, hashlib, json, os, pathlib, re, shutil, statistics, subprocess, sys, time
from dataclasses import dataclass

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))
from traceable.check import check_answer, select_answer  # noqa: E402
from traceable.ledger import Ledger  # noqa: E402
from traceable.numbers import extract, parse_block, table_captions, matches_in_block, close_to_display, currency_code, Figure, ParsedBlock  # noqa: E402
from traceable.ocr import MODEL  # noqa: E402

ARMS = ("read", "calc", "full")
DOCS = {"relx": "demo/finance/relx_income_statement.pdf", "diageo": "demo/finance/diageo_income_statement.pdf",
        "bol": "demo/logistics/bill_of_lading.pdf", "invoice": "demo/logistics/commercial_invoice.pdf",
        "arrival": "demo/logistics/arrival_notice.pdf"}
TURN_LIMIT = "Turn limit of 16 reached"
_OK = ("traced", "calculated", "calculated_with_assumption")

class EvalStopped(SystemExit):
    """The eval cannot go on (a failed install, a missing OCR cache, or a full-arm run that was not checked)."""

@dataclass
class Result:
    code: int
    stdout: str
    stderr: str
    seconds: float

@dataclass
class Ctx:
    repo: pathlib.Path
    out: pathlib.Path
    runner: object

def subprocess_runner(cmd: list[str], cwd: pathlib.Path, env: dict) -> Result:
    """Run a command with stdin closed: vibe -p waits forever on an open stdin."""
    t0 = time.monotonic()
    p = subprocess.run(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return Result(p.returncode, p.stdout, p.stderr, round(time.monotonic() - t0, 1))

# --- the eval files: a small YAML subset, read without PyYAML ---

def _uncomment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"" and (i == 0 or line[i - 1] in " [-"):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()

def _scalar(s: str):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    if s.startswith('"'):
        return json.loads(s)
    if s.startswith("[") and s.endswith("]"):
        return [_scalar(x) for x in s[1:-1].split(",") if x.strip()]
    if s in ("true", "false"):
        return s == "true"
    return int(s) if re.fullmatch(r"-?\d+", s) else s

def read_yaml(path) -> list[dict]:
    """Read the YAML subset the eval files use: a list of mappings, one "key: value" per line, where a value is a plain,
    'single' or "double" quoted scalar, a flow list [a, b] of plain words, or a block list of "- item" lines."""
    items: list[dict] = []
    key = None
    for n, raw in enumerate(pathlib.Path(path).read_text().splitlines(), 1):
        line = _uncomment(raw)
        if not line.strip():
            continue
        indent, text = len(line) - len(line.lstrip(" ")), line.strip()
        if indent == 0 and text.startswith("- "):
            items.append({})
            key, indent, text = None, 2, text[2:].strip()
        if items and indent == 4 and text.startswith("- ") and isinstance(items[-1].get(key), list):
            items[-1][key].append(_scalar(text[2:]))
            continue
        k, sep, v = text.partition(":")
        if not items or indent != 2 or not sep or not re.fullmatch(r"[A-Za-z_]\w*", k):
            raise ValueError(f"{path}:{n}: unsupported YAML for this reader: {raw!r}")
        key = k
        items[-1][key] = _scalar(v) if v.strip() else []
    return items

def load(questions: pathlib.Path, answers: pathlib.Path) -> list[dict]:
    """The questions, each with its answer key under "key"."""
    keys = {a["id"]: a for a in read_yaml(answers)}
    qs = read_yaml(questions)
    for q in qs:
        if q["id"] not in keys:
            raise ValueError(f"{answers}: no answer key for {q['id']}")
        if any(d not in DOCS for d in q["docs"]):
            raise ValueError(f"{questions}: {q['id']} names an unknown document: {q['docs']}")
        q["key"] = keys[q["id"]]
    return qs

# --- running ---

def prompt_for(q: dict, run_dir: pathlib.Path) -> str:
    paths = " and ".join(str(run_dir / pathlib.Path(DOCS[d]).name) for d in q["docs"])
    return f"Using {paths}, {q['question'][:1].lower()}{q['question'][1:]}"

def vibe_command(prompt: str) -> list[str]:
    return ["perl", "-e", "alarm 300; exec @ARGV", "vibe", "--legacy-harness", "-p", prompt, "--trust",
            "--max-turns", "16", "--max-price", "0.5", "--output", "text"]

def setup_home(arm: str, ctx: Ctx) -> None:
    """install.sh into the arm's own VIBE_HOME; the read arm then drops the calculator, the prompt rules and the skill."""
    home = ctx.out / f".home-{arm}"
    env = {**os.environ, "VIBE_HOME": str(home)}
    r = ctx.runner(["bash", str(ctx.repo / "install.sh")], ctx.repo, env)
    if r.code:
        raise EvalStopped(f"install.sh failed for the {arm} arm: {r.stderr[-400:]}")
    if arm == "read":
        py = str(ctx.repo / "server/.venv/bin/python")
        r = ctx.runner([py, "-m", "traceable.install_config", "config", str(home / "config.toml"), py, "--read-only"],
                       ctx.repo, {**env, "PYTHONPATH": str(ctx.repo / "server")})
        if r.code:
            raise EvalStopped(f"the read-only config failed: {r.stderr[-400:]}")
        shutil.rmtree(home / "skills" / "traceable-numbers", ignore_errors=True)

def prepare_run(arm: str, q: dict, run: int, ctx: Ctx) -> pathlib.Path:
    """A fresh folder with the question's PDFs and the shipped OCR cache; the full arm also gets the hook."""
    d = ctx.out / "work" / arm / q["id"] / str(run)
    if d.exists():
        shutil.rmtree(d)
    (d / ".traceable" / "ocr").mkdir(parents=True)
    for doc in q["docs"]:
        pdf = ctx.repo / DOCS[doc]
        shutil.copy(pdf, d / pdf.name)
        for cached in (pdf.parent / ".traceable" / "ocr").glob("*-blocks.json"):
            shutil.copy(cached, d / ".traceable" / "ocr" / cached.name)
    if arm == "full":
        (d / ".vibe").mkdir()
        shutil.copy((ctx.repo / DOCS[q["docs"][0]]).parent / ".vibe" / "hooks.toml", d / ".vibe" / "hooks.toml")
    return d

def find_session(home: pathlib.Path, run_dir: pathlib.Path) -> pathlib.Path | None:
    """The Vibe session whose meta.json names the run folder as its working directory."""
    want = run_dir.resolve()
    for meta in sorted((home / "logs" / "session").glob("*/meta.json"), reverse=True):
        try:
            wd = json.loads(meta.read_text()).get("environment", {}).get("working_directory")
        except (OSError, ValueError):
            continue
        if wd and pathlib.Path(wd).resolve() == want:
            return meta.parent
    return None

def run_one(arm: str, q: dict, run: int, ctx: Ctx) -> dict:
    d = prepare_run(arm, q, run, ctx)
    home = ctx.out / f".home-{arm}"
    r = ctx.runner(vibe_command(prompt_for(q, d)), d, {**os.environ, "VIBE_HOME": str(home)})
    session = find_session(home, d)
    stats = (json.loads((session / "meta.json").read_text()).get("stats") or {}) if session else {}
    rec = {"arm": arm, "qid": q["id"], "run": run, "dir": str(d), "exit": r.code, "seconds": r.seconds,
           "answer": r.stdout.strip(), "turn_limit": TURN_LIMIT in r.stderr, "stderr_tail": r.stderr[-400:],
           "session": str(session) if session else None, "cost": stats.get("session_cost"), "steps": stats.get("steps")}
    (d / "run.json").write_text(json.dumps(rec, indent=1))
    return rec

def full_arm_problem(rec: dict) -> str | None:
    """Why a full-arm run proves nothing about the check: a NOT CHECKED record, or an answer with no check at all."""
    ledger = pathlib.Path(rec["dir"]) / ".traceable" / "ledger.json"
    checks = json.loads(ledger.read_text()).get("checks", []) if ledger.exists() else []
    where = f"{rec['arm']}/{rec['qid']}/{rec['run']}"
    skipped = [c for c in checks if c.get("decision") == "not_checked"]
    if skipped:
        return f"{where}: NOT CHECKED ({skipped[-1].get('reason')})"
    if rec["answer"] and not checks:
        return f"{where}: an answer with no hook check (is the hook installed, and is the harness the legacy one?)"
    return None

# --- scoring ---

def _fig(text: str) -> Figure:
    figs = extract(str(text), apply_exemptions=False)
    if len(figs) != 1:
        raise ValueError(f"answer key figure {text!r} is not one figure")
    return figs[0]

def key_figures(q: dict) -> tuple[list[Figure], list[Figure]]:
    return [_fig(s) for s in q["key"].get("expect") or []], [_fig(s) for s in q["key"].get("allow") or []]

def _sign(f: Figure) -> int:
    return -1 if f.neg or f.direction < 0 else 1 if f.direction > 0 else 0

def same_figure(a: Figure, e: Figure) -> bool:
    """Does answer figure a state key figure e? Same percent flag, unit, currency and sign; any rounding the check
    accepts for a [Cn] citation (half a unit of the last shown digit, at most 25% away)."""
    if a.unparsed or a.is_percent != e.is_percent:
        return False
    bare = not (a.currency or a.scale or a.unit or a.is_percent)
    if not a.is_percent and a.unit != e.unit and not bare:
        return False                                             # 173.2 pence is not 173.2 cents; $23.1m is not 23.1c
    ca, ce = currency_code(a.currency), currency_code(e.currency)
    if ca and ce and ca != ce:
        return False                                             # £20.3bn is not $20,269m
    if _sign(a) and _sign(a) != (-1 if e.neg else 1):
        return False
    return close_to_display(a, abs(e.base)) or (bare and close_to_display(a, abs(e.value)))

def _asked(question: str) -> set:
    return {(f.value, f.scale, f.is_percent, f.unit) for f in extract(question or "")}

_OPS = re.compile(r"[/×÷^]|\d\s*[*x]\s*\d")

def split_claims(text: str) -> tuple[str, str]:
    """(claims, results): a formula line (one with "=" and an arithmetic operator) is working, not a claim; its result,
    after the last "=", may supply a key figure but never counts as wrong (pilot run read/f3/1)."""
    claims, results = [], []
    for line in (text or "").splitlines():
        if "=" in line and _OPS.search(line):
            results.append(line.rsplit("=", 1)[1])
        else:
            claims.append(line)
    return "\n".join(claims), "\n".join(results)

def _aside(a: Figure) -> Figure | None:
    """A parenthesised amount in prose ("... match (USD 759,500.00).") is an aside, not an accounting negative: return the
    unsigned reading to try when the signed one fails (real run calc/l2/1). An explicit minus is never an aside."""
    raw = a.raw.strip()
    return dataclasses.replace(a, neg=False) if a.neg and raw.startswith("(") and raw.endswith(")") else None

def wrong_figures(text: str, q: dict, blocks: list[ParsedBlock]) -> list[str]:
    """Figures that are neither in the key, nor stated in the question's documents, nor numbers from the question."""
    expected, allowed = key_figures(q)
    asked, out = _asked(q["question"]), []
    for a in extract(split_claims(text)[0]):
        if a.unparsed or any(same_figure(a, e) for e in expected + allowed):
            continue
        if (a.value, a.scale, a.is_percent, a.unit) in asked or any(matches_in_block(a, pb) for pb in blocks):
            continue
        u = _aside(a)
        if u is not None and (any(same_figure(u, e) for e in expected + allowed) or any(matches_in_block(u, pb) for pb in blocks)):
            continue
        out.append(a.raw.strip())
    return out

def score_answer(answer: str, q: dict, blocks: list[ParsedBlock]) -> dict:
    expected, _ = key_figures(q)
    claims, results = split_claims(answer)
    figs = extract(claims)
    found = figs + extract(results)
    found += [u for u in (_aside(a) for a in found) if u is not None]
    missing = [str(s) for s, e in zip(q["key"].get("expect") or [], expected) if not any(same_figure(a, e) for a in found)]
    wrong = wrong_figures(answer, q, blocks)
    unsaid = [p for p in q["key"].get("must_say") or [] if not re.search(p, answer or "", re.I)]
    return {"correct": bool((answer or "").strip()) and not (missing or wrong or unsaid), "missing": missing,
            "wrong": wrong, "unsaid": unsaid, "figures": [a.raw.strip() for a in figs]}

def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return ""

def drafts_and_denials(messages: list[dict]) -> tuple[list[str], list[str]]:
    """Answers the hook denied (each followed by an injected message) and the denial texts, for the last question."""
    real = [i for i, m in enumerate(messages) if m.get("role") == "user" and not m.get("injected")]
    drafts, denials, last = [], [], None
    for m in messages[(real[-1] + 1) if real else 0:]:
        if m.get("role") == "assistant" and _text(m.get("content")).strip():
            last = _text(m.get("content"))
        elif m.get("role") == "user" and m.get("injected"):
            denials.append(_text(m.get("content")))
            if last is not None:
                drafts.append(last)
                last = None
    return drafts, denials

def doc_blocks(q: dict, repo: pathlib.Path) -> list[ParsedBlock]:
    """Every block of the question's documents, from the shipped OCR cache."""
    out = []
    for d in q["docs"]:
        pdf = repo / DOCS[d]
        cache = pdf.parent / ".traceable" / "ocr" / f"{hashlib.sha256(pdf.read_bytes()).hexdigest()}-{MODEL}-all-blocks.json"
        if not cache.exists():
            raise EvalStopped(f"no shipped OCR cache for {pdf}: read it once with traceable_read_document and commit the cache")
        for page in json.loads(cache.read_text())["pages"]:
            blocks = [{"type": b.get("type"), "text": b.get("content") or ""} for b in page.get("blocks") or []]
            out += [parse_block(b["text"], cap) for b, cap in zip(blocks, table_captions(blocks))   # as the reader does
                    if b["type"] not in ("header", "footer", "title")]
    return out

def score_record(rec: dict, q: dict, blocks: list[ParsedBlock]) -> dict:
    messages = []
    if rec.get("session") and (pathlib.Path(rec["session"]) / "messages.jsonl").exists():
        lines = (pathlib.Path(rec["session"]) / "messages.jsonl").read_text().splitlines()
        messages = [m for m in (json.loads(l) for l in lines if l.strip()) if isinstance(m, dict)]
    answer = rec.get("answer") or ""
    score = score_answer(answer, q, blocks)
    _, user_text, calcs = select_answer(messages) if messages else (None, None, [])
    check = check_answer(answer, user_text or q["question"], set(calcs), Ledger(pathlib.Path(rec["dir"])))
    statuses = [f["status"] for f in check["figures"] if f["status"] != "from_question"]
    drafts, denials = drafts_and_denials(messages)
    final = set(score["figures"])
    caught = sorted({w for d in drafts for w in wrong_figures(d, q, blocks) if w not in final})
    return {**rec, **score, "traced": bool(answer.strip()) and not check["problems"], "problems": check["problems"],
            "coverage": [sum(s in _OK for s in statuses), len(statuses)], "denials": denials, "caught": caught}

def score_all(ctx: Ctx, questions: list[dict], arms: list[str]) -> list[dict]:
    scored = []
    for q in questions:
        blocks = doc_blocks(q, ctx.repo)
        for arm in arms:
            for f in sorted((ctx.out / "work" / arm / q["id"]).glob("*/run.json"), key=lambda p: int(p.parent.name)):
                scored.append(score_record(json.loads(f.read_text()), q, blocks))
    return scored

# --- reporting ---

def _cost(costs: list[float]) -> str:
    return f"${statistics.median(costs):.2f} / ${sum(costs):.2f}" if costs else "-"

def summarise(scored: list[dict], questions: list[dict], arms: list[str]) -> str:
    out = ["# Eval results", "", f"Written {time.strftime('%Y-%m-%d %H:%M')} by eval/run_eval.py from the runs in "
           "eval/work. Raw counts: each run of each question counts once. Headline: correct and fully traced.", "",
           "| Arm | Correct and fully traced | Correct | Wrong figures in final answers | Figures traced | Denials per answer "
           "| Turn-limit hits | Empty answers | Seconds, median / worst | Cost, median / total |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for arm in arms:
        rs = [s for s in scored if s["arm"] == arm]
        if not rs:
            continue
        secs = [s["seconds"] for s in rs if s.get("seconds") is not None]
        costs = [s["cost"] for s in rs if s.get("cost") is not None]
        out.append(f"| {arm} | {sum(s['correct'] and s['traced'] for s in rs)} of {len(rs)} | "
                   f"{sum(s['correct'] for s in rs)} of {len(rs)} | {sum(len(s['wrong']) for s in rs)} | "
                   f"{sum(s['coverage'][0] for s in rs)} of {sum(s['coverage'][1] for s in rs)} | "
                   f"{sum(len(s['denials']) for s in rs) / len(rs):.2f} | {sum(s['turn_limit'] for s in rs)} | "
                   f"{sum(not s['answer'] for s in rs)} | "
                   f"{f'{statistics.median(secs):.1f} / {max(secs):.1f}' if secs else '-'} | {_cost(costs)} |")
    full = [s for s in scored if s["arm"] == "full"]
    if full:
        out += ["", "Full arm: wrong figures the check caught (in a denied draft, gone from the final answer): "
                f"{sum(len(s['caught']) for s in full)}; wrong figures that got through: {sum(len(s['wrong']) for s in full)}."]
    out += ["", "## Correct and fully traced, per question", "", "| Question | Kind | " + " | ".join(arms) + " |",
            "|---|---|" + "---|" * len(arms)]
    for q in questions:
        cells = []
        for arm in arms:
            rs = [s for s in scored if s["arm"] == arm and s["qid"] == q["id"]]
            cells.append(f"{sum(s['correct'] and s['traced'] for s in rs)}/{len(rs)}" if rs else "-")
        out.append(f"| {q['id']} | {q['kind']} | " + " | ".join(cells) + " |")
    out += ["", "## Runs that were not correct", ""]
    out += [f"- {s['arm']}/{s['qid']}/{s['run']}: missing {s['missing']}, wrong {s['wrong']}, not said {s['unsaid']}"
            + (" (turn limit)" if s["turn_limit"] else "") for s in scored if not s["correct"]] or ["- none"]
    out += ["", "## False alarms", "", "Read eval/denials.md and list here, by hand, every denial of a correct answer.", ""]
    return "\n".join(out)

def denials_md(scored: list[dict]) -> str:
    out = ["# Denials", "", "Every denial the hook sent, for a manual false-alarm review.", ""]
    for s in scored:
        for i, d in enumerate(s["denials"], 1):
            out += [f"## {s['arm']}/{s['qid']}/{s['run']}, denial {i}", "", "```", d.strip(), "```", ""]
    return "\n".join(out)

def main(argv: list[str] | None = None, runner=subprocess_runner) -> int:
    ap = argparse.ArgumentParser(description="Run and score the Receipts eval (see the module docstring).")
    ap.add_argument("--runs", type=int, default=3, help="runs per question per arm (default 3)")
    ap.add_argument("--arms", default=",".join(ARMS), help="comma-separated arms: read, calc, full")
    ap.add_argument("--only", default="", help="comma-separated question ids, for example f3,t1")
    ap.add_argument("--repo", default=str(REPO), help=argparse.SUPPRESS)
    ap.add_argument("--score-only", action="store_true", help="score the runs already in the output folder's work/")
    ap.add_argument("--suite", default="eval", help="folder (relative to the repo) with questions.yaml, answers.yaml and, "
                                                    "optionally, docs.json mapping document names to PDF paths")
    ap.add_argument("--out", default="", help="output folder (relative to the repo) for work/, results.md and "
                                              "denials.md (default: the suite folder)")
    a = ap.parse_args(argv)
    arms = [x for x in a.arms.split(",") if x]
    if not arms or any(x not in ARMS for x in arms):
        ap.error(f"--arms takes a comma-separated subset of {', '.join(ARMS)}")
    repo = pathlib.Path(a.repo).resolve()
    suite = repo / a.suite
    default_docs = dict(DOCS)
    if (suite / "docs.json").exists():                           # a suite on other documents replaces the default ones
        DOCS.clear()
        DOCS.update(json.loads((suite / "docs.json").read_text()))
    try:
        return _run(a, arms, repo, suite, Ctx(repo, repo / (a.out or a.suite), runner))
    finally:
        DOCS.clear()
        DOCS.update(default_docs)

def _run(a, arms: list[str], repo: pathlib.Path, suite: pathlib.Path, ctx: Ctx) -> int:
    ctx.out.mkdir(parents=True, exist_ok=True)
    questions = load(suite / "questions.yaml", suite / "answers.yaml")
    if a.only:
        wanted = set(a.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    if not a.score_only:
        order = sorted(arms, key=lambda x: x != "full")          # the full arm first: a harness problem stops the eval early
        for arm in order:
            setup_home(arm, ctx)
        for q in questions:
            for arm in order:
                for run in range(1, a.runs + 1):
                    rec = run_one(arm, q, run, ctx)
                    print(f"{arm}/{q['id']}/{run}: exit {rec['exit']}, {rec['seconds']} s", flush=True)
                    problem = full_arm_problem(rec) if arm == "full" else None
                    if problem:
                        raise EvalStopped(f"eval stopped: {problem}")
    scored = score_all(ctx, questions, arms)
    (ctx.out / "results.md").write_text(summarise(scored, questions, arms))
    (ctx.out / "denials.md").write_text(denials_md(scored))
    print(f"wrote {ctx.out / 'results.md'} and {ctx.out / 'denials.md'}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
