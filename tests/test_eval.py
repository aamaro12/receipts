# tests/test_eval.py
import hashlib, json, pathlib, shutil, sys
import pytest
from conftest import FIXTURES, ROOT
from traceable.check import run as check_run
from traceable.numbers import parse_block, matches_in_block
from traceable.server import tool_read_document, tool_calculate_many
from test_ocr import BrokenOcr
sys.path.insert(0, str(ROOT / "eval"))
from run_eval import (read_yaml, load, score_answer, key_figures, main, Result, EvalStopped, DOCS)  # noqa: E402

QS = {q["id"]: q for q in load(ROOT / "eval/questions.yaml", ROOT / "eval/answers.yaml")}

def fixture_blocks(name):
    return [parse_block(b["content"]) for p in json.load(open(FIXTURES / name))["pages"] for b in p["blocks"]
            if b["type"] not in ("header", "footer", "title")]

RELX, DIAGEO = fixture_blocks("relx_is.json"), fixture_blocks("diageo_is.json")

def test_read_yaml_reads_the_subset_the_eval_files_use(tmp_path):
    p = tmp_path / "q.yaml"
    p.write_text("# a comment\n- id: f1                 # a trailing comment\n  kind: finance\n  docs: [relx, diageo]\n"
                 "  question: \"What were RELX's revenue and profit? (see: the table)\"\n  expect:\n    - £9,434m        # revenue\n"
                 "    - -0.603792%\n  must_say:\n    - 'fiscal|financial year'\n  runs: 3\n  draft: true\n- id: f2\n")
    assert read_yaml(p) == [{"id": "f1", "kind": "finance", "docs": ["relx", "diageo"],
                             "question": "What were RELX's revenue and profit? (see: the table)", "expect": ["£9,434m", "-0.603792%"],
                             "must_say": ["fiscal|financial year"], "runs": 3, "draft": True}, {"id": "f2"}]
    p.write_text("- id: x\n    bad: indent\n")
    with pytest.raises(ValueError, match="unsupported"):
        read_yaml(p)

def test_the_question_set_has_keys_and_the_four_traps():
    assert list(QS) == [f"f{i}" for i in range(1, 9)] + ["t1", "t2", "t3", "t4", "l1", "l2", "l3"]
    assert [QS[t]["kind"] for t in ("t1", "t2", "t3", "t4")] == ["trap-scale", "trap-year", "trap-cents", "trap-year-end"]
    assert "in billions" in QS["t1"]["question"] and "in pence" in QS["t3"]["question"]
    assert "calendar year 2023" in QS["t4"]["question"] and "2023" in QS["t2"]["question"]
    assert "2024 operating margin and 2022-2024 revenue CAGR" in QS["f3"]["question"]        # the demo question
    for q in QS.values():
        key_figures(q)                                                     # every key figure is one parsable figure

def test_the_answer_key_matches_the_documents():  # every expected figure is in a block or is this arithmetic
    derived = {"30.32648%": 2861 / 9434, "5.02404%": (9434 / 8553) ** 0.5 - 1, "29.60679%": 6001 / 20269,
               "-0.603792%": (20269 / 20516) ** 0.5 - 1, "23.97341%": 613 / 2557, "9.5p": 103.6 - 94.1, "10.09564%": 9.5 / 94.1,
               "10.41594%": 298 / 2861, "-$286m": (20269 - 20555) * 1e6, "-1.39139%": -286 / 20555,
               "0.719690pp": 2861 / 9434 - 6001 / 20269, "29.27628%": 2682 / 9161, "-23.1c": 173.2 - 196.3}
    blocks = {"relx": RELX, "diageo": DIAGEO}
    for q in QS.values():
        if q["kind"] == "logistics":
            continue
        for text, e in zip(q["key"].get("expect") or [], key_figures(q)[0]):
            if text in derived:
                got = float(e.base) / 100 if e.is_percent else float(e.base)
                assert abs(got - derived[text]) <= 5e-7 * max(1, abs(derived[text])), (q["id"], text)
            else:
                assert any(matches_in_block(e, pb) for d in q["docs"] for pb in blocks[d]), (q["id"], text)

