# tests/test_calc.py
import json
from decimal import Decimal as D
import pytest
from conftest import FIXTURES
import re
from traceable.calc import (evaluate, resolve_input, format_result, check_inputs, pct_guard, CalcError, PCT_HINT,
                            literal_problems, money_dim, parse_money_unit)
from traceable.numbers import extract, close_to_display, currency_code

BLOCK = "| FOR THE YEAR | 2022 GBPm | 2024 GBPm |\n| Revenue | 8,553 | 9,434 |\n| Operating profit | 2,323 | 2,861 |"
RELX = [b for b in json.load(open(FIXTURES / "relx_is.json"))["pages"][0]["blocks"] if b["type"] == "table"][0]["content"]
KPI = "Effective tax rate was 24.0% and dividend cover 2.1x."

def test_margin_and_cagr():
    env = {"op": D("2861"), "rev": D("9434"), "r22": D("8553")}
    assert evaluate("op / rev", env)[0].quantize(D("0.0001")) == D("0.3033")
    r, used_float = evaluate("(rev / r22) ** (1/2) - 1", env)
    assert used_float and abs(r - D("0.0502")) < D("0.0001")

@pytest.mark.parametrize("expr", ["__import__('os')", "open('x')", "rev.real", "[1,2]", "lambda: 1"])
def test_disallowed(expr):
    with pytest.raises(CalcError):
        evaluate(expr, {"rev": D(1)})

def test_errors():
    with pytest.raises(CalcError, match="division by zero"):
        evaluate("1 / zero", {"zero": D(0)})
    with pytest.raises(CalcError, match="unknown name"):
        evaluate("nope + 1", {})
    with pytest.raises(CalcError, match="negative"):
        evaluate("neg ** 0.5", {"neg": D(-4)})

def test_resolve_input_uses_block_scale():
    base, ev = resolve_input({"name": "rev", "value": "9,434", "scale": "m", "source_id": "D1:p1:b3"}, BLOCK)
    assert base == D("9434000000") and ev["block_scale"] == 6

def test_resolve_input_rejects_scale_disagreement():
    with pytest.raises(CalcError, match="scale"):
        resolve_input({"name": "rev", "value": "9,434", "scale": "bn", "source_id": "D1:p1:b3"}, BLOCK)

def test_resolve_input_rejects_value_not_in_block():
    with pytest.raises(CalcError, match="not found"):
        resolve_input({"name": "rev", "value": "9,999", "source_id": "D1:p1:b3"}, BLOCK)

def test_assumption_requires_reason():
    with pytest.raises(CalcError, match="reason"):
        resolve_input({"name": "fx", "value": "1.17", "source_id": "assumption"}, None)
    assert resolve_input({"name": "fx", "value": "1.17", "source_id": "assumption", "reason": "ECB rate 30 Jun"}, None)[0] == D("1.17")

def test_format():
    assert format_result(D("0.30327"), "pct") == ("30.3%", True)
    assert format_result(D("820"), "num") == ("820", False)
    assert format_result(D("1234567.8"), "money") == ("1,234,567.80", False)


def test_only_small_literals_are_allowed():
    env = {"op": D("2861"), "rev": D("9434")}
    for expr in ["op / rev + 0.15", "0 * op + 0.45", "op * 100 / rev", "op / rev * 1e2", "op / 0.01"]:
        with pytest.raises(CalcError, match="not allowed: an expression may only contain the integers 0 to 12 and 0.5"):
            evaluate(expr, env)
    assert evaluate("(rev / op) ** (1/2) - 1 + rev / 12 * 0.5 * 0", env)[0] > 0   # 0..12 and 0.5 are fine

@pytest.mark.parametrize("expr", ["9**9**9", "12 ** 12 ** 12", "(1 + 1/rev) ** (12 ** 12)", "0 ** 0", "zero ** -1",
                                  "round(x, 12 ** 12)", "min()", "max(x)", "abs(x, x)", "-" * 600 + "x",
                                  "(" * 300 + "x" + ")" * 300, "+".join(["x"] * 300)])
def test_hostile_expressions_raise_calc_error(expr):
    with pytest.raises(CalcError):
        evaluate(expr, {"x": D("0.30327"), "rev": D("9434000000"), "zero": D(0)})

