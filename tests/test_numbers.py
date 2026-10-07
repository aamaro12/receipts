# tests/test_numbers.py
import json
from decimal import Decimal as D
from conftest import FIXTURES
from traceable.numbers import (normalise, extract, block_scale, match_in_block, close_enough, close_to_display,
                               parse_block, currency_code, DERIVED_WORDS)

RELX_TABLE = [b for b in json.load(open(FIXTURES / "relx_is.json"))["pages"][0]["blocks"] if b["type"] == "table"][0]["content"]

def vals(text):
    return [(f.raw.strip(), f.base, f.is_percent) for f in extract(text)]

def test_normalise_strips_markdown_and_latex():
    assert normalise("**9,434** and 12.5 \\% and $2 \\times$ x${ }^{1}$") == "9,434 and 12.5 % and 2 x x"

def test_money_with_scale_and_currency():
    f = extract("Revenue was £9,434m in 2024")[0]
    assert (f.value, f.scale, f.currency) == (D("9434"), 6, "£") and f.base == D("9434000000")

def test_uppercase_suffixes_and_codes():
    assert [f.base for f in extract("$1.2B and EUR12.5M")] == [D("1200000000"), D("12500000")]

def test_units_percent_multiple_pence_weight():
    got = vals("30.3% margin, 12.5x leverage, 103.6p EPS, 18,420 kg gross")
    assert got == [("30.3%", D("30.3"), True), ("12.5x", D("12.5"), False), ("103.6p", D("103.6"), False), ("18,420 kg", D("18420"), False)]

def test_parentheses_negative_and_direction_words():
    assert extract("Cost of sales (3,300)")[0].neg is True
    assert extract("revenue declined 5%")[0].direction == -1
    assert extract("revenue rose 5%")[0].direction == 1

def test_ranges_become_two_figures():
    assert [f.base for f in extract("guidance of 10-12%")] == [D("10"), D("12")]

def test_exemptions():
    text = ("In 2024 and 2020-2023, on 31 December 2024, the 3-year plan and 24-month runway; "
            "container MSKU4417302, Q3, FY2023, 10-K, B/L 123456789, HS 8471.30, over 3 years")
    assert extract(text) == []

def test_small_int_with_measure_word_is_not_exempt():
    assert [f.base for f in extract("margin rose 3 points")] == [D("3")]

def test_hs_code_exempt_only_near_hs_keyword():
    assert [f.base for f in extract("paid USD 1250.00 in fees")] == [D("1250.00")]

def test_exempt_numbers_from_user_message():
    assert extract("growth above 5% target", exempt_numbers={D("5")}) == []

def test_block_scale_from_header():
    assert block_scale(RELX_TABLE) == 6
    assert block_scale("Revenue (€m) 1,380") == 6
    assert block_scale("in thousands of euros") == 3
    assert block_scale("Gross weight 18,420 kg") == 0

def test_match_scale_via_header():
    f = extract("£9,434m")[0]
    m = match_in_block(f, RELX_TABLE)
    assert m is not None and m.value == D("9434")

def test_wrong_scale_does_not_match():
    assert match_in_block(extract("€1.38m")[0], "Revenue (€m) | 1,380") is None

def test_rounding_tolerance_capped_at_two_percent():
    assert match_in_block(extract("£9.4bn")[0], RELX_TABLE) is not None
    assert match_in_block(extract("£8bn")[0], RELX_TABLE) is None  # £9bn would legitimately match 2023 revenue 9,161 within 2%

def test_percent_only_matches_percent():
    assert match_in_block(extract("19%")[0], "Share of results | 19 | 46") is None
    assert match_in_block(extract("19%")[0], "margin was 19%") is not None

def test_direction_words_only_mark_change_figures():
    # Direction is checked in check.py and only for change figures; match_in_block ignores it.
    assert extract("costs declined by 3,300")[0].direction == -1
    assert extract("costs declined 3,300")[0].direction == -1       # a verb right before the amount: a change
    assert extract("costs declined to 3,300")[0].direction == 0     # a level claim: not a change figure
    assert extract("a loss of £6m")[0].direction == 0
    assert extract("profit or loss rose 5%")[0].direction == 1
    assert extract("income/(loss) of 5%")[0].direction == 0          # IFRS captions are not direction words
    assert match_in_block(extract("costs declined by 3,300")[0], "Cost of sales | 3,300") is not None
    assert match_in_block(extract("a loss of £6m")[0], "Deferred tax (GBPm) | 53 | 68 | (6)") is not None

