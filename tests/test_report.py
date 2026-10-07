# tests/test_report.py
import json, re
from test_check import setup, transcript, Q, add_block, B
from traceable.check import run
from traceable.ledger import Ledger
from traceable.server import tool_calculate

def payload(html):
    return json.loads(re.search(r"const D=(.*?);\n", html, re.S).group(1))

def test_report_bolds_matched_span(tmp_path):
    c = setup(tmp_path)
    run(transcript(tmp_path, [Q, {"role": "tool", "content": c},
        {"role": "assistant", "content": "Margin 30.3% [C1] on £9,434m revenue [D1:p1:b3]."}]))
    html = (tmp_path / ".traceable/report.html").read_text()
    # The payload escapes "<", so the bolded span is read from the decoded JSON.
    assert "<b>9,434</b>" in payload(html)["figures"][1]["bold_html"]
    assert "D1-p1.png" in html and "C1" in html and "op / rev" in html
    assert 'class="figure-chip status-untraced"' not in html and "0 untraced" in html


def test_report_escapes_block_text_and_calc_fields(tmp_path):
    setup(tmp_path)
    add_block(tmp_path, 'Revenue 9,434 </script><script>document.title="PWNED-block"</script>')
    c = tool_calculate('<img src=x onerror="document.title=1">',
                       [{"name": "rev", "value": "9,434", "scale": "m", "source_id": "D1:p1:b3"},
                        {"name": "fx", "value": "1.17", "source_id": "assumption", "reason": "<i>ECB</i>"}],
                       "rev * fx", "money", root=tmp_path)
    assert c.startswith("C2 = ")
    run(transcript(tmp_path, [Q, {"role": "tool", "content": c},
        {"role": "assistant", "content": "Revenue was 9,434 [D2:p1:b1], or €11,037,780,000.00 [C2]."}]))
    html = (tmp_path / ".traceable/report.html").read_text()
    script = html[html.index("<script>") + len("<script>"):html.rindex("</script>")]
    assert "</script" not in script.lower() and "<img src=x" not in html and "<i>ECB" not in html
    assert "${esc(c.title)}" in script and "${esc(f.raw)}" in script and "${esc(x.name)}" in script
    assert [f["status"] for f in payload(html)["figures"]] == ["traced", "calculated_with_assumption"]

def test_report_places_repeated_figures_and_bolds_by_offset(tmp_path):
    c = setup(tmp_path)
    run(transcript(tmp_path, [Q, {"role": "tool", "content": c}, {"role": "assistant",
        "content": "Revenue £9,434m [D1:p1:b3]; again £9,434m [D1:p1:b3]; JV share £43m [D1:p1:b3]; margin 30.3% [C1]."}]))
    html = (tmp_path / ".traceable/report.html").read_text()
    answer = html[html.index('<div class="answer">'):html.index("</div>", html.index('<div class="answer">'))]
    assert re.findall(r'data-i="(\d)">([^<]*)</span>', answer) == [("0", "£9,434m"), ("1", "£9,434m"), ("2", "£43m"), ("3", "30.3%")]
    bold = payload(html)["figures"][2]["bold_html"]
    assert "<td>46</td><td><b>43</b></td>" in bold and "9,<b>43</b>4" not in bold   # tables render as HTML since the native build

def test_report_scales_highlights_without_page_dimensions(tmp_path):
    setup(tmp_path)
    lg = Ledger(tmp_path); d = lg.load()
    list(d["documents"].values())[0]["pages"][0]["dimensions"] = None
    lg.save(d)
    run(transcript(tmp_path, [Q, {"role": "assistant", "content": "Revenue was £9,434m [D1:p1:b3]."}]))
    html = (tmp_path / ".traceable/report.html").read_text()
    assert payload(html)["blocks"]["D1:p1:b3"]["w"] is None and "naturalWidth" in html

def test_report_header_counts_denials_and_follows_answers_without_figures(tmp_path):
    c = setup(tmp_path)
    msgs = [Q, {"role": "tool", "content": c}, {"role": "assistant", "content": "Margin was 30.3%."},
            {"role": "user", "content": "Untraced figures", "injected": True}, {"role": "assistant", "content": "Margin was 30.3% (C1)."}]
    run(transcript(tmp_path, msgs[:3]))
    run(transcript(tmp_path, msgs))
    run(transcript(tmp_path, msgs + [{"role": "user", "content": "Untraced figures", "injected": True},
                                     {"role": "assistant", "content": f"Margin was 30.3% [C1] on revenue of £9,434m {B}."}]))
    html = (tmp_path / ".traceable/report.html").read_text()
    assert "denials for this question: 2" in html and "OCR model: mistral-ocr-latest" in html and "9,434" in html
    run(transcript(tmp_path, [Q, {"role": "assistant", "content": "Which year do you mean?"}]))
    assert "9,434" not in (tmp_path / ".traceable/report.html").read_text()


