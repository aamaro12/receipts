# tests/test_sources.py: citations from every tool. Uploaded PDFs answer with verified quotes ([D1:p1:c1]), web search
# and Mistral Libraries with fetched text ([W1], [L1]); the same check verifies every figure against that text.
import json
from conftest import FIXTURES
from test_ocr import FakeOcr
from test_check import transcript
from traceable.check import run
from traceable.ledger import Ledger
from traceable.sources import ask_document, web_search, library_search, page_of

PDF = str(FIXTURES / "relx_is.pdf")


def fake_qa(answer, citations):
    def qa(pdf_bytes, question):
        return {"answer_with_citations": answer, "citations": citations}
    return qa


def _verdict(tmp_path, answer, q="What was RELX's revenue?"):
    out = run(transcript(tmp_path, [{"role": "user", "content": q, "injected": False},
                                    {"role": "assistant", "content": answer}]))
    return out


# --- uploaded documents ([CITE_N] placeholders, verified against the page) ---

def test_a_quote_found_on_its_page_becomes_a_citable_block_with_its_box(tmp_path):
    out = ask_document(PDF, "What was revenue in 2024?", root=tmp_path, ocr=FakeOcr(),
                       qa=fake_qa("Revenue was £9,434m in 2024 [CITE_1].",
                                  [{"page_number": 1, "quote": "Revenue 8,553 9,161 9,434"}]))
    assert "Revenue was £9,434m in 2024 [D1:p1:c1]." in out and "[CITE_1]" not in out
    b = Ledger(tmp_path).block("D1:p1:c1")
    assert b["type"] == "quote" and b["bbox"] == Ledger(tmp_path).block("D1:p1:b3")["bbox"]
    assert "2024 GBPm" in b["text"] and "9,434" in b["text"] and "Cost of sales" not in b["text"]  # header + that row
    assert _verdict(tmp_path, "Revenue was £9,434m in 2024 [D1:p1:c1].")["decision"] == "allow"
    assert _verdict(tmp_path, "Revenue was £9,161m in 2024 [D1:p1:c1].")["decision"] == "deny"   # wrong year column


def test_a_quote_on_another_page_moves_there_and_says_so(tmp_path):
    out = ask_document(PDF, "Net profit?", root=tmp_path, ocr=FakeOcr(),
                       qa=fake_qa("Actuarial gains were £43m [CITE_1].",
                                  [{"page_number": 1, "quote": "Actuarial gains/(losses) on defined benefit pension schemes 164 (75) 43"}]))
    assert "[D1:p2:c1]" in out and "moved from page 1" in out
    assert Ledger(tmp_path).block("D1:p2:c1")["moved_from_page"] == 1


def test_an_invented_quote_gets_no_id_and_a_figure_citing_its_placeholder_is_denied(tmp_path):
    out = ask_document(PDF, "Revenue?", root=tmp_path, ocr=FakeOcr(),
                       qa=fake_qa("Revenue was £12,000m [CITE_1].", [{"page_number": 1, "quote": "Revenue 12,000"}]))
    assert "not found in the document" in out and "[CITE_1]" not in out
    assert not [b for d in Ledger(tmp_path).load()["documents"].values() for b in d["blocks"] if b.get("type") == "quote"]
    assert _verdict(tmp_path, "Revenue was £12,000m [D1:p1:c1].")["decision"] == "deny"      # a phantom ID


def test_multiple_placeholders_in_one_bracket_are_split(tmp_path):
    out = ask_document(PDF, "Revenue and cost of sales?", root=tmp_path, ocr=FakeOcr(),
                       qa=fake_qa("Revenue £9,434m and cost of sales £3,300m [CITE_1, CITE_2].",
                                  [{"page_number": 1, "quote": "Revenue 8,553 9,161 9,434"},
                                   {"page_number": 1, "quote": "Cost of sales (3,045) (3,216) (3,300)"}]))
    assert "[D1:p1:c1][D1:p1:c2]" in out


# --- web search: fetched text, not just a URL ---

PAGE = ("<html><head><title>NVIDIA results</title></head><body><nav>Menu Products</nav>"
        "<p>NVIDIA today reported revenue for the second quarter of fiscal 2026 of $46.7 billion.</p>"
        "<p>Data Center revenue was $41.1 billion, up 5% from the previous quarter.</p>"
        "<p>Cookie settings and legal notices.</p></body></html>")


def fake_conv(answer, refs):
    def conv(query, tool, library_id=None):
        return {"answer": answer, "references": refs}
    return conv