def test_close_enough_for_calc_results():
    f = extract("30.3%")[0]
    assert close_enough(f, D("30.327")) and not close_enough(f, D("31.0"))

def test_real_answer_variants_match_relx_block():
    for s in ["£9,434m", "£9.4bn", "GBP 9,434 million", "9,434", "£9,434 million", "£2,861m"]:
        assert match_in_block(extract(s)[0], RELX_TABLE) is not None, s


def test_scale_words_mm_mln_bln_tn():
    got = [(f.raw, f.base) for f in extract("$12,345mm; €12.3bln; €12,345mln; $1.2tn; 2trn; 3 trillion")]
    assert got == [("$12,345mm", D("12345000000")), ("€12.3bln", D("12300000000")), ("€12,345mln", D("12345000000")),
                   ("$1.2tn", D("1200000000000")), ("2trn", D("2000000000000")), ("3 trillion", D("3000000000000"))]

def test_percentage_point_and_basis_point_units():
    figs = extract("up 1.3ppts, 1.3ppt, 2.0pp, 5 pts and 1.3 percentage points; spread 50bp, 25bps")
    assert [(f.value, f.unit, f.is_percent) for f in figs] == [
        (D("1.3"), "pp", True), (D("1.3"), "pp", True), (D("2.0"), "pp", True), (D("5"), "pp", True),
        (D("1.3"), "pp", True), (D("50"), "bps", True), (D("25"), "bps", True)]
    assert figs[5].base == D("0.5")                 # basis points compare as percent points

def test_per_cent_percent_and_pence():
    got = [(f.value, f.unit, f.is_percent) for f in extract("45.6 per cent, 30.3 percent and EPS of 99.9 pence")]
    assert got == [(D("45.6"), "%", True), (D("30.3"), "%", True), (D("99.9"), "p", False)]

def test_superscripts_stripped_and_cubic_metres_kept():
    assert normalise("Revenue £12,345m¹ and 12.5m³") == "Revenue £12,345m and 12.5m3"
    assert [(f.base, f.unit) for f in extract("Revenue £12,345m¹ and volume 12.5m³")] == [(D("12345000000"), None), (D("12.5"), "m3")]

def test_leading_minus_is_negative_but_not_a_bullet_or_range():
    figs = extract("- Operating profit was -£2,861m; margin −0.2%; guidance 10-12%")
    assert [(f.raw, f.neg) for f in figs] == [("-£2,861m", True), ("-0.2%", True), ("10", False), ("12%", False)]

def test_parenthetical_after_a_figure_is_an_aside_not_a_negative():  # keeps "820 kg (4.45%)" valid with sign checks
    figs = extract("a difference of 820 kg (4.45%), and operating profit was (£2,861m)")
    assert [(f.raw, f.neg) for f in figs] == [("820 kg", False), ("4.45%", False), ("(£2,861m)", True)]
    assert extract("Cost of sales (3,045) (3,216)", apply_exemptions=False)[1].neg    # blocks keep accounting negatives

def test_dollar_amounts_keep_their_currency():  # the LaTeX $...$ rule no longer eats two dollar amounts
    assert [f.currency for f in extract("costs $5bn and revenue $9bn")] == ["$", "$"]

def test_trailing_letters():
    figs = extract("revenue 12,345GBP and 9.4bn USD; a 3D printer, 5G and 4K screens; a 40ft box")
    assert [(f.raw, f.currency, f.unparsed) for f in figs] == [
        ("12,345GBP", "GBP", False), ("9.4bn USD", "USD", False), ("40ft", None, True)]

def test_letters_before_digits_stay_identifiers():
    assert extract("Q3 FY2023 MSKU4417302 COVID-19 1H24 FY2023-24 2025E revenue") == []

def test_new_masks():
    text = ("Revenue (2024) and (FY2024) and (2023/24); on 31/12/2024; see page 140, p. 12, pp. 4-5 and Note 15; "
            "margin = 2,861 / 9,434 × 100, or x 100, or * 100; at 4pm")
    assert [f.raw for f in extract(text)] == ["2,861", "9,434"]