def test_pct_guard_catches_hidden_scaling():
    for expr in ["op / rev * 10 * 10", "op / rev / 10 / 10", "(op * 12 * 12) / rev", "op / rev * (10 * 10)"]:
        with pytest.raises(CalcError, match="multiplies by 100"):
            pct_guard(expr)
    for expr in ["op / rev", "(rev / r22) ** (1/2) - 1", "op * 10 / rev / 10", "(a - b) / b"]:
        pct_guard(expr)

def test_format_must_be_known():
    with pytest.raises(CalcError, match="format must be pct, num or money"):
        format_result(D("0.3"), "percent")

def test_format_significant_digits_and_units():
    assert format_result(D("0.0502"), "pct") == ("5.02%", True)
    assert format_result(D("-0.00216"), "pct") == ("-0.216%", True)
    assert format_result(D("1.5"), "pct") == ("150.0%", True)
    assert format_result(D("1234568"), "num") == ("1,234,568", False)
    assert format_result(D("0.30327"), "num") == ("0.303", False)
    assert format_result(D("1866.795"), "num") == ("1,867", False)
    assert format_result(D("2.1"), "num", "x") == ("2.10x", False)
    assert format_result(D("820"), "num", "kg") == ("820 kg", False)

def test_tool_displays_always_pass_the_calc_tolerance():
    for fmt, lo, hi in [("pct", D("0.001"), D("0.1")), ("pct", D("-0.03"), D("-0.0005")), ("num", D("0.01"), D("1")),
                        ("num", D("100.5"), D("5000.5"))]:
        step, x = (hi - lo) / 997, lo
        while x < hi:
            disp, is_pct = format_result(x, fmt)
            fig = extract(disp, apply_exemptions=False)[0]
            assert close_to_display(fig, x * (100 if is_pct else 1)), (x, disp)
            x += step

def test_resolve_input_parses_the_value_as_written():
    assert resolve_input({"name": "c", "value": "(3,300)", "source_id": "D1:p1:b3"}, RELX)[0] == D("-3300000000")
    assert resolve_input({"name": "c", "value": "-3,300", "source_id": "D1:p1:b3"}, RELX)[0] == D("-3300000000")
    assert resolve_input({"name": "op", "value": "£2,861m", "source_id": "D1:p1:b3"}, RELX)[0] == D("2861000000")
    assert resolve_input({"name": "r", "value": "**9,434**", "scale": "m", "source_id": "D1:p1:b3"}, RELX)[0] == D("9434000000")
    base, ev = resolve_input({"name": "eps", "value": "103.6", "source_id": "D1:p1:b3"}, RELX)
    assert base == D("103.6") and ev["unit"] == "p" and ev["matched"] == "103.6p"   # pence never take the GBPm scale
    assert resolve_input({"name": "eps", "value": "103.6p", "scale": "m", "source_id": "D1:p1:b3"}, RELX)[0] == D("103.6")
    with pytest.raises(CalcError, match="currency"):
        resolve_input({"name": "op", "value": "$2,861m", "source_id": "D1:p1:b3"}, RELX)

def test_percent_inputs_become_ratios():
    assert resolve_input({"name": "t", "value": "24.0%", "source_id": "D2:p1:b1"}, KPI)[0] == D("0.24")
    with pytest.raises(CalcError, match="percentage"):
        resolve_input({"name": "t", "value": "24.0", "source_id": "D2:p1:b1"}, KPI)

def test_header_and_note_cells_are_not_inputs():
    for v in ("2024", "3"):
        with pytest.raises(CalcError, match="not found"):
            resolve_input({"name": "y", "value": v, "source_id": "D1:p1:b3"}, RELX)

def test_negative_input_needs_a_negative_block_value():
    with pytest.raises(CalcError, match="negative"):
        resolve_input({"name": "op", "value": "-2,861", "scale": "m", "source_id": "D1:p1:b3"}, RELX)
    assert resolve_input({"name": "c", "value": "3,300", "scale": "m", "source_id": "D1:p1:b3"}, RELX)[0] == D("3300000000")

def test_input_checks():
    doc = {"source_id": "D1:p1:b3", "value": "1"}
    with pytest.raises(CalcError, match="duplicate input name 'rev'"):
        check_inputs([{**doc, "name": "rev"}, {**doc, "name": "rev"}], "rev")
    with pytest.raises(CalcError, match="input 'op' is not used"):
        check_inputs([{**doc, "name": "rev"}, {**doc, "name": "op"}], "rev * 2")
    with pytest.raises(CalcError, match="at least one input must come from a document"):
        check_inputs([{"name": "m", "value": "0.45", "source_id": "assumption", "reason": "view"}], "m")
    check_inputs([{**doc, "name": "rev"}, {"name": "fx", "value": "1.17", "source_id": "assumption", "reason": "ECB"}], "rev * fx")