def test_web_results_become_blocks_holding_the_fetched_text(tmp_path):
    out = web_search("NVIDIA latest data center revenue", root=tmp_path,
                     conv=fake_conv("Data Center revenue was $41.1 billion.",
                                    [{"title": "NVIDIA Q2 FY26 results", "url": "https://example.com/nv"}]),
                     fetch=lambda url: PAGE)
    assert "[W1] NVIDIA Q2 FY26 results (https://example.com/nv)" in out
    b = Ledger(tmp_path).block("W1")
    assert b["type"] == "web" and b["url"] == "https://example.com/nv" and "$41.1 billion" in b["text"]
    assert "Cookie" not in b["text"]                              # the passages that matter, not the whole page
    assert _verdict(tmp_path, "Data Center revenue was $41.1bn [W1].", q="Latest data center revenue?")["decision"] == "allow"
    assert _verdict(tmp_path, "Data Center revenue was $44.2bn [W1].", q="Latest data center revenue?")["decision"] == "deny"
    assert _verdict(tmp_path, "Data Center revenue was $41.1bn [W9].", q="Latest data center revenue?")["decision"] == "deny"


def test_a_page_that_cannot_be_fetched_is_listed_but_not_citable(tmp_path):
    def fail(url):
        raise RuntimeError("403")
    out = web_search("q", root=tmp_path, conv=fake_conv("x", [{"title": "T", "url": "https://example.com/x"}]), fetch=fail)
    assert "could not be fetched" in out and Ledger(tmp_path).block("W1") is None


def test_calculations_take_web_and_upload_blocks_as_inputs(tmp_path):
    from traceable.server import tool_calculate
    ask_document(PDF, "Revenue?", root=tmp_path, ocr=FakeOcr(),
                 qa=fake_qa("Revenue £9,434m [CITE_1].", [{"page_number": 1, "quote": "Revenue 8,553 9,161 9,434"}]))
    web_search("q", root=tmp_path, conv=fake_conv("x", [{"title": "NV", "url": "https://example.com/nv"}]), fetch=lambda u: PAGE)
    r = tool_calculate("ratio", [{"name": "dc", "value": "$41.1", "scale": "bn", "source_id": "W1"},
                                 {"name": "rev", "value": "9,434", "scale": "m", "source_id": "D1:p1:c1"}],
                       "dc / rev", "num", root=tmp_path)
    assert r.startswith("C1 = "), r


# --- Mistral Libraries: no page markers, so the page is best-effort ---

LIB_TEXT = ("140 RELX Annual Report 2024 | Financial statements and other information\n"
            "Consolidated income statement\nFor the year ended 31 December, GBPm\nRevenue 8,553 9,161 9,434\n"
            "Cost of sales (3,045) (3,216) (3,300)\n141\nConsolidated statement of comprehensive income\n")


def test_library_results_keep_document_and_best_effort_page(tmp_path):
    out = library_search("RELX revenue 2024", "lib-1", root=tmp_path,
                         conv=fake_conv("Revenue was 9,434.", [{"title": "relx_2024.pdf", "url": None}]),
                         docs=lambda lib: [{"id": "doc-9", "name": "relx_2024.pdf"}], text=lambda lib, doc: LIB_TEXT)
    assert "[L1] relx_2024.pdf" in out
    b = Ledger(tmp_path).block("L1")
    assert b["document_id"] == "doc-9" and b["page"] == 140 and "best-effort" in b["page_note"]
    assert _verdict(tmp_path, "RELX revenue was £9,434m in 2024 [L1].")["decision"] == "allow"


def test_a_library_page_that_cannot_be_parsed_is_left_empty_but_still_citable(tmp_path):
    library_search("revenue", "lib-1", root=tmp_path, conv=fake_conv("x", [{"title": "notes.pdf", "url": None}]),
                   docs=lambda lib: [{"id": "d1", "name": "notes.pdf"}], text=lambda lib, doc: "Revenue grew to 9,434 this year.")
    b = Ledger(tmp_path).block("L1")
    assert b["page"] is None and "9,434" in b["text"]


def test_page_of_reads_printed_page_headers():
    assert page_of("140 RELX Annual Report 2024 | Financial statements") == 140
    assert page_of("RELX Annual Report 2024 | Governance 57") == 57
    assert page_of("Revenue grew to 9,434") is None


