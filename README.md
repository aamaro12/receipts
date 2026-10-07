# Receipts

A prototype for the Mistral Vibe CLI that enforces one rule on answers about documents:

> **Every number is either in a highlighted box on the source page, or a calculation whose inputs are.**

A citation says where the model looked, not that the figure is there. A computed figure, such as a margin or a growth rate, has no machine-readable link to its inputs at all. For most knowledge work, users need to verify the exact figure, period and unit before relying on an answer. Receipts turns that into a check.

Personal prototype, not a Mistral product. The filings and annual-report pages are public; the logistics documents are synthetic.

## How it works

1. **Read.** `traceable_read_document` reads a PDF with Mistral OCR and returns blocks with IDs such as `[D1:p1:b3]` (document, page, block) and their position on the page. `traceable_ask_document`, `traceable_web_search` and `traceable_library_search` store citable passages too; every quote is verified against its page before it gets an ID.
2. **Calculate.** `traceable_calculate_many` computes derived figures from inputs that must be in the cited blocks with the same value, scale, unit, currency and sign. Each result, such as `[C1]`, records its formula and inputs.
3. **Check.** A `post_agent` hook checks the final answer. Any figure that is not in its cited block or calculation goes back to the model with the reason ("is in D1:p1:b3 but not in its 2024 column"), and Vibe retries. Each check writes `report.html`: click a figure to see its page with the block highlighted, or its formula and inputs.

| Path | What it is |
|---|---|
| `server/traceable/` | MCP server: `ocr`, `sources` (uploads, web, Libraries), `numbers` (figure parsing and scale), `calc`, `check`, `report` |
| `hooks/check_numbers.py` | The `post_agent` hook |
| `skill/traceable-numbers/` | Vibe skill with the citation rules |
| `receipts` | Starts Vibe with the legacy harness, then prints the check's verdict |
| `demo/` | Documents (with their OCR cache), questions and screenshots of runs |
| `eval/` | Eval runner, questions, hand-written answer keys and results |

## Results

All 39 questions, 7 October 2026: 16 on Affirm and NVIDIA filings, 8 held out, and 15 on annual reports and shipping
documents, 4 of them traps. Mistral Medium 3.5, 3 runs per question per arm. Both arms use the same tools, calculator
and rules; the only difference is the final-answer check. Correctness is after manual review of every answer the
scorer flagged, and "traced" is judged by the prototype's own check. Details: `eval/results-2026-10-07.md`.

| | No check | With the check |
|---|---|---|
| Correct and every figure traced | 64 of 117 (55%) | 111 of 117 (95%) |
| Correct | 113 of 117 (97%) | 116 of 117 (99%) |
| Runs with a wrong figure in the final answer | 2 | 1 |
| Runs that ended without an answer | 2 | 0 |
| Median time per answer (90th percentile) | 12.3 s (22.1 s) | 13.6 s (29.6 s) |
| Median cost per answer | $0.051 | $0.064 |

The one wrong figure with the check (earnings per share in cents written as $ million) exposed a bug in how quotes kept
units; it is fixed and tested, and a targeted re-run of that question (not the full suite) gave the right figures
three times out of three. Earlier runs on smaller sets are in `eval/results.md` (24 Sep) and `eval/suites/results.md`
(5 Oct). The sets are small: they show the direction, not a benchmark.

## Run it

Needs Vibe CLI 2.25 or later, [uv](https://docs.astral.sh/uv/) and a `MISTRAL_API_KEY` (or a Vibe login on macOS).

The demo PDFs ship with their OCR results, so the demos make no OCR calls; with the check, an answer costs about $0.06
in model calls.

```bash
./install.sh                         # MCP server, skill and hooks for the demo folders
cd demo/finance && ../../receipts    # trust the folder once, then ask the questions in demo/questions.md
cd server && uv run pytest           # 288 tests, no API calls
uv run --project server python eval/run_eval.py --suite eval/suites/heldout --runs 1
```

`install.sh` registers the MCP server and the skill in `~/.vibe` (the original `config.toml` is kept as `config.toml.bak`)
and writes the check's hook into each demo folder.

## Limits

- **Hooks need the legacy harness.** Vibe's unified harness gives hooks no transcript, so `receipts` always starts the legacy one and prints NOT CHECKED rather than passing silently.
- **Figures are found by patterns, not a model.** English number formats only; some correct phrasings are still denied, such as a citation placed before its figure.
- **The check verifies inputs and arithmetic, not the choice of formula.** A misnamed input is shown in the report, not refused.
- **Highlights are per OCR block.** A figure in a table highlights the table and bolds the matched cell.
- **Excel is not included.** A private version also reads workbooks cell by cell, with each cell's row, column and
  formula.
- **Retries cost turns.** Denials add retries and latency; on the 5 October filings set, 5 of 72 checked runs hit the 16-turn cap (none on 7 October).

## What this suggests for the platform

1. Typed figure spans `{value, unit, cite}` and input references on tool results. `ToolExecutionEntry.info` already allows extra properties, so computed figures could carry their inputs without breaking the API.
2. A per-figure verify step, as a guardrail or a Studio judge, with a policy to retry, flag or block.
3. A metric in Studio observability: the share of figures traced per agent or workflow, and wrong figures caught before the user sees them.
