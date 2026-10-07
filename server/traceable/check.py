"""Hook decision logic. Standard library only."""
from __future__ import annotations
import bisect, hashlib, json, pathlib, re, traceback
from dataclasses import dataclass, field
from decimal import Decimal
from .numbers import (parse_source, extract, normalise, parse_block, matches_in_block, close_to_display, magnitudes, may_match,
                      row_cells, is_separator_row, currency_code, explain_miss, CITATION, DERIVED_WORDS, UNIT_NAMES,
                      SYMBOLS, Figure, ParsedBlock)
from .ledger import Ledger

_IDS = re.compile(r"D\d+:p\d+:[bc]\d+|D\d+:s\d+:[A-Z]{1,3}\d+|[CWL]\d+")
_CITE_RUN = re.compile(rf"(?:{CITATION.pattern})(?:[ \t]*(?:{CITATION.pattern}))*")    # [C1][C2] is one scope
_MOVE = re.compile(rf"([.;!?])[ \t]*({_CITE_RUN.pattern})")                            # "£9,434m. [D1:p1:b3]"
_CALC_LINE = re.compile(r"\b(C\d+) = [^\n]*\(write it as:")
_ABBREV = re.compile(r"(?:\b(?:vs|c|ca|e\.g|i\.e|approx|incl|no|p|pp)|^\s*\d+)$", re.I)
_YEAR = re.compile(r"(?<![\d.,/–-])(?:FY\s?)?((?:19|20)\d{2})(?!\d|[.,]\d|\s?[-–/]\s?\d)", re.I)
# "from 2023 to 2024", "between 2023 and 2024", "2022-2024": a period, so neither year pins a figure to a column
# (seen in a run: "grew 2.98% [C5] from 2023 to 2024, from £9,161m" gave 2024 to £9,161m and denied a correct answer).
_PERIOD = re.compile(r"(?:\b(?:from|between)\s+)?(?:FY\s?)?(?:19|20)\d{2}(?:\s*[-–/]\s*|\s+(?:to|until|through|and|vs\.?|versus)\s+)"
                     r"(?:FY\s?)?(?:19|20)\d{2}\b", re.I)
MAX_ATTEMPTS = 4          # the first answer plus Vibe's 3 retries
HINTS = 5                 # "it appears in ..." is computed for the first 5 failing figures only
_REWRITE = ("Rewrite the full answer. Put a citation right after every figure: [Dd:pP:bB], [Dd:pP:cN], [Wn], [Ln] or [Cn] (square brackets); "
            "compute derived and change figures with traceable_calculate. Write it for the user: do not mention this check, "
            "its denials or your retries.")
_NO_DOCS = ("No source documents were read in this folder: read them with traceable_read_document, "
            "or say the figures are not from documents.")
_PRIORITY = {"phantom": 1, "mismatch": 2, "derived_needs_calc": 3}
_OK = ("traced", "calculated", "calculated_with_assumption")
_CHANGE_WHY = "is a change: a change must be computed with traceable_calculate and cited with its [Cn]"
_DERIVED_WHY = "is a derived figure: compute it with traceable_calculate and cite its [Cn]"
_COMPUTED_HINT = "; if you computed it, use traceable_calculate and cite its [Cn]"
NOT_CHECKED_HARNESS = "this Vibe harness does not pass the transcript to hooks; run vibe --legacy-harness"
_NOT_CHECKED_FILE = "no transcript file (is Vibe session logging off?)"
_NOT_CHECKED_NO_QUESTION = "no question found in the transcript (for example after compaction)"
_NOT_CHECKED_NO_ANSWER = "no answer in this turn (for example a turn limit or a cancelled tool)"
_NOT_CHECKED_INPUT = "the hook input was not valid JSON"
_NOT_CHECKED_TRANSCRIPT = "the transcript could not be read"

@dataclass
class Segment:
    text: str                                   # one citation scope: a clause piece or a table cell, no citations
    cites: list[str]
    is_row: bool = False
    header: str = ""                            # table cell: its own column header
    figures: list[Figure] = field(default_factory=list)
    years: list[int | None] = field(default_factory=list)