def test_not_checked_report_has_a_red_banner_and_no_stale_answer(tmp_path):
    c = setup(tmp_path)
    run(transcript(tmp_path, [Q, {"role": "tool", "content": c}, {"role": "assistant", "content": f"Revenue was £9,434m {B}."}]))
    run({"cwd": str(tmp_path), "hook_event_name": "post_agent"})
    html = (tmp_path / ".traceable/report.html").read_text()
    assert '<div class="banner not-checked">NOT CHECKED: this Vibe harness does not pass the transcript to hooks; ' \
           'run vibe --legacy-harness</div>' in html
    assert "9,434" not in html and payload(html)["figures"] == [] and "No answer was checked." in html
    assert ".not-checked{" in html and "var(--bad)" in html

def test_assumption_and_constant_inputs_are_labelled_not_linked(tmp_path):
    c = setup(tmp_path)
    fx = {"name": "fx", "value": "1.17", "source_id": "assumption", "reason": "ECB rate on 31 Dec"}
    n = {"name": "n", "value": "2", "source_id": "assumption", "reason": "two years"}
    rev = {"name": "rev", "value": "9,434", "scale": "m", "source_id": "D1:p1:b3"}
    r22 = {"name": "r22", "value": "8,553", "scale": "m", "source_id": "D1:p1:b3"}
    c2 = tool_calculate("rev in EUR", [rev, fx], "rev * fx", "money", root=tmp_path)
    c3 = tool_calculate("cagr", [rev, r22, n], "(rev / r22) ** (1 / n) - 1", "pct", root=tmp_path)
    run(transcript(tmp_path, [Q, {"role": "tool", "content": c}, {"role": "tool", "content": c2}, {"role": "tool", "content": c3},
        {"role": "assistant", "content": "Revenue was €11,037,780,000.00 [C2] and grew 5.02% a year [C3]."}]))
    html = (tmp_path / ".traceable/report.html").read_text()
    d = payload(html)
    assert [(x["kind"], x.get("label")) for x in d["calcs"]["C2"]["inputs"]] == [("block", None), ("assumption", "assumption: ECB rate on 31 Dec")]
    assert [(x["kind"], x.get("label")) for x in d["calcs"]["C3"]["inputs"]] == [("block", None), ("block", None), ("constant", "constant")]
    assert [f["status"] for f in d["figures"]] == ["calculated_with_assumption", "calculated"]
    script = html[html.index("<script>"):]
    assert "x.kind==='block'" in script and "${esc(x.label)}" in script
    assert ".status-calculated_with_assumption{border-color:var(--assume)" in html and "--assume:" in html

def test_report_counts_citations_without_a_figure(tmp_path):
    c = setup(tmp_path)
    run(transcript(tmp_path, [Q, {"role": "tool", "content": c}, {"role": "assistant", "content": "Operating margin: [C1]"}]))
    html = (tmp_path / ".traceable/report.html").read_text()
    assert "1 citation without a figure" in html


def test_calculation_inputs_show_their_row(tmp_path):  # an "EBITDA" input from the operating profit row
    c = setup(tmp_path)
    run(transcript(tmp_path, [Q, {"role": "tool", "content": c}, {"role": "assistant", "content": "Margin was 30.3% [C1]."}]))
    html = (tmp_path / ".traceable/report.html").read_text()
    assert [x["row"] for x in payload(html)["calcs"]["C1"]["inputs"]] == ["Operating profit", "Revenue"]
    assert "from row '${esc(x.row)}'" in html

# --- tables render as tables, links stay readable ---

def test_answer_tables_render_as_html_tables_with_chips_in_cells(tmp_path):
    c = setup(tmp_path)
    ans = "| Company | Operating margin |\n|---|---|\n| RELX | 30.3% [C1] |"
    run(transcript(tmp_path, [Q, {"role": "tool", "content": c}, {"role": "assistant", "content": ans}]))
    html = (tmp_path / ".traceable/report.html").read_text()
    answer = html[html.index('<div class="answer">'):html.index("</section>")]
    assert "<table>" in answer and "<th>Company</th>" in answer and "|---" not in answer
    assert re.search(r'<td>\s*<span class="figure-chip status-calculated" data-i="0">30\.3%</span>', answer)