def test_pct_hint_text():  # the exact repair text the model sees
    assert PCT_HINT == "format 'pct' already multiplies by 100: give the ratio (for example op / rev) and do not multiply by 100"

def test_literal_error_lists_what_is_allowed_and_explains_pct():  # seen in a run: "* 100"
    env = {"op": D("2861"), "rev": D("9434")}
    for expr in ["op / rev * 100", "op / rev * 1e2", "op / rev * 100.0", "op / rev / 0.01"]:
        with pytest.raises(CalcError) as e:
            evaluate(expr, env)
        assert "integers 0 to 12 and 0.5" in str(e.value) and PCT_HINT in str(e.value), expr
        assert "assumption" not in str(e.value), expr          # the old text sent the model to a "hundred" assumption
    with pytest.raises(CalcError) as e:
        evaluate("op / rev + 0.15", env)
    assert "integers 0 to 12 and 0.5" in str(e.value) and PCT_HINT not in str(e.value)
    assert literal_problems("(103.6 - 94.1) / 94.1 * 100") == [
        "literals 103.6, 94.1 and 100 not allowed: an expression may only contain the integers 0 to 12 and 0.5; "
        "write input names, not values, and pass any other number as an input from its block. " + PCT_HINT]
    assert literal_problems("(a / b) ** (1 / 2) - 1") == []

def test_pct_guard_treats_assumption_inputs_as_constants():
    for consts in ({"hundred": D(100)}, {"hundred": D("100.0")}, {"multiplier": D("1E+2")}):
        name = next(iter(consts))
        with pytest.raises(CalcError, match=re.escape(PCT_HINT)):
            pct_guard(f"op / rev * {name}", consts)
    with pytest.raises(CalcError, match="multiplies by 100"):
        pct_guard("op / rev / pct", {"pct": D("0.01")})
    with pytest.raises(CalcError, match="multiplies by 100"):
        pct_guard("op / rev * ten * ten", {"ten": D(10)})
    pct_guard("rev * fx / rev_2023 - 1", {"fx": D("1.17")})           # an FX rate is not a percent scaling
    pct_guard("(a / b) ** (1 / n) - 1", {"n": D(2)})                   # constants inside exponents do not scale
    pct_guard("op / rev * hundred", {})                                 # a document value of 100 is a real figure

def test_small_assumptions_are_constants():  # seen in runs: n_years = 2 and one = 1
    base, ev = resolve_input({"name": "n_years", "value": "2", "source_id": "assumption",
                              "reason": "2-year period from 2022 to 2024"}, None)
    assert base == D(2) and ev["constant"] is True
    for v in ("1", "0.5", "12", "-1"):
        assert resolve_input({"name": "k", "value": v, "source_id": "assumption"}, None)[1]["constant"], v
    for v in ("1.17", "12%", "2m", "£2"):
        with pytest.raises(CalcError, match="needs a reason"):
            resolve_input({"name": "k", "value": v, "source_id": "assumption"}, None)
        assert not resolve_input({"name": "k", "value": v, "source_id": "assumption", "reason": "r"}, None)[1]["constant"]

def test_money_inputs_record_their_currency():
    assert resolve_input({"name": "fc", "value": "(304)", "source_id": "D1:p1:b3"}, RELX)[1]["currency"] == "GBP"
    assert resolve_input({"name": "eps", "value": "103.6p", "source_id": "D1:p1:b3"}, RELX)[1]["currency"] is None
    assert resolve_input({"name": "t", "value": "24.0%", "source_id": "D2:p1:b1"}, KPI)[1]["currency"] is None
    assert resolve_input({"name": "v", "value": "USD 1.2bn", "source_id": "assumption", "reason": "r"}, None)[1]["currency"] == "USD"

