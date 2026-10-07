# tests/test_server.py
import json
from conftest import FIXTURES
from traceable.ledger import Ledger
from traceable.server import tool_read_document, tool_calculate, tool_calculate_many
from test_ocr import FakeOcr

def test_read_then_calculate(tmp_path):
    out = tool_read_document(str(FIXTURES / "relx_is.pdf"), client=FakeOcr(), root=tmp_path)
    assert "[D1:p1:b3]" in out
    r = tool_calculate("margin", [{"name": "op", "value": "2,861", "scale": "m", "source_id": "D1:p1:b3"},
                                  {"name": "rev", "value": "9,434", "scale": "m", "source_id": "D1:p1:b3"}],
                       "op / rev", "pct", root=tmp_path)
    assert r.startswith("C1 = 30.3%") and "[C1]" in r
    calc = json.loads((tmp_path / ".traceable/ledger.json").read_text())["calculations"][0]
    assert calc["is_percent"] and calc["inputs"][0]["evidence"]["matched"] == "2,861"

def test_pct_with_times_100_is_rejected(tmp_path):
    tool_read_document(str(FIXTURES / "relx_is.pdf"), client=FakeOcr(), root=tmp_path)
    r = tool_calculate("m", [{"name": "op", "value": "2,861", "scale": "m", "source_id": "D1:p1:b3"},
                             {"name": "rev", "value": "9,434", "scale": "m", "source_id": "D1:p1:b3"}],
                       "(op / rev) * 100", "pct", root=tmp_path)
    assert r.startswith("ERROR:") and "100" in r

def test_relative_path_and_bad_input_are_errors(tmp_path):
    assert tool_read_document("relx.pdf", client=FakeOcr(), root=tmp_path).startswith("ERROR:")
    tool_read_document(str(FIXTURES / "relx_is.pdf"), client=FakeOcr(), root=tmp_path)
    r = tool_calculate("x", [{"name": "a", "value": "1,111", "source_id": "D1:p1:b3"}], "a", root=tmp_path)
    assert r.startswith("ERROR:") and "not found" in r


OP = {"name": "op", "value": "2,861", "scale": "m", "source_id": "D1:p1:b3"}
REV = {"name": "rev", "value": "9,434", "scale": "m", "source_id": "D1:p1:b3"}

def test_calculate_many_never_loses_the_batch(tmp_path):
    tool_read_document(str(FIXTURES / "relx_is.pdf"), client=FakeOcr(), root=tmp_path)
    out = tool_calculate_many([
        {"title": "ok", "inputs": [OP, REV], "expression": "op / rev", "format": "pct"},
        {"title": "huge", "inputs": [OP], "expression": "op ** 12 ** 12"},
        {"title": "zero", "inputs": [OP], "expression": "0 ** -1 + op"},
        {"title": "bad format", "inputs": [OP, REV], "expression": "op / rev", "format": "percent"},
        {"title": "dup", "inputs": [REV, {**REV, "value": "8,553"}], "expression": "rev"},
        {"title": "unused", "inputs": [OP, REV], "expression": "op"},
        {"title": "all assumed", "inputs": [{"name": "m", "value": "0.45", "source_id": "assumption", "reason": "view"}],
         "expression": "m", "format": "pct"},
        {"title": "hidden 100", "inputs": [OP, REV], "expression": "op / rev * 10 * 10", "format": "pct"},
        {"title": "literal", "inputs": [OP, REV], "expression": "op / rev + 0.15", "format": "pct"},
        "not an object",
    ], root=tmp_path).splitlines()
    assert out[0].startswith("C1 = 30.3%") and len(out) == 10 and all(l.startswith("ERROR:") for l in out[1:])
    assert "format must be pct, num or money" in out[3] and "duplicate input name" in out[4] and "not used" in out[5]
    assert "at least one input must come from a document" in out[6] and "multiplies by 100" in out[7]
    assert len(Ledger(tmp_path).load()["calculations"]) == 1

