# tests/test_check.py
import json
from conftest import FIXTURES
from traceable.ledger import Ledger
from traceable.server import tool_read_document, tool_calculate
from traceable.check import run, check_answer, select_answer
from test_ocr import FakeOcr

def setup(tmp_path):
    tool_read_document(str(FIXTURES / "relx_is.pdf"), client=FakeOcr(), root=tmp_path)
    r = tool_calculate("margin", [{"name": "op", "value": "2,861", "scale": "m", "source_id": "D1:p1:b3"},
                                  {"name": "rev", "value": "9,434", "scale": "m", "source_id": "D1:p1:b3"}],
                       "op / rev", "pct", root=tmp_path)
    return r  # "C1 = 30.3%"

def transcript(tmp_path, msgs):
    p = tmp_path / "messages.jsonl"
    p.write_text("\n".join(json.dumps(m) for m in msgs))
    return {"cwd": str(tmp_path), "transcript_path": str(p), "session_id": "s", "hook_event_name": "post_agent"}

Q = {"role": "user", "content": "What was RELX's 2024 operating margin?", "injected": False}

def test_good_answer_allowed(tmp_path):
    c = setup(tmp_path)
    out = run(transcript(tmp_path, [Q, {"role": "tool", "content": c},
        {"role": "assistant", "content": "Operating margin was 30.3% [C1], from £2,861m of operating profit on £9,434m revenue [D1:p1:b3]."}]))
    assert out["decision"] == "allow" and "3 figures traced" in out["system_message"]
    assert (tmp_path / ".traceable/report.html").exists()

def test_uncited_and_wrong_bracket_denied(tmp_path):
    c = setup(tmp_path)
    out = run(transcript(tmp_path, [Q, {"role": "tool", "content": c},
        {"role": "assistant", "content": "Operating margin was 30.3% (C1) on £9,434m revenue."}]))
    assert out["decision"] == "deny" and "30.3%" in out["reason"] and "£9,434m" in out["reason"]

def test_percent_cited_to_block_needs_calc(tmp_path):
    c = setup(tmp_path)
    out = run(transcript(tmp_path, [Q, {"role": "tool", "content": c},
        {"role": "assistant", "content": "Operating margin was 30.3% [D1:p1:b3]."}]))
    assert out["decision"] == "deny" and "[Cn]" in out["reason"]

def test_retry_checks_only_latest_answer(tmp_path):
    c = setup(tmp_path)
    out = run(transcript(tmp_path, [Q, {"role": "tool", "content": c},
        {"role": "assistant", "content": "Margin was 30.3%."},
        {"role": "user", "content": "Untraced figures: 1. '30.3%'", "injected": True},
        {"role": "assistant", "content": "Margin was 30.3% [C1]."}]))
    assert out["decision"] == "allow"

def test_stale_calc_from_previous_question_is_phantom(tmp_path):
    c = setup(tmp_path)
    out = run(transcript(tmp_path, [Q, {"role": "tool", "content": c},
        {"role": "assistant", "content": "Margin was 30.3% [C1]."},
        {"role": "user", "content": "And the gross margin?", "injected": False},
        {"role": "assistant", "content": "It was 30.3% [C1]."}]))
    assert out["decision"] == "deny" and "C1" in out["reason"]

def test_phantom_block_and_mismatch(tmp_path):
    setup(tmp_path)
    out = run(transcript(tmp_path, [Q, {"role": "assistant", "content": "Revenue was £9,434m [D9:p1:b1] and costs £5,000m [D1:p1:b3]."}]))
    assert out["decision"] == "deny" and "D9:p1:b1" in out["reason"] and "£5,000m" in out["reason"]
    assert "is not in D1:p1:b3" in out["reason"]

def test_mismatch_names_the_block_that_contains_the_figure(tmp_path):
    setup(tmp_path)
    out = run(transcript(tmp_path, [Q, {"role": "assistant", "content": "Operating profit was 2861 [D1:p1:b2]."}]))
    assert out["decision"] == "deny" and "it appears in D1:p1:b3" in out["reason"]

def test_table_row_scope_and_header_derived_words(tmp_path):
    c = setup(tmp_path)
    ans = "| Company | Revenue | Operating margin |\n|---|---|---|\n| RELX | £9,434m [D1:p1:b3] | 30.3% [C1] |"
    assert run(transcript(tmp_path, [Q, {"role": "tool", "content": c}, {"role": "assistant", "content": ans}]))["decision"] == "allow"

def test_run_of_figures_in_one_clause(tmp_path):
    setup(tmp_path)
    ans = "Revenue grew from £8,553m to £9,434m [D1:p1:b3]."
    assert run(transcript(tmp_path, [Q, {"role": "assistant", "content": ans}]))["decision"] == "allow"

def test_no_figures_allows(tmp_path):
    setup(tmp_path)
    out = run(transcript(tmp_path, [Q, {"role": "assistant", "content": "Which year do you mean?"}]))
    assert out["decision"] == "allow"

def test_fail_open(tmp_path):
    assert run({"cwd": str(tmp_path), "transcript_path": ""})["decision"] == "allow"
    assert run({"cwd": str(tmp_path), "transcript_path": str(tmp_path / "missing.jsonl")})["decision"] == "allow"
    p = tmp_path / "m.jsonl"; p.write_text(json.dumps({"role": "user", "content": "summary", "injected": True}))
    assert run({"cwd": str(tmp_path), "transcript_path": str(p)})["decision"] == "allow"


import time
from traceable.server import tool_calculate_many

B = "[D1:p1:b3]"
OP = {"name": "op", "value": "2,861", "scale": "m", "source_id": "D1:p1:b3"}
REV = {"name": "rev", "value": "9,434", "scale": "m", "source_id": "D1:p1:b3"}

def with_calcs(tmp_path, *specs):
    tool_read_document(str(FIXTURES / "relx_is.pdf"), client=FakeOcr(), root=tmp_path)
    return tool_calculate_many([{"title": t, "inputs": i, "expression": e, "format": f} for t, i, e, f in specs],
                               root=tmp_path).splitlines()

def verdict(tmp_path, answer, tools=(), q=Q):
    out = run(transcript(tmp_path, [q] + [{"role": "tool", "content": t} for t in tools] + [{"role": "assistant", "content": answer}]))
    figs = Ledger(tmp_path).load()["checks"][-1]["figures"]
    return out["decision"], {f["raw"]: f["status"] for f in figs}, out

