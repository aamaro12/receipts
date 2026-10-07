# Eval results

Written 2026-09-24 15:04 by eval/run_eval.py from the runs in eval/work. Raw counts: each run of each question counts once. Headline: correct and fully traced.

| Arm | Correct and fully traced | Correct | Wrong figures in final answers | Figures traced | Denials per answer | Turn-limit hits | Empty answers | Seconds, median / worst | Cost, median / total |
|---|---|---|---|---|---|---|---|---|---|
| read | 2 of 45 | 36 of 45 | 13 | 47 of 256 | 0.00 | 0 | 0 | 10.5 / 39.8 | $0.02 / $0.81 |
| calc | 28 of 45 | 42 of 45 | 10 | 116 of 171 | 0.00 | 0 | 0 | 13.4 / 53.9 | $0.02 / $1.13 |
| full | 42 of 45 | 42 of 45 | 0 | 165 of 165 | 0.40 | 0 | 0 | 14.5 / 37.4 | $0.03 / $1.41 |

Full arm: wrong figures the check caught (in a denied draft, gone from the final answer): 9; wrong figures that got through: 0.

## Correct and fully traced, per question

| Question | Kind | read | calc | full |
|---|---|---|---|---|
| f1 | finance | 0/3 | 3/3 | 3/3 |
| f2 | finance | 0/3 | 3/3 | 3/3 |
| f3 | finance | 0/3 | 2/3 | 3/3 |
| f4 | finance | 0/3 | 2/3 | 3/3 |
| f5 | finance | 0/3 | 1/3 | 3/3 |
| f6 | finance | 0/3 | 2/3 | 3/3 |
| f7 | finance | 0/3 | 1/3 | 3/3 |
| f8 | finance | 0/3 | 0/3 | 3/3 |
| t1 | trap-scale | 0/3 | 3/3 | 3/3 |
| t2 | trap-year | 0/3 | 3/3 | 3/3 |
| t3 | trap-cents | 0/3 | 0/3 | 0/3 |
| t4 | trap-year-end | 0/3 | 2/3 | 3/3 |
| l1 | logistics | 0/3 | 3/3 | 3/3 |
| l2 | logistics | 0/3 | 0/3 | 3/3 |
| l3 | logistics | 2/3 | 3/3 | 3/3 |

## Runs that were not correct

- read/f3/1: missing ['5.02404%', '-0.603792%'], wrong [], not said []
- read/f3/2: missing ['5.02404%'], wrong ['4.9%'], not said []
- read/f3/3: missing ['29.60679%'], wrong ['29.60%'], not said []
- read/f5/3: missing ['10.09564%'], wrong [], not said []
- read/f8/1: missing [], wrong ['29.60%'], not said []
- read/f8/3: missing ['0.719690pp'], wrong ['0.73 percentage points'], not said []
- read/t3/1: missing [], wrong ['173.2 pence', '196.3 pence', '23.1 pence'], not said ['cent']
- read/t3/2: missing ['173.2c', '196.3c', '-23.1c'], wrong ['173.2 pence', '196.3 pence', '23.1 pence'], not said ['cent']
- read/t3/3: missing ['173.2c', '196.3c', '-23.1c'], wrong ['173.2 pence', '196.3 pence', '23.1 pence'], not said ['cent']
- calc/t3/1: missing ['173.2c', '196.3c', '-23.1c'], wrong ['173.2p', '196.3p', '-23.1p', '23.1 pence'], not said ['cent']
- calc/t3/2: missing ['173.2c', '196.3c', '-23.1c'], wrong ['173.2p', '196.3p', '-23.1p'], not said ['cent']
- calc/t3/3: missing ['173.2c', '196.3c', '-23.1c'], wrong ['173.2p', '196.3p', '-23.1p'], not said ['cent']
- full/t3/1: missing [], wrong [], not said ['cent']
- full/t3/2: missing [], wrong [], not said ['cent']
- full/t3/3: missing [], wrong [], not said ['cent']

## False alarms

Read by hand from `eval/denials.md` on 24 Sep 2026. There were 18 denials across the 45 full-arm runs. Four of them denied figures that were correct and cited, in 3 runs:

| Run and denial | What was denied | Why | Retries it cost |
|---|---|---|---|
| full/f8/2, denial 1 | "RELX higher by 0.72pp [C3]" | C3 had been computed as Diageo minus RELX (-0.720): a sign convention | 1 |
| full/t4/3, denials 2 and 3 | "Operating margin: 27.0% [C1]", followed by the line "[C1] = 5,547 / 20,555 = 27.0%" | The explanation line put the citation before its figure, a format the check still rejects (spec §9) | 2 |
| full/l3/2, denial 1 | "USD 759,500.00" | The citations were placed away from the figure | 1 |

The other 14 denials flagged real problems:
- uncited figures;
- changes and derived figures without a calculation;
- a phantom [C1];
- pence written for a row in US cents (3 of 3 t3 runs; all were fixed to cents).

## Notes on scoring

The runs were re-scored with `--score-only` after two fixes to the scorer and one to the figure grammar. All three applied to every arm, each pinned by a test built from the real answer that exposed it:
1. **Formula lines are working, not claims.** A line with "=" and an operator is not scored as a claim; its result may still supply a key figure. Example: read/f3/1 had been marked wrong for "(£9,434/£8,553)^(1/2) – 1 = 0.0504".
2. **Parentheses and "Yes".** A parenthesised amount in prose is read as an aside, not a negative (calc/l2). "Yes" affirms a match (full/l3/2).
3. **Abbreviated dates (after the final code review).** "31 Dec" and "30 Jun" were read as the figures '31' and '30' (read/f3/3, read/f8/1), because only full month names were masked. The fix to the figure grammar applies to the check and the scorer alike. It removed these 4 date fragments from the read arm's wrong figures (17 to 13) and changed nothing else.

The only full-arm misses are t3, 3 of 3 runs. The figures were correct and in cents, but none of those answers said that the question's "pence" was the wrong unit, so the key's "must say: cent" was not met.