def test_calculation_records_unit_and_assumption(tmp_path):
    tool_read_document(str(FIXTURES / "relx_is.pdf"), client=FakeOcr(), root=tmp_path)
    fin = {"name": "fin", "value": "(298)", "scale": "m", "source_id": "D1:p1:b3"}
    assert tool_calculate("cover", [OP, fin], "op / abs(fin)", "num", "x", root=tmp_path).startswith("C1 = 9.60x")
    fx = {"name": "fx", "value": "1.17", "source_id": "assumption", "reason": "ECB rate on 31 Dec"}
    assert tool_calculate("rev in EUR", [REV, fx], "rev * fx", "money", root=tmp_path).startswith("C2 = 11,037,780,000.00")
    c1, c2 = Ledger(tmp_path).load()["calculations"]
    assert (c1["unit"], c1["has_assumption"], c2["has_assumption"]) == ("x", False, True)

def test_read_document_pages_are_one_based_and_range_checked(tmp_path):
    out = tool_read_document(str(FIXTURES / "relx_is.pdf"), [2], client=FakeOcr(), root=tmp_path)
    assert out.startswith("D1 = relx_is.pdf, pages 2") and "[D1:p2:b4]" in out and "[D1:p1:" not in out
    for bad in ([0], [99], ["2"]):
        r = tool_read_document(str(FIXTURES / "relx_is.pdf"), bad, client=FakeOcr(), root=tmp_path)
        assert r.startswith("ERROR:"), bad
    assert "has 2 pages" in tool_read_document(str(FIXTURES / "relx_is.pdf"), [99], client=FakeOcr(), root=tmp_path)


from traceable.calc import PCT_HINT

B3 = "D1:p1:b3"
TAX = {"name": "tax_expense_2024", "value": "613", "source_id": B3}
PBT = {"name": "profit_before_tax_2024", "value": "2,557", "source_id": B3}
HUNDRED = {"name": "hundred", "value": "100", "source_id": "assumption", "reason": "conversion factor for percentage"}

def read(tmp_path):
    tool_read_document(str(FIXTURES / "relx_is.pdf"), client=FakeOcr(), root=tmp_path)

def test_pct_refuses_100_passed_as_an_assumption(tmp_path):  # seen in a run: C1 = 2,397.3%
    read(tmp_path)
    r = tool_calculate("RELX 2024 effective tax rate", [TAX, PBT, HUNDRED], "tax_expense_2024 / profit_before_tax_2024 * hundred",
                       "pct", root=tmp_path)
    assert r == "ERROR: " + PCT_HINT and Ledger(tmp_path).load()["calculations"] == []
    pct = {"name": "pct", "value": "0.01", "source_id": "assumption", "reason": "percent"}
    assert tool_calculate("t", [TAX, PBT, pct], "tax_expense_2024 / profit_before_tax_2024 / pct", "pct",
                          root=tmp_path) == "ERROR: " + PCT_HINT
    ok = tool_calculate("RELX 2024 effective tax rate", [TAX, PBT], "tax_expense_2024 / profit_before_tax_2024", "pct", root=tmp_path)
    assert ok.startswith("C1 = 24.0%  (write it as: 24.0% [C1])")

def test_every_static_problem_is_reported_at_once(tmp_path):
    read(tmp_path)
    r = tool_calculate("RELX 2024 operating margin", [OP, REV], "op / rev * 100", "pct", "p", root=tmp_path)
    assert r.startswith("ERROR: unit 'p' (pence) does not go with format 'pct': for a percentage leave unit out")
    assert PCT_HINT in r and "pp" not in r
    eps = [{"name": "eps_2023", "source_id": B3, "value": "94.1"}, {"name": "eps_2024", "source_id": B3, "value": "103.6"}]
    r = tool_calculate("EPS growth 2023 to 2024", eps, "(103.6 - 94.1) / 94.1 * 100", "pct", "pct", root=tmp_path)
    assert "input 'eps_2023' is not used" in r and "input 'eps_2024' is not used" in r
    assert "literals 103.6, 94.1 and 100 not allowed" in r and PCT_HINT in r