def add_block(tmp_path, text, block_id="D2:p1:b1"):
    lg = Ledger(tmp_path); d = lg.load()
    d["documents"]["extra-" + block_id] = {"doc_id": block_id.split(":")[0], "path": "/k.pdf", "model": "m",
        "pages": [{"index": 0, "dimensions": {"width": 720, "height": 1018}}],
        "blocks": [{"block_id": block_id, "page_index": 0, "n": 1, "type": "text", "bbox": [0, 0, 10, 10],
                    "text": text, "raw_text": text, "block_scale": 0}]}
    lg.save(d)

MARGIN = ("margin", [OP, REV], "op / rev", "pct")
GROWTH = ("growth", [{**REV, "name": "a"}, {**REV, "name": "b", "value": "9,161"}], "a / b - 1", "pct")

def test_calc_citations_check_percent_unit_sign_and_display(tmp_path):
    T = with_calcs(tmp_path, MARGIN, GROWTH,
                   ("op change", [{**OP, "name": "a"}, {**OP, "name": "b", "value": "2,682"}], "a - b", "num"),
                   ("ratio", [OP, REV], "op / rev", "num"))          # C1 30.3%, C2 2.98%, C3 179,000,000, C4 0.303
    for answer in ["Revenue declined 3.0% [C2] in 2024.", "Operating profit fell by £179m [C3].",
                   "Operating margin was 0.3% [C4].", "Revenue growth was 3.0bps [C2].", "Net debt/EBITDA was 30.3x [C1]."]:
        assert verdict(tmp_path, answer, T)[0] == "deny", answer
    for answer in ["Revenue grew 3.0% [C2].", "Operating profit rose by £179m [C3].", "The ratio was 0.303 [C4].",
                   "Operating margin was c.30% [C1].", "Operating margin was 30 per cent [C1]."]:
        assert verdict(tmp_path, answer, T)[0] == "allow", answer

def test_block_citations_check_year_sign_currency_unit_and_scale(tmp_path):
    setup(tmp_path)
    for answer in [f"Revenue in 2024 was £9,161m {B}.", f"Operating profit grew by £7m {B} in 2024.",
                   f"Revenue increased by £2m {B}.", f"RELX revenue was $9,434m {B}.", f"Basic EPS was £103.6 {B}.",
                   f"Basic EPS was £103.6m {B}.", f"Operating profit was -£2,861m {B}.", f"Operating profit was (£2,861m) {B}.",
                   f"Revenue was £9,434 {B}.", f"Revenue declined £9,434m {B}."]:
        assert verdict(tmp_path, answer)[0] == "deny", answer
    assert verdict(tmp_path, f"Revenue was £9,434m (2024) {B}.")[1] == {"£9,434m": "traced"}
    assert "but not in its 2024 column" in verdict(tmp_path, f"Revenue in 2024 was £9,161m {B}.")[2]["reason"]

def test_year_hints_follow_the_nearest_figure(tmp_path):
    setup(tmp_path)
    for answer in [f"Revenue rose from £9,161m in 2023 to £9,434m {B}.", f"In 2023 revenue was £9,161m and in 2024 £9,434m {B}.",
                   f"Revenue was £9,161m in 2023 and £9,434m in 2024 {B}.", f"2024 revenue was £9,434m, up from £9,161m {B}.",
                   f"Revenue was £9,434m (2023: £9,161m) {B}.", f"Revenue was £9,434m {B} in 2024."]:
        assert verdict(tmp_path, answer)[0] == "allow", answer
    for answer in [f"Revenue was £9,161m in 2024 {B}.", f"In 2024 revenue was £9,161m {B}.",
                   "| Metric | 2024 |\n|---|---|\n| Revenue | £9,161m [D1:p1:b3] |",
                   "| Year | Revenue |\n|---|---|\n| 2024 | £9,161m [D1:p1:b3] |"]:
        assert verdict(tmp_path, answer)[0] == "deny", answer

FA1_ALLOWED = [
    "Operating margin was c.30% [C1].", "Operating margin was ~30% [C1].", "Operating margin was 30 per cent [C1].",
    "Operating margin was 30.3 percent [C1].", f"Revenue was £9.4 billion {B}.", f"Revenue was 9.4bn GBP {B}.",
    f"Revenue was GBP 9.4bn {B}.", f"Revenue was £9,434,000,000 {B}.",
    f"## Results\n- **Revenue:** *£9,434m* {B}\n- _Operating margin_: **30.3%** [C1]",
    "Revenue was £9,434m and comprehensive income £2,145m [D1:p1:b3, D1:p2:b4].",
    "Margin and growth were 30.3% and 3.0% [C1][C2].", "Margin and growth were 30.3% and 3.0% [C1, C2].",
    "Margin and growth were 30.3% and 3.0% [C1; C2].", "Revenue of £9,434m and margin of 30.3% [D1:p1:b3][C1].",
    f"Revenue was £9,434m. {B}", "Operating margin was 30.3% (2024) [C1].", f"Revenue was £9,434m (2023: £9,161m) {B}.",
    "| Company | Revenue | Margin | Source |\n|---|---|---|---|\n| RELX | £9,434m | 30.3% | [D1:p1:b3] [C1] |",
    "| Company | Revenue | Operating margin |\n|---|---|---|\n| RELX | £9.4bn [D1:p1:b3] | 30.3% [C1] |",
    f"13. Revenue was £9,434m {B}.", f"Basic EPS was 103.6p {B}.", f"Basic EPS was 103.6 pence {B}.",
    f"Disposals were -£6m {B}.", f"Disposals were (£6m) {B}.", f"Disposals produced a £6m loss {B}.",
    f"Cost of sales rose to £3,300m {B}.", f"Tax expense increased to £613m {B}.", f"Share of JV results fell to £43m {B}.",
    f"Revenue was £9,434m vs. £9,161m {B}.", "Operating margin was 30.3% [C1], i.e. 2,861 [D1:p1:b3] / 9,434 [D1:p1:b3] × 100.",
    "Admin expenses changed by -0.2% [C3].", "JV results were 0.5% of revenue [C4].",
    f"For the year to 31/12/2024 revenue was £9,434m {B}.", f"Revenue was £9,434m {B} (page 140 of the annual report).",
    f"Revenue was in the £9.2-9.4bn range {B}."]