def test_ranges_share_scale_and_currency():
    assert [(f.base, f.currency) for f in extract("in the £9.2-9.4bn range")] == [(D("9200000000"), "£"), (D("9400000000"), "£")]

def test_parse_block_relx_table():
    pb = parse_block(RELX_TABLE)
    assert (pb.scale, pb.currency, pb.note_col) == (6, "GBP", 1)
    assert pb.col_years == {2: 2022, 3: 2023, 4: 2024}
    assert not any(f.in_header or f.col == 1 for f in pb.candidates)            # header row and Note column
    assert not {"2022", "2023", "2024"} & {f.raw for f in pb.candidates}        # the EPS sub-header years too
    rev = next(f for f in pb.candidates if f.raw == "9,434")
    assert pb.text[rev.start:rev.end] == "9,434" and rev.col == 4
    eps = next(f for f in pb.candidates if f.raw == "103.6p")
    assert eps.unit == "p" and eps.scale == 0

def test_normalise_is_idempotent_on_ocr_blocks():  # stored block offsets stay valid
    for p in json.load(open(FIXTURES / "relx_is.json"))["pages"]:
        for b in p["blocks"]:
            once = normalise(b["content"])
            assert normalise(once) == once

def test_units_must_agree():
    assert match_in_block(extract("103.6p")[0], RELX_TABLE) is not None
    assert match_in_block(extract("103.6 pence")[0], RELX_TABLE) is not None
    assert match_in_block(extract("£103.6")[0], RELX_TABLE) is None        # pounds, not pence
    assert match_in_block(extract("£103.6m")[0], RELX_TABLE) is None       # EPS never takes the GBPm header scale

def test_currency_must_agree():
    assert match_in_block(extract("$9,434m")[0], RELX_TABLE) is None
    assert match_in_block(extract("9,434m USD")[0], RELX_TABLE) is None
    assert match_in_block(extract("GBP 9,434m")[0], RELX_TABLE) is not None

def test_negative_figure_needs_a_negative_block_value():
    assert match_in_block(extract("-£2,861m")[0], RELX_TABLE) is None
    assert match_in_block(extract("(£2,861m)")[0], RELX_TABLE) is None
    assert match_in_block(extract("(£3,300m)")[0], RELX_TABLE) is not None
    assert match_in_block(extract("£3,300m")[0], RELX_TABLE) is not None    # costs shown as (3,300) may be written positive

def test_currency_without_scale_compares_with_the_scaled_block_value():
    assert match_in_block(extract("£9,434")[0], RELX_TABLE) is None          # nine thousand pounds
    assert match_in_block(extract("£9,434,000,000")[0], RELX_TABLE) is not None
    assert match_in_block(extract("9,434")[0], RELX_TABLE) is not None       # no currency, no scale: face value

def test_year_hint_selects_the_column():
    f = extract("£9,161m")[0]
    assert match_in_block(f, RELX_TABLE, year=2023) is not None
    assert match_in_block(f, RELX_TABLE, year=2024) is None
    assert match_in_block(f, RELX_TABLE, year=2019) is not None             # no such column: the hint is ignored

def test_header_and_note_cells_never_match():
    assert match_in_block(extract("£3m")[0], RELX_TABLE) is None             # only in the Note column ("2, 3")
    assert match_in_block(extract("£2,024m")[0], RELX_TABLE) is None         # a header year

def test_percentage_and_basis_points_match_percent():
    assert match_in_block(extract("50bps")[0], "spread widened 0.5%") is not None
    assert match_in_block(extract("3.0bps")[0], "growth 3.0%") is None
    assert match_in_block(extract("1.3pp")[0], "margin up 1.3%") is not None

def test_close_to_display_for_calc_citations():
    assert close_to_display(extract("-0.2%")[0], D("-0.2162")) and close_to_display(extract("0.5%")[0], D("0.4558"))
    assert close_to_display(extract("c.30%")[0], D("30.327"))
    assert not close_to_display(extract("0.3%")[0], D("0.4"))
    assert not close_to_display(extract("0%")[0], D("0.4"))                  # within half a unit, but 100% off