def test_percent_units_are_ignored_and_pp_is_never_suggested(tmp_path):
    read(tmp_path)
    for n, unit in enumerate((None, "", "%", "pct", "percent"), start=1):
        assert tool_calculate("m", [OP, REV], "op / rev", "pct", unit, root=tmp_path).startswith(f"C{n} = 30.3%  "), unit
    bad = tool_calculate("w", [OP, REV], "op - rev", "num", "widgets", root=tmp_path)
    assert bad.startswith("ERROR: unit 'widgets' is not a known unit") and "pp" not in bad
    for name in ("x (multiple)", "p (pence)", "kg (kilograms)", "GBPm"):
        assert name in bad, name
    r = tool_calculate("m", [OP, REV], "op / rev", "num", "%", root=tmp_path)
    assert r.startswith("ERROR: unit '%' needs format 'pct'")
    op23 = {**OP, "name": "op23", "value": "2,682"}
    rev23 = {**REV, "name": "rev23", "value": "9,161"}
    asked = tool_calculate("margin change", [OP, REV, op23, rev23], "op / rev - op23 / rev23", "pct", "pp", root=tmp_path)
    assert asked.startswith("C6 = 1.05pp")                             # pp only when asked for
    displays = [c["display"] for c in Ledger(tmp_path).load()["calculations"]]
    assert [d for d in displays if d.endswith("pp")] == ["1.05pp"]

def test_money_results_display_in_the_inputs_currency_and_scale(tmp_path):
    read(tmp_path)
    fc = lambda v23, v24: [{"name": "fc_2023", "value": v23, "source_id": B3}, {"name": "fc_2024", "value": v24, "source_id": B3}]
    assert tool_calculate("Finance costs change", fc("323", "304"), "fc_2024 - fc_2023", "num",
                          root=tmp_path) == ("C1 = -£19m  (write it as: -£19m [C1])  inputs: fc_2023 = (323) from row "
                                             "'Finance costs' [D1:p1:b3]; fc_2024 = (304) from row 'Finance costs' [D1:p1:b3]")
    assert tool_calculate("Finance costs change", fc("(323)", "(304)"), "fc_2024 - fc_2023", "num",
                          root=tmp_path).startswith("C2 = £19m  ")
    for n, unit in enumerate(("GBPm", "m", "£m"), start=3):             # runs used unit "GBPm" and "m"
        assert tool_calculate("fc", fc("323", "304"), "fc_2024 - fc_2023", "num", unit, root=tmp_path).startswith(f"C{n} = -£19m  "), unit
    c1 = Ledger(tmp_path).load()["calculations"][0]
    assert (c1["result"], c1["currency"], c1["scale"], c1["unit"]) == ("-19000000", "GBP", 6, None)
    assert tool_calculate("ratio", [OP, REV], "op / rev", "num", root=tmp_path).startswith("C6 = 0.303  ")
    fin = {"name": "fin", "value": "(298)", "scale": "m", "source_id": B3}
    assert tool_calculate("cover", [OP, fin], "op / abs(fin)", "num", "x", root=tmp_path).startswith("C7 = 9.60x  ")
    fx = {"name": "fx", "value": "1.17", "source_id": "assumption", "reason": "ECB rate"}
    assert tool_calculate("rev in EUR", [REV, fx], "rev * fx", "num", root=tmp_path).startswith("C8 = 11,037,780,000  ")
    assert tool_calculate("rev in EUR", [REV, fx], "rev * fx", "num", "EURm", root=tmp_path).startswith("C9 = €11,038m  ")
    r = tool_calculate("margin", [OP, REV], "op / rev", "num", "GBPm", root=tmp_path)
    assert r.startswith("ERROR: unit 'GBPm' is a currency or scale, but op / rev is not an amount")

