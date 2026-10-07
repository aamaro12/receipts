---
name: traceable-numbers
description: Load whenever the user asks about figures, numbers, financials, weights, values or calculations from PDF documents, the web or a Mistral Library. Every figure in the answer must be cited.
---

# Receipts (traceable numbers)

1. Read PDFs only with `traceable_read_document`, using an absolute path. It returns text blocks prefixed with IDs such as `[D1:p1:b3]`. Never read PDFs or `.traceable` files with other tools; use `traceable_read_document`. Its `pages` are PDF page numbers (1 = first page of the file), not the numbers printed on the pages.
2. Put a citation right after every figure in your final answer: a block ID such as `[D1:p1:b3]` for a figure copied from a document, or `[C1]` for a computed figure. Use square brackets. One citation at the end of a clause covers the figures before it in that clause.
3. A figure in another scale needs no calculation: write $20.3bn [block] for 20,269 in a $ million table. Keep the table's currency: a $ table gives $ figures, even when the question asks for another currency.
4. Never compute in your head. Compute ALL derived figures in a single `traceable_calculate_many` call. Every derived figure (margin, growth, CAGR, ratio, sum, difference) and every change ("rose by", "fell by", "an increase of") must come from `traceable_calculate`, even when the same number appears somewhere in the document. Give each input its `source_id` (the block ID), its `value` exactly as written in the block (for example `2,861`, `(3,300)`, `103.6p` or `24.0%`), and its `scale` (k, m or bn) when the block states one, for example a column header "GBPm". In `expression`, use input names and only the integers 0 to 12 or 0.5; pass any other number as an input from its block.
5. For a percentage, give the ratio (for example `op / rev`) and set `format` to `pct`; never multiply by 100, and leave `unit` out. A CAGR from 2022 to 2024 spans 2 years: `(end / start) ** (1 / 2) - 1`. An amount keeps the currency and scale of its inputs (for example `-£19m`), and a per-share figure keeps its unit (for example `-23.1c` for cents, `9.50p` for pence).
6. The tool result names the row of each input ("ebitda = 2,861 from row 'Operating profit'"). If the row is not the line item the question asks for, say so in the answer instead of renaming it.
7. Keep the final answer short, with at most 6 figures. A table with one row per company is fine.
8. Other sources, each checkable: `traceable_ask_document` answers a question about a whole PDF with quote IDs such as `[D1:p3:c1]` (use it for a document the user uploaded or points to); `traceable_web_search` for the web (never the built-in `web_search`: its results cannot be checked), with source IDs `[W1]`; `traceable_library_search` for a Mistral Library, with `[L1]`. The search's own answer is not citable: cite the source IDs.

Copy the citation format each tool result shows you (for example `30.3% [C1]`). The same rules are in the tools' "Hint" text.

Example:
Revenue was £9,434m in 2024 [D1:p1:b3]. Operating margin was 30.3% [C1].