def test_the_report_shows_web_and_library_citations_with_title_link_and_snippet(tmp_path):
    web_search("q", root=tmp_path, conv=fake_conv("x", [{"title": "NVIDIA Q2 FY26 results", "url": "https://example.com/nv"}]),
               fetch=lambda u: PAGE)
    _verdict(tmp_path, "Data Center revenue was $41.1bn [W1].", q="Latest data center revenue?")
    html = (tmp_path / ".traceable/report.html").read_text()
    payload = json.loads(html.split("const D=")[1].split(";\n")[0])
    w = payload["blocks"]["W1"]
    assert w["kind"] == "web" and "https://example.com/nv" in w["source_html"] and "NVIDIA Q2 FY26 results" in w["source_html"]
    assert "$41.1 billion" in w["source_html"]


def test_snippets_from_the_search_are_used_without_fetching_the_page(tmp_path):
    def no_fetch(url):
        raise AssertionError("the page should not be fetched when the search returned snippets")
    refs = [{"title": "NVIDIA Q2 FY27 results", "url": "https://example.com/pr",
             "snippets": ["Revenue of $96.2 billion, up 106% from a year ago. Data Center revenue of $89.0 billion, up 117%.",
                          "NVIDIA will pay its next quarterly cash dividend of $0.25 per share."]}]
    out = web_search("NVIDIA data center revenue latest quarter", root=tmp_path, conv=fake_conv("x", refs), fetch=no_fetch)
    assert "[W1] NVIDIA Q2 FY27 results" in out and "$89.0 billion" in Ledger(tmp_path).block("W1")["text"]


def test_parse_conversation_reads_snippets_from_tool_execution_and_finance_cards():
    from traceable.sources import parse_conversation
    web = {"outputs": [
        {"type": "tool.execution", "name": "web_search", "info": {"result": json.dumps(
            {"a1": {"url": "https://example.com/pr", "title": "PR", "description": "d", "snippets": ["Data Center $89.0 billion"]}})}},
        {"type": "tool.execution", "name": "finance_search", "info": {"result": [{"type": "text", "text": json.dumps(
            {"description": "Data Center revenue latest value was $89,023,000,000 for fiscal Q2 2027",
             "content": {"card_data": {"title": "NVIDIA Data Center Revenue (Quarterly)",
                                       "series": [{"points": [{"x": "Q1 2026", "y": 75246000000.0}, {"x": "Q2 2026", "y": 89023000000.0}]}]}}})}]}},
        {"type": "message.output", "content": [{"type": "text", "text": "It was $89.0 billion."},
                                               {"type": "tool_reference", "title": "PR", "url": "https://example.com/pr"}]}]}
    res = parse_conversation(web)
    assert res["answer"] == "It was $89.0 billion."
    pr = next(r for r in res["references"] if r["url"] == "https://example.com/pr")
    assert pr["snippets"] == ["Data Center $89.0 billion"]
    card = next(r for r in res["references"] if r["url"] is None)
    assert card["title"].startswith("NVIDIA Data Center Revenue") and "89,023,000,000" in card["snippets"][0]
    assert any("Q2 2026: 89,023,000,000" in s for s in card["snippets"])


def test_the_same_quote_twice_gets_one_id(tmp_path):  # seen in a run: [D1:p1:c1] and [D1:p1:c3] quoted the same row
    out = ask_document(PDF, "Revenue and its growth?", root=tmp_path, ocr=FakeOcr(),
                       qa=fake_qa("Revenue was £9,434m [CITE_1], against £9,161m [CITE_2].",
                                  [{"page_number": 1, "quote": "Revenue 8,553 9,161 9,434"},
                                   {"page_number": 1, "quote": "Revenue 8,553 9,161 9,434"}]))
    assert "£9,434m [D1:p1:c1], against £9,161m [D1:p1:c1]." in out and "c2" not in out



class CaptionOcr(FakeOcr):
    """The RELX table with its unit moved out of the column headers into the heading above it, as Affirm prints it."""
    def ocr(self, pdf_bytes, pages=None):
        resp = super().ocr(pdf_bytes, pages)
        for b in resp["pages"][0]["blocks"]:
            if b["type"] == "title":
                b["content"] = "# Consolidated income statement (£ in millions)"
            if b["type"] == "table":
                b["content"] = b["content"].replace(" GBPm", "")
        return resp