def test_constant_assumptions_do_not_flag_the_calculation(tmp_path):  # seen in a run: CAGR with n_years = 2
    read(tmp_path)
    ins = [{"name": "revenue_2022", "source_id": B3, "value": "8553"}, {"name": "revenue_2024", "source_id": B3, "value": "9434"},
           {"name": "n_years", "source_id": "assumption", "reason": "2-year period from 2022 to 2024", "value": "2"}]
    r = tool_calculate("RELX 2022-2024 revenue CAGR", ins, "(revenue_2024 / revenue_2022) ** (1 / n_years) - 1", "pct", root=tmp_path)
    assert r.startswith("C1 = 5.02%")
    c = Ledger(tmp_path).load()["calculations"][0]
    assert c["has_assumption"] is False and c["inputs"][2]["evidence"]["constant"] is True

def test_tool_descriptions_never_suggest_pp():
    from traceable.server import build
    descs = {t.name: t.description for t in build()._tool_manager.list_tools()}
    assert "pp" not in descs["calculate"] and "never multiply by 100" in descs["calculate"]


D2 = "D2:p1:b7"
EPS = [{"name": "eps_2024", "value": "173.2", "source_id": D2}, {"name": "eps_2023", "value": "196.3", "source_id": D2}]

def read_both(tmp_path):
    tool_read_document(str(FIXTURES / "relx_is.pdf"), client=FakeOcr(), root=tmp_path)
    tool_read_document(str(FIXTURES / "diageo_is.pdf"), client=FakeOcr("diageo_is.json"), root=tmp_path)

def test_calculations_show_the_row_of_each_input(tmp_path):  # seen in a run: "EBITDA" was the operating profit row
    read(tmp_path)
    r = tool_calculate("RELX EBITDA margin 2024", [{"name": "ebitda", "value": "2,861", "scale": "m", "source_id": B3}, REV],
                       "ebitda / rev", "pct", root=tmp_path)
    assert r == ("C1 = 30.3%  (write it as: 30.3% [C1])  inputs: ebitda = 2,861 from row 'Operating profit' [D1:p1:b3]; "
                 "rev = 9,434 from row 'Revenue' [D1:p1:b3]")
    n = {"name": "n", "value": "2", "source_id": "assumption"}
    fx = {"name": "fx", "value": "1.17", "source_id": "assumption", "reason": "ECB rate"}
    r = tool_calculate("cagr", [REV, {**REV, "name": "r22", "value": "8,553"}, n], "(rev / r22) ** (1 / n) - 1", "pct", root=tmp_path)
    assert r.endswith("inputs: rev = 9,434 from row 'Revenue' [D1:p1:b3]; r22 = 8,553 from row 'Revenue' [D1:p1:b3]; n = 2 (constant)")
    assert tool_calculate("eur", [REV, fx], "rev * fx", "num", "EURm", root=tmp_path).endswith("fx = 1.17 (assumption: ECB rate)")
    c = Ledger(tmp_path).load()["calculations"][0]
    assert [(i["evidence"]["row"], i["evidence"]["year"]) for i in c["inputs"]] == [("Operating profit", 2024), ("Revenue", 2024)]

def test_a_cube_root_over_two_years_is_refused(tmp_path):  # seen in a run: "3-year revenue CAGR" = 3.32% over 2022-2024
    read_both(tmp_path)
    ins = [{"name": "revenue_2024", "value": "9,434", "scale": "m", "source_id": B3},
           {"name": "revenue_2022", "value": "8,553", "scale": "m", "source_id": B3}]
    r = tool_calculate("RELX 3-year revenue CAGR", ins, "(revenue_2024 / revenue_2022) ** (1/3) - 1", "pct", root=tmp_path)
    assert r.startswith("ERROR: the exponent uses 1/3 but the inputs span 2 years (2022 to 2024)")
    dia = [{"name": "ns24", "value": "20,269", "source_id": D2}, {"name": "ns22", "value": "20,516", "source_id": D2}]
    assert tool_calculate("Diageo", dia, "(ns24 / ns22) ** (1/3) - 1", "pct", root=tmp_path).startswith(
        "ERROR: the exponent uses 1/3 but the inputs span 2 years (2022 to 2024)")        # columns in reverse order
    assert tool_calculate("RELX 2022-2024 revenue CAGR", ins, "(revenue_2024 / revenue_2022) ** 0.5 - 1", "pct",
                          root=tmp_path).startswith("C1 = 5.02%")
    assert tool_calculate("Diageo", dia, "(ns24 / ns22) ** 0.5 - 1", "pct", root=tmp_path).startswith("C2 = -0.604%")