def test_currency_code_with_scale_after_the_number():  # seen in a run: "-19 GBPm", "(323) GBPm"
    got = [(f.raw, f.value, currency_code(f.currency), f.scale, f.neg)
           for f in extract("-19 GBPm, 19GBPm, GBP 19m, (323) GBPm and 9.4bn USD")]
    assert got == [("-19 GBPm", D("19"), "GBP", 6, True), ("19GBPm", D("19"), "GBP", 6, False),
                   ("GBP 19m", D("19"), "GBP", 6, False), ("(323) GBPm", D("323"), "GBP", 6, True),
                   ("9.4bn USD", D("9.4"), "USD", 9, False)]
    assert extract("| Metric | 2023 GBPm | 2024 GBPm |") == []          # column labels in an answer table
    assert match_in_block(extract("(304) GBPm")[0], RELX_TABLE, year=2024) is not None

def test_change_words_mark_explicit_changes():  # seen in a run: "an increase of", "a reduction of"
    figs = extract("rose by £19m, fell £19m, a reduction of 19 GBPm, an increase of 3%, rose to £304m, the 2% limit")
    assert [(f.raw, f.explicit_change, f.change) for f in figs] == [
        ("£19m", True, True), ("£19m", True, True), ("19 GBPm", True, True), ("3%", True, True),
        ("£304m", False, False), ("2%", False, True)]
    assert extract("a reduction of 19 GBPm")[0].direction == -1
    assert not extract("Change in provisions £55m")[0].explicit_change   # a line item, not a change

def test_multiplication_tokens_in_formulas_are_masked():  # seen in a run: "613/2557×100 = 23.97%"
    assert [f.raw for f in extract("613/2557×100 = 23.97%")] == ["613", "2557", "23.97%"]
    assert [f.raw for f in extract("(1944–1788)/1788×100 = 8.73%")] == ["1788", "1788", "8.73%"]
    assert [f.raw for f in extract("2,861 * 100 / 9,434, 2,861x100/9,434 and 2,861 × 1.17")] == [
        "2,861", "9,434", "2,861", "9,434", "2,861", "1.17"]
    assert [(f.raw, f.unit) for f in extract("cover of 12.5x, 9.60× (2023: 11.1×)")] == [
        ("12.5x", "x"), ("9.60×", "x"), ("11.1×", "x")]

def test_rate_is_a_derived_word():
    assert "rate" in DERIVED_WORDS


from traceable.numbers import explain_miss

DIAGEO = [b for b in json.load(open(FIXTURES / "diageo_is.json"))["pages"][0]["blocks"] if b["type"] == "table"][0]["content"]

def test_cents_are_a_unit_like_pence():  # seen in a run: Diageo reports EPS in US cents
    got = [(f.raw, f.value, f.unit) for f in extract("EPS fell 23.1c, or 23.1 cents, from 196.3¢")]
    assert got == [("23.1c", D("23.1"), "c"), ("23.1 cents", D("23.1"), "c"), ("196.3¢", D("196.3"), "c")]
    assert [(f.raw, f.unit) for f in extract("45.6 per cent, c.30% of sales")] == [("45.6 per cent", "%"), ("30%", "%")]

def test_unit_rows_set_the_unit_of_the_rows_below():
    pb = parse_block(DIAGEO)
    assert (pb.scale, pb.currency, pb.col_years, pb.note_col) == (6, "USD", {2: 2024, 3: 2023, 4: 2022}, 1)
    eps = next(f for f in pb.candidates if f.raw == "173.2")                 # below the "| | | cents | cents | cents |" row
    assert (eps.unit, eps.scale, eps.col) == ("c", 0, 2)
    shares = next(f for f in pb.candidates if f.raw == "2,234")              # below "| Weighted average ... | | million |"
    assert (shares.unit, shares.ctx_scale, shares.ctx_currency) == (None, 6, "")
    sales = next(f for f in pb.candidates if f.raw == "20,269")              # above both unit rows: the $ million header
    assert (sales.unit, sales.ctx_scale) == (None, None)
    for s in ("173.2c", "173.2 cents", "173.2", "2,234 million", "$20,269m", "$20.3bn", "20,269"):
        assert match_in_block(extract(s)[0], pb) is not None, s
    for s in ("$173.2m", "173.2p", "173.2 pence", "$2,234m", "20,269c"):
        assert match_in_block(extract(s)[0], pb) is None, s