def test_a_quote_keeps_the_scale_stated_in_the_heading_above_its_table(tmp_path):  # seen in a run: '$691,013' passed
    ask_document(PDF, "Revenue in 2024?", root=tmp_path, ocr=CaptionOcr(),
                 qa=fake_qa("Revenue was £9,434m [CITE_1].", [{"page_number": 1, "quote": "Revenue 8,553 9,161 9,434"}]))
    assert _verdict(tmp_path, "Revenue was £9,434m in 2024 [D1:p1:c1].")["decision"] == "allow"
    assert _verdict(tmp_path, "Revenue was £9,434 in 2024 [D1:p1:c1].")["decision"] == "deny"

SUMMARY = "RELX's 2024 Annual Report shows revenue growth from £8,553m (2022) to £9,434m (2024)."
CHUNK_140 = ("140 RELX Annual Report 2024 | Financial statements and other information\n\n# Consolidated income statement\n\n"
             "|  FOR THE YEAR ENDED 31 DECEMBER | Note | 2022 GBPm | 2023 GBPm | 2024 GBPm  |\n| --- | --- | --- | --- | --- |\n"
             "|  **Revenue** | 2 | 8,553 | 9,161 | **9,434**  |\n|  **Operating profit** | 2, 3 | 2,323 | 2,682 | **2,861**  |")
CHUNK_141 = ("RELX Annual Report 2024\n\n&lt; &gt; 141\n\n# Consolidated statement of comprehensive income\n\n"
             "|  FOR THE YEAR ENDED 31 DECEMBER | Note | 2022 GBPm | 2023 GBPm | 2024 GBPm  |\n| --- | --- | --- | --- | --- |\n"
             "|  **Other comprehensive income/(loss) for the year** |  | 521 | (306) | **201**  |")


def test_real_library_results_use_the_chunks_not_the_ai_summary(tmp_path):
    # Url holds the document id; the first snippet is a model-written summary equal to description
    def no_text(lib, doc):
        raise AssertionError("the chunks were returned, so the full text is not needed")
    refs = [{"title": "relx_income_statement.pdf", "url": "doc-9", "description": SUMMARY,
             "snippets": [SUMMARY, CHUNK_140, CHUNK_141]}]
    out = library_search("RELX revenue and operating profit 2024", "lib-1", root=tmp_path, conv=fake_conv("x", refs),
                         docs=lambda lib: [], text=no_text)
    b = Ledger(tmp_path).block("L1")
    assert b["document_id"] == "doc-9" and b["page"] == 140 and "9,434" in b["text"] and "shows revenue growth" not in b["text"]
    assert "2024 GBPm" in b["text"]                                 # the table header travels with the rows
    assert _verdict(tmp_path, "RELX's 2024 revenue was £9,434m [L1].")["decision"] == "allow"
    assert _verdict(tmp_path, "RELX's 2024 revenue was £9,500m [L1].")["decision"] == "deny"


def test_a_bare_page_number_near_the_top_of_a_chunk_is_its_page():
    from traceable.sources import chunk_page
    assert chunk_page(CHUNK_141) == 141 and chunk_page(CHUNK_140) == 140
    assert chunk_page("Revenue grew.\n| Revenue | 9,434 |") is None


def test_passages_prefer_the_questions_words_and_drop_repeats():
    # live run: the Revenue row lost to two copies of "Net profit for the year" (common words from the answer)
    from traceable.sources import passages
    rows = ["| **Revenue** | 2 | 8,553 | 9,161 | **9,434** |", "| **Operating profit** | 2, 3 | 2,323 | 2,682 | **2,861** |",
            "| **Net profit for the year** | | 1,632 | 1,788 | **1,944** |", "| **Net profit for the year** | | 1,632 | 1,788 | **1,944** |",
            "| Finance costs | 7 | (205) | (323) | **(304)** |"]
    keep = passages(rows, "RELX 2024 revenue and operating profit",
                    answer="Revenue grew for the year and profit for the year rose, net profit for the year too.")
    assert keep[0].startswith("| **Revenue**") and keep[1].startswith("| **Operating profit**")
    assert len([k for k in keep if "Net profit" in k]) <= 1



NVDA = ("| | Revenue by Market Platform | | | | |\n| --- | --- | --- | --- | --- | --- |\n"
        "| ($ in millions) | Q3 FY24 | Q2 FY24 | Q3 FY23 | Q/Q | Y/Y |\n"
        "| Data Center | $14,514 | $10,323 | $3,833 | Up 41% | Up 279% |\n| Gaming | 2,856 | 2,486 | 1,574 | Up 15% | Up 81% |")