def test_money_results_keep_currency_and_scale():  # seen in a run: C2 = -19,000,000
    assert format_result(D("-19000000"), "num", money=("GBP", 6)) == ("-£19m", False)
    assert format_result(D("1866795000"), "num", money=("GBP", 6)) == ("£1,867m", False)
    assert format_result(D("9434000000"), "num", money=("USD", 9)) == ("$9.43bn", False)
    assert format_result(D("759500"), "num", money=("USD", 0)) == ("$759,500", False)
    assert format_result(D("5000000"), "num", money=("CHF", 6)) == ("CHF 5m", False)
    assert format_result(D("-19000000"), "num", money=(None, 6)) == ("-19m", False)
    assert format_result(D("-19000000"), "money", money=("GBP", 6)) == ("-£19.00m", False)
    for x in (D("-19000000"), D("273456789"), D("1234"), D("-0.5")):
        for money in (("GBP", 6), ("EUR", 3), ("USD", 0), ("CHF", 9)):
            disp = format_result(x, "num", money=money)[0]
            fig = extract(disp, apply_exemptions=False)[0]
            assert close_to_display(fig, x) and currency_code(fig.currency) == money[0], (x, money, disp)

def test_money_dimension_of_an_expression():  # only amounts keep a currency
    dims = {"a": 1, "b": 1, "r": 0, "n": 0, "fx": None}
    for expr in ["a - b", "(a + b) / 2", "a * r", "abs(a) - min(a, b)", "-a", "round(a - b, 1)"]:
        assert money_dim(expr, dims) == 1, expr
    for expr, want in [("a / b", 0), ("(a / b) ** (1 / n) - 1", 0), ("a * b", 2), ("a * fx", None), ("a + r", None)]:
        assert money_dim(expr, dims) == want, expr

def test_currency_and_scale_units():
    assert parse_money_unit("GBPm") == ("GBP", 6) and parse_money_unit("£m") == ("GBP", 6)
    assert parse_money_unit("m") == (None, 6) and parse_money_unit("bn") == (None, 9) and parse_money_unit("USD") == ("USD", None)
    assert parse_money_unit("EUR k") == ("EUR", 3) and parse_money_unit("$bn") == ("USD", 9)
    for bad in ("widgets", "", "kg", "p", "%", "pp", "GBPx"):
        assert parse_money_unit(bad) is None, bad


from traceable.calc import period_guard, scale_guard, static_problems, resolve_unit, SCALE_HINT

DIAGEO = [b for b in json.load(open(FIXTURES / "diageo_is.json"))["pages"][0]["blocks"] if b["type"] == "table"][0]["content"]

def test_a_cagr_root_must_match_the_years_its_inputs_span():  # seen in a run: "3-year CAGR" over 2022-2024 used 1/3
    years = {"r24": 2024, "r22": 2022}
    for expr, consts in [("(r24 / r22) ** (1/3) - 1", {}), ("(r24 / r22) ** (1 / n) - 1", {"n": D(3)}),
                         ("((r24 / r22) ** (1 / 3) - 1)", {})]:
        with pytest.raises(CalcError) as e:
            period_guard(expr, years, consts)
        assert str(e.value).startswith("the exponent uses 1/3 but the inputs span 2 years (2022 to 2024)"), expr
    with pytest.raises(CalcError, match=re.escape("the exponent uses 1/2 but the inputs span 3 years (2021 to 2024)")):
        period_guard("(r24 / r21) ** 0.5 - 1", {"r24": 2024, "r21": 2021})
    for expr, consts in [("(r24 / r22) ** (1/2) - 1", {}), ("(r24 / r22) ** 0.5 - 1", {}), ("(r24 / r22) ** (1 / n) - 1", {"n": D(2)}),
                         ("r24 / r22 - 1", {}), ("(r24 / r22) ** 2", {}), ("(r24 / r22) ** (1 / r22)", {})]:
        period_guard(expr, years, consts)
    period_guard("(r24 / x) ** (1/3) - 1", {"r24": 2024})                # one year known: nothing to compare

def test_power_of_ten_rescales_are_refused():  # seen in a run: net_sales / thousand gave $0.0203bn
    for expr, consts in [("ns / thousand", {"thousand": D(1000)}), ("ns / 10 / 10 / 10", {}), ("ns * k", {"k": D("0.001")}),
                         ("-abs(ns) * m", {"m": D("1E+6")}), ("ns / (10 * 10 * 10)", {})]:
        with pytest.raises(CalcError) as e:
            scale_guard(expr, consts)
        assert str(e.value) == SCALE_HINT, expr
    assert SCALE_HINT.startswith("set the figure's scale (k, m, bn) instead of multiplying")
    for expr, consts in [("ns * fx", {"fx": D("1.17")}), ("(a - b) / thousand", {"thousand": D(1000)}), ("ns", {}),
                         ("ns / 10", {}), ("a / b * thousand", {"thousand": D(1000)}), ("ns * 12 / 12", {})]:
        scale_guard(expr, consts)

