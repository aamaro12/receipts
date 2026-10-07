"""MCP server exposing read_document, calculate and calculate_many. Logs to stderr only."""
from __future__ import annotations
import os, pathlib, sys
from .ledger import Ledger
from .ocr import read_pdf, listing, MistralOcr
from .calc import (resolve_input, evaluate, format_result, pct_guard, pp_guard, scale_guard, period_guard, static_problems,
                   resolve_unit, money_dim, CalcError, FORMATS, _UNIT_MEANING)
from .numbers import parse_source, SYMBOLS

MAX_PAGES = 30
PAGES = "pages are PDF page numbers (1 = first page of the file), not the numbers printed on the pages"
_SCALE_WORDS = {3: "thousand", 6: "million", 9: "billion", 12: "trillion"}

def _root(root) -> pathlib.Path:
    return pathlib.Path(root) if root else pathlib.Path(os.getcwd())

def tool_read_document(path: str, pages: list[int] | None = None, *, client=None, root=None) -> str:
    p = pathlib.Path(path)
    if not p.is_absolute():
        return "ERROR: path must be absolute"
    if not p.exists() or p.suffix.lower() != ".pdf":
        return f"ERROR: {path} is not an existing PDF"
    if pages is not None and not all(isinstance(x, int) and not isinstance(x, bool) for x in pages):
        return "ERROR: pages must be whole page numbers, for example [1, 2]"
    want = sorted(set(pages)) if pages else None
    try:
        import pypdfium2 as pdfium
        n = len(pdfium.PdfDocument(str(p)))
        if want is None and n > MAX_PAGES:
            return f"ERROR: {n} pages; pass pages=[...] with at most {MAX_PAGES} pages ({PAGES})"
        if want is not None:
            if len(want) > MAX_PAGES:
                return f"ERROR: at most {MAX_PAGES} pages per call"
            bad = [x for x in want if not 1 <= x <= n]
            if bad:
                return f"ERROR: page {bad[0]} is out of range: {p.name} has {n} pages, numbered 1 to {n} ({PAGES})"
        client = client or MistralOcr(os.environ.get("MISTRAL_API_KEY", ""))
        doc_id, blocks = read_pdf(p, Ledger(_root(root)), client, want)
    except Exception as e:  # noqa: BLE001 - surface any failure to the model
        return f"ERROR: could not read document: {e}"
    # The page count tells the model it has the whole file (without it, a run re-read it and tried pdfinfo).
    head = f"{doc_id} = {p.name}, " + (f"pages {', '.join(map(str, want))} of {n}" if want else f"all {n} page{'s' * (n != 1)}")
    return head + "\n" + listing(blocks)

def _change_sign(expression: str, env: dict, doc_names: list[str], result) -> tuple[str, int]:
    """The sign a direction word must agree with. When every document input is negative (costs or losses shown in
    brackets), words such as "fell" describe their size, so the sign comes from the same expression on sizes."""
    sign = lambda v: (v > 0) - (v < 0)
    if doc_names and all(env[n] < 0 for n in doc_names):
        try:
            size, _ = evaluate(expression, {k: abs(v) if k in doc_names else v for k, v in env.items()})
            return "magnitude", sign(size)
        except CalcError:
            pass
    return "signed", sign(result)

def _note(inp: dict, rec: dict) -> str:
    """One input as the tool output shows it, with the row it came from, so a misnamed input is visible to the model
    ("ebitda = 2,861 from row 'Operating profit' [D1:p1:b3]")."""
    ev, name = rec["evidence"], inp.get("name")
    if inp.get("source_id") == "assumption":
        return f"{name} = {inp.get('value')} ({'constant' if ev.get('constant') else 'assumption: ' + str(inp.get('reason'))})"
    if ev.get("in_table"):
        where = f" from row '{ev['row']}'" if ev.get("row") else " from an unlabelled row"
    else:
        where = f" from '{ev['row']}'" if ev.get("row") else ""
    return f"{name} = {ev.get('matched')}{where} [{inp.get('source_id')}]"

def _amounts(cur: str | None, exp: int) -> str:
    word = _SCALE_WORDS.get(exp, "")
    return f"{SYMBOLS.get(cur, cur)} {word}".strip() if cur else (f"{word}s" if word else "plain numbers")