def test_correct_answer_variants_are_allowed(tmp_path):
    T = with_calcs(tmp_path, MARGIN, GROWTH,
                   ("admin change", [{**OP, "name": "a", "value": "(1,846)"}, {**OP, "name": "b", "value": "(1,850)"}], "a / b - 1", "pct"),
                   ("JV share", [{**OP, "name": "a", "value": "43"}, REV], "a / rev", "pct"))
    assert [t.split("  ")[0] for t in T] == ["C1 = 30.3%", "C2 = 2.98%", "C3 = -0.216%", "C4 = 0.456%"]
    for answer in FA1_ALLOWED:
        dec, st, out = verdict(tmp_path, answer, T)
        assert dec == "allow", (answer, st, out.get("reason"))

def test_known_limitations_stay_denied(tmp_path):
    T = with_calcs(tmp_path, MARGIN)
    for answer in [f"Revenue was 9 434 {B}.",                      # space as a thousands separator
                   f"{B} Revenue was £9,434m.",                    # citation before the figure
                   "Revenue was £9,434m [1].\n\nSources: [1] D1:p1:b3",   # footnote-style sources
                   f"Basic EPS was £1.036 {B}.",                   # pence converted to pounds
                   f"Revenue saw an increase to £9.4bn {B}."]:     # derived word next to a rounded level
        assert verdict(tmp_path, answer, T)[0] == "deny", answer

def test_ifrs_captions_are_not_direction_words(tmp_path):
    setup(tmp_path)
    assert verdict(tmp_path, "Items that will not be reclassified to profit or loss were £32m [D1:p2:b4].")[0] == "allow"
    assert verdict(tmp_path, "Other comprehensive income/(loss) of £201m [D1:p2:b4] was recorded.")[0] == "allow"

def test_derived_words_are_found_through_bold_markers(tmp_path):
    setup(tmp_path)
    for answer in ["The operating margin change was £9.2bn [D1:p1:b3].",
                   "**The** **operating** **margin** **change** **was** **£9.2bn** [D1:p1:b3]."]:
        dec, st, _ = verdict(tmp_path, answer)
        assert dec == "deny" and st == {"£9.2bn": "derived_needs_calc"}, answer

def test_derived_waiver_covers_line_items_but_not_change_figures(tmp_path):
    setup(tmp_path)
    add_block(tmp_path, "Key performance indicators: underlying revenue growth 7%; adjusted operating margin 34.1%; "
                        "underlying growth in adjusted operating profit 10%. Effective tax rate 24.0%. "
                        "Change in provisions (GBPm) 55. Total revenue (GBPm) 9,434.")
    for answer in ["The change in provisions was £55m [D2:p1:b1].", "**Change** **in** **provisions**: **55** [D2:p1:b1]"]:
        assert verdict(tmp_path, answer)[0] == "allow", answer     # the exact-match waiver stays for non-change figures
    # Reported KPIs stated exactly by the block stay citable (they cannot be recomputed from the statements) ...
    for answer in ["RELX's adjusted operating margin was 34.1% [D2:p1:b1].", "Underlying revenue growth was 7% [D2:p1:b1].",
                   "**Underlying** **revenue** **growth** was **7%** [D2:p1:b1].", "The effective tax rate was 24.0 per cent [D2:p1:b1]."]:
        assert verdict(tmp_path, answer)[0] == "allow", answer
    # ... but a rounded or different figure next to a derived word must be computed, and explicit changes always are.
    for answer in ["RELX's adjusted operating margin was 34% [D2:p1:b1].", "Adjusted operating profit increased by 10% [D2:p1:b1]."]:
        dec, st, out = verdict(tmp_path, answer)
        assert dec == "deny" and set(st.values()) == {"derived_needs_calc"}, answer
        assert "traceable_calculate" in out["reason"], answer

def test_question_figures_are_exempt_only_when_they_match_exactly(tmp_path):
    setup(tmp_path)
    q = {"role": "user", "content": "Did RELX's 2024 operating margin exceed 35%?", "injected": False}
    dec, st, out = verdict(tmp_path, "No, it did not exceed 35%.", q=q)
    assert dec == "allow" and st == {"35%": "from_question"} and "1 from your question, not verified" in out["system_message"]
    dec, st, _ = verdict(tmp_path, "No. Revenue was £35bn and operating profit was 35 billion pounds.", q=q)
    assert dec == "deny" and set(st.values()) == {"untraced"}

def test_table_header_rows_and_one_row_tables_are_checked(tmp_path):
    setup(tmp_path)
    q = {"role": "user", "content": "Summarise the income statement in one table.", "injected": False}
    for answer in ["| RELX revenue £99,999m | Margin 99.9% |\n|---|---|\n| see above | see above |",
                   "| Revenue | £99,999m | Margin | 99.9% |"]:
        dec, st, _ = verdict(tmp_path, answer, q=q)
        assert dec == "deny" and st == {"£99,999m": "untraced", "99.9%": "untraced"}, answer

def test_table_cells_use_their_own_column_header(tmp_path):
    setup(tmp_path)
    dec, st, _ = verdict(tmp_path, "| Company | Revenue | Revenue change |\n|---|---|---|\n| RELX | £9.4bn [D1:p1:b3] | £0.3bn [D1:p1:b3] |")
    assert dec == "deny" and st == {"£9.4bn": "traced", "£0.3bn": "derived_needs_calc"}

def test_answers_without_figures_record_a_check_and_refresh_the_report(tmp_path):
    setup(tmp_path)
    verdict(tmp_path, f"Revenue was £9,434m {B}.")
    assert run(transcript(tmp_path, [Q, {"role": "assistant", "content": "Which year do you mean?"}])) == {"decision": "allow"}
    assert Ledger(tmp_path).load()["checks"][-1]["figures"] == [] and (tmp_path / ".traceable/report.html").exists()

def test_figures_without_any_document_are_denied_with_guidance(tmp_path):
    assert run(transcript(tmp_path, [Q, {"role": "assistant", "content": "Which year do you mean?"}])) == {"decision": "allow"}
    assert not (tmp_path / ".traceable").exists()
    out = run(transcript(tmp_path, [Q, {"role": "assistant", "content": "The margin is 30.3%."}]))
    assert out["decision"] == "deny" and "No source documents were read in this folder" in out["reason"]

