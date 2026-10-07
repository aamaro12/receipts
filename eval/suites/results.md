# Receipts on unseen public filings, after the scale fixes (5 Oct 2026)

Suites: `eval/suites/filings` (16 questions: Affirm FQ1'25 and FQ4'24 supplements, NVIDIA Q3 FY24 CFO commentary; the set
where the thousands-as-millions bug was found) and `eval/suites/heldout` (8 questions on Affirm's FQ3'24 shareholder
letter, written after the fix; its answer-key check, not model runs, surfaced the heading and "(27.9) %" fixes).
Mistral Medium 3.5, Vibe 2.25.8, 3 runs per question per arm, 16-turn cap. The counts below are after reading every flagged
answer by hand.

| | Without the check (calc) | With the check (full) |
|---|---|---|
| Correct, development set | 43 of 48 | 43 of 48 |
| Correct, held-out set | 23 of 24 | 24 of 24 |
| Correct, all | 66 of 72 (92%) | 67 of 72 (93%) |
| Correct and fully traced | about 37 of 72 (51%) | 67 of 72 (93%) |
| Runs with a wrong or unit-less figure in the final answer | 3 | 0 |
| Runs with no answer (turn cap) | 3 | 5 |

Before the fix (same development questions): with the check, 30 of 48 correct, 12 runs with
thousands written as millions.

Scorer false negatives corrected by hand: `$(73,460)k`, `$(615,847) thousand`, `($310.0) million`, `\(27.9\)%`,
an extra true figure (net loss $45m) and a "Note: figures are in thousands" answer. Strict rule applied to both arms:
a dollar figure copied from a thousands table without saying "thousand" is wrong (`$77,075` for $77.075m).