def _calculate(title: str, inputs: list[dict], expression: str, fmt: str, unit: str | None, ledger: Ledger) -> str:
    given = unit
    problems = [] if fmt in FORMATS else [f"format must be pct, num or money, not '{fmt}'"]
    unit, money_unit, unit_problem = resolve_unit(fmt, unit)
    problems += [unit_problem] if unit_problem else []
    problems += static_problems(inputs, expression)
    if problems:
        raise CalcError("; ".join(problems))                    # every static problem at once: one retry fixes them all
    blocks = {b["block_id"]: b for d in ledger.load()["documents"].values() for b in d["blocks"]}
    env, recorded, dims, money_specs, doc_names = {}, [], {}, set(), []
    for inp in inputs:
        sid, name = inp.get("source_id"), inp["name"]
        b = blocks.get(sid) if sid != "assumption" else None
        base, evidence = resolve_input(inp, parse_source(b) if b else None)
        env[name] = base
        recorded.append({**inp, "base": str(base), "evidence": evidence})
        if evidence.get("constant") or evidence.get("percent"):
            dims[name] = 0
        elif evidence.get("currency") and not evidence.get("unit"):
            dims[name] = 1
            money_specs.add((evidence["currency"], evidence["block_scale"]))
        else:
            dims[name] = None
        if sid != "assumption":
            doc_names.append(name)
    assumed = {i["name"]: env[i["name"]] for i in inputs if i.get("source_id") == "assumption"}
    period_guard(expression, {i["name"]: r["evidence"]["year"] for i, r in zip(inputs, recorded) if r["evidence"].get("year")},
                 assumed)
    if fmt == "pct":
        pct_guard(expression, assumed)
    else:
        scale_guard(expression, assumed)
        pp_guard(expression, {i["name"] for i, r in zip(inputs, recorded) if r["evidence"].get("percent")})
    result, used_float = evaluate(expression, env)
    dim = money_dim(expression, dims)
    common = next(iter(money_specs)) if len(money_specs) == 1 else (None, None)
    # Units: a result in its inputs' unit keeps it (EPS 103.6p - 94.1p = 9.50p; Diageo 173.2c - 196.3c = -23.1c, never
    # -$23.1m), and a unit the inputs contradict is refused.
    unit_dims, in_units = {}, set()
    for i, r in zip(inputs, recorded):
        ev = r["evidence"]
        if ev.get("constant") or ev.get("percent"):
            unit_dims[i["name"]] = 0
        elif ev.get("unit"):
            unit_dims[i["name"]] = 1
            in_units.add(ev["unit"])
        else:
            unit_dims[i["name"]] = None
    in_unit = next(iter(in_units)) if fmt != "pct" and len(in_units) == 1 and money_dim(expression, unit_dims) == 1 else None
    if fmt != "pct" and unit is not None:
        if in_unit and unit != in_unit:
            raise CalcError(f"unit '{unit}' ({_UNIT_MEANING.get(unit, unit)}) does not match the inputs, which are in "
                            f"{in_unit} ({_UNIT_MEANING.get(in_unit, in_unit)}): leave unit out")
        if dim == 1 and common[0]:
            raise CalcError(f"unit '{unit}' ({_UNIT_MEANING.get(unit, unit)}) does not match the inputs, which are amounts "
                            f"in {_amounts(*common)}: leave unit out")
    if money_unit is not None and in_unit:
        raise CalcError(f"unit '{given}' is a currency or scale, but the inputs are in {in_unit} "
                        f"({_UNIT_MEANING.get(in_unit, in_unit)}): leave unit out")
    unit = unit or in_unit
    money = None
    if money_unit is not None:
        if dim in (0, 2):
            raise CalcError(f"unit '{given}' is a currency or scale, but {expression} is not an amount; leave unit out")
        cur, exp = money_unit
        money = (cur or common[0], exp if exp is not None else (common[1] or 0))
    elif fmt == "num" and unit is None and dim == 1 and common[0]:
        money = common                                          # an amount keeps its inputs' currency and scale
    display, is_pct = format_result(result, fmt, unit, money)
    basis, change_sign = _change_sign(expression, env, doc_names, result)
    calc_id = ledger.add_calculation({"title": title, "inputs": recorded, "expression": expression,
                                      "result": str(result), "display": display, "is_percent": is_pct,
                                      "unit": unit, "currency": money[0] if money else None,
                                      "scale": money[1] if money else 0, "used_float": used_float,
                                      "change_sign": change_sign, "sign_basis": basis,
                                      "has_assumption": any(i.get("source_id") == "assumption" and not r["evidence"]["constant"]
                                                            for i, r in zip(inputs, recorded))})
    notes = "; ".join(_note(i, r) for i, r in zip(inputs, recorded))
    return f"{calc_id} = {display}  (write it as: {display} [{calc_id}])  inputs: {notes}"