def test_denials_are_counted_per_question(tmp_path):
    c = setup(tmp_path)
    msgs = [Q, {"role": "tool", "content": c}, {"role": "assistant", "content": "Margin was 30.3%."}]
    assert "Attempt 1 of 4." in run(transcript(tmp_path, msgs))["reason"]
    msgs += [{"role": "user", "content": "Untraced figures", "injected": True}, {"role": "assistant", "content": "Margin was 30.3% (C1)."}]
    assert "Attempt 2 of 4." in run(transcript(tmp_path, msgs))["reason"]
    msgs += [{"role": "user", "content": "Untraced figures", "injected": True}, {"role": "assistant", "content": "Margin was 30.3% [C1]."}]
    assert run(transcript(tmp_path, msgs))["decision"] == "allow"
    checks = Ledger(tmp_path).load()["checks"]
    assert [c["attempt"] for c in checks] == [1, 2, 3] and len({c["question"] for c in checks}) == 1
    msgs += [{"role": "user", "content": "And in 2023?", "injected": False}, {"role": "assistant", "content": "It was 29.3%."}]
    assert "Attempt 1 of 4." in run(transcript(tmp_path, msgs))["reason"]

def test_message_content_lists_are_joined(tmp_path):
    c = setup(tmp_path)
    out = run(transcript(tmp_path, [Q, {"role": "tool", "content": c},
                                    {"role": "assistant", "content": [{"type": "text", "text": "Margin was 99.9% [C1]."}]}]))
    assert out["decision"] == "deny"
    out = run(transcript(tmp_path, [Q, {"role": "tool", "content": [{"type": "text", "text": c}]},
                                    {"role": "assistant", "content": "Margin was 30.3% [C1]."}]))
    assert out["decision"] == "allow"

def test_only_calculate_output_validates_a_calc_id(tmp_path):
    setup(tmp_path)
    out = run(transcript(tmp_path, [Q, {"role": "assistant", "content": "Margin 30.3% [C1]."},
        {"role": "user", "content": "And the gross margin?", "injected": False},
        {"role": "tool", "content": "[D1:p9:b2] Container C1 = 40ft high cube"}, {"role": "assistant", "content": "It was 30.3% [C1]."}]))
    assert out["decision"] == "deny" and "C1" in out["reason"]

def test_unparsed_figures_are_denied(tmp_path):
    setup(tmp_path)
    dec, st, out = verdict(tmp_path, f"Revenue was 12,345abc {B}.")
    assert dec == "deny" and st == {"12,345abc": "unparsed"} and "write it with a standard unit such as k, m, bn" in out["reason"]
    for answer in ["RELX revenue was $12,345mm in 2024.", "RELX revenue was €99.9bln.", "Operating margin rose 9.9ppts."]:
        assert verdict(tmp_path, answer)[0] == "deny", answer

def test_calcs_with_assumptions_are_allowed_and_flagged(tmp_path):
    fx = {"name": "fx", "value": "1.17", "source_id": "assumption", "reason": "ECB rate on 31 Dec"}
    T = with_calcs(tmp_path, ("rev in EUR", [REV, fx], "rev * fx", "money"))
    dec, st, out = verdict(tmp_path, "Revenue was about €11,037,780,000.00 [C1].", T)
    assert dec == "allow" and st == {"€11,037,780,000.00": "calculated_with_assumption"}
    assert "C1 uses an assumed input" in out["system_message"]

def test_large_ledger_is_checked_quickly(tmp_path):  # distinct block texts, so nothing is shared
    setup(tmp_path)
    lg = Ledger(tmp_path); d = lg.load()
    tbl = [b for b in list(d["documents"].values())[0]["blocks"] if b["type"] == "table"][0]
    for doc in range(2, 5):
        blocks = [{**tbl, "page_index": p, "n": n, "block_id": f"D{doc}:p{p + 1}:b{n}", "text": f"Block D{doc} p{p} b{n}\n" + tbl["text"]}
                  for p in range(30) for n in range(1, 16)]
        d["documents"][f"sha{doc}"] = {"doc_id": f"D{doc}", "path": "/x.pdf", "model": "m", "blocks": blocks,
                                       "pages": [{"index": p, "dimensions": {"width": 720, "height": 1018}} for p in range(30)]}
    lg.save(d)
    ans = "Figures: " + "; ".join([f"£9,434m {B}"] * 30 + [f"£{1000 + i}m {B}" for i in range(30)]) + "."
    t0 = time.time()
    out = run(transcript(tmp_path, [Q, {"role": "assistant", "content": ans}]))
    assert out["decision"] == "deny" and time.time() - t0 < 2.0

def test_logistics_answer_with_units_and_an_aside_is_allowed(tmp_path):
    add_block(tmp_path, "Bill of lading MEDU8841207. Container MSKU4417302, 1 x 40' HC. Industrial control units "
                        "(HS 8537.10). Gross weight 18,420 kg. 1,240 cartons.", "D1:p1:b2")
    add_block(tmp_path, "Commercial invoice: 1,240 units at USD 612.50. Total declared value USD 759,500.00. Incoterm CIF.", "D2:p1:b2")
    add_block(tmp_path, "Arrival notice: gross weight 19,240 kg (verified weight at terminal). Declared value USD 759,500.00. "
                        "Release is withheld where verified gross weight differs from the B/L by more than 2%.", "D3:p1:b2")
    an, bl = {"name": "an", "value": "19,240 kg", "source_id": "D3:p1:b2"}, {"name": "bl", "value": "18,420 kg", "source_id": "D1:p1:b2"}
    T = [tool_calculate("weight difference", [an, bl], "an - bl", "num", "kg", root=tmp_path),
         tool_calculate("weight difference %", [an, bl], "(an - bl) / bl", "pct", root=tmp_path)]
    assert [t.split("  ")[0] for t in T] == ["C1 = 820 kg", "C2 = 4.45%"]
    answer = ("Hold the container. The verified gross weight is 19,240 kg [D3:p1:b2] against 18,420 kg on the bill of lading "
              "[D1:p1:b2], a difference of 820 kg (4.45%) [C1][C2], above the 2% limit [D3:p1:b2]. The declared value matches: "
              "USD 759,500.00 on the invoice [D2:p1:b2] and on the arrival notice.")
    dec, st, out = verdict(tmp_path, answer, T)
    assert dec == "allow", out.get("reason")
    assert st == {"19,240 kg": "traced", "18,420 kg": "traced", "820 kg": "calculated", "4.45%": "calculated",
                  "2%": "traced", "USD 759,500.00": "traced"}


HARNESS = "this Vibe harness does not pass the transcript to hooks; run vibe --legacy-harness"