def test_scale_conversions_are_refused(tmp_path):  # seen in a run: C1 = $0.0203bn, C5 = 20,269,000; turn limit
    read_both(tmp_path)
    ns = {"name": "net_sales_million", "value": "20,269", "scale": "m", "source_id": D2}
    thousand = {"name": "thousand", "value": "1000", "source_id": "assumption", "reason": "conversion factor from millions to billions"}
    for inputs, expr, unit in [([ns, thousand], "net_sales_million / thousand", "bn"), ([ns], "net_sales_million / 10 / 10 / 10", "bn"),
                               ([ns, thousand], "net_sales_million / thousand", None)]:
        r = tool_calculate("FY2024 Net sales in USD billions", inputs, expr, "num", unit, root=tmp_path)
        assert r == "ERROR: " + SCALE_HINT, r
    r = tool_calculate("x", [ns], "net_sales_million / 1000", "num", "bn", root=tmp_path)
    assert "literal 1000 not allowed" in r and r.endswith(SCALE_HINT)
    assert Ledger(tmp_path).load()["calculations"] == []

def test_per_share_changes_keep_their_unit(tmp_path):  # seen in a run: -$23.1m and -23,100,000p before
    read_both(tmp_path)
    assert tool_calculate("Diageo basic EPS change", EPS, "eps_2024 - eps_2023", root=tmp_path).startswith(
        "C1 = -23.1c  (write it as: -23.1c [C1])  inputs: eps_2024 = 173.2 from row 'Basic earnings per share' [D2:p1:b7]")
    assert tool_calculate("Diageo EPS change %", EPS, "(eps_2024 - eps_2023) / eps_2023", "pct", root=tmp_path).startswith("C2 = -11.8%  ")
    relx = [{"name": "a", "value": "103.6p", "source_id": B3}, {"name": "b", "value": "94.1", "source_id": B3}]
    assert tool_calculate("RELX EPS change", relx, "a - b", root=tmp_path).startswith("C3 = 9.50p  ")
    assert tool_calculate("RELX EPS ratio", relx, "a / b", root=tmp_path).startswith("C4 = 1.10  ")
    c = Ledger(tmp_path).load()["calculations"]
    assert [(x["unit"], x["currency"]) for x in c] == [("c", None), (None, None), ("p", None), (None, None)]

def test_a_unit_the_inputs_contradict_is_refused(tmp_path):
    read_both(tmp_path)
    r = tool_calculate("EPS change", EPS, "eps_2024 - eps_2023", "num", "p", root=tmp_path)
    assert r == "ERROR: unit 'p' (pence) does not match the inputs, which are in c (cents): leave unit out"
    fc = [{"name": "fc_2023", "value": "323", "source_id": B3}, {"name": "fc_2024", "value": "304", "source_id": B3}]
    r = tool_calculate("fc", fc, "fc_2024 - fc_2023", "num", "kg", root=tmp_path)
    assert r == "ERROR: unit 'kg' (kilograms) does not match the inputs, which are amounts in £ million: leave unit out"
    r = tool_calculate("EPS change", EPS, "eps_2024 - eps_2023", "num", "USDm", root=tmp_path)
    assert r == "ERROR: unit 'USDm' is a currency or scale, but the inputs are in c (cents): leave unit out"
    assert tool_calculate("EPS change", EPS, "eps_2024 - eps_2023", "num", "c", root=tmp_path).startswith("C1 = -23.1c  ")
    assert tool_calculate("EPS change", EPS, "eps_2024 - eps_2023", "num", "cents", root=tmp_path).startswith("C2 = -23.1c  ")