def tool_calculate(title: str, inputs: list[dict], expression: str, format: str = "num", unit: str | None = None,
                   *, root=None) -> str:
    try:
        return _calculate(title, inputs, expression, format, unit, Ledger(_root(root)))
    except CalcError as e:
        return f"ERROR: {e}"
    except Exception as e:  # noqa: BLE001 - one bad calculation must never lose a calculate_many batch
        return f"ERROR: calculation failed ({type(e).__name__}: {e})"

def tool_calculate_many(calculations: list[dict], *, root=None) -> str:
    if not isinstance(calculations, list):
        return "ERROR: calculations must be a list"
    out = []
    for c in calculations:
        if not isinstance(c, dict):
            out.append("ERROR: each calculation must be an object with title, inputs, expression and format")
            continue
        out.append(tool_calculate(c.get("title", ""), c.get("inputs", []), c.get("expression", ""),
                                  c.get("format", "num"), c.get("unit"), root=root))
    return "\n".join(out)

def build():
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("traceable")

    @mcp.tool(structured_output=False)
    def read_document(path: str, pages: list[int] | None = None) -> str:
        """Read a PDF (absolute path). pages: optional page numbers to read, as in pN of block IDs; pages are PDF page numbers (1 = first page of the file), not the numbers printed on the pages. Returns text blocks prefixed with IDs like [D1:p3:b7]. Cite these IDs. Never read PDFs or .traceable files with other tools; use traceable_read_document."""
        return tool_read_document(path, pages)

    @mcp.tool(structured_output=False)
    def ask_document(path: str, question: str) -> str:
        """Ask a question about a PDF the user gave you (absolute path). A Mistral model reads the whole file and answers with citations; every citation is a quote verified on its page before it gets an ID like [D1:p3:c1] (a quote found nowhere gets none). Cite those IDs right after each figure. Use it for long documents; use traceable_read_document to read specific pages block by block."""
        from .sources import ask_document as ask
        return ask(path, question)

    @mcp.tool(structured_output=False)
    def web_search(query: str) -> str:
        """Search the web. Each source page is fetched and the passages with figures are stored as citable blocks [W1], [W2]...; the search engine's own answer is not citable. Cite [Wn] right after each figure it supports; a figure must appear in that passage."""
        from .sources import web_search as search
        return search(query)

    @mcp.tool(structured_output=False)
    def library_search(query: str, library_id: str) -> str:
        """Search a Mistral Library (library_id). Passages of the matched documents are stored as citable blocks [L1], [L2]... with the document name and a best-effort page (Libraries text has no page markers). Cite [Ln] right after each figure."""
        from .sources import library_search as search
        return search(query, library_id)

    if os.environ.get("TRACEABLE_TOOLS", "all") != "read":
        @mcp.tool(structured_output=False)
        def calculate(title: str, inputs: list[dict], expression: str, format: str = "num", unit: str | None = None) -> str:
            """Compute a derived or change figure. inputs: [{name, value (exactly as written in the block, e.g. 2,861 or (3,300) or 103.6p or 24.0%), scale (k|m|bn, optional), source_id (block ID, or 'assumption' with a reason)}]. expression uses input names with + - * / ** min max abs round; the only numbers allowed in it are the integers 0 to 12 and 0.5. format: pct|num|money. For a percentage give the ratio (op / rev) with format pct and never multiply by 100. An amount keeps the currency and scale of its inputs (e.g. -£19m). unit (optional): x (multiple), p (pence), kg, t, TEU, m3, pcs. Returns 'C<n> = value'; cite [C<n>]."""
            return tool_calculate(title, inputs, expression, format, unit)

        @mcp.tool(structured_output=False)
        def calculate_many(calculations: list[dict]) -> str:
            """Compute several derived or change figures in ONE call. calculations: [{title, inputs, expression, format, unit}] with the same fields as calculate. Returns one 'C<n> = value' or 'ERROR: ...' line per calculation."""
            return tool_calculate_many(calculations)
    return mcp

if __name__ == "__main__":
    print("traceable server starting", file=sys.stderr)
    build().run()