def test_row_labels_of_block_figures():  # each calculator input shows the row it came from
    rel, dia = parse_block(RELX_TABLE), parse_block(DIAGEO)
    assert next(f for f in rel.candidates if f.raw == "2,861").label == "Operating profit"
    assert next(f for f in rel.candidates if f.raw == "103.6p").label == "Basic earnings per share"
    assert next(f for f in dia.candidates if f.raw == "20,269").label == "Net sales"
    assert next(f for f in dia.candidates if f.raw == "2,239").label == ""          # a total row with no label
    text = parse_block("Bill of lading MEDU8841207. Gross weight 18,420 kg (verified). Total declared value USD 759,500.00.")
    assert [f.label for f in text.candidates] == ["Gross weight", "Total declared value"]

def test_misses_name_the_real_difference():  # seen in a run: all denied as "is not in" before
    def why(s, block=DIAGEO, cid="D2:p1:b7"):
        return explain_miss(extract(s)[0], parse_block(block), cid)
    assert why("£20.269 billion") == "has currency £ but D2:p1:b7 is in $"
    assert why("$20.269") == "is missing its scale: the table is in $ million, so write $20,269m or $20.3bn"
    assert why("20.269") == "is missing its scale: the table is in $ million, so write $20,269m or $20.3bn"
    assert why("$20.269m") == "has the wrong scale: the table is in $ million, so write $20,269m or $20.3bn"
    assert why("$6.001 million") == "has the wrong scale: the table is in $ million, so write $6,001m or $6.00bn"
    assert why("£20.269") == ("has currency £ but D2:p1:b7 is in $, and is missing its scale: the table is in $ million, "
                              "so write $20,269m or $20.3bn")
    assert why("173.2 pence") == "is in pence but D2:p1:b7 shows 173.2 in cents: write 173.2c"
    assert why("$173.2m") == "is an amount in $ but D2:p1:b7 shows 173.2 in cents: write 173.2c"
    assert why("£103.6", RELX_TABLE, "D1:p1:b3") == "is an amount in £ but D1:p1:b3 shows 103.6 in pence: write 103.6p"
    assert why("$9,434m", RELX_TABLE, "D1:p1:b3") == "has currency $ but D1:p1:b3 is in £"
    assert why("-£2,861m", RELX_TABLE, "D1:p1:b3") == "is negative but D1:p1:b3 shows 2,861 as positive"
    assert why("£9,434", RELX_TABLE, "D1:p1:b3") == ("is missing its scale: the table is in £ million, "
                                                     "so write £9,434m or £9.43bn")
    for s, block in (("£5,000m", RELX_TABLE), ("£1.036", RELX_TABLE), ("$21,000m", DIAGEO), ("£3m", RELX_TABLE)):
        assert why(s, block) is None, s                                   # nothing close: plain "is not in"

# --- Dates and direction words ---

def test_abbreviated_month_dates_are_masked():  # seen in a run: "31 Dec" made '31' a figure
    for text in ["the year to 31 Dec 2024", "the year ended 30 Jun 2024", "on 30 Sept 2024", "on 31 Dec. 2024",
                 "as at Dec 31, 2024", "from Jun. 30 2024", "on 30 June 2024"]:
        assert extract(text) == [], text
    assert [f.raw for f in extract("revenue for the year to 31 Dec 2024 was £9,434m")] == ["£9,434m"]

def test_direction_words_on_levels_set_no_sign():  # a level with a comparative or "fell to" is not a change
    for text in ["Diageo's was lower at 29.6%", "RELX had the higher margin at 30.3%", "30.3%, down from 29.3%",
                 "the margin fell to 27.0%", "the margin decreased from 28.7% to 27.0%", "a lower 29.6%",
                 "margins were up at 30.3%", "growth of up to 3%"]:
        assert [f.direction for f in extract(text)] == [0] * len(extract(text)), text
    for text, d in [("revenue fell 3%", -1), ("revenue fell by 3%", -1), ("net sales were lower by 1.39%", -1),
                    ("net sales grew at 5% a year", 1), ("revenue rose a modest 2%", 1), ("a decline of 3%", -1),
                    ("revenue was down 3%", -1), ("revenue fell more than 3%", -1), ("higher by about 2%", 1)]:
        assert extract(text)[0].direction == d, text