def test_scoring_catches_the_trap_answers():  # trap answers seen in runs: pence, £ for a $ table, a 1/3 root, the June year end
    ok = score_answer("Diageo's FY2024 net sales were $20.3bn [D1:p1:b7] and operating profit $6.0bn [D1:p1:b7].", QS["t1"], DIAGEO)
    assert ok["correct"] and ok["wrong"] == [] and ok["missing"] == []
    bad = score_answer("Net sales were £20.3bn [D1:p1:b7] and operating profit £6.0bn [D1:p1:b7].", QS["t1"], DIAGEO)
    assert not bad["correct"] and bad["wrong"] == ["£20.3bn", "£6.0bn"] and bad["missing"] == ["$20,269m", "$6,001m"]
    pence = score_answer("Basic EPS was 173.2 pence [D1:p1:b7] in FY2024 and 196.3 pence [D1:p1:b7] in FY2023, a fall of "
                         "23.1 pence [C1].", QS["t3"], DIAGEO)
    assert not pence["correct"] and pence["wrong"] == ["173.2 pence", "196.3 pence", "23.1 pence"]
    cents = score_answer("Diageo reports EPS in US cents, not pence: 173.2c [D1:p1:b7] in FY2024 against 196.3c [D1:p1:b7] "
                         "in FY2023, a fall of 23.1c [C1] or 11.8% [C2].", QS["t3"], DIAGEO)
    assert cents["correct"], cents
    rise = score_answer("EPS was 173.2c [D1:p1:b7] against 196.3c [D1:p1:b7], cents, a rise of 23.1c [C1].", QS["t3"], DIAGEO)
    assert rise["missing"] == ["-23.1c"]                                    # the sign is part of the figure
    cube = score_answer("RELX: margin 30.3% [C1], CAGR 3.32% [C2]. Diageo: margin 29.6% [C3], CAGR -0.403% [C4].", QS["f3"], RELX + DIAGEO)
    assert not cube["correct"] and cube["wrong"] == ["3.32%", "-0.403%"]
    june = score_answer("The document has fiscal years to 30 June, not calendar 2023. In the year to 30 June 2023 net sales "
                        "were $20,555 million [D1:p1:b7] and the operating margin 27.0% [C1].", QS["t4"], DIAGEO)
    assert june["correct"], june
    made_up = score_answer("Calendar 2023 net sales were about $20,412 million [C1].", QS["t4"], DIAGEO)
    assert not made_up["correct"] and made_up["wrong"] == ["$20,412 million"] and made_up["unsaid"]

def fake_repo(tmp_path):
    """The repo layout run_eval needs: the finance excerpts with their shipped OCR cache, a hooks.toml and the eval files."""
    repo = tmp_path / "repo"
    fin = repo / "demo/finance"
    (fin / ".traceable/ocr").mkdir(parents=True)
    (fin / ".vibe").mkdir()
    (fin / ".vibe/hooks.toml").write_text('[[hooks]]\nname = "traceable-check"\ntype = "post_agent"\ncommand = "true"\ntimeout = 30\n')
    for fixture, name in (("relx_is", "relx_income_statement.pdf"), ("diageo_is", "diageo_income_statement.pdf")):
        shutil.copy(FIXTURES / f"{fixture}.pdf", fin / name)
        sha = hashlib.sha256((fin / name).read_bytes()).hexdigest()
        shutil.copy(FIXTURES / f"{fixture}.json", fin / f".traceable/ocr/{sha}-mistral-ocr-latest-all-blocks.json")
    (repo / "eval").mkdir()
    for f in ("questions.yaml", "answers.yaml"):
        shutil.copy(ROOT / "eval" / f, repo / "eval" / f)
    return repo

CALCS = [("RELX 2024 operating margin", [("op", "2,861", "D1:p1:b3"), ("rev", "9,434", "D1:p1:b3")], "op / rev"),
         ("RELX 2022-2024 revenue CAGR", [("r24", "9,434", "D1:p1:b3"), ("r22", "8,553", "D1:p1:b3")], "(r24 / r22) ** 0.5 - 1"),
         ("Diageo FY2024 operating margin", [("op", "6,001", "D2:p1:b7"), ("ns", "20,269", "D2:p1:b7")], "op / ns"),
         ("Diageo 2022-2024 net sales CAGR", [("n24", "20,269", "D2:p1:b7"), ("n22", "20,516", "D2:p1:b7")], "(n24 / n22) ** 0.5 - 1")]