def test_a_quoted_row_keeps_the_unit_and_period_rows_under_the_separator():
    from traceable.sources import _header
    head = _header({"type": "table", "text": NVDA})
    assert head[-1].startswith("| ($ in millions) | Q3 FY24") and not any("14,514" in h for h in head)


def test_the_note_shows_the_verified_line_and_a_quote_with_a_year_is_found(tmp_path):
    out = ask_document(PDF, "Adjusted operating profit?", root=tmp_path, ocr=FakeOcr(),
                       qa=fake_qa("Adjusted operating profit was £2,861m [CITE_1].",
                                  [{"page_number": 1, "quote": "Adjusted operating profit 2,861"}]))
    note = next(ln for ln in out.splitlines() if ln.startswith("[D1:p1:c1]"))
    assert "Operating profit" in note and "matched to" in note           # what the page says, not the model's words
    out = ask_document(PDF, "Revenue?", root=tmp_path, ocr=FakeOcr(),
                       qa=fake_qa("Revenue was £9,434m [CITE_1].", [{"page_number": 1, "quote": "Revenue 2024: 9,434"}]))
    assert "not found" not in out


def test_odd_api_shapes_do_not_break_the_search():
    from traceable.sources import parse_conversation
    r = {"outputs": [{"type": "tool.execution", "info": {"result": json.dumps(
            {"content": {"card_data": {"series": [{"points": [{"y": 1.0}]}]}}, "description": "Card"})}},
        {"type": "message.output", "content": {"type": "text", "text": "Hi"}}]}
    out = parse_conversation(r)
    assert out["references"][0]["title"] == "Data card"


def test_only_web_links_are_clickable_in_the_report():
    from traceable.report import _safe_url
    assert _safe_url("https://x.com/a?b=1") == "https://x.com/a?b=1"
    assert _safe_url("javascript:alert(1)") is None and _safe_url(" JAVASCRIPT:alert(1)") is None


# --- Library pages: the extracted-text route gives the file's pages, so a Library citation has an exact page ---

PAGES = [CHUNK_140.replace("&lt; &gt; ", ""), CHUNK_141.replace("&lt; &gt; ", "< > ")]   # file page 1 = printed page 140


def test_a_library_chunk_gets_the_file_page_that_holds_its_rows(tmp_path):
    refs = [{"title": "relx_income_statement.pdf", "url": "doc-9", "description": SUMMARY,
             "snippets": [SUMMARY, CHUNK_141, CHUNK_140]}]
    out = library_search("RELX revenue and operating profit 2024", "lib-1", root=tmp_path, conv=fake_conv("x", refs),
                         docs=lambda lib: [], text=None, pages=lambda lib, doc: PAGES)
    b = Ledger(tmp_path).block("L1")
    assert b["page"] == 1 and "printed page 140" in b["page_note"] and "9,434" in b["text"]
    assert "page 1 of the file" in out


def test_without_chunks_the_passage_is_taken_from_the_pages_with_its_page(tmp_path):
    out = library_search("comprehensive income 2024", "lib-1", root=tmp_path,
                         conv=fake_conv("x", [{"title": "relx_2024.pdf", "url": None}]),
                         docs=lambda lib: [{"id": "doc-9", "name": "relx_2024.pdf"}],
                         text=lambda lib, doc: (_ for _ in ()).throw(AssertionError("pages are enough")),
                         pages=lambda lib, doc: PAGES)
    b = Ledger(tmp_path).block("L1")
    assert b["page"] == 2 and "201" in b["text"] and "best-effort" not in b["page_note"]
    assert "page 2 of the file" in out


def test_when_the_pages_cannot_be_read_the_old_page_rules_apply(tmp_path):
    def broken(lib, doc):
        raise RuntimeError("HTTP 404")
    refs = [{"title": "relx_income_statement.pdf", "url": "doc-9", "description": SUMMARY, "snippets": [SUMMARY, CHUNK_140]}]
    library_search("RELX revenue 2024", "lib-1", root=tmp_path, conv=fake_conv("x", refs), docs=lambda lib: [],
                   text=None, pages=broken)
    assert Ledger(tmp_path).block("L1")["page"] == 140


def test_library_pages_reads_the_signed_url():
    from traceable import sources
    calls = []
    def get(path):
        calls.append(path)
        return "https://blob.example/ocr.json?sig=1"
    def fetch_json(url):
        calls.append(url)
        return [{"pages": [{"markdown": "page one"}, {"markdown": "page two"}]}]
    assert sources.library_pages("lib-1", "doc-9", get=get, fetch_json=fetch_json) == ["page one", "page two"]
    assert calls == ["/libraries/lib-1/documents/doc-9/extracted-text-signed-url", "https://blob.example/ocr.json?sig=1"]