def test_a_missing_transcript_path_is_not_checked_and_says_so(tmp_path):
    setup(tmp_path)
    verdict(tmp_path, f"Revenue was £9,434m {B}.")                      # an older checked answer
    out = run({"cwd": str(tmp_path), "hook_event_name": "post_agent"})
    assert out["decision"] == "allow" and out["system_message"].startswith("traceable: NOT CHECKED: " + HARNESS)
    last = Ledger(tmp_path).load()["checks"][-1]
    assert (last["decision"], last["reason"], last["figures"]) == ("not_checked", HARNESS, [])
    html = (tmp_path / ".traceable/report.html").read_text()
    assert "NOT CHECKED" in html and "9,434" not in html                 # never a stale report
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    out = run({"cwd": str(fresh), "hook_event_name": "post_agent"})
    assert "NOT CHECKED" in out["system_message"] and not (fresh / ".traceable").exists()
    out = run({"cwd": str(tmp_path), "transcript_path": "", "hook_event_name": "post_agent"})
    assert out["decision"] == "allow" and "NOT CHECKED" in out["system_message"]
    assert Ledger(tmp_path).load()["checks"][-1]["decision"] == "not_checked"

Q2 = {"role": "user", "content": "What was RELX's 2024 effective tax rate (tax expense over profit before tax) and its 2023-2024 "
      "net profit growth?", "injected": False}
Q2_R1_FINAL = "**2024 Effective Tax Rate**: [C7]\n\n**2023-2024 Net Profit Growth**: [C8]\n\n[D1:p1:b3]"   # a past answer, allowed before the fix
TAX, PBT = {"name": "t", "value": "613", "source_id": "D1:p1:b3"}, {"name": "p", "value": "2,557", "source_id": "D1:p1:b3"}
NP24, NP23 = {"name": "a", "value": "1,944", "source_id": "D1:p1:b3"}, {"name": "b", "value": "1,788", "source_id": "D1:p1:b3"}

def q2_calcs(tmp_path):
    """C1-C6 as fillers, C7 = 24.0% (tax rate) and C8 = 8.72% (net profit growth), as in a past run."""
    return with_calcs(tmp_path, *[MARGIN] * 6, ("tax rate", [TAX, PBT], "t / p", "pct"), ("growth", [NP24, NP23], "a / b - 1", "pct"))

def test_citations_without_a_figure_are_denied(tmp_path):
    T = q2_calcs(tmp_path)
    assert T[6].startswith("C7 = 24.0%") and T[7].startswith("C8 = 8.72%")
    dec, st, out = verdict(tmp_path, Q2_R1_FINAL, T, q=Q2)
    assert dec == "deny" and st == {}
    assert "Put the figure next to its citation, for example 24.0% [C7]" in out["reason"]
    assert "for example 8.72% [C8]" in out["reason"] and "[D1:p1:b3] has no figure next to it" in out["reason"]
    table = "| Metric | Value |\n|---|---|\n| Tax rate | 24.0% [C7] |\n| Source | [D1:p1:b3] |"
    assert verdict(tmp_path, table, T, q=Q2)[0] == "deny"
    fixed = "The 2024 effective tax rate was 24.0% [C7] and 2023-2024 net profit growth was 8.72% [C8]."
    assert verdict(tmp_path, fixed, T, q=Q2)[0] == "allow"

FIN23, FIN24 = ("Finance costs change 2023 to 2024", "fc_2024 - fc_2023")

def test_a_change_cited_to_a_block_must_be_calculated(tmp_path):
    setup(tmp_path)
    # Before this fix, "increased" (wrong: costs fell 323 -> 304) passed on the 2022 JV share 19, and "decreased" was denied.
    for answer in ["Finance costs increased by £19m [D1:p1:b3].", "Finance costs decreased by £19m [D1:p1:b3].",
                   "Finance costs fell £19m [D1:p1:b3].", "Finance costs saw a reduction of 19 GBPm [D1:p1:b3].",
                   "**Finance costs change (2023 to 2024):** Decreased by **£19m** (from £323m to £304m) [D1:p1:b3]",
                   "Revenue grew 3.0% [D1:p1:b3]."]:
        dec, st, out = verdict(tmp_path, answer)
        assert dec == "deny" and "a change must be computed with traceable_calculate" in out["reason"], answer
        assert "opposite sign" not in out["reason"], answer

def test_change_direction_follows_the_calculation_for_either_sign_convention(tmp_path):
    tool_read_document(str(FIXTURES / "relx_is.pdf"), client=FakeOcr(), root=tmp_path)
    for v23, v24 in (("323", "304"), ("(323)", "(304)"), ("-323", "-304")):
        c = tool_calculate(FIN23, [{"name": "fc_2023", "value": v23, "source_id": "D1:p1:b3"},
                                   {"name": "fc_2024", "value": v24, "source_id": "D1:p1:b3"}], FIN24, "num", root=tmp_path)
        cid = c.split(" ")[0]
        for answer in [f"Finance costs decreased by £19m [{cid}].", f"Finance costs fell by 19 GBPm [{cid}].",
                       f"Finance costs changed by -£19m [{cid}].", f"Finance costs changed by £19m [{cid}].",
                       f"Change: 19,000,000 [{cid}]", f"Finance costs change: -19 GBPm [{cid}]"]:
            assert verdict(tmp_path, answer, [c])[0] == "allow", (v23, answer)
        for answer in [f"Finance costs increased by £19m [{cid}].", f"Finance costs rose by 19 GBPm [{cid}].",
                       f"Finance costs changed by $19m [{cid}]."]:
            assert verdict(tmp_path, answer, [c])[0] == "deny", (v23, answer)

FIX_Q3_R3_ATTEMPT2 = """| Metric | 2023 | 2024 | Change |
|--------|------|------|--------|
| **Basic earnings per share** | 94.1p [D1:p1:b3] | 103.6p [D1:p1:b3] | +10.1% [C2] |
| **Finance costs** | (323) GBPm [D1:p1:b3] | (304) GBPm [D1:p1:b3] | \u221219 GBPm [C1] |

**Summary:**
- Basic EPS increased from 94.1p [D1:p1:b3] in 2023 to 103.6p [D1:p1:b3] in 2024, representing 10.1% [C2] growth
- Finance costs decreased from (323) GBPm [D1:p1:b3] to (304) GBPm [D1:p1:b3], a reduction of 19 GBPm [C1]"""