TRACED = "RELX: 2024 operating margin 30.3% [C1], 2022-2024 revenue CAGR 5.02% [C2]. Diageo: 29.6% [C3] and -0.604% [C4]."

class StubVibe:
    """Stands in for install.sh and Vibe, never runs either. Its 'model' reads the documents in the run folder with the
    real tool (the shipped OCR cache answers; the OCR client would fail), computes with the real calculator in the calc
    and full arms, writes a Vibe-like session, and runs the real hook where the folder has one (the full arm)."""

    def __init__(self, not_checked=False):
        self.calls, self.sessions, self.not_checked = [], 0, not_checked

    def __call__(self, cmd, cwd, env):
        self.calls.append(cmd)
        if cmd[0] != "perl":                                     # install.sh, or the read-only config rewrite
            return Result(0, "", "", 0.1)
        home, run = pathlib.Path(env["VIBE_HOME"]), int(cwd.name)
        arm = home.name.removeprefix(".home-")
        prompt = cmd[cmd.index("-p") + 1]
        if arm == "read" and run == 2:
            return Result(1, "", "<vibe_stop_event>Turn limit of 16 reached</vibe_stop_event>", 88.0)
        msgs = [{"role": "user", "content": prompt, "injected": False}]
        for pdf in sorted(cwd.glob("*.pdf"), key=lambda p: p.name != "relx_income_statement.pdf"):
            msgs.append({"role": "tool", "content": tool_read_document(str(pdf), client=BrokenOcr(), root=cwd)})
        if arm == "read":
            answer = "RELX: 30.3% [D1:p1:b3] and 5.02% [D1:p1:b3]. Diageo: 29.6% [D2:p1:b7] and -0.604% [D2:p1:b7]."
        else:
            calcs = [{"title": t, "inputs": [{"name": n, "value": v, "source_id": s} for n, v, s in ins], "expression": e,
                      "format": "pct"} for t, ins, e in CALCS]
            msgs.append({"role": "tool", "content": tool_calculate_many(calcs, root=cwd)})
            answer = TRACED
            if arm == "full" and run == 1:                       # a draft with the cube-root CAGR, denied, then fixed
                msgs += [{"role": "assistant", "content": answer.replace("5.02% [C2]", "3.32% [C2]")},
                         {"role": "user", "content": "Attempt 1 of 4. Untraced figures (1 of 4):\n1. '3.32%' does not match C2 = 5.02%",
                          "injected": True}]
        msgs.append({"role": "assistant", "content": answer})
        self.sessions += 1
        session = home / "logs/session" / f"session_{self.sessions:03d}"
        session.mkdir(parents=True)
        (session / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in msgs))
        (session / "meta.json").write_text(json.dumps({"environment": {"working_directory": str(cwd)},
                                                       "stats": {"steps": 4, "session_cost": 0.02}}))
        if (cwd / ".vibe/hooks.toml").exists():
            payload = {"cwd": str(cwd), "hook_event_name": "post_agent"}
            if not self.not_checked:
                payload |= {"transcript_path": str(session / "messages.jsonl"), "session_id": "s"}
            check_run(payload)
        return Result(0, answer, "", 12.5)