def _text(content) -> str:
    """Message content as text: a string, or the text parts of a list."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return content.get("text") if isinstance(content.get("text"), str) else ""
    if isinstance(content, list):
        return "\n".join(t for t in (_text(p) for p in content if isinstance(p, (str, dict))) if t)
    return str(content)

def m_injected(m: dict) -> bool:
    return bool(m.get("injected"))

def _last_real_user(messages: list[dict]) -> int | None:
    real = [i for i, m in enumerate(messages) if m.get("role") == "user" and not m_injected(m)]
    return real[-1] if real else None

def select_answer(messages: list[dict]):
    users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    last_real = _last_real_user(messages)
    if not users or last_real is None:
        return None, None, []
    answers = [_text(m.get("content")) for m in messages[users[-1] + 1:] if m.get("role") == "assistant"]
    answers = [a for a in answers if a.strip()]
    answer = answers[-1] if answers else None
    if answer is not None and not extract(CITATION.sub(" ", answer)):
        joined = "\n".join(answers)
        answer = joined if extract(CITATION.sub(" ", joined)) else answer
    calcs = []
    for m in messages[last_real + 1:]:
        if m.get("role") == "tool":
            calcs += _CALC_LINE.findall(_text(m.get("content")))
    return answer, _text(messages[last_real].get("content")), calcs

def prepare(answer: str) -> str:
    """Normalise the answer and move a citation that follows sentence punctuation in front of it."""
    return _MOVE.sub(lambda m: " " + m.group(2) + m.group(1), normalise(answer))

def _clauses(line: str) -> list[str]:
    out, start = [], 0
    cites = [m.span() for m in CITATION.finditer(line)]
    for m in re.finditer(r"[.;!?]\s+", line):
        if any(s < m.start() < e for s, e in cites):
            continue                            # the ";" inside [C1; C2]
        if line[m.start()] == "." and _ABBREV.search(line[start:m.start()]):
            continue                            # vs., c., e.g., i.e., approx., no., a leading "13."
        out.append(line[start:m.end()])
        start = m.end()
    out.append(line[start:])
    return [c for c in out if c.strip()]

def _year_hints(clause: str, spans: list[tuple[int, int]]) -> list[int | None]:
    """Give each year in the clause to the nearest figure within 15 characters ("2023: X" goes to X)."""
    hints: list[set[int]] = [set() for _ in spans]
    starts = [s for s, _ in spans]
    periods = [m.span() for m in _PERIOD.finditer(clause)]
    for m in _YEAR.finditer(clause):
        ys, ye = m.span()
        if any(s < ye and ys < e for s, e in spans) or any(s <= ys and ye <= e for s, e in periods):
            continue
        k = bisect.bisect_left(starts, ye)
        before = k - 1 if k > 0 and spans[k - 1][1] <= ys and ys - spans[k - 1][1] <= 15 else None
        after = k if k < len(spans) and spans[k][0] - ye <= 15 else None
        if after is not None and (before is None or clause[ye:ye + 1] == ":"
                                  or spans[after][0] - ye < ys - spans[before][1]):
            hints[after].add(int(m.group(1)))
        elif before is not None:
            hints[before].add(int(m.group(1)))
    return [h.pop() if len(h) == 1 else None for h in hints]

_COMPARE = re.compile(r"\b(?:from|compared|versus|vs\.?|against|prior|previous|preceding|before|earlier|last year|a year ago)\b", re.I)

def _clause_year(clause: str) -> int | None:
    """The one year a clause is about, for figures with no year of their own; none when it compares periods. A year
    in brackets ("(2023: £9,161m)") belongs to its own figure only."""
    return None if _COMPARE.search(clause) else _single_year(re.sub(r"\([^)]*\)", " ", clause))

def _single_year(text: str) -> int | None:
    periods = [m.span() for m in _PERIOD.finditer(text)]
    years = {int(m.group(1)) for m in _YEAR.finditer(text) if not any(s <= m.start() and m.end() <= e for s, e in periods)}
    return years.pop() if len(years) == 1 else None

def _prose_segments(line: str) -> list[Segment]:
    out = []
    for clause in _clauses(line):
        pieces, pos = [], 0
        for m in _CITE_RUN.finditer(clause):
            pieces.append((clause[pos:m.start()], _IDS.findall(m.group())))
            pos = m.end()
        pieces.append((clause[pos:], []))
        joined, offsets = "", []
        for text, _ in pieces:                  # citations count as one space when measuring distances
            offsets.append(len(joined))
            joined += text + " "
        figs = [extract(text, normalised=True) for text, _ in pieces]
        whole = _clause_year(joined)
        hints = iter([y or whole for y in
                      _year_hints(joined, [(offsets[k] + f.start, offsets[k] + f.end) for k, fs in enumerate(figs) for f in fs])])
        for (text, ids), fs in zip(pieces, figs):
            if fs:
                out.append(Segment(text, ids, figures=fs, years=[next(hints) for _ in fs]))
            elif ids:
                out.append(Segment(text, ids))                  # a citation with no figure in its scope
    return out

def _table_segments(rows: list[str]) -> list[Segment]:
    """One segment per cell; citations anywhere in the row cover the row; header rows are checked too."""
    cells = [row_cells(r) for r in rows]
    seps = [is_separator_row(r) for r in rows]
    header = 0 if len(rows) > 1 and seps[1] else None
    heads = [_CITE_RUN.sub(" ", c).strip().lower() for c in cells[header]] if header is not None else []
    out = []
    for i, (row, cs) in enumerate(zip(rows, cells)):
        if seps[i]:
            continue
        ids = [x for m in _CITE_RUN.finditer(row) for x in _IDS.findall(m.group())]
        row_year = _single_year(cs[0]) if cs and i != header else None
        before = len(out)
        for col, cell in enumerate(cs):
            text = _CITE_RUN.sub(" ", cell)
            figs = extract(text, normalised=True)
            if not figs:
                continue
            hdr = heads[col] if i != header and col < len(heads) else ""
            fallback = _single_year(hdr) or (row_year if col else None)
            years = [y or fallback for y in _year_hints(text, [(f.start, f.end) for f in figs])]
            out.append(Segment(text, ids, True, hdr, figs, years))
        if ids and len(out) == before:
            out.append(Segment(row, ids, True))                 # a row with citations but no figure
    return out

def _segments(text: str) -> list[Segment]:
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            out += _table_segments(lines[i:j])
            i = j
        else:
            out += _prose_segments(lines[i])
            i += 1
    return out

def _derived(text: str, fig: Figure, header: str) -> bool:
    words = re.findall(r"[a-z]+", text[max(0, fig.start - 50):fig.start].lower())[-4:]
    return any(w in DERIVED_WORDS for w in words) or any(w in DERIVED_WORDS for w in re.findall(r"[a-z]+", header))

def _sign(fig: Figure) -> int:
    """-1 or +1 when the figure states a sign: a minus or parentheses, or a direction word on a change figure."""
    return -1 if fig.neg or fig.direction < 0 else 1 if fig.direction > 0 else 0

def _sign_ok(fig: Figure, other: int) -> bool:
    return _sign(fig) == 0 or other == 0 or _sign(fig) == other

def _qkey(f: Figure) -> tuple:
    return f.value, f.scale, f.is_percent, f.unit

def _check_calc(fig: Figure, cid: str, c: dict | None) -> tuple[str, str | None, dict]:
    if c is None:
        return "phantom", None, {}
    target = Decimal(c["result"]) * (100 if c.get("is_percent") else 1)
    shown = f"{cid} = {c['display']}"
    sign = c.get("change_sign")
    sign = (target > 0) - (target < 0) if sign is None else sign
    exp = int(c.get("scale") or 0)
    bare = not (fig.scale or fig.currency or fig.unit or fig.is_percent)   # "-19" for -£19m: compared at the shown scale
    fcur, ccur = currency_code(fig.currency), c.get("currency")
    if fig.is_percent != bool(c.get("is_percent")):
        if fig.is_percent:
            why = (f"is a percentage but {shown} is not one: compute the ratio with format 'pct' (do not multiply by 100) "
                   "and cite that [Cn]")
        else:
            why = f"is not a percentage but {shown} is one: write it as {c['display']}"
    elif fig.unit and not fig.is_percent and fig.unit != c.get("unit"):
        have = f"is in {UNIT_NAMES.get(fig.unit, fig.unit)}"
        why = (f"{have} but {shown} is in {UNIT_NAMES.get(c['unit'], c['unit'])}: write it as {c['display']}" if c.get("unit")
               else f"{have} but {shown} has no unit: write it as {c['display']}, or compute it with unit '{fig.unit}'")
    elif c.get("unit") and not fig.is_percent and not fig.unit and (fig.currency or fig.scale):
        have = f"an amount in {SYMBOLS.get(fcur, fcur)}" if fcur else "an amount"
        why = f"is {have} but {shown} is in {UNIT_NAMES.get(c['unit'], c['unit'])}: write it as {c['display']}"
    elif fcur and ccur and fcur != ccur:
        why = f"has currency {fig.currency.strip()} but {shown} is in {SYMBOLS.get(ccur, ccur)}"
    elif not _sign_ok(fig, sign):
        why = f"has the opposite sign or direction to {shown}"
        if c.get("sign_basis") == "magnitude":
            why += f" (its inputs are negative amounts such as costs, so it means they {'fell' if sign < 0 else 'rose'})"
    elif not (close_to_display(fig, target) or (bare and exp and close_to_display(fig, target / (Decimal(10) ** exp)))):
        why = f"does not match {shown}"
    else:
        return ("calculated_with_assumption" if c.get("has_assumption") else "calculated"), None, {"matched": c["display"]}
    return "mismatch", why, {}

def _needs_calc(fig: Figure, seg: Segment) -> bool:
    """An explicit change ("increased by £19m", "a reduction of 19 GBPm") must cite a [Cn], even when the cited block
    happens to contain the number (seen in a run: "increased by £19m" matched an unrelated 19). A percentage next to a
    derived word is handled in _check_block: allowed only when the block states that exact figure (reported KPIs such
    as "adjusted operating margin 34.1%" cannot be recomputed from the statements, so they must stay citable)."""
    return fig.change and fig.explicit_change

def _check_block(fig: Figure, cid: str, pb: ParsedBlock | None, year: int | None, seg: Segment):
    if pb is None:
        return "phantom", None, {}
    if _needs_calc(fig, seg):
        return "derived_needs_calc", _CHANGE_WHY, {}
    hits = matches_in_block(fig, pb, year)
    signed = [h for h in hits if _sign_ok(fig, -1 if h.neg or h.direction < 0 else 1)]
    if not signed:
        if not hits and _derived(seg.text, fig, seg.header):
            return "derived_needs_calc", _DERIVED_WHY, {}
        if hits:
            return "mismatch", f"has the opposite sign or direction to {cid} ('{hits[0].raw.strip()}')", {}
        if year is not None and matches_in_block(fig, pb):
            return "mismatch", f"is in {cid} but not in its {year} column", {}
        return "mismatch", explain_miss(fig, pb, cid, year) or f"is not in {cid}", {}   # names a unit, currency or scale
    exact = [h for h in signed if h.value == fig.value and h.is_percent == fig.is_percent]
    if not exact and _derived(seg.text, fig, seg.header):
        return "derived_needs_calc", _DERIVED_WHY, {}
    hit = (exact or signed)[0]
    return "traced", None, {"matched": hit.raw.strip(), "span": [hit.start, hit.end]}

def check_answer(answer: str, user_text: str, valid_calcs: set[str], ledger: Ledger, *,
                 data: dict | None = None, cache: dict | None = None) -> dict:
    data = ledger.load() if data is None else data
    blocks = {b["block_id"]: b for d in data["documents"].values() for b in d["blocks"]}
    calcs = {c["calc_id"]: c for c in data["calculations"]}
    cache = {} if cache is None else cache              # parsed blocks and magnitudes, keyed by block text

    def parsed(block_id: str) -> ParsedBlock | None:
        b = blocks.get(block_id)
        if b is None:
            return None
        key = ("parsed", b["text"], b.get("caption") or "")
        if key not in cache:
            cache[key] = parse_source(b)
        return cache[key]

    def where(fig: Figure) -> str | None:
        hits = []
        for bid, b in blocks.items():
            key = ("mags", b["text"])
            if key not in cache:
                cache[key] = magnitudes(b["text"])
            if may_match(fig, cache[key]) and matches_in_block(fig, parsed(bid)):
                hits.append(bid)
                if len(hits) == 3:
                    break
        return ", ".join(hits) or None

    def example(ids: list[str]) -> str:
        for i in ids:
            if i in calcs and i in valid_calcs:
                return f", for example {calcs[i]['display']} [{i}]"
            pb = parsed(i) if not i.startswith("C") else None
            if pb and pb.candidates:
                return f", for example {pb.candidates[0].raw.strip()} [{i}]"
        return ""

    asked = {_qkey(f) for f in extract(user_text or "")}
    text = prepare(answer)
    figures, problems, empty, hints = [], [], [], 0
    for seg in _segments(text):
        if not seg.figures:
            empty.append(seg.cites)
            problems.append(f"citation {''.join(f'[{i}]' for i in seg.cites)} has no figure next to it. "
                            f"Put the figure next to its citation{example(seg.cites)}")
            continue
        for fig, year in zip(seg.figures, seg.years):
            rec = {"raw": fig.raw.strip(), "cite": None, "status": "untraced", "matched": None}
            figures.append(rec)
            if fig.unparsed:
                rec["status"] = "unparsed"
                problems.append(f"unparsed figure '{rec['raw']}': write it with a standard unit such as k, m, bn, %, x, p")
                continue
            if not seg.cites:
                if _qkey(fig) in asked:
                    rec["status"] = "from_question"
                else:
                    problems.append(f"'{rec['raw']}' has no citation")
                continue
            fail = None                                  # (status, cite, why) of the most useful failure
            for cid in seg.cites:
                if cid.startswith("C"):
                    status, why, extra = _check_calc(fig, cid, calcs.get(cid) if cid in valid_calcs else None)
                else:
                    status, why, extra = _check_block(fig, cid, parsed(cid), year, seg)
                if status in ("traced", "calculated", "calculated_with_assumption"):
                    rec.update(status=status, cite=cid, **extra)
                    break
                if fail is None or _PRIORITY[status] > _PRIORITY[fail[0]]:
                    fail = (status, cid, why)
            else:
                status, cid, why = fail
                rec.update(status=status, cite=cid)
                if status == "phantom":
                    problems.append(f"'{rec['raw']}' cites {cid}, which does not exist in this turn")
                else:
                    msg = f"'{rec['raw']}' {why}"
                    if why.startswith("is not in"):
                        if hints < HINTS:
                            hints += 1
                            found = where(fig)
                            if found:
                                msg += f"; it appears in {found}"
                        if fig.is_percent or fig.change or _derived(seg.text, fig, seg.header):
                            msg += _COMPUTED_HINT
                    problems.append(msg)
    return {"figures": figures, "problems": problems, "text": text, "empty_citations": empty}

def _question_id(hook_input: dict, messages: list[dict], user_text: str | None) -> str:
    """The same question across Vibe's retries; a new real user message starts a new count."""
    key = f"{hook_input.get('session_id') or ''}\n{_last_real_user(messages)}\n{user_text or ''}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]