# Scale from a table header such as "($ in 000s except GMV)", and a scale in one row's label applying to that row only
# (Affirm's FQ1'25 supplement, page 42: answers wrote "$698,479m" for $698,479 thousand and the check passed them).
THOUSANDS_TABLE = (
    "|  ($ in 000s except GMV) | Three Months Ended  |   |\n"
    "| --- | --- | --- |\n"
    "|   |  June 30, 2024 | September 30, 2024  |\n"
    "|  **Gross Merchandise Value ($M)** | $ 7,241 | $ 7,598 |\n"
    "|  Total Revenue, net | $ 659,185 | $ 698,479 |\n")

def test_header_in_000s_sets_thousands():
    for header in ("($ in 000s except GMV)", "(in thousands)", "$000s", "(000s)", "US$'000", "($ in thousands)"):
        assert parse_block(f"| {header} | 2024 |\n| --- | --- |\n| Revenue | 5 |\n").scale == 3, header

def test_row_label_scale_applies_to_its_row_only():
    pb = parse_block(THOUSANDS_TABLE)
    assert pb.scale == 3
    by_value = {f.value: f for f in pb.candidates}
    from traceable.numbers import value_scale
    assert value_scale(by_value[D("7598")], pb) == 6       # GMV ($M)
    assert value_scale(by_value[D("698479")], pb) == 3     # $ in 000s

def test_thousands_written_as_millions_does_not_match():
    assert not match_in_block(extract("$698,479m")[0], THOUSANDS_TABLE)
    for ok in ("$698.5m", "$698.479 million", "$698,479 thousand", "$698,479k", "$698,479,000"):
        assert match_in_block(extract(ok)[0], THOUSANDS_TABLE), ok

def test_millions_row_still_matches_in_millions():
    assert match_in_block(extract("$7,598m")[0], THOUSANDS_TABLE)
    assert not match_in_block(extract("$7,598k")[0], THOUSANDS_TABLE)

def test_row_label_scale_alone_does_not_set_the_block_scale():
    pb = parse_block("| Metric | 2024 |\n| --- | --- |\n| GMV ($M) | 7,598 |\n| Customers | 19,500 |\n")
    assert pb.scale == 0


# Financial statements state the unit in the page heading above the table, and write negative percentages as "(27.9) %"
# (Affirm's FQ3'24 shareholder letter, pages 11, 17 and 18).
STATEMENT = ("|   | March 31, 2024 | June 30, 2023  |\n| --- | --- | --- |\n"
             "|  Cash and cash equivalents | $ 1,272,760 | $ 892,027  |\n")
HEADING = "# **CONDENSED CONSOLIDATED BALANCE SHEETS**\n(Unaudited) (in thousands, except share and per share amounts)"

def test_a_negative_percentage_in_brackets_before_the_sign():
    f = extract("Operating Margin  (27.9) %  (81.4) %")[0]
    assert (f.value, f.neg, f.is_percent) == (D("27.9"), True, True)

def test_a_heading_above_the_table_sets_its_scale():
    assert parse_block(STATEMENT).scale == 0
    assert parse_block(STATEMENT, context=HEADING).scale == 3
    assert match_in_block(extract("$1,272.8m")[0], parse_block(STATEMENT, context=HEADING))
    assert not match_in_block(extract("$1,272,760m")[0], parse_block(STATEMENT, context=HEADING))

def test_table_captions_take_the_nearest_heading_above_on_the_page():
    from traceable.numbers import table_captions
    blocks = [{"type": "title", "text": HEADING}, {"type": "table", "text": STATEMENT},
              {"type": "text", "text": "The following table presents stock-based compensation (in thousands):"},
              {"type": "table", "text": STATEMENT}, {"type": "table", "text": "| ($ in millions) | 2024 |\n| --- | --- |\n| A | 5 |\n"}]
    caps = table_captions(blocks)
    assert caps[1] == HEADING and "stock-based" in caps[3]
    assert caps[0] == caps[2] == caps[4] == ""           # not tables, or a table that states its own scale