def test_eval_runs_three_arms_on_a_stubbed_vibe_and_writes_raw_counts(tmp_path):
    repo, stub = fake_repo(tmp_path), StubVibe()
    assert main(["--repo", str(repo), "--runs", "2", "--only", "f3"], runner=stub) == 0
    installs = [c for c in stub.calls if c[0] != "perl"]
    assert [c for c in installs if c[0] == "bash"] == [["bash", str(repo / "install.sh")]] * 3
    assert any("--read-only" in c for c in installs)
    vibe = [c for c in stub.calls if c[0] == "perl"]
    assert len(vibe) == 6 and all(c[:5] == ["perl", "-e", "alarm 300; exec @ARGV", "vibe", "--legacy-harness"] for c in vibe)
    assert all(c[-7:] == ["--trust", "--max-turns", "16", "--max-price", "0.5", "--output", "text"] for c in vibe)
    prompt = vibe[0][vibe[0].index("-p") + 1]
    assert prompt.startswith(f"Using {repo}/eval/work/full/f3/1/relx_income_statement.pdf and ")
    assert prompt.endswith("diageo_income_statement.pdf, what were each company's 2024 operating margin and 2022-2024 "
                           "revenue CAGR? Use net sales as Diageo's revenue; Diageo's fiscal year ends on 30 June.")
    results = (repo / "eval/results.md").read_text()
    assert "| read | 0 of 2 | 1 of 2 | 0 | 0 of 4 | 0.00 | 1 | 1 | 50.2 / 88.0 | $0.02 / $0.02 |" in results
    assert "| calc | 2 of 2 | 2 of 2 | 0 | 8 of 8 | 0.00 | 0 | 0 | 12.5 / 12.5 | $0.02 / $0.04 |" in results
    assert "| full | 2 of 2 | 2 of 2 | 0 | 8 of 8 | 0.50 | 0 | 0 | 12.5 / 12.5 | $0.02 / $0.04 |" in results
    assert "wrong figures the check caught (in a denied draft, gone from the final answer): 1" in results
    assert "| f3 | finance | 0/2 | 2/2 | 2/2 |" in results and "\u2014" not in results
    assert "'3.32%' does not match C2 = 5.02%" in (repo / "eval/denials.md").read_text()
    assert main(["--repo", str(repo), "--only", "f3", "--score-only"], runner=stub) == 0 and len(stub.calls) == 10

def test_a_full_arm_run_without_a_real_check_stops_the_eval(tmp_path):  # the unified harness would pass unchecked
    repo = fake_repo(tmp_path)
    with pytest.raises(EvalStopped, match="NOT CHECKED"):
        main(["--repo", str(repo), "--runs", "1", "--only", "f3"], runner=StubVibe(not_checked=True))

# --- Native build (eval pilot): score what an answer claims, not the working in its formula lines ---

PILOT_READ_F3 = """**RELX Group**
- 2024 operating margin: **30.3%**
  *Operating profit £2,861m / Revenue £9,434m = 0.3033*
- 2022–2024 revenue CAGR: **5.0%**
  *(£9,434/£8,553)^(1/2) – 1 = 0.0504*

**Diageo**
- 2024 operating margin: **29.6%**
  *Operating profit $6,001m / Net sales $20,269m = 0.2960*
- 2022–2024 revenue CAGR: **-0.6%**
  *($20,269/$20,516)^(1/2) – 1 = –0.0060*

Sources:
- RELX: [D1:p1:b3]
- Diageo: [D2:p1:b7]"""

def test_formula_lines_are_working_not_claims():  # a correct answer scored 8 "wrong" intermediates
    blocks = fixture_blocks("relx_is.json") + fixture_blocks("diageo_is.json")
    s = score_answer(PILOT_READ_F3, QS["f3"], blocks)
    assert s["correct"] and s["wrong"] == [] and s["missing"] == [], s
    wrong_headline = PILOT_READ_F3.replace("margin: **30.3%**", "margin: **30.9%**")
    s2 = score_answer(wrong_headline, QS["f3"], blocks)
    assert not s2["correct"] and s2["wrong"] == ["30.9%"], s2