def test_literal_powers_of_ten_get_the_scale_hint():  # seen in a run: "net_sales_million / 1000"
    msg = literal_problems("net_sales_million / 1000")[0]
    assert msg.startswith("literal 1000 not allowed") and msg.endswith(SCALE_HINT) and "assumption" not in msg
    assert SCALE_HINT not in literal_problems("op / rev + 0.15")[0] and SCALE_HINT not in literal_problems("op / rev * 100")[0]

def test_cents_are_a_unit():
    assert format_result(D("-23.1"), "num", "c") == ("-23.1c", False)
    assert resolve_unit("num", "c")[0] == "c" and resolve_unit("num", "cents")[0] == "c" and resolve_unit("num", "pence")[0] == "p"
    assert "c (cents)" in resolve_unit("num", "widgets")[2]

def test_diageo_eps_inputs_are_cents_and_record_row_and_year():  # seen in a run: C2 = -$23.1m
    base, ev = resolve_input({"name": "eps", "value": "173.2", "source_id": "D2:p1:b7"}, DIAGEO)
    assert base == D("173.2") and (ev["unit"], ev["currency"], ev["row"], ev["year"]) == ("c", None, "Basic earnings per share", 2024)
    base, ev = resolve_input({"name": "ns", "value": "20,269", "source_id": "D2:p1:b7"}, DIAGEO)
    assert base == D("20269000000") and (ev["unit"], ev["currency"], ev["row"], ev["year"]) == (None, "USD", "Net sales", 2024)
    base, ev = resolve_input({"name": "sh", "value": "2,234", "source_id": "D2:p1:b7"}, DIAGEO)
    assert base == D("2234000000") and ev["currency"] is None                     # shares in millions, not dollars
    assert resolve_input({"name": "d", "value": "7", "source_id": "D2:p1:b7"}, DIAGEO)[1]["year"] is None   # 2023 and 2022

def test_input_mismatches_name_the_real_difference():
    for inp, block, want in [
            ({"name": "eps", "value": "173.2p", "source_id": "D2:p1:b7"}, DIAGEO,
             "input 'eps': 173.2p is in pence but D2:p1:b7 shows 173.2 in cents; pass it as written in the block (173.2)"),
            ({"name": "eps", "value": "£103.6", "source_id": "D1:p1:b3"}, RELX,
             "input 'eps': £103.6 is an amount in £ but D1:p1:b3 shows 103.6 in pence; pass it as written in the block (103.6p)"),
            ({"name": "op", "value": "£6,001m", "source_id": "D2:p1:b7"}, DIAGEO, "input 'op': £6,001m has currency £ but D2:p1:b7 is in $"),
            ({"name": "sh", "value": "$2,234m", "source_id": "D2:p1:b7"}, DIAGEO,
             "input 'sh': $2,234m has currency $ but D2:p1:b7 shows 2,234 in millions, with no currency"),
            ({"name": "rev", "value": "9,434"}, None,
             "input 'rev' has no source_id: give its block ID (for example D1:p1:b3), or 'assumption' with a reason")]:
        with pytest.raises(CalcError) as e:
            resolve_input(inp, block)
        assert str(e.value) == want
    with pytest.raises(CalcError, match="unknown source_id 'D9:p1:b1'"):
        resolve_input({"name": "rev", "value": "9,434", "source_id": "D9:p1:b1"}, None)

def test_syntax_errors_name_the_input_names():  # seen in a run: "Operating profit FY2024 / Net sales FY2024"
    inputs = [{"name": "Operating profit FY2024", "value": "6,001", "source_id": "D1:p1:b7"},
              {"name": "Net sales FY2024", "value": "20,269", "source_id": "D1:p1:b7"}]
    assert static_problems(inputs, "Operating profit FY2024 / Net sales FY2024") == [
        "syntax error in the expression: input names must be simple names (letters, digits and _): rename "
        "'Operating profit FY2024' to operating_profit_fy2024 and 'Net sales FY2024' to net_sales_fy2024, "
        "and write the expression as operating_profit_fy2024 / net_sales_fy2024"]
    ok = [{"name": "op", "value": "1", "source_id": "D1:p1:b3"}, {"name": "rev", "value": "1", "source_id": "D1:p1:b3"}]
    assert static_problems(ok, "op / / rev") == [
        "syntax error in the expression (invalid syntax): write it with the input names (op, rev), + - * / ** ( ) "
        "and min, max, abs, round"]