def _allow_message(figures: list[dict], report: str) -> str:
    asked = sum(1 for f in figures if f["status"] == "from_question")
    assumed = sorted({f["cite"] for f in figures if f["status"] == "calculated_with_assumption"})
    parts = [f"traceable: {len(figures) - asked} figures traced"]
    if asked:
        parts.append(f"{asked} from your question, not verified")
    if assumed:
        parts.append(f"{', '.join(assumed)} {'uses an assumed input' if len(assumed) == 1 else 'use assumed inputs'}")
    return " · ".join(parts + [f"report: {report}"])

def _not_checked(hook_input: dict, reason: str) -> dict:
    """Allow, but say so, record it and rewrite the report: a report must never show an older answer as checked. When
    the ledger itself cannot be read, the report is rewritten from the NOT CHECKED record alone."""
    message = f"traceable: NOT CHECKED: {reason}"
    try:
        ledger = Ledger(pathlib.Path(hook_input.get("cwd") or "."))
        if ledger.exists():
            from .report import write_report
            record = {"decision": "not_checked", "reason": reason, "answer": "", "figures": [], "problems": [],
                      "text": "", "empty_citations": []}
            try:
                ledger.add_check(record)
                path = write_report(ledger)
            except Exception:  # noqa: BLE001 - an unreadable ledger: replace the old report all the same
                path = write_report(ledger, data={"documents": {}, "calculations": [], "checks": [record]})
            message += f" · report: {path}"
    except Exception:  # noqa: BLE001 - never block the answer
        pass
    return {"decision": "allow", "system_message": message}