def test_an_eps_and_finance_cost_answer_now_passes(tmp_path):  # seen in a run: denied twice, then the turn limit
    tool_read_document(str(FIXTURES / "relx_is.pdf"), client=FakeOcr(), root=tmp_path)
    T = [tool_calculate(FIN23, [{"name": "finance_costs_2023", "value": "323", "source_id": "D1:p1:b3"},
                                {"name": "finance_costs_2024", "value": "304", "source_id": "D1:p1:b3"}],
                        "finance_costs_2024 - finance_costs_2023", "num", root=tmp_path),
         tool_calculate("EPS growth 2023 to 2024", [{"name": "eps_2023", "value": "94.1", "source_id": "D1:p1:b3"},
                                                     {"name": "eps_2024", "value": "103.6", "source_id": "D1:p1:b3"}],
                        "(eps_2024 - eps_2023) / eps_2023", "pct", root=tmp_path)]
    assert [t.split("  ")[0] for t in T] == ["C1 = -£19m", "C2 = 10.1%"]
    q = {"role": "user", "content": "What were RELX's basic earnings per share in 2023 and 2024, the EPS growth between them, "
         "and how did finance costs change from 2023 to 2024?", "injected": False}
    dec, st, out = verdict(tmp_path, FIX_Q3_R3_ATTEMPT2, T, q=q)
    assert dec == "allow", out.get("reason")
    assert st["-19 GBPm"] == "calculated" and st["19 GBPm"] == "calculated" and st["(323) GBPm"] == "traced"

Q2_R2_ATTEMPT1 = ("Based on the consolidated income statement data:\n\n**2024 Effective Tax Rate**: 613 / 2,557 = **24.0%** [D1:p1:b3]\n\n"
                  "**2023-2024 Net Profit Growth**: (1,944 - 1,788) / 1,788 = **8.7%** [D1:p1:b3]")

def test_percentages_cited_to_a_block_point_to_the_calculator(tmp_path):
    setup(tmp_path)
    dec, st, out = verdict(tmp_path, Q2_R2_ATTEMPT1, q=Q2)
    assert dec == "deny" and st["24.0%"] == st["8.7%"] == "derived_needs_calc" and st["2,557"] == "traced"
    assert "'24.0%' is a derived figure: compute it with traceable_calculate and cite its [Cn]" in out["reason"]
    dec, st, out = verdict(tmp_path, "Tax expense over profit before tax: 613 / 2,557 = 24.0% [D1:p1:b3].", q=Q2)
    assert dec == "deny" and "'24.0%' is not in D1:p1:b3; if you computed it, use traceable_calculate" in out["reason"]

def test_percent_flag_mismatch_explains_the_fix(tmp_path):
    T = with_calcs(tmp_path, ("rate x 100", [TAX, PBT, {"name": "k", "value": "100", "source_id": "assumption", "reason": "x"}],
                              "t / p * k", "num"), ("ratio", [OP, REV], "op / rev", "num"))
    assert [t.split("  ")[0] for t in T] == ["C1 = 24.0", "C2 = 0.303"]
    out = verdict(tmp_path, "The effective tax rate was 24.0% [C1].", T, q=Q2)[2]
    assert "'24.0%' is a percentage but C1 = 24.0 is not one: compute the ratio with format 'pct'" in out["reason"]
    out = verdict(tmp_path, "Operating margin was 0.303% [C2].", T)[2]
    assert "is a percentage but C2 = 0.303 is not one" in out["reason"]

Q2_R3_ATTEMPT1 = """**RELX 2024 effective tax rate:** 23.97% [C1]

**RELX 2023\u20132024 net profit growth:** 8.73%

---

[C1] 2024 tax expense 613 GBPm and profit before tax 2,557 GBPm [D1:p1:b3]; 613/2557\u00d7100 = 23.97%

Net profit: 2023 = 1,788 GBPm, 2024 = 1,944 GBPm [D1:p1:b3]; (1944\u20131788)/1788\u00d7100 = 8.73%"""

def test_formula_tokens_are_not_figures(tmp_path):
    c = setup(tmp_path)
    dec, st, out = verdict(tmp_path, Q2_R3_ATTEMPT1, [c], q=Q2)
    assert dec == "deny"
    assert not [r for r in st if r.endswith("\u00d7") or r == "100"], st
    assert "'2557\u00d7'" not in out["reason"] and "'100'" not in out["reason"]

def test_constant_inputs_are_not_assumptions(tmp_path):  # seen in a run: 5.02% was "calculated with an assumed input"
    n = {"name": "n_years", "value": "2", "source_id": "assumption", "reason": "2-year period from 2022 to 2024"}
    T = with_calcs(tmp_path, ("cagr", [{**REV, "name": "r24"}, {**REV, "name": "r22", "value": "8,553"}, n],
                              "(r24 / r22) ** (1 / n_years) - 1", "pct"))
    dec, st, out = verdict(tmp_path, "The 2022-2024 revenue CAGR was 5.02% [C1].", T)
    assert dec == "allow" and st == {"5.02%": "calculated"} and "assumed" not in out["system_message"]


from traceable.check import run_hook

D2 = "D2:p1:b7"

def read_both(tmp_path):
    tool_read_document(str(FIXTURES / "relx_is.pdf"), client=FakeOcr(), root=tmp_path)
    tool_read_document(str(FIXTURES / "diageo_is.pdf"), client=FakeOcr("diageo_is.json"), root=tmp_path)

def test_period_phrases_do_not_pin_a_year(tmp_path):
    T = with_calcs(tmp_path, MARGIN, MARGIN, MARGIN, MARGIN, GROWTH)
    assert T[4].startswith("C5 = 2.98%")
    for answer in ["Revenue grew 2.98% [C5] from 2023 to 2024, from £9,161m [D1:p1:b3] to £9,434m [D1:p1:b3]",
                   "Revenue grew 2.98% [C5] between 2023 and 2024, from £9,161m [D1:p1:b3] to £9,434m [D1:p1:b3].",
                   "From FY2023 to FY2024 revenue rose from £9,161m [D1:p1:b3] to £9,434m [D1:p1:b3].",
                   "Over 2023 - 2024 revenue went from £9,161m to £9,434m [D1:p1:b3].",
                   "2023 to 2024: £9,161m to £9,434m [D1:p1:b3]."]:
        dec, st, out = verdict(tmp_path, answer, T)
        assert dec == "allow", (answer, out.get("reason"))
    for answer in [f"Revenue in 2024 was £9,161m {B}.", f"Revenue grew from 2023 to 2024, reaching £9,161m in 2024 {B}."]:
        assert verdict(tmp_path, answer, T)[0] == "deny", answer

def test_the_diageo_period_false_alarm_is_gone(tmp_path):  # seen in a run: "not in its 2024 column"
    read_both(tmp_path)
    c = tool_calculate("Diageo net sales growth FY2023 to FY2024", [{"name": "a", "value": "20,269", "source_id": D2},
                       {"name": "b", "value": "20,555", "source_id": D2}], "a / b - 1", "pct", root=tmp_path)
    answer = ("Diageo's net sales declined by 1.39% [C1] from FY2023 to FY2024, from $20,555 million [D2:p1:b7] "
              "to $20,269 million [D2:p1:b7].")
    dec, st, out = verdict(tmp_path, answer, [c])
    assert dec == "allow" and st == {"1.39%": "calculated", "$20,555 million": "traced", "$20,269 million": "traced"}