def test_block_tables_render_with_the_matched_cell_bolded(tmp_path):
    c = setup(tmp_path)
    run(transcript(tmp_path, [Q, {"role": "tool", "content": c},
        {"role": "assistant", "content": "Margin 30.3% [C1] on £9,434m revenue [D1:p1:b3]."}]))
    bold = payload((tmp_path / ".traceable/report.html").read_text())["figures"][1]["bold_html"]
    assert "<table>" in bold and "<td><b>9,434</b></td>" in bold and "| ---" not in bold

def test_links_are_readable_in_both_themes(tmp_path):
    c = setup(tmp_path)
    run(transcript(tmp_path, [Q, {"role": "tool", "content": c}, {"role": "assistant", "content": "Margin 30.3% [C1]."}]))
    css = (tmp_path / ".traceable/report.html").read_text().split("<style>")[1].split("</style>")[0]
    light, dark = css.split("@media (prefers-color-scheme:dark)")[0], css.split("@media (prefers-color-scheme:dark)")[1]
    assert "--link:" in light and "--link:" in dark and "a{color:var(--link)}" in css


def test_markdown_headings_in_the_answer_render_as_headings():
    from traceable.report import _headings
    assert _headings("## Analysis\nText") == "<h3>Analysis</h3>\nText"
    assert _headings("### Sub\n#nothashtag") == "<h4>Sub</h4>\n#nothashtag"


# --- one folder for every demo, so the report keeps every question; clearer at a glance ---

def _ask(tmp_path, question, answer, tools=()):
    q = {"role": "user", "content": question, "injected": False}
    return run(transcript(tmp_path, [q] + [{"role": "tool", "content": t} for t in tools]
                          + [{"role": "assistant", "content": answer}]))

def test_the_check_records_its_question(tmp_path):
    setup(tmp_path)
    _ask(tmp_path, "What was RELX's 2024 revenue?", f"Revenue was £9,434m {B}.")
    assert Ledger(tmp_path).load()["checks"][-1]["question_text"] == "What was RELX's 2024 revenue?"

def test_the_report_keeps_every_question_newest_first(tmp_path):
    c = setup(tmp_path)
    _ask(tmp_path, "What was RELX's 2024 revenue?", f"Revenue was £9,434m {B}.")
    _ask(tmp_path, "And the operating margin?", "Margin was 30.3% [C1].", tools=[c])
    html = (tmp_path / ".traceable/report.html").read_text()
    d = payload(html)
    assert [v["question"] for v in d["views"]] == ["And the operating margin?", "What was RELX's 2024 revenue?"]
    assert d["views"][1]["figures"][0]["raw"] == "£9,434m" and "C1" in d["views"][0]["calcs"]
    assert html.count('class="qitem') == 2

def test_the_header_shows_the_question_and_a_clear_verdict(tmp_path):
    setup(tmp_path)
    _ask(tmp_path, "What was RELX's 2024 revenue?", f"Revenue was £9,434m {B}.")
    html = (tmp_path / ".traceable/report.html").read_text()
    assert "What was RELX&#x27;s 2024 revenue?" in html and 'class="verdict verified"' in html
    _ask(tmp_path, "What was RELX's 2024 revenue?", f"Revenue in 2024 was £9,161m {B}.")
    html = (tmp_path / ".traceable/report.html").read_text()
    assert 'class="verdict denied"' in html

def test_the_answer_renders_bold_lists_and_quiet_citation_tags(tmp_path):
    setup(tmp_path)
    _ask(tmp_path, "Revenue?", f"Summary:\n- **Revenue**: £9,434m {B}\n- Up from £9,161m in 2023 {B}")
    html = (tmp_path / ".traceable/report.html").read_text()
    answer = html[html.index('<div class="answer">'):html.index("</section>")]
    assert "<ul>" in answer and "<li>" in answer and "<strong>Revenue</strong>" in answer
    assert '<span class="cite" data-cite="D1:p1:b3">D1:p1:b3</span>' in answer

def test_every_figure_is_listed_with_a_readable_source(tmp_path):
    c = setup(tmp_path)
    _ask(tmp_path, "Margin?", f"Margin was 30.3% [C1] on £9,434m revenue {B}.", tools=[c])
    d = payload((tmp_path / ".traceable/report.html").read_text())
    labels = [f["source_label"] for f in d["figures"]]
    assert labels[0].startswith("Calculation C1") and labels[1] == "relx_is.pdf · page 1"