def run_hook(raw: str, cwd: str) -> dict:
    """The hook script's entry point: stdin as text and the hook's own folder (Vibe runs hooks in the project folder).
    Input that is not a JSON object is NOT CHECKED, with the report replaced, never left standing."""
    try:
        hook_input = json.loads(raw)
    except ValueError:
        hook_input = None
    if not isinstance(hook_input, dict):
        return _not_checked({"cwd": cwd}, _NOT_CHECKED_INPUT)
    if not hook_input.get("cwd"):
        hook_input["cwd"] = cwd
    return run(hook_input)

def run(hook_input: dict) -> dict:
    try:
        if "transcript_path" not in hook_input:
            return _not_checked(hook_input, NOT_CHECKED_HARNESS)  # the unified harness sends only cwd and hook_event_name
        tp = hook_input.get("transcript_path") or ""
        if not tp or not pathlib.Path(tp).exists():
            return _not_checked(hook_input, _NOT_CHECKED_FILE)
        try:
            msgs = [json.loads(l) for l in pathlib.Path(tp).read_text().splitlines() if l.strip()]
        except (OSError, ValueError):
            return _not_checked(hook_input, _NOT_CHECKED_TRANSCRIPT)
        msgs = [m for m in msgs if isinstance(m, dict)]
        answer, user_text, calcs = select_answer(msgs)
        if answer is None:
            return _not_checked(hook_input, _NOT_CHECKED_NO_QUESTION if user_text is None else _NOT_CHECKED_NO_ANSWER)
        ledger = Ledger(pathlib.Path(hook_input.get("cwd") or "."))
        try:
            data = ledger.load()
        except (OSError, ValueError) as e:
            return _not_checked(hook_input, f"the ledger {ledger.path} could not be read ({type(e).__name__})")
        result = check_answer(answer, user_text, set(calcs), ledger, data=data)
        figures, problems, empty = result["figures"], result["problems"], result["empty_citations"]
        if not figures and not empty and not ledger.exists():
            return {"decision": "allow"}
        question = _question_id(hook_input, msgs, user_text)
        attempt = 1 + sum(1 for c in data["checks"] if c.get("question") == question)
        decision = "deny" if problems else "allow"
        ledger.add_check({"decision": decision, "answer": answer, **result, "question": question, "attempt": attempt,
                          "question_text": (user_text or "")[:600]})
        from .report import write_report
        try:
            report = write_report(ledger)
        except Exception as e:  # noqa: BLE001 - the report is a view: its failure must never change the verdict
            report = f"(the report could not be written: {type(e).__name__}: {e})"
        if decision == "allow":
            if figures:
                return {"decision": "allow", "system_message": _allow_message(figures, report)}
            if any(c.get("question") == question and c.get("decision") == "deny" for c in data["checks"]):
                return {"decision": "allow", "system_message": "traceable: the final answer states no figures; an earlier "
                        f"draft for this question was denied and its figures are not verified. Report: {report}"}
            return {"decision": "allow"}
        if not data["documents"]:
            body = _NO_DOCS
        else:
            head = (f"Untraced figures ({sum(1 for f in figures if f['status'] not in _OK + ('from_question',))} of "
                    f"{len(figures)})" if figures else "Citations without a figure")
            if figures and empty:
                head += f", citations without a figure: {len(empty)}"
            lines = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(problems))
            body = f"{head}:\n{lines}\n{_REWRITE}"
        return {"decision": "deny", "reason": f"Attempt {attempt} of {MAX_ATTEMPTS}. {body}\nReport: {report}"}
    except Exception:  # noqa: BLE001 - fail open, but never silently
        out = _not_checked(hook_input, "check error")
        out["system_message"] += "\n" + traceback.format_exc(limit=2)
        return out