NET = "|  **Net profit for the year** |  | 1,632 | 1,788 | **1,944**  |"
TWO_TABLES = ("# Segment revenue\n\n| Segment | 2023 GBPm | 2024 GBPm |\n| --- | --- | --- |\n| Risk | 3,127 | 3,265 |\n\n"
              "# Employees\n\n| Region | 2022 | 2023 |\n| --- | --- | --- |\n| Europe | 11,400 | 11,900 |")


def test_a_row_on_two_pages_is_not_given_an_exact_page():
    from traceable.sources import page_holding
    pages = [CHUNK_140 + "\n" + NET, CHUNK_141 + "\n" + NET]
    block = "|  FOR THE YEAR ENDED 31 DECEMBER | Note | 2022 GBPm | 2023 GBPm | 2024 GBPm  |\n| --- | --- | --- | --- | --- |\n" + NET
    assert page_holding(block, pages) is None                     # ambiguous: two pages hold it
    assert page_holding(block + "\n|  **Revenue** | 2 | 8,553 | 9,161 | **9,434**  |", pages) == 1


def test_header_rows_alone_do_not_place_a_passage():
    from traceable.sources import page_holding
    block = "|  FOR THE YEAR ENDED 31 DECEMBER | Note | 2022 GBPm | 2023 GBPm | 2024 GBPm  |\n| x | 1 | 2 | 3 | 999,999 |"
    assert page_holding(block, PAGES) is None


def test_without_chunks_the_passage_stays_on_one_page_under_its_own_table_header(tmp_path):
    pages = [CHUNK_140, TWO_TABLES]
    library_search("Europe employees 2023", "lib-1", root=tmp_path, conv=fake_conv("x", [{"title": "r.pdf", "url": None}]),
                   docs=lambda lib: [{"id": "d", "name": "r.pdf"}], text=None, pages=lambda lib, doc: pages)
    b = Ledger(tmp_path).block("L1")
    assert b["page"] == 2 and "| Region | 2022 | 2023 |" in b["text"]
    assert "Segment" not in b["text"] and "9,434" not in b["text"]          # no other table's header, no other page


def test_a_chunk_found_on_no_page_says_so(tmp_path):
    refs = [{"title": "relx.pdf", "url": "doc-9", "description": SUMMARY, "snippets": [SUMMARY, CHUNK_140]}]
    library_search("RELX revenue 2024", "lib-1", root=tmp_path, conv=fake_conv("x", refs), docs=lambda lib: [],
                   text=None, pages=lambda lib, doc: ["An unrelated page 12,345."])
    b = Ledger(tmp_path).block("L1")
    assert b["page"] == 140 and "not found on any page" in b["page_note"]


def test_the_page_note_reads_plainly(tmp_path):
    refs = [{"title": "relx.pdf", "url": "doc-9", "description": SUMMARY, "snippets": [SUMMARY, CHUNK_140]}]
    library_search("RELX revenue 2024", "lib-1", root=tmp_path, conv=fake_conv("x", refs), docs=lambda lib: [],
                   text=None, pages=lambda lib, doc: PAGES)
    assert Ledger(tmp_path).block("L1")["page_note"] == "file page, from the Library's extracted pages; printed page 140"


def test_a_quoted_row_keeps_the_unit_row_above_it(tmp_path):  # seen in a run: EPS of 173.2c accepted as "$173.2m"
    DIAGEO_PDF = str(FIXTURES / "diageo_is.pdf")
    ask_document(DIAGEO_PDF, "Basic EPS?", root=tmp_path, ocr=FakeOcr("diageo_is.json"),
                 qa=fake_qa("Basic EPS was 173.2 [CITE_1].", [{"page_number": 1, "quote": "Basic earnings per share 173.2 196.3 184.6"}]))
    assert "cents" in Ledger(tmp_path).block("D1:p1:c1")["text"]
    q = "What were Diageo's basic earnings per share in FY2024?"
    assert _verdict(tmp_path, "Basic earnings per share were $173.2m [D1:p1:c1].", q)["decision"] == "deny"
    assert _verdict(tmp_path, "Basic earnings per share were 173.2c [D1:p1:c1].", q)["decision"] == "allow"