def test_per_share_changes_are_checked_in_their_unit(tmp_path):  # seen in a run: "fell by $23.1m [C6]" passed
    read_both(tmp_path)
    fill = lambda: tool_calculate("margin", [OP, REV], "op / rev", "pct", root=tmp_path)
    T = [fill() for _ in range(5)]
    T.append(tool_calculate("Diageo basic EPS change", [{"name": "a", "value": "173.2", "source_id": D2},
                            {"name": "b", "value": "196.3", "source_id": D2}], "a - b", root=tmp_path))
    T.append(fill())
    T.append(tool_calculate("RELX basic EPS change", [{"name": "a", "value": "103.6p", "source_id": "D1:p1:b3"},
                            {"name": "b", "value": "94.1p", "source_id": "D1:p1:b3"}], "a - b", root=tmp_path))
    assert T[5].startswith("C6 = -23.1c  ") and T[7].startswith("C8 = 9.50p  ")
    for answer in ["EPS rose by 9.5p [C8].", "Basic EPS fell by 23.1c [C6].", "Basic EPS fell by 23.1 cents [C6].",
                   "Basic EPS was 173.2c [D2:p1:b7], down from 196.3 cents [D2:p1:b7]: a decline of 23.1c [C6].",
                   "Basic EPS was 173.2 [D2:p1:b7] against 196.3 [D2:p1:b7], a change of -23.1c [C6]."]:
        dec, st, out = verdict(tmp_path, answer, T)
        assert dec == "allow", (answer, out.get("reason"))
    for answer, why in [("Basic EPS fell by $23.1m [C6].", "'$23.1m' is an amount in $ but C6 = -23.1c is in cents: write it as -23.1c"),
                        ("Basic EPS fell by 23.1p [C6].", "'23.1p' is in pence but C6 = -23.1c is in cents: write it as -23.1c"),
                        ("Basic EPS was 173.2 pence [D2:p1:b7].", "'173.2 pence' is in pence but D2:p1:b7 shows 173.2 in cents: write 173.2c")]:
        dec, st, out = verdict(tmp_path, answer, T)
        assert dec == "deny" and why in out["reason"], (answer, out.get("reason"))

def test_scale_and_currency_denials_name_the_difference(tmp_path):  # seen in a run: both ended at the turn limit
    read_both(tmp_path)
    dec, _, out = verdict(tmp_path, "Diageo's FY2024 net sales were £20.269 billion [D2:p1:b7].")
    assert dec == "deny" and "1. '£20.269 billion' has currency £ but D2:p1:b7 is in $\n" in out["reason"]
    dec, _, out = verdict(tmp_path, "Diageo's FY2024 net sales were $20.269 [D2:p1:b7] billion.")
    assert "'$20.269' is missing its scale: the table is in $ million, so write $20,269m or $20.3bn" in out["reason"]
    assert "is not in" not in out["reason"]
    for answer in ["Diageo's FY2024 net sales were $20.3bn [D2:p1:b7] and operating profit $6.0bn [D2:p1:b7].",
                   "Net sales were $20,269m [D2:p1:b7], or 20.3 billion dollars [D2:p1:b7]."]:
        assert verdict(tmp_path, answer)[0] == "allow", answer

def test_hook_failures_are_not_checked_and_never_leave_a_stale_report(tmp_path):
    setup(tmp_path)
    report = tmp_path / ".traceable/report.html"
    good = [Q, {"role": "assistant", "content": f"Revenue was £9,434m {B}."}]
    def checked():
        assert run(transcript(tmp_path, good))["decision"] == "allow" and "9,434" in report.read_text()
    checked()
    out = run_hook("not json {", str(tmp_path))
    assert out == {"decision": "allow", "system_message": "traceable: NOT CHECKED: the hook input was not valid JSON · "
                                                           f"report: {report.resolve()}"}
    assert "NOT CHECKED" in report.read_text() and "9,434" not in report.read_text()
    checked()
    p = tmp_path / "messages.jsonl"
    p.write_text(p.read_text() + "\n{broken")
    out = run({"cwd": str(tmp_path), "transcript_path": str(p), "hook_event_name": "post_agent"})
    assert "NOT CHECKED: the transcript could not be read" in out["system_message"] and "9,434" not in report.read_text()
    checked()
    (tmp_path / ".traceable/ledger.json").write_text("{ corrupt")
    out = run_hook(json.dumps(transcript(tmp_path, good)), "/somewhere/else")
    assert out["decision"] == "allow" and "NOT CHECKED: the ledger" in out["system_message"]
    assert "NOT CHECKED" in report.read_text() and "9,434" not in report.read_text()

# --- Dates and direction words ---

def test_abbreviated_dates_are_not_figures(tmp_path):  # seen in runs: "31 Dec" and "30 Jun"
    read_both(tmp_path)
    for answer in [f"RELX's revenue for the year to 31 Dec 2024 was £9,434m {B}.",
                   f"For the year ended 31 Dec. 2024, RELX's revenue was £9,434m {B}.",
                   "Diageo's net sales for the year ended 30 Jun 2024 were $20,269m [D2:p1:b7].",
                   "As at Jun 30, 2024 Diageo's net sales were $20,269m [D2:p1:b7]."]:
        dec, st, out = verdict(tmp_path, answer)
        assert dec == "allow", (answer, out.get("reason"))