def test_a_draft_denied_after_the_last_retry_does_not_reload(tmp_path):
    setup(tmp_path)
    for _ in range(4):
        _ask(tmp_path, "Revenue?", f"Revenue in 2024 was £9,161m {B}.")
    html = (tmp_path / ".traceable/report.html").read_text()
    assert 'http-equiv="refresh"' not in html and "DENIED" in html and "not verified" in html

def test_a_synthetic_marker_that_names_files_flags_only_those_sources(tmp_path):
    setup(tmp_path)
    (tmp_path / "SYNTHETIC").write_text("Synthetic files: operating_plan_fy24_26.xlsx\n")
    _ask(tmp_path, "Revenue?", f"Revenue was £9,434m {B}.")
    html = (tmp_path / ".traceable/report.html").read_text()
    assert "SYNTHETIC DOCUMENTS" not in html and "synthetic</span>" not in html
    (tmp_path / "SYNTHETIC").write_text("Synthetic files: relx_is.pdf\n")
    _ask(tmp_path, "Revenue?", f"Revenue was £9,434m {B}.")
    html = (tmp_path / ".traceable/report.html").read_text()
    assert '<span class="sname">relx_is.pdf</span> <em>synthetic</em></span>' in html

def test_questions_show_file_names_not_full_paths(tmp_path):  # pasted paths made the header unreadable
    setup(tmp_path)
    _ask(tmp_path, "Using /Users/me/Desktop/Mistral Demo/traceable-numbers/demo/live/relx_income_statement.pdf, revenue?",
         f"Revenue was £9,434m {B}.")
    html = (tmp_path / ".traceable/report.html").read_text()
    assert "Using relx_income_statement.pdf, revenue?" in html and "/Users/me/Desktop" not in html

def test_two_paths_in_one_question_both_shorten():
    from traceable.report import _short_paths
    q = ("Using /Users/me/Desktop/Mistral Demo/x/demo/live/relx_income_statement.pdf and "
         "/Users/me/Desktop/Mistral Demo/x/demo/live/diageo_income_statement.pdf, what were margins?")
    assert _short_paths(q) == "Using relx_income_statement.pdf and diageo_income_statement.pdf, what were margins?"


# --- one verdict sentence instead of pills, sources that say where they come from ---

def test_one_verdict_sentence_replaces_the_pills(tmp_path):
    setup(tmp_path)
    _ask(tmp_path, "Revenue?", f"Revenue in 2024 was £9,161m {B}.")
    _ask(tmp_path, "Revenue?", f"Revenue in 2024 was £9,434m {B}.")
    html = (tmp_path / ".traceable/report.html").read_text()
    assert 'class="pill' not in html
    assert "1 of 1 figure verified: 1 found in its source." in html and "The check sent back 1 draft first." in html
    meta = html[html.index('<details class="meta">'):]
    assert "denials for this question: 1" in meta and "OCR model: mistral-ocr-latest" in meta

def test_web_sources_show_their_site_or_say_there_is_no_link(tmp_path):
    from test_sources import fake_conv, PAGE
    from traceable.sources import web_search
    web_search("q", root=tmp_path, conv=fake_conv("x", [{"title": "NVIDIA Q2 results", "url": "https://www.example.com/nv"}]),
               fetch=lambda u: PAGE)
    _ask(tmp_path, "Data center?", "Data Center revenue was $41.1bn [W1].")
    html = (tmp_path / ".traceable/report.html").read_text()
    assert '<span class="domain">example.com</span>' in html
    d = payload(html)
    assert "example.com" in d["blocks"]["W1"]["source_html"] and d["figures"][0]["source_label"] == "Web · NVIDIA Q2 results · example.com"

def test_a_data_card_says_it_has_no_link(tmp_path):
    from traceable.sources import web_search
    card = {"answer": "x", "references": [{"title": "NVIDIA Data Center Revenue (Quarterly)", "url": None,
            "description": "value was $18,404,000,000 for fiscal Q4 2024", "snippets": ["value was $18,404,000,000 for fiscal Q4 2024"]}]}
    web_search("q", root=tmp_path, conv=lambda query, tool, library_id=None: card, fetch=lambda u: "")
    _ask(tmp_path, "Data center?", "Data Center revenue was $18,404,000,000 [W1].")
    d = payload((tmp_path / ".traceable/report.html").read_text())
    assert "a data series from Mistral's web search, not a web page, so there is no link" in d["blocks"]["W1"]["source_html"]
    assert d["figures"][0]["source_label"].startswith("Mistral finance data · NVIDIA Data Center Revenue")
    assert d["figures"][0]["src_name"] == "Mistral finance data"