REAL_CALC_L2 = '## Release Decision: **NO — withhold container MSKU4417302**\n\n### Comparison Table\n\n| Document | Gross Weight | Declared Value |\n|----------|--------------|----------------|\n| Bill of Lading | 18,420 kg [D1:p1:b10] | — |\n| Commercial Invoice | — | USD 759,500.00 [D2:p1:b8] |\n| Arrival Notice | 19,240 kg [D3:p1:b5] | USD 759,500.00 [D3:p1:b6] |\n\n### Analysis\n\n- **Declared value matches** across Commercial Invoice and Arrival Notice (USD 759,500.00).\n- **Gross weight discrepancy**: \n  - B/L: 18,420 kg\n  - Verified at terminal: 19,240 kg\n  - **Difference: +820 kg = +4.45%** [D3:p1:b7]\n\nPer the Arrival Notice rule, **release is withheld where verified gross weight differs from the B/L by more than 2%**. The 4.45% discrepancy exceeds this threshold.\n\n**Result: Do not release container MSKU4417302.**'
REAL_FULL_L3 = 'Yes. The declared value of container MSKU4417302 is USD 759,500.00 [D1:p1:b8] on the commercial invoice and USD 759,500.00 [D2:p1:b6] on the arrival notice.'

def logistics_blocks():
    import hashlib
    out = []
    for name in ("commercial_invoice", "arrival_notice", "bill_of_lading"):
        pdf = ROOT / "demo/logistics" / f"{name}.pdf"
        cache = pdf.parent / ".traceable/ocr" / f"{hashlib.sha256(pdf.read_bytes()).hexdigest()}-mistral-ocr-latest-all-blocks.json"
        out += [parse_block(b["content"]) for p in json.load(open(cache))["pages"] for b in p["blocks"]]
    return out

def test_a_parenthesised_aside_is_not_a_negative_wrong_figure():  # seen in a run: "(USD 759,500.00)" after a sentence
    s = score_answer(REAL_CALC_L2, QS["l2"], logistics_blocks())
    assert "(USD 759,500.00)" not in s["wrong"], s
    s2 = score_answer("The declared value was -USD 759,500.00 on the invoice.", QS["l3"], logistics_blocks())
    assert s2["wrong"] == ["-USD 759,500.00"], s2          # an explicit minus is still a wrong sign

def test_yes_affirms_a_match():  # seen in a run: "Yes. The declared value ... is USD 759,500.00 ... and USD 759,500.00"
    s = score_answer(REAL_FULL_L3, QS["l3"], logistics_blocks())
    assert s["correct"] and s["unsaid"] == [], s


REAL_READ_F8 = "**RELX** had the higher 2024 operating margin, by **0.72 percentage points**.\n\n---\n\n**Calculation:**\n\n- **RELX** (year ended 31 Dec 2024):  \n  Operating profit £2,861m / Revenue £9,434m = **30.33%**\n\n- **Diageo** (year ended 30 Jun 2024):  \n  Operating profit $6,001m / Net sales $20,269m = **29.60%**\n\n- **Difference**: 30.33% – 29.60% = **0.72 pp**"   # real run read/f8/1

def test_abbreviated_dates_are_not_wrong_figures():  # "31 Dec 2024" and "30 Jun 2024" scored '31', '30' wrong
    blocks = fixture_blocks("relx_is.json") + fixture_blocks("diageo_is.json")
    s = score_answer(REAL_READ_F8, QS["f8"], blocks)
    assert s["wrong"] == ["29.60%"], s                    # the truncated 29.607% stays wrong; the dates are not figures

def test_a_suite_folder_brings_its_own_questions_answers_and_documents(tmp_path):
    repo, stub = fake_repo(tmp_path), StubVibe()
    suite = repo / "eval/suites/mini"
    suite.mkdir(parents=True)
    for name in ("questions.yaml", "answers.yaml"):
        shutil.copy(repo / "eval" / name, suite / name)
    (suite / "docs.json").write_text(json.dumps(DOCS))
    out = repo / "eval/out/mini"
    assert main(["--repo", str(repo), "--suite", "eval/suites/mini", "--out", "eval/out/mini", "--runs", "1",
                 "--arms", "calc,full", "--only", "f3"], runner=stub) == 0
    prompt = [c for c in stub.calls if c[0] == "perl"][0]
    assert f"{out}/work/full/f3/1/relx_income_statement.pdf" in prompt[prompt.index("-p") + 1]
    assert "| f3 | finance |" in (out / "results.md").read_text() and not (repo / "eval/results.md").exists()
    assert DOCS.get("relx") == "demo/finance/relx_income_statement.pdf"     # the default documents are restored