def test_direction_words_on_level_percentages_are_not_denied(tmp_path):
    read_both(tmp_path)
    dg = lambda name, v: {"name": name, "value": v, "source_id": D2}
    T = [tool_calculate("RELX margin 2024", [OP, REV], "op / rev", "pct", root=tmp_path),
         tool_calculate("Diageo margin FY2024", [dg("op", "6,001"), dg("ns", "20,269")], "op / ns", "pct", root=tmp_path),
         tool_calculate("Diageo margin FY2023", [dg("op", "5,547"), dg("ns", "20,555")], "op / ns", "pct", root=tmp_path),
         tool_calculate("Diageo margin FY2022", [dg("op", "5,897"), dg("ns", "20,516")], "op / ns", "pct", root=tmp_path),
         tool_calculate("Diageo net sales growth", [dg("a", "20,269"), dg("b", "20,555")], "a / b - 1", "pct", root=tmp_path)]
    assert [t.split("  ")[0] for t in T] == ["C1 = 30.3%", "C2 = 29.6%", "C3 = 27.0%", "C4 = 28.7%", "C5 = -1.39%"]
    add_block(tmp_path, "Adjusted operating margin 34.1%", "D3:p1:b1")
    for answer in ["RELX had the higher margin at 30.3% [C1]; Diageo's was lower at 29.6% [C2].",
                   "Diageo's operating margin fell to 27.0% [C3] in FY2023, down from 28.7% [C4] in FY2022.",
                   "Diageo's margin decreased from 28.7% [C4] to 27.0% [C3] between FY2022 and FY2023.",
                   "RELX's adjusted operating margin was higher at 34.1% [D3:p1:b1].",
                   "Diageo's net sales fell 1.39% [C5].", "Diageo's net sales were lower by 1.39% [C5]."]:
        dec, st, out = verdict(tmp_path, answer, T)
        assert dec == "allow", (answer, out.get("reason"))
    for answer in ["Diageo's net sales grew 1.39% [C5].", "Diageo's net sales grew at 1.39% [C5] a year.",
                   "Diageo's net sales rose a modest 1.39% [C5].", "Diageo's net sales were higher by 1.39% [C5]."]:
        dec, st, out = verdict(tmp_path, answer, T)
        assert dec == "deny" and "opposite sign or direction to C5 = -1.39%" in out["reason"], (answer, out.get("reason"))



def test_a_turn_with_no_answer_says_so(tmp_path):
    setup(tmp_path)
    out = run(transcript(tmp_path, [Q, {"role": "assistant", "content": ""}]))
    assert out["decision"] == "allow" and "NOT CHECKED: no answer in this turn" in out["system_message"]
    assert "no question found" not in out["system_message"]

def test_one_year_in_a_clause_pins_every_figure_that_has_none(tmp_path):  # wrong year >15 chars away
    setup(tmp_path)
    for answer in [f"RELX's 2024 operating profit was £2,682m {B}.",
                   f"In 2024 RELX had revenue of £9,434m and net profit of £1,788m {B}."]:
        assert verdict(tmp_path, answer)[0] == "deny", answer
    for answer in [f"RELX's 2024 operating profit was £2,861m {B}.",
                   f"In 2024, revenue was £9,434m, compared with £9,161m the year before {B}.",
                   f"2024 revenue was £9,434m, up from £9,161m {B}."]:
        assert verdict(tmp_path, answer)[0] == "allow", answer

def test_a_quote_block_states_its_table_unit(tmp_path):  # "$14,514 million" was denied
    from test_sources import NVDA
    from traceable.sources import _header
    lines = NVDA.splitlines()
    setup(tmp_path)
    add_block(tmp_path, "\n".join(_header({"type": "table", "text": NVDA}) + [lines[3]]), "D2:p1:c1")
    assert verdict(tmp_path, "Data Center revenue was $14,514 million [D2:p1:c1].")[0] == "allow"
    assert verdict(tmp_path, "Data Center revenue was $14,514 [D2:p1:c1].")[0] == "deny"

def test_a_figure_free_reply_after_a_denial_says_so(tmp_path):  # the denied draft stays on screen
    setup(tmp_path)
    assert verdict(tmp_path, f"Revenue in 2024 was £9,161m {B}.")[0] == "deny"
    dec, _, out = verdict(tmp_path, "I could not verify the revenue figure, please see my previous message.")
    assert dec == "allow" and "earlier draft" in out.get("system_message", "")

def test_the_denial_asks_for_an_answer_without_talk_of_the_check(tmp_path):  # "All figures pass now; ..."
    setup(tmp_path)
    out = verdict(tmp_path, f"Revenue in 2024 was £9,161m {B}.")[2]
    assert "do not mention this check" in out["reason"]

def test_a_denied_draft_report_says_so_and_refreshes(tmp_path):  # a tab opened mid-retry showed a denied draft
    setup(tmp_path)
    verdict(tmp_path, f"Revenue in 2024 was £9,161m {B}.")
    page = (tmp_path / ".traceable" / "report.html").read_text()
    assert "DENIED DRAFT" in page and 'http-equiv="refresh"' in page
    verdict(tmp_path, f"Revenue in 2024 was £9,434m {B}.")
    page = (tmp_path / ".traceable" / "report.html").read_text()
    assert "DENIED DRAFT" not in page and 'http-equiv="refresh"' not in page

def test_numbers_inside_file_names_are_not_figures():  # '26' from operating_plan_fy24_26.xlsx
    from traceable.numbers import extract
    raws = [f.raw.strip() for f in extract("In operating_plan_fy24_26.xlsx: the margin was 66.8%, see relx_2024.pdf.")]
    assert raws == ["66.8%"]

def test_a_report_crash_never_changes_the_verdict(tmp_path, monkeypatch):  # a report bug turned a deny into allow
    import traceable.report as rp
    setup(tmp_path)
    monkeypatch.setattr(rp, "write_report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("report bug")))
    dec, _, out = verdict(tmp_path, f"Revenue in 2024 was £9,161m {B}.")
    assert dec == "deny" and "not in its 2024 column" in out["reason"]
    dec, _, out = verdict(tmp_path, f"Revenue in 2024 was £9,434m {B}.")
    assert dec == "allow" and "report could not be written" in out.get("system_message", "")

def test_a_table_takes_its_unit_from_the_heading_above_it(tmp_path):  # Affirm FQ3'24 letter: "(in thousands, ...)"
    from test_numbers import STATEMENT, HEADING
    setup(tmp_path)
    add_block(tmp_path, STATEMENT, "D2:p1:b2")
    lg = Ledger(tmp_path); d = lg.load()
    d["documents"]["extra-D2:p1:b2"]["blocks"][0].update(type="table", caption=HEADING)
    lg.save(d)
    assert verdict(tmp_path, "Cash and cash equivalents were $1,272.8m [D2:p1:b2].")[0] == "allow"
    assert verdict(tmp_path, "Cash and cash equivalents were $1,272,760m [D2:p1:b2].")[0] == "deny"

def test_reading_a_pdf_records_the_heading_of_each_table():
    from traceable.numbers import table_captions
    from test_numbers import STATEMENT, HEADING
    import traceable.ocr as ocr
    assert hasattr(ocr, "table_captions") and table_captions([{"type": "title", "text": HEADING},
                                                             {"type": "table", "text": STATEMENT}])[1] == HEADING