def test_detail_titles_keep_the_figure_as_written_and_citation_tags_are_clickable(tmp_path):
    setup(tmp_path)
    _ask(tmp_path, "Revenue?", f"Revenue was £9,434m {B}.")
    html = (tmp_path / ".traceable/report.html").read_text()
    assert 'class="dtitle"' in html and '<span class="cite" data-cite="D1:p1:b3">D1:p1:b3</span>' in html


# --- a calculation says what it was calculated from, in plain words ---

def test_the_source_column_says_what_a_calculation_was_calculated_from(tmp_path):
    c = setup(tmp_path)
    _ask(tmp_path, "Margin?", f"Margin was 30.3% [C1] on £9,434m revenue {B}.", tools=[c])
    f = payload((tmp_path / ".traceable/report.html").read_text())["figures"]
    assert (f[0]["src_name"], f[0]["src_where"]) == ("Calculated from 2 inputs", "relx_is.pdf · page 1")
    assert (f[1]["src_name"], f[1]["src_where"]) == ("relx_is.pdf", "page 1")

def test_the_calculation_shows_its_values_and_each_input_with_its_file(tmp_path):
    c = setup(tmp_path)
    _ask(tmp_path, "Margin?", "Margin was 30.3% [C1].", tools=[c])
    calc = payload((tmp_path / ".traceable/report.html").read_text())["calcs"]["C1"]
    assert calc["filled"] == "2,861m / 9,434m"
    assert [(x["src_name"], x["src_where"]) for x in calc["inputs"]] == [("relx_is.pdf", "page 1"), ("relx_is.pdf", "page 1")]

def test_a_web_input_has_no_table_row(tmp_path):
    from test_sources import fake_conv, PAGE
    from traceable.sources import web_search
    web_search("q", root=tmp_path, conv=fake_conv("x", [{"title": "NVIDIA Q2 results", "url": "https://www.example.com/nv"}]),
               fetch=lambda u: PAGE)
    c = tool_calculate("dc", [{"name": "dc", "value": "$41.1", "scale": "bn", "source_id": "W1"}], "dc", "money", root=tmp_path)
    _ask(tmp_path, "Data center?", "Data Center revenue was $41.1bn [C1].", tools=[c])
    x = payload((tmp_path / ".traceable/report.html").read_text())["calcs"]["C1"]["inputs"][0]
    assert x["row"] is None and (x["src_name"], x["src_where"]) == ("example.com", "NVIDIA Q2 results")


# --- short question titles, one source per line ---

def test_the_question_list_shows_the_question_without_its_file_preamble():
    from traceable.report import _nav_title
    assert _nav_title("Using relx_income_statement.pdf and diageo_income_statement.pdf, what were each company's "
                      "2024 operating margin?") == "What were each company's 2024 operating margin?"
    assert _nav_title("In operating_plan_fy24_26.xlsx: by what percentage did group revenue grow?") == \
        "By what percentage did group revenue grow?"
    assert _nav_title("The CFO commentary I uploaded is NVIDIA_Q3FY24_CFO_Commentary.pdf. Compare NVIDIA's revenue.") == \
        "Compare NVIDIA's revenue."
    assert _nav_title("What was revenue?") == "What was revenue?"

def test_a_calculation_lists_each_input_source_on_its_own_line(tmp_path):
    c = setup(tmp_path)
    _ask(tmp_path, "Margin?", "Margin was 30.3% [C1].", tools=[c])
    f = payload((tmp_path / ".traceable/report.html").read_text())["figures"][0]
    assert f["src_lines"] == ["relx_is.pdf · page 1"]

def test_every_source_carries_its_kind_for_an_icon(tmp_path):  # an icon per source (file, web, data, calculation)
    c = setup(tmp_path)
    _ask(tmp_path, "Margin?", f"Margin was 30.3% [C1] on £9,434m revenue {B}.", tools=[c])
    d = payload((tmp_path / ".traceable/report.html").read_text())
    f = d["figures"]
    assert (f[0]["src_kind"], f[0]["src_line_kinds"]) == ("CALC", ["PDF"]) and f[1]["src_kind"] == "PDF"
    assert d["calcs"]["C1"]["inputs"][0]["src_kind"] == "PDF"
    assert {"PDF", "WEB", "CARD", "LIBRARY", "CALC", "QUESTION"} <= set(d["icons"])