def test_input_errors_name_the_problem(tmp_path):  # seen in a run: names with spaces
    read_both(tmp_path)
    r = tool_calculate("Diageo FY2024 operating margin",
                       [{"name": "Operating profit FY2024", "value": "6,001", "scale": "m", "source_id": D2},
                        {"name": "Net sales FY2024", "value": "20,269", "scale": "m", "source_id": D2}],
                       "Operating profit FY2024 / Net sales FY2024", "pct", root=tmp_path)
    assert r.startswith("ERROR: syntax error in the expression: input names must be simple names")
    assert "write the expression as operating_profit_fy2024 / net_sales_fy2024" in r
    r = tool_calculate("m", [{"name": "op", "value": "6,001", "scale": "m"}, {"name": "ns", "value": "20,269", "source_id": D2}],
                       "op / ns", "pct", root=tmp_path)
    assert r == "ERROR: input 'op' has no source_id: give its block ID (for example D1:p1:b3), or 'assumption' with a reason"
    r = tool_calculate("eps", [{**EPS[0], "value": "173.2p"}, EPS[1]], "eps_2024 - eps_2023", root=tmp_path)
    assert r == "ERROR: input 'eps_2024': 173.2p is in pence but D2:p1:b7 shows 173.2 in cents; pass it as written in the block (173.2)"

def test_tool_texts_explain_pages_and_other_readers(tmp_path):
    from traceable.server import build
    desc = {t.name: t.description for t in build()._tool_manager.list_tools()}["read_document"]
    assert "pages are PDF page numbers (1 = first page of the file), not the numbers printed on the pages" in desc
    assert "Never read PDFs or .traceable files with other tools; use traceable_read_document" in desc
    # Seen in a run: not knowing it had the whole file, the model re-read it and then tried bash pdfinfo.
    assert tool_read_document(str(FIXTURES / "diageo_is.pdf"), client=FakeOcr("diageo_is.json"), root=tmp_path).startswith(
        "D1 = diageo_is.pdf, all 3 pages\n")
    assert tool_read_document(str(FIXTURES / "diageo_is.pdf"), [2], client=FakeOcr("diageo_is.json"), root=tmp_path).startswith(
        "D1 = diageo_is.pdf, pages 2 of 3\n")
    r = tool_read_document(str(FIXTURES / "diageo_is.pdf"), [161], client=FakeOcr("diageo_is.json"), root=tmp_path)
    assert r == ("ERROR: page 161 is out of range: diageo_is.pdf has 3 pages, numbered 1 to 3 (pages are PDF page numbers "
                 "(1 = first page of the file), not the numbers printed on the pages)")

from traceable.calc import SCALE_HINT


def test_reading_without_a_key_names_the_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    out = tool_read_document(str(FIXTURES / "relx_is.pdf"), pages=[1], root=tmp_path)
    assert out.startswith("ERROR:") and "MISTRAL_API_KEY" in out and "Keychain" in out


def test_a_difference_of_percentages_must_be_a_percentage(tmp_path):  # seen in a run: "exceeded by 0.0100"
    Ledger(tmp_path).add_source("w", "web", "https://example.com", {"block_id": "W1", "type": "web",
                                "text": "Gross margin: GAAP 74.0%, non-GAAP 75.0%"})
    a = {"name": "non_gaap", "value": "75.0%", "source_id": "W1"}
    b = {"name": "gaap", "value": "74.0%", "source_id": "W1"}
    bare = tool_calculate("gap", [a, b], "non_gaap - gaap", "num", root=tmp_path)
    assert bare.startswith("ERROR:") and "format 'pct'" in bare and "percentage points" in bare
    assert tool_calculate("gap", [a, b], "non_gaap - gaap", "pct", "pp", root=tmp_path).startswith("C1 = 1.00pp")
    assert tool_calculate("ratio", [a, b], "non_gaap / gaap", "num", root=tmp_path).startswith("C2 = ")   # a ratio stays allowed
